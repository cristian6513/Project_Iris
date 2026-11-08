"""A08.7 Windows staging: the private runtime and Iris.exe in one movable folder.

It is the input of the future installer (A08.9). Every path inside is relative
and nothing records where the folder was built, so it can be moved or copied as
a whole. It is not an installer, not signed and activates nothing: release
authenticity belongs to the Iris update channel (A08.8), whose address and
trusted public keys a version carries in ``channel.json``.

    python scripts/build_staging.py --runtime <A08.4 candidate> --executable <iris-desktop.exe> --output <new folder>
        [--channel-url <https://.../stable.json> --channel-key <trusted key .json> [--channel-name stable] [--channel-test]]
        [--companion <folder from stage_companion_release.py>] [--nsis-dir <NSIS that builds the installers>]
    python scripts/build_staging.py --verify <staging folder>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import struct
import subprocess
from pathlib import Path
from typing import Any

if __package__:
    from .desktop_licenses import cargo_metadata, collect, collect_installer, collect_web, load_reviewed
    from .distribution_assets import DistributionError
    from .runtime_inventory import INVENTORY_NAME, PIN_NAME, _link_check, _pe_machine, read_json, verify_runtime
    from .stage_companion_release import _pe_architecture
    from .stage_companion_release import _read_manifest as read_companion_manifest
else:
    from desktop_licenses import cargo_metadata, collect, collect_installer, collect_web, load_reviewed
    from distribution_assets import DistributionError
    from runtime_inventory import INVENTORY_NAME, PIN_NAME, _link_check, _pe_machine, read_json, verify_runtime
    from stage_companion_release import _pe_architecture
    from stage_companion_release import _read_manifest as read_companion_manifest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
APPROVED_PIN = Path(__file__).with_name("runtime_sources.json")
MANIFEST = "iris-staging.json"
DESCRIPTOR = "runtime.json"
EXECUTABLE = "Iris.exe"
README = "LEEME.txt"
DESKTOP = "desktop"
DESKTOP_LICENSES = "desktop/licenses.json"
CHANNEL = "channel.json"
COMPANION = "companion"
COMPANION_MANIFEST = "companion/release-manifest.json"
COMPANION_LICENSES = "companion/licenses.json"
INSTALLER = "installer"
INSTALLER_LICENSES = "installer/licenses.json"
SITE_PACKAGES = "runtimes/iris/Lib/site-packages"
# Top-level entries the staging adds next to the A08.4 runtime; this manifest
# verifies them, the runtime inventory verifies everything else.
STAGED = frozenset({MANIFEST, DESCRIPTOR, EXECUTABLE, README, DESKTOP, CHANNEL, COMPANION, INSTALLER})
# WebView2 is detected and explained, never downloaded behind the user's back (A08.9).
COMPANION_WEBVIEW2_MODES = frozenset({"skip"})
WINDOWS_MAX_PATH = 259  # MAX_PATH minus the terminating NUL, as in core.runtime_descriptor
_SCAN_BLOCK = 4 * 1024 * 1024


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def windows_path_length(value: str | Path) -> int:
    return len(str(value).encode("utf-16-le")) // 2


def descriptor_for(pin: dict, max_relative_path: int) -> dict[str, Any]:
    """Packaged descriptor of the A08.4 layout. The Iris revision identifies the
    exact wheel installed (first 40 hex digits of its SHA-256)."""
    return {
        "schema_version": 2, "mode": "packaged",
        "iris": {
            "root": "runtimes/iris/Lib/site-packages", "python": "runtimes/iris/python.exe",
            "assets": "runtimes/iris/Lib/site-packages/face", "revision": pin["iris"]["wheel_sha256"][:40],
        },
        "hermes": {
            "root": "hermes", "python": "runtimes/hermes/python.exe", "cli": "hermes/iris_cli.py",
            "version": pin["hermes"]["version"], "revision": pin["hermes"]["revision"],
            "metadata": "hermes/runtime-pin.json",
        },
        "install": {"max_relative_path": max_relative_path},
    }


def readme(version: str, root_limit: int) -> bytes:
    lines = [
        f"IRIS OS {version} — CARPETA DE INSTALACIÓN (CANDIDATO DE PRUEBA)",
        "",
        "Abre Iris.exe. No hace falta Python, Git ni Hermes: todo lo necesario está en esta carpeta.",
        f"Puedes mover o copiar la carpeta completa. Su ruta no puede superar {root_limit} caracteres;",
        "si la superas, Iris lo indica al abrirse y no inicia nada.",
        "Tu configuración, tus cuentas y tus avatares se guardan en tu perfil, fuera de esta carpeta.",
        "Cerrar la ventana la oculta en la bandeja. «Salir de Iris Desktop» cierra la aplicación;",
        "los servicios de Iris y Hermes continúan para Companion, rutinas y mensajería.",
        "Licencias de terceros: botón Licencias del asistente, y las carpetas licenses, desktop/licenses,",
        "companion/licenses e installer/licenses.",
        "Esta carpeta todavía no es un instalador ni recibe actualizaciones automáticas.",
    ]
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")


def _walk(root: Path, top: str) -> list[str]:
    """Regular files under a staged entry; links and other types are refused."""
    path = root / top
    info = path.lstat()
    _link_check(path, info)
    if stat.S_ISREG(info.st_mode):
        return [top]
    if not stat.S_ISDIR(info.st_mode):
        raise DistributionError(f"Unexpected staging entry: {top}")
    names = []
    for current, directories, files in os.walk(path):
        for name in directories + files:
            item = Path(current) / name
            _link_check(item, item.lstat())
            if name in files:
                if not stat.S_ISREG(item.lstat().st_mode):
                    raise DistributionError(f"Unexpected staging entry: {item}")
                names.append(item.relative_to(root).as_posix())
    return sorted(names)


def _max_relative_path(root: Path) -> int:
    longest = 0
    for current, _directories, files in os.walk(root):
        for name in files:
            longest = max(longest, windows_path_length((Path(current) / name).relative_to(root)))
    return longest


def scan_for_paths(root: Path, needles: list[str]) -> list[str]:
    """Files that embed any of these absolute paths (UTF-8 or UTF-16), case-insensitively."""
    patterns = set()
    for needle in needles:
        for variant in {needle, needle.replace("\\", "/"), needle.replace("\\", "\\\\")}:
            for encoding in ("utf-8", "utf-16-le"):
                # bytes.lower() folds ASCII only: keep non-ASCII letters as written and lowered.
                patterns.add(variant.encode(encoding).lower())
                patterns.add(variant.lower().encode(encoding).lower())
    overlap = max(len(p) for p in patterns)
    hits = []
    for current, _directories, files in os.walk(root):
        for name in files:
            path = Path(current) / name
            tail = b""
            with path.open("rb") as handle:
                for block in iter(lambda: handle.read(_SCAN_BLOCK), b""):
                    window = (tail + block).lower()
                    if any(pattern in window for pattern in patterns):
                        hits.append(path.relative_to(root).as_posix())
                        break
                    tail = window[-overlap:]
    return sorted(hits)


# DLLs of the Visual C++ Redistributable: a clean Windows does not have them, so Iris.exe
# must not import them (the Universal CRT, api-ms-win-crt-* and ucrtbase, ships with Windows).
_VC_REDIST = ("vcruntime", "msvcp", "concrt", "vccorlib", "vcomp", "vcamp", "mfc")


def _pe_imports(path: Path) -> list[str]:
    """DLL names a PE32+ file imports, normal and delay-loaded, read from its headers."""
    data = path.read_bytes()

    def u16(offset: int) -> int:
        return struct.unpack_from("<H", data, offset)[0]

    def u32(offset: int) -> int:
        return struct.unpack_from("<I", data, offset)[0]

    pe = u32(60)
    sections, optional_size = u16(pe + 6), u16(pe + 20)
    optional = pe + 24
    if optional_size < 112 or u16(optional) != 0x20B:  # no data directories: nothing imported
        return []
    table = optional + optional_size
    spans = [(u32(table + 40 * i + 12), max(u32(table + 40 * i + 8), u32(table + 40 * i + 16)),
              u32(table + 40 * i + 20)) for i in range(sections)]

    def offset_of(rva: int) -> int:
        for address, size, raw in spans:
            if address <= rva < address + size:
                return raw + rva - address
        raise DistributionError(f"{path.name}: import table outside its sections")

    def name_at(rva: int) -> str:
        start = offset_of(rva)
        return data[start:data.index(b"\0", start)].decode("ascii")

    names = []
    directories = u32(optional + 108)
    for index, width, name_field in ((1, 20, 12), (13, 32, 4)):  # imports, delay-load imports
        if directories <= index:
            continue
        rva = u32(optional + 112 + 8 * index)
        if not rva:
            continue
        entry = offset_of(rva)
        while any(data[entry:entry + width]):
            names.append(name_at(u32(entry + name_field)))
            entry += width
    return names


def _pe_check(path: Path) -> None:
    with path.open("rb") as handle:
        _pe_machine(handle, path.stat().st_size, path.name)
    redist = [name for name in _pe_imports(path) if name.casefold().startswith(_VC_REDIST)]
    if redist:
        raise DistributionError(f"{path.name} needs the Visual C++ runtime ({', '.join(redist)}), which a clean "
                                "Windows does not have: build it with STATIC_VCRUNTIME=true")


def _tauri_version(repository: Path, app: str = "desktop") -> str:
    return json.loads((repository / app / "src-tauri/tauri.conf.json").read_text("utf-8"))["version"]


def _write_json(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", "utf-8")


def stage_companion(source: Path, output: Path, repository: Path, metadata: dict, reviewed: dict) -> dict[str, Any]:
    """Copy the Companion installer and its release manifest into the version, with the
    notices of what the Companion links and bundles (A08.9). Returns its manifest entry."""
    source = Path(source).absolute()
    try:
        release = read_companion_manifest(source / "release-manifest.json")
    except (OSError, ValueError) as exc:
        raise DistributionError(f"Invalid Companion release manifest: {exc}") from exc
    setup = source / release["filename"]
    if release["webview2_mode"] not in COMPANION_WEBVIEW2_MODES:
        raise DistributionError("The Companion installer must not download WebView2 (webview2_mode: skip)")
    if release["version"] != _tauri_version(repository, "companion"):
        raise DistributionError("The Companion release manifest and companion/tauri.conf.json have different versions")
    if not setup.is_file() or _sha256(setup) != release["sha256"].casefold():
        raise DistributionError("The Companion installer does not match its release manifest")
    # NSIS starts from a 32-bit stub that installs the x64 Companion.
    if _pe_architecture(setup) not in {"x86", "x64"}:
        raise DistributionError("The Companion installer is not a Windows executable")
    (output / COMPANION).mkdir()
    shutil.copy2(source / "release-manifest.json", output / COMPANION_MANIFEST)
    shutil.copy2(setup, output / COMPANION / release["filename"])
    licenses = collect(metadata, output, repository, "companion", reviewed)
    web, web_files = collect_web(output, SITE_PACKAGES, "companion/licenses")
    licenses["licenses"] += web
    licenses["files"] = sorted(licenses["files"] + web_files, key=lambda f: f["path"])
    _write_json(output / COMPANION_LICENSES, licenses)
    return {"version": release["version"], "setup": release["filename"], "sha256": release["sha256"].casefold(),
            "signed": release["signed"], "webview2_mode": release["webview2_mode"],
            "cargo_lock_sha256": licenses["cargo_lock_sha256"], "linked_crates": licenses["linked_crates"],
            "licenses_pending": [p["package"] for p in licenses["pending"]]}


def launcher_rustflags(repository: Path, target_dir: Path) -> str:
    """Encoded RUSTFLAGS that keep local folders (and the user name in them) out
    of Iris.exe: panic locations otherwise record every crate's absolute path.
    When several prefixes match, rustc applies the last one, so specific go last."""
    cargo_home = Path(os.environ.get("CARGO_HOME") or Path.home() / ".cargo")
    rustup_home = Path(os.environ.get("RUSTUP_HOME") or Path.home() / ".rustup")
    remaps = [(cargo_home, "/cargo"), (rustup_home, "/rustup"), (repository, "/iris"), (target_dir, "/target")]
    return "\x1f".join(f"--remap-path-prefix={Path(source).absolute()}={label}" for source, label in remaps)


def build_launcher(repository: Path, target_dir: Path, cargo: str | Path = "cargo") -> Path:
    """Release build of the launcher from the locked, already fetched crates."""
    target_dir = Path(target_dir).absolute()
    # STATIC_VCRUNTIME: tauri-build links the Visual C++ runtime into the exe only when asked
    # (the Tauri CLI asks; plain cargo does not). Without it Iris.exe needs VCRUNTIME140.dll,
    # which a clean Windows does not have (A08.9 QA on a clean Windows 11).
    env = {**os.environ, "CARGO_TARGET_DIR": str(target_dir), "STATIC_VCRUNTIME": "true",
           "CARGO_ENCODED_RUSTFLAGS": launcher_rustflags(repository, target_dir)}
    env.pop("RUSTFLAGS", None)
    subprocess.run([str(cargo), "build", "--release", "--locked", "--offline", "--features", "custom-protocol",
                    "--manifest-path", str(repository / "desktop/src-tauri/Cargo.toml")], env=env, check=True)
    return target_dir / "release" / "iris-desktop.exe"


_DESCRIPTOR_PROBE = (
    "import json, os; from core.runtime_descriptor import load_runtime_descriptor; "
    "from core.profile_paths import PROFILE_FORMAT_VERSION; "
    "r = load_runtime_descriptor(os.environ['IRIS_RUNTIME_DESCRIPTOR']); "
    "print(json.dumps({'mode': r.mode, 'max_relative_path': r.max_relative_path, "
    "'profile_format': PROFILE_FORMAT_VERSION}))"
)


def profile_format(repository: Path) -> int:
    """Profile format the Iris source of this build writes (A08.2 contract)."""
    import re

    text = (repository / "core" / "profile_paths.py").read_text("utf-8")
    match = re.search(r"^PROFILE_FORMAT_VERSION = (\d+)$", text, re.M)
    if not match:
        raise DistributionError("PROFILE_FORMAT_VERSION not found in core/profile_paths.py")
    return int(match.group(1))


def channel_document(url: str, keys: list[dict], name: str = "stable", test: bool = False) -> dict[str, Any]:
    """channel.json: where this version looks for updates and whom it trusts (A08.8)."""
    import re
    import sys

    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    from core.iris_updates import UpdateError, channel_url

    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,31}", name):
        raise DistributionError("Invalid channel name")
    try:
        channel_url(url, test=test)  # the rule the installed client applies
    except UpdateError as exc:
        raise DistributionError(f"Invalid channel URL (HTTPS to a public host; loopback HTTP only "
                                f"for a test channel): {exc}") from exc
    if not keys or any(set(key) != {"keyid", "ed25519"} for key in keys):
        raise DistributionError("A channel needs at least one trusted public key entry")
    return {"schema": 1, "channel": name, "url": url, "keys": keys, "test": bool(test)}


def check_descriptor_with_staged_python(root: Path) -> dict[str, Any]:
    """Load runtime.json with the Iris code of this staging, as the controller will.

    Guards against pairing a launcher and a descriptor with an older runtime that
    does not understand them. Runs the staged interpreter isolated from the
    environment; nothing is written (no bytecode, no profile).
    """
    root = Path(root).absolute()
    environment = {key: value for key, value in os.environ.items()
                   if key.upper() in {"SYSTEMROOT", "WINDIR", "TEMP", "TMP"}}
    environment.update(IRIS_RUNTIME_DESCRIPTOR=str(root / DESCRIPTOR), PYTHONIOENCODING="utf-8")
    result = subprocess.run(
        [str(root / "runtimes/iris/python.exe"), "-E", "-s", "-B", "-X", "utf8", "-c", _DESCRIPTOR_PROBE],
        cwd=str(root / "runtimes/iris/Lib/site-packages"), env=environment,
        capture_output=True, text=True, encoding="utf-8", timeout=120, check=False,
    )
    if result.returncode != 0:
        raise DistributionError(f"The staged Iris rejects runtime.json: {result.stderr.strip()[-600:]}")
    loaded = json.loads(result.stdout.strip().splitlines()[-1])
    if loaded["mode"] != "packaged":
        raise DistributionError("The staged Iris did not load runtime.json as packaged")
    return loaded


def build_staging(runtime: Path, executable: Path, output: Path, *, repository: Path = PROJECT_ROOT,
                  cargo: str | Path = "cargo", metadata: dict | None = None,
                  scan_paths: bool = True, self_check: bool = True,
                  channel: dict | None = None, pin_path: Path | None = None,
                  companion: Path | None = None, companion_metadata: dict | None = None,
                  nsis_dir: Path | None = None) -> dict[str, Any]:
    """``pin_path`` replaces runtime_sources.json for another version's build (A08.8 QA).

    ``companion`` is the folder stage_companion_release.py writes; ``nsis_dir`` the
    NSIS that builds the installers, whose notices the version carries (A08.9).
    """
    runtime, executable, output = Path(runtime).absolute(), Path(executable).absolute(), Path(output).absolute()
    if output.exists():
        raise DistributionError(f"The staging folder must be new: {output}")
    pin = read_json(Path(pin_path) if pin_path else APPROVED_PIN, maximum=64 * 1024)
    verify_runtime(runtime, expected_pin=pin)
    if _tauri_version(repository) != pin["iris"]["version"]:
        raise DistributionError("Iris.exe metadata (tauri.conf.json) and the Iris wheel have different versions")
    _pe_check(executable)
    if metadata is None:
        metadata = cargo_metadata(repository / "desktop/src-tauri/Cargo.toml", cargo)
    if companion is not None and companion_metadata is None:
        companion_metadata = cargo_metadata(repository / "companion/src-tauri/Cargo.toml", cargo)
    nsis_dir = Path(nsis_dir) if nsis_dir else repository / "companion/src-tauri/target/.tauri/NSIS"
    reviewed = load_reviewed(repository)
    shutil.copytree(runtime, output, symlinks=False, copy_function=shutil.copy2)
    try:
        shutil.copy2(executable, output / EXECUTABLE)
        licenses = collect(metadata, output, repository, "desktop", reviewed)
        _write_json(output / DESKTOP_LICENSES, licenses)
        installer = collect_installer(output, repository, nsis_dir, reviewed)
        _write_json(output / INSTALLER_LICENSES, installer)
        companion_entry = (stage_companion(companion, output, repository, companion_metadata, reviewed)
                           if companion is not None else None)
        # README length depends on the limit, which depends on the deepest path;
        # neither changes the other, so measure without the README first.
        longest = max(_max_relative_path(output), windows_path_length(README), windows_path_length(MANIFEST))
        root_limit = WINDOWS_MAX_PATH - 1 - longest
        (output / README).write_bytes(readme(pin["iris"]["version"], root_limit))
        (output / DESCRIPTOR).write_text(json.dumps(descriptor_for(pin, longest), indent=2) + "\n", "utf-8")
        if channel is not None:
            (output / CHANNEL).write_text(json.dumps(channel, indent=2) + "\n", "utf-8")
        if scan_paths:
            # The user's home covers the cargo registry and any other local folder.
            embedded = scan_for_paths(output, [str(runtime), str(repository), str(executable.parent), str(Path.home())])
            if embedded:
                raise DistributionError(f"Files embed a build path and would break when moved: {embedded[:10]}")
        files = []
        for top in sorted(STAGED - {MANIFEST}):
            if not (output / top).exists():
                continue  # channel.json only exists when a channel is configured
            for name in _walk(output, top):
                path = output / name
                files.append({"path": name, "size": path.stat().st_size, "sha256": _sha256(path)})
        manifest = {
            "schema": 1, "component": "iris-windows-staging", "platform": "windows", "architecture": "x86_64",
            "iris": {"version": pin["iris"]["version"], "revision": pin["iris"]["wheel_sha256"][:40],
                     "wheel_sha256": pin["iris"]["wheel_sha256"]},
            "hermes": {"version": pin["hermes"]["version"], "revision": pin["hermes"]["revision"]},
            "runtime": {"inventory_sha256": _sha256(output / INVENTORY_NAME), "pin_sha256": _sha256(output / PIN_NAME)},
            "desktop": {"executable": EXECUTABLE, "sha256": _sha256(output / EXECUTABLE),
                        "version": _tauri_version(repository), "cargo_lock_sha256": licenses["cargo_lock_sha256"],
                        "linked_crates": licenses["linked_crates"],
                        "licenses_pending": [p["package"] for p in licenses["pending"]]},
            "installer": {"licenses_pending": [p["package"] for p in installer["pending"]]},
            "licenses": {"reviewed_sources_sha256": licenses["reviewed_sources_sha256"]},
            "install": {"max_relative_path": longest, "max_root_length": root_limit},
            "profile": {"format_version": profile_format(repository)},
            "files": files,
        }
        if companion_entry is not None:
            manifest["companion"] = companion_entry
        (output / MANIFEST).write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", "utf-8")
        if self_check:
            loaded = check_descriptor_with_staged_python(output)
            if loaded.get("profile_format") != manifest["profile"]["format_version"]:
                raise DistributionError("The staged Iris writes a different profile format than its source")
    except BaseException:
        shutil.rmtree(output, ignore_errors=True)  # a new folder created by this call only
        raise
    return verify_staging(output, expected_pin=pin)


def verify_staging(root: Path, expected_pin: dict | None = None) -> dict[str, Any]:
    """Static check of a (possibly moved) staging: nothing inside is executed."""
    root = Path(root).absolute()
    pin = expected_pin if expected_pin is not None else read_json(APPROVED_PIN, maximum=64 * 1024)
    manifest = read_json(root / MANIFEST, maximum=8 * 1024 * 1024)
    if (manifest.get("schema") != 1 or manifest.get("component") != "iris-windows-staging"
            or manifest.get("platform") != "windows" or manifest.get("architecture") != "x86_64"):
        raise DistributionError("Unknown staging manifest")
    inventory = verify_runtime(root, expected_pin=pin, staged=STAGED)
    if (manifest["runtime"] != {"inventory_sha256": _sha256(root / INVENTORY_NAME), "pin_sha256": _sha256(root / PIN_NAME)}
            or manifest["iris"] != {"version": pin["iris"]["version"], "revision": pin["iris"]["wheel_sha256"][:40],
                                    "wheel_sha256": pin["iris"]["wheel_sha256"]}
            or manifest["hermes"] != {"version": pin["hermes"]["version"], "revision": pin["hermes"]["revision"]}):
        raise DistributionError("Staging manifest does not match its runtime")
    present = {name for top in STAGED - {MANIFEST} if (root / top).exists() or (root / top).is_symlink()
               for name in _walk(root, top)}
    expected = {item["path"]: item for item in manifest["files"]}
    if present != set(expected):
        raise DistributionError(f"Staging files differ: missing={sorted(set(expected) - present)}, "
                                f"unexpected={sorted(present - set(expected))}")
    for name, item in expected.items():
        path = root / name
        if path.stat().st_size != item["size"] or _sha256(path) != item["sha256"]:
            raise DistributionError(f"Incorrect staging hash or size: {name}")
    _pe_check(root / EXECUTABLE)
    if manifest["desktop"]["sha256"] != expected[EXECUTABLE]["sha256"]:
        raise DistributionError("Iris.exe differs from the staging manifest")
    longest = manifest["install"]["max_relative_path"]
    if (type(longest) is not int or longest != _max_relative_path(root)
            or manifest["install"]["max_root_length"] != WINDOWS_MAX_PATH - 1 - longest):
        raise DistributionError("Staging path budget is stale")
    if read_json(root / DESCRIPTOR, maximum=64 * 1024) != descriptor_for(pin, longest):
        raise DistributionError("runtime.json differs from the packaged descriptor of this staging")
    inventories = [DESKTOP_LICENSES, INSTALLER_LICENSES] + ([COMPANION_LICENSES] if "companion" in manifest else [])
    for notices in inventories:
        if notices not in expected:
            raise DistributionError(f"Missing license inventory: {notices}")
        for record in read_json(root / notices, maximum=8 * 1024 * 1024)["files"]:
            if expected.get(record["path"], {}).get("sha256") != record["sha256"]:
                raise DistributionError(f"License file not covered by the manifest: {record['path']}")
    pending = list(manifest["desktop"]["licenses_pending"]) + list(manifest["installer"]["licenses_pending"])
    if "companion" in manifest:
        entry = manifest["companion"]
        release = read_json(root / COMPANION_MANIFEST, maximum=64 * 1024)
        setup = f"{COMPANION}/{entry['setup']}"
        if (release.get("filename") != entry["setup"] or str(release.get("sha256", "")).casefold() != entry["sha256"]
                or release.get("version") != entry["version"] or release.get("signed") is not entry["signed"]
                or release.get("webview2_mode") != entry["webview2_mode"]
                or expected.get(setup, {}).get("sha256") != entry["sha256"]
                or entry["webview2_mode"] not in COMPANION_WEBVIEW2_MODES):
            raise DistributionError("The Companion installer differs from its release manifest")
        pending += entry["licenses_pending"]
    root_length = windows_path_length(root)
    return {
        "root": str(root), "runtime_files": len(inventory["files"]), "staged_files": len(expected),
        "iris": manifest["iris"]["version"], "hermes": manifest["hermes"]["version"],
        "executable_sha256": manifest["desktop"]["sha256"], "max_relative_path": longest,
        "max_root_length": manifest["install"]["max_root_length"], "root_length": root_length,
        "fits": root_length <= manifest["install"]["max_root_length"],
        "licenses_pending": pending,
        "companion": manifest.get("companion", {}).get("version"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runtime", type=Path)
    parser.add_argument("--executable", type=Path, help="prebuilt launcher; otherwise it is built")
    parser.add_argument("--cargo-target-dir", type=Path, help="new or reused cargo target folder for the build")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--cargo", default=os.environ.get("CARGO", "cargo"))
    parser.add_argument("--verify", type=Path)
    parser.add_argument("--channel-url", help="update channel manifest URL (A08.8)")
    parser.add_argument("--channel-key", type=Path, action="append", default=[],
                        help="trusted public key entry (.json from build_update_package.py keygen)")
    parser.add_argument("--channel-name", default="stable")
    parser.add_argument("--channel-test", action="store_true", help="test channel; allows loopback HTTP")
    parser.add_argument("--pin", type=Path, help="approved runtime pin (default scripts/runtime_sources.json)")
    parser.add_argument("--companion", type=Path, help="folder with the Companion installer and release-manifest.json")
    parser.add_argument("--nsis-dir", type=Path, help="NSIS folder that builds the installers")
    args = parser.parse_args(argv)
    if args.verify:
        result = verify_staging(args.verify, expected_pin=read_json(args.pin, maximum=64 * 1024) if args.pin else None)
    elif args.runtime and args.output and (args.executable or args.cargo_target_dir):
        executable = args.executable or build_launcher(PROJECT_ROOT, args.cargo_target_dir, args.cargo)
        channel = None
        if args.channel_url:
            keys = [json.loads(path.read_text("utf-8")) for path in args.channel_key]
            channel = channel_document(args.channel_url, keys, args.channel_name, args.channel_test)
        result = build_staging(args.runtime, executable, args.output, cargo=args.cargo, channel=channel,
                               pin_path=args.pin, companion=args.companion, nsis_dir=args.nsis_dir)
    else:
        parser.error("use --verify, or --runtime, --output and --executable or --cargo-target-dir")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["fits"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
