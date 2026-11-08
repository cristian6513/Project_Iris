"""F11 loopback boundary, strict query/body contract, private responses and manual sync."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.calendar_api import create_calendar_router
from core.calendar_service import CalendarService
from test_calendar_service import ACCOUNT, Cache


def client(peer="127.0.0.1"):
    cache = Cache()
    app = FastAPI()
    app.include_router(create_calendar_router(lambda: CalendarService(cache)))
    return TestClient(app, client=(peer, 43000), base_url="http://127.0.0.1"), cache


def test_get_is_readonly_private_and_uses_dates_end_exclusive():
    browser, cache = client()
    answer = browser.get("/api/calendar", params={"start": "2026-10-01", "end": "2026-11-01", "time_zone": "America/Bogota"})
    assert answer.status_code == 200 and "no-store" in answer.headers["cache-control"]
    assert answer.json()["read_only"] and cache.syncs == []


@pytest.mark.parametrize("path,method,body", [("/api/calendar?start=2026-10-01&end=2026-11-01", "get", None),
                                            ("/api/calendar/sync", "post", {"account_ids": [ACCOUNT]})])
def test_lan_peer_cannot_read_or_sync_this_computers_calendar(path, method, body):
    browser, cache = client("192.168.1.8")
    answer = getattr(browser, method)(path, **({"json": body} if body is not None else {}))
    assert answer.status_code == 403 and cache.reads == 0 and cache.syncs == []
    assert "no-store" in answer.headers["cache-control"]


@pytest.mark.parametrize("query", ["start=2026-10-01&end=2026-10-01", "start=2026-10-01&end=2027-01-10",
    "start=2026-10-01&end=2026-11-01&time_zone=Missing/Zone", "start=2026-10-01"])
def test_invalid_queries_are_refused_without_reading_cache(query):
    browser, cache = client()
    answer = browser.get("/api/calendar?" + query)
    assert answer.status_code == 422 and cache.reads == 0
    assert "no-store" in answer.headers["cache-control"]


def test_sync_is_explicit_and_rejects_unknown_body_fields():
    browser, cache = client()
    bad = browser.post("/api/calendar/sync", json={"account_ids": [ACCOUNT], "services": ["gmail.read"]})
    assert bad.status_code == 422 and cache.syncs == []
    assert "no-store" in bad.headers["cache-control"]
    good = browser.post("/api/calendar/sync", json={"account_ids": [ACCOUNT]})
    assert good.status_code == 202 and good.json()["account_ids"] == [ACCOUNT]
    assert cache.syncs == [(ACCOUNT, ["calendar.read"])] and "no-store" in good.headers["cache-control"]


def test_unknown_or_disconnected_account_is_refused_without_partial_sync():
    browser, cache = client()
    answer = browser.post("/api/calendar/sync", json={"account_ids": [ACCOUNT, "unknown"]})
    assert answer.status_code == 409 and cache.syncs == []


def test_unconfigured_calendar_router_explains_unavailability_without_data():
    app = FastAPI()
    app.include_router(create_calendar_router(lambda: None))
    browser = TestClient(app, client=("127.0.0.1", 43000))
    answer = browser.get("/api/calendar?start=2026-10-01&end=2026-11-01")
    assert answer.status_code == 503 and answer.json()["error"]["code"] == "client_unavailable"
    assert "no-store" in answer.headers["cache-control"]
