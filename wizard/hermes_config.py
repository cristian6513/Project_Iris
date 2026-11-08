"""
Hermes Configuration Writer — Project IRIS

Writes the user's provider / model selection + API server settings
into ``~/.hermes/.env`` and ``~/.hermes/config.yaml`` so the Hermes
gateway picks them up on next start.

Why this exists:
    Hermes ships with empty config.yaml + .env templates. Without
    this writer, after the wizard finishes the user would still have
    to run ``hermes setup`` interactively to configure providers —
    defeating the "one-click install" flow.

Strategy:
    1. ``~/.hermes/.env`` — flat KEY=VALUE format (dotenv). We
       merge: read existing, update specific keys, write back. We
       NEVER blindly overwrite — the user may have edited it by
       hand, and other Hermes setup wizards (``hermes setup``) may
       have added keys we don't know about.
    2. ``~/.hermes/config.yaml`` — YAML, much larger surface. We
       only patch the keys we own (``model``, ``providers``) and
       leave the rest untouched. Uses PyYAML which IRIS already
       depends on indirectly.
    3. All operations are idempotent — re-running with the same
       inputs is a no-op.

Config we write:
    .env:
        API_SERVER_ENABLED=true        # turns on the OpenAI HTTP endpoint
        API_SERVER_KEY=<token>         # Bearer auth for /v1/*
        API_SERVER_MODEL_NAME=hermes-agent
        OPENROUTER_API_KEY=...         # only if user picked openrouter
        OPENAI_API_KEY=...             # only if user picked openai
        ANTHROPIC_API_KEY=...          # ...etc

    config.yaml:
        model: "openrouter/anthropic/claude-opus-4.6"
        providers:
            openrouter: { ... }
"""

from __future__ import annotations

import logging
import os
import re
import secrets
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from core.file_lock import interprocess_file_lock
from core.request_security import is_loopback_url, is_safe_service_url
from core import runtime_descriptor as runtime_policy

logger = logging.getLogger("IRIS.wizard.hermes_config")


# Compatibility export for callers that imported the former predictable
# fallback. Fresh installations generate a private token instead.
API_SERVER_KEY_DEFAULT = ""


def get_hermes_home() -> Path:
    """Return Hermes' home directory, honouring ``HERMES_HOME`` env var.

    Resolution order (first match wins):

      1. ``HERMES_HOME`` env var if set — covers users who relocated
         their config or run multiple Hermes profiles.
      2. **Windows**: ``%LOCALAPPDATA%\\hermes`` (e.g.
         ``C:\\Users\\You\\AppData\\Local\\hermes``). This is where
         the official ``install.ps1`` puts the workspace — NOT
         ``~/.hermes/``. We mistakenly defaulted to the latter and
         spent a debugging session chasing a 401-loop because Hermes
         was reading its real ``.env`` from LOCALAPPDATA while we
         kept rewriting a stale shadow copy under the user profile.
      3. **POSIX**: ``$XDG_CONFIG_HOME/hermes`` if set, else
         ``~/.hermes``. Matches the Linux/macOS installer layout.
      4. Last-resort fallback: ``~/.hermes`` (works on every OS but
         won't be where Hermes actually looks on a stock Windows install).

    All resolved homes must pass the profile path policy; invalid locations
    raise ProfilePathError instead of becoming writable fallback directories.
    """
    runtime_policy.get_runtime()  # Reject invalid descriptors before consulting profile paths.
    from core.profile_paths import validate_profile_home

    custom = os.environ.get("HERMES_HOME", "").strip()
    if custom:
        path = Path(custom).expanduser()
        return validate_profile_home(path if path.is_absolute() else Path.home() / path)

    # Windows — Hermes' install.ps1 hard-codes %LOCALAPPDATA%\hermes
    # as the workspace root and the launcher does NOT export
    # HERMES_HOME (verified against v0.12.0). So we mirror the
    # installer's choice instead of letting the POSIX default win.
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA", "").strip()
        if local:
            candidate = Path(local) / "hermes"
            # The official installer owns this location even before its first
            # config write. Using it immediately prevents a phantom
            # ~/.hermes profile during installation or repair.
            return validate_profile_home(candidate)
        return validate_profile_home(Path.home() / ".hermes")

    # POSIX — XDG-compliant: $XDG_CONFIG_HOME/hermes wins, falling
    # back to ~/.hermes which matches the install.sh default.
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    if xdg:
        candidate = Path(xdg) / "hermes"
        if (candidate / "config.yaml").exists() or (candidate / ".env").exists():
            return validate_profile_home(candidate)
    return validate_profile_home(Path.home() / ".hermes")


def get_env_path() -> Path:
    """``~/.hermes/.env``. Created lazily by the writer."""
    return get_hermes_home() / ".env"


def get_config_yaml_path() -> Path:
    """``~/.hermes/config.yaml``. Created lazily by the writer."""
    return get_hermes_home() / "config.yaml"


def hermes_cli_available() -> bool:
    """True if the ``hermes`` CLI is on PATH (with .exe on Windows)."""
    if runtime_policy.get_runtime().hermes:
        from core.hermes_runtime import resolve_hermes_cli
        return bool(resolve_hermes_cli())
    return bool(shutil.which("hermes") or shutil.which("hermes.exe"))


# ════════════════════════════════════════════════════════════
# .env reader / writer (dotenv format)
# ════════════════════════════════════════════════════════════

# Match KEY=VALUE lines, with optional inline whitespace + quoted values.
# We don't try to be a full dotenv parser — Hermes' .env stays simple
# enough that this regex is sufficient and avoids pulling python-dotenv
# in just for read/write.
_ENV_LINE_RE = re.compile(r"^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*?)\s*$")


def read_hermes_dotenv() -> dict[str, str]:
    """Public alias for _read_env_file — parses ``~/.hermes/.env`` into a flat dict."""
    return _read_env_file()


def read_hermes_dotenv_if_present() -> dict[str, str] | None:
    """Like ``read_hermes_dotenv`` but None when the profile has no ``.env``.

    Hermes treats a missing file and an empty one differently when it loads
    the profile (see ``copilot_acp_launch``); ``KEY=`` is kept as ``""``.
    """
    return _read_env_file() if get_env_path().exists() else None


# GitHub Copilot ACP exactly as the pinned Hermes starts it
# (agent/copilot_acp_client.py: _resolve_command/_resolve_args and Popen): the
# whole HERMES_COPILOT_ACP_COMMAND (or COPILOT_CLI_PATH) value is argv[0], never
# a command line; arguments come only from HERMES_COPILOT_ACP_ARGS.
#
# Which value Hermes sees follows its profile loader (hermes_cli/env_loader.py,
# _PROFILE_MANAGED_ENV_KEYS): when the profile has a .env, it overrides the
# inherited environment for these keys, an explicit empty value included, and an
# inherited key the file does not define is removed. Only a profile without
# .env leaves the inherited values in place.
COPILOT_ACP_DEFAULT_ARGS = ("--acp", "--stdio")


def copilot_acp_launch(env_file: dict[str, str] | None) -> tuple[str, list[str]]:
    """Return (command, args); raises ValueError for unparseable ACP arguments.

    ``env_file`` is the parsed profile ``.env``, or None when the profile has
    none (``read_hermes_dotenv_if_present``).
    """
    values = os.environ if env_file is None else env_file

    def setting(name: str) -> str:
        return str(values.get(name) or "").strip()

    command = setting("HERMES_COPILOT_ACP_COMMAND") or setting("COPILOT_CLI_PATH") or "copilot"
    raw_args = setting("HERMES_COPILOT_ACP_ARGS")
    args = shlex.split(raw_args) if raw_args else []
    return command, args or list(COPILOT_ACP_DEFAULT_ARGS)


def copilot_acp_command_problem(command: str, *, packaged: bool) -> str:
    """'' when Hermes' Popen([command, *args]) can start it, else a reason code.

    No shell, quote removal or ``~`` expansion happens in Hermes. A relative
    path depends on the agent's working directory, so only an absolute path or a
    bare name is accepted, and the bundled engine (sealed PATH) needs the former.
    """
    path = Path(command)
    if path.is_absolute():
        return "" if path.is_file() else "missing"
    if packaged or len(path.parts) != 1:
        return "not_absolute"
    if os.name != "nt":
        return "" if shutil.which(command) else "missing"
    # CreateProcess searches PATH and appends only ".exe" to a bare name, so an
    # npm "copilot.cmd" shim found by shutil.which would still fail to start.
    names = (command,) if path.suffix else (command + ".exe",)
    for directory in filter(None, os.environ.get("PATH", "").split(os.pathsep)):
        if any((Path(directory) / name).is_file() for name in names):
            return ""
    return "missing"


def resolve_api_server_key(explicit: str = "", configured_url: str = "") -> str:
    """Resolve the bearer token expected by the live Hermes gateway.

    An existing installation may own a generated token in its ``.env``.
    Ignoring it makes a healthy gateway answer 401 and look offline.
    """
    explicit_key = (explicit or "").strip()
    if explicit_key:
        return explicit_key
    if configured_url and not is_loopback_url(configured_url):
        return ""
    return (
        os.environ.get("API_SERVER_KEY", "").strip()
        or _read_env_file().get("API_SERVER_KEY", "").strip()
        or ""
    )


def resolve_local_api_server_url(configured: str = "") -> str:
    """Respect remote/custom URLs and fold Hermes' local port into defaults."""
    current = (configured or "").strip().rstrip("/")
    default_urls = {
        "",
        "http://127.0.0.1:8642",
        "http://localhost:8642",
    }
    if current not in default_urls:
        if not is_safe_service_url(current):
            raise ValueError("Hermes remoto requiere HTTPS y una URL sin credenciales embebidas.")
        return current
    try:
        port = int(_read_env_file().get("API_SERVER_PORT") or 8642)
    except (TypeError, ValueError):
        port = 8642
    if not 1 <= port <= 65535:
        port = 8642
    return f"http://127.0.0.1:{port}"


def _read_env_file() -> dict[str, str]:
    """Parse ``~/.hermes/.env`` into a flat dict.

    Comment lines (``#``) and blank lines are skipped. Inline comments
    are NOT stripped because users sometimes write
    ``KEY=value # this is the value`` and we'd corrupt it. Hermes'
    own dotenv parser keeps such tails as part of the value too.
    """
    path = get_env_path()
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        m = _ENV_LINE_RE.match(s)
        if not m:
            continue
        key, val = m.group(1), m.group(2)
        # Strip wrapping quotes ONLY if the value is a well-formed
        # quoted dotenv string. A naive ``val[0] == val[-1] and v[0]
        # in "'\""`` test corrupts values that happen to start and end
        # with the same character without being a quoted string at
        # all (e.g. ``KEY='O'Brien'`` would become ``O'Brien'`` rather
        # than the intended literal ``'O'Brien'``).
        #
        # We accept two well-formed shapes here, matching python-dotenv:
        #   "double-quoted with optional escaped \" and \\ inside"
        #   'single-quoted with literal everything, no escapes'
        # Anything else is returned verbatim.
        if len(val) >= 2 and val[0] == '"' and val[-1] == '"':
            inner = val[1:-1]
            # Check the inner doesn't contain an UNescaped ``"`` —
            # otherwise the literal isn't actually closed at the end.
            i = 0
            balanced = True
            while i < len(inner):
                if inner[i] == "\\" and i + 1 < len(inner):
                    i += 2
                    continue
                if inner[i] == '"':
                    balanced = False
                    break
                i += 1
            if balanced:
                val = inner.replace('\\"', '"').replace("\\\\", "\\").replace("\\n", "\n")
        elif len(val) >= 2 and val[0] == "'" and val[-1] == "'":
            inner = val[1:-1]
            if "'" not in inner:  # single-quoted is literal — no escapes
                val = inner
        out[key] = val
    return out


def _write_env_file(env: dict[str, str]) -> None:
    """Write ``env`` back to ``~/.hermes/.env`` preserving file shape.

    We DO try to keep existing comments + ordering: read the file
    line by line, replace KEY=VALUE lines whose key is in ``env``,
    append any keys we want that weren't already present. New keys
    get a leading blank line + a one-line comment so a hand-editing
    user can tell the wizard added them.
    """
    path = get_env_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    existing_lines = (
        path.read_text(encoding="utf-8").splitlines()
        if path.exists() else []
    )
    seen: set[str] = set()
    out_lines: list[str] = []

    for line in existing_lines:
        m = _ENV_LINE_RE.match(line.strip())
        if m and m.group(1) in env:
            key = m.group(1)
            seen.add(key)
            out_lines.append(f"{key}={_quote_if_needed(env[key])}")
        else:
            out_lines.append(line)

    # Append new keys we weren't able to update in-place.
    new_keys = [k for k in env if k not in seen]
    if new_keys:
        if out_lines and out_lines[-1].strip() != "":
            out_lines.append("")
        out_lines.append("# Added by Project IRIS wizard")
        for k in new_keys:
            out_lines.append(f"{k}={_quote_if_needed(env[k])}")

    # Always end with a trailing newline so editors / git don't whine.
    _atomic_private_write(path, "\n".join(out_lines) + "\n")


def _atomic_private_write(path: Path, text: str) -> None:
    """Replace a Hermes credential/config file atomically and privately."""
    temporary: Path | None = None
    try:
        fd, name = tempfile.mkstemp(prefix=f".{path.name}.iris-", suffix=".tmp", dir=path.parent)
        temporary = Path(name)
        if os.name != "nt":
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            path.chmod(0o600)
    finally:
        if temporary and temporary.exists():
            temporary.unlink(missing_ok=True)


def _quote_if_needed(value: str) -> str:
    """Wrap value in double quotes if it contains spaces or shell-special chars."""
    if value == "":
        return ""
    if any(c in value for c in (" ", "\t", "#", "$", "`")):
        # Escape internal quotes minimally
        escaped = value.replace('"', '\\"')
        return f'"{escaped}"'
    return value


# ════════════════════════════════════════════════════════════
# config.yaml reader / writer
# ════════════════════════════════════════════════════════════

def _read_config_yaml() -> dict[str, Any]:
    """Load ``~/.hermes/config.yaml`` as a dict. Empty / missing → {}.

    PyYAML is a transitive dependency through Hermes itself; if the
    user runs IRIS without Hermes installed we degrade gracefully
    by returning an empty dict and logging a warning.
    """
    path = get_config_yaml_path()
    if not path.exists():
        return {}
    try:
        import yaml  # type: ignore[import-not-found]
    except ImportError:
        logger.warning(
            "PyYAML not installed — cannot read config.yaml. "
            "Install with: pip install pyyaml"
        )
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except yaml.YAMLError as exc:
        logger.warning("config.yaml is malformed: %s", exc)
        return {}


def _write_config_yaml(data: dict[str, Any]) -> None:
    """Dump ``data`` back to ``~/.hermes/config.yaml`` with key order
    preserved as much as PyYAML allows."""
    path = get_config_yaml_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import yaml  # type: ignore[import-not-found]
    except ImportError as exc:
        # This is a required write, not a best-effort cache update.  Returning
        # here made callers report a successful provider switch even though
        # Hermes kept using the old model/provider from config.yaml.
        raise RuntimeError(
            "Cannot write Hermes config.yaml because PyYAML is not installed."
        ) from exc
    text = yaml.safe_dump(
        data,
        default_flow_style=False,
        sort_keys=False,        # preserve insertion order
        allow_unicode=True,
    )
    _atomic_private_write(path, text)


def read_provider_base_url(wizard_id: str) -> str:
    """Return the selected provider's configured endpoint, if it has one."""
    from wizard.providers import get_provider, hermes_id_for

    provider = (wizard_id or "").strip().lower()
    if not provider:
        return ""
    info = get_provider(provider) or {}
    model = _read_config_yaml().get("model")
    if isinstance(model, dict):
        selected = str(model.get("provider") or "").strip().lower()
        expected = hermes_id_for(provider).strip().lower()
        if selected in {provider, expected}:
            value = str(model.get("base_url") or "").strip()
            if value:
                return value
    env_name = str(info.get("base_url_env_key") or "").strip()
    if env_name:
        value = _read_env_file().get(env_name, "").strip()
        if value:
            return value
    return str(info.get("base_url") or "").strip()


def azure_foundry_uses_entra() -> bool:
    """True when an existing Azure Foundry config intentionally uses Entra."""
    model = _read_config_yaml().get("model")
    return bool(
        isinstance(model, dict)
        and str(model.get("provider") or "").strip().lower() == "azure-foundry"
        and str(model.get("auth_mode") or "").strip().lower() == "entra_id"
    )


# ════════════════════════════════════════════════════════════
# Public API
# ════════════════════════════════════════════════════════════

# Map from IRIS's `llm_provider` short name to (env var, hermes
# provider key) tuples. The right-hand provider id MUST be one
# Hermes recognises — verified against ``hermes_cli/auth.py
# ::PROVIDER_REGISTRY`` (auth_type + api_key_env_vars) and
# ``hermes_cli/models.py::CANONICAL_PROVIDERS``. Anything else
# fails with ``Unknown provider 'X'`` at gateway boot or first
# request.
#
# Notable wizard-id → hermes-id remappings (where IRIS's short
# name doesn't match Hermes' real slug):
#
#   * ``openai``  → ``openai-api``: direct OpenAI API-key provider in
#                   Hermes 0.19+. ``openai-codex`` is the separate
#                   ChatGPT subscription / OAuth path.
#   * ``google``  → ``gemini``: Hermes' Google AI Studio plugin is
#                   named ``gemini`` in its registry.
#   * ``moonshot`` → ``kimi-coding``: Moonshot's global Kimi API
#                    surfaces under Hermes' ``kimi-coding`` slug.
#   * ``zhipu``   → ``zai``: Hermes uses the rebrand name.
#   * ``bedrock`` → ``bedrock``: NOT ``amazon-bedrock``.
#   * ``ollama``  → ``ollama-cloud``: Hermes has no plain ``ollama``;
#                   only the cloud-hosted endpoint is first-class.
#                   Fully-local Ollama uses ``custom`` with base_url.
_PROVIDER_KEY_MAP: dict[str, tuple[str, str]] = {
    # Popular
    "openai":          ("OPENAI_API_KEY",      "openai-api"),  # see note above
    "anthropic":       ("ANTHROPIC_API_KEY",   "anthropic"),
    "google":          ("GOOGLE_API_KEY",      "gemini"),
    "deepseek":        ("DEEPSEEK_API_KEY",    "deepseek"),
    # Cloud (direct API providers in Hermes' registry)
    "xai":             ("XAI_API_KEY",         "xai"),
    "xai-oauth":       ("",                    "xai-oauth"),
    "nvidia":          ("NVIDIA_API_KEY",      "nvidia"),
    "bedrock":         ("",                    "bedrock"),
    "azure-foundry":   ("AZURE_FOUNDRY_API_KEY", "azure-foundry"),
    "copilot":         ("GITHUB_TOKEN",        "copilot"),
    "copilot-acp":     ("",                    "copilot-acp"),  # external_process, no env
    "huggingface":     ("HF_TOKEN",            "huggingface"),
    "xiaomi":          ("XIAOMI_API_KEY",      "xiaomi"),
    "tencent-tokenhub": ("TOKENHUB_API_KEY",   "tencent-tokenhub"),
    "arcee":           ("ARCEEAI_API_KEY",     "arcee"),
    "gmi":             ("GMI_API_KEY",         "gmi"),
    # Chinese
    "minimax":             ("MINIMAX_API_KEY",     "minimax"),
    "minimax-cn":          ("MINIMAX_CN_API_KEY",  "minimax-cn"),
    "alibaba":             ("DASHSCOPE_API_KEY",   "alibaba"),
    "alibaba-coding-plan": ("ALIBABA_CODING_PLAN_API_KEY", "alibaba-coding-plan"),
    "moonshot":            ("KIMI_API_KEY",        "kimi-coding"),
    "kimi-coding-cn":      ("KIMI_CN_API_KEY",     "kimi-coding-cn"),
    "zhipu":               ("GLM_API_KEY",         "zai"),
    "stepfun":             ("STEPFUN_API_KEY",     "stepfun"),
    # Aggregators
    "openrouter":          ("OPENROUTER_API_KEY",  "openrouter"),
    "ai-gateway":          ("AI_GATEWAY_API_KEY",  "ai-gateway"),
    "kilocode":            ("KILOCODE_API_KEY",    "kilocode"),
    "opencode-zen":        ("OPENCODE_ZEN_API_KEY", "opencode-zen"),
    "opencode-go":         ("OPENCODE_GO_API_KEY", "opencode-go"),
    # Local / self-hosted. ``custom`` covers anything OpenAI-compat
    # we don't have a dedicated plugin for (Ollama loopback, vLLM,
    # llama.cpp, etc.). Hermes' alias table maps "ollama" / "vllm" /
    # "llamacpp" → "custom", but we always write "custom" explicitly
    # so config.yaml stays unambiguous.
    "ollama":          ("OLLAMA_API_KEY",      "ollama-cloud"),
    "ollama-local":    ("",                    "custom"),
    "vllm":            ("",                    "custom"),
    "llamacpp":        ("",                    "custom"),
    "lmstudio":        ("",                    "lmstudio"),  # local, key optional
    "custom":          ("",                    "custom"),
    # OAuth-only providers (no API-key path; env var blank)
    "nous":              ("",  "nous"),
    "openai-codex":      ("",  "openai-codex"),
    "qwen-oauth":        ("",  "qwen-oauth"),
    "google-gemini-cli": ("",  "google-gemini-cli"),
    "minimax-oauth":     ("",  "minimax-oauth"),
}


def _write_hermes_config_unlocked(
    *,
    llm_provider: str = "",
    llm_api_key: str = "",
    llm_model: str = "",
    llm_use_oauth: bool = False,
    api_server_key: str = "",
    base_url: str = "",
) -> dict[str, Any]:
    """Sync IRIS's settings into Hermes' .env + config.yaml.

    Args:
        llm_provider: One of the keys in ``_PROVIDER_KEY_MAP`` (or
            empty to leave provider settings untouched).
        llm_api_key: API key for the chosen provider. Stored in
            ``~/.hermes/.env`` under ``<PROVIDER>_API_KEY``.
        llm_model: Model identifier — Hermes' format is
            ``<provider>/<model-id>`` (e.g. ``openai/gpt-4o-mini``).
            We compose it from ``llm_provider`` + ``llm_model`` if
            the value passed isn't already prefixed.
        llm_use_oauth: Resolve the provider through its OAuth-specific
            Hermes slug and do not associate a pasted API key with this
            selection. Existing keys in .env are preserved for switching back.
        api_server_key: Bearer token for the api-server. When omitted on a
            fresh install IRIS generates a private random token.
        base_url: Endpoint override for the ``custom`` provider
            (vLLM, llama.cpp, local Ollama, anything OpenAI-compat).
            Written to ``model.base_url`` in config.yaml. Auto-filled
            with the wizard's per-provider default when the caller
            omits it but the provider is one of ``ollama-local`` /
            ``vllm`` / ``llamacpp`` / ``lmstudio``.

    Returns:
        A status dict ``{".env": ..., "config.yaml": ..., "model": ...}``
        for logging / UI feedback. Statuses are ``"changed"`` /
        ``"unchanged"`` / ``"skipped"``.
    """
    summary: dict[str, Any] = {}

    # ── .env ────────────────────────────────────────────────
    env = _read_env_file()
    target_env = dict(env)  # copy to detect changes

    # Always ensure the api-server is on.
    target_env["API_SERVER_ENABLED"] = "true"
    # API_SERVER_KEY: respect whatever Hermes generated during its own
    # `hermes setup`. The ONLY way this
    # gets overridden is if the caller explicitly passed a non-empty
    # ``api_server_key`` (e.g. user typed one in the wizard's advanced
    # box). Otherwise we leave Hermes' token alone — overwriting it
    # was the root cause of the "chat doesn't respond" 401-loop bug
    # where the bridge sent a fixed fallback while Hermes was authenticating
    # against its own generated random token.
    if api_server_key:
        target_env["API_SERVER_KEY"] = api_server_key
    else:
        target_env.setdefault("API_SERVER_KEY", secrets.token_urlsafe(32))
    target_env.setdefault("API_SERVER_MODEL_NAME", "hermes-agent")

    # Provider key (only if the user chose the API-key path and supplied one).
    # Resolve through providers.py so auto-discovered `oc-*` entries use the
    # env var declared by the installed Hermes version.
    provider = (llm_provider or "").strip().lower()
    from wizard.providers import env_key_for, get_provider, hermes_id_for
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", provider or "") and provider:
        raise ValueError(f"Invalid provider id: {provider!r}")
    if llm_model == "__custom__":
        raise ValueError("A custom model id must be entered before saving.")
    hermes_slug = hermes_id_for(provider, use_oauth=llm_use_oauth) if provider else ""
    if llm_api_key and not llm_use_oauth:
        env_var = env_key_for(provider)
        if env_var:  # ollama / lmstudio don't need keys
            target_env[env_var] = llm_api_key

    provider_info = get_provider(provider) or {}
    base_url_env_key = str(provider_info.get("base_url_env_key") or "").strip()
    if base_url_env_key and base_url.strip():
        target_env[base_url_env_key] = base_url.strip()

    # Some Hermes adapters (notably Anthropic) use the same provider slug for
    # API-key and OAuth profiles. Keep a non-secret IRIS marker in Hermes'
    # dotenv so a restart can distinguish the user's explicit choice even if
    # an older API key is intentionally retained for switching back later.
    if hermes_slug:
        selected_auth = "oauth" if llm_use_oauth else "api"
        target_env["IRIS_LLM_AUTH_SELECTION"] = f"{hermes_slug}|{selected_auth}"

    if target_env != env:
        _write_env_file(target_env)
        summary[".env"] = "changed"
    else:
        summary[".env"] = "unchanged"

    # ── config.yaml ─────────────────────────────────────────
    # Hermes' schema (verified against the bundled
    # ``cli-config.yaml.example``) treats ``model`` as a *block*,
    # not a flat string::
    #
    #     model:
    #       default: "openai/gpt-4o-mini"
    #       provider: "openai"     # or "auto"
    #
    # An earlier version of this writer collapsed the block into
    # ``model: "openai/gpt-4o-mini"``, which Hermes silently treats
    # as malformed and falls back to its bundled default — that's
    # the bug that produced "I picked OpenAI but Hermes is still
    # routing through OpenRouter and returning empty content".
    cfg = _read_config_yaml()
    target_cfg = dict(cfg)

    # Pull (or create) the model block, preserving any keys we don't
    # touch (e.g. ``base_url`` for custom endpoints, ``temperature``
    # if the user set one manually).
    raw_model_block = target_cfg.get("model")
    if isinstance(raw_model_block, dict):
        model_block = dict(raw_model_block)
    else:
        # Either missing or the broken flat-string form — start fresh.
        model_block = {}
    previous_provider = str(model_block.get("provider") or "").strip().lower()
    previous_base_url = str(model_block.get("base_url") or "").strip()

    # Look up the real Hermes slug for this wizard provider id.
    # Wizard ids and Hermes slugs are NOT always the same: e.g.
    # "openai" → "openai-api", "google" → "gemini",
    # "ollama-local" / "vllm" / "llamacpp" → "custom". Writing the
    # raw wizard id into ``model.provider`` would be rejected by
    # Hermes with "Unknown provider 'X'" at first request.
    if llm_model:
        # Hermes' first-class provider plugins (anthropic, gemini,
        # deepseek, xai, …) expect the bare model id — they read
        # ``provider:`` to know which adapter to use. Adding a
        # ``<provider>/`` prefix makes Gemini's adapter send
        # ``gemini/gemini-2.0-flash`` to googleapis.com which
        # responds 404.
        #
        # Multi-vendor routers (openrouter, vercel-ai-gateway, kilocode,
        # opencode-zen, opencode-go) DO need a ``<vendor>/<model>``
        # form because that's the routing key the upstream API expects.
        # The wizard's provider data should already include the vendor
        # prefix in those entries' model values; we only prepend the
        # slug here as a fallback when the model id has no slash.
        _ROUTER_SLUGS = {
            "openrouter", "vercel-ai-gateway", "ai-gateway",
            "kilocode", "opencode-zen", "opencode-go",
        }
        full_model = llm_model
        if hermes_slug in _ROUTER_SLUGS and "/" not in llm_model:
            # Best-effort fallback: keep model as-is. OpenRouter and
            # friends will reject it with a clearer error than ours
            # if the vendor prefix is missing.
            full_model = llm_model
        model_block["default"] = full_model
        # Setting ``provider`` explicitly avoids Hermes' "auto" mode
        # routing through OpenRouter just because the model id
        # carries a vendor prefix.
        if hermes_slug:
            model_block["provider"] = hermes_slug
        summary["model"] = full_model

        # ``base_url`` lives in ``model.base_url`` in config.yaml.
        # We treat it differently per provider class:
        #
        #   * First-class plugins (anthropic, gemini, deepseek, …):
        #     remove any leftover URL — Hermes' bundled template
        #     seeds an OpenRouter base_url which would silently
        #     re-route calls to the wrong endpoint.
        #   * ``custom`` provider: REQUIRES a base_url. Caller
        #     provides one explicitly, OR we fall back to the
        #     wizard's per-provider default below.
        #   * ``lmstudio`` / ``ollama-cloud``: optional, only set
        #     if the user hasn't customised it.
        custom_defaults = {
            "ollama-local": "http://127.0.0.1:11434/v1",
            "vllm":         "http://127.0.0.1:8000/v1",
            "llamacpp":     "http://127.0.0.1:8080/v1",
        }
        wizard_id = (llm_provider or "").strip().lower()
        configured_env_url = (
            target_env.get(base_url_env_key, "").strip()
            if base_url_env_key else ""
        )
        url_to_set = (
            (base_url or "").strip()
            or custom_defaults.get(wizard_id, "")
            or configured_env_url
            or (previous_base_url if previous_provider == hermes_slug else "")
        )

        target_hermes_id = hermes_slug
        if target_hermes_id in {"custom", "azure-foundry"}:
            if url_to_set:
                model_block["base_url"] = url_to_set
            elif target_hermes_id == "azure-foundry":
                # Never carry another provider's endpoint into Azure.
                model_block.pop("base_url", None)
        elif target_hermes_id and target_hermes_id not in {"lmstudio", "ollama-cloud"}:
            model_block.pop("base_url", None)
        elif target_hermes_id == "lmstudio":
            model_block.setdefault("base_url", "http://127.0.0.1:1234/v1")
        elif target_hermes_id == "ollama-cloud":
            model_block.setdefault("base_url", "https://ollama.com/api/v1")

        # Hermes 0.20 stores credentials for an explicit custom endpoint in
        # ``model.api_key``. ``CUSTOM_API_KEY`` is not a runtime contract.
        # Remove this inline secret whenever the destination is built-in or a
        # known no-auth local alias so it cannot contaminate a later route.
        if target_hermes_id == "custom" and wizard_id == "custom":
            if llm_api_key and not llm_use_oauth:
                model_block["api_key"] = llm_api_key
            else:
                model_block.pop("api_key", None)
                model_block.pop("api", None)
        else:
            model_block.pop("api_key", None)
            model_block.pop("api", None)
        if target_hermes_id == "azure-foundry":
            # This wizard exposes Azure's API-key route, not Entra. Stale
            # keyless-auth settings would otherwise override the selected key.
            model_block.pop("auth_mode", None)
            model_block.pop("entra", None)

    target_cfg["model"] = model_block

    if target_cfg != cfg:
        _write_config_yaml(target_cfg)
        summary["config.yaml"] = "changed"
    else:
        summary["config.yaml"] = "unchanged"

    logger.info("Hermes config sync: %s", summary)
    return summary


class ProviderIsMain(RuntimeError):
    """The provider became the main one before its key was written: the key is the main writer's."""


def write_provider_api_key(provider: str, api_key: str) -> str:
    """Store one more provider's API key in Hermes' ``.env``: only that variable changes.

    The main provider stays as it is (``model`` in config.yaml, the auth selection marker
    and every other key): Hermes counts a provider with its key as configured and Iris
    offers its models per conversation. Returns ``"changed"`` or ``"unchanged"``.

    Whether the provider is the main one is decided again under the lock the main writer
    also takes (R03): a main provider saved meanwhile raises ``ProviderIsMain`` and
    nothing is written, so an earlier check never replaces the new main provider's key.
    """
    from wizard.providers import env_key_for, hermes_id_for

    provider = (provider or "").strip().lower()
    env_var = env_key_for(provider)
    if not env_var:
        raise ValueError(f"Provider without an API key variable: {provider!r}")
    if not api_key or any(c.isspace() or not c.isprintable() for c in api_key) or len(api_key) > 4096:
        raise ValueError("The API key must be one line without spaces.")
    with interprocess_file_lock(get_hermes_home() / ".iris-config.lock"):
        model = _read_config_yaml().get("model")
        main = str(model.get("provider") or "").strip().lower() if isinstance(model, dict) else ""
        if main and main in {hermes_id_for(provider), hermes_id_for(provider, use_oauth=True)}:
            raise ProviderIsMain(f"{provider!r} is the main provider now")
        env = _read_env_file()
        if env.get(env_var) == api_key:
            return "unchanged"
        _write_env_file({env_var: api_key})  # every other line is kept as written
        return "changed"


def write_hermes_config(
    *,
    llm_provider: str = "",
    llm_api_key: str = "",
    llm_model: str = "",
    llm_use_oauth: bool = False,
    api_server_key: str = "",
    base_url: str = "",
) -> dict[str, Any]:
    """Serialize the complete Hermes read/merge/write transaction."""
    with interprocess_file_lock(get_hermes_home() / ".iris-config.lock"):
        return _write_hermes_config_unlocked(
            llm_provider=llm_provider,
            llm_api_key=llm_api_key,
            llm_model=llm_model,
            llm_use_oauth=llm_use_oauth,
            api_server_key=api_server_key,
            base_url=base_url,
        )


def read_api_server_host() -> str:
    """Return the configured ``API_SERVER_HOST`` from .env.

    Used by ``core/websocket_server.py::network_info`` to advise
    "are we LAN-reachable for chat too". Empty string if not set,
    which means Hermes uses its default 127.0.0.1.
    """
    env = _read_env_file()
    return env.get("API_SERVER_HOST", "")


def write_api_server_host(host: str) -> bool:
    """Persist the bind host consumed by Hermes' OpenAI api-server.

    Only loopback and all-interface binds are accepted. Returning whether
    the file changed lets the UI state exactly which process needs restart.
    """
    normalized = (host or "").strip()
    if normalized not in {"127.0.0.1", "0.0.0.0"}:
        raise ValueError("API_SERVER_HOST must be 127.0.0.1 or 0.0.0.0")
    with interprocess_file_lock(get_hermes_home() / ".iris-config.lock"):
        env = _read_env_file()
        target = dict(env)
        target["API_SERVER_HOST"] = normalized
        # Binding Hermes to every interface without bearer auth would expose
        # its API to the whole local network. Older/partial installations may
        # predate the token generated by the current wizard, so repair that
        # invariant atomically before publishing the LAN bind.
        if normalized == "0.0.0.0" and not target.get("API_SERVER_KEY", "").strip():
            target["API_SERVER_KEY"] = secrets.token_urlsafe(32)
        if target == env:
            return False
        _write_env_file(target)
        return True


def has_hermes_workspace() -> bool:
    """True iff ~/.hermes/ exists with at least a config.yaml.

    Used as the gate for "is Hermes ready to talk to" in wizard
    health checks and IRIS's workspace_sync skip path.
    """
    return get_config_yaml_path().exists()


def run_claw_migrate(*, dry_run: bool = False) -> tuple[bool, str]:
    """Run ``hermes claw migrate`` (or ``--dry-run``) and capture output.

    Returns ``(ok, combined_output)``. Output truncated at 8 KB
    because the migrate report can be long.

    Forces ``PYTHONIOENCODING=utf-8`` because Hermes prints emojis
    and Windows' default cp1252 makes the migrate command crash
    before showing any results.
    """
    runtime_policy.require_runtime_capability("external_hermes_install")
    cli = shutil.which("hermes") or shutil.which("hermes.exe")
    if not cli:
        return False, "hermes CLI not on PATH"
    argv = [cli, "claw", "migrate", "--yes"]
    if dry_run:
        argv = [cli, "claw", "migrate", "--dry-run"]
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=120,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            encoding="utf-8",
            errors="replace",
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return False, f"migration failed: {exc}"
    output = (result.stdout or "") + (result.stderr or "")
    if len(output) > 8192:
        output = output[:8192] + "\n[...truncated]"
    return result.returncode == 0, output


# ════════════════════════════════════════════════════════════
# Auth-profile + env-key helpers (consumed by health_check.py)
# ════════════════════════════════════════════════════════════

def provider_env_key(provider: str) -> str:
    """Return the env-var name that holds the API key for ``provider``.

    Empty string means the provider is local-only (Ollama / LM Studio)
    or uses OAuth exclusively. Mirrors ``_PROVIDER_KEY_MAP`` plus a
    handful of OAuth/aggregator providers seen in
    ``provider_sync.DISCOVERY_META``.
    """
    p = (provider or "").strip().lower()
    if p in _PROVIDER_KEY_MAP:
        return _PROVIDER_KEY_MAP[p][0]
    # Common extras the wizard surfaces but Hermes' env doesn't carry
    # canonical names for. Keep the list short — unknown providers
    # return "" so health_check shows "no env_key" gracefully.
    extras = {
        "groq":             "GROQ_API_KEY",
        "cerebras":         "CEREBRAS_API_KEY",
        "github-copilot":   "GITHUB_TOKEN",
        "vercel":           "VERCEL_API_KEY",
        "minimax-cn":       "MINIMAX_API_KEY",
        "zai":              "ZHIPUAI_API_KEY",
    }
    return extras.get(p, "")


def has_hermes_auth_profile(provider: str, *, auth_type: str | None = None, home: Path | None = None) -> bool:
    """True iff Hermes has stored credentials for ``provider``.

    Hermes 0.12.x keeps its credential store at ``<HERMES_HOME>/auth.json``
    with this shape::

        {
          "version": 1,
          "credential_pool": {
            "anthropic":     [{"auth_type": "oauth", "access_token": "...", ...}, ...],
            "openai-codex":  [{...}],
            ...
          }
        }

    We parse the JSON properly and look for at least one non-empty
    pool entry under the requested provider slug. When ``auth_type`` is
    supplied (normally ``"oauth"``), the stored entry must also match that
    type; an API credential must never impersonate an OAuth login.
    ``provider`` here
    must be the **Hermes slug** (e.g. ``"openai-codex"``,
    ``"anthropic"``), not the wizard id — callers that work with
    wizard ids should run them through ``providers.hermes_id_for``
    first, with ``use_oauth=True`` when applicable.

    Falls back to a substring probe of older / unknown auth store
    formats so the function stays useful if Hermes changes its
    on-disk layout in a future release.
    """
    if not provider:
        return False
    p = provider.strip().lower()
    expected_type = (auth_type or "").strip().lower()
    home = Path(home) if home is not None else get_hermes_home()  # callers that already resolved it
    auth_path = home / "auth.json"
    if not auth_path.exists():
        return False

    # Preferred path: parse JSON and look up the provider's pool.
    try:
        import json as _json
        data = _json.loads(auth_path.read_text(encoding="utf-8", errors="ignore"))
        pool = (data or {}).get("credential_pool") or {}
        # Hermes uses slug keys verbatim in the pool; honour case for
        # exact match, then fall through to a case-insensitive scan.
        entries = pool.get(p)
        if entries is None:
            for slug, items in pool.items():
                if str(slug).lower() == p:
                    entries = items
                    break
        if isinstance(entries, list) and entries:
            if not expected_type:
                return True
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                stored_type = str(entry.get("auth_type") or "").strip().lower()
                if stored_type == expected_type:
                    return True
                # OpenRouter browser authorization mints an API key, not an
                # OAuth refresh pair. Only its native PKCE source qualifies.
                if (p == "openrouter" and expected_type == "oauth"
                        and stored_type == "api_key"
                        and entry.get("source") == "manual:openrouter_pkce"
                        and entry.get("access_token")):
                    return True
                # A few old Hermes stores omitted auth_type. Their token shape
                # is still unambiguous enough to recognize without treating an
                # API key as OAuth (the bug this filter is preventing).
                if not stored_type and expected_type == "oauth" and (
                    entry.get("access_token") or entry.get("refresh_token")
                ):
                    return True
                if not stored_type and expected_type == "api" and entry.get("api_key"):
                    return True
        # Older versions stored providers under a flat ``providers``
        # dict instead of ``credential_pool``.
        legacy = (data or {}).get("providers") or {}
        if isinstance(legacy, dict):
            legacy_value = legacy.get(p)
            if legacy_value is None:
                for slug, value in legacy.items():
                    if str(slug).lower() == p:
                        legacy_value = value
                        break
            if legacy_value is not None:
                if not expected_type:
                    return True
                legacy_entries = legacy_value if isinstance(legacy_value, list) else [legacy_value]
                for entry in legacy_entries:
                    if not isinstance(entry, dict):
                        continue
                    stored_type = str(entry.get("auth_type") or "").strip().lower()
                    if stored_type == expected_type:
                        return True
                    if not stored_type and expected_type == "oauth" and (
                        entry.get("access_token") or entry.get("refresh_token")
                    ):
                        return True
                    # Hermes 0.21 keeps a first OpenAI Codex login only as the provider's own
                    # state, tokens nested: {"tokens": {...}, "auth_mode": "chatgpt"}. Its
                    # credential_pool entry appears later (a login only syncs existing ones).
                    # Same rule as Hermes' _read_codex_tokens: both tokens, non-empty text;
                    # anything less Hermes rejects, so it must not pass for a login here.
                    tokens = entry.get("tokens")
                    if (not stored_type and expected_type == "oauth" and isinstance(tokens, dict)
                            and str(entry.get("auth_mode") or "").strip().lower() not in {"api_key", "apikey"}
                            and all(isinstance(tokens.get(name), str) and tokens[name].strip()
                                    for name in ("access_token", "refresh_token"))):
                        return True
                    if not stored_type and expected_type == "api" and entry.get("api_key"):
                        return True
    except (ValueError, OSError):
        pass

    # Fallback: cheap substring probe for unknown/legacy formats.
    try:
        text = auth_path.read_text(encoding="utf-8", errors="ignore")
        # Unknown legacy formats can prove that *some* credential exists but
        # cannot prove its type. Be conservative for OAuth validation.
        return not expected_type and p in text.lower()
    except OSError:
        return False


__all__ = [
    "API_SERVER_KEY_DEFAULT",
    "get_hermes_home",
    "get_env_path",
    "get_config_yaml_path",
    "hermes_cli_available",
    "write_hermes_config",
    "read_hermes_dotenv",
    "resolve_api_server_key",
    "resolve_local_api_server_url",
    "read_provider_base_url",
    "azure_foundry_uses_entra",
    "read_api_server_host",
    "write_api_server_host",
    "has_hermes_workspace",
    "run_claw_migrate",
    "provider_env_key",
    "has_hermes_auth_profile",
]
