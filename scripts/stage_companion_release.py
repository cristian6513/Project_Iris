"""Stage the optional Windows Companion beside its verified manifest.

Run this after ``npm run tauri build`` and before building the IRIS Python
distribution. The wizard never downloads or compiles the Companion at runtime;
therefore a release must deliberately ship these two files together.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().lower()


def _pe_architecture(path: Path) -> str:
    try:
        size = path.stat().st_size
        if size < 65_536:
            return ""
        with path.open("rb") as handle:
            header = handle.read(64)
            if len(header) != 64 or header[:2] != b"MZ":
                return ""
            offset = int.from_bytes(header[0x3C:0x40], "little")
            if offset < 64 or offset + 6 > size:
                return ""
            handle.seek(offset)
            signature = handle.read(6)
    except OSError:
        return ""
    if len(signature) != 6 or signature[:4] != b"PE\0\0":
        return ""
    return {0x014C: "x86", 0x8664: "x64"}.get(
        int.from_bytes(signature[4:6], "little"),
        "",
    )


def _read_manifest(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("release-manifest.json debe contener un objeto JSON")
    expected = {
        "schema": 1,
        "product": "IRIS Desktop Companion",
        "platform": "windows",
        "arch": "x64",
    }
    for field, value in expected.items():
        if document.get(field) != value:
            raise ValueError(f"campo {field!r} inválido en release-manifest.json")
    filename = str(document.get("filename") or "")
    if Path(filename).name != filename or not filename.casefold().endswith(".exe"):
        raise ValueError("filename inválido en release-manifest.json")
    expected_hash = str(document.get("sha256") or "").casefold()
    if len(expected_hash) != 64 or any(character not in "0123456789abcdef" for character in expected_hash):
        raise ValueError("sha256 inválido en release-manifest.json")
    version = str(document.get("version") or "").strip()
    if not re.fullmatch(r"\d+(?:\.\d+){1,3}", version):
        raise ValueError("version inválida en release-manifest.json")
    if type(document.get("signed")) is not bool:
        raise ValueError("signed debe ser booleano en release-manifest.json")
    if document.get("webview2_mode") not in {
        "downloadBootstrapper",
        "embedBootstrapper",
        "offlineInstaller",
        "fixedRuntime",
        "skip",
    }:
        raise ValueError("webview2_mode inválido en release-manifest.json")
    return document


def _authenticode_status(path: Path) -> str:
    """Return the OS-verified Authenticode status for release gating."""
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    if os.name != "nt" or not powershell:
        return "unverified"
    try:
        result = subprocess.run(
            [
                powershell,
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "(Get-AuthenticodeSignature -LiteralPath $env:IRIS_COMPANION_TARGET).Status.ToString()",
            ],
            capture_output=True,
            text=True,
            timeout=12,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            env={**os.environ, "IRIS_COMPANION_TARGET": str(path)},
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unverified"
    return (result.stdout or "").strip().casefold() or "unverified"


def _validate_output_directory(project_root: Path, output: Path, filename: str) -> None:
    """Refuse broad or mixed destinations before transactional replacement."""
    root = project_root.resolve()
    destination = output.resolve()
    if destination == root or destination in root.parents:
        raise ValueError("--output debe apuntar a un subdirectorio dedicado, no al proyecto")
    if destination.name.casefold() in {"dist", "build", "target", "src"}:
        raise ValueError("--output debe ser un subdirectorio dedicado al Companion")
    if not destination.exists():
        return
    allowed = {"release-manifest.json", filename.casefold()}
    for entry in destination.iterdir():
        name = entry.name.casefold()
        is_previous_installer = bool(
            entry.is_file()
            and re.fullmatch(r"iris desktop companion_.+_x64-setup\.exe", name)
        )
        if entry.is_dir() or (name not in allowed and not is_previous_installer):
            raise ValueError(
                "--output contiene archivos ajenos; usa un directorio exclusivo del Companion"
            )


def _remove_known_release_directory(directory: Path, filename: str) -> None:
    """Remove only known release files; never recursively delete a raced path."""
    allowed = {"release-manifest.json", filename.casefold()}
    for entry in tuple(directory.iterdir()):
        name = entry.name.casefold()
        is_previous_installer = bool(
            entry.is_file()
            and re.fullmatch(r"iris desktop companion_.+_x64-setup\.exe", name)
        )
        if entry.is_file() and (name in allowed or is_previous_installer):
            entry.unlink()
    directory.rmdir()


def _replace_staged_directory(
    staged: Path,
    destination: Path,
    project_root: Path,
    filename: str,
) -> None:
    """Publish the pair while preserving any concurrently introduced data."""
    backup: Path | None = None
    try:
        if destination.exists():
            backup = Path(tempfile.mkdtemp(
                dir=destination.parent,
                prefix=f".{destination.name}.backup.",
            ))
            backup.rmdir()
            os.replace(destination, backup)
            # Validate the directory object that was actually moved, closing
            # the check/replace race. If it contains anything foreign, restore
            # it untouched and refuse publication.
            try:
                _validate_output_directory(project_root, backup, filename)
            except Exception:
                os.replace(backup, destination)
                backup = None
                raise
        os.replace(staged, destination)
    except Exception:
        if backup and backup.exists() and not destination.exists():
            os.replace(backup, destination)
        raise
    else:
        if backup:
            # No recursive delete: a file raced into the backup is preserved
            # and makes rmdir fail visibly instead of being destroyed.
            _remove_known_release_directory(backup, filename)


def stage_companion_release(project_root: Path, output: Path) -> tuple[Path, Path]:
    manifest_source = project_root / "companion" / "release-manifest.json"
    manifest = _read_manifest(manifest_source)
    filename = str(manifest["filename"])
    _validate_output_directory(project_root, output, filename)
    installer_source = (
        project_root
        / "companion"
        / "src-tauri"
        / "target"
        / "release"
        / "bundle"
        / "nsis"
        / filename
    )
    if not installer_source.is_file():
        raise FileNotFoundError(f"No existe el instalador NSIS esperado: {installer_source}")
    if _sha256(installer_source) != str(manifest["sha256"]).casefold():
        raise ValueError("El SHA-256 del instalador no coincide con release-manifest.json")
    # NSIS uses an x86 bootstrapper for the current x64 payload. The installed
    # executable is validated as x64 by the lifecycle backend after setup.
    if _pe_architecture(installer_source) not in {"x86", "x64"}:
        raise ValueError("El instalador no es un ejecutable PE válido")
    if manifest["signed"] and _authenticode_status(installer_source) != "valid":
        raise ValueError("El manifiesto declara firma Authenticode, pero el instalador no es válido")

    output.parent.mkdir(parents=True, exist_ok=True)
    staged = Path(tempfile.mkdtemp(
        dir=output.parent,
        prefix=f".{output.name}.staging.",
    ))
    try:
        installer_destination = staged / filename
        manifest_destination = staged / "release-manifest.json"
        shutil.copy2(installer_source, installer_destination)
        shutil.copy2(manifest_source, manifest_destination)
        if _sha256(installer_destination) != str(manifest["sha256"]).casefold():
            raise ValueError("La copia preparada no conserva el SHA-256 del instalador")
        if _read_manifest(manifest_destination) != manifest:
            raise ValueError("El manifiesto cambió mientras se preparaba el release")
        # Recheck after staging so a concurrently-created foreign file cannot
        # turn the atomic publish into deletion of an unrelated directory.
        _validate_output_directory(project_root, output, filename)
        _replace_staged_directory(staged, output, project_root, filename)
    finally:
        if staged.exists():
            shutil.rmtree(staged, ignore_errors=True)
    installer_destination = output / filename
    manifest_destination = output / "release-manifest.json"
    return installer_destination, manifest_destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    parser.add_argument("--output", type=Path, default=None)
    arguments = parser.parse_args()
    project_root = arguments.project_root.resolve()
    output = (arguments.output or project_root / "dist" / "companion").resolve()
    installer, manifest = stage_companion_release(project_root, output)
    print(f"Companion staged: {installer}")
    print(f"Manifest staged: {manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
