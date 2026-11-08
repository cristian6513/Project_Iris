"""Release-level guarantees for the optional Desktop Companion."""

from __future__ import annotations

import hashlib
import importlib
import json
import runpy
import tomllib
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
stage_companion_release = runpy.run_path(
    str(PROJECT_ROOT / "scripts" / "stage_companion_release.py")
)["stage_companion_release"]


def test_release_and_launcher_scripts_are_safe_to_import() -> None:
    release_module = importlib.import_module("scripts.build_release")
    assert callable(release_module.main)
    assert callable(importlib.import_module("scripts.start").main)
    with pytest.raises(SystemExit) as help_exit:
        release_module.main(["--help"])
    assert help_exit.value.code == 0


def _write_fake_pe(path: Path, machine: int = 0x014C) -> None:
    payload = bytearray(65_536)
    payload[:2] = b"MZ"
    payload[0x3C:0x40] = (0x80).to_bytes(4, "little")
    payload[0x80:0x84] = b"PE\0\0"
    payload[0x84:0x86] = machine.to_bytes(2, "little")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def _release_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    project = tmp_path / "project"
    filename = "IRIS Desktop Companion_0.1.0_x64-setup.exe"
    manifest_path = project / "companion" / "release-manifest.json"
    installer = project / "companion" / "src-tauri" / "target" / "release" / "bundle" / "nsis" / filename
    _write_fake_pe(installer)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps({
        "schema": 1,
        "product": "IRIS Desktop Companion",
        "version": "0.1.0",
        "platform": "windows",
        "arch": "x64",
        "filename": filename,
        "sha256": hashlib.sha256(installer.read_bytes()).hexdigest(),
        "signed": False,
        "webview2_mode": "downloadBootstrapper",
    }), encoding="utf-8")
    return project, manifest_path, installer


def test_companion_versions_and_windows_update_policy_are_synchronized() -> None:
    manifest = json.loads((PROJECT_ROOT / "companion" / "release-manifest.json").read_text(encoding="utf-8"))
    tauri = json.loads((PROJECT_ROOT / "companion" / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))
    package = json.loads((PROJECT_ROOT / "companion" / "package.json").read_text(encoding="utf-8"))
    cargo = tomllib.loads((PROJECT_ROOT / "companion" / "src-tauri" / "Cargo.toml").read_text(encoding="utf-8"))

    versions = {
        manifest["version"],
        tauri["version"],
        package["version"],
        cargo["package"]["version"],
    }
    assert len(versions) == 1
    assert manifest["filename"] == (
        f"IRIS Desktop Companion_{manifest['version']}_x64-setup.exe"
    )
    windows = tauri["bundle"]["windows"]
    assert windows["allowDowngrades"] is False
    # A08.9: WebView2 is detected and explained by Iris, never downloaded by the
    # Companion installer behind the user's back.
    assert windows["webviewInstallMode"] == {"type": "skip"}
    assert manifest["webview2_mode"] == "skip"

    packaging = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    source_manifest = (PROJECT_ROOT / "MANIFEST.in").read_text(encoding="utf-8")
    assert '"dist/companion/*.exe"' in packaging
    assert "include dist/companion/*.exe" in source_manifest
    release_builder = (PROJECT_ROOT / "scripts" / "build_release.py").read_text(encoding="utf-8")
    assert '"tauri", "--", "build"' in release_builder
    assert "stage_companion_release(" in release_builder
    assert '"--locked"' in release_builder
    assert '"--extra"' in release_builder
    assert '"dev"' in release_builder
    assert '"--no-isolation"' in release_builder
    assert '"-m"' in release_builder and '"build"' in release_builder
    assert "_verify_wheel(manifest, wheels[0])" in release_builder


def test_current_real_nsis_and_staged_copy_match_the_manifest() -> None:
    manifest = json.loads(
        (PROJECT_ROOT / "companion" / "release-manifest.json").read_text(encoding="utf-8")
    )
    source = (
        PROJECT_ROOT
        / "companion"
        / "src-tauri"
        / "target"
        / "release"
        / "bundle"
        / "nsis"
        / manifest["filename"]
    )
    if not source.is_file():
        pytest.skip("El gate completo scripts/build_release.py genera este artefacto en releases")
    staged = PROJECT_ROOT / "dist" / "companion" / manifest["filename"]
    assert staged.is_file()
    assert hashlib.sha256(source.read_bytes()).hexdigest() == manifest["sha256"]
    assert hashlib.sha256(staged.read_bytes()).hexdigest() == manifest["sha256"]


def test_release_staging_copies_manifest_and_verified_nsis(tmp_path: Path) -> None:
    project = tmp_path / "project"
    manifest_path = project / "companion" / "release-manifest.json"
    filename = "IRIS Desktop Companion_0.1.0_x64-setup.exe"
    installer = project / "companion" / "src-tauri" / "target" / "release" / "bundle" / "nsis" / filename
    _write_fake_pe(installer)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps({
        "schema": 1,
        "product": "IRIS Desktop Companion",
        "version": "0.1.0",
        "platform": "windows",
        "arch": "x64",
        "filename": filename,
        "sha256": hashlib.sha256(installer.read_bytes()).hexdigest(),
        "signed": False,
        "webview2_mode": "downloadBootstrapper",
    }), encoding="utf-8")

    output = tmp_path / "release"
    output.mkdir()
    (output / "IRIS Desktop Companion_0.0.9_x64-setup.exe").write_bytes(b"obsolete")
    staged_installer, staged_manifest = stage_companion_release(project, output)
    assert staged_installer.read_bytes() == installer.read_bytes()
    assert json.loads(staged_manifest.read_text(encoding="utf-8"))["sha256"] == hashlib.sha256(staged_installer.read_bytes()).hexdigest()
    assert sorted(path.name for path in output.iterdir()) == [filename, "release-manifest.json"]


def test_release_staging_rejects_a_hash_mismatch(tmp_path: Path) -> None:
    project = tmp_path / "project"
    manifest_path = project / "companion" / "release-manifest.json"
    filename = "setup.exe"
    installer = project / "companion" / "src-tauri" / "target" / "release" / "bundle" / "nsis" / filename
    _write_fake_pe(installer)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps({
        "schema": 1,
        "product": "IRIS Desktop Companion",
        "version": "0.1.0",
        "platform": "windows",
        "arch": "x64",
        "filename": filename,
        "sha256": "0" * 64,
        "signed": False,
        "webview2_mode": "downloadBootstrapper",
    }), encoding="utf-8")

    with pytest.raises(ValueError, match="SHA-256"):
        stage_companion_release(project, tmp_path / "release")


def test_release_staging_refuses_to_replace_the_project_root(tmp_path: Path) -> None:
    project, manifest, installer = _release_fixture(tmp_path)

    with pytest.raises(ValueError, match="subdirectorio dedicado"):
        stage_companion_release(project, project)

    assert manifest.is_file()
    assert installer.is_file()


def test_release_staging_preserves_a_mixed_output_directory(tmp_path: Path) -> None:
    project, _, _ = _release_fixture(tmp_path)
    output = tmp_path / "release"
    output.mkdir()
    foreign = output / "notes.txt"
    foreign.write_text("do not delete", encoding="utf-8")

    with pytest.raises(ValueError, match="archivos ajenos"):
        stage_companion_release(project, output)

    assert foreign.read_text(encoding="utf-8") == "do not delete"


def test_release_staging_rejects_an_invalid_signature_contract(tmp_path: Path) -> None:
    project, manifest_path, _ = _release_fixture(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["signed"] = "false"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="signed"):
        stage_companion_release(project, tmp_path / "release")


def test_release_staging_verifies_a_declared_authenticode_signature(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project, manifest_path, _ = _release_fixture(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["signed"] = True
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setitem(
        stage_companion_release.__globals__,
        "_authenticode_status",
        lambda path: "notsigned",
    )

    with pytest.raises(ValueError, match="Authenticode"):
        stage_companion_release(project, tmp_path / "release")


def test_release_staging_rejects_a_manifest_changed_during_copy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project, manifest_path, _ = _release_fixture(tmp_path)
    original_copy = stage_companion_release.__globals__["shutil"].copy2

    def changing_copy(source: Path, destination: Path):
        if Path(source) == manifest_path:
            document = json.loads(manifest_path.read_text(encoding="utf-8"))
            document["version"] = "0.1.1"
            manifest_path.write_text(json.dumps(document), encoding="utf-8")
        return original_copy(source, destination)

    monkeypatch.setattr(stage_companion_release.__globals__["shutil"], "copy2", changing_copy)

    with pytest.raises(ValueError, match="cambió"):
        stage_companion_release(project, tmp_path / "release")
