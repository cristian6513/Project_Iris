"""Contract tests for the Hermes-owned Continuity Center data path."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from core.hermes_continuity import (
    continuity_snapshot,
    normalize_messages,
    normalize_sessions,
    parse_profile_list,
)
from core.websocket_server import (
    ContinuityProfileActivationRequest,
    ContinuityProfileCreateRequest,
    ContinuitySessionUpdateRequest,
    ContinuitySessionResumeRequest,
    ContinuitySessionCreateRequest,
    ContinuitySessionForkRequest,
    _continuity_snapshot,
    hermes_continuity_activate_profile,
    hermes_continuity_create_profile,
    hermes_continuity_resume_session,
    hermes_continuity_create_session,
    hermes_continuity_fork_session,
    hermes_continuity_update_session,
    app,
)

ROOT = Path(__file__).resolve().parents[1]


def _body(response) -> dict:
    return json.loads(response.body.decode("utf-8"))


def test_profile_parser_accepts_current_table_and_documented_legacy_marker() -> None:
    output = """
 Profile          Model                        Gateway      Alias        Distribution
 ───────────────    ───────────────────────────    ───────────    ───────────
 ◆default         openai/gpt-5.6-luna          running      —            —
  work            nous/hermes-4                stopped      h-work       studio@2.0
"""
    rows = parse_profile_list(output)

    assert [row["name"] for row in rows] == ["default", "work"]
    assert rows[0]["active"] is True
    assert rows[0]["provider"] == "openai"
    assert rows[1]["distribution"] == "studio@2.0"

    assert parse_profile_list("  default\n* work\n") == [
        {
            "name": "default",
            "is_default": True,
            "active": False,
            "model": "",
            "provider": "",
            "gateway": "unknown",
            "has_alias": False,
            "distribution": "",
        },
        {
            "name": "work",
            "is_default": False,
            "active": True,
            "model": "",
            "provider": "",
            "gateway": "unknown",
            "has_alias": False,
            "distribution": "",
        },
    ]


def test_session_projection_keeps_only_client_safe_metadata() -> None:
    payload = {
        "object": "list",
        "data": [
            {
                "id": "api_session-01",
                "title": "Auditar IRIS",
                "preview": "Revisando continuidad",
                "profile": "work",
                "model": "openai/gpt-5.6",
                "source": "api_server",
                "message_count": "7",
                "input_tokens": 120,
                "output_tokens": 45,
                "last_active": "2026-08-28T12:00:00Z",
                "cwd": "C:\\Users\\private\\Projects\\IRIS OS",
                "pinned": 1,
                "archived": 0,
                "system_prompt": "secret personality",
            },
            {"id": "../../unsafe", "cwd": "C:\\secret"},
        ],
    }

    sessions = normalize_sessions(
        payload,
        default_profile="work",
        current_session_id="api_session-01",
    )

    assert len(sessions) == 1
    session = sessions[0]
    assert session["workspace_label"] == "IRIS OS"
    assert session["workspace_id"].startswith("ws_")
    assert session["last_active"] > 0
    assert session["is_current"] is True
    assert session["pinned"] is True
    assert "cwd" not in session
    assert "system_prompt" not in session
    assert "private" not in json.dumps(session)


def test_message_projection_keeps_only_visible_conversation_text() -> None:
    messages = normalize_messages({
        "data": [
            {"role": "system", "content": "secret personality"},
            {"role": "user", "content": [
                {"type": "text", "text": "Mira esto"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,SECRET"}},
            ]},
            {"role": "assistant", "content": "Lo veo", "reasoning": "private chain"},
            {"role": "tool", "content": "API_KEY=secret"},
        ],
    })

    assert messages == [
        {
            "role": "user",
            "content": "Mira esto\n[Imagen adjunta]",
            "emotion": "neutral",
            "timestamp": 0.0,
        },
        {
            "role": "IRIS",
            "content": "Lo veo",
            "emotion": "neutral",
            "timestamp": 0.0,
        },
    ]
    assert "secret" not in json.dumps(messages).casefold()


def test_snapshot_degrades_without_opening_private_hermes_storage() -> None:
    partial = continuity_snapshot(
        profile_output="◆default  openai/gpt-5.6  running  —  —",
        session_payload=None,
        sessions_available=False,
        session_update_available=False,
        error="Gateway apagado",
    )
    offline = continuity_snapshot(
        profile_output="",
        session_payload=None,
        sessions_available=False,
        session_update_available=False,
    )

    assert partial["mode"] == "partial"
    assert partial["supports"]["resume_in_iris"] is False
    assert offline["mode"] == "offline"
    assert offline["success"] is False
    assert "no fueron modificados" in offline["notice"]


def test_snapshot_enables_resume_only_with_messages_and_authenticated_continuity() -> None:
    unavailable = continuity_snapshot(
        profile_output="◆default  openai/gpt-5.6  running  —  —",
        session_payload={"data": [{"id": "session-1"}]},
        sessions_available=True,
        session_update_available=True,
        session_messages_available=True,
        session_continuity_available=False,
    )
    available = continuity_snapshot(
        profile_output="◆default  openai/gpt-5.6  running  —  —",
        session_payload={"data": [{"id": "session-1"}]},
        sessions_available=True,
        session_update_available=True,
        session_messages_available=True,
        session_create_available=True,
        session_fork_available=True,
        session_continuity_available=True,
    )

    assert unavailable["supports"]["resume_in_iris"] is False
    assert available["supports"] == {
        "profiles": True,
        "profile_activation": True,
        "sessions": True,
        "session_update": True,
        "session_messages": True,
        "session_create": True,
        "session_fork": True,
        "workspace_metadata": False,
        "resume_in_iris": True,
    }


def test_sessions_follow_the_running_gateway_not_the_new_sticky_profile() -> None:
    snapshot = continuity_snapshot(
        profile_output=(
            "  default  gpt-5.6-luna  running  —  —\n"
            "◆work     hermes-4        stopped  —  —"
        ),
        session_payload={"data": [{"id": "session-1", "title": "Default chat"}]},
        sessions_available=True,
        session_update_available=True,
        gateway_model="gpt-5.6-luna",
    )

    assert snapshot["active_profile"] == "work"
    assert snapshot["gateway_profile"] == "default"
    assert snapshot["sessions"][0]["profile"] == "default"


def test_live_snapshot_hides_transport_errors_when_gateway_is_offline() -> None:
    profiles = {
        "ok": True,
        "stdout": "◆default  openai/gpt-5.6  stopped  —  —",
        "error": "",
    }
    capabilities = {
        "reachable": False,
        "capabilities_available": False,
        "capabilities": {},
        "error": "<urlopen error [WinError 10061] connection refused>",
    }
    with (
        patch("core.websocket_server._continuity_profile_list", return_value=profiles),
        patch("core.websocket_server._hermes_capabilities_snapshot", return_value=capabilities),
        patch("core.websocket_server._hermes_version", return_value="Hermes 0.20.1"),
    ):
        snapshot = asyncio.run(_continuity_snapshot())

    assert snapshot["mode"] == "partial"
    assert snapshot["error"] == ""
    assert "Inicia el Gateway" in snapshot["notice"]
    assert "urlopen" not in json.dumps(snapshot)


def test_remote_gateway_never_mixes_in_local_cli_profiles() -> None:
    capabilities = {
        "reachable": True,
        "capabilities_available": True,
        "capabilities": {
            "model": "remote-model",
            "endpoints": {
                "sessions": {"method": "GET", "path": "/api/sessions"},
                "session_update": {
                    "method": "PATCH",
                    "path": "/api/sessions/{session_id}",
                },
            },
        },
    }
    sessions = {
        "ok": True,
        "data": {"data": [{"id": "remote-1", "title": "Remote"}], "has_more": True},
    }
    with (
        patch("core.websocket_server._continuity_uses_local_profiles", return_value=False),
        patch("core.websocket_server._continuity_profile_list") as profile_list,
        patch("core.websocket_server._hermes_capabilities_snapshot", return_value=capabilities),
        patch("core.websocket_server._hermes_api_request", return_value=sessions),
        patch("core.websocket_server._hermes_version", return_value="Hermes remote"),
    ):
        snapshot = asyncio.run(_continuity_snapshot())

    profile_list.assert_not_called()
    assert snapshot["profile_scope"] == "remote"
    assert snapshot["profiles"] == []
    assert snapshot["sessions"][0]["profile"] == "remote"
    assert snapshot["has_more"] is True
    assert "perfiles del CLI local" in snapshot["notice"]


def test_profile_activation_delegates_to_official_cli_with_an_argv_list() -> None:
    with (
        patch("core.websocket_server._continuity_uses_local_profiles", return_value=True),
        patch("core.websocket_server._continuity_profile_rows", return_value=[{"name": "work"}]),
        patch("core.websocket_server._resolve_hermes_cli", return_value="hermes.exe"),
        patch(
            "core.websocket_server._run_hermes_cli_command",
            return_value={"ok": True, "stdout": "Switched", "stderr": ""},
        ) as run,
    ):
        response = asyncio.run(hermes_continuity_activate_profile(
            ContinuityProfileActivationRequest(name="work", confirm=True),
        ))

    assert _body(response)["requires_gateway_restart"] is True
    # The managed interpreter runs the CLI: no launcher path in the argv (qa9).
    run.assert_called_once_with(["profile", "use", "work"], timeout=60)


def test_profile_list_runs_the_cli_through_its_managed_interpreter() -> None:
    # qa9: the included CLI is hermes/iris_cli.py. Started directly, Windows
    # refuses it (WinError 193) and Continuity said Hermes was unreachable.
    from core import websocket_server

    managed = {"success": True, "returncode": 0, "stdout": "default  ...", "error": ""}
    with (
        patch("core.websocket_server._resolve_hermes_cli", return_value="C:/Iris OS/v/1/hermes/iris_cli.py"),
        patch("core.hermes_runtime.run_hermes_cli", return_value=managed) as run,
        patch("core.websocket_server.subprocess.run", side_effect=AssertionError("launcher started directly")),
    ):
        result = websocket_server._continuity_profile_list()

    assert result == {"ok": True, "stdout": "default  ...", "error": ""}
    run.assert_called_once_with(["profile", "list"], timeout=60)


def test_a_cli_that_cannot_start_is_a_partial_snapshot_not_a_server_error() -> None:
    from core import websocket_server

    failed = {"success": False, "returncode": None, "stdout": "",
              "error": "No se pudo iniciar Hermes: %1 no es una aplicación Win32 válida"}
    with patch("core.hermes_runtime.run_hermes_cli", return_value=failed):
        result = websocket_server._run_hermes_cli_command(["profile", "list"], timeout=60)

    assert result["ok"] is False and result["returncode"] is None
    assert "No se pudo iniciar Hermes" in result["stderr"]


def test_profile_creation_uses_hermes_clone_contract_without_copying_files() -> None:
    with (
        patch("core.websocket_server._continuity_uses_local_profiles", return_value=True),
        patch("core.websocket_server._continuity_profile_rows", return_value=[{"name": "default"}]),
        patch("core.websocket_server._resolve_hermes_cli", return_value="hermes.exe"),
        patch(
            "core.websocket_server._run_hermes_cli_command",
            return_value={"ok": True, "stdout": "Created", "stderr": ""},
        ) as run,
    ):
        response = asyncio.run(hermes_continuity_create_profile(
            ContinuityProfileCreateRequest(
                name="research",
                description="  Investiga   con fuentes  ",
                clone_mode="all",
                clone_from="default",
            ),
        ))

    assert _body(response)["needs_setup"] is False
    run.assert_called_once_with(
        [
            "profile",
            "create",
            "research",
            "--clone-from",
            "default",
            "--clone-all",
            "--description",
            "Investiga con fuentes",
        ],
        timeout=90,
    )


def test_remote_gateway_rejects_local_profile_mutation() -> None:
    with patch("core.websocket_server._continuity_uses_local_profiles", return_value=False):
        with pytest.raises(HTTPException) as raised:
            asyncio.run(hermes_continuity_activate_profile(
                ContinuityProfileActivationRequest(name="work", confirm=True),
            ))

    assert raised.value.status_code == 409
    assert "Gateway remoto" in raised.value.detail


def test_session_updates_require_the_advertised_hermes_endpoint() -> None:
    capabilities = {
        "capabilities": {
            "features": {"session_resources": True},
            "endpoints": {
                "sessions": {"method": "GET", "path": "/api/sessions"},
                "session_update": {"method": "PATCH", "path": "/api/sessions/{session_id}"},
            },
        },
    }
    hermes_response = {
        "ok": True,
        "status": 200,
        "data": {
            "object": "hermes.session",
            "session": {"id": "session-1", "title": "Nuevo título", "pinned": True},
        },
    }
    with (
        patch("core.websocket_server._hermes_capabilities_snapshot", return_value=capabilities),
        patch("core.websocket_server._hermes_api_request", return_value=hermes_response) as request,
    ):
        response = asyncio.run(hermes_continuity_update_session(
            "session-1",
            ContinuitySessionUpdateRequest(title="Nuevo título", pinned=True),
        ))

    assert _body(response)["session"]["title"] == "Nuevo título"
    request.assert_called_once_with(
        "PATCH",
        "/api/sessions/session-1",
        payload={"title": "Nuevo título", "pinned": True},
        timeout=12,
    )


def test_resume_switches_hermes_local_memory_and_visible_transcript_together() -> None:
    class FakeBridge:
        session_id = "previous-session"

        def resume_session(self, session_id: str) -> None:
            self.session_id = session_id

    class FakeMemory:
        def __init__(self) -> None:
            self.saved: list[tuple[str, str]] = []

        async def save_preference(self, key: str, value: str) -> None:
            self.saved.append((key, value))

    class FakeAgent:
        def __init__(self) -> None:
            self.bridge = FakeBridge()
            self.memory = FakeMemory()
            self._session_id = "previous-local"
            self._conversation_lock = asyncio.Lock()
            self._current_turn_task = None

    fake = FakeAgent()
    capabilities = {
        "capabilities": {
            "features": {
                "session_resources": True,
                "session_continuity_header": "X-Hermes-Session-Id",
            },
            "endpoints": {
                "sessions": {"method": "GET", "path": "/api/sessions"},
                "session_messages": {
                    "method": "GET",
                    "path": "/api/sessions/{session_id}/messages",
                },
            },
        },
    }

    def request(_method: str, path: str, **_kwargs):
        if path.endswith("/messages"):
            return {
                "ok": True,
                "data": {
                    "session_id": "session-1",
                    "data": [
                        {"role": "user", "content": "Hola"},
                        {"role": "assistant", "content": json.dumps({
                            "texto_hablado": "Bienvenido", "emocion": "feliz",
                            "animacion_virtual": "wave",
                        })},
                    ],
                },
            }
        return {
            "ok": True,
            "data": {"session": {"id": "session-1", "title": "Trabajo", "archived": False}},
        }

    previous = getattr(app.state, "agent", None)
    app.state.agent = fake
    try:
        with (
            patch("core.websocket_server._hermes_capabilities_snapshot", return_value=capabilities),
            patch("core.websocket_server._hermes_api_token", return_value="secret"),
            patch("core.websocket_server._hermes_api_request", side_effect=request),
            patch("core.websocket_server.broadcast_to_face", new_callable=AsyncMock) as broadcast,
        ):
            response = asyncio.run(hermes_continuity_resume_session(
                "session-1",
                ContinuitySessionResumeRequest(confirm=True),
            ))
    finally:
        app.state.agent = previous

    body = _body(response)
    assert fake.bridge.session_id == "session-1"
    assert fake._session_id == "session-1"
    assert fake.memory.saved == [("continuity.active_session_id", "session-1")]
    assert [row["content"] for row in body["messages"]] == ["Hola", "Bienvenido"]
    broadcast.assert_awaited_once()
    assert broadcast.await_args.args[0] == "chat_history_replace"
    assert broadcast.await_args.args[1]["messages"] == body["messages"]


def test_create_session_delegates_to_hermes_then_opens_it() -> None:
    with (
        patch(
            "core.websocket_server._continuity_require_session_capabilities",
            new_callable=AsyncMock,
        ) as require,
        patch(
            "core.websocket_server._hermes_api_request",
            return_value={
                "ok": True,
                "data": {"session": {"id": "api-new", "title": "Nueva idea"}},
            },
        ) as request,
        patch(
            "core.websocket_server._continuity_resume_existing_session",
            new_callable=AsyncMock,
            return_value={"success": True, "session_id": "api-new", "messages": []},
        ) as resume,
    ):
        response = asyncio.run(hermes_continuity_create_session(
            ContinuitySessionCreateRequest(title="  Nueva   idea ", confirm=True),
        ))

    require.assert_awaited_once_with(
        "session_create",
        "sessions",
        "session_messages",
        require_header=True,
    )
    request.assert_called_once_with(
        "POST",
        "/api/sessions",
        payload={"source": "api_server", "title": "Nueva idea"},
        timeout=15,
    )
    resume.assert_awaited_once_with("api-new", confirm=True)
    assert _body(response)["created"] is True


def test_fork_session_uses_hermes_lineage_and_opens_the_child() -> None:
    with (
        patch(
            "core.websocket_server._continuity_require_session_capabilities",
            new_callable=AsyncMock,
        ) as require,
        patch(
            "core.websocket_server._hermes_api_request",
            return_value={
                "ok": True,
                "data": {"session": {"id": "api-child", "parent_session_id": "api-parent"}},
            },
        ) as request,
        patch(
            "core.websocket_server._continuity_resume_existing_session",
            new_callable=AsyncMock,
            return_value={"success": True, "session_id": "api-child", "messages": []},
        ) as resume,
    ):
        response = asyncio.run(hermes_continuity_fork_session(
            "api-parent",
            ContinuitySessionForkRequest(title=" Alternativa ", confirm=True),
        ))

    require.assert_awaited_once_with(
        "session_fork",
        "sessions",
        "session_messages",
        require_header=True,
    )
    request.assert_called_once_with(
        "POST",
        "/api/sessions/api-parent/fork",
        payload={"title": "Alternativa"},
        timeout=20,
    )
    resume.assert_awaited_once_with("api-child", confirm=True)
    assert _body(response)["parent_session_id"] == "api-parent"


def test_create_and_fork_reject_an_active_turn_before_mutating_hermes() -> None:
    class BusyLock:
        @staticmethod
        def locked() -> bool:
            return True

    class BusyAgent:
        def __init__(self) -> None:
            self._conversation_lock = BusyLock()
            self._current_turn_task = None

    previous = getattr(app.state, "agent", None)
    app.state.agent = BusyAgent()
    try:
        with patch("core.websocket_server._hermes_api_request") as request:
            with pytest.raises(HTTPException) as create_error:
                asyncio.run(hermes_continuity_create_session(
                    ContinuitySessionCreateRequest(title="No crear", confirm=True),
                ))
            with pytest.raises(HTTPException) as fork_error:
                asyncio.run(hermes_continuity_fork_session(
                    "api-parent",
                    ContinuitySessionForkRequest(title="No ramificar", confirm=True),
                ))
    finally:
        app.state.agent = previous

    assert create_error.value.status_code == 409
    assert fork_error.value.status_code == 409
    request.assert_not_called()


def test_continuity_implementation_has_no_private_database_fallback() -> None:
    projection = (ROOT / "core" / "hermes_continuity.py").read_text(encoding="utf-8")
    server = (ROOT / "core" / "websocket_server.py").read_text(encoding="utf-8")

    assert "import sqlite" not in projection
    assert "SessionDB" not in projection
    assert '_run_hermes_cli_command(["profile", "list"], timeout=60)' in server
    assert '"/api/sessions"' in server
    assert 'f"/api/sessions/{urllib.parse.quote(clean_id, safe=\'\')}"' in server
