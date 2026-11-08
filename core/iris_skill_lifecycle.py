"""Install first-party IRIS OS skills into Hermes' active profile.

The repository copy is source material; Hermes discovers local skills only
under ``<HERMES_HOME>/skills``.  This module performs an atomic, bounded sync
without overwriting a foreign skill that happens to use the same directory.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import shutil
import sys
import uuid
from pathlib import Path
from typing import Any

from core.workspace_sync import get_hermes_home

_SKILL_NAME = "iris-os"
APP_BUILDER_SKILL = "iris-app-builder"
APP_TEMPLATE_FILES = ("app.json", "ui/main.js", "ui/style.css", "ui/widget.js")
_MANAGED_MARKER = ".iris-managed.json"
_MAX_BUNDLE_BYTES = 1_048_576


def _source_candidates(name: str = _SKILL_NAME) -> list[Path]:
    candidates = [
        Path(__file__).resolve().parents[1] / ".hermes" / "skills" / name,
        Path(sys.prefix) / "share" / "iris-os" / "hermes-skills" / name,
    ]
    try:
        distribution = importlib.metadata.distribution("iris-os")
        candidates.append(Path(distribution.locate_file(
            f"share/iris-os/hermes-skills/{name}"
        )))
    except importlib.metadata.PackageNotFoundError:
        pass
    return candidates


def bundled_iris_skill_source() -> Path | None:
    """Return a complete bundled skill, preferring the editable checkout."""
    for candidate in _source_candidates():
        if (
            (candidate / "SKILL.md").is_file()
            and (candidate / "scripts" / "control_player.py").is_file()
            and (candidate / "scripts" / "app_action.py").is_file()
            and (candidate / "references" / "action-contracts.md").is_file()
        ):
            return candidate
    return None


def bundled_app_builder_source() -> Path | None:
    for candidate in _source_candidates(APP_BUILDER_SKILL):
        if (candidate / "SKILL.md").is_file() and all(
            (candidate / "template" / name).is_file() for name in APP_TEMPLATE_FILES
        ):
            return candidate
    return None


def _bundle_files(root: Path) -> list[Path]:
    files: list[Path] = []
    total = 0
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("La skill incluida no puede contener enlaces simbólicos.")
        if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        total += path.stat().st_size
        if total > _MAX_BUNDLE_BYTES:
            raise ValueError("La skill incluida supera el tamaño permitido.")
        files.append(path)
    return files


def _bundle_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in _bundle_files(root):
        relative = path.relative_to(root).as_posix()
        if relative == _MANAGED_MARKER:
            continue
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _is_first_party_destination(destination: Path, name: str) -> bool:
    if (destination / _MANAGED_MARKER).is_file():
        return True
    if name != _SKILL_NAME:
        return False
    try:
        skill_text = (destination / "SKILL.md").read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return False
    return "name: iris-os" in skill_text and "author: IRIS OS Contributors" in skill_text


def installed_iris_skill_dir(*, hermes_home: Path | None = None) -> Path:
    """Where Hermes runs IRIS' skill from; turn contexts name its scripts by this path."""
    home = Path(hermes_home) if hermes_home is not None else get_hermes_home()
    return home / "skills" / _SKILL_NAME


def sync_iris_skill_to_hermes(*, hermes_home: Path | None = None) -> dict[str, Any]:
    """Install or update IRIS' skill and return a UI-friendly result."""
    return _sync_skill(bundled_iris_skill_source(), installed_iris_skill_dir(hermes_home=hermes_home))


def sync_app_builder_to_hermes(*, hermes_home: Path | None = None) -> dict[str, Any]:
    home = Path(hermes_home) if hermes_home is not None else get_hermes_home()
    if not home.is_dir():
        return {"ok": False, "status": "unavailable", "detail": "El perfil de Hermes no existe."}
    return _sync_skill(bundled_app_builder_source(), home / "skills" / APP_BUILDER_SKILL)


def _sync_skill(source: Path | None, destination: Path) -> dict[str, Any]:
    name = destination.name
    if source is None:
        return {
            "ok": False,
            "status": "unavailable",
            "detail": "La skill incluida no está disponible.",
        }

    skills_root = destination.parent
    source_digest = _bundle_digest(source)

    if destination.exists() and not _is_first_party_destination(destination, name):
        return {
            "ok": False,
            "status": "conflict",
            "detail": f"Ya existe una skill {name} no administrada.",
        }
    if destination.is_dir() and _bundle_digest(destination) == source_digest:
        return {"ok": True, "status": "current", "detail": f"La skill {name} ya está actualizada."}

    skills_root.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    staged = skills_root / f".{name}.staged-{token}"
    backup = skills_root / f".{name}.backup-{token}"
    try:
        staged.mkdir()
        for source_file in _bundle_files(source):
            relative = source_file.relative_to(source)
            target = staged / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_file, target)
        (staged / _MANAGED_MARKER).write_text(
            json.dumps({"name": name, "digest": source_digest}, indent=2) + "\n",
            encoding="utf-8",
        )
        if destination.exists():
            destination.replace(backup)
        staged.replace(destination)
        if backup.exists():
            shutil.rmtree(backup)
    except Exception:
        if not destination.exists() and backup.exists():
            backup.replace(destination)
        raise
    finally:
        if staged.exists():
            shutil.rmtree(staged, ignore_errors=True)
        if backup.exists() and destination.exists():
            shutil.rmtree(backup, ignore_errors=True)

    return {"ok": True, "status": "installed", "detail": f"Skill {name} instalada para Hermes."}


__all__ = [
    "bundled_iris_skill_source", "installed_iris_skill_dir", "sync_iris_skill_to_hermes",
    "bundled_app_builder_source", "sync_app_builder_to_hermes",
]


if __name__ == "__main__":
    print(json.dumps(sync_iris_skill_to_hermes(), ensure_ascii=False))
