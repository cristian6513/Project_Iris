"""Packaged launch/probe/stop use fake processes and authenticated HTTP fixtures."""

import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from pathlib import Path

import httpx
import psutil
import pytest
from fastapi.responses import JSONResponse

from core import desktop, process_launcher
from core.runtime_descriptor import (
    DESCRIPTOR_ENV, RuntimeDescriptorError, RuntimePolicyError, get_runtime,
    runtime_pycache_dir, runtime_service_identity,
)
from tests.runtime_descriptor_fixtures import write_runtime_fixture


@pytest.fixture
def packaged(tmp_path, monkeypatch):
    descriptor, _ = write_runtime_fixture(tmp_path / "Iris bundled á")
    monkeypatch.setenv(DESCRIPTOR_ENV, str(descriptor))
    monkeypatch.setenv("IRIS_HOME", str(tmp_path / "profile"))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profile/hermes"))
    return get_runtime()


def core_process(runtime, *, pid=123, created=10.0):
    environment = {key: os.environ[key] for key in ["IRIS_HOME", "HERMES_HOME", DESCRIPTOR_ENV]}
    return SimpleNamespace(
        pid=pid, exe=lambda: str(runtime.iris_python), cwd=lambda: str(runtime.iris_root),
        create_time=lambda: created,
        cmdline=lambda: [str(runtime.iris_python), *process_launcher._packaged_core_flags(), "-m", "core.agent"],
        environ=lambda: environment,
        terminate=Mock(), kill=Mock(), wait=Mock(),
    )


def record(process):
    process_launcher._write_pid_file(process_launcher.ProcessHandle(process.pid, 9876, "fixture.log", 1.0))


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["none", "origin", "profile", "hermes_profile", "revision", "mode", "valid"])
async def test_packaged_probe_requires_authorized_origin_and_profile(packaged, monkeypatch, change):
    home = process_launcher.get_iris_home()
    (home / "companion_token").write_text("fixture-token")
    identity = runtime_service_identity(home)
    if change == "origin":
        identity["runtime_id"] = "0" * 64
    elif change == "profile":
        identity["profile_id"] = "0" * 64
    elif change == "hermes_profile":
        monkeypatch.setenv("HERMES_HOME", str(home / "other-hermes"))
    elif change == "revision":
        identity["hermes_revision"] = "c" * 40
    elif change == "mode":
        identity["mode"] = "development"
    body = {"name": "IRIS", "status": "running", "version": "1.0", "runtime": identity}
    if change == "none":
        del body["runtime"]
    monkeypatch.setattr(desktop, "candidate_ports", lambda _: [9876])

    def handler(request):
        assert request.headers["X-Iris-Companion-Token"] == "fixture-token"
        return httpx.Response(200, json=body)

    original_client = httpx.AsyncClient

    def client_factory(**kwargs):
        return original_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(desktop.httpx, "AsyncClient", client_factory)
    result = await desktop.probe_iris(None)
    assert result == ("http://127.0.0.1:9876/face/" if change == "valid" else None)


@pytest.mark.asyncio
async def test_invalid_descriptor_is_visible_before_profile_or_service_work(packaged, monkeypatch):
    packaged.descriptor_path.write_text("{}")
    load = Mock(side_effect=AssertionError("config must not be loaded"))
    monkeypatch.setattr(desktop.IRISConfig, "load", load)
    gateway = AsyncMock()
    monkeypatch.setattr("wizard.app.start_gateway", gateway)
    state = desktop.DesktopRuntime()
    await state._start()
    assert state.state["phase"] == "error"
    assert "runtime" in state.state["message"]
    load.assert_not_called()
    gateway.assert_not_called()


def test_core_entrypoint_rejects_invalid_runtime_before_loading_profile(packaged, monkeypatch):
    from core import agent

    packaged.descriptor_path.write_text("{}")
    load = Mock(side_effect=AssertionError("profile must not be loaded"))
    run = Mock(side_effect=AssertionError("agent must not run"))
    monkeypatch.setattr(agent.IRISConfig, "load", load)
    monkeypatch.setattr(agent.asyncio, "run", run)
    with pytest.raises(RuntimeDescriptorError):
        agent.IRISAgent()
    with pytest.raises(RuntimeDescriptorError):
        agent.main()
    load.assert_not_called()
    run.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("becomes_ready", [True, False])
async def test_owned_pending_gateway_is_waited_without_core_duplicate(packaged, monkeypatch, becomes_ready):
    home = process_launcher.get_iris_home()
    (home / "config.json").write_text("{}")
    # A profile that already accepted Hermes' notice (A08.6); without it
    # Desktop stops at setup before reaching the Gateway.
    config = SimpleNamespace(websocket_port=9876, companion_loopback_port=9877, lan_access=False,
                             hermes_security_acknowledged=True)
    monkeypatch.setattr(desktop.IRISConfig, "load", lambda: config)
    url = "http://127.0.0.1:9876/face/"
    monkeypatch.setattr(desktop, "probe_iris", AsyncMock(return_value=url))
    responses = [JSONResponse({"status": "pending", "code": "gateway_runtime_pending"}, status_code=202)]
    if becomes_ready:
        responses.append(JSONResponse({"status": "running"}))
    gateway = AsyncMock(side_effect=responses)
    monkeypatch.setattr("wizard.app.start_gateway", gateway)
    monkeypatch.setattr(desktop.asyncio, "sleep", AsyncMock())
    launch = Mock()
    monkeypatch.setattr(desktop, "launch_iris", launch)
    state = desktop.DesktopRuntime(ready_timeout=1 if becomes_ready else 0)
    await state._start()
    assert state.state["phase"] == ("ready" if becomes_ready else "error")
    assert gateway.await_count == (2 if becomes_ready else 1)
    launch.assert_not_called()


def test_core_spawn_propagates_selected_python_mode_and_profiles(packaged, monkeypatch):
    process = core_process(packaged)
    monkeypatch.setattr("psutil.Process", lambda _: process)
    monkeypatch.setattr(process_launcher, "is_iris_running", lambda: None)
    monkeypatch.setattr(process_launcher, "_read_pid_file", lambda: None)
    child = Mock(pid=process.pid)
    spawn = Mock(return_value=child)
    monkeypatch.setattr(process_launcher.platform, "system", lambda: "Windows")
    monkeypatch.setattr(process_launcher.subprocess, "Popen", spawn)
    monkeypatch.setenv("PYTHONHOME", "external")
    monkeypatch.setenv("HERMES_REPO", "external")
    handle = process_launcher.launch_iris(port=9876, wait_for_port_s=0)
    assert handle.pid == process.pid
    assert spawn.call_args.args[0] == process.cmdline()
    # qa9: bytecode is cached in the profile, never written into the installation.
    cache = runtime_pycache_dir()
    assert cache is not None and cache.is_relative_to(process_launcher.get_iris_home())
    assert not cache.is_relative_to(packaged.base)
    assert spawn.call_args.args[0][1:7] == ["-E", "-s", "-X", f"pycache_prefix={cache}", "-X", "utf8"]
    assert "-B" not in spawn.call_args.args[0]
    # Its own console without a window, inherited by the Hermes programs it
    # starts: no visible console per command (qa9). DETACHED_PROCESS is gone.
    assert spawn.call_args.kwargs["creationflags"] == 0x00000200 | 0x08000000
    assert spawn.call_args.kwargs["cwd"] == str(packaged.iris_root)
    env = spawn.call_args.kwargs["env"]
    assert env[DESCRIPTOR_ENV] == str(packaged.descriptor_path)
    assert env["IRIS_HOME"] == str(process_launcher.get_iris_home().resolve())
    assert env["HERMES_HOME"].endswith("hermes")
    assert "PYTHONHOME" not in env and "HERMES_REPO" not in env


def test_root_override_rejected_before_spawn(packaged, tmp_path, monkeypatch):
    spawn = Mock()
    monkeypatch.setattr(process_launcher.subprocess, "Popen", spawn)
    with pytest.raises(RuntimePolicyError):
        process_launcher.launch_iris(project_root=tmp_path, port=9876)
    spawn.assert_not_called()


@pytest.mark.parametrize("change", ["exe", "cwd", "argv", "created", "profile", "iris_env", "hermes_env", "descriptor_env", "valid"])
def test_pending_pid_requires_process_and_profile_provenance(packaged, monkeypatch, change):
    process = core_process(packaged)
    monkeypatch.setattr("psutil.Process", lambda _: process)
    record(process)
    if change == "exe":
        process.exe = lambda: __file__
    elif change == "cwd":
        process.cwd = lambda: str(packaged.base)
    elif change == "argv":
        process.cmdline = lambda: ["python", "-m", "core.agent"]
    elif change == "created":
        process.create_time = lambda: 11.0
    elif change in {"iris_env", "hermes_env", "descriptor_env"}:
        environment = dict(process.environ())
        key = {"iris_env": "IRIS_HOME", "hermes_env": "HERMES_HOME", "descriptor_env": DESCRIPTOR_ENV}[change]
        environment[key] = str(packaged.base / "other-profile-or-runtime")
        process.environ = lambda: environment
    elif change == "profile":
        path = process_launcher._pid_file()
        doc = json.loads(path.read_text())
        doc["runtime"]["profile_id"] = "0" * 64
        path.write_text(json.dumps(doc))
    assert process_launcher.running_iris_pid(process.pid) is (change == "valid")


@pytest.mark.parametrize("doc", [[], None, {"pid": None}, {"pid": []}])
def test_invalid_pid_sidecar_is_not_a_process(packaged, doc):
    process_launcher._pid_file().write_text(json.dumps(doc))
    with pytest.raises(RuntimePolicyError):
        process_launcher._read_pid_file()


def test_tcp_listener_does_not_make_packaged_core_ready(packaged, monkeypatch):
    process = core_process(packaged)
    monkeypatch.setattr("psutil.Process", lambda _: process)
    record(process)
    monkeypatch.setattr(process_launcher, "_pid_alive", lambda _: True)
    monkeypatch.setattr(process_launcher, "_port_open", lambda _: True)
    home = process_launcher.get_iris_home()
    (home / "companion_token").write_text("fixture-token")
    client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"name": "Another service"})))
    monkeypatch.setattr(httpx, "Client", lambda **_: client)
    assert process_launcher.is_iris_running() is None


def test_packaged_stop_does_not_signal_unowned_process(packaged, monkeypatch):
    process = core_process(packaged)
    monkeypatch.setattr("psutil.Process", lambda _: process)
    record(process)
    process.exe = lambda: __file__
    monkeypatch.setattr(process_launcher, "_pid_alive", lambda _: True)
    with pytest.raises(RuntimePolicyError):
        process_launcher.stop_iris(process.pid, timeout_s=0)
    process.terminate.assert_not_called()
    process.kill.assert_not_called()
    assert process_launcher._pid_file().exists()


@pytest.mark.parametrize("state", ["recycled", "same_creation", "access_denied", "port_busy"])
def test_launch_recovers_only_provably_recycled_pid(packaged, monkeypatch, state):
    original = core_process(packaged)
    replacement = core_process(packaged, created=10.0 if state == "same_creation" else 20.0)
    replacement.exe = lambda: __file__
    new_core = core_process(packaged, pid=456, created=30.0)
    monkeypatch.setattr("psutil.Process", lambda _: original)
    record(original)
    path = process_launcher._pid_file()
    previous = path.read_bytes()
    if state == "access_denied":
        replacement.create_time = Mock(side_effect=psutil.AccessDenied(replacement.pid))
    monkeypatch.setattr(
        "psutil.Process", lambda pid: replacement if pid == original.pid else new_core,
    )
    monkeypatch.setattr(process_launcher, "_pid_alive", lambda _: True)
    monkeypatch.setattr(process_launcher, "_port_open", lambda _: state == "port_busy")
    monkeypatch.setattr(process_launcher.platform, "system", lambda: "Windows")
    spawn = Mock(return_value=Mock(pid=new_core.pid))
    monkeypatch.setattr(process_launcher.subprocess, "Popen", spawn)

    if state == "recycled":
        assert process_launcher.launch_iris(port=9876, wait_for_port_s=0).pid == new_core.pid
        spawn.assert_called_once()
        assert json.loads(path.read_text())["pid"] == new_core.pid
    else:
        with pytest.raises(RuntimePolicyError, match="El proceso guardado"):
            process_launcher.launch_iris(port=9876, wait_for_port_s=0)
        spawn.assert_not_called()
        assert path.read_bytes() == previous
    replacement.terminate.assert_not_called()
    replacement.kill.assert_not_called()


def test_recycled_pid_during_stop_never_receives_hard_kill(packaged, monkeypatch):
    original = core_process(packaged)
    replacement = core_process(packaged, created=20.0)
    monkeypatch.setattr("psutil.Process", lambda _: original)
    record(original)
    original.wait.side_effect = psutil.TimeoutExpired(0)
    monkeypatch.setattr(process_launcher, "_owned_core_process", Mock(side_effect=[original, replacement]))
    with pytest.raises(RuntimePolicyError):
        process_launcher.stop_iris(original.pid, timeout_s=0)
    original.terminate.assert_called_once()
    original.kill.assert_not_called()
    replacement.terminate.assert_not_called()
    assert process_launcher._pid_file().exists()


def test_stop_preserves_replaced_record(packaged, monkeypatch):
    process = core_process(packaged)
    monkeypatch.setattr("psutil.Process", lambda _: process)
    record(process)

    def replace_record(**_):
        path = process_launcher._pid_file()
        doc = json.loads(path.read_text())
        doc["pid"] = 456
        path.write_text(json.dumps(doc))

    process.wait.side_effect = replace_record
    assert process_launcher.stop_iris(process.pid)
    assert json.loads(process_launcher._pid_file().read_text())["pid"] == 456
    process.kill.assert_not_called()


@pytest.mark.parametrize("field,value", [
    ("pid", True), ("pid", 1.0), ("pid", "123"), ("pid", 0x100000000),
    ("port", True), ("port", 9876.0), ("port", "9876"), ("port", 65536),
    ("started_at", float("inf")), ("process_created_at", float("nan")),
    ("started_at", 10 ** 500), ("unknown", "unexpected"),
])
def test_unverified_receipt_fields_block_spawn(packaged, monkeypatch, field, value):
    process = core_process(packaged)
    monkeypatch.setattr("psutil.Process", lambda _: process)
    record(process)
    path = process_launcher._pid_file()
    doc = json.loads(path.read_text())
    doc[field] = value
    path.write_text(json.dumps(doc))
    previous = path.read_bytes()
    spawn = Mock()
    monkeypatch.setattr(process_launcher.subprocess, "Popen", spawn)
    with pytest.raises(RuntimePolicyError):
        process_launcher.launch_iris(port=9876)
    spawn.assert_not_called()
    assert path.read_bytes() == previous


def test_oversized_receipt_blocks_spawn_without_reading_payload(packaged, monkeypatch):
    path = process_launcher._pid_file()
    path.write_bytes(b" " * (process_launcher.MAX_PID_BYTES + 1))
    spawn = Mock()
    monkeypatch.setattr(process_launcher.subprocess, "Popen", spawn)
    with pytest.raises(RuntimePolicyError):
        process_launcher.launch_iris(port=9876)
    spawn.assert_not_called()
    assert path.stat().st_size == process_launcher.MAX_PID_BYTES + 1


def test_deeply_nested_receipt_fails_with_visible_policy_error(packaged):
    process_launcher._pid_file().write_text("[" * 1200 + "0" + "]" * 1200)
    with pytest.raises(RuntimePolicyError):
        process_launcher._read_pid_file()


def test_duplicate_receipt_fields_are_not_authorized(packaged, monkeypatch):
    process = core_process(packaged)
    monkeypatch.setattr("psutil.Process", lambda _: process)
    record(process)
    path = process_launcher._pid_file()
    path.write_text(path.read_text().replace('"pid": 123', '"pid": 123, "pid": 123'))
    with pytest.raises(RuntimePolicyError):
        process_launcher._read_pid_file()


@pytest.mark.parametrize("alias", ["hardlink", "symlink"])
def test_pid_receipt_alias_never_writes_or_signals_external_file(packaged, monkeypatch, tmp_path, alias):
    process = core_process(packaged)
    monkeypatch.setattr("psutil.Process", lambda _: process)
    record(process)
    path = process_launcher._pid_file()
    external = tmp_path / "foreign-pid.json"
    external.write_bytes(path.read_bytes())
    path.unlink()
    if alias == "hardlink":
        os.link(external, path)
    else:
        try:
            path.symlink_to(external)
        except OSError as exc:
            if os.name == "nt" and exc.winerror == 1314:
                pytest.skip("Windows denies file symlinks; real hardlink and junction cases execute.")
            raise
    previous = external.read_bytes()
    spawn = Mock()
    monkeypatch.setattr(process_launcher.subprocess, "Popen", spawn)
    for operation in [process_launcher._read_pid_file,
                      lambda: process_launcher.launch_iris(port=9876),
                      lambda: process_launcher.stop_iris(), lambda: record(process)]:
        with pytest.raises(RuntimePolicyError):
            operation()
    spawn.assert_not_called()
    process.terminate.assert_not_called()
    process.kill.assert_not_called()
    assert external.read_bytes() == previous


def test_atomic_receipt_write_failure_preserves_previous_record(packaged, monkeypatch):
    process = core_process(packaged)
    monkeypatch.setattr("psutil.Process", lambda _: process)
    record(process)
    path = process_launcher._pid_file()
    previous = path.read_bytes()
    monkeypatch.setattr(process_launcher.os, "replace", Mock(side_effect=OSError("fixture commit failure")))
    with pytest.raises(OSError):
        record(process)
    assert path.read_bytes() == previous
    assert not list(path.parent.glob(".iris-pid-*.json"))


def test_dead_pid_probe_preserves_a_replaced_record(packaged, monkeypatch):
    process = core_process(packaged)
    monkeypatch.setattr("psutil.Process", lambda _: process)
    record(process)
    path = process_launcher._pid_file()

    def replace_during_probe(_):
        doc = json.loads(path.read_text())
        doc.update(pid=456, started_at=2.0, process_created_at=20.0)
        path.write_text(json.dumps(doc))
        return False

    monkeypatch.setattr(process_launcher, "_pid_alive", replace_during_probe)
    monkeypatch.setattr(process_launcher, "_port_open", lambda _: False)
    assert process_launcher.is_iris_running() is None
    assert json.loads(path.read_text())["pid"] == 456


def test_receipt_replaced_during_open_is_not_authorized(packaged, monkeypatch):
    process = core_process(packaged)
    monkeypatch.setattr("psutil.Process", lambda _: process)
    record(process)
    path = process_launcher._pid_file()
    original_open = Path.open

    def replace_before_open(self, *args, **kwargs):
        if self == path:
            replacement = path.with_suffix(".replacement")
            replacement.write_bytes(b"{}")
            os.replace(replacement, path)
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", replace_before_open)
    with pytest.raises(RuntimePolicyError):
        process_launcher._read_pid_file()
