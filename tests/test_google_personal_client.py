"""Personal OAuth client import, isolation and API error hygiene; no browser or Google calls."""
import asyncio
import json
import threading

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.google_api import create_google_router
from core.google_client_config import open_client, parse_client, protect_client
from core.google_connections import GoogleConnectionsService, GoogleServiceError
from core.google_contracts import requested_scopes
from core.google_store import AuthorizationCandidate, GoogleStore, profile_store_path
from core.google_worker_client import GoogleWorkerClient

CLIENT = {'installed': {'client_id': 'test-own-client.apps.googleusercontent.com', 'client_secret': 'synthetic-sensitive-value'}}
TEXT = json.dumps(CLIENT)


def make(home):
    home.mkdir(exist_ok=True)
    worker = GoogleWorkerClient(client=None, command=lambda: ['synthetic-never-executed'])
    return GoogleConnectionsService(home, worker=worker, client_configurable=True), worker


def test_read_only_status_and_invalid_json_create_no_store(tmp_path):
    async def run():
        service, _ = make(tmp_path / 'profile')
        status = await service.snapshot()
        assert not status['available'] and not status['client']['configured']
        assert status['client']['revision'] == 0 and status['client']['can_change']
        with pytest.raises(GoogleServiceError) as exc:
            await service.configure_client('bad sensitive json', revision=0)
        assert exc.value.code == 'client_invalid'
        assert not profile_store_path(service.home).exists()
        await service.close()
    asyncio.run(run())


def test_real_dpapi_round_trip_and_other_profile_key_fails():
    client = parse_client(TEXT)
    blob = protect_client(client, b'a' * 32)
    assert CLIENT['installed']['client_secret'].encode() not in blob
    assert open_client(blob, b'a' * 32) == client
    with pytest.raises(Exception):
        open_client(blob, b'b' * 32)


def test_save_reloads_after_restart_and_removal_is_durable(tmp_path):
    async def run():
        service, worker = make(tmp_path / 'profile')
        status = await service.configure_client(TEXT, revision=0)
        assert status['configured'] and status['source'] == 'personal' and status['revision'] == 1
        assert service.available and worker._client == parse_client(TEXT)
        public = json.dumps(await service.snapshot())
        assert 'client_secret' not in public and CLIENT['installed']['client_secret'] not in public
        assert CLIENT['installed']['client_id'] not in public
        await service.close()
        for path in (tmp_path / 'profile').rglob('*'):
            if path.is_file():
                assert CLIENT['installed']['client_secret'].encode() not in path.read_bytes()
        next_service, next_worker = make(tmp_path / 'profile')
        assert (await next_service.snapshot())['client']['configured']
        assert next_worker._client == parse_client(TEXT)
        with pytest.raises(GoogleServiceError) as conflict:
            await next_service.configure_client(TEXT, revision=0)
        assert conflict.value.code == 'revision_conflict'
        assert next_worker._client == parse_client(TEXT)
        removed = await next_service.configure_client(None, revision=1)
        assert removed['revision'] == 2 and not removed['configured']
        await next_service.close()
        third, third_worker = make(tmp_path / 'profile')
        # A removed personal setting must not fall back to a former developer client.
        third_worker.configure_client(parse_client(TEXT))
        assert not (await third.snapshot())['available']
        assert third_worker._client is None
        await third.close()
    asyncio.run(run())


def test_connected_accounts_block_a_client_change_without_destroying_the_old_setting(tmp_path):
    async def run():
        service, worker = make(tmp_path / 'profile')
        await service.configure_client(TEXT, revision=0)
        store = await service._claim()
        account = store.commit_authorization(AuthorizationCandidate(
            client_id=CLIENT['installed']['client_id'], issuer='https://accounts.google.com', subject='fake-subject',
            email='test@example.com', requested_scopes=requested_scopes(['calendar.read']),
            granted_scopes=requested_scopes(['calendar.read']), credential=b'fake-token-envelope'))
        assert not (await service.client_configuration())['can_change']
        with pytest.raises(GoogleServiceError) as exc:
            await service.configure_client(None, revision=1)
        assert exc.value.code == 'client_in_use'
        assert store.oauth_client()[0] == 1 and worker._client == parse_client(TEXT)
        assert store.credential(account.id).credential == b'fake-token-envelope'
        await service.disconnect(account.id)
        assert (await service.configure_client(None, revision=1))['revision'] == 2
        assert store.account(account.id).state == 'disconnected'
        await service.close()
    asyncio.run(run())


def test_running_jobs_block_replacement_and_closed_service_cannot_write(tmp_path):
    async def run():
        service, _ = make(tmp_path / 'profile')
        await service.configure_client(TEXT, revision=0)
        job = asyncio.create_task(asyncio.sleep(60))
        service._refresh_tasks.add(job)
        with pytest.raises(GoogleServiceError) as exc:
            await service.configure_client(None, revision=1)
        assert exc.value.code == 'client_in_use'
        job.cancel()
        await asyncio.gather(job, return_exceptions=True)
        service._refresh_tasks.clear()
        await service.close()
        with pytest.raises(GoogleServiceError):
            await service.configure_client(None, revision=1)
    asyncio.run(run())


@pytest.mark.parametrize('text', ['{}', '[]', '{"type":"service_account"}', '{"web":{}}',
    TEXT.replace('test-own-client', 'bad client'), TEXT.replace('synthetic-sensitive-value', 'bad\\nsecret'),
    TEXT.replace('"installed":', '"installed":{},"installed":'),
    json.dumps({'installed': {**CLIENT['installed'], 'token_uri': 'https://other.example/token'}}), 'x' * 65537], ids=lambda _: 'invalid-document')
def test_invalid_client_documents_are_rejected(text):
    with pytest.raises((ValueError, UnicodeError)):
        parse_client(text)


def test_api_import_status_and_error_responses_never_echo_uploaded_secrets(tmp_path):
    service, _ = make(tmp_path / 'profile')
    app = FastAPI(); app.include_router(create_google_router(lambda: service))
    with TestClient(app, client=('127.0.0.1', 3210)) as client:
        for body in [None, {'revision': True, 'json_text': TEXT}, {'revision': 0, 'json_text': TEXT, 'extra': TEXT},
                     {'revision': 0, 'json_text': 'sensitive-value invalid json'}]:
            response = client.put('/api/connections/google/client', json=body)
            assert response.status_code == 422 and 'no-store' in response.headers['cache-control']
            assert 'synthetic-sensitive-value' not in response.text and 'sensitive-value' not in response.text
        response = client.put('/api/connections/google/client', json={'revision': 0, 'json_text': TEXT})
        assert response.status_code == 200 and response.json()['configured']
        assert 'synthetic-sensitive-value' not in response.text
        assert client.get('/api/connections/google/client').json()['revision'] == 1
        response = client.put('/api/connections/google/client', content=b'x' * (128 * 1024 + 1))
        assert response.status_code == 422
        response = client.request('DELETE', '/api/connections/google/client', json={'revision': 1})
        assert response.status_code == 200 and not response.json()['configured']
    asyncio.run(service.close())


@pytest.mark.parametrize('method', ['GET', 'PUT', 'DELETE'])
def test_client_routes_are_local_and_private_before_reading_a_body(tmp_path, method):
    service, _ = make(tmp_path / 'profile')
    app = FastAPI(); app.include_router(create_google_router(lambda: service))
    with TestClient(app, client=('192.168.1.44', 3210)) as client:
        response = client.request(method, '/api/connections/google/client', content=TEXT)
        assert response.status_code == 403 and 'no-store' in response.headers['cache-control']
    assert not profile_store_path(service.home).exists()
    asyncio.run(service.close())


def test_client_cannot_be_opened_with_another_profiles_protection(tmp_path):
    async def run():
        service, _ = make(tmp_path / 'one')
        await service.configure_client(TEXT, revision=0)
        blob = (await service._claim()).oauth_client()[1]
        await service.close()
        home = tmp_path / 'two'; home.mkdir()
        store = GoogleStore.for_profile(home)
        store.set_oauth_client(blob, expected_revision=0)
        other, worker = make(home)
        snapshot = await other.snapshot()
        assert not snapshot['client']['configured'] and snapshot['client']['error'] == 'client_unavailable'
        assert worker._client is None
        await other.close()
    asyncio.run(run())


def test_competing_imports_with_the_same_revision_have_only_one_winner(tmp_path):
    async def run():
        service, worker = make(tmp_path / 'profile')
        other = TEXT.replace('test-own-client', 'other-client')
        results = await asyncio.gather(service.configure_client(TEXT, revision=0),
                                       service.configure_client(other, revision=0), return_exceptions=True)
        assert sum(isinstance(value, dict) for value in results) == 1
        errors = [value for value in results if isinstance(value, GoogleServiceError)]
        assert len(errors) == 1 and errors[0].code == 'revision_conflict'
        winner = worker._client
        assert (await service.client_configuration())['revision'] == 1
        await service.close()
        restarted, reloaded = make(tmp_path / 'profile')
        await restarted.snapshot()
        assert reloaded._client == winner
        await restarted.close()
    asyncio.run(run())


def test_cancelled_import_collects_its_write_before_close_and_reloads_the_committed_result(tmp_path, monkeypatch):
    import core.google_client_config as config
    entered, release = threading.Event(), threading.Event()
    original = config.protect_client
    def slow(*args):
        entered.set()
        assert release.wait(5)
        return original(*args)
    monkeypatch.setattr(config, 'protect_client', slow)
    async def run():
        service, _ = make(tmp_path / 'profile')
        saving = asyncio.create_task(service.configure_client(TEXT, revision=0))
        assert await asyncio.to_thread(entered.wait, 5)
        saving.cancel()
        closing = asyncio.create_task(service.close())
        await asyncio.sleep(.02)
        assert not closing.done(), 'the profile cannot be released while the write still owns it'
        release.set()
        results = await asyncio.gather(saving, closing, return_exceptions=True)
        assert isinstance(results[0], asyncio.CancelledError)
        restarted, worker = make(tmp_path / 'profile')
        assert (await restarted.snapshot())['client']['configured']
        assert worker._client == parse_client(TEXT)
        await restarted.close()
    asyncio.run(run())
