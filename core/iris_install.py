"""Versioned Windows installation of Iris and its durable activation state (A08.8).

Layout created by the installer (A08.9) or by ``create_install``::

    <root>/Iris.exe        launcher: starts the active version (install.json)
    <root>/install.json    active/previous version, pending activation, history
    <root>/install.trial   launches of a version that has not confirmed yet
    <root>/v/<id>/         one complete A08.7 staging folder per version
    <root>/updates/        downloads, extraction and the activation log

``<id>`` is the first 8 hex digits of the SHA-256 of the version's
``iris-staging.json``, which binds every file of that version. A new version is
always written to a new folder: nothing ever writes over a version in use.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from core.update_signing import SignatureError, strict_json

STATE_NAME = "install.json"
TRIAL_NAME = "install.trial"
VERSIONS = "v"
UPDATES = "updates"
STAGING_MANIFEST = "iris-staging.json"
INVENTORY = "runtime-inventory.json"
PIN = "runtime-pin.json"
DESCRIPTOR = "runtime.json"
EXECUTABLE = "Iris.exe"
CHANNEL = "channel.json"
# Launches of an unconfirmed version before the launcher falls back (Rust mirror).
TRIAL_LIMIT = 3
WINDOWS_MAX_PATH = 259
OPERATIONS = ("upgrade", "rollback")
PHASES = ("starting", "closing", "stopping", "checking", "backup", "switched", "reverting")
# After these phases the switch is committed; an interruption is not discarded.
COMMITTED = frozenset({"switched", "reverting"})
# Highest manifest sequence seen, per channel address (anti-replay). Keyed by
# address so a version that moves to another channel or host starts that
# channel's count instead of refusing its manifests forever.
MAX_CHANNELS = 8
_ID =re.compile(r"[0-9a-f]{8}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_REVISION = re.compile(r"[0-9a-f]{40}\Z")
_MAX_STATE = 256 * 1024
_MAX_MANIFEST = 32 * 1024 * 1024
_CHUNK = 1024 * 1024
# Regenerable or private bookkeeping, never part of a profile backup.
_IRIS_BACKUP_EXCLUDED = ("extras/", "logs/", "cache/", "tmp/", "temp/", ".profile/backups/",
                         ".profile/writers/", ".profile/coordination.lock")
_HERMES_BACKUP_EXCLUDED = ("logs/", "cache/", "tmp/", "temp/")


class InstallError(RuntimeError):
    """User-facing reason why an installation operation cannot continue."""


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def _is_link(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _read_regular(path: Path, maximum: int) -> bytes:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise InstallError(f"Falta {path.name} en la versión.") from exc
    if _is_link(info) or not stat.S_ISREG(info.st_mode):
        raise InstallError(f"{path.name} no es un archivo normal.")
    if info.st_size > maximum:
        raise InstallError(f"{path.name} supera el tamaño permitido.")
    return path.read_bytes()


def _json(path: Path, maximum: int) -> Any:
    try:
        return strict_json(_read_regular(path, maximum), maximum)
    except SignatureError as exc:
        raise InstallError(f"{path.name} no es válido: {exc}") from exc


@dataclass(frozen=True)
class Install:
    root: Path

    @property
    def state_path(self) -> Path:
        return self.root / STATE_NAME

    @property
    def trial_path(self) -> Path:
        return self.root / TRIAL_NAME

    @property
    def versions(self) -> Path:
        return self.root / VERSIONS

    @property
    def updates(self) -> Path:
        return self.root / UPDATES

    @property
    def launcher(self) -> Path:
        return self.root / EXECUTABLE

    def folder(self, version: str) -> Path:
        if not _ID.match(version or ""):
            raise InstallError("Identificador de versión inválido.")
        return self.versions / version


def version_id(folder: Path) -> str:
    return hashlib.sha256(_read_regular(Path(folder) / STAGING_MANIFEST, _MAX_MANIFEST)).hexdigest()[:8]


def find_install(runtime: Any = None) -> Install | None:
    """The versioned installation the running packaged runtime belongs to."""
    if runtime is None:
        from core.runtime_descriptor import get_runtime

        runtime = get_runtime()
    if not runtime.packaged:
        return None
    base = Path(runtime.base)
    if base.parent.name.casefold() != VERSIONS or not _ID.match(base.name):
        return None
    root = base.parent.parent
    return Install(root) if (root / STATE_NAME).is_file() else None


def own_version_id(runtime: Any = None) -> str:
    if runtime is None:
        from core.runtime_descriptor import get_runtime

        runtime = get_runtime()
    return version_id(Path(runtime.base))


# ── Durable state ──────────────────────────────────────────────────────────

def _version_record(value: Any) -> None:
    if (not isinstance(value, dict) or not isinstance(value.get("iris"), str)
            or not _REVISION.match(str(value.get("iris_revision", "")))
            or not isinstance(value.get("hermes"), str)
            or not _REVISION.match(str(value.get("hermes_revision", "")))
            or type(value.get("profile_format")) is not int or value["profile_format"] < 1):
        raise InstallError("Registro de versión inválido en install.json.")


def validate_state(document: Any) -> dict[str, Any]:
    """Known fields are strict; unknown ones (from newer versions) are kept."""
    if not isinstance(document, dict) or document.get("schema") != 1 or type(document.get("schema")) is not int:
        raise InstallError("install.json tiene un formato desconocido.")
    versions = document.get("versions")
    if not isinstance(versions, dict) or not versions:
        raise InstallError("install.json no registra versiones.")
    for key, record in versions.items():
        if not _ID.match(key):
            raise InstallError("Identificador de versión inválido en install.json.")
        _version_record(record)
    if document.get("active") not in versions:
        raise InstallError("install.json no indica una versión activa registrada.")
    if document.get("previous") is not None and document["previous"] not in versions:
        raise InstallError("install.json indica una versión anterior desconocida.")
    if type(document.get("confirmed")) is not bool:
        raise InstallError("install.json no indica si la versión activa está confirmada.")
    sequences = document.get("channel_sequences")
    if (not isinstance(sequences, dict) or len(sequences) > MAX_CHANNELS
            or any(not isinstance(url, str) or not url or len(url) > 2048 or type(value) is not int or value < 1
                   for url, value in sequences.items())):
        raise InstallError("Secuencias de canal inválidas en install.json.")
    pending = document.get("pending")
    if pending is not None:
        if (not isinstance(pending, dict) or pending.get("operation") not in OPERATIONS
                or pending.get("phase") not in PHASES
                or not _ID.match(str(pending.get("from", ""))) or not _ID.match(str(pending.get("to", "")))):
            raise InstallError("Activación pendiente inválida en install.json.")
        helper = pending.get("helper")
        if helper is not None and (not isinstance(helper, dict) or type(helper.get("pid")) is not int
                                   or type(helper.get("created")) not in {int, float}):
            raise InstallError("Proceso de activación inválido en install.json.")
    last = document.get("last_activation")
    if last is not None and not isinstance(last, dict):
        raise InstallError("Último resultado de activación inválido en install.json.")
    return document


def read_state(install: Install) -> dict[str, Any]:
    return validate_state(_json(install.state_path, _MAX_STATE))


def _atomic_write(path: Path, data: bytes) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        # A reader without delete sharing (the launcher, a probe) may hold the
        # target for a moment; Windows then refuses the replace briefly.
        for attempt in range(40):
            try:
                os.replace(temporary, path)
                return
            except PermissionError:
                if attempt == 39:
                    raise
                time.sleep(0.05)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_state(install: Install, document: dict[str, Any]) -> None:
    validate_state(document)
    _atomic_write(install.state_path, json.dumps(document, indent=1, ensure_ascii=False).encode("utf-8"))


@contextmanager
def state_lock(install: Install) -> Iterator[None]:
    from core.file_lock import interprocess_file_lock

    install.updates.mkdir(exist_ok=True)
    with interprocess_file_lock(install.updates / "state.lock"):
        yield


def read_trial(install: Install) -> tuple[str, int]:
    try:
        data = strict_json(_read_regular(install.trial_path, 4096), 4096)
        if isinstance(data, dict) and _ID.match(str(data.get("target", ""))) and type(data.get("launches")) is int:
            return data["target"], max(0, data["launches"])
    except (InstallError, SignatureError, OSError):
        pass
    return "", 0


def clear_trial(install: Install) -> None:
    try:
        install.trial_path.unlink()
    except FileNotFoundError:
        pass


# ── Version folders ────────────────────────────────────────────────────────

def _member(value: Any) -> str:
    if (not isinstance(value, str) or not value or len(value) > 1024 or value.startswith("/")
            or "\\" in value or ":" in value or any(part in {"", ".", ".."} for part in value.split("/"))):
        raise InstallError(f"Ruta inválida en el manifiesto de la versión: {value!r}")
    return value


def _listing(folder: Path) -> dict[str, int]:
    """Every regular file below *folder*; links and junctions are rejected."""
    found: dict[str, int] = {}
    aliases: set[str] = set()
    stack = [folder]
    while stack:
        current = stack.pop()
        with os.scandir(current) as entries:
            for entry in entries:
                path = Path(entry.path)
                info = path.lstat()
                name = path.relative_to(folder).as_posix()
                if name.casefold() in aliases:
                    raise InstallError(f"Ruta duplicada o ambigua en la versión: {name}")
                aliases.add(name.casefold())
                if _is_link(info):
                    raise InstallError(f"La versión contiene un enlace: {name}")
                if stat.S_ISDIR(info.st_mode):
                    stack.append(path)
                elif stat.S_ISREG(info.st_mode):
                    found[name] = info.st_size
                else:
                    raise InstallError(f"La versión contiene un elemento no admitido: {name}")
    return found


def windows_length(path: Path) -> int:
    from core.runtime_descriptor import windows_path_length

    return windows_path_length(path)


def fits(root: Path, max_relative_path: int) -> bool:
    """``root\\v\\<id>\\<longest>`` must stay within MAX_PATH (A08.7 budget)."""
    return windows_length(Path(root)) + len("\\v\\00000000") <= WINDOWS_MAX_PATH - 1 - max_relative_path


def verify_version(folder: Path, staging_sha256: str | None = None, *, hashes: bool = True,
                   executables: bool = False) -> dict[str, Any]:
    """Static check of a version folder against its own manifests; runs nothing.

    The staging manifest binds the files it adds and the runtime inventory,
    which binds every runtime file. ``staging_sha256`` comes from the signed
    channel manifest for downloaded versions. ``hashes=False`` keeps every
    check except reading each file (sizes still match): the activation helper
    uses it on a folder Core fully verified moments before, while Iris is down.
    ``executables=True`` still hashes the programs (``.exe``), as for a
    rollback to a version that was fully verified when it was installed.
    """
    folder = Path(folder)
    raw = _read_regular(folder / STAGING_MANIFEST, _MAX_MANIFEST)
    digest = hashlib.sha256(raw).hexdigest()
    if staging_sha256 is not None and digest != staging_sha256:
        raise InstallError("La versión no coincide con la publicada en el canal.")
    try:
        manifest = strict_json(raw, _MAX_MANIFEST)
    except SignatureError as exc:
        raise InstallError("El manifiesto de la versión no es válido.") from exc
    try:
        if (manifest.get("schema") != 1 or manifest.get("component") != "iris-windows-staging"
                or manifest.get("platform") != "windows" or manifest.get("architecture") != "x86_64"):
            raise InstallError("La versión no es para Windows x64.")
        iris, hermes, runtime = manifest["iris"], manifest["hermes"], manifest["runtime"]
        if (not isinstance(iris.get("version"), str) or not _REVISION.match(str(iris.get("revision")))
                or not isinstance(hermes.get("version"), str) or not _REVISION.match(str(hermes.get("revision")))
                or not _SHA.match(str(runtime.get("inventory_sha256"))) or not _SHA.match(str(runtime.get("pin_sha256")))):
            raise InstallError("El manifiesto de la versión está incompleto.")
        longest = manifest["install"]["max_relative_path"]
        if type(longest) is not int or not 1 <= longest <= 4096:
            raise InstallError("El presupuesto de rutas de la versión no es válido.")
        profile = manifest.get("profile", {"format_version": 1})
        profile_format = profile.get("format_version") if isinstance(profile, dict) else None
        if type(profile_format) is not int or profile_format < 1:
            raise InstallError("La versión no declara un formato de perfil válido.")
        staged = {_member(item["path"]): (item["size"], item["sha256"]) for item in manifest["files"]}
    except (KeyError, TypeError, AttributeError) as exc:
        raise InstallError("El manifiesto de la versión está incompleto.") from exc
    inventory_raw = _read_regular(folder / INVENTORY, _MAX_MANIFEST)
    if hashlib.sha256(inventory_raw).hexdigest() != runtime["inventory_sha256"]:
        raise InstallError("El inventario del runtime no coincide con la versión.")
    try:
        inventory = strict_json(inventory_raw, _MAX_MANIFEST)
        files = {_member(item["path"]): (item["size"], item["sha256"]) for item in inventory["files"]}
    except (SignatureError, KeyError, TypeError) as exc:
        raise InstallError("El inventario del runtime no es válido.") from exc
    if set(files) & set(staged):
        raise InstallError("La versión declara el mismo archivo dos veces.")
    expected = {**files, **staged}
    present = _listing(folder)
    present.pop(STAGING_MANIFEST, None)
    present.pop(INVENTORY, None)
    if set(present) != set(expected):
        missing = sorted(set(expected) - set(present))[:5]
        extra = sorted(set(present) - set(expected))[:5]
        raise InstallError(f"La versión tiene archivos distintos a su manifiesto (faltan {missing}, sobran {extra}).")
    for name, (size, sha) in expected.items():
        read = hashes or (executables and name.casefold().endswith(".exe"))
        if present[name] != size or (read and sha256_file(folder / name) != sha):
            raise InstallError(f"Archivo alterado en la versión: {name}")
    if sha256_file(folder / PIN) != runtime["pin_sha256"]:
        raise InstallError("El pin del runtime no coincide con la versión.")
    descriptor = _json(folder / DESCRIPTOR, 64 * 1024)
    try:
        if (descriptor["schema_version"] != 2 or descriptor["mode"] != "packaged"
                or descriptor["iris"]["revision"] != iris["revision"]
                or descriptor["hermes"]["version"] != hermes["version"]
                or descriptor["hermes"]["revision"] != hermes["revision"]):
            raise InstallError("El descriptor de la versión no coincide con su manifiesto.")
    except (KeyError, TypeError) as exc:
        raise InstallError("El descriptor de la versión está incompleto.") from exc
    return {"id": digest[:8], "staging_sha256": digest, "iris": iris["version"], "iris_revision": iris["revision"],
            "hermes": hermes["version"], "hermes_revision": hermes["revision"], "profile_format": profile_format,
            "max_relative_path": longest, "files": len(expected) + 2}


def version_record(info: dict[str, Any], source: str) -> dict[str, Any]:
    return {"iris": info["iris"], "iris_revision": info["iris_revision"], "hermes": info["hermes"],
            "hermes_revision": info["hermes_revision"], "profile_format": info["profile_format"],
            "installed_at": now(), "source": source}


def _copy_listed(source: Path, target: Path) -> None:
    names = [STAGING_MANIFEST, INVENTORY]
    manifest = strict_json(_read_regular(source / STAGING_MANIFEST, _MAX_MANIFEST), _MAX_MANIFEST)
    inventory = strict_json(_read_regular(source / INVENTORY, _MAX_MANIFEST), _MAX_MANIFEST)
    names += [item["path"] for item in manifest["files"]] + [item["path"] for item in inventory["files"]]
    for name in names:
        destination = target / _member(name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, destination)


def create_install(staging: Path, root: Path) -> str:
    """Lay out a versioned installation from one verified staging folder.

    Used by the QA and, later, by the Windows installer (A08.9). The target
    folder must be new or empty; nothing outside it is touched.
    """
    staging, root = Path(staging).absolute(), Path(root).absolute()
    # Structure first; the copy is then hashed against the same manifests.
    info = verify_version(staging, hashes=False)
    if root.exists() and any(root.iterdir()):
        raise InstallError("La carpeta de instalación no está vacía.")
    if not fits(root, info["max_relative_path"]):
        raise InstallError("La carpeta de instalación es demasiado larga para Windows.")
    install = Install(root)
    target = install.folder(info["id"])
    target.mkdir(parents=True)
    _copy_listed(staging, target)
    verify_version(target, info["staging_sha256"])
    shutil.copyfile(target / EXECUTABLE, install.launcher)
    install.updates.mkdir()
    write_state(install, {
        "schema": 1, "active": info["id"], "previous": None, "confirmed": True, "pending": None,
        "channel_sequences": {}, "last_activation": None,
        "versions": {info["id"]: version_record(info, "installer")},
    })
    return info["id"]


# ── Profile backup for format or Hermes changes ────────────────────────────

def _in_scope(name: str, excluded: tuple[str, ...]) -> bool:
    lowered = name.casefold()
    return not any(lowered == item.rstrip("/") or lowered.startswith(item) for item in excluded)


def _profile_files(home: Path, excluded: tuple[str, ...]) -> dict[str, int]:
    if not home.is_dir():
        return {}
    files: dict[str, int] = {}
    stack = [home]
    while stack:
        current = stack.pop()
        with os.scandir(current) as entries:
            for entry in entries:
                path = Path(entry.path)
                name = path.relative_to(home).as_posix()
                info = path.lstat()
                if _is_link(info) or not _in_scope(name + ("/" if stat.S_ISDIR(info.st_mode) else ""), excluded):
                    continue
                if stat.S_ISDIR(info.st_mode):
                    stack.append(path)
                elif stat.S_ISREG(info.st_mode):
                    files[name] = info.st_size
    return files


def backup_profile(iris_home: Path, hermes_home: Path, label: str) -> Path:
    """Verified copy of the mutable profile before a format or Hermes change."""
    destination = Path(iris_home) / ".profile" / "backups" / f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{label}"
    destination.mkdir(parents=True)
    record: dict[str, Any] = {"schema": 1, "created_at": now(), "label": label, "files": {}}
    for scope, home, excluded in (("iris", Path(iris_home), _IRIS_BACKUP_EXCLUDED),
                                  ("hermes", Path(hermes_home), _HERMES_BACKUP_EXCLUDED)):
        entries = {}
        for name in sorted(_profile_files(home, excluded)):
            target = destination / scope / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(home / name, target)
            entries[name] = sha256_file(target)
            if sha256_file(home / name) != entries[name]:
                raise InstallError(f"El perfil cambió mientras se copiaba: {name}")
        record["files"][scope] = entries
    _atomic_write(destination / "backup.json", json.dumps(record, indent=1, ensure_ascii=False).encode("utf-8"))
    return destination


def restore_profile(iris_home: Path, hermes_home: Path, backup: Path) -> Path:
    """Put the profile back as it was; current in-scope files are moved aside, never deleted."""
    backup = Path(backup)
    record = _json(backup / "backup.json", 64 * 1024 * 1024)
    for scope, entries in record["files"].items():
        for name, sha in entries.items():
            if sha256_file(backup / scope / _member(name)) != sha:
                raise InstallError("La copia de seguridad del perfil está dañada; no se restauró.")
    replaced = backup / f"replaced-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    for scope, home, excluded in (("iris", Path(iris_home), _IRIS_BACKUP_EXCLUDED),
                                  ("hermes", Path(hermes_home), _HERMES_BACKUP_EXCLUDED)):
        for name in sorted(_profile_files(home, excluded)):
            aside = replaced / scope / name
            aside.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(home / name), str(aside))
        for name in record["files"].get(scope, {}):
            target = home / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(backup / scope / name, target)
    return replaced


# ── Startup reconciliation (Desktop controller) ────────────────────────────

def helper_alive(helper: dict[str, Any] | None) -> bool:
    if not helper:
        return False
    try:
        import psutil

        return abs(psutil.Process(helper["pid"]).create_time() - helper["created"]) < 0.01
    except Exception:
        return False


def _outcome(state: dict[str, Any], outcome: str, message: str, pending: dict[str, Any] | None) -> None:
    state["last_activation"] = {
        "operation": (pending or {}).get("operation", "upgrade"), "from": (pending or {}).get("from"),
        "to": (pending or {}).get("to"), "outcome": outcome, "message": message, "at": now()}


def describe(state: dict[str, Any], version: str | None) -> str:
    record = state["versions"].get(version or "", {})
    return f"Iris {record.get('iris', '?')}" if record else "otra versión"


def startup_check(install: Install, own: str) -> str | None:
    """Reconcile an interrupted activation; refuse a version that is not active.

    Returns a message when this copy must not start. A version reached by the
    launcher's fallback (the active one never confirmed after TRIAL_LIMIT
    launches) takes the activation back, after stopping what the failed
    version left running and restoring the profile backup taken for it.
    """
    with state_lock(install):
        state = read_state(install)
        pending = state.get("pending")
        if pending and helper_alive(pending.get("helper")):
            return None if own == state["active"] else "Iris se está actualizando. Espera a que vuelva a abrirse."
        if pending and pending["phase"] not in COMMITTED:
            state["pending"] = None
            _outcome(state, "interrupted", "La actualización se interrumpió antes de activarse; "
                     f"se mantiene {describe(state, state['active'])}.", pending)
            write_state(install, state)
        if own == state["active"]:
            return None
        target, launches = read_trial(install)
        if (own == state["previous"] and not state["confirmed"] and target == state["active"]
                and launches > TRIAL_LIMIT):
            failed = state["active"]
            pending = state.get("pending")
            state["active"], state["confirmed"] = own, True
            state["previous"] = (pending or {}).get("previous_before") if pending and pending.get("to") == failed else None
            state["pending"] = None
            message = (f"{describe(state, failed)} no logró arrancar tras {TRIAL_LIMIT} intentos; "
                       f"se volvió a {describe(state, own)}.")
            if pending and pending.get("backup") and pending.get("to") == failed:
                message += _restore_after_failed_version(install, failed, Path(pending["backup"]))
            reverted = pending if pending and pending.get("to") == failed else {"operation": "upgrade", "to": failed}
            _outcome(state, "reverted", message, reverted)
            clear_trial(install)
            write_state(install, state)
            sync_windows_entry(install)
            return None
    return (f"Esta copia ({describe(state, own)}) no es la versión activa de Iris "
            f"({describe(state, state['active'])}). Abre Iris desde su acceso directo.")


def _restore_after_failed_version(install: Install, failed: str, backup: Path) -> str:
    """Stop the failed version's own leftovers for this profile, then restore."""
    from core import iris_updates
    from core.config import get_iris_home
    from core.workspace_sync import get_hermes_home

    try:
        iris_updates._stop_version_services(install, failed)
        remaining = iris_updates._wait_gone(iris_updates.processes_of(install.folder(failed)), 30)
        for process in remaining:
            process.terminate()
        if iris_updates._wait_gone(remaining, 10):
            raise InstallError("la versión fallida sigue en uso")
        restore_profile(get_iris_home(), get_hermes_home(), backup)
        return " El perfil se restauró desde la copia previa."
    except Exception as exc:
        return f" No se pudo restaurar la copia del perfil ({exc}); está en {backup}."


def confirm_active(install: Install, own: str) -> bool:
    """Called once the active version reached ready: its trial is over."""
    with state_lock(install):
        state = read_state(install)
        pending = state.get("pending")
        if state["active"] != own or state["confirmed"] or (pending and pending["phase"] == "reverting"):
            return False  # a helper that gave up on this version is reverting it
        state["confirmed"] = True
        if pending and pending.get("to") == own and pending["phase"] == "switched":
            verb = "Se volvió a" if pending["operation"] == "rollback" else "Actualizado a"
            _outcome(state, "completed", f"{verb} {describe(state, own)}.", pending)
            state["pending"] = None
        clear_trial(install)
        write_state(install, state)
    sync_windows_entry(install)
    return True


WINDOWS_ENTRY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\IrisOS"


def sync_windows_entry(install: Install, registry: Any = None) -> str:
    """Show the version that actually runs in Windows' Installed apps (A08.9).

    The installer writes the entry; an upgrade only becomes the shown version
    once it confirms, and a rollback, a revert or the launcher's fallback show
    the version they went back to. Only the per-user entry whose
    InstallLocation is this installation is touched. Best effort: never raises.
    """
    if registry is None:
        if os.name != "nt":
            return "unsupported"
        import winreg as registry
    try:
        with registry.OpenKey(registry.HKEY_CURRENT_USER, WINDOWS_ENTRY, 0,
                              registry.KEY_READ | registry.KEY_SET_VALUE) as key:
            location = str(registry.QueryValueEx(key, "InstallLocation")[0]).strip().strip('"')
            if not location or os.path.normcase(os.path.realpath(location)) != os.path.normcase(
                    os.path.realpath(install.root)):
                return "other"
            state = read_state(install)
            version = state["versions"][state["active"]]["iris"]
            registry.SetValueEx(key, "DisplayVersion", 0, registry.REG_SZ, version)
            return version
    except (OSError, KeyError, InstallError):
        return "absent"
