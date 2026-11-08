"""A03: exercise the real API without starting optional model converters."""

from __future__ import annotations

import asyncio
import json
import os
import struct
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient

from core import rigger
from core import rigging_jobs as jobs
from wizard import app as wizard


def glb(document=None):
    body = json.dumps(document or {"asset": {"version": "2.0"}}).encode()
    body += b" " * (-len(body) % 4)
    return struct.pack("<4sIIII", b"glTF", 2, 20 + len(body), len(body), 0x4E4F534A) + body


def directory_link(link, target):
    """Real Windows junctions require no symlink privilege; never skip this."""
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


@pytest.fixture
def rig(monkeypatch, tmp_path):
    home = tmp_path.resolve() / "profile"
    avatars = home / "avatars"
    avatars.mkdir(parents=True)
    (avatars / "model.glb").write_bytes(glb())
    outside = tmp_path.resolve() / "avatars-private"
    outside.mkdir()
    sentinel = outside / "private.glb"
    sentinel.write_bytes(b"DO NOT READ OR MODIFY")
    monkeypatch.setattr(wizard, "get_iris_home", lambda: home)

    async def convert(source, output, method):
        Path(output).write_bytes(glb())
        metadata = Path(output).with_suffix(".bones.json")
        metadata.write_text('{"bones": []}', encoding="utf-8")
        return {
            "status": "success", "method": method, "output_path": output,
            "metadata_path": str(metadata), "bones_found": 0,
        }

    fake = SimpleNamespace(initialize=AsyncMock(), auto_rig=AsyncMock(side_effect=convert))
    factory = Mock(return_value=fake)
    monkeypatch.setattr(rigger, "AutoRigger", factory)
    client = TestClient(
        wizard.app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 50000),
    )
    yield SimpleNamespace(
        home=home, avatars=avatars, outside=outside, sentinel=sentinel, fake=fake,
        factory=factory, client=client, convert=convert,
    )
    client.close()
    assert sentinel.read_bytes() == b"DO NOT READ OR MODIFY"


def request(rig, filename="model.glb", method="auto"):
    return rig.client.post("/api/rig-model", params={"filename": filename, "method": method})


def not_started(rig):
    rig.factory.assert_not_called()
    rig.fake.initialize.assert_not_awaited()
    rig.fake.auto_rig.assert_not_awaited()


def cleaned(rig):
    assert not list((rig.home / ".rig-jobs").glob("job-*"))


@pytest.mark.parametrize("filename", ["model.glb", "muñeca & amiga #1.GLB", "角色.glb"])
def test_valid_basename_uses_private_copy_and_publishes_only_outputs(rig, filename):
    (rig.avatars / filename).write_bytes(glb())
    response = request(rig, filename)
    assert response.status_code == 200, response.text
    rig.fake.initialize.assert_awaited_once()
    source, output, method = rig.fake.auto_rig.call_args.args
    assert method == "auto"
    assert Path(source).parent.parent == rig.home / ".rig-jobs"
    assert Path(output).parent == Path(source).parent
    assert Path(source) != rig.avatars / filename
    body = response.json()
    destination = Path(body["output_path"])
    assert destination.parent.parent == rig.avatars
    assert destination.parent.name.startswith("rigged_")
    assert set(p.name for p in destination.parent.iterdir()) == {"model.glb", "model.bones.json"}
    assert destination.read_bytes() == glb()
    assert json.loads(Path(body["metadata_path"]).read_bytes()) == {"bones": []}
    assert (rig.avatars / filename).read_bytes() == glb()
    cleaned(rig)


@pytest.mark.parametrize("filename", [
    "/tmp/private.glb", "../private.glb", "../../avatars-private/private.glb",
    r"..\private.glb", "folder/model.glb", r"folder\model.glb",
    r"C:\private.glb", "C:/private.glb", "C:private.glb",
    r"\\server\share\private.glb", "//server/share/private.glb",
    r"\\?\C:\private.glb", r"\private.glb", "model.glb:secret", "model:secret.glb",
    "model..glb", "model.glb.", " model.glb", "model.glb ", "NUL.glb", "COM1.glb",
    "\x00model.glb", "model\n.glb", "", ".hidden.glb",
])
def test_path_syntax_is_rejected_before_constructing_converter(rig, filename):
    assert request(rig, filename).status_code == 400
    not_started(rig)


def test_existing_absolute_outside_file_cannot_be_rigged(rig):
    assert request(rig, str(rig.sentinel)).status_code == 400
    not_started(rig)


@pytest.mark.parametrize("method", ["unknown", "AUTO", "", "../heuristic", "blender --python evil"])
def test_unknown_method_is_http_validation_error(rig, method):
    assert request(rig, method=method).status_code == 422
    not_started(rig)


@pytest.mark.parametrize("method", ["GET", "PUT", "BREW"])
def test_unsupported_http_method_never_runs_converter(rig, method):
    assert rig.client.request(method, "/api/rig-model?filename=model.glb").status_code == 405
    not_started(rig)


@pytest.mark.parametrize("kind,status", [
    ("missing", 404), ("directory", 409), ("extension", 415), ("empty", 400),
    ("broken_glb", 400), ("broken_gltf", 400), ("fbx", 415), ("dae", 415),
])
def test_invalid_file_never_initializes_converter(rig, kind, status):
    filename = "test.glb"
    if kind == "directory":
        (rig.avatars / filename).mkdir()
    elif kind != "missing":
        filename = {"extension": "test.exe", "broken_gltf": "test.gltf"}.get(
            kind, f"test.{kind}" if kind in {"fbx", "dae"} else filename,
        )
        (rig.avatars / filename).write_bytes(b"" if kind == "empty" else b"invalid")
    assert request(rig, filename).status_code == status
    not_started(rig)


@pytest.mark.parametrize("where", ["input", "avatars", "jobs", "destination"])
def test_real_junction_or_symlink_cannot_redirect_managed_paths(rig, monkeypatch, where):
    if where == "input":
        (rig.avatars / "model.glb").unlink()
        directory_link(rig.avatars / "model.glb", rig.outside)
    elif where == "avatars":
        alias_home = rig.home / "alias"
        alias_home.mkdir()
        directory_link(alias_home / "avatars", rig.outside)
        monkeypatch.setattr(wizard, "get_iris_home", lambda: alias_home)
    elif where == "jobs":
        directory_link(rig.home / ".rig-jobs", rig.outside)
    else:
        monkeypatch.setattr(jobs, "_new_result_name", lambda: "rigged_collision")
        directory_link(rig.avatars / "rigged_collision", rig.outside)
    assert request(rig).status_code == 409
    not_started(rig)
    assert sorted(p.name for p in rig.outside.iterdir()) == ["private.glb"]


def test_file_alias_to_outside_is_rejected(rig):
    link = rig.avatars / "alias.glb"
    if os.name == "nt":
        os.link(rig.sentinel, link)
    else:
        link.symlink_to(rig.sentinel)
    assert request(rig, link.name).status_code == 409
    not_started(rig)


def test_symbolic_link_guard_runs_before_open(rig, monkeypatch):
    # Exercise the explicit symlink guard even on Windows without that privilege.
    original = Path.is_symlink
    model = rig.avatars / "model.glb"
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == model or original(path))
    assert request(rig).status_code == 409
    not_started(rig)


@pytest.mark.parametrize("extension", ["gltf", "glb"])
@pytest.mark.parametrize("uri", [
    "../private.bin", r"..\private.png", "C:/private.bin", r"C:\private.png",
    "/private.bin", "file:///private.bin", "https://example.invalid/private.bin",
    r"\\server\share\private.bin", "model.bin", "data:invalid", None,
])
def test_external_gltf_resources_never_reach_converter(rig, extension, uri):
    document = {"asset": {"version": "2.0"}, "buffers": [{"uri": uri}]}
    raw = glb(document) if extension == "glb" else json.dumps(document).encode()
    (rig.avatars / f"model.{extension}").write_bytes(raw)
    assert request(rig, f"model.{extension}").status_code == 415
    not_started(rig)


def test_nested_extension_image_uri_is_also_rejected(rig):
    document = {"asset": {"version": "2.0"}, "extensions": {"custom": {"images": [
        {"uri": "../private.png"},
    ]}}}
    (rig.avatars / "model.glb").write_bytes(glb(document))
    assert request(rig).status_code == 415
    not_started(rig)


@pytest.mark.parametrize("extension,content", [
    ("gltf", json.dumps({"asset": {"version": "2.0"}, "buffers": [
        {"uri": "data:application/octet-stream;base64,AAAA"},
    ]}).encode()),
    ("obj", b"v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n"),
    ("ply", b"ply\nformat ascii 1.0\nelement vertex 0\nend_header\n"),
    ("stl", b"solid example\nendsolid example\n"),
])
def test_self_contained_formats_are_copied_without_changing_bytes(rig, extension, content):
    async def convert(source, output, method):
        assert Path(source).read_bytes() == content
        return await rig.convert(source, output, method)

    rig.fake.auto_rig.side_effect = convert
    (rig.avatars / f"model.{extension}").write_bytes(content)
    assert request(rig, f"model.{extension}").status_code == 200
    cleaned(rig)


@pytest.mark.parametrize("extension,content", [
    ("obj", b"mtllib ../private.mtl\n"),
    ("obj", b"mtl\\\nlib private.mtl\n"),
    ("obj", b"call C:/private.obj\n"),
    ("obj", b"maplib private.map\n"),
    ("ply", b"ply\ncomment TextureFile ../private.png\nend_header\n"),
    ("ply", b"ply\ncomment texture_file C:/private.png\nend_header\n"),
])
def test_obj_and_ply_sidecars_are_rejected_with_explanation(rig, extension, content):
    (rig.avatars / f"model.{extension}").write_bytes(content)
    response = request(rig, f"model.{extension}")
    assert response.status_code == 415
    assert "GLB" in response.json()["message"]
    not_started(rig)


@pytest.mark.parametrize("kind", ["file", "directory"])
def test_existing_generated_destination_is_not_overwritten(rig, monkeypatch, kind):
    monkeypatch.setattr(jobs, "_new_result_name", lambda: "rigged_collision")
    collision = rig.avatars / "rigged_collision"
    if kind == "file":
        collision.write_bytes(b"old")
    else:
        collision.mkdir()
        (collision / "model.glb").write_bytes(b"old")
    assert request(rig).status_code == 409
    assert (collision if kind == "file" else collision / "model.glb").read_bytes() == b"old"
    not_started(rig)


def test_collision_created_during_conversion_is_preserved(rig, monkeypatch):
    monkeypatch.setattr(jobs, "_new_result_name", lambda: "rigged_collision")

    async def convert(source, output, method):
        result = await rig.convert(source, output, method)
        (rig.avatars / "rigged_collision").mkdir()
        return result

    rig.fake.auto_rig.side_effect = convert
    assert request(rig).status_code == 409
    assert list((rig.avatars / "rigged_collision").iterdir()) == []
    cleaned(rig)


@pytest.mark.parametrize("failure", [
    "error", "exception", "initialize", "missing", "outside_output", "outside_metadata",
    "bad_metadata", "bad_output", "output_hardlink", "metadata_hardlink", "rename",
])
def test_failure_never_publishes_or_cleans_other_jobs(rig, monkeypatch, failure):
    unrelated = rig.home / ".rig-jobs" / "keep-another-job"
    unrelated.mkdir(parents=True)
    (unrelated / "keep.txt").write_text("keep")

    async def convert(source, output, method):
        result = await rig.convert(source, output, method)
        # Prove job cleanup removes only the link, never its outside target.
        directory_link(Path(source).parent / "outside-link", rig.outside)
        if failure == "error":
            return {"status": "error", "message": "fake failure"}
        if failure == "exception":
            raise RuntimeError("fake exception")
        if failure == "missing":
            Path(output).unlink()
        elif failure == "outside_output":
            result["output_path"] = str(rig.sentinel)
        elif failure == "outside_metadata":
            result["metadata_path"] = str(rig.sentinel)
        elif failure == "bad_metadata":
            Path(result["metadata_path"]).write_bytes(b"not json")
        elif failure == "bad_output":
            Path(output).write_bytes(b"not a GLB")
        elif failure in {"output_hardlink", "metadata_hardlink"}:
            path = Path(output if failure == "output_hardlink" else result["metadata_path"])
            path.unlink()
            os.link(rig.sentinel, path)
        return result

    rig.fake.auto_rig.side_effect = convert
    if failure == "initialize":
        rig.fake.initialize.side_effect = RuntimeError("fake init failure")
    if failure == "rename":
        monkeypatch.setattr(jobs.os, "rename", Mock(side_effect=OSError("disk error")))
    response = request(rig)
    assert response.status_code >= 400, response.text
    assert response.json()["status"] == "error"
    assert sorted(p.name for p in rig.avatars.iterdir()) == ["model.glb"]
    assert (rig.avatars / "model.glb").read_bytes() == glb()
    assert (unrelated / "keep.txt").read_text() == "keep"
    assert sorted(p.name for p in rig.outside.iterdir()) == ["private.glb"]
    cleaned(rig)


@pytest.mark.asyncio
async def test_cancelled_request_waits_for_converter_before_cleanup(rig):
    started, finish = asyncio.Event(), asyncio.Event()

    async def convert(source, output, method):
        started.set()
        await finish.wait()
        assert Path(source).read_bytes() == glb()
        return await rig.convert(source, output, method)

    rig.fake.auto_rig.side_effect = convert
    task = asyncio.create_task(jobs.rig_managed_model(rig.avatars, "model.glb", "auto"))
    await asyncio.wait_for(started.wait(), timeout=5)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)
    cleaned(rig)
    assert sorted(p.name for p in rig.avatars.iterdir()) == ["model.glb"]


@pytest.mark.asyncio
async def test_concurrent_jobs_have_disjoint_private_and_public_paths(rig):
    results = await asyncio.gather(*[
        jobs.rig_managed_model(rig.avatars, "model.glb", "auto") for _ in range(4)
    ])
    sources = {call.args[0] for call in rig.fake.auto_rig.call_args_list}
    destinations = {result["output_path"] for result in results}
    assert len(sources) == len(destinations) == 4
    assert all(Path(path).read_bytes() == glb() for path in destinations)
    cleaned(rig)


@pytest.mark.parametrize("filename,method", [("model.obj", "anymate"), ("model.ply", "blender")])
def test_http_incompatible_format_is_rejected_before_initialization(rig, filename, method):
    (rig.avatars / filename).write_bytes(b"placeholder")
    assert request(rig, filename, method).status_code == 415
    not_started(rig)


def test_basename_is_never_resolved_against_cwd(rig, monkeypatch):
    monkeypatch.chdir(rig.outside)
    assert request(rig, "private.glb").status_code == 404
    not_started(rig)


def test_copy_failure_cleans_own_directory_without_initializing(rig, monkeypatch):
    original = Path.open

    def fail_copy(path, mode="r", *args, **kwargs):
        if mode == "xb" and path.parent.parent == rig.home / ".rig-jobs":
            raise OSError("fake disk full")
        return original(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_copy)
    assert request(rig).status_code == 503
    not_started(rig)
    cleaned(rig)


def test_success_without_optional_metadata_and_extra_files_not_published(rig):
    async def convert(source, output, method):
        result = await rig.convert(source, output, method)
        result.pop("metadata_path")
        (Path(source).parent / "private-log.txt").write_text("private")
        result["private_path"] = str(rig.sentinel)
        return result

    rig.fake.auto_rig.side_effect = convert
    response = request(rig)
    assert response.status_code == 200
    assert "metadata_path" not in response.json() and "private_path" not in response.json()
    output = Path(response.json()["output_path"])
    assert sorted(p.name for p in output.parent.iterdir()) == ["model.glb"]
    cleaned(rig)


def test_external_resources_in_generated_output_are_not_published(rig):
    async def convert(source, output, method):
        result = await rig.convert(source, output, method)
        Path(output).write_bytes(glb({
            "asset": {"version": "2.0"}, "images": [{"uri": "../private.png"}],
        }))
        return result

    rig.fake.auto_rig.side_effect = convert
    assert request(rig).status_code == 415
    assert sorted(p.name for p in rig.avatars.iterdir()) == ["model.glb"]
    cleaned(rig)


def test_published_avatar_can_be_served_by_core(rig, monkeypatch):
    from core import config as config_module
    from core import websocket_server as core
    from core.config import IRISConfig

    output = request(rig).json()["output_path"]
    cfg = IRISConfig.model_construct(avatar_vrm_path=output, lan_access=False)
    monkeypatch.setattr(IRISConfig, "load", classmethod(lambda cls: cfg))
    monkeypatch.setattr(config_module, "get_iris_home", lambda: rig.home)
    if os.name == "nt":
        assert core._public_avatar_url(output) == "/api/avatar/current"
    client = TestClient(
        core.app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 50000),
    )
    try:
        response = client.get("/api/avatar/current")
    finally:
        client.close()
    assert response.status_code == 200
    assert response.content == glb()
