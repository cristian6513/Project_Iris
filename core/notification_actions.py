"""Reversible local inbox actions; no chat dispatch, tools, voice or scheduler."""

from __future__ import annotations

import time

from core.vision_experience import experience_blocks, physical_copy
from core.vision_notifications import (
    CONVERSATION_COPY,
    NotificationActionRequest,
    NotificationPreferenceRequest,
    NotificationPreferences,
)
from core.vision_session import VisionSessionTracker


class NotificationConflictError(ValueError):
    pass


class NotificationActionsMixin:
    def _notification_blocks(self, wall: float) -> dict:
        prefs = self._inbox.preferences
        if prefs.muted_until > wall:
            return dict.fromkeys(CONVERSATION_COPY, "notifications_muted")
        blocks = dict.fromkeys(prefs.disabled_kinds, "notification_kind_disabled")
        blocks.update(
            dict.fromkeys(experience_blocks(prefs.experience), "notification_kind_disabled")
        )
        if not prefs.return_summary:
            blocks["returned"] = "notification_kind_disabled"
        for note in self._inbox.retained(days=self._retention_days, wall=wall):
            if note.dismissed_at is None and note.snoozed_until > wall:
                blocks.setdefault(note.candidate.kind, "notification_snoozed")
        return blocks

    def _notification_for_action(self, notification_id, retention_days, wall):
        return next(
            (
                note
                for note in self._inbox.retained(days=retention_days, wall=wall)
                if note.candidate.candidate_id == notification_id
            ),
            None,
        )

    def _check_notification_revision(self, expected):
        if expected != self._inbox.revision:
            raise NotificationConflictError(
                "La bandeja cambió en otra ventana. Actualiza antes de repetir."
            )

    def act_on_notification(
        self, request: NotificationActionRequest, *, retention_days, wall=None, tick=None
    ):
        wall = time.time() if wall is None else wall
        tick = time.monotonic() if tick is None else tick
        VisionSessionTracker.validate_clock(wall, tick)
        with self._lock:
            self._check_notification_revision(request.expected_revision)
            note = self._notification_for_action(request.id, retention_days, wall)
            if note is None:
                raise KeyError(request.id)
            if request.action == "snooze" and note.dismissed_at is not None:
                raise NotificationConflictError("Restaura el aviso antes de posponerlo.")
            changes = {
                "dismiss": {"dismissed_at": wall, "snoozed_until": 0.0},
                "restore": {"dismissed_at": None, "snoozed_until": 0.0, "read_at": None},
                "snooze": {
                    "dismissed_at": None,
                    "snoozed_until": wall + (request.minutes or 0) * 60,
                    "read_at": None,
                },
            }[request.action]
            replacement = note.model_copy(update=changes)
            if replacement == note:
                return
            items = [
                replacement if item.candidate.candidate_id == request.id else item
                for item in self._inbox.retained(days=retention_days, wall=wall)
            ]
            self._save_inbox(self._inbox.changed(items), wall=wall, tick=tick)
            self._policy.invalidate_offers()

    def set_notification_preferences(
        self, request: NotificationPreferenceRequest, *, wall=None, tick=None
    ):
        wall = time.time() if wall is None else wall
        tick = time.monotonic() if tick is None else tick
        VisionSessionTracker.validate_clock(wall, tick)
        with self._lock:
            self._check_notification_revision(request.expected_revision)
            prefs = self._inbox.preferences
            disabled = set(prefs.disabled_kinds)
            muted_until = prefs.muted_until
            return_summary = prefs.return_summary
            if request.action == "mute":
                muted_until = wall + request.minutes * 60
            elif request.action == "unmute":
                muted_until = 0.0
            elif request.action == "disable_kind":
                disabled.add(request.kind)
            elif request.action == "enable_kind":
                disabled.discard(request.kind)
            else:
                return_summary = request.action == "enable_return"
            replacement = NotificationPreferences(
                muted_until=muted_until,
                disabled_kinds=sorted(disabled),
                return_summary=return_summary,
                experience=prefs.experience,
            )
            if replacement == prefs:
                return
            updated = self._inbox.changed(self._inbox.items).model_copy(
                update={"preferences": replacement}
            )
            self._save_inbox(updated, wall=wall, tick=tick)
            self._policy.invalidate_offers()

    def notification_draft(self, notification_id, *, retention_days, wall=None):
        wall = time.time() if wall is None else wall
        with self._lock:
            note = self._notification_for_action(notification_id, retention_days, wall)
            if note is None:
                raise KeyError(notification_id)
            if note.dismissed_at is not None or note.snoozed_until > wall:
                raise NotificationConflictError(
                    "Restaura el aviso antes de preparar la conversación."
                )
            return {
                "notification_id": notification_id,
                "text": (
                    "¿Me ayudas a revisar mis prioridades y a elegir lo que sigue a mi ritmo?"
                    if not physical_copy(self._inbox.preferences.experience)
                    and note.candidate.kind == "long_session"
                    else CONVERSATION_COPY[note.candidate.kind]
                ),
                "sent": False,
                "contains_observation_data": False,
            }
