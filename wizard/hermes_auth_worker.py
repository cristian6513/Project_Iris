"""Private JSON-lines adapter, executed by Hermes' Python, never Iris' Python.

Hermes owns token exchange, refresh and credential persistence. Only display
fields cross stdout; native logs and exceptions never reach the browser.
No gateway, scheduler, microphone or interactive terminal is started here.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import secrets
import sys
import threading
import time
from types import SimpleNamespace
from urllib.parse import urlencode

DEVICE_PROVIDERS = {"nous", "openai-codex", "minimax-oauth", "xai-oauth"}


def error_code(error: BaseException) -> str:
    # Classify internally; upstream HTTP bodies can contain credentials.
    detail = str(getattr(error, "detail", error)).lower()
    if isinstance(error, (ImportError, AttributeError, KeyError)):
        return "unsupported_runtime"
    if "device" in detail and ("enable" in detail or "disabled" in detail):
        return "device_disabled"
    if "expir" in detail or "timeout" in detail or "timed out" in detail:
        return "expired"
    if "denied" in detail or "declined" in detail or "access_denied" in detail:
        return "denied"
    return "provider_error"


async def device_login(provider, emit):
    from hermes_cli.web_routers.oauth import _start_device_code_flow
    from hermes_cli.web_server_oauth import _oauth_sessions, _oauth_sessions_lock

    started = await _start_device_code_flow(provider)
    emit(
        {
            "status": "pending",
            "flow": "device_code",
            "user_code": started["user_code"],
            "url": started["verification_url"],
            "expires_in": started["expires_in"],
        }
    )
    deadline = time.monotonic() + min(900, max(1, int(started["expires_in"])))
    while time.monotonic() < deadline:
        with _oauth_sessions_lock:
            state = dict(_oauth_sessions.get(started["session_id"], {}))
        status = state.get("status", "error")
        if status != "pending":
            if status == "approved":
                emit({"status": "approved"})
            else:
                code = (
                    status
                    if status in {"denied", "expired", "cancelled"}
                    else error_code(RuntimeError(state.get("error_message", "")))
                )
                emit({"status": "error", "code": code})
            return
        await asyncio.sleep(0.25)
    emit({"status": "error", "code": "expired"})


def openrouter_login(emit):
    # Reuse Hermes' PKCE, callback handler, exchange and credential-pool schema.
    # The nonce in the callback path is Hermes' CSRF binding for OpenRouter.
    from agent.credential_pool import PooledCredential, load_pool
    from hermes_cli.auth_commands import (
        _OAUTH_ADD_SPECS,
        _provider_base_url,
        _unsuppress_provider_sources,
    )
    from hermes_cli.auth_constants import OPENROUTER_AUTH_URL, _openrouter_err
    from hermes_cli.auth_device_flow import (
        _bind_loopback_callback_server,
        _make_loopback_callback_handler,
        _pkce_code_challenge,
        _pkce_code_verifier,
        _serve_loopback_callback,
    )
    from hermes_cli.auth_openrouter import _openrouter_exchange_code

    verifier = _pkce_code_verifier()
    path = "/callback/" + secrets.token_urlsafe(24)
    handler, result = _make_loopback_callback_handler(path, display_name="Iris — OpenRouter")
    server = _bind_loopback_callback_server(
        "127.0.0.1",
        0,
        handler,
        err=_openrouter_err,
        bind_failed_code="openrouter_callback_bind_failed",
    )
    try:
        url = (
            OPENROUTER_AUTH_URL
            + "?"
            + urlencode(
                {
                    "callback_url": f"http://127.0.0.1:{server.server_address[1]}{path}",
                    "code_challenge": _pkce_code_challenge(verifier),
                    "code_challenge_method": "S256",
                }
            )
        )
        emit({"status": "pending", "flow": "browser", "url": url, "expires_in": 300})
        callback = _serve_loopback_callback(
            server,
            result,
            timeout_seconds=300,
            err=_openrouter_err,
            timeout_code="openrouter_callback_timeout",
        )
    finally:
        server.server_close()
    if callback.get("error"):
        emit({"status": "error", "code": "denied"})
        return
    if not callback.get("code"):
        raise ValueError("Missing authorization code")
    token = _openrouter_exchange_code(callback["code"], verifier)
    spec = _OAUTH_ADD_SPECS["openrouter"]
    pool = load_pool("openrouter")
    pool.add_entry(
        PooledCredential(
            provider="openrouter",
            id=secrets.token_hex(6),
            label="Iris — OpenRouter",
            auth_type=spec.auth_type,
            source=spec.source,
            priority=0,
            access_token=token,
            base_url=_provider_base_url("openrouter"),
        )
    )
    _unsuppress_provider_sources("openrouter")
    emit({"status": "approved"})


def existing_login(provider, emit):
    # Qwen's supported path imports its CLI credential; it does not start a
    # login and cannot ask for input. Other external flows remain explicit.
    if provider == "qwen-oauth":
        from hermes_cli.auth_commands import auth_add_command

        auth_add_command(SimpleNamespace(provider=provider, auth_type="oauth", label="Iris — Qwen"))
        emit({"status": "approved"})
    else:
        emit({"status": "error", "code": "external_required"})


def _stdin_pipe():
    """wizard.stdin_pipe, or the copy next to this script. The packaged Hermes interpreter runs
    isolated (python311._pth): neither Iris nor this script's folder is on its sys.path, so the
    sibling is loaded by file path, never by adding the wizard folder to Hermes' imports."""
    try:
        from wizard import stdin_pipe
    except ImportError:
        import importlib.util
        from pathlib import Path

        spec = importlib.util.spec_from_file_location("iris_stdin_pipe", Path(__file__).with_name("stdin_pipe.py"))
        if spec is None or spec.loader is None:
            raise
        stdin_pipe = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(stdin_pipe)
    return stdin_pipe


def main():
    provider, mode, repo = sys.argv[1:4]
    sys.path.insert(0, repo)
    output = sys.stdout

    def emit(payload):
        output.write(json.dumps(payload, ensure_ascii=True) + "\n")
        output.flush()

    # EOF means the owning wizard died. In-flight native polling threads must
    # not survive and later persist credentials for an abandoned operation.
    # Polled, not read: a pending stdin read can freeze DLL loads on Windows.
    # Loaded before the thread starts: an import error there would only kill the watchdog,
    # silently (the packaged Hermes interpreter cannot import the sibling by name).
    wait_for_eof = _stdin_pipe().wait_for_eof

    def watch_parent():
        wait_for_eof()
        os._exit(1)

    threading.Thread(target=watch_parent, daemon=True).start()
    with contextlib.redirect_stdout(sys.stderr):
        try:
            if mode == "existing":
                existing_login(provider, emit)
            elif provider in DEVICE_PROVIDERS:
                asyncio.run(device_login(provider, emit))
            elif provider == "openrouter":
                openrouter_login(emit)
            else:
                emit({"status": "error", "code": "external_required"})
        except (Exception, SystemExit) as exc:
            emit({"status": "error", "code": error_code(exc)})


if __name__ == "__main__":
    main()
