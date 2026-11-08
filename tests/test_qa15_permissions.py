"""QA3: bounded confirmations, scoped remembered operations and section writes."""
import asyncio
from dataclasses import replace

import pytest

from core.app_actions import AppActionBridge, AppActionRegistry, AppOperation
from core.capability_permissions import CapabilityPermissionStore
from test_app_actions import _Surface, _registry_with_surface, _ok


def setup(tmp_path, *, remember=True, reply=None):
    calls = []
    async def handler(args):
        calls.append(args)
        return {'status': 'ok', 'data': {}}
    registry = AppActionRegistry()
    operation = AppOperation(app='qa', action='count', label='contar elementos', capability='apps.actions',
        effect='read', validate_args=dict, project_data=dict, handler=handler,
        permission_scope=lambda args: 'installation-one', remember_args=False)
    registry.register(operation)
    owner = _Surface(reply=reply or (lambda intent: _ok(intent, permission_confirmed=True, permission_remember=remember)))
    store = CapabilityPermissionStore(tmp_path/'permissions.json')
    surfaces = _registry_with_surface('hosts_iris_apps', owner)
    bridge = AppActionBridge(registry, surfaces=lambda: surfaces, capabilities=lambda: store)
    owner.bridge = bridge
    return bridge, registry, store, owner, calls


def test_remember_is_per_operation_and_installation_and_never_changes_global_policy(tmp_path):
    async def run():
        bridge, registry, store, owner, calls = setup(tmp_path)
        request = {'app': 'qa', 'action': 'count'}
        assert (await bridge.execute(request))['status'] == 'ok'
        assert len(owner.sent) == 2
        assert store.evaluate('apps.actions')['mode'] == 'ask'
        assert store.evaluate('google.read')['mode'] == 'ask'
        assert (await bridge.execute(request))['status'] == 'ok'
        assert len(owner.sent) == 2 and len(calls) == 2
        registry.unregister([('qa', 'count')])
        # New reviewed code/installation must ask again, even with the same app id.
        old = AppOperation(app='qa', action='count', label='contar elementos', capability='apps.actions',
            effect='read', validate_args=dict, project_data=dict,
            handler=lambda args: {'status': 'ok'}, permission_scope=lambda args: 'installation-two', remember_args=False)
        registry.register(old)
        assert (await bridge.execute(request))['status'] == 'ok'
        assert len(owner.sent) == 4
        # Another operation also asks; an explicit block overrides every remembered choice.
        registry.register(replace(old, action='other'))
        assert (await bridge.execute({'app': 'qa', 'action': 'other'}))['status'] == 'ok'
        assert len(owner.sent) == 6
        store.set_mode('apps.actions', 'deny')
        assert (await bridge.execute(request))['status'] == 'denied'
        assert len(owner.sent) == 6
        assert not store.snapshot()['action_grants']
    asyncio.run(run())


def test_once_does_not_persist_and_a_remembered_operation_can_be_revoked(tmp_path):
    async def run():
        bridge, _, store, owner, _ = setup(tmp_path, remember=False)
        request = {'app': 'qa', 'action': 'count'}
        await bridge.execute(request)
        assert not store.snapshot()['action_grants']
        owner.reply = lambda intent: _ok(intent, permission_confirmed=True, permission_remember=True)
        await bridge.execute(request)
        grant = store.snapshot()['action_grants'][0]
        store.forget_action(grant['id'])
        await bridge.execute(request)
        assert len(owner.sent) == 6
    asyncio.run(run())


def test_a_policy_changed_during_confirmation_cannot_be_overwritten_by_remember(tmp_path):
    async def run():
        bridge, _, store, owner, calls = setup(tmp_path)
        def answer(intent):
            store.set_mode('apps.actions', 'ask')
            return _ok(intent, permission_confirmed=True, permission_remember=True)
        owner.reply = answer
        result = await bridge.execute({'app': 'qa', 'action': 'count'})
        assert result['status'] == 'denied' and not calls
        assert not store.snapshot()['action_grants']
    asyncio.run(run())


def test_expired_confirmation_is_closed_and_late_yes_never_executes(tmp_path):
    async def run():
        bridge, registry, store, owner, calls = setup(tmp_path)
        owner.reply = None
        object.__setattr__(registry.get('qa', 'count'), 'confirmation_timeout_s', .03)
        result = await bridge.execute({'app': 'qa', 'action': 'count'})
        intent = owner.sent[0]['payload']
        assert result['status'] == 'denied' and 'tiempo' in result['message']
        assert owner.sent[-1]['type'] == 'iris_app_intent_closed'
        assert owner.sent[-1]['payload']['request_id'] == intent['request_id']
        assert not bridge.answer(owner, 'iris_app_result', _ok(intent, permission_confirmed=True, permission_remember=True))
        assert not calls and not store.snapshot()['action_grants']
    asyncio.run(run())


def test_section_is_one_write_and_never_changes_another_section(tmp_path, monkeypatch):
    store = CapabilityPermissionStore(tmp_path/'permissions.json')
    before = store.snapshot()
    writes = []
    original = store._write_unlocked
    def write(document):
        writes.append(1)
        original(document)
    monkeypatch.setattr(store, '_write_unlocked', write)
    after = store.set_section('actions', 'allow')
    assert len(writes) == 1
    old = {row['id']: row['mode'] for row in before['capabilities']}
    for row in after['capabilities']:
        assert row['mode'] == ('allow' if row['category'] == 'actions' else old[row['id']])
    with pytest.raises(ValueError):
        store.set_section('unknown', 'allow')
    assert len(writes) == 1


def test_google_remember_requires_one_explicit_account_and_the_same_query(tmp_path):
    from test_google_actions import Google, registry as google_registry
    async def run():
        google = Google()
        store = CapabilityPermissionStore(tmp_path/'permissions.json')
        owner = _Surface(reply=lambda intent: _ok(intent, permission_confirmed=True, permission_remember=True))
        surfaces = _registry_with_surface('hosts_iris_apps', owner)
        bridge = AppActionBridge(google_registry(google), surfaces=lambda: surfaces, capabilities=lambda: store)
        owner.bridge = bridge
        # Discovery and all-account queries never offer persistent permission.
        await bridge.execute({'app': 'conexiones', 'action': 'status'})
        assert 'remember_scope' not in owner.sent[0]['payload']['permission']
        await bridge.execute({'app': 'mail', 'action': 'list_messages'})
        assert 'remember_scope' not in owner.sent[2]['payload']['permission']
        assert not store.snapshot()['action_grants']
        selected = {'app': 'mail', 'action': 'list_messages', 'args': {'account_id': 'test-account'}}
        await bridge.execute(selected)
        sent = len(owner.sent)
        assert store.snapshot()['action_grants']
        await bridge.execute(selected)
        assert len(owner.sent) == sent
        await bridge.execute({**selected, 'args': {'account_id': 'test-account', 'query': 'otra búsqueda'}})
        assert len(owner.sent) == sent + 2
        assert store.evaluate('google.read')['mode'] == 'ask'
    asyncio.run(run())
