"""Exercise Iris' adapter against installed Hermes with synthetic provider replies.

Run with Iris Python. Child uses Hermes Python and an empty temporary home;
network access and native credential writers are replaced before login starts.
No account is authenticated and no real credential is read or written.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import importlib.util
import io
import json
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]


async def verify(repo: Path):
    sys.path.insert(0, str(repo))
    spec = importlib.util.spec_from_file_location(
        "iris_auth_worker", ROOT / "wizard/hermes_auth_worker.py"
    )
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    from agent import credential_pool
    from hermes_cli import auth as native
    from hermes_cli import auth_commands, auth_device_flow, auth_openrouter
    from hermes_cli.web_routers import oauth

    def offline(*_args, **_kwargs):
        raise AssertionError("Network is disabled for this contract check")

    report = {}
    with patch.object(socket.socket, "connect", offline):
        for provider in ["nous", "openai-codex", "minimax-oauth", "xai-oauth"]:
            events, saved = [], []
            device = {
                "device_code": "fake-device",
                "user_code": "FAKE-1234",
                "verification_uri": "https://example.test/authorize",
                "verification_uri_complete": "https://example.test/authorize?user_code=FAKE-1234",
                "expires_in": 600,
                "interval": 2,
                "expired_in": 600,
            }
            token = {
                "access_token": "synthetic-access",
                "refresh_token": "synthetic-refresh",
                "expires_in": 3600,
                "expired_in": 3600,
            }
            with contextlib.ExitStack() as stack:

                def mock(obj, name, **kwargs):
                    return stack.enter_context(patch.object(obj, name, **kwargs))

                if provider == "nous":
                    from hermes_cli import anon_auth

                    mock(anon_auth, "guest_enabled", return_value=False)
                    mock(native, "_request_device_code", return_value=device)
                    mock(native, "_poll_for_token", return_value=token)
                    mock(
                        native,
                        "refresh_nous_oauth_from_state",
                        side_effect=lambda state, **kw: state,
                    )
                    mock(
                        native,
                        "persist_nous_credentials",
                        side_effect=lambda *a, **kw: saved.append("nous"),
                    )
                    mock(anon_auth, "settle_after_upgrade", return_value={})
                elif provider == "openai-codex":
                    mock(
                        oauth,
                        "_codex_request_user_code",
                        return_value={**device, "device_auth_id": "fake"},
                    )
                    mock(
                        oauth,
                        "_codex_poll_authorization",
                        return_value={"authorization_code": "fake"},
                    )
                    mock(oauth, "_codex_exchange_tokens", return_value=token)
                    mock(
                        native,
                        "_save_codex_tokens",
                        side_effect=lambda *a, **kw: saved.append("codex"),
                    )
                elif provider == "minimax-oauth":
                    mock(native, "_minimax_request_user_code", return_value=device)
                    mock(native, "_minimax_poll_token", return_value=token)
                    mock(
                        native,
                        "_minimax_save_auth_state",
                        side_effect=lambda *a, **kw: saved.append("minimax"),
                    )
                else:
                    mock(native, "_xai_oauth_request_device_code", return_value=device)
                    mock(
                        native,
                        "_xai_oauth_discovery",
                        return_value={"token_endpoint": "https://example.test/token"},
                    )
                    mock(native, "_xai_oauth_poll_device_token", return_value=token)
                    mock(
                        native,
                        "_save_xai_oauth_tokens",
                        side_effect=lambda *a, **kw: saved.append("xai"),
                    )
                    mock(native, "mark_provider_active_if_unset", return_value=None)
                    mock(native, "unsuppress_credential_source", return_value=None)
                await worker.device_login(provider, events.append)
            assert events[0]["status"] == "pending", (provider, events)
            assert events[-1]["status"] == "approved", (provider, events)
            assert len(saved) == 1, (provider, saved)
            assert "synthetic-access" not in json.dumps(events)
            assert "synthetic-refresh" not in json.dumps(events)
            report[provider] = {"native_start_poll_save": True, "display_only": True}

        events = []
        pool = MagicMock()
        with (
            patch.object(
                auth_device_flow,
                "_serve_loopback_callback",
                return_value={"code": "synthetic-code"},
            ),
            patch.object(
                auth_openrouter, "_openrouter_exchange_code", return_value="synthetic-api-key"
            ),
            patch.object(credential_pool, "load_pool", return_value=pool),
            patch.object(auth_commands, "_unsuppress_provider_sources"),
        ):
            worker.openrouter_login(events.append)
        entry = pool.add_entry.call_args.args[0]
        assert entry.auth_type == "api_key"
        assert entry.source == "manual:openrouter_pkce"
        assert events[0]["flow"] == "browser"
        assert "callback_url=" in events[0]["url"]
        assert events[-1]["status"] == "approved"
        assert "synthetic-api-key" not in json.dumps(events)
        report["openrouter"] = {"native_callback_and_pool_schema": True, "display_only": True}
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--child", type=Path)
    args = parser.parse_args()
    if args.child:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            report = asyncio.run(verify(args.child))
        print(json.dumps(report))
        return
    sys.path.insert(0, str(ROOT))
    from core.hermes_runtime import get_hermes_repo, hermes_subprocess_env, resolve_hermes_python

    python = resolve_hermes_python()
    if not python:
        raise SystemExit("Hermes runtime is not installed")
    with tempfile.TemporaryDirectory(prefix="iris-provider-auth-") as directory:
        env = {
            k: v
            for k, v in hermes_subprocess_env().items()
            if not any(
                part in k.upper() for part in ("TOKEN", "API_KEY", "SECRET", "HERMES", "NOUS")
            )
        }
        env.update(HERMES_HOME=directory, PYTHONDONTWRITEBYTECODE="1")
        result = subprocess.run(
            [python, "-B", str(Path(__file__).resolve()), "--child", str(get_hermes_repo())],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode:
            raise SystemExit(result.stderr or result.stdout)
        report = json.loads(result.stdout)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
