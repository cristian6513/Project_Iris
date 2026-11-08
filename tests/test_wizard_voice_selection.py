import json
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient
from wizard import app as wizard

@pytest.fixture
def profile(tmp_path,monkeypatch):
    monkeypatch.setenv('IRIS_HOME',str(tmp_path/'iris'))
    monkeypatch.setenv('HERMES_HOME',str(tmp_path/'hermes'))
    monkeypatch.delenv('IRIS_RUNTIME_DESCRIPTOR',raising=False)
    cfg=wizard.IRISConfig(assistant_name='Conservar',fish_audio_api_key='synthetic-saved-key',fish_audio_voice_id='a'*32,visual_theme='rose')
    cfg.save()
    monkeypatch.setattr(wizard,'_voice_hardware_error',lambda *a:None)
    async def accepted(key): return None
    monkeypatch.setattr('core.fish_audio.check_key',accepted)
    return TestClient(wizard.app,base_url="http://127.0.0.1:7777",client=("127.0.0.1",5000))

H={'X-IRIS-Intent':'save-voice'}

def test_save_only_the_voice_card_and_keep_blank_secret(profile):
    with patch.object(wizard,'save_llm_selection',side_effect=AssertionError('no AI writes')),patch.object(wizard,'sync_hermes_identity_from_config',side_effect=AssertionError('no identity writes')):
        r=profile.post('/api/save-voice-selection',headers=H,json={'tts_provider':'fish-audio','fish_audio_api_key':'','fish_audio_voice_id':'b'*32,'fish_audio_model':'s2.1-pro-free'})
    assert r.status_code==200,r.text
    cfg=wizard.IRISConfig.load()
    assert cfg.tts_provider=='fish-audio' and cfg.fish_audio_voice_id=='b'*32
    assert cfg.fish_audio_api_key=='synthetic-saved-key' and cfg.assistant_name=='Conservar' and cfg.visual_theme=='rose'
    assert 'synthetic-saved-key' not in r.text and 'no-store' in r.headers['cache-control']
    assert 'próxima respuesta' in r.json()['message'] and 'reiniciar' not in r.json()['message']

@pytest.mark.parametrize('body',[{'tts_provider':'fish-audio','assistant_name':'erase'}, {'tts_provider':'fish-audio','minimax_api_key':'secret'}, {'tts_provider':'fish-audio','fish_audio_model':'invalid-secret'}, {'tts_provider':'unknown'}, {'tts_provider':'fish-audio','fish_audio_voice_id':'../bad'}])
def test_invalid_voice_never_saves_or_echoes_credentials(profile,body):
    before=wizard.IRISConfig.load().model_dump()
    r=profile.post('/api/save-voice-selection',headers=H,json=body)
    assert r.status_code in (400,422) and wizard.IRISConfig.load().model_dump()==before
    assert 'invalid-secret' not in r.text

def test_cross_site_or_unintentional_saves_refused(profile):
    for h in ({},{**H,'Sec-Fetch-Site':'cross-site'},{**H,'Origin':'https://external.example'}):
        assert profile.post('/api/save-voice-selection',headers=h,json={'tts_provider':'edge-tts'}).status_code==403

def test_bad_json_duplicate_keys_and_size_are_bounded(profile):
    for payload in ('{"tts_provider":"edge-tts","tts_provider":"fish-audio"}','x','x'*33000):
        assert profile.post('/api/save-voice-selection',headers=H,content=payload).status_code==422


def test_a_key_fish_rejects_is_not_saved_and_says_why(profile,monkeypatch):
    from core.fish_audio import FishKeyRejected
    async def rejected(key): raise FishKeyRejected('no')
    monkeypatch.setattr('core.fish_audio.check_key',rejected)
    before=wizard.IRISConfig.load().model_dump()
    r=profile.post('/api/save-voice-selection',headers=H,json={'tts_provider':'fish-audio','fish_audio_api_key':'rejected-secret','fish_audio_voice_id':'b'*32})
    assert r.status_code==400 and r.json()['code']=='tts_key_rejected'
    assert 'rechaz' in r.json()['message'] and 'rejected-secret' not in r.text
    assert wizard.IRISConfig.load().model_dump()==before

def test_the_key_already_saved_is_the_one_checked_when_the_field_is_blank(profile,monkeypatch):
    seen=[]
    async def check(key): seen.append(key)
    monkeypatch.setattr('core.fish_audio.check_key',check)
    r=profile.post('/api/save-voice-selection',headers=H,json={'tts_provider':'fish-audio','fish_audio_api_key':'','fish_audio_voice_id':'b'*32})
    assert r.status_code==200 and seen==['synthetic-saved-key']

def test_without_an_answer_from_fish_the_voice_still_saves(profile,monkeypatch):
    from core.fish_audio import FishAudioError
    async def offline(key): raise FishAudioError('sin red')
    monkeypatch.setattr('core.fish_audio.check_key',offline)
    r=profile.post('/api/save-voice-selection',headers=H,json={'tts_provider':'fish-audio','fish_audio_api_key':'','fish_audio_voice_id':'b'*32})
    assert r.status_code==200 and wizard.IRISConfig.load().fish_audio_voice_id=='b'*32
