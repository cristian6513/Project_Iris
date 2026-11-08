"""A08.7 movable staging: synthetic runtime and crates; nothing is executed."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from core.runtime_descriptor import load_runtime_descriptor
from scripts import build_staging as staging
from scripts import desktop_licenses
from scripts.desktop_licenses import DesktopLicenseError, collect, linked_packages
from scripts.distribution_assets import DistributionError
from scripts.runtime_inventory import INVENTORY_NAME, PIN_NAME, build_inventory
from tests.test_runtime_inventory import fixture_pin, pe, source_files

HERMES_REV = "b" * 40


def _pin():
    pin = fixture_pin()
    pin["iris"]["wheel_sha256"] = "c" * 64
    return pin


def _runtime(root, extra=None):
    files = {
        "runtimes/iris/python.exe": pe(),
        "runtimes/hermes/python.exe": pe(),
        "runtimes/iris/Lib/site-packages/core/desktop.py": b"# synthetic Iris\n",
        "runtimes/iris/Lib/site-packages/face/index.html": b"<!doctype html>\n",
        "hermes/hermes_cli/__init__.py": b"# synthetic Hermes\n",
        "hermes/iris_cli.py": b"# synthetic entry point\n",
        "hermes/runtime-pin.json": json.dumps({"schema_version": 1, "version": "0.21.4", "revision": HERMES_REV}).encode(),
        "tools/audio/bin/ffmpeg.exe": pe(),
        "licenses/FIXTURE.txt": b"Synthetic notice.\n",
        **source_files(),
        PIN_NAME: json.dumps(_pin()).encode(),
        **(extra or {}),
    }
    for name, payload in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    licenses = [{"component": "fixture", "package": "fixture", "version": "1.0.0", "license": "MIT",
                 "files": ["licenses/FIXTURE.txt"], "source": "https://example.test/license"}]
    executables = sorted(name for name in files if name.endswith(".exe"))
    document = build_inventory(root, _pin(), licenses, executables)
    (root / INVENTORY_NAME).write_text(json.dumps(document), "utf-8")
    return root


def _crate(root, name, version, *, license_files=("LICENSE-MIT",), proc_macro=False):
    folder = root / "registry" / f"{name}-{version}"
    folder.mkdir(parents=True)
    (folder / "Cargo.toml").write_text(f'[package]\nname = "{name}"\n', "utf-8")
    for filename in license_files:
        (folder / filename).write_text(f"{name} license text\n", "utf-8")
    kind = ["proc-macro"] if proc_macro else ["lib"]
    return {"id": f"{name} {version}", "name": name, "version": version, "license": "MIT OR Apache-2.0",
            "license_file": None, "repository": f"https://example.test/{name}",
            "manifest_path": str(folder / "Cargo.toml"), "targets": [{"kind": kind}]}


def _metadata(root):
    packages = [
        {"id": "iris-desktop", "name": "iris-desktop", "version": "0.1.0", "license": None,
         "manifest_path": str(root / "Cargo.toml"), "targets": [{"kind": ["bin"]}]},
        _crate(root, "tauri", "2.9.0", license_files=("LICENSE_MIT", "LICENSE_APACHE-2.0")),
        _crate(root, "serde_derive", "1.0.0", proc_macro=True),
        _crate(root, "cc", "1.2.0"),
        _crate(root, "unic-common", "0.9.0", license_files=()),
        _crate(root, "webview2-com-sys", "0.38.2"),
    ]
    normal, build = [{"kind": None, "target": None}], [{"kind": "build", "target": None}]
    nodes = [
        {"id": "iris-desktop", "deps": [
            {"pkg": "tauri 2.9.0", "dep_kinds": normal}, {"pkg": "serde_derive 1.0.0", "dep_kinds": normal},
            {"pkg": "cc 1.2.0", "dep_kinds": build}, {"pkg": "webview2-com-sys 0.38.2", "dep_kinds": normal}]},
        {"id": "tauri 2.9.0", "deps": [{"pkg": "unic-common 0.9.0", "dep_kinds": normal}]},
        *({"id": p["id"], "deps": []} for p in packages[2:]),
    ]
    return {"packages": packages, "resolve": {"root": "iris-desktop", "nodes": nodes}}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    pin_file = tmp_path / "runtime_sources.json"
    pin_file.write_text(json.dumps(_pin()), "utf-8")
    monkeypatch.setattr(staging, "APPROVED_PIN", pin_file)
    repository = tmp_path / "repo"
    (repository / "desktop/src-tauri").mkdir(parents=True)
    (repository / "desktop/third-party").mkdir(parents=True)
    (repository / "desktop/src-tauri/tauri.conf.json").write_text(json.dumps({"version": "1.0.0"}), "utf-8")
    (repository / "desktop/src-tauri/Cargo.lock").write_text("# lock\n", "utf-8")
    (repository / "desktop/third-party/Syne-OFL-1.1.txt").write_text("SIL Open Font License 1.1\n", "utf-8")
    (repository / "core").mkdir()
    (repository / "core/profile_paths.py").write_text("PROFILE_FORMAT_VERSION = 1\n", "utf-8")
    runtime = _runtime(tmp_path / "candidate r99")
    executable = tmp_path / "build" / "iris-desktop.exe"
    executable.parent.mkdir()
    executable.write_bytes(pe() + b"launcher")
    return {"runtime": runtime, "executable": executable, "repository": repository,
            "metadata": _metadata(tmp_path), "out": tmp_path / "staging"}


def _build(setup, **kwargs):
    # Synthetic interpreters cannot run; the staged-Python check has its own test.
    return staging.build_staging(setup["runtime"], setup["executable"], setup["out"], repository=setup["repository"],
                                 metadata=setup["metadata"], self_check=False, **kwargs)


def test_staging_contains_launcher_descriptor_and_verified_runtime(setup):
    result = _build(setup)
    root = setup["out"]
    assert result["runtime_files"] > 10 and result["iris"] == "1.0.0" and result["fits"]
    assert (root / "Iris.exe").read_bytes() == setup["executable"].read_bytes()
    manifest = json.loads((root / staging.MANIFEST).read_text("utf-8"))
    longest = manifest["install"]["max_relative_path"]
    assert json.loads((root / "runtime.json").read_text("utf-8")) == staging.descriptor_for(_pin(), longest)
    assert manifest["install"]["max_root_length"] == 258 - longest
    assert manifest["desktop"]["linked_crates"] == 3  # tauri, unic-common, webview2-com-sys
    assert manifest["desktop"]["licenses_pending"] == ["unic-common", "webview2-com-sys"]
    assert "licenses" in (root / "LEEME.txt").read_text("utf-8")
    # The A08.4 runtime inside is byte-identical and keeps its inventory next to the descriptor.
    assert (root / INVENTORY_NAME).read_bytes() == (setup["runtime"] / INVENTORY_NAME).read_bytes()


def test_moved_staging_still_verifies_and_resolves_inside_its_new_folder(setup, tmp_path):
    _build(setup)
    moved = tmp_path / "Programas Iris ñ" / "Iris OS movida"
    moved.parent.mkdir()
    shutil.move(str(setup["out"]), str(moved))
    assert staging.verify_staging(moved)["fits"]
    runtime = load_runtime_descriptor(moved / "runtime.json")
    assert runtime.packaged and runtime.base == moved.resolve()
    assert runtime.iris_python == (moved / "runtimes/iris/python.exe").resolve()
    assert runtime.hermes.cli == (moved / "hermes/iris_cli.py").resolve()
    assert runtime.max_relative_path == json.loads((moved / staging.MANIFEST).read_text("utf-8"))["install"]["max_relative_path"]


@pytest.mark.parametrize("change", ["launcher", "descriptor", "extra_root", "extra_desktop", "runtime_file",
                                    "license_text", "missing_readme"])
def test_any_change_to_the_staging_is_detected(setup, change):
    _build(setup)
    root = setup["out"]
    if change == "launcher":
        (root / "Iris.exe").write_bytes(pe() + b"other")
    elif change == "descriptor":
        doc = json.loads((root / "runtime.json").read_text("utf-8"))
        doc["hermes"]["cli"] = "hermes/hermes_cli/__init__.py"
        (root / "runtime.json").write_text(json.dumps(doc), "utf-8")
    elif change == "extra_root":
        (root / "notas.txt").write_text("x", "utf-8")
    elif change == "extra_desktop":
        (root / "desktop/extra.txt").write_text("x", "utf-8")
    elif change == "runtime_file":
        (root / "hermes/iris_cli.py").write_text("# changed\n", "utf-8")
    elif change == "license_text":
        next((root / "desktop/licenses").rglob("LICENSE_MIT")).write_text("changed\n", "utf-8")
    elif change == "missing_readme":
        (root / "LEEME.txt").unlink()
    with pytest.raises(DistributionError):
        staging.verify_staging(root)


def test_existing_output_and_version_mismatch_are_refused_without_leftovers(setup):
    setup["out"].mkdir()
    with pytest.raises(DistributionError, match="must be new"):
        _build(setup)
    setup["out"].rmdir()
    (setup["repository"] / "desktop/src-tauri/tauri.conf.json").write_text(json.dumps({"version": "0.1.0"}), "utf-8")
    with pytest.raises(DistributionError, match="different versions"):
        _build(setup)
    assert not setup["out"].exists()


def test_a_runtime_that_records_its_build_folder_is_not_movable(setup, tmp_path):
    source = tmp_path / "candidate con ruta"
    _runtime(source, {"runtimes/iris/Lib/site-packages/broken.pth": str(source).encode("utf-8") + b"\n"})
    setup["runtime"] = source
    with pytest.raises(DistributionError, match="embed a build path"):
        _build(setup)
    assert not setup["out"].exists()


def test_path_scan_finds_utf16_and_forward_slash_variants(tmp_path):
    root = tmp_path / "scan"
    root.mkdir()
    needle = "C:\\Build\\Iris Ñ"
    (root / "a.bin").write_bytes(b"\0\0" + needle.encode("utf-16-le") + b"\0")
    (root / "b.txt").write_text("path=c:/build/iris ñ/x", "utf-8")
    (root / "c.txt").write_text("unrelated", "utf-8")
    assert staging.scan_for_paths(root, [needle]) == ["a.bin", "b.txt"]


def test_tampered_runtime_candidate_is_rejected_before_copying(setup):
    (setup["runtime"] / "hermes/iris_cli.py").write_text("# tampered\n", "utf-8")
    with pytest.raises(DistributionError):
        _build(setup)
    assert not setup["out"].exists()


def test_only_linked_crates_are_listed_and_missing_notices_stay_pending(tmp_path, setup):
    names = [p["name"] for p in linked_packages(setup["metadata"])]
    assert names == ["tauri", "unic-common", "webview2-com-sys"]  # no proc-macro, no build-only crate
    out = tmp_path / "notices"
    document = collect(setup["metadata"], out, setup["repository"])
    by_name = {r["package"]: r for r in document["licenses"]}
    assert by_name["tauri"]["files"] == ["desktop/licenses/tauri-2.9.0/LICENSE_APACHE-2.0",
                                         "desktop/licenses/tauri-2.9.0/LICENSE_MIT"]
    assert by_name["unic-common"]["files"] == [] and by_name["Syne"]["license"] == "OFL-1.1"
    pending = {p["package"]: p["reason"] for p in document["pending"]}
    assert "no incluye archivo de licencia" in pending["unic-common"]
    assert "WebView2" in pending["webview2-com-sys"]
    for item in document["files"]:
        assert hashlib.sha256((out / item["path"]).read_bytes()).hexdigest() == item["sha256"]


def test_verify_reports_whether_the_current_folder_fits(setup, monkeypatch):
    _build(setup)
    result = staging.verify_staging(setup["out"])
    assert result["root_length"] == staging.windows_path_length(setup["out"].absolute())
    real = staging.windows_path_length
    root = str(setup["out"].absolute())
    monkeypatch.setattr(staging, "windows_path_length", lambda value: 10_000 if str(value) == root else real(value))
    assert staging.verify_staging(setup["out"])["fits"] is False


def test_desktop_licenses_feed_the_wizard_catalog(setup, monkeypatch):
    _build(setup)
    monkeypatch.setenv("IRIS_RUNTIME_DESCRIPTOR", str(setup["out"] / "runtime.json"))
    from wizard import licenses

    licenses._cache.clear()
    catalog = licenses.license_catalog()
    desktop = [c for c in catalog["components"] if c["group"] == "desktop"]
    assert {c["package"] for c in desktop} >= {"tauri", "Syne", "webview2-com-sys"}
    assert catalog["complete"] is False  # two notices are still pending review
    source = next(s for s in catalog["sources"] if s["id"] == "desktop")
    assert source["available"] and source["pending"] == 2
    text = licenses.license_text("desktop", "desktop/licenses/tauri-2.9.0/LICENSE_MIT")
    assert text["text"].strip() == "tauri license text"


def test_staged_python_check_reports_a_runtime_that_rejects_the_descriptor(tmp_path, monkeypatch):
    import subprocess

    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 1, "", "RuntimeDescriptorError: Campos de runtime inválidos")

    monkeypatch.setattr(staging.subprocess, "run", run)
    with pytest.raises(DistributionError, match="rejects runtime.json"):
        staging.check_descriptor_with_staged_python(tmp_path)
    command, kwargs = calls[0]
    assert command[0] == str(tmp_path / "runtimes/iris/python.exe")
    assert command[1:6] == ["-E", "-s", "-B", "-X", "utf8"]
    assert kwargs["env"]["IRIS_RUNTIME_DESCRIPTOR"] == str(tmp_path / "runtime.json")
    assert "PYTHONPATH" not in kwargs["env"] and "PATH" not in kwargs["env"]


def test_build_runs_the_staged_python_check_and_cleans_up_on_failure(setup, monkeypatch):
    monkeypatch.setattr(staging, "check_descriptor_with_staged_python",
                        lambda root: (_ for _ in ()).throw(DistributionError("rejects runtime.json")))
    with pytest.raises(DistributionError, match="rejects"):
        staging.build_staging(setup["runtime"], setup["executable"], setup["out"], repository=setup["repository"],
                              metadata=setup["metadata"])
    assert not setup["out"].exists()



# ── A08.8: update channel and profile format ──────────────────────────────

TEST_KEY = {"keyid": "0123456789abcdef", "ed25519": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="}


def test_channel_and_profile_format_are_part_of_the_verified_staging(setup):
    channel = staging.channel_document("https://updates.example/stable.json", [TEST_KEY])
    _build(setup, channel=channel)
    out = setup["out"]
    manifest = json.loads((out / staging.MANIFEST).read_text("utf-8"))
    assert manifest["profile"] == {"format_version": 1}
    assert any(item["path"] == "channel.json" for item in manifest["files"])
    assert json.loads((out / "channel.json").read_text("utf-8")) == channel
    staging.verify_staging(out)
    (out / "channel.json").write_text(json.dumps({**channel, "url": "https://evil.example/x.json"}), "utf-8")
    with pytest.raises(DistributionError):
        staging.verify_staging(out)


def test_channel_document_requires_https_and_keys():
    assert staging.channel_document("http://127.0.0.1:8080/s.json", [TEST_KEY], test=True)["test"] is True
    for url, keys, test in [("http://127.0.0.1:8080/s.json", [TEST_KEY], False),
                            ("http://updates.example/s.json", [TEST_KEY], True),
                            ("https://127.0.0.1/s.json", [TEST_KEY], False),
                            ("https://localhost/s.json", [TEST_KEY], False),
                            ("https://192.168.1.20/s.json", [TEST_KEY], False),
                            ("https://updates.example/s.json", [], False),
                            ("https://updates.example/s.json", [{"keyid": "x"}], False)]:
        with pytest.raises(DistributionError):
            staging.channel_document(url, keys, test=test)
    with pytest.raises(DistributionError):
        staging.channel_document("https://updates.example/s.json", [TEST_KEY], name="Stable!")


def test_build_rejects_a_staged_python_with_another_profile_format(setup, monkeypatch):
    monkeypatch.setattr(staging, "check_descriptor_with_staged_python",
                        lambda root: {"mode": "packaged", "max_relative_path": 1, "profile_format": 2})
    with pytest.raises(DistributionError, match="profile format"):
        staging.build_staging(setup["runtime"], setup["executable"], setup["out"], repository=setup["repository"],
                              metadata=setup["metadata"])
    assert not setup["out"].exists()


# ── A08.9: reviewed notices, installer notices and the Companion inside the version ──

APACHE = b"Apache License 2.0 (fixture text)\n"
SDK = b"Microsoft WebView2 SDK license (fixture text)\n"
NSIS_COPYING = b"NSIS COPYING (fixture text)\n"
PLUGIN = b"nsis_tauri_utils.dll fixture"
LOADER = b"WebView2LoaderStatic.lib fixture"


def _sha(payload):
    return hashlib.sha256(payload).hexdigest()


def _reviewed(repository, *, loader=LOADER):
    """A small desktop_license_sources.json in the synthetic repository."""
    notices = repository / "scripts/desktop_notices"
    for relative, payload in {"common/Apache-2.0.txt": APACHE, "sdk/LICENSE.txt": SDK,
                              "nsis/COPYING": NSIS_COPYING}.items():
        (notices / relative).parent.mkdir(parents=True, exist_ok=True)
        (notices / relative).write_bytes(payload)
    texts = {relative: {"sha256": _sha(payload), "origin": "fixture"} for relative, payload in
             {"common/Apache-2.0.txt": APACHE, "sdk/LICENSE.txt": SDK, "nsis/COPYING": NSIS_COPYING}.items()}
    document = {
        "schema": 1, "texts": texts,
        "crates": {
            "unic-common==0.9.0": {"license": "Apache-2.0", "source": "https://example.test/unic",
                                   "provenance": "elected", "files": {"LICENSE-APACHE": "common/Apache-2.0.txt"}},
            "webview2-com-sys==0.38.2": {
                "license": "MIT; SDK", "source": "https://example.test/webview2", "provenance": "reviewed",
                "files": {"WebView2-SDK-LICENSE.txt": "sdk/LICENSE.txt"},
                "vendored": [{"path": "x64/WebView2LoaderStatic.lib", "sha256": _sha(loader)}]},
        },
        "installer": {
            "NSIS==3.11": {"license": "zlib", "source": "https://example.test/nsis", "provenance": "tool",
                           "files": {"COPYING": "nsis/COPYING"}, "tool": {"path": "COPYING", "sha256": _sha(NSIS_COPYING)}},
            "nsis-tauri-utils==0.5.3": {"license": "Apache-2.0", "source": "https://example.test/plugin",
                                        "provenance": "plugin", "files": {"LICENSE-APACHE": "common/Apache-2.0.txt"},
                                        "tool": {"path": "Plugins/nsis_tauri_utils.dll", "sha256": _sha(PLUGIN)}},
        },
        "requirements": {"Microsoft Edge WebView2 Runtime": {
            "license": "Microsoft", "source": "https://example.test/webview2-runtime", "provenance": "requirement",
            "files": {"AVISO.txt": "sdk/LICENSE.txt"}}},
    }
    (repository / "scripts/desktop_license_sources.json").write_text(json.dumps(document), "utf-8")
    return document


def _nsis(root):
    (root / "Plugins").mkdir(parents=True)
    (root / "COPYING").write_bytes(NSIS_COPYING)
    (root / "Plugins/nsis_tauri_utils.dll").write_bytes(PLUGIN)
    return root


def _vendor_loader(setup, payload=LOADER):
    package = next(p for p in setup["metadata"]["packages"] if p["name"] == "webview2-com-sys")
    loader = Path(package["manifest_path"]).parent / "x64/WebView2LoaderStatic.lib"
    loader.parent.mkdir(exist_ok=True)
    loader.write_bytes(payload)


def test_reviewed_notices_resolve_crates_without_license_files(tmp_path, setup):
    _reviewed(setup["repository"])
    _vendor_loader(setup)
    document = collect(setup["metadata"], tmp_path / "out", setup["repository"])
    assert document["pending"] == []
    by_name = {r["package"]: r for r in document["licenses"]}
    assert by_name["unic-common"]["license"] == "Apache-2.0" and by_name["unic-common"]["provenance"] == "elected"
    assert (tmp_path / "out/desktop/licenses/unic-common-0.9.0/LICENSE-APACHE").read_bytes() == APACHE
    # The crate's own license stays; the reviewed SDK notice is added next to it.
    assert by_name["webview2-com-sys"]["files"] == [
        "desktop/licenses/webview2-com-sys-0.38.2/LICENSE-MIT",
        "desktop/licenses/webview2-com-sys-0.38.2/WebView2-SDK-LICENSE.txt"]
    assert by_name["Microsoft Edge WebView2 Runtime"]["files"] == [
        "desktop/licenses/Microsoft_Edge_WebView2_Runtime/AVISO.txt"]
    assert document["reviewed_sources_sha256"] == _sha(
        (setup["repository"] / "scripts/desktop_license_sources.json").read_bytes())


def test_a_changed_vendored_binary_reopens_its_review(tmp_path, setup):
    _reviewed(setup["repository"])
    _vendor_loader(setup, b"another loader")
    document = collect(setup["metadata"], tmp_path / "out", setup["repository"])
    pending = {p["package"]: p["reason"] for p in document["pending"]}
    assert list(pending) == ["webview2-com-sys"] and "no coincide con el binario revisado" in pending["webview2-com-sys"]
    record = next(r for r in document["licenses"] if r["package"] == "webview2-com-sys")
    assert record["files"] == ["desktop/licenses/webview2-com-sys-0.38.2/LICENSE-MIT"] and "provenance" not in record


@pytest.mark.parametrize("change", ["text", "identity", "source", "file"])
def test_reviewed_sources_are_validated_before_use(setup, change):
    document = _reviewed(setup["repository"])
    repository = setup["repository"]
    if change == "text":
        (repository / "scripts/desktop_notices/common/Apache-2.0.txt").write_bytes(b"edited\n")
    else:
        if change == "identity":
            document["crates"]["unic-common"] = document["crates"].pop("unic-common==0.9.0")
        elif change == "source":
            document["crates"]["unic-common==0.9.0"]["source"] = "http://example.test/unic"
        else:
            document["crates"]["unic-common==0.9.0"]["files"] = {"LICENSE": "common/missing.txt"}
        (repository / "scripts/desktop_license_sources.json").write_text(json.dumps(document), "utf-8")
    with pytest.raises(DesktopLicenseError):
        desktop_licenses.load_reviewed(repository)


def test_installer_notices_must_match_the_nsis_that_builds_it(tmp_path, setup):
    _reviewed(setup["repository"])
    nsis = _nsis(tmp_path / "NSIS")
    document = desktop_licenses.collect_installer(tmp_path / "out", setup["repository"], nsis)
    assert document["pending"] == []
    assert {r["package"]: r["files"] for r in document["licenses"]} == {
        "NSIS": ["installer/licenses/NSIS-3.11/COPYING"],
        "nsis-tauri-utils": ["installer/licenses/nsis-tauri-utils-0.5.3/LICENSE-APACHE"]}
    (nsis / "Plugins/nsis_tauri_utils.dll").write_bytes(b"another plugin")
    document = desktop_licenses.collect_installer(tmp_path / "out2", setup["repository"], nsis)
    assert [p["package"] for p in document["pending"]] == ["nsis-tauri-utils"]


def test_the_real_reviewed_notices_match_their_hashes():
    reviewed = desktop_licenses.load_reviewed(staging.PROJECT_ROOT)
    assert len(reviewed["crates"]) == 9 and set(reviewed["installer"]) == {"NSIS==3.11", "nsis-tauri-utils==0.5.3"}
    sdk = reviewed["crates"]["webview2-com-sys==0.38.2"]
    assert sdk["vendored"][0]["path"] == "x64/WebView2LoaderStatic.lib"
    assert "webview2-sdk-1.0.3650.58/LICENSE.txt" in sdk["files"].values()
    tool = reviewed["installer"]["NSIS==3.11"]["tool"]
    assert set(tool) == {"tree_sha256", "files"}
    nsis = staging.PROJECT_ROOT / "companion/src-tauri/target/.tauri/NSIS"
    if nsis.is_dir():  # the local toolchain, when this machine has it
        assert desktop_licenses.tree_digest(nsis) == (tool["tree_sha256"], tool["files"])


COMPANION_SETUP = "IRIS Desktop Companion_0.1.3_x64-setup.exe"
WEB_LICENSE = b"three.js MIT (fixture)\n"


def _companion(setup, tmp_path, *, mode="skip", version="0.1.3"):
    repository = setup["repository"]
    (repository / "companion/src-tauri").mkdir(parents=True, exist_ok=True)
    (repository / "companion/src-tauri/tauri.conf.json").write_text(json.dumps({"version": "0.1.3"}), "utf-8")
    (repository / "companion/src-tauri/Cargo.lock").write_text("# companion lock\n", "utf-8")
    folder = tmp_path / "companion-release"
    folder.mkdir()
    installer = pe(0x014C) + bytes(70_000) + b"companion setup"  # NSIS: x86 stub
    (folder / COMPANION_SETUP).write_bytes(installer)
    (folder / "release-manifest.json").write_text(json.dumps({
        "schema": 1, "product": "IRIS Desktop Companion", "version": version, "platform": "windows", "arch": "x64",
        "filename": COMPANION_SETUP, "sha256": _sha(installer), "signed": False, "webview2_mode": mode}), "utf-8")
    return folder


TAURI_API_APACHE = b"@tauri-apps/api Apache-2.0 (fixture)\n"
TAURI_API_MIT = b"@tauri-apps/api MIT (fixture)\n"


def _web_runtime(tmp_path):
    texts = {"face/vendor/licenses/three-LICENSE.txt": WEB_LICENSE,
             "wizard/ui/vendor/licenses/three-LICENSE.txt": WEB_LICENSE,
             "face/vendor/licenses/_tauri-apps_api-LICENSE_APACHE-2.0.txt": TAURI_API_APACHE,
             "face/vendor/licenses/_tauri-apps_api-LICENSE_MIT.txt": TAURI_API_MIT}
    web = {"schema": 1, "component": "iris-web", "licenses": [
        {"package": "three", "version": "0.170.0", "license": "MIT",
         "files": ["face/vendor/licenses/three-LICENSE.txt", "wizard/ui/vendor/licenses/three-LICENSE.txt"]},
        {"package": "@tauri-apps/api", "version": "2.11.1", "license": "Apache-2.0 OR MIT",
         "files": ["face/vendor/licenses/_tauri-apps_api-LICENSE_APACHE-2.0.txt",
                   "face/vendor/licenses/_tauri-apps_api-LICENSE_MIT.txt"]}],
        "files": [{"path": path, "size": len(body), "sha256": _sha(body)} for path, body in texts.items()]}
    site = "runtimes/iris/Lib/site-packages/"
    return _runtime(tmp_path / "candidate web", {**{site + path: body for path, body in texts.items()},
                                                 site + "face/web-assets.json": json.dumps(web).encode()})


def test_the_companion_ships_inside_the_version_with_its_notices(setup, tmp_path, monkeypatch):
    _reviewed(setup["repository"])
    _vendor_loader(setup)
    release = _companion(setup, tmp_path)
    runtime = _web_runtime(tmp_path)
    result = staging.build_staging(runtime, setup["executable"], setup["out"], repository=setup["repository"],
                                   metadata=setup["metadata"], self_check=False, companion=release,
                                   companion_metadata=setup["metadata"], nsis_dir=_nsis(tmp_path / "NSIS"))
    out = setup["out"]
    assert result["companion"] == "0.1.3" and result["licenses_pending"] == []
    assert (out / "companion" / COMPANION_SETUP).read_bytes() == (release / COMPANION_SETUP).read_bytes()
    manifest = json.loads((out / staging.MANIFEST).read_text("utf-8"))
    assert manifest["companion"]["webview2_mode"] == "skip" and manifest["installer"]["licenses_pending"] == []
    companion = json.loads((out / staging.COMPANION_LICENSES).read_text("utf-8"))
    assert companion["component"] == "iris-companion"
    assert (out / "companion/licenses/web/three-LICENSE.txt").read_bytes() == WEB_LICENSE
    web = {r["package"]: r["files"] for r in companion["licenses"] if r["package"] in {"three", "@tauri-apps/api"}}
    assert web["three"] == ["companion/licenses/web/three-LICENSE.txt"]  # identical Wizard copy stored once
    assert [(out / f).read_bytes() for f in web["@tauri-apps/api"]] == [TAURI_API_APACHE, TAURI_API_MIT]

    monkeypatch.setenv("IRIS_RUNTIME_DESCRIPTOR", str(out / "runtime.json"))
    from wizard import licenses

    licenses._cache.clear()
    catalog = licenses.license_catalog()
    groups = {c["group"] for c in catalog["components"]}
    assert {"desktop", "companion", "installer"} <= groups
    assert catalog["complete"] is True
    assert licenses.license_text("companion", "companion/licenses/web/three-LICENSE.txt")["text"] == \
        WEB_LICENSE.decode()

    (out / "companion" / COMPANION_SETUP).write_bytes(pe() + b"tampered")
    with pytest.raises(DistributionError):
        staging.verify_staging(out)


@pytest.mark.parametrize("mode, version", [("downloadBootstrapper", "0.1.3"), ("embedBootstrapper", "0.1.3"),
                                           ("skip", "0.1.2")])
def test_a_companion_that_downloads_webview2_or_mismatches_is_refused(setup, tmp_path, mode, version):
    release = _companion(setup, tmp_path, mode=mode, version=version)
    with pytest.raises(DistributionError):
        staging.build_staging(_web_runtime(tmp_path), setup["executable"], setup["out"],
                              repository=setup["repository"], metadata=setup["metadata"], self_check=False,
                              companion=release, companion_metadata=setup["metadata"])
    assert not setup["out"].exists()


def _catalog(out, monkeypatch):
    monkeypatch.setenv("IRIS_RUNTIME_DESCRIPTOR", str(out / "runtime.json"))
    from wizard import licenses

    licenses._cache.clear()
    catalog = licenses.license_catalog()
    return catalog, {s["id"]: s for s in catalog["sources"]}


def _full_build(setup, tmp_path, companion=True):
    _reviewed(setup["repository"])
    _vendor_loader(setup)
    staging.build_staging(_web_runtime(tmp_path), setup["executable"], setup["out"], repository=setup["repository"],
                          metadata=setup["metadata"], self_check=False,
                          companion=_companion(setup, tmp_path) if companion else None,
                          companion_metadata=setup["metadata"], nsis_dir=_nsis(tmp_path / "NSIS"))
    return setup["out"]


@pytest.mark.parametrize("missing", ["folder", "inventory"])
def test_a_declared_companion_that_is_missing_keeps_the_catalog_incomplete(setup, tmp_path, monkeypatch, missing):
    out = _full_build(setup, tmp_path)
    assert _catalog(out, monkeypatch)[0]["complete"] is True
    if missing == "folder":
        shutil.rmtree(out / "companion")
    else:
        (out / staging.COMPANION_LICENSES).unlink()
    catalog, sources = _catalog(out, monkeypatch)
    assert catalog["complete"] is False and sources["companion"]["available"] is False


def test_a_version_without_the_companion_can_still_be_complete(setup, tmp_path, monkeypatch):
    out = _full_build(setup, tmp_path, companion=False)
    catalog, sources = _catalog(out, monkeypatch)
    assert catalog["complete"] is True and "companion" not in sources


def test_an_unreadable_staging_manifest_never_claims_completeness(setup, tmp_path, monkeypatch):
    out = _full_build(setup, tmp_path, companion=False)
    (out / staging.MANIFEST).write_text("{not json", "utf-8")
    catalog, sources = _catalog(out, monkeypatch)
    assert catalog["complete"] is False and sources["companion"]["available"] is False


def test_the_nsis_notice_is_tied_to_the_whole_toolchain(tmp_path, setup):
    document = _reviewed(setup["repository"])
    nsis = _nsis(tmp_path / "NSIS")
    (nsis / "Bin").mkdir()
    (nsis / "Bin/makensis.exe").write_bytes(b"makensis 3.11")
    tree, count = desktop_licenses.tree_digest(nsis)
    document["installer"]["NSIS==3.11"]["tool"] = {"tree_sha256": tree, "files": count}
    (setup["repository"] / "scripts/desktop_license_sources.json").write_text(json.dumps(document), "utf-8")
    assert desktop_licenses.collect_installer(tmp_path / "ok", setup["repository"], nsis)["pending"] == []
    (nsis / "Bin/makensis.exe").write_bytes(b"another makensis")  # same COPYING, different compiler
    document = desktop_licenses.collect_installer(tmp_path / "changed", setup["repository"], nsis)
    assert [p["package"] for p in document["pending"]] == ["NSIS"]
    assert document["nsis_tree_sha256"] != tree


@pytest.mark.parametrize("field, value", [("version", "0.1.4"), ("signed", True),
                                          ("webview2_mode", "downloadBootstrapper")])
def test_verify_compares_every_companion_release_field(setup, tmp_path, field, value):
    out = _full_build(setup, tmp_path)
    staging.verify_staging(out)
    release = json.loads((out / staging.COMPANION_MANIFEST).read_text("utf-8"))
    manifest = json.loads((out / staging.MANIFEST).read_text("utf-8"))
    release[field] = value
    body = json.dumps(release).encode()
    (out / staging.COMPANION_MANIFEST).write_bytes(body)
    entry = next(f for f in manifest["files"] if f["path"] == staging.COMPANION_MANIFEST)
    entry.update(size=len(body), sha256=_sha(body))  # a consistent forgery of the file list
    (out / staging.MANIFEST).write_text(json.dumps(manifest), "utf-8")
    with pytest.raises(DistributionError, match="differs from its release manifest"):
        staging.verify_staging(out)


# ── A08.9 QA on a clean Windows 11: Iris.exe needed VCRUNTIME140.dll ──

def _pe_with_imports(imports=(), delayed=()):
    """A minimal PE32+ (x64) with one section holding its import and delay-import tables."""
    import struct

    data = bytearray(0x600)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 60, 64)
    data[64:68] = b"PE\0\0"
    struct.pack_into("<HH", data, 68, 0x8664, 1)  # machine, one section
    struct.pack_into("<H", data, 84, 240)  # size of the PE32+ optional header
    optional = 88
    struct.pack_into("<H", data, optional, 0x20B)
    struct.pack_into("<I", data, optional + 108, 16)  # data directories
    section = optional + 240
    data[section:section + 6] = b".idata"
    struct.pack_into("<IIII", data, section + 8, 0x200, 0x1000, 0x200, 0x400)  # size, rva, raw size, raw

    def at(rva):
        return 0x400 + rva - 0x1000

    names = 0x1100
    if imports:
        struct.pack_into("<II", data, optional + 112 + 8 * 1, 0x1000, 20 * (len(imports) + 1))
    for i, name in enumerate(imports):
        struct.pack_into("<I", data, at(0x1000 + 20 * i) + 12, names)
        data[at(names):at(names) + len(name)] = name.encode()
        names += 0x20
    if delayed:
        struct.pack_into("<II", data, optional + 112 + 8 * 13, 0x1080, 32 * (len(delayed) + 1))
    for i, name in enumerate(delayed):
        struct.pack_into("<I", data, at(0x1080 + 32 * i) + 4, names)
        data[at(names):at(names) + len(name)] = name.encode()
        names += 0x20
    return bytes(data)


@pytest.mark.parametrize("imports, delayed", [
    (["KERNEL32.dll", "VCRUNTIME140.dll"], []),
    (["KERNEL32.dll", "msvcp140.dll"], []),
    (["KERNEL32.dll"], ["VCRUNTIME140_1.dll"]),
])
def test_iris_exe_must_not_need_the_visual_cpp_runtime(tmp_path, imports, delayed):
    exe = tmp_path / "Iris.exe"
    exe.write_bytes(_pe_with_imports(imports, delayed))
    assert staging._pe_imports(exe) == imports + delayed
    with pytest.raises(DistributionError, match="Visual C\\+\\+ runtime"):
        staging._pe_check(exe)


def test_windows_libraries_and_the_universal_crt_are_fine(tmp_path):
    exe = tmp_path / "Iris.exe"
    shipped = ["KERNEL32.dll", "USER32.dll", "api-ms-win-crt-runtime-l1-1-0.dll", "ucrtbase.dll"]
    exe.write_bytes(_pe_with_imports(shipped, ["WebView2Loader.dll"]))
    staging._pe_check(exe)


def test_the_launcher_is_built_with_the_visual_cpp_runtime_linked_in(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(staging.subprocess, "run", lambda command, env, check: seen.update(env))
    staging.build_launcher(tmp_path, tmp_path / "target", cargo="cargo")
    assert seen["STATIC_VCRUNTIME"] == "true"
