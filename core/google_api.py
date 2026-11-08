"""F10: the local HTTP contract of Iris' Google connections, ``/api/connections/google``.

Every route, GET included, answers only to this PC (loopback peer), whatever pairing a LAN
device has: a paired phone is not consent to read someone's mail. Core's Host/Origin middleware
still applies on top. Answers are ``no-store, private`` and carry public projections only.

The UI calls this; Calendario and Correo (F11/F13) call the service, not HTTP.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict

from core.google_connections import GoogleConnectionsService, GoogleServiceError
from core.google_contracts import ERRORS
from core.request_security import is_loopback_host

_STATUS = {
    "invalid_request": 422,
    "not_found": 404,
    "busy": 409,
    "service_busy": 409,
    "account_exists": 409,
    "identity_conflict": 409,
    "revocation_pending": 409,
    "revision_conflict": 409,
    "stale": 409,
    "service_not_granted": 409,
    "not_connected": 409,
    "still_connected": 409,
    "client_unavailable": 503,
    "client_invalid": 422,
    "client_in_use": 409,
}
_PRIVATE = {"Cache-Control": "no-store, private"}


def require_local_peer(request: Request) -> None:
    """Only this PC: the loopback peer, for every route of the router."""
    peer = request.client.host if request.client else ""
    if not peer or not is_loopback_host(peer):
        raise HTTPException(status_code=403, detail="Las conexiones de Google solo se administran en este equipo.")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SessionRequest(_Strict):
    services: list[str]
    request_id: str
    account_id: str | None = None


class ServicesRequest(_Strict):
    services: list[str]
    revision: int


class DisconnectRequest(_Strict):
    revoke_remote: bool = False
    clear_cache: bool = False
    revision: int | None = None


class SyncRequest(_Strict):
    services: list[str] | None = None


class _PrivateGoogleRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()
        async def handler(request):
            try:
                response = await original(request)
            except HTTPException as exc:
                response = JSONResponse({'detail': exc.detail}, status_code=exc.status_code)
            response.headers.update(_PRIVATE)
            return response
        return handler


async def _client_request(request: Request, *, removing: bool = False) -> dict:
    """Bound and validate ourselves: Pydantic validation errors must never echo a credential."""
    from core.google_client_config import unique_object
    raw = bytearray()
    try:
        async for chunk in request.stream():
            if len(raw) + len(chunk) > 128 * 1024:
                raise ValueError
            raw.extend(chunk)
        body = json.loads(raw, object_pairs_hook=unique_object)
        required = {'revision'} if removing else {'revision', 'json_text'}
        if not isinstance(body, dict) or set(body) != required or type(body['revision']) is not int or body['revision'] < 0:
            raise ValueError
        if not removing and not isinstance(body['json_text'], str):
            raise ValueError
        return body
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise GoogleServiceError('client_invalid') from None


def _answer(payload: Any, status_code: int = 200) -> JSONResponse:
    return JSONResponse(payload, status_code=status_code, headers=_PRIVATE)


def _refused(exc: GoogleServiceError) -> JSONResponse:
    return _answer({"error": {"code": exc.code, "message": ERRORS.get(exc.code, "")}},
                   status_code=_STATUS.get(exc.code, 502))


def create_google_router(get_service: Callable[[], GoogleConnectionsService | None]) -> APIRouter:
    router = APIRouter(prefix="/api/connections/google", dependencies=[Depends(require_local_peer)], route_class=_PrivateGoogleRoute)

    def service() -> GoogleConnectionsService:
        current = get_service()
        if current is None:
            raise GoogleServiceError("client_unavailable")
        return current

    @router.get("")
    async def connections() -> JSONResponse:
        try:
            return _answer(await service().snapshot())
        except GoogleServiceError as exc:
            return _refused(exc)

    @router.get('/client')
    async def client_configuration():
        try:
            return _answer(await service().client_configuration())
        except GoogleServiceError as exc:
            return _refused(exc)

    @router.put('/client')
    async def import_client(request: Request):
        try:
            body = await _client_request(request)
            return _answer(await service().configure_client(body['json_text'], revision=body['revision']))
        except GoogleServiceError as exc:
            return _refused(exc)

    @router.delete('/client')
    async def remove_client(request: Request):
        try:
            body = await _client_request(request, removing=True)
            return _answer(await service().configure_client(None, revision=body['revision']))
        except GoogleServiceError as exc:
            return _refused(exc)

    @router.post("/sessions")
    async def start_session(body: SessionRequest) -> JSONResponse:
        try:
            session = await service().start_session(body.services, request_id=body.request_id,
                                                    account_id=body.account_id)
        except GoogleServiceError as exc:
            return _refused(exc)
        return _answer(session, status_code=202)

    @router.get("/sessions/{session_id}")
    async def session(session_id: str) -> JSONResponse:
        try:
            return _answer(service().session(session_id))
        except GoogleServiceError as exc:
            return _refused(exc)

    @router.post("/sessions/{session_id}/cancel")
    async def cancel_session(session_id: str) -> JSONResponse:
        try:
            return _answer(await service().cancel_session(session_id))
        except GoogleServiceError as exc:
            return _refused(exc)

    @router.patch("/accounts/{account_id}/services")
    async def set_services(account_id: str, body: ServicesRequest) -> JSONResponse:
        try:
            return _answer(await service().set_services(account_id, body.services,
                                                        expected_revision=body.revision))
        except GoogleServiceError as exc:
            return _refused(exc)

    @router.post("/accounts/{account_id}/disconnect")
    async def disconnect(account_id: str, body: DisconnectRequest) -> JSONResponse:
        try:
            return _answer(await service().disconnect(account_id, revoke_remote=body.revoke_remote,
                                                      clear_cache=body.clear_cache, expected_revision=body.revision))
        except GoogleServiceError as exc:
            return _refused(exc)

    @router.delete("/accounts/{account_id}")
    async def forget(account_id: str) -> JSONResponse:
        """«Quitar de la lista»: only a disconnected account; its saved data goes with it."""
        try:
            return _answer(await service().forget(account_id))
        except GoogleServiceError as exc:
            return _refused(exc)

    @router.post("/accounts/{account_id}/sync")
    async def sync(account_id: str, body: SyncRequest) -> JSONResponse:
        """«Actualizar datos»: a bounded read, joined if one is running. Numbers and states only."""
        try:
            return _answer(await service().sync(account_id, body.services), status_code=202)
        except GoogleServiceError as exc:
            return _refused(exc)

    @router.get("/accounts/{account_id}/sync")
    async def sync_state(account_id: str) -> JSONResponse:
        try:
            return _answer(await service().sync_state(account_id))
        except GoogleServiceError as exc:
            return _refused(exc)

    return router
