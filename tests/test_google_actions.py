"""Chat uses Iris' Google owner; no accounts, network or provider tokens in tests."""
import asyncio
import json

import pytest
from core.app_actions import AppActionBridge, AppActionRegistry, OperationRejected
from core.google_actions import register_google_actions
from core.surface_registry import SurfaceRegistry
from core.capability_permissions import CapabilityPermissionStore

class Google:
    def __init__(self):
        self.calls=[]
        self.account={"id":"test-account","email":"ana@example.com","auth_state":"connected",
            "services":{k:{"granted":True,"enabled":True} for k in ("gmail.read","calendar.read")},"sync":{}}
    async def snapshot(self):
        self.calls.append("status")
        return {"available":True,"accounts":[self.account],"session":{"authorization_url":"secret"},"client":{"secret":"hidden"}}
    async def mail_cache(self):
        self.calls.append("mail_cache")
        return {"available":True,"accounts":[self.account],"messages":[{"account_id":"test-account","message":{
            "id":"remote-id","labelIds":["INBOX"],"internalDate":"12345","snippet":"texto ficticio",
            "payload":{"headers":[{"name":"Subject","value":"Prueba"},{"name":"From","value":"Ana"}]}}}],"warnings":[]}
    async def mail_body(self, account, message):
        self.calls.append(("body",account,message))
        return {"text":"cuerpo "*3000,"truncated":False,"html":"do not export","attachment":"hidden"}
    async def calendar_cache(self):
        self.calls.append("calendar_cache")
        return {"available":True,"accounts":[self.account],"calendars":[{"account_id":"test-account",
            "calendar":{"id":"primary","summary":"Personal","timeZone":"UTC"},
            "events":[{"id":"evt","summary":"Reunión","description":"private extra",
                "start":{"dateTime":"2026-10-23T10:00:00Z"},"end":{"dateTime":"2026-10-23T11:00:00Z"}}]}],"warnings":[]}

def registry(google):
    r=AppActionRegistry()
    register_google_actions(r,lambda:google)
    return r

def bridge(tmp_path,google,mode):
    # Keep Hermes CLI discovery out of these isolated action tests. The real
    # policy evaluator reads this snapshot and still governs the bridge.
    store=CapabilityPermissionStore(tmp_path/'permissions.json')
    store.snapshot=lambda: {'capabilities':[{'id':'google.read','mode':mode}]}
    store.set_mode('google.read',mode)
    b=AppActionBridge(registry(google),surfaces=SurfaceRegistry,capabilities=lambda:store)
    return b

def test_catalog_is_data_free_and_has_no_google_writes():
    g=Google(); r=registry(g)
    assert {(v['app'],v['action']) for v in r.catalog()} == {('conexiones','status'),('mail','list_messages'),('mail','read_message'),('calendar','list_events')}
    assert all(v['capability']=='google.read' and v['effect']=='read' for v in r.catalog())
    assert g.calls==[] and 'ana@' not in json.dumps(r.catalog())

@pytest.mark.parametrize('mode',['deny','ask'])
def test_no_read_before_permission(tmp_path,mode):
    g=Google(); b=bridge(tmp_path,g,mode)
    answer=asyncio.run(b.execute({'app':'conexiones','action':'status'}))
    assert answer['status'] in ('denied','unavailable')
    assert g.calls==[]

def test_status_does_not_export_session_or_client(tmp_path):
    g=Google(); b=bridge(tmp_path,g,'allow')
    answer=asyncio.run(b.execute({'app':'conexiones','action':'status'}))
    assert answer['status']=='ok' and answer['data']['accounts'][0]['email']=='ana@example.com'
    assert 'secret' not in json.dumps(answer) and 'client' not in answer['data'] and 'session' not in answer['data']


def test_periodic_sync_is_named_separately_from_chat_read_permission(tmp_path):
    async def run():
        g = Google()
        g.account['services']['gmail.read']['enabled'] = False
        b = bridge(tmp_path, g, 'allow')
        status = await b.execute({'app': 'conexiones', 'action': 'status'})
        service = status['data']['accounts'][0]['services']['gmail.read']
        assert service == {'granted': True, 'sync_enabled': False}
        assert 'permiso del chat' in status['data']['sync_note']
        listing = await b.execute({'app': 'mail', 'action': 'list_messages'})
        assert listing['status'] == 'ok' and listing['data']['messages']
    asyncio.run(run())

def test_mail_uses_same_owner_and_rechecks_disconnect(tmp_path):
    async def scenario():
        g=Google(); b=bridge(tmp_path,g,'allow')
        listing=await b.execute({'app':'mail','action':'list_messages'})
        assert listing['status']=='ok' and len(listing['data']['messages'])==1
        key=listing['data']['messages'][0]['id']
        detail=await b.execute({'app':'mail','action':'read_message','args':{'message_id':key}})
        assert detail['status']=='ok' and len(detail['data']['text'])==12000 and detail['data']['truncated']
        assert 'html' not in detail['data'] and 'attachment' not in detail['data']
        g.account['auth_state']='disconnected'
        assert not (await b.execute({'app':'mail','action':'list_messages'}))['data']['messages']
        g.calls.clear()
        assert (await b.execute({'app':'mail','action':'read_message','args':{'message_id':key}}))['status']=='unavailable'
        assert not any(isinstance(c,tuple) for c in g.calls)
    asyncio.run(scenario())

def test_calendar_is_bounded_and_uses_the_owner_cache(tmp_path):
    g=Google(); b=bridge(tmp_path,g,'allow')
    answer=asyncio.run(b.execute({'app':'calendar','action':'list_events','args':{'start':'2026-10-23','end':'2026-10-24'}}))
    assert answer['status']=='ok' and answer['data']['events'][0]['title']=='Reunión'
    assert g.calls==['calendar_cache'] and 'description' not in answer['data']['events'][0]

@pytest.mark.parametrize('app,action,args',[
    ('conexiones','status',{'token':'x'}),('mail','list_messages',{'limit':True}),
    ('mail','list_messages',{'limit':51}),('mail','list_messages',{'query':'x'*201}),
    ('mail','read_message',{'message_id':'../../private'}),
    ('calendar','list_events',{'start':'2026-01-01','end':'2026-03-01'}),
    ('calendar','list_events',{'start':'2026-10-23','end':'2026-10-24','time_zone':'../private'}),
])
def test_invalid_args_never_read(tmp_path,app,action,args):
    g=Google(); b=bridge(tmp_path,g,'allow')
    with pytest.raises(OperationRejected): asyncio.run(b.execute({'app':app,'action':action,'args':args}))
    assert not g.calls

def test_unavailable_owner_is_explicit(tmp_path):
    assert asyncio.run(bridge(tmp_path,None,'allow').execute({'app':'conexiones','action':'status'}))['status']=='unavailable'


@pytest.mark.parametrize('choice', ['allow', 'cancel', 'policy_changed'])
def test_google_data_is_read_only_after_local_confirmation(tmp_path, choice):
    from tests.test_app_actions import _Surface, _registry_with_surface, _ok
    g = Google()
    b = bridge(tmp_path, g, 'ask')
    def reply(intent):
        assert not g.calls
        assert intent['confirm_only'] is True
        assert intent['permission']['capability'] == 'google.read'
        assert 'proveedor de IA' in intent['operation']['summary']
        if choice == 'policy_changed':
            b._capabilities().snapshot = lambda: {'capabilities': [{'id': 'google.read', 'mode': 'deny'}]}
        return _ok(intent, permission_confirmed=choice != 'cancel')
    surface = _Surface(reply)
    surface.bridge = b
    surfaces = _registry_with_surface('hosts_iris_apps', surface)
    b._surfaces = lambda: surfaces
    answer = asyncio.run(b.execute({'app': 'conexiones', 'action': 'status'}))
    assert answer['status'] == ('ok' if choice == 'allow' else 'denied')
    assert g.calls == (['status'] if choice == 'allow' else [])
