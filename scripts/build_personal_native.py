"""Rebuild the private audio inputs of a personal Windows build from pinned sources.

Called by build_from_source.py in its frozen source copy, never by the installer.
No Iris executable or developer cache is downloaded. Existing producers enforce
their source, toolchain, codec, license and dependency contracts.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

from scripts.prepare_private_runtime import (
    cached_archive,
    file_hash,
    output_directory,
    regular_file,
)

ROOT = Path(__file__).resolve().parents[1]


def document(name):
    return json.loads((ROOT / "scripts" / name).read_text("utf-8"))


def build(work: Path, cache: Path, msvc: Path, sdk: Path, seven_zip: Path, jobs: int) -> Path:
    output_directory(work)
    output_directory(cache)
    work.mkdir(exist_ok=False)
    cache.mkdir(parents=True, exist_ok=True)
    runtime = document("runtime_sources.json")
    audio = document("audio_build_sources.json")
    pyav = document("pyav_build_sources.json")
    messaging = document("messaging_audio_build_sources.json")

    def fetch(spec, filename=None):
        spec = dict(spec)
        spec["filename"] = filename or spec.get("filename") or Path(urlsplit(spec["url"]).path).name
        return cached_archive(cache, spec, offline=False)

    def run(name, *arguments):
        print(name, flush=True)
        with (work / (name + ".log")).open("w", encoding="utf-8") as log:
            subprocess.run(
                [str(value) for value in arguments],
                cwd=ROOT,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
            )

    ffmpeg = fetch(audio["source"], "ffmpeg-source.tar.gz")
    compiler_archive = fetch(audio["toolchain"])
    compiler_output = work / "compiler"
    run("extract-compiler", seven_zip, "x", compiler_archive, "-o" + str(compiler_output), "-y")
    compilers = list(compiler_output.glob("*/bin/gcc.exe"))
    if len(compilers) != 1:
        raise ValueError("No se encontró el único compilador portable esperado.")
    compiler = compilers[0].parents[1]
    for item in audio["notices"]:
        fetch(item)
    opus = fetch(messaging["codecs"]["opus"])
    lame = fetch(messaging["codecs"]["lame"])
    python_archive = fetch(pyav["python"])
    for item in [pyav["source"], *pyav["tools"]]:
        fetch(item)

    common = [
        "--source",
        ffmpeg,
        "--compiler",
        compiler,
        "--compiler-archive",
        compiler_archive,
        "--notices",
        cache,
    ]
    run(
        "messaging-audio",
        sys.executable,
        "-B",
        "-m",
        "scripts.build_messaging_audio",
        *common,
        "--opus-source",
        opus,
        "--lame-source",
        lame,
        "--output",
        work / "audio",
        "--jobs",
        jobs,
    )
    run(
        "ffmpeg-sdk",
        sys.executable,
        "-B",
        "-m",
        "scripts.build_pyav_runtime",
        *common,
        "--output",
        work / "ffmpeg",
        "--jobs",
        jobs,
    )
    run(
        "pyav-wheel",
        sys.executable,
        "-B",
        "-m",
        "scripts.build_pyav_wheel",
        "--inputs",
        cache,
        "--python-archive",
        python_archive,
        "--ffmpeg-sdk",
        work / "ffmpeg/package",
        "--msvc",
        msvc,
        "--windows-sdk",
        sdk,
        "--output",
        work / "pyav",
    )
    run(
        "private-shell",
        sys.executable,
        "-B",
        "-m",
        "scripts.build_shell_runtime",
        "--output",
        work / "shell",
        "--cache-root",
        cache,
        "--sources-cache",
        cache,
    )

    # Hashes of locally produced files belong to this build's pin. Upstream
    # source versions, producer names, source hashes and dependency locks stay fixed.
    runtime_cache = work / "runtime-inputs"
    runtime_cache.mkdir()
    produced = [
        (work / "audio/iris-native-messaging-audio.zip", runtime["audio"], "filename", "sha256")
    ]
    overlays = runtime.get("local_wheels", [])
    if len(overlays) != 1 or overlays[0]["package"] != "av":
        raise ValueError("Esta receta espera únicamente el overlay PyAV revisado.")
    produced.append(
        (work / "pyav/dist" / overlays[0]["filename"], overlays[0], "filename", "sha256")
    )
    for name, digest in [("filename", "sha256"), ("source_filename", "source_sha256")]:
        produced.append((work / "shell" / runtime["shell"][name], runtime["shell"], name, digest))
    for source, entry, filename_key, hash_key in produced:
        source = regular_file(source)
        target = runtime_cache / entry[filename_key]
        digest = file_hash(source)
        if target.exists():
            regular_file(target, digest)  # Never replace a different cached input.
        else:
            shutil.copyfile(source, target)
            regular_file(target, digest)
        entry[hash_key] = digest
    for archive in [
        fetch(runtime["python"]),
        fetch(runtime["hermes_python"]),
        fetch(
            {
                "url": runtime["hermes"]["source_url"],
                "filename": runtime["hermes"]["source_archive_filename"],
                "sha256": runtime["hermes"]["source_archive_sha256"],
            }
        ),
    ]:
        shutil.copyfile(archive, runtime_cache / archive.name)
    result = work / "native-pin.json"
    result.write_text(json.dumps(runtime, indent=2) + "\n", "utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for argument in ("work", "cache", "msvc", "windows-sdk", "seven-zip"):
        parser.add_argument("--" + argument, type=Path, required=True)
    parser.add_argument("--jobs", type=int, choices=range(1, 17), default=4)
    args = parser.parse_args()
    print(build(args.work, args.cache, args.msvc, args.windows_sdk, args.seven_zip, args.jobs))


if __name__ == "__main__":
    main()
