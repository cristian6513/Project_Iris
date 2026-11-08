"""QA12: Edge-TTS, the default voice, failed and the wizard blamed API keys and local models.

Microsoft answered 403 to edge-tts 7.2.8 (7.3.x adds the Sec-MS-GEC v2 token). The cause now
reaches the wizard in plain words, never with the request URL (it carries a client token).
"""
from __future__ import annotations

import asyncio
import re
import sys
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from core.config import IRISConfig
from core.tts import EdgeTTSProvider
from wizard import app as wizard

ROOT = Path(__file__).resolve().parents[1]
TOKEN_URL = ("wss://speech.platform.bing.com/consumer/speech/synthesize/readaloud/edge/v1"
             "?TrustedClientToken=SYNTHETIC-CLIENT-TOKEN&Sec-MS-GEC=SYNTHETIC")


class HandshakeRejected(Exception):
    """Shaped like aiohttp's WSServerHandshakeError."""

    def __init__(self, status):
        super().__init__(f"{status}, message='Invalid response status', url='{TOKEN_URL}'")
        self.status = status


def fake_edge_tts(error):
    class Communicate:
        def __init__(self, text, voice):
            pass

        async def stream(self):
            raise error
            yield  # pragma: no cover - makes this an async generator

        async def close(self):
            pass

    return types.SimpleNamespace(Communicate=Communicate)


def synthesize_with(monkeypatch, error):
    monkeypatch.setitem(sys.modules, 'edge_tts', fake_edge_tts(error))
    provider = EdgeTTSProvider('es-MX-DaliaNeural')

    async def run():
        await provider.initialize()
        return await provider.synthesize('Hola mundo, soy IRIS.')

    return provider, asyncio.run(run())


@pytest.mark.parametrize('error, words', [
    (HandshakeRejected(403), 'HTTP 403'),
    (HandshakeRejected(503), 'HTTP 503'),
    (OSError('Cannot connect to host speech.platform.bing.com'), 'Comprueba la conexión'),
    (asyncio.TimeoutError(), 'Comprueba la conexión'),
    (type('NoAudioReceived', (Exception,), {})('No audio was received'), 'no devolvió audio'),
])
def test_a_failed_synthesis_keeps_a_short_cause_without_the_request_url(monkeypatch, error, words):
    provider, (audio, visemes) = synthesize_with(monkeypatch, error)
    assert (audio, visemes) == (b'', [])
    assert words in provider.last_error
    for leaked in ('TrustedClientToken', 'SYNTHETIC', 'wss://', 'bing.com'):
        assert leaked not in provider.last_error


def test_the_wizard_preview_names_the_edge_failure_instead_of_api_keys(monkeypatch, tmp_path):
    monkeypatch.setenv('IRIS_HOME', str(tmp_path))
    monkeypatch.setenv('HERMES_HOME', str(tmp_path / 'hermes'))
    config = IRISConfig()
    monkeypatch.setattr(IRISConfig, 'load', lambda: config)
    monkeypatch.setattr(wizard, '_TTS_PREVIEW_CACHE', {})
    monkeypatch.setattr(wizard, '_TTS_PREVIEW_LOADING', {})
    monkeypatch.setattr(wizard, '_TTS_PREVIEW_EPOCH', {})
    monkeypatch.setitem(sys.modules, 'edge_tts', fake_edge_tts(HandshakeRejected(403)))
    client = TestClient(wizard.app, base_url='http://127.0.0.1:8000', client=('127.0.0.1', 51999))
    response = client.post('/api/test-tts-audio', json={'provider': 'edge-tts', 'voice': 'es-MX-DaliaNeural'},
                           headers={'X-IRIS-Intent': 'preview-tts', 'Origin': 'http://127.0.0.1:8000'})
    assert response.status_code == 502
    message = response.json()['message']
    assert message.startswith('Edge-TTS no produjo audio: Microsoft rechazó la conexión (HTTP 403).')
    assert 'API key' not in message and 'NeuTTS' not in message
    assert 'SYNTHETIC' not in response.text and 'TrustedClientToken' not in response.text


def test_the_packaged_runtime_never_goes_back_to_an_edge_tts_microsoft_rejects():
    lock = (ROOT / 'scripts/locks/iris-windows-cp311.txt').read_text(encoding='utf-8')
    version = re.search(r'^edge-tts==(\d+)\.(\d+)\.(\d+) ', lock, re.M)
    assert version and tuple(map(int, version.groups())) >= (7, 3, 0)
    assert '"edge-tts>=7.3.0"' in (ROOT / 'pyproject.toml').read_text(encoding='utf-8')
