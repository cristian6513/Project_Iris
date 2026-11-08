"""Behavioral regressions for Vision V2 observed-time contracts."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from core.vision_assistant import VisionAssistantRuntime, VisionSignalError
from core.vision_session import (
    MotionWindow,
    PresenceSession,
    VisionCandidate,
    VisionDecision,
    VisionSessionTracker,
)


@pytest.mark.parametrize(
    "event_type",
    [
        "vision_assistant_state",
        "vision_assistant_event",
        "vision_assistant_events_cleared",
    ],
)
def test_vision_broadcasts_remain_local_but_other_events_still_reach_lan(event_type):
    from core.websocket_server import broadcast_to_face

    class Socket:
        def __init__(self, host):
            self.client = SimpleNamespace(host=host)
            self.send_text = AsyncMock()

    local, companion, remote, unknown = (
        Socket("127.0.0.1"),
        Socket("::1"),
        Socket("192.168.1.15"),
        Socket(None),
    )
    with patch("core.websocket_server._connected_clients", {local, companion, remote, unknown}):
        asyncio.run(broadcast_to_face(event_type, {"v2": {"session": {}}}))
        local.send_text.assert_awaited_once()
        companion.send_text.assert_awaited_once()
        remote.send_text.assert_not_awaited()
        unknown.send_text.assert_not_awaited()
        asyncio.run(broadcast_to_face("state", {"status": "idle"}))
        remote.send_text.assert_awaited_once()


def test_settings_api_exposes_v2_state_without_creating_a_session(tmp_path):
    from core.config import IRISConfig
    from core.websocket_server import get_vision_assistant_settings

    runtime = VisionAssistantRuntime(tmp_path / "events.json")
    with (
        patch("core.websocket_server._vision_assistant", runtime),
        patch("core.websocket_server.IRISConfig.load", return_value=IRISConfig()),
    ):
        response = asyncio.run(get_vision_assistant_settings())
    payload = json.loads(response.body)
    assert payload["v2"]["stage"] == "notifications"
    assert payload["v2"]["session"] is None
    assert payload["v2"]["decision"]["reason"] == "disabled"
    assert payload["contract"]["automatic_conversation"] is False
    assert not runtime.state_path.exists()


def observe(tracker, tick, *, wall=None, present=True, motion="still", surface="face"):
    tracker.observe(
        present=present,
        motion=motion,
        confidence=0.9,
        tick=float(tick),
        wall=float(1000 + tick if wall is None else wall),
        surface=surface,
    )
    return tracker.public(float(tick))


def ingest(runtime, tick, *, wall=None, present=True, confidence=0.9, **options):
    return runtime.ingest(
        {"present": present, "motion": "still", "confidence": confidence},
        surface_id="face",
        now=float(1000 + tick if wall is None else wall),
        monotonic_now=float(tick),
        **{
            "enabled": True,
            "paused_until": 0,
            "cooldown_minutes": 10,
            "retention_days": 7,
            **options,
        },
    )


def test_contracts_reject_unbounded_or_unannounced_information():
    for payload in (
        {"image": "raw"},
        {"still_seconds": float("nan")},
        {"low_seconds": True},
        {"active_seconds": -1},
        {"still_seconds": 601},
        {"basis": "emotion"},
    ):
        with pytest.raises(ValidationError):
            MotionWindow(**payload)
    with pytest.raises(ValidationError):
        VisionDecision(reason="collecting", delivery="voice")
    with pytest.raises(ValidationError):
        VisionDecision(reason="read-user-screen")
    candidate = VisionCandidate(
        candidate_id="a" * 32,
        session_id="b" * 32,
        kind="low_head_motion",
        observed_at=1000.0,
        observed_seconds=10.0,
        confidence=0.8,
    )
    assert candidate.model_dump(by_alias=True)["schema"] == "iris.vision-candidate/v2"
    with pytest.raises(ValidationError):
        VisionCandidate(**{**candidate.model_dump(), "frame": "raw"})


def test_only_consecutive_confirmed_samples_add_presence_time(tmp_path):
    runtime = VisionAssistantRuntime(tmp_path / "events.json")
    assert ingest(runtime, 0)["v2"]["session"] is None
    assert ingest(runtime, 2)["v2"]["session"]["observed_presence_seconds"] == 0
    result = ingest(runtime, 4)
    assert result["v2"]["session"]["observed_presence_seconds"] == 2
    assert result["v2"]["session"]["motion"]["still_seconds"] == 2
    assert result["v2"]["candidate"] is None
    assert result["v2"]["decision"]["delivery"] == "none"


def test_low_confidence_and_stabilization_do_not_bridge_time(tmp_path):
    runtime = VisionAssistantRuntime(tmp_path / "events.json")
    for t in (0, 2, 4):
        ingest(runtime, t)
    uncertain = ingest(runtime, 6, confidence=0.2)
    assert uncertain["v2"]["decision"]["reason"] == "low_confidence"
    assert uncertain["v2"]["session"]["observed_presence_seconds"] == 2
    ingest(runtime, 8)
    resumed = ingest(runtime, 10)
    assert resumed["v2"]["session"]["observed_presence_seconds"] == 4
    assert resumed["v2"]["session"]["motion"]["still_seconds"] == 2


def test_motion_window_clips_expired_intervals_and_uses_active_endpoint():
    tracker = VisionSessionTracker()
    for tick in range(0, 605, 2):
        observe(tracker, tick)
    result = observe(tracker, 606, motion="active")
    assert result["session"]["observed_presence_seconds"] == 606
    motion = result["session"]["motion"]
    assert motion["still_seconds"] == 598
    assert motion["active_seconds"] == 2
    # Reading a later snapshot ages the window, never extrapolates observations.
    snapshot = tracker.public(610)
    assert snapshot["session"]["observed_presence_seconds"] == 606
    assert snapshot["session"]["motion"]["still_seconds"] == 594


def test_short_absence_preserves_total_but_long_absence_starts_new_session():
    tracker = VisionSessionTracker()
    observe(tracker, 0)
    first = observe(tracker, 2)["session"]
    observe(tracker, 4, present=False)
    brief = observe(tracker, 30)["session"]
    assert brief["session_id"] == first["session_id"]
    assert brief["observed_presence_seconds"] == 2
    observe(tracker, 32)
    observe(tracker, 34, present=False)
    returned = observe(tracker, 332)["session"]
    assert returned["session_id"] != first["session_id"]
    assert returned["observed_presence_seconds"] == 0


@pytest.mark.parametrize(
    "tick,wall",
    [
        (2, 1002),  # duplicate
        (1, 1001),  # monotonic backwards
        (4, 4604),  # wall clock jumps forward
        (4, 800),  # wall clock jumps backwards
        (30, 1030),  # stalled stream
        (3600, 4600),  # sleep / missing stream for an hour
    ],
)
def test_clock_discontinuity_or_missing_stream_never_adds_phantom_time(tick, wall):
    tracker = VisionSessionTracker()
    observe(tracker, 0)
    observe(tracker, 2)
    result = observe(tracker, tick, wall=wall)
    assert result["session"]["observed_presence_seconds"] <= 2
    assert result["session"]["motion"]["still_seconds"] == 0
    assert result["decision"]["reason"] in {
        "clock_discontinuity",
        "signal_gap",
        "session_expired",
    }


def test_surface_handoff_and_camera_release_do_not_bridge_motion():
    tracker = VisionSessionTracker()
    observe(tracker, 0)
    observe(tracker, 2)
    result = observe(tracker, 4, surface="companion")
    assert result["decision"]["reason"] == "surface_changed"
    assert result["session"]["observed_presence_seconds"] == 2
    tracker.interrupt("camera_released")
    result = observe(tracker, 6, surface="companion")
    assert result["session"]["observed_presence_seconds"] == 2
    assert result["session"]["motion"]["still_seconds"] == 0


@pytest.mark.parametrize(
    "options,reason",
    [
        ({"enabled": False}, "disabled"),
        ({"paused_until": 1100}, "paused"),
    ],
)
def test_paused_or_disabled_never_adds_time(tmp_path, options, reason):
    runtime = VisionAssistantRuntime(tmp_path / "events.json")
    for t in (0, 2, 4):
        ingest(runtime, t)
    stopped = ingest(runtime, 6, **options)
    assert stopped["v2"]["decision"]["reason"] == reason
    assert stopped["v2"]["session"]["observed_presence_seconds"] == 2
    ingest(runtime, 10)
    assert ingest(runtime, 12)["v2"]["session"]["observed_presence_seconds"] == 2


def test_restart_restores_only_recent_aggregate_without_counting_downtime(tmp_path):
    path = tmp_path / "events.json"
    runtime = VisionAssistantRuntime(path)
    for t in range(0, 65, 2):
        ingest(runtime, t)
    state = json.loads(runtime.state_path.read_text(encoding="utf-8"))
    assert state["schema"] == "iris.vision-runtime/v2"
    assert "motion" not in state["session"]
    total = state["session"]["observed_presence_seconds"]
    with patch("core.vision_assistant.time.time", return_value=1070):
        restarted = VisionAssistantRuntime(path)
    result = ingest(restarted, 0, wall=1070)
    assert result["v2"]["session"]["observed_presence_seconds"] == total
    assert result["v2"]["session"]["motion"]["still_seconds"] == 0
    ingest(restarted, 2, wall=1072)
    assert (
        ingest(restarted, 4, wall=1074)["v2"]["session"]["observed_presence_seconds"] == total + 2
    )
    with patch("core.vision_assistant.time.time", return_value=2000):
        expired = VisionAssistantRuntime(path)
    assert (
        expired.snapshot(
            enabled=False,
            paused_until=0,
            camera={"active": False},
        )["v2"]["session"]
        is None
    )


def test_restored_session_expires_even_if_first_new_samples_are_uncertain():
    session = PresenceSession(
        session_id="c" * 32,
        started_at=1000.0,
        last_observed_at=1000.0,
        observed_presence_seconds=10.0,
    )
    tracker = VisionSessionTracker(session)
    observe(tracker, 0, wall=1002, present=None)
    result = observe(tracker, 300, wall=1302)
    assert result["session"]["session_id"] != session.session_id
    assert result["session"]["observed_presence_seconds"] == 0


def test_v1_state_migrates_lazily_and_preserves_greeting_cooldown(tmp_path):
    path = tmp_path / "events.json"
    state_path = tmp_path / "events_state.json"
    legacy = {"schema": "iris.vision-runtime/v1", "last_action_at": 995}
    state_path.write_text(json.dumps(legacy), encoding="utf-8")
    runtime = VisionAssistantRuntime(path)
    assert json.loads(state_path.read_text(encoding="utf-8")) == legacy
    ingest(runtime, 0, present=False)
    ingest(runtime, 2, present=False)
    ingest(runtime, 4, present=True)
    result = ingest(runtime, 6, present=True)
    assert result["event"]["suppression_reason"] == "notification_policy"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["last_action_at"] == 995
    assert state["schema"] == "iris.vision-runtime/v2"


@pytest.mark.parametrize(
    "content",
    [
        "{",
        "[]",
        '{"schema":"future/v9","session":{}}',
        '{"schema":"iris.vision-runtime/v2","session":{"frame":"raw"}}',
        '{"schema":"iris.vision-runtime/v2","session":{"last_observed_at":NaN}}',
    ],
)
def test_broken_or_unknown_checkpoint_does_not_break_startup(tmp_path, content):
    (tmp_path / "events_state.json").write_text(content, encoding="utf-8")
    runtime = VisionAssistantRuntime(tmp_path / "events.json")
    assert ingest(runtime, 0)["v2"]["session"] is None


def test_zero_retention_and_clear_delete_session_checkpoint_without_resetting_cooldown(tmp_path):
    runtime = VisionAssistantRuntime(tmp_path / "events.json")
    for t in (0, 2, 4):
        ingest(runtime, t)
    runtime._last_action_at = 999
    runtime.clear_events()
    state = json.loads(runtime.state_path.read_text(encoding="utf-8"))
    assert state["session"] is None
    assert state["last_action_at"] == 999
    assert runtime._session.session is None
    for t in (6, 8, 10):
        ingest(runtime, t, retention_days=0)
    assert json.loads(runtime.state_path.read_text(encoding="utf-8"))["session"] is None
    assert runtime._session.session.observed_presence_seconds == 4


def test_checkpoint_io_is_bounded_and_failure_is_reported_without_dropping_signals(tmp_path):
    runtime = VisionAssistantRuntime(tmp_path / "events.json")
    with patch.object(
        runtime, "_persist_last_action_at", wraps=runtime._persist_last_action_at
    ) as save:
        for tick in range(100):
            ingest(runtime, tick)
    assert save.call_count <= 4
    with patch.object(runtime, "_persist_last_action_at", side_effect=OSError("disk full")) as save:
        failed = ingest(runtime, 200)
        following = ingest(runtime, 202)
    assert failed["accepted"] is True
    assert failed["state_persisted"] is False
    assert following["state_persisted"] is False
    assert save.call_count == 1


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, True])
def test_invalid_server_clock_never_mutates_session(tmp_path, value):
    runtime = VisionAssistantRuntime(tmp_path / "events.json")
    with pytest.raises(VisionSignalError):
        runtime.ingest(
            {"present": True, "motion": "still", "confidence": 0.9},
            surface_id="face",
            enabled=True,
            paused_until=0,
            cooldown_minutes=10,
            retention_days=7,
            now=value,
            monotonic_now=1,
        )
    assert runtime._session.session is None
    assert not runtime.state_path.exists()


@pytest.mark.parametrize(
    "options,reason",
    [
        ({"enabled": False}, "disabled"),
        ({"paused_until": 2000}, "paused"),
    ],
)
def test_inactive_reason_is_not_overwritten_by_stale_signal(tmp_path, options, reason):
    runtime = VisionAssistantRuntime(tmp_path / "events.json")
    ingest(runtime, 0)
    ingest(runtime, 2)
    result = ingest(runtime, 100, **options)
    assert result["v2"]["decision"]["reason"] == reason
    assert result["v2"]["session"]["observed_presence_seconds"] == 0


def test_high_rate_signals_keep_motion_storage_bounded():
    tracker = VisionSessionTracker()
    for index in range(5000):
        observe(tracker, index / 100)
    assert len(tracker._segments) <= tracker.MAX_SEGMENTS
    result = tracker.public(50)
    assert result["session"]["observed_presence_seconds"] == pytest.approx(49.99)
    assert result["session"]["motion"]["still_seconds"] <= 20.49


def test_checkpoint_replace_failure_preserves_previous_valid_state(tmp_path):
    runtime = VisionAssistantRuntime(tmp_path / "events.json")
    for tick in (0, 2, 4):
        ingest(runtime, tick)
    before = runtime.state_path.read_bytes()
    with patch("core.vision_assistant.os.replace", side_effect=OSError("locked")):
        result = ingest(runtime, 70)
    assert result["state_persisted"] is False
    assert runtime.state_path.read_bytes() == before
    assert not list(tmp_path.glob("*.tmp"))
