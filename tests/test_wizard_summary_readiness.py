"""A08.9: the final summary never calls IRIS ready when the launch would refuse it.

Quick and Repair keep the provider already in Hermes without validating it, so the summary
runs the Gateway's own pre-flight (key or login, an external provider's program, a local
runtime's server and model). Real save, readiness and Gateway pre-flight on throwaway homes;
no network, no process (based on Codex's post-qa8 reproduction).
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from wizard import app as wizard


def _local_server(models):
    """httpx.AsyncClient double: ``None`` = the local server is off."""
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.__aexit__.return_value = False
    if models is None:
        client.get.side_effect = httpx.ConnectError("synthetic local server is off")
    else:
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"models": [{"name": name} for name in models]}
        client.get.return_value = response
    return client


CASES = {
    "copilot-without-program": ("copilot-acp", False, None, "external_cli_missing"),
    "copilot-with-program": ("copilot-acp", True, None, ""),
    "ollama-off": ("ollama-local", False, None, "local_server_unavailable"),
    "ollama-on-with-model": ("ollama-local", False, ["synthetic-model"], ""),
}


@pytest.mark.parametrize("mode", ["quick", "repair"])
@pytest.mark.parametrize("case", CASES.values(), ids=CASES.keys())
def test_quick_and_repair_summaries_agree_with_the_gateway(tmp_path, monkeypatch, mode, case):
    provider, program_exists, local_models, expected = case
    monkeypatch.setenv("IRIS_HOME", str(tmp_path / "iris"))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    monkeypatch.delenv("IRIS_RUNTIME_DESCRIPTOR", raising=False)
    monkeypatch.delenv("IRIS_RUNTIME_MODE", raising=False)
    hermes = tmp_path / "hermes"
    hermes.mkdir()
    program = tmp_path / "copilot.exe"
    if program_exists:
        program.write_bytes(b"synthetic program, never run")
    hermes_provider, endpoint = ("custom", "  base_url: http://127.0.0.1:11434/v1\n") if provider == "ollama-local" else (provider, "")
    (hermes / "config.yaml").write_text(
        f"model:\n  provider: {hermes_provider}\n  default: synthetic-model\n{endpoint}", encoding="utf-8")
    (hermes / ".env").write_text(f"HERMES_COPILOT_ACP_COMMAND={program}\n", encoding="utf-8")

    provider_write = AsyncMock(side_effect=AssertionError("Quick/Repair must keep the saved provider"))
    with (
        patch.object(wizard, "resolve_hermes_cli", return_value=None),
        patch.object(wizard, "sync_hermes_identity_from_config", return_value={}),
        patch.object(wizard, "_edge_tts_voice_ids", new=AsyncMock(return_value=set())),
        patch.object(wizard, "_tts_config_error", return_value=None),
        patch.object(wizard, "save_llm_selection", new=provider_write),
        patch("wizard.health_check.check_gateway_port", new=AsyncMock(return_value={"ok": False})),
        patch("wizard.health_check.check_gateway_health", new=AsyncMock(side_effect=AssertionError("no live probe"))),
        patch.object(wizard.httpx, "AsyncClient", return_value=_local_server(local_models)),
        patch.object(wizard.subprocess, "Popen", side_effect=AssertionError("nothing may start")),
    ):
        saved = asyncio.run(wizard._save_config_impl(wizard.ConfigUpdate(
            hermes_mode=mode, llm_provider="", llm_model="", llm_use_oauth=False, hermes_wake_mode="keep")))
        ai = json.loads(saved.body)["ai"]
        kept = wizard.IRISConfig.load().llm_provider
        gateway = json.loads((asyncio.run(wizard._start_gateway_impl())).body) if expected else None

    assert saved.status_code == 200 and kept == provider
    provider_write.assert_not_awaited()
    assert ai["ready"] is (expected == "") and ai["code"] == expected
    if gateway is not None:  # the launch refuses with the very words the summary showed
        assert (gateway["code"], gateway["message"]) == (ai["code"], ai["message"])
