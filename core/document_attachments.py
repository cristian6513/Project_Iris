"""Bounded, local extraction for documents attached to IRIS chat.

Raw documents never enter the WebSocket or Hermes provider payload.  The
browser uploads a PDF to the loopback Core process, this module extracts a
bounded amount of text in memory, and only that text is included when the user
explicitly sends the chat turn.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from pypdf import PdfReader

MAX_PDF_BYTES = 12 * 1024 * 1024
MAX_PDF_PAGES = 80
MAX_PDF_TEXT_CHARS = 384 * 1024


class PdfAttachmentError(ValueError):
    """Base error safe to surface to the local UI."""


class PdfTooLargeError(PdfAttachmentError):
    """Raised before parsing a PDF that exceeds the byte cap."""


class PdfNoTextError(PdfAttachmentError):
    """Raised when a PDF is image-only or otherwise has no extractable text."""


def _display_name(filename: str | None) -> str:
    raw = str(filename or "documento.pdf").replace("\\", "/")
    return Path(raw).name or "documento.pdf"


def extract_pdf_text(data: bytes, *, filename: str | None = None) -> dict[str, Any]:
    """Extract bounded text from a PDF held entirely in memory.

    This intentionally does not perform OCR.  Scanned/image-only documents
    return a precise error so the UI can ask for screenshots instead of
    pretending that Hermes received content it cannot inspect.
    """

    size = len(data)
    if size > MAX_PDF_BYTES:
        raise PdfTooLargeError(
            f'"{_display_name(filename)}" supera el límite de {MAX_PDF_BYTES // (1024 * 1024)} MB.'
        )
    if not data or b"%PDF-" not in data[:1024]:
        raise PdfAttachmentError("El archivo no contiene una cabecera PDF válida.")

    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted and not reader.decrypt(""):
            raise PdfAttachmentError("El PDF está protegido con contraseña.")
        page_count = len(reader.pages)
    except PdfAttachmentError:
        raise
    except Exception as exc:
        raise PdfAttachmentError(
            "No se pudo abrir el PDF; puede estar dañado o incompleto."
        ) from exc

    page_limit = min(page_count, MAX_PDF_PAGES)
    chunks: list[str] = []
    extracted_chars = 0
    pages_with_text = 0
    failed_pages = 0
    pages_read = 0
    text_truncated = False
    truncated = page_count > MAX_PDF_PAGES

    for index in range(page_limit):
        pages_read += 1
        try:
            page_text = (reader.pages[index].extract_text() or "").replace("\x00", "").strip()
        except Exception:
            failed_pages += 1
            continue
        if not page_text:
            continue
        pages_with_text += 1
        header = f"--- Página {index + 1} ---\n"
        remaining = MAX_PDF_TEXT_CHARS - extracted_chars
        if remaining <= len(header):
            truncated = True
            text_truncated = True
            break
        page_chunk = f"{header}{page_text}"
        if len(page_chunk) > remaining:
            page_chunk = page_chunk[:remaining].rstrip()
            truncated = True
            text_truncated = True
        chunks.append(page_chunk)
        extracted_chars += len(page_chunk)
        if extracted_chars >= MAX_PDF_TEXT_CHARS:
            truncated = True
            break

    text = "\n\n".join(chunks).strip()
    if not text:
        raise PdfNoTextError(
            "El PDF no contiene texto seleccionable. Si es un escaneo, "
            "adjunta capturas de sus páginas para usar visión."
        )

    warnings: list[str] = []
    if page_count > MAX_PDF_PAGES:
        warnings.append(f"Se leyeron las primeras {MAX_PDF_PAGES} de {page_count} páginas.")
    if text_truncated:
        warnings.append("El texto se truncó para mantener el turno dentro de un tamaño seguro.")
    if failed_pages:
        warnings.append(f"No se pudo extraer texto de {failed_pages} página(s).")

    return {
        "success": True,
        "name": _display_name(filename),
        "mime": "application/pdf",
        "size": size,
        "page_count": page_count,
        "pages_read": pages_read,
        "pages_with_text": pages_with_text,
        "text": text,
        "truncated": truncated,
        "warnings": warnings,
        "processing": "local-text-extraction",
        "raw_file_retained": False,
    }
