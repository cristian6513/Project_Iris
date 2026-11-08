"""F11 preferences and reminder deliveries, in the mutable profile's apps/calendar.

This store contains no Google credentials and never writes Google or automation stores.
Every open uses the shared profile SQLite boundary, sidecar and newer-version checks.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import math
import re
import secrets
import sqlite3
import time
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from core.profile_sqlite import ProfileSqliteStore, StoreSpec, profile_store_path as _profile_store_path


class CalendarStoreError(RuntimeError):
    code = "store_unavailable"


class CalendarStorePathError(CalendarStoreError):
    pass


class CalendarStoreVersionError(CalendarStoreError):
    pass


class CalendarRevisionConflict(CalendarStoreError):
    code = "revision_conflict"


class UnknownCalendarNotice(CalendarStoreError):
    code = "not_found"


_IDENTIFIERS = {
    "notice": re.compile(r"^[0-9a-f]{32}$"),
    "account": re.compile(r"^gacc_[0-9a-f]{32}$"),
    "calendar": re.compile(r"^cal_[0-9a-f]{32}$"),
    "event": re.compile(r"^event_[0-9a-f]{32}$"),
}
_SURFACE_ID = re.compile(r"^[A-Za-z0-9._:-]{1,96}$")


def _timestamp(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ValueError("Fecha de aviso inválida.")
    value = float(value)
    if not math.isfinite(value) or not 0 <= value <= 1e12:
        raise ValueError("Fecha de aviso inválida.")
    return value


def _identifier(value: Any, kind: str) -> str:
    """Match the exact opaque contract of this field, never a remote Google id."""
    if not isinstance(value, str) or not _IDENTIFIERS[kind].fullmatch(value):
        raise ValueError("Identificador de calendario inválido.")
    return value


def _safe_link(value: Any) -> str:
    """A link is opened only by a user later; reject executable/local URL forms here too."""
    if value == "":
        return ""
    if not isinstance(value, str) or len(value) > 4096 or any(ord(char) < 33 for char in value):
        raise ValueError("Enlace de calendario inválido.")
    try:
        parts = urlsplit(value)
        host = (parts.hostname or "").lower().rstrip(".")
        port = parts.port
    except ValueError:
        raise ValueError("Enlace de calendario inválido.") from None
    if (parts.scheme != "https" or not host or parts.username is not None or parts.password is not None
            or port not in (None, 443) or "\\" in value or "." not in host
            or host.endswith((".localhost", ".local", ".internal", ".invalid"))):
        raise ValueError("Enlace de calendario inválido.")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if not address.is_global:
            raise ValueError("Enlace de calendario inválido.")
    return value


@dataclass(frozen=True)
class CalendarPreferences:
    revision: int = 0
    time_zone: str = "UTC"
    hidden_calendars: tuple[str, ...] = ()
    reminders_enabled: bool = False

    def public(self) -> dict[str, Any]:
        return {**asdict(self), "hidden_calendars": list(self.hidden_calendars)}

    @classmethod
    def parse(cls, data: dict[str, Any]) -> CalendarPreferences:
        if not isinstance(data, dict) or set(data) - {"revision", "time_zone", "hidden_calendars", "reminders_enabled"}:
            raise ValueError("Preferencias de calendario inválidas.")
        revision = data.get("revision", 0)
        if type(revision) is not int or not 0 <= revision <= 1_000_000:
            raise ValueError("Revisión de calendario inválida.")
        zone = data.get("time_zone", "UTC")
        if not isinstance(zone, str) or not zone or len(zone) > 100:
            raise ValueError("Zona horaria inválida.")
        try:
            ZoneInfo(zone)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("Elige una zona horaria IANA disponible.") from None
        hidden = data.get("hidden_calendars", ())
        if not isinstance(hidden, (tuple, list)) or len(hidden) > 500:
            raise ValueError("Lista de calendarios inválida.")
        keys = tuple(sorted(set(_identifier(key, "calendar") for key in hidden)))
        enabled = data.get("reminders_enabled", False)
        if type(enabled) is not bool:
            raise ValueError("El permiso de recordatorios debe ser verdadero o falso.")
        return cls(revision, zone, keys, enabled)


@dataclass(frozen=True)
class CalendarNotice:
    id: str
    account_id: str
    calendar_key: str
    event_id: str
    phase: str
    start: float
    end: float
    due_at: float
    expires_at: float
    title: str
    text: str
    join_url: str = ""
    event_url: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    revision: int = 1
    status: str = "pending"
    presented_at: float | None = None
    read_at: float | None = None
    snoozed_until: float | None = None
    dismissed_at: float | None = None
    active: bool = True
    presentation_attempted_at: float | None = None
    presentation_token: str | None = field(default=None, repr=False)
    presentation_surface: str | None = field(default=None, repr=False)

    @property
    def presentation_uncertain(self) -> bool:
        return self.presentation_attempted_at is not None and self.presented_at is None

    def delivery_status(self, now: float) -> str:
        if not self.active:
            return "expired"
        status = self.status
        if status == "snoozed" and self.snoozed_until is not None and now >= self.snoozed_until:
            status = "presented" if self.presented_at is not None else "pending"
        if status in ("pending", "presented", "snoozed") and now >= self.expires_at:
            return "expired"
        return status

    def actions(self, now: float) -> tuple[str, ...]:
        if not self.active:
            return ()
        status = self.delivery_status(now)
        if status == "dismissed":
            return ("restore",)
        if status == "read":
            return ("unread", "dismiss")
        if status == "expired":
            return ("read", "dismiss")
        if status == "snoozed":
            return ("restore", "read", "dismiss")
        return ("read", "snooze", "dismiss")

    def public(self, now: float) -> dict[str, Any]:
        # Presentation tokens bind one socket surface to one durable intent. They
        # must never reach the ordinary inbox projection, even after a receipt.
        fields = ("id", "account_id", "calendar_key", "event_id", "phase", "start", "end", "due_at", "expires_at",
                  "title", "text", "join_url", "event_url", "created_at", "updated_at", "revision", "presented_at",
                  "read_at", "snoozed_until", "dismissed_at", "active", "presentation_attempted_at")
        return {**{key: getattr(self, key) for key in fields}, "status": self.delivery_status(now),
                "actions": list(self.actions(now)), "presentation_uncertain": self.presentation_uncertain}

    @classmethod
    def parse(cls, data: dict[str, Any]) -> CalendarNotice:
        notice = cls(**data)
        for key, kind in ((notice.id, "notice"), (notice.account_id, "account"),
                          (notice.calendar_key, "calendar"), (notice.event_id, "event")):
            _identifier(key, kind)
        for value in (notice.start, notice.end, notice.due_at, notice.expires_at,
                      notice.created_at, notice.updated_at):
            _timestamp(value)
        for value in (notice.presented_at, notice.read_at, notice.snoozed_until, notice.dismissed_at,
                      notice.presentation_attempted_at):
            if value is not None:
                _timestamp(value)
        attempted = notice.presentation_attempted_at is not None
        if attempted:
            _identifier(notice.presentation_token, "notice")
            if not isinstance(notice.presentation_surface, str) or not _SURFACE_ID.fullmatch(notice.presentation_surface):
                raise ValueError("Superficie de presentación inválida.")
        elif notice.presentation_token is not None or notice.presentation_surface is not None:
            raise ValueError("La presentación necesita un intento registrado.")
        if (notice.phase not in ("before_60", "start") or notice.end <= notice.start
                or not notice.due_at <= notice.expires_at <= notice.end
                or notice.created_at > notice.updated_at or type(notice.revision) is not int
                or notice.revision < 1 or type(notice.active) is not bool
                or notice.status not in ("pending", "presented", "read", "snoozed", "dismissed")):
            raise ValueError("Aviso de calendario inválido.")
        if not isinstance(notice.title, str) or not 1 <= len(notice.title) <= 120:
            raise ValueError("Título de calendario inválido.")
        if not isinstance(notice.text, str) or len(notice.text) > 2000:
            raise ValueError("Texto de calendario inválido.")
        _safe_link(notice.join_url)
        _safe_link(notice.event_url)
        if notice.status == "read" and notice.read_at is None:
            raise ValueError("El aviso leído necesita fecha.")
        if notice.status == "presented" and notice.presented_at is None:
            raise ValueError("El aviso presentado necesita fecha.")
        if notice.status == "dismissed" and notice.dismissed_at is None:
            raise ValueError("El aviso descartado necesita fecha.")
        if notice.status == "snoozed" and notice.snoozed_until is None:
            raise ValueError("El aviso pospuesto necesita fecha.")
        return notice


def notice_id(account: str, calendar: str, event: str, start: float, phase: str) -> str:
    """An occurrence's actual start is part of its stable, opaque phase identity."""
    body = json.dumps([account, calendar, event, start, phase], separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:32]


_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS notices (id TEXT PRIMARY KEY, account_id TEXT NOT NULL, "
    "calendar_key TEXT NOT NULL, event_id TEXT NOT NULL, start REAL NOT NULL, document TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS calendar_notice_event ON notices (account_id, calendar_key, event_id)",
)
CALENDAR_SPEC = StoreSpec(
    folder=("apps", "calendar"), filename="calendar.sqlite3", lock_name="calendar.lock",
    noun="almacén del calendario", schema=_SCHEMA, schema_version=1,
    store_name="iris.calendar-store/v1", path_error=CalendarStorePathError,
    version_error=CalendarStoreVersionError, busy_error=CalendarStoreError,
)


def profile_store_path(home: Path) -> Path:
    return _profile_store_path(CALENDAR_SPEC, home)


def _dump(value: Any) -> str:
    return json.dumps(asdict(value), ensure_ascii=False, separators=(",", ":"), allow_nan=False)


class CalendarStore(ProfileSqliteStore):
    SPEC = CALENDAR_SPEC

    def __init__(self, path: Path, *, root: Path | None = None, clock: Callable[[], float] = time.time):
        self._clock = clock
        super().__init__(path, root=root)

    @classmethod
    def for_profile(cls, home: Path, **kwargs) -> CalendarStore:
        return cls(profile_store_path(home), root=home, **kwargs)

    @staticmethod
    def _preferences(conn: sqlite3.Connection) -> CalendarPreferences:
        row = conn.execute("SELECT value FROM meta WHERE key='preferences'").fetchone()
        return CalendarPreferences.parse(json.loads(row[0])) if row else CalendarPreferences()

    def preferences(self) -> CalendarPreferences:
        with self._read() as conn:
            return self._preferences(conn)

    def set_preferences(self, *, expected_revision: int, time_zone: str | None = None,
                        hidden_calendars: Iterable[str] | None = None,
                        reminders_enabled: bool | None = None) -> CalendarPreferences:
        if type(expected_revision) is not int:
            raise ValueError("Revisión de calendario inválida.")
        with self._transaction() as conn:
            current = self._preferences(conn)
            if expected_revision != current.revision:
                raise CalendarRevisionConflict("El calendario cambió; vuelve a cargarlo.")
            data = current.public()
            if time_zone is not None:
                data["time_zone"] = time_zone
            if hidden_calendars is not None:
                if not isinstance(hidden_calendars, (list, tuple)):
                    raise ValueError("Lista de calendarios inválida.")
                data["hidden_calendars"] = hidden_calendars
            if reminders_enabled is not None:
                data["reminders_enabled"] = reminders_enabled
            data["revision"] += 1
            updated = CalendarPreferences.parse(data)
            conn.execute("INSERT OR REPLACE INTO meta VALUES ('preferences', ?)", (_dump(updated),))
            # Withdrawing permission/visibility takes effect now, not at the next tick.
            for (document,) in conn.execute("SELECT document FROM notices").fetchall():
                item = CalendarNotice.parse(json.loads(document))
                if item.active and (not updated.reminders_enabled or item.calendar_key in updated.hidden_calendars):
                    self._write(conn, replace(item, active=False, revision=item.revision + 1,
                                              updated_at=max(item.updated_at, self._clock())))
        return updated

    @staticmethod
    def _write(conn: sqlite3.Connection, notice: CalendarNotice) -> None:
        conn.execute("INSERT OR REPLACE INTO notices VALUES (?, ?, ?, ?, ?, ?)",
                     (notice.id, notice.account_id, notice.calendar_key, notice.event_id, notice.start, _dump(notice)))

    def inbox_rows(self, limit: int = 100, *, include_inactive: bool = False) -> list[CalendarNotice]:
        with self._read() as conn:
            rows = [CalendarNotice.parse(json.loads(row[0]))
                    for row in conn.execute("SELECT document FROM notices")]
        return sorted((row for row in rows if include_inactive or row.active),
                      key=lambda row: (row.updated_at, row.id), reverse=True)[:max(1, min(500, limit))]

    def notice(self, source_id: str) -> CalendarNotice | None:
        with self._read() as conn:
            row = conn.execute("SELECT document FROM notices WHERE id=?", (source_id,)).fetchone()
        return CalendarNotice.parse(json.loads(row[0])) if row else None

    def reconcile(self, *, candidates: list[CalendarNotice], event_starts: dict[tuple[str, str, str], float],
                  connected_account_ids: set[str] | None, calendar_keys: set[str] | None,
                  complete: bool, coverage: tuple[float, float] | None) -> list[CalendarNotice]:
        """Atomically withdraw old occurrences and insert due phases once; no external effects.

        Only an authoritative complete window can remove an event by absence. Inventory
        and explicit rescheduling are authoritative even when an unrelated calendar failed.
        A removed notice stays as a tombstone so restart/re-enable cannot replay it.
        """
        now = _timestamp(self._clock())
        inserted: list[CalendarNotice] = []
        with self._transaction() as conn:
            prefs = self._preferences(conn)
            old = [CalendarNotice.parse(json.loads(row[0]))
                   for row in conn.execute("SELECT document FROM notices")]
            for item in old:
                key = (item.account_id, item.calendar_key, item.event_id)
                removed = (not prefs.reminders_enabled or item.calendar_key in prefs.hidden_calendars
                           or (connected_account_ids is not None and item.account_id not in connected_account_ids)
                           or (calendar_keys is not None and item.calendar_key not in calendar_keys)
                           or (key in event_starts and item.start != event_starts[key])
                           or (complete and coverage is not None and coverage[0] <= item.start < coverage[1]
                               and key not in event_starts))
                if item.active and removed:
                    self._write(conn, replace(item, active=False, revision=item.revision + 1,
                                              updated_at=max(item.updated_at, now)))
            if not prefs.reminders_enabled:
                return []
            for proposed in candidates:
                # Recheck permission/visibility under the write lock: a stale snapshot
                # cannot deliver after the user's concurrent opt-out.
                item = CalendarNotice.parse(asdict(proposed))
                if (item.calendar_key in prefs.hidden_calendars
                        or (connected_account_ids is not None and item.account_id not in connected_account_ids)
                        or (calendar_keys is not None and item.calendar_key not in calendar_keys)):
                    continue
                found = conn.execute("SELECT document FROM notices WHERE id=?", (item.id,)).fetchone()
                if found:
                    previous = CalendarNotice.parse(json.loads(found[0]))
                    expires = item.expires_at
                    if previous.snoozed_until is not None:
                        expires = min(item.end, max(expires, previous.snoozed_until + 300))
                    content = (item.title, item.text, item.join_url, item.event_url, item.end, expires)
                    before = (previous.title, previous.text, previous.join_url, previous.event_url,
                              previous.end, previous.expires_at)
                    if previous.active and content != before:
                        self._write(conn, replace(previous, title=item.title, text=item.text,
                                                  join_url=item.join_url, event_url=item.event_url, end=item.end,
                                                  expires_at=expires, revision=previous.revision + 1,
                                                  updated_at=max(previous.updated_at, now)))
                    continue
                if (complete and coverage is not None
                        and item.start < coverage[1] and item.end > coverage[0]
                        and item.due_at <= now < item.expires_at):
                    if item.phase == "before_60":
                        later = notice_id(item.account_id, item.calendar_key, item.event_id, item.start, "start")
                        if conn.execute("SELECT 1 FROM notices WHERE id=?", (later,)).fetchone():
                            continue  # a backwards clock cannot replay the earlier phase
                    self._write(conn, item)
                    inserted.append(item)
        return inserted

    def act(self, source_id: str, action: str, *, expected_revision: int,
            minutes: int | None = None) -> tuple[CalendarNotice, bool]:
        if action not in ("read", "unread", "snooze", "dismiss", "restore", "presented"):
            raise ValueError("Acción de calendario inválida.")
        if type(expected_revision) is not int or expected_revision < 1:
            raise ValueError("Revisión del aviso inválida.")
        now = _timestamp(self._clock())
        with self._transaction() as conn:
            row = conn.execute("SELECT document FROM notices WHERE id=?", (source_id,)).fetchone()
            if row is None:
                raise UnknownCalendarNotice("Ese aviso ya no existe.")
            item = CalendarNotice.parse(json.loads(row[0]))
            status = item.delivery_status(now)
            holds = {"read": status == "read", "unread": item.read_at is None,
                     "snooze": status == "snoozed", "dismiss": status == "dismissed",
                     "restore": status not in ("dismissed", "snoozed"),
                     "presented": item.presented_at is not None}[action]
            if not item.active:
                raise UnknownCalendarNotice("El evento ya no está disponible.")
            if expected_revision != item.revision:
                if holds:
                    return item, False
                raise CalendarRevisionConflict("Ese aviso cambió; vuelve a cargarlo.")
            if holds:
                return item, False
            if action == "presented":
                if status not in ("pending", "presented"):
                    raise ValueError("El aviso ya no está pendiente de presentación.")
                item = replace(item, presented_at=now, status="presented")
            else:
                if action not in item.actions(now):
                    raise ValueError("Esa acción no está disponible para este aviso.")
                if action == "read":
                    item = replace(item, status="read", read_at=now, snoozed_until=None)
                elif action == "unread":
                    item = replace(item, status="presented" if item.presented_at is not None else "pending", read_at=None)
                elif action == "dismiss":
                    item = replace(item, status="dismissed", dismissed_at=now, snoozed_until=None)
                elif action == "restore":
                    item = replace(item, status="read" if item.read_at is not None else
                                   "presented" if item.presented_at is not None else "pending",
                                   dismissed_at=None, snoozed_until=None)
                elif action == "snooze":
                    if type(minutes) is not int or minutes not in (10, 30, 60, 240):
                        raise ValueError("Posponer: 10, 30, 60 o 240 minutos.")
                    until = now + minutes * 60
                    if until >= item.end:
                        raise ValueError("No se puede posponer más allá del final del evento.")
                    item = replace(item, status="snoozed", snoozed_until=until,
                                   expires_at=min(item.end, max(item.expires_at, until + 300)))
            item = replace(item, revision=item.revision + 1, updated_at=max(item.updated_at, now))
            self._write(conn, item)
        return item, True

    def presented(self, source_id: str, *, expected_revision: int) -> tuple[CalendarNotice, bool]:
        return self.act(source_id, "presented", expected_revision=expected_revision)

    def claim_presentation(self, source_id: str, *, expected_revision: int,
                           surface_id: str) -> tuple[CalendarNotice, bool]:
        """Commit one delivery intent, without claiming that a visual was displayed.

        An ambiguous send remains durable across restart. No automatic caller may
        acquire a second intent for the same notice, including another surface.
        """
        _identifier(source_id, "notice")
        if type(expected_revision) is not int or expected_revision < 1:
            raise ValueError("Revisión del aviso inválida.")
        if not isinstance(surface_id, str) or not _SURFACE_ID.fullmatch(surface_id):
            raise ValueError("Superficie de presentación inválida.")
        with self._transaction() as conn:
            # The lock may have waited across the notice deadline.
            now = _timestamp(self._clock())
            row = conn.execute("SELECT document FROM notices WHERE id=?", (source_id,)).fetchone()
            if row is None:
                raise UnknownCalendarNotice("Ese aviso ya no existe.")
            item = CalendarNotice.parse(json.loads(row[0]))
            prefs = self._preferences(conn)
            if (item.presentation_attempted_at is not None or item.presented_at is not None
                    or not item.active or not prefs.reminders_enabled or item.calendar_key in prefs.hidden_calendars
                    or item.delivery_status(now) != "pending" or not item.due_at <= now < item.expires_at):
                return item, False
            if item.revision != expected_revision:
                raise CalendarRevisionConflict("Ese aviso cambió; vuelve a cargarlo.")
            item = replace(item, presentation_attempted_at=now, presentation_token=secrets.token_hex(16),
                           presentation_surface=surface_id, revision=item.revision + 1,
                           updated_at=max(item.updated_at, now))
            self._write(conn, item)
        return item, True

    def presentation_receipt(self, source_id: str, *, token: str, surface_id: str,
                             shown: bool) -> tuple[CalendarNotice, bool]:
        """Record a matched surface's receipt; it is never evidence of actual reading."""
        _identifier(source_id, "notice")
        _identifier(token, "notice")
        if not isinstance(surface_id, str) or not _SURFACE_ID.fullmatch(surface_id) or type(shown) is not bool:
            raise ValueError("Recibo de presentación inválido.")
        with self._transaction() as conn:
            now = _timestamp(self._clock())
            row = conn.execute("SELECT document FROM notices WHERE id=?", (source_id,)).fetchone()
            if row is None:
                raise UnknownCalendarNotice("Ese aviso ya no existe.")
            item = CalendarNotice.parse(json.loads(row[0]))
            if (item.presentation_attempted_at is None or item.presentation_token is None
                    or not hmac.compare_digest(item.presentation_token, token) or item.presentation_surface != surface_id):
                raise ValueError("El recibo no corresponde a la presentación pendiente.")
            prefs = self._preferences(conn)
            if (not shown or item.presented_at is not None or not item.active or not prefs.reminders_enabled
                    or item.calendar_key in prefs.hidden_calendars or not item.due_at <= now < item.expires_at):
                return item, False
            # A user might have read, dismissed or postponed the inbox row while
            # the socket receipt travelled back. Preserve that newer user action.
            status = "presented" if item.status == "pending" else item.status
            item = replace(item, presented_at=now, status=status, revision=item.revision + 1,
                           updated_at=max(item.updated_at, now))
            self._write(conn, item)
        return item, True
