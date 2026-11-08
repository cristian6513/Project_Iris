"""Serve local web runtimes with MIME types independent of Windows associations."""

from __future__ import annotations

import os
from pathlib import Path

from starlette.responses import FileResponse, Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

_LOCAL_WEB_MIME_TYPES = {
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".css": "text/css",
    ".wasm": "application/wasm",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".task": "application/octet-stream",
    ".vrm": "model/gltf-binary",  # IRIS' factory avatar (face/animations/iris.vrm)
    ".glb": "model/gltf-binary",  # room furniture (face/public/assets/room)
    ".ktx2": "image/ktx2",  # GPU-compressed room pictures (face/public/assets/room)
}


class LocalWebStaticFiles(StaticFiles):
    """Keep Starlette path/conditional/range handling and fix only web MIME."""

    def file_response(
        self,
        full_path: str | os.PathLike[str],
        stat_result: os.stat_result,
        scope: Scope,
        status_code: int = 200,
    ) -> Response:
        response = super().file_response(full_path, stat_result, scope, status_code)
        media_type = _LOCAL_WEB_MIME_TYPES.get(Path(full_path).suffix.lower())
        if media_type and isinstance(response, FileResponse):
            response.media_type = media_type
            response.headers["content-type"] = (
                f"{media_type}; charset={response.charset}"
                if media_type.startswith("text/") else media_type
            )
        return response
