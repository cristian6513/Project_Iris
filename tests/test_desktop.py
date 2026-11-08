"""Desktop lifecycle tests use isolated profiles and never start the user's agents."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from core import desktop
from core.process_launcher import ProcessHandle


@pytest.fixture
def profile(tmp_path, monkeypatch):
    monkeypatch.setenv("IRIS_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    config = SimpleNamespace(websocket_port=9876, companion_loopback_port=9877, lan_access=True)
    monkeypatch.setattr(desktop.IRISConfig, "load", lambda: config)
    monkeypatch.setattr("core.hermes_runtime.resolve_hermes_python", lambda: "test-python")
    monkeypatch.setattr(desktop, "probe_iris", AsyncMock(return_value=None))
    monkeypatch.setattr(desktop, "_read_pid_file", lambda: None)
    monkeypatch.setattr(desktop, "_port_open", lambda _: False)
    monkeypatch.setattr(desktop, "_port_bindable", lambda _: True)
    monkeypatch.setattr(desktop, "running_iris_pid", lambda _: True)
    return tmp_path, config


@pytest.mark.asyncio
async def test_first_start_opens_configuration_without_launching(profile, monkeypatch):
    gateway = AsyncMock()
    launch = Mock()
    monkeypatch.setattr("wizard.app.start_gateway", gateway)
    monkeypatch.setattr(desktop, "launch_iris", launch)
    runtime = desktop.DesktopRuntime()
    await runtime._start()
    assert runtime.state["phase"] == "setup"
    gateway.assert_not_called()
    launch.assert_not_called()


@pytest.mark.asyncio
async def test_reuses_iris_on_private_http_listener(profile, monkeypatch):
    url = "http://127.0.0.1:9877/face/"
    monkeypatch.setattr(desktop, "probe_iris", AsyncMock(return_value=url))
    monkeypatch.setattr(
        "wizard.app.start_gateway", AsyncMock(return_value=JSONResponse({"status": "running"}))
    )
    launch = Mock()
    monkeypatch.setattr(desktop, "launch_iris", launch)
    runtime = desktop.DesktopRuntime()
    await runtime._start()
    assert runtime.state["face_url"] == url
    assert runtime.state["reused"] is True
    assert runtime.state["phase"] == "ready"
    launch.assert_not_called()


@pytest.mark.asyncio
async def test_missing_oauth_stays_in_setup(profile, monkeypatch):
    (profile[0] / "config.json").write_text("{}")
    monkeypatch.setattr(
        "wizard.app.start_gateway",
        AsyncMock(
            return_value=JSONResponse(
                {
                    "status": "error",
                    "code": "missing_oauth",
                    "message": "Conecta tu cuenta.",
                }
            )
        ),
    )
    launch = Mock()
    monkeypatch.setattr(desktop, "launch_iris", launch)
    runtime = desktop.DesktopRuntime()
    await runtime._start()
    assert runtime.state["phase"] == "setup"
    assert runtime.state["message"] == "Conecta tu cuenta."
    launch.assert_not_called()


@pytest.mark.asyncio
async def test_retry_of_slow_boot_never_spawns_second_core(profile, monkeypatch):
    (profile[0] / "config.json").write_text("{}")
    handle = ProcessHandle(123, 9876, "isolated.log", 1.0)
    monkeypatch.setattr(desktop, "_read_pid_file", lambda: handle)
    monkeypatch.setattr(
        "wizard.app.start_gateway", AsyncMock(return_value=JSONResponse({"status": "running"}))
    )
    launch = Mock()
    monkeypatch.setattr(desktop, "launch_iris", launch)
    runtime = desktop.DesktopRuntime(ready_timeout=0)
    await runtime._start()
    assert runtime.state["phase"] == "error"
    assert runtime.state["log_path"] == "isolated.log"
    monkeypatch.setattr(
        desktop, "probe_iris", AsyncMock(side_effect=[None, "http://127.0.0.1:9877/face/"])
    )
    runtime.ready_timeout = 1
    await runtime._start()
    assert runtime.state["phase"] == "ready"
    launch.assert_not_called()


@pytest.mark.asyncio
async def test_the_start_screen_speaks_of_engines_and_the_room_not_hermes(profile, monkeypatch):
    # QA12: the welcome screen names IRIS's own steps; Hermes stays in About and diagnostics.
    (profile[0] / "config.json").write_text("{}")
    handle = ProcessHandle(123, 9876, "isolated.log", 1.0)
    monkeypatch.setattr(desktop, "_read_pid_file", lambda: handle)
    monkeypatch.setattr(
        desktop, "probe_iris", AsyncMock(side_effect=[None, "http://127.0.0.1:9877/face/"])
    )
    monkeypatch.setattr(
        "wizard.app.start_gateway", AsyncMock(return_value=JSONResponse({"status": "running"}))
    )
    runtime = desktop.DesktopRuntime()
    messages = []
    update = runtime.update
    monkeypatch.setattr(runtime, "update", lambda *a, **kw: (messages.append(a[1]), update(*a, **kw)))
    await runtime._start()
    assert runtime.state["phase"] == "ready"
    assert messages[:2] == ["Arrancando motores…", "Organizando la habitación…"]
    page = (Path(__file__).resolve().parents[1] / "wizard/ui/desktop.html").read_text(encoding="utf-8")
    welcome = page[page.index('<main id="welcome">'):page.index("</main>")]
    assert "Arrancando motores" in welcome and "Organizando la habitación" in welcome
    assert "Hermes" not in welcome and "Gateway" not in welcome
    assert not any("Hermes" in message or "Gateway" in message for message in messages)


@pytest.mark.asyncio
async def test_port_conflict_is_not_a_success(profile, monkeypatch):
    (profile[0] / "config.json").write_text("{}")
    monkeypatch.setattr(desktop, "_port_open", lambda _: True)
    monkeypatch.setattr(
        "wizard.app.start_gateway", AsyncMock(return_value=JSONResponse({"status": "running"}))
    )
    launch = Mock()
    monkeypatch.setattr(desktop, "launch_iris", launch)
    runtime = desktop.DesktopRuntime()
    await runtime._start()
    assert runtime.state["phase"] == "error"
    assert "9877" in runtime.state["message"]
    launch.assert_not_called()


@pytest.mark.asyncio
async def test_double_click_shares_launch_task(profile, monkeypatch):
    runtime = desktop.DesktopRuntime()
    job = AsyncMock()
    monkeypatch.setattr(runtime, "_start_services", job)
    runtime.start()
    first = runtime.task
    runtime.start()
    assert runtime.task is first
    await first
    job.assert_awaited_once()


@pytest.mark.asyncio
async def test_shutdown_only_cancels_controller_task(profile, monkeypatch):
    runtime = desktop.DesktopRuntime()
    monkeypatch.setattr(runtime, "_start_services", lambda: asyncio.sleep(30))
    runtime.start()
    await runtime.close()
    assert runtime.task.cancelled()


def test_desktop_http_rejects_remote_origin_and_dns_rebinding(profile):
    runtime = desktop.DesktopRuntime()
    runtime.start = Mock()
    with TestClient(
        desktop.create_desktop_app(runtime), base_url="http://127.0.0.1:9123"
    ) as client:
        assert client.get("/desktop/state").status_code == 200
        for origin in [None, "https://evil.test", "http://127.0.0.1:9000"]:
            headers = {"Origin": origin} if origin else {}
            assert client.post("/desktop/start", headers=headers).status_code == 403
        assert client.get("/desktop/state", headers={"Host": "evil.test"}).status_code == 403
        response = client.post("/desktop/start", headers={"Origin": "http://127.0.0.1:9123"})
        assert response.status_code == 202
        assert response.headers["cache-control"] == "no-store"
        runtime.start.assert_called_once()


def test_runtime_descriptor_requires_an_iris_pid(tmp_path, monkeypatch):
    monkeypatch.setenv("IRIS_HOME", str(tmp_path))
    (tmp_path / "companion_runtime.json").write_text(json.dumps({"pid": 12, "api_port": 9009}))
    config = SimpleNamespace(lan_access=True, websocket_port=8766, companion_loopback_port=8766)
    monkeypatch.setattr(desktop, "running_iris_pid", lambda _: False)
    assert desktop.candidate_ports(config) == [8767, 8766]
    monkeypatch.setattr(desktop, "running_iris_pid", lambda _: True)
    assert desktop.candidate_ports(config) == [9009, 8767, 8766]


@pytest.mark.asyncio
async def test_http_probe_rejects_other_service_and_uses_private_listener(monkeypatch, tmp_path):
    monkeypatch.setenv("IRIS_HOME", str(tmp_path))
    (tmp_path / "companion_token").write_text("test-profile-token")
    monkeypatch.setattr(desktop, "candidate_ports", lambda _: [1234, 1235, 1236])

    def handler(request):
        if request.url.port == 1234:
            return httpx.Response(200, text="<html>unrelated server</html>")
        name = "Other app" if request.url.port == 1235 else "IRIS"
        return httpx.Response(200, json={"name": name, "status": "running", "version": "1.0"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(desktop.httpx, "AsyncClient", lambda **_: client)
    assert await desktop.probe_iris(None) == "http://127.0.0.1:1236/face/"


def test_recycled_pid_does_not_count_as_iris(monkeypatch):
    from core.process_launcher import running_iris_pid

    monkeypatch.setattr(
        "psutil.Process", lambda _: SimpleNamespace(cmdline=lambda: ["python", "other.py"])
    )
    assert not running_iris_pid(123)
    monkeypatch.setattr(
        "psutil.Process", lambda _: SimpleNamespace(cmdline=lambda: ["python", "-m", "core.agent"])
    )
    assert running_iris_pid(123)


def test_start_lock_excludes_second_launcher_and_releases_after_exception(profile):
    from core.service_start_lock import service_start_lock

    with pytest.raises(ValueError):
        with service_start_lock("iris"):
            with pytest.raises(TimeoutError):
                with service_start_lock("iris", timeout=0):
                    pytest.fail("second launcher acquired the lock")
            raise ValueError("failure during startup")
    with service_start_lock("iris", timeout=0):
        pass


@pytest.mark.asyncio
async def test_async_lock_releases_after_cancellation(profile):
    from core.service_start_lock import async_service_start_lock

    acquired = asyncio.Event()

    async def hold():
        async with async_service_start_lock("hermes"):
            acquired.set()
            await asyncio.sleep(30)

    task = asyncio.create_task(hold())
    await acquired.wait()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    async with async_service_start_lock("hermes", timeout=0):
        pass


# ── A08.7: a refused startup is explained in the native window ─────────────

def test_startup_messages_explain_profile_and_path_failures():
    from core.profile_paths import ProfileBusyError, ProfilePathError

    assert "Otra versión de Iris" in desktop.startup_message(ProfileBusyError("x"))
    assert desktop.startup_message(ProfilePathError("Perfil inválido.")) == "Perfil inválido."
    too_long = OSError(2, "path")
    too_long.winerror = 3
    assert "ruta demasiado larga" in desktop.startup_message(too_long)
    assert desktop.startup_message(RuntimeError("detalle interno")) == "No se pudo iniciar el servicio de Iris."
    assert desktop.startup_message(None) == "No se pudo iniciar el servicio de Iris."


def test_failed_wizard_startup_is_reported_in_the_handshake(profile, monkeypatch):
    import contextlib
    import io

    from core.profile_paths import ProfileBusyError
    from wizard.app import app as wizard_app

    @contextlib.asynccontextmanager
    async def refused(_app):
        raise ProfileBusyError("Este proceso no puede escribir el formato del perfil.")
        yield  # pragma: no cover

    monkeypatch.setattr(wizard_app.router, "lifespan_context", refused)
    output = io.StringIO()
    monkeypatch.setattr(desktop.sys, "__stdout__", output)
    monkeypatch.setattr(desktop.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(b"")))
    asyncio.run(desktop.serve())
    hello = json.loads(output.getvalue().splitlines()[0])
    assert hello["type"] == "desktop_error" and "Otra versión de Iris" in hello["message"]


def test_rejected_descriptor_is_reported_before_anything_starts(monkeypatch):
    import io

    output = io.StringIO()
    monkeypatch.setattr(desktop.sys, "__stdout__", output)
    monkeypatch.setattr(desktop, "get_runtime", Mock(side_effect=desktop.RuntimeDescriptorError(
        "Iris está en una carpeta con una ruta demasiado larga para Windows (88 caracteres; máximo 84).")))
    serve = Mock()
    monkeypatch.setattr(desktop, "serve", serve)
    with pytest.raises(SystemExit):
        desktop.main()
    hello = json.loads(output.getvalue())
    assert hello == {"type": "desktop_error",
                     "message": "Iris está en una carpeta con una ruta demasiado larga para Windows (88 caracteres; máximo 84)."}
    serve.assert_not_called()


def test_a_version_that_is_not_active_refuses_to_start(monkeypatch):
    import io

    output = io.StringIO()
    monkeypatch.setattr(desktop.sys, "__stdout__", output)
    monkeypatch.setattr(desktop, "get_runtime", lambda: SimpleNamespace(packaged=True))
    monkeypatch.setattr(desktop, "installation_check", lambda runtime: "Esta copia (Iris 1.0.0) no es la versión activa")
    serve = Mock()
    monkeypatch.setattr(desktop, "serve", serve)
    with pytest.raises(SystemExit):
        desktop.main()
    assert json.loads(output.getvalue()) == {"type": "desktop_error",
                                             "message": "Esta copia (Iris 1.0.0) no es la versión activa"}
    serve.assert_not_called()


def test_installation_check_is_silent_outside_a_versioned_installation(monkeypatch):
    from core import iris_install

    monkeypatch.setattr(iris_install, "find_install", lambda runtime: None)
    assert desktop.installation_check(SimpleNamespace(packaged=True)) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("phase,confirmed", [("ready", True), ("setup", True), ("error", False)])
async def test_reaching_ready_confirms_a_new_version(profile, monkeypatch, phase, confirmed):
    confirm = Mock(return_value=True)
    monkeypatch.setattr(desktop, "confirm_installation", confirm)
    runtime = desktop.DesktopRuntime()

    async def start():
        runtime.update(phase, "fixture")

    monkeypatch.setattr(runtime, "_start_services", start)
    await runtime._start()
    assert confirm.called is confirmed


_PIPE_CHILD = """
import ctypes, sys, threading, time
sys.path.insert(0, sys.argv[1])
from core.desktop import wait_for_parent_pipe
closed = threading.Event()
threading.Thread(target=lambda: (wait_for_parent_pipe(interval=0.05), closed.set()), daemon=True).start()
time.sleep(0.5)
start = time.monotonic()
# What OpenBLAS's runtime does with the standard handles while numpy loads it.
info = ctypes.create_string_buffer(64)
ctypes.windll.kernel32.GetFileInformationByHandle(ctypes.windll.kernel32.GetStdHandle(-10), info)
print(f"queried in {time.monotonic() - start:.2f}s", flush=True)
print("closed" if closed.wait(20) else "still open", flush=True)
"""


@pytest.mark.skipif(desktop.os.name != "nt", reason="Windows serializes synchronous I/O on a pipe handle")
def test_parent_pipe_watch_does_not_stall_other_stdin_queries():
    # A08.7: a thread blocked in stdin.read() made numpy's OpenBLAS load (via
    # core.audio_decoder, when the engine starts) wait forever, freezing the
    # controller. The watcher must leave no read pending and still see the close.
    import subprocess
    import sys
    import threading
    from pathlib import Path

    repo = str(Path(__file__).resolve().parents[1])
    child = subprocess.Popen([sys.executable, "-c", _PIPE_CHILD, repo], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    lines = []

    def read():
        for line in child.stdout:
            lines.append(line.decode().strip())

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    try:
        deadline = 30
        while deadline > 0 and not lines:
            reader.join(0.5)
            deadline -= 0.5
        assert lines and lines[0].startswith("queried in"), (lines, child.poll())
        child.stdin.close()  # Iris.exe exiting
        reader.join(25)
        assert lines[1:] == ["closed"], lines
    finally:
        child.kill()


def test_parent_pipe_watch_falls_back_to_reading_a_non_pipe_stdin():
    import io

    stream = SimpleNamespace(buffer=io.BytesIO(b"bye"))
    desktop.wait_for_parent_pipe(stream)  # returns at EOF, like the former read
    assert stream.buffer.read() == b""


def test_a_listener_that_never_accepts_still_counts_as_taken(monkeypatch):
    # A08.7 smoke: a program bound to Core's port with a full accept queue
    # looked closed to the connect probe, and Core was launched only to fail.
    import socket

    monkeypatch.setattr(desktop, "_port_open", lambda _: False)
    with socket.socket() as blocker:
        blocker.bind(("127.0.0.1", 0))
        blocker.listen(1)
        port = blocker.getsockname()[1]
        assert desktop.port_taken(port) is True
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        free = probe.getsockname()[1]
    assert desktop.port_taken(free) is False


@pytest.mark.asyncio
async def test_unbindable_core_port_is_reported_before_launching(profile, monkeypatch):
    (profile[0] / "config.json").write_text("{}")
    monkeypatch.setattr(desktop, "_port_bindable", lambda port: port != 9876)
    monkeypatch.setattr("wizard.app.start_gateway", AsyncMock(return_value=JSONResponse({"status": "running"})))
    launch = Mock()
    monkeypatch.setattr(desktop, "launch_iris", launch)
    runtime = desktop.DesktopRuntime()
    await runtime._start()
    assert runtime.state["phase"] == "error" and "9876" in runtime.state["message"]
    launch.assert_not_called()
