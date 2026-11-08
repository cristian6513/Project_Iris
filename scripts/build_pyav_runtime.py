"""Build a separate, pinned LGPL native FFmpeg SDK for the PyAV runtime.

The existing Iris audio decoder and its pins are immutable inputs. This profile
adds avdevice/swscale and the permitted native CPU codec/filter catalog without
external libraries. It never discovers or installs global compilers.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

if __package__:
    from . import build_audio_decoder as audio
    from .distribution_assets import DistributionError
    from .runtime_inventory import read_json
else:
    import build_audio_decoder as audio
    from distribution_assets import DistributionError
    from runtime_inventory import read_json

PIN_PATH = Path(__file__).with_name("pyav_ffmpeg_sources.json")
FLAGS = [
    "--prefix=../package",
    "--target-os=mingw32",
    "--arch=x86_64",
    "--cc=gcc",
    "--cxx=g++",
    "--ln_s=cp -f",
    "--disable-autodetect",
    "--disable-network",
    "--disable-ffplay",
    "--disable-doc",
    "--disable-debug",
    "--disable-x86asm",
    # Without external x86asm, the remaining MMXEXT inline-asm swscale vertical scaler
    # corrupts packed RGB -> yuv420p chroma (verified 2026-10-02); build pure C instead.
    "--disable-inline-asm",
    "--disable-pthreads",
    "--enable-w32threads",
    "--enable-shared",
    "--disable-static",
    "--disable-gpl",
    "--disable-version3",
    "--disable-nonfree",
    "--disable-indevs",
    "--disable-outdevs",
    "--disable-hwaccels",
    "--enable-ffmpeg",
    "--enable-ffprobe",
    "--enable-avcodec",
    "--enable-avformat",
    "--enable-avdevice",
    "--enable-avutil",
    "--enable-avfilter",
    "--enable-swscale",
    "--enable-swresample",
    "--extra-ldflags=-static-libgcc -Wl,--no-insert-timestamp",
    "--extra-version=iris-pyav-29e619e767",
]
LIBRARIES = {
    "avcodec-62.dll",
    "avformat-62.dll",
    "avdevice-62.dll",
    "avutil-60.dll",
    "avfilter-11.dll",
    "swscale-9.dll",
    "swresample-6.dll",
}


def validate_pin(pin: dict) -> dict:
    fields = {
        "schema",
        "component",
        "platform",
        "arch",
        "profile",
        "base_sources_sha256",
        "source",
        "toolchain",
        "notices",
        "configure",
    }
    if (
        not isinstance(pin, dict)
        or set(pin) != fields
        or type(pin.get("schema")) is not int
        or pin["schema"] != 1
        or pin["component"] != "iris-pyav-ffmpeg-sdk"
        or pin["platform"] != "windows"
        or pin["arch"] != "x86_64"
        or pin["profile"] != "native-lgpl-cpu"
        or pin["configure"] != FLAGS
    ):
        raise DistributionError("Unknown or modified PyAV FFmpeg build profile")
    if audio.file_hash(audio.PIN_PATH) != pin["base_sources_sha256"]:
        raise DistributionError("Iris audio source pin changed; SDK must be reviewed again")
    base = read_json(audio.PIN_PATH)
    audio.validate_pin(base)
    if any(pin[name] != base[name] for name in ("source", "toolchain", "notices")):
        raise DistributionError("PyAV SDK must use the reviewed audio source/compiler/notices")
    return base


def preflight(source, compiler, compiler_archive, notices, output, pin):
    base = validate_pin(pin)
    return audio.preflight(source, compiler, compiler_archive, notices, output, base)


def finish_sdk_provenance(package: Path, pin: dict) -> None:
    """Supply rebuild sources and truthful shared-library instructions for PyAV."""
    licenses = package / "licenses/audio"
    sources = licenses / "sources"
    for filename in (
        "build_pyav_runtime.py",
        "pyav_ffmpeg_sources.json",
        "build_audio_decoder.py",
        "audio_build_sources.json",
        "runtime_inventory.py",
        "distribution_assets.py",
    ):
        shutil.copyfile(Path(__file__).with_name(filename), sources / filename)
    (sources / "BUILD.md").write_text(
        "# Iris PyAV native LGPL FFmpeg SDK\n\n"
        "The bundled FFmpeg source archive is unmodified commit "
        + pin["source"]["revision"]
        + ". This separate SDK builds seven native shared FFmpeg libraries for PyAV. "
        "It includes the permitted native CPU codec/filter/demux/mux catalog; external "
        "libraries, GPL, version3, nonfree, hardware devices and network are disabled.\n\n"
        "Use the official pinned w64devkit binary archive and its matching source archive "
        "from pyav_ffmpeg_sources.json. The compiler is a build input, not part of Iris. "
        "Its GCC runtime exception and MinGW runtime notices are in the parent directory.\n\n"
        "Rebuild in an extracted source directory with PATH set only to the compiler bin, "
        "COMSPEC pointing to Windows System32/cmd.exe, TMPDIR=../temp, ZERO_AR_DATE=1 "
        "and SOURCE_DATE_EPOCH=" + str(pin["source"]["source_date_epoch"]) + ". "
        "Create ../temp first. Run sh ./configure with the exact configure argument list "
        "in pyav_ffmpeg_sources.json, then make -j4 V=1 and make install V=1. "
        "Alternatively use the included Python build_pyav_runtime.py with explicit "
        "--source, --compiler, --compiler-archive, --notices and a fresh --output directory.\n\n"
        "PyAV dynamically links these LGPL-2.1-or-later DLLs. Compatible rebuilt DLLs "
        "can replace them; preserve the API and the wheel's mangled filenames. The wheel "
        "binding build instructions explain MSVC import libraries and delvewheel repair. "
        "The FFmpeg source and this build recipe are supplied to allow modification "
        "and rebuilding of those shared libraries.\n",
        "utf8",
    )
    components = read_json(licenses / "components.json")
    components["licenses"][0]["files"] += [
        "licenses/audio/sources/" + filename
        for filename in (
            "build_pyav_runtime.py",
            "pyav_ffmpeg_sources.json",
            "build_audio_decoder.py",
            "audio_build_sources.json",
            "runtime_inventory.py",
            "distribution_assets.py",
        )
    ]
    (licenses / "components.json").write_text(json.dumps(components, indent=2) + "\n", "utf8")
    result = read_json(licenses / "audio-build.json")
    result["component"] = "iris-pyav-ffmpeg-sdk"
    result["files"] = [
        {
            "path": path.relative_to(package).as_posix(),
            "size": path.stat().st_size,
            "sha256": audio.file_hash(path),
        }
        for directory in (package / "bin", licenses)
        for path in sorted(directory.rglob("*"))
        if path.is_file()
        and path.name != "audio-build.json"
        and (directory == licenses or path.suffix.lower() in {".exe", ".dll"})
    ]
    (licenses / "audio-build.json").write_text(json.dumps(result, indent=2) + "\n", "utf8")


def swscale_smoke(package: Path, environment: dict, output: Path) -> None:
    """PyAV needs swscale: neutral gray must survive RGB -> yuv420p -> RGB exactly, every run."""
    import subprocess

    work = output / "swscale-smoke"
    work.mkdir()
    gray = work / "gray.rgb"
    gray.write_bytes(bytes([128]) * 64 * 64 * 3)
    env = dict(environment, PATH=str(package / "bin"))
    binary = str(package / "bin/ffmpeg.exe")
    raw = ["-f", "rawvideo", "-s", "64x64"]
    for run in range(3):
        yuv, back = work / f"gray-{run}.yuv", work / f"gray-{run}.rgb"
        for source, source_format, target, target_format in (
            (gray, "rgb24", yuv, "yuv420p"), (yuv, "yuv420p", back, "rgb24")):
            command = [binary, "-nostdin", "-v", "error", "-y", *raw, "-pix_fmt", source_format,
                       "-i", str(source), "-pix_fmt", target_format, *raw[:2], str(target)]
            subprocess.run(command,
                           env=env, cwd=work, check=True, timeout=30, stdin=subprocess.DEVNULL)
        planes = yuv.read_bytes()
        if len(planes) != 64 * 64 * 3 // 2 or set(planes[64 * 64:]) != {128}:
            raise DistributionError("PyAV SDK swscale corrupts yuv420p chroma")
        if max(abs(value - 128) for value in back.read_bytes()) > 2:
            raise DistributionError("PyAV SDK swscale RGB round trip is not neutral")


def build_sdk(source, compiler, compiler_archive, notices, output, *, pin=None, jobs=4) -> Path:
    import os

    pin = read_json(PIN_PATH) if pin is None else pin
    source, compiler, output = preflight(source, compiler, compiler_archive, notices, output, pin)
    if os.name != "nt" or type(jobs) is not int or not 1 <= jobs <= 16:
        raise DistributionError("PyAV FFmpeg SDK requires Windows and 1–16 workers")
    output.mkdir()
    for directory in ("home", "temp", "logs"):
        (output / directory).mkdir()
    build = output / "source"
    audio.extract_source(source, build)
    environment = audio.build_environment(compiler, output, pin["source"]["source_date_epoch"])
    audio.run(
        [str(compiler / "bin/sh.exe"), "./configure", *FLAGS],
        build,
        environment,
        output / "logs/configure.log",
    )
    audio.run(
        [str(compiler / "bin/make.exe"), f"-j{jobs}", "V=1"],
        build,
        environment,
        output / "logs/make.log",
    )
    audio.run(
        [str(compiler / "bin/make.exe"), "install", "V=1"],
        build,
        environment,
        output / "logs/install.log",
    )
    # The existing finalizer checks actual LGPL output, WAV/FFprobe decoding,
    # PE imports and includes exact source archives and compiler runtime notices.
    package = audio.finish_build(source, compiler, notices, output, pin, environment)
    dlls = {path.name for path in (package / "bin").glob("*.dll")}
    if dlls != LIBRARIES:
        raise DistributionError(f"SDK requires exactly the seven reviewed FFmpeg DLLs: {dlls}")
    for library in LIBRARIES:
        if not (package / "lib" / (Path(library).stem + ".def")).is_file():
            raise DistributionError(f"Missing SDK export definitions for {library}")
    if not (package / "include/libavdevice/avdevice.h").is_file():
        raise DistributionError("Missing SDK public development headers")
    swscale_smoke(package, environment, output)
    finish_sdk_provenance(package, pin)
    receipt = {
        "schema": 1,
        "component": "iris-pyav-ffmpeg-sdk",
        "pin": pin,
        "libraries": sorted(LIBRARIES),
        "wav_smoke": True,
        "files": [
            {
                "path": path.relative_to(package).as_posix(),
                "size": path.stat().st_size,
                "sha256": audio.file_hash(path),
            }
            for path in sorted(package.rglob("*"))
            if path.is_file()
        ],
    }
    (package / "pyav-ffmpeg-build.json").write_text(json.dumps(receipt, indent=2) + "\n", "utf8")
    return package


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for argument in ("source", "compiler", "compiler-archive", "notices", "output"):
        parser.add_argument("--" + argument, type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=4)
    arguments = parser.parse_args(argv)
    package = build_sdk(
        arguments.source,
        arguments.compiler,
        arguments.compiler_archive,
        arguments.notices,
        arguments.output,
        jobs=arguments.jobs,
    )
    print(f"PyAV native LGPL FFmpeg SDK built: {package}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
