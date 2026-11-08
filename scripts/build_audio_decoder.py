"""Build pinned native-only LGPL audio FFmpeg using a workspace-private compiler.

No download, PATH discovery, global installation or developer venv is performed.
Inputs are verified before creating a new output; failed evidence is retained.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import struct
import subprocess
import tarfile
import time
import wave
import zipfile
from pathlib import Path

if __package__:
    from .distribution_assets import DistributionError, member_path
    from .runtime_inventory import _link_check, _regular, _root, read_json
else:
    from distribution_assets import DistributionError, member_path
    from runtime_inventory import _link_check, _regular, _root, read_json

PIN_PATH = Path(__file__).with_name("audio_build_sources.json")
FLAGS = [
    "--prefix=../package",
    "--target-os=mingw32",
    "--arch=x86_64",
    "--cc=gcc",
    "--cxx=g++",
    "--ln_s=cp -f",
    "--disable-autodetect",
    "--disable-everything",
    "--disable-network",
    "--disable-ffplay",
    "--disable-doc",
    "--disable-debug",
    "--disable-x86asm",
    "--disable-pthreads",
    "--enable-w32threads",
    "--enable-shared",
    "--disable-static",
    "--disable-gpl",
    "--disable-version3",
    "--disable-nonfree",
    "--enable-ffmpeg",
    "--enable-ffprobe",
    "--enable-avcodec",
    "--enable-avformat",
    "--enable-avfilter",
    "--enable-swresample",
    "--disable-avdevice",
    "--disable-swscale",
    "--enable-decoder=aac,aac_fixed,aac_latm,mp3,mp3float,mp3adu,mp3adufloat,mp3on4,mp3on4float,vorbis,opus,flac,pcm_s16le,pcm_s16be,pcm_s24le,pcm_s24be,pcm_s32le,pcm_s32be,pcm_f32le,pcm_f32be,pcm_f64le,pcm_f64be,pcm_u8,pcm_alaw,pcm_mulaw",
    "--enable-parser=aac,aac_latm,mpegaudio,vorbis,opus,flac",
    "--enable-demuxer=wav,mp3,ogg,mov,aac,flac,matroska",
    "--enable-muxer=wav,pcm_s16le,pcm_s16be,pcm_f32le,null",
    "--enable-encoder=pcm_s16le,pcm_s16be,pcm_s24le,pcm_f32le",
    "--enable-protocol=file,pipe",
    "--enable-filter=abuffer,abuffersink,aformat,anull,aresample,atrim,asetpts,volume",
    "--extra-ldflags=-static-libgcc -Wl,--no-insert-timestamp",
    "--extra-version=iris-audio-29e619e767",
]


def file_hash(path: Path) -> str:
    path, _ = _regular(Path(path).absolute().parent, Path(path).name)
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def tree_hash(root: Path) -> str:
    root = _root(root)
    files, directories, stack = [], [], [root]
    while stack:
        current = stack.pop()
        directories.append((current, current.lstat()))
        for path in current.iterdir():
            info = path.lstat()
            _link_check(path, info)
            name = path.relative_to(root).as_posix()
            member_path(name)
            if path.is_dir():
                stack.append(path)
            elif path.is_file():
                files.append((name, path, info))
            else:
                raise DistributionError(f"Non-regular compiler input: {name}")
    digest = hashlib.sha256()
    for name, path, before in sorted(files):
        # The complete tree was checked above. Compare opened file identities
        # before reading and directory identities afterwards, avoiding a second
        # full ancestor walk for each of thousands of compiler headers.
        def identity(info):
            return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns

        hashed = hashlib.sha256()
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        with os.fdopen(os.open(path, flags), "rb") as handle:
            opened = os.fstat(handle.fileno())
            _link_check(path, opened)
            if identity(opened) != identity(before):
                raise DistributionError(f"Compiler input changed before read: {name}")
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                hashed.update(block)
            after = path.lstat()
            _link_check(path, after)
            if identity(after) != identity(before) or identity(
                os.fstat(handle.fileno())
            ) != identity(before):
                raise DistributionError(f"Compiler input changed during read: {name}")
        digest.update(
            (name + "\0" + str(before.st_size) + "\0" + hashed.hexdigest() + "\n").encode()
        )
    for path, before in directories:
        after = path.lstat()
        _link_check(path, after)
        if (after.st_dev, after.st_ino, after.st_mtime_ns) != (
            before.st_dev,
            before.st_ino,
            before.st_mtime_ns,
        ):
            raise DistributionError("Compiler directory changed during inventory")
    return digest.hexdigest()


def validate_pin(pin: dict) -> None:
    if (
        not isinstance(pin, dict)
        or set(pin)
        != {
            "schema",
            "platform",
            "arch",
            "source",
            "toolchain",
            "notices",
            "configure",
        }
        or type(pin.get("schema")) is not int
        or pin["schema"] != 1
        or (pin["platform"] != "windows" or pin["arch"] != "x86_64" or pin["configure"] != FLAGS)
    ):
        raise DistributionError("Unknown or modified audio build contract")
    for component in ("source", "toolchain"):
        item = pin[component]
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("sha256"), str)
            or (
                len(item["sha256"]) != 64
                or any(c not in "0123456789abcdef" for c in item["sha256"])
            )
            or not isinstance(item.get("url"), str)
            or not item["url"].startswith("https://")
        ):
            raise DistributionError(f"Invalid pinned audio build source: {component}")
    if pin["source"].get("revision") != "29e619e767cde9045a75c29bc9a8278ae7b3a98b" or (
        type(pin["source"].get("source_date_epoch")) is not int
        or pin["source"]["source_date_epoch"] < 0
    ):
        raise DistributionError("Incorrect FFmpeg source revision or epoch")
    tree = pin["toolchain"].get("tree_sha256")
    if (
        not isinstance(tree, str)
        or len(tree) != 64
        or any(c not in "0123456789abcdef" for c in tree)
    ):
        raise DistributionError("Missing independently pinned compiler tree")
    if not isinstance(pin["notices"], list) or len(pin["notices"]) != 2:
        raise DistributionError("Missing GCC runtime notices")
    for item in pin["notices"]:
        if (
            not isinstance(item, dict)
            or set(item) != {"filename", "url", "sha256"}
            or (
                len(member_path(item["filename"]).parts) != 1
                or not isinstance(item["url"], str)
                or not item["url"].startswith("https://")
                or not isinstance(item["sha256"], str)
                or len(item["sha256"]) != 64
                or any(c not in "0123456789abcdef" for c in item["sha256"])
            )
        ):
            raise DistributionError("Invalid GCC runtime notice pin")


def preflight(source, compiler, compiler_archive, notices, output, pin):
    validate_pin(pin)
    output = Path(output).absolute()
    _root(output.parent)
    if output.exists() or output.is_symlink():
        raise DistributionError("Audio build output must be a new directory")
    source, compiler = Path(source).absolute(), _root(compiler)
    if output.is_relative_to(compiler) or compiler.is_relative_to(output):
        raise DistributionError("Compiler and output directories must be separate")
    inputs = [
        (source, pin["source"]["sha256"]),
        (Path(compiler_archive), pin["toolchain"]["sha256"]),
    ]
    inputs.extend((Path(notices) / item["filename"], item["sha256"]) for item in pin["notices"])
    for path, expected in inputs:
        if file_hash(path) != expected:
            raise DistributionError(f"Incorrect audio build input hash: {path.name}")
    if tree_hash(compiler) != pin["toolchain"]["tree_sha256"]:
        raise DistributionError("Compiler tree differs from approved portable toolchain")
    for name in ("sh.exe", "make.exe", "gcc.exe", "objdump.exe"):
        _regular(compiler, "bin/" + name)
    return source, compiler, output


def extract_source(archive: Path, destination: Path) -> None:
    if destination.exists():
        raise DistributionError("FFmpeg source output must be new")
    with tarfile.open(archive, "r:gz") as tar:
        members, roots, aliases, total = tar.getmembers(), set(), set(), 0
        for item in members:
            name = item.name.rstrip("/") if item.isdir() else item.name
            path = member_path(name)
            if name.casefold() in aliases or not (item.isfile() or item.isdir()):
                raise DistributionError("Linked or ambiguous FFmpeg source archive member")
            aliases.add(name.casefold())
            roots.add(path.parts[0])
            total += item.size
        if len(roots) != 1 or total > 256 * 1024 * 1024 or len(members) > 50000:
            raise DistributionError("Unexpected FFmpeg source archive layout or size")
        destination.mkdir()
        for item in members:
            parts = member_path(item.name.rstrip("/") if item.isdir() else item.name).parts[1:]
            target = destination.joinpath(*parts)
            if item.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(item) as incoming, target.open("xb") as outgoing:
                    shutil.copyfileobj(incoming, outgoing)


def build_environment(compiler: Path, work: Path, epoch: int) -> dict[str, str]:
    env = {
        key: value for key, value in os.environ.items() if key.upper() in {"SYSTEMROOT", "WINDIR"}
    }
    env.update(
        {
            "PATH": str(compiler / "bin"),
            "LC_ALL": "C",
            "LANG": "C",
            "HOME": str(work / "home"),
            "TEMP": str(work / "temp"),
            "TMP": str(work / "temp"),
            "SOURCE_DATE_EPOCH": str(epoch),
            "ZERO_AR_DATE": "1",
            # configure uses eval for temporary filenames. A relative POSIX
            # directory avoids Windows backslash/space interpolation there.
            "TMPDIR": "../temp",
        }
    )
    # MinGW windres invokes its C preprocessor via Windows _popen. Keep that
    # shell explicit while PATH stays limited to the verified private tools.
    system_root = env.get("SystemRoot") or env.get("SYSTEMROOT") or env.get("WINDIR")
    if system_root:
        env["COMSPEC"] = str(Path(system_root) / "System32/cmd.exe")
    return env


def run(command: list[str], cwd: Path, env: dict[str, str], log: Path) -> None:
    with log.open("ab") as handle:
        handle.write((json.dumps(command, ensure_ascii=False) + "\n").encode())
        handle.flush()
        result = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=handle,
            stderr=handle,
            timeout=1800,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    if result.returncode != 0:
        raise DistributionError(f"Audio build command failed ({result.returncode}); see {log}")


def pe_imports(path: Path) -> list[str]:
    data = path.read_bytes()
    if data[:2] != b"MZ" or len(data) < 64:
        raise DistributionError("Invalid audio PE file")
    pe = struct.unpack_from("<I", data, 60)[0]
    if pe + 24 > len(data) or data[pe : pe + 4] != b"PE\0\0":
        raise DistributionError("Invalid audio PE header")
    machine, sections = struct.unpack_from("<HH", data, pe + 4)
    optional_size, opt = struct.unpack_from("<H", data, pe + 20)[0], pe + 24
    if (
        machine != 0x8664
        or opt + optional_size > len(data)
        or optional_size < 128
        or struct.unpack_from("<H", data, opt)[0] != 0x20B
    ):
        raise DistributionError("Audio binary must be Windows x86_64 PE32+")
    import_rva, _ = struct.unpack_from("<II", data, opt + 120)
    ranges = []
    for index in range(sections):
        section = opt + optional_size + 40 * index
        if section + 40 > len(data):
            raise DistributionError("Invalid audio PE section")
        virtual_size, rva, raw_size, raw = struct.unpack_from("<IIII", data, section + 8)
        ranges.append((rva, max(virtual_size, raw_size), raw))

    def offset(rva):
        for start, size, raw in ranges:
            if start <= rva < start + size and raw + rva - start < len(data):
                return raw + rva - start
        raise DistributionError("Invalid audio PE import address")

    if not import_rva:
        return []
    imports, descriptor = [], offset(import_rva)
    for index in range(256):
        current = descriptor + 20 * index
        if current + 20 > len(data):
            raise DistributionError("Invalid audio PE import table")
        fields = struct.unpack_from("<IIIII", data, current)
        if not any(fields):
            return sorted(imports)
        name = offset(fields[3])
        end = data.find(b"\0", name, name + 256)
        if end < name:
            raise DistributionError("Invalid audio PE import name")
        imports.append(data[name:end].decode("ascii"))
    raise DistributionError("Oversized audio PE import table")


def build_decoder(source, compiler, compiler_archive, notices, output, *, pin=None, jobs=4) -> Path:
    pin = read_json(PIN_PATH) if pin is None else pin
    source, compiler, output = preflight(source, compiler, compiler_archive, notices, output, pin)
    if os.name != "nt" or type(jobs) is not int or not 1 <= jobs <= 16:
        raise DistributionError("Decoder build requires Windows and 1–16 workers")
    output.mkdir()
    for name in ("home", "temp", "logs"):
        (output / name).mkdir()
    build = output / "source"
    extract_source(source, build)
    env = build_environment(compiler, output, pin["source"]["source_date_epoch"])
    run(
        [str(compiler / "bin/sh.exe"), "./configure", *FLAGS],
        build,
        env,
        output / "logs/configure.log",
    )
    run([str(compiler / "bin/make.exe"), f"-j{jobs}", "V=1"], build, env, output / "logs/make.log")
    run([str(compiler / "bin/make.exe"), "install", "V=1"], build, env, output / "logs/install.log")
    return finish_build(source, compiler, notices, output, pin, env)


def finish_build(
    source, compiler, notices, output, pin, env, *, extra_components=(), build_instructions=None,
) -> Path:
    build = output / "source"
    package, binary = output / "package", output / "package/bin/ffmpeg.exe"
    smoke_env = dict(env, PATH=str(package / "bin"))
    buildconf = subprocess.check_output(
        [str(binary), "-buildconf"],
        env=smoke_env,
        cwd=package,
        timeout=30,
        stderr=subprocess.STDOUT,
    )
    license_text = subprocess.check_output(
        [str(binary), "-L"], env=smoke_env, cwd=package, timeout=30, stderr=subprocess.STDOUT
    )
    validate_license_output(license_text, buildconf)
    (output / "logs/buildconf.txt").write_bytes(buildconf)
    (output / "logs/license.txt").write_bytes(license_text)
    with wave.open(str(output / "audio-smoke.wav"), "wb") as wav:
        wav.setparams((1, 2, 16000, 1600, "NONE", "not compressed"))
        wav.writeframes(b"\0\0" * 1600)
    decoded = subprocess.check_output(
        [
            str(binary),
            "-v",
            "error",
            "-nostdin",
            "-i",
            str(output / "audio-smoke.wav"),
            "-f",
            "s16le",
            "-acodec",
            "pcm_s16le",
            "-",
        ],
        cwd=package,
        env=smoke_env,
        timeout=30,
    )
    if decoded != b"\0\0" * 1600:
        raise DistributionError("Private decoder WAV smoke failed")
    subprocess.check_output(
        [
            str(package / "bin/ffprobe.exe"),
            "-v",
            "error",
            "-show_format",
            str(output / "audio-smoke.wav"),
        ],
        cwd=package,
        env=smoke_env,
        timeout=30,
    )
    licenses = package / "licenses/audio"
    licenses.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(build / "COPYING.LGPLv2.1", licenses / "FFmpeg-COPYING.LGPLv2.1")
    shutil.copyfile(compiler / "COPYING.MinGW-w64-runtime.txt", licenses / "MinGW-w64-runtime.txt")
    for item in pin["notices"]:
        shutil.copyfile(Path(notices) / item["filename"], licenses / item["filename"])
    (licenses / "FFmpeg-buildconf.txt").write_bytes(buildconf)
    (licenses / "FFmpeg-license-output.txt").write_bytes(license_text)
    sources = licenses / "sources"
    sources.mkdir(exist_ok=True)
    shutil.copyfile(source, sources / "ffmpeg-source.tar.gz")
    (sources / "audio-build-sources.json").write_text(json.dumps(pin, indent=2) + "\n", "utf-8")
    (sources / "BUILD.md").write_text(
        build_instructions if build_instructions is not None else "# Iris native audio decoder\n\n"
        "The bundled source archive is unmodified FFmpeg commit "
        + pin["source"]["revision"]
        + ".\n"
        "There are no third-party codec libraries, GPL features, version3 features "
        "or network protocols.\n"
        "Build in the extracted source directory using the pinned portable w64devkit compiler. "
        "Set PATH only to its bin directory, COMSPEC to the Windows System32/cmd.exe, "
        "TMPDIR to ../temp, SOURCE_DATE_EPOCH to "
        + str(pin["source"]["source_date_epoch"])
        + " and ZERO_AR_DATE=1. Create ../temp first.\n\n"
        "The exact shell arguments are recorded in audio-build-sources.json (configure). "
        "Run sh ./configure with those arguments, then make -j4 V=1 and make install V=1. "
        "The result is in ../package.\n\n"
        "FFmpeg is LGPL-2.1-or-later. MinGW startup/runtime notices and GCC runtime exception "
        "are included in the parent directory. These shared DLLs may be replaced with "
        "compatible rebuilt copies; Iris invokes ffmpeg/ffprobe as separate processes. "
        "The compiler itself is a build input, not bundled "
        "with Iris. Its official binary/source archive URLs and hashes are recorded in the pin.\n",
        "utf-8",
    )
    components = [
        {
            "component": "audio",
            "package": "FFmpeg",
            "version": pin["source"]["version"],
            "license": "LGPL-2.1-or-later",
            "files": [
                "licenses/audio/FFmpeg-COPYING.LGPLv2.1",
                "licenses/audio/FFmpeg-buildconf.txt",
                "licenses/audio/FFmpeg-license-output.txt",
                "licenses/audio/sources/ffmpeg-source.tar.gz",
                "licenses/audio/sources/BUILD.md",
                "licenses/audio/sources/audio-build-sources.json",
            ],
            "source": pin["source"]["url"],
        },
        {
            "component": "audio",
            "package": "GCC runtime",
            "version": pin["toolchain"]["gcc"],
            "license": "GPL-3.0-or-later WITH GCC-exception-3.1",
            "files": ["licenses/audio/" + item["filename"] for item in pin["notices"]],
            "source": pin["toolchain"]["source_url"],
        },
        {
            "component": "audio",
            "package": "MinGW-w64 runtime",
            "version": pin["toolchain"]["mingw"],
            "license": "MinGW-w64 runtime notices (permissive and LGPL-2.1-or-later portions)",
            "files": ["licenses/audio/MinGW-w64-runtime.txt"],
            "source": pin["toolchain"]["source_url"],
        },
    ]
    components.extend(extra_components)
    (licenses / "components.json").write_text(
        json.dumps({"schema": 1, "licenses": components}, indent=2) + "\n", "utf-8"
    )
    imports = {
        path.name: pe_imports(path)
        for path in sorted((package / "bin").iterdir())
        if path.suffix.lower() in {".exe", ".dll"}
    }
    local = {name.casefold() for name in imports}
    system = {
        "kernel32.dll",
        "msvcrt.dll",
        "bcrypt.dll",
        "advapi32.dll",
        "user32.dll",
        "shell32.dll",
        "ole32.dll",
        "ws2_32.dll",
        "secur32.dll",
        "shlwapi.dll",
    }
    if any(dep.casefold() not in local | system for deps in imports.values() for dep in deps):
        raise DistributionError(f"Audio decoder imports unapproved non-system DLL: {imports}")
    result = {
        "schema": 1,
        "component": "iris-native-audio",
        "source_pin": pin,
        "imports": imports,
        "wav_smoke": True,
        "files": [
            {
                "path": path.relative_to(package).as_posix(),
                "size": path.stat().st_size,
                "sha256": file_hash(path),
            }
            for directory in (package / "bin", licenses)
            for path in sorted(directory.rglob("*"))
            if path.is_file() and (directory == licenses or path.suffix.lower() in {".exe", ".dll"})
        ],
    }
    (licenses / "audio-build.json").write_text(json.dumps(result, indent=2) + "\n", "utf-8")
    return package


def validate_license_output(license_text: bytes, buildconf: bytes) -> None:
    # The upstream -L output wraps its license name across lines on Windows.
    words = b" ".join(license_text.split())
    if not (b"GNU Lesser General Public License" in words and b"version 2.1" in words) or any(
        flag in buildconf for flag in (b"--enable-gpl", b"--enable-version3", b"--enable-nonfree")
    ):
        raise DistributionError("Built decoder does not satisfy native LGPL2.1 policy")


def package_decoder(package: Path, archive: Path, epoch: int) -> str:
    """Create only the redistributable layout, with fixed ZIP timestamps."""
    package = _root(package)
    archive = Path(archive).absolute()
    _root(archive.parent)
    if archive.exists():
        raise DistributionError("Audio package archive must be a new file")
    inputs = []
    for directory in ("bin", "licenses/audio"):
        for path in sorted((package / directory).rglob("*")):
            if path.is_file():
                if directory == "bin" and path.suffix.lower() not in {".exe", ".dll"}:
                    continue
                relative = path.relative_to(package).as_posix()
                _regular(package, relative)
                inputs.append((relative, path))
    if not {"bin/ffmpeg.exe", "bin/ffprobe.exe", "licenses/audio/components.json"} <= {
        name for name, _ in inputs
    }:
        raise DistributionError("Missing native audio redistribution files")
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as handle:
        for relative, path in inputs:
            info = zipfile.ZipInfo(relative, time.gmtime(epoch)[:6])
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            with path.open("rb") as source, handle.open(info, "w") as target:
                shutil.copyfileobj(source, target)
    return file_hash(archive)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "compiler", "compiler-archive", "notices", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=4)
    args = parser.parse_args(argv)
    package = build_decoder(
        args.source, args.compiler, args.compiler_archive, args.notices, args.output, jobs=args.jobs
    )
    pin = read_json(PIN_PATH)
    archive = package.parent / "iris-native-audio.zip"
    digest = package_decoder(package, archive, pin["source"]["source_date_epoch"])
    print(f"native audio decoder built: {package}")
    print(f"archive: {archive} sha256={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
