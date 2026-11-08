"""Codex's progress review of F02/F03 (2026-10-10): one regression per finding.

R01 reconnect, R02 route frozen before sending, R03 LAN origin, R04
revocation, R05 surface and lease at the answer, R06 successor turns, R08
temporary copies. R07 is in tests/js/vision_snapshot.test.mjs.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import vision_model, vision_snapshot
from tests.test_f02_vision_model import FAKE_HERMES, IMAGE, TurnBridge, turn_agent
from tests.test_f03b_vision_snapshot import PHOTO, Agent, core, turn  # noqa: F401

EXPLICIT = {"auxiliary": {"vision": {"provider": "fake-v", "model": "ojo"}}}


# ── R01: a turn that reconnects routes its images like any other ─────────


class Reconnecting(TurnBridge):
    def __init__(self, comes_back=True):
        super().__init__()
        self.is_available = False
        self.comes_back = comes_back
        self.reconnects = 0

    async def reconnect(self):
        self.reconnects += 1
        self.is_available = self.comes_back
        return self.comes_back


@pytest.fixture
def analyses(monkeypatch, tmp_path):
    calls = []

    async def analyze(home, images, prompt, *, expected=None):
        calls.append({"images": images, "expected": expected})
        return {"analyses": ["Una manzana."], "planned": {}, "route": {"provider": "fake-v", "model": "ojo"}}

    monkeypatch.setattr(vision_model, "profile_home", lambda profile: tmp_path)
    monkeypatch.setattr(vision_model, "read_configured", lambda home: vision_model.configured(EXPLICIT))
    monkeypatch.setattr(vision_model, "analyze", analyze)
    return calls


def test_after_reconnecting_an_explicit_vision_model_still_describes_the_image(analyses):
    bridge = Reconnecting()
    asyncio.run(turn_agent(bridge)._think("¿qué es?", images=[IMAGE]))
    assert bridge.reconnects == 1 and len(analyses) == 1
    [sent] = bridge.sent
    assert sent["images"] is None and "Una manzana." in sent["text"]


def test_after_reconnecting_a_photo_still_goes_to_its_authorised_destination(analyses):
    bridge = Reconnecting()
    snapshot = {"image": PHOTO, "destination": {"provider": "fake-v", "model": "ojo"}}
    asyncio.run(turn_agent(bridge)._think("¿qué tengo en la mano?", images=[PHOTO], snapshot=snapshot))
    assert analyses[0]["expected"] == {"provider": "fake-v", "model": "ojo"}
    assert bridge.sent[0]["images"] is None


def test_without_hermes_no_image_goes_anywhere(analyses):
    bridge = Reconnecting(comes_back=False)
    agent = turn_agent(bridge)
    agent.fallback = SimpleNamespace(process=lambda text: {"texto_hablado": "Sin conexión.", "emocion": "triste",
                                                           "animacion_virtual": "pensar"})
    reply = asyncio.run(agent._think("¿qué es?", images=[IMAGE]))
    assert reply["texto_hablado"] == "Sin conexión." and analyses == [] and bridge.sent == []


def test_a_photo_whose_permission_was_revoked_is_not_sent(analyses):
    bridge = TurnBridge()

    async def revoked():
        return False

    snapshot = {"image": PHOTO, "destination": {"provider": "fake-v", "model": "ojo"}, "authorized": revoked}
    reply = asyncio.run(turn_agent(bridge)._think("mira esto", images=[PHOTO], snapshot=snapshot))
    assert reply["texto_hablado"] == vision_snapshot.OUTCOMES["permission_denied"]
    assert analyses == [] and bridge.sent == []


# ── R02 and R08: the worker against a fake Hermes ─────────────────────────


@pytest.fixture
def hermes(tmp_path):
    root = tmp_path / "hermes"
    for name, source in FAKE_HERMES.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(source), "utf-8")
    home = tmp_path / "perfil"
    (home / "cache" / "vision").mkdir(parents=True)

    def configure(vision):
        (home / "config.json").write_text(json.dumps({"auxiliary": {"vision": vision}}), "utf-8")

    def tool(body: str):
        (root / "tools/vision_tools.py").write_text(textwrap.dedent(body), "utf-8")

    def calls():
        log = home / "calls.jsonl"
        return [json.loads(line) for line in log.read_text("utf-8").splitlines()] if log.exists() else []

    return SimpleNamespace(root=root, home=home, configure=configure, tool=tool, calls=calls)


def run_worker(hermes, request):
    env = {**os.environ, "HERMES_HOME": str(hermes.home)}
    done = subprocess.run([sys.executable, "-B", "-c", vision_model._bootstrap()], input=json.dumps(request),
                          capture_output=True, text=True, cwd=hermes.root, env=env, timeout=60)
    line = next(l for l in reversed(done.stdout.splitlines()) if l.startswith(vision_model.MARKER))
    return json.loads(line[len(vision_model.MARKER):])


CHANGING_TOOL = '''
    import json, os
    async_call_llm = None
    def _load_auxiliary_client():
        global async_call_llm
        import agent.auxiliary_client as aux
        async_call_llm = async_call_llm or aux.async_call_llm
    async def vision_analyze_tool(image_url, user_prompt, model=None):
        _load_auxiliary_client()
        # Another writer changes the vision provider while the image is prepared.
        path = os.path.join(os.environ["HERMES_HOME"], "config.json")
        json.dump({"auxiliary": {"vision": {"provider": "otro", "model": "lee"}}}, open(path, "w"))
        await async_call_llm(task="vision", messages=[image_url[:20], user_prompt])
        return json.dumps({"success": True, "analysis": "Se ve una manzana roja."})
'''


def test_a_config_change_while_the_image_is_prepared_cannot_redirect_it(hermes):
    hermes.configure({"provider": "visionprov", "model": "lee"})
    hermes.tool(CHANGING_TOOL)
    result = run_worker(hermes, {"command": "analyze", "images": [IMAGE], "prompt": "p",
                                 "expected": {"provider": "visionprov", "model": "lee"}})
    # The provider that received the call is the one checked, not the one written meanwhile.
    assert result["ok"] is True and result["route"] == {"provider": "visionprov", "model": "lee"}
    assert json.loads((hermes.home / "config.json").read_text("utf-8"))["auxiliary"]["vision"]["provider"] == "otro"


SLOW_TOOL = '''
    import json, os, time, uuid
    async_call_llm = None
    def _load_auxiliary_client():
        global async_call_llm
        import agent.auxiliary_client as aux
        async_call_llm = async_call_llm or aux.async_call_llm
    async def vision_analyze_tool(image_url, user_prompt, model=None):
        _load_auxiliary_client()
        # Like Hermes: a private copy named after uuid4(), deleted in finally.
        path = os.path.join(os.environ["HERMES_HOME"], "cache", "vision", f"temp_image_{uuid.uuid4()}.img")
        open(path, "wb").write(b"\\x89PNG fake")
        try:
            if os.environ.get("IRIS_TEST_SLOW"):
                time.sleep(30)
            await async_call_llm(task="vision", messages=[image_url[:20], user_prompt])
            return json.dumps({"success": True, "analysis": "Una manzana."})
        finally:
            os.unlink(path)
'''


@pytest.fixture
def async_worker(hermes, monkeypatch):
    import wizard.provider_sync as provider_sync

    monkeypatch.setattr(provider_sync, "_resolve_hermes_python", lambda: Path(sys.executable))
    monkeypatch.setattr(provider_sync, "_hermes_python_probe_command", lambda python, probe: [str(python), "-B", "-c", probe])

    def kwargs(timeout, slow=False):
        env = dict(os.environ)
        if slow:
            env["IRIS_TEST_SLOW"] = "1"
        return {"env": env, "cwd": str(hermes.root), "timeout": timeout}

    slow = SimpleNamespace(on=False)
    monkeypatch.setattr(provider_sync, "_hermes_probe_kwargs", lambda timeout: kwargs(timeout, slow.on))
    hermes.configure({"provider": "visionprov", "model": "lee"})
    hermes.tool(SLOW_TOOL)
    return slow


def test_a_cancelled_analysis_leaves_no_copy_of_the_image(hermes, async_worker):
    foreign = hermes.home / "cache" / "vision" / "temp_image_de-otra-operacion.img"
    foreign.write_bytes(b"ajeno")
    async_worker.on = True

    async def scenario():
        task = asyncio.create_task(vision_model.analyze(hermes.home, [IMAGE], "p"))
        for _ in range(100):  # until Hermes wrote its copy
            await asyncio.sleep(0.1)
            if len(list((hermes.home / "cache" / "vision").iterdir())) > 1:
                break
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert [p.name for p in (hermes.home / "cache" / "vision").iterdir()] == [foreign.name]  # only the other one


def test_a_finished_analysis_leaves_nothing_and_keeps_other_files(hermes, async_worker):
    other = hermes.home / "cache" / "vision" / "otro.bin"
    other.write_bytes(b"x")
    result = asyncio.run(vision_model.analyze(hermes.home, [IMAGE], "p"))
    assert result["analyses"] == ["Una manzana."]
    assert [p.name for p in (hermes.home / "cache" / "vision").iterdir()] == ["otro.bin"]


def test_temporary_copies_are_never_removed_through_a_link(tmp_path):
    from tests.test_runtime_descriptor import make_directory_alias

    home = tmp_path / "perfil"
    (home / "cache").mkdir(parents=True)
    outside = tmp_path / "fuera"
    outside.mkdir()
    victim = outside / "temp_image_abcdef012345-0000.img"
    victim.write_bytes(b"no borrar")
    make_directory_alias(home / "cache" / "vision", outside)
    try:
        assert vision_model.remove_temporary_images(home, "abcdef012345") == 0
        assert victim.exists()
    finally:
        (home / "cache" / "vision").rmdir()
    assert vision_model.remove_temporary_images(home, "../../x") == 0


# ── R03–R06: Core's photo request ─────────────────────────────────────────


def test_a_lan_chat_never_asks_this_pcs_camera(core, turn):
    face = core.surface()
    core.server._surface_registry.request_camera(face)
    core.store.set_mode("vision.snapshot", "allow")
    core.consent.remember({"provider": "openai-codex", "model": "gpt-5.5"})
    lan = core.surface("192.168.1.30", snapshots=False)

    async def scenario():
        await core.server._handle_face_message(lan, {"type": "user_text", "payload": {
            "text": "¿qué tengo en la mano?", "camera_question": True}})
        await asyncio.gather(*core.server._background_turns)

    asyncio.run(scenario())
    assert face.requests() == []  # no photo for a remote origin, remembered or not
    assert turn.thoughts == [{"text": "¿qué tengo en la mano?", "images": None, "snapshot": None}]  # chat as usual


def test_revoking_while_the_dialog_waits_cancels_and_an_old_answer_cannot_undo_it(core):
    face = core.surface()

    async def scenario():
        task = asyncio.create_task(core.server._take_turn_snapshot(face, core.agent, "mira esto"))
        for _ in range(50):
            await asyncio.sleep(0.01)
            if face.requests():
                break
        [request] = face.requests()
        core.store.set_mode("vision.snapshot", "deny")
        await core.server._reconcile_runtime_capability_policy("vision.snapshot")
        outcome = await task
        # The surface answers late, with "no volver a preguntar".
        accepted = core.broker.answer(face, {"request_id": request["request_id"], "status": "ok",
                                             "image": PHOTO, "remember": True})
        return outcome, accepted

    outcome, accepted = asyncio.run(scenario())
    assert outcome == {"code": "permission_denied"} and accepted is False
    assert face.cancels()[-1]["reason"] == "permission_changed"
    assert core.store.evaluate("vision.snapshot")["verdict"] == "deny"
    assert not core.consent.covers({"provider": "openai-codex", "model": "gpt-5.5"})


def test_remember_is_ignored_if_the_permission_changed_meanwhile(core):
    face = core.surface()

    def change_then_answer(payload):
        core.store.set_mode("vision.snapshot", "deny")
        core.store.set_mode("vision.snapshot", "ask")  # changed and back: another revision
        core.broker.answer(face, {"request_id": payload["request_id"], "status": "ok", "image": PHOTO, "remember": True})

    face.reply = change_then_answer
    outcome = asyncio.run(core.server._take_turn_snapshot(face, core.agent, "mira esto"))
    assert "image" in outcome  # the photo was confirmed with a click
    assert core.store.evaluate("vision.snapshot")["verdict"] == "ask"  # but not "remembered"
    assert not core.consent.covers({"provider": "openai-codex", "model": "gpt-5.5"})


def test_a_deny_written_just_before_remembering_is_never_overwritten(core, monkeypatch):
    # Q01: Codex's race. A deny lands after the dialog's revision was read and before
    # "remember" writes: the compare and the write share one lock, so the deny stands.
    face = core.surface()
    face.reply = core.answer(face, status="ok", image=PHOTO, remember=True)
    real = core.store.set_mode_if_unchanged

    def deny_first(*args, **kwargs):
        core.store.set_mode("vision.snapshot", "deny")  # the competing writer wins the lock
        return real(*args, **kwargs)

    monkeypatch.setattr(core.store, "set_mode_if_unchanged", deny_first)
    outcome = asyncio.run(core.server._take_turn_snapshot(face, core.agent, "mira esto"))
    assert "image" in outcome  # clicked before the deny; analysis checks `authorized` again
    assert asyncio.run(outcome["authorized"]()) is False
    assert core.store.evaluate("vision.snapshot")["verdict"] == "deny"
    assert not core.consent.covers({"provider": "openai-codex", "model": "gpt-5.5"})


@pytest.mark.parametrize("competitor", ["deny", "ask", "preset"])
def test_remembering_and_a_competing_change_always_end_in_the_users_choice(tmp_path, competitor):
    # Q01 in the store: whichever wins the lock, a stale "remember" never survives a newer choice.
    import threading

    from core.capability_permissions import CapabilityPermissionStore

    for round_ in range(25):
        store = CapabilityPermissionStore(tmp_path / f"policy-{round_}.json")
        store.set_mode("vision.snapshot", "ask")
        row = next(r for r in store.snapshot()["capabilities"] if r["id"] == "vision.snapshot")
        expected = (row["mode"], row["updated_at"])
        remembered, results = [], []
        start = threading.Barrier(2)

        def remember():
            start.wait()
            results.append(store.set_mode_if_unchanged("vision.snapshot", "allow", expected=expected,
                                                       on_commit=lambda: remembered.append(True)))

        def change():
            start.wait()
            if competitor == "preset":
                store.apply_preset("strict")
            else:
                store.set_mode("vision.snapshot", competitor)

        threads = [threading.Thread(target=remember), threading.Thread(target=change)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        final = store.evaluate("vision.snapshot")["mode"]
        assert final == {"deny": "deny", "ask": "ask", "preset": "deny"}[competitor]
        assert remembered == ([True] if results == [True] else [])  # the destination only with the write


def test_two_parallel_remembers_write_once(tmp_path):
    import threading

    from core.capability_permissions import CapabilityPermissionStore

    store = CapabilityPermissionStore(tmp_path / "policy.json")
    row = next(r for r in store.snapshot()["capabilities"] if r["id"] == "vision.snapshot")
    expected = (row["mode"], row["updated_at"])
    start, results = threading.Barrier(2), []

    def remember():
        start.wait()
        results.append(store.set_mode_if_unchanged("vision.snapshot", "allow", expected=expected))

    threads = [threading.Thread(target=remember) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(results) == [False, True]  # the second saw a newer revision
    audit = [a for a in store.snapshot()["audit"] if a["capability"] == "vision.snapshot"]
    assert len(audit) == 1


def test_a_remembered_photo_is_refused_once_the_permission_goes_back_to_ask(core):
    face = core.surface()
    core.server._surface_registry.request_camera(face)
    core.store.set_mode("vision.snapshot", "allow")
    core.consent.remember({"provider": "openai-codex", "model": "gpt-5.5"})

    def downgrade_then_answer(payload):
        assert payload["needs_confirmation"] is False
        core.store.set_mode("vision.snapshot", "ask")
        core.broker.answer(face, {"request_id": payload["request_id"], "status": "ok", "image": PHOTO})

    face.reply = downgrade_then_answer
    assert asyncio.run(core.server._take_turn_snapshot(face, core.agent, "mira esto")) == {"code": "permission_denied"}


def test_a_photo_from_a_surface_that_hid_is_discarded(core):
    face = core.surface()

    def hide_then_answer(payload):
        core.server._surface_registry.update_state(face, {"visible": False})
        core.broker.answer(face, {"request_id": payload["request_id"], "status": "ok", "image": PHOTO})

    face.reply = hide_then_answer
    assert asyncio.run(core.server._take_turn_snapshot(face, core.agent, "mira esto")) == {"code": "surface_changed"}


def test_the_lease_history_tells_a_legit_photo_from_a_moved_camera(core):
    face, other = core.surface(), core.surface()
    registry = core.server._surface_registry

    def own_open_and_release(payload):
        registry.request_camera(face)  # the camera was off: the photo opens it...
        registry.release_camera(face)  # ...and releases it
        core.broker.answer(face, {"request_id": payload["request_id"], "status": "ok", "image": PHOTO})

    face.reply = own_open_and_release
    assert "image" in asyncio.run(core.server._take_turn_snapshot(face, core.agent, "mira esto"))

    def another_takes_it_and_gives_it_back(payload):
        registry.request_camera(other)
        registry.release_camera(other)
        core.broker.answer(face, {"request_id": payload["request_id"], "status": "ok", "image": PHOTO})

    face.reply = another_takes_it_and_gives_it_back
    assert asyncio.run(core.server._take_turn_snapshot(face, core.agent, "mira esto")) == {"code": "surface_changed"}


def test_another_message_ends_the_photo_wait_and_runs_next(core, turn):
    face = core.surface()

    async def scenario():
        await core.server._handle_face_message(face, {"type": "user_text", "payload": {"text": "mira esto"}})
        for _ in range(100):
            await asyncio.sleep(0.01)
            if face.requests():
                break
        await core.server._handle_face_message(face, {"type": "user_text", "payload": {"text": "ya no, mejor dime hola"}})
        await asyncio.wait_for(asyncio.gather(*core.server._background_turns), 5)

    asyncio.run(scenario())
    [request] = face.requests()
    assert face.cancels() == [{"request_id": request["request_id"], "reason": "superseded"}]
    assert turn.responses[0]["texto_hablado"] == vision_snapshot.OUTCOMES["superseded"]
    assert [t["text"] for t in turn.thoughts] == ["ya no, mejor dime hola"]
    assert core.broker.pending() == 0


def test_an_earlier_turn_that_has_not_asked_yet_will_not_ask(core, monkeypatch):
    face = core.surface()
    gate = asyncio.Event

    async def scenario():
        release = gate()
        real = vision_model.planned_route

        async def slow_planned(home):
            await release.wait()  # the first turn is still finding its destination...
            return await real(home)

        monkeypatch.setattr(vision_model, "planned_route", slow_planned)
        epoch = core.server._turn_epoch
        task = asyncio.create_task(core.server._take_turn_snapshot(face, core.agent, "mira esto", turn_epoch=epoch))
        await asyncio.sleep(0.01)
        core.server._turn_epoch += 1  # ...when another message arrives
        release.set()
        return await task

    assert asyncio.run(scenario()) == {"code": "superseded"}
    assert face.requests() == []
