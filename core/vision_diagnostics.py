"""Bounded RAM-only diagnostics: scalar counters, never frames or facial geometry."""

from collections import deque
from typing import Annotated, Literal

from pydantic import Field

from core.vision_session import VisionContract

Count = Annotated[int, Field(ge=0, le=1_000_000_000)]


class VisionSensorDiagnostics(VisionContract):
    frames: Count
    misses: Count
    latency_ms: Annotated[float, Field(ge=0, le=60000, allow_inf_nan=False)]
    interval_ms: Annotated[int, Field(ge=0, le=1000)]
    state: Literal["observing", "hidden", "overloaded", "calibrating", "stopped", "unavailable"]
    calibration: Literal["idle", "collecting", "ready", "insufficient", "noisy"]
    progress: Annotated[int, Field(ge=0, le=100)]
    still_threshold: Annotated[float, Field(ge=0.3, le=0.6, allow_inf_nan=False)]
    active_threshold: Annotated[float, Field(ge=1.2, le=2.4, allow_inf_nan=False)]


class VisionDiagnostics:
    def __init__(self):
        self.rows = deque(maxlen=100)
        self.sensor = None
        self.last_key = None

    def clear(self):
        self.rows.clear()
        self.sensor = None
        self.last_key = None

    def record(self, report, *, wall, role, retention_days):
        if retention_days <= 0:
            self.clear()
            return
        decision = report.get("decision", {})
        candidate = report.get("candidate") or {}
        key = (candidate.get("kind", "none"), decision.get("outcome"), decision.get("reason"), role)
        if key == self.last_key:
            return
        self.last_key = key
        self.rows.append(
            {
                "at": wall,
                "kind": key[0],
                "outcome": key[1],
                "reason": key[2],
                "explanation": decision.get("explanation", ""),
                "surface": role,
            }
        )

    def snapshot(self, *, wall, tick, retention_days):
        if retention_days <= 0:
            self.clear()
        cutoff = wall - min(30, max(0, retention_days)) * 86400
        self.rows = deque((r for r in self.rows if r["at"] >= cutoff), maxlen=100)
        sensor = self.sensor
        fresh = sensor is not None and 0 <= tick - sensor["tick"] <= 8
        return {
            "scope": "current_core_session",
            "limit": 100,
            "contains_media": False,
            "rows": list(reversed(self.rows)),
            "sensor": sensor["data"] if fresh else None,
            "sensor_status": "fresh" if fresh else "unavailable",
            "suppressed_transitions": sum(r["outcome"] == "suppressed" for r in self.rows),
        }
