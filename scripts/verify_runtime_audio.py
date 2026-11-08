"""Real private-runtime decoder smoke with synthetic audio and no hardware.

Run this script with the staged Iris interpreter, -E -s -B, an isolated profile,
and a cwd outside staging. It intentionally never adds the checkout to sys.path.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import math
import os
import re
import socket
import stat
import sys
from pathlib import Path


def _block_network(*_args, **_kwargs):
    raise AssertionError("The synthetic decoder smoke does not use network sockets")


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--descriptor", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--fixtures-root", required=True, type=Path)
    args = parser.parse_args()
    descriptor = args.descriptor.resolve(strict=True)
    if not args.descriptor.is_absolute() or not args.evidence.is_absolute():
        parser.error("Descriptor and evidence paths must be absolute")
    os.environ["IRIS_RUNTIME_DESCRIPTOR"] = str(descriptor)
    socket.socket.connect = _block_network
    socket.socket.connect_ex = _block_network
    socket.create_connection = _block_network

    # These imports must resolve from the installation, without development's
    # venv, user site, cwd, PYTHONPATH or a fallback sys.path insertion.
    import numpy as np

    from core import audio_decoder, tts
    from core.runtime_descriptor import get_runtime

    runtime = get_runtime()
    if not runtime.packaged or Path(sys.executable).resolve(strict=True) != runtime.iris_python:
        raise AssertionError("Run the smoke with the packaged Iris interpreter")
    if Path.cwd().resolve().is_relative_to(runtime.base):
        raise AssertionError("The smoke cwd must be outside the installation")
    if args.evidence.resolve(strict=False).is_relative_to(runtime.base):
        raise AssertionError("Evidence must not be written to the installation")

    modules = {}
    for name in (
        "core", "core.tts", "core.audio_decoder", "numpy", "sounddevice", "edge_tts", "yaml",
    ):
        module = importlib.import_module(name)
        path = Path(module.__file__).resolve(strict=True)
        if not path.is_relative_to(runtime.base):
            raise AssertionError(f"External module used: {name}")
        modules[name] = path.relative_to(runtime.base).as_posix()
    audio_bin = audio_decoder.private_audio_bin(runtime)
    before = {item.name: _hash(item) for item in audio_bin.iterdir()}
    ffprobe = audio_decoder.private_audio_tool("ffprobe", runtime)
    command_env = {
        key: value for key, value in os.environ.items()
        if key.upper() in {"SYSTEMROOT", "WINDIR", "TEMP", "TMP"}
    }
    command_env["PATH"] = ""

    # Fixtures are generated explicitly during QA, never by the product's
    # decoder. Native decoders do not need third-party encoder libraries.
    fixture_root = args.fixtures_root.resolve(strict=True)
    if not args.fixtures_root.is_absolute() or fixture_root.is_relative_to(runtime.base):
        raise AssertionError("Synthetic fixtures must be absolute and outside installation")
    fixture_manifest = fixture_root / "manifest.json"
    manifest_bytes = fixture_manifest.read_bytes()
    if len(manifest_bytes) > 64 * 1024:
        raise AssertionError("Oversized synthetic audio manifest")
    manifest = json.loads(manifest_bytes)
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise AssertionError("Unknown synthetic audio manifest schema")
    entries = manifest.get("fixtures")
    required = {"wav", "mp3", "vorbis", "opus", "aac"}
    if not isinstance(entries, list) or len(entries) != 5:
        raise AssertionError("The decoder smoke requires five synthetic audio formats")
    encoded = {}
    durations = {}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {
            "format", "file", "size", "sha256", "duration_seconds",
        }:
            raise AssertionError("Invalid synthetic audio fixture fields")
        label, filename = entry["format"], entry["file"]
        if (
            label not in required or label in encoded or not isinstance(filename, str)
            or not re.fullmatch(r"[a-z0-9_]+\.(wav|mp3|ogg|opus|aac)", filename)
        ):
            raise AssertionError("Unknown or duplicate audio format/path")
        path = fixture_root / filename
        info = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or getattr(info, "st_file_attributes", 0) & 0x400
            or path.resolve(strict=True) != path
            or not 0 < info.st_size <= 1024 * 1024
        ):
            raise AssertionError("Synthetic audio fixture is aliased or oversized")
        data = path.read_bytes()
        duration = entry["duration_seconds"]
        if (
            type(entry["size"]) is not int or len(data) != entry["size"]
            or hashlib.sha256(data).hexdigest() != entry["sha256"]
            or type(duration) not in {int, float} or not math.isfinite(duration)
            or not 0 < duration < 10
        ):
            raise AssertionError("Synthetic audio fixture hash, size or duration mismatch")
        encoded[label] = data
        durations[label] = duration

    results = []
    for path_mode, path_value in (
        ("empty", ""), ("untrusted", str(args.evidence.parent / "PATH señuelo")),
    ):
        os.environ["PATH"] = path_value
        os.environ["FFMPEG_BINARY"] = str(args.evidence.parent / "wrong ffmpeg.exe")
        os.environ["IMAGEIO_FFMPEG_EXE"] = os.environ["FFMPEG_BINARY"]
        os.environ["FFREPORT"] = "file=must-not-be-created.log:level=32"
        for label, data in encoded.items():
            duration = durations[label]
            samples, rate = tts.decode_audio(data)
            seconds = len(samples) / rate
            rms = float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))
            if samples.dtype != np.int16 or samples.ndim != 1 or rate != 24_000:
                raise AssertionError(f"Invalid private decoder PCM contract: {label}")
            if not duration - 0.03 <= seconds <= duration + 0.2 or rms < 500:
                raise AssertionError(f"Incorrect decoded duration or tone amplitude: {label}")
            results.append({
                "format": label, "path": path_mode, "encoded_bytes": len(data),
                "samples": len(samples), "sample_rate": rate, "duration_seconds": seconds,
                "rms": round(rms, 3), "pcm_sha256": hashlib.sha256(samples.tobytes()).hexdigest(),
            })

    # FFprobe is included for Hermes consumers too; validate the private binary
    # and machine-readable audio probe independently of any global executable.
    probe_output = audio_decoder._bounded_run(
        [str(ffprobe), "-v", "error", "-protocol_whitelist", "pipe", "-i", "pipe:0",
         "-show_entries", "stream=codec_name,sample_rate,channels", "-of", "json"],
        encoded["mp3"], cwd=audio_bin, env=command_env, output_limit=64 * 1024,
    )
    probe = json.loads(probe_output)
    if len(probe.get("streams", [])) != 1 or probe["streams"][0].get("codec_name") != "mp3":
        raise AssertionError("Private FFprobe returned ambiguous audio stream metadata")
    rejected = 0
    for invalid in (b"not an audio stream", b"#EXTM3U\nhttp://127.0.0.1:1/private\n"):
        try:
            tts.decode_audio(invalid)
        except RuntimeError:
            rejected += 1
        else:
            raise AssertionError("The decoder accepted invalid synthetic input")
    after = {item.name: _hash(item) for item in audio_bin.iterdir()}
    if after != before or (audio_bin / "must-not-be-created.log").exists():
        raise AssertionError("The decoder modified the installation")
    evidence = {
        "completed": True, "hardware_access": False, "network_access": False,
        "python": runtime.iris_python.relative_to(runtime.base).as_posix(),
        "modules": modules, "decoder_bin": audio_bin.relative_to(runtime.base).as_posix(),
        "decoder_hashes": before, "decoder_unchanged": True,
        "fixture_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "results": results, "private_ffprobe": probe, "invalid_inputs_rejected": rejected,
    }
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", "utf-8")
    print(json.dumps({"completed": True, "formats": len(encoded), "decodes": len(results),
                      "evidence": str(args.evidence)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
