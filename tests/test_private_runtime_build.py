"""Builder preflight regressions; synthetic inputs, no downloads or installs."""

from __future__ import annotations

import base64
import copy
import hashlib
import io
import json
import os
import stat
import struct
import tarfile
import zipfile
from unittest.mock import Mock

import pytest

from scripts import build_runtime_wheel as wheel_builder
from scripts import prepare_private_runtime as builder
from scripts.runtime_inventory import read_json


def digest(payload):
    return hashlib.sha256(payload).hexdigest()


def tar_fixture(path, entries):
    with tarfile.open(path, "w") as archive:
        for name, payload, kind in entries:
            member = tarfile.TarInfo(name)
            member.type = kind
            if kind == tarfile.REGTYPE:
                member.size = len(payload)
                archive.addfile(member, io.BytesIO(payload))
            else:
                member.linkname = "outside"
                archive.addfile(member)
    return {"sha256": digest(path.read_bytes()), "archive_prefix": "python/"}


def zip_fixture(path, entries):
    with zipfile.ZipFile(path, "w") as archive:
        for name, payload, mode in entries:
            member = zipfile.ZipInfo(name)
            # ZipInfo normalizes os.sep on Windows during construction. Set
            # the original filename afterwards to retain the hostile archive
            # header; readers expose it as orig_filename even after repair.
            member.filename = name
            member.create_system = 3
            member.external_attr = mode << 16
            archive.writestr(member, payload)
    return {"sha256": digest(path.read_bytes())}


def change_zip_header(path, index, *, flags=None, method=None):
    """Retain matching local/central headers for unsupported ZIP metadata."""
    with zipfile.ZipFile(path) as archive:
        local = archive.infolist()[index].header_offset
        central = archive.start_dir
    payload = bytearray(path.read_bytes())
    for _ in range(index):
        assert payload[central:central + 4] == b"PK\x01\x02"
        lengths = struct.unpack_from("<HHH", payload, central + 28)
        central += 46 + sum(lengths)
    assert payload[local:local + 4] == b"PK\x03\x04"
    assert payload[central:central + 4] == b"PK\x01\x02"
    for value, local_offset, central_offset in ((flags, 6, 8), (method, 8, 10)):
        if value is not None:
            struct.pack_into("<H", payload, local + local_offset, value)
            struct.pack_into("<H", payload, central + central_offset, value)
    path.write_bytes(payload)
    return {"sha256": digest(payload)}


def directory_alias(link, target):
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


def remove_alias(link):
    link.rmdir() if os.name == "nt" else link.unlink()


def test_regular_inputs_are_hashed_and_hardlinks_never_read(tmp_path):
    source = tmp_path / "public input.txt"
    source.write_bytes(b"synthetic")
    assert builder.regular_file(source, digest(b"synthetic")) == source
    with pytest.raises(ValueError, match="hash mismatch"):
        builder.regular_file(source, "0" * 64)
    alias = tmp_path / "alias.txt"
    alias.hardlink_to(source)
    with pytest.raises(ValueError, match="Non-regular"):
        builder.regular_file(alias)
    assert source.read_bytes() == b"synthetic"


def test_linked_input_parent_and_output_parent_never_modify_external_directory(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    source = outside / "source.txt"
    source.write_bytes(b"untouched")
    alias = tmp_path / "alias"
    directory_alias(alias, outside)
    try:
        with pytest.raises(ValueError):
            builder.regular_file(alias / "source.txt")
        with pytest.raises(ValueError):
            builder.new_directory(alias / "new stage")
        assert not (outside / "new stage").exists()
        assert source.read_bytes() == b"untouched"
    finally:
        remove_alias(alias)


@pytest.mark.parametrize("kind", ["directory", "file", "hardlink"])
def test_existing_output_is_preserved(tmp_path, kind):
    output = tmp_path / "candidate"
    source = tmp_path / "external.txt"
    source.write_bytes(b"external content")
    if kind == "directory":
        output.mkdir()
        (output / "keep.txt").write_bytes(b"keep")
    elif kind == "file":
        output.write_bytes(b"keep")
    else:
        output.hardlink_to(source)
    with pytest.raises(ValueError):
        builder.new_directory(output)
    assert source.read_bytes() == b"external content"
    assert (output / "keep.txt").read_bytes() == b"keep" if kind == "directory" else output.exists()


def test_python_extraction_preserves_stdlib_and_notices_without_installer_or_caches(tmp_path):
    source, output = tmp_path / "python.tar", tmp_path / "runtime"
    files = {
        "python/python.exe": b"synthetic PE",
        "python/LICENSE.txt": b"Python notices",
        "python/Lib/json/__init__.py": b"stdlib source",
        "python/licenses/libffi.txt": b"third party notices",
        "python/Lib/site-packages/private.txt": b"excluded",
        "python/Scripts/pip.exe": b"excluded",
        "python/Lib/venv/scripts/nt/python.exe": b"excluded",
        "python/Lib/__pycache__/json.pyc": b"excluded",
        "python/pythonw.exe": b"excluded",
    }
    entries = [(name, payload, tarfile.REGTYPE) for name, payload in files.items()]
    spec = tar_fixture(source, entries)
    builder.extract_python(source, output, spec)
    assert (output / "Lib/json/__init__.py").read_bytes() == b"stdlib source"
    assert (output / "LICENSE.txt").read_bytes() == b"Python notices"
    assert (output / "licenses/libffi.txt").read_bytes() == b"third party notices"
    assert not (output / "Lib/site-packages").exists()
    assert not (output / "Scripts").exists()
    assert not (output / "pythonw.exe").exists()


@pytest.mark.parametrize("name", [
    "/python/evil.py", "../python/evil.py", "python/../evil.py", "python/./evil.py",
    r"python\evil.py", "C:/evil.py", r"\\host\share\evil.py", "other/evil.py",
    "python/evil.py:stream", "python//evil.py", "python/NUL.py",
])
def test_python_archive_paths_are_rejected_before_any_output(tmp_path, name):
    source, output = tmp_path / "python.tar", tmp_path / "runtime"
    spec = tar_fixture(source, [
        ("python/python.exe", b"first valid member", tarfile.REGTYPE),
        (name, b"must not extract", tarfile.REGTYPE),
    ])
    with pytest.raises(ValueError):
        builder.extract_python(source, output, spec)
    assert not output.exists()


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE])
def test_python_archive_links_and_special_files_are_rejected_before_output(tmp_path, kind):
    source, output = tmp_path / "python.tar", tmp_path / "runtime"
    spec = tar_fixture(source, [
        ("python/python.exe", b"valid", tarfile.REGTYPE),
        ("python/alias", b"", kind),
    ])
    with pytest.raises(ValueError):
        builder.extract_python(source, output, spec)
    assert not output.exists()


def test_python_duplicate_case_aliases_and_wrong_hash_fail_before_output(tmp_path):
    source, output = tmp_path / "python.tar", tmp_path / "runtime"
    spec = tar_fixture(source, [
        ("python/Lib/Thing.py", b"first", tarfile.REGTYPE),
        ("python/Lib/thing.py", b"alias", tarfile.REGTYPE),
    ])
    with pytest.raises(ValueError, match="Ambiguous"):
        builder.extract_python(source, output, spec)
    assert not output.exists()
    with pytest.raises(ValueError, match="hash mismatch"):
        builder.extract_python(source, output, dict(spec, sha256="0" * 64))
    assert not output.exists()


@pytest.mark.parametrize("component", ["python", "audio"])
def test_hardlinked_archive_input_is_rejected_before_output(tmp_path, component):
    source = tmp_path / "source.archive"
    source.write_bytes(b"synthetic source must remain unchanged")
    alias = tmp_path / "archive alias"
    alias.hardlink_to(source)
    output = tmp_path / "runtime"
    spec = {"sha256": digest(source.read_bytes()), "archive_prefix": "python/"}
    method = builder.extract_python if component == "python" else builder.extract_audio
    with pytest.raises(ValueError):
        method(alias, output, spec)
    assert not output.exists()
    assert source.read_bytes() == b"synthetic source must remain unchanged"


@pytest.mark.parametrize("component", ["python", "audio"])
def test_archive_expanded_byte_bound_is_checked_before_output(tmp_path, monkeypatch, component):
    source, output = tmp_path / "source.archive", tmp_path / "runtime"
    if component == "python":
        spec = tar_fixture(source, [("python/python.exe", b"large fixture", tarfile.REGTYPE)])
        method = builder.extract_python
    else:
        spec = zip_fixture(source, [("bin/ffmpeg.exe", b"large fixture", stat.S_IFREG)])
        method = builder.extract_audio
    monkeypatch.setattr(builder, "MAX_EXPANDED_BYTES", 2)
    with pytest.raises(ValueError, match="limits"):
        method(source, output, spec)
    assert not output.exists()


@pytest.mark.parametrize("component", ["python", "audio"])
def test_extraction_output_alias_is_rejected_before_creating_external_content(tmp_path, component):
    source = tmp_path / "source.archive"
    if component == "python":
        spec = tar_fixture(source, [("python/python.exe", b"valid", tarfile.REGTYPE)])
        method = builder.extract_python
    else:
        spec = zip_fixture(source, [("bin/ffmpeg.exe", b"valid", stat.S_IFREG)])
        method = builder.extract_audio
    outside = tmp_path / "external directory"
    outside.mkdir()
    marker = outside / "keep.txt"
    marker.write_bytes(b"unchanged synthetic external data")
    alias = tmp_path / "output alias"
    directory_alias(alias, outside)
    try:
        with pytest.raises(ValueError, match="extraction output"):
            method(source, alias / "nested" / "new candidate", spec)
        assert list(outside.iterdir()) == [marker]
        assert marker.read_bytes() == b"unchanged synthetic external data"
    finally:
        remove_alias(alias)


@pytest.mark.parametrize("relative", ["tools/audio", "licenses/audio"])
def test_audio_nested_output_alias_is_rejected_before_extracting_any_member(tmp_path, relative):
    source, output = tmp_path / "audio.zip", tmp_path / "candidate"
    spec = zip_fixture(source, [
        ("bin/ffmpeg.exe", b"valid", stat.S_IFREG),
        ("licenses/audio/NOTICE", b"valid notice", stat.S_IFREG),
    ])
    outside = tmp_path / "external directory"
    outside.mkdir()
    marker = outside / "keep.txt"
    marker.write_bytes(b"unchanged")
    alias = output / relative
    alias.parent.mkdir(parents=True)
    directory_alias(alias, outside)
    try:
        with pytest.raises(ValueError, match="extraction output"):
            builder.extract_audio(source, output, spec)
        assert list(outside.iterdir()) == [marker]
        assert marker.read_bytes() == b"unchanged"
        assert not (output / "tools/audio/bin/ffmpeg.exe").exists()
    finally:
        remove_alias(alias)


@pytest.mark.parametrize("kind", ["ordinary", "hardlink"])
def test_audio_existing_member_is_preserved_before_any_new_member_is_written(tmp_path, kind):
    source, output = tmp_path / "audio.zip", tmp_path / "candidate"
    spec = zip_fixture(source, [
        ("bin/ffmpeg.exe", b"new executable", stat.S_IFREG),
        ("licenses/audio/NOTICE", b"new notice", stat.S_IFREG),
    ])
    external = tmp_path / "external notice.txt"
    external.write_bytes(b"existing notice")
    target = output / "licenses/audio/NOTICE"
    target.parent.mkdir(parents=True)
    if kind == "hardlink":
        target.hardlink_to(external)
    else:
        target.write_bytes(b"existing notice")
    with pytest.raises(ValueError, match="replace existing content"):
        builder.extract_audio(source, output, spec)
    assert target.read_bytes() == external.read_bytes() == b"existing notice"
    assert not (output / "tools").exists()


@pytest.mark.parametrize("name,mode", [
    ("/bin/evil.dll", stat.S_IFREG), ("../bin/evil.dll", stat.S_IFREG),
    (r"bin\evil.dll", stat.S_IFREG), ("C:/bin/evil.dll", stat.S_IFREG),
    ("bin/ffplay.exe", stat.S_IFREG), ("bin/alias.dll", stat.S_IFLNK),
    ("bin/pipe.dll", stat.S_IFIFO), ("private/token.txt", stat.S_IFREG),
    ("bin/nested/foreign.dll", stat.S_IFREG), ("../ignored/", stat.S_IFDIR),
    (r"bin\ignored/", stat.S_IFDIR), ("licenses/aliased/", stat.S_IFLNK),
])
def test_audio_archive_paths_types_and_nested_binaries_fail_before_output(tmp_path, name, mode):
    source, output = tmp_path / "audio.zip", tmp_path / "runtime"
    spec = zip_fixture(source, [
        ("bin/ffmpeg.exe", b"valid", stat.S_IFREG), (name, b"invalid", mode),
    ])
    with pytest.raises(ValueError):
        builder.extract_audio(source, output, spec)
    assert not output.exists()


@pytest.mark.parametrize("name,mode", [
    ("bin/", stat.S_IFREG), ("licenses/audio/", stat.S_IFREG),
    ("bin", stat.S_IFDIR), ("licenses/audio", stat.S_IFDIR),
])
def test_audio_directory_header_type_must_agree_with_name_before_output(tmp_path, name, mode):
    source, output = tmp_path / "audio.zip", tmp_path / "runtime"
    spec = zip_fixture(source, [
        ("bin/ffmpeg.exe", b"valid", stat.S_IFREG), (name, b"", mode),
    ])
    with pytest.raises(ValueError, match="directory type disagrees"):
        builder.extract_audio(source, output, spec)
    assert not output.exists()


@pytest.mark.parametrize("name,mode", [("bin/avcodec.dll", stat.S_IFREG), ("licenses/audio/", stat.S_IFDIR)])
@pytest.mark.parametrize("metadata", ["encrypted", "bzip2", "lzma", "unknown"])
def test_audio_unsupported_zip_headers_are_rejected_even_for_directories(tmp_path, name, mode, metadata):
    source, output = tmp_path / "audio.zip", tmp_path / "runtime"
    zip_fixture(source, [
        ("bin/ffmpeg.exe", b"valid", stat.S_IFREG), (name, b"", mode),
    ])
    if metadata == "encrypted":
        spec = change_zip_header(source, 1, flags=1)
        message = "Encrypted audio archives"
    else:
        method = {"bzip2": zipfile.ZIP_BZIP2, "lzma": zipfile.ZIP_LZMA, "unknown": 99}[metadata]
        spec = change_zip_header(source, 1, method=method)
        message = "compression method"
    with pytest.raises(ValueError, match=message):
        builder.extract_audio(source, output, spec)
    assert not output.exists()


@pytest.mark.parametrize("mode", [0, stat.S_IFDIR])
def test_audio_ordinary_directory_headers_allow_approved_payload(tmp_path, mode):
    source, output = tmp_path / "audio.zip", tmp_path / "runtime"
    spec = zip_fixture(source, [
        ("bin/", b"", mode), ("licenses/", b"", mode),
        ("bin/ffmpeg.exe", b"valid", stat.S_IFREG),
    ])
    builder.extract_audio(source, output, spec)
    assert (output / "tools/audio/bin/ffmpeg.exe").read_bytes() == b"valid"


def test_audio_archive_preserves_approved_binaries_and_license_payload(tmp_path):
    source, output = tmp_path / "audio.zip", tmp_path / "runtime"
    spec = zip_fixture(source, [
        ("bin/ffmpeg.exe", b"ffmpeg", stat.S_IFREG),
        ("bin/ffprobe.exe", b"ffprobe", stat.S_IFREG),
        ("bin/avcodec.dll", b"dll", stat.S_IFREG),
        ("licenses/audio/COPYING.LGPLv2.1", b"license", stat.S_IFREG),
    ])
    builder.extract_audio(source, output, spec)
    assert (output / "tools/audio/bin/ffmpeg.exe").read_bytes() == b"ffmpeg"
    assert (output / "licenses/audio/COPYING.LGPLv2.1").read_bytes() == b"license"


def test_cached_archive_preserves_a_preexisting_download_on_failure(tmp_path, monkeypatch):
    destination = tmp_path / "approved.tar.download"
    destination.write_bytes(b"must survive")
    spec = {"filename": "approved.tar", "sha256": digest(b"new"), "url": "https://example.test/archive"}
    class Response(io.BytesIO):
        def geturl(self):
            return "https://example.test/archive"

    monkeypatch.setattr(builder.urllib.request, "urlopen", lambda *a, **kw: Response(b"new"))
    try:
        builder.cached_archive(tmp_path, spec, offline=False)
    except (ValueError, FileExistsError):
        pass
    assert destination.read_bytes() == b"must survive"


def test_locked_install_uses_only_wheels_hashes_copy_and_clean_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "developer environment")
    monkeypatch.setenv("VIRTUAL_ENV", "developer environment")
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", "developer environment")
    monkeypatch.setenv("HERMES_HOME", "developer profile")
    run = Mock()
    monkeypatch.setattr(builder.subprocess, "run", run)
    builder.install_locked(tmp_path / "uv.exe", tmp_path / "runtime/python.exe",
                           tmp_path / "lock.txt", tmp_path / "cache", offline=True)
    command = run.call_args.args[0]
    assert "--require-hashes" in command and "--no-deps" in command
    assert "--only-binary=:all:" in command or "--no-build" in command
    assert command[command.index("--link-mode") + 1] == "copy"
    assert "--offline" in command
    environment = run.call_args.kwargs["env"]
    assert environment["PATH"] == ""
    forbidden = {"PYTHONPATH", "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT", "HERMES_HOME"}
    assert not forbidden & set(environment)


@pytest.fixture
def build_inputs(tmp_path, monkeypatch):
    root = tmp_path / "source"
    root.mkdir()
    pin = copy.deepcopy(read_json(builder.ROOT / "scripts/runtime_sources.json"))
    # Local wheel overlays and exclusions have dedicated tests; these inputs exercise the base layout.
    pin.pop("local_wheels", None)
    pin.pop("local_exclusions", None)
    pin.pop("dependency_overrides", None)  # synthetic Hermes source has no real dependency exception
    pin.pop("shell", None)  # this fixture models Python/audio; shell has its own extraction tests
    # Each runtime's lock is named after its own CPython (cp311, or cp314 for Hermes 0.21.6).
    lock_files = {
        name: f"scripts/locks/{name}-windows-cp{builder.python_tag(builder.runtime_python(pin, name))}.txt"
        for name in ("iris", "hermes")
    }
    files = {
        "pyproject.toml": b'[tool.setuptools.data-files]\n"share/iris" = []\n',
        "uv.lock": b"synthetic source lock",
        lock_files["iris"]: b"synthetic iris lock",
        lock_files["hermes"]: b"synthetic hermes lock",
        "scripts/runtime_native_license_sources.json": b"synthetic native notice manifest",
    }
    for filename, payload in files.items():
        target = root / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    cache = tmp_path / "cache"
    cache.mkdir()
    archives = [("python", "python.tar"), ("audio", "audio.zip")]
    if "hermes_python" in pin:
        archives.append(("hermes_python", "hermes-python.tar"))
    for component, name in archives:
        pin[component]["filename"] = name
        payload = b"synthetic archive input"
        (cache / name).write_bytes(payload)
        pin[component]["sha256"] = digest(payload)
    pin["hermes"]["source_archive_filename"] = "hermes.tar"
    (cache / "hermes.tar").write_bytes(b"synthetic hermes archive")
    pin["hermes"]["source_archive_sha256"] = digest(b"synthetic hermes archive")
    pin["iris"]["pyproject_sha256"] = digest(files["pyproject.toml"])
    pin["iris"]["uv_lock_sha256"] = digest(files["uv.lock"])
    pin["native_license_sources_sha256"] = digest(files["scripts/runtime_native_license_sources.json"])
    for component in ("iris", "hermes"):
        payload = files[lock_files[component]]
        pin["locks"][component]["sha256"] = digest(payload)
    wheel = tmp_path / "iris.whl"
    wheel.write_bytes(b"synthetic wheel")
    pin["iris"]["wheel_sha256"] = digest(wheel.read_bytes())
    uv = tmp_path / "uv.exe"
    uv.write_bytes(b"synthetic builder tool")
    monkeypatch.setattr(builder, "ROOT", root)
    monkeypatch.setattr(builder, "verify_archive", Mock())
    return root, cache, wheel, uv, pin


@pytest.mark.parametrize("review", [
    None, [], {}, {"pending": []}, {"records": []},
    {"pending": [], "records": [], "approved": True},
])
def test_native_license_review_cannot_be_missing_or_have_unknown_fields(review):
    with pytest.raises(ValueError, match="Missing or unknown native license review"):
        builder.reviewed_native_licenses({"native_licenses": review})
    with pytest.raises(ValueError, match="Missing or unknown native license review"):
        builder.reviewed_native_licenses({})


@pytest.mark.parametrize("pending,records", [
    (None, []), ("PyAV", []), ({}, []), ([None], []), ([1], []), ([""], []),
    ([], None), ([], {}), ([], "BSD-3-Clause"),
])
def test_native_license_review_lists_require_component_names_and_records(pending, records):
    with pytest.raises(ValueError, match="Invalid native license review"):
        builder.reviewed_native_licenses({"native_licenses": {"pending": pending, "records": records}})


@pytest.mark.parametrize("records", [[], ["BSD-3-Clause"], [None], [{}, "unknown"]])
def test_native_license_review_requires_component_notice_records(records):
    with pytest.raises(ValueError, match="preserved component notice records"):
        builder.reviewed_native_licenses({"native_licenses": {"pending": [], "records": records}})


def test_wrapper_license_does_not_clear_pending_native_notice_review():
    record = {
        "component": "iris-runtime", "package": "av", "version": "synthetic",
        "license": "BSD-3-Clause", "files": ["wrapper/LICENSE"],
        "source": "https://example.test/wrapper",
    }
    review = {"pending": ["PyAV FFmpeg native dependencies"], "records": [record]}
    with pytest.raises(ValueError, match="PyAV FFmpeg native dependencies"):
        builder.reviewed_native_licenses({"native_licenses": review})
    assert review == {"pending": ["PyAV FFmpeg native dependencies"], "records": [record]}


def test_completed_native_notice_review_preserves_the_component_evidence():
    records = [{
        "component": "iris-runtime", "package": "native-example", "version": "synthetic",
        "license": "MIT", "files": ["licenses/native-example/NOTICE"],
        "source": "https://example.test/native-example",
    }]
    assert builder.reviewed_native_licenses({"native_licenses": {"pending": [], "records": records}}) == records


def test_pending_native_review_preserves_prepared_candidate_without_sealing_inventory(
    build_inputs, tmp_path, monkeypatch,
):
    root, cache, wheel, uv, pin = build_inputs
    checker = root / "scripts/verify_runtime_dependencies.py"
    checker.write_bytes(b"synthetic dependency checker")
    pin["native_licenses"] = {"pending": ["native dependency notices"], "records": []}
    output = tmp_path / "new-stage"

    def python_payload(_archive, destination, _spec):
        destination.mkdir(parents=True)
        (destination / "LICENSE.txt").write_bytes(b"preserved CPython notice")

    def audio_payload(_archive, stage, _spec):
        notices = stage / "licenses/audio"
        notices.mkdir(parents=True)
        (notices / "components.json").write_text(json.dumps({"licenses": []}), encoding="utf8")
        (notices / "NOTICE").write_bytes(b"preserved synthetic audio notice")

    monkeypatch.setattr(builder, "extract_python", python_payload)
    monkeypatch.setattr(builder, "extract_audio", audio_payload)
    monkeypatch.setattr(builder, "extract_hermes_source", Mock())
    monkeypatch.setattr(builder, "install_locked", Mock())
    monkeypatch.setattr(builder.subprocess, "run", Mock())
    monkeypatch.setattr(builder, "normalize_runtime_layout", Mock())
    monkeypatch.setattr(builder, "prepare_python_license_fallbacks", Mock(return_value={}))
    monkeypatch.setattr(builder, "inventory_python_licenses", Mock(return_value=[]))
    collect_native = Mock(return_value=[])
    monkeypatch.setattr(builder, "prepare_native_license_records", collect_native)
    seal, verify = Mock(), Mock()
    monkeypatch.setattr(builder, "build_inventory", seal)
    monkeypatch.setattr(builder, "verify_runtime", verify)

    with pytest.raises(ValueError, match="native dependency notices"):
        builder.prepare_runtime(output, cache, wheel, uv, pin, offline=True)

    assert (output / "licenses/audio/NOTICE").read_bytes() == b"preserved synthetic audio notice"
    for component in ("iris", "hermes"):
        assert (output / f"runtimes/{component}/LICENSE.txt").read_bytes() == b"preserved CPython notice"
    assert (output / "locks/iris-pyproject.toml").read_bytes() == (root / "pyproject.toml").read_bytes()
    assert not (output / "runtime-inventory.json").exists()
    assert not (output / "runtime-pin.json").exists()
    assert (output / "licenses/native/sources.json").read_bytes() == (
        root / "scripts/runtime_native_license_sources.json"
    ).read_bytes()
    collect_native.assert_called_once_with(
        output, root / "scripts/runtime_native_license_sources.json",
        expected_manifest_sha256=pin["native_license_sources_sha256"],
    )
    seal.assert_not_called()
    verify.assert_not_called()


@pytest.mark.parametrize("value", [None, "", "0" * 63, "G" * 64, 1, []])
def test_native_manifest_requires_exact_hash_before_output_or_cache(build_inputs, tmp_path, value):
    _, _, wheel, uv, pin = build_inputs
    pin["native_license_sources_sha256"] = value
    output, cache = tmp_path / "new-stage", tmp_path / "new-cache"
    with pytest.raises(ValueError, match="native license source manifest hash"):
        builder.prepare_runtime(output, cache, wheel, uv, pin, offline=True)
    assert not output.exists() and not cache.exists()


def test_changed_native_manifest_is_rejected_before_creating_output(build_inputs, tmp_path):
    root, cache, wheel, uv, pin = build_inputs
    (root / "scripts/runtime_native_license_sources.json").write_bytes(b"changed notice provenance")
    output = tmp_path / "new-stage"
    with pytest.raises(ValueError, match="hash mismatch"):
        builder.prepare_runtime(output, cache, wheel, uv, pin, offline=True)
    assert not output.exists()


@pytest.mark.parametrize("schema", [None, True, 0, 2, "1"])
def test_unknown_pin_schema_never_creates_output_or_cache(build_inputs, tmp_path, schema):
    _, _, wheel, uv, pin = build_inputs
    pin["schema"] = schema
    output, cache = tmp_path / "new-stage", tmp_path / "new-cache"
    with pytest.raises(ValueError):
        builder.prepare_runtime(output, cache, wheel, uv, pin, offline=True)
    assert not output.exists() and not cache.exists()


def test_invalid_tool_or_wheel_never_creates_cache(build_inputs, tmp_path):
    _, _, wheel, _, pin = build_inputs
    output, cache = tmp_path / "new-stage", tmp_path / "new-cache"
    with pytest.raises((ValueError, OSError)):
        builder.prepare_runtime(
            output, cache, wheel, tmp_path / "missing-uv.exe", pin, offline=True,
        )
    assert not output.exists() and not cache.exists()


def test_cache_alias_is_rejected_before_creating_a_nested_directory(build_inputs, tmp_path):
    _, _, wheel, uv, pin = build_inputs
    outside = tmp_path / "outside cache"
    outside.mkdir()
    alias = tmp_path / "cache alias"
    directory_alias(alias, outside)
    output = tmp_path / "new-stage"
    try:
        with pytest.raises((ValueError, OSError)):
            builder.prepare_runtime(output, alias / "new nested", wheel, uv, pin, offline=True)
        assert not output.exists() and not (outside / "new nested").exists()
    finally:
        remove_alias(alias)


@pytest.mark.parametrize("component,key", [
    ("python", "filename"), ("audio", "filename"), ("hermes", "source_archive_filename"),
])
@pytest.mark.parametrize("bad", [
    "/absolute.tar", "../foreign.tar", r"cache\foreign.tar", "C:/foreign.tar", None, [],
])
def test_pin_archive_names_are_portable_basenames(build_inputs, tmp_path, component, key, bad):
    _, cache, wheel, uv, pin = build_inputs
    pin[component][key] = bad
    output = tmp_path / "new-stage"
    with pytest.raises(ValueError):
        builder.prepare_runtime(output, cache, wheel, uv, pin, offline=True)
    assert not output.exists()


def test_unknown_lock_role_cannot_choose_arbitrary_input_or_output(build_inputs, tmp_path):
    _, cache, wheel, uv, pin = build_inputs
    pin["locks"]["../../foreign"] = {"path": "/outside", "sha256": "0" * 64}
    output = tmp_path / "new-stage"
    with pytest.raises(ValueError):
        builder.prepare_runtime(output, cache, wheel, uv, pin, offline=True)
    assert not output.exists()


@pytest.mark.parametrize("prefix", [None, [], "/python/", "../python/", "python\\", "python"])
def test_python_archive_prefix_requires_a_portable_directory(build_inputs, tmp_path, prefix):
    _, cache, wheel, uv, pin = build_inputs
    pin["python"]["archive_prefix"] = prefix
    output = tmp_path / "new-stage"
    with pytest.raises(ValueError):
        builder.prepare_runtime(output, cache, wheel, uv, pin, offline=True)
    assert not output.exists()


def test_cli_unknown_options_never_begin_a_build(monkeypatch, tmp_path):
    prepare, build = Mock(), Mock()
    monkeypatch.setattr(builder, "prepare_runtime", prepare)
    monkeypatch.setattr(wheel_builder, "build_runtime_wheel", build)
    with pytest.raises(SystemExit):
        builder.main([
            "--output", str(tmp_path / "output"), "--cache-root", str(tmp_path / "cache"),
            "--iris-wheel", str(tmp_path / "iris.whl"), "--uv", str(tmp_path / "uv.exe"),
            "--method", "unknown",
        ])
    with pytest.raises(SystemExit):
        wheel_builder.main([
            "--work-root", str(tmp_path / "work"), "--python", str(tmp_path / "python.exe"),
            "--web-root", str(tmp_path / "web"), "--method", "unknown",
        ])
    prepare.assert_not_called()
    build.assert_not_called()


def test_wheel_source_alias_fails_before_creating_worktree(tmp_path, monkeypatch):
    root = tmp_path / "source"
    (root / "core").mkdir(parents=True)
    external = tmp_path / "external.py"
    external.write_bytes(b"synthetic developer secret")
    (root / "core/secret.py").hardlink_to(external)
    interpreter = tmp_path / "builder-python.exe"
    interpreter.write_bytes(b"synthetic builder")
    monkeypatch.setattr(wheel_builder, "ROOT", root)
    monkeypatch.setattr(wheel_builder, "prepare_assets", lambda *a: {})
    monkeypatch.setattr(wheel_builder, "prepare_web_assets", lambda *a: {})
    output = tmp_path / "wheel-work"
    with pytest.raises(ValueError):
        wheel_builder.build_runtime_wheel(output, interpreter, tmp_path / "web")
    assert not output.exists()
    assert external.read_bytes() == b"synthetic developer secret"


LOCK = (
    "attrs==25.4.0 \\\n    --hash=sha256:" + "1" * 64 + "\n"
    "av==17.0.0 \\\n    --hash=sha256:" + "2" * 64 + " \\\n    --hash=sha256:" + "3" * 64 + "\n"
    "babel==2.17.0 \\\n    --hash=sha256:" + "4" * 64 + "\n"
)


def overlay(**changes):
    item = {
        "package": "av", "version": "17.0.0", "lock": "hermes",
        "filename": "av-17.0.0-cp311-abi3-win_amd64.whl", "sha256": "5" * 64,
        "producer": "scripts/build_pyav_wheel.py",
        "reason": "Official wheel bundles GPL FFmpeg components without their sources.",
    }
    for key, name in (("build_sources_sha256", "pyav_build_sources.json"),
                      ("sdk_sources_sha256", "pyav_ffmpeg_sources.json")):
        if key not in changes:
            item[key] = builder.file_hash(builder.ROOT / "scripts" / name)
    item.update(changes)
    return item


def test_effective_lock_removes_only_the_replaced_hashed_requirement():
    result = builder.effective_lock(LOCK, [overlay()])
    assert "av==" not in result and "2" * 64 not in result and "3" * 64 not in result
    assert result == LOCK.replace(LOCK[LOCK.index("av=="):LOCK.index("babel==")], "")
    assert builder.effective_lock(LOCK, []) == LOCK


@pytest.mark.parametrize("lock,message", [
    (LOCK.replace("av==17.0.0", "av==16.1.0"), "does not match"),
    (LOCK.replace("av==17.0.0 \\", "av==17.0.0 ; sys_platform == 'win32' \\"), "does not match"),
    (LOCK.replace("av==17.0.0", "avx==17.0.0"), "exactly one"),
    (LOCK + LOCK[LOCK.index("av=="):LOCK.index("babel==")], "exactly one"),
    (LOCK.replace("    --hash=sha256:" + "3" * 64, "    -e ../evil"), "Unexpected content"),
])
def test_effective_lock_fails_closed_on_ambiguous_replacements(lock, message):
    with pytest.raises(ValueError, match=message):
        builder.effective_lock(lock, [overlay()])


# Hermes 0.21.6 locks av under faster-whisper's platform marker (A08.4b).
WHISPER_MARKER = ("(platform_machine != 'x86_64' and sys_platform == 'darwin') or "
                  "(platform_machine != 'ARM64' and sys_platform == 'win32') or "
                  "(sys_platform != 'darwin' and sys_platform != 'win32')")


def test_a_marker_true_for_the_runtime_still_lets_the_overlay_replace_it():
    lock = LOCK.replace("av==17.0.0 \\", f"av==17.0.0 ; {WHISPER_MARKER} \\")
    environment = builder.target_environment({"version": "3.14.8"})
    result = builder.effective_lock(lock, [overlay()], environment)
    assert result == LOCK.replace(LOCK[LOCK.index("av=="):LOCK.index("babel==")], "")


@pytest.mark.parametrize("marker", [
    "sys_platform == 'darwin'", "platform_machine == 'ARM64'", "python_version < '3.12'", "not a marker",
])
def test_a_marker_false_or_invalid_for_the_runtime_is_never_replaced(marker):
    lock = LOCK.replace("av==17.0.0 \\", f"av==17.0.0 ; {marker} \\")
    with pytest.raises(ValueError, match="does not match"):
        builder.effective_lock(lock, [overlay()], builder.target_environment({"version": "3.14.8"}))


def test_the_runtime_environment_is_windows_amd64_with_its_own_cpython():
    environment = builder.target_environment({"version": "3.14.8"})
    assert (environment["sys_platform"], environment["platform_machine"]) == ("win32", "AMD64")
    assert (environment["python_version"], environment["python_full_version"]) == ("3.14", "3.14.8")


def test_local_wheels_accept_only_reviewed_producers_and_current_pins():
    assert builder.local_wheels({}) == []
    assert builder.local_wheels({"local_wheels": [overlay()]}) == [overlay()]


@pytest.mark.parametrize("changes", [
    {"package": "numpy"}, {"lock": "iris"}, {"producer": "scripts/other.py"},
    {"version": "latest"},
    {"filename": "../av-17.0.0-cp311-abi3-win_amd64.whl"}, {"filename": "av-17.0.0.tar.gz"},
    {"filename": "other-17.0.0-cp311-abi3-win_amd64.whl"}, {"sha256": "5" * 63},
    {"reason": " "}, {"extra": True},
])
def test_local_wheels_reject_unknown_or_unpinned_overlays(changes):
    with pytest.raises(ValueError):
        builder.local_wheels({"local_wheels": [overlay(**changes)]})


@pytest.mark.parametrize("key", ["build_sources_sha256", "sdk_sources_sha256"])
def test_local_wheels_require_the_build_pins_they_were_produced_from(key):
    with pytest.raises(ValueError, match="rebuild and review"):
        builder.local_wheels({"local_wheels": [overlay(**{key: "0" * 64})]})


def test_local_wheels_reject_duplicates_and_non_lists():
    with pytest.raises(ValueError):
        builder.local_wheels({"local_wheels": [overlay(), overlay()]})
    with pytest.raises(ValueError):
        builder.local_wheels({"local_wheels": overlay()})


def record_wheel(path, files):
    rows = []
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
            encoded = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
            rows.append(f"{name},sha256={encoded},{len(data)}")
        rows.append("av-17.0.0.dist-info/RECORD,,")
        archive.writestr("av-17.0.0.dist-info/RECORD", "\n".join(rows) + "\n")


def test_installed_overlay_must_match_every_approved_wheel_file(tmp_path):
    files = {"av/__init__.py": b"approved", "av.libs/avcodec-62-abc.dll": b"lgpl build",
             "av-17.0.0.dist-info/METADATA": b"Name: av"}
    wheel, site = tmp_path / "av.whl", tmp_path / "site"
    record_wheel(wheel, files)
    for name, data in files.items():
        (site / name).parent.mkdir(parents=True, exist_ok=True)
        (site / name).write_bytes(data)
    builder.verify_local_wheel_install(site, wheel)
    (site / "av.libs/avcodec-62-abc.dll").write_bytes(b"official gpl build")
    with pytest.raises(ValueError, match="differs from the approved local wheel"):
        builder.verify_local_wheel_install(site, wheel)
    (site / "av.libs/avcodec-62-abc.dll").unlink()
    with pytest.raises((ValueError, OSError)):
        builder.verify_local_wheel_install(site, wheel)


def test_missing_local_wheel_pins_or_artifact_fail_before_output(build_inputs, tmp_path):
    root, cache, wheel, uv, pin = build_inputs
    pin["local_wheels"] = [overlay(build_sources_sha256="0" * 64, sdk_sources_sha256="0" * 64)]
    with pytest.raises(ValueError, match="Missing local wheel build pin"):
        builder.prepare_runtime(tmp_path / "candidate", cache, wheel, uv, pin, offline=True)
    assert not (tmp_path / "candidate").exists()
    for name in ("pyav_build_sources.json", "pyav_ffmpeg_sources.json"):
        (root / "scripts" / name).write_bytes(b"synthetic pin")
    synthetic = digest(b"synthetic pin")
    pin["local_wheels"] = [overlay(build_sources_sha256=synthetic, sdk_sources_sha256=synthetic)]
    with pytest.raises(ValueError, match="Approved local wheel missing from cache"):
        builder.prepare_runtime(tmp_path / "candidate", cache, wheel, uv, pin, offline=True)
    assert not (tmp_path / "candidate").exists()


def test_installer_cleanup_removes_launchers_and_their_record_rows_only(tmp_path):
    site = tmp_path / "site"
    for name, data in {"bin/distro.exe": b"launcher with variable bytes",
                       "distro/__init__.py": b"package",
                       "iris_os-1.0.0.dist-info/direct_url.json": b"{}"}.items():
        (site / name).parent.mkdir(parents=True, exist_ok=True)
        (site / name).write_bytes(data)
    record = site / "distro-1.9.0.dist-info/RECORD"
    record.parent.mkdir(parents=True)
    record.write_text("distro/__init__.py,sha256=abc,7\nbin/distro.exe,sha256=xyz,28\n"
                      "distro-1.9.0.dist-info/RECORD,,\n", encoding="utf8", newline="")
    untouched = site / "other-1.0.dist-info/RECORD"
    untouched.parent.mkdir(parents=True)
    untouched.write_text("other/__init__.py,sha256=def,3\n", encoding="utf8", newline="")
    before = untouched.read_bytes()
    builder.clean_installer_metadata(site)
    assert not (site / "bin").exists()
    assert not (site / "iris_os-1.0.0.dist-info/direct_url.json").exists()
    assert record.read_text(encoding="utf8") == ("distro/__init__.py,sha256=abc,7\n"
                                                 "distro-1.9.0.dist-info/RECORD,,\n")
    assert untouched.read_bytes() == before
    assert (site / "distro/__init__.py").read_bytes() == b"package"


# ── A08.4b: Hermes 0.21.6 runs on its own CPython 3.14 while Iris keeps 3.11


def _with_hermes_python(root, cache, pin, version="3.14.8"):
    payload = b"synthetic hermes cpython"
    (cache / "hermes-python.tar").write_bytes(payload)
    pin["hermes_python"] = dict(pin["python"], version=version, filename="hermes-python.tar",
                                sha256=digest(payload))
    lock = root / f"scripts/locks/hermes-windows-cp{version.replace('.', '')[:3]}.txt"
    lock.write_bytes(b"synthetic hermes lock for its own cpython")
    pin["locks"]["hermes"]["sha256"] = digest(lock.read_bytes())
    return lock


def test_each_runtime_gets_its_own_cpython_search_path_lock_and_notice(build_inputs, tmp_path, monkeypatch):
    root, cache, wheel, uv, pin = build_inputs
    (root / "scripts/verify_runtime_dependencies.py").write_bytes(b"synthetic dependency checker")
    lock = _with_hermes_python(root, cache, pin)
    record = {"component": "hermes-runtime", "package": "native-example", "version": "1",
              "license": "MIT", "files": ["licenses/native/x/NOTICE"], "source": "https://example.test/x"}
    pin["native_licenses"] = {"pending": [], "records": [record]}
    extracted = {}

    def python_payload(archive, destination, spec):
        extracted[destination.name] = (archive.name, spec["version"])
        destination.mkdir(parents=True)
        (destination / "LICENSE.txt").write_bytes(b"preserved CPython notice")

    def audio_payload(_archive, stage, _spec):
        notices = stage / "licenses/audio"
        notices.mkdir(parents=True)
        (notices / "components.json").write_text(json.dumps({"licenses": []}), encoding="utf8")

    installed = []
    monkeypatch.setattr(builder, "extract_python", python_payload)
    monkeypatch.setattr(builder, "extract_audio", audio_payload)
    monkeypatch.setattr(builder, "extract_hermes_source", Mock())
    monkeypatch.setattr(builder, "install_locked", lambda _uv, python, effective, *_a, **_k: installed.append(
        (python.parent.name, effective.read_bytes())))
    monkeypatch.setattr(builder.subprocess, "run", Mock())
    monkeypatch.setattr(builder, "normalize_runtime_layout", Mock())
    monkeypatch.setattr(builder, "prepare_python_license_fallbacks", Mock(return_value={}))
    monkeypatch.setattr(builder, "inventory_python_licenses", Mock(return_value=[]))
    monkeypatch.setattr(builder, "prepare_native_license_records", Mock(return_value=[record]))
    seal = Mock(return_value={"files": [], "licenses": []})
    monkeypatch.setattr(builder, "build_inventory", seal)
    monkeypatch.setattr(builder, "verify_runtime", Mock())
    output = tmp_path / "new-stage"

    builder.prepare_runtime(output, cache, wheel, uv, pin, offline=True)

    assert extracted == {"iris": ("python.tar", pin["python"]["version"]), "hermes": ("hermes-python.tar", "3.14.8")}
    search_path = builder.interpreter_path_file(hermes=True)
    assert (output / "runtimes/hermes/python314._pth").read_text("utf8") == search_path
    assert not (output / "runtimes/hermes/python311._pth").exists()
    assert (output / "runtimes/iris/python311._pth").read_text("utf8") == builder.interpreter_path_file(hermes=False)
    assert (output / "locks/hermes.txt").read_bytes() == lock.read_bytes()
    assert dict(installed)["hermes"] == lock.read_bytes()
    cpython = {(item["component"], item["version"]) for item in seal.call_args.args[2] if item["package"] == "CPython"}
    assert cpython == {("iris-runtime", pin["python"]["version"]), ("hermes-runtime", "3.14.8")}


def test_hermes_cpython_is_validated_like_the_shared_one(build_inputs, tmp_path):
    root, cache, wheel, uv, pin = build_inputs
    _with_hermes_python(root, cache, pin)
    for key, bad in (("archive_prefix", "python"), ("filename", "../foreign.tar"), ("sha256", "0" * 63),
                     ("url", "http://example.test/python.tar")):
        broken = copy.deepcopy(pin)
        broken["hermes_python"][key] = bad
        output = tmp_path / f"new-stage-{key}"
        with pytest.raises(ValueError):
            builder.prepare_runtime(output, cache, wheel, uv, broken, offline=True)
        assert not output.exists()


def test_a_hermes_cpython_without_its_own_lock_fails_before_output(build_inputs, tmp_path):
    root, cache, wheel, uv, pin = build_inputs
    _with_hermes_python(root, cache, pin).unlink()
    output = tmp_path / "new-stage"
    with pytest.raises(OSError):
        builder.prepare_runtime(output, cache, wheel, uv, pin, offline=True)
    assert not output.exists()



# ── A08.4b: fetch-use is left out of the Hermes runtime (no published license text)

FETCH_USE_LOCK = LOCK.replace(
    "babel==", "fetch-use==0.4.0 \\\n    --hash=sha256:" + "6" * 64 + "\nbabel==")


def exclusion(**changes):
    return {"package": "fetch-use", "version": "0.4.0", "lock": "hermes",
            "reason": "No license text published; browser-harness falls back to urllib.", **changes}


def test_a_reviewed_exclusion_is_accepted():
    assert builder.local_exclusions({}) == []
    assert builder.local_exclusions({"local_exclusions": [exclusion()]}) == [exclusion()]


@pytest.mark.parametrize("changes", [
    {"package": "numpy"}, {"lock": "iris"}, {"version": "latest"}, {"version": "99.0.0"},
    {"version": "0.4.1"}, {"reason": " "}, {"extra": True},
])
def test_unknown_or_unreviewed_exclusions_are_refused(changes):
    with pytest.raises(ValueError, match="Invalid local exclusion"):
        builder.local_exclusions({"local_exclusions": [exclusion(**changes)]})


def test_a_package_cannot_be_both_replaced_and_excluded():
    pin = {"local_wheels": [overlay(package="fetch-use")], "local_exclusions": [exclusion()]}
    with pytest.raises(ValueError, match="Invalid local exclusion"):
        builder.local_exclusions(pin)


def test_effective_lock_drops_an_excluded_requirement_with_its_hashes():
    result = builder.effective_lock(FETCH_USE_LOCK, [], builder.target_environment({"version": "3.14.8"}),
                                    [exclusion()])
    assert result == LOCK
    both = builder.effective_lock(FETCH_USE_LOCK, [overlay()], None, [exclusion()])
    assert "fetch-use==" not in both and "av==" not in both and "babel==" in both


@pytest.mark.parametrize("lock", [LOCK, FETCH_USE_LOCK.replace("fetch-use==0.4.0", "fetch-use==0.3.3")])
def test_an_exclusion_must_match_exactly_one_locked_version(lock):
    with pytest.raises(ValueError):
        builder.effective_lock(lock, [], None, [exclusion()])


def test_the_dependency_check_is_told_about_the_exclusion(build_inputs, tmp_path, monkeypatch):
    root, cache, wheel, uv, pin = build_inputs
    (root / "scripts/verify_runtime_dependencies.py").write_bytes(b"synthetic dependency checker")
    lock = root / "scripts/locks" / f"hermes-windows-cp{builder.python_tag(builder.runtime_python(pin, 'hermes'))}.txt"
    lock.write_text(FETCH_USE_LOCK, encoding="utf8")
    pin["locks"]["hermes"]["sha256"] = digest(lock.read_bytes())
    pin["local_exclusions"] = [exclusion()]
    pin["native_licenses"] = {"pending": ["stop after the dependency check"], "records": []}

    def python_payload(_archive, destination, _spec):
        destination.mkdir(parents=True)
        (destination / "LICENSE.txt").write_bytes(b"preserved CPython notice")

    def audio_payload(_archive, stage, _spec):
        notices = stage / "licenses/audio"
        notices.mkdir(parents=True)
        (notices / "components.json").write_text(json.dumps({"licenses": []}), encoding="utf8")

    installed, run = {}, Mock()
    monkeypatch.setattr(builder, "extract_python", python_payload)
    monkeypatch.setattr(builder, "extract_audio", audio_payload)
    monkeypatch.setattr(builder, "extract_hermes_source", Mock())
    monkeypatch.setattr(builder, "install_locked", lambda _uv, python, effective, *_a, **_k: installed.update(
        {python.parent.name: effective.read_text("utf8")}))
    monkeypatch.setattr(builder.subprocess, "run", run)
    monkeypatch.setattr(builder, "normalize_runtime_layout", Mock())
    monkeypatch.setattr(builder, "prepare_python_license_fallbacks", Mock(return_value={}))
    monkeypatch.setattr(builder, "inventory_python_licenses", Mock(return_value=[]))
    monkeypatch.setattr(builder, "prepare_native_license_records", Mock(return_value=[]))
    with pytest.raises(ValueError, match="stop after the dependency check"):
        builder.prepare_runtime(tmp_path / "new-stage", cache, wheel, uv, pin, offline=True)
    assert "fetch-use" not in installed["hermes"] and "attrs==" in installed["hermes"]
    checks = [call.args[0] for call in run.call_args_list if str(call.args[0][-1]).endswith("hermes.txt")
              or "--excluded" in call.args[0]]
    hermes_check = next(command for command in checks if "runtimes" in str(command[0]) and "hermes" in str(command[0]))
    assert hermes_check[-2:] == ["--excluded", "fetch-use==0.4.0"]
    assert (tmp_path / "new-stage/locks/hermes.txt").read_text("utf8") == FETCH_USE_LOCK
