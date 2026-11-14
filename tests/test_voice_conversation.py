"""Conversational microphone lifecycle with Hermes RPC and audio receipts isolated."""

import asyncio
import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core import websocket_server as server
from core.surface_registry import SurfaceRegistry
from core.tts_playback import TtsPlaybackReceipts
from tests.test_manual_voice import runtime_fixture


class Socket:
    def __init__(self, host="127.0.0.1"):
        self.client = SimpleNamespace(host=host)
        self.messages = []

    async def send_json(self, message):
        self.messages.append(message)

    async def send_text(self, data):
        await self.send_json(json.loads(data))

    def phase(self):
        return next((msg["payload"]["phase"] for msg in reversed(self.messages)
                     if msg["type"] == "voice_conversation_state"), None)


def wire(monkeypatch, *, wake=False):
    registry = SurfaceRegistry()
    monkeypatch.setattr(server, "_surface_registry", registry)
    monkeypatch.setattr(server, "_connected_clients", set())
    monkeypatch.setattr(server, "_tts_playback", TtsPlaybackReceipts())
    runtime, _ = runtime_fixture(wake=wake)
    runtime.event_handler = server.handle_hermes_wake_event
    route = {"token": "default:0"}
    bridge = SimpleNamespace(is_available=True, route_context=lambda: route)
    monkeypatch.setattr(server.app.state, "agent", SimpleNamespace(bridge=bridge), raising=False)
    monkeypatch.setattr(server.app.state, "hermes_wake", runtime, raising=False)
    monkeypatch.setattr(server, "_conversational_mic_allowed", lambda: True)
    monkeypatch.setattr(server.VoiceConversation, "SPEAKER_TAIL_SECONDS", 0)
    owner = Socket()
    other = Socket()
    for socket, role in [(owner, "face"), (other, "desktop_companion")]:
        server._connect_surface(socket)
        registry.register_ready(socket, {"role": role, "visible": True, "focused": socket is owner,
                                         "capabilities": {"reports_tts_playback": True}})
    start = {
        "action": "start", "id": str(uuid.uuid4()),
        "client_id": str(uuid.uuid4()), "confirmed": True,
    }
    return runtime, owner, other, start, route


async def until(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0.001)


def takes(runtime):
    return sum(call.args == ("voice.record", {"action": "start"})
               for call in runtime.request.await_args_list)


def test_multiple_turns_require_the_correct_audio_receipt_and_keep_one_capture(monkeypatch):
    async def scenario():
        runtime, owner, other, start, _ = wire(monkeypatch)
        prompts = []

        async def respond(socket, text, **kwargs):
            assert socket is owner and kwargs["voice_guard"]()
            prompts.append(text)
            await server.send_tts_audio({"audio": "YWJj"}, wait_for_end=True, duration_s=0.1)

        monkeypatch.setattr(server, "_handle_user_text", respond)
        await server._handle_face_message(owner, {"type": "voice_conversation", "payload": start})
        await until(lambda: owner.phase() == "listening")
        first = runtime.manual_voice.capture_id
        assert takes(runtime) == 1
        await server._handle_voice_conversation(other, {**start, "action": "end"})
        assert runtime.voice_conversation.session.owner is owner
        await server._handle_voice_conversation(other, {**start, "id": str(uuid.uuid4())})
        assert other.phase() == "off"
        turn = asyncio.create_task(
            runtime._handle_native_event("voice.transcript", {"text": "uno"}),
        )
        await until(lambda: any(msg["type"] == "tts_audio" for msg in owner.messages))
        receipt = {"playback_id": next(msg["payload"]["playback_id"] for msg in owner.messages
                                      if msg["type"] == "tts_audio"), "status": "ended"}
        assert runtime.manual_voice.state == "processing"
        assert not runtime.manual_voice.snapshot()["active"]
        assert owner.phase() == "replying"
        started = {"type": "tts_playback_result", "payload": {**receipt, "status": "playing"}}
        await server._handle_face_message(owner, {
            **started, "payload": {**started["payload"], "playback_id": "old"},
        })
        await server._handle_face_message(other, started)
        assert owner.phase() == "replying"
        await server._handle_face_message(owner, started)
        assert owner.phase() == "speaking"
        assert not server._tts_playback.answer(other, receipt)
        await asyncio.sleep(0.02)
        assert not turn.done() and takes(runtime) == 1
        assert server._tts_playback.answer(owner, receipt)
        await turn
        await until(lambda: owner.phase() == "listening" and takes(runtime) == 2)
        await server._handle_face_message(owner, started)
        assert owner.phase() == "listening"
        assert runtime.manual_voice.capture_id != first
        await runtime._handle_native_event("voice.transcript", {
            "text": "duplicado", "_iris_generation": ("manual", "1", start["client_id"], first),
        })
        assert prompts == ["uno"]
        await server._handle_voice_conversation(owner, {**start, "action": "end"})
        await until(lambda: runtime.voice_conversation.session is None)
        await until(lambda: owner.phase() == "off")
        assert owner.phase() == "off" and not runtime.manual_voice.busy
        assert takes(runtime) == 2

    asyncio.run(scenario())


def test_host_speaker_callback_only_changes_its_live_voice_turn(monkeypatch):
    async def scenario():
        runtime, owner, _, start, _ = wire(monkeypatch)
        callbacks = []

        async def respond(*args, **kwargs):
            callback = server.local_speech_started_callback()
            assert callback is not None and owner.phase() == "replying"
            if callbacks:
                await asyncio.to_thread(callbacks[0])
                await asyncio.sleep(0.02)
                assert owner.phase() == "replying"
            callbacks.append(callback)
            await asyncio.to_thread(callback)
            await until(lambda: owner.phase() == "speaking")

        monkeypatch.setattr(server, "_handle_user_text", respond)
        await server._handle_voice_conversation(owner, start)
        for n in (1, 2):
            await until(lambda: owner.phase() == "listening" and takes(runtime) == n)
            await runtime._handle_native_event("voice.transcript", {"text": "hola"})
        await server._handle_voice_conversation(owner, {**start, "action": "end"})
        await until(lambda: runtime.voice_conversation.session is None)

    asyncio.run(scenario())


@pytest.mark.parametrize("reason", ["hide", "disconnect", "permission", "switch", "end"])
def test_stop_before_or_during_arming_never_starts_another_capture(monkeypatch, reason):
    async def scenario():
        runtime, owner, _, start, route = wire(monkeypatch)
        entered, release = asyncio.Event(), asyncio.Event()
        original = runtime.request.side_effect

        async def rpc(method, params, **kwargs):
            if method == "voice.toggle" and params["action"] == "on":
                entered.set()
                await release.wait()
            return await original(method, params, **kwargs)

        runtime.request.side_effect = rpc
        await server._handle_voice_conversation(owner, start)
        await entered.wait()
        if reason == "hide":
            await server._handle_face_message(owner, {
                "type": "surface_state", "payload": {"visible": False},
            })
        elif reason == "disconnect":
            server._disconnect_surface(owner)
        elif reason == "permission":
            monkeypatch.setattr(server, "_conversational_mic_allowed", lambda: False)
            monkeypatch.setattr(runtime.voice_conversation, "allowed", lambda: False)
            await server._reconcile_runtime_capability_policy("microphone.capture")
        elif reason == "switch":
            route["token"] = "default:1"
        else:
            await server._handle_voice_conversation(owner, {**start, "action": "end"})
        release.set()
        await until(lambda: runtime.voice_conversation.session is None)
        await until(lambda: owner.phase() == "off")
        assert takes(runtime) == 0 and not runtime.manual_voice.busy
        assert owner.phase() == "off"
        if reason == "permission":
            assert owner.messages[-1]["payload"]["code"] == "microphone_blocked"

    asyncio.run(scenario())


@pytest.mark.parametrize("reason", ["end", "timeout"])
def test_end_or_missing_playback_receipt_disables_rearm(monkeypatch, reason):
    async def scenario():
        runtime, owner, _, start, _ = wire(monkeypatch)
        gate = asyncio.Event()

        async def wait(*_):
            await gate.wait()
            return "timeout" if reason == "timeout" else "ended"

        monkeypatch.setattr(server._tts_playback, "wait", wait)

        async def respond(*args, **kwargs):
            await server.send_tts_audio({"audio": "YWJj"}, wait_for_end=True, duration_s=0.1)

        monkeypatch.setattr(server, "_handle_user_text", respond)
        await server._handle_voice_conversation(owner, start)
        await until(lambda: owner.phase() == "listening")
        turn = asyncio.create_task(
            runtime._handle_native_event("voice.transcript", {"text": "hola"}),
        )
        await until(lambda: owner.phase() == "replying")
        if reason == "end":
            await server._handle_voice_conversation(owner, {**start, "action": "end"})
        gate.set()
        await turn
        await until(lambda: runtime.voice_conversation.session is None)
        await until(lambda: owner.phase() == "off")
        assert takes(runtime) == 1 and owner.phase() == "off"

    asyncio.run(scenario())


def test_remote_hidden_and_unconfirmed_requests_never_open_the_microphone(monkeypatch):
    async def scenario():
        runtime, owner, _, start, _ = wire(monkeypatch)
        await server._handle_voice_conversation(owner, {**start, "confirmed": False})
        assert owner.phase() == "off"
        server._surface_registry.update_state(owner, {"visible": False})
        await server._handle_voice_conversation(owner, start)
        assert runtime.voice_conversation is None
        remote = Socket("192.168.1.5")
        server._surface_registry.register_ready(remote, {"role": "face", "visible": True})
        await server._handle_voice_conversation(remote, start)
        assert remote.phase() == "off" and takes(runtime) == 0

    asyncio.run(scenario())


def test_wake_is_suspended_during_listening_and_restored_only_on_end(monkeypatch):
    async def scenario():
        runtime, owner, _, start, _ = wire(monkeypatch, wake=True)

        async def stop(**kwargs):
            runtime.listening, runtime.state = False, "off"
            return runtime.snapshot()

        async def restore(**kwargs):
            runtime.listening, runtime.state = True, "listening"
            return runtime.snapshot()

        runtime.stop_listener = AsyncMock(side_effect=stop)
        runtime.start_listener = AsyncMock(side_effect=restore)
        await server._handle_voice_conversation(owner, start)
        await until(lambda: owner.phase() == "listening")
        runtime.stop_listener.assert_awaited_once_with(persist=False)
        assert not runtime.listening
        runtime.start_listener.assert_not_called()
        await server._handle_voice_conversation(owner, {**start, "action": "end"})
        await until(lambda: runtime.voice_conversation.session is None)
        runtime.start_listener.assert_awaited_once_with(persist=False)
        assert runtime.listening

    asyncio.run(scenario())
