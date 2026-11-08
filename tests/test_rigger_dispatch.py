"""A03 backend selection and Blender invocation; no optional binaries needed."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from core import rigger


@pytest.fixture
def engine():
    instance = rigger.AutoRigger()
    instance._anymate_available = True
    instance._blender_path = "fake-blender"
    for name in ("anymate", "blender", "heuristic"):
        setattr(instance, f"_rig_{name}", AsyncMock(return_value={"status": "success"}))
    return instance


@pytest.mark.asyncio
@pytest.mark.parametrize("method,extension", [
    ("unknown", ".glb"), ("AUTO", ".glb"), ("", ".glb"),
    ("anymate", ".obj"), ("anymate", ".gltf"), ("blender", ".ply"),
    ("heuristic", ".fbx"), ("auto", ".dae"), ("auto", ".fbx"), ("auto", ".exe"),
])
async def test_invalid_method_or_format_never_falls_back(engine, tmp_path, method, extension):
    source = tmp_path / f"model{extension}"
    source.write_bytes(b"placeholder")
    result = await engine.auto_rig(str(source), method=method)
    assert result["status"] == "error"
    for name in ("anymate", "blender", "heuristic"):
        getattr(engine, f"_rig_{name}").assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("extension,anymate,blender,expected", [
    (".glb", True, True, "anymate"), (".glb", False, True, "blender"),
    (".glb", False, False, "heuristic"), (".obj", True, True, "blender"),
    (".obj", True, False, "heuristic"), (".ply", True, True, "heuristic"),
    (".GLTF", True, True, "blender"), (".stl", True, False, "heuristic"),
])
async def test_auto_selects_available_backend_for_format(
    engine, tmp_path, extension, anymate, blender, expected,
):
    engine._anymate_available = anymate
    engine._blender_path = "fake-blender" if blender else None
    source, output = tmp_path / f"model{extension}", tmp_path / "output.glb"
    source.write_bytes(b"placeholder")
    assert (await engine.auto_rig(str(source), str(output)))["status"] == "success"
    for name in ("anymate", "blender", "heuristic"):
        backend = getattr(engine, f"_rig_{name}")
        if name == expected:
            backend.assert_awaited_once_with(str(source), str(output))
        else:
            backend.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["anymate", "blender"])
async def test_explicit_unavailable_backend_is_an_error(engine, tmp_path, method):
    engine._anymate_available = False
    engine._blender_path = None
    source = tmp_path / "model.glb"
    source.write_bytes(b"placeholder")
    assert (await engine.auto_rig(str(source), method=method))["status"] == "error"
    engine._rig_heuristic.assert_not_awaited()
    getattr(engine, f"_rig_{method}").assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "exit_code", "timeout", "no_result"])
async def test_blender_script_is_private_arguments_are_separate_and_failure_is_not_success(
    tmp_path, monkeypatch, failure,
):
    engine = rigger.AutoRigger()
    engine._blender_path = "fake-blender.exe"
    job = tmp_path.resolve() / "private job"
    job.mkdir()
    source, output = job / "input.glb", job / "output.glb"
    source.write_bytes(b"placeholder")
    scripts = []

    def execute(argv, **kwargs):
        assert argv[0] == "fake-blender.exe"
        assert argv[-3:] == ["--", str(source), str(output)]
        assert "--factory-startup" in argv and "--disable-autoexec" in argv
        assert not kwargs.get("shell")
        assert kwargs["cwd"] == job and kwargs["timeout"] == 120
        script = Path(argv[argv.index("--python") + 1])
        assert script.parent == job and script.is_file()
        assert "bpy.ops.export_scene.gltf" in script.read_text(encoding="utf-8")
        scripts.append(script)
        if failure == "timeout":
            raise subprocess.TimeoutExpired(argv, 120)
        return subprocess.CompletedProcess(
            argv, 1 if failure == "exit_code" else 0,
            stdout="no result" if failure == "no_result" else json.dumps({"status": "success"}),
            stderr="",
        )

    monkeypatch.setattr(rigger.subprocess, "run", execute)
    result = await engine._rig_blender(str(source), str(output))
    assert result["status"] == ("error" if failure else "success")
    assert len(scripts) == 1 and not scripts[0].exists()
    assert source.read_bytes() == b"placeholder"
