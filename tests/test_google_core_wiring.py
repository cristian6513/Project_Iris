"""F10.4: the Google connections inside Core: the OAuth client file, the service and its routes."""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from core import google_worker_client as client_module
from core.google_worker_client import CLIENT_ENV, create_google_service, load_client_config

DESKTOP = {"installed": {"client_id": "1-sintetico.apps.googleusercontent.com", "client_secret": "secreto-sintetico",
                         "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                         "token_uri": "https://oauth2.googleapis.com/token"}}


def test_without_a_client_file_the_connections_say_they_are_not_available(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv(CLIENT_ENV, raising=False)
    assert load_client_config() is None
    (tmp_path / "perfil").mkdir()
    service = create_google_service(tmp_path / "perfil")
    snapshot = asyncio.run(service.snapshot())
    assert snapshot["available"] is False and snapshot["accounts"] == []
    assert not (tmp_path / "perfil" / "connections").exists(), "a dormant service creates nothing"


def test_a_development_client_file_is_validated_and_never_read_from_the_web_root(tmp_path, monkeypatch) -> None:
    good = tmp_path / "fuera del repositorio" / "cliente.json"
    good.parent.mkdir()
    good.write_text(json.dumps(DESKTOP), encoding="utf-8")
    monkeypatch.setenv(CLIENT_ENV, str(good))
    assert load_client_config().client_id == "1-sintetico.apps.googleusercontent.com"

    bad = tmp_path / "web.json"
    bad.write_text(json.dumps({"web": DESKTOP["installed"]}), encoding="utf-8")
    monkeypatch.setenv(CLIENT_ENV, str(bad))
    assert load_client_config() is None

    from core.runtime_descriptor import get_runtime

    served = get_runtime().assets_root / "oauth-client-test-should-not-exist.json"
    assert not served.exists()
    served.write_text(json.dumps(DESKTOP), encoding="utf-8")
    try:
        monkeypatch.setenv(CLIENT_ENV, str(served))
        assert load_client_config() is None, "anything in the web root is served over HTTP"
    finally:
        served.unlink()


def test_a_packaged_iris_reserves_the_shared_client_and_ignores_the_override(tmp_path, monkeypatch) -> None:
    class Packaged:
        packaged = True
        base = tmp_path / "instalacion"
        assets_root = tmp_path / "instalacion" / "web"

    import core.runtime_descriptor as runtime_descriptor

    monkeypatch.setattr(runtime_descriptor, "get_runtime", lambda: Packaged)
    monkeypatch.setenv(CLIENT_ENV, str(tmp_path / "otro.json"))
    assert client_module.client_config_path() is None
    monkeypatch.setattr(client_module, 'ALLOW_SHARED_GOOGLE_CLIENT', True)
    assert client_module.client_config_path() == tmp_path / "instalacion" / "google" / "oauth_client.json"


@pytest.mark.parametrize("path", ["/api/connections/google", "/api/connections/google/sessions/gses_" + "0" * 32,
                                  "/api/connections/google/accounts/gacc_" + "0" * 32 + "/sync"])
def test_cores_routes_answer_only_to_this_pc(path) -> None:
    import core.websocket_server as server

    remote = TestClient(server.app, base_url="http://127.0.0.1:8765", client=("192.168.1.20", 50000))
    assert remote.get(path).status_code in (403, 421), "a LAN device never reads the accounts"
    local = TestClient(server.app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 50000))
    response = local.get(path)
    assert response.status_code in (503, 404) and "no-store" in response.headers.get("cache-control", "")
