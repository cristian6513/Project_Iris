"""F11 durable preferences/deliveries and profile boundary regressions."""

import os
import subprocess
from dataclasses import asdict

import pytest

from core.calendar_reminders import CalendarReminderEngine
from core.calendar_store import (
    CalendarNotice, CalendarPreferences, CalendarRevisionConflict, CalendarStore,
    CalendarStorePathError, CalendarStoreVersionError, UnknownCalendarNotice,
)
from core.profile_paths import ProfilePathError
from tests.test_calendar_reminders import CALENDAR, START, event, snapshot


@pytest.fixture
def state(tmp_path):
    home = tmp_path / "profile"
    home.mkdir()
    clock = [START]
    store = CalendarStore.for_profile(home, clock=lambda: clock[0])
    store.set_preferences(expected_revision=0, reminders_enabled=True)
    notice, = CalendarReminderEngine(store, clock=lambda: clock[0]).tick(snapshot(event()))
    return store, clock, notice


def test_preferences_persist_and_keep_explicit_iana_zone(tmp_path):
    home = tmp_path / "profile"
    home.mkdir()
    store = CalendarStore.for_profile(home)
    prefs = store.set_preferences(expected_revision=0, time_zone="America/Bogota",
                                   hidden_calendars=[CALENDAR, CALENDAR], reminders_enabled=True)
    reopened = CalendarStore.for_profile(home)
    assert reopened.preferences() == prefs
    assert prefs.public() == {"revision": 1, "time_zone": "America/Bogota",
                              "hidden_calendars": [CALENDAR], "reminders_enabled": True}
    assert store.path == home / "apps" / "calendar" / "calendar.sqlite3"
    assert not (home / "automations").exists() and not (home / "connections").exists()


@pytest.mark.parametrize("changes", [{"time_zone": "not-a-zone"}, {"time_zone": "../UTC"},
                                    {"hidden_calendars": ["../../outside"]},
                                    {"hidden_calendars": "abc"}, {"reminders_enabled": "true"},
                                    {"reminders_enabled": 1}])
def test_invalid_preferences_are_atomic(state, changes):
    store, _, notice = state
    before = store.preferences(), store.notice(notice.id)
    with pytest.raises(ValueError):
        store.set_preferences(expected_revision=1, **changes)
    assert (store.preferences(), store.notice(notice.id)) == before


def test_stale_preference_revision_cannot_overwrite_newer_permission(state):
    store, _, _ = state
    store.set_preferences(expected_revision=1, reminders_enabled=False)
    with pytest.raises(CalendarRevisionConflict):
        store.set_preferences(expected_revision=1, reminders_enabled=True)
    assert not store.preferences().reminders_enabled


def test_preferences_parser_rejects_unknown_and_non_integral_revision():
    for changes in ({"extra": True}, {"revision": True}, {"revision": -1}, {"revision": 1.0}):
        with pytest.raises(ValueError):
            CalendarPreferences.parse(changes)


@pytest.mark.parametrize("key", ["b" * 32, "event_" + "b" * 32, "cal_" + "B" * 32,
                                "cal_" + "b" * 64, "cal_" + "b" * 31])
def test_hidden_calendar_ids_follow_real_projection_contract(state, key):
    store, _, notice = state
    with pytest.raises(ValueError):
        store.set_preferences(expected_revision=1, hidden_calendars=[key])
    assert store.preferences().revision == 1 and store.notice(notice.id) == notice


@pytest.mark.parametrize("field,value", [("id", "a" * 64), ("id", "event_" + "a" * 32),
                                          ("account_id", "a" * 32), ("calendar_key", "b" * 32),
                                          ("event_id", "evt_" + "c" * 32)])
def test_durable_notice_rejects_ids_from_the_wrong_field(state, field, value):
    _, _, notice = state
    with pytest.raises(ValueError):
        CalendarNotice.parse({**asdict(notice), field: value})


def test_presented_is_not_read_and_idempotent(state):
    store, _, notice = state
    shown, changed = store.presented(notice.id, expected_revision=1)
    assert changed and shown.status == "presented" and shown.read_at is None
    same, changed = store.presented(notice.id, expected_revision=1)
    assert not changed and same == shown


def test_read_unread_dismiss_restore_keep_facts_separate(state):
    store, _, notice = state
    read, changed = store.act(notice.id, "read", expected_revision=notice.revision)
    assert changed and read.read_at == START and read.presented_at is None
    repeated, changed = store.act(notice.id, "read", expected_revision=1)
    assert not changed and repeated == read
    dismissed, _ = store.act(notice.id, "dismiss", expected_revision=read.revision)
    restored, _ = store.act(notice.id, "restore", expected_revision=dismissed.revision)
    assert restored.status == "read" and restored.read_at == START
    unread, _ = store.act(notice.id, "unread", expected_revision=restored.revision)
    assert unread.status == "pending" and unread.read_at is None


def test_conflicting_action_refuses_stale_revision_without_writing(state):
    store, _, notice = state
    read, _ = store.act(notice.id, "read", expected_revision=1)
    with pytest.raises(CalendarRevisionConflict):
        store.act(notice.id, "dismiss", expected_revision=1)
    assert store.notice(notice.id) == read


def test_snooze_is_explicit_bounded_and_survives_tick_and_restart(state):
    store, clock, notice = state
    snoozed, _ = store.act(notice.id, "snooze", expected_revision=1, minutes=10)
    assert snoozed.delivery_status(START + 599) == "snoozed"
    assert snoozed.delivery_status(START + 600) == "pending"
    assert snoozed.delivery_status(START + 900) == "expired"
    clock[0] += 1
    engine = CalendarReminderEngine(store, clock=lambda: clock[0])
    assert engine.tick(snapshot(event())) == []
    assert store.notice(notice.id).revision == snoozed.revision
    reopened = CalendarStore.for_profile(store.root, clock=lambda: clock[0])
    assert reopened.notice(notice.id) == snoozed


def test_snooze_cannot_revive_past_event(state):
    store, _, notice = state
    with pytest.raises(ValueError):
        store.act(notice.id, "snooze", expected_revision=1, minutes=60)
    assert store.notice(notice.id) == notice


def test_expired_notices_are_archival_never_pending_presentation(state):
    store, clock, notice = state
    clock[0] = START + 300
    assert store.notice(notice.id).delivery_status(clock[0]) == "expired"
    with pytest.raises(ValueError):
        store.presented(notice.id, expected_revision=1)
    assert store.notice(notice.id).presented_at is None


def test_withdrawn_event_cannot_be_restored_or_presented(state):
    store, _, notice = state
    store.set_preferences(expected_revision=1, reminders_enabled=False)
    for action in ("restore", "read", "presented"):
        with pytest.raises(UnknownCalendarNotice):
            store.act(notice.id, action, expected_revision=2)


def test_pending_candidates_recheck_permission_under_store_lock(state):
    store, _, notice = state
    store.set_preferences(expected_revision=1, reminders_enabled=False)
    with store._transaction() as conn:
        conn.execute("DELETE FROM notices")  # a candidate computed before opt-out, not already seen
    assert store.reconcile(candidates=[notice], event_starts={}, connected_account_ids=None,
                           calendar_keys=None, complete=True, coverage=None) == []
    assert store.inbox_rows(include_inactive=True) == []


def test_action_revision_rejects_boolean(state):
    store, _, notice = state
    with pytest.raises(ValueError):
        store.act(notice.id, "read", expected_revision=True)
    assert store.notice(notice.id) == notice


def test_newer_schema_is_refused_without_changing_bytes(state):
    store, _, _ = state
    with store._transaction() as conn:
        conn.execute("PRAGMA user_version=99")
    before = store.path.read_bytes()
    with pytest.raises(CalendarStoreVersionError):
        CalendarStore.for_profile(store.root)
    assert store.path.read_bytes() == before


def test_changed_store_identity_is_refused(state):
    store, _, _ = state
    with store._transaction() as conn:
        conn.execute("UPDATE meta SET value=? WHERE key='store_id'", ("different-store",))
    with pytest.raises(CalendarStorePathError):
        store.preferences()


def _link_directory(link, target):
    if os.name == "nt":
        result = subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(link), str(target)],
                                capture_output=True, text=True)
        if result.returncode:
            pytest.skip("No se pudo crear junction de prueba.")
    else:
        link.symlink_to(target, target_is_directory=True)


@pytest.mark.parametrize("level", ["apps", "calendar"])
def test_linked_store_folder_never_writes_outside_profile(tmp_path, level):
    home, outside = tmp_path / "profile", tmp_path / "outside"
    home.mkdir()
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_bytes(b"unchanged")
    link = home / "apps" if level == "apps" else home / "apps" / "calendar"
    link.parent.mkdir(parents=True, exist_ok=True)
    _link_directory(link, outside)
    before = {p.name: p.read_bytes() for p in outside.iterdir()}
    with pytest.raises((CalendarStorePathError, ProfilePathError)):
        CalendarStore.for_profile(home)
    assert {p.name: p.read_bytes() for p in outside.iterdir()} == before


def test_linked_database_file_never_writes_outside_profile(tmp_path):
    home = tmp_path / "profile"
    home.mkdir()
    store = CalendarStore.for_profile(home)
    outside = tmp_path / "outside.sqlite3"
    outside.write_bytes(store.path.read_bytes())
    store.path.unlink()
    os.link(outside, store.path)
    before = outside.read_bytes()
    with pytest.raises(CalendarStorePathError):
        CalendarStore.for_profile(home)
    assert outside.read_bytes() == before


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_linked_sqlite_sidecar_is_refused_before_touching_target(state, tmp_path, suffix):
    store, _, _ = state
    outside = tmp_path / ("outside" + suffix)
    outside.write_bytes(b"outside-sidecar-must-not-change")
    os.link(outside, store.path.with_name(store.path.name + suffix))
    before = outside.read_bytes()
    with pytest.raises(CalendarStorePathError):
        store.preferences()
    assert outside.read_bytes() == before
