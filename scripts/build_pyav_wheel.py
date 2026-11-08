"""Build PyAV 18 against the private seven-library LGPL FFmpeg SDK.

Inputs are pinned public archives. No global packages, compiler discovery, network
resolution or venv copy is performed. The installed MSVC toolchain is selected
explicitly and fingerprinted; all mutable files stay in a fresh workspace output.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import tarfile
import zipfile
from pathlib import Path

if __package__:
    from .build_audio_decoder import file_hash, pe_imports, tree_hash
    from .distribution_assets import DistributionError, member_path
    from .runtime_inventory import _regular, _root, read_json
else:
    from build_audio_decoder import file_hash, pe_imports, tree_hash
    from distribution_assets import DistributionError, member_path
    from runtime_inventory import _regular, _root, read_json

PIN_PATH = Path(__file__).with_name("pyav_build_sources.json")
# The version Hermes' lock pins (0.21.6). Its stable-ABI cp311 wheel also loads on CPython 3.14.
PYAV_VERSION = "18.1.0"
LIBRARIES = ["avcodec", "avdevice", "avfilter", "avformat", "avutil", "swresample", "swscale"]
TOOL_VERSIONS = {
    "Cython": "3.2.4",
    "setuptools": "81.0.0",
    "wheel": "0.46.3",
    "build": "1.5.0",
    "delvewheel": "1.12.0",
    "packaging": "26.2",
    "pyproject-hooks": "1.2.0",
    "pefile": "2024.8.26",
    "colorama": "0.4.6",
}
SYSTEM_DLLS = {
    "kernel32.dll",
    "msvcrt.dll",
    "bcrypt.dll",
    "advapi32.dll",
    "user32.dll",
    "shell32.dll",
    "ole32.dll",
    "oleaut32.dll",
    "ws2_32.dll",
    "secur32.dll",
    "shlwapi.dll",
    "gdi32.dll",
    "comdlg32.dll",
    "ntdll.dll",
    "vcruntime140.dll",
    "vcruntime140_1.dll",
    "python3.dll",
}
EPOCH = 1790713770
SDK_BIN = "bin/10.0.26100.0/x64"


def validate_pin(pin: dict) -> None:
    expected = {
        "schema",
        "platform",
        "arch",
        "source",
        "python",
        "tools",
        "source_date_epoch",
        "ffmpeg_libraries",
        "msvc",
        "repair",
    }
    if (
        not isinstance(pin, dict)
        or set(pin) != expected
        or type(pin["schema"]) is not int
        or pin["schema"] != 1
        or pin["platform"] != "windows"
        or pin["arch"] != "x86_64"
        or pin["ffmpeg_libraries"] != LIBRARIES
        or pin["source_date_epoch"] != EPOCH
        or pin["source"]["name"] != "av"
        or pin["source"]["version"] != PYAV_VERSION
        or pin["python"]["version"] != "3.11.16"
        or pin["msvc"]
        != {
            "version": "14.50.35717",
            "sdk_version": "10.0.26100.0",
            "compile_flags": ["/Brepro"],
            "link_flags": ["/Brepro", "/DEBUG:NONE"],
        }
        or pin["repair"]
        != {"tool": "delvewheel", "version": "1.12.0", "mangle": True, "private_sdk_only": True}
    ):
        raise DistributionError("Unknown or modified PyAV build contract")
    tools = pin["tools"]
    if (
        not isinstance(tools, list)
        or len(tools) != len(TOOL_VERSIONS)
        or {i["name"]: i["version"] for i in tools} != TOOL_VERSIONS
    ):
        raise DistributionError("Unknown PyAV build tools")
    for item in [pin["source"], pin["python"], *tools]:
        if (
            not re.fullmatch("[0-9a-f]{64}", item["sha256"])
            or len(member_path(item["filename"]).parts) != 1
            or not item["url"].startswith("https://")
        ):
            raise DistributionError("Invalid PyAV input pin")
        if item is not pin["python"] and not item["url"].startswith(
            "https://files.pythonhosted.org/packages/"
        ):
            raise DistributionError("PyAV tools must use official PyPI archives")


def verify_input(path: Path, expected: str) -> Path:
    path = Path(path).absolute()
    if file_hash(path) != expected:
        raise DistributionError("PyAV input hash mismatch: " + path.name)
    return path


def check_new_output(output: Path, inputs: list[Path]) -> Path:
    output = Path(output).absolute()
    if output.exists():
        raise DistributionError("PyAV output must be a new directory")
    _root(output.parent)
    for item in inputs:
        item = Path(item).absolute()
        if output == item or output in item.parents or item in output.parents:
            raise DistributionError("PyAV output must be separate from all inputs")
    return output


def extract_tar(archive: Path, output: Path, prefix: str) -> None:
    """Validate the entire tar before creating any destination."""
    if output.exists():
        raise DistributionError("Archive output must be new")
    with tarfile.open(archive, "r:gz") as incoming:
        members, names, total = incoming.getmembers(), set(), 0
        for item in members:
            name = item.name.rstrip("/") if item.isdir() else item.name
            parts = member_path(name).parts
            if (
                parts[0] != prefix
                or name.casefold() in names
                or not (item.isdir() or item.isfile())
            ):
                raise DistributionError("Linked or unexpected PyAV archive member")
            names.add(name.casefold())
            total += item.size
        if total > 512 * 1024 * 1024 or len(members) > 60000:
            raise DistributionError("Oversized PyAV build archive")
        output.mkdir()
        for item in members:
            name = item.name.rstrip("/") if item.isdir() else item.name
            parts = member_path(name).parts[1:]
            # The standalone archive includes unrelated pip/setuptools versions.
            # Build tools come only from our separately pinned fresh wheels.
            if prefix == "python" and parts[:2] == ("Lib", "site-packages"):
                continue
            target = output.joinpath(*parts)
            if item.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with incoming.extractfile(item) as src, target.open("xb") as dst:
                    shutil.copyfileobj(src, dst)


def wheel_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    seen = set()
    members = archive.infolist()
    if sum(i.file_size for i in members) > 512 * 1024 * 1024:
        raise DistributionError("Oversized PyAV wheel")
    for item in members:
        name = item.filename.rstrip("/") if item.is_dir() else item.filename
        member_path(name)
        mode = (item.external_attr >> 16) & 0o170000
        if name.casefold() in seen or mode not in (0, stat.S_IFREG, stat.S_IFDIR):
            raise DistributionError("Linked or ambiguous PyAV wheel member")
        seen.add(name.casefold())
    return members


def install_tool(wheel: Path, site: Path) -> None:
    """Install verified purelib/platlib wheels without pip or dependency resolution."""
    with zipfile.ZipFile(wheel) as archive:
        members = wheel_members(archive)
        records = [
            i.filename
            for i in members
            if i.filename.endswith(".dist-info/RECORD") and i.filename.count("/") == 1
        ]
        if len(records) != 1:
            raise DistributionError("Build tool wheel requires one RECORD")
        rows = list(csv.reader(io.StringIO(archive.read(records[0]).decode())))
        indexed = {i.filename: i for i in members if not i.is_dir()}
        if {r[0] for r in rows} != set(indexed):
            raise DistributionError("Build tool RECORD inventory mismatch")
        for name, digest, length in rows:
            data = archive.read(name)
            if digest:
                actual = (
                    base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
                )
                if digest != "sha256=" + actual or int(length) != len(data):
                    raise DistributionError("Build tool RECORD hash mismatch")
            elif name != records[0]:
                raise DistributionError("Unhashed build tool member")
        for item in members:
            if item.is_dir():
                continue
            parts = member_path(item.filename).parts
            if parts[0].endswith(".data"):
                if parts[1] not in {"purelib", "platlib"}:
                    # Entry point scripts are not needed: invoke modules explicitly.
                    continue
                parts = parts[2:]
            destination = site.joinpath(*parts)
            if destination.exists():
                raise DistributionError("Build tools cannot overwrite existing files")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(archive.read(item))


def build_environment(msvc: Path, sdk: Path, python: Path, output: Path) -> dict[str, str]:
    """Use only explicit public compiler inputs and workspace-local mutable state."""
    system = os.environ.get("SystemRoot") or os.environ.get("SYSTEMROOT") or "C:/Windows"
    sdk_inc = sdk / "Include/10.0.26100.0"
    sdk_lib = sdk / "Lib/10.0.26100.0"
    return {
        "SystemRoot": system,
        "WINDIR": system,
        "COMSPEC": str(Path(system) / "System32/cmd.exe"),
        # link.exe embeds manifests through the SDK resource compiler (rc.exe/mt.exe).
        "PATH": os.pathsep.join((str(msvc / "bin/Hostx64/x64"), str(sdk / SDK_BIN))),
        "INCLUDE": ";".join(
            str(p)
            for p in [
                python / "include",
                msvc / "include",
                sdk_inc / "ucrt",
                sdk_inc / "shared",
                sdk_inc / "um",
                sdk_inc / "winrt",
                sdk_inc / "cppwinrt",
            ]
        ),
        "LIB": ";".join(
            str(p)
            for p in [python / "libs", msvc / "lib/x64", sdk_lib / "ucrt/x64", sdk_lib / "um/x64"]
        ),
        "DISTUTILS_USE_SDK": "1",
        "MSSdk": "1",
        "VSCMD_ARG_TGT_ARCH": "x64",
        "CL": "/Brepro",
        "LINK": "/Brepro /DEBUG:NONE",
        "SOURCE_DATE_EPOCH": str(EPOCH),
        "PYTHONHASHSEED": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONSAFEPATH": "1",
        "PYTHONUTF8": "1",
        "HOME": str(output / "home"),
        "USERPROFILE": str(output / "home"),
        "APPDATA": str(output / "home/AppData/Roaming"),
        "LOCALAPPDATA": str(output / "home/AppData/Local"),
        "TEMP": str(output / "temp"),
        "TMP": str(output / "temp"),
    }


def run(command: list[str], cwd: Path, env: dict[str, str], log: Path) -> None:
    with log.open("ab") as stream:
        stream.write((json.dumps(command) + "\n").encode())
        stream.flush()
        result = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=stream,
            stderr=stream,
            timeout=1800,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    if result.returncode:
        raise DistributionError(f"PyAV command failed ({result.returncode}); see {log}")


def compiler_receipt(msvc: Path, sdk: Path, env: dict, output: Path) -> dict:
    receipt = {
        "schema": 1,
        "msvc_version": "14.50.35717",
        "sdk_version": "10.0.26100.0",
        "binaries": [],
        "trees": [],
    }
    for name in ("cl.exe", "lib.exe", "link.exe", "c1.dll", "c2.dll", "cvtres.exe"):
        path, _ = _regular(msvc, "bin/Hostx64/x64/" + name)
        receipt["binaries"].append({"public_path": str(path), "sha256": file_hash(path)})
        if name.endswith(".exe"):
            result = subprocess.run(
                [str(path)],
                env=env,
                cwd=output,
                timeout=30,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            receipt["binaries"][-1]["version_output"] = result.stdout.decode("utf-8", "replace")
    for root in (
        msvc / "include",
        msvc / "lib/x64",
        sdk / "Include/10.0.26100.0",
        sdk / "Lib/10.0.26100.0/ucrt/x64",
        sdk / "Lib/10.0.26100.0/um/x64",
    ):
        receipt["trees"].append({"public_path": str(root), "tree_sha256": tree_hash(root)})
    for name in ("rc.exe", "rcdll.dll", "mt.exe"):
        path, _ = _regular(sdk, SDK_BIN + "/" + name)
        receipt["binaries"].append({"public_path": str(path), "sha256": file_hash(path)})
    return receipt


def normalized_def(data: str, dll: str) -> str:
    if not re.fullmatch(
        r"(?:avcodec|avdevice|avfilter|avformat|avutil|swscale|swresample)-[0-9]+\.dll", dll
    ):
        raise DistributionError("Unexpected FFmpeg DLL basename")
    if not re.search(r"(?m)^EXPORTS\s*$", data):
        raise DistributionError("FFmpeg .def lacks EXPORTS")
    data = re.sub(r"(?m)^LIBRARY[^\r\n]*\r?\n?", "", data)
    return "LIBRARY " + dll + "\n" + data.replace("\r\n", "\n")


def copy_sdk(ffmpeg: Path, target: Path, msvc: Path, env: dict, logs: Path) -> dict:
    _root(ffmpeg)
    dlls = sorted((ffmpeg / "bin").glob("*.dll"))
    matched = {}
    for path in dlls:
        if not re.fullmatch(r"(?:" + "|".join(LIBRARIES) + r")-[0-9]+\.dll", path.name):
            raise DistributionError("Private FFmpeg SDK contains an external DLL")
        matched[path.name.split("-")[0]] = path
    if len(dlls) != 7 or set(matched) != set(LIBRARIES):
        raise DistributionError("PyAV requires exactly seven FFmpeg DLLs")
    for root in ("include", "lib", "licenses"):
        _root(ffmpeg / root)
    target.mkdir()
    (target / "bin").mkdir()
    (target / "lib").mkdir()
    # Inventories reject symlinks, reparse points and hardlinks before copying.
    sdk_tree = tree_hash(ffmpeg)
    shutil.copytree(ffmpeg / "include", target / "include")
    shutil.copytree(ffmpeg / "licenses", target / "licenses")
    manifest = {}
    for name in LIBRARIES:
        source = matched[name]
        shutil.copyfile(source, target / "bin" / source.name)
        definitions = list((ffmpeg / "lib").glob(name + "*.def"))
        if len(definitions) != 1:
            raise DistributionError("SDK requires one .def per FFmpeg library")
        definition = target / "lib" / (name + ".def")
        definition.write_text(
            normalized_def(definitions[0].read_text("utf-8"), source.name), "utf-8"
        )
        library = target / "lib" / (name + ".lib")
        run(
            [
                str(msvc / "bin/Hostx64/x64/lib.exe"),
                "/nologo",
                "/Brepro",
                "/MACHINE:X64",
                "/DEF:" + str(definition),
                "/OUT:" + str(library),
            ],
            target,
            env,
            logs / "import-libs.log",
        )
        manifest[name] = {
            "dll": source.name,
            "dll_sha256": file_hash(source),
            "def_sha256": file_hash(definition),
            "lib_sha256": file_hash(library),
        }
    if tree_hash(ffmpeg) != sdk_tree:
        raise DistributionError("FFmpeg SDK changed during copy")
    return {"sdk_tree_sha256": sdk_tree, "libraries": manifest}


def verify_native_members(wheel: Path, output: Path) -> dict:
    native = output / "native-inspect"
    native.mkdir()
    imports = {}
    with zipfile.ZipFile(wheel) as archive:
        members = wheel_members(archive)
        dll_names = {
            Path(i.filename).name.casefold() for i in members if i.filename.lower().endswith(".dll")
        }
        ffmpeg = [n for n in dll_names if any(n.startswith(i + "-") for i in LIBRARIES)]
        if len(dll_names) != 7 or len(ffmpeg) != 7:
            raise DistributionError("Repaired wheel must bundle seven native FFmpeg DLLs only")
        for item in members:
            if item.filename.lower().endswith((".dll", ".pyd")):
                destination = native / Path(item.filename).name
                destination.write_bytes(archive.read(item))
                imports[item.filename] = pe_imports(destination)
        allowed = dll_names | SYSTEM_DLLS
        for deps in imports.values():
            for dep in deps:
                lower = dep.casefold()
                if lower not in allowed and not lower.startswith("api-ms-win-crt-"):
                    raise DistributionError("Unapproved PyAV PE dependency: " + dep)
        if any("x264" in n or "x265" in n for n in dll_names):
            raise DistributionError("External GPL codec DLL in PyAV wheel")
    return imports


def add_notices(
    wheel: Path, sdk: Path, source: Path, pin: dict, output: Path, provenance: dict
) -> Path:
    """Put corresponding full sources and regeneration instructions in the wheel."""
    extras = {
        "av/licenses/PyAV-LICENSE.txt": (source / "LICENSE.txt").read_bytes(),
        f"av/licenses/sources/av-{PYAV_VERSION}.tar.gz": verify_input(
            output / "inputs" / pin["source"]["filename"], pin["source"]["sha256"]
        ).read_bytes(),
        "av/licenses/sources/pyav-build-sources.json": (json.dumps(pin, indent=2) + "\n").encode(),
        "av/licenses/sources/build_pyav_wheel.py": Path(__file__).read_bytes(),
        "av/licenses/sources/pyav-build-receipt.json": (
            json.dumps(provenance, indent=2) + "\n"
        ).encode(),
    }
    for path in sorted((sdk / "licenses").rglob("*")):
        if path.is_file():
            extras["av/licenses/ffmpeg/" + path.relative_to(sdk / "licenses").as_posix()] = (
                path.read_bytes()
            )
    ffmpeg_names = set(extras)
    if (
        not any(n.endswith("ffmpeg-source.tar.gz") for n in ffmpeg_names)
        or not any("LGPLv2.1" in n for n in ffmpeg_names)
        or not any(n.endswith("COPYING.RUNTIME") for n in ffmpeg_names)
        or not any("MinGW" in n for n in ffmpeg_names)
    ):
        raise DistributionError("FFmpeg corresponding source or required runtime notices missing")
    extras["av/licenses/sources/BUILD.md"] = (
        f"# PyAV {PYAV_VERSION} private LGPL FFmpeg wheel\n\n".encode()
        + b"The exact unmodified PyAV sdist and FFmpeg source are included alongside pins. "
        b"Run build_pyav_wheel.py --inputs INPUTS --python-archive PYTHON_ARCHIVE "
        b"--ffmpeg-sdk FFMPEG_SDK --msvc MSVC_ROOT --windows-sdk SDK_ROOT --output NEW_OUTPUT. "
        b"Inputs are the pinned PyPI wheels and sdist. Build FFmpeg first using its included "
        b"source/pins/build instructions. Use the public MSVC and Windows SDK versions and hashes "
        b"recorded in pyav-build-receipt.json. A fresh CPython is extracted from the archive; "
        b"no global install or dependency resolution occurs. The script normalizes LIBRARY lines "
        b"in copies of exported .def files, runs lib.exe /MACHINE:X64 /Brepro, setup.py "
        b"--ffmpeg-dir=<private mirror> build_ext bdist_wheel, and delvewheel repair --add-path "
        b"<private mirror/bin>. /Brepro /DEBUG:NONE and SOURCE_DATE_EPOCH=1790713770 "
        b"are applied.\n\n"
        b"The LGPL FFmpeg libraries remain separately replaceable shared libraries. To replace "
        b"them or modify FFmpeg, rebuild the SDK and this binding, then rerun delvewheel repair "
        b"so import names and private loader match. No x264/x265 or other external codec libraries "
        b"are bundled. PyAV is BSD-3-Clause; FFmpeg LGPL-2.1-or-later; MinGW and GCC runtime "
        b"notices and GCC exception are included under av/licenses/ffmpeg. "
        b"The compiler and build tools are build inputs and are not redistributed in this wheel.\n"
    )
    final = output / "dist" / wheel.name
    final.parent.mkdir()
    stamp = (2026, 9, 29, 20, 29, 30)
    with zipfile.ZipFile(wheel) as incoming:
        members = wheel_members(incoming)
        record = next(i.filename for i in members if i.filename.endswith(".dist-info/RECORD"))
        payload = {
            i.filename: incoming.read(i) for i in members if not i.is_dir() and i.filename != record
        }
        if set(payload) & set(extras):
            raise DistributionError("Notice paths collide with wheel")
        payload.update(extras)
        rows = []
        for name, data in sorted(payload.items()):
            encoded = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
            rows.append([name, "sha256=" + encoded, str(len(data))])
        rows.append([record, "", ""])
        out = io.StringIO(newline="")
        csv.writer(out, lineterminator="\n").writerows(rows)
        payload[record] = out.getvalue().encode()
        with zipfile.ZipFile(
            final, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as package:
            for name, data in sorted(payload.items()):
                item = zipfile.ZipInfo(name, stamp)
                item.compress_type = zipfile.ZIP_DEFLATED
                item.external_attr = (stat.S_IFREG | 0o644) << 16
                package.writestr(item, data)
    return final


def build_wheel(
    inputs: Path,
    python_archive: Path,
    ffmpeg: Path,
    msvc: Path,
    windows_sdk: Path,
    output: Path,
    *,
    pin: dict | None = None,
) -> Path:
    pin = read_json(PIN_PATH) if pin is None else pin
    validate_pin(pin)
    inputs, ffmpeg, msvc, windows_sdk = map(_root, (inputs, ffmpeg, msvc, windows_sdk))
    python_archive = verify_input(python_archive, pin["python"]["sha256"])
    for item in [pin["source"], *pin["tools"]]:
        verify_input(inputs / item["filename"], item["sha256"])
    for name in ("cl.exe", "lib.exe", "link.exe"):
        _regular(msvc, "bin/Hostx64/x64/" + name)
    for name in ("rc.exe", "rcdll.dll", "mt.exe"):
        _regular(windows_sdk, SDK_BIN + "/" + name)
    if msvc.name != pin["msvc"]["version"]:
        raise DistributionError("MSVC version differs from build pin")
    output = check_new_output(output, [inputs, python_archive, ffmpeg, msvc, windows_sdk])
    if os.name != "nt":
        raise DistributionError("PyAV wheel build requires Windows")
    output.mkdir()
    for name in ("home", "temp", "logs", "inputs"):
        (output / name).mkdir()
    for item in [pin["source"], *pin["tools"]]:
        shutil.copyfile(inputs / item["filename"], output / "inputs" / item["filename"])
        verify_input(output / "inputs" / item["filename"], item["sha256"])
    python = output / "python"
    extract_tar(python_archive, python, "python")
    source = output / "source"
    extract_tar(inputs / pin["source"]["filename"], source, f"av-{PYAV_VERSION}")
    for name in ("include/Python.h", "libs/python3.lib", "libs/python311.lib"):
        _regular(python, name)
    site = python / "Lib/site-packages"
    for item in pin["tools"]:
        install_tool(output / "inputs" / item["filename"], site)
    env = build_environment(msvc, windows_sdk, python, output)
    receipt = compiler_receipt(msvc, windows_sdk, env, output)
    receipt["python_archive_sha256"] = pin["python"]["sha256"]
    receipt["build_pin"] = pin
    sdk = output / "sdk"
    receipt["ffmpeg"] = copy_sdk(ffmpeg, sdk, msvc, env, output / "logs")
    (output / "build-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", "utf-8")
    executable = str(python / "python.exe")
    # Use the exact upstream setup API. -I suppresses global environment/user site.
    run(
        [
            executable,
            "-I",
            "-B",
            "setup.py",
            "--ffmpeg-dir=" + str(sdk),
            "build_ext",
            "bdist_wheel",
            "--dist-dir=" + str(output / "raw"),
        ],
        source,
        env,
        output / "logs/build.log",
    )
    raw = list((output / "raw").glob("*.whl"))
    if len(raw) != 1 or raw[0].name != f"av-{PYAV_VERSION}-cp311-abi3-win_amd64.whl":
        raise DistributionError("Unexpected upstream PyAV wheel tag")
    repair_env = dict(env, PATH=str(Path(env["SystemRoot"]) / "System32"))
    run(
        [
            executable,
            "-I",
            "-B",
            "-m",
            "delvewheel",
            "repair",
            "--add-path",
            str(sdk / "bin"),
            "--wheel-dir",
            str(output / "repaired"),
            str(raw[0]),
        ],
        output,
        repair_env,
        output / "logs/repair.log",
    )
    repaired = list((output / "repaired").glob("*.whl"))
    if len(repaired) != 1:
        raise DistributionError("PyAV repair did not produce one wheel")
    receipt["raw_wheel_sha256"] = file_hash(raw[0])
    receipt["repaired_wheel_sha256"] = file_hash(repaired[0])
    receipt["pe_imports"] = verify_native_members(repaired[0], output)
    final = add_notices(repaired[0], sdk, source, pin, output, receipt)
    receipt["final_wheel"] = {
        "filename": final.name,
        "sha256": file_hash(final),
        "size": final.stat().st_size,
    }
    (output / "build-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", "utf-8")
    return final


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("inputs", "python-archive", "ffmpeg-sdk", "msvc", "windows-sdk", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args(argv)
    wheel = build_wheel(
        args.inputs, args.python_archive, args.ffmpeg_sdk, args.msvc, args.windows_sdk, args.output
    )
    print(json.dumps({"wheel": str(wheel), "sha256": file_hash(wheel)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
