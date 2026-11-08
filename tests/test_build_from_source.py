from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts import build_from_source as build
from scripts import build_personal_native as native


def test_plan_is_side_effect_free_and_does_not_run_tools(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        subprocess, "run", lambda *args, **kwargs: pytest.fail("plan executed a tool")
    )
    destination = tmp_path / "never-created"
    assert build.main(["--plan", "--work", str(destination)]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert not plan["downloads_iris_installer"] and not plan["signs_or_publishes"]
    assert not destination.exists()


def source_fixture(root):
    files = {
        ".gitignore": b".env\nartifacts/\n",
        "core/engine.py": b"answer = 42\n",
        "core/credentials_personal.json": b"private configuration",
        ".env": b"private environment",
        "artifacts/private.txt": b"private evidence",
        "desktop/installer/qa/private.txt": b"private capture",
    }
    policy = {
        "main_roots": ["core", "desktop", "tools"],
        "main_files": [".gitignore"],
        "excluded_prefixes": ["desktop/installer/qa/"],
        "secret_exceptions": [],
    }
    files["tools/repositories/exports.json"] = json.dumps(policy).encode()
    for name, data in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def test_frozen_source_works_without_source_git_and_excludes_private_files(tmp_path):
    git = shutil.which("git")
    if not git:
        pytest.skip("Git is a documented build prerequisite")
    source, work = tmp_path / "source", tmp_path / "build"
    source.mkdir()
    work.mkdir()
    source_fixture(source)
    frozen, tree = build.freeze_source(source, work, Path(git))
    assert not (source / ".git").exists()
    assert (frozen / "core/engine.py").read_bytes() == b"answer = 42\n"
    assert not (frozen / "core/credentials_personal.json").exists()
    assert not (frozen / ".env").exists()
    assert not (frozen / "artifacts").exists()
    assert not (frozen / "desktop/installer/qa/private.txt").exists()
    (source / "core/engine.py").write_bytes(b"changed after snapshot")
    assert (frozen / "core/engine.py").read_bytes() == b"answer = 42\n"
    records = json.loads((work / "source-files.json").read_text())
    assert records["tree_sha256"] == tree
    assert all(
        hashlib.sha256((frozen / row["path"]).read_bytes()).hexdigest() == row["sha256"]
        for row in records["files"]
    )


def test_secret_in_source_stops_before_creating_the_build_source(tmp_path):
    git = shutil.which("git")
    if not git:
        pytest.skip("Git is a documented build prerequisite")
    source, work = tmp_path / "source", tmp_path / "build"
    source.mkdir()
    work.mkdir()
    source_fixture(source)
    (source / "core/engine.py").write_text('token = "sk-' + "aB37" * 12 + '"')
    with pytest.raises(ValueError, match="Posibles secretos"):
        build.freeze_source(source, work, Path(git))
    assert not (work / "source").exists()


def test_failed_step_is_recorded_without_claiming_completion(tmp_path, monkeypatch):
    (tmp_path / "logs").mkdir()
    runner = build.Builder(tmp_path, tmp_path, {})
    runner.src = tmp_path
    monkeypatch.setattr(
        subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 17)
    )
    with pytest.raises(ValueError, match="Falló python-tests"):
        runner.run("python-tests", ["unused"])
    record = json.loads((tmp_path / "build-result.json").read_text())
    assert record["completed"] is False
    assert record["steps"][0]["exit_code"] == 17
    assert record["steps"][0]["log"] == "logs/python-tests.log"


def test_builder_does_not_inherit_an_installed_profile_or_foreign_build_target(
    tmp_path, monkeypatch
):
    for key in (
        "IRIS_RUNTIME_DESCRIPTOR",
        "HERMES_HOME",
        "IRIS_HOME",
        "PYTHONPATH",
        "CARGO_TARGET_DIR",
    ):
        monkeypatch.setenv(key, "personal-installed-state")
    runner = build.Builder(tmp_path, tmp_path, {})
    assert runner.env["IRIS_HOME"] == str(tmp_path / "test-profile/iris")
    assert runner.env["HERMES_HOME"] == str(tmp_path / "test-profile/hermes")
    assert "IRIS_RUNTIME_DESCRIPTOR" not in runner.env
    assert "PYTHONPATH" not in runner.env
    assert "CARGO_TARGET_DIR" not in runner.env


def test_check_does_not_start_a_build_when_a_requirement_is_missing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(build, "tool_paths", lambda *args: {"node": None})
    monkeypatch.setattr(build, "preflight", lambda *args: ["Falta Node"])
    monkeypatch.setattr(
        build.Builder, "build", lambda *args: pytest.fail("started a blocked build")
    )
    assert build.main(["--build", "--work", str(tmp_path / "new")]) == 1
    assert "Falta Node" in capsys.readouterr().out
    assert not (tmp_path / "new").exists()


def test_native_pipeline_uses_generated_outputs_and_preserves_approved_pin(tmp_path, monkeypatch):
    """Exercise the filesystem handoff between real CLI producer layouts and runtime assembly."""
    import copy

    pin = {
        "iris": {"version": "1.0.0"},
        "audio": {"filename": "iris-native-messaging-audio.zip", "sha256": "approved-audio"},
        "local_wheels": [{"package": "av", "filename": "av.whl", "sha256": "approved-wheel"}],
        "shell": {
            "filename": "shell.zip",
            "sha256": "approved-shell",
            "source_filename": "shell-sources.zip",
            "source_sha256": "approved-sources",
        },
        "python": {"filename": "iris-python.tar.gz", "url": "https://example.invalid/iris"},
        "hermes_python": {
            "filename": "hermes-python.tar.gz",
            "url": "https://example.invalid/hermes",
        },
        "hermes": {
            "source_url": "https://example.invalid/source",
            "source_archive_filename": "hermes.tar.gz",
            "source_archive_sha256": "approved-hermes",
        },
    }
    approved = copy.deepcopy(pin)
    documents = {
        "runtime_sources.json": pin,
        "audio_build_sources.json": {
            "source": {"url": "https://example.invalid/ffmpeg"},
            "toolchain": {"url": "https://example.invalid/compiler.exe"},
            "notices": [],
        },
        "pyav_build_sources.json": {
            "python": pin["python"],
            "source": {"filename": "av.tar.gz"},
            "tools": [],
        },
        "messaging_audio_build_sources.json": {
            "codecs": {"opus": {"filename": "opus.tar.gz"}, "lame": {"filename": "lame.tar.gz"}}
        },
    }
    monkeypatch.setattr(native, "document", lambda name: copy.deepcopy(documents[name]))

    def cached(cache, spec, *, offline):
        path = cache / spec["filename"]
        path.write_bytes(spec["filename"].encode())
        return path

    monkeypatch.setattr(native, "cached_archive", cached)
    work = tmp_path / "native"
    calls = []

    def run(command, **kwargs):
        command = list(map(str, command))
        calls.append(command)
        if command[0] == "fake-7zip":
            path = work / "compiler/w64devkit/bin/gcc.exe"
            path.parent.mkdir(parents=True)
            path.write_bytes(b"compiler")
        else:
            module = command[3]
            if module.endswith("build_messaging_audio"):
                outputs = {work / "audio/iris-native-messaging-audio.zip": b"rebuilt audio"}
            elif module.endswith("build_pyav_runtime"):
                outputs = {work / "ffmpeg/package/bin/avcodec.dll": b"rebuilt sdk"}
            elif module.endswith("build_pyav_wheel"):
                assert Path(command[command.index("--ffmpeg-sdk") + 1]).is_dir()
                outputs = {work / "pyav/dist/av.whl": b"rebuilt wheel"}
            else:
                assert module.endswith("build_shell_runtime")
                outputs = {
                    work / "shell/shell.zip": b"rebuilt shell",
                    work / "shell/shell-sources.zip": b"corresponding sources",
                }
            for path, content in outputs.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", run)
    result = native.build(
        work, tmp_path / "downloads", tmp_path / "msvc", tmp_path / "sdk", Path("fake-7zip"), 4
    )
    actual = json.loads(result.read_text())
    assert actual["audio"]["sha256"] == hashlib.sha256(b"rebuilt audio").hexdigest()
    assert actual["local_wheels"][0]["sha256"] == hashlib.sha256(b"rebuilt wheel").hexdigest()
    assert (work / "runtime-inputs/shell-sources.zip").read_bytes() == b"corresponding sources"
    assert (work / "runtime-inputs/iris-python.tar.gz").is_file()
    assert documents["runtime_sources.json"] == approved
    assert len(calls) == 5
