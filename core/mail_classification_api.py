"""Private F14 controls; no classification/remote mail reads on GET."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from core.calendar_api import _PrivateRoute, _answer
from core.google_api import require_local_peer
from core.mail_classification_store import Options


class Probe(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    provider: str
    model: str


class Configure(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    options: Options
    route: dict | None = None
    confirmed: bool = False


class Correction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    label: str
    revision: int


def create_classification_router(get_runtime):
    router = APIRouter(prefix="/api/mail/classification", dependencies=[Depends(require_local_peer)], route_class=_PrivateRoute)

    async def invoke(method, *args, **kwargs):
        runtime = get_runtime()
        if runtime is None:
            return _answer({"error": {"code": "unavailable", "message": "La clasificación no está disponible."}}, 503)
        try:
            return _answer(await getattr(runtime, method)(*args, **kwargs))
        except Exception:
            # Worker/store exceptions must not reveal credentials, prompts or mail.
            return _answer({"error": {"code": "classification_refused", "message": "No se completó el cambio. Revisa las cuentas, el destino y la revisión de la configuración."}}, 409)

    @router.get("")
    async def snapshot():
        return await invoke("snapshot")

    @router.post("/probe")
    async def probe(body: Probe):
        return await invoke("probe", body.provider, body.model)

    @router.put("")
    async def configure(body: Configure):
        await_result = await invoke("configure", body.options.model_dump(), route=body.route, confirmed=body.confirmed)
        return await_result

    @router.post("/{identity}/correction")
    async def correct(identity: str, body: Correction):
        return await invoke("correct", identity, body.label, body.revision)

    return router
