"""Local desktop controller. The web UI never receives native-shell permissions.

The shell owns only this controller. Iris and Hermes remain independent services,
so closing a window cannot interrupt Companion, scheduled jobs or messaging.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

from core.companion_auth import COMPANION_TOKEN_HEADER, companion_token_path
from core.config import ENGINE_CONSENT_MESSAGE, IRISConfig, get_iris_home
from core.desktop_theme import DesktopTheme, read_theme_cache, save_theme_cache
from core.process_launcher import _port_open, _read_pid_file, launch_iris, running_iris_pid
from core.runtime_descriptor import (
    RuntimeDescriptorError, RuntimePolicyError, get_runtime,
    runtime_public_info, runtime_service_identity,
)

logger = logging.getLogger("IRIS.desktop")
ROOT = Path(__file__).resolve().parent.parent
SETUP_CODES = {"missing_api_key", "missing_oauth", "hermes_not_found", "security_ack_required"}


def _port_bindable(port: int) -> bool:
    """Whether Core could bind the loopback port right now."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def port_taken(port: int) -> bool:
    """Something answers on the port, or Core could not bind it.

    A connect probe alone misses a program that listens without accepting: its
    queue fills and the port looks free (A08.7). Nothing is closed or stopped.
    """
    return _port_open(port) or not _port_bindable(port)


def candidate_ports(config: IRISConfig) -> list[int]:
    ports = []
    try:
        doc = json.loads((get_iris_home() / "companion_runtime.json").read_text("utf-8"))
        if running_iris_pid(int(doc["pid"])):
            ports.append(int(doc["api_port"]))
    except (OSError, ValueError, TypeError, KeyError):
        pass
    if config.lan_access:
        port = config.companion_loopback_port
        if port == config.websocket_port:
            port = 8766 if port != 8766 else 8767
        ports.append(port)
    ports.append(config.websocket_port)
    return list(dict.fromkeys(p for p in ports if 1024 <= p <= 65535))


async def probe_iris(config: IRISConfig) -> str | None:
    """Identify the actual HTTP service; a listening TCP port is insufficient."""
    runtime = get_runtime()
    expected = runtime_service_identity(get_iris_home()) if runtime.packaged else None
    try:
        token = companion_token_path().read_text("utf-8").strip()
    except OSError:
        return None
    if not token:
        return None
    headers = {"Origin": "http://tauri.localhost", COMPANION_TOKEN_HEADER: token}
    async with httpx.AsyncClient(timeout=1.5, trust_env=False, headers=headers) as client:
        for port in candidate_ports(config):
            base = f"http://127.0.0.1:{port}"
            try:
                response = await client.get(f"{base}/api/status")
                value = response.json()
                if (
                    response.status_code == 200
                    and isinstance(value, dict)
                    and value.get("name") == "IRIS"
                    and value.get("status") == "running"
                    and isinstance(value.get("version"), str)
                    and (expected is None or value.get("runtime") == expected)
                ):
                    return f"{base}/face/"
            except (httpx.HTTPError, ValueError):
                continue
    return None


class DesktopRuntime:
    def __init__(self, *, ready_timeout: float = 120) -> None:
        self.ready_timeout = ready_timeout
        self.task: asyncio.Task | None = None
        self._config: IRISConfig | None = None
        self._next_probe = 0.0
        self._misses = 0
        self.state: dict[str, Any] = {
            "phase": "idle",
            "message": "Tu espacio está listo para comenzar.",
            "face_url": "",
            "reused": False,
            "log_path": "",
        }

    def update(self, phase: str, message: str, **details: Any) -> None:
        self.state.update(phase=phase, message=message, **details)
        if phase == "ready":
            self._next_probe = time.monotonic() + 5
            self._misses = 0

    async def snapshot(self) -> dict[str, Any]:
        if self.state["phase"] == "ready" and self._config is not None:
            now = time.monotonic()
            if now >= self._next_probe:
                self._next_probe = now + 5
                url = await probe_iris(self._config)
                self._misses = 0 if url else self._misses + 1
                if self._misses >= 3:
                    self.update(
                        "error",
                        "Se perdió la conexión con Iris. Vuelve a comprobarla.",
                        face_url="",
                    )
        return {
            **self.state, "visual": await asyncio.to_thread(read_theme_cache),
            "runtime": runtime_public_info(),
        }

    def start(self) -> None:
        # A double click or two tabs share the same in-flight launch.
        if self.task is None or self.task.done():
            self.update("checking", "Comprobando tu configuración…", face_url="")
            self.task = asyncio.create_task(self._start(), name="iris-desktop-start")

    async def _start(self) -> None:
        try:
            await self._start_services()
        except asyncio.CancelledError:
            raise
        except (RuntimeDescriptorError, RuntimePolicyError) as exc:
            self.update("error", str(exc), face_url="")
        except Exception:
            logger.exception("Desktop startup failed")
            self.update(
                "error",
                "No se pudo iniciar Iris. Revisa el registro y vuelve a intentarlo.",
            )
        if self.state["phase"] in {"ready", "setup"}:
            # A08.8: a version that reaches here after an activation is confirmed.
            await asyncio.to_thread(confirm_installation)

    async def _start_services(self) -> None:
        from core.hermes_runtime import resolve_hermes_python
        from wizard.app import start_gateway

        runtime = get_runtime()
        config = await asyncio.to_thread(IRISConfig.load)
        self._config = config
        face_url = await probe_iris(config)
        if not (get_iris_home() / "config.json").is_file() and not face_url:
            self.update("setup", "Primero, configura tu asistente dentro de Iris.")
            return
        if not resolve_hermes_python():
            if runtime.packaged:
                raise RuntimeDescriptorError("Falta el runtime Hermes incluido; no se eligió otra instalación.")
            self.update("setup", "Completa la instalación de Hermes en Configuración.")
            return
        if runtime.packaged and not getattr(config, "hermes_security_acknowledged", False):
            # A08.6: the bundled engine starts only once the profile has accepted
            # Hermes' security notice (asked by the Wizard, stored in config.json).
            # An Iris that is already open is not reused either: Face could
            # start the engine from there. Nothing is started or stopped.
            self.update("setup", ENGINE_CONSENT_MESSAGE)
            return

        self.update("hermes", "Arrancando motores…")
        response = await start_gateway()
        result = json.loads(response.body)
        if runtime.packaged:
            deadline = asyncio.get_running_loop().time() + self.ready_timeout
            while response.status_code == 202 and result.get("code") == "gateway_runtime_pending":
                self.update("hermes", "Los motores siguen calentando…")
                if asyncio.get_running_loop().time() >= deadline:
                    self.update("error", "El motor de IA sigue arrancando. Puedes volver a comprobarlo sin iniciar otra copia.")
                    return
                await asyncio.sleep(0.5)
                response = await start_gateway()
                result = json.loads(response.body)
        if response.status_code >= 400 or result.get("status") not in {
            "ok",
            "success",
            "started",
            "running",
        }:
            self.update(
                "setup" if result.get("code") in SETUP_CODES else "error",
                str(result.get("message") or "El motor de IA todavía no está disponible."),
                log_path=str(result.get("log_path") or ""),
            )
            return

        if face_url:
            self.update("ready", "Iris está conectado.", face_url=face_url, reused=True)
            return

        self.update("iris", "Organizando la habitación…", reused=False)
        pending = await asyncio.to_thread(_read_pid_file)
        if pending is not None and not running_iris_pid(pending.pid):
            pending = None
        if pending is None:
            # A manual terminal launch also publishes companion_runtime.json.
            # Recheck after Hermes starts, before attempting a second Core.
            face_url = await probe_iris(config)
            if face_url:
                self.update("ready", "Iris está conectado.", face_url=face_url, reused=True)
                return
            for port in candidate_ports(config):
                if await asyncio.to_thread(port_taken, port):
                    self.update(
                        "error",
                        f"El puerto {port} está ocupado por otro servicio. "
                        "Revisa los puertos en Configuración.",
                    )
                    return
            pending = await asyncio.to_thread(
                launch_iris,
                port=config.websocket_port,
                wait_for_port_s=0,
            )
        self.state["log_path"] = pending.log_path
        deadline = asyncio.get_running_loop().time() + self.ready_timeout
        while asyncio.get_running_loop().time() < deadline:
            face_url = await probe_iris(config)
            if face_url:
                self.update("ready", "Iris está conectado.", face_url=face_url)
                return
            if not await asyncio.to_thread(running_iris_pid, pending.pid):
                self.update("error", "Iris se detuvo durante el arranque. Revisa el registro.")
                return
            await asyncio.sleep(0.5)
        self.update(
            "error",
            "Iris sigue arrancando y está tardando más de lo esperado. "
            "Puedes volver a comprobarlo sin iniciar otra copia.",
        )

    async def close(self) -> None:
        if self.task and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)


def startup_message(error: BaseException | None) -> str:
    """What the native window shows when the controller cannot start (A08.7)."""
    from core.profile_paths import ProfileBusyError, ProfilePathError

    if isinstance(error, ProfileBusyError):
        return ("Otra versión de Iris está usando este perfil o ya lo actualizó, y esta versión no puede "
                "abrirlo sin riesgo. Cierra la otra versión o abre la versión más reciente de Iris.")
    if isinstance(error, (ProfilePathError, RuntimeDescriptorError, RuntimePolicyError)):
        return str(error)
    if isinstance(error, OSError) and getattr(error, "winerror", None) in {3, 206}:
        return ("No se pudo preparar tu perfil de Iris: su carpeta tiene una ruta demasiado larga para "
                "Windows. Usa una carpeta de perfil más corta.")
    return "No se pudo iniciar el servicio de Iris."


def installation_check(runtime=None) -> str | None:
    """A08.8: reconcile an interrupted activation; only the active version starts."""
    from core import iris_install

    runtime = runtime or get_runtime()
    install = iris_install.find_install(runtime)
    if install is None:
        return None
    try:
        return iris_install.startup_check(install, iris_install.own_version_id(runtime))
    except (iris_install.InstallError, OSError) as exc:
        return f"No se pudo leer el estado de la instalación de Iris: {exc}"


def confirm_installation() -> bool:
    from core import iris_install

    try:
        install = iris_install.find_install()
        return bool(install) and iris_install.confirm_active(install, iris_install.own_version_id())
    except (iris_install.InstallError, OSError):
        logger.exception("Could not confirm the active Iris version")
        return False


def create_desktop_app(runtime: DesktopRuntime | None = None) -> FastAPI:
    context = get_runtime()  # Validate before loading the mounted Wizard.
    from wizard.app import app as wizard_app

    runtime = runtime or DesktopRuntime()

    @asynccontextmanager
    async def lifespan(_app):
        try:
            async with wizard_app.router.lifespan_context(wizard_app):
                yield
                await runtime.close()
        except Exception as exc:
            # uvicorn only logs a failed startup; keep it for the handshake.
            app.state.startup_error = exc
            raise

    app = FastAPI(title="IRIS Desktop", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.runtime = runtime
    app.state.startup_error = None

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        # No DNS names, remote clients, proxy environment or cross-origin writes.
        # The wizard mounted below retains all its own authorization checks.
        origin = request.headers.get("origin")
        if (
            request.url.hostname != "127.0.0.1"
            or not request.client
            or request.client.host not in {"127.0.0.1", "testclient"}
            or (
                request.method not in {"GET", "HEAD", "OPTIONS"}
                and origin != str(request.base_url).rstrip("/")
            )
        ):
            return JSONResponse({"detail": "Local desktop origin required"}, status_code=403)
        response = await call_next(request)
        if request.url.path.startswith("/desktop"):
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Frame-Options"] = "SAMEORIGIN"
            response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response

    @app.get("/desktop/")
    async def desktop_page():
        return FileResponse(context.iris_root / "wizard" / "ui" / "desktop.html")

    @app.get("/desktop/state")
    async def desktop_state():
        return JSONResponse(await runtime.snapshot())

    @app.get("/desktop/theme-contract.js")
    async def theme_contract():
        return FileResponse(context.assets_root / "js" / "os_theme.js", media_type="text/javascript")

    @app.post("/desktop/theme")
    async def cache_theme(theme: DesktopTheme):
        await asyncio.to_thread(save_theme_cache, theme)
        return JSONResponse({"saved": True})

    @app.post("/desktop/start")
    async def desktop_start():
        runtime.start()
        return JSONResponse(runtime.state, status_code=202)

    @app.get("/desktop/fonts/{name}")
    async def desktop_font(name: str):
        if name not in {"outfit-latin-400-normal.woff2", "syne-latin-700-normal.woff2"}:
            return JSONResponse({"detail": "Not found"}, status_code=404)
        return FileResponse(context.assets_root / "companion" / "fonts" / name)

    app.mount("/", wizard_app)
    return app


def wait_for_parent_pipe(stream=None, interval: float = 0.25) -> None:
    """Return once Iris.exe closes the controller's stdin (exit, crash or «Salir»).

    A blocked stdin read froze the controller on Windows when the engine start
    first imported numpy (see wizard.stdin_pipe); the pipe is polled instead.
    """
    from wizard.stdin_pipe import wait_for_eof

    wait_for_eof(stream, interval)


def _handshake(payload: dict[str, Any]) -> None:
    """One JSON line on the private stdout pipe that Iris.exe reads."""
    sys.__stdout__.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.__stdout__.flush()


async def serve() -> bool:
    """Serve until the native parent closes stdin; False if startup failed."""
    import uvicorn

    # Reserve the ephemeral port before announcing it; no find-free-port race.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    sock.setblocking(False)
    port = sock.getsockname()[1]
    app = create_desktop_app()
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="warning",
            access_log=False,
        )
    )
    loop = asyncio.get_running_loop()

    def wait_for_shell() -> None:
        # Native parent closes this private pipe on exit, including crashes.
        # Never kill Iris/Hermes by name or by a stale PID from a previous run.
        wait_for_parent_pipe()
        loop.call_soon_threadsafe(setattr, server, "should_exit", True)

    threading.Thread(target=wait_for_shell, daemon=True).start()
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        while not server.started and not task.done():
            await asyncio.sleep(0.02)
        started = server.started
        if started:
            _handshake({"type": "desktop_server", "url": f"http://127.0.0.1:{port}/desktop/"})
        else:
            # The native parent shows this instead of a bare "stopped" error.
            _handshake({"type": "desktop_error", "message": startup_message(app.state.startup_error)})
        await task
        return started
    finally:
        sock.close()


def main() -> None:
    try:
        refusal = installation_check(get_runtime())
    except (RuntimeDescriptorError, RuntimePolicyError) as exc:
        # Nothing has started; the window explains why (A08.7).
        _handshake({"type": "desktop_error", "message": startup_message(exc)})
        raise SystemExit(1) from exc
    if refusal:
        _handshake({"type": "desktop_error", "message": refusal})
        raise SystemExit(1)
    # stdout is the native parent's small machine-readable handshake only.
    sys.stdout = sys.stderr
    os.environ["PYTHONIOENCODING"] = "utf-8"
    logging.basicConfig(level=logging.INFO)
    if not asyncio.run(serve()):
        # Skip interpreter finalization: the stdin watcher is still blocked in a read.
        logging.shutdown()
        os._exit(1)


if __name__ == "__main__":
    main()
