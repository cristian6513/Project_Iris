"""Privacy-first state machine for IRIS Vision Assistant.

The browser/Companion keeps ownership of every camera frame.  This module only
accepts small, derived presence signals and stores a bounded local event log.
It deliberately has no OpenCV, MediaPipe, network, image or audio dependency.
"""

from __future__ import annotations

import json
import logging
import math
import os
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from core.config import get_iris_home
from core.file_lock import interprocess_file_lock
from core.notification_actions import NotificationActionsMixin
from core.vision_diagnostics import VisionDiagnostics
from core.vision_experience import effective_cooldown, effective_policy, physical_copy
from core.vision_experience_actions import VisionExperienceActionsMixin
from core.vision_notifications import NotificationInbox, ReturnEvidence, VisionNotification
from core.vision_policy import (
    DeliveryReceipt,
    PolicyCheckpoint,
    VisionActivity,
    VisionPolicyEngine,
    VisionPolicySettings,
)
from core.vision_return import VisionReturnTracker
from core.vision_session import (
    PresenceSession,
    Reason,
    VisionCandidate,
    VisionDecision,
    VisionSessionTracker,
)

logger = logging.getLogger(__name__)

FORBIDDEN_SIGNAL_FIELDS = frozenset(
    {
        "audio",
        "blob",
        "bytes",
        "data",
        "data_url",
        "frame",
        "frames",
        "image",
        "images",
        "pixels",
        "video",
    }
)
MOTION_LEVELS = frozenset({"still", "low", "active"})
EVENT_TYPES = frozenset({"present", "absent", "returned"})
ALLOWED_SIGNAL_FIELDS = frozenset({"present", "confidence", "motion"})
EMOTION_LABELS = frozenset(
    {"happy", "sad", "surprised", "angry", "fearful", "disgusted", "confused", "neutral"}
)
ALLOWED_EMOTION_FIELDS = frozenset({"label", "valence", "arousal", "intensity", "confidence"})


class VisionSignalError(ValueError):
    """Raised when a surface attempts to publish unsafe or malformed data."""


def normalize_vision_signal(payload: Any) -> dict[str, Any]:
    """Return the safe, bounded subset of one derived browser signal.

    Raw media-like keys are rejected rather than ignored so a cached or hostile
    surface cannot accidentally turn this endpoint into a frame transport.
    """
    if not isinstance(payload, dict):
        raise VisionSignalError("La señal visual debe ser un objeto JSON.")
    forbidden = FORBIDDEN_SIGNAL_FIELDS.intersection(str(key).casefold() for key in payload)
    if forbidden:
        names = ", ".join(sorted(forbidden))
        raise VisionSignalError(f"Vision Assistant no acepta contenido multimedia ({names}).")
    unknown = set(payload) - ALLOWED_SIGNAL_FIELDS
    if unknown:
        names = ", ".join(sorted(str(key) for key in unknown))
        raise VisionSignalError(f"La señal visual contiene campos no reconocidos ({names}).")
    if type(payload.get("present")) is not bool:
        raise VisionSignalError("present debe ser booleano.")
    if isinstance(payload.get("confidence", 0.0), bool):
        raise VisionSignalError("confidence debe ser numérico.")
    try:
        confidence = float(payload.get("confidence", 0.0))
    except (TypeError, ValueError) as exc:
        raise VisionSignalError("confidence debe ser numérico.") from exc
    if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
        raise VisionSignalError("confidence debe estar entre 0 y 1.")
    motion = str(payload.get("motion") or "still").strip().casefold()
    if motion not in MOTION_LEVELS:
        raise VisionSignalError("motion debe ser still, low o active.")
    return {
        "present": payload["present"],
        "confidence": round(confidence, 4),
        "motion": motion,
    }


def normalize_user_emotion(payload: Any) -> dict[str, Any]:
    """Validate derived emotion context before it can reach Hermes."""
    if not isinstance(payload, dict):
        raise VisionSignalError("La señal emocional debe ser un objeto JSON.")
    forbidden = FORBIDDEN_SIGNAL_FIELDS.intersection(str(key).casefold() for key in payload)
    if forbidden:
        names = ", ".join(sorted(forbidden))
        raise VisionSignalError(f"El contexto perceptivo no acepta contenido multimedia ({names}).")
    unknown = set(payload) - ALLOWED_EMOTION_FIELDS
    if unknown:
        names = ", ".join(sorted(str(key) for key in unknown))
        raise VisionSignalError(f"La señal emocional contiene campos no reconocidos ({names}).")

    label = str(payload.get("label") or "").strip().casefold()
    if label not in EMOTION_LABELS:
        raise VisionSignalError("La etiqueta emocional no está permitida.")

    normalized: dict[str, Any] = {"label": label}
    for name, lower, upper in (
        ("valence", -1.0, 1.0),
        ("arousal", 0.0, 1.0),
        ("intensity", 0.0, 1.0),
        ("confidence", 0.0, 1.0),
    ):
        value = payload.get(name, 0.0)
        if isinstance(value, bool):
            raise VisionSignalError(f"{name} debe ser numérico.")
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise VisionSignalError(f"{name} debe ser numérico.") from exc
        if not math.isfinite(number) or not lower <= number <= upper:
            raise VisionSignalError(f"{name} debe estar entre {lower:g} y {upper:g}.")
        normalized[name] = round(number, 4)
    return normalized


class VisionAssistantRuntime(NotificationActionsMixin, VisionExperienceActionsMixin):
    """Stabilize presence transitions and keep a privacy-safe local history."""

    def __init__(self, event_path: Path | None = None, *, stable_samples: int = 2) -> None:
        self.event_path = event_path or (get_iris_home() / "vision_events.json")
        self.state_path = self.event_path.with_name(f"{self.event_path.stem}_state.json")
        self.stable_samples = max(2, int(stable_samples))
        self._lock = threading.RLock()
        self._presence: bool | None = None
        self._candidate: bool | None = None
        self._candidate_count = 0
        self._last_action_at = self._load_last_action_at()
        self._last_signal_at = 0.0
        self._session = VisionSessionTracker(self._load_session())
        self._checkpoint_tick: float | None = None
        self._state_persisted = True
        self._retention_days = 0
        self._policy = VisionPolicyEngine()
        self._policy_clock = (time.time(), time.monotonic())
        self._activities: dict[str, tuple[VisionActivity, float]] = {}
        self._policy_settings = VisionPolicySettings()
        self._signal_confidence = 0.0
        self._notification_storage_error = False
        self._inbox = self._load_inbox()
        self._stored_inbox_revision = self._inbox.revision
        self._inbox_retry_tick = 0.0
        self._availability_clock: tuple[float, float] | None = None
        self._return_tracker = VisionReturnTracker()
        self._presentation_attempts: dict[str, None] = {}
        self._diagnostics = VisionDiagnostics()

    def reset_observation(self, reason: Reason = "camera_released") -> None:
        """Forget transient camera state without deleting the user's history."""
        with self._lock:
            self._presence = None
            self._candidate = None
            self._candidate_count = 0
            self._last_signal_at = 0.0
            self._session.interrupt(reason)
            self._activities.clear()
            self._policy.invalidate_offers()
            self._availability_clock = None
            self._return_tracker.reset()
            self._diagnostics.sensor = None

    def update_activity(
        self,
        payload: Any,
        *,
        surface_id: str,
        monotonic_now: float | None = None,
    ) -> None:
        """Record an ephemeral local activity summary, never user input."""
        try:
            activity = VisionActivity.model_validate(payload)
        except ValidationError as exc:
            raise VisionSignalError(
                "Actividad visual inválida; usa solo indicadores booleanos."
            ) from exc
        tick = time.monotonic() if monotonic_now is None else monotonic_now
        VisionSessionTracker.validate_clock(0.0, tick)
        with self._lock:
            if surface_id not in self._activities and len(self._activities) >= 16:
                self._activities.pop(next(iter(self._activities)))
            self._activities[surface_id] = (activity, tick)
            self._policy.invalidate_offers()

    def _activity_context(self, surface_ids: tuple[str, ...], tick: float) -> VisionActivity:
        rows = []
        for surface_id in surface_ids:
            row = self._activities.get(surface_id)
            rows.append(row[0] if row and 0 <= tick - row[1] <= 8 else VisionActivity())
        combined = VisionActivity.combine(rows)
        if self.availability(wall=self._policy_clock[0], tick=tick)["available"]:
            # Explicit, short-lived self-report fills unknown activity, never visibility.
            # Positive evidence from any real surface still wins.
            combined = combined.model_copy(
                update={
                    name: False
                    for name in VisionActivity.model_fields
                    if name != "visible" and getattr(combined, name) is None
                }
            )
        return combined

    def ingest(
        self,
        payload: Any,
        *,
        surface_id: str,
        enabled: bool,
        paused_until: float,
        cooldown_minutes: int,
        retention_days: int,
        now: float | None = None,
        monotonic_now: float | None = None,
        policy_settings: VisionPolicySettings | None = None,
        surface_role: str = "unknown",
        activity_surfaces: tuple[str, ...] = (),
        backend_busy: bool = False,
    ) -> dict[str, Any]:
        """Account presence and durably deliver eligible V2 notes; preserve V1 events."""
        signal = normalize_vision_signal(payload)
        wall = time.time() if now is None else now
        tick = time.monotonic() if monotonic_now is None else monotonic_now
        try:
            VisionSessionTracker.validate_clock(wall, tick)
        except ValueError as exc:
            raise VisionSignalError(str(exc)) from exc
        with self._lock:
            self._policy_clock = (wall, tick)
            self._signal_confidence = signal["confidence"]
            self._policy_settings = policy_settings or self._policy_settings
            retention_changed = self._retention_days != retention_days
            self._retention_days = retention_days
            result = self._ingest_presence(
                signal,
                surface_id=surface_id,
                enabled=enabled,
                paused_until=paused_until,
                cooldown_minutes=cooldown_minutes,
                retention_days=retention_days,
                now=wall,
            )
            if result["reason"] in {"disabled", "paused"}:
                self.reset_observation(result["reason"])
            else:
                reliable = signal["confidence"] >= 0.55
                present = (
                    signal["present"] if reliable and self._presence is signal["present"] else None
                )
                if not reliable:
                    self._candidate = None
                    self._candidate_count = 0
                self._session.observe(
                    present=present,
                    motion=signal["motion"],
                    confidence=signal["confidence"],
                    wall=wall,
                    tick=tick,
                    surface=surface_id,
                    uncertain_reason="stabilizing" if reliable else "low_confidence",
                )
            result["v2"] = self._session.public(tick)
            absence = self._return_tracker.observe(
                signal=signal,
                stable_presence=self._presence,
                event=result["event"],
                surface=surface_id,
                wall=wall,
                tick=tick,
                enabled=enabled and paused_until <= wall,
            )
            return_candidate, return_evidence = self._return_offer(
                result["event"],
                absence=absence,
                wall=wall,
                tick=tick,
                confidence=signal["confidence"],
            )
            result["v2"].update(
                self._policy.evaluate(
                    self._session.summary(tick),
                    observation_reason=result["v2"]["decision"]["reason"],
                    activity=self._activity_context(activity_surfaces or (surface_id,), tick),
                    settings=effective_policy(
                        self._policy_settings, self._inbox.preferences.experience
                    ),
                    wall=wall,
                    tick=tick,
                    confidence=signal["confidence"],
                    cooldown_minutes=effective_cooldown(
                        cooldown_minutes, self._inbox.preferences.experience
                    ),
                    backend_busy=backend_busy,
                    blocked_kinds=self._notification_blocks(wall),
                    return_candidate=return_candidate,
                    return_evidence=return_evidence,
                )
            )
            result["notification"] = self._deliver_notification(
                result["v2"],
                wall=wall,
                tick=tick,
                retention_days=retention_days,
            )
            result["state_persisted"] = self._checkpoint_session(
                tick,
                force=retention_changed or result["event"] is not None,
            )
            self._diagnostics.record(
                result["v2"], wall=wall, role=surface_role, retention_days=retention_days
            )
            return result

    def return_presentation(self, notification_id, *, activity_surfaces, wall=None, tick=None):
        wall = time.time() if wall is None else wall
        tick = time.monotonic() if tick is None else tick
        with self._lock:
            if not self._inbox.preferences.experience.greetings:
                return None
            self._policy_clock = (wall, tick)
            if not self.availability(wall=wall, tick=tick)["available"]:
                return None
            if "returned" in self._notification_blocks(wall):
                return None
            activity = self._activity_context(activity_surfaces, tick)
            if activity.visible is not True or any(
                getattr(activity, flag) is not False
                for flag in VisionActivity.model_fields
                if flag != "visible"
            ):
                return None
            note = self._notification_for_action(notification_id, self._retention_days, wall)
            if (
                note is None
                or note.candidate.kind != "returned"
                or note.read_at is not None
                or note.dismissed_at is not None
                or note.snoozed_until > wall
                or not 0 <= wall - note.delivered_at <= 8
                or not self._state_persisted
                or self._notification_storage_error
                or notification_id in self._presentation_attempts
            ):
                return None
            self._presentation_attempts[notification_id] = None
            if len(self._presentation_attempts) > 256:
                self._presentation_attempts.pop(next(iter(self._presentation_attempts)))
            return {
                "id": notification_id,
                "kind": "returned",
                "animation": "wave",
                "created_at": note.delivered_at,
                "expires_at": note.delivered_at + 8,
                "delivery": "inbox",
                "contains_media": False,
            }

    def _return_offer(self, event, *, absence, wall, tick, confidence):
        if not event or event["type"] != "returned" or not absence:
            return None, None
        session = self._session.summary(tick)
        if session is None:
            return None, None
        items = self._inbox.retained(days=self._retention_days, wall=wall)
        summarized = {
            identifier
            for note in items
            if isinstance(note.evidence, ReturnEvidence)
            for identifier in note.evidence.related_ids
        }
        related = [
            note.candidate.candidate_id
            for note in items
            if note.candidate.kind != "returned"
            and note.candidate.candidate_id not in summarized
            and note.read_at is None
            and note.dismissed_at is None
            and note.snoozed_until <= wall
            and note.delivered_at <= wall
            and note.candidate.kind not in self._notification_blocks(wall)
        ]
        if not related:
            return None, None
        candidate = VisionCandidate(
            candidate_id=event["id"],
            session_id=session.session_id,
            kind="returned",
            observed_at=wall,
            observed_seconds=absence,
            confidence=confidence,
        )
        evidence = ReturnEvidence(
            observed_seconds=absence,
            confidence=confidence,
            related_ids=related,
        )
        return candidate, evidence.model_dump()

    def _ingest_presence(
        self,
        payload: Any,
        *,
        surface_id: str,
        enabled: bool,
        paused_until: float,
        cooldown_minutes: int,
        retention_days: int,
        now: float | None = None,
    ) -> dict[str, Any]:
        signal = normalize_vision_signal(payload)
        timestamp = float(time.time() if now is None else now)
        if not enabled:
            return {"accepted": False, "reason": "disabled", "event": None}
        if paused_until > timestamp:
            return {"accepted": False, "reason": "paused", "event": None}
        if signal["confidence"] < 0.55:
            return {"accepted": False, "reason": "low_confidence", "event": None}

        with self._lock:
            self._last_signal_at = timestamp
            present = bool(signal["present"])
            if self._candidate is present:
                self._candidate_count += 1
            else:
                self._candidate = present
                self._candidate_count = 1
            if self._candidate_count < self.stable_samples or self._presence is present:
                return {"accepted": True, "reason": "stabilizing", "event": None}

            previous = self._presence
            self._presence = present
            self._candidate_count = 0
            if present and previous is False:
                event_type = "returned"
            elif present:
                event_type = "present"
            else:
                event_type = "absent"

            # V1 history remains informational. Only a durable V2 notification
            # may request a targeted presentation; cached clients cannot greet twice.
            actionable = False
            suppression_reason = "notification_policy" if event_type == "returned" else ""
            event = {
                "id": uuid.uuid4().hex,
                "type": event_type,
                "label": {
                    "present": "Presencia detectada",
                    "absent": "Ausencia detectada",
                    "returned": "Regreso detectado",
                }[event_type],
                "surface_id": str(surface_id or "surface")[:96],
                "confidence": signal["confidence"],
                "motion": signal["motion"],
                "observed_at": timestamp,
                "actionable": actionable,
                "suppression_reason": suppression_reason,
                "contains_media": False,
            }
            history_persisted = True
            try:
                self._append_event(event, retention_days=retention_days, now=timestamp)
            except OSError:
                history_persisted = False
                logger.warning("Could not persist Vision event", exc_info=True)
            return {
                "accepted": True,
                "reason": "transition",
                "event": event,
                "history_persisted": history_persisted,
            }

    def events(
        self, *, retention_days: int, limit: int = 80, now: float | None = None
    ) -> list[dict[str, Any]]:
        timestamp = float(time.time() if now is None else now)
        with self._lock, interprocess_file_lock(self.event_path.with_suffix(".lock")):
            rows, dirty = self._load_events()
            retained = self._prune(rows, retention_days=retention_days, now=timestamp)
            if dirty or retained != rows:
                self._atomic_write(retained)
            return retained[-max(1, min(int(limit), 200)) :][::-1]

    def enforce_retention(self, *, retention_days: int, now: float | None = None) -> None:
        """Physically remove expired or malformed rows from the local log."""
        with self._lock:
            self.events(retention_days=retention_days, limit=200, now=now)
            self._retention_days = retention_days
            self._checkpoint_session(time.monotonic(), force=True)
            self.notification_snapshot(retention_days=retention_days, wall=now)

    def clear_events(self) -> None:
        with self._lock, interprocess_file_lock(self.event_path.with_suffix(".lock")):
            self._atomic_write([])
            self._diagnostics.clear()
            self._session.interrupt("history_cleared", clear=True)
            self._return_tracker.reset()
            self._policy.invalidate_offers()
            previous = self._inbox
            self._inbox = self._inbox.changed([])
            try:
                self._persist_last_action_at(self._last_action_at)
            except OSError:
                self._inbox = previous
                raise

    def snapshot(
        self,
        *,
        enabled: bool,
        paused_until: float,
        camera: dict[str, Any],
        now: float | None = None,
        monotonic_now: float | None = None,
        policy_settings: VisionPolicySettings | None = None,
        cooldown_minutes: int = 25,
        activity_surfaces: tuple[str, ...] = (),
        backend_busy: bool = False,
    ) -> dict[str, Any]:
        timestamp = float(time.time() if now is None else now)
        tick = time.monotonic() if monotonic_now is None else monotonic_now
        paused = bool(enabled and paused_until > timestamp)
        if not enabled:
            state = "off"
        elif paused:
            state = "paused"
        elif not camera.get("active"):
            state = "waiting_camera"
        elif self._last_signal_at and 0 <= timestamp - self._last_signal_at <= 8:
            state = "observing"
        else:
            state = "camera_ready"
        with self._lock:
            reason = (
                "disabled"
                if not enabled
                else "paused"
                if paused
                else "camera_released"
                if not camera.get("active")
                else None
            )
            self._policy_clock = (timestamp, tick)
            v2 = self._session.public(tick, reason=reason)
            settings = effective_policy(
                policy_settings or self._policy_settings, self._inbox.preferences.experience
            )
            v2.update(
                self._policy.evaluate(
                    self._session.summary(tick),
                    observation_reason=v2["decision"]["reason"],
                    activity=self._activity_context(activity_surfaces, tick),
                    settings=settings,
                    wall=timestamp,
                    tick=tick,
                    confidence=self._signal_confidence,
                    cooldown_minutes=effective_cooldown(
                        cooldown_minutes, self._inbox.preferences.experience
                    ),
                    backend_busy=backend_busy,
                    blocked_kinds=self._notification_blocks(timestamp),
                )
            )
            self._prepare_notification(
                v2, wall=timestamp, tick=tick, retention_days=self._retention_days
            )
        return {
            "state": state,
            "enabled": bool(enabled),
            "paused": paused,
            "paused_until": float(paused_until),
            "camera": camera,
            "presence": self._presence,
            "last_signal_at": self._last_signal_at,
            "v2": v2,
            "experience": self._inbox.preferences.experience.model_dump(),
            "effective_policy": settings.model_dump(),
            "effective_cooldown_minutes": effective_cooldown(
                cooldown_minutes, self._inbox.preferences.experience
            ),
            "privacy": {
                "processing": "local",
                "automatic_media_upload": False,
                "stored_media": False,
            },
        }

    @staticmethod
    def _prune(
        rows: list[dict[str, Any]], *, retention_days: int, now: float
    ) -> list[dict[str, Any]]:
        days = max(0, min(int(retention_days), 30))
        if days == 0:
            return []
        cutoff = now - days * 86400
        return [row for row in rows if float(row.get("observed_at") or 0) >= cutoff][-500:]

    def _append_event(self, event: dict[str, Any], *, retention_days: int, now: float) -> None:
        with interprocess_file_lock(self.event_path.with_suffix(".lock")):
            if int(retention_days) <= 0:
                self._atomic_write([])
                return
            rows = self._prune(self._read_events(), retention_days=retention_days, now=now)
            rows.append(event)
            self._atomic_write(rows[-500:])

    def _read_events(self) -> list[dict[str, Any]]:
        rows, _ = self._load_events()
        return rows

    def _load_last_action_at(self) -> float:
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
            timestamp = (
                float(payload.get("last_action_at", 0.0)) if isinstance(payload, dict) else 0.0
            )
        except (
            FileNotFoundError,
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            TypeError,
            ValueError,
        ):
            return 0.0
        if not math.isfinite(timestamp) or timestamp < 0:
            return 0.0
        # A corrected system clock must not turn one greeting cooldown into an
        # effectively permanent block.
        return timestamp if timestamp <= time.time() + 300 else 0.0

    def _load_session(self) -> PresenceSession | None:
        try:
            if self.state_path.stat().st_size > 524_288:
                return None
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or payload.get("schema") != "iris.vision-runtime/v2":
                return None
            # V1 only contained cooldown; its migration starts with no session.
            session = PresenceSession.model_validate(payload.get("session"))
            age = time.time() - session.last_observed_at
            return session if 0 <= age < VisionSessionTracker.SESSION_BREAK else None
        except (OSError, UnicodeError, ValueError, TypeError, ValidationError):
            return None

    def _checkpoint_session(self, tick: float, *, force: bool = False) -> bool:
        if (
            not force
            and self._checkpoint_tick is not None
            and 0 <= tick - self._checkpoint_tick < 60
        ):
            return self._state_persisted
        self._checkpoint_tick = tick
        try:
            self._persist_last_action_at(self._last_action_at)
        except OSError:
            logger.warning("Could not checkpoint Vision session", exc_info=True)
            self._state_persisted = False
            return False
        self._state_persisted = True
        return True

    def _persist_last_action_at(self, timestamp: float) -> None:
        if self._notification_storage_error:
            raise OSError("El estado local no es verificable; se conserva sin sobrescribir.")
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        with interprocess_file_lock(self.state_path.with_suffix(".lock")):
            try:
                if self.state_path.exists():
                    try:
                        if self.state_path.stat().st_size > 524_288:
                            raise ValueError("State exceeds local bound")
                        current = json.loads(self.state_path.read_text(encoding="utf-8"))
                        _, current_inbox = self._notification_state(current)
                        revision = current_inbox.revision
                    except (ValueError, TypeError, AttributeError) as exc:
                        self._notification_storage_error = True
                        raise OSError("El estado cambió y ya no es verificable.") from exc
                    if revision != self._stored_inbox_revision:
                        raise OSError(
                            "Otra instancia cambió la bandeja; reinicia Core para sincronizar."
                        )
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    dir=self.state_path.parent,
                    prefix=f".{self.state_path.name}.",
                    suffix=".tmp",
                    delete=False,
                ) as handle:
                    temporary = Path(handle.name)
                    json.dump(
                        {
                            "schema": "iris.vision-runtime/v2",
                            "last_action_at": timestamp,
                            "inbox": self._inbox.model_dump(by_alias=True),
                            "policy": self._policy.checkpoint(
                                wall=self._policy_clock[0],
                                tick=self._policy_clock[1],
                            ),
                            "session": self._session.checkpoint()
                            if self._retention_days > 0
                            else None,
                        },
                        handle,
                        ensure_ascii=False,
                        indent=2,
                    )
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, self.state_path)
                self._stored_inbox_revision = self._inbox.revision
            finally:
                if temporary and temporary.exists():
                    temporary.unlink(missing_ok=True)

    def _load_events(self) -> tuple[list[dict[str, Any]], bool]:
        try:
            parsed = json.loads(self.event_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return [], False
        except OSError:
            return [], False
        except (UnicodeError, json.JSONDecodeError):
            return [], True
        if not isinstance(parsed, list):
            return [], True
        rows: list[dict[str, Any]] = []
        dirty = False
        for row in parsed:
            normalized = self._normalize_event_row(row)
            if normalized is None:
                dirty = True
                continue
            rows.append(normalized)
            if normalized != row:
                dirty = True
        return rows, dirty

    @staticmethod
    def _normalize_event_row(row: Any) -> dict[str, Any] | None:
        if not isinstance(row, dict) or row.get("type") not in EVENT_TYPES:
            return None
        try:
            observed_at = float(row.get("observed_at"))
        except (TypeError, ValueError):
            return None
        if not math.isfinite(observed_at) or observed_at < 0:
            return None
        try:
            confidence = float(row.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        if not math.isfinite(confidence):
            confidence = 0.0
        event_type = str(row["type"])
        default_label = {
            "present": "Presencia detectada",
            "absent": "Ausencia detectada",
            "returned": "Regreso detectado",
        }[event_type]
        motion = str(row.get("motion") or "still").strip().casefold()
        if motion not in MOTION_LEVELS:
            motion = "still"
        suppression_reason = str(row.get("suppression_reason") or "")
        if suppression_reason not in {"", "cooldown", "notification_policy"}:
            suppression_reason = ""
        return {
            "id": str(row.get("id") or "")[:64],
            "type": event_type,
            "label": str(row.get("label") or default_label)[:120],
            "surface_id": str(row.get("surface_id") or "surface")[:96],
            "confidence": round(max(0.0, min(confidence, 1.0)), 4),
            "motion": motion,
            "observed_at": observed_at,
            "actionable": bool(row.get("actionable", False)),
            "suppression_reason": suppression_reason,
            "contains_media": False,
        }

    def _atomic_write(self, rows: list[dict[str, Any]]) -> None:
        self.event_path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.event_path.parent,
                prefix=f".{self.event_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                json.dump(rows, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.event_path)
        finally:
            if temporary and temporary.exists():
                temporary.unlink(missing_ok=True)

    @staticmethod
    def _notification_state(payload: Any) -> tuple[PolicyCheckpoint | None, NotificationInbox]:
        if not isinstance(payload, dict) or payload.get("schema") not in {
            "iris.vision-runtime/v1",
            "iris.vision-runtime/v2",
        }:
            raise ValueError("Unknown state")
        policy = PolicyCheckpoint.model_validate(payload["policy"]) if "policy" in payload else None
        if "inbox" in payload:
            if policy is None:
                raise ValueError("Inbox requires its durable budget")
            return policy, NotificationInbox.model_validate(payload["inbox"])
        return policy, NotificationInbox()

    def _load_inbox(self) -> NotificationInbox:
        try:
            if not self.state_path.exists():
                return NotificationInbox()
            if self.state_path.stat().st_size > 524_288:
                raise ValueError("State exceeds local bound")
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
            policy, inbox = self._notification_state(payload)
            if policy is None and self._last_action_at > 0:
                # A recent V1 greeting also consumes the shared V2 budget.
                # The policy engine expires old timestamps using its guarded clock.
                policy = PolicyCheckpoint(
                    receipts=[
                        DeliveryReceipt(
                            candidate_id=uuid.uuid5(
                                uuid.NAMESPACE_URL, f"iris-vision-v1:{self._last_action_at}"
                            ).hex,
                            kind="returned",
                            delivered_at=self._last_action_at,
                        )
                    ]
                )
            # Receipt and note must come from the same atomic file version.
            self._policy = VisionPolicyEngine(policy)
            return inbox
        except (OSError, UnicodeError, ValueError, TypeError):
            # Preserve the original file. A damaged budget must not enable new delivery.
            self._notification_storage_error = True
            return NotificationInbox()

    def availability(self, *, wall: float, tick: float) -> dict[str, Any]:
        with self._lock:
            grant = self._availability_clock
            if grant:
                elapsed = tick - grant[1]
                if not 0 <= elapsed < 900 or abs(wall - grant[0] - elapsed) > 5:
                    self._availability_clock = None
                    self._policy.invalidate_offers()
                    grant = None
            return {
                "available": grant is not None,
                "expires_at": grant[0] + 900 if grant else 0.0,
                "remaining_seconds": max(0.0, 900 - (tick - grant[1])) if grant else 0.0,
                "source": "explicit_local_self_report",
            }

    def set_notification_availability(
        self,
        available: bool,
        *,
        wall: float | None = None,
        tick: float | None = None,
    ) -> dict[str, Any]:
        wall = time.time() if wall is None else wall
        tick = time.monotonic() if tick is None else tick
        VisionSessionTracker.validate_clock(wall, tick)
        with self._lock:
            self._availability_clock = (wall, tick) if available else None
            self._policy.invalidate_offers()
            return self.availability(wall=wall, tick=tick)

    def _save_inbox(self, inbox: NotificationInbox, *, wall: float, tick: float) -> None:
        previous = self._inbox
        self._inbox = inbox
        self._policy_clock = (wall, tick)
        try:
            self._persist_last_action_at(self._last_action_at)
        except OSError:
            self._inbox = previous
            self._state_persisted = False
            raise
        self._checkpoint_tick = tick
        self._state_persisted = True

    def notification_snapshot(
        self,
        *,
        retention_days: int,
        wall: float | None = None,
        tick: float | None = None,
    ) -> dict[str, Any]:
        wall = time.time() if wall is None else wall
        tick = time.monotonic() if tick is None else tick
        VisionSessionTracker.validate_clock(wall, tick)
        with self._lock:
            items = self._inbox.retained(days=retention_days, wall=wall)
            if items != self._inbox.items and not self._notification_storage_error:
                try:
                    self._save_inbox(self._inbox.changed(items), wall=wall, tick=tick)
                except OSError:
                    pass
            return {
                "schema": "iris.notification-center/v1",
                "revision": self._inbox.revision,
                "items": [
                    item.public(
                        wall=wall,
                        physical_suggestions=physical_copy(self._inbox.preferences.experience),
                    )
                    for item in reversed(items)
                ],
                "unread": sum(
                    item.read_at is None
                    and item.dismissed_at is None
                    and item.snoozed_until <= wall
                    for item in items
                ),
                "server_now": wall,
                "preferences": self._inbox.preferences.model_dump(),
                "diagnostics": self._diagnostics.snapshot(
                    wall=wall, tick=tick, retention_days=retention_days
                ),
                "next_change_at": min(
                    (
                        deadline
                        for deadline in (
                            self._inbox.preferences.muted_until,
                            *(item.snoozed_until for item in items if item.dismissed_at is None),
                        )
                        if deadline > wall
                    ),
                    default=0.0,
                ),
                "retention_days": retention_days,
                "storage_available": not self._notification_storage_error and self._state_persisted,
                "availability": self.availability(wall=wall, tick=tick),
                "delivery": "silent_inbox",
                "contains_media": False,
            }

    def update_notification(
        self,
        candidate_id: str,
        *,
        read: bool,
        wall: float | None = None,
        tick: float | None = None,
    ) -> None:
        wall = time.time() if wall is None else wall
        tick = time.monotonic() if tick is None else tick
        with self._lock:
            if not any(item.candidate.candidate_id == candidate_id for item in self._inbox.items):
                raise KeyError(candidate_id)
            VisionSessionTracker.validate_clock(wall, tick)
            items = [
                item.model_copy(
                    update={
                        "read_at": (item.read_at if item.read_at is not None else wall)
                        if read
                        else None,
                    }
                )
                if item.candidate.candidate_id == candidate_id
                else item
                for item in self._inbox.items
            ]
            if items != self._inbox.items:
                self._save_inbox(self._inbox.changed(items), wall=wall, tick=tick)

    def clear_notifications(self) -> None:
        with self._lock:
            self._save_inbox(self._inbox.changed([]), wall=time.time(), tick=time.monotonic())

    def clear_vision_diagnostics(self):
        with self._lock:
            self._diagnostics.clear()

    def report_vision_sensor(self, payload, *, surface_role, tick=None):
        with self._lock:
            self._diagnostics.sensor = {
                "tick": time.monotonic() if tick is None else tick,
                "data": {**payload.model_dump(), "surface": surface_role},
            }

    def _prepare_notification(
        self,
        report: dict,
        *,
        wall: float,
        tick: float,
        retention_days: int,
    ) -> bool:
        report["stage"] = "notifications"
        if report["decision"]["outcome"] != "eligible":
            return False
        reason = None
        if not self.availability(wall=wall, tick=tick)["available"]:
            reason = "availability_required"
        elif retention_days <= 0:
            reason = "history_disabled"
        elif self._notification_storage_error or tick < self._inbox_retry_tick:
            reason = "notification_storage"
        candidate_id = report["candidate"]["candidate_id"]
        if reason:
            report["decision"] = VisionDecision(
                outcome="suppressed",
                reason=reason,
                candidate_id=candidate_id,
            ).public()
            self._policy.invalidate_offers()
            return False
        return True

    def _deliver_notification(
        self,
        report: dict,
        *,
        wall: float,
        tick: float,
        retention_days: int,
    ) -> dict | None:
        if not self._prepare_notification(
            report, wall=wall, tick=tick, retention_days=retention_days
        ):
            return None
        candidate_id = report["candidate"]["candidate_id"]
        prior_policy = self._policy.checkpoint(wall=wall, tick=tick)
        row = next(
            row for row in report["evaluations"] if row["candidate"]["candidate_id"] == candidate_id
        )
        if not self._policy.confirm_delivery(candidate_id, wall=wall, tick=tick):
            return None
        notification = VisionNotification(
            candidate=row["candidate"],
            evidence=row["evidence"],
            delivered_at=wall,
        )
        items = self._inbox.retained(days=retention_days, wall=wall)
        # Defensive dedup also survives a malformed/expired policy migration.
        items = [item for item in items if item.candidate.candidate_id != candidate_id]
        try:
            self._save_inbox(self._inbox.changed([*items, notification]), wall=wall, tick=tick)
        except OSError:
            self._policy = VisionPolicyEngine(PolicyCheckpoint.model_validate(prior_policy))
            self._inbox_retry_tick = tick + 60
            report["decision"] = VisionDecision(
                outcome="suppressed",
                reason="notification_storage",
                candidate_id=candidate_id,
            ).public()
            return None
        report["decision"] = VisionDecision(
            outcome="delivered",
            reason="delivered",
            delivery="inbox",
            candidate_id=candidate_id,
        ).public()
        report["budget"]["used_last_hour"] += 1
        return notification.public()
