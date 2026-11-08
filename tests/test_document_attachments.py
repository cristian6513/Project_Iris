from __future__ import annotations

import asyncio
import io
import json

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from starlette.datastructures import UploadFile

from core.document_attachments import (
    MAX_PDF_BYTES,
    PdfAttachmentError,
    PdfNoTextError,
    PdfTooLargeError,
    extract_pdf_text,
)


def _text_pdf(text: str = "Constancia casa 47") -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=200)
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    resources = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)}),
    })
    stream = DecodedStreamObject()
    safe_text = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream.set_data(f"BT /F1 12 Tf 40 120 Td ({safe_text}) Tj ET".encode("latin-1"))
    page[NameObject("/Resources")] = resources
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def test_pdf_text_is_extracted_locally_with_page_context() -> None:
    payload = extract_pdf_text(_text_pdf(), filename=r"C:\fake\constancia.pdf")

    assert payload["name"] == "constancia.pdf"
    assert payload["page_count"] == 1
    assert payload["pages_with_text"] == 1
    assert "--- Página 1 ---" in payload["text"]
    assert "Constancia casa 47" in payload["text"]
    assert payload["raw_file_retained"] is False


def test_image_only_pdf_reports_that_ocr_is_not_available() -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    output = io.BytesIO()
    writer.write(output)

    with pytest.raises(PdfNoTextError, match="texto seleccionable"):
        extract_pdf_text(output.getvalue(), filename="scan.pdf")


def test_pdf_validation_rejects_oversize_and_non_pdf_data() -> None:
    with pytest.raises(PdfTooLargeError):
        extract_pdf_text(b"%PDF-" + b"x" * MAX_PDF_BYTES, filename="huge.pdf")
    with pytest.raises(PdfAttachmentError, match="cabecera PDF"):
        extract_pdf_text(b"not a document", filename="fake.pdf")


def test_chat_pdf_endpoint_returns_extracted_text_without_raw_bytes() -> None:
    from core.websocket_server import extract_chat_pdf

    upload = UploadFile(
        filename=r"C:\fake\constancia.pdf",
        file=io.BytesIO(_text_pdf("Propietario de la casa 47")),
        headers={"content-type": "application/pdf"},
    )
    response = asyncio.run(extract_chat_pdf(upload))
    payload = json.loads(response.body)

    assert payload["name"] == "constancia.pdf"
    assert "Propietario de la casa 47" in payload["text"]
    assert payload["processing"] == "local-text-extraction"
    assert payload["raw_file_retained"] is False
