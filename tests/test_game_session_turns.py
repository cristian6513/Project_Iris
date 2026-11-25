"""The game session feeds the existing chat/voice pipeline, not a second agent."""

import asyncio

import pytest
import test_f03b_vision_snapshot as snapshot_fixtures
from test_f03b_vision_snapshot import PHOTO

from core import game_session, vision_model
from core.agent import IRISAgent


def test_human_question_preempts_real_commentary_task_before_conversation_lock(
    core, turn, monkeypatch
):
    async def run():
        from types import SimpleNamespace

        import core.agent as agent_module

        owner, service, s = await setup_game(core, turn, monkeypatch)
        turn._conversation_lock, turn._speak_lock = asyncio.Lock(), asyncio.Lock()
        stops = []

        async def stopped(*args, **kwargs):
            stops.append(True)

        async def noop(*args, **kwargs):
            pass

        turn.tts = SimpleNamespace(stop=stopped)
        turn.bridge.stop_current_run = noop
        monkeypatch.setattr(agent_module, "send_speech_end", noop)
        monkeypatch.setattr(agent_module, "send_status", noop)
        turn.interrupt = lambda **kwargs: IRISAgent.interrupt(turn, **kwargs)
        service.interrupt = lambda task: turn.interrupt(expected_turn=task, reason="user_question")
        now = [0.0]
        service.clock = lambda: now[0]
        s.expires = 1000
        entered = asyncio.Event()

        async def analysis(*args, **kwargs):
            entered.set()
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                return {"analyses": ['{"speak":true,"text":"Comentario antiguo."}']}

        monkeypatch.setattr(vision_model, "analyze", analysis)
        await service.command(
            owner,
            {
                "command": "commentary",
                "session_id": s.id,
                "enabled": True,
                "confirmed": True,
                "interval": 45,
                "limit": 100,
                "style": "chat",
            },
        )
        now[0] = 45
        await service.commentary.tick(s)
        automatic = s.auto_task
        await entered.wait()
        await asyncio.wait_for(core.server._handle_user_text(owner, "Ayúdame"), 2)
        await automatic
        assert stops and len(turn.responses) == 1
        assert turn.responses[0]["texto_hablado"] != "Comentario antiguo."
        assert any(role == "user" and text == "Ayúdame" for role, text in turn.memory.recalled)
        assert not service.commentary.humans and s.automatic
        assert turn._current_turn_task is None
        await service.stop()

    asyncio.run(run())


# Reuse the isolated Core fixtures without duplicating their setup.
core = snapshot_fixtures.core
turn = snapshot_fixtures.turn


@pytest.fixture(autouse=True)
def isolated_last_analysis(monkeypatch):
    monkeypatch.setattr(vision_model, "_last", {})


async def setup_game(h, agent, monkeypatch):
    owner = h.surface(role="desktop_companion")
    h.server._surface_registry.register_ready(
        owner,
        {
            "role": "desktop_companion",
            "visible": True,
            "capabilities": {
                "takes_screen_snapshots": True,
                "plays_tts": True,
                "accepts_chat": True,
            },
        },
    )

    async def interrupt(task):
        task.cancel()

    service = game_session.GameSessions(
        store=h.store,
        registry=h.server._surface_registry,
        is_local=h.server._is_local_client,
        agent=lambda: agent,
        interrupt=interrupt,
    )
    monkeypatch.setattr(h.server, "_game_sessions", service)
    original = owner.send_json

    async def send(message):
        await original(message)
        if message["type"] == "game_session_frame":
            service.broker.answer(owner, {**message["payload"], "status": "ok", "image": PHOTO})

    owner.send_json = send
    await service.command(owner, {"command": "prepare", "request_id": "test"})
    s = service.session
    await service.command(
        owner,
        {
            "command": "start",
            "session_id": s.id,
            "confirmed": True,
            "surface": "window",
            "game": "Juego de prueba",
            "spoiler_free": True,
        },
    )
    return owner, service, s


def test_one_game_question_uses_authorized_vision_then_the_same_chat_and_memory(
    core, turn, monkeypatch
):
    async def run():
        owner, service, s = await setup_game(core, turn, monkeypatch)
        analyses = []

        async def analyze(home, images, prompt, expected=None):
            analyses.append((images, expected))
            return {"analyses": ["Un menú de habilidades visible"], "route": s.destination}

        monkeypatch.setattr(vision_model, "analyze", analyze)
        original = turn._think

        async def think(
            text, images=None, reasoning_effort=None, *, snapshot=None, turn_context=""
        ):
            routed = await IRISAgent._route_turn_images(turn, text, images, snapshot=snapshot)
            assert routed["images"] is None and "menú de habilidades" in routed["text"]
            return await original(text, images, snapshot=snapshot, turn_context=turn_context)

        turn._think = think
        await core.server._handle_user_text_unlocked(owner, "¿Qué habilidad me conviene?")
        assert len(analyses) == len(turn.thoughts) == len(turn.responses) == 1
        assert analyses[0][1] == s.destination and s.analyses == 1
        assert "Modo juego" in turn.thoughts[0]["turn_context"]
        assert ("user", "¿Qué habilidad me conviene?") in turn.memory.recalled
        assert PHOTO not in str(turn.memory.recalled)
        assert s.memory.recent[-2]["type"] == "pregunta_del_usuario"
        assert s.memory.recent[-1]["text"] == turn.responses[0]["texto_hablado"]
        assert "intercambios_recientes" in service.context(s)
        await service.stop()

    asyncio.run(run())


def test_forget_during_model_work_cannot_restore_old_game_context(core, turn, monkeypatch):
    async def run():
        owner, service, s = await setup_game(core, turn, monkeypatch)
        s.memory.goal = "Objetivo anterior"
        entered = asyncio.Event()

        async def stubborn(*args, **kwargs):
            entered.set()
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                return {"texto_hablado": "Respuesta antigua que no debe recordar"}

        turn._think = stubborn
        work = asyncio.create_task(core.server._handle_user_text_unlocked(owner, "Ayuda"))
        await entered.wait()
        await service.command(owner, {"command": "context", "session_id": s.id, "forget": True})
        with pytest.raises(asyncio.CancelledError):
            await work
        assert s.active and not s.memory.recent and not s.memory.goal
        assert turn.responses == []
        await service.stop()

    asyncio.run(run())


@pytest.mark.parametrize("frame_fails", [False, True])
def test_silenced_game_answers_by_text_without_respond_or_tts(core, turn, monkeypatch, frame_fails):
    async def run():
        owner, service, s = await setup_game(core, turn, monkeypatch)
        s.muted = True
        if frame_fails:

            async def failed(message):
                owner.sent.append(message)
                if message["type"] == "game_session_frame":
                    service.broker.answer(owner, {**message["payload"], "status": "unavailable"})

            owner.send_json = failed
        await core.server._handle_user_text_unlocked(owner, "¿Qué ves en el juego?")
        assert len(turn.thoughts) == 1 and turn.responses == []
        replies = [m for m in owner.sent if m["type"] == "game_session_answer"]
        assert len(replies) == 1 and replies[0]["payload"]["session_id"] == s.id
        assert "Modo juego" in turn.thoughts[0]["turn_context"]
        await service.stop()

    asyncio.run(run())


def test_exiting_while_model_is_thinking_cannot_publish_or_persist_its_late_answer(
    core, turn, monkeypatch
):
    async def run():
        owner, service, s = await setup_game(core, turn, monkeypatch)
        entered = asyncio.Event()

        async def think(*args, **kwargs):
            entered.set()
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                return {"texto_hablado": "Resultado antiguo"}

        turn._think = think
        work = asyncio.create_task(core.server._handle_user_text_unlocked(owner, "Ayuda"))
        await entered.wait()
        await service.stop()
        with pytest.raises(asyncio.CancelledError):
            await work
        assert turn.responses == [] and turn.memory.recalled == []
        assert not any(m["type"] == "game_session_answer" for m in owner.sent)

    asyncio.run(run())


def test_explicit_camera_question_keeps_its_original_sensor(core, turn, monkeypatch):
    async def run():
        owner, service, s = await setup_game(core, turn, monkeypatch)
        taken = []

        async def camera(*a, **k):
            taken.append(True)
            return {"code": "cancelled"}

        monkeypatch.setattr(core.server, "_take_turn_snapshot", camera)
        await core.server._handle_user_text_unlocked(owner, "¿Qué ves en la cámara?")
        assert taken == [True]
        assert not any(m["type"] == "game_session_frame" for m in owner.sent)
        await service.stop()

    asyncio.run(run())


def test_unmute_cannot_resurrect_an_answer_that_ignored_cancellation(core, turn, monkeypatch):
    async def run():
        owner, service, s = await setup_game(core, turn, monkeypatch)
        entered = asyncio.Event()

        async def think(*a, **k):
            entered.set()
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                return {"texto_hablado": "Respuesta de antes de silenciar"}

        turn._think = think
        work = asyncio.create_task(core.server._handle_user_text_unlocked(owner, "Ayuda"))
        await entered.wait()
        await service.command(owner, {"command": "mute", "session_id": s.id, "muted": True})
        await service.command(owner, {"command": "mute", "session_id": s.id, "muted": False})
        with pytest.raises(asyncio.CancelledError):
            await work
        assert turn.responses == [] and turn.memory.recalled == []
        await service.stop()

    asyncio.run(run())


def test_local_voice_uses_the_active_companion_owner(core, turn, monkeypatch):
    async def run():
        owner, service, s = await setup_game(core, turn, monkeypatch)
        assert core.server._surface_registry.audio_owner() is owner
        await core.server._handle_user_text_unlocked(None, "¿Qué habilidad elijo?")
        assert len(turn.thoughts) == 1 and turn.thoughts[0]["images"] == [PHOTO]
        assert len(turn.responses) == 1
        await service.stop()

    asyncio.run(run())


def test_preparing_through_protocol_does_not_block_cancel_messages(core, turn, monkeypatch):
    async def run():
        owner, service, s = await setup_game(core, turn, monkeypatch)
        await service.stop()
        entered, release = asyncio.Event(), asyncio.Event()

        async def planned(_):
            entered.set()
            await release.wait()
            return dict(core.planned.value)

        monkeypatch.setattr(vision_model, "planned_route", planned)
        before = set(core.server._background_deliveries)
        await asyncio.wait_for(
            core.server._handle_face_message(
                owner,
                {
                    "type": "game_session_command",
                    "payload": {"command": "prepare", "request_id": "new"},
                },
            ),
            1,
        )
        await asyncio.wait_for(entered.wait(), 1)
        await core.server._handle_face_message(
            owner,
            {
                "type": "game_session_command",
                "payload": {"command": "cancel_prepare", "request_id": "new"},
            },
        )
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(*(set(core.server._background_deliveries) - before))
        assert service.session is None

    asyncio.run(run())


def test_app_context_does_not_automatically_include_the_game_frame(core, turn, monkeypatch):
    async def run():
        owner, service, s = await setup_game(core, turn, monkeypatch)
        await core.server._handle_user_text_unlocked(
            owner,
            "Resume este documento",
            memory_text="Petición desde una app",
        )
        assert len(turn.thoughts) == 1 and turn.thoughts[0]["images"] is None
        assert not any(m["type"] == "game_session_frame" for m in owner.sent)
        await service.stop()

    asyncio.run(run())
