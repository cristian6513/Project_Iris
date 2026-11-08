"""Local receipts for gateways spawned by one validated packaged runtime.

A healthy HTTP response is liveness, not proof of installation ownership.
Receipts contain no credentials and are accepted only while the recorded
process still has the same executable, arguments, cwd and creation time.
"""
from __future__ import annotations

import contextlib
import json
import math
import os
import stat
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from core import runtime_descriptor as runtime_policy
from core.request_security import is_loopback_url

RECEIPT_NAME = "iris-hermes-runtime.json"
MAX_RECEIPT_BYTES = 16 * 1024


def _path_key(value: str | Path) -> str:
    return os.path.normcase(str(Path(value).resolve(strict=True)))


def _plain_receipt(path: Path) -> bool:
    try:
        return (path.is_file() and not path.is_symlink()
                and not (hasattr(path, "is_junction") and path.is_junction())
                and path.stat().st_nlink == 1 and path.stat().st_size <= MAX_RECEIPT_BYTES)
    except OSError:
        return False


def _unique_fields(pairs) -> dict[str, Any]:
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate receipt field")
        value[key] = item
    return value


def _read_receipt(path: Path) -> dict[str, Any]:
    """Bound the opened file and reject replacement or aliasing before reading."""
    if not _plain_receipt(path):
        raise ValueError("Invalid gateway receipt file")
    with path.open("rb") as handle:
        opened = os.fstat(handle.fileno())
        current = path.lstat()
        if (not stat.S_ISREG(opened.st_mode) or not stat.S_ISREG(current.st_mode)
                or opened.st_nlink != 1 or current.st_nlink != 1
                or opened.st_size > MAX_RECEIPT_BYTES
                or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)):
            raise ValueError("Gateway receipt changed or is aliased")
        raw = handle.read(MAX_RECEIPT_BYTES + 1)
    if len(raw) > MAX_RECEIPT_BYTES:
        raise ValueError("Gateway receipt exceeds the size limit")
    return json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_fields)


def _fingerprint(pid: int, profile: Path, *, port: int | None = None, process=None) -> float:
    """Fail closed when process inspection is unavailable or ambiguous."""
    import psutil

    runtime = runtime_policy.get_runtime()
    if not runtime.packaged or runtime.hermes is None:
        raise ValueError("A packaged Hermes runtime is required")
    process = process if process is not None else psutil.Process(pid)
    created = float(process.create_time())
    if not math.isfinite(created) or created <= 0:
        raise ValueError("Invalid process creation time")
    if _path_key(process.exe()) != _path_key(runtime.hermes.python):
        raise ValueError("Gateway interpreter does not belong to this installation")
    from core.hermes_runtime import hermes_command_prefix

    prefix = hermes_command_prefix()
    argv = process.cmdline()
    if (len(argv) != len(prefix) + 2
            or _path_key(argv[0]) != _path_key(runtime.hermes.python)
            or argv[1:len(prefix) - 1] != prefix[1:-1]
            or _path_key(argv[len(prefix) - 1]) != _path_key(runtime.hermes.cli)
            or argv[len(prefix):] != ["gateway", "run"]
            or _path_key(process.cwd()) != _path_key(runtime.hermes.root)):
        raise ValueError("Gateway invocation does not belong to this installation")
    process_env = {key.upper(): value for key, value in process.environ().items()}
    if (not process_env.get("HERMES_HOME") or not process_env.get(runtime_policy.DESCRIPTOR_ENV)
            or _path_key(process_env["HERMES_HOME"]) != _path_key(profile)
            or _path_key(process_env.get(runtime_policy.DESCRIPTOR_ENV, "")) != _path_key(runtime.descriptor_path)):
        raise ValueError("Gateway process does not belong to this runtime and profile")
    if port is not None:
        connections = process.net_connections(kind="inet")
        if not any(connection.status == psutil.CONN_LISTEN
                   and connection.laddr and connection.laddr.port == port
                   and connection.laddr.ip in {"127.0.0.1", "::1", "0.0.0.0", "::"}
                   for connection in connections):
            raise ValueError("The recorded gateway does not own the API listener")
    return created


def _recorded_process_gone(pid: int, created: float) -> bool:
    """True only when the recorded process provably no longer runs."""
    import psutil

    try:
        live_created = float(psutil.Process(pid).create_time())
    except psutil.NoSuchProcess:
        return True
    except (psutil.Error, OSError, ValueError):
        return False  # Uninspectable: keep failing closed.
    return math.isfinite(live_created) and abs(live_created - created) > 0.001


def record_spawned_gateway(profile: Path, pid: int) -> None:
    """Called only with the PID returned by our successful Popen."""
    created = _fingerprint(pid, profile)
    payload = {"schema_version": 1, "pid": pid, "created_at": created,
               "runtime": runtime_policy.runtime_service_identity(profile)}
    profile.mkdir(parents=True, exist_ok=True)
    target = profile / RECEIPT_NAME
    fd, temporary = tempfile.mkstemp(prefix=".iris-gateway-", suffix=".json", dir=profile)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(payload, output, sort_keys=True)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
    finally:
        with contextlib.suppress(OSError):
            os.unlink(temporary)


def gateway_registry_state(profile: Path, base_url: str | None = None) -> dict[str, Any]:
    runtime_policy.get_runtime()  # Invalid descriptors must not fall back to legacy discovery.
    path = profile / RECEIPT_NAME
    if not path.exists() and not path.is_symlink():
        return {"verified": False, "state": "absent", "pid": None}
    if not _plain_receipt(path):
        return {"verified": False, "state": "unverified", "pid": None}
    identity_verified = False
    recorded: tuple[int, float] | None = None
    try:
        data = _read_receipt(path)
        if not isinstance(data, dict):
            raise ValueError("Invalid gateway receipt")
        identity_verified = True
        pid = data.get("pid")
        created = data.get("created_at")
        if (set(data) != {"schema_version", "pid", "created_at", "runtime"}
                or type(data.get("schema_version")) is not int or data["schema_version"] != 1
                or not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0
                or not isinstance(created, (int, float)) or isinstance(created, bool)
                or not math.isfinite(created)):
            raise ValueError("Invalid gateway receipt")
        recorded = pid, float(created)
        if data.get("runtime") != runtime_policy.runtime_service_identity(profile):
            raise ValueError("Invalid gateway receipt")
        port = None
        if base_url is not None:
            if not is_loopback_url(base_url):
                raise ValueError("Packaged gateways must use loopback")
            parsed = urlsplit(base_url)
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
        live_created = _fingerprint(pid, profile, port=port)
        if abs(live_created - float(created)) > 0.001:
            raise ValueError("Gateway PID was reused")
        return {"verified": True, "state": "owned", "pid": pid, "created_at": created}
    except runtime_policy.RuntimeDescriptorError:
        raise
    except Exception as exc:
        try:
            import psutil
            if identity_verified and isinstance(exc, psutil.NoSuchProcess):
                return {"verified": False, "state": "stale", "pid": None}
            # A well-formed receipt whose process no longer runs (gone, or its
            # PID now taken by a younger process) describes no gateway, whichever
            # version wrote it: it cannot block this one. A gateway that died
            # with its Core, a restart or an update left such receipts, and the
            # new version refused to start Hermes and rolled back (qa9). A live
            # recorded process that is not ours still fails closed below.
            if recorded is not None and _recorded_process_gone(*recorded):
                return {"verified": False, "state": "stale", "pid": None}
        except ImportError:
            pass
        return {"verified": False, "state": "unverified", "pid": None}


def stop_owned_gateway(profile: Path) -> dict[str, Any]:
    expected_identity = runtime_policy.runtime_service_identity(profile)
    state = gateway_registry_state(profile)
    if not state["verified"]:
        return {"stopped": False, "pid": None, "blocked": state["state"] not in {"absent", "stale"},
                "detail": "No hay un Gateway acreditado de esta instalación; no se terminó ningún proceso."}
    import psutil

    pid = state["pid"]
    try:
        # Reinspect immediately before mutation; never use an arbitrary gateway.pid.
        process = psutil.Process(pid)
        if abs(_fingerprint(pid, profile, process=process) - state["created_at"]) > 0.001:
            raise ValueError("Gateway PID was reused")
        process.terminate()
        try:
            process.wait(timeout=4)
        except psutil.TimeoutExpired:
            if abs(_fingerprint(pid, profile, process=process) - state["created_at"]) > 0.001:
                raise ValueError("Gateway PID was reused")
            process.kill()
            process.wait(timeout=4)
    except psutil.NoSuchProcess:
        pass
    except Exception:
        return {"stopped": False, "pid": pid, "blocked": True,
                "detail": "No se pudo detener el Gateway acreditado; se conservó el estado del perfil."}
    # Delete only the receipt and PID file that still refer to this process.
    for filename in (RECEIPT_NAME, "gateway.pid"):
        path = profile / filename
        if not _plain_receipt(path):
            continue
        try:
            current = _read_receipt(path)
            if not isinstance(current, dict) or type(current.get("pid")) is not int or current["pid"] != pid:
                continue
            if filename == RECEIPT_NAME and (
                    set(current) != {"schema_version", "pid", "created_at", "runtime"}
                    or type(current.get("schema_version")) is not int or current["schema_version"] != 1
                    or current.get("created_at") != state["created_at"]
                    or current.get("runtime") != expected_identity):
                continue
            path.unlink()
        except (OSError, ValueError, RecursionError):
            pass
    return {"stopped": True, "pid": pid, "blocked": False,
            "detail": "Gateway de esta instalación detenido."}
