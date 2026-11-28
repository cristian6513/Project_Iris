"""«Idioma al hablar»: the speech-to-text language IRIS shows and saves (Hermes ``stt.language``).

The voice dialog (IRIS OS and the Companion) and IRIS OS Ajustes read it from
``/api/hermes-voice/status`` and save it through ``POST /api/hermes-voice/settings``, apart
from the «Transcribir mientras hablo» checkbox. Synthetic profiles only; no real Hermes or audio.
"""
import asyncio
import contextlib
import json
import re
import tempfile
import threading
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import yaml
from fastapi import HTTPException
from pydantic import ValidationError

from core import hermes_wake as wake
from test_manual_voice import request_fixture, runtime_fixture

ROOT = Path(__file__).resolve().parents[1]
SHOWN_KEYS = {'streaming', 'language', 'language_saved', 'live_text', 'language_override'}


@pytest.fixture
def config_path(tmp_path, monkeypatch):
    path = tmp_path / 'hermes' / 'config.yaml'
    path.parent.mkdir()
    monkeypatch.setattr(wake, 'get_hermes_config_path', lambda: path)
    monkeypatch.setattr(wake, 'iris_stt_language', lambda: 'es')  # never the PC's IRIS profile
    return path


@pytest.fixture
def hermes_home(tmp_path, monkeypatch):
    """A whole synthetic profile, for the writer IRIS runs before ``hermes serve``."""
    monkeypatch.setenv('IRIS_HOME', str(tmp_path / 'iris'))
    monkeypatch.setenv('HERMES_HOME', str(tmp_path / 'hermes'))
    monkeypatch.delenv('IRIS_RUNTIME_DESCRIPTOR', raising=False)
    monkeypatch.setattr(wake, 'iris_stt_language', lambda: 'es')
    home = tmp_path / 'hermes'
    home.mkdir()
    return home


def write(path, text):
    path.write_text(text, encoding='utf-8', newline='\n')


def stt_of(path):
    return yaml.safe_load(path.read_text(encoding='utf-8'))['stt']


def status(runtime=None):
    return asyncio.run(wake.get_hermes_voice_status(request_fixture(runtime or runtime_fixture()[0])))['settings']


def save(change, runtime=None, **request):
    payload = wake.HermesVoiceSettings(**change)
    return asyncio.run(wake.update_hermes_voice_settings(payload, request_fixture(runtime or runtime_fixture()[0], **request)))


# ── What is shown ──

def test_a_profile_without_stt_language_shows_iris_language(config_path, monkeypatch):
    write(config_path, 'stt:\n  streaming: false\n')
    assert status() == {'streaming': False, 'language': 'es', 'language_saved': False,
                        'live_text': False, 'language_override': ''}
    monkeypatch.setattr(wake, 'iris_stt_language', lambda: 'pt')
    assert status()['language'] == 'pt'  # what ensure_stt_language_default would write
    monkeypatch.setattr(wake, 'iris_stt_language', lambda: '')
    assert status()['language'] == 'es'  # IRIS' default language
    assert config_path.read_text(encoding='utf-8') == 'stt:\n  streaming: false\n'  # reading never writes


@pytest.mark.parametrize('text, shown', [
    ('stt:\n  language: fr\n', 'fr'),
    ("stt:\n  language: ''\n", ''),          # Hermes auto-detect
    ('stt:\n  language: null\n', ''),        # Hermes treats it the same
    ('stt:\n  language: ko\n', 'ko'),        # not in IRIS' list: shown as is
    ('stt:\n  language: 7\n', ''),
    ('stt:\n  language: ' + 'x' * 80 + '\n', 'x' * 32),
])
def test_an_explicit_language_is_shown_as_saved(config_path, text, shown):
    write(config_path, text)
    settings = status()
    assert settings['language'] == shown and settings['language_saved'] is True


@pytest.mark.parametrize('stt, live', [
    ({}, False), ({'provider': 'local'}, False), ({'provider': 'local_command'}, False),
    ({'provider': 'groq'}, False), ({'provider': 'mistral'}, False), ({'provider': 'deepinfra'}, False),
    ({'provider': 'nous'}, False),  # managed gateway: REST audio only, no live socket
    ({'provider': 'openai'}, True), ({'provider': ' XAI '}, True), ({'provider': 'elevenlabs'}, True),
    ({'provider': ['openai']}, False),
])
def test_live_text_follows_the_providers_hermes_streams_with(config_path, stt, live):
    write(config_path, yaml.safe_dump({'stt': {**stt, 'streaming': True}}))
    assert status()['live_text'] is live


@pytest.mark.parametrize('stt, override', [
    ({'local': {'language': 'en'}}, 'en'),                                   # provider unset = local
    ({'local': {'language': ''}}, ''),                                       # Hermes' blank = inherit
    ({'provider': 'openai', 'local': {'language': 'en'}}, ''),               # not the active one
    ({'provider': 'openai', 'openai': {'language': 'de'}}, 'de'),
    ({'provider': 'nous', 'openai': {'language': 'it'}}, 'it'),
    ({'provider': 'elevenlabs', 'elevenlabs': {'language_code': 'ja'}}, 'ja'),
    ({'local': 'not a section'}, ''),
])
def test_a_per_provider_language_is_reported_because_it_wins(config_path, stt, override):
    write(config_path, yaml.safe_dump({'stt': stt}))
    assert status()['language_override'] == override


def test_status_never_exposes_keys_endpoints_or_the_provider(config_path):
    write(config_path, yaml.safe_dump({'stt': {
        'provider': 'openai', 'language': 'es', 'base_url': 'https://stt.private.example/v1',
        'openai': {'api_key': 'sk-private-key', 'base_url': 'https://proxy.private.example'},
    }}))
    settings = status()
    assert set(settings) == SHOWN_KEYS
    text = json.dumps(settings)
    for secret in ('sk-private-key', 'private.example', 'openai', 'base_url', 'api_key'):
        assert secret not in text


# ── Saving ──

def test_language_saves_alone_and_keeps_every_other_key(config_path):
    source = {'stt': {'streaming': False, 'provider': 'local', 'local': {'model': 'small'},
                      'openai': {'api_key': 'sk-private'}}, 'wake_word': {'enabled': True}, 'model': 'keep'}
    write(config_path, yaml.safe_dump(source, sort_keys=False))
    response = save({'language': 'it'})
    source['stt']['language'] = 'it'
    assert yaml.safe_load(config_path.read_text(encoding='utf-8')) == source
    assert response == {'streaming': False, 'language': 'it', 'language_saved': True,
                        'live_text': False, 'language_override': ''}
    assert status() == response
    assert 'sk-private' not in json.dumps(response)


def test_streaming_saves_alone_and_keeps_the_language(config_path):
    write(config_path, 'stt:\n  language: de\n')
    assert save({'streaming': True})['language'] == 'de'
    assert stt_of(config_path) == {'language': 'de', 'streaming': True}


def test_both_options_save_in_one_write(config_path):
    response = save({'streaming': True, 'language': ''})
    assert stt_of(config_path) == {'streaming': True, 'language': ''}
    assert response['language'] == '' and response['language_saved'] is True


def test_an_empty_stt_section_counts_as_none(config_path):
    write(config_path, 'stt:\nmodel: keep\n')
    save({'language': 'en'})
    assert yaml.safe_load(config_path.read_text(encoding='utf-8')) == {'stt': {'language': 'en'}, 'model': 'keep'}


@pytest.mark.parametrize('change', [
    {}, {'language': None}, {'streaming': None, 'language': None},
    {'language': 'xx'}, {'language': 'ES'}, {'language': 'es-ES'}, {'language': 'auto'}, {'language': 'Español'},
    {'language': ' es'}, {'language': 1}, {'language': ['es']}, {'streaming': 'yes'}, {'streaming': 1},
    {'language': 'es', 'provider': 'openai'}, {'language': 'es', 'api_key': 'x'},
])
def test_only_the_offered_languages_are_accepted(change):
    with pytest.raises(ValidationError):
        wake.HermesVoiceSettings(**change)


@pytest.mark.parametrize('language', wake.STT_LANGUAGE_CHOICES)
def test_every_offered_language_is_accepted(config_path, language):
    assert save({'language': language})['language'] == language


def test_the_writer_refuses_a_language_outside_the_list(config_path):
    with pytest.raises(ValueError):
        wake.configure_voice_settings(language='ko')
    assert not config_path.exists()


@pytest.mark.parametrize('raw', ['stt: [broken]', 'stt: 12', '[not-a-map]', 'stt: ['])
def test_malformed_config_is_preserved_for_the_language_too(config_path, raw):
    write(config_path, raw)
    with pytest.raises(HTTPException) as error:
        save({'language': 'en'})
    assert error.value.status_code == 422
    assert config_path.read_text(encoding='utf-8') == raw


@pytest.mark.parametrize('state', ['recording', 'arming', 'transcribing', 'processing'])
def test_language_cannot_change_mid_take(config_path, state):
    runtime, _ = runtime_fixture()
    runtime.state = state
    with pytest.raises(HTTPException) as error:
        save({'language': 'en'}, runtime)
    assert error.value.status_code == 409 and not config_path.exists()


@pytest.mark.parametrize('peer,headers', [('192.168.0.2', {}), ('127.0.0.1', {'origin': 'https://evil.test'}),
                                          ('127.0.0.1', {'sec-fetch-site': 'cross-site'})])
def test_language_requires_a_local_caller(config_path, peer, headers):
    with pytest.raises(HTTPException) as error:
        save({'language': 'en'}, peer=peer, headers=headers)
    assert error.value.status_code == 403 and not config_path.exists()


def test_without_a_voice_runtime_nothing_is_written(config_path):
    request = request_fixture(None)
    with pytest.raises(HTTPException) as error:
        asyncio.run(wake.update_hermes_voice_settings(wake.HermesVoiceSettings(language='en'), request))
    assert error.value.status_code == 409 and not config_path.exists()


# ── The shared lock (.iris-config.lock, like the streaming box and the wizard) ──

def test_the_write_happens_inside_the_shared_config_lock(config_path, monkeypatch):
    events = []

    @contextlib.contextmanager
    def lock(path):
        events.append(('lock', path))
        yield
        events.append(('unlock', path))

    real_write = wake._write_yaml_document
    monkeypatch.setattr(wake, 'interprocess_file_lock', lock)
    monkeypatch.setattr(wake, '_write_yaml_document',
                        lambda data: (events.append(('write', data['stt']['language'])), real_write(data)))
    wake.configure_voice_settings(language='ja')
    lock_path = config_path.parent / '.iris-config.lock'
    assert events == [('lock', lock_path), ('write', 'ja'), ('unlock', lock_path)]


def test_iris_and_the_wizard_lock_the_same_file(hermes_home):
    from wizard import hermes_config

    assert wake.get_hermes_config_path().parent / '.iris-config.lock' == hermes_config.get_hermes_home() / '.iris-config.lock'


def test_concurrent_checkbox_and_language_writes_lose_nothing(config_path):
    write(config_path, 'stt:\n  streaming: false\nmodel: keep\n')
    languages = ['en', 'pt', 'fr', 'de', 'it']
    errors = []

    def run(task):
        try:
            task()
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [
        threading.Thread(target=run, args=(lambda: [wake.configure_voice_settings(language=code)
                                                    for code in languages * 4],)),
        threading.Thread(target=run, args=(lambda: [wake.configure_voice_settings(streaming=bool(i % 2))
                                                    for i in range(20)],)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not errors
    data = yaml.safe_load(config_path.read_text(encoding='utf-8'))
    assert data == {'stt': {'streaming': True, 'language': 'it'}, 'model': 'keep'}


# ── A saved choice survives Core restarts ──

@pytest.mark.real_stt_language_default
@pytest.mark.parametrize('language', ['fr', '', 'en'])
def test_a_saved_language_is_never_replaced_by_the_default(hermes_home, language):
    from wizard.hermes_config import ensure_stt_language_default

    write(hermes_home / 'config.yaml', 'stt:\n  streaming: false\n')
    wake.configure_voice_settings(language=language)
    assert ensure_stt_language_default('es') == 'kept'
    assert stt_of(hermes_home / 'config.yaml') == {'streaming': False, 'language': language}


@pytest.mark.real_stt_language_default
def test_a_saved_language_survives_the_voice_runtime_start(hermes_home):
    """Core restarts: a new runtime runs ensure_stt_language_default before ``hermes serve``."""
    write(hermes_home / 'config.yaml', 'stt:\n  streaming: false\n')
    wake.configure_voice_settings(language='pt')
    with tempfile.TemporaryDirectory() as folder:
        runtime = wake.HermesWakeRuntime()
        with (
            patch('core.hermes_wake.hermes_command_prefix', return_value=['hermes']),
            patch('core.hermes_wake.get_hermes_repo', return_value=Path(folder)),
            patch('core.hermes_wake.asyncio.create_subprocess_exec',
                  new=AsyncMock(side_effect=PermissionError('test stops here'))),
        ):
            with pytest.raises(wake.HermesGatewayError):
                asyncio.run(runtime.ensure_gateway())
    assert stt_of(hermes_home / 'config.yaml') == {'streaming': False, 'language': 'pt'}
    fresh, _ = runtime_fixture()
    assert status(fresh)['language'] == 'pt' and status(fresh)['language_saved'] is True


# ── The UI offers the same list ──

def test_the_shared_ui_module_offers_exactly_the_accepted_languages():
    source = (ROOT / 'face' / 'js' / 'stt_language.js').read_text(encoding='utf-8')
    block = source[source.index('STT_LANGUAGES'):source.index('].map(Object.freeze)')]
    codes = tuple(re.findall(r"code: '([a-z]*)'", block))
    assert codes == wake.STT_LANGUAGE_CHOICES
    assert "STT_DEFAULT_LANGUAGE = 'es'" in source and wake.STT_DEFAULT_LANGUAGE == 'es'
