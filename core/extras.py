"""Approved optional extras in isolated profile environments (A08.5).

Each admitted extra has an exact PEP 751 lock shipped with Iris. Installation
downloads only the wheels that lock selects for this interpreter, verifies
size and SHA-256, extracts them into a new generation directory that nothing
references yet, proves the declared imports in an isolated interpreter and
then commits by atomically rewriting ``state.json``. Large trees are never
renamed: Windows scanners briefly hold freshly written files. The base
Iris/Hermes runtime is never written: there is
no ``pip`` and no ``--upgrade`` here. Consumers do not import an extra into
Core; they talk to ``core/extras_worker.py`` running with ``-I -S`` and only
the extra's site directory on ``sys.path``.

Layout under ``<IRIS_HOME>/extras``::

    .operation.lock          one install/repair/remove at a time
    .downloads/              verified wheels until their install commits
    <id>/state.json          active generation and its lock identity
    <id>/env-<gen>/site/     isolated packages
    <id>/env-<gen>/receipt.json  size/SHA-256 of every installed file
    <id>/pycache/            bytecode of the worker interpreter
    <id>/models/             model caches; kept on removal unless purged
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import stat
import subprocess
import sys
import sysconfig
import threading
import time
import tomllib
import uuid
import zipfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

import httpx
from packaging.pylock import PackageWheel, Pylock
from packaging.tags import sys_tags

from core.extras_worker import MARKER, PROTOCOL
from core.profile_paths import (
    ProfilePathError,
    _open_plain_file,
    _plain_directory,
    _try_lock,
    _unlock,
    validate_profile_home,
)
from core.runtime_descriptor import get_runtime, runtime_subprocess_env

logger = logging.getLogger("IRIS.extras")

DATA_DIR = Path(__file__).with_name("extras_data")
MANIFEST_PATH = DATA_DIR / "manifest.json"
WORKER_SCRIPT = Path(__file__).with_name("extras_worker.py")
ALLOWED_HOSTS = frozenset({"files.pythonhosted.org"})
SPACE_MARGIN = 256 * 1024 * 1024
# Before downloading, unpacked size is unknown; wheels rarely expand beyond 3x.
# The exact uncompressed size is checked again before extraction.
INSTALL_ESTIMATE_FACTOR = 3
CHUNK = 1024 * 1024
PROBE_TIMEOUT = 300.0
_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,31}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_GENERATION = re.compile(r"[0-9a-f]{12}\Z")
_DEVICE = re.compile(r"(?:CON|PRN|AUX|NUL|CLOCK\$|COM[1-9¹²³]|LPT[1-9¹²³])\Z", re.I | re.ASCII)
_MAX_JSON_BYTES = 256 * 1024
_MAX_RECEIPT_BYTES = 64 * 1024 * 1024
_CREATE_NO_WINDOW = 0x08000000
# Antivirus/indexers briefly open new files without FILE_SHARE_DELETE, which
# makes renames and deletions fail with sharing violations for a few seconds.
_SHARING_RETRY_DELAYS = (0.1, 0.2, 0.4, 0.8, 1.6, 2.0, 2.0)


class ExtrasError(RuntimeError):
    """Expected extra failure with a stable code and a user-facing message."""

    def __init__(self, code: str, message: str, *, status_code: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class ExtraSpec:
    id: str
    label: str
    consumer: str
    requirements: tuple[str, ...]
    lock: str
    lock_sha256: str
    probe: tuple[str, ...]
    download_bytes: int
    packages: int
    models: str
    warning: str


@dataclass(frozen=True)
class ExtrasManifest:
    platform: str
    python: str
    extras: dict[str, ExtraSpec]
    unsupported: dict[str, str]
    data_dir: Path


@dataclass(frozen=True)
class LockedWheel:
    name: str
    version: str
    filename: str
    url: str
    size: int
    sha256: str


@dataclass(frozen=True)
class ExtrasPaths:
    home: Path
    root: Path

    def extra(self, extra_id: str) -> Path:
        return self.root / extra_id


@dataclass(frozen=True)
class ExtraEnvironment:
    """A committed environment a consumer may launch; never Core's sys.path."""

    extra_id: str
    env_dir: Path
    site: Path
    models: Path
    pycache: Path
    home: Path

    def writable_directories(self) -> tuple[Path, ...]:
        """Every directory handed to the worker as a write target."""
        return (self.models, self.pycache, *(self.models / name for name in _CACHE_DIRECTORIES.values()))


# Environment variable -> directory under ``<extra>/models`` used by engines.
_CACHE_DIRECTORIES = {
    "HF_HOME": "huggingface",
    "TTS_HOME": "coqui",
    "XDG_DATA_HOME": "data",
    "XDG_CACHE_HOME": "cache",
    "TORCH_HOME": "torch",
    "NLTK_DATA": "nltk",
}


# ── Manifest and lock ───────────────────────────────────────────────


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate field")
        result[key] = value
    return result


def _strict_object(value: Any, required: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != required:
        raise ExtrasError("manifest_invalid", f"Manifiesto de extras inválido: {label}.", status_code=500)
    return value


def _text(value: Any, label: str, *, empty: bool = False, maximum: int = 600) -> str:
    if not isinstance(value, str) or (not value and not empty) or len(value) > maximum:
        raise ExtrasError("manifest_invalid", f"Manifiesto de extras inválido: {label}.", status_code=500)
    return value


def _count(value: Any, label: str) -> int:
    if type(value) is not int or value < 0:
        raise ExtrasError("manifest_invalid", f"Manifiesto de extras inválido: {label}.", status_code=500)
    return value


def parse_manifest(document: Any, data_dir: Path) -> ExtrasManifest:
    root = _strict_object(
        document, {"schema_version", "platform", "python", "resolution", "extras", "unsupported"}, "raíz",
    )
    if type(root["schema_version"]) is not int or root["schema_version"] != 1:
        raise ExtrasError("manifest_invalid", "Versión de manifiesto de extras no admitida.", status_code=500)
    resolution = _strict_object(
        root["resolution"], {"index_url", "exclude_newer", "python_platform"}, "resolution",
    )
    for key, value in resolution.items():
        _text(value, f"resolution.{key}", maximum=200)
    extras: dict[str, ExtraSpec] = {}
    if not isinstance(root["extras"], dict) or not isinstance(root["unsupported"], dict):
        raise ExtrasError("manifest_invalid", "Manifiesto de extras inválido.", status_code=500)
    for extra_id, raw in root["extras"].items():
        if not isinstance(extra_id, str) or not _ID.fullmatch(extra_id):
            raise ExtrasError("manifest_invalid", "Identificador de extra inválido.", status_code=500)
        item = _strict_object(raw, {
            "label", "consumer", "requirements", "lock", "lock_sha256", "probe",
            "download_bytes", "packages", "models", "warning",
        }, extra_id)
        lock = _text(item["lock"], f"{extra_id}.lock", maximum=80)
        if lock != f"pylock.{extra_id}.toml":  # PEP 751 file name, one per extra
            raise ExtrasError("manifest_invalid", f"Lock de extra inválido: {extra_id}.", status_code=500)
        if not isinstance(item["lock_sha256"], str) or not _SHA256.fullmatch(item["lock_sha256"]):
            raise ExtrasError("manifest_invalid", f"Hash de lock inválido: {extra_id}.", status_code=500)
        for key in ("requirements", "probe"):
            if not isinstance(item[key], list) or not item[key]:
                raise ExtrasError("manifest_invalid", f"Lista inválida: {extra_id}.{key}.", status_code=500)
        probe = tuple(_text(name, f"{extra_id}.probe", maximum=120) for name in item["probe"])
        if any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*", name) for name in probe):
            raise ExtrasError("manifest_invalid", f"Módulo de prueba inválido: {extra_id}.", status_code=500)
        extras[extra_id] = ExtraSpec(
            id=extra_id,
            label=_text(item["label"], f"{extra_id}.label", maximum=120),
            consumer=_text(item["consumer"], f"{extra_id}.consumer", maximum=60),
            requirements=tuple(_text(req, f"{extra_id}.requirements", maximum=200) for req in item["requirements"]),
            lock=lock,
            lock_sha256=item["lock_sha256"],
            probe=probe,
            download_bytes=_count(item["download_bytes"], f"{extra_id}.download_bytes"),
            packages=_count(item["packages"], f"{extra_id}.packages"),
            models=_text(item["models"], f"{extra_id}.models", empty=True),
            warning=_text(item["warning"], f"{extra_id}.warning", empty=True),
        )
    unsupported = {}
    for extra_id, reason in root["unsupported"].items():
        if not isinstance(extra_id, str) or not _ID.fullmatch(extra_id) or extra_id in extras:
            raise ExtrasError("manifest_invalid", "Extra no admitido mal declarado.", status_code=500)
        unsupported[extra_id] = _text(reason, f"unsupported.{extra_id}")
    return ExtrasManifest(
        platform=_text(root["platform"], "platform", maximum=40),
        python=_text(root["python"], "python", maximum=10),
        extras=extras,
        unsupported=unsupported,
        data_dir=data_dir,
    )


def load_manifest(path: Path | None = None) -> ExtrasManifest:
    path = path or MANIFEST_PATH
    try:
        raw = path.read_bytes()
        if len(raw) > _MAX_JSON_BYTES:
            raise ValueError("manifest too large")
        document = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique)
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        raise ExtrasError("manifest_invalid", "No se pudo leer el manifiesto de extras.", status_code=500) from exc
    return parse_manifest(document, path.parent)


def interpreter_platform() -> str:
    return sysconfig.get_platform().replace("-", "_").replace(".", "_")


def compatibility_error(manifest: ExtrasManifest) -> str:
    python = f"{sys.version_info[0]}.{sys.version_info[1]}"
    if python != manifest.python:
        return f"Los extras aprobados requieren Python {manifest.python}; este runtime usa {python}."
    if interpreter_platform() != manifest.platform:
        return f"Los extras aprobados solo están disponibles para {manifest.platform}."
    return ""


def _spec(extra_id: str, manifest: ExtrasManifest) -> ExtraSpec:
    if not isinstance(extra_id, str) or not _ID.fullmatch(extra_id):
        raise ExtrasError("unknown_extra", "Extra desconocido.", status_code=404)
    if extra_id in manifest.unsupported:
        raise ExtrasError("unsupported", manifest.unsupported[extra_id], status_code=409)
    spec = manifest.extras.get(extra_id)
    if spec is None:
        raise ExtrasError("unknown_extra", f"Extra desconocido: {extra_id}.", status_code=404)
    return spec


def _safe_filename(name: str) -> bool:
    return (
        bool(name)
        and name == PurePosixPath(name).name
        and not any(char in name for char in '\\:<>"|?*\0')
        and name.endswith(".whl")
        and not name.startswith(".")
    )


def locked_wheels(spec: ExtraSpec, manifest: ExtrasManifest) -> list[LockedWheel]:
    """Return exactly the wheels the approved lock selects for this interpreter."""
    path = manifest.data_dir / spec.lock
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ExtrasError("lock_invalid", f"Falta el lock aprobado de {spec.label}.", status_code=500) from exc
    if hashlib.sha256(raw).hexdigest() != spec.lock_sha256:
        raise ExtrasError("lock_invalid", f"El lock de {spec.label} no coincide con el aprobado.", status_code=500)
    try:
        lock = Pylock.from_dict(tomllib.loads(raw.decode("utf-8")))
    except Exception as exc:
        raise ExtrasError("lock_invalid", f"El lock de {spec.label} no es PEP 751 válido.", status_code=500) from exc
    try:
        selected = list(lock.select(tags=list(sys_tags())))
    except Exception as exc:
        raise ExtrasError("incompatible", f"{spec.label} no tiene paquetes para este sistema: {exc}") from exc
    wheels = []
    for package, dist in selected:
        if not isinstance(dist, PackageWheel):
            raise ExtrasError("lock_invalid", f"{spec.label} requiere compilar {package.name}; solo se admiten wheels.",
                              status_code=500)
        url = dist.url or ""
        parts = urlsplit(url)
        sha = dict(dist.hashes or {}).get("sha256", "")
        filename = dist.filename
        if (
            parts.scheme != "https" or parts.hostname not in ALLOWED_HOSTS or parts.username or parts.password
            or parts.port not in {None, 443} or not _SHA256.fullmatch(str(sha))
            or type(dist.size) is not int or dist.size <= 0 or not _safe_filename(filename)
            or package.version is None
        ):
            raise ExtrasError("lock_invalid", f"Entrada no aprobada en el lock de {spec.label}: {package.name}.",
                              status_code=500)
        wheels.append(LockedWheel(str(package.name), str(package.version), filename, url, dist.size, sha))
    if not wheels:
        raise ExtrasError("lock_invalid", f"El lock de {spec.label} no selecciona paquetes.", status_code=500)
    return wheels


# ── Profile paths and state ─────────────────────────────────────────


def extras_paths(home: Path | None = None) -> ExtrasPaths:
    if home is None:
        from core.config import get_iris_home

        home = get_iris_home()
    home = validate_profile_home(home)
    root = home / "extras"
    _plain_directory(root, home)
    return ExtrasPaths(home, root)


def _directory(paths: ExtrasPaths, path: Path, *, create: bool) -> Path:
    _plain_directory(path, paths.home)
    if create:
        path.mkdir(parents=True, exist_ok=True)
        _plain_directory(path, paths.home)
    return path


def _read_json(path: Path, limit: int = _MAX_JSON_BYTES) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("not a regular file")
    info = path.lstat()
    if info.st_nlink != 1 or info.st_size > limit:
        raise ValueError("unexpected file")
    data = json.loads(path.read_bytes().decode("utf-8"), object_pairs_hook=_unique)
    if not isinstance(data, dict):
        raise ValueError("not an object")
    return data


def _write_json_atomic(path: Path, document: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(document, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_state(extra_dir: Path, extra_id: str) -> dict[str, Any] | None:
    path = extra_dir / "state.json"
    if not path.exists() and not path.is_symlink():
        return None
    try:
        state = _read_json(path)
    except (OSError, UnicodeError, ValueError, RecursionError):
        return {"invalid": True}
    if (
        set(state) != {"schema_version", "extra", "generation", "lock_sha256", "installed_at", "packages"}
        or state["schema_version"] != 1 or state["extra"] != extra_id
        or not isinstance(state["generation"], str) or not _GENERATION.fullmatch(state["generation"])
        or not isinstance(state["lock_sha256"], str) or not _SHA256.fullmatch(state["lock_sha256"])
        or not isinstance(state["packages"], dict)
    ):
        return {"invalid": True}
    return state


@contextmanager
def _operation_lock(paths: ExtrasPaths) -> Iterator[None]:
    _directory(paths, paths.root, create=True)
    with _open_plain_file(paths.root / ".operation.lock") as handle:
        if not _try_lock(handle):
            raise ExtrasError("busy", "Otra operación de extras está en curso en este perfil.")
        try:
            yield
        finally:
            _unlock(handle)


def _remove_tree(path: Path) -> bool:
    """Remove a profile-owned tree without following links; report leftovers."""
    if not path.exists() and not path.is_symlink():
        return True
    if path.is_symlink() or path.resolve(strict=False) != path.parent.resolve(strict=False) / path.name:
        # Remove the alias itself; never descend into its target.
        try:
            path.unlink()
        except OSError:
            os.rmdir(path)
        return True

    def retry_readonly(function, target, _info):
        try:
            os.chmod(target, stat.S_IWRITE)
            function(target)
        except OSError:
            pass

    # Python 3.11 runtime: ``onexc`` exists only from 3.12. On Windows rmtree
    # does not follow junctions found inside the tree.
    for delay in (*_SHARING_RETRY_DELAYS[:3], None):
        shutil.rmtree(path, onerror=retry_readonly)
        if not path.exists() or delay is None:
            break
        time.sleep(delay)
    return not path.exists()


def _replace_with_retry(source: Path, target: Path) -> None:
    for delay in (*_SHARING_RETRY_DELAYS, None):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if delay is None:
                raise
            time.sleep(delay)


def _cleanup_leftovers(extra_dir: Path, active_generation: str | None) -> None:
    for entry in extra_dir.iterdir() if extra_dir.is_dir() else ():
        name = entry.name
        stale_env = name.startswith("env-") and name != f"env-{active_generation}"
        if name.startswith((".staging-", ".trash-")) or stale_env:
            if not _remove_tree(entry):
                logger.info("Extra leftover still in use, kept for a later cleanup: %s", entry)


# ── Download, extraction and verification ───────────────────────────


def _emit(progress: Callable[[dict[str, Any]], None] | None, **event: Any) -> None:
    if progress is not None:
        progress(event)


def _check_cancel(cancel: threading.Event) -> None:
    if cancel.is_set():
        raise ExtrasError("cancelled", "Instalación cancelada. El entorno anterior no se modificó.")


def _free_bytes(path: Path) -> int:
    return shutil.disk_usage(path).free


def _require_space(path: Path, needed: int) -> None:
    free = _free_bytes(path)
    if free < needed + SPACE_MARGIN:
        missing_mb = (needed + SPACE_MARGIN - free) // (1024 * 1024) + 1
        raise ExtrasError(
            "insufficient_space",
            f"Espacio insuficiente en el perfil: faltan unos {missing_mb} MB. No se modificó nada.",
            status_code=507,
        )


def _verified_file(path: Path, wheel: LockedWheel) -> bool:
    if path.is_symlink() or not path.is_file() or path.stat().st_size != wheel.size:
        return False
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest() == wheel.sha256


# A stalled or dropped connection retries the same file; a hash mismatch never does.
_DOWNLOAD_ATTEMPTS = 3
_DOWNLOAD_RETRY_DELAYS = (2.0, 5.0)


def _download(
    wheel: LockedWheel,
    downloads: Path,
    client: httpx.Client,
    cancel: threading.Event,
    progress: Callable[[dict[str, Any]], None] | None,
) -> Path:
    target = downloads / f"{wheel.sha256[:16]}-{wheel.filename}"
    if target.exists():
        if _verified_file(target, wheel):
            _emit(progress, type="log", phase="fetch", text=f"Reutilizando {wheel.filename} verificado")
            return target
        target.unlink()
    for attempt in range(1, _DOWNLOAD_ATTEMPTS + 1):
        try:
            return _download_once(wheel, target, client, cancel, progress)
        except ExtrasError as exc:
            if not getattr(exc, "transient", False) or attempt == _DOWNLOAD_ATTEMPTS:
                raise
            _emit(progress, type="log", phase="fetch",
                  text=f"Reintentando {wheel.filename} ({attempt + 1}/{_DOWNLOAD_ATTEMPTS}) tras un fallo de red…")
            delay = _DOWNLOAD_RETRY_DELAYS[min(attempt - 1, len(_DOWNLOAD_RETRY_DELAYS) - 1)]
            if delay > 0:
                cancel.wait(delay)
            _check_cancel(cancel)
    raise AssertionError("unreachable")


def _download_once(
    wheel: LockedWheel,
    target: Path,
    client: httpx.Client,
    cancel: threading.Event,
    progress: Callable[[dict[str, Any]], None] | None,
) -> Path:
    part = target.parent / f".{uuid.uuid4().hex[:12]}.part"
    try:
        digest = hashlib.sha256()
        written = 0
        with client.stream("GET", wheel.url) as response:
            if response.status_code != 200:
                error = ExtrasError("download_failed",
                                    f"No se pudo descargar {wheel.filename} (HTTP {response.status_code}).",
                                    status_code=502)
                error.transient = response.status_code == 429 or response.status_code >= 500  # type: ignore[attr-defined]
                raise error
            with part.open("xb") as output:
                for chunk in response.iter_bytes(CHUNK):
                    _check_cancel(cancel)
                    written += len(chunk)
                    if written > wheel.size:
                        raise ExtrasError("corrupt_download", f"{wheel.filename} supera el tamaño aprobado.",
                                          status_code=502)
                    output.write(chunk)
                    digest.update(chunk)
                    _emit(progress, type="progress", phase="fetch", file=wheel.filename,
                          done=written, total=wheel.size)
        if written != wheel.size or digest.hexdigest() != wheel.sha256:
            raise ExtrasError("corrupt_download", f"La descarga de {wheel.filename} no coincide con el hash aprobado.",
                              status_code=502)
        os.replace(part, target)
        return target
    except httpx.HTTPError as exc:
        reason = "la conexión se detuvo" if isinstance(exc, httpx.TimeoutException) else "falló la conexión"
        error = ExtrasError("download_failed", f"No se pudo descargar {wheel.filename}: {reason} ({type(exc).__name__}).",
                            status_code=502)
        error.transient = isinstance(exc, httpx.TransportError)  # type: ignore[attr-defined]
        raise error from exc
    finally:
        part.unlink(missing_ok=True)


def _member_target(name: str, data_dir: str) -> tuple[str, ...] | None:
    """Map a wheel member to its site-relative parts, or ``None`` to skip it."""
    if (
        not name or name.startswith("/") or "\\" in name or ":" in name or "\0" in name
        or any(ord(char) < 32 for char in name)
    ):
        raise ValueError("unsafe member name")
    parts = tuple(part for part in name.split("/") if part)
    if not parts or any(
        part in {".", ".."} or part.endswith((".", " ")) or _DEVICE.fullmatch(part.split(".")[0].rstrip(" "))
        for part in parts
    ):
        raise ValueError("unsafe member path")
    if parts[0] != data_dir:
        return parts
    if len(parts) < 3:
        return None
    if parts[1] in {"purelib", "platlib", "data"}:
        return parts[2:]
    if parts[1] in {"scripts", "headers"}:
        return None  # console launchers and C headers are not used by workers
    raise ValueError("unknown wheel data scheme")


def _long_paths_enabled() -> bool:
    """Read (never change) Windows' long path policy; other systems have no 260 limit."""
    if os.name != "nt":
        return True
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem") as key:
            return winreg.QueryValueEx(key, "LongPathsEnabled")[0] == 1
    except OSError:
        return False


# CreateDirectoryW stops at 248 characters and files at 259 without long paths.
_MAX_DIRECTORY, _MAX_FILE = 247, 259


def _path_too_long(path: Path) -> ExtrasError:
    return ExtrasError(
        "path_too_long",
        f"La carpeta del perfil es demasiado profunda para este extra ({len(str(path))} caracteres; "
        f"Windows sin rutas largas admite {_MAX_FILE}). Usa un perfil en una ruta más corta.",
    )


def _extract(wheel_path: Path, site: Path, cancel: threading.Event) -> int:
    root = os.path.normcase(str(site.resolve(strict=True))) + os.sep
    written = 0
    with zipfile.ZipFile(wheel_path) as archive:
        tops = {info.filename.split("/", 1)[0] for info in archive.infolist()}
        dist_info = [top for top in tops if top.endswith(".dist-info")]
        if len(dist_info) != 1:
            raise ValueError("wheel must contain one .dist-info directory")
        data_dir = dist_info[0][: -len(".dist-info")] + ".data"
        members = []
        for info in archive.infolist():
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise ValueError("links are not allowed in wheels")
            if info.is_dir():
                continue
            parts = _member_target(info.filename, data_dir)
            if parts is None:
                continue
            target = site.joinpath(*parts)
            if not os.path.normcase(os.path.abspath(target)).startswith(root):
                raise ValueError("member escapes the environment")
            members.append((info, target))
        # Validate every target before writing anything from this wheel.
        if not _long_paths_enabled():
            for _info, target in members:
                if len(str(target)) > _MAX_FILE or len(str(target.parent)) > _MAX_DIRECTORY:
                    raise _path_too_long(target)
        for info, target in members:
            _check_cancel(cancel)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("wb") as output:
                for block in iter(lambda: source.read(CHUNK), b""):
                    output.write(block)
            written += info.file_size
    return written


def _uncompressed_size(wheel_path: Path) -> int:
    with zipfile.ZipFile(wheel_path) as archive:
        return sum(info.file_size for info in archive.infolist())


def _receipt(site: Path, cancel: threading.Event | None = None) -> dict[str, Any]:
    files: dict[str, list[Any]] = {}
    for path in sorted(site.rglob("*")):
        if cancel is not None:
            _check_cancel(cancel)
        if path.is_symlink():
            raise ValueError("link inside the environment")
        if not path.is_file():
            continue
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(CHUNK), b""):
                digest.update(block)
        files[path.relative_to(site).as_posix()] = [path.stat().st_size, digest.hexdigest()]
    return {"schema_version": 1, "files": files}


def verify_environment(env_dir: Path, cancel: threading.Event | None = None) -> list[str]:
    """Return missing or modified files; extra runtime files are tolerated."""
    try:
        receipt = _read_json(env_dir / "receipt.json", _MAX_RECEIPT_BYTES)
        files = receipt["files"]
        if receipt.get("schema_version") != 1 or not isinstance(files, dict):
            raise ValueError("invalid receipt")
    except (OSError, UnicodeError, ValueError, KeyError, RecursionError):
        return ["receipt.json"]
    site = env_dir / "site"
    problems = []
    for relative, expected in files.items():
        if cancel is not None:
            _check_cancel(cancel)
        path = site / relative
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size != expected[0]:
                problems.append(relative)
                continue
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for block in iter(lambda: handle.read(CHUNK), b""):
                    digest.update(block)
            if digest.hexdigest() != expected[1]:
                problems.append(relative)
        except OSError:
            problems.append(relative)
    return problems


# ── Worker processes ────────────────────────────────────────────────


def worker_command(site: Path, *mode: str, pycache: Path | None = None) -> list[str]:
    """Isolated Iris interpreter: no base site-packages, user site or PYTHON*."""
    python = get_runtime().iris_python
    flags = ["-I", "-S", "-X", "utf8"]
    flags += ["-X", f"pycache_prefix={pycache}"] if pycache is not None else ["-B"]
    return [str(python), *flags, str(WORKER_SCRIPT), "--site", str(site), *mode]


def worker_environment(models: Path | None = None) -> dict[str, str]:
    env = runtime_subprocess_env()
    runtime = get_runtime()
    if runtime.packaged:
        from core.audio_decoder import private_audio_bin

        paths = [str(private_audio_bin(runtime)), str(runtime.iris_python.parent)]
        if os.name == "nt":
            windows = Path(os.environ.get("SystemRoot", r"C:\Windows"))
            paths.append(str(windows / "System32"))
        env["PATH"] = os.pathsep.join(paths)
    if models is not None:
        env.update({key: str(models / name) for key, name in _CACHE_DIRECTORIES.items()})
        env.update({"HF_HUB_DISABLE_TELEMETRY": "1", "TOKENIZERS_PARALLELISM": "false"})
    return env


def _validate_writable_directories(environment: ExtraEnvironment, *, create: bool) -> None:
    """Reject a symlink/junction anywhere from the profile root to each cache.

    Run before resolving and again right before launching: a cache directory
    that aliases another folder would let engines write outside the profile.
    Creating them here means the worker never has to create its cache roots.
    """
    for directory in environment.writable_directories():
        _plain_directory(directory, environment.home)
        if create:
            directory.mkdir(parents=True, exist_ok=True)
            _plain_directory(directory, environment.home)


def _creationflags() -> int:
    return _CREATE_NO_WINDOW if os.name == "nt" else 0


def _parse_reply(data: bytes) -> dict[str, Any] | None:
    for line in data.splitlines():
        index = line.find(MARKER)
        if index < 0:
            continue
        try:
            value = json.loads(line[index + len(MARKER):].decode("utf-8"))
        except (UnicodeError, ValueError):
            continue
        if isinstance(value, dict):
            return value
    return None


def run_probe(site: Path, modules: tuple[str, ...], cancel: threading.Event,
              timeout: float = PROBE_TIMEOUT) -> dict[str, Any]:
    command = worker_command(site, "--probe", ",".join(modules))
    process = subprocess.Popen(
        command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=worker_environment(), cwd=str(site), creationflags=_creationflags(),
    )
    deadline = time.monotonic() + timeout
    while True:
        try:
            stdout, stderr = process.communicate(timeout=0.25)
            break
        except subprocess.TimeoutExpired:
            if cancel.is_set() or time.monotonic() > deadline:
                process.kill()
                process.communicate()
                _check_cancel(cancel)
                raise ExtrasError("probe_failed", "La comprobación del extra superó el tiempo límite.") from None
    reply = _parse_reply(stdout)
    if process.returncode != 0 or not reply or reply.get("ok") is not True:
        detail = (reply or {}).get("detail") or stderr.decode("utf-8", "replace").strip()[-400:]
        raise ExtrasError("probe_failed", f"El extra no se pudo importar en su entorno aislado: {detail}")
    return reply


# ── Public operations ───────────────────────────────────────────────


def _active_environment(paths: ExtrasPaths, extra_id: str, state: dict[str, Any] | None) -> Path | None:
    if not state or state.get("invalid"):
        return None
    env_dir = paths.extra(extra_id) / f"env-{state['generation']}"
    try:
        _plain_directory(env_dir / "site", paths.home)
    except ProfilePathError:
        return None
    if not (env_dir / "site").is_dir() or not (env_dir / "receipt.json").is_file():
        return None
    return env_dir


def install_extra(
    extra_id: str,
    *,
    repair: bool = False,
    progress: Callable[[dict[str, Any]], None] | None = None,
    cancel: threading.Event | None = None,
    manifest: ExtrasManifest | None = None,
    home: Path | None = None,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    """Install or repair one approved extra; the active environment changes only on commit."""
    manifest = manifest or load_manifest()
    spec = _spec(extra_id, manifest)
    problem = compatibility_error(manifest)
    if problem:
        raise ExtrasError("incompatible", problem)
    wheels = locked_wheels(spec, manifest)
    cancel = cancel or threading.Event()
    paths = extras_paths(home)
    with _operation_lock(paths):
        extra_dir = _directory(paths, paths.extra(extra_id), create=True)
        state = _read_state(extra_dir, extra_id)
        active = _active_environment(paths, extra_id, state)
        _cleanup_leftovers(extra_dir, state.get("generation") if active and state else None)
        if active is not None and state and state["lock_sha256"] == spec.lock_sha256:
            _emit(progress, type="log", phase="verify", text="Verificando el entorno instalado…")
            problems = verify_environment(active, cancel) if repair else []
            if not problems:
                _emit(progress, type="log", phase="done", text=f"{spec.label} ya está instalado y verificado.")
                return {"status": "installed", "changed": False, "verified": repair}
            _emit(progress, type="log", phase="verify",
                  text=f"{len(problems)} archivo(s) dañados; se reconstruye en un entorno nuevo.")
        downloads = _directory(paths, paths.root / ".downloads", create=True)
        for leftover in downloads.glob(".*.part"):
            leftover.unlink(missing_ok=True)
        pending = sum(w.size for w in wheels
                      if not _verified_file(downloads / f"{w.sha256[:16]}-{w.filename}", w))
        _require_space(paths.root, pending + INSTALL_ESTIMATE_FACTOR * sum(w.size for w in wheels))
        _emit(progress, type="log", phase="resolve",
              text=f"Instalando {spec.label}: {len(wheels)} paquetes aprobados.")
        files = []
        with httpx.Client(transport=transport, timeout=httpx.Timeout(60.0, connect=20.0),
                          follow_redirects=False) as client:
            for wheel in wheels:
                _check_cancel(cancel)
                _emit(progress, type="log", phase="fetch", text=f"Descargando {wheel.filename}")
                files.append(_download(wheel, downloads, client, cancel, progress))
        # Extract straight into a generation nobody references yet; writing
        # state.json is the only commit, so a crash leaves a cleanable leftover.
        generation = uuid.uuid4().hex[:12]
        env_dir = extra_dir / f"env-{generation}"
        try:
            site = env_dir / "site"
            site.mkdir(parents=True)
            _require_space(paths.root, sum(_uncompressed_size(path) for path in files))
            for path in files:
                _emit(progress, type="log", phase="install", text=f"Extrayendo {path.name.split('-', 1)[1]}")
                try:
                    _extract(path, site, cancel)
                except (zipfile.BadZipFile, ValueError, OSError) as exc:
                    if isinstance(exc, OSError) and getattr(exc, "errno", None) == 28:
                        raise ExtrasError("insufficient_space", "El disco se llenó durante la instalación.",
                                          status_code=507) from exc
                    if isinstance(exc, OSError) and getattr(exc, "winerror", None) == 206:
                        raise _path_too_long(Path(exc.filename or site)) from exc
                    raise ExtrasError("install_failed", f"Wheel rechazado: {path.name}: {exc}") from exc
            _emit(progress, type="log", phase="verify", text="Registrando hashes del entorno…")
            _write_json_atomic(env_dir / "receipt.json", _receipt(site, cancel))
            _emit(progress, type="log", phase="verify", text="Comprobando el extra en un intérprete aislado…")
            run_probe(site, spec.probe, cancel)
            _check_cancel(cancel)
        except BaseException:
            _remove_tree(env_dir)
            raise
        _write_json_atomic(extra_dir / "state.json", {
            "schema_version": 1, "extra": extra_id, "generation": generation,
            "lock_sha256": spec.lock_sha256,
            "installed_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
            "packages": {wheel.name: wheel.version for wheel in wheels},
        })
        _cleanup_leftovers(extra_dir, generation)
        for path in files:
            path.unlink(missing_ok=True)
        _emit(progress, type="log", phase="done", text=f"{spec.label} instalado en su entorno aislado.")
        return {"status": "installed", "changed": True, "packages": len(wheels)}


def remove_extra(
    extra_id: str,
    *,
    purge_models: bool = False,
    manifest: ExtrasManifest | None = None,
    home: Path | None = None,
) -> dict[str, Any]:
    """Remove one environment; models are kept unless explicitly purged."""
    manifest = manifest or load_manifest()
    if not isinstance(extra_id, str) or not _ID.fullmatch(extra_id) or (
        extra_id not in manifest.extras and extra_id not in manifest.unsupported
    ):
        raise ExtrasError("unknown_extra", "Extra desconocido.", status_code=404)
    paths = extras_paths(home)
    with _operation_lock(paths):
        extra_dir = paths.extra(extra_id)
        _plain_directory(extra_dir, paths.home)
        if not extra_dir.is_dir():
            return {"status": "not_installed", "models_kept": False}
        state = _read_state(extra_dir, extra_id)
        environments = sorted(extra_dir.glob("env-*"))
        active = f"env-{state['generation']}" if state and not state.get("invalid") else None
        environments.sort(key=lambda path: path.name != active)
        trashed = []
        for index, env_dir in enumerate(environments):
            trash = extra_dir / f".trash-{uuid.uuid4().hex[:12]}"
            try:
                _replace_with_retry(env_dir, trash)
            except OSError as exc:
                if index == 0:
                    raise ExtrasError("in_use", "El extra está en uso. Cierra Iris o cambia de voz y vuelve a intentarlo.",
                                      status_code=409) from exc
                continue
            trashed.append(trash)
        (extra_dir / "state.json").unlink(missing_ok=True)
        leftovers = [str(path.name) for path in trashed if not _remove_tree(path)]
        _remove_tree(extra_dir / "pycache")
        models = extra_dir / "models"
        if purge_models and not _remove_tree(models):
            # _remove_tree deletes an aliased folder as a link, never its target.
            leftovers.append("models")
        try:
            _plain_directory(models, paths.home)
            models_kept = models.is_dir() and any(models.iterdir())
        except ProfilePathError:
            models_kept = False
        if not any(extra_dir.iterdir()):
            extra_dir.rmdir()
        return {"status": "not_installed", "models_kept": models_kept, "leftovers": leftovers}


def resolve_environment(
    extra_id: str,
    *,
    manifest: ExtrasManifest | None = None,
    home: Path | None = None,
) -> ExtraEnvironment | None:
    """Return the committed, approved environment for a consumer; never create one."""
    manifest = manifest or load_manifest()
    spec = manifest.extras.get(extra_id)
    if spec is None or compatibility_error(manifest):
        return None
    paths = extras_paths(home)
    extra_dir = paths.extra(extra_id)
    _plain_directory(extra_dir, paths.home)
    state = _read_state(extra_dir, extra_id) if extra_dir.is_dir() else None
    active = _active_environment(paths, extra_id, state)
    if active is None or not state or state["lock_sha256"] != spec.lock_sha256:
        return None
    environment = ExtraEnvironment(
        extra_id, active, active / "site", extra_dir / "models", extra_dir / "pycache", paths.home,
    )
    _validate_writable_directories(environment, create=False)
    return environment


def extras_status(*, manifest: ExtrasManifest | None = None, home: Path | None = None) -> dict[str, Any]:
    manifest = manifest or load_manifest()
    problem = compatibility_error(manifest)
    try:
        paths = extras_paths(home)
    except ProfilePathError as exc:
        paths, problem = None, problem or str(exc)
    extras = {}
    for extra_id, spec in manifest.extras.items():
        entry: dict[str, Any] = {
            "label": spec.label, "consumer": spec.consumer, "requirements": list(spec.requirements),
            "download_bytes": spec.download_bytes, "packages": spec.packages,
            "models": spec.models, "warning": spec.warning, "models_present": False,
        }
        if problem or paths is None:
            entry.update(state="incompatible", detail=problem)
        else:
            extra_dir = paths.extra(extra_id)
            try:
                _plain_directory(extra_dir, paths.home)
                state = _read_state(extra_dir, extra_id) if extra_dir.is_dir() else None
                active = _active_environment(paths, extra_id, state)
                models = extra_dir / "models"
                if active is not None:
                    _validate_writable_directories(ExtraEnvironment(
                        extra_id, active, active / "site", models, extra_dir / "pycache", paths.home,
                    ), create=False)
                _plain_directory(models, paths.home)
                entry["models_present"] = models.is_dir() and any(models.iterdir())
            except (ProfilePathError, OSError):
                state, active = {"invalid": True}, None
            if state is None:
                entry["state"] = "not_installed"
            elif active is None:
                entry.update(state="broken", detail="El entorno del extra está incompleto; repáralo o quítalo.")
            elif state["lock_sha256"] != spec.lock_sha256:
                entry.update(state="outdated", detail="Esta versión de Iris aprobó otro lock; repáralo para actualizar.")
            else:
                entry.update(state="installed", installed_at=state["installed_at"],
                              versions=state["packages"])
        extras[extra_id] = entry
    return {
        "extras": extras,
        "unsupported": dict(manifest.unsupported),
        "platform": manifest.platform,
        "python": manifest.python,
    }


# ── Async worker client ─────────────────────────────────────────────


class ExtraWorker:
    """One isolated worker process speaking the line protocol of ``extras_worker``."""

    def __init__(self, environment: ExtraEnvironment) -> None:
        self.environment = environment
        self._process: asyncio.subprocess.Process | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._next_id = 0
        self.stderr_tail: list[str] = []

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    async def start(self, timeout: float = 60.0) -> None:
        env = self.environment
        # Revalidate: the profile may have changed since the environment was resolved.
        _validate_writable_directories(env, create=True)
        self._process = await asyncio.create_subprocess_exec(
            *worker_command(env.site, "--serve", pycache=env.pycache),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, env=worker_environment(env.models),
            cwd=str(env.env_dir), creationflags=_creationflags(), limit=32 * 1024 * 1024,
        )
        self._stderr_task = asyncio.create_task(self._pump_stderr())
        try:
            ready = await asyncio.wait_for(self._read_reply(), timeout)
        except BaseException:
            await self.close()
            raise
        if ready.get("op") != "ready" or ready.get("protocol") != PROTOCOL:
            await self.close()
            raise ExtrasError("worker_failed", "El proceso del extra no respondió con el protocolo esperado.")

    async def _pump_stderr(self) -> None:
        assert self._process is not None and self._process.stderr is not None
        async for raw in self._process.stderr:
            line = raw.decode("utf-8", "replace").rstrip()
            if line:
                self.stderr_tail = (self.stderr_tail + [line])[-20:]
                logger.info("[extra %s] %s", self.environment.extra_id, line[:500])

    async def _read_reply(self) -> dict[str, Any]:
        assert self._process is not None and self._process.stdout is not None
        while True:
            line = await self._process.stdout.readline()
            if not line:
                tail = " | ".join(self.stderr_tail[-3:])
                raise ExtrasError("worker_failed", f"El proceso del extra terminó inesperadamente. {tail}".strip())
            reply = _parse_reply(line)
            if reply is not None:
                return reply
            logger.debug("Stray extra worker output: %r", line[:200])

    async def request(self, payload: dict[str, Any], *, timeout: float) -> dict[str, Any]:
        async with self._lock:
            if not self.running:
                raise ExtrasError("worker_failed", "El proceso del extra no está en ejecución.")
            assert self._process is not None and self._process.stdin is not None
            self._next_id += 1
            message = dict(payload, id=self._next_id)
            self._process.stdin.write(json.dumps(message).encode("utf-8") + b"\n")
            try:
                await self._process.stdin.drain()
                reply = await asyncio.wait_for(self._read_reply(), timeout)
            except BaseException:
                # A timed out or cancelled request leaves the protocol desynchronised.
                await self._kill()
                raise
            if reply.get("id") != self._next_id:
                await self._kill()
                raise ExtrasError("worker_failed", "Respuesta del extra fuera de orden.")
            return reply

    async def _kill(self) -> None:
        process, self._process = self._process, None
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()
        if self._stderr_task is not None:
            self._stderr_task.cancel()
            self._stderr_task = None

    async def close(self) -> None:
        process = self._process
        if process is not None and process.returncode is None and process.stdin is not None:
            try:
                process.stdin.write(b'{"op":"shutdown"}\n')
                await process.stdin.drain()
                await asyncio.wait_for(process.wait(), 3.0)
            except (OSError, TimeoutError, ConnectionError):
                pass
        await self._kill()
