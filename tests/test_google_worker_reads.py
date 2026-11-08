"""F10.3: the worker's read rounds against the stand-in's Calendar and Gmail (no network)."""

from __future__ import annotations

import asyncio
import contextlib

import pytest

from core.google_contracts import requested_scopes
from tests.test_google_worker import KEY, Google, authorize, pytestmark  # noqa: F401 (same skips)

LONG = "x" * 9000
EVENTS = {
    "": {"pages": [
        {"items": [{"id": "serie", "status": "confirmed", "summary": "Reunión semanal", "description": LONG,
                    "start": {"dateTime": "2026-10-20T09:00:00-05:00", "timeZone": "America/Bogota"},
                    "end": {"dateTime": "2026-10-20T10:00:00-05:00"}, "recurrence": ["RRULE:FREQ=WEEKLY"],
                    "conferenceData": {"entryPoints": [{"entryPointType": "video", "uri": "https://meet.google.com/abc"},
                                                       {"entryPointType": "phone", "uri": "tel:+1"}]},
                    "htmlLink": "javascript:alert(1)"}]},
        {"items": [{"id": "serie_20261027", "status": "cancelled", "recurringEventId": "serie",
                    "originalStartTime": {"dateTime": "2026-10-27T09:00:00-05:00"}},
                   {"id": "borrado", "status": "cancelled"}]},
    ], "next": "tok-1"},
    "tok-1": {"pages": [{"items": [{"id": "nuevo", "status": "confirmed", "summary": "Cita",
                                    "start": {"date": "2026-11-02"}, "end": {"date": "2026-11-03"}}]},
                        {"items": [{"id": "serie", "status": "confirmed", "summary": "Reunión semanal (movida)"}]}],
              "next": "tok-2"},
    "tok-2": {"status": 410},
}


def _message(message_id: str, subject: str) -> dict:
    return {"id": message_id, "threadId": "t-" + message_id, "labelIds": ["INBOX", "UNREAD"], "snippet": "Hola…",
            "internalDate": "1792000000000", "sizeEstimate": 1234,
            "payload": {"headers": [{"name": "From", "value": "bea@example.com"}, {"name": "Subject", "value": subject},
                                    {"name": "X-Secret", "value": "nunca"}]}}


GMAIL = {
    "historyId": "1000",
    "list": [{"messages": [{"id": "m1"}, {"id": "m2"}]}, {"messages": [{"id": "m3"}]}],
    "messages": {"m1": _message("m1", "Primero"), "m2": _message("m2", "Segundo"), "m4": _message("m4", "Llegó mientras")},
    "history": {"1000": {"pages": [{"history": [{"id": "1001", "messagesAdded": [{"message": {"id": "m4"}}]}]},
                                   {"history": [{"id": "1005", "messagesDeleted": [{"message": {"id": "m2"}}]}]}],
                         "historyId": "1010"},
                "1010": {"status": 404}},
}


def _google(tmp_path, **api) -> Google:
    scope = " ".join(requested_scopes(["calendar.read", "gmail.read"]))
    return Google(tmp_path, token={"scope": scope, "id_token": {}}, api={
        "calendar_list": [{"items": [{"id": "ana@example.com", "summary": "Ana", "primary": True, "selected": True}]},
                          {"items": [{"id": "familia@group.calendar.google.com", "summary": "Familia"},
                                     {"id": "viejo@group.calendar.google.com", "deleted": True}]}],
        "events": {"ana@example.com": EVENTS}, "gmail": GMAIL, **api})


def _credential(google: Google) -> bytes:
    _handle, _page, outcome = asyncio.run(authorize(google, scopes=requested_scopes(["calendar.read", "gmail.read"])))
    assert outcome.error == ""
    return outcome.credential


def read(google: Google, credential: bytes, method: str, **params) -> list[dict]:
    async def run():
        lines = []
        stream = google.client().read_round(method, credential, protection=KEY, params=params, timeout=120)
        async with contextlib.aclosing(stream):
            async for message in stream:
                lines.append(message)
        return lines

    return asyncio.run(run())


def records(lines: list[dict]) -> tuple[list[dict], list[str]]:
    kept = [record for line in lines if line["event"] == "page" for record in line["public"]["records"]]
    gone = [record for line in lines if line["event"] == "page" for record in line["public"]["deleted"]]
    return kept, gone


def test_calendars_are_read_in_full_then_by_their_sync_token(tmp_path) -> None:
    google = _google(tmp_path)
    credential = _credential(google)
    calendars, _ = records(read(google, credential, "sync.calendar_list"))
    assert [item["id"] for item in calendars] == ["ana@example.com", "familia@group.calendar.google.com"]
    assert calendars[0]["primary"] is True

    lines = read(google, credential, "sync.calendar_events", calendar_id="ana@example.com", sync_token=None)
    kept, gone = records(lines)
    assert [item["id"] for item in kept] == ["serie", "serie_20261027"], "a cancelled instance hides its occurrence"
    assert gone == ["borrado"], "a cancelled single event is gone"
    serie = kept[0]
    assert len(serie["description"]) == 4000 and serie["video"] == "https://meet.google.com/abc"
    assert serie["htmlLink"] is None and serie["recurrence"] == ["RRULE:FREQ=WEEKLY"]
    assert lines[-1] == {"schema": "iris.google-rpc/v1", "id": lines[-1]["id"], "event": "result",
                         "public": {"sync_token": "tok-1", "full": True}}

    lines = read(google, credential, "sync.calendar_events", calendar_id="ana@example.com", sync_token="tok-1")
    assert [item["id"] for item in records(lines)[0]] == ["nuevo", "serie"]
    assert lines[-1]["public"] == {"sync_token": "tok-2", "full": False}
    incremental = [entry for entry in google.log("api") if entry["query"].get("syncToken") == "tok-1"]
    assert [entry["query"].get("pageToken") for entry in incremental] == [None, "page-1"], "one token for every page"
    assert all(entry["query"].get("singleEvents") == "false" for entry in google.log("api") if "events" in entry["path"])

    expired = read(google, credential, "sync.calendar_events", calendar_id="ana@example.com", sync_token="tok-2")
    assert expired[-1]["event"] == "error" and expired[-1]["public"]["code"] == "sync_expired"
    assert {entry["method"] for entry in google.log("api")} == {"GET"}, "only reads"


def test_the_mail_window_replays_what_changed_while_it_was_read_and_never_writes(tmp_path) -> None:
    google = _google(tmp_path)
    credential = _credential(google)
    lines = read(google, credential, "sync.gmail", checkpoint=None, window_days=30, max_messages=200)
    kept, gone = records(lines)
    assert sorted(item["id"] for item in kept) == ["m1", "m2", "m4"] and set(gone) == {"m2", "m3"}
    assert lines[-1]["public"] == {"checkpoint": "1010", "full": True,
                                   "coverage": {"window_days": 30, "max_messages": 200, "complete": True, "messages": 3},
                                   "arrivals": ["m4"]}
    m1 = next(item for item in kept if item["id"] == "m1")
    assert m1["headers"] == {"from": "bea@example.com", "subject": "Primero"}, "only the four headers Iris reads"
    assert m1["labelIds"] == ["INBOX", "UNREAD"], "read as it is: nothing marked as read"
    calls = google.log("api")
    paths = [entry["path"] for entry in calls]
    assert paths.index("/gmail/v1/users/me/profile") < paths.index("/gmail/v1/users/me/messages"), \
        "the history point is taken before the window is read"
    assert all(entry["query"].get("format") == "metadata" for entry in calls if entry["path"].startswith("/gmail/v1/users/me/messages/"))
    assert {entry["method"] for entry in calls} == {"GET"}

    expired = read(google, credential, "sync.gmail", checkpoint="1010", window_days=30, max_messages=200)
    assert expired[-1]["public"]["code"] == "sync_expired"


def test_a_token_google_rejects_mid_round_is_renewed_and_reported_first(tmp_path) -> None:
    google = _google(tmp_path, unauthorized_once=True)
    credential = _credential(google)
    lines = read(google, credential, "sync.calendar_list")
    kinds = [line["event"] for line in lines]
    assert kinds[0] == "refreshed" and kinds[-1] == "result", kinds
    renewed = lines[0]["private"]["credential"]
    assert google.log("refresh"), "renewed with the refresh token"
    tokens = [entry["token"] for entry in google.log("api")]
    assert tokens[0] != tokens[-1], "the retried call carried the new access token"
    assert renewed  # the protected credential, for Core to store over the one it renewed


@pytest.mark.parametrize(("fail", "code", "retry"), [
    ({"match": "calendarList", "status": 429, "reason": "rateLimitExceeded", "retry_after": "7"}, "rate_limited", 7.0),
    ({"match": "calendarList", "status": 403, "reason": "accessNotConfigured"}, "api_disabled", None),
    ({"match": "calendarList", "status": 403, "reason": "insufficientPermissions"}, "permission_missing", None),
    ({"match": "calendarList", "status": 503}, "provider_unavailable", None),
])
def test_google_answers_become_contract_codes(tmp_path, fail, code, retry) -> None:
    google = _google(tmp_path, fail=fail)
    credential = _credential(google)
    lines = read(google, credential, "sync.calendar_list")
    assert lines[-1]["event"] == "error" and lines[-1]["public"]["code"] == code
    assert lines[-1]["public"].get("retry_after") == retry
