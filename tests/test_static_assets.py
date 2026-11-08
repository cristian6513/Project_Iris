"""Local web MIME remains valid with hostile Windows file associations."""

from __future__ import annotations

import mimetypes
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.static_assets import LocalWebStaticFiles


_RESOURCES = [
    ("module.mjs", b"export const local = true;", "text/javascript"),
    ("runtime.js", b"window.localRuntime = true;", "text/javascript"),
    ("layout.css", b"body { color: white; }", "text/css"),
    ("runtime.wasm", b"\x00asm\x01\x00\x00\x00", "application/wasm"),
    ("outfit.woff", b"wOFF-local-font", "font/woff"),
    ("outfit.woff2", b"wOF2-local-font", "font/woff2"),
    ("face_landmarker.task", b"local-model-bytes", "application/octet-stream"),
    ("iris.vrm", b"glTF-local-avatar", "model/gltf-binary"),
]


@pytest.fixture
def incorrect_host_mimes(monkeypatch):
    # Use the same standard API that reflects Windows associations. The fixture
    # records and restores each mapping; production never modifies this registry.
    mimetypes.guess_type("initialize-mime-registry.mjs")
    for filename, _, _ in _RESOURCES:
        suffix = "." + filename.rsplit(".", 1)[1]
        monkeypatch.setitem(mimetypes.types_map, suffix, "text/plain")
        monkeypatch.setitem(mimetypes._db.types_map[True], suffix, "text/plain")
        mimetypes.add_type("text/plain", suffix)
    yield
    for filename, _, _ in _RESOURCES:
        assert mimetypes.guess_type(filename)[0] == "text/plain"


@pytest.fixture
def local_files(tmp_path):
    root = tmp_path / "local-web"
    root.mkdir()
    for filename, payload, _ in _RESOURCES:
        (root / filename).write_bytes(payload)
    (root / "index.html").write_text("<html>Local web</html>", encoding="utf-8")
    return root


@pytest.mark.parametrize("filename,payload,media_type", _RESOURCES)
def test_canonical_local_mime_get_head_without_global_fix(
    incorrect_host_mimes, local_files, filename, payload, media_type,
):
    app = FastAPI()
    app.mount("/static", LocalWebStaticFiles(directory=local_files), name="local-static")
    client = TestClient(app)
    response = client.get("/static/" + filename)
    assert response.status_code == 200
    assert response.content == payload
    assert response.headers["content-type"].split(";")[0] == media_type
    assert int(response.headers["content-length"]) == len(payload)
    assert response.headers["accept-ranges"] == "bytes"
    head = client.head("/static/" + filename)
    assert head.status_code == 200 and head.content == b""
    assert head.headers["content-type"] == response.headers["content-type"]
    assert head.headers["content-length"] == response.headers["content-length"]
    assert mimetypes.guess_type(filename)[0] == "text/plain"


def test_corrected_mime_preserves_etag_conditionals_and_byte_ranges(incorrect_host_mimes, local_files):
    app = FastAPI()
    app.mount("/static", LocalWebStaticFiles(directory=local_files), name="local-static")
    client = TestClient(app)
    response = client.get("/static/module.mjs")
    etag = response.headers["etag"]
    not_modified = client.get("/static/module.mjs", headers={"If-None-Match": etag})
    assert not_modified.status_code == 304 and not_modified.content == b""
    assert not_modified.headers["etag"] == etag
    assert client.head("/static/module.mjs", headers={"If-None-Match": etag}).status_code == 304
    assert client.get("/static/module.mjs", headers={
        "If-Modified-Since": response.headers["last-modified"],
    }).status_code == 304
    partial = client.get("/static/module.mjs", headers={"Range": "bytes=0-5", "If-Range": etag})
    assert partial.status_code == 206 and partial.content == b"export"
    assert partial.headers["content-type"] == "text/javascript; charset=utf-8"
    assert partial.headers["etag"] == etag
    assert partial.headers["content-range"] == f"bytes 0-5/{len(response.content)}"
    head = client.head("/static/module.mjs", headers={"Range": "bytes=0-5"})
    assert head.status_code == 206 and head.content == b""
    assert head.headers["content-length"] == "6"
    full = client.get("/static/module.mjs", headers={"Range": "bytes=0-5", "If-Range": '"different"'})
    assert full.status_code == 200 and full.content == response.content
    assert client.get("/static/module.mjs", headers={"Range": "bytes=999-1000"}).status_code == 416


def test_local_mime_keeps_static_path_confinement_and_method_policy(incorrect_host_mimes, local_files, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.mjs").write_bytes(b"PRIVATE-MODULE")
    link = local_files / "linked"
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(outside), str(link))
    else:
        link.symlink_to(outside, target_is_directory=True)
    try:
        app = FastAPI()
        app.mount("/static", LocalWebStaticFiles(directory=local_files), name="local-static")
        client = TestClient(app)
        assert client.get("/static/linked/private.mjs").status_code == 404
        assert client.get("/static/%2e%2e/outside/private.mjs").status_code == 404
        assert client.post("/static/module.mjs").status_code == 405
    finally:
        if os.name == "nt":
            link.rmdir()
        else:
            link.unlink()


def test_production_face_static_class_serves_correct_local_mimes(incorrect_host_mimes, local_files):
    from core.websocket_server import _FaceStaticFiles

    assert issubclass(_FaceStaticFiles, LocalWebStaticFiles)
    app = FastAPI()
    app.mount("/face", _FaceStaticFiles(directory=local_files, html=True), name="face")
    client = TestClient(app)
    for filename, payload, media_type in _RESOURCES:
        response = client.get("/face/" + filename)
        assert response.status_code == 200 and response.content == payload
        assert response.headers["content-type"].split(";")[0] == media_type
        assert client.head("/face/" + filename).headers["content-type"] == response.headers["content-type"]


def test_production_wizard_ui_mount_serves_correct_local_mimes(incorrect_host_mimes, local_files, monkeypatch):
    from core.config import IRISConfig
    from wizard import app as wizard

    # Use the actual configured /ui mount class against a temporary web root.
    # Host/origin middleware remains active, with only synthetic configuration.
    mount = next(route for route in wizard.app.router.routes if getattr(route, "name", None) == "ui")
    assert isinstance(mount.app, LocalWebStaticFiles)
    monkeypatch.setattr(mount, "app", type(mount.app)(directory=local_files, html=True))
    cfg = IRISConfig.model_construct(lan_access=False, llm_provider="")
    monkeypatch.setattr(IRISConfig, "load", classmethod(lambda cls: cfg))
    client = TestClient(wizard.app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 51000))
    for filename, payload, media_type in _RESOURCES:
        response = client.get("/ui/" + filename)
        assert response.status_code == 200 and response.content == payload
        assert response.headers["content-type"].split(";")[0] == media_type
        assert client.head("/ui/" + filename).headers["content-type"] == response.headers["content-type"]
    assert client.get("/ui/module.mjs", headers={"Host": "audit.invalid"}).status_code == 403
