"""IRIS delegates Hermes upgrades and keeps one observable job per process."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from core import hermes_updates as updates
from core import websocket_server as ws


@pytest.mark.parametrize('output,success,status,available', [
    ('Update available: 10 commits behind origin/main.', True, 'update_available', True),
    ('Already up to date.', True, 'current', False),
    ('Local branch is ahead of origin/main.', True, 'current', False),
    ('Unknown new CLI output', True, 'unknown', False),
    ('Network failed', False, 'error', False),
])
def test_official_check_does_not_infer_success_from_different_git_hashes(monkeypatch, tmp_path, output, success, status, available):
    monkeypatch.setattr(updates, 'get_hermes_repo', lambda: tmp_path)
    monkeypatch.setattr(updates, 'hermes_version', lambda: 'Hermes Test')
    monkeypatch.setattr(updates, '_git_output', lambda *args: 'abc')
    monkeypatch.setattr(updates, '_latest_receipt', lambda before: {})
    cli = Mock(return_value={'success': success, 'output': output})
    monkeypatch.setattr(updates, 'run_hermes_cli', cli)
    state = updates.hermes_update_snapshot()
    assert state['status'] == status
    assert state['can_update'] is available
    cli.assert_called_once_with(['update', '--check'], timeout=90)


@pytest.mark.parametrize('exit_ok,outcome,expected', [
    (True, 'success', True), (True, 'partial', False),
    (False, 'failed', False), (True, 'refused', False),
])
def test_upgrade_syncs_dependencies_and_checks_native_receipt(monkeypatch, tmp_path, exit_ok, outcome, expected):
    directory = tmp_path / 'logs' / 'update_receipts'
    directory.mkdir(parents=True)
    monkeypatch.setattr(updates, 'get_hermes_home', lambda: tmp_path)
    monkeypatch.setattr(updates, 'hermes_version', lambda: 'Hermes Updated')

    def cli(args, **kwargs):
        assert args == ['update', '--yes']
        assert kwargs == {'timeout': None}
        (directory / 'this-run.json').write_text(json.dumps({'outcome': outcome}), encoding='utf8')
        return {'success': exit_ok, 'output': 'Native updater result'}

    monkeypatch.setattr(updates, 'run_hermes_cli', cli)
    result = updates.apply_hermes_update()
    assert result['ok'] is expected
    assert result['receipt']['outcome'] == outcome


@pytest.mark.parametrize('outcome,sha,repair', [('failed', 'abc123', True), ('partial', 'abc123', True), ('success', 'abc123', False), ('failed', 'different', False)])
def test_current_code_with_failed_install_can_be_retried(monkeypatch, tmp_path, outcome, sha, repair):
    monkeypatch.setattr(updates, 'get_hermes_repo', lambda: tmp_path)
    monkeypatch.setattr(updates, 'hermes_version', lambda: 'Hermes Updated')
    monkeypatch.setattr(updates, '_git_output', lambda *args: 'abc123')
    monkeypatch.setattr(updates, 'run_hermes_cli', lambda *args, **kwargs: {'success': True, 'output': 'Up to date'})
    monkeypatch.setattr(updates, '_latest_receipt', lambda before: {'outcome': outcome, 'post_update': {'sha': sha}})
    result = updates.hermes_update_snapshot()
    assert result['can_update'] is repair
    assert result['status'] == ('repair_required' if repair else 'current')


@pytest.fixture
def job(monkeypatch):
    monkeypatch.setattr(ws, '_system_update_task', None)
    monkeypatch.setattr(ws, '_system_update_job', {'active': False, 'phase': 'idle'})
    monkeypatch.setattr(ws, '_api_cache', {})
    monkeypatch.setattr(ws.app.state, 'agent', None, raising=False)
    snapshot = {'can_update': True, 'items': [
        {'id': 'iris-os', 'can_update': True}, {'id': 'hermes-agent', 'can_update': True},
    ]}
    monkeypatch.setattr(ws, '_system_update_snapshot', lambda: snapshot)
    native = Mock(return_value={'label': 'Hermes Agent', 'ok': True, 'detail': 'Updated'})
    git = Mock(return_value={'label': 'IRIS OS', 'ok': True})
    monkeypatch.setattr(updates, 'apply_hermes_update', native)
    monkeypatch.setattr(ws, '_apply_git_update', git)
    return native, git


def test_selected_component_uses_one_background_job_and_survives_client_disconnect(job):
    native, git = job
    async def exercise():
        first = await ws.system_updates_apply({'components': ['hermes-agent']})
        duplicate = await ws.system_updates_apply({'components': ['iris-os']})
        assert first.status_code == duplicate.status_code == 202
        assert json.loads((await ws.system_updates_status()).body)['active']
        await ws._system_update_task
        result = json.loads((await ws.system_updates_status()).body)
        assert result['phase'] == 'completed'
        assert result['ok'] is True and result['active'] is False
    asyncio.run(exercise())
    native.assert_called_once()
    git.assert_not_called()


def test_windows_busy_executable_has_actionable_spanish_message(monkeypatch):
    monkeypatch.setattr(updates, '_receipt_state', lambda: {})
    monkeypatch.setattr(updates, '_latest_receipt', lambda before: {'outcome': 'refused'})
    monkeypatch.setattr(updates, 'hermes_version', lambda: 'old')
    monkeypatch.setattr(updates, 'run_hermes_cli', lambda *a, **k: {
        'success': False, 'output': 'Another hermes.exe is running: PID 123'})
    result = updates.apply_hermes_update()
    assert not result['ok']
    assert '/exit' in result['detail'] and 'PID 123' in result['detail']


def test_checks_during_an_update_do_not_run_a_second_native_fetch(job, monkeypatch):
    probe = Mock(side_effect=AssertionError("Must not fetch during installation"))
    monkeypatch.setattr(ws, "_system_update_snapshot", probe)
    ws._system_update_job.update(active=True, phase="updating", snapshot={"items": [{"id": "hermes-agent"}]})
    result = json.loads(asyncio.run(ws.system_updates_check(force=True)).body)
    assert result["can_update"] is False
    assert result["items"] == [{"id": "hermes-agent"}]
    probe.assert_not_called()


def test_partial_upgrade_remains_a_visible_failure(job):
    native, _ = job
    native.return_value = {'label': 'Hermes Agent', 'ok': False, 'detail': 'Dependency sync failed'}
    async def exercise():
        await ws.system_updates_apply({'components': ['hermes-agent']})
        await ws._system_update_task
        result = json.loads((await ws.system_updates_status()).body)
        assert not result['ok']
        assert result['phase'] == 'failed'
        assert 'Dependency sync failed' in result['message']
    asyncio.run(exercise())


def test_active_conversation_blocks_update_admission(job, monkeypatch):
    monkeypatch.setattr(ws.app.state, 'agent', SimpleNamespace(_current_turn_task=SimpleNamespace(done=lambda: False)))
    with pytest.raises(ws.HTTPException) as exc:
        asyncio.run(ws.system_updates_apply({'components': ['hermes-agent']}))
    assert exc.value.status_code == 409
    assert ws._system_update_task is None


@pytest.mark.parametrize('components', [[], ['unknown'], 'hermes-agent', [{}]])
def test_invalid_targets_never_start_an_update(job, components):
    with pytest.raises(ws.HTTPException) as exc:
        asyncio.run(ws.system_updates_apply({'components': components}))
    assert exc.value.status_code == 400
    assert ws._system_update_task is None
