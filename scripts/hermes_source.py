"""Export only public runtime files from a reviewed, hashed Hermes Git archive.

This is deliberately not an upstream wheel build or a copy of an installed
environment. Git provenance and the upstream manifests are checked before any
runtime file is written; callers supply a new Hermes source destination.

The exported bytes are upstream's, except for the reviewed IRIS patches listed in
the pin (``source_patches``). Each one names the exact upstream file hash it was
written against and the hash of the result, so an upstream change to that file
stops the build until the patch is reviewed again or dropped.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shutil
import stat
import tarfile
import tempfile
from collections.abc import Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

CODE_DIRECTORIES = frozenset({
    "agent", "tools", "hermes_cli", "gateway", "tui_gateway", "cron",
    "acp_adapter", "plugins", "providers",
    # Hermes 0.21.6: its package manager and platform layer.
    "pm", "hermes_platform",
    # Hermes 0.23.x: the agent package, session storage (the old hermes_state_* modules),
    # session rules and utils (a module until 0.21).
    "hermes_agent", "persistence", "session_rules", "utils",
})
ASSET_DIRECTORIES = frozenset({
    "skills", "optional-skills", "optional-mcps", "plugin-catalog", "locales", "assets",
})
ROOT_FILES = frozenset({"LICENSE", "README.md", "pyproject.toml", "uv.lock"})
EXCLUDED_COMPONENTS = frozenset({
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".cache", ".pytest_cache",
})
REQUIRED_SOURCE_HASHES = frozenset({"LICENSE", "pyproject.toml", "uv.lock"})
GENERATED_FILES = frozenset({"runtime-pin.json", "iris_cli.py", "install-stamp.json"})
DEFAULT_ARCHIVE_PREFIX = "hermes"
# Who replaces the tree: Iris, never ``hermes update`` (upstream UPDATE_MECHANISMS).
STAMP_UPDATE_MECHANISMS = frozenset({"external"})
WINDOWS_RESERVED_NAMES = frozenset({
    "con", "prn", "aux", "nul", *{f"com{number}" for number in range(1, 10)},
    *{f"lpt{number}" for number in range(1, 10)},
})

_PATCH_ID = re.compile(r"[a-z0-9][a-z0-9-]{2,63}")
_HASH = re.compile(r"[0-9a-f]{64}")

CLI_LAUNCHER = '''"""Portable IRIS entry point into the pinned Hermes source."""
import runpy
import sys
from pathlib import Path

if __name__ == "__main__":
    source_root = str(Path(__file__).resolve().parent)
    if source_root not in sys.path:
        sys.path.insert(0, source_root)
    runpy.run_module("hermes_cli.main", run_name="__main__")
'''


class HermesSourceError(ValueError):
    """The source input or destination does not satisfy the reviewed pin."""


def _stream_sha256(handle) -> str:
    digest = hashlib.sha256()
    handle.seek(0)
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


@contextmanager
def _hashed_archive(path: Path, expected_hash: str):
    # Retain the same open file through verification and extraction, so a path
    # replacement cannot switch in a different source after the checksum gate.
    with path.open("rb") as handle:
        if _stream_sha256(handle) != expected_hash:
            raise HermesSourceError("Hermes source archive SHA256 does not match the reviewed pin.")
        handle.seek(0)
        with tarfile.open(fileobj=handle, mode="r:*") as archive:
            yield archive, handle


def _archive_prefix(pin: Mapping[str, Any]) -> str:
    """The archive's single top directory: ``hermes`` (``git archive --prefix``) or GitHub's ``<repo>-<sha>``."""
    prefix = pin.get("archive_prefix", DEFAULT_ARCHIVE_PREFIX)
    if not isinstance(prefix, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", prefix):
        raise HermesSourceError("Invalid Hermes archive prefix.")
    return prefix


def _stamp_policy(pin: Mapping[str, Any]) -> dict[str, str] | None:
    """The reviewed install-stamp fields, when the pin asks for a stamp (Hermes 0.21.6+)."""
    stamp = pin.get("install_stamp")
    if stamp is None:
        return None
    if (not isinstance(stamp, Mapping) or set(stamp) != {"distribution", "update_mechanism"}
            or not isinstance(stamp["distribution"], str)
            or not re.fullmatch(r"[a-z][a-z0-9-]{1,31}", stamp["distribution"])
            or stamp["update_mechanism"] not in STAMP_UPDATE_MECHANISMS):
        raise HermesSourceError("Invalid Hermes install stamp policy.")
    return dict(stamp)


def install_stamp(version: str, revision: str, commit_date: int, policy: Mapping[str, str]) -> dict[str, Any]:
    """Hermes' schema-2 ``install-stamp.json`` (``scripts/write_install_stamp.py`` upstream).

    The sealed tree's version comes from it, and its steward refuses ``hermes update``.
    Deterministic: the build time is the commit time.
    """
    return {
        "schemaVersion": 2,
        "commit": revision,
        "commitDate": commit_date,
        "branch": None,
        "builtAt": datetime.fromtimestamp(commit_date, timezone.utc).isoformat(),
        "dirty": False,
        "source": "iris",
        "distribution": policy["distribution"],
        "updateMechanism": policy["update_mechanism"],
        "baseVersion": version,
        "displayVersion": version,
        "distance": 0,
        "payload": "bootstrap",
        "tag": None,
    }


def _validate_pin(pin: Mapping[str, Any]) -> tuple[str, str, str, dict[str, str]]:
    version, revision = pin.get("version"), pin.get("revision")
    archive_hash, source_hashes = pin.get("source_archive_sha256"), pin.get("source_hashes")
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise HermesSourceError("Hermes version must be an exact semantic version.")
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise HermesSourceError("Hermes revision must be an exact lowercase Git commit.")
    if not isinstance(archive_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", archive_hash):
        raise HermesSourceError("A reviewed source_archive_sha256 is required.")
    if not isinstance(source_hashes, dict) or not REQUIRED_SOURCE_HASHES <= source_hashes.keys():
        raise HermesSourceError("Reviewed hashes for LICENSE, pyproject.toml and uv.lock are required.")
    for name, value in source_hashes.items():
        if name not in ROOT_FILES | {"setup.py", "hermes_cli/__init__.py"}:
            raise HermesSourceError(f"Unexpected source manifest hash: {name!r}.")
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
            raise HermesSourceError(f"Invalid source hash for {name!r}.")
    return version, revision, archive_hash, dict(source_hashes)


def _validate_patches(pin: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """The pin's reviewed IRIS patches, keyed by the Hermes code file each one changes.

    A patch is ``{"id", "reason", "files"}``; each file entry is ``{"path", "sha256",
    "patched_sha256", "replace"}`` with ``replace`` a list of exact ``{"old", "new"}``
    text blocks. One patch per file keeps both hashes unambiguous.
    """
    patches = pin.get("source_patches", [])
    if not isinstance(patches, list):
        raise HermesSourceError("Hermes source_patches must be a list.")
    by_file: dict[str, dict[str, Any]] = {}
    ids: set[str] = set()
    for patch in patches:
        if not isinstance(patch, Mapping) or set(patch) != {"id", "reason", "files"}:
            raise HermesSourceError("Invalid Hermes source patch.")
        patch_id = patch["id"]
        if not isinstance(patch_id, str) or not _PATCH_ID.fullmatch(patch_id) or patch_id in ids:
            raise HermesSourceError(f"Invalid or duplicate Hermes source patch id: {patch_id!r}.")
        ids.add(patch_id)
        if not isinstance(patch["reason"], str) or not patch["reason"].strip():
            raise HermesSourceError(f"Hermes source patch {patch_id} needs a reason.")
        files = patch["files"]
        if not isinstance(files, list) or not files:
            raise HermesSourceError(f"Hermes source patch {patch_id} changes no file.")
        for item in files:
            if not isinstance(item, Mapping) or set(item) != {"path", "sha256", "patched_sha256", "replace"}:
                raise HermesSourceError(f"Invalid file entry in Hermes source patch {patch_id}.")
            path = item["path"]
            parts = PurePosixPath(path).parts if isinstance(path, str) else ()
            if (not parts or str(PurePosixPath(*parts)) != path or not path.endswith(".py")
                    or parts[0] not in CODE_DIRECTORIES or not _allowed(path)
                    or any(part in {".", ".."} for part in parts)):
                raise HermesSourceError(f"Hermes source patch {patch_id} targets an unexpected path: {path!r}.")
            if path in by_file:
                raise HermesSourceError(f"Two Hermes source patches change {path}.")
            digests = (item["sha256"], item["patched_sha256"])
            if not all(isinstance(value, str) and _HASH.fullmatch(value) for value in digests) or digests[0] == digests[1]:
                raise HermesSourceError(f"Invalid hashes for {path} in Hermes source patch {patch_id}.")
            steps = item["replace"]
            if not isinstance(steps, list) or not steps or not all(
                isinstance(step, Mapping) and set(step) == {"old", "new"}
                and isinstance(step["old"], str) and step["old"]
                and isinstance(step["new"], str) and step["old"] != step["new"]
                for step in steps
            ):
                raise HermesSourceError(f"Invalid replacements for {path} in Hermes source patch {patch_id}.")
            by_file[path] = {"id": patch_id, "sha256": digests[0], "patched_sha256": digests[1],
                             "replace": [(step["old"], step["new"]) for step in steps]}
    return by_file


def apply_source_patch(name: str, payload: bytes, spec: Mapping[str, Any]) -> bytes:
    """``payload`` (upstream bytes of ``name``) with one reviewed patch applied, or an error."""
    if hashlib.sha256(payload).hexdigest() != spec["sha256"]:
        raise HermesSourceError(
            f"Hermes changed {name}: review IRIS patch {spec['id']} again, or drop it if upstream fixed the bug.")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HermesSourceError(f"{name} is not UTF-8 text.") from exc
    for old, new in spec["replace"]:
        if text.count(old) != 1:
            raise HermesSourceError(f"IRIS patch {spec['id']} does not match {name} exactly once.")
        text = text.replace(old, new)
    patched = text.encode("utf-8")
    if hashlib.sha256(patched).hexdigest() != spec["patched_sha256"]:
        raise HermesSourceError(f"IRIS patch {spec['id']} did not produce the reviewed {name}.")
    return patched


def _member_name(member: tarfile.TarInfo, prefix: str = DEFAULT_ARCHIVE_PREFIX) -> str | None:
    name = member.name
    if not name or "\\" in name or "\x00" in name or name.startswith("/"):
        raise HermesSourceError(f"Unsafe Git archive path: {name!r}.")
    parts = name.rstrip("/").split("/")
    if any(part in {"", ".", ".."} or ":" in part or part.endswith((".", " "))
           or part.split(".", 1)[0].casefold() in WINDOWS_RESERVED_NAMES
           for part in parts):
        raise HermesSourceError(f"Unsafe Git archive path: {name!r}.")
    if parts[0] != prefix:
        raise HermesSourceError(f"The Git archive must use the {prefix}/ prefix.")
    if not (member.isfile() or member.isdir()):
        raise HermesSourceError(f"Unsupported archive member type: {name!r}.")
    if len(parts) == 1:
        if not member.isdir():
            raise HermesSourceError("The archive prefix must be a directory.")
        return None
    return str(PurePosixPath(*parts[1:]))


def _allowed(name: str) -> bool:
    parts = PurePosixPath(name).parts
    if any(part.casefold() in EXCLUDED_COMPONENTS for part in parts):
        return False
    if any(part.casefold() == ".env" for part in parts):
        return False
    if len(parts) == 1:
        return name in ROOT_FILES or name.endswith(".py")
    return parts[0] in CODE_DIRECTORIES | ASSET_DIRECTORIES


def _reject_linked_destination(destination: Path) -> None:
    # Reject Windows junctions as well as symlinks before resolving; resolving
    # first would erase the evidence that a destination escapes through a link.
    for path in (destination, *destination.parents):
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise HermesSourceError("The Hermes destination cannot traverse a symlink or junction.")


def _read_member(archive: tarfile.TarFile, member: tarfile.TarInfo) -> bytes:
    with archive.extractfile(member) as handle:
        return handle.read()


def _source_version(payload: bytes) -> str | None:
    try:
        source = ast.parse(payload.decode("utf-8"))
    except (SyntaxError, UnicodeDecodeError) as exc:
        raise HermesSourceError("Invalid Hermes version source.") from exc
    for node in source.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets
        ) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            return node.value.value
    return None


def extract_hermes_source(
    archive: str | Path, destination: str | Path, pin: Mapping[str, Any],
) -> dict[str, Any]:
    """Atomically create *destination* (the Hermes root) from reviewed Git bytes.

    The version is a literal ``__version__`` in ``hermes_cli/__init__.py`` up to
    Hermes 0.21.4. From 0.21.6 Hermes reads it from ``install-stamp.json``: the pin
    then names the stamp's steward, pins that ``__init__.py`` by hash, and the
    stamp written here carries the reviewed version and revision.

    Files named in the pin's ``source_patches`` are written with their reviewed IRIS
    patch applied (``apply_source_patch``); the result lists the patch ids, and the version's full
    runtime pin (``source_patches``) records them. ``runtime-pin.json`` here keeps its three fields.
    """
    version, revision, expected_archive_hash, source_hashes = _validate_pin(pin)
    prefix, stamp_policy = _archive_prefix(pin), _stamp_policy(pin)
    patches = _validate_patches(pin)
    archive_path = Path(archive).resolve(strict=True)
    destination_path = Path(os.path.abspath(destination))
    _reject_linked_destination(destination_path)
    if destination_path.exists():
        raise HermesSourceError("Hermes source extraction requires a new destination.")
    temporary: Path | None = None
    with _hashed_archive(archive_path, expected_archive_hash) as (source, source_handle):
        if source.pax_headers.get("comment") != revision:
            raise HermesSourceError("The Git archive commit does not match the reviewed revision.")
        selected: dict[str, tarfile.TarInfo] = {}
        seen: set[str] = set()
        for member in source.getmembers():
            name = _member_name(member, prefix)
            if name is None:
                continue
            folded = name.casefold()
            if folded in seen:
                raise HermesSourceError(f"Duplicate or Windows case-colliding source path: {name!r}.")
            seen.add(folded)
            if member.isfile() and name in GENERATED_FILES:
                raise HermesSourceError("Upstream source collides with an IRIS runtime file.")
            if member.isfile() and _allowed(name):
                selected[name] = member
        for name, digest in source_hashes.items():
            if name not in selected or hashlib.sha256(_read_member(source, selected[name])).hexdigest() != digest:
                raise HermesSourceError(f"Reviewed Hermes source hash mismatch: {name}.")
        version_member = selected.get("hermes_cli/__init__.py")
        if version_member is None:
            raise HermesSourceError("Hermes source version does not match the reviewed pin.")
        literal = _source_version(_read_member(source, version_member))
        if literal is None:
            # 0.21.6+: no literal version. Its bytes are reviewed and the stamp states it.
            if stamp_policy is None or "hermes_cli/__init__.py" not in source_hashes:
                raise HermesSourceError(
                    "Hermes source without a literal version needs a reviewed __init__ hash and install stamp.")
        elif literal != version:
            raise HermesSourceError("Hermes source version does not match the reviewed pin.")
        stamp = None if stamp_policy is None else install_stamp(
            version, revision, int(selected["pyproject.toml"].mtime), stamp_policy)
        missing = sorted(name for name in patches if name not in selected)
        if missing:
            raise HermesSourceError(f"IRIS patch target missing from the Hermes source: {missing[0]}.")
        # Every patch is checked before anything is written.
        patched = {name: apply_source_patch(name, _read_member(source, selected[name]), spec)
                   for name, spec in patches.items()}
        patch_ids = sorted({spec["id"] for spec in patches.values()})
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        _reject_linked_destination(destination_path)
        temporary = Path(tempfile.mkdtemp(prefix=".hermes-source-", dir=destination_path.parent))
        try:
            for name, member in selected.items():
                target = temporary.joinpath(*PurePosixPath(name).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                if name in patched:
                    with target.open("xb") as output:
                        output.write(patched[name])
                    continue
                # Do not apply archive ownership, modes, links or metadata.
                with source.extractfile(member) as handle, target.open("xb") as output:
                    shutil.copyfileobj(handle, output)
            # Exactly these three fields: core.runtime_descriptor and the desktop launcher (Rust,
            # deny_unknown_fields), including an installed older version verifying an update, refuse
            # any other. The applied patches are recorded in the version's full runtime pin.
            (temporary / "runtime-pin.json").write_text(json.dumps({
                "schema_version": 1, "version": version, "revision": revision,
            }, sort_keys=True, indent=2) + "\n", encoding="utf-8")
            (temporary / "iris_cli.py").write_text(CLI_LAUNCHER, encoding="utf-8")
            if stamp is not None:
                (temporary / "install-stamp.json").write_text(
                    json.dumps(stamp, indent=2) + "\n", encoding="utf-8")
            if _stream_sha256(source_handle) != expected_archive_hash:
                raise HermesSourceError("Hermes source archive changed during extraction.")
            if destination_path.exists():
                raise HermesSourceError("The Hermes destination appeared during extraction.")
            temporary.rename(destination_path)
            temporary = None
        finally:
            if temporary is not None:
                shutil.rmtree(temporary)
    return {
        "root": str(destination_path), "source_archive_sha256": expected_archive_hash,
        "source_files": len(selected),
        "source_bytes": sum(len(patched[name]) if name in patched else item.size for name, item in selected.items()),
        "version": version, "revision": revision, "patches": patch_ids,
    }
