"""Surfaces tell turns apart by Core's number: a stopped turn, its late tools and the next message cross."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import core.agent as agent_module
import core.websocket_server as server


@pytest.fixture
def world(monkeypatch):
    events: list[tuple[str, dict]] = []

    async def record(kind, payload):
        events.append((kind, payload))

    async def nothing(*args, **kwargs):
        return None

    monkeypatch.setattr(server, "broadcast_to_face", record)
    monkeypatch.setattr(agent_module, "broadcast_to_face", record)
    monkeypatch.setattr(server, "_stop_inbox_voice", nothing)
    monkeypatch.setattr(server, "_cancel_pending_snapshots", nothing)
    monkeypatch.setattr(server.app.state, "agent", SimpleNamespace(_conversation_lock=asyncio.Lock()), raising=False)
    return SimpleNamespace(events=events, turns=lambda: [e for e in events if e[0].startswith("turn_")])


def run_turn(monkeypatch, body):
    monkeypatch.setattr(server, "_handle_user_text_unlocked", body)
    return server._handle_user_text(None, "hola")


def test_a_turn_opens_and_closes_under_one_number(world, monkeypatch):
    async def body(*args, **kwargs):
        return None

    asyncio.run(run_turn(monkeypatch, body))
    [(start, first), (end, last)] = world.turns()
    assert (start, end) == ("turn_started", "turn_ended")
    assert first == last and isinstance(first["turn"], int)


def test_a_failed_turn_still_closes(world, monkeypatch):
    async def body(*args, **kwargs):
        raise RuntimeError("no")

    with pytest.raises(RuntimeError):
        asyncio.run(run_turn(monkeypatch, body))
    assert [kind for kind, _ in world.turns()] == ["turn_started", "turn_ended"]


def test_a_stopped_turn_closes_without_touching_the_next(world, monkeypatch):
    """The stopped turn ends after the new one started: its number says which one ended."""
    release = None

    async def body(*args, **kwargs):
        await release.wait()

    async def scenario():
        nonlocal release
        release = asyncio.Event()
        monkeypatch.setattr(server, "_handle_user_text_unlocked", body)
        stopped = asyncio.create_task(server._handle_user_text(None, "uno"))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        following = asyncio.create_task(server._handle_user_text(None, "dos"))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        stopped.cancel()
        with pytest.raises(asyncio.CancelledError):
            await stopped
        assert not following.done()
        release.set()
        await following

    asyncio.run(scenario())
    kinds = [kind for kind, _ in world.turns()]
    numbers = [payload["turn"] for _, payload in world.turns()]
    assert kinds == ["turn_started", "turn_started", "turn_ended", "turn_ended"]
    assert numbers[2] == numbers[0] != numbers[1] == numbers[3]


def test_a_tool_event_says_which_turn_it_belongs_to(world, monkeypatch):
    async def body(*args, **kwargs):
        agent_module.IRISAgent._on_tool_progress(None, {"tool": "web_search"})
        await asyncio.sleep(0)

    asyncio.run(run_turn(monkeypatch, body))
    number = world.turns()[0][1]["turn"]
    assert [payload["turn"] for kind, payload in world.events if kind == "tool_progress"] == [number]


def test_a_tool_event_outside_a_turn_belongs_to_none(world):
    async def scenario():
        agent_module.IRISAgent._on_tool_progress(None, {"tool": "web_search"})
        await asyncio.sleep(0)

    asyncio.run(scenario())
    assert [payload["turn"] for kind, payload in world.events if kind == "tool_progress"] == [None]
