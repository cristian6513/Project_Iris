"""F07 layers 2 and 3: the inbox's persistence and API, and the visual notice on return.

The store is real (SQLite in a temporary profile); Hermes, the camera and TTS
are never involved: the presenter only reads the store and sends one message.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import closing
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.automation_contracts import AutomationRule, new_id
from core.automation_store import (
    SCHEMA_VERSION,
    AutomationStore,
    InvalidTransition,
    PresentationRefused,
    RevisionConflict,
    UnknownDelivery,
)
from core.inbox_api import InboxPresenter, InboxService, inbox_router
from core.inbox_contracts import inbox_key
from core.surface_registry import SurfaceRegistry

NOW = 1_800_000_000.0


@pytest.fixture
def env(tmp_path):
    clock = {"now": NOW}
    store = AutomationStore(tmp_path / "automations" / "automations.sqlite3", clock=lambda: clock["now"])
    rule = store.create_rule(AutomationRule.model_validate({
        "id": new_id(), "title": "Investigar al irme", "enabled": True,
        "trigger": {"source": "iris_idle", "threshold_s": 300}, "execution": {"prompt": "Tarea sintética"},
        "delivery": {"expiration_s": 3600}, "created_at": NOW, "updated_at": NOW}))
    return SimpleNamespace(store=store, rule=rule, clock=clock, path=store.path)


def sent(env, key):
    occurrence, _ = env.store.record_candidate(env.rule.id, env.rule.revision, key)
    claimed = env.store.claim(occurrence.id, "core-1")
    env.store.admit(occurrence.id, claimed.claim.token, body={"input": key}, idempotency_key="k-" + occurrence.id,
                    context={}, max_parallel=10, quota_since=0)
    return env.store.advance(occurrence.id, "running")


def finished(env, key, status="completed", text="Resultado sintético."):
    occurrence = sent(env, key)
    return env.store.finish(occurrence.id, status=status, text=text)


def service(env, vision=None):
    return InboxService(store=lambda: env.store, store_problem=lambda: "", vision=vision,
                        clock=lambda: env.clock["now"])


# ── Persistence ──────────────────────────────────────────────────────────────


def test_a_v1_store_is_migrated_on_open_and_its_deliveries_are_not_rewritten_by_reading(env):
    _, result, _ = finished(env, "k1")
    v1 = json.dumps({"schema": "iris.automation-delivery/v1", "result_id": result.id, "status": "pending",
                     "presented_at": None, "read_at": None, "voice_attempts": 0, "voice_receipt": "",
                     "expires_at": NOW + 3600}, sort_keys=True)
    with closing(sqlite3.connect(env.path)) as conn:  # what a v1 Iris left behind
        conn.execute("PRAGMA locking_mode = EXCLUSIVE")
        conn.execute("UPDATE deliveries SET document = ? WHERE result_id = ?", (v1, result.id))
        conn.execute("DROP TABLE presentations")
        conn.execute("PRAGMA user_version = 1")
        conn.commit()
    reopened = AutomationStore(env.path, clock=lambda: env.clock["now"])
    with closing(sqlite3.connect(env.path)) as conn:
        conn.execute("PRAGMA locking_mode = EXCLUSIVE")
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 2
        assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'presentations'").fetchone()
    assert any(row["kind"] == "store_migrated" for row in reopened.history())
    assert reopened.delivery(result.id).revision == 1
    reopened.inbox_rows()  # reading the inbox
    with closing(sqlite3.connect(env.path)) as conn:
        conn.execute("PRAGMA locking_mode = EXCLUSIVE")
        assert conn.execute("SELECT document FROM deliveries WHERE result_id = ?", (result.id,)).fetchone()[0] == v1
    updated, changed = reopened.act_on_delivery(result.id, "read", expected_revision=1)
    assert changed and updated.revision == 2 and updated.schema_name == "iris.automation-delivery/v2"


def test_results_live_in_their_own_transaction_and_survive_a_restart(env):
    occurrence, result, delivery = finished(env, "k1")
    assert delivery.expires_at == NOW + 3600  # the rule's own expiry
    restarted = AutomationStore(env.path, clock=lambda: env.clock["now"])  # Core restarted right after finish()
    snapshot = InboxService(store=lambda: restarted, store_problem=lambda: "", clock=lambda: NOW).snapshot()
    [item] = snapshot["items"]
    assert item["key"] == inbox_key("automation", "default", occurrence.id)
    assert item["delivery"]["status"] == "pending" and snapshot["unread"] == 1


def test_actions_name_their_revision_and_repeats_are_no_ops(env):
    _, result, _ = finished(env, "k1")
    store = env.store
    read, changed = store.act_on_delivery(result.id, "read", expected_revision=1)
    assert changed and read.status == "read" and read.presented_at is None  # reading is not presenting
    again, changed = store.act_on_delivery(result.id, "read", expected_revision=1)  # the answer was lost
    assert not changed and again.revision == read.revision
    with pytest.raises(RevisionConflict):
        store.act_on_delivery(result.id, "dismiss", expected_revision=1)
    with pytest.raises(InvalidTransition):
        store.act_on_delivery(result.id, "snooze", expected_revision=read.revision, minutes=30)  # read: no snooze
    unread, _ = store.act_on_delivery(result.id, "unread", expected_revision=read.revision)
    assert unread.status == "pending" and unread.read_at is None
    dismissed, _ = store.act_on_delivery(result.id, "dismiss", expected_revision=unread.revision)
    restored, _ = store.act_on_delivery(result.id, "restore", expected_revision=dismissed.revision)
    assert restored.status == "pending" and restored.dismissed_at is None
    with pytest.raises(UnknownDelivery):
        store.act_on_delivery(new_id(), "read", expected_revision=1)


def test_snoozing_comes_back_when_due_and_never_reruns_or_changes_the_result(env):
    occurrence, result, _ = finished(env, "k1")
    before = (env.store.get_occurrence(occurrence.id), env.store.result(result.id), len(env.store.occurrences()))
    snoozed, _ = env.store.act_on_delivery(result.id, "snooze", expected_revision=1, minutes=240)
    assert snoozed.expires_at >= snoozed.snoozed_until + 3600  # the reminder outlives the expiry
    assert (env.store.get_occurrence(occurrence.id), env.store.result(result.id),
            len(env.store.occurrences())) == before
    assert service(env).snapshot()["items"][0]["delivery"]["status"] == "snoozed"
    env.clock["now"] = NOW + 240 * 60 + 1
    item = service(env).snapshot()["items"][0]
    assert item["delivery"]["status"] == "pending" and item["actions"] == ["read", "snooze", "dismiss"]


def test_expiry_is_derived_without_writing(env):
    _, result, _ = finished(env, "k1")
    env.clock["now"] = NOW + 3601
    assert service(env).snapshot()["items"][0]["delivery"]["status"] == "expired"
    assert env.store.delivery(result.id).status == "pending" and env.store.delivery(result.id).revision == 1


def test_a_failed_write_answers_an_error_and_changes_nothing(env, monkeypatch):
    occurrence, result, _ = finished(env, "k1")
    real = env.store._history

    def broken(*args, **kwargs):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(env.store, "_history", broken)
    with pytest.raises(sqlite3.OperationalError):
        env.store.act_on_delivery(result.id, "read", expected_revision=1)
    monkeypatch.setattr(env.store, "_history", real)
    assert env.store.delivery(result.id).status == "pending" and env.store.delivery(result.id).revision == 1


def test_listeners_hear_committed_changes_and_cannot_undo_them(env):
    heard = []
    env.store.subscribe(heard.append)
    env.store.subscribe(lambda kind: 1 / 0)
    _, result, _ = finished(env, "k1")
    env.store.act_on_delivery(result.id, "read", expected_revision=1)
    assert heard == ["result", "delivery"]
    assert env.store.delivery(result.id).status == "read"


# ── API ───────────────────────────────────────────────────────────────────────


def client(svc, changed=None):
    app = FastAPI()
    app.include_router(inbox_router(lambda: svc, changed=changed or AsyncMock()))
    return TestClient(app)


VISION = {"revision": 4, "items": [{"schema": "iris.notification/v1", "source": "vision", "id": "c" * 32,
                                    "title": "Pausa activa", "body": "Llevas 45 minutos.", "unread": True,
                                    "status": "active", "created_at": NOW - 10}]}


def test_the_inbox_shows_both_sources_as_separate_items(env):
    occurrence, _, _ = finished(env, "k1", status="failed", text="Hermes no respondió a tiempo.")
    body = client(service(env, vision=lambda: VISION)).get("/api/inbox").json()
    keys = [item["key"] for item in body["items"]]
    assert keys == [inbox_key("automation", "default", occurrence.id), inbox_key("vision", "default", "c" * 32)]
    assert body["unread"] == 2 and body["items"][0]["outcome"] == "failed"
    assert body["items"][1]["actions"] == [] and body["items"][1]["delivery"]["revision"] == 4
    assert body["sources"] == {"automation": {"status": "ok", "message": ""},
                               "vision": {"status": "ok", "message": "", "revision": 4}}


def test_a_source_that_fails_is_reported_never_shown_as_empty(env):
    finished(env, "k1")

    def broken():
        raise OSError("bandeja de Vision ilegible")

    body = client(service(env, vision=broken)).get("/api/inbox").json()
    assert body["sources"]["vision"]["status"] == "unavailable" and len(body["items"]) == 1
    missing = InboxService(store=lambda: None, store_problem=lambda: "El perfil contiene un enlace.")
    body = client(missing).get("/api/inbox").json()
    assert body["sources"]["automation"] == {"status": "unavailable", "message": "El perfil contiene un enlace."}


def test_actions_through_the_api(env):
    occurrence, result, _ = finished(env, "k1")
    changed = AsyncMock()
    api = client(service(env, vision=lambda: VISION), changed)
    key = inbox_key("automation", "default", occurrence.id)
    answer = api.post("/api/inbox/action", json={"key": key, "action": "snooze", "expected_revision": 1,
                                                 "minutes": 30}).json()
    assert answer["changed"] and answer["item"]["delivery"]["status"] == "snoozed" and answer["unread"] == 1
    assert changed.await_count == 1
    stale = api.post("/api/inbox/action", json={"key": key, "action": "dismiss", "expected_revision": 1})
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "revision_conflict"
    repeat = api.post("/api/inbox/action", json={"key": key, "action": "snooze", "expected_revision": 1,
                                                 "minutes": 30}).json()
    assert repeat["changed"] is False and changed.await_count == 1
    vision = api.post("/api/inbox/action", json={"key": inbox_key("vision", "default", "c" * 32),
                                                 "action": "read", "expected_revision": 4})
    assert vision.status_code == 409 and vision.json()["detail"]["code"] == "vision_actions"
    other = api.post("/api/inbox/action", json={"key": inbox_key("automation", "coder", occurrence.id),
                                                "action": "read", "expected_revision": 2})
    assert other.status_code == 404  # another profile's key never reaches this store
    assert api.post("/api/inbox/action", json={"key": key, "action": "read"}).status_code == 400
    assert env.store.delivery(result.id).status == "snoozed"


def test_a_running_task_cannot_be_acted_on_yet(env):
    occurrence = sent(env, "k1")
    api = client(service(env))
    response = api.post("/api/inbox/action", json={"key": inbox_key("automation", "default", occurrence.id),
                                                   "action": "read", "expected_revision": 0})
    assert response.status_code == 409 and response.json()["detail"]["code"] == "no_result"
    body = service(env).snapshot()
    assert body["items"][0]["outcome"] == "running" and body["unread"] == 0  # work in progress is not unread


def test_a_store_that_fails_while_saving_answers_503_and_changes_nothing(env, monkeypatch):
    occurrence, result, _ = finished(env, "k1")

    def broken(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(env.store, "act_on_delivery", broken)
    response = client(service(env)).post("/api/inbox/action", json={
        "key": inbox_key("automation", "default", occurrence.id), "action": "read", "expected_revision": 1})
    assert response.status_code == 503 and response.json()["detail"]["code"] == "store_unavailable"
    assert env.store.delivery(result.id).revision == 1


def test_preferences_are_revisioned(env):
    api = client(service(env))
    body = api.post("/api/inbox/preferences", json={"expected_revision": 0, "quiet_minutes": 60}).json()
    assert body["preferences"]["quiet_until"] == NOW + 3600 and body["preferences"]["revision"] == 1
    conflict = api.post("/api/inbox/preferences", json={"expected_revision": 0, "return_notice": False})
    assert conflict.status_code == 409
    assert api.post("/api/inbox/preferences", json={"expected_revision": 1, "quiet_minutes": 5}).status_code == 400


# ── Return notice ────────────────────────────────────────────────────────────


def presenter(env):
    return InboxPresenter(store=lambda: env.store, clock=lambda: env.clock["now"])


def test_one_claimant_at_a_time_and_only_the_receipt_presents(env):
    _, result, _ = finished(env, "k1")
    first = presenter(env).offer("face-a")
    assert [row["title"] for row in first["results"]] == ["Investigar al irme"]
    assert first["results"][0]["excerpt"] == "Resultado sintético."
    assert presenter(env).offer("companion-b") is None  # the lease is held: never two notices
    _, newer, _ = finished(env, "k2")
    assert presenter(env).offer("companion-b") is None  # not even for a result that came meanwhile
    assert env.store.delivery(result.id).status == "pending"  # claiming is not presenting
    presenter(env).sent(first)
    assert presenter(env).receipt({**first, "shown": True}, surface_id="face-a")
    delivery = env.store.delivery(result.id)
    assert delivery.status == "presented" and delivery.read_at is None  # presented, never read
    assert delivery.visual_receipt.surface_id == "face-a"
    revision = delivery.revision
    assert presenter(env).receipt({**first, "shown": True}, surface_id="face-a")  # duplicate: no change
    assert env.store.delivery(result.id).revision == revision
    later = presenter(env).offer("companion-b")  # only what was not shown yet
    assert [row["key"] for row in later["results"]] == [
        inbox_key("automation", "default", newer.occurrence_id)]


@pytest.mark.parametrize("change", ["token", "generation", "surface"])
def test_a_receipt_for_another_attempt_changes_nothing(env, change):
    _, result, _ = finished(env, "k1")
    notice = presenter(env).offer("face-a")
    forged = {**notice, "shown": True}
    surface = "face-a"
    if change == "token":
        forged["token"] = new_id()
    elif change == "generation":
        forged["generation"] += 1
    else:
        surface = "face-b"
    assert presenter(env).receipt(forged, surface_id=surface) is False
    with pytest.raises(PresentationRefused):
        env.store.presentation_receipt(notice["id"], token=forged["token"], generation=forged["generation"],
                                       surface_id=surface, shown=True)
    assert env.store.delivery(result.id).status == "pending"


def test_a_notice_that_may_have_been_shown_is_never_presented_again(env):
    occurrence, result, _ = finished(env, "k1")
    notice = presenter(env).offer("face-a")
    presenter(env).sent(notice)  # sent, then the surface vanished: no receipt
    env.clock["now"] = NOW + 31
    assert presenter(env).offer("face-b") is None  # the lease ran out: ambiguous, not resent elsewhere
    assert env.store.presentation(notice["id"]).state == "ambiguous"
    item = service(env).snapshot()["items"][0]
    assert item["delivery"]["status"] == "pending" and item["delivery"]["uncertain"] is True
    assert presenter(env).receipt({**notice, "shown": True}, surface_id="face-a")  # a late receipt still counts
    assert env.store.delivery(result.id).status == "presented"
    assert service(env).snapshot()["items"][0]["delivery"]["uncertain"] is False
    assert occurrence.id


def test_a_send_that_certainly_failed_or_a_deferral_releases_the_items(env):
    _, result, _ = finished(env, "k1")
    notice = presenter(env).offer("face-a")
    presenter(env).failed(notice, certain=True)
    again = presenter(env).offer("face-a")
    assert again is not None and again["generation"] == notice["generation"] + 1
    assert presenter(env).receipt({**again, "shown": False}, surface_id="face-a")  # the surface deferred it
    assert env.store.presentation(again["id"]).state == "released"
    assert env.store.delivery(result.id).status == "pending"
    assert presenter(env).offer("face-a") is not None


def test_ambiguous_sends_are_not_released(env):
    finished(env, "k1")
    notice = presenter(env).offer("face-a")
    presenter(env).failed(notice, certain=False)
    assert env.store.presentation(notice["id"]).state == "ambiguous"
    assert presenter(env).offer("face-a") is None


def test_running_work_is_mentioned_once_and_its_result_comes_later(env):
    occurrence = sent(env, "k1")
    notice = presenter(env).offer("face-a")
    assert notice["results"] == [] and notice["running"] == [
        {"key": inbox_key("automation", "default", occurrence.id), "title": "Investigar al irme", "state": "running"}]
    presenter(env).receipt({**notice, "shown": True}, surface_id="face-a")
    assert presenter(env).offer("face-a") is None  # the same running task is not announced again
    env.store.finish(occurrence.id, status="completed", text="Listo al final.")
    later = presenter(env).offer("face-a")
    assert later["running"] == [] and later["results"][0]["excerpt"] == "Listo al final."


def test_silence_return_switch_and_quiet_hours_defer_the_notice(env):
    _, result, _ = finished(env, "k1")
    env.store.set_inbox_preferences(expected_revision=0, quiet_minutes=60)
    assert presenter(env).offer("face-a") is None
    env.clock["now"] = NOW + 3601 - 1800  # still silenced
    assert presenter(env).offer("face-a") is None
    env.store.set_inbox_preferences(expected_revision=1, quiet_minutes=0, return_notice=False)
    assert presenter(env).offer("face-a") is None
    env.store.set_inbox_preferences(expected_revision=2, return_notice=True)
    always_quiet = env.store.update_rule(env.rule.id, expected_revision=env.rule.revision,
                                         delivery={"expiration_s": 3600, "quiet_hours":
                                                   {"start_minute": 0, "end_minute": 0}})
    assert presenter(env).offer("face-a") is None  # its rule is in quiet hours
    env.store.update_rule(env.rule.id, expected_revision=always_quiet.revision, delivery={"expiration_s": 3600})
    assert presenter(env).offer("face-a")["results"]
    assert env.store.delivery(result.id).status == "pending"  # deferring never marks anything


def test_read_dismissed_and_expired_results_are_not_presented(env):
    _, read, _ = finished(env, "k1")
    _, dismissed, _ = finished(env, "k2")
    env.store.act_on_delivery(read.id, "read", expected_revision=1)
    env.store.act_on_delivery(dismissed.id, "dismiss", expected_revision=1)
    assert presenter(env).offer("face-a") is None
    finished(env, "k3")
    env.clock["now"] = NOW + 3601
    assert presenter(env).offer("face-a") is None


def test_the_users_own_act_before_the_receipt_stands(env):
    _, result, _ = finished(env, "k1")
    notice = presenter(env).offer("face-a")
    env.store.act_on_delivery(result.id, "read", expected_revision=1)
    presenter(env).receipt({**notice, "shown": True}, surface_id="face-a")
    assert env.store.delivery(result.id).status == "read"


def test_a_snooze_brings_the_notice_back_once(env):
    _, result, _ = finished(env, "k1")
    notice = presenter(env).offer("face-a")
    presenter(env).receipt({**notice, "shown": True}, surface_id="face-a")
    presented = env.store.delivery(result.id)
    env.store.act_on_delivery(result.id, "snooze", expected_revision=presented.revision, minutes=10)
    env.clock["now"] = NOW + 5 * 60
    assert presenter(env).offer("face-a") is None
    env.clock["now"] = NOW + 11 * 60
    back = presenter(env).offer("face-a")
    assert back["results"][0]["key"]
    presenter(env).receipt({**back, "shown": True}, surface_id="face-a")
    assert env.store.delivery(result.id).status == "presented"
    assert presenter(env).offer("face-a") is None


def test_a_notice_survives_a_core_restart(env):
    _, result, _ = finished(env, "k1")
    notice = presenter(env).offer("face-a")
    restarted = AutomationStore(env.path, clock=lambda: env.clock["now"])
    assert InboxPresenter(store=lambda: restarted).receipt({**notice, "shown": True}, surface_id="face-a")
    assert restarted.delivery(result.id).status == "presented"


# ── Core: who may receive the notice ─────────────────────────────────────────


class Socket:
    def __init__(self, host="127.0.0.1"):
        self.client = SimpleNamespace(host=host)
        self.send_json = AsyncMock()


def register(registry, role="face", *, visible=True, host="127.0.0.1", presents=True):
    ws = Socket(host)
    registry.register_ready(ws, {"role": role, "visible": visible, "focused": True,
                                 "capabilities": {"renders_avatar": True, "presents_vision": True,
                                                  "presents_inbox": presents}})
    return ws


@pytest.mark.parametrize("case", ["ok", "hidden", "no_capability", "vision_only", "remote", "observer", "busy",
                                  "throttled", "send_fails"])
def test_core_offers_the_notice_only_to_the_surface_the_user_used(env, monkeypatch, case):
    from core import websocket_server as server

    finished(env, "k1")
    registry = SurfaceRegistry()
    ws = register(registry, "observer" if case == "observer" else "face", visible=case != "hidden",
                  host="192.168.1.8" if case == "remote" else "127.0.0.1",
                  presents=case not in ("no_capability", "vision_only"))
    if case == "remote":  # a LAN page cannot claim it even if it says so
        registry.update_state(ws, server._surface_payload_for_peer(
            {"capabilities": {"presents_inbox": True}}, "192.168.1.8"))
        assert registry.get(ws).supports("presents_inbox") is False
    if case == "send_fails":
        ws.send_json = AsyncMock(side_effect=RuntimeError("socket closed mid-send"))
    runtime = SimpleNamespace(store=lambda: env.store, interaction=lambda: None)
    monkeypatch.setattr(server, "_surface_registry", registry)
    monkeypatch.setattr(server, "_automation_runtime", runtime)
    monkeypatch.setattr(server, "_vision_backend_busy", lambda: case == "busy")
    monkeypatch.setattr(server, "_inbox_offer_at", float("inf") if case == "throttled" else float("-inf"))
    hermes = AsyncMock()
    monkeypatch.setattr(server, "_hermes_api_request", hermes)
    asyncio.run(server._offer_inbox_notice(ws))
    hermes.assert_not_called()  # nothing asked of Hermes
    calls = ws.send_json.await_args_list
    if case in ("ok", "send_fails"):
        assert len(calls) == 1 and calls[0].args[0]["type"] == "inbox_presentation"
        notice = calls[0].args[0]["payload"]
        state = env.store.presentation(notice["id"]).state
        assert state == ("sent" if case == "ok" else "ambiguous")
    else:
        assert calls == []
        assert all(row["kind"] != "presentation_claimed" for row in env.store.history())  # nothing claimed


def test_a_paired_lan_client_cannot_read_or_change_the_inbox():
    from unittest.mock import patch

    from core.config import IRISConfig
    from core.websocket_server import app

    cfg = IRISConfig(lan_access=True, websocket_host="0.0.0.0", lan_pairing_token="pair-secret")
    with (
        patch("core.websocket_server.IRISConfig.load", return_value=cfg),
        TestClient(app, base_url="http://192.168.1.10:8765", client=("192.168.1.25", 50000)) as lan,
    ):
        lan.get("/api/status?pair=pair-secret", follow_redirects=False)
        read = lan.get("/api/inbox")
        act = lan.post("/api/inbox/action", json={"key": "automation:default:" + "a" * 32, "action": "read",
                                                   "expected_revision": 1},
                       headers={"Origin": "http://192.168.1.10:8765"})
        prefs = lan.post("/api/inbox/preferences", json={"expected_revision": 0, "quiet_minutes": 60},
                         headers={"Origin": "http://192.168.1.10:8765"})
    assert read.status_code == act.status_code == prefs.status_code == 403


def test_a_page_load_or_a_chat_message_never_offers_the_notice(monkeypatch):
    from core import websocket_server as server

    offered = []
    monkeypatch.setattr(server, "_automation_runtime", SimpleNamespace(interaction=lambda: None))
    monkeypatch.setattr(server, "_spawn_inbox_task", lambda coroutine: (offered.append(1), coroutine.close()))
    server._note_iris_interaction(Socket())  # a chat message
    assert offered == []
    server._note_iris_interaction(Socket(), offer_notice=True)  # a pointer interaction
    assert offered == [1]
    server._note_iris_interaction(Socket("192.168.1.8"), offer_notice=True)  # a LAN surface
    assert offered == [1]
