"""License inventories read only sealed files and require actual notice evidence."""

from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import runtime_licenses as licenses

BODY = b"MIT License\nCopyright (c) Example Authors\nPermission is hereby granted, free of charge.\n"


def distribution(stage, *, name="example", version="1.2.3", label="MIT", fields="",
                 notices=None, record=""):
    site = stage / "runtimes/iris/Lib/site-packages"
    info = site / f"{name}-{version}.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text(
        f"Metadata-Version: 2.4\nName: {name}\nVersion: {version}\nLicense: {label}\n"
        f"Project-URL: Source, https://example.org/source\n{fields}\n", encoding="utf8")
    if record:
        (info / "RECORD").write_text(record, encoding="utf8")
    for relative, data in (notices or {}).items():
        file = site / relative
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(data)
    return site, info


def fallback(stage, *, name="example", version="1.2.3", body=BODY):
    file = stage / "licenses/reviewed-example/LICENSE"
    file.parent.mkdir(parents=True)
    file.write_bytes(body)
    return {f"{name}=={version}": {
        "license": "MIT", "source": "https://example.org/source/v1.2.3/LICENSE",
        "files": [{"path": file.relative_to(stage).as_posix(),
                   "sha256": hashlib.sha256(body).hexdigest()}],
    }}


def reviewed_sources(tmp_path):
    root, stage = tmp_path / "sources", tmp_path / "stage"
    stage.mkdir()
    file = root / "runtime_notices/example-1.2.3/LICENSE"
    file.parent.mkdir(parents=True)
    file.write_bytes(BODY)
    document = {"schema_version": 1, "packages": {"example==1.2.3": {
        "license": "MIT", "source": "https://example.org/source/v1.2.3/LICENSE",
        "provenance": {"type": "git", "repository": "https://example.org/source", "revision": "a" * 40},
        "files": [{"path": "licenses/python/example-1.2.3/LICENSE",
                   "source_path": "runtime_notices/example-1.2.3/LICENSE",
                   "sha256": hashlib.sha256(BODY).hexdigest()}],
    }}}
    manifest = root / "runtime_license_sources.json"
    manifest.write_text(json.dumps(document), "utf8")
    return stage, manifest, document


def rewrite_manifest(manifest, document):
    manifest.write_text(json.dumps(document), "utf8")


def test_reviewed_copy_checks_manifest_and_notice_hashes_and_returns_fallbacks(tmp_path):
    stage, manifest, _ = reviewed_sources(tmp_path)
    digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
    result = licenses.prepare_python_license_fallbacks(stage, manifest, expected_manifest_sha256=digest)
    assert result == {"example==1.2.3": {
        "license": "MIT", "source": "https://example.org/source/v1.2.3/LICENSE",
        "files": [{"path": "licenses/python/example-1.2.3/LICENSE", "sha256": hashlib.sha256(BODY).hexdigest()}],
    }}
    target = stage / "licenses/python/example-1.2.3/LICENSE"
    assert target.read_bytes() == BODY
    assert licenses.prepare_python_license_fallbacks(stage, manifest) == result
    site, _ = distribution(stage, label="UNKNOWN")
    assert licenses.inventory_python_licenses(stage, "iris-runtime", site, fallbacks=result)[0]["files"] == [
        "licenses/python/example-1.2.3/LICENSE"]


def test_reviewed_manifest_hash_mismatch_leaves_staging_empty(tmp_path):
    stage, manifest, _ = reviewed_sources(tmp_path)
    with pytest.raises(licenses.RuntimeLicenseError, match="manifest SHA256 mismatch"):
        licenses.prepare_python_license_fallbacks(stage, manifest, expected_manifest_sha256="b" * 64)
    assert not list(stage.iterdir())


@pytest.mark.parametrize("field,value", [
    ("source_path", "/outside/LICENSE"), ("source_path", "../LICENSE"),
    ("source_path", "C:/outside/LICENSE"), ("source_path", "C:\\outside\\LICENSE"),
    ("source_path", "runtime_notices/example-1.2.3/../LICENSE"),
    ("path", "licenses/python/other-1.2.3/LICENSE"),
    ("path", "licenses/python/example-1.2.3/LICENSE:ads"),
    ("path", "licenses/python/example-1.2.3/LICENSE."),
    ("path", "licenses/python/example-1.2.3/CON/LICENSE"),
])
def test_reviewed_copy_rejects_absolute_traversal_windows_and_wrong_package_paths(tmp_path, field, value):
    stage, manifest, document = reviewed_sources(tmp_path)
    document["packages"]["example==1.2.3"]["files"][0][field] = value
    rewrite_manifest(manifest, document)
    with pytest.raises(licenses.RuntimeLicenseError):
        licenses.prepare_python_license_fallbacks(stage, manifest)
    assert not list(stage.iterdir())


def test_reviewed_copy_validates_all_sources_before_any_writes(tmp_path):
    stage, manifest, document = reviewed_sources(tmp_path)
    file = manifest.parent / "runtime_notices/example-1.2.3/NOTICE"
    file.write_bytes(b"corrupt")
    document["packages"]["example==1.2.3"]["files"].append({
        "path": "licenses/python/example-1.2.3/NOTICE",
        "source_path": "runtime_notices/example-1.2.3/NOTICE", "sha256": "c" * 64})
    rewrite_manifest(manifest, document)
    with pytest.raises(licenses.RuntimeLicenseError, match="SHA256 mismatch"):
        licenses.prepare_python_license_fallbacks(stage, manifest)
    assert not list(stage.iterdir())


def test_reviewed_copy_does_not_replace_different_stage_content(tmp_path):
    stage, manifest, _ = reviewed_sources(tmp_path)
    target = stage / "licenses/python/example-1.2.3/LICENSE"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"keep this")
    with pytest.raises(licenses.RuntimeLicenseError, match="replace different"):
        licenses.prepare_python_license_fallbacks(stage, manifest)
    assert target.read_bytes() == b"keep this"


@pytest.mark.parametrize("kind", ["symlink", "junction", "hardlink"])
def test_reviewed_copy_rejects_linked_source_notices(tmp_path, monkeypatch, kind):
    stage, manifest, _ = reviewed_sources(tmp_path)
    target = manifest.parent / "runtime_notices/example-1.2.3/LICENSE"
    original = Path.lstat

    def lstat(path):
        if path == target:
            return SimpleNamespace(st_mode=stat.S_IFLNK if kind == "symlink" else stat.S_IFREG,
                                   st_file_attributes=0x400 if kind == "junction" else 0,
                                   st_nlink=2 if kind == "hardlink" else 1)
        return original(path)

    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(licenses.RuntimeLicenseError, match="hardlink|symlink|junction"):
        licenses.prepare_python_license_fallbacks(stage, manifest)
    assert not list(stage.iterdir())


def test_reviewed_copy_rejects_unknown_provenance_method(tmp_path):
    stage, manifest, document = reviewed_sources(tmp_path)
    document["packages"]["example==1.2.3"]["provenance"]["type"] = "external-profile"
    rewrite_manifest(manifest, document)
    with pytest.raises(licenses.RuntimeLicenseError, match="Unknown reviewed"):
        licenses.prepare_python_license_fallbacks(stage, manifest)
    assert not list(stage.iterdir())


def test_reviewed_sdist_provenance_requires_archive_sha_and_public_url(tmp_path):
    stage, manifest, document = reviewed_sources(tmp_path)
    record = document["packages"]["example==1.2.3"]
    record["provenance"] = {"type": "sdist", "url": "https://example.org/package-1.2.3.tar.gz", "sha256": "d" * 64}
    rewrite_manifest(manifest, document)
    assert licenses.prepare_python_license_fallbacks(stage, manifest)
    record["provenance"]["sha256"] = "unknown"
    rewrite_manifest(manifest, document)
    with pytest.raises(licenses.RuntimeLicenseError, match="sdist provenance"):
        licenses.prepare_python_license_fallbacks(stage, manifest)


def test_collects_dist_info_and_record_package_notices_without_importing_code(tmp_path):
    site, info = distribution(tmp_path, fields="License-File: LICENSE\n", notices={
        "example-1.2.3.dist-info/licenses/LICENSE": BODY,
        "example-1.2.3.dist-info/licenses/NOTICE.txt": b"Required publisher notice\n",
        "example-1.2.3.dist-info/AUTHORS": b"Author names\n",
        "example/ThirdPartyNotices.txt": b"Required native library notices\n",
        "example/__init__.py": b"raise AssertionError('Do not import package code')\n",
    }, record="example/ThirdPartyNotices.txt,,\nexample/__init__.py,,\n")
    result = licenses.inventory_python_licenses(tmp_path, "iris-runtime", site)
    assert result == [{
        "component": "iris-runtime", "package": "example", "version": "1.2.3", "license": "MIT",
        "files": sorted([
            (info / "licenses/LICENSE").relative_to(tmp_path).as_posix(),
            (info / "licenses/NOTICE.txt").relative_to(tmp_path).as_posix(),
            (info / "AUTHORS").relative_to(tmp_path).as_posix(),
            (site / "example/ThirdPartyNotices.txt").relative_to(tmp_path).as_posix(),
        ]), "source": "https://example.org/source",
    }]


def test_package_root_license_from_record_is_not_lost(tmp_path):
    site, _ = distribution(tmp_path, notices={"example/LICENSE.txt": BODY}, record="example/LICENSE.txt,,\n")
    result = licenses.inventory_python_licenses(tmp_path, "hermes-runtime", site)
    assert result[0]["files"] == ["runtimes/iris/Lib/site-packages/example/LICENSE.txt"]
    assert result[0]["component"] == "hermes-runtime"


def test_spdx_expression_precedes_legacy_license_and_classifiers(tmp_path):
    site, _ = distribution(tmp_path, label="UNKNOWN", fields="License-Expression: Apache-2.0 OR BSD-3-Clause\n",
                           notices={"example-1.2.3.dist-info/LICENSE": BODY})
    assert licenses.inventory_python_licenses(tmp_path, "iris-runtime", site)[0]["license"] == "Apache-2.0 OR BSD-3-Clause"


def test_classifier_preserved_without_guessing_spdx_only_or_later(tmp_path):
    field = "License :: OSI Approved :: GNU General Public License v3 (GPLv3)"
    site, _ = distribution(tmp_path, label="UNKNOWN", fields=f"Classifier: {field}\n",
                           notices={"example-1.2.3.dist-info/COPYING": BODY})
    assert licenses.inventory_python_licenses(tmp_path, "iris-runtime", site)[0]["license"] == "Classifier: " + field


@pytest.mark.parametrize("label,fields", [
    ("UNKNOWN", ""), ("", ""), ("none", ""),
    ("UNKNOWN", "Classifier: License :: OSI Approved\n"),
])
def test_unknown_license_never_becomes_proof_from_notice_presence(tmp_path, label, fields):
    site, _ = distribution(tmp_path, label=label, fields=fields,
                           notices={"example-1.2.3.dist-info/LICENSE": BODY})
    with pytest.raises(licenses.RuntimeLicenseError) as error:
        licenses.inventory_python_licenses(tmp_path, "iris-runtime", site)
    assert error.value.missing == [{
        "package": "example", "version": "1.2.3",
        "reasons": ["Missing explicit license expression, metadata or classifier"],
    }]


@pytest.mark.parametrize("notices", [None, {"example-1.2.3.dist-info/LICENSE": b""},
                                    {"example-1.2.3.dist-info/AUTHORS": b"Names"}])
def test_license_identifier_without_body_fails(tmp_path, notices):
    site, _ = distribution(tmp_path, notices=notices)
    with pytest.raises(licenses.RuntimeLicenseError) as error:
        licenses.inventory_python_licenses(tmp_path, "iris-runtime", site)
    assert "No preserved license/notice body" in error.value.missing[0]["reasons"]


def test_full_license_body_in_metadata_is_preserved(tmp_path):
    body = "MIT License\n Copyright Example\n Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files."
    site, info = distribution(tmp_path, label=body)
    result = licenses.inventory_python_licenses(tmp_path, "iris-runtime", site)
    assert (info / "METADATA").relative_to(tmp_path).as_posix() in result[0]["files"]


def test_all_missing_packages_reported_together(tmp_path):
    site, _ = distribution(tmp_path, label="UNKNOWN")
    distribution(tmp_path, name="other", label="MIT")
    with pytest.raises(licenses.RuntimeLicenseError) as error:
        licenses.inventory_python_licenses(tmp_path, "iris-runtime", site)
    assert [item["package"] for item in error.value.missing] == ["example", "other"]


def test_partial_valid_records_are_available_when_other_packages_lack_notices(tmp_path):
    site, _ = distribution(tmp_path, notices={"example-1.2.3.dist-info/LICENSE": BODY})
    distribution(tmp_path, name="other", label="MIT")
    with pytest.raises(licenses.RuntimeLicenseError) as error:
        licenses.inventory_python_licenses(tmp_path, "iris-runtime", site)
    assert [item["package"] for item in error.value.records] == ["example"]
    assert [item["package"] for item in error.value.missing] == ["other"]


def test_source_code_metadata_url_is_preferred_to_homepage(tmp_path):
    site, info = distribution(tmp_path, notices={"example-1.2.3.dist-info/LICENSE": BODY})
    metadata = (info / "METADATA").read_text("utf8").replace(
        "Project-URL: Source, https://example.org/source",
        "Project-URL: homepage, https://example.org/\nProject-URL: Source Code, https://example.org/source")
    (info / "METADATA").write_text(metadata, encoding="utf8")
    assert licenses.inventory_python_licenses(tmp_path, "iris-runtime", site)[0]["source"] == "https://example.org/source"


def test_missing_declared_notice_fails_even_when_another_notice_exists(tmp_path):
    site, _ = distribution(tmp_path, fields="License-File: LICENSE\nLicense-File: NOTICE\n",
                           notices={"example-1.2.3.dist-info/LICENSE": BODY})
    with pytest.raises(licenses.RuntimeLicenseError) as error:
        licenses.inventory_python_licenses(tmp_path, "iris-runtime", site)
    assert error.value.missing[0]["reasons"] == ["Missing declared License-File: NOTICE"]


def test_exact_version_hashed_fallback_supplies_missing_body_and_label(tmp_path):
    site, _ = distribution(tmp_path, label="UNKNOWN", fields="License-File: LICENSE\n")
    expected = fallback(tmp_path)
    result = licenses.inventory_python_licenses(tmp_path, "iris-runtime", site, fallbacks=expected)
    assert result[0]["license"] == "MIT"
    assert result[0]["files"] == ["licenses/reviewed-example/LICENSE"]
    assert result[0]["source"] == "https://example.org/source/v1.2.3/LICENSE"


def test_fallback_never_applies_to_another_version(tmp_path):
    site, _ = distribution(tmp_path, version="1.2.4", label="UNKNOWN")
    expected = fallback(tmp_path)
    with pytest.raises(licenses.RuntimeLicenseError) as error:
        licenses.inventory_python_licenses(tmp_path, "iris-runtime", site, fallbacks=expected)
    assert error.value.missing[0]["version"] == "1.2.4"


def test_corrupt_fallback_notice_fails(tmp_path):
    site, _ = distribution(tmp_path, label="UNKNOWN")
    expected = fallback(tmp_path)
    (tmp_path / "licenses/reviewed-example/LICENSE").write_bytes(b"corrupt")
    with pytest.raises(licenses.RuntimeLicenseError, match="hash mismatch"):
        licenses.inventory_python_licenses(tmp_path, "iris-runtime", site, fallbacks=expected)


@pytest.mark.parametrize("path", ["/outside/LICENSE", "C:/outside/LICENSE", "C:\\outside\\LICENSE",
                                  "\\\\server\\share\\LICENSE", "pkg/LICENSE:ads"])
def test_absolute_windows_and_ads_record_license_paths_rejected(tmp_path, path):
    site, _ = distribution(tmp_path, record=path + ",,\n")
    with pytest.raises(licenses.RuntimeLicenseError, match="Unsafe license path"):
        licenses.inventory_python_licenses(tmp_path, "iris-runtime", site)


def test_windows_record_separators_resolve_only_inside_staging(tmp_path):
    site, _ = distribution(tmp_path, notices={"example/LICENSE": BODY}, record="example\\LICENSE,,\n")
    assert licenses.inventory_python_licenses(tmp_path, "iris-runtime", site)[0]["files"] == [
        "runtimes/iris/Lib/site-packages/example/LICENSE"]


def test_record_traversal_outside_stage_rejected_before_read(tmp_path):
    stage = tmp_path / "stage"
    stage.mkdir()
    site, _ = distribution(stage, record="../../../../../../LICENSE,,\n")
    (tmp_path / "LICENSE").write_bytes(BODY)
    with pytest.raises((licenses.RuntimeLicenseError, FileNotFoundError)):
        licenses.inventory_python_licenses(stage, "iris-runtime", site)


def test_outside_license_is_rejected_before_filesystem_probe(tmp_path, monkeypatch):
    stage = tmp_path / "stage"
    stage.mkdir()
    outside = tmp_path / "LICENSE"
    original = Path.lstat

    def lstat(path):
        if path == outside:
            raise AssertionError("Must not even inspect a personal notice outside staging")
        return original(path)

    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(licenses.RuntimeLicenseError, match="outside"):
        licenses._safe_path(stage, outside)


def test_record_relative_prefix_notice_inside_stage_is_allowed(tmp_path):
    site, _ = distribution(tmp_path, record="../../licenses/LICENSE,,\n")
    file = tmp_path / "runtimes/iris/licenses/LICENSE"
    file.parent.mkdir(parents=True)
    file.write_bytes(BODY)
    assert licenses.inventory_python_licenses(tmp_path, "iris-runtime", site)[0]["files"] == [
        "runtimes/iris/licenses/LICENSE"]


@pytest.mark.parametrize("kind", ["symlink", "junction", "hardlink"])
def test_linked_notice_evidence_rejected(tmp_path, monkeypatch, kind):
    site, info = distribution(tmp_path, notices={"example-1.2.3.dist-info/LICENSE": BODY})
    file = info / "LICENSE"
    real_lstat = Path.lstat

    def lstat(path):
        if path == file:
            return SimpleNamespace(st_mode=stat.S_IFLNK if kind == "symlink" else stat.S_IFREG,
                                   st_file_attributes=0x400 if kind == "junction" else 0,
                                   st_nlink=2 if kind == "hardlink" else 1)
        return real_lstat(path)

    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(licenses.RuntimeLicenseError, match="linked|hardlink|symlink"):
        licenses.inventory_python_licenses(tmp_path, "iris-runtime", site)


def test_duplicate_metadata_distributions_rejected(tmp_path):
    site, _ = distribution(tmp_path, notices={"example-1.2.3.dist-info/LICENSE": BODY})
    distribution(tmp_path, name="Example", version="2.0.0", notices={"Example-2.0.0.dist-info/LICENSE": BODY})
    with pytest.raises(licenses.RuntimeLicenseError, match="Duplicate"):
        licenses.inventory_python_licenses(tmp_path, "iris-runtime", site)


def test_unknown_component_and_empty_runtime_rejected(tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    with pytest.raises(licenses.RuntimeLicenseError, match="Unknown Python runtime component"):
        licenses.inventory_python_licenses(tmp_path, "unknown", site)
    with pytest.raises(licenses.RuntimeLicenseError, match="No installed distributions"):
        licenses.inventory_python_licenses(tmp_path, "iris-runtime", site)


def test_public_sources_remove_query_secrets_and_ignore_embedded_credentials(tmp_path):
    site, info = distribution(tmp_path, notices={"example-1.2.3.dist-info/LICENSE": BODY})
    metadata = (info / "METADATA").read_text("utf8").replace(
        "https://example.org/source", "https://example.org/source?token=SECRET#fragment")
    (info / "METADATA").write_text(metadata, encoding="utf8")
    assert licenses.inventory_python_licenses(tmp_path, "iris-runtime", site)[0]["source"] == "https://example.org/source"
    (info / "METADATA").write_text(metadata.replace("https://example.org/source?token=SECRET#fragment",
                                                    "https://user:SECRET@example.org/source"), encoding="utf8")
    assert licenses.inventory_python_licenses(tmp_path, "iris-runtime", site)[0]["source"] == "https://pypi.org/project/example/1.2.3/"


@pytest.mark.parametrize("url", ["http://example.org/LICENSE", "file:///secret", "https://user:SECRET@example.org/x",
                                 "https://example.org/LICENSE?token=SECRET", "https://10.0.0.1/LICENSE",
                                 "https://localhost/LICENSE", "https://host.internal/LICENSE"])
def test_fallback_provenance_is_public_https_without_credentials(tmp_path, url):
    site, _ = distribution(tmp_path, label="UNKNOWN")
    expected = fallback(tmp_path)
    expected["example==1.2.3"]["source"] = url
    with pytest.raises(licenses.RuntimeLicenseError, match="public HTTPS"):
        licenses.inventory_python_licenses(tmp_path, "iris-runtime", site, fallbacks=expected)
