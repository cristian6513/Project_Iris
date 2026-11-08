"""F01: the selector routes (state, conversation selection, default model)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import model_selection as ms
from core.model_selection_api import model_selection_router

OPTIONS = {
    "provider": "fake-a",
    "model": "alpha-1",
    "providers": [
        {"slug": "fake-a", "name": "Fake A", "models": ["alpha-1", "alpha-2"], "is_current": True, "authenticated": True},
        {"slug": "fake-b", "name": "Fake B", "models": ["beta-1"], "is_current": False, "authenticated": True},
        {"slug": "openai-codex", "name": "OpenAI Codex", "models": ["gpt-5"], "is_current": False,
         "authenticated": False, "source": "configured-current"},
    ],
}
# The named profile ``coder`` has its own catalog: fake-b and fake-c, no fake-a.
CODER_OPTIONS = {
    "provider": "fake-c",
    "model": "gamma-1",
    "providers": [
        {"slug": "fake-b", "name": "Fake B", "models": ["beta-1"], "is_current": False, "authenticated": True},
        {"slug": "fake-c", "name": "Fake C", "models": ["gamma-1"], "is_current": True, "authenticated": True},
    ],
}
CATALOGS = {None: OPTIONS, "coder": CODER_OPTIONS}


class FakeBridge:
    """The bridge surface the routes use, with Wake/Continuity switches and rotations."""

    def __init__(self, tmp_path, default) -> None:
        self.session_id = "IRIS-iris"
        self.active_profile = None
        self.generation = 0
        self.is_available = True
        self.supports_model_selection = True
        self.local = True
        self.default = default
        self.remote_default = {"provider": "fake-b", "model": "beta-1"}
        self.model_selection = ms.ModelSelectionService(
            ms.ConversationModelStore(tmp_path / "models.json"), default_reader=lambda profile: dict(default))
        self.inventory_calls: list[bool] = []
        self.inventory_profiles: list[str] = []  # the route each catalog query used
        self.inventory_error: Exception | None = None
        self.during_inventory = None  # runs once while the catalog request is "in flight"
        self.inventory_gate: asyncio.Event | None = None  # holds the next catalog query until set
        self.locality_checks = 0

    def route_context(self):
        profile = self.active_profile
        return {"profile": profile, "session_id": self.session_id, "generation": self.generation,
                "token": f"{profile or 'default'}:{self.generation}", "api_key": f"key-{profile or 'base'}"}

    def switch(self, session_id, profile=None):
        """Wake or Continuity: another conversation (bumps the generation)."""
        self.session_id, self.active_profile = session_id, profile
        self.generation += 1

    def rotate(self, child_id):
        """Hermes compression: same conversation, new id; the selection is carried."""
        self.model_selection.store.copy(self.active_profile, self.session_id, child_id)
        self.session_id = child_id

    async def default_model(self, route):
        assert route["profile"] in CATALOGS  # always the captured route, never the active one
        return dict(self.default if self.local else self.remote_default)

    async def gateway_is_local(self):
        self.locality_checks += 1
        return self.local

    async def model_inventory(self, *, refresh: bool = False, route=None):
        profile = route["profile"]  # the routes must pass the route they captured
        self.inventory_calls.append(refresh)
        self.inventory_profiles.append(profile or "default")
        if self.during_inventory:
            hook, self.during_inventory = self.during_inventory, None
            hook()
        if self.inventory_gate is not None:
            gate, self.inventory_gate = self.inventory_gate, None
            await gate.wait()
        if self.inventory_error:
            raise self.inventory_error
        return ms.normalize_inventory(CATALOGS[profile])


@pytest.fixture()
def env(tmp_path, monkeypatch):
    default = {"provider": "fake-a", "model": "alpha-1"}
    bridge = FakeBridge(tmp_path, default)
    agent = SimpleNamespace(bridge=bridge, _current_turn_task=None)
    events: list[tuple[str, dict]] = []
    writes: list[tuple] = []

    async def broadcast(event, payload):
        events.append((event, payload))

    def fake_writer(provider, model, profile=None):
        writes.append((provider, model, profile))
        if model == "alpha-2":
            return {"ok": False, "error": "Hermes said no"}
        default.update(provider=provider, model=model)
        return {"ok": True, "provider": provider, "model": model, "warning": ""}

    monkeypatch.setattr(ms, "read_default_model", lambda profile=None: dict(default))
    monkeypatch.setattr(ms, "change_default_model", fake_writer)
    monkeypatch.setenv("IRIS_HOME", str(tmp_path / "iris-profile"))
    visibility = ms.SelectorVisibilityStore(tmp_path / "model_selector_visible.json")
    app = FastAPI()
    app.include_router(model_selection_router(agent=lambda: agent, broadcast=broadcast, visibility=visibility))
    return SimpleNamespace(client=TestClient(app), app=app, bridge=bridge, agent=agent, events=events,
                           writes=writes, default=default, visibility=visibility)


def record(env, **outcome):
    plan = env.bridge.model_selection.turn_plan(None, env.bridge.session_id)
    base = ms.ModelSelectionService.outcome(plan, {"model": plan["model"], "model_lock": "confirmed"}, status="completed")
    env.bridge.model_selection.store.record_outcome(None, env.bridge.session_id, {**base, **outcome})


def test_state_describes_the_conversation_default_and_inventory(env):
    body = env.client.get("/api/models/selector").json()
    assert body["supported"] is True and body["reason"] == "" and body["busy"] is False
    assert body["conversation"] == {"session_id": "IRIS-iris", "profile": "default", "context": "default:0",
                                    "mode": "default", "provider": "fake-a", "model": "alpha-1",
                                    "state": "pending", "last": None}
    assert body["default_writable"] is True and body["default_reason"] == ""
    assert body["default"] == {"provider": "fake-a", "model": "alpha-1"}
    # A provider without credentials that is not the current one is not offered.
    assert [row["id"] for row in body["inventory"]["providers"]] == ["fake-a", "fake-b"]


def test_state_follows_the_last_outcome_of_the_same_plan(env):
    record(env)
    assert env.client.get("/api/models/selector").json()["conversation"]["state"] == "confirmed"
    record(env, status="failed", confirmed=False, error="404")
    body = env.client.get("/api/models/selector").json()["conversation"]
    assert body["state"] == "failed" and body["last"]["error"] == "404"
    record(env, status="cancelled", confirmed=False)
    assert env.client.get("/api/models/selector").json()["conversation"]["state"] == "cancelled"


def test_a_new_default_makes_a_default_conversation_pending_again(env):
    record(env)
    env.default.update(model="alpha-2")
    body = env.client.get("/api/models/selector").json()["conversation"]
    assert body["state"] == "pending" and body["model"] == "alpha-2"


def test_choosing_a_model_for_the_conversation(env):
    record(env)
    response = env.client.put("/api/models/conversation", json={
        "mode": "custom", "provider": "fake-b", "model": "beta-1", "session_id": "IRIS-iris"})
    assert response.status_code == 200
    body = response.json()["conversation"]
    assert (body["mode"], body["provider"], body["model"], body["state"]) == ("custom", "fake-b", "beta-1", "pending")
    assert env.bridge.model_selection.store.selection(None, "IRIS-iris") == {"provider": "fake-b", "model": "beta-1"}
    assert env.events == [("model_selection_changed", {"session_id": "IRIS-iris"})]


def test_choosing_while_iris_answers_is_stored_for_the_next_turn(env):
    env.agent._current_turn_task = SimpleNamespace(done=lambda: False)
    response = env.client.put("/api/models/conversation", json={"mode": "custom", "provider": "fake-b", "model": "beta-1"})
    assert response.status_code == 200
    assert response.json()["busy"] is True and response.json()["conversation"]["state"] == "pending"
    assert env.bridge.model_selection.store.selection(None, "IRIS-iris") == {"provider": "fake-b", "model": "beta-1"}


@pytest.mark.parametrize(
    ("payload", "status", "code"),
    [
        ({"mode": "custom", "provider": "fake-a", "model": "alpha-9"}, 400, "unknown_model"),
        ({"mode": "custom", "provider": "fake-z", "model": "zeta"}, 400, "unknown_provider"),
        ({"mode": "custom", "provider": "openai-codex", "model": "gpt-5"}, 400, "unknown_provider"),
        ({"mode": "custom", "provider": "fake a", "model": "alpha-1"}, 400, "invalid_selection"),
    ],
)
def test_invalid_selections_are_refused_and_nothing_changes(env, payload, status, code):
    response = env.client.put("/api/models/conversation", json=payload)
    assert response.status_code == status and response.json()["detail"]["code"] == code
    assert env.bridge.model_selection.store.selection(None, "IRIS-iris") is None
    assert env.events == []


def test_unknown_mode_is_refused(env):
    assert env.client.put("/api/models/conversation", json={"mode": "whatever"}).status_code == 400


def test_a_stale_conversation_is_refused(env):
    response = env.client.put("/api/models/conversation", json={
        "mode": "custom", "provider": "fake-b", "model": "beta-1", "session_id": "IRIS-old"})
    assert response.status_code == 409
    assert env.bridge.model_selection.store.selection(None, "IRIS-iris") is None


def test_back_to_default_clears_the_selection(env):
    env.bridge.model_selection.store.set_selection(None, "IRIS-iris", "fake-b", "beta-1")
    body = env.client.put("/api/models/conversation", json={"mode": "default"}).json()
    assert body["conversation"]["mode"] == "default" and body["conversation"]["model"] == "alpha-1"
    assert env.bridge.model_selection.store.selection(None, "IRIS-iris") is None


def test_older_hermes_reports_why_and_refuses_changes(env):
    env.bridge.supports_model_selection = False
    body = env.client.get("/api/models/selector").json()
    assert body["supported"] is False and body["reason"] == "hermes_without_session_models"
    assert body["inventory"]["providers"] == [] and env.bridge.inventory_calls == []
    assert env.client.put("/api/models/conversation", json={"mode": "default"}).status_code == 409
    assert env.client.put("/api/models/default", json={"provider": "fake-b", "model": "beta-1"}).status_code == 409


def test_offline_hermes_is_reported(env):
    env.bridge.is_available = False
    env.bridge.supports_model_selection = False
    assert env.client.get("/api/models/selector").json()["reason"] == "hermes_offline"


def test_inventory_failure_is_part_of_the_state(env):
    env.bridge.inventory_error = RuntimeError("Hermes devolvió 500.")
    body = env.client.get("/api/models/selector").json()
    assert body["inventory"] == {"providers": [], "error": "Hermes devolvió 500."}
    assert env.client.put("/api/models/conversation", json={
        "mode": "custom", "provider": "fake-b", "model": "beta-1"}).status_code == 502


def test_inventory_is_cached_and_refresh_bypasses_the_cache(env):
    env.client.get("/api/models/selector")
    env.client.get("/api/models/selector")
    env.client.get("/api/models/selector?refresh=true")
    assert env.bridge.inventory_calls == [False, True]


def test_changing_the_default_uses_hermes_writer_after_validation(env):
    env.client.get("/api/models/selector")
    response = env.client.put("/api/models/default", json={"provider": "fake-b", "model": "beta-1"})
    assert response.status_code == 200
    assert env.writes == [("fake-b", "beta-1", None)]
    assert response.json()["default"] == {"provider": "fake-b", "model": "beta-1"}
    # The inventory is reread after a default change (Hermes marks a new current row).
    assert env.bridge.inventory_calls == [False, False]
    assert env.events[-1] == ("model_selection_changed", {"session_id": "IRIS-iris", "default": True})


def test_default_change_validates_before_calling_hermes(env):
    response = env.client.put("/api/models/default", json={"provider": "fake-a", "model": "alpha-9"})
    assert response.status_code == 400 and response.json()["detail"]["code"] == "unknown_model"
    assert env.writes == []


def test_hermes_refusing_the_default_is_reported(env):
    response = env.client.put("/api/models/default", json={"provider": "fake-a", "model": "alpha-2"})
    assert response.status_code == 400 and response.json()["detail"] == "Hermes said no"
    assert env.default == {"provider": "fake-a", "model": "alpha-1"}


def test_routes_are_mounted_and_refuse_foreign_origins():
    from core.websocket_server import app

    client = TestClient(app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 50000))
    response = client.put("/api/models/conversation", json={"mode": "default"},
                          headers={"Origin": "http://attacker.example"})
    assert response.status_code == 403
    paths = {route.path for route in app.routes}
    assert {"/api/models/selector", "/api/models/conversation", "/api/models/default"} <= paths


# ── Codex review 2026-10-08: conversation context (R2, R3), plan identity (R5), remote Gateway (R6)


def test_a_write_for_another_conversation_is_refused(env):
    response = env.client.put("/api/models/conversation", json={
        "mode": "custom", "provider": "fake-b", "model": "beta-1", "context": "default:7"})
    assert response.status_code == 409
    assert env.bridge.model_selection.store.selection(None, "IRIS-iris") is None


def test_default_change_refused_when_the_profile_switches_during_validation(env):
    env.bridge.during_inventory = lambda: env.bridge.switch("IRIS-coder-1", profile="coder")
    response = env.client.put("/api/models/default", json={
        "provider": "fake-b", "model": "beta-1", "context": "default:0"})
    assert response.status_code == 409
    assert env.writes == []


def test_default_change_writes_the_profile_it_validated(env):
    env.bridge.switch("IRIS-coder-1", profile="coder")
    response = env.client.put("/api/models/default", json={
        "provider": "fake-b", "model": "beta-1", "context": "coder:1"})
    assert response.status_code == 200
    assert env.writes == [("fake-b", "beta-1", "coder")]
    assert response.json()["default_changed_for"] == "coder"
    assert env.bridge.inventory_profiles[0] == "coder"


def test_a_choice_made_during_compression_lands_on_the_child_session(env):
    env.bridge.model_selection.store.set_selection(None, "IRIS-iris", "fake-a", "alpha-2")
    env.bridge.during_inventory = lambda: env.bridge.rotate("IRIS-iris-child")
    # As the UI sends it: the session it showed (the parent) and the conversation context.
    response = env.client.put("/api/models/conversation", json={
        "mode": "custom", "provider": "fake-b", "model": "beta-1", "session_id": "IRIS-iris", "context": "default:0"})
    assert response.status_code == 200
    conversation = response.json()["conversation"]
    assert (conversation["session_id"], conversation["mode"], conversation["model"]) == ("IRIS-iris-child", "custom", "beta-1")
    plan = env.bridge.model_selection.turn_plan(None, "IRIS-iris-child")
    assert (plan["provider"], plan["model"]) == ("fake-b", "beta-1")


def test_an_explicit_switch_during_validation_leaves_the_new_conversation_alone(env):
    env.bridge.during_inventory = lambda: env.bridge.switch("IRIS-voice-2")
    response = env.client.put("/api/models/conversation", json={
        "mode": "custom", "provider": "fake-b", "model": "beta-1", "context": "default:0"})
    assert response.status_code == 409
    store = env.bridge.model_selection.store
    assert store.selection(None, "IRIS-voice-2") is None and store.selection(None, "IRIS-iris") is None


def test_state_is_read_again_if_the_conversation_switches_while_reading(env):
    env.bridge.during_inventory = lambda: env.bridge.switch("IRIS-voice-2")
    body = env.client.get("/api/models/selector").json()
    assert body["conversation"]["session_id"] == "IRIS-voice-2"
    assert body["conversation"]["context"] == "default:1"


def test_a_new_default_provider_with_the_same_model_name_is_pending(env):
    record(env)
    assert env.client.get("/api/models/selector").json()["conversation"]["state"] == "confirmed"
    env.default.update(provider="fake-b")  # same model id, other provider
    assert env.client.get("/api/models/selector").json()["conversation"]["state"] == "pending"
    plan = env.bridge.model_selection.turn_plan(None, "IRIS-iris")
    env.bridge.model_selection.store.record_outcome(None, "IRIS-iris", ms.ModelSelectionService.outcome(
        plan, {"model": "alpha-1", "model_lock": "confirmed", "provider": "custom"}, status="completed"))
    assert env.client.get("/api/models/selector").json()["conversation"]["state"] == "confirmed"


def test_a_remote_gateway_shows_its_own_default_and_cannot_be_changed_from_here(env):
    env.bridge.local = False
    body = env.client.get("/api/models/selector").json()
    assert body["default"] == {"provider": "fake-b", "model": "beta-1"}
    assert body["conversation"]["model"] == "beta-1"
    assert body["default_writable"] is False and "no es el de este equipo" in body["default_reason"]
    response = env.client.put("/api/models/default", json={"provider": "fake-a", "model": "alpha-2"})
    assert response.status_code == 409
    assert env.writes == []


# ── Codex recheck 2026-10-08: each catalog is queried and cached for the route that asked (C1)


def _other_profile(origin):
    return "coder" if origin is None else None


@pytest.mark.parametrize("origin", [None, "coder"])
def test_overlapping_refreshes_across_a_profile_switch_keep_each_catalog_on_its_profile(env, origin):
    """Two refreshes share the inventory lock; Wake switches profile while the second waits on it."""
    bridge, other = env.bridge, _other_profile(origin)
    if origin:
        bridge.switch("IRIS-coder-1", profile=origin)
    origin_session = bridge.session_id
    valid = {None: ("fake-a", "alpha-1"), "coder": ("fake-c", "gamma-1")}[origin]

    async def go():
        transport = httpx.ASGITransport(app=env.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as client:
            gate = bridge.inventory_gate = asyncio.Event()
            requests = [asyncio.create_task(client.get("/api/models/selector", params={"refresh": "true"}))
                        for _ in range(2)]
            for _ in range(500):  # both checked locality: one holds the lock, the other waits on it
                if bridge.locality_checks >= 2:
                    break
                await asyncio.sleep(0.01)
            assert bridge.locality_checks == 2 and bridge.inventory_profiles == [origin or "default"]
            bridge.switch("IRIS-other-1", profile=other)
            gate.set()
            during = [response.json() for response in await asyncio.gather(*requests)]
            bridge.switch(origin_session, profile=origin)
            back = (await client.get("/api/models/selector")).json()
            chosen = await client.put("/api/models/conversation", json={
                "mode": "custom", "provider": valid[0], "model": valid[1],
                "context": back["conversation"]["context"]})
            return during, back, chosen

    during, back, chosen = asyncio.run(go())
    # Both reads ended on the conversation that was active by then, with its own catalog.
    assert {body["conversation"]["profile"] for body in during} == {other or "default"}
    assert bridge.inventory_profiles.count(origin or "default") == 2
    expected = [row["id"] for row in ms.normalize_inventory(CATALOGS[origin])["providers"]]
    assert [row["id"] for row in back["inventory"]["providers"]] == expected
    assert chosen.status_code == 200, chosen.text
    assert bridge.model_selection.store.selection(origin, origin_session) == dict(zip(("provider", "model"), valid))


@pytest.mark.parametrize("origin", [None, "coder"])
def test_a_selection_is_validated_against_the_catalog_of_its_own_profile(env, origin):
    bridge, other = env.bridge, _other_profile(origin)
    if origin:
        bridge.switch("IRIS-coder-1", profile=origin)
    only_origin = {None: ("fake-a", "alpha-1"), "coder": ("fake-c", "gamma-1")}[origin]
    env.client.get("/api/models/selector")
    bridge.switch("IRIS-other-1", profile=other)
    env.client.get("/api/models/selector")  # both catalogs are cached now
    response = env.client.put("/api/models/conversation", json={
        "mode": "custom", "provider": only_origin[0], "model": only_origin[1],
        "context": bridge.route_context()["token"]})
    assert response.status_code == 400 and response.json()["detail"]["code"] in ("unknown_provider", "unknown_model")
    assert bridge.model_selection.store.selection(other, "IRIS-other-1") is None


# ── Modelos del selector (qa9): which models the selector lists ────────────

def test_without_a_choice_the_selector_lists_every_model(env):
    assert env.client.get("/api/models/selector").json()["visible"] is None


def test_the_chosen_models_are_saved_and_returned_with_the_state(env):
    chosen = [{"provider": "fake-b", "model": "beta-1"}, {"provider": "fake-b", "model": "beta-1"}]
    response = env.client.put("/api/models/visible", json={"models": chosen})
    assert response.status_code == 200
    assert response.json() == {"visible": [{"provider": "fake-b", "model": "beta-1"}]}  # duplicates folded
    assert env.client.get("/api/models/selector").json()["visible"] == [{"provider": "fake-b", "model": "beta-1"}]
    assert env.events[-1] == ("model_selection_changed", {"visible": True})
    # Back to every model.
    assert env.client.put("/api/models/visible", json={"models": None}).json() == {"visible": None}
    assert env.visibility.load() is None


def test_hiding_a_model_never_changes_what_a_conversation_may_choose(env):
    env.client.put("/api/models/visible", json={"models": [{"provider": "fake-b", "model": "beta-1"}]})
    response = env.client.put("/api/models/conversation", json={
        "mode": "custom", "provider": "fake-a", "model": "alpha-2", "session_id": "IRIS-iris"})
    assert response.status_code == 200  # the catalog decides, not the list shown


@pytest.mark.parametrize("payload", [{}, {"models": "all"}, {"models": [{"provider": "fake-a"}]},
                                     {"models": [{"provider": "bad provider", "model": "x"}]},
                                     {"models": [{"provider": "fake-a", "model": "x" * 300}]}])
def test_invalid_choices_are_refused_without_changing_the_saved_one(env, payload):
    env.client.put("/api/models/visible", json={"models": [{"provider": "fake-b", "model": "beta-1"}]})
    assert env.client.put("/api/models/visible", json=payload).status_code == 400
    assert env.visibility.load() == [{"provider": "fake-b", "model": "beta-1"}]


def test_an_unreadable_choice_lists_every_model(env):
    env.visibility.path.write_text("{not json", encoding="utf-8")
    assert env.client.get("/api/models/selector").json()["visible"] is None
