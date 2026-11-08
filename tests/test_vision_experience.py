"""Vision V2 step 6: explicit local tuning, bounded diagnostics and ownership."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from pydantic import ValidationError

from core.config import IRISConfig
from core.notification_actions import NotificationConflictError
from core.surface_registry import SurfaceRegistry
from core.vision_assistant import VisionAssistantRuntime
from core.vision_diagnostics import VisionDiagnostics, VisionSensorDiagnostics
from core.vision_experience import VisionExperience, effective_cooldown, effective_policy
from core.vision_notifications import (
    NotificationExperienceRequest,
    NotificationFeedbackRequest,
    NotificationPreferenceRequest,
)
from core.vision_policy import ACTIVITY_FLAGS, VisionPolicySettings


@pytest.fixture
def runtime(tmp_path):
    rt = VisionAssistantRuntime(tmp_path / "events.json")
    rt.set_notification_availability(True, wall=1000.0, tick=0.0)
    for tick in range(0, 64, 2):
        rt.update_activity(
            {"visible": True, **dict.fromkeys(ACTIVITY_FLAGS, False)},
            surface_id="face",
            monotonic_now=float(tick),
        )
        rt.ingest(
            {"present": True, "motion": "still", "confidence": 0.9},
            surface_id="face",
            surface_role="face",
            enabled=True,
            paused_until=0,
            cooldown_minutes=25,
            retention_days=7,
            now=1000.0 + tick,
            monotonic_now=float(tick),
            policy_settings=VisionPolicySettings(long_session_seconds=60),
        )
    assert len(rt._inbox.items) == 1
    return rt


def request(rt, **data):
    return NotificationExperienceRequest(confirm=True, expected_revision=rt._inbox.revision, **data)


def feedback(rt, value):
    return NotificationFeedbackRequest(
        confirm=True,
        expected_revision=rt._inbox.revision,
        id=rt._inbox.items[0].candidate.candidate_id,
        feedback=value,
    )


def snapshot(rt):
    return rt.notification_snapshot(retention_days=7, wall=1063.0, tick=63.0)


@pytest.mark.parametrize(
    "profile,cap,seconds",
    [
        ("custom", 2, 2700),
        ("silent", 0, 2700),
        ("company", 1, 3600),
        ("wellbeing", 2, 2700),
        ("work", 1, 5400),
    ],
)
def test_presets_are_explicit_and_keep_custom_policy(profile, cap, seconds):
    base = VisionPolicySettings()
    before = base.model_dump()
    experience = VisionExperience(profile=profile)
    effective = effective_policy(base, experience)
    assert (effective.max_per_hour, effective.long_session_seconds) == (cap, seconds)
    assert base.model_dump() == before
    assert effective_cooldown(180, experience) == 180


@pytest.mark.parametrize(
    "fields",
    [
        {"action": "profile", "profile": "invalid"},
        {"action": "profile"},
        {"action": "performance", "enabled": True},
        {"action": "greetings", "enabled": 1},
        {"action": "physical_suggestions", "enabled": "false"},
        {"action": "reset_feedback", "enabled": False},
        {"action": "profile", "profile": "work", "performance": "economy"},
        {"action": "profile", "profile": "work", "confirm": False},
        {"action": "profile", "profile": "work", "camera": True},
    ],
)
def test_strict_experience_contract(fields):
    with pytest.raises(ValidationError):
        NotificationExperienceRequest.model_validate(
            {"confirm": True, "expected_revision": 0, **fields}
        )


def test_profile_accessibility_and_mute_preserve_permissions_receipts_and_restart(runtime):
    before_receipts = json.loads(runtime.state_path.read_text())["policy"]["receipts"]
    runtime.set_vision_experience(
        request(runtime, action="profile", profile="work"), wall=1063.0, tick=63.0
    )
    runtime.set_vision_experience(
        request(runtime, action="physical_suggestions", enabled=False), wall=1063.0, tick=63.0
    )
    runtime.set_vision_experience(
        request(runtime, action="performance", performance="economy"), wall=1063.0, tick=63.0
    )
    runtime.set_notification_preferences(
        NotificationPreferenceRequest(
            confirm=True, expected_revision=runtime._inbox.revision, action="mute", minutes=30
        ),
        wall=1063.0,
        tick=63.0,
    )
    with patch("core.vision_assistant.time.time", return_value=1063.0):
        restored = VisionAssistantRuntime(runtime.event_path)
    assert restored._inbox.preferences.experience == runtime._inbox.preferences.experience
    assert "prioridades" in snapshot(restored)["items"][0]["body"]
    draft = restored.notification_draft(
        runtime._inbox.items[0].candidate.candidate_id, retention_days=7, wall=1063.0
    )
    assert "prioridades" in draft["text"] and not draft["sent"]
    saved = json.loads(runtime.state_path.read_text())
    assert saved["policy"]["receipts"] == before_receipts
    assert "permissions" not in saved["inbox"]["preferences"]


def test_feedback_once_atomic_and_reversible_local_effect(runtime):
    vote = feedback(runtime, "too_soon")
    runtime.record_vision_feedback(vote, retention_days=7, wall=1063.0, tick=63.0)
    assert runtime._inbox.preferences.experience.feedback_delays.long_session == 15
    with pytest.raises(NotificationConflictError):
        runtime.record_vision_feedback(vote, retention_days=7, wall=1063.0, tick=63.0)
    runtime.record_vision_feedback(
        feedback(runtime, "too_soon"), retention_days=7, wall=1063.0, tick=63.0
    )
    assert runtime._inbox.preferences.experience.feedback_delays.long_session == 15
    with pytest.raises(NotificationConflictError):
        runtime.record_vision_feedback(
            feedback(runtime, "useful"), retention_days=7, wall=1063.0, tick=63.0
        )
    runtime.set_vision_experience(request(runtime, action="reset_feedback"), wall=1063.0, tick=63.0)
    assert runtime._inbox.preferences.experience.feedback_delays.long_session == 0
    assert snapshot(runtime)["items"][0]["feedback"] == "too_soon"
    assert len(json.loads(runtime.state_path.read_text())["policy"]["receipts"]) == 1


@pytest.mark.parametrize("operation", ["preferences", "feedback"])
def test_failed_tuning_rolls_back_disk_and_memory(runtime, operation):
    before = runtime.state_path.read_bytes()
    inbox = runtime._inbox
    with patch("core.vision_assistant.os.replace", side_effect=OSError("locked")):
        with pytest.raises(OSError):
            if operation == "preferences":
                runtime.set_vision_experience(
                    request(runtime, action="profile", profile="silent"), wall=1063.0, tick=63.0
                )
            else:
                runtime.record_vision_feedback(
                    feedback(runtime, "not_applicable"), retention_days=7, wall=1063.0, tick=63.0
                )
    assert runtime.state_path.read_bytes() == before and runtime._inbox == inbox


def test_not_applicable_keeps_note_but_blocks_kind_even_after_profile_change(runtime):
    runtime.record_vision_feedback(
        feedback(runtime, "not_applicable"), retention_days=7, wall=1063.0, tick=63.0
    )
    runtime.set_vision_experience(
        request(runtime, action="profile", profile="wellbeing"), wall=1063.0, tick=63.0
    )
    assert runtime._notification_blocks(1063.0)["long_session"] == "notification_kind_disabled"
    assert len(snapshot(runtime)["items"]) == 1


def test_disabled_greeting_and_physical_suggestions_are_independent(runtime):
    runtime.set_vision_experience(
        request(runtime, action="greetings", enabled=False), wall=1063.0, tick=63.0
    )
    assert (
        runtime.return_presentation("a" * 32, activity_surfaces=("face",), wall=1063.0, tick=63.0)
        is None
    )
    runtime.set_vision_experience(
        request(runtime, action="physical_suggestions", enabled=False), wall=1063.0, tick=63.0
    )
    assert "low_head_motion" in runtime._notification_blocks(1063.0)
    assert "long_session" not in runtime._notification_blocks(1063.0)


def test_diagnostics_are_bounded_unique_read_only_and_clearable(runtime):
    before = runtime.state_path.read_bytes()
    rows = snapshot(runtime)["diagnostics"]["rows"]
    for _ in range(5):
        assert snapshot(runtime)["diagnostics"]["rows"] == rows
    assert runtime.state_path.read_bytes() == before
    diag = VisionDiagnostics()
    report = {"decision": {"outcome": "suppressed", "reason": "activity_typing"}}
    for tick in range(500):
        diag.record(report, wall=float(tick), role="face", retention_days=7)
    assert len(diag.rows) == 1
    for tick in range(200):
        diag.record(
            report,
            wall=float(tick),
            role="face" if tick % 2 else "desktop_companion",
            retention_days=7,
        )
    assert len(diag.rows) == 100
    assert diag.snapshot(wall=1000.0, tick=1000.0, retention_days=0)["rows"] == []
    runtime.clear_vision_diagnostics()
    assert snapshot(runtime)["diagnostics"]["rows"] == []
    assert runtime.state_path.read_bytes() == before


SENSOR = dict(
    frames=50,
    misses=0,
    latency_ms=9.0,
    interval_ms=67,
    state="observing",
    calibration="ready",
    progress=100,
    still_threshold=0.3,
    active_threshold=1.2,
)


@pytest.mark.parametrize(
    "change",
    [
        {"image": "private"},
        {"frames": True},
        {"latency_ms": float("nan")},
        {"progress": 101},
        {"still_threshold": 2.0},
        {"state": "happy"},
        {"audio": []},
        {"frames": -1},
    ],
)
def test_sensor_contract_rejects_media_and_invalid_counters(change):
    with pytest.raises(ValidationError):
        VisionSensorDiagnostics.model_validate({**SENSOR, **change})


def test_sensor_snapshot_expires_and_camera_release_discards_it(runtime):
    runtime.report_vision_sensor(VisionSensorDiagnostics(**SENSOR), surface_role="face", tick=63.0)
    assert snapshot(runtime)["diagnostics"]["sensor"]["calibration"] == "ready"
    assert (
        runtime.notification_snapshot(retention_days=7, wall=1072.0, tick=72.0)["diagnostics"][
            "sensor"
        ]
        is None
    )
    runtime.reset_observation()
    assert snapshot(runtime)["diagnostics"]["sensor"] is None


@pytest.mark.parametrize(
    "block", ["none", "off", "paused", "old", "hidden", "remote", "no_camera", "failed"]
)
def test_calibration_targets_only_existing_local_modern_camera(monkeypatch, block):
    from core import websocket_server as server

    registry = SurfaceRegistry()
    owner = SimpleNamespace()  # use a hashable socket object

    class Socket:
        pass

    owner = Socket()
    owner.client = SimpleNamespace(host="192.168.1.3" if block == "remote" else "127.0.0.1")
    owner.send_json = AsyncMock(side_effect=OSError("closed") if block == "failed" else None)
    registry.register_ready(
        owner,
        {
            "role": "desktop_companion",
            "visible": block != "hidden",
            "capabilities": {"tunes_vision": block != "old"},
        },
    )
    if block != "no_camera":
        registry.request_camera(owner)
    monkeypatch.setattr(server, "_surface_registry", registry)
    monkeypatch.setattr(
        server.IRISConfig,
        "load",
        lambda: IRISConfig(
            vision_assistant_enabled=block != "off",
            vision_assistant_paused_until=1e12 if block == "paused" else 0,
        ),
    )
    before = registry.camera_owner()
    if block == "none":
        asyncio.run(server._calibrate_vision("start"))
        data = owner.send_json.call_args.args[0]
        assert data["type"] == "vision_assistant_calibration"
        assert data["payload"]["expires_at"] - data["payload"]["created_at"] == 5
    else:
        from fastapi import HTTPException

        with pytest.raises(HTTPException):
            asyncio.run(server._calibrate_vision("start"))
    assert registry.camera_owner() is before
    assert owner.send_json.call_count == int(block in {"none", "failed"})


def test_http_experience_is_confirmed_local_and_has_no_agent_dispatch(runtime, monkeypatch):
    from core import websocket_server as server

    monkeypatch.setattr(server, "_vision_assistant", runtime)
    monkeypatch.setattr(server.IRISConfig, "load", lambda: IRISConfig())
    dispatch = AsyncMock(side_effect=AssertionError("must not send"))
    monkeypatch.setattr(server, "_handle_user_text", dispatch)

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server.app, client=("127.0.0.1", 5000)),
            base_url="http://127.0.0.1",
        ) as client:
            body = request(runtime, action="profile", profile="work").model_dump(exclude_none=True)
            r = await client.post("/api/notifications/experience", json=body)
            assert r.status_code == 200
            assert r.json()["vision"]["enabled"] is False
            assert r.json()["vision"]["effective_policy"]["max_per_hour"] == 1
            assert (
                await client.post("/api/notifications/experience", json=body)
            ).status_code == 409
            assert (
                await client.post(
                    "/api/notifications/calibration", json={"confirm": True, "action": "start"}
                )
            ).status_code == 409
            assert (
                await client.post("/api/notifications/clear-diagnostics", json={"confirm": False})
            ).status_code == 422
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server.app, client=("192.168.1.10", 5000)),
            base_url="http://127.0.0.1",
        ) as client:
            assert (
                await client.post("/api/notifications/experience", json=body)
            ).status_code == 403
            assert (await client.get("/api/notifications")).status_code == 403

    with patch("core.vision_assistant.time.time", return_value=1063.0):
        asyncio.run(run())
    dispatch.assert_not_awaited()


@pytest.mark.parametrize(
    "block", ["none", "peer", "wrong_owner", "off", "paused", "retention", "media"]
)
def test_sensor_messages_require_owner_local_optin_and_scalar_contract(runtime, monkeypatch, block):
    from core import websocket_server as server

    class Socket:
        def __init__(self, host):
            self.client = SimpleNamespace(host=host)
            self.send_json = AsyncMock()

    registry = SurfaceRegistry()
    socket = Socket("192.168.1.4" if block == "peer" else "127.0.0.1")
    registry.register_ready(socket, {"role": "face", "capabilities": {"tunes_vision": True}})
    if block != "wrong_owner":
        registry.request_camera(socket)
    monkeypatch.setattr(server, "_surface_registry", registry)
    monkeypatch.setattr(server, "_vision_assistant", runtime)
    monkeypatch.setattr(
        server.IRISConfig,
        "load",
        lambda: IRISConfig(
            vision_assistant_enabled=block != "off",
            vision_assistant_paused_until=1e12 if block == "paused" else 0,
            vision_assistant_log_retention_days=0 if block == "retention" else 7,
        ),
    )
    payload = {**SENSOR, **({"image": "private"} if block == "media" else {})}
    asyncio.run(
        server._handle_face_message(
            socket,
            {
                "type": "vision_sensor_diagnostics",
                "payload": payload,
            },
        )
    )
    assert (runtime._diagnostics.sensor is not None) == (block == "none")
