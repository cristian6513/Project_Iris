"""Conservative return detection from uninterrupted, local presence summaries."""

from __future__ import annotations

from core.vision_session import VisionSessionTracker


class VisionReturnTracker:
    MIN_ABSENCE_SECONDS = 30

    def __init__(self):
        self.reset()

    def reset(self):
        self._previous = None
        self._absent_seconds = 0.0
        self._seen_presence = False

    def observe(self, *, signal, stable_presence, event, surface, wall, tick, enabled):
        previous = self._previous
        continuous = False
        if previous is not None:
            old_wall, old_tick, old_surface, old_absent = previous
            delta = tick - old_tick
            continuous = (
                old_surface == surface
                and 0 <= delta <= VisionSessionTracker.MAX_SIGNAL_GAP
                and abs(wall - old_wall - delta) <= 5
            )
        reliable = enabled and signal["confidence"] >= 0.7
        if not continuous or not reliable:
            self._absent_seconds = 0.0
        if not reliable:
            self._previous = None
            return 0.0
        absent = signal["present"] is False and stable_presence is False
        if continuous and absent and old_absent and self._seen_presence:
            self._absent_seconds += delta
        seconds = 0.0
        if event and event["type"] == "returned":
            seconds = self._absent_seconds
            self._absent_seconds = 0.0
        elif stable_presence is True:
            self._absent_seconds = 0.0
        if stable_presence is True and signal["present"]:
            self._seen_presence = True
        self._previous = (wall, tick, surface, absent)
        return seconds if seconds >= self.MIN_ABSENCE_SECONDS else 0.0
