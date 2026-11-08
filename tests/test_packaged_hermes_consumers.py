"""Hermes bridges use fake runtimes/processes, without accounts or native audio."""

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from core import hermes_cron, hermes_kanban, hermes_runtime, hermes_wake
from core.runtime_descriptor import (
    DESCRIPTOR_ENV, RuntimeDescriptorError, RuntimePolicyError, get_runtime, runtime_pycache_dir,
)
from tests.runtime_descriptor_fixtures import write_runtime_fixture
from wizard import messaging as messaging_bridge, provider_auth


@pytest.fixture
def packaged(tmp_path, monkeypatch):
    descriptor, spec = write_runtime_fixture(tmp_path / "Iris instalación á")
    monkeypatch.setenv(DESCRIPTOR_ENV, str(descriptor))
    monkeypatch.setenv("IRIS_HOME", str(tmp_path / "profile"))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profile/hermes"))
    for name in ("PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT"):
        monkeypatch.setenv(name, str(tmp_path / "external-python"))
    monkeypatch.setenv("HERMES_REPO", str(tmp_path / "external-hermes"))
    monkeypatch.setenv("HERMES_AGENT_ROOT", str(tmp_path / "another-hermes"))
    monkeypatch.setattr(hermes_runtime.shutil, "which", Mock(side_effect=AssertionError("No PATH discovery")))
    return SimpleNamespace(context=get_runtime(), spec=spec)


def assert_pinned_command(call, runtime, *, worker=False):
    command = list(call.args[0]) if isinstance(call.args[0], list) else list(call.args)
    # qa9: bytecode goes to the profile's cache (never the installation), so a
    # command no longer recompiles Hermes every time and runs past its timeout.
    cache = runtime_pycache_dir()
    assert cache is not None and cache.is_relative_to(Path(os.environ["IRIS_HOME"]))
    assert not cache.is_relative_to(runtime.base)
    assert command[:7] == [str(runtime.hermes.python), "-E", "-s", "-X", f"pycache_prefix={cache}", "-X", "utf8"]
    assert command[7] == ("-u" if worker else "-c")
    assert "-B" not in command
    assert call.kwargs["cwd"] == str(runtime.hermes.root)
    assert "shell" not in call.kwargs
    environment = call.kwargs["env"]
    assert environment[DESCRIPTOR_ENV] == str(runtime.descriptor_path)
    assert environment["HERMES_HOME"].endswith("hermes")
    assert environment["PYTHONNOUSERSITE"] == "1"
    assert environment["PYTHONPYCACHEPREFIX"] == str(cache)
    assert "PYTHONDONTWRITEBYTECODE" not in environment
    for name in ("PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT", "HERMES_REPO", "HERMES_AGENT_ROOT"):
        assert name not in environment


@pytest.mark.parametrize("consumer", ["cron", "kanban", "wake"])
def test_direct_bridges_use_pinned_python_and_clean_environment(packaged, monkeypatch, consumer):
    process = SimpleNamespace(returncode=0, stdout=json.dumps({"success": True, "available": False}), stderr="")
    spawn = Mock(return_value=process)
    monkeypatch.setattr(hermes_runtime.subprocess, "run", spawn)
    for module in (hermes_cron, hermes_kanban):
        monkeypatch.setattr(module, "_hermes_python", Mock(side_effect=AssertionError("No legacy interpreter")))
    if consumer == "cron":
        assert hermes_cron.run_cronjob(action="list")["success"]
    elif consumer == "kanban":
        assert hermes_kanban._run_bridge({"action": "list"})["success"]
    else:
        assert hermes_wake.probe_hermes_wake_requirements()["installed"]
    assert_pinned_command(spawn.call_args, packaged.context)


def test_packaged_cron_never_imports_hermes_inside_iris(packaged, monkeypatch):
    repository = Mock(side_effect=AssertionError("In-process path must not run"))
    monkeypatch.setattr(hermes_cron, "_hermes_repo", repository)
    assert hermes_cron._list_cron_jobs_in_process() is None
    repository.assert_not_called()
    monkeypatch.setattr(hermes_cron, "run_hermes_cli", lambda *a, **k: {"success": False})
    bridge = Mock(return_value={"success": True, "jobs": []})
    monkeypatch.setattr(hermes_cron, "run_cronjob", bridge)
    assert hermes_cron.list_cron_jobs()["source_mode"] == "hermes-cronjob-tool"
    bridge.assert_called_once_with(action="list", include_disabled=True)


@pytest.mark.parametrize("module", [hermes_cron, hermes_kanban])
def test_development_keeps_legacy_interpreter_fallback(module, tmp_path, monkeypatch):
    monkeypatch.delenv(DESCRIPTOR_ENV, raising=False)
    monkeypatch.setenv("IRIS_HOME", str(tmp_path / "profile"))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profile/hermes"))
    monkeypatch.setattr(module, "_hermes_repo", lambda: tmp_path)
    spawn = Mock(return_value=SimpleNamespace(returncode=0, stdout='{"success":true}', stderr=""))
    monkeypatch.setattr(module.subprocess, "run", spawn)
    if module is hermes_cron:
        module.run_cronjob(action="list")
    else:
        module._run_bridge({"action": "list"})
    assert spawn.call_args.args[0][0:2] == [sys.executable, "-c"]
    assert spawn.call_args.kwargs["cwd"] == str(tmp_path)


class Worker:
    def __init__(self, response):
        self.returncode = None
        self.stdout = SimpleNamespace(readline=AsyncMock(return_value=json.dumps(response).encode() + b"\n"))
        self.stdin = SimpleNamespace(write=Mock(), drain=AsyncMock(), close=Mock())
        self.wait = AsyncMock(return_value=0)

    def kill(self):
        self.returncode = -1


@pytest.mark.asyncio
@pytest.mark.parametrize("consumer", ["auth", "messaging"])
@pytest.mark.parametrize("mode", ["packaged", "development"])
async def test_wizard_workers_keep_runtime_python_cwd_and_flags(packaged, monkeypatch, consumer, mode):
    packaged.spec["mode"] = mode
    packaged.context.descriptor_path.write_text(json.dumps(packaged.spec), encoding="utf-8")
    runtime = get_runtime()
    response = {"status": "error", "code": "provider_error"} if consumer == "auth" else {"ok": True, "data": {"platforms": []}}
    process = Worker(response)
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr(provider_auth.asyncio, "create_subprocess_exec", spawn)
    if consumer == "auth":
        session = provider_auth.Login("fixture", "nous", "nous")
        await provider_auth.ProviderAuth()._run(session, "login")
        assert session.status == "error"
    else:
        manager = messaging_bridge.HermesMessaging()
        assert await manager.call("catalog") == {"platforms": []}
        await manager.close()
    if mode == "packaged":
        assert_pinned_command(spawn.call_args, runtime, worker=True)
    else:
        assert spawn.call_args.args[:3] == (str(runtime.hermes.python), "-B", "-u")
        assert spawn.call_args.kwargs["cwd"] == str(runtime.hermes.root)
        assert "HERMES_REPO" in spawn.call_args.kwargs["env"]


def break_runtime(packaged, broken):
    runtime = packaged.context
    if broken == "descriptor":
        runtime.descriptor_path.write_text("{}", encoding="utf-8")
    elif broken == "metadata":
        runtime.hermes.metadata.write_text('{"schema_version":1,"version":"wrong","revision":"' + "b" * 40 + '"}', encoding="utf-8")
    else:
        runtime.hermes.python.unlink()


@pytest.mark.parametrize("consumer", ["cron", "kanban", "wake"])
@pytest.mark.parametrize("broken", ["descriptor", "metadata", "python"])
def test_invalid_packaged_runtime_never_falls_back_or_spawns(packaged, monkeypatch, consumer, broken):
    break_runtime(packaged, broken)
    spawn = Mock(side_effect=AssertionError("No process for invalid runtime"))
    monkeypatch.setattr(hermes_runtime.subprocess, "run", spawn)
    operation = {
        "cron": lambda: hermes_cron.run_cronjob(action="list"),
        "kanban": lambda: hermes_kanban._run_bridge({"action": "list"}),
        "wake": hermes_wake.probe_hermes_wake_requirements,
    }[consumer]
    with pytest.raises(RuntimeDescriptorError):
        operation()
    spawn.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("consumer", ["auth", "messaging"])
@pytest.mark.parametrize("broken", ["descriptor", "metadata", "python"])
async def test_wizard_invalid_runtime_never_starts_a_worker(packaged, monkeypatch, consumer, broken):
    break_runtime(packaged, broken)
    spawn = AsyncMock(side_effect=AssertionError("No worker for invalid runtime"))
    monkeypatch.setattr(provider_auth.asyncio, "create_subprocess_exec", spawn)
    if consumer == "auth":
        session = provider_auth.Login("fixture", "nous", "nous")
        await provider_auth.ProviderAuth()._run(session, "login")
        assert session.status == "error"
    else:
        with pytest.raises(HTTPException) as error:
            await messaging_bridge.HermesMessaging().call("catalog")
        assert error.value.status_code == 503
    spawn.assert_not_called()


def test_shared_python_prefix_rejects_external_interpreter_in_packaged(packaged):
    assert hermes_runtime.hermes_python_prefix("external-python") == [
        str(packaged.context.hermes.python), "-E", "-s", "-X", f"pycache_prefix={runtime_pycache_dir()}", "-X", "utf8",
    ]


def test_an_unsafe_cache_folder_falls_back_to_no_bytecode(packaged, monkeypatch):
    # A cache that is a link (or resolves outside the profile) is never used:
    # processes then run as before, with -B and PYTHONDONTWRITEBYTECODE.
    import core.runtime_descriptor as descriptor

    monkeypatch.setattr(descriptor, "_reparse_point", lambda path: True)
    assert hermes_runtime.hermes_python_prefix() == [str(packaged.context.hermes.python), "-E", "-s", "-B", "-X", "utf8"]
    environment = hermes_runtime.hermes_subprocess_env()
    assert environment["PYTHONDONTWRITEBYTECODE"] == "1" and "PYTHONPYCACHEPREFIX" not in environment


def test_direct_cli_update_is_blocked_before_resolution_or_spawn(packaged, monkeypatch):
    prefix, spawn = Mock(), Mock()
    monkeypatch.setattr(hermes_runtime, "hermes_command_prefix", prefix)
    monkeypatch.setattr(hermes_runtime.subprocess, "run", spawn)
    with pytest.raises(RuntimePolicyError):
        hermes_runtime.run_hermes_cli(["update"])
    prefix.assert_not_called()
    spawn.assert_not_called()


def test_development_cli_update_keeps_existing_command(tmp_path, monkeypatch):
    monkeypatch.delenv(DESCRIPTOR_ENV, raising=False)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profile/hermes"))
    monkeypatch.setattr(hermes_runtime, "hermes_command_prefix", lambda: ["development-hermes"])
    monkeypatch.setattr(hermes_runtime, "get_hermes_repo", lambda: tmp_path)
    spawn = Mock(return_value=SimpleNamespace(returncode=0, stdout="updated", stderr=""))
    monkeypatch.setattr(hermes_runtime.subprocess, "run", spawn)
    assert hermes_runtime.run_hermes_cli(["update"])["success"]
    assert spawn.call_args.args[0] == ["development-hermes", "update"]
