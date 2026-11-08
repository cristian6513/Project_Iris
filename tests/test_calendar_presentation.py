"""A durable send intent is neither visual presentation nor evidence of reading."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import asdict
import json
import re
import threading

import pytest

from core.calendar_reminders import CalendarReminderEngine
from core.calendar_store import CalendarNotice, CalendarRevisionConflict, CalendarStore
from tests.test_calendar_reminders import START, event, snapshot


@pytest.fixture
def state(tmp_path):
    home = tmp_path / "profile"
    home.mkdir()
    clock = [START]
    store = CalendarStore.for_profile(home, clock=lambda: clock[0])
    store.set_preferences(expected_revision=0, reminders_enabled=True)
    notice, = CalendarReminderEngine(store, clock=lambda: clock[0]).tick(snapshot(event()))
    return store, clock, notice


def claim(store, notice, surface="face-local"):
    return store.claim_presentation(notice.id, expected_revision=notice.revision, surface_id=surface)


def receipt(store, notice, *, shown=True, token=None, surface=None):
    return store.presentation_receipt(notice.id, token=token or notice.presentation_token,
                                      surface_id=surface or notice.presentation_surface, shown=shown)


def test_claim_records_only_intent_and_hides_private_capability_from_public_rows(state):
    store, clock, original = state
    notice, changed = claim(store, original)
    assert changed and notice.presentation_attempted_at == clock[0]
    assert re.fullmatch(r"[0-9a-f]{32}", notice.presentation_token)
    assert notice.presentation_surface == "face-local"
    assert notice.presented_at is None and notice.read_at is None and notice.status == "pending"
    assert notice.revision == original.revision + 1 and notice.presentation_uncertain
    public = notice.public(clock[0])
    assert public["presentation_uncertain"] and public["presentation_attempted_at"] == clock[0]
    assert "presentation_token" not in public and "presentation_surface" not in public
    assert notice.presentation_token not in json.dumps(public) and "face-local" not in json.dumps(public)
    assert notice.presentation_token not in repr(notice)


def test_repeated_claim_and_restart_keep_one_durable_intent(state):
    store, clock, original = state
    attempted, _ = claim(store, original)
    reopened = CalendarStore.for_profile(store.root, clock=lambda: clock[0])
    same, changed = claim(reopened, original, "second-local-surface")
    assert not changed and same == attempted
    assert reopened.notice(original.id).presentation_token == attempted.presentation_token
    assert reopened.notice(original.id).presented_at is None


def test_simultaneous_surfaces_create_exactly_one_intent(state):
    store, clock, original = state
    other = CalendarStore.for_profile(store.root, clock=lambda: clock[0])
    gate = threading.Barrier(2)

    def attempt(owner, surface):
        gate.wait(timeout=5)
        return claim(owner, original, surface)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(attempt, owner, surface) for owner, surface in
                   ((store, "face-local"), (other, "desktop-companion"))]
        attempts = [future.result(timeout=10) for future in futures]
    assert sum(changed for _, changed in attempts) == 1
    assert attempts[0][0].presentation_token == attempts[1][0].presentation_token
    assert store.notice(original.id).read_at is None and store.notice(original.id).presented_at is None


def test_stale_initial_revision_is_refused_without_recording_intent(state):
    store, _, original = state
    with pytest.raises(CalendarRevisionConflict):
        store.claim_presentation(original.id, expected_revision=original.revision + 1, surface_id="face-local")
    assert store.notice(original.id) == original


def test_positive_receipt_records_actual_visual_presentation_but_never_reading(state):
    store, clock, original = state
    attempted, _ = claim(store, original)
    clock[0] += 1
    shown, changed = receipt(store, attempted)
    assert changed and shown.presented_at == clock[0] and shown.status == "presented"
    assert shown.read_at is None and not shown.presentation_uncertain
    same, changed = receipt(store, attempted)
    assert not changed and same == shown
    same, changed = receipt(store, attempted, shown=False)
    assert not changed and same == shown
    assert "presentation_token" not in shown.public(clock[0])


def test_negative_receipt_and_missing_receipt_leave_durable_uncertainty(state):
    store, clock, original = state
    attempted, _ = claim(store, original)
    rejected, changed = receipt(store, attempted, shown=False)
    assert not changed and rejected == attempted
    assert rejected.status == "pending" and rejected.read_at is None and rejected.presented_at is None
    reopened = CalendarStore.for_profile(store.root, clock=lambda: clock[0])
    assert reopened.notice(original.id).presentation_uncertain
    assert not claim(reopened, original, "another-surface")[1]


@pytest.mark.parametrize("changes", [{"token": "f" * 32}, {"surface": "other-local-surface"}])
def test_receipt_token_and_surface_must_match_exactly(state, changes):
    store, _, original = state
    attempted, _ = claim(store, original)
    with pytest.raises(ValueError):
        receipt(store, attempted, **changes)
    assert store.notice(original.id) == attempted


def test_a_receipt_cannot_claim_an_intent_that_does_not_exist(state):
    store, _, original = state
    with pytest.raises(ValueError):
        store.presentation_receipt(original.id, token="a" * 32, surface_id="face-local", shown=True)
    assert store.notice(original.id) == original


@pytest.mark.parametrize("action", ["read", "dismiss", "snooze"])
def test_receipt_preserves_a_newer_explicit_user_action(state, action):
    store, _, original = state
    attempted, _ = claim(store, original)
    acted, _ = store.act(original.id, action, expected_revision=attempted.revision,
                         **({"minutes": 10} if action == "snooze" else {}))
    shown, changed = receipt(store, attempted)
    assert changed and shown.presented_at is not None and shown.status == acted.status
    assert shown.read_at == acted.read_at and shown.dismissed_at == acted.dismissed_at
    assert shown.snoozed_until == acted.snoozed_until


@pytest.mark.parametrize("change", ["disabled", "hidden", "expired"])
def test_withdrawn_or_expired_intent_cannot_be_resurrected_by_late_receipt(state, change):
    store, clock, original = state
    attempted, _ = claim(store, original)
    if change == "disabled":
        store.set_preferences(expected_revision=1, reminders_enabled=False)
    elif change == "hidden":
        store.set_preferences(expected_revision=1, hidden_calendars=[original.calendar_key])
    else:
        clock[0] = original.expires_at
    before = store.notice(original.id)
    after, changed = receipt(store, attempted)
    assert not changed and after == before and after.presented_at is None and after.read_at is None


@pytest.mark.parametrize("change", ["disabled", "snoozed", "read", "dismissed", "expired"])
def test_non_pending_notices_cannot_acquire_a_visual_intent(state, change):
    store, clock, original = state
    if change == "disabled":
        store.set_preferences(expected_revision=1, reminders_enabled=False)
    elif change == "expired":
        clock[0] = original.expires_at
    else:
        action = {"snoozed": "snooze", "read": "read", "dismissed": "dismiss"}[change]
        store.act(original.id, action, expected_revision=original.revision,
                   **({"minutes": 10} if change == "snoozed" else {}))
    before = store.notice(original.id)
    after, changed = claim(store, before)
    assert not changed and after == before and after.presentation_attempted_at is None


def test_old_schema_one_rows_parse_with_additive_presentation_defaults(state):
    _, _, original = state
    legacy = asdict(original)
    for key in ("presentation_attempted_at", "presentation_token", "presentation_surface"):
        legacy.pop(key)
    parsed = CalendarNotice.parse(legacy)
    assert parsed == original and not parsed.presentation_uncertain


@pytest.mark.parametrize("changes", [
    {"presentation_attempted_at": START},
    {"presentation_token": "a" * 32},
    {"presentation_surface": "face-local"},
    {"presentation_attempted_at": START, "presentation_token": "A" * 32, "presentation_surface": "face-local"},
    {"presentation_attempted_at": START, "presentation_token": "a" * 32, "presentation_surface": "bad surface"},
    {"presentation_attempted_at": float("nan"), "presentation_token": "a" * 32, "presentation_surface": "face-local"},
])
def test_partial_or_malformed_presentation_capabilities_are_rejected(state, changes):
    _, _, original = state
    with pytest.raises(ValueError):
        CalendarNotice.parse({**asdict(original), **changes})


@pytest.mark.parametrize("shown", [1, "true", None])
def test_receipt_boolean_is_strict_and_invalid_input_does_not_write(state, shown):
    store, _, original = state
    attempted, _ = claim(store, original)
    with pytest.raises(ValueError):
        receipt(store, attempted, shown=shown)
    assert store.notice(original.id) == attempted


def test_existing_presented_method_still_records_a_real_display_without_an_intent(state):
    store, _, original = state
    actual, changed = store.presented(original.id, expected_revision=original.revision)
    assert changed and actual.presented_at == START and actual.status == "presented"
    assert actual.read_at is None and actual.presentation_attempted_at is None
    assert not claim(store, actual)[1]


@pytest.mark.parametrize("operation", ["claim", "receipt"])
def test_waiting_for_the_profile_lock_cannot_use_a_pre_expiry_clock(state, monkeypatch, operation):
    store, clock, original = state
    attempted = claim(store, original)[0] if operation == "receipt" else original
    original_transaction = store._transaction
    entered = threading.Event()

    @contextmanager
    def observed_transaction():
        entered.set()
        with original_transaction() as connection:
            yield connection

    monkeypatch.setattr(store, "_transaction", observed_transaction)
    with ThreadPoolExecutor(max_workers=1) as pool:
        # Hold the real profile transaction while the competing operation begins.
        # Its eligibility check must sample the clock only after this lock releases.
        with original_transaction():
            future = pool.submit(claim, store, original) if operation == "claim" else pool.submit(receipt, store, attempted)
            assert entered.wait(3), "presentation operation did not reach the held profile lock"
            clock[0] = original.expires_at
        after, changed = future.result(timeout=10)
    assert not changed and after == attempted
    assert after.presented_at is None and after.read_at is None
    if operation == "claim":
        assert after.presentation_attempted_at is None
