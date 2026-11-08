"""Real HTTP upload routes share A02 storage and post-commit semantics."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from asset_upload_fixtures import BVH, animation, animation_document, container
from fastapi import UploadFile
from fastapi.testclient import TestClient
from test_animation_store import glb

from core import animation_store as store
from core import animations, identity_api
from core import websocket_server as core
from core.animation_library import AnimationLibrary
from core.config import IRISConfig
from wizard import app as wizard


@pytest.fixture
def uploads(monkeypatch, tmp_path):
    factory = tmp_path / "face" / "animations"
    factory.mkdir(parents=True)
    (factory / "manifest.json").write_text('{"clips":{}}', encoding="utf-8")
    root = tmp_path / "profile" / "animations"
    root.mkdir(parents=True)
    previous = f"wave.{hashlib.sha256(glb('old')).hexdigest()}.vrma"
    (root / previous).write_bytes(glb("old"))
    (root / "manifest.json").write_text(json.dumps({"clips": {"wave": previous}}), encoding="utf-8")
    monkeypatch.setenv("IRIS_HOME", str(root.parent))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    monkeypatch.setattr(core.app.state, "face_dir", factory.parent, raising=False)
    monkeypatch.setattr(wizard, "_animations_dir", lambda: root)
    monkeypatch.setattr(wizard, "_animation_library", lambda: AnimationLibrary(factory, root))
    monkeypatch.setattr(animations, "_ANIM_DIR", root)
    monkeypatch.setattr(animations, "_MANIFEST_PATH", root / "manifest.json")
    monkeypatch.setattr(animations, "_cache_token", None)
    monkeypatch.setattr(animations, "_cache_clips", [])
    monkeypatch.setattr(core.app.state, "agent", None, raising=False)
    monkeypatch.setattr(IRISConfig, "load", classmethod(lambda cls: IRISConfig.model_construct()))
    sync = Mock(return_value={"SOUL.md": "changed"})
    monkeypatch.setattr(identity_api, "sync_hermes_identity_from_config", sync)
    broadcast = AsyncMock()
    monkeypatch.setattr(core, "broadcast_to_face", broadcast)
    from core.profile_migration import migrate_animation_profile

    migrate_animation_profile(factory, root)
    return SimpleNamespace(root=root, previous=previous, sync=sync, broadcast=broadcast)


def client_for(surface):
    return TestClient(
        core.app if surface == "core" else wizard.app,
        base_url="http://127.0.0.1:8765",
        client=("127.0.0.1", 50000),
    )


def upload(client, surface, content, *, filename="new.glb", name="wave"):
    return client.post(
        "/api/upload-vrma" if surface == "core" else "/api/upload-animation",
        data={"name" if surface == "core" else "slot": name},
        files={"file": (filename, content, "application/octet-stream")},
    )


@pytest.mark.parametrize("surface", ["core", "wizard"])
def test_empty_different_extension_never_removes_previous_animation(uploads, surface):
    original = (uploads.root / "manifest.json").read_bytes()
    response = upload(client_for(surface), surface, b"")
    assert response.status_code == 400
    assert (uploads.root / "manifest.json").read_bytes() == original
    assert (uploads.root / uploads.previous).read_bytes() == glb("old")
    uploads.sync.assert_not_called()
    uploads.broadcast.assert_not_called()


@pytest.mark.parametrize("surface", ["core", "wizard"])
def test_success_response_sync_and_notification_follow_the_commit(uploads, surface):
    def sync(_cfg):
        manifest = json.loads((uploads.root / "manifest.json").read_bytes())
        assert manifest["revision"] == 1
        assert (uploads.root / manifest["clips"]["wave"]).read_bytes() == glb()
        return {"SOUL.md": "changed"}

    uploads.sync.side_effect = sync
    response = upload(client_for(surface), surface, glb())
    assert response.status_code == 200
    body = response.json()
    assert body["saved"] is True and body["status"] == "ok"
    assert body["changed"] is True and body["revision"] == 1
    assert body["sync_status"] == "synced" and not body["warnings"]
    assert body["size" if surface == "core" else "size_bytes"] == len(glb())
    assert (uploads.root / body["filename"]).read_bytes() == glb()
    assert (uploads.root / uploads.previous).exists()
    uploads.sync.assert_called_once()
    if surface == "core":
        uploads.broadcast.assert_awaited_once_with(
            "animation_catalog_changed",
            {
                "name": "wave",
                "filename": body["filename"],
                "revision": 1,
            },
        )
    else:
        listed = client_for(surface).get("/api/animation-slots").json()
        assert listed["revision"] == 1
        assert next(slot for slot in listed["slots"] if slot["slot"] == "wave")["present"]


@pytest.mark.parametrize("surface", ["core", "wizard"])
@pytest.mark.parametrize("failure", ["exception", "error_status"])
def test_sync_failure_keeps_committed_success_and_retry_is_idempotent(uploads, surface, failure):
    if failure == "exception":
        uploads.sync.side_effect = OSError("fake sync error")
    else:
        uploads.sync.return_value = {"SOUL.md": "error"}
    client = client_for(surface)
    first = upload(client, surface, glb())
    assert first.status_code == 200
    body = first.json()
    assert body["saved"] and body["sync_status"] == "pending" and body["warnings"]
    snapshot = (uploads.root / "manifest.json").read_bytes()
    uploads.sync.side_effect = None
    uploads.sync.return_value = {"SOUL.md": "changed"}
    second = upload(client, surface, glb()).json()
    assert second["revision"] == 1 and not second["changed"]
    assert second["sync_status"] == "synced" and not second["warnings"]
    assert (uploads.root / "manifest.json").read_bytes() == snapshot


@pytest.mark.parametrize("surface", ["core", "wizard"])
@pytest.mark.parametrize("failure", ["manifest", "write", "replace", "too_large", "format"])
def test_rejected_upload_never_announces_success(uploads, monkeypatch, surface, failure):
    expected = 503
    if failure == "manifest":
        (uploads.root / "manifest.json").write_bytes(b"{broken")
        expected = 409
    elif failure == "too_large":
        monkeypatch.setattr(store, "MAX_UPLOAD_BYTES", 5)
        expected = 413
    elif failure == "write":
        monkeypatch.setattr(store, "_write_temp", Mock(side_effect=OSError("disk full")))
    elif failure == "replace":
        original = store._replace

        def fail_manifest(source, destination):
            if destination.name == "manifest.json":
                raise OSError("replace failed")
            return original(source, destination)

        monkeypatch.setattr(store, "_replace", fail_manifest)
    else:
        expected = 400
    before = (uploads.root / "manifest.json").read_bytes()
    response = upload(client_for(surface), surface, b"bad format" if failure == "format" else glb())
    assert response.status_code == expected
    assert not response.json().get("saved")
    assert (uploads.root / "manifest.json").read_bytes() == before
    assert (uploads.root / uploads.previous).read_bytes() == glb("old")
    uploads.sync.assert_not_called()
    uploads.broadcast.assert_not_called()


@pytest.mark.parametrize("surface", ["core", "wizard"])
@pytest.mark.parametrize("filename", ["/outside.glb", "../outside.glb", r"..\outside.glb"])
def test_http_path_filenames_do_not_select_a_storage_path(uploads, surface, filename):
    response = upload(client_for(surface), surface, glb(), filename=filename)
    assert response.status_code == 400
    assert (uploads.root / uploads.previous).read_bytes() == glb("old")
    uploads.sync.assert_not_called()


@pytest.mark.parametrize("surface", ["core", "wizard"])
def test_unknown_http_method_does_not_invoke_upload(uploads, surface):
    path = "/api/upload-vrma" if surface == "core" else "/api/upload-animation"
    before = (uploads.root / "manifest.json").read_bytes()
    response = client_for(surface).request("BREW", path)
    assert response.status_code == 405
    assert (uploads.root / "manifest.json").read_bytes() == before
    uploads.sync.assert_not_called()
    uploads.broadcast.assert_not_called()


@pytest.mark.parametrize("surface", ["core", "wizard"])
def test_unknown_or_unsafe_slot_never_invokes_storage(uploads, monkeypatch, surface):
    save = Mock()
    monkeypatch.setattr(store.AnimationStore, "save_upload", save)
    response = upload(client_for(surface), surface, glb(), name="../wave")
    assert response.status_code == 400
    save.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ["core", "wizard"])
async def test_file_io_and_lock_wait_run_outside_the_event_loop(uploads, monkeypatch, surface):
    entered, release = threading.Event(), threading.Event()
    original = store.AnimationStore.save_upload

    def slow(self, *args):
        entered.set()
        assert release.wait(5)
        return original(self, *args)

    monkeypatch.setattr(store.AnimationStore, "save_upload", slow)
    file = UploadFile(io.BytesIO(glb()), filename="new.glb")
    handler = core.upload_vrma if surface == "core" else wizard.upload_animation
    task = asyncio.create_task(handler(file, "wave"))
    try:
        # A loop-blocking implementation cannot signal this waiter before the
        # worker's timeout; the test checks actual scheduling, not source text.
        assert await asyncio.to_thread(entered.wait, 2)
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        release.set()
    assert (await task).status_code == 200


@pytest.mark.parametrize("surface", ["core", "wizard"])
@pytest.mark.parametrize(
    "case",
    [
        "external",
        "accessor",
        "no_vrma",
        "bad_bvh",
        "fbx",
        "windows_absolute",
        "unc",
        "drive_relative",
    ],
)
def test_a05_structural_rejection_preserves_entire_catalog(uploads, surface, case):
    doc, binary = animation_document()
    filename = "new.vrma"
    expected = 400
    if case == "external":
        doc["buffers"][0]["uri"] = "../outside.bin"
    elif case == "accessor":
        doc["accessors"][0]["count"] = 999999999
    elif case == "no_vrma":
        del doc["extensions"]
    payload = container(doc, binary)
    if case == "bad_bvh":
        filename, payload = "new.bvh", BVH.replace(b"Frames: 1", b"Frames: 2")
    elif case == "fbx":
        filename, payload, expected = "new.fbx", b"; FBX 7.4.0 project file", 415
    elif case == "windows_absolute":
        filename = r"C:\outside.vrma"
    elif case == "unc":
        filename = r"\\server\outside.vrma"
    elif case == "drive_relative":
        filename = "C:outside.vrma"
    before = {p.name: p.read_bytes() for p in uploads.root.iterdir()}
    response = upload(client_for(surface), surface, payload, filename=filename)
    assert response.status_code == expected, response.text
    assert {p.name: p.read_bytes() for p in uploads.root.iterdir()} == before
    uploads.sync.assert_not_called()
    uploads.broadcast.assert_not_called()


@pytest.mark.parametrize("surface", ["core", "wizard"])
@pytest.mark.parametrize("extension", ["vrma", "glb", "gltf", "bvh"])
def test_a05_supported_formats_publish_through_both_http_routes(uploads, surface, extension):
    payload = BVH if extension == "bvh" else animation(embedded=extension == "gltf")
    response = upload(client_for(surface), surface, payload, filename="clip." + extension)
    assert response.status_code == 200, response.text
    assert (uploads.root / response.json()["filename"]).read_bytes() == payload
