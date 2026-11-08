"""External apps: real stdlib workers, owned skills, transactions and stale versions."""
import asyncio
import copy
import json
from pathlib import Path

import pytest

from core.app_actions import AppActionRegistry, AppActionBridge
from core.app_builder import builder_guides, create_draft, pack_draft
from core.app_extensions import AppExtensions
from core.app_package_tool import inspect_package
from core.app_packages import PackageError, check_package, open_store
from core.app_extension_files import SkillChange, encode_files
from core.widget_catalog import APP_IDS, ICON_NAMES


MANIFEST = {
    'schema': 'iris.app-manifest/v1', 'sdkVersion': 2, 'id': 'contador', 'name': 'Contador',
    'version': '1.0.0', 'description': 'Ejemplo externo', 'icon': 'sparkle', 'presentation': 'window',
    'window': {'width': 420, 'height': 360}, 'ui': {'entry': 'main.js'},
    'extension': {'schema': 'iris.app-extension/v1', 'python': 'main.py', 'skill': True, 'operations': [
        {'action': 'status', 'label': 'Consultar contador', 'effect': 'read', 'arguments': {}},
        {'action': 'increment', 'label': 'Sumar al contador', 'effect': 'local', 'arguments': {'amount': 'integer'}},
    ]},
}
BACKEND = '''from pathlib import Path
import json
def handle(action, args, context):
    file = context.data_dir / 'counter.json'
    state = json.loads(file.read_text()) if file.exists() else {'count': 0}
    if action == 'increment':
        state['count'] += args['amount']
        file.write_text(json.dumps(state))
    return state
'''
SKILL = b'---\nname: iris-app-contador\ndescription: Consulta y actualiza el contador de ejemplo.\n---\nUsa las acciones de contador del catalogo de iris-os.\n'


def package(*, manifest=None, backend=BACKEND, skill=SKILL):
    manifest = copy.deepcopy(manifest or MANIFEST)
    files = {'app.json': json.dumps(manifest).encode(), 'ui/main.js': b'export function createApp() { return {}; }'}
    if manifest['extension'].get('python'):
        files['backend/main.py'] = backend.encode()
    if manifest['extension'].get('skill'):
        files['skill/SKILL.md'] = skill
    return check_package(files, bundled_ids=APP_IDS, icon_names=ICON_NAMES)


def test_sdk2_draft_runs_through_store_with_its_own_skill_and_data(tmp_path):
    async def scenario():
        home, hermes = tmp_path / 'iris', tmp_path / 'hermes'
        registry = AppActionRegistry()
        manager = AppExtensions(lambda: home, registry=registry, hermes_home=lambda: hermes)
        draft = create_draft(home, sdk2=True)
        app_id = draft['id']
        source = Path(draft['source'])
        skill_file = hermes / 'skills' / f'iris-app-{app_id}' / 'SKILL.md'
        assert f"openApp?.('{app_id}')" in (source / 'ui/view.js').read_text(encoding='utf-8')
        assert (source / 'LICENSE').is_file()
        manifest_path = source / 'app.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        manifest['extension']['operations'][1]['audience'] = 'ui'
        manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
        packed = pack_draft(home, app_id)
        checked, _ = inspect_package(Path(packed['package']))
        assert not manager.workers and not registry.catalog() and not skill_file.exists()
        try:
            await manager.install(checked, expected=None)
            assert not manager.workers
            assert f'`{app_id}`' in skill_file.read_text(encoding='utf-8')
            assert registry.get(app_id, 'increment') is None
            answer = await manager.call(app_id, 'increment', {'amount': 3}, caller='ui')
            assert answer['data'] == {'count': 3}
            answer = await registry.get(app_id, 'status').handler({})
            assert answer['data'] == {'count': 3}
            await manager.uninstall(app_id)
            assert not skill_file.exists() and not registry.catalog()
        finally:
            await manager.close()

    for path in builder_guides().values():
        assert Path(path).is_file()
    asyncio.run(scenario())


def test_full_worker_actions_skill_update_uninstall_and_data_retention(tmp_path):
    async def scenario():
        home, hermes = tmp_path / 'iris', tmp_path / 'hermes'
        registry = AppActionRegistry()
        manager = AppExtensions(lambda: home, registry=registry, hermes_home=lambda: hermes)
        try:
            assert not home.exists()
            await manager.reconcile()
            assert not home.exists(), 'catalog reads are passive'
            first = await manager.install(package(), expected=None)
            skill_file = hermes / 'skills/iris-app-contador/SKILL.md'
            assert skill_file.read_bytes() == SKILL
            assert not manager.workers, 'installation does not execute Python'
            assert len(registry.catalog()) == 2
            assert (await manager.call('contador', 'status', {}, caller='ui'))['data'] == {'count': 0}
            assert (await manager.call('contador', 'increment', {'amount': 3}, caller='ui'))['data'] == {'count': 3}
            old_worker = manager.workers['contador']
            old_process = old_worker.process
            old_action = registry.get('contador', 'increment')
            changed = copy.deepcopy(MANIFEST)
            changed['version'] = '1.1.0'
            second = await manager.install(package(manifest=changed, backend=BACKEND.replace("return state", "return state | {'version': 2}")), expected=first['installation'])
            assert old_process.returncode is not None
            assert old_worker.retired
            assert (await old_action.handler({'amount': 100}))['status'] == 'unavailable'
            assert (await manager.call('contador', 'status', {}, caller='ui'))['data'] == {'count': 3, 'version': 2}
            await manager.close()
            assert not registry.catalog()
            await manager.reconcile()
            assert len(registry.catalog()) == 2, 'restart recovers operations from the store'
            assert (await manager.call('contador', 'status', {}, caller='ui'))['data']['count'] == 3
            removed = await manager.uninstall('contador')
            assert removed['id'] == 'contador'
            assert not skill_file.exists()
            assert not registry.catalog() and not manager.workers
            assert (await manager.call('contador', 'status', {}, caller='ui'))['status'] == 'unavailable'
            assert (home / 'apps/app-packages/data/contador/counter.json').exists()
            await manager.install(package(), expected=None)
            assert (await manager.call('contador', 'status', {}, caller='ui'))['data']['count'] == 3
            await manager.uninstall('contador', keep_data=False)
            assert not (home / 'apps/app-packages/data/contador').exists()
        finally:
            await manager.close()
    asyncio.run(scenario())


def test_foreign_and_edited_skills_are_preserved(tmp_path):
    async def scenario():
        home, hermes = tmp_path / 'iris', tmp_path / 'hermes'
        skill = hermes / 'skills/iris-app-contador/SKILL.md'
        skill.parent.mkdir(parents=True)
        skill.write_bytes(b'foreign skill')
        manager = AppExtensions(lambda: home, hermes_home=lambda: hermes)
        with pytest.raises(PackageError, match='conserv'):
            await manager.install(package(), expected=None)
        assert skill.read_bytes() == b'foreign skill'
        assert open_store(home).list() == []
        skill.unlink()
        skill.parent.rmdir()
        first = await manager.install(package(), expected=None)
        skill.write_bytes(SKILL + b'\nPersonal edit\n')
        with pytest.raises(PackageError, match='modificada'):
            await manager.install(package(), expected=first['installation'])
        assert open_store(home).get('contador')['installation'] == first['installation']
        removed = await manager.uninstall('contador')
        assert removed['warnings'] and skill.read_bytes().endswith(b'Personal edit\n')
        assert manager.registry.catalog() == []
        await manager.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('committed', [False, True])
def test_recovery_uses_the_committed_database_identity(tmp_path, committed):
    async def scenario():
        home, hermes = tmp_path / 'iris', tmp_path / 'hermes'
        manager = AppExtensions(lambda: home, hermes_home=lambda: hermes)
        first = await manager.install(package(), expected=None)
        store = open_store(home)
        token = 'ins_' + 'a' * 32
        new = SKILL + b'\nNew version\n'
        mutation = SkillChange(hermes, 'contador', {'SKILL.md': SKILL}, {'SKILL.md': new}, token)
        store.journal('contador', {'app': 'contador', 'home': str(hermes), 'token': token,
                                  'old': encode_files({'SKILL.md': SKILL}), 'new': encode_files({'SKILL.md': new}), 'next': token})
        mutation.apply()
        if committed:
            store.install(package(skill=new), expected=first['installation'], installation=token, skill_home=str(hermes))
        await manager.close()
        await manager.reconcile()
        assert (hermes / 'skills/iris-app-contador/SKILL.md').read_bytes() == (new if committed else SKILL)
        assert store.changes() == []
        assert not mutation.backup.exists() and not mutation.staged.exists()
        await manager.close()
    asyncio.run(scenario())


def test_interrupted_skill_staging_rolls_back_its_owned_partial_files(tmp_path):
    async def scenario():
        home, hermes = tmp_path / 'iris', tmp_path / 'hermes'
        manager = AppExtensions(lambda: home, hermes_home=lambda: hermes)
        first = await manager.install(package(), expected=None)
        store = open_store(home)
        token = 'ins_' + 'b' * 32
        new = {'SKILL.md': SKILL + b'\nNew\n', 'references/help.md': b'Longer instructions'}
        mutation = SkillChange(hermes, 'contador', {'SKILL.md': SKILL}, new, token)
        store.journal('contador', {'app': 'contador', 'home': str(hermes), 'token': token,
                                  'old': encode_files({'SKILL.md': SKILL}), 'new': encode_files(new), 'next': token})
        mutation.staged.mkdir()
        (mutation.staged / 'SKILL.md').write_bytes(SKILL[:10])
        await manager.reconcile()
        assert store.get('contador')['installation'] == first['installation']
        assert (mutation.folder / 'SKILL.md').read_bytes() == SKILL
        assert not mutation.staged.exists() and store.changes() == []
        await manager.close()
    asyncio.run(scenario())


def test_a_database_failure_restores_the_previous_skill_and_operations(tmp_path, monkeypatch):
    from core.app_packages import AppPackageStore, PackageStoreError
    async def scenario():
        home, hermes = tmp_path / 'iris', tmp_path / 'hermes'
        manager = AppExtensions(lambda: home, hermes_home=lambda: hermes)
        first = await manager.install(package(), expected=None)
        original = AppPackageStore.install
        def fail(*args, **kwargs):
            raise PackageStoreError('Synthetic failure before commit')
        monkeypatch.setattr(AppPackageStore, 'install', fail)
        with pytest.raises(PackageStoreError):
            await manager.install(package(skill=SKILL + b'\nNew\n'), expected=first['installation'])
        monkeypatch.setattr(AppPackageStore, 'install', original)
        assert (hermes / 'skills/iris-app-contador/SKILL.md').read_bytes() == SKILL
        assert open_store(home).get('contador')['installation'] == first['installation']
        assert len(manager.registry.catalog()) == 2
        assert open_store(home).changes() == []
        await manager.close()
    asyncio.run(scenario())


def test_slow_or_cancelled_worker_never_blocks_core_or_retries(tmp_path):
    async def scenario():
        home = tmp_path / 'iris'
        manager = AppExtensions(lambda: home, hermes_home=lambda: tmp_path / 'hermes', worker_timeout=.3)
        slow = BACKEND.replace("file = context.data_dir", "import time; time.sleep(10)\n    file = context.data_dir")
        try:
            await manager.install(package(backend=slow), expected=None)
            pulse = asyncio.create_task(asyncio.sleep(.01, result='alive'))
            answer = await manager.call('contador', 'status', {}, caller='ui')
            assert await pulse == 'alive'
            assert answer['status'] == 'unavailable' and 'Pudo haberse hecho' in answer['message']
            assert manager.workers['contador'].process is None
            task = asyncio.create_task(manager.call('contador', 'status', {}, caller='ui'))
            await asyncio.sleep(.05)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert manager.workers['contador'].process is None
        finally:
            await manager.close()
    asyncio.run(scenario())


def test_arguments_and_components_are_checked_without_execution(tmp_path):
    bad = copy.deepcopy(MANIFEST)
    bad['extension']['python'] = '../outside.py'
    with pytest.raises(PackageError):
        package(manifest=bad)
    bad = copy.deepcopy(MANIFEST)
    bad['sdkVersion'] = 1
    with pytest.raises(PackageError, match='requiere sdkVersion 2'):
        package(manifest=bad)
    with pytest.raises(PackageError, match='name debe ser'):
        package(skill=SKILL.replace(b'iris-app-contador', b'iris-os'))
    async def scenario():
        manager = AppExtensions(lambda: tmp_path / 'iris', hermes_home=lambda: tmp_path / 'hermes')
        try:
            await manager.install(package(), expected=None)
            for args in ({'amount': True}, {'amount': '3'}, {'amount': 1, 'extra': 2}, {}):
                with pytest.raises(ValueError):
                    await manager.call('contador', 'increment', args, caller='ui')
            assert not manager.workers
        finally:
            await manager.close()
    asyncio.run(scenario())


def test_uninstall_during_an_action_discards_the_answer_and_cannot_restart_an_old_worker(tmp_path):
    async def scenario():
        manager = AppExtensions(lambda: tmp_path / 'iris', hermes_home=lambda: tmp_path / 'hermes')
        first = await manager.install(package(backend=BACKEND.replace("file = context.data_dir", "import time; time.sleep(.5)\n    file = context.data_dir")), expected=None)
        running = asyncio.create_task(manager.call('contador', 'increment', {'amount': 1}, caller='ui', installation=first['installation']))
        while 'contador' not in manager.workers or manager.workers['contador'].process is None:
            await asyncio.sleep(.01)
        old_worker = manager.workers['contador']
        queued = asyncio.create_task(old_worker.call('increment', {'amount': 100}, caller='ui'))
        await manager.uninstall('contador')
        assert (await running)['status'] == 'unavailable'
        assert (await queued)['status'] == 'unavailable'
        assert old_worker.process is None
        assert not (old_worker.folder).exists(), 'owned executable cache is removed'
        assert not manager.registry.catalog()
        await manager.close()
    asyncio.run(scenario())


def test_profiles_and_environment_are_separate(tmp_path, monkeypatch):
    monkeypatch.setenv('TEST_APP_SECRET', 'must-not-leak')
    async def scenario():
        selected = [tmp_path / 'a']
        manager = AppExtensions(lambda: selected[0], hermes_home=lambda: selected[0] / 'hermes')
        environment = 'import os\ndef handle(action, args, context):\n    return {"leaked": "TEST_APP_SECRET" in os.environ}\n'
        await manager.install(package(backend=environment), expected=None)
        assert (await manager.call('contador', 'status', {}, caller='ui'))['data'] == {'leaked': False}
        action = manager.registry.get('contador', 'status')
        process = manager.workers['contador'].process
        selected[0] = tmp_path / 'b'
        await manager.reconcile()
        assert process.returncode is not None
        assert not manager.registry.catalog()
        assert (await action.handler({}))['status'] == 'unavailable'
        assert not selected[0].exists()
        await manager.close()
    asyncio.run(scenario())


def test_each_operation_reaches_only_its_audience_and_the_backend_knows_the_caller(tmp_path):
    manifest = copy.deepcopy(MANIFEST)
    manifest['extension']['operations'] = [
        {'action': 'status', 'label': 'Consultar contador', 'effect': 'read', 'arguments': {}},
        {'action': 'sync', 'label': 'Sincronizar la ventana', 'effect': 'local', 'arguments': {}, 'audience': 'ui'},
        {'action': 'reflect', 'label': 'Pensar una jugada', 'effect': 'read', 'arguments': {}, 'audience': 'iris'},
    ]
    backend = 'def handle(action, args, context):\n    return {"action": action, "caller": context.caller}\n'

    async def scenario():
        registry = AppActionRegistry()
        manager = AppExtensions(lambda: tmp_path / 'iris', registry=registry, hermes_home=lambda: tmp_path / 'hermes')
        try:
            await manager.install(package(manifest=manifest, backend=backend), expected=None)
            assert {op['action'] for op in registry.catalog()} == {'status', 'reflect'}
            assert registry.get('contador', 'sync') is None, 'a window-only operation is never offered to Iris'
            for action, caller in (('status', 'ui'), ('sync', 'ui')):
                answer = await manager.call('contador', action, {}, caller=caller)
                assert answer['data'] == {'action': action, 'caller': caller}
            for action in ('status', 'reflect'):
                answer = await registry.get('contador', action).handler({})
                assert answer['data'] == {'action': action, 'caller': 'iris'}
            # Knowing the name is not enough: Core refuses the other audience before the worker runs.
            with pytest.raises(ValueError, match='solo para la ventana'):
                await manager.call('contador', 'sync', {}, caller='iris')
            with pytest.raises(ValueError, match='solo para Iris'):
                await manager.call('contador', 'reflect', {}, caller='ui')
            with pytest.raises(ValueError, match='Origen'):
                await manager.call('contador', 'status', {}, caller='app')
        finally:
            await manager.close()
    asyncio.run(scenario())


def test_an_unknown_audience_is_refused_at_review():
    manifest = copy.deepcopy(MANIFEST)
    manifest['extension']['operations'][0]['audience'] = 'window'
    with pytest.raises(PackageError, match='audience'):
        package(manifest=manifest)


def test_store_api_exposes_components_and_pins_manual_calls_to_the_installation(tmp_path):
    import io
    import zipfile
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from core.app_packages import create_app_packages_router
    home = tmp_path / 'iris'
    manager = AppExtensions(lambda: home, hermes_home=lambda: tmp_path / 'hermes')
    from contextlib import asynccontextmanager
    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            await manager.close()
    server = FastAPI(lifespan=lifespan)
    server.include_router(create_app_packages_router(lambda: home, bundled_ids=APP_IDS, icon_names=ICON_NAMES, extensions=manager))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('app.json', json.dumps(MANIFEST))
        archive.writestr('ui/main.js', 'export function createApp() { return {}; }')
        archive.writestr('backend/main.py', BACKEND)
        archive.writestr('skill/SKILL.md', SKILL)
    origin = {'origin': 'http://127.0.0.1'}
    with TestClient(server, base_url='http://127.0.0.1', client=('127.0.0.1', 12345)) as api:
        reviewed = api.post('/api/app-packages/inspect', content=buffer.getvalue(), headers={**origin, 'content-type': 'application/zip'})
        assert reviewed.status_code == 200, reviewed.text
        extension = reviewed.json()['app']['extension']
        assert extension['python'] == 'main.py' and extension['skill'] == 'iris-app-contador'
        assert {op['action']: op['audience'] for op in extension['operations']} == {'status': 'all', 'increment': 'all'}
        assert {risk['code'] for risk in reviewed.json()['app']['risks']} >= {'python', 'skill'}
        assert not manager.workers
        installed = api.post('/api/app-packages/install', json={'token': reviewed.json()['token']}, headers=origin)
        assert installed.status_code == 200, installed.text
        installation = installed.json()['installed']['entry'].split('/')[4]
        endpoint = '/api/app-packages/contador/call/status'
        assert api.post(endpoint, json={}, headers=origin).status_code == 422
        assert api.post(endpoint, json={}, headers={'X-Iris-App-Installation': installation}).status_code == 403
        headers = {**origin, 'X-Iris-App-Installation': installation}
        assert api.post(endpoint, json={}, headers=headers).json()['data'] == {'count': 0}
        assert api.get(f'/api/app-packages/files/{installation}/__extension__/backend/main.py').status_code == 404
        assert api.delete('/api/app-packages/contador?keep_data=false', headers=origin).status_code == 200
        assert api.post(endpoint, json={}, headers=headers).json()['status'] == 'unavailable'
        assert not manager.workers


@pytest.mark.parametrize('description', ['true', '12', '[contador]', '{x: y}', 'Consulta #comentario', 'Consulta: actualiza', '2026-10-23'])
def test_skill_metadata_cannot_change_type_or_text_when_hermes_reads_yaml(description):
    raw = SKILL.replace(b'description: Consulta y actualiza el contador de ejemplo.', ('description: ' + description).encode())
    with pytest.raises(PackageError, match='comillas'):
        package(skill=raw)


@pytest.mark.parametrize('description', ['true', 'Consulta: actualiza #contador'])
def test_quoted_skill_metadata_is_preserved_for_hermes(description):
    import yaml
    raw = SKILL.replace(b'description: Consulta y actualiza el contador de ejemplo.', ('description: ' + json.dumps(description)).encode())
    checked = package(skill=raw)
    stored = checked['files']['__extension__/skill/SKILL.md'].decode()
    assert yaml.safe_load(stored.split('---')[1])['description'] == description


def test_work_under_the_lock_runs_to_the_end_and_a_timeout_still_arrives():
    """Codex SDK03-R1: AppActionBridge cancels a slow action with asyncio.timeout. The thread is
    waited for, its own error is not what the caller sees, and the timeout is still raised."""
    import threading
    import time

    from core.app_extensions import _thread_to_the_end

    finished = threading.Event()

    def slow(fail):
        time.sleep(0.3)
        finished.set()
        if fail:
            raise OSError('disco')
        return 'escrito'

    async def scenario():
        assert await _thread_to_the_end(slow, False) == 'escrito'
        for fail in (False, True):
            finished.clear()
            started = time.monotonic()
            with pytest.raises(TimeoutError):
                async with asyncio.timeout(0.05):
                    await _thread_to_the_end(slow, fail)
            assert finished.is_set() and time.monotonic() - started >= 0.25, 'the thread ended first'
        with pytest.raises(OSError):
            await _thread_to_the_end(slow, True)
    asyncio.run(scenario())
