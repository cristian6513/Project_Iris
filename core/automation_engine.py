"""F05: when an enabled rule's condition holds, one claimed occurrence per episode.

Each pass looks at every enabled rule's own source in ``PresenceTracker``
(F04). A rule fires when its source is known, inactive (idle or absent)
and has *observed* at least ``threshold_s`` in the current episode: 299 s is
not 300 s, unknown is not absence, and nobody waits for the user to come
back to decide that the moment passed. There is no cron pretending to be an
absence check.

The episode key is the occurrence key, so an episode yields one occurrence,
across passes, engines (Face/Companion, two Core handles) and edits of the
rule. A claim is a lease; right after taking it the signal is checked again,
and an occurrence whose user came back before the claim is cancelled.

An occurrence of the current episode that was never sent is taken up again
(Q03): a recorded candidate, one given back, or one whose claim expired is
claimed by the next pass (this engine or another), keeping its id and
correlation. Sent, finished or cancelled ones are never repeated. Unsent
occurrences of an episode that is over are cancelled, so a lost claim never
keeps the rule busy. Handing a ready occurrence to Hermes is F06
(``core.automation_executor``).
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

from core.automation_contracts import AutomationRule
from core.automation_store import AutomationStore, AutomationStoreError, StaleRevision
from core.presence_state import PresenceTracker

logger = logging.getLogger(__name__)

BUSY_STATES = frozenset({"claimed", "submitted", "running", "ambiguous", "stopping"})
SENT_STATES = frozenset({"submitted", "running", "ambiguous", "stopping", "completed", "failed"})
UNSENT_STATES = frozenset({"candidate", "claimed"})


@dataclass(frozen=True)
class Decision:
    rule_id: str
    revision: int
    outcome: str  # unknown | active | waiting | cooldown | busy | already | stale | claimed_elsewhere | returned | ready
    reason: str = ""
    occurrence_id: str = ""
    claim_token: str = ""


class AutomationEngine:
    def __init__(self, store: AutomationStore, tracker: PresenceTracker, *, owner: str,
                 lease_s: float = 60.0, clock: Callable[[], float] = time.time) -> None:
        self.store, self.tracker, self.owner = store, tracker, owner
        self.lease_s, self._clock = lease_s, clock

    def recover(self) -> int:
        """At start: cancel unsent occurrences of episodes from an earlier run.

        Their signal can no longer be checked again; a restart never resumes an
        episode. Sent work (submitted, running, ambiguous) is F06's to reconcile.
        """
        cancelled = 0
        current = f":{self.tracker.run_id}:"
        for occurrence in self.store.occurrences(active_only=True):
            if occurrence.state in {"candidate", "claimed"} and current not in occurrence.occurrence_key:
                try:
                    self.store.advance(occurrence.id, "cancelled", error="reinicio: el episodio ya no se puede comprobar")
                    cancelled += 1
                except AutomationStoreError:
                    pass
        return cancelled

    def holds(self, rule: AutomationRule, episode: str) -> bool:
        """The rule's condition, for that very episode, right now."""
        state = self.tracker.state(rule.trigger.source)
        return bool(
            state.inactive and state.episode == episode
            and state.confirmed_s >= rule.trigger.threshold_s
            and state.confidence >= rule.trigger.min_confidence
        )

    def evaluate(self) -> list[Decision]:
        decisions = []
        for rule in self.store.list_rules():
            if rule.enabled:
                decisions.append(self._evaluate(rule))
        return decisions

    def _abandoned(self, occurrence, now: float) -> bool:
        """Unsent, of an episode that is over, and nobody holds a live claim on it."""
        if occurrence.state == "candidate":
            return True
        return occurrence.state == "claimed" and occurrence.claim.lease_expires_at <= now

    def _recent(self, rule: AutomationRule, episode: str):
        """(busy, in cooldown, this episode's unsent occurrence, this episode already handled)."""
        busy = cooling = handled = False
        pending = None
        now = self._clock()
        for occurrence in self.store.occurrences(rule.id):
            if occurrence.occurrence_key == episode:
                if occurrence.rule_revision == rule.revision and occurrence.state in UNSENT_STATES:
                    pending = occurrence  # recoverable: the same occurrence is taken up again
                else:
                    handled = True  # sent, finished, cancelled, or seen by an older revision
                continue
            if occurrence.state in UNSENT_STATES and self._abandoned(occurrence, now):
                try:  # its episode is over: it can no longer be checked, nor keep the rule busy
                    self.store.advance(occurrence.id, "cancelled", error="El episodio terminó antes de enviarla.")
                except AutomationStoreError:
                    pass
                continue
            if occurrence.state in BUSY_STATES:
                busy = True
            if occurrence.state in SENT_STATES and now - occurrence.updated_at < rule.trigger.cooldown_s:
                cooling = True
        return busy, cooling, pending, handled

    def _evaluate(self, rule: AutomationRule) -> Decision:
        def decision(outcome: str, reason: str = "", **extra) -> Decision:
            return Decision(rule.id, rule.revision, outcome, reason, **extra)

        state = self.tracker.state(rule.trigger.source)
        if state.status != "known":
            return decision("unknown", state.reason)
        if not state.inactive:
            return decision("active", state.condition)
        if state.confidence < rule.trigger.min_confidence:
            return decision("unknown", "low_confidence")
        if state.confirmed_s < rule.trigger.threshold_s:
            return decision("waiting", f"{state.confirmed_s:.0f}/{rule.trigger.threshold_s} s observados")
        busy, cooling, occurrence, handled = self._recent(rule, state.episode)
        if handled:
            # Once per episode, even if the rule was edited since: an edit does
            # not fire again for an episode it already saw.
            return decision("already", "episodio ya atendido")
        if busy:
            return decision("busy", "la regla ya tiene una ejecución en curso")
        if cooling:
            return decision("cooldown", "en espera tras la última ejecución")
        if occurrence is None:
            try:
                occurrence, _created = self.store.record_candidate(rule.id, rule.revision, state.episode)
            except StaleRevision:
                return decision("stale", "la regla cambió")
        if occurrence.state not in UNSENT_STATES:
            return decision("already", occurrence.state)
        # A candidate (new, recorded earlier, given back) or an expired claim:
        # the store hands it over only if nobody holds a live lease on it.
        claimed = self.store.claim(occurrence.id, self.owner, lease_s=self.lease_s)
        if claimed is None:
            return decision("claimed_elsewhere", "", occurrence_id=occurrence.id)
        if not self.holds(rule, state.episode):
            # The user came back (or the signal went unknown) before the claim.
            self.store.advance(occurrence.id, "cancelled", token=claimed.claim.token,
                               error="la condición dejó de cumplirse antes de enviar")
            return decision("returned", "", occurrence_id=occurrence.id)
        return decision("ready", "", occurrence_id=occurrence.id, claim_token=claimed.claim.token)


async def run_loop(engine: AutomationEngine, *, tick: Callable[[], None],
                   on_ready: Callable[[Decision], Awaitable[None]], interval: float = 5.0,
                   stop: asyncio.Event | None = None,
                   after_tick: Callable[[], Awaitable[None]] | None = None) -> None:
    """Core's continuous evaluation: observe (``tick``), decide, hand ready occurrences to F06."""
    stop = stop or asyncio.Event()
    while not stop.is_set():
        try:
            tick()
            if after_tick is not None:
                await after_tick()
            if stop.is_set():
                break
            for decision in await asyncio.to_thread(engine.evaluate):
                if stop.is_set():
                    break
                if decision.outcome == "ready":
                    await on_ready(decision)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Automation evaluation failed; retrying on the next pass")
        try:
            await asyncio.wait_for(stop.wait(), interval)
        except TimeoutError:
            pass
