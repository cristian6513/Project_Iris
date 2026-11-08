"""Explicit post-auth model discovery. Draft keys stay in memory, never in a catalog cache."""
from __future__ import annotations

import asyncio
import json
import subprocess
from decimal import Decimal, InvalidOperation

import httpx


def _openrouter_browser_key() -> str:
    """PKCE mints a key into Hermes' pool; an unrelated .env key is a different access."""
    from wizard.hermes_config import get_hermes_home

    try:
        data = json.loads((get_hermes_home() / "auth.json").read_text(encoding="utf-8"))
        entries = data.get("credential_pool", {}).get("openrouter", [])
        for entry in reversed(entries):
            if (isinstance(entry, dict) and entry.get("auth_type") == "api_key"
                    and entry.get("source") == "manual:openrouter_pkce"):
                token = entry.get("access_token")
                if isinstance(token, str) and token.strip():
                    return token.strip()
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    return ""


def model_rows(data: object, *, openrouter: bool = False) -> list[dict]:
    rows = data.get("data") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise ValueError("invalid_catalog")
    result = {}
    for item in rows:
        if not isinstance(item, dict):
            continue
        model = item.get("id")
        if not isinstance(model, str) or not model.strip() or len(model) > 240:
            continue
        architecture = item.get("architecture")
        modalities = architecture.get("output_modalities") if isinstance(architecture, dict) else None
        if modalities and "text" not in modalities:
            continue
        # This is an agent picker: don't offer media-only or known non-tool models.
        params = item.get("supported_parameters")
        if openrouter and isinstance(params, list) and "tools" not in params:
            continue
        free = False
        pricing = item.get("pricing") if isinstance(item.get("pricing"), dict) else {}
        try:
            free = all(Decimal(str(pricing[k])) == 0 for k in ("prompt", "completion"))
        except (KeyError, InvalidOperation, TypeError, ValueError):
            pass
        free = free or model.endswith((":free", "-free"))
        label = item.get("name") if isinstance(item.get("name"), str) else model
        result[model] = {"value": model, "label": label[:240] + (" · Gratis" if free else ""), "free": free}
    return sorted(result.values(), key=lambda m: (not m["free"], m["value"]))


async def discover_models(*, slug: str, api_key: str = "", use_oauth: bool = False, base_url: str = "") -> dict:
    if slug == "openrouter" and use_oauth:
        api_key = await asyncio.to_thread(_openrouter_browser_key)
        if not api_key:
            return {"status": "error", "code": "missing_oauth"}
        use_oauth = False
        base_url = "https://openrouter.ai/api/v1"
    if not use_oauth and slug in {"openrouter", "opencode-zen", "opencode-go"}:
        # Never redirect a draft key. OpenRouter's user endpoint respects account preferences;
        # Zen's public list does not prove a key is valid or a free promotion is usable by Iris.
        url = base_url.rstrip("/") + ("/models/user" if slug == "openrouter" else "/models")
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
                async with client.stream("GET", url, headers={"Authorization": "Bearer " + api_key}) as response:
                    if response.status_code in {401, 403}:
                        return {"status": "error", "code": "invalid_credentials"}
                    response.raise_for_status()
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > 8_000_000:
                            raise ValueError("catalog_too_large")
            models = model_rows(json.loads(content), openrouter=slug == "openrouter")
            return {"status": "ok", "source": "account" if slug == "openrouter" else "public", "models": models}
        except (httpx.HTTPError, ValueError, TypeError):
            return {"status": "error", "code": "catalog_unavailable"}

    from wizard.provider_sync import _resolve_hermes_python, _hermes_python_probe_command, _hermes_probe_kwargs

    python = _resolve_hermes_python()
    if not python:
        return {"status": "error", "code": "engine_missing"}
    from wizard.provider_models_probe import PROBE_SOURCE
    probe = PROBE_SOURCE
    kwargs = _hermes_probe_kwargs(40)
    timeout = kwargs.pop("timeout")
    for key in ("capture_output", "text", "encoding", "errors"):
        kwargs.pop(key, None)
    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            *_hermes_python_probe_command(python, probe), stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, **kwargs,
        )
        body = json.dumps({"slug": slug, "api_key": api_key, "use_oauth": use_oauth, "base_url": base_url}).encode()
        stdout, _ = await asyncio.wait_for(process.communicate(body), timeout=timeout)
        marker = "IRIS_PROVIDER_MODELS="
        line = next(s[len(marker):] for s in reversed(stdout.decode("utf-8").splitlines()) if s.startswith(marker))
        result = json.loads(line)
        if not isinstance(result, dict):
            raise ValueError("invalid_catalog")
        return result
    except (OSError, ValueError, StopIteration, asyncio.TimeoutError):
        return {"status": "error", "code": "catalog_unavailable"}
    finally:
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()
