"""Regenerate the approved PEP 751 locks of Iris optional extras (A08.5).

Developer tool; the product never runs it. For each extra in
``core/extras_data/manifest.json`` it resolves the declared requirements with
uv for the manifest's interpreter (CPython 3.11, win_amd64), wheels only,
from the declared index and cutoff date. It then validates every selected
wheel with the same rules the installer enforces and records the lock
SHA-256, package count and download size in the manifest.

Run on Windows x64 with CPython 3.11 (the target), for example:

    uv run --no-sync python scripts/lock_extras.py --extra kokoro

Review the lock diff before committing: these files define exactly what a
user may install into an isolated profile environment.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import extras  # noqa: E402


def resolve(extra_id: str, entry: dict, resolution: dict, uv: str) -> None:
    lock = extras.DATA_DIR / entry["lock"]
    with tempfile.TemporaryDirectory() as temporary:
        requirements = Path(temporary) / "requirements.in"
        requirements.write_text("\n".join(entry["requirements"]) + "\n", encoding="utf-8")
        output = Path(temporary) / entry["lock"]
        subprocess.run([
            uv, "pip", "compile", str(requirements), "--format", "pylock.toml",
            "--python-version", "3.11", "--python-platform", resolution["python_platform"],
            "--only-binary", ":all:", "--index-url", resolution["index_url"],
            "--exclude-newer", resolution["exclude_newer"], "--no-header", "--no-config",
            "-o", str(output),
        ], check=True)
        shutil.copyfile(output, lock)
    raw = lock.read_bytes()
    entry["lock_sha256"] = hashlib.sha256(raw).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--extra", action="append", help="extra id to resolve again (default: all)")
    parser.add_argument("--uv", default=shutil.which("uv") or "uv")
    parser.add_argument("--check", action="store_true", help="only validate the existing locks")
    args = parser.parse_args(argv)
    document = json.loads(extras.MANIFEST_PATH.read_text(encoding="utf-8"))
    current = extras.parse_manifest(document, extras.DATA_DIR)
    problem = extras.compatibility_error(current)
    if problem:
        raise SystemExit(f"Run this tool with the target interpreter: {problem}")
    selected = args.extra or list(document["extras"])
    for extra_id in selected:
        entry = document["extras"][extra_id]
        if not args.check:
            resolve(extra_id, entry, document["resolution"], args.uv)
        manifest = extras.parse_manifest(document, extras.DATA_DIR)
        wheels = extras.locked_wheels(manifest.extras[extra_id], manifest)
        measured = {"packages": len(wheels), "download_bytes": sum(wheel.size for wheel in wheels)}
        if args.check and any(entry[key] != value for key, value in measured.items()):
            raise SystemExit(f"{extra_id}: manifest totals differ from its lock: {measured}")
        entry.update(measured)
        print(f"{extra_id}: {len(wheels)} wheels, {entry['download_bytes'] / 1e6:.1f} MB, "
              f"lock {entry['lock_sha256'][:12]}")
    if not args.check:
        extras.MANIFEST_PATH.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n",
                                        encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
