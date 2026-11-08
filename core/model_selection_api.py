"""F01 routes: the model selector of the active conversation and the default model.

The panel reads one state document. It covers:

* the active conversation's selection (``default`` or its own provider/model);
* whether that selection still waits for a turn, was confirmed by Hermes, or
  failed on the last turn;
* the default model of the Gateway that serves the conversation;
* Hermes' authenticated inventory;
* the models the user chose to list (``visible``; None lists them all).

A selection made while IRIS is answering is stored at once and applies to
the next turn; the running turn keeps the model it started with.

Every write names the conversation it was made for (``context``, which changes
on Wake/Continuity switches and survives a compression rotation). A write whose
conversation changed while it was validated is refused; one that passed that
check applies to the conversation and profile it was made for, never to another.

Each request captures the active route (profile, path and credential) once and
uses it for every Hermes query and cache entry it makes, so a switch during an
await never files one profile's catalog or default under another.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Callable

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import JSONResponse

from core import model_selection
from core.model_selection import SelectionError
from core.request_security import is_loopback_host

_INVENTORY_TTL = 60.0
_STALE = "La conversación activa cambió mientras se guardaba; vuelve a elegir el modelo."
_REMOTE_DEFAULT = (
    "El Gateway de Hermes no es el de este equipo: su modelo predeterminado se cambia en ese Hermes."
)


def conversation_state(entry: dict[str, Any], plan: dict[str, Any]) -> str:
    """``pending`` | ``confirmed`` | ``unconfirmed`` | ``failed`` | ``cancelled``.

    The last outcome counts only if it came from the same plan the next turn
    will use (mode, provider and model, also for the default): a new
    selection, a new default model or a new default provider make it
    ``pending``.
    """
    last = entry.get("last") if isinstance(entry.get("last"), dict) else None
    same_plan = bool(
        last
        and last.get("mode") == plan.get("mode")
        and (last.get("model") or "") == (plan.get("model") or "")
        and (last.get("provider") or "") == (plan.get("provider") or "")
    )
    if not same_plan:
        return "pending"
    status = last.get("status")
    if status == "completed":
        return "confirmed" if last.get("confirmed") else "unconfirmed"
    return status if status in ("failed", "cancelled") else "unconfirmed"


def model_selection_router(
    *,
    agent: Callable[[], Any],
    broadcast: Callable[[str, dict], Any],
    visibility: model_selection.SelectorVisibilityStore | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/models")
    inventory_cache: dict[str, tuple[float, dict[str, Any]]] = {}
    inventory_lock = asyncio.Lock()
    visibility = visibility or model_selection.SelectorVisibilityStore()

    def bridge_or_503():
        current = agent()
        bridge = getattr(current, "bridge", None) if current is not None else None
        if bridge is None:
            raise HTTPException(status_code=503, detail="IRIS todavía no está lista.")
        return current, bridge

    async def inventory(bridge, route: dict[str, Any], *, refresh: bool = False) -> dict[str, Any]:
        """``route``'s catalog: queried for that route and cached under it."""
        key = route["profile"] or "default"
        async with inventory_lock:
            cached = inventory_cache.get(key)
            if cached and not refresh and time.monotonic() - cached[0] < _INVENTORY_TTL:
                return cached[1]
            fresh = await bridge.model_inventory(refresh=refresh, route=route)
            inventory_cache[key] = (time.monotonic(), fresh)
            return fresh

    def busy(current) -> bool:
        turn = getattr(current, "_current_turn_task", None)
        return bool(turn is not None and not turn.done())

    async def snapshot(current, bridge, route: dict[str, Any], *, refresh: bool) -> dict[str, Any]:
        profile, session_id = route["profile"], route["session_id"]
        service = bridge.model_selection
        body: dict[str, Any] = {
            "supported": bool(bridge.supports_model_selection),
            "reason": "",
            "busy": busy(current),
            "conversation": {"session_id": session_id, "profile": profile or "default", "context": route["token"]},
            "default": {"provider": "", "model": ""},
            "default_writable": False,
            "default_reason": "",
            "inventory": {"providers": [], "error": ""},
            # The models the user chose to list (None: all of them).
            "visible": await asyncio.to_thread(visibility.load),
        }
        if not bridge.is_available:
            body["reason"] = "hermes_offline"
        elif not bridge.supports_model_selection:
            body["reason"] = "hermes_without_session_models"
        default = await bridge.default_model(route)
        plan, entry = await asyncio.gather(
            asyncio.to_thread(service.turn_plan, profile, session_id, default=default),
            asyncio.to_thread(service.store.entry, profile, session_id),
        )
        body["default"] = {"provider": default.get("provider") or "", "model": default.get("model") or ""}
        body["conversation"].update({
            "mode": plan["mode"],
            "provider": plan.get("provider") or "",
            "model": plan.get("model") or "",
            "state": conversation_state(entry, plan),
            "last": entry.get("last") if isinstance(entry.get("last"), dict) else None,
        })
        if body["supported"]:
            body["default_writable"] = await bridge.gateway_is_local()
            body["default_reason"] = "" if body["default_writable"] else _REMOTE_DEFAULT
            try:
                fresh = await inventory(bridge, route, refresh=refresh)
                body["inventory"]["providers"] = fresh["providers"]
            except Exception as exc:
                body["inventory"]["error"] = str(exc)[:300] or "Hermes no entregó el catálogo."
        return body

    async def state(*, refresh: bool = False) -> dict[str, Any]:
        """One conversation's state: re-read if it was switched during the awaits."""
        current, bridge = bridge_or_503()
        for _attempt in range(3):
            route = bridge.route_context()
            body = await snapshot(current, bridge, route, refresh=refresh)
            now = bridge.route_context()
            if (now["token"], now["session_id"]) == (route["token"], body["conversation"]["session_id"]):
                return body
            refresh = False
        return body

    def require_supported(bridge) -> None:
        if not bridge.supports_model_selection:
            raise HTTPException(
                status_code=409,
                detail="Esta versión de Hermes no permite elegir el modelo por conversación.",
            )

    def require_context(bridge, payload: dict) -> dict[str, Any]:
        """The route of the conversation the client chose for; 409 if it is not the active one."""
        route = bridge.route_context()
        expected_token = str(payload.get("context") or "").strip()
        expected_session = str(payload.get("session_id") or "").strip()
        if (expected_token and expected_token != route["token"]) or (
            expected_session and not expected_token and expected_session != route["session_id"]
        ):
            raise HTTPException(status_code=409, detail=_STALE)
        return route

    def still_active(bridge, route: dict[str, Any]) -> None:
        if bridge.route_context()["token"] != route["token"]:
            raise HTTPException(status_code=409, detail=_STALE)

    async def validated(bridge, route: dict[str, Any], provider: str, model: str) -> None:
        try:
            catalog = await inventory(bridge, route)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Hermes no entregó el catálogo: {str(exc)[:200]}") from exc
        try:
            model_selection.validate_selection(catalog, provider, model)
        except SelectionError as exc:
            raise HTTPException(status_code=400, detail={"code": exc.code, "message": exc.message}) from exc

    @router.get("/selector")
    async def get_selector(refresh: bool = False) -> dict[str, Any]:
        return await state(refresh=refresh)

    @router.get("/apps")
    async def get_app_catalog(request: Request, refresh: bool = False):
        # These apps resolve their workers in the local default profile. A chat
        # may instead be using a named profile or a remote/foreign Gateway.
        headers = {"Cache-Control": "no-store, private"}
        if not request.client or not is_loopback_host(request.client.host):
            raise HTTPException(status_code=403, detail="Consulta los modelos de las apps desde este equipo.", headers=headers)
        _current, bridge = bridge_or_503()
        try:
            payload = await bridge.app_model_inventory(refresh=refresh)
        except Exception:
            raise HTTPException(status_code=503, detail="Inicia el motor local de Iris y vuelve a consultar los modelos.", headers=headers) from None
        return JSONResponse(payload, headers=headers)

    @router.put("/conversation")
    async def set_conversation(payload: dict = Body(...)) -> dict[str, Any]:
        _current, bridge = bridge_or_503()
        require_supported(bridge)
        route = require_context(bridge, payload)
        mode = str(payload.get("mode") or "").strip()
        store = bridge.model_selection.store
        profile = route["profile"]
        if mode == "custom":
            provider = model_selection.clean_provider(payload.get("provider"))
            model = model_selection.clean_model(payload.get("model"))
            await validated(bridge, route, provider, model)
        elif mode != "default":
            raise HTTPException(status_code=400, detail="mode debe ser 'default' o 'custom'.")
        still_active(bridge, route)
        # Read the effective session and write in one step on the event loop: a
        # compression rotation (same conversation, new id) is followed, and a
        # turn ending now cannot copy the old selection over this one.
        session_id = bridge.session_id
        if mode == "custom":
            store.set_selection(profile, session_id, provider, model)
        else:
            store.clear_selection(profile, session_id)
        body = await state()
        await broadcast("model_selection_changed", {"session_id": session_id})
        return body

    @router.put("/visible")
    async def set_visible(payload: dict = Body(...)) -> dict[str, Any]:
        """Choose the models the selector lists; ``models: null`` lists them all again."""
        if "models" not in payload:
            raise HTTPException(status_code=400, detail="Falta la lista 'models' (o null para mostrar todos).")
        try:
            models = await asyncio.to_thread(visibility.save, payload.get("models"))
        except SelectionError as exc:
            raise HTTPException(status_code=400, detail={"code": exc.code, "message": exc.message}) from exc
        await broadcast("model_selection_changed", {"visible": True})
        return {"visible": models}

    @router.put("/default")
    async def set_default(payload: dict = Body(...)) -> dict[str, Any]:
        _current, bridge = bridge_or_503()
        require_supported(bridge)
        route = require_context(bridge, payload)
        profile = route["profile"]
        if not await bridge.gateway_is_local():
            raise HTTPException(status_code=409, detail=_REMOTE_DEFAULT)
        provider = model_selection.clean_provider(payload.get("provider"))
        model = model_selection.clean_model(payload.get("model"))
        # Hermes soft-accepts a model its own endpoint does not list (with a
        # warning); Iris only offers the models of the inventory.
        await validated(bridge, route, provider, model)
        # The catalog was this profile's: write only if it is still the one shown.
        still_active(bridge, route)
        result = await asyncio.to_thread(model_selection.change_default_model, provider, model, profile)
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result.get("error") or "Hermes no cambió el modelo predeterminado.")
        inventory_cache.clear()
        body = await state()
        body["warning"] = result.get("warning") or ""
        body["default_changed_for"] = profile or "default"
        await broadcast("model_selection_changed", {"session_id": bridge.session_id, "default": True})
        return body

    return router
