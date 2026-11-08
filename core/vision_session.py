"""Versioned local-only contracts and observed-time accounting for Vision V2."""

from __future__ import annotations

import math
import uuid
from collections import deque
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Seconds = Annotated[float, Field(ge=0, le=315_360_000, allow_inf_nan=False)]
Timestamp = Annotated[float, Field(ge=0, le=1e12, allow_inf_nan=False)]
Confidence = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
Identifier = Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
Motion = Literal["still", "low", "active"]
Reason = Literal[
    "collecting",
    "stabilizing",
    "low_confidence",
    "absent",
    "disabled",
    "paused",
    "camera_released",
    "signal_gap",
    "clock_discontinuity",
    "surface_changed",
    "session_expired",
    "restarted",
    "history_cleared",
    "policy_pending",
    "availability_required",
    "notification_storage",
    "notifications_muted",
    "notification_snoozed",
    "notification_kind_disabled",
    "history_disabled",
    "delivered",
    "below_threshold",
    "eligible",
    "lower_priority",
    "agent_busy",
    "activity_unknown",
    "surface_hidden",
    "quiet_hours",
    "duplicate",
    "hourly_budget",
    "global_cooldown",
    "kind_cooldown",
    "activity_quiet",
    "activity_focus",
    "activity_typing",
    "activity_speaking",
    "activity_listening",
    "activity_gaming",
    "activity_presenting",
    "activity_meeting",
]
REASON_TEXT = {
    "notifications_muted": "Silenciaste temporalmente las nuevas sugerencias de Vision.",
    "notification_snoozed": "Pospusiste un aviso de este tipo; se espera antes de sugerir otro.",
    "notification_kind_disabled": (
        "Desactivaste este tipo de sugerencia; puedes volver a habilitarlo."
    ),
    "availability_required": "Declara tu disponibilidad temporal antes de recibir sugerencias.",
    "notification_storage": "No se pudo confirmar la bandeja local; no se entrega el aviso.",
    "history_disabled": "La retención está en cero; no se crean avisos persistentes.",
    "delivered": "Guardado en la bandeja silenciosa; esto no significa que ya lo hayas leído.",
    "collecting": "Acumulando únicamente intervalos de presencia confirmada.",
    "stabilizing": "Esperando una señal de presencia estable.",
    "low_confidence": "La señal no tiene confianza suficiente para esta evaluación.",
    "absent": "Ausencia observada; el tiempo de presencia está detenido.",
    "disabled": "Vision Assistant está desactivado.",
    "paused": "La observación está pausada.",
    "camera_released": "La cámara dejó de aportar señales; el tiempo está detenido.",
    "signal_gap": "Faltan señales recientes; ese intervalo no se cuenta.",
    "clock_discontinuity": "El reloj cambió; se reinició la referencia temporal.",
    "surface_changed": "Cambió la superficie; no se cuenta el intervalo de transferencia.",
    "session_expired": "Comienza otra sesión tras un intervalo prolongado sin presencia fiable.",
    "restarted": "Se recuperó el tiempo acumulado; se esperan nuevas señales.",
    "history_cleared": "Se borraron los datos acumulados de la sesión.",
    "policy_pending": "Evento pendiente del motor de política; no se ha mostrado ningún aviso.",
    "below_threshold": "Los intervalos observados todavía no cumplen los umbrales.",
    "eligible": "La sugerencia cumple las reglas; todavía no se ha entregado.",
    "lower_priority": "Otra sugerencia tiene prioridad en esta evaluación.",
    "agent_busy": "IRIS está atendiendo una conversación o reproduciendo voz.",
    "activity_unknown": "Falta contexto de actividad fiable; se mantiene silencio.",
    "surface_hidden": "No hay una superficie visible para presentar la sugerencia.",
    "quiet_hours": "Está dentro del horario silencioso configurado.",
    "duplicate": "Esta sugerencia ya tuvo una entrega confirmada.",
    "hourly_budget": "Se alcanzó el límite de intervenciones por hora.",
    "global_cooldown": "Aún no ha transcurrido el tiempo mínimo entre intervenciones.",
    "kind_cooldown": "Este tipo de sugerencia requiere más tiempo entre entregas.",
    "activity_quiet": "El modo silencioso está activo.",
    "activity_focus": "El modo Focus está activo.",
    "activity_typing": "Hay un campo de escritura en uso o actividad de escritura declarada.",
    "activity_speaking": "Hay voz en curso.",
    "activity_listening": "Hay una captura de voz en curso.",
    "activity_gaming": "Está declarado un juego en curso.",
    "activity_presenting": "Está declarada una presentación en curso.",
    "activity_meeting": "Está declarada una reunión en curso.",
}


class VisionContract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, populate_by_name=True)


class MotionWindow(VisionContract):
    """Durations of observed head movement, not body mobility or posture."""

    window_seconds: Seconds = 600.0
    still_seconds: Seconds = 0.0
    low_seconds: Seconds = 0.0
    active_seconds: Seconds = 0.0
    confidence: Confidence = 0.0
    basis: Literal["head_pose"] = "head_pose"

    @model_validator(mode="after")
    def bounded_duration(self) -> MotionWindow:
        if self.observed_seconds > self.window_seconds + 0.0001:
            raise ValueError("Motion durations exceed the observation window")
        return self

    @property
    def observed_seconds(self) -> float:
        return self.still_seconds + self.low_seconds + self.active_seconds


class PresenceSession(VisionContract):
    schema_version: Literal["iris.vision-session/v2"] = Field(
        default="iris.vision-session/v2",
        alias="schema",
    )
    session_id: Identifier
    started_at: Timestamp
    last_observed_at: Timestamp
    observed_presence_seconds: Seconds = 0.0
    motion: MotionWindow = Field(default_factory=MotionWindow)


class VisionCandidate(VisionContract):
    """A local policy candidate. Creating this value never dispatches an action."""

    schema_version: Literal["iris.vision-candidate/v2"] = Field(
        default="iris.vision-candidate/v2",
        alias="schema",
    )
    candidate_id: Identifier
    session_id: Identifier
    kind: Literal["long_session", "low_head_motion", "returned"]
    observed_at: Timestamp
    observed_seconds: Seconds
    confidence: Confidence
    contains_media: Literal[False] = False


class VisionDecision(VisionContract):
    schema_version: Literal["iris.vision-decision/v2"] = Field(
        default="iris.vision-decision/v2",
        alias="schema",
    )
    outcome: Literal["observe", "deferred", "eligible", "suppressed", "delivered"] = "observe"
    reason: Reason
    delivery: Literal["none", "inbox"] = "none"
    candidate_id: Identifier | None = None
    retry_after_seconds: Seconds = 0.0

    def public(self) -> dict:
        return {**self.model_dump(by_alias=True), "explanation": REASON_TEXT[self.reason]}


class VisionSessionTracker:
    """Account only consecutive, stable, server-timed observations.

    Monotonic time measures duration; wall time records dates and detects sleep
    or clock corrections. Checkpoints contain aggregates, never rolling samples.
    """

    MAX_SIGNAL_GAP = 8.0
    SESSION_BREAK = 300.0
    WINDOW_SECONDS = 600.0
    MAX_SEGMENTS = 2048

    def __init__(self, restored: PresenceSession | None = None) -> None:
        self.session = restored
        self.reason: Reason = "restarted" if restored else "stabilizing"
        self._segments: deque[tuple[float, float, Motion, float]] = deque(
            maxlen=self.MAX_SEGMENTS,
        )
        self._previous: tuple[float, float, bool | None, Motion, float, str] | None = None
        self._last_presence_tick: float | None = None
        self._resume_pending = restored is not None
        self.last_tick: float | None = None

    @staticmethod
    def validate_clock(wall: float, tick: float) -> None:
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or not 0 <= value <= 1e12
            for value in (wall, tick)
        ):
            raise ValueError("Vision clocks must be finite, non-negative numbers")

    def interrupt(self, reason: Reason, *, clear: bool = False) -> None:
        self.reason = reason
        self._previous = None
        self._segments.clear()
        if clear:
            self.session = None
            self._last_presence_tick = None
            self._resume_pending = False

    def observe(
        self,
        *,
        present: bool | None,
        motion: Motion,
        confidence: float,
        wall: float,
        tick: float,
        surface: str,
        uncertain_reason: Reason = "stabilizing",
    ) -> None:
        self.validate_clock(wall, tick)
        self.last_tick = tick
        discontinuity: Reason | None = None
        previous = self._previous
        if previous:
            old_wall, old_tick, _, _, _, old_surface = previous
            delta = tick - old_tick
            if delta <= 0 or abs((wall - old_wall) - delta) > 5:
                discontinuity = "clock_discontinuity"
            elif surface != old_surface:
                discontinuity = "surface_changed"
            elif delta > self.MAX_SIGNAL_GAP:
                discontinuity = "signal_gap"
        if discontinuity:
            self.interrupt(discontinuity)
            previous = None
        expired = False
        if self.session and self._resume_pending:
            age = wall - self.session.last_observed_at
            expired = not 0 <= age < self.SESSION_BREAK
            self._last_presence_tick = tick - age
            self._resume_pending = False
        elif self.session and self._last_presence_tick is not None:
            age = tick - self._last_presence_tick
            expired = age < 0 or age >= self.SESSION_BREAK
        if expired:
            self.interrupt("session_expired", clear=True)
            previous = None
            discontinuity = "session_expired"
        self.reason = discontinuity or (
            "collecting" if present is True else "absent" if present is False else uncertain_reason
        )
        if present is True:
            if self.session is None:
                self.session = PresenceSession(
                    session_id=uuid.uuid4().hex,
                    started_at=wall,
                    last_observed_at=wall,
                )
            seconds = self.session.observed_presence_seconds
            if previous and previous[2] is True:
                delta = tick - previous[1]
                # Attribute transitions to the more active endpoint.
                level = max((previous[3], motion), key=("still", "low", "active").index)
                self._segments.append((previous[1], tick, level, min(previous[4], confidence)))
                seconds = min(315_360_000.0, seconds + delta)
            self.session = self.session.model_copy(
                update={
                    "last_observed_at": wall,
                    "observed_presence_seconds": seconds,
                }
            )
            self._last_presence_tick = tick
        else:
            self._segments.clear()
        self._previous = (wall, tick, present, motion, confidence, surface)
        self._prune(tick)

    def _prune(self, tick: float) -> None:
        cutoff = tick - self.WINDOW_SECONDS
        while self._segments and self._segments[0][1] <= cutoff:
            self._segments.popleft()

    def summary(self, tick: float) -> PresenceSession | None:
        if self.session is None:
            return None
        totals = dict.fromkeys(("still", "low", "active"), 0.0)
        confidence_seconds = 0.0
        for start, end, motion, confidence in self._segments:
            duration = max(0.0, min(end, tick) - max(start, tick - self.WINDOW_SECONDS))
            totals[motion] += duration
            confidence_seconds += duration * confidence
        observed = sum(totals.values())
        window = MotionWindow(
            window_seconds=self.WINDOW_SECONDS,
            still_seconds=totals["still"],
            low_seconds=totals["low"],
            active_seconds=totals["active"],
            confidence=min(1.0, confidence_seconds / observed) if observed else 0.0,
        )
        return self.session.model_copy(update={"motion": window})

    def public(self, tick: float, *, reason: Reason | None = None) -> dict:
        current_reason = reason or self.reason
        if (
            reason is None
            and self.last_tick is not None
            and self.reason not in {"disabled", "paused", "camera_released", "history_cleared"}
        ):
            age = tick - self.last_tick
            if age < 0 or age > self.MAX_SIGNAL_GAP:
                current_reason = "signal_gap"
        summary = self.summary(tick)
        return {
            "stage": "contracts",
            "session": summary.model_dump(by_alias=True) if summary else None,
            "candidate": None,
            "decision": VisionDecision(reason=current_reason).public(),
        }

    def checkpoint(self) -> dict | None:
        if self.session is None:
            return None
        return self.session.model_dump(by_alias=True, exclude={"motion"})
