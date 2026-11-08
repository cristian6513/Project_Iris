"""Pinned native decoder build boundaries; no compiler or decoder is launched."""

from __future__ import annotations

import copy
import io
import os
import tarfile
import zipfile
from pathlib import Path

import pytest

from scripts.build_audio_decoder import (
    FLAGS,
    PIN_PATH,
    build_environment,
    extract_source,
    file_hash,
    main,
    package_decoder,
    pe_imports,
    preflight,
    tree_hash,
    validate_license_output,
    validate_pin,
)
from scripts.distribution_assets import DistributionError
from scripts.runtime_inventory import read_json


@pytest.fixture
def inputs(tmp_path):
    source, archive, notices = (
        tmp_path / "source.tar.gz",
        tmp_path / "compiler.7z.exe",
        tmp_path / "notices",
    )
    source.write_bytes(b"synthetic source archive")
    archive.write_bytes(b"synthetic compiler archive")
    notices.mkdir()
    compiler = tmp_path / "Compiler con espacios ñ"
    (compiler / "bin").mkdir(parents=True)
    for name in ("gcc.exe", "make.exe", "objdump.exe", "sh.exe"):
        (compiler / "bin" / name).write_bytes(b"not executed")
    pin = copy.deepcopy(read_json(PIN_PATH))
    pin["source"]["sha256"] = file_hash(source)
    pin["toolchain"].update(sha256=file_hash(archive), tree_sha256=tree_hash(compiler))
    for item in pin["notices"]:
        path = notices / item["filename"]
        path.write_bytes(b"synthetic notice")
        item["sha256"] = file_hash(path)
    return source, compiler, archive, notices, tmp_path / "fresh-output", pin


def test_preflight_accepts_verified_inputs_and_never_creates_output(inputs):
    source, compiler, archive, notices, output, pin = inputs
    assert preflight(*inputs) == (source, compiler, output)
    assert not output.exists()
    assert file_hash(source) == pin["source"]["sha256"]


@pytest.mark.parametrize("target", ["source", "archive", "compiler", "notice"])
def test_modified_inputs_fail_before_output_or_any_execution(inputs, target):
    source, compiler, archive, notices, output, pin = inputs
    path = {
        "source": source,
        "archive": archive,
        "compiler": compiler / "bin/gcc.exe",
        "notice": notices / pin["notices"][0]["filename"],
    }[target]
    path.write_bytes(b"modified fixture")
    with pytest.raises(DistributionError, match="(?:hash|tree)"):
        preflight(*inputs)
    assert not output.exists()


def test_output_must_be_new_and_compiler_keeps_its_contents(inputs):
    source, compiler, archive, notices, output, pin = inputs
    output.mkdir()
    (output / "existing").write_bytes(b"keep me")
    with pytest.raises(DistributionError, match="new directory"):
        preflight(*inputs)
    assert (output / "existing").read_bytes() == b"keep me"
    with pytest.raises(DistributionError, match="separate"):
        preflight(source, compiler, archive, notices, compiler / "new", pin)
    assert (compiler / "bin/gcc.exe").read_bytes() == b"not executed"


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema", True),
        ("schema", 2),
        ("platform", "linux"),
        ("arch", "ARM64"),
        ("configure", ["--enable-gpl"]),
        ("configure", ["--enable-libmp3lame"]),
    ],
)
def test_unknown_methods_platforms_or_external_libraries_cannot_change_contract(
    inputs, field, value
):
    pin = copy.deepcopy(inputs[-1])
    pin[field] = value
    with pytest.raises(DistributionError):
        validate_pin(pin)


def source_tar(path, member, kind=tarfile.REGTYPE):
    with tarfile.open(path, "w:gz") as archive:
        item = tarfile.TarInfo(member)
        item.type = kind
        item.size = 7 if kind == tarfile.REGTYPE else 0
        item.linkname = "../outside"
        archive.addfile(item, io.BytesIO(b"fixture") if item.size else None)


@pytest.mark.parametrize(
    "member",
    [
        "../outside",
        "/absolute",
        "C:/outside",
        r"root\payload",
        "root/../outside",
        "root/file:stream",
    ],
)
def test_source_extraction_rejects_absolute_traversal_windows_paths_before_writing(
    tmp_path, member
):
    archive, output = tmp_path / "source.tar.gz", tmp_path / "output"
    source_tar(archive, member)
    with pytest.raises(DistributionError):
        extract_source(archive, output)
    assert not output.exists()


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE])
def test_source_extraction_rejects_linked_and_nonregular_members(tmp_path, kind):
    archive, output = tmp_path / "source.tar.gz", tmp_path / "output"
    source_tar(archive, "root/linked", kind)
    with pytest.raises(DistributionError, match="Linked"):
        extract_source(archive, output)
    assert not output.exists()


def test_compiler_hardlinks_are_rejected_and_external_bytes_preserved(inputs, tmp_path):
    source, compiler, archive, notices, output, pin = inputs
    target, outside = compiler / "bin/gcc.exe", tmp_path / "outside.exe"
    target.rename(outside)
    os.link(outside, target)
    with pytest.raises(DistributionError, match="Hardlinked"):
        preflight(*inputs)
    assert outside.read_bytes() == b"not executed"
    assert not output.exists()


def test_compiler_junction_is_rejected(inputs, tmp_path):
    source, compiler, archive, notices, output, pin = inputs
    outside = tmp_path / "outside"
    compiler.rename(outside)
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(outside), str(compiler))
    else:
        compiler.symlink_to(outside, target_is_directory=True)
    try:
        with pytest.raises(DistributionError, match="Linked"):
            preflight(*inputs)
        assert not output.exists()
    finally:
        compiler.rmdir() if os.name == "nt" else compiler.unlink()


def test_build_environment_cannot_discover_user_compilers_or_credentials(inputs, monkeypatch):
    _, compiler, _, _, output, _ = inputs
    monkeypatch.setenv("PATH", "hostile-global")
    monkeypatch.setenv("HOME", "personal-home")
    monkeypatch.setenv("CPATH", "hostile-headers")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-secret")
    monkeypatch.setenv("SYSTEMROOT", "C:/Windows")
    monkeypatch.setenv("COMSPEC", "hostile-shell")
    env = build_environment(compiler, output, 123)
    assert env["PATH"] == str(compiler / "bin")
    assert env["HOME"] == str(output / "home")
    assert env["SOURCE_DATE_EPOCH"] == "123"
    assert env["TMPDIR"] == "../temp"
    assert env["COMSPEC"] == str(Path("C:/Windows") / "System32/cmd.exe")
    assert not {"CPATH", "OPENAI_API_KEY", "PYTHONPATH", "VIRTUAL_ENV"} & env.keys()


def test_native_flags_disable_all_external_libraries_and_network():
    assert {
        "--disable-autodetect",
        "--disable-everything",
        "--disable-network",
        "--disable-gpl",
        "--disable-version3",
        "--disable-nonfree",
        "--enable-shared",
        "--disable-static",
    } <= set(FLAGS)
    assert not any(flag.startswith("--enable-lib") for flag in FLAGS)


def test_invalid_pe_never_qualifies_as_a_runtime(tmp_path):
    binary = tmp_path / "wrong.exe"
    binary.write_bytes(b"MZinvalid")
    with pytest.raises(DistributionError, match="PE"):
        pe_imports(binary)


def test_cli_rejects_unknown_operations_without_execution():
    with pytest.raises(SystemExit):
        main(["--method", "unknown"])


def test_upstream_license_line_wrapping_does_not_hide_lgpl():
    validate_license_output(
        b"GNU Lesser General Public\r\nLicense; either\r\nversion 2.1 or any later version",
        b"--disable-gpl --disable-version3 --disable-nonfree",
    )


@pytest.mark.parametrize("flags", [b"--enable-gpl", b"--enable-version3", b"--enable-nonfree"])
def test_enabled_restricted_features_cannot_pass_license_check(flags):
    with pytest.raises(DistributionError, match="LGPL"):
        validate_license_output(b"GNU Lesser General Public License version 2.1", flags)


def fixture_package(tmp_path):
    package = tmp_path / "package"
    for name in ("bin/ffmpeg.exe", "bin/ffprobe.exe", "licenses/audio/components.json"):
        path = package / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic redistributable")
    (package / "developer-note.txt").write_bytes(b"excluded build input")
    (package / "bin/linker-import.lib").write_bytes(b"excluded import library")
    return package


def test_redistribution_zip_is_stable_and_contains_only_public_package(tmp_path):
    package = fixture_package(tmp_path)
    first, second = tmp_path / "first.zip", tmp_path / "second.zip"
    assert package_decoder(package, first, 1790713770) == package_decoder(
        package, second, 1790713770
    )
    with zipfile.ZipFile(first) as archive:
        assert set(archive.namelist()) == {
            "bin/ffmpeg.exe",
            "bin/ffprobe.exe",
            "licenses/audio/components.json",
        }


def test_zip_missing_required_files_never_creates_archive(tmp_path):
    package = fixture_package(tmp_path)
    (package / "bin/ffprobe.exe").unlink()
    archive = tmp_path / "new.zip"
    with pytest.raises(DistributionError, match="Missing"):
        package_decoder(package, archive, 1790713770)
    assert not archive.exists()


def test_zip_preserves_existing_output(tmp_path):
    package = fixture_package(tmp_path)
    archive = tmp_path / "existing.zip"
    archive.write_bytes(b"preserve existing artifact")
    with pytest.raises(DistributionError, match="new file"):
        package_decoder(package, archive, 1790713770)
    assert archive.read_bytes() == b"preserve existing artifact"


def test_zip_rejects_hardlinked_binary_before_reading_or_creating_output(tmp_path):
    package = fixture_package(tmp_path)
    outside = tmp_path / "outside.exe"
    (package / "bin/ffmpeg.exe").rename(outside)
    os.link(outside, package / "bin/ffmpeg.exe")
    archive = tmp_path / "new.zip"
    with pytest.raises(DistributionError, match="Hardlinked"):
        package_decoder(package, archive, 1790713770)
    assert not archive.exists()
    assert outside.read_bytes() == b"synthetic redistributable"
