"""Regressions for the qa9 QA on Windows 11 (2026-10-09), without real Hermes or accounts."""

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from core import capability_permissions, hermes_runtime, hermes_updates
from core.runtime_descriptor import DESCRIPTOR_ENV, get_runtime
from tests.runtime_descriptor_fixtures import write_runtime_fixture
from wizard.provider_sync import _convert_hermes_inventory_payload, _humanize_model_id


@pytest.fixture
def packaged(tmp_path, monkeypatch):
    descriptor, _ = write_runtime_fixture(tmp_path / "Iris instalación")
    monkeypatch.setenv(DESCRIPTOR_ENV, str(descriptor))
    monkeypatch.setenv("IRIS_HOME", str(tmp_path / "profile"))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profile/hermes"))
    return get_runtime()


# ── Hermes commands: no console window, room for a cold start, readable errors ──

def test_packaged_hermes_commands_hide_their_window_and_tolerate_a_cold_start(packaged, monkeypatch):
    run = Mock(return_value=subprocess.CompletedProcess([], 0, "ok", ""))
    monkeypatch.setattr(hermes_runtime.subprocess, "run", run)
    hermes_runtime.run_hermes_cli(["status"], timeout=8)
    assert run.call_args.kwargs["creationflags"] == (0x08000000 if os.name == "nt" else 0)
    # `hermes status` took 12-27 s cold on a fast PC; 8 s always failed (qa9).
    assert run.call_args.kwargs["timeout"] == hermes_runtime.PACKAGED_MIN_TIMEOUT


def test_development_keeps_the_callers_timeout(monkeypatch):
    monkeypatch.delenv(DESCRIPTOR_ENV, raising=False)
    assert hermes_runtime.hermes_timeout(8) == 8


def test_a_timeout_reads_as_a_sentence_and_keeps_the_command_as_detail(packaged, monkeypatch):
    command = [str(packaged.hermes.python), "-E", "-c", "import json"]
    monkeypatch.setattr(hermes_runtime.subprocess, "run",
                        Mock(side_effect=subprocess.TimeoutExpired(command, 60)))
    result = hermes_runtime.run_hermes_cli(["status"])
    assert result["error"] == "Hermes tardó más de 60 s en responder. Vuelve a intentarlo."
    assert "python.exe" not in result["error"] and "-E" in result["detail"]


def test_the_included_engine_version_needs_no_hermes_process(packaged, monkeypatch):
    effect = Mock(side_effect=AssertionError("No Hermes process for a pinned version"))
    monkeypatch.setattr(hermes_updates, "run_hermes_cli", effect)
    monkeypatch.setattr(capability_permissions, "run_hermes_cli", effect)
    assert hermes_updates.hermes_version().startswith(f"Hermes Agent v{packaged.hermes.version}")
    result = capability_permissions._hermes_version_result()
    assert result["success"] and packaged.hermes.version in result["output"]
    effect.assert_not_called()


# ── Model names: versions stay readable ─────────────────────────────────────

@pytest.mark.parametrize("model_id,name", [
    ("claude-opus-5-5", "Claude Opus 5.5"),
    ("claude-opus-4-5-20251101", "Claude Opus 4.5 20251101"),
    ("claude-fable-5.1", "Claude Fable 5.1"),
    ("anthropic/claude-haiku-4-5-20251001", "Claude Haiku 4.5 20251001"),
    ("gpt-5.4-mini", "Gpt 5.4 Mini"),
    ("kimi-k2-0905-preview", "Kimi K2 0905 Preview"),
    ("MiniMax-M2.7", "MiniMax M2.7"),
    ("glm-5", "Glm 5"),
])
def test_model_ids_become_names_with_dotted_versions(model_id, name):
    assert _humanize_model_id(model_id) == name


def test_inventory_names_come_from_the_readable_form():
    catalog = _convert_hermes_inventory_payload({"providers": [{
        "slug": "anthropic", "authenticated": False,
        "models": [{"id": "claude-opus-5-5", "name": "Claude Opus 5 5"}],
    }]})
    assert catalog["anthropic"][0]["name"] == "Claude Opus 5.5"


# ── Companion: the installed program is recognised without PowerShell ──────

@pytest.mark.skipif(sys.platform != "win32", reason="Windows version resources")
def test_companion_identity_is_read_through_the_windows_api(monkeypatch):
    from core import companion_lifecycle

    # PowerShell needed more than 12 s on a busy PC: a correct install looked
    # damaged and every repair asked for another one (qa9).
    companion_lifecycle.platform.system()  # cached by Python after its first `ver` call
    monkeypatch.setattr(companion_lifecycle.subprocess, "run", Mock(side_effect=AssertionError("no process")))
    # The base interpreter carries version resources (a venv launcher does not).
    identity = companion_lifecycle._windows_file_identity(Path(sys.base_prefix) / "python.exe")
    assert identity["product"].startswith("Python")
    assert identity["file_version"]
    assert companion_lifecycle._windows_file_identity(Path(__file__)) == {}


# ── Installer: closes only the Companion Iris manages ──────────────────────

@pytest.mark.parametrize("source,closes", [("managed", True), ("registry", False), ("", False)])
def test_the_installer_closes_only_the_companion_iris_manages(monkeypatch, source, closes):
    from core import companion_lifecycle

    monkeypatch.setattr(companion_lifecycle.platform, "system", lambda: "Windows")
    monkeypatch.setattr(companion_lifecycle.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(companion_lifecycle, "_installation_diagnostic", lambda: {"source": source, "running": True})
    closed = Mock(return_value="stopped")
    monkeypatch.setattr(companion_lifecycle, "_close_verified_companion", closed)
    saved = Mock()
    monkeypatch.setattr(companion_lifecycle, "_save_companion_flags", saved)
    assert companion_lifecycle.close_companion_for_update() is closes
    assert closed.called is closes
    saved.assert_not_called()  # closing for an update keeps the user's preferences
