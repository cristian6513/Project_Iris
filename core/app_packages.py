"""F17: install IRIS OS apps from a file — the first, simple Iris Store.

A package is the folder of an app (`app.json` + `ui/`), as a .zip or as the files of a folder.
Core checks it with the same rules as the build (scripts/build_app_catalog.mjs) and keeps its
files inside a profile SQLite store, never as loose files: nothing is extracted to disk, no
archive path becomes a file path, and uninstalling deletes rows, not folders.

Trust model chosen by the user for this phase (2026-10-20): an installed app runs like the
bundled ones, with the same access as Iris. Iris says so before installing and lists what its
code appears to do (`scan_risks`, a heuristic, not a guarantee). Installed apps bring no Python
service, no Hermes skill and request no capability: only interface and widgets.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import re
import secrets
import sqlite3
import stat
import threading
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from core.app_manifest import validate_app_manifest
from core.app_extension_files import component_files
from core.calendar_api import _PrivateRoute
from core.profile_paths import ProfilePathError
from core.profile_sqlite import ProfileSqliteStore, StoreSpec, profile_store_path
from core.request_security import is_loopback_host, is_trusted_local_origin

MAX_REQUEST_BYTES = 6 * 1024 * 1024        # the .zip, or the folder sent as JSON (base64)
MAX_ENTRIES = 400                          # every entry of the archive, kept or not
MAX_MANIFEST_BYTES = 16 * 1024             # as the build
MAX_UI_FILE_BYTES = 512 * 1024             # as the build
MAX_UI_BYTES = 2 * 1024 * 1024             # as the build
MAX_PACKAGES = 32
UI_EXTENSIONS = {'js': 'text/javascript', 'css': 'text/css', 'svg': 'image/svg+xml',
                 'png': 'image/png', 'webp': 'image/webp', 'json': 'application/json'}
UI_SEGMENT = re.compile(r'[a-z0-9][a-z0-9_.-]{0,63}')
INSTALLATION = re.compile(r'ins_[0-9a-f]{32}')
TOKEN = re.compile(r'[A-Za-z0-9_-]{32,64}')
CREATE_APP = re.compile(r'^export (?:async )?function createApp\(', re.M)
CREATE_WIDGET = re.compile(r'^export (?:async )?function createWidget\(', re.M)
# Everything besides app.json and ui/ (README, tests, licence) ships with the package but is
# not installed; the Store lists it. A service runs inside Core and a skill inside Hermes: both need a maintainer's review (F09).
REVIEW_ONLY = {'backend': 'un servicio de Python (backend/)', 'skill': 'una skill de Hermes (skill/)'}
RESERVED_IDS = frozenset({'store', 'desktop', 'apps', 'iris', 'hermes', 'core', 'face', 'system'})


class PackageError(ValueError):
    def __init__(self, message: str, code: str = 'invalid_package'):
        super().__init__(message)
        self.code = code


class PackageStoreError(RuntimeError):
    pass


# ---------------------------------------------------------------- reading a package

def _entry_name(raw: str) -> list[str]:
    if not isinstance(raw, str) or not raw or len(raw) > 240 or '\\' in raw or ':' in raw \
            or any(ord(char) < 32 or ord(char) == 127 for char in raw) or raw.startswith('/'):
        raise PackageError('El paquete contiene un nombre de archivo no válido.')
    parts = raw.rstrip('/').split('/')
    if any(part in ('', '.', '..') for part in parts):
        raise PackageError('El paquete contiene rutas no válidas («..» o carpetas vacías).')
    return parts


def read_zip(data: bytes) -> tuple[dict[str, bytes], list[str]]:
    """Files of a .zip as {posix path: bytes}, bounded before anything is decompressed."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
        infos = archive.infolist()
    except (zipfile.BadZipFile, OSError, ValueError, EOFError):
        raise PackageError('El archivo no es un .zip válido.') from None
    if len(infos) > MAX_ENTRIES:
        raise PackageError(f'El paquete tiene más de {MAX_ENTRIES} archivos.')
    entries = []
    for info in infos:
        parts = _entry_name(info.filename)
        if info.is_dir():
            continue
        mode = info.external_attr >> 16
        if stat.S_ISLNK(mode):
            raise PackageError('El paquete contiene enlaces; no se admiten.')
        if info.flag_bits & 0x1:
            raise PackageError('El paquete está cifrado con contraseña.')
        if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            raise PackageError('El .zip usa una compresión no admitida. Usa la compresión normal (Deflate).')
        entries.append(('/'.join(parts), info))
    return _bounded(entries, lambda info, limit: _read_member(archive, info, limit))


def _read_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo, limit: int) -> bytes:
    if info.file_size > limit:
        raise PackageError(f'{info.filename}: supera el tamaño permitido.')
    try:
        with archive.open(info) as handle:
            data = handle.read(limit + 1)
    except (zipfile.BadZipFile, OSError, ValueError, EOFError, RuntimeError, NotImplementedError):
        raise PackageError(f'{info.filename}: no se pudo leer del .zip.') from None
    if len(data) > limit or len(data) != info.file_size:
        raise PackageError(f'{info.filename}: su tamaño no coincide con el declarado.')
    return data


def read_folder(payload) -> tuple[dict[str, bytes], list[str]]:
    """The files of a folder chosen in the Store, sent as JSON {files: [{path, data}]} (base64)."""
    files = payload.get('files') if isinstance(payload, dict) else None
    if not isinstance(files, list) or not files:
        raise PackageError('La carpeta está vacía.')
    if len(files) > MAX_ENTRIES:
        raise PackageError(f'La carpeta tiene más de {MAX_ENTRIES} archivos.')
    entries = []
    for item in files:
        if not isinstance(item, dict) or set(item) != {'path', 'data'} or not isinstance(item['data'], str):
            raise PackageError('La carpeta no se envió en el formato esperado.')
        entries.append(('/'.join(_entry_name(item['path'])), item['data']))

    def decode(data: str, limit: int) -> bytes:
        if len(data) > (limit * 4 // 3) + 8:
            raise PackageError('Un archivo de la carpeta supera el tamaño permitido.')
        try:
            raw = base64.b64decode(data, validate=True)
        except (binascii.Error, ValueError):
            raise PackageError('La carpeta no se envió en el formato esperado.') from None
        if len(raw) > limit:
            raise PackageError('Un archivo de la carpeta supera el tamaño permitido.')
        return raw
    return _bounded(entries, decode)


def _bounded(entries, load: Callable) -> tuple[dict[str, bytes], list[str]]:
    """Strip one wrapping folder (enfoque/app.json), then load only app.json and ui/ files."""
    # System leftovers of macOS zips and hidden folders (.git, .DS_Store) are not part of an app.
    entries = [(name, source) for name, source in entries
               if not any(part.startswith('.') or part == '__MACOSX' for part in name.split('/'))]
    names = [name for name, _ in entries]
    if len({name.casefold() for name in names}) != len(names):
        raise PackageError('El paquete repite un archivo (quizá con otra mayúscula).')
    roots = {name.split('/', 1)[0] for name in names}
    if 'app.json' not in names and len(roots) == 1 and all('/' in name for name in names):
        prefix = next(iter(roots)) + '/'
        entries = [(name[len(prefix):], source) for name, source in entries]
    files: dict[str, bytes] = {}
    ignored: list[str] = []
    total = 0
    component_total = 0
    for name, source in entries:
        top = name.split('/', 1)[0]
        if name == 'app.json':
            files[name] = load(source, MAX_MANIFEST_BYTES)
        elif top in REVIEW_ONLY:
            data = load(source, 256 * 1024)
            component_total += len(data)
            if component_total > 1024 * 1024:
                raise PackageError('Python y la skill superan 1 MiB.')
            files[name] = data
        elif name.startswith('ui/'):
            data = load(source, MAX_UI_FILE_BYTES)
            total += len(data)
            if total > MAX_UI_BYTES:
                raise PackageError(f'La interfaz supera {MAX_UI_BYTES // 1024 // 1024} MB en total.')
            files[name] = data
        else:
            ignored.append(name)
    if any(name.startswith(('backend/', 'skill/')) for name in files):
        try:
            manifest = json.loads(files.get('app.json', b'{}'))
        except (ValueError, UnicodeError):
            manifest = {}
        if not isinstance(manifest, dict) or manifest.get('sdkVersion') != 2 or not manifest.get('extension'):
            raise PackageError('Esta app trae un servicio de Python o una skill de Hermes sin el contrato '
                               'extension del SDK 2. Actualiza la ficha; no se activan servicios revisados desde la Store.', 'needs_review')
    return files, sorted(ignored)


# ---------------------------------------------------------------- checking a package

SIGNALS = (
    ('network', 'Se conecta a internet o a otros servicios',
     re.compile(r'\bfetch\s*\(|XMLHttpRequest|\bWebSocket\s*\(|\bEventSource\s*\(|sendBeacon|\bimport\s*\(\s*[\'"`]https?:|from\s+[\'"]https?:')),
    ('iris', 'Llama a funciones internas de Iris (correo, chat, ajustes…)',
     re.compile(r'[\'"`]/api/|[\'"`]/ws\b|127\.0\.0\.1|localhost:|new\s+WebSocket')),
    ('links', 'Contiene direcciones de internet', re.compile(r'https?://(?!www\.w3\.org/)')),
    ('dynamic', 'Ejecuta código creado en el momento',
     re.compile(r'\beval\s*\(|\bnew\s+Function\s*\(|\bFunction\s*\(\s*[\'"`]|set(?:Timeout|Interval)\s*\(\s*[\'"`]|\bimport\s*\(\s*(?![\'"`]\.{1,2}/)')),
    ('html', 'Inserta HTML sin filtrar',
     re.compile(r'\.innerHTML\s*[+]?=|\.outerHTML\s*=|insertAdjacentHTML|document\.write')),
    ('storage', 'Guarda datos en el navegador', re.compile(r'localStorage|sessionStorage|indexedDB|document\.cookie')),
    ('navigation', 'Abre ventanas o páginas', re.compile(r'window\.open\s*\(|\blocation\s*=|location\.(?:href\s*=|assign|replace)')),
)
LONG_LINE = 1500
CSS_REMOTE = re.compile(r'(?i)url\(\s*\\?[\'"]?\s*(?:https?:)?//|@import\s')
BLOB = re.compile(r'[A-Za-z0-9+/=]{400,}|(?:\\x[0-9a-fA-F]{2}){40,}')


def scan_risks(files: dict[str, bytes], *, css_modules: set[str] | frozenset[str] = frozenset()) -> list[dict]:
    """What the code appears to do, by plain text search. A hint for the person, not a verdict."""
    found: dict[str, set] = {}
    for path, data in files.items():
        if path.startswith('__extension__/backend/'):
            found.setdefault('python', set()).add(path.removeprefix('__extension__/'))
        if path.startswith('__extension__/skill/'):
            found.setdefault('skill', set()).add(path.removeprefix('__extension__/'))
        if path in css_modules:
            # Only CSS converted by Core gets this treatment; a supplied .css.js is executable JS.
            text = data.decode('utf-8', 'replace')
            if CSS_REMOTE.search(text):
                found.setdefault('network', set()).add(path)
            if re.search(r'https?://(?!www\.w3\.org/)', text):
                found.setdefault('links', set()).add(path)
            continue
        if not path.endswith(('.js', '.svg')):
            continue
        text = data.decode('utf-8', 'replace')
        for code, _, pattern in SIGNALS:
            if pattern.search(text):
                found.setdefault(code, set()).add(path)
        if path.endswith('.js') and (any(len(line) > LONG_LINE for line in text.splitlines()) or BLOB.search(text)):
            found.setdefault('obfuscated', set()).add(path)
    labels = {code: label for code, label, _ in SIGNALS} | {'obfuscated': 'Tiene código comprimido o difícil de revisar',
        'python': 'Ejecuta código Python local con tus permisos', 'skill': 'Añade instrucciones de uso para Hermes'}
    order = ['python', 'skill'] + [code for code, _, _ in SIGNALS] + ['obfuscated']
    return [{'code': code, 'label': labels[code], 'files': sorted(found[code])} for code in order if code in found]


def check_package(files: dict[str, bytes], ignored=(), *, bundled_ids, icon_names) -> dict:
    """Validate like the build does; return the manifest, the files to serve and a summary."""
    ignored = list(ignored)
    if 'app.json' not in files:
        raise PackageError('Falta app.json: elige la carpeta de la app o un .zip que la contenga.')
    try:
        raw = json.loads(files['app.json'].decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PackageError(f'app.json no es un JSON válido ({exc.__class__.__name__}).') from None
    result = validate_app_manifest(raw, icon_names=icon_names)
    if not result['ok']:
        raise PackageError('app.json no es válido: ' + '; '.join(result['errors'][:6]))
    manifest = result['manifest']
    app_id = manifest['id']
    if app_id in bundled_ids or app_id in RESERVED_IDS:
        raise PackageError(f'El id «{app_id}» pertenece a una app incluida con Iris. Cambia el id de la app.', 'reserved_id')
    if 'entry' not in manifest['ui']:
        raise PackageError('Una app de la Store trae su interfaz en ui/ («ui.entry»), no un módulo de Iris.')
    if manifest['presentation'] != 'window':
        raise PackageError('Una app de la Store se abre en ventana («presentation»: «window»).')
    if manifest['requestedCapabilities'] or manifest['services']:
        raise PackageError('Esta versión de la Store instala apps sin permisos ni servicios.', 'needs_review')

    styles = {manifest['ui'].get('style')} | {widget.get('style') for widget in manifest.get('widgets', [])}
    try:
        outputs: dict[str, bytes] = component_files(files, manifest)
    except ValueError as exc:
        raise PackageError(str(exc)) from None
    css_modules: set[str] = set()
    for path, data in sorted(files.items()):
        if path == 'app.json' or path.startswith(('backend/', 'skill/')):
            continue
        rel = path[len('ui/'):]
        segments = rel.split('/')
        extension = rel.rsplit('.', 1)[-1] if '.' in rel else ''
        if len(segments) > 3 or not all(UI_SEGMENT.fullmatch(segment) for segment in segments):
            raise PackageError(f'ui/{rel}: nombres en minúsculas, sin espacios, hasta dos subcarpetas.')
        if extension not in UI_EXTENSIONS:
            raise PackageError(f'ui/{rel}: solo se instalan archivos .js, .css, .svg, .png, .webp y .json.')
        if extension in ('js', 'css', 'svg', 'json'):
            try:
                text = data.decode('utf-8')
            except UnicodeDecodeError:
                raise PackageError(f'ui/{rel}: no es texto UTF-8.') from None
        if extension == 'css':
            if rel not in styles:
                raise PackageError(f'ui/{rel}: una app tiene una sola hoja de estilos, la de ui.style (y las de sus widgets).')
            # As the build: the stylesheet becomes a module that exports its text.
            key, data = f'{rel}.js', f'export default {json.dumps(text)};\n'.encode()
            css_modules.add(key)
        else:
            key = rel
        if key in outputs:
            raise PackageError(f'ui/{rel}: se publicaría como {key}, igual que otro archivo.')
        outputs[key] = data

    def module(name: str, pattern, what: str) -> None:
        source = outputs.get(name)
        if source is None or not pattern.search(source.decode('utf-8')):
            raise PackageError(f'ui/{name}: falta el módulo o no exporta {what}.')
    module(manifest['ui']['entry'], CREATE_APP, 'createApp()')
    if manifest['ui'].get('style') and f"{manifest['ui']['style']}.js" not in outputs:
        raise PackageError(f"ui/{manifest['ui']['style']}: falta la hoja de estilos (ui.style).")
    for widget in manifest.get('widgets', []):
        module(widget['entry'], CREATE_WIDGET, 'createWidget()')
        if widget.get('style') and f"{widget['style']}.js" not in outputs:
            raise PackageError(f"ui/{widget['style']}: falta el estilo del widget.")

    digest = hashlib.sha256(json.dumps([manifest, sorted((path, hashlib.sha256(data).hexdigest())
                                                         for path, data in outputs.items())]).encode()).hexdigest()
    return {'manifest': manifest, 'files': outputs, 'digest': digest, 'ignored': ignored,
            'size': sum(len(data) for data in outputs.values()), 'risks': scan_risks(outputs, css_modules=css_modules)}


# ---------------------------------------------------------------- the store

PACKAGE_SPEC = StoreSpec(
    folder=('apps', 'app-packages'), filename='packages.sqlite3', lock_name='packages.lock',
    noun='almacén de apps instaladas', schema=(
        'CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)',
        'CREATE TABLE IF NOT EXISTS packages (app_id TEXT PRIMARY KEY, installation TEXT NOT NULL UNIQUE, '
        'version TEXT NOT NULL, manifest TEXT NOT NULL, digest TEXT NOT NULL, size INTEGER NOT NULL, '
        'installed_at TEXT NOT NULL, source TEXT NOT NULL)',
        'CREATE TABLE IF NOT EXISTS files (installation TEXT NOT NULL, path TEXT NOT NULL, sha256 TEXT NOT NULL, '
        'content BLOB NOT NULL, PRIMARY KEY (installation, path))',
        'CREATE TABLE IF NOT EXISTS extension_changes (app_id TEXT PRIMARY KEY, payload TEXT NOT NULL)'),
    schema_version=2, store_name='iris.app-packages/v1', path_error=PackageStoreError,
    version_error=PackageStoreError, busy_error=PackageStoreError)


class AppPackageStore(ProfileSqliteStore):
    SPEC = PACKAGE_SPEC

    @staticmethod
    def _row(row) -> dict:
        return {'id': row[0], 'installation': row[1], 'version': row[2], 'manifest': json.loads(row[3]),
                'digest': row[4], 'size': row[5], 'installed_at': row[6], 'source': row[7]}

    def list(self) -> list[dict]:
        with self._read() as conn:
            return [self._row(row) for row in conn.execute(
                'SELECT app_id,installation,version,manifest,digest,size,installed_at,source FROM packages ORDER BY app_id')]

    def get(self, app_id: str):
        with self._read() as conn:
            row = conn.execute('SELECT app_id,installation,version,manifest,digest,size,installed_at,source '
                               'FROM packages WHERE app_id=?', (app_id,)).fetchone()
            return self._row(row) if row else None

    def file(self, installation: str, path: str):
        with self._read() as conn:
            row = conn.execute('SELECT f.sha256, f.content FROM files f JOIN packages p ON p.installation=f.installation '
                               'WHERE f.installation=? AND f.path=?', (installation, path)).fetchone()
        if not row:
            return None
        data = bytes(row[1])
        if hashlib.sha256(data).hexdigest() != row[0]:
            raise PackageStoreError('Un archivo de la app instalada cambió.')
        return data

    def install(self, package: dict, *, expected: str | None, installation: str | None = None, skill_home: str | None = None) -> dict:
        """One transaction: the new version replaces the old one entirely, or nothing changes."""
        manifest = package['manifest']
        installation = installation or 'ins_' + secrets.token_hex(16)
        if not INSTALLATION.fullmatch(installation):
            raise PackageError('Identidad de instalación no válida.')
        with self._transaction() as conn:
            row = conn.execute('SELECT installation FROM packages WHERE app_id=?', (manifest['id'],)).fetchone()
            if (row[0] if row else None) != expected:
                raise PackageError('La app cambió mientras la revisabas. Vuelve a elegir el archivo.', 'changed')
            if not row and conn.execute('SELECT count(*) FROM packages').fetchone()[0] >= MAX_PACKAGES:
                raise PackageError(f'Ya hay {MAX_PACKAGES} apps instaladas. Desinstala alguna antes.')
            if row:
                conn.execute('DELETE FROM files WHERE installation=?', (row[0],))
                conn.execute('DELETE FROM packages WHERE app_id=?', (manifest['id'],))
            conn.execute('INSERT INTO packages VALUES (?,?,?,?,?,?,?,?)', (
                manifest['id'], installation, manifest['version'], json.dumps(manifest, ensure_ascii=False),
                package['digest'], package['size'], datetime.now(timezone.utc).isoformat(timespec='seconds'), 'archivo'))
            conn.executemany('INSERT INTO files VALUES (?,?,?,?)', [
                (installation, path, hashlib.sha256(data).hexdigest(), data) for path, data in package['files'].items()])
            conn.execute('DELETE FROM meta WHERE key=?', ('skill-home:' + manifest['id'],))
            if skill_home is not None:
                conn.execute('INSERT INTO meta VALUES (?,?)', ('skill-home:' + manifest['id'], skill_home))
        return self.get(manifest['id'])

    def uninstall(self, app_id: str):
        with self._transaction() as conn:
            row = conn.execute('SELECT installation, version FROM packages WHERE app_id=?', (app_id,)).fetchone()
            if not row:
                return None
            conn.execute('DELETE FROM files WHERE installation=?', (row[0],))
            conn.execute('DELETE FROM packages WHERE app_id=?', (app_id,))
            conn.execute('DELETE FROM meta WHERE key=?', ('skill-home:' + app_id,))
            return {'id': app_id, 'version': row[1]}

    def components(self, row: dict) -> dict[str, bytes]:
        with self._read() as conn:
            entries = conn.execute('SELECT path,sha256,content FROM files WHERE installation=? AND path LIKE ?',
                                   (row['installation'], '__extension__/%')).fetchall()
        files = {}
        for name, digest, raw in entries:
            if not name.startswith('__extension__/'):
                continue
            data = bytes(raw)
            if hashlib.sha256(data).hexdigest() != digest:
                raise PackageStoreError('Un componente de la app instalada cambió.')
            files[name] = data
        return files

    def skill_home(self, app_id: str):
        with self._read() as conn:
            row = conn.execute('SELECT value FROM meta WHERE key=?', ('skill-home:' + app_id,)).fetchone()
        return row[0] if row else None

    def changes(self):
        with self._read() as conn:
            return [json.loads(row[0]) for row in conn.execute('SELECT payload FROM extension_changes')]

    def journal(self, app_id: str, payload: dict | None):
        with self._transaction() as conn:
            if payload is None:
                conn.execute('DELETE FROM extension_changes WHERE app_id=?', (app_id,))
            else:
                conn.execute('INSERT INTO extension_changes VALUES (?,?)', (app_id, json.dumps(payload)))


def file_url(installation: str, path: str) -> str:
    return f'/api/app-packages/files/{installation}/{path}'


def public_entry(row: dict) -> dict:
    """What Face needs to register an installed app: its checked manifest and module URLs."""
    manifest, installation = row['manifest'], row['installation']
    ui = manifest['ui']
    return {'id': row['id'], 'version': row['version'], 'installed_at': row['installed_at'], 'size': row['size'],
            'manifest': manifest, 'entry': file_url(installation, ui['entry']),
            **({'style': file_url(installation, ui['style'] + '.js')} if ui.get('style') else {}),
            'widgets': {widget['id']: {'entry': file_url(installation, widget['entry']),
                                       **({'style': file_url(installation, widget['style'] + '.js')} if widget.get('style') else {})}
                        for widget in manifest.get('widgets', [])}}


def open_store(home: Path, *, create: bool = False):
    root = Path(home)
    try:
        path = profile_store_path(PACKAGE_SPEC, root)
    except ProfilePathError as exc:
        raise PackageStoreError('La carpeta de apps instaladas no es una carpeta normal del perfil.') from exc
    return AppPackageStore(path, root=root) if create or path.exists() else None


def installed_catalog(home: Path) -> tuple[dict, set]:
    """Widgets and app IDs of installed apps, for the desktop's own checks (desktop_widgets)."""
    store = open_store(home)
    if store is None:
        return {}, set()
    rows = store.list()
    widgets = {widget['id']: {'name': widget['name'], 'app': row['id'], 'size': widget['size'],
                              'configVersion': widget['configVersion']}
               for row in rows for widget in row['manifest'].get('widgets', [])}
    return widgets, {row['id'] for row in rows}


# ---------------------------------------------------------------- HTTP

class _Staging:
    """Checked packages waiting for the person's yes; memory only, a few minutes, a few at once."""
    TTL = 15 * 60

    def __init__(self, clock=time.monotonic):
        self.clock, self.items, self.lock = clock, {}, threading.Lock()

    def put(self, package: dict) -> str:
        token = secrets.token_urlsafe(32)
        with self.lock:
            now = self.clock()
            self.items = {key: value for key, value in self.items.items() if value[0] > now}
            while len(self.items) >= 4:
                self.items.pop(next(iter(self.items)))
            self.items[token] = (now + self.TTL, package)
        return token

    def take(self, token: str):
        with self.lock:
            entry = self.items.pop(token, None)
        return entry[1] if entry and entry[0] > self.clock() else None


def create_app_packages_router(home: Callable[[], Path], *, bundled_ids, icon_names,
                               forget_app: Callable[[Path, str], bool] | None = None, clock=time.monotonic,
                               extensions=None):
    if extensions is None:
        from core.app_extensions import AppExtensions
        extensions = AppExtensions(home)
    staging = _Staging(clock)

    def local(request: Request):
        if not request.client or not is_loopback_host(request.client.host):
            raise HTTPException(403, 'Las apps solo se instalan desde este equipo.')
        origin = request.headers.get('origin')
        if request.method != 'GET' and (not origin or not is_trusted_local_origin(
                origin, request.url.hostname, request_scheme=request.url.scheme, request_port=request.url.port)):
            raise HTTPException(403, 'Abre Iris Store en la ventana local de Iris.')

    router = APIRouter(prefix='/api/app-packages', dependencies=[Depends(local)], route_class=_PrivateRoute)

    def refuse(exc: PackageError, status: int = 422):
        return JSONResponse({'error': {'code': exc.code, 'message': str(exc)}}, status_code=status)

    def unavailable():
        return JSONResponse({'error': {'code': 'store_unavailable',
                                       'message': 'No pude completar la operación del almacén. Vuelve a consultar las apps instaladas antes de reintentar.'}}, status_code=503)

    def summary(package: dict, current: dict | None) -> dict:
        manifest = package['manifest']
        return {'id': manifest['id'], 'name': manifest['name'], 'version': manifest['version'],
                'description': manifest['description'], 'icon': manifest['icon'], 'accent': manifest['accent'],
                'widgets': [{'id': widget['id'], 'name': widget['name']} for widget in manifest.get('widgets', [])],
                'size': package['size'], 'files': len(package['files']), 'ignored': package['ignored'],
                'risks': package['risks'], 'current': {'version': current['version']} if current else None,
                **({'extension': {'python': manifest['extension'].get('python'),
                                   'skill': 'iris-app-' + manifest['id'] if manifest['extension']['skill'] else None,
                                   'operations': [{'action': op['action'], 'label': op['label'], 'effect': op['effect'],
                                                   'audience': op.get('audience', 'all')}
                                                  for op in manifest['extension']['operations']],
                                   'dependencies': 'Biblioteca estándar; no hay descargas ni instalaciones adicionales.'}}
                   if manifest.get('extension') else {})}

    @router.get('')
    async def listing():
        try:
            await extensions.reconcile()
            store = open_store(home())
            return {'apps': [public_entry(row) for row in store.list()] if store else []}
        except (PackageStoreError, OSError, sqlite3.Error, ValueError):
            return unavailable()

    @router.post('/inspect')
    async def inspect(request: Request):
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > MAX_REQUEST_BYTES:
                return refuse(PackageError(f'El paquete supera {MAX_REQUEST_BYTES // 1024 // 1024} MB.'), 413)
            body.extend(chunk)
        kind = request.headers.get('content-type', '').split(';')[0].strip().lower()
        try:
            if kind == 'application/json':
                try:
                    payload = json.loads(bytes(body).decode('utf-8'))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    raise PackageError('La carpeta no se envió en el formato esperado.') from None
                files, ignored = read_folder(payload)
            elif kind in ('application/zip', 'application/x-zip-compressed', 'application/octet-stream'):
                files, ignored = read_zip(bytes(body))
            else:
                raise PackageError('Elige un archivo .zip o la carpeta de la app.')
            package = check_package(files, bundled_ids=bundled_ids, icon_names=icon_names, ignored=ignored)
            store = open_store(home())
            current = store.get(package['manifest']['id']) if store else None
        except PackageError as exc:
            return refuse(exc)
        except (PackageStoreError, OSError, sqlite3.Error):
            return unavailable()
        package['expected'] = current['installation'] if current else None
        package['profile'] = str(Path(home()).resolve())
        return {'token': staging.put(package), 'app': summary(package, current)}

    @router.post('/install')
    async def install(request: Request):
        try:
            body = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            body = None
        token = body.get('token') if isinstance(body, dict) else None
        if not isinstance(token, str) or not TOKEN.fullmatch(token):
            return refuse(PackageError('Vuelve a elegir el archivo de la app.', 'expired'))
        package = staging.take(token)
        if package is None:
            return refuse(PackageError('La revisión caducó. Vuelve a elegir el archivo de la app.', 'expired'), 409)
        if package['profile'] != str(Path(home()).resolve()):
            return refuse(PackageError('El perfil cambió. Vuelve a revisar la app en el perfil actual.', 'changed'), 409)
        try:
            row = await extensions.install(package, expected=package['expected'])
        except PackageError as exc:
            return refuse(exc, 409)
        except (PackageStoreError, OSError, sqlite3.Error):
            return unavailable()
        return {'installed': public_entry(row)}

    @router.delete('/{app_id}')
    async def uninstall(app_id: str, request: Request, keep_data: bool = True):
        if not re.fullmatch(r'[a-z][a-z0-9-]{1,31}', app_id):
            raise HTTPException(404, 'Esa app no está instalada desde la Store.')
        try:
            store = open_store(home())
            expected = request.headers.get('x-iris-app-installation')
            if expected is not None and not INSTALLATION.fullmatch(expected):
                return refuse(PackageError('Identidad de instalación no válida.'))
            removed = await extensions.uninstall(app_id, keep_data=keep_data, expected=expected) if store else None
        except PackageError as exc:
            return refuse(exc, 409)
        except (PackageStoreError, OSError, sqlite3.Error):
            return unavailable()
        if removed is None:
            raise HTTPException(404, 'Esa app no está instalada desde la Store.')
        cleaned = True
        if forget_app is not None:
            try:
                cleaned = forget_app(Path(home()), app_id)
            except Exception:  # the app is gone; a leftover widget stays removable as «no disponible»
                cleaned = False
        warnings = removed.pop('warnings', [])
        return {'removed': removed, 'desktop_cleaned': cleaned, **({'warnings': warnings} if warnings else {})}

    @router.post('/{app_id}/call/{action}')
    async def call(app_id: str, action: str, request: Request):
        # Human UI calls use the same declared operations and validation. Hermes
        # goes through /api/app-actions and its Capability Center confirmations.
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > 32768:
                return refuse(PackageError('Los argumentos superan 32 KiB.'), 413)
            body.extend(chunk)
        try:
            args = json.loads(body)
            installation = request.headers.get('x-iris-app-installation', '')
            if not INSTALLATION.fullmatch(installation):
                raise PackageError('Vuelve a abrir la app para usar sus funciones locales.')
            answer = await extensions.call(app_id, action, args, caller='ui', installation=installation)
            return answer
        except RecursionError:
            return refuse(PackageError('Los argumentos tienen demasiados niveles.'))
        except (ValueError, PackageError) as exc:
            return refuse(PackageError(str(exc)))

    @router.get('/files/{installation}/{path:path}')
    def content(installation: str, path: str):
        segments = path.split('/')
        extension = path.rsplit('.', 1)[-1] if '.' in path else ''
        if (not INSTALLATION.fullmatch(installation) or len(segments) > 3 or extension not in UI_EXTENSIONS
                or not all(UI_SEGMENT.fullmatch(segment) for segment in segments)):
            raise HTTPException(404, 'Archivo no disponible.')
        try:
            store = open_store(home())
            data = store.file(installation, path) if store else None
        except (PackageStoreError, OSError, sqlite3.Error):
            return unavailable()
        if data is None:
            raise HTTPException(404, 'Archivo no disponible.')
        headers = {'X-Content-Type-Options': 'nosniff'}
        if extension == 'svg':
            headers['Content-Security-Policy'] = "default-src 'none'; style-src 'unsafe-inline'; sandbox"
        return Response(data, media_type=UI_EXTENSIONS[extension], headers=headers)

    return router
