"""
IRIS Process Launcher — Cross-platform detached spawn

Lets the wizard start and stop the main IRIS agent without opening a
terminal. Two design constraints we learned the hard way:

  1. **Truly detached.** Closing the wizard window must not kill IRIS.
     On Windows that means ``CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW``
     (its own hidden console, which the Hermes programs it starts inherit);
     on macOS/Linux it means ``start_new_session=True``. Without these
     flags the OS attaches IRIS to the wizard's process group and
     SIGHUP / WM_CLOSE cascades into IRIS when the wizard quits.

  2. **PID alone isn't enough.** A stale PID file from a previous boot
     can collide with an unrelated process that happens to occupy the
     same number. We therefore probe the websocket port (8765 by
     default) before declaring IRIS "running" — if the PID is alive
     **and** the port responds, it's our process.

Logs are written to ``~/.iris/logs/agent-YYYY-MM-DD.log`` (one file per
calendar day, rotated by date). The wizard never tails the log file in
v1; users open it manually if something looks wrong. (The "console
panel in wizard" idea was deferred per the master plan.)
"""

from __future__ import annotations

import json
import logging
import math
import os
import platform
import signal
import socket
import stat
import subprocess
import sys
import time
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from core.config import get_iris_home
from core.runtime_descriptor import (
    RuntimePolicyError, get_runtime, runtime_service_identity, runtime_subprocess_env,
)

logger = logging.getLogger("IRIS.process_launcher")
MAX_PID_BYTES = 16 * 1024


# ════════════════════════════════════════════════════════════
# Paths & constants
# ════════════════════════════════════════════════════════════

# PID file. We use a JSON sidecar instead of a bare PID so we can also
# remember the port, log path, and start time without re-querying the
# OS each time the wizard polls /api/IRIS-status.
def _pid_file() -> Path:
    return get_iris_home() / "IRIS.pid.json"


def _logs_dir() -> Path:
    p = get_iris_home() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _today_log_path() -> Path:
    return _logs_dir() / f"agent-{datetime.now().strftime('%Y-%m-%d')}.log"


# ════════════════════════════════════════════════════════════
# Public dataclass
# ════════════════════════════════════════════════════════════

@dataclass
class ProcessHandle:
    """Snapshot of a running IRIS process — written to PID file and
    returned to API callers verbatim."""

    pid: int
    port: int
    log_path: str
    started_at: float  # epoch seconds, ``time.time()``

    def to_json(self) -> dict[str, int | str | float]:
        return asdict(self)


# ════════════════════════════════════════════════════════════
# Probes — "is IRIS really up?"
# ════════════════════════════════════════════════════════════

def _pid_alive(pid: int) -> bool:
    """Return True if ``pid`` corresponds to a live process.

    We don't try to verify it's *our* process here — that's what the
    port probe is for. ``os.kill(pid, 0)`` is the standard POSIX
    cheap-liveness check; on Windows we use ``OpenProcess`` via
    ctypes.
    """
    if pid <= 0:
        return False
    if platform.system() == "Windows":
        # Avoid spawning a subprocess just to check liveness — open the
        # process handle directly. PROCESS_QUERY_LIMITED_INFORMATION is
        # the lowest-privilege right that lets us call GetExitCodeProcess.
        import ctypes
        from ctypes import wintypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid,
        )
        if not handle:
            return False
        try:
            exit_code = wintypes.DWORD()
            ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            return bool(ok) and exit_code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    else:
        try:
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, PermissionError):
            return False


def _port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.3) -> bool:
    """TCP connect probe — does *something* listen on ``host:port``?"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, socket.timeout):
        return False


# ════════════════════════════════════════════════════════════
# PID file IO
# ════════════════════════════════════════════════════════════

def _unique_pid_fields(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("Duplicate receipt field")
        result[name] = value
    return result


def _read_packaged_pid_data() -> dict | None:
    """A receipt is evidence only when it is a bounded, ordinary profile file."""
    path = _pid_file()
    try:
        before = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise RuntimePolicyError("No se pudo acreditar el registro del proceso Iris.") from exc
    try:
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or getattr(before, "st_file_attributes", 0) & 0x400
                or before.st_size > MAX_PID_BYTES):
            raise ValueError("Not a plain receipt")
        with path.open("rb") as handle:
            opened = os.fstat(handle.fileno())
            raw = handle.read(MAX_PID_BYTES + 1)
        after = path.lstat()
        fingerprint = lambda item: (item.st_dev, item.st_ino)
        if (fingerprint(before) != fingerprint(opened) or fingerprint(opened) != fingerprint(after)
                or opened.st_nlink != 1 or len(raw) > MAX_PID_BYTES):
            raise ValueError("Receipt changed during read")
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pid_fields)
        if (not isinstance(data, dict)
                or set(data) != {"pid", "port", "log_path", "started_at", "runtime", "process_created_at"}
                or type(data["pid"]) is not int or not 0 < data["pid"] <= 0xFFFFFFFF
                or type(data["port"]) is not int or not 1024 <= data["port"] <= 65535
                or not isinstance(data["log_path"], str)
                or not isinstance(data["runtime"], dict)
                or any(type(data[key]) not in {int, float} or not math.isfinite(data[key])
                       or data[key] <= 0 for key in ["started_at", "process_created_at"])):
            raise ValueError("Invalid receipt fields")
        return data
    except (OSError, ValueError, TypeError, OverflowError, UnicodeError, RecursionError) as exc:
        raise RuntimePolicyError("El registro del proceso Iris no está acreditado; se conservó sin iniciar otra copia.") from exc


def _read_pid_file() -> ProcessHandle | None:
    runtime = get_runtime()
    path = _pid_file()
    data = _read_packaged_pid_data() if runtime.packaged else None
    if runtime.packaged and data is None:
        return None
    if not runtime.packaged and not path.exists():
        return None
    try:
        if not runtime.packaged:
            data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        if runtime.packaged and data.get("runtime") != runtime_service_identity(get_iris_home()):
            if _pid_alive(int(data["pid"])):
                raise RuntimePolicyError("Otro runtime de Iris está usando el perfil; no se reutilizó su proceso.")
            return None
        return ProcessHandle(
            pid=int(data["pid"]),
            port=int(data["port"]),
            log_path=str(data["log_path"]),
            started_at=float(data["started_at"]),
        )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        logger.warning("Stale/unreadable PID file at %s: %s", path, exc)
        return None


def _write_pid_file(handle: ProcessHandle) -> None:
    data = handle.to_json()
    if get_runtime().packaged:
        import psutil

        data["runtime"] = runtime_service_identity(get_iris_home())
        data["process_created_at"] = psutil.Process(handle.pid).create_time()
        _read_packaged_pid_data()  # Never truncate an alias or an unverified receipt.
        target = _pid_file()
        descriptor, temporary = tempfile.mkstemp(prefix=".iris-pid-", suffix=".json", dir=target.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(data, output, indent=2)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, target)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
        return
    _pid_file().write_text(
        json.dumps(data, indent=2),
        encoding="utf-8",
    )


def _clear_pid_file() -> None:
    p = _pid_file()
    if p.exists():
        try:
            p.unlink()
        except OSError as exc:
            logger.warning("Failed to remove %s: %s", p, exc)


# ════════════════════════════════════════════════════════════
# Public API
# ════════════════════════════════════════════════════════════

def is_iris_running() -> ProcessHandle | None:
    """Return the live ``ProcessHandle`` if IRIS is up, else None.

    Both PID liveness AND port-open must hold. If the PID file points
    to a dead PID (e.g. crash/reboot), we wipe it so subsequent
    callers don't keep tripping over the stale entry.
    """
    runtime = get_runtime()
    handle = _read_pid_file()
    if handle is None:
        return None
    alive = _pid_alive(handle.pid)
    listening = _port_open(handle.port)
    if alive and listening:
        if runtime.packaged and not _packaged_iris_ready(handle.pid, handle.port):
            return None
        return handle
    # Stale — clean up so the wizard shows "stopped" cleanly.
    if not alive:
        logger.info("PID %d no longer alive — clearing stale PID file", handle.pid)
        _clear_handle_pid_record(handle) if runtime.packaged else _clear_pid_file()
    return None


def running_iris_pid(pid: int) -> bool:
    """Do not trust a recycled PID, or mistake another Python for Iris."""
    import psutil

    runtime = get_runtime()
    if runtime.packaged:
        return _owned_core_process(pid) is not None
    try:
        process = psutil.Process(pid)
        args = process.cmdline()
        return any(args[i:i + 2] == ["-m", "core.agent"] for i in range(len(args)))
    except (psutil.Error, ValueError):
        return False


def _packaged_core_flags() -> list[str]:
    from core.runtime_descriptor import runtime_bytecode_flags

    return ["-E", "-s", *runtime_bytecode_flags(), "-X", "utf8", "-u"]


def _owned_core_process(pid: int):
    """Retain the process instance so psutil also detects PID reuse on signals."""
    import psutil
    from core.workspace_sync import get_hermes_home
    from core.runtime_descriptor import DESCRIPTOR_ENV

    runtime = get_runtime()
    try:
        process = psutil.Process(pid)
        args = process.cmdline()
        process_env = {key.upper(): value for key, value in process.environ().items()}
        data = _read_packaged_pid_data()
        valid = (
            isinstance(data, dict)
            and type(data.get("pid")) is int
            and data.get("pid") == pid
            and data.get("runtime") == runtime_service_identity(get_iris_home())
            and type(data.get("process_created_at")) in {int, float}
            and abs(process.create_time() - data["process_created_at"]) < 0.001
            and Path(process.exe()).resolve(strict=True) == runtime.iris_python
            and Path(process.cwd()).resolve(strict=True) == runtime.iris_root
            and bool(args)
            and Path(args[0]).resolve(strict=True) == runtime.iris_python
            and args[1:] == _packaged_core_flags() + ["-m", "core.agent"]
            and bool(process_env.get("IRIS_HOME"))
            and Path(process_env["IRIS_HOME"]).resolve(strict=False) == get_iris_home().resolve(strict=False)
            and bool(process_env.get("HERMES_HOME"))
            and Path(process_env["HERMES_HOME"]).resolve(strict=False) == get_hermes_home().resolve(strict=False)
            and bool(process_env.get(DESCRIPTOR_ENV))
            and Path(process_env[DESCRIPTOR_ENV]).resolve(strict=True) == runtime.descriptor_path
        )
        return process if valid else None
    except (psutil.Error, ValueError, OSError, TypeError, RuntimeError):
        return None


def _packaged_iris_ready(pid: int, port: int) -> bool:
    import httpx
    from core.companion_auth import COMPANION_TOKEN_HEADER, companion_token_path

    if not running_iris_pid(pid) or not 1024 <= port <= 65535:
        return False
    try:
        token = companion_token_path().read_text("utf-8").strip()
        if not token:
            return False
        with httpx.Client(timeout=0.5, trust_env=False) as client:
            response = client.get(
                f"http://127.0.0.1:{port}/api/status",
                headers={COMPANION_TOKEN_HEADER: token, "Origin": "http://tauri.localhost"},
            )
            value = response.json()
        return (
            response.status_code == 200 and isinstance(value, dict)
            and value.get("name") == "IRIS" and value.get("status") == "running"
            and value.get("runtime") == runtime_service_identity(get_iris_home())
        )
    except (OSError, ValueError, httpx.HTTPError):
        return False


def launch_iris(
    config: object | None = None,
    *,
    project_root: Path | None = None,
    port: int | None = None,
    wait_for_port_s: float = 6.0,
) -> ProcessHandle:
    from core.config import IRISConfig
    from core.service_start_lock import service_start_lock

    get_runtime()  # Validate before acquiring a profile lock or probing services.
    with service_start_lock("iris"):
        if port is None:
            port = IRISConfig.load().websocket_port
        return _launch_iris_unlocked(
            config, project_root=project_root, port=port, wait_for_port_s=wait_for_port_s,
        )


def _launch_iris_unlocked(
    config: object | None = None,
    *,
    project_root: Path | None = None,
    port: int = 8765,
    wait_for_port_s: float = 6.0,
) -> ProcessHandle:
    """Spawn ``python -m core.agent`` detached from the wizard.

    Args:
        config: Reserved (a future iteration may pass env-var overrides
            from the wizard's in-memory config without round-tripping
            through ``~/.iris/config.json``). Currently unused — the
            child reads its own config on boot.
        project_root: Directory to run the child from. Defaults to the
            repo root inferred from this file's location, which is
            correct for ``python -m core.agent`` to resolve.
        port: WebSocket port we expect IRIS to bind. Used solely for
            the post-spawn readiness probe; the child reads the actual
            port from its config.
        wait_for_port_s: How long to poll the port before giving up on
            the readiness check. We *do not* kill the process if the
            check times out — IRIS may simply be slow on the first
            boot (Whisper model download, etc.); we just return the
            handle without "ready" guarantees so the UI can show
            "starting" instead of "running".

    Raises:
        RuntimeError: if IRIS is already running (caller should call
            ``stop_iris`` first or just no-op).

    Returns:
        ``ProcessHandle`` written to the PID file. The ``port`` field
        reflects the requested port; the "is it actually listening
        yet?" answer is whatever ``is_iris_running()`` says next.
    """
    runtime = get_runtime()
    if runtime.packaged and project_root and Path(project_root).resolve(strict=True) != runtime.iris_root:
        raise RuntimePolicyError("No se puede sustituir la raíz del runtime incluido.")
    existing = is_iris_running()
    if existing is not None:
        raise RuntimeError(
            f"IRIS already running (PID {existing.pid}, port {existing.port})"
        )

    # A slow boot still owns its PID: retrying must not spawn a second Core.
    pending = _read_pid_file()
    if pending is not None:
        if running_iris_pid(pending.pid):
            return pending
        if runtime.packaged and _pid_alive(pending.pid):
            from core.service_start_lock import recorded_process_gone

            data = _read_packaged_pid_data()
            # A reboot can leave a receipt whose PID now belongs to another
            # application. Keep the receipt until a successful spawn replaces it.
            if (data is None or data["pid"] != pending.pid or data["port"] != pending.port
                    or data["started_at"] != pending.started_at
                    or data["runtime"] != runtime_service_identity(get_iris_home())
                    or not recorded_process_gone(pending.pid, data["process_created_at"])
                    or _port_open(pending.port) or (port != pending.port and _port_open(port))):
                raise RuntimePolicyError(
                    "El proceso guardado no pertenece a este runtime; no se inició otra copia."
                )

    root = runtime.iris_root if runtime.packaged else (project_root or runtime.iris_root)
    python = runtime.iris_python if runtime.descriptor_path else Path(sys.executable)
    log_path = _today_log_path()
    # Append mode so the same daily file accumulates restarts. The user
    # typically only cares about the most recent boot, but having the
    # full day's history lets us correlate crashes with whatever
    # happened earlier.
    log_fh = open(log_path, "a", buffering=1, encoding="utf-8", errors="replace")
    log_fh.write(
        f"\n{'=' * 60}\n"
        f"IRIS launch at {datetime.now().isoformat(timespec='seconds')}\n"
        f"cwd={root}\npython={python}\n"
        f"{'=' * 60}\n"
    )
    log_fh.flush()

    if runtime.packaged:
        from core.runtime_descriptor import prune_runtime_pycache

        try:
            prune_runtime_pycache()
        except OSError:
            pass  # A stale cache only costs disk space.
    flags = _packaged_core_flags() if runtime.packaged else []
    cmd = [str(python), *flags, "-m", "core.agent"]
    env = runtime_subprocess_env()
    if runtime.packaged:
        from core.workspace_sync import get_hermes_home

        env["IRIS_HOME"] = str(get_iris_home().resolve())
        env["HERMES_HOME"] = str(get_hermes_home().resolve())

    # Platform-specific detach flags. Without these, killing the wizard
    # cascades into IRIS — see module docstring.
    creationflags = 0
    start_new_session = False
    if platform.system() == "Windows":
        # CREATE_NEW_PROCESS_GROUP lets us send Ctrl+Break to the group
        # without affecting the parent. CREATE_NO_WINDOW gives IRIS its own
        # console without a window: closing the wizard window cannot reach
        # it, and every console program IRIS starts (Hermes commands and
        # bridges) inherits that hidden console. With DETACHED_PROCESS
        # instead IRIS had no console, and Windows opened a visible window
        # for each of those programs (qa9).
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        CREATE_NO_WINDOW = 0x08000000
        creationflags = CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
    else:
        # POSIX: new session detaches from the parent's process group
        # so SIGHUP from the wizard doesn't propagate.
        start_new_session = True

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(root),
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            close_fds=True,
            creationflags=creationflags,
            start_new_session=start_new_session,
            env=env,
        )
    except OSError as exc:
        log_fh.close()
        raise RuntimeError(f"Failed to spawn IRIS: {exc}") from exc

    # We deliberately do NOT close log_fh — Popen has dup'd the FD into
    # the child. Keeping our handle open lets the GC keep the file
    # object around; closing it here would close the parent's view but
    # that's harmless. We close it explicitly to avoid a resource
    # warning when the wizard process eventually exits.
    log_fh.close()

    handle = ProcessHandle(
        pid=proc.pid,
        port=port,
        log_path=str(log_path),
        started_at=time.time(),
    )
    try:
        _write_pid_file(handle)
    except Exception:
        if runtime.packaged:
            proc.terminate()  # Only the process object spawned by this call.
        raise

    # Readiness probe — best effort. We poll quickly because the wizard
    # UI is waiting on us synchronously.
    deadline = time.time() + wait_for_port_s
    while time.time() < deadline:
        ready = _packaged_iris_ready(proc.pid, port) if runtime.packaged else _port_open(port)
        if ready:
            logger.info("IRIS ready on port %d (PID %d)", port, proc.pid)
            return handle
        # If the child died during boot, surface that immediately
        # rather than waiting out the full timeout.
        if proc.poll() is not None:
            _clear_handle_pid_record(handle) if runtime.packaged else _clear_pid_file()
            raise RuntimeError(
                f"IRIS exited during boot (code {proc.returncode}). "
                f"Check {log_path}"
            )
        time.sleep(0.2)

    # Timed out waiting for the port — but the process is alive, so
    # return the handle anyway. The wizard's status badge will catch
    # up on its next poll.
    logger.warning(
        "IRIS spawned (PID %d) but port %d not listening after %.1fs",
        proc.pid, port, wait_for_port_s,
    )
    return handle


def _clear_owned_pid_record(pid: int, created_at: float) -> None:
    path = _pid_file()
    try:
        data = _read_packaged_pid_data()
        if (
            isinstance(data, dict) and data.get("pid") == pid
            and data.get("process_created_at") == created_at
            and data.get("runtime") == runtime_service_identity(get_iris_home())
        ):
            path.unlink(missing_ok=True)
    except (OSError, ValueError, RuntimePolicyError):
        pass


def _clear_handle_pid_record(handle: ProcessHandle) -> None:
    try:
        data = _read_packaged_pid_data()
        if (data is not None and data["pid"] == handle.pid and data["started_at"] == handle.started_at
                and data["runtime"] == runtime_service_identity(get_iris_home())):
            _clear_owned_pid_record(handle.pid, data["process_created_at"])
    except RuntimePolicyError:
        pass


def _stop_packaged_iris(pid: int | None, timeout_s: float) -> bool:
    import psutil
    from core.service_start_lock import service_start_lock

    with service_start_lock("iris"):
        if pid is None:
            handle = _read_pid_file()
            if handle is None:
                return False
            pid = handle.pid
        process = _owned_core_process(pid)
        if process is None:
            if _pid_alive(pid):
                raise RuntimePolicyError("El proceso no pertenece a este runtime; no se detuvo.")
            return False
        created_at = process.create_time()
        try:
            process.terminate()
            try:
                process.wait(timeout=timeout_s)
            except psutil.TimeoutExpired:
                # Recheck registry/identity while retaining the original psutil
                # instance, whose signal methods also detect recycled PIDs.
                current = _owned_core_process(pid)
                if current is None or current.create_time() != created_at:
                    raise RuntimePolicyError("La identidad del proceso cambió; no se terminó otro proceso.")
                process.kill()
                process.wait(timeout=timeout_s)
        except psutil.NoSuchProcess:
            pass
        _clear_owned_pid_record(pid, created_at)
        return True


def stop_iris(pid: int | None = None, *, timeout_s: float = 5.0) -> bool:
    """Terminate IRIS and clear the PID file.

    Args:
        pid: Override PID. If ``None`` (default), reads from the PID
            file. Explicit override exists for tests / recovery from
            a desynced PID file.
        timeout_s: How long to wait for graceful shutdown before
            escalating to a hard kill.

    Returns:
        True if a process was actually terminated; False if nothing
        was running (no PID file or PID was already dead). Either way
        the PID file ends up cleared.
    """
    runtime = get_runtime()
    if runtime.packaged:
        return _stop_packaged_iris(pid, timeout_s)
    if pid is None:
        handle = _read_pid_file()
        if handle is None:
            return False
        pid = handle.pid

    if not _pid_alive(pid):
        _clear_pid_file()
        return False

    # Graceful first.
    try:
        if platform.system() == "Windows":
            # CTRL_BREAK_EVENT only works on processes in a different
            # process group, which is exactly what CREATE_NEW_PROCESS_GROUP
            # gave us at launch. If the user
            # somehow killed the parent and re-attached, we fall back
            # to TerminateProcess via os.kill.
            try:
                os.kill(pid, signal.CTRL_BREAK_EVENT)
            except (OSError, AttributeError):
                # CTRL_BREAK_EVENT may not exist on every Python build;
                # SIGTERM behaves like TerminateProcess on Windows.
                os.kill(pid, signal.SIGTERM)
        else:
            os.kill(pid, signal.SIGTERM)
    except (OSError, ProcessLookupError) as exc:
        logger.warning("SIGTERM to PID %d failed: %s", pid, exc)

    # Wait for graceful exit.
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if not _pid_alive(pid):
            _clear_pid_file()
            return True
        time.sleep(0.1)

    # Hard kill.
    logger.warning("PID %d didn't exit in %.1fs — sending SIGKILL", pid, timeout_s)
    try:
        if platform.system() == "Windows":
            os.kill(pid, signal.SIGTERM)  # Windows treats this as TerminateProcess
        else:
            os.kill(pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        pass

    _clear_pid_file()
    return True


__all__ = [
    "ProcessHandle",
    "is_iris_running",
    "launch_iris",
    "stop_iris",
]

# Legacy import aliases for third-party launchers.
is_alice_running = is_iris_running
launch_alice = launch_iris
stop_alice = stop_iris

