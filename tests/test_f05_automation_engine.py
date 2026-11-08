"""F05: the condition engine over F04's store and signals, with fake clocks only."""

from __future__ import annotations

import asyncio

import pytest

from core.automation_contracts import AutomationRule, new_id
from core.automation_engine import AutomationEngine, run_loop
from core.automation_store import AutomationStore
from core.presence_state import PresenceTracker


class Clocks:
    def __init__(self):
        self.mono, self.wall = 1000.0, 1_800_000_000.0

    def step(self, seconds, *, wall_extra=0.0):
        self.mono += seconds
        self.wall += seconds + wall_extra


@pytest.fixture
def world(tmp_path):
    clocks = Clocks()
    store = AutomationStore(tmp_path / "automations.sqlite3", clock=lambda: clocks.wall)
    tracker = PresenceTracker(monotonic=lambda: clocks.mono, wall=lambda: clocks.wall, run_id="run1")
    engine = AutomationEngine(store, tracker, owner="core-1", lease_s=60, clock=lambda: clocks.wall)

    def add_rule(source="iris_idle", threshold=300, **trigger):
        rule = AutomationRule.model_validate({
            "id": new_id(), "title": "Regla sintética", "enabled": True,
            "trigger": {"source": source, "threshold_s": threshold, **trigger},
            "execution": {"prompt": "Tarea sintética"}, "created_at": clocks.wall, "updated_at": clocks.wall})
        return store.create_rule(rule)

    def idle_for(seconds, step=5):
        for _ in range(int(seconds // step)):
            clocks.step(step)
            tracker.iris_tick(local_surface_open=True)

    return store, tracker, engine, clocks, add_rule, idle_for


def outcomes(engine):
    return [d.outcome for d in engine.evaluate()]


def send(store, decision):
    """What F06 does with a ready occurrence: admit it (claimed → submitted)."""
    return store.admit(decision.occurrence_id, decision.claim_token, body={"input": "x"},
                       idempotency_key="k-" + decision.occurrence_id, context={}, max_parallel=5, quota_since=0)


def test_299_seconds_wait_and_300_fire_once(world):
    store, tracker, engine, clocks, add_rule, idle_for = world
    add_rule()
    tracker.iris_tick(local_surface_open=True)
    idle_for(295)
    clocks.step(4)
    tracker.iris_tick(local_surface_open=True)  # 299 s observed
    assert outcomes(engine) == ["waiting"]
    clocks.step(1)
    tracker.iris_tick(local_surface_open=True)  # 300 s
    [ready] = engine.evaluate()
    assert ready.outcome == "ready" and ready.claim_token
    send(store, ready)
    idle_for(60)
    assert outcomes(engine) == ["already"]  # the same episode never fires again once sent
    assert len(store.occurrences()) == 1


def test_unknown_never_fires_and_a_closed_iris_is_unknown(world):
    store, tracker, engine, clocks, add_rule, idle_for = world
    add_rule()
    assert outcomes(engine) == ["unknown"]
    tracker.iris_tick(local_surface_open=True)
    idle_for(400)
    tracker.iris_tick(local_surface_open=False)
    assert outcomes(engine) == ["unknown"] and store.occurrences() == []


def test_camera_off_is_not_absence(world):
    store, tracker, engine, clocks, add_rule, idle_for = world
    add_rule(source="observed_absence", threshold=60)
    tracker.camera_sample(present=True, confidence=0.9, surface="face")
    for _ in range(20):
        clocks.step(2)
        tracker.camera_sample(present=False, confidence=0.9, surface="face")
    tracker.camera_unavailable("camera_off")
    clocks.step(600)
    assert outcomes(engine) == ["unknown"] and store.occurrences() == []


def test_observed_absence_fires_from_its_own_source_only(world):
    store, tracker, engine, clocks, add_rule, idle_for = world
    absence = add_rule(source="observed_absence", threshold=60)
    tracker.iris_tick(local_surface_open=True)
    idle_for(400)  # Iris idle is not camera absence
    assert outcomes(engine) == ["unknown"]
    tracker.camera_sample(present=True, confidence=0.9, surface="face")
    for _ in range(31):
        clocks.step(2)
        tracker.camera_sample(present=False, confidence=0.9, surface="face")
    [ready] = engine.evaluate()
    assert ready.outcome == "ready" and ready.rule_id == absence.id


def test_a_return_just_before_the_claim_cancels_the_occurrence(world, monkeypatch):
    store, tracker, engine, clocks, add_rule, idle_for = world
    add_rule()
    tracker.iris_tick(local_surface_open=True)
    idle_for(300)
    real_claim = store.claim

    def claim_after_the_user_came_back(*args, **kwargs):
        tracker.iris_interaction()  # the user touched Iris while the claim was being taken
        return real_claim(*args, **kwargs)

    monkeypatch.setattr(store, "claim", claim_after_the_user_came_back)
    [returned] = engine.evaluate()
    assert returned.outcome == "returned"
    assert [o.state for o in store.occurrences()] == ["cancelled"]


def test_an_edited_rule_does_not_fire_again_for_the_same_episode(world):
    store, tracker, engine, clocks, add_rule, idle_for = world
    rule = add_rule()
    tracker.iris_tick(local_surface_open=True)
    idle_for(300)
    [ready] = engine.evaluate()
    store.update_rule(rule.id, expected_revision=1, title="Editada")
    assert [o.state for o in store.occurrences()] == ["cancelled"]  # unsent: cancelled by the edit
    assert outcomes(engine) == ["already"]  # revision 2 does not take the same episode
    tracker.iris_interaction()
    clocks.step(5)
    tracker.iris_tick(local_surface_open=True)
    idle_for(300)
    assert outcomes(engine) == ["ready"]  # a new episode does


def test_two_engines_one_occurrence(world, tmp_path):
    store, tracker, engine, clocks, add_rule, idle_for = world
    add_rule()
    tracker.iris_tick(local_surface_open=True)
    idle_for(300)
    other_store = AutomationStore(store.path, clock=lambda: clocks.wall)
    other = AutomationEngine(other_store, tracker, owner="core-2", clock=lambda: clocks.wall)
    first, second = engine.evaluate()[0], other.evaluate()[0]
    assert sorted([first.outcome, second.outcome]) == ["claimed_elsewhere", "ready"]  # the episode is taken
    assert len(store.occurrences()) == 1


def test_an_expired_lease_is_taken_again_by_another_engine(world):
    # Q03: through the engine itself, not a manual store.claim().
    store, tracker, engine, clocks, add_rule, idle_for = world
    add_rule()
    tracker.iris_tick(local_surface_open=True)
    idle_for(300)
    [ready] = engine.evaluate()  # claimed, then its executor never sent it
    first = store.get_occurrence(ready.occurrence_id)
    other = AutomationEngine(store, tracker, owner="core-2", lease_s=60, clock=lambda: clocks.wall)
    idle_for(10)
    assert outcomes(other) == ["claimed_elsewhere"]  # a live lease is respected
    idle_for(55)
    [again] = other.evaluate()
    assert again.outcome == "ready" and again.occurrence_id == ready.occurrence_id
    retaken = store.get_occurrence(again.occurrence_id)
    assert retaken.claim.owner == "core-2" and retaken.correlation_id == first.correlation_id
    assert len(store.occurrences()) == 1


@pytest.mark.parametrize("how", ["recorded", "released"])
def test_an_unsent_candidate_of_this_episode_is_claimed_by_the_engine(world, how):
    store, tracker, engine, clocks, add_rule, idle_for = world
    rule = add_rule()
    tracker.iris_tick(local_surface_open=True)
    idle_for(300)
    if how == "recorded":  # recorded, then the process stopped before claiming it
        occurrence, created = store.record_candidate(rule.id, 1, tracker.state("iris_idle").episode)
        assert created
        expected_id = occurrence.id
    else:  # claimed and given back without sending
        [ready] = engine.evaluate()
        store.release(ready.occurrence_id, ready.claim_token)
        expected_id = ready.occurrence_id
    [again] = engine.evaluate()
    assert again.outcome == "ready" and again.occurrence_id == expected_id
    assert len(store.occurrences()) == 1


def test_a_failure_between_recording_and_claiming_is_recovered_by_the_next_pass(world, monkeypatch):
    import sqlite3

    store, tracker, engine, clocks, add_rule, idle_for = world
    add_rule()
    tracker.iris_tick(local_surface_open=True)
    idle_for(300)
    real_claim = store.claim

    def locked_once(*args, **kwargs):
        monkeypatch.setattr(store, "claim", real_claim)
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(store, "claim", locked_once)
    with pytest.raises(sqlite3.OperationalError):
        engine.evaluate()
    [candidate] = store.occurrences()
    assert candidate.state == "candidate"
    [again] = engine.evaluate()
    assert again.outcome == "ready" and again.occurrence_id == candidate.id


def test_a_recovered_occurrence_is_rechecked_and_cancelled_if_the_condition_went(world, monkeypatch):
    store, tracker, engine, clocks, add_rule, idle_for = world
    add_rule()
    tracker.iris_tick(local_surface_open=True)
    idle_for(300)
    [ready] = engine.evaluate()
    idle_for(65)  # its lease expired unsent
    real_claim = store.claim

    def claim_then_the_user_returns(*args, **kwargs):
        claimed = real_claim(*args, **kwargs)
        tracker.iris_interaction()
        return claimed

    monkeypatch.setattr(store, "claim", claim_then_the_user_returns)
    [returned] = engine.evaluate()
    assert returned.outcome == "returned" and returned.occurrence_id == ready.occurrence_id
    assert store.get_occurrence(ready.occurrence_id).state == "cancelled"


def test_two_engines_on_a_recovered_occurrence_one_wins(world):
    store, tracker, engine, clocks, add_rule, idle_for = world
    add_rule()
    tracker.iris_tick(local_surface_open=True)
    idle_for(300)
    engine.evaluate()
    idle_for(65)
    a = AutomationEngine(AutomationStore(store.path, clock=lambda: clocks.wall), tracker, owner="core-a",
                         clock=lambda: clocks.wall)
    b = AutomationEngine(AutomationStore(store.path, clock=lambda: clocks.wall), tracker, owner="core-b",
                         clock=lambda: clocks.wall)
    assert sorted([a.evaluate()[0].outcome, b.evaluate()[0].outcome]) == ["claimed_elsewhere", "ready"]
    assert len(store.occurrences()) == 1


def test_sent_work_is_never_taken_again(world):
    store, tracker, engine, clocks, add_rule, idle_for = world
    add_rule()
    tracker.iris_tick(local_surface_open=True)
    idle_for(300)
    [ready] = engine.evaluate()
    send(store, ready)
    idle_for(65)
    other = AutomationEngine(store, tracker, owner="core-2", clock=lambda: clocks.wall)
    assert outcomes(other) == ["already"]
    assert store.claim(ready.occurrence_id, "core-2") is None


def test_a_lost_claim_does_not_keep_the_rule_busy_in_later_episodes(world):
    store, tracker, engine, clocks, add_rule, idle_for = world
    add_rule()
    tracker.iris_tick(local_surface_open=True)
    idle_for(300)
    [lost] = engine.evaluate()  # claimed, its executor vanished
    tracker.iris_interaction()  # that episode ends
    clocks.step(5)
    tracker.iris_tick(local_surface_open=True)
    idle_for(300)
    [ready] = engine.evaluate()
    assert ready.outcome == "ready" and ready.occurrence_id != lost.occurrence_id
    old = store.get_occurrence(lost.occurrence_id)
    assert old.state == "cancelled" and "episodio" in old.error


def test_a_restart_cancels_unsent_work_and_needs_a_full_new_threshold(world):
    store, tracker, engine, clocks, add_rule, idle_for = world
    add_rule()
    tracker.iris_tick(local_surface_open=True)
    idle_for(300)
    engine.evaluate()
    restarted_tracker = PresenceTracker(monotonic=lambda: clocks.mono, wall=lambda: clocks.wall, run_id="run2")
    restarted = AutomationEngine(store, restarted_tracker, owner="core-1", clock=lambda: clocks.wall)
    assert restarted.recover() == 1
    assert [o.state for o in store.occurrences()] == ["cancelled"]
    restarted_tracker.iris_tick(local_surface_open=True)
    assert outcomes(restarted) == ["waiting"]  # no time carried over


def test_suspension_and_clock_jumps_never_complete_the_threshold(world):
    store, tracker, engine, clocks, add_rule, idle_for = world
    add_rule()
    tracker.iris_tick(local_surface_open=True)
    idle_for(200)
    clocks.step(3600)  # suspended an hour
    tracker.iris_tick(local_surface_open=True)
    clocks.step(5, wall_extra=7200)  # and the clock jumps
    tracker.iris_tick(local_surface_open=True)
    assert outcomes(engine) == ["waiting"]
    idle_for(100)
    assert outcomes(engine) == ["ready"]  # only once 300 s were really observed


def test_paused_rules_and_cooldown_are_respected(world):
    store, tracker, engine, clocks, add_rule, idle_for = world
    rule = add_rule(cooldown_s=3600)
    store.update_rule(rule.id, expected_revision=1, enabled=False)
    tracker.iris_tick(local_surface_open=True)
    idle_for(300)
    assert engine.evaluate() == []
    store.update_rule(rule.id, expected_revision=2, enabled=True)
    [ready] = engine.evaluate()
    store.advance(ready.occurrence_id, "submitted", token=ready.claim_token)
    store.advance(ready.occurrence_id, "failed", error="sintético")
    tracker.iris_interaction()
    clocks.step(5)
    tracker.iris_tick(local_surface_open=True)
    idle_for(300)
    assert outcomes(engine) == ["cooldown"]


def test_the_loop_observes_decides_and_hands_over_ready_occurrences(world):
    store, tracker, engine, clocks, add_rule, idle_for = world
    add_rule(threshold=60)
    handed = []

    async def scenario():
        stop = asyncio.Event()

        def tick():
            clocks.step(5)
            tracker.iris_tick(local_surface_open=True)

        async def on_ready(decision):
            handed.append(decision)
            stop.set()

        await asyncio.wait_for(run_loop(engine, tick=tick, on_ready=on_ready, interval=0, stop=stop), 10)

    asyncio.run(scenario())
    assert len(handed) == 1 and handed[0].outcome == "ready"
