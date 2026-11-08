"""Async Agents: identity isolation, durable manual history and nonblocking reads."""
import asyncio
import json
import sqlite3
from unittest.mock import Mock

import pytest

from core.async_agents import ManualRunStore, chat_delegations, result_text
from core import async_agents_api as api
from core import websocket_server as ws


def body(response):
    return json.loads(response.body)


@pytest.fixture
def ledger(tmp_path):
    path = tmp_path / 'state.db'
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE async_delegations (delegation_id TEXT, origin_session TEXT, parent_session_id TEXT, state TEXT, dispatched_at REAL, completed_at REAL, task_json TEXT, result_json TEXT, event_json TEXT)')
        rows = [
            ('mine', 'iris:alice', 'old-session', 'completed', 10, 12,
             json.dumps({'goals': ['First', 'Second'], 'context': 'private context'}),
             json.dumps({'results': [{'status': 'completed', 'summary': 'Answer A'}, {'status': 'error', 'error': 'Worker failed'}], 'live_transcripts': 'private reasoning'}), '{}'),
            ('other', 'iris:bob', 'other-session', 'completed', 11, 14, '{}', '{"summary":"Other identity"}', '{}'),
            ('running', 'iris:alice', 'new-session', 'running', 15, None, '{"goal":"Research"}', None, None),
        ]
        conn.executemany('INSERT INTO async_delegations VALUES (?,?,?,?,?,?,?,?,?)', rows)
    return path


def test_chat_is_scoped_to_identity_including_older_sessions_and_read_only(ledger):
    before = ledger.read_bytes()
    result = chat_delegations(session_key='iris:alice', session_id='new-session', db_path=ledger)
    assert [run['id'] for run in result['runs']] == ['running', 'mine']
    assert all('result' not in run for run in result['runs'])
    assert 'private' not in json.dumps(result)
    detail = chat_delegations(session_key='iris:alice', session_id='', run_id='mine', db_path=ledger)['runs'][0]
    assert detail['result'] == 'First\nAnswer A\n\nSecond\nWorker failed'
    assert detail['child_count'] == 2
    assert detail['children'][1]['status'] == 'error'
    assert ledger.read_bytes() == before
    assert chat_delegations(session_key='iris:alice', session_id='', run_id='other', db_path=ledger)['runs'] == []
    assert chat_delegations(session_key='', session_id='', db_path=ledger)['runs'] == []


def test_chat_missing_ledger_does_not_create_database(tmp_path):
    path = tmp_path / 'missing.db'
    assert chat_delegations(session_key='iris:alice', session_id='', db_path=path)['success']
    assert not path.exists()


def test_chat_tolerates_malformed_payloads_and_incompatible_schema(ledger):
    with sqlite3.connect(ledger) as conn:
        conn.execute('UPDATE async_delegations SET task_json=?, result_json=?, event_json=?', ('{"goals":{"bad":1}}', 'not json', '[]'))
    assert chat_delegations(session_key='iris:alice', session_id='', db_path=ledger)['success']
    with sqlite3.connect(ledger) as conn:
        conn.execute('DROP TABLE async_delegations')
        conn.execute('CREATE TABLE async_delegations (id TEXT)')
    assert not chat_delegations(session_key='iris:alice', session_id='', db_path=ledger)['success']


def test_manual_store_survives_reload_preserves_order_and_scopes_gateway(tmp_path):
    path = tmp_path / 'manual.db'
    store = ManualRunStore(path)
    store.save('local', {'id': 'old', 'status': 'running', 'title': 'Old'})
    store.save('local', {'id': 'new', 'status': 'running', 'title': 'New'})
    store.save('local', {'id': 'old', 'status': 'completed', 'result': 'Full answer', 'raw': {'unused': True}})
    store.save('remote', {'id': 'new', 'title': 'Another gateway'})
    reloaded = ManualRunStore(path)
    assert [r['id'] for r in reloaded.list('local')] == ['new', 'old']
    assert reloaded.get('local', 'old')['result'] == 'Full answer'
    assert 'raw' not in reloaded.get('local', 'old')
    assert reloaded.get('remote', 'old') is None


@pytest.fixture
def manual(monkeypatch, tmp_path):
    store = ManualRunStore(tmp_path / 'manual.db')
    monkeypatch.setattr(api, 'ManualRunStore', lambda: store)
    monkeypatch.setattr(ws, 'ManualRunStore', lambda: store)
    monkeypatch.setattr(ws, '_hermes_api_base_url', lambda: 'test-gateway')
    monkeypatch.setattr(ws, '_api_cache', {})
    return store


def test_manual_create_poll_and_detail_use_native_input_output_without_event_stream(monkeypatch, manual):
    request = Mock(return_value={'ok': True, 'data': {'run_id': 'r1', 'status': 'started'}})
    monkeypatch.setattr(ws, '_hermes_api_request', request)
    created = body(asyncio.run(ws.hermes_create_run({'prompt': 'Summarize'})))
    assert created['success']
    assert request.call_args.kwargs['payload']['input'] == 'Summarize'
    assert manual.get('test-gateway', 'r1')['title'] == 'Summarize'
    request.return_value = {'ok': True, 'data': {'run_id': 'r1', 'status': 'completed', 'output': 'Full answer'}}
    snapshot = asyncio.run(api.manual_snapshot(False))
    assert snapshot['runs'][0]['status'] == 'completed'
    assert 'result' not in snapshot['runs'][0]
    detail = body(asyncio.run(ws.hermes_run_detail('r1', force=True)))
    assert detail['run']['result'] == 'Full answer'
    assert detail['run']['title'] == 'Summarize'
    assert all('/events' not in call.args[1] for call in request.call_args_list)
    assert all(call.args[0] == 'POST' or call.args[1] != '/v1/runs' for call in request.call_args_list)
    request.return_value = {'ok': False, 'error': 'offline'}
    offline = body(asyncio.run(ws.hermes_run_detail('r1', force=True)))
    assert offline['success'] and offline['run']['stale']
    assert offline['run']['result'] == 'Full answer'


def test_storage_failure_after_admission_does_not_report_creation_as_failed(monkeypatch, manual):
    monkeypatch.setattr(ws, '_hermes_api_request', Mock(return_value={'ok': True, 'data': {'run_id': 'accepted'}}))
    monkeypatch.setattr(manual, 'save', Mock(side_effect=sqlite3.OperationalError('read only')))
    result = body(asyncio.run(ws.hermes_create_run({'prompt': 'Test', 'input': 'Test'})))
    assert result['success'] and result['run']['id'] == 'accepted'
    assert result['warning']


def test_snapshot_keeps_chat_available_when_manual_storage_fails(monkeypatch):
    monkeypatch.setattr(api, 'chat_scope', lambda: {'session_key': 'mine', 'session_id': 's'})
    monkeypatch.setattr(api, 'chat_delegations', lambda **kw: {'success': True, 'runs': [{'id': 'chat'}]})
    async def broken(force):
        raise sqlite3.OperationalError('broken')
    monkeypatch.setattr(api, 'manual_snapshot', broken)
    result = body(asyncio.run(api.async_agents_snapshot()))
    assert not result['manual']['success']
    assert result['chat']['runs'] == [{'id': 'chat'}]


def test_delegation_detail_rejects_another_identity(monkeypatch, ledger):
    monkeypatch.setattr(api, 'chat_scope', lambda: {'session_key': 'iris:alice', 'session_id': ''})
    monkeypatch.setattr(api, 'chat_delegations', lambda **kw: chat_delegations(**kw, db_path=ledger))
    with pytest.raises(api.HTTPException) as error:
        asyncio.run(api.delegation_detail('other'))
    assert error.value.status_code == 404
    assert body(asyncio.run(api.delegation_detail('mine')))['run']['result']


def test_iris_json_answer_hides_animation_metadata():
    assert result_text('{"texto_hablado":"Hola","animacion":"dance"}') == 'Hola'
    assert result_text({'output': 'Final'}) == 'Final'

def test_chat_uses_active_profile_and_never_reads_local_ledger_for_remote_gateway(monkeypatch, tmp_path, ledger):
    monkeypatch.setattr('core.async_agents.get_hermes_home', lambda: tmp_path)
    profile = tmp_path / 'profiles' / 'work'
    profile.mkdir(parents=True)
    (profile / 'state.db').write_bytes(ledger.read_bytes())
    with sqlite3.connect(tmp_path / 'state.db') as conn:
        conn.execute("DELETE FROM async_delegations")
    scoped = chat_delegations(session_key='iris:alice', session_id='', profile='work')
    assert len(scoped['runs']) == 2
    assert not chat_delegations(session_key='iris:alice', session_id='', profile='../other')['success']
    remote = chat_delegations(session_key='iris:alice', session_id='', local=False, db_path=ledger)
    assert not remote['success'] and remote['runs'] == []


def test_slow_manual_reads_do_not_hold_snapshot_for_every_pending_task(monkeypatch, manual):
    import time
    for number in range(10):
        manual.save('test-gateway', {'id': str(number), 'title': 'Pending', 'status': 'running'})
    def slow(*args, **kwargs):
        time.sleep(0.08)
        return {'ok': False}
    monkeypatch.setattr(ws, '_hermes_api_request', slow)
    monkeypatch.setattr(api, 'SNAPSHOT_TIMEOUT', 0.02)
    async def check():
        started = asyncio.get_running_loop().time()
        result = await api.manual_snapshot(True)
        elapsed = asyncio.get_running_loop().time() - started
        assert elapsed < 0.2
        assert len(result['runs']) == 10
        assert all(run['stale'] for run in result['runs'])
    asyncio.run(check())
