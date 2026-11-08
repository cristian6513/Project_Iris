"""The final wizard commit must inherit the provider plan check before any write."""
from unittest.mock import patch, AsyncMock

import pytest
from fastapi.testclient import TestClient

from wizard import app as wizard
from wizard.provider_sync import _convert_hermes_inventory_payload


@pytest.mark.parametrize("endpoint,payload", [
    ("/api/save-llm-selection", {"provider": "nous", "model": "paid-model", "use_oauth": True}),
    ("/api/save-config", {"assistant_name": "IRIS", "hermes_mode": "full", "llm_provider": "nous", "llm_model": "paid-model", "llm_use_oauth": True}),
])
def test_both_wizard_save_routes_refuse_a_known_locked_model_without_writes(tmp_path, monkeypatch, endpoint, payload):
    monkeypatch.setenv("IRIS_HOME", str(tmp_path / "iris"))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    catalog = _convert_hermes_inventory_payload({"providers": [{
        "slug": "nous", "authenticated": True, "free_tier": True,
        "unavailable_models": ["paid-model"], "models": [{"id": "paid-model"}],
    }]})
    with patch("wizard.provider_sync.load_snapshot", return_value=(catalog, {})), \
         patch("wizard.provider_models.discover_models", new=AsyncMock(return_value={"status": "ok", "models": [{"value": "free-model"}]})), \
         patch("wizard.hermes_config.has_hermes_auth_profile", return_value=True), \
         patch("wizard.hermes_config.write_hermes_config") as writer:
        response = TestClient(wizard.app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 50123)).post(
            endpoint, headers={"Origin": "http://127.0.0.1:8765"}, json=payload,
        )
    assert response.status_code == 400
    assert response.json()["code"] == "model_not_in_plan"
    writer.assert_not_called()
    assert not (tmp_path / "iris/config.json").exists()
    assert not (tmp_path / "hermes/config.yaml").exists()
