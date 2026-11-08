"""Atomic preference/feedback mutations; never dispatches sensor or agent commands."""

import time

from core.notification_actions import NotificationConflictError
from core.vision_experience import FeedbackDelays
from core.vision_session import VisionSessionTracker


class VisionExperienceActionsMixin:
    def set_vision_experience(self, request, *, wall=None, tick=None):
        wall = time.time() if wall is None else wall
        tick = time.monotonic() if tick is None else tick
        VisionSessionTracker.validate_clock(wall, tick)
        with self._lock:
            self._check_notification_revision(request.expected_revision)
            prefs = self._inbox.preferences
            if request.action == "reset_feedback":
                changes = {"feedback_delays": FeedbackDelays()}
            elif request.action in {"physical_suggestions", "greetings"}:
                changes = {request.action: request.enabled}
            else:
                changes = {request.action: getattr(request, request.action)}
            experience = prefs.experience.model_copy(update=changes)
            if experience == prefs.experience:
                return
            updated = self._inbox.changed(self._inbox.items).model_copy(
                update={"preferences": prefs.model_copy(update={"experience": experience})}
            )
            self._save_inbox(updated, wall=wall, tick=tick)
            self._policy.invalidate_offers()

    def record_vision_feedback(self, request, *, retention_days, wall=None, tick=None):
        wall = time.time() if wall is None else wall
        tick = time.monotonic() if tick is None else tick
        VisionSessionTracker.validate_clock(wall, tick)
        with self._lock:
            self._check_notification_revision(request.expected_revision)
            note = self._notification_for_action(request.id, retention_days, wall)
            if note is None:
                raise KeyError(request.id)
            if note.feedback == request.feedback:
                return
            if note.feedback is not None:
                raise NotificationConflictError(
                    "Este aviso ya tiene feedback. No se aplicó otro ajuste."
                )
            prefs = self._inbox.preferences
            kind = note.candidate.kind
            if request.feedback == "too_soon":
                delay = prefs.experience.feedback_delays
                delay = delay.model_copy(update={kind: min(180, getattr(delay, kind) + 15)})
                prefs = prefs.model_copy(
                    update={
                        "experience": prefs.experience.model_copy(update={"feedback_delays": delay})
                    }
                )
            elif request.feedback == "not_applicable":
                prefs = prefs.model_copy(
                    update={"return_summary": False}
                    if kind == "returned"
                    else {"disabled_kinds": sorted(set(prefs.disabled_kinds) | {kind})}
                )
            replacement = note.model_copy(update={"feedback": request.feedback})
            items = [
                replacement if row.candidate.candidate_id == request.id else row
                for row in self._inbox.retained(days=retention_days, wall=wall)
            ]
            updated = self._inbox.changed(items).model_copy(update={"preferences": prefs})
            self._save_inbox(updated, wall=wall, tick=tick)
            self._policy.invalidate_offers()
