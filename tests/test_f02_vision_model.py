"""F02: the auxiliary vision model of the profile that serves the conversation.

The worker runs against a fake Hermes package tree (same names as the pinned
0.21.6 API it uses); the routes and the turn use fake bridges. The contract of
the real pinned Hermes is measured in ``artifacts/f02-vision-contract``.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import model_selection as ms
from core import vision_model
from core.vision_model_api import vision_model_router

# ── Hermes' routing rule, as Iris applies it ──────────────────────────────


@pytest.mark.parametrize("raw, explicit, route", [
    ({}, False, "chat"),
    ({"auxiliary": {"vision": {"provider": "auto", "model": ""}}}, False, "chat"),
    ({"auxiliary": {"vision": {"provider": "AUTO"}}}, False, "chat"),
    ({"auxiliary": {"vision": {"provider": "openrouter"}}}, True, "vision_model"),
    ({"auxiliary": {"vision": {"provider": "auto", "model": "gemini-3-flash"}}}, True, "vision_model"),
    ({"auxiliary": {"vision": {"base_url": "http://127.0.0.1:9/v1"}}}, True, "vision_model"),
    # agent.image_input_mode is absolute, like decide_image_input_mode.
    ({"agent": {"image_input_mode": "native"}, "auxiliary": {"vision": {"provider": "openrouter"}}}, True, "chat"),
    ({"agent": {"image_input_mode": "text"}}, False, "vision_model"),
    ({"agent": {"image_input_mode": "rare"}}, False, "chat"),
])
def test_iris_routes_images_like_hermes_decides_them(raw, explicit, route):
    settings = vision_model.configured(raw)
    assert vision_model.explicit(settings) is explicit
    assert vision_model.image_route(settings) == route


def test_settings_are_read_from_the_profile_file_without_starting_hermes(tmp_path):
    (tmp_path / "config.yaml").write_text(
        "auxiliary:\n  vision:\n    provider: custom:local\n    model: llava\n    api_key: secreto\n", "utf-8")
    settings = vision_model.read_configured(tmp_path)
    assert settings == {"provider": "custom:local", "model": "llava", "custom_endpoint": False,
                        "own_key": True, "image_input_mode": "auto"}
    assert "secreto" not in json.dumps(settings)
    (tmp_path / "config.yaml").write_text("{ no es yaml", "utf-8")
    assert vision_model.read_configured(tmp_path)["provider"] == "auto"  # unreadable: automatic


# ── The worker against a fake Hermes ──────────────────────────────────────

FAKE_HERMES = {
    "hermes_cli/__init__.py": "",
    "hermes_cli/config.py": '''
        import json, os
        PATH = os.path.join(os.environ["HERMES_HOME"], "config.json")
        def read_raw_config():
            return json.load(open(PATH, encoding="utf-8")) if os.path.exists(PATH) else {}
        def load_config():
            return read_raw_config()
        def save_config(cfg):
            json.dump(cfg, open(PATH, "w", encoding="utf-8"))
    ''',
    "agent/__init__.py": "",
    "agent/auxiliary_client.py": '''
        import contextlib, json, os
        from hermes_cli.config import read_raw_config
        LOG = os.path.join(os.environ["HERMES_HOME"], "calls.jsonl")
        def log(entry):
            with open(LOG, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry) + "\\n")
        @contextlib.contextmanager
        def aux_probe_mode():
            yield
        def resolve_vision_provider_client():
            vision = read_raw_config().get("auxiliary", {}).get("vision", {})
            provider = vision.get("provider") or "auto"
            if provider == "unavailable":
                return provider, None, None
            if provider == "auto":
                return "main", object(), "main-model"
            return provider, object(), vision.get("model") or "default"
        def _ladder_provider_fallback(first_err, route):
            yield "another-provider"
        async def async_call_llm(**kwargs):
            route = kwargs.get("route_info")
            vision = read_raw_config().get("auxiliary", {}).get("vision", {})
            if route is not None:
                route.update(provider=vision.get("provider") or "main", model=vision.get("model") or "main-model")
            # The fallback rung Iris must have replaced for this process:
            rung = list(_ladder_provider_fallback(None, None)) if callable(_ladder_provider_fallback) else []
            log({"call": kwargs["messages"], "fallback_rung_yields": rung})
            return "analysis"
    ''',
    "agent/image_routing.py": '''
        def _lookup_supports_vision(provider, model, cfg):
            return {"lee": True, "texto": False}.get(model)
    ''',
    "tools/__init__.py": "",
    "tools/vision_tools.py": '''
        import json
        async_call_llm = None
        def _load_auxiliary_client():
            global async_call_llm
            import agent.auxiliary_client as aux
            async_call_llm = async_call_llm or aux.async_call_llm
        async def vision_analyze_tool(image_url, user_prompt, model=None):
            _load_auxiliary_client()
            if not image_url.startswith("data:image/"):
                return json.dumps({"success": False, "analysis": "not an image"})
            await async_call_llm(task="vision", messages=[image_url[:20], user_prompt])
            return json.dumps({"success": True, "analysis": "Se ve una manzana roja."})
    ''',
}


@pytest.fixture
def fake_hermes(tmp_path):
    root = tmp_path / "hermes"
    for name, source in FAKE_HERMES.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(source), "utf-8")
    home = tmp_path / "perfil"
    home.mkdir()

    def configure(vision=None):
        (home / "config.json").write_text(json.dumps({"auxiliary": {"vision": vision}} if vision else {}), "utf-8")

    def run(request):
        env = {**os.environ, "HERMES_HOME": str(home)}
        done = subprocess.run([sys.executable, "-B", "-c", vision_model._bootstrap()], input=json.dumps(request),
                              capture_output=True, text=True, cwd=root, env=env, timeout=60)
        line = next(l for l in reversed(done.stdout.splitlines()) if l.startswith(vision_model.MARKER))
        return json.loads(line[len(vision_model.MARKER):])

    def calls():
        log = home / "calls.jsonl"
        return [json.loads(line) for line in log.read_text("utf-8").splitlines()] if log.exists() else []

    return SimpleNamespace(root=root, home=home, configure=configure, run=run, calls=calls)


IMAGE = "data:image/png;base64,iVBORw0KGgo="


def test_the_worker_reports_the_route_that_answered_with_the_fallback_rung_off(fake_hermes):
    fake_hermes.configure({"provider": "visionprov", "model": "lee"})
    result = fake_hermes.run({"command": "analyze", "images": [IMAGE, IMAGE], "prompt": "¿qué tengo en la mano?"})
    assert result == {"ok": True, "analyses": ["Se ve una manzana roja."] * 2,
                      "planned": {"provider": "visionprov", "model": "lee", "available": True},
                      "route": {"provider": "visionprov", "model": "lee"}}
    calls = fake_hermes.calls()
    assert len(calls) == 2
    # Hermes' provider-fallback rung yields nothing inside the worker: no second provider.
    assert all(call["fallback_rung_yields"] == [] for call in calls)


@pytest.mark.parametrize("vision, expected, code", [
    ({"provider": "unavailable", "model": "x"}, None, "vision_unavailable"),
    ({"provider": "visionprov", "model": "lee"}, {"provider": "otro", "model": "lee"}, "route_changed"),
])
def test_the_worker_refuses_before_sending_anything(fake_hermes, vision, expected, code):
    # Hermes alone falls back to its automatic route for an unresolvable
    # provider (measured: the image reached the chat endpoint). Iris refuses.
    fake_hermes.configure(vision)
    request = {"command": "analyze", "images": [IMAGE], "prompt": "p"}
    if expected:
        request["expected"] = expected
    assert fake_hermes.run(request)["code"] == code
    assert fake_hermes.calls() == []


def test_the_worker_reports_status_capabilities_and_writes_through_hermes(fake_hermes):
    fake_hermes.configure(None)
    status = fake_hermes.run({"command": "status", "models": [
        {"provider": "p", "model": "lee"}, {"provider": "p", "model": "texto"}, {"provider": "p", "model": "otro"}]})
    assert status["configured"]["provider"] == "auto"
    assert status["planned"] == {"provider": "main", "model": "main-model", "available": True}
    assert [row["vision"] for row in status["models"]] == [True, False, None]

    written = fake_hermes.run({"command": "write", "provider": "visionprov", "model": "lee"})
    assert written["ok"] and written["configured"]["model"] == "lee"
    assert written["planned"] == {"provider": "visionprov", "model": "lee", "available": True}
    stored = json.loads((fake_hermes.home / "config.json").read_text("utf-8"))
    assert stored == {"auxiliary": {"vision": {"provider": "visionprov", "model": "lee"}}}
    back = fake_hermes.run({"command": "write", "mode": "auto"})
    assert back["configured"]["provider"] == "auto" and back["configured"]["model"] == ""
    assert fake_hermes.run({"command": "write", "provider": "auto", "model": "x"})["code"] == "invalid_selection"


def test_the_worker_keeps_a_custom_endpoint_set_outside_iris(fake_hermes):
    fake_hermes.configure({"provider": "auto", "base_url": "http://127.0.0.1:9/v1", "api_key": "k"})
    result = fake_hermes.run({"command": "write", "provider": "visionprov", "model": "lee"})
    assert result["ok"] is False and result["code"] == "custom_endpoint"
    stored = json.loads((fake_hermes.home / "config.json").read_text("utf-8"))
    assert stored["auxiliary"]["vision"]["api_key"] == "k"  # untouched


def test_a_bad_request_and_a_missing_hermes_api_are_classified(fake_hermes):
    assert fake_hermes.run({"command": "borrar"})["code"] == "invalid_request"
    (fake_hermes.root / "agent/auxiliary_client.py").write_text("raise ImportError('no')\n", "utf-8")
    fake_hermes.configure({"provider": "visionprov", "model": "lee"})
    assert fake_hermes.run({"command": "status"})["code"] == "unsupported_runtime"


def test_run_worker_uses_hermes_python_in_the_captured_profile(monkeypatch, tmp_path):
    seen = {}

    def run(command, **kwargs):
        seen.update(command=command, **kwargs)
        return SimpleNamespace(returncode=0, stdout='log\nIRIS_VISION={"ok": true, "configured": {}, "planned": {}}\n', stderr="")

    import wizard.provider_sync as provider_sync

    monkeypatch.setattr(provider_sync, "_resolve_hermes_python", lambda: Path("hermes-python"))
    monkeypatch.setattr(vision_model.subprocess, "run", run)
    assert vision_model.run_worker(tmp_path / "perfil B", {"command": "status"})["ok"] is True
    assert seen["env"]["HERMES_HOME"] == str(tmp_path / "perfil B")
    assert seen["command"][-2] == "-c" and "runpy.run_path" in seen["command"][-1]  # no Iris folder on sys.path
    assert json.loads(seen["input"]) == {"command": "status"}
    monkeypatch.setattr(vision_model.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr="x"))
    assert vision_model.run_worker(tmp_path, {"command": "status"}) == {"ok": False, "code": "no_output"}


# ── Routes: profiles A/B, catalog, capability, persistence ────────────────

OPTIONS = {"providers": [
    {"slug": "fake-a", "name": "Fake A", "models": ["lee", "texto", "quiza"], "is_current": True, "authenticated": True},
    {"slug": "fake-off", "name": "Sin sesión", "models": ["lee"], "is_current": False, "authenticated": False},
]}
CODER = {"providers": [{"slug": "fake-c", "name": "Fake C", "models": ["lee-c"], "is_current": True, "authenticated": True}]}
VERDICTS = {"lee": True, "texto": False, "quiza": None, "lee-c": True}


class VisionBridge:
    def __init__(self):
        self.active_profile, self.generation, self.local = None, 0, True
        self.during_status = None

    def route_context(self):
        profile = self.active_profile
        return {"profile": profile, "session_id": "s", "generation": self.generation,
                "token": f"{profile or 'default'}:{self.generation}", "api_key": ""}

    async def model_inventory(self, *, refresh=False, route=None):
        return ms.normalize_inventory(CODER if route["profile"] == "coder" else OPTIONS)

    async def gateway_is_local(self):
        return self.local


@pytest.fixture
def vision_app(monkeypatch, tmp_path):
    bridge = VisionBridge()
    homes = {None: tmp_path / "default", "coder": tmp_path / "coder"}
    for home in homes.values():
        home.mkdir()
    store = {home: {"provider": "auto", "model": "", "custom_endpoint": False, "own_key": False,
                    "image_input_mode": "auto"} for home in homes.values()}
    calls = SimpleNamespace(status=[], write=[], broadcasts=[])
    monkeypatch.setattr(vision_model, "profile_home", lambda profile: homes[profile])

    def status(home, pairs=None):
        calls.status.append(home)
        if bridge.during_status:
            hook, bridge.during_status = bridge.during_status, None
            hook()
        settings = store[home]
        planned = ({"provider": settings["provider"], "model": settings["model"], "available": True}
                   if settings["provider"] != "auto" else {"provider": "fake-a", "model": "lee", "available": True})
        return {"configured": dict(settings), "planned": planned,
                "capabilities": {(p["provider"], p["model"]): VERDICTS.get(p["model"]) for p in pairs or []}}

    def write(home, *, provider="", model=""):
        calls.write.append((home, provider, model))
        store[home].update(provider=provider or "auto", model=model)
        return {"configured": dict(store[home]), "planned": {"provider": provider or "fake-a", "model": model or "lee",
                                                              "available": True}}

    monkeypatch.setattr(vision_model, "status", status)
    monkeypatch.setattr(vision_model, "write", write)
    monkeypatch.setattr(vision_model, "read_configured", lambda home: dict(store[home]))

    async def broadcast(event, payload):
        calls.broadcasts.append((event, payload))

    app = FastAPI()
    app.include_router(vision_model_router(agent=lambda: SimpleNamespace(bridge=bridge), broadcast=broadcast))
    return SimpleNamespace(client=TestClient(app), bridge=bridge, homes=homes, store=store, calls=calls)


def test_the_view_lists_authenticated_models_with_their_image_capability(vision_app):
    body = vision_app.client.get("/api/vision-model").json()
    assert body["profile"] == "default" and body["mode"] == "auto" and body["route"] == "chat"
    assert body["planned"] == {"provider": "fake-a", "model": "lee", "available": True}
    assert [(m["provider"], m["model"], m["vision"]) for m in body["models"]] == [
        ("fake-a", "lee", True), ("fake-a", "texto", False), ("fake-a", "quiza", None)]  # no unauthenticated rows
    assert body["writable"] is True and body["last"] is None
    vision_app.client.get("/api/vision-model")
    assert len(vision_app.calls.status) == 1  # cached per profile
    vision_app.client.get("/api/vision-model?refresh=true")
    assert len(vision_app.calls.status) == 2


def test_choosing_a_model_writes_only_the_captured_profile_and_switches_the_route(vision_app):
    body = vision_app.client.put("/api/vision-model", json={"profile": "default", "mode": "model",
                                                             "provider": "fake-a", "model": "lee"}).json()
    assert vision_app.calls.write == [(vision_app.homes[None], "fake-a", "lee")]
    assert body["mode"] == "model" and body["route"] == "vision_model" and body["chosen_vision"] is True
    assert vision_app.store[vision_app.homes["coder"]]["provider"] == "auto"  # the other profile is untouched
    assert vision_app.calls.broadcasts == [("vision_model_changed", {"profile": "default"})]
    # Unknown capability may be chosen; the view says so.
    assert vision_app.client.put("/api/vision-model", json={"mode": "model", "provider": "fake-a",
                                                            "model": "quiza"}).json()["chosen_vision"] is None
    back = vision_app.client.put("/api/vision-model", json={"mode": "auto"}).json()
    assert back["mode"] == "auto" and vision_app.calls.write[-1] == (vision_app.homes[None], "", "")


@pytest.mark.parametrize("payload, status, code", [
    ({"mode": "model", "provider": "fake-a", "model": "texto"}, 400, "model_without_vision"),
    # F01's catalog leaves out a provider without a session (unless it is the current one).
    ({"mode": "model", "provider": "fake-off", "model": "lee"}, 400, "unknown_provider"),
    ({"mode": "model", "provider": "fake-a", "model": "inventado"}, 400, "unknown_model"),
    ({"mode": "model", "provider": "fake-c", "model": "lee-c"}, 400, "unknown_provider"),  # coder's, not default's
])
def test_only_catalog_models_that_may_read_images_are_accepted(vision_app, payload, status, code):
    response = vision_app.client.put("/api/vision-model", json=payload)
    assert response.status_code == status and response.json()["detail"]["code"] == code
    assert vision_app.calls.write == []


def test_a_profile_switch_while_validating_refuses_the_write(vision_app):
    vision_app.client.get("/api/vision-model")  # warm: capabilities are read from the cache
    vision_app.client.get("/api/vision-model?refresh=true")
    vision_app.bridge.during_status = lambda: setattr(vision_app.bridge, "active_profile", "coder")
    # A stale view (profile 'default') cannot write while 'coder' is active.
    vision_app.bridge.active_profile = "coder"
    response = vision_app.client.put("/api/vision-model", json={"profile": "default", "mode": "auto"})
    assert response.status_code == 409 and vision_app.calls.write == []
    vision_app.bridge.active_profile = None
    vision_app.bridge.during_status = None

    async def switching_inventory(*, refresh=False, route=None):
        vision_app.bridge.active_profile = "coder"  # Continuity switched during the catalog query
        return ms.normalize_inventory(OPTIONS)

    vision_app.bridge.model_inventory = switching_inventory
    response = vision_app.client.put("/api/vision-model", json={"mode": "model", "provider": "fake-a", "model": "lee"})
    assert response.status_code == 409 and vision_app.calls.write == []


def test_each_profile_has_its_own_view_and_a_remote_gateway_is_read_only(vision_app):
    vision_app.bridge.active_profile = "coder"
    body = vision_app.client.get("/api/vision-model").json()
    assert body["profile"] == "coder" and [m["model"] for m in body["models"]] == ["lee-c"]
    vision_app.client.put("/api/vision-model", json={"profile": "coder", "mode": "model", "provider": "fake-c",
                                                     "model": "lee-c"})
    assert vision_app.calls.write == [(vision_app.homes["coder"], "fake-c", "lee-c")]
    vision_app.bridge.local = False
    assert vision_app.client.get("/api/vision-model").json()["writable"] is False
    assert vision_app.client.put("/api/vision-model", json={"mode": "auto"}).status_code == 409


def test_a_custom_endpoint_makes_the_view_read_only(vision_app):
    vision_app.store[vision_app.homes[None]].update(custom_endpoint=True, provider="auto")
    body = vision_app.client.get("/api/vision-model").json()
    assert body["mode"] == "model" and body["writable"] is False


# ── The turn: images reach Hermes by the profile's route ─────────────────


class TurnBridge:
    is_available = True

    def __init__(self):
        self.active_profile, self.generation, self.sent = "coder", 3, []

    def route_context(self):
        return {"profile": self.active_profile, "session_id": "s", "generation": self.generation,
                "token": f"{self.active_profile or 'default'}:{self.generation}", "api_key": ""}

    async def send_message(self, text, **kwargs):
        self.sent.append({"text": text, "images": kwargs.get("images")})
        return {"texto_hablado": "Es una manzana.", "emocion": "feliz", "animacion_virtual": "idle"}


def turn_agent(bridge):
    from core.agent import IRISAgent

    agent = IRISAgent.__new__(IRISAgent)
    agent.bridge = bridge
    agent._continuity_active = False
    agent._build_emotion_context = lambda: ""
    agent._on_tool_progress = lambda event: None
    return agent


def test_with_the_automatic_setting_images_travel_with_the_turn(monkeypatch, tmp_path):
    bridge = TurnBridge()
    monkeypatch.setattr(vision_model, "read_configured", lambda home: vision_model.configured({}))
    monkeypatch.setattr(vision_model, "analyze", lambda *a, **k: pytest.fail("no analysis in automatic mode"))
    asyncio.run(turn_agent(bridge)._think("¿qué es?", images=[IMAGE]))
    assert bridge.sent == [{"text": "¿qué es?", "images": [IMAGE]}]


def test_an_explicit_vision_model_describes_the_images_and_the_chat_gets_text(monkeypatch, tmp_path):
    bridge = TurnBridge()
    homes = []
    monkeypatch.setattr(vision_model, "profile_home", lambda profile: tmp_path / str(profile))
    monkeypatch.setattr(vision_model, "read_configured", lambda home: vision_model.configured(
        {"auxiliary": {"vision": {"provider": "fake-v", "model": "ojo"}}}))

    async def analyze(home, images, prompt, **kwargs):
        homes.append(home)
        assert "¿qué tengo en la mano?" in prompt
        return {"analyses": ["Una manzana roja."], "planned": {}, "route": {"provider": "fake-v", "model": "ojo"}}

    monkeypatch.setattr(vision_model, "analyze", analyze)
    reply = asyncio.run(turn_agent(bridge)._think("¿qué tengo en la mano?", images=[IMAGE]))
    assert reply["texto_hablado"] == "Es una manzana."
    assert homes == [tmp_path / "coder"]  # the profile of the conversation
    [sent] = bridge.sent
    assert sent["images"] is None  # one provider sees the image: the vision model
    assert "ojo (fake-v)" in sent["text"] and "Una manzana roja." in sent["text"]
    assert sent["text"].endswith("¿qué tengo en la mano?")
    assert vision_model.last_analysis("coder")["model"] == "ojo"


def test_a_failed_analysis_answers_honestly_without_a_chat_turn(monkeypatch, tmp_path):
    bridge = TurnBridge()
    monkeypatch.setattr(vision_model, "read_configured", lambda home: vision_model.configured(
        {"auxiliary": {"vision": {"provider": "fake-v", "model": "ojo"}}}))

    async def analyze(*args, **kwargs):
        raise vision_model.VisionModelError("vision_unavailable", vision_model.MESSAGES["vision_unavailable"])

    monkeypatch.setattr(vision_model, "analyze", analyze)
    reply = asyncio.run(turn_agent(bridge)._think("mira", images=[IMAGE]))
    assert "No pude analizar la imagen" in reply["texto_hablado"] and bridge.sent == []


def test_a_conversation_switch_during_the_analysis_discards_it(monkeypatch, tmp_path):
    bridge = TurnBridge()
    monkeypatch.setattr(vision_model, "read_configured", lambda home: vision_model.configured(
        {"auxiliary": {"vision": {"provider": "fake-v", "model": "ojo"}}}))

    async def analyze(*args, **kwargs):
        bridge.generation += 1  # Wake/Continuity switched meanwhile
        return {"analyses": ["x"], "planned": {}, "route": {"provider": "fake-v", "model": "ojo"}}

    monkeypatch.setattr(vision_model, "analyze", analyze)
    reply = asyncio.run(turn_agent(bridge)._think("mira", images=[IMAGE]))
    assert "La conversación cambió" in reply["texto_hablado"] and bridge.sent == []


def test_cancelling_a_turn_ends_the_analysis_before_the_image_is_sent(fake_hermes, monkeypatch):
    """Stop or a new turn cancels the analysis: Hermes' process ends, nothing goes out."""
    import wizard.provider_sync as provider_sync

    tool = fake_hermes.root / "tools/vision_tools.py"
    tool.write_text(tool.read_text("utf-8").replace(
        "    _load_auxiliary_client()\n    if not image_url",
        "    _load_auxiliary_client()\n    import time; time.sleep(30)\n    if not image_url"), "utf-8")
    fake_hermes.configure({"provider": "visionprov", "model": "lee"})
    monkeypatch.setattr(provider_sync, "_resolve_hermes_python", lambda: Path(sys.executable))
    monkeypatch.setattr(provider_sync, "_hermes_python_probe_command", lambda python, probe: [str(python), "-B", "-c", probe])
    monkeypatch.setattr(provider_sync, "_hermes_probe_kwargs", lambda timeout: {
        "env": dict(os.environ), "cwd": str(fake_hermes.root), "timeout": timeout})

    async def scenario():
        task = asyncio.create_task(vision_model.analyze(fake_hermes.home, [IMAGE], "p"))
        await asyncio.sleep(4)  # the worker is waiting before its request
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    started = time.monotonic()
    asyncio.run(scenario())
    assert time.monotonic() - started < 20  # not the 30 s the worker would have slept
    time.sleep(1)
    assert fake_hermes.calls() == []


def test_the_async_worker_returns_the_analysis(fake_hermes, monkeypatch):
    import wizard.provider_sync as provider_sync

    fake_hermes.configure({"provider": "visionprov", "model": "lee"})
    monkeypatch.setattr(provider_sync, "_resolve_hermes_python", lambda: Path(sys.executable))
    monkeypatch.setattr(provider_sync, "_hermes_python_probe_command", lambda python, probe: [str(python), "-B", "-c", probe])
    monkeypatch.setattr(provider_sync, "_hermes_probe_kwargs", lambda timeout: {
        "env": dict(os.environ), "cwd": str(fake_hermes.root), "timeout": timeout})
    result = asyncio.run(vision_model.analyze(fake_hermes.home, [IMAGE], "p", expected={"provider": "visionprov", "model": "lee"}))
    assert result["analyses"] == ["Se ve una manzana roja."]
    assert result["route"] == {"provider": "visionprov", "model": "lee"}
    with pytest.raises(vision_model.VisionModelError) as refused:
        asyncio.run(vision_model.analyze(fake_hermes.home, [IMAGE], "p", expected={"provider": "otro", "model": "x"}))
    assert refused.value.code == "route_changed"
