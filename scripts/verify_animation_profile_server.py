"""Isolated loopback A08.2 QA server. Never run against an existing user profile.

The companion adapter is the browser fixture, not the native Tauri executable.
Only this CLI fixture exposes /qa controls; production routes remain unchanged.
"""

from __future__ import annotations

import argparse
import asyncio
import builtins
import hashlib
import io
import json
import os
import shutil
import socket
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-root", required=True)
    parser.add_argument("--avatar", required=True)
    args = parser.parse_args()
    repository = Path(__file__).resolve().parent.parent
    work = Path(args.work_root).resolve()
    if not work.is_relative_to(repository / "artifacts") or any(work.iterdir()):
        raise ValueError("QA requires a new empty directory inside repository/artifacts")
    avatar = Path(args.avatar).resolve(strict=True)
    sys.path.insert(0, str(repository))
    for name in (
        "IRIS_RUNTIME_DESCRIPTOR",
        "HERMES_REPO",
        "HERMES_AGENT_ROOT",
        "PYTHONHOME",
        "PYTHONPATH",
    ):
        os.environ.pop(name, None)
    profile = work / "profile"
    os.environ.update(
        {
            "IRIS_HOME": str(profile),
            "HERMES_HOME": str(work / "hermes"),
            "HOME": str(work),
            "USERPROFILE": str(work),
            "APPDATA": str(work / "AppData/Roaming"),
            "LOCALAPPDATA": str(work / "AppData/Local"),
        }
    )
    profile.mkdir()
    token = "a08-fixture-" + "a" * 48
    (profile / "companion_token").write_text(token, encoding="utf-8")
    factory = work / "factory" / "face"
    shutil.copytree(repository / "face" / "animations", factory / "animations")
    shutil.copytree(repository / "face" / "js", factory / "js")
    before = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (factory / "animations").iterdir()
        if p.is_file()
    }

    # No provider calls or real accounts. Only loopback HTTP is available.
    original_connect = socket.socket.connect

    def local_connect(instance, address):
        if isinstance(address, tuple) and address[0] not in {"127.0.0.1", "::1", "localhost"}:
            raise OSError("External connections disabled in A08.2 fixture")
        return original_connect(instance, address)

    socket.socket.connect = local_connect
    import uvicorn
    from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles

    from core import profile_paths
    from core import websocket_server as core
    from core.animation_library import AnimationLibrary
    from core.animation_store import _replace, _write_temp
    from core.config import IRISConfig
    from core.profile_migration import migrate_animation_profile
    from core.profile_paths import ProfilePaths, profile_writer
    from wizard import app as wizard

    # The lease/process safety scanner has separate mocked regression tests.
    # A brand-new CLI fixture must not inspect other applications' environment.
    profile_paths._legacy_profile_writers = lambda *args, **kwargs: None

    def assert_not_factory(candidate):
        if isinstance(candidate, (str, bytes, os.PathLike)):
            selected = Path(os.fsdecode(candidate)).resolve()
            if selected == factory or selected.is_relative_to(factory):
                raise PermissionError("QA factory is read-only")

    def guarded_open(original):
        def opened(candidate, mode="r", *args, **kwargs):
            if any(flag in mode for flag in "wax+"):
                assert_not_factory(candidate)
            return original(candidate, mode, *args, **kwargs)

        return opened

    builtins.open = guarded_open(builtins.open)
    io.open = guarded_open(io.open)
    original_os_open = os.open

    def file_open(candidate, flags, *args, **kwargs):
        if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
            assert_not_factory(candidate)
        return original_os_open(candidate, flags, *args, **kwargs)

    os.open = file_open

    def guarded_mutation(original, two_paths=False):
        def mutated(candidate, *args, **kwargs):
            assert_not_factory(candidate)
            if two_paths and args:
                assert_not_factory(args[0])
            return original(candidate, *args, **kwargs)

        return mutated

    for name in ("mkdir", "unlink", "remove", "rmdir", "rename", "replace"):
        setattr(os, name, guarded_mutation(getattr(os, name), name in {"rename", "replace"}))

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    config = IRISConfig.model_construct(websocket_port=port, websocket_host="127.0.0.1")
    IRISConfig.load = classmethod(lambda cls: config)
    library = AnimationLibrary(factory / "animations", profile / "animations")
    wizard._animations_dir = lambda: profile / "animations"
    wizard._animation_library = lambda: library
    paths = ProfilePaths(profile)
    with profile_writer(
        "qa",
        paths=paths,
        prepare=lambda: migrate_animation_profile(factory / "animations", profile / "animations"),
    ):
        initial = (profile / "animations" / "manifest.json").read_bytes()
        core.mount_static_files(factory)
        core.app.mount(
            "/vendor", StaticFiles(directory=str(repository / "companion" / "node_modules"))
        )
        core.app.mount("/wizard", wizard.app)

        @core.app.get("/qa-avatar.vrm")
        def qa_avatar():
            return FileResponse(avatar)

        @core.app.get("/tests/fixtures/animation_hot_reload.html")
        def qa_html():
            source = (repository / "tests/fixtures/animation_hot_reload.html").read_text(
                encoding="utf-8"
            )
            return HTMLResponse(source.replace("qa-only", token).replace("A07", "A08.2"))

        @core.app.post("/qa/reset")
        def qa_reset():
            # Test-only rollback keeps immutable historical files available.
            target = profile / "animations" / "manifest.json"
            _replace(_write_temp(target.parent, initial), target)
            return {"status": "ok"}

        @core.app.get("/qa/factory-hashes")
        def qa_hashes():
            current = {
                p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in (factory / "animations").iterdir()
                if p.is_file()
            }
            return JSONResponse({"unchanged": current == before, "files": len(current)})

        print(json.dumps({"origin": f"http://127.0.0.1:{port}"}), flush=True)
        server = uvicorn.Server(
            uvicorn.Config(core.app, host="127.0.0.1", port=port, log_level="warning")
        )
        asyncio.run(server.serve(sockets=[sock]))


if __name__ == "__main__":
    main()
