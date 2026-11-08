"""Deterministic, local Vision V2 policy. Evaluation never delivers an intervention."""

from __future__ import annotations

import time
import uuid
from collections import deque
from typing import Annotated, Literal

from pydantic import Field

from core.vision_session import (
    Identifier,
    PresenceSession,
    Reason,
    Timestamp,
    VisionCandidate,
    VisionContract,
    VisionDecision,
    VisionSessionTracker,
)

Kind = Literal["long_session", "low_head_motion", "returned"]
ACTIVITY_FLAGS = (
    "quiet",
    "focus",
    "typing",
    "speaking",
    "listening",
    "gaming",
    "presenting",
    "meeting",
)
PRIORITY = {"returned": 30, "low_head_motion": 20, "long_session": 10}


class VisionActivity(VisionContract):
    """Only boolean summaries from a local surface; no input text or app titles."""

    visible: bool | None = None
    typing: bool | None = None
    speaking: bool | None = None
    listening: bool | None = None
    focus: bool | None = None
    quiet: bool | None = None
    gaming: bool | None = None
    presenting: bool | None = None
    meeting: bool | None = None

    @classmethod
    def combine(cls, rows: list[VisionActivity]) -> VisionActivity:
        if not rows:
            return cls()
        flags = {}
        for flag in ACTIVITY_FLAGS:
            values = [getattr(row, flag) for row in rows]
            flags[flag] = (
                True if True in values else False if all(v is False for v in values) else None
            )
        visibility = [row.visible for row in rows]
        flags["visible"] = (
            True if True in visibility else False if all(v is False for v in visibility) else None
        )
        return cls(**flags)


class QuietHours(VisionContract):
    start_minute: Annotated[int, Field(ge=0, le=1439)]
    end_minute: Annotated[int, Field(ge=0, le=1439)]

    def contains(self, minute: int) -> bool:
        if self.start_minute == self.end_minute:
            return True
        if self.start_minute < self.end_minute:
            return self.start_minute <= minute < self.end_minute
        return minute >= self.start_minute or minute < self.end_minute


class KindCooldowns(VisionContract):
    returned: Annotated[int, Field(ge=1, le=180)] = 45
    long_session: Annotated[int, Field(ge=1, le=180)] = 45
    low_head_motion: Annotated[int, Field(ge=1, le=180)] = 45


class VisionPolicySettings(VisionContract):
    long_session_seconds: Annotated[int, Field(ge=60, le=43200)] = 2700
    low_head_session_seconds: Annotated[int, Field(ge=60, le=43200)] = 2400
    min_motion_window_seconds: Annotated[int, Field(ge=30, le=600)] = 540
    low_motion_ratio: Annotated[float, Field(ge=0.5, le=1, allow_inf_nan=False)] = 0.9
    min_confidence: Annotated[float, Field(ge=0.55, le=1, allow_inf_nan=False)] = 0.7
    max_per_hour: Annotated[int, Field(ge=0, le=6)] = 2
    kind_cooldown_minutes: KindCooldowns = Field(default_factory=KindCooldowns)
    quiet_hours: Annotated[list[QuietHours], Field(max_length=4)] = Field(default_factory=list)


class DeliveryReceipt(VisionContract):
    candidate_id: Identifier
    kind: Kind
    delivered_at: Timestamp


class PolicyCheckpoint(VisionContract):
    schema_version: Literal["iris.vision-policy-state/v1"] = Field(
        default="iris.vision-policy-state/v1",
        alias="schema",
    )
    receipts: Annotated[list[DeliveryReceipt], Field(max_length=128)] = Field(default_factory=list)


class VisionPolicyEngine:
    """Preview is side-effect free with respect to budgets.

    Only internal confirm_delivery() consumes a budget, atomically with inbox
    persistence. A best-effort avatar gesture never counts as proof of reading.
    """

    MAX_RECEIPT_AGE = 86400.0  # bounded dedup, longer than the 12-hour threshold
    CLOCK_GUARD_SECONDS = 60.0

    def __init__(self, restored: PolicyCheckpoint | None = None) -> None:
        self._restored = restored
        self._receipts: deque[tuple[DeliveryReceipt, float]] = deque(maxlen=128)
        self._last_clock: tuple[float, float] | None = None
        self._clock_guard_until = 0.0
        self._offers: dict[str, tuple[VisionCandidate, float]] = {}

    def _advance(self, wall: float, tick: float) -> None:
        VisionSessionTracker.validate_clock(wall, tick)
        if self._restored is not None:
            for receipt in self._restored.receipts:
                age = wall - receipt.delivered_at
                if age < 0:
                    # Future checkpoints do not erase the budget after a clock correction.
                    age = 0.0
                    self._clock_guard_until = tick + self.CLOCK_GUARD_SECONDS
                if age < self.MAX_RECEIPT_AGE:
                    self._receipts.append((receipt, tick - age))
            self._restored = None
        if self._last_clock is not None:
            old_wall, old_tick = self._last_clock
            delta = tick - old_tick
            if delta < 0 or abs(wall - old_wall - delta) > 5:
                self._clock_guard_until = tick + self.CLOCK_GUARD_SECONDS
                self._offers.clear()
                if delta < 0:
                    # Reset monotonic origin conservatively without dropping receipts.
                    self._receipts = deque(
                        ((r, tick - max(0.0, old_tick - at)) for r, at in self._receipts),
                        maxlen=128,
                    )
        self._last_clock = (wall, tick)
        self._receipts = deque(
            ((r, at) for r, at in self._receipts if tick - at < self.MAX_RECEIPT_AGE),
            maxlen=128,
        )

    def candidates(
        self,
        session: PresenceSession,
        settings: VisionPolicySettings,
        *,
        wall: float,
        confidence: float,
    ) -> list[VisionCandidate]:
        window = session.motion
        low_seconds = window.still_seconds + window.low_seconds
        coverage = window.observed_seconds
        specs: list[tuple[Kind, int, float]] = []
        if (
            session.observed_presence_seconds >= settings.low_head_session_seconds
            and coverage >= settings.min_motion_window_seconds
            and coverage > 0
            and low_seconds / coverage >= settings.low_motion_ratio
        ):
            specs.append(
                (
                    "low_head_motion",
                    settings.low_head_session_seconds,
                    min(confidence, window.confidence),
                )
            )
        if session.observed_presence_seconds >= settings.long_session_seconds:
            specs.append(("long_session", settings.long_session_seconds, confidence))
        result = []
        for kind, threshold, measured_confidence in specs:
            bucket = int(session.observed_presence_seconds // threshold)
            key = f"iris-vision:{session.session_id}:{kind}:{bucket}"
            result.append(
                VisionCandidate(
                    candidate_id=uuid.uuid5(uuid.NAMESPACE_URL, key).hex,
                    session_id=session.session_id,
                    kind=kind,
                    observed_at=wall,
                    observed_seconds=session.observed_presence_seconds,
                    confidence=measured_confidence,
                )
            )
        return result

    def evaluate(
        self,
        session: PresenceSession | None,
        *,
        observation_reason: Reason,
        activity: VisionActivity,
        settings: VisionPolicySettings,
        wall: float,
        tick: float,
        confidence: float,
        cooldown_minutes: int,
        backend_busy: bool = False,
        blocked_kinds: dict[str, Reason] | None = None,
        return_candidate: VisionCandidate | None = None,
        return_evidence: dict | None = None,
    ) -> dict:
        self._advance(wall, tick)
        self._offers.clear()
        rows = []
        candidates = (
            self.candidates(session, settings, wall=wall, confidence=confidence)
            if (session is not None and observation_reason == "collecting")
            else []
        )
        if return_candidate is not None and observation_reason == "collecting":
            candidates.insert(0, return_candidate)
        for candidate in candidates:
            reason, retry = self._gate(
                candidate,
                activity=activity,
                settings=settings,
                tick=tick,
                wall=wall,
                cooldown_minutes=cooldown_minutes,
                backend_busy=backend_busy,
            )
            if candidate.kind in (blocked_kinds or {}):
                reason, retry = blocked_kinds[candidate.kind], 0.0
            if reason == "eligible" and self._offers:
                reason = "lower_priority"
            if reason == "eligible":
                self._offers[candidate.candidate_id] = (candidate, tick)
            threshold = (
                settings.low_head_session_seconds
                if candidate.kind == "low_head_motion"
                else settings.long_session_seconds
            )
            rows.append(
                {
                    "candidate": candidate.model_dump(by_alias=True),
                    "decision": VisionDecision(
                        outcome="eligible" if reason == "eligible" else "suppressed",
                        reason=reason,
                        candidate_id=candidate.candidate_id,
                        retry_after_seconds=retry,
                    ).public(),
                    "evidence": {
                        "rule": candidate.kind,
                        "priority": PRIORITY[candidate.kind],
                        "observed_seconds": candidate.observed_seconds,
                        "threshold_seconds": threshold,
                        "confidence": candidate.confidence,
                        "motion_window_seconds": session.motion.observed_seconds,
                        "low_head_motion_ratio": (
                            (session.motion.still_seconds + session.motion.low_seconds)
                            / session.motion.observed_seconds
                            if session.motion.observed_seconds
                            else 0.0
                        ),
                        "basis": "head_pose",
                    },
                }
            )
            if candidate.kind == "returned":
                rows[-1]["evidence"] = return_evidence
        selected = next((r for r in rows if r["decision"]["outcome"] == "eligible"), None)
        selected = selected or (rows[0] if rows else None)
        reason = observation_reason if observation_reason != "collecting" else "below_threshold"
        return {
            "stage": "policy_preview",
            "candidate": selected["candidate"] if selected else None,
            "decision": selected["decision"]
            if selected
            else VisionDecision(reason=reason).public(),
            "evaluations": rows,
            "budget": {
                "used_last_hour": sum(0 <= tick - at < 3600 for _, at in self._receipts),
                "max_per_hour": settings.max_per_hour,
                "consumed_by": "confirmed_delivery",
            },
        }

    def _gate(
        self,
        candidate: VisionCandidate,
        *,
        activity: VisionActivity,
        settings: VisionPolicySettings,
        tick: float,
        wall: float,
        cooldown_minutes: int,
        backend_busy: bool,
    ) -> tuple[Reason, float]:
        if tick < self._clock_guard_until:
            return "clock_discontinuity", self._clock_guard_until - tick
        if candidate.confidence < settings.min_confidence:
            return "low_confidence", 0.0
        if backend_busy:
            return "agent_busy", 0.0
        for flag in ACTIVITY_FLAGS:
            if getattr(activity, flag) is True:
                return f"activity_{flag}", 0.0
        if activity.visible is False:
            return "surface_hidden", 0.0
        if activity.visible is None or any(
            getattr(activity, flag) is None for flag in ACTIVITY_FLAGS
        ):
            return "activity_unknown", 0.0
        local = time.localtime(wall)
        if any(
            period.contains(local.tm_hour * 60 + local.tm_min) for period in settings.quiet_hours
        ):
            return "quiet_hours", 0.0
        if any(r.candidate_id == candidate.candidate_id for r, _ in self._receipts):
            return "duplicate", 0.0
        if settings.max_per_hour == 0:
            return "hourly_budget", 3600.0
        hour = sorted(at for _, at in self._receipts if 0 <= tick - at < 3600)
        if len(hour) >= settings.max_per_hour:
            # If settings were reduced, enough receipts must expire, not just the oldest.
            return "hourly_budget", max(0.0, hour[-settings.max_per_hour] + 3600 - tick)
        if self._receipts:
            wait = max(at for _, at in self._receipts) + cooldown_minutes * 60 - tick
            if wait > 0:
                return "global_cooldown", wait
        same = [at for r, at in self._receipts if r.kind == candidate.kind]
        if same:
            wait = max(same) + getattr(settings.kind_cooldown_minutes, candidate.kind) * 60 - tick
            if wait > 0:
                return "kind_cooldown", wait
        return "eligible", 0.0

    def invalidate_offers(self) -> None:
        self._offers.clear()

    def confirm_delivery(self, candidate_id: str, *, wall: float, tick: float) -> bool:
        """Consume one current eligible offer, once, after an internal delivery ACK."""
        self._advance(wall, tick)
        offer = self._offers.pop(candidate_id, None)
        if offer is None or not 0 <= tick - offer[1] <= 8 or tick < self._clock_guard_until:
            return False
        candidate = offer[0]
        if any(receipt.candidate_id == candidate_id for receipt, _ in self._receipts):
            return False
        self._receipts.append(
            (
                DeliveryReceipt(
                    candidate_id=candidate_id,
                    kind=candidate.kind,
                    delivered_at=wall,
                ),
                tick,
            )
        )
        self._offers.clear()
        return True

    def checkpoint(self, *, wall: float, tick: float) -> dict:
        self._advance(wall, tick)
        # Normalize clock reference while preserving elapsed monotonic cooldown.
        state = PolicyCheckpoint(
            receipts=[
                receipt.model_copy(update={"delivered_at": max(0.0, wall - max(0.0, tick - at))})
                for receipt, at in self._receipts
            ]
        )
        return state.model_dump(by_alias=True)
