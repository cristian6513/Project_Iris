"""Session scheduler, real permission/frame broker, fake vision and speech."""

import asyncio
from types import SimpleNamespace

import pytest
import test_game_session as session_fixtures

from core import vision_model
from core.game_commentary import comment_text

world = session_fixtures.world


async def setup(h, monkeypatch, *, result=None, **options):
    s = await h.activate()
    h.registry.register_ready(
        h.owner,
        {
            "role": "desktop_companion",
            "visible": True,
            "capabilities": {"takes_screen_snapshots": True, "plays_tts": True},
        },
    )
    h.agent._conversation_lock = asyncio.Lock()
    h.agent._turn_lock = asyncio.Lock()
    h.agent._speak_lock = asyncio.Lock()
    h.agent._current_turn_task = None
    h.calls, h.spoken = [], []

    async def analyze(home, images, prompt, *, expected):
        h.calls.append((images, prompt, expected))
        return result or {"analyses": ['{"speak":true,"text":"Hay un cofre a la izquierda."}']}

    async def respond(response, *, still_relevant):
        if still_relevant():
            h.spoken.append(response["texto_hablado"])
            return True
        return False

    monkeypatch.setattr(vision_model, "analyze", analyze)
    h.agent._respond = respond
    await h.service.command(
        h.owner,
        {
            "command": "commentary",
            "session_id": s.id,
            "enabled": True,
            "confirmed": True,
            "interval": 45,
            "limit": 100,
            "style": "tips",
            **options,
        },
    )
    return s


async def tick(h, s, seconds=45):
    h.now[0] += seconds
    await h.service.commentary.tick(s)
    task = s.auto_task
    if task:
        await task


def test_schedule_waits_then_one_fresh_frame_and_same_destination(world, monkeypatch):
    async def run():
        h = world
        s = await setup(h, monkeypatch)
        await tick(h, s, 44)
        assert h.frames == h.calls == h.spoken == []
        await tick(h, s, 1)
        assert len(h.frames) == len(h.calls) == len(h.spoken) == s.analyses == 1
        assert h.calls[0][2] == s.destination
        assert s.memory.recent[0]["type"] == "comentario_de_iris"
        assert "No hay pregunta del usuario" in h.calls[0][1]
        assert not h.agent._conversation_lock.locked()
        assert h.agent._current_turn_task is None
        await tick(h, s)
        assert s.analyses == 2 and len(h.spoken) == 1  # repeated text suppressed
        await h.service.stop()

    asyncio.run(run())


@pytest.mark.parametrize(
    "options",
    [
        {"confirmed": False},
        {"enabled": "yes"},
        {"interval": 19},
        {"interval": 301},
        {"interval": True},
        {"limit": -1},
        {"limit": True},
        {"limit": 1001},
        {"style": "tools"},
        {"style": []},
    ],
)
def test_automatic_requires_explicit_valid_consent(world, monkeypatch, options):
    async def run():
        h = world
        s = await setup(h, monkeypatch, **options)
        await tick(h, s, 400)
        assert not s.automatic and h.frames == []
        await h.service.stop()

    asyncio.run(run())


@pytest.mark.parametrize("busy", ["conversation", "speech", "microphone", "human", "audio_owner"])
def test_busy_tick_skips_without_queuing_or_spending(world, monkeypatch, busy):
    async def run():
        h = world
        s = await setup(h, monkeypatch)
        if busy == "conversation":
            await h.agent._conversation_lock.acquire()
        if busy == "speech":
            await h.agent._speak_lock.acquire()
        if busy == "microphone":
            h.service.commentary.busy = lambda: True
        if busy == "human":
            h.service.commentary.humans.add(asyncio.current_task())
        if busy == "audio_owner":
            monkeypatch.setattr(h.registry, "audio_owner", lambda: None)
        await tick(h, s, 60)
        assert h.frames == h.calls == [] and s.auto_task is None
        await h.service.stop()

    asyncio.run(run())


@pytest.mark.parametrize(
    "analysis",
    [
        '{"speak":false,"text":""}',
        "invalid",
        '{"speak":true,"text":42}',
        '{"speak":"true","text":"No"}',
    ],
)
def test_silence_and_invalid_output_still_count_analysis_without_tts(world, monkeypatch, analysis):
    async def run():
        h = world
        s = await setup(h, monkeypatch, result={"analyses": [analysis]})
        await tick(h, s)
        assert s.analyses == 1 and h.spoken == []
        assert s.automatic
        await h.service.stop()

    asyncio.run(run())


def test_limit_pauses_automatic_but_manual_capture_remains_available(world, monkeypatch):
    async def run():
        h = world
        s = await setup(h, monkeypatch, limit=1)
        await tick(h, s)
        await tick(h, s)
        assert not s.automatic and s.auto_status == "limit" and s.analyses == 1
        frame = await h.service.take(h.owner, current=lambda: True)
        await frame["on_analysis"]()
        assert frame["image"] and s.analyses == 2 and s.active
        h.service.release_turn(asyncio.current_task())
        await h.service.stop()

    asyncio.run(run())


@pytest.mark.parametrize(
    "action", ["pause", "mute", "stop", "human", "expired", "provider", "agent_stop"]
)
def test_late_model_cannot_resurrect_comment_after_cancellation(world, monkeypatch, action):
    async def run():
        h = world
        s = await setup(h, monkeypatch)
        entered, finish = asyncio.Event(), asyncio.Event()

        async def stubborn(*args, **kwargs):
            entered.set()
            try:
                await finish.wait()
            except asyncio.CancelledError:
                pass
            return {"analyses": ['{"speak":true,"text":"Resultado antiguo."}']}

        monkeypatch.setattr(vision_model, "analyze", stubborn)
        h.now[0] = 45
        await h.service.commentary.tick(s)
        task = s.auto_task
        await entered.wait()
        if action == "pause":
            await h.service.command(
                h.owner, {"command": "commentary", "session_id": s.id, "enabled": False}
            )
        elif action == "mute":
            await h.service.command(h.owner, {"command": "mute", "session_id": s.id, "muted": True})
            await h.service.command(
                h.owner, {"command": "mute", "session_id": s.id, "muted": False}
            )
            assert not s.automatic
        elif action == "stop":
            await h.service.stop()
        elif action == "human":
            await h.service.commentary.begin_human(asyncio.current_task())
        elif action == "expired":
            h.now[0] += 31
        elif action == "agent_stop":
            task.cancel()  # general agent Stop, outside the session protocol
        else:
            h.destination["provider"] = "other"
        finish.set()
        await task
        assert h.spoken == []
        if action == "agent_stop":
            assert not s.automatic and s.auto_status == "paused"
        assert not h.agent._conversation_lock.locked()
        if action == "human":
            h.service.commentary.end_human(asyncio.current_task())
            assert s.automatic  # resumes after a full interval, no backlog
        await h.service.stop()

    asyncio.run(run())


def test_analysis_error_pauses_instead_of_repeatedly_spending(world, monkeypatch):
    async def run():
        h = world
        s = await setup(h, monkeypatch)

        async def fails(*args, **kwargs):
            raise RuntimeError("private provider details")

        monkeypatch.setattr(vision_model, "analyze", fails)
        await tick(h, s)
        assert s.analyses == 1 and not s.automatic and s.auto_status == "error"
        await tick(h, s, 300)
        assert s.analyses == 1 and h.spoken == []
        assert "private provider details" not in str(h.owner.messages)
        await h.service.stop()

    asyncio.run(run())


def test_pending_comment_is_single_flight_and_pause_keeps_session(world, monkeypatch):
    async def run():
        h = world
        s = await setup(h, monkeypatch)
        h.answer["send"] = False
        h.now[0] = 45
        await h.service.commentary.tick(s)
        task = s.auto_task
        for _ in range(100):
            if h.frames:
                break
            await asyncio.sleep(0.001)
        h.now[0] += 300
        await h.service.commentary.tick(s)
        assert s.auto_task is task and len(h.frames) == 1
        await h.service.command(
            h.owner, {"command": "commentary", "session_id": s.id, "enabled": False}
        )
        await task
        assert s.active and s.auto_task is None and h.calls == []
        await h.service.stop()

    asyncio.run(run())


def test_comment_output_is_bounded():
    assert comment_text({"analyses": ['{"speak":true,"text":"Uno. Dos. Tres."}']}) == "Uno. Dos."
    assert comment_text(SimpleNamespace()) == ""


def test_slow_enable_cannot_override_newer_mute_and_unmute(world, monkeypatch):
    async def run():
        h = world
        s = await setup(h, monkeypatch, confirmed=False)
        entered, finish = asyncio.Event(), asyncio.Event()
        original = h.service.authorized

        async def slow(session):
            entered.set()
            await finish.wait()
            return await original(session)

        monkeypatch.setattr(h.service, "authorized", slow)
        enabling = asyncio.create_task(
            h.service.command(
                h.owner,
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
        )
        await entered.wait()
        for muted in (True, False):
            await h.service.command(
                h.owner, {"command": "mute", "session_id": s.id, "muted": muted}
            )
        finish.set()
        await enabling
        assert not s.automatic
        await h.service.stop()

    asyncio.run(run())


def test_cancelled_preflight_cannot_request_frame_after_it_resolves(world, monkeypatch):
    async def run():
        h = world
        s = await setup(h, monkeypatch)
        entered = asyncio.Event()
        original = h.service.authorized

        async def stubborn(session):
            entered.set()
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                return await original(session)

        monkeypatch.setattr(h.service, "authorized", stubborn)
        h.now[0] = 45
        await h.service.commentary.tick(s)
        task = s.auto_task
        await entered.wait()
        await h.service.commentary.begin_human(asyncio.current_task())
        await task
        assert h.frames == [] and s.work is None
        h.service.commentary.end_human(asyncio.current_task())
        await h.service.stop()

    asyncio.run(run())
