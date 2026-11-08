"""F04: what Iris knows about "the user is not here", per source, never mixed.

Three sources, each with its own name and semantics
(``docs/iris_automations_google_plan.md``, "Qué significa no estoy y regresé"):

* ``iris_idle``: time without interaction inside Iris, observed while Core
  runs and a local Iris surface is open. Not typing can mean reading: it is
  inactivity of Iris, not absence.
* ``desktop_idle``: time without system keyboard/mouse input. Its native
  adapter is future work (F08a): until it reports, this source is unknown.
* ``observed_absence``: a person seen present before, then absent before the
  camera with enough confidence, sample after sample (Vision V2 signals).

Only observed time counts. A gap between observations (suspend, restart, a
hidden or closed Iris, a stale or missing signal), a clock that jumps or goes
back, a surface change or a doubtful sample is never added: the count stops,
and for the camera the episode ends. Camera off, permission missing, hidden,
disconnected, stale or low confidence is **unknown**, never absence.

Each idle/absence episode gets a key (``<source>:<run>:<n>``) that F05 uses as
the occurrence key, so one episode yields at most one occurrence. A restart
is a new run: an episode is never resumed with time nobody observed.
Clocks are injectable; nothing here opens a sensor or starts a timer.
"""

from __future__ import annotations

import functools
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Callable, Literal

Source = Literal["iris_idle", "desktop_idle", "observed_absence"]
SOURCES: tuple[str, ...] = ("iris_idle", "desktop_idle", "observed_absence")

MAX_IRIS_GAP = 15.0  # Core's heartbeat runs every few seconds while it observes
MAX_DESKTOP_GAP = 15.0
MAX_CAMERA_GAP = 8.0  # Vision V2: a longer interval between signals is not counted
MAX_CLOCK_SKEW = 5.0  # wall and monotonic clocks disagreeing more than this: discontinuity
DEFAULT_MIN_CONFIDENCE = 0.7


@dataclass(frozen=True)
class SourceState:
    source: str
    status: Literal["known", "unknown"]
    condition: Literal["", "active", "idle", "present", "absent"]
    confirmed_s: float  # observed seconds of the current idle/absence episode
    episode: str  # occurrence key of the current idle/absence episode ("" otherwise)
    reason: str  # why it is unknown, or what last stopped the count
    confidence: float = 1.0

    @property
    def inactive(self) -> bool:
        return self.status == "known" and self.condition in {"idle", "absent"}


class _Lane:
    """One source's continuity: last observation, current episode, confirmed time."""

    def __init__(self, source: str, gap: float) -> None:
        self.source, self.gap = source, gap
        self.last: tuple[float, float] | None = None  # (monotonic, wall)
        self.condition = ""
        self.episode = ""
        self.confirmed = 0.0
        self.reason = "not_observed"
        self.unknown = True
        self.confidence = 1.0
        self.extra: dict = {}

    def interval(self, mono: float, wall: float) -> float | None:
        """Seconds since the last observation, or None when that interval cannot be trusted."""
        if self.last is None:
            return None
        delta, wall_delta = mono - self.last[0], wall - self.last[1]
        if delta < 0 or wall_delta < 0 or abs(wall_delta - delta) > MAX_CLOCK_SKEW:
            self.reason = "clock_discontinuity"
            return None
        if delta > self.gap:
            self.reason = "observation_gap"
            return None
        return delta


def _locked(method):
    """Core's loop writes signals while F05 reads them from a worker thread: one at a time."""

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapper


class PresenceTracker:
    def __init__(
        self,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
        run_id: str | None = None,
    ) -> None:
        self._monotonic, self._wall = monotonic, wall
        self.run_id = run_id or secrets.token_hex(4)
        self._episodes = 0
        self._lock = threading.RLock()
        self._lanes = {
            "iris_idle": _Lane("iris_idle", MAX_IRIS_GAP),
            "desktop_idle": _Lane("desktop_idle", MAX_DESKTOP_GAP),
            "observed_absence": _Lane("observed_absence", MAX_CAMERA_GAP),
        }
        self._lanes["desktop_idle"].reason = "no_adapter"

    def _now(self) -> tuple[float, float]:
        return float(self._monotonic()), float(self._wall())

    def _new_episode(self, lane: _Lane) -> None:
        self._episodes += 1
        lane.episode = f"{lane.source}:{self.run_id}:{self._episodes}"
        lane.confirmed = 0.0

    @staticmethod
    def _end_episode(lane: _Lane) -> None:
        lane.episode, lane.confirmed = "", 0.0

    # ── Iris inactivity ─────────────────────────────────────────────────

    @_locked
    def iris_interaction(self) -> None:
        """A user interaction on a local Iris surface: Iris is in use, the idle episode ends."""
        lane = self._lanes["iris_idle"]
        lane.last, lane.condition, lane.unknown, lane.reason = self._now(), "active", False, "interaction"
        self._end_episode(lane)

    @_locked
    def iris_tick(self, *, local_surface_open: bool) -> None:
        """Core's heartbeat. Without a local Iris surface open, Iris cannot observe: unknown."""
        lane = self._lanes["iris_idle"]
        mono, wall = self._now()
        if not local_surface_open:
            lane.last, lane.unknown, lane.reason, lane.condition = None, True, "iris_closed", ""
            self._end_episode(lane)
            return
        delta = lane.interval(mono, wall)
        if lane.unknown or lane.condition != "idle":
            # Observation (re)starts: an idle episode begins now, from zero.
            lane.unknown, lane.condition = False, "idle"
            self._new_episode(lane)
        elif delta is not None:
            lane.confirmed += delta
        lane.reason = "observed"
        lane.last = (mono, wall)

    # ── Desktop inactivity (adapter: F08a) ──────────────────────────────

    @_locked
    def desktop_sample(self, *, idle: bool) -> None:
        """The native adapter's report: input seen (``idle=False``) or none since its last report."""
        lane = self._lanes["desktop_idle"]
        mono, wall = self._now()
        delta = lane.interval(mono, wall)
        if not idle:
            lane.condition, lane.unknown, lane.reason = "active", False, "input"
            self._end_episode(lane)
        elif lane.unknown or lane.condition != "idle" or delta is None:
            lane.unknown, lane.condition, lane.reason = False, "idle", "observed"
            if delta is None and lane.episode:
                pass  # a gap: the episode goes on, the gap is not counted
            else:
                self._new_episode(lane)
        else:
            lane.confirmed += delta
        lane.last = (mono, wall)

    @_locked
    def desktop_unavailable(self, reason: str = "no_adapter") -> None:
        lane = self._lanes["desktop_idle"]
        lane.last, lane.unknown, lane.reason, lane.condition = None, True, reason, ""
        self._end_episode(lane)

    # ── Observed absence (Vision V2 signals) ────────────────────────────

    @_locked
    def camera_sample(self, *, present: bool, confidence: float, surface: str,
                      min_confidence: float = DEFAULT_MIN_CONFIDENCE) -> None:
        """One local presence signal. Absence counts only after presence was seen, sample by sample."""
        lane = self._lanes["observed_absence"]
        mono, wall = self._now()
        confidence = float(confidence) if confidence == confidence else 0.0  # NaN: no confidence
        same_surface = lane.extra.get("surface") == surface
        delta = lane.interval(mono, wall) if same_surface else None
        if confidence < min_confidence:
            self.camera_unavailable("low_confidence")
            return
        if delta is None and lane.last is not None and not same_surface:
            lane.reason = "surface_changed"
        if delta is None:
            # A new or broken observation: absence needs presence seen again first.
            lane.extra["seen_presence"] = False
            self._end_episode(lane)
        lane.last, lane.unknown, lane.confidence = (mono, wall), False, confidence
        lane.extra["surface"] = surface
        if present:
            lane.condition, lane.reason = "present", "present"
            lane.extra["seen_presence"] = True
            self._end_episode(lane)
            return
        lane.condition = "absent"
        if not lane.extra.get("seen_presence"):
            lane.unknown, lane.reason = True, "no_prior_presence"
            return
        lane.reason = "observed"
        if not lane.episode:
            self._new_episode(lane)
        elif delta is not None:
            lane.confirmed += delta

    @_locked
    def camera_unavailable(self, reason: str) -> None:
        """Camera off, hidden, disconnected, paused, without permission or doubtful: unknown."""
        lane = self._lanes["observed_absence"]
        lane.last, lane.unknown, lane.reason, lane.condition = None, True, reason, ""
        lane.extra = {}
        self._end_episode(lane)

    # ── Reading ─────────────────────────────────────────────────────────

    @_locked
    def state(self, source: str) -> SourceState:
        lane = self._lanes[source]
        if lane.unknown or lane.last is None:
            return SourceState(source, "unknown", "", 0.0, "", lane.reason)
        mono, wall = self._now()
        if lane.interval(mono, wall) is None:  # stale: nothing observed recently, or the clock jumped
            return SourceState(source, "unknown", "", 0.0, "", lane.reason)
        return SourceState(source, "known", lane.condition, round(lane.confirmed, 3), lane.episode, lane.reason,
                           lane.confidence)
