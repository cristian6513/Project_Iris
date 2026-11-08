"""F16: provisional STT is display-only, bounded and owned by one native take."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import yaml
from fastapi import HTTPException
from pydantic import ValidationError

from core import hermes_wake as wake
from core.hermes_voice import MAX_PARTIAL_CHARS
from test_manual_voice import runtime_fixture, request_fixture


@pytest.mark.parametrize('stop', [False, True])
def test_many_partials_then_one_final_create_one_turn(stop):
    from core import websocket_server as server
    async def run():
        runtime, _ = runtime_fixture(wake=True)
        runtime.event_handler = server.handle_hermes_wake_event
        with patch.object(server, 'broadcast_to_face', new=AsyncMock()) as broadcast, \
             patch.object(server, '_handle_user_text', new=AsyncMock()) as turn:
            await runtime.manual_voice.start('face', 'one')
            for text in ['hola', 'hola', 'hola Iris', '<img src=x onerror=evil()>']:
                await runtime._handle_native_event('voice.partial', {'text': text})
            turn.assert_not_awaited()
            if stop:
                await runtime.manual_voice.stop('face', 'one')
            await runtime._handle_native_event('voice.transcript', {'text': 'hola Iris'})
            await runtime._handle_native_event('voice.transcript', {'text': 'duplicado'})
            await runtime._handle_native_event('voice.partial', {'text': 'tardío'})
        turn.assert_awaited_once_with(None, 'hola Iris')
        partials = [c.args[1] for c in broadcast.await_args_list if c.args[0] == 'voice_partial']
        assert [p['text'] for p in partials] == ['hola', 'hola Iris', '<img src=x onerror=evil()>']
        assert [p['sequence'] for p in partials] == [1, 2, 3]
        assert all(p['capture_id'] == 'one' and p['client_id'] == 'face' for p in partials)
        assert not runtime.manual_voice.partial
        assert runtime.listening  # Resume only the previously active Wake.
    asyncio.run(run())


@pytest.mark.parametrize('payload', [{'text': {}}, {'text': None}, {}, {'text': '  '}])
def test_invalid_partials_are_not_turns_or_ui_events(payload):
    async def run():
        runtime, events = runtime_fixture()
        await runtime.manual_voice.start('face', 'one')
        await runtime._handle_native_event('voice.partial', payload)
        assert not any(k.endswith('.partial') or k.endswith('.transcript') for k, _ in events)
        await runtime.manual_voice.cancel('face', 'one')
    asyncio.run(run())


def test_partial_is_bounded_and_disappears_after_transport_loss():
    async def run():
        runtime, events = runtime_fixture()
        await runtime.manual_voice.start('face', 'one')
        await runtime._handle_native_event('voice.partial', {'text': 'x' * 100_000})
        assert len(events[-1][1]['text']) == MAX_PARTIAL_CHARS
        runtime.manual_voice.disconnected()
        await runtime._handle_native_event('voice.partial', {'text': 'late'})
        assert not runtime.manual_voice.partial
        assert not any(k.endswith('.transcript') for k, _ in events)
    asyncio.run(run())


def test_cancel_then_new_take_rejects_queued_old_partials_and_final():
    async def run():
        runtime, events = runtime_fixture()
        await runtime.manual_voice.start('face', 'one')
        old = {'text': 'viejo', '_iris_generation': runtime._voice_generation()}
        await runtime.manual_voice.cancel('face', 'one')
        runtime.websocket = SimpleNamespace(close_code=None)
        await runtime.manual_voice.start('face', 'two')
        await runtime._handle_native_event('voice.partial', old)
        await runtime._handle_native_event('voice.transcript', old)
        assert not any(k.endswith('.partial') or k.endswith('.transcript') for k, _ in events)
        await runtime._handle_native_event('voice.partial', {'text': 'nuevo'})
        assert events[-1][1]['capture_id'] == 'two'
        await runtime.manual_voice.cancel('face', 'two')
    asyncio.run(run())


@pytest.mark.parametrize('failure', [False, True])
def test_previous_turn_cleanup_cannot_release_or_poison_new_take_after_disconnect(failure):
    async def run():
        runtime, events = runtime_fixture()
        entered, finish = asyncio.Event(), asyncio.Event()
        async def emit(kind, payload):
            events.append((kind, dict(payload)))
            if kind == 'manual_voice.transcript':
                entered.set()
                await finish.wait()
                if failure:
                    raise RuntimeError('old turn failed')
        runtime.event_handler = emit
        voice = runtime.manual_voice
        await voice.start('face', 'one')
        task = asyncio.create_task(runtime._handle_native_event('voice.transcript', {'text': 'primera orden'}))
        await asyncio.wait_for(entered.wait(), 2)
        voice.disconnected()
        runtime.state = 'disconnected'
        runtime.websocket = SimpleNamespace(close_code=None)
        await voice.start('face', 'two')
        calls = runtime.request.await_count
        finish.set()
        await asyncio.wait_for(task, 2)
        assert voice.state == 'recording' and voice.capture_id == 'two'
        assert not voice.error and runtime.request.await_count == calls
        await runtime._handle_native_event('voice.partial', {'text': 'segunda toma'})
        assert events[-1][1]['capture_id'] == 'two'
        await voice.cancel('face', 'two')
    asyncio.run(run())


def test_stream_without_partials_still_delivers_final_once():
    async def run():
        runtime, events = runtime_fixture()
        await runtime.manual_voice.start('face', 'one')
        await runtime._handle_native_event('voice.status', {'state': 'transcribing'})
        await runtime._handle_native_event('voice.transcript', {'text': 'resultado del WAV'})
        assert [p['text'] for k, p in events if k == 'manual_voice.transcript'] == ['resultado del WAV']
    asyncio.run(run())


def test_wake_partials_and_duplicate_final_use_one_detected_take():
    async def run():
        runtime, events = runtime_fixture()
        await runtime._handle_native_event('voice.partial', {'text': 'sin toma'})
        await runtime._handle_native_event('wake.detected', {})
        capture = runtime._wake_capture_id
        for text in ['mi agenda', 'mi agenda', 'mi agenda hoy']:
            await runtime._handle_native_event('voice.partial', {'text': text})
        await runtime._handle_native_event('voice.transcript', {'text': 'mi agenda hoy'})
        await runtime._handle_native_event('voice.transcript', {'text': 'mi agenda hoy'})
        await runtime._handle_native_event('voice.partial', {'text': 'demasiado tarde'})
        partials = [p for k, p in events if k == 'voice.partial']
        assert len(partials) == 2 and all(p['capture_id'] == capture for p in partials)
        assert len([k for k, _ in events if k == 'voice.transcript']) == 1
    asyncio.run(run())


def test_partial_flood_uses_one_queue_slot_before_final():
    async def run():
        runtime, events = runtime_fixture()
        await runtime.manual_voice.start('face', 'one')
        for i in range(10000):
            await runtime._queue_native_event('voice.partial', {'text': str(i)})
        assert runtime._events.qsize() == 1
        await runtime._queue_native_event('voice.transcript', {'text': 'final'})
        worker = asyncio.create_task(runtime._consume_events())
        await asyncio.wait_for(runtime._events.join(), 2)
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        assert [p['text'] for k, p in events if k == 'manual_voice.partial'] == ['9999']
        assert [p['text'] for k, p in events if k == 'manual_voice.transcript'] == ['final']
    asyncio.run(run())


def test_stopping_wake_discards_late_partial_and_final():
    async def run():
        runtime, events = runtime_fixture()
        await runtime._handle_native_event('wake.detected', {})
        await runtime._handle_native_event('voice.partial', {'text': 'borrador'})
        await runtime.stop_listener(persist=False)
        await runtime._handle_native_event('voice.partial', {'text': 'tardío'})
        await runtime._handle_native_event('voice.transcript', {'text': 'tardío'})
        assert len([k for k, _ in events if k == 'voice.partial']) == 1
        assert not any(k == 'voice.transcript' for k, _ in events)
    asyncio.run(run())


def test_stdio_partials_are_protocol_events_never_recent_logs(caplog):
    async def run():
        runtime, _ = runtime_fixture()
        await runtime.manual_voice.start('face', 'one')
        stream = asyncio.StreamReader()
        message = {'jsonrpc': '2.0', 'method': 'event', 'params': {'type': 'voice.partial', 'payload': {'text': 'private draft'}}}
        stream.feed_data(json.dumps(message).encode() + b'\n'); stream.feed_eof()
        runtime.process = SimpleNamespace(stdout=stream)
        await runtime._drain_server_logs()
        assert runtime._events.qsize() == 1
        assert not runtime._recent_logs and 'private draft' not in caplog.text
        await runtime.manual_voice.cancel('face', 'one')
    asyncio.run(run())


@pytest.fixture
def config_path(tmp_path, monkeypatch):
    path = tmp_path / 'hermes' / 'config.yaml'
    path.parent.mkdir()
    monkeypatch.setattr(wake, 'get_hermes_config_path', lambda: path)
    return path


def test_config_read_is_passive_and_missing_defaults_off(config_path):
    runtime, _ = runtime_fixture()
    response = asyncio.run(wake.get_hermes_voice_status(request_fixture(runtime)))
    assert response['settings'] == {'streaming': False}
    assert not config_path.exists()
    runtime.request.assert_not_awaited()


@pytest.mark.parametrize('enabled', [False, True])
def test_config_merge_preserves_provider_secrets_wake_and_unknown_keys(config_path, enabled):
    source = {'stt': {'provider': 'openai', 'openai': {'api_key': 'private'}, 'future': 1}, 'wake_word': {'enabled': True}, 'model': 'keep'}
    config_path.write_text(yaml.safe_dump(source), encoding='utf-8')
    runtime, _ = runtime_fixture()
    response = asyncio.run(wake.update_hermes_voice_settings(wake.HermesVoiceSettings(streaming=enabled), request_fixture(runtime)))
    source['stt']['streaming'] = enabled
    assert yaml.safe_load(config_path.read_text(encoding='utf-8')) == source
    assert response == {'streaming': enabled}
    status = asyncio.run(wake.get_hermes_voice_status(request_fixture(runtime)))
    assert status['settings'] == response
    assert 'private' not in json.dumps(status) and 'openai' not in json.dumps(status)
    runtime.request.assert_not_awaited()


@pytest.mark.parametrize('raw', ['stt: [broken]', 'stt: 12', '[not-a-map]', 'stt: ['])
def test_malformed_config_is_preserved_and_explained(config_path, raw):
    config_path.write_text(raw, encoding='utf-8')
    runtime, _ = runtime_fixture()
    with pytest.raises(HTTPException) as error:
        asyncio.run(wake.update_hermes_voice_settings(wake.HermesVoiceSettings(streaming=True), request_fixture(runtime)))
    assert error.value.status_code == 422
    assert config_path.read_text(encoding='utf-8') == raw


def test_yaml_diagnostic_does_not_expose_a_credential_line(config_path):
    config_path.write_text('stt:\n  api_key: [private-secret-do-not-display', encoding='utf-8')
    runtime, _ = runtime_fixture()
    with pytest.raises(HTTPException) as error:
        asyncio.run(wake.update_hermes_voice_settings(wake.HermesVoiceSettings(streaming=True), request_fixture(runtime)))
    assert error.value.status_code == 422
    assert 'private-secret' not in error.value.detail
    assert 'config.yaml' in error.value.detail


@pytest.mark.parametrize('state', ['recording', 'arming', 'transcribing', 'processing'])
def test_settings_cannot_change_mid_take(config_path, state):
    runtime, _ = runtime_fixture()
    runtime.state = state
    with pytest.raises(HTTPException) as error:
        asyncio.run(wake.update_hermes_voice_settings(wake.HermesVoiceSettings(streaming=True), request_fixture(runtime)))
    assert error.value.status_code == 409 and not config_path.exists()


@pytest.mark.parametrize('peer,headers', [('192.168.0.2', {}), ('127.0.0.1', {'origin': 'https://evil.test'}), ('127.0.0.1', {'sec-fetch-site': 'cross-site'})])
def test_setting_requires_local_peer_and_origin(config_path, peer, headers):
    runtime, _ = runtime_fixture()
    with pytest.raises(HTTPException) as error:
        asyncio.run(wake.update_hermes_voice_settings(wake.HermesVoiceSettings(streaming=True), request_fixture(runtime, peer=peer, headers=headers)))
    assert error.value.status_code == 403 and not config_path.exists()


@pytest.mark.parametrize('value', ['yes', 1, None, {}])
def test_settings_schema_does_not_coerce_untrusted_flags(value):
    with pytest.raises(ValidationError):
        wake.HermesVoiceSettings(streaming=value)
