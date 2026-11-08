"""Private codec selection and package immutability for Hermes subprocesses."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from core import hermes_runtime
from core.runtime_descriptor import RuntimeDescriptorError, load_runtime_descriptor
from tests.runtime_descriptor_fixtures import write_runtime_fixture


def test_packaged_environment_prefers_owned_decoder_and_blocks_lazy_install(tmp_path, monkeypatch):
    descriptor, _ = write_runtime_fixture(tmp_path / "Iris instalación ñ")
    runtime = load_runtime_descriptor(descriptor)
    monkeypatch.setattr(hermes_runtime, "get_runtime", lambda: runtime)
    monkeypatch.setattr(hermes_runtime, "get_hermes_home", lambda: tmp_path / "profile")
    monkeypatch.setenv("PATH", str(tmp_path / "unrelated Python FFmpeg"))
    monkeypatch.setenv("HERMES_DISABLE_LAZY_INSTALLS", "0")
    monkeypatch.setenv("HERMES_LAZY_INSTALL_TARGET", str(tmp_path / "foreign target"))
    monkeypatch.setenv("hermes_lazy_install_target", str(tmp_path / "another target"))
    environment = hermes_runtime.hermes_subprocess_env()
    paths = environment["PATH"].split(os.pathsep)
    assert paths[:2] == [str(runtime.base / "tools/audio/bin"), str(runtime.hermes.python.parent)]
    assert str(tmp_path / "unrelated Python FFmpeg") not in paths
    assert environment["HERMES_DISABLE_LAZY_INSTALLS"] == "1"
    assert environment["HERMES_GIT_BASH_PATH"] == str(runtime.base / "tools/shell/usr/bin/bash.exe")
    assert paths[2] == str(runtime.base / "tools/shell/usr/bin")
    assert not any(key.upper() == "HERMES_LAZY_INSTALL_TARGET" for key in environment)


def test_missing_private_decoder_does_not_inherit_foreign_path(tmp_path, monkeypatch):
    descriptor, _ = write_runtime_fixture(tmp_path / "Iris")
    runtime = load_runtime_descriptor(descriptor)
    (runtime.base / "tools/audio/bin/ffmpeg.exe").unlink()
    monkeypatch.setattr(hermes_runtime, "get_runtime", lambda: runtime)
    monkeypatch.setenv("PATH", str(tmp_path / "foreign FFmpeg"))
    with pytest.raises(RuntimeDescriptorError):
        hermes_runtime.hermes_subprocess_env()


def test_missing_private_shell_never_falls_back_to_system_git(tmp_path, monkeypatch):
    descriptor, _ = write_runtime_fixture(tmp_path / "Iris")
    runtime = load_runtime_descriptor(descriptor)
    (runtime.base / "tools/shell/usr/bin/bash.exe").unlink()
    monkeypatch.setattr(hermes_runtime, "get_runtime", lambda: runtime)
    monkeypatch.setenv("HERMES_GIT_BASH_PATH", str(tmp_path / "foreign/bash.exe"))
    with pytest.raises(RuntimeDescriptorError, match="consola privada"):
        hermes_runtime.hermes_subprocess_env()


def test_development_preserves_external_tools_and_optional_install_policy(tmp_path, monkeypatch):
    descriptor, _ = write_runtime_fixture(tmp_path / "development", mode="development")
    runtime = load_runtime_descriptor(descriptor)
    monkeypatch.setattr(hermes_runtime, "get_runtime", lambda: runtime)
    monkeypatch.setattr(hermes_runtime, "get_hermes_home", lambda: tmp_path / "profile")
    monkeypatch.setenv("PATH", str(tmp_path / "developer tools"))
    monkeypatch.setenv("HERMES_DISABLE_LAZY_INSTALLS", "0")
    monkeypatch.setenv("HERMES_LAZY_INSTALL_TARGET", str(tmp_path / "optional target"))
    environment = hermes_runtime.hermes_subprocess_env()
    assert environment["PATH"] == str(tmp_path / "developer tools")
    assert environment["HERMES_DISABLE_LAZY_INSTALLS"] == "0"
    assert environment["HERMES_LAZY_INSTALL_TARGET"] == str(tmp_path / "optional target")


def test_iris_base_excludes_duplicate_stt_but_keeps_explicit_dependencies():
    import tomllib

    project = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text("utf8"))["project"]
    dependencies = {item.split(">=")[0].casefold() for item in project["dependencies"]}
    assert "faster-whisper" not in dependencies
    assert {"pyyaml", "sounddevice", "numpy", "edge-tts", "pydub"} <= dependencies
    # A08.5: Hermes owns STT; the consumer-less Iris extra was removed.
    assert "faster-whisper" not in project["optional-dependencies"]
