"""Gateway startup regressions exposed by automatic desktop launch."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import httpx
import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body,ok",
    [
        ({"platform": "hermes-agent", "status": "ok", "version": "0.21.4"}, True),
        ({"platform": "other-server", "status": "ok"}, False),
        ({"platform": "hermes-agent", "status": "starting"}, False),
        ({"status": "ok"}, False),
        ("<html>Another service</html>", False),
    ],
)
async def test_hermes_health_requires_native_identity(monkeypatch, body, ok):
    from wizard.health_check import check_gateway_health

    def handler(_request):
        if isinstance(body, dict):
            return httpx.Response(200, json=body)
        return httpx.Response(200, text=body)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr("wizard.health_check.httpx.AsyncClient", lambda **_: client)
    assert (await check_gateway_health("http://127.0.0.1:8642"))["ok"] is ok


@pytest.mark.asyncio
async def test_already_synced_gateway_launch_returns_success(tmp_path, monkeypatch):
    from wizard.app import start_gateway

    monkeypatch.setenv("IRIS_HOME", str(tmp_path))
    cfg = SimpleNamespace(
        hermes_url="http://127.0.0.1:8642",
        llm_provider="ollama-local",
        llm_model="test-local-model",
        llm_api_key="",
        llm_use_oauth=False,
    )
    monkeypatch.setattr("wizard.app.IRISConfig.load", lambda: cfg)
    monkeypatch.setattr(
        "wizard.health_check.check_gateway_port", AsyncMock(return_value={"ok": False})
    )
    monkeypatch.setattr(
        "wizard.health_check.check_gateway_health", AsyncMock(return_value={"ok": True})
    )
    monkeypatch.setattr(
        "wizard.hermes_config.read_hermes_dotenv",
        lambda: {
            "LLM_PROVIDER": "ollama-local",
            "LLM_MODEL": "test-local-model",
        },
    )
    monkeypatch.setattr("wizard.hermes_config.has_hermes_auth_profile", lambda *a, **kw: False)
    monkeypatch.setattr("wizard.hermes_config.read_provider_base_url", lambda _: "")
    monkeypatch.setattr("wizard.hermes_config._read_env_file", lambda: {})
    monkeypatch.setattr("wizard.app._external_provider_preflight", AsyncMock(return_value=None))
    monkeypatch.setattr("wizard.app._local_provider_preflight", AsyncMock(return_value=None))
    monkeypatch.setattr("wizard.app._hermes_child_env", lambda _: {})
    monkeypatch.setattr("wizard.app._resolve_hermes_invocation", lambda: (["hermes"], "test"))
    monkeypatch.setattr("wizard.app.resolve_cmd", lambda _: "hermes")
    sync = AsyncMock()
    monkeypatch.setattr("wizard.app.sync_hermes_config_from_iris", sync)
    process = Mock(pid=123, poll=lambda: None)
    spawn = Mock(return_value=process)
    monkeypatch.setattr("wizard.app.subprocess.Popen", spawn)
    monkeypatch.setattr("socket.create_connection", MagicMock())
    response = await start_gateway()
    payload = json.loads(response.body)
    assert payload["status"] == "success", payload
    assert "sincronizada" in payload["sync"]["detail"]
    sync.assert_not_called()
    spawn.assert_called_once()
    assert spawn.call_args.kwargs["stdout"].closed


def test_process_launcher_preserves_slow_pending_process(tmp_path, monkeypatch):
    from core import process_launcher

    monkeypatch.setenv("IRIS_HOME", str(tmp_path))
    handle = process_launcher.ProcessHandle(123, 9876, "isolated.log", 1.0)
    monkeypatch.setattr(process_launcher, "is_iris_running", lambda: None)
    monkeypatch.setattr(process_launcher, "_read_pid_file", lambda: handle)
    monkeypatch.setattr(process_launcher, "running_iris_pid", lambda _: True)
    spawn = Mock()
    monkeypatch.setattr(process_launcher.subprocess, "Popen", spawn)
    assert process_launcher.launch_iris(port=9876) is handle
    spawn.assert_not_called()
