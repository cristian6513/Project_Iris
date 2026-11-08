"""F10.2: Core's side of the Google worker (``core/google_worker.py``): one private child per job.

* The child runs in Hermes' Python (``hermes_python_prefix`` + ``hermes_subprocess_env``), with no
  console and stderr discarded: nothing it prints can carry a secret into Iris' logs.
* The request, with the OAuth client and the profile's protection key, travels on stdin, never
  on the command line or in the environment. stdin stays open while the job is wanted; closing
  it is the cancel, and the child ends at once.
* Creating the process is shielded: cancelled meanwhile, it is still created, then collected.
  Closing a job always waits for the child to be gone (EOF, a short wait, then kill).
* Lines are read with a size limit and validated (``parse_rpc``); a line that breaks the
  contract ends the job as ``provider_unavailable``. Each job has one deadline.
* A read round (F10.3) streams its lines to the service as they come; whatever happens, the
  child is collected when the round ends or its consumer stops reading.

The OAuth client is a Desktop ("installed") client of Google's own endpoints; its file is
validated here and the worker uses Google's endpoints as constants whatever the file says.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Callable

from core.google_connections import AuthorizationOutcome, GoogleServiceError, RefreshOutcome
from core.google_contracts import (
    MAX_RPC_LINE_BYTES,
    RpcError,
    decode_credential,
    encode_credential,
    parse_rpc,
    rpc_line,
)

WORKER = Path(__file__).with_name("google_worker.py")
START_TIMEOUT_S = 60.0  # starting Hermes' Python and its libraries, until the consent URL is ready
JOB_TIMEOUT_S = 60.0  # a renewal or a revocation, whole
READ_METHODS = frozenset({"sync.calendar_list", "sync.calendar_events", "sync.gmail", "read.gmail_body"})
CLOSE_GRACE_S = 3.0

_GOOGLE_AUTH_URIS = {"https://accounts.google.com/o/oauth2/auth", "https://accounts.google.com/o/oauth2/v2/auth"}
_GOOGLE_TOKEN_URIS = {"https://oauth2.googleapis.com/token", "https://accounts.google.com/o/oauth2/token"}


@dataclass(frozen=True)
class GoogleClientConfig:
    """A Desktop OAuth client of Google. The secret of an installed app is not a real secret, but it
    is still kept out of logs and the UI."""

    client_id: str
    client_secret: str

    def __repr__(self) -> str:
        return f"GoogleClientConfig(client_id={self.client_id[:12]}…)"

    @classmethod
    def from_document(cls, document: Any) -> GoogleClientConfig:
        installed = document.get("installed") if isinstance(document, dict) else None
        if not isinstance(installed, dict) or set(document) != {"installed"}:
            raise ValueError("El cliente OAuth debe ser de tipo escritorio («installed»).")
        client_id, secret = installed.get("client_id"), installed.get("client_secret")
        if (not isinstance(client_id, str) or re.fullmatch(r'[A-Za-z0-9_-]+\.apps\.googleusercontent\.com', client_id) is None
                or len(client_id) > 256 or not isinstance(secret, str) or not 0 < len(secret) <= 256):
            raise ValueError("El cliente OAuth no tiene un identificador de Google válido.")
        if any(ord(char) < 33 or ord(char) == 127 for char in secret):
            raise ValueError("El cliente OAuth no tiene un secreto válido.")
        if installed.get("auth_uri", next(iter(_GOOGLE_AUTH_URIS))) not in _GOOGLE_AUTH_URIS:
            raise ValueError("El cliente OAuth apunta a otro servidor de autorización.")
        if installed.get("token_uri", next(iter(_GOOGLE_TOKEN_URIS))) not in _GOOGLE_TOKEN_URIS:
            raise ValueError("El cliente OAuth apunta a otro servidor de tokens.")
        return cls(client_id=client_id, client_secret=secret)

    @classmethod
    def from_file(cls, path: Path) -> GoogleClientConfig:
        raw = Path(path).read_bytes()
        if len(raw) > 64 * 1024:
            raise ValueError("El archivo del cliente OAuth es demasiado grande.")
        return cls.from_document(json.loads(raw.decode("utf-8")))

    def private(self) -> dict[str, str]:
        return {"client_id": self.client_id, "client_secret": self.client_secret}


def default_command() -> list[str] | None:
    """Hermes' Python running the worker by its path, or None when Hermes is not available."""
    from core.hermes_runtime import hermes_python_prefix

    prefix = hermes_python_prefix()
    if not prefix:
        return None
    if "-B" not in prefix[1:] and not any(flag.startswith("pycache_prefix=") for flag in prefix[1:]):
        prefix = [*prefix, "-B"]
    return [*prefix, str(WORKER)]


def default_env() -> dict[str, str]:
    from core.hermes_runtime import hermes_subprocess_env

    return hermes_subprocess_env()


async def _finish_cleanup(task: asyncio.Task) -> None:
    """Keep ownership until cleanup finishes, even if its caller is cancelled again.

    Shielding once is insufficient: another cancel interrupts the shielded await while the
    cleanup keeps running. Remember that cancellation and deliver it only after collection.
    """
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    task.result()
    if cancelled:
        raise asyncio.CancelledError


class _Job:
    """One child process and its correlated lines."""

    def __init__(self, process: asyncio.subprocess.Process, job_id: str) -> None:
        self.process = process
        self.id = job_id
        self._closed = False
        self._closing: asyncio.Task | None = None

    async def send(self, message: dict[str, Any]) -> None:
        self.process.stdin.write(rpc_line(message))
        await self.process.stdin.drain()

    async def next_event(self, timeout: float | None) -> dict[str, Any]:
        try:
            line = await asyncio.wait_for(self.process.stdout.readline(), timeout)
        except (ValueError, asyncio.LimitOverrunError) as exc:  # a line over the limit
            raise GoogleServiceError("provider_unavailable") from exc
        if not line:
            raise GoogleServiceError("provider_unavailable")  # the worker ended without answering
        try:
            message = parse_rpc(line)
        except RpcError as exc:
            raise GoogleServiceError("provider_unavailable") from exc
        if message.get("id") != self.id or "event" not in message:
            raise GoogleServiceError("provider_unavailable")
        return message

    async def close(self) -> None:
        """EOF (the worker's cancel), a short grace, then kill; returns once the child is gone."""
        if self._closing is None:
            self._closing = asyncio.ensure_future(self._close())
        await _finish_cleanup(self._closing)

    async def _close(self) -> None:
        process = self.process
        if process.stdin is not None and not process.stdin.is_closing():
            process.stdin.close()
        try:
            await asyncio.wait_for(process.wait(), CLOSE_GRACE_S)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await process.wait()


class _AuthorizationHandle:
    def __init__(self, job: _Job, url: str, expires_at: float) -> None:
        self._job = job
        self.url = url
        self.expires_at = expires_at

    async def result(self) -> AuthorizationOutcome:
        try:
            message = await self._job.next_event(None)  # the service owns this deadline
        except GoogleServiceError as exc:
            return AuthorizationOutcome(error=exc.code)
        if message["event"] == "error":
            return AuthorizationOutcome(error=str(message.get("public", {}).get("code") or "provider_unavailable"))
        private, public = message.get("private", {}), message.get("public", {})
        identity, credential = private.get("identity"), private.get("credential")
        granted = public.get("granted_scopes")
        if message["event"] != "authorization_candidate" or not identity or not credential or not isinstance(granted, list):
            return AuthorizationOutcome(error="provider_unavailable")
        return AuthorizationOutcome(
            client_id=identity["client_id"], issuer=identity["issuer"], subject=identity["subject"],
            email=identity.get("email", ""), granted_scopes=tuple(str(scope) for scope in granted),
            credential=decode_credential(credential),
        )

    async def close(self) -> None:
        await self._job.close()


class GoogleWorkerClient:
    """The service's worker (``core.google_connections.GoogleWorker``), over private child processes."""

    def __init__(
        self,
        *,
        client: GoogleClientConfig | None,
        command: Callable[[], list[str] | None] = default_command,
        env: Callable[[], dict[str, str]] = default_env,
        authorization_timeout_s: float = 600.0,
        start_timeout_s: float = START_TIMEOUT_S,
        job_timeout_s: float = JOB_TIMEOUT_S,
    ) -> None:
        self._client = client
        self._command = command
        self._env = env
        self.authorization_timeout_s = authorization_timeout_s
        self.start_timeout_s = start_timeout_s
        self.job_timeout_s = job_timeout_s
        self._serial = 0

    @property
    def available(self) -> bool:
        return self._client is not None and self._command() is not None

    def configure_client(self, client: GoogleClientConfig | None) -> None:
        """Called only by the owning service with no connected accounts or jobs."""
        self._client = client

    async def _spawn(self) -> asyncio.subprocess.Process:
        command = self._command()
        if not command:
            raise GoogleServiceError("client_unavailable")
        options = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if os.name == "nt" else {}
        spawning = asyncio.ensure_future(asyncio.create_subprocess_exec(
            *command, cwd=str(WORKER.parent), env=self._env(), stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, limit=MAX_RPC_LINE_BYTES + 1,
            **options,
        ))
        try:
            return await asyncio.shield(spawning)
        except asyncio.CancelledError:
            # A process being created cannot be interrupted: let it exist, then collect it.
            async def collect() -> None:
                process = await spawning
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                await process.wait()

            await _finish_cleanup(asyncio.ensure_future(collect()))
            raise

    async def _start(self, method: str, **message: Any) -> _Job:
        self._serial += 1
        job = _Job(await self._spawn(), f"job{self._serial}")
        try:
            await job.send({"id": job.id, "method": method, **message})
        except BaseException:
            await job.close()
            raise
        return job

    def _private(self, protection: bytes, **extra: Any) -> dict[str, Any]:
        if self._client is None:
            raise GoogleServiceError("client_unavailable")
        return {"client": self._client.private(), "protection": base64.b64encode(protection).decode("ascii"), **extra}

    async def probe(self) -> dict[str, Any]:
        job = await self._start("probe")
        try:
            message = await job.next_event(self.start_timeout_s)
            if message["event"] != "result":
                raise GoogleServiceError("provider_unavailable")
            return message.get("public", {})
        finally:
            await job.close()

    async def begin_authorization(self, *, session_id: str, scopes: tuple[str, ...],
                                  protection: bytes) -> _AuthorizationHandle:
        job = await self._start(
            "auth.begin", session_id=session_id,
            params={"scopes": list(scopes), "timeout_s": self.authorization_timeout_s},
            private=self._private(protection),
        )
        try:
            message = await job.next_event(self.start_timeout_s)  # the listener is ready first
            if message["event"] == "error":
                raise GoogleServiceError(str(message.get("public", {}).get("code") or "provider_unavailable"))
            public = message.get("public", {})
            if message["event"] != "authorization_required" or not isinstance(public.get("url"), str):
                raise GoogleServiceError("provider_unavailable")
            return _AuthorizationHandle(job, public["url"], float(public.get("expires_at") or 0))
        except BaseException:
            await job.close()
            raise

    async def _credential_job(self, method: str, credential: bytes, protection: bytes) -> dict[str, Any]:
        job = await self._start(method, private=self._private(protection, credential=encode_credential(credential)))
        try:
            return await job.next_event(self.job_timeout_s)
        except TimeoutError:
            raise GoogleServiceError("provider_unavailable") from None
        finally:
            await job.close()

    async def refresh(self, credential: bytes, *, protection: bytes) -> RefreshOutcome:
        try:
            message = await self._credential_job("credential.refresh", credential, protection)
        except GoogleServiceError as exc:
            return RefreshOutcome(error=exc.code)
        if message["event"] == "error":
            return RefreshOutcome(error=str(message.get("public", {}).get("code") or "provider_unavailable"))
        renewed = message.get("private", {}).get("credential")
        if message["event"] != "refreshed" or not renewed:
            return RefreshOutcome(error="provider_unavailable")
        return RefreshOutcome(credential=decode_credential(renewed))

    async def revoke(self, credential: bytes, *, protection: bytes) -> str:
        try:
            message = await self._credential_job("credential.revoke", credential, protection)
        except GoogleServiceError:
            return "unknown"  # the job may have reached Google before it failed
        if message["event"] == "error":
            code = message.get("public", {}).get("code")
            return "failed" if code in ("reauth_required", "invalid_request", "client_unavailable") else "unknown"
        outcome = message.get("public", {}).get("outcome")
        return outcome if outcome in ("confirmed", "failed", "unknown") else "unknown"

    async def read_round(self, method: str, credential: bytes, *, protection: bytes, params: dict[str, Any],
                         timeout: float) -> AsyncIterator[dict[str, Any]]:
        """One read round's lines, in order, until its ``result`` or ``error`` (one deadline)."""
        if method not in READ_METHODS:
            raise GoogleServiceError("invalid_request")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        job = await self._start(method, params=params,
                                private=self._private(protection, credential=encode_credential(credential)))
        try:
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise GoogleServiceError("provider_unavailable")
                try:
                    message = await job.next_event(remaining)
                except TimeoutError:
                    raise GoogleServiceError("provider_unavailable") from None
                yield message
                if message["event"] in ("result", "error"):
                    return
        finally:
            await job.close()


# ── The OAuth client and the service Core runs (F10.4) ─────────────────

CLIENT_ENV = "IRIS_GOOGLE_OAUTH_CLIENT"
# Reserved for a future verified Iris client. Personal configuration is the released path.
ALLOW_SHARED_GOOGLE_CLIENT = False


def client_config_path() -> Path | None:
    """Where the developer's Desktop OAuth client is read from.

    Packaged: ``<installation>/google/oauth_client.json``, part of the installation (F10.5), outside
    the web root. In development only: the file named by ``IRIS_GOOGLE_OAUTH_CLIENT``, kept outside
    the repository. Never a file found by searching, never the person's own downloads."""
    from core.runtime_descriptor import get_runtime

    runtime = get_runtime()
    if runtime.packaged:
        return runtime.base / "google" / "oauth_client.json" if ALLOW_SHARED_GOOGLE_CLIENT else None
    override = os.environ.get(CLIENT_ENV, "").strip()
    return Path(override) if override else None


def load_client_config() -> GoogleClientConfig | None:
    """The validated client, or None (the connections then say they are not available here)."""
    import logging

    from core.runtime_descriptor import get_runtime

    path = client_config_path()
    if path is None or not path.is_file():
        return None
    resolved = path.resolve()
    if resolved.is_relative_to(get_runtime().assets_root.resolve()):
        logging.getLogger(__name__).warning("Google OAuth client refused: it is inside the web root")
        return None  # anything there is served over HTTP
    try:
        return GoogleClientConfig.from_file(resolved)
    except (OSError, ValueError, UnicodeDecodeError):
        logging.getLogger(__name__).warning("Google OAuth client refused: not a valid Desktop client")
        return None


def create_google_service(home: Path | None = None):
    """The profile's connections service with its real worker. Dormant: nothing starts until asked."""
    from core.google_connections import GoogleConnectionsService

    if home is None:
        from core.config import get_iris_home

        home = Path(get_iris_home())
    client = load_client_config()
    from core.runtime_descriptor import get_runtime
    source = ('shared' if get_runtime().packaged else 'development') if client else 'none'
    return GoogleConnectionsService(home, worker=GoogleWorkerClient(client=client),
                                    client_configurable=True, client_source=source)
