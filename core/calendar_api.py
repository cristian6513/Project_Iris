"""F11 local-only calendar cache view and explicitly requested calendar-only sync."""

from __future__ import annotations

from typing import Callable

from fastapi import APIRouter, Depends, HTTPException
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict

from core.calendar_models import CalendarError
from core.calendar_service import CalendarService
from core.google_api import require_local_peer
from core.google_connections import GoogleServiceError
from core.google_contracts import ERRORS

_PRIVATE = {"Cache-Control": "no-store, private"}


class _PrivateRoute(APIRoute):
    """Keep validation and peer-boundary refusals private as well as successful reads."""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def private(request):
            try:
                response = await handler(request)
            except RequestValidationError as exc:
                response = await request_validation_exception_handler(request, exc)
            except HTTPException as exc:
                exc.headers = {**(exc.headers or {}), **_PRIVATE}
                raise
            response.headers.update(_PRIVATE)
            return response

        return private


class CalendarSyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_ids: list[str] | None = None


def _answer(payload: dict, status: int = 200) -> JSONResponse:
    return JSONResponse(payload, status_code=status, headers=_PRIVATE)


def _refused(exc: CalendarError | GoogleServiceError) -> JSONResponse:
    if isinstance(exc, CalendarError):
        return _answer({"error": {"code": exc.code, "message": str(exc)}}, 422)
    status = {"client_unavailable": 503, "not_found": 404, "invalid_request": 422}.get(exc.code, 409)
    return _answer({"error": {"code": exc.code, "message": ERRORS.get(exc.code, "No se pudo consultar el calendario.")}}, status)


def create_calendar_router(get_service: Callable[[], CalendarService | None]) -> APIRouter:
    router = APIRouter(prefix="/api/calendar", dependencies=[Depends(require_local_peer)], route_class=_PrivateRoute)

    def service() -> CalendarService:
        value = get_service()
        if value is None:
            raise GoogleServiceError("client_unavailable")
        return value

    @router.get("")
    async def calendar(start: str, end: str, time_zone: str = "UTC") -> JSONResponse:
        try:
            return _answer(await service().snapshot(start, end, time_zone))
        except (CalendarError, GoogleServiceError) as exc:
            return _refused(exc)

    @router.post("/sync")
    async def sync(body: CalendarSyncRequest) -> JSONResponse:
        try:
            return _answer(await service().sync(body.account_ids), 202)
        except (CalendarError, GoogleServiceError) as exc:
            return _refused(exc)

    return router
