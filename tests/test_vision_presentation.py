"""A return has one local presentation owner, without changing camera or audio."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.config import IRISConfig
from core.surface_registry import SurfaceRegistry


class Socket:
    def __init__(self, host="127.0.0.1"):
        self.client = SimpleNamespace(host=host)
        self.send_json = AsyncMock()


def register(registry, role="face", *, visible=True, focused=False, host="127.0.0.1", modern=True):
    ws = Socket(host)
    registry.register_ready(
        ws,
        {
            "role": role,
            "visible": visible,
            "focused": focused,
            "capabilities": {"renders_avatar": True, "presents_vision": modern},
        },
    )
    return ws


def test_owner_is_visible_explicit_local_and_does_not_change_resources():
    registry = SurfaceRegistry()
    face = register(registry)
    companion = register(registry, "desktop_companion", focused=True)
    hidden = register(registry, focused=True, visible=False)
    legacy = register(registry, "legacy_face", focused=True)
    remote = register(registry, focused=True, host="192.168.1.2")
    registry.request_camera(face)
    audio = registry.audio_owner()
    local = [face, companion, hidden, legacy]
    assert registry.vision_owner(local_clients=local) is companion
    assert registry.camera_owner() is face
    assert registry.audio_owner() is audio
    registry.update_state(companion, {"visible": False})
    assert registry.vision_owner(local_clients=local) is face
    registry.update_state(face, {"capabilities": {"presents_vision": False}})
    assert registry.vision_owner(local_clients=local) is None
    assert registry.get(remote).visible


@pytest.mark.parametrize(
    "blocked", ["none", "hidden", "legacy", "remote", "paused", "busy", "failed"]
)
def test_server_targets_once_and_never_reroutes(monkeypatch, blocked):
    from core import websocket_server as server

    registry = SurfaceRegistry()
    companion = register(
        registry,
        "desktop_companion",
        focused=True,
        visible=blocked != "hidden",
        modern=blocked != "legacy",
        host="192.168.1.8" if blocked == "remote" else "127.0.0.1",
    )
    observer = register(registry, "observer")
    runtime = SimpleNamespace(
        return_presentation=lambda *a, **k: {
            "id": "a" * 32,
            "kind": "returned",
            "animation": "wave",
            "delivery": "inbox",
        }
    )
    monkeypatch.setattr(server, "_surface_registry", registry)
    monkeypatch.setattr(server, "_vision_assistant", runtime)
    monkeypatch.setattr(server, "_vision_backend_busy", lambda: blocked == "busy")
    monkeypatch.setattr(
        server.IRISConfig,
        "load",
        lambda: IRISConfig(
            vision_assistant_enabled=True,
            vision_assistant_paused_until=1e12 if blocked == "paused" else 0,
        ),
    )
    broadcast = AsyncMock()
    monkeypatch.setattr(server, "broadcast_to_face", broadcast)
    if blocked == "failed":
        companion.send_json.side_effect = OSError("disconnected")
    asyncio.run(server._present_vision_notification({"id": "a" * 32, "kind": "returned"}))
    assert companion.send_json.call_count == int(blocked in {"none", "failed"})
    observer.send_json.assert_not_called()
    broadcast.assert_not_called()
    if blocked == "none":
        message = companion.send_json.call_args.args[0]
        assert message["type"] == "vision_assistant_presentation"
        assert message["payload"]["surface_id"] == registry.get(companion).surface_id


def test_peer_cannot_advertise_local_vision_presentation():
    from core.websocket_server import _surface_payload_for_peer

    payload = {"role": "face", "capabilities": {"presents_vision": True}}
    assert (
        _surface_payload_for_peer(payload, "192.168.1.4")["capabilities"]["presents_vision"]
        is False
    )
    assert payload["capabilities"]["presents_vision"] is True


def test_camera_renewal_keeps_session_but_handoff_resets_transient_observation(monkeypatch):
    from unittest.mock import Mock

    from core import websocket_server as server

    registry = SurfaceRegistry()
    face = register(registry)
    companion = register(registry, "desktop_companion")
    registry.request_camera(face)
    reset = Mock()
    monkeypatch.setattr(server, "_surface_registry", registry)
    monkeypatch.setattr(server, "_vision_assistant", SimpleNamespace(reset_observation=reset))
    monkeypatch.setattr(
        server,
        "_capability_store",
        SimpleNamespace(
            evaluate=lambda *a, **k: {"verdict": "allow"},
        ),
    )
    monkeypatch.setattr(server, "broadcast_to_face", AsyncMock())
    monkeypatch.setattr(server.app.state, "user_emotion", None, raising=False)

    async def run():
        request = {"type": "camera_lease_request", "payload": {"confirmed": True}}
        await server._handle_face_message(face, request)
        reset.assert_not_called()
        await server._handle_face_message(companion, request)

    asyncio.run(run())
    reset.assert_called_once_with("surface_changed")
    assert registry.camera_owner() is companion
    assert any(
        call.args[0]["type"] == "camera_lease_revoked" for call in face.send_json.call_args_list
    )
