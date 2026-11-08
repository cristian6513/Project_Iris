"""Prepare private Windows runtimes from approved archives and hashed wheel locks.

This creates a new candidate. It never clones a developer virtual environment,
installs into a global interpreter, or activates an installation.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

if __package__:
    from .hermes_source import extract_hermes_source
    from .runtime_dependency_overrides import verify_dependency_overrides
    from .native_runtime_licenses import prepare_native_license_records
    from .runtime_inventory import (
        build_inventory,
        read_json,
        runtime_path,
        validate_pin,
        verify_runtime,
    )
    from .runtime_layout import normalize_runtime_layout
    from .runtime_licenses import inventory_python_licenses, prepare_python_license_fallbacks
    from .verify_distribution import verify_archive
else:
    from hermes_source import extract_hermes_source
    from runtime_dependency_overrides import verify_dependency_overrides
    from native_runtime_licenses import prepare_native_license_records
    from runtime_inventory import (
        build_inventory,
        read_json,
        runtime_path,
        validate_pin,
        verify_runtime,
    )
    from runtime_layout import normalize_runtime_layout
    from runtime_licenses import inventory_python_licenses, prepare_python_license_fallbacks
    from verify_distribution import verify_archive

ROOT = Path(__file__).resolve().parents[1]
MAX_ARCHIVE_FILES = 100_000
MAX_EXPANDED_BYTES = 2 * 1024**3
MAX_DOWNLOAD_BYTES = 512 * 1024**2
# Locally built replacements for locked wheels, each with its reviewed producer and pins.
LOCAL_WHEEL_PRODUCERS = {
    "av": {
        "lock": "hermes",
        "producer": "scripts/build_pyav_wheel.py",
        "build_sources": "scripts/pyav_build_sources.json",
        "sdk_sources": "scripts/pyav_ffmpeg_sources.json",
    },
}
_LOCAL_WHEEL_FIELDS = {
    "package", "version", "lock", "filename", "sha256", "producer",
    "build_sources_sha256", "sdk_sources_sha256", "reason",
}
# Locked packages left out of a runtime: exact version, lock, and the exact
# dependents reviewed to keep working without it. verify_runtime_dependencies.py
# enforces the same policy (REVIEWED_EXCLUSIONS; a test keeps both equal); a new
# version of either side needs a new review. fetch-use publishes no license text;
# browser-harness 0.1.13 imports it only for the Browser Use cloud proxy (bot
# detection, residential proxies, managed retries), which is therefore not shipped,
# and falls back to plain urllib HTTP without it.
LOCAL_EXCLUSIONS = {
    "fetch-use": {"version": "0.4.0", "lock": "hermes", "dependents": {"browser-harness": "0.1.13"}},
}
_LOCAL_EXCLUSION_FIELDS = {"package", "version", "lock", "reason"}


def regular_file(path: Path, expected: str | None = None) -> Path:
    path = Path(path).absolute()
    for item in (*reversed(path.parents), path):
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError(f"Linked runtime build input: {item}")
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError(f"Non-regular runtime build input: {path}")
    if expected and file_hash(path) != expected:
        raise ValueError(f"Runtime build input hash mismatch: {path.name}")
    return path


def file_hash(path: Path) -> str:
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def archive_bytes(path: Path, expected: str) -> bytes:
    """Hash the exact bounded bytes that the archive parser will consume."""
    path = regular_file(path)
    before = path.lstat()
    with path.open("rb") as handle:
        opened = os.fstat(handle.fileno())
        payload = handle.read(MAX_DOWNLOAD_BYTES + 1)
    after = regular_file(path).lstat()
    identity = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
    if identity(before) != identity(opened) or identity(opened) != identity(after):
        raise ValueError("Runtime archive changed during its verified read")
    if len(payload) > MAX_DOWNLOAD_BYTES:
        raise ValueError("Runtime archive exceeds its compressed size limit")
    if hashlib.sha256(payload).hexdigest() != expected:
        raise ValueError(f"Runtime build input hash mismatch: {path.name}")
    return payload


def new_directory(path: Path) -> Path:
    path = Path(path).absolute()
    if path.exists() or path.is_symlink():
        raise ValueError(f"Runtime candidate must be a new directory: {path}")
    for item in (*reversed(path.parent.parents), path.parent):
        info = item.lstat()
        if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError(f"Linked or invalid runtime output parent: {item}")
    path.mkdir()
    return path


def output_directory(path: Path) -> None:
    """Check every existing ancestor before creating an extraction directory."""
    path = Path(path).absolute()
    for item in (*reversed(path.parents), path):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & 0x400):
            raise ValueError("Linked or invalid runtime extraction output")


def output_file(path: Path) -> None:
    output_directory(path.parent)
    if os.path.lexists(path):
        raise ValueError("Runtime extraction must not replace existing content")


def cached_archive(cache: Path, spec: dict, *, offline: bool) -> Path:
    name = runtime_path(spec["filename"])
    if "/" in name:
        raise ValueError("Archive filename must be a portable basename")
    destination = cache / name
    if destination.exists() or destination.is_symlink():
        return regular_file(destination, spec["sha256"])
    if offline:
        raise ValueError(f"Missing approved archive in offline cache: {name}")
    if not spec["url"].startswith("https://"):
        raise ValueError("Runtime archives require HTTPS provenance")
    request = urllib.request.Request(spec["url"], headers={"User-Agent": "IRIS-runtime-builder"})
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=cache, prefix=".runtime-download-", delete=False) as handle:
            temporary = Path(handle.name)
            with urllib.request.urlopen(request, timeout=60) as response:
                if not response.geturl().startswith("https://"):
                    raise ValueError("Runtime archive redirected away from HTTPS")
                total = 0
                while block := response.read(1024 * 1024):
                    total += len(block)
                    if total > MAX_DOWNLOAD_BYTES:
                        raise ValueError("Runtime download exceeds its size limit")
                    handle.write(block)
        regular_file(temporary, spec["sha256"])
        temporary.replace(destination)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
    return destination


def _portable_member(name: str, prefix: str) -> str:
    name = runtime_path(name.rstrip("/"))
    if not name.startswith(prefix):
        raise ValueError("Archive member outside its approved prefix")
    return runtime_path(name[len(prefix):])


def extract_python(archive: Path, destination: Path, spec: dict) -> None:
    payload = archive_bytes(archive, spec["sha256"])
    prefix = spec["archive_prefix"]
    with tarfile.open(fileobj=io.BytesIO(payload)) as source:
        members = source.getmembers()
        aliases, total = set(), 0
        selected = []
        for member in members:
            if member.name.rstrip("/") == prefix.rstrip("/") and member.isdir():
                continue
            name = _portable_member(member.name, prefix)
            if name.casefold() in aliases:
                raise ValueError("Ambiguous Python archive member")
            aliases.add(name.casefold())
            if not member.isfile() and not member.isdir():
                raise ValueError("Linked or special Python archive member")
            total += member.size
            parts = name.split("/")
            # Neither installer tooling, venv launchers nor cache bytecode is
            # a runtime dependency. Retain stdlib source and its notices.
            if (
                "__pycache__" in parts
                or name.endswith((".pyc", ".pyo"))
                or name.startswith(("Lib/site-packages/", "Scripts/", "Lib/venv/scripts/"))
                or name == "pythonw.exe"
            ):
                continue
            if member.isfile():
                selected.append((member, name))
        if len(members) > MAX_ARCHIVE_FILES or total > MAX_EXPANDED_BYTES:
            raise ValueError("Python source archive exceeds runtime limits")
        output_directory(destination)
        if os.path.lexists(destination):
            raise ValueError("Python extraction requires a new runtime directory")
        destination.mkdir(parents=True)
        for member, name in selected:
            target = destination / name
            output_file(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            output_file(target)
            with source.extractfile(member) as handle, target.open("xb") as output:
                shutil.copyfileobj(handle, output)


def interpreter_path_file(*, hermes: bool) -> str:
    """Exact text of the runtime's ``python3XY._pth``; the Windows uninstaller checks Iris' byte for byte."""
    paths = [".", "DLLs", "Lib", "Lib/site-packages"]
    if hermes:
        paths.append("../../hermes")
    return "\n".join([*paths, "import site", ""])


def runtime_python(pin: dict, name: str) -> dict:
    """The CPython of one runtime: Hermes may pin its own (``hermes_python``), else both share ``python``."""
    return pin["hermes_python"] if name == "hermes" and "hermes_python" in pin else pin["python"]


def python_tag(spec: dict) -> str:
    """``311`` for CPython 3.11.x: names ``python311._pth`` and the ``cp311`` lock."""
    major, minor = spec["version"].split(".")[:2]
    return f"{major}{minor}"


def interpreter_paths(destination: Path, *, hermes: bool, tag: str = "311") -> None:
    (destination / "Lib/site-packages").mkdir(parents=True, exist_ok=True)
    (destination / f"python{tag}._pth").write_text(
        interpreter_path_file(hermes=hermes), encoding="utf8", newline="\n"
    )


def build_environment(cache: Path) -> dict[str, str]:
    env = {
        key: value for key, value in os.environ.items()
        if key.upper() in {"SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP", "PATHEXT"}
    }
    env.update(
        PATH="", UV_CACHE_DIR=str(cache / "uv-cache"), UV_PYTHON_DOWNLOADS="never",
        UV_NO_CONFIG="1", UV_COMPILE_BYTECODE="0", PYTHONDONTWRITEBYTECODE="1",
        PIP_DISABLE_PIP_VERSION_CHECK="1",
    )
    if os.name == "nt":
        # CPython's platform.machine() reads this Windows variable. Omitting
        # it would silently skip locked AMD64 dependencies such as nemo-relay.
        env["PROCESSOR_ARCHITECTURE"] = "AMD64"
    return env


def install_locked(uv: Path, interpreter: Path, lock: Path, cache: Path, *, offline: bool) -> None:
    command = [
        str(uv), "pip", "install", "--python", str(interpreter), "--target",
        str(interpreter.parent / "Lib/site-packages"), "--require-hashes", "--no-deps",
        "--only-binary=:all:", "--link-mode", "copy", "--no-config", "-r", str(lock),
    ]
    if offline:
        command.append("--offline")
    subprocess.run(command, env=build_environment(cache), check=True)


def clean_installer_metadata(site: Path) -> None:
    # uv --target creates console-script wrappers and direct URL metadata for
    # a locally built wheel. Runtime launch uses the explicit private Python.
    removed = set()
    for path in site.rglob("direct_url.json"):
        regular_file(path).unlink()
        removed.add(path.relative_to(site).as_posix())
    scripts = site / "bin"
    if scripts.exists():
        for path in scripts.rglob("*"):
            if path.is_file():
                regular_file(path).unlink()
                removed.add(path.relative_to(site).as_posix())
        for path in sorted(scripts.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            if path.is_dir():
                path.rmdir()
        scripts.rmdir()
    # Drop exactly the removed files from installer RECORDs: uv's launcher bytes vary
    # per run, so stale rows would make the candidate both inaccurate and unreproducible.
    if removed:
        for record in site.glob("*.dist-info/RECORD"):
            rows = regular_file(record).read_text(encoding="utf8").splitlines(keepends=True)
            kept = [row for row in rows if next(csv.reader([row]), [""])[0] not in removed]
            if len(kept) != len(rows):
                record.write_text("".join(kept), encoding="utf8", newline="")
    # The universal sounddevice wheel contains both architectures; only the
    # AMD64 PortAudio library is part of this architecture-specific runtime.
    x86_audio = site / "_sounddevice_data/portaudio-binaries/libportaudio32bit.dll"
    if x86_audio.exists():
        regular_file(x86_audio).unlink()


def extract_audio(archive: Path, stage: Path, spec: dict) -> None:
    payload = archive_bytes(archive, spec["sha256"])
    with zipfile.ZipFile(io.BytesIO(payload)) as source:
        aliases, selected, total = set(), [], 0
        for item in source.infolist():
            runtime_path(item.orig_filename.rstrip("/"))
            name = runtime_path(item.filename.rstrip("/"))
            if name.casefold() in aliases:
                raise ValueError("Ambiguous audio archive member")
            aliases.add(name.casefold())
            mode = item.external_attr >> 16
            if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR}):
                raise ValueError("Linked or special audio archive member")
            if bool(stat.S_IFMT(mode) == stat.S_IFDIR) != item.is_dir() and stat.S_IFMT(mode):
                raise ValueError("Audio archive directory type disagrees with its name")
            if item.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                raise ValueError("Unknown audio archive compression method")
            if item.flag_bits & 1:
                raise ValueError("Encrypted audio archives are not permitted")
            if not (name in {"bin", "licenses"} or name.startswith(("bin/", "licenses/"))):
                raise ValueError("Audio archive outside bin/licenses")
            if item.is_dir():
                if stat.S_IFMT(mode) not in {0, stat.S_IFDIR}:
                    raise ValueError("Ambiguous audio archive directory")
                continue
            if name.startswith("bin/") and not (
                len(name.split("/")) == 2
                and (name in {"bin/ffmpeg.exe", "bin/ffprobe.exe"} or name.endswith(".dll"))
            ):
                raise ValueError("Unapproved audio executable or file")
            total += item.file_size
            selected.append((item, name))
        if len(source.infolist()) > MAX_ARCHIVE_FILES or total > MAX_EXPANDED_BYTES:
            raise ValueError("Audio archive exceeds runtime limits")
        output_directory(stage)
        for _, name in selected:
            output_file(stage / ("tools/audio/" + name if name.startswith("bin/") else name))
        for item, name in selected:
            target = stage / ("tools/audio/" + name if name.startswith("bin/") else name)
            output_file(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            output_file(target)
            with source.open(item) as handle, target.open("xb") as output:
                shutil.copyfileobj(handle, output)


def extract_shell(archive: Path, stage: Path, spec: dict) -> tuple[list[dict], list[str]]:
    """Only the reviewed selection; a complete source sidecar is verified before staging."""
    manifest = regular_file(ROOT / "scripts/shell_runtime_sources.json", spec["build_sources_sha256"])
    sources = read_json(manifest)
    expected = {"tools/shell/" + name for name in sources["files"]}
    expected |= {"licenses/shell/" + name for row in sources["sources"] for name in row["notices"]}
    expected |= {"licenses/shell/README.txt", "licenses/shell/components.json", "licenses/shell/sources.json"}
    payload = archive_bytes(archive, spec["sha256"])
    with zipfile.ZipFile(io.BytesIO(payload)) as source:
        entries, total = {}, 0
        for item in source.infolist():
            name = runtime_path(item.orig_filename)
            mode = item.external_attr >> 16
            if (name not in expected or name in entries or item.is_dir() or item.flag_bits & 1
                    or stat.S_IFMT(mode) not in {0, stat.S_IFREG}
                    or item.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}):
                raise ValueError("Unapproved or aliased private shell member")
            total += item.file_size
            if total > 64 * 1024**2:
                raise ValueError("Private shell exceeds its size limit")
            entries[name] = item
        if entries.keys() != expected:
            raise ValueError("Private shell package is incomplete")
        for name in entries:
            output_file(stage / name)
        for name, item in entries.items():
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as handle:
                handle.write(source.read(item))
    return (read_json(stage / "licenses/shell/components.json")["licenses"],
            sorted(name for name in expected if name.endswith(".exe")))


def local_wheels(pin: dict) -> list[dict]:
    """Validate explicit locally built replacements before any output is created."""
    items = pin.get("local_wheels", [])
    if not isinstance(items, list):
        raise ValueError("Invalid local wheel overlays")
    seen = set()
    for item in items:
        package = item.get("package") if isinstance(item, dict) else None
        producer = LOCAL_WHEEL_PRODUCERS.get(package)
        if (producer is None or set(item) != _LOCAL_WHEEL_FIELDS or item["package"] in seen
                or item["lock"] != producer["lock"] or item["producer"] != producer["producer"]
                or not isinstance(item["version"], str)
                or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)+", item["version"])
                or not isinstance(item["reason"], str) or not item["reason"].strip()
                or any(not isinstance(item[key], str)
                       or not re.fullmatch(r"[0-9a-f]{64}", item[key])
                       for key in ("sha256", "build_sources_sha256", "sdk_sources_sha256"))):
            raise ValueError("Invalid local wheel overlay")
        name = runtime_path(item["filename"])
        if ("/" in name or not name.endswith(".whl")
                or not name.startswith(f"{item['package']}-{item['version']}-")):
            raise ValueError("Local wheel overlay needs a matching portable wheel filename")
        for key, source in (("build_sources_sha256", producer["build_sources"]),
                            ("sdk_sources_sha256", producer["sdk_sources"])):
            try:
                current = file_hash(regular_file(ROOT / source))
            except OSError as exc:
                raise ValueError(f"Missing local wheel build pin: {source}") from exc
            if current != item[key]:
                raise ValueError(
                    "Local wheel build pins changed; rebuild and review the wheel again")
        seen.add(item["package"])
    return items


def local_exclusions(pin: dict) -> list[dict]:
    """Validate locked packages deliberately left out (``LOCAL_EXCLUSIONS``) before any output."""
    items = pin.get("local_exclusions", [])
    if not isinstance(items, list):
        raise ValueError("Invalid local exclusions")
    seen = set(item.get("package") for item in pin.get("local_wheels", []) if isinstance(item, dict))
    for item in items:
        package = item.get("package") if isinstance(item, dict) else None
        reviewed = LOCAL_EXCLUSIONS.get(package)
        if (reviewed is None or set(item) != _LOCAL_EXCLUSION_FIELDS or package in seen
                or item["lock"] != reviewed["lock"] or item["version"] != reviewed["version"]
                or not isinstance(item["reason"], str) or not item["reason"].strip()):
            raise ValueError("Invalid local exclusion")
        seen.add(package)
    return items


def target_environment(spec: dict) -> dict[str, str]:
    """PEP 508 marker values of a private runtime: Windows AMD64 and its pinned CPython."""
    version = spec["version"]
    return {
        "sys_platform": "win32", "platform_system": "Windows", "platform_machine": "AMD64",
        "os_name": "nt", "implementation_name": "cpython", "platform_python_implementation": "CPython",
        "python_version": ".".join(version.split(".")[:2]), "python_full_version": version,
        "implementation_version": version,
    }


def effective_lock(
    text: str, overlays: list[dict], environment: dict[str, str] | None = None,
    exclusions: list[dict] = (),
) -> str:
    """Remove exactly one hashed requirement block per local overlay or exclusion; keep everything else.

    The overlay is installed unconditionally, so a replaced requirement may carry
    a marker only when ``environment`` (the runtime it is built for) satisfies it
    (Hermes 0.21.6 locks ``av`` under faster-whisper's platform marker). An
    excluded requirement must be one this runtime would otherwise install.
    """
    from packaging.markers import InvalidMarker, Marker

    lines, output, removed, index = text.splitlines(keepends=True), [], {}, 0
    wanted = {item["package"]: item for item in (*overlays, *exclusions)}
    while index < len(lines):
        match = re.match(r"([A-Za-z0-9_.-]+)==([^\s;\\]+)(\s*;[^\\]*)?\s*\\?\s*$", lines[index])
        name = match.group(1).lower().replace("_", "-") if match else None
        if name in wanted:
            marker = (match.group(3) or "").strip().lstrip(";").strip()
            try:
                applies = not marker or (environment is not None and Marker(marker).evaluate(environment))
            except InvalidMarker:
                applies = False
            if match.group(2) != wanted[name]["version"] or not applies:
                raise ValueError("Local wheel overlay does not match the locked requirement")
            removed[name] = removed.get(name, 0) + 1
            index += 1
            while index < len(lines) and lines[index].startswith((" ", "\t")):
                if not lines[index].strip().startswith("--hash=sha256:"):
                    raise ValueError("Unexpected content in a replaced lock requirement")
                index += 1
            continue
        output.append(lines[index])
        index += 1
    if any(removed.get(name) != 1 for name in wanted):
        raise ValueError("Local wheel overlay must replace exactly one locked requirement")
    return "".join(output)


def verify_local_wheel_install(site: Path, wheel: Path) -> None:
    """Every installed payload file must carry the approved wheel's recorded bytes."""
    with zipfile.ZipFile(wheel) as archive:
        record = next(n for n in archive.namelist() if n.endswith(".dist-info/RECORD"))
        rows = list(csv.reader(io.StringIO(archive.read(record).decode("utf8"))))
    checked = 0
    for name, digest, size in rows:
        if name == record or ".dist-info/" in name:
            continue
        installed = regular_file(site / name)
        data = installed.read_bytes()
        encoded = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        actual = "sha256=" + encoded
        if actual != digest or int(size) != len(data):
            raise ValueError(f"Installed file differs from the approved local wheel: {name}")
        checked += 1
    if not checked:
        raise ValueError("Approved local wheel installed no payload")


def reviewed_native_licenses(pin: dict) -> list[dict]:
    """Do not seal a candidate using wrapper licenses for unreviewed DLLs."""
    review = pin.get("native_licenses")
    if not isinstance(review, dict) or set(review) != {"pending", "records"}:
        raise ValueError("Missing or unknown native license review")
    pending, records = review["pending"], review["records"]
    if (not isinstance(pending, list) or any(not isinstance(item, str) or not item for item in pending)
            or not isinstance(records, list)):
        raise ValueError("Invalid native license review")
    if pending:
        raise ValueError("Native license evidence remains incomplete: " + "; ".join(pending))
    if not records or any(not isinstance(item, dict) for item in records):
        raise ValueError("Native review requires preserved component notice records")
    return records


def prepare_runtime(
    output: Path, cache: Path, iris_wheel: Path, uv: Path, pin: dict, *, offline: bool = False,
) -> dict:
    validate_pin(pin)
    native_digest = pin.get("native_license_sources_sha256")
    if (not isinstance(native_digest, str) or len(native_digest) != 64
            or any(char not in "0123456789abcdef" for char in native_digest)):
        raise ValueError("Missing or invalid native license source manifest hash")
    if set(pin["locks"]) != {"iris", "hermes"}:
        raise ValueError("Unknown runtime lock component")
    pythons = {name: runtime_python(pin, name) for name in ("iris", "hermes")}
    for spec, key in ((pythons["iris"], "filename"), (pythons["hermes"], "filename"),
                      (pin["audio"], "filename"), (pin["hermes"], "source_archive_filename")):
        name = runtime_path(spec[key])
        if "/" in name:
            raise ValueError("Runtime source filenames must be portable basenames")
    if any(spec.get("archive_prefix") != "python/" for spec in pythons.values()):
        raise ValueError("Unknown Python archive layout")
    overlays = local_wheels(pin)
    exclusions = local_exclusions(pin)
    uv = regular_file(uv)
    wheel = regular_file(iris_wheel, pin["iris"]["wheel_sha256"])
    verify_archive(wheel)
    cache = Path(cache).absolute()
    for item in (*reversed(cache.parents), cache):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("Linked or invalid runtime build cache")
    cache.mkdir(parents=True, exist_ok=True)
    python_archives = {name: cached_archive(cache, spec, offline=offline) for name, spec in pythons.items()}
    audio_archive = regular_file(cache / pin["audio"]["filename"], pin["audio"]["sha256"])
    shell_archive = None
    if "shell" in pin:
        shell = pin["shell"]
        if shell.get("producer") != "scripts/build_shell_runtime.py":
            raise ValueError("Unknown private shell producer")
        regular_file(ROOT / "scripts/shell_runtime_sources.json", shell["build_sources_sha256"])
        # The release must retain this source sidecar, never just the executables.
        regular_file(cache / runtime_path(shell["source_filename"]), shell["source_sha256"])
        shell_archive = regular_file(cache / runtime_path(shell["filename"]), shell["sha256"])
    hermes_archive = regular_file(
        cache / pin["hermes"]["source_archive_filename"], pin["hermes"]["source_archive_sha256"]
    )
    input_locks = {
        name: regular_file(ROOT / f"scripts/locks/{name}-windows-cp{python_tag(pythons[name])}.txt",
                           item["sha256"])
        for name, item in pin["locks"].items()
    }
    dependency_overrides = verify_dependency_overrides(
        pin, input_locks["hermes"].read_text("utf8"),
    )
    sources = {
        "iris-pyproject.toml": regular_file(ROOT / "pyproject.toml", pin["iris"]["pyproject_sha256"]),
        "iris-uv.lock": regular_file(ROOT / "uv.lock", pin["iris"]["uv_lock_sha256"]),
    }
    overlay_wheels = {}
    for item in overlays:
        try:
            overlay_wheels[item["package"]] = regular_file(
                cache / runtime_path(item["filename"]), item["sha256"])
        except OSError as exc:
            raise ValueError(
                f"Approved local wheel missing from cache: {item['filename']}") from exc
    effective_locks = {
        name: effective_lock(path.read_text(encoding="utf8"),
                             [item for item in overlays if item["lock"] == name],
                             target_environment(pythons[name]),
                             [item for item in exclusions if item["lock"] == name])
        for name, path in input_locks.items()
    }
    native_manifest = regular_file(
        ROOT / "scripts/runtime_native_license_sources.json",
        native_digest,
    )
    stage = new_directory(output)
    (stage / "locks").mkdir()
    for name, source in sources.items():
        shutil.copyfile(source, stage / "locks" / name)
    for name, source in input_locks.items():
        shutil.copyfile(source, stage / pin["locks"][name]["path"])
    extract_hermes_source(hermes_archive, stage / "hermes", pin["hermes"])
    verify_dependency_overrides(pin, input_locks["hermes"].read_text("utf8"), stage / "hermes")
    extract_audio(audio_archive, stage, pin["audio"])
    shell_licenses, shell_executables = extract_shell(shell_archive, stage, pin["shell"]) if shell_archive else ([], [])
    for name in ("iris", "hermes"):
        destination = stage / "runtimes" / name
        extract_python(python_archives[name], destination, pythons[name])
        interpreter_paths(destination, hermes=name == "hermes", tag=python_tag(pythons[name]))
        # The pinned lock stays in the stage unchanged; installation uses it minus any
        # requirement replaced by an approved local wheel, which is installed next.
        lock_id = hashlib.sha256(effective_locks[name].encode()).hexdigest()[:16]
        effective = cache / f".effective-{name}-{lock_id}.txt"
        effective.write_text(effective_locks[name], encoding="utf8", newline="")
        install_locked(uv, destination / "python.exe", effective, cache, offline=offline)
        site = destination / "Lib/site-packages"
        for item in overlays:
            if item["lock"] != name:
                continue
            approved = regular_file(overlay_wheels[item["package"]], item["sha256"])
            command = [str(uv), "pip", "install", "--python", str(destination / "python.exe"),
                       "--target", str(site), "--no-deps", "--link-mode", "copy", "--no-config",
                       str(approved)]
            subprocess.run([*command, "--offline"] if offline else command,
                           env=build_environment(cache), check=True)
            verify_local_wheel_install(site, approved)
    subprocess.run([
        str(uv), "pip", "install", "--python", str(stage / "runtimes/iris/python.exe"),
        "--target", str(stage / "runtimes/iris/Lib/site-packages"), "--no-deps",
        "--link-mode", "copy", "--no-config", str(wheel),
    ], env=build_environment(cache), check=True)
    normalize_runtime_layout(stage)
    checker = regular_file(ROOT / "scripts/verify_runtime_dependencies.py")
    if dependency_overrides:
        (stage / "runtime-pin.json").write_text(json.dumps(pin, indent=2) + "\n", encoding="utf8")
    for name in ("iris", "hermes"):
        excluded = [arg for item in exclusions if item["lock"] == name
                    for arg in ("--excluded", f"{item['package']}=={item['version']}")]
        override_args = (["--pin", str(stage / "runtime-pin.json")]
                         if name == "hermes" and dependency_overrides else [])
        subprocess.run([
            str(stage / f"runtimes/{name}/python.exe"), "-E", "-s", "-B", "-X", "utf8",
            str(checker), "--lock", str(stage / pin["locks"][name]["path"]), *excluded, *override_args,
        ], cwd=cache, env=build_environment(cache), check=True, timeout=60)
    fallbacks = prepare_python_license_fallbacks(
        stage, expected_manifest_sha256=pin["license_sources_sha256"],
    )
    licenses = list(shell_licenses)
    for name in ("iris", "hermes"):
        destination = stage / "runtimes" / name
        site = destination / "Lib/site-packages"
        clean_installer_metadata(site)
        licenses.extend(inventory_python_licenses(stage, f"{name}-runtime", site, fallbacks=fallbacks))
        licenses.append({
            "component": f"{name}-runtime", "package": "CPython", "version": pythons[name]["version"],
            "license": pythons[name]["license"], "files": [f"runtimes/{name}/LICENSE.txt"],
            "source": pythons[name]["url"],
        })
    licenses.append({
        "component": "hermes-source", "package": "hermes-agent", "version": pin["hermes"]["version"],
        "license": "MIT", "files": ["hermes/LICENSE"], "source": pin["hermes"]["source_url"],
    })
    audio_licenses = read_json(stage / "licenses/audio/components.json")
    licenses.extend(audio_licenses["licenses"])
    native_records = prepare_native_license_records(
        stage, native_manifest, expected_manifest_sha256=pin["native_license_sources_sha256"],
    )
    native_evidence = stage / "licenses/native/sources.json"
    output_file(native_evidence)
    native_evidence.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(native_manifest, native_evidence)
    # Keep partial preparation reviewable, but never emit a release inventory
    # while bundled third-party libraries still lack their own notice evidence.
    reviewed = reviewed_native_licenses(pin)
    if reviewed != native_records:
        raise ValueError("Collected native notices differ from the approved component records")
    licenses.extend(reviewed)
    (stage / "runtime-pin.json").write_text(json.dumps(pin, indent=2) + "\n", encoding="utf8")
    executables = [
        "runtimes/hermes/python.exe", "runtimes/iris/python.exe",
        "tools/audio/bin/ffmpeg.exe", "tools/audio/bin/ffprobe.exe",
        "runtimes/hermes/Lib/site-packages/winpty/winpty-agent.exe",
        # pywinpty 3 (Hermes 0.21.6) also ships the ConPTY host it starts.
        "runtimes/hermes/Lib/site-packages/winpty/OpenConsole.exe",
    ]
    document = build_inventory(stage, pin, licenses, sorted(executables + shell_executables))
    (stage / "runtime-inventory.json").write_text(json.dumps(document, indent=2) + "\n", encoding="utf8")
    verify_runtime(stage, expected_pin=pin)
    return document


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache-root", type=Path, required=True)
    parser.add_argument("--iris-wheel", type=Path, required=True)
    parser.add_argument("--uv", type=Path, required=True)
    parser.add_argument("--pin", type=Path, default=ROOT / "scripts/runtime_sources.json")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args(argv)
    document = prepare_runtime(
        args.output, args.cache_root, args.iris_wheel, args.uv, read_json(args.pin), offline=args.offline
    )
    print(json.dumps({"files": len(document["files"]), "licenses": len(document["licenses"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
