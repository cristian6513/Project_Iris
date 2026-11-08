"""API/runtime integration for the silent Vision V2 policy preview."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from core.config import IRISConfig
from core.surface_registry import SurfaceRegistry
from core.vision_assistant import VisionAssistantRuntime, VisionSignalError
from core.vision_policy import ACTIVITY_FLAGS, VisionActivity, VisionPolicySettings

FREE = VisionActivity(visible=True, **dict.fromkeys(ACTIVITY_FLAGS, False)).model_dump()


def test_activity_context_expires_is_bounded_and_rejects_media(tmp_path):
    runtime = VisionAssistantRuntime(tmp_path / "events.json")
    runtime.update_activity(FREE, surface_id="face", monotonic_now=0)
    runtime.update_activity({"typing": True}, surface_id="companion", monotonic_now=0)
    assert runtime._activity_context(("face", "companion"), 8).typing is True
    assert runtime._activity_context(("face",), 8.001).typing is None
    assert runtime._activity_context(("face",), -1).visible is None
    assert runtime._activity_context(("new",), 0).visible is None
    with pytest.raises(VisionSignalError):
        runtime.update_activity({"text": "private"}, surface_id="face", monotonic_now=0)
    assert runtime._activity_context(("face",), 0).visible is True
    for index in range(50):
        runtime.update_activity(FREE, surface_id=str(index), monotonic_now=0)
    assert len(runtime._activities) == 16
    runtime.reset_observation()
    assert runtime._activities == {}
    assert not runtime.state_path.exists()


def test_runtime_snapshot_agrees_with_signal_and_never_delivers(tmp_path):
    runtime = VisionAssistantRuntime(tmp_path / "events.json")
    settings = VisionPolicySettings(long_session_seconds=60)
    for tick in range(0, 67, 2):
        runtime.update_activity(FREE, surface_id="face", monotonic_now=tick)
        result = runtime.ingest(
            {"present": True, "motion": "still", "confidence": 0.9},
            surface_id="face",
            now=1000.0 + tick,
            monotonic_now=float(tick),
            enabled=True,
            paused_until=0,
            cooldown_minutes=25,
            retention_days=7,
            policy_settings=settings,
        )
    assert result["v2"]["decision"]["reason"] == "availability_required"
    snapshot = runtime.snapshot(
        enabled=True,
        paused_until=0,
        camera={"active": True},
        now=1066.0,
        monotonic_now=66.0,
        policy_settings=settings,
        activity_surfaces=("face",),
    )
    assert snapshot["v2"]["decision"] == result["v2"]["decision"]
    assert snapshot["v2"]["budget"]["used_last_hour"] == 0
    assert result["event"] is None
    assert runtime._policy.checkpoint(wall=1066.0, tick=66.0)["receipts"] == []
    # A second local surface can suppress without owning the camera.
    runtime.update_activity({"typing": True}, surface_id="companion", monotonic_now=66)
    blocked = runtime.snapshot(
        enabled=True,
        paused_until=0,
        camera={"active": True},
        now=1066.0,
        monotonic_now=66.0,
        activity_surfaces=("face", "companion"),
    )
    assert blocked["v2"]["decision"]["reason"] == "activity_typing"
    assert not runtime._policy.confirm_delivery(
        result["v2"]["candidate"]["candidate_id"],
        wall=1066.0,
        tick=66.0,
    )
    state = json.loads(runtime.state_path.read_text(encoding="utf-8"))
    assert state["policy"]["schema"] == "iris.vision-policy-state/v1"
    assert "activities" not in state
    assert "evaluations" not in state


def test_policy_settings_round_trip_and_old_config_remains_off(tmp_path):
    settings = VisionPolicySettings(max_per_hour=1)
    cfg = IRISConfig(vision_assistant_policy=settings)
    with patch("core.config.get_iris_home", return_value=tmp_path):
        cfg.save()
    saved = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert saved["vision_assistant_policy"]["max_per_hour"] == 1
    assert IRISConfig(**saved).vision_assistant_policy == settings
    assert IRISConfig().vision_assistant_enabled is False
    assert IRISConfig().vision_assistant_mode == "silent"


def test_settings_api_partial_update_preserves_other_rules_and_opt_in(tmp_path, monkeypatch):
    from core import websocket_server as server

    cfg = IRISConfig(
        vision_assistant_policy=VisionPolicySettings.model_validate(
            {
                "max_per_hour": 1,
                "kind_cooldown_minutes": {"long_session": 80, "low_head_motion": 90},
            }
        )
    )
    monkeypatch.setattr(server.app.state, "agent", None, raising=False)
    with (
        patch.object(server, "_vision_assistant", VisionAssistantRuntime(tmp_path / "events.json")),
        patch.object(server.IRISConfig, "load", return_value=cfg),
        patch.object(server.IRISConfig, "save") as save,
        patch.object(server, "broadcast_to_face", new=AsyncMock()),
    ):
        response = asyncio.run(
            server.save_vision_assistant_settings(
                {
                    "policy": {"kind_cooldown_minutes": {"long_session": 70}},
                }
            )
        )
    body = json.loads(response.body)
    assert body["policy"]["kind_cooldown_minutes"] == {
        "returned": 45,
        "long_session": 70,
        "low_head_motion": 90,
    }
    assert body["policy"]["max_per_hour"] == 1
    assert body["enabled"] is False
    assert body["mode"] == "silent"
    assert body["v2"]["decision"]["delivery"] == "none"
    save.assert_called_once()


@pytest.mark.parametrize(
    "policy",
    [
        None,
        [],
        {"max_per_hour": True},
        {"max_per_hour": 7},
        {"kind_cooldown_minutes": None},
        {"image": "private"},
    ],
)
def test_invalid_policy_never_saves_or_leaks_payload(policy):
    from core import websocket_server as server

    with (
        patch.object(server.IRISConfig, "load", return_value=IRISConfig()),
        patch.object(server.IRISConfig, "save") as save,
    ):
        with pytest.raises(HTTPException) as error:
            asyncio.run(server.save_vision_assistant_settings({"policy": policy}))
    assert error.value.status_code == 422
    assert "private" not in error.value.detail
    save.assert_not_called()


class Socket:
    def __init__(self, host="127.0.0.1"):
        self.client = SimpleNamespace(host=host)
        self.messages = []

    async def send_json(self, message):
        self.messages.append(message)


@pytest.mark.parametrize(
    "host,role,registered,enabled,paused,expected",
    [
        ("127.0.0.1", "face", True, True, False, "vision_activity_ack"),
        ("::1", "desktop_companion", True, True, False, "vision_activity_ack"),
        ("192.168.1.9", "face", True, True, False, "protocol_error"),
        ("127.0.0.1", "face", False, True, False, "protocol_error"),
        ("127.0.0.1", "observer", True, True, False, "protocol_error"),
        ("127.0.0.1", "face", True, False, False, "vision_activity_ack"),
        ("127.0.0.1", "face", True, True, True, "vision_activity_ack"),
    ],
)
def test_activity_websocket_is_registered_local_opt_in_only(
    tmp_path,
    host,
    role,
    registered,
    enabled,
    paused,
    expected,
):
    from core import websocket_server as server

    registry = SurfaceRegistry()
    socket = Socket(host)
    if registered:
        registry.register_ready(socket, {"surface_id": "test", "role": role})
    runtime = VisionAssistantRuntime(tmp_path / "events.json")
    cfg = IRISConfig(
        vision_assistant_enabled=enabled,
        vision_assistant_paused_until=1e12 if paused else 0,
    )
    with (
        patch.object(server, "_surface_registry", registry),
        patch.object(server, "_vision_assistant", runtime),
        patch.object(server.IRISConfig, "load", return_value=cfg),
        patch.object(server, "broadcast_to_face", new=AsyncMock()) as broadcast,
    ):
        asyncio.run(
            server._handle_face_message(socket, {"type": "vision_activity", "payload": FREE})
        )
    assert socket.messages[-1]["type"] == expected
    accepted = expected == "vision_activity_ack" and enabled and not paused
    assert bool(runtime._activities) is accepted
    if expected == "vision_activity_ack":
        assert socket.messages[-1]["payload"]["accepted"] is accepted
    broadcast.assert_not_awaited()


def test_surface_scope_excludes_lan_hidden_and_observers_but_includes_camera_owner():
    from core import websocket_server as server

    registry = SurfaceRegistry()
    for name, host, role, visible in (
        ("visible", "127.0.0.1", "face", True),
        ("remote", "192.168.1.9", "face", True),
        ("hidden", "127.0.0.1", "face", False),
        ("observer", "127.0.0.1", "observer", True),
        ("owner", "::1", "desktop_companion", True),
    ):
        socket = Socket(host)
        registry.register_ready(
            socket,
            {
                "surface_id": name,
                "role": role,
                "visible": visible,
                "capabilities": {"captures_camera": True},
            },
        )
        if name == "owner":
            registry.request_camera(socket, purpose="vision_assistant")
    with patch.object(server, "_surface_registry", registry):
        assert set(server._vision_activity_surfaces()) == {"visible", "owner"}


def test_busy_uses_real_agent_locks_and_task_state(monkeypatch):
    from core import websocket_server as server

    conversation = asyncio.Lock()
    speech = asyncio.Lock()
    agent = SimpleNamespace(
        _conversation_lock=conversation, _speak_lock=speech, _current_turn_task=None
    )
    monkeypatch.setattr(server.app.state, "agent", agent, raising=False)
    assert server._vision_backend_busy() is False
    for lock in (conversation, speech):
        asyncio.run(lock.acquire())
        assert server._vision_backend_busy() is True
        lock.release()
    agent._current_turn_task = SimpleNamespace(done=lambda: False)
    assert server._vision_backend_busy() is True
    agent._current_turn_task = SimpleNamespace(done=lambda: True)
    assert server._vision_backend_busy() is False
