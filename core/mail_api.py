"""Private F13 API. GET is local; explicit POSTs read a body or sync Gmail."""
import asyncio
from typing import Callable
import sqlite3

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict

from core.calendar_api import _PrivateRoute, _answer
from core.google_api import require_local_peer
from core.google_connections import GoogleConnectionsService, GoogleServiceError
from core.google_contracts import ERRORS
from core.google_store import GoogleStoreError
from core.mail_service import MailError, MailService


class MailSyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    account_ids: list[str]


def create_mail_router(get_google: Callable[[], GoogleConnectionsService | None]) -> APIRouter:
    router = APIRouter(prefix="/api/mail", dependencies=[Depends(require_local_peer)], route_class=_PrivateRoute)

    def refused(exc):
        status = exc.status if isinstance(exc, MailError) else {"client_unavailable": 503, "not_found": 404, "invalid_request": 422}.get(exc.code, 409)
        return _answer({"error": {"code": exc.code, "message": str(exc) if isinstance(exc, MailError)
                                  else ERRORS.get(exc.code, "No se pudo consultar Correo.")}}, status)

    def unavailable():
        return _answer({"error": {"code": "cache_unavailable", "message": "No se pudo abrir la copia local de Correo. Reintenta cuando esté disponible."}}, 503)

    @router.get("")
    async def mail(account_id: str = "", folder: str = "inbox", filter: str = "all", query: str = "", cursor: str = "", limit: int = 40, focus_id: str = ""):
        try:
            return _answer(await MailService(get_google()).snapshot(account_id=account_id, folder=folder, filter=filter, query=query, cursor=cursor, limit=limit, focus_id=focus_id))
        except (MailError, GoogleServiceError) as exc:
            return refused(exc)
        except (GoogleStoreError, sqlite3.Error, OSError, ValueError):
            return unavailable()

    @router.get("/messages/{key}")
    async def detail(key: str):
        try:
            return _answer(await MailService(get_google()).detail(key))
        except (MailError, GoogleServiceError) as exc:
            return refused(exc)
        except (GoogleStoreError, sqlite3.Error, OSError, ValueError):
            return unavailable()

    @router.post("/sync")
    async def sync(body: MailSyncRequest):
        try:
            return _answer(await MailService(get_google()).sync(body.account_ids), 202)
        except (MailError, GoogleServiceError) as exc:
            return refused(exc)
        except (GoogleStoreError, sqlite3.Error, OSError, ValueError):
            return unavailable()

    @router.post("/messages/{key}/body")
    async def body(key: str, request: Request):
        async def disconnected():
            while not await request.is_disconnected():
                await asyncio.sleep(.1)

        work = asyncio.create_task(MailService(get_google()).body(key))
        gone = asyncio.create_task(disconnected())
        try:
            done, _ = await asyncio.wait([work, gone], return_when=asyncio.FIRST_COMPLETED)
            if gone in done:
                return _answer({"error": {"code": "cancelled", "message": "Lectura cancelada."}}, 499)
            return _answer(await work)
        except (MailError, GoogleServiceError) as exc:
            return refused(exc)
        except (GoogleStoreError, sqlite3.Error, OSError, ValueError):
            return unavailable()
        finally:
            work.cancel(); gone.cancel()
            # Work cancellation collects the worker before ownership is released.
            await GoogleConnectionsService._settle(asyncio.create_task(GoogleConnectionsService._collect_mail([work, gone])))

    return router
