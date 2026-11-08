"""F07: the general inbox and the visual notice on return.

**Inbox** (``/api/inbox``). One view over what Iris has to tell the user, built
at read time from each source of truth, never from a copy:

* automation results come from the profile's ``AutomationStore`` (the
  result, delivery and final state that ``finish()`` wrote together), so a
  restart between finishing and showing loses nothing;
* Vision notices stay in ``iris.notification/v1`` with their own store and
  actions (``/api/notifications``); here they are only wrapped.

A source that cannot be read is reported as unavailable, never as "no
results". Actions on an automation result (read, unread, snooze, dismiss,
restore) name the revision they saw; repeating one is a no-op; a failed write
answers an error and changes nothing.

**Return notice** (``InboxPresenter``). After a new interaction on a local,
visible surface that says it can show it (``presents_inbox``; Vision's
``presents_vision`` is not enough), the pending results — and work still
running — are grouped in one notice. One surface claims it with a durable
lease, a token and a generation; only its receipt marks the results presented
(never read). Silence, a rule's quiet hours and an active conversation defer
it; nothing is spoken, sent to Hermes or asked of the camera.
"""

from __future__ import annotations

import asyncio
import logging
import re
import sqlite3
import time
from typing import TYPE_CHECKING, Any, Callable

from fastapi import APIRouter, Body, HTTPException

from core.automation_contracts import AutomationRule
from core.automation_store import (
    AutomationStore,
    AutomationStoreError,
    InvalidTransition,
    PresentationRefused,
    RevisionConflict,
    UnknownDelivery,
)
from core.calendar_store import CalendarRevisionConflict, CalendarStoreError, UnknownCalendarNotice
from core.inbox_contracts import (INBOX_SCHEMA, from_app_notice, from_automation, from_background, from_calendar,
                                   from_mail, from_meeting, from_vision, inbox_key, is_unread)

if TYPE_CHECKING:
    from core.calendar_runtime import CalendarRuntime

logger = logging.getLogger(__name__)

# Iris keeps one profile per home; the key reserves its place for when there are more.
INBOX_PROFILE = "default"
EXCERPT_CHARS = 280


def _quiet_hours(rule: AutomationRule, now: float) -> bool:
    hours = rule.delivery.quiet_hours
    if hours is None:
        return False
    local = time.localtime(now)
    minute = local.tm_hour * 60 + local.tm_min
    if hours.start_minute == hours.end_minute:
        return True
    if hours.start_minute < hours.end_minute:
        return hours.start_minute <= minute < hours.end_minute
    return minute >= hours.start_minute or minute < hours.end_minute


def _excerpt(text: str) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= EXCERPT_CHARS else text[:EXCERPT_CHARS - 1].rstrip() + "…"


class InboxService:
    """Reads and changes the general inbox; every method is synchronous (run it in a thread)."""

    def __init__(self, *, store: Callable[[], AutomationStore | None], store_problem: Callable[[], str],
                 vision: Callable[[], dict[str, Any]] | None = None, clock: Callable[[], float] = time.time,
                 calendar: Callable[[], CalendarRuntime | None] | None = None, mail=None, meeting=None,
                 background=None, apps=None):
        self._store, self._store_problem, self._vision, self._clock = store, store_problem, vision, clock
        self._calendar = calendar
        self._mail = mail
        self._meeting = meeting
        self._background = background  # finished background tasks kept for their conversation
        self._apps = apps  # notices that installed apps scheduled, once due

    def snapshot(self) -> dict[str, Any]:
        now = self._clock()
        items: list[Any] = []
        sources: dict[str, dict[str, Any]] = {}
        preferences: dict[str, Any] = {"available": False, "revision": 0, "return_notice": True, "quiet_until": 0.0}
        store = self._store()
        if store is None:
            problem = self._store_problem()
            sources["automation"] = {"status": "unavailable" if problem else "none", "message": problem[:600]}
        else:
            try:
                for occurrence, title, result, delivery, uncertain in store.inbox_rows():
                    item = from_automation(profile=INBOX_PROFILE, rule_title=title, occurrence=occurrence,
                                           result=result, delivery=delivery, now=now, uncertain=uncertain)
                    if item is not None:
                        items.append(item)
                prefs = store.inbox_preferences()
                preferences = {"available": True, "revision": prefs.revision, "return_notice": prefs.return_notice,
                               "quiet_until": prefs.quiet_until}
                sources["automation"] = {"status": "ok", "message": ""}
            except (AutomationStoreError, sqlite3.Error, OSError, ValueError) as exc:
                logger.warning("Inbox: automation results unavailable: %s", exc)
                sources["automation"] = {"status": "unavailable",
                                         "message": "No se pudieron leer los resultados de automatizaciones."}
        if self._vision is None:
            sources["vision"] = {"status": "none", "message": ""}
        else:
            try:
                notices = self._vision()
                revision = int(notices.get("revision") or 0)
                for notice in notices.get("items") or []:
                    item = from_vision(profile=INBOX_PROFILE, notice=notice, revision=revision)
                    if item is not None:
                        items.append(item)
                sources["vision"] = {"status": "ok", "message": "", "revision": revision}
            except Exception as exc:  # Vision's own view reports its problem; here it is only "unavailable"
                logger.warning("Inbox: Vision notices unavailable: %s", exc)
                sources["vision"] = {"status": "unavailable", "message": "No se pudieron leer los avisos de Vision."}
        if self._calendar is not None:
            try:
                runtime = self._calendar_runtime()
                zone = runtime.preferences()["time_zone"]
                projected = [from_calendar(profile=INBOX_PROFILE, notice=notice, time_zone=zone, now=now)
                             for notice in runtime.inbox_rows()]
                items.extend(item for item in projected if item is not None)
                sources["calendar"] = {"status": "ok", "message": ""}
            except Exception as exc:
                logger.warning("Inbox: Calendar notices unavailable (%s)", type(exc).__name__)
                sources["calendar"] = {"status": "unavailable",
                                       "message": "No se pudieron leer los avisos del calendario."}
        if self._mail is not None:
            try:
                runtime = self._mail()
                if runtime is None:
                    raise ValueError("unavailable")
                items.extend(from_mail(profile=INBOX_PROFILE, job=job) for job in runtime.inbox_rows())
                sources["gmail"] = {"status": "ok", "message": ""}
            except Exception:
                sources["gmail"] = {"status": "unavailable", "message": "No se pudieron leer los avisos de Correo."}
        if self._meeting is not None:
            try:
                runtime = self._meeting()
                if runtime is None:
                    raise ValueError("unavailable")
                items.extend(from_meeting(profile=INBOX_PROFILE, job=job) for job in runtime.inbox_rows())
                sources["meeting"] = {"status": "ok", "message": ""}
            except Exception:
                sources["meeting"] = {"status": "unavailable", "message": "No se pudieron leer las preparaciones de reuniones."}
        if self._background is not None:
            try:
                store = self._background()
                if store is None:
                    raise ValueError("unavailable")
                items.extend(from_background(profile=INBOX_PROFILE, task=task) for task in store.inbox_rows())
                sources["background"] = {"status": "ok", "message": ""}
            except Exception:
                sources["background"] = {"status": "unavailable",
                                         "message": "No se pudieron leer las tareas en segundo plano."}
        if self._apps is not None:
            try:
                store = self._apps()
                if store is None:
                    raise ValueError("unavailable")
                items.extend(from_app_notice(profile=INBOX_PROFILE, notice=row) for row in store.inbox_rows(now=now))
                sources["app"] = {"status": "ok", "message": ""}
            except Exception:
                sources["app"] = {"status": "unavailable", "message": "No se pudieron leer los avisos de las apps."}
        items.sort(key=lambda item: (item.updated_at, item.key), reverse=True)
        return {
            "schema": "iris.inbox/v1",
            "item_schema": INBOX_SCHEMA,
            "server_now": now,
            "items": [item.model_dump(mode="json", by_alias=True) for item in items],
            "unread": sum(1 for item in items if is_unread(item)),
            "sources": sources,
            "preferences": preferences,
        }

    def unread(self) -> int:
        return int(self.snapshot()["unread"])

    def _automation_store(self) -> AutomationStore:
        store = self._store()
        if store is None:
            problem = self._store_problem()
            raise HTTPException(status_code=409 if problem else 404, detail={
                "code": "store_unavailable" if problem else "no_automations",
                "message": problem or "Todavía no hay resultados de automatizaciones."})
        return store

    def _calendar_runtime(self) -> CalendarRuntime:
        runtime = self._calendar() if self._calendar is not None else None
        if runtime is None:
            raise CalendarStoreError("El calendario no está disponible.")
        return runtime

    def _act_calendar(self, source_id: str, action: str, *, expected_revision: int,
                      minutes: int | None = None) -> bool:
        try:
            _, changed = self._calendar_runtime().act(source_id, action,
                expected_revision=expected_revision, minutes=minutes)
            return changed
        except UnknownCalendarNotice as exc:
            raise HTTPException(status_code=404, detail={"code": "unknown_item", "message": str(exc)}) from exc
        except CalendarRevisionConflict as exc:
            raise HTTPException(status_code=409, detail={"code": "revision_conflict", "message": str(exc)}) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail={"code": "invalid_action", "message": str(exc)[:300]}) from exc
        except (CalendarStoreError, sqlite3.Error, OSError) as exc:
            raise HTTPException(status_code=503, detail={
                "code": "store_unavailable", "message": "No se guardó el cambio; inténtalo de nuevo."}) from exc

    def act(self, key: str, action: str, *, expected_revision: int, minutes: int | None = None) -> dict[str, Any]:
        origin, _, rest = str(key).partition(":")
        profile, _, source_id = rest.partition(":")
        if origin == "vision":
            raise HTTPException(status_code=409, detail={
                "code": "vision_actions", "message": "Este aviso se gestiona en la vista Vision."})
        if (origin not in ("automation", "calendar", "gmail", "meeting", "background", "app") or profile != INBOX_PROFILE
                or not re.fullmatch(r"[0-9a-f]{32}", source_id)):
            raise HTTPException(status_code=404, detail={"code": "unknown_item", "message": "Ese elemento no existe."})
        if origin == "background":
            return self._act_background(key, source_id, action, expected_revision)
        if origin == "app":
            return self._act_app(key, source_id, action, expected_revision)
        if origin in ("gmail", "meeting"):
            source = self._mail if origin == "gmail" else self._meeting
            if source is None:
                raise HTTPException(status_code=404, detail={"code": "unknown_item", "message": "La fuente de Correo no está registrada."})
            try:
                runtime = source()
                if runtime is None:
                    raise ValueError("unavailable")
                _, changed = runtime.act(source_id, action, expected_revision=expected_revision, minutes=minutes)
            except Exception:
                raise HTTPException(status_code=409, detail={"code": "revision_conflict", "message": "El aviso cambió o no está disponible."}) from None
            snapshot = self.snapshot()
            return {"changed": changed, "item": next((row for row in snapshot["items"] if row["key"] == key), None), "unread": snapshot["unread"]}
        if origin == "calendar":
            changed = self._act_calendar(source_id, action, expected_revision=expected_revision, minutes=minutes)
            snapshot = self.snapshot()
            item = next((row for row in snapshot["items"] if row["key"] == key), None)
            return {"changed": changed, "item": item, "unread": snapshot["unread"]}
        store = self._automation_store()
        try:
            result_id = store.result_for(source_id)
            if not result_id:
                raise HTTPException(status_code=409, detail={
                    "code": "no_result", "message": "Esa tarea todavía no tiene resultado."})
            _, changed = store.act_on_delivery(result_id, action, expected_revision=expected_revision,
                                               minutes=minutes)
        except UnknownDelivery as exc:
            raise HTTPException(status_code=404, detail={"code": "unknown_item", "message": str(exc)}) from exc
        except RevisionConflict as exc:
            raise HTTPException(status_code=409, detail={"code": "revision_conflict", "message": str(exc)}) from exc
        except (InvalidTransition, ValueError) as exc:
            raise HTTPException(status_code=409, detail={"code": "invalid_action", "message": str(exc)[:300]}) from exc
        except (AutomationStoreError, sqlite3.Error, OSError) as exc:
            raise HTTPException(status_code=503, detail={
                "code": "store_unavailable", "message": "No se guardó el cambio; inténtalo de nuevo."}) from exc
        snapshot = self.snapshot()
        item = next((row for row in snapshot["items"] if row["key"] == key), None)
        return {"changed": changed, "item": item, "unread": snapshot["unread"]}

    def _act_background(self, key: str, source_id: str, action: str, expected_revision: int) -> dict[str, Any]:
        store = self._background() if self._background is not None else None
        if store is None:
            raise HTTPException(status_code=404, detail={"code": "unknown_item",
                                                         "message": "Las tareas en segundo plano no están disponibles."})
        try:
            _, changed = store.act(source_id, action, expected_revision=expected_revision)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail={"code": "unknown_item", "message": "Ese elemento no existe."}) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail={"code": "revision_conflict",
                                                         "message": "El aviso cambió; vuelve a cargar la Bandeja."}) from exc
        except (sqlite3.Error, OSError) as exc:
            raise HTTPException(status_code=503, detail={
                "code": "store_unavailable", "message": "No se guardó el cambio; inténtalo de nuevo."}) from exc
        snapshot = self.snapshot()
        item = next((row for row in snapshot["items"] if row["key"] == key), None)
        return {"changed": changed, "item": item, "unread": snapshot["unread"]}

    def _act_app(self, key: str, source_id: str, action: str, expected_revision: int) -> dict[str, Any]:
        store = self._apps() if self._apps is not None else None
        if store is None:
            raise HTTPException(status_code=404, detail={"code": "unknown_item",
                                                         "message": "Los avisos de las apps no están disponibles."})
        try:
            _, changed = store.act(source_id, action, expected_revision=expected_revision, now=self._clock())
        except KeyError as exc:
            raise HTTPException(status_code=404, detail={"code": "unknown_item", "message": "Ese elemento no existe."}) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail={"code": "revision_conflict",
                                                         "message": "El aviso cambió; vuelve a cargar la Bandeja."}) from exc
        except (sqlite3.Error, OSError) as exc:
            raise HTTPException(status_code=503, detail={
                "code": "store_unavailable", "message": "No se guardó el cambio; inténtalo de nuevo."}) from exc
        snapshot = self.snapshot()
        item = next((row for row in snapshot["items"] if row["key"] == key), None)
        return {"changed": changed, "item": item, "unread": snapshot["unread"]}

    def set_preferences(self, *, expected_revision: int, return_notice: bool | None,
                        quiet_minutes: int | None) -> dict[str, Any]:
        store = self._automation_store()
        try:
            store.set_inbox_preferences(expected_revision=expected_revision, return_notice=return_notice,
                                        quiet_minutes=quiet_minutes)
        except RevisionConflict as exc:
            raise HTTPException(status_code=409, detail={"code": "revision_conflict", "message": str(exc)}) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"code": "invalid_preferences", "message": str(exc)}) from exc
        except (AutomationStoreError, sqlite3.Error, OSError) as exc:
            raise HTTPException(status_code=503, detail={
                "code": "store_unavailable", "message": "No se guardó el cambio; inténtalo de nuevo."}) from exc
        return self.snapshot()


class InboxPresenter:
    """Claims and builds the return notice for one surface; synchronous (run it in a thread)."""

    def __init__(self, *, store: Callable[[], AutomationStore | None], clock: Callable[[], float] = time.time,
                 lease_s: float = 30.0):
        self._store, self._clock, self.lease_s = store, clock, lease_s

    def offer(self, surface_id: str) -> dict[str, Any] | None:
        """The notice to send to ``surface_id`` now, or None (nothing pending, another notice in
        flight, or the user silenced it). The caller already checked the surface and the conversation."""
        store = self._store()
        if store is None:
            return None
        now = self._clock()
        presentation = store.claim_presentation(surface_id, lease_s=self.lease_s,
                                                quiet=lambda rule: _quiet_hours(rule, now))
        if presentation is None:
            return None
        results, running = [], []
        titles: dict[str, str] = {}

        def title(rule_id: str) -> str:
            if rule_id not in titles:
                rule = store.get_rule(rule_id)
                titles[rule_id] = rule.title if rule else "Automatización"
            return titles[rule_id]

        for result_id in presentation.results:
            result = store.result(result_id)
            if result is not None:
                results.append({"key": inbox_key("automation", INBOX_PROFILE, result.occurrence_id),
                                "title": title(result.rule_id), "outcome": result.status,
                                "excerpt": _excerpt(result.text)})
        for occurrence_id in presentation.running:
            occurrence = store.get_occurrence(occurrence_id)
            if occurrence is not None:
                running.append({"key": inbox_key("automation", INBOX_PROFILE, occurrence.id),
                                "title": title(occurrence.rule_id),
                                "state": "running" if occurrence.state in ("submitted", "running") else "unknown"})
        return {
            "schema": "iris.inbox-presentation/v1", "id": presentation.id, "token": presentation.token,
            "generation": presentation.generation, "surface_id": surface_id,
            "results": results, "running": running, "lease_expires_at": presentation.lease_expires_at,
        }

    def sent(self, payload: dict[str, Any]) -> None:
        store = self._store()
        if store is not None:
            store.presentation_sent(payload["id"], payload["token"])

    def failed(self, payload: dict[str, Any], *, certain: bool) -> None:
        store = self._store()
        if store is not None:
            store.presentation_failed(payload["id"], payload["token"], certain=certain)

    def receipt(self, payload: dict[str, Any], *, surface_id: str) -> bool:
        """The surface's receipt; False when it does not match its notice (nothing changed)."""
        store = self._store()
        if store is None:
            return False
        try:
            presentation_id, token = str(payload.get("id") or ""), str(payload.get("token") or "")
            generation = payload.get("generation")
            if len(presentation_id) != 32 or len(token) != 32 or not isinstance(generation, int):
                return False
            store.presentation_receipt(presentation_id, token=token, generation=generation, surface_id=surface_id,
                                       shown=payload.get("shown") is True)
            return True
        except PresentationRefused:
            return False


def inbox_router(service: Callable[[], InboxService | None], *,
                 changed: Callable[[], Any] = lambda: None) -> APIRouter:
    """``changed()`` is awaited after a successful change, to tell the surfaces."""
    router = APIRouter(prefix="/api/inbox")

    def require() -> InboxService:
        current = service()
        if current is None:
            raise HTTPException(status_code=503, detail="Iris todavía no está lista.")
        return current

    @router.get("")
    async def snapshot() -> dict[str, Any]:
        return await asyncio.to_thread(require().snapshot)

    @router.post("/action")
    async def action(payload: dict = Body(...)) -> dict[str, Any]:
        key, name = payload.get("key"), payload.get("action")
        expected, minutes = payload.get("expected_revision"), payload.get("minutes")
        if not isinstance(key, str) or not isinstance(name, str) or not isinstance(expected, int) or isinstance(
                expected, bool) or (minutes is not None and (not isinstance(minutes, int) or isinstance(minutes, bool))):
            raise HTTPException(status_code=400, detail={
                "code": "invalid_request", "message": "Hace falta key, action y expected_revision."})
        answer = await asyncio.to_thread(require().act, key, name, expected_revision=expected, minutes=minutes)
        if answer["changed"]:
            await changed()
        return answer

    @router.post("/preferences")
    async def preferences(payload: dict = Body(...)) -> dict[str, Any]:
        expected = payload.get("expected_revision")
        notice, quiet = payload.get("return_notice"), payload.get("quiet_minutes")
        if (not isinstance(expected, int) or isinstance(expected, bool)
                or (notice is not None and not isinstance(notice, bool))
                or (quiet is not None and (not isinstance(quiet, int) or isinstance(quiet, bool)))
                or (notice is None and quiet is None)):
            raise HTTPException(status_code=400, detail={
                "code": "invalid_request", "message": "Hace falta expected_revision y un cambio."})
        answer = await asyncio.to_thread(require().set_preferences, expected_revision=expected,
                                         return_notice=notice, quiet_minutes=quiet)
        await changed()
        return answer

    return router
