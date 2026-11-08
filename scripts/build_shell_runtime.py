"""Assemble a pinned private Bash and a separate corresponding-source archive.

No installer, Git registration, upstream patch or execution of downloaded code.
Publish iris-private-shell-sources.zip alongside an installer containing this shell.
"""
from __future__ import annotations

import argparse
import io
import json
import tarfile
import zipfile
from pathlib import Path

from scripts.prepare_private_runtime import (archive_bytes, cached_archive, file_hash,
    new_directory, regular_file, runtime_path)
from scripts.build_audio_decoder import pe_imports

ROOT = Path(__file__).resolve().parents[1]
SYSTEM_DLLS = {"advapi32.dll", "kernel32.dll", "shell32.dll", "user32.dll", "userenv.dll", "ntdll.dll"}


def write_zip(path: Path, files: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in sorted(files.items()):
            runtime_path(name)
            entry = zipfile.ZipInfo(name, (2026, 10, 23, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o100644 << 16
            archive.writestr(entry, payload)


def build(output: Path, cache: Path, sources_cache: Path, pin: dict, *, offline: bool) -> dict:
    import hashlib
    selected = pin["files"]
    if not isinstance(selected, dict) or not {"usr/bin/bash.exe", "usr/bin/sh.exe", "usr/bin/msys-2.0.dll", "etc/fstab"} <= selected.keys():
        raise ValueError("Private shell selection is incomplete")
    for name in selected:
        runtime_path(name)
        if not (name in {"etc/fstab", "etc/nsswitch.conf"} or
                (name.startswith("usr/bin/") and len(name.split("/")) == 3 and name.endswith((".exe", ".dll")))):
            raise ValueError("Unapproved private shell file")
    binary = archive_bytes(cached_archive(cache, pin["archive"], offline=offline), pin["archive"]["sha256"])
    payloads, total = {}, 0
    with tarfile.open(fileobj=io.BytesIO(binary)) as archive:
        for member in archive:
            total += member.size
            if total > 2 * 1024**3:
                raise ValueError("Shell archive exceeds its expanded size limit")
            if member.name not in selected:
                continue
            if not member.isfile() or member.name in payloads or member.size > 32 * 1024**2:
                raise ValueError("Ambiguous or linked shell file")
            data = archive.extractfile(member).read()
            if hashlib.sha256(data).hexdigest() != selected[member.name]:
                raise ValueError("Shell member hash mismatch")
            payloads[member.name] = data
    if payloads.keys() != selected.keys():
        raise ValueError("Shell archive is missing selected files")
    sources, notices, licenses = {}, {}, []
    for item in pin["sources"]:
        source = cached_archive(sources_cache, item, offline=offline)
        sources["sources/" + item["filename"]] = regular_file(source, item["sha256"]).read_bytes()
        names = []
        for name, digest in item["notices"].items():
            if "/" in runtime_path(name):
                raise ValueError("Shell notice needs a basename")
            path = "licenses/shell/" + name
            notices[path] = regular_file(ROOT / "scripts/notices/shell" / name, digest).read_bytes()
            names.append(path)
        licenses.append({"component": "shell", "package": item["package"], "version": item["version"],
                         "license": item["license"], "source": item["url"], "files": sorted(names)})
    stage = new_directory(output)
    # Check the actual selected PE dependency closure before sealing either ZIP.
    binary_root = stage / "verification"
    for name, payload in payloads.items():
        target = binary_root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    for name in selected:
        if name.endswith((".exe", ".dll")):
            for dependency in pe_imports(binary_root / name):
                if dependency.lower() not in SYSTEM_DLLS and "usr/bin/" + dependency.lower() not in selected:
                    raise ValueError("Private shell has an unbundled DLL dependency")
    notice = ("Private Bash selected without modification from Git for Windows " + pin["version"] +
              ".\nSources, build recipes and patches are in iris-private-shell-sources.zip, "
              "distributed alongside the Iris installer. No system Git is installed.\n")
    all_files = {"tools/shell/" + name: data for name, data in payloads.items()}
    all_files.update(notices)
    all_files["licenses/shell/README.txt"] = notice.encode()
    all_files["licenses/shell/components.json"] = (json.dumps({"licenses": licenses}, indent=2) + "\n").encode()
    all_files["licenses/shell/sources.json"] = (json.dumps(pin, indent=2) + "\n").encode()
    write_zip(stage / "iris-private-shell.zip", all_files)
    sources["README.txt"] = notice.encode()
    sources["sources.json"] = (json.dumps(pin, indent=2) + "\n").encode()
    write_zip(stage / "iris-private-shell-sources.zip", sources)
    result = {name: file_hash(stage / name) for name in ("iris-private-shell.zip", "iris-private-shell-sources.zip")}
    (stage / "receipt.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf8")
    return result


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--cache-root", type=Path, required=True)
    p.add_argument("--sources-cache", type=Path, required=True)
    p.add_argument("--offline", action="store_true")
    args = p.parse_args()
    print(json.dumps(build(args.output, args.cache_root, args.sources_cache,
                           json.loads((ROOT / "scripts/shell_runtime_sources.json").read_text("utf8")), offline=args.offline)))


if __name__ == "__main__":
    main()
