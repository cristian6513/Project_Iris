"""Native license proof must match staged bytes, not just package metadata."""

from __future__ import annotations

import hashlib
import json
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import native_runtime_licenses as native

BODY = b"MIT License\nCopyright Authors\nPermission is hereby granted to redistribute.\n"
BINARY = b"reviewed native binary fixture\0"


def sources(tmp_path):
    stage, root = tmp_path / "stage", tmp_path / "sources"
    binary = stage / "runtimes/hermes/Lib/site-packages/example/example.dll"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(BINARY)
    file = root / "runtime_native_notices/example-1.2.3/LICENSE"
    file.parent.mkdir(parents=True)
    file.write_bytes(BODY)
    item = {"component": "hermes-runtime", "package": "Example", "version": "1.2.3",
            "license": "MIT", "source": "https://example.org/releases/1.2.3/archive.zip",
            "provenance": [{"type": "archive", "url": "https://example.org/releases/1.2.3/archive.zip",
                            "archive_sha256": "a" * 64, "archive_sha256_verified": True}],
            "binaries": [{"path": binary.relative_to(stage).as_posix(), "size": len(BINARY),
                          "sha256": hashlib.sha256(BINARY).hexdigest(),
                          "evidence": {"provenance_index": 0, "member": "bin/example.dll"}}],
            "files": [{"path": "licenses/native/example-1.2.3/LICENSE",
                       "source_path": "runtime_native_notices/example-1.2.3/LICENSE",
                       "sha256": hashlib.sha256(BODY).hexdigest(),
                       "evidence": {"provenance_index": 0, "member": "LICENSE"}}]}
    document = {"schema_version": 1, "packages": {"example==1.2.3": item}}
    manifest = root / "native.json"
    rewrite(manifest, document)
    return stage, manifest, document, item


def rewrite(manifest, document):
    manifest.write_text(json.dumps(document), encoding="utf8")


def assert_unwritten(stage):
    assert not (stage / "licenses").exists()


def test_copy_verifies_manifest_binary_and_notices_without_importing_package(tmp_path):
    stage, manifest, _, _ = sources(tmp_path)
    package = stage / "runtimes/hermes/Lib/site-packages/example/__init__.py"
    package.write_text("raise AssertionError('must not import')", encoding="utf8")
    digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
    records = native.prepare_native_license_records(stage, manifest, expected_manifest_sha256=digest)
    assert records == [{"component": "hermes-runtime", "package": "Example", "version": "1.2.3",
                        "license": "MIT", "files": ["licenses/native/example-1.2.3/LICENSE"],
                        "source": "https://example.org/releases/1.2.3/archive.zip"}]
    assert (stage / records[0]["files"][0]).read_bytes() == BODY
    assert native.prepare_native_license_records(stage, manifest) == records
    assert (stage / "runtimes/hermes/Lib/site-packages/example/example.dll").read_bytes() == BINARY


def test_manifest_hash_mismatch_writes_nothing(tmp_path):
    stage, manifest, _, _ = sources(tmp_path)
    with pytest.raises(native.RuntimeLicenseError, match="manifest SHA256 mismatch"):
        native.prepare_native_license_records(stage, manifest, expected_manifest_sha256="b" * 64)
    assert_unwritten(stage)


@pytest.mark.parametrize("field,value", [
    ("source_path", "/outside/LICENSE"), ("source_path", "../LICENSE"),
    ("source_path", "C:/outside/LICENSE"), ("source_path", "C:\\outside\\LICENSE"),
    ("source_path", "runtime_native_notices/example-1.2.3/../LICENSE"),
    ("path", "licenses/native/other-1.2.3/LICENSE"),
    ("path", "licenses/native/example-1.2.3/LICENSE:ads"),
    ("path", "licenses/native/example-1.2.3/LICENSE."),
    ("path", "licenses/native/example-1.2.3/CON/LICENSE"),
])
def test_unsafe_notice_paths_fail_before_writes(tmp_path, field, value):
    stage, manifest, document, item = sources(tmp_path)
    item["files"][0][field] = value
    rewrite(manifest, document)
    with pytest.raises(native.RuntimeLicenseError):
        native.prepare_native_license_records(stage, manifest)
    assert_unwritten(stage)


@pytest.mark.parametrize("value", [
    "/outside/example.dll", "../example.dll", "C:/outside/example.dll", "C:\\outside\\example.dll",
    "runtimes/hermes/Lib/site-packages/../example.dll",
    "runtimes/hermes/Lib/site-packages/example/example.dll:ads",
    "runtimes/hermes/Lib/site-packages/example/example.dll.",
    "runtimes/hermes/Lib/site-packages/NUL/example.dll",
    "runtimes/iris/Lib/site-packages/example/example.dll",
    "runtimes/hermes/Lib/site-packages/example/example.exe",
])
def test_unsafe_binary_paths_fail_before_reading_or_writing(tmp_path, value):
    stage, manifest, document, item = sources(tmp_path)
    item["binaries"][0]["path"] = value
    rewrite(manifest, document)
    with pytest.raises(native.RuntimeLicenseError):
        native.prepare_native_license_records(stage, manifest)
    assert_unwritten(stage)


@pytest.mark.parametrize("field,value", [("sha256", "a" * 64), ("size", len(BINARY) + 1)])
def test_real_native_binary_must_match_both_hash_and_size(tmp_path, field, value):
    stage, manifest, document, item = sources(tmp_path)
    item["binaries"][0][field] = value
    rewrite(manifest, document)
    with pytest.raises(native.RuntimeLicenseError, match="binary SHA256 or size mismatch"):
        native.prepare_native_license_records(stage, manifest)
    assert_unwritten(stage)


def test_validate_all_notice_sources_before_first_write(tmp_path):
    stage, manifest, document, item = sources(tmp_path)
    file = manifest.parent / "runtime_native_notices/example-1.2.3/NOTICE"
    file.write_bytes(b"corrupt")
    item["files"].append({"path": "licenses/native/example-1.2.3/NOTICE",
                          "source_path": "runtime_native_notices/example-1.2.3/NOTICE", "sha256": "c" * 64,
                          "evidence": {"provenance_index": 0, "member": "NOTICE"}})
    rewrite(manifest, document)
    with pytest.raises(native.RuntimeLicenseError, match="notice SHA256 mismatch"):
        native.prepare_native_license_records(stage, manifest)
    assert_unwritten(stage)


def test_cannot_replace_different_staging_notice(tmp_path):
    stage, manifest, _, _ = sources(tmp_path)
    target = stage / "licenses/native/example-1.2.3/LICENSE"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"keep this")
    with pytest.raises(native.RuntimeLicenseError, match="replace different"):
        native.prepare_native_license_records(stage, manifest)
    assert target.read_bytes() == b"keep this"


@pytest.mark.parametrize("location,kind", [
    ("notice", "symlink"), ("notice", "junction"), ("notice", "hardlink"),
    ("binary", "symlink"), ("binary", "junction"), ("binary", "hardlink"),
])
def test_linked_notice_or_binary_fails_closed(tmp_path, monkeypatch, location, kind):
    stage, manifest, _, _ = sources(tmp_path)
    target = (manifest.parent / "runtime_native_notices/example-1.2.3/LICENSE" if location == "notice"
              else stage / "runtimes/hermes/Lib/site-packages/example/example.dll")
    original = native.Path.lstat

    def lstat(path):
        if path == target:
            actual = original(path)
            return SimpleNamespace(st_mode=stat.S_IFLNK if kind == "symlink" else actual.st_mode,
                                   st_nlink=2 if kind == "hardlink" else 1,
                                   st_file_attributes=0x400 if kind == "junction" else 0)
        return original(path)

    monkeypatch.setattr(native.Path, "lstat", lstat)
    with pytest.raises(native.RuntimeLicenseError, match="symlink or junction|hardlink"):
        native.prepare_native_license_records(stage, manifest)
    assert_unwritten(stage)


@pytest.mark.parametrize("value", ["execute", "git", "wheel", None])
def test_unknown_provenance_method_fails_closed(tmp_path, value):
    stage, manifest, document, item = sources(tmp_path)
    item["provenance"][0]["type"] = value
    rewrite(manifest, document)
    with pytest.raises(native.RuntimeLicenseError, match="Unknown native source provenance"):
        native.prepare_native_license_records(stage, manifest)
    assert_unwritten(stage)


def test_partial_wheel_reads_do_not_claim_full_archive_verification(tmp_path):
    stage, manifest, document, item = sources(tmp_path)
    item["provenance"] = [{"type": "wheel-range", "url": item["source"], "archive_size": 100,
                           "archive_sha256_declared": "d" * 64, "archive_sha256_verified": False,
                           "zip_entry_crc_verified": True}]
    rewrite(manifest, document)
    assert native.prepare_native_license_records(stage, manifest)[0]["files"]
    item["provenance"][0]["archive_sha256_verified"] = True
    rewrite(manifest, document)
    with pytest.raises(native.RuntimeLicenseError, match="cannot claim whole-archive"):
        native.prepare_native_license_records(stage, manifest)


@pytest.mark.parametrize("url", ["http://example.org", "https://user:secret@example.org/file",
                                "https://localhost/file", "https://127.0.0.1/file",
                                "https://example.org/file?token=secret"])
def test_private_or_credential_sources_are_rejected(tmp_path, url):
    stage, manifest, document, item = sources(tmp_path)
    item["source"] = url
    rewrite(manifest, document)
    with pytest.raises(native.RuntimeLicenseError, match="public HTTPS"):
        native.prepare_native_license_records(stage, manifest)
    assert_unwritten(stage)


def test_documentation_must_preserve_original_verified_html(tmp_path):
    stage, manifest, document, item = sources(tmp_path)
    item["provenance"].append({"type": "documentation", "url": "https://example.org/docs/eula.html",
                               "document_sha256": hashlib.sha256(BODY).hexdigest(),
                               "document_sha256_verified": True})
    item["files"][0]["evidence"] = {"provenance_index": 1, "representation": "text",
                                    "extraction": "htmlparser-bd-article-v1"}
    rewrite(manifest, document)
    with pytest.raises(native.RuntimeLicenseError, match="preserve its verified original HTML"):
        native.prepare_native_license_records(stage, manifest)
    assert_unwritten(stage)
    item["files"][0]["evidence"] = {"provenance_index": 1, "representation": "html"}
    rewrite(manifest, document)
    assert native.prepare_native_license_records(stage, manifest)[0]["files"]


def test_documentation_cannot_prove_native_binary_origin(tmp_path):
    stage, manifest, document, item = sources(tmp_path)
    item["provenance"] = [{"type": "documentation", "url": "https://example.org/docs/eula.html",
                           "document_sha256": hashlib.sha256(BODY).hexdigest(),
                           "document_sha256_verified": True}]
    rewrite(manifest, document)
    with pytest.raises(native.RuntimeLicenseError, match="cannot prove native binary provenance"):
        native.prepare_native_license_records(stage, manifest)
    assert_unwritten(stage)


@pytest.mark.parametrize("field,value", [("component", "other"), ("component", []),
                                        ("license", "UNKNOWN"), ("version", "1.2.4")])
def test_unknown_component_license_or_inexact_version_fail_closed(tmp_path, field, value):
    stage, manifest, document, item = sources(tmp_path)
    item[field] = value
    rewrite(manifest, document)
    with pytest.raises(native.RuntimeLicenseError):
        native.prepare_native_license_records(stage, manifest)
    assert_unwritten(stage)


def test_no_case_aliases_of_notice_targets(tmp_path):
    stage, manifest, document, item = sources(tmp_path)
    second = dict(item["files"][0])
    second["path"] = second["path"].replace("LICENSE", "license")
    item["files"].append(second)
    rewrite(manifest, document)
    with pytest.raises(native.RuntimeLicenseError, match="Duplicate native notice"):
        native.prepare_native_license_records(stage, manifest)
    assert_unwritten(stage)


def static_package(root, binary, name="embedded", version="4.5.6", markers=("reviewed native",)):
    notice = root / f"runtime_native_notices/{name}-{version}/LICENSE"
    notice.parent.mkdir(parents=True, exist_ok=True)
    notice.write_bytes(BODY)
    return {"component": "hermes-runtime", "package": name.capitalize(), "version": version,
            "license": "Apache-2.0", "source": f"https://github.com/example/{name}/tree/v{version}",
            "provenance": [{"type": "git-revision", "url": f"https://github.com/example/{name}",
                            "revision": "e" * 40, "tag": f"v{version}"}],
            "binaries": [{"path": binary, "size": len(BINARY), "sha256": hashlib.sha256(BINARY).hexdigest(),
                          "linkage": "static",
                          "evidence": {"provenance_index": 0, "member": "src/verbose.cpp",
                                       "markers": list(markers)}}],
            "files": [{"path": f"licenses/native/{name}-{version}/LICENSE",
                       "source_path": f"runtime_native_notices/{name}-{version}/LICENSE",
                       "sha256": hashlib.sha256(BODY).hexdigest(),
                       "evidence": {"provenance_index": 0, "member": "LICENSE"}}]}


def test_static_components_share_a_dll_with_one_exact_attribution(tmp_path):
    stage, manifest, document, item = sources(tmp_path)
    binary = item["binaries"][0]["path"]
    document["packages"]["embedded==4.5.6"] = static_package(manifest.parent, binary)
    document["packages"]["second==7.8.9"] = static_package(
        manifest.parent, binary, "second", "7.8.9", markers=("binary fixture",))
    rewrite(manifest, document)
    records = native.prepare_native_license_records(stage, manifest)
    assert [record["package"] for record in records] == ["Embedded", "Example", "Second"]


@pytest.mark.parametrize("markers,message", [
    (("not in the dll bytes",), "markers not found"),
    ((), "distinct printable byte markers"),
    (("short",), "distinct printable byte markers"),
    (("reviewed native", "reviewed native"), "distinct printable byte markers"),
    (("reviewed\nnative",), "distinct printable byte markers"),
])
def test_static_component_markers_must_be_valid_and_present(tmp_path, markers, message):
    stage, manifest, document, item = sources(tmp_path)
    document["packages"]["embedded==4.5.6"] = static_package(
        manifest.parent, item["binaries"][0]["path"], markers=markers)
    rewrite(manifest, document)
    with pytest.raises(native.RuntimeLicenseError, match=message):
        native.prepare_native_license_records(stage, manifest)
    assert_unwritten(stage)


def test_one_marker_cannot_prove_two_static_components(tmp_path):
    stage, manifest, document, item = sources(tmp_path)
    binary = item["binaries"][0]["path"]
    document["packages"]["embedded==4.5.6"] = static_package(manifest.parent, binary)
    document["packages"]["second==7.8.9"] = static_package(manifest.parent, binary, "second", "7.8.9")
    rewrite(manifest, document)
    with pytest.raises(native.RuntimeLicenseError, match="already attributed"):
        native.prepare_native_license_records(stage, manifest)
    assert_unwritten(stage)


def test_exact_attribution_of_one_dll_remains_unique(tmp_path):
    stage, manifest, document, item = sources(tmp_path)
    other = json.loads(json.dumps(item))
    other["package"], other["version"] = "Other", "9.9.9"
    other["files"][0]["path"] = "licenses/native/other-9.9.9/LICENSE"
    other["files"][0]["source_path"] = "runtime_native_notices/other-9.9.9/LICENSE"
    notice = manifest.parent / "runtime_native_notices/other-9.9.9/LICENSE"
    notice.parent.mkdir(parents=True)
    notice.write_bytes(BODY)
    document["packages"]["other==9.9.9"] = other
    rewrite(manifest, document)
    with pytest.raises(native.RuntimeLicenseError, match="Duplicate native binary evidence"):
        native.prepare_native_license_records(stage, manifest)
    assert_unwritten(stage)


@pytest.mark.parametrize("change,message", [
    ({"linkage": "dynamic"}, "Unknown native binary linkage"),
    ({"evidence": {"provenance_index": 0, "member": "bin/example.dll", "markers": ["reviewed native"]}},
     "Only static native components"),
])
def test_exact_binaries_cannot_use_static_proof(tmp_path, change, message):
    stage, manifest, document, item = sources(tmp_path)
    item["binaries"][0].update(change)
    rewrite(manifest, document)
    with pytest.raises(native.RuntimeLicenseError, match=message):
        native.prepare_native_license_records(stage, manifest)
    assert_unwritten(stage)


def test_source_revision_cannot_prove_exact_binary_bytes(tmp_path):
    stage, manifest, document, item = sources(tmp_path)
    package = static_package(manifest.parent, item["binaries"][0]["path"])
    package["binaries"][0].pop("linkage")
    package["binaries"][0]["evidence"].pop("markers")
    document["packages"] = {"embedded==4.5.6": package}
    rewrite(manifest, document)
    with pytest.raises(native.RuntimeLicenseError, match="cannot prove exact native binary"):
        native.prepare_native_license_records(stage, manifest)
    assert_unwritten(stage)


@pytest.mark.parametrize("field,value", [
    ("revision", "e" * 39), ("revision", "E" * 40), ("tag", ""), ("tag", "v1 2"),
    ("url", "https://gitlab.com/example/embedded"),
])
def test_git_revision_provenance_is_exact(tmp_path, field, value):
    stage, manifest, document, item = sources(tmp_path)
    package = static_package(manifest.parent, item["binaries"][0]["path"])
    package["provenance"][0][field] = value
    document["packages"]["embedded==4.5.6"] = package
    rewrite(manifest, document)
    with pytest.raises(native.RuntimeLicenseError):
        native.prepare_native_license_records(stage, manifest)
    assert_unwritten(stage)


def nuget(**changes):
    item = {"type": "nuget-range",
            "url": "https://api.nuget.org/v3-flatcontainer/example/1.2.3/example.1.2.3.nupkg",
            "archive_size": 100, "archive_sha512_declared": "f" * 128,
            "archive_sha256_verified": False, "zip_entry_crc_verified": True,
            "publisher_hash_source": "https://api.nuget.org/v3/catalog0/data/example.1.2.3.json"}
    item.update(changes)
    return item


@pytest.mark.parametrize("changes,ok", [
    ({}, True),
    ({"archive_sha256_verified": True}, False),
    ({"zip_entry_crc_verified": False}, False),
    ({"archive_sha512_declared": "f" * 64}, False),
    ({"url": "https://example.org/example.1.2.3.nupkg"}, False),
    ({"publisher_hash_source": "http://api.nuget.org/catalog.json"}, False),
])
def test_nuget_ranges_never_claim_whole_archive_verification(tmp_path, changes, ok):
    stage, manifest, document, item = sources(tmp_path)
    item["provenance"].append(nuget(**changes))
    item["files"][0]["evidence"] = {"provenance_index": 1, "member": "license.txt"}
    rewrite(manifest, document)
    if ok:
        assert native.prepare_native_license_records(stage, manifest)[0]["files"]
    else:
        with pytest.raises(native.RuntimeLicenseError):
            native.prepare_native_license_records(stage, manifest)
        assert_unwritten(stage)


def local_build(**changes):
    item = {"type": "local-build", "url": "https://codeload.github.com/example/example/tar.gz/1.2.3",
            "source_sha256": "a" * 64, "source_sha256_verified": True,
            "producer": "scripts/build_pyav_wheel.py",
            "artifact": "example-1.2.3-cp311-abi3-win_amd64.whl", "artifact_sha256": "b" * 64}
    item.update(changes)
    return item


@pytest.mark.parametrize("changes,ok", [
    ({}, True),
    ({"source_sha256_verified": False}, False),
    ({"producer": "scripts/anything.py"}, False),
    ({"artifact": "../example.whl"}, False),
    ({"artifact": "example.zip"}, False),
    ({"artifact_sha256": "z" * 64}, False),
    ({"url": "http://codeload.github.com/example.tar.gz"}, False),
])
def test_local_builds_need_verified_source_reviewed_producer_and_artifact(tmp_path, changes, ok):
    stage, manifest, document, item = sources(tmp_path)
    item["provenance"] = [local_build(**changes)]
    item["binaries"][0]["evidence"] = {"provenance_index": 0, "member": "example.libs/example.dll"}
    item["files"][0]["evidence"] = {"provenance_index": 0, "member": "example/licenses/LICENSE"}
    rewrite(manifest, document)
    if ok:
        assert native.prepare_native_license_records(stage, manifest)[0]["files"]
    else:
        with pytest.raises(native.RuntimeLicenseError):
            native.prepare_native_license_records(stage, manifest)
        assert_unwritten(stage)


def test_notice_body_in_repository_matches_every_manifest_hash():
    document = json.loads(native.DEFAULT_NATIVE_LICENSE_SOURCES.read_bytes())
    assert len(document["packages"]) == 7  # A08.4b: ConPTY of pywinpty 3 (Hermes 0.21.6)
    for item in document["packages"].values():
        for file in item["files"]:
            body = (native.DEFAULT_NATIVE_LICENSE_SOURCES.parent / file["source_path"]).read_bytes()
            assert hashlib.sha256(body).hexdigest() == file["sha256"]


def test_direct_script_import_works_outside_repository_cwd(tmp_path):
    scripts_path = Path(native.__file__).resolve().parent
    code = (f"import sys; sys.path.insert(0, {str(scripts_path)!r}); "
            "import native_runtime_licenses as native; "
            "assert callable(native.prepare_native_license_records); print('PASS')")
    result = subprocess.run([sys.executable, "-E", "-s", "-B", "-c", code], cwd=tmp_path,
                            capture_output=True, text=True, timeout=10, check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "PASS"
