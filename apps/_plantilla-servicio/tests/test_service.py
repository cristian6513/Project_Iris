"""Tests of the service template: run with `pytest apps/<id>/tests`.

Core mounts the router the way it mounts any reviewed app service; here it goes on a small
FastAPI app with a disposable profile, so nothing touches the person's data. The app's id comes
from app.json, so renaming the app does not break these tests.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.app_services import load_app_router

APP_DIR = Path(__file__).resolve().parents[1]
APP_ID = json.loads((APP_DIR / "app.json").read_text(encoding="utf-8"))["id"]
URL = f"/api/apps/{APP_ID}/items"


def _client(home: Path) -> TestClient:
    app = FastAPI()
    app.include_router(load_app_router(APP_ID, home=home, source_dir=APP_DIR), prefix=f"/api/apps/{APP_ID}")
    return TestClient(app)


def test_it_serves_the_example_data_until_the_profile_has_its_own(tmp_path: Path) -> None:
    client = _client(tmp_path)
    body = client.get(URL).json()
    assert body["source"] == "ejemplo"
    assert [item["id"] for item in body["items"]] == ["uno", "dos"]

    own = tmp_path / "app-data" / APP_ID / "items.json"
    own.write_text(json.dumps([{"id": "mio", "title": "Mío", "detail": "x" * 900}, {"bad": True}]), encoding="utf-8")
    body = client.get(URL).json()
    assert body["source"] == "perfil"
    assert body["items"] == [{"id": "mio", "title": "Mío", "detail": "x" * 500}]


def test_a_damaged_profile_file_is_an_error_not_an_empty_list(tmp_path: Path) -> None:
    client = _client(tmp_path)
    (tmp_path / "app-data" / APP_ID).mkdir(parents=True, exist_ok=True)
    (tmp_path / "app-data" / APP_ID / "items.json").write_text("{no", encoding="utf-8")
    response = client.get(URL)
    assert response.status_code == 409
    assert "no se pudo leer" in response.json()["detail"]


def test_it_only_reads(tmp_path: Path) -> None:
    client = _client(tmp_path)
    assert client.post(URL, json={}).status_code == 405
