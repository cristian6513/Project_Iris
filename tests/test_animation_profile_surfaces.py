"""A08.2: Core and Wizard expose one profile overlay over read-only assets."""

from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from asset_upload_fixtures import animation
from fastapi.testclient import TestClient

from core import identity_api, runtime_descriptor
from core import websocket_server as core
from core.config import IRISConfig
from wizard import app as wizard


@pytest.fixture
def surfaces(tmp_path, monkeypatch):
    face = tmp_path / "installation" / "face"
    factory = face / "animations"
    factory.mkdir(parents=True)
    (factory / "wave.vrma").write_bytes(animation("factory"))
    (factory / "manifest.json").write_text(
        json.dumps(
            {
                "clips": {"wave": "wave.vrma"},
                "body_only_clips": ["wave"],
                "facial_profiles": {"wave": {"emotion": "feliz"}},
            }
        ),
        encoding="utf-8",
    )
    profile = tmp_path / "profile"
    monkeypatch.setenv("IRIS_HOME", str(profile))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    runtime = dataclasses.replace(runtime_descriptor.get_runtime(), assets_root=face)
    monkeypatch.setattr(runtime_descriptor, "get_runtime", lambda: runtime)
    config = IRISConfig.model_construct(lan_access=True, lan_pairing_token="A08-PROFILE")
    monkeypatch.setattr(IRISConfig, "load", classmethod(lambda cls: config))
    monkeypatch.setattr(identity_api, "sync_hermes_identity_from_config", lambda cfg: {})
    monkeypatch.setattr(core, "broadcast_to_face", AsyncMock())
    monkeypatch.setattr(core.app.state, "agent", None, raising=False)
    monkeypatch.setattr(core.app.state, "face_dir", None, raising=False)
    monkeypatch.setattr(core.app.router, "routes", list(core.app.router.routes))
    core.mount_static_files(face)
    original = {path.name: path.read_bytes() for path in factory.iterdir()}

    # Windows chmod does not make a directory read-only. Reject every native
    # mutation of this factory fixture so even a lock-file attempt fails.
    def in_factory(path):
        if isinstance(path, int):
            return False
        try:
            Path(path).absolute().relative_to(factory)
        except (TypeError, ValueError):
            return False
        return True

    original_open, original_mkdir = os.open, os.mkdir
    original_replace, original_unlink = os.replace, os.unlink
    original_path_open = Path.open

    def checked_open(path, flags, *args, **kwargs):
        if in_factory(path) and flags & (
            os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
        ):
            raise PermissionError("read-only factory")
        return original_open(path, flags, *args, **kwargs)

    def checked_path_open(path, mode="r", *args, **kwargs):
        if in_factory(path) and any(flag in mode for flag in "wax+"):
            raise PermissionError("read-only factory")
        return original_path_open(path, mode, *args, **kwargs)

    def checked_mkdir(path, *args, **kwargs):
        if in_factory(path):
            raise PermissionError("read-only factory")
        return original_mkdir(path, *args, **kwargs)

    def checked_replace(source, destination, *args, **kwargs):
        if in_factory(source) or in_factory(destination):
            raise PermissionError("read-only factory")
        return original_replace(source, destination, *args, **kwargs)

    def checked_unlink(path, *args, **kwargs):
        if in_factory(path):
            raise PermissionError("read-only factory")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", checked_open)
    monkeypatch.setattr(Path, "open", checked_path_open)
    monkeypatch.setattr(os, "mkdir", checked_mkdir)
    monkeypatch.setattr(os, "replace", checked_replace)
    monkeypatch.setattr(os, "unlink", checked_unlink)
    yield SimpleNamespace(factory=factory, profile=profile, original=original, config=config)
    assert {path.name: path.read_bytes() for path in factory.iterdir()} == original


def client(surface="core", *, remote=False):
    return TestClient(
        core.app if surface == "core" else wizard.app,
        base_url="http://192.168.1.10:8765" if remote else "http://127.0.0.1:8765",
        client=("192.168.1.20" if remote else "127.0.0.1", 51000),
    )


def upload(http, surface, payload, filename="replacement.vrma"):
    return http.post(
        "/api/upload-vrma" if surface == "core" else "/api/upload-animation",
        data={"name" if surface == "core" else "slot": "wave"},
        files={"file": (filename, payload, "application/octet-stream")},
    )


def test_factory_snapshot_and_slots_read_without_writing_installation(surfaces):
    with client() as http:
        snapshot = http.get("/api/animation-manifest").json()
        resource = snapshot["resources"]["wave"]
        assert resource["source"] == "factory"
        assert http.get(resource["url"]).content == animation("factory")
    with client("wizard") as http:
        slots = http.get("/api/animation-slots")
        assert slots.status_code == 200
        assert next(row for row in slots.json()["slots"] if row["slot"] == "wave")["present"]


@pytest.mark.parametrize("surface", ["core", "wizard"])
def test_both_surfaces_publish_to_profile_and_share_effective_snapshot(surfaces, surface):
    with client() as core_http, client("wizard") as wizard_http:
        first = core_http.get("/api/animation-manifest")
        response = upload(
            core_http if surface == "core" else wizard_http, surface, animation("user")
        )
        assert response.status_code == 200, response.text
        filename = response.json()["filename"]
        assert (surfaces.profile / "animations" / filename).read_bytes() == animation("user")
        changed = core_http.get(
            "/api/animation-manifest", headers={"If-None-Match": first.headers["etag"]}
        )
        assert changed.status_code == 200
        snapshot = changed.json()
        resource = snapshot["resources"]["wave"]
        assert resource["filename"] == filename and resource["source"] == "user"
        assert snapshot["manifest"]["facial_profiles"]["wave"]["emotion"] == "feliz"
        assert core_http.get(resource["url"]).content == animation("user")
        # The factory URL remains bound to its old bytes after replacement.
        assert core_http.get("/face/animations/wave.vrma").content == animation("factory")
        slots = wizard_http.get("/api/animation-slots").json()
        assert next(row for row in slots["slots"] if row["slot"] == "wave")["filename"] == filename


@pytest.mark.parametrize("surface", ["core", "wizard"])
def test_invalid_replacement_preserves_profile_manifest_and_http_revision(surfaces, surface):
    with client() as core_http, client("wizard") as wizard_http:
        http = core_http if surface == "core" else wizard_http
        assert upload(http, surface, animation("user")).status_code == 200
        before = core_http.get("/api/animation-manifest")
        manifest = (surfaces.profile / "animations" / "manifest.json").read_bytes()
        rejected = upload(http, surface, b"malformed")
        assert rejected.status_code == 400
        assert (surfaces.profile / "animations" / "manifest.json").read_bytes() == manifest
        assert (
            core_http.get(
                "/api/animation-manifest", headers={"If-None-Match": before.headers["etag"]}
            ).status_code
            == 304
        )


def test_effective_static_manifest_head_clip_range_and_conditional_reads(surfaces):
    with client() as http:
        response = upload(http, "core", animation("user"))
        assert response.status_code == 200, response.text
        manifest = http.get("/face/animations/manifest.json")
        head = http.head("/face/animations/manifest.json")
        assert head.content == b"" and head.headers["content-length"] == str(len(manifest.content))
        assert head.headers["cache-control"] == "no-store"
        filename = manifest.json()["clips"]["wave"]
        clip = http.get("/face/animations/" + filename)
        assert http.head("/face/animations/" + filename).content == b""
        assert (
            http.get("/face/animations/" + filename, headers={"Range": "bytes=0-3"}).content
            == b"glTF"
        )
        assert (
            http.get(
                "/face/animations/" + filename, headers={"If-None-Match": clip.headers["etag"]}
            ).status_code
            == 304
        )
        assert http.request("BREW", "/face/animations/manifest.json").status_code == 405
        assert http.request("BREW", "/face/animations/" + filename).status_code == 405


def test_profile_clip_urls_require_pairing_and_reject_ambiguous_paths(surfaces):
    with client() as local:
        saved = upload(local, "core", animation("user"))
        assert saved.status_code == 200, saved.text
        path = "/face/animations/" + saved.json()["filename"]
        assert local.get("/face/animations/subdir/" + saved.json()["filename"]).status_code == 404
        assert local.get("/face/animations/%5C" + saved.json()["filename"]).status_code == 404
        assert local.get("/face/animations/C%3A" + saved.json()["filename"]).status_code == 404
        assert local.get("/face/animations/.animation-store.lock").status_code == 404
    with client(remote=True) as remote:
        assert remote.get(path).status_code == 403
        assert remote.get(path, headers={"X-IRIS-LAN-Token": "A08-PROFILE"}).status_code == 200
        assert remote.get("/api/animation-manifest").status_code == 403
