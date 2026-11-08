"""
IRIS Hermes Bridge — integration with Hermes Agent (Nous Research).

Communicates with Hermes' OpenAI-compatible HTTP surface — the
``api-server`` platform — documented in
``website/docs/user-guide/features/api-server.md`` of the Hermes repo.

Two protocol details to keep in mind:

  1. The endpoint is **disabled by default**. Enabling it requires
     ``API_SERVER_ENABLED=true`` in ``~/.hermes/.env`` plus
     ``API_SERVER_KEY=<token>``. The wizard's
     ``write_hermes_config()`` writes both flags now; without them
     ``hermes gateway`` won't start the api-server adapter and every
     POST to ``/v1/chat/completions`` will time out.

  2. The OpenAI ``model`` field is the **literal string ``hermes-agent``**.
     Hermes resolves the underlying provider/model from its own
     ``config.yaml``. We just send ``"hermes-agent"`` and Hermes
     routes to whichever model the user configured.

Session continuity: Hermes accepts ``X-Hermes-Session-Id`` for the
transcript and ``X-Hermes-Session-Key`` for durable memory scope. IRIS keeps
the latter stable while the transcript id rotates on a fresh wake session or
changes through Continuity Center.

Model per conversation (F01): when Hermes advertises ``session_chat_streaming``
and ``session_model_lock``, every turn goes through
``POST /api/sessions/{id}/chat/stream`` with a confirmed model lock, because
only that route reports and enforces the model that answered.
``/v1/chat/completions`` echoes the requested name and ignores session locks
(docs/audits/iris_f01_model_selector_report_2026-10-07.md). Older Hermes keeps
the chat-completions path and no per-conversation selection.

Auth: ``Authorization: Bearer <API_SERVER_KEY>``. The wizard preserves an
existing Hermes token or creates a private random one on first setup.

Default port: ``8642`` (Hermes own default), configurable via
``config.hermes_url``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import urllib.parse
import uuid
from pathlib import Path
from typing import Any, Callable

import httpx

from core.animations import format_animation_catalog_for_prompt
from core.config import IRISConfig
from core.model_selection import ModelSelectionService, normalize_inventory, parse_runtime
from core.request_security import is_loopback_url, is_safe_service_url
from core.response_text import iris_response_envelope

logger = logging.getLogger("IRIS.hermes_bridge")

# Session-stream tool events -> the status ``Agent._on_tool_progress`` shows.
_SESSION_TOOL_STATUS = {"tool.started": "running", "tool.completed": "completed", "tool.failed": "failed"}
# Hermes' 4xx codes for a model selection it cannot route (``_runtime_lock_error``).
_SELECTION_ERROR_CODES = frozenset({"missing_model", "model_lock_unavailable", "model_lock_persistence_failed"})
# How long a Gateway locality check and a remote route's reported default stay valid.
_GATEWAY_LOCAL_TTL = 60.0
_ROUTE_DEFAULT_TTL = 30.0


def _local_gateway_matches(pid: Any) -> bool:
    """True when this machine's Hermes home records ``pid`` as its running Gateway."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    try:
        from core.workspace_sync import get_hermes_home

        home = get_hermes_home()
        state = json.loads((home / "gateway_state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(state, dict) or state.get("pid") != pid:
        return False
    recorded = str(state.get("hermes_home") or "").strip()
    if not recorded:
        return False
    try:
        return os.path.normcase(str(Path(recorded).resolve())) == os.path.normcase(str(home.resolve()))
    except OSError:
        return False


def _enabled_skills_block() -> str:
    """Lazy import wrapper.

    ``core.skills_bridge`` shells out to the Hermes CLI, which is a
    heavier dependency surface than the rest of this file. Importing
    it inside the function keeps ``hermes_bridge`` importable even
    when Hermes isn't on PATH (e.g. CI, fallback-only mode).
    """
    try:
        from core.skills_bridge import get_enabled_skills_block as _impl
        return _impl()
    except Exception as exc:  # pragma: no cover — defensive
        logger.debug("Skills block unavailable: %s", exc)
        return ""


# ════════════════════════════════════════════════════════════
# System prompt suffix — JSON contract
# ════════════════════════════════════════════════════════════
#
# IRIS emits `texto_hablado` / `emocion` / `animacion_virtual`
# regardless of which agent runtime sits behind the OpenAI endpoint.
_JSON_ENFORCEMENT = (
    "\n\nIMPORTANTE — FORMATO DE SALIDA:"
    "\n  1. Responde SOLO con el JSON, sin prosa antes ni después, sin markdown."
    "\n  2. Los 3 campos son OBLIGATORIOS en cada respuesta:"
    "\n     • `texto_hablado` (string) — Lo que dirás en voz alta."
    "\n     • `emocion` (neutral|feliz|triste|pensativa|sorprendida|enojada)."
    "\n     • `animacion_virtual` (string) — el nombre EXACTO de un clip"
    " listado en \"ANIMACIONES VRMA DISPONIBLES\" más abajo. Si no estás"
    " segura, usa `idle`. NO inventes nombres — los que no estén en esa"
    " lista no existen y la animación quedará muda."
    "\n  3. Si la respuesta es una charla casual sin animación específica, "
    "usa `\"animacion_virtual\": \"idle\"`."
    # accion_escena: a futuro, animaciones de escena no implementadas aún.
    # TODO: futuro — campo `accion_escena` (idle|buscar_internet|
    # recordar_memoria|pensar|buscar_archivos|respuesta_lista) para que
    # el avatar camine/interactúe con el cuarto 3D. Pendiente hasta
    # tener animaciones naturales.
    "\n\nANTI-INVENCIÓN: No inventes detalles sobre tu apariencia, tu interfaz "
    "ni capacidades técnicas del proyecto (SoulAura, anillos cyberpunk, HUDs, "
    "etc.) a menos que el usuario los mencione primero. Si te preguntan cómo "
    "luces y no tienes esa información en tu IDENTIDAD, di honestamente que "
    "no puedes verte. Nunca afirmes cosas como \"tengo X feature configurado\" "
    "si no aparece en tu IDENTITY.md."
    "\n\nCAPACIDADES AVANZADAS: Si Hermes dispone de herramientas, "
    "subagentes o tareas programadas, las gestiona internamente. IRIS no "
    "debe añadir campos `delegate`, `delegates`, `cron` ni `scheduled_task` "
    "en su JSON de salida. Mantén el contrato visible limitado a "
    "`texto_hablado`, `emocion` y `animacion_virtual`."
)


_DEFAULT_API_KEY = ""


# Maps Hermes' ``model.provider`` config string to the environment
# variable that must hold the provider's API key. Sourced from
# Hermes' bundled ``cli-config.yaml.example`` and the help text of
# ``hermes setup``. OAuth-only providers (codex, nous-oauth,
# openai-codex, xai-oauth, etc.) deliberately have an empty value so the
# diagnostic message tells the user to run the OAuth flow instead
# of looking for a phantom env var.
_PROVIDER_ENV_KEYS: dict[str, str] = {
    "openrouter":   "OPENROUTER_API_KEY",
    "anthropic":    "ANTHROPIC_API_KEY",
    "openai":       "OPENAI_API_KEY",
    "openai-api":   "OPENAI_API_KEY",
    "nous-api":     "NOUS_API_KEY",
    "gemini":       "GOOGLE_API_KEY",
    "google":       "GOOGLE_API_KEY",
    "zai":          "GLM_API_KEY",
    "kimi-coding":  "KIMI_API_KEY",
    "minimax":      "MINIMAX_API_KEY",
    "minimax-cn":   "MINIMAX_CN_API_KEY",
    "huggingface":  "HF_TOKEN",
    "nvidia":       "NVIDIA_API_KEY",
    "xiaomi":       "XIAOMI_API_KEY",
    "arcee":        "ARCEEAI_API_KEY",
    "ollama-cloud": "OLLAMA_API_KEY",
    "kilocode":     "KILOCODE_API_KEY",
    "ai-gateway":   "AI_GATEWAY_API_KEY",
    "copilot":      "GITHUB_TOKEN",
    "xai":          "XAI_API_KEY",
    # OAuth-only — no env var. Empty value tells the diagnostic
    # to suggest the OAuth login flow.
    "nous":              "",
    "openai-codex":      "",
    "xai-oauth":         "",
}


def _tail_gateway_error(max_lines: int = 200) -> str:
    """Scan the recent Hermes gateway log for the last upstream error.

    Hermes' adapters log non-retryable client errors verbatim, e.g.::

        ERROR root: Non-retryable client error: Error code: 400 -
        {'type':'error','error':{'type':'invalid_request_error',
         'message':"You're out of extra usage. Add more at..."}}

    Surfacing that message tells the user EXACTLY what Anthropic /
    OpenAI / OpenRouter said instead of a generic "puede ser créditos
    o filtro" — so they know whether to recharge credits, switch
    provider, change model, etc.

    Returns the extracted human-readable ``message`` field, or an
    empty string if no recent error is found.
    """
    try:
        from core.config import get_iris_home
        log_path = get_iris_home() / "logs" / "hermes-gateway.log"
        if not log_path.exists():
            return ""
        # Read only the tail so we don't load megabytes for nothing.
        size = log_path.stat().st_size
        with open(log_path, "rb") as fh:
            if size > 256_000:
                fh.seek(-256_000, 2)
                fh.readline()  # skip partial first line
            data = fh.read()
        text = data.decode("utf-8", errors="ignore")
        lines = text.splitlines()[-max_lines:]
        # Walk lines bottom-up so we get the most recent error first.
        import re as _re
        for line in reversed(lines):
            s = line.strip()
            if "Non-retryable client error" not in s and "ERROR" not in s and "API call failed" not in s:
                continue
            # Pull the first ``'message': "..."`` we find — Anthropic
            # and OpenAI both use this key in their error envelope.
            m = _re.search(r"'message':\s*\"([^\"]+)\"", s) or _re.search(r'"message":\s*"([^"]+)"', s)
            if m:
                return m.group(1).strip()
            # Gemini / generic "HTTP NNN: <provider> returned HTTP NNN: <body> | provider=...".
            # Strip the trailing "| provider=... model=..." pipe block
            # because the user already saw the model id in the panel.
            m = _re.search(r"HTTP\s+\d+:\s*(.+?)(?:\s*\|\s*provider=|\s*$)", s)
            if m:
                msg = m.group(1).strip()
                if msg:
                    return msg[:300]
            # Fallback: extract everything after "Error code: NNN -"
            m = _re.search(r"Error code:\s*\d+\s*-\s*(.+)$", s)
            if m:
                return m.group(1).strip()[:300]
            # Last fallback: surface "API call failed after N retries. <rest>"
            m = _re.search(r"API call failed[^.]*\.\s*(.+?)(?:\s*\|\s*provider=|\s*$)", s)
            if m:
                return m.group(1).strip()[:300]
        return ""
    except Exception:
        return ""


def _diagnose_empty_response() -> str:
    """Read Hermes' config + .env to explain why the provider returned
    an empty body, in user-actionable Spanish.

    Hermes' api-server returns HTTP 200 with empty content when the
    underlying provider is configured but the call effectively
    no-ops — no API key, expired credit, the model isn't granted
    to the account. Without telling the user *which* var to fix or
    *which* provider to switch, the fallback message is unhelpful.

    Robust to a missing/malformed config file: any failure during
    parsing returns a generic hint rather than crashing the bridge.
    """
    try:
        from core.workspace_sync import get_hermes_home
        home = get_hermes_home()
        cfg_path = home / "config.yaml"
        env_path = home / ".env"

        provider = ""
        model = ""

        # Try YAML parse first (cleanest); fall back to a tiny regex
        # scanner if pyyaml isn't on the import path. The file shape
        # we care about is::
        #     model:
        #       default: "anthropic/claude-opus-4.6"
        #       provider: "auto"
        if cfg_path.exists():
            try:
                import yaml  # type: ignore[import-untyped]
                cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
                model_block = cfg.get("model") or {}
                provider = str(model_block.get("provider") or "").strip()
                model = str(
                    model_block.get("default")
                    or model_block.get("model")
                    or ""
                ).strip()
            except Exception:
                # Regex fallback — only handles the straightforward
                # ``<key>: <value>`` shape, no anchors or arrays.
                import re
                txt = cfg_path.read_text(encoding="utf-8", errors="ignore")
                m_def = re.search(r"^\s+default:\s*[\"']?([^\"'\n]+)[\"']?", txt, re.M)
                m_prov = re.search(r"^\s+provider:\s*[\"']?([^\"'\n]+)[\"']?", txt, re.M)
                if m_def:
                    model = m_def.group(1).strip()
                if m_prov:
                    provider = m_prov.group(1).strip()

        # ``provider: auto`` means Hermes will infer from credentials
        # at call time. With a vendor-prefixed model id (most common
        # on a fresh install since ``default: anthropic/claude-...``)
        # auto-detect goes through OpenRouter, so that's the env var
        # we should be flagging.
        if (not provider or provider == "auto") and model:
            provider = "openrouter" if "/" in model else "auto"

        env_var = _PROVIDER_ENV_KEYS.get(provider, "")

        # Read .env line-by-line and check whether the env var is
        # populated. Comments and blanks ignored. Empty value is
        # treated the same as a missing var.
        has_key = False
        if env_var and env_path.exists():
            for line in env_path.read_text(
                encoding="utf-8", errors="ignore",
            ).splitlines():
                s = line.strip()
                if not s or s.startswith("#") or "=" not in s:
                    continue
                k, _, v = s.partition("=")
                if k.strip() != env_var:
                    continue
                v = v.strip().strip('"').strip("'")
                if v:
                    has_key = True
                break

        # Probe Hermes' OAuth store with the wizard's reader: if the user logged in via
        # the browser we must NOT tell them to paste an API key. It covers the
        # credential pool (Hermes >= 0.12) and a provider's own state (a first OpenAI
        # Codex login in Hermes 0.21 keeps its tokens there, nested).
        has_oauth = False
        try:
            from wizard.hermes_config import has_hermes_auth_profile

            has_oauth = bool(provider) and has_hermes_auth_profile(provider, auth_type="oauth", home=home)
        except Exception:
            pass

        # Build the message
        if not provider or provider == "auto":
            return (
                "No tienes proveedor de IA configurado en Hermes. "
                "Abre el wizard, paso 4, y elige uno (OpenRouter es el "
                "más fácil para empezar)."
            )

        # If neither API key NOR OAuth — nothing's configured.
        if env_var and not has_key and not has_oauth:
            return (
                f"Tu proveedor configurado es {provider} con modelo "
                f"{model or 'desconocido'}, pero no encuentro credenciales: "
                f"ni la variable {env_var} en ~/.hermes/.env ni un perfil "
                f"OAuth en auth.json. Pega tu API key en el wizard (paso 5) "
                f"o haz clic en \"Autenticar con OAuth\"."
            )

        if not env_var and not has_oauth:
            return (
                f"Tu proveedor es {provider} con modelo "
                f"{model or 'desconocido'} y requiere OAuth, pero no veo "
                f"un perfil guardado. Vuelve al wizard y haz clic en "
                f"\"Autenticar con OAuth\"."
            )

        # Auth IS configured (OAuth or API key) — empty body means
        # something else (no credits, content filter, model gating).
        # Try to surface the REAL error from the gateway log so the
        # user sees Anthropic / OpenAI's verbatim message instead of
        # a generic guess. We tail ``IRIS/logs/hermes-gateway.log``
        # because Hermes streams its upstream errors there.
        which = "OAuth" if has_oauth and not has_key else (
            "API key" if has_key and not has_oauth else "OAuth y API key"
        )
        upstream_msg = _tail_gateway_error()
        if upstream_msg:
            return (
                f"El proveedor {provider} (modelo {model or 'desconocido'}) "
                f"está autenticado con {which}, pero rechazó la petición: "
                f"{upstream_msg}"
            )
        return (
            f"El proveedor {provider} (modelo {model or 'desconocido'}) "
            f"está autenticado con {which}, pero devolvió una respuesta "
            f"vacía. Puede ser que tu cuenta esté sin créditos, un filtro "
            f"de seguridad o que el modelo no esté disponible para tu plan."
        )
    except Exception as exc:
        logger.debug("provider diagnosis failed: %s", exc)
        return (
            "No pude leer la config de Hermes para diagnosticar. "
            "Abre el wizard y revisa que tengas un proveedor con su "
            "API key."
        )


def _read_api_server_key_from_file(env_path: Path) -> str:
    """Read one API server key without exposing any other environment data."""
    try:
        if not env_path.exists():
            return ""
        for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, _, value = stripped.partition("=")
            if key.strip() != "API_SERVER_KEY":
                continue
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            return value
    except Exception:
        pass
    return ""


def _read_api_server_key_from_hermes_env() -> str:
    """Read ``API_SERVER_KEY`` from Hermes' live ``.env`` file.

    Hermes generates this token during ``hermes setup`` and stores it
    in the workspace ``.env`` at whatever path the official installer
    chose (``%LOCALAPPDATA%\\hermes\\.env`` on Windows,
    ``~/.hermes/.env`` on POSIX). We resolve the path with the same
    logic ``core.workspace_sync.get_hermes_home`` uses so we always
    look in the same place Hermes writes to.

    Returns ``""`` when the file is missing, malformed or doesn't
    contain the key — caller falls back to ``_DEFAULT_API_KEY``.
    Best-effort: failure is silent because this is only ONE of four
    token resolution sources.
    """
    try:
        from core.workspace_sync import get_hermes_home
        return _read_api_server_key_from_file(get_hermes_home() / ".env")
    except Exception:
        return ""


# Hermes treats the OpenAI ``model`` field as a literal magic string
# — its api-server adapter responds when ``model == "hermes-agent"``
# (or ``hermes-agent:<provider>/<model>`` for explicit override).
# The actual provider/model is resolved from ``~/.hermes/config.yaml``.
_HERMES_MODEL_TARGET = "hermes-agent"


def _build_reasoning_model_options(effort: str | None) -> dict[str, Any] | None:
    """Translate the UI choice into Hermes' request-level model options.

    Returns ``None`` when ``effort`` is falsy so the caller can simply
    omit ``model_options`` and let Hermes' config decide. Hermes 0.20
    accepts both the structured ``reasoning`` object and the compatibility
    ``reasoning_effort`` scalar; sending both keeps the contract explicit.

    Mapping:
        'none'   → reasoning OFF (key motivator: MiniMax M2.7 multimodal)
        'low'    → reasoning ON with effort=low
        'medium' → reasoning ON with effort=medium
        'high'   → reasoning ON with effort=high
        other    → None (no override)
    """
    if not effort:
        return None
    effort = str(effort).strip().lower()
    if effort == "none":
        return {
            "reasoning": {"enabled": False},
            "reasoning_effort": "none",
        }
    if effort in ("low", "medium", "high"):
        return {
            "reasoning": {"enabled": True, "effort": effort},
            "reasoning_effort": effort,
        }
    return None


def _stable_session_id(config: IRISConfig) -> str:
    """Derive a stable ``X-Hermes-Session-Id`` for the api-server.

    Hermes uses this header to bucket calls into ONE session instead
    of minting a fresh anonymous session per request. We tie it to
    the assistant's configured name so:
      • Every chat from THIS IRIS instance shares one session.
      • If the user reconfigures IRIS with a new name, they get a
        fresh session (matches "I'm a different assistant now"
        semantics rather than continuing under the old persona).
      • Two different IRIS installs on the same Hermes stay
        isolated as long as their assistant_name differs.

    Falls back to a constant for unconfigured IRIS — better one
    shared session than one-per-request (which would re-prepend
    history every turn and explode the prompt size).
    """
    name = (config.assistant_name or "").strip().lower()
    if not name:
        return "IRIS-default"
    safe = "".join(c if c.isalnum() else "-" for c in name).strip("-")
    return f"IRIS-{safe or 'default'}"


def _stable_session_key(config: IRISConfig) -> str:
    """Keep long-term memory scoped to this IRIS identity across transcripts.

    Hermes deliberately separates the transcript identifier, which rotates on
    ``/new`` or a Continuity Center switch, from the durable session key used
    by external memory providers.  Sending both headers lets IRIS resume or
    branch a transcript without accidentally creating a new long-term-memory
    identity each time.
    """
    name = (config.assistant_name or "").strip().lower()
    safe = "".join(c if c.isalnum() else "-" for c in name).strip("-")
    return f"agent:main:iris:dm:{safe or 'default'}"


_HERMES_PROFILE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def _named_profile_route(profile: str) -> tuple[str | None, str, str]:
    """Resolve Hermes' documented multiplex route for a named profile.

    Returns ``(profile, api_key, warning)``. A warning means routing is not
    safe and callers must not silently send the turn to the default profile.
    """
    normalized = str(profile or "").strip().lower()
    if not normalized or normalized == "default":
        return None, "", ""
    if not _HERMES_PROFILE_RE.fullmatch(normalized):
        return None, "", "Hermes detectó un nombre de perfil no válido; la orden no se ejecutó."

    try:
        from core.workspace_sync import get_hermes_home

        home = get_hermes_home()
        profile_home = home / "profiles" / normalized
        if not profile_home.is_dir():
            return None, "", (
                f"Hermes detectó el perfil ‘{normalized}’, pero ese perfil no existe en este equipo."
            )

        multiplex: bool | None = None
        override = os.environ.get("GATEWAY_MULTIPLEX_PROFILES", "").strip().lower()
        if override in {"1", "true", "yes", "on"}:
            multiplex = True
        elif override in {"0", "false", "no", "off"}:
            multiplex = False
        if multiplex is None:
            import yaml

            config_path = home / "config.yaml"
            document = yaml.safe_load(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
            document = document if isinstance(document, dict) else {}
            gateway = document.get("gateway") if isinstance(document.get("gateway"), dict) else {}
            multiplex = bool(document.get("multiplex_profiles") or gateway.get("multiplex_profiles"))
        if not multiplex:
            return None, "", (
                f"Hermes detectó el perfil ‘{normalized}’, pero el gateway multi‑perfil no está activo; "
                "la orden no se ejecutó en el perfil predeterminado."
            )

        api_key = _read_api_server_key_from_file(profile_home / ".env")
        if not api_key:
            return None, "", (
                f"El perfil ‘{normalized}’ no tiene API_SERVER_KEY propia; Hermes bloqueó el cambio seguro."
            )
        return normalized, api_key, ""
    except Exception as exc:
        logger.debug("Hermes profile route probe failed for %s: %s", normalized, exc)
        return None, "", (
            f"No pude validar el perfil ‘{normalized}’ de Hermes; la orden no se ejecutó."
        )


class HermesBridge:
    """Bridge to Hermes Agent's OpenAI-compatible api-server."""

    def __init__(self, config: IRISConfig, personality: Any = None) -> None:
        self.config = config
        self.base_url = config.hermes_url.rstrip("/")
        self.model = config.hermes_model or _HERMES_MODEL_TARGET
        self._personality = personality
        self._client: httpx.AsyncClient | None = None
        self._is_available: bool = False
        self._supports_model_options: bool = False
        # F01: session API transport with confirmed model locks.
        self._supports_session_runtime: bool = False
        self.model_selection = ModelSelectionService()
        self._known_sessions: set[tuple[str, str]] = set()
        # Summary of the last turn's model (what was planned, what Hermes confirmed).
        self.last_model_turn: dict[str, Any] | None = None
        # Called with that summary when a turn ends, cancelled turns included
        # (the agent broadcasts it; ``_think`` may never return on a barge-in).
        self.on_model_turn: Callable[[dict[str, Any]], None] | None = None
        # Bumped on every explicit conversation switch (Wake, Continuity):
        # a reply that arrives later belongs to the conversation it started in.
        self._conversation_generation = 0
        # Whether the Gateway is the one of this machine's Hermes home
        # (``(checked_at, is_local)``), and the default each remote route reported.
        self._gateway_local: tuple[float, bool] | None = None
        self._route_defaults: dict[str, tuple[float, dict[str, str]]] = {}
        self._base_session_id = _stable_session_id(config)
        self._session_id = self._base_session_id
        self._session_key = _stable_session_key(config)
        self._default_api_key = ""
        self._active_profile: str | None = None
        self._profile_api_key = ""
        self._profile_routing_warning = ""

        # Phase-3 enhancement (Quick Win #2): Hermes' OpenAI-compatible
        # api-server returns a ``run id`` in every chat-completion
        # response (``data.id`` for non-stream, the SSE chunk ``id``
        # for streaming). When the user barges in mid-reply we POST
        # ``/v1/runs/{id}/stop`` so Hermes drops the underlying
        # provider call instead of just letting it run to completion
        # while we silently discard the bytes on our side. This is
        # populated on every successful ``send_message`` call and
        # consumed by ``stop_current_run()`` (called from
        # ``Agent.interrupt``).
        self._current_run_id: str | None = None

    async def initialize(self) -> None:
        """Initialize the HTTP client and check if Hermes is running."""
        if not is_safe_service_url(self.base_url):
            logger.error("Rejected unsafe Hermes endpoint: %s", self.base_url)
            self._is_available = False
            return
        # Auth token resolution order:
        #   1. Explicit cfg.hermes_api_key (user pasted one in the wizard)
        #   2. API_SERVER_KEY env var (CI / advanced users)
        #   3. **API_SERVER_KEY read live from Hermes' own .env** —
        #      Hermes generates this token during ``hermes setup`` and
        #      we have no way to know its value otherwise. Reading the
        #      live file means IRIS authenticates correctly even when
        #      the user installed Hermes outside the wizard. This is
        #      the fix for the "chat doesn't respond" bug where the
        #      bridge sent ``IRIS-wizard-token`` while Hermes had
        #      generated its own random token under ``%LOCALAPPDATA%``.
        # There is deliberately no public fallback token. Fresh wizard
        # installs persist a random API_SERVER_KEY before starting Hermes;
        # an unknown token must fail closed instead of being guessable.
        token = self.config.hermes_api_key
        if not token and is_loopback_url(self.base_url):
            token = (
                os.environ.get("API_SERVER_KEY", "")
                or _read_api_server_key_from_hermes_env()
                or _DEFAULT_API_KEY
            )
        self._default_api_key = token
        headers: dict[str, str] = {
            "Content-Type":  "application/json",
            # Per-request session id is set in send_message — but we
            # also stamp it as a default so health checks land in the
            # same session bucket as chat turns (helps debugging).
            "X-Hermes-Session-Id": self._session_id,
            # Stable across transcript switches. Hermes uses this separately
            # from X-Hermes-Session-Id for long-term memory scoping.
            "X-Hermes-Session-Key": self._session_key,
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"

        # `trust_env=False` skips Windows' WPAD/proxy auto-detect which
        # adds 200-700ms of cold-start latency on each new client.
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=headers,
            timeout=httpx.Timeout(60.0, connect=10.0),
            trust_env=False,
        )

        await self._check_availability()

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def active_profile(self) -> str | None:
        return self._active_profile

    @property
    def profile_routing_warning(self) -> str:
        return self._profile_routing_warning

    def start_new_session(self, profile: str | None = None) -> dict[str, Any]:
        """Rotate the API transcript and, when safe, select a Hermes profile."""
        requested = str(profile or "").strip().lower()
        routed_profile, profile_key, warning = _named_profile_route(requested)
        profile_tag = routed_profile or (requested if requested else "default")
        safe_tag = "".join(c if c.isalnum() else "-" for c in profile_tag).strip("-") or "default"
        self._session_id = f"{self._base_session_id}-voice-{safe_tag}-{uuid.uuid4().hex}"
        self._active_profile = routed_profile
        self._profile_api_key = profile_key
        self._profile_routing_warning = warning
        self._current_run_id = None
        self._conversation_generation += 1
        if self._client is not None:
            self._client.headers["X-Hermes-Session-Id"] = self._session_id
        return {
            "session_id": self._session_id,
            "profile": routed_profile or ("default" if not requested or requested == "default" else requested),
            "profile_routed": bool(routed_profile) or not requested or requested == "default",
            "warning": warning,
        }

    def resume_session(self, session_id: str) -> dict[str, str]:
        """Point subsequent API turns at an already validated Hermes session.

        Existence and transcript access are verified by Continuity Center
        before this method is called.  Keeping the actual pointer mutation in
        the bridge ensures default headers, per-request headers and run
        cancellation state move together.
        """
        clean_id = str(session_id or "").strip()
        if not clean_id or len(clean_id) > 192 or re.search(r"[\r\n\x00]", clean_id):
            raise ValueError("Invalid Hermes session ID")
        # Continuity Center validates sessions against the configured API
        # base. A wake-routed multiplex profile is a temporary transport
        # choice and must not leak into this explicit base-session switch.
        self._active_profile = None
        self._profile_api_key = ""
        self._profile_routing_warning = ""
        self._session_id = clean_id
        self._current_run_id = None
        self._conversation_generation += 1
        if self._client is not None:
            self._client.headers["X-Hermes-Session-Id"] = clean_id
            self._client.headers["X-Hermes-Session-Key"] = self._session_key
        return {"session_id": clean_id, "session_key": self._session_key}

    @property
    def conversation_context(self) -> dict[str, Any]:
        """The active conversation as the selector names it.

        ``token`` changes on every explicit switch and survives a compression
        rotation (same conversation, new session id).
        """
        route = self.route_context()
        return {
            "profile": route["profile"] or "default",
            "session_id": route["session_id"],
            "generation": route["generation"],
            "token": route["token"],
        }

    async def restore_session(self, session_id: str) -> bool:
        """Restore a saved pointer only when Hermes still owns the session."""
        if self._client is None or not self._is_available:
            return False
        clean_id = str(session_id or "").strip()
        if not clean_id or len(clean_id) > 192 or re.search(r"[\r\n\x00]", clean_id):
            return False
        try:
            response = await self._client.get(
                self._api_path(
                    f"/api/sessions/{urllib.parse.quote(clean_id, safe='')}"
                ),
                headers=self._request_headers(),
                timeout=8.0,
            )
        except Exception as exc:
            logger.debug("Could not validate saved Hermes session %s: %s", clean_id, exc)
            return False
        if response.status_code != 200:
            return False
        try:
            payload = response.json()
        except ValueError:
            return False
        session = payload.get("session") if isinstance(payload, dict) else None
        if not isinstance(session, dict) or session.get("archived") is True:
            return False
        effective_id = str(session.get("id") or clean_id).strip()
        self.resume_session(effective_id)
        return True

    def _api_path(self, path: str) -> str:
        if self._active_profile:
            return f"/p/{self._active_profile}{path}"
        return path

    def _request_headers(self) -> dict[str, str]:
        headers = {
            "X-Hermes-Session-Id": self._session_id,
            "X-Hermes-Session-Key": self._session_key,
        }
        token = self._profile_api_key if self._active_profile else self._default_api_key
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _capture_session_header(
        self, response: Any, *, sent_id: str | None = None, generation: int | None = None,
    ) -> bool:
        """Follow compression-triggered transcript rotations from Hermes.

        With ``sent_id``/``generation`` (a chat turn), a header that arrives
        after the user switched conversation is ignored: it rotated the old one.
        """
        headers = getattr(response, "headers", None)
        if headers is None or not hasattr(headers, "get"):
            return False
        if generation is not None and (
            generation != self._conversation_generation or sent_id != self._session_id
        ):
            return False
        effective_id = str(headers.get("X-Hermes-Session-Id") or "").strip()
        if (
            not effective_id
            or len(effective_id) > 192
            or re.search(r"[\r\n\x00]", effective_id)
            or effective_id == self._session_id
        ):
            return False
        self._session_id = effective_id
        client_headers = getattr(self._client, "headers", None)
        if client_headers is not None and hasattr(client_headers, "__setitem__"):
            client_headers["X-Hermes-Session-Id"] = effective_id
        logger.info("Hermes rotated the active transcript to %s", effective_id)
        return True

    async def _check_availability(self) -> None:
        """Confirm Hermes' api-server adapter is up.

        ``/health`` is the always-on liveness endpoint (no auth).
        ``/v1/models`` is the gated one — if the wizard's
        ``write_hermes_config()`` didn't set ``API_SERVER_ENABLED=true``
        and ``hermes gateway`` was never started, neither endpoint
        responds. We log a clear hint in that case instead of letting
        the user wait for the first chat to fail.
        """
        assert self._client is not None

        try:
            health = await self._client.get("/health")
            if health.status_code != 200:
                logger.warning(
                    "Hermes /health returned %d — api-server reachable but unhealthy.",
                    health.status_code,
                )
                self._is_available = False
                return
        except httpx.ConnectError:
            logger.warning(
                "Hermes api-server not reachable at %s. "
                "Make sure it's running: `hermes gateway` "
                "(and API_SERVER_ENABLED=true in ~/.hermes/.env).",
                self.base_url,
            )
            self._is_available = False
            return
        except Exception as e:
            logger.warning("Error reaching Hermes /health: %s", e)
            self._is_available = False
            return

        try:
            response = await self._client.get("/v1/models")
            if response.status_code == 200:
                self._capture_session_header(response)
                data = response.json()
                models = [m.get("id", "") for m in data.get("data", [])]
                logger.info(
                    "Hermes api-server connected. Available models: %s",
                    ", ".join(models) or "(none)",
                )
                self._is_available = True
                await self._probe_request_capabilities()
            else:
                logger.warning(
                    "Hermes /v1/models returned %d — api-server may be off. "
                    "Set API_SERVER_ENABLED=true in ~/.hermes/.env.",
                    response.status_code,
                )
                self._is_available = False
        except Exception as e:
            logger.warning("Error reaching Hermes /v1/models: %s", e)
            self._is_available = False

    async def _probe_request_capabilities(self) -> None:
        """Detect optional per-request controls without gating basic chat.

        Hermes 0.19 remains a supported chat baseline, but not every 0.19
        build exposes ``/v1/capabilities`` or request ``model_options``.
        A missing/failed probe therefore disables only the per-turn override;
        the persisted ``agent.reasoning_effort`` setting remains effective.
        """
        self._supports_model_options = False
        self._supports_session_runtime = False
        if self._client is None:
            return
        try:
            response = await self._client.get(
                self._api_path("/v1/capabilities"),
                headers=self._request_headers(),
            )
            if response.status_code != 200:
                logger.info(
                    "Hermes request capabilities unavailable (status %d); "
                    "using persisted reasoning settings.",
                    response.status_code,
                )
                return
            data = response.json()
            features = data.get("features") if isinstance(data, dict) else None
            self._supports_model_options = bool(
                isinstance(features, dict) and features.get("model_options") is True
            )
            self._supports_session_runtime = bool(
                isinstance(features, dict)
                and features.get("session_chat_streaming") is True
                and features.get("session_model_lock") is True
            )
        except Exception as exc:
            logger.info(
                "Hermes request capability probe failed; using persisted "
                "reasoning settings: %s",
                exc,
            )

    @property
    def is_available(self) -> bool:
        return self._is_available

    async def send_message(
        self,
        user_message: str,
        context: list[dict[str, str]] | None = None,
        memory_summary: str = "",
        images: list[str] | None = None,
        reasoning_effort: str | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """
        Send a message to Hermes and get IRIS's response.

        Args:
            user_message: The transcribed speech / typed text from the user.
            context: Previous conversation messages in OpenAI format.
                Usually empty — Hermes maintains the session itself
                via ``X-Hermes-Session-Id``.
            memory_summary: Optional side-channel context (vision +
                emotion). Appended to the system prompt, not as a
                chat turn.
            images: Optional list of data-URL strings for multimodal
                turns (Fase 2.C). Hermes' api-server normalises these
                inline.
            progress_callback: Optional callable invoked with each
                ``hermes.tool.progress`` SSE event Hermes emits while
                the underlying provider is running tools (web search,
                file read, code exec, etc). When set we automatically
                switch to streaming mode under the hood so the events
                are observable, then accumulate the result back into
                the same dict shape the non-stream path returns.
                Quick Win #1 — the callback usually broadcasts to the
                face WebSocket so the avatar can show "🔧 buscando…"
                instead of staring blankly while a tool runs.

        Returns:
            Parsed JSON response from IRIS, or a fallback structure.
        """
        if not self._is_available or self._client is None:
            return self._fallback_response(user_message)

        # Build the messages array
        messages: list[dict[str, Any]] = []

        # System prompt — dynamic SOUL from personality engine.
        # ``format_animation_catalog_for_prompt`` lists VRMA clips the
        # face layer actually has, so the LLM picks valid names for
        # ``animacion_virtual`` instead of inventing them. The catalog
        # function caches by manifest mtime and refreshes lazily.
        if self._personality:
            system_content = (
                self._personality.get_soul()
                + _JSON_ENFORCEMENT
                + format_animation_catalog_for_prompt()
                + _enabled_skills_block()
            )
        else:
            # Minimal fallback if no personality loaded.  It still receives
            # the exact same JSON contract and live animation catalog; a
            # damaged/missing personality must not make Hermes guess clip ids.
            system_content = (
                "Eres IRIS, una asistente clara y cercana."
                + _JSON_ENFORCEMENT
                + format_animation_catalog_for_prompt()
                + _enabled_skills_block()
            )

        if memory_summary:
            system_content += f"\n\nContexto adicional:\n{memory_summary}"

        messages.append({"role": "system", "content": system_content})

        # Add conversation context (recent history) — usually empty;
        # Hermes' api-server prepends prior turns from the session
        # automatically via X-Hermes-Session-Id.
        if context:
            messages.extend(context)

        # Add the current user message. When images are attached we
        # promote the content to OpenAI's multimodal array shape:
        #   [{type: "text", text: "..."},
        #    {type: "image_url", image_url: {url: "data:image/png;base64,..."}}]
        # Hermes' api-server explicitly supports both http(s) and
        # data: URLs per the docs — no extra encoding work needed.
        if images:
            content_parts: list[dict[str, Any]] = []
            if user_message:
                content_parts.append({"type": "text", "text": user_message})
            for url in images:
                if not url:
                    continue
                content_parts.append({
                    "type": "image_url",
                    "image_url": {"url": url},
                })
            messages.append({"role": "user", "content": content_parts})
        else:
            messages.append({"role": "user", "content": user_message})

        # Hermes 0.20 exposes request-level reasoning through
        # ``model_options``. Older 0.19 builds ignore that field, so the
        # capability probe keeps them on their persisted global setting.
        # This matters most for image turns, where the UI requests ``none``
        # to avoid providers that cannot combine reasoning + image input.
        model_options = (
            _build_reasoning_model_options(reasoning_effort)
            if self._supports_model_options
            else None
        )

        # F01: Hermes keeps the history of a session itself, so the
        # session route takes only this turn (``context`` is not replayed,
        # exactly as X-Hermes-Session-Id already made Hermes do).
        if self._supports_session_runtime:
            return await self._send_session_turn(
                system_content,
                messages[-1]["content"],
                progress_callback,
                model_options=model_options,
                has_images=bool(images),
            )

        # When the caller supplied a ``progress_callback`` we route
        # through the streaming code path so ``hermes.tool.progress``
        # events are observable. The streaming helper accumulates the
        # final content and returns the same parsed dict the
        # non-stream branch produces, keeping the caller contract
        # identical.
        if progress_callback is not None:
            return await self._send_streaming_with_progress(
                messages,
                progress_callback,
                model_options=model_options,
                has_images=bool(images),
            )

        try:
            # `model` = literal "hermes-agent" — Hermes resolves the
            # backend provider/model from its own config.yaml. No
            # side-channel header needed.
            #
            # `X-Hermes-Session-Id` consolidates the conversation into
            # ONE Hermes session per assistant name. Without it Hermes
            # treats every call as a fresh anonymous session and
            # re-prepends history each turn (prompt bloat).
            body: dict[str, Any] = {
                "model":       self.model,
                "messages":    messages,
                "temperature": 0.7,
                "max_tokens":  1024,
                "stream":      False,
            }
            if model_options:
                body["model_options"] = model_options
            response = await self._client.post(
                self._api_path("/v1/chat/completions"),
                json=body,
                headers=self._request_headers(),
            )

            if response.status_code == 200:
                data = response.json()
                # Capture Hermes' run id so a later barge-in can call
                # POST /v1/runs/{id}/stop. Hermes uses the OpenAI
                # ``id`` field (e.g. "chatcmpl-…") which doubles as the
                # run id on its server — verified against api-server
                # adapter output v0.12.0.
                rid = data.get("id")
                if isinstance(rid, str) and rid:
                    self._current_run_id = rid
                assistant_content = (
                    data.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                )
                # Diagnostic — without this, an empty-content response
                # looks identical to a successful one in the logs (200 OK,
                # then silence) and the avatar appears frozen.
                logger.info(
                    "Hermes response: %d chars, run_id=%s",
                    len(assistant_content or ""),
                    self._current_run_id or "—",
                )
                return self._parse_iris_response(assistant_content)
            else:
                if images:
                    return self._vision_error_response(response.text, response.status_code)
                logger.error(
                    "Hermes returned status %d: %s",
                    response.status_code,
                    response.text[:200],
                )
                self._is_available = False
                return self._fallback_response(user_message)

        except httpx.ConnectError:
            logger.error("Lost connection to Hermes api-server.")
            self._is_available = False
            return self._fallback_response(user_message)
        except Exception as e:
            logger.error("Error communicating with Hermes: %s", e)
            return self._fallback_response(user_message)

    async def _send_streaming_with_progress(
        self,
        messages: list[dict[str, Any]],
        progress_callback: Callable[[dict[str, Any]], None],
        *,
        model_options: dict[str, Any] | None = None,
        has_images: bool = False,
    ) -> dict[str, Any]:
        """Stream-mode replacement for ``send_message`` when the caller
        wants to observe ``hermes.tool.progress`` events.

        Walks Hermes' SSE stream, fires ``progress_callback`` for every
        progress event, accumulates content deltas, and parses the
        final accumulated string the same way the non-stream branch
        does. Captures ``self._current_run_id`` from the first chunk
        that carries an ``id`` (Hermes emits it on the very first SSE
        chunk), so the stop button works the moment the user can see
        the agent is busy.

        Errors degrade to ``_fallback_response("")`` to stay drop-in
        compatible with the non-stream return shape.
        """
        if not self._is_available or self._client is None:
            return self._fallback_response("")

        chunks: list[str] = []
        # The conversation this turn belongs to (see ``_capture_session_header``).
        sent_id, generation = self._session_id, self._conversation_generation
        try:
            stream_body: dict[str, Any] = {
                "model":       self.model,
                "messages":    messages,
                "temperature": 0.7,
                "max_tokens":  1024,
                "stream":      True,
            }
            if model_options:
                stream_body["model_options"] = model_options
            async with self._client.stream(
                "POST",
                self._api_path("/v1/chat/completions"),
                json=stream_body,
                headers=self._request_headers(),
            ) as response:
                if response.status_code != 200:
                    body = (await response.aread()).decode(errors="replace")[:200]
                    logger.error(
                        "Hermes streaming returned status %d: %s",
                        response.status_code, body,
                    )
                    if has_images:
                        return self._vision_error_response(body, response.status_code)
                    self._is_available = False
                    return self._fallback_response("")

                self._capture_session_header(response, sent_id=sent_id, generation=generation)

                # Diagnostic state — track what Hermes actually sends
                # so we can debug "0 chunks, 0 chars" silent failures.
                chunk_count = 0
                tool_call_seen = False
                first_chunk_dump: str = ""
                non_stream_content: str = ""

                current_sse_event: str | None = None

                async for line in response.aiter_lines():
                    if line.startswith("event: "):
                        current_sse_event = line[7:].strip()
                        continue
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    chunk_count += 1
                    if chunk_count == 1:
                        # Snapshot the raw first chunk so empty-stream
                        # cases produce an actionable log line. Truncated
                        # to keep the log readable.
                        first_chunk_dump = data_str[:400]

                    # Capture run id from the first chunk that carries
                    # one — needed so ``stop_current_run`` can cancel
                    # the underlying provider call. Subsequent chunks
                    # carry the same id; idempotent overwrite.
                    rid = data.get("id")
                    if isinstance(rid, str) and rid:
                        self._current_run_id = rid

                    # ``hermes.tool.progress`` is the side-channel
                    # event Hermes emits while tools run. Current
                    # Hermes sends it as an SSE event line:
                    #
                    #   event: hermes.tool.progress
                    #   data: {"tool": "...", "status": "..."}
                    #
                    # Older/alternate adapters may encode the same marker
                    # in the JSON object itself. Support both shapes and
                    # never treat progress as spoken content.
                    is_progress_event = (
                        current_sse_event == "hermes.tool.progress"
                        or data.get("object") == "hermes.tool.progress"
                        or data.get("event") in {"tool.started", "tool.completed"}
                    )
                    if is_progress_event:
                        if current_sse_event and "event" not in data:
                            data["event"] = current_sse_event
                        try:
                            progress_callback(data)
                        except Exception as exc:
                            logger.debug(
                                "tool.progress callback raised: %s", exc,
                            )
                        current_sse_event = None
                        continue

                    current_sse_event = None

                    choice = (data.get("choices") or [{}])[0]
                    if not isinstance(choice, dict):
                        continue

                    # Streaming format — ``choices[].delta.content`` is
                    # the canonical OpenAI shape. Some Hermes adapters
                    # (and any provider returning a non-streaming body
                    # when streaming was requested) emit
                    # ``choices[].message.content`` instead; accept both
                    # so we don't silently drop the entire response.
                    delta_obj = choice.get("delta") or {}
                    delta = delta_obj.get("content") or ""
                    if delta:
                        chunks.append(delta)

                    msg_obj = choice.get("message") or {}
                    msg_content = msg_obj.get("content") or ""
                    if msg_content:
                        non_stream_content = msg_content

                    # If the model responds with tool calls instead of
                    # text, ``delta.content`` stays empty for the whole
                    # stream and we'd report "0 chars" forever. Flag it
                    # so the diagnostic at the end can mention it.
                    if delta_obj.get("tool_calls") or msg_obj.get("tool_calls"):
                        tool_call_seen = True

                # Prefer streaming chunks; fall back to a one-shot
                # ``message.content`` body if the server didn't
                # actually stream (some adapters do that on small
                # responses).
                if not chunks and non_stream_content:
                    chunks.append(non_stream_content)

                # Surface diagnostic context only when we ended up empty
                # — successful turns shouldn't pollute the log.
                if not chunks:
                    if tool_call_seen:
                        logger.warning(
                            "Hermes stream had tool_calls but no text content. "
                            "The model is trying to use tools instead of replying."
                        )
                    if first_chunk_dump:
                        logger.warning(
                            "First Hermes chunk (truncated): %s",
                            first_chunk_dump,
                        )
                    logger.warning(
                        "Stream stats: %d chunks parsed, 0 content captured.",
                        chunk_count,
                    )
        except httpx.ConnectError:
            logger.error("Lost connection to Hermes mid-stream.")
            self._is_available = False
            return self._fallback_response("")
        except Exception as e:
            logger.error("Hermes streaming error: %s", e)
            return self._fallback_response("")

        full_content = "".join(chunks)
        # Same diagnostic as the non-stream branch — without this an
        # empty stream is indistinguishable from a successful one in
        # the logs.
        logger.info(
            "Hermes stream complete: %d chars across %d chunks, run_id=%s",
            len(full_content), len(chunks), self._current_run_id or "—",
        )
        return self._parse_iris_response(full_content)

    # ── F01: session API transport ──────────────────────────────────────

    @property
    def supports_model_selection(self) -> bool:
        """True when this Hermes can confirm and enforce a model per turn."""
        return self._is_available and self._supports_session_runtime

    @staticmethod
    def _hermes_error(response: Any) -> tuple[str, str]:
        """``(code, message)`` from Hermes' JSON error envelope."""
        try:
            payload = response.json()
        except ValueError:
            payload = None
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict):
            return str(error.get("code") or ""), str(error.get("message") or "")[:300]
        if isinstance(error, str):
            return "", error[:300]
        return "", str(getattr(response, "text", "") or "")[:300]

    async def _ensure_session(self, turn: dict[str, Any]) -> str:
        """Create the session row the session API requires; '' once it exists.

        ``/v1/chat/completions`` created rows implicitly; the session routes
        answer 404 until ``POST /api/sessions`` does (409 = it already exists).
        """
        assert self._client is not None
        session_id = turn["session_id"]
        key = (turn["profile"] or "", session_id)
        if key in self._known_sessions:
            return ""
        response = await self._client.post(
            self._turn_path(turn, "/api/sessions"),
            json={"id": session_id, "source": "api_server"},
            headers=self._turn_headers(turn),
            timeout=15.0,
        )
        if response.status_code in (201, 409):
            self._known_sessions.add(key)
            return ""
        _code, message = self._hermes_error(response)
        logger.error("Hermes refused to create session %s (%d): %s", session_id, response.status_code, message)
        return message or f"Hermes no pudo abrir la conversación ({response.status_code})."

    def route_context(self) -> dict[str, Any]:
        """The active conversation with its route and credential, captured now.

        A turn, or a selector read, keeps using them after its awaits even if
        Wake or Continuity switches the active conversation meanwhile: it never
        queries, caches under or files its reply in another profile.
        ``token`` is the selector's conversation token.
        """
        profile = self._active_profile
        return {
            "profile": profile,
            "session_id": self._session_id,
            "generation": self._conversation_generation,
            "token": f"{profile or 'default'}:{self._conversation_generation}",
            "api_key": self._profile_api_key if profile else self._default_api_key,
        }

    @staticmethod
    def _turn_path(turn: dict[str, Any], path: str) -> str:
        return f"/p/{turn['profile']}{path}" if turn["profile"] else path

    def _turn_headers(self, turn: dict[str, Any]) -> dict[str, str]:
        headers = {"X-Hermes-Session-Id": turn["session_id"], "X-Hermes-Session-Key": self._session_key}
        if turn["api_key"]:
            headers["Authorization"] = f"Bearer {turn['api_key']}"
        return headers

    def _is_turn_conversation(self, turn: dict[str, Any]) -> bool:
        """Is the active conversation still the one ``turn`` started in?

        Same route and same session id. Returning to that conversation through
        Continuity counts: its compression must still be followed.
        """
        return self._active_profile == turn["profile"] and self._session_id == turn["session_id"]

    def _adopt_session_id(self, effective_id: str, *, turn: dict[str, Any]) -> str:
        """Follow a compression rotation of ``turn``'s conversation; return the turn's session.

        The selection always moves to the child session. The active pointer
        moves only while that conversation is still the active one: a reply
        that ends after Wake or Continuity switched conversation must not
        bring the old one back.
        """
        previous = turn["session_id"]
        clean = str(effective_id or "").strip()
        if (
            not clean
            or len(clean) > 192
            or re.search(r"[\r\n\x00]", clean)
            or clean == previous
        ):
            return previous
        self._known_sessions.add((turn["profile"] or "", clean))
        try:
            self.model_selection.store.copy(turn["profile"], previous, clean)
        except Exception as exc:
            logger.warning("Could not carry the model selection to %s: %s", clean, exc)
        if not self._is_turn_conversation(turn):
            logger.info("Hermes rotated %s to %s after a conversation switch; pointer kept", previous, clean)
            return clean
        self._session_id = clean
        client_headers = getattr(self._client, "headers", None)
        if client_headers is not None and hasattr(client_headers, "__setitem__"):
            client_headers["X-Hermes-Session-Id"] = clean
        logger.info("Hermes rotated the active transcript to %s", clean)
        return clean

    @staticmethod
    def _forward_tool_event(
        callback: Callable[[dict[str, Any]], None] | None, name: str, data: dict[str, Any],
    ) -> None:
        """Session ``tool.*`` events in the shape ``Agent._on_tool_progress`` reads.

        Arguments stay out: only the tool name and Hermes' own preview travel.
        """
        tool = str(data.get("tool_name") or "")
        if callback is None or not tool or tool.startswith("_"):
            return
        try:
            callback({
                "event": name,
                "tool": tool,
                "status": _SESSION_TOOL_STATUS[name],
                "preview": str(data.get("preview") or "")[:300],
            })
        except Exception as exc:
            logger.debug("tool.progress callback raised: %s", exc)

    def _failed_turn_response(self, content: str) -> dict[str, Any]:
        """What IRIS says when Hermes ends the turn as failed (``run.failed``)."""
        text = (content or "").strip()
        if text.startswith("Error code:"):
            return self._parse_provider_error(text)
        if text.startswith("{"):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict) and parsed.get("texto_hablado"):
                return parsed
        return {
            "texto_hablado": (
                "No pude completar la respuesta. Te dejé el detalle en el panel, junto al modelo."
            ),
            "emocion": "pensativa",
            "animacion_virtual": "pensar",
            "_provider_error": {"kind": "turn_failed", "raw": text[:240]},
        }

    @staticmethod
    def _selection_refused_response(message: str) -> dict[str, Any]:
        return {
            "texto_hablado": (
                "No pude usar el modelo de esta conversación. "
                "Elige otro o vuelve al predeterminado."
            ),
            "emocion": "pensativa",
            "animacion_virtual": "pensar",
            "_provider_error": {"kind": "model_selection", "raw": (message or "")[:240]},
        }

    def _store_turn(
        self,
        plan: dict[str, Any],
        turn: dict[str, Any],
        session_id: str,
        runtime: dict[str, Any] | None,
        status: str,
        error: str,
    ) -> dict[str, Any]:
        """Store a turn's outcome under the conversation it ran in (thread-safe)."""
        outcome = ModelSelectionService.outcome(plan, runtime, status=status, error=error)
        public = {**outcome, "session_id": session_id, "profile": turn["profile"] or "default"}
        try:
            self.model_selection.store.record_outcome(turn["profile"], session_id, outcome)
        except Exception as exc:
            logger.warning("Could not record the model outcome for %s: %s", session_id, exc)
        self.last_model_turn = public
        return public

    def _publish_turn(self, public: dict[str, Any]) -> None:
        """Hand the outcome to ``on_model_turn``; always on the event loop's thread."""
        if self.on_model_turn is None:
            return
        try:
            self.on_model_turn(public)
        except Exception as exc:
            logger.debug("on_model_turn raised: %s", exc)

    async def _finish_session_turn(
        self,
        plan: dict[str, Any],
        turn: dict[str, Any],
        runtime: dict[str, Any] | None,
        status: str,
        error: str,
        response: dict[str, Any],
        *,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        public = await asyncio.to_thread(
            self._store_turn, plan, turn, session_id or turn["session_id"], runtime, status, error,
        )
        self._publish_turn(public)
        response["_model_turn"] = public
        return response

    def _known_plan(self, turn: dict[str, Any]) -> dict[str, Any]:
        """``turn``'s plan from what is known without asking Hermes.

        The conversation's own selection; else the default last read for its
        Gateway (the local config only when that Gateway was accredited as
        local), or a default without a model if none was read yet.
        """
        profile = turn["profile"]
        default: dict[str, str] | None = {"provider": "", "model": ""}
        if self._gateway_local and self._gateway_local[1]:
            default = None  # turn_plan reads this machine's config
        elif cached := self._route_defaults.get(profile or "default"):
            default = dict(cached[1])
        try:
            return self.model_selection.turn_plan(profile, turn["session_id"], default=default)
        except Exception as exc:
            logger.debug("No plan for the stopped turn: %s", exc)
            return {"mode": "default", "provider": "", "model": "", "fields": {}}

    async def default_model(self, route: dict[str, Any] | None = None) -> dict[str, str]:
        """The default of the Gateway for ``route``'s profile (the active route if omitted).

        This machine's Hermes config when the Gateway is accredited as the
        local one; otherwise what that Gateway reports in that profile's
        inventory.
        """
        route = route or self.route_context()
        profile = route["profile"]
        if await self.gateway_is_local():
            return await asyncio.to_thread(self.model_selection.read_default, profile)
        key = profile or "default"
        cached = self._route_defaults.get(key)
        if cached and time.monotonic() - cached[0] < _ROUTE_DEFAULT_TTL:
            return dict(cached[1])
        try:
            default = (await self.model_inventory(route=route))["default"]
        except Exception as exc:
            logger.warning("Could not read the Gateway's default model: %s", exc)
            return {"provider": "", "model": ""}
        self._route_defaults[key] = (time.monotonic(), dict(default))
        return dict(default)

    async def gateway_is_local(self) -> bool:
        """True only when the Gateway is the one of this machine's Hermes home.

        The address must be loopback and ``/health/detailed`` must report the
        PID recorded in that home's ``gateway_state.json``, for that home.
        Iris reads and writes that config only then; a remote or foreign
        Gateway keeps its own default.
        """
        if self._gateway_local and time.monotonic() - self._gateway_local[0] < _GATEWAY_LOCAL_TTL:
            return self._gateway_local[1]
        local = False
        if self._client is not None and is_loopback_url(self.base_url):
            try:
                response = await self._client.get(
                    "/health/detailed",
                    headers={"Authorization": f"Bearer {self._default_api_key}"} if self._default_api_key else None,
                    timeout=8.0,
                )
                pid = response.json().get("pid") if response.status_code == 200 else None
                local = await asyncio.to_thread(_local_gateway_matches, pid)
            except Exception as exc:
                logger.debug("Gateway locality check failed: %s", exc)
        self._gateway_local = (time.monotonic(), local)
        return local

    async def _send_session_turn(
        self,
        system_content: str,
        user_content: Any,
        progress_callback: Callable[[dict[str, Any]], None] | None,
        *,
        model_options: dict[str, Any] | None = None,
        has_images: bool = False,
    ) -> dict[str, Any]:
        """One turn through ``/api/sessions/{id}/chat/stream`` with a confirmed lock.

        The turn's conversation and model are fixed here, when it starts: a
        selection made while it runs applies to the next turn, and a reply that
        ends after a conversation switch is filed under its own conversation.
        Only the terminal event decides success (the non-streaming route
        answers 200 even when the turn failed). A local cancellation (barge-in,
        Detener) is recorded as cancelled before it propagates.
        """
        assert self._client is not None
        turn = self.route_context()
        profile, session_id = turn["profile"], turn["session_id"]
        # No run exists until Hermes starts this one: Stop must not target the
        # previous turn's (finished) run meanwhile.
        self._current_run_id = None
        try:
            default = await self.default_model(turn)
            plan = await asyncio.to_thread(
                self.model_selection.turn_plan, profile, session_id, default=default,
            )
        except asyncio.CancelledError:
            # Stopped while the turn was prepared: nothing reached Hermes. File
            # the attempt as cancelled all the same (the previous confirmed
            # answer is not its result), then keep cancelling.
            self._publish_turn(self._store_turn(self._known_plan(turn), turn, session_id, None, "cancelled", ""))
            raise
        except Exception as exc:
            logger.warning("Model selection unavailable, using Hermes' own default: %s", exc)
            plan = {"mode": "default", "provider": "", "model": "", "fields": {}}

        runtime: dict[str, Any] | None = None
        status = ""
        error = ""
        chunks: list[str] = []
        final_content: str | None = None
        effective_id = ""
        try:
            problem = await self._ensure_session(turn)
            if problem:
                return await self._finish_session_turn(
                    plan, turn, None, "failed", problem, self._fallback_response(""),
                )
            body: dict[str, Any] = {
                "message": user_content,
                "system_message": system_content,
                **plan["fields"],
            }
            if model_options:
                body["model_options"] = model_options
            path = self._turn_path(
                turn, f"/api/sessions/{urllib.parse.quote(session_id, safe='')}/chat/stream"
            )
            async with self._client.stream(
                "POST", path, json=body, headers=self._turn_headers(turn),
            ) as response:
                if response.status_code != 200:
                    await response.aread()
                    code, message = self._hermes_error(response)
                    logger.error(
                        "Hermes session turn returned %d %s: %s",
                        response.status_code, code, message,
                    )
                    if response.status_code == 404:
                        # The row vanished (deleted in Hermes): the next turn recreates it.
                        self._known_sessions.discard((profile or "", session_id))
                        return await self._finish_session_turn(
                            plan, turn, None, "failed", message, self._fallback_response(""),
                        )
                    if response.status_code == 409 or code in _SELECTION_ERROR_CODES:
                        return await self._finish_session_turn(
                            plan, turn, None, "failed", message,
                            self._selection_refused_response(message),
                        )
                    if has_images:
                        return await self._finish_session_turn(
                            plan, turn, None, "failed", message,
                            self._vision_error_response(message, response.status_code),
                        )
                    self._is_available = False
                    return await self._finish_session_turn(
                        plan, turn, None, "failed", message, self._fallback_response(""),
                    )

                current_event: str | None = None
                async for line in response.aiter_lines():
                    if line.startswith("event: "):
                        current_event = line[7:].strip()
                        continue
                    if not line.startswith("data: "):
                        continue
                    try:
                        data = json.loads(line[6:])
                    except json.JSONDecodeError:
                        continue
                    name, current_event = current_event or "", None
                    if not isinstance(data, dict):
                        continue
                    if name == "run.started":
                        rid = data.get("run_id")
                        # Stop acts on the active conversation's run only.
                        if isinstance(rid, str) and rid and self._is_turn_conversation(turn):
                            self._current_run_id = rid
                    elif name == "assistant.delta":
                        delta = data.get("delta")
                        if isinstance(delta, str):
                            chunks.append(delta)
                    elif name in _SESSION_TOOL_STATUS:
                        self._forward_tool_event(progress_callback, name, data)
                    elif name == "assistant.completed":
                        if isinstance(data.get("content"), str):
                            final_content = data["content"]
                        runtime = parse_runtime(data.get("runtime")) or runtime
                        effective_id = str(data.get("session_id") or "") or effective_id
                    elif name in ("run.completed", "run.failed", "run.cancelled"):
                        status = name[len("run."):]
                        runtime = parse_runtime(data.get("runtime")) or runtime
                        effective_id = str(data.get("session_id") or "") or effective_id
                    elif name == "error":
                        error = str(data.get("message") or "")[:400]
                    elif name == "done":
                        break
        except asyncio.CancelledError:
            # Barge-in or Detener cancelled this task: the stop was sent and the
            # stream closed. File the turn as cancelled (never leave the previous
            # confirmed answer as this turn's result), then keep cancelling.
            self._publish_turn(self._store_turn(plan, turn, session_id, runtime, "cancelled", ""))
            raise
        except httpx.ConnectError:
            logger.error("Lost connection to Hermes mid-stream.")
            self._is_available = False
            return await self._finish_session_turn(
                plan, turn, runtime, "failed", "Se perdió la conexión con Hermes.",
                self._fallback_response(""),
            )
        except Exception as exc:
            logger.error("Hermes session streaming error: %s", exc)
            return await self._finish_session_turn(
                plan, turn, runtime, "failed", str(exc), self._fallback_response(""),
            )

        turn_session = self._adopt_session_id(effective_id, turn=turn) if effective_id else session_id
        content = final_content if final_content is not None else "".join(chunks)
        logger.info(
            "Hermes session turn %s: %d chars, model=%s, run_id=%s",
            status or "unfinished", len(content), (runtime or {}).get("model") or "—",
            self._current_run_id or "—",
        )
        if (
            status == "completed"
            and plan["fields"].get("require_model_lock")
            and (runtime or {}).get("model_lock") != "confirmed"
        ):
            # Hermes closes the turn as completed when the locked provider could
            # not even be resolved (no agent ran, the lock stays "accepted"); its
            # text is a diagnostic, not an answer from the selected model.
            status = ""
        if status == "completed":
            reply = self._parse_iris_response(content)
        elif status == "cancelled":
            reply = (
                self._parse_iris_response(content)
                if content.strip()
                else {"texto_hablado": "Detuve la respuesta.", "emocion": "neutral", "animacion_virtual": "idle"}
            )
        else:
            # run.failed, an ``error`` event, or a stream that ended without
            # a terminal event: never present it as a finished answer.
            status = "failed"
            error = error or content.strip()[:400] or "Hermes no terminó la respuesta."
            reply = self._failed_turn_response(content)
        return await self._finish_session_turn(
            plan, turn, runtime, status, error, reply, session_id=turn_session,
        )

    async def model_inventory(
        self, *, refresh: bool = False, route: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Hermes' authenticated model inventory for ``route`` (the active route if omitted)."""
        if self._client is None or not self._is_available:
            raise RuntimeError("Hermes no está disponible.")
        route = route or self.route_context()
        response = await self._client.get(
            self._turn_path(route, "/api/model/options"),
            params={"refresh": "true"} if refresh else None,
            headers=self._turn_headers(route),
            timeout=60.0 if refresh else 25.0,
        )
        if response.status_code != 200:
            _code, message = self._hermes_error(response)
            raise RuntimeError(message or f"Hermes devolvió {response.status_code}.")
        return normalize_inventory(response.json())

    async def app_model_inventory(self, *, refresh: bool = False) -> dict[str, Any]:
        """Mail and meeting preparation use this machine's default profile, not the chat's route."""
        if not self._is_available or not await self.gateway_is_local():
            raise RuntimeError("Las apps necesitan el motor local de Iris.")
        route = {"profile": None, "session_id": self._session_id, "api_key": self._default_api_key}
        catalog = await self.model_inventory(refresh=refresh, route=route)
        return {"inventory": catalog, "default": await self.default_model(route)}

    async def stop_current_run(self) -> bool:
        """Cancel Hermes' in-flight run server-side via
        ``POST /v1/runs/{id}/stop``.

        Quick Win #2 — the previous interrupt path only stopped TTS
        playback and cancelled our local asyncio task, but Hermes
        kept the underlying provider call running to completion (and
        we still got billed for it). With this, the moment the user
        speaks over IRIS we tell Hermes to abort the run too.

        Returns:
            ``True`` on a 2xx response, ``False`` on any failure or
            when no run is currently tracked. Callers should treat
            failure as "best effort already done client-side" and
            never block on the result.
        """
        rid = self._current_run_id
        if not rid or self._client is None:
            return False
        try:
            r = await self._client.post(
                self._api_path(f"/v1/runs/{rid}/stop"),
                headers=self._request_headers(),
                timeout=5.0,
            )
            ok = 200 <= r.status_code < 300
            if not ok:
                logger.debug(
                    "stop_current_run %s returned HTTP %d",
                    rid, r.status_code,
                )
            return ok
        except Exception as exc:
            # 404 here usually means the run already finished — fine.
            logger.debug("stop_current_run(%s) failed: %s", rid, exc)
            return False
        finally:
            # Always clear the id so a stale value doesn't haunt the
            # next interrupt — we want stop_current_run to be a no-op
            # until the next chat call populates a fresh id.
            self._current_run_id = None

    def _parse_iris_response(self, content: str) -> dict[str, Any]:
        """Parse IRIS's JSON response, with fallback for plain text."""
        content = content.strip()

        # ── Empty-content guard ─────────────────────────────────────
        # Hermes occasionally returns HTTP 200 with an empty message
        # ``content`` field — typically when the underlying provider
        # silently rejected the call (safety filter, missing tool
        # response, exhausted credits with no error). Without this
        # guard the avatar would appear frozen because
        # ``agent._respond`` skips the entire TTS/animation pipeline
        # when ``texto_hablado`` is empty (see ``if spoken_text:``).
        #
        # We diagnose by reading Hermes' own config to point the user
        # at the precise variable / OAuth flow they need to complete.
        if not content:
            spoken = _diagnose_empty_response()
            logger.warning(
                "Hermes returned empty content — diagnosis: %s", spoken,
            )
            return {
                "texto_hablado":     spoken,
                "emocion":           "pensativa",
                "animacion_virtual": "pensar",
            }

        # ── Wrapped-error detection (provider 401 / 429 / 500) ──
        # Hermes' api-server doesn't surface upstream provider errors
        # as HTTP non-2xx — it returns HTTP 200 with the error
        # serialized as the assistant's content (e.g.
        # ``"Error code: 401 - {'error': {'message': 'Missing
        # Authentication header', ...}}"``). Without special-casing
        # this, the user sees IRIS silently fail to talk because the
        # JSON parser gives up and the plain-text fallback below
        # tries to "speak" the raw error string.
        #
        # We pattern-match on the leading "Error code:" prefix Hermes
        # uses consistently across providers, then map the most common
        # status codes to user-friendly Spanish hints. Always returns
        # a sad emotion + suggestion to fix it in the wizard.
        if content.startswith("Error code:"):
            return self._parse_provider_error(content)

        # Hermes may append a failed-write diagnostic after the model's JSON.
        # The same read-only decoder serves history/background presentation.
        # It recovers the envelope and turns that diagnostic into an honest,
        # human-readable warning instead of announcing an unconfirmed action.
        envelope = iris_response_envelope(content)
        if envelope is not None:
            if failure := envelope.get("_file_mutation_error"):
                logger.warning(
                    "Hermes reports %d unconfirmed file edit(s)", failure["failed_count"],
                )
            return envelope

        # Fallback: treat entire content as spoken text. Three-field
        # contract — `accion_escena` is intentionally omitted (TODO:
        # futuro — animaciones de escena no implementadas aún).
        return {
            "texto_hablado": content,
            "emocion": "neutral",
            "animacion_virtual": "idle",
        }

    def _parse_provider_error(self, content: str) -> dict[str, Any]:
        """Translate a Hermes-wrapped provider error into a fallback dict.

        Hermes responds 200 OK with content like::

            "Error code: 401 - {'error': {'message': 'Missing Authentication header', 'code': 401}}"

        We extract the numeric code (first integer after "Error code:")
        and map known-bad cases (401 = missing/invalid API key, 429 =
        rate-limited, 5xx = upstream outage) to actionable messages.
        Unknown codes fall through to a generic "el proveedor falló"
        explanation that still beats showing the raw error string to
        the user via TTS.
        """
        import re
        code_match = re.search(r"Error code:\s*(\d+)", content)
        code = int(code_match.group(1)) if code_match else 0
        snippet = content[:240]
        logger.warning("Provider returned wrapped error: %s", snippet)

        if code == 401:
            spoken = (
                "El proveedor rechazó mis credenciales. "
                "Pega tu API key en el wizard o entra con OAuth."
            )
            emotion = "triste"
        elif code == 403:
            spoken = (
                "El proveedor dice que no tengo permiso. "
                "Revisa que tu cuenta tenga acceso al modelo elegido."
            )
            emotion = "triste"
        elif code == 404:
            spoken = (
                "El modelo no existe o cambió de nombre. "
                "Elige otro en el wizard."
            )
            emotion = "pensativa"
        elif code == 429:
            spoken = (
                "Estoy yendo muy rápido para mi proveedor. "
                "Espera un momento o cambia a otro modelo."
            )
            emotion = "pensativa"
        elif 500 <= code < 600:
            spoken = (
                f"El proveedor tuvo un fallo interno (código {code}). "
                "Probá de nuevo en un minuto."
            )
            emotion = "triste"
        else:
            spoken = (
                f"Hermes me devolvió un error del proveedor "
                f"({code or 'sin código'}). Revisa tu config."
            )
            emotion = "triste"

        return {
            "texto_hablado":     spoken,
            "emocion":           emotion,
            "animacion_virtual": "pensar",
            # Internal field for logs / future telemetry — not consumed
            # by the face today but useful for Sentry-style triage.
            "_provider_error":   {"code": code, "raw": snippet},
        }

    def _vision_error_response(self, raw: str, status_code: int) -> dict[str, Any]:
        """Explain multimodal failures without marking Hermes offline."""
        snippet = (raw or "")[:240]
        logger.warning(
            "Hermes vision turn failed with status %s: %s",
            status_code,
            snippet,
        )
        return {
            "texto_hablado": (
                "Hermes recibio la imagen, pero el modelo actual no acepto vision. "
                "Prueba con un modelo multimodal o configura el modelo auxiliar de vision."
            ),
            "emocion": "pensativa",
            "animacion_virtual": "pensar",
            "_provider_error": {
                "code": status_code,
                "raw": snippet,
                "kind": "vision",
            },
        }

    def _fallback_response(self, user_message: str) -> dict[str, Any]:
        """Generate a fallback response when Hermes is unavailable."""
        return {
            "texto_hablado": (
                "Lo siento, no puedo conectarme con mi cerebro en este momento. "
                "Verifica que Hermes esté ejecutándose con `hermes gateway`."
            ),
            "emocion": "triste",
            "animacion_virtual": "pensar",
        }

    async def reconnect(self) -> bool:
        """Attempt to reconnect to Hermes."""
        await self._check_availability()
        return self._is_available

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

