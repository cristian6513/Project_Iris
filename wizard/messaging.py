"""Local wizard routes backed by the installed Hermes Channels implementation."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import io
import json
import os
import subprocess
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from core.hermes_runtime import (
    get_hermes_repo, hermes_python_prefix, hermes_subprocess_env, resolve_hermes_python,
)
from core.runtime_descriptor import get_runtime

router = APIRouter(prefix="/api/messaging", tags=["messaging"])

# The Gateway's provider pre-flight, injected by the app that mounts this router
# (wizard.app's ``_ai_readiness``; no import cycle). Restarting Hermes from here refuses
# exactly when «Iniciar gateway» would (R02); without a check it refuses too.
_restart_readiness = None


def set_restart_readiness(check) -> None:
    global _restart_readiness
    _restart_readiness = check


class HermesMessaging:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.process = None

    async def _close(self):
        process, self.process = self.process, None
        if not process:
            return
        if process.stdin:
            process.stdin.close()
        try:
            await asyncio.wait_for(process.wait(), 8)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await process.wait()

    async def close(self):
        async with self.lock:
            await self._close()

    async def call(self, action, data=None):
        async with self.lock:
            try:
                if not self.process or self.process.returncode is not None:
                    python, repo = resolve_hermes_python(), get_hermes_repo()
                    prefix = hermes_python_prefix(python) if python else None
                    if not prefix or not repo.is_dir():
                        raise HTTPException(503, (
                            "El motor incluido no está disponible. Repara Iris para configurar canales."
                            if get_runtime().packaged else "Instala Hermes para consultar sus canales."
                        ))
                    if "-B" not in prefix[1:] and not any(
                        flag.startswith("pycache_prefix=") for flag in prefix[1:]
                    ):
                        prefix.append("-B")
                    options = (
                        {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
                        if os.name == "nt"
                        else {}
                    )
                    spawning = asyncio.create_task(
                        asyncio.create_subprocess_exec(
                            *prefix,
                            "-u",
                            str(Path(__file__).with_name("hermes_messaging_worker.py")),
                            str(repo),
                            cwd=str(repo),
                            env=hermes_subprocess_env(),
                            stdin=asyncio.subprocess.PIPE,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.DEVNULL,
                            limit=2 * 1024 * 1024,
                            **options,
                        )
                    )
                    try:
                        self.process = await asyncio.shield(spawning)
                    except asyncio.CancelledError:
                        self.process = await spawning
                        raise
                self.process.stdin.write(
                    json.dumps({"action": action, "data": data or {}}).encode() + b"\n"
                )
                await self.process.stdin.drain()
                line = await asyncio.wait_for(self.process.stdout.readline(), 60)
                if not line:
                    raise RuntimeError("Messaging adapter ended")
                reply = json.loads(line)
                if not reply.get("ok"):
                    raise HTTPException(
                        int(reply.get("status", 500)), reply.get("message", "Error de Hermes.")
                    )
                return reply["data"]
            except HTTPException:
                raise
            except asyncio.CancelledError:
                await self._close()
                raise
            except Exception:
                await self._close()
                raise HTTPException(
                    503, "No se pudo comunicar con los canales de Hermes. Vuelve a actualizar."
                ) from None


messaging = HermesMessaging()


class ChannelUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool | None = None
    env: dict[str, str] = Field(default_factory=dict, max_length=128)
    clear_env: list[str] = Field(default_factory=list, max_length=128)


class PairingStart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: str = Field(default="self-chat", pattern="^(self-chat|bot)$")
    allowed_users: str = Field(default="", max_length=4000)


def _local(request):
    from wizard.app import _enforce_local_browser_action

    _enforce_local_browser_action(request, "La configuración de mensajería")


def _response(data):
    return JSONResponse(data, headers={"Cache-Control": "no-store"})


async def _sync_identity():
    def sync():
        from core.config import IRISConfig, get_iris_home
        from core.file_lock import interprocess_file_lock
        from core.identity_api import sync_hermes_identity_from_config

        with interprocess_file_lock(get_iris_home() / "identity.lock"):
            result = sync_hermes_identity_from_config(IRISConfig.load())
        if any(value == "error" for value in result.values()):
            raise RuntimeError("Identity synchronization failed")

    try:
        await asyncio.to_thread(sync)
        return None
    except Exception:
        return (
            "El canal se guardó, pero Iris no pudo actualizar la personalidad compartida. "
            "Reinicia Iris para reintentar."
        )


@router.get("/platforms")
async def catalog(request: Request, refresh: bool = False):
    _local(request)
    return _response(await messaging.call("catalog", {"refresh": refresh}))


@router.put("/platforms/{platform}")
async def save_channel(platform: str, body: ChannelUpdate, request: Request):
    _local(request)
    if any(len(k) > 128 or len(v) > 100_000 for k, v in body.env.items()):
        raise HTTPException(400, "Uno de los campos es demasiado largo.")
    result = await messaging.call("save", {"platform": platform, "update": body.model_dump()})
    result["warning"] = await _sync_identity()
    return _response(result)


@router.post("/prepare")
async def prepare(request: Request):
    _local(request)
    result = await messaging.call("prepare")
    result["warning"] = await _sync_identity()
    return _response(result)


@router.post("/platforms/{platform}/test")
async def test_channel(platform: str, request: Request):
    _local(request)
    return _response(await messaging.call("test", {"platform": platform}))


@router.post("/restart")
async def restart(request: Request):
    _local(request)
    from core.config import ENGINE_CONSENT_MESSAGE, engine_consent_missing

    if await asyncio.to_thread(engine_consent_missing):
        raise HTTPException(status_code=409, detail=ENGINE_CONSENT_MESSAGE)
    readiness = await _restart_readiness() if _restart_readiness is not None else {"ready": None}
    if readiness.get("ready") is not True:
        raise HTTPException(status_code=409, detail=readiness.get("message")
                            or "No se pudo comprobar tu proveedor de IA; Hermes no se reinició.")
    result = await messaging.call("restart")
    return _response(
        {
            "restart_started": bool(result.get("restart_started")),
            "needs_restart": bool(result.get("needs_restart")),
        }
    )


def _pairing_response(result):
    raw = result.pop("qr_payload", None)
    if raw:
        import qrcode
        import qrcode.image.svg

        out = io.BytesIO()
        qrcode.make(raw, image_factory=qrcode.image.svg.SvgPathImage, border=4).save(out)
        result["qr_image"] = (
            "data:image/svg+xml;base64," + base64.b64encode(out.getvalue()).decode()
        )
    return _response(result)


@router.post("/whatsapp/pairing")
async def start_pairing(body: PairingStart, request: Request):
    _local(request)
    return _pairing_response(await messaging.call("whatsapp_start", body.model_dump()))


@router.get("/whatsapp/pairing/{pairing_id}")
async def poll_pairing(pairing_id: str, request: Request):
    _local(request)
    return _pairing_response(await messaging.call("whatsapp_poll", {"pairing_id": pairing_id}))


@router.delete("/whatsapp/pairing/{pairing_id}")
async def cancel_pairing(pairing_id: str, request: Request):
    _local(request)
    return _response(await messaging.call("whatsapp_cancel", {"pairing_id": pairing_id}))


@router.post("/whatsapp/pairing/{pairing_id}/apply")
async def apply_pairing(pairing_id: str, request: Request):
    _local(request)
    result = await messaging.call("whatsapp_apply", {"pairing_id": pairing_id})
    result["warning"] = await _sync_identity()
    return _response(result)
