"""F06: run a ready occurrence in Hermes, once, and keep its result.

Sending uses what the pinned Hermes 0.21.6 really admits (``POST /v1/runs``
with ``input``, ``instructions``, ``provider``/``model``, and a durable
``Idempotency-Key``; ``GET /v1/runs/{id}``; ``POST /v1/runs/{id}/stop``).
Each run gets its own Hermes session: the conversation's model (F01) and its
history are not touched.

Destination (T02). Every request goes to a ``RunDestination`` resolved once:
the Hermes endpoint, the profile's route and its credential, in memory only,
with a non-secret fingerprint of exactly those values. The fingerprint is
what is stored and compared, and the request is built from that same object,
so the configuration cannot change between the check and the send.

Admission (Q04, Q05). The slow question goes first: whether Hermes keeps run
keys durably for that destination, for the whole task timeout
(``/v1/capabilities``). Then one store transaction
(``AutomationStore.admit``) checks everything again and records the request
*before* it is posted: the live claim, the rule's current revision, Iris'
single place in Hermes (across rules and processes sharing the store), the
daily quota, the episode's signal, the ``automations.run`` permission, the
profile, the destination, the deadline and the limits the pin cannot enforce.
The request is recorded with an immutable context (Q07): revision, profile,
the destination's fingerprint and the deadline. Following, replaying and
stopping use that context, never the rule as edited later, and only under a
destination with the same fingerprint.

Lost answers (Q06, T03). If the answer to the post is lost the occurrence is
``ambiguous``. Hermes cannot say whether it holds a run without being asked
to admit one, so the same request is posted again only when that would be a
fair admission now: the slow question first, then the episode still holding,
same revision, «Permitir», the same destination, durable keys and the
deadline not passed, checked after every slow step. Otherwise Iris stops
posting (``stopping``), keeps its place until the deadline and then says it
could not know whether Hermes ran the task. A timeout never creates a second
run.

Stopping (Q07, T04). Asking Hermes to stop is not a stop: the occurrence stays
``stopping``, and keeps its place, until Hermes reports the run finished or
its deadline passes; the result says what is known. An answer that arrives
after a cancellation never undoes it, and whoever is settling an occurrence
reads its state again after every wait.

Results are stored as visible text (``core.response_text``): no control
JSON, no avatar actions. Hermes 0.21.6 cannot restrict tools or tokens per
run: a rule asking for either is refused rather than run without the limit.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from core.automation_contracts import FINAL_STATES, AutomationOccurrence, AutomationRule
from core.automation_engine import AutomationEngine, Decision
from core.automation_store import AdmissionRefused, AutomationStore, AutomationStoreError, ClaimLost, StorePathError
from core.response_text import visible_response_text

logger = logging.getLogger(__name__)

TERMINAL = {"completed": "completed", "failed": "failed", "cancelled": "failed", "interrupted": "failed"}
RUNNING = {"queued", "started", "running", "waiting_for_approval"}
SENT = {"submitted", "running", "ambiguous", "stopping"}
DAY = 86_400.0
STOP_CONFIRM_POLLS = 3  # after asking Hermes to stop, how many polls wait for it to say the run ended
UNKNOWN_END = ("Iris no pudo saber si Hermes llegó a recibir la tarea; si la recibió, pudo ejecutarse "
               "sin que Iris tenga su resultado.")
APPROVAL_NOTE = " Hermes esperaba una aprobación que nadie podía dar."
OTHER_DESTINATION = ("Cambió el Hermes o la credencial con que se envió: Iris no puede seguirla ni detenerla "
                     "desde otro destino.")


@dataclass(frozen=True)
class RunDestination:
    """Where and as whom a run is admitted: resolved once, used for the request itself (T02)."""

    profile: str
    base_url: str
    prefix: str
    token: str = field(repr=False)  # in memory only: never stored, never logged
    scope: str = ""  # non-secret fingerprint of exactly these values


class RunTransport(Protocol):
    """How Iris talks to Hermes' runs API (fakes in tests)."""

    def resolve(self, profile: str) -> RunDestination | None:
        """The destination of ``profile`` now; None when unknown or unsafe."""

    def submit(self, destination: RunDestination, body: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
        """``{"outcome": "accepted"|"rejected"|"lost", "run_id": str, "error": str}``."""

    def status(self, destination: RunDestination, run_id: str) -> dict[str, Any]:
        """``{"status": str, "output": Any, "error": str}`` or ``{"status": "lost"}``."""

    def stop(self, destination: RunDestination, run_id: str) -> bool:
        """True when Hermes accepted the request to stop (not that the run ended)."""

    def idempotency(self, destination: RunDestination) -> dict[str, Any]:
        """``{"supported": bool, "durable": bool, "retention_s": float}``; ``{}`` when unknown."""


def unsupported(rule: AutomationRule) -> str:
    """What the pinned Hermes cannot enforce per run, so the rule cannot run (``""``: none)."""
    if rule.execution.tool_allowlist:
        return "Hermes 0.21.6 no permite limitar las herramientas de una ejecución; deja la lista vacía."
    if rule.execution.token_budget:
        return "Hermes 0.21.6 no permite fijar un presupuesto de tokens por ejecución; déjalo en 0."
    return ""


def run_body(rule: AutomationRule) -> dict[str, Any]:
    body: dict[str, Any] = {
        "input": rule.execution.prompt,
        "instructions": (
            f"Esta tarea la inicia la automatización de Iris «{rule.title}» mientras el usuario no está. "
            "Responde con el resultado final en texto. No esperes respuestas del usuario."
        ),
    }
    if rule.execution.provider:
        body.update(provider=rule.execution.provider, model=rule.execution.model)
    return body


class AutomationExecutor:
    def __init__(
        self,
        store: AutomationStore,
        engine: AutomationEngine,
        transport: RunTransport,
        *,
        permission: Callable[[], str],
        profile_problem: Callable[[str], str] = lambda profile: "",
        clock: Callable[[], float] = time.time,
        max_parallel: int = 1,
        poll_s: float = 5.0,
        sleep: Callable[[float], Any] = asyncio.sleep,
    ) -> None:
        self.store, self.engine, self.transport = store, engine, transport
        self._permission, self._profile_problem = permission, profile_problem
        self._clock, self.max_parallel, self.poll_s, self._sleep = clock, max_parallel, poll_s, sleep
        self._settling: set[str] = set()  # occurrences this executor is sending or following

    # ── Checks ──────────────────────────────────────────────────────────

    def _idempotency_problem(self, destination: RunDestination, timeout_s: float) -> str:
        """Why Hermes could not settle a lost answer without a second run (``""``: it can). Slow."""
        info = self.transport.idempotency(destination)
        if not info.get("supported") or not info.get("durable"):
            return ("Hermes no guarda de forma duradera las claves de sus ejecuciones: si se perdiera una "
                    "respuesta, Iris no podría evitar repetir la tarea.")
        if float(info.get("retention_s") or 0) < timeout_s:
            return "Hermes no conserva la clave de la ejecución durante todo el plazo de la tarea."
        return ""

    def _consent_problem(self, rule: AutomationRule, occurrence: AutomationOccurrence,
                         destination: RunDestination) -> str:
        """What must still hold at the moment a request is admitted or posted again (fast, last)."""
        if self._clock() >= occurrence.created_at + rule.execution.timeout_s:
            return "Ya pasó su plazo."  # T03: checked after every slow step
        if not self.engine.holds(rule, occurrence.occurrence_key):
            return "La condición dejó de cumplirse antes de enviar."
        verdict = self._permission()
        if verdict != "allow":
            return ("Las automatizaciones necesitan «Permitir» en Capability Center para ejecutarse "
                    "sin que estés." if verdict == "ask" else "Las automatizaciones están bloqueadas en Capability Center.")
        if reason := unsupported(rule):
            return reason
        if reason := self._profile_problem(rule.execution.profile):
            return reason
        current = self.transport.resolve(rule.execution.profile)
        if current is None or current.scope != destination.scope:
            return "Cambió el Hermes o la credencial mientras se preparaba el envío."
        return ""

    def _context(self, rule: AutomationRule, occurrence: AutomationOccurrence,
                 destination: RunDestination) -> dict[str, Any]:
        """The immutable context a request is admitted in (Q07); no credential is kept."""
        return {
            "rule_revision": rule.revision, "profile": destination.profile, "scope": destination.scope,
            "deadline": occurrence.created_at + rule.execution.timeout_s, "timeout_s": rule.execution.timeout_s,
            "provider": rule.execution.provider, "model": rule.execution.model,
        }

    def _bound(self, context: dict[str, Any]) -> RunDestination | None:
        """The destination of the recorded context, only if it is still the same one (T02)."""
        destination = self.transport.resolve(context["profile"])
        return destination if destination is not None and destination.scope == context["scope"] else None

    def _replay(self, occurrence: AutomationOccurrence, context: dict[str, Any]) -> tuple[str, RunDestination | None]:
        """``(problem, destination)``: whether posting the recorded request again would be a fair
        admission now, and the destination to post it to (Q06, T02, T03).

        Hermes cannot say whether it holds a run without admitting one: if the
        first post never arrived, this post is what runs the task.
        """
        if self._clock() >= float(context["deadline"]):
            return "Ya pasó su plazo.", None
        destination = self._bound(context)
        if destination is None:
            return "Cambió el Hermes o la credencial con que se envió: repetirla podría crear otra ejecución.", None
        if reason := self._idempotency_problem(destination, float(context["timeout_s"])):  # slow first
            return reason, None
        rule = self.store.get_rule(occurrence.rule_id)
        if rule is None or not rule.enabled or rule.revision != context["rule_revision"]:
            return "La regla cambió o se pausó mientras tanto.", None
        if reason := self._consent_problem(rule, occurrence, destination):
            return reason, None
        return "", destination

    def _refuse(self, occurrence_id: str, token: str, reason: str) -> str:
        try:
            self.store.advance(occurrence_id, "cancelled", token=token, error=reason)
        except AutomationStoreError:
            pass
        return "cancelled"

    # ── Dispatch ────────────────────────────────────────────────────────

    async def dispatch(self, decision: Decision) -> str:
        """Admit a ready occurrence, send it and follow it to its end; returns its final state."""
        occurrence = self.store.get_occurrence(decision.occurrence_id)
        if occurrence is None or occurrence.state != "claimed" or occurrence.id in self._settling:
            return "skipped"
        rule = self.store.get_rule(occurrence.rule_id)
        if rule is None or not rule.enabled or rule.revision != occurrence.rule_revision:
            return self._refuse(occurrence.id, decision.claim_token, "La regla cambió o se pausó antes de enviar.")
        self._settling.add(occurrence.id)  # reserved here: reconciliation leaves it alone
        try:
            if reason := self._profile_problem(rule.execution.profile):
                return self._refuse(occurrence.id, decision.claim_token, reason)
            destination = self.transport.resolve(rule.execution.profile)
            if destination is None:
                return self._refuse(occurrence.id, decision.claim_token,
                                    "No se pudo identificar el Hermes al que se enviaría la tarea.")
            # The slow question first (Q04): what changes while it is asked is
            # checked afterwards, at admission.
            problem = await asyncio.to_thread(self._idempotency_problem, destination, rule.execution.timeout_s)
            if problem:
                return self._refuse(occurrence.id, decision.claim_token, problem)
            try:
                await asyncio.to_thread(
                    self.store.admit, occurrence.id, decision.claim_token, body=run_body(rule),
                    idempotency_key=f"iris-automation-{occurrence.correlation_id}",
                    context=self._context(rule, occurrence, destination), max_parallel=self.max_parallel,
                    quota_since=self._clock() - DAY,
                    guard=lambda current: self._consent_problem(current, occurrence, destination))
            except ClaimLost:
                return "claim_lost"  # the lease expired: the engine may take it up again
            except AdmissionRefused as refused:
                return self._refuse(occurrence.id, decision.claim_token, str(refused))
            return await self._settle(occurrence.id, first_post=destination)
        finally:
            self._settling.discard(occurrence.id)

    async def _settle(self, occurrence_id: str, *, first_post: RunDestination | None = None) -> str:
        """Drive sent work to its end, always with its recorded request and context.

        ``first_post``: the destination just admitted, for the admission's own post.
        """
        recorded = self.store.submission(occurrence_id)
        if recorded is None:
            return "skipped"
        key, body, context = recorded
        if not context.get("scope"):
            # Recorded before Q07 (development stores only): nothing can be checked.
            occurrence = self.store.get_occurrence(occurrence_id)
            if occurrence and occurrence.state not in FINAL_STATES:
                self.store.finish(occurrence_id, status="failed",
                                  text="No se puede comprobar el contexto con que se envió. " + UNKNOWN_END)
                return "failed"
            return occurrence.state if occurrence else "skipped"
        deadline = float(context["deadline"])
        asked_stop = False
        while True:
            occurrence = self.store.get_occurrence(occurrence_id)
            if occurrence is None:
                return "skipped"
            if occurrence.state in FINAL_STATES:
                return occurrence.state
            try:
                if occurrence.run_id:
                    destination = self._bound(context)
                    if occurrence.state == "stopping" and not asked_stop and destination is not None:
                        # The user cancelled before Iris knew the run: ask now that it is known.
                        await asyncio.to_thread(self.transport.stop, destination, occurrence.run_id)
                        asked_stop = True
                    final = await self._watch(occurrence, destination, deadline)
                    if final:
                        return final
                elif occurrence.state == "stopping":
                    # Never known to Iris, and it will not be posted again: wait its deadline, then say so.
                    if self._clock() >= deadline:
                        self.store.finish(occurrence_id, status="failed",
                                          text=f"{occurrence.error} {UNKNOWN_END}".strip())
                        return "failed"
                else:
                    admission, first_post = first_post, None
                    if admission is not None:
                        destination = admission
                    else:
                        problem, destination = await asyncio.to_thread(self._replay, occurrence, context)
                        if problem:
                            self._stop_sending(occurrence_id, "No se reenvía. " + problem)
                            continue
                        # S02: the slow step may have let a cancellation in. Only a request still
                        # waiting for its answer may be posted again; anything else is followed.
                        current = self.store.get_occurrence(occurrence_id)
                        if current is None or current.state not in {"submitted", "ambiguous"} or current.run_id:
                            continue
                    sent = await asyncio.to_thread(self.transport.submit, destination, body, key)
                    if self._after_post(occurrence_id, sent, replay=admission is None):
                        return self.store.get_occurrence(occurrence_id).state
                    if sent.get("outcome") == "accepted":
                        continue  # follow the run now
            except StorePathError:
                raise  # the store itself is no longer usable here: nothing more can be recorded
            except AutomationStoreError:
                # The state moved meanwhile (a cancellation, another settler): read it again after the wait.
                logger.info("Occurrence %s changed while it was being settled; reading it again", occurrence_id)
            await self._sleep(self.poll_s)

    def _after_post(self, occurrence_id: str, sent: dict[str, Any], *, replay: bool) -> bool:
        """Record what a post answered on the occurrence *as it is now* (T04); True when it ended.

        An answer never undoes a cancellation: a run learnt after it is kept
        with ``stopping`` (and asked to stop), a lost answer leaves it waiting.
        """
        current = self.store.get_occurrence(occurrence_id)
        if current is None or current.state in FINAL_STATES:
            return True
        outcome = sent.get("outcome")
        if outcome == "accepted" and sent.get("run_id"):
            self.store.attach_run(occurrence_id, str(sent["run_id"]))
            return False
        if outcome == "lost":
            if current.state == "submitted":
                self.store.advance(occurrence_id, "ambiguous", error="No llegó la respuesta de Hermes al enviar.")
            return False
        error = str(sent.get("error") or "Hermes no aceptó la tarea.")[:400]
        if replay:
            # Refusing the repetition does not prove the first post never arrived.
            self._stop_sending(occurrence_id, f"No se pudo confirmar con Hermes: {error}")
            return False
        self.store.finish(occurrence_id, status="failed", text=error)
        return True

    def _stop_sending(self, occurrence_id: str, reason: str) -> None:
        current = self.store.get_occurrence(occurrence_id)
        if current is not None and current.state in {"submitted", "ambiguous", "running"}:
            self.store.advance(occurrence_id, "stopping", error=reason[:500])

    async def _watch(self, occurrence: AutomationOccurrence, destination: RunDestination | None,
                     deadline: float) -> str | None:
        """One look at a known run; past its deadline, ask Hermes to stop it and confirm the end.

        ``destination`` None: the recorded destination changed, so Iris cannot ask about the
        run under another one; it waits its deadline and says so.
        """
        run_id = occurrence.run_id
        if destination is None:
            if self._clock() < deadline:
                return None
            self.store.finish(occurrence.id, status="failed",
                              text="Se agotó el tiempo de la tarea. " + OTHER_DESTINATION
                              + " Puede seguir ejecutándose en Hermes.")
            return "failed"
        state = await asyncio.to_thread(self.transport.status, destination, run_id)
        status = str(state.get("status") or "")
        if status in TERMINAL:
            return self._finish_run(occurrence.id, state)
        if occurrence.state in {"submitted", "ambiguous"} and status in RUNNING:
            self.store.attach_run(occurrence.id, run_id, state="running")
        if self._clock() < deadline:
            return None
        accepted = await asyncio.to_thread(self.transport.stop, destination, run_id)
        approval = APPROVAL_NOTE if status == "waiting_for_approval" else ""
        for _ in range(STOP_CONFIRM_POLLS):
            await self._sleep(self.poll_s)
            later = await asyncio.to_thread(self.transport.status, destination, run_id)
            if str(later.get("status") or "") in TERMINAL:
                return self._finish_run(occurrence.id, later, timed_out=True, note=approval)
        ending = (" Se pidió a Hermes detenerla, pero no confirmó que terminara: puede seguir ejecutándose en Hermes."
                  if accepted else " Hermes no aceptó detenerla: puede seguir ejecutándose en Hermes.")
        self.store.finish(occurrence.id, status="failed", text="Se agotó el tiempo de la tarea." + approval + ending)
        return "failed"

    def _finish_run(self, occurrence_id: str, state: dict[str, Any], *, timed_out: bool = False,
                    note: str = "") -> str:
        """A run Hermes reports finished: its visible text, or what ended it."""
        final = TERMINAL[str(state["status"])]
        current = self.store.get_occurrence(occurrence_id)
        if final == "completed":
            text = visible_response_text(state.get("output"))
        elif current is not None and current.state == "stopping":
            text = "Cancelada por el usuario; Hermes confirmó que se detuvo."
        elif timed_out:
            text = "Se agotó el tiempo de la tarea; Hermes la detuvo." + note
        else:
            text = str(state.get("error") or "La tarea terminó sin completarse.")[:2000]
        self.store.finish(occurrence_id, status=final, text=text)
        return final

    # ── Reconciliation (start-up, after a cancellation, and periodically) ──

    async def reconcile(self) -> list[str]:
        """Settle sent work nobody here is settling, each with its own context. It never
        admits new work: a request is only posted again under ``_replay``'s conditions."""
        pending = [o.id for o in self.store.occurrences(active_only=True)
                   if o.state in SENT and o.id not in self._settling]
        self._settling.update(pending)

        async def one(occurrence_id: str) -> str:
            try:
                return await self._settle(occurrence_id)
            finally:
                self._settling.discard(occurrence_id)

        return list(await asyncio.gather(*(one(occurrence_id) for occurrence_id in pending)))

    def cancel(self, occurrence_id: str) -> str:
        """The user cancels. Unsent work is cancelled; sent work stops being sent and, if its
        run is known, Hermes is asked to stop it. Nothing is called stopped until Hermes says so."""
        occurrence = self.store.get_occurrence(occurrence_id)
        if occurrence is None:
            return "unknown"
        if occurrence.state in {"candidate", "claimed"}:
            self.store.advance(occurrence_id, "cancelled", error="Cancelada por el usuario.")
            return "cancelled"
        if occurrence.state not in SENT:
            return occurrence.state
        if occurrence.state != "stopping":
            self.store.advance(occurrence_id, "stopping", error="Cancelación pedida por el usuario.")
        if not occurrence.run_id:
            return "not_resent"  # it will not be posted again; a run Iris does not know cannot be stopped
        recorded = self.store.submission(occurrence_id)
        destination = self._bound(recorded[2]) if recorded and recorded[2].get("scope") else None
        if destination is None:
            return "stop_not_possible"  # its destination changed: Iris cannot reach that run
        return "stop_requested" if self.transport.stop(destination, occurrence.run_id) else "stop_not_accepted"
