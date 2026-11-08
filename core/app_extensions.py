"""External apps: lazy stdlib workers, registered actions and owned Hermes skills.

Installation never runs package code. Existing reviewed services keep their separate
in-Core allowlist. The Store's explicit full-trust warning still applies.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import uuid

from core.app_actions import AppActionRegistry, AppOperation, OperationRejected
from core.app_extension_contract import CALLERS, bounded_json, callable_by, validate_arguments
from core.app_extension_files import (SkillChange, decode_files, encode_files, read_tree, write_tree,
                                      _plain_directory, validate_profile_home, plain_bytes, remove_tree)
from core.app_manifest import validate_app_manifest
from core.profile_sqlite import StoreLock


def _components(files, kind):
    prefix = '__extension__/' + kind + '/'
    return {key[len(prefix):]: value for key, value in files.items() if key.startswith(prefix)}


async def _thread_to_the_end(func, /, *args, **kwargs):
    """``asyncio.to_thread`` for work done under ``AppExtensions.lock``.

    Cancelling a coroutine stops its wait, not the thread. A plain ``to_thread`` would then leave
    the ``async with`` and let an uninstall and its cleanup run while the thread still writes
    (Codex SDK03-R1). Here a cancelled caller keeps waiting, and keeps holding the lock, until the
    thread really ends; then the cancellation goes on.
    """
    work = asyncio.ensure_future(asyncio.to_thread(func, *args, **kwargs))
    cancelled = None
    while not work.done():
        try:
            await asyncio.wait({work})  # never cancels ``work``
        except asyncio.CancelledError as error:
            cancelled = error
    if cancelled is not None:
        if not work.cancelled():
            work.exception()  # retrieved: nobody is left to read it
        raise cancelled
    return work.result()


class Worker:
    def __init__(self, folder, entry, data_dir, *, timeout=15, files=None, home=None):
        self.folder, self.entry, self.data_dir = folder, entry, data_dir
        self.timeout = timeout
        self.process = None
        self.lock = asyncio.Lock()
        self.drain = None
        self.retired = False
        self.files, self.home = files, home

    async def retire(self):
        self.retired = True
        await self.close()
        if self.files is not None:
            try:
                await _thread_to_the_end(remove_tree, self.folder, self.files, self.home)
            except (ValueError, OSError):
                pass  # A modified/foreign cache is retained, never recursively deleted.

    async def close(self):
        process, self.process = self.process, None
        if process is not None:
            if process.stdin:
                process.stdin.close()
            try:
                if process.returncode is None:
                    process.terminate()
                await asyncio.wait_for(process.wait(), 2)
            except TimeoutError:
                if process.returncode is None:
                    process.kill()
                await process.wait()
            except ProcessLookupError:
                await process.wait()
        if self.drain is not None:
            self.drain.cancel()
            await asyncio.gather(self.drain, return_exceptions=True)
            self.drain = None

    async def _start(self):
        # No inherited API keys, provider sessions, PYTHONPATH or HERMES_HOME.
        env = {key: value for key, value in os.environ.items()
               if key.upper() in {'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'COMSPEC', 'SYSTEMDRIVE'}}
        env['PYTHONIOENCODING'] = 'utf-8'
        self.process = await asyncio.create_subprocess_exec(
            sys.executable, '-I', '-S', '-B', '-u', str(Path(__file__).with_name('app_worker.py')),
            str(self.folder), self.entry, str(self.data_dir), cwd=self.folder, env=env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, limit=65536,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        if self.retired:
            await self.close()
            raise ValueError('La versión de la app ya fue retirada.')
        process = self.process

        async def drain():
            # Drain without retaining or logging arbitrary app output/secrets.
            while await process.stderr.read(4096):
                pass
        self.drain = asyncio.create_task(drain())

    async def call(self, action, args, *, caller):
        async with self.lock:
            if self.retired:
                return {'status': 'unavailable', 'data': {}, 'message': 'La versión de la app ya fue retirada.'}
            request_id = uuid.uuid4().hex
            try:
                async with asyncio.timeout(self.timeout):
                    if self.process is None or self.process.returncode is not None:
                        await self.close()
                        await self._start()
                    process = self.process
                    line = json.dumps({'schema': 'iris.app-worker/v1', 'id': request_id, 'action': action, 'args': args,
                                       'caller': caller}, ensure_ascii=True, allow_nan=False).encode() + b'\n'
                    process.stdin.write(line)
                    await process.stdin.drain()
                    raw = await process.stdout.readline()
                    if not raw or len(raw) > 65536:
                        raise ValueError('La app no produjo una respuesta válida.')
                    answer = json.loads(raw)
                    if (not isinstance(answer, dict) or answer.get('id') != request_id
                            or answer.get('schema') != 'iris.app-worker-result/v1'
                            or answer.get('status') not in ('ok', 'error')):
                        raise ValueError('La respuesta de la app no coincide con la petición.')
                    bounded_json(answer.get('data'), object_only=True)
                    answer['data'].pop('permission_confirmed', None)
                    return {'status': answer['status'], 'data': answer['data'],
                            'message': str(answer.get('message', ''))[:300]}
            except asyncio.CancelledError:
                await self.close()
                raise
            except TimeoutError:
                await self.close()
                return {'status': 'unavailable', 'data': {}, 'message': 'La app tardó demasiado. Pudo haberse hecho; compruébalo antes de reintentar.'}
            except (OSError, ValueError, RuntimeError):
                await self.close()
                return {'status': 'error', 'data': {}, 'message': 'La app no respondió correctamente. Revisa su backend Python.'}


def describe_action_args(args):
    """Short human-readable context, without nested payloads or credential fields."""
    labels = {'title': 'Título', 'name': 'Nombre', 'text': 'Texto', 'note': 'Nota',
              'query': 'Búsqueda', 'for_iris': 'Para Iris', 'done': 'Completada'}
    parts = []
    for key, value in args.items():
        if any(word in key.lower() for word in ('key', 'token', 'secret', 'password', 'credential')):
            continue
        if isinstance(value, (dict, list)) or value is None:
            continue
        label = labels.get(key, key.replace('_', ' ').capitalize())
        text = ('Sí' if value else 'No') if isinstance(value, bool) else str(value)
        parts.append(f'{label}: {text}')
    return ' · '.join(parts)[:160]


class AppExtensions:
    def __init__(self, home, *, registry=None, hermes_home=None, worker_timeout=15, notices=None,
                 notices_changed=None):
        self.home = home
        # Notices a backend returns under ``iris_notices`` (core.app_notices): the store, or None.
        self.notices = notices or (lambda: None)
        self.notices_changed = notices_changed or (lambda: None)
        self.registry = registry if registry is not None else AppActionRegistry()
        if hermes_home is None:
            from core.workspace_sync import get_hermes_home
            hermes_home = get_hermes_home
        self.hermes_home = hermes_home
        self.worker_timeout = worker_timeout
        self.lock = asyncio.Lock()
        self.profile = None
        self.registered = {}
        self.workers = {}

    async def _retire(self, app_id):
        registered = self.registered.pop(app_id, None)
        if registered:
            self.registry.unregister(registered[1])
        worker = self.workers.pop(app_id, None)
        if worker:
            await worker.retire()

    async def _close(self):
        for app_id in list(set(self.registered) | set(self.workers)):
            await self._retire(app_id)

    async def close(self):
        async with self.lock:
            await self._close()

    async def _profile(self):
        current = validate_profile_home(self.home())
        if current != self.profile:
            await self._close()
            self.profile = current
        return current

    @staticmethod
    def _recover(store):
        for change in store.changes():
            mutation = SkillChange(Path(change['home']), change['app'], decode_files(change['old']),
                                   decode_files(change['new']), change['token'])
            active = store.get(change['app'])
            committed = (active['installation'] if active else None) == change['next']
            mutation.recover(committed)
            store.journal(change['app'], None)

    async def _reconcile(self):
        from core.app_packages import open_store
        current = await self._profile()
        store = await _thread_to_the_end(open_store, current)
        if store is None:
            rows = []
        else:
            def listing():
                lock = StoreLock.for_folder(store.path.parent, store.SPEC)
                lock.acquire()
                try:
                    self._recover(store)
                    return store.list()
                finally:
                    lock.release()
            rows = await _thread_to_the_end(listing)
        wanted = {row['id']: row for row in rows if row['manifest'].get('extension')}
        for app_id, (installation, _) in list(self.registered.items()):
            if app_id not in wanted or wanted[app_id]['installation'] != installation:
                await self._retire(app_id)
        for app_id, row in wanted.items():
            if app_id in self.registered:
                continue
            checked = validate_app_manifest(row['manifest'])
            if not checked['ok'] or checked['manifest']['id'] != app_id:
                raise ValueError('La ficha de una app instalada no es válida.')
            operations = []
            for spec in row['manifest']['extension']['operations']:
                if not callable_by(spec, 'iris'):
                    continue  # the app's own window and widgets only: never in Iris' catalog

                def arguments(args, spec=spec):
                    try:
                        return validate_arguments(spec['arguments'], dict(args))
                    except ValueError as exc:
                        raise OperationRejected(str(exc)) from None

                async def handler(args, app_id=app_id, action=spec['action'], installation=row['installation'], profile=current):
                    return await self.call(app_id, action, args, caller='iris', installation=installation, profile=profile)

                operations.append(AppOperation(
                    app=app_id, action=spec['action'], label=spec['label'], capability='apps.actions', effect=spec['effect'],
                    validate_args=arguments, project_data=lambda data: bounded_json(dict(data), object_only=True),
                    arguments=spec['arguments'], handler=handler, timeout_s=self.worker_timeout + 5,
                    permission_scope=lambda args, installation=row['installation']: installation,
                    remember_args=False, describe=describe_action_args))
            keys = self.registry.register_all(operations)
            self.registered[app_id] = (row['installation'], keys)
        return store

    async def reconcile(self):
        async with self.lock:
            await self._reconcile()

    def _change(self, store, app_id, package=None, expected=None, keep_data=True):
        from core.app_packages import PackageError
        token = 'ins_' + secrets.token_hex(16)
        lock = StoreLock.for_folder(store.path.parent, store.SPEC)
        lock.acquire()
        mutation = None
        journaled = False
        warnings = []
        try:
            self._recover(store)
            old = store.get(app_id)
            if (package is not None or expected is not None) and (old['installation'] if old else None) != expected:
                raise PackageError('La app cambió mientras la revisabas. Vuelve a elegir el archivo.', 'changed')
            old_skill = _components(store.components(old), 'skill') if old and old['manifest'].get('extension', {}).get('skill') else None
            new_skill = _components(package['files'], 'skill') if package and package['manifest'].get('extension', {}).get('skill') else None
            if old_skill is not None or new_skill is not None:
                old_home = store.skill_home(app_id) if old_skill is not None else None
                new_home = str(validate_profile_home(self.hermes_home())) if new_skill is not None else old_home
                if old_home and new_home != old_home:
                    raise PackageError('Esta app administra una skill en otro perfil de Hermes. Conserva ese perfil al actualizar.', 'skill_conflict')
                mutation = SkillChange(Path(new_home), app_id, old_skill, new_skill, token)
                try:
                    mutation.preflight()
                except ValueError:
                    if package is not None:
                        raise
                    warnings.append('La skill fue modificada por ti y se conservó. Sus acciones ya no están disponibles.')
                    mutation = None
                if mutation is not None:
                    store.journal(app_id, {'app': app_id, 'home': str(mutation.home), 'token': token,
                                          'old': encode_files(old_skill), 'new': encode_files(new_skill),
                                          'next': token if package else None})
                    journaled = True
                    mutation.apply()
            if package is not None:
                answer = store.install(package, expected=expected, installation=token,
                                       skill_home=str(mutation.home) if new_skill is not None else None)
            else:
                answer = store.uninstall(app_id)
            if mutation is not None:
                mutation.recover(True)
                store.journal(app_id, None)
            if package is None and answer and not keep_data:
                try:
                    self._remove_data(app_id)
                except (ValueError, OSError):
                    warnings.append('No se borraron los datos porque su carpeta cambió o contiene enlaces.')
            if warnings and answer:
                answer['warnings'] = warnings
            return answer
        except BaseException:
            if mutation is not None and journaled:
                active = store.get(app_id)
                committed = (active['installation'] if active else None) == (token if package else None)
                mutation.recover(committed)
                store.journal(app_id, None)
            raise
        finally:
            lock.release()

    async def _mutate(self, app_id, package=None, expected=None, keep_data=True):
        from core.app_packages import open_store, PackageError
        async with self.lock:
            await self._profile()
            store = await _thread_to_the_end(open_store, self.profile, create=package is not None)
            if store is None:
                return None
            await self._retire(app_id)
            try:
                answer = await _thread_to_the_end(self._change, store, app_id, package, expected, keep_data)
            except PackageError:
                raise
            except ValueError as exc:
                raise PackageError(str(exc), 'component_conflict') from None
            finally:
                await self._reconcile()
            return answer

    async def install(self, package, *, expected):
        return await self._mutate(package['manifest']['id'], package, expected)

    async def uninstall(self, app_id, *, keep_data=True, expected=None):
        return await self._mutate(app_id, keep_data=keep_data, expected=expected)

    def _data_dir(self, app_id):
        from core.app_packages import open_store
        store = open_store(self.profile, create=True)
        folder = self.profile / 'apps' / 'app-packages' / 'data' / app_id
        _plain_directory(folder, self.profile)
        key = 'data-owner:' + app_id
        with store._transaction() as conn:
            row = conn.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
            owner = row[0] if row else secrets.token_hex(16)
            marker = json.dumps({'app': app_id, 'owner': owner}, sort_keys=True).encode()
            if folder.exists():
                if plain_bytes(folder / '.iris-data-owner.json') != marker:
                    raise ValueError('La carpeta de datos no pertenece a esta instalación.')
            else:
                folder.mkdir(parents=True)
                (folder / '.iris-data-owner.json').write_bytes(marker)
            if not row:
                conn.execute('INSERT INTO meta VALUES (?,?)', (key, owner))
        return folder

    def _remove_data(self, app_id):
        from core.app_packages import open_store
        store = open_store(self.profile)
        if store is None:
            return
        folder = self.profile / 'apps' / 'app-packages' / 'data' / app_id
        _plain_directory(folder, self.profile)
        if not folder.exists():
            return
        with store._transaction() as conn:
            key = 'data-owner:' + app_id
            row = conn.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
            if not row or plain_bytes(folder / '.iris-data-owner.json') != json.dumps({'app': app_id, 'owner': row[0]}, sort_keys=True).encode():
                raise ValueError('La carpeta de datos no pertenece a Iris.')
            paths = list(folder.rglob('*'))
            for path in paths:
                if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
                    raise ValueError('La carpeta de datos contiene enlaces.')
                if path.is_dir():
                    _plain_directory(path, self.profile)
                elif path.stat().st_nlink != 1:
                    raise ValueError('La carpeta contiene archivos compartidos.')
            for path in sorted(paths, key=lambda p: len(p.parts), reverse=True):
                path.rmdir() if path.is_dir() else path.unlink()
            folder.rmdir()
            conn.execute('DELETE FROM meta WHERE key=?', (key,))

    def _materialize(self, store, row):
        files = _components(store.components(row), 'backend')
        folder = self.profile / 'apps' / 'app-packages' / 'runtime' / row['installation']
        actual = read_tree(folder, self.profile)
        if actual is None:
            write_tree(folder, files, self.profile)
        elif actual != files:
            raise ValueError('El backend instalado cambió. Reinstala la app para recuperarlo.')
        return folder, self._data_dir(row['id']), files

    async def call(self, app_id, action, args, *, caller, installation=None, profile=None):
        """Run one declared operation for ``caller``: ``ui`` (the Store's route for the app's
        window and widgets) or ``iris`` (the registered action). The app reads it as
        ``context.caller``; an operation declared for the other audience is refused here."""
        if caller not in CALLERS:
            raise ValueError('Origen de la llamada no válido.')
        async with self.lock:
            store = await self._reconcile()
            if profile is not None and profile != self.profile:
                return {'status': 'unavailable', 'data': {}, 'message': 'El perfil de Iris cambió.'}
            row = await _thread_to_the_end(store.get, app_id) if store else None
            if row is None or (installation is not None and row['installation'] != installation):
                return {'status': 'unavailable', 'data': {}, 'message': 'La app fue actualizada o desinstalada. Consulta su catálogo de nuevo.'}
            spec = next((op for op in row['manifest'].get('extension', {}).get('operations', []) if op['action'] == action), None)
            if spec is None:
                raise ValueError('La app no ofrece esa operación.')
            if not callable_by(spec, caller):
                # Knowing the name is not enough: the declared audience decides.
                raise ValueError('Esa operación es solo para Iris.' if caller == 'ui'
                                 else 'Esa operación es solo para la ventana de la app.')
            args = validate_arguments(spec['arguments'], args)
            worker = self.workers.get(app_id)
            folder, data_dir, files = await _thread_to_the_end(self._materialize, store, row)
            if worker is None:
                worker = Worker(folder, row['manifest']['extension']['python'], data_dir,
                                timeout=self.worker_timeout, files=files, home=self.profile)
                self.workers[app_id] = worker
            identity = row['installation']
            app_name = str(row['manifest']['name'])
            current_profile = self.profile
        answer = await worker.call(action, args, caller=caller)
        # A reply from a retired worker cannot revive the old version's result.
        async with self.lock:
            if self.profile != current_profile or Path(self.home()).resolve() != current_profile:
                return {'status': 'unavailable', 'data': {}, 'message': 'El perfil de Iris cambió.'}
            current = await _thread_to_the_end(store.get, app_id)
            if current is None or current['installation'] != identity:
                return {'status': 'unavailable', 'data': {}, 'message': 'La app cambió mientras trabajaba. El resultado anterior se descartó.'}
            requested = answer['data'].pop('iris_notices', None) if answer['status'] == 'ok' else None
            if requested is not None:
                # Core names the app: it knows which worker answered. Written under the same lock
                # that checked the installation, so an uninstall (which also takes it, then cancels
                # the app's notices) never runs in between (Codex SDK03), not even when the action's
                # timeout cancels this call while the thread writes (SDK03-R1).
                notices = self.notices()
                if notices is None:
                    answer['notices'] = {'scheduled': 0, 'cancelled': 0, 'refused': ['Los avisos de apps no están disponibles.']}
                else:
                    from core.app_notices import apply_requests
                    try:
                        answer['notices'] = await _thread_to_the_end(apply_requests, notices, app_id, app_name, requested)
                    finally:
                        self.notices_changed()  # also when cancelled: the write did happen
        return answer

    async def for_installation(self, app_id, installation, work):
        """Run ``work(app_name)`` in a thread only while ``installation`` is the app's current one.

        Holds the lock an uninstall takes, so what ``work`` writes is either refused or written
        before the uninstall, whose cleanup then sees it; a cancelled caller holds it until ``work``
        ends. None when the app is not that installation.
        """
        from core.app_packages import open_store
        async with self.lock:
            current = await self._profile()
            store = await _thread_to_the_end(open_store, current)
            row = await _thread_to_the_end(store.get, app_id) if store is not None else None
            if row is None or row['installation'] != installation:
                return None
            return await _thread_to_the_end(work, str(row['manifest']['name']))
