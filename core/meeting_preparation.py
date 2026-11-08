"""Explicit per-event F15 consent. No periodic analysis and no reminder dependency."""
import asyncio
from datetime import date, datetime, timedelta
import hashlib
import json
from pathlib import Path
import time
import uuid
from zoneinfo import ZoneInfo

from core.calendar_models import CalendarWindow
from core.calendar_service import CalendarService
from core.calendar_runtime import CalendarRuntime
from core.mail_classifier_client import worker
from core.meeting_store import MeetingStore, MeetingError, SPEC
from core.meeting_worker import MARKER, validate_result
from core.profile_sqlite import profile_store_path


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


async def invoke(home, request):
    return await worker(home, request, script_name="meeting_worker.py", marker=MARKER)


class MeetingPreparationRuntime:
    def __init__(self, home, *, google, hermes_home=None, invoke=invoke, clock=time.time,
                 changed=None, functional_enabled=lambda: True, timeout=60, calendar=lambda: None):
        from core.vision_model import profile_home
        self.home = Path(home)
        self.hermes_home = Path(hermes_home if hermes_home is not None else profile_home(None)).resolve()
        self.google, self.invoke, self.clock = google, invoke, clock
        self.changed, self.functional_enabled, self.timeout = changed, functional_enabled, timeout
        self.calendar = calendar
        self._store = None
        self._previews, self._tasks = {}, {}
        self._preview_tasks = set()
        self._lock = asyncio.Lock()
        self._closed = False

    def store(self, create=False):
        if self._store is None and (create or profile_store_path(SPEC, self.home).exists()):
            self._store = MeetingStore.for_profile(self.home)
        return self._store

    async def _db(self, fn, *args):
        return await CalendarRuntime._thread(fn, *args)

    def _notify(self):
        if self.changed:
            self.changed()

    async def _source(self, event_id, day):
        owner = self.google()
        if self._closed or not self.functional_enabled() or owner is None:
            raise MeetingError("Calendario no está disponible.")
        start = date.fromisoformat(day)
        cache = await owner.calendar_cache()
        view = await self._db(CalendarService(owner, clock=self.clock)._project, cache,
                              CalendarWindow.parse(start.isoformat(), (start + timedelta(days=1)).isoformat(), "UTC"))
        event = next((e for e in view["events"] if e["id"] == event_id), None)
        account = next((a for a in cache.get("accounts", []) if event and a.get("id") == event["account_id"]), None)
        if (not view["available"] or event is None or account is None
                or account.get("auth_state") not in {"connected", "partial"}
                or account.get("services", {}).get("calendar.read", {}).get("granted") is not True
                or any(w.get("account_id") in (None, event["account_id"]) for w in view["warnings"])):
            raise MeetingError("Actualiza Calendario y selecciona un evento disponible.")
        binding = {key: account[key] for key in ("id", "generation", "revision")}
        return owner, event, binding

    async def preview(self, event_id, day, provider, model, *, description=False, notes=""):
        task = asyncio.current_task()
        self._preview_tasks.add(task)
        try:
            return await self._preview(event_id, day, provider, model, description=description, notes=notes)
        finally:
            self._preview_tasks.discard(task)

    async def _preview(self, event_id, day, provider, model, *, description=False, notes=""):
        if type(description) is not bool or not isinstance(notes, str) or len(notes) > 4000:
            raise MeetingError("Contexto inválido.")
        if self._closed or len(self._tasks) >= 1:
            raise MeetingError("Espera a que termine la preparación actual.")
        owner, event, binding = await self._source(event_id, day)
        # A probe only resolves metadata; no event/context is sent to the model.
        async with asyncio.timeout(self.timeout):
            probe = await self.invoke(self.hermes_home, {"command": "probe", "provider": provider, "model": model})
        if not probe.get("ok") or not isinstance(probe.get("route"), dict):
            raise MeetingError("El proveedor o modelo no está disponible para esta función.")
        route = probe["route"]
        if route.get("provider") != provider or route.get("model") != model:
            raise MeetingError("El destino cambió. Revisa el proveedor.")
        _, latest, current = await self._source(event_id, day)
        if self.google() is not owner or digest([latest, current]) != digest([event, binding]):
            raise MeetingError("El evento o la cuenta cambió. Vuelve a previsualizar.")
        sources = {"event": {key: event[key] for key in ("title", "start", "end", "all_day")}}
        if description:
            sources["event"]["description"] = event.get("description", "")[:12000]
        if notes.strip():
            sources["notes"] = notes.strip()
        calendar = self.calendar()
        zone = (await self._db(calendar.preferences))["time_zone"] if calendar is not None else "UTC"
        calendar_date = event["start_date"] if event["all_day"] else datetime.fromisoformat(event["start"]).astimezone(ZoneInfo(zone)).date().isoformat()
        now = self.clock()
        preview = {"id": uuid.uuid4().hex, "event_id": event_id, "date": day, "calendar_date": calendar_date,
                   "account_id": binding["id"], "binding": binding, "event_digest": digest(event),
                   "title": event["title"][:120] or "Reunión", "route": route, "sources": sources,
                   "expires_at": now + 300, "hermes_home": digest(str(self.hermes_home))}
        self._previews = {k: v for k, v in self._previews.items() if v["expires_at"] > now}
        if len(self._previews) >= 20:
            raise MeetingError("Demasiadas previsualizaciones. Espera unos minutos.")
        self._previews[preview["id"]] = preview
        return {"preview": {k: preview[k] for k in ("id", "title", "route", "sources", "expires_at")},
                "daily_limit": 10}

    async def _live(self, job):
        owner, event, binding = await self._source(job["event_id"], job["date"])
        if binding != job["binding"] or digest(event) != job["event_digest"]:
            raise MeetingError("El evento o la cuenta cambió.")
        return owner

    async def start(self, identity, *, confirmed):
        async with self._lock:
            if self._closed or confirmed is not True or not self.functional_enabled():
                raise MeetingError("Autoriza esta preparación antes de enviarla.")
            store = await self._db(self.store)
            old = await self._db(store.get, identity) if store else None
            if old:
                return {"job": self.public(old)}  # idempotent even after restart/expired preview
            preview = self._previews.get(identity)
            if not preview or preview["expires_at"] <= self.clock():
                raise MeetingError("La previsualización venció; vuelve a revisarla.")
            owner = await self._live(preview)
            store = await self._db(self.store, True)
            job, fresh = await self._db(store.reserve, preview, self.clock())
            if fresh:
                try:
                    task = await owner.start_calendar_preparation(job["account_id"], job["binding"]["generation"],
                        job["binding"]["revision"], lambda: self._run(job, owner))
                except BaseException:
                    await self._db(store.finish, identity, None, "cancelled", self.clock())
                    raise
                self._tasks[identity] = task
                task.add_done_callback(lambda done: self._tasks.pop(identity, None))
            self._previews.pop(identity, None)
            self._notify()
            return {"job": self.public(job)}

    async def _run(self, job, owner):
        result, error, reported = None, "request_unknown", ""
        deadline = asyncio.get_running_loop().time() + self.timeout
        try:
            async with asyncio.timeout(self.timeout):
                if await self._live(job) is not owner:
                    raise MeetingError("Cuenta cambiada.")
                response = await self.invoke(self.hermes_home, {"command": "prepare", **job["route"],
                    "expected": job["route"], "sources": job["sources"]})
                if asyncio.get_running_loop().time() >= deadline:
                    raise TimeoutError
                if response.get("ok") and response.get("route") == job["route"]:
                    result = validate_result(response["result"], job["sources"])
                    reported = response.get("reported_model", "")
                    if not isinstance(reported, str) or len(reported) > 200:
                        reported = ""
                    error = ""
                else:
                    error = "model_failed"
                if await self._live(job) is not owner:
                    raise MeetingError("Cuenta cambiada.")
        except asyncio.CancelledError:
            result, error = None, "cancelled"
        except MeetingError:
            result, error = None, "source_changed"
        except Exception:
            result, error = None, "request_unknown"
        await self._db(self._store.finish, job["id"], result, error, self.clock(), reported)
        self._notify()

    @staticmethod
    def public(job):
        return {k: v for k, v in job.items() if k not in {"binding", "event_digest", "hermes_home"}}

    async def snapshot(self):
        store = await self._db(self.store)
        rows = await self._db(store.rows) if store else []
        # An interrupted send remains unknown on restart; there is never an automatic retry.
        return {"schema": "iris.meeting-preparation/v1", "jobs": [dict(self.public(j),
            running=j["id"] in self._tasks and not self._tasks[j["id"]].done()) for j in rows], "daily_limit": 10}

    async def cancel(self, identity):
        async with self._lock:
            store = await self._db(self.store)
            if store is None or await self._db(store.get, identity) is None:
                self._previews.pop(identity, None)
                return {"cancelled": True}
            row = await self._db(store.finish, identity, None, "cancelled", self.clock())
            task = self._tasks.get(identity)
            if task:
                task.cancel()
                from core.google_connections import GoogleConnectionsService
                await GoogleConnectionsService._settle(asyncio.gather(task, return_exceptions=True))
            self._notify()
            return {"job": self.public(row)}

    def inbox_rows(self):
        store = self.store()
        return [row for row in store.rows() if row["state"] in {"completed", "failed"}] if store else []

    def act(self, identity, action, *, expected_revision, minutes=None):
        store = self.store()
        if store is None or minutes is not None:
            raise MeetingError("Acción no disponible.")
        result = store.act(identity, action, expected_revision, self.clock())
        self._notify()
        return result

    async def close(self):
        async with self._lock:
            self._closed = True
            self._previews.clear()
            tasks = list(self._tasks.values()) + list(self._preview_tasks)
            for task in tasks:
                task.cancel()
            from core.google_connections import GoogleConnectionsService
            if tasks:
                await GoogleConnectionsService._settle(asyncio.gather(*tasks, return_exceptions=True))
