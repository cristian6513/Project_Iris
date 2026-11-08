"""Local font resources. Binary content belongs to the profile, never to a caller's path."""
from __future__ import annotations

import hashlib
import re
import sqlite3
import struct
from pathlib import Path
from typing import Callable
from urllib.parse import unquote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response, JSONResponse

from core.calendar_api import _PrivateRoute
from core.request_security import is_loopback_host, is_trusted_local_origin
from core.profile_sqlite import ProfileSqliteStore, StoreSpec, profile_store_path
from core.profile_paths import ProfilePathError

MAX_FONT_BYTES = 2_000_000
MAX_FONTS = 8
FONT_ID = re.compile(r'^font_[a-f0-9]{64}$')


class FontStoreError(RuntimeError):
    pass


def font_label(value: str) -> str:
    label = value.strip()
    if not 1 <= len(label) <= 60 or any(ord(char) < 32 or ord(char) == 127 for char in label):
        raise ValueError('El nombre de la fuente debe tener entre 1 y 60 caracteres.')
    return label


FONT_SPEC = StoreSpec(folder=('apps', 'theme-fonts'), filename='fonts.sqlite3', lock_name='fonts.lock',
    noun='almacén de tipografías', schema=(
        'CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)',
        'CREATE TABLE IF NOT EXISTS fonts (id TEXT PRIMARY KEY, label TEXT NOT NULL, data BLOB NOT NULL)'),
    schema_version=1, store_name='iris.theme-fonts/v1', path_error=FontStoreError,
    version_error=FontStoreError, busy_error=FontStoreError)


def validate_font(data: bytes) -> None:
    # WOFF2 only in this first version. The browser's font sanitizer must decode a
    # preview before upload; Core independently bounds the compressed/decompressed header.
    if not 48 <= len(data) <= MAX_FONT_BYTES or data[:4] != b'wOF2':
        raise ValueError('Selecciona una fuente WOFF2 de hasta 2 MB.')
    flavor, length, tables, reserved, sfnt, compressed = struct.unpack('>IIHHII', data[4:24])
    if flavor not in (0x00010000, 0x4F54544F) or length != len(data) or not 1 <= tables <= 256 or reserved:
        raise ValueError('La cabecera de la fuente WOFF2 no es válida.')
    if not 12 <= sfnt <= 16_000_000 or not 1 <= compressed <= len(data) - 48:
        raise ValueError('La fuente supera los límites de esta versión.')
    for offset, length in ((int.from_bytes(data[28:32], 'big'), int.from_bytes(data[32:36], 'big')),
                           (int.from_bytes(data[40:44], 'big'), int.from_bytes(data[44:48], 'big'))):
        if (offset == 0) != (length == 0) or offset and (offset < 48 or offset + length > len(data)):
            raise ValueError('Los bloques de la fuente WOFF2 no son válidos.')


class FontStore(ProfileSqliteStore):
    SPEC = FONT_SPEC

    def list(self):
        with self._read() as conn:
            return [{'id': row[0], 'label': row[1], 'bytes': row[2]} for row in conn.execute('SELECT id,label,length(data) FROM fonts ORDER BY label,id')]

    def add(self, data: bytes, label: str):
        validate_font(data)
        label = font_label(label)
        key = 'font_' + hashlib.sha256(data).hexdigest()
        with self._transaction() as conn:
            row = conn.execute('SELECT label FROM fonts WHERE id=?', (key,)).fetchone()
            if row:
                return {'id': key, 'label': row[0], 'bytes': len(data)}
            if conn.execute('SELECT count(*) FROM fonts').fetchone()[0] >= MAX_FONTS:
                raise ValueError('Ya hay 8 fuentes guardadas. Quita una antes de añadir otra.')
            conn.execute('INSERT INTO fonts VALUES (?,?,?)', (key, label, data))
        return {'id': key, 'label': label, 'bytes': len(data)}

    def get(self, key):
        with self._read() as conn:
            row = conn.execute('SELECT data FROM fonts WHERE id=?', (key,)).fetchone()
            if not row:
                return None
            data = bytes(row[0]); validate_font(data)
            if 'font_' + hashlib.sha256(data).hexdigest() != key:
                raise FontStoreError('La fuente guardada cambió.')
            return data

    def remove(self, key):
        with self._transaction() as conn:
            conn.execute('DELETE FROM fonts WHERE id=?', (key,))


def create_theme_fonts_router(home: Callable[[], Path]):
    def local(request: Request):
        if not request.client or not is_loopback_host(request.client.host):
            raise HTTPException(403, 'Las fuentes solo se administran desde este equipo.')
        origin = request.headers.get('origin')
        if request.method != 'GET' and origin and not is_trusted_local_origin(origin, request.url.hostname,
                request_scheme=request.url.scheme, request_port=request.url.port):
            raise HTTPException(403, 'Abre Theme Studio en la ventana local de Iris.')
    router = APIRouter(prefix='/api/os/fonts', dependencies=[Depends(local)], route_class=_PrivateRoute)

    def store(create=False):
        root = Path(home())
        try:
            path = profile_store_path(FONT_SPEC, root)
        except ProfilePathError as exc:
            raise FontStoreError('La carpeta de fuentes no es una carpeta normal del perfil.') from exc
        return FontStore(path, root=root) if create or path.exists() else None

    def checked_key(key):
        if not FONT_ID.fullmatch(key):
            raise HTTPException(404, 'Fuente no disponible.')

    def failure(exc):
        return JSONResponse({'error': {'code': 'font_unavailable', 'message': 'No pude abrir el almacén de fuentes. No se cambiaron tus archivos.'}}, status_code=503)

    @router.get('')
    def listing():
        try:
            current = store()
            return {'fonts': current.list() if current else []}
        except (FontStoreError, OSError, sqlite3.Error):
            return failure(None)

    @router.post('')
    async def upload(request: Request):
        try:
            label = font_label(unquote(request.headers.get('x-iris-font-name', 'Fuente propia'), errors='strict'))
            data = bytearray()
            async for chunk in request.stream():
                if len(data) + len(chunk) > MAX_FONT_BYTES:
                    raise HTTPException(413, 'Selecciona una fuente WOFF2 de hasta 2 MB.')
                data.extend(chunk)
            data = bytes(data)
            validate_font(data)
            return store(create=True).add(data, label)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        except (FontStoreError, OSError, sqlite3.Error):
            return failure(None)

    @router.get('/{key}')
    def content(key: str):
        checked_key(key)
        try:
            current = store(); data = current.get(key) if current else None
            if data is None:
                raise HTTPException(404, 'La fuente ya no está en este perfil. Elige otra en Theme Studio.')
            return Response(data, media_type='font/woff2', headers={'X-Content-Type-Options': 'nosniff'})
        except (FontStoreError, ValueError, OSError, sqlite3.Error):
            return failure(None)

    @router.delete('/{key}')
    def remove(key: str):
        checked_key(key)
        try:
            current = store()
            if current:
                current.remove(key)
            return {'removed': True}
        except (FontStoreError, OSError, sqlite3.Error):
            return failure(None)

    return router
