"""One read-only factory library and a confined, immutable-file user overlay."""

from __future__ import annotations

import copy
import hashlib
import io
import json
from dataclasses import replace
from pathlib import Path
from urllib.parse import quote

from core.animation_store import (
    ANIMATION_EXTENSIONS,
    MANAGED_FILENAME,
    MAX_UPLOAD_BYTES,
    AnimationStore,
    AnimationStoreError,
    _basename,
    _json_document,
    _plain_file,
    normalize_animation_name,
    read_animation_manifest,
)
from core.asset_uploads import AssetUploadError, read_bounded, validate_asset

_MAP_FIELDS = frozenset(
    {
        "pose_map",
        "activity_clips",
        "facial_profiles",
        "_source_mapping",
        "variants",
        "variant_weights",
    }
)


def validate_library_roots(factory: Path, user: Path) -> None:
    """Reject aliases/overlap before any profile directory or lock is created."""
    factory, user = Path(factory).absolute(), Path(user).absolute()
    if factory.resolve(strict=False) != factory or user.resolve(strict=False) != user:
        raise AnimationStoreError("La biblioteca no puede usar directorios enlazados.", 409)
    if factory.is_relative_to(user) or user.is_relative_to(factory):
        raise AnimationStoreError("La fábrica y el perfil deben estar separados.", 409)


def _copy_metadata(value):
    try:
        return copy.deepcopy(value)
    except RecursionError as exc:
        raise AnimationStoreError("Metadatos de animación demasiado complejos.", 409) from exc


def checked_bytes(root: Path, filename: str) -> bytes:
    """Bound reads and reject links, including a linked directory ancestor."""
    path = _plain_file(root.absolute(), filename)
    with path.open("rb") as handle:
        content = handle.read(MAX_UPLOAD_BYTES + 1)
    if not content or len(content) > MAX_UPLOAD_BYTES:
        raise AnimationStoreError("Animación vacía o demasiado grande.", 409)
    return content


def read_library_manifest(root: Path, *, user: bool = False, timeout=None) -> dict:
    root = root.absolute()
    try:
        raw = read_animation_manifest(root / "manifest.json", timeout=timeout)
    except FileNotFoundError:
        return {"clips": {}}
    try:
        data = _json_document(raw)
        if type(data.get("schema_version", 1)) is not int or data.get("schema_version", 1) != 1:
            raise ValueError("Unsupported manifest schema")
        if type(data.get("revision", 0)) is not int or data.get("revision", 0) < 0:
            raise ValueError("Invalid revision")
        if not isinstance(data.get("clips"), dict):
            raise ValueError("Invalid clips")
        for name, filename in data["clips"].items():
            if not isinstance(name, str) or not name or not isinstance(filename, str):
                raise ValueError("Invalid clip entry")
            _basename(filename)
            extension = filename.rsplit(".", 1)[-1].lower()
            if extension not in ANIMATION_EXTENSIONS:
                raise ValueError("Unsupported clip format")
            path = _plain_file(root, filename)
            if not path.is_file() or path.stat().st_size == 0:
                raise ValueError("Missing registered clip")
            if user:
                if not MANAGED_FILENAME.fullmatch(filename):
                    raise ValueError("Overlay files must be immutable versions")
                if (
                    hashlib.sha256(checked_bytes(root, filename)).hexdigest()
                    != filename.rsplit(".", 2)[1]
                ):
                    raise ValueError("Overlay digest mismatch")
        return data
    except (ValueError, TypeError, RecursionError, AnimationStoreError) as exc:
        raise AnimationStoreError(
            "Biblioteca de animaciones inválida; se conserva la versión anterior.", 409
        ) from exc


class AnimationLibrary:
    def __init__(self, factory: Path, user: Path):
        self.factory = Path(factory).absolute()
        self.user = Path(user).absolute()
        if self.factory == self.user:
            raise AnimationStoreError("La fábrica y el perfil deben estar separados.", 409)

    def _documents(self, *, timeout=None):
        validate_library_roots(self.factory, self.user)
        factory = read_library_manifest(self.factory, timeout=timeout)
        user = read_library_manifest(self.user, user=True, timeout=timeout)
        # Once migration published a manifest, its disappearance is an outage,
        # not permission to conceal the user's overrides behind factory clips.
        if not (self.user / "manifest.json").exists():
            journal = self.user.parent / "migrations" / "animations-v1" / "journal.json"
            try:
                state = _json_document(read_animation_manifest(journal, timeout=timeout))
            except FileNotFoundError:
                state = {}
            except (ValueError, RecursionError) as exc:
                raise AnimationStoreError(
                    "Registro de migración inválido o demasiado complejo.", 409
                ) from exc
            if state.get("phase") in {"complete", "published"} or (
                state.get("phase") in {"backing-up", "prepared", "restored"}
                and state.get("user_manifest_hash") is not None
            ):
                raise AnimationStoreError("Falta el manifiesto personal de animaciones.", 409)
        return factory, user

    @staticmethod
    def _merge(factory: dict, user: dict) -> dict:
        merged = _copy_metadata(factory)
        for key, value in user.items():
            if key in {"clips", "schema_version", "revision", "_migration"}:
                continue
            if key in _MAP_FIELDS and isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key].update(_copy_metadata(value))
            else:
                merged[key] = _copy_metadata(value)
        merged["clips"] = {**factory["clips"], **user["clips"]}
        AnimationLibrary._replaced_clips_play_alone(merged, factory, user)
        merged["schema_version"] = 1
        merged["revision"] = user.get("revision", 0)
        return merged

    @staticmethod
    def _replaced_clips_play_alone(merged: dict, factory: dict, user: dict) -> None:
        """A factory gesture the person replaced plays their clip, and only theirs.

        Otherwise the factory's other variants kept playing it just now and then, and the
        factory clip's timed facial ``moments`` fired out of step with the new clip (QA23).
        Variants or a facial profile the person set for that name themselves still win.
        """
        own_variants = user.get("variants") if isinstance(user.get("variants"), dict) else {}
        own_faces = user.get("facial_profiles") if isinstance(user.get("facial_profiles"), dict) else {}
        variants = merged.get("variants") if isinstance(merged.get("variants"), dict) else {}
        faces = merged.get("facial_profiles") if isinstance(merged.get("facial_profiles"), dict) else {}
        for name in user["clips"]:
            if name not in factory["clips"]:
                continue
            if name in variants and name not in own_variants:
                del variants[name]
            profile = faces.get(name)
            if isinstance(profile, dict) and "moments" in profile and name not in own_faces:
                faces[name] = {key: value for key, value in profile.items() if key != "moments"}

    def merged_manifest(self, *, timeout=None) -> dict:
        return self._merge(*self._documents(timeout=timeout))

    def snapshot(self) -> dict:
        factory, user = self._documents()
        manifest = self._merge(factory, user)
        resources = {}
        for name, filename in manifest["clips"].items():
            origin = "user" if name in user["clips"] else "factory"
            root = self.user if origin == "user" else self.factory
            payload = checked_bytes(root, filename)
            if MANAGED_FILENAME.fullmatch(filename):
                other = self.factory if origin == "user" else self.user
                candidate = _plain_file(other, filename)
                if candidate.exists() and checked_bytes(other, filename) != payload:
                    raise AnimationStoreError("Colisión de versiones de animación.", 409)
            digest = hashlib.sha256(payload).hexdigest()
            resources[name] = {
                "filename": filename,
                "format": filename.rsplit(".", 1)[-1].lower(),
                "revision": digest,
                "source": origin,
                "url": f"/face/animations/{quote(filename)}?v={digest}",
            }
        snapshot = {"manifest": manifest, "resources": resources}
        raw = json.dumps(snapshot, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
        return {**snapshot, "revision": hashlib.sha256(raw).hexdigest()}

    def resolve_file(self, filename: str) -> Path:
        """Old managed URLs survive replacement, but never enter the catalog."""
        validate_library_roots(self.factory, self.user)
        _basename(filename)
        if MANAGED_FILENAME.fullmatch(filename):
            candidate = _plain_file(self.user, filename)
            if candidate.exists():
                payload = checked_bytes(self.user, filename)
                if hashlib.sha256(payload).hexdigest() != filename.rsplit(".", 2)[1]:
                    raise AnimationStoreError("Versión personal con hash inválido.", 409)
                factory_candidate = _plain_file(self.factory, filename)
                if factory_candidate.exists() and checked_bytes(self.factory, filename) != payload:
                    raise AnimationStoreError("Colisión de versiones de animación.", 409)
                return candidate
        factory = read_library_manifest(self.factory)
        if filename in factory["clips"].values():
            return _plain_file(self.factory, filename)
        # Pre-A08.2 published versions can still be referenced by live clients.
        if MANAGED_FILENAME.fullmatch(filename):
            candidate = _plain_file(self.factory, filename)
            if candidate.exists():
                if (
                    hashlib.sha256(checked_bytes(self.factory, filename)).hexdigest()
                    != filename.rsplit(".", 2)[1]
                ):
                    raise AnimationStoreError("Versión de animación con hash inválido.", 409)
                return candidate
        raise FileNotFoundError(filename)

    def save_upload(self, source, filename: str, name: str):
        # Invalid requests must not initialize/migrate a profile as a side effect.
        if normalize_animation_name(name) != name:
            raise AnimationStoreError("Identificador de pose inválido.")
        _basename(filename)
        extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if extension not in ANIMATION_EXTENSIONS:
            raise AnimationStoreError("Formato de animación no soportado.")
        try:
            payload = read_bounded(source, MAX_UPLOAD_BYTES)
            validate_asset(payload, extension)
        except AssetUploadError as exc:
            raise AnimationStoreError(str(exc), exc.status_code) from exc
        except OSError as exc:
            raise AnimationStoreError(
                "No se pudo leer la animación; se conservó la anterior.", 503
            ) from exc
        validate_library_roots(self.factory, self.user)
        from core.profile_migration import migrate_animation_profile
        from core.profile_paths import (
            ProfileBusyError,
            ProfilePathError,
            ProfilePaths,
            profile_writer,
        )

        saved = None
        try:
            with profile_writer(
                "animations",
                paths=ProfilePaths(self.user.parent),
                prepare=lambda: migrate_animation_profile(self.factory, self.user, _locked=True),
            ):
                saved = AnimationStore(self.user).save_upload(io.BytesIO(payload), filename, name)
            return saved
        except (ProfileBusyError, ProfilePathError) as exc:
            if saved is not None:
                return replace(
                    saved,
                    warning="Animación guardada; el cierre del control del perfil sigue pendiente.",
                )
            raise AnimationStoreError(str(exc), getattr(exc, "status_code", 409)) from exc
        except OSError as exc:
            if saved is not None:
                return replace(
                    saved,
                    warning="Animación guardada; el cierre del control del perfil sigue pendiente.",
                )
            raise AnimationStoreError(
                "No se pudo preparar el perfil; se conservó la animación anterior.", 503
            ) from exc


def get_animation_library(factory: Path | None = None) -> AnimationLibrary:
    from core.profile_paths import get_profile_paths
    from core.runtime_descriptor import get_runtime

    return AnimationLibrary(
        factory if factory is not None else get_runtime().assets_root / "animations",
        get_profile_paths().animations,
    )
