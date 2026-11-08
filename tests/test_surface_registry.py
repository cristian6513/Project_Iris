"""Focused tests for multi-surface audio and camera ownership."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

from core.surface_registry import SurfaceRegistry
from core.websocket_server import _handle_face_message, app, send_tts_audio


class Clock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value

    def tick(self, seconds: float = 1.0) -> None:
        self.value += seconds


class FakeSocket:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.messages: list[dict] = []

    async def send_text(self, message: str) -> None:
        if self.fail:
            raise ConnectionError("disconnected")
        self.messages.append(json.loads(message))

    async def send_json(self, message: dict) -> None:
        if self.fail:
            raise ConnectionError("disconnected")
        self.messages.append(message)


def _ready(registry: SurfaceRegistry, client: object, role: str, **state):
    return registry.register_ready(client, {
        "role": role,
        "surface_id": state.pop("surface_id", role),
        "visible": state.pop("visible", True),
        "focused": state.pop("focused", False),
        "capabilities": state.pop("capabilities", {}),
        **state,
    })


def test_legacy_connection_preserves_the_existing_face_capabilities() -> None:
    registry = SurfaceRegistry()
    client = object()

    record = registry.connect(client)

    assert record.role == "legacy_face"
    assert record.supports("renders_avatar") is True
    assert record.supports("plays_tts") is True
    assert registry.audio_owner() is client


def test_face_is_idle_default_but_recent_interaction_moves_audio_owner() -> None:
    clock = Clock()
    registry = SurfaceRegistry(clock=clock)
    face = object()
    companion = object()
    _ready(registry, face, "face")
    clock.tick()
    _ready(registry, companion, "desktop_companion")

    # Connection order alone must not steal sound from the established Face.
    assert registry.audio_owner() is face

    clock.tick()
    registry.mark_interaction(companion)
    assert registry.audio_owner() is companion

    clock.tick()
    registry.mark_interaction(face)
    assert registry.audio_owner() is face


def test_hidden_face_yields_audio_to_visible_companion() -> None:
    registry = SurfaceRegistry()
    face = object()
    companion = object()
    _ready(registry, face, "face", visible=False)
    _ready(registry, companion, "desktop_companion", visible=True)

    assert registry.audio_owner() is companion


def test_focused_visible_face_reclaims_audio_from_an_older_interacted_tab() -> None:
    """A stale browser tab must not make the currently focused IRIS silent."""
    clock = Clock()
    registry = SurfaceRegistry(clock=clock)
    old_tab = object()
    active_tab = object()
    _ready(registry, old_tab, "face", visible=True, focused=False, surface_id="old")
    registry.mark_interaction(old_tab)
    clock.tick()
    _ready(registry, active_tab, "face", visible=True, focused=True, surface_id="active")

    assert registry.audio_owner() is active_tab


def test_non_audio_observer_never_blocks_server_fallback() -> None:
    registry = SurfaceRegistry()
    observer = object()
    _ready(registry, observer, "observer")

    assert registry.audio_capable_count() == 0
    assert registry.audio_owner() is None


def test_capabilities_accept_only_known_boolean_fields() -> None:
    registry = SurfaceRegistry()
    client = object()
    record = _ready(registry, client, "desktop_companion", capabilities={
        "plays_tts": False,
        "captures_camera": "yes",
        "arbitrary_permission": True,
    })

    assert record.capabilities == {
        "renders_avatar": True,
        "plays_tts": False,
        "captures_camera": True,
        "accepts_chat": True,
        "controls_local_player": False,
    }
    assert "arbitrary_permission" not in record.capabilities


def test_player_capability_uses_one_focused_owner() -> None:
    registry = SurfaceRegistry()
    background = object()
    focused = object()
    _ready(
        registry,
        background,
        "face",
        visible=True,
        focused=False,
        capabilities={"controls_local_player": True},
    )
    _ready(
        registry,
        focused,
        "face",
        visible=True,
        focused=True,
        capabilities={"controls_local_player": True},
    )

    assert registry.capability_owner("controls_local_player") is focused


def test_duplicate_and_unsafe_surface_ids_are_sanitized_and_unique() -> None:
    registry = SurfaceRegistry()
    first = object()
    second = object()
    one = _ready(registry, first, "face", surface_id=" IRIS face / main ")
    two = _ready(registry, second, "face", surface_id=" IRIS face / main ")

    assert one.surface_id == "IRIS-face-main"
    assert two.surface_id.startswith("IRIS-face-main-")
    assert one.surface_id != two.surface_id


def test_camera_lease_is_exclusive_and_disconnect_releases_it() -> None:
    registry = SurfaceRegistry()
    face = object()
    companion = object()
    _ready(registry, face, "face")
    _ready(registry, companion, "desktop_companion")

    first = registry.request_camera(face, purpose="mocap")
    assert first.granted is True
    assert first.previous_owner is None

    takeover = registry.request_camera(companion, purpose="emotion")
    assert takeover.granted is True
    assert takeover.previous_owner is face
    assert registry.camera_snapshot()["surface_id"] == "desktop_companion"

    registry.disconnect(companion)
    assert registry.camera_snapshot() == {
        "active": False,
        "surface_id": None,
        "purpose": "",
    }


def test_surface_without_camera_capability_cannot_take_the_lease() -> None:
    registry = SurfaceRegistry()
    observer = object()
    _ready(registry, observer, "observer")

    decision = registry.request_camera(observer)

    assert decision.granted is False
    assert decision.reason == "surface-does-not-capture-camera"
    assert registry.camera_owner() is None


def test_only_camera_lease_owner_can_publish_derived_emotion() -> None:
    registry = SurfaceRegistry()
    face = FakeSocket()
    companion = FakeSocket()
    _ready(registry, face, "face")
    _ready(registry, companion, "desktop_companion")
    payload = {
        "type": "user_emotion",
        "payload": {"label": "happy", "confidence": 0.8, "intensity": 0.5},
    }

    with (
        patch("core.websocket_server._surface_registry", registry),
        patch("core.websocket_server._capability_store.evaluate", return_value={
            "capability": "vision.context",
            "mode": "allow",
            "trigger": "automatic",
            "verdict": "allow",
            "requires_confirmation": False,
            "sensor_activation": False,
            "bypasses_hermes_approvals": False,
        }),
    ):
        asyncio.run(_handle_face_message(companion, payload))
        assert companion.messages[-1]["type"] == "protocol_error"

        registry.request_camera(face, purpose="mocap_and_emotion")
        asyncio.run(_handle_face_message(face, payload))
        assert app.state.user_emotion["label"] == "happy"
        asyncio.run(_handle_face_message(face, {
            "type": "camera_lease_release",
            "payload": {},
        }))

    assert app.state.user_emotion is None


def test_tts_bytes_go_to_one_owner_while_other_avatar_gets_only_visemes() -> None:
    registry = SurfaceRegistry()
    face = FakeSocket()
    companion = FakeSocket()
    registry.register_ready(face, {"role": "face", "surface_id": "face"})
    registry.register_ready(companion, {
        "role": "desktop_companion",
        "surface_id": "companion",
    })
    registry.mark_interaction(companion)

    with (
        patch("core.websocket_server._surface_registry", registry),
        patch("core.websocket_server._connected_clients", {face, companion}),
    ):
        delivered = asyncio.run(send_tts_audio({
            "audio": "YWJj",
            "mime": "audio/mpeg",
            "visemes": [{"time": 0.0, "text": "hola"}],
        }))

    assert delivered is True
    assert [message["type"] for message in companion.messages] == ["tts_audio"]
    assert [message["type"] for message in face.messages] == ["lip_sync"]
    assert "audio" not in face.messages[0]["payload"]


def test_tts_delivery_reselects_after_the_first_owner_disconnects() -> None:
    registry = SurfaceRegistry()
    face = FakeSocket()
    companion = FakeSocket(fail=True)
    registry.register_ready(face, {"role": "face", "surface_id": "face"})
    registry.register_ready(companion, {
        "role": "desktop_companion",
        "surface_id": "companion",
    })
    registry.mark_interaction(companion)

    with (
        patch("core.websocket_server._surface_registry", registry),
        patch("core.websocket_server._connected_clients", {face, companion}),
    ):
        delivered = asyncio.run(send_tts_audio({
            "audio": "YWJj",
            "mime": "audio/mpeg",
            "visemes": [],
        }))

    assert delivered is True
    assert [message["type"] for message in face.messages] == ["tts_audio"]
    assert registry.get(companion) is None


def test_surface_ready_handler_registers_and_acknowledges_capabilities() -> None:
    registry = SurfaceRegistry()
    socket = FakeSocket()
    registry.connect(socket)

    with patch("core.websocket_server._surface_registry", registry):
        asyncio.run(_handle_face_message(socket, {
            "type": "surface_ready",
            "payload": {
                "role": "desktop_companion",
                "surface_id": "iris-companion",
                "visible": True,
                "capabilities": {
                    "renders_avatar": True,
                    "plays_tts": False,
                    "captures_camera": False,
                    "accepts_chat": True,
                },
            },
        }))

    record = registry.get(socket)
    assert record is not None
    assert record.role == "desktop_companion"
    assert record.supports("plays_tts") is False
    assert socket.messages[-1]["type"] == "surface_registered"
    assert socket.messages[-1]["payload"]["surface_id"] == "iris-companion"


def test_remote_surface_with_invalid_capabilities_cannot_control_player() -> None:
    registry = SurfaceRegistry()
    socket = FakeSocket()
    socket.client = type("Peer", (), {"host": "192.168.1.25"})()
    registry.connect(socket)

    with patch("core.websocket_server._surface_registry", registry):
        asyncio.run(_handle_face_message(socket, {
            "type": "surface_ready",
            "payload": {
                "role": "face",
                "surface_id": "remote-browser",
                "visible": True,
                "capabilities": "controls_local_player",
            },
        }))

    record = registry.get(socket)
    assert record is not None
    assert record.supports("controls_local_player") is False
    assert socket.messages[-1]["type"] == "surface_registered"

    # Registration is not the only capability boundary: a remote page must
    # not be able to elevate itself with a later state update either.
    with patch("core.websocket_server._surface_registry", registry):
        asyncio.run(_handle_face_message(socket, {
            "type": "surface_state",
            "payload": {"capabilities": {"controls_local_player": True}},
        }))

    assert registry.get(socket).supports("controls_local_player") is False
    assert socket.messages[-1]["type"] == "surface_state_ack"
