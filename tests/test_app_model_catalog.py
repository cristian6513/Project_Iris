"""Apps choose from the local default profile, never another chat's profile or Gateway."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.hermes_bridge import HermesBridge
from core.model_selection_api import model_selection_router


@pytest.mark.asyncio
async def test_app_catalog_captures_default_credentials_without_changing_the_active_chat():
    bridge = object.__new__(HermesBridge)
    bridge._is_available = True
    bridge._session_id, bridge._active_profile = "named-session", "coder"
    bridge._profile_api_key, bridge._default_api_key = "named-private", "default-private"
    bridge.gateway_is_local = AsyncMock(return_value=True)
    bridge.model_inventory = AsyncMock(return_value={"providers": [{"id": "local-provider"}]})
    bridge.default_model = AsyncMock(return_value={"provider": "local-provider", "model": "configured"})
    result = await bridge.app_model_inventory(refresh=True)
    route = bridge.model_inventory.call_args.kwargs["route"]
    assert route["profile"] is None and route["api_key"] == "default-private"
    assert bridge.model_inventory.call_args.kwargs["refresh"] is True
    assert bridge.default_model.call_args.args == (route,)
    assert result["default"]["model"] == "configured"
    assert "private" not in str(result)
    assert bridge._active_profile == "coder" and bridge._session_id == "named-session"


@pytest.mark.asyncio
async def test_a_remote_or_foreign_gateway_is_not_a_catalog_for_the_local_app_workers():
    bridge = object.__new__(HermesBridge)
    bridge._is_available = True
    bridge.gateway_is_local = AsyncMock(return_value=False)
    bridge.model_inventory = AsyncMock()
    with pytest.raises(RuntimeError):
        await bridge.app_model_inventory()
    bridge.model_inventory.assert_not_called()


def test_the_app_catalog_is_read_only_local_and_does_not_access_conversation_state(tmp_path, monkeypatch):
    monkeypatch.setenv("IRIS_HOME", str(tmp_path))
    payload = {"default": {"provider": "configured", "model": "main"}, "inventory": {"providers": []}}
    method = AsyncMock(return_value=payload)
    bridge = SimpleNamespace(app_model_inventory=method)
    app = FastAPI()
    app.include_router(model_selection_router(agent=lambda: SimpleNamespace(bridge=bridge), broadcast=AsyncMock()))
    client = TestClient(app, client=("127.0.0.1", 1234))
    response = client.get("/api/models/apps?refresh=true")
    assert response.status_code == 200 and response.json() == payload
    assert response.headers["Cache-Control"] == "no-store, private"
    method.assert_awaited_once_with(refresh=True)
    method.reset_mock()
    response = TestClient(app, client=("192.168.1.15", 1234)).get("/api/models/apps")
    assert response.status_code == 403
    method.assert_not_called()
    assert not list(tmp_path.iterdir())
