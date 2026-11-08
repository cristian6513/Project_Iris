"""Operations that Hermes may ask an IRIS OS app to perform (F09.4).

An operation exists only when reviewed code registers it: app, action, version, an argument
validator, a public projection of its result, the capability that governs it, where it runs
and its effect (``read``, ``local`` or ``external``). The model sends
``iris.app-intent/v1`` ``{schema, app, action, args}``; Core

1. validates the intent against the registered operation (unknown fields, types, ranges, IDs),
2. applies the Capability Center policy (``deny`` stops it here, ``ask`` is confirmed on the
   surface that shows it, never by a flag from the model),
3. runs it once: in a Core handler, or on exactly one surface that announced the route's
   capability, waiting only for that surface's correlated answer,
4. returns ``iris.app-result/v1`` with the projected data alone.

``request_id`` correlates an answer; it is not idempotency. When the answer does not arrive in
time the result says the action may have happened, and nothing is retried. Camera photos keep
their own controller and permission: they are not operations of this bridge.
"""

from __future__ import annotations

import asyncio
import inspect
import hashlib
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Literal, Mapping

logger = logging.getLogger(__name__)

INTENT_SCHEMA = "iris.app-intent/v1"
RESULT_SCHEMA = "iris.app-result/v1"
RESULT_STATUSES = frozenset({"ok", "needs_choice", "unavailable", "denied", "error"})
_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,47}$")


class OperationRejected(Exception):
    """The intent cannot become an operation (HTTP 422 for the caller)."""

    def __init__(self, detail: str, status_code: int = 422) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


@dataclass(frozen=True)
class SurfaceRoute:
    """Where a browser-side operation runs and what it says when it cannot."""

    capability: str
    intent_message: str
    result_message: str
    unavailable_message: str
    disconnected_message: str
    timeout_message: str


# The IRIS OS surface: it runs the apps with their own code and shows the confirmations of
# operations that Core itself handles.
APP_SURFACE_ROUTE = SurfaceRoute(
    capability="hosts_iris_apps",
    intent_message="iris_app_intent",
    result_message="iris_app_result",
    unavailable_message="Abre IRIS OS para usar esta app.",
    disconnected_message="La superficie de IRIS OS se desconectó.",
    timeout_message=(
        "La app recibió la orden, pero no confirmó el resultado a tiempo. "
        "Pudo haberse hecho; compruébalo antes de reintentar."
    ),
)


@dataclass(frozen=True)
class AppOperation:
    app: str
    action: str
    label: str
    capability: str
    effect: Literal["read", "local", "external"]
    validate_args: Callable[[Mapping[str, Any]], dict[str, Any]]
    project_data: Callable[[Mapping[str, Any]], dict[str, Any]]
    version: int = 1
    arguments: Mapping[str, str] = field(default_factory=dict)
    surface: SurfaceRoute | None = None
    handler: Callable[[dict[str, Any]], Any] | None = None
    timeout_s: float = 20.0
    confirmation_timeout_s: float = 120.0
    denied_message: str = "Capability Center bloqueó esta acción."
    # Reviewed code that says, from the validated arguments, what the action works on
    # («Elemento: dos», «Volumen al 35 %»). Shown in the confirmation; never secrets.
    describe: Callable[[Mapping[str, Any]], str] | None = None
    permission_scope: Callable[[dict[str, Any]], Any] | None = None
    remember_args: bool = True
    remember_required_args: tuple[str, ...] = ()

    def can_remember(self, args: Mapping[str, Any]) -> bool:
        return self.permission_scope is not None and all(args.get(key) for key in self.remember_required_args)

    def summary(self, args: Mapping[str, Any]) -> str:
        if self.describe is None:
            return ""
        try:
            text = str(self.describe(args) or "")
        except Exception:
            logger.warning("Could not describe %s.%s", self.app, self.action, exc_info=True)
            return ""
        return " ".join(text.split())[:160]

    def __post_init__(self) -> None:
        if not _NAME.fullmatch(self.app) or not _NAME.fullmatch(self.action):
            raise ValueError(f"Operación con nombre no válido: {self.app}.{self.action}")
        if self.effect not in {"read", "local", "external"}:
            raise ValueError(f"Efecto no válido en {self.app}.{self.action}")
        if (self.surface is None) == (self.handler is None):
            raise ValueError(f"{self.app}.{self.action} necesita una superficie o un handler, uno de los dos")


class AppActionRegistry:
    """The reviewed operations, by app and action. Registering twice is an error."""

    def __init__(self) -> None:
        self._operations: dict[tuple[str, str], AppOperation] = {}

    def register(self, operation: AppOperation) -> None:
        key = (operation.app, operation.action)
        if key in self._operations:
            raise ValueError(f"La operación {operation.app}.{operation.action} ya está registrada")
        self._operations[key] = operation

    def register_all(self, operations: list[AppOperation] | tuple[AppOperation, ...]) -> tuple[tuple[str, str], ...]:
        """Register several operations at once, or none: a duplicate, inside the list or with
        what is already registered, leaves the registry as it was. Returns the keys added."""
        keys = [(operation.app, operation.action) for operation in operations]
        repeated = {key for key in keys if keys.count(key) > 1 or key in self._operations}
        if repeated:
            names = ", ".join(sorted(f"{app}.{action}" for app, action in repeated))
            raise ValueError(f"Operaciones repetidas: {names}")
        for operation in operations:
            self._operations[(operation.app, operation.action)] = operation
        return tuple(keys)

    def unregister(self, keys: Iterable[tuple[str, str]]) -> None:
        """Take back exactly what one register_all added; what was there before stays."""
        for key in keys:
            self._operations.pop(key, None)

    def get(self, app: str, action: str) -> AppOperation | None:
        return self._operations.get((app, action))

    def catalog(self) -> list[dict[str, Any]]:
        """What Hermes may ask for: names, effect and arguments, never data or paths."""
        return [{
            "app": op.app,
            "action": op.action,
            "version": op.version,
            "label": op.label,
            "effect": op.effect,
            "capability": op.capability,
            "arguments": dict(op.arguments),
            "runs_on": "surface" if op.surface else "core",
        } for op in sorted(self._operations.values(), key=lambda op: (op.app, op.action))]


def result(status: str, message: str = "", data: Mapping[str, Any] | None = None, request_id: str = "") -> dict[str, Any]:
    return {
        "schema": RESULT_SCHEMA,
        "request_id": request_id,
        "status": status if status in RESULT_STATUSES else "error",
        "message": str(message or "")[:300],
        "data": dict(data or {}),
    }


class AppActionBridge:
    """Validate, authorize and run registered operations; correlate surface answers."""

    def __init__(
        self,
        registry: AppActionRegistry,
        *,
        surfaces: Callable[[], Any],
        capabilities: Callable[[], Any],
        on_disconnect: Callable[[Any], None] = lambda socket: None,
        on_audit: Callable[[], None] = lambda: None,
        owner_allowed: Callable[[Any], bool] = lambda socket: True,
    ) -> None:
        self.registry = registry
        self._surfaces = surfaces
        self._capabilities = capabilities
        self._on_disconnect = on_disconnect
        self._on_audit = on_audit
        self._owner_allowed = owner_allowed
        # request_id → (surface socket, result message type, operation, future)
        self._waiters: dict[str, tuple[Any, str, AppOperation, asyncio.Future]] = {}

    def resolve(self, payload: Mapping[str, Any]) -> tuple[AppOperation, dict[str, Any]]:
        """The registered operation and its validated arguments, or OperationRejected."""
        if payload.get("schema", INTENT_SCHEMA) != INTENT_SCHEMA:
            raise OperationRejected("Contrato de acción no compatible")
        unknown = set(payload) - {"schema", "app", "action", "args"}
        if unknown:
            raise OperationRejected("La solicitud contiene campos no admitidos")
        app = str(payload.get("app") or "").strip().casefold()
        action = str(payload.get("action") or "").strip().casefold()
        operation = self.registry.get(app, action)
        if operation is None:
            raise OperationRejected("Acción no admitida")
        args = payload.get("args", {})
        if not isinstance(args, Mapping):
            raise OperationRejected("Los argumentos deben ser un objeto")
        return operation, operation.validate_args(args)

    def project(self, operation: AppOperation, payload: Mapping[str, Any], request_id: str) -> tuple[dict[str, Any], bool | None]:
        """The public result of a surface answer, and the person's confirmation, kept apart."""
        status = str(payload.get("status") or "error").strip().casefold()
        if payload.get("schema") != RESULT_SCHEMA or status not in RESULT_STATUSES:
            status = "error"
        raw = payload.get("data") if isinstance(payload.get("data"), Mapping) else {}
        confirmed = raw.get("permission_confirmed")
        public = result(status, str(payload.get("message") or "").strip(), operation.project_data(raw), request_id)
        return public, confirmed if isinstance(confirmed, bool) else None

    async def _audit(self, operation: AppOperation, kind: str, verdict: str, detail: str) -> None:
        try:
            await asyncio.to_thread(
                self._capabilities().record_event, kind,
                capability=operation.capability, verdict=verdict, detail=detail,
            )
        except Exception:
            logger.warning("Could not persist the capability event of %s.%s", operation.app, operation.action, exc_info=True)
        else:
            self._on_audit()

    async def _scope_key(self, operation: AppOperation, args: dict) -> str:
        identity = operation.permission_scope(args)
        if inspect.isawaitable(identity):
            identity = await identity
        if identity is None:
            raise ValueError("El alcance de esta operación ya no está disponible.")
        data = [operation.app, operation.action, operation.version, operation.capability, identity,
                args if operation.remember_args else None]
        return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=True, allow_nan=False).encode()).hexdigest()

    async def _decision(self, operation: AppOperation, args: dict | None = None) -> Mapping[str, Any]:
        decision = await asyncio.to_thread(
            self._capabilities().evaluate, operation.capability, trigger="manual", confirmed=False,
        )
        if decision["verdict"] == "ask" and args is not None and operation.can_remember(args):
            grants = await asyncio.to_thread(self._capabilities().action_grants, operation.app, operation.action)
            if grants:
                try:
                    key = await self._scope_key(operation, args)
                except Exception:
                    key = ""
                if key in grants:
                    return {**decision, "verdict": "allow", "requires_confirmation": False, "remembered": True}
        return decision

    async def execute(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        operation, args = self.resolve(payload)
        decision = await self._decision(operation, args)
        if decision["verdict"] == "deny":
            await self._audit(operation, "action_blocked", "deny",
                              f"Acción {operation.action} bloqueada por la política local.")
            return result("denied", operation.denied_message)
        if operation.handler is not None:
            if decision["requires_confirmation"]:
                # A Core handler has no screen: IRIS OS asks the person first, and only a
                # «yes» shown on that surface lets it run.
                asked = await self._run_on_surface(operation, args, decision, route=APP_SURFACE_ROUTE, confirm_only=True)
                if asked["status"] != "ok":
                    return asked
                # The policy may have changed while the dialog was open: a «yes» given
                # meanwhile does not outlive a deny (Codex A02). It is checked again after
                # the confirmation is recorded, so the last check sits right before the effect,
                # with no other wait in between (Codex R08).
                if not await self._still_allowed(operation):
                    return result("denied", operation.denied_message, request_id=asked["request_id"])
                await self._audit(operation, "action_confirmed", "allow",
                                  f"Acción {operation.action} confirmada en la superficie local.")
                if not await self._still_allowed(operation):
                    return result("denied", operation.denied_message, request_id=asked["request_id"])
            return await self._run_handler(operation, args)
        return await self._run_on_surface(operation, args, decision)

    async def _still_allowed(self, operation: AppOperation) -> bool:
        if (await self._decision(operation))["verdict"] != "deny":
            return True
        await self._audit(operation, "action_blocked", "deny",
                          f"Acción {operation.action} bloqueada: la política cambió durante la confirmación.")
        return False

    async def _run_handler(self, operation: AppOperation, args: dict[str, Any]) -> dict[str, Any]:
        # A plain function runs in a worker thread so it never blocks Core's loop. Its limit
        # stops the wait, not the work: past it the action may still have happened. One
        # deadline covers the whole run, including an awaitable a plain function returns
        # (Codex R10).
        try:
            async with asyncio.timeout(operation.timeout_s):
                if inspect.iscoroutinefunction(operation.handler):
                    answer = await operation.handler(args)
                else:
                    answer = await asyncio.to_thread(operation.handler, args)
                if inspect.isawaitable(answer):
                    answer = await answer
        except OperationRejected:
            raise
        except TimeoutError:
            return result("unavailable", "La acción no terminó a tiempo. Pudo haberse hecho; compruébalo antes de reintentar.")
        except Exception:
            logger.warning("Operation %s.%s failed", operation.app, operation.action, exc_info=True)
            return result("error", "La acción no se pudo completar.")
        if not isinstance(answer, Mapping):
            return result("error", "La acción no devolvió un resultado válido.")
        status = str(answer.get("status") or "ok")
        data = answer.get("data") if isinstance(answer.get("data"), Mapping) else {}
        return result(status, str(answer.get("message") or ""), operation.project_data(data))

    async def _run_on_surface(
        self,
        operation: AppOperation,
        args: dict[str, Any],
        decision: Mapping[str, Any],
        *,
        route: SurfaceRoute | None = None,
        confirm_only: bool = False,
    ) -> dict[str, Any]:
        route = route or operation.surface
        owner = self._surfaces().capability_owner(route.capability, allowed=self._owner_allowed)
        if owner is None:
            return result("unavailable", route.unavailable_message)
        request_id = uuid.uuid4().hex
        timeout_s = operation.confirmation_timeout_s if confirm_only else operation.timeout_s
        summary = await asyncio.to_thread(operation.summary, args)  # may read a library: off the loop
        intent = {
            "schema": INTENT_SCHEMA,
            "app": operation.app,
            "action": operation.action,
            "args": args,
            "operation": {
                "version": operation.version, "effect": operation.effect, "label": operation.label,
                "summary": summary,
            },
            "permission": {
                "capability": operation.capability,
                "mode": decision["mode"],
                "requires_confirmation": decision["requires_confirmation"],
            },
            "request_id": request_id,
        }
        if confirm_only:
            intent["confirm_only"] = True
            intent["expires_at"] = time.time() + timeout_s
            if operation.can_remember(args):
                intent["permission"]["remember_scope"] = (
                    "Solo esta consulta con los mismos datos en la cuenta seleccionada. Otra cuenta o consulta volverá a pedir permiso."
                    if operation.remember_args else
                    "Solo esta operación en esta app. Una actualización o reinstalación volverá a pedir permiso.")
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._waiters[request_id] = (owner, route.result_message, operation, future)
        try:
            await owner.send_text(json.dumps({"type": route.intent_message, "payload": intent}))
            outcome, confirmed, remember = await asyncio.wait_for(future, timeout=timeout_s)
        except TimeoutError:
            if confirm_only:
                # Nobody said yes in time: the Core handler never ran.
                return result("denied", "No se confirmó a tiempo; la acción no se ejecutó.", request_id=request_id)
            # Delivered but not confirmed: it may have happened. Never retried here.
            return result("unavailable", route.timeout_message, request_id=request_id)
        except Exception:
            self._on_disconnect(owner)
            return result("unavailable", route.disconnected_message, request_id=request_id)
        finally:
            self._waiters.pop(request_id, None)
            if confirm_only:
                try:
                    await owner.send_text(json.dumps({"type": "iris_app_intent_closed", "payload": {"request_id": request_id}}))
                except Exception:
                    pass
        if decision["requires_confirmation"] and confirmed is not None and not (confirm_only and confirmed):
            await self._audit(
                operation,
                "action_confirmed" if confirmed else "action_blocked",
                "allow" if confirmed else "deny",
                f"Acción {operation.action} {'confirmada' if confirmed else 'rechazada'} en la superficie local.",
            )
        if confirm_only and outcome["status"] != "unavailable" and (outcome["status"] != "ok" or confirmed is not True):
            # Only an explicit «yes» on the surface counts; anything else means it did not run.
            return result("denied", outcome["message"] or "La acción no fue confirmada en este equipo.", request_id=request_id)
        if confirm_only and outcome["status"] == "ok" and confirmed is True and remember and operation.can_remember(args):
            try:
                key = await self._scope_key(operation, args)
                saved = await asyncio.to_thread(self._capabilities().remember_action, key,
                    app=operation.app, action=operation.action, capability=operation.capability, label=operation.label,
                    expected=(decision["mode"], decision.get("updated_at", "")))
            except Exception:
                return result("error", "No se pudo recordar el permiso; la acción no se ejecutó.", request_id=request_id)
            if not saved:
                return result("denied", "El permiso cambió mientras decidías. Revisa Permisos y vuelve a intentarlo.", request_id=request_id)
            self._on_audit()
        return outcome

    def answer(self, socket: Any, message_type: str, payload: Mapping[str, Any]) -> bool:
        """A surface's answer. Only the socket that received the intent, with the same message
        family, can answer it, and only once."""
        request_id = str(payload.get("request_id") or "")
        pending = self._waiters.get(request_id)
        if pending is None or pending[0] is not socket or pending[1] != message_type:
            logger.warning("Ignored an unsolicited app action result: %s", request_id[:48])
            return False
        _, _, operation, future = pending
        if not future.done():
            raw = payload.get("data")
            remember = isinstance(raw, Mapping) and raw.get("permission_remember") is True
            future.set_result((*self.project(operation, payload, request_id), remember))
        return True

    def pending(self) -> int:
        return len(self._waiters)
