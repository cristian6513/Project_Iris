"""A05: shared format validation and rejection before any avatar commit."""

import io
import json
import struct
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from asset_upload_fixtures import BVH, animation, animation_document, avatar, container
from fastapi.testclient import TestClient
from test_animation_store import make_directory_alias, remove_directory_alias

import wizard.app as wizard
from core import asset_uploads as assets
from core import websocket_server as core
from core.config import IRISConfig


@pytest.mark.parametrize("version", [0, 1])
def test_minimal_vrm_contract_is_preserved(version):
    assert assets.validate_asset(avatar(version), "vrm", avatar=True)


@pytest.mark.parametrize("extension", ["vrma", "glb", "gltf", "bvh"])
def test_real_single_frame_track_and_embedded_resources(extension):
    payload = BVH if extension == "bvh" else animation(embedded=extension == "gltf")
    assets.validate_asset(payload, extension)


def test_expression_and_look_at_nodes_are_not_stripped_or_retimed():
    doc, binary = animation_document()
    doc["nodes"] += [{"name": "happy"}, {"name": "lookAt"}]
    doc["extensions"]["VRMC_vrm_animation"]["expressions"] = {"preset": {"happy": {"node": 1}}}
    doc["extensions"]["VRMC_vrm_animation"]["lookAt"] = {"node": 2}
    for node in (1, 2):
        doc["animations"][0]["channels"].append(
            {"sampler": 0, "target": {"node": node, "path": "rotation"}}
        )
    original = container(doc, binary)
    assert assets.validate_asset(original, "vrma") == doc


@pytest.mark.parametrize(
    "payload",
    [b"", b"glTF", container([]), container({"asset": {"version": "2.0"}}), avatar()[:-1]],
)
def test_invalid_or_generic_model_is_not_a_vrm(payload):
    with pytest.raises(assets.AssetUploadError):
        assets.validate_asset(payload, "vrm", avatar=True)


@pytest.mark.parametrize(
    "uri",
    [
        "/outside.bin",
        "../outside.bin",
        r"..\outside.bin",
        r"C:\outside.bin",
        "C:outside.bin",
        r"\\server\file.bin",
        "https://example.org/a.bin",
        "file:///x",
        "%2e%2e/outside.bin",
        "relative.bin",
    ],
)
@pytest.mark.parametrize("extension", ["gltf", "glb", "vrma"])
def test_external_resources_rejected_in_every_gltf_container(uri, extension):
    doc, binary = animation_document()
    doc["buffers"][0]["uri"] = uri
    with pytest.raises(assets.AssetUploadError, match="externos"):
        assets.validate_asset(
            json.dumps(doc).encode() if extension == "gltf" else container(doc, binary), extension
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "view_end",
        "stride",
        "count",
        "sparse",
        "cycle",
        "duplicate_parent",
        "time",
        "output",
        "node",
        "extension",
        "nan",
        "huge_length",
        "bad_chunk",
        "json_chunk_twice",
        "unknown_interpolation",
        "extension_list",
    ],
)
def test_malformed_animation_layouts_fail_before_publication(mutation):
    doc, binary = animation_document()
    if mutation == "view_end":
        doc["bufferViews"][1]["byteLength"] = 1000
    elif mutation == "stride":
        doc["bufferViews"][1]["byteStride"] = 3
    elif mutation == "count":
        doc["accessors"][1]["count"] = 10**12
    elif mutation == "sparse":
        doc["accessors"][1]["sparse"] = {"count": 1000}
    elif mutation == "cycle":
        doc["nodes"][0]["children"] = [0]
    elif mutation == "duplicate_parent":
        doc["nodes"] += [{"children": [0, 0]}]
    elif mutation == "time":
        binary = struct.pack("<f", -1) + binary[4:]
    elif mutation == "output":
        doc["accessors"][1]["type"] = "SCALAR"
    elif mutation == "node":
        doc["animations"][0]["channels"][0]["target"]["node"] = 20
    elif mutation == "extension":
        del doc["extensions"]
    elif mutation == "nan":
        binary = binary[:4] + struct.pack("<f", float("nan")) + binary[8:]
    elif mutation == "unknown_interpolation":
        doc["animations"][0]["samplers"][0]["interpolation"] = "BREW"
    elif mutation == "extension_list":
        doc["extensionsUsed"] = 12
    payload = container(doc, binary)
    if mutation == "huge_length":
        payload = payload[:8] + struct.pack("<I", 2**32 - 1) + payload[12:]
    elif mutation == "bad_chunk":
        payload = payload[:16] + b"BLAH" + payload[20:]
    elif mutation == "json_chunk_twice":
        offset = 20 + struct.unpack_from("<I", payload, 12)[0]
        payload = payload[: offset + 4] + b"JSON" + payload[offset + 8 :]
    with pytest.raises(assets.AssetUploadError):
        assets.validate_asset(payload, "vrma")


@pytest.mark.parametrize(
    "payload",
    [
        b"[" * 70 + b"]" * 70,
        b'{"asset":{},"asset":{}}',
        b'{"asset":{"version":"2.0"},"extras":NaN}',
    ],
)
def test_json_depth_duplicate_keys_and_nonfinite_numbers(payload):
    with pytest.raises(assets.AssetUploadError):
        assets.validate_asset(payload, "gltf")


@pytest.mark.parametrize(
    "payload",
    [
        BVH.replace(b"Frames: 1", b"Frames: 2"),
        BVH + b" 0",
        BVH.replace(b"Xposition", b"Bogus"),
        BVH.replace(b"Time: 0.0333333", b"Time: nan"),
        BVH.replace(b"OFFSET", b"BROKEN", 1),
        BVH[:-10],
        BVH.replace(b"0 0 0 0 0 0 0 0 0", b"0 0 0\n0 0 0 0 0 0"),
    ],
)
def test_bvh_hierarchy_channels_and_motion_must_agree(payload):
    with pytest.raises(assets.AssetUploadError):
        assets.validate_asset(payload, "bvh")


@pytest.mark.parametrize(
    "payload",
    [
        b"Kaydara FBX Binary  \0\x1a\0" + struct.pack("<I", 7400),
        b"; FBX 7.4.0 project file\nFBXHeaderExtension: { FBXVersion: 7400 }",
        b"garbage",
    ],
)
def test_fbx_upload_is_explicitly_unsupported_instead_of_magic_only_validation(payload):
    with pytest.raises(assets.AssetUploadError, match="FBX") as exc:
        assets.validate_asset(payload, "fbx")
    assert exc.value.status_code == 415


def test_unknown_format_fails_closed():
    with pytest.raises(assets.AssetUploadError) as exc:
        assets.validate_asset(animation(), "unknown")
    assert exc.value.status_code == 415


def test_bounded_read_exact_limit_and_one_extra_byte():
    class Stream(io.BytesIO):
        def read(self, size=-1):
            assert 0 < size <= 1024 * 1024
            return super().read(size)

    assert assets.read_bounded(Stream(b"x" * 32), 32) == b"x" * 32
    with pytest.raises(assets.AssetUploadError) as exc:
        assets.read_bounded(Stream(b"x" * 33), 32)
    assert exc.value.status_code == 413


@pytest.fixture
def avatars(monkeypatch, tmp_path):
    monkeypatch.setattr("core.config.get_iris_home", lambda: tmp_path)
    monkeypatch.setattr(wizard, "get_iris_home", lambda: tmp_path)
    (tmp_path / "avatars").mkdir()
    for name in ("same.vrm", "os-same.vrm"):
        (tmp_path / "avatars" / name).write_bytes(avatar(0))
    cfg = IRISConfig()
    cfg.avatar_vrm_path = str(tmp_path / "avatars" / "os-same.vrm")
    cfg.save()
    broadcast = AsyncMock()
    monkeypatch.setattr(core, "broadcast_to_face", broadcast)
    return tmp_path, broadcast


def client(surface):
    return TestClient(
        core.app if surface == "core" else wizard.app,
        base_url="http://127.0.0.1",
        client=("127.0.0.1", 50123),
    )


def avatar_path(surface):
    return "/api/avatar/upload" if surface == "core" else "/api/upload-model"


@pytest.mark.parametrize("surface", ["core", "wizard"])
@pytest.mark.parametrize("version", [0, 1])
def test_both_avatar_endpoints_preserve_contract_and_validate_before_commit(
    avatars, surface, version
):
    root, broadcast = avatars
    response = client(surface).post(
        avatar_path(surface), files={"file": ("same.vrm", avatar(version))}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    name = "os-same.vrm" if surface == "core" else "same.vrm"
    assert (root / "avatars" / name).read_bytes() == avatar(version)
    assert body["size_bytes"] == len(avatar(version))
    if surface == "wizard":
        assert body["has_vrm_extension"] is True and body["needs_conversion"] is False
        assert body["path"] == str(root / "avatars" / name)
        broadcast.assert_not_called()
    else:
        assert body["avatar_url"] == "/avatars/os-same.vrm"
        broadcast.assert_awaited_once()


@pytest.mark.parametrize("surface", ["core", "wizard"])
@pytest.mark.parametrize(
    "filename,payload",
    [
        ("same.vrm", b""),
        ("same.vrm", b"bad.vrm"),
        ("same.vrm", animation()),
        ("same.vrm", avatar()[:-1]),
        ("/outside.vrm", avatar()),
        ("../outside.vrm", avatar()),
        (r"..\outside.vrm", avatar()),
        (r"C:\outside.vrm", avatar()),
        (r"\\server\outside.vrm", avatar()),
        ("C:outside.vrm", avatar()),
        ("same.unknown", avatar()),
    ],
)
def test_rejected_avatar_keeps_files_config_and_notifications_unchanged(
    avatars, surface, filename, payload
):
    root, broadcast = avatars
    before = {p.name: p.read_bytes() for p in (root / "avatars").iterdir()}
    config = (root / "config.json").read_bytes()
    response = client(surface).post(avatar_path(surface), files={"file": (filename, payload)})
    assert response.status_code == 400, response.text
    assert {p.name: p.read_bytes() for p in (root / "avatars").iterdir()} == before
    assert (root / "config.json").read_bytes() == config
    broadcast.assert_not_called()


@pytest.mark.parametrize("surface", ["core", "wizard"])
@pytest.mark.parametrize("alias", ["root", "target", "hardlink"])
def test_avatar_links_cannot_redirect_uploads(avatars, surface, alias, tmp_path):
    import os

    root, broadcast = avatars
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "private.vrm"
    sentinel.write_bytes(b"untouched")
    target = root / "avatars" / ("os-new.vrm" if surface == "core" else "new.vrm")
    if alias == "root":
        link = tmp_path / "home-alias"
        make_directory_alias(outside, link)
        # get_iris_home stays real; redirect only the managed avatars directory.
        (root / "avatars").rename(root / "previous-avatars")
        remove_directory_alias(link)
        link = root / "avatars"
        make_directory_alias(outside, link)
    elif alias == "hardlink":
        link = target
        os.link(sentinel, link)
    else:
        link = target
        make_directory_alias(outside, link)
    try:
        response = client(surface).post(avatar_path(surface), files={"file": ("new.vrm", avatar())})
        assert response.status_code == 409, response.text
        assert sentinel.read_bytes() == b"untouched" and list(outside.iterdir()) == [sentinel]
        broadcast.assert_not_called()
    finally:
        link.unlink() if alias == "hardlink" else remove_directory_alias(link)


@pytest.mark.parametrize("surface", ["core", "wizard"])
def test_unknown_http_method_never_changes_avatar(avatars, surface):
    root, broadcast = avatars
    response = client(surface).request("BREW", avatar_path(surface), content=b"ignored")
    assert response.status_code == 405
    assert (root / "avatars" / "os-same.vrm").read_bytes() == avatar(0)
    broadcast.assert_not_called()


@pytest.mark.parametrize("surface", ["core", "wizard"])
def test_avatar_oversize_has_no_side_effects_and_exact_limit_is_accepted(
    avatars, monkeypatch, surface
):
    from core import upload_routes

    root, broadcast = avatars
    payload = avatar()
    monkeypatch.setitem(upload_routes.UPLOAD_LIMITS, avatar_path(surface), (len(payload), None))
    config = (root / "config.json").read_bytes()
    response = client(surface).post(
        avatar_path(surface), files={"file": ("same.vrm", payload + b"x")}
    )
    assert response.status_code == 413
    assert (root / "config.json").read_bytes() == config
    assert (root / "avatars" / "same.vrm").read_bytes() == avatar(0)
    assert (root / "avatars" / "os-same.vrm").read_bytes() == avatar(0)
    broadcast.assert_not_called()
    response = client(surface).post(avatar_path(surface), files={"file": ("same.vrm", payload)})
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("surface", ["core", "wizard"])
def test_a01_authorization_runs_before_upload_parsing(avatars, monkeypatch, surface):
    from core.upload_routes import BoundedMultipartParser

    def fail(*args, **kwargs):
        raise AssertionError("unauthorized body parsed")

    monkeypatch.setattr(BoundedMultipartParser, "parse", fail)
    response = client(surface).post(
        avatar_path(surface),
        headers={"host": "evil.example"},
        files={"file": ("same.vrm", avatar())},
    )
    assert response.status_code == 403
    avatars[1].assert_not_called()


def test_atomic_avatar_write_cleans_staging_on_replace_failure(tmp_path, monkeypatch):
    target = tmp_path / "same.vrm"
    target.write_bytes(b"previous")

    def fail(*args):
        raise OSError("disk failure")

    monkeypatch.setattr(assets.os, "replace", fail)
    with pytest.raises(OSError):
        assets.atomic_avatar_write(tmp_path, "same.vrm", avatar())
    assert target.read_bytes() == b"previous"
    assert list(tmp_path.iterdir()) == [target]


def test_factory_pose_files_pass_without_any_byte_changes():
    root = Path(__file__).parents[1] / "face" / "animations"
    names = json.loads((root / "manifest.json").read_text(encoding="utf-8"))["clips"].values()
    for filename in set(names):
        if filename.endswith(".vrma"):
            original = (root / filename).read_bytes()
            assets.validate_asset(original, "vrma")
            assert (root / filename).read_bytes() == original
