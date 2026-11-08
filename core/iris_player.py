"""Local-first music library for IRIS Player.

The library is deliberately synchronous.  WebSocket/HTTP adapters should call
its potentially blocking methods through ``asyncio.to_thread``.  Merely
constructing :class:`IrisPlayerLibrary` never scans the filesystem: a user must
first authorize one explicit directory and the caller must then request a
refresh.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unicodedata
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from core.config import get_iris_home
from core.file_lock import interprocess_file_lock

SUPPORTED_AUDIO_EXTENSIONS = frozenset(
    {
        ".aac",
        ".aif",
        ".aiff",
        ".alac",
        ".flac",
        ".m4a",
        ".mp3",
        ".oga",
        ".ogg",
        ".opus",
        ".wav",
        ".wma",
    }
)

_STATE_VERSION = 1


class IrisPlayerError(RuntimeError):
    """Base error for expected IRIS Player library failures."""


class LibraryNotConfiguredError(IrisPlayerError):
    """Raised when a library operation needs an authorized directory."""


class InvalidLibraryRootError(IrisPlayerError):
    """Raised when the selected directory cannot be used as a library."""


class TrackNotFoundError(IrisPlayerError):
    """Raised when a track ID is unknown or its file no longer exists."""


class UnsafeTrackPathError(IrisPlayerError):
    """Raised when a catalog entry escapes the authorized directory."""


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _empty_state() -> dict[str, Any]:
    return {
        "version": _STATE_VERSION,
        "library_root": "",
        "catalog": {},
        "user_state": {},
        "refreshed_at": None,
    }


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _clean_relative_path(value: object) -> str | None:
    """Return a portable, safe catalog path or ``None`` for untrusted data."""
    if not isinstance(value, str) or not value or "\\" in value:
        return None
    candidate = PurePosixPath(value)
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        return None
    return candidate.as_posix()


def _validated_record(track_id: str, value: object) -> dict[str, Any] | None:
    """Validate one persisted catalog record before exposing it to callers."""
    if not isinstance(value, dict) or value.get("id") != track_id:
        return None
    relative = _clean_relative_path(value.get("relative_path"))
    title = value.get("title")
    extension = value.get("extension")
    size = value.get("size_bytes")
    modified = value.get("modified_ns")
    if (
        relative is None
        or not isinstance(title, str)
        or not title.strip()
        or not isinstance(extension, str)
        or extension.casefold() not in SUPPORTED_AUDIO_EXTENSIONS
        or PurePosixPath(relative).suffix.casefold() != extension.casefold()
        or not isinstance(size, int)
        or isinstance(size, bool)
        or size < 0
        or not isinstance(modified, int)
        or isinstance(modified, bool)
        or modified < 0
    ):
        return None
    artist = value.get("artist")
    album = value.get("album")
    duration = value.get("duration_seconds")
    if artist is not None and not isinstance(artist, str):
        return None
    if album is not None and not isinstance(album, str):
        return None
    if duration is not None and (
        not isinstance(duration, (int, float)) or isinstance(duration, bool) or duration < 0
    ):
        return None
    return {
        "id": track_id,
        "relative_path": relative,
        "title": title.strip(),
        "artist": artist,
        "album": album,
        "duration_seconds": duration,
        "extension": extension.casefold(),
        "size_bytes": size,
        "modified_ns": modified,
    }


def _validated_user_state(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    if isinstance(value.get("favorite"), bool):
        result["favorite"] = value["favorite"]
    play_count = value.get("play_count")
    if isinstance(play_count, int) and not isinstance(play_count, bool) and play_count >= 0:
        result["play_count"] = play_count
    last_played = value.get("last_played")
    if isinstance(last_played, str) and len(last_played) <= 64:
        result["last_played"] = last_played
    return result


class IrisPlayerLibrary:
    """Persist and query one explicitly authorized local music directory."""

    def __init__(self, state_root: Path | str | None = None) -> None:
        base = Path(state_root) if state_root is not None else get_iris_home()
        self._state_root = base.expanduser().resolve(strict=False) / "player"
        self._state_path = self._state_root / "library.json"
        self._lock_path = self._state_root / "library.lock"

    @property
    def state_path(self) -> Path:
        """Path to the private state document, primarily for diagnostics."""
        return self._state_path

    def status(self) -> dict[str, Any]:
        """Return JSON-safe library status without disclosing an absolute path."""
        with interprocess_file_lock(self._lock_path):
            state = self._read_state_unlocked()
        root = self._configured_root(state)
        return {
            "configured": root is not None,
            "available": bool(root and root.is_dir()),
            "folder_name": root.name if root else None,
            "track_count": len(state["catalog"]),
            "favorite_count": sum(
                bool(state["user_state"].get(track_id, {}).get("favorite"))
                for track_id in state["catalog"]
            ),
            "refreshed_at": state["refreshed_at"],
            "supported_extensions": sorted(SUPPORTED_AUDIO_EXTENSIONS),
        }

    def configure_library(self, directory: Path | str) -> dict[str, Any]:
        """Authorize *directory* without scanning it.

        Separating authorization from :meth:`refresh` keeps filesystem access
        explicit and lets a UI explain the operation before indexing begins.
        """
        raw = Path(directory).expanduser()
        if not raw.is_absolute():
            raise InvalidLibraryRootError("La carpeta de música debe ser una ruta absoluta.")
        try:
            root = raw.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise InvalidLibraryRootError(
                "La carpeta de música no existe o no es accesible."
            ) from exc
        if not root.is_dir():
            raise InvalidLibraryRootError("La ruta seleccionada no es una carpeta.")

        with interprocess_file_lock(self._lock_path):
            state = self._read_state_unlocked()
            previous = self._configured_root(state)
            state["library_root"] = str(root)
            if previous != root:
                state["catalog"] = {}
                state["refreshed_at"] = None
            self._write_state_unlocked(state)
        return self.status()

    def refresh(self) -> dict[str, Any]:
        """Recursively rebuild the catalog and report additions/removals/changes."""
        with interprocess_file_lock(self._lock_path):
            state = self._read_state_unlocked()
            root = self._require_root(state)
            catalog, skipped = self._scan(root)
            old_catalog = state["catalog"]
            old_ids = set(old_catalog)
            new_ids = set(catalog)
            shared_ids = old_ids & new_ids
            changed = {
                track_id for track_id in shared_ids if old_catalog[track_id] != catalog[track_id]
            }
            state["catalog"] = catalog
            state["refreshed_at"] = _utc_now()
            self._write_state_unlocked(state)
            return {
                "added": len(new_ids - old_ids),
                "removed": len(old_ids - new_ids),
                "changed": len(changed),
                "unchanged": len(shared_ids - changed),
                "skipped": skipped,
                "total": len(catalog),
                "refreshed_at": state["refreshed_at"],
            }

    def list_tracks(
        self,
        *,
        query: str = "",
        favorites_only: bool = False,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List JSON-safe tracks, optionally filtered and paginated."""
        if offset < 0:
            raise ValueError("offset must be non-negative")
        if limit is not None and limit < 0:
            raise ValueError("limit must be non-negative or None")

        with interprocess_file_lock(self._lock_path):
            state = self._read_state_unlocked()
        needle = " ".join(query.casefold().split())
        tracks = []
        for track_id, record in state["catalog"].items():
            user_state = state["user_state"].get(track_id, {})
            if favorites_only and not user_state.get("favorite", False):
                continue
            public = self._public_track(record, user_state)
            searchable = " ".join(
                str(public.get(key) or "") for key in ("title", "artist", "album", "relative_path")
            ).casefold()
            if needle and needle not in searchable:
                continue
            tracks.append(public)

        tracks.sort(key=lambda item: (str(item["title"]).casefold(), item["relative_path"]))
        end = None if limit is None else offset + limit
        return tracks[offset:end]

    def set_favorite(self, track_id: str, favorite: bool) -> dict[str, Any]:
        """Persist a track's favorite flag and return its public metadata."""
        with interprocess_file_lock(self._lock_path):
            state = self._read_state_unlocked()
            record = self._known_track(state, track_id)
            user_state = state["user_state"].setdefault(track_id, {})
            user_state["favorite"] = bool(favorite)
            self._write_state_unlocked(state)
            return self._public_track(record, user_state)

    def record_play(self, track_id: str) -> dict[str, Any]:
        """Increment play count and persist the UTC time of the play."""
        with interprocess_file_lock(self._lock_path):
            state = self._read_state_unlocked()
            record = self._known_track(state, track_id)
            user_state = state["user_state"].setdefault(track_id, {})
            count = user_state.get("play_count", 0)
            user_state["play_count"] = count + 1 if isinstance(count, int) and count >= 0 else 1
            user_state["last_played"] = _utc_now()
            self._write_state_unlocked(state)
            return self._public_track(record, user_state)

    def track_label(self, track_id: str) -> str | None:
        """«Title — Artist» of a known track, for a confirmation; never its path."""
        with interprocess_file_lock(self._lock_path):
            state = self._read_state_unlocked()
        record = state["catalog"].get(track_id)
        if not isinstance(record, dict):
            return None
        title = " ".join(str(record.get("title") or "").split())
        artist = " ".join(str(record.get("artist") or "").split())
        if not title:
            return None
        return f"{title} — {artist}" if artist else title

    def resolve_track(self, track_id: str) -> Path:
        """Resolve *track_id* to an existing safe file below the authorized root."""
        with interprocess_file_lock(self._lock_path):
            state = self._read_state_unlocked()
        root = self._require_root(state)
        record = self._known_track(state, track_id)
        relative = _clean_relative_path(record.get("relative_path"))
        if relative is None:
            raise UnsafeTrackPathError("La ruta de la canción no es segura.")
        return self._resolve_safe_track(root, relative)

    def _scan(self, root: Path) -> tuple[dict[str, dict[str, Any]], int]:
        catalog: dict[str, dict[str, Any]] = {}
        skipped = 0

        def on_error(_error: OSError) -> None:
            nonlocal skipped
            skipped += 1

        for directory, directory_names, file_names in os.walk(
            root,
            topdown=True,
            followlinks=False,
            onerror=on_error,
        ):
            parent = Path(directory)
            safe_directories: list[str] = []
            for name in directory_names:
                candidate = parent / name
                try:
                    resolved = candidate.resolve(strict=True)
                    if (
                        candidate.is_symlink()
                        or not resolved.is_dir()
                        or not _is_relative_to(resolved, root)
                    ):
                        skipped += 1
                        continue
                except (OSError, RuntimeError):
                    skipped += 1
                    continue
                safe_directories.append(name)
            directory_names[:] = safe_directories

            for name in file_names:
                candidate = parent / name
                if candidate.suffix.casefold() not in SUPPORTED_AUDIO_EXTENSIONS:
                    continue
                try:
                    relative = candidate.relative_to(root).as_posix()
                    resolved = self._resolve_safe_track(root, relative)
                    stat = resolved.stat()
                except (OSError, RuntimeError, UnsafeTrackPathError, TrackNotFoundError):
                    skipped += 1
                    continue
                track_id = self._track_id(root, relative)
                catalog[track_id] = {
                    "id": track_id,
                    "relative_path": relative,
                    "title": candidate.stem.replace("_", " ").strip() or candidate.stem,
                    "artist": None,
                    "album": None,
                    "duration_seconds": None,
                    "extension": candidate.suffix.casefold(),
                    "size_bytes": stat.st_size,
                    "modified_ns": stat.st_mtime_ns,
                }
        return catalog, skipped

    @staticmethod
    def _track_id(root: Path, relative: str) -> str:
        root_key = unicodedata.normalize("NFC", os.path.normcase(str(root)))
        relative_key = unicodedata.normalize("NFC", os.path.normcase(relative))
        digest = hashlib.sha256(f"{root_key}\0{relative_key}".encode()).hexdigest()
        return f"trk_{digest[:32]}"

    @staticmethod
    def _public_track(record: dict[str, Any], user_state: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": record["id"],
            "relative_path": record["relative_path"],
            "title": record["title"],
            "artist": record.get("artist"),
            "album": record.get("album"),
            "duration_seconds": record.get("duration_seconds"),
            "extension": record["extension"],
            "size_bytes": record["size_bytes"],
            "favorite": bool(user_state.get("favorite", False)),
            "play_count": max(user_state.get("play_count", 0), 0)
            if isinstance(user_state.get("play_count", 0), int)
            else 0,
            "last_played": user_state.get("last_played")
            if isinstance(user_state.get("last_played"), str)
            else None,
        }

    @staticmethod
    def _known_track(state: dict[str, Any], track_id: str) -> dict[str, Any]:
        record = state["catalog"].get(track_id)
        if not isinstance(record, dict):
            raise TrackNotFoundError("La canción solicitada no está en la biblioteca.")
        return record

    @staticmethod
    def _configured_root(state: dict[str, Any]) -> Path | None:
        value = state.get("library_root")
        if not isinstance(value, str) or not value:
            return None
        candidate = Path(value)
        if not candidate.is_absolute():
            return None
        try:
            resolved = candidate.resolve(strict=False)
        except (OSError, RuntimeError):
            return None
        # configure_library stores a canonical path. If it resolves somewhere
        # else later, one of its components was replaced by a link and the
        # former authorization must not silently follow it.
        if os.path.normcase(str(resolved)) != os.path.normcase(str(candidate)):
            return None
        return resolved

    def _require_root(self, state: dict[str, Any]) -> Path:
        root = self._configured_root(state)
        if root is None:
            raise LibraryNotConfiguredError("Primero selecciona una carpeta de música.")
        if not root.is_dir():
            raise InvalidLibraryRootError("La carpeta de música ya no está disponible.")
        return root

    @staticmethod
    def _resolve_safe_track(root: Path, relative: str) -> Path:
        clean = _clean_relative_path(relative)
        if clean is None:
            raise UnsafeTrackPathError("La ruta de la canción no es segura.")
        candidate = root.joinpath(*PurePosixPath(clean).parts)

        current = root
        for part in PurePosixPath(clean).parts:
            current /= part
            if current.is_symlink():
                raise UnsafeTrackPathError("Los enlaces simbólicos no se reproducen.")
        try:
            resolved = candidate.resolve(strict=True)
        except (FileNotFoundError, OSError, RuntimeError) as exc:
            raise TrackNotFoundError("El archivo de la canción ya no existe.") from exc
        if not _is_relative_to(resolved, root):
            raise UnsafeTrackPathError("La canción está fuera de la carpeta autorizada.")
        if not resolved.is_file() or resolved.suffix.casefold() not in SUPPORTED_AUDIO_EXTENSIONS:
            raise TrackNotFoundError("El archivo ya no es una canción compatible.")
        return resolved

    def _read_state_unlocked(self) -> dict[str, Any]:
        try:
            document = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
            return _empty_state()
        if not isinstance(document, dict) or document.get("version") != _STATE_VERSION:
            return _empty_state()

        state = _empty_state()
        root = document.get("library_root")
        if isinstance(root, str) and Path(root).is_absolute():
            state["library_root"] = root
        state["refreshed_at"] = (
            document.get("refreshed_at") if isinstance(document.get("refreshed_at"), str) else None
        )

        catalog = document.get("catalog")
        if isinstance(catalog, dict):
            for track_id, record in catalog.items():
                if not isinstance(track_id, str) or not track_id.startswith("trk_"):
                    continue
                validated = _validated_record(track_id, record)
                if validated is not None:
                    state["catalog"][track_id] = validated
        user_state = document.get("user_state")
        if isinstance(user_state, dict):
            state["user_state"] = {
                track_id: _validated_user_state(value)
                for track_id, value in user_state.items()
                if isinstance(track_id, str) and isinstance(value, dict)
            }
        return state

    def _write_state_unlocked(self, state: dict[str, Any]) -> None:
        self._state_root.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self._state_root,
                prefix=".library.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                json.dump(state, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self._state_path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
