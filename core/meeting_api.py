"""Private F15 routes. GET is local-only; every model send requires a preview confirmation."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from core.calendar_api import _PrivateRoute, _answer
from core.google_api import require_local_peer
from core.meeting_store import MeetingError


class Preview(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    event_id: str = Field(pattern=r"^event_[a-f0-9]{32}$")
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    provider: str = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=200)
    description: bool = False
    notes: str = Field(default="", max_length=4000)


class Consent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    confirmed: bool = False


def create_meeting_router(get_runtime):
    router = APIRouter(prefix="/api/calendar/preparation", dependencies=[Depends(require_local_peer)], route_class=_PrivateRoute)
    async def call(method, *args, **kwargs):
        runtime = get_runtime()
        if runtime is None:
            return _answer({"error": {"code": "unavailable", "message": "La preparación de reuniones no está disponible."}}, 503)
        try:
            return _answer(await getattr(runtime, method)(*args, **kwargs))
        except MeetingError as exc:
            return _answer({"error": {"code": "preparation_refused", "message": str(exc)}}, 409)
        except Exception:
            return _answer({"error": {"code": "preparation_failed", "message": "No se pudo preparar la reunión. No se reintentará automáticamente."}}, 409)
    @router.get("")
    async def snapshot():
        return await call("snapshot")
    @router.post("/preview")
    async def preview(body: Preview):
        values = body.model_dump()
        return await call("preview", values.pop("event_id"), values.pop("date"), values.pop("provider"), values.pop("model"), **values)
    @router.post("/{identity}/start")
    async def start(identity: str, body: Consent):
        return await call("start", identity, confirmed=body.confirmed)
    @router.post("/{identity}/cancel")
    async def cancel(identity: str):
        return await call("cancel", identity)
    return router
