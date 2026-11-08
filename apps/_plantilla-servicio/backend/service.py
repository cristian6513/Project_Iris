"""Template of an app's Python service (F09.3–F09.4): it reads data and writes nothing.

Core imports this file only for apps listed in ``core/app_services.py``
(``REVIEWED_APP_SERVICES``). It may define:

- ``create_router(context)``: HTTP routes for the app's interface, mounted under
  ``/api/apps/<id>/`` behind Core's usual HTTP protections;
- ``operations(context)``: actions Hermes may ask for, validated and authorized by Core
  (capability ``apps.actions``) before they run, here in Core or in the app's interface.

The interface and Hermes use the same domain function (``load_items``), so they never
disagree. ``context.data_dir`` is the app's own folder in the profile; a request never chooses
a path. Use only the standard library and the packages Iris already ships: an app never
installs dependencies.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from fastapi import APIRouter, HTTPException

from core.app_actions import APP_SURFACE_ROUTE, AppOperation, OperationRejected
from core.app_services import AppDataError

SAMPLE = Path(__file__).with_name("sample_items.json")
MAX_ITEMS = 200


class ItemsUnreadable(Exception):
    """The person's items.json exists but cannot be read."""


def _items(raw: object) -> list[dict]:
    """Only well-formed elements, with bounded text, ever leave the service."""
    if not isinstance(raw, list):
        return []
    items = []
    for entry in raw[:MAX_ITEMS]:
        if isinstance(entry, dict) and isinstance(entry.get("id"), str) and isinstance(entry.get("title"), str):
            items.append({
                "id": entry["id"][:64],
                "title": entry["title"][:120],
                "detail": str(entry.get("detail", ""))[:500],
            })
    return items


def load_items(context) -> dict:
    """The domain operation: the person's own items.json, or the example data, said as such.
    ``context.read_file`` only reads a plain file of the app's own folder, of bounded size."""
    try:
        own = context.read_file("items.json")
        if own is not None:
            return {"items": _items(json.loads(own.decode("utf-8"))), "source": "perfil"}
    except (AppDataError, OSError, ValueError) as exc:
        raise ItemsUnreadable from exc
    return {"items": _items(json.loads(SAMPLE.read_text(encoding="utf-8"))), "source": "ejemplo"}


def create_router(context) -> APIRouter:
    router = APIRouter()

    @router.get("/items")
    def list_items() -> dict:
        try:
            return load_items(context)
        except ItemsUnreadable as exc:
            raise HTTPException(status_code=409, detail="El archivo items.json del perfil no se pudo leer.") from exc

    return router


def _no_args(args: Mapping[str, Any]) -> dict[str, Any]:
    if args:
        raise OperationRejected("Esta acción no lleva argumentos")
    return {}


def _item_id(args: Mapping[str, Any]) -> dict[str, Any]:
    if set(args) != {"item"} or not isinstance(args["item"], str) or not 0 < len(args["item"]) <= 64:
        raise OperationRejected("Indica «item» con el id de un elemento devuelto por la app")
    return {"item": args["item"]}


def _project_items(data: Mapping[str, Any]) -> dict[str, Any]:
    return {"items": _items(data.get("items")), "source": data.get("source") if data.get("source") in {"perfil", "ejemplo"} else ""}


def _project_shown(data: Mapping[str, Any]) -> dict[str, Any]:
    item = data.get("item")
    return {"item": item[:64]} if isinstance(item, str) else {}


def operations(context) -> list[AppOperation]:
    def list_handler(_args: dict[str, Any]) -> dict[str, Any]:
        try:
            return {"status": "ok", "data": load_items(context)}
        except ItemsUnreadable:
            return {"status": "error", "message": "El archivo items.json del perfil no se pudo leer."}

    return [
        # A read Core answers by itself, with the same function the interface uses.
        AppOperation(
            app=context.app_id, action="list_items", label="leer los elementos de la app",
            capability="apps.actions", effect="read", validate_args=_no_args, project_data=_project_items,
            handler=list_handler,
        ),
        # A local change that happens in the app's interface (its handleIntent).
        AppOperation(
            app=context.app_id, action="show_item", label="mostrar un elemento",
            capability="apps.actions", effect="local", validate_args=_item_id, project_data=_project_shown,
            arguments={"item": "id de un elemento devuelto por list_items"}, surface=APP_SURFACE_ROUTE,
            # What the person sees when confirming: the element this action will show.
            describe=lambda args: f"Elemento: {args['item']}",
        ),
    ]
