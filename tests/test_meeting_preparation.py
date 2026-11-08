"""F15 with the real Google/profile stores and invented account/event/model only."""
import asyncio
import copy
import json
from datetime import datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.calendar_service import CalendarService
from core.calendar_runtime import CalendarRuntime
from core.google_sync import events_collection, CALENDARS
from core.meeting_preparation import MeetingPreparationRuntime
from core.meeting_store import MeetingError, MeetingStore
from core.meeting_worker import validate_result
from core.meeting_api import create_meeting_router
from core.inbox_api import InboxService
from test_calendar_google_cache import seeded

DAY = '2026-10-17'
ROUTE = {'provider': 'custom:meeting', 'model': 'planner', 'endpoint': 'http://127.0.0.1:1/v1/',
         'transport': 'chat_completions', 'fingerprint': 'f'*64}
RESULT = {'summary': 'Revisar el proyecto, sin antecedentes confirmados.', 'points': ['Confirmar el alcance.'],
          'questions': ['¿Qué falta por definir?'], 'sources': ['event']}
EVENT = {'id': 'meeting', 'summary': 'Revisión inventada', 'start': {'dateTime': DAY+'T16:00:00Z'},
         'end': {'dateTime': DAY+'T17:00:00Z'}, 'description': 'Contexto confidencial de ejemplo',
         'location': 'No enviar esta sala', 'attendees': [{'email': 'private@example.test'}],
         'hangoutLink': 'https://meet.google.com/abc-defg-hij'}


class Fixture:
    async def setup(self, path, **kwargs):
        self.now = 1792252800.0
        self.owner, self.google_store, self.account = await seeded(path/'profile', events=0)
        self.home = path/'profile'
        self.calls = []
        self.gate = None
        self.response = {'ok': True, 'route': ROUTE, 'result': copy.deepcopy(RESULT), 'reported_model': 'planner-dated'}
        self.write(EVENT)
        snapshot = await CalendarService(self.owner).snapshot(DAY, '2026-10-18')
        self.event = snapshot['events'][0]
        self.enabled = True
        self.runtime = MeetingPreparationRuntime(self.home, google=lambda: self.owner,
            invoke=self.invoke, clock=lambda: self.now, hermes_home=self.home/'fake-hermes',
            functional_enabled=lambda: self.enabled, **kwargs)
        return self

    def write(self, event):
        self.google_store.commit_round(self.account.id, generation=self.account.generation,
            service='calendar.read', collection=events_collection('cal'), upserts={'meeting': event},
            deletes=(), checkpoint='fake', coverage={}, replace=True)

    async def invoke(self, home, request):
        self.calls.append(copy.deepcopy(request))
        if request['command'] == 'probe':
            return {'ok': True, 'route': ROUTE}
        if self.gate:
            await self.gate.wait()
        return copy.deepcopy(self.response)

    async def preview(self, **kw):
        return (await self.runtime.preview(self.event['id'], DAY, ROUTE['provider'], ROUTE['model'], **kw))['preview']

    async def start(self, **kw):
        preview = await self.preview(**kw)
        await self.runtime.start(preview['id'], confirmed=True)
        return preview['id']

    async def complete(self):
        await asyncio.gather(*list(self.runtime._tasks.values()))
        return (await self.runtime.snapshot())['jobs'][0]

    async def close(self):
        await self.runtime.close(); await self.owner.close()


@pytest.mark.asyncio
async def test_open_is_local_passive_and_preview_minimizes_context(tmp_path):
    f = await Fixture().setup(tmp_path)
    try:
        assert (await f.runtime.snapshot())['jobs'] == [] and f.calls == []
        assert not (f.home/'apps/calendar/preparations.sqlite3').exists()
        p = await f.preview()
        assert f.calls == [{'command': 'probe', 'provider': ROUTE['provider'], 'model': ROUTE['model']}]
        assert set(p['sources']['event']) == {'title', 'start', 'end', 'all_day'}
        assert all(secret not in json.dumps(p) for secret in ['private@example.test', 'No enviar', 'confidencial', 'SECRET-CREDENTIAL', 'meet.google'])
        p = await f.preview(description=True, notes='Una nota propia')
        assert p['sources']['notes'] == 'Una nota propia'
        assert 'confidencial' in p['sources']['event']['description']
    finally:
        await f.close()


@pytest.mark.asyncio
async def test_exact_consent_idempotency_persistence_and_separate_inbox(tmp_path):
    f = await Fixture().setup(tmp_path)
    try:
        p = await f.preview()
        with pytest.raises(MeetingError):
            await f.runtime.start(p['id'], confirmed=False)
        await f.runtime.start(p['id'], confirmed=True)
        job = await f.complete()
        assert job['state'] == 'completed' and job['reported_model'] == 'planner-dated'
        assert job['result'] == RESULT
        await f.runtime.start(p['id'], confirmed=True)
        assert len([c for c in f.calls if c['command'] == 'prepare']) == 1
        inbox = InboxService(store=lambda: None, store_problem=lambda: '', meeting=lambda: f.runtime)
        item = inbox.snapshot()['items'][0]
        assert item['origin'] == 'meeting' and item['outcome'] == 'completed' and item['join_url'] is None
        assert item['calendar_event_id'] == f.event['id']
        inbox.act(item['key'], 'read', expected_revision=item['delivery']['revision'])
        assert inbox.snapshot()['unread'] == 0
        reopened = MeetingPreparationRuntime(f.home, google=lambda: f.owner, invoke=f.invoke)
        assert (await reopened.snapshot())['jobs'][0]['result'] == RESULT
        await reopened.close()
    finally:
        await f.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['event', 'cancelled', 'account', 'expired', 'disabled'])
async def test_changes_after_preview_refuse_before_send(tmp_path, change):
    f = await Fixture().setup(tmp_path)
    try:
        p = await f.preview()
        if change == 'event': f.write({**EVENT, 'summary': 'Otra reunión'})
        elif change == 'cancelled': f.write({**EVENT, 'status': 'cancelled'})
        elif change == 'account': await f.owner.disconnect(f.account.id, clear_cache=False)
        elif change == 'expired': f.now += 301
        else: f.enabled = False
        with pytest.raises(MeetingError): await f.runtime.start(p['id'], confirmed=True)
        assert not any(c['command'] == 'prepare' for c in f.calls)
    finally:
        await f.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['event', 'cancelled', 'disabled'])
async def test_late_results_are_discarded_when_sources_change(tmp_path, change):
    f = await Fixture().setup(tmp_path)
    try:
        f.gate = asyncio.Event()
        await f.start()
        while not any(c['command'] == 'prepare' for c in f.calls): await asyncio.sleep(.001)
        if change == 'event': f.write({**EVENT, 'description': 'Descripción nueva'})
        elif change == 'cancelled': f.write({**EVENT, 'status': 'cancelled'})
        else: f.enabled = False
        f.gate.set()
        job = await f.complete()
        assert job['result'] is None and job['state'] == 'failed'
    finally:
        await f.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['disconnect', 'services', 'client-close', 'cancel', 'core-close'])
async def test_google_and_core_collect_owned_model_work(tmp_path, change):
    f = await Fixture().setup(tmp_path)
    try:
        f.gate = asyncio.Event()
        identity = await f.start()
        while not any(c['command'] == 'prepare' for c in f.calls): await asyncio.sleep(.001)
        if change == 'disconnect': await f.owner.disconnect(f.account.id, clear_cache=False)
        elif change == 'services': await f.owner.set_services(f.account.id, ['gmail.read'], expected_revision=f.account.revision)
        elif change == 'client-close': await f.owner.close()
        elif change == 'cancel': await f.runtime.cancel(identity)
        else: await f.runtime.close()
        row = f.runtime._store.get(identity)
        assert row['state'] == 'cancelled' and row['result'] is None
        assert not any(not task.done() for task in f.owner._mail_tasks)
    finally:
        await f.close()


@pytest.mark.asyncio
async def test_cancel_wins_even_when_provider_ignores_cancellation(tmp_path):
    f = await Fixture().setup(tmp_path)
    original = f.runtime.invoke
    entered = asyncio.Event()
    async def resistant(home, request):
        if request['command'] == 'probe': return await original(home, request)
        entered.set()
        try: await asyncio.sleep(30)
        except asyncio.CancelledError: return copy.deepcopy(f.response)
    f.runtime.invoke = resistant
    try:
        identity = await f.start(); await entered.wait()
        await f.runtime.cancel(identity)
        job = f.runtime._store.get(identity)
        assert job['state'] == 'cancelled' and job['result'] is None
    finally:
        await f.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['route', 'invalid', 'provider-error', 'timeout'])
async def test_failures_never_retry_or_change_reminder_preferences(tmp_path, mode):
    f = await Fixture().setup(tmp_path, timeout=2 if mode == 'timeout' else 60)
    try:
        if mode == 'route': f.response['route'] = {**ROUTE, 'model': 'other'}
        if mode == 'invalid': f.response['result']['sources'] = ['invented-file']
        if mode == 'provider-error': f.response = {'ok': False, 'code': 'private-provider-error'}
        if mode == 'timeout': f.gate = asyncio.Event()
        await f.start(); job = await f.complete()
        assert job['state'] == 'failed' and job['result'] is None
        assert len([c for c in f.calls if c['command'] == 'prepare']) == 1
        assert not (f.home/'apps/calendar/calendar.sqlite3').exists()
        assert job['error'] in {'request_unknown', 'model_failed'}
    finally:
        await f.close()


@pytest.mark.asyncio
async def test_unknown_after_restart_does_not_resend_and_budget_is_atomic(tmp_path):
    f = await Fixture().setup(tmp_path)
    try:
        p = await f.preview()
        internal = f.runtime._previews[p['id']]
        store = MeetingStore.for_profile(f.home)
        row, fresh = store.reserve(internal, f.now)
        assert fresh and row['state'] == 'unknown'
        await f.runtime.start(p['id'], confirmed=True)
        assert len(f.calls) == 1
        with pytest.raises(MeetingError): store.reserve({**internal, 'id': 'a'*32}, f.now)
        for i in range(9):
            f.now += 181
            store.reserve({**internal, 'id': f'{i:032x}'}, f.now)
        f.now += 181
        with pytest.raises(MeetingError, match='diario'): store.reserve({**internal, 'id': 'b'*32}, f.now)
    finally:
        await f.close()


@pytest.mark.parametrize('patch', [{'sources': ['https://evil.test']}, {'points': []}, {'summary': 'x'*1501}, {'questions': ['\x00']}, {'extra': True}])
def test_model_output_requires_known_sources_and_bounded_plain_text(patch):
    with pytest.raises(ValueError): validate_result({**RESULT, **patch})


@pytest.mark.asyncio
async def test_timeout_stays_unknown_even_if_transport_suppresses_cancellation(tmp_path):
    f = await Fixture().setup(tmp_path, timeout=2)
    original = f.runtime.invoke
    async def slow(home, request):
        if request['command'] == 'probe': return await original(home, request)
        try: await asyncio.sleep(30)
        except asyncio.CancelledError: return copy.deepcopy(f.response)
    f.runtime.invoke = slow
    try:
        await f.start(); job = await f.complete()
        assert job['state'] == 'failed' and job['error'] == 'request_unknown' and job['result'] is None
    finally: await f.close()


@pytest.mark.asyncio
async def test_close_collects_probe_and_cannot_publish_a_late_preview(tmp_path):
    f = await Fixture().setup(tmp_path)
    entered = asyncio.Event()
    async def slow(home, request):
        entered.set(); await asyncio.sleep(30)
    f.runtime.invoke = slow
    try:
        preview = asyncio.create_task(f.preview()); await entered.wait()
        await f.runtime.close()
        assert preview.cancelled() and f.runtime._previews == {}
    finally: await f.close()


@pytest.mark.asyncio
async def test_deterministic_reminder_fires_while_model_is_waiting_and_survives_failure(tmp_path):
    f = await Fixture().setup(tmp_path)
    f.now = datetime.fromisoformat(DAY+'T15:00:00+00:00').timestamp()
    calendar = CalendarRuntime(f.home, google=lambda: f.owner, clock=lambda: f.now)
    calendar.set_preferences(revision=0, time_zone='UTC', reminders_enabled=True)
    try:
        f.gate = asyncio.Event(); await f.start()
        while not any(c['command'] == 'prepare' for c in f.calls): await asyncio.sleep(.001)
        notices = await calendar.tick(force=True)
        assert notices and notices[0].event_id == f.event['id']
        f.response = {'ok': False}; f.gate.set(); assert (await f.complete())['state'] == 'failed'
        again = await calendar.tick(force=True)
        assert [n.id for n in notices] == [n.id for n in again]
        assert calendar.preferences()['reminders_enabled'] is True
    finally:
        await calendar.close(); await f.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('all_day', [False, True])
async def test_inbox_destination_uses_civil_day_separately_from_utc_source_lookup(tmp_path, all_day):
    f = await Fixture().setup(tmp_path)
    calendar = CalendarRuntime(f.home, google=lambda: f.owner)
    calendar.set_preferences(revision=0, time_zone='America/Bogota')
    f.runtime.calendar = lambda: calendar
    try:
        if all_day:
            f.google_store.commit_round(f.account.id,generation=f.account.generation,service='calendar.read',collection=CALENDARS,
                upserts={'cal': {'id':'cal','summary':'Inventado','timeZone':'Pacific/Kiritimati'}},deletes=(),checkpoint='fake',coverage={},replace=True)
            f.write({**EVENT,'start':{'date':DAY},'end':{'date':'2026-10-18'}})
            lookup='2026-10-16'
        else:
            f.write({**EVENT,'start':{'dateTime':'2026-10-18T00:30:00Z'},'end':{'dateTime':'2026-10-18T01:30:00Z'}})
            lookup='2026-10-18'
        snapshot=await CalendarService(f.owner).snapshot('2026-10-16','2026-10-19')
        event=snapshot['events'][0]
        p=(await f.runtime.preview(event['id'],lookup,ROUTE['provider'],ROUTE['model']))['preview']
        await f.runtime.start(p['id'],confirmed=True);job=await f.complete()
        assert job['date']==lookup and job['calendar_date']==DAY
        inbox=InboxService(store=lambda:None,store_problem=lambda:'',meeting=lambda:f.runtime)
        assert inbox.snapshot()['items'][0]['calendar_date']==DAY
    finally:
        await calendar.close();await f.close()


@pytest.mark.parametrize('peer', ['192.168.1.9', '::ffff:192.168.1.9'])
@pytest.mark.parametrize('path', ['', '/preview', '/abc/start', '/abc/cancel'])
def test_private_api_rejects_lan_before_work(peer, path):
    class Runtime:
        def __getattr__(self, name): raise AssertionError('LAN reached runtime')
    app = FastAPI(); app.include_router(create_meeting_router(lambda: Runtime()))
    with TestClient(app, client=(peer, 43000), base_url='http://127.0.0.1') as client:
        response = client.get('/api/calendar/preparation') if not path else client.post('/api/calendar/preparation'+path, json={})
        assert response.status_code == 403 and 'no-store' in response.headers['cache-control']
