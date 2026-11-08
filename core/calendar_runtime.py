"""F11 integration: local cache, profile preferences and Core-driven reminder ticks.

The UI only reads this projection. Google owns synchronization and credentials;
the existing automation evaluation loop calls ``tick`` even without a calendar
window. Nothing here starts another scheduler, a model, voice or a capture.
"""
from __future__ import annotations

import asyncio
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, StrictBool, StrictInt

from core.calendar_models import safe_public_https
from core.calendar_api import _PrivateRoute
from core.calendar_reminders import CalendarReminderEngine
from core.calendar_service import CalendarService
from core.calendar_store import CalendarPreferences, CalendarStore, CalendarStoreError, profile_store_path
from core.google_api import require_local_peer
from core.google_connections import GoogleConnectionsService, GoogleServiceError


class CalendarRuntime:
    def __init__(self, home: Path, *, google: Callable[[], GoogleConnectionsService | None],
                 clock: Callable[[], float] = time.time, monotonic: Callable[[], float] = time.monotonic,
                 changed: Callable[[], None] | None = None):
        self.home = Path(home)
        self.google, self.clock, self.monotonic = google, clock, monotonic
        self.changed = changed
        self._store: CalendarStore | None = None
        self._store_lock = threading.RLock()
        self._tick_lock = asyncio.Lock()
        self._last_tick = float('-inf')
        self._last_signature = None
        self._closed = False

    def store(self, *, create: bool = False) -> CalendarStore | None:
        with self._store_lock:
            if self._closed:
                raise CalendarStoreError('El calendario se está cerrando.')
            if self._store is None and (create or profile_store_path(self.home).exists()):
                self._store = CalendarStore.for_profile(self.home, clock=self.clock)
            return self._store

    def preferences(self) -> dict:
        store = self.store()
        return (store.preferences() if store else CalendarPreferences()).public()

    def set_preferences(self, *, revision: int, **changes) -> dict:
        # Validate before creating even an empty preference store.
        if type(revision) is not int or revision < 0:
            raise ValueError('Revisión de calendario inválida.')
        with self._store_lock:
            existing = self.preferences()
            if existing['revision'] != revision:
                from core.calendar_store import CalendarRevisionConflict
                raise CalendarRevisionConflict('El calendario cambió; vuelve a cargarlo.')
            CalendarPreferences.parse({**existing, **{key: value for key, value in changes.items() if value is not None}})
            store = self.store(create=True)
            result = store.set_preferences(expected_revision=revision, **changes).public()
        self._notify()
        return result

    def _notify(self):
        if self.changed is not None:
            self.changed()

    @staticmethod
    async def _thread(function, *args, **kwargs):
        # A cancelled caller still collects the profile operation before releasing
        # its runtime guard. The thread cannot write after close() returns.
        task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
        return await GoogleConnectionsService._settle(task)

    async def snapshot(self, start: str, end: str, time_zone: str = 'UTC') -> dict:
        result = await CalendarService(self.google(), clock=self.clock).snapshot(start, end, time_zone)
        try:
            result['preferences'] = await self._thread(self.preferences)
        except (CalendarStoreError, OSError) as exc:
            raise GoogleServiceError('client_unavailable') from exc
        return result

    async def sync(self, account_ids=None) -> dict:
        return await CalendarService(self.google(), clock=self.clock).sync(account_ids)

    def inbox_rows(self):
        store = self.store()
        return store.inbox_rows(limit=300) if store else []

    def act(self, source_id: str, action: str, *, expected_revision: int, minutes=None):
        store = self.store()
        if store is None:
            from core.calendar_store import UnknownCalendarNotice
            raise UnknownCalendarNotice('Ese aviso ya no está disponible.')
        result = store.act(source_id, action, expected_revision=expected_revision, minutes=minutes)
        self._notify()
        return result

    async def tick(self, *, force: bool = False):
        """One cached evaluation, at most once per 15s, driven by Core's shared loop."""
        if self._closed:
            return []
        async with self._tick_lock:
            if not force and self.monotonic() - self._last_tick < 15:
                return []
            self._last_tick = self.monotonic()
            if self._closed:
                return []
            store = await self._thread(self.store)
            if store is None:
                return []
            prefs = await self._thread(store.preferences)
            if not prefs.reminders_enabled:
                return []
            local = datetime.fromtimestamp(self.clock(), ZoneInfo(prefs.time_zone)).date()
            try:
                snapshot = await CalendarService(self.google(), clock=self.clock).snapshot(
                    (local - timedelta(days=1)).isoformat(), (local + timedelta(days=2)).isoformat(), prefs.time_zone)
                await self._thread(CalendarReminderEngine(store, clock=self.clock).tick, snapshot)
                rows = await self._thread(store.inbox_rows, 300)
            except GoogleServiceError:
                return []  # transient owner/worker failure is not an empty calendar
            signature = tuple((row.id, row.revision, row.delivery_status(self.clock()), row.active) for row in rows)
            if signature != self._last_signature:
                self._last_signature = signature
                self._notify()
            return [row for row in rows if row.delivery_status(self.clock()) == 'pending'
                    and row.presentation_attempted_at is None
                    and row.presented_at is None and row.due_at <= self.clock() < row.expires_at]

    def claim_notice(self, source_id: str, revision: int, *, surface_id: str):
        """Persist a single visual presentation intent before sending it; never replay ambiguously."""
        store = self.store()
        row = store.notice(source_id) if store else None
        if row is None or row.presented_at is not None or row.delivery_status(self.clock()) != 'pending':
            return None
        if row.revision != revision or not row.due_at <= self.clock() < row.expires_at:
            return None
        result, changed = store.claim_presentation(source_id, expected_revision=revision, surface_id=surface_id)
        if changed:
            self._notify()
        return result if changed else None

    def presentation_receipt(self, source_id: str, *, token: str, surface_id: str, shown: bool):
        store = self.store()
        if store is None:
            return False
        _, changed = store.presentation_receipt(source_id, token=token, surface_id=surface_id, shown=shown)
        if changed:
            self._notify()
        return changed

    def public_notice(self, source_id: str):
        store = self.store()
        row = store.notice(source_id) if store else None
        if row is None or not row.active or self.clock() >= row.expires_at:
            return None
        date = datetime.fromtimestamp(row.start, ZoneInfo(store.preferences().time_zone)).date().isoformat()
        return {'id': row.id, 'title': row.title, 'text': row.text, 'event_id': row.event_id, 'date': date,
                'phase': row.phase, 'join_url': safe_public_https(row.join_url), 'expires_at': row.expires_at}

    async def close(self):
        # The common loop awaits its tick before this close; no detached work is owned here.
        async with self._tick_lock:
            self._closed = True


class CalendarPreferenceRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: StrictInt
    time_zone: str | None = None
    hidden_calendars: list[str] | None = None
    reminders_enabled: StrictBool | None = None


def create_calendar_preferences_router(get_runtime: Callable[[], CalendarRuntime | None]) -> APIRouter:
    router = APIRouter(prefix='/api/calendar', dependencies=[Depends(require_local_peer)], route_class=_PrivateRoute)
    headers = {'Cache-Control': 'no-store, private'}

    def failure(exc):
        code = getattr(exc, 'code', 'invalid_request')
        status = {'revision_conflict': 409, 'not_found': 404, 'store_unavailable': 503}.get(code, 422)
        message = {'revision_conflict': 'El calendario cambió; vuelve a cargarlo.',
                   'not_found': 'Ese aviso ya no está disponible.',
                   'store_unavailable': 'No se pudieron leer o guardar los ajustes del calendario.'}.get(
                       code, 'Los ajustes del calendario no son válidos.')
        return JSONResponse({'error': {'code': code, 'message': message}}, status_code=status, headers=headers)

    def runtime():
        value = get_runtime()
        if value is None:
            raise CalendarStoreError('Calendario no disponible.')
        return value

    @router.get('/preferences')
    async def preferences():
        try:
            return JSONResponse(await CalendarRuntime._thread(runtime().preferences), headers=headers)
        except (CalendarStoreError, OSError, ValueError) as exc:
            return failure(exc)

    @router.patch('/preferences')
    async def save(body: CalendarPreferenceRequest):
        try:
            return JSONResponse(await CalendarRuntime._thread(runtime().set_preferences,
                                **body.model_dump(exclude_unset=True)), headers=headers)
        except (CalendarStoreError, OSError, ValueError) as exc:
            return failure(exc)

    @router.get('/notices/{source_id}')
    async def notice(source_id: str):
        try:
            value = await CalendarRuntime._thread(runtime().public_notice, source_id)
            if value is None:
                from core.calendar_store import UnknownCalendarNotice
                raise UnknownCalendarNotice('Aviso no disponible.')
            return JSONResponse(value, headers=headers)
        except (CalendarStoreError, OSError, ValueError) as exc:
            return failure(exc)

    return router
