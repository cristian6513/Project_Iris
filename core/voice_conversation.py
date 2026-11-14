"""One window's opt-in, turn-by-turn voice conversation over Hermes capture."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class VoiceSession:
    owner: Any
    id: str
    client_id: str
    route: str
    restore_wake: bool
    capture_id: str = ""
    phase: str = "arming"
    reason: str = ""
    code: str = ""
    playback_id: str = ""
    mic_uncertain: bool = False
    turn: asyncio.Task | None = None
    arm: asyncio.Task | None = None
    cleanup: asyncio.Task | None = None
    watchdog: asyncio.Task | None = None
    last_ping: float = 0.0
    completed_turn: bool = False
    accepted: bool = False


class VoiceConversation:
    SPEAKER_TAIL_SECONDS = 0.35
    OWNER_TIMEOUT_SECONDS = 20.0

    def __init__(self, runtime: Any, *, visible: Callable[[Any], bool], route: Callable[[], str],
                 allowed: Callable[[], bool], busy: Callable[[], bool]) -> None:
        self.runtime = runtime
        self.visible, self.route, self.allowed, self.busy = visible, route, allowed, busy
        self.session: VoiceSession | None = None

    async def notify(self, session: VoiceSession, phase: str, reason: str = "") -> None:
        session.phase = phase
        try:
            await asyncio.wait_for(session.owner.send_json({
                "type": "voice_conversation_state", "payload": {
                    "id": session.id, "phase": phase, "reason": reason,
                    "mic_uncertain": session.mic_uncertain, "code": session.code,
                },
            }), timeout=2)
        except Exception:
            if self.session is session and phase not in {"ending", "off"}:
                self.stop()

    def valid(self, session: VoiceSession) -> bool:
        return (self.session is session and session.phase not in {"ending", "off"}
                and self.visible(session.owner) and self.route() == session.route
                and self.allowed())

    def start(self, owner: Any, session_id: str, client_id: str, *,
              before_listening: Callable[[], Awaitable[None]]) -> None:
        route = self.route()
        wake_busy = self.runtime.state in {
            "arming", "detected", "recording", "transcribing", "processing",
        }
        if self.session is not None or self.runtime.manual_voice.busy or self.busy() or wake_busy:
            raise RuntimeError("Iris está usando la voz o respondiendo. Intenta cuando termine.")
        if not route or not self.visible(owner):
            raise RuntimeError("Abre una ventana local de Iris y espera a que se conecte.")
        if not self.allowed():
            raise PermissionError("El micrófono está bloqueado en Permisos.")
        session = VoiceSession(owner, session_id, client_id, route, self.runtime.listening)
        self.session = session
        self.keep_alive(owner)
        session.watchdog = asyncio.create_task(self._watch_owner(session), name="IRIS-voice-owner")
        session.arm = asyncio.create_task(
            self._arm(session, before_listening=before_listening), name="IRIS-voice-arm",
        )

    def keep_alive(self, owner: Any) -> None:
        session = self.session
        if session is not None and session.owner is owner:
            session.last_ping = asyncio.get_running_loop().time()

    async def _watch_owner(self, session: VoiceSession) -> None:
        clock = asyncio.get_running_loop().time
        while self.session is session and session.phase != "ending":
            await asyncio.sleep(max(0.05, session.last_ping + self.OWNER_TIMEOUT_SECONDS - clock()))
            if clock() - session.last_ping >= self.OWNER_TIMEOUT_SECONDS:
                self.stop("Se perdió la conexión con la ventana que usaba el micrófono.")
                return

    def stop(self, reason: str = "", *, restore_wake: bool = True,
             code: str = "") -> asyncio.Task | None:
        session = self.session
        if session is None:
            return None
        session.restore_wake &= restore_wake
        if session.phase != "ending":
            session.phase = "ending"
            session.reason = reason
            session.code = code
            if session.watchdog is not asyncio.current_task() and session.watchdog is not None:
                session.watchdog.cancel()
        if session.cleanup is None or session.cleanup.done():
            session.cleanup = asyncio.create_task(self._finish(session), name="IRIS-voice-close")
        return session.cleanup

    async def _arm(self, session: VoiceSession, *,
                   before_listening: Callable[[], Awaitable[None]] | None = None) -> None:
        try:
            if before_listening is not None:
                if not self.valid(session):
                    self.stop()
                    return
                await before_listening()
                if session.restore_wake and self.valid(session):
                    # Hermes may resume a paused Wake when a take ends. Release it until End.
                    await self.runtime.stop_listener(persist=False)
                    await self.runtime._emit("wake.resumed", self.runtime.snapshot())
            await asyncio.sleep(self.SPEAKER_TAIL_SECONDS)
            # The previous take can publish its final state before release has
            # finished notifying the other surfaces. Wait for that same lock.
            async with self.runtime.manual_voice._lock:
                pass
            if not self.valid(session) or self.busy():
                self.stop("La ventana, los permisos o la conversación cambiaron.")
                return
            session.capture_id = uuid.uuid4().hex
            session.completed_turn = False
            if before_listening is None:
                await self.notify(session, "arming")
            if not self.valid(session) or self.busy():
                self.stop()
                return
            result = await self.runtime.manual_voice.start(
                session.client_id, session.capture_id,
                still_allowed=lambda: self.valid(session) and not self.busy(),
            )
            if self.valid(session) and result["state"] == "recording" and session.phase == "arming":
                await self.notify(session, "listening")
            elif not result["busy"] and session.phase == "arming":
                self.stop("No se pudo iniciar el micrófono.")
        except Exception:
            logger.warning("Could not start conversational microphone", exc_info=True)
            self.stop("No se pudo iniciar el micrófono. Revisa la configuración de voz.")

    async def _finish(self, session: VoiceSession) -> None:
        if not session.mic_uncertain:
            await self.notify(session, "ending", session.reason)
        if session.arm is not None and not session.arm.done():
            await asyncio.gather(session.arm, return_exceptions=True)
        voice = self.runtime.manual_voice
        if self.owns_capture(session, voice.snapshot()) and voice.busy:
            if voice.state == "processing":
                # Capture is already closed. Release will complete an accepted turn.
                return
            try:
                await voice.cancel(session.client_id, session.capture_id)
            except Exception:
                session.mic_uncertain = True
                session.reason = "No se pudo confirmar el cierre del micrófono."
                await self.notify(session, "ending", session.reason)
                return
        session.mic_uncertain = False
        try:
            if (session.restore_wake and not self.runtime.listening
                    and not voice.busy and self.allowed()):
                await self.runtime.start_listener(persist=False)
                await self.runtime._emit("wake.resumed", self.runtime.snapshot())
        except Exception:
            logger.warning("Could not restore Wake after conversational voice", exc_info=True)
            session.reason = session.reason or "Revisa la frase de activación en Configuración."
        self.session = None
        await self.notify(session, "off", session.reason)

    @staticmethod
    def owns_capture(session: VoiceSession, payload: dict[str, Any]) -> bool:
        return bool(session.capture_id and payload.get("client_id") == session.client_id
                    and payload.get("capture_id") == session.capture_id)

    async def capture_state(self, payload: dict[str, Any]) -> None:
        session = self.session
        if session is None or not self.owns_capture(session, payload):
            return
        if session.phase == "ending":
            if not payload["busy"]:
                self.stop()  # A turn that was processing has now released the capture.
            return
        if not self.valid(session):
            self.stop("La ventana, los permisos o la conversación cambiaron.")
        elif payload["state"] == "unknown":
            self.stop("No se pudo confirmar el cierre del micrófono.")
        elif payload["state"] in {"transcribing", "stopping"}:
            await self.notify(session, "transcribing")
        elif not payload["busy"]:
            if session.completed_turn and payload["state"] == "off":
                session.completed_turn = False
                session.accepted = False
                session.capture_id = ""
                session.arm = asyncio.create_task(self._arm(session), name="IRIS-voice-rearm")
            else:
                self.stop(payload.get("error") or "Pulsa Hablar para reiniciar la conversación.")

    async def transcript(self, session: VoiceSession, text: str, *,
                         reply: Callable[[VoiceSession, str], Awaitable[None]]) -> None:
        if not self.valid(session) or session.accepted:
            return
        if self.runtime.manual_voice.state != "processing":
            return
        session.accepted = True
        session.turn = asyncio.current_task()
        try:
            await self.notify(session, "replying")
            if self.valid(session):
                await reply(session, text)
                session.completed_turn = self.valid(session)
        except asyncio.CancelledError:
            if self.valid(session):
                self.stop("La respuesta se interrumpió. Puedes iniciar otra conversación por voz.")
            # The voice RPC owns a separate event consumer. A stopped chat turn
            # must return control to it so the microphone can finish cleanup.
        except Exception:
            logger.exception("Conversational voice turn failed")
            self.stop("Iris no pudo completar la respuesta. Vuelve a iniciar la conversación.")
        finally:
            session.playback_id = ""
            session.turn = None

    async def playback_started(self, session: VoiceSession) -> None:
        if self.valid(session) and session.phase == "replying":
            await self.notify(session, "speaking")
