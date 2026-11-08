"""Native Hermes wake-word integration for IRIS OS.

IRIS deliberately does not implement a second hot-word detector.  Hermes owns
the microphone lease, local detection, lazy dependency installation and voice
recording through its supported TUI-gateway JSON-RPC methods:

``wake.start`` / ``wake.stop`` / ``wake.status`` / ``voice.record``.

This module only provides three thin pieces of glue:

* safe, merge-only editing of Hermes' ``wake_word`` config section;
* a long-lived client for Hermes' official headless WebSocket gateway;
* local-only HTTP controls used by the IRIS ear icon and settings panel.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import math
import os
import re
import secrets
import socket
import subprocess
import tempfile
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from core.config import IRISConfig
from core.capability_permissions import CapabilityPermissionStore
from core.hermes_voice import ManualVoiceCapture, partial_text
from core.file_lock import interprocess_file_lock
from core.hermes_runtime import (
    NO_WINDOW,
    get_hermes_config_path,
    get_hermes_repo,
    hermes_failure_message,
    hermes_subprocess_env,
    hermes_command_prefix,
    hermes_python_prefix,
    resolve_hermes_cli,
    resolve_hermes_python,
)
from core.request_security import is_loopback_host, is_trusted_local_origin


logger = logging.getLogger("IRIS.hermes_wake")
router = APIRouter(tags=["hermes-wake"])

WakeMode = Literal["keep", "off", "hey_hermes", "custom"]
GatewayEventHandler = Callable[[str, dict[str, Any]], Awaitable[None] | None]

_CUSTOM_WAKE_PHRASE_MAX_LENGTH = 80

WAKE_PRESETS: dict[str, dict[str, Any]] = {
    "hey_hermes": {
        "provider": "openwakeword",
        "phrase": "hey hermes",
        "capture": "local",
        "openwakeword": {"model": "hey_hermes"},
    },
}


def _friendly_native_failure(detail: str) -> str:
    """Turn Hermes bootstrap tracebacks into an actionable, secret-free hint."""
    missing = re.search(r"No module named ['\"]([^'\"]+)['\"]", detail or "")
    if missing:
        return (
            "La instalación de Hermes está incompleta: falta el módulo "
            f"{missing.group(1)}. Abre el wizard, elige Reparar Hermes y vuelve a intentarlo. "
            "El micrófono no se activó."
        )
    return (detail or "Hermes no devolvió un diagnóstico de wake word.").strip()[-1200:]


def _load_yaml_document() -> dict[str, Any]:
    """Read Hermes config without converting malformed data into an overwrite."""
    path = get_hermes_config_path()
    if not path.exists():
        return {}
    try:
        import yaml

        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"Hermes config.yaml no se puede leer: {exc}") from exc
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ValueError("Hermes config.yaml debe contener un objeto YAML en la raíz.")
    return loaded


def _write_yaml_document(data: dict[str, Any]) -> None:
    """Atomically replace config.yaml after a merge-only update."""
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - project dependency
        raise RuntimeError("PyYAML no está instalado; no se modificó Hermes.") from exc

    path = get_hermes_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.iris-wake-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink(missing_ok=True)


def read_voice_streaming_settings() -> dict[str, Any]:
    """Expose only the native opt-in, never STT keys, endpoints or credentials."""
    raw = _load_yaml_document().get("stt", {})
    if not isinstance(raw, dict):
        raise ValueError("La sección stt de Hermes debe ser un objeto; no se modificó.")
    value = raw.get("streaming", False)
    enabled = value is True or (isinstance(value, str) and value.strip().lower() in {"true", "1", "yes", "on"}) or value == 1
    return {"streaming": bool(enabled)}


def configure_voice_streaming(enabled: bool) -> dict[str, Any]:
    with interprocess_file_lock(get_hermes_config_path().parent / ".iris-config.lock"):
        document = _load_yaml_document()
        raw = document.get("stt", {})
        if not isinstance(raw, dict):
            raise ValueError("La sección stt de Hermes debe ser un objeto; no se modificó.")
        document["stt"] = {**raw, "streaming": enabled}
        _write_yaml_document(document)
    return {"streaming": enabled}


def infer_wake_mode(wake: dict[str, Any]) -> str:
    """Map Hermes' native config to the guided IRIS wake modes."""
    provider = str(wake.get("provider") or "openwakeword").strip().casefold()
    phrase = " ".join(str(wake.get("phrase") or "hey hermes").split()).casefold()
    model = ""
    openwakeword = wake.get("openwakeword")
    if isinstance(openwakeword, dict):
        model = str(openwakeword.get("model") or "").strip().casefold()

    if provider in {"openwakeword", "oww", "local"} and (
        phrase == "hey hermes" or model in {"hey_hermes", "hey hermes"}
    ):
        return "hey_hermes"
    return "custom"


def normalize_custom_wake_phrase(value: str | None) -> str:
    """Build the native Sherpa phrase from an editable assistant/profile name.

    The UI presents a fixed ``Hey`` prefix, but API clients may submit either
    a bare name or an already-prefixed phrase. Keeping this normalization in the backend avoids
    duplicated prefixes and prevents arbitrary YAML-shaped text from reaching
    Hermes' spoken-phrase configuration.
    """
    candidate = " ".join(str(value or "").split()).strip()
    if candidate.casefold() == "hey":
        candidate = ""
    elif candidate.casefold().startswith("hey "):
        candidate = candidate[4:].strip()
    if not candidate:
        raise ValueError("Escribe el nombre que irá después de «Hey».")
    if "_" in candidate or not re.fullmatch(r"[\w .’'\-]+", candidate, flags=re.UNICODE):
        raise ValueError(
            "El nombre de activación solo admite letras, números, espacios, puntos, guiones y apóstrofes."
        )
    phrase = f"hey {candidate}"
    if len(phrase) > _CUSTOM_WAKE_PHRASE_MAX_LENGTH:
        raise ValueError(
            f"La frase de activación puede tener como máximo {_CUSTOM_WAKE_PHRASE_MAX_LENGTH} caracteres."
        )
    return phrase


def _wake_sensitivity(wake: dict[str, Any]) -> float:
    """Return Hermes' shared 0..1 threshold without trusting hand-edited YAML."""
    try:
        value = float(wake.get("sensitivity", 0.6))
    except (TypeError, ValueError):
        value = 0.6
    if not math.isfinite(value):
        value = 0.6
    return min(max(value, 0.0), 1.0)


def read_hermes_wake_settings() -> dict[str, Any]:
    """Return native config truth; missing config follows Hermes defaults."""
    document = _load_yaml_document()
    raw = document.get("wake_word")
    wake = dict(raw) if isinstance(raw, dict) else {}
    provider = str(wake.get("provider") or "openwakeword")
    phrase = " ".join(str(wake.get("phrase") or "hey hermes").split())
    return {
        # Keep Hermes' effective defaults separate from persisted user state.
        # Without this bit, a clean install looks configured merely because
        # the default phrase is "hey hermes" even when no wake_word block
        # exists in config.yaml.
        "configured": isinstance(raw, dict),
        "enabled": bool(wake.get("enabled", False)),
        "mode": infer_wake_mode(wake),
        "provider": provider,
        "phrase": phrase,
        "capture": str(wake.get("capture") or "auto"),
        "surface": str(wake.get("surface") or "auto"),
        "start_new_session": bool(wake.get("start_new_session", True)),
        "profile_routing": bool(wake.get("profile_routing", True)),
        "sensitivity": _wake_sensitivity(wake),
        "config_path": str(get_hermes_config_path()),
    }


def configure_hermes_wake(
    mode: WakeMode,
    *,
    enabled: bool | None = None,
    sensitivity: float | None = None,
    phrase: str | None = None,
) -> dict[str, Any]:
    """Serialize merge-only wake updates from the wizard and live UI."""
    with interprocess_file_lock(get_hermes_config_path().parent / ".iris-config.lock"):
        return _configure_hermes_wake(
            mode,
            enabled=enabled,
            sensitivity=sensitivity,
            phrase=phrase,
        )


def _configure_hermes_wake(
    mode: WakeMode,
    *,
    enabled: bool | None = None,
    sensitivity: float | None = None,
    phrase: str | None = None,
) -> dict[str, Any]:
    """Merge a guided preset into Hermes config, preserving unrelated keys.

    Choosing a phrase never grants microphone access.  ``enabled`` is only
    changed when the caller explicitly supplies it; a new config therefore
    remains off, while an existing user's persisted choice is respected.
    """
    if mode == "keep" and enabled is None and sensitivity is None and phrase is None:
        return read_hermes_wake_settings()

    document = _load_yaml_document()
    existing = document.get("wake_word")
    wake = dict(existing) if isinstance(existing, dict) else {}

    if mode == "off":
        wake["enabled"] = False
    elif mode == "custom":
        wake.update({
            "provider": "sherpa",
            "phrase": normalize_custom_wake_phrase(phrase),
            # Open-vocabulary phrases belong to Hermes' native Sherpa path.
            # IRIS never captures or forwards pre-wake browser audio.
            "capture": "local",
            "surface": "gui",
            "start_new_session": True,
            "profile_routing": True,
        })
        if "enabled" not in wake:
            wake["enabled"] = False
    elif mode != "keep":
        preset = WAKE_PRESETS.get(mode)
        if preset is None:
            raise ValueError(f"Modo de activación no soportado: {mode}")
        wake.update({
            "provider": preset["provider"],
            "phrase": preset["phrase"],
            # IRIS deliberately does not stream browser/renderer audio into
            # Hermes.  Keep microphone ownership in Hermes' native backend so
            # ``auto`` cannot silently select the unsupported client protocol.
            "capture": preset["capture"],
            "surface": "gui",
            "start_new_session": True,
            "profile_routing": True,
        })
        if mode == "hey_hermes":
            native = wake.get("openwakeword")
            openwakeword = dict(native) if isinstance(native, dict) else {}
            openwakeword.update(preset["openwakeword"])
            wake["openwakeword"] = openwakeword
        if "enabled" not in wake:
            wake["enabled"] = False

    if enabled is not None and mode != "off":
        wake["enabled"] = bool(enabled)
    if sensitivity is not None:
        if not 0.0 <= sensitivity <= 1.0:
            raise ValueError("La sensibilidad del wake word debe estar entre 0 y 1.")
        wake["sensitivity"] = float(sensitivity)

    document["wake_word"] = wake
    _write_yaml_document(document)
    return read_hermes_wake_settings()


def probe_hermes_wake_requirements(timeout: float = 60.0) -> dict[str, Any]:
    """Ask Hermes' own requirement checker; never claim readiness by guessing."""
    python = resolve_hermes_python()
    repo = get_hermes_repo()
    prefix = hermes_python_prefix(python) if python else None
    if not prefix or not repo.exists():
        return {
            "installed": False,
            "available": False,
            "hint": "Hermes Agent no está instalado o su entorno administrado está incompleto.",
        }

    script = (
        "import json; "
        "from tui_gateway.ws import handle_ws; "
        "from tools.wake_word import check_wake_word_requirements, load_wake_word_config; "
        "print(json.dumps(check_wake_word_requirements(load_wake_word_config()), ensure_ascii=False))"
    )
    try:
        result = subprocess.run(
            [*prefix, "-c", script],
            cwd=str(repo),
            env=hermes_subprocess_env(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"installed": True, "available": False, "hint": hermes_failure_message(exc), "detail": str(exc)}

    # Hermes 0.20 may route the probe's print through its logging bootstrap to
    # stderr, and some builds return a pre-serialized JSON string. Accept both
    # shapes while still requiring the final value to be an object.
    output_lines = [
        *((result.stdout or "").splitlines()),
        *((result.stderr or "").splitlines()),
    ]
    for line in reversed(output_lines):
        try:
            payload = json.loads(line)
        except ValueError:
            continue
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except ValueError:
                continue
        if isinstance(payload, dict):
            payload["installed"] = True
            payload["probe_returncode"] = result.returncode
            # Hermes' ``available`` also means "the missing engine can be
            # installed lazily". That is useful for the activation path, but
            # it is not proof that the local detector and microphone are ready
            # right now. Keep both truths so IRIS never paints an installable
            # first use as an already-working microphone.
            capture = str(payload.get("capture") or "local").strip().casefold()
            deps_ready = payload.get("deps_available") is True
            audio_ready = payload.get("audio_available") is True
            capture_supported = capture != "client"
            payload["ready"] = bool(
                payload.get("available")
                and deps_ready
                and audio_ready
                and capture_supported
            )
            if capture == "client":
                payload["hint"] = (
                    "Hermes solicita audio del cliente, pero IRIS OS no envía un stream "
                    "del navegador. Selecciona Hey Hermes con captura local o conserva "
                    "la frase nativa de tu perfil."
                )
            elif payload.get("available") and not payload["ready"] and not payload.get("hint"):
                from core.runtime_descriptor import get_runtime

                # The packaged runtime seals Hermes' lazy installs (A08.4).
                payload["hint"] = (
                    "Al activarla, Hermes comprobará el motor incluido y el micrófono; "
                    "esta instalación no descarga componentes."
                    if get_runtime().packaged else
                    "Hermes puede instalar el motor local al activarlo; después comprobará "
                    "el dispositivo de micrófono."
                )
            return payload
    detail = _friendly_native_failure(result.stderr or result.stdout)
    return {
        "installed": True,
        "available": False,
        "hint": detail,
        "probe_returncode": result.returncode,
    }


class HermesGatewayError(RuntimeError):
    """A native Hermes JSON-RPC request failed."""


class HermesWakeRuntime:
    """Own one official ``hermes serve`` connection and microphone lease."""

    def __init__(self, event_handler: GatewayEventHandler | None = None) -> None:
        self.event_handler = event_handler
        self.process: asyncio.subprocess.Process | None = None
        self.websocket: Any | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._log_task: asyncio.Task[None] | None = None
        self._event_task: asyncio.Task[None] | None = None
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._request_id = 0
        self._start_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        self._ready = asyncio.Event()
        self._events: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()
        self._recent_logs: deque[str] = deque(maxlen=20)
        self._closing = False
        self.listening = False
        self.state = "off"
        self.last_error = ""
        self.manual_voice = ManualVoiceCapture(self)
        self._wake_capture_id = ""
        self._wake_partial = ""
        self._wake_partial_sequence = 0
        self._pending_partial: dict[str, Any] | None = None
        try:
            native = read_hermes_wake_settings()
        except ValueError as exc:
            native = {"phrase": "hey hermes", "provider": "openwakeword"}
            self.last_error = str(exc)
        self.phrase = str(native["phrase"])
        self.provider = str(native["provider"])

    @property
    def running(self) -> bool:
        websocket = self.websocket
        return bool(websocket is not None and getattr(websocket, "close_code", None) is None)

    @staticmethod
    def _free_loopback_port() -> int:
        """Return a currently free loopback port for the owned server."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            return int(probe.getsockname()[1])

    async def ensure_gateway(self) -> None:
        if self.running and self._ready.is_set():
            return
        async with self._start_lock:
            if self.running and self._ready.is_set():
                return
            stale_process = self.process
            if stale_process and stale_process.returncode is None:
                await self._terminate_owned_process(stale_process)
            if self.process is stale_process:
                self.process = None
            command_prefix = hermes_command_prefix()
            repo = get_hermes_repo()
            if not command_prefix or not repo.exists():
                raise HermesGatewayError("Hermes Agent administrado no está disponible.")

            creationflags = 0
            if os.name == "nt":
                creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            port = self._free_loopback_port()
            token = secrets.token_urlsafe(32)
            websocket_url = f"ws://127.0.0.1:{port}/api/ws?token={quote(token, safe='')}"
            environment = hermes_subprocess_env()
            environment["HERMES_DASHBOARD_SESSION_TOKEN"] = token
            self._ready = asyncio.Event()
            self._recent_logs.clear()
            self._closing = False
            self._events = asyncio.Queue()
            self._pending_partial = None
            try:
                self.process = await asyncio.create_subprocess_exec(
                    *command_prefix,
                    "serve",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--skip-build",
                    "--isolated",
                    cwd=str(repo),
                    env=environment,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    creationflags=creationflags,
                )
            except OSError as exc:
                self.process = None
                self.state = "error"
                self.last_error = (
                    "No se pudo iniciar el servidor nativo de Hermes. "
                    "Usa Reparar Hermes y vuelve a intentarlo."
                )
                raise HermesGatewayError(self.last_error) from exc
            self._log_task = asyncio.create_task(
                self._drain_server_logs(), name="Hermes-wake-server-log"
            )
            self._event_task = asyncio.create_task(self._consume_events(), name="Hermes-wake-event-worker")
            try:
                await self._connect_websocket(websocket_url, timeout=30.0)
                await asyncio.wait_for(self._ready.wait(), timeout=10.0)
            except Exception as exc:
                raw_detail = " | ".join(self._recent_logs)
                detail = _friendly_native_failure(raw_detail) if raw_detail else ""
                await self.close()
                suffix = f" Detalle: {detail}" if detail else f" Detalle: {exc}"
                self.state = "error"
                self.last_error = "Hermes no inició su servidor nativo de voz a tiempo." + suffix
                raise HermesGatewayError(self.last_error) from exc

    async def _connect_websocket(self, url: str, *, timeout: float) -> None:
        """Poll the freshly spawned official server until ``/api/ws`` is ready."""
        try:
            import websockets
        except ImportError as exc:  # pragma: no cover - declared dependency
            raise HermesGatewayError("Falta la dependencia websockets de IRIS OS.") from exc

        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        last_error: Exception | None = None
        while loop.time() < deadline:
            process = self.process
            if process and process.returncode is not None:
                raise HermesGatewayError(
                    f"hermes serve terminó con código {process.returncode}."
                )
            try:
                self.websocket = await websockets.connect(
                    url,
                    open_timeout=2.0,
                    close_timeout=2.0,
                    ping_interval=20.0,
                    max_size=4 * 1024 * 1024,
                )
                self._reader_task = asyncio.create_task(
                    self._read_websocket(), name="Hermes-wake-rpc-reader"
                )
                return
            except (OSError, TimeoutError) as exc:
                last_error = exc
                await asyncio.sleep(0.2)
        raise HermesGatewayError(f"No se pudo conectar con hermes serve: {last_error}")

    async def request(self, method: str, params: dict[str, Any] | None = None, *, timeout: float = 30.0) -> Any:
        await self.ensure_gateway()
        websocket = self.websocket
        if not websocket or not self.running:
            raise HermesGatewayError("El gateway nativo de Hermes no está conectado.")
        self._request_id += 1
        request_id = self._request_id
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        self._pending[request_id] = future
        message = json.dumps({
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or {},
        }, ensure_ascii=False)
        try:
            async with self._write_lock:
                await websocket.send(message)
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            self._pending.pop(request_id, None)

    @staticmethod
    def _rpc_object(method: str, result: Any) -> dict[str, Any]:
        """Require the object result promised by Hermes' wake RPC methods."""
        if isinstance(result, dict):
            return result
        raise HermesGatewayError(
            f"Hermes devolvió una respuesta inválida para {method}. "
            "Actualiza o repara Hermes; el estado del micrófono no se asumió."
        )

    async def start_listener(self, *, persist: bool) -> dict[str, Any]:
        if self.manual_voice.busy or self.manual_voice._lock.locked():
            raise HermesGatewayError("Termina la grabación manual antes de activar Wake.")
        self.state = "arming"
        raw_result = await self.request(
            "wake.start",
            {"persist": persist, "surface": "gui"},
            timeout=185.0,
        )
        try:
            result = self._rpc_object("wake.start", raw_result)
        except HermesGatewayError:
            # An invalid success payload cannot prove whether Hermes acquired
            # the microphone. Stop defensively before reporting the protocol
            # mismatch, without persisting a preference change.
            try:
                await self.request("wake.stop", {"persist": False}, timeout=20.0)
            except Exception:
                pass
            self.listening = False
            self.state = "error"
            raise
        capture = str(result.get("capture") or "").strip().casefold()
        if capture == "client":
            # Hermes' client mode waits for ``wake.feed`` audio frames.  IRIS
            # intentionally has no browser hot-word recorder, so treating this
            # response as active would both mislead the user and invite a
            # future privacy regression.  Stop the native state immediately.
            try:
                await self.request("wake.stop", {"persist": False}, timeout=20.0)
            except Exception as exc:  # pragma: no cover - best-effort cleanup
                logger.warning("Could not stop unsupported Hermes client capture: %s", exc)
            self.listening = False
            self.state = "blocked"
            self.last_error = (
                "Hermes solicitó captura de audio del cliente, que IRIS OS no envía. "
                "Selecciona Hey Hermes para usar captura local o conserva la frase "
                "nativa de tu perfil. "
                "El micrófono no se activó."
            )
            raise HermesGatewayError(self.last_error)
        self.listening = bool(result.get("started"))
        self.state = "listening" if self.listening else "blocked"
        self.phrase = str(result.get("phrase") or self.phrase)
        self.provider = str(result.get("provider") or self.provider)
        self.last_error = "" if self.listening else str(result.get("hint") or result.get("reason") or "")
        return self.snapshot(extra=result)

    async def stop_listener(self, *, persist: bool) -> dict[str, Any]:
        self.manual_voice.resume_wake = False
        if not self.running:
            if persist:
                configure_hermes_wake("keep", enabled=False)
            self.listening = False
            self.state = "off"
            self._wake_capture_id = ""
            self._wake_partial = ""
            return self.snapshot()
        result = self._rpc_object(
            "wake.stop",
            await self.request("wake.stop", {"persist": persist}, timeout=20.0),
        )
        self.listening = False
        self.state = "off"
        self._wake_capture_id = ""
        self._wake_partial = ""
        return self.snapshot(extra=result)

    async def status(self) -> dict[str, Any]:
        if not self.running:
            return self.snapshot()
        result = self._rpc_object(
            "wake.status",
            await self.request("wake.status", {}, timeout=20.0),
        )
        self.listening = bool(result.get("listening"))
        if self.state not in {"arming", "detected", "recording", "transcribing", "processing"}:
            self.state = "listening" if self.listening else "off"
        self.phrase = str(result.get("phrase") or self.phrase)
        self.provider = str(result.get("provider") or self.provider)
        self.last_error = str(result.get("hint") or "")
        return self.snapshot(extra=result)

    def snapshot(self, *, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            "active": self.listening,
            "state": self.state,
            "phrase": self.phrase,
            "provider": self.provider,
            "gateway_running": self.running,
            "error": self.last_error,
            **(extra or {}),
        }

    async def _read_websocket(self) -> None:
        websocket = self.websocket
        if websocket is None:
            return
        try:
            async for raw in websocket:
                try:
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8", errors="replace")
                    message = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(message, dict):
                    continue
                request_id = message.get("id")
                if request_id is not None and request_id in self._pending:
                    future = self._pending[request_id]
                    if future.done():
                        continue
                    if "error" in message:
                        error = message.get("error") or {}
                        detail = error.get("message") if isinstance(error, dict) else error
                        future.set_exception(HermesGatewayError(str(detail or "Error RPC de Hermes")))
                    else:
                        future.set_result(message.get("result", {}))
                    continue
                if message.get("method") == "event":
                    params = message.get("params") or {}
                    if not isinstance(params, dict):
                        continue
                    event_type = str(params.get("type") or "")
                    payload = params.get("payload") or {}
                    if event_type == "gateway.ready":
                        self._ready.set()
                    if event_type:
                        await self._queue_native_event(event_type, payload if isinstance(payload, dict) else {})
        finally:
            error = HermesGatewayError("El gateway nativo de Hermes se cerró.")
            for future in list(self._pending.values()):
                if not future.done():
                    future.set_exception(error)
            self.listening = False
            if not self._closing:
                self.state = "disconnected"
                process = self.process
                if process and process.returncode is None:
                    await self._terminate_owned_process(process)
                if self.process is process:
                    self.process = None
            if self.websocket is websocket:
                self.websocket = None
            self.manual_voice.disconnected()
            self._wake_capture_id = ""
            self._wake_partial = ""
            self._pending_partial = None
            await self.manual_voice.publish()

    async def _drain_server_logs(self) -> None:
        process = self.process
        if not process or not process.stdout:
            return
        while True:
            raw = await process.stdout.readline()
            if not raw:
                return
            line = raw.decode("utf-8", errors="replace").strip()
            if line:
                # Sessionless voice callbacks run on native worker threads.
                # Hermes routes those to stdio when there is no session-bound
                # WebSocket. Consume the owned process's protocol output too;
                # otherwise transcription succeeds but Face waits forever.
                try:
                    message = json.loads(line)
                except ValueError:
                    message = None
                params = message.get("params") if isinstance(message, dict) else None
                if (isinstance(message, dict) and message.get("jsonrpc") == "2.0"
                        and message.get("method") == "event" and isinstance(params, dict)
                        and params.get("type") in {"voice.status", "voice.transcript", "voice.partial"}):
                    if isinstance(params.get("payload"), dict):
                        await self._queue_native_event(params["type"], params["payload"])
                    continue
                self._recent_logs.append(line)
                logger.debug("Hermes wake server: %s", line)

    def _voice_generation(self) -> tuple[str, ...]:
        if self.manual_voice.intercept:
            return ("manual", str(self.manual_voice.generation), self.manual_voice.client_id, self.manual_voice.capture_id)
        return ("wake", self._wake_capture_id)

    async def _queue_native_event(self, event_type: str, payload: dict[str, Any]) -> None:
        # Stamp on arrival, before a slow agent turn. Old queued callbacks must
        # not be relabelled as a new capture. Coalesce partials to one queue slot.
        if event_type.startswith("voice."):
            payload = {**payload, "_iris_generation": self._voice_generation()}
        if event_type == "voice.partial":
            text = partial_text(payload)
            if not text:
                return
            queued = self._pending_partial is not None
            self._pending_partial = {"text": text, "_iris_generation": payload["_iris_generation"]}
            if queued:
                return
            await self._events.put(("iris.voice.partial", {}))
            return
        await self._events.put((event_type, payload))

    async def _consume_events(self) -> None:
        events = self._events
        while True:
            event_type, payload = await events.get()
            try:
                if event_type == "iris.voice.partial":
                    event_type = "voice.partial"
                    payload, self._pending_partial = self._pending_partial or {}, None
                await self._handle_native_event(event_type, payload)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = str(exc)
                self.state = "listening" if self.listening else "error"
                logger.exception("Native Hermes wake event failed: %s", event_type)
                await self._emit("wake.error", {
                    "active": self.listening,
                    "state": self.state,
                    "error": str(exc),
                })
            finally:
                events.task_done()
            if self._closing or events is not self._events:
                return

    async def _handle_native_event(self, event_type: str, payload: dict[str, Any]) -> None:
        generation = payload.get("_iris_generation")
        if generation is not None and generation != self._voice_generation():
            return
        payload = {key: value for key, value in payload.items() if key != "_iris_generation"}
        if await self.manual_voice.handle_event(event_type, payload):
            return
        if event_type == "wake.detected":
            self._wake_capture_id = uuid.uuid4().hex
            self._wake_partial = ""
            self._wake_partial_sequence = 0
            self.listening = False
            self.state = "detected"
            try:
                await self._emit(event_type, {**payload, "capture_id": self._wake_capture_id})
                self._rpc_object(
                    "voice.toggle",
                    await self.request("voice.toggle", {"action": "on"}, timeout=20.0),
                )
                recording = self._rpc_object(
                    "voice.record",
                    await self.request("voice.record", {"action": "start"}, timeout=30.0),
                )
            except Exception:
                # A failed UI notification or record start must not leave
                # voice mode armed or the detector suspended. Cleanup is
                # best-effort; retain the original actionable error.
                try:
                    await self._restore_wake_after_turn()
                except Exception:
                    self.listening = False
                    self.state = "error"
                raise
            self.state = "recording" if recording.get("status") == "recording" else "blocked"
            await self._emit("voice.status", {"state": self.state, **recording})
            return

        if event_type == "voice.partial":
            text = partial_text(payload)
            if self._wake_capture_id and self.state in {"detected", "recording", "transcribing"} and text and text != self._wake_partial:
                self._wake_partial = text
                self._wake_partial_sequence += 1
                await self._emit("voice.partial", {"text": text, "mode": "wake",
                    "capture_id": self._wake_capture_id, "sequence": self._wake_partial_sequence})
            return

        if event_type == "voice.transcript":
            # A native take has one final. Duplicate WS/stdio delivery cannot
            # launch the same turn again after the first one finished.
            if not self._wake_capture_id:
                return
            self._wake_capture_id = ""
            self._wake_partial = ""
            text = str(payload.get("text") or "").strip()
            if payload.get("stop_phrase"):
                # Hermes already ends hands-free capture for whole-utterance
                # stop phrases. Confirm voice is off and mirror its detector
                # state, but never forward "stop" as a user turn.
                try:
                    await self.request("voice.toggle", {"action": "off"}, timeout=15.0)
                    status = self._rpc_object(
                        "wake.status",
                        await self.request("wake.status", {}, timeout=20.0),
                    )
                except Exception:
                    try:
                        await self._restore_wake_after_turn()
                    except Exception:
                        self.listening = False
                        self.state = "error"
                    raise
                self.listening = bool(status.get("listening"))
                self.state = "listening" if self.listening else "off"
                await self._emit("voice.stopped", {
                    **self.snapshot(extra=status),
                    "stop_phrase": True,
                    "text": text,
                })
                return
            if text:
                # Hermes resumes its detector after transcription. Pause it
                # again while IRIS thinks/speaks so the assistant cannot wake
                # itself through the speakers.
                try:
                    await self.request("wake.pause", {}, timeout=15.0)
                except Exception:
                    await self._restore_wake_after_turn()
                    raise
                self.state = "processing"
                try:
                    await self._emit(event_type, {**payload, "text": text})
                finally:
                    # Even if the IRIS turn fails, releases the voice capture
                    # and restores the wake detector so the microphone state
                    # cannot get stuck between two UI states.
                    await self._restore_wake_after_turn()
            else:
                status = self._rpc_object(
                    "wake.status",
                    await self.request("wake.status", {}, timeout=20.0),
                )
                self.listening = bool(status.get("listening"))
                self.state = "listening" if self.listening else "off"
                await self._emit(event_type, payload)
            return

        if event_type == "voice.status":
            reported = str(payload.get("state") or self.state)
            if reported == "idle":
                self._wake_capture_id = ""
                self._wake_partial = ""
            self.state = "listening" if reported == "idle" and self.listening else reported
        await self._emit(event_type, payload)

    async def _restore_wake_after_turn(self) -> None:
        """Best-effort cleanup where one failing RPC cannot skip the next."""
        errors: list[str] = []
        try:
            self._rpc_object(
                "voice.toggle",
                await self.request("voice.toggle", {"action": "off"}, timeout=15.0),
            )
        except Exception as exc:
            errors.append(f"voice.toggle: {exc}")

        resumed: dict[str, Any] = {}
        try:
            resumed = self._rpc_object(
                "wake.resume",
                await self.request("wake.resume", {}, timeout=20.0),
            )
            self.listening = bool(resumed.get("resumed"))
        except Exception as exc:
            errors.append(f"wake.resume: {exc}")
            self.listening = False

        self.state = "listening" if self.listening else ("error" if errors else "off")
        if errors:
            self.last_error = "; ".join(errors)
            logger.warning("Hermes wake cleanup incomplete: %s", self.last_error)
        await self._emit("wake.resumed", self.snapshot(extra=resumed))

    async def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        if not self.event_handler:
            return
        result = self.event_handler(event_type, payload)
        if inspect.isawaitable(result):
            await result

    async def close(self) -> None:
        if self.running and self._ready.is_set():
            try:
                await self.request("wake.stop", {"persist": False}, timeout=5.0)
            except Exception:
                pass
        self._closing = True
        websocket = self.websocket
        if websocket is not None:
            try:
                await websocket.close()
            except Exception:
                pass
        process = self.process
        if process and process.returncode is None:
            await self._terminate_owned_process(process)
        current = asyncio.current_task()
        cancelled = []
        for task in (self._reader_task, self._log_task, self._event_task):
            if task and task is not current and not task.done():
                task.cancel()
                cancelled.append(task)
        if cancelled:
            await asyncio.gather(*cancelled, return_exceptions=True)
        error = HermesGatewayError("El gateway nativo de Hermes se cerró.")
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(error)
        self._pending.clear()
        self._ready.clear()
        self.websocket = None
        self.process = None
        self.listening = False
        self.state = "off"
        self._wake_capture_id = ""
        self._wake_partial = ""
        self._pending_partial = None
        self.manual_voice.disconnected()

    @staticmethod
    async def _terminate_owned_process(process: asyncio.subprocess.Process) -> None:
        """Stop only the ``hermes serve`` tree created by this runtime."""
        if process.returncode is not None:
            return
        if os.name == "nt":
            killer = await asyncio.create_subprocess_exec(
                "taskkill",
                "/PID",
                str(process.pid),
                "/T",
                "/F",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            await killer.wait()
            if killer.returncode and process.returncode is None:
                process.kill()
        else:
            try:
                process.terminate()
            except ProcessLookupError:
                return
        try:
            await asyncio.wait_for(process.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            if process.returncode is None:
                process.kill()
                await process.wait()


class HermesWakeSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: WakeMode | None = None
    enabled: bool | None = None
    sensitivity: float | None = Field(default=None, ge=0.0, le=1.0)
    phrase: str | None = Field(default=None, max_length=_CUSTOM_WAKE_PHRASE_MAX_LENGTH)


class HermesWakeRuntimeUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    active: bool


def _enforce_local_request(request: Request) -> None:
    headers = getattr(request, "headers", {})
    if headers.get("sec-fetch-site", "").casefold() == "cross-site":
        raise HTTPException(status_code=403, detail="La escucha de Hermes no acepta solicitudes cross-site.")
    peer = getattr(getattr(request, "client", None), "host", "")
    if not is_loopback_host(peer):
        raise HTTPException(
            status_code=403,
            detail="La escucha de Hermes solo puede controlarse desde este equipo.",
        )
    origin = headers.get("origin", "").strip()
    url = getattr(request, "url", None)
    host = getattr(url, "hostname", None)
    if origin and not is_trusted_local_origin(
        origin,
        host,
        request_scheme=getattr(url, "scheme", None),
        request_port=getattr(url, "port", None),
    ):
        raise HTTPException(status_code=403, detail="El origen no está autorizado para controlar el micrófono.")


def _runtime_from(request: Request) -> HermesWakeRuntime | None:
    runtime = getattr(request.app.state, "hermes_wake", None)
    return runtime if isinstance(runtime, HermesWakeRuntime) else None


def _reconcile_wake_input_truth(
    live: dict[str, Any],
    requirements: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Make a live device failure override an optimistic dependency probe."""
    live_state = dict(live or {})
    capability_state = dict(requirements or {})
    input_device = live_state.get("input_device")
    input_error = (
        str(input_device.get("error") or "").strip()
        if isinstance(input_device, dict)
        else ""
    )
    runtime_error = str(live_state.get("error") or "").strip()
    if not input_error and not runtime_error:
        return live_state, capability_state

    hint = (
        "No se encontró un micrófono utilizable. Conecta uno y vuelve a comprobar."
        if input_error
        else _friendly_native_failure(runtime_error)
    )
    if input_error:
        live_state["local_input_available"] = False
        live_state["input_available"] = False
        capability_state["audio_available"] = False
        capability_state["local_input_available"] = False
    capability_state["ready"] = False
    capability_state["hint"] = hint
    return live_state, capability_state


async def hermes_wake_snapshot(request: Request, *, probe: bool = True) -> dict[str, Any]:
    settings = read_hermes_wake_settings()
    try:
        suggested_name = IRISConfig.load().assistant_name.strip() or "IRIS"
    except Exception:
        suggested_name = "IRIS"
    runtime = _runtime_from(request)
    if runtime:
        live = await runtime.status() if runtime.running else runtime.snapshot()
    else:
        live = {
            "active": False,
            "state": "off",
            "gateway_running": False,
        }
    requirements = await asyncio.to_thread(probe_hermes_wake_requirements) if probe else {}
    live, requirements = _reconcile_wake_input_truth(live, requirements)
    return {
        **settings,
        "suggested_name": suggested_name,
        "runtime": live,
        "capabilities": requirements,
        "privacy": {
            "local_detection": True,
            "audio_sent_before_wake": False,
            "default_enabled": False,
        },
    }


@router.get("/api/hermes-wake/settings")
async def get_hermes_wake_settings(request: Request, probe: bool = True) -> JSONResponse:
    try:
        return JSONResponse(await hermes_wake_snapshot(request, probe=probe))
    except ValueError as exc:
        return JSONResponse({"status": "error", "detail": str(exc)}, status_code=422)


@router.post("/api/hermes-wake/settings")
async def update_hermes_wake_settings(payload: HermesWakeSettingsUpdate, request: Request) -> JSONResponse:
    _enforce_local_request(request)
    runtime = _runtime_from(request)
    was_active = bool(runtime and runtime.listening)
    if was_active and runtime:
        try:
            await runtime.stop_listener(persist=False)
        except HermesGatewayError as exc:
            return JSONResponse({"status": "error", "detail": str(exc)}, status_code=422)

    try:
        settings = await asyncio.to_thread(
            configure_hermes_wake,
            payload.mode or "keep",
            enabled=payload.enabled,
            sensitivity=payload.sensitivity,
            phrase=payload.phrase,
        )
    except (ValueError, OSError) as exc:
        restore_error = ""
        if was_active and runtime:
            try:
                await runtime.start_listener(persist=False)
            except HermesGatewayError as restore_exc:
                restore_error = f" Tampoco pude restaurar la escucha anterior: {restore_exc}"
        return JSONResponse(
            {"status": "error", "detail": f"{exc}{restore_error}"},
            status_code=422,
        )

    try:
        if was_active and runtime and settings["enabled"]:
            await runtime.start_listener(persist=False)
        snapshot = await hermes_wake_snapshot(request, probe=False)
        snapshot["status"] = "saved"
        return JSONResponse(snapshot)
    except (ValueError, HermesGatewayError) as exc:
        return JSONResponse({"status": "error", "detail": str(exc)}, status_code=422)


@router.post("/api/hermes-wake/runtime")
async def update_hermes_wake_runtime(payload: HermesWakeRuntimeUpdate, request: Request) -> JSONResponse:
    _enforce_local_request(request)
    runtime = _runtime_from(request)
    if runtime is None:
        raise HTTPException(status_code=409, detail="IRIS Core aún no inició el runtime de voz de Hermes.")
    try:
        result = (
            await runtime.start_listener(persist=True)
            if payload.active
            else await runtime.stop_listener(persist=True)
        )
    except HermesGatewayError as exc:
        # A failed stop does not prove that Hermes released the microphone.
        # Preserve the last confirmed runtime truth for every client; the UI
        # may then reconcile with /settings instead of painting a false OFF.
        return JSONResponse(
            {**runtime.snapshot(), "detail": str(exc)},
            status_code=503,
        )
    status_code = 200 if (not payload.active or result.get("active")) else 409
    return JSONResponse(result, status_code=status_code)


class HermesVoiceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["start", "stop", "cancel"]
    client_id: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
    capture_id: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
    confirmed: bool = False


class HermesVoiceSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    streaming: bool = Field(strict=True)


@router.post("/api/hermes-voice/settings")
async def update_hermes_voice_settings(payload: HermesVoiceSettings, request: Request) -> dict[str, Any]:
    _enforce_local_request(request)
    runtime = _runtime_from(request)
    if runtime is None:
        raise HTTPException(status_code=409, detail="El runtime de voz todavía no está listo.")
    if runtime.manual_voice.busy or runtime.state in {"arming", "detected", "recording", "transcribing", "processing"}:
        raise HTTPException(status_code=409, detail="Termina o cancela la intervención antes de cambiar la transcripción.")
    try:
        return configure_voice_streaming(payload.streaming)
    except (ValueError, OSError) as exc:
        # YAML diagnostics may echo the offending line, including an API key.
        raise HTTPException(status_code=422, detail=(
            "No se pudo leer o guardar la configuración STT de Hermes. "
            "Revisa config.yaml; no se cambió la opción."
        )) from exc


@router.get("/api/hermes-voice/status")
async def get_hermes_voice_status(request: Request) -> dict[str, Any]:
    _enforce_local_request(request)
    runtime = _runtime_from(request)
    if runtime is None:
        raise HTTPException(status_code=409, detail="IRIS Core aún no inició el runtime de voz de Hermes.")
    try:
        settings = read_voice_streaming_settings()
    except ValueError:
        settings = {"streaming": False, "error": "No se pudo leer la configuración STT de Hermes."}
    return {**runtime.manual_voice.snapshot(), "settings": settings}


@router.post("/api/hermes-voice/runtime")
async def update_hermes_voice_runtime(payload: HermesVoiceUpdate, request: Request) -> JSONResponse:
    _enforce_local_request(request)
    runtime = _runtime_from(request)
    if runtime is None:
        raise HTTPException(status_code=409, detail="IRIS Core aún no inició el runtime de voz de Hermes.")
    if payload.action == "start":
        verdict = CapabilityPermissionStore().evaluate(
            "microphone.capture", trigger="manual", confirmed=payload.confirmed,
        )
        if not payload.confirmed or verdict["verdict"] != "allow":
            raise HTTPException(status_code=403, detail=(
                "Activa Preguntar para Micrófono en Capability & Permission Center "
                "y confirma la captura manual. La transcripción usa el proveedor STT de Hermes."
            ))
    voice = runtime.manual_voice
    try:
        if payload.action == "start":
            result = await voice.start(payload.client_id, payload.capture_id)
        elif payload.action == "stop":
            result = await voice.stop(payload.client_id, payload.capture_id)
        else:
            result = await voice.cancel(payload.client_id, payload.capture_id)
    except (RuntimeError, TimeoutError) as exc:
        return JSONResponse({**voice.snapshot(), "detail": str(exc)}, status_code=409)
    return JSONResponse(result)


__all__ = [
    "HermesWakeRuntime",
    "WAKE_PRESETS",
    "configure_hermes_wake",
    "infer_wake_mode",
    "normalize_custom_wake_phrase",
    "probe_hermes_wake_requirements",
    "read_hermes_wake_settings",
    "router",
]
