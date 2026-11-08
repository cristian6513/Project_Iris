"""Regression tests for the wizard's connection diagnostics."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from wizard.health_check import (
    check_config_valid,
    check_gateway_port,
    check_iris_websocket,
)


def test_config_summary_describes_the_selected_oauth_adapter(tmp_path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "model:\n  provider: openai-codex\n  default: gpt-5.6-luna\n",
        encoding="utf-8",
    )
    env_path = tmp_path / ".env"
    env_path.write_text("API_SERVER_ENABLED=true\n", encoding="utf-8")
    with (
        patch("wizard.health_check.get_config_yaml_path", return_value=config_path),
        patch("wizard.health_check.get_env_path", return_value=env_path),
        patch(
            "core.config._derive_llm_from_hermes",
            return_value={"llm_use_oauth": True},
        ),
    ):
        result = asyncio.run(check_config_valid())

    assert result["ok"] is True
    assert result["detail"] == "openai-codex / gpt-5.6-luna · OAuth seleccionado"
    assert "{'default'" not in result["detail"]


def test_stopped_local_services_are_inactive_not_configuration_failures() -> None:
    with patch("wizard.health_check._tcp_port_open", return_value=False):
        gateway = asyncio.run(check_gateway_port())
        iris = asyncio.run(check_iris_websocket())

    assert gateway["status"] == "inactive"
    assert gateway["ok"] is False
    assert "Iniciar Gateway" in gateway["hint"]
    assert iris["status"] == "inactive"
    assert "normal antes del último paso" in iris["hint"]
