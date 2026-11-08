"""Normalize only known Windows wheel payloads in a new private candidate.

The public Iris wheel remains intact. Its factory skill data is relocated from
uv's --target directory to sys.prefix/share; the old Companion installer belongs
to the separate desktop release step, not either private Python runtime.
"""

from __future__ import annotations

import csv
import hashlib
import io
import os
import stat
from dataclasses import dataclass
from pathlib import Path

if __package__:
    from .runtime_inventory import read_json, runtime_path
    from .stage_companion_release import _pe_architecture, _read_manifest
else:
    from runtime_inventory import read_json, runtime_path
    from stage_companion_release import _pe_architecture, _read_manifest


# These exact files are unused launchers or native libraries for other platforms
# included by the approved universal wheels. Never prune arbitrary *.exe files:
# Hermes needs Winpty and Nemo relay's native child processes.
WINDOWS_WHEEL_PRUNE_FILES = frozenset({
    *(f"setuptools/{kind}{suffix}.exe" for kind in ("cli", "gui")
      for suffix in ("", "-32", "-64", "-arm64")),
    "pythonwin/Pythonwin.exe",
    "win32/pythonservice.exe",
    "_sounddevice_data/portaudio-binaries/libportaudio32bit.dll",
    "discord/bin/libopus-0.x86.dll",
    "pvporcupine/lib/linux/x86_64/libpv_porcupine.so",
    "pvporcupine/lib/mac/arm64/libpv_porcupine.dylib",
    "pvporcupine/lib/mac/x86_64/libpv_porcupine.dylib",
    *(f"pvporcupine/lib/raspberry-pi/{arch}/libpv_porcupine.so" for arch in (
        "arm11", "cortex-a53", "cortex-a53-aarch64", "cortex-a72",
        "cortex-a72-aarch64", "cortex-a76", "cortex-a76-aarch64",
    )),
    "pvporcupine/lib/windows/arm64/libpv_porcupine.dll",
    # Test-suite private key and stray bytecode shipped inside approved wheels.
    "tornado/test/test.key",
    "numpy/distutils/__pycache__/conv_template.cpython-311.pyc",
})
# Removed only when pruning above leaves them empty.
WINDOWS_WHEEL_PRUNE_EMPTY_DIRECTORIES = frozenset({"numpy/distutils/__pycache__"})
_SKILL_FILES = frozenset({
    "iris-os/hermes-skills/iris-app-builder/SKILL.md",
    "iris-os/hermes-skills/iris-app-builder/template/app.json",
    "iris-os/hermes-skills/iris-app-builder/template/ui/main.js",
    "iris-os/hermes-skills/iris-app-builder/template/ui/style.css",
    "iris-os/hermes-skills/iris-app-builder/template/ui/widget.js",
    "iris-os/builder/README.md",
    "iris-os/builder/community-apps-python.md",
    "iris-os/builder/sdk2/app.json",
    "iris-os/builder/sdk2/LICENSE",
    "iris-os/builder/sdk2/ui/main.js",
    "iris-os/builder/sdk2/ui/style.css",
    "iris-os/builder/sdk2/ui/view.js",
    "iris-os/builder/sdk2/ui/widget.js",
    "iris-os/builder/sdk2/backend/main.py",
    "iris-os/builder/sdk2/skill/SKILL.md",
    "iris-os/hermes-skills/iris-os/SKILL.md",
    "iris-os/hermes-skills/iris-os/scripts/control_player.py",
    "iris-os/hermes-skills/iris-os/scripts/app_action.py",
    "iris-os/hermes-skills/iris-os/scripts/visual_request.py",
    "iris-os/hermes-skills/iris-os/scripts/background_task.py",
    "iris-os/hermes-skills/iris-os/references/action-contracts.md",
    "iris-os/hermes-skills/iris-os/references/background.md",
    "iris-os/hermes-skills/iris-os/references/google.md",
    "iris-os/hermes-skills/iris-os/references/player.md",
    "iris-os/hermes-skills/iris-os/references/vision.md",
})
_COMPANION_MANIFEST = "iris-os/companion/release-manifest.json"
_MAX_SKILL_BYTES = 1024 * 1024


@dataclass(frozen=True)
class _Entry:
    path: Path
    identity: tuple[int, int, int, int]
    directory: bool


def _entry(path: Path) -> _Entry:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ValueError(f"Linked runtime layout entry: {path}")
    directory = stat.S_ISDIR(info.st_mode)
    if not directory and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
        raise ValueError(f"Non-regular runtime layout entry: {path}")
    return _Entry(path, (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns), directory)


def _directory(path: Path) -> Path:
    path = Path(path).absolute()
    if path != path.resolve(strict=True):
        raise ValueError("Runtime layout directory must be canonical")
    for parent in (*reversed(path.parents), path):
        if not _entry(parent).directory:
            raise ValueError(f"Runtime layout requires a directory: {parent}")
    return path


def _tree(root: Path) -> dict[str, _Entry]:
    _directory(root)
    result: dict[str, _Entry] = {}
    aliases: set[str] = set()

    def visit(directory: Path) -> None:
        for path in sorted(directory.iterdir()):
            name = runtime_path(path.relative_to(root).as_posix())
            if name.casefold() in aliases:
                raise ValueError(f"Ambiguous runtime layout entry: {name}")
            aliases.add(name.casefold())
            entry = _entry(path)
            result[name] = entry
            if entry.directory:
                visit(path)

    visit(root)
    return result


def _unchanged(entry: _Entry) -> None:
    if _entry(entry.path) != entry:
        raise ValueError(f"Runtime layout input changed: {entry.path}")
    # Recheck every ancestor immediately before deletion or rename. A junction
    # substituted after preflight must not redirect a mutation outside staging.
    _directory(entry.path.parent)


def _factory_payload(root: Path, *, companion: bool) -> tuple[dict[str, _Entry], list[_Entry]]:
    entries = _tree(root)
    files = {name for name, entry in entries.items() if not entry.directory}
    expected = set(_SKILL_FILES)
    removed = []
    if companion:
        manifest_path = root / _COMPANION_MANIFEST
        document = read_json(manifest_path, maximum=64 * 1024)
        if type(document.get("schema")) is not int or document["schema"] != 1:
            raise ValueError("Unknown factory Companion manifest schema")
        manifest = _read_manifest(manifest_path)
        if manifest != document:
            raise ValueError("Factory Companion manifest changed during preflight")
        _unchanged(entries[_COMPANION_MANIFEST])
        filename = runtime_path(manifest["filename"])
        if "/" in filename:
            raise ValueError("Factory Companion filename must be a portable basename")
        installer_name = "iris-os/companion/" + filename
        expected.update({_COMPANION_MANIFEST, installer_name})
        installer = entries.get(installer_name)
        if not installer or installer.directory:
            raise ValueError("Factory Companion installer is missing")
        _unchanged(installer)
        with installer.path.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        if digest != manifest["sha256"] or _pe_architecture(installer.path) not in {"x86", "x64"}:
            raise ValueError("Factory Companion release pair is invalid")
        _unchanged(installer)
        removed = [entries[_COMPANION_MANIFEST], installer]
    if files != expected:
        raise ValueError("Unknown or missing public Iris factory data")
    expected_dirs = {
        parent.as_posix()
        for name in expected for parent in Path(name).parents if parent != Path(".")
    }
    if {name for name, entry in entries.items() if entry.directory} != expected_dirs:
        raise ValueError("Unknown public Iris factory directory")
    size = sum(entries[name].identity[2] for name in _SKILL_FILES)
    if not 0 < size <= _MAX_SKILL_BYTES:
        raise ValueError("Iris factory skill exceeds its public-data limit")
    for name in _SKILL_FILES:
        entry = entries[name]
        if not entry.identity[2]:
            raise ValueError("Empty Iris factory skill file")
        _unchanged(entry)
        entry.path.read_text(encoding="utf8")
        _unchanged(entry)
    return entries, removed


def normalize_runtime_layout(stage: Path) -> dict[str, list[str]]:
    """Preflight both private sites, then prune and relocate public factory data.

    Called after locked wheels and Iris itself are installed, before the license
    and binary inventory. A second call validates the finished layout unchanged.
    Invalid input anywhere in the candidate is rejected before any mutation.
    """
    stage = _directory(stage)
    sites = {
        name: stage / "runtimes" / name / "Lib/site-packages"
        for name in ("iris", "hermes")
    }
    trees = {name: _tree(site) for name, site in sites.items()}
    removals = [
        entry for tree in trees.values() for name, entry in tree.items()
        if name in WINDOWS_WHEEL_PRUNE_FILES
    ]
    if any(entry.directory for entry in removals):
        raise ValueError("Known wheel runtime binary is not an ordinary file")
    source = sites["iris"] / "share"
    destination = stage / "runtimes/iris/share"
    relocate = "share" in trees["iris"]
    if relocate:
        if not trees["iris"]["share"].directory:
            raise ValueError("Factory share is not a directory")
        if os.path.lexists(destination):
            raise ValueError("Iris factory relocation destination already exists")
        _, companion = _factory_payload(source, companion=True)
        _directory(destination.parent)
    else:
        _factory_payload(destination, companion=False)
        companion = []
    # uv --target records data files relative to site-packages. Once share is
    # moved to the interpreter prefix, RECORD must describe that actual layout.
    # Prepare every rewrite before pruning anything; unknown share rows fail
    # closed instead of inventing a new location or dropping license evidence.
    record_updates = []
    if relocate:
        relocated = {"share/" + name for name in _SKILL_FILES}
        discarded = {entry.path.relative_to(sites["iris"]).as_posix() for entry in companion}
        for name, entry in trees["iris"].items():
            parts = Path(name).parts
            if len(parts) != 2 or not parts[0].endswith(".dist-info") or parts[1] != "RECORD":
                continue
            _unchanged(entry)
            rows, changed = [], False
            for row in csv.reader(io.StringIO(entry.path.read_text(encoding="utf8"))):
                value = row[0].replace("\\", "/") if row else ""
                if value in discarded:
                    changed = True
                    continue
                if value in relocated:
                    row[0] = "../../" + value
                    changed = True
                elif value.startswith("share/"):
                    raise ValueError("Unknown public Iris factory RECORD path")
                rows.append(row)
            if changed:
                output = io.StringIO(newline="")
                csv.writer(output, lineterminator="\n").writerows(rows)
                record_updates.append((entry, output.getvalue().encode("utf8")))
    # Check every planned input again as a group before the first mutation.
    for entry in (*removals, *companion, *(entry for entry, _ in record_updates)):
        _unchanged(entry)
    removed = []
    for entry in (*removals, *companion):
        _unchanged(entry)
        entry.path.unlink()
        removed.append(entry.path.relative_to(stage).as_posix())
    for site in sites.values():
        for name in sorted(WINDOWS_WHEEL_PRUNE_EMPTY_DIRECTORIES):
            directory = site / name
            if os.path.lexists(directory) and not any(_directory(directory).iterdir()):
                directory.rmdir()
                removed.append(directory.relative_to(stage).as_posix())
    if relocate:
        (source / "iris-os/companion").rmdir()
        _factory_payload(source, companion=False)
        _directory(destination.parent)
        if os.path.lexists(destination):
            raise ValueError("Iris factory relocation destination appeared during preparation")
        source.rename(destination)
    for entry, payload in record_updates:
        _unchanged(entry)
        entry.path.write_bytes(payload)
    return {
        "removed": sorted(removed),
        "relocated": ["runtimes/iris/share"] if relocate else [],
    }
