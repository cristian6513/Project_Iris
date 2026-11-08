"""One-shot manual capture over Hermes' existing voice RPC, never a wake engine."""
from __future__ import annotations

import asyncio
import uuid
from collections import deque
from typing import Any

MAX_PARTIAL_CHARS = 4096


def partial_text(payload: dict[str, Any]) -> str:
    """Bound untrusted provisional text; never interpret it as a user turn."""
    value = payload.get("text")
    return value[:MAX_PARTIAL_CHARS].strip() if isinstance(value, str) else ""


class ManualVoiceCapture:
    MAX_CAPTURE_SECONDS = 120

    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime
        self.state = "off"
        self.revision = 0
        self.error = ""
        self.client_id = ""
        self.capture_id = ""
        self.intercept = False
        self.resume_wake = False
        self.partial = ""
        self.partial_sequence = 0
        self.generation = 0
        self._timer: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._cancelled: deque[tuple[str, str]] = deque(maxlen=128)

    @property
    def busy(self) -> bool:
        return self.state not in {"off", "error"}

    def snapshot(self) -> dict[str, Any]:
        return {"state": self.state, "busy": self.busy,
                "active": self.state in {"arming", "recording", "stopping", "unknown"},
                "client_id": self.client_id, "capture_id": self.capture_id,
                "error": self.error, "mode": "manual", "max_seconds": self.MAX_CAPTURE_SECONDS,
                "revision": self.revision}

    async def publish(self) -> None:
        self.revision += 1
        await self.runtime._emit("manual_voice.state", self.snapshot())

    async def rpc(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        return self.runtime._rpc_object(method, await self.runtime.request(method, params, timeout=30.0))

    def _clear_timer(self) -> None:
        timer, self._timer = self._timer, None
        if timer and timer is not asyncio.current_task():
            timer.cancel()

    def disconnected(self) -> None:
        self.generation += 1
        self._clear_timer()
        self.resume_wake = False
        self.state = "off"
        self.partial = ""

    async def start(self, client_id: str, capture_id: str | None = None) -> dict[str, Any]:
        if (client_id, capture_id) in self._cancelled:
            raise RuntimeError("La grabación ya fue cancelada desde esa ventana.")
        if self.busy or self._lock.locked():
            raise RuntimeError("El micrófono manual ya está ocupado.")
        if self.runtime.state in {"arming", "detected", "recording", "transcribing", "processing"}:
            raise RuntimeError("Hermes está usando la voz; termina esa intervención primero.")
        async with self._lock:
            self.generation += 1
            self.client_id = client_id
            self.capture_id = capture_id or uuid.uuid4().hex
            self.intercept = True
            self.partial = ""
            self.partial_sequence = 0
            self.error = ""
            self.state = "arming"
            self.resume_wake = bool(self.runtime.listening)
            await self.publish()
            try:
                # Do not start, probe or reconfigure the wake detector. Manual
                # capture must work even when the configured wake engine fails.
                if self.resume_wake:
                    await self.rpc("wake.pause", {})
                    self.runtime.listening = False
                    self.runtime.state = "paused"
                    await self.runtime._emit("wake.resumed", self.runtime.snapshot())
                enabled = await self.rpc("voice.toggle", {"action": "on"})
                if enabled.get("enabled") is not True:
                    raise RuntimeError("Hermes no confirmó el modo de voz.")
                result = await self.rpc("voice.record", {"action": "start"})
                if result.get("status") != "recording":
                    raise RuntimeError("Hermes no pudo iniciar la grabación: " + str(result.get("reason") or result.get("status") or "respuesta desconocida"))
                if self.state == "arming":
                    self.state = "recording"
                self._timer = asyncio.create_task(self._expire(self.capture_id), name="IRIS-manual-voice-limit")
            except Exception as exc:
                self.error = str(exc)
                await self._release(reset_transport=True)
                raise
            await self.publish()
            return self.snapshot()

    async def _expire(self, capture_id: str) -> None:
        await asyncio.sleep(self.MAX_CAPTURE_SECONDS)
        if capture_id == self.capture_id and self.state in {"arming", "recording", "stopping", "transcribing"}:
            await self.cancel(self.client_id, capture_id, reason="Grabación cancelada por límite de tiempo.")

    def _check_owner(self, client_id: str, capture_id: str) -> None:
        if client_id != self.client_id or capture_id != self.capture_id:
            raise RuntimeError("Esta grabación pertenece a otra ventana o ya terminó.")

    async def stop(self, client_id: str, capture_id: str) -> dict[str, Any]:
        self._check_owner(client_id, capture_id)
        async with self._lock:
            self._check_owner(client_id, capture_id)
            if self.state != "recording":
                return self.snapshot()
            self.state = "stopping"
            await self.publish()
            try:
                result = await self.rpc("voice.record", {"action": "stop"})
                if result.get("status") != "stopped":
                    raise RuntimeError("Hermes no confirmó el cierre de la captura.")
                if self.state == "stopping":
                    self.state = "transcribing"
            except Exception as exc:
                self.error = str(exc)
                await self._release(reset_transport=True)
                raise
            await self.publish()
            return self.snapshot()

    async def cancel(self, client_id: str, capture_id: str, *, reason: str = "") -> dict[str, Any]:
        # A pagehide request may arrive before the start request. Remember the
        # exact pair, without stopping a different window's current capture.
        self._cancelled.append((client_id, capture_id))
        self._check_owner(client_id, capture_id)
        async with self._lock:
            self._check_owner(client_id, capture_id)
            if not self.busy or self.state == "processing":
                return self.snapshot()
            self.state = "cancelling"
            self.error = reason
            await self.publish()
            # Hermes' transcription callbacks have no capture generation.
            # Retire our isolated transport on cancellation so a late result
            # cannot become a command in the next recording (or wake session).
            if self.resume_wake:
                self.error = (reason + " Wake quedó apagado; puedes reactivarlo en Configuración.").strip()
            await self._release(reset_transport=True)
            return self.snapshot()

    async def _release(self, *, reset_transport: bool = False) -> None:
        self.partial = ""
        self._clear_timer()
        resume = self.resume_wake and not reset_transport
        self.resume_wake = False
        try:
            if not self.runtime.running:
                raise RuntimeError("El servicio de voz de Hermes no está conectado.")
            result = await self.rpc("voice.toggle", {"action": "off"})
            if result.get("enabled") is not False:
                raise RuntimeError("Hermes no confirmó la liberación del micrófono.")
        except Exception as exc:
            self.error = self.error or str(exc)
            reset_transport = True
            resume = False
        if reset_transport:
            # close() only terminates the Hermes serve process owned by IRIS.
            try:
                await self.runtime.close()
            except Exception as exc:
                self.state = "unknown"
                self.error = f"No se pudo confirmar el cierre del micrófono: {exc}"
                await self.publish()
                raise RuntimeError(self.error) from exc
        elif resume:
            try:
                result = await self.rpc("wake.resume", {})
                self.runtime.listening = bool(result.get("resumed"))
            except Exception as exc:
                self.error = str(exc)
                self.runtime.listening = False
        self.runtime.state = "listening" if self.runtime.listening else "off"
        self.state = "error" if self.error else "off"
        await self.publish()
        await self.runtime._emit("wake.resumed", self.runtime.snapshot())

    async def handle_event(self, event_type: str, payload: dict[str, Any]) -> bool:
        if event_type == "wake.detected":
            if self.busy or self._lock.locked():
                return True
            self.intercept = False
            return False
        if not self.intercept or event_type not in {"voice.status", "voice.transcript", "voice.partial"}:
            return False
        text = ""
        async with self._lock:
            if not self.busy or self.state in {"processing", "cancelling"}:
                return True
            if event_type == "voice.partial":
                text = partial_text(payload)
                if text and text != self.partial and self.state in {"recording", "stopping", "transcribing"}:
                    self.partial = text
                    self.partial_sequence += 1
                    await self.runtime._emit("manual_voice.partial", {
                        "text": text, "client_id": self.client_id, "capture_id": self.capture_id,
                        "sequence": self.partial_sequence, "mode": "manual",
                    })
                return True
            if event_type == "voice.status":
                status = payload.get("state")
                if status == "transcribing":
                    self.state = "transcribing"
                elif status == "idle":
                    self.error = "Hermes no devolvió una transcripción. Revisa el micrófono y el proveedor STT."
                    await self._release()
                    return True
                # A delayed 'listening' event must not reopen a stopped capture.
                await self.publish()
                return True
            text = str(payload.get("text") or "").strip()
            if not text or payload.get("stop_phrase") or payload.get("no_speech_limit"):
                self.error = "Grabación detenida." if payload.get("stop_phrase") else "Hermes no detectó una orden de voz."
                await self._release()
                return True
            self._clear_timer()
            self.partial = ""
            generation = self.generation
            self.state = "processing"
            try:
                # Hermes may resume Wake when the native recorder ends.
                if self.resume_wake:
                    await self.rpc("wake.pause", {})
                result = await self.rpc("voice.toggle", {"action": "off"})
                if result.get("enabled") is not False:
                    raise RuntimeError("No se confirmó el cierre del micrófono.")
            except Exception as exc:
                self.error = str(exc)
                await self._release(reset_transport=True)
                return True
            await self.publish()
        # Do not hold the capture lock during a potentially long agent turn.
        try:
            await self.runtime._emit("manual_voice.transcript", {
                "text": text, "client_id": self.client_id, "capture_id": self.capture_id,
            })
        except Exception as exc:
            if generation == self.generation:
                self.error = f"No se pudo enviar la transcripción: {exc}"
        finally:
            async with self._lock:
                # A disconnected service may have already admitted a new take
                # while the previous agent turn was finishing. Its cleanup and
                # error belong only to the original capture, never the new one.
                if generation == self.generation:
                    await self._release()
        return True
