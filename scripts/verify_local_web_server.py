"""Serve an isolated A08.3 candidate with synthetic APIs and no real services.

This fixture checks compiled web resources. It does not exercise providers,
physical cameras, native Companion installation or production authorization.
"""

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import socket
import sys
from pathlib import Path


def file_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", required=True)
    parser.add_argument("--work-root", required=True)
    parser.add_argument("--avatar", required=True)
    args = parser.parse_args()
    repository = Path(__file__).resolve().parent.parent
    stage = Path(args.stage).resolve(strict=True)
    work = Path(args.work_root).resolve(strict=True)
    avatar = Path(args.avatar).resolve(strict=True)
    if not work.is_relative_to(repository / "artifacts") or any(work.iterdir()):
        raise ValueError("QA requires a new empty directory within artifacts")
    for name in (
        "IRIS_RUNTIME_DESCRIPTOR",
        "HERMES_REPO",
        "HERMES_AGENT_ROOT",
        "PYTHONPATH",
        "PYTHONHOME",
    ):
        os.environ.pop(name, None)
    os.environ.update(
        {
            "IRIS_HOME": str(work / "profile"),
            "HERMES_HOME": str(work / "hermes"),
            "HOME": str(work),
            "USERPROFILE": str(work),
            "APPDATA": str(work / "AppData/Roaming"),
            "LOCALAPPDATA": str(work / "AppData/Local"),
        }
    )
    sys.path.insert(0, str(repository))
    assets = work / "candidato Iris ñ"
    shutil.copytree(stage, assets)
    before = file_hashes(assets)
    connect = socket.socket.connect

    def local_connect(instance, address):
        if isinstance(address, tuple) and address[0] not in {"127.0.0.1", "::1", "localhost"}:
            raise OSError("External connections disabled in A08.3 QA")
        return connect(instance, address)

    socket.socket.connect = local_connect
    import uvicorn
    from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
    from fastapi.responses import FileResponse, JSONResponse, Response

    from core.animation_library import AnimationLibrary
    from core.static_assets import LocalWebStaticFiles

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    library = AnimationLibrary(assets / "face/animations", work / "profile/animations")
    config = {
        "avatar_vrm_path": "/qa/avatar.vrm",
        "room_enabled": True,
        "room_theme": "cyber",
        "visual_theme": "iris",
        "visual_theme_selected": True,
        "websocket_port": port,
        "assistant_name": "IRIS",
        "personality_template": "iris",
        "iris_version": "A08.3 QA",
    }
    app = FastAPI()

    # Exercise real preference contracts against this throwaway profile only.
    # Register before the generic synthetic fallback so /home is not a false success.
    from core.desktop_widgets import create_desktop_widgets_router
    app.include_router(create_desktop_widgets_router(lambda: work / "profile"))

    # Match the real Wizard's explicit shared-file allowlist, using staged bytes.
    @app.get("/shared/{name}")
    def shared_ui_file(name: str):
        shared = {
            "iris_dialog.js": ("js", "text/javascript"),
            "iris_dialog.css": ("css", "text/css"),
            "os_icons.js": ("js", "text/javascript"),
        }
        entry = shared.get(name)
        target = assets / "face" / entry[0] / name if entry else None
        if target is None or not target.is_file():
            raise HTTPException(status_code=404)
        return FileResponse(target, media_type=entry[1], headers={"Cache-Control": "no-cache"})

    @app.get("/qa/avatar.vrm")
    def qa_avatar():
        return FileResponse(avatar)

    @app.get("/qa/hashes")
    def hashes():
        current = file_hashes(assets)
        return {"unchanged": current == before, "files": len(before)}

    @app.get("/favicon.ico")
    def favicon():
        return Response(status_code=204)

    @app.api_route("/api/{endpoint:path}", methods=["GET", "POST"])
    async def fixture_api(endpoint: str):
        # All responses are fixture data; no account, install or model action runs.
        if endpoint in {"config", "current-config"}:
            return config
        if endpoint == "animation-manifest":
            return library.snapshot()
        if endpoint == "network-info":
            return {
                "avatar_url": origin + "/face/?pair=qa",
                "lan_reachable": True,
                "bound_for_lan": True,
                "lan_access": True,
            }
        if endpoint == "identity-options":
            return {
                "traits": [],
                "max_traits": 6,
                "iris_backstory": "IRIS QA",
                "legacy_file": {"detected": False},
            }
        if endpoint == "identity-preview":
            return {
                "soul": "IRIS QA",
                "warnings": [],
                "selected_traits": [],
                "characters": 7,
                "estimated_tokens": 2,
            }
        if endpoint == "identity-history":
            return {"entries": []}
        if endpoint in {"current-identity", "identity"}:
            return {"assistant_name": "IRIS", "name": "IRIS"}
        if endpoint == "vision-assistant/settings":
            return {"enabled": False, "state": "off", "paused_until": 0}
        if endpoint.startswith("hermes-wake"):
            return {"enabled": False, "available": False}
        if endpoint == "iris/status":
            return {"running": False, "port": port}
        if endpoint == "runtime-info":
            return {"mode": "source", "operations": {}}
        if endpoint == "autostart-status":
            return {"enabled": False}
        return JSONResponse(
            {
                "status": "offline",
                "connected": False,
                "jobs": [],
                "outputs": [],
                "skills": [],
                "messages": [],
                "providers": [],
                "channels": [],
                "memories": [],
                "installed": False,
                "enabled": False,
                "entries": [],
                "models": [],
                "message": "Synthetic offline QA response",
            }
        )

    @app.websocket("/ws")
    async def fixture_socket(websocket: WebSocket):
        await websocket.accept()
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass

    app.mount("/face", LocalWebStaticFiles(directory=assets / "face", html=True))
    app.mount("/ui", LocalWebStaticFiles(directory=assets / "wizard/ui", html=True))
    app.mount("/theme-assets", LocalWebStaticFiles(directory=assets / "face/public/assets/os"))
    print(json.dumps({"origin": origin, "assets": str(assets), "files": len(before)}), flush=True)
    asyncio.run(uvicorn.Server(uvicorn.Config(app, log_level="warning")).serve(sockets=[sock]))


if __name__ == "__main__":
    main()
