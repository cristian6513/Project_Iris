"""Messaging audio CLI profile and input boundaries; never execute a compiler or a runtime."""

from __future__ import annotations

import copy

import pytest

from scripts import build_audio_decoder as audio
from scripts import build_messaging_audio as messaging
from scripts.distribution_assets import DistributionError
from scripts.runtime_inventory import read_json


@pytest.fixture
def messaging_inputs(tmp_path, monkeypatch):
    source, archive = tmp_path / "source.tar.gz", tmp_path / "compiler.7z.exe"
    opus, lame = tmp_path / "opus-1.6.1.tar.gz", tmp_path / "lame-4.0.tar.gz"
    for path in (source, archive, opus, lame):
        path.write_bytes(b"synthetic " + path.name.encode())
    compiler = tmp_path / "Compiler con espacios ñ"
    (compiler / "bin").mkdir(parents=True)
    for name in ("gcc.exe", "make.exe", "objdump.exe", "sh.exe", "pkg-config.exe"):
        (compiler / "bin" / name).write_bytes(b"not executable")
    notices = tmp_path / "notices"
    notices.mkdir()
    base = copy.deepcopy(read_json(audio.PIN_PATH))
    base["source"]["sha256"] = audio.file_hash(source)
    base["toolchain"].update(
        sha256=audio.file_hash(archive), tree_sha256=audio.tree_hash(compiler)
    )
    for item in base["notices"]:
        notice = notices / item["filename"]
        notice.write_bytes(b"synthetic notice")
        item["sha256"] = audio.file_hash(notice)
    codecs = copy.deepcopy(messaging.CODECS)
    codecs["opus"]["sha256"] = audio.file_hash(opus)
    codecs["lame"]["sha256"] = audio.file_hash(lame)
    monkeypatch.setattr(messaging, "CODECS", codecs)
    original = messaging.read_json
    monkeypatch.setattr(
        messaging, "read_json", lambda p: base if p == audio.PIN_PATH else original(p)
    )
    pin = copy.deepcopy(read_json(messaging.PIN_PATH))
    pin["codecs"] = codecs
    output = tmp_path / "fresh-messaging"
    return source, opus, lame, compiler, archive, notices, output, pin


def test_pin_mirrors_reviewed_constants():
    pin = read_json(messaging.PIN_PATH)
    assert pin["codecs"] == messaging.CODECS and pin["configure"] == messaging.FFMPEG_FLAGS
    assert pin["base_sources_sha256"] == audio.file_hash(audio.PIN_PATH)
    messaging.validate_pin(pin, read_json(audio.PIN_PATH), audio.file_hash(audio.PIN_PATH))


def test_preflight_verifies_codec_and_base_inputs_before_creating_output(messaging_inputs):
    result = messaging.preflight(*messaging_inputs)
    assert result[1:3] == (messaging_inputs[1], messaging_inputs[2])
    assert not messaging_inputs[6].exists()


@pytest.mark.parametrize("target", ["opus", "lame", "source", "archive", "compiler"])
def test_changed_inputs_are_rejected_before_output(messaging_inputs, target):
    source, opus, lame, compiler, archive, notices, output, pin = messaging_inputs
    path = {"opus": opus, "lame": lame, "source": source, "archive": archive,
            "compiler": compiler / "bin/gcc.exe"}[target]
    path.write_bytes(b"changed input")
    with pytest.raises(DistributionError, match="hash|tree"):
        messaging.preflight(*messaging_inputs)
    assert not output.exists()


def test_missing_private_pkg_config_is_rejected(messaging_inputs):
    compiler = messaging_inputs[3]
    (compiler / "bin/pkg-config.exe").unlink()
    with pytest.raises(DistributionError):
        messaging.preflight(*messaging_inputs)


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema", True), ("schema", 2), ("platform", "linux"), ("arch", "arm64"),
        ("configure", messaging.FFMPEG_FLAGS + ["--enable-gpl"]),
        ("configure", messaging.FFMPEG_FLAGS + ["--enable-libx264"]),
        ("method", "unknown"),
    ],
)
def test_contract_cannot_enable_unknown_or_restricted_builds(field, value):
    pin = copy.deepcopy(read_json(messaging.PIN_PATH))
    pin[field] = value
    with pytest.raises(DistributionError, match="contract"):
        messaging.validate_pin(pin, read_json(audio.PIN_PATH), audio.file_hash(audio.PIN_PATH))


@pytest.mark.parametrize(
    "codec,field,value",
    [
        ("opus", "url", "https://example.invalid/opus-1.6.1.tar.gz"),
        ("opus", "sha256", "0" * 64),
        ("opus", "configure", messaging.OPUS_FLAGS + ["--enable-dred"]),
        ("lame", "license", "GPL-2.0-only"),
        ("lame", "configure", messaging.LAME_FLAGS + ["--enable-frontend"]),
        ("lame", "version", "3.100"),
    ],
)
def test_codec_sources_are_reviewed_constants(codec, field, value):
    pin = copy.deepcopy(read_json(messaging.PIN_PATH))
    pin["codecs"][codec][field] = value
    with pytest.raises(DistributionError, match="Opus/LAME"):
        messaging.validate_pin(pin, read_json(audio.PIN_PATH), audio.file_hash(audio.PIN_PATH))


def test_changed_base_pin_requires_review():
    pin = copy.deepcopy(read_json(messaging.PIN_PATH))
    pin["base_sources_sha256"] = "0" * 64
    with pytest.raises(DistributionError, match="reviewed again"):
        messaging.validate_pin(pin, read_json(audio.PIN_PATH), audio.file_hash(audio.PIN_PATH))


def test_messaging_profile_extends_minimal_lgpl_profile_only_with_reviewed_parts():
    flags = messaging.FFMPEG_FLAGS
    changed = tuple(messaging._FLAG_CHANGES)
    for flag in audio.FLAGS:
        if not flag.startswith(changed):
            assert flag in flags
    assert {"--disable-gpl", "--disable-version3", "--disable-nonfree", "--disable-network",
            "--disable-autodetect", "--enable-shared", "--disable-static"} <= set(flags)
    assert [flag for flag in flags if flag.startswith("--enable-lib")] == [
        "--enable-libopus", "--enable-libmp3lame"]
    by_prefix = {
        flag.split("=", 1)[0]: flag.split("=", 1)[1].split(",") for flag in flags if "=" in flag
    }
    assert {"concat", "wav", "ogg", "mov"} <= set(by_prefix["--enable-demuxer"])
    muxers = set(by_prefix["--enable-muxer"])
    assert {"ogg", "opus", "mp3", "flac", "adts", "ipod", "pcm_s16le"} <= muxers
    assert {"libopus", "libmp3lame", "aac", "flac"} <= set(by_prefix["--enable-encoder"])
    assert by_prefix["--enable-protocol"] == ["file", "pipe"]
    assert "--enable-bsf=aac_adtstoasc" in flags


def test_changed_minimal_profile_fails_closed():
    with pytest.raises(DistributionError, match="review"):
        messaging._messaging_flags(
            [flag for flag in audio.FLAGS if not flag.startswith("--enable-muxer=")]
        )


def test_codec_flags_are_static_portable_and_record_no_producer_path():
    for item in messaging.CODECS.values():
        flags = item["configure"]
        assert {"--enable-static", "--disable-shared", "--prefix=/codecs",
                "--build=x86_64-w64-mingw32", "--host=x86_64-w64-mingw32"} <= set(flags)
        assert not any(":" in flag or "\\" in flag or " " in flag for flag in flags)
    assert {"--disable-dred", "--disable-deep-plc", "--disable-osce"} <= set(messaging.OPUS_FLAGS)
    assert {"--disable-frontend", "--disable-decoder"} <= set(messaging.LAME_FLAGS)


def test_codec_build_names_libtool_and_copies_reviewed_install_set(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(messaging.audio, "extract_source", lambda archive, tree: tree.mkdir())
    def fake_run(command, cwd, env, log):
        calls.append((command, cwd, env, log))
        if "-j3" in command:
            (cwd / "libmp3lame/.libs").mkdir(parents=True)
            (cwd / "include").mkdir()
            (cwd / "libmp3lame/.libs/libmp3lame.a").write_bytes(b"built lame")
            (cwd / "include/lame.h").write_bytes(b"built header")

    monkeypatch.setattr(messaging.audio, "run", fake_run)
    output, compiler = tmp_path / "output", tmp_path / "compiler"
    output.mkdir()
    tree = messaging._build_codec("lame", tmp_path / "lame.tar.gz", output, compiler,
                                  {"PATH": "private"}, 3)
    assert tree == output / "lame-source"
    configure, make = (call[0] for call in calls)
    assert configure[1:] == ["./configure", *messaging.LAME_FLAGS]
    assert make[1:] == ["-j3", "V=1", "MAKE=make", "LIBTOOL=sh $(top_builddir)/libtool"]
    assert (output / "codecs/lib/libmp3lame.a").read_bytes() == b"built lame"
    assert (output / "codecs/include/lame/lame.h").read_bytes() == b"built header"
    assert all(call[1] == tree for call in calls)
    assert all(call[2]["CFLAGS"] == "-O2 -ffile-prefix-map=.=lame-source" for call in calls)
    assert all(call[2]["PATH"] == "private" and call[2]["PATH_SEPARATOR"] == ";"
               and call[2]["ac_executable_extensions"] == ".exe" for call in calls)
    for name, value in messaging.AUTOCONF_TOOLS.items():
        assert all(call[2][name] == value for call in calls)
        assert ":" not in value and "\\" not in value


def test_cli_unknown_method_never_runs_builder(monkeypatch):
    monkeypatch.setattr(messaging, "build_messaging_audio",
                        lambda *a, **k: pytest.fail("unknown CLI method executed the builder"))
    with pytest.raises(SystemExit):
        messaging.main(["--method", "unknown"])
