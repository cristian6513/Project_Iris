"""Upload-only HTTP budgets, inside the existing Host/Origin authorization.

Own multipart parsing so cancellation, disconnects, malformed messages and size
failures close every spool, including files not yet added to the final FormData.
The byte counter runs before each chunk reaches the multipart parser.
"""

from __future__ import annotations

from fastapi import HTTPException
from fastapi.routing import APIRoute
from python_multipart.exceptions import MultipartParseError
from python_multipart.multipart import parse_options_header
from starlette.formparsers import MultiPartException, MultiPartParser
from starlette.requests import ClientDisconnect

from core.asset_uploads import (
    ANIMATION_MAX_BYTES,
    AVATAR_MAX_BYTES,
    AssetUploadError,
    upload_filename,
)

MULTIPART_OVERHEAD_BYTES = 64 * 1024
MAX_PART_HEADER_BYTES = 8192
MAX_FIELD_BYTES = 1024
UPLOAD_LIMITS = {
    "/api/avatar/upload": (AVATAR_MAX_BYTES, None),
    "/api/upload-model": (AVATAR_MAX_BYTES, None),
    "/api/upload-vrma": (ANIMATION_MAX_BYTES, "name"),
    "/api/upload-animation": (ANIMATION_MAX_BYTES, "slot"),
}


class BoundedMultipartParser(MultiPartParser):
    def __init__(self, headers, stream, maximum, field):
        super().__init__(
            headers,
            stream,
            max_files=1,
            max_fields=int(field is not None),
            max_part_size=MAX_FIELD_BYTES,
        )
        self.maximum = maximum
        self.field = field
        self.finished = False
        self.part_bytes = self.header_bytes = 0

    def on_part_begin(self):
        super().on_part_begin()
        self.part_bytes = self.header_bytes = 0

    def _header_budget(self, size):
        self.header_bytes += size
        if self.header_bytes > MAX_PART_HEADER_BYTES:
            raise AssetUploadError("Cabeceras multipart demasiado grandes.", 413)

    def on_header_field(self, data, start, end):
        self._header_budget(end - start)
        super().on_header_field(data, start, end)

    def on_header_value(self, data, start, end):
        self._header_budget(end - start)
        super().on_header_value(data, start, end)

    def on_headers_finished(self):
        disposition = self._current_part.content_disposition or b""
        # python-multipart deliberately strips old IE Windows paths. Reject
        # those on the wire before that compatibility normalization can occur.
        if b"filename" in disposition.lower() and b"\\" in disposition:
            raise AssetUploadError("El nombre del archivo no puede contener rutas Windows.")
        super().on_headers_finished()
        part = self._current_part
        if part.file is not None:
            upload_filename(part.file.filename or "")
            if part.field_name != "file":
                raise AssetUploadError("Campo de archivo no permitido.")
        elif part.field_name != self.field:
            raise AssetUploadError("Campo multipart no permitido.")

    def on_part_data(self, data, start, end):
        self.part_bytes += end - start
        maximum = self.maximum if self._current_part.file is not None else MAX_FIELD_BYTES
        if self.part_bytes > maximum:
            raise AssetUploadError("El archivo o campo supera el límite permitido.", 413)
        super().on_part_data(data, start, end)

    def on_end(self):
        self.finished = True


class AssetUploadRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def bounded(request):
            maximum, field = UPLOAD_LIMITS[self.path]
            budget = maximum + MULTIPART_OVERHEAD_BYTES
            parser = None
            try:
                lengths = request.headers.getlist("content-length")
                if lengths:
                    if (
                        len(lengths) != 1
                        or not lengths[0].isascii()
                        or not lengths[0].isdigit()
                        or len(lengths[0]) > 16
                    ):
                        raise AssetUploadError("Content-Length inválido.")
                    if int(lengths[0]) > budget:
                        raise AssetUploadError("La subida supera el límite permitido.", 413)
                kind, options = parse_options_header(request.headers.get("content-type", ""))
                boundary = options.get(b"boundary", b"")
                if kind != b"multipart/form-data" or not 1 <= len(boundary) <= 200:
                    raise AssetUploadError(
                        "Se requiere multipart/form-data con un boundary válido."
                    )

                async def counted():
                    size = 0
                    async for chunk in request.stream():
                        size += len(chunk)
                        if size > budget:
                            raise AssetUploadError("La subida supera el límite permitido.", 413)
                        # ASGI servers can deliver a large chunk. Keep parser
                        # callbacks and queued spool writes bounded as well.
                        for offset in range(0, len(chunk), 64 * 1024):
                            yield chunk[offset : offset + 64 * 1024]

                parser = BoundedMultipartParser(request.headers, counted(), maximum, field)
                form = await parser.parse()
                if not parser.finished:
                    raise AssetUploadError("Subida multipart incompleta.")
                request._form = form  # FastAPI reuses it; there is no second parse.
                return await original(request)
            except AssetUploadError as exc:
                raise HTTPException(exc.status_code, str(exc)) from exc
            except (MultiPartException, MultipartParseError, ClientDisconnect) as exc:
                raise HTTPException(400, "Subida multipart inválida o interrumpida.") from exc
            finally:
                if parser is not None:
                    # Includes the current unfinished part and runs on
                    # BaseException/CancelledError, unlike the stock parser.
                    for spool in parser._files_to_close_on_error:
                        spool.close()

        return bounded


def asset_upload_route(app, path):
    """Register just the four asset endpoints; other APIs keep their policies."""

    def register(handler):
        app.router.add_api_route(
            path, handler, methods=["POST"], route_class_override=AssetUploadRoute
        )
        return handler

    return register
