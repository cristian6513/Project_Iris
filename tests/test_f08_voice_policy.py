"""Compile the actual server policy functions, with no module startup/profile."""
import ast
import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.automation_contracts import AutomationPreferences
from core.surface_registry import SurfaceRegistry
from core.inbox_api import InboxPresenter
from core.request_security import is_loopback_host
from test_f07_inbox_delivery import env
from test_f08_inbox_voice import voice
from unittest.mock import AsyncMock
from core.inbox_voice import InboxVoiceService


@pytest.mark.parametrize('case',['ready','lan','hidden','unfocused','role','incapable','busy','wake','off','focus','native-unknown','other-owner'])
def test_server_voice_gate_requires_local_elected_available_owner_and_both_consents(monkeypatch,case):
    from core import windows_idle
    queried=[]
    monkeypatch.setattr(windows_idle,'windows_notifications_allowed',lambda:queried.append(1) or case!='native-unknown')
    class Socket:
        def __init__(self,host): self.client=SimpleNamespace(host=host)
    ws=Socket('192.168.1.10' if case=='lan' else '127.0.0.1')
    registry=SurfaceRegistry()
    registry.register_ready(ws,{'role':'observer' if case=='role' else 'face', 'visible':case!='hidden',
        'focused':case!='unfocused','capabilities':{'plays_tts':True,'speaks_inbox':case!='incapable'}})
    if case=='other-owner':
        other=Socket('127.0.0.1')
        registry.register_ready(other,{'role':'face','focused':True,'interaction':True})
    prefs=AutomationPreferences(return_voice=case!='off',focus=case=='focus')
    ns={'Any':object,'app':SimpleNamespace(state=SimpleNamespace(hermes_wake=SimpleNamespace(listening=case=='wake'))),
        '_automation_runtime':SimpleNamespace(presence_preferences=lambda:prefs),
        '_surface_registry':registry,'_is_local_client':lambda client:client.client.host=='127.0.0.1',
        '_vision_backend_busy':lambda:case=='busy'}
    source=Path(__file__).resolve().parents[1]/'core/websocket_server.py'
    tree=ast.parse(source.read_text(encoding='utf-8'))
    nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_voice_eligible']
    exec(compile(ast.Module(body=nodes,type_ignores=[]),str(source),'exec'),ns)
    assert ns['_voice_eligible'](ws) is (case=='ready')
    assert queried == ([1] if case in {'ready','native-unknown'} else [])


@pytest.mark.parametrize('case',['shown','deferred','forged','lan'])
def test_actual_ws_handler_schedules_voice_only_after_a_valid_positive_visual_receipt(voice,case):
    class Socket:
        def __init__(self):
            self.client=SimpleNamespace(host='192.168.1.20' if case=='lan' else '127.0.0.1')
            self.send_json=AsyncMock()
    socket=Socket()
    registry=SurfaceRegistry()
    registry.register_ready(socket,{'role':'face','surface_id':'face-voice','focused':True})
    service=SimpleNamespace(offer=AsyncMock())
    scheduled=[]
    runtime=SimpleNamespace(store=lambda:voice.store)
    ns={'asyncio':asyncio,'Any':object,'WebSocket':object,'logger':logging.getLogger('f08-ws'),
        'is_loopback_host':is_loopback_host,'_surface_registry':registry,'_automation_runtime':runtime,
        'InboxPresenter':InboxPresenter,'_inbox_voice':lambda r:service,'_spawn_inbox_task':scheduled.append}
    source=Path(__file__).resolve().parents[1]/'core/websocket_server.py'
    tree=ast.parse(source.read_text(encoding='utf-8'))
    nodes=[n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='_handle_face_message']
    exec(compile(ast.Module(body=nodes,type_ignores=[]),str(source),'exec'),ns)
    # The fixture already recorded a matching visual receipt. Repeating it is
    # accepted by F07, while the durable F08 claim still prevents another voice.
    payload={**voice.notice,'shown':case!='deferred'}
    if case=='forged':payload['token']='0'*32
    async def run():
        await ns['_handle_face_message'](socket,{'type':'inbox_presentation_receipt','payload':payload})
        if scheduled:await asyncio.gather(*scheduled)
    asyncio.run(run())
    assert service.offer.await_count == (1 if case=='shown' else 0)


def test_revoking_one_rules_voice_stops_an_active_summary_even_when_refresh_is_coalesced(voice):
    class Client:
        send_json=AsyncMock()
    client=Client()
    service=InboxVoiceService(store=lambda:voice.store,eligible=lambda c:True,
        synthesize=AsyncMock(return_value=(b'RIFFsynthetic',[])),clock=lambda:voice.clock['now'])
    source=Path(__file__).resolve().parents[1]/'core/websocket_server.py'
    tree=ast.parse(source.read_text(encoding='utf-8'))
    nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_queue_inbox_announcement']
    tasks=[]
    ns={'_inbox_announce_pending':True,'_stop_inbox_voice':service.stop,
        '_spawn_inbox_task':lambda coroutine:tasks.append(asyncio.create_task(coroutine))}
    exec(compile(ast.Module(body=nodes,type_ignores=[]),str(source),'exec'),ns)
    async def run():
        await service.offer(client,'face-voice',voice.notice)
        voice.store.subscribe(ns['_queue_inbox_announcement'])
        voice.store.update_rule(voice.rule.id,expected_revision=voice.rule.revision,delivery={'voice':False})
        assert tasks
        await asyncio.gather(*tasks)
        assert client.send_json.call_args.args[0]['type']=='inbox_voice_stop'
        assert not service._active
        assert voice.store.delivery(voice.result.id).voice_receipt=='ambiguous'
    asyncio.run(run())
