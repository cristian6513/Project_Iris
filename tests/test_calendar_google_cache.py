"""F11 cache access through the existing Google owner, with no network or real accounts."""
from __future__ import annotations
import asyncio
import json
import threading
from types import SimpleNamespace

from core.google_connections import GoogleConnectionsService
from core.google_contracts import requested_scopes
from core.google_store import AuthorizationCandidate, profile_store_path
from core.google_sync import CALENDARS, MAILBOX, SUMMARY, events_collection


async def seeded(home, *, events=2):
    home.mkdir()
    svc = GoogleConnectionsService(home, worker=SimpleNamespace(available=True))
    store = await svc._claim()
    scopes = requested_scopes(['calendar.read', 'gmail.read'])
    account = store.commit_authorization(AuthorizationCandidate(
        client_id='synthetic-client', issuer='https://accounts.google.com', subject='synthetic-sub',
        email='test@example.test', requested_scopes=scopes, granted_scopes=scopes, credential=b'SECRET-CREDENTIAL'))
    common = dict(generation=account.generation, service='calendar.read', deletes=(), checkpoint='PRIVATE-CURSOR',
                  coverage={}, replace=True)
    store.commit_round(account.id, collection=CALENDARS,
                       upserts={'cal': {'id': 'cal', 'summary': 'Pruebas', 'timeZone': 'UTC'}}, **common)
    store.commit_round(account.id, collection=events_collection('cal'),
                       upserts={f'event-{i:05}': {'id': f'event-{i:05}', 'summary': str(i)} for i in range(events)}, **common)
    store.commit_round(account.id, collection=SUMMARY, upserts={}, **common)
    store.commit_round(account.id, generation=account.generation, service='gmail.read', collection=MAILBOX,
                       upserts={'mail': {'id': 'mail', 'snippet': 'PRIVATE-MAIL'}}, deletes=(), checkpoint='MAIL-CURSOR',
                       coverage={}, replace=True)
    return svc, store, account


def test_empty_calendar_cache_does_not_create_a_profile_store(tmp_path):
    async def run():
        home = tmp_path / 'profile'
        home.mkdir()
        svc = GoogleConnectionsService(home, worker=None)
        try:
            assert (await svc.calendar_cache()) == {'available': False, 'accounts': [], 'calendars': [], 'warnings': []}
            assert not profile_store_path(home).exists()
            assert not (home / 'connections').exists()
        finally:
            await svc.close()
    asyncio.run(run())


def test_calendar_cache_never_exposes_credentials_mail_or_cursors(tmp_path):
    async def run():
        svc, store, account = await seeded(tmp_path / 'profile')
        try:
            cache = await svc.calendar_cache()
            assert cache['calendars'][0]['account_id'] == account.id
            assert len(cache['calendars'][0]['events']) == 2
            assert cache['accounts'][0]['sync']['calendar.read']['last_success_at']
            encoded = json.dumps(cache)
            for secret in ['SECRET-CREDENTIAL', 'PRIVATE-CURSOR', 'MAIL-CURSOR', 'PRIVATE-MAIL', 'refresh_token']:
                assert secret not in encoded
        finally:
            await svc.close()
    asyncio.run(run())


def test_calendar_cache_pages_past_the_store_page_limit(tmp_path):
    async def run():
        svc, _, _ = await seeded(tmp_path / 'profile', events=1003)
        try:
            cache = await svc.calendar_cache()
            assert len(cache['calendars'][0]['events']) == 1003
            assert cache['warnings'] == []
        finally:
            await svc.close()
    asyncio.run(run())


def test_calendar_cache_limit_is_global_and_reports_partial_data(tmp_path, monkeypatch):
    monkeypatch.setattr('core.google_connections.MAX_CALENDAR_CACHE_RECORDS', 4)
    async def run():
        svc, _, _ = await seeded(tmp_path / 'profile', events=5)
        try:
            cache = await svc.calendar_cache()
            assert len(cache['calendars'][0]['events']) == 3  # calendar list shares the budget
            assert {'code': 'cache_limit'} in cache['warnings']
        finally:
            await svc.close()
    asyncio.run(run())


def test_disconnected_account_keeps_no_visible_calendar_data(tmp_path):
    async def run():
        svc, store, account = await seeded(tmp_path / 'profile')
        try:
            await svc.disconnect(account.id, clear_cache=False)
            assert store.items(account.id, 'calendar.read', events_collection('cal'))
            cache = await svc.calendar_cache()
            assert cache['accounts'][0]['auth_state'] == 'disconnected'
            assert cache['calendars'] == []
        finally:
            await svc.close()
    asyncio.run(run())


def test_disconnect_waits_for_an_inflight_cache_read(tmp_path, monkeypatch):
    async def run():
        svc, store, account = await seeded(tmp_path / 'profile')
        entered, release = threading.Event(), threading.Event()
        original = store.items
        def slow(*args, **kwargs):
            entered.set()
            assert release.wait(5), 'read gate was not released'
            return original(*args, **kwargs)
        monkeypatch.setattr(store, 'items', slow)
        read = asyncio.create_task(svc.calendar_cache())
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            disconnect = asyncio.create_task(svc.disconnect(account.id, clear_cache=True))
            await asyncio.sleep(0.03)
            assert not disconnect.done()
            release.set()
            assert len((await read)['calendars']) == 1
            await disconnect
            assert (await svc.calendar_cache())['calendars'] == []
        finally:
            release.set()
            await asyncio.gather(read, return_exceptions=True)
            await svc.close()
    asyncio.run(run())
