"""Durable voice claims and real async pipeline; no real TTS or account."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.automation_store import AutomationStore
from core.inbox_api import InboxPresenter
from core.inbox_voice import InboxVoiceOutbox, InboxVoiceService
from test_f07_inbox_delivery import env, finished


@pytest.fixture
def voice(env):
    env.rule = env.store.update_rule(env.rule.id,expected_revision=env.rule.revision,
                                    delivery={'voice':True,'expiration_s':3600})
    env.store.set_automation_preferences(expected_revision=0,return_voice=True)
    _, env.result, _ = finished(env,'voice-one')
    presenter = InboxPresenter(store=lambda:env.store,clock=lambda:env.clock['now'])
    env.notice = presenter.offer('face-voice')
    assert presenter.receipt({**env.notice,'shown':True},surface_id='face-voice')
    env.box = InboxVoiceOutbox(env.store,clock=lambda:env.clock['now'])
    return env


def test_claim_is_durable_ambiguous_before_tts_and_never_claimed_after_restart(voice):
    intent = voice.box.begin(voice.notice,surface_id='face-voice')
    assert intent and voice.store.delivery(voice.result.id).voice_attempts == 1
    delivery = voice.store.delivery(voice.result.id)
    assert delivery.voice_receipt == 'ambiguous' and delivery.read_at is None
    store = AutomationStore(voice.path,clock=lambda:voice.clock['now'])
    assert InboxVoiceOutbox(store,clock=lambda:voice.clock['now']).begin(voice.notice,surface_id='face-voice') is None


@pytest.mark.parametrize('reason',['off','focus','quiet','read','dismiss','snooze','rule-off','expiry','wrong-surface','wrong-token','not-shown'])
def test_policy_or_invalid_visual_receipt_never_claims(voice,reason):
    notice = dict(voice.notice)
    if reason in {'off','focus'}:
        voice.store.set_automation_preferences(expected_revision=1,**({'return_voice':False} if reason=='off' else {'focus':True}))
    if reason=='quiet':
        voice.store.set_inbox_preferences(expected_revision=0,quiet_minutes=60)
    if reason in {'read','dismiss','snooze'}:
        voice.store.act_on_delivery(voice.result.id,reason,expected_revision=voice.store.delivery(voice.result.id).revision,
                                   **({'minutes':10} if reason=='snooze' else {}))
    if reason=='rule-off':
        voice.store.update_rule(voice.rule.id,expected_revision=voice.rule.revision,delivery={'voice':False})
    if reason=='expiry':
        voice.clock['now'] += 3601
    if reason=='wrong-token':
        notice['token']='0'*32
    if reason=='not-shown':
        _, result, _ = finished(voice,'second')
        presenter=InboxPresenter(store=lambda:voice.store,clock=lambda:voice.clock['now'])
        notice=presenter.offer('face-voice')
    assert voice.box.begin(notice,surface_id='other' if reason=='wrong-surface' else 'face-voice') is None
    assert voice.store.delivery(voice.result.id).voice_attempts==0


def test_receipts_are_bound_to_intent_surface_token_and_never_mark_read(voice):
    intent=voice.box.begin(voice.notice,surface_id='face-voice')
    for changes in ({'token':'0'*32},{'surface_id':'other'},{'outcome':'read'},{'outcome':{}}, {'outcome':True}):
        args={'token':intent.token,'surface_id':intent.surface_id,'outcome':'spoken'}|changes
        assert not voice.box.finish(intent.id,**args)
    assert voice.box.finish(intent.id,token=intent.token,surface_id=intent.surface_id,outcome='spoken')
    delivery=voice.store.delivery(voice.result.id)
    assert delivery.voice_receipt=='spoken' and delivery.voice_attempts==1 and delivery.status=='presented' and delivery.read_at is None
    assert not voice.box.finish(intent.id,token=intent.token,surface_id=intent.surface_id,outcome='spoken')


@pytest.mark.parametrize('change',['revoke','focus','quiet','read','edit','expiry'])
def test_policy_is_rechecked_after_slow_synthesis(voice,change):
    client=SimpleNamespace(send_json=AsyncMock())
    # A transport is hashable, like the actual WebSocket.
    class Client:
        send_json=AsyncMock()
    client=Client()
    async def synthesize(text):
        assert voice.store.delivery(voice.result.id).voice_receipt=='ambiguous'
        if change in {'revoke','focus'}:
            voice.store.set_automation_preferences(expected_revision=1,**({'return_voice':False} if change=='revoke' else {'focus':True}))
        elif change=='quiet':
            voice.store.set_inbox_preferences(expected_revision=0,quiet_minutes=60)
        elif change=='read':
            voice.store.act_on_delivery(voice.result.id,'read',expected_revision=voice.store.delivery(voice.result.id).revision)
        elif change=='edit':
            voice.store.update_rule(voice.rule.id,expected_revision=voice.rule.revision,title='Changed')
        elif change=='expiry':
            voice.clock['now']+=61
        return b'RIFFsynthetic',[]
    service=InboxVoiceService(store=lambda:voice.store,eligible=lambda c:True,synthesize=synthesize,clock=lambda:voice.clock['now'])
    asyncio.run(service.offer(client,'face-voice',voice.notice))
    client.send_json.assert_not_called()
    assert voice.store.delivery(voice.result.id).voice_attempts==1


def test_actual_pipeline_sends_once_and_missing_receipt_never_replays(voice):
    class Client:
        def __init__(self):
            self.send_json=AsyncMock()
    client=Client()
    synth=AsyncMock(return_value=(b'RIFFsynthetic',[]))
    service=InboxVoiceService(store=lambda:voice.store,eligible=lambda c:True,synthesize=synth,clock=lambda:voice.clock['now'])
    async def run():
        await service.offer(client,'face-voice',voice.notice)
        payload=client.send_json.call_args.args[0]['payload']
        assert payload['mime']=='audio/wav' and payload['surface_id']=='face-voice'
        assert not await service.receipt(Client(),'face-voice',{**payload,'outcome':'spoken'})
        assert await service.receipt(client,'face-voice',{**payload,'outcome':'spoken'})
        await service.offer(client,'face-voice',voice.notice)
    asyncio.run(run())
    assert synth.await_count==1 and client.send_json.await_count==1


def test_two_surfaces_cannot_play_two_return_summaries_at_once(voice):
    class Client:
        def __init__(self): self.send_json=AsyncMock()
    synth=AsyncMock(return_value=(b'RIFFsynthetic',[]))
    service=InboxVoiceService(store=lambda:voice.store,eligible=lambda c:True,synthesize=synth,clock=lambda:voice.clock['now'])
    first,second=Client(),Client()
    async def run():
        await service.offer(first,'face-voice',voice.notice)
        finished(voice,'voice-second')
        presenter=InboxPresenter(store=lambda:voice.store,clock=lambda:voice.clock['now'])
        notice=presenter.offer('companion-b')
        assert presenter.receipt({**notice,'shown':True},surface_id='companion-b')
        await service.offer(second,'companion-b',notice)
        assert synth.await_count==1 and second.send_json.await_count==0
    asyncio.run(run())


def test_cancel_during_synthesis_keeps_ambiguity_and_releases_the_shared_slot(voice):
    class Client:
        send_json=AsyncMock()
    async def run():
        entered=asyncio.Event()
        async def synth(text):
            entered.set()
            await asyncio.Event().wait()
        service=InboxVoiceService(store=lambda:voice.store,eligible=lambda c:True,synthesize=synth,clock=lambda:voice.clock['now'])
        client=Client()
        task=asyncio.create_task(service.offer(client,'face-voice',voice.notice))
        await asyncio.wait_for(entered.wait(),3)
        await service.stop()
        assert task.cancelled() and not service._tasks and not service._active
        assert client.send_json.call_args.args[0]['type']=='inbox_voice_stop'
        assert voice.store.delivery(voice.result.id).voice_receipt=='ambiguous'
        assert voice.box.begin(voice.notice,surface_id='face-voice') is None
    asyncio.run(run())
