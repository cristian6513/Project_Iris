"""F06: Hermes transport, Core runtime and local API of Iris automations.

The runtime opens the profile's store only once a rule exists (nothing is
created for users who never make one), observes Iris activity, evaluates the
enabled rules every few seconds (F05) and hands ready occurrences to the
executor. At start it cancels unsent occurrences of the previous run and
reconciles sent ones. Rules are created paused; enabling one is an explicit,
confirmed request. Sources whose adapter is not ready yet (desktop input,
F08a; camera absence until F03 is validated) cannot be enabled.

The Automatizaciones app (F07) reads and edits through this API; results reach
the user through the general inbox and its return notice (``core.inbox_api``),
which this runtime tells about every committed change (``on_inbox_change``).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, Body, HTTPException
from pydantic import ValidationError

from core.automation_contracts import AutomationRule, DeliveryPolicy, Execution, Trigger, new_id
from core.automation_engine import AutomationEngine, Decision, run_loop
from core.automation_executor import AutomationExecutor, RunDestination, unsupported
from core.automation_store import (
    AutomationStore,
    AutomationStoreError,
    RevisionConflict,
    StorePathError,
    profile_store_path,
)
from core.presence_state import PresenceTracker
from core.windows_idle import DesktopIdleObserver
from core.automation_contracts import AutomationPreferences

logger = logging.getLogger(__name__)

# How often sent work nobody is settling is taken up again (T04): after an
# error or a crash of its settler it always gets an owner, never a new send.
RECONCILE_EVERY_S = 60.0

# Sources a rule may be enabled for today.
READY_SOURCES = {"iris_idle"}
SOURCE_PENDING = {
    "desktop_idle": "Activa la observación de Windows en un equipo compatible para usar esta condición.",
    "observed_absence": "La ausencia observada por la cámara se habilita cuando se valide F03.",
}


class HermesRunsTransport:
    """``POST/GET /v1/runs`` of the profile's Hermes, through Core's request helper.

    T02: a request is bound to the destination it was checked against.
    ``resolve`` reads the endpoint and credential once into a ``RunDestination``,
    whose fingerprint is what the executor stores and compares, and every call
    sends to exactly that destination: the configuration is not read again in
    between. ``identity`` gives the Hermes base URL and the default profile's key.
    """

    def __init__(self, request: Callable[..., dict[str, Any]], route: Callable[[str], tuple[str, str | None, str]],
                 identity: Callable[[], tuple[str, str | None]] = lambda: ("", None)):
        self._request, self._route, self._identity = request, route, identity

    def resolve(self, profile: str) -> RunDestination | None:
        """Where and as whom a run of ``profile`` is admitted now (None: unknown or unsafe)."""
        prefix, token, problem = self._route(profile)
        if problem:
            return None
        try:
            base, default_token = self._identity()
        except Exception:  # an unsafe or unreadable configuration
            return None
        secret = token if token is not None else (default_token or "")
        # Hermes keeps run keys per profile and credential (its idempotency scope
        # is a hash of both); the fingerprint adds the endpoint. Only hashes are kept.
        digest = hashlib.sha256(secret.encode("utf-8")).hexdigest()
        scope = hashlib.sha256("\0".join(("iris-run-scope/v1", base or "", prefix, profile, digest))
                               .encode("utf-8")).hexdigest()
        return RunDestination(profile=profile, base_url=base or "", prefix=prefix, token=secret, scope=scope)

    def _call(self, destination: RunDestination, method: str, path: str, **kwargs) -> dict[str, Any]:
        return self._request(method, destination.prefix + path, base_url=destination.base_url or None,
                             token=destination.token, **kwargs)

    def submit(self, destination: RunDestination, body: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
        result = self._call(destination, "POST", "/v1/runs", payload=body, timeout=20,
                            headers={"Idempotency-Key": idempotency_key})
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        if result.get("ok") and data.get("run_id"):
            return {"outcome": "accepted", "run_id": str(data["run_id"]), "replayed": data.get("replayed") is True}
        status = result.get("status")
        if status is None or int(status) >= 500 or int(status) == 408:
            # No answer, or a server error after which Hermes may hold the run.
            return {"outcome": "lost", "error": str(result.get("error") or "sin respuesta")[:300]}
        return {"outcome": "rejected", "error": str(result.get("error") or f"HTTP {status}")[:300]}

    def status(self, destination: RunDestination, run_id: str) -> dict[str, Any]:
        result = self._call(destination, "GET", "/v1/runs/" + urllib.parse.quote(run_id, safe=""), timeout=10)
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        if not result.get("ok") or not data.get("status"):
            return {"status": "lost"}
        return {"status": str(data["status"]), "output": data.get("output"), "error": str(data.get("error") or "")}

    def stop(self, destination: RunDestination, run_id: str) -> bool:
        result = self._call(destination, "POST", "/v1/runs/" + urllib.parse.quote(run_id, safe="") + "/stop",
                            timeout=8)
        return bool(result.get("ok"))

    def idempotency(self, destination: RunDestination) -> dict[str, Any]:
        """``features.runs_idempotency`` of ``/v1/capabilities`` (``{}``: unknown, so nothing is sent)."""
        result = self._call(destination, "GET", "/v1/capabilities", timeout=8)
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        features = data.get("features") if isinstance(data.get("features"), dict) else {}
        info = features.get("runs_idempotency")
        if not result.get("ok") or not isinstance(info, dict):
            return {}
        try:
            retention = float(info.get("retention_seconds") or 0)
        except (TypeError, ValueError):
            retention = 0.0
        return {"supported": info.get("supported") is True, "durable": info.get("durable") is True,
                "retention_s": retention}


def hermes_profile_route(profile: str) -> tuple[str, str | None, str]:
    """(path prefix, token, problem): the default profile, or a routable named one."""
    if profile in ("", "default"):
        return "", None, ""
    from core.hermes_bridge import _named_profile_route

    routed, key, warning = _named_profile_route(profile)
    if warning or not routed:
        return "", None, warning or f"El perfil «{profile}» no está disponible para automatizaciones."
    return f"/p/{routed}", key, ""


class AutomationRuntime:
    """Store, signals, engine and executor of this Core; started by ``core.agent``."""

    def __init__(self, *, home: Path | None, transport: Any, permission: Callable[[], str],
                 profile_problem: Callable[[str], str], local_surface_open: Callable[[], bool],
                 interval: float = 5.0, owner: str | None = None,
                 after_tick: Callable | None = None, desktop_reader=None) -> None:
        from core.profile_paths import validate_profile_home

        if home is None:
            from core.config import get_iris_home

            home = Path(get_iris_home())
        self.home = validate_profile_home(home)
        self.path = profile_store_path(self.home)
        self.problem = ""  # why the store cannot be opened (Q02), for the API
        self.tracker = PresenceTracker()
        self.desktop_observer = DesktopIdleObserver(self.tracker, reader=desktop_reader)
        self._presence_lock = threading.RLock()
        self._desktop_returned = False
        self._closed = False
        self.voice_service = None
        self.owner = owner or f"core-{new_id()[:8]}"
        self._transport, self._permission, self._profile_problem = transport, permission, profile_problem
        self._local_surface_open = local_surface_open
        self.interval = interval
        self.after_tick = after_tick
        self._store: AutomationStore | None = None
        self._engine: AutomationEngine | None = None
        self._executor: AutomationExecutor | None = None
        self._tasks: set[asyncio.Task] = set()
        self._stop = asyncio.Event()
        self._last_reconcile = float("-inf")
        self._monotonic = time.monotonic
        # F07: called (on any thread) after a committed change the inbox shows.
        self.on_inbox_change: Callable[[str], None] | None = None

    def store(self, *, create: bool = False) -> AutomationStore | None:
        if self._store is None and (create or self.path.exists()):
            try:
                # Q02: the deferred open checks the profile boundary again, now.
                self._store = AutomationStore.for_profile(self.home)
            except StorePathError as exc:
                if str(exc) != self.problem:
                    logger.warning("Automations store refused: %s", exc)
                self.problem = str(exc)
                return None
            self.problem = ""
            self._store.subscribe(self._inbox_changed)
            self._engine = AutomationEngine(self._store, self.tracker, owner=self.owner)
            self._executor = AutomationExecutor(self._store, self._engine, self._transport,
                                                permission=self._permission, profile_problem=self._profile_problem)
            self._engine.recover()
            self._reconcile_now()
        return self._store

    @property
    def executor(self) -> AutomationExecutor | None:
        return self._executor

    def _inbox_changed(self, kind: str) -> None:
        callback = self.on_inbox_change
        if callback is not None:
            callback(kind)

    def _spawn(self, coroutine) -> None:
        try:
            task = asyncio.get_running_loop().create_task(coroutine)
        except RuntimeError:
            coroutine.close()
            return
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def interaction(self) -> None:
        self.tracker.iris_interaction()

    def settle_later(self) -> None:
        self._reconcile_now()

    def _reconcile_now(self) -> None:
        if self._executor is not None:
            self._last_reconcile = self._monotonic()
            self._spawn(self._executor.reconcile())

    def presence_preferences(self) -> AutomationPreferences:
        return self._store.automation_preferences() if self._store is not None else AutomationPreferences()

    def presence_snapshot(self) -> dict:
        prefs = self.presence_preferences()
        return prefs.model_dump(by_alias=True) | {"supported": self.desktop_observer.supported,
                                                "voice_supported": self.desktop_observer.supported}

    def ready_sources(self) -> set[str]:
        sources = set(READY_SOURCES)
        if self.desktop_observer.supported and self.presence_preferences().desktop_idle:
            sources.add("desktop_idle")
        return sources

    def configure_presence(self, *, expected_revision: int, **changes) -> AutomationPreferences:
        with self._presence_lock:
            if self._closed or self._store is None:
                raise ValueError("Iris ya no está disponible.")
            prefs = self._store.set_automation_preferences(expected_revision=expected_revision, **changes)
            self.desktop_observer.reset("consent_changed")
            self._desktop_returned = False
            return prefs

    def take_desktop_return(self) -> bool:
        with self._presence_lock:
            returned, self._desktop_returned = self._desktop_returned, False
            return returned

    def _tick(self) -> None:
        if self._closed:
            return
        self.tracker.iris_tick(local_surface_open=self._local_surface_open())
        with self._presence_lock:
            try:
                prefs = self.presence_preferences()
                returned = self.desktop_observer.tick(enabled=prefs.desktop_idle, revision=prefs.revision)
                self._desktop_returned = self._desktop_returned or returned
            except Exception:
                # Corrupt/unreadable consent never enables observation.
                self.desktop_observer.reset("preferences_unavailable")
                self._desktop_returned = False
        if self._executor is not None and self._monotonic() - self._last_reconcile >= RECONCILE_EVERY_S:
            self._reconcile_now()

    async def _on_ready(self, decision: Decision) -> None:
        if self._executor is not None:
            self._spawn(self._executor.dispatch(decision))

    async def run(self) -> None:
        """Observe every few seconds; evaluate only when a store exists (a rule was made)."""
        while not self._stop.is_set():
            self._tick()
            if self.after_tick is not None:
                await self.after_tick()
            if self._stop.is_set():
                return
            if self.store() is not None:
                await run_loop(self._engine, tick=self._tick, on_ready=self._on_ready,
                               interval=self.interval, stop=self._stop, after_tick=self.after_tick)
                return
            try:
                await asyncio.wait_for(self._stop.wait(), self.interval)
            except TimeoutError:
                pass

    async def close(self) -> None:
        self._stop.set()
        if self.voice_service is not None:
            await self.voice_service.stop()
        with self._presence_lock:
            self._closed = True
            self.desktop_observer.reset("closed")
            self._desktop_returned = False
        for task in list(self._tasks):
            task.cancel()


def _rule_problem(rule: AutomationRule, *, ready_sources=None) -> str:
    """Why this rule cannot be enabled today (``""``: it can)."""
    if rule.trigger.source not in (READY_SOURCES if ready_sources is None else ready_sources):
        return SOURCE_PENDING.get(rule.trigger.source, "Esa fuente no está disponible.")
    return unsupported(rule)


def automation_router(runtime: Callable[[], AutomationRuntime | None], *, permission: Callable[[], str]) -> APIRouter:
    router = APIRouter(prefix="/api/automations")

    def require() -> AutomationRuntime:
        current = runtime()
        if current is None:
            raise HTTPException(status_code=503, detail="Iris todavía no está lista.")
        return current

    def unavailable(message: str) -> HTTPException:
        return HTTPException(status_code=409, detail={"code": "store_unavailable", "message": message[:600]})

    def public_occurrence(store: AutomationStore, occurrence) -> dict[str, Any]:
        body = occurrence.model_dump(mode="json", by_alias=True)
        body.pop("claim", None)
        result = store.result(occurrence.result_id) if occurrence.result_id else None
        if result is not None:
            body["result"] = {"status": result.status, "text": result.text, "finished_at": result.finished_at}
        return body

    @router.get("")
    async def overview() -> dict[str, Any]:
        current = require()
        store = current.store()  # opened on the event loop: reconciliation starts here
        ready = current.ready_sources()
        presence = current.presence_snapshot()
        sources = {name: current.tracker.state(name).__dict__ for name in ("iris_idle", "desktop_idle", "observed_absence")}
        if store is None:
            return {"rules": [], "occurrences": [], "sources": sources, "permission": permission(),
                    "ready_sources": sorted(ready), "presence": presence, "store_problem": current.problem}
        try:
            rules = await asyncio.to_thread(store.list_rules)
            occurrences = await asyncio.to_thread(store.occurrences)
            recent = sorted(occurrences, key=lambda o: o.updated_at, reverse=True)[:50]
            shown = [public_occurrence(store, o) for o in recent]
        except AutomationStoreError as exc:
            raise unavailable(str(exc)) from exc
        return {
            "rules": [rule.model_dump(mode="json", by_alias=True) | {"problem": _rule_problem(rule, ready_sources=ready)} for rule in rules],
            "occurrences": shown,
            "sources": sources,
            "permission": permission(),
            "ready_sources": sorted(ready), "presence": presence,
        }

    @router.put("/preferences")
    async def presence_preferences(payload: dict = Body(...)) -> dict:
        current = require()
        allowed = {"expected_revision", "desktop_idle", "return_voice", "focus", "confirm"}
        if set(payload) - allowed or type(payload.get("expected_revision")) is not int:
            raise HTTPException(status_code=400, detail="Preferencias o revisión inválidas.")
        changes = {key:payload[key] for key in ("desktop_idle","return_voice","focus") if key in payload}
        if not changes or any(type(value) is not bool for value in changes.values()):
            raise HTTPException(status_code=400, detail="Elige activar o desactivar cada preferencia.")
        if any(changes.get(key) is True for key in ("desktop_idle","return_voice")) and (
                payload.get("confirm") is not True or not current.desktop_observer.supported):
            raise HTTPException(status_code=409, detail="La función requiere tu confirmación y Windows compatible.")
        if current.store(create=True) is None:
            raise unavailable(current.problem)
        try:
            await asyncio.to_thread(current.configure_presence, expected_revision=payload["expected_revision"],
                                    **changes)
        except RevisionConflict as exc:
            raise HTTPException(status_code=409, detail={"code": "revision_conflict", "message": str(exc)}) from exc
        except (AutomationStoreError, ValueError) as exc:
            raise unavailable("No se guardó la preferencia.") from exc
        return current.presence_snapshot()

    @router.post("/rules")
    async def create(payload: dict = Body(...)) -> dict[str, Any]:
        current = require()
        now = time.time()
        try:
            rule = AutomationRule(
                id=new_id(), title=str(payload.get("title") or "").strip(), enabled=False,
                trigger=Trigger.model_validate(payload.get("trigger") or {}),
                execution=Execution.model_validate(payload.get("execution") or {}),
                delivery=DeliveryPolicy.model_validate(payload.get("delivery") or {}),
                created_at=now, updated_at=now)
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail={"code": "invalid_rule", "message": str(exc)[:600]}) from exc
        store = current.store(create=True)
        if store is None:
            raise unavailable(current.problem)
        try:
            saved = await asyncio.to_thread(store.create_rule, rule)
        except AutomationStoreError as exc:
            raise unavailable(str(exc)) from exc
        return saved.model_dump(mode="json", by_alias=True) | {"problem": _rule_problem(saved, ready_sources=current.ready_sources())}

    @router.put("/rules/{rule_id}")
    async def update(rule_id: str, payload: dict = Body(...)) -> dict[str, Any]:
        current = require()
        store = current.store()
        if store is None:
            if current.problem:
                raise unavailable(current.problem)
            raise HTTPException(status_code=404, detail="No hay automatizaciones.")
        expected = payload.get("expected_revision")
        if not isinstance(expected, int):
            raise HTTPException(status_code=400, detail="expected_revision es obligatorio.")
        changes: dict[str, Any] = {}
        try:
            if "title" in payload:
                changes["title"] = str(payload["title"]).strip()
            if "trigger" in payload:
                changes["trigger"] = Trigger.model_validate(payload["trigger"])
            if "execution" in payload:
                changes["execution"] = Execution.model_validate(payload["execution"])
            if "delivery" in payload:
                changes["delivery"] = DeliveryPolicy.model_validate(payload["delivery"])
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail={"code": "invalid_rule", "message": str(exc)[:600]}) from exc
        if "enabled" in payload:
            enabled = payload["enabled"] is True
            if enabled and payload.get("confirm") is not True:
                raise HTTPException(status_code=400, detail="Habilitar una automatización necesita confirm: true.")
            changes["enabled"] = enabled
        try:
            existing = await asyncio.to_thread(store.get_rule, rule_id)
        except AutomationStoreError as exc:
            raise unavailable(str(exc)) from exc
        if existing is None:
            raise HTTPException(status_code=404, detail="La regla no existe.")
        if changes.get("enabled", existing.enabled):
            preview = AutomationRule.model_validate(existing.model_dump(by_alias=True) | {
                k: (v.model_dump(by_alias=True) if hasattr(v, "model_dump") else v) for k, v in changes.items()})
            if problem := _rule_problem(preview, ready_sources=current.ready_sources()):
                raise HTTPException(status_code=409, detail={"code": "rule_not_ready", "message": problem})
        try:
            updated = await asyncio.to_thread(store.update_rule, rule_id, expected_revision=expected, **changes)
        except RevisionConflict as exc:
            raise HTTPException(status_code=409, detail={"code": "revision_conflict", "message": str(exc)}) from exc
        except StorePathError as exc:
            raise unavailable(str(exc)) from exc
        except (AutomationStoreError, ValidationError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)[:600]) from exc
        warning = "" if permission() == "allow" else (
            "Para que se ejecute sin ti, pon «Automatizaciones» en «Permitir» en Capability Center.")
        return updated.model_dump(mode="json", by_alias=True) | {"problem": _rule_problem(updated, ready_sources=current.ready_sources()), "warning": warning}

    @router.post("/occurrences/{occurrence_id}/cancel")
    async def cancel(occurrence_id: str) -> dict[str, Any]:
        current = require()
        if current.store() is None or current.executor is None:
            raise HTTPException(status_code=404, detail="No hay automatizaciones.")
        try:
            result = await asyncio.to_thread(current.executor.cancel, occurrence_id)
        except AutomationStoreError as exc:
            raise unavailable(str(exc)) from exc
        current.settle_later()  # whatever stays "stopping" is followed until Hermes confirms its end
        return {"result": result}

    return router
