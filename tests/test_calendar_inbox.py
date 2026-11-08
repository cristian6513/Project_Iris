"""Calendar's real profile store projected into Bandeja, without Google or AI."""

from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import AsyncMock
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from core.calendar_reminders import CalendarReminderEngine
from core.calendar_runtime import CalendarRuntime
from core.calendar_store import CalendarStoreError
from core.inbox_api import InboxService, inbox_router
from core.inbox_contracts import InboxItem, from_calendar, inbox_key


NOW = datetime(2026, 10, 16, 2, 0, tzinfo=timezone.utc).timestamp()
ACCOUNT, CALENDAR, EVENT = "gacc_" + "a" * 32, "cal_" + "b" * 32, "event_" + "c" * 32


def iso(at):
    return datetime.fromtimestamp(at, timezone.utc).isoformat()


@pytest.fixture
def env(tmp_path):
    home = tmp_path / "profile"
    home.mkdir()
    clock = [NOW]

    def forbidden_google():
        pytest.fail("Bandeja must never request the Google owner or synchronize data")

    runtime = CalendarRuntime(home, google=forbidden_google, clock=lambda: clock[0])
    runtime.set_preferences(revision=0, time_zone="America/Bogota", reminders_enabled=True)
    store = runtime.store()
    notice, = CalendarReminderEngine(store, clock=lambda: clock[0]).tick({
        "available": True, "complete": True, "connected_account_ids": [ACCOUNT], "calendar_keys": [CALENDAR],
        "coverage": {"start": iso(NOW - 86400), "end": iso(NOW + 86400)},
        "events": [{"id": EVENT, "account_id": ACCOUNT, "calendar_key": CALENDAR,
                    "title": "Reunión sintética", "start": iso(NOW), "end": iso(NOW + 7200),
                    "all_day": False, "join_url": "https://meet.google.com/abc-defg-hij", "event_url": ""}],
    })
    service = InboxService(store=lambda: None, store_problem=lambda: "", calendar=lambda: runtime,
                           clock=lambda: clock[0])
    return runtime, store, service, notice, clock


def api(service):
    app = FastAPI()
    app.include_router(inbox_router(lambda: service, changed=AsyncMock()))
    return TestClient(app)


def test_calendar_snapshot_projects_actual_delivery_and_preferred_local_day_without_mutation(env):
    runtime, store, service, notice, _ = env
    before = store.notice(notice.id)
    body = service.snapshot()
    [item] = body["items"]
    assert body["sources"]["calendar"] == {"status": "ok", "message": ""}
    assert body["unread"] == 1 and item["outcome"] == "notice"
    assert item["key"] == inbox_key("calendar", "default", notice.id)
    assert item["source_id"] == notice.id and item["calendar_event_id"] == EVENT
    assert item["calendar_date"] == "2026-10-15"  # UTC day is the 16th; the chosen IANA zone owns the day.
    assert item["join_url"] == "https://meet.google.com/abc-defg-hij"
    assert item["delivery"]["revision"] == notice.revision
    assert item["delivery"]["presented_at"] is None and item["delivery"]["read_at"] is None
    assert item["actions"] == ["read", "snooze", "dismiss"]
    assert store.notice(notice.id) == before and runtime.preferences()["revision"] == 1
    assert not (store.root / "automations").exists() and not (store.root / "connections").exists()


def test_calendar_actions_reach_the_source_with_revision_and_idempotence(env):
    _, store, service, notice, _ = env
    key = inbox_key("calendar", "default", notice.id)
    client = api(service)

    def action(name, revision, **extra):
        return client.post("/api/inbox/action", json={"key": key, "action": name,
                                                      "expected_revision": revision, **extra})

    read = action("read", 1)
    assert read.status_code == 200 and read.json()["changed"] and read.json()["unread"] == 0
    assert store.notice(notice.id).status == "read" and store.notice(notice.id).presented_at is None
    repeat = action("read", 1)
    assert repeat.status_code == 200 and repeat.json()["changed"] is False
    stale = action("dismiss", 1)
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "revision_conflict"
    unread = action("unread", 2)
    assert unread.status_code == 200 and unread.json()["item"]["delivery"]["status"] == "pending"
    snoozed = action("snooze", 3, minutes=10)
    assert snoozed.status_code == 200 and snoozed.json()["item"]["delivery"]["status"] == "snoozed"
    assert store.notice(notice.id).snoozed_until == NOW + 600
    restored = action("restore", 4)
    assert restored.status_code == 200 and restored.json()["item"]["delivery"]["status"] == "pending"
    dismissed = action("dismiss", 5)
    assert dismissed.status_code == 200 and dismissed.json()["item"]["delivery"]["status"] == "dismissed"
    restored = action("restore", 6)
    assert restored.status_code == 200 and restored.json()["item"]["delivery"]["status"] == "pending"
    assert store.notice(notice.id).revision == 7


@pytest.mark.parametrize("key", ["calendar:coder:" + "a" * 32, "calendar:default:not-hex",
                                "calendar:default:" + "g" * 32, "gmail:default:" + "a" * 32])
def test_invalid_or_reserved_composite_keys_never_act_on_the_calendar(env, monkeypatch, key):
    runtime, _, service, _, _ = env
    monkeypatch.setattr(runtime, "act", lambda *args, **kwargs: pytest.fail("unexpected mutation"))
    response = api(service).post("/api/inbox/action", json={"key": key, "action": "read", "expected_revision": 1})
    assert response.status_code == 404


def test_missing_calendar_notice_is_404(env):
    _, _, service, _, _ = env
    response = api(service).post("/api/inbox/action", json={
        "key": "calendar:default:" + "d" * 32, "action": "read", "expected_revision": 1})
    assert response.status_code == 404 and response.json()["detail"]["code"] == "unknown_item"


def test_unavailable_source_is_reported_and_write_is_503():
    service = InboxService(store=lambda: None, store_problem=lambda: "", calendar=lambda: None, clock=lambda: NOW)
    body = service.snapshot()
    assert body["sources"]["calendar"]["status"] == "unavailable"
    assert "leer" in body["sources"]["calendar"]["message"]
    response = api(service).post("/api/inbox/action", json={
        "key": "calendar:default:" + "d" * 32, "action": "read", "expected_revision": 1})
    assert response.status_code == 503


def test_calendar_read_failure_is_not_a_successful_empty_source(env, monkeypatch):
    runtime, _, service, _, _ = env

    def broken():
        raise CalendarStoreError("temporary store failure")

    monkeypatch.setattr(runtime, "inbox_rows", broken)
    assert service.snapshot()["sources"]["calendar"]["status"] == "unavailable"


def test_failed_calendar_write_preserves_notice(env, monkeypatch):
    runtime, store, service, notice, _ = env

    def broken(*args, **kwargs):
        raise CalendarStoreError("disk failure")

    monkeypatch.setattr(runtime, "act", broken)
    response = api(service).post("/api/inbox/action", json={
        "key": inbox_key("calendar", "default", notice.id), "action": "read", "expected_revision": 1})
    assert response.status_code == 503 and store.notice(notice.id) == notice


def test_calendar_source_projection_is_atomic_and_preserves_other_origins(env, monkeypatch):
    runtime, _, _, notice, _ = env
    vision = {"revision": 1, "items": [{"schema": "iris.notification/v1", "source": "vision", "id": notice.id,
                                        "title": "Aviso", "body": "Vision", "status": "active", "created_at": NOW}]}
    service = InboxService(store=lambda: None, store_problem=lambda: "", vision=lambda: vision,
                           calendar=lambda: runtime, clock=lambda: NOW)
    monkeypatch.setattr(runtime, "inbox_rows", lambda: [notice, replace(notice, id="bad")])
    body = service.snapshot()
    assert body["sources"]["calendar"]["status"] == "unavailable"
    assert [row["origin"] for row in body["items"]] == ["vision"]


def test_same_source_id_in_vision_and_calendar_remains_two_items(env):
    runtime, _, _, notice, _ = env
    vision = {"revision": 1, "items": [{"schema": "iris.notification/v1", "source": "vision", "id": notice.id,
                                        "title": "Aviso", "body": "Vision", "status": "active", "created_at": NOW}]}
    service = InboxService(store=lambda: None, store_problem=lambda: "", vision=lambda: vision,
                           calendar=lambda: runtime, clock=lambda: NOW)
    body = service.snapshot()
    assert len({item["key"] for item in body["items"]}) == 2 and body["unread"] == 2


def test_snooze_and_expiration_are_derived_without_reading_or_reexecuting(env):
    _, store, service, notice, clock = env
    key = inbox_key("calendar", "default", notice.id)
    service.act(key, "snooze", expected_revision=1, minutes=10)
    assert service.snapshot()["items"][0]["delivery"]["status"] == "snoozed"
    clock[0] += 600
    assert service.snapshot()["items"][0]["delivery"]["status"] == "pending"
    clock[0] += 300
    assert service.snapshot()["items"][0]["delivery"]["status"] == "expired"
    assert store.notice(notice.id).status == "snoozed" and store.notice(notice.id).read_at is None


def test_links_and_destinations_are_only_taken_from_calendar_source_fields(env):
    _, _, _, notice, _ = env
    item = from_calendar(profile="default", notice=replace(notice, join_url="", text="https://meet.google.com/fake"),
                         time_zone="UTC", now=NOW)
    assert item.join_url is None and item.calendar_event_id == EVENT
    unsafe = from_calendar(profile="default", notice=replace(notice, join_url="javascript:alert(1)"), now=NOW)
    assert unsafe.join_url is None


@pytest.mark.parametrize("url", ["http://meet.google.com/room", "javascript:alert(1)", "https://127.0.0.1/",
                                "https://local.internal/", "https://user:pass@meet.google.com/room"])
def test_untrusted_join_url_is_refused_by_the_envelope(env, url):
    _, _, _, notice, _ = env
    payload = from_calendar(profile="default", notice=notice, now=NOW).model_dump(by_alias=True)
    with pytest.raises(ValidationError):
        InboxItem.model_validate({**payload, "join_url": url})


@pytest.mark.parametrize("origin", ["vision", "automation", "gmail"])
def test_calendar_destinations_cannot_be_attached_to_other_origins(env, origin):
    _, _, _, notice, _ = env
    payload = from_calendar(profile="default", notice=notice, now=NOW).model_dump(by_alias=True)
    with pytest.raises(ValidationError):
        InboxItem.model_validate({**payload, "origin": origin, "key": inbox_key(origin, "default", notice.id)})


def test_calendar_notice_cannot_claim_automation_execution_outcome(env):
    _, _, _, notice, _ = env
    payload = from_calendar(profile="default", notice=notice, now=NOW).model_dump(by_alias=True)
    with pytest.raises(ValidationError):
        InboxItem.model_validate({**payload, "outcome": "completed"})


def test_intent_to_present_is_uncertain_until_the_real_surface_receipt(env):
    _, _, _, notice, _ = env
    values = {**vars(notice), "delivery_status": notice.delivery_status, "actions": notice.actions,
              "presentation_attempted_at": NOW}
    pending = from_calendar(profile="default", notice=SimpleNamespace(**values), now=NOW)
    assert pending.delivery.uncertain and pending.delivery.presented_at is None
    assert pending.delivery.status == "pending" and pending.delivery.read_at is None
    values["presented_at"] = NOW
    shown = from_calendar(profile="default", notice=SimpleNamespace(**values), now=NOW)
    assert not shown.delivery.uncertain and shown.delivery.read_at is None
    assert "presentation_attempted_at" not in pending.model_dump()
