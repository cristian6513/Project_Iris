"""A07: snapshots/ETags, process boundaries and unsafe manifest references."""

import hashlib
import io
import json
import os
import subprocess
import sys

import pytest
from asset_upload_fixtures import BVH, animation
from fastapi.testclient import TestClient

from core import websocket_server as core
from core.animation_store import AnimationStore
from core.config import IRISConfig


@pytest.fixture
def library(tmp_path, monkeypatch):
    monkeypatch.setenv("IRIS_HOME", str(tmp_path / "profile"))
    root = tmp_path / "face" / "animations"
    root.mkdir(parents=True)
    (root / "wave.vrma").write_bytes(animation("old"))
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "clips": {"wave": "wave.vrma"},
                "body_only_clips": ["wave"],
                "facial_profiles": {"wave": {"emotion": "feliz"}},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(core.app.state, "face_dir", root.parent, raising=False)
    config = IRISConfig.model_construct(lan_access=True, lan_pairing_token="A07-PAIR")
    monkeypatch.setattr(IRISConfig, "load", classmethod(lambda cls: config))
    return root


def client(remote=False):
    return TestClient(
        core.app,
        base_url="http://127.0.0.1:8765" if not remote else "http://192.168.1.10:8765",
        client=("192.168.1.20" if remote else "127.0.0.1", 50000),
    )


def test_snapshot_etag_same_revision_and_new_bytes_or_extension(library):
    with client() as http:
        first = http.get("/api/animation-manifest")
        assert first.status_code == 200
        data = first.json()
        assert data["resources"]["wave"]["revision"] == hashlib.sha256(animation("old")).hexdigest()
        assert data["manifest"]["facial_profiles"]["wave"]["emotion"] == "feliz"
        etag = first.headers["etag"]
        unchanged = http.get("/api/animation-manifest", headers={"If-None-Match": etag})
        assert unchanged.status_code == 304 and unchanged.content == b""
        head = http.head("/api/animation-manifest")
        assert head.headers["etag"] == etag and head.content == b""
        assert head.headers["content-length"] == first.headers["content-length"]
        for extension, data in [("vrma", animation("new")), ("bvh", BVH)]:
            saved = AnimationStore(library).save_upload(
                io.BytesIO(data), "new." + extension, "wave"
            )
            changed = http.get("/api/animation-manifest", headers={"If-None-Match": etag})
            assert changed.status_code == 200 and changed.headers["etag"] != etag
            resource = changed.json()["resources"]["wave"]
            assert resource["filename"] == saved.filename and resource["format"] == extension
            assert str(library) not in changed.text
            etag = changed.headers["etag"]
            AnimationStore(library).save_upload(io.BytesIO(data), "again." + extension, "wave")
            assert (
                http.get("/api/animation-manifest", headers={"If-None-Match": etag}).status_code
                == 304
            )


def test_separate_writer_is_visible_and_withdrawal_changes_snapshot(library):
    with client() as http:
        etag = http.get("/api/animation-manifest").headers["etag"]
        # Same process boundary as a separately launched Wizard; no shared cache/event.
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys,json;from pathlib import Path;p=Path(sys.argv[1]);"
                "m=json.loads(p.read_text());"
                "m['clips']={};p.with_suffix('.next').write_text(json.dumps(m));"
                "p.with_suffix('.next').replace(p)",
                str(library / "manifest.json"),
            ],
            capture_output=True,
            timeout=15,
        )
        assert result.returncode == 0, result.stderr
        changed = http.get("/api/animation-manifest", headers={"If-None-Match": etag})
        assert changed.status_code == 200 and changed.json()["resources"] == {}


@pytest.mark.parametrize(
    "filename",
    [
        "/outside.vrma",
        "../outside.vrma",
        r"..\outside.vrma",
        r"C:\outside.vrma",
        r"\\server\share\outside.vrma",
    ],
)
def test_snapshot_rejects_absolute_traversal_and_windows_references(library, filename):
    manifest = library / "manifest.json"
    manifest.write_text(json.dumps({"clips": {"wave": filename}}))
    before = manifest.read_bytes()
    with client() as http:
        response = http.get("/api/animation-manifest")
    assert response.status_code == 503
    assert manifest.read_bytes() == before


@pytest.mark.parametrize("target", ["root", "manifest", "clip"])
def test_snapshot_cannot_follow_directory_or_file_aliases(library, tmp_path, target):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "manifest.json").write_text('{"clips":{},"secret":"PRIVATE"}')
    (outside / "wave.vrma").write_bytes(b"PRIVATE")
    if target == "root":
        saved = library.with_name("saved")
        library.rename(saved)
        if os.name == "nt":
            import _winapi

            _winapi.CreateJunction(str(outside), str(library))
        else:
            library.symlink_to(outside, target_is_directory=True)
    else:
        path = library / ("manifest.json" if target == "manifest" else "wave.vrma")
        path.unlink()
        if os.name == "nt":
            os.link(
                outside / path.name, path
            )  # A02 rejects file aliases, even without symlink privilege.
        else:
            path.symlink_to(outside / path.name)
    try:
        with client() as http:
            response = http.get("/api/animation-manifest")
        assert response.status_code == 503 and "PRIVATE" not in response.text
        assert (outside / "wave.vrma").read_bytes() == b"PRIVATE"
    finally:
        if target == "root":
            if os.name == "nt":
                library.rmdir()  # Remove the junction itself, never its target.
            else:
                library.unlink()


def test_unknown_method_and_unpaired_lan_cannot_read_or_mutate_snapshot(library):
    before = (library / "manifest.json").read_bytes()
    with client() as http:
        assert http.request("BREW", "/api/animation-manifest").status_code == 405
    with client(remote=True) as http:
        assert http.get("/api/animation-manifest").status_code == 403
        assert (
            http.get(
                "/api/animation-manifest", headers={"X-IRIS-LAN-Token": "A07-PAIR"}
            ).status_code
            == 200
        )
    assert (library / "manifest.json").read_bytes() == before
