"""
IRIS ↔ Hermes Health Check
───────────────────────────

Six end-to-end checks that verify the whole pipeline works: the Hermes
CLI is installed, its config is valid, the api-server is listening, it
responds on its real REST surface (`/health`), the configured provider
has usable credentials, and IRIS's own WebSocket server is up.

Why we don't probe `/v1/models` or `/v1/chat/completions`:
    Hermes' api-server exposes an OpenAI-compatible REST surface at
    ``:8642``, but the only endpoint that returns liveness JSON without
    needing a fully-configured model is ``/health``. The chat endpoints
    require a configured provider AND a Bearer token — probing them
    would either burn the user's tier or return 401 if the wizard
    hasn't finished its sync.

    So instead of pretending Hermes is OpenAI-compatible end-to-end,
    we verify the two things that would actually break the user's
    experience:
      4. api-server responds with JSON liveness on ``/health``.
      5. The configured provider has credentials (API key in env/config
         or a non-expired OAuth profile in Hermes' auth store) — the
         same gate the start-gateway pre-flight uses, so passing here
         is a strong predictor that the first chat will succeed.

Each check is an independent async coroutine that ALWAYS returns a
`HealthResult` dict — it never raises, even on timeouts or network errors.
That way the UI can render all six lights regardless of individual
failures, and the user sees exactly which step is broken.

Public API:
    run_all_checks()            → list[HealthResult]  (sequential, eager)
    iter_checks()               → AsyncIterator[HealthResult]  (for SSE)

HealthResult shape:
    {
        "id":       "cli_in_path" | "config_valid" | ...,
        "label":    "Hermes installed",
        "ok":       bool,
        "status":   "ok" | "fail" | "skip",
        "detail":   "human-readable one-liner, safe to show in UI",
        "hint":     "how to fix it (only when ok=False)",
        "elapsed_ms": int,
    }
"""

from __future__ import annotations

import asyncio
import os
import shutil
import socket
import time
from typing import Any, AsyncIterator

import httpx

from core.config import IRISConfig
from core import runtime_descriptor as runtime_policy
from wizard.hermes_config import (
    get_config_yaml_path,
    get_env_path,
)


# Hermes' default api-server port. Configurable via API_SERVER_PORT
# in ~/.hermes/.env, but the wizard always pre-seeds 8642.
HERMES_DEFAULT_URL = "http://127.0.0.1:8642"
HERMES_DEFAULT_PORT = 8642


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────

def _result(
    id: str,
    label: str,
    ok: bool,
    detail: str,
    *,
    hint: str = "",
    status: str | None = None,
    elapsed_ms: int = 0,
) -> dict[str, Any]:
    """Build a HealthResult dict. `status` defaults to ok/fail based on `ok`."""
    return {
        "id": id,
        "label": label,
        "ok": ok,
        "status": status or ("ok" if ok else "fail"),
        "detail": detail,
        "hint": hint,
        "elapsed_ms": elapsed_ms,
    }


def _skip(id: str, label: str, reason: str) -> dict[str, Any]:
    """Build a skipped HealthResult (e.g. when a dependency check failed)."""
    return _result(id, label, ok=False, status="skip", detail=reason)


def _inactive(id: str, label: str, detail: str, *, hint: str = "", elapsed_ms: int = 0) -> dict[str, Any]:
    """Build a non-fatal result for a configured service that is not running.

    The connection check is available before the final ``Lanzar IRIS`` action,
    so a stopped runtime is useful state rather than a broken configuration.
    Keeping it distinct from ``fail`` prevents the wizard from accusing the
    provider/OAuth setup when only a local process still needs to be started.
    """
    return _result(
        id,
        label,
        ok=False,
        status="inactive",
        detail=detail,
        hint=hint,
        elapsed_ms=elapsed_ms,
    )


async def _tcp_port_open(host: str, port: int, timeout: float = 3.0) -> bool:
    """True iff a TCP connection to host:port succeeds within `timeout`."""
    try:
        fut = asyncio.open_connection(host, port)
        reader, writer = await asyncio.wait_for(fut, timeout=timeout)
        writer.close()
        try:
            await writer.wait_closed()
        except (OSError, ConnectionResetError):
            pass
        return True
    except (asyncio.TimeoutError, OSError, socket.gaierror):
        return False


# ─────────────────────────────────────────────────────────────────────
# Individual checks
# ─────────────────────────────────────────────────────────────────────

async def check_cli_in_path() -> dict[str, Any]:
    """1/6 — `hermes` (or `hermes.exe` on Windows) is on PATH."""
    t0 = time.monotonic()
    runtime = runtime_policy.get_runtime()
    if runtime.hermes:
        from core.hermes_runtime import resolve_hermes_cli
        path = resolve_hermes_cli()
    else:
        path = shutil.which("hermes") or shutil.which("hermes.exe")
    elapsed = int((time.monotonic() - t0) * 1000)
    label = "Motor Hermes Agent incluido" if runtime.packaged else "Hermes CLI instalado"
    if path:
        return _result(
            "cli_in_path",
            label,
            ok=True,
            detail="Runtime incluido de Iris" if runtime.packaged else path,
            elapsed_ms=elapsed,
        )
    return _result(
        "cli_in_path",
        label,
        ok=False,
        detail="El motor incluido no está disponible." if runtime.packaged else "No se encontró el comando `hermes` en PATH.",
        hint="Repara la instalación de Iris." if runtime.packaged else "Instálalo con: irm https://hermes-agent.nousresearch.com/install.ps1 | iex",
        elapsed_ms=elapsed,
    )


async def check_config_valid() -> dict[str, Any]:
    """2/6 — `~/.hermes/config.yaml` exists and has a `model` key set."""
    t0 = time.monotonic()
    path = get_config_yaml_path()

    def done(**kw):
        kw["elapsed_ms"] = int((time.monotonic() - t0) * 1000)
        return _result(id="config_valid", label="Config hermes válida", **kw)

    if not path.exists():
        return done(
            ok=False,
            detail=f"No existe {path}.",
            hint="Guarda la config del wizard (elige proveedor + modelo) o corre /api/sync-hermes.",
        )

    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return done(
            ok=False,
            detail="El archivo existe pero está vacío.",
            hint="Guarda la config del wizard para regenerarlo.",
        )

    try:
        import yaml  # type: ignore
    except ImportError:
        return done(
            ok=False,
            detail="PyYAML no está instalado.",
            hint="Instálalo con: pip install pyyaml",
        )

    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        return done(
            ok=False,
            detail=f"No se pudo parsear config.yaml: {exc}",
            hint="Borra el archivo y vuelve a correr el wizard.",
        )

    if not isinstance(data, dict):
        return done(ok=False, detail="El YAML raíz no es un objeto.", hint="Regenera el archivo.")

    primary = data.get("model")
    if not primary:
        return done(
            ok=False,
            detail="Falta `model` en config.yaml.",
            hint="Elige un proveedor y modelo en el wizard y guarda.",
        )

    if not isinstance(primary, dict):
        return done(
            ok=False,
            detail="`model` usa el formato antiguo y Hermes no puede identificar proveedor y modelo con seguridad.",
            hint="Vuelve a guardar proveedor + modelo desde el wizard para migrar config.yaml.",
        )

    provider = str(primary.get("provider") or "").strip()
    model = str(primary.get("default") or "").strip()
    if not provider or not model:
        return done(
            ok=False,
            detail="El bloque `model` está incompleto: necesita `provider` y `default`.",
            hint="Elige de nuevo proveedor + modelo y guarda la selección.",
        )

    # Show a quick summary of which API keys are present in .env. OAuth does
    # not use provider API keys, so never render "sin API keys" as if it were
    # suspicious when the selected adapter is backed by an OAuth profile.
    env_path = get_env_path()
    env_keys: list[str] = []
    if env_path.exists():
        try:
            for line in env_path.read_text(encoding="utf-8").splitlines():
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                if "_API_KEY" in s and "=" in s:
                    key, _, val = s.partition("=")
                    if val.strip():
                        env_keys.append(key.strip())
        except OSError:
            pass

    try:
        from core.config import _derive_llm_from_hermes

        derived = _derive_llm_from_hermes()
        oauth_selected = bool(derived.get("llm_use_oauth"))
    except Exception:
        oauth_selected = False
    if oauth_selected:
        credential_summary = "OAuth seleccionado"
    elif env_keys:
        credential_summary = f"credencial: {', '.join(env_keys)}"
    else:
        credential_summary = "sin API key"
    return done(
        ok=True,
        detail=f"{provider} / {model} · {credential_summary}",
    )


async def check_gateway_port(
    url: str = HERMES_DEFAULT_URL,
) -> dict[str, Any]:
    """3/6 — The api-server port is accepting TCP connections."""
    runtime = runtime_policy.get_runtime()
    t0 = time.monotonic()
    if runtime.packaged:
        from core.request_security import is_loopback_url
        if not is_loopback_url(url):
            return _result("gateway_port", "API server escuchando", ok=False,
                           detail="El runtime incluido requiere un Gateway local.")
    host, port = "127.0.0.1", HERMES_DEFAULT_PORT
    # Parse host/port out of the URL when possible
    try:
        from urllib.parse import urlparse
        u = urlparse(url)
        host = u.hostname or host
        port = u.port or port
    except Exception:
        pass

    is_open = await _tcp_port_open(host, port, timeout=3.0)
    elapsed = int((time.monotonic() - t0) * 1000)
    if is_open:
        return _result(
            "gateway_port",
            "API server escuchando",
            ok=True,
            detail=f"{host}:{port} responde",
            elapsed_ms=elapsed,
        )
    return _inactive(
        "gateway_port",
        "API server escuchando",
        detail=f"No hay proceso escuchando en {host}:{port}.",
        hint="Pulsa “Iniciar Gateway” en este paso o en Command Center. La configuración puede estar correcta aunque el proceso siga apagado.",
        elapsed_ms=elapsed,
    )


async def check_gateway_health(
    base_url: str = HERMES_DEFAULT_URL,
) -> dict[str, Any]:
    """4/6 — `GET /health` returns liveness JSON.

    Hermes' api-server exposes ``/health`` as a tiny no-auth liveness
    endpoint (returns ``{"status": "ok"}`` or similar). Other paths
    like ``/v1/chat/completions`` require Bearer auth + a configured
    model, so probing them in a health check is unreliable."""
    runtime = runtime_policy.get_runtime()
    t0 = time.monotonic()

    def done(**kw):
        kw["elapsed_ms"] = int((time.monotonic() - t0) * 1000)
        return _result(id="gateway_health", label="API server responde JSON (/health)", **kw)

    if runtime.packaged:
        from core.hermes_service_registry import gateway_registry_state
        from core.workspace_sync import get_hermes_home
        ownership = await asyncio.to_thread(gateway_registry_state, get_hermes_home(), base_url)
        if not ownership["verified"]:
            return done(ok=False, detail="No se acreditó un Gateway de esta instalación y perfil.",
                        hint="Conserva el servicio ajeno y revisa el puerto configurado de Iris.")

    # Why 10s and not the obvious 5s: on Windows, the FIRST httpx call
    # to a localhost loopback often pays a 700-1500ms warmup tax (DNS,
    # socket setup, connection pool init). 10s gives enough slack
    # without making the check feel slow on the happy path (still <1s
    # when warm). `trust_env=False` short-circuits the WinHTTP / WPAD
    # proxy lookup which is another ~hundreds-of-ms tax on cold starts.
    try:
        async with httpx.AsyncClient(timeout=10.0, trust_env=False) as client:
            res = await client.get(f"{base_url.rstrip('/')}/health")
    except httpx.HTTPError as e:
        return done(
            ok=False,
            detail=f"Fallo de red: {e.__class__.__name__}",
            hint="¿Está corriendo Hermes? Reactívalo desde el paso de Hermes.",
        )

    if res.status_code != 200:
        return done(
            ok=False,
            detail=f"HTTP {res.status_code}",
            hint="Revisa los logs en ~/.iris/logs/hermes-gateway.log",
        )

    # A random web server returning HTTP 200 must never become our gateway.
    # This is the public contract of the supported native api-server adapter.
    try:
        data = res.json()
    except ValueError:
        data = None
    if not isinstance(data, dict) or data.get("platform") != "hermes-agent":
        return done(ok=False, detail="El servicio no se identifica como Hermes Gateway.")
    if data.get("status") != "ok":
        return done(ok=False, detail="Hermes responde, pero todavía no está listo.")
    return done(ok=True, detail="Hermes Gateway está listo.")


async def check_provider_credentials(
    provider: str = "",
    api_key: str = "",
    use_oauth: bool | None = None,
) -> dict[str, Any]:
    """5/6 — Configured provider has usable credentials.

    Verifies the same gate that ``/api/start-gateway`` uses:

      - ``auth=api``        → must have an API key (in config OR in env)
      - ``auth=oauth``      → must have a stored OAuth profile in Hermes'
                              auth store (the provider validates it at runtime)
      - ``auth=api+oauth``  → either path is enough
      - ``auth=none``       → always passes (Ollama / vLLM / LM Studio)

    Passing this is a strong predictor that the first chat will succeed,
    without needing to spend ~10s on a real round-trip + risk hitting
    rate limits on the user's free tier."""
    from wizard.hermes_config import (
        azure_foundry_uses_entra,
        has_hermes_auth_profile,
        provider_env_key,
        read_hermes_dotenv_if_present,
    )
    from wizard.providers import effective_auth_mode_for, get_provider, hermes_id_for

    t0 = time.monotonic()

    def done(**kw):
        kw["elapsed_ms"] = int((time.monotonic() - t0) * 1000)
        return _result(id="provider_credentials", label="Credenciales del proveedor listas", **kw)

    if not provider:
        return done(
            ok=False,
            detail="No hay proveedor configurado.",
            hint="Elige un proveedor en el paso de Hermes.",
        )

    auth_mode      = effective_auth_mode_for(provider)
    env_key        = provider_env_key(provider)
    has_key        = bool(api_key) or (bool(env_key) and bool(os.environ.get(env_key)))
    oauth_candidates = {
        hermes_id_for(provider, use_oauth=True),
        hermes_id_for(provider),
    }
    has_oauth = any(
        candidate and has_hermes_auth_profile(candidate, auth_type="oauth")
        for candidate in oauth_candidates
    )
    provider_label = (get_provider(provider) or {}).get("name") or provider

    if provider == "azure-foundry" and azure_foundry_uses_entra():
        return done(
            ok=True,
            detail=f"{provider_label}: autenticación Microsoft Entra configurada en Hermes.",
        )

    if auth_mode == "none":
        return done(
            ok=True,
            detail=f"{provider_label} es local — no necesita credenciales.",
        )

    if auth_mode == "api-optional":
        return done(
            ok=True,
            detail=(
                f"{provider_label}: la API key es opcional; "
                + ("hay una configurada." if has_key else "el endpoint se usará sin autenticación.")
            ),
        )

    if auth_mode == "aws":
        return done(
            ok=True,
            detail=(
                f"{provider_label}: Hermes usará la cadena de credenciales AWS SDK "
                "(perfil, variables, rol o token de Bedrock)."
            ),
        )

    if auth_mode == "external":
        from wizard.hermes_config import copilot_acp_command_problem, copilot_acp_launch

        # Same argv the pinned Hermes builds; the bundled engine needs an absolute path.
        packaged = runtime_policy.get_runtime().packaged
        try:
            command, _args = copilot_acp_launch(read_hermes_dotenv_if_present())
            problem = copilot_acp_command_problem(command, packaged=packaged)
        except ValueError:
            problem = "invalid_args"
        if problem:
            return done(
                ok=False,
                detail=(f"{provider_label}: HERMES_COPILOT_ACP_ARGS no es válido." if problem == "invalid_args"
                        else f"{provider_label}: no se encuentra la CLI externa requerida."),
                hint=("Indica en HERMES_COPILOT_ACP_COMMAND la ruta completa del ejecutable, sin comillas "
                      "ni argumentos (van en HERMES_COPILOT_ACP_ARGS)."
                      if packaged else
                      "Instala Copilot CLI o indica su ejecutable en HERMES_COPILOT_ACP_COMMAND, sin comillas "
                      "ni argumentos (van en HERMES_COPILOT_ACP_ARGS)."),
            )
        return done(
            ok=True,
            detail=f"{provider_label}: CLI externa detectada; Hermes reutilizará su sesión.",
        )

    if auth_mode == "oauth":
        if has_oauth:
            return done(ok=True, detail=f"{provider_label}: 🔐 perfil OAuth presente en Hermes.")
        return done(
            ok=False,
            detail=f"{provider_label}: no encuentro un perfil OAuth guardado.",
            hint="Vuelve al paso de Hermes y haz clic en \"Autenticar con OAuth\".",
        )

    if auth_mode == "api+oauth":
        # Validate the path selected in config. A stale OAuth profile must not
        # make an explicit API configuration look viable (or vice versa).
        if use_oauth is True:
            if has_oauth:
                return done(ok=True, detail=f"{provider_label}: 🔐 perfil OAuth presente en Hermes.")
            return done(
                ok=False,
                detail=f"{provider_label}: la configuración eligió OAuth, pero falta la sesión.",
                hint="Autentica con OAuth o cambia explícitamente al modo API key.",
            )
        if use_oauth is False:
            if has_key:
                return done(ok=True, detail=f"{provider_label}: 🔑 API key configurada ({env_key}).")
            return done(
                ok=False,
                detail=f"{provider_label}: la configuración eligió API key, pero falta {env_key or 'la clave'}.",
                hint=f"Pega {env_key or 'la API key'} o cambia explícitamente a OAuth.",
            )
        # Standalone callers that do not know the selected path retain the
        # broad capability check: either usable credential is sufficient.
        if has_key and has_oauth:
            return done(ok=True, detail=f"{provider_label}: 🔑 API key + 🔐 perfil OAuth presentes.")
        if has_key:
            return done(ok=True, detail=f"{provider_label}: 🔑 API key configurada ({env_key}).")
        if has_oauth:
            return done(ok=True, detail=f"{provider_label}: 🔐 perfil OAuth presente en Hermes.")
        return done(
            ok=False,
            detail=f"{provider_label}: ni API key ni OAuth configurados.",
            hint=f"Pega {env_key} o haz OAuth en el paso de Hermes.",
        )

    # auth_mode == "api" (or unknown defaults to "api")
    if has_key:
        return done(ok=True, detail=f"{provider_label}: 🔑 API key configurada ({env_key}).")
    return done(
        ok=False,
        detail=f"{provider_label} requiere {env_key or 'una API key'} y no la encuentro.",
        hint="Pégala en el paso de proveedor del wizard.",
    )


async def check_iris_websocket() -> dict[str, Any]:
    """6/6 — IRIS's own WebSocket server (`:8765`) is accepting connections."""
    t0 = time.monotonic()
    is_open = await _tcp_port_open("127.0.0.1", 8765, timeout=2.0)
    elapsed = int((time.monotonic() - t0) * 1000)
    if is_open:
        return _result(
            "iris_ws",
            "IRIS WebSocket activo",
            ok=True,
            detail="127.0.0.1:8765 responde — IRIS lista para el avatar.",
            elapsed_ms=elapsed,
        )
    return _inactive(
        "iris_ws",
        "IRIS WebSocket activo",
        detail="El puerto 8765 no está abierto.",
        hint="Es normal antes del último paso. Pulsa “Lanzar IRIS” al finalizar o ejecuta: python -m core.agent",
        elapsed_ms=elapsed,
    )


async def check_voice_extra(provider: str) -> dict[str, Any]:
    """7 — a local voice engine has its approved, isolated extra (A08.5)."""
    from core import extras

    label = "Voz local en su entorno aislado"
    t0 = time.monotonic()
    try:
        status = await asyncio.to_thread(extras.extras_status)
    except (extras.ExtrasError, extras.ProfilePathError) as exc:
        return _result("voice_extra", label, ok=False, detail=str(exc),
                       elapsed_ms=int((time.monotonic() - t0) * 1000))
    elapsed = int((time.monotonic() - t0) * 1000)
    if provider in status["unsupported"]:
        return _result("voice_extra", label, ok=False, detail=status["unsupported"][provider],
                       hint="Elige otra voz en el paso de voz.", elapsed_ms=elapsed)
    entry = next((item for item in status["extras"].values()
                  if item["consumer"] == f"tts:{provider}"), None)
    if entry is None:
        return _skip("voice_extra", label, "La voz elegida no necesita un extra local.")
    state = entry["state"]
    if state == "installed":
        return _result("voice_extra", label, ok=True,
                       detail=f"{entry['label']}: entorno aislado instalado y aprobado.",
                       elapsed_ms=elapsed)
    if state == "not_installed" and not runtime_policy.get_runtime().packaged:
        return _inactive(
            "voice_extra", label,
            detail=f"{entry['label']} no tiene extra en el perfil; desarrollo puede usar el venv.",
            hint="Para el paquete de Iris, instálalo desde el paso de voz.", elapsed_ms=elapsed,
        )
    detail = entry.get("detail") or f"{entry['label']} no está instalado."
    return _result("voice_extra", label, ok=False, detail=detail,
                   hint="Instala o repara el extra desde el paso de voz.", elapsed_ms=elapsed)


# ─────────────────────────────────────────────────────────────────────
# Runners
# ─────────────────────────────────────────────────────────────────────

async def iter_checks() -> AsyncIterator[dict[str, Any]]:
    """
    Yield each HealthResult in order as it completes.
    Later checks are skipped (with status="skip") when a prerequisite fails,
    so the UI can render all seven rows immediately.
    """
    cfg = IRISConfig.load()
    base_url = getattr(cfg, "hermes_url", "") or HERMES_DEFAULT_URL

    # ── 1. CLI ──
    r1 = await check_cli_in_path()
    yield r1

    # ── 2. Config (independent of CLI — file may exist even without CLI) ──
    r2 = await check_config_valid()
    yield r2

    # ── 3. Port ──
    r3 = await check_gateway_port(base_url)
    yield r3

    # ── 4. /health (depends on port — no point probing HTTP if TCP is dead) ──
    if not r3["ok"]:
        r4 = _skip("gateway_health", "API server responde JSON (/health)",
                   "Saltado porque el api-server no está escuchando.")
    else:
        r4 = await check_gateway_health(base_url)
    yield r4

    # ── 5. Provider credentials (independent of gateway — checks our own
    #      config + Hermes' auth store) ──
    r5 = await check_provider_credentials(
        provider=cfg.llm_provider or "",
        api_key=cfg.llm_api_key or "",
        use_oauth=bool(cfg.llm_use_oauth),
    )
    yield r5

    # ── 6. IRIS WS (independent — IRIS may run even if Hermes doesn't) ──
    r6 = await check_iris_websocket()
    yield r6

    # ── 7. Local voice extra (independent — reports the real engine state) ──
    yield await check_voice_extra(getattr(cfg, "tts_provider", "") or "")


async def run_all_checks() -> list[dict[str, Any]]:
    """Eager version: runs every check and returns all results in a list."""
    out: list[dict[str, Any]] = []
    async for r in iter_checks():
        out.append(r)
    return out

