"""Manual voice contracts: no hardware, accounts or model downloads."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from core.hermes_wake import (
    HermesWakeRuntime, HermesVoiceUpdate, get_hermes_voice_status,
    update_hermes_voice_runtime,
)


def runtime_fixture(*, wake=False):
    runtime = HermesWakeRuntime()
    runtime.websocket = SimpleNamespace(close_code=None)
    runtime.listening = wake
    runtime.state = 'listening' if wake else 'off'
    events = []

    async def emit(kind, payload):
        events.append((kind, dict(payload)))

    async def rpc(method, params, **kwargs):
        if method == 'voice.toggle':
            return {'enabled': params['action'] == 'on'}
        if method == 'voice.record':
            return {'status': 'recording' if params['action'] == 'start' else 'stopped'}
        return {'paused': True, 'resumed': True}

    async def close():
        runtime.websocket = None
        runtime.listening = False
        runtime.manual_voice.disconnected()

    runtime.request = AsyncMock(side_effect=rpc)
    runtime.close = AsyncMock(side_effect=close)
    runtime.event_handler = emit
    return runtime, events


def test_manual_utterance_never_starts_wake_or_new_session():
    async def scenario():
        runtime, events = runtime_fixture()
        runtime.state = 'error'  # A broken wake phrase does not block manual voice.
        voice = runtime.manual_voice
        await voice.start('room', 'one')
        assert voice.snapshot()['active'] is True
        await runtime._handle_native_event('voice.transcript', {'text': 'abre mi agenda'})
        await runtime._handle_native_event('voice.transcript', {'text': 'late duplicate'})
        assert [payload['text'] for kind, payload in events if kind == 'manual_voice.transcript'] == ['abre mi agenda']
        assert not any(kind == 'wake.detected' for kind, _ in events)
        assert all(not call.args[0].startswith('wake.') for call in runtime.request.await_args_list)
        assert not voice.busy and not runtime.listening
        assert voice._timer is None
    asyncio.run(scenario())


def test_stop_is_not_cancel_and_delayed_listening_cannot_reopen_capture():
    async def scenario():
        runtime, _ = runtime_fixture()
        voice = runtime.manual_voice
        await voice.start('room', 'one')
        await voice.stop('room', 'one')
        await runtime._handle_native_event('voice.status', {'state': 'listening'})
        assert voice.state == 'transcribing'
        assert any(call.args == ('voice.record', {'action': 'stop'}) for call in runtime.request.await_args_list)
        await voice.cancel('room', 'one')
        assert runtime.close.await_count == 1
    asyncio.run(scenario())


@pytest.mark.parametrize('payload', [{}, {'text': 'stop', 'stop_phrase': True}, {'no_speech_limit': True}])
def test_empty_or_stop_phrase_does_not_create_a_user_turn(payload):
    async def scenario():
        runtime, events = runtime_fixture()
        await runtime.manual_voice.start('room', 'one')
        await runtime._handle_native_event('voice.transcript', payload)
        assert not runtime.manual_voice.busy
        assert not any(kind == 'manual_voice.transcript' for kind, _ in events)
    asyncio.run(scenario())


@pytest.mark.parametrize('disable_during_capture', [False, True])
def test_only_previously_active_wake_can_resume(disable_during_capture):
    async def scenario():
        runtime, _ = runtime_fixture(wake=True)
        await runtime.manual_voice.start('room', 'one')
        if disable_during_capture:
            await runtime.stop_listener(persist=False)
        await runtime._handle_native_event('voice.transcript', {'text': 'hola'})
        methods = [call.args[0] for call in runtime.request.await_args_list]
        assert ('wake.resume' in methods) is not disable_during_capture
        assert 'wake.start' not in methods
    asyncio.run(scenario())


def test_cancel_discards_late_result_and_retires_only_owned_transport():
    async def scenario():
        runtime, events = runtime_fixture(wake=True)
        voice = runtime.manual_voice
        await voice.start('room', 'one')
        await voice.cancel('room', 'one')
        await runtime._handle_native_event('voice.transcript', {'text': 'late command'})
        assert runtime.close.await_count == 1
        assert not voice.busy
        assert not runtime.listening
        assert not any(kind.endswith('transcript') for kind, _ in events)
        assert 'Configuración' in voice.error
    asyncio.run(scenario())


def test_other_window_cannot_stop_capture_and_cancel_before_start_is_remembered():
    async def scenario():
        runtime, _ = runtime_fixture()
        voice = runtime.manual_voice
        with pytest.raises(RuntimeError):
            await voice.cancel('companion', 'cancel-before-start')
        with pytest.raises(RuntimeError, match='cancelada'):
            await voice.start('companion', 'cancel-before-start')
        assert runtime.request.await_count == 0
        await voice.start('room', 'one')
        with pytest.raises(RuntimeError):
            await voice.stop('companion', 'one')
        with pytest.raises(RuntimeError, match='ocupado'):
            await voice.start('companion', 'two')
        with pytest.raises(RuntimeError, match='manual'):
            await runtime.start_listener(persist=False)
        assert voice.state == 'recording'
        await voice.cancel('room', 'one')
    asyncio.run(scenario())


def test_record_failure_disarms_voice_and_timeout_cancels_without_transcribing():
    async def scenario():
        runtime, _ = runtime_fixture()
        voice = runtime.manual_voice
        runtime.request.side_effect = [{'enabled': True}, {'status': 'busy'}, {'enabled': False}]
        with pytest.raises(RuntimeError, match='iniciar'):
            await voice.start('room', 'one')
        assert runtime.close.await_count == 1
        assert not voice.busy
        runtime, _ = runtime_fixture()
        voice = runtime.manual_voice
        voice.MAX_CAPTURE_SECONDS = 0
        await voice.start('room', 'one')
        timer = voice._timer
        await timer
        assert runtime.close.await_count == 1
        assert not any(call.args == ('voice.record', {'action': 'stop'}) for call in runtime.request.await_args_list)
    asyncio.run(scenario())


def request_fixture(runtime, *, peer='127.0.0.1', headers=None):
    return SimpleNamespace(headers=headers or {}, client=SimpleNamespace(host=peer),
        url=SimpleNamespace(hostname='127.0.0.1', scheme='http', port=8765),
        app=SimpleNamespace(state=SimpleNamespace(hermes_wake=runtime)))


@pytest.mark.parametrize('peer,headers,verdict,confirmed', [
    ('192.168.1.2', {}, 'allow', True),
    ('127.0.0.1', {'sec-fetch-site': 'cross-site'}, 'allow', True),
    ('127.0.0.1', {'origin': 'https://evil.example'}, 'allow', True),
    ('127.0.0.1', {}, 'deny', True), ('127.0.0.1', {}, 'ask', False),
    ('127.0.0.1', {}, 'allow', False),
])
def test_manual_capture_requires_local_origin_policy_and_explicit_activation(peer, headers, verdict, confirmed):
    runtime, _ = runtime_fixture()
    with patch('core.hermes_wake.CapabilityPermissionStore') as store:
        store.return_value.evaluate.return_value = {'verdict': verdict}
        with pytest.raises(HTTPException) as error:
            asyncio.run(update_hermes_voice_runtime(HermesVoiceUpdate(
                action='start', client_id='room', capture_id='one', confirmed=confirmed),
                request_fixture(runtime, peer=peer, headers=headers)))
    assert error.value.status_code == 403
    assert runtime.request.await_count == 0


def test_status_is_read_only_and_cancel_works_even_after_policy_revoked():
    async def scenario():
        runtime, _ = runtime_fixture()
        request = request_fixture(runtime)
        assert (await get_hermes_voice_status(request))['state'] == 'off'
        assert runtime.request.await_count == 0
        with patch('core.hermes_wake.CapabilityPermissionStore') as store:
            store.return_value.evaluate.return_value = {'verdict': 'allow'}
            response = await update_hermes_voice_runtime(HermesVoiceUpdate(
                action='start', client_id='room', capture_id='one', confirmed=True), request)
            assert json.loads(response.body)['state'] == 'recording'
            store.return_value.evaluate.return_value = {'verdict': 'deny'}
            response = await update_hermes_voice_runtime(HermesVoiceUpdate(
                action='cancel', client_id='room', capture_id='one'), request)
            assert not json.loads(response.body)['busy']
            assert store.return_value.evaluate.call_count == 1
    asyncio.run(scenario())


@pytest.mark.parametrize('manual_stop', [False, True])
def test_native_voice_event_sequence_delivers_once_then_releases(manual_stop):
    from core import websocket_server as server
    async def scenario():
        runtime, events = runtime_fixture()
        voice = runtime.manual_voice
        with patch.object(server, 'broadcast_to_face', new=AsyncMock()) as broadcast, \
             patch.object(server, '_handle_user_text', new=AsyncMock()) as turn:
            runtime.event_handler = server.handle_hermes_wake_event
            await voice.start('face-window', 'utterance-one')
            await runtime._handle_native_event('voice.status', {'state': 'listening'})
            if manual_stop:
                await voice.stop('face-window', 'utterance-one')
            await runtime._handle_native_event('voice.status', {'state': 'transcribing'})
            await runtime._handle_native_event('voice.transcript', {'text': 'hola iris'})
            await runtime._handle_native_event('voice.status', {'state': 'idle'})
            await runtime._handle_native_event('voice.transcript', {'text': 'late duplicate'})
        turn.assert_awaited_once_with(None, 'hola iris')
        sent = [call.args[1] for call in broadcast.await_args_list if call.args[0] == 'voice_transcript']
        assert len(sent) == 1
        assert sent[0]['accepted'] and sent[0]['capture_id'] == 'utterance-one'
        assert sent[0]['client_id'] == 'face-window'
        assert voice.state == 'off' and not voice.error
    asyncio.run(scenario())


def test_manual_transcript_uses_current_iris_conversation_not_wake_routing():
    from core import websocket_server as server
    async def scenario():
        with patch.object(server, 'broadcast_to_face', new=AsyncMock()) as broadcast, \
             patch.object(server, '_handle_user_text', new=AsyncMock()) as turn:
            await server.handle_hermes_wake_event('manual_voice.transcript', {'text': 'hola iris'})
        turn.assert_awaited_once_with(None, 'hola iris')
        assert [call.args[0] for call in broadcast.await_args_list] == ['voice_transcript']
    asyncio.run(scenario())


def test_failed_transport_close_reports_unknown_not_a_released_microphone():
    async def scenario():
        runtime, _ = runtime_fixture()
        voice = runtime.manual_voice
        await voice.start('room', 'one')
        runtime.close = AsyncMock(side_effect=RuntimeError('process locked'))
        with pytest.raises(RuntimeError, match='confirmar'):
            await voice.cancel('room', 'one')
        assert voice.state == 'unknown'
        assert voice.snapshot()['active'] and voice.busy
    asyncio.run(scenario())


def test_reading_wake_status_cannot_clear_arming_and_allow_parallel_manual_capture():
    async def scenario():
        runtime, _ = runtime_fixture()
        runtime.state = 'arming'
        runtime.request = AsyncMock(return_value={'listening': False})
        await runtime.status()
        assert runtime.state == 'arming'
        with pytest.raises(RuntimeError, match='usando la voz'):
            await runtime.manual_voice.start('room', 'one')
        assert runtime.request.await_count == 1
    asyncio.run(scenario())
