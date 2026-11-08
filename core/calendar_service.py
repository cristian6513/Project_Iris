"""F11's calendar view consumes the one Google owner's local cache; GET never syncs."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any, Callable

from core.calendar_models import CalendarError, CalendarWindow, calendar_projection, normalize_events, text, utc_iso
from core.google_connections import GoogleConnectionsService, GoogleServiceError

SCHEMA = "iris.calendar/v1"
MAX_EVENTS = 12_000
_MESSAGES = {
    "cache_limit": "La caché excede el límite de esta vista; faltan datos por mostrar.",
    "invalid_calendar": "Un calendario guardado no tiene un formato válido.",
    "invalid_time_zone": "Un calendario o evento tiene una zona horaria que Iris no reconoce.",
    "invalid_event": "Un evento guardado no tiene fechas válidas y no se ha mostrado.",
    "event_duration_limit": "Un evento excede la duración máxima admitida por esta vista.",
    "unsupported_recurrence": "Una serie usa una regla de repetición que esta vista no admite.",
    "recurrence_history_limit": "Una serie requiere revisar demasiadas repeticiones históricas.",
    "expansion_limit": "La expansión de repeticiones alcanzó el límite de esta vista.",
    "event_limit": "Hay más eventos de los que puede mostrar esta vista.",
    "sync_error": "No se pudieron actualizar todos los calendarios; se muestra la última copia guardada.",
    "never_synced": "Falta actualizar los datos de esta cuenta o de uno de sus calendarios.",
    "sync_running": "Se están actualizando los calendarios; esta vista aún puede estar incompleta.",
    "client_unavailable": "La conexión con Google no está disponible en esta instalación.",
    "orphan_exception": (
        "Falta el evento original de una repetición guardada. "
        "Actualiza Google; algunas fechas pueden no mostrarse."
    ),
    "account_changed": (
        "La cuenta cambió durante la lectura. "
        "Actualiza la vista para consultar sus datos vigentes."
    ),
}


def _warning(code: str, **context: str) -> dict:
    return {"code": code, "message": _MESSAGES.get(code, "No se pudieron proyectar todos los datos del calendario."),
            **context}


def _granted(account: dict) -> list[str]:
    services = account.get("services", {})
    if not isinstance(services, dict):
        return []
    return [key for key, value in services.items() if isinstance(value, dict) and value.get("granted") is True]


def _live(account: dict) -> bool:
    return account.get("auth_state") in ("connected", "partial") and "calendar.read" in _granted(account)


def _public_sync(value: Any) -> dict:
    if not isinstance(value, dict):
        return {"state": "never", "last_success_at": None, "error": None}
    # Do not pass checkpoints or arbitrary fields even if another cache implementation supplies them.
    return {key: value.get(key) for key in ("state", "last_success_at", "next_attempt_at", "error", "coverage")}


class CalendarService:
    def __init__(self, google: GoogleConnectionsService | None, *, clock: Callable[[], float] = time.time) -> None:
        self.google = google
        self.clock = clock

    async def snapshot(self, start: str, end: str, time_zone: str = "UTC") -> dict:
        window = CalendarWindow.parse(start, end, time_zone)
        if self.google is None:
            cache = {"available": False, "accounts": [], "calendars": [], "warnings": []}
        else:
            cache = await self.google.calendar_cache()
        return await asyncio.to_thread(self._project, cache, window)

    def _project(self, cache: dict, window: CalendarWindow) -> dict:
        warnings, accounts, calendars, events = [], [], [], []
        for warning in cache.get("warnings", []):
            if isinstance(warning, dict):
                context = {key: warning[key] for key in ("account_id", "calendar_key")
                           if isinstance(warning.get(key), str)}
                warnings.append(_warning(str(warning.get("code", "cache_limit")), **context))
        for account in cache.get("accounts", []):
            if not isinstance(account, dict) or not isinstance(account.get("id"), str):
                continue
            sync = _public_sync((account.get("sync") or {}).get("calendar.read"))
            projected = {"id": account["id"], "email": text(account.get("email"), 320),
                         "label": text(account.get("label"), 200), "auth_state": account.get("auth_state"),
                         "granted": _granted(account), "sync": sync}
            accounts.append(projected)
            if _live(account):
                if sync.get("state") == "running":
                    warnings.append(_warning("sync_running", account_id=account["id"]))
                elif sync.get("error") or sync.get("state") in ("error", "backoff"):
                    warnings.append(_warning("sync_error", account_id=account["id"]))
                elif not sync.get("last_success_at"):
                    warnings.append(_warning("never_synced", account_id=account["id"]))
        connected_ids = {account["id"] for account in cache.get("accounts", []) if isinstance(account, dict) and _live(account)}
        deadline = time.monotonic() + 2.0
        for cached in cache.get("calendars", []):
            if not isinstance(cached, dict) or cached.get("account_id") not in connected_ids:
                continue
            account_id = cached["account_id"]
            try:
                calendar = calendar_projection(account_id, cached.get("calendar") or {})
                sync = _public_sync(cached.get("sync"))
                calendar["sync"] = sync
                calendars.append(calendar)
                if sync.get("error") or sync.get("state") in ("error", "backoff"):
                    warnings.append(_warning("sync_error", account_id=account_id, calendar_key=calendar["key"]))
                elif not sync.get("last_success_at"):
                    warnings.append(_warning("never_synced", account_id=account_id, calendar_key=calendar["key"]))
                projected, skipped = normalize_events(cached.get("events") or [], account_id=account_id,
                                                      calendar_key=calendar["key"], time_zone=calendar["time_zone"],
                                                      window=window, deadline=deadline)
                events.extend(projected)
                warnings.extend(_warning(item["code"], account_id=account_id, calendar_key=calendar["key"])
                                for item in skipped)
                if len(events) > MAX_EVENTS:
                    events = events[:MAX_EVENTS]
                    warnings.append(_warning("event_limit"))
                    break
            except CalendarError as exc:
                warnings.append(_warning(exc.code, account_id=account_id))
        available = cache.get("available") is True
        if not available:
            warnings.append(_warning("client_unavailable"))
        # Coalesce warning counts instead of echoing remote event ids or hundreds of repeated rows.
        unique = {}
        for warning in warnings:
            key = (warning["code"], warning.get("account_id"), warning.get("calendar_key"))
            if key in unique:
                unique[key]["count"] += 1
            else:
                unique[key] = dict(warning, count=1)
        return {"schema": SCHEMA, "server_now": utc_iso(datetime.fromtimestamp(self.clock(), timezone.utc)),
                "time_zone": window.time_zone, "range": {"start": window.start_date.isoformat(), "end": window.end_date.isoformat()},
                "coverage": {"start": utc_iso(window.start), "end": utc_iso(window.end)}, "read_only": True,
                "available": available, "complete": not warnings, "accounts": accounts, "calendars": calendars,
                # This inventory is consumed by reminders to retire removed calendars.
                # A bounded or malformed projection is not proof that a calendar vanished.
                "calendar_keys": [calendar["key"] for calendar in calendars] if available and not warnings else None,
                "connected_account_ids": sorted(connected_ids) if available else None,
                "events": sorted(events, key=lambda item: (item["start"], item["end"], item["id"])),
                "warnings": list(unique.values())}

    async def sync(self, account_ids: list[str] | None = None) -> dict:
        """One explicit calendar-only request, never Gmail or enabling a periodic service."""
        if self.google is None:
            raise GoogleServiceError("client_unavailable")
        cache = await self.google.calendar_cache()
        eligible = {account["id"] for account in cache.get("accounts", []) if isinstance(account, dict) and _live(account)}
        if account_ids is None:
            targets = sorted(eligible)
        elif not isinstance(account_ids, list) or not account_ids or len(account_ids) > 100 \
                or any(not isinstance(item, str) for item in account_ids) or len(set(account_ids)) != len(account_ids):
            raise CalendarError("invalid_request", "Selecciona cuentas válidas para actualizar el calendario.")
        else:
            targets = list(account_ids)
        if not targets:
            raise GoogleServiceError("not_connected")
        if any(account_id not in eligible for account_id in targets):
            raise GoogleServiceError("service_not_granted")
        # All requested identities/permissions were checked before dispatching the first round.
        accepted, errors = [], []
        for account_id in targets:
            try:
                await self.google.sync(account_id, ["calendar.read"])
                accepted.append(account_id)
            except GoogleServiceError as exc:
                errors.append({"account_id": account_id, "code": exc.code})
        return {"accepted": bool(accepted), "account_ids": accepted, "errors": errors}
