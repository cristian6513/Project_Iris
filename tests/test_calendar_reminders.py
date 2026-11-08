"""F11 deterministic reminders: local synthetic snapshots, no accounts or network."""

from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

import pytest

from core.calendar_reminders import CalendarReminderEngine, utc_timestamp
from core.calendar_store import CalendarStore
from core.calendar_models import CalendarWindow
from core.calendar_service import CalendarService
from core.google_contracts import new_account_id


ACCOUNT, CALENDAR, EVENT = "gacc_" + "a" * 32, "cal_" + "b" * 32, "event_" + "c" * 32
START = 1_798_199_100.0


def iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def event(*, start=START, end=None, **changes):
    return {"id": EVENT, "account_id": ACCOUNT, "calendar_key": CALENDAR,
            "start": iso(start), "end": iso(end if end is not None else start + 3600),
            "title": "Reunión sintética", "all_day": False, "time_zone": "America/Bogota",
            "join_url": "https://meet.google.com/abc-defg-hij", "event_url": "", **changes}


def snapshot(*events, **changes):
    return {"available": True, "complete": True, "events": list(events),
            "connected_account_ids": [ACCOUNT], "calendar_keys": [CALENDAR],
            "coverage": {"start": iso(START - 86400), "end": iso(START + 86400)}, **changes}


@pytest.fixture
def state(tmp_path):
    home = tmp_path / "profile"
    home.mkdir()
    clock = [START - 3600]
    store = CalendarStore.for_profile(home, clock=lambda: clock[0])
    store.set_preferences(expected_revision=0, reminders_enabled=True)
    return store, clock, CalendarReminderEngine(store, clock=lambda: clock[0])


def test_exact_phases_survive_restart_and_never_duplicate(state):
    store, clock, engine = state
    clock[0] = START - 3601
    assert engine.tick(snapshot(event())) == []
    clock[0] += 1
    notice, = engine.tick(snapshot(event()))
    assert notice.phase == "before_60" and notice.start == START
    assert engine.tick(snapshot(event())) == []
    reopened = CalendarStore(store.path, root=store.root, clock=lambda: clock[0])
    restarted = CalendarReminderEngine(reopened, clock=lambda: clock[0])
    assert restarted.tick(snapshot(event())) == []
    clock[0] = START
    second, = restarted.tick(snapshot(event()))
    assert second.phase == "start" and second.id != notice.id
    assert len(reopened.inbox_rows()) == 2


def test_default_is_opt_in_and_tick_does_not_enable_it(tmp_path):
    home = tmp_path / "profile"
    home.mkdir()
    store = CalendarStore.for_profile(home, clock=lambda: START)
    assert not store.preferences().reminders_enabled
    assert CalendarReminderEngine(store, clock=lambda: START).tick(snapshot(event())) == []
    assert store.inbox_rows(include_inactive=True) == []


@pytest.mark.parametrize("elapsed,expected", [(0, 1), (299.9, 1), (300, 0), (3600, 0), (7200, 0)])
def test_wake_grace_does_not_replay_expired_start(state, elapsed, expected):
    store, clock, engine = state
    clock[0] = START + elapsed
    assert len(engine.tick(snapshot(event()))) == expected
    assert all(row.phase == "start" for row in store.inbox_rows())


def test_short_event_already_finished_is_not_announced(state):
    _, clock, engine = state
    clock[0] = START + 120
    assert engine.tick(snapshot(event(end=START + 60))) == []


def test_wake_before_start_skips_missed_hour_phase(state):
    _, clock, engine = state
    clock[0] = START - 60
    assert engine.tick(snapshot(event())) == []


@pytest.mark.parametrize("elapsed,expected", [(0, 1), (299.9, 1), (300, 0), (1800, 0)])
def test_hour_phase_has_the_same_five_minute_wake_grace(state, elapsed, expected):
    store, clock, engine = state
    clock[0] = START - 3600 + elapsed
    assert len(engine.tick(snapshot(event()))) == expected
    assert all(row.phase == "before_60" for row in store.inbox_rows())


def test_backwards_clock_cannot_replay_earlier_phase(state):
    store, clock, engine = state
    clock[0] = START
    engine.tick(snapshot(event()))
    clock[0] = START - 3600
    assert engine.tick(snapshot(event())) == []
    assert [row.phase for row in store.inbox_rows()] == ["start"]


def test_reschedule_retires_old_and_uses_new_start_identity(state):
    store, clock, engine = state
    previous, = engine.tick(snapshot(event()))
    moved = event(start=START + 600)
    assert engine.tick(snapshot(moved)) == []
    assert store.notice(previous.id).active is False
    clock[0] = START - 3000
    new, = engine.tick(snapshot(moved))
    assert new.id != previous.id and new.start == START + 600


def test_explicit_reschedule_is_authoritative_even_in_partial_snapshot(state):
    store, clock, engine = state
    previous, = engine.tick(snapshot(event()))
    moved = event(start=START + 600)
    assert engine.tick(snapshot(moved, complete=False)) == []
    assert not store.notice(previous.id).active
    clock[0] = START - 3000
    assert engine.tick(snapshot(moved, complete=False)) == []
    new, = engine.tick(snapshot(moved))
    assert new.id != previous.id


def test_returning_to_an_already_announced_time_does_not_replay_tombstone(state):
    store, _, engine = state
    previous, = engine.tick(snapshot(event()))
    assert engine.tick(snapshot(event(start=START + 600))) == []
    assert engine.tick(snapshot(event())) == []
    assert not store.notice(previous.id).active


def test_title_changes_update_same_notice_without_replaying_read_state(state):
    store, clock, engine = state
    previous, = engine.tick(snapshot(event()))
    store.act(previous.id, "read", expected_revision=previous.revision)
    clock[0] += 1
    assert engine.tick(snapshot(event(title="Título corregido"))) == []
    updated = store.notice(previous.id)
    assert updated.title == "Título corregido" and updated.status == "read"
    revision = updated.revision
    assert engine.tick(snapshot(event(title="Título corregido"))) == []
    assert store.notice(previous.id).revision == revision


def test_cancellation_in_complete_window_retires_and_recreation_does_not_replay(state):
    store, _, engine = state
    previous, = engine.tick(snapshot(event()))
    engine.tick(snapshot())
    assert not store.notice(previous.id).active
    assert engine.tick(snapshot(event())) == []
    assert store.inbox_rows() == []


def test_unrelated_date_window_does_not_retire_existing_event(state):
    store, _, engine = state
    previous, = engine.tick(snapshot(event()))
    engine.tick(snapshot(coverage={"start": iso(START + 86400), "end": iso(START + 172800)}))
    assert store.notice(previous.id).active


@pytest.mark.parametrize("change", [{"complete": False}, {"available": False}, {"coverage": None}])
def test_incomplete_window_cannot_cancel_by_absence(state, change):
    store, _, engine = state
    previous, = engine.tick(snapshot(event()))
    engine.tick(snapshot(**change))
    assert store.notice(previous.id).active


@pytest.mark.parametrize("change", [{"complete": False}, {"available": False}])
def test_unavailable_or_incomplete_snapshot_cannot_issue_new_notices(state, change):
    store, _, engine = state
    assert engine.tick(snapshot(event(), **change)) == []
    assert store.inbox_rows() == []


def test_disconnect_retires_even_when_another_calendar_failed(state):
    store, _, engine = state
    previous, = engine.tick(snapshot(event()))
    engine.tick(snapshot(complete=False, connected_account_ids=[], calendar_keys=[]))
    assert not store.notice(previous.id).active


def test_hidden_calendar_and_permission_withdrawal_are_immediate(state):
    store, _, engine = state
    previous, = engine.tick(snapshot(event()))
    store.set_preferences(expected_revision=1, hidden_calendars=[CALENDAR])
    assert not store.notice(previous.id).active
    store.set_preferences(expected_revision=2, hidden_calendars=[])
    assert engine.tick(snapshot(event())) == []
    another, = engine.tick(snapshot(event(id="event_" + "d" * 32)))
    store.set_preferences(expected_revision=3, reminders_enabled=False)
    assert not store.notice(another.id).active
    assert not store.inbox_rows()


def test_all_day_events_are_visible_input_but_have_no_timed_phases(state):
    store, _, engine = state
    assert engine.tick(snapshot({**event(all_day=True, start_date="2026-10-15", end_date="2026-10-16"),
                                 "start": None, "end": None})) == []
    assert not store.inbox_rows()


def test_conversion_to_all_day_retires_timed_notice_even_if_partial(state):
    store, _, engine = state
    previous, = engine.tick(snapshot(event()))
    engine.tick(snapshot(event(all_day=True), complete=False))
    assert not store.notice(previous.id).active


def test_two_accounts_and_recurrence_instances_have_independent_identities(state):
    store, _, engine = state
    second_account = "gacc_" + "d" * 32
    notices = engine.tick(snapshot(event(), event(account_id=second_account), event(id="event_" + "e" * 32),
                                   connected_account_ids=[ACCOUNT, second_account]))
    assert len(notices) == len({row.id for row in notices}) == 3
    assert len(store.inbox_rows()) == 3


@pytest.mark.parametrize("local", ["2026-03-08T01:30:00-05:00", "2026-03-08T03:30:00-04:00",
                                   "2026-11-01T01:30:00-04:00", "2026-11-01T01:30:00-05:00"])
def test_dst_instants_remain_distinct_and_remind_one_real_hour_before(state, local):
    store, clock, engine = state
    start = datetime.fromisoformat(local).timestamp()
    clock[0] = start - 3600
    notice, = engine.tick(snapshot(event(start=start), coverage={"start": iso(start - 86400),
                                                               "end": iso(start + 86400)}))
    assert notice.due_at == start - 3600 and notice.start == start
    assert datetime.fromtimestamp(notice.start, ZoneInfo("America/New_York")).isoformat() == local


@pytest.mark.parametrize("value", ["2026-11-01T01:30:00", "2026-10-15", "wrong", None])
def test_naive_or_invalid_instants_fail_without_changing_store(state, value):
    store, _, engine = state
    with pytest.raises(ValueError):
        engine.tick(snapshot({**event(), "start": value}))
    assert not store.inbox_rows(include_inactive=True)


def test_invalid_event_after_valid_event_is_atomic(state):
    store, _, engine = state
    with pytest.raises(ValueError):
        engine.tick(snapshot(event(), event(id="bad")))
    assert not store.inbox_rows(include_inactive=True)


def test_untrusted_links_are_never_promoted_to_notice(state):
    store, _, engine = state
    with pytest.raises(ValueError):
        engine.tick(snapshot(event(join_url="javascript:alert(1)")))
    assert not store.inbox_rows()


def test_two_evaluators_persist_only_one_notice(state):
    store, clock, engine = state
    other_store = CalendarStore.for_profile(store.root, clock=lambda: clock[0])
    other = CalendarReminderEngine(other_store, clock=lambda: clock[0])
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda target: target.tick(snapshot(event())), (engine, other)))
    assert sum(map(len, results)) == 1
    assert len(store.inbox_rows()) == 1


def test_partial_snapshot_updates_title_but_does_not_emit_new_notice(state):
    store, _, engine = state
    original, = engine.tick(snapshot(event()))
    assert engine.tick(snapshot(event(title="Título actual"), event(id="event_" + "d" * 32), complete=False)) == []
    assert store.notice(original.id).title == "Título actual"
    assert len(store.inbox_rows()) == 1


def test_unknown_inventory_keeps_notice_when_source_is_unavailable(state):
    store, _, engine = state
    original, = engine.tick(snapshot(event()))
    engine.tick({"available": False, "complete": False})
    assert store.notice(original.id).active


def test_iso_offsets_normalize_to_same_instant():
    assert utc_timestamp("2026-10-15T12:00:00-05:00") == utc_timestamp("2026-10-15T17:00:00Z")


def test_real_cache_projection_feeds_engine_and_hidden_calendar_preferences(state):
    """Exercise the producer contract instead of three matching synthetic hex fixtures."""
    store, clock, engine = state
    account = new_account_id()
    sync = {"state": "idle", "last_success_at": START - 7200, "error": None}
    cache = {"available": True, "warnings": [], "accounts": [{
        "id": account, "email": "synthetic@example.com", "label": "Prueba", "auth_state": "connected",
        "services": {"calendar.read": {"granted": True}}, "sync": {"calendar.read": sync},
    }], "calendars": [{
        "account_id": account, "calendar": {"id": "synthetic@example.com", "summary": "Trabajo", "timeZone": "UTC"},
        "sync": sync, "events": [{"id": "google-event-id", "summary": "Reunión sintética",
                                   "start": {"dateTime": iso(START)},
                                   "end": {"dateTime": iso(START + 3600)}}],
    }]}
    window = CalendarWindow.parse(iso(START - 86400)[:10], iso(START + 86400)[:10], "UTC")
    projected = CalendarService(None, clock=lambda: clock[0])._project(cache, window)
    assert projected["complete"] and projected["available"]
    assert projected["connected_account_ids"] == [account]
    calendar = projected["calendars"][0]["key"]
    assert calendar.startswith("cal_") and projected["events"][0]["id"].startswith("event_")
    notice, = engine.tick(projected)
    assert notice.account_id == account and notice.calendar_key == calendar
    assert notice.event_id == projected["events"][0]["id"]
    assert len(notice.id) == 32 and notice.id.isalnum()
    store.set_preferences(expected_revision=1, hidden_calendars=[calendar])
    assert store.preferences().hidden_calendars == (calendar,)
    assert not store.notice(notice.id).active and engine.tick(projected) == []


@pytest.mark.parametrize("field,value", [
    ("account_id", "a" * 32), ("account_id", "cal_" + "a" * 32),
    ("account_id", "gacc_" + "A" * 32), ("account_id", "gacc_" + "a" * 64),
    ("calendar_key", "b" * 32), ("calendar_key", "gacc_" + "b" * 32),
    ("id", "c" * 32), ("id", "evt_" + "c" * 32), ("id", "event_" + "c" * 31),
])
def test_event_id_fields_match_only_the_exact_producer_contract(state, field, value):
    store, _, engine = state
    with pytest.raises(ValueError):
        engine.tick(snapshot(event(**{field: value})))
    assert not store.inbox_rows(include_inactive=True)


@pytest.mark.parametrize("coverage", [None, {}, {"start": "2026-10-15", "end": "2026-10-17"},
                                       {"start": iso(START + 1), "end": iso(START)}])
def test_unknown_or_invalid_coverage_never_emits_an_uncovered_notice(state, coverage):
    store, _, engine = state
    if coverage is None:
        assert engine.tick(snapshot(event(), coverage=coverage)) == []
    else:
        with pytest.raises(ValueError):
            engine.tick(snapshot(event(), coverage=coverage))
    assert not store.inbox_rows(include_inactive=True)


def test_event_outside_declared_complete_window_is_not_delivered(state):
    store, _, engine = state
    assert engine.tick(snapshot(event(), coverage={"start": iso(START + 7200),
                                                    "end": iso(START + 86400)})) == []
    assert not store.inbox_rows()


def test_utc_coverage_with_local_offsets_withdraws_absent_event(state):
    store, _, engine = state
    notice, = engine.tick(snapshot(event()))
    offset = ZoneInfo("America/Bogota")
    coverage = {"start": datetime.fromtimestamp(START - 1, offset).isoformat(),
                "end": datetime.fromtimestamp(START + 1, offset).isoformat()}
    engine.tick(snapshot(coverage=coverage))
    assert not store.notice(notice.id).active


# Review 2026-10-19: F10 syncs the user's answer and the event type, but F11 reminded declined
# meetings and out-of-office/focus/working-location blocks like any meeting.
@pytest.mark.parametrize("changes", [{"declined": True}, {"event_type": "outOfOffice"},
                                     {"event_type": "focusTime"}, {"event_type": "workingLocation"}])
def test_declined_meetings_and_status_blocks_are_never_reminded(state, changes):
    store, clock, engine = state
    clock[0] = START - 3600
    assert engine.tick(snapshot(event(**changes))) == []
    clock[0] = START
    assert engine.tick(snapshot(event(**changes))) == []
    assert store.inbox_rows(include_inactive=True) == []


def test_declining_after_the_reminder_withdraws_it(state):
    store, clock, engine = state
    clock[0] = START - 3600
    notice, = engine.tick(snapshot(event()))
    assert [row.id for row in store.inbox_rows()] == [notice.id]
    assert engine.tick(snapshot(event(declined=True))) == []
    assert store.inbox_rows() == []
    assert store.notice(notice.id).active is False
    clock[0] = START
    assert engine.tick(snapshot(event(declined=True))) == []


def test_a_meeting_still_attended_is_reminded_as_before(state):
    store, clock, engine = state
    clock[0] = START - 3600
    notice, = engine.tick(snapshot(event(declined=False, event_type="default")))
    assert notice.phase == "before_60"
