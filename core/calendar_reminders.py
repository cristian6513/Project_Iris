"""Deterministic F11 calendar phases, called by Core's existing evaluation loop.

There is no scheduler, AI, Google request, sound or camera here. ``tick`` consumes
the local normalized snapshot and an injected clock. All-day events are visible
in Calendar but intentionally have no timed reminder. A missed phase is eligible
for at most five minutes, so waking a PC does not replay its expired meetings.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Callable

from core.calendar_models import reminds
from core.calendar_store import CalendarNotice, CalendarStore, _identifier, _timestamp, notice_id


GRACE_SECONDS = 300


def utc_timestamp(value: Any) -> float:
    """Only an aware datetime is an instant; never guess DST for a naive value."""
    if not isinstance(value, str) or len(value) > 100:
        raise ValueError("Fecha de evento inválida.")
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("Fecha de evento inválida.") from None
    if date.tzinfo is None or date.utcoffset() is None:
        raise ValueError("El evento necesita una zona horaria explícita.")
    return _timestamp(date.astimezone(timezone.utc).timestamp())


class CalendarReminderEngine:
    def __init__(self, store: CalendarStore, *, clock: Callable[[], float] = time.time):
        self.store, self._clock = store, clock

    def tick(self, snapshot: dict[str, Any]) -> list[CalendarNotice]:
        """Return newly persisted notices; repeated ticks/restarts do not duplicate.

        ``connected_account_ids`` and ``calendar_keys`` must be full inventories,
        independent of the visible date range. Missing inventories are unknown,
        never an instruction to remove every reminder. A complete snapshot plus
        its UTC ``coverage`` can retire cancelled events by absence in that range.
        """
        if not isinstance(snapshot, dict):
            raise ValueError("Estado del calendario inválido.")
        now = _timestamp(self._clock())
        accounts = snapshot.get("connected_account_ids")
        calendars = snapshot.get("calendar_keys")
        if accounts is not None:
            if not isinstance(accounts, (list, tuple, set)):
                raise ValueError("Inventario de cuentas inválido.")
            accounts = {_identifier(value, "account") for value in accounts}
        if calendars is not None:
            if not isinstance(calendars, (list, tuple, set)):
                raise ValueError("Inventario de calendarios inválido.")
            calendars = {_identifier(value, "calendar") for value in calendars}
        complete = snapshot.get("complete") is True and snapshot.get("available") is True
        coverage = None
        if complete and isinstance(snapshot.get("coverage"), dict):
            coverage = (utc_timestamp(snapshot["coverage"].get("start")),
                        utc_timestamp(snapshot["coverage"].get("end")))
            if coverage[1] <= coverage[0]:
                raise ValueError("Cobertura del calendario inválida.")
        # Completeness without a known UTC window proves neither presence nor
        # absence for reminder delivery. Retain old rows, but emit nothing new.
        complete = complete and coverage is not None
        events = snapshot.get("events", [])
        if not isinstance(events, list) or len(events) > 100_000:
            raise ValueError("Inventario de eventos inválido.")
        starts: dict[tuple[str, str, str], float] = {}
        candidates: list[CalendarNotice] = []
        for event in events:
            if not isinstance(event, dict):
                raise ValueError("Evento inválido.")
            account = _identifier(event.get("account_id"), "account")
            calendar = _identifier(event.get("calendar_key"), "calendar")
            identity = _identifier(event.get("id"), "event")
            if event.get("all_day") is True or not reminds(event):
                # Explicitly replaced timed occurrence: also withdraws a notice created before
                # the user declined, or before the event became an out-of-office/focus block.
                starts[(account, calendar, identity)] = -1.0
                continue
            start, end = utc_timestamp(event.get("start")), utc_timestamp(event.get("end"))
            if end <= start:
                raise ValueError("El final del evento debe ser posterior al inicio.")
            key = (account, calendar, identity)
            if key in starts and starts[key] != start:
                raise ValueError("Dos versiones de una misma ocurrencia en el calendario.")
            starts[key] = start
            title = str(event.get("title") or "Evento sin título").strip()[:120] or "Evento sin título"
            for phase, due, boundary, prefix in (
                    ("before_60", start - 3600, start, "Empieza en una hora"),
                    ("start", start, end, "Empieza ahora")):
                if due < 0:
                    continue
                expires = min(boundary, due + GRACE_SECONDS)
                # Existing rows still receive content updates after the due window;
                # only insertion is gated by reconcile's phase-expiration check.
                candidates.append(CalendarNotice(
                    id=notice_id(account, calendar, identity, start, phase),
                    account_id=account, calendar_key=calendar, event_id=identity,
                    phase=phase, start=start, end=end, due_at=due, expires_at=expires,
                    title=title, text=f"{prefix}: {title}.",
                    join_url=str(event.get("join_url") or ""), event_url=str(event.get("event_url") or ""),
                    created_at=now, updated_at=now))
        return self.store.reconcile(candidates=candidates, event_starts=starts,
                                    connected_account_ids=accounts, calendar_keys=calendars,
                                    complete=complete, coverage=coverage)
