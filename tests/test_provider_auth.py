from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from wizard import provider_auth as auth
from wizard.app import app
from wizard.hermes_auth_worker import error_code
from wizard.hermes_config import has_hermes_auth_profile


class Process:
    def __init__(self, messages=()):
        self.stdout = asyncio.StreamReader()
        for message in messages:
            self.stdout.feed_data(json.dumps(message).encode() + b"\n")
        self.returncode = None
        self.stdin = None
        self.killed = False

    def kill(self):
        self.killed = True
        self.returncode = -1
        self.stdout.feed_eof()

    async def wait(self):
        return self.returncode


@pytest.fixture
def runtime(tmp_path):
    with (
        patch.object(auth, "get_hermes_repo", return_value=tmp_path),
        patch.object(auth, "resolve_hermes_python", return_value="hermes-python"),
        patch.object(auth, "hermes_subprocess_env", return_value={"SAFE": "1"}),
        patch.object(auth, "effective_auth_mode_for", return_value="oauth"),
    ):
        yield


@pytest.mark.asyncio
async def test_display_fields_only_and_credentials_must_be_persisted(runtime):
    process = Process(
        [
            {
                "status": "pending",
                "flow": "device_code",
                "url": "https://auth.openai.com/codex/device",
                "user_code": "FAKE-1234",
                "expires_in": 600,
                "access_token": "never-to-browser",
            },
            {"status": "approved", "refresh_token": "also-secret"},
        ]
    )
    manager = auth.ProviderAuth()
    with (
        patch.object(
            auth.asyncio, "create_subprocess_exec", AsyncMock(return_value=process)
        ) as spawn,
        patch.object(auth, "has_hermes_auth_profile", return_value=False),
    ):
        started = await manager.start("openai")
        session = manager.get(started["session_id"])
        await session.task
    assert session.public()["code"] == "not_persisted"
    assert session.status == "error"
    assert "secret" not in json.dumps(session.public())
    assert "never-to-browser" not in json.dumps(session.public())
    assert process.killed
    assert spawn.call_args.args[0] == "hermes-python"
    assert "openai-codex" in spawn.call_args.args
    assert "shell" not in spawn.call_args.kwargs


@pytest.mark.asyncio
async def test_duplicate_start_reuses_operation_and_switch_requires_cancel(runtime):
    manager, process = auth.ProviderAuth(), Process()
    with patch.object(
        auth.asyncio, "create_subprocess_exec", AsyncMock(return_value=process)
    ) as spawn:
        first = await manager.start("openai")
        await asyncio.sleep(0)
        second = await manager.start("openai")
        assert first["session_id"] == second["session_id"]
        with pytest.raises(HTTPException) as error:
            await manager.start("nous")
        assert error.value.status_code == 409
        stopped = await manager.cancel(first["session_id"])
    assert stopped["status"] == "cancelled"
    assert process.killed
    assert spawn.await_count == 1


@pytest.mark.asyncio
async def test_cancel_during_process_creation_cannot_orphan_worker(runtime):
    manager, process = auth.ProviderAuth(), Process()
    entered, release = asyncio.Event(), asyncio.Event()

    async def spawn(*args, **kwargs):
        entered.set()
        await release.wait()
        return process

    with patch.object(auth.asyncio, "create_subprocess_exec", spawn):
        started = await manager.start("nous")
        await entered.wait()
        cancellation = asyncio.create_task(manager.cancel(started["session_id"]))
        await asyncio.sleep(0)
        release.set()
        result = await cancellation
    assert result["status"] == "cancelled"
    assert process.killed


@pytest.mark.asyncio
async def test_expired_operation_stops_native_worker(runtime):
    manager = auth.ProviderAuth()
    session = auth.Login("id", "nous", "nous")
    process = Process()
    with (
        patch.object(auth.asyncio, "create_subprocess_exec", AsyncMock(return_value=process)),
        patch.object(process.stdout, "readline", AsyncMock(side_effect=TimeoutError)),
    ):
        await manager._run(session, "login")
    assert session.code == "expired"
    assert process.killed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url", ["javascript:alert(1)", "http://example.com", "https://user:password@example.com"]
)
async def test_unusable_auth_urls_never_reach_browser(runtime, url):
    process = Process([{"status": "pending", "url": url}])
    session = auth.Login("id", "nous", "nous")
    with patch.object(auth.asyncio, "create_subprocess_exec", AsyncMock(return_value=process)):
        await auth.ProviderAuth()._run(session, "login")
    assert session.status == "error"
    assert "url" not in session.public()
    assert process.killed


@pytest.mark.asyncio
async def test_external_provider_explains_limit_without_launching_console(runtime):
    manager = auth.ProviderAuth()
    with (
        patch.object(auth, "has_hermes_auth_profile", return_value=False),
        patch.object(auth.asyncio, "create_subprocess_exec", AsyncMock()) as spawn,
    ):
        result = await manager.start("anthropic")
    assert result["status"] == "external"
    assert "API key" in result["message"]
    spawn.assert_not_called()


@pytest.mark.asyncio
async def test_existing_external_session_can_be_used_without_login(runtime):
    with (
        patch.object(auth, "has_hermes_auth_profile", return_value=True),
        patch.object(auth.asyncio, "create_subprocess_exec", AsyncMock()) as spawn,
    ):
        result = await auth.ProviderAuth().start("anthropic", existing=True)
    assert result["status"] == "approved"
    spawn.assert_not_called()


@pytest.mark.parametrize(
    "method,path",
    [
        ("post", "/api/provider-auth/sessions"),
        ("get", "/api/provider-auth/sessions/anything"),
        ("delete", "/api/provider-auth/sessions/anything"),
    ],
)
def test_cross_site_requests_rejected_before_any_login(method, path):
    with TestClient(
        app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 50000),
    ) as client, patch.object(auth.provider_auth, "start", AsyncMock()) as start:
        options = {"headers": {"origin": "https://evil.example", "sec-fetch-site": "cross-site"}}
        if method == "post":
            options["json"] = {"provider": "openai"}
        response = getattr(client, method)(path, **options)
    assert response.status_code == 403
    start.assert_not_called()


def test_post_does_not_accept_commands_or_unknown_provider():
    with TestClient(app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 1234)) as client:
        response = client.post(
            "/api/provider-auth/sessions", json={"provider": "openai", "command": "anything"}
        )
        assert response.status_code == 422
        response = client.post("/api/provider-auth/sessions", json={"provider": "nonexistent"})
        assert response.status_code == 400


def test_old_get_login_does_not_spawn_process():
    with (
        TestClient(app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 50000)) as client,
        patch("wizard.app.asyncio.create_subprocess_exec", AsyncMock()) as spawn,
    ):
        response = client.get("/api/auth-provider-stream?provider=openai")
    assert response.status_code == 410
    spawn.assert_not_called()


@pytest.mark.parametrize("source,accepted", [("manual:openrouter_pkce", True), ("manual", False)])
def test_openrouter_browser_key_is_recognized_without_misclassifying_manual_key(
    tmp_path, source, accepted
):
    (tmp_path / "auth.json").write_text(
        json.dumps(
            {
                "credential_pool": {
                    "openrouter": [
                        {
                            "auth_type": "api_key",
                            "source": source,
                            "access_token": "fake",
                        }
                    ]
                }
            }
        )
    )
    with patch("wizard.hermes_config.get_hermes_home", return_value=tmp_path):
        assert has_hermes_auth_profile("openrouter", auth_type="oauth") is accepted
        assert has_hermes_auth_profile("anthropic", auth_type="oauth") is False


def test_error_classifier_does_not_echo_upstream_credentials():
    for detail in [
        "access_token=secret",
        "denied refresh_token=secret",
        "device authorization disabled: secret",
    ]:
        code = error_code(RuntimeError(detail))
        assert code in auth.MESSAGES
        assert "secret" not in auth.MESSAGES[code]
