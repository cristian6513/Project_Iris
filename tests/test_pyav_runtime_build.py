"""PyAV SDK profile and input boundaries; never execute a compiler or a runtime."""

from __future__ import annotations

import copy
import io
import os
import tarfile
from pathlib import Path

import pytest

from scripts import build_audio_decoder as audio
from scripts import build_pyav_runtime as sdk
from scripts.distribution_assets import DistributionError
from scripts.runtime_inventory import read_json


@pytest.fixture
def sdk_inputs(tmp_path, monkeypatch):
    source, archive = tmp_path / "source.tar.gz", tmp_path / "compiler.7z.exe"
    source.write_bytes(b"synthetic source")
    archive.write_bytes(b"synthetic compiler archive")
    compiler = tmp_path / "Compiler con espacios ñ"
    (compiler / "bin").mkdir(parents=True)
    for name in ("gcc.exe", "make.exe", "objdump.exe", "sh.exe"):
        (compiler / "bin" / name).write_bytes(b"not executable")
    notices = tmp_path / "notices"
    notices.mkdir()
    base = copy.deepcopy(read_json(audio.PIN_PATH))
    base["source"]["sha256"] = audio.file_hash(source)
    base["toolchain"].update(sha256=audio.file_hash(archive), tree_sha256=audio.tree_hash(compiler))
    for item in base["notices"]:
        notice = notices / item["filename"]
        notice.write_bytes(b"synthetic notice")
        item["sha256"] = audio.file_hash(notice)
    pin = copy.deepcopy(read_json(sdk.PIN_PATH))
    for field in ("source", "toolchain", "notices"):
        pin[field] = base[field]
    original = sdk.read_json
    monkeypatch.setattr(sdk, "read_json", lambda p: base if p == audio.PIN_PATH else original(p))
    return source, compiler, archive, notices, tmp_path / "fresh-sdk", pin


def test_sdk_preflight_verifies_all_inputs_before_creating_output(sdk_inputs):
    assert sdk.preflight(*sdk_inputs) == (sdk_inputs[0], sdk_inputs[1], sdk_inputs[4])
    assert not sdk_inputs[4].exists()


@pytest.mark.parametrize("target", ["source", "archive", "compiler", "notice"])
def test_sdk_rejects_changed_inputs_before_output(sdk_inputs, target):
    source, compiler, archive, notices, output, pin = sdk_inputs
    path = {
        "source": source,
        "archive": archive,
        "compiler": compiler / "bin/gcc.exe",
        "notice": notices / pin["notices"][0]["filename"],
    }[target]
    path.write_bytes(b"changed input")
    with pytest.raises(DistributionError, match="hash|tree"):
        sdk.preflight(*sdk_inputs)
    assert not output.exists()


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema", True),
        ("schema", 2),
        ("component", "unknown"),
        ("platform", "linux"),
        ("arch", "arm64"),
        ("profile", "gpl"),
        ("configure", ["--enable-gpl"]),
        ("configure", ["--enable-libx264"]),
        ("configure", ["--disable-everything"]),
    ],
)
def test_sdk_profile_cannot_enable_unknown_or_restricted_builds(sdk_inputs, field, value):
    pin = copy.deepcopy(sdk_inputs[-1])
    pin[field] = value
    with pytest.raises(DistributionError, match="profile"):
        sdk.validate_pin(pin)


def test_sdk_rejects_unreviewed_source_compiler_and_fields(sdk_inputs):
    for field in ("source", "toolchain", "notices"):
        pin = copy.deepcopy(sdk_inputs[-1])
        pin[field] = []
        with pytest.raises(DistributionError, match="reviewed"):
            sdk.validate_pin(pin)
    pin = copy.deepcopy(sdk_inputs[-1])
    pin["method"] = "unknown"
    with pytest.raises(DistributionError, match="profile"):
        sdk.validate_pin(pin)


def test_changed_base_pin_requires_review(sdk_inputs):
    pin = copy.deepcopy(sdk_inputs[-1])
    pin["base_sources_sha256"] = "0" * 64
    with pytest.raises(DistributionError, match="reviewed again"):
        sdk.validate_pin(pin)


def test_output_cannot_overlap_inputs_or_replace_existing_artifact(sdk_inputs):
    source, compiler, archive, notices, output, pin = sdk_inputs
    output.mkdir()
    marker = output / "preserve.txt"
    marker.write_bytes(b"preserve")
    with pytest.raises(DistributionError, match="new directory"):
        sdk.preflight(*sdk_inputs)
    with pytest.raises(DistributionError, match="separate"):
        sdk.preflight(source, compiler, archive, notices, compiler / "new-sdk", pin)
    assert marker.read_bytes() == b"preserve"


def test_sdk_rejects_hardlinked_compiler_before_read_or_execution(sdk_inputs, tmp_path):
    compiler, output = sdk_inputs[1], sdk_inputs[4]
    outside = tmp_path / "outside.exe"
    (compiler / "bin/gcc.exe").rename(outside)
    os.link(outside, compiler / "bin/gcc.exe")
    with pytest.raises(DistributionError, match="Hardlinked"):
        sdk.preflight(*sdk_inputs)
    assert not output.exists()
    assert outside.read_bytes() == b"not executable"


@pytest.mark.parametrize(
    "member",
    [
        "/absolute",
        "C:/outside",
        "../outside",
        "root/../outside",
        r"root\outside",
    ],
)
def test_sdk_source_extractor_rejects_absolute_traversal_and_windows_separators(tmp_path, member):
    source, output = tmp_path / "source.tar.gz", tmp_path / "new-sdk"
    with tarfile.open(source, "w:gz") as archive:
        item = tarfile.TarInfo(member)
        item.size = 7
        archive.addfile(item, io.BytesIO(b"fixture"))
    with pytest.raises(DistributionError):
        sdk.audio.extract_source(source, output)
    assert not output.exists()


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE])
def test_sdk_source_extractor_rejects_symlink_and_hardlink_members(tmp_path, kind):
    source, output = tmp_path / "source.tar.gz", tmp_path / "new-sdk"
    with tarfile.open(source, "w:gz") as archive:
        item = tarfile.TarInfo("root/linked")
        item.type, item.linkname = kind, "../outside"
        archive.addfile(item)
    with pytest.raises(DistributionError, match="Linked"):
        sdk.audio.extract_source(source, output)
    assert not output.exists()


def test_sdk_preserves_native_catalog_and_all_seven_shared_libraries():
    assert "--disable-everything" not in sdk.FLAGS
    assert not any(flag.startswith("--enable-lib") for flag in sdk.FLAGS)
    assert {
        "--disable-gpl",
        "--disable-version3",
        "--disable-nonfree",
        "--disable-autodetect",
        "--disable-network",
        "--disable-hwaccels",
    } <= set(sdk.FLAGS)
    # External and inline x86 asm both disabled: the inline-only MMXEXT swscale path
    # corrupted yuv420p chroma when the external asm was absent.
    assert {"--disable-x86asm", "--disable-inline-asm"} <= set(sdk.FLAGS)
    assert len(sdk.LIBRARIES) == 7
    for library in sdk.LIBRARIES:
        assert "--enable-" + library.split("-")[0] in sdk.FLAGS


def test_sdk_cli_unknown_method_never_runs_builder(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("unknown CLI method executed the builder")

    monkeypatch.setattr(sdk, "build_sdk", unexpected)
    with pytest.raises(SystemExit):
        sdk.main(["--method", "unknown"])


def test_sdk_notices_include_rebuild_sources_and_dynamic_link_instructions(tmp_path):
    package = tmp_path / "package"
    licenses = package / "licenses/audio"
    (licenses / "sources").mkdir(parents=True)
    (package / "bin").mkdir()
    (licenses / "components.json").write_text('{"schema":1,"licenses":[{"files":[]}]}')
    (licenses / "audio-build.json").write_text('{"schema":1,"files":[]}')
    sdk.finish_sdk_provenance(package, read_json(sdk.PIN_PATH))
    assert (licenses / "sources/build_pyav_runtime.py").read_bytes() == Path(
        sdk.__file__
    ).read_bytes()
    assert (
        licenses / "sources/audio_build_sources.json"
    ).read_bytes() == audio.PIN_PATH.read_bytes()
    instructions = (licenses / "sources/BUILD.md").read_text()
    assert "PyAV dynamically links" in instructions and "seven native shared" in instructions
    receipt = read_json(licenses / "audio-build.json")
    assert receipt["component"] == "iris-pyav-ffmpeg-sdk"
    assert not any(item["path"].endswith("audio-build.json") for item in receipt["files"])
    assert any(item["path"].endswith("BUILD.md") for item in receipt["files"])
