"""Background tasks: Iris leaves long work running and keeps talking.

The runtime is driven with a fake runs transport and a controlled clock.
Core's real turn handler runs with the camera tests' sockets and agent, and the
delegated run is held by a flag the test releases, so the order of events is
fixed by the test rather than by timing.
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from core import background_tasks as bt
from core.inbox_api import InboxService
from core.inbox_contracts import from_background
from tests import test_f03b_vision_snapshot as camera

core, turn, Agent = camera.core, camera.turn, camera.Agent
ROOT = Path(__file__).resolve().parents[1]
CODE = re.compile(r"código de tarea «([^»]+)»")


class FakeRuns:
    """``HermesRunsTransport`` as Core uses it; called from worker threads."""

    def __init__(self):
        self.submits, self.stops, self.outcomes = [], [], []
        self.state = {"status": "running"}
        self.scope = "scope-1"
        self.keys = {"supported": True, "durable": True, "retention_s": 86_400.0}
        self.lock = threading.Lock()

    def idempotency(self, destination):
        return dict(self.keys)

    def resolve(self, profile):
        return SimpleNamespace(profile=profile, scope=self.scope, base_url="", prefix="", token="")

    def submit(self, destination, body, key):
        self.submits.append((body, key))
        return self.outcomes.pop(0) if self.outcomes else {"outcome": "accepted", "run_id": "run_1"}

    def status(self, destination, run_id):
        with self.lock:
            return dict(self.state)

    def set(self, **state):
        with self.lock:
            self.state = state

    def stop(self, destination, run_id):
        self.stops.append(run_id)
        return True


@pytest.fixture
def runtime(tmp_path):
    clock = SimpleNamespace(now=1000.0)
    calls = SimpleNamespace(changed=0, finished=[])
    runs = FakeRuns()

    def finished(task_id):
        calls.finished.append(task_id)

    def changed():
        calls.changed += 1

    made = bt.BackgroundTaskRuntime(bt.BackgroundTaskStore(tmp_path / "bg.db"), runs, clock=lambda: clock.now,
                                    timeout_s=600, changed=changed, finished=finished)
    return SimpleNamespace(rt=made, runs=runs, clock=clock, calls=calls, store=made.store, path=tmp_path / "bg.db")


def offer(text="Busca las noticias más importantes de hoy"):
    return bt.LaunchOffers().open(epoch=1, route_token="default:0", profile="", session_id="s", request=text)


def launch(env, title="revisar noticias", task="Busca las noticias más importantes de hoy en español."):
    return asyncio.run(env.rt.launch(offer(), title=title, task=task))


def poll(env):
    asyncio.run(env.rt.poll_once())


# ── Offer and launch ─────────────────────────────────────────────────────


def test_an_offer_serves_its_turn_once():
    offers = bt.LaunchOffers()
    first = offers.open(epoch=1, route_token="t", profile="", session_id="s", request="hola")
    assert offers.claim("inventado-1234567890") == (None, "invalid_turn")
    assert offers.claim(first.token) == (first, "")
    assert offers.claim(first.token) == (None, "used")
    second = offers.open(epoch=2, route_token="t", profile="", session_id="s", request="otra")
    assert first.closed == "replaced" and offers.claim(first.token) == (None, "invalid_turn")
    offers.close(second)
    assert offers.claim(second.token) == (None, "invalid_turn")
    assert "una sola vez" in bt.launch_context(second, "x") and second.token in bt.launch_context(second, "x")


def test_the_offer_carries_the_whole_procedure_and_a_code_argparse_accepts(tmp_path):
    """Real-Hermes test 2026-10-27: offers that were optional or sent the model to the reference first
    were passed over for browsing in the turn itself."""
    offer = bt.LaunchOffers().open(epoch=1, route_token="t", profile="", session_id="s", request="noticias")
    script = (tmp_path / "skills" / "iris-os" / "scripts" / "background_task.py").as_posix()
    text = bt.launch_context(offer, script)
    assert CODE.search(text).group(1) == offer.token
    assert f"python \"{script}\" --turn {offer.token} --payload-file" in text and "write_file" in text
    assert text.startswith("REGLA DE ESTE TURNO") and "browser_*" in text and "detente" in text
    assert '"started"' in text and '"unconfirmed"' in text
    assert re.fullmatch(r"[0-9a-f]{32}", offer.token)  # never "-…", which argparse takes for an option


def test_started_only_after_hermes_accepts_with_the_task_id_as_its_key(runtime):
    answer = launch(runtime)
    assert answer["status"] == "started" and answer["title"] == "revisar noticias"
    [(body, key)] = runtime.runs.submits
    assert key == answer["task_id"] and body["instructions"] == bt.WORKER_INSTRUCTIONS
    assert "Busca las noticias" in body["input"] and "Petición original del usuario" in body["input"]
    row = runtime.store.get(key)
    assert row["state"] == "running" and row["run_id"] == "run_1" and row["delivery"] == "none"
    assert runtime.calls.changed == 1


def test_a_rejected_launch_promises_nothing_and_leaves_nothing_to_deliver(runtime):
    runtime.runs.outcomes = [{"outcome": "rejected", "error": "HTTP 400"}]
    assert launch(runtime)["status"] == "rejected"
    assert runtime.store.active() == [] and runtime.store.pending_deliveries() == []
    assert runtime.store.inbox_rows() == []  # it never started: no record to read later


def test_an_unconfirmed_start_keeps_its_place_and_is_settled_with_the_same_key(runtime):
    """Codex B02: Hermes may hold the run although both answers were lost."""
    runtime.runs.outcomes = [{"outcome": "lost"}, {"outcome": "lost"}]
    answer = launch(runtime)
    assert answer["status"] == "unconfirmed" and "puede estar en marcha" in answer["message"]
    task_id = answer["task_id"]
    assert runtime.store.get(task_id)["state"] == "uncertain"
    assert launch(runtime, title="otra investigación")["status"] == "busy"  # its place is kept
    runtime.runs.outcomes = [{"outcome": "lost"}]
    poll(runtime)
    assert runtime.store.get(task_id)["state"] == "uncertain"
    poll(runtime)  # Hermes replays the run it holds under that key
    assert runtime.store.get(task_id)["state"] == "running" and runtime.store.get(task_id)["run_id"] == "run_1"
    assert {key for _, key in runtime.runs.submits} == {task_id}
    assert all(body == runtime.runs.submits[0][0] for body, _ in runtime.runs.submits)  # same request


@pytest.mark.parametrize("ending", ["deadline", "cancel"])
def test_an_unconfirmed_start_keeps_its_place_until_the_user_releases_it(runtime, ending):
    """Codex B02: no post after a cancel or past the limit, and time alone frees nothing."""
    runtime.runs.outcomes = [{"outcome": "lost"}] * 2
    task_id = launch(runtime)["task_id"]
    posts = len(runtime.runs.submits)
    if ending == "cancel":
        assert asyncio.run(runtime.rt.cancel(task_id)) is True
        assert runtime.rt.snapshot()[0]["state"] == "stopping"  # shown as cancelling, not finished
    else:
        runtime.clock.now += 601
    poll(runtime)
    assert runtime.rt.snapshot()[0]["state"] == "unresolved"
    runtime.clock.now += 10 * 86_400  # days later: still unknown, still holding its place
    poll(runtime)
    assert len(runtime.runs.submits) == posts  # a new post could create work the user withdrew
    assert runtime.store.get(task_id)["state"] == "uncertain" and runtime.calls.finished == []
    assert launch(runtime, title="otra investigación")["status"] == "busy"
    assert asyncio.run(runtime.rt.release(task_id)) is True  # the user decides, knowingly
    row = runtime.store.get(task_id)
    if ending == "cancel":
        assert row["state"] == "cancelled" and row["error"] == bt.UNKNOWN_CANCEL
        assert "no pude detenerla" in bt.outcome_text(row)
    else:
        assert row["state"] == "interrupted" and row["error"] == bt.UNKNOWN_START
    assert runtime.calls.finished == [task_id] and runtime.runs.stops == []
    assert launch(runtime, title="otra investigación")["status"] == "started"


def test_only_an_unresolved_start_can_be_released(runtime):
    running = launch(runtime)["task_id"]
    assert asyncio.run(runtime.rt.release(running)) is False and runtime.store.get(running)["state"] == "running"
    assert asyncio.run(runtime.rt.release("f" * 32)) is False


def test_without_durable_run_keys_nothing_is_launched(runtime):
    runtime.runs.keys = {"supported": True, "durable": False, "retention_s": 0}
    assert launch(runtime)["status"] == "no_idempotency" and runtime.runs.submits == []


def test_a_lost_answer_is_retried_with_the_same_key_never_as_a_second_task(runtime):
    runtime.runs.outcomes = [{"outcome": "lost"}, {"outcome": "accepted", "run_id": "run_1", "replayed": True}]
    assert launch(runtime)["status"] == "started"
    assert len({key for _, key in runtime.runs.submits}) == 1


def test_one_task_at_a_time_and_bounded_input(runtime):
    assert launch(runtime)["status"] == "started"
    busy = launch(runtime)
    assert busy["status"] == "busy" and busy["title"] == "revisar noticias"
    assert asyncio.run(runtime.rt.launch(offer(), title="  ", task="hola"))["status"] == "invalid"
    assert len(runtime.runs.submits) == 1


def test_the_indicator_sees_titles_never_requests_or_results(runtime):
    task_id = launch(runtime)["task_id"]
    assert runtime.rt.snapshot() == [{"id": task_id, "title": "revisar noticias", "state": "running",
                                      "created_at": 1000.0}]


# ── Following the run ────────────────────────────────────────────────────


def test_a_completed_run_becomes_one_pending_delivery(runtime):
    task_id = launch(runtime)["task_id"]
    poll(runtime)
    assert runtime.calls.finished == [] and runtime.store.get(task_id)["state"] == "running"
    runtime.runs.set(status="completed", output={"texto_hablado": "Tres noticias: A, B y C."})
    poll(runtime)
    poll(runtime)  # an ended task is not followed again
    row = runtime.store.get(task_id)
    assert row["state"] == "completed" and row["result"] == "Tres noticias: A, B y C." and row["delivery"] == "pending"
    assert runtime.calls.finished == [task_id] and runtime.rt.snapshot() == []


def test_cancel_asks_hermes_to_stop_and_the_user_decision_stands(runtime):
    task_id = launch(runtime)["task_id"]
    assert asyncio.run(runtime.rt.cancel(task_id)) is True
    assert runtime.runs.stops == ["run_1"] and runtime.store.get(task_id)["state"] == "stopping"
    runtime.runs.set(status="completed", output="tarde")  # it ended before the stop took effect
    poll(runtime)
    assert runtime.store.get(task_id)["state"] == "cancelled"
    assert bt.outcome_text(runtime.store.get(task_id)) == "Cancelaste la tarea «revisar noticias»."
    assert asyncio.run(runtime.rt.cancel(task_id)) is False


def test_a_stop_hermes_never_confirms_ends_after_its_grace(runtime):
    task_id = launch(runtime)["task_id"]
    asyncio.run(runtime.rt.cancel(task_id))
    runtime.clock.now += bt.STOP_GRACE_S + 1
    poll(runtime)
    assert runtime.store.get(task_id)["state"] == "cancelled"


def test_the_deadline_stops_a_long_task_but_a_finished_answer_still_counts(runtime):
    task_id = launch(runtime)["task_id"]
    runtime.clock.now += 601
    poll(runtime)
    assert runtime.runs.stops == ["run_1"] and runtime.store.get(task_id)["state"] == "stopping"
    runtime.runs.set(status="cancelled")
    poll(runtime)
    assert runtime.store.get(task_id)["state"] == "timed_out"
    runtime.runs.set(status="running")
    second = launch(runtime)["task_id"]
    runtime.clock.now += 601
    poll(runtime)
    runtime.runs.set(status="completed", output="Listo justo a tiempo.")
    poll(runtime)
    assert runtime.store.get(second)["state"] == "completed"


def test_a_run_waiting_for_an_approval_is_stopped_and_reported(runtime):
    task_id = launch(runtime)["task_id"]
    runtime.runs.set(status="waiting_for_approval")
    poll(runtime)
    assert runtime.runs.stops == ["run_1"]
    runtime.runs.set(status="cancelled")
    poll(runtime)
    row = runtime.store.get(task_id)
    assert row["state"] == "failed" and "aprobación" in row["error"]


def test_a_run_hermes_forgot_or_another_destination_is_interrupted(runtime):
    task_id = launch(runtime)["task_id"]
    runtime.runs.set(status="lost")
    for _ in range(bt.LOST_POLLS - 1):
        poll(runtime)
        assert runtime.store.get(task_id)["state"] == "running"
    poll(runtime)
    assert runtime.store.get(task_id)["state"] == "interrupted"
    other = launch(runtime)["task_id"]
    runtime.runs.scope = "otra-credencial"
    poll(runtime)
    assert runtime.store.get(other)["state"] == "interrupted" and runtime.runs.stops == []


# ── Restart and single delivery ──────────────────────────────────────────


def test_a_restart_recovers_without_repeating_a_task_or_an_announcement(runtime):
    running = launch(runtime)["task_id"]
    runtime.store.create({"id": "b" * 32, "created_at": 1000.0, "updated_at": 1000.0, "deadline": 1600.0,
                          "title": "ya dicha", "request": "r", "task": "t", "profile": "", "session_id": "s",
                          "scope": "scope-1", "state": "completed", "result": "x", "delivery": "presenting"})
    later = SimpleNamespace(finished=[])
    again = bt.BackgroundTaskRuntime(bt.BackgroundTaskStore(runtime.path), runtime.runs,
                                     clock=lambda: runtime.clock.now, finished=later.finished.append)
    asyncio.run(again.recover())
    assert again.store.get("b" * 32)["delivery"] == "inbox"  # maybe spoken already: kept, not repeated
    assert again.store.get(running)["state"] == "running" and len(runtime.runs.submits) == 1
    assert later.finished == []
    runtime.runs.set(status="completed", output="hecho")
    asyncio.run(again.poll_once())
    assert later.finished == [running]
    assert again.store.claim_delivery(running)["id"] == running
    assert again.store.claim_delivery(running) is None  # a repeated hand-out never presents twice
    assert again.store.settle_delivery(running, "delivered") is True
    assert again.store.settle_delivery(running, "inbox") is False


def test_a_task_launching_when_iris_closed_is_settled_with_its_key_not_posted_anew(runtime):
    runtime.store.create({"id": "a" * 32, "created_at": 1000.0, "updated_at": 1000.0, "deadline": 1600.0,
                          "title": "a medias", "request": "r", "task": "una tarea larga", "profile": "",
                          "session_id": "s", "scope": "scope-1", "state": "launching"})
    asyncio.run(runtime.rt.recover())
    assert runtime.store.get("a" * 32)["state"] == "uncertain"
    poll(runtime)
    assert runtime.store.get("a" * 32)["state"] == "running" and runtime.runs.submits[-1][1] == "a" * 32
    assert runtime.runs.submits[-1][0] == bt.run_body("una tarea larga", "r")


def test_a_kept_result_is_a_bandeja_item_with_its_own_actions(runtime):
    task_id = launch(runtime)["task_id"]
    runtime.runs.set(status="completed", output="Informe de noticias.")
    poll(runtime)
    runtime.store.claim_delivery(task_id)
    runtime.store.settle_delivery(task_id, "inbox")
    service = InboxService(store=lambda: None, store_problem=lambda: "", background=lambda: runtime.store)
    [item] = service.snapshot()["items"]
    assert item["origin"] == "background" and item["outcome"] == "completed"
    assert item["title"] == "Tarea: revisar noticias" and "Informe de noticias." in item["text"]
    assert "Busca las noticias más importantes de hoy" in item["text"] and service.snapshot()["unread"] == 1
    read = service.act(item["key"], "read", expected_revision=item["delivery"]["revision"])
    assert read["changed"] is True and read["unread"] == 0
    with pytest.raises(HTTPException) as stale:
        service.act(item["key"], "dismiss", expected_revision=item["delivery"]["revision"])
    assert stale.value.status_code == 409
    failed = dict(runtime.store.get(task_id), state="interrupted", error="se reinició")
    assert from_background(profile="default", task=failed).outcome == "failed"


# ── One conversation through Core ────────────────────────────────────────


class Talking(Agent):
    """The model: delegates long work when offered, answers quick questions at once."""

    def __init__(self, server):
        super().__init__()
        self.server, self._conversation_lock, self.launches = server, asyncio.Lock(), []

    async def _think(self, text, images=None, reasoning_effort=None, *, snapshot=None, turn_context=""):
        await super()._think(text, images, reasoning_effort, snapshot=snapshot, turn_context=turn_context)
        if text.startswith("[IRIS OS · resultado"):
            return {"texto_hablado": "Ya tengo las noticias: A, B y C.", "emocion": "feliz", "animacion_virtual": "idle"}
        found = CODE.search(turn_context or "")
        if "noticias" in text and found:
            answer = await self.server._launch_background_task(
                found.group(1), "revisar noticias", "Busca las noticias más importantes de hoy, en español.")
            self.launches.append(answer)
            if answer["status"] == "started":
                return {"texto_hablado": "Voy a revisarlas; puedes seguir hablando conmigo.", "emocion": "neutral",
                        "animacion_virtual": "idle"}
        return {"texto_hablado": "Son las diez y cuarto.", "emocion": "neutral", "animacion_virtual": "idle"}


@pytest.fixture
def chat(core, turn, monkeypatch, tmp_path):
    runs = FakeRuns()
    events = []

    async def broadcast(event, payload):
        events.append((event, payload))

    monkeypatch.setattr(core.server, "broadcast_to_face", broadcast)
    monkeypatch.setattr(core.server, "_launch_offers", bt.LaunchOffers())
    agent = Talking(core.server)
    monkeypatch.setattr(core.server.app.state, "agent", agent, raising=False)
    core.agent = agent
    rt = bt.BackgroundTaskRuntime(
        bt.BackgroundTaskStore(tmp_path / "bg.db"), runs, poll_s=0.01,
        changed=lambda: None, finished=lambda task_id: core.server._schedule_background_delivery(task_id))
    monkeypatch.setattr(core.server, "_background_runtime", rt)
    face = core.surface()
    return SimpleNamespace(agent=agent, runs=runs, rt=rt, face=face, events=events, server=core.server)


async def settle(server):
    while server._background_deliveries:
        await asyncio.gather(*list(server._background_deliveries))


def spoken(agent):
    return [response["texto_hablado"] for response in agent.responses]


def test_a_second_question_is_answered_before_the_delegated_task_ends(chat):
    async def scenario():
        follow = asyncio.create_task(chat.rt.run())  # Core's follower, polling the held run
        await chat.server._handle_user_text_unlocked(chat.face, "Busca las noticias más importantes de hoy")
        await chat.server._handle_user_text_unlocked(chat.face, "¿Qué hora es?")
        assert spoken(chat.agent) == ["Voy a revisarlas; puedes seguir hablando conmigo.", "Son las diez y cuarto."]
        assert chat.rt.store.active()[0]["state"] == "running"  # still working while we talked
        chat.runs.set(status="completed", output="Noticias: A, B y C.")  # the run ends now
        for _ in range(200):
            if len(chat.agent.responses) == 3:
                break
            await asyncio.sleep(0.01)
        await settle(chat.server)
        follow.cancel()

    asyncio.run(scenario())
    assert spoken(chat.agent)[2] == "Ya tengo las noticias: A, B y C."
    presentation = chat.agent.thoughts[2]
    assert "Noticias: A, B y C." in presentation["text"] and "No es un mensaje del usuario" in presentation["text"]
    assert "turn_context" not in presentation  # the presenting turn gets no code to delegate again
    assert "Hay una tarea en segundo plano en curso: «revisar noticias»" in chat.agent.thoughts[1]["turn_context"]
    assert CODE.search(chat.agent.thoughts[1]["turn_context"]) is None  # no second launch while busy
    assert ("IRIS", "Ya tengo las noticias: A, B y C.") in chat.agent.memory.recalled
    assert not any(role == "user" and text.startswith("[IRIS OS") for role, text in chat.agent.memory.recalled)
    assert chat.rt.store.get(chat.agent.launches[0]["task_id"])["delivery"] == "delivered"


def finished_task(chat, state="completed", result="Noticias: A, B y C.", **fields):
    async def scenario():
        await chat.server._handle_user_text_unlocked(chat.face, "Busca las noticias más importantes de hoy")
        task_id = chat.agent.launches[-1]["task_id"]
        if state == "cancelled":
            await chat.rt.cancel(task_id)
            chat.runs.set(status="cancelled")
        else:
            chat.runs.set(status=state if state != "timed_out" else "completed", output=result, **fields)
        return task_id

    return asyncio.run(scenario())


def deliver(chat, task_id, times=1):
    async def scenario():
        for _ in range(times):
            await chat.server._deliver_background_task(task_id)

    asyncio.run(scenario())


@pytest.mark.parametrize("profile, session", [(None, "otra"), ("trabajo", "s")], ids=["conversation", "profile"])
def test_a_result_for_another_conversation_or_profile_waits_in_the_bandeja(chat, profile, session):
    task_id = finished_task(chat)
    asyncio.run(chat.rt.poll_once())
    chat.agent.bridge.route_context = lambda: {"profile": profile, "session_id": session, "generation": 1,
                                               "token": f"{profile or 'default'}:1", "api_key": ""}
    chat.rt.store.update(task_id, states=bt.FINISHED, delivery="pending")
    deliver(chat, task_id)
    assert chat.rt.store.get(task_id)["delivery"] == "inbox" and len(chat.agent.responses) == 1
    assert not any(t["text"].startswith("[IRIS OS") for t in chat.agent.thoughts)


def test_no_visible_iris_window_keeps_it_in_the_bandeja(chat):
    task_id = finished_task(chat)
    asyncio.run(chat.rt.poll_once())
    chat.server._disconnect_surface(chat.face)
    chat.rt.store.update(task_id, states=bt.FINISHED, delivery="pending")
    deliver(chat, task_id)
    assert chat.rt.store.get(task_id)["delivery"] == "inbox"


def test_a_repeated_delivery_presents_once(chat):
    task_id = finished_task(chat)

    async def scenario():
        await chat.rt.poll_once()  # schedules the delivery
        await chat.server._deliver_background_task(task_id)  # and a second attempt arrives
        await settle(chat.server)

    asyncio.run(scenario())
    assert spoken(chat.agent).count("Ya tengo las noticias: A, B y C.") == 1


@pytest.mark.parametrize("state, said", [
    ("failed", "No pude terminar la tarea «revisar noticias». El motor de IA informó un error."),
    ("interrupted", "La tarea «revisar noticias» se interrumpió antes de terminar."),
])
def test_failures_are_told_without_asking_the_model(chat, state, said):
    task_id = finished_task(chat, state=state, result="", error="sin red")

    async def scenario():
        await chat.rt.poll_once()
        await settle(chat.server)

    asyncio.run(scenario())
    assert spoken(chat.agent)[-1] == said and len(chat.agent.thoughts) == 1  # Hermes' raw text is not spoken
    row = chat.rt.store.get(task_id)
    assert row["delivery"] == "delivered" and "sin red" in bt.outcome_text(row)  # it stays readable


def test_cancelling_from_the_indicator_is_a_chat_line_and_a_new_message_never_cancels(chat):
    async def scenario():
        await chat.server._handle_user_text_unlocked(chat.face, "Busca las noticias más importantes de hoy")
        await chat.server._handle_user_text_unlocked(chat.face, "¿Qué hora es?")
        assert chat.runs.stops == [] and chat.rt.store.active()[0]["state"] == "running"
        lan = camera.Socket("192.168.1.20")
        task_id = chat.agent.launches[0]["task_id"]
        await chat.server._handle_face_message(lan, {"type": "background_task_cancel", "payload": {"id": task_id}})
        await settle(chat.server)
        assert chat.runs.stops == []  # another device on the network cannot stop it
        await chat.server._handle_face_message(chat.face, {"type": "background_task_cancel",
                                                           "payload": {"id": task_id}})
        await settle(chat.server)
        chat.runs.set(status="cancelled")
        await chat.rt.poll_once()
        await settle(chat.server)

    asyncio.run(scenario())
    assert chat.runs.stops == ["run_1"]
    assert ("background_task_notice", {"text": "Cancelaste la tarea «revisar noticias»."}) in chat.events
    assert len(chat.agent.responses) == 2  # nothing spoken for the user's own cancellation


def test_after_a_cancel_the_next_turn_knows_nothing_is_running(chat):
    """Real-Hermes test 2026-10-27: the cancel line never reaches the model's history, so it believed
    the task was still running and refused a new one."""
    async def scenario():
        await chat.server._handle_user_text_unlocked(chat.face, "Busca las noticias más importantes de hoy")
        task_id = chat.agent.launches[0]["task_id"]
        await chat.server._handle_face_message(chat.face, {"type": "background_task_cancel",
                                                           "payload": {"id": task_id}})
        chat.runs.set(status="cancelled")
        await chat.rt.poll_once()
        await settle(chat.server)
        await chat.server._handle_user_text_unlocked(chat.face, "¿Qué hora es?")

    asyncio.run(scenario())
    context = chat.agent.thoughts[-1]["turn_context"]
    assert CODE.search(context) is not None and "Hay una tarea en segundo plano en curso" not in context
    assert ("Estado: ahora no hay ninguna tarea en segundo plano en curso. La última, «revisar noticias», "
            "fue cancelada por el usuario.") in context
    # The context alone lost to a history that still said "te avisaré": the ending goes into the
    # history once, at the start of the next message sent to Hermes, never into Iris' memory.
    assert chat.agent.thoughts[-1]["text"] == (
        "[IRIS OS · aviso, no es un mensaje del usuario: la tarea en segundo plano «revisar noticias» fue "
        "cancelada por el usuario.]\n¿Qué hora es?")
    assert not any(text.startswith("[IRIS OS") for _, text in chat.agent.memory.recalled)

    async def again():
        await chat.server._handle_user_text_unlocked(chat.face, "Gracias")

    asyncio.run(again())
    assert chat.agent.thoughts[-1]["text"] == "Gracias"  # once


def test_a_presented_result_needs_no_note_but_one_left_in_the_bandeja_does(tmp_path):
    store = bt.BackgroundTaskStore(tmp_path / "background_tasks.db")
    base = {"created_at": 1.0, "updated_at": 1.0, "deadline": 9.0, "request": "r", "task": "t", "profile": "",
            "session_id": "s", "scope": "x", "state": "completed"}
    store.create({**base, "id": "a", "title": "presentada", "delivery": "delivered", "noted": 1})
    store.create({**base, "id": "b", "title": "en la bandeja", "delivery": "inbox"})
    store.create({**base, "id": "c", "title": "por presentar", "delivery": "none"})
    store.create({**base, "id": "d", "title": "otra conversación", "delivery": "inbox", "session_id": "t"})
    assert [task["id"] for task in store.unnoted(profile="", session_id="s")] == ["b"]
    assert bt.history_note(store.unnoted(profile="", session_id="s")) == (
        "[IRIS OS · aviso, no es un mensaje del usuario: la tarea en segundo plano «en la bandeja» terminó y su "
        "resultado quedó en la Bandeja de IRIS OS.]")
    store.mark_noted(["b"])
    assert store.unnoted(profile="", session_id="s") == []


def test_the_status_line_names_a_result_left_in_the_bandeja():
    task = {"title": "revisar noticias", "state": "completed", "delivery": "inbox"}
    assert "terminó y su resultado quedó en la Bandeja de IRIS OS." in bt.previous_context(task)
    for state in bt.FINISHED:
        assert bt.ENDINGS[state] in bt.previous_context({**task, "state": state, "delivery": "delivered"})


def test_a_turn_from_the_network_gets_no_offer(chat):
    asyncio.run(chat.server._handle_user_text_unlocked(camera.Socket("192.168.1.20"),
                                                       "Busca las noticias más importantes de hoy"))
    assert "turn_context" not in chat.agent.thoughts[0] and chat.runs.submits == []


def test_a_code_from_a_finished_turn_or_another_conversation_starts_nothing(chat):
    asyncio.run(chat.server._handle_user_text_unlocked(chat.face, "Dime algo corto"))
    token = CODE.search(chat.agent.thoughts[0]["turn_context"]).group(1)
    late = asyncio.run(chat.server._launch_background_task(token, "x", "Una tarea cualquiera de prueba."))
    assert late["status"] == "invalid_turn" and chat.runs.submits == []


# ── Endpoint, script and packaging ───────────────────────────────────────


def test_the_endpoint_is_loopback_only_and_strict():
    from core import websocket_server as server

    body = server.BackgroundTaskBody(turn="x" * 24, title="t", task="tarea de prueba")
    with pytest.raises(HTTPException) as refused:
        asyncio.run(server.background_task_launch(body, SimpleNamespace(client=SimpleNamespace(host="10.0.0.5"))))
    assert refused.value.status_code == 403
    with pytest.raises(ValueError):
        server.BackgroundTaskBody(turn="x" * 24, title="t", task="tarea", run_id="otro")


def run_script(*args, port=None, payload=None, tmp_path=None):
    import os

    script = ROOT / ".hermes" / "skills" / "iris-os" / "scripts" / "background_task.py"
    path = tmp_path / "tarea.json"
    path.write_text(json.dumps(payload or {"title": "revisar noticias", "task": "Busca noticias."}), "utf-8")
    env = {**os.environ, **({"IRIS_WEBSOCKET_PORT": str(port)} if port else {})}
    return subprocess.run([sys.executable, "-I", "-X", "utf8", str(script), *args, "--payload-file", str(path)],
                          capture_output=True, text=True, encoding="utf-8", timeout=30, env=env)


def test_the_script_posts_the_task_and_prints_whether_it_started(tmp_path):
    from http.server import BaseHTTPRequestHandler, HTTPServer

    seen = {}

    class Core(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            seen["path"] = self.path
            seen["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            body = json.dumps(bt.result("started", task_id="c" * 32, title="revisar noticias")).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Core)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        done = run_script("--turn", "a" * 24, port=server.server_address[1], tmp_path=tmp_path)
    finally:
        server.shutdown()
    assert seen == {"path": "/api/background-tasks", "body": {
        "schema": "iris.background-task/v1", "turn": "a" * 24, "title": "revisar noticias", "task": "Busca noticias."}}
    assert done.returncode == 0 and json.loads(done.stdout)["status"] == "started"


def test_the_script_refuses_a_bad_code_or_extra_fields_without_calling_core(tmp_path):
    bad = run_script("--turn", "x; rm -rf /", tmp_path=tmp_path)
    assert bad.returncode == 1 and "no es válido" in bad.stdout
    extra = run_script("--turn", "a" * 24, payload={"title": "t", "task": "x", "run": "y"}, tmp_path=tmp_path)
    assert extra.returncode == 1 and "solo «title» y «task»" in extra.stdout


def test_the_script_and_its_reference_ship_with_the_skill():
    from scripts import runtime_layout

    text = (ROOT / "pyproject.toml").read_text("utf-8")
    for name in ("scripts/background_task.py", "references/background.md"):
        assert f"iris-os/hermes-skills/iris-os/{name}" in runtime_layout._SKILL_FILES
        assert f".hermes/skills/iris-os/{name}" in text


# ── Codex review 2026-10-27 (B01–B04), kept as regressions ───────────────


def test_a_cancel_accepted_during_a_status_read_wins_over_that_answer(runtime, monkeypatch):
    """B03: the end is decided on the row as it is, not as it was when the status was asked."""
    task_id = launch(runtime)["task_id"]

    async def scenario():
        entered, release = asyncio.Event(), threading.Event()
        loop = asyncio.get_running_loop()

        def status(destination, run_id):
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(5)
            return {"status": "completed", "output": "Un resultado de antes de cancelar."}

        monkeypatch.setattr(runtime.runs, "status", status)
        polling = asyncio.create_task(runtime.rt.poll_once())
        try:
            await asyncio.wait_for(entered.wait(), 3)
            assert await runtime.rt.cancel(task_id)
        finally:
            release.set()
        await asyncio.wait_for(polling, 3)

    asyncio.run(scenario())
    row = runtime.store.get(task_id)
    assert row["state"] == "cancelled" and row["result"] == ""


async def ready_to_deliver(chat, state="completed", **fields):
    chat.rt._finished = lambda task_id: None  # the test hands the delivery out itself
    await chat.server._handle_user_text_unlocked(chat.face, "Busca las noticias más importantes de hoy")
    task_id = chat.agent.launches[-1]["task_id"]
    chat.runs.set(status=state, output="Noticias: A, B y C.", **fields)
    await chat.rt.poll_once()
    return task_id


@pytest.mark.parametrize("change", ["conversation", "profile", "hidden"])
def test_a_presentation_whose_place_changed_while_the_model_answered_goes_to_the_bandeja(chat, monkeypatch,
                                                                                       change):
    """B01: the destination is checked again after the model and before anything is said."""
    async def scenario():
        task_id = await ready_to_deliver(chat)
        entered, release = asyncio.Event(), asyncio.Event()
        original = chat.agent._think

        async def held(text, *args, **kwargs):
            if text.startswith("[IRIS OS · resultado"):
                entered.set()
                await release.wait()
            return await original(text, *args, **kwargs)

        monkeypatch.setattr(chat.agent, "_think", held)
        delivery = asyncio.create_task(chat.server._deliver_background_task(task_id))
        await asyncio.wait_for(entered.wait(), 3)
        if change == "hidden":
            chat.server._disconnect_surface(chat.face)
        else:
            chat.agent._session_id = "otra"
            monkeypatch.setattr(chat.agent.bridge, "route_context", lambda: {
                "profile": "trabajo" if change == "profile" else None, "session_id": "otra", "generation": 1,
                "token": "otra-ruta", "api_key": ""})
        release.set()
        await asyncio.wait_for(delivery, 3)
        return task_id

    task_id = asyncio.run(scenario())
    assert len(chat.agent.responses) == 1  # only the launch answer: nothing said elsewhere
    assert chat.rt.store.get(task_id)["delivery"] == "inbox"
    assert not any(text == "Ya tengo las noticias: A, B y C." for _, text in chat.agent.memory.recalled)


def bandeja(chat):
    return InboxService(store=lambda: None, store_problem=lambda: "", background=lambda: chat.rt.store).snapshot()


def test_an_empty_presentation_keeps_the_result_unread_in_the_bandeja(chat, monkeypatch):
    """B04: saying nothing is not presenting."""
    async def scenario():
        task_id = await ready_to_deliver(chat)

        async def empty(*args, **kwargs):
            return {"texto_hablado": "", "emocion": "neutral", "animacion_virtual": "idle"}

        monkeypatch.setattr(chat.agent, "_think", empty)
        await chat.server._deliver_background_task(task_id)
        return task_id

    task_id = asyncio.run(scenario())
    [item] = [item for item in bandeja(chat)["items"] if task_id in item["key"]]
    assert item["delivery"]["status"] == "pending" and "Noticias: A, B y C." in item["text"]


def test_a_failure_detail_is_readable_in_the_bandeja_and_a_presented_result_stays_there_as_read(chat):
    """B04: the Bandeja is the record; read items are never announced again."""
    async def scenario():
        failed = await ready_to_deliver(chat, state="failed", error="detalle-de-diagnostico")
        await chat.server._deliver_background_task(failed)
        chat.runs.set(status="running")
        done = await ready_to_deliver(chat)
        await chat.server._deliver_background_task(done)
        return failed, done

    failed, done = asyncio.run(scenario())
    view = bandeja(chat)
    by_id = {item["source_id"]: item for item in view["items"]}
    assert "detalle-de-diagnostico" in by_id[failed]["text"]
    assert "detalle-de-diagnostico" not in " ".join(r["texto_hablado"] for r in chat.agent.responses)
    assert by_id[done]["delivery"]["status"] == "read" and "Noticias: A, B y C." in by_id[done]["text"]
    assert view["unread"] == 0  # both were told: nothing to announce again


# ── Codex recheck 2026-10-27: after the audio, and an uncertain start's real run ──


@pytest.mark.parametrize("change", ["conversation", "profile", "hidden", "unchanged"])
def test_the_destination_is_checked_again_after_the_audio_is_generated(chat, monkeypatch, change):
    """B01: IRISAgent._respond itself runs; only its device and network outputs are replaced."""
    from types import MethodType

    from core import agent as agent_module

    async def scenario():
        task_id = await ready_to_deliver(chat)
        entered, release = asyncio.Event(), asyncio.Event()
        events, played = [], []

        async def speak(text, **kwargs):
            entered.set()
            await release.wait()
            return b"audio-sintetico", []

        async def play(audio):
            played.append(audio)

        def recorder(kind):
            async def record(*args, **kwargs):
                events.append(kind)
            return record

        for name in ("send_emotion", "send_animation", "send_speech_start", "send_subtitle",
                     "send_speech_end", "send_status"):
            monkeypatch.setattr(agent_module, name, recorder(name))
        monkeypatch.setattr(agent_module, "audio_clients_connected", lambda: 0)
        chat.agent._speak_lock = asyncio.Lock()
        chat.agent.tts = SimpleNamespace(speak=speak, play_audio=play)
        monkeypatch.setattr(chat.agent, "_respond", MethodType(agent_module.IRISAgent._respond, chat.agent))
        delivery = asyncio.create_task(chat.server._deliver_background_task(task_id))
        await asyncio.wait_for(entered.wait(), 3)
        if change == "hidden":
            chat.server._disconnect_surface(chat.face)
        elif change != "unchanged":
            monkeypatch.setattr(chat.agent.bridge, "route_context", lambda: {
                "profile": "trabajo" if change == "profile" else None, "session_id": "otra", "generation": 1,
                "token": "otra-ruta", "api_key": ""})
        release.set()
        await asyncio.wait_for(delivery, 3)
        return task_id, events, played

    task_id, events, played = asyncio.run(scenario())
    if change == "unchanged":
        assert played and "send_speech_start" in events
        assert chat.rt.store.get(task_id)["delivery"] == "delivered"
    else:
        assert not played and "send_speech_start" not in events and "send_subtitle" not in events
        assert chat.rt.store.get(task_id)["delivery"] == "inbox"


@pytest.mark.parametrize("ending", ["cancel", "deadline", "reconcile_then_cancel"])
def test_an_uncertain_start_that_hermes_is_running_is_never_abandoned(runtime, monkeypatch, ending):
    """B02: Hermes accepted the run and both answers were lost; at most one run ever runs."""
    backend, posts = {}, {}

    def submit(destination, body, key):
        posts[key] = posts.get(key, 0) + 1
        backend.setdefault(key, {"run_id": f"run-{len(backend) + 1}", "state": "running"})
        if len(backend) == 1 and posts[key] <= 2:
            return {"outcome": "lost"}
        return {"outcome": "accepted", "run_id": backend[key]["run_id"]}

    def stop(destination, run_id):
        runtime.runs.stops.append(run_id)
        for job in backend.values():
            if job["run_id"] == run_id:
                job["state"] = "cancelled"
        return True

    def status(destination, run_id):
        return next({"status": job["state"]} for job in backend.values() if job["run_id"] == run_id)

    monkeypatch.setattr(runtime.runs, "submit", submit)
    monkeypatch.setattr(runtime.runs, "stop", stop)
    monkeypatch.setattr(runtime.runs, "status", status)

    async def scenario():
        first = await runtime.rt.launch(offer(), title="primera", task="Investiga la primera noticia de prueba.")
        assert first["status"] == "unconfirmed"
        if ending == "reconcile_then_cancel":
            await runtime.rt.poll_once()  # the same key brings the run id back
            assert runtime.store.get(first["task_id"])["run_id"] == backend[first["task_id"]]["run_id"]
        if ending == "deadline":
            runtime.clock.now += 601
        else:
            assert await runtime.rt.cancel(first["task_id"])
        await runtime.rt.poll_once()
        second = await runtime.rt.launch(offer(), title="segunda", task="Investiga una segunda noticia de prueba.")
        running = [key for key, job in backend.items() if job["state"] == "running"]
        assert len(running) <= 1, (running, second)
        assert backend[first["task_id"]]["state"] != "running" or runtime.store.active()
        if ending == "reconcile_then_cancel":
            assert runtime.runs.stops == [backend[first["task_id"]]["run_id"]]
        else:
            assert second["status"] == "busy" and runtime.runs.stops == []

    asyncio.run(scenario())


# ── Codex second recheck 2026-10-27: cancelling across the reconciliation ──


class Backend:
    """Idempotent synthetic server whose first two answers are lost before or after admitting."""

    def __init__(self, *, initial_admitted):
        self.initial_admitted, self.jobs, self.posts, self.stops = initial_admitted, {}, [], []

    def submit(self, destination, body, key):
        self.posts.append(key)
        lost = len(self.posts) <= 2
        if lost and not self.initial_admitted:
            return {"outcome": "lost"}  # never reached the server
        job = self.jobs.setdefault(key, {"run_id": "run-" + key, "status": "running"})
        return {"outcome": "lost"} if lost else {"outcome": "accepted", "run_id": job["run_id"]}

    def stop(self, destination, run_id):
        self.stops.append(run_id)
        for job in self.jobs.values():
            if job["run_id"] == run_id:
                job["status"] = "cancelled"
        return True

    def status(self, destination, run_id):
        return next({"status": job["status"]} for job in self.jobs.values() if job["run_id"] == run_id)

    def attach(self, runtime, monkeypatch):
        for name in ("submit", "stop", "status"):
            monkeypatch.setattr(runtime.runs, name, getattr(self, name))


def test_a_cancel_during_the_destination_lookup_prevents_the_next_post(runtime, monkeypatch):
    backend = Backend(initial_admitted=False)
    backend.attach(runtime, monkeypatch)

    async def scenario():
        first = await runtime.rt.launch(offer(), title="primera", task="Investiga una noticia de prueba.")
        assert first["status"] == "unconfirmed" and backend.jobs == {}
        entered, release = asyncio.Event(), threading.Event()
        loop = asyncio.get_running_loop()
        original = runtime.runs.resolve

        def held(profile):
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(5)
            return original(profile)

        monkeypatch.setattr(runtime.runs, "resolve", held)
        polling = asyncio.create_task(runtime.rt.poll_once())
        try:
            await asyncio.wait_for(entered.wait(), 3)
            assert await runtime.rt.cancel(first["task_id"])
        finally:
            release.set()
        await asyncio.wait_for(polling, 3)
        return first["task_id"]

    task_id = asyncio.run(scenario())
    assert len(backend.posts) == 2 and backend.jobs == {}  # no work created after the cancel
    assert runtime.store.get(task_id)["unresolved"] == 1


def test_a_cancel_during_the_reconciling_post_stops_the_id_when_it_arrives(runtime, monkeypatch):
    backend = Backend(initial_admitted=False)
    backend.attach(runtime, monkeypatch)

    async def scenario():
        first = await runtime.rt.launch(offer(), title="primera", task="Investiga una noticia de prueba.")
        entered, release = asyncio.Event(), threading.Event()
        loop = asyncio.get_running_loop()

        def held(destination, body, key):
            answer = backend.submit(destination, body, key)
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(5)
            return answer

        monkeypatch.setattr(runtime.runs, "submit", held)
        polling = asyncio.create_task(runtime.rt.poll_once())
        try:
            await asyncio.wait_for(entered.wait(), 3)
            assert await runtime.rt.cancel(first["task_id"])
        finally:
            release.set()
        await asyncio.wait_for(polling, 3)
        await runtime.rt.poll_once()
        return first["task_id"]

    task_id = asyncio.run(scenario())
    assert backend.stops == ["run-" + task_id] and backend.jobs[task_id]["status"] == "cancelled"
    assert runtime.store.get(task_id)["state"] == "cancelled"


@pytest.mark.parametrize("ending", ["cancel", "deadline"])
def test_time_alone_never_lets_a_second_run_overlap_an_unconfirmed_one(runtime, monkeypatch, ending):
    backend = Backend(initial_admitted=True)
    backend.attach(runtime, monkeypatch)

    async def scenario():
        first = await runtime.rt.launch(offer(), title="primera", task="Investiga una noticia de prueba.")
        assert first["status"] == "unconfirmed"
        if ending == "cancel":
            assert await runtime.rt.cancel(first["task_id"])
        runtime.clock.now = runtime.store.get(first["task_id"])["deadline"] + 30 * 86_400
        await runtime.rt.poll_once()
        second = await runtime.rt.launch(offer(), title="segunda", task="Investiga una segunda noticia de prueba.")
        running = [key for key, job in backend.jobs.items() if job["status"] == "running"]
        assert len(running) <= 1 and second["status"] == "busy", (running, second)

    asyncio.run(scenario())


def test_release_comes_only_from_this_pcs_indicator(chat):
    async def scenario():
        chat.runs.outcomes = [{"outcome": "lost"}] * 2
        await chat.server._handle_user_text_unlocked(chat.face, "Busca las noticias más importantes de hoy")
        task_id = chat.agent.launches[-1]["task_id"]
        await chat.rt.cancel(task_id)
        await chat.rt.poll_once()  # now unresolved
        lan = camera.Socket("192.168.1.20")
        await chat.server._handle_face_message(lan, {"type": "background_task_release", "payload": {"id": task_id}})
        await settle(chat.server)
        assert chat.rt.store.get(task_id)["state"] == "uncertain"
        await chat.server._handle_face_message(chat.face, {"type": "background_task_release",
                                                           "payload": {"id": task_id}})
        await settle(chat.server)
        return task_id

    task_id = asyncio.run(scenario())
    assert chat.rt.store.get(task_id)["state"] == "cancelled"
