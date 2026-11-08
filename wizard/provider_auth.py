"""Local provider-login operations; the browser receives display data only."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import secrets
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from core.hermes_runtime import (
    get_hermes_repo, hermes_python_prefix, hermes_subprocess_env, resolve_hermes_python,
)
from core.runtime_descriptor import get_runtime
from wizard.hermes_config import has_hermes_auth_profile
from wizard.providers import effective_auth_mode_for, get_provider, hermes_id_for

router = APIRouter(tags=["provider-auth"])
INTEGRATED = {"nous", "openai-codex", "minimax-oauth", "xai-oauth", "openrouter"}
ACTIVE = {"starting", "pending"}
MESSAGES = {
    "provider_error": (
        "El proveedor no pudo completar el acceso. Comprueba tu conexión y los permisos de "
        "tu cuenta, y vuelve a intentarlo."
    ),
    "unsupported_runtime": (
        "Esta instalación de Hermes no ofrece el flujo gráfico necesario. Revisa su "
        "instalación y compatibilidad con Iris."
    ),
    "device_disabled": (
        "Activa el inicio de sesión por código de dispositivo en la seguridad de tu cuenta "
        "ChatGPT y vuelve a intentarlo."
    ),
    "expired": "El tiempo para autorizar terminó. Pulsa Conectar para generar un enlace nuevo.",
    "denied": "La autorización fue rechazada. Puedes volver a intentarlo.",
    "cancelled": "Inicio de sesión cancelado.",
    "external_required": (
        "No se encontró una sesión compatible. Este proveedor todavía necesita su "
        "herramienta oficial para iniciar sesión."
    ),
    "not_installed": "Instala Hermes desde el wizard antes de conectar tu cuenta.",
    "not_persisted": (
        "Hermes terminó, pero no se encontró la credencial del proveedor. Vuelve a intentarlo."
    ),
}
# The packaged runtime cannot install or replace Hermes; only Iris repairs it.
PACKAGED_MESSAGES = {
    "unsupported_runtime": (
        "La versión del motor incluida en Iris no ofrece este acceso. Usa una API key "
        "o espera una actualización de Iris."
    ),
    "not_installed": "El motor incluido no está disponible. Repara Iris para conectar tu cuenta.",
}
EXTERNAL_HINTS = {
    "anthropic": (
        "Hermes todavía delega el acceso de Claude a su herramienta de autenticación. "
        "Puedes usar una sesión ya configurada en Hermes o elegir API key en esta pantalla."
    ),
    "qwen-oauth": (
        "Esta versión de Hermes reutiliza una sesión de Qwen CLI. Si ya iniciaste sesión "
        "allí, pulsa Comprobar sesión. También puedes elegir Alibaba (Qwen Cloud) con API "
        "key."
    ),
}


@dataclass
class Login:
    id: str
    provider: str
    slug: str
    status: str = "starting"
    flow: str = ""
    url: str = ""
    user_code: str = ""
    code: str = ""
    created: float = field(default_factory=time.time)
    expires_at: float = 0
    process: asyncio.subprocess.Process | None = None
    task: asyncio.Task | None = None

    def public(self):
        result = {
            "session_id": self.id,
            "provider": self.provider,
            "status": self.status,
            "flow": self.flow,
            "expires_at": self.expires_at,
            "code": self.code,
        }
        if self.status == "pending":
            result.update(url=self.url, user_code=self.user_code)
        if self.status == "external":
            result["message"] = EXTERNAL_HINTS.get(self.slug, MESSAGES["external_required"])
            result["can_check_existing"] = True
        elif self.code:
            result["message"] = _message(self.code)
        return result


def _message(code: str) -> str:
    if code in PACKAGED_MESSAGES and get_runtime().packaged:
        return PACKAGED_MESSAGES[code]
    return MESSAGES.get(code, MESSAGES["provider_error"])


class ProviderAuth:
    def __init__(self):
        self.sessions: dict[str, Login] = {}
        self.lock = asyncio.Lock()

    async def start(self, provider: str, existing: bool = False):
        if not get_provider(provider) or effective_auth_mode_for(provider) not in {
            "oauth",
            "api+oauth",
        }:
            raise HTTPException(400, "Elige un proveedor que admita inicio de sesión.")
        slug = hermes_id_for(provider, use_oauth=True)
        async with self.lock:
            for session in self.sessions.values():
                if session.status in ACTIVE:
                    if session.provider == provider:
                        return session.public()
                    raise HTTPException(
                        409, "Cancela el acceso pendiente antes de cambiar de proveedor."
                    )
            self.sessions = {
                k: v for k, v in self.sessions.items() if time.time() - v.created < 1200
            }
            while len(self.sessions) >= 16:
                self.sessions.pop(next(iter(self.sessions)))
            session = Login(secrets.token_urlsafe(24), provider, slug)
            self.sessions[session.id] = session
            if slug not in INTEGRATED:
                if has_hermes_auth_profile(slug, auth_type="oauth"):
                    session.status = "approved"
                elif existing and slug == "qwen-oauth":
                    session.task = asyncio.create_task(self._run(session, "existing"))
                else:
                    session.status = "external"
            else:
                session.task = asyncio.create_task(self._run(session, "login"))
            return session.public()

    def get(self, session_id: str):
        session = self.sessions.get(session_id)
        if session is None:
            raise HTTPException(404, "Este acceso ya no está disponible. Vuelve a conectar.")
        return session

    async def _stop_process(self, session: Login):
        process = session.process
        if process:
            if process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
            await process.wait()
            if process.stdin:
                process.stdin.close()

    async def cancel(self, session_id: str):
        async with self.lock:
            session = self.get(session_id)
            if session.status in ACTIVE:
                # Kill and await before returning: no native poller can persist
                # credentials after cancellation has been acknowledged.
                if session.task:
                    session.task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await session.task
                session.status, session.code = "cancelled", "cancelled"
                session.url = session.user_code = ""
            return session.public()

    async def close(self):
        for key in list(self.sessions):
            await self.cancel(key)

    async def _run(self, session: Login, mode: str):
        try:
            python, repo = resolve_hermes_python(), get_hermes_repo()
            prefix = hermes_python_prefix(python) if python else None
            if not prefix or not repo.is_dir():
                session.status, session.code = "error", "not_installed"
                return
            if "-B" not in prefix[1:] and not any(flag.startswith("pycache_prefix=") for flag in prefix[1:]):
                prefix.append("-B")
            options = (
                {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
                if os.name == "nt"
                else {}
            )
            # Shield process creation so cancellation cannot orphan a child
            # between CreateProcess and assignment to session.process.
            spawning = asyncio.create_task(
                asyncio.create_subprocess_exec(
                    *prefix,
                    "-u",
                    str(Path(__file__).with_name("hermes_auth_worker.py")),
                    session.slug,
                    mode,
                    str(repo),
                    cwd=str(repo),
                    env=hermes_subprocess_env(),
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                    **options,
                )
            )
            try:
                session.process = await asyncio.shield(spawning)
            except asyncio.CancelledError:
                session.process = await spawning
                raise
            deadline = time.monotonic() + 90
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError
                line = await asyncio.wait_for(session.process.stdout.readline(), remaining)
                if not line:
                    session.status, session.code = "error", "provider_error"
                    break
                message = json.loads(line)
                status = message.get("status")
                if status == "pending":
                    url = str(message.get("url") or "")
                    parsed = urlsplit(url)
                    if (
                        parsed.scheme != "https"
                        or not parsed.hostname
                        or parsed.username
                        or parsed.password
                        or len(url) > 8192
                        or any(ord(c) < 32 for c in url)
                    ):
                        raise ValueError("Invalid verification URL")
                    seconds = min(900, max(1, int(message.get("expires_in", 300))))
                    deadline = time.monotonic() + seconds
                    session.status, session.flow, session.url = (
                        "pending",
                        str(message.get("flow")),
                        url,
                    )
                    session.user_code = str(message.get("user_code") or "")[:256]
                    session.expires_at = time.time() + seconds
                elif status == "approved":
                    session.status = (
                        "approved"
                        if has_hermes_auth_profile(session.slug, auth_type="oauth")
                        else "error"
                    )
                    if session.status == "error":
                        session.code = "not_persisted"
                    break
                elif status == "error":
                    session.status = "error"
                    session.code = (
                        message.get("code") if message.get("code") in MESSAGES else "provider_error"
                    )
                    break
                else:
                    raise ValueError("Invalid authentication event")
        except TimeoutError:
            session.status, session.code = "error", "expired"
        except asyncio.CancelledError:
            raise
        except Exception:
            session.status, session.code = "error", "provider_error"
        finally:
            await self._stop_process(session)


provider_auth = ProviderAuth()


class StartLogin(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    existing: bool = False


def _local(request):
    from wizard.app import _enforce_local_browser_action

    _enforce_local_browser_action(request, "La autenticación del proveedor")


def _response(payload):
    return JSONResponse(payload, headers={"Cache-Control": "no-store"})


@router.post("/api/provider-auth/sessions")
async def start_login(body: StartLogin, request: Request):
    _local(request)
    return _response(await provider_auth.start(body.provider, body.existing))


@router.get("/api/provider-auth/sessions/{session_id}")
async def poll_login(session_id: str, request: Request):
    _local(request)
    return _response(provider_auth.get(session_id).public())


@router.delete("/api/provider-auth/sessions/{session_id}")
async def cancel_login(session_id: str, request: Request):
    _local(request)
    return _response(await provider_auth.cancel(session_id))
