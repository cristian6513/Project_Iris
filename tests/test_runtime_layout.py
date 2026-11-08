"""Private wheel normalization uses synthetic public files, never profiles."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import tomllib
from pathlib import Path

import pytest

from scripts import runtime_layout as layout


def write(root, name, data=b"public synthetic fixture"):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def pe_installer():
    payload = bytearray(65536)
    payload[:2] = b"MZ"
    payload[60:64] = (64).to_bytes(4, "little")
    payload[64:70] = b"PE\0\0\x4c\x01"
    return bytes(payload)


@pytest.fixture
def candidate(tmp_path):
    stage = tmp_path / "candidate"
    sites = {}
    for name in ("iris", "hermes"):
        site = stage / "runtimes" / name / "Lib/site-packages"
        site.mkdir(parents=True)
        sites[name] = site
        write(stage, f"runtimes/{name}/LICENSE.txt", b"CPython notices")
        write(stage, f"runtimes/{name}/Lib/json/__init__.py", b"stdlib source")
    share = sites["iris"] / "share"
    for name in layout._SKILL_FILES:
        write(share, name, b"first-party public skill\n")
    installer = pe_installer()
    filename = "IRIS Desktop Companion_0.1.2_x64-setup.exe"
    write(share, "iris-os/companion/" + filename, installer)
    manifest = {
        "schema": 1, "product": "IRIS Desktop Companion", "platform": "windows",
        "arch": "x64", "version": "0.1.2", "filename": filename,
        "sha256": hashlib.sha256(installer).hexdigest(), "signed": False,
        "webview2_mode": "downloadBootstrapper",
    }
    write(share, layout._COMPANION_MANIFEST, json.dumps(manifest).encode())
    return stage, sites, share


def snapshot(root):
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*") if path.is_file()
    }


def directory_alias(link, target):
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


def remove_alias(link):
    link.rmdir() if os.name == "nt" else link.unlink()


def test_known_launchers_and_foreign_binaries_are_pruned_only_in_candidate(candidate):
    stage, sites, _ = candidate
    for site in sites.values():
        for name in layout.WINDOWS_WHEEL_PRUNE_FILES:
            write(site, name)
        for name in (
            "setuptools/__init__.py", "pythonwin/license.txt", "pythonwin/win32ui.pyd",
            "win32/win32service.pyd", "win32/license.txt",
            "_sounddevice_data/portaudio-binaries/libportaudio64bit.dll",
            "_sounddevice_data/portaudio-binaries/libportaudio64bit-asio.dll",
            "pvporcupine/LICENSE", "pvporcupine/lib/common/porcupine_params.pv",
            "pvporcupine/lib/windows/amd64/libpv_porcupine.dll",
            "pvporcupine/lib/windows/amd64/pv_ypu_impl_cuda_porcupine.dll",
            "pvporcupine/resources/keyword_files/windows/porcupine_windows.ppn",
            "discord/bin/libopus-0.x64.dll",
            "winpty/winpty-agent.exe", "winpty/winpty.dll",
            "nemo_relay/bin/nemo-relay.exe", "new_dependency/tool.exe",
            "av.libs/avcodec.dll", "package.dist-info/licenses/LICENSE",
        ):
            write(site, name)
    before = snapshot(stage)
    result = layout.normalize_runtime_layout(stage)
    removed = set(result["removed"])
    # Each site loses every listed file and its emptied listed directory; +2 Companion.
    assert len(removed) == 2 * (len(layout.WINDOWS_WHEEL_PRUNE_FILES)
                                + len(layout.WINDOWS_WHEEL_PRUNE_EMPTY_DIRECTORIES)) + 2
    for site in ("iris", "hermes"):
        assert f"runtimes/{site}/Lib/site-packages/numpy/distutils/__pycache__" in removed
    after = snapshot(stage)
    for name, payload in before.items():
        if name in removed:
            assert name not in after
        elif name.startswith("runtimes/iris/Lib/site-packages/share/"):
            new_name = name.replace(
                "runtimes/iris/Lib/site-packages/share/", "runtimes/iris/share/"
            )
            assert after[new_name] == payload
        else:
            assert after[name] == payload
    assert result["relocated"] == ["runtimes/iris/share"]
    assert not (sites["iris"] / "share").exists()
    assert not (stage / "runtimes/iris/share/iris-os/companion").exists()
    assert layout.normalize_runtime_layout(stage) == {"removed": [], "relocated": []}
    assert snapshot(stage) == after


def test_current_wheel_skill_files_are_relocated_with_their_exact_contents(candidate):
    stage, _, share = candidate
    root = Path(__file__).resolve().parents[1]
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    published = {}
    for destination, sources in project["tool"]["setuptools"]["data-files"].items():
        if destination.startswith("share/iris-os/hermes-skills/"):
            for source in sources:
                relative = destination.removeprefix("share/") + "/" + Path(source).name
                payload = (root / source).read_bytes()
                write(share, relative, payload)
                published[relative] = payload
    assert published, "The wheel must declare the public factory skill"
    result = layout.normalize_runtime_layout(stage)
    assert result["relocated"] == ["runtimes/iris/share"]
    destination = stage / "runtimes/iris/share"
    assert {name: (destination / name).read_bytes() for name in published} == published
    assert not share.exists()
    assert layout.normalize_runtime_layout(stage) == {"removed": [], "relocated": []}


def test_relocated_sdk_license_and_all_record_rows_resolve_to_their_real_files(candidate):
    from scripts.runtime_licenses import inventory_python_licenses

    stage, sites, share = candidate
    site = sites["iris"]
    info = site / "iris_os-1.0.0.dist-info"
    write(info, "METADATA", b"Metadata-Version: 2.4\nName: iris-os\nVersion: 1.0.0\n"
          b"License: MIT\nProject-URL: Source, https://example.org/iris\n\n")
    write(info, "licenses/LICENSE", b"MIT License\nCopyright Iris contributors\n")
    write(site, "core/example.py", b"# unchanged module\n")
    original = [["core/example.py", "sha256=module", "19"]]
    for path in sorted(share.rglob("*")):
        if path.is_file():
            original.append([path.relative_to(site).as_posix(),
                             "sha256=" + hashlib.sha256(path.read_bytes()).hexdigest(),
                             str(path.stat().st_size)])
    data = io.StringIO(newline="")
    csv.writer(data, lineterminator="\n").writerows(original)
    record = write(info, "RECORD", data.getvalue().encode())

    layout.normalize_runtime_layout(stage)

    rows = list(csv.reader(io.StringIO(record.read_text("utf8"))))
    expected = [(["../../" + row[0], *row[1:]] if row[0].startswith("share/") else row)
                for row in original if not row[0].startswith("share/iris-os/companion/")]
    assert rows == expected
    assert all((site / row[0]).is_file() for row in rows)
    notices = inventory_python_licenses(stage, "iris-runtime", site)
    assert "runtimes/iris/share/iris-os/builder/sdk2/LICENSE" in notices[0]["files"]
    after = snapshot(stage)
    assert layout.normalize_runtime_layout(stage) == {"removed": [], "relocated": []}
    assert snapshot(stage) == after


@pytest.mark.parametrize("name", ["share/iris-os/private.txt", "share/../outside/LICENSE"])
def test_unknown_share_record_is_rejected_before_pruning_or_relocation(candidate, name):
    stage, sites, _ = candidate
    write(sites["iris"], "iris_os-1.0.0.dist-info/RECORD", (name + ",,\n").encode())
    write(sites["hermes"], "setuptools/cli-32.exe")
    before = snapshot(stage)
    with pytest.raises(ValueError, match="RECORD"):
        layout.normalize_runtime_layout(stage)
    assert snapshot(stage) == before


@pytest.mark.parametrize("invalid", [
    "unknown-file", "unknown-directory", "private-file", "missing-file", "empty-file",
    "binary-skill", "manifest-hash", "manifest-schema", "manifest-unknown-platform",
    "manifest-absolute", "manifest-parent", "manifest-backslash", "manifest-drive",
    "manifest-unknown-mode", "bad-pe", "destination-directory", "destination-file",
])
def test_public_data_preflight_fails_before_any_pruning(candidate, invalid):
    stage, sites, share = candidate
    launcher = write(sites["hermes"], "setuptools/cli-32.exe")
    skill = share / sorted(layout._SKILL_FILES)[0]
    manifest_path = share / layout._COMPANION_MANIFEST
    if invalid == "unknown-file":
        write(share, "iris-os/unexpected.txt")
    elif invalid == "unknown-directory":
        (share / "iris-os/empty-extra").mkdir()
    elif invalid == "private-file":
        write(share, "iris-os/credentials.json", b"synthetic, never a real credential")
    elif invalid == "missing-file":
        skill.unlink()
    elif invalid == "empty-file":
        skill.write_bytes(b"")
    elif invalid == "binary-skill":
        skill.write_bytes(b"\xff\xfe")
    elif invalid.startswith("manifest-"):
        doc = json.loads(manifest_path.read_text("utf8"))
        field, value = {
            "manifest-hash": ("sha256", "0" * 64),
            "manifest-schema": ("schema", True),
            "manifest-unknown-platform": ("platform", "linux"),
            "manifest-absolute": ("filename", "/evil.exe"),
            "manifest-parent": ("filename", "../evil.exe"),
            "manifest-backslash": ("filename", "folder\\evil.exe"),
            "manifest-drive": ("filename", "C:evil.exe"),
            "manifest-unknown-mode": ("webview2_mode", "unknown"),
        }[invalid]
        doc[field] = value
        manifest_path.write_text(json.dumps(doc), encoding="utf8")
    elif invalid == "bad-pe":
        installer = share / "iris-os/companion/IRIS Desktop Companion_0.1.2_x64-setup.exe"
        installer.write_bytes(b"not a PE")
        doc = json.loads(manifest_path.read_text("utf8"))
        doc["sha256"] = hashlib.sha256(installer.read_bytes()).hexdigest()
        manifest_path.write_text(json.dumps(doc), encoding="utf8")
    elif invalid == "destination-directory":
        (stage / "runtimes/iris/share").mkdir()
    elif invalid == "destination-file":
        write(stage, "runtimes/iris/share")
    before = snapshot(stage)
    with pytest.raises((ValueError, UnicodeError, FileNotFoundError)):
        layout.normalize_runtime_layout(stage)
    assert snapshot(stage) == before
    assert launcher.exists()


@pytest.mark.parametrize("relative", [
    "runtimes/hermes/Lib/site-packages/setuptools/cli-32.exe",
    "runtimes/iris/Lib/site-packages/share/iris-os/hermes-skills/iris-os/SKILL.md",
    "runtimes/hermes/Lib/site-packages/unrelated-package/data.txt",
])
def test_hardlinks_anywhere_in_private_site_are_rejected_before_changes(
    candidate, tmp_path, relative,
):
    stage, sites, _ = candidate
    launcher = write(sites["iris"], "setuptools/gui.exe")
    path = write(stage, relative)
    outside = tmp_path / "outside.txt"
    outside.hardlink_to(path)
    with pytest.raises(ValueError, match="Non-regular"):
        layout.normalize_runtime_layout(stage)
    assert launcher.exists()
    assert outside.read_bytes() == b"public synthetic fixture"


@pytest.mark.parametrize("relative", [
    "runtimes/hermes/Lib/site-packages/pythonwin",
    "runtimes/iris/Lib/site-packages/share/iris-os/hermes-skills/iris-os/references",
    "runtimes/iris/share",
])
def test_directory_alias_never_deletes_or_relocates_external_data(candidate, tmp_path, relative):
    stage, sites, _ = candidate
    launcher = write(sites["hermes"], "setuptools/gui-32.exe")
    alias = stage / relative
    if alias.exists():
        # Only remove ordinary synthetic files in this fixture's explicit folder.
        for path in alias.iterdir():
            path.unlink()
        alias.rmdir()
    alias.parent.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = write(outside, "keep.txt")
    directory_alias(alias, outside)
    try:
        with pytest.raises(ValueError):
            layout.normalize_runtime_layout(stage)
        assert launcher.exists()
        assert marker.read_bytes() == b"public synthetic fixture"
    finally:
        remove_alias(alias)


def test_parent_alias_and_noncanonical_stage_are_rejected(candidate, tmp_path):
    stage, _, _ = candidate
    before = snapshot(stage)
    alias = tmp_path / "alias"
    directory_alias(alias, stage)
    try:
        with pytest.raises(ValueError):
            layout.normalize_runtime_layout(alias)
    finally:
        remove_alias(alias)
    with pytest.raises(ValueError, match="canonical"):
        layout.normalize_runtime_layout(stage / "runtimes/..")
    assert snapshot(stage) == before


def test_prune_path_directory_is_rejected_and_not_recursively_deleted(candidate):
    stage, sites, _ = candidate
    directory = sites["hermes"] / "win32/pythonservice.exe"
    directory.mkdir(parents=True)
    marker = write(directory, "keep.txt")
    before = snapshot(stage)
    with pytest.raises(ValueError, match="ordinary file"):
        layout.normalize_runtime_layout(stage)
    assert snapshot(stage) == before
    assert marker.exists()


def test_second_call_rejects_extra_public_data_without_touching_existing_files(candidate):
    stage, sites, _ = candidate
    layout.normalize_runtime_layout(stage)
    write(stage, "runtimes/iris/share/iris-os/unexpected.txt")
    launcher = write(sites["hermes"], "setuptools/cli.exe")
    before = snapshot(stage)
    with pytest.raises(ValueError, match="public Iris factory data"):
        layout.normalize_runtime_layout(stage)
    assert snapshot(stage) == before
    assert launcher.exists()


@pytest.mark.parametrize("target", ["skill", "manifest"])
def test_factory_size_limits_fail_before_mutation(candidate, target):
    stage, sites, share = candidate
    launcher = write(sites["hermes"], "setuptools/gui.exe")
    if target == "skill":
        write(share, sorted(layout._SKILL_FILES)[0], b"x" * (layout._MAX_SKILL_BYTES + 1))
    else:
        manifest = share / layout._COMPANION_MANIFEST
        manifest.write_bytes(b" " * (64 * 1024 + 1))
    before = snapshot(stage)
    with pytest.raises(ValueError):
        layout.normalize_runtime_layout(stage)
    assert snapshot(stage) == before
    assert launcher.exists()


def test_invalid_other_runtime_is_checked_before_first_prune(candidate):
    stage, sites, _ = candidate
    launcher = write(sites["iris"], "setuptools/cli-32.exe")
    (sites["hermes"] / "package").mkdir()
    invalid = sites["hermes"] / "package/data.txt"
    invalid.write_bytes(b"fixture")
    alias = sites["hermes"] / "package/alias.txt"
    alias.hardlink_to(invalid)
    before = snapshot(stage)
    with pytest.raises(ValueError, match="Non-regular"):
        layout.normalize_runtime_layout(stage)
    assert snapshot(stage) == before
    assert launcher.exists()
