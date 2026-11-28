"""Hermes transcribes in IRIS' language, not in its built-in English default.

Hermes 0.23 ships ``stt.language: en`` in its defaults; a profile that never set the key decoded
every recording as English (user log, 2026-11-27: 19 of 19 clips ``lang=en``; «Hola Iris, ¿qué
tal?» came back as "Hello Iris, how are you?"). Synthetic profiles only; no real Hermes or audio.
"""
import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import yaml

from core.config import IRISConfig
from core.hermes_wake import HermesGatewayError, HermesWakeRuntime, iris_stt_language


@pytest.fixture
def hermes_home(tmp_path, monkeypatch):
    monkeypatch.setenv("IRIS_HOME", str(tmp_path / "iris"))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    monkeypatch.delenv("IRIS_RUNTIME_DESCRIPTOR", raising=False)
    home = tmp_path / "hermes"
    home.mkdir()
    return home


def iris_language(monkeypatch, value):
    cfg = IRISConfig.model_construct(language=value)
    monkeypatch.setattr(IRISConfig, "load", classmethod(lambda cls: cfg))


# ── The writer (wizard.hermes_config.ensure_stt_language_default) ──

@pytest.mark.real_stt_language_default
@pytest.mark.parametrize("text, result, language", [
    ("stt:\n  streaming: false\n", "written", "es"),       # the user's profile on 2026-11-27
    ("model:\n  default: x\n", "written", "es"),
    ("", "written", "es"),
    ("stt:\n  language: fr\n", "kept", "fr"),
    ("stt:\n  language: ''\n", "kept", ""),                 # explicit auto-detect
    ("stt:\n  language: en\n", "kept", "en"),
])
def test_language_is_written_once_and_an_explicit_choice_is_kept(hermes_home, text, result, language):
    from wizard.hermes_config import ensure_stt_language_default

    (hermes_home / "config.yaml").write_text(text, encoding="utf-8", newline="\n")
    assert ensure_stt_language_default("es") == result
    config = yaml.safe_load((hermes_home / "config.yaml").read_text("utf-8"))
    assert config["stt"]["language"] == language
    if "streaming" in text:
        assert config["stt"]["streaming"] is False
    if "model" in text:
        assert config["model"] == {"default": "x"}


@pytest.mark.real_stt_language_default
@pytest.mark.parametrize("text", ["model: [unclosed\n", "- a list\n", "stt: custom\n"])
def test_unreadable_or_unexpected_config_is_never_rewritten(hermes_home, text):
    from wizard.hermes_config import ensure_stt_language_default

    (hermes_home / "config.yaml").write_text(text, encoding="utf-8", newline="\n")
    assert ensure_stt_language_default("es") == "skipped"
    assert (hermes_home / "config.yaml").read_text("utf-8") == text


@pytest.mark.real_stt_language_default
@pytest.mark.parametrize("language", ["", "Español", "es-ES", "auto", "e", "es\nprovider: x"])
def test_only_a_plain_whisper_code_is_written(hermes_home, language):
    from wizard.hermes_config import ensure_stt_language_default

    (hermes_home / "config.yaml").write_text("stt:\n  streaming: false\n", encoding="utf-8", newline="\n")
    assert ensure_stt_language_default(language) == "skipped"
    assert (hermes_home / "config.yaml").read_text("utf-8") == "stt:\n  streaming: false\n"


@pytest.mark.real_stt_language_default
def test_profile_without_config_is_left_to_the_wizard(hermes_home):
    from wizard.hermes_config import ensure_stt_language_default

    assert ensure_stt_language_default("es") == "absent"
    assert not (hermes_home / "config.yaml").exists()


# ── IRIS' language, as the voice runtime reads it ──

@pytest.mark.parametrize("value, expected", [
    ("es", "es"), ("ES", "es"), ("es-CO", "es"), ("pt_BR", "pt"), ("yue", "yue"),
    ("", ""), ("  ", ""), ("Español", ""), ("auto detect", ""), ("es; rm", ""), ("../es", ""), ("x", ""),
])
def test_iris_language_becomes_a_whisper_code_or_nothing(monkeypatch, value, expected):
    iris_language(monkeypatch, value)
    assert iris_stt_language() == expected


def test_unreadable_iris_config_gives_no_language(monkeypatch):
    def broken(cls):
        raise ValueError("config.json dañado")
    monkeypatch.setattr(IRISConfig, "load", classmethod(broken))
    assert iris_stt_language() == ""


def start_voice_runtime():
    """ensure_gateway up to the spawn, which the test refuses."""
    with tempfile.TemporaryDirectory() as folder:
        runtime = HermesWakeRuntime()
        with (
            patch("core.hermes_wake.hermes_command_prefix", return_value=["hermes"]),
            patch("core.hermes_wake.get_hermes_repo", return_value=Path(folder)),
            patch("core.hermes_wake.asyncio.create_subprocess_exec",
                  new=AsyncMock(side_effect=PermissionError("test stops here"))),
        ):
            with pytest.raises(HermesGatewayError):
                asyncio.run(runtime.ensure_gateway())


def test_voice_runtime_applies_iris_language_before_hermes_serve(monkeypatch):
    from wizard import hermes_config

    iris_language(monkeypatch, "es-MX")
    calls = []
    monkeypatch.setattr(hermes_config, "ensure_stt_language_default", lambda language: calls.append(language))
    start_voice_runtime()
    assert calls == ["es"]


def test_voice_runtime_skips_the_write_without_a_usable_language(monkeypatch):
    from wizard import hermes_config

    iris_language(monkeypatch, "Español")
    calls = []
    monkeypatch.setattr(hermes_config, "ensure_stt_language_default", lambda language: calls.append(language))
    start_voice_runtime()
    assert calls == []


def test_a_failed_write_never_blocks_voice(monkeypatch, caplog):
    from wizard import hermes_config

    iris_language(monkeypatch, "es")

    def fails(language):
        raise RuntimeError("api_key: sk-secret-in-a-yaml-error")
    monkeypatch.setattr(hermes_config, "ensure_stt_language_default", fails)
    start_voice_runtime()  # still reaches the spawn
    assert "RuntimeError" in caplog.text
    assert "sk-secret" not in caplog.text


@pytest.mark.real_stt_language_default
def test_voice_runtime_writes_the_users_profile_shape(hermes_home, monkeypatch):
    """End to end on a synthetic copy of the profile seen on 2026-11-27."""
    iris_language(monkeypatch, "es")
    (hermes_home / "config.yaml").write_text("stt:\n  streaming: false\n", encoding="utf-8", newline="\n")
    start_voice_runtime()
    config = yaml.safe_load((hermes_home / "config.yaml").read_text("utf-8"))
    assert config["stt"] == {"streaming": False, "language": "es"}
