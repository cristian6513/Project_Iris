"""Private audio contracts; no microphone, model download or speaker access."""

from __future__ import annotations

import io
import os
import subprocess
import sys
import wave
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import numpy as np
import pytest

from core import audio_decoder, tts
from core.runtime_descriptor import RuntimeDescriptorError, load_runtime_descriptor
from tests.runtime_descriptor_fixtures import write_runtime_fixture


@pytest.fixture
def packaged(tmp_path):
    descriptor, _ = write_runtime_fixture(tmp_path / "Iris con espacios ñ")
    runtime = load_runtime_descriptor(descriptor)
    directory = runtime.base / "tools/audio/bin"
    directory.mkdir(parents=True, exist_ok=True)
    # The shared descriptor fixture includes a codec for other packaged
    # consumers; this fixture owns the exact set for incomplete-bin regressions.
    (directory / "avcodec.dll").unlink(missing_ok=True)
    for name in ("ffmpeg.exe", "ffprobe.exe", "avcodec-62.dll"):
        (directory / name).write_bytes(b"fixture, not executable")
    return runtime, directory


def test_private_audio_is_fixed_and_independent_of_environment(packaged, monkeypatch, tmp_path):
    runtime, directory = packaged
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", str(tmp_path / "external"))
    monkeypatch.setenv("FFMPEG_BINARY", str(tmp_path / "foreign.exe"))
    monkeypatch.setenv("IMAGEIO_FFMPEG_EXE", str(tmp_path / "foreign.exe"))
    assert audio_decoder.private_audio_bin(runtime) == directory
    assert audio_decoder.private_audio_tool("ffmpeg", runtime) == directory / "ffmpeg.exe"


@pytest.mark.parametrize("name", [
    "unknown", "/absolute", "../ffmpeg", "C:/ffmpeg", r"C:\ffmpeg",
    r"..\ffmpeg", r"\\host\share", "ffmpeg.exe", "FFMPEG", "", None, [],
])
def test_unknown_methods_and_paths_cannot_select_a_tool(packaged, name):
    runtime, _ = packaged
    with pytest.raises(RuntimeDescriptorError, match="desconocida"):
        audio_decoder.private_audio_tool(name, runtime)


def test_development_does_not_resolve_a_private_decoder(packaged):
    runtime, _ = packaged
    with pytest.raises(RuntimeDescriptorError, match="empaquetado"):
        audio_decoder.private_audio_bin(replace(runtime, mode="development"))


@pytest.mark.parametrize("missing", ["ffmpeg.exe", "ffprobe.exe", "avcodec-62.dll"])
def test_incomplete_shared_decoder_fails_closed(packaged, missing):
    runtime, directory = packaged
    (directory / missing).unlink()
    with pytest.raises(RuntimeDescriptorError):
        audio_decoder.private_audio_bin(runtime)


@pytest.mark.parametrize("name", ["ffplay.exe", "private-token.txt", "external.py"])
def test_unknown_binary_directory_members_are_rejected(packaged, name):
    runtime, directory = packaged
    (directory / name).write_bytes(b"unexpected")
    with pytest.raises(RuntimeDescriptorError):
        audio_decoder.private_audio_bin(runtime)


def test_empty_binary_and_directory_instead_of_dll_are_rejected(packaged):
    runtime, directory = packaged
    (directory / "ffmpeg.exe").write_bytes(b"")
    with pytest.raises(RuntimeDescriptorError):
        audio_decoder.private_audio_bin(runtime)
    (directory / "ffmpeg.exe").write_bytes(b"fixture")
    (directory / "avcodec-62.dll").unlink()
    (directory / "avcodec-62.dll").mkdir()
    with pytest.raises(RuntimeDescriptorError):
        audio_decoder.private_audio_bin(runtime)


def test_hardlinked_binary_cannot_select_an_external_file(packaged, tmp_path):
    runtime, directory = packaged
    target = tmp_path / "foreign.exe"
    target.write_bytes(b"not executable")
    executable = directory / "ffmpeg.exe"
    executable.unlink()
    executable.hardlink_to(target)
    with pytest.raises(RuntimeDescriptorError):
        audio_decoder.private_audio_bin(runtime)
    assert target.read_bytes() == b"not executable"


def test_symlinked_binary_is_rejected_even_inside_installation(packaged, monkeypatch):
    runtime, directory = packaged
    executable = directory / "ffmpeg.exe"
    target = directory / "avcodec-62.dll"
    executable.unlink()
    try:
        executable.symlink_to(target)
    except OSError:
        # Windows without symlink privilege still exercises the exact reparse
        # rejection. Directory junctions receive their own real Windows test.
        executable.write_bytes(b"fixture")
        original = Path.lstat

        def reparse(path):
            result = original(path)
            if path == executable:
                return type("Reparse", (), {
                    "st_mode": result.st_mode, "st_nlink": result.st_nlink,
                    "st_size": result.st_size, "st_file_attributes": 0x400,
                })()
            return result

        monkeypatch.setattr(Path, "lstat", reparse)
    with pytest.raises(RuntimeDescriptorError):
        audio_decoder.private_audio_bin(runtime)


def test_noncanonical_installation_is_rejected(packaged):
    runtime, _ = packaged
    with pytest.raises(RuntimeDescriptorError):
        audio_decoder.private_audio_bin(replace(runtime, base=runtime.base / "tools/.."))


def test_directory_symlink_or_windows_junction_cannot_redirect_decoder(packaged, tmp_path):
    runtime, directory = packaged
    outside = tmp_path / "outside decoder"
    outside.mkdir()
    for item in directory.iterdir():
        (outside / item.name).write_bytes(item.read_bytes())
        item.unlink()
    directory.rmdir()
    if os.name == "nt":
        result = subprocess.run(
            ["cmd.exe", "/D", "/C", "mklink", "/J", str(directory), str(outside)],
            capture_output=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        assert result.returncode == 0, result.stderr
    else:
        directory.symlink_to(outside, target_is_directory=True)
    try:
        with pytest.raises(RuntimeDescriptorError):
            audio_decoder.private_audio_bin(runtime)
        assert (outside / "ffmpeg.exe").read_bytes() == b"fixture, not executable"
    finally:
        directory.rmdir() if os.name == "nt" else directory.unlink()


def test_packaged_decoder_uses_only_fixed_arguments_and_private_environment(packaged, monkeypatch):
    runtime, directory = packaged
    monkeypatch.setenv("PATH", "foreign")
    monkeypatch.setenv("FFREPORT", "file=private.log:level=32")
    monkeypatch.setenv("SECRET_PROVIDER_KEY", "synthetic-test-value")
    pcm = np.array([-3000, 0, 3000], dtype="<i2")
    run = Mock(return_value=pcm.tobytes())
    monkeypatch.setattr(audio_decoder, "_bounded_run", run)
    samples, rate = audio_decoder.decode_private_audio(b"synthetic encoded audio", runtime)
    np.testing.assert_array_equal(samples, pcm)
    assert samples.dtype == np.int16 and rate == 24_000
    args, kwargs = run.call_args
    assert args[0][0] == str(directory / "ffmpeg.exe")
    assert args[0][-1] == "pipe:1"
    assert args[0][args[0].index("-i") + 1] == "pipe:0"
    assert args[0][args[0].index("-protocol_whitelist") + 1] == "pipe"
    assert args[0][args[0].index("-ac") + 1] == "1"
    assert args[0][args[0].index("-ar") + 1] == "24000"
    assert "-nostdin" in args[0]
    assert kwargs["cwd"] == directory
    assert kwargs["env"]["PATH"] == str(directory)
    assert "FFREPORT" not in kwargs["env"]
    assert "SECRET_PROVIDER_KEY" not in kwargs["env"]


@pytest.mark.parametrize("value", [b"", None, bytearray(b"x"), "audio"])
def test_invalid_input_does_not_launch_a_process(monkeypatch, value):
    launch = Mock(side_effect=AssertionError("invalid audio must not launch"))
    monkeypatch.setattr(audio_decoder.subprocess, "Popen", launch)
    with pytest.raises(RuntimeError):
        audio_decoder.decode_private_audio(value)
    launch.assert_not_called()


def test_input_limit_applies_before_resolving_the_runtime(monkeypatch):
    monkeypatch.setattr(audio_decoder, "MAX_AUDIO_INPUT_BYTES", 4)
    resolve = Mock(side_effect=AssertionError("oversized input must not resolve"))
    monkeypatch.setattr(audio_decoder, "private_audio_tool", resolve)
    with pytest.raises(RuntimeError, match="entrada"):
        audio_decoder.decode_private_audio(b"12345")
    resolve.assert_not_called()


@pytest.mark.parametrize("raw", [b"", b"x", b"abc"])
def test_ambiguous_or_empty_pcm_is_rejected(packaged, monkeypatch, raw):
    runtime, _ = packaged
    monkeypatch.setattr(audio_decoder, "_bounded_run", lambda *args, **kwargs: raw)
    with pytest.raises(RuntimeError, match="PCM"):
        audio_decoder.decode_private_audio(b"fixture", runtime)


def run_child(tmp_path, code, **kwargs):
    return audio_decoder._bounded_run(
        [str(Path(sys.executable).resolve()), "-E", "-s", "-B", "-c", code],
        b"fixture", cwd=tmp_path, env={"PATH": "", "SystemRoot": os.environ.get("SystemRoot", "")},
        **kwargs,
    )


def test_bounded_pipes_collect_only_complete_output(tmp_path):
    code = "import sys; data=sys.stdin.buffer.read(); sys.stdout.buffer.write(data)"
    assert run_child(tmp_path, code) == b"fixture"


@pytest.mark.parametrize("stream,limit", [("stdout", "output_limit"), ("stderr", "error_limit")])
def test_output_and_diagnostic_bounds_terminate_the_process(tmp_path, stream, limit):
    with pytest.raises(RuntimeError, match="límite"):
        run_child(tmp_path, f"import sys; sys.{stream}.buffer.write(b'x'*1000000)", **{limit: 1024})


def test_decoder_timeout_terminates_and_drains_process(tmp_path):
    with pytest.raises(RuntimeError, match="tiempo"):
        run_child(tmp_path, "import time; time.sleep(10)", timeout=0.05)


def test_nonzero_exit_never_returns_partial_pcm(tmp_path):
    with pytest.raises(RuntimeError, match="rechazó"):
        run_child(tmp_path, "import sys; sys.stdout.buffer.write(b'partial'); sys.exit(1)")


def test_unlaunchable_private_binary_never_uses_path(packaged, monkeypatch, tmp_path):
    runtime, _ = packaged
    monkeypatch.setenv("PATH", str(tmp_path / "foreign decoder"))
    with pytest.raises(RuntimeError, match="iniciar"):
        audio_decoder.decode_private_audio(b"fixture", runtime)


def test_packaged_tts_cannot_fall_back_to_path_even_for_wav(packaged, monkeypatch):
    runtime, _ = packaged
    monkeypatch.setattr(tts, "get_runtime", lambda: runtime)
    decoder = Mock(side_effect=RuntimeError("private decoder unavailable"))
    monkeypatch.setattr(audio_decoder, "decode_private_audio", decoder)
    forbidden = Mock(side_effect=AssertionError("external decoder is forbidden"))
    monkeypatch.setattr(tts, "_decode_with_pydub", forbidden)
    monkeypatch.setattr(tts, "_decode_with_ffmpeg", forbidden)
    monkeypatch.setattr(tts, "_decode_wav_stdlib", forbidden)
    with pytest.raises(RuntimeError, match="private decoder unavailable"):
        tts.decode_audio(b"RIFFsynthetic")
    forbidden.assert_not_called()


def test_development_preserves_stdlib_wav_fastpath(packaged, monkeypatch):
    runtime, _ = packaged
    monkeypatch.setattr(tts, "get_runtime", lambda: replace(runtime, mode="development"))
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16_000)
        output.writeframes(np.array([-100, 0, 100], dtype="<i2").tobytes())
    decoder = Mock(side_effect=AssertionError("development must retain its decoder"))
    monkeypatch.setattr(audio_decoder, "decode_private_audio", decoder)
    samples, rate = tts.decode_audio(buffer.getvalue())
    np.testing.assert_array_equal(samples, [-100, 0, 100])
    assert rate == 16_000
    decoder.assert_not_called()


def test_development_keeps_pydub_then_external_ffmpeg(packaged, monkeypatch):
    runtime, _ = packaged
    monkeypatch.setattr(tts, "get_runtime", lambda: replace(runtime, mode="development"))
    pydub = Mock(return_value=None)
    ffmpeg = Mock(return_value=(np.array([1], dtype=np.int16), 24_000))
    monkeypatch.setattr(tts, "_decode_with_pydub", pydub)
    monkeypatch.setattr(tts, "_decode_with_ffmpeg", ffmpeg)
    samples, rate = tts.decode_audio(b"encoded fixture")
    pydub.assert_called_once_with(b"encoded fixture")
    ffmpeg.assert_called_once_with(b"encoded fixture")
    assert samples.tolist() == [1] and rate == 24_000
