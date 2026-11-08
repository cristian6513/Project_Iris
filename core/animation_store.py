"""Recoverable animation uploads: immutable bytes and one manifest commit.

All writers must use this store. Readers use read_animation_manifest without
a lock: it tolerates transient Windows sharing errors and returns one complete
version. Previous clip files are never removed.
This is not a sandbox against processes that can modify the store themselves.
"""

from __future__ import annotations

import errno
import hashlib
import json
import logging
import os
import re
import stat
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from core.asset_uploads import AssetUploadError, read_bounded, validate_asset

logger = logging.getLogger("IRIS.animation_store")
ANIMATION_EXTENSIONS = ("vrma", "bvh", "fbx", "glb", "gltf")
MAX_UPLOAD_BYTES = 64 * 1024 * 1024
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
_MANIFEST_READ_TIMEOUT = 1.0
_WINDOWS = os.name == "nt"
_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {
    f"{prefix}{n}" for prefix in ("COM", "LPT") for n in range(1, 10)
}
MANAGED_FILENAME = re.compile(r"[a-z0-9_]{1,48}\.[a-f0-9]{64}\.(vrma|bvh|fbx|glb|gltf)\Z")


class AnimationStoreError(Exception):
    """A recoverable error before the manifest commit."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _basename(value: str) -> str:
    if (
        not value
        or value in {".", ".."}
        or len(value) > 200
        or value.startswith(".")
        or value.endswith((".", " "))
        or any(ord(c) < 32 or c in '/\\:<>"|?*' for c in value)
        or value.split(".", 1)[0].upper() in _RESERVED
    ):
        raise AnimationStoreError("Nombre de archivo inválido: usa un nombre sin rutas.")
    return value


def normalize_animation_name(value: str) -> str:
    """Keep Core's friendly label normalization without accepting path syntax."""
    if any(c in value for c in "/\\:") or ".." in value:
        raise AnimationStoreError("El nombre de pose no puede contener rutas.")
    name = re.sub(r"[^a-z0-9_]+", "_", value.strip().lower()).strip("_")
    if not re.fullmatch(r"[a-z0-9_]{1,48}", name) or name.upper() in _RESERVED:
        raise AnimationStoreError("Nombre de pose inválido (máximo 48 caracteres).")
    return name


def _json_document(raw: bytes) -> dict:
    def invalid_constant(_value):
        raise ValueError("Non-finite JSON number")

    data = json.loads(raw.decode("utf-8-sig"), parse_constant=invalid_constant)
    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object")
    return data


def _validate_format(content: bytes, extension: str) -> None:
    try:
        validate_asset(content, extension)
    except AssetUploadError as exc:
        raise AnimationStoreError(str(exc), exc.status_code) from exc


def _plain_file(root: Path, name: str) -> Path:
    """Reject aliases, Windows path syntax and non-regular store entries."""
    path = root / _basename(name)
    if path.is_symlink() or path.resolve() != path:
        raise AnimationStoreError("El almacén contiene un enlace no permitido.", 409)
    if path.exists():
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise AnimationStoreError("El almacén contiene un archivo no regular.", 409)
    return path


def read_animation_manifest(path: Path, *, timeout: float | None = None) -> bytes:
    """Read one complete manifest without locking writers.

    Windows can deny opening a path briefly during atomic replacement. The
    CRT's open() can lose winerror and expose only EACCES; retry that case too,
    with a deadline. Missing, unsafe and permanently inaccessible files still
    raise. Reopen for each attempt; never return partial bytes or an empty map.
    Call from a worker when used by an async HTTP handler.
    """
    deadline = time.monotonic() + (
        _MANIFEST_READ_TIMEOUT if timeout is None else max(0.0, timeout)
    )
    while True:
        try:
            candidate = _plain_file(path.parent.absolute(), path.name)
            with candidate.open("rb") as handle:
                raw = handle.read(MAX_MANIFEST_BYTES + 1)
        except PermissionError as exc:
            winerror = getattr(exc, "winerror", None)
            retryable = _WINDOWS and (
                winerror in {5, 32, 33}
                or (winerror is None and exc.errno in {errno.EACCES, errno.EPERM})
            )
            remaining = deadline - time.monotonic()
            if not retryable or remaining <= 0:
                raise
            time.sleep(min(0.01, remaining))
            continue
        if len(raw) > MAX_MANIFEST_BYTES:
            raise ValueError("Manifest too large")
        return raw


@contextmanager
def _store_lock(root: Path, timeout: float):
    # A persistent lock file is essential: unlinking it would create two locks
    # for the same store while another process still holds the old inode.
    path = root / ".animation-store.lock"
    if path.is_symlink() or path.resolve() != path:
        raise AnimationStoreError("Lock de animaciones no válido.", 409)
    if path.exists() and (not path.is_file() or path.stat().st_nlink != 1):
        raise AnimationStoreError("Lock de animaciones no válido.", 409)
    with path.open("a+b") as handle:
        if path.stat().st_size == 0:
            handle.write(b"0")
            handle.flush()
        deadline = time.monotonic() + timeout
        while True:
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                    raise
                if time.monotonic() >= deadline:
                    raise AnimationStoreError(
                        "Otra subida está en curso. Intenta de nuevo en unos segundos.",
                        409,
                    ) from exc
                time.sleep(0.025)
        try:
            yield
        finally:
            try:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                # Closing the handle releases the OS lock. A release error
                # must not report a successfully committed upload as failed.
                logger.exception("Animation lock release failed")


def _discard(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.warning("Retaining animation temporary file for recovery: %s", path.name)


def _cleanup_staging(root: Path, referenced: set[str]) -> None:
    """Under the store lock, reclaim only abandoned staging files after a day.

    Published versions (including orphans) remain available for recovery and
    clients holding an older URL. Factory assets are never cleanup candidates.
    """
    cutoff = time.time() - 86400
    try:
        for path in root.glob(".animation-upload-*.tmp"):
            if path.name in referenced or path.is_symlink() or path.resolve() != path:
                continue
            info = path.stat()
            if stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_mtime < cutoff:
                _discard(path)
    except OSError:
        logger.warning("Animation staging cleanup deferred", exc_info=True)


def _write_temp(root: Path, content: bytes) -> Path:
    fd, name = tempfile.mkstemp(prefix=".animation-upload-", suffix=".tmp", dir=root)
    path = Path(name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        _discard(path)
        raise
    return path


def _replace(source: Path, destination: Path) -> None:
    # Windows readers can briefly deny delete-sharing. Retry only that case,
    # under the store lock, keeping the previous manifest intact on failure.
    deadline = time.monotonic() + 1.0
    while True:
        try:
            os.replace(source, destination)
            return
        except PermissionError as exc:
            if os.name != "nt" or getattr(exc, "winerror", None) not in {5, 32, 33}:
                raise
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.01)


def _sync_directory(root: Path) -> None:
    if os.name != "nt":
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


@dataclass(frozen=True)
class AnimationCommit:
    name: str
    filename: str
    format: str
    size: int
    revision: int
    changed: bool
    path: Path
    warning: str | None = None


class AnimationStore:
    def __init__(self, root: Path, *, lock_timeout: float = 5.0, max_bytes: int | None = None):
        self.root = root.absolute()
        self.lock_timeout = lock_timeout
        self.max_bytes = MAX_UPLOAD_BYTES if max_bytes is None else max_bytes

    def _manifest(self) -> dict:
        try:
            raw = read_animation_manifest(self.root / "manifest.json")
        except FileNotFoundError:
            return {"clips": {}}
        except ValueError as exc:
            raise AnimationStoreError("El manifest supera el límite permitido.", 409) from exc
        try:
            data = _json_document(raw)
            if type(data.get("schema_version", 1)) is not int or data.get("schema_version", 1) != 1:
                raise ValueError("Unsupported manifest schema")
            if type(data.get("revision", 0)) is not int or data.get("revision", 0) < 0:
                raise ValueError("Invalid manifest revision")
            clips = data.get("clips")
            if not isinstance(clips, dict):
                raise ValueError("Invalid clips mapping")
            for name, filename in clips.items():
                if not isinstance(name, str) or not name or not isinstance(filename, str):
                    raise ValueError("Invalid clip entry")
                target = _plain_file(self.root, filename)
                if not target.is_file() or target.stat().st_size == 0:
                    raise ValueError("Missing or empty registered clip")
            return data
        except (ValueError, TypeError, AnimationStoreError) as exc:
            raise AnimationStoreError(
                "El manifest no es válido o tiene referencias rotas. Se conservó sin cambios.",
                409,
            ) from exc

    def save_upload(self, source: BinaryIO, filename: str, name: str) -> AnimationCommit:
        """Run in a worker: bounded read, validate, lock, stage, then commit."""
        if normalize_animation_name(name) != name:
            raise AnimationStoreError("Identificador de pose inválido.")
        _basename(filename)
        extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if extension not in ANIMATION_EXTENSIONS:
            raise AnimationStoreError("Formato de animación no soportado.")
        try:
            try:
                payload = read_bounded(source, self.max_bytes)
            except AssetUploadError as exc:
                raise AnimationStoreError(str(exc), exc.status_code) from exc
            _validate_format(payload, extension)
            if self.root.resolve() != self.root:
                raise AnimationStoreError("La raíz de animaciones no puede ser un enlace.", 409)
            self.root.mkdir(parents=True, exist_ok=True)
            with _store_lock(self.root, self.lock_timeout):
                return self._commit(payload, extension, name)
        except OSError as exc:
            logger.warning("Animation upload failed before commit: %s", exc)
            raise AnimationStoreError(
                "No se pudo guardar la animación. La versión anterior se conserva.",
                503,
            ) from exc

    def _commit(self, content: bytes, extension: str, name: str) -> AnimationCommit:
        manifest = self._manifest()  # Always re-read AFTER acquiring the lock.
        _cleanup_staging(self.root, set(manifest["clips"].values()))
        digest = hashlib.sha256(content).hexdigest()
        filename = f"{name}.{digest}.{extension}"
        destination = _plain_file(self.root, filename)
        manifest_path = _plain_file(self.root, "manifest.json")
        changed = manifest["clips"].get(name) != filename
        revision = manifest.get("revision", 0) + int(changed)
        if destination.exists():
            with destination.open("rb") as handle:
                existing = handle.read(self.max_bytes + 1)
            if existing != content:
                raise AnimationStoreError("La versión almacenada tiene contenido inesperado.", 409)
        else:
            staged = _write_temp(self.root, content)
            try:
                _replace(staged, destination)
                _sync_directory(self.root)
            finally:
                _discard(staged)
        result = AnimationCommit(
            name, filename, extension, len(content), revision, changed, destination
        )
        if not changed:
            return result  # Idempotent retry: same bytes, mapping and revision.
        manifest["clips"][name] = filename
        manifest["schema_version"] = 1
        manifest["revision"] = revision
        raw = (json.dumps(manifest, indent=4, ensure_ascii=False, allow_nan=False) + "\n").encode()
        if len(raw) > MAX_MANIFEST_BYTES:
            raise AnimationStoreError("El manifest supera el límite permitido.", 409)
        staged_manifest = _write_temp(self.root, raw)
        try:
            _replace(staged_manifest, manifest_path)  # The sole commit point.
        finally:
            _discard(staged_manifest)
        try:
            _sync_directory(self.root)
        except OSError:
            logger.exception("Animation committed, but directory sync failed")
            return AnimationCommit(
                name,
                filename,
                extension,
                len(content),
                revision,
                changed,
                destination,
                "Animación guardada; no se pudo confirmar la sincronización del disco.",
            )
        return result
