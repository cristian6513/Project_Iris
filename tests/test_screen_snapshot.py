from __future__ import annotations

import asyncio
import base64
import struct
from types import SimpleNamespace

import pytest

from core import screen_snapshot, vision_model, vision_snapshot
from core.capability_permissions import CapabilityPermissionStore
from core.surface_registry import SurfaceRegistry

IMAGE = 'data:image/jpeg;base64,' + base64.b64encode(
    b'\xff\xd8\xff\xc0' + struct.pack('>HBHHB', 11, 8, 480, 640, 1) + b'\x01\x11\x00\xff\xd9'
).decode()


@pytest.mark.parametrize('text', ['Mira mi pantalla', '¿puedes ver lo que estoy viendo en pantalla?',
    'Lee lo que aparece en mi pantalla', 'Comparte mi pantalla', 'look at my screen',
    'Iris, por favor, mira mi pantalla', 'Toma una captura de pantalla', 'pantallazo'])
def test_explicit_screen_requests(text):
    assert screen_snapshot.is_screen_request(text)


@pytest.mark.parametrize('text', ['Cambia el fondo de pantalla', 'No captures mi pantalla',
    'Mira mi pantalla por la cámara', '¿Qué tengo en la mano?', 'hola',
    'La pantalla se ve muy oscura, ¿qué hago?', '¿Cómo puedo compartir pantalla en Zoom?',
    'Quiero ver una película en pantalla completa', '¿Qué monitor me recomiendas para ver series?',
    'Mira lo que tengo en el escritorio', '¿Cómo hago una captura de pantalla?',
    '¿Qué es un pantallazo?', '¿Puedes explicarme cómo compartir mi pantalla?', 'Lee esto en mi monitor'])
def test_other_sources_or_negated_requests_do_not_capture(text):
    assert not screen_snapshot.is_screen_request(text)


@pytest.fixture
def setup(monkeypatch, tmp_path):
    broker=vision_snapshot.SnapshotBroker(); store=CapabilityPermissionStore(tmp_path/'policy.json')
    registry=SurfaceRegistry(); context={'profile':None,'token':'t'}; current=[True]
    bridge=SimpleNamespace(is_available=True,route_context=lambda:dict(context))
    async def planned(_):
        return {'available':True,'provider':'fixture','model':'vision'}
    monkeypatch.setattr(vision_model,'planned_route',planned)
    monkeypatch.setattr(vision_model,'profile_home',lambda _:tmp_path)
    monkeypatch.setattr(screen_snapshot,'TIMEOUT_S',0.1)
    sent=[]; before=lambda:None
    class Socket:
        async def send_json(self, message):
            sent.append(message)
            if message['type']=='screen_snapshot_request':
                before()
                broker.answer(self,{'request_id':message['payload']['request_id'],**answer})
    face=Socket(); face.local=True
    registry.register_ready(face,{'role':'face','visible':True,'capabilities':{'takes_screen_snapshots':True}})
    answer={'status':'ok','confirmed':True,'image':IMAGE}
    cancels=[]
    async def notify(request, reason):
        cancels.append(reason)
    async def take(origin=face):
        return await screen_snapshot.take_snapshot(origin,SimpleNamespace(bridge=bridge),broker=broker,
            store=store,registry=registry,is_local=lambda s:getattr(s,'local',False),
            turn_is_current=lambda:current[0],notify_cancel=notify)
    def set_before(fn):
        nonlocal before
        before=fn
    return SimpleNamespace(**locals())


def test_one_reviewed_image_has_independent_permission_and_route(setup):
    async def run():
        h=setup
        h.store.set_mode('camera.capture','deny');h.store.set_mode('computer.control','deny')
        assert h.store.evaluate('screen.snapshot')['verdict']=='ask'
        result=await h.take()
        assert result['image']==IMAGE and result['source']=='screen'
        assert result['destination']=={'provider':'fixture','model':'vision'}
        assert await result['authorized']()
        h.store.set_mode('screen.snapshot','deny')
        assert not await result['authorized']()
        assert h.registry.camera_owner() is None and h.broker.pending()==0
    asyncio.run(run())


@pytest.mark.parametrize('which', ['deny','remote','hidden','old','no_model'])
def test_unavailable_or_unauthorized_requests_take_no_image(setup,which):
    h=setup
    if which=='deny': h.store.set_mode('screen.snapshot','deny')
    if which=='remote': h.face.local=False
    if which=='hidden': h.registry.update_state(h.face,{'visible':False})
    if which=='old': h.current[0]=False
    if which=='no_model': h.bridge.is_available=False
    assert 'code' in asyncio.run(h.take())
    assert h.sent==[]


@pytest.mark.parametrize('change', ['profile','turn','permission','surface'])
def test_changes_while_user_selects_or_reviews_discard_the_image(setup,change):
    h=setup
    def mutate():
        if change=='profile':h.context['token']='new'
        if change=='turn':h.current[0]=False
        if change=='permission':h.store.set_mode('screen.snapshot','deny')
        if change=='surface':h.registry.disconnect(h.face)
    h.set_before(mutate)
    assert 'image' not in asyncio.run(h.take())


@pytest.mark.parametrize('answer', [ {'status':'cancelled'}, {'status':'ok','image':IMAGE},
    {'status':'ok','confirmed':True,'image':'https://example.invalid/image.png'}])
def test_cancel_unreviewed_and_invalid_images_are_not_used(setup, answer):
    setup.answer.clear();setup.answer.update(answer)
    assert 'image' not in asyncio.run(setup.take())


def test_screen_description_is_not_presented_as_a_camera_photo():
    text=vision_model.describe_snapshot_for_chat('ayuda',['Aparece un error'],{'model':'fixture'},source='screen')
    assert 'Captura de pantalla' in text and 'Foto de la cámara' not in text
    assert 'no autoriza controlar' in text
