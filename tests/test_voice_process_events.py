"""Native voice workers can emit JSON-RPC on stdio instead of WebSocket."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from core.hermes_wake import HermesWakeRuntime


def test_process_voice_events_reach_manual_capture_without_logging_transcripts():
    async def scenario():
        runtime = HermesWakeRuntime()
        runtime.websocket = SimpleNamespace(close_code=None)
        delivered = []

        async def emit(kind, payload):
            if kind == 'manual_voice.transcript':
                delivered.append(payload['text'])

        async def request(method, params, **kwargs):
            if method == 'voice.toggle':
                return {'enabled': params['action'] == 'on'}
            return {'status': 'recording' if params['action'] == 'start' else 'stopped'}

        runtime.event_handler = emit
        runtime.request = AsyncMock(side_effect=request)
        await runtime.manual_voice.start('face', 'utterance')
        await runtime.manual_voice.stop('face', 'utterance')
        stream = asyncio.StreamReader()
        stream.feed_data(b'ordinary service log\n')
        for kind, payload in [('voice.status', {'state': 'transcribing'}),
                              ('voice.transcript', {'text': 'hola'}),
                              ('voice.status', {'state': 'idle'})]:
            stream.feed_data((json.dumps({'jsonrpc': '2.0', 'method': 'event',
                                         'params': {'type': kind, 'payload': payload}}) + '\n').encode())
        stream.feed_eof()
        runtime.process = SimpleNamespace(stdout=stream)
        await runtime._drain_server_logs()
        assert list(runtime._recent_logs) == ['ordinary service log']
        while not runtime._events.empty():
            kind, payload = await runtime._events.get()
            await runtime._handle_native_event(kind, payload)
        assert delivered == ['hola']
        assert runtime.manual_voice.state == 'off'
        assert runtime.manual_voice._timer is None
        # If a newer Hermes also delivers the event via WS, don't submit twice.
        await runtime._handle_native_event('voice.transcript', {'text': 'hola'})
        assert delivered == ['hola']
    asyncio.run(scenario())


def test_process_logs_cannot_inject_unrelated_or_malformed_events():
    async def scenario():
        runtime = HermesWakeRuntime()
        stream = asyncio.StreamReader()
        for frame in [[], {'method': 'event'},
                      {'jsonrpc': '2.0', 'method': 'event', 'params': {'type': 'wake.detected', 'payload': {}}},
                      {'jsonrpc': '2.0', 'method': 'event', 'params': {'type': 'voice.transcript', 'payload': 'bad'}}]:
            stream.feed_data((json.dumps(frame) + '\n').encode())
        stream.feed_eof()
        runtime.process = SimpleNamespace(stdout=stream)
        await runtime._drain_server_logs()
        assert runtime._events.empty()
    asyncio.run(scenario())
