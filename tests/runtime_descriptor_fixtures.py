"""Small fake installations; no executable, account or model is actually used."""

import json
from pathlib import Path


def write_runtime_fixture(base: Path, *, mode: str = "packaged") -> tuple[Path, dict]:
    base.mkdir(parents=True, exist_ok=True)
    files = [
        "iris/core/desktop.py", "private/iris/python.exe",
        "private/hermes/app/hermes_cli/__init__.py",
        "private/hermes/python.exe", "private/hermes/commands/hermes.exe",
        "tools/audio/bin/ffmpeg.exe", "tools/audio/bin/ffprobe.exe",
        "tools/audio/bin/avcodec.dll",
        "tools/shell/usr/bin/bash.exe", "tools/shell/usr/bin/sh.exe",
        "tools/shell/usr/bin/msys-2.0.dll", "tools/shell/etc/fstab",
    ]
    for name in files:
        path = base / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture only")
    (base / "web/Face á").mkdir(parents=True, exist_ok=True)
    metadata = base / "private/hermes/app/runtime-pin.json"
    metadata.write_text(json.dumps({"schema_version": 1, "version": "0.21.4", "revision": "b" * 40}), "utf-8")
    spec = {
        "schema_version": 2, "mode": mode,
        "iris": {"root": "iris", "python": "private/iris/python.exe", "assets": "web/Face á", "revision": "a" * 40},
        "hermes": {
            "root": "private/hermes/app", "python": "private/hermes/python.exe",
            "cli": "private/hermes/commands/hermes.exe", "metadata": "private/hermes/app/runtime-pin.json",
            "version": "0.21.4", "revision": "b" * 40,
        },
    }
    descriptor = base / "runtime.json"
    descriptor.write_text(json.dumps(spec, ensure_ascii=False), "utf-8")
    return descriptor, spec
