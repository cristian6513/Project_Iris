"""F06: executor, Hermes transport and API, against a fake Hermes runs API.

The fake honours Idempotency-Key like Hermes 0.21.6: a replay in the same
scope (profile and credential) returns the run it admitted, and another scope
admits a new one. So "one run per occurrence" is measured, not assumed.
Codex's qa11 review (Q04–Q07) has its own regressions below.
"""

from __future__ import annotations

import asyncio
import json
import threading
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.automation_api import HermesRunsTransport, automation_router
from core.automation_contracts import AutomationRule, Execution, new_id
from core.automation_engine import AutomationEngine
from core.automation_executor import UNKNOWN_END, AutomationExecutor, RunDestination, run_body
from core.automation_store import AdmissionRefused, AutomationStore
from core.presence_state import PresenceTracker
from tests.test_f03b_vision_snapshot import core  # noqa: F401


class Clocks:
    def __init__(self):
        self.mono, self.wall = 1000.0, 1_800_000_000.0

    def step(self, seconds):
        self.mono += seconds
        self.wall += seconds


class FakeHermes:
    """Runs keyed by (scope, Idempotency-Key); each status() call advances the script of that run."""

    def __init__(self):
        self.runs: dict[str, dict] = {}
        self.by_key: dict[tuple[str, str], str] = {}
        self.posts: list[tuple[str, dict, str]] = []
        self.stops: list[str] = []
        self.stop_profiles: list[str] = []
        self.status_profiles: list[str] = []
        self.lose_next_answer = False  # admitted, but the answer is lost
        self.lost_answers = 0  # answers lost from now on (every post is admitted)
        self.drop_next_post = False  # the post never reaches Hermes: nothing is admitted
        self.reject_next_post = False  # Hermes refuses the post (for instance a 401): nothing is admitted
        self.stop_works = True  # Hermes accepts a request to stop a run it knows
        self.idempotency_info = {"supported": True, "durable": True, "retention_s": 86_400.0}
        self.on_idempotency = None  # called while /v1/capabilities is being read
        self.on_resolve = None  # called after each resolve, with how many there were
        self.on_submit = None  # called while a post is in flight (before its answer)
        self.resolves = 0
        self.scopes: dict[str, str] = {}
        self.script = ["queued", "running", "completed"]
        self.output = json.dumps({"texto_hablado": "Resumen sintético listo.", "emocion": "feliz",
                                  "animacion_virtual": "saludar"})

    def scope(self, profile):
        return self.scopes.get(profile, f"scope-{profile}")

    def resolve(self, profile):
        destination = RunDestination(profile=profile, base_url="http://hermes.fake", prefix="",
                                     token=f"clave-{self.scope(profile)}", scope=self.scope(profile))
        self.resolves += 1
        if self.on_resolve:
            self.on_resolve(self.resolves)
        return destination

    def submit(self, destination, body, key):
        # Hermes sees the credential the request carries: the destination's, nothing re-read.
        self.posts.append((destination.profile, body, key))
        if self.on_submit:
            self.on_submit()
        if self.drop_next_post:
            self.drop_next_post = False
            return {"outcome": "lost", "error": "timed out"}
        if self.reject_next_post:
            self.reject_next_post = False
            return {"outcome": "rejected", "error": "HTTP 401"}
        scoped = (destination.scope, key)
        if scoped in self.by_key:
            answer = self._answer(self.by_key[scoped])
            return answer | {"replayed": True} if answer["outcome"] == "accepted" else answer
        run_id = f"run_{len(self.runs) + 1}"
        self.by_key[scoped] = run_id
        self.runs[run_id] = {"step": 0, "script": list(self.script), "profile": destination.profile,
                             "scope": destination.scope}
        return self._answer(run_id)

    def _answer(self, run_id):
        if self.lose_next_answer or self.lost_answers:
            self.lose_next_answer = False
            self.lost_answers = max(0, self.lost_answers - 1)
            return {"outcome": "lost", "error": "timed out"}  # Hermes admitted it; the answer was lost
        return {"outcome": "accepted", "run_id": run_id}

    def status(self, destination, run_id):
        self.status_profiles.append(destination.profile)
        run = self.runs.get(run_id)
        if run is None or run["scope"] != destination.scope:  # another scope cannot see it
            return {"status": "lost"}
        status = run["script"][min(run["step"], len(run["script"]) - 1)]
        run["step"] += 1
        return {"status": status, "output": self.output if status == "completed" else None,
                "error": "Hermes falló (sintético)" if status == "failed" else ""}

    def stop(self, destination, run_id):
        self.stops.append(run_id)
        self.stop_profiles.append(destination.profile)
        run = self.runs.get(run_id)
        if not self.stop_works or run is None or run["scope"] != destination.scope:
            return False
        run.update(script=["cancelled"], step=0)
        return True

    def idempotency(self, destination):
        if self.on_idempotency:
            self.on_idempotency(destination.profile)
        return dict(self.idempotency_info)


@pytest.fixture
def setup(tmp_path):
    clocks = Clocks()
    store = AutomationStore(tmp_path / "automations.sqlite3", clock=lambda: clocks.wall)
    tracker = PresenceTracker(monotonic=lambda: clocks.mono, wall=lambda: clocks.wall, run_id="run1")
    engine = AutomationEngine(store, tracker, owner="core-1", clock=lambda: clocks.wall)
    hermes = FakeHermes()
    permission = SimpleNamespace(verdict="allow")
    profile = SimpleNamespace(problem="")
    hooks: list = []  # called on every wait, after the clock moved

    async def wait(seconds):
        clocks.step(seconds)
        tracker.iris_tick(local_surface_open=True)  # Core's loop keeps observing while the executor waits
        for hook in list(hooks):
            hook()

    executor = AutomationExecutor(store, engine, hermes, permission=lambda: permission.verdict,
                                  profile_problem=lambda name: profile.problem,
                                  clock=lambda: clocks.wall, poll_s=5, sleep=wait)

    def add_rule(**execution):
        return store.create_rule(AutomationRule.model_validate({
            "id": new_id(), "title": "Regla sintética", "enabled": True,
            "trigger": {"source": "iris_idle", "threshold_s": 300},
            "execution": {"prompt": "Tarea sintética", **execution},
            "created_at": clocks.wall, "updated_at": clocks.wall}))

    def idle_until_ready():
        tracker.iris_tick(local_surface_open=True)
        for _ in range(60):
            clocks.step(5)
            tracker.iris_tick(local_surface_open=True)
        return [d for d in engine.evaluate() if d.outcome == "ready"]

    def ready_now():
        [decision] = idle_until_ready()
        return decision

    def new_episode():
        tracker.iris_interaction()
        clocks.step(5)
        return ready_now()

    def result_text(occurrence_id):
        return store.result(store.get_occurrence(occurrence_id).result_id).text

    return SimpleNamespace(store=store, tracker=tracker, engine=engine, hermes=hermes, executor=executor,
                           permission=permission, profile=profile, hooks=hooks, clocks=clocks, add_rule=add_rule,
                           ready_now=ready_now, idle_until_ready=idle_until_ready, new_episode=new_episode,
                           result_text=result_text, wait=wait)


def run(coroutine):
    return asyncio.run(coroutine)


def admit_directly(store, decision, *, max_parallel=1, guard=lambda rule: ""):
    """Admission as F06 does it, for tests that need a place taken by hand."""
    return store.admit(decision.occurrence_id, decision.claim_token, body={"input": "x"},
                       idempotency_key="k-" + decision.occurrence_id,
                       context={"rule_revision": 1, "profile": "default", "scope": "scope-default",
                                "deadline": 1e11, "timeout_s": 900, "provider": "", "model": ""},
                       max_parallel=max_parallel, quota_since=0, guard=guard)


def test_a_ready_occurrence_runs_once_and_keeps_visible_text(setup):
    rule = setup.add_rule()
    decision = setup.ready_now()
    assert run(setup.executor.dispatch(decision)) == "completed"
    [occurrence] = setup.store.occurrences()
    assert occurrence.state == "completed" and occurrence.run_id == "run_1"
    result = setup.store.result(occurrence.result_id)
    assert result.text == "Resumen sintético listo."  # no control JSON, no avatar action
    assert setup.store.delivery(result.id).status == "pending"
    [(profile, body, key)] = setup.hermes.posts
    assert key == f"iris-automation-{occurrence.correlation_id}" and profile == "default"
    assert body == run_body(rule) and "session_id" not in body  # its own session: F01 untouched
    _, _, context = setup.store.submission(occurrence.id)
    assert context["profile"] == "default" and context["scope"] == "scope-default"
    assert context["deadline"] == occurrence.created_at + 900 and context["rule_revision"] == 1


def events_of(store):
    return [entry["kind"] for entry in reversed(store.history())]


def test_a_lost_answer_is_ambiguous_and_the_same_request_settles_it(setup):
    setup.add_rule()
    decision = setup.ready_now()
    setup.hermes.lose_next_answer = True
    assert run(setup.executor.dispatch(decision)) == "completed"
    events = events_of(setup.store)
    assert events.index("occurrence_ambiguous") < events.index("occurrence_running")  # until the same key answered
    assert len(setup.hermes.runs) == 1  # the resend got the run Hermes had admitted
    keys = {key for _, _, key in setup.hermes.posts}
    bodies = {json.dumps(body, sort_keys=True) for _, body, _ in setup.hermes.posts}
    assert len(setup.hermes.posts) == 2 and len(keys) == 1 and len(bodies) == 1


def test_a_restart_while_ambiguous_reconciles_to_the_same_run(setup):
    setup.add_rule()
    decision = setup.ready_now()
    setup.hermes.lose_next_answer = True

    async def core_stops(seconds):
        raise asyncio.CancelledError  # Core closes while it waits to post again

    setup.executor._sleep = core_stops
    with pytest.raises(asyncio.CancelledError):
        run(setup.executor.dispatch(decision))
    assert [o.state for o in setup.store.occurrences()] == ["ambiguous"]
    restarted = AutomationExecutor(setup.store, setup.engine, setup.hermes, permission=lambda: "allow",
                                   clock=lambda: setup.clocks.wall, poll_s=5, sleep=setup.wait)
    assert run(restarted.reconcile()) == ["completed"]
    assert len(setup.hermes.runs) == 1 and len({key for _, _, key in setup.hermes.posts}) == 1


def test_after_a_core_restart_an_ambiguous_request_is_not_replayed(setup):
    # The executor above restarted while its episode still held. A Core restart starts a
    # new observation run: the old episode can no longer be checked, so nothing is posted again.
    setup.add_rule(timeout_s=60)
    decision = setup.ready_now()
    setup.hermes.lose_next_answer = True

    async def core_stops(seconds):
        raise asyncio.CancelledError

    setup.executor._sleep = core_stops
    with pytest.raises(asyncio.CancelledError):
        run(setup.executor.dispatch(decision))
    tracker = PresenceTracker(monotonic=lambda: setup.clocks.mono, wall=lambda: setup.clocks.wall, run_id="run2")
    engine = AutomationEngine(setup.store, tracker, owner="core-new", clock=lambda: setup.clocks.wall)

    async def new_core_waits(seconds):
        setup.clocks.step(seconds)
        tracker.iris_tick(local_surface_open=True)

    restarted = AutomationExecutor(setup.store, engine, setup.hermes, permission=lambda: "allow",
                                   clock=lambda: setup.clocks.wall, poll_s=5, sleep=new_core_waits)
    assert run(restarted.reconcile()) == ["failed"]
    assert len(setup.hermes.posts) == 1 and len(setup.hermes.runs) == 1  # Hermes may hold it; Iris says so
    text = setup.result_text(decision.occurrence_id)
    assert "No se reenvía" in text and "condición" in text and UNKNOWN_END in text


def test_an_unanswered_request_is_never_posted_again_past_its_deadline(setup):
    setup.add_rule(timeout_s=60)
    decision = setup.ready_now()
    setup.hermes.lost_answers = 1_000  # Hermes never answers the post
    assert run(setup.executor.dispatch(decision)) == "failed"
    [occurrence] = setup.store.occurrences()
    text = setup.result_text(occurrence.id)
    assert "No se reenvía" in text and "plazo" in text and UNKNOWN_END in text
    assert setup.clocks.wall < occurrence.created_at + 60 + 10  # settled at the deadline, not later
    assert len(setup.hermes.runs) == 1 and len({key for _, _, key in setup.hermes.posts}) == 1
    posts = len(setup.hermes.posts)
    assert run(setup.executor.reconcile()) == []  # settled: nothing is posted at the next start
    assert len(setup.hermes.posts) == posts


@pytest.mark.parametrize("info, needle", [
    ({}, "duradera"),  # Hermes does not say: nothing is sent
    ({"supported": True, "durable": False, "retention_s": 86_400.0}, "duradera"),
    ({"supported": True, "durable": True, "retention_s": 60.0}, "plazo"),  # shorter than the task's timeout
])
def test_without_durable_idempotency_nothing_is_sent(setup, info, needle):
    setup.add_rule()
    setup.hermes.idempotency_info = info
    assert run(setup.executor.dispatch(setup.ready_now())) == "cancelled"
    assert needle in setup.store.occurrences()[0].error and setup.hermes.posts == []


def test_an_ambiguous_request_is_not_posted_again_once_keys_stop_being_durable(setup):
    setup.add_rule(timeout_s=60)
    decision = setup.ready_now()
    setup.hermes.lose_next_answer = True
    setup.hooks.append(lambda: setup.hermes.idempotency_info.update(durable=False))  # Hermes restarted badly
    assert run(setup.executor.dispatch(decision)) == "failed"
    assert len(setup.hermes.posts) == 1  # a second post could have become a second run
    assert "No se reenvía" in setup.result_text(decision.occurrence_id)


@pytest.mark.parametrize("change, needle", [
    ("permission", "Permitir"),
    ("pause", "pausó"),
    ("edit", "cambió"),
])
def test_after_the_wait_a_revoked_or_changed_rule_is_not_posted_again(setup, change, needle):
    # If the first post never arrived, posting again is what runs the task: it needs today's consent.
    rule = setup.add_rule(timeout_s=60)
    decision = setup.ready_now()
    setup.hermes.lost_answers = 1_000

    def the_user_changes_something():
        if change == "permission":
            setup.permission.verdict = "ask"
        elif setup.store.get_rule(rule.id).revision == 1:
            setup.store.update_rule(rule.id, expected_revision=1,
                                    **({"enabled": False} if change == "pause" else {"title": "Otra tarea"}))

    setup.hooks.append(the_user_changes_something)
    assert run(setup.executor.dispatch(decision)) == "failed"
    assert len(setup.hermes.posts) == 1
    text = setup.result_text(decision.occurrence_id)
    assert "No se reenvía" in text and needle in text


def test_a_disk_failure_before_sending_sends_nothing(setup, monkeypatch):
    setup.add_rule()
    decision = setup.ready_now()

    def broken(*args, **kwargs):
        raise OSError("disco lleno (simulado)")

    monkeypatch.setattr(setup.store, "admit", broken)
    with pytest.raises(OSError):
        run(setup.executor.dispatch(decision))
    assert setup.hermes.posts == []
    assert [o.state for o in setup.store.occurrences()] == ["claimed"]  # the engine takes it up again later


def test_a_disk_failure_after_sending_is_reconciled_to_the_same_run(setup, monkeypatch):
    setup.add_rule()
    decision = setup.ready_now()
    real = setup.store.attach_run
    calls = {"n": 0}

    def fails_once(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("disco lleno (simulado)")
        return real(*args, **kwargs)

    monkeypatch.setattr(setup.store, "attach_run", fails_once)
    with pytest.raises(OSError):
        run(setup.executor.dispatch(decision))
    assert [o.state for o in setup.store.occurrences()] == ["submitted"]  # sent, run id not recorded
    monkeypatch.setattr(setup.store, "attach_run", real)
    assert run(setup.executor.reconcile()) == ["completed"]
    assert len(setup.hermes.runs) == 1


def test_an_expired_lease_is_never_sent(setup):
    setup.add_rule()
    decision = setup.ready_now()
    setup.clocks.step(61)
    setup.tracker.iris_tick(local_surface_open=True)
    assert run(setup.executor.dispatch(decision)) == "claim_lost"
    assert setup.hermes.posts == []


@pytest.mark.parametrize("verdict", ["ask", "deny"])
def test_without_allow_nothing_runs(setup, verdict):
    setup.add_rule()
    setup.permission.verdict = verdict
    decision = setup.ready_now()
    assert run(setup.executor.dispatch(decision)) == "cancelled"
    [occurrence] = setup.store.occurrences()
    assert occurrence.state == "cancelled" and "Capability Center" in occurrence.error
    assert setup.hermes.posts == []


def test_limits_the_pin_cannot_enforce_are_refused(setup):
    setup.add_rule(tool_allowlist=["web_search"])
    decision = setup.ready_now()
    assert run(setup.executor.dispatch(decision)) == "cancelled"
    assert "herramientas" in setup.store.occurrences()[0].error and setup.hermes.posts == []


def test_revalidation_right_before_sending(setup):
    rule = setup.add_rule()
    decision = setup.ready_now()
    setup.tracker.iris_interaction()  # the user came back after the claim
    assert run(setup.executor.dispatch(decision)) == "cancelled"
    assert "condición" in setup.store.occurrences()[0].error
    decision = setup.new_episode()
    setup.store.update_rule(rule.id, expected_revision=1, title="Editada")  # edited after the claim
    assert run(setup.executor.dispatch(decision)) in {"cancelled", "skipped"}
    assert setup.hermes.posts == []


def test_daily_quota_and_concurrency(setup):
    setup.add_rule(max_runs_per_day=1)
    assert run(setup.executor.dispatch(setup.ready_now())) == "completed"
    second = setup.new_episode()
    assert run(setup.executor.dispatch(second)) == "cancelled"
    assert "límite" in setup.store.get_occurrence(second.occurrence_id).error
    other = setup.add_rule()
    third_rule = setup.add_rule()
    ready = {d.rule_id: d for d in setup.engine.evaluate() if d.outcome == "ready"}
    admit_directly(setup.store, ready[other.id])  # another rule's sent run holds the place
    assert run(setup.executor.dispatch(ready[third_rule.id])) == "cancelled"
    assert "otra automatización" in setup.store.get_occurrence(ready[third_rule.id].occurrence_id).error


def test_a_run_past_its_timeout_is_stopped_and_failed(setup):
    setup.add_rule(timeout_s=60)
    setup.hermes.script = ["running"]
    assert run(setup.executor.dispatch(setup.ready_now())) == "failed"
    assert setup.hermes.stops == ["run_1"]
    result = setup.store.result(setup.store.occurrences()[0].result_id)
    assert result.status == "failed" and "tiempo" in result.text and "Hermes la detuvo" in result.text


def test_a_run_hermes_stops_answering_about_is_settled_at_its_deadline(setup):
    setup.add_rule(timeout_s=60)
    setup.hermes.script = ["running"]
    real_status = setup.hermes.status
    setup.hermes.status = lambda destination, run_id: {"status": "lost"}
    decision = setup.ready_now()
    assert run(setup.executor.dispatch(decision)) == "failed"
    setup.hermes.status = real_status
    assert setup.store.get_occurrence(decision.occurrence_id).state == "failed" and setup.hermes.stops == ["run_1"]
    assert "no confirmó que terminara" in setup.result_text(decision.occurrence_id)
    setup.hermes.script = ["completed"]  # the place is free again: nothing stays unsettled holding it
    assert run(setup.executor.dispatch(setup.new_episode())) == "completed"


def test_a_failed_run_keeps_its_error(setup):
    setup.add_rule()
    setup.hermes.script = ["failed"]
    decision = setup.ready_now()
    assert run(setup.executor.dispatch(decision)) == "failed"
    assert setup.result_text(decision.occurrence_id) == "Hermes falló (sintético)"


# ── Q04: what changes while Hermes' capabilities are read is caught at admission ──


@pytest.mark.parametrize("change, needle", [
    ("permission", "Capability Center"),
    ("return", "condición"),
    ("closed", "condición"),
    ("profile", "perfil"),
    ("edit", "regla"),
])
def test_a_change_while_capabilities_are_read_is_caught_at_admission(setup, change, needle):
    rule = setup.add_rule()
    decision = setup.ready_now()

    def meanwhile(profile):
        if change == "permission":
            setup.permission.verdict = "deny"
        elif change == "return":
            setup.tracker.iris_interaction()
        elif change == "closed":
            setup.tracker.iris_tick(local_surface_open=False)  # the Iris window was closed
        elif change == "profile":
            setup.profile.problem = "El perfil «default» ya no está disponible."
        else:
            setup.store.update_rule(rule.id, expected_revision=1, title="Otra")

    setup.hermes.on_idempotency = meanwhile
    assert run(setup.executor.dispatch(decision)) in {"cancelled", "claim_lost"}
    occurrence = setup.store.get_occurrence(decision.occurrence_id)
    assert occurrence.state == "cancelled" and needle in occurrence.error
    assert setup.hermes.posts == []


def test_nothing_changing_while_capabilities_are_read_still_runs_once(setup):
    setup.add_rule()
    setup.hermes.on_idempotency = lambda profile: None
    assert run(setup.executor.dispatch(setup.ready_now())) == "completed"
    assert len(setup.hermes.posts) == 1


# ── Q05: one place in Hermes, reserved inside the admission transaction ──


def test_two_rules_admitted_at_once_never_both_run(setup):
    setup.add_rule()
    setup.add_rule()
    decisions = setup.idle_until_ready()
    assert len(decisions) == 2
    both_asking = threading.Barrier(2)
    setup.hermes.on_idempotency = lambda profile: both_asking.wait(10)  # both pass the slow question together

    async def both():
        return await asyncio.gather(*(setup.executor.dispatch(d) for d in decisions))

    assert sorted(run(both())) == ["cancelled", "completed"]
    assert len(setup.hermes.posts) == 1 and len(setup.hermes.runs) == 1
    refused = [o for o in setup.store.occurrences() if o.state == "cancelled"]
    assert len(refused) == 1 and "otra automatización" in refused[0].error


def test_two_executors_on_the_same_store_never_both_run(setup, tmp_path):
    setup.add_rule()
    setup.add_rule()
    decisions = setup.idle_until_ready()
    other_store = AutomationStore(setup.store.path, clock=lambda: setup.clocks.wall)
    other_engine = AutomationEngine(other_store, setup.tracker, owner="core-2", clock=lambda: setup.clocks.wall)
    other = AutomationExecutor(other_store, other_engine, setup.hermes, permission=lambda: "allow",
                               clock=lambda: setup.clocks.wall, poll_s=5, sleep=setup.wait)
    both_asking = threading.Barrier(2)
    setup.hermes.on_idempotency = lambda profile: both_asking.wait(10)

    async def both():
        return await asyncio.gather(setup.executor.dispatch(decisions[0]), other.dispatch(decisions[1]))

    assert sorted(run(both())) == ["cancelled", "completed"]
    assert len(setup.hermes.posts) == 1


def test_the_daily_quota_is_counted_inside_the_admission(setup):
    rule = setup.add_rule(max_runs_per_day=1)
    store = setup.store
    claimed = []
    for key in ("manual:1", "manual:2"):
        occurrence, _ = store.record_candidate(rule.id, 1, key)
        claimed.append(store.claim(occurrence.id, "core-1"))
    start, outcomes = threading.Barrier(2), []

    def admit(occurrence):
        start.wait(10)
        try:
            store.admit(occurrence.id, occurrence.claim.token, body={"input": "x"}, idempotency_key="k-" + occurrence.id,
                        context={}, max_parallel=5, quota_since=0)
            outcomes.append("admitted")
        except AdmissionRefused as refused:
            outcomes.append(str(refused))

    threads = [threading.Thread(target=admit, args=(occurrence,)) for occurrence in claimed]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(outcomes) == ["La regla alcanzó su límite de ejecuciones de hoy.", "admitted"]


# ── Q06: a lost post is not replayed once its episode no longer holds ──


@pytest.mark.parametrize("arrived", [False, True])
def test_a_lost_post_is_not_replayed_once_the_user_is_back(setup, arrived):
    first = setup.add_rule(timeout_s=60)
    second = setup.add_rule()
    decision = [d for d in setup.idle_until_ready() if d.rule_id == first.id][0]
    if arrived:
        setup.hermes.lose_next_answer = True  # Hermes admitted it; only the answer was lost
    else:
        setup.hermes.drop_next_post = True  # the post never reached Hermes
    checks, tried = [], []

    def user_back_then_try_another():
        if not checks:
            setup.tracker.iris_interaction()
        occurrence = setup.store.get_occurrence(decision.occurrence_id)
        if occurrence.state == "stopping" and not tried:
            tried.append(True)
            # While its fate is unknown it keeps Iris' place: another rule cannot be admitted.
            other, _ = setup.store.record_candidate(second.id, 1, "manual:other")
            other = setup.store.claim(other.id, "core-1")
            with pytest.raises(AdmissionRefused, match="otra automatización"):
                setup.store.admit(other.id, other.claim.token, body={}, idempotency_key="k-other", context={},
                                  max_parallel=1, quota_since=0)
        checks.append(occurrence.state)

    setup.hooks.append(user_back_then_try_another)
    assert run(setup.executor.dispatch(decision)) == "failed"
    assert len(setup.hermes.posts) == 1  # never replayed after the user came back
    assert len(setup.hermes.runs) == (1 if arrived else 0)
    text = setup.result_text(decision.occurrence_id)
    assert "No se reenvía" in text and "condición" in text and UNKNOWN_END in text
    assert "stopping" in checks


def test_a_replay_rechecks_after_its_own_slow_question(setup):
    setup.add_rule(timeout_s=60)
    decision = setup.ready_now()
    setup.hermes.drop_next_post = True
    asked = []

    def revoke_on_the_replays_question(profile):
        asked.append(profile)
        if len(asked) == 2:  # 1: admission, 2: the replay's own capabilities question
            setup.permission.verdict = "deny"

    setup.hermes.on_idempotency = revoke_on_the_replays_question
    assert run(setup.executor.dispatch(decision)) == "failed"
    assert len(setup.hermes.posts) == 1 and setup.hermes.runs == {}
    assert "Capability Center" in setup.result_text(decision.occurrence_id)


# ── Q07: following, replaying and stopping use the context it was admitted in ──


def test_editing_the_rule_does_not_move_the_profile_or_deadline_of_a_sent_run(setup):
    rule = setup.add_rule(profile="coder", timeout_s=60)
    setup.hermes.script = ["running"]
    decision = setup.ready_now()

    def edit_once():
        current = setup.store.get_rule(rule.id)
        if current.revision == 1:
            setup.store.update_rule(rule.id, expected_revision=1,
                                    execution=Execution(prompt="Otra", profile="otro", timeout_s=7200))

    setup.hooks.append(edit_once)
    occurrence_created = setup.store.get_occurrence(decision.occurrence_id).created_at
    assert run(setup.executor.dispatch(decision)) == "failed"
    assert set(setup.hermes.status_profiles) == {"coder"} and setup.hermes.stop_profiles == ["coder"]
    assert setup.clocks.wall < occurrence_created + 60 + 30  # its own 60 s, not the edited 7200 s
    assert "Hermes la detuvo" in setup.result_text(decision.occurrence_id)


def test_after_a_restart_an_edited_rule_does_not_move_its_sent_run(setup):
    rule = setup.add_rule(profile="coder", timeout_s=60)
    setup.hermes.script = ["running"]
    decision = setup.ready_now()

    async def edit_then_core_stops(seconds):
        setup.store.update_rule(rule.id, expected_revision=1,
                                execution=Execution(prompt="Otra", profile="otro", timeout_s=7200))
        raise asyncio.CancelledError

    setup.executor._sleep = edit_then_core_stops
    with pytest.raises(asyncio.CancelledError):
        run(setup.executor.dispatch(decision))
    created = setup.store.get_occurrence(decision.occurrence_id).created_at
    restarted = AutomationExecutor(setup.store, setup.engine, setup.hermes, permission=lambda: "allow",
                                   clock=lambda: setup.clocks.wall, poll_s=5, sleep=setup.wait)
    assert run(restarted.reconcile()) == ["failed"]
    assert set(setup.hermes.status_profiles) == {"coder"} and setup.hermes.stop_profiles == ["coder"]
    assert setup.clocks.wall < created + 60 + 30  # its own deadline, not the edited one
    assert "Hermes la detuvo" in setup.result_text(decision.occurrence_id)


def test_cancelling_after_an_edit_stops_the_run_in_its_own_profile(setup):
    rule = setup.add_rule(profile="coder")
    setup.hermes.script = ["running"]
    decision = setup.ready_now()
    answers = []

    def edit_then_cancel():
        if setup.store.get_rule(rule.id).revision == 1:
            setup.store.update_rule(rule.id, expected_revision=1, execution=Execution(prompt="Otra", profile="otro"))
        elif not answers and setup.store.get_occurrence(decision.occurrence_id).run_id:
            answers.append(setup.executor.cancel(decision.occurrence_id))

    setup.hooks.append(edit_then_cancel)
    assert run(setup.executor.dispatch(decision)) == "failed"
    assert answers == ["stop_requested"] and setup.hermes.stop_profiles[0] == "coder"
    assert setup.result_text(decision.occurrence_id) == "Cancelada por el usuario; Hermes confirmó que se detuvo."


@pytest.mark.parametrize("change", ["credential", "endpoint"])
def test_a_lost_post_is_not_replayed_under_another_credential_or_endpoint(setup, change):
    setup.add_rule(timeout_s=60)
    decision = setup.ready_now()
    setup.hermes.lose_next_answer = True
    setup.hooks.append(lambda: setup.hermes.scopes.update(default=f"scope-default-{change}-rotated"))
    assert run(setup.executor.dispatch(decision)) == "failed"
    # Replaying in the new scope would have admitted a second run with the same key and body.
    assert len(setup.hermes.posts) == 1 and len(setup.hermes.runs) == 1
    text = setup.result_text(decision.occurrence_id)
    assert "No se reenvía" in text and "credencial" in text


# ── Codex's recheck of qa11 (T02–T04) ──


@pytest.mark.parametrize("changed_after, replayed", [(3, False), (4, True)])
def test_a_destination_change_during_a_replay_never_makes_a_second_run(setup, changed_after, replayed):
    # T02. Resolves: 1 dispatch, 2 admission; then the replay: 3 its recorded destination, 4 right
    # before posting. A change after 3 is caught; after 4 the post still goes where it was checked.
    setup.add_rule(timeout_s=60)
    decision = setup.ready_now()
    setup.hermes.lose_next_answer = True

    def rotate(count):
        if count == changed_after:
            setup.hermes.scopes["default"] = "scope-default-rotated"

    setup.hermes.on_resolve = rotate
    assert run(setup.executor.dispatch(decision)) == "failed"
    assert len(setup.hermes.runs) == 1  # never a second run under the rotated credential
    assert len(setup.hermes.posts) == (2 if replayed else 1)
    text = setup.result_text(decision.occurrence_id)
    if replayed:  # the run is known, but Iris cannot follow it under another destination
        assert "Cambió el Hermes o la credencial" in text and "Puede seguir ejecutándose" in text
    else:
        assert "No se reenvía" in text and "credencial" in text


def test_a_replay_whose_deadline_passes_during_the_slow_question_is_not_posted(setup):
    # T03, Codex's case: posts that never arrive, deadline 30 s, and at about 25 s the
    # capabilities question takes 8 s. Nothing may be posted after the deadline.
    setup.add_rule(timeout_s=30)
    decision = setup.ready_now()
    created = setup.store.get_occurrence(decision.occurrence_id).created_at
    post_times = []
    setup.hermes.on_submit = lambda: post_times.append(setup.clocks.wall - created)
    setup.hermes.drop_next_post = True
    setup.hooks.append(lambda: setattr(setup.hermes, "drop_next_post", setup.clocks.wall - created < 25))

    def slow_question(profile):
        if setup.clocks.wall - created >= 25:
            setup.clocks.step(8)

    setup.hermes.on_idempotency = slow_question
    assert run(setup.executor.dispatch(decision)) == "failed"
    assert post_times and max(post_times) < 30 and setup.hermes.runs == {}
    assert "plazo" in setup.result_text(decision.occurrence_id)


def test_a_quick_replay_before_the_deadline_still_settles_the_task(setup):
    setup.add_rule(timeout_s=30)
    decision = setup.ready_now()
    setup.hermes.drop_next_post = True
    assert run(setup.executor.dispatch(decision)) == "completed"
    assert len(setup.hermes.posts) == 2 and len(setup.hermes.runs) == 1


@pytest.mark.parametrize("answer", ["lost", "accepted", "rejected"])
def test_cancelling_while_the_post_is_in_flight_keeps_it_followed(setup, answer):
    # T04: the cancellation lands while the post waits for its answer; the endpoint asks to
    # reconcile, which finds it busy. The late answer must not undo the cancellation or end
    # the follow-up: the occurrence reaches an honest end and frees its place.
    setup.add_rule(timeout_s=60)
    decision = setup.ready_now()
    if answer == "lost":
        setup.hermes.lose_next_answer = True  # Hermes admitted it; the answer is lost
    elif answer == "rejected":
        setup.hermes.reject_next_post = True
    seen = {}

    def cancel_while_posting():
        if not seen:
            seen["cancel"] = setup.executor.cancel(decision.occurrence_id)
            seen["reconcile"] = asyncio.run(setup.executor.reconcile())

    setup.hermes.on_submit = cancel_while_posting
    final = run(setup.executor.dispatch(decision))
    assert seen == {"cancel": "not_resent", "reconcile": []}
    assert final == "failed" and len(setup.hermes.posts) == 1
    text = setup.result_text(decision.occurrence_id)
    if answer == "lost":
        assert text.startswith("Cancelación pedida por el usuario.") and UNKNOWN_END in text
    elif answer == "accepted":  # the run became known after the cancellation: asked to stop, confirmed
        assert setup.hermes.stops and text == "Cancelada por el usuario; Hermes confirmó que se detuvo."
    else:
        assert text == "HTTP 401"
    setup.hermes.on_submit = None
    setup.hermes.script = ["completed"]
    assert run(setup.executor.dispatch(setup.new_episode())) == "completed"  # the place is free again


def test_cancelling_during_the_replays_slow_question_never_posts_again(setup):
    # S02, Codex's case: the first post never arrived (no run); while the replay asks for
    # capabilities the user cancels, and the endpoint's reconciliation finds it busy.
    setup.add_rule(timeout_s=60)
    decision = setup.ready_now()
    setup.hermes.drop_next_post = True
    asked, seen = [], {}

    def cancel_during_the_replays_question(profile):
        asked.append(profile)
        if len(asked) == 2:  # 1: admission, 2: the replay's own question
            seen["cancel"] = setup.executor.cancel(decision.occurrence_id)
            seen["reconcile"] = asyncio.run(setup.executor.reconcile())

    setup.hermes.on_idempotency = cancel_during_the_replays_question
    assert run(setup.executor.dispatch(decision)) == "failed"
    assert seen == {"cancel": "not_resent", "reconcile": []}
    assert len(setup.hermes.posts) == 1 and setup.hermes.runs == {}  # one post in total, no run admitted
    text = setup.result_text(decision.occurrence_id)
    assert text.startswith("Cancelación pedida por el usuario.") and UNKNOWN_END in text


def test_the_runtime_takes_up_sent_work_nobody_is_settling(tmp_path):
    # T04: if a settler stops (an error, a crash), the runtime's periodic pass gives the
    # occurrence an owner again; it never sends anything new.
    import time as real_time

    from core.automation_api import RECONCILE_EVERY_S, AutomationRuntime

    hermes = FakeHermes()
    runtime = AutomationRuntime(home=tmp_path, transport=hermes, permission=lambda: "allow",
                                profile_problem=lambda profile: "", local_surface_open=lambda: True)

    async def scenario():
        store = runtime.store(create=True)
        while runtime._tasks:  # the reconciliation started by opening the store (nothing to settle yet)
            await asyncio.gather(*list(runtime._tasks))
            await asyncio.sleep(0)
        now = real_time.time()
        rule = store.create_rule(AutomationRule.model_validate({
            "id": new_id(), "title": "Regla sintética", "enabled": True,
            "trigger": {"source": "iris_idle", "threshold_s": 300},
            "execution": {"prompt": "Tarea sintética"}, "created_at": now, "updated_at": now}))
        occurrence, _ = store.record_candidate(rule.id, 1, "manual:1")
        claimed = store.claim(occurrence.id, "core-1")
        store.admit(occurrence.id, claimed.claim.token, body={"input": "x"}, idempotency_key="k-1",
                    context={"rule_revision": 1, "profile": "default", "scope": "scope-default",
                             "deadline": now - 1, "timeout_s": 900, "provider": "", "model": ""},
                    max_parallel=1, quota_since=0)
        store.advance(occurrence.id, "stopping", error="Cancelación pedida por el usuario.")
        clock = {"now": 1000.0}
        runtime._monotonic = lambda: clock["now"]
        runtime._last_reconcile = 1000.0
        runtime._tick()  # too soon: nothing is spawned
        assert not runtime._tasks and store.get_occurrence(occurrence.id).state == "stopping"
        clock["now"] += RECONCILE_EVERY_S
        runtime._tick()
        await asyncio.gather(*list(runtime._tasks))
        return store.get_occurrence(occurrence.id)

    settled = asyncio.run(scenario())
    assert settled.state == "failed" and hermes.posts == []


def test_a_stop_hermes_does_not_accept_keeps_the_place_and_is_never_called_stopped(setup):
    first = setup.add_rule(timeout_s=60)
    second = setup.add_rule()
    setup.hermes.script = ["running"]
    setup.hermes.stop_works = False
    decision = [d for d in setup.idle_until_ready() if d.rule_id == first.id][0]
    answers = []

    def cancel_then_check_the_place():
        occurrence = setup.store.get_occurrence(decision.occurrence_id)
        if not answers and occurrence.run_id:
            answers.append(setup.executor.cancel(decision.occurrence_id))
            assert setup.store.get_occurrence(decision.occurrence_id).state == "stopping"
            other, _ = setup.store.record_candidate(second.id, 1, "manual:other")
            other = setup.store.claim(other.id, "core-1")
            with pytest.raises(AdmissionRefused, match="otra automatización"):
                setup.store.admit(other.id, other.claim.token, body={}, idempotency_key="k-other", context={},
                                  max_parallel=1, quota_since=0)

    setup.hooks.append(cancel_then_check_the_place)
    assert run(setup.executor.dispatch(decision)) == "failed"
    assert answers == ["stop_not_accepted"]
    text = setup.result_text(decision.occurrence_id)
    assert "no aceptó detenerla" in text and "puede seguir ejecutándose" in text


def test_a_run_that_finishes_after_an_unaccepted_stop_keeps_its_real_result(setup):
    setup.add_rule()
    setup.hermes.script = ["running", "running", "running", "completed"]
    setup.hermes.stop_works = False
    decision = setup.ready_now()
    answers = []

    def cancel_once():
        if not answers and setup.store.get_occurrence(decision.occurrence_id).run_id:
            answers.append(setup.executor.cancel(decision.occurrence_id))

    setup.hooks.append(cancel_once)
    assert run(setup.executor.dispatch(decision)) == "completed"  # Hermes finished it: that is the truth
    assert answers == ["stop_not_accepted"]
    assert setup.result_text(decision.occurrence_id) == "Resumen sintético listo."


def test_a_restart_follows_a_stopping_run_with_its_context(setup):
    setup.add_rule(profile="coder")
    setup.hermes.script = ["running"]
    decision = setup.ready_now()
    answers = []

    async def cancel_then_core_stops(seconds):
        if not answers:
            answers.append(setup.executor.cancel(decision.occurrence_id))
        raise asyncio.CancelledError

    setup.executor._sleep = cancel_then_core_stops
    with pytest.raises(asyncio.CancelledError):
        run(setup.executor.dispatch(decision))
    assert setup.store.get_occurrence(decision.occurrence_id).state == "stopping"
    restarted = AutomationExecutor(setup.store, setup.engine, setup.hermes, permission=lambda: "allow",
                                   clock=lambda: setup.clocks.wall, poll_s=5, sleep=setup.wait)
    assert run(restarted.reconcile()) == ["failed"]
    assert set(setup.hermes.status_profiles) == {"coder"}
    assert setup.result_text(decision.occurrence_id) == "Cancelada por el usuario; Hermes confirmó que se detuvo."


def test_cancelling_a_request_whose_run_is_unknown_never_claims_it_stopped(setup):
    setup.add_rule(timeout_s=60)
    decision = setup.ready_now()
    setup.hermes.lost_answers = 1_000
    answers = []

    def user_cancels():
        if not answers:
            answers.append(setup.executor.cancel(decision.occurrence_id))

    setup.hooks.append(user_cancels)
    assert run(setup.executor.dispatch(decision)) == "failed"
    assert answers == ["not_resent"] and len(setup.hermes.posts) == 1
    text = setup.result_text(decision.occurrence_id)
    assert text.startswith("Cancelación pedida por el usuario.") and UNKNOWN_END in text


# ── Transport ─────────────────────────────────────────────────────────────


def test_the_transport_destination_is_a_fingerprint_of_endpoint_profile_and_credential():
    identity = {"base": "http://127.0.0.1:8642", "key": "clave-default-secreta"}
    route = {"coder": ("/p/coder", "clave-coder-secreta", ""), "roto": ("", None, "perfil no disponible")}
    transport = HermesRunsTransport(lambda *a, **k: {}, lambda profile: route.get(profile, ("", None, "")),
                                    lambda: (identity["base"], identity["key"]))
    default, coder = transport.resolve("default"), transport.resolve("coder")
    assert len(default.scope) == 64 and default.scope != coder.scope
    assert transport.resolve("default").scope == default.scope
    assert "secreta" not in default.scope + coder.scope + repr(default) + repr(coder)  # never the key itself
    assert transport.resolve("roto") is None
    identity["key"] = "clave-rotada"
    assert transport.resolve("default").scope != default.scope and transport.resolve("coder").scope == coder.scope
    identity.update(key="clave-default-secreta", base="http://127.0.0.1:9999")
    assert transport.resolve("default").scope != default.scope


def test_an_unreadable_identity_makes_the_destination_unknown():
    def unsafe():
        raise ValueError("Hermes remoto requiere HTTPS")

    transport = HermesRunsTransport(lambda *a, **k: {}, lambda profile: ("", None, ""), unsafe)
    assert transport.resolve("default") is None


def test_every_request_goes_to_the_destination_it_was_resolved_to():
    # T02: the configuration changes after resolving; the request still carries the resolved values.
    calls, identity = [], {"base": "http://127.0.0.1:8642", "key": "clave-original"}

    def request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return {"ok": True, "status": 202, "data": {"run_id": "run_1", "status": "running"}}

    transport = HermesRunsTransport(request, lambda profile: ("", None, ""),
                                    lambda: (identity["base"], identity["key"]))
    destination = transport.resolve("default")
    identity.update(base="http://otro-hermes:8642", key="clave-rotada")
    transport.submit(destination, {"input": "x"}, "k")
    transport.status(destination, "run_1")
    transport.stop(destination, "run_1")
    transport.idempotency(destination)
    assert {(kwargs["base_url"], kwargs["token"]) for _, _, kwargs in calls} == {("http://127.0.0.1:8642",
                                                                                   "clave-original")}


def test_the_transport_sends_the_key_and_classifies_answers():
    calls = []
    answers = iter([
        {"ok": True, "status": 202, "data": {"run_id": "run_1", "status": "started", "replayed": False}},
        {"ok": False, "status": None, "error": "timed out"},
        {"ok": False, "status": 503, "error": "busy"},
        {"ok": False, "status": 409, "error": "idempotency_key_conflict"},
        {"ok": True, "status": 200, "data": {"status": "completed", "output": "hola"}},
        {"ok": False, "status": None, "error": "down"},
        {"ok": True, "status": 200, "data": {"features": {"runs_idempotency": {
            "supported": True, "durable": True, "retention_seconds": 86400}}}},
        {"ok": True, "status": 200, "data": {"features": {}}},
        {"ok": False, "status": None, "error": "down"},
    ])

    def request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return next(answers)

    transport = HermesRunsTransport(
        request, lambda profile: ("/p/coder", "key-coder", "") if profile == "coder" else ("", None, ""),
        lambda: ("http://127.0.0.1:8642", "key-default"))
    default, coder = transport.resolve("default"), transport.resolve("coder")
    assert transport.submit(default, {"input": "x"}, "iris-automation-1") == {"outcome": "accepted",
                                                                              "run_id": "run_1", "replayed": False}
    assert calls[0][1] == "/v1/runs" and calls[0][2]["headers"] == {"Idempotency-Key": "iris-automation-1"}
    assert calls[0][2]["token"] == "key-default" and calls[0][2]["base_url"] == "http://127.0.0.1:8642"
    assert transport.submit(coder, {"input": "x"}, "k")["outcome"] == "lost"
    assert calls[1][1] == "/p/coder/v1/runs" and calls[1][2]["token"] == "key-coder"
    assert transport.submit(default, {"input": "x"}, "k")["outcome"] == "lost"  # 5xx: Hermes may hold it
    assert transport.submit(default, {"input": "x"}, "k")["outcome"] == "rejected"
    assert transport.status(default, "run_1") == {"status": "completed", "output": "hola", "error": ""}
    assert transport.status(default, "run_1") == {"status": "lost"}
    assert transport.idempotency(coder) == {"supported": True, "durable": True, "retention_s": 86400.0}
    assert calls[-1][1] == "/p/coder/v1/capabilities" and calls[-1][2]["token"] == "key-coder"
    assert transport.idempotency(default) == {}  # an older Hermes that does not say
    assert transport.idempotency(default) == {}


# ── API ───────────────────────────────────────────────────────────────────


@pytest.fixture
def api(tmp_path):
    from core.automation_api import AutomationRuntime

    permission = SimpleNamespace(verdict="ask")
    runtime = AutomationRuntime(home=tmp_path, transport=FakeHermes(), permission=lambda: permission.verdict,
                                profile_problem=lambda profile: "", local_surface_open=lambda: True)
    app = FastAPI()
    app.include_router(automation_router(lambda: runtime, permission=lambda: permission.verdict))
    return SimpleNamespace(client=TestClient(app), runtime=runtime, permission=permission, home=tmp_path)


RULE = {"title": "Investigar al irme", "trigger": {"source": "iris_idle", "threshold_s": 300},
        "execution": {"prompt": "Resume lo que te encargué (sintético)."}}


def test_nothing_is_created_until_a_rule_is(api):
    body = api.client.get("/api/automations").json()
    assert body["rules"] == [] and body["permission"] == "ask"
    assert not (api.home / "automations").exists()


def test_rules_start_paused_and_enabling_needs_confirmation_and_a_ready_source(api):
    created = api.client.post("/api/automations", json=RULE)
    assert created.status_code == 405  # rules live under /rules
    rule = api.client.post("/api/automations/rules", json=RULE).json()
    assert rule["enabled"] is False and rule["revision"] == 1
    no_confirm = api.client.put(f"/api/automations/rules/{rule['id']}", json={"expected_revision": 1, "enabled": True})
    assert no_confirm.status_code == 400
    enabled = api.client.put(f"/api/automations/rules/{rule['id']}",
                             json={"expected_revision": 1, "enabled": True, "confirm": True}).json()
    assert enabled["enabled"] is True and enabled["revision"] == 2 and "Permitir" in enabled["warning"]
    stale = api.client.put(f"/api/automations/rules/{rule['id']}", json={"expected_revision": 1, "title": "x"})
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "revision_conflict"
    camera = api.client.post("/api/automations/rules", json={**RULE, "trigger": {"source": "observed_absence",
                                                                               "threshold_s": 300}}).json()
    refused = api.client.put(f"/api/automations/rules/{camera['id']}",
                             json={"expected_revision": 1, "enabled": True, "confirm": True})
    assert refused.status_code == 409 and "F03" in refused.json()["detail"]["message"]
    assert refused.json()["detail"]["code"] == "rule_not_ready"
    voice = api.client.post("/api/automations/rules", json={**RULE, "delivery": {"voice": True}}).json()
    assert voice["problem"] == ""  # F08b exists; voice remains globally opt-in.
    assert api.runtime.presence_preferences().return_voice is False
    spoken = api.client.put(f"/api/automations/rules/{voice['id']}",
                            json={"expected_revision": 1, "enabled": True, "confirm": True})
    assert spoken.status_code == 200 and spoken.json()["enabled"] is True
    assert api.runtime.presence_preferences().return_voice is False
    tools = api.client.post("/api/automations/rules", json={**RULE, "execution": {
        "prompt": "p", "tool_allowlist": ["web"]}}).json()
    assert "herramientas" in tools["problem"]
    invalid = api.client.post("/api/automations/rules", json={**RULE, "trigger": {"source": "cron", "threshold_s": 1}})
    assert invalid.status_code == 400
    listed = api.client.get("/api/automations").json()
    assert len(listed["rules"]) == 4 and listed["occurrences"] == []


# ── Core wiring ───────────────────────────────────────────────────────────


def test_only_local_iris_surfaces_count_as_using_iris(core, monkeypatch):
    server = core.server
    interactions = []
    monkeypatch.setattr(server, "_automation_runtime", SimpleNamespace(interaction=lambda: interactions.append(1)))
    assert server._local_iris_surface_open() is False  # Iris closed: iris_idle is unknown
    phone = core.surface("192.168.1.30")
    assert server._local_iris_surface_open() is False
    server._note_iris_interaction(phone)
    assert interactions == []  # a LAN chat is not this PC's user using Iris
    face = core.surface()
    assert server._local_iris_surface_open() is True
    server._note_iris_interaction(face)
    assert interactions == [1]


def test_automations_run_only_with_an_explicit_allow(core):
    from core.capability_permissions import PRESETS

    assert core.server._automation_permission() == "ask"
    assert all(policy["automations.run"] != "allow" for policy in PRESETS.values())  # no preset turns it on
    core.store.set_mode("automations.run", "allow")
    assert core.server._automation_permission() == "allow"
    core.store.set_mode("automations.run", "deny")
    assert core.server._automation_permission() == "deny"


def test_a_lan_client_cannot_read_or_change_automations():
    from unittest.mock import patch

    from core.config import IRISConfig
    from core.websocket_server import app

    cfg = IRISConfig(lan_access=True, websocket_host="0.0.0.0", lan_pairing_token="pair-secret")
    with (
        patch("core.websocket_server.IRISConfig.load", return_value=cfg),
        TestClient(app, base_url="http://192.168.1.10:8765", client=("192.168.1.25", 50000)) as client,
    ):
        client.get("/api/status?pair=pair-secret", follow_redirects=False)
        read = client.get("/api/automations")
        write = client.post("/api/automations/rules", json=RULE, headers={"Origin": "http://192.168.1.10:8765"})
    assert read.status_code == 403 and write.status_code == 403
