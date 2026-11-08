"""Build the private audio CLI with the encoders used by pinned Hermes messaging/TTS.

The minimal native decoder remains a separate reproducible profile. This build adds
only reviewed static Opus (BSD-3-Clause) and LAME (LGPL-2.0-or-later) encoders, the
native AAC/FLAC encoders, Ogg/Opus/MP3/FLAC/ADTS/M4A muxers and the concat demuxer
that Hermes TTS delivery, voice bubbles and Signal/Matrix adapters invoke. It
performs no downloads, discovery or global installation.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import struct
import subprocess
import wave
from pathlib import Path

if __package__:
    from . import build_audio_decoder as audio
    from .distribution_assets import DistributionError
    from .runtime_inventory import read_json
else:
    import build_audio_decoder as audio
    from distribution_assets import DistributionError
    from runtime_inventory import read_json

PIN_PATH = Path(__file__).with_name("messaging_audio_build_sources.json")
# Autoconf requires an absolute --prefix and the workspace path contains spaces.
# Codecs are configured for a fixed POSIX prefix and copied below ../codecs;
# pkg-config maps it back through PKG_CONFIG_SYSROOT_DIR, so no producer path is recorded.
CODEC_PREFIX = "/codecs"
CODEC_SYSROOT = ".."
OPUS_FLAGS = [
    "--prefix=" + CODEC_PREFIX,
    "--build=x86_64-w64-mingw32",
    "--host=x86_64-w64-mingw32",
    "--disable-shared",
    "--enable-static",
    "--disable-doc",
    "--disable-extra-programs",
    "--disable-asm",
    "--disable-rtcd",
    "--disable-intrinsics",
    "--disable-dred",
    "--disable-deep-plc",
    "--disable-osce",
    "--disable-maintainer-mode",
    "--disable-dependency-tracking",
]
LAME_FLAGS = [
    "--prefix=" + CODEC_PREFIX,
    "--build=x86_64-w64-mingw32",
    "--host=x86_64-w64-mingw32",
    "--disable-shared",
    "--enable-static",
    "--disable-frontend",
    "--disable-decoder",
    "--disable-gtktest",
    "--disable-analyzer-hooks",
    "--disable-rpath",
    "--disable-dependency-tracking",
]
CODECS = {
    "opus": {
        "version": "1.6.1",
        "url": "https://downloads.xiph.org/releases/opus/opus-1.6.1.tar.gz",
        "sha256": "6ffcb593207be92584df15b32466ed64bbec99109f007c82205f0194572411a1",
        "license": "BSD-3-Clause",
        "notices": ["COPYING"],
        "configure": OPUS_FLAGS,
    },
    "lame": {
        "version": "4.0",
        "url": "https://downloads.sourceforge.net/project/lame/lame/4.0/lame-4.0.tar.gz",
        "sha256": "3df5124d5ad3a98312ffd7ba6a9b36230e4f8a3e66d3ce0f425e336c32d216eb",
        "license": "LGPL-2.0-or-later",
        "notices": ["COPYING", "LICENSE"],
        "configure": LAME_FLAGS,
    },
}
# Autoconf records absolute PATH hits; the verified compiler lives below a path with spaces,
# which unquoted Makefile variables split. Name the tools so the private PATH resolves them.
AUTOCONF_TOOLS = {
    "GREP": "grep", "EGREP": "grep -E", "FGREP": "grep -F", "SED": "sed",
    "MKDIR_P": "mkdir -p", "INSTALL": "install -c", "LD": "ld", "NM": "nm",
    "PKG_CONFIG": "pkg-config",
    # Codecs may only see what this build produced, never compiler-bundled .pc files.
    "PKG_CONFIG_LIBDIR": "../codecs/lib/pkgconfig", "PKG_CONFIG_PATH": "",
}
# GNU make for Windows expands $(SHELL) to the absolute (spaced) shell path in every
# sub-make; libtool is the only $(SHELL) consumer of these builds, so it is named here.
CODEC_MAKE_ARGS = ["V=1", "MAKE=make", "LIBTOOL=sh $(top_builddir)/libtool"]
# libtool refuses a relative DESTDIR and an absolute one contains spaces: copy the
# reviewed upstream install set (static library, public headers, pkg-config) instead.
CODEC_INSTALL = {
    "opus": {
        ".libs/libopus.a": "lib/libopus.a",
        "opus.pc": "lib/pkgconfig/opus.pc",
        **{f"include/{header}": f"include/opus/{header}" for header in (
            "opus.h", "opus_multistream.h", "opus_types.h", "opus_defines.h", "opus_projection.h")},
    },
    "lame": {
        "libmp3lame/.libs/libmp3lame.a": "lib/libmp3lame.a",
        "include/lame.h": "include/lame/lame.h",
    },
}
_FLAG_CHANGES = {
    "--enable-demuxer=": "--enable-demuxer=concat,",
    "--enable-muxer=": "--enable-muxer=ogg,opus,mp3,flac,adts,ipod,mp4,",
    "--enable-encoder=": "--enable-encoder=libopus,libmp3lame,aac,flac,",
    "--extra-ldflags=": "--extra-ldflags=-L../codecs/lib ",
    "--extra-version=iris-audio-": "--extra-version=iris-messaging-audio-",
}


def _messaging_flags(base: list[str]) -> list[str]:
    flags = list(base)
    for prefix, replacement in _FLAG_CHANGES.items():
        matches = [index for index, flag in enumerate(flags) if flag.startswith(prefix)]
        if len(matches) != 1:
            raise DistributionError("Minimal audio profile changed; messaging flags need review")
        flags[matches[0]] = replacement + flags[matches[0]][len(prefix):]
    return flags + [
        "--enable-bsf=aac_adtstoasc",
        "--enable-libopus",
        "--enable-libmp3lame",
        "--extra-cflags=-I../codecs/include",
    ]


FFMPEG_FLAGS = _messaging_flags(audio.FLAGS)


def validate_pin(pin: dict, base_pin: dict, base_hash: str) -> None:
    if (
        not isinstance(pin, dict)
        or set(pin) != {"schema", "platform", "arch", "base_sources_sha256", "codecs", "configure"}
        or type(pin.get("schema")) is not int
        or pin["schema"] != 1
        or pin.get("platform") != "windows"
        or pin.get("arch") != "x86_64"
        or pin.get("configure") != FFMPEG_FLAGS
    ):
        raise DistributionError("Unknown or modified messaging audio build contract")
    if pin.get("base_sources_sha256") != base_hash:
        raise DistributionError(
            "Iris audio source pin changed; messaging audio must be reviewed again"
        )
    audio.validate_pin(base_pin)
    if pin.get("codecs") != CODECS:
        raise DistributionError("Unknown or modified Opus/LAME build source")


def preflight(source, opus_source, lame_source, compiler, compiler_archive, notices, output, pin):
    base_pin = read_json(audio.PIN_PATH)
    validate_pin(pin, base_pin, audio.file_hash(audio.PIN_PATH))
    for name, archive in (("opus", opus_source), ("lame", lame_source)):
        if audio.file_hash(Path(archive)) != CODECS[name]["sha256"]:
            raise DistributionError(f"Incorrect {name} build input hash")
    source, compiler, output = audio.preflight(
        source, compiler, compiler_archive, notices, output, base_pin
    )
    # Require the verified compiler's own pkg-config, never a producer PATH tool.
    audio._regular(compiler, "bin/pkg-config.exe")
    opus_source, lame_source = Path(opus_source).absolute(), Path(lame_source).absolute()
    return base_pin, opus_source, lame_source, source, compiler, output


def _tone(path: Path, rate: int = 24000, seconds: float = 0.25) -> None:
    frames = int(rate * seconds)
    samples = (int(8000 * math.sin(2 * math.pi * 440 * index / rate)) for index in range(frames))
    with wave.open(str(path), "wb") as wav:
        wav.setparams((1, 2, rate, frames, "NONE", "not compressed"))
        wav.writeframes(b"".join(struct.pack("<h", sample) for sample in samples))


def _probe(probe: Path, path: Path, work: Path, env: dict) -> dict:
    raw = subprocess.check_output(
        [str(probe), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        cwd=work, env=env, timeout=30, stdin=subprocess.DEVNULL,
    )
    document = json.loads(raw)
    streams = document.get("streams", [])
    if len(streams) != 1:
        raise DistributionError(f"Messaging smoke expected one audio stream: {path.name}")
    return {
        "codec": streams[0].get("codec_name"),
        "channels": streams[0].get("channels"),
        "format": document.get("format", {}).get("format_name"),
        "duration": round(float(document.get("format", {}).get("duration", 0)), 3),
    }


def messaging_smoke(binary: Path, probe: Path, work: Path, env: dict) -> dict:
    """Run the exact argument shapes of pinned Hermes consumers, offline."""
    work.mkdir()

    def ffmpeg(*args):
        subprocess.run([str(binary), "-nostdin", *args], cwd=work, env=env, check=True,
                       timeout=60, stdin=subprocess.DEVNULL, capture_output=True)

    source = work / "tone.wav"
    _tone(source)
    voice = ["-acodec", "libopus", "-ac", "1", "-b:a", "48k", "-vbr", "on",
             "-application", "voip", "-compression_level", "10"]
    # tools/tts_tool_delivery._write_wav_bytes_as and _finalize_wav_output.
    ffmpeg("-i", str(source), *voice, "-y", "-loglevel", "error", str(work / "voice.ogg"))
    for suffix in ("mp3", "m4a", "aac", "flac", "opus"):
        ffmpeg("-i", str(source), "-y", "-loglevel", "error", str(work / f"tts.{suffix}"))
    # _concat_audio_files: Ogg is re-encoded, MP3 keeps frames.
    for name, codec in (("voice", ["-c:a", "libopus", "-ac", "1", "-b:a", "64k", "-vbr", "off"]),
                        ("tts", ["-c:a", "copy"])):
        suffix = "ogg" if name == "voice" else "mp3"
        listing = work / f"{name}.concat.txt"
        listing.write_text("".join(f"file '{(work / f'{name}.{suffix}').as_posix()}'\n"
                                   for _ in range(2)), "utf-8")
        ffmpeg("-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listing), "-vn",
               *codec, str(work / f"{name}-joined.{suffix}"))
    # gateway.platforms.base.transcode_to_ogg_opus repairing WAV bytes in a .ogg path.
    shutil.copyfile(source, work / "repair.ogg")
    ffmpeg("-v", "error", "-y", "-i", str(work / "repair.ogg"), *voice, "-f", "ogg",
           str(work / "repair.tmp.ogg"))
    # gateway/platforms/signal.py AAC -> M4A remux.
    ffmpeg("-y", "-loglevel", "error", "-i", str(work / "tts.aac"), "-c:a", "copy",
           "-movflags", "+faststart", str(work / "signal.m4a"))
    expected = {
        "voice.ogg": ("opus", "ogg", 0.25), "voice-joined.ogg": ("opus", "ogg", 0.5),
        "repair.tmp.ogg": ("opus", "ogg", 0.25), "tts.mp3": ("mp3", "mp3", 0.25),
        "tts-joined.mp3": ("mp3", "mp3", 0.5), "tts.flac": ("flac", "flac", 0.25),
        "tts.opus": ("opus", "ogg", 0.25), "tts.aac": ("aac", "aac", 0.25),
        "tts.m4a": ("aac", "mov,mp4,m4a,3gp,3g2,mj2", 0.25),
        "signal.m4a": ("aac", "mov,mp4,m4a,3gp,3g2,mj2", 0.25),
    }
    evidence = {}
    for name, (codec, container, seconds) in expected.items():
        found = _probe(probe, work / name, work, env)
        # Encoders add priming/padding; never accept a dropped or truncated chunk.
        if (found["codec"] != codec or found["channels"] != 1 or found["format"] != container
                or not seconds - 0.05 <= found["duration"] <= seconds + 0.12):
            raise DistributionError(f"Messaging audio smoke failed for {name}: {found}")
        evidence[name] = found
    # plugins/platforms/matrix waveform decode and the five-format decoder path.
    for name in ("voice.ogg", "tts.mp3", "tts.m4a", "tts.flac", "tone.wav"):
        pcm = subprocess.check_output(
            [str(binary), "-v", "error", "-nostdin", "-i", str(work / name),
             "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
            cwd=work, env=env, timeout=30, stdin=subprocess.DEVNULL,
        )
        if not 3200 <= len(pcm) <= 5800 or not any(pcm):
            raise DistributionError(f"Messaging audio decode smoke failed for {name}")
        evidence[name + ".s16le"] = {"bytes": len(pcm)}
    return evidence


def _build_codec(name: str, archive: Path, output: Path, compiler: Path, env: dict, jobs: int):
    tree = output / f"{name}-source"
    audio.extract_source(archive, tree)
    # busybox-w32 passes autoconf's ":" probe, which would split "F:\..." PATH entries.
    # busybox-w32 only matches executables with their .exe suffix during PATH searches.
    codec_env = dict(env, CFLAGS=f"-O2 -ffile-prefix-map=.={name}-source", PATH_SEPARATOR=";",
                     ac_executable_extensions=".exe", **AUTOCONF_TOOLS)
    make = str(compiler / "bin/make.exe")
    audio.run([str(compiler / "bin/sh.exe"), "./configure", *CODECS[name]["configure"]],
              tree, codec_env, output / f"logs/{name}-configure.log")
    # Automake recurses through an unquoted $(MAKE); the verified absolute make path
    # contains spaces, so recursion resolves "make" from the private-only PATH instead.
    audio.run([make, f"-j{jobs}", *CODEC_MAKE_ARGS], tree, codec_env,
              output / f"logs/{name}-make.log")
    codecs = output / CODEC_PREFIX.lstrip("/")
    for built, installed in CODEC_INSTALL[name].items():
        source, _ = audio._regular(tree, built)
        target = codecs / installed
        target.parent.mkdir(parents=True, exist_ok=True)
        with source.open("rb") as incoming, target.open("xb") as outgoing:
            shutil.copyfileobj(incoming, outgoing)
    return tree


def build_messaging_audio(
    source, opus_source, lame_source, compiler, compiler_archive, notices, output, *,
    pin=None, jobs=4,
) -> Path:
    pin = read_json(PIN_PATH) if pin is None else pin
    base_pin, opus_source, lame_source, source, compiler, output = preflight(
        source, opus_source, lame_source, compiler, compiler_archive, notices, output, pin
    )
    if os.name != "nt" or type(jobs) is not int or not 1 <= jobs <= 16:
        raise DistributionError("Messaging audio build requires Windows and 1–16 workers")
    stray = Path(output.anchor) / CODEC_PREFIX.lstrip("/")
    stray_before = stray.exists()
    output.mkdir()
    for name in ("home", "temp", "logs"):
        (output / name).mkdir()
    env = audio.build_environment(compiler, output, base_pin["source"]["source_date_epoch"])
    trees = {
        name: _build_codec(name, archive, output, compiler, env, jobs)
        for name, archive in (("opus", opus_source), ("lame", lame_source))
    }
    if not stray_before and stray.exists():
        raise DistributionError("Codec build escaped its staging directory")
    codecs = output / CODEC_PREFIX.lstrip("/")
    for required in ("lib/libopus.a", "lib/libmp3lame.a", "include/opus/opus.h",
                     "include/lame/lame.h", "lib/pkgconfig/opus.pc"):
        audio._regular(codecs, required)
    build = output / "source"
    audio.extract_source(source, build)
    ffmpeg_env = dict(env, PKG_CONFIG_LIBDIR="../codecs/lib/pkgconfig", PKG_CONFIG_PATH="",
                      PKG_CONFIG_SYSROOT_DIR=CODEC_SYSROOT)
    make = str(compiler / "bin/make.exe")
    audio.run([str(compiler / "bin/sh.exe"), "./configure", *FFMPEG_FLAGS], build, ffmpeg_env,
              output / "logs/configure.log")
    audio.run([make, f"-j{jobs}", "V=1"], build, ffmpeg_env, output / "logs/make.log")
    audio.run([make, "install", "V=1"], build, ffmpeg_env, output / "logs/install.log")
    package = output / "package"
    licenses = package / "licenses/audio"
    (licenses / "sources").mkdir(parents=True)
    components = []
    for name, archive in (("opus", opus_source), ("lame", lame_source)):
        item, label = CODECS[name], {"opus": "Opus", "lame": "LAME"}[name]
        files = []
        for notice in item["notices"]:
            target = f"{label}-{notice}"
            shutil.copyfile(trees[name] / notice, licenses / target)
            files.append("licenses/audio/" + target)
        shutil.copyfile(archive, licenses / "sources" / archive.name)
        files.append("licenses/audio/sources/" + archive.name)
        components.append({"component": "audio", "package": label, "version": item["version"],
                           "license": item["license"], "source": item["url"], "files": files})
    # LAME's LICENSE asks redistributors to acknowledge LAME and link its site.
    (licenses / "LAME-NOTICE.txt").write_text(
        "This software uses LAME " + CODECS["lame"]["version"] + " (https://lame.sourceforge.io/), "
        "statically linked into the shared FFmpeg avcodec library under the LGPL.\n", "utf-8",
    )
    components[1]["files"].append("licenses/audio/LAME-NOTICE.txt")
    recipe =("build_messaging_audio.py", "messaging_audio_build_sources.json",
              "build_audio_decoder.py", "audio_build_sources.json",
              "runtime_inventory.py", "distribution_assets.py")
    for filename in recipe:
        shutil.copyfile(Path(__file__).with_name(filename), licenses / "sources" / filename)
    components[0]["files"] += ["licenses/audio/sources/" + filename for filename in recipe]
    smoke_env = dict(env, PATH=str(package / "bin"))
    evidence = messaging_smoke(package / "bin/ffmpeg.exe", package / "bin/ffprobe.exe",
                               output / "smoke", smoke_env)
    (licenses / "messaging-smoke.json").write_text(json.dumps(evidence, indent=2) + "\n", "utf-8")
    combined_pin = dict(base_pin, configure=FFMPEG_FLAGS, codecs=CODECS,
                        messaging_sources_sha256=audio.file_hash(PIN_PATH))
    instructions = (
        "# Iris messaging audio CLI\n\n"
        "The bundled FFmpeg source is unmodified commit " + base_pin["source"]["revision"] + ". "
        "Opus " + CODECS["opus"]["version"] + " and LAME " + CODECS["lame"]["version"] + " "
        "are the unmodified upstream archives in this directory, built as static libraries and "
        "linked into the shared FFmpeg avcodec library. No GPL, version3, nonfree or network "
        "features are enabled.\n\n"
        "Use the pinned portable w64devkit compiler with PATH limited to its bin, COMSPEC set to "
        "the Windows System32/cmd.exe, LC_ALL=C, ZERO_AR_DATE=1, SOURCE_DATE_EPOCH="
        + str(base_pin["source"]["source_date_epoch"]) + " and TMPDIR=../temp. Extract the "
        "archives into sibling directories opus-source, lame-source and source, and create "
        "../temp and ../home next to them.\n\n"
        "1. In opus-source and then lame-source set PATH_SEPARATOR=; "
        "ac_executable_extensions=.exe, "
        "the tool names in AUTOCONF_TOOLS of build_messaging_audio.py and "
        "CFLAGS='-O2 -ffile-prefix-map=.=<directory>' "
        "and run sh ./configure with the codecs.<name>.configure arguments from "
        "messaging_audio_build_sources.json, then make -j4 with CODEC_MAKE_ARGS "
        "(V=1 MAKE=make LIBTOOL='sh $(top_builddir)/libtool'). Copy the CODEC_INSTALL files "
        "(static library, public headers, opus.pc) into ../codecs, preserving the listed "
        "relative paths; the configured prefix " + CODEC_PREFIX + " maps to ../codecs.\n"
        "2. Unset CFLAGS; set PKG_CONFIG_LIBDIR=../codecs/lib/pkgconfig, an empty PKG_CONFIG_PATH "
        "and PKG_CONFIG_SYSROOT_DIR=.. ; in source run sh ./configure with the configure array, "
        "make -j4 V=1 and make install V=1. The result is in ../package.\n\n"
        "Alternatively run the included build_messaging_audio.py with --source, --opus-source, "
        "--lame-source, --compiler, --compiler-archive, --notices and a fresh --output.\n\n"
        "FFmpeg and LAME are LGPL; Opus is BSD-3-Clause. The shared FFmpeg DLLs, including the "
        "statically linked Opus and LAME code inside avcodec, may be replaced with compatible "
        "rebuilt copies made from these sources; Iris and Hermes invoke ffmpeg/ffprobe as "
        "separate processes. GCC runtime exception and MinGW runtime notices are in the parent "
        "directory. The compiler is a build input, not bundled with Iris.\n"
    )
    return audio.finish_build(source, compiler, notices, output, combined_pin, env,
                              extra_components=components, build_instructions=instructions)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "opus-source", "lame-source", "compiler", "compiler-archive",
                 "notices", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=4)
    args = parser.parse_args(argv)
    package = build_messaging_audio(
        args.source, args.opus_source, args.lame_source, args.compiler, args.compiler_archive,
        args.notices, args.output, jobs=args.jobs,
    )
    base = read_json(audio.PIN_PATH)
    archive = package.parent / "iris-native-messaging-audio.zip"
    digest = audio.package_decoder(package, archive, base["source"]["source_date_epoch"])
    print(f"messaging audio archive: {archive} sha256={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
