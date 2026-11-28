"""The packaged Companion may use manual voice; nothing else gains access.

The Companion's Tauri webview (http://tauri.localhost) calls Core on 127.0.0.1, so WebView2
sends ``Sec-Fetch-Site: cross-site`` and an Origin that is never same-origin with Core. It
proves itself with the pairing token. Synthetic tokens only; no real profile, Hermes or audio.
"""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from core import hermes_wake
from core.hermes_wake import (
    HermesVoiceSettings, HermesVoiceUpdate, HermesWakeRuntime, HermesWakeRuntimeUpdate,
    HermesWakeSettingsUpdate, get_hermes_voice_status, update_hermes_voice_runtime,
    update_hermes_voice_settings, update_hermes_wake_runtime, update_hermes_wake_settings,
)

GOOD = 'F' * 43  # Shape of a real token_urlsafe(32) value; never a real token.
WRONG = 'W' * 43
COMPANION = {'origin': 'http://tauri.localhost', 'sec-fetch-site': 'cross-site'}


def runtime_fixture():
    runtime = HermesWakeRuntime()
    runtime.websocket = SimpleNamespace(close_code=None)
    runtime.state = 'off'

    async def rpc(method, params, **kwargs):  # Same replies as tests/test_manual_voice.py.
        if method == 'voice.toggle':
            return {'enabled': params['action'] == 'on'}
        if method == 'voice.record':
            return {'status': 'recording' if params['action'] == 'start' else 'stopped'}
        return {'paused': True, 'resumed': True}

    async def close():
        runtime.websocket = None
        runtime.manual_voice.disconnected()

    runtime.request = AsyncMock(side_effect=rpc)
    runtime.close = AsyncMock(side_effect=close)
    runtime.event_handler = AsyncMock()
    return runtime


def request_fixture(runtime, headers, *, peer='127.0.0.1', host='127.0.0.1'):
    return SimpleNamespace(headers=dict(headers), client=SimpleNamespace(host=peer),
        url=SimpleNamespace(hostname=host, scheme='http', port=8765),
        app=SimpleNamespace(state=SimpleNamespace(hermes_wake=runtime)))


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    """Synthetic pairing token and Hermes STT settings: no profile or config.yaml is read or written.

    The settings route writes through ``configure_voice_settings``; the config path also points at
    a temporary folder, so a writer this fixture misses still cannot reach a real profile.
    """
    monkeypatch.setattr(hermes_wake, 'get_hermes_config_path', lambda: tmp_path / 'hermes' / 'config.yaml')
    monkeypatch.setattr(hermes_wake, 'is_valid_companion_token', lambda value: value == GOOD)
    monkeypatch.setattr(hermes_wake, 'read_voice_streaming_settings', lambda: {'streaming': False})
    monkeypatch.setattr(hermes_wake, 'configure_voice_settings',
                        lambda **changes: {key: value for key, value in changes.items() if value is not None})
    monkeypatch.setattr(hermes_wake, 'configure_voice_streaming', lambda enabled: {'streaming': enabled})


def allow_microphone(verdict='allow'):
    store = patch('core.hermes_wake.CapabilityPermissionStore')
    mock = store.start()
    mock.return_value.evaluate.return_value = {'verdict': verdict}
    return store


def start(confirmed=True):
    return HermesVoiceUpdate(action='start', client_id='companion', capture_id='one', confirmed=confirmed)


# The paired Companion: status, the streaming checkbox and «Hablar ahora».

@pytest.mark.parametrize('origin', ['http://tauri.localhost', 'https://tauri.localhost', 'tauri://localhost'])
@pytest.mark.parametrize('fetch_site', ['cross-site', None])
def test_paired_companion_reads_status_with_settings(origin, fetch_site):
    headers = {'origin': origin, 'x-iris-companion-token': GOOD}
    if fetch_site:
        headers['sec-fetch-site'] = fetch_site
    snapshot = asyncio.run(get_hermes_voice_status(request_fixture(runtime_fixture(), headers)))
    assert snapshot['settings'] == {'streaming': False}
    assert snapshot['state'] == 'off'


def test_paired_companion_saves_streaming_setting():
    request = request_fixture(runtime_fixture(), {**COMPANION, 'x-iris-companion-token': GOOD})
    assert asyncio.run(update_hermes_voice_settings(HermesVoiceSettings(streaming=True), request)) == {
        'streaming': True}


def test_paired_companion_saves_the_speech_language():
    request = request_fixture(runtime_fixture(), {**COMPANION, 'x-iris-companion-token': GOOD})
    assert asyncio.run(update_hermes_voice_settings(HermesVoiceSettings(language='pt'), request)) == {
        'language': 'pt'}


def test_paired_companion_starts_and_cancels_manual_capture_with_permission():
    runtime = runtime_fixture()
    request = request_fixture(runtime, {**COMPANION, 'x-iris-companion-token': GOOD})
    store = allow_microphone()
    try:
        response = asyncio.run(update_hermes_voice_runtime(start(), request))
        assert response.status_code == 200
        assert json.loads(response.body)['state'] == 'recording'
        response = asyncio.run(update_hermes_voice_runtime(HermesVoiceUpdate(
            action='cancel', client_id='companion', capture_id='one'), request))
        assert not json.loads(response.body)['busy']
    finally:
        store.stop()


@pytest.mark.parametrize('verdict,confirmed,code', [
    ('allow', False, 'confirmation_required'),
    ('ask', False, 'confirmation_required'),
    ('deny', True, 'microphone_blocked'),
])
def test_paired_companion_still_obeys_microphone_permission(verdict, confirmed, code):
    runtime = runtime_fixture()
    request = request_fixture(runtime, {**COMPANION, 'x-iris-companion-token': GOOD})
    store = allow_microphone(verdict)
    try:
        response = asyncio.run(update_hermes_voice_runtime(start(confirmed), request))
    finally:
        store.stop()
    assert response.status_code == 403
    assert json.loads(response.body)['code'] == code
    assert runtime.request.await_count == 0


# What stays refused.

REFUSED = [
    pytest.param({**COMPANION}, '127.0.0.1', '127.0.0.1', id='tauri-without-token'),
    pytest.param({**COMPANION, 'x-iris-companion-token': ''}, '127.0.0.1', '127.0.0.1', id='tauri-empty-token'),
    pytest.param({**COMPANION, 'x-iris-companion-token': WRONG}, '127.0.0.1', '127.0.0.1', id='tauri-wrong-token'),
    pytest.param({'origin': 'http://tauri.localhost', 'x-iris-companion-token': WRONG}, '127.0.0.1', '127.0.0.1',
                 id='tauri-wrong-token-no-fetch-site'),
    pytest.param({'origin': 'https://evil.example', 'sec-fetch-site': 'cross-site'}, '127.0.0.1', '127.0.0.1',
                 id='other-origin-cross-site'),
    pytest.param({'origin': 'https://evil.example', 'sec-fetch-site': 'cross-site', 'x-iris-companion-token': GOOD},
                 '127.0.0.1', '127.0.0.1', id='other-origin-with-valid-token'),
    pytest.param({'origin': 'http://tauri.localhost.evil.example', 'x-iris-companion-token': GOOD},
                 '127.0.0.1', '127.0.0.1', id='lookalike-origin'),
    pytest.param({'sec-fetch-site': 'cross-site', 'x-iris-companion-token': GOOD}, '127.0.0.1', '127.0.0.1',
                 id='token-without-origin-cross-site'),
    pytest.param({**COMPANION, 'x-iris-companion-token': GOOD}, '192.168.1.25', '127.0.0.1',
                 id='companion-from-lan-peer'),
    pytest.param({**COMPANION, 'x-iris-companion-token': GOOD}, '127.0.0.1', 'rebound.example',
                 id='companion-on-rebound-host'),
]


@pytest.mark.parametrize('headers,peer,host', REFUSED)
def test_refused_callers_cannot_read_status(headers, peer, host):
    with pytest.raises(HTTPException) as error:
        asyncio.run(get_hermes_voice_status(request_fixture(runtime_fixture(), headers, peer=peer, host=host)))
    assert error.value.status_code == 403


@pytest.mark.parametrize('headers,peer,host', REFUSED)
@pytest.mark.parametrize('change', [{'streaming': True}, {'language': 'en'}])
def test_refused_callers_cannot_change_stt_settings(headers, peer, host, change):
    request = request_fixture(runtime_fixture(), headers, peer=peer, host=host)
    with pytest.raises(HTTPException) as error:
        asyncio.run(update_hermes_voice_settings(HermesVoiceSettings(**change), request))
    assert error.value.status_code == 403


@pytest.mark.parametrize('headers,peer,host', REFUSED)
def test_refused_callers_cannot_open_the_microphone(headers, peer, host):
    runtime = runtime_fixture()
    store = allow_microphone()
    try:
        with pytest.raises(HTTPException) as error:
            asyncio.run(update_hermes_voice_runtime(start(), request_fixture(runtime, headers, peer=peer, host=host)))
    finally:
        store.stop()
    assert error.value.status_code == 403
    assert runtime.request.await_count == 0


def test_token_is_checked_again_in_the_route_not_only_in_core_middleware(monkeypatch):
    """The Wizard mounts this router and its middleware does not know the pairing token."""
    seen = []
    monkeypatch.setattr(hermes_wake, 'is_valid_companion_token', lambda value: seen.append(value) or False)
    with pytest.raises(HTTPException):
        asyncio.run(get_hermes_voice_status(request_fixture(
            runtime_fixture(), {**COMPANION, 'x-iris-companion-token': GOOD}))
        )
    assert seen == [GOOD]


def test_wake_controls_do_not_accept_the_companion():
    """Its UI only reads Wake; turning Wake on or off stays with IRIS OS and the Wizard."""
    request = request_fixture(runtime_fixture(), {**COMPANION, 'x-iris-companion-token': GOOD})
    with pytest.raises(HTTPException) as error:
        asyncio.run(update_hermes_wake_runtime(HermesWakeRuntimeUpdate(active=True), request))
    assert error.value.status_code == 403
    with pytest.raises(HTTPException) as error:
        asyncio.run(update_hermes_wake_settings(HermesWakeSettingsUpdate(enabled=True), request))
    assert error.value.status_code == 403


def test_browser_same_origin_room_is_unchanged():
    request = request_fixture(runtime_fixture(), {'origin': 'http://127.0.0.1:8765', 'sec-fetch-site': 'same-origin'})
    assert asyncio.run(get_hermes_voice_status(request))['settings'] == {'streaming': False}


# Through Core's real HTTP middleware, as WebView2 sends it.

@pytest.fixture
def core_client(monkeypatch):
    from core import websocket_server as ws
    from core.config import IRISConfig

    cfg = IRISConfig.model_construct(lan_access=False, websocket_host='127.0.0.1', llm_provider='')
    monkeypatch.setattr(IRISConfig, 'load', classmethod(lambda cls: cfg))
    monkeypatch.setattr(ws, 'is_valid_companion_token', lambda value: value == GOOD)
    runtime = runtime_fixture()
    monkeypatch.setattr(ws.app.state, 'hermes_wake', runtime, raising=False)
    client = TestClient(ws.app, base_url='http://127.0.0.1:8766', client=('127.0.0.1', 51000))
    return client, runtime


def webview_headers(token=None, origin='http://tauri.localhost'):
    headers = {'Origin': origin, 'Sec-Fetch-Site': 'cross-site', 'Sec-Fetch-Mode': 'cors'}
    if token is not None:
        headers['X-IRIS-Companion-Token'] = token
    return headers


def test_core_http_admits_paired_companion_on_manual_voice(core_client):
    client, runtime = core_client
    status = client.get('/api/hermes-voice/status', headers=webview_headers(GOOD))
    assert status.status_code == 200, status.text
    assert status.json()['settings'] == {'streaming': False}
    saved = client.post('/api/hermes-voice/settings', json={'streaming': True}, headers=webview_headers(GOOD))
    assert saved.status_code == 200, saved.text
    saved = client.post('/api/hermes-voice/settings', json={'language': 'fr'}, headers=webview_headers(GOOD))
    assert saved.status_code == 200, saved.text
    assert saved.json() == {'language': 'fr'}
    refused = client.post('/api/hermes-voice/settings', json={'language': 'xx'}, headers=webview_headers(GOOD))
    assert refused.status_code == 422, refused.text
    store = allow_microphone()
    try:
        started = client.post('/api/hermes-voice/runtime', headers=webview_headers(GOOD), json={
            'action': 'start', 'client_id': 'companion', 'capture_id': 'one', 'confirmed': True})
    finally:
        store.stop()
    assert started.status_code == 200, started.text
    assert started.json()['state'] == 'recording'
    assert runtime.request.await_count >= 1


@pytest.mark.parametrize('token,origin', [
    (None, 'http://tauri.localhost'),
    (WRONG, 'http://tauri.localhost'),
    (GOOD, 'https://evil.example'),
    (None, 'https://evil.example'),
])
def test_core_http_refuses_unpaired_or_foreign_callers(core_client, token, origin):
    client, runtime = core_client
    headers = webview_headers(token, origin)
    assert client.get('/api/hermes-voice/status', headers=headers).status_code == 403
    assert client.post('/api/hermes-voice/settings', json={'language': 'en'}, headers=headers).status_code == 403
    store = allow_microphone()
    try:
        response = client.post('/api/hermes-voice/runtime', headers=headers, json={
            'action': 'start', 'client_id': 'companion', 'capture_id': 'one', 'confirmed': True})
    finally:
        store.stop()
    assert response.status_code == 403
    assert runtime.request.await_count == 0


def test_core_http_refuses_paired_companion_from_lan_peer(core_client, monkeypatch):
    from core import websocket_server as ws

    _, runtime = core_client
    client = TestClient(ws.app, base_url='http://127.0.0.1:8766', client=('192.168.1.25', 51000))
    assert client.get('/api/hermes-voice/status', headers=webview_headers(GOOD)).status_code == 403
    assert runtime.request.await_count == 0
