"""A provider changed while a slow turn runs: the next message must not wait behind it.

QA22 (2026-11-10): a Nous Portal answer took over a minute, the person switched to another
provider in Configuración and wrote again. Hermes runs one turn per session and queues the
next, and Core does the same, so Iris stayed silent until the process was killed.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import core.agent as agent_module
import core.websocket_server as server
from core.model_selection import ConversationModelStore, ModelSelectionService
from tests.test_hermes_session_transport import FakeHermes, _PendingStream, _interruptible_agent, make_bridge


def _bridge_with_default(tmp_path, current):
    hermes = FakeHermes("locked_completed.sse")
    bridge = make_bridge(tmp_path, hermes)
    bridge.model_selection = ModelSelectionService(
        ConversationModelStore(tmp_path / "models.json"),
        default_reader=lambda profile: dict(current),
    )
    return hermes, bridge


def test_a_turn_running_on_a_provider_left_since_is_superseded(tmp_path, monkeypatch):
    monkeypatch.setattr("core.hermes_bridge.keep_unusable_tools_off", lambda profile=None: True)
    current = {"provider": "nous", "model": "hermes-4"}
    hermes, bridge = _bridge_with_default(tmp_path, current)
    seen = {}

    async def go():
        try:
            seen["idle"] = await bridge.running_turn_superseded()
            pending = _PendingStream()
            hermes.pending_stream = pending
            task = asyncio.create_task(bridge.send_message("hola"))
            await asyncio.wait_for(pending.started.wait(), 5)
            seen["same"] = await bridge.running_turn_superseded()
            current.clear()  # the default could not be read during the comparison
            seen["unknown"] = await bridge.running_turn_superseded()
            current.update(provider="xai", model="grok-4")  # Configuración saved another provider
            seen["changed"] = await bridge.running_turn_superseded()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            seen["after"] = await bridge.running_turn_superseded()
        finally:
            await bridge._client.aclose()

    asyncio.run(go())
    assert seen == {"idle": False, "same": False, "unknown": False, "changed": True, "after": False}


def test_a_selection_made_during_a_slow_turn_supersedes_it(tmp_path, monkeypatch):
    monkeypatch.setattr("core.hermes_bridge.keep_unusable_tools_off", lambda profile=None: True)
    hermes, bridge = _bridge_with_default(tmp_path, {"provider": "nous", "model": "hermes-4"})
    store = bridge.model_selection.store
    seen = {}

    async def go():
        try:
            pending = _PendingStream()
            hermes.pending_stream = pending
            task = asyncio.create_task(bridge.send_message("hola"))
            await asyncio.wait_for(pending.started.wait(), 5)
            store.set_selection(None, bridge.session_id, "xai", "grok-4")  # the model selector
            seen["changed"] = await bridge.running_turn_superseded()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            await bridge._client.aclose()

    asyncio.run(go())
    assert seen == {"changed": True}


@pytest.fixture
def world(monkeypatch):
    async def nothing(*args, **kwargs):
        return None

    monkeypatch.setattr(server, "broadcast_to_face", nothing)
    monkeypatch.setattr(agent_module, "broadcast_to_face", nothing)
    monkeypatch.setattr(server, "_stop_inbox_voice", nothing)
    monkeypatch.setattr(server, "_cancel_pending_snapshots", nothing)


def _agent(superseded: bool):
    """A Core whose conversation is held by a slow turn; «interrupt» stops it as Detener does."""
    agent = SimpleNamespace(_conversation_lock=asyncio.Lock(), interrupts=[], old=None)

    async def check():
        return superseded

    async def interrupt(*, reason, expected_turn):
        assert expected_turn is agent._current_turn_task
        agent.interrupts.append(reason)
        agent.old.cancel()
        with pytest.raises(asyncio.CancelledError):
            await agent.old

    agent.bridge = SimpleNamespace(running_turn_superseded=check)
    agent.interrupt = interrupt
    return agent


async def _hold(lock):
    async with lock:
        await asyncio.sleep(3600)  # the old provider, still thinking


def test_a_message_after_a_provider_change_stops_the_turn_still_running(tmp_path, world, monkeypatch):
    monkeypatch.setattr("core.hermes_bridge.keep_unusable_tools_off", lambda profile=None: True)
    current = {"provider": "fake-a", "model": "alpha-1"}
    hermes, bridge = _bridge_with_default(tmp_path, current)
    agent, _ = _interruptible_agent(bridge, monkeypatch)
    answered = []

    async def body(websocket, text, **kwargs):
        async with agent._turn_lock:
            agent._current_turn_task = asyncio.current_task()
        try:
            answered.append((text, await bridge.send_message(text)))
        finally:
            async with agent._turn_lock:
                agent._current_turn_task = None

    monkeypatch.setattr(server, "_handle_user_text_unlocked", body)
    monkeypatch.setattr(server.app.state, "agent", agent, raising=False)

    async def scenario():
        agent._conversation_lock = asyncio.Lock()
        agent._turn_lock = asyncio.Lock()
        try:
            pending = _PendingStream()
            hermes.pending_stream = pending
            stop = "/v1/runs/run_pending/stop"
            hermes.on_path[stop] = lambda: setattr(hermes, "pending_stream", None)
            old = asyncio.create_task(server._handle_user_text(None, "pregunta anterior"))
            await asyncio.wait_for(pending.started.wait(), 5)
            current.update(provider="fake-b", model="beta-1")
            await asyncio.wait_for(server._handle_user_text(None, "otra pregunta"), 5)
            with pytest.raises(asyncio.CancelledError):
                await old
            paths = [path for _, path, _ in hermes.requests]
            assert paths.index(stop) < max(i for i, path in enumerate(paths) if path.endswith("/chat/stream"))
        finally:
            await bridge._client.aclose()

    asyncio.run(scenario())
    assert [body["model"] for body in hermes.bodies("/chat/stream")] == ["alpha-1", "beta-1"]
    [(text, response)] = answered
    assert text == "otra pregunta"
    assert response["_model_turn"]["confirmed"] is True


def test_without_a_change_the_message_still_waits_its_turn(world, monkeypatch):
    answered = []

    async def body(websocket, text, **kwargs):
        answered.append(text)

    monkeypatch.setattr(server, "_handle_user_text_unlocked", body)

    async def scenario():
        agent = _agent(superseded=False)
        monkeypatch.setattr(server.app.state, "agent", agent, raising=False)
        agent.old = asyncio.create_task(_hold(agent._conversation_lock))
        agent._current_turn_task = agent.old
        await asyncio.sleep(0)
        following = asyncio.create_task(server._handle_user_text(None, "otra pregunta"))
        await asyncio.sleep(0.1)
        waited = not following.done()
        agent.old.cancel()
        with pytest.raises(asyncio.CancelledError):
            await agent.old
        await asyncio.wait_for(following, 5)
        return agent, waited

    agent, waited = asyncio.run(scenario())
    assert waited and agent.interrupts == []
    assert answered == ["otra pregunta"]


def test_provider_check_cannot_interrupt_the_next_turn(world, monkeypatch):
    async def scenario():
        old_started, finish_old = asyncio.Event(), asyncio.Event()
        queued_started, finish_queued = asyncio.Event(), asyncio.Event()
        queued_checked, checking, finish_check = asyncio.Event(), asyncio.Event(), asyncio.Event()
        answered = []

        async def check():
            if not queued_checked.is_set():
                queued_checked.set()
                return False
            checking.set()
            await finish_check.wait()
            return True

        agent = agent_module.IRISAgent.__new__(agent_module.IRISAgent)
        agent._conversation_lock = asyncio.Lock()
        agent._turn_lock = asyncio.Lock()
        agent._current_turn_task = None
        agent._session_id = "IRIS-iris"
        agent.tts, agent.memory = AsyncMock(), AsyncMock()
        agent.bridge = SimpleNamespace(running_turn_superseded=check, stop_current_run=AsyncMock())

        async def body(websocket, text, **kwargs):
            async with agent._turn_lock:
                agent._current_turn_task = asyncio.current_task()
            try:
                if text == "old":
                    old_started.set()
                    await finish_old.wait()
                elif text == "queued":
                    queued_started.set()
                    await finish_queued.wait()
                answered.append(text)
            finally:
                async with agent._turn_lock:
                    agent._current_turn_task = None

        monkeypatch.setattr(server.app.state, "agent", agent, raising=False)
        monkeypatch.setattr(server, "_handle_user_text_unlocked", body)
        old = asyncio.create_task(server._handle_user_text(None, "old"))
        await old_started.wait()
        queued = asyncio.create_task(server._handle_user_text(None, "queued"))
        await queued_checked.wait()
        new = asyncio.create_task(server._handle_user_text(None, "new"))
        await checking.wait()
        finish_old.set()
        await queued_started.wait()
        finish_check.set()
        await asyncio.sleep(0)
        finish_queued.set()
        await asyncio.gather(old, queued, new)

        agent.tts.stop.assert_not_awaited()
        agent.bridge.stop_current_run.assert_not_awaited()
        assert answered == ["old", "queued", "new"]

    asyncio.run(asyncio.wait_for(scenario(), 5))
