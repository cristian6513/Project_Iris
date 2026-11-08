"""Hermes contract probes use declared staging and only synthetic local state."""

from __future__ import annotations

import io
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts import verify_hermes_contract as contract


@pytest.fixture
def declared(tmp_path):
    root = tmp_path / "Hermes source á"
    (root / "hermes_cli").mkdir(parents=True)
    (root / "hermes_cli/__init__.py").write_text('__version__ = "0.21.4"', encoding="utf8")
    iris = tmp_path / "runtimes/iris/python.exe"
    hermes = tmp_path / "runtimes/hermes/python.exe"
    for executable in (iris, hermes):
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b"test interpreter, never executed")
    args = ["--hermes-root", str(root), "--hermes-python", str(hermes),
            "--iris-python", str(iris), "--work-root", str(tmp_path / "work ü")]
    return contract.resolve_runtime(contract.parser().parse_args(args)), args


def test_explicit_inputs_never_discover_external_hermes(declared, monkeypatch):
    from core import hermes_runtime

    forbidden = Mock(side_effect=AssertionError("No external discovery"))
    monkeypatch.setattr(hermes_runtime, "get_hermes_repo", forbidden)
    monkeypatch.setattr(hermes_runtime, "resolve_hermes_python", forbidden)
    runtime, args = declared
    actual = contract.resolve_runtime(contract.parser().parse_args(args))
    assert actual == runtime
    assert actual.explicit
    forbidden.assert_not_called()


@pytest.mark.parametrize("option", ["--hermes-root", "--hermes-python", "--iris-python",
                                     "--work-root"])
def test_partial_staging_inputs_fail_instead_of_discovery(declared, option):
    _, args = declared
    index = args.index(option)
    with pytest.raises(ValueError, match="Explicit staging requires"):
        contract.resolve_runtime(contract.parser().parse_args(args[:index] + args[index + 2:]))


def test_declared_missing_interpreter_fails(declared):
    runtime, args = declared
    runtime.hermes_python.unlink()
    with pytest.raises(FileNotFoundError):
        contract.resolve_runtime(contract.parser().parse_args(args))


def test_declared_missing_source_fails(declared):
    runtime, args = declared
    (runtime.hermes_root / "hermes_cli/__init__.py").unlink()
    with pytest.raises(ValueError, match="source root is incomplete"):
        contract.resolve_runtime(contract.parser().parse_args(args))


def test_declared_directory_is_not_an_interpreter(declared):
    runtime, args = declared
    args[args.index("--hermes-python") + 1] = str(runtime.hermes_python.parent)
    with pytest.raises(ValueError, match="interpreters must be files"):
        contract.resolve_runtime(contract.parser().parse_args(args))


def test_developer_default_still_discovers_managed_runtime(declared, monkeypatch):
    from core import hermes_runtime

    runtime, _ = declared
    monkeypatch.setattr(hermes_runtime, "get_hermes_repo", lambda: runtime.hermes_root)
    monkeypatch.setattr(hermes_runtime, "resolve_hermes_python", lambda: runtime.hermes_python)
    args = contract.parser().parse_args(["--work-root", str(runtime.work_root)])
    actual = contract.resolve_runtime(args)
    assert not actual.explicit
    assert actual.hermes_root == runtime.hermes_root
    assert actual.iris_python == Path(sys.executable).resolve()


def test_environment_contains_no_inherited_secrets_or_python_configuration(tmp_path):
    inherited = {"SystemRoot": "C:/Windows", "WINDIR": "C:/Windows",
                 "PATH": "external Python;Hermes;FFmpeg", "VIRTUAL_ENV": "developer",
                 "PYTHONPATH": "outside", "IRIS_RUNTIME_DESCRIPTOR": "outside",
                 "OPENAI_API_KEY": "SECRET", "AZURE_CLIENT_SECRET": "SECRET",
                 "AWS_ACCESS_KEY_ID": "SECRET", "GOOGLE_APPLICATION_CREDENTIALS": "SECRET",
                 "HERMES_HOME": "personal", "LOCALAPPDATA": "personal"}
    env = contract.isolated_environment(tmp_path, inherited)
    assert env["PATH"] == ""
    assert env["SystemRoot"] == "C:/Windows"
    assert env["HERMES_DISABLE_LAZY_INSTALLS"] == "1"
    assert "SECRET" not in repr(env)
    for key in inherited.keys() - {"SystemRoot", "WINDIR", "PATH", "HERMES_HOME", "LOCALAPPDATA"}:
        assert key not in env
    for name in ("HERMES_HOME", "IRIS_HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "TEMP"):
        assert Path(env[name]).is_relative_to(tmp_path)
        assert Path(env[name]).is_dir()


@pytest.mark.parametrize("platform", ["nt", "posix"])
def test_architecture_marker_matches_private_windows_runtime_without_inheriting_host(tmp_path, monkeypatch, platform):
    monkeypatch.setattr(contract, "os", SimpleNamespace(name=platform, environ={}))
    env = contract.isolated_environment(tmp_path, {"PROCESSOR_ARCHITECTURE": "ARM64",
                                                   "PROCESSOR_ARCHITEW6432": "ARM64"})
    assert env.get("PROCESSOR_ARCHITECTURE") == ("AMD64" if platform == "nt" else None)
    assert "PROCESSOR_ARCHITEW6432" not in env


def fake_server(port="32123"):
    return SimpleNamespace(
        stdout=io.StringIO(f"PORT={port}\n"), stdin=io.StringIO(),
        wait=Mock(return_value=0), terminate=Mock(), kill=Mock())


def test_launch_uses_two_declared_interpreters_and_fresh_profiles(declared, monkeypatch):
    runtime, _ = declared
    processes = []

    def create(*args, **kwargs):
        process = fake_server()
        processes.append(process)
        return process

    popen = Mock(side_effect=create)
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout="PASS: fixture\n", stderr=""))
    monkeypatch.setattr(contract.subprocess, "Popen", popen)
    monkeypatch.setattr(contract.subprocess, "run", run)
    for _ in range(2):
        assert contract.launch(runtime) == 0
    flags = ["-E", "-s", "-B", "-X", "utf8"]
    homes = []
    for native, iris in zip(popen.call_args_list, run.call_args_list, strict=True):
        assert native.args[0][:6] == [str(runtime.hermes_python), *flags]
        assert iris.args[0][:6] == [str(runtime.iris_python), *flags]
        assert "--hermes-root" in native.args[0]
        assert "--worker-origin" in iris.args[0]
        assert native.kwargs["cwd"] == str(runtime.hermes_root)
        assert native.kwargs["env"] == iris.kwargs["env"]
        assert native.kwargs["env"]["PATH"] == ""
        assert "PYTHONPATH" not in native.kwargs["env"]
        homes.append(native.kwargs["env"]["HERMES_HOME"])
        assert Path(iris.kwargs["cwd"]).is_relative_to(runtime.work_root)
    assert homes[0] != homes[1]
    for process in processes:
        process.wait.assert_called_once_with(timeout=10)
        process.terminate.assert_not_called()
        assert process.stdout.closed and process.stdin.closed


@pytest.mark.parametrize("failure", ["worker-error", "worker-timeout", "invalid-port", "early-exit"])
def test_launch_always_stops_server_on_failure(declared, monkeypatch, failure):
    runtime, _ = declared
    process = fake_server("invalid" if failure == "invalid-port" else "32123")
    if failure == "early-exit":
        process.stdout = io.StringIO("")
    monkeypatch.setattr(contract.subprocess, "Popen", Mock(return_value=process))
    run = Mock(return_value=SimpleNamespace(returncode=1, stdout="", stderr="fixture failure"))
    if failure == "worker-timeout":
        run.side_effect = subprocess.TimeoutExpired("test-only-worker", 1)
    monkeypatch.setattr(contract.subprocess, "run", run)
    expected = subprocess.TimeoutExpired if failure == "worker-timeout" else RuntimeError
    with pytest.raises(expected):
        contract.launch(runtime)
    process.wait.assert_called_once_with(timeout=10)
    assert process.stdout.closed and process.stdin.closed


def test_hung_server_is_terminated_then_killed():
    process = fake_server()
    process.wait.side_effect = [subprocess.TimeoutExpired("fixture", 10),
                                subprocess.TimeoutExpired("fixture", 5), 0]
    contract._stop_server(process)
    process.terminate.assert_called_once()
    process.kill.assert_called_once()
    assert process.stdout.closed and process.stdin.closed


def test_readiness_timeout_is_bounded():
    unblock = threading.Event()

    class QuietStream:
        def __iter__(self):
            unblock.wait(1)
            return iter(())

    try:
        with pytest.raises(TimeoutError, match="did not become ready"):
            contract._read_port(SimpleNamespace(stdout=QuietStream()), .01)
    finally:
        unblock.set()


@pytest.mark.parametrize("args", [["--unknown-method"], ["--native-server"],
                                   ["--timeout", "0"], ["--timeout", "901"],
                                   ["--worker-origin", "https://example.invalid", "--output", "x"],
                                   ["--worker-origin", "http://127.0.0.1:123"]])
def test_invalid_cli_inputs_fail_before_launch(args, monkeypatch):
    launch = Mock(side_effect=AssertionError("No process must run"))
    monkeypatch.setattr(contract, "launch", launch)
    with pytest.raises(SystemExit) as result:
        contract.main(args)
    assert result.value.code == 2
    launch.assert_not_called()


def test_help_is_read_only_and_never_discovers_or_launches(monkeypatch):
    forbidden = Mock(side_effect=AssertionError("Help cannot run a contract probe"))
    monkeypatch.setattr(contract, "resolve_runtime", forbidden)
    monkeypatch.setattr(contract, "launch", forbidden)
    with pytest.raises(SystemExit) as result:
        contract.main(["--help"])
    assert result.value.code == 0
    forbidden.assert_not_called()


def test_external_socket_connections_are_blocked(monkeypatch):
    import socket

    original = Mock(return_value="local fixture")
    monkeypatch.setattr(socket.socket, "connect", original)
    contract.block_external_network()
    guard = socket.socket.connect
    with pytest.raises(OSError, match="External network disabled"):
        guard(None, ("198.51.100.1", 443))
    assert guard(None, ("127.0.0.1", 12345)) == "local fixture"
    original.assert_called_once_with(None, ("127.0.0.1", 12345))


def test_new_command_documented_options_parse(declared):
    runtime, args = declared
    options = contract.parser().parse_args([*args, "--output", str(runtime.work_root / "report.json")])
    assert contract.resolve_runtime(options).output == runtime.work_root / "report.json"
    assert options.timeout == 90
