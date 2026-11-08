"""F11: real dateutil/zoneinfo projection with entirely synthetic Google documents."""
from __future__ import annotations

import copy
import time

import pytest

from core.calendar_models import (
    CalendarError, CalendarWindow, calendar_projection, conference_url, normalize_events, safe_public_https,
)


def timed(event_id="one", start="2026-10-15T10:00:00-05:00", end="2026-10-15T11:00:00-05:00", **extra):
    return {"id": event_id, "summary": "Reunión", "start": {"dateTime": start, "timeZone": "America/Bogota"},
            "end": {"dateTime": end, "timeZone": "America/Bogota"}, **extra}


def expand(records, start="2026-10-01", end="2026-11-01", tz="America/Bogota", **kw):
    return normalize_events(records, account_id="gacc_" + "a" * 32, calendar_key="cal_" + "b" * 32, time_zone=tz,
                            window=CalendarWindow.parse(start, end, tz), **kw)


def test_exclusive_window_and_93_day_boundary():
    assert (CalendarWindow.parse("2026-01-01", "2026-04-04", "UTC").end_date.isoformat()) == "2026-04-04"
    for start, end in [("2026-01-01", "2026-04-05"), ("2026-01-01", "2026-01-01"),
                       ("2026-02-31", "2026-03-01"), ("2026-1-1", "2026-01-02")]:
        with pytest.raises(CalendarError):
            CalendarWindow.parse(start, end, "UTC")
    with pytest.raises(CalendarError, match="IANA"):
        CalendarWindow.parse("2026-01-01", "2026-01-02", "America/No_such_zone")


def test_window_uses_dst_not_a_fixed_24_hour_day():
    spring = CalendarWindow.parse("2026-03-08", "2026-03-09", "America/New_York")
    autumn = CalendarWindow.parse("2026-11-01", "2026-11-02", "America/New_York")
    assert (spring.end - spring.start).total_seconds() == 23 * 3600
    assert (autumn.end - autumn.start).total_seconds() == 25 * 3600


@pytest.mark.parametrize("value", ["http://meet.google.com/abc", "https://127.0.0.1/a", "https://[::1]/",
    "https://192.168.1.1/", "https://localhost/a", "https://dev.local/", "https://x.internal/",
    "https://x.test/", "https://user:pass@meet.google.com/", "https://meet.google.com:8443/",
    "javascript:alert(1)", "https://meet.google.com\\@evil.com/", "https://2130706433/",
    "https://0x7f000001/", "https://foo.123/", "https://meet.google.com/\npath", "//meet.google.com/abc"])
def test_join_rejects_nonpublic_and_ambiguous_urls(value):
    assert safe_public_https(value) is None


def test_conference_link_comes_only_from_structured_fields_not_text():
    assert conference_url({"description": "https://meet.google.com/abc", "location": "https://zoom.us/j/1"}) is None
    assert conference_url({"conferenceData": {"entryPoints": [
        {"entryPointType": "phone", "uri": "https://example.com/not-video"},
        {"entryPointType": "video", "uri": "https://meet.google.com/abc"}]}}) == "https://meet.google.com/abc"
    assert conference_url({"video": "https://localhost/", "hangoutLink": "https://meet.google.com/def"}) == "https://meet.google.com/def"
    assert safe_public_https("HTTPS://MEET.GOOGLE.COM:443/abc?x=1#part") == "https://meet.google.com/abc?x=1#part"


def test_all_day_dates_end_exclusive_and_overlap_multiday():
    source = {"id": "holiday", "summary": "Vacaciones", "start": {"date": "2026-10-12"}, "end": {"date": "2026-10-16"}}
    events, warnings = expand([source], start="2026-10-15", end="2026-10-16")
    assert not warnings and len(events) == 1
    assert events[0]["all_day"] and events[0]["start_date"] == "2026-10-12" and events[0]["end_date"] == "2026-10-16"
    assert events[0]["start"] == "2026-10-12T05:00:00Z"
    assert expand([source], start="2026-10-16", end="2026-10-17")[0] == []


def test_single_event_overlap_and_exclusive_end():
    events, warnings = expand([timed()], start="2026-10-15", end="2026-10-16")
    assert not warnings and events[0]["start"] == "2026-10-15T15:00:00Z"
    assert expand([timed(start="2026-10-14T23:00:00-05:00", end="2026-10-15T00:00:00-05:00")],
                  start="2026-10-15", end="2026-10-16")[0] == []
    assert len(expand([timed(start="2026-10-14T23:00:00-05:00", end="2026-10-15T00:01:00-05:00")],
                      start="2026-10-15", end="2026-10-16")[0]) == 1


def test_fields_remain_plain_text_and_ids_are_opaque_and_account_scoped():
    source = timed("remote-secret-id", summary="<img src=x onerror=alert(1)>", description="<script>test</script>",
                   htmlLink="https://calendar.google.com/calendar/event?eid=1")
    before = copy.deepcopy(source)
    first = expand([source])[0][0]
    assert first["title"] == source["summary"] and first["description"] == source["description"]
    assert "remote-secret-id" not in first["id"]
    other = normalize_events([source], account_id="other", calendar_key="calendar", time_zone="America/Bogota",
                             window=CalendarWindow.parse("2026-10-01", "2026-11-01", "UTC"))[0][0]
    assert first["id"] != other["id"] and source == before


def test_weekly_count_exdate_and_rdate():
    source = timed(start="2026-10-01T10:00:00-05:00", end="2026-10-01T11:00:00-05:00",
                   recurrence=["RRULE:FREQ=WEEKLY;COUNT=4", "EXDATE;TZID=America/Bogota:20261008T100000",
                               "RDATE;TZID=America/Bogota:20261010T100000"])
    events, warnings = expand([source])
    assert not warnings
    assert [item["start"][:10] for item in events] == ["2026-10-01", "2026-10-10", "2026-10-15", "2026-10-22"]


def test_monthly_invalid_dates_are_not_shifted_and_count_keeps_original_start():
    source = timed(start="2026-01-31T10:00:00-05:00", end="2026-01-31T11:00:00-05:00",
                   recurrence=["RRULE:FREQ=MONTHLY;COUNT=3"])
    events, warnings = expand([source], start="2026-03-01", end="2026-06-01")
    assert not warnings
    assert [item["start"][:10] for item in events] == ["2026-03-31", "2026-05-31"]


def test_dst_recurrence_keeps_local_clock_and_skips_nonexistent_time():
    source = {"id": "spring", "start": {"dateTime": "2026-03-07T02:30:00-05:00", "timeZone": "America/New_York"},
              "end": {"dateTime": "2026-03-07T03:30:00-05:00", "timeZone": "America/New_York"},
              "recurrence": ["RRULE:FREQ=DAILY;COUNT=3"]}
    events, warnings = expand([source], start="2026-03-07", end="2026-03-12", tz="America/New_York")
    assert not warnings
    assert [item["start"] for item in events] == ["2026-03-07T07:30:00Z", "2026-03-09T06:30:00Z", "2026-03-10T06:30:00Z"]


def test_all_day_recursions_and_exdates():
    source = {"id": "holiday", "start": {"date": "2026-10-01"}, "end": {"date": "2026-10-03"},
              "recurrence": ["RRULE:FREQ=WEEKLY;COUNT=3", "EXDATE;VALUE=DATE:20261008"]}
    events, warnings = expand([source])
    assert not warnings
    assert [item["start_date"] for item in events] == ["2026-10-01", "2026-10-15"]
    assert [item["end_date"] for item in events] == ["2026-10-03", "2026-10-17"]


def test_moved_and_cancelled_exceptions_replace_original_with_stable_identity():
    master = timed("series", start="2026-10-01T10:00:00-05:00", end="2026-10-01T11:00:00-05:00",
                   recurrence=["RRULE:FREQ=WEEKLY;COUNT=4"])
    initial = expand([master])[0]
    moved = timed("exception", start="2026-10-09T12:00:00-05:00", end="2026-10-09T13:00:00-05:00",
                  recurringEventId="series", originalStartTime={"dateTime": "2026-10-08T10:00:00-05:00"})
    cancelled = {"id": "cancelled", "status": "cancelled", "recurringEventId": "series",
                 "originalStartTime": {"dateTime": "2026-10-15T15:00:00Z"}}
    events, warnings = expand([master, moved, cancelled])
    assert not warnings
    assert [item["start"][:10] for item in events] == ["2026-10-01", "2026-10-09", "2026-10-22"]
    assert events[1]["id"] == initial[1]["id"]
    assert expand([master, moved], start="2026-10-09", end="2026-10-10")[0][0]["id"] == initial[1]["id"]


def test_cancelled_master_and_orphan_exception_do_not_revive_series():
    cancelled = timed("series", status="cancelled", recurrence=["RRULE:FREQ=DAILY;COUNT=3"])
    child = timed("child", recurringEventId="series", originalStartTime={"dateTime": "2026-10-15T10:00:00-05:00"})
    events, warnings = expand([cancelled, child])
    assert not events and warnings[0]["code"] == "orphan_exception"


def test_a_deleted_orphan_occurrence_does_not_make_a_complete_calendar_partial():
    child = {"id": "removed-child", "status": "cancelled", "recurringEventId": "missing-series",
             "originalStartTime": {"date": "2020-01-01"}}
    events, warnings = expand([child])
    assert events == [] and warnings == []


def test_a_confirmed_orphan_only_warns_in_its_actual_period_without_reviving_it():
    orphan = timed("old-child", start="2025-07-22T17:00:00-05:00", end="2025-07-22T18:00:00-05:00",
                   recurringEventId="missing-series", originalStartTime={"dateTime":"2025-07-22T17:00:00-05:00"})
    assert expand([orphan]) == ([], [])
    events, warnings = expand([orphan], start="2025-07-01", end="2025-08-01")
    assert not events and warnings[0]["code"] == "orphan_exception"


@pytest.mark.parametrize("rule", ["RRULE:FREQ=SECONDLY", "RRULE:FREQ=HOURLY", "RRULE:FREQ=DAILY;INTERVAL=0",
    "RRULE:FREQ=DAILY;COUNT=10000000", "RRULE:FREQ=DAILY;COUNT=3;UNTIL=20261020T000000Z",
    "RRULE:FREQ=DAILY;BYHOUR=1,2,3", "RRULE:FREQ=DAILY;BOGUS=1", "DTSTART:20260101T000000Z",
    "RRULE:FREQ=YEARLY;BYMONTH=13", "RRULE:FREQ=DAILY;BYSECOND=60", "RRULE:FREQ=YEARLY;BYDAY=54MO",
    "RRULE:FREQ=WEEKLY;BYDAY=1MO", "RRULE:FREQ=DAILY;BYSETPOS=1", "RRULE:FREQ=WEEKLY;BYMONTHDAY=1",
    "RRULE:FREQ=MONTHLY;BYYEARDAY=40", "RRULE:FREQ=DAILY;BYWEEKNO=2"])
def test_unsupported_dense_or_invalid_rules_are_reported_without_fabricated_occurrences(rule):
    events, warnings = expand([timed(recurrence=[rule])])
    assert not events and warnings[0]["code"] == "unsupported_recurrence"


def test_expansion_deadline_is_honest_not_silent_empty_success():
    events, warnings = expand([timed(recurrence=["RRULE:FREQ=DAILY"])], deadline=time.monotonic() - 1)
    assert not events and warnings[0]["code"] == "expansion_limit"


def test_invalid_calendar_zone_is_not_silently_replaced_by_utc():
    with pytest.raises(CalendarError):
        calendar_projection("account", {"id": "remote", "timeZone": "not-a-zone"})
    projected = calendar_projection("account", {"id": "remote", "color": "red;position:fixed", "summary": "<b>Title</b>"})
    assert projected["name"] == "<b>Title</b>" and projected["color"] is None


def test_yearly_leap_day_keeps_dates_and_count_across_missing_years():
    source = timed(start="2024-02-29T10:00:00-05:00", end="2024-02-29T11:00:00-05:00",
                   recurrence=["RRULE:FREQ=YEARLY;COUNT=3"])
    events, warnings = expand([source], start="2028-02-01", end="2028-03-01")
    assert not warnings and [event["start"][:10] for event in events] == ["2028-02-29"]
    assert expand([source], start="2036-02-01", end="2036-03-01")[0] == []


def test_monthly_last_weekday_with_setpos():
    source = timed(start="2026-09-30T10:00:00-05:00", end="2026-09-30T11:00:00-05:00",
                   recurrence=["RRULE:FREQ=MONTHLY;BYDAY=MO,TU,WE,TH,FR;BYSETPOS=-1;COUNT=3"])
    events, warnings = expand([source])
    assert not warnings and [event["start"][:10] for event in events] == ["2026-10-30"]


def test_historical_dense_rules_are_partial_before_dateutil_expansion():
    source = timed(start="1800-01-01T10:00:00-05:00", end="1800-01-01T11:00:00-05:00",
                   recurrence=["RRULE:FREQ=MONTHLY;BYMONTHDAY=1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20"])
    events, warnings = expand([source])
    assert not events and warnings[0]["code"] == "recurrence_history_limit"


def test_normalized_f10_documents_are_the_calendar_input_without_a_second_reader():
    from core.google_worker import _calendar, _event
    source = timed("master", recurrence=["RRULE:FREQ=WEEKLY;COUNT=2"], conferenceData={"entryPoints": [
        {"entryPointType": "phone", "uri": "https://phone.example.com/"},
        {"entryPointType": "video", "uri": "https://meet.google.com/qa-test"}]})
    normalized = _event(source)
    calendar = calendar_projection("gacc_" + "a" * 32, _calendar({"id": "remote", "summary": "QA",
        "timeZone": "America/Bogota", "backgroundColor": "#123abc", "primary": True}))
    events, warnings = expand([normalized])
    assert not warnings and len(events) == 2
    assert normalized.get("conferenceData") is None and normalized["video"] == events[0]["join_url"]
    assert calendar["color"] == "#123abc" and calendar["primary"]


def test_future_series_with_a_valid_until_does_not_make_an_earlier_view_partial():
    source = timed(start="2026-12-01T10:00:00-05:00", end="2026-12-01T11:00:00-05:00",
                   recurrence=["RRULE:FREQ=WEEKLY;UNTIL=20261231T235959Z"])
    assert expand([source]) == ([], [])


def test_projection_carries_the_users_answer_and_the_event_type():
    """Review 2026-10-19: F10 synced both, F11 dropped them, so declined meetings were reminded."""
    from core.calendar_models import CalendarWindow, normalize_events, reminds
    window = CalendarWindow.parse("2026-10-20", "2026-10-21", "America/Bogota")
    timed = lambda identity, **extra: {"id": identity, "status": "confirmed", "summary": identity,
        "start": {"dateTime": "2026-10-20T10:00:00-05:00"}, "end": {"dateTime": "2026-10-20T11:00:00-05:00"}, **extra}
    events, warnings = normalize_events(
        [timed("declined", response="declined"), timed("accepted", response="accepted"), timed("plain"),
         timed("ooo", eventType="outOfOffice"), timed("odd", eventType="<b>x</b>")],
        account_id="gacc_" + "a" * 32, calendar_key="cal_" + "b" * 32, time_zone="America/Bogota", window=window)
    by_title = {event["title"]: event for event in events}
    assert warnings == []
    assert [by_title[name]["declined"] for name in ("declined", "accepted", "plain")] == [True, False, False]
    assert by_title["ooo"]["event_type"] == "outOfOffice" and by_title["odd"]["event_type"] == "default"
    assert [reminds(by_title[name]) for name in ("declined", "accepted", "plain", "ooo")] == [False, True, True, False]
