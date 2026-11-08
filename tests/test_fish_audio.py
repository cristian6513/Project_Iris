"""Hosted voice contracts through the real provider/wizard; all HTTP is intercepted."""
from __future__ import annotations

import asyncio
import io
import json
import uuid
import wave

import httpx
import pytest
from fastapi.testclient import TestClient

from core.config import IRISConfig
from core.tts import TextToSpeech
from wizard import app as wizard

MP3 = b'ID3\x04' + b'\0' * 100


def wav_sample() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(24000)
        stream.writeframes(b'\0\0' * 24000 * 12)
    return buffer.getvalue()


@pytest.fixture
def setup(monkeypatch, tmp_path):
    monkeypatch.setenv('IRIS_HOME', str(tmp_path))
    monkeypatch.setenv('HERMES_HOME', str(tmp_path / 'hermes'))
    config = IRISConfig()
    monkeypatch.setattr(IRISConfig, 'load', lambda: config)
    monkeypatch.setattr(wizard, '_TTS_PREVIEW_CACHE', {})
    monkeypatch.setattr(wizard, '_TTS_PREVIEW_LOADING', {})
    monkeypatch.setattr(wizard, '_TTS_PREVIEW_EPOCH', {})
    monkeypatch.setattr(wizard, '_FISH_KEYS_CHECKED', {})
    requests = []
    reply = {'handler': lambda req: httpx.Response(200, content=MP3, headers={'Content-Type': 'audio/mpeg'})}
    real_client = httpx.AsyncClient

    def handler(request):
        requests.append(request)
        return reply['handler'](request)

    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    client = TestClient(wizard.app, base_url='http://127.0.0.1:8000', client=('127.0.0.1', 51999))
    return config, requests, reply, client, tmp_path


def preview(client, provider='fish-audio', **values):
    return client.post('/api/test-tts-audio', json={'provider': provider, 'voice': 'voice-iris', **values},
                       headers={'X-IRIS-Intent': 'preview-tts', 'Origin': 'http://127.0.0.1:8000',
                                'X-IRIS-TTS-Key': 'synthetic-test-token'})


def clone(client, provider='fish-audio', **values):
    return client.post('/api/clone-voice', data={'provider': provider, 'name': 'Iris',
                       'transcript': 'Muestra sintetica de prueba.', 'consent_confirmed': 'true',
                       'api_key': 'synthetic-test-token', **values},
                       files={'audio': ('sample.wav', wav_sample(), 'audio/wav')},
                       headers={'X-IRIS-Intent': 'clone-voice', 'Origin': 'http://127.0.0.1:8000',
                                'Idempotency-Key': str(uuid.uuid4())})


@pytest.mark.parametrize('model', ['s2.1-pro-free', 's2.1-pro', 's2-pro'])
def test_preview_returns_the_requested_fish_audio_without_saving_or_fallback(setup, model):
    config, requests, _, client, home = setup
    response = preview(client, model=model)
    assert response.status_code == 200 and response.content == MP3
    assert response.headers['content-type'] == 'audio/mpeg'
    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == 'https://api.fish.audio/v1/tts'
    assert request.headers['Authorization'] == 'Bearer synthetic-test-token'
    assert request.headers['model'] == model
    body = json.loads(request.content)
    assert body['reference_id'] == 'voice-iris' and body['format'] == 'mp3'
    assert not (home / 'config.json').exists()
    assert config.tts_allow_remote_fallback is False


def test_missing_key_and_unknown_model_do_not_contact_fish(setup):
    _, requests, _, client, _ = setup
    missing = client.post('/api/test-tts-audio', json={'provider': 'fish-audio', 'voice': 'voice'},
                          headers={'X-IRIS-Intent': 'preview-tts', 'Origin': 'http://127.0.0.1:8000'})
    assert missing.status_code == 400 and 'API key' in missing.json()['message']
    wrong = preview(client, model='s2-pro-typo')
    assert wrong.status_code == 400
    assert requests == []   # no provider fallback to its billed default


@pytest.mark.parametrize('status', [401, 402, 403, 404, 422, 429, 500])
def test_fish_failure_is_visible_and_does_not_leak_its_body_or_use_another_provider(setup, status):
    _, requests, reply, client, _ = setup
    reply['handler'] = lambda req: httpx.Response(status, text='synthetic-test-token PRIVATE PROVIDER BODY')
    response = preview(client)
    assert response.status_code == 502
    assert response.json()['message'].startswith('Fish Audio no produjo audio:')
    assert 'synthetic-test-token' not in response.text and 'PRIVATE PROVIDER BODY' not in response.text
    assert len(requests) == 1


def test_http_success_with_json_instead_of_audio_is_not_reported_as_success(setup):
    _, _, reply, client, _ = setup
    reply['handler'] = lambda req: httpx.Response(200, json={'error': 'no audio'})
    response = preview(client)
    assert response.status_code == 502 and 'MP3 válido' in response.json()['message']


def test_timeout_is_not_retried_or_exposed_with_request_credentials(setup):
    _, requests, reply, client, _ = setup
    def timeout(request):
        raise httpx.ReadTimeout('synthetic-test-token', request=request)
    reply['handler'] = timeout
    response = preview(client)
    assert response.status_code == 502 and len(requests) == 1
    assert 'synthetic-test-token' not in response.text
    assert 'no se reintentó' in response.json()['message']


def test_low_local_resources_do_not_block_a_hosted_voice(setup, monkeypatch):
    from core import voice_requirements as vr
    monkeypatch.setattr(vr, 'current_machine', lambda home: vr.Machine(2, 2, {}, 0.1, 0.5))
    assert preview(setup[3]).status_code == 200


def test_preview_cache_separates_models_and_saved_accounts(setup):
    config, requests, _, client, _ = setup
    config.fish_audio_api_key = 'saved-account-a'
    headers = {'X-IRIS-Intent': 'preview-tts', 'Origin': 'http://127.0.0.1:8000'}
    for key, model in [('saved-account-a', 's2.1-pro-free'), ('saved-account-b', 's2.1-pro-free'), ('saved-account-b', 's2.1-pro')]:
        config.fish_audio_api_key = key
        response = client.post('/api/test-tts-audio', json={'provider': 'fish-audio', 'voice': 'same', 'model': model}, headers=headers)
        assert response.status_code == 200
    assert [request.headers['Authorization'] for request in requests] == ['Bearer saved-account-a', 'Bearer saved-account-b', 'Bearer saved-account-b']
    assert len(wizard._TTS_PREVIEW_CACHE) == 3


def test_private_clone_sends_wav_transcript_and_preserves_its_voice_id(setup):
    config, requests, reply, client, home = setup
    reply['handler'] = lambda req: httpx.Response(201, json={'_id': 'new-private-voice', 'visibility': 'private', 'state': 'created'})
    response = clone(client)
    assert response.status_code == 200 and response.json()['voice_id'] == 'new-private-voice'
    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == 'https://api.fish.audio/model'
    assert request.headers['content-type'].startswith('multipart/form-data')
    assert b'name="voices"; filename="voice_sample.wav"' in request.content
    assert b'RIFF' in request.content and b'Muestra sintetica' in request.content
    assert b'name="visibility"\r\n\r\nprivate' in request.content
    assert b'name="generate_sample"\r\n\r\nfalse' in request.content
    saved = json.loads((home / 'config.json').read_text(encoding='utf-8'))
    assert config.fish_audio_voice_id == saved['fish_audio_voice_id'] == 'new-private-voice'
    assert config.fish_audio_voice_name == saved['fish_audio_voice_name'] == 'Iris'
    assert 'synthetic-test-token' not in response.text


def test_clone_without_transcript_lets_fish_transcribe_and_accepts_up_to_a_minute(setup):
    _, requests, reply, client, _ = setup
    reply['handler'] = lambda req: httpx.Response(201, json={'_id': 'auto-text', 'visibility': 'private', 'state': 'created'})
    long_sample = io.BytesIO()
    with wave.open(long_sample, 'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(24000)
        stream.writeframes(b'\0\0' * 24000 * 55)
    response = client.post('/api/clone-voice', data={'provider': 'fish-audio', 'name': 'Iris', 'transcript': '',
                           'consent_confirmed': 'true', 'api_key': 'synthetic-test-token'},
                           files={'audio': ('sample.wav', long_sample.getvalue(), 'audio/wav')},
                           headers={'X-IRIS-Intent': 'clone-voice', 'Origin': 'http://127.0.0.1:8000',
                                    'Idempotency-Key': str(uuid.uuid4())})
    assert response.status_code == 200 and response.json()['voice_id'] == 'auto-text'
    assert b'name="texts"' not in requests[0].content


@pytest.mark.parametrize('provider', ['elevenlabs', 'minimax'])
def test_saved_cloud_key_changes_do_not_reuse_the_previous_accounts_engine(setup, provider):
    config, requests, reply, client, _ = setup
    if provider == 'minimax':
        reply['handler'] = lambda req: httpx.Response(200, json={'data': {'audio': MP3.hex()}})
    headers = {'X-IRIS-Intent': 'preview-tts', 'Origin': 'http://127.0.0.1:8000'}
    for key in ['account-first', 'account-second']:
        setattr(config, f'{provider}_api_key', key)
        response = client.post('/api/test-tts-audio', json={'provider': provider, 'voice': 'same-voice'}, headers=headers)
        assert response.status_code == 200
    assert len(wizard._TTS_PREVIEW_CACHE) == 2
    header = 'xi-api-key' if provider == 'elevenlabs' else 'Authorization'
    prefix = '' if provider == 'elevenlabs' else 'Bearer '
    assert [request.headers[header] for request in requests] == [prefix+'account-first', prefix+'account-second']


@pytest.mark.parametrize('change', [{'visibility': 'public'}, {'_id': ''}, {'state': 'failed'}])
def test_unconfirmed_clone_never_overwrites_the_saved_voice(setup, change):
    config, _, reply, client, home = setup
    config.fish_audio_voice_id = 'old-voice'
    reply['handler'] = lambda req: httpx.Response(201, json={'_id': 'new', 'visibility': 'private', 'state': 'created', **change})
    response = clone(client)
    assert response.status_code == 502
    assert config.fish_audio_voice_id == 'old-voice'
    assert not (home / 'config.json').exists()


def test_clone_needs_permission_a_minimum_length_and_key_before_network(setup):
    _, requests, _, client, _ = setup
    assert clone(client, consent_confirmed='false').status_code == 400
    assert clone(client, api_key='').status_code == 400
    short = io.BytesIO()
    with wave.open(short, 'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(24000)
        stream.writeframes(b'\0\0' * 24000 * 5)
    too_short = client.post('/api/clone-voice', data={'provider': 'fish-audio', 'name': 'Iris', 'consent_confirmed': 'true',
                            'api_key': 'synthetic-test-token'}, files={'audio': ('s.wav', short.getvalue(), 'audio/wav')},
                            headers={'X-IRIS-Intent': 'clone-voice', 'Origin': 'http://127.0.0.1:8000',
                                     'Idempotency-Key': str(uuid.uuid4())})
    assert too_short.status_code == 400 and '10 a 60 segundos' in too_short.json()['error']
    assert requests == []


VOICES = {'has_more': True, 'total': 1000, 'items': [
    {'_id': 'first', 'type': 'tts', 'state': 'trained', 'title': '<script>untrusted</script>',
     'cover_image': 'https://tracker.example/x', 'languages': ['es', '<b>', 'en'], 'like_count': 12045,
     'task_count': 2190861, 'samples': [{'audio': 'https://platform.r2.fish.audio/task/one.mp3', 'text': 'Hola'}]},
    {'_id': 'elsewhere', 'type': 'tts', 'state': 'trained', 'title': 'Sample elsewhere', 'like_count': True,
     'samples': [{'audio': 'https://tracker.example/one.mp3'}, 'secret']},
    {'_id': 'removed', 'dmca_taken_down': True},
    {'_id': 'converter', 'type': 'svc', 'state': 'trained'},
    {'_id': 'unfinished', 'type': 'tts', 'state': 'training'},
    {'_id': 'broken', 'type': 'tts', 'state': 'failed'},
    {'_id': '../bad-id'},
]}


def test_catalog_uses_saved_key_and_returns_only_safe_fields_and_no_remote_urls(setup):
    config, requests, reply, client, _ = setup
    config.fish_audio_api_key = 'saved-account'
    reply['handler'] = lambda req: httpx.Response(200, json=VOICES)
    response = client.post('/api/fish-audio/voices', json={'query': 'Iris', 'self_only': True},
                           headers={'X-IRIS-Intent': 'preview-tts', 'Origin': 'http://127.0.0.1:8000'})
    assert response.status_code == 200
    assert response.json() == {'page': 1, 'has_more': True, 'voices': [
        {'id': 'first', 'name': '<script>untrusted</script>', 'languages': ['es', 'en'], 'likes': 12045,
         'uses': 2190861, 'has_sample': True},
        {'id': 'elsewhere', 'name': 'Sample elsewhere', 'languages': [], 'likes': 0, 'uses': 0, 'has_sample': False},
    ]}
    assert response.headers['cache-control'] == 'no-store'
    # Listing the account's own voices proves the key: no separate check.
    assert len(requests) == 1
    assert dict(requests[0].url.params) == {'page_size': '20', 'page_number': '1', 'title': 'Iris', 'self': 'true'}
    assert requests[0].headers['Authorization'] == 'Bearer saved-account'
    for leaked in ('saved-account', 'tracker.example', 'platform.r2.fish.audio'):
        assert leaked not in response.text


def test_catalog_filters_by_language_omits_empty_filters_and_checks_a_key_once(setup):
    _, requests, reply, client, _ = setup
    reply['handler'] = lambda req: httpx.Response(200, json=VOICES if req.url.params.get('self') != 'true'
                                                  else {'items': [], 'total': 0})
    headers = {'X-IRIS-Intent': 'preview-tts', 'Origin': 'http://127.0.0.1:8000', 'X-IRIS-TTS-Key': 'typed-key'}
    for page in (1, 2):
        response = client.post('/api/fish-audio/voices', json={'language': 'es', 'page': page}, headers=headers)
        assert response.status_code == 200 and response.json()['page'] == page
    check, first, second = requests
    assert dict(check.url.params) == {'self': 'true', 'page_size': '1'}
    assert dict(first.url.params) == {'page_size': '20', 'page_number': '1', 'language': 'es'}
    assert dict(second.url.params) == {'page_size': '20', 'page_number': '2', 'language': 'es'}


def test_a_wrong_key_is_reported_at_search_although_the_catalog_is_public(setup):
    _, requests, reply, client, _ = setup
    reply['handler'] = lambda req: (httpx.Response(401, json={'message': 'synthetic body'})
                                    if req.url.params.get('self') == 'true' else httpx.Response(200, json=VOICES))
    response = client.post('/api/fish-audio/voices', json={'query': 'hola'}, headers={
        'X-IRIS-Intent': 'preview-tts', 'Origin': 'http://127.0.0.1:8000', 'X-IRIS-TTS-Key': 'wrong-key'})
    assert response.status_code == 502 and 'no es válida' in response.json()['message']
    assert len(requests) == 1 and 'synthetic body' not in response.text


@pytest.mark.parametrize('body', [{'language': 'xx'}, {'language': 'es-ES'}, {'page': 0}, {'page': 51}])
def test_catalog_rejects_unknown_languages_and_pages_without_contacting_fish(setup, body):
    _, requests, _, client, _ = setup
    response = client.post('/api/fish-audio/voices', json=body, headers={
        'X-IRIS-Intent': 'preview-tts', 'Origin': 'http://127.0.0.1:8000', 'X-IRIS-TTS-Key': 'typed-key'})
    assert response.status_code in {400, 422} and requests == []


def test_more_pages_follow_the_total_when_fish_omits_has_more(setup):
    _, _, reply, client, _ = setup
    reply['handler'] = lambda req: httpx.Response(200, json={'total': 45, 'items': []})
    headers = {'X-IRIS-Intent': 'preview-tts', 'Origin': 'http://127.0.0.1:8000', 'X-IRIS-TTS-Key': 'typed-key'}
    pages = [client.post('/api/fish-audio/voices', json={'page': page}, headers=headers).json()['has_more']
             for page in (1, 2, 3)]
    assert pages == [True, True, False]


def test_no_more_pages_past_the_thousand_results_fish_serves(setup):
    _, _, reply, client, _ = setup
    reply['handler'] = lambda req: httpx.Response(200, json={'has_more': True, 'total': 5000, 'items': []})
    headers = {'X-IRIS-Intent': 'preview-tts', 'Origin': 'http://127.0.0.1:8000', 'X-IRIS-TTS-Key': 'typed-key'}
    pages = [client.post('/api/fish-audio/voices', json={'page': page}, headers=headers).json()['has_more']
             for page in (49, 50)]
    assert pages == [True, False]


def sample_request(client, voice='first'):
    return client.post('/api/fish-audio/sample', json={'voice_id': voice}, headers={
        'X-IRIS-Intent': 'preview-tts', 'Origin': 'http://127.0.0.1:8000', 'X-IRIS-TTS-Key': 'typed-key'})


# Measured 2026-10-21: the catalog links a sample on the CDN, a single voice as a
# signed link to Fish's storage bucket; both serve the same MP3.
BUCKET_SAMPLE = ('https://c97f3361.r2.cloudflarestorage.com/fish-platform-data/task/one.mp3'
                 '?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Expires=3600&X-Amz-Signature=abc')


@pytest.mark.parametrize('url', ['https://platform.r2.fish.audio/task/one.mp3', BUCKET_SAMPLE])
def test_published_sample_is_relayed_without_synthesis_or_sending_the_key_to_the_cdn(setup, url):
    _, requests, reply, client, home = setup
    voice = {**VOICES['items'][0], 'samples': [{'audio': url}]}
    reply['handler'] = lambda req: (httpx.Response(200, json=voice) if req.url.host == 'api.fish.audio'
                                    else httpx.Response(200, content=MP3, headers={'Content-Type': 'audio/mpeg'}))
    response = sample_request(client)
    assert response.status_code == 200 and response.content == MP3
    assert response.headers['content-type'] == 'audio/mpeg'
    lookup, cdn = requests
    assert str(lookup.url) == 'https://api.fish.audio/model/first'
    assert lookup.headers['Authorization'] == 'Bearer typed-key'
    assert str(cdn.url) == url
    assert 'Authorization' not in cdn.headers
    assert all(request.url.path != '/v1/tts' for request in requests)
    assert not (home / 'config.json').exists()


@pytest.mark.parametrize('samples', [
    [], [{'audio': 'https://tracker.example/one.mp3'}], [{'audio': 'http://platform.r2.fish.audio/one.mp3'}],
    [{'audio': 'https://platform.r2.fish.audio:8443/one.mp3'}], [{'audio': 'https://user@platform.r2.fish.audio/one.mp3'}],
    [{'audio': 'https://c97f3361.r2.cloudflarestorage.com/another-bucket/one.mp3'}],
    [{'audio': 'https://tracker.example/fish-platform-data/one.mp3'}],
])
def test_a_voice_without_a_sample_on_fishs_cdn_fetches_nothing_else(setup, samples):
    _, requests, reply, client, _ = setup
    reply['handler'] = lambda req: httpx.Response(200, json={'_id': 'first', 'samples': samples})
    response = sample_request(client)
    assert response.status_code == 502 and 'no tiene una muestra' in response.json()['message']
    assert [request.url.host for request in requests] == ['api.fish.audio']


@pytest.mark.parametrize('cdn', [
    httpx.Response(200, content=b'<html>not audio</html>', headers={'Content-Type': 'text/html'}),
    httpx.Response(302, headers={'Location': 'https://tracker.example/x'}),
    httpx.Response(200, content=b'ID3' + b'\0' * (5 * 1024 * 1024), headers={'Content-Type': 'audio/mpeg'}),
    httpx.Response(200, content=b'RIFF' + b'\0' * 100, headers={'Content-Type': 'audio/mpeg'}),
])
def test_a_sample_that_is_not_a_small_mp3_is_refused(setup, cdn):
    _, requests, reply, client, _ = setup
    voice = {'_id': 'first', 'samples': [{'audio': 'https://platform.r2.fish.audio/task/one.mp3'}]}
    reply['handler'] = lambda req: cdn if req.url.host == 'platform.r2.fish.audio' else httpx.Response(200, json=voice)
    response = sample_request(client)
    assert response.status_code == 502 and response.headers['content-type'].startswith('application/json')
    assert len(requests) == 2   # no redirect followed


def test_sample_needs_a_key_and_a_valid_voice_id_before_network(setup):
    _, requests, _, client, _ = setup
    headers = {'X-IRIS-Intent': 'preview-tts', 'Origin': 'http://127.0.0.1:8000'}
    assert client.post('/api/fish-audio/sample', json={'voice_id': 'first'}, headers=headers).status_code == 400
    assert sample_request(client, '../model').status_code == 502
    assert requests == []


def test_catalog_missing_key_and_untrusted_requests_never_contact_fish(setup):
    _, requests, _, client, _ = setup
    headers = {'X-IRIS-Intent': 'preview-tts', 'Origin': 'http://127.0.0.1:8000'}
    assert client.post('/api/fish-audio/voices', json={}, headers=headers).status_code == 400
    lan = TestClient(wizard.app, base_url='http://127.0.0.1:8000', client=('192.168.1.50', 4000))
    assert lan.post('/api/fish-audio/voices', json={}, headers={**headers, 'Origin': 'https://untrusted.example'}).status_code == 403
    assert requests == []


def test_fish_key_is_preserved_on_revisit_and_never_returned_in_current_config(setup):
    config, _, _, client, _ = setup
    config.fish_audio_api_key = 'saved-secret'
    config.fish_audio_voice_id = 'saved-voice'
    config.fish_audio_voice_name = 'Voz Femenina Español'
    updated = wizard._merge_config_update(wizard.ConfigUpdate(fish_audio_api_key=''), config)
    assert updated.fish_audio_api_key == 'saved-secret'
    response = client.get('/api/current-config')
    assert response.status_code == 200 and response.json()['has_fish_audio_api_key'] is True
    assert response.json()['fish_audio_voice_id'] == 'saved-voice'
    assert response.json()['fish_audio_voice_name'] == 'Voz Femenina Español'
    assert 'fish_audio_api_key' not in response.json() and 'saved-secret' not in response.text


def test_core_selects_fish_audio_with_its_stored_voice_and_model(setup):
    config, requests, _, _, _ = setup
    config.tts_provider = 'fish-audio'
    config.fish_audio_api_key, config.fish_audio_voice_id = 'stored-key', 'stored-voice'

    async def run():
        engine = TextToSpeech(config)
        await engine.initialize()
        assert requests == []  # initialization never talks to Fish
        audio, _ = await engine.speak('Hola Iris')
        await engine.close()
        return audio

    assert asyncio.run(run()) == MP3
    assert json.loads(requests[0].content)['reference_id'] == 'stored-voice'


@pytest.mark.parametrize('provider', ['elevenlabs', 'minimax'])
def test_other_two_cloud_providers_preview_and_clone_through_the_wizard(setup, provider):
    config, requests, reply, client, _ = setup

    def response(req):
        if req.url.path == '/v1/files/upload':
            return httpx.Response(200, json={'file': {'file_id': 123}})
        if req.url.path == '/v1/voice_clone':
            return httpx.Response(200, json={'base_resp': {'status_code': 0}})
        if req.url.path == '/v1/voices/add':
            return httpx.Response(200, json={'voice_id': 'cloned-eleven'})
        if req.url.host == 'api.minimax.io':
            return httpx.Response(200, json={'data': {'audio': MP3.hex()}, 'base_resp': {'status_code': 0}})
        return httpx.Response(200, content=MP3)

    reply['handler'] = response
    assert clone(client, provider).status_code == 200
    voice = getattr(config, f'{provider}_voice_id')
    audio = preview(client, provider, voice=voice)
    assert audio.status_code == 200 and audio.content == MP3
    assert all('fish.audio' not in str(request.url) for request in requests)
