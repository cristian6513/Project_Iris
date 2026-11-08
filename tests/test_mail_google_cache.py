"""Real GoogleStore/owner; fake records, secret/cursor separation and disconnect race."""
import asyncio
import json
import threading

from core.google_connections import GoogleConnectionsService
from core.google_store import profile_store_path
from core.google_sync import MAILBOX, SUMMARY
from test_calendar_google_cache import seeded


def test_empty_read_does_not_create_connection_store(tmp_path):
    async def scenario():
        home = tmp_path / 'profile'; home.mkdir()
        service = GoogleConnectionsService(home, worker=None)
        try:
            assert (await service.mail_cache()) == {'available': False, 'accounts': [], 'messages': [], 'warnings': []}
            assert not profile_store_path(home).exists()
        finally:
            await service.close()
    asyncio.run(scenario())


def test_mail_projection_pages_over_1000_and_omits_other_services_secrets_and_cursors(tmp_path):
    async def scenario():
        service, store, account = await seeded(tmp_path / 'profile')
        try:
            store.commit_round(account.id, generation=account.generation, service='gmail.read', collection=MAILBOX,
                upserts={f'm-{i:05}': {'id': f'm-{i:05}', 'snippet': 'Email', 'token': 'SECRET-TOKEN'} for i in range(1003)},
                deletes=(), checkpoint='SECRET-CURSOR', coverage={}, replace=True)
            data = await service.mail_cache()
            assert len(data['messages']) == 1003
            serialized = json.dumps(data)
            for secret in ['SECRET-CREDENTIAL', 'SECRET-TOKEN', 'SECRET-CURSOR', 'PRIVATE-CURSOR', 'calendar.read', 'event-']:
                assert secret not in serialized
            await service.disconnect(account.id, clear_cache=False)
            assert store.items(account.id, 'gmail.read', MAILBOX)
            assert (await service.mail_cache())['messages'] == []
        finally:
            await service.close()
    asyncio.run(scenario())


def test_global_limit_reports_incomplete_cache(tmp_path, monkeypatch):
    monkeypatch.setattr('core.google_connections.MAX_MAIL_CACHE_RECORDS', 1)
    async def scenario():
        service, store, account = await seeded(tmp_path / 'profile')
        try:
            store.commit_round(account.id, generation=account.generation, service='gmail.read', collection=MAILBOX,
                upserts={'second': {'id': 'second'}}, deletes=(), checkpoint='cursor', coverage={}, replace=False)
            data = await service.mail_cache()
            assert len(data['messages']) == 1 and data['warnings'][0]['code'] == 'cache_limit'
        finally:
            await service.close()
    asyncio.run(scenario())


def test_cancelled_projection_is_collected_before_disconnect_clears_data(tmp_path, monkeypatch):
    async def scenario():
        service, store, account = await seeded(tmp_path / 'profile')
        entered, release = threading.Event(), threading.Event()
        original = store.items
        def slow(*args, **kwargs):
            entered.set(); assert release.wait(5)
            return original(*args, **kwargs)
        monkeypatch.setattr(store, 'items', slow)
        read = asyncio.create_task(service.mail_cache())
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            read.cancel()
            disconnect = asyncio.create_task(service.disconnect(account.id, clear_cache=True))
            await asyncio.sleep(.03)
            assert not disconnect.done()
            release.set()
            await asyncio.gather(read, return_exceptions=True)
            await disconnect
            assert (await service.mail_cache())['messages'] == []
        finally:
            release.set(); await asyncio.gather(read, return_exceptions=True); await service.close()
    asyncio.run(scenario())
