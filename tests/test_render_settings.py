import asyncio
import json

import pytest
from fastapi import HTTPException

from core.config import IRISConfig


@pytest.fixture
def server(monkeypatch,tmp_path):
    from core import config,websocket_server
    monkeypatch.setattr(config,'get_iris_home',lambda:tmp_path)
    events=[]
    async def broadcast(kind,payload):events.append((kind,payload))
    monkeypatch.setattr(websocket_server,'broadcast_to_face',broadcast)
    return websocket_server,events,tmp_path


def test_default_is_60_and_saving_survives_a_reload_without_changing_other_preferences(server):
    api,events,home=server
    config=IRISConfig(assistant_name='Prueba',render_fps_limit=60);config.save()
    for fps in (30,0,60):
        response=asyncio.run(api.save_render_settings({'render_fps_limit':fps}))
        preferences={'render_fps_limit':fps,'render_quality':'balanced'}
        assert json.loads(response.body)==preferences
        assert IRISConfig.load().render_fps_limit==fps
        assert IRISConfig.load().assistant_name=='Prueba'
        assert json.loads(asyncio.run(api.get_render_settings()).body)==preferences
        assert events[-1]==('render_preferences',preferences)
    assert (home/'config.json').is_file()


def test_quality_persists_and_partial_updates_keep_fps_and_other_settings(server):
    api,events,home=server
    # A profile from before graphics quality was configurable.
    (home/'config.json').write_text(json.dumps({'assistant_name':'Prueba','render_fps_limit':30}))
    assert IRISConfig.load().render_quality=='balanced'
    for quality in ('saving','high','balanced'):
        response=asyncio.run(api.save_render_settings({'render_quality':quality}))
        preferences={'render_fps_limit':30,'render_quality':quality}
        assert json.loads(response.body)==preferences
        assert events[-1]==('render_preferences',preferences)
        saved=IRISConfig.load()
        assert (saved.render_quality,saved.render_fps_limit,saved.assistant_name)==(quality,30,'Prueba')
    asyncio.run(api.save_render_settings({'render_quality':'high','render_fps_limit':0}))
    asyncio.run(api.save_render_settings({'render_fps_limit':60}))
    assert IRISConfig.load().render_quality=='high'


def test_public_config_exposes_quality_but_no_credentials(server):
    api,_,_=server
    IRISConfig(render_quality='saving',lan_pairing_token='test-only-private').save()
    data=json.loads(asyncio.run(api.get_config()).body)
    assert data['render_quality']=='saving'
    assert 'test-only-private' not in json.dumps(data)


@pytest.mark.parametrize('payload',[{}, {'render_fps_limit':True},{'render_fps_limit':'30'},
    {'render_fps_limit':31},{'render_fps_limit':-1},{'render_fps_limit':30.0},
    {'render_fps_limit':30,'tts_provider':'fake'}, {'render_quality':'ultra'},
    {'render_quality':None}, {'render_quality':True}, {'render_quality':1},
    {'render_quality':[]}, {'render_quality':{}}, {'render_quality':'high','tts_provider':'fake'}])
def test_invalid_preferences_are_not_saved_or_broadcast(server,payload):
    api,events,home=server
    with pytest.raises(HTTPException) as exc:asyncio.run(api.save_render_settings(payload))
    assert exc.value.status_code==422 and events==[] and not (home/'config.json').exists()


def test_lan_cannot_change_the_hosts_render_settings(server):
    import httpx
    api,events,home=server
    IRISConfig(lan_access=True,lan_pairing_token='synthetic-test-only',render_fps_limit=60).save()
    async def run():
        transport=httpx.ASGITransport(app=api.app,client=('192.168.1.80',50000))
        async with httpx.AsyncClient(transport=transport,base_url='http://localhost') as client:
            response=await client.put('/api/render-settings',json={'render_fps_limit':0,'render_quality':'saving'})
            assert response.status_code==403
    asyncio.run(run())
    assert IRISConfig.load().render_fps_limit==60 and events==[]


def test_save_failure_never_broadcasts_uncommitted_preferences(server,monkeypatch):
    api,events,_=server
    IRISConfig(render_fps_limit=60,render_quality='balanced').save()
    def fail(_):raise OSError('synthetic full disk')
    monkeypatch.setattr(IRISConfig,'save',fail)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(api.save_render_settings({'render_quality':'saving'}))
    assert exc.value.status_code==500 and events==[]
    assert IRISConfig.load().render_quality=='balanced'
