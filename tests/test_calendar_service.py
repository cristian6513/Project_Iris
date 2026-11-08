"""F11 consumes the existing Google owner; local cache reads cannot start providers."""
from __future__ import annotations

import asyncio
import copy

import pytest

from core.calendar_models import CalendarError
from core.calendar_service import CalendarService
from core.google_connections import GoogleServiceError
from test_calendar_models import timed

ACCOUNT = "gacc_" + "a" * 32
SECOND_ACCOUNT = "gacc_" + "b" * 32

def account(account_id=ACCOUNT, *, state="connected", granted=True, sync=None):
    return {"id": account_id, "email": "calendar-qa@example.com", "label": "Calendar QA", "auth_state": state,
            "services": {"calendar.read": {"granted": granted}, "gmail.read": {"granted": True}},
            "sync": {"calendar.read": sync or {"state": "idle", "last_success_at": "2026-10-15T12:00:00Z"}},
            "credential": "must-not-leak"}


class Cache:
    def __init__(self):
        self.reads = 0
        self.syncs = []
        self.value = {"available": True, "accounts": [account()], "warnings": [], "calendars": [
            {"account_id": ACCOUNT, "calendar": {"id": "remote-cal", "summary": "Personal", "timeZone": "America/Bogota"},
             "sync": {"state": "idle", "last_success_at": "2026-10-15T12:00:00Z"}, "events": [timed()]}]}

    async def calendar_cache(self):
        self.reads += 1
        return copy.deepcopy(self.value)

    async def sync(self, account_id, services):
        self.syncs.append((account_id, services))
        return {"state": "running"}


def snapshot(cache):
    return asyncio.run(CalendarService(cache, clock=lambda: 1_792_062_000).snapshot("2026-10-01", "2026-11-01", "America/Bogota"))


def test_get_uses_only_cache_and_public_allowlisted_account_fields():
    cache = Cache()
    answer = snapshot(cache)
    assert cache.reads == 1 and cache.syncs == []
    assert answer["schema"] == "iris.calendar/v1" and answer["read_only"] and answer["complete"]
    assert answer["events"][0]["calendar_key"] == answer["calendars"][0]["key"]
    assert answer["connected_account_ids"] == [ACCOUNT]
    assert answer["accounts"][0]["granted"] == ["calendar.read", "gmail.read"]
    assert answer["server_now"].endswith("Z") and isinstance(answer["accounts"][0]["sync"], dict)
    assert answer["events"][0]["id"].startswith("event_") and answer["calendars"][0]["key"].startswith("cal_")
    assert "credential" not in str(answer) and "remote-cal" not in str(answer)
    assert answer["coverage"] == {"start": "2026-10-01T05:00:00Z", "end": "2026-11-01T05:00:00Z"}


@pytest.mark.parametrize("state,granted", [("disconnected", True), ("reauth_required", True), ("connected", False)])
def test_cached_events_of_an_unavailable_account_are_never_emitted(state, granted):
    cache = Cache()
    cache.value["accounts"] = [account(state=state, granted=granted)]
    answer = snapshot(cache)
    assert answer["events"] == [] and answer["calendars"] == [] and answer["connected_account_ids"] == []
    assert answer["accounts"][0]["auth_state"] == state


@pytest.mark.parametrize("sync,code", [({"state": "never"}, "never_synced"),
    ({"state": "backoff", "last_success_at": "2026-10-15T12:00:00Z", "error": {"code": "rate_limited"}}, "sync_error")])
def test_failed_or_never_synced_copy_is_distinct_from_empty_calendar(sync, code):
    cache = Cache()
    cache.value["accounts"] = [account(sync=sync)]
    answer = snapshot(cache)
    assert answer["events"] and not answer["complete"]
    assert any(item["code"] == code for item in answer["warnings"])


def test_two_accounts_and_same_remote_event_ids_remain_separate():
    cache = Cache()
    cache.value["accounts"].append(account(SECOND_ACCOUNT, state="partial"))
    second = copy.deepcopy(cache.value["calendars"][0])
    second["account_id"] = SECOND_ACCOUNT
    cache.value["calendars"].append(second)
    answer = snapshot(cache)
    assert len({item["key"] for item in answer["calendars"]}) == 2
    assert len({item["id"] for item in answer["events"]}) == 2


def test_absence_of_connections_is_not_a_fake_network_error():
    cache = Cache()
    cache.value.update(accounts=[], calendars=[])
    answer = snapshot(cache)
    assert answer["available"] and answer["complete"] and answer["events"] == [] and answer["warnings"] == []
    unavailable = snapshot(None)
    assert not unavailable["available"] and not unavailable["complete"]


def test_malformed_event_and_cache_limit_make_projection_partial():
    cache = Cache()
    cache.value["calendars"][0]["events"].append({"id": "broken", "start": {"date": "invalid"}})
    cache.value["warnings"] = [{"code": "cache_limit"}]
    answer = snapshot(cache)
    assert not answer["complete"] and len(answer["events"]) == 1
    assert {item["code"] for item in answer["warnings"]} == {"cache_limit", "invalid_event"}


def test_unresolved_series_and_changed_accounts_have_actionable_warnings():
    cache = Cache()
    cache.value["calendars"][0]["events"] = [timed("child", recurringEventId="missing-series")]
    cache.value["warnings"] = [{"code": "account_changed", "account_id": ACCOUNT}]
    answer = snapshot(cache)
    warnings = {row["code"]: row["message"] for row in answer["warnings"]}
    assert not answer["complete"]
    assert "evento original" in warnings["orphan_exception"]
    assert "cuenta cambió" in warnings["account_changed"]


def test_sync_requests_calendar_only_and_never_enables_periodic_services():
    cache = Cache()
    cache.value["accounts"].append(account(SECOND_ACCOUNT))
    answer = asyncio.run(CalendarService(cache).sync())
    assert answer == {"accepted": True, "account_ids": [ACCOUNT, SECOND_ACCOUNT], "errors": []}
    assert cache.syncs == [(ACCOUNT, ["calendar.read"]), (SECOND_ACCOUNT, ["calendar.read"])]


@pytest.mark.parametrize("ids", [[ACCOUNT, "not-found"], [ACCOUNT, ACCOUNT], [], [10]])
def test_bad_sync_selection_does_not_dispatch_any_account(ids):
    cache = Cache()
    with pytest.raises((CalendarError, GoogleServiceError)):
        asyncio.run(CalendarService(cache).sync(ids))
    assert cache.syncs == []


def test_invalid_range_is_refused_before_opening_the_google_cache():
    cache = Cache()
    with pytest.raises(CalendarError):
        asyncio.run(CalendarService(cache).snapshot("2026-10-01", "2027-01-10", "UTC"))
    assert cache.reads == 0


def test_partial_event_projection_never_claims_an_authoritative_calendar_inventory():
    cache = Cache()
    cache.value["calendars"][0]["events"].append({"id": "bad", "start": {"date": "invalid"}})
    answer = snapshot(cache)
    assert not answer["complete"] and answer["calendar_keys"] is None
    assert answer["connected_account_ids"] == [ACCOUNT]


def test_event_limit_cannot_retire_a_calendar_not_reached_by_projection(monkeypatch):
    cache = Cache()
    monkeypatch.setattr("core.calendar_service.MAX_EVENTS", 0)
    other = copy.deepcopy(cache.value["calendars"][0])
    other["calendar"]["id"] = "second-calendar"
    cache.value["calendars"].append(other)
    answer = snapshot(cache)
    assert answer["calendar_keys"] is None and not answer["complete"]
    assert any(item["code"] == "event_limit" for item in answer["warnings"])
