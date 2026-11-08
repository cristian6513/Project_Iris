"""F02 routes: the vision model of the profile that serves the conversation.

The view reads one document: the profile's setting (automatic or a model), the
route Hermes' resolver plans for it, the authenticated models with whether
each one reads images (Hermes' own lookup: yes, no or unknown), how Iris
routes images with that setting, and the last analysis. Writes go to the
profile captured when the request arrived; one whose profile changed while it
was validated is refused, so a choice never lands in another profile. Nothing
here sends an image or changes the conversation's chat model (F01).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Callable

from fastapi import APIRouter, Body, HTTPException

from core import model_selection, vision_model
from core.model_selection import SelectionError

_STATUS_TTL = 600.0
_STALE = "El perfil de Hermes cambió mientras se guardaba; vuelve a elegir el modelo de visión."
_REMOTE = "El Gateway de Hermes no es el de este equipo: su modelo de visión se cambia en ese Hermes."
_NO_VISION = "Hermes indica que ese modelo no lee imágenes."


def vision_model_router(*, agent: Callable[[], Any], broadcast: Callable[[str, dict], Any]) -> APIRouter:
    router = APIRouter(prefix="/api/vision-model")
    cache: dict[str, tuple[float, dict[str, Any]]] = {}
    lock = asyncio.Lock()

    def bridge_or_503():
        current = agent()
        bridge = getattr(current, "bridge", None) if current is not None else None
        if bridge is None:
            raise HTTPException(status_code=503, detail="IRIS todavía no está lista.")
        return bridge

    async def catalog(bridge, route: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
        try:
            inventory = await bridge.model_inventory(route=route)
        except Exception as exc:
            return [], str(exc)[:300] or "Hermes no entregó el catálogo."
        return list(inventory.get("providers") or []), ""

    async def profile_status(bridge, route: dict[str, Any], *, refresh: bool) -> dict[str, Any]:
        """The worker's view of ``route``'s profile, cached under that profile only."""
        key = route["profile"] or "default"
        async with lock:
            cached = cache.get(key)
            if cached and not refresh and time.monotonic() - cached[0] < _STATUS_TTL:
                return cached[1]
            rows, catalog_error = await catalog(bridge, route)
            rows = [row for row in rows if row.get("authenticated")]  # only models Hermes can call
            pairs = [{"provider": row["id"], "model": model} for row in rows for model in row.get("models") or []]
            home = vision_model.profile_home(route["profile"])
            try:
                status = await asyncio.to_thread(vision_model.status, home, pairs)
                error = ""
            except vision_model.VisionModelError as exc:
                status = {"configured": await asyncio.to_thread(vision_model.read_configured, home),
                          "planned": {}, "capabilities": {}}
                error = exc.message
            fresh = {"rows": rows, "catalog_error": catalog_error, "status": status, "error": error}
            if not error:
                cache[key] = (time.monotonic(), fresh)
            return fresh

    async def snapshot(bridge, route: dict[str, Any], *, refresh: bool) -> dict[str, Any]:
        profile = route["profile"]
        view = await profile_status(bridge, route, refresh=refresh)
        status = view["status"]
        settings = status["configured"]
        verdicts = status["capabilities"]
        models = [
            {"provider": row["id"], "provider_label": row.get("label") or row["id"], "model": model,
             "vision": verdicts.get((row["id"], model))}
            for row in view["rows"] for model in row.get("models") or []
        ]
        chosen = None
        if vision_model.explicit(settings) and not settings.get("custom_endpoint"):
            chosen = verdicts.get((settings["provider"], settings["model"]))
        writable = await bridge.gateway_is_local()
        return {
            "profile": profile or "default",
            "configured": settings,
            "mode": "model" if vision_model.explicit(settings) else "auto",
            "route": vision_model.image_route(settings),
            "planned": status["planned"],
            "chosen_vision": chosen,
            "models": models,
            "catalog_error": view["catalog_error"],
            "error": view["error"],
            "writable": writable and not settings.get("custom_endpoint"),
            "reason": "" if writable else _REMOTE,
            "last": vision_model.last_analysis(profile),
        }

    @router.get("")
    async def get_state(refresh: bool = False) -> dict[str, Any]:
        bridge = bridge_or_503()
        route = bridge.route_context()
        body = await snapshot(bridge, route, refresh=refresh)
        if bridge.route_context()["profile"] != route["profile"]:
            body = await snapshot(bridge, bridge.route_context(), refresh=False)
        return body

    @router.put("")
    async def set_model(payload: dict = Body(...)) -> dict[str, Any]:
        bridge = bridge_or_503()
        route = bridge.route_context()
        expected = str(payload.get("profile") or "").strip().lower()
        if expected and expected != (route["profile"] or "default"):
            raise HTTPException(status_code=409, detail=_STALE)
        if not await bridge.gateway_is_local():
            raise HTTPException(status_code=409, detail=_REMOTE)
        mode = str(payload.get("mode") or "").strip()
        provider = model = ""
        if mode == "model":
            provider = model_selection.clean_provider(payload.get("provider"))
            model = model_selection.clean_model(payload.get("model"))
            rows, error = await catalog(bridge, route)
            if error:
                raise HTTPException(status_code=502, detail=f"Hermes no entregó el catálogo: {error[:200]}")
            try:
                model_selection.validate_selection({"providers": rows}, provider, model)
            except SelectionError as exc:
                raise HTTPException(status_code=400, detail={"code": exc.code, "message": exc.message}) from exc
            view = await profile_status(bridge, route, refresh=False)
            if view["status"]["capabilities"].get((provider, model)) is False:
                raise HTTPException(status_code=400, detail={"code": "model_without_vision", "message": _NO_VISION})
        elif mode != "auto":
            raise HTTPException(status_code=400, detail="mode debe ser 'auto' o 'model'.")
        if bridge.route_context()["profile"] != route["profile"]:
            raise HTTPException(status_code=409, detail=_STALE)
        home = vision_model.profile_home(route["profile"])
        try:
            written = await asyncio.to_thread(vision_model.write, home, provider=provider, model=model)
        except vision_model.VisionModelError as exc:
            raise HTTPException(status_code=409 if exc.code == "custom_endpoint" else 400,
                                detail={"code": exc.code, "message": exc.message}) from exc
        key = route["profile"] or "default"
        async with lock:
            entry = cache.get(key)
            if entry:  # the models' capabilities did not change: keep them, take Hermes' new route
                status = {**entry[1]["status"], "configured": vision_model.configured({}) | written["configured"],
                          "planned": written["planned"]}
                cache[key] = (entry[0], {**entry[1], "status": status})
        body = await snapshot(bridge, route, refresh=False)
        await broadcast("vision_model_changed", {"profile": route["profile"] or "default"})
        return body

    return router
