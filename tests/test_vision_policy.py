"""Deterministic policy regressions: preview never dispatches or spends a budget."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from core.vision_policy import (
    ACTIVITY_FLAGS,
    DeliveryReceipt,
    KindCooldowns,
    PolicyCheckpoint,
    QuietHours,
    VisionActivity,
    VisionPolicyEngine,
    VisionPolicySettings,
)
from core.vision_session import MotionWindow, PresenceSession

WALL = 100_000.0
FREE = VisionActivity(visible=True, **dict.fromkeys(ACTIVITY_FLAGS, False))
SETTINGS = VisionPolicySettings(low_head_session_seconds=43200)


def evaluate(
    engine,
    *,
    tick=0.0,
    wall=None,
    total=2700.0,
    activity=FREE,
    settings=SETTINGS,
    observation_reason="collecting",
    motion=None,
    session_id="a" * 32,
    **kwargs,
):
    session = PresenceSession(
        session_id=session_id,
        started_at=WALL,
        last_observed_at=WALL + tick,
        observed_presence_seconds=total,
        motion=motion or MotionWindow(still_seconds=600.0, confidence=0.9),
    )
    return engine.evaluate(
        session,
        activity=activity,
        settings=settings,
        wall=WALL + tick if wall is None else wall,
        tick=float(tick),
        observation_reason=observation_reason,
        **{"confidence": 0.9, "cooldown_minutes": 25, **kwargs},
    )


def acknowledge(engine, result, tick):
    return engine.confirm_delivery(
        result["candidate"]["candidate_id"],
        wall=WALL + tick,
        tick=float(tick),
    )


@pytest.mark.parametrize(
    "settings",
    [
        {"max_per_hour": True},
        {"max_per_hour": 7},
        {"max_per_hour": -1},
        {"long_session_seconds": 59},
        {"low_head_session_seconds": 43201},
        {"min_motion_window_seconds": 601},
        {"low_motion_ratio": 0.0},
        {"min_confidence": float("nan")},
        {"min_confidence": 0.54},
        {"quiet_hours": [{"start_minute": 1440, "end_minute": 0}]},
        {"quiet_hours": [{"start_minute": 0, "end_minute": 0}] * 5},
        {"kind_cooldown_minutes": {"long_session": 181}},
        {"image": "private"},
    ],
)
def test_policy_settings_are_bounded_and_strict(settings):
    with pytest.raises(ValidationError):
        VisionPolicySettings.model_validate(settings)


@pytest.mark.parametrize(
    "payload",
    [
        {"typing": "false"},
        {"visible": 1},
        {"audio": "private"},
        {"typing": {"text": "private"}},
        {"app_title": "private"},
    ],
)
def test_activity_contract_never_accepts_content(payload):
    with pytest.raises(ValidationError):
        VisionActivity.model_validate(payload)


def test_threshold_boundary_and_stable_ids_do_not_spend_budget():
    engine = VisionPolicyEngine()
    early = evaluate(engine, total=2699.0)
    assert early["candidate"] is None
    assert early["decision"]["reason"] == "below_threshold"
    ids = set()
    for tick in range(50):
        result = evaluate(engine, tick=tick, total=2700.0 + tick)
        ids.add(result["candidate"]["candidate_id"])
        assert result["decision"]["outcome"] == "eligible"
        assert result["decision"]["delivery"] == "none"
        assert result["budget"]["used_last_hour"] == 0
    assert len(ids) == 1
    assert engine.checkpoint(wall=WALL + 49, tick=49)["receipts"] == []


def test_motion_requires_coverage_ratio_session_and_window_confidence():
    settings = VisionPolicySettings()
    engine = VisionPolicyEngine()
    assert evaluate(engine, total=2399.0, settings=settings)["candidate"] is None
    for motion in (
        MotionWindow(still_seconds=539.0, confidence=0.9),
        MotionWindow(still_seconds=500.0, active_seconds=100.0, confidence=0.9),
    ):
        assert evaluate(engine, total=2400.0, settings=settings, motion=motion)["candidate"] is None
    result = evaluate(
        engine,
        total=2400.0,
        settings=settings,
        motion=MotionWindow(still_seconds=540.0, confidence=0.6),
    )
    assert result["candidate"]["kind"] == "low_head_motion"
    assert result["decision"]["reason"] == "low_confidence"


def test_priority_selects_at_most_one_rule_and_keeps_explanation():
    result = evaluate(VisionPolicyEngine(), settings=VisionPolicySettings())
    assert result["candidate"]["kind"] == "low_head_motion"
    assert [r["decision"]["reason"] for r in result["evaluations"]] == [
        "eligible",
        "lower_priority",
    ]
    evidence = result["evaluations"][0]["evidence"]
    assert evidence["basis"] == "head_pose"
    assert evidence["threshold_seconds"] == 2400
    assert evidence["confidence"] == 0.9
    assert result["decision"]["explanation"]


@pytest.mark.parametrize("flag", ACTIVITY_FLAGS)
def test_each_explicit_activity_suppresses(flag):
    activity = FREE.model_copy(update={flag: True})
    result = evaluate(VisionPolicyEngine(), activity=activity)
    assert result["decision"]["reason"] == f"activity_{flag}"
    assert result["decision"]["outcome"] == "suppressed"


@pytest.mark.parametrize(
    "activity,reason",
    [
        (VisionActivity(), "activity_unknown"),
        (FREE.model_copy(update={"visible": False}), "surface_hidden"),
        (FREE.model_copy(update={"meeting": None}), "activity_unknown"),
        (VisionActivity(typing=True), "activity_typing"),
    ],
)
def test_missing_context_fails_closed_and_positive_evidence_wins(activity, reason):
    assert evaluate(VisionPolicyEngine(), activity=activity)["decision"]["reason"] == reason


def test_backend_busy_suppresses_even_if_surface_says_idle():
    assert evaluate(VisionPolicyEngine(), backend_busy=True)["decision"]["reason"] == "agent_busy"


@pytest.mark.parametrize(
    "reason",
    [
        "disabled",
        "paused",
        "signal_gap",
        "camera_released",
        "absent",
        "surface_changed",
        "clock_discontinuity",
        "restarted",
    ],
)
def test_unavailable_observation_never_builds_candidate(reason):
    result = evaluate(VisionPolicyEngine(), observation_reason=reason)
    assert result["candidate"] is None
    assert result["decision"]["reason"] == reason
    assert result["evaluations"] == []


@pytest.mark.parametrize(
    "minute,expected",
    [
        (1319, False),
        (1320, True),
        (1439, True),
        (0, True),
        (419, True),
        (420, False),
    ],
)
def test_quiet_hours_wrap_midnight_with_exclusive_end(minute, expected):
    period = QuietHours(start_minute=1320, end_minute=420)
    assert period.contains(minute) is expected
    settings = SETTINGS.model_copy(update={"quiet_hours": [period]})
    with patch(
        "core.vision_policy.time.localtime",
        return_value=SimpleNamespace(tm_hour=minute // 60, tm_min=minute % 60),
    ):
        reason = evaluate(VisionPolicyEngine(), settings=settings)["decision"]["reason"]
    assert (reason == "quiet_hours") is expected


def test_equal_quiet_endpoints_mean_all_day():
    period = QuietHours(start_minute=60, end_minute=60)
    assert all(period.contains(m) for m in range(1440))


def test_confirmed_delivery_is_once_and_deduplicated_across_surfaces():
    engine = VisionPolicyEngine()
    first = evaluate(engine)
    assert acknowledge(engine, first, 0)
    assert not acknowledge(engine, first, 0)
    duplicate = evaluate(engine, tick=3)
    assert duplicate["decision"]["reason"] == "duplicate"
    assert duplicate["budget"]["used_last_hour"] == 1
    restored = VisionPolicyEngine(
        PolicyCheckpoint.model_validate(
            engine.checkpoint(wall=WALL + 3, tick=3),
        )
    )
    assert evaluate(restored, tick=0, wall=WALL + 5)["decision"]["reason"] == "duplicate"


@pytest.mark.parametrize("action", ["expired", "invalidated", "suppressed", "clock"])
def test_stale_or_revoked_offer_cannot_consume_budget(action):
    engine = VisionPolicyEngine()
    offer = evaluate(engine)
    tick, wall = 0.0, WALL
    if action == "expired":
        tick, wall = 9.0, WALL + 9
    elif action == "invalidated":
        engine.invalidate_offers()
    elif action == "suppressed":
        evaluate(engine, activity=VisionActivity(typing=True))
    else:
        tick, wall = 1.0, WALL + 100
    assert not engine.confirm_delivery(offer["candidate"]["candidate_id"], tick=tick, wall=wall)
    assert engine.checkpoint(tick=tick, wall=wall)["receipts"] == []


def test_global_and_kind_cooldowns_are_independent_and_monotonic():
    engine = VisionPolicyEngine()
    assert acknowledge(engine, evaluate(engine), 0)
    result = evaluate(engine, tick=60, total=5400.0)
    assert result["decision"]["reason"] == "global_cooldown"
    assert result["decision"]["retry_after_seconds"] == 1440
    result = evaluate(engine, tick=1500, total=5400.0)
    assert result["decision"]["reason"] == "kind_cooldown"
    assert result["decision"]["retry_after_seconds"] == 1200
    result = evaluate(engine, tick=2700, total=5400.0)
    assert result["decision"]["reason"] == "eligible"
    assert acknowledge(engine, result, 2700)
    assert evaluate(engine, tick=2701, total=8100.0)["decision"]["reason"] == "hourly_budget"
    # At exactly one hour the first receipt expires, but the cooldown remains.
    result = evaluate(engine, tick=3600, total=8100.0)
    assert result["budget"]["used_last_hour"] == 1
    assert result["decision"]["reason"] == "global_cooldown"


def test_hourly_cap_reduction_waits_for_enough_receipts_to_expire():
    engine = VisionPolicyEngine()
    settings = SETTINGS.model_copy(
        update={
            "long_session_seconds": 60,
            "max_per_hour": 6,
            "kind_cooldown_minutes": KindCooldowns(long_session=1, low_head_motion=1),
        }
    )
    for t in (0, 60, 120):
        result = evaluate(engine, tick=t, total=60.0 + t, settings=settings, cooldown_minutes=1)
        assert acknowledge(engine, result, t)
    settings = settings.model_copy(update={"max_per_hour": 1})
    result = evaluate(engine, tick=180, total=240.0, settings=settings, cooldown_minutes=1)
    assert result["decision"]["reason"] == "hourly_budget"
    assert result["decision"]["retry_after_seconds"] == 3540
    result = evaluate(engine, tick=3720, total=240.0, settings=settings, cooldown_minutes=1)
    assert result["decision"]["reason"] == "eligible"


def test_zero_budget_disables_interventions_without_disabling_observation():
    result = evaluate(
        VisionPolicyEngine(), settings=SETTINGS.model_copy(update={"max_per_hour": 0})
    )
    assert result["candidate"] is not None
    assert result["decision"]["reason"] == "hourly_budget"


@pytest.mark.parametrize("wall,tick", [(WALL + 3601, 1), (WALL - 3599, 1), (WALL - 1, 0)])
def test_clock_changes_never_reset_receipts(wall, tick):
    engine = VisionPolicyEngine()
    first = evaluate(engine, tick=1, wall=WALL + 1)
    assert acknowledge(engine, first, 1)
    result = evaluate(engine, wall=wall, tick=tick, total=5400.0)
    assert result["decision"]["reason"] == "clock_discontinuity"
    assert result["budget"]["used_last_hour"] == 1
    result = evaluate(engine, wall=wall + 60, tick=tick + 60, total=5400.0)
    assert result["decision"]["reason"] == "global_cooldown"


def test_future_checkpoint_is_guarded_not_forgotten():
    checkpoint = PolicyCheckpoint(
        receipts=[
            DeliveryReceipt(
                candidate_id="b" * 32,
                kind="long_session",
                delivered_at=WALL + 1000,
            )
        ]
    )
    engine = VisionPolicyEngine(checkpoint)
    result = evaluate(engine)
    assert result["decision"]["reason"] == "clock_discontinuity"
    assert result["budget"]["used_last_hour"] == 1
    assert evaluate(engine, tick=60)["decision"]["reason"] == "global_cooldown"


def test_dedup_outlives_max_threshold_but_checkpoint_storage_is_bounded():
    engine = VisionPolicyEngine()
    settings = SETTINGS.model_copy(update={"long_session_seconds": 43200})
    result = evaluate(
        engine,
        total=43200.0,
        settings=settings,
        motion=MotionWindow(active_seconds=600.0, confidence=0.9),
    )
    assert acknowledge(engine, result, 0)
    result = evaluate(
        engine,
        tick=10801,
        total=54001.0,
        settings=settings,
        motion=MotionWindow(active_seconds=600.0, confidence=0.9),
    )
    assert result["decision"]["reason"] == "duplicate"
    checkpoint = engine.checkpoint(wall=WALL + 10801, tick=10801)
    assert len(checkpoint["receipts"]) == 1
    assert len(json.dumps(checkpoint)) < 1024
    assert engine.checkpoint(wall=WALL + 86400, tick=86400)["receipts"] == []


def test_surface_merge_requires_all_known_but_any_busy_surface_blocks():
    assert VisionActivity.combine([FREE, VisionActivity()]).meeting is None
    assert VisionActivity.combine([FREE, VisionActivity(typing=True)]).typing is True
    assert VisionActivity.combine([FREE, VisionActivity(visible=False)]).visible is True
    assert VisionActivity.combine([]) == VisionActivity()
