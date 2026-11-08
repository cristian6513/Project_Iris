import hashlib
from pathlib import Path
import struct
import os
import subprocess

from fastapi import FastAPI
from starlette.testclient import TestClient
import pytest

from core.theme_fonts import FontStore, FONT_SPEC, MAX_FONT_BYTES, create_theme_fonts_router, validate_font
from core.profile_sqlite import profile_store_path

FONT = Path(__file__).resolve().parents[1]/'face/companion/fonts/outfit-latin-400-normal.woff2'

def client(home, peer='127.0.0.1'):
    app=FastAPI();app.include_router(create_theme_fonts_router(lambda:home))
    return TestClient(app,base_url='http://127.0.0.1',client=(peer,12345))

def test_font_read_is_passive_and_stores_exact_bytes_by_hash_across_restart(tmp_path):
    api=client(tmp_path);assert api.get('/api/os/fonts').json()=={'fonts':[]};assert list(tmp_path.iterdir())==[]
    data=FONT.read_bytes();reply=api.post('/api/os/fonts',content=data,headers={'x-iris-font-name':'Mi%20fuente'})
    assert reply.status_code==200;key='font_'+hashlib.sha256(data).hexdigest();assert reply.json()['id']==key
    assert client(tmp_path).get('/api/os/fonts/'+key).content==data
    assert client(tmp_path).get('/api/os/fonts').json()['fonts']==[{'id':key,'label':'Mi fuente','bytes':len(data)}]
    duplicate=api.post('/api/os/fonts',content=data,headers={'x-iris-font-name':'Otro nombre'});assert duplicate.json()['label']=='Mi fuente'
    assert api.get('/api/os/fonts/'+key).headers['content-type']=='font/woff2'
    assert 'no-store' in api.get('/api/os/fonts/'+key).headers['cache-control']
    other=tmp_path/'other';other.mkdir();assert client(other).get('/api/os/fonts/'+key).status_code==404

@pytest.mark.parametrize('payload',[b'',b'not-a-font',b'wOF2'+bytes(44),b'x'*(MAX_FONT_BYTES+1)],ids=['empty','not-font','bad-header','too-large'])
def test_invalid_fonts_do_not_create_a_store(tmp_path,payload):
    assert client(tmp_path).post('/api/os/fonts',content=payload).status_code in (413,422)
    assert list(tmp_path.iterdir())==[]

def test_header_limits_and_collection_fonts_are_rejected():
    original=FONT.read_bytes();validate_font(original)
    for offset,value in [(4,0x74746366),(8,4),(16,16000001),(20,len(original)),(28,len(original)-1)]:
        changed=bytearray(original);changed[offset:offset+4]=struct.pack('>I',value)
        with pytest.raises(ValueError):validate_font(bytes(changed))

def test_maximum_library_and_remove_never_touch_other_data(tmp_path):
    store=FontStore(profile_store_path(FONT_SPEC,tmp_path),root=tmp_path)
    original=FONT.read_bytes();keys=[]
    for n in range(8):
        data=bytearray(original);data[24:26]=struct.pack('>H',n);keys.append(store.add(bytes(data),'Font '+str(n))['id'])
    data=bytearray(original);data[24:26]=struct.pack('>H',9)
    with pytest.raises(ValueError,match='8 fuentes'):store.add(bytes(data),'Limit')
    marker=tmp_path/'keep.txt';marker.write_text('keep');store.remove(keys[0]);assert marker.read_text()=='keep'
    assert len(store.list())==7;assert store.get(keys[0]) is None

@pytest.mark.parametrize('method',['get','post','delete'])
def test_lan_and_foreign_origin_cannot_manage_fonts(tmp_path,method):
    url='/api/os/fonts'+('/font_'+'a'*64 if method=='delete' else '')
    assert getattr(client(tmp_path,'192.168.0.2'),method)(url).status_code==403
    if method!='get':assert getattr(client(tmp_path),method)(url,headers={'origin':'https://elsewhere.example'}).status_code==403
    assert list(tmp_path.iterdir())==[]

@pytest.mark.parametrize('kind',['corrupt','newer'])
def test_unreadable_store_is_reported_without_replacing_its_bytes(tmp_path,kind):
    path=profile_store_path(FONT_SPEC,tmp_path)
    if kind=='corrupt':path.parent.mkdir(parents=True);path.write_bytes(b'not sqlite')
    else:
        store=FontStore(path,root=tmp_path)
        with store._transaction() as conn:conn.execute('PRAGMA user_version = 99')
    before=path.read_bytes();api=client(tmp_path)
    assert api.get('/api/os/fonts').status_code==503
    assert api.post('/api/os/fonts',content=FONT.read_bytes()).status_code==503
    assert path.read_bytes()==before

def test_font_store_refuses_a_folder_link_outside_its_profile(tmp_path):
    home=tmp_path/'profile';home.mkdir();(home/'apps').mkdir();outside=tmp_path/'outside';outside.mkdir()
    target=home/'apps/theme-fonts'
    if os.name=='nt':
        result=subprocess.run(['cmd','/c','mklink','/J',str(target),str(outside)],capture_output=True)
        if result.returncode:pytest.skip('Junction unavailable')
    else:target.symlink_to(outside,target_is_directory=True)
    try:
        api=client(home);assert api.get('/api/os/fonts').status_code==503
        assert api.post('/api/os/fonts',content=FONT.read_bytes()).status_code==503
        assert list(outside.iterdir())==[]
    finally:
        if os.name=='nt':target.rmdir()
        else:target.unlink()
