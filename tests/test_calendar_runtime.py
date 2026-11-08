"""F11 runtime wiring with a local synthetic Google cache and disposable profiles."""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.calendar_runtime import CalendarRuntime, create_calendar_preferences_router
from core.calendar_store import CalendarRevisionConflict, CalendarStoreError, profile_store_path
from test_calendar_models import timed
from test_calendar_service import ACCOUNT, Cache

NOW = datetime(2026, 10, 16, 15, tzinfo=timezone.utc).timestamp()


@pytest.fixture
def state(tmp_path):
    home = tmp_path / "profile"
    home.mkdir()
    clock = [NOW]
    monotonic = [100.0]
    changed = []
    cache = Cache()
    cache.value["calendars"][0]["events"] = [timed(
        start="2026-10-16T11:00:00-05:00", end="2026-10-16T12:00:00-05:00",
        video="https://meet.google.com/qa-test")]
    runtime = CalendarRuntime(home, google=lambda: cache, clock=lambda: clock[0],
                              monotonic=lambda: monotonic[0], changed=lambda: changed.append(True))
    return runtime, cache, home, clock, monotonic, changed


def test_reading_default_preferences_or_inbox_does_not_create_a_store(state):
    runtime, cache, home, *_ = state
    assert runtime.preferences() == {"revision": 0, "time_zone": "UTC", "hidden_calendars": [],
                                     "reminders_enabled": False}
    assert runtime.inbox_rows() == [] and cache.reads == 0 and cache.syncs == []
    assert not (home / "apps").exists() and not profile_store_path(home).exists()


def test_preferences_opt_in_revision_conflicts_and_no_network(state):
    runtime, cache, _, _, _, changed = state
    prefs = runtime.set_preferences(revision=0, time_zone="America/Bogota", reminders_enabled=True)
    assert prefs["revision"] == 1 and prefs["reminders_enabled"] and len(changed) == 1
    with pytest.raises(CalendarRevisionConflict):
        runtime.set_preferences(revision=0, reminders_enabled=False)
    assert runtime.preferences() == prefs and len(changed) == 1
    assert cache.reads == 0 and cache.syncs == []


@pytest.mark.parametrize("changes", [{"time_zone": "Missing/Zone"}, {"reminders_enabled": "false"},
                                     {"hidden_calendars": ["remote-calendar-id"]}])
def test_invalid_preferences_are_rejected_before_creating_a_folder(state, changes):
    runtime, _, home, *_ = state
    with pytest.raises(ValueError):
        runtime.set_preferences(revision=0, **changes)
    assert not (home / "apps").exists()


def test_boolean_revision_is_rejected_before_creating_a_folder(state):
    runtime, _, home, *_ = state
    with pytest.raises((ValueError, CalendarRevisionConflict)):
        runtime.set_preferences(revision=False, time_zone="UTC")
    assert not (home / "apps").exists()


def test_snapshot_projects_actual_ids_and_saved_preferences_without_sync(state):
    runtime, cache, *_ = state
    prefs = runtime.set_preferences(revision=0, time_zone="America/Bogota")
    result = asyncio.run(runtime.snapshot("2026-10-16", "2026-10-17", "America/Bogota"))
    assert result["preferences"] == prefs and result["complete"]
    assert result["connected_account_ids"] == [ACCOUNT]
    assert result["events"][0]["id"].startswith("event_")
    assert result["events"][0]["calendar_key"].startswith("cal_")
    assert result["server_now"] == "2026-10-16T15:00:00Z"
    assert cache.syncs == []


def test_tick_off_has_no_cache_read_and_does_not_create_a_store(state):
    runtime, cache, home, *_ = state
    assert asyncio.run(runtime.tick(force=True)) == []
    assert cache.reads == 0 and cache.syncs == [] and not (home / "apps").exists()
    runtime.set_preferences(revision=0, reminders_enabled=False)
    assert asyncio.run(runtime.tick(force=True)) == []
    assert cache.reads == 0


def test_tick_enabled_emits_real_phase_once_and_respects_cadence(state):
    runtime, cache, _, clock, monotonic, changed = state
    runtime.set_preferences(revision=0, time_zone="America/Bogota", reminders_enabled=True)

    async def run():
        before, = await runtime.tick(force=True)
        assert before.phase == "before_60" and before.account_id == ACCOUNT
        projection = await runtime.snapshot("2026-10-16", "2026-10-17", "America/Bogota")
        assert before.event_id == projection["events"][0]["id"]
        assert runtime.public_notice(before.id)["join_url"] == "https://meet.google.com/qa-test"
        first = runtime.claim_notice(before.id, before.revision, surface_id="face-test")
        assert first is not None and runtime.claim_notice(before.id, before.revision, surface_id="face-test") is None
        reads = cache.reads
        assert await runtime.tick() == [] and cache.reads == reads
        monotonic[0] += 15
        assert await runtime.tick() == []
        assert len(runtime.inbox_rows()) == 1
        clock[0] += 3600
        monotonic[0] += 15
        start, = await runtime.tick()
        assert start.phase == "start" and start.event_id == before.event_id and start.id != before.id
        assert len(runtime.inbox_rows()) == 2 and len(changed) >= 3

    asyncio.run(run())
    assert cache.syncs == []


def test_two_surfaces_can_claim_only_one_visual_presentation(state, monkeypatch):
    runtime, *_ = state
    runtime.set_preferences(revision=0, reminders_enabled=True)
    before, = asyncio.run(runtime.tick(force=True))
    store = runtime.store()
    original = store.notice
    barrier = threading.Barrier(2)

    def read_together(source_id):
        row = original(source_id)
        barrier.wait(timeout=5)
        return row

    monkeypatch.setattr(store, "notice", read_together)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(runtime.claim_notice, before.id, before.revision, surface_id="face-test") for _ in range(2)]
        claimed = [future.result(timeout=10) for future in futures]
    assert sum(result is not None for result in claimed) == 1


def test_closed_runtime_cannot_create_new_work(state):
    runtime, cache, home, *_ = state
    asyncio.run(runtime.close())
    assert asyncio.run(runtime.tick(force=True)) == []
    with pytest.raises(CalendarStoreError):
        runtime.preferences()
    assert cache.reads == 0 and not (home / "apps").exists()


def test_cancelling_a_tick_does_not_leave_an_uncollected_writer_after_close(state, monkeypatch):
    runtime, _, _, *_ = state
    runtime.set_preferences(revision=0, reminders_enabled=True)
    entered, release = threading.Event(), threading.Event()
    from core.calendar_reminders import CalendarReminderEngine
    original = CalendarReminderEngine.tick

    def gated(engine, snapshot):
        entered.set()
        assert release.wait(5), "test writer gate was not released"
        return original(engine, snapshot)

    monkeypatch.setattr(CalendarReminderEngine, "tick", gated)

    async def run():
        evaluation = asyncio.create_task(runtime.tick(force=True))
        closing = None
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            evaluation.cancel()
            await asyncio.sleep(0)
            closing = asyncio.create_task(runtime.close())
            await asyncio.sleep(0.03)
            assert not closing.done(), "close returned while an owned SQLite writer was still running"
            evaluation.cancel()
            await asyncio.sleep(0.01)
            assert not closing.done(), "a repeated cancellation detached the owned writer"
        finally:
            release.set()
            await asyncio.gather(evaluation, return_exceptions=True)
            if closing is not None:
                await closing

    asyncio.run(run())


def browser(runtime, peer="127.0.0.1"):
    app = FastAPI()
    app.include_router(create_calendar_preferences_router(lambda: runtime))
    return TestClient(app, client=(peer, 44000), base_url="http://127.0.0.1")


def test_preferences_api_is_local_private_and_never_syncs(state):
    runtime, cache, home, *_ = state
    client = browser(runtime)
    answer = client.get("/api/calendar/preferences")
    assert answer.status_code == 200 and "no-store" in answer.headers["cache-control"]
    assert not (home / "apps").exists()
    saved = client.patch("/api/calendar/preferences", json={"revision": 0, "reminders_enabled": True})
    assert saved.status_code == 200 and saved.json()["revision"] == 1
    stale = client.patch("/api/calendar/preferences", json={"revision": 0, "reminders_enabled": False})
    assert stale.status_code == 409 and "no-store" in stale.headers["cache-control"]
    assert cache.reads == 0 and cache.syncs == []


@pytest.mark.parametrize("body", [{"revision": False}, {"revision": 0, "reminders_enabled": "false"},
                                  {"revision": 0, "enabled": True}])
def test_preferences_api_rejects_coercion_and_unknown_fields_without_side_effects(state, body):
    runtime, cache, home, *_ = state
    answer = browser(runtime).patch("/api/calendar/preferences", json=body)
    assert answer.status_code == 422 and cache.reads == 0 and cache.syncs == []
    assert not (home / "apps").exists()


def test_lan_peer_cannot_read_or_write_preferences(state):
    runtime, cache, home, *_ = state
    client = browser(runtime, "192.168.1.25")
    assert client.get("/api/calendar/preferences").status_code == 403
    assert client.patch("/api/calendar/preferences", json={"revision": 0, "reminders_enabled": True}).status_code == 403
    assert cache.reads == 0 and cache.syncs == [] and not (home / "apps").exists()
