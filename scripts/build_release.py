"""Build a release that cannot silently omit the Desktop Companion.

This is the canonical release entry point on Windows:

    python scripts/build_release.py

It builds Tauri/NSIS, refreshes the manifest hash, stages the verified pair,
builds the Python distributions and checks that the wheel contains both files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import tomllib
import zipfile
from pathlib import Path

if __package__:
    from .verify_distribution import verify_archive
    from .distribution_web_assets import prepare_web_assets
    from .stage_companion_release import _read_manifest, stage_companion_release
else:
    from verify_distribution import verify_archive
    from distribution_web_assets import prepare_web_assets
    from stage_companion_release import _read_manifest, stage_companion_release


PROJECT_ROOT = Path(__file__).resolve().parents[1]
COMPANION_ROOT = PROJECT_ROOT / "companion"
MANIFEST_PATH = COMPANION_ROOT / "release-manifest.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().lower()


def _run(command: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> None:
    subprocess.run(command, cwd=cwd, env=env, check=True)


def _release_versions() -> dict[str, str]:
    manifest = _read_manifest(MANIFEST_PATH)
    tauri = json.loads((COMPANION_ROOT / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))
    package = json.loads((COMPANION_ROOT / "package.json").read_text(encoding="utf-8"))
    cargo = tomllib.loads((COMPANION_ROOT / "src-tauri" / "Cargo.toml").read_text(encoding="utf-8"))
    versions = {
        "manifest": str(manifest["version"]),
        "tauri": str(tauri["version"]),
        "package": str(package["version"]),
        "cargo": str(cargo["package"]["version"]),
    }
    if len(set(versions.values())) != 1:
        raise ValueError(f"Versiones del Companion desincronizadas: {versions}")
    expected_suffix = f"_{versions['manifest']}_x64-setup.exe"
    if not str(manifest["filename"]).endswith(expected_suffix):
        raise ValueError("El filename del manifiesto no corresponde a su versión")
    webview_mode = str(
        tauri["bundle"]["windows"]["webviewInstallMode"]["type"]
    )
    if str(manifest["webview2_mode"]) != webview_mode:
        raise ValueError(
            "webview2_mode no coincide con bundle.windows.webviewInstallMode.type"
        )
    return versions


def _refresh_manifest_hash() -> dict[str, object]:
    manifest = _read_manifest(MANIFEST_PATH)
    installer = (
        COMPANION_ROOT
        / "src-tauri"
        / "target"
        / "release"
        / "bundle"
        / "nsis"
        / str(manifest["filename"])
    )
    if not installer.is_file():
        raise FileNotFoundError(f"Tauri no produjo el NSIS esperado: {installer}")
    manifest["sha256"] = _sha256(installer)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=MANIFEST_PATH.parent,
            prefix=".release-manifest.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, MANIFEST_PATH)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
    return manifest


def _verify_wheel(manifest: dict[str, object], wheel: Path) -> Path:
    if not wheel.is_file():
        raise FileNotFoundError("El build no produjo la wheel esperada de IRIS OS")
    verify_archive(wheel)
    filename = str(manifest["filename"])
    with zipfile.ZipFile(wheel) as archive:
        members = archive.namelist()
        manifest_member = next(
            (name for name in members if name.endswith("share/iris-os/companion/release-manifest.json")),
            "",
        )
        installer_member = next(
            (name for name in members if name.endswith(f"share/iris-os/companion/{filename}")),
            "",
        )
        if not manifest_member or not installer_member:
            raise ValueError("La wheel omitió el manifiesto o el NSIS del Companion")
        packaged_manifest = json.loads(archive.read(manifest_member))
        packaged_hash = hashlib.sha256(archive.read(installer_member)).hexdigest()
        contract_fields = (
            "schema", "product", "version", "platform", "arch",
            "filename", "sha256", "signed", "webview2_mode",
        )
        if any(packaged_manifest.get(field) != manifest.get(field) for field in contract_fields):
            raise ValueError("El manifiesto de la wheel no coincide con el release actual")
        if packaged_hash != manifest.get("sha256"):
            raise ValueError("El Companion dentro de la wheel no coincide con su manifiesto")
    return wheel


def _verify_sdist(manifest: dict[str, object], source_distribution: Path) -> Path:
    """Verify the source archive can rebuild the same Companion-bearing wheel."""
    if not source_distribution.is_file():
        raise FileNotFoundError("El build no produjo el sdist esperado de IRIS OS")
    verify_archive(source_distribution)
    filename = str(manifest["filename"])
    with tarfile.open(source_distribution, "r:gz") as archive:
        members = archive.getmembers()
        manifest_member = next(
            (item for item in members if item.name.endswith("companion/release-manifest.json")),
            None,
        )
        installer_member = next(
            (item for item in members if item.name.endswith(f"dist/companion/{filename}")),
            None,
        )
        if not manifest_member or not installer_member:
            raise ValueError("El sdist omitió el manifiesto o el NSIS del Companion")
        manifest_stream = archive.extractfile(manifest_member)
        installer_stream = archive.extractfile(installer_member)
        if not manifest_stream or not installer_stream:
            raise ValueError("No pude leer el contrato del Companion dentro del sdist")
        packaged_manifest = json.loads(manifest_stream.read())
        packaged_hash = hashlib.sha256(installer_stream.read()).hexdigest()
        contract_fields = (
            "schema", "product", "version", "platform", "arch",
            "filename", "sha256", "signed", "webview2_mode",
        )
        if any(packaged_manifest.get(field) != manifest.get(field) for field in contract_fields):
            raise ValueError("El manifiesto del sdist no coincide con el release actual")
        if packaged_hash != manifest.get("sha256"):
            raise ValueError("El Companion dentro del sdist no coincide con su manifiesto")
    return source_distribution


def _atomic_copy(source: Path, destination: Path) -> None:
    """Publish a completed artifact without exposing a partial replacement."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w+b",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            with source.open("rb") as source_handle:
                shutil.copyfileobj(source_handle, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    npm = "npm.cmd" if os.name == "nt" else "npm"
    environment = os.environ.copy()
    cargo_home = Path.home() / ".cargo" / "bin"
    environment["PATH"] = os.pathsep.join((str(cargo_home), environment.get("PATH", "")))
    _release_versions()
    manifest_before_build = _read_manifest(MANIFEST_PATH)
    expected_installer = (
        COMPANION_ROOT / "src-tauri" / "target" / "release" / "bundle" / "nsis"
        / str(manifest_before_build["filename"])
    )
    expected_installer.unlink(missing_ok=True)
    subprocess.run(
        [npm, "run", "tauri", "--", "build"],
        cwd=COMPANION_ROOT,
        env=environment,
        check=True,
    )
    manifest = _refresh_manifest_hash()
    stage_companion_release(PROJECT_ROOT, PROJECT_ROOT / "dist" / "companion")
    uv = shutil.which("uv")
    if not uv:
        raise RuntimeError("uv es obligatorio para construir con uv.lock")
    with tempfile.TemporaryDirectory(prefix="iris-release-") as temporary:
        build_output = Path(temporary)
        node = shutil.which("node")
        if not node:
            raise RuntimeError("Node is required to prepare pinned local web resources")
        web_output = build_output / "web-assets"
        _run(
            [node, str(PROJECT_ROOT / "scripts" / "build_web.mjs"), "--output", str(web_output)],
            cwd=PROJECT_ROOT,
        )
        prepare_web_assets(web_output)
        build_environment = os.environ.copy()
        build_environment["IRIS_WEB_ASSETS_ROOT"] = str(web_output)
        _run(
            [
                uv,
                "run",
                "--locked",
                "--extra",
                "dev",
                "python",
                "-m",
                "build",
                "--no-isolation",
                "--outdir",
                str(build_output),
            ],
            cwd=PROJECT_ROOT,
            env=build_environment,
        )
        wheels = list(build_output.glob("iris_os-*.whl"))
        if len(wheels) != 1:
            raise ValueError("El build debe producir exactamente una wheel de IRIS OS")
        _verify_wheel(manifest, wheels[0])
        source_distributions = list(build_output.glob("iris_os-*.tar.gz"))
        if len(source_distributions) != 1:
            raise ValueError("El build debe producir exactamente un sdist de IRIS OS")
        _verify_sdist(manifest, source_distributions[0])
        release_output = PROJECT_ROOT / "dist"
        release_output.mkdir(parents=True, exist_ok=True)
        published_wheel = release_output / wheels[0].name
        _atomic_copy(wheels[0], published_wheel)
        source_distribution = source_distributions[0]
        _atomic_copy(source_distribution, release_output / source_distribution.name)
    wheel = published_wheel
    print(f"IRIS release verified: {wheel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
