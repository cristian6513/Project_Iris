"""Recoverable v1 migration of legacy poses; never writes the installation.

Only the animation manifest and animation files enter the backup inventory.
Credentials and other profile stores have independent owners and are not copied.
The user manifest replacement is the sole visibility commit; the durable journal
can recognize it after a crash before the migration version was recorded.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path

from core.animation_library import (
    AnimationLibrary,
    checked_bytes,
    read_library_manifest,
    validate_library_roots,
)
from core.animation_store import (
    ANIMATION_EXTENSIONS,
    MANAGED_FILENAME,
    MAX_MANIFEST_BYTES,
    AnimationStoreError,
    _basename,
    _discard,
    _json_document,
    _plain_file,
    _replace,
    _store_lock,
    _sync_directory,
    _write_temp,
    normalize_animation_name,
    read_animation_manifest,
)

MIGRATION_VERSION = 1


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _directory(path: Path) -> Path:
    path = path.absolute()
    if path.resolve() != path:
        raise AnimationStoreError("La migración no puede usar directorios enlazados.", 409)
    path.mkdir(parents=True, exist_ok=True)
    if path.resolve() != path:
        raise AnimationStoreError("Directorio de migración inválido.", 409)
    return path


def _raw_manifest(root: Path):
    try:
        return read_animation_manifest(root / "manifest.json")
    except FileNotFoundError:
        return None


def _raw_profile_format(user: Path):
    try:
        return read_animation_manifest(user.parent / ".profile" / "format.json")
    except FileNotFoundError:
        return None


def _atomic(root: Path, filename: str, payload: bytes):
    _directory(root)
    destination = _plain_file(root, filename)
    staged = _write_temp(root, payload)
    try:
        _replace(staged, destination)
        _sync_directory(root)
    finally:
        _discard(staged)


def _json_bytes(document: dict) -> bytes:
    try:
        raw = (json.dumps(document, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode()
    except RecursionError as exc:
        raise AnimationStoreError("Metadatos de migración demasiado complejos.", 409) from exc
    if len(raw) > MAX_MANIFEST_BYTES:
        raise AnimationStoreError("El registro de migración supera el límite permitido.", 409)
    return raw


def _journal(root: Path, state: dict):
    _atomic(root, "journal.json", _json_bytes(state))


def _read_journal(root: Path):
    try:
        state = _json_document(read_animation_manifest(root / "journal.json"))
    except FileNotFoundError:
        return None
    except (ValueError, RecursionError) as exc:
        raise AnimationStoreError(
            "Registro de migración inválido o demasiado complejo.", 409
        ) from exc
    if (
        type(state.get("version")) is not int
        or state.get("version") != MIGRATION_VERSION
        or not isinstance(state.get("phase"), str)
        or state.get("phase")
        not in {
            "backing-up",
            "prepared",
            "published",
            "complete",
            "restored",
        }
    ):
        raise AnimationStoreError("Registro de migración desconocido.", 409)

    def valid_digest(value):
        return isinstance(value, str) and bool(re.fullmatch(r"[a-f0-9]{64}", value))

    if not valid_digest(state.get("transaction")):
        raise AnimationStoreError("Transacción de migración inválida.", 409)
    for key in ("source_manifest_hash", "user_manifest_hash", "profile_format_hash"):
        if key not in state or (state[key] is not None and not valid_digest(state[key])):
            raise AnimationStoreError("Hash de migración inválido.", 409)
    for key in ("inventory", "user_inventory"):
        if not isinstance(state.get(key), dict):
            raise AnimationStoreError("Inventario de migración inválido.", 409)
        for filename, digest in state[key].items():
            _basename(filename)
            if not valid_digest(digest):
                raise AnimationStoreError("Hash de pose en migración inválido.", 409)
    return state


def _inventory(factory: Path, document: dict) -> dict[str, str]:
    names = set(document["clips"].values())
    if factory.exists():
        names.update(
            path.name for path in factory.iterdir() if MANAGED_FILENAME.fullmatch(path.name)
        )
    result = {}
    for filename in sorted(names):
        payload = checked_bytes(factory, filename)
        digest = _digest(payload)
        if MANAGED_FILENAME.fullmatch(filename) and digest != filename.rsplit(".", 2)[1]:
            raise AnimationStoreError("La versión histórica tiene un hash inválido.", 409)
        result[filename] = digest
    return result


def _factory_contract() -> dict:
    # This is the approved current inventory, not a guess about historical intent.
    try:
        from core.factory_animation_inventory import FACTORY_CLIPS, FACTORY_INVENTORY_SCHEMA
    except ImportError as exc:
        raise AnimationStoreError("Falta el inventario de fábrica incluido en Core.", 409) from exc
    if (
        type(FACTORY_INVENTORY_SCHEMA) is not int
        or FACTORY_INVENTORY_SCHEMA != 1
        or not isinstance(FACTORY_CLIPS, dict)
        or not FACTORY_CLIPS
    ):
        raise AnimationStoreError("Inventario de fábrica inválido.", 409)
    filenames = set()
    for name, item in FACTORY_CLIPS.items():
        if (
            not isinstance(name, str)
            or normalize_animation_name(name) != name
            or not isinstance(item, dict)
        ):
            raise AnimationStoreError("Mapping del inventario de fábrica inválido.", 409)
        filename = item.get("filename")
        if not isinstance(filename, str):
            raise AnimationStoreError("Nombre del inventario de fábrica inválido.", 409)
        _basename(filename)
        if (
            filename.rsplit(".", 1)[-1].lower() not in ANIMATION_EXTENSIONS
            or filename.casefold() in filenames
            or not isinstance(item.get("sha256"), str)
            or not re.fullmatch(r"[a-f0-9]{64}", item["sha256"])
        ):
            raise AnimationStoreError("Hash o archivo del inventario de fábrica inválido.", 409)
        filenames.add(filename.casefold())
    return copy.deepcopy(FACTORY_CLIPS)


def _verify_source(factory: Path, state: dict):
    raw = _raw_manifest(factory)
    if (_digest(raw) if raw is not None else None) != state["source_manifest_hash"]:
        raise AnimationStoreError(
            "La biblioteca anterior cambió durante la migración; se conservó.", 409
        )
    document = read_library_manifest(factory)
    if _inventory(factory, document) != state["inventory"]:
        raise AnimationStoreError(
            "Las poses anteriores cambiaron durante la migración; se conservaron.", 409
        )


def _backup_files(root: Path, factory: Path, user: Path, state: dict):
    legacy = _directory(root / "legacy")
    previous = _directory(root / "previous")
    _verify_source(factory, state)
    raw = _raw_manifest(factory)
    if raw is not None:
        _atomic(legacy, "manifest.json", raw)
    for filename, digest in state["inventory"].items():
        payload = checked_bytes(factory, filename)
        if _digest(payload) != digest:
            raise AnimationStoreError("La pose cambió antes del backup.", 409)
        _atomic(legacy, filename, payload)
    before = _raw_manifest(user)
    if (_digest(before) if before is not None else None) != state["user_manifest_hash"]:
        raise AnimationStoreError("El overlay cambió antes del backup.", 409)
    if before is not None:
        _atomic(previous, "manifest.json", before)
    profile_format = _raw_profile_format(user)
    if (_digest(profile_format) if profile_format is not None else None) != state[
        "profile_format_hash"
    ]:
        raise AnimationStoreError("El formato del perfil cambió antes del backup.", 409)
    if profile_format is not None:
        _atomic(previous, "profile-format.json", profile_format)
    # Back up every existing overlay version as well as its exact manifest.
    for filename, digest in state["user_inventory"].items():
        payload = checked_bytes(user, filename)
        if _digest(payload) != digest:
            raise AnimationStoreError("El overlay cambió antes del backup.", 409)
        _atomic(previous, filename, payload)
    _verify_backups(root, state)


def _verify_backups(root: Path, state: dict):
    try:
        profile_format = read_animation_manifest(root / "previous" / "profile-format.json")
    except FileNotFoundError:
        profile_format = None
    if (_digest(profile_format) if profile_format is not None else None) != state[
        "profile_format_hash"
    ]:
        raise AnimationStoreError("Backup del formato del perfil inválido.", 409)
    for subdir, hash_key, inventory_key in [
        ("legacy", "source_manifest_hash", "inventory"),
        ("previous", "user_manifest_hash", "user_inventory"),
    ]:
        backup = root / subdir
        raw = _raw_manifest(backup)
        if (_digest(raw) if raw is not None else None) != state[hash_key]:
            raise AnimationStoreError("Backup de manifiesto inválido.", 409)
        for filename, digest in state[inventory_key].items():
            if _digest(checked_bytes(backup, filename)) != digest:
                raise AnimationStoreError("Backup de pose inválido.", 409)


def _publish_file(user: Path, filename: str, payload: bytes):
    target = _plain_file(user, filename)
    if target.exists():
        if checked_bytes(user, filename) != payload:
            raise AnimationStoreError("Colisión de archivos de poses; se conservó el overlay.", 409)
        return
    _atomic(user, filename, payload)


def _migrate_locked(factory: Path, user: Path) -> dict:
    root = _directory(user.parent / "migrations" / "animations-v1")
    _directory(user)
    with _store_lock(user, 5.0):
        state = _read_journal(root)
        if state and state["phase"] in {"complete", "restored"}:
            if not (user / "manifest.json").exists() and (
                state["phase"] == "complete" or state["user_manifest_hash"] is not None
            ):
                raise AnimationStoreError(
                    "Falta el manifiesto personal; restaura el backup antes de escribir.", 409
                )
            read_library_manifest(user, user=True)
            return state
        if state is None:
            legacy = read_library_manifest(factory)
            previous = read_library_manifest(user, user=True)
            source_raw, previous_raw = _raw_manifest(factory), _raw_manifest(user)
            profile_format = _raw_profile_format(user)
            inventory = _inventory(factory, legacy)
            user_inventory = _inventory(user, previous)
            # The transaction id derives from the immutable plan. Resume cannot
            # accidentally attribute another import's manifest to this one.
            plan = {
                "source_manifest_hash": _digest(source_raw) if source_raw is not None else None,
                "user_manifest_hash": _digest(previous_raw) if previous_raw is not None else None,
                "profile_format_hash": _digest(profile_format)
                if profile_format is not None
                else None,
                "inventory": inventory,
                "user_inventory": user_inventory,
            }
            state = {
                "version": MIGRATION_VERSION,
                "phase": "backing-up",
                "transaction": _digest(_json_bytes(plan)),
                **plan,
            }
            _journal(root, state)
        current_raw = _raw_manifest(user)
        try:
            current = _json_document(current_raw) if current_raw is not None else {}
        except (ValueError, RecursionError) as exc:
            raise AnimationStoreError(
                "Overlay inválido o demasiado complejo para migrar.", 409
            ) from exc
        if current.get("_migration") == state["transaction"]:
            _verify_backups(root, state)
            state["phase"] = "complete"
            _journal(root, state)
            return state
        if state["phase"] == "published":
            raise AnimationStoreError("Falta el commit registrado de la migración.", 409)
        if state["phase"] == "backing-up":
            _backup_files(root, factory, user, state)
            state["phase"] = "prepared"
            _journal(root, state)
        _verify_backups(root, state)
        _verify_source(factory, state)
        if (_digest(current_raw) if current_raw is not None else None) != state[
            "user_manifest_hash"
        ]:
            raise AnimationStoreError("El overlay cambió durante la migración; se conservó.", 409)
        legacy = read_library_manifest(root / "legacy")
        previous = read_library_manifest(root / "previous", user=True)
        contract = _factory_contract()
        imported = {}
        classifications = {}
        for name, filename in legacy["clips"].items():
            if normalize_animation_name(name) != name or name in {
                "__proto__",
                "prototype",
                "constructor",
            }:
                raise AnimationStoreError("Identificador de pose anterior inválido.", 409)
            approved = contract.get(name, {})
            if (
                approved.get("filename") == filename
                and approved.get("sha256") == state["inventory"][filename]
            ):
                classifications[name] = "factory"
                continue
            payload = checked_bytes(root / "legacy", filename)
            extension = filename.rsplit(".", 1)[-1].lower()
            managed = f"{name}.{_digest(payload)}.{extension}"
            _publish_file(user, managed, payload)
            imported[name] = managed
            classifications[name] = (
                "existing-user" if name in previous["clips"] else "preserved-legacy"
            )
        for filename in state["inventory"]:
            if MANAGED_FILENAME.fullmatch(filename):
                _publish_file(user, filename, checked_bytes(root / "legacy", filename))
        metadata = {**legacy, "clips": imported} if imported else {"clips": {}}
        merged = AnimationLibrary._merge(metadata, previous)
        merged["_migration"] = state["transaction"]
        state["classifications"] = classifications
        _journal(root, state)
        # Recheck the read-only legacy inventory immediately before visibility.
        _verify_source(factory, state)
        _atomic(user, "manifest.json", _json_bytes(merged))
        state["phase"] = "published"
        _journal(root, state)
        state["phase"] = "complete"
        _journal(root, state)
        return state


def migrate_animation_profile(factory: Path, user: Path, *, _locked=False) -> dict:
    """Idempotent migration, serialized against profile and overlay writers."""
    factory, user = Path(factory).absolute(), Path(user).absolute()
    validate_library_roots(factory, user)
    if factory == user:
        raise AnimationStoreError("La migración requiere fábrica y perfil separados.", 409)
    try:
        if _locked:
            return _migrate_locked(factory, user)
        from core.profile_paths import ProfilePaths, profile_writer

        result = {}

        def prepare():
            result.update(_migrate_locked(factory, user))

        with profile_writer(
            "animation-migration", paths=ProfilePaths(user.parent), prepare=prepare
        ):
            return result
    except OSError as exc:
        raise AnimationStoreError(
            "Migración interrumpida; el backup y las poses anteriores se conservan.", 503
        ) from exc


def restore_animation_profile(user: Path) -> dict:
    """Restore the exact prior manifest/files; keep immutable newer versions."""
    from core.profile_paths import ProfilePaths, profile_writer

    user = Path(user).absolute()
    root = user.parent / "migrations" / "animations-v1"

    def prepare():
        # An interrupted rollback may already have restored an older format or
        # removed its marker. Check the backup before this writer temporarily
        # registers the current format, so retry cannot advance a damaged profile.
        with _store_lock(user, 5.0):
            state = _read_journal(root)
            if state is None:
                raise AnimationStoreError("No existe un backup de migración.", 409)
            _verify_backups(root, state)

    with profile_writer("animation-restore", paths=ProfilePaths(user.parent), prepare=prepare):
        with _store_lock(user, 5.0):
            state = _read_journal(root)
            if state is None:
                raise AnimationStoreError("No existe un backup de migración.", 409)
            _verify_backups(root, state)
            for filename in state["user_inventory"]:
                _publish_file(user, filename, checked_bytes(root / "previous", filename))
            previous = _raw_manifest(root / "previous")
            if previous is None:
                _plain_file(user, "manifest.json").unlink(missing_ok=True)
                _sync_directory(user)
            else:
                _atomic(user, "manifest.json", previous)
            profile_state = _directory(user.parent / ".profile")
            if state["profile_format_hash"] is None:
                _plain_file(profile_state, "format.json").unlink(missing_ok=True)
                _sync_directory(profile_state)
            else:
                raw = read_animation_manifest(root / "previous" / "profile-format.json")
                _atomic(profile_state, "format.json", raw)
            state["phase"] = "restored"
            _journal(root, state)
            return state
