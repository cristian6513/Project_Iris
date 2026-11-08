"""Receive budgets before multipart spooling, including chunked ASGI input."""

import asyncio
from contextlib import AsyncExitStack
from unittest.mock import AsyncMock

import pytest
import starlette.formparsers as forms
from fastapi import FastAPI, File, HTTPException, UploadFile
from starlette.requests import Request

from core import upload_routes as routes


def multipart(payload=b"abcd", *, fields=(), files=1, finish=True, filename="model.vrm"):
    parts = []
    for name, value in fields:
        parts.append(
            f'--test\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        )
    for _ in range(files):
        parts.append(
            (
                f'--test\r\nContent-Disposition: form-data; name="file"; '
                f'filename="{filename}"\r\n\r\n'
            ).encode()
            + payload
            + b"\r\n"
        )
    return b"".join(parts) + (b"--test--\r\n" if finish else b"")


@pytest.fixture
def spools(monkeypatch):
    made = []
    original = forms.SpooledTemporaryFile

    def tracked(*args, **kwargs):
        spool = original(*args, **kwargs)
        made.append(spool)
        return spool

    monkeypatch.setattr(forms, "SpooledTemporaryFile", tracked)
    return made


def handler(path, calls):
    app = FastAPI()

    @routes.asset_upload_route(app, path)
    async def endpoint(file: UploadFile = File(...)):
        calls.append(file)
        return {"size": file.size}

    return app.routes[-1].get_route_handler()


async def request(handler, chunks, *, path="/api/upload-model", headers=(), abort=None):
    messages = iter(chunks)

    async def receive():
        chunk = next(messages, None)
        if chunk is None:
            if abort == "cancel":
                raise asyncio.CancelledError()
            if abort == "disconnect":
                return {"type": "http.disconnect"}
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.request", "body": chunk, "more_body": True}

    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "query_string": b"",
        "headers": [(b"content-type", b"multipart/form-data; boundary=test"), *headers],
    }
    async with AsyncExitStack() as stack:
        for key in ("fastapi_middleware_astack", "fastapi_inner_astack", "fastapi_function_astack"):
            scope[key] = stack
        return await handler(Request(scope, receive))


@pytest.mark.asyncio
@pytest.mark.parametrize("path", list(routes.UPLOAD_LIMITS))
@pytest.mark.parametrize("length_header", [False, True])
async def test_exact_file_limit_succeeds_and_one_extra_byte_fails(
    monkeypatch, spools, path, length_header
):
    field = routes.UPLOAD_LIMITS[path][1]
    monkeypatch.setitem(routes.UPLOAD_LIMITS, path, (4, field))
    calls = []
    endpoint = handler(path, calls)
    for payload, expected in [(b"abcd", 200), (b"abcde", 413)]:
        body = multipart(payload, fields=[(field, "wave")] if field else [])
        headers = [(b"content-length", str(len(body)).encode())] if length_header else []
        if expected == 200:
            result = await request(endpoint, [body[:20], body[20:]], path=path, headers=headers)
            assert result.status_code == 200
        else:
            with pytest.raises(HTTPException) as error:
                await request(endpoint, [body[:20], body[20:]], path=path, headers=headers)
            assert error.value.status_code == expected
        assert all(spool.closed for spool in spools)
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("length", [None, b"1"])
async def test_actual_asgi_byte_counter_rejects_before_parser_even_with_false_length(
    monkeypatch, spools, length
):
    monkeypatch.setitem(routes.UPLOAD_LIMITS, "/api/upload-model", (4, None))
    monkeypatch.setattr(routes, "MULTIPART_OVERHEAD_BYTES", 20)
    calls = []
    headers = [(b"content-length", length)] if length else []
    with pytest.raises(HTTPException) as error:
        await request(handler("/api/upload-model", calls), [b"x" * 25], headers=headers)
    assert error.value.status_code == 413
    assert not spools and not calls


@pytest.mark.asyncio
async def test_large_content_length_rejected_without_reading_body(monkeypatch):
    calls = []
    endpoint = handler("/api/upload-model", calls)
    receive = AsyncMock(side_effect=AssertionError("body must not be read"))
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/upload-model",
        "headers": [(b"content-length", b"999999999999")],
    }
    with pytest.raises(HTTPException) as error:
        await endpoint(Request(scope, receive))
    assert error.value.status_code == 413
    receive.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["files", "fields", "field_size", "truncated", "header_size", "windows_path"]
)
async def test_multipart_cardinality_structure_and_overhead_are_bounded(monkeypatch, spools, case):
    body = multipart()
    expected = 400
    if case == "files":
        body = multipart(files=2)
    elif case == "fields":
        body = multipart(fields=[("unexpected", "x")])
    elif case == "field_size":
        body = multipart(fields=[("slot", "x" * (routes.MAX_FIELD_BYTES + 1))])
        expected = 413
    elif case == "truncated":
        body = multipart(finish=False)
    elif case == "header_size":
        monkeypatch.setattr(routes, "MAX_PART_HEADER_BYTES", 128)
        body = multipart(filename="x" * 150)
        expected = 413
    elif case == "windows_path":
        body = multipart(filename=r"C:\outside.vrm")
    calls = []
    path = "/api/upload-animation" if case == "field_size" else "/api/upload-model"
    with pytest.raises(HTTPException) as error:
        await request(handler(path, calls), [body], path=path)
    assert error.value.status_code == expected
    assert all(spool.closed for spool in spools) and not calls


@pytest.mark.asyncio
@pytest.mark.parametrize("abort", ["cancel", "disconnect"])
async def test_interrupted_upload_closes_unfinished_spool(monkeypatch, spools, abort):
    monkeypatch.setattr(routes.BoundedMultipartParser, "spool_max_size", 1)
    calls = []
    endpoint = handler("/api/upload-model", calls)
    with pytest.raises(asyncio.CancelledError if abort == "cancel" else HTTPException):
        await request(endpoint, [multipart(b"pending", finish=False)], abort=abort)
    assert spools and all(spool.closed for spool in spools)
    assert not calls


@pytest.mark.asyncio
async def test_failed_endpoint_also_closes_completed_spool(spools):
    app = FastAPI()

    @routes.asset_upload_route(app, "/api/upload-model")
    async def failing(file: UploadFile = File(...)):
        raise RuntimeError("intentional")

    with pytest.raises(RuntimeError):
        await request(app.routes[-1].get_route_handler(), [multipart()])
    assert spools and all(spool.closed for spool in spools)
