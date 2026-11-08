"""Third-party notices of the running Iris package, read-only.

The lists come from the inventories written when the package was prepared:
the private runtime inventory next to the runtime descriptor (A08.4), the
web asset manifest inside the Iris tree (A08.3) and, in a full staging, the
notices of what Iris.exe links (A08.7). Only a file that one of those
lists as a license or notice is served, and only after its recorded size and
SHA-256 match, so this route never becomes a reader for arbitrary files.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import threading
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from core.runtime_descriptor import get_runtime

router = APIRouter(prefix="/api/licenses", tags=["licenses"])

RUNTIME_INVENTORY = "runtime-inventory.json"
WEB_MANIFEST = "face/web-assets.json"
STAGING_MANIFEST = "iris-staging.json"
DESKTOP_LICENSES = "desktop/licenses.json"
COMPANION_LICENSES = "companion/licenses.json"
INSTALLER_LICENSES = "installer/licenses.json"
MAX_INVENTORY_BYTES = 32 * 1024 * 1024
MAX_TEXT_BYTES = 2 * 1024 * 1024
# Source archives ship for license compliance; they are listed, not displayed.
_BINARY_SUFFIXES = frozenset({
    ".gz", ".tgz", ".tar", ".zip", ".whl", ".xz", ".bz2", ".7z",
    ".dll", ".exe", ".pyd", ".so", ".dylib", ".bin", ".png", ".jpg", ".jpeg",
})
GROUPS = {
    "iris-runtime": "Runtime de Iris",
    "hermes-source": "Hermes Agent",
    "hermes-runtime": "Runtime de Hermes Agent",
    "audio": "Audio privado",
    "web": "Interfaz web y fuentes",
    "desktop": "Iris Desktop (ventana nativa)",
    "companion": "Iris Companion",
    "installer": "Instalador de Windows",
    "project": "IRIS OS",
}
_HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


class LicenseSourceError(ValueError):
    """An inventory is missing, unreadable or describes a file it cannot vouch for."""


@dataclass(frozen=True)
class _Source:
    id: str
    root: Path
    records: tuple[dict[str, Any], ...]
    files: dict[str, tuple[int, str]]  # relative path -> (size, sha256); empty = no hash check
    pending: int = 0  # notices the inventory itself declares as still to review


# source id -> (inventory identity, parsed source); a new installation replaces it.
_cache: dict[str, tuple[tuple[str, int, int], _Source]] = {}
_cache_lock = threading.Lock()


def _relative(value: Any) -> str:
    """A portable path inside its root: forward slashes, no drive, dots or empty parts."""
    if not isinstance(value, str) or not value or len(value) > 1024 or "\\" in value or ":" in value:
        raise LicenseSourceError("Ruta de licencia no válida.")
    parts = PurePosixPath(value).parts
    if value.startswith("/") or any(part in {"", ".", ".."} for part in parts) or any(
        ord(c) < 32 for c in value
    ):
        raise LicenseSourceError("Ruta de licencia no válida.")
    return value


def _text(value: Any, maximum: int = 512) -> str:
    return value.strip()[:maximum] if isinstance(value, str) else ""


def _read_json(path: Path) -> dict:
    try:
        with path.open("rb") as handle:
            data = handle.read(MAX_INVENTORY_BYTES + 1)
        if len(data) > MAX_INVENTORY_BYTES:
            raise LicenseSourceError("El inventario supera el tamaño permitido.")
        value = json.loads(data.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise LicenseSourceError("No se pudo leer el inventario de licencias.") from exc
    if not isinstance(value, dict):
        raise LicenseSourceError("El inventario de licencias no es válido.")
    return value


def _load(source_id: str, root: Path, document_path: Path, *, hashed: bool) -> _Source:
    try:
        info = document_path.stat()
    except OSError as exc:
        raise LicenseSourceError("Inventario no disponible.") from exc
    key = (str(document_path), info.st_mtime_ns, info.st_size)
    with _cache_lock:
        cached = _cache.get(source_id)
        if cached and cached[0] == key:
            return cached[1]
    document = _read_json(document_path)
    raw_licenses, raw_files = document.get("licenses"), document.get("files")
    if not isinstance(raw_licenses, list) or (hashed and not isinstance(raw_files, list)):
        raise LicenseSourceError("El inventario de licencias no es válido.")
    files: dict[str, tuple[int, str]] = {}
    for entry in raw_files or ():
        if not isinstance(entry, dict):
            raise LicenseSourceError("El inventario de licencias no es válido.")
        size, digest = entry.get("size"), entry.get("sha256")
        if type(size) is not int or size < 0 or not isinstance(digest, str) or len(digest) != 64:
            raise LicenseSourceError("El inventario de licencias no es válido.")
        files[_relative(entry.get("path"))] = (size, digest.lower())
    records = []
    for record in raw_licenses:
        if not isinstance(record, dict) or not isinstance(record.get("files"), list):
            raise LicenseSourceError("El inventario de licencias no es válido.")
        paths = [_relative(path) for path in record["files"]]
        if hashed and any(path not in files for path in paths):
            raise LicenseSourceError("El inventario cita un archivo que no registra.")
        records.append({
            "source": source_id,
            "group": record.get("component") if source_id == "runtime" else source_id,
            "package": _text(record.get("package"), 200),
            "version": _text(record.get("version"), 80),
            "license": _text(record.get("license"), 300),
            "url": _text(record.get("source"), 500),
            "files": paths,
        })
    pending = document.get("pending") or []
    if not isinstance(pending, list):
        raise LicenseSourceError("El inventario de licencias no es válido.")
    result = _Source(source_id, root, tuple(records), files, len(pending))
    with _cache_lock:
        _cache[source_id] = (key, result)
    return result


def _sources() -> list[tuple[str, Path, Path, bool]]:
    runtime = get_runtime()
    candidates = []
    if runtime.packaged:
        candidates.append(("runtime", runtime.base, runtime.base / RUNTIME_INVENTORY, True))
    candidates.append(("web", runtime.iris_root, runtime.iris_root / WEB_MANIFEST, True))
    # A complete staging (Iris.exe next to the runtime) must also list the launcher
    # and the installer code; a version whose manifest declares the Companion must
    # list it too, so a missing folder or inventory shows as unavailable (A08.9).
    if runtime.packaged and (runtime.base / STAGING_MANIFEST).is_file():
        candidates.append(("desktop", runtime.base, runtime.base / DESKTOP_LICENSES, True))
        candidates.append(("installer", runtime.base, runtime.base / INSTALLER_LICENSES, True))
        if _declares_companion(runtime.base / STAGING_MANIFEST) or (runtime.base / "companion").is_dir():
            candidates.append(("companion", runtime.base, runtime.base / COMPANION_LICENSES, True))
    return candidates


def _declares_companion(manifest: Path) -> bool:
    """Whether the version's own manifest ships the Companion. An unreadable
    manifest counts as declaring it: the catalog then cannot claim completeness."""
    try:
        return "companion" in _read_json(manifest)
    except LicenseSourceError:
        return True


def _project_record() -> dict[str, Any] | None:
    """Development has no runtime inventory; still show Iris' own license."""
    runtime = get_runtime()
    if runtime.packaged or not (runtime.iris_root / "LICENSE").is_file():
        return None
    return {"source": "project", "group": "project", "package": "iris-os", "version": "",
            "license": "MIT", "url": "", "files": ["LICENSE"]}


def _viewable(path: str) -> bool:
    return PurePosixPath(path).suffix.lower() not in _BINARY_SUFFIXES


def _public(record: dict[str, Any]) -> dict[str, Any]:
    return {**record, "group_label": GROUPS.get(record["group"], record["group"]),
            "files": [{"path": path, "viewable": _viewable(path)} for path in record["files"]]}


def license_catalog() -> dict[str, Any]:
    runtime = get_runtime()
    sources, components = [], []
    for source_id, root, document, hashed in _sources():
        try:
            loaded = _load(source_id, root, document, hashed=hashed)
        except LicenseSourceError as exc:
            sources.append({"id": source_id, "available": False, "detail": str(exc)})
            continue
        sources.append({"id": source_id, "available": True, "detail": "", "count": len(loaded.records),
                        "pending": loaded.pending})
        components.extend(_public(record) for record in loaded.records)
    project = _project_record()
    if project:
        sources.append({"id": "project", "available": True, "detail": "", "count": 1})
        components.append(_public(project))
    components.sort(key=lambda r: (r["group_label"].casefold(), r["package"].casefold(), r["version"]))
    complete = runtime.packaged and all(s["available"] and not s.get("pending") for s in sources)
    return {"mode": runtime.mode, "complete": complete,
            "sources": sources, "components": components}


def _open_verified(root: Path, relative: str, expected: tuple[int, str] | None) -> bytes:
    path = root.joinpath(*PurePosixPath(relative).parts)
    try:
        resolved, base = path.resolve(strict=True), root.resolve(strict=True)
        resolved.relative_to(base)
        info = path.lstat()
    except (OSError, ValueError, RuntimeError) as exc:
        raise HTTPException(404, "El aviso de licencia no está en la instalación.") from exc
    reparse = getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    # Roots are canonical, so any link along the way changes the resolved path.
    aliased = os.path.normcase(str(resolved)) != os.path.normcase(str(path.absolute()))
    if not stat.S_ISREG(info.st_mode) or reparse or aliased:
        raise HTTPException(404, "El aviso de licencia no está en la instalación.")
    if info.st_size > MAX_TEXT_BYTES or (expected and info.st_size != expected[0]):
        raise HTTPException(409, "El aviso de licencia no coincide con el inventario.")
    with path.open("rb") as handle:
        data = handle.read(MAX_TEXT_BYTES + 1)
    if expected and (len(data) != expected[0] or hashlib.sha256(data).hexdigest() != expected[1]):
        raise HTTPException(409, "El aviso de licencia no coincide con el inventario.")
    return data


def license_text(source: str, path: str) -> dict[str, Any]:
    if source == "project":
        record = _project_record()
        if not record or path != "LICENSE":
            raise HTTPException(404, "Aviso de licencia desconocido.")
        data = _open_verified(get_runtime().iris_root, "LICENSE", None)
    else:
        match = next((item for item in _sources() if item[0] == source), None)
        if match is None:
            raise HTTPException(404, "Aviso de licencia desconocido.")
        try:
            loaded = _load(*match[:3], hashed=match[3])
            relative = _relative(path)
        except LicenseSourceError as exc:
            raise HTTPException(404, str(exc)) from exc
        if not any(relative in record["files"] for record in loaded.records):
            raise HTTPException(404, "Aviso de licencia desconocido.")
        if not _viewable(relative):
            raise HTTPException(415, "Este archivo es código fuente incluido; no se muestra como texto.")
        data = _open_verified(loaded.root, relative, loaded.files.get(relative))
    if b"\x00" in data:
        raise HTTPException(415, "Este aviso no es un archivo de texto.")
    return {"source": source, "path": path, "text": data.decode("utf-8", errors="replace")}


@router.get("")
async def licenses() -> JSONResponse:
    import asyncio

    return JSONResponse(await asyncio.to_thread(license_catalog), headers=_HEADERS)


@router.get("/text")
async def licenses_text(source: str, path: str) -> JSONResponse:
    import asyncio

    if len(source) > 32 or len(path) > 1024:
        raise HTTPException(404, "Aviso de licencia desconocido.")
    return JSONResponse(await asyncio.to_thread(license_text, source, path), headers=_HEADERS)
