import asyncio

import pytest
import test_game_session as session_fixtures

from core.game_memory import GameMemory

world = session_fixtures.world


def test_context_is_small_quoted_and_never_shared_between_sessions():
    a, b = GameMemory(), GameMemory()
    a.goal = 'Resolver "puzle" sin spoilers'
    for i in range(20):
        a.add("comentario_de_iris", f"Observación {i}: " + "x" * 1000)
    assert len(a.recent) == 6 and all(len(row["text"]) <= 300 for row in a.recent)
    assert "datos citados" in a.context() and "no verificadas" in a.context()
    assert '\\"puzle\\"' in a.context()
    assert not b.recent and b.context() == ""
    a.clear()
    assert a.context() == "" and a.snapshot() == {"goal": "", "notes": "", "entries": 0}


@pytest.mark.parametrize("action", ["forget", "exit", "disconnect"])
def test_game_context_updates_and_is_forgotten_without_touching_other_history(world, action):
    async def run():
        h = world
        s = await h.activate()
        await h.service.command(
            h.owner,
            {
                "command": "context",
                "session_id": s.id,
                "goal": "Abrir el cofre",
                "notes": "Tengo una llave azul",
            },
        )
        assert s.memory.goal == "Abrir el cofre"
        assert "llave azul" in h.service.context(s)
        s.memory.add("respuesta_de_iris", "Mira la puerta azul.")
        if action == "forget":
            await h.service.command(
                h.owner, {"command": "context", "session_id": s.id, "forget": True}
            )
            assert s.active and h.service.session is s
        elif action == "disconnect":
            h.service.drop(h.owner)
            await asyncio.sleep(0)
        else:
            await h.service.stop()
        assert s.memory.context() == ""
        await h.service.stop()

    asyncio.run(run())


def test_changing_context_cancels_inflight_response_before_it_can_repopulate_memory(world):
    async def run():
        h = world
        s = await h.activate()
        pending = asyncio.create_task(h.service.take(h.owner, current=lambda: True))
        outcome = await pending
        await h.service.command(h.owner, {"command": "context", "session_id": s.id, "forget": True})
        assert not await outcome["authorized"]()
        assert s.memory.recent == []
        await h.service.stop()

    asyncio.run(run())


@pytest.mark.parametrize("goal,notes", [(None, "a"), ("x" * 241, ""), ("", "x" * 801)])
def test_invalid_context_is_not_coerced_or_saved(world, goal, notes):
    async def run():
        h = world
        s = await h.activate()
        await h.service.command(
            h.owner, {"command": "context", "session_id": s.id, "goal": goal, "notes": notes}
        )
        assert s.memory.context() == ""
        await h.service.stop()

    asyncio.run(run())
