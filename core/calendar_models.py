"""F11: bounded, read-only projection of Google's masters and exceptions.

No provider, HTML renderer or network is called here. Dates retain their source timezone;
all-day ends are exclusive. Unsupported/dense recurrence rules are reported, never guessed.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, time as daytime, timedelta, timezone
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil.parser import isoparse
from dateutil.rrule import rrulestr
from dateutil.tz import datetime_exists

UTC = timezone.utc
MAX_RANGE_DAYS = 93
MAX_OCCURRENCES = 4000
MAX_SCAN_PERIODS = 50_000
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
_FREQ = {"DAILY": 1, "WEEKLY": 7, "MONTHLY": 28, "YEARLY": 365}
_RULE_KEYS = frozenset({"FREQ", "INTERVAL", "COUNT", "UNTIL", "BYDAY", "BYMONTH", "BYMONTHDAY",
                        "BYYEARDAY", "BYWEEKNO", "BYSETPOS", "BYHOUR", "BYMINUTE", "BYSECOND", "WKST"})
_NUMERIC_RULE = {"BYMONTH": (1, 12, False), "BYMONTHDAY": (-31, 31, True),
                 "BYYEARDAY": (-366, 366, True), "BYWEEKNO": (-53, 53, True),
                 "BYSETPOS": (-366, 366, True), "BYHOUR": (0, 23, False),
                 "BYMINUTE": (0, 59, False), "BYSECOND": (0, 59, False)}


class CalendarError(ValueError):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


def text(value: Any, limit: int = 4000) -> str:
    """Untrusted plain text. Consumers must use text nodes, never innerHTML."""
    if not isinstance(value, str):
        return ""
    return "".join(char for char in value[:limit] if ord(char) >= 32 or char in "\n\t")


def opaque_id(kind: str, *parts: str) -> str:
    raw = json.dumps(parts, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return kind + "_" + hashlib.sha256(raw).hexdigest()[:32]


def zone(value: Any) -> ZoneInfo:
    if not isinstance(value, str) or not value or len(value) > 100 or value.startswith(("/", ".")):
        raise CalendarError("invalid_time_zone", "Selecciona una zona horaria IANA válida.")
    try:
        return ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise CalendarError("invalid_time_zone", "Selecciona una zona horaria IANA válida.") from None


def utc_iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class CalendarWindow:
    start_date: date
    end_date: date
    time_zone: str
    start: datetime
    end: datetime

    @classmethod
    def parse(cls, start: str, end: str, time_zone: str) -> "CalendarWindow":
        tz = zone(time_zone)
        try:
            if not isinstance(start, str) or not isinstance(end, str) or not _DATE.fullmatch(start) or not _DATE.fullmatch(end):
                raise ValueError
            first, last = date.fromisoformat(start), date.fromisoformat(end)
            if not 0 < (last - first).days <= MAX_RANGE_DAYS:
                raise ValueError
        except (ValueError, OverflowError):
            raise CalendarError("invalid_range", "El rango debe tener entre 1 y 93 días; el final es exclusivo.") from None
        return cls(first, last, time_zone, datetime.combine(first, daytime(), tz).astimezone(UTC),
                   datetime.combine(last, daytime(), tz).astimezone(UTC))


def safe_public_https(value: Any) -> str | None:
    """Public-domain HTTPS only, without credentials/IP literals/odd ports or local names.

    This validates a link, not its DNS or future redirects. Opening is always an explicit click.
    """
    if not isinstance(value, str) or not value or len(value) > 2000 or any(ord(c) <= 32 for c in value) or "\\" in value:
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme.lower() != "https" or parsed.username is not None or parsed.password is not None \
                or parsed.port not in (None, 443) or not parsed.hostname:
            return None
        host = parsed.hostname.rstrip(".").encode("idna").decode("ascii").lower()
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            return None
        suffixes = ("localhost", "local", "localdomain", "internal", "lan", "home", "test", "invalid", "example")
        if "." not in host or any(host == suffix or host.endswith("." + suffix) for suffix in suffixes):
            return None
        if len(host) > 253 or not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", part) for part in host.split(".")):
            return None
        if not re.fullmatch(r"[a-z]{2,63}|xn--[a-z0-9-]{2,59}", host.rsplit(".", 1)[1]):
            return None
        return urlunsplit(("https", host, parsed.path, parsed.query, parsed.fragment))
    except (ValueError, UnicodeError):
        return None


def conference_url(document: dict) -> str | None:
    # F10's worker stores a video's URI in `video`, only from a conference entryPointType=video.
    candidates = [document.get("video")]
    conference = document.get("conferenceData")
    if isinstance(conference, dict):
        entries = conference.get("entryPoints")
        if isinstance(entries, list):
            candidates.extend(item.get("uri") for item in entries[:20]
                              if isinstance(item, dict) and item.get("entryPointType") == "video")
    candidates.append(document.get("hangoutLink"))
    return next((valid for value in candidates if (valid := safe_public_https(value))), None)


def calendar_projection(account_id: str, document: dict) -> dict:
    remote_id = text(document.get("id"), 512)
    if not remote_id:
        raise CalendarError("invalid_calendar")
    tz_name = document.get("timeZone") or "UTC"
    zone(tz_name)
    color = document.get("color") or document.get("backgroundColor")
    return {"key": opaque_id("cal", account_id, remote_id), "account_id": account_id,
            "name": text(document.get("summaryOverride") or document.get("summary"), 500) or "Calendario",
            "color": color if isinstance(color, str) and _COLOR.fullmatch(color) else None,
            "time_zone": tz_name, "primary": document.get("primary") is True}


def _when(document: Any, fallback: str) -> tuple[datetime, bool]:
    if not isinstance(document, dict):
        raise CalendarError("invalid_event")
    tz = zone(document.get("timeZone") or fallback)
    if isinstance(document.get("date"), str):
        raw = document["date"]
        if not _DATE.fullmatch(raw):
            raise CalendarError("invalid_event")
        try:
            return datetime.combine(date.fromisoformat(raw), daytime(), tz), True
        except ValueError:
            raise CalendarError("invalid_event") from None
    raw = document.get("dateTime")
    if not isinstance(raw, str) or len(raw) > 64:
        raise CalendarError("invalid_event")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if "T" not in raw:
            raise ValueError
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=tz)
        else:
            parsed = parsed.astimezone(tz)
        if not datetime_exists(parsed):
            raise ValueError
        return parsed, False
    except (ValueError, OverflowError):
        raise CalendarError("invalid_event") from None


def _identity(value: datetime, all_day: bool) -> str:
    return value.date().isoformat() if all_day else utc_iso(value)


def _bounds(document: dict, fallback: str) -> tuple[datetime, datetime, bool]:
    start, all_day = _when(document.get("start"), fallback)
    end, end_all_day = _when(document.get("end"), fallback)
    if all_day != end_all_day or end.astimezone(UTC) <= start.astimezone(UTC):
        raise CalendarError("invalid_event")
    if (end.astimezone(UTC) - start.astimezone(UTC)).days > 366:
        raise CalendarError("event_duration_limit")
    return start, end, all_day


def _rdate(line: str, fallback: str, all_day: bool) -> list[datetime]:
    head, sep, body = line.partition(":")
    if not sep or not body or len(body.split(",")) > 100:
        raise CalendarError("unsupported_recurrence")
    parameters = head.split(";")[1:]
    tz = zone(fallback)
    for parameter in parameters:
        name, _, value = parameter.partition("=")
        if name == "TZID":
            tz = zone(value)
        elif name == "VALUE" and value == "DATE":
            if not all_day:
                raise CalendarError("unsupported_recurrence")
        else:
            raise CalendarError("unsupported_recurrence")
    result = []
    try:
        for raw in body.split(","):
            if "/" in raw:
                raise ValueError
            parsed = isoparse(raw)
            if all_day and ("T" in raw or parsed.time() != daytime()):
                raise ValueError
            if not all_day and "T" not in raw:
                raise ValueError
            parsed = parsed.replace(tzinfo=tz) if parsed.tzinfo is None else parsed.astimezone(tz)
            if datetime_exists(parsed):
                result.append(parsed)
    except (ValueError, TypeError, OverflowError):
        raise CalendarError("unsupported_recurrence") from None
    return result


def _rule(line: str, start: datetime, end: datetime):
    values = {}
    for pair in line.removeprefix("RRULE:").split(";"):
        key, sep, value = pair.partition("=")
        if not sep or not value or key not in _RULE_KEYS or key in values:
            raise CalendarError("unsupported_recurrence")
        values[key] = value
    freq = values.get("FREQ")
    if freq not in _FREQ or ("COUNT" in values and "UNTIL" in values):
        raise CalendarError("unsupported_recurrence")
    if (("BYMONTHDAY" in values and freq == "WEEKLY")
            or ("BYYEARDAY" in values and freq != "YEARLY")
            or ("BYWEEKNO" in values and freq != "YEARLY")
            or ("BYSETPOS" in values and not any(key.startswith("BY") and key != "BYSETPOS" for key in values))):
        raise CalendarError("unsupported_recurrence")
    try:
        interval = int(values.get("INTERVAL", "1"))
        count = int(values["COUNT"]) if "COUNT" in values else None
        if not 1 <= interval <= 366 or (count is not None and not 1 <= count <= MAX_SCAN_PERIODS):
            raise ValueError
        periods = max(0, (end.date() - start.date()).days) / (_FREQ[freq] * interval)
        if periods > MAX_SCAN_PERIODS:
            raise CalendarError("recurrence_history_limit")
        cardinality = 1
        for key, value in values.items():
            pieces = value.split(",")
            if len(pieces) > 366 or (key in ("BYHOUR", "BYMINUTE", "BYSECOND") and len(pieces) != 1):
                raise ValueError
            if key.startswith("BY"):
                cardinality *= len(pieces)
            if key in _NUMERIC_RULE:
                low, high, exclude_zero = _NUMERIC_RULE[key]
                if any(not low <= int(piece) <= high or (exclude_zero and int(piece) == 0) for piece in pieces):
                    raise ValueError
            if key == "BYDAY":
                for piece in pieces:
                    match = re.fullmatch(r"([+-]?[1-9]\d?)?(?:MO|TU|WE|TH|FR|SA|SU)", piece)
                    if not match:
                        raise ValueError
                    ordinal = match.group(1)
                    if ordinal and (abs(int(ordinal)) > 53 or freq not in ("MONTHLY", "YEARLY") or "BYWEEKNO" in values):
                        raise ValueError
            if key == "WKST" and value not in ("MO", "TU", "WE", "TH", "FR", "SA", "SU"):
                raise ValueError
        if cardinality > 2000:
            raise ValueError
        if periods * cardinality > MAX_SCAN_PERIODS:
            raise CalendarError("recurrence_history_limit")
        until = end.astimezone(UTC)
        if "UNTIL" in values:
            raw_until = values["UNTIL"]
            parsed = isoparse(raw_until)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=start.tzinfo)
                if "T" not in raw_until:
                    parsed = parsed + timedelta(days=1, seconds=-1)
            if parsed.astimezone(UTC) < start.astimezone(UTC):
                raise ValueError
            # A valid series can begin after this view's exclusive end. Bounding
            # its iteration before DTSTART must yield no rows, not a bad-rule warning.
            until = min(until, parsed.astimezone(UTC))
        # Bound even impossible rules before iterating. COUNT is kept at its original DTSTART.
        if count is None:
            values["UNTIL"] = until.strftime("%Y%m%dT%H%M%SZ")
        else:
            values["UNTIL"] = until.strftime("%Y%m%dT%H%M%SZ")
            # dateutil disallows COUNT+UNTIL together. Apply the count while iterating below.
            values.pop("COUNT")
        parsed_rule = rrulestr("RRULE:" + ";".join(key + "=" + value for key, value in values.items()),
                              dtstart=start, cache=False)
        return parsed_rule, count
    except CalendarError:
        raise
    except (ValueError, TypeError, OverflowError):
        raise CalendarError("unsupported_recurrence") from None


def _occurrences(document: dict, start: datetime, end: datetime, all_day: bool,
                 window: CalendarWindow, deadline: float) -> list[datetime]:
    lines = document.get("recurrence")
    if not isinstance(lines, list) or not lines:
        return [start]
    if len(lines) > 20 or any(not isinstance(line, str) or len(line) > 500 for line in lines):
        raise CalendarError("unsupported_recurrence")
    included = {_identity(start, all_day): start}
    excluded = set()
    for line in lines:
        if time.monotonic() > deadline:
            raise CalendarError("expansion_limit")
        if line.startswith("RRULE:"):
            if all_day and any(key + "=" in line for key in ("BYHOUR", "BYMINUTE", "BYSECOND")):
                raise CalendarError("unsupported_recurrence")
            parsed, count = _rule(line, start, window.end)
            valid_count = 0
            for index, occurrence in enumerate(parsed):
                if index >= MAX_SCAN_PERIODS or time.monotonic() > deadline:
                    raise CalendarError("expansion_limit")
                if occurrence.astimezone(UTC) >= window.end:
                    break
                if not datetime_exists(occurrence):
                    continue
                if count is not None and valid_count >= count:
                    break
                valid_count += 1
                if (occurrence + (end - start)).astimezone(UTC) <= window.start:
                    continue
                included[_identity(occurrence, all_day)] = occurrence
                if len(included) > MAX_OCCURRENCES:
                    raise CalendarError("expansion_limit")
        elif line.startswith(("RDATE:", "RDATE;")):
            for occurrence in _rdate(line, str(start.tzinfo), all_day):
                included[_identity(occurrence, all_day)] = occurrence
        elif line.startswith(("EXDATE:", "EXDATE;")):
            excluded.update(_identity(item, all_day) for item in _rdate(line, str(start.tzinfo), all_day))
        else:
            raise CalendarError("unsupported_recurrence")
    return [value for identity, value in included.items() if identity not in excluded]


def _project(document: dict, account_id: str, calendar_key: str, fallback: str,
             start: datetime, end: datetime, all_day: bool, remote_id: str, original: str,
             recurring: bool) -> dict:
    return {"id": opaque_id("event", account_id, calendar_key, remote_id, original),
            "account_id": account_id, "calendar_key": calendar_key,
            "title": text(document.get("summary"), 500) or "Sin título", "start": utc_iso(start), "end": utc_iso(end),
            "all_day": all_day, "start_date": start.date().isoformat() if all_day else None,
            "end_date": end.date().isoformat() if all_day else None, "time_zone": str(start.tzinfo) or fallback,
            "location": text(document.get("location"), 500), "description": text(document.get("description")),
            "join_url": conference_url(document), "event_url": safe_public_https(document.get("htmlLink")),
            "recurring": recurring,
            # F10 syncs the user's own answer and Google's event type; reminders need both
            # (review 2026-10-19: declined meetings and «Fuera de la oficina» were reminded).
            "declined": document.get("response") == "declined",
            "event_type": event_type(document)}


# Google event types that mark the user's time, not a meeting to be reminded of.
NO_REMINDER_TYPES = frozenset({"outOfOffice", "focusTime", "workingLocation"})


def event_type(document: dict) -> str:
    value = document.get("eventType")
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z]{1,40}", value) else "default"


def reminds(event: dict) -> bool:
    """A timed occurrence the user still attends: not declined, not a status block."""
    return not event.get("declined") and event.get("event_type", "default") not in NO_REMINDER_TYPES


def normalize_events(records: Iterable[dict], *, account_id: str, calendar_key: str, time_zone: str,
                     window: CalendarWindow, deadline: float | None = None) -> tuple[list[dict], list[dict]]:
    """Expand masters, then replace/suppress original instances using each exception.

    Moved exceptions are selected by their actual time, even when the original is outside
    the query. Stable opaque ids retain the original instance across title/time updates.
    """
    deadline = deadline if deadline is not None else time.monotonic() + 2.0
    zone(time_zone)
    documents = [item for item in records if isinstance(item, dict)]
    warnings = []
    result = {}
    exceptions = {}
    masters = {item.get("id") for item in documents if item.get("recurrence")
               and not item.get("recurringEventId") and item.get("status") != "cancelled"
               and isinstance(item.get("id"), str)}
    for document in documents:
        master = document.get("recurringEventId")
        if not isinstance(master, str) or not master:
            continue
        if master not in masters:
            if document.get("status") == "cancelled":
                continue  # a removed occurrence cannot revive an absent series
            try:
                start, end, _ = _bounds(document, time_zone)
                if (start.astimezone(UTC) >= window.end
                        or end.astimezone(UTC) <= window.start):
                    continue  # a detached occurrence outside this view cannot hide its events
            except CalendarError:
                pass  # its range is unknown; keep an honest incomplete-view warning
            warnings.append({"code": "orphan_exception", "account_id": account_id, "calendar_key": calendar_key})
            continue
        try:
            original, all_day = _when(document.get("originalStartTime"), time_zone)
            exceptions[(master, _identity(original, all_day))] = document
        except CalendarError as exc:
            warnings.append({"code": exc.code, "account_id": account_id, "calendar_key": calendar_key})
    for document in documents:
        if document.get("recurringEventId") or document.get("status") == "cancelled":
            continue
        remote_id = text(document.get("id"), 512)
        try:
            if not remote_id:
                raise CalendarError("invalid_event")
            start, end, all_day = _bounds(document, time_zone)
            recurring = bool(document.get("recurrence"))
            occurrences = _occurrences(document, start, end, all_day, window, deadline)
            for occurrence in occurrences:
                identity = _identity(occurrence, all_day)
                if (remote_id, identity) in exceptions:
                    continue
                occurrence_end = occurrence + (end - start) if recurring else end
                if occurrence.astimezone(UTC) >= window.end or occurrence_end.astimezone(UTC) <= window.start:
                    continue
                event = _project(document, account_id, calendar_key, time_zone, occurrence, occurrence_end,
                                 all_day, remote_id, identity if recurring else "", recurring)
                result[event["id"]] = event
        except (CalendarError, ValueError, TypeError, OverflowError) as exc:
            warnings.append({"code": exc.code if isinstance(exc, CalendarError) else "invalid_event",
                             "account_id": account_id, "calendar_key": calendar_key})
    for (master, identity), document in exceptions.items():
        if document.get("status") == "cancelled":
            continue
        try:
            start, end, all_day = _bounds(document, time_zone)
            if start.astimezone(UTC) >= window.end or end.astimezone(UTC) <= window.start:
                continue
            event = _project(document, account_id, calendar_key, time_zone, start, end, all_day, master, identity, True)
            result[event["id"]] = event
        except CalendarError as exc:
            warnings.append({"code": exc.code, "account_id": account_id, "calendar_key": calendar_key})
    return sorted(result.values(), key=lambda item: (item["start"], item["end"], item["id"])), warnings
