"""A08.9 Windows installer: one verified version folder in a per-user NSIS installer.

    python scripts/build_installer.py --staging <version folder> --output <folder>
        [--pin <runtime pin of that version>] [--nsis-dir <NSIS>]

The version folder must pass build_staging.py's static check and the full
verification the installer itself repeats (core.iris_install.verify_version),
with no license notice pending. NSIS must be exactly the reviewed toolchain
(scripts/desktop_license_sources.json, tree hash): the installer carries its
code. No certificate is used, so the file is named and recorded "sin firma".
Writes ``<installer>.exe`` and ``<installer>.exe.json`` (hashes and provenance);
never overwrites either.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

if __package__:
    from .build_staging import COMPANION_WEBVIEW2_MODES, verify_staging
    from .desktop_licenses import load_reviewed, tree_digest
    from .distribution_assets import DistributionError
    from .prepare_private_runtime import interpreter_path_file
    from .runtime_inventory import read_json
else:
    from build_staging import COMPANION_WEBVIEW2_MODES, verify_staging
    from desktop_licenses import load_reviewed, tree_digest
    from distribution_assets import DistributionError
    from prepare_private_runtime import interpreter_path_file
    from runtime_inventory import read_json

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "desktop" / "installer" / "iris-os.nsi"
ICON = PROJECT_ROOT / "companion" / "src-tauri" / "icons" / "icon.ico"
DEFAULT_NSIS = PROJECT_ROOT / "companion" / "src-tauri" / "target" / ".tauri" / "NSIS"
VERSION_PREFIX = len("\\v\\00000000")  # the installation keeps each version in v\<id> (A08.8)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def installer_name(version: str) -> str:
    return f"IrisOS-{version}-beta-x64-sin-firma-setup.exe"


def check_nsis(nsis_dir: Path, repository: Path = PROJECT_ROOT) -> tuple[str, int]:
    """The NSIS folder must be the reviewed one, file for file."""
    expected = load_reviewed(repository)["installer"].get("NSIS==3.11", {}).get("tool", {})
    found = tree_digest(nsis_dir)
    if found != (expected.get("tree_sha256"), expected.get("files")):
        raise DistributionError(f"NSIS in {nsis_dir} is not the reviewed toolchain (tree {found[0][:12]}…, "
                                f"{found[1]} files)")
    return found


def defines(staging: Path, output: Path) -> dict[str, str]:
    """The /D values the NSIS script expects, from the version's own manifest."""
    sys.path.insert(0, str(PROJECT_ROOT))
    from core import iris_install

    manifest = read_json(staging / iris_install.STAGING_MANIFEST, maximum=8 * 1024 * 1024)
    version_id = iris_install.version_id(staging)
    version = manifest["iris"]["version"]
    parts = (version.split(".") + ["0", "0", "0"])[:4]
    if not all(part.isdigit() for part in parts):
        raise DistributionError(f"Version {version!r} cannot be a Windows file version")
    max_root = manifest["install"]["max_root_length"] - VERSION_PREFIX
    if max_root < 20:
        raise DistributionError("The version leaves no room for an installation folder")
    return {"VERSION": version, "VIVERSION": ".".join(parts), "VERSION_ID": version_id,
            "STAGED": "_" + version_id[1:], "STAGED_TAIL": version_id[1:], "PAYLOAD": str(staging),
            "MAX_ROOT": str(max_root), "OUTFILE": str(output / installer_name(version)), "ICON": str(ICON)}


def check_search_path(staging: Path, script: str | None = None) -> None:
    """The uninstaller only runs a version whose Python search path is the shipped one.

    Refuse to build an installer whose own runtime its uninstaller would reject, and keep
    the script's copy of python311._pth equal to the contract that writes it.
    """
    expected = interpreter_path_file(hermes=False)
    text = SCRIPT.read_text("utf-8") if script is None else script
    pth = re.search(r'^!define IRIS_PTH "(.*)"$', text, re.M)
    size = re.search(r"^!define IRIS_PTH_BYTES (\d+)$", text, re.M)
    script_text = pth.group(1).replace("$\\n", "\n") if pth else None
    if script_text != expected or not size or int(size.group(1)) != len(expected.encode("utf-8")):
        raise DistributionError("The installer script's python311._pth differs from the runtime contract")
    runtime = staging / "runtimes" / "iris"
    if (runtime / "python311._pth").read_bytes() != expected.encode("utf-8"):
        raise DistributionError("The Iris runtime's python311._pth is not the shipped search path")
    extra = [path.name for folder in (runtime, runtime / "Lib" / "site-packages") for path in folder.iterdir()
             if path.name.lower().endswith(".pth") or (path.name.lower().endswith("._pth")
                                                       and path.parent == runtime and path.name != "python311._pth")]
    extra += [str(path) for path in (runtime / "pyvenv.cfg", runtime.parent / "pyvenv.cfg") if path.exists()]
    if extra:
        raise DistributionError(f"The Iris runtime would change its search path: {extra}")


def authenticode_status(path: Path) -> str:
    if os.name != "nt":
        return "unverified"
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                             "(Get-AuthenticodeSignature -LiteralPath $env:IRIS_INSTALLER).Status.ToString()"],
                            capture_output=True, text=True, timeout=60, env={**os.environ, "IRIS_INSTALLER": str(path)},
                            check=False)
    return result.stdout.strip() or "unverified"


def build_installer(staging: Path, output: Path, *, pin: Path | None = None, nsis_dir: Path = DEFAULT_NSIS,
                    makensis: Path | None = None) -> dict[str, Any]:
    staging, output = Path(staging).absolute(), Path(output).absolute()
    sys.path.insert(0, str(PROJECT_ROOT))
    from core import iris_install

    checked = verify_staging(staging, expected_pin=read_json(pin, maximum=64 * 1024) if pin else None)
    if checked["licenses_pending"]:
        raise DistributionError(f"License notices still pending: {checked['licenses_pending']}")
    info = iris_install.verify_version(staging)  # every file, as the installer will
    manifest = read_json(staging / iris_install.STAGING_MANIFEST, maximum=8 * 1024 * 1024)
    companion = manifest.get("companion")
    if companion is None or companion["webview2_mode"] not in COMPANION_WEBVIEW2_MODES:
        raise DistributionError("The version must carry the Companion, which must not download WebView2")
    check_search_path(staging)
    tree = check_nsis(nsis_dir)
    values = defines(staging, output)
    target = Path(values["OUTFILE"])
    record = target.with_name(target.name + ".json")
    if target.exists() or record.exists():
        raise DistributionError(f"Refusing to overwrite {target}")
    output.mkdir(parents=True, exist_ok=True)
    command = [str(makensis or Path(nsis_dir) / "makensis.exe"), "/V2", "/INPUTCHARSET", "UTF8",
               *(f"/D{key}={value}" for key, value in values.items()), str(SCRIPT)]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    (output / (target.name + ".log")).write_text(result.stdout + result.stderr, "utf-8")
    if result.returncode != 0 or not target.is_file():
        raise DistributionError(f"makensis failed ({result.returncode}): {(result.stdout + result.stderr)[-1500:]}")
    document = {
        "schema": 1, "product": "Iris OS", "publisher": "Iris OS", "version": values["VERSION"],
        "version_id": values["VERSION_ID"], "staging_sha256": info["staging_sha256"],
        "hermes": {"version": info["hermes"], "revision": info["hermes_revision"]},
        "companion": {"version": companion["version"], "sha256": companion["sha256"]},
        "installer": {"file": target.name, "size": target.stat().st_size, "sha256": _sha256(target)},
        "signed": False, "authenticode": authenticode_status(target),
        "per_user": True, "max_install_root": int(values["MAX_ROOT"]), "webview2": "detect-and-inform",
        "nsis": {"version": "3.11", "tree_sha256": tree[0], "files": tree[1]},
        "script_sha256": _sha256(SCRIPT),
    }
    if document["authenticode"] not in {"NotSigned", "unverified"}:
        raise DistributionError(f"Unexpected Authenticode status for an unsigned build: {document['authenticode']}")
    record.write_text(json.dumps(document, indent=1, ensure_ascii=False) + "\n", "utf-8")
    return document


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--staging", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--pin", type=Path, help="runtime pin of that version (default scripts/runtime_sources.json)")
    parser.add_argument("--nsis-dir", type=Path, default=DEFAULT_NSIS)
    args = parser.parse_args(argv)
    print(json.dumps(build_installer(args.staging, args.output, pin=args.pin, nsis_dir=args.nsis_dir), indent=1,
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
