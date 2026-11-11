"""
LLM Provider Registry
─────────────────────

Single source of truth for the list of LLM providers the wizard supports.

The UI fetches ``/api/providers`` and renders whatever this module ships
— curated entries here, plus anything Hermes' live catalog discovers at
runtime (merged in ``merged_providers``). The OAuth route in
``wizard/app.py`` maps each wizard provider id to a Hermes provider slug
via ``hermes_id_for()`` defined below, so picking any provider in the UI
gives ``hermes auth add`` the exact slug it expects.

The registry is data-only on purpose — no I/O, no async, safe to import
from anywhere. Mutations happen only by editing this file.
"""

from __future__ import annotations

import asyncio
from typing import Any, Literal

# ── Categories (labels shown in the UI) ──────────────────────────────
CATEGORIES: dict[str, str] = {
    "popular":    "⭐ Populares",
    "cloud":      "☁️ Cloud",
    "chinese":    "🇨🇳 IA China",
    "aggregator": "🌐 Agregadores (100+ modelos)",
    "local":      "🏠 Local / Self-hosted",
}

# ── Auth modes ──
#   "api"       → only API key (paste a key, no other option)
#   "api-optional" → endpoint may accept a key, but it is not required
#   "oauth"     → only OAuth / device-flow login (no API key path)
#   "api+oauth" → API key OR OAuth login via `hermes auth add --type oauth`
#   "aws"       → AWS SDK credential chain (not a single API key)
#   "external"  → authentication is owned by an external CLI/process
#   "none"      → local provider, no auth needed at all
AuthMode = Literal["api", "api-optional", "oauth", "api+oauth", "aws", "external", "none"]


# ── Provider catalog ─────────────────────────────────────────────────
# Fields:
#   id              Wizard-side ID (stable — persisted in ~/.iris/config.json)
#   name            Display name (shown in the radio label)
#   icon            Short emoji shown before the name
#   category        One of CATEGORIES keys
#   auth            AuthMode
#   url             Where the user gets an API key / signs up
#   hint            Short one-liner shown under the name
#   hermes_id       Provider ID to pass to `hermes auth add --provider <id>`
#                   (empty ⇒ same as `id`)
#   env_key         Env var Hermes reads for this provider's API key
#                   (empty for local providers)
#   models          List of {value, label, tier} dicts
PROVIDERS: list[dict[str, Any]] = [
    # ── Popular / Recommended ──────────────────────────────────────
    {
        "id": "anthropic", "name": "Anthropic (Claude)", "icon": "🟤",
        "category": "popular", "auth": "api+oauth",
        "url": "https://platform.claude.com/settings/keys",
        "hint": "platform.claude.com",
        "hermes_id": "anthropic", "env_key": "ANTHROPIC_API_KEY",
        "models": [
            # Labels only: the selectable list is the one Hermes publishes
            # (models it does not list are never added from here).
            {"value": "claude-sonnet-5",         "label": "Claude Sonnet 5 ⭐ (Recomendado)",      "tier": "mid"},
            {"value": "claude-fable-5.1",        "label": "Claude Fable 5.1 (Máxima capacidad)",   "tier": "premium"},
            {"value": "claude-opus-5-5",         "label": "Claude Opus 5.5",                       "tier": "premium"},
            {"value": "claude-sonnet-5-5",       "label": "Claude Sonnet 5.5",                     "tier": "mid"},
            {"value": "claude-haiku-5-5",        "label": "Claude Haiku 5.5 (Ultra-rápido)",       "tier": "fast"},
            {"value": "claude-sonnet-4-6",       "label": "Claude Sonnet 4.6",                     "tier": "mid"},
            {"value": "claude-opus-4-6",         "label": "Claude Opus 4.6",                       "tier": "premium"},
            {"value": "claude-haiku-4-5",        "label": "Claude Haiku 4.5",                      "tier": "fast"},
        ],
    },
    {
        "id": "openai", "name": "OpenAI", "icon": "🟢",
        "category": "popular", "auth": "api+oauth",
        # Hermes 0.19+ separates the API path (``openai-api``) from the
        # ChatGPT subscription / device-login path (``openai-codex``).
        # The public model catalog still groups both under ``openai``.
        "url": "https://platform.openai.com/api-keys",
        "hint": "API key de platform.openai.com o inicio de sesión con ChatGPT (OAuth)",
        "hermes_id": "openai-api", "hermes_id_oauth": "openai-codex",
        "catalog_id": "openai", "env_key": "OPENAI_API_KEY",
        "models": [
            {"value": "gpt-5.4",         "label": "GPT-5.4 ⭐ (Flagship)",                 "tier": "premium"},
            {"value": "gpt-5.4-mini",    "label": "GPT-5.4 Mini",                          "tier": "fast"},
            {"value": "gpt-5.3-instant", "label": "GPT-5.3 Instant (Velocidad máxima)",    "tier": "fast"},
            {"value": "gpt-5.3-codex",   "label": "GPT-5.3 Codex (Agente de código)",      "tier": "reasoning"},
            {"value": "gpt-5.2",         "label": "GPT-5.2",                               "tier": "premium"},
            {"value": "gpt-5.2-mini",    "label": "GPT-5.2 Mini",                          "tier": "fast"},
            {"value": "o3-mini",         "label": "o3-mini (razonamiento)",                "tier": "reasoning"},
            {"value": "gpt-4o",          "label": "GPT-4o (Legacy — aún disponible en API)", "tier": "mid"},
        ],
    },
    {
        "id": "google", "name": "Google (Gemini)", "icon": "🔵",
        # Hermes' API-key plugin for Google AI Studio is named
        # ``gemini`` (not ``google``). Verified against
        # ``hermes_cli/auth.py::PROVIDER_REGISTRY``. ``hermes auth add``
        # does not expose Google OAuth. Gemini CLI is
        # an external-process integration, not a login flow the wizard can
        # complete, so this card promises only the supported API-key path.
        "category": "popular", "auth": "api",
        "url": "https://aistudio.google.com/apikey",
        "hint": "aistudio.google.com (tiene nivel gratuito)",
        "hermes_id": "gemini",
        "catalog_id": "google", "env_key": "GOOGLE_API_KEY",
        "models": [
            {"value": "gemini-3.1-pro-preview",        "label": "Gemini 3.1 Pro ⭐",        "tier": "premium"},
            {"value": "gemini-3.1-flash-preview",      "label": "Gemini 3.1 Flash",         "tier": "fast"},
            {"value": "gemini-3.1-flash-lite-preview", "label": "Gemini 3.1 Flash Lite",    "tier": "fast"},
            {"value": "gemini-3-flash-preview",        "label": "Gemini 3 Flash",           "tier": "fast"},
            {"value": "gemini-2.5-flash",              "label": "Gemini 2.5 Flash",         "tier": "fast"},
            {"value": "gemini-2.5-pro",                "label": "Gemini 2.5 Pro",           "tier": "mid"},
            {"value": "gemini-2.0-flash",              "label": "Gemini 2.0 Flash",         "tier": "fast"},
        ],
    },
    {
        "id": "deepseek", "name": "DeepSeek", "icon": "🔮",
        "category": "popular", "auth": "api",
        "url": "https://platform.deepseek.com/api-keys",
        "hint": "platform.deepseek.com",
        "hermes_id": "deepseek", "env_key": "DEEPSEEK_API_KEY",
        "models": [
            {"value": "deepseek-chat",     "label": "DeepSeek-V3 ⭐",              "tier": "mid"},
            {"value": "deepseek-reasoner", "label": "DeepSeek-R1 (razonamiento)",  "tier": "reasoning"},
        ],
    },

    # ── Hermes-native OAuth providers ──────────────────────────────
    # These six providers exist ONLY through Hermes (Nous Research's
    # agent runtime). Each authenticates via OAuth — no API key
    # field needed. Listed under "popular" so they appear next to
    # OpenAI / Anthropic without a separate scroll. The OAuth slug
    # in `hermes_id` matches what `hermes auth add <id>` expects.
    {
        "id": "nous", "name": "Nous Portal", "icon": "🟣",
        "category": "popular", "auth": "oauth",
        "url": "https://portal.nousresearch.com",
        "hint": "OAuth con tu cuenta Nous — incluye Hermes 4 + modelos custom",
        "hermes_id": "nous", "env_key": "",
        "models": [
            {"value": "hermes-4-405b",     "label": "Hermes 4 405B ⭐",            "tier": "premium"},
            {"value": "hermes-4-70b",      "label": "Hermes 4 70B",                "tier": "mid"},
            {"value": "hermes-3-405b",     "label": "Hermes 3 405B",               "tier": "mid"},
        ],
    },
    {
        "id": "qwen-oauth", "name": "Qwen (OAuth)", "icon": "🐉",
        "category": "popular", "auth": "oauth",
        "url": "https://qianwen.aliyun.com/",
        "hint": "OAuth con tu cuenta Alibaba — Qwen Max/Plus",
        "hermes_id": "qwen-oauth", "env_key": "",
        "models": [
            {"value": "qwen-max",   "label": "Qwen Max ⭐",  "tier": "premium"},
            {"value": "qwen-plus",  "label": "Qwen Plus",    "tier": "mid"},
            {"value": "qwen-turbo", "label": "Qwen Turbo",   "tier": "fast"},
        ],
    },
    {
        "id": "minimax-oauth", "name": "MiniMax (OAuth)", "icon": "🎙️",
        "category": "cloud", "auth": "oauth",
        "url": "https://minimax.io/",
        "hint": "OAuth para chat; TTS y clonación requieren una API key de MiniMax aparte",
        "hermes_id": "minimax-oauth", "env_key": "",
        "models": [
            {"value": "abab6.5s-chat",  "label": "abab6.5s-chat ⭐", "tier": "mid"},
            {"value": "abab6.5g-chat",  "label": "abab6.5g-chat",    "tier": "mid"},
        ],
    },

    # ── Cloud ──────────────────────────────────────────────────────
    # NOTE on omissions: Mistral, Groq, Cohere, Perplexity, Cloudflare,
    # LiteLLM, Doubao, vLLM are NOT in Hermes' provider registry
    # (verified against ``hermes_cli/auth.py::PROVIDER_REGISTRY`` and
    # ``hermes_cli/models.py::CANONICAL_PROVIDERS``). They surface in
    # ``provider_sync`` only when Hermes' live catalog discovers them
    # — which it doesn't, since Hermes never had adapters for them.
    # Listing them here would write a ``provider:`` value Hermes
    # rejects with ``Unknown provider 'X'``. To add models from those
    # vendors, the user routes through OpenRouter or AI Gateway.
    {
        "id": "xai", "name": "xAI (Grok)", "icon": "✖️",
        "category": "cloud", "auth": "api+oauth",
        "url": "https://console.x.ai/",
        "hint": "console.x.ai",
        "hermes_id": "xai", "hermes_id_oauth": "xai-oauth",
        "env_key": "XAI_API_KEY",
        "models": [
            {"value": "grok-4-1-fast",                    "label": "Grok 4.1 Fast ⭐ (Nuevo — API Enterprise)", "tier": "fast"},
            {"value": "grok-4.20-beta-latest-reasoning",  "label": "Grok 4.20 Beta (Flagship — Premium+)",      "tier": "premium"},
            {"value": "grok-3",                           "label": "Grok 3",                                    "tier": "mid"},
            {"value": "grok-3-mini",                      "label": "Grok 3 Mini (rápido)",                      "tier": "fast"},
            {"value": "grok-2-latest",                    "label": "Grok 2 (Legacy)",                           "tier": "mid"},
        ],
    },
    {
        "id": "nvidia", "name": "NVIDIA NIM", "icon": "💚",
        "category": "cloud", "auth": "api",
        "url": "https://build.nvidia.com/",
        "hint": "build.nvidia.com",
        "hermes_id": "nvidia", "env_key": "NVIDIA_API_KEY",
        "models": [
            {"value": "meta/llama-3.3-70b-instruct",            "label": "Llama 3.3 70B ⭐", "tier": "mid"},
            {"value": "nvidia/llama-3.1-nemotron-70b-instruct", "label": "Nemotron 70B",     "tier": "mid"},
        ],
    },
    {
        "id": "bedrock", "name": "Amazon Bedrock", "icon": "🟠",
        "category": "cloud", "auth": "aws",
        "url": "https://console.aws.amazon.com/bedrock/",
        "hint": "AWS Console → Bedrock",
        # Hermes' real slug is ``bedrock`` (not ``amazon-bedrock``).
        # auth_type is ``aws_sdk`` so credentials come from the AWS
        # SDK chain (env vars or ~/.aws/credentials).
        "hermes_id": "bedrock", "env_key": "",
        "models": [
            {"value": "us.anthropic.claude-opus-4-6-v1:0",        "label": "Claude 4.6 Opus ⭐ (Bedrock inference profile)", "tier": "premium"},
            {"value": "us.anthropic.claude-sonnet-4-6-v1:0",      "label": "Claude 4.6 Sonnet (Bedrock inference profile)", "tier": "mid"},
            {"value": "us.anthropic.claude-3-7-sonnet-20250219-v1:0", "label": "Claude 3.7 Sonnet (Bedrock)",                "tier": "mid"},
            {"value": "amazon.nova-pro-v1:0",                     "label": "Amazon Nova Pro",                                "tier": "mid"},
            {"value": "amazon.nova-lite-v1:0",                    "label": "Amazon Nova Lite",                               "tier": "fast"},
        ],
    },
    {
        "id": "azure-foundry", "name": "Azure Foundry", "icon": "🔷",
        "category": "cloud", "auth": "api",
        "url": "https://ai.azure.com/",
        "hint": "Azure AI Foundry deployment",
        "hermes_id": "azure-foundry", "env_key": "AZURE_FOUNDRY_API_KEY",
        "base_url": "",
        "base_url_required": True,
        "base_url_env_key": "AZURE_FOUNDRY_BASE_URL",
        "models": [
            {"value": "gpt-4o", "label": "Tu deployment de Foundry", "tier": "mid"},
        ],
    },
    {
        "id": "copilot", "name": "GitHub Copilot", "icon": "🐙",
        "category": "cloud", "auth": "api",
        "url": "https://github.com/settings/tokens",
        "hint": "GITHUB_TOKEN o `gh auth token`",
        # Hermes accepts COPILOT_GITHUB_TOKEN, GH_TOKEN, or GITHUB_TOKEN.
        "hermes_id": "copilot", "env_key": "GITHUB_TOKEN",
        "models": [
            {"value": "gpt-4o",                "label": "GPT-4o (via Copilot) ⭐", "tier": "mid"},
            {"value": "claude-sonnet-4",       "label": "Claude Sonnet 4 (via Copilot)", "tier": "mid"},
        ],
    },
    {
        "id": "copilot-acp", "name": "GitHub Copilot (ACP)", "icon": "🐙",
        "category": "cloud", "auth": "external",
        "url": "https://github.com/cli/cli",
        # Hermes' copilot-acp uses ``auth_type="external_process"`` —
        # it spawns ``copilot --acp --stdio`` and reuses whatever
        # auth your local ``gh``/``copilot`` CLI already has. No
        # env key needed; the user must run ``gh auth login`` once.
        "hint": "Spawns `copilot --acp --stdio` — usa la sesión local de `gh` / Copilot CLI",
        "hermes_id": "copilot-acp", "env_key": "",
        "models": [
            {"value": "gpt-4o", "label": "Lo que Copilot CLI tenga activo ⭐", "tier": "mid"},
        ],
    },
    {
        "id": "huggingface", "name": "Hugging Face", "icon": "🤗",
        "category": "cloud", "auth": "api",
        "url": "https://huggingface.co/settings/tokens",
        "hint": "huggingface.co/settings/tokens",
        # Hermes' real env var is HF_TOKEN (not HUGGINGFACE_API_KEY).
        "hermes_id": "huggingface", "env_key": "HF_TOKEN",
        "models": [
            {"value": "meta-llama/Llama-3.3-70B-Instruct", "label": "Llama 3.3 70B ⭐", "tier": "mid"},
            {"value": "Qwen/Qwen2.5-72B-Instruct",         "label": "Qwen 2.5 72B",     "tier": "mid"},
        ],
    },
    {
        "id": "xiaomi", "name": "Xiaomi MiMo", "icon": "📱",
        "category": "cloud", "auth": "api",
        "url": "https://mimo.mi.com/docs/en-US/quick-start/faq/api-integration",
        "hint": "MiMo-V2.5 (pro/omni/flash)",
        "hermes_id": "xiaomi", "env_key": "XIAOMI_API_KEY",
        "models": [
            {"value": "mimo-v2.5-pro", "label": "MiMo V2.5 Pro ⭐", "tier": "mid"},
        ],
    },
    {
        "id": "tencent-tokenhub", "name": "Tencent TokenHub", "icon": "🐧",
        "category": "cloud", "auth": "api",
        "url": "https://cloud.tencent.com/document/product/1823/130078",
        "hint": "Tencent TokenHub (Hy3 Preview)",
        "hermes_id": "tencent-tokenhub", "env_key": "TOKENHUB_API_KEY",
        "models": [
            {"value": "hy3-preview", "label": "Hy3 Preview ⭐", "tier": "mid"},
        ],
    },
    {
        "id": "arcee", "name": "Arcee AI", "icon": "🟦",
        "category": "cloud", "auth": "api",
        "url": "https://arcee.ai/",
        "hint": "Trinity models — direct API",
        "hermes_id": "arcee", "env_key": "ARCEEAI_API_KEY",
        "models": [
            {"value": "trinity-mini", "label": "Trinity Mini", "tier": "fast"},
        ],
    },
    {
        "id": "gmi", "name": "GMI Cloud", "icon": "🌐",
        "category": "cloud", "auth": "api",
        "url": "https://gmicloud.ai/",
        "hint": "Multi-model direct API",
        "hermes_id": "gmi", "env_key": "GMI_API_KEY",
        "models": [
            {"value": "auto", "label": "Auto-select", "tier": "mid"},
        ],
    },

    # ── Chinese AI ─────────────────────────────────────────────────
    {
        "id": "minimax-cn", "name": "MiniMax (China)", "icon": "🇨🇳",
        "category": "chinese", "auth": "api",
        "url": "https://api.minimax.chat/",
        "hint": "Endpoint chino directo (MINIMAX_CN_API_KEY)",
        "hermes_id": "minimax-cn", "env_key": "MINIMAX_CN_API_KEY",
        "models": [
            {"value": "abab6.5s-chat",  "label": "abab6.5s-chat ⭐", "tier": "mid"},
            {"value": "abab6.5g-chat",  "label": "abab6.5g-chat",    "tier": "mid"},
        ],
    },
    {
        "id": "minimax", "name": "MiniMax (API key)", "icon": "🔷",
        "category": "chinese", "auth": "api",
        "url": "https://platform.minimax.io/",
        "hint": "platform.minimax.io — versión API key (OAuth = entrada aparte ↑)",
        # Hermes bundles this as `minimax` (global API key). The OAuth
        # flavour lives under the separate `minimax-oauth` wizard
        # entry above. The China-region endpoint is `minimax-cn`.
        "hermes_id": "minimax", "env_key": "MINIMAX_API_KEY",
        "models": [
            {"value": "MiniMax-M2.7",           "label": "MiniMax M2.7 ⭐ (razonamiento, 200K ctx)", "tier": "mid"},
            {"value": "MiniMax-M2.7-highspeed", "label": "MiniMax M2.7 Highspeed (más rápido)",      "tier": "fast"},
        ],
    },
    {
        "id": "alibaba", "name": "Alibaba (Qwen Cloud)", "icon": "🟧",
        "category": "chinese", "auth": "api",
        "url": "https://dashscope.console.aliyun.com/",
        "hint": "Aliyun DashScope (general API)",
        "hermes_id": "alibaba", "env_key": "DASHSCOPE_API_KEY",
        "models": [
            {"value": "qwen-max-latest", "label": "Qwen-Max ⭐", "tier": "mid"},
            {"value": "qwen-plus",       "label": "Qwen-Plus",   "tier": "fast"},
            {"value": "qwen-turbo",      "label": "Qwen-Turbo",  "tier": "fast"},
        ],
    },
    {
        "id": "alibaba-coding-plan", "name": "Alibaba Coding Plan", "icon": "🟧",
        "category": "chinese", "auth": "api",
        "url": "https://dashscope.console.aliyun.com/",
        "hint": "Tier dedicado de coding (coding-intl.dashscope.aliyuncs.com)",
        # Hermes maps this to its own slug — separate endpoint from
        # plain ``alibaba``, but both can reuse DASHSCOPE_API_KEY.
        "hermes_id": "alibaba-coding-plan",
        "env_key": "ALIBABA_CODING_PLAN_API_KEY",
        "models": [
            {"value": "qwen3-coder-plus",  "label": "Qwen3 Coder Plus ⭐", "tier": "mid"},
            {"value": "qwen3-coder-flash", "label": "Qwen3 Coder Flash",   "tier": "fast"},
        ],
    },
    {
        "id": "moonshot", "name": "Moonshot AI (Kimi)", "icon": "🌙",
        "category": "chinese", "auth": "api",
        "url": "https://platform.moonshot.ai/",
        "hint": "platform.moonshot.ai (global) — KIMI_API_KEY",
        # Hermes' slug for the global Moonshot/Kimi API is
        # ``kimi-coding`` (env: KIMI_API_KEY or KIMI_CODING_API_KEY).
        # The China-region endpoint lives under ``kimi-coding-cn``.
        "hermes_id": "kimi-coding", "env_key": "KIMI_API_KEY",
        "models": [
            {"value": "kimi-k2-instruct",   "label": "Kimi K2 Instruct ⭐", "tier": "mid"},
            {"value": "moonshot-v1-128k",   "label": "Kimi 128K",            "tier": "mid"},
            {"value": "moonshot-v1-32k",    "label": "Kimi 32K",             "tier": "fast"},
        ],
    },
    {
        "id": "kimi-coding-cn", "name": "Kimi (China region)", "icon": "🌙",
        "category": "chinese", "auth": "api",
        "url": "https://platform.moonshot.cn/",
        "hint": "platform.moonshot.cn — endpoint chino (KIMI_CN_API_KEY)",
        "hermes_id": "kimi-coding-cn", "env_key": "KIMI_CN_API_KEY",
        "models": [
            {"value": "kimi-k2", "label": "Kimi K2 ⭐", "tier": "mid"},
        ],
    },
    {
        "id": "zhipu", "name": "Z.AI (GLM)", "icon": "🔶",
        "category": "chinese", "auth": "api",
        "url": "https://open.bigmodel.cn/",
        "hint": "open.bigmodel.cn",
        # Hermes' slug is ``zai`` (rebrand). Real env var is
        # GLM_API_KEY (also accepts ZAI_API_KEY, Z_AI_API_KEY).
        "hermes_id": "zai", "env_key": "GLM_API_KEY",
        "models": [
            {"value": "glm-4-plus",  "label": "GLM-4 Plus ⭐",    "tier": "mid"},
            {"value": "glm-4-flash", "label": "GLM-4 Flash (gratis)", "tier": "fast"},
        ],
    },
    {
        "id": "stepfun", "name": "StepFun", "icon": "🪜",
        "category": "chinese", "auth": "api",
        "url": "https://platform.stepfun.com/",
        "hint": "platform.stepfun.com",
        "hermes_id": "stepfun", "env_key": "STEPFUN_API_KEY",
        "models": [
            {"value": "step-2-16k", "label": "Step-2 16K ⭐", "tier": "mid"},
        ],
    },

    # ── Aggregators ────────────────────────────────────────────────
    {
        "id": "openrouter", "name": "OpenRouter", "icon": "🌐",
        "category": "aggregator", "auth": "api+oauth",
        "url": "https://openrouter.ai/keys",
        "hint": "openrouter.ai — Accede a 100+ modelos con una key",
        "hermes_id": "openrouter", "env_key": "OPENROUTER_API_KEY",
        "models": [
            {"value": "anthropic/claude-sonnet-4-6",       "label": "Claude 4.6 Sonnet ⭐ (via OR)",  "tier": "mid"},
            {"value": "anthropic/claude-opus-4-6",         "label": "Claude 4.6 Opus (via OR)",       "tier": "premium"},
            {"value": "openai/gpt-4o-mini",                "label": "GPT-4o-mini (via OR)",            "tier": "fast"},
            {"value": "openai/gpt-5.4",                    "label": "GPT-5.4 (via OR)",                "tier": "premium"},
            {"value": "google/gemini-3.1-pro-preview",     "label": "Gemini 3.1 Pro (via OR)",         "tier": "premium"},
            {"value": "google/gemini-2.5-flash",           "label": "Gemini 2.5 Flash (via OR)",       "tier": "fast"},
            {"value": "deepseek/deepseek-chat",            "label": "DeepSeek-V3 (via OR)",            "tier": "mid"},
            {"value": "meta-llama/llama-3.3-70b-instruct", "label": "Llama 3.3 70B (via OR)",          "tier": "mid"},
            {"value": "mistralai/mistral-large-2411",      "label": "Mistral Large (via OR)",          "tier": "mid"},
            {"value": "x-ai/grok-4-1-fast",                "label": "Grok 4.1 Fast (via OR)",          "tier": "fast"},
        ],
    },
    {
        "id": "ai-gateway", "name": "Vercel AI Gateway", "icon": "▲",
        "category": "aggregator", "auth": "api",
        "url": "https://vercel.com/ai-gateway",
        "hint": "vercel.com/ai-gateway — 200+ modelos, $5 free credit",
        "hermes_id": "ai-gateway", "env_key": "AI_GATEWAY_API_KEY",
        "models": [
            {"value": "openai/gpt-4o-mini",      "label": "GPT-4o-mini ⭐", "tier": "fast"},
            {"value": "anthropic/claude-sonnet-4", "label": "Claude Sonnet 4", "tier": "mid"},
            {"value": "google/gemini-1.5-pro",   "label": "Gemini 1.5 Pro",  "tier": "mid"},
        ],
    },
    {
        "id": "kilocode", "name": "Kilo Code", "icon": "🔧",
        "category": "aggregator", "auth": "api",
        "url": "https://kilocode.ai/",
        "hint": "Kilo Gateway API",
        "hermes_id": "kilocode", "env_key": "KILOCODE_API_KEY",
        "models": [
            {"value": "auto", "label": "Auto-select", "tier": "mid"},
        ],
    },
    {
        "id": "opencode-zen", "name": "OpenCode Zen", "icon": "🧘",
        "category": "aggregator", "auth": "api",
        "url": "https://opencode.ai/docs/zen/",
        "hint": "35+ modelos curados, pay-as-you-go",
        "hermes_id": "opencode-zen", "env_key": "OPENCODE_ZEN_API_KEY",
        "models": [
            {"value": "auto", "label": "Auto-select", "tier": "mid"},
        ],
    },
    {
        "id": "opencode-go", "name": "OpenCode Go", "icon": "🧘",
        "category": "aggregator", "auth": "api",
        "url": "https://opencode.ai/docs/go/",
        "hint": "Modelos open, $10/mes",
        "hermes_id": "opencode-go", "env_key": "OPENCODE_GO_API_KEY",
        "models": [
            {"value": "auto", "label": "Auto-select", "tier": "mid"},
        ],
    },

    # ── Local / Self-hosted ────────────────────────────────────────
    # ``lmstudio`` and ``ollama-cloud`` are first-class Hermes
    # providers (own slugs in PROVIDER_REGISTRY). Everything else
    # local — bare Ollama on 127.0.0.1, vLLM, llama.cpp, anything
    # else OpenAI-compatible — routes through Hermes' generic
    # ``custom`` provider. We expose dedicated wizard entries for
    # the common local servers so the user picks what they're
    # running and we plug in the right ``base_url`` automatically.
    # Hermes' alias table (verified in
    # ``hermes_cli/auth.py::_PROVIDER_ALIASES``) maps "ollama",
    # "vllm", "llamacpp", "llama.cpp" all to ``custom``.
    {
        "id": "lmstudio", "name": "LM Studio (Local)", "icon": "🏠",
        "category": "local", "auth": "api-optional",
        "url": "https://lmstudio.ai/",
        "hint": "App de escritorio con servidor local — sin API key",
        # Hermes' lmstudio plugin defaults to http://127.0.0.1:1234/v1
        # and accepts an optional LM_API_KEY for auth-enabled servers.
        "hermes_id": "lmstudio", "env_key": "LM_API_KEY",
        "models": [
            {"value": "auto", "label": "Modelo cargado en LM Studio", "tier": "mid"},
        ],
    },
    {
        "id": "ollama-local", "name": "Ollama (Local)", "icon": "🦙",
        "category": "local", "auth": "none",
        "url": "https://ollama.com/download",
        "hint": "Ollama corriendo en 127.0.0.1:11434 — sin API key",
        # Routed through Hermes' ``custom`` provider with the standard
        # Ollama OpenAI-compat URL. ``base_url`` is written into
        # config.yaml by ``write_hermes_config`` based on this
        # provider's ``base_url`` field.
        "hermes_id": "custom", "env_key": "",
        "base_url": "http://127.0.0.1:11434/v1",
        "models": [
            {"value": "gemma3:4b",       "label": "Gemma 3 4B",      "tier": "fast"},
            {"value": "qwen2.5:3b",      "label": "Qwen 2.5 3B",     "tier": "fast"},
            {"value": "llama3.2:3b",     "label": "Llama 3.2 3B",    "tier": "fast"},
            {"value": "phi4-mini",       "label": "Phi-4 Mini",      "tier": "fast"},
            {"value": "deepseek-r1:8b",  "label": "DeepSeek-R1 8B", "tier": "reasoning"},
        ],
    },
    {
        "id": "vllm", "name": "vLLM (Local)", "icon": "⚙️",
        "category": "local", "auth": "none",
        "url": "https://docs.vllm.ai/",
        "hint": "Servidor vLLM en 127.0.0.1:8000 — sin API key",
        "hermes_id": "custom", "env_key": "",
        "base_url": "http://127.0.0.1:8000/v1",
        "models": [
            {"value": "meta-llama/Llama-3.3-70B-Instruct", "label": "Lo que cargues con vLLM", "tier": "mid"},
        ],
    },
    {
        "id": "llamacpp", "name": "llama.cpp (Local)", "icon": "🐉",
        "category": "local", "auth": "none",
        "url": "https://github.com/ggerganov/llama.cpp",
        "hint": "Servidor llama-server en 127.0.0.1:8080 — sin API key",
        "hermes_id": "custom", "env_key": "",
        "base_url": "http://127.0.0.1:8080/v1",
        "models": [
            {"value": "auto", "label": "Modelo cargado en llama-server", "tier": "mid"},
        ],
    },
    {
        "id": "ollama", "name": "Ollama Cloud", "icon": "☁️",
        "category": "cloud", "auth": "api",
        "url": "https://ollama.com/settings",
        "hint": "ollama.com — modelos cloud-hosted (con cuenta gratuita)",
        "hermes_id": "ollama-cloud", "env_key": "OLLAMA_API_KEY",
        "models": [
            {"value": "kimi-k2.5:cloud",     "label": "Kimi K2.5 (cloud)",   "tier": "mid"},
            {"value": "glm-5:cloud",         "label": "GLM-5 (cloud)",        "tier": "premium"},
            {"value": "minimax-m2.5:cloud",  "label": "MiniMax M2.5 (cloud)", "tier": "mid"},
        ],
    },
    {
        "id": "custom", "name": "Endpoint OpenAI-compatible", "icon": "🔌",
        "category": "local", "auth": "api-optional",
        "url": "",
        "hint": "Cualquier servidor OpenAI-compatible — indica su URL en el wizard",
        "hermes_id": "custom", "env_key": "",
        # No default base_url — the user MUST edit config.yaml or
        # set HERMES_INFERENCE_BASE_URL because we don't know where
        # their server lives.
        "base_url": "",
        "base_url_required": True,
        "models": [
            {"value": "auto", "label": "(configura `model.default` en ~/.hermes/config.yaml)", "tier": "mid"},
        ],
    },
]


# ── Lookup helpers ───────────────────────────────────────────────────

_BY_ID: dict[str, dict[str, Any]] = {p["id"]: p for p in PROVIDERS}


def get_provider(wizard_id: str) -> dict[str, Any] | None:
    """Return the provider dict for `wizard_id`, or None if unknown."""
    curated = _BY_ID.get(wizard_id)
    if curated:
        return curated
    if wizard_id.startswith("oc-"):
        from .provider_sync import local_hermes_provider_metadata

        slug = wizard_id[3:]
        runtime = local_hermes_provider_metadata(allow_probe=False).get(slug)
        if runtime:
            return _synthesize_discovered_provider(slug, [], runtime)
    return None


def hermes_id_for(wizard_id: str, *, use_oauth: bool = False) -> str:
    """Return the Hermes provider slug for a wizard ID.

    Resolution order:
      1. Curated entries map via their ``hermes_id`` field. When
         ``use_oauth=True`` AND the entry declares a
         ``hermes_id_oauth`` override, that override wins — this
         is how we route the **same** wizard provider id to two
         different Hermes plugins depending on the chosen auth
         method (e.g. wizard "openai" → Hermes "openai" with API
         key, but → "openai-codex" under OAuth, since the OAuth
         plugin doesn't accept api keys).
      2. Discovered entries carry an ``oc-<slug>`` wizard id —
         the Hermes slug is whatever follows the prefix.
      3. Any other id is passed through unchanged so an unmapped value
         still produces something predictable instead of "".
    """
    p = _BY_ID.get(wizard_id)
    if p:
        if use_oauth and p.get("hermes_id_oauth"):
            return p["hermes_id_oauth"]
        return p.get("hermes_id") or wizard_id
    if wizard_id.startswith("oc-"):
        return wizard_id[3:]
    return wizard_id


def wizard_id_for_hermes_slug(hermes_slug: str, *, base_url: str = "") -> str:
    """Reverse of ``hermes_id_for``: find the wizard provider id whose
    ``hermes_id`` (or ``hermes_id_oauth``) matches the given slug.

    Used by ``/api/import-hermes-config`` when we need to translate
    Hermes' on-disk ``config.yaml`` provider name back into the wizard's
    internal id space. Returns the slug unchanged if no curated mapping
    is found, so unknown / discovered providers still produce a usable
    value (matching ``hermes_id_for``'s pass-through behaviour).
    """
    if not hermes_slug:
        return ""
    s = hermes_slug.strip().lower()
    # ``provider: auto`` is Hermes' routing mode, not an installable provider.
    # Turning it into the synthetic id ``oc-auto`` made the health check ask
    # for a fictional AUTO_API_KEY and could make a stale OpenRouter route look
    # like the provider the user had just selected. Existing automatic configs
    # must be re-saved explicitly by the wizard instead.
    if s == "auto":
        return ""
    if s == "custom" and base_url.strip():
        endpoint = base_url.strip().rstrip("/").lower()
        for provider in PROVIDERS:
            known_endpoint = str(provider.get("base_url") or "").strip().rstrip("/").lower()
            if (
                provider.get("hermes_id") == "custom"
                and known_endpoint
                and known_endpoint == endpoint
            ):
                return str(provider["id"])
    # 1. Curated providers: scan PROVIDERS once, prefer non-OAuth match
    #    (e.g. "openai" should resolve to wizard id "openai", not to
    #    whatever provider happened to declare "openai" as its
    #    hermes_id_oauth alias — there shouldn't be one, but be defensive).
    api_match = None
    oauth_match = None
    for p in PROVIDERS:
        if (p.get("hermes_id") or "").lower() == s:
            api_match = p["id"]
        if (p.get("hermes_id_oauth") or "").lower() == s:
            oauth_match = p["id"]
    if api_match:
        return api_match
    if oauth_match:
        return oauth_match
    # 2. Discovered providers are wizard-prefixed with "oc-".
    return f"oc-{s}" if not s.startswith("oc-") else s


def env_key_for(wizard_id: str) -> str:
    """Return the conventional env var name (ANTHROPIC_API_KEY, …) or ""."""
    p = _BY_ID.get(wizard_id)
    if p:
        return p.get("env_key", "") or ""
    # Discovered providers carry an `oc-<slug>` id. Prefer the installed
    # Hermes registry so newly-added providers work without an IRIS release.
    if wizard_id.startswith("oc-"):
        from .provider_sync import DISCOVERY_META, local_hermes_provider_metadata
        slug = wizard_id[3:]
        runtime = local_hermes_provider_metadata(allow_probe=False).get(slug, {})
        runtime_keys = runtime.get("env_vars") or ()
        if runtime_keys:
            return str(runtime_keys[0])
        return (DISCOVERY_META.get(slug, {}) or {}).get("env_key", "") or ""
    return ""


def auth_mode_for(wizard_id: str) -> AuthMode:
    """Return the authentication contract for ``wizard_id``.

    Resolution order:
      1. Curated entry in `_BY_ID` (covers anthropic / openai / ollama / …).
      2. Discovered slug via `DISCOVERY_META` (covers `oc-codex`,
         `oc-google-antigravity`, `oc-github-copilot`, …).
      3. Conservative default `"api"` so an unknown provider is treated
         like a key-bearing one and we still surface a helpful error if
         the key is missing.

    This is the single source of truth used by the pre-flight gate in
    `/api/start-gateway` so OAuth-only providers don't get rejected for
    "missing API key" when their credentials live in Hermes' auth
    profile store instead of in an env var.
    """
    p = _BY_ID.get(wizard_id)
    if p:
        return p.get("auth") or "api"  # type: ignore[return-value]
    if wizard_id.startswith("oc-"):
        from .provider_sync import DISCOVERY_META, local_hermes_provider_metadata
        slug = wizard_id[3:]
        runtime = local_hermes_provider_metadata(allow_probe=False).get(slug, {})
        if runtime:
            runtime_auth = str(runtime.get("auth_type") or "").strip().lower()
            if runtime_auth == "aws_sdk":
                return "aws"
            if runtime_auth == "external_process":
                return "external"
            has_key = bool(runtime.get("env_vars"))
            has_oauth = bool(runtime.get("oauth_capable"))
            if has_key and has_oauth:
                return "api+oauth"
            if has_oauth:
                return "oauth"
            if has_key:
                return "api"
            return "none"
        meta = DISCOVERY_META.get(slug, {}) or {}
        explicit = meta.get("auth")
        if explicit:
            return explicit  # type: ignore[return-value]
        # No installed metadata: never invent an OAuth button. Unknown
        # providers are treated conservatively as API-key integrations.
        env_key  = meta.get("env_key") or ""
        if env_key:
            return "api"
        return "api"
    return "api"


def effective_auth_mode_for(
    wizard_id: str,
    provider_metadata: dict[str, dict[str, Any]] | None = None,
) -> AuthMode:
    """Resolve auth exactly as the installed Hermes version advertises it.

    ``auth_mode_for`` remains the offline registry contract. This helper folds
    in the non-secret capability snapshot used by the UI, so save/preflight
    cannot disagree with the controls that were just rendered.
    """
    fallback = auth_mode_for(wizard_id)
    provider = _BY_ID.get(wizard_id)
    if provider is None:
        return fallback
    # These modes describe endpoint semantics rather than a mandatory Hermes
    # credential mechanism. In particular, LM Studio may expose LM_API_KEY,
    # but an unauthenticated local server remains valid.
    if fallback in {"none", "api-optional"}:
        return fallback
    if provider_metadata is None:
        from .provider_sync import local_hermes_provider_metadata

        provider_metadata = local_hermes_provider_metadata(allow_probe=False)
    slug = str(provider.get("hermes_id") or wizard_id)
    oauth_slug = str(provider.get("hermes_id_oauth") or "")
    base_meta = (provider_metadata or {}).get(slug)
    oauth_meta = (provider_metadata or {}).get(oauth_slug) if oauth_slug else base_meta
    if not base_meta and not oauth_meta:
        return fallback

    runtime_auth = str((base_meta or {}).get("auth_type") or "").strip().lower()
    if runtime_auth == "aws_sdk":
        return "aws"
    if runtime_auth == "external_process":
        return "external"
    # OpenRouter deliberately has no PROVIDER_REGISTRY row in the pinned Hermes.
    # Its API-key resolver still reads OPENROUTER_API_KEY (hermes_cli/auth.py), so
    # an empty registry-derived env list must not turn this card into OAuth-only.
    has_api = bool((base_meta or {}).get("env_vars")) or slug == "openrouter"
    has_oauth = bool(
        oauth_meta
        and (
            oauth_meta.get("oauth_capable")
            or bool(oauth_slug and oauth_slug != slug)
        )
    )
    if has_api and has_oauth:
        return "api+oauth"
    if has_oauth:
        return "oauth"
    if has_api:
        return "api"
    return fallback


# Provider-specific cautions surfaced inline with the selection in
# Spanish, so the user reads them at pick time instead of discovering
# them only when the request fails (e.g. Gemini OAuth's account-risk
# notice or Codex's "needs special access" wrinkle).
#
# Keys are wizard provider IDs. Values are dicts with:
#   - `level`: "info" | "warn" | "danger"
#   - `title`: short heading
#   - `body`:  multi-line explanation (Spanish)
#
# Add new entries here as Hermes introduces new cautions; the UI picks
# them up automatically via /api/providers (no JS edits needed).
_GOOGLE_OAUTH_WARNING = {
    "level": "warn",
    "title": "Aviso sobre Gemini CLI OAuth",
    "body": (
        "Esta es una integración no oficial — Google no la respalda. "
        "Algunos usuarios han reportado restricciones o suspensiones de cuenta "
        "al usar clientes OAuth de terceros (Gemini CLI, Antigravity).\n\n"
        "Solo continúa si entiendes y aceptas ese riesgo. "
        "La tarjeta Google (Gemini) normal usa una API key directa de AI Studio."
    ),
}

_OPENAI_OAUTH_WARNING = {
    "level": "info",
    "title": "OpenAI OAuth (Codex)",
    "body": (
        "Si activas OAuth, Hermes usa el adaptador OpenAI Codex y tu sesión "
        "de ChatGPT; no usa la API key de platform.openai.com. La cuenta debe "
        "tener acceso compatible. Sin OAuth, esta tarjeta usa OpenAI API."
    ),
}

PROVIDER_WARNINGS: dict[str, dict[str, str]] = {
    "opencode-zen": {
        "level": "info",
        "title": "OpenCode Zen: créditos para usarlo desde Iris",
        "body": (
            "Los modelos gratuitos son exclusivos de la aplicación OpenCode. "
            "Para usar Zen desde Iris necesitas créditos y elegir un modelo de pago. "
            "Añadir créditos no desbloquea los modelos gratuitos."
        ),
    },
    "opencode-go": {
        "level": "info",
        "title": "OpenCode Go: suscripción y modelos incluidos",
        "body": (
            "Necesitas una suscripción activa a OpenCode Go y elegir uno de sus modelos incluidos. "
            "Los modelos gratuitos exclusivos de la aplicación OpenCode no se habilitan con esta conexión."
        ),
    },
    # The curated Google card is API-only. These warnings belong only to the
    # OAuth integrations that Hermes may discover at runtime.
    "oc-google-antigravity": _GOOGLE_OAUTH_WARNING,
    "oc-google-gemini-cli": _GOOGLE_OAUTH_WARNING,
    "anthropic": {
        "level": "info",
        "title": "Anthropic: API key o Claude Code",
        "body": (
            "Si activas OAuth, Hermes usa el flujo compatible con Claude Code; "
            "no reutiliza una API key de Claude Platform. La disponibilidad "
            "depende de los permisos y del plan de tu cuenta. Si prefieres el "
            "consumo directo de API, deja OAuth desactivado y pega una API key."
        ),
    },
    "openai": _OPENAI_OAUTH_WARNING,
    "oc-codex": _OPENAI_OAUTH_WARNING,
    "oc-openai-codex": _OPENAI_OAUTH_WARNING,
    "bedrock": {
        "level": "info",
        "title": "Amazon Bedrock usa AWS SDK",
        "body": (
            "No pegues solo un Access Key ID. Hermes resuelve la cadena completa "
            "de AWS: perfil, variables de acceso + secreto, rol, web identity o "
            "token de Bedrock. La cuenta también debe tener acceso al modelo."
        ),
    },
    "azure-foundry": {
        "level": "info",
        "title": "Azure necesita el endpoint del deployment",
        "body": (
            "La API key no basta. Copia también la URL completa del endpoint "
            "de Azure Foundry; IRIS la guardará junto al proveedor."
        ),
    },
    "copilot-acp": {
        "level": "info",
        "title": "Copilot ACP usa una CLI externa",
        "body": (
            "Hermes inicia `copilot --acp --stdio`. Debes instalar y autenticar "
            "Copilot CLI antes; IRIS no instala ni inicia sesión en esa herramienta."
        ),
    },
    "custom": {
        "level": "info",
        "title": "Endpoint OpenAI-compatible",
        "body": (
            "Indica la URL completa y mantén el servidor activo. IRIS comprobará "
            "el endpoint y el modelo antes de reemplazar una configuración funcional."
        ),
    },
    "ollama-local": {
        "level": "info",
        "title": "Ollama local",
        "body": (
            "Ollama corre 100% en tu máquina — sin API key, sin internet. "
            "Asegúrate de tener `ollama serve` corriendo y el modelo descargado "
            "(`ollama pull <modelo>`) antes de iniciar el Gateway."
        ),
    },
    "vllm": {
        "level": "info",
        "title": "vLLM autohospedado",
        "body": (
            "vLLM espera un endpoint OpenAI-compatible que tú alojas. "
            "Configúralo en tu servidor antes de seleccionar este proveedor."
        ),
    },
}


def warning_for(provider_id: str) -> dict[str, str] | None:
    """Return the warning dict for `provider_id`, or None if there isn't one."""
    return PROVIDER_WARNINGS.get(provider_id)


def providers_payload() -> dict[str, Any]:
    """Build the payload the wizard UI fetches from `/api/providers`.

    Shape matches what wizard.js currently expects in-memory so the
    migration is a straight swap. We include the categories dict too so
    the UI can render the section headers without hardcoding anything.

    Prefer `merged_providers_payload()` when `asyncio` is available —
    it blends in whatever Hermes currently knows about, so new
    providers (Tencent Hy3, new Grok tools, etc.) appear without us
    having to cut a release. This sync variant is kept for tests and
    for the rare caller that can't await.
    """
    # Fold provider warnings into the static payload too so the UI can
    # show them even when Hermes isn't installed yet (first-run case).
    providers_with_warnings: list[dict[str, Any]] = []
    for p in PROVIDERS:
        entry = dict(p)
        entry["models"] = []
        warn = warning_for(p["id"])
        if warn:
            entry["warning"] = warn
        providers_with_warnings.append(entry)
    return {
        "categories": CATEGORIES,
        "providers":  providers_with_warnings,
        "source": "static",
        "hermes": {"version": "", "providers_detected": 0, "models_detected": 0},
        "selectable_model_count": 0,
        "degraded": True,
    }


# ── Dynamic merge with Hermes ──────────────────────────────────────
#
# The static `PROVIDERS` list above is the curated source of truth for
# labels / icons / sign-up URLs / tier tags. But Hermes also ships a
# catalog of its own, and that catalog grows faster than we can edit
# this file (see release notes: Tencent Hy3 showed up in 2026.4.22).
#
# `merged_providers_payload()` blends the two:
#
#   * Every curated provider whose `hermes_id` matches a slug in
#     Hermes' catalog gets its `models` list replaced by Hermes' current
#     selectable IDs. Hand-curated labels / tiers survive only when that ID
#     still exists; removed models are not resurrected by the fallback.
#
#   * Providers Hermes knows about but we don't curate are appended
#     as synthesized entries, marked `discovered: True`. The UI can
#     render them with a "🆕 Detectado por Hermes" badge so users
#     understand they're auto-surfaced and may lack polish.
#
#   * Providers we curate but Hermes doesn't know about (local,
#     plugin-only, or just not bundled in this Hermes version) stay
#     in the list as-is, tagged `discovered: False`.
#
#   * When Hermes isn't installed / crashes / times out, we fall
#     straight back to the static list. First-run must never break.


def _authoritative_provider_models(
    static_models: list[dict[str, Any]],
    hermes_models: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Use Hermes IDs as truth while preserving IRIS presentation metadata."""
    from .provider_sync import guess_tier

    static_by_id = {str(model.get("value")): model for model in static_models}
    out: list[dict[str, Any]] = []
    for hermes_model in hermes_models:
        if hermes_model.get("missing"):
            continue
        model_id = str(hermes_model.get("id") or "").strip()
        if not model_id:
            continue
        curated = static_by_id.get(model_id)
        merged = dict(curated) if curated else {
            "value": model_id,
            "label": str(hermes_model.get("name") or model_id),
            "tier": guess_tier(
                model_id,
                str(hermes_model.get("name") or model_id),
                hermes_model.get("tags", []),
            ),
            "discovered": True,
        }
        merged["value"] = model_id
        merged["context"] = int(
            hermes_model.get("context") or merged.get("context") or 0
        )
        merged["input"] = str(
            hermes_model.get("input") or merged.get("input") or "text"
        )
        merged["available"] = bool(hermes_model.get("available"))
        # Q04: a model the account's plan cannot use stays listed, locked.
        merged.pop("locked", None)
        merged.pop("lock_reason", None)
        if hermes_model.get("locked"):
            merged["locked"] = True
            merged["lock_reason"] = str(hermes_model.get("lock_reason") or "plan")
        out.append(merged)
    return out


def _synthesize_discovered_provider(
    slug: str,
    hermes_models: list[dict[str, Any]],
    runtime_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a provider dict for a slug we don't curate.

    Uses `DISCOVERY_META` for known slugs (nice name + icon +
    category) and falls back to a generic "🔌" entry otherwise.
    """
    from .provider_sync import DISCOVERY_META, guess_tier

    meta = DISCOVERY_META.get(slug, {})
    runtime_meta = runtime_meta or {}
    name = meta.get("name") or runtime_meta.get("label") or slug.replace("-", " ").title()
    icon = meta.get("icon") or "🔌"
    category = meta.get("category") or "aggregator"
    runtime_keys = runtime_meta.get("env_vars") or ()
    env_key = (str(runtime_keys[0]) if runtime_keys else "") or meta.get("env_key") or ""

    # Every model, sorted by preference: available first, then reasoning /
    # premium / mid / fast / local. QA12/Q03 kept only ten; the wizard now
    # filters a long list instead of losing models.
    tier_order = {"reasoning": 0, "premium": 1, "mid": 2, "fast": 3}
    scored = []
    for hm in hermes_models:
        if hm.get("missing"):
            continue
        tier = guess_tier(hm["id"], hm["name"], hm.get("tags", []))
        scored.append((
            0 if hm.get("available") else 1,
            tier_order.get(tier, 9),
            hm["name"].lower(),
            hm,
            tier,
        ))
    scored.sort(key=lambda t: (t[0], t[1], t[2]))

    models_out = []
    for _, _, _, hm, tier in scored:
        model = {
            "value":      hm["id"],
            "label":      hm["name"],
            "tier":       tier,
            "context":    hm["context"],
            "input":      hm["input"],
            "available":  hm["available"],
            "discovered": True,
        }
        if hm.get("locked"):
            model["locked"] = True
            model["lock_reason"] = str(hm.get("lock_reason") or "plan")
        models_out.append(model)

    # Resolve auth mode. Order of precedence:
    #   1. Explicit `auth` in DISCOVERY_META (we curate these for known
    #      slugs — e.g. `codex` is "oauth", `lmstudio` is "none").
    #   2. Inferred from category + env_key:
    #        local category, no env_key  → "none"
    #        no env_key (OAuth-only flow) → "oauth"
    #        env_key present              → "api"
    #
    # The explicit field exists because the env_key heuristic alone
    # can't distinguish OAuth-only providers (codex, google-antigravity)
    # from local-no-auth providers (lmstudio) — both have empty
    # env_key but mean very different things in the UI.
    has_oauth = bool(runtime_meta.get("oauth_capable"))
    runtime_auth = str(runtime_meta.get("auth_type") or "").strip().lower()
    if runtime_meta:
        if runtime_auth == "aws_sdk":
            auth_mode = "aws"
        elif runtime_auth == "external_process":
            auth_mode = "external"
        elif env_key and has_oauth:
            auth_mode = "api+oauth"
        elif has_oauth:
            auth_mode = "oauth"
        elif env_key:
            auth_mode = "api"
        else:
            auth_mode = "none"
    else:
        auth_mode = meta.get("auth") or ("api" if env_key else "none")

    return {
        # `oc-` prefix is historical (collision-avoidance against the
        # curated ids); kept stable so persisted user selections from
        # earlier installs still resolve. Reads as "auto-discovered
        # slug" today.
        "id":          f"oc-{slug}",
        "name":        name,
        "icon":        icon,
        "category":    category,
        "auth":        auth_mode,
        "url":         "",
        "hint":        "Detectado automáticamente en Hermes",
        "hermes_id":   slug,
        "env_key":     env_key,
        "models":      models_out,
        "discovered":  True,
    }


async def merged_providers_payload() -> dict[str, Any]:
    """Build the `/api/providers` payload with Hermes data folded in.

    Layout of the returned dict:
      {
        "categories": {...},
        "providers":  [...curated, then discovered...],
        "source":     "merged" | "static",
        "hermes":     {"version": "...", "providers_detected": N,
                       "models_detected": M},
      }

    The `source` field lets the UI know whether the list is just the
    hardcoded fallback (Hermes absent) or the real blended view —
    useful for a subtle footer like "Lista en vivo desde Hermes".
    """
    from .provider_sync import hermes_catalog, local_hermes_provider_metadata

    try:
        catalog = await hermes_catalog()
    except Exception:
        catalog = {}
    try:
        loop = asyncio.get_running_loop()
        local_meta = await loop.run_in_executor(
            None,
            lambda: local_hermes_provider_metadata(allow_probe=False),
        )
    except Exception:
        local_meta = {}

    if not catalog:
        # Hermes absent or unhappy — static list, no surprises.
        # Still fold warnings so the user sees them on first run.
        static_with_warnings: list[dict[str, Any]] = []
        for p in PROVIDERS:
            entry = dict(p)
            # Provider cards remain useful offline, but build-time model IDs
            # age independently of IRIS. An unavailable Hermes catalog must
            # fall back to the explicit manual-model field, never present an
            # old identifier as if it had just been validated.
            entry["models"] = []
            warn = warning_for(p["id"])
            if warn:
                entry["warning"] = warn
            static_with_warnings.append(entry)
        empty = {"version": "", "providers_detected": 0, "models_detected": 0}
        return {
            "categories": CATEGORIES,
            "providers":  static_with_warnings,
            "source":     "static",
            "hermes":     empty,
            "selectable_model_count": 0,
            "degraded": True,
        }

    # 1. Enhance each curated provider with Hermes' catalog data
    out: list[dict[str, Any]] = []
    curated_slugs: set[str] = set()
    for p in PROVIDERS:
        slug = p.get("hermes_id") or p["id"]
        oauth_slug = p.get("hermes_id_oauth") or ""
        catalog_slug = p.get("catalog_id") or slug
        curated_slugs.update(s for s in (slug, oauth_slug, catalog_slug) if s)
        merged = dict(p)
        # Current Hermes inventories use executable provider slugs
        # (openai-api, gemini). ``catalog_id`` remains only as a fallback for
        # old bundled/public manifests that grouped them as openai/google.
        may_use_catalog = not (slug == "custom" and p.get("id") != "custom")
        if may_use_catalog and slug in catalog:
            hermes_models = catalog[slug]
            has_authoritative_models = True
        elif may_use_catalog and catalog_slug in catalog:
            hermes_models = catalog[catalog_slug]
            has_authoritative_models = True
        else:
            hermes_models = []
            has_authoritative_models = False
        if has_authoritative_models:
            merged["models"] = _authoritative_provider_models(
                p.get("models", []), hermes_models,
            )
            merged["discovered"] = False
        else:
            merged["models"] = []
            merged["discovered"] = False
        if oauth_slug and oauth_slug != catalog_slug and oauth_slug in catalog:
            merged["oauth_models"] = _authoritative_provider_models(
                [], catalog[oauth_slug],
            )
        merged["oauth_catalog_authoritative"] = bool(
            oauth_slug and oauth_slug != slug and oauth_slug in catalog
        )
        merged["hermes_detected"] = bool(
            local_meta and (slug in local_meta or (oauth_slug and oauth_slug in local_meta))
        )

        # Adapt the auth controls to the installed Hermes version. This is
        # what removes stale promises such as Google OAuth on Hermes 0.19,
        # while automatically enabling a new OAuth alias when Hermes adds it.
        if local_meta:
            merged["auth"] = effective_auth_mode_for(p["id"], local_meta)
        # Fold in the warning text (if any) so the UI can render it
        # inline when this provider is selected, without a second fetch.
        warn = warning_for(p["id"])
        if warn:
            merged["warning"] = warn
        out.append(merged)

    # 2. Append only providers confirmed by the installed Hermes runtime.
    # The public model manifest also contains vendor/model groupings that are
    # not selectable auth providers (historically these became fake OAuth
    # cards such as Mistral/Google CLI). Without Hermes, the curated list is
    # the honest fallback and no unverified cards are synthesized.
    for slug, runtime_meta in sorted(local_meta.items()):
        if slug in curated_slugs:
            continue
        # Credential schemes such as Vertex ADC/service-account files or
        # composite MOA presets need dedicated fields the wizard does not
        # currently implement. Hiding them is more honest than labelling them
        # "Local" or asking the user to paste a meaningless API key.
        auth_type = str(runtime_meta.get("auth_type") or "")
        if (
            not runtime_meta.get("env_vars")
            and not runtime_meta.get("oauth_capable")
            and auth_type not in {"noauth", "external_process"}
        ):
            continue
        hermes_models = catalog.get(slug, [])
        out.append(_synthesize_discovered_provider(slug, hermes_models, runtime_meta))

    total_models = sum(len(v) for v in catalog.values())
    selectable_models = sum(
        len(provider.get("models") or []) + len(provider.get("oauth_models") or [])
        for provider in out
    )
    summary = {
        "version":            "",   # reserved — `_probe_version` can fill this later
        "providers_detected": len(catalog),
        "models_detected":    total_models,
    }
    return {
        "categories": CATEGORIES,
        "providers":  out,
        "source":     "merged",
        "hermes":     summary,
        "selectable_model_count": selectable_models,
    }
