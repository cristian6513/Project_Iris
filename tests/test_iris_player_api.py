"""API and structured-control contracts for IRIS Player."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from core.capability_permissions import CapabilityPermissionStore
from core.iris_player import IrisPlayerLibrary
from core.surface_registry import SurfaceRegistry
from core.websocket_server import (
    PlayerActionRequest,
    _handle_face_message,
    _player_public_snapshot,
    _validated_player_action,
    _validated_player_action_result,
    player_execute_action,
)


def _request(host: str = "127.0.0.1") -> Request:
    return Request({
        "type": "http",
        "method": "POST",
        "path": "/api/player/actions",
        "headers": [],
        "client": (host, 54000),
        "server": ("127.0.0.1", 8765),
        "scheme": "http",
        "query_string": b"",
    })


def test_public_snapshot_never_exposes_library_paths(tmp_path: Path) -> None:
    music = tmp_path / "private" / "music"
    music.mkdir(parents=True)
    (music / "Secret_Name.mp3").write_bytes(b"audio")
    library = IrisPlayerLibrary(tmp_path / "state")
    library.configure_library(music)
    library.refresh()

    payload = _player_public_snapshot(library)
    serialized = json.dumps(payload)

    assert payload["folder_name"] == "music"
    assert payload["tracks"][0]["stream_url"].startswith("/api/player/tracks/trk_")
    assert "relative_path" not in payload["tracks"][0]
    assert str(tmp_path) not in serialized


def test_installed_skill_example_uses_the_canonical_nested_args_contract() -> None:
    fixture = Path(__file__).parent / "fixtures" / "iris_player_play_by_query_intent.json"
    payload = PlayerActionRequest.model_validate_json(fixture.read_text(encoding="utf-8"))

    assert _validated_player_action(payload) == {
        "schema": "iris.app-intent/v1",
        "app": "player",
        "action": "play_by_query",
        "args": {"query": "Believer"},
    }


@pytest.mark.parametrize(
    ("action", "args"),
    [
        ("set_volume", {"percent": 101}),
        ("pause", {"path": "C:/Music"}),
        ("play", {"track_id": "../song.mp3"}),
        ("play_by_query", {"query": ""}),
        ("unknown", {}),
    ],
)
def test_action_validator_rejects_unbounded_or_unknown_input(
    action: str,
    args: dict[str, object],
) -> None:
    with pytest.raises(HTTPException) as exc:
        _validated_player_action(PlayerActionRequest(action=action, args=args))

    assert exc.value.status_code == 422


def test_result_projection_drops_browser_supplied_private_fields() -> None:
    result = _validated_player_action_result({
        "schema": "iris.app-result/v1",
        "request_id": "forged",
        "status": "ok",
        "message": "done",
        "data": {
            "track_id": "trk_abc",
            "title": "Song",
            "playback_state": "playing",
            "path": "C:/private/song.mp3",
            "token": "secret",
        },
    }, "server-id")

    assert result["request_id"] == "server-id"
    assert result["data"] == {
        "track_id": "trk_abc",
        "title": "Song",
        "playback_state": "playing",
    }


def test_result_projection_rejects_unknown_playback_state() -> None:
    result = _validated_player_action_result({
        "schema": "iris.app-result/v1",
        "status": "ok",
        "data": {"playback_state": "maybe"},
    }, "server-id")

    assert "playback_state" not in result["data"]


class _PlayerSocket:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []
        self.client = type("Peer", (), {"host": "127.0.0.1"})()

    async def send_text(self, message: str) -> None:
        outbound = json.loads(message)
        self.messages.append(outbound)
        intent = outbound["payload"]
        await _handle_face_message(self, {
            "type": "iris_player_result",
            "payload": {
                "schema": "iris.app-result/v1",
                "request_id": intent["request_id"],
                "status": "ok",
                "message": "Pausado",
                "data": {"selection_origin": "manual"},
            },
        })

    async def send_json(self, message: dict[str, object]) -> None:
        self.messages.append(message)


def test_action_is_delivered_to_exactly_one_capable_surface(tmp_path: Path) -> None:
    registry = SurfaceRegistry()
    store = CapabilityPermissionStore(tmp_path / "permissions.json")
    owner = _PlayerSocket()
    registry.connect(owner)
    registry.register_ready(owner, {
        "role": "face",
        "surface_id": "main",
        "visible": True,
        "focused": True,
        "capabilities": {"controls_local_player": True},
    })

    async def run() -> dict[str, object]:
        with (
            patch("core.websocket_server._surface_registry", registry),
            patch("core.websocket_server._capability_store", store),
        ):
            response = await player_execute_action(
                PlayerActionRequest(action="pause", args={}),
                _request(),
            )
        return json.loads(response.body)

    result = asyncio.run(run())

    assert result["status"] == "ok"
    assert len(owner.messages) == 1
    assert owner.messages[0]["type"] == "iris_player_intent"
    assert owner.messages[0]["payload"]["permission"] == {
        "capability": "player.control",
        "mode": "ask",
        "requires_confirmation": True,
    }


def test_action_reports_unavailable_without_open_player_surface(tmp_path: Path) -> None:
    store = CapabilityPermissionStore(tmp_path / "permissions.json")
    async def run() -> dict[str, object]:
        with (
            patch("core.websocket_server._surface_registry", SurfaceRegistry()),
            patch("core.websocket_server._capability_store", store),
        ):
            response = await player_execute_action(
                PlayerActionRequest(action="next", args={}),
                _request(),
            )
        return json.loads(response.body)

    result = asyncio.run(run())

    assert result["status"] == "unavailable"
    assert "Abre IRIS OS" in result["message"]


def test_action_is_blocked_by_capability_center_before_reaching_a_surface(tmp_path: Path) -> None:
    store = CapabilityPermissionStore(tmp_path / "permissions.json")
    store.set_mode("player.control", "deny")

    async def run() -> dict[str, object]:
        with (
            patch("core.websocket_server._capability_store", store),
            patch("core.websocket_server._surface_registry", SurfaceRegistry()),
        ):
            response = await player_execute_action(
                PlayerActionRequest(action="pause", args={}),
                _request(),
            )
        return json.loads(response.body)

    result = asyncio.run(run())

    assert result["status"] == "denied"
    assert "Capability Center" in result["message"]
    assert store.snapshot()["audit"][0]["kind"] == "action_blocked"


def test_audit_write_failure_does_not_override_player_policy_result(tmp_path: Path) -> None:
    store = CapabilityPermissionStore(tmp_path / "permissions.json")
    store.set_mode("player.control", "deny")

    async def run() -> dict[str, object]:
        with (
            patch("core.websocket_server._capability_store", store),
            patch.object(store, "record_event", side_effect=OSError("read-only ledger")),
        ):
            response = await player_execute_action(
                PlayerActionRequest(action="pause", args={}),
                _request(),
            )
        return json.loads(response.body)

    result = asyncio.run(run())

    assert result["status"] == "denied"
    assert "Capability Center" in result["message"]


def test_action_endpoint_is_host_only() -> None:
    async def run() -> None:
        with patch("core.websocket_server._surface_registry", SurfaceRegistry()):
            await player_execute_action(
                PlayerActionRequest(action="pause", args={}),
                _request("192.168.1.20"),
            )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(run())

    assert exc.value.status_code == 403
