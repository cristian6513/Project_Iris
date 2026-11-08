"""Return summaries share the durable inbox, policy budget and local permissions."""

import json
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from core.vision_assistant import VisionAssistantRuntime
from core.vision_notifications import NotificationPreferenceRequest, ReturnEvidence
from core.vision_policy import ACTIVITY_FLAGS, VisionPolicySettings

FREE = {"visible": True, **dict.fromkeys(ACTIVITY_FLAGS, False)}


def emit(runtime, tick, present=True, *, activity=None, threshold=43200, **kwargs):
    runtime.update_activity(
        FREE if activity is None else activity, surface_id="face", monotonic_now=float(tick)
    )
    return runtime.ingest(
        {"present": present, "motion": "active", "confidence": 0.9},
        surface_id="face",
        enabled=True,
        paused_until=0,
        cooldown_minutes=1,
        retention_days=7,
        now=1000.0 + tick,
        monotonic_now=float(tick),
        policy_settings=VisionPolicySettings(long_session_seconds=threshold),
        **kwargs,
    )


def preference(runtime, action, tick=64, **fields):
    runtime.set_notification_preferences(
        NotificationPreferenceRequest(
            action=action,
            confirm=True,
            expected_revision=runtime._inbox.revision,
            **fields,
        ),
        wall=1000.0 + tick,
        tick=float(tick),
    )


def ready(tmp_path, *, enabled=True):
    runtime = VisionAssistantRuntime(tmp_path / "events.json")
    runtime.set_notification_availability(True, wall=1000.0, tick=0.0)
    for tick in range(0, 64, 2):
        emit(runtime, tick, threshold=60)
    assert len(runtime._inbox.items) == 1
    if enabled:
        preference(runtime, "enable_return")
    emit(runtime, 120)
    for tick in range(130, 202, 2):
        emit(runtime, tick, present=False)
    emit(runtime, 202)
    return runtime


def test_return_delivers_summary_once_with_shared_budget(tmp_path):
    runtime = ready(tmp_path)
    result = emit(runtime, 204)
    assert result["event"]["type"] == "returned"
    assert result["event"]["actionable"] is False
    note = result["notification"]
    assert note["kind"] == "returned"
    assert note["explanation"]["related_ids"] == [runtime._inbox.items[0].candidate.candidate_id]
    assert note["explanation"]["observed_seconds"] >= 30
    assert result["v2"]["budget"]["used_last_hour"] == 2
    assert emit(runtime, 206)["notification"] is None
    saved = json.loads(runtime.state_path.read_text(encoding="utf-8"))
    assert saved["policy"]["receipts"][-1]["kind"] == "returned"
    assert saved["inbox"]["preferences"]["return_summary"] is True
    with patch("core.vision_assistant.time.time", return_value=1206.0):
        restarted = VisionAssistantRuntime(runtime.event_path)
    assert len(restarted._inbox.items) == 2
    assert not restarted.availability(wall=1206.0, tick=206.0)["available"]
    # Opening either UI is read-only for delivery/budget.
    for _ in range(3):
        runtime.notification_snapshot(retention_days=7, wall=1206.0, tick=206.0)
    assert len(runtime._inbox.items) == 2


@pytest.mark.parametrize("block", ["default", "mute", "availability", "typing", "busy"])
def test_return_respects_opt_in_and_activity(tmp_path, block):
    runtime = ready(tmp_path, enabled=block != "default")
    kwargs = {}
    if block == "mute":
        preference(runtime, "mute", tick=203, minutes=30)
    if block == "availability":
        runtime.set_notification_availability(False, wall=1203.0, tick=203.0)
    if block == "typing":
        kwargs["activity"] = {**FREE, "typing": True}
    if block == "busy":
        kwargs["backend_busy"] = True
    result = emit(runtime, 204, **kwargs)
    assert result["notification"] is None
    assert result["event"]["actionable"] is False
    assert result["v2"]["budget"]["used_last_hour"] == 1


@pytest.mark.parametrize("state", ["read", "dismissed", "snoozed", "disabled", "empty"])
def test_only_relevant_unread_items_qualify(tmp_path, state):
    runtime = ready(tmp_path)
    note = runtime._inbox.items[0]
    if state == "disabled":
        preference(runtime, "disable_kind", tick=203, kind="long_session")
    else:
        changes = {
            "read": {"read_at": 1203.0},
            "dismissed": {"dismissed_at": 1203.0},
            "snoozed": {"snoozed_until": 1400.0},
            "empty": {},
        }[state]
        runtime._save_inbox(
            runtime._inbox.changed([] if state == "empty" else [note.model_copy(update=changes)]),
            wall=1203.0,
            tick=203.0,
        )
    assert emit(runtime, 204)["notification"] is None


def test_summary_does_not_summarize_itself_or_repeat_pending_items(tmp_path):
    runtime = ready(tmp_path)
    assert emit(runtime, 204)["notification"]
    runtime.set_notification_availability(True, wall=6000.0, tick=5000.0)
    emit(runtime, 5000)
    for tick in range(5002, 5074, 2):
        emit(runtime, tick, present=False)
    emit(runtime, 5074)
    assert emit(runtime, 5076)["notification"] is None
    assert len(runtime._inbox.items) == 2


@pytest.mark.parametrize("reason", ["camera_released", "surface_changed", "paused", "disabled"])
def test_reset_does_not_create_a_return(tmp_path, reason):
    runtime = ready(tmp_path)
    runtime.reset_observation(reason)
    runtime.set_notification_availability(True, wall=1203.0, tick=203.0)
    emit(runtime, 204)
    assert emit(runtime, 206)["notification"] is None
    assert len(runtime._inbox.items) == 1


def test_gap_and_disk_failure_do_not_deliver_or_consume(tmp_path):
    runtime = ready(tmp_path)
    assert emit(runtime, 230)["notification"] is None
    assert len(runtime._inbox.items) == 1
    runtime = ready(tmp_path / "failure")
    before = runtime.state_path.read_bytes()
    with patch("core.vision_assistant.os.replace", side_effect=OSError("locked")):
        result = emit(runtime, 204)
    assert result["notification"] is None
    assert result["v2"]["decision"]["reason"] == "notification_storage"
    assert runtime.state_path.read_bytes() == before
    assert len(runtime._policy.checkpoint(wall=1204.0, tick=204.0)["receipts"]) == 1


def test_return_contracts_are_strict_and_default_off():
    from core.vision_notifications import NotificationPreferences

    assert NotificationPreferences().return_summary is False
    for payload in [
        {"action": "enable_return", "confirm": False, "expected_revision": 0},
        {"action": "enable_return", "confirm": True, "expected_revision": 0, "minutes": 30},
        {
            "action": "enable_return",
            "confirm": True,
            "expected_revision": 0,
            "kind": "long_session",
        },
    ]:
        with pytest.raises(ValidationError):
            NotificationPreferenceRequest.model_validate(payload)
    for references in [[], ["a" * 32, "a" * 32], ["not-an-id"]]:
        with pytest.raises(ValidationError):
            ReturnEvidence(observed_seconds=30.0, confidence=0.9, related_ids=references)


@pytest.mark.parametrize("block", ["none", "read", "disabled", "typing", "stale"])
def test_presentation_is_revalidated_and_one_shot(tmp_path, block):
    runtime = ready(tmp_path)
    note = emit(runtime, 204)["notification"]
    if block == "read":
        runtime.update_notification(note["id"], read=True, wall=1205.0, tick=205.0)
    if block == "disabled":
        preference(runtime, "disable_return", tick=205)
    if block == "typing":
        runtime.update_activity({**FREE, "typing": True}, surface_id="face", monotonic_now=205.0)
    tick = 214.0 if block == "stale" else 205.0
    output = runtime.return_presentation(
        note["id"],
        activity_surfaces=("face",),
        wall=1000.0 + tick,
        tick=tick,
    )
    assert (output is not None) == (block == "none")
    assert (
        runtime.return_presentation(
            note["id"],
            activity_surfaces=("face",),
            wall=1000.0 + tick,
            tick=tick,
        )
        is None
    )


def test_regression_existing_receipt_blocks_return_budget(tmp_path):
    runtime = ready(tmp_path)
    runtime._policy_settings = VisionPolicySettings(max_per_hour=1)
    # evaluate the actual return at an already full shared hourly budget.
    runtime.update_activity(FREE, surface_id="face", monotonic_now=204.0)
    result = runtime.ingest(
        {"present": True, "confidence": 0.9, "motion": "active"},
        surface_id="face",
        enabled=True,
        paused_until=0,
        cooldown_minutes=1,
        retention_days=7,
        now=1204.0,
        monotonic_now=204.0,
        policy_settings=VisionPolicySettings(max_per_hour=1),
    )
    assert result["notification"] is None
    assert result["v2"]["decision"]["reason"] == "hourly_budget"


@pytest.mark.parametrize(
    "case", ["normal", "short", "gap", "clock", "confidence", "handoff", "initial"]
)
def test_return_requires_continuous_reliable_observed_absence(case):
    from core.vision_return import VisionReturnTracker

    tracker = VisionReturnTracker()

    def observe(tick, present, event=None, confidence=0.9, offset=0, surface="face"):
        return tracker.observe(
            signal={"present": present, "confidence": confidence},
            stable_presence=present,
            event=event,
            surface=surface,
            wall=1000.0 + tick + offset,
            tick=float(tick),
            enabled=True,
        )

    if case != "initial":
        observe(0, True)
    for tick in range(2, 30 if case == "short" else 44, 2):
        observe(tick, False)
    tick = 30 if case == "short" else 70 if case == "gap" else 44
    result = observe(
        tick,
        True,
        event={"type": "returned"},
        confidence=0.4 if case == "confidence" else 0.9,
        offset=20 if case == "clock" else 0,
        surface="companion" if case == "handoff" else "face",
    )
    assert bool(result) == (case == "normal")


def test_legacy_greeting_migrates_into_shared_budget(tmp_path):
    state = tmp_path / "events_state.json"
    state.write_text(
        json.dumps({"schema": "iris.vision-runtime/v1", "last_action_at": 995}),
        encoding="utf-8",
    )
    runtime = VisionAssistantRuntime(tmp_path / "events.json")
    receipts = runtime._policy.checkpoint(wall=1000.0, tick=0.0)["receipts"]
    assert len(receipts) == 1 and receipts[0]["kind"] == "returned"
    assert runtime._inbox.preferences.return_summary is False
