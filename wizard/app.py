"""
IRIS Setup Wizard — backend

Comprehensive setup wizard with:
- Hardware auto-detection (OS, RAM, audio devices)
- Hermes connectivity testing
- TTS voice testing & selection
- 3D model upload & auto-rigging
- Configuration persistence to ~/.iris/config.json
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import ipaddress
import json
import logging
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, File, Form, Header, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from core.static_assets import LocalWebStaticFiles
from pydantic import BaseModel, ConfigDict, Field

from core.animation_store import ANIMATION_EXTENSIONS
from core.asset_uploads import (
    AVATAR_MAX_BYTES,
    AssetUploadError,
    atomic_avatar_write,
    read_bounded,
    upload_filename,
    validate_asset,
)
from core.upload_routes import asset_upload_route
from core.config import IRISConfig, get_iris_home
from core.file_lock import interprocess_file_lock
from core.hermes_runtime import (
    hermes_command_prefix,
    get_hermes_config_path,
    get_hermes_repo,
    hermes_subprocess_env,
    resolve_hermes_cli,
    resolve_hermes_python,
    run_hermes_cli,
)
from core import runtime_descriptor as runtime_policy
from core.identity_api import (
    _identity_validation_error,
    router as identity_router,
    sync_hermes_identity_from_config,
)
from core.identity_profiles import (
    MAX_PERSONALITY_TRAITS,
    normalize_trait_ids,
    record_identity_history,
)
from core.personality import (
    SUPPORTED_PERSONALITY_TEMPLATES,
    normalize_personality_template,
)
from core.request_security import (
    is_loopback_host,
    is_trusted_http_host,
    is_trusted_local_origin,
)
from core.rigging_jobs import RigMethod
from core.version import __version__ as IRIS_VERSION
from core.hermes_wake import (
    configure_hermes_wake,
    normalize_custom_wake_phrase,
    read_hermes_wake_settings,
    router as hermes_wake_router,
)
from core.companion_lifecycle import (
    companion_operation_lock,
    router as companion_lifecycle_router,
)

from wizard.provider_auth import provider_auth, router as provider_auth_router
from wizard.messaging import messaging, router as messaging_router, set_restart_readiness
from wizard.licenses import router as licenses_router

logger = logging.getLogger(__name__)


@contextlib.asynccontextmanager
async def wizard_lifespan(_app):
    from core.profile_migration import migrate_animation_profile
    from core.profile_paths import async_profile_writer, get_profile_paths

    paths = get_profile_paths()
    factory = runtime_policy.get_runtime().assets_root / "animations"
    async with async_profile_writer(
        "wizard", paths=paths,
        prepare=lambda: migrate_animation_profile(factory, paths.animations),
    ):
        try:
            yield
        finally:
            await provider_auth.close()
            await messaging.close()


app = FastAPI(title="IRIS Setup Wizard", version=IRIS_VERSION, lifespan=wizard_lifespan)


@app.exception_handler(runtime_policy.RuntimePolicyError)
async def runtime_policy_error(_request: Request, exc) -> JSONResponse:
    return JSONResponse({"detail": str(exc), "code": exc.code}, status_code=exc.status_code)


@app.exception_handler(runtime_policy.RuntimeDescriptorError)
async def runtime_descriptor_error(_request: Request, _exc) -> JSONResponse:
    return JSONResponse({"detail": "El descriptor de runtime no es válido.",
                         "code": "runtime_descriptor_invalid"}, status_code=503)


@app.get("/api/runtime-info")
async def runtime_info() -> JSONResponse:
    return JSONResponse(runtime_policy.runtime_public_info(), headers={"Cache-Control": "no-store"})

# Identity patch endpoints (shared with websocket_server so the side
# panel hits the same surface whichever backend is serving it).
app.include_router(identity_router)
app.include_router(hermes_wake_router)
app.include_router(companion_lifecycle_router)

app.include_router(provider_auth_router)
app.include_router(messaging_router)
app.include_router(licenses_router)

# CORS — wizard is a LOCAL setup tool. Relative requests from a LAN client
# are same-origin and need no CORS rule. Wildcard ``*`` would let any website
# the user visits read API keys / OAuth tokens cached in the wizard,
# so we only allow loopback aliases for local cross-port workflows.
_CORS_ORIGIN_REGEX = (
    r"^https?://(127\.0\.0\.1|localhost|\[::1\])(:\d+)?$"
)
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=_CORS_ORIGIN_REGEX,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    allow_credentials=False,
)


@app.middleware("http")
async def enforce_wizard_mutation_origin(request: Request, call_next):
    """Reject untrusted authorities on every HTTP method, then check Origin."""
    authorities = request.headers.getlist("host")
    if len(authorities) != 1 or not is_trusted_http_host(authorities[0]):
        return JSONResponse(
            {"status": "error", "detail": "Untrusted HTTP Host"},
            status_code=403,
            headers={"Cache-Control": "no-store"},
        )
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin", "").strip()
        peer = request.client.host if request.client else ""
        if not origin and peer and not is_loopback_host(peer):
            return JSONResponse(
                {"status": "error", "detail": "Remote mutations require a browser origin"},
                status_code=403,
                headers={"Cache-Control": "no-store"},
            )
        if origin and not is_trusted_local_origin(
            origin,
            request.url.hostname,
            request_scheme=request.url.scheme,
            request_port=request.url.port,
        ):
            return JSONResponse(
                {"status": "error", "detail": "Untrusted browser origin"},
                status_code=403,
                headers={"Cache-Control": "no-store"},
            )
    response = await call_next(request)
    path = request.scope["path"]
    if path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store, private" if path == "/api/provider-models" else "no-store"
    elif path == "/ui" or (path.startswith("/ui/") and (path.endswith("/") or path.endswith(".html"))):
        # The page names its versioned scripts; a heuristically cached page kept loading the
        # previous wizard.js after an update (review 2026-10-19). Revalidate: ETag keeps it cheap.
        response.headers["Cache-Control"] = "no-cache"
    return response

# ── Serve wizard UI ──
wizard_ui_dir = Path(__file__).parent / "ui"
if wizard_ui_dir.exists():
    app.mount("/ui", LocalWebStaticFiles(directory=str(wizard_ui_dir), html=True), name="ui")

# Reuse the production Theme Studio artwork in the installer previews.  This
# is a read-only static mount; the wizard never edits Theme Studio assets.
theme_assets_dir = Path(__file__).parents[1] / "face" / "public" / "assets" / "os"
if theme_assets_dir.exists():
    app.mount(
        "/theme-assets",
        StaticFiles(directory=str(theme_assets_dir)),
        name="theme-assets",
    )

# IRIS OS dialogs and icons: the wizard shows the same ones as Iris (face/js/iris_dialog.js,
# its stylesheet and face/js/os_icons.js). Only these files are served, read-only; the rest
# of Face is not exposed.
_SHARED_UI = {
    "iris_dialog.js": ("js", "text/javascript"),
    "iris_dialog.css": ("css", "text/css"),
    "os_icons.js": ("js", "text/javascript"),
}


@app.get("/shared/{name}", include_in_schema=False)
async def shared_ui_file(name: str) -> FileResponse:
    entry = _SHARED_UI.get(name)
    path = Path(__file__).parents[1] / "face" / entry[0] / name if entry else None
    if path is None or not path.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(path, media_type=entry[1], headers={"Cache-Control": "no-cache"})


# ────────────────────────────────────────────────────────
# Pydantic models
# ────────────────────────────────────────────────────────

class ConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # --- Identity & Personality ---
    assistant_name: str = Field("IRIS", min_length=1, max_length=48)
    language: str = "es"
    personality_template: str = Field("iris", max_length=32)
    personality_instructions: str = Field("", max_length=4000)
    personality_traits: list[str] = Field(default_factory=list, max_length=MAX_PERSONALITY_TRAITS)
    custom_backstory: str = Field("", max_length=4000)
    include_iris_backstory: bool = True
    legacy_personality_file_enabled: bool = False
    custom_personality: str = Field("", max_length=4000)  # legacy combined field
    visual_theme: Literal["iris", "sunrise", "verdant", "rose"] = "iris"
    visual_theme_selected: bool = False

    # --- Hermes Agent ---
    hermes_url: str = "http://127.0.0.1:8642"
    hermes_api_key: str = ""
    hermes_model: str = "hermes-agent"
    # Which wizard branch the user is in: "full" (install + pick
    # provider from scratch), "quick" (use existing Hermes config),
    # "repair" (restart with existing config), "skip" (Hermes already
    # running). Determines whether /api/save-config is allowed to
    # rewrite Hermes' credential files. Only "full" gets the write.
    hermes_mode: Literal["full", "quick", "repair", "skip", ""] = ""

    # --- LLM selection (Modo Completo only) ---
    # The wizard UI collects these in Modo Completo; they are written
    # to Hermes' own config (~/.hermes/.env + config.yaml) but NEVER
    # persisted in IRIS' local config (IRISConfig.save() strips them).
    # In Modo Rápido / Reparar these stay empty and Hermes' existing
    # credential setup is left untouched.
    llm_provider: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_use_oauth: bool = False
    llm_base_url: str = ""

    # --- Messaging channels (optional) ---
    channels: list[str] = Field(default_factory=list)
    telegram_bot_token: str = ""
    discord_bot_token: str = ""
    slack_bot_token: str = ""

    # --- STT ---
    stt_provider: str = "whisper-local"
    whisper_model: str = "base"
    whisper_api_key: str = ""

    # --- TTS ---
    #
    # Keep every TTS field the wizard can actively edit here. The save
    # handler merges submitted fields into the existing IRISConfig, so
    # fields owned by another screen are preserved instead of resetting.
    tts_provider: str = "edge-tts"
    tts_allow_remote_fallback: bool = False
    edge_tts_voice: str = "es-MX-DaliaNeural"
    coqui_model: str = "tts_models/es/css10/vits"
    coqui_ref_audio: str = ""
    coqui_language: str = "es"
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = ""
    minimax_api_key: str = ""
    fish_audio_api_key: str = ""
    fish_audio_voice_id: str = ""
    fish_audio_voice_name: str = Field("", max_length=120)
    fish_audio_model: Literal["s2.1-pro-free", "s2.1-pro", "s2-pro"] = "s2.1-pro-free"
    minimax_voice_id: str = "Spanish_Wiselady"
    minimax_tts_model: str = "speech-2.8-hd"
    fish_speech_url: str = "http://127.0.0.1:8080"
    fish_speech_ref_audio: str = ""
    fish_speech_ref_text: str = ""
    fish_speech_reference_id: str = ""
    fish_speech_format: str = "wav"
    # Kokoro default: Spanish female. Older wizard builds used invalid
    # ``ef_*`` ids for English; current ids follow Kokoro's language prefix.
    kokoro_voice: str = "ef_dora"
    # NeuTTS — local 3-second voice cloner. Reference audio + transcript
    # are filled by the /api/clone-voice endpoint when the user records.
    neutts_model_repo: str = "neuphonic/neutts-nano-spanish-q4-gguf"
    neutts_codec_repo: str = "neuphonic/neucodec"
    neutts_ref_audio:  str = ""
    neutts_ref_text:   str = ""
    neutts_device:     Literal["cpu", "cuda", "metal", "rocm"] = "cpu"
    # Hugging Face speech-to-speech: IRIS uses its Qwen3-TTS component,
    # not the competing VAD/STT/LLM stages. Base models require the local
    # reference fields populated by /api/clone-voice.
    hf_s2s_model: str = "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice"
    hf_s2s_speaker: str = "Aiden"
    hf_s2s_language: str = "spanish"
    hf_s2s_ref_audio: str = ""
    hf_s2s_ref_text: str = ""
    hf_s2s_device: Literal["auto", "cpu", "cuda", "metal", "rocm"] = "auto"

    # --- Hermes native wake word ---
    # This is wizard-only routing state and is excluded from IRISConfig.
    # Hermes' config.yaml remains the single source of truth.
    # Missing means preserve Hermes' native config. The current wizard sends
    # its explicit clean-install selection; older/API clients must not reset an
    # existing custom phrase merely because they predate this field.
    hermes_wake_mode: Literal["keep", "off", "hey_hermes", "custom"] = "keep"
    hermes_wake_phrase: str = Field("", max_length=80)

    # --- Avatar ---
    # Empty: IRIS' included VRM. A path: the person's own model.
    avatar_vrm_path: str = ""

    # --- Perception ---
    perception_enabled: bool = False
    perception_camera: int = 0


class TTSPreviewRequest(BaseModel):
    """Provider-specific preview values that are safe to keep out of URLs."""

    model_config = ConfigDict(extra="forbid")

    provider: str = Field("edge-tts", max_length=32)
    voice: str = Field("", max_length=256)
    model: str = Field("", max_length=256)
    text: str = Field("Hola mundo, soy IRIS.", max_length=240)
    fish_url: str = Field("", max_length=512)


class VisualThemeUpdate(BaseModel):
    """Small, safe update used by the first installer screen."""

    model_config = ConfigDict(extra="forbid")

    theme: Literal["iris", "sunrise", "verdant", "rose"]


class FishVoicesQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field("", max_length=80)
    self_only: bool = False
    language: str = Field("", max_length=8)
    page: int = Field(1, ge=1, le=50)


class FishSampleQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    voice_id: str = Field(..., max_length=128)


# Fish's public catalog answers any key, so a wrong one would only surface on
# the first preview. Checked once per key (by digest) for ten minutes.
_FISH_KEYS_CHECKED: dict[str, float] = {}
_FISH_KEY_CHECK_SECONDS = 600.0


async def _check_fish_key(key: str) -> None:
    from core.fish_audio import check_key

    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    now = time.monotonic()
    if now - _FISH_KEYS_CHECKED.get(digest, -_FISH_KEY_CHECK_SECONDS) < _FISH_KEY_CHECK_SECONDS:
        return
    await check_key(key)
    _FISH_KEYS_CHECKED[digest] = now


@app.post("/api/fish-audio/voices")
async def fish_audio_voices(request: Request, query: FishVoicesQuery,
                            tts_api_key: str = Header("", alias="X-IRIS-TTS-Key")) -> JSONResponse:
    _enforce_local_install_request(request, "preview-tts", "las voces de Fish Audio")
    from core.fish_audio import FishAudioError, list_voices, validate_language

    try:
        validate_language(query.language)
    except FishAudioError as exc:
        return JSONResponse({"message": str(exc)}, status_code=400)
    key = tts_api_key.strip() or IRISConfig.load().fish_audio_api_key
    try:
        if key and not query.self_only:   # listing the account's own voices checks the key itself
            await _check_fish_key(key)
        result = await list_voices(key, query.query, query.self_only, query.language, query.page)
        return JSONResponse({**result, "page": query.page}, headers={"Cache-Control": "no-store"})
    except FishAudioError as exc:
        return JSONResponse({"message": str(exc)}, status_code=400 if not key else 502)


@app.post("/api/fish-audio/sample")
async def fish_audio_sample(request: Request, query: FishSampleQuery,
                            tts_api_key: str = Header("", alias="X-IRIS-TTS-Key")) -> Response:
    """The sample Fish publishes with a voice, relayed so the page never loads it from Fish."""
    _enforce_local_install_request(request, "preview-tts", "la muestra de Fish Audio")
    from core.fish_audio import FishAudioError, sample_audio

    key = tts_api_key.strip() or IRISConfig.load().fish_audio_api_key
    try:
        audio = await sample_audio(key, query.voice_id)
    except FishAudioError as exc:
        return JSONResponse({"message": str(exc)}, status_code=400 if not key else 502)
    return Response(audio, media_type="audio/mpeg", headers={"Cache-Control": "no-store"})


def _merge_config_update(
    config: ConfigUpdate,
    existing_config: IRISConfig | None = None,
) -> IRISConfig:
    """Merge wizard-owned values without erasing unrelated settings."""
    current = existing_config or IRISConfig.load()
    submitted = config.model_dump(
        exclude={"hermes_mode", "hermes_wake_mode", "hermes_wake_phrase", "llm_base_url"},
        exclude_unset=True,
    )

    # These values never travel back to the browser. A blank field during a
    # revisit therefore means "unchanged", not "delete my stored secret".
    secret_fields = {
        "hermes_api_key",
        "whisper_api_key",
        "elevenlabs_api_key",
        "minimax_api_key",
        "fish_audio_api_key",
        "telegram_bot_token",
        "discord_bot_token",
        "slack_bot_token",
    }
    for field in secret_fields:
        if submitted.get(field) == "" and getattr(current, field, ""):
            submitted.pop(field)

    return IRISConfig(**{**current.model_dump(), **submitted}).inherit_loaded_snapshot(current)


# ────────────────────────────────────────────────────────
# Routes
# ────────────────────────────────────────────────────────


@app.post("/api/save-visual-theme")
async def save_visual_theme(payload: VisualThemeUpdate) -> JSONResponse:
    """Persist an explicit visual-edition choice without touching Hermes."""
    try:
        config = IRISConfig.load()
        config.visual_theme = payload.theme
        config.visual_theme_selected = True
        await asyncio.to_thread(config.save)
    except Exception as exc:
        return JSONResponse(
            {
                "status": "error",
                "message": "No se pudo guardar la edición visual de IRIS OS.",
                "detail": str(exc),
            },
            status_code=500,
        )
    return JSONResponse({"status": "saved", "theme": payload.theme})


class SecurityAcknowledgement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    accepted: bool


@app.post("/api/hermes-security-ack")
async def save_hermes_security_ack(payload: SecurityAcknowledgement, request: Request) -> JSONResponse:
    """Record the user's answer to Hermes' security notice in the profile.

    Packaged Iris asks once per profile before the Wizard first starts or
    restarts the bundled engine. Only this flag changes; nothing else is saved.
    """
    from core.config import set_profile_flag

    _enforce_local_browser_action(request, "El aviso de seguridad")
    try:
        await asyncio.to_thread(set_profile_flag, "hermes_security_acknowledged", payload.accepted)
    except Exception:
        return JSONResponse({"status": "error", "message": "No se pudo guardar la respuesta al aviso."},
                            status_code=500)
    return JSONResponse({"status": "saved", "accepted": payload.accepted})


def _hermes_runtime_health(timeout: float = 15.0) -> tuple[bool, str]:
    """Verify that Hermes' managed headless runtime can actually import.

    ``hermes --version`` intentionally loads very little and can succeed when
    the environment is missing packages required by ``hermes serve``. IRIS
    uses that server for the native wake bridge, so version-only detection
    would incorrectly label a partial install as healthy.
    """
    from core.profile_paths import ProfilePathError

    try:
        runtime = runtime_policy.get_runtime()
        python = resolve_hermes_python()
        repo = get_hermes_repo()
        if not python or not repo.exists():
            return False, "No se encontró el intérprete administrado de Hermes."
        probe = "import tui_gateway.ws; print('IRIS_HERMES_RUNTIME_OK')"
        command = (
            [*hermes_command_prefix()[:-1], "-c", probe]
            if runtime.packaged
            else [python, "-c", probe]
        )
        environment = hermes_subprocess_env()
    except ProfilePathError as exc:
        return False, f"El perfil de Hermes no es válido: {exc}"
    try:
        result = subprocess.run(
            command,
            cwd=str(repo),
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=(getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"El runtime administrado de Hermes no respondió: {exc}"
    output = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
    if result.returncode == 0 and "IRIS_HERMES_RUNTIME_OK" in output:
        return True, ""
    missing = re.search(r"No module named ['\"]([^'\"]+)['\"]", output)
    if missing:
        return False, f"Falta el módulo {missing.group(1)} en el entorno administrado de Hermes."
    last_line = next((line.strip() for line in reversed(output.splitlines()) if line.strip()), "")
    return False, (last_line[:500] or f"El runtime terminó con código {result.returncode}.")


def _hermes_installation_state() -> dict[str, Any]:
    runtime = runtime_policy.get_runtime()
    result = _hermes_installation_state_legacy()
    result["runtime"] = runtime_policy.runtime_public_info()
    if runtime.packaged:
        result["installation_kind"] = "bundled"
        result["residual_launcher"] = False
        if result["status"] in {"missing", "damaged"}:
            # Nothing to install or reinstall here: only repairing Iris restores it.
            result["mode"] = "package_repair"
            result["message"] = "El motor incluido no está disponible. Repara Iris; no se instalará otro Hermes."
        elif result["status"] == "needs_setup":
            result["message"] = result["message"].replace("Hermes funciona", "El motor incluido funciona", 1)
        elif result["status"] == "installed":
            result["message"] = "El motor incluido está configurado y puede activarse con tu configuración guardada."
    return result


def _hermes_installation_state_legacy() -> dict[str, Any]:
    """Classify the launcher by execution, not by path existence alone."""
    from core.hermes_compatibility import (
        IRIS_MIN_HERMES_VERSION,
        IRIS_MIN_HERMES_VERSION_TEXT,
        IRIS_VALIDATED_HERMES_SERIES,
        IRIS_VALIDATED_HERMES_VERSION,
        parse_hermes_version,
    )

    cli_path = resolve_hermes_cli()
    if not cli_path:
        return {
            "status": "missing",
            "mode": "full",
            "message": "Hermes no está instalado. El asistente puede instalarlo desde cero.",
            "cli_path": "",
            "version": "",
            "config_exists": False,
            "repo_exists": False,
            "compatibility": {"status": "not_installed", "label": "No instalado"},
        }

    repo = get_hermes_repo()
    repo_exists = repo.exists()
    managed_python = resolve_hermes_python()
    version_result = run_hermes_cli(["--version"], timeout=8)
    output = str(version_result.get("output") or "").strip()
    version = output.splitlines()[0] if output else ""
    config_path = get_hermes_config_path()
    config_exists = config_path.exists()

    # The official uninstaller can coexist with an older global/pip console
    # script elsewhere on PATH.  That orphan may still answer ``--version``,
    # but it is not the managed Hermes runtime IRIS needs for ``serve``, wake
    # word and voice.  Calling this a repair hides the normal install path and
    # confuses a clean reinstall after ``hermes uninstall``.  Treat the
    # supported runtime as absent while retaining the residual path for an
    # honest diagnostic; the official installer will create the managed
    # layout and preserve any user profile that still exists.
    if not repo_exists and not managed_python:
        diagnostic = (
            f"Se encontró un launcher global residual en {cli_path}"
            + (f" ({version})" if version else "")
            + ", pero no existe el runtime administrado de Hermes."
        )
        return {
            "status": "missing",
            "mode": "full",
            "message": (
                "No se encontró una instalación administrada de Hermes. "
                "El asistente puede instalarla desde cero y conservar los datos del perfil."
            ),
            "cli_path": cli_path,
            "version": version,
            "config_exists": config_exists,
            "repo_exists": False,
            "diagnostic": diagnostic,
            "residual_launcher": True,
            "installation_kind": "unmanaged_launcher",
            "compatibility": {
                "status": "not_installed",
                "label": "Runtime administrado no instalado",
            },
        }

    if not version_result.get("success"):
        diagnostic = str(version_result.get("error") or output or "El lanzador no respondió.")[:500]
        return {
            "status": "damaged",
            "mode": "repair",
            "message": "Se encontró Hermes, pero su lanzador no funciona. Requiere reparación.",
            "cli_path": cli_path,
            "version": "",
            "config_exists": config_exists,
            "repo_exists": repo_exists,
            "diagnostic": diagnostic,
            "compatibility": {"status": "unavailable", "label": "No verificable"},
        }

    parsed_version = parse_hermes_version(output)
    if parsed_version is None:
        compatibility = {
            "status": "unknown",
            "label": "Versión no reconocida",
            "minimum": IRIS_MIN_HERMES_VERSION_TEXT,
        }
    elif parsed_version < IRIS_MIN_HERMES_VERSION:
        compatibility = {
            "status": "update_recommended",
            "label": f"Actualización recomendada (IRIS requiere {IRIS_MIN_HERMES_VERSION_TEXT}+)",
            "minimum": IRIS_MIN_HERMES_VERSION_TEXT,
            "validated": IRIS_VALIDATED_HERMES_VERSION,
        }
    elif parsed_version[:2] not in IRIS_VALIDATED_HERMES_SERIES:
        compatibility = {
            "status": "runtime_validation",
            "label": f"Fuera de la familia validada {IRIS_VALIDATED_HERMES_VERSION}",
            "minimum": IRIS_MIN_HERMES_VERSION_TEXT,
            "validated": IRIS_VALIDATED_HERMES_VERSION,
        }
    else:
        compatibility = {
            "status": "ready",
            "label": f"Compatible con IRIS ({IRIS_VALIDATED_HERMES_VERSION})",
            "minimum": IRIS_MIN_HERMES_VERSION_TEXT,
            "validated": IRIS_VALIDATED_HERMES_VERSION,
        }

    runtime_ok, runtime_diagnostic = _hermes_runtime_health()
    if not runtime_ok:
        return {
            "status": "damaged",
            "mode": "repair",
            "message": (
                "Hermes muestra su versión, pero su entorno administrado está incompleto. "
                "Requiere reparación oficial."
            ),
            "cli_path": cli_path,
            "version": version,
            "config_exists": config_exists,
            "repo_exists": repo_exists,
            "diagnostic": runtime_diagnostic,
            "compatibility": compatibility,
        }

    config_health = "missing"
    config_diagnostic = ""
    if config_exists:
        try:
            import yaml  # type: ignore[import-not-found]

            parsed_config = yaml.safe_load(config_path.read_text(encoding="utf-8", errors="replace"))
            if not isinstance(parsed_config, dict) or not parsed_config:
                config_health = "empty"
            else:
                # A wake-word/theme-only config is not an inference setup.
                # Calling it ready lets Hermes fall through to its bundled
                # provider/model defaults (historically perceived as IRIS
                # forcing Claude). Quick mode is safe only after the user has
                # an explicit provider and model selection on disk.
                model = parsed_config.get("model")
                has_provider = bool(
                    isinstance(model, dict)
                    and str(model.get("provider") or "").strip()
                )
                has_model = bool(
                    isinstance(model, dict)
                    and str(model.get("default") or "").strip()
                )
                config_health = "ready" if has_provider and has_model else "incomplete"
        except ImportError:
            config_health = "unknown"
        except (OSError, yaml.YAMLError) as exc:
            config_health = "invalid"
            config_diagnostic = str(exc)[:500]

    if config_health in {"missing", "empty", "incomplete", "invalid"}:
        config_reason = {
            "missing": "todavía no tiene una configuración base",
            "empty": "tiene una configuración vacía",
            "incomplete": "todavía no tiene proveedor y modelo explícitos",
            "invalid": "tiene un config.yaml inválido",
        }[config_health]
        return {
            "status": "needs_setup",
            "mode": "full",
            "message": f"Hermes funciona, pero {config_reason}.",
            "cli_path": cli_path,
            "version": version,
            "config_exists": config_exists,
            "config_health": config_health,
            "diagnostic": config_diagnostic,
            "repo_exists": repo_exists,
            "compatibility": compatibility,
        }

    return {
        "status": "installed",
        "mode": "quick",
        "message": "Hermes está instalado y su lanzador responde.",
        "cli_path": cli_path,
        "version": version,
        "config_exists": True,
        "config_health": config_health,
        "repo_exists": repo_exists,
        "compatibility": compatibility,
    }

@app.get("/")
async def root():
    """Redirect to wizard UI."""
    return JSONResponse({"message": "IRIS Setup Wizard", "ui": "/ui/index.html"})


@app.get("/api/detect-hardware")
async def detect_hardware() -> JSONResponse:
    """Detect hardware and recommend only setup paths IRIS can substantiate."""
    from core.hermes_compatibility import platform_support
    from wizard.providers import effective_auth_mode_for
    from wizard.system_profile import (
        cpu_profile,
        gpu_profile,
        probe_local_model_servers,
        setup_recommendations,
        total_ram_gb,
    )

    cpu = await asyncio.to_thread(cpu_profile)
    gpu = await asyncio.to_thread(gpu_profile)
    ram_gb = await asyncio.to_thread(total_ram_gb)
    local_servers = await probe_local_model_servers()
    install_state = await asyncio.to_thread(_hermes_installation_state)
    support = platform_support()

    info: dict[str, Any] = {
        "os": platform.system(),
        "os_version": platform.version(),
        "machine": platform.machine(),
        "node": platform.node(),
        "python_version": platform.python_version(),
        "cpu": cpu,
        "ram_gb": ram_gb,
        "gpu": gpu,
        "cuda_available": gpu.get("acceleration") == "cuda",
        "gpu_name": gpu.get("name") or "",
        "platform_support": support,
        "local_model_servers": local_servers,
    }

    # Node.js
    node_ok, node_version = await asyncio.to_thread(_probe_command, "node", "--version")
    info["node_version"] = node_version if node_ok else None

    # Audio devices
    try:
        import sounddevice as sd
        devices = sd.query_devices()
        info["audio_input"] = [
            {"index": i, "name": d["name"], "channels": d["max_input_channels"]}
            for i, d in enumerate(devices) if d["max_input_channels"] > 0
        ]
        info["audio_output"] = [
            {"index": i, "name": d["name"], "channels": d["max_output_channels"]}
            for i, d in enumerate(devices) if d["max_output_channels"] > 0
        ]
    except Exception:
        info["audio_input"] = []
        info["audio_output"] = []

    cfg = IRISConfig.load()
    existing_provider = (cfg.llm_provider or "").strip()
    existing_configured = bool(
        existing_provider
        and (
            effective_auth_mode_for(existing_provider) in {"none", "api-optional"}
            or cfg.llm_api_key
            or cfg.llm_use_oauth
        )
    )
    info["recommendations"] = setup_recommendations(
        ram_gb=ram_gb,
        cpu_threads=int(cpu.get("logical_cores") or 1),
        gpu=gpu,
        local_servers=local_servers,
        existing_provider=existing_provider if install_state["status"] != "missing" else "",
        existing_configured=existing_configured,
    )
    info["recommended_engine"] = "hermes"
    info["hermes_state"] = install_state["status"]
    info["hermes_installed"] = install_state["status"] != "missing"
    info["hermes_runnable"] = install_state["status"] in {"installed", "needs_setup"}
    info["hermes_version"] = install_state.get("version") or None
    info["hermes_compatibility"] = install_state.get("compatibility") or {}
    info["hermes_path"] = install_state.get("cli_path") or ""
    info["hermes_repo"] = str(get_hermes_repo()) if install_state["status"] != "missing" else ""
    info["hermes_residual_launcher"] = bool(install_state.get("residual_launcher"))
    if runtime_policy.get_runtime().packaged:
        # The bundled engine is part of Iris: no launcher or checkout to point at.
        info.update(hermes_path="", hermes_repo="", hermes_bundled=True)

    return JSONResponse(info)


def resolve_cmd(cmd_name: str) -> str:
    """Resolve the command executable path, crucial for Windows .cmd/.bat wrappers."""
    path = shutil.which(cmd_name)
    return path if path else cmd_name


def _hermes_child_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Return a sanitized environment dict for spawning ``hermes.exe``.

    Strips parent-venv pointers so Hermes' Python launcher uses ITS OWN
    venv (the one its launcher exe was created from) rather than the
    venv we're currently running under.
    """
    env = hermes_subprocess_env()
    if extra:
        # This call only overlays the selected provider key, never runtime pointers.
        env.update({key: value for key, value in extra.items()
                    if key.endswith("_API_KEY")})
    return env


def _resolve_hermes_invocation() -> tuple[list[str] | None, str]:
    """Build a hermes-launching argv that bypasses the broken launcher.

    Returns ``(argv_prefix, diag_message)``. ``argv_prefix`` is a list
    you append CLI args to before spawning; it's ``None`` if Hermes
    isn't installed in the expected layout, in which case callers
    should fall back to ``hermes.exe`` directly.

    On Windows, setuptools-generated console_script launchers (the
    ``hermes.exe`` zipapp) sometimes skip site-packages init, so
    ``import hermes_cli`` fails inside the embedded ``__main__.py``.
    We sidestep this by invoking the venv's ``python.exe`` and giving
    it the SAME ``hermes.exe`` zipapp as the script argument — Python
    treats the .exe as a zipapp, runs the embedded ``__main__.py``,
    and crucially DOES process .pth files first, so the editable
    finder gets installed and ``hermes_cli`` is importable.
    """
    runtime = runtime_policy.get_runtime()
    if runtime.hermes:
        return hermes_command_prefix(), "Runtime incluido de Iris" if runtime.packaged else "Runtime Hermes declarado"
    hermes_exe = resolve_hermes_cli()
    if not hermes_exe:
        return None, "shutil.which('hermes') = None — PATH no contiene Hermes"
    try:
        scripts_dir = Path(hermes_exe).parent
        py = (scripts_dir / "python.exe") if os.name == "nt" else (scripts_dir / "python")
        if not py.exists():
            return None, f"python.exe no existe en {scripts_dir}"
        venv_root = scripts_dir.parent
        hermes_root = venv_root.parent
        init_py = hermes_root / "hermes_cli" / "__init__.py"
        if not init_py.exists():
            return None, f"hermes_cli/__init__.py no existe en {hermes_root}"
        # zipapp invocation: python.exe <hermes.exe> <args...>
        return [str(py), str(hermes_exe)], f"OK → bypass via python.exe + zipapp ({py})"
    except Exception as exc:
        return None, f"excepción inspeccionando layout: {exc!r}"

_HERMES_INSTALL_LOCK = asyncio.Lock()
_HERMES_INSTALLER_URLS = {
    "Windows": "https://hermes-agent.nousresearch.com/install.ps1",
    "default": "https://hermes-agent.nousresearch.com/install.sh",
}
_INSTALLER_MAX_BYTES = 3_000_000


def _trusted_request_origin(origin: str) -> bool:
    """Accept only loopback/private origins for wizard mutations."""
    try:
        parsed = urlparse(origin)
    except (TypeError, ValueError):
        return False
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    hostname = parsed.hostname.lower()
    if hostname == "localhost":
        return True
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return False
    return address.is_loopback or address.is_private


def _enforce_local_install_request(
    request: Request,
    intent: str = "install-hermes",
    subject: str = "Hermes",
) -> None:
    """Reject cross-site/prefetch attempts against the mutating installer."""
    if request.headers.get("x-iris-intent") != intent:
        raise HTTPException(status_code=403, detail=f"Falta la intención explícita para {subject}.")
    if request.headers.get("sec-fetch-site", "").lower() == "cross-site":
        raise HTTPException(status_code=403, detail="La instalación no acepta solicitudes cross-site.")
    origin = request.headers.get("origin", "").strip()
    if origin and not _trusted_request_origin(origin):
        raise HTTPException(status_code=403, detail=f"El origen no está autorizado para modificar {subject}.")


def _enforce_local_browser_action(request: Request, action: str) -> None:
    """Reject cross-site browser attempts against a local side-effecting flow."""
    if request.headers.get("sec-fetch-site", "").lower() == "cross-site":
        raise HTTPException(status_code=403, detail=f"{action} no acepta solicitudes cross-site.")
    origin = request.headers.get("origin", "").strip()
    if origin and not _trusted_request_origin(origin):
        raise HTTPException(status_code=403, detail=f"El origen no está autorizado para {action.lower()}.")


def _probe_command(command: str, *args: str) -> tuple[bool, str]:
    """Run a small, UTF-8 safe dependency probe without invoking a shell."""
    path = shutil.which(command)
    if not path:
        return False, ""
    try:
        result = subprocess.run(
            [path, *args],
            capture_output=True,
            text=True,
            timeout=8,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            encoding="utf-8",
            errors="replace",
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False, ""
    output = (result.stdout or result.stderr or "").strip().splitlines()
    return result.returncode == 0, output[0] if output else ""


def _managed_dependency(name: str, installed: bool, value: str, detail: str) -> dict[str, Any]:
    """Describe a dependency Hermes itself will provision when absent."""
    return {
        "name": name,
        "status": "ok" if installed else "managed",
        "version": value,
        "required": False,
        "managed_by_installer": not installed,
        "detail": detail,
    }


def _tool_version_at_least(value: str, minimum: tuple[int, ...]) -> bool:
    """Parse the first dotted version in a tool banner."""
    match = re.search(r"(?<!\d)(\d+)(?:\.(\d+))?(?:\.(\d+))?", value or "")
    if not match:
        return False
    parsed = tuple(int(part or 0) for part in match.groups())
    padded = parsed + (0,) * max(0, len(minimum) - len(parsed))
    return padded[:len(minimum)] >= minimum


def _hermes_install_readiness() -> dict[str, Any]:
    runtime = runtime_policy.get_runtime()
    if runtime.packaged:
        return {"all_required_ok": True, "deps": {}, "blocking_dependencies": [],
                "install_action": "unavailable", "runtime": runtime_policy.runtime_public_info(),
                "hint": "El motor se incluye en Iris. Su instalación independiente está deshabilitada."}
    result = _hermes_install_readiness_legacy()
    result["runtime"] = runtime_policy.runtime_public_info()
    return result


def _hermes_install_readiness_legacy() -> dict[str, Any]:
    """Check only the prerequisites the *official* installer really needs."""
    from core.hermes_compatibility import platform_support

    system = platform.system()
    is_windows = system == "Windows"
    is_linux = system == "Linux"
    deps: dict[str, dict[str, Any]] = {}

    git_ok, git_version = _probe_command("git", "--version")
    deps["git"] = {
        "name": "Git",
        "status": "ok" if git_ok else ("managed" if is_windows else "missing"),
        "version": git_version,
        "required": not is_windows,
        "managed_by_installer": is_windows and not git_ok,
        "detail": (
            "Hermes usará Git ya instalado."
            if git_ok
            else "El instalador de Windows descarga Git portátil de forma aislada."
            if is_windows
            else "Instala Git y vuelve a comprobar el entorno."
        ),
    }

    python_ok, python_version = _probe_command("python", "--version")
    if not python_ok:
        python_ok, python_version = _probe_command("python3", "--version")
    deps["python"] = _managed_dependency(
        "Python 3.11+", python_ok, python_version,
        "Hermes gestiona una versión compatible mediante uv si hace falta.",
    )

    node_ok, node_version = _probe_command("node", "--version")
    node_compatible = node_ok and _tool_version_at_least(node_version, (22, 22, 0))
    deps["node"] = _managed_dependency(
        "Node.js 22.22+", node_compatible, node_version,
        (
            "Hermes usará la versión compatible instalada."
            if node_compatible
            else "Hermes instalará una versión compatible para sus herramientas web."
        ),
    )

    uv_ok, uv_version = _probe_command("uv", "--version")
    deps["uv"] = _managed_dependency(
        "uv", uv_ok, uv_version,
        "Hermes instala y usa su propio uv administrado cuando falta.",
    )

    if is_windows:
        powershell_ok, powershell_version = _probe_command("powershell", "-NoProfile", "-Command", "$PSVersionTable.PSVersion.ToString()")
        if not powershell_ok:
            powershell_ok, powershell_version = _probe_command("pwsh", "-NoProfile", "-Command", "$PSVersionTable.PSVersion.ToString()")
        deps["shell"] = {
            "name": "PowerShell",
            "status": "ok" if powershell_ok else "missing",
            "version": powershell_version,
            "required": True,
            "detail": "Se usa para ejecutar el instalador oficial de Windows.",
        }
    else:
        bash_ok, bash_version = _probe_command("bash", "--version")
        curl_ok, curl_version = _probe_command("curl", "--version")
        deps["shell"] = {
            "name": "bash",
            "status": "ok" if bash_ok else "missing",
            "version": bash_version,
            "required": True,
            "detail": "Se usa para ejecutar el instalador oficial.",
        }
        deps["curl"] = {
            "name": "curl",
            "status": "ok" if curl_ok else "missing",
            "version": curl_version,
            "required": True,
            "detail": "Descarga el instalador oficial por HTTPS.",
        }
        if is_linux:
            xz_ok, xz_version = _probe_command("xz", "--version")
            deps["xz"] = {
                "name": "xz-utils",
                "status": "ok" if xz_ok else "missing" if not node_compatible else "optional",
                "version": xz_version,
                "required": not node_compatible,
                "detail": (
                    "Hermes lo necesita para provisionar Node.js en Linux."
                    if not node_compatible
                    else "No es necesario porque ya existe un Node.js compatible."
                ),
            }

    install_state = _hermes_installation_state()
    hermes_path = str(install_state.get("cli_path") or "")
    hermes_status = str(install_state["status"])
    compatibility_status = str((install_state.get("compatibility") or {}).get("status") or "")
    deps["hermes"] = {
        "name": "Hermes Agent",
        "status": (
            "outdated" if hermes_status == "installed" and compatibility_status == "update_recommended"
            else "ok" if hermes_status == "installed"
            else "setup" if hermes_status == "needs_setup"
            else "broken" if hermes_status == "damaged"
            else "pending"
        ),
        "path": hermes_path,
        "version": install_state.get("version") or "",
        "required": False,
        "detail": (
            f"{install_state['message']} {(install_state.get('compatibility') or {}).get('label', '')}"
        ).strip(),
    }

    support = platform_support()
    blocking = [key for key, dep in deps.items() if dep.get("required") and dep.get("status") != "ok"]
    if support.get("status") == "unsupported":
        blocking.append("platform")
    ready = not blocking
    if ready and hermes_status == "damaged":
        hint = "Entorno listo para reparar Hermes con el instalador oficial."
    elif ready and hermes_status == "needs_setup":
        hint = "Hermes responde; falta completar proveedor y modelo, no reinstalarlo."
    elif ready and compatibility_status == "update_recommended":
        hint = "Hermes funciona, pero IRIS recomienda actualizarlo antes de usar APIs avanzadas."
    elif ready and hermes_status == "installed":
        hint = "Hermes ya está listo; el wizard no ejecutará el instalador."
    elif ready:
        hint = "Entorno listo. Hermes administrará Python, Node.js y uv si faltan."
    elif "platform" in blocking:
        hint = "Esta plataforma no está incluida en la matriz compatible de Hermes."
    else:
        names = ", ".join(str(deps[key]["name"]) for key in blocking if key in deps)
        hint = f"Instala o corrige: {names}."

    return {
        "deps": deps,
        "all_required_ok": ready,
        "blocking_dependencies": blocking,
        "platform": system,
        "platform_support": support,
        "status": "ready" if ready else "blocked",
        "install_action": (
            "repair" if hermes_status == "damaged"
            else "configure" if hermes_status == "needs_setup"
            else "update" if hermes_status == "installed" and compatibility_status == "update_recommended"
            else "verify" if hermes_status == "installed" and compatibility_status == "runtime_validation"
            else "none" if hermes_status == "installed"
            else "install"
        ),
        "hint": hint,
    }


@app.get("/api/detect-dependencies")
async def detect_dependencies() -> JSONResponse:
    """Return the real official-installer readiness, without false blockers."""
    return JSONResponse(_hermes_install_readiness())


def _installer_phase(line: str) -> str:
    text = line.lower()
    if any(token in text for token in ("clon", "download", "descarg", "fetch", "receiving")):
        return "fetch"
    if any(token in text for token in ("installing uv", "managed uv", "python", "node", "dependenc", "prerequis")):
        return "provision"
    if any(token in text for token in ("venv", "virtual environment", "environment")):
        return "environment"
    if any(token in text for token in ("postinstall", "playwright", "chromium", "browser")):
        return "postinstall"
    if any(token in text for token in ("success", "complete", "ready", "installed")):
        return "finalize"
    return ""


def _validate_installer_payload(system: str, script: bytes) -> dict[str, Any]:
    """Validate a downloaded script before it can reach a command interpreter."""
    if len(script) > _INSTALLER_MAX_BYTES:
        raise RuntimeError("El script oficial superó el tamaño máximo permitido.")
    decoded = script.decode("utf-8", errors="replace")
    expected_entry = "param(" if system == "Windows" else "#!/bin/bash"
    if (
        len(script) < 1024
        or "hermes agent installer" not in decoded.lower()
        or expected_entry not in decoded.lower()
    ):
        raise RuntimeError("La respuesta no parece ser el instalador oficial de Hermes; no se ejecutó nada.")
    return {
        "bytes": len(script),
        "sha256": hashlib.sha256(script).hexdigest(),
        "platform": system,
    }


async def _download_official_installer() -> tuple[Path, dict[str, Any]]:
    """Fetch a bounded installer file from the fixed official Hermes origin."""
    runtime_policy.require_runtime_capability("external_hermes_install")
    system = platform.system()
    url = _HERMES_INSTALLER_URLS["Windows"] if system == "Windows" else _HERMES_INSTALLER_URLS["default"]
    allowed_hosts = {"hermes-agent.nousresearch.com", "raw.githubusercontent.com"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(35.0, connect=12.0), follow_redirects=True) as client:
            async with client.stream("GET", url, headers={"Accept": "text/plain, text/x-shellscript, */*"}) as response:
                response.raise_for_status()
                redirect_chain = [item.url for item in response.history] + [response.url]
                if any(
                    item.scheme != "https" or (item.host or "").lower() not in allowed_hosts
                    for item in redirect_chain
                ):
                    raise RuntimeError("La descarga fue redirigida a un origen no permitido.")
                try:
                    declared_size = int(response.headers.get("content-length", "0") or 0)
                except ValueError as exc:
                    raise RuntimeError("El origen devolvió un tamaño de instalador inválido.") from exc
                if declared_size > _INSTALLER_MAX_BYTES:
                    raise RuntimeError("El script oficial superó el tamaño máximo permitido.")
                payload = bytearray()
                async for chunk in response.aiter_bytes():
                    payload.extend(chunk)
                    if len(payload) > _INSTALLER_MAX_BYTES:
                        raise RuntimeError("El script oficial superó el tamaño máximo permitido.")
    except httpx.HTTPError as exc:
        detail = str(exc).strip() or type(exc).__name__
        raise RuntimeError(f"No se pudo descargar el instalador oficial: {detail}") from exc

    script = bytes(payload)
    metadata = _validate_installer_payload(system, script)
    metadata["source_url"] = url
    metadata["resolved_host"] = (response.url.host or "").lower()

    suffix = ".ps1" if system == "Windows" else ".sh"
    with tempfile.NamedTemporaryFile(prefix="iris-hermes-", suffix=suffix, delete=False) as handle:
        handle.write(script)
        return Path(handle.name), metadata


def _installer_environment() -> dict[str, str]:
    inherited_venv = {
        "VIRTUAL_ENV", "__PYVENV_LAUNCHER__", "PYTHONHOME", "PYTHONPATH",
        "PYTHONSTARTUP", "PYTHONNOUSERSITE", "UV_PROJECT_ENVIRONMENT",
    }
    env = {key: value for key, value in os.environ.items() if key not in inherited_venv}
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _installer_process_group_options() -> dict[str, Any]:
    """Create a group so cancellation can stop the complete installer tree."""
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


async def _terminate_installer_process(proc: asyncio.subprocess.Process) -> None:
    """Best-effort termination of only the process tree spawned by IRIS."""
    if proc.returncode is not None:
        return
    if os.name == "nt":
        taskkill = shutil.which("taskkill")
        if taskkill:
            killer = await asyncio.create_subprocess_exec(
                taskkill,
                "/PID",
                str(proc.pid),
                "/T",
                "/F",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(killer.wait(), timeout=8)
    else:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGTERM)
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(proc.pid, signal.SIGKILL)
    if proc.returncode is None:
        with contextlib.suppress(ProcessLookupError, OSError):
            proc.kill()
    with contextlib.suppress(asyncio.TimeoutError, ProcessLookupError):
        await asyncio.wait_for(proc.wait(), timeout=5)


def _verify_hermes_install() -> dict[str, Any]:
    """Verify the installed launcher from the long-lived wizard process."""
    from core.hermes_compatibility import (
        IRIS_MIN_HERMES_VERSION,
        IRIS_MIN_HERMES_VERSION_TEXT,
        parse_hermes_version,
    )

    cli = resolve_hermes_cli()
    if not cli:
        return {"ok": False, "message": "El instalador terminó, pero IRIS no encontró el lanzador de Hermes."}
    result = run_hermes_cli(["--version"], timeout=12)
    if not result.get("success"):
        detail = str(result.get("error") or "no respondió a --version")[:360]
        return {"ok": False, "message": f"Hermes se instaló pero no pasó la verificación: {detail}", "cli_path": cli}
    output = str(result.get("output") or "")
    parsed = parse_hermes_version(output)
    if parsed is None:
        return {
            "ok": False,
            "message": "Hermes respondió, pero IRIS no pudo reconocer su versión instalada.",
            "cli_path": cli,
        }
    if parsed < IRIS_MIN_HERMES_VERSION:
        installed = ".".join(str(part) for part in parsed)
        return {
            "ok": False,
            "message": (
                f"El instalador dejó Hermes {installed}, pero IRIS requiere "
                f"{IRIS_MIN_HERMES_VERSION_TEXT} o posterior."
            ),
            "cli_path": cli,
        }
    runtime_ok, runtime_diagnostic = _hermes_runtime_health()
    if not runtime_ok:
        return {
            "ok": False,
            "message": f"Hermes respondió, pero su runtime sigue incompleto: {runtime_diagnostic}",
            "cli_path": cli,
        }
    version = output.splitlines()
    return {
        "ok": True,
        "message": "Lanzador verificado por IRIS.",
        "cli_path": cli,
        "version": version[0] if version else "Detectado",
    }


@app.post("/api/install-hermes-stream")
async def install_hermes_stream(request: Request) -> StreamingResponse:
    """Run, monitor and verify the official Hermes installer as one operation."""
    runtime_policy.require_runtime_capability("external_hermes_install")
    _enforce_local_install_request(request)

    async def event_stream():
        runtime_policy.require_runtime_capability("external_hermes_install")
        if _HERMES_INSTALL_LOCK.locked():
            yield f"data: {json.dumps({'type': 'error', 'message': 'Ya hay una instalación de Hermes en curso. Espera a que termine.'})}\n\n"
            return
        await _HERMES_INSTALL_LOCK.acquire()
        script_path: Path | None = None
        artifact: dict[str, Any] = {}
        proc: asyncio.subprocess.Process | None = None
        try:
            readiness = _hermes_install_readiness()
            if not readiness["all_required_ok"]:
                yield f"data: {json.dumps({'type': 'error', 'message': readiness['hint']})}\n\n"
                return

            yield f"data: {json.dumps({'type': 'phase', 'phase': 'fetch', 'text': 'Descargando el instalador oficial de Hermes...'})}\n\n"
            script_path, artifact = await _download_official_installer()
            yield f"data: {json.dumps({'type': 'artifact', 'artifact': artifact})}\n\n"
            if platform.system() == "Windows":
                host = shutil.which("powershell") or shutil.which("pwsh")
                if not host:
                    yield f"data: {json.dumps({'type': 'error', 'message': 'PowerShell no está disponible para ejecutar el instalador oficial.'})}\n\n"
                    return
                command = [host, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "ByPass", "-File", str(script_path), "-SkipSetup", "-NonInteractive"]
            else:
                command = ["bash", str(script_path), "--skip-setup", "--non-interactive"]

            yield f"data: {json.dumps({'type': 'phase', 'phase': 'provision', 'text': 'Ejecutando instalador oficial en modo no interactivo...'})}\n\n"
            runtime_policy.require_runtime_capability("external_hermes_install")
            proc = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                stdin=asyncio.subprocess.DEVNULL,
                env=_installer_environment(),
                **_installer_process_group_options(),
            )
            assert proc.stdout is not None
            while True:
                try:
                    raw_line = await asyncio.wait_for(proc.stdout.readline(), timeout=12.0)
                except asyncio.TimeoutError:
                    if proc.returncode is not None:
                        break
                    yield f"data: {json.dumps({'type': 'waiting', 'text': 'El instalador sigue trabajando; puede tardar varios minutos en la primera ejecución.'})}\n\n"
                    continue
                if not raw_line:
                    break
                clean = re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]", "", raw_line.decode("utf-8", errors="replace").rstrip()).replace("\r", "")
                if clean:
                    yield f"data: {json.dumps({'type': 'log', 'text': clean, 'phase': _installer_phase(clean)})}\n\n"

            await proc.wait()
            if proc.returncode != 0:
                yield f"data: {json.dumps({'type': 'done', 'ok': False, 'returncode': proc.returncode, 'artifact': artifact})}\n\n"
                return

            yield f"data: {json.dumps({'type': 'phase', 'phase': 'verify', 'text': 'Verificando el lanzador instalado...'})}\n\n"
            verification = await asyncio.to_thread(_verify_hermes_install)
            if verification.get("ok"):
                try:
                    from wizard.provider_sync import invalidate_cache
                    invalidate_cache()
                except Exception:
                    pass
            yield f"data: {json.dumps({'type': 'done', 'ok': bool(verification.get('ok')), 'returncode': proc.returncode, 'verification': verification, 'artifact': artifact})}\n\n"
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            yield f"data: {json.dumps({'type': 'error', 'message': f'Instalación detenida de forma segura: {exc}'})}\n\n"
        finally:
            if proc and proc.returncode is None:
                await _terminate_installer_process(proc)
            if script_path:
                with contextlib.suppress(OSError):
                    script_path.unlink(missing_ok=True)
            _HERMES_INSTALL_LOCK.release()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Hermes install + config endpoints live above ───────────────────
#
# The wizard installs Hermes via /api/install-hermes-stream, detects
# preconditions via /api/detect-dependencies, and lets Hermes auto-
# create its own config.yaml — no separate config-detect step needed.


async def sync_hermes_config_from_iris() -> dict[str, Any]:
    """
    Reflect IRIS' saved LLM selection into ``~/.hermes/.env`` and
    ``~/.hermes/config.yaml``.

    Pulls the current ``llm_provider``, ``llm_model`` and
    ``llm_api_key`` from IRIS' config and hands them off to
    ``wizard.hermes_config.write_hermes_config``. Safe to call when
    no provider is selected yet — returns a no-op result with a
    "skipped" detail string.
    """
    from wizard.hermes_config import write_hermes_config
    from wizard.providers import get_provider

    cfg = IRISConfig.load()
    provider = (cfg.llm_provider or "").strip()
    model = (cfg.llm_model or "").strip()
    api_key = (cfg.llm_api_key or "").strip()

    if not provider:
        return {"ok": False, "method": "none", "detail": "No LLM provider selected yet."}

    base_url_default = (get_provider(provider) or {}).get("base_url", "")
    summary = await asyncio.to_thread(
        write_hermes_config,
        llm_provider=provider,
        llm_api_key=api_key,
        llm_model=model,
        llm_use_oauth=bool(cfg.llm_use_oauth),
        api_server_key=(cfg.hermes_api_key or "").strip(),
        base_url=base_url_default,
    )
    return {
        "ok":     True,
        "method": "hermes_config",
        "detail": f"env: {summary.get('.env')}, config.yaml: {summary.get('config.yaml')}",
        "model":  summary.get("model", ""),
    }


def _validated_provider_base_url(value: str) -> str:
    """Validate a provider endpoint before persisting it into Hermes."""
    endpoint = (value or "").strip().rstrip("/")
    if not endpoint:
        return ""
    if len(endpoint) > 2048:
        raise ValueError("La URL del endpoint es demasiado larga.")
    parsed = urlparse(endpoint)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise ValueError("Usa una URL http(s) válida, sin credenciales ni fragmentos.")
    return endpoint


async def _external_provider_preflight(provider: str) -> dict[str, Any] | None:
    """Verify executables required by Hermes external-process providers."""
    from wizard.providers import effective_auth_mode_for, get_provider
    from wizard.hermes_config import read_hermes_dotenv_if_present

    if effective_auth_mode_for(provider) != "external":
        return None
    provider_info = get_provider(provider) or {}
    label = str(provider_info.get("name") or provider)
    if provider != "copilot-acp":
        return None
    from wizard.hermes_config import copilot_acp_command_problem, copilot_acp_launch

    env = await asyncio.to_thread(read_hermes_dotenv_if_present)
    try:
        command, _args = copilot_acp_launch(env)
    except ValueError as exc:
        return {
            "status": "error",
            "code": "external_command_invalid",
            "message": f"HERMES_COPILOT_ACP_ARGS de {label} no es válido.",
            "diagnostic": str(exc),
        }
    # Same argv[0] the pinned Hermes passes to Popen; the bundled engine has a
    # sealed PATH (A08.4), so only an absolute path reaches it there.
    packaged = runtime_policy.get_runtime().packaged
    if copilot_acp_command_problem(command, packaged=packaged):
        where = (
            "la ruta completa del ejecutable: el motor incluido no busca programas en el PATH"
            if packaged else
            "la CLI de GitHub Copilot en el PATH o la ruta completa de su ejecutable"
        )
        return {
            "status": "error",
            "code": "external_cli_missing",
            "message": (
                f"{label} necesita {where}. Indícala en HERMES_COPILOT_ACP_COMMAND sin comillas ni "
                "argumentos; los argumentos van en HERMES_COPILOT_ACP_ARGS."
            ),
        }
    return None


async def _local_provider_preflight(
    provider: str,
    model: str,
    base_url: str = "",
) -> dict[str, Any] | None:
    """Return a user-facing error when a selected local runtime is not ready."""
    from wizard.providers import get_provider

    provider_info = get_provider(provider) or {}
    if provider_info.get("category") != "local":
        return None

    label = str(provider_info.get("name") or provider)
    if provider == "ollama-local":
        url = "http://127.0.0.1:11434/api/tags"
        model_key = "name"
        payload_key = "models"
    elif provider == "lmstudio":
        url = "http://127.0.0.1:1234/v1/models"
        model_key = "id"
        payload_key = "data"
    else:
        endpoint = (base_url or str(provider_info.get("base_url") or "")).rstrip("/")
        if not endpoint:
            return {
                "status": "error",
                "code": "local_endpoint_missing",
                "message": (
                    f"IRIS no tiene un endpoint configurable para '{label}'. "
                    "Configúralo directamente en Hermes antes de iniciar el Gateway."
                ),
            }
        url = f"{endpoint}/models"
        model_key = "id"
        payload_key = "data"

    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        return {
            "status": "error",
            "code": "local_server_unavailable",
            "message": (
                f"No se detecta el servidor de {label}. IRIS no instala ni inicia esta aplicación. "
                "Ábrela, activa su servidor local y reintenta."
            ),
            "diagnostic": str(exc)[:300],
        }

    rows = payload.get(payload_key, []) if isinstance(payload, dict) else []
    models = [
        str(item.get(model_key) or item.get("model") or "").strip()
        for item in rows
        if isinstance(item, dict)
    ]
    models = [item for item in models if item]
    if not models:
        return {
            "status": "error",
            "code": "local_model_missing",
            "message": (
                f"{label} está activo, pero no expone ningún modelo. "
                "Carga o descarga uno en esa aplicación y reintenta."
            ),
        }
    if model and model != "auto" and model not in models:
        return {
            "status": "error",
            "code": "local_model_not_found",
            "message": f"El modelo '{model}' no está disponible en {label}.",
            "available_models": models[:20],
        }
    return None


def _engine_consent_refusal() -> JSONResponse | None:
    """Backend side of the A08.6 notice: no start or restart without it."""
    from core.config import ENGINE_CONSENT_MESSAGE, engine_consent_missing

    if not engine_consent_missing():
        return None
    return JSONResponse({"status": "error", "code": "security_ack_required",
                         "message": ENGINE_CONSENT_MESSAGE}, status_code=409)


@app.post("/api/start-gateway")
async def start_gateway() -> JSONResponse:
    runtime_policy.get_runtime()
    refusal = await asyncio.to_thread(_engine_consent_refusal)
    if refusal is not None:
        return refusal
    from core.service_start_lock import async_service_start_lock

    async with async_service_start_lock("hermes"):
        return await _start_gateway_impl()


def _rollback_spawned_gateway(process: subprocess.Popen) -> bool:
    """Clean up only the child handle returned by this failed launch."""
    try:
        if process.poll() is not None:
            return True
        process.terminate()
        try:
            process.wait(timeout=4)
        except subprocess.TimeoutExpired:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=4)
        return True
    except (OSError, subprocess.TimeoutExpired):
        return False


async def _llm_credentials(cfg: IRISConfig, existing_env: dict[str, str]) -> dict[str, Any]:
    """The Gateway pre-flight on the chosen provider: its API key or its OAuth login.

    ``error`` is exactly what ``start_gateway`` answers when they are missing (core/desktop.py
    routes those codes to the wizard); the final summary asks the same question, so the
    wizard never calls IRIS ready while the launch would refuse it.
    """
    from wizard.hermes_config import azure_foundry_uses_entra, has_hermes_auth_profile
    from wizard.providers import effective_auth_mode_for, env_key_for, get_provider, hermes_id_for

    provider = (cfg.llm_provider or "").strip()
    api_key  = (cfg.llm_api_key or "").strip()
    env_key  = env_key_for(provider) if provider else ""
    auth_mode = effective_auth_mode_for(provider) if provider else "api"
    use_oauth = auth_mode == "oauth" or bool(cfg.llm_use_oauth)
    uses_entra = bool(
        provider == "azure-foundry"
        and await asyncio.to_thread(azure_foundry_uses_entra)
    )

    # ── Pre-flight: validate credentials ──────────────────────────────
    has_key   = bool(api_key) or (
        bool(env_key)
        and bool(existing_env.get(env_key) or os.environ.get(env_key))
    )
    has_oauth = False
    if provider:
        for candidate in {hermes_id_for(provider, use_oauth=True), hermes_id_for(provider)}:
            if candidate and has_hermes_auth_profile(candidate, auth_type="oauth"):
                has_oauth = True
                break

    provider_label = (get_provider(provider) or {}).get("name") or provider or "(ninguno)"

    error = None
    if auth_mode in {"api", "api+oauth"} and not use_oauth and not has_key and not uses_entra:
        error = {
            "status": "error",
            "code": "missing_api_key",
            "message": (
                f"El proveedor '{provider_label}' necesita una API key"
                + (f" ({env_key})" if env_key else "")
                + " y no encuentro ninguna configurada. "
                  "Pégala en el paso de proveedor antes de iniciar el Gateway."
            ),
            "hint_env": env_key,
        }

    elif use_oauth and not has_oauth:
        error = {
            "status": "error",
            "code": "missing_oauth",
            "message": (
                f"El proveedor '{provider_label}' usa OAuth y todavía no has iniciado sesión. "
                "Vuelve al paso de proveedor y haz clic en \"🔑 Autenticar con OAuth\"."
            ),
        }

    return {"provider": provider, "api_key": api_key, "env_key": env_key, "auth_mode": auth_mode,
            "use_oauth": use_oauth, "uses_entra": uses_entra, "has_key": has_key, "has_oauth": has_oauth,
            "provider_label": provider_label, "error": error}


async def _provider_preflight(cfg: IRISConfig, existing_env: dict[str, str]) -> dict[str, Any]:
    """Everything the Gateway checks on the chosen provider before starting it, in its
    order: the key or OAuth login, an external provider's program, a local runtime's server
    and model. Nothing is started: files are looked up and local servers asked over HTTP.
    ``error`` is the Gateway's answer, and the final summary asks exactly the same."""
    from wizard.hermes_config import read_provider_base_url

    credentials = await _llm_credentials(cfg, existing_env)
    if not credentials["error"]:
        provider = credentials["provider"]
        credentials["error"] = await _external_provider_preflight(provider) or await _local_provider_preflight(
            provider,
            (cfg.llm_model or "").strip(),
            await asyncio.to_thread(read_provider_base_url, provider),
        )
    return credentials


async def _start_gateway_impl() -> JSONResponse:
    """Start Hermes' gateway in the background.

    Before launching, we sync ``~/.hermes/.env`` and
    ``~/.hermes/config.yaml`` with the provider the user picked in
    the wizard. The api-server adapter is enabled via
    ``API_SERVER_ENABLED=true`` in .env so the OpenAI-compatible
    endpoint at :8642 comes up alongside any messaging platforms.

    Liveness check: polls the api-server port for up to 90 s. If the
    process dies, returns the tail of its log so the wizard UI can
    show the REAL reason instead of a fake "started" checkmark.
    """
    from wizard.hermes_config import read_hermes_dotenv

    runtime = runtime_policy.get_runtime()
    try:
        # Read what's currently in ~/.hermes/.env so we only write if needed.
        existing_env = await asyncio.to_thread(read_hermes_dotenv)
        cfg = IRISConfig.load()
        # Idempotency and port ownership come before spawning. Besides avoiding
        # a second Hermes process on a double-click, the HTTP health probe keeps
        # an unrelated process on :8642 from being mistaken for Hermes.
        from wizard.health_check import check_gateway_health, check_gateway_port

        gateway_url = (cfg.hermes_url or "").strip() or "http://127.0.0.1:8642"
        if runtime.packaged:
            from core.request_security import is_loopback_url
            if not is_loopback_url(gateway_url):
                return JSONResponse({"status": "error", "code": "gateway_runtime_conflict",
                                     "message": "El runtime incluido requiere un Gateway local acreditado."}, status_code=409)
        port_state = await check_gateway_port(gateway_url)
        if port_state.get("ok"):
            health_state = await check_gateway_health(gateway_url)
            if health_state.get("ok"):
                return JSONResponse({
                    "status": "running",
                    "message": "Hermes Gateway ya estaba activo y respondió correctamente.",
                    "already_running": True,
                })
            return JSONResponse({
                "status": "error",
                "code": "gateway_port_conflict",
                "message": (
                    "El puerto configurado está ocupado, pero /health no identifica un Hermes Gateway saludable. "
                    "Cierra el proceso que usa ese puerto o cambia API_SERVER_PORT."
                ),
            }, status_code=409)
        if runtime.packaged:
            from core.hermes_service_registry import gateway_registry_state
            from core.workspace_sync import get_hermes_home

            ownership = await asyncio.to_thread(gateway_registry_state, get_hermes_home())
            if ownership["state"] == "unverified":
                return JSONResponse({"status": "error", "code": "gateway_runtime_conflict",
                                     "message": "El perfil contiene un Gateway sin identidad acreditada. No se inició ni detuvo otro proceso."}, status_code=409)
            if ownership["verified"]:
                return JSONResponse({"status": "pending", "code": "gateway_runtime_pending",
                                     "message": "El Gateway de esta instalación sigue iniciándose. Espera y vuelve a comprobar su API."}, status_code=202)
        credentials = await _provider_preflight(cfg, existing_env)
        if credentials["error"]:
            return JSONResponse(credentials["error"])
        provider, api_key, env_key = credentials["provider"], credentials["api_key"], credentials["env_key"]
        auth_mode, use_oauth, uses_entra = credentials["auth_mode"], credentials["use_oauth"], credentials["uses_entra"]
        has_key, has_oauth = credentials["has_key"], credentials["has_oauth"]
        provider_label = credentials["provider_label"]

        # ── Sync only if the LLM selection changed ─────────────────────────
        # If Hermes already has the SAME provider + model + key (or has
        # an OAuth profile to cover the missing key), skip the write so
        # we don't churn config.yaml on every gateway boot.
        #
        # The previous version only checked ``LLM_PROVIDER`` /
        # ``LLM_API_KEY`` — switching ONLY the model (anthropic claude-3.5
        # → anthropic claude-4.6) would pass that check and never re-write
        # config.yaml, leaving Hermes pointing at the old model.
        model = (cfg.llm_model or "").strip()
        env_key_name = env_key  # alias for readability
        env_key_value = existing_env.get(env_key_name, "") if env_key_name else ""
        creds_match = (
            auth_mode in {"none", "api-optional", "aws", "external"}
            or (bool(env_key_value) and env_key_value == api_key)
            or (not api_key and has_oauth)
            or (bool(api_key) and env_key_value == api_key)
        )
        already_synced = bool(
            provider
            and existing_env.get("LLM_PROVIDER", "") == provider
            and existing_env.get("LLM_MODEL", "") == model
            and creds_match
        )
        sync_result = {"detail": "La configuración de Hermes ya estaba sincronizada."}
        if not already_synced:
            sync_result = await sync_hermes_config_from_iris()
            print(f"[Hermes sync] {sync_result.get('detail', '')}")
        else:
            print("[Hermes sync] already up-to-date — skipping write")

        # 2. Build env for the gateway subprocess. Hermes reads the
        # provider key from its own .env (we synced it above), so we
        # only need to pass through the standard env + force UTF-8
        # for Windows console output. ``_hermes_child_env`` strips
        # parent-venv pointers so Hermes' launcher uses its own venv
        # (otherwise we hit ``ModuleNotFoundError: hermes_cli``).
        gateway_env = _hermes_child_env(
            # If the user pasted a key just now and didn't restart
            # the wizard, also export it on the subprocess env so
            # Hermes sees it even if .env caching gets in the way.
            ({env_key: api_key} if env_key and api_key else None)
        )

        # 3. Rotate the log so `hermes-gateway.log` only holds THIS run's
        #    output — users debugging a crash want the current boot, not
        #    weeks of accumulated noise.
        log_path = get_iris_home() / "logs" / "hermes-gateway.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            if log_path.exists() and log_path.stat().st_size > 0:
                bak = log_path.with_suffix(".log.prev")
                try:
                    if bak.exists(): bak.unlink()
                except OSError: pass
                try: log_path.rename(bak)
                except OSError: log_path.unlink(missing_ok=True)  # type: ignore[arg-type]
        except OSError:
            pass  # best-effort; don't block launch on log rotation issues
        log_fh = open(log_path, "ab", buffering=0)

        # Hermes is invoked as a CLI subcommand. ``hermes gateway run``
        # is the foreground form; ``hermes gateway`` alias also works.
        # We use `run` to be explicit — the `start` form is for the
        # systemd/launchd service path which we don't use here.
        # On Windows we bypass the broken setuptools launcher so the
        # gateway can find ``hermes_cli`` even when the .exe stub
        # skips site-packages init (see _resolve_hermes_invocation).
        if runtime.hermes:
            base_argv = hermes_command_prefix()
            if not base_argv:
                raise runtime_policy.RuntimeDescriptorError("El runtime declarado de Hermes no está disponible.")
            popen_cwd = str(runtime.hermes.root)
        elif os.name == "nt":
            hermes_argv, _ = _resolve_hermes_invocation()
            base_argv = hermes_argv if hermes_argv else [resolve_cmd("hermes")]
        else:
            base_argv = [resolve_cmd("hermes")]
        cmd = base_argv + ["gateway", "run"]
        popen_kwargs: dict[str, Any] = {
            "stdout": log_fh,
            "stderr": log_fh,
            "stdin": subprocess.DEVNULL,
            "env": gateway_env,
            "close_fds": True,
        }
        if runtime.hermes:
            popen_kwargs["cwd"] = popen_cwd
        if os.name == "nt":
            # CREATE_NEW_PROCESS_GROUP: Ctrl-C in the wizard terminal won't
            # propagate to the gateway. CREATE_NO_WINDOW keeps it headless.
            #
            # We deliberately do NOT pass DETACHED_PROCESS — combining it with
            # redirected stdout/stderr makes Windows drop the early write
            # buffer when the child dies instantly (we'd see a 0-byte log
            # for a crashed gateway, which is exactly what was hiding
            # "Gateway start blocked: existing config is missing
            # gateway.mode"). CREATE_NO_WINDOW alone is enough to keep the
            # gateway headless while still flushing stderr to our handle.
            new_group = subprocess.CREATE_NEW_PROCESS_GROUP
            no_window = 0x08000000  # CREATE_NO_WINDOW
            popen_kwargs["creationflags"] = new_group | no_window
        else:
            # On POSIX, start_new_session detaches from the parent so SIGHUP
            # on wizard exit doesn't reach the gateway.
            popen_kwargs["start_new_session"] = True

        try:
            proc = subprocess.Popen(cmd, **popen_kwargs)
        finally:
            log_fh.close()

        if runtime.packaged:
            from core.hermes_service_registry import record_spawned_gateway
            from core.workspace_sync import get_hermes_home
            try:
                await asyncio.to_thread(record_spawned_gateway, get_hermes_home(), proc.pid)
            except runtime_policy.RuntimeDescriptorError:
                await asyncio.to_thread(_rollback_spawned_gateway, proc)
                raise
            except asyncio.CancelledError:
                await asyncio.shield(asyncio.to_thread(_rollback_spawned_gateway, proc))
                raise
            except Exception:
                rolled_back = await asyncio.to_thread(_rollback_spawned_gateway, proc)
                return JSONResponse({"status": "error", "code": "gateway_runtime_unverified",
                                     "message": (
                                         "No se pudo acreditar el Gateway recién lanzado; se canceló ese arranque."
                                         if rolled_back else
                                         "No se pudo acreditar ni detener el Gateway recién lanzado. Ese proceso no se reutilizará."
                                     )}, status_code=503)

        # 4. Liveness check — poll process + port. First boot of
        #    Hermes is fast on warm cache (~5-10s) but the first ever
        #    launch may pull/compile native deps and/or download a
        #    model adapter, so we allow 90s.
        import socket, time
        deadline = time.time() + 90.0
        listening = False
        # Hermes' api-server defaults to 8642. Configurable via
        # API_SERVER_PORT in .env — read it here so a custom port
        # doesn't make us wait on the wrong socket.
        try:
            from wizard.hermes_config import _read_env_file
            _h_env = _read_env_file()
            api_port = int(_h_env.get("API_SERVER_PORT") or 8642)
        except Exception:
            api_port = 8642

        while time.time() < deadline:
            # If the process already exited, bail with log tail.
            rc = proc.poll()
            if rc is not None:
                await asyncio.sleep(0.15)
                tail = _read_log_tail(log_path, max_bytes=4096)
                return JSONResponse({
                    "status": "error",
                    "code": "gateway_crashed",
                    "message": (
                        f"El gateway arrancó pero terminó con código {rc}. "
                        f"Revisa el log para el motivo."
                    ),
                    "returncode": rc,
                    "log_path": str(log_path),
                    "log_tail": tail,
                })
            # Still alive — check if api-server is listening yet.
            try:
                with socket.create_connection(("127.0.0.1", api_port), timeout=0.4):
                    pass
                health = await check_gateway_health(f"http://127.0.0.1:{api_port}")
                if health.get("ok"):
                    listening = True
                    break
            except OSError:
                pass
            await asyncio.sleep(0.4)

        if not listening:
            tail = _read_log_tail(log_path, max_bytes=4096)
            return JSONResponse({
                "status": "warning",
                "code": "gateway_not_listening",
                "message": (
                    f"Gateway lanzado pero el puerto {api_port} no respondió "
                    "tras 90 s. Verifica que API_SERVER_ENABLED=true en "
                    "~/.hermes/.env. Revisa el log y reintenta."
                ),
                "pid": proc.pid,
                "log_path": str(log_path),
                "log_tail": tail,
                "sync": sync_result,
            })

        return JSONResponse({
            "status": "success",
            "message": f"Hermes Gateway iniciado y escuchando en :{api_port}.",
            "pid": proc.pid,
            "log_path": str(log_path),
            "sync": sync_result,
        })
    except (runtime_policy.RuntimeDescriptorError, runtime_policy.RuntimePolicyError):
        raise
    except Exception as e:
        return JSONResponse({"status": "error", "message": f"No se pudo iniciar el Gateway: {str(e)}"})


def _is_hermes_gateway_command(*parts: str) -> bool:
    """Return True only for a command line that identifies Hermes gateway."""
    text = " ".join(str(part or "") for part in parts).lower().replace("\\", "/")
    has_hermes = "hermes" in text or "hermes_cli" in text
    has_gateway = "gateway" in text
    return has_hermes and has_gateway


def _native_process_command(pid: int) -> str:
    """Best-effort command lookup used when psutil is unavailable."""
    if pid <= 0:
        return ""
    if os.name == "nt":
        ok, output = _probe_command(
            "powershell", "-NoProfile", "-Command",
            f"(Get-CimInstance Win32_Process -Filter \"ProcessId={pid}\").CommandLine",
        )
        return output if ok else ""
    proc_cmdline = Path(f"/proc/{pid}/cmdline")
    if proc_cmdline.exists():
        try:
            return proc_cmdline.read_bytes().replace(b"\0", b" ").decode("utf-8", errors="replace")
        except OSError:
            return ""
    ok, output = _probe_command("ps", "-p", str(pid), "-o", "command=")
    return output if ok else ""


async def _stop_running_gateway() -> dict[str, Any]:
    """Kill the currently-running Hermes gateway (if any).

    Hermes writes its PID to ``<HERMES_HOME>/gateway.pid`` — we read
    that, send a graceful terminate, fall back to a hard kill after a
    short grace period, then wait for the configured API port to free so the
    next ``hermes gateway run`` doesn't bail with EADDRINUSE.

    Returns a dict with ``stopped`` (bool), ``pid`` (int|None), and a
    short ``detail`` string for the UI log.
    """
    from core.workspace_sync import get_hermes_home
    home = get_hermes_home()
    if runtime_policy.get_runtime().packaged:
        from core.hermes_service_registry import stop_owned_gateway
        from core.service_start_lock import async_service_start_lock
        async with async_service_start_lock("hermes"):
            return await asyncio.to_thread(stop_owned_gateway, home)
    pid_path = home / "gateway.pid"
    pid: int | None = None
    if pid_path.exists():
        try:
            payload = json.loads(pid_path.read_text(encoding="utf-8", errors="ignore"))
            pid = int(payload.get("pid")) if isinstance(payload, dict) else None
        except (ValueError, OSError):
            pid = None
    if not pid:
        return {"stopped": False, "pid": None, "detail": "No active gateway PID found."}

    # Kill the process tree. We use psutil if available for clean
    # child-process handling on Windows; otherwise fall back to the
    # platform's native API.
    killed = False
    try:
        import psutil  # type: ignore[import-not-found]
        try:
            parent = psutil.Process(pid)
        except psutil.NoSuchProcess:
            killed = True  # already gone
        else:
            try:
                identity = [parent.name(), parent.exe(), *parent.cmdline()]
            except psutil.NoSuchProcess:
                with contextlib.suppress(OSError):
                    pid_path.unlink()
                return {"stopped": True, "pid": pid, "detail": "El gateway ya no estaba activo."}
            except (psutil.AccessDenied, psutil.ZombieProcess) as exc:
                return {
                    "stopped": False,
                    "pid": pid,
                    "detail": f"No se pudo validar la identidad del PID {pid}: {exc}",
                }
            if not _is_hermes_gateway_command(*identity):
                with contextlib.suppress(OSError):
                    pid_path.unlink()
                return {
                    "stopped": False,
                    "pid": pid,
                    "detail": "El PID guardado pertenece a otro proceso; no se terminó por seguridad.",
                    "stale_pid": True,
                }
            children = parent.children(recursive=True)
            for child in children:
                with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied):
                    child.terminate()
            with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied):
                parent.terminate()
            gone, alive = psutil.wait_procs([parent, *children], timeout=4)
            for proc in alive:
                with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied):
                    proc.kill()
            killed = True
    except ImportError:
        command = await asyncio.to_thread(_native_process_command, pid)
        if not command:
            with contextlib.suppress(OSError):
                pid_path.unlink()
            return {
                "stopped": False,
                "pid": pid,
                "detail": "El PID ya no existe o no se pudo validar; no se terminó ningún proceso.",
                "stale_pid": True,
            }
        if not _is_hermes_gateway_command(command):
            with contextlib.suppress(OSError):
                pid_path.unlink()
            return {
                "stopped": False,
                "pid": pid,
                "detail": "El PID guardado pertenece a otro proceso; no se terminó por seguridad.",
                "stale_pid": True,
            }
        try:
            if os.name == "nt":
                # /T = include child processes; /F = force.
                result = subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    capture_output=True, timeout=8,
                )
                if result.returncode != 0:
                    detail = (result.stderr or result.stdout or b"").decode("utf-8", errors="replace").strip()
                    return {"stopped": False, "pid": pid, "detail": f"taskkill falló: {detail}"}
            else:
                os.kill(pid, 15)  # SIGTERM
                await asyncio.sleep(2)
                with contextlib.suppress(ProcessLookupError):
                    os.kill(pid, 9)  # SIGKILL
            killed = True
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"stopped": False, "pid": pid, "detail": f"kill failed: {exc}"}

    # Wait for the configured API port to free so the immediate restart isn't
    # rejected with "address already in use".
    try:
        from wizard.hermes_config import read_hermes_dotenv
        api_port = int((await asyncio.to_thread(read_hermes_dotenv)).get("API_SERVER_PORT") or 8642)
    except (TypeError, ValueError, OSError):
        api_port = 8642
    for _ in range(20):  # up to ~4 s
        try:
            with httpx.Client(timeout=0.4) as client:
                client.get(f"http://127.0.0.1:{api_port}/health")
            await asyncio.sleep(0.2)
        except httpx.HTTPError:
            break  # port no longer responsive → freed

    # Best-effort cleanup of the stale pid/lock files. Hermes itself
    # writes them on boot, so leftover stale files would only hurt
    # process-detection.
    for stale in ("gateway.pid", "gateway.lock"):
        with contextlib.suppress(OSError):
            (home / stale).unlink()

    return {"stopped": killed, "pid": pid, "detail": f"Gateway PID {pid} terminado."}


@app.post("/api/restart-gateway")
async def restart_gateway() -> JSONResponse:
    """Stop the running gateway (if any) and start it again.

    The provider/model/api-key already on disk wins — callers should
    POST ``/api/save-llm-selection`` first if they want a different
    config to take effect.
    """
    # Checked before stopping: a refused restart leaves the engine as it was.
    refusal = await asyncio.to_thread(_engine_consent_refusal)
    if refusal is not None:
        return refusal
    stop_info = await _stop_running_gateway()
    if stop_info.get("blocked"):
        return JSONResponse({"status": "error", "code": "gateway_runtime_conflict",
                             "message": stop_info["detail"], "stop": stop_info}, status_code=409)
    start_resp = await start_gateway()
    # ``start_gateway`` always returns a JSONResponse, but its ``.body``
    # is a bytes object that CAN be empty when something upstream
    # returned a response with no payload. ``bytes(None)`` raises
    # ``TypeError`` and ``bytes(<bytes>)`` is a no-op, so we just guard
    # the conversion + fall back to an empty dict so callers always
    # get a usable JSON document with the stop-info attached.
    raw = getattr(start_resp, "body", None) or b""
    if isinstance(raw, memoryview):
        raw = bytes(raw)
    try:
        body = json.loads(raw.decode("utf-8")) if raw else {}
    except (ValueError, UnicodeDecodeError) as exc:
        body = {
            "status": "error",
            "message": f"Gateway start returned non-JSON payload: {exc}",
        }
    if not isinstance(body, dict):
        body = {"status": "error", "message": "Gateway start returned non-object JSON."}
    body["stop"] = stop_info
    return JSONResponse(body)


def _read_log_tail(path: Path, max_bytes: int = 4096) -> str:
    """Return the last `max_bytes` of a log file as UTF-8 text (errors=replace).

    Used by /api/start-gateway when the gateway dies or fails to listen,
    so the wizard UI can show the user WHY instead of a fake success."""
    try:
        if not path.exists(): return ""
        with open(path, "rb") as fh:
            size = path.stat().st_size
            if size > max_bytes:
                fh.seek(-max_bytes, os.SEEK_END)
            data = fh.read()
        return data.decode("utf-8", errors="replace").strip()
    except OSError:
        return ""


# ────────────────────────────────────────────────────────
# Autostart — gateway survives reboots without the user
# ────────────────────────────────────────────────────────

class AutostartAction(BaseModel):
    action: Literal["enable", "disable"] = "enable"


@app.get("/api/autostart-status")
async def autostart_status_endpoint() -> JSONResponse:
    """Report whether the gateway is set to start at login."""
    from wizard.autostart import autostart_status
    return JSONResponse(await autostart_status())


@app.post("/api/autostart")
async def autostart_toggle(payload: AutostartAction) -> JSONResponse:
    """Enable or disable gateway autostart at user login.

    Windows  → drops a hidden-window `.vbs` into the Startup folder.
    Linux    → drops a `.desktop` entry into ``~/.config/autostart/``.
    macOS    → installs a LaunchAgent plist via ``launchctl load``.

    The body is `{"action": "enable"|"disable"}`.
    """
    runtime_policy.require_runtime_capability("external_autostart")
    from wizard.autostart import enable_autostart, disable_autostart
    if payload.action == "enable":
        return JSONResponse(await enable_autostart())
    return JSONResponse(await disable_autostart())


@app.get("/api/providers")
async def list_providers() -> JSONResponse:
    """Return the LLM provider catalog used by the wizard UI.

    The catalog blends two sources:

      1. `wizard/providers.py` — a curated, hand-written list. Gives
         us consistent icons, categories, tier tags, and sign-up URLs.
      2. The bundled / runtime catalog snapshot — the live list of
         providers + models Hermes knows about. The provider step refreshes
         a stale snapshot once per day; the button remains as an explicit
         retry, so new providers do not require an IRIS release.

    The merged payload also carries a `source` field (`"merged"` vs
    `"static"`) so the UI can tell the user whether the list is live
    or just the baked-in fallback (e.g. when Hermes isn't installed).
    """
    from wizard.providers import merged_providers_payload, providers_payload
    from wizard.provider_sync import snapshot_info

    # `/api/providers` is read-only — it never spawns the Hermes CLI;
    # that's the job of `POST /api/providers/refresh`. Under the hood
    # `merged_providers_payload()` serves from memory-cache → runtime
    # snapshot → bundled snapshot → static fallback, which means this
    # endpoint completes in < 50 ms regardless of what Hermes is
    # doing. We keep a defensive try/except so any downstream bug in
    # the merge path can't take the wizard offline.
    try:
        payload = await merged_providers_payload()
    except Exception as exc:  # noqa: BLE001 - never block the UI
        print(f"[providers] merge failed, serving static: {exc!r}", file=sys.stderr)
        payload = providers_payload()
        payload["source"] = "static"

    # Attach snapshot metadata so the UI can render the "Datos
    # actualizados hasta DD/MM/AAAA" badge and decide whether to
    # offer the "Actualizar catálogo" button. The metadata is
    # cheap to read (< 10 ms) and gets updated synchronously when
    # the user triggers a refresh via `/api/providers/refresh`.
    payload["snapshot"] = snapshot_info()
    return JSONResponse(
        payload,
        headers={"Cache-Control": "no-store, max-age=0"},
    )


# ── Refresh endpoint ────────────────────────────────────────────
#
# Automatic daily and explicit retry catalog refresh. The UI calls this SSE
# endpoint in the background when its snapshot is stale and from the
# "Actualizar catálogo" button. We stream progress so both paths remain
# observable and an aborted connection cancels the HTTP fetch cleanly.

@app.post("/api/providers/refresh")
async def refresh_providers_stream() -> StreamingResponse:
    """Re-fetch the Hermes catalog and overwrite the runtime
    snapshot. Streams progress via SSE.

    Event shapes (one JSON object per event, all on `data:`):

        {"type": "start",    "max_seconds": 45}
        {"type": "progress", "elapsed_s": 12, "phase": "fetching"}
        {"type": "done",     "snapshot": {...}, "providers_count": 24,
                              "models_count": 835}
        {"type": "error",    "code": "hermes_not_found" |
                                      "timeout" | "crashed" |
                                      "empty" | "unsupported" |
                                      "write_failed",
                             "message": "..."}

    The front listens for `done` / `error` as terminal events and
    closes the EventSource. Progress events fire every ~2 s while
    the subprocess is running so the UI's timer stays in sync.
    """
    from wizard.provider_sync import fetch_live_catalog

    async def event_stream():
        # Tell the UI up-front what the worst-case is, so it can
        # size the progress bar and set expectations. The preferred path
        # runs Hermes' official inventory in its managed interpreter; old
        # releases fall back to the public manifest.
        yield f"data: {json.dumps({'type': 'start', 'max_seconds': 45})}\n\n"

        # Run the fetch as a task so we can interleave heartbeat
        # events every 2 s. `fetch_live_catalog` enforces its own
        # inventory/HTTP deadlines; we rely on those plus our own
        # cancellation cleanup below.
        fetch_task = asyncio.create_task(fetch_live_catalog())
        elapsed = 0

        try:
            while not fetch_task.done():
                try:
                    # Poll with a 2 s timeout. Each tick becomes a
                    # heartbeat event so the UI's timer animates.
                    await asyncio.wait_for(
                        asyncio.shield(fetch_task), timeout=2.0,
                    )
                except asyncio.TimeoutError:
                    elapsed += 2
                    heartbeat = {
                        "type":      "progress",
                        "elapsed_s": elapsed,
                        "phase":     "fetching",
                    }
                    yield f"data: {json.dumps(heartbeat)}\n\n"

            # Task is done — either success or an exception.
            catalog, meta = fetch_task.result()
            providers_count = len(catalog)
            models_count = sum(len(v) for v in catalog.values())
            done = {
                "type":             "done",
                "snapshot":         meta,
                "providers_count":  providers_count,
                "models_count":     models_count,
            }
            yield f"data: {json.dumps(done)}\n\n"

        except asyncio.CancelledError:
            # Client disconnected mid-refresh. Cancel the in-flight
            # HTTP fetch so we don't leak a request that nobody will
            # read.
            fetch_task.cancel()
            with contextlib.suppress(Exception):
                await fetch_task
            raise  # re-raise so FastAPI closes the stream cleanly

        except FileNotFoundError as exc:
            # Reserved for future paths that may need a CLI on disk.
            # The current HTTP-based fetch never raises this.
            err = {"type": "error", "code": "hermes_not_found",
                   "message": str(exc)}
            yield f"data: {json.dumps(err)}\n\n"
        except TimeoutError as exc:
            err = {"type": "error", "code": "timeout",
                   "message": str(exc)}
            yield f"data: {json.dumps(err)}\n\n"
        except OSError as exc:
            err = {"type": "error", "code": "write_failed",
                   "message": str(exc)}
            yield f"data: {json.dumps(err)}\n\n"
        except RuntimeError as exc:
            # ``UnsupportedByEngine`` is a vestigial subclass kept for
            # the case a future engine swap removes the published
            # manifest path. Test it first because it's a subclass of
            # RuntimeError. Otherwise this covers HTTP failures,
            # malformed JSON, or an empty catalog from the manifest.
            from wizard.provider_sync import UnsupportedByEngine
            if isinstance(exc, UnsupportedByEngine):
                err = {"type": "error", "code": "unsupported",
                       "message": str(exc)}
            else:
                err = {"type": "error", "code": "crashed",
                       "message": str(exc)}
            yield f"data: {json.dumps(err)}\n\n"
        except Exception as exc:  # noqa: BLE001
            err = {"type": "error", "code": "crashed",
                   "message": f"Error inesperado: {exc}"}
            yield f"data: {json.dumps(err)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":    "no-cache",
            "X-Accel-Buffering": "no",
            "Connection":       "keep-alive",
        },
    )


@app.get("/api/auth-provider-stream")
async def auth_provider_stream(provider: str, request: Request, auth_type: str = "oauth"):
    """Old clients must reload instead of starting an interactive CLI."""
    _enforce_local_browser_action(request, "La autenticación OAuth")
    return JSONResponse(
        {"status": "error", "message": "Recarga el wizard para usar el acceso integrado."},
        status_code=410,
    )


@app.get("/api/hermes-status")
async def hermes_status() -> JSONResponse:
    """Return detailed Hermes installation status to drive the wizard flow.

    Returns one of four modes (`mode` field):
      - "full"   → Managed Hermes is not installed (status="missing"). This
                   also covers an unsupported global launcher left on PATH
                   after uninstall. Frontend hides Skip/Repair cards and
                   auto-selects "Modo Completo".
                   The same mode is used for a runnable install with no base
                   config (status="needs_setup"), without reinstalling it.
      - "skip"   → Hermes installed and api-server responding
                   (status="connected"). Frontend offers to skip to
                   Voice/Avatar/Wake config.
      - "quick"  → Hermes installed but api-server not responding
                   (status="installed"). Frontend renames the Skip card
                   to "Activar Gateway" and auto-runs startGateway().
                   "Repair" is offered alongside as a manual fallback.
      - "repair" → a launcher exists but cannot execute (status="damaged").
                   The frontend offers an explicit reinstall; it never tries
                   to restart a known-broken launcher.
    """
    runtime = runtime_policy.get_runtime()
    install_state = await asyncio.to_thread(_hermes_installation_state)
    if install_state["status"] in {"missing", "damaged", "needs_setup"}:
        return JSONResponse({
            **install_state,
            "beta_warning": not runtime.packaged and install_state["status"] == "missing",
        })

    # Check if Hermes is actually running (api-server responding).
    config = IRISConfig.load()
    if runtime.packaged:
        from wizard.health_check import check_gateway_health
        health = await check_gateway_health(config.hermes_url)
        return JSONResponse({**install_state,
                             "status": "connected" if health.get("ok") else install_state["status"],
                             "mode": "skip" if health.get("ok") else install_state["mode"],
                             "beta_warning": False, "runtime": runtime_policy.runtime_public_info()})
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            from wizard.hermes_config import resolve_api_server_key, resolve_local_api_server_url
            token = resolve_api_server_key(config.hermes_api_key, config.hermes_url)
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            base_url = resolve_local_api_server_url(config.hermes_url)
            res = await client.get(f"{base_url}/v1/models", headers=headers)
            if res.status_code == 200:
                # Hermes is installed AND running — quick path!
                models = [m.get("id", "?") for m in res.json().get("data", [])]
                return JSONResponse({
                    "mode": "skip",
                    "status": "connected",
                    "message": "Hermes detectado y funcionando.",
                    "models": models,
                    "beta_warning": False,
                    "cli_path": install_state["cli_path"],
                    "version": install_state["version"],
                    "compatibility": install_state["compatibility"],
                })
    except Exception:
        pass

    # 3. Hermes is installed but NOT running — pre-select "quick" so
    # the user sees "Configuración básica" as the recommended path.
    # The frontend maps it to "Activar Gateway" and auto-starts the gateway.
    return JSONResponse({
        **install_state,
        "message": "Hermes instalado pero no se está ejecutando.",
        "beta_warning": False,
    })


@app.get("/api/test-hermes")
async def test_hermes() -> JSONResponse:
    """Test Hermes api-server connectivity."""
    runtime = runtime_policy.get_runtime()
    config = IRISConfig.load()
    if runtime.packaged:
        from wizard.health_check import check_gateway_health
        health = await check_gateway_health(config.hermes_url)
        if health.get("ok"):
            return JSONResponse({"status": "connected", "code": "", "message": health.get("detail"),
                                 "runtime": runtime_policy.runtime_public_info()})
        # A stopped Gateway is not a missing engine: report the bundled state so the
        # step offers activation or repair of Iris, never an installation.
        install_state = await asyncio.to_thread(_hermes_installation_state)
        return JSONResponse({"status": install_state["status"], "code": "gateway_runtime_unverified",
                             "message": health.get("detail"), "version": install_state.get("version") or "",
                             "diagnostic": install_state.get("diagnostic") or "",
                             "installation_kind": "bundled", "runtime": install_state["runtime"]})
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            # Bearer matches what wizard/hermes_config.py wrote into
            # ~/.hermes/.env. Override with the user's hermes_api_key
            # if they pasted a custom one.
            from wizard.hermes_config import resolve_api_server_key, resolve_local_api_server_url
            token = resolve_api_server_key(config.hermes_api_key, config.hermes_url)
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            base_url = resolve_local_api_server_url(config.hermes_url)
            res = await client.get(f"{base_url}/v1/models", headers=headers)
            if res.status_code == 200:
                models = [m.get("id", "?") for m in res.json().get("data", [])]
                return JSONResponse({"status": "connected", "models": models})
            return JSONResponse({"status": "error", "code": res.status_code})
    except Exception as e:
        # Path existence is not enough: a partial install can leave a broken
        # launcher behind. Keep that distinct so the UI offers reinstall
        # rather than repeatedly trying to start it.
        install_state = await asyncio.to_thread(_hermes_installation_state)
        if install_state["status"] != "missing":
            return JSONResponse({
                "status": install_state["status"],
                "path": install_state.get("cli_path") or "",
                "version": install_state.get("version") or "",
                "diagnostic": install_state.get("diagnostic") or "",
            })
        return JSONResponse({"status": "unreachable", "error": str(e)})


@app.get("/api/voices")
async def list_voices(lang: str = "") -> JSONResponse:
    """List available Edge-TTS voices, optionally filtered by language."""
    try:
        import edge_tts

        all_voices = await edge_tts.list_voices()

        # Curated favorites per language for quick selection
        favorites = {
            "es-MX-DaliaNeural", "es-MX-JorgeNeural",
            "es-CO-GonzaloNeural", "es-CO-SalomeNeural",
            "es-ES-ElviraNeural", "es-ES-AlvaroNeural",
            "es-AR-ElenaNeural", "es-AR-TomasNeural",
            "en-US-JennyNeural", "en-US-GuyNeural",
            "en-US-AriaNeural", "en-US-DavisNeural",
            "en-GB-SoniaNeural", "en-GB-RyanNeural",
            "ja-JP-NanamiNeural", "ja-JP-KeitaNeural",
            "pt-BR-FranciscaNeural", "pt-BR-AntonioNeural",
            "fr-FR-DeniseNeural", "fr-FR-HenriNeural",
            "de-DE-KatjaNeural", "de-DE-ConradNeural",
            "ko-KR-SunHiNeural", "zh-CN-XiaoxiaoNeural",
        }

        voices = []
        for v in all_voices:
            short_name = v.get("ShortName", "")
            voice_lang = v.get("Locale", "")

            # Filter by language if specified
            if lang and not voice_lang.lower().startswith(lang.lower()):
                continue

            voices.append({
                "id": short_name,
                "name": v.get("FriendlyName", short_name),
                "lang": voice_lang,
                "gender": v.get("Gender", ""),
                "favorite": short_name in favorites,
            })

        # Sort: favorites first, then by language, then by name
        voices.sort(key=lambda x: (not x["favorite"], x["lang"], x["name"]))

        return JSONResponse({"voices": voices, "count": len(voices)})

    except ImportError:
        return JSONResponse({"voices": [], "error": "edge-tts not installed"})
    except Exception as e:
        return JSONResponse({"voices": [], "error": str(e)})


@app.post("/api/test-audio")
async def test_audio(request: Request) -> JSONResponse:
    """Play a test beep through the default speaker."""
    _enforce_local_install_request(request, "preview-tts", "el audio")
    try:
        import numpy as np
        import sounddevice as sd

        sr = 44100
        duration = 0.3
        t = np.linspace(0, duration, int(sr * duration), False)
        # Two-tone chord (A4 + E5) for a pleasant sound
        beep = (np.sin(2 * np.pi * 440 * t) + np.sin(2 * np.pi * 659 * t)) * 0.15
        # Fade in/out to avoid clicks
        fade = int(sr * 0.02)
        beep[:fade] *= np.linspace(0, 1, fade)
        beep[-fade:] *= np.linspace(1, 0, fade)

        sd.play(beep.astype(np.float32), sr)
        sd.wait()
        return JSONResponse({"status": "ok"})
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


# ── Preview-engine cache (FIX W1) ───────────────────────
# /api/test-tts-audio used to build a fresh TextToSpeech + call
# initialize() on every click. For Coqui / Kokoro / NeuTTS that means
# loading a large local model PER preview, which routinely tripped the
# wizard's 30s HTTP timeout on first-run and made the UI feel broken
# even when the engine actually worked.
#
# We now cache initialised engines keyed by (provider, voice/model_id)
# so repeat clicks on the same card reuse the warm model. The cache is
# bounded (LRU, 4 entries) — preview is a one-off UI affordance, we
# don't need every voice ever previewed to stick around in RAM.
import collections as _collections

_TTS_PREVIEW_CACHE: "_collections.OrderedDict[tuple[str, str], Any]" = (
    _collections.OrderedDict()
)
_TTS_PREVIEW_CACHE_MAX = 4
_TTS_PREVIEW_LOCK = asyncio.Lock()


def _preview_cache_key(provider: str, voice: str, credential: str = "") -> tuple[str, str]:
    """Identity for a preview engine. Voice/model id is part of the key
    so switching voices invalidates the cached instance — otherwise the
    user would always hear the first voice they previewed."""
    fingerprint = ""
    if credential:
        import hashlib
        fingerprint = hashlib.sha256(credential.encode("utf-8")).hexdigest()[:10]
    identity = (voice or "").strip()
    if fingerprint:
        identity = f"{identity}#key:{fingerprint}"
    return (provider or "edge-tts", identity)


# Loads in flight, one per identity: a second preview awaits the first instead
# of building (and leaking) another engine. Invalidation bumps the provider's
# epoch and cancels its loads, so a retired load closes itself and is never
# published. All of it is per provider: other engines are never blocked.
_TTS_PREVIEW_LOADING: dict[tuple[str, str], "asyncio.Task[Any]"] = {}
_TTS_PREVIEW_EPOCH: dict[str, int] = {}


class _RetiredPreview(Exception):
    """The voice changed (clone, install or removal) while this engine loaded."""


async def _load_preview_engine(cache_key: tuple[str, str], cfg: Any, timeout: float, epoch: int) -> Any:
    from core.tts import TextToSpeech

    fresh = TextToSpeech(cfg)
    evicted: list[Any] = []
    try:
        await asyncio.wait_for(fresh.initialize(), timeout=timeout)
        async with _TTS_PREVIEW_LOCK:
            retired = _TTS_PREVIEW_EPOCH.get(cache_key[0], 0) != epoch
            if not retired:
                _TTS_PREVIEW_CACHE[cache_key] = fresh
                # LRU bound — evict oldest beyond cap.
                while len(_TTS_PREVIEW_CACHE) > _TTS_PREVIEW_CACHE_MAX:
                    evicted.append(_TTS_PREVIEW_CACHE.popitem(last=False)[1])
        if retired:
            raise _RetiredPreview()
    except BaseException:
        # A failed, timed-out, cancelled or retired load owns a worker: close it.
        with contextlib.suppress(Exception):
            await fresh.close()
        raise
    finally:
        async with _TTS_PREVIEW_LOCK:
            if _TTS_PREVIEW_LOADING.get(cache_key) is asyncio.current_task():
                _TTS_PREVIEW_LOADING.pop(cache_key, None)
    for stale in evicted:
        # Isolated extra engines own a worker process (A08.5).
        with contextlib.suppress(Exception):
            await stale.close()
    return fresh


async def _preview_engine(cache_key: tuple[str, str], cfg: Any, timeout: float) -> Any:
    """The cached engine for this identity, joining its load if one is running."""
    async with _TTS_PREVIEW_LOCK:
        engine = _TTS_PREVIEW_CACHE.pop(cache_key, None)
        if engine is not None:
            _TTS_PREVIEW_CACHE[cache_key] = engine   # mark as MRU
            return engine
        task = _TTS_PREVIEW_LOADING.get(cache_key)
        if task is None:
            epoch = _TTS_PREVIEW_EPOCH.get(cache_key[0], 0)
            task = asyncio.ensure_future(_load_preview_engine(cache_key, cfg, timeout, epoch))
            _TTS_PREVIEW_LOADING[cache_key] = task
    try:
        # Shielded: one caller giving up never cancels the load others await.
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        current = asyncio.current_task()
        if task.cancelled() and not (current is not None and current.cancelling()):
            raise _RetiredPreview() from None
        raise


@app.post("/api/test-tts-audio")
async def test_tts_audio(
    request: Request,
    preview: TTSPreviewRequest,
    tts_api_key: str = Header("", alias="X-IRIS-TTS-Key"),
) -> Response:
    """Generate a real TTS sample and stream the audio bytes back so
    the browser can play them through an HTML5 ``<audio>`` element.

    This is the "hear it actually" companion to ``/api/test-tts``,
    which only returns metadata. We piggy-back on ``core.tts``'s
    existing provider dispatch so any new TTS engine added there
    works here automatically — the same code path the running agent
    uses to speak, just behind an HTTP boundary.

    Why not just shell out to `play` or `aplay`: those would play on
    the SERVER's speakers, which is exactly wrong if the wizard is
    being used remotely (e.g. from a phone via the LAN-access flag).
    Returning bytes lets the BROWSER play them through whatever
    output device the user is actually listening on.

    Limits:
      - text capped at 240 chars to prevent giant downloads. Plenty
        for a "Hola mundo" test; users can still edit the input.
      - 30s overall timeout for remote providers. Local engines (Kokoro,
        Qwen3-TTS and other approved extras) may download their weights into
        the profile on first use, so they get the isolated worker's own load
        and synthesis limits; a timeout is still reported as failure.
    """
    _enforce_local_install_request(request, "preview-tts", "la voz")
    provider = (preview.provider or "").strip().lower()
    voice = (preview.voice or "").strip()[:256]
    model = (preview.model or "").strip()[:256]
    fish_url = (preview.fish_url or "").strip()[:512]
    text = (preview.text or "").strip()[:240]
    if not text:
        text = "Hola mundo, soy IRIS."
    supported = {
        "edge-tts", "coqui", "elevenlabs", "minimax",
        "fish-speech", "fish-audio", "kokoro", "neutts", "hf-s2s",
    }
    if provider not in supported:
        return JSONResponse({
            "status": "error",
            "message": f"El proveedor TTS '{provider}' no está soportado.",
        }, status_code=400)
    tts_api_key = (tts_api_key or "").strip()[:512]

    try:
        cfg = IRISConfig.load()
        # Override provider + relevant voice fields based on the
        # incoming request so the user can preview voices BEFORE
        # committing the choice in their config.
        cfg.tts_provider = provider
        # A preview must exercise the engine named on the selected card.
        # Playing Edge-TTS after a local engine fails would be a false
        # positive and could send text off-device unexpectedly.
        cfg.tts_allow_remote_fallback = False
        if provider == "edge-tts" and voice:
            cfg.edge_tts_voice = voice
        elif provider == "kokoro" and voice:
            cfg.kokoro_voice = voice
        elif provider == "minimax" and voice:
            cfg.minimax_voice_id = voice
        elif provider == "elevenlabs" and voice:
            cfg.elevenlabs_voice_id = voice
        elif provider == "fish-audio":
            from core.fish_audio import FishAudioError, validate_model

            try:
                cfg.fish_audio_model = validate_model(model or cfg.fish_audio_model)
            except FishAudioError as exc:
                return JSONResponse({"status": "error", "message": str(exc)}, status_code=400)
            cfg.fish_audio_voice_id = voice or cfg.fish_audio_voice_id
            cfg.fish_audio_api_key = tts_api_key or cfg.fish_audio_api_key
            fish_error = _tts_config_error(cfg)
            if fish_error:
                return JSONResponse({"status": "error", "message": fish_error[1]}, status_code=400)
        elif provider == "fish-speech" and fish_url:
            url_error = _local_service_url_error(fish_url)
            if url_error:
                return JSONResponse({"status": "error", "message": url_error}, status_code=400)
            cfg.fish_speech_url = fish_url.rstrip("/")
        elif provider == "hf-s2s":
            selected_model = model or cfg.hf_s2s_model
            selected_speaker = voice or cfg.hf_s2s_speaker
            if selected_model not in _HF_S2S_QWEN_MODELS:
                return JSONResponse({
                    "status": "error",
                    "message": "Selecciona uno de los modelos Qwen3-TTS validados por IRIS.",
                }, status_code=400)
            if "Base" not in selected_model and selected_speaker not in _HF_S2S_QWEN_SPEAKERS:
                return JSONResponse({
                    "status": "error",
                    "message": "Selecciona una voz predefinida válida de Qwen3-TTS.",
                }, status_code=400)
            cfg.hf_s2s_model = selected_model
            cfg.hf_s2s_speaker = selected_speaker
            qwen_error = _tts_config_error(cfg)
            if qwen_error:
                _code, message = qwen_error
                return JSONResponse({"status": "error", "message": message}, status_code=400)
        if provider == "kokoro":
            hardware_error = _voice_hardware_error(provider)
            if hardware_error:
                return JSONResponse({"status": "error", "message": hardware_error[1]}, status_code=409)
        if provider == "minimax" and tts_api_key:
            cfg.minimax_api_key = tts_api_key.strip()
        elif provider == "elevenlabs" and tts_api_key:
            cfg.elevenlabs_api_key = tts_api_key.strip()
        if provider == "elevenlabs" and not (cfg.elevenlabs_voice_id or "").strip():
            return JSONResponse({
                "status": "error",
                "message": "Selecciona o clona una voz de ElevenLabs antes de probar.",
            }, status_code=400)

        # Spin up the TTS engine just for this preview. We do NOT
        # share the agent's TTS instance because (a) the agent might
        # not be running yet during wizard setup, and (b) we want
        # provider switching to work without restarting IRIS.
        #
        # Cached path (FIX W1): if we've already initialised the same
        # (provider, voice) tuple in this wizard session, reuse it —
        # avoids reloading the model on every preview click.
        from core.tts import LOCAL_ENGINE_PROVIDERS, IsolatedExtraTTSProvider
        if provider == "hf-s2s":
            ref_text_fingerprint = hashlib.sha256(
                cfg.hf_s2s_ref_text.encode("utf-8")
            ).hexdigest()[:10] if cfg.hf_s2s_ref_text else ""
            preview_identity = "|".join((
                cfg.hf_s2s_model,
                cfg.hf_s2s_speaker,
                cfg.hf_s2s_ref_audio,
                ref_text_fingerprint,
            ))
        elif provider == "fish-audio":
            preview_identity = f"{cfg.fish_audio_model}|{cfg.fish_audio_voice_id}"
        elif provider == "elevenlabs":
            preview_identity = cfg.elevenlabs_voice_id
        elif provider == "minimax":
            preview_identity = f"{cfg.minimax_tts_model}|{cfg.minimax_voice_id}"
        else:
            preview_identity = voice or (cfg.fish_speech_url if provider == "fish-speech" else "")
        credential = {"fish-audio": cfg.fish_audio_api_key, "elevenlabs": cfg.elevenlabs_api_key,
                      "minimax": cfg.minimax_api_key}.get(provider, tts_api_key)
        cache_key = _preview_cache_key(provider, preview_identity, credential)
        local_engine = provider in LOCAL_ENGINE_PROVIDERS
        # A first preview may be the moment a local model downloads (A08.5).
        load_timeout = IsolatedExtraTTSProvider.LOAD_TIMEOUT + 30.0 if local_engine else 30.0
        # A slow local engine (Qwen3-TTS on CPU) may declare a longer budget per character.
        speak_timeout = IsolatedExtraTTSProvider.max_synth_timeout(text) + 30.0 if local_engine else 30.0
        try:
            engine = await _preview_engine(cache_key, cfg, load_timeout)
        except _RetiredPreview:
            return JSONResponse({
                "status": "error",
                "message": "La voz cambió mientras se cargaba el motor. Vuelve a probarla.",
            }, status_code=409)
        except asyncio.TimeoutError:
            raise
        except Exception as ce:
            # Clean 503 so the UI can show "engine unavailable"
            # instead of a generic 500 stack trace.
            return JSONResponse({
                "status":  "error",
                "message": (
                    f"No se pudo inicializar '{provider}': "
                    f"{type(ce).__name__}: {ce}"
                ),
            }, status_code=503)
        audio_bytes, _viseme = await asyncio.wait_for(
            engine.speak(text), timeout=speak_timeout,
        )

        if not audio_bytes or audio_bytes == b"FALLBACK_FAILED":
            # A local engine reports why it failed; show that instead of a guess.
            reports_reason = local_engine or provider in {"fish-audio", "edge-tts"}
            reason = str(getattr(engine, "last_error", "") or "").strip() if reports_reason else ""
            label = {"fish-audio": "Fish Audio", "edge-tts": "Edge-TTS"}.get(provider, f"El proveedor '{provider}'")
            return JSONResponse({
                "status":  "error",
                "message": (
                    f"{label} no produjo audio: {reason}" if reason else (
                        f"El proveedor '{provider}' no produjo audio. "
                        "Revisa los logs (puede faltar API key, modelo no descargado, "
                        "o referencia de voz para NeuTTS/Qwen3-TTS Base)."
                    )
                ),
            }, status_code=502)

        # Heuristic content-type — edge-tts ships MP3, the rest of our
        # providers wrap PCM in WAV. The browser's <audio> element
        # auto-detects from the bytes regardless, but a correct
        # Content-Type helps caching and devtools.
        #
        # MP3 detection: ID3 tag at start, OR an MP3 frame sync
        # (12 bits of 1: byte 0 = 0xFF, top 3 bits of byte 1 = 0b111).
        # Edge-TTS streams use various frame variants — \xff\xfb (MPEG-1
        # L3), \xff\xfa, \xff\xf3 (MPEG-2 L3), \xff\xf2 — so checking
        # only one variant misclassifies most streams.
        media_type = "audio/wav"
        if len(audio_bytes) >= 3 and audio_bytes[:3] == b"ID3":
            media_type = "audio/mpeg"
        elif (len(audio_bytes) >= 2
              and audio_bytes[0] == 0xFF
              and (audio_bytes[1] & 0xE0) == 0xE0):
            media_type = "audio/mpeg"
        elif len(audio_bytes) >= 4 and audio_bytes[:4] == b"RIFF":
            media_type = "audio/wav"
        return Response(content=audio_bytes, media_type=media_type)

    except asyncio.TimeoutError:
        return JSONResponse({
            "status":  "error",
            "message": (
                f"Timeout generando audio de prueba con '{provider}'. "
                "Si es la primera vez probando este proveedor puede tardar "
                "más (descarga inicial del modelo). Reintenta en un momento."
            ),
        }, status_code=504)
    except Exception as e:
        return JSONResponse({
            "status":  "error",
            "message": f"{type(e).__name__}: {e}",
        }, status_code=500)


@app.post("/api/test-tts")
async def test_tts(request: Request, provider: str = "edge-tts", voice: str = "es-MX-DaliaNeural") -> JSONResponse:
    """Test a TTS provider with a short phrase."""
    _enforce_local_install_request(request, "preview-tts", "la voz")
    text = "¡Hola! Soy IRIS, estoy lista para ayudarte."

    try:
        if provider == "edge-tts":
            import edge_tts

            communicate = edge_tts.Communicate(text, voice)
            tmp = Path(tempfile.mktemp(suffix=".mp3"))
            await communicate.save(str(tmp))

            size = tmp.stat().st_size
            tmp.unlink(missing_ok=True)
            return JSONResponse({"status": "ok", "provider": provider, "bytes": size})

        elif provider == "coqui":
            return JSONResponse({
                "status": "info",
                "message": "Coqui TTS requiere descargar el modelo seleccionado. "
                           "Se descargará automáticamente al iniciar IRIS.",
            })

        elif provider == "elevenlabs":
            return JSONResponse({
                "status": "info",
                "message": "ElevenLabs requiere una API key. "
                           "Configúrala en IRIS_ELEVENLABS_API_KEY.",
            })

        elif provider == "fish-audio":
            return JSONResponse({"status": "info", "message": "Fish Audio necesita tu API key y una voz. Usa «Escuchar voz seleccionada» para generar una muestra."})
        elif provider == "fish-speech":
            config = IRISConfig.load()
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    res = await client.get(f"{config.fish_speech_url}/v1/health")
                    if res.status_code == 200:
                        return JSONResponse({
                            "status": "ok",
                            "provider": "fish-speech",
                            "message": "Fish Speech server está corriendo y listo. ✅",
                        })
                    else:
                        return JSONResponse({
                            "status": "error",
                            "provider": "fish-speech",
                            "message": f"Fish Speech respondió con HTTP {res.status_code}.",
                        })
            except Exception:
                return JSONResponse({
                    "status": "unreachable",
                    "provider": "fish-speech",
                    "message": f"No se pudo conectar al servidor Fish Speech en {config.fish_speech_url}. "
                               "Inicia el API server con el método oficial de Fish Speech (por ejemplo, Docker Compose).",
                })

        return JSONResponse({"status": "error", "message": f"Provider desconocido: {provider}"})

    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


@app.get("/api/tts-voices")
async def list_tts_voices() -> JSONResponse:
    """List available Edge-TTS voices for Spanish."""
    try:
        import edge_tts

        voices = await edge_tts.list_voices()
        spanish = [
            {"id": v["ShortName"], "name": v["FriendlyName"], "gender": v["Gender"]}
            for v in voices
            if v["Locale"].startswith("es-")
        ]
        return JSONResponse({"voices": spanish})
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)})


_VOICE_CLONE_PROVIDERS = {
    "elevenlabs", "minimax", "fish-audio", "coqui", "neutts", "fish-speech", "hf-s2s",
}
_VOICE_CLONE_MAX_BYTES = 20 * 1024 * 1024
_COQUI_XTTS_MODEL = "tts_models/multilingual/multi-dataset/xtts_v2"
_HF_S2S_QWEN_MODELS = {
    "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",
    "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice",
    "Qwen/Qwen3-TTS-12Hz-0.6B-Base",
    "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
}
_HF_S2S_QWEN_SPEAKERS = {
    "Vivian", "Serena", "Uncle_Fu", "Dylan", "Eric",
    "Ryan", "Aiden", "Ono_Anna", "Sohee",
}


def _clone_wav_duration(audio_bytes: bytes) -> float:
    """Validate the normalized upload and return its duration in seconds.

    The browser converts recordings/uploads to mono PCM WAV before sending.
    Enforcing that contract here prevents the old failure where WebM bytes
    were merely renamed to ``.wav`` and then rejected by NeuTTS/XTTS.
    """
    import io
    import wave

    if len(audio_bytes) > _VOICE_CLONE_MAX_BYTES:
        raise ValueError("El audio supera el máximo de 20 MB.")
    if len(audio_bytes) < 44 or audio_bytes[:4] != b"RIFF" or audio_bytes[8:12] != b"WAVE":
        raise ValueError(
            "La muestra debe llegar como WAV PCM. Vuelve a seleccionarla para que el wizard la normalice."
        )
    try:
        with wave.open(io.BytesIO(audio_bytes), "rb") as wav:
            if wav.getnchannels() != 1:
                raise ValueError("La referencia debe ser mono.")
            if wav.getsampwidth() != 2:
                raise ValueError("La referencia debe usar PCM de 16 bits.")
            rate = wav.getframerate()
            frames = wav.getnframes()
    except (wave.Error, EOFError) as exc:
        raise ValueError("El archivo WAV está dañado o incompleto.") from exc
    if rate < 16_000 or rate > 44_100:
        raise ValueError("La referencia debe tener una frecuencia entre 16 y 44.1 kHz.")
    return frames / rate if rate else 0.0


def _validate_clone_duration(provider: str, duration: float) -> None:
    limits = {
        "elevenlabs": (3.0, 120.0),
        "minimax": (10.0, 300.0),
        "coqui": (3.0, 120.0),
        "neutts": (3.0, 15.0),
        "fish-speech": (10.0, 30.0),
        # Fish suggests 30-60 s when a shorter clone sounds robotic.
        "fish-audio": (10.0, 60.0),
        "hf-s2s": (3.0, 15.0),
    }
    minimum, maximum = limits[provider]
    if duration < minimum or duration > maximum:
        raise ValueError(
            f"{provider} requiere una muestra de {minimum:g} a {maximum:g} segundos "
            f"(recibida: {duration:.1f} s)."
        )


def _safe_clone_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", (name or "").strip()).strip("_") or "iris_clone"


def _clone_reference_paths(
    directory: Path,
    name: str,
    tag: str = "",
    *,
    transcript: bool = False,
) -> tuple[Path, Path | None]:
    """Choose non-destructive local reference paths for one cloned voice."""
    import uuid

    stem = _safe_clone_name(name)
    if tag:
        stem = f"{stem}_{tag}"
    wav_path = directory / f"{stem}.wav"
    txt_path = directory / f"{stem}.txt" if transcript else None
    if wav_path.exists() or (txt_path is not None and txt_path.exists()):
        stem = f"{stem}_{uuid.uuid4().hex[:8]}"
        wav_path = directory / f"{stem}.wav"
        txt_path = directory / f"{stem}.txt" if transcript else None
    return wav_path, txt_path


def _minimax_clone_voice_id(name: str) -> str:
    """Build a valid, collision-resistant MiniMax voice id."""
    import uuid

    safe = _safe_clone_name(name)
    if not safe[0].isalpha():
        safe = f"Iris_{safe}"
    safe = safe[:220].rstrip("-_") or "IrisClone"
    return f"{safe}_{uuid.uuid4().hex[:10]}"


async def _invalidate_tts_preview_provider(provider: str) -> None:
    # Close, never just drop: a dropped local engine left its isolated worker
    # running with the model in RAM (about 12 GB for Qwen3 1.7B) and holding the
    # extra, so «Quitar» answered «en uso» until the wizard restarted.
    await _close_preview_engines(provider)


_VOICE_CLONE_OPERATION_LOCK = asyncio.Lock()
_VOICE_CLONE_RESULTS: dict[str, tuple[float, int, bytes]] = {}


@app.post("/api/clone-voice")
async def clone_voice(
    request: Request,
    audio: UploadFile = File(...),
    name: str = Form("IRIS Clone"),
    provider: str = Form("elevenlabs"),
    model: str = Form(""),
    transcript: str = Form(""),
    api_key: str = Form(""),
    consent_confirmed: bool = Form(False),
) -> Response:
    """Deduplicate a voice-clone request before it can reach a paid API."""
    _enforce_local_install_request(request, "clone-voice", "voces")
    request_key = getattr(request, "headers", {}).get("idempotency-key", "").strip()[:128]
    async with _VOICE_CLONE_OPERATION_LOCK:
        now = time.monotonic()
        for key, (created_at, _status, _body) in list(_VOICE_CLONE_RESULTS.items()):
            if now - created_at > 600:
                _VOICE_CLONE_RESULTS.pop(key, None)
        if request_key and request_key in _VOICE_CLONE_RESULTS:
            _created_at, status_code, body = _VOICE_CLONE_RESULTS[request_key]
            return Response(content=body, status_code=status_code, media_type="application/json")
        response = await _clone_voice_impl(
            request=request,
            audio=audio,
            name=name,
            provider=provider,
            model=model,
            transcript=transcript,
            api_key=api_key,
            consent_confirmed=consent_confirmed,
        )
        if request_key:
            _VOICE_CLONE_RESULTS[request_key] = (
                now,
                response.status_code,
                bytes(response.body),
            )
            if len(_VOICE_CLONE_RESULTS) > 128:
                _VOICE_CLONE_RESULTS.pop(next(iter(_VOICE_CLONE_RESULTS)))
        return response


async def _clone_voice_impl(
    request: Request,
    audio: UploadFile,
    name: str,
    provider: str,
    model: str,
    transcript: str,
    api_key: str,
    consent_confirmed: bool,
) -> JSONResponse:
    """
    Clone a voice from an audio sample.
    - ElevenLabs: sends to EL API → returns voice_id
    - MiniMax: sends to MiniMax API → returns voice_id
    - Coqui/NeuTTS/Fish Speech/HF Qwen3-TTS: persists a normalized local WAV reference
    """
    # FastAPI unwraps Form defaults for HTTP requests. Direct Python callers
    # (small tests, diagnostics or future internal reuse) receive the Form
    # metadata object when an optional argument is omitted, so normalise the
    # public function boundary as well.
    provider = provider if isinstance(provider, str) else "elevenlabs"
    model = model if isinstance(model, str) else ""
    name = name if isinstance(name, str) else "IRIS Clone"
    transcript = transcript if isinstance(transcript, str) else ""
    api_key = api_key if isinstance(api_key, str) else ""
    consent_confirmed = consent_confirmed if isinstance(consent_confirmed, bool) else False

    provider = (provider or "").strip().lower()
    model = (model or "").strip()[:256]
    if provider not in _VOICE_CLONE_PROVIDERS:
        return JSONResponse(
            {"status": "error", "error": f"Provider '{provider}' no soporta clonación de voz."},
            status_code=400,
        )
    name = (name or "IRIS Clone").strip()[:80] or "IRIS Clone"
    transcript = (transcript or "").strip()[:4000]
    if not consent_confirmed:
        return JSONResponse({
            "status": "error",
            "error": "Confirma que tienes permiso para usar y clonar esta voz.",
        }, status_code=400)

    audio_bytes = await audio.read(_VOICE_CLONE_MAX_BYTES + 1)
    try:
        duration = _clone_wav_duration(audio_bytes)
        _validate_clone_duration(provider, duration)
    except ValueError as exc:
        return JSONResponse({"status": "error", "error": str(exc)}, status_code=400)

    config = IRISConfig.load()
    supplied_key = api_key.strip()[:512]

    if provider == "fish-audio":
        from core.fish_audio import FishAudioError, create_voice

        resolved_key = supplied_key or config.fish_audio_api_key
        # Optional: without a transcript Fish transcribes the sample itself.
        try:
            voice_id = await create_voice(resolved_key, name, audio_bytes, transcript.strip())
        except FishAudioError as exc:
            return JSONResponse({"status": "error", "error": str(exc)}, status_code=400 if not resolved_key else 502)
        config.fish_audio_voice_id = voice_id
        config.fish_audio_voice_name = name
        if supplied_key:
            config.fish_audio_api_key = supplied_key
        config.save()
        await _invalidate_tts_preview_provider(provider)
        return JSONResponse({"status": "ok", "provider": provider, "voice_id": voice_id,
                             "name": name, "duration": round(duration, 1),
                             "message": "Voz privada creada en tu cuenta de Fish Audio. Escúchala para comprobar el resultado."})

    # ── ElevenLabs Instant Clone ──
    elif provider == "elevenlabs":
        resolved_key = supplied_key or config.elevenlabs_api_key
        if not resolved_key:
            return JSONResponse({
                "status": "error",
                "error": "Introduce una API key de ElevenLabs para crear la voz.",
            }, status_code=400)

        try:
            import httpx

            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    "https://api.elevenlabs.io/v1/voices/add",
                    headers={"xi-api-key": resolved_key},
                    data={
                        "name": name,
                        "description": f"Voice clone for {name} via IRIS Setup Wizard",
                    },
                    files={"files": ("voice_sample.wav", audio_bytes, "audio/wav")},
                )

            if response.status_code == 200:
                data = response.json()
                voice_id = data.get("voice_id", "")
                if not voice_id:
                    return JSONResponse({
                        "status": "error",
                        "error": "ElevenLabs aceptó la solicitud pero no devolvió un voice_id.",
                    }, status_code=502)
                config.elevenlabs_voice_id = voice_id
                if supplied_key:
                    config.elevenlabs_api_key = supplied_key
                config.save()
                await _invalidate_tts_preview_provider(provider)
                return JSONResponse({
                    "status": "ok",
                    "provider": "elevenlabs",
                    "voice_id": voice_id,
                    "name": name,
                    "duration": round(duration, 1),
                })
            else:
                error_msg = response.text[:200]
                return JSONResponse({
                    "status": "error",
                    "error": f"ElevenLabs {response.status_code}: {error_msg}",
                }, status_code=502)

        except Exception as e:
            return JSONResponse({"status": "error", "error": str(e)}, status_code=502)

    # ── MiniMax Voice Clone ──
    elif provider == "minimax":
        resolved_key = supplied_key or config.minimax_api_key
        if not resolved_key:
            return JSONResponse({
                "status": "error",
                "error": "Introduce una API key de MiniMax para crear la voz.",
            }, status_code=400)

        try:
            import httpx

            async with httpx.AsyncClient(timeout=60.0) as client:
                headers = {"Authorization": f"Bearer {resolved_key}"}
                upload = await client.post(
                    "https://api.minimax.io/v1/files/upload",
                    headers=headers,
                    data={"purpose": "voice_clone"},
                    files={"file": ("voice_sample.wav", audio_bytes, "audio/wav")},
                )
                if upload.status_code != 200:
                    return JSONResponse({
                        "status": "error",
                        "error": f"MiniMax upload {upload.status_code}: {upload.text[:200]}",
                    }, status_code=502)
                file_id = upload.json().get("file", {}).get("file_id")
                if file_id is None:
                    return JSONResponse({
                        "status": "error",
                        "error": "MiniMax no devolvió file_id para el audio subido.",
                    }, status_code=502)

                voice_id = _minimax_clone_voice_id(name)
                response = await client.post(
                    "https://api.minimax.io/v1/voice_clone",
                    headers={**headers, "Content-Type": "application/json"},
                    json={
                        "file_id": file_id,
                        "voice_id": voice_id,
                        "need_noise_reduction": False,
                        "need_volume_normalization": True,
                    },
                )

            if response.status_code == 200:
                data = response.json()
                base_resp = data.get("base_resp")
                if not isinstance(base_resp, dict) or base_resp.get("status_code") != 0:
                    return JSONResponse({
                        "status": "error",
                        "error": (
                            f"MiniMax: {base_resp.get('status_msg') or 'falló la clonación'}"
                            if isinstance(base_resp, dict)
                            else "MiniMax devolvió una respuesta de clonación incompleta."
                        ),
                    }, status_code=502)
                config.minimax_voice_id = voice_id
                if supplied_key:
                    config.minimax_api_key = supplied_key
                config.save()
                await _invalidate_tts_preview_provider(provider)
                return JSONResponse({
                    "status": "ok",
                    "provider": "minimax",
                    "voice_id": voice_id,
                    "name": name,
                    "duration": round(duration, 1),
                })
            else:
                return JSONResponse({
                    "status": "error",
                    "error": f"MiniMax {response.status_code}: {response.text[:200]}",
                }, status_code=502)

        except Exception as e:
            return JSONResponse({"status": "error", "error": str(e)}, status_code=502)

    # ── Coqui XTTS (local reference audio) ──
    elif provider == "coqui":
        try:
            ref_dir = get_iris_home() / "voices"
            ref_dir.mkdir(parents=True, exist_ok=True)
            ref_path, _ = _clone_reference_paths(ref_dir, name, "coqui")
            ref_path.write_bytes(audio_bytes)
            config.coqui_model = _COQUI_XTTS_MODEL
            config.coqui_ref_audio = str(ref_path)
            config.coqui_language = "es"
            config.save()
            await _invalidate_tts_preview_provider(provider)
            return JSONResponse({
                "status": "ok",
                "provider": "coqui",
                "voice_id": str(ref_path),
                "name": name,
                "duration": round(duration, 1),
                "message": "Referencia guardada y Coqui cambiado a XTTS v2 para clonación local.",
            })
        except Exception as e:
            return JSONResponse({"status": "error", "error": f"Coqui save failed: {e}"}, status_code=500)

    # ── HF speech-to-speech / Qwen3-TTS Base clone ──
    elif provider == "hf-s2s":
        transcript = (transcript or "").strip()
        if not transcript:
            return JSONResponse({
                "status": "error",
                "error": (
                    "Qwen3-TTS requiere la transcripción exacta de la muestra. "
                    "Escribe exactamente lo que se escucha en el WAV."
                ),
            }, status_code=400)

        selected_model = model or (config.hf_s2s_model or "")
        if selected_model not in _HF_S2S_QWEN_MODELS:
            return JSONResponse({
                "status": "error",
                "error": "El modelo Qwen3-TTS seleccionado no está validado por IRIS.",
            }, status_code=400)
        hardware_error = _voice_hardware_error(provider, selected_model)
        if hardware_error:
            return JSONResponse({"status": "error", "error": hardware_error[1]}, status_code=409)

        try:
            voices_dir = get_iris_home() / "voices"
            voices_dir.mkdir(parents=True, exist_ok=True)
            wav_path, txt_path = _clone_reference_paths(
                voices_dir, name, "qwen3", transcript=True,
            )
            assert txt_path is not None
            wav_path.write_bytes(audio_bytes)
            txt_path.write_text(transcript, encoding="utf-8")

            # Preserving the selected model size avoids a surprising 0.6B →
            # 1.7B download. Cloning itself requires the corresponding Base
            # checkpoint; preset-speaker CustomVoice checkpoints cannot clone.
            size = "1.7B" if "1.7B" in selected_model else "0.6B"
            config.hf_s2s_model = f"Qwen/Qwen3-TTS-12Hz-{size}-Base"
            config.hf_s2s_ref_audio = str(wav_path)
            config.hf_s2s_ref_text = transcript
            config.hf_s2s_language = "spanish"
            config.save()
            await _invalidate_tts_preview_provider(provider)
            return JSONResponse({
                "status": "ok",
                "provider": "hf-s2s",
                "voice_id": str(wav_path),
                "model": config.hf_s2s_model,
                "name": name,
                "duration": round(duration, 1),
                "message": (
                    "Referencia guardada y Qwen3-TTS cambiado al modelo Base "
                    "compatible con clonación local. Iris la usará en su próxima respuesta."
                ),
            })
        except Exception as e:
            return JSONResponse({
                "status": "error", "error": f"Qwen3-TTS save failed: {e}",
            }, status_code=500)

    # ── NeuTTS Voice Clone (on-device, no API call) ──
    elif provider == "neutts":
        # NeuTTS does its cloning at TTS-init time inside the agent
        # process — there's no remote service to talk to. Our job here
        # is just to (a) persist the audio + transcript somewhere stable,
        # and (b) update IRIS' config so the next agent restart picks
        # them up. The transcript is REQUIRED (NeuTTS uses it to align
        # phonemes during reference encoding).
        transcript = (transcript or "").strip()
        if not transcript:
            return JSONResponse({
                "status": "error",
                "error": "NeuTTS requiere la transcripción exacta del audio. "
                         "Escribe en el campo de texto exactamente lo que dijiste en el audio.",
            }, status_code=400)

        try:
            voices_dir = get_iris_home() / "voices"
            voices_dir.mkdir(parents=True, exist_ok=True)
            # Sanitise the name into a filesystem-safe filename. We
            # don't allow path traversal or extension overrides — the
            # output is always .wav alongside .txt.
            wav_path, txt_path = _clone_reference_paths(
                voices_dir, name, transcript=True,
            )
            assert txt_path is not None
            wav_path.write_bytes(audio_bytes)
            txt_path.write_text(transcript, encoding="utf-8")

            # Update the persisted config so the agent loads this
            # reference on next start. We don't restart TTS here —
            # the running agent (if any) keeps using its old reference
            # until restart, which matches NeuTTS's "encode once" model.
            config.neutts_ref_audio = str(wav_path)
            config.neutts_ref_text  = transcript
            # A decoder-only codec cannot encode a fresh recording. Migrate
            # old wizard defaults at the moment the user explicitly clones.
            config.neutts_codec_repo = "neuphonic/neucodec"
            if config.neutts_model_repo == "neuphonic/neutts-air-q4-gguf":
                config.neutts_model_repo = "neuphonic/neutts-nano-spanish-q4-gguf"
            config.save()
            await _invalidate_tts_preview_provider(provider)

            return JSONResponse({
                "status": "ok",
                "provider": "neutts",
                "voice_id": str(wav_path),
                "name": name,
                "message": (
                    "Voz guardada. NeuTTS la usará la próxima vez que arranques IRIS. "
                    "Se normalizó a WAV mono y se usará después de reiniciar IRIS."
                ),
            })
        except Exception as e:
            return JSONResponse({"status": "error", "error": f"NeuTTS save failed: {e}"}, status_code=500)

    # ── Fish Speech S2 inline reference ──
    transcript = (transcript or "").strip()
    if not transcript:
        return JSONResponse({
            "status": "error",
            "error": "Fish Speech requiere la transcripción exacta del audio de referencia.",
        }, status_code=400)
    try:
        voices_dir = get_iris_home() / "voices"
        voices_dir.mkdir(parents=True, exist_ok=True)
        wav_path, txt_path = _clone_reference_paths(
            voices_dir, name, "fish", transcript=True,
        )
        assert txt_path is not None
        wav_path.write_bytes(audio_bytes)
        txt_path.write_text(transcript, encoding="utf-8")
        config.fish_speech_ref_audio = str(wav_path)
        config.fish_speech_ref_text = transcript
        config.fish_speech_reference_id = ""
        config.save()
        await _invalidate_tts_preview_provider(provider)
        return JSONResponse({
            "status": "ok",
            "provider": "fish-speech",
            "voice_id": str(wav_path),
            "name": name,
            "duration": round(duration, 1),
            "message": "Referencia local guardada para el servidor Fish Speech S2.",
        })
    except Exception as e:
        return JSONResponse({"status": "error", "error": f"Fish Speech save failed: {e}"}, status_code=500)


@asset_upload_route(app, "/api/upload-model")
async def upload_model(file: UploadFile = File(...)) -> JSONResponse:
    """Validate and atomically store a VRM; activation remains in save-config."""
    try:
        safe_name = upload_filename(file.filename or "")
        if Path(safe_name).suffix.casefold() != ".vrm":
            raise AssetUploadError("El Wizard solo admite avatares .vrm.")
        content = await asyncio.to_thread(read_bounded, file.file, AVATAR_MAX_BYTES)
        metadata = await asyncio.to_thread(validate_asset, content, "vrm", avatar=True)
        dest = await asyncio.to_thread(
            atomic_avatar_write, get_iris_home() / "avatars", safe_name, content
        )
    except AssetUploadError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(503, "No se pudo guardar el avatar.") from exc
    extensions = metadata.get("extensions", {})
    return JSONResponse({
        "filename": safe_name,
        "format": "vrm",
        "size_bytes": len(content),
        "path": str(dest),
        "needs_conversion": False,
        "has_vrm_extension": any(name in extensions for name in ("VRM", "VRMC_vrm")),
        "extensions_used": sorted(metadata.get("extensionsUsed", list(extensions))),
    })


# ─────────────────────────────────────────────────────────────────────
# Animation uploads (.vrma + .bvh)
#
# .vrma plays natively via @pixiv/three-vrm-animation.
# .bvh is retargeted on the fly by face/js/bvh_retargeter.js onto the
# VRM humanoid — gives us access to the enormous free BVH mocap corpus
# (CMU, Mixamo exports, Truebones, MMD) without Blender round-trips.
# ─────────────────────────────────────────────────────────────────────

# Gestures and rests the person may replace from the wizard, by group: each is one factory
# clip (face/animations/manifest.json), and once replaced it plays alone, without the factory's
# variants or timed facial moments (core.animation_library). Room sequences (sitting, reading,
# the tablet, the bed, the desk, the window) are not offered: their clips are timed to the
# furniture and the props. The old empty slots (idle_smile, idle_love, idle_noble, idle_weave,
# look_around, sit, lie) went 2026-11-11 (QA23); clips already uploaded to them stay.
ANIMATION_SLOT_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Reposo", ("idle", "idle_casual", "idle_lazy", "idle_waiting", "idle_yawn", "stretch", "hair_touch")),
    ("Conversación", ("listen", "think", "talk", "nod", "shake", "confirm", "confused", "point", "attention")),
    ("Emociones", (
        "laugh", "sad", "angry", "surprised", "scared", "shy", "annoyed",
        "celebrate", "heart", "clap", "apologize", "shrug", "idea", "jump",
    )),
    ("Saludos y baile", ("wave", "wave_big", "wave_cute", "wave_flirty", "dance", "dance_sexy")),
)
ANIMATION_SLOTS: tuple[str, ...] = tuple(slot for _, slots in ANIMATION_SLOT_GROUPS for slot in slots)
_ANIMATION_SLOT_GROUP = {slot: group for group, slots in ANIMATION_SLOT_GROUPS for slot in slots}
# Extensions the per-slot uploader will accept. Must stay in sync with
# ``face/js/vrm_animation_loader.js`` dispatch table (.vrma/.bvh/.fbx/
# .glb/.gltf). Adding a new format here ONLY makes the wizard accept
# the upload; the actual playback dispatcher in ``vrm_animation_loader``
# is what teaches the avatar how to interpret it.


def _animations_dir() -> Path:
    """Return the same mutable animation directory used by Core."""
    from core.profile_paths import get_profile_paths

    return get_profile_paths().animations


def _animation_library():
    from core.animation_library import AnimationLibrary

    return AnimationLibrary(
        runtime_policy.get_runtime().assets_root / "animations", _animations_dir()
    )


@app.get("/api/animation-slots")
def list_animation_slots() -> JSONResponse:
    """
    Return the animation slot catalog + what's currently mapped in
    manifest.json. The wizard UI uses this to render an upload row per
    slot with a hint of what's already there.
    FastAPI runs this synchronous reader in its worker pool.
    """
    from core.animation_store import AnimationStoreError

    library = _animation_library()
    clips: dict[str, str] = {}
    revision = 0
    try:
        data = library.merged_manifest()
        clips = dict(data.get("clips") or {})
        revision = data.get("revision", 0)
    except FileNotFoundError:
        pass  # A fresh installation may not have a manifest yet.
    except Exception as e:
        # Preserve the existing validation response, but signal a read outage
        # as unavailable rather than claiming a successful empty catalog.
        return JSONResponse(
            {"slots": [], "error": f"manifest.json unreadable: {e}"},
            status_code=503 if isinstance(e, (OSError, AnimationStoreError)) else 200,
        )

    slots = []
    for slot in ANIMATION_SLOTS:
        filename = clips.get(slot) or ""
        try:
            file_path = library.resolve_file(filename) if filename else None
        except (OSError, ValueError, AnimationStoreError):
            file_path = None
        slots.append({
            "slot": slot,
            "group": _ANIMATION_SLOT_GROUP[slot],
            "filename": filename,
            "present": bool(file_path and file_path.exists()),
            "format": filename.rsplit(".", 1)[-1].lower() if "." in filename else "",
        })
    return JSONResponse({
        "slots": slots,
        "accepted_formats": list(ANIMATION_EXTENSIONS),
        "revision": revision,
    })


@asset_upload_route(app, "/api/upload-animation")
async def upload_animation(
    file: UploadFile = File(...),
    slot: str = Form(...),
) -> JSONResponse:
    """Commit a new version of one slot, preserving previous files and metadata."""
    from core.animation_store import AnimationStoreError
    from core.animations import synchronize_animation_upload

    if slot not in ANIMATION_SLOTS:
        raise HTTPException(400, "Slot de animación desconocido.")
    try:
        saved = await asyncio.to_thread(
            _animation_library().save_upload, file.file, file.filename or "", slot,
        )
    except AnimationStoreError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc

    sync = await synchronize_animation_upload()
    warnings = [saved.warning] if saved.warning else []
    warnings.extend(sync["warnings"])
    return JSONResponse({
        "status": "ok", "saved": True,
        "slot": slot, "filename": saved.filename, "format": saved.format,
        "size_bytes": saved.size, "path": str(saved.path),
        "revision": saved.revision, "changed": saved.changed,
        "sync_status": sync["sync_status"], "warnings": warnings,
    })


@app.post("/api/rig-model")
async def rig_model(filename: str, method: RigMethod = "auto") -> JSONResponse:
    """Rig an existing managed basename through an isolated job."""
    from core.rigging_jobs import RiggingError, rig_managed_model

    try:
        result = await rig_managed_model(get_iris_home() / "avatars", filename, method)
        return JSONResponse(result)
    except RiggingError as exc:
        return JSONResponse(
            {"status": "error", "message": str(exc)}, status_code=exc.status_code,
        )
    except Exception:
        logger.exception("Managed auto-rigging failed")
        return JSONResponse(
            {"status": "error", "message": "No se pudo completar el rigging."},
            status_code=500,
        )


# ── Known Kokoro voice IDs (FIX W3) ──────────────────────
# Mirrors the <option> values in wizard/ui/index.html's
# #kokoro-voice <select>. If you add a Kokoro voice option, add it
# here too — the wizard warns when saving an id that's not in this
# enumerable list. Kept as a set so membership tests are O(1).
_KOKORO_KNOWN_VOICES: set[str] = {
    "ef_dora", "em_alex", "em_santa",
    "af_heart", "af_bella", "af_sarah", "am_adam", "am_michael",
    "bf_emma", "bm_george",
    "jf_alpha", "jm_kumo",
    "zf_xiaoxiao", "zm_yunjian",
}


async def _edge_tts_voice_ids() -> set[str]:
    """Return the set of edge-tts ShortName values available on this
    machine. Used by /api/save-config to warn on typos like
    ``es-MX-Wrongname`` BEFORE the user hits chat and gets a runtime
    error from synthesize(). Returns an empty set if edge-tts isn't
    installed OR if Microsoft's catalog endpoint is unreachable — we
    then skip validation rather than block save.

    The 3-second timeout is critical: ``edge_tts.list_voices()`` is an
    httpx call to a Microsoft endpoint that doesn't enforce its own
    deadline. Under a flaky network this would hang the entire
    `/api/save-config` request, leaving the user stuck on "Guardando…"
    indefinitely.
    """
    try:
        import edge_tts
        all_voices = await asyncio.wait_for(edge_tts.list_voices(), timeout=3.0)
        return {v.get("ShortName", "") for v in all_voices if v.get("ShortName")}
    except asyncio.TimeoutError:
        logger.debug("Edge-TTS voice catalog timed out — skipping validation.")
        return set()
    except Exception:
        return set()


def _local_service_url_error(value: str) -> str:
    """Reject public or credential-bearing URLs for local voice servers."""
    try:
        parsed = urlparse((value or "").strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError
        if parsed.path not in {"", "/"}:
            raise ValueError
        # Accessing .port also validates its range and syntax.
        _ = parsed.port
    except ValueError:
        return "La URL de Fish Speech debe ser una base HTTP(S) local válida, sin credenciales, ruta ni parámetros."

    host = parsed.hostname.lower().rstrip(".")
    if host == "localhost" or host.endswith(".local"):
        return ""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return "Fish Speech debe apuntar a localhost, una IP privada o un host .local."
    if address.is_loopback or address.is_private or address.is_link_local:
        return ""
    return "Fish Speech debe apuntar a una dirección local o privada."


def _tts_config_error(config: IRISConfig) -> tuple[str, str] | None:
    """Return a blocking provider-specific configuration error, if any."""
    if config.tts_provider == "coqui":
        reference = (config.coqui_ref_audio or "").strip()
        if reference and not Path(reference).expanduser().is_file():
            return "missing_voice_reference_file", "La referencia WAV de Coqui ya no existe. Vuelve a cargar o clonar la voz."
    elif config.tts_provider == "elevenlabs":
        if not (config.elevenlabs_api_key or "").strip():
            return "missing_tts_api_key", "ElevenLabs requiere una API key para generar voz."
        if not (config.elevenlabs_voice_id or "").strip():
            return "missing_tts_voice_id", "ElevenLabs requiere seleccionar o clonar una voz antes de guardar."
    elif config.tts_provider == "minimax" and not (config.minimax_api_key or "").strip():
        return "missing_tts_api_key", "MiniMax TTS requiere una API key; el OAuth de Hermes no la sustituye."
    elif config.tts_provider == "fish-audio":
        from core.fish_audio import FishAudioError, validate_model, validate_voice_id

        if not config.fish_audio_api_key.strip():
            return "missing_tts_api_key", "Fish Audio requiere tu API key para generar voz."
        try:
            validate_model(config.fish_audio_model)
            validate_voice_id(config.fish_audio_voice_id)
        except FishAudioError as exc:
            return "invalid_tts_voice", str(exc)
    elif config.tts_provider == "neutts":
        if not ((config.neutts_ref_audio or "").strip() and (config.neutts_ref_text or "").strip()):
            return "missing_voice_reference", "NeuTTS requiere clonar o cargar un WAV de 3-15 s con su transcripción exacta."
        if not Path(config.neutts_ref_audio).expanduser().is_file():
            return "missing_voice_reference_file", "La referencia WAV de NeuTTS ya no existe. Vuelve a cargar o clonar la voz."
    elif config.tts_provider == "hf-s2s":
        model = (config.hf_s2s_model or "").strip()
        if model not in _HF_S2S_QWEN_MODELS:
            return "unsupported_voice_model", "El modelo Qwen3-TTS seleccionado no está validado por IRIS."
        if "Base" in model:
            if not ((config.hf_s2s_ref_audio or "").strip() and (config.hf_s2s_ref_text or "").strip()):
                return (
                    "missing_voice_reference",
                    "Qwen3-TTS Base requiere clonar un WAV de 3-15 s con su transcripción exacta.",
                )
            if not Path(config.hf_s2s_ref_audio).expanduser().is_file():
                return (
                    "missing_voice_reference_file",
                    "La referencia WAV de Qwen3-TTS ya no existe. Vuelve a clonar la voz.",
                )
        elif config.hf_s2s_speaker not in _HF_S2S_QWEN_SPEAKERS:
            return "unsupported_voice_id", "La voz predefinida de Qwen3-TTS no es válida."
    elif config.tts_provider == "fish-speech":
        url_error = _local_service_url_error(config.fish_speech_url)
        if url_error:
            return "invalid_voice_server_url", url_error
        has_reference_id = bool((config.fish_speech_reference_id or "").strip())
        has_inline_reference = bool(
            (config.fish_speech_ref_audio or "").strip()
            and (config.fish_speech_ref_text or "").strip()
        )
        partial_inline_reference = bool(
            (config.fish_speech_ref_audio or "").strip()
            or (config.fish_speech_ref_text or "").strip()
        )
        if has_reference_id and has_inline_reference:
            return "conflicting_voice_reference", "Fish Speech usa un reference_id o audio + transcripción, no ambos."
        if partial_inline_reference and not has_inline_reference and not has_reference_id:
            return "incomplete_voice_reference", "Fish Speech necesita audio y transcripción juntos, o un reference_id."
        if has_inline_reference and not Path(config.fish_speech_ref_audio).expanduser().is_file():
            return "missing_voice_reference_file", "La referencia WAV de Fish Speech ya no existe. Vuelve a cargar o clonar la voz."
    return _voice_hardware_error(config.tts_provider, config.hf_s2s_model)


def _voice_hardware_error(provider: str, model: str = "") -> tuple[str, str] | None:
    """A local voice this PC cannot run (RAM, disk, CUDA), with the reason in plain words."""
    from core.voice_requirements import blocked_reason

    reason = blocked_reason(provider, model if provider == "hf-s2s" else "", get_iris_home())
    return ("hardware_insufficient", reason) if reason else None


_FINAL_SAVE_LOCK = asyncio.Lock()
_FINAL_SAVE_GENERATIONS: dict[str, int] = {}


_VOICE_SELECTION_FIELDS = {
    "edge-tts": {"edge_tts_voice"}, "kokoro": {"kokoro_voice"},
    "elevenlabs": {"elevenlabs_api_key", "elevenlabs_voice_id"},
    "minimax": {"minimax_api_key", "minimax_voice_id", "minimax_tts_model"},
    "fish-audio": {"fish_audio_api_key", "fish_audio_voice_id", "fish_audio_voice_name", "fish_audio_model"},
    "coqui": {"coqui_model", "coqui_ref_audio", "coqui_language"},
    "neutts": {"neutts_model_repo", "neutts_codec_repo", "neutts_ref_audio", "neutts_ref_text"},
    "hf-s2s": {"hf_s2s_model", "hf_s2s_speaker", "hf_s2s_ref_audio", "hf_s2s_ref_text"},
    "fish-speech": {"fish_speech_url", "fish_speech_reference_id", "fish_speech_ref_audio", "fish_speech_ref_text"},
}


@app.post("/api/save-voice-selection")
async def save_voice_selection(request: Request) -> JSONResponse:
    """Commit only this voice card, without saving AI/identity/avatar drafts or synthesizing."""
    _enforce_local_install_request(request, "save-voice", "la voz")
    try:
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 32 * 1024:
                raise ValueError
        from core.google_client_config import unique_object
        values = json.loads(raw, object_pairs_hook=unique_object)
        if not isinstance(values, dict):
            raise ValueError
        provider = values.get("tts_provider")
        if not isinstance(provider, str) or provider not in _VOICE_SELECTION_FIELDS:
            raise ValueError
        allowed = _VOICE_SELECTION_FIELDS[provider] | {"tts_provider", "tts_allow_remote_fallback"}
        if set(values) - allowed or any(isinstance(v, str) and len(v) > 8000 for v in values.values()):
            raise ValueError
        update = ConfigUpdate.model_validate(values)
    except (ValueError, TypeError, UnicodeError):
        # Never echo keys or Pydantic's input values in a credential-bearing request.
        return JSONResponse({"status": "error", "message": "Los ajustes de esta voz no son válidos."}, status_code=422)
    rejected = await _rejected_fish_key(update) if provider == "fish-audio" else ""
    if rejected:
        return JSONResponse({"status": "error", "code": "tts_key_rejected", "message": rejected}, status_code=400,
                            headers={"Cache-Control": "no-store, private"})
    async with _FINAL_SAVE_LOCK:
        with companion_operation_lock():
            candidate = _merge_config_update(update, IRISConfig.load())
            error = _tts_config_error(candidate)
            if error:
                code, message = error
                return JSONResponse({"status": "error", "code": code, "message": message}, status_code=400)
            candidate.save()
    return JSONResponse({"status": "saved", "provider": provider,
                         "message": "Voz guardada. Iris la usará en su próxima respuesta."},
                        headers={"Cache-Control": "no-store, private"})


async def _rejected_fish_key(update: ConfigUpdate) -> str:
    """Why Fish refuses the key about to be saved. Without an answer from Fish, nothing is refused."""
    from core.fish_audio import FishAudioError, FishKeyRejected, check_key

    key = (update.fish_audio_api_key or "").strip() or IRISConfig.load().fish_audio_api_key
    if not key.strip():
        return ""
    try:
        await check_key(key)
    except FishKeyRejected:
        return "Fish Audio rechazó esta clave: no es válida o caducó. Copia una nueva desde tu cuenta de Fish."
    except FishAudioError:
        return ""
    return ""


@app.post("/api/save-config")
async def save_config(config: ConfigUpdate, request: Request) -> JSONResponse:
    """Serialize final wizard commits and reject stale browser generations."""
    session_id = request.headers.get("x-iris-save-session", "").strip()[:96]
    try:
        generation = int(request.headers.get("x-iris-save-generation", "0"))
    except ValueError:
        generation = 0
    if session_id and generation > 0:
        previous = _FINAL_SAVE_GENERATIONS.get(session_id, 0)
        if generation <= previous:
            return JSONResponse(
                {"status": "stale", "message": "Esta configuración fue reemplazada por una edición más reciente."},
                status_code=409,
            )
        _FINAL_SAVE_GENERATIONS[session_id] = generation
        if len(_FINAL_SAVE_GENERATIONS) > 256:
            _FINAL_SAVE_GENERATIONS.pop(next(iter(_FINAL_SAVE_GENERATIONS)))

    async with _FINAL_SAVE_LOCK:
        if session_id and _FINAL_SAVE_GENERATIONS.get(session_id) != generation:
            return JSONResponse(
                {"status": "stale", "message": "Esta configuración fue reemplazada por una edición más reciente."},
                status_code=409,
            )
        return await _save_config_impl(config)


async def _save_config_impl(config: ConfigUpdate) -> JSONResponse:
    """Save the wizard configuration to disk.

    Two-part save:
      1. IRIS' local config at ~/.iris/config.json (user prefs, keys, voice)
      2. Hermes' config at ~/.hermes/.env + ~/.hermes/config.yaml
         (provider + model + api-server settings)

    The Hermes sync is what makes the wizard's provider choice
    actually take effect — without it Hermes would launch with empty
    config.yaml and refuse chat completions.
    """
    config.assistant_name = config.assistant_name.strip()
    if not config.assistant_name:
        return JSONResponse({
            "status": "error",
            "code": "invalid_assistant_name",
            "message": "Escribe un nombre para tu asistente.",
        }, status_code=400)
    config.personality_template = normalize_personality_template(config.personality_template)
    if config.personality_template not in SUPPORTED_PERSONALITY_TEMPLATES:
        return JSONResponse({
            "status": "error",
            "code": "unsupported_personality",
            "message": "La personalidad seleccionada no está soportada.",
        }, status_code=400)
    config.personality_traits = normalize_trait_ids(config.personality_traits)

    # Validate the editable wake name before any part of the final wizard
    # submission writes to disk. It remains a Hermes-only setting and choosing
    # it never grants microphone access.
    if config.hermes_wake_mode == "custom":
        try:
            config.hermes_wake_phrase = normalize_custom_wake_phrase(
                config.hermes_wake_phrase
            )
        except ValueError as exc:
            return JSONResponse({
                "status": "error",
                "code": "invalid_wake_phrase",
                "message": str(exc),
            }, status_code=400)

    supported_tts = {
        "edge-tts", "coqui", "elevenlabs", "minimax",
        "fish-speech", "fish-audio", "kokoro", "neutts", "hf-s2s",
    }
    if config.tts_provider not in supported_tts:
        return JSONResponse({
            "status": "error",
            "code": "unsupported_tts_provider",
            "message": f"El motor de voz '{config.tts_provider}' no está soportado por IRIS.",
        }, status_code=400)

    # Build and validate the non-mutating candidate before save_llm_selection,
    # because that endpoint writes Hermes immediately. A broken voice setup
    # must not leave half of the final wizard submission persisted.
    current_iris_config = IRISConfig.load()
    iris_config = _merge_config_update(config, current_iris_config)
    identity_error = _identity_validation_error(iris_config)
    if identity_error:
        code, message = identity_error
        return JSONResponse({
            "status": "error",
            "code": code,
            "message": message,
        }, status_code=400)
    voice_error = _tts_config_error(iris_config)
    if voice_error:
        code, message = voice_error
        return JSONResponse({"status": "error", "code": code, "message": message}, status_code=400)

    # Validate and persist the provider selection before touching the rest of
    # the wizard-owned settings. This closes the direct/final-save path that
    # used to bypass the key/OAuth/endpoint/local-runtime pre-flight performed
    # by the dedicated provider screen.
    validated_llm: dict[str, Any] | None = None
    if config.hermes_mode == "full" and config.llm_provider:
        selection_response = await save_llm_selection(LLMSelectionUpdate(
            provider=config.llm_provider,
            model=config.llm_model,
            api_key=config.llm_api_key,
            use_oauth=config.llm_use_oauth,
            base_url=config.llm_base_url,
        ))
        if selection_response.status_code >= 400:
            return selection_response
        validated_llm = json.loads(selection_response.body)

    # A wizard pass must not reset settings that this form does not own.
    # This is particularly important for existing installations: hardware,
    # LAN, perception tuning and custom paths are configured elsewhere and
    # are intentionally absent from ConfigUpdate.
    # Core can remain open while the wizard is revisited. Rebase the wizard's
    # owned fields on the latest config under the same interprocess lock used
    # by Companion preferences so neither surface loses the other's changes.
    with interprocess_file_lock(get_iris_home() / "identity.lock"):
        with companion_operation_lock():
            latest_iris_config = IRISConfig.load()
            iris_config = _merge_config_update(config, latest_iris_config)
            from wizard.hermes_config import resolve_local_api_server_url
            iris_config.hermes_url = resolve_local_api_server_url(iris_config.hermes_url)
            record_identity_history(
                latest_iris_config,
                source="wizard-save",
                if_different_from=iris_config,
            )
            iris_config.save()  # llm_* fields are auto-stripped — Hermes owns those

        # Keep config, history and Hermes' SOUL/IDENTITY mirror in the same
        # cross-process identity transaction. A second wizard/runtime cannot
        # interleave another profile while these files are being committed.
        try:
            sync_hermes_identity_from_config(iris_config)
        except Exception as e:
            print(f"Failed to sync identity: {e}")

    # ── Voice-id validation (FIX W3) ────────────────────
    # We DON'T reject — the user can still save and either fix the typo
    # later or have the provider auto-correct. We just surface a clear
    # warning in the response so the wizard UI can render a yellow toast
    # instead of waiting until the first chat turn fails at synthesis.
    warnings: list[str] = []
    try:
        if config.tts_provider == "edge-tts" and config.edge_tts_voice:
            known = await _edge_tts_voice_ids()
            # Only warn if we actually got a catalog back. An empty set
            # means edge-tts isn't installed yet — in which case the
            # voice id can't be checked locally and the warning would
            # be a false positive.
            if known and config.edge_tts_voice not in known:
                warnings.append(
                    f"La voz '{config.edge_tts_voice}' no aparece en el catálogo "
                    "de Edge-TTS. Revisa el id (p. ej. 'es-MX-DaliaNeural'). "
                    "Se guardó igualmente pero la síntesis fallará en chat."
                )
        elif config.tts_provider == "kokoro" and config.kokoro_voice:
            if config.kokoro_voice not in _KOKORO_KNOWN_VOICES:
                warnings.append(
                    f"La voz Kokoro '{config.kokoro_voice}' no está en la lista "
                    f"conocida ({len(_KOKORO_KNOWN_VOICES)} voces). "
                    "Verifica el id o el modelo no podrá cargarla."
                )
    except Exception as _e:
        # Validation is best-effort — never block save on it.
        print(f"[save-config] voice validation skipped: {_e}")

    # Mirror personalization → Hermes' MD files. This is the ONLY thing
    # the wizard should touch in Hermes' workspace (apart from the
    # credentials write below, which only runs in Modo Completo when
    # the user is deliberately picking a new provider).
    # Credential sync (~/.hermes/.env + config.yaml) — ONLY when the user
    # explicitly picked a provider in Modo Completo. In Modo Rápido /
    # Reparar the wizard never sends llm_provider, so we skip this and
    # Hermes' existing credential setup stays intact (the whole point of
    # those modes is "don't touch what's working").
    #
    # The previous behaviour was to call write_hermes_config() whenever
    # llm_provider was non-empty in the payload — but the JS auto-fills
    # the radio to "anthropic" by default, so even a "save other fields"
    # call would overwrite the user's working provider config. Removing
    # this implicit overwrite kills a whole class of data-loss bugs.
    hermes_sync: dict[str, Any] = {"ok": False, "method": "none", "detail": "skipped"}
    explicit_provider_choice = bool(
        config.llm_provider
        and config.hermes_mode == "full"  # gated by Modo Completo only
    )
    if explicit_provider_choice:
        hermes_sync = (validated_llm or {}).get("hermes") or {
            "ok": True,
            "method": "hermes_config",
            "detail": "provider selection validated",
        }
    else:
        hermes_sync["detail"] = (
            "skipped — Modo Rápido / Reparar no modifica las credenciales "
            "de Hermes. Sólo Modo Completo dispara write_hermes_config()."
        )

    # Hermes does not discover project-local ``.hermes/skills`` directories.
    # A successful wizard pass installs IRIS' first-party skill into the active
    # Hermes profile without overwriting a foreign folder with the same name.
    iris_skill_sync: dict[str, Any] = {
        "ok": False,
        "status": "skipped",
        "detail": "Hermes Agent todavía no está disponible.",
    }
    if resolve_hermes_cli():
        try:
            from core.iris_skill_lifecycle import sync_iris_skill_to_hermes

            iris_skill_sync = await asyncio.to_thread(sync_iris_skill_to_hermes)
            if not iris_skill_sync.get("ok"):
                warnings.append(str(
                    iris_skill_sync.get("detail") or "No se instaló la skill iris-os."
                ))
        except Exception as exc:
            message = f"No se pudo instalar la skill iris-os para Hermes: {exc}"
            warnings.append(message)
            iris_skill_sync = {"ok": False, "status": "error", "detail": message}

    # Wake word is an independent native Hermes setting. Phrase selection
    # never enables the microphone: new installs stay OFF until the user
    # explicitly clicks the ear icon. Existing persisted consent is preserved.
    wake_sync: dict[str, Any] = {"ok": False, "detail": "sin cambios"}
    if config.hermes_wake_mode != "keep":
        if not resolve_hermes_cli():
            message = (
                "Hermes Agent todavía no está disponible; no se configuró "
                "la activación por voz. Podrás hacerlo después desde IRIS OS."
            )
            warnings.append(message)
            wake_sync = {"ok": False, "detail": message}
        else:
            try:
                wake_settings = await asyncio.to_thread(
                    configure_hermes_wake,
                    config.hermes_wake_mode,
                    phrase=(
                        config.hermes_wake_phrase
                        if config.hermes_wake_mode == "custom"
                        else None
                    ),
                )
                wake_sync = {
                    "ok": True,
                    "detail": "configurado por Hermes Agent; escucha apagada hasta activación explícita",
                    **wake_settings,
                }
            except Exception as exc:
                message = f"No se pudo configurar la activación nativa de Hermes: {exc}"
                warnings.append(message)
                wake_sync = {"ok": False, "detail": message}

    return JSONResponse({
        "status":   "saved",
        "path":     str(get_iris_home() / "config.json"),
        "hermes":   hermes_sync,
        "iris_skill": iris_skill_sync,
        "hermes_wake": wake_sync,
        "ai":       await _ai_readiness(),
        "warnings": warnings,
    })


async def _ai_readiness() -> dict[str, Any]:
    """Whether the saved setup can talk: a provider that passes the Gateway pre-flight the
    launch will run (``_provider_preflight``: key or login, external program, local server).
    Quick and Repair keep an existing provider without validating it, so this is the only
    check before the summary. Saving preferences without a ready provider is fine, but the
    summary must not call IRIS ready. ``ready`` is None when it cannot tell; the launch then
    decides, as before."""
    from wizard.hermes_config import read_hermes_dotenv

    try:
        cfg = IRISConfig.load()
        if not (cfg.llm_provider or "").strip():
            return {"ready": False, "code": "missing_provider",
                    "message": "Falta elegir y conectar tu proveedor de IA: sin él, IRIS no puede conversar."}
        error = (await _provider_preflight(cfg, await asyncio.to_thread(read_hermes_dotenv)))["error"]
    except Exception:
        logger.exception("Could not check the AI provider after saving")
        return {"ready": None, "code": "unknown", "message": ""}
    if error:
        return {"ready": False, "code": error["code"], "message": error["message"]}
    return {"ready": True, "code": "", "message": ""}


# Restarting Hermes from the messaging section asks the same pre-flight (R02).
set_restart_readiness(_ai_readiness)


class ProviderModelsRequest(BaseModel):
    provider: str = Field(max_length=80)
    api_key: str = Field(default="", repr=False, max_length=4096)
    use_oauth: bool = False
    base_url: str = Field(default="", max_length=2048)


@app.post("/api/provider-models")
async def provider_models(payload: ProviderModelsRequest, request: Request) -> JSONResponse:
    """Explicit metadata query, with a draft key or this Iris profile's signed-in session.

    Does not save the draft, change the principal provider or run inference.
    """
    from wizard.providers import effective_auth_mode_for, env_key_for, get_provider, hermes_id_for
    from wizard.hermes_config import has_hermes_auth_profile, read_hermes_dotenv
    from wizard.provider_models import discover_models

    def answer(body, status=200):
        messages = {
            "missing_api_key": "Pega la API key de este proveedor antes de cargar sus modelos.",
            "missing_oauth": "Conecta tu cuenta antes de cargar sus modelos.",
            "invalid_credentials": "El proveedor rechazó la credencial. Revísala y vuelve a intentar.",
            "plan_unknown": "No se pudo comprobar tu plan. Reintenta para consultar los modelos de tu cuenta.",
            "engine_missing": "Prepara el motor de IRIS para consultar los modelos de tu cuenta.",
        }
        if body.get("status") == "error":
            body["message"] = messages.get(body.get("code"), "No se pudo cargar el catálogo. Revisa el acceso y reintenta.")
            if body.get('code') == 'plan_unknown':
                body['message'] = {
                    'session_unavailable': 'La sesión de Nous no está disponible. Vuelve a conectar la cuenta.',
                    'account_lookup_failed': 'Iniciaste sesión, pero Nous no respondió al consultar el acceso de tu cuenta. Reintenta cargar modelos.',
                    'tls_certificate_untrusted': 'La sesión está iniciada, pero Iris no pudo verificar el certificado HTTPS de Nous. Revisa la fecha del equipo y el diagnóstico de conexión.',
                    'access_unknown': 'Nous no confirmó el acceso de tu cuenta. Reintenta cargar modelos; Iris no supondrá que tienes un plan de pago.',
                    'pricing_unavailable': 'La cuenta está conectada, pero no se pudo comprobar qué modelos son gratuitos. Reintenta cargar modelos.',
                }.get(body.get('reason'), body['message'])
        return JSONResponse(body, status_code=status, headers={"Cache-Control": "no-store, private"})

    if not request.client or not is_loopback_host(request.client.host):
        return answer({"status": "error", "code": "local_only"}, 403)
    provider = payload.provider.strip().lower()
    info = get_provider(provider)
    if not info:
        return answer({"status": "error", "code": "invalid_provider"}, 400)
    auth = effective_auth_mode_for(provider)
    oauth = payload.use_oauth or auth == "oauth"
    if oauth and auth not in {"oauth", "api+oauth"}:
        return answer({"status": "error", "code": "oauth_not_supported"}, 400)
    slug = hermes_id_for(provider, use_oauth=oauth)
    key = ""
    if oauth:
        if not await asyncio.to_thread(has_hermes_auth_profile, slug, auth_type="oauth"):
            return answer({"status": "error", "code": "missing_oauth"}, 400)
    else:
        key = payload.api_key.strip()
        if not key:
            cfg = await asyncio.to_thread(IRISConfig.load)
            if cfg.llm_provider == provider and not cfg.llm_use_oauth:
                key = (cfg.llm_api_key or "").strip()
            env_key = env_key_for(provider)
            if not key and env_key:
                saved = await asyncio.to_thread(read_hermes_dotenv)
                key = (saved.get(env_key) or os.environ.get(env_key) or "").strip()
        if not key and auth in {"api", "api+oauth"}:
            return answer({"status": "error", "code": "missing_api_key"}, 400)
        if key.startswith("oc_sk_") and slug not in {"opencode-zen", "opencode-go"}:
            return answer({"status": "error", "code": "invalid_credentials"}, 400)
    try:
        base = _validated_provider_base_url(payload.base_url) if payload.base_url else ""
    except ValueError:
        return answer({"status": "error", "code": "invalid_base_url"}, 400)
    base = base or {"openrouter": "https://openrouter.ai/api/v1", "opencode-zen": "https://opencode.ai/zen/v1",
                    "opencode-go": "https://opencode.ai/zen/go/v1"}.get(slug, "") or str(info.get("base_url") or "")
    result = await discover_models(slug=slug, api_key=key, use_oauth=oauth, base_url=base)
    result.update(provider=provider, use_oauth=oauth)
    return answer(result)


class LLMSelectionUpdate(BaseModel):
    """Body for /api/save-llm-selection — partial update for the LLM step.

    Why this exists separately from /api/save-config: the full
    ``/api/save-config`` payload requires every wizard field (assistant
    name, TTS provider, channels, …) and overwriting them with defaults
    when the user only changed the LLM provider would silently wipe the
    rest of their configuration.

    This endpoint loads the current IRISConfig, mutates only the four
    LLM-related fields, persists the result, and re-syncs Hermes' own
    config file. It's safe to call multiple times — every field is
    optional (``None`` means "leave unchanged")."""
    provider:  str | None = None
    model:     str | None = None
    api_key:   str | None = None
    use_oauth: bool | None = None
    base_url:  str | None = None


def _plan_locked_model_error(provider: str, model: str, use_oauth: bool) -> dict[str, str] | None:
    """Q04: the last inventory says the account's plan cannot use this model (a free Nous
    account and a paid model). Hermes would only fail later, in the middle of a chat."""
    from wizard.provider_sync import load_snapshot
    from wizard.providers import hermes_id_for

    try:
        catalog, _meta = load_snapshot()
    except Exception:
        return None
    for entry in catalog.get(hermes_id_for(provider, use_oauth=use_oauth)) or []:
        if (isinstance(entry, dict) and entry.get("id") == model
                and entry.get("locked") and entry.get("lock_reason") == "plan"):
            return {
                "status": "error",
                "code": "model_not_in_plan",
                "message": "Tu plan con este proveedor no incluye ese modelo. Elige uno disponible "
                           "o mejora el plan en la web del proveedor.",
            }
    return None


@app.post("/api/save-llm-selection")
async def save_llm_selection(payload: LLMSelectionUpdate) -> JSONResponse:
    """Persist the LLM provider/model/key chosen in the wizard's Step 2.

    Called by the UI right before ``/api/start-gateway`` so the gateway's
    pre-flight reads the *current* selection from disk instead of the
    last full save (which only happens on the final summary step).

    Returns the resolved provider + model + auth_mode so the UI can
    display a confirmation toast like "Guardado: openai / gpt-4o-mini (API)"."""
    from wizard.hermes_config import (
        has_hermes_auth_profile,
        read_hermes_dotenv,
        read_provider_base_url,
        write_hermes_config,
    )
    from wizard.providers import effective_auth_mode_for, env_key_for, get_provider, hermes_id_for

    requested_provider = (payload.provider or "").strip().lower()
    requested_model = (payload.model or "").strip()
    if not requested_provider:
        return JSONResponse(
            {"status": "error", "code": "missing_provider", "message": "Selecciona un proveedor."},
            status_code=400,
        )
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", requested_provider):
        return JSONResponse(
            {"status": "error", "code": "invalid_provider", "message": "El identificador del proveedor no es válido."},
            status_code=400,
        )
    if not requested_model or requested_model == "__custom__":
        return JSONResponse(
            {"status": "error", "code": "missing_model", "message": "Selecciona o escribe un modelo válido."},
            status_code=400,
        )
    requested_auth = effective_auth_mode_for(requested_provider)
    if payload.use_oauth and requested_auth not in {"oauth", "api+oauth"}:
        return JSONResponse(
            {
                "status": "error",
                "code": "oauth_not_supported",
                "message": "Este proveedor no admite OAuth mediante Hermes. Usa una API key.",
            },
            status_code=400,
        )

    cfg = IRISConfig.load()
    selected_oauth = (
        bool(getattr(cfg, "llm_use_oauth", False))
        if payload.use_oauth is None and getattr(cfg, "llm_provider", "") == requested_provider
        else bool(payload.use_oauth)
    )
    use_oauth = requested_auth == "oauth" or selected_oauth
    provider_info = get_provider(requested_provider) or {}
    try:
        if payload.base_url is not None:
            effective_base_url = _validated_provider_base_url(payload.base_url)
        elif getattr(cfg, "llm_provider", "") == requested_provider:
            effective_base_url = _validated_provider_base_url(
                await asyncio.to_thread(read_provider_base_url, requested_provider)
            )
        else:
            effective_base_url = _validated_provider_base_url(
                str(provider_info.get("base_url") or "")
            )
    except ValueError as exc:
        return JSONResponse(
            {"status": "error", "code": "invalid_base_url", "message": str(exc)},
            status_code=400,
        )

    # Validate the credentials for the auth path the user explicitly chose
    # BEFORE writing Hermes. Previously we wrote provider/model first and only
    # start_gateway discovered the missing key/profile afterwards, which could
    # replace a working provider with an unusable one.
    requested_key = (payload.api_key or "").strip() if payload.api_key is not None else ""
    existing_key = ""
    if cfg.llm_provider == requested_provider and not cfg.llm_use_oauth:
        existing_key = (cfg.llm_api_key or "").strip()
    env_key = env_key_for(requested_provider)
    if not requested_key and not existing_key and env_key:
        existing_env = await asyncio.to_thread(read_hermes_dotenv)
        existing_key = (
            existing_env.get(env_key, "")
            or os.environ.get(env_key, "")
            or ""
        ).strip()
    effective_key = requested_key or existing_key

    has_oauth = False
    if use_oauth:
        oauth_candidates = {
            hermes_id_for(requested_provider, use_oauth=True),
            hermes_id_for(requested_provider),
        }
        has_oauth = any(
            candidate and has_hermes_auth_profile(candidate, auth_type="oauth")
            for candidate in oauth_candidates
        )

    if use_oauth and not has_oauth:
        return JSONResponse(
            {
                "status": "error",
                "code": "missing_oauth",
                "message": "Completa la autenticación OAuth antes de guardar e iniciar Hermes.",
            },
            status_code=400,
        )
    if requested_auth in {"api", "api+oauth"} and not use_oauth and not effective_key:
        return JSONResponse(
            {
                "status": "error",
                "code": "missing_api_key",
                "message": "Pega una API key válida antes de guardar e iniciar Hermes.",
                "hint_env": env_key,
            },
            status_code=400,
        )

    if provider_info.get("base_url_required") and not effective_base_url:
        return JSONResponse(
            {
                "status": "error",
                "code": "missing_base_url",
                "message": "Indica el endpoint completo de este proveedor antes de guardar.",
            },
            status_code=400,
        )

    if requested_provider == "nous" and use_oauth:
        # A shared snapshot may describe the previous account/plan. Recheck this
        # signed-in account before either save path writes the selection.
        from wizard.provider_models import discover_models
        current = await discover_models(slug="nous", use_oauth=True)
        if current.get("status") != "ok":
            return JSONResponse({"status": "error", "code": "plan_unknown",
                                 "message": "No se pudo comprobar tu plan de Nous. Reintenta antes de guardar."}, status_code=400)
        plan_error = None if any(m.get("value") == requested_model for m in current.get("models", [])) else {
            "status": "error", "code": "model_not_in_plan",
            "message": "Tu cuenta de Nous no ofrece ese modelo. Actualiza la lista y elige uno disponible.",
        }
    else:
        plan_error = await asyncio.to_thread(
            _plan_locked_model_error, requested_provider, requested_model, use_oauth,
        )
    if plan_error:
        return JSONResponse(plan_error, status_code=400)

    external_error = await _external_provider_preflight(requested_provider)
    if external_error:
        return JSONResponse(external_error, status_code=400)
    local_error = await _local_provider_preflight(
        requested_provider,
        requested_model,
        effective_base_url,
    )
    if local_error:
        return JSONResponse(local_error, status_code=400)

    # These four fields are deliberately excluded from IRISConfig.save();
    # Hermes' own files below are their only persistent source of truth. Keep
    # them on this in-memory candidate without rewriting unrelated IRIS prefs.
    cfg.llm_provider = requested_provider
    cfg.llm_model = requested_model
    cfg.llm_api_key = "" if use_oauth else effective_key
    cfg.llm_use_oauth = use_oauth

    # Re-sync ~/.hermes/.env + config.yaml so Hermes picks up the new
    # provider/model on next gateway boot. This write is the persistence
    # operation: IRISConfig.save() intentionally excludes the llm_* fields,
    # so reporting "saved" after this fails would be false and the gateway
    # would silently keep using the previous provider.
    sync_result: dict[str, Any] = {"ok": False, "method": "skipped", "detail": "no provider"}
    if cfg.llm_provider:
        try:
            summary = await asyncio.to_thread(
                write_hermes_config,
                llm_provider=cfg.llm_provider,
                llm_api_key=cfg.llm_api_key,
                llm_model=cfg.llm_model,
                llm_use_oauth=bool(cfg.llm_use_oauth),
                api_server_key=(cfg.hermes_api_key or "").strip(),
                base_url=effective_base_url,
            )
            sync_result = {
                "ok":     True,
                "method": "hermes_config",
                "detail": f"env: {summary.get('.env')}, config.yaml: {summary.get('config.yaml')}",
                "model":  summary.get("model", ""),
            }
        except Exception as e:
            return JSONResponse(
                {
                    "status": "error",
                    "code": "hermes_config_write_failed",
                    "message": "No se pudo guardar el proveedor en la configuración de Hermes.",
                    "provider": cfg.llm_provider,
                    "model": cfg.llm_model,
                    "hermes": {"ok": False, "method": "error", "detail": str(e)},
                },
                status_code=500,
            )

    return JSONResponse({
        "status":   "saved",
        "provider": cfg.llm_provider,
        "model":    cfg.llm_model,
        "auth":     "oauth" if cfg.llm_use_oauth else effective_auth_mode_for(cfg.llm_provider),
        "base_url": effective_base_url,
        "hermes":   sync_result,
    })


# ── More than one AI provider ─────────────────────────────────────────────
#
# Hermes keeps one main provider (``model`` in config.yaml) and counts every other provider
# with its own credential as configured: a key in its .env or an OAuth login in its auth
# store. Iris' model selector offers those providers' models per conversation, so adding
# one is storing its credential and nothing else.

# Hermes routes these through the main model block (endpoint, local server, cloud SDK chain,
# external program): they can be the main provider, not one more next to it.
_MAIN_ONLY_SLUGS = frozenset({"custom", "lmstudio", "azure-foundry", "bedrock", "copilot-acp"})


def _additional_provider_problem(wizard_id: str, main: str) -> str:
    """Why ``wizard_id`` cannot be added next to ``main`` ("" when it can)."""
    from wizard.providers import effective_auth_mode_for, env_key_for, get_provider, hermes_id_for

    info = get_provider(wizard_id)
    if not info:
        return "Ese proveedor no está en el catálogo de esta versión."
    if main and hermes_id_for(wizard_id) in {hermes_id_for(main), hermes_id_for(main, use_oauth=True)}:
        return "Ese ya es tu proveedor principal."
    auth = effective_auth_mode_for(wizard_id)
    if (hermes_id_for(wizard_id) in _MAIN_ONLY_SLUGS or info.get("base_url_required")
            or auth not in {"api", "api+oauth", "oauth"} or (auth == "api" and not env_key_for(wizard_id))):
        return ("Este proveedor solo puede usarse como principal: elígelo con «Cambiar proveedor».")
    return ""


def _additional_llm_providers(main: str) -> list[dict[str, str]]:
    """Providers other than ``main`` with their own credential in Hermes' profile (no secrets)."""
    from wizard.hermes_config import has_hermes_auth_profile, read_hermes_dotenv
    from wizard.providers import PROVIDERS, effective_auth_mode_for, env_key_for, hermes_id_for

    env = read_hermes_dotenv()
    main_key = env_key_for(main) if main else ""
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for entry in PROVIDERS:
        wizard_id = str(entry["id"])
        slug = hermes_id_for(wizard_id)
        if slug in seen or _additional_provider_problem(wizard_id, main):
            continue
        auth = effective_auth_mode_for(wizard_id)
        key = env_key_for(wizard_id)
        has_key = auth in {"api", "api+oauth"} and bool(key) and key != main_key and bool(env.get(key, "").strip())
        has_oauth = auth in {"oauth", "api+oauth"} and any(
            candidate and has_hermes_auth_profile(candidate, auth_type="oauth")
            for candidate in {hermes_id_for(wizard_id, use_oauth=True), slug})
        if has_key or has_oauth:
            seen.add(slug)
            rows.append({"provider": wizard_id, "name": str(entry.get("name") or wizard_id),
                         "auth": "api" if has_key else "oauth"})
    return rows


@app.get("/api/llm-setup")
async def llm_setup() -> JSONResponse:
    """The provider step: the main provider (the one Hermes runs by default) and whether it
    passes the Gateway's pre-flight, plus the other providers that already have a credential.
    ``addable`` lists the catalog providers that can be added next to the main one."""
    from wizard.providers import PROVIDERS, effective_auth_mode_for, get_provider

    cfg = IRISConfig.load()
    main = (cfg.llm_provider or "").strip()
    readiness = await _ai_readiness()
    primary = None
    if main:
        auth = "oauth" if cfg.llm_use_oauth or effective_auth_mode_for(main) == "oauth" else effective_auth_mode_for(main)
        primary = {"provider": main, "name": (get_provider(main) or {}).get("name") or main,
                   "model": (cfg.llm_model or "").strip(), "auth": auth, "ready": readiness["ready"],
                   "code": readiness["code"], "message": readiness["message"]}
    additional = await asyncio.to_thread(_additional_llm_providers, main)
    addable = [str(p["id"]) for p in PROVIDERS if not _additional_provider_problem(str(p["id"]), main)]
    return JSONResponse({"primary": primary, "additional": additional, "addable": addable},
                        headers={"Cache-Control": "no-store"})


class AdditionalProviderRequest(BaseModel):
    """Body for /api/add-llm-provider: one more provider, never the main one."""
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1, max_length=80)
    api_key: str = Field(default="", max_length=4096)
    use_oauth: bool = False


@app.post("/api/add-llm-provider")
async def add_llm_provider(payload: AdditionalProviderRequest) -> JSONResponse:
    """Add a provider next to the main one: its API key goes to Hermes' .env, or its OAuth
    login (already done through /api/provider-auth) is checked. The main provider, its model
    and its auth choice are not touched. Hermes reads the new credential when it starts."""
    from wizard.hermes_config import ProviderIsMain, has_hermes_auth_profile, write_provider_api_key
    from wizard.providers import effective_auth_mode_for, get_provider, hermes_id_for

    provider = payload.provider.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", provider):
        return JSONResponse({"status": "error", "code": "invalid_provider",
                             "message": "El identificador del proveedor no es válido."}, status_code=400)
    main = (IRISConfig.load().llm_provider or "").strip()
    if not main:
        return JSONResponse({"status": "error", "code": "missing_provider",
                             "message": "Primero guarda tu proveedor principal."}, status_code=400)
    problem = _additional_provider_problem(provider, main)
    if problem:
        return JSONResponse({"status": "error", "code": "not_addable", "message": problem}, status_code=400)
    auth = effective_auth_mode_for(provider)
    name = (get_provider(provider) or {}).get("name") or provider
    use_oauth = auth == "oauth" or (payload.use_oauth and auth == "api+oauth")
    if use_oauth:
        candidates = {hermes_id_for(provider, use_oauth=True), hermes_id_for(provider)}
        if not await asyncio.to_thread(
                lambda: any(c and has_hermes_auth_profile(c, auth_type="oauth") for c in candidates)):
            return JSONResponse({"status": "error", "code": "missing_oauth",
                                 "message": "Completa el inicio de sesión con OAuth antes de agregarlo."},
                                status_code=400)
        return JSONResponse({"status": "saved", "provider": provider, "name": name, "auth": "oauth",
                             "hermes": "unchanged"})
    api_key = payload.api_key.strip()
    if not api_key:
        return JSONResponse({"status": "error", "code": "missing_api_key",
                             "message": "Pega la API key de este proveedor para agregarlo."}, status_code=400)
    try:
        written = await asyncio.to_thread(write_provider_api_key, provider, api_key)
    except ProviderIsMain:
        return JSONResponse({"status": "error", "code": "became_main",
                             "message": "Ese proveedor acaba de guardarse como principal: no se cambió su clave."},
                            status_code=409)
    except ValueError:
        return JSONResponse({"status": "error", "code": "invalid_api_key",
                             "message": "La API key debe ser una sola línea, sin espacios."}, status_code=400)
    return JSONResponse({"status": "saved", "provider": provider, "name": name, "auth": "api", "hermes": written})


@app.post("/api/import-hermes-config")
async def import_hermes_config() -> JSONResponse:
    """Import Hermes' on-disk config into IRIS' ``config.json``.

    Why this exists: the wizard's pre-flight (``/api/start-gateway``)
    decides which provider to validate by reading IRIS' ``config.json``.
    But ``config.json`` can drift from Hermes' ``config.yaml`` + ``.env``
    if the user configured Hermes through another tool, or if a buggy
    wizard run overwrote ``config.json`` with stale defaults (which is
    exactly how we lost a user's openrouter/openai setup once).

    In **Modo Rápido / Modo Reparar** the user's intent is "use what's
    already on disk in Hermes". This endpoint enforces that: it reads
    Hermes' actual config and writes ``config.json`` to match, so the
    pre-flight validates the RIGHT provider against the RIGHT key.

    Reads (never writes) Hermes files:
      * ``~/.hermes/config.yaml`` → ``model.provider`` and
        ``model.default``
      * ``~/.hermes/.env`` → corresponding ``<PROVIDER>_API_KEY``
      * ``~/.hermes/auth.json`` → OAuth credential pool

    Writes only ``~/.iris/config.json``. Idempotent.
    """
    # Delegate the Hermes-config → llm_* derivation to the shared helper
    # in core.config so we don't drift from the same logic IRISConfig.load()
    # runs on every startup.
    from core.config import _derive_llm_from_hermes
    from wizard.providers import effective_auth_mode_for, get_provider

    try:
        derived = await asyncio.to_thread(_derive_llm_from_hermes)
    except Exception as exc:
        return JSONResponse(
            {"status": "error", "message": f"No pude leer la config de Hermes: {exc}"},
            status_code=500,
        )

    wizard_id    = (derived.get("llm_provider") or "").strip()
    hermes_model = (derived.get("llm_model")    or "").strip()
    api_key      = (derived.get("llm_api_key")  or "").strip()
    use_oauth    = bool(derived.get("llm_use_oauth"))

    if not wizard_id:
        return JSONResponse({
            "status": "warning",
            "code":   "no_hermes_config",
            "message": (
                "Hermes no tiene proveedor configurado en config.yaml. "
                "No hay nada que importar — vuelve atrás y usa Modo Completo."
            ),
        })

    # IRISConfig.load() already hydrates these values from Hermes. They are
    # intentionally excluded from config.json, so writing that file here would
    # only risk overwriting unrelated, concurrently edited IRIS preferences.
    cfg = IRISConfig.load()
    prev_provider = cfg.llm_provider
    prev_model    = cfg.llm_model

    auth_mode = effective_auth_mode_for(wizard_id) if wizard_id else "api"
    provider_label = (get_provider(wizard_id) or {}).get("name") or wizard_id

    return JSONResponse({
        "status":   "imported",
        "provider": wizard_id,
        "provider_label": provider_label,
        "model":    hermes_model,
        "auth_mode": auth_mode,
        "has_api_key": bool(api_key),
        "has_oauth":   use_oauth,
        "changed": bool(prev_provider != wizard_id or prev_model != hermes_model),
        "previous": {"provider": prev_provider, "model": prev_model},
    })


# ────────────────────────────────────────────────────────
# Optional extras (A08.5) — approved versions only, each in its own
# isolated environment inside the profile. Nothing is installed into the
# Iris/Hermes runtime: no pip and no ``--upgrade``. Consumers (local TTS)
# run the extra through ``core/extras_worker.py``; see ``core/extras.py``.
#
# Security note: the ``extra`` key is validated against the shipped
# manifest and every download is checked against its exact lock entry.
# ────────────────────────────────────────────────────────

_EXTRA_OPERATIONS: dict[str, threading.Event] = {}
_EXTRA_OPERATION_GUARD = threading.Lock()


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload)}\n\n"


async def _close_preview_engines(provider: str) -> None:
    """Release this wizard's own workers: cached engines and loads in flight.

    The epoch bump retires every load of this provider that already started, so
    none can publish itself after a clone, install or removal; cancelling them
    stops their workers now, and each closes its own engine as it unwinds.
    """
    async with _TTS_PREVIEW_LOCK:
        _TTS_PREVIEW_EPOCH[provider] = _TTS_PREVIEW_EPOCH.get(provider, 0) + 1
        keys = [key for key in _TTS_PREVIEW_CACHE if key[0] == provider]
        engines = [_TTS_PREVIEW_CACHE.pop(key) for key in keys]
        loads = [task for key, task in _TTS_PREVIEW_LOADING.items() if key[0] == provider]
    for task in loads:
        task.cancel()
    if loads:
        # The loads' own outcomes (cancelled, failed) are expected here; only a
        # cancellation of this caller propagates.
        await asyncio.gather(*loads, return_exceptions=True)
    for engine in engines:
        with contextlib.suppress(Exception):
            await engine.close()


@app.get("/api/check-extras")
async def check_extras() -> JSONResponse:
    """Report each admitted extra, its real state and the unsupported ones."""
    from core import extras as extras_manager

    try:
        status = await asyncio.to_thread(extras_manager.extras_status)
    except extras_manager.ExtrasError as exc:
        return JSONResponse({"error": str(exc), "code": exc.code}, status_code=exc.status_code)
    with _EXTRA_OPERATION_GUARD:
        running = set(_EXTRA_OPERATIONS)
    for key, entry in status["extras"].items():
        entry["operation_running"] = key in running
    status["runtime_allowed"] = runtime_policy.runtime_capability_allowed("profile_extras")
    hardware = await asyncio.to_thread(_voice_hardware_status, status["extras"])
    for key, verdict in hardware.items():
        status["extras"][key]["hardware"] = verdict
    return JSONResponse(status, headers={"Cache-Control": "no-store"})


def _voice_hardware_status(extras: dict[str, Any]) -> dict[str, Any]:
    """Whether this PC can run each local voice extra, per model and with reasons.

    Only a committed, current environment saves its download: an install,
    repair or update of any other state extracts a new one.
    """
    from core.voice_requirements import VOICE_NEEDS, current_machine, engine_assessment

    home = get_iris_home()
    machine = current_machine(home)
    return {
        key: engine_assessment(key, machine, home / "extras" / key, env_installed=entry.get("state") == "installed")
        for key, entry in extras.items() if key in VOICE_NEEDS
    }


@app.post("/api/install-extra-stream")
async def install_extra_stream(
    request: Request, extra: str, action: Literal["install", "repair"] = "install",
) -> StreamingResponse:
    """Install or repair one approved extra and stream progress as SSE.

    Events: ``log`` (text/phase), ``progress`` (file/done/total),
    ``done`` (ok/changed) and ``error`` (code/message). A client disconnect or
    ``/api/extras/cancel`` stops the operation before its commit; the previous
    environment and the base runtime remain unchanged.
    """
    runtime_policy.require_runtime_capability("profile_extras")
    _enforce_local_install_request(request, "install-extra", "los componentes opcionales")
    from core import extras as extras_manager

    if action in {"install", "repair"}:
        # No download, install or repair alike, for an engine none of whose models this PC can run.
        verdict = (await asyncio.to_thread(_voice_hardware_status, {extra: {"state": "not_installed"}})).get(extra)
        if verdict and verdict["verdict"] == "blocked":
            message = " ".join(verdict["reasons"])

            async def refused():
                yield _sse({"type": "error", "code": "hardware_insufficient", "message": message})

            return StreamingResponse(refused(), media_type="text/event-stream",
                                     headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    cancel = threading.Event()
    with _EXTRA_OPERATION_GUARD:
        busy = extra in _EXTRA_OPERATIONS
        if not busy:
            _EXTRA_OPERATIONS[extra] = cancel

    async def event_stream():
        if busy:
            yield _sse({"type": "error", "code": "busy", "message": "Ya hay una operación en curso para este extra."})
            return
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

        def progress(event: dict[str, Any]) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, event)

        async def operate() -> dict[str, Any]:
            runtime_policy.require_runtime_capability("profile_extras")
            await _close_preview_engines(extra)
            try:
                return await asyncio.to_thread(
                    extras_manager.install_extra, extra,
                    repair=action == "repair", progress=progress, cancel=cancel,
                )
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        task = asyncio.create_task(operate())
        sent: dict[str, int] = {}
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                if event.get("type") == "progress":
                    # Coarse progress: about one event per 8 MiB and file end.
                    last = sent.get(event["file"], -1)
                    if event["done"] != event["total"] and event["done"] - last < 8 * 1024 * 1024:
                        continue
                    sent[event["file"]] = event["done"]
                yield _sse(event)
            result = await task
            yield _sse({"type": "done", "ok": True, "changed": result.get("changed", False),
                        "verified": result.get("verified", False), "returncode": 0})
        except extras_manager.ExtrasError as exc:
            yield _sse({"type": "error", "code": exc.code, "message": str(exc)})
        except (runtime_policy.RuntimePolicyError, extras_manager.ProfilePathError) as exc:
            yield _sse({"type": "error", "code": "not_allowed", "message": str(exc)})
        except Exception as exc:
            logger.exception("Extra %s operation failed", extra)
            yield _sse({"type": "error", "code": "install_failed", "message": f"{type(exc).__name__}: {exc}"})
        finally:
            # Disconnect or cancellation: stop before commit and wait so the
            # staging directory is gone before the operation is released.
            if not task.done():
                cancel.set()
            with contextlib.suppress(BaseException):
                await asyncio.shield(task)
            with _EXTRA_OPERATION_GUARD:
                if _EXTRA_OPERATIONS.get(extra) is cancel:
                    _EXTRA_OPERATIONS.pop(extra, None)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/extras/cancel")
async def cancel_extra(request: Request, extra: str) -> JSONResponse:
    _enforce_local_install_request(request, "cancel-extra", "los componentes opcionales")
    with _EXTRA_OPERATION_GUARD:
        event = _EXTRA_OPERATIONS.get(extra)
    if event is not None:
        event.set()
    return JSONResponse({"ok": True, "cancelling": event is not None})


class ExtraRemoval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    extra: str = Field(min_length=1, max_length=32)
    purge_models: bool = False


@app.post("/api/extras/remove")
async def remove_extra(request: Request, removal: ExtraRemoval) -> JSONResponse:
    """Remove one extra environment; models stay unless ``purge_models``."""
    runtime_policy.require_runtime_capability("profile_extras")
    _enforce_local_install_request(request, "remove-extra", "los componentes opcionales")
    from core import extras as extras_manager

    with _EXTRA_OPERATION_GUARD:
        if removal.extra in _EXTRA_OPERATIONS:
            return JSONResponse({"ok": False, "code": "busy", "error": "Hay una operación en curso para este extra."},
                                status_code=409)
    await _close_preview_engines(removal.extra)
    try:
        result = await asyncio.to_thread(
            extras_manager.remove_extra, removal.extra, purge_models=removal.purge_models,
        )
    except extras_manager.ExtrasError as exc:
        return JSONResponse({"ok": False, "code": exc.code, "error": str(exc)}, status_code=exc.status_code)
    except extras_manager.ProfilePathError as exc:
        return JSONResponse({"ok": False, "code": "not_allowed", "error": str(exc)}, status_code=409)
    return JSONResponse({"ok": True, **result})


# ────────────────────────────────────────────────────────
# Health-check: end-to-end diagnostics
# ────────────────────────────────────────────────────────

@app.get("/api/health-check-stream")
async def health_check_stream() -> StreamingResponse:
    """Stream each health check as an SSE event as it completes.

    Events:
        data: {"type": "check",   ...HealthResult}   — one per check
        data: {"type": "done",    "ok": bool}         — final signal
        data: {"type": "error",   "message": str}     — fatal error
    """
    from wizard.health_check import iter_checks

    async def event_stream():
        hard_failures = 0
        inactive = 0
        skipped = 0
        try:
            async for result in iter_checks():
                status = str(result.get("status") or "")
                if status == "fail":
                    hard_failures += 1
                elif status == "inactive":
                    inactive += 1
                elif status == "skip":
                    skipped += 1
                payload = json.dumps({"type": "check", **result})
                yield f"data: {payload}\n\n"
            connected = hard_failures == 0 and inactive == 0 and skipped == 0
            done = {
                'type': 'done',
                'ok': connected,
                'ready': hard_failures == 0,
                'failures': hard_failures,
                'inactive': inactive,
                'skipped': skipped,
            }
            yield f"data: {json.dumps(done)}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# This is an API contract, not a denylist: new config fields stay private until
# explicitly reviewed. Keep the setup UI's existing non-credential settings.
_PUBLIC_CONFIG_FIELDS = frozenset({
    "assistant_name", "language", "personality_template", "personality_instructions",
    "personality_traits", "custom_backstory", "include_iris_backstory",
    "legacy_personality_file_enabled", "custom_personality",
    "visual_theme", "visual_theme_selected", "hermes_security_acknowledged",
    "hermes_url", "hermes_model", "use_picoclaw", "picoclaw_binary",
    "llm_provider", "llm_model", "llm_use_oauth", "channels",
    "stt_provider", "whisper_model", "whisper_api_url",
    "tts_provider", "tts_allow_remote_fallback", "edge_tts_voice",
    "coqui_model", "coqui_ref_audio", "coqui_language", "elevenlabs_voice_id",
    "minimax_voice_id", "minimax_tts_model", "fish_audio_voice_id", "fish_audio_voice_name",
    "fish_audio_model",
    "fish_speech_url", "fish_speech_ref_audio", "fish_speech_ref_text",
    "fish_speech_reference_id", "fish_speech_format", "kokoro_voice",
    "neutts_model_repo", "neutts_codec_repo", "neutts_ref_audio",
    "neutts_ref_text", "neutts_device",
    "hf_s2s_model", "hf_s2s_speaker", "hf_s2s_language",
    "hf_s2s_ref_audio", "hf_s2s_ref_text", "hf_s2s_device",
    "websocket_host", "websocket_port", "container_proxy_mode",
    "companion_loopback_port", "companion_enabled", "companion_autostart",
    "companion_always_on_top", "companion_scale", "companion_reduced_motion",
    "companion_show_controls", "companion_locomotion_enabled", "companion_quiet_mode",
    "companion_life",
    "lan_access", "memory_db_path", "avatar_vrm_path",
    "perception_enabled", "perception_camera", "perception_yolo",
    "perception_mediapipe", "perception_fps",
    "vision_assistant_enabled", "vision_assistant_mode",
    "vision_assistant_cooldown_minutes", "vision_assistant_log_retention_days",
    "vision_assistant_paused_until", "vision_assistant_policy",
    "hardware_enabled", "serial_port", "serial_baudrate",
})


@app.get("/api/current-config")
async def current_config() -> JSONResponse:
    """Get the current configuration (safe — no secrets).

    Selects reviewed non-credential settings and enriches the
    payload with **boolean presence flags** so the wizard UI can render
    "✅ Configurada" / "⚠️ No configurada" badges without ever seeing
    the raw secret. Also folds in the resolved auth mode and OAuth
    profile state for the configured LLM provider so the summary screen
    can show "🔐 OAuth" vs "🔑 API Key" based on reality, not on a
    possibly-stale DOM checkbox."""
    import os
    from wizard.providers import effective_auth_mode_for, env_key_for, get_provider, hermes_id_for
    from wizard.hermes_config import (
        azure_foundry_uses_entra,
        has_hermes_auth_profile as _has_hermes_auth_profile,
        read_hermes_dotenv,
        read_provider_base_url,
    )

    # OAuth presence is now probed via Hermes' on-disk credential pool
    # (``<HERMES_HOME>/auth.json``), so the UI's auth-mode badges
    # reflect the truth instead of always showing "no autenticado".
    def has_hermes_auth_profile(p: str) -> bool:
        # Translate wizard id → Hermes slug before probing. Check both
        # the OAuth-flavour slug (e.g. "openai-codex") and the plain
        # hermes_id (e.g. "openai") so the badge lights up regardless
        # of which auth flow the user actually completed.
        if not p:
            return False
        for candidate in {hermes_id_for(p, use_oauth=True), hermes_id_for(p)}:
            if candidate and _has_hermes_auth_profile(candidate, auth_type="oauth"):
                return True
        return False

    config = IRISConfig.load()
    safe = config.model_dump(include=_PUBLIC_CONFIG_FIELDS)
    from wizard.hermes_config import resolve_local_api_server_url
    safe["hermes_url"] = resolve_local_api_server_url(config.hermes_url)

    # Presence flags — let the UI render "configured?" without the value.
    safe["has_llm_api_key"]         = bool((config.llm_api_key or "").strip())
    safe["has_whisper_api_key"]     = bool((config.whisper_api_key or "").strip())
    safe["has_elevenlabs_api_key"]  = bool((config.elevenlabs_api_key or "").strip())
    safe["has_minimax_api_key"]     = bool((config.minimax_api_key or "").strip())
    safe["has_fish_audio_api_key"]  = bool((config.fish_audio_api_key or "").strip())
    try:
        wake_settings = read_hermes_wake_settings()
        safe["hermes_wake_configured"] = wake_settings["configured"]
        safe["hermes_wake_mode"] = wake_settings["mode"]
        safe["hermes_wake_enabled"] = wake_settings["enabled"]
        safe["hermes_wake_phrase"] = wake_settings["phrase"]
    except ValueError:
        # A malformed Hermes YAML is diagnosed in the dedicated settings
        # endpoint. The general config screen must remain usable.
        safe["hermes_wake_configured"] = False
        safe["hermes_wake_mode"] = "keep"
        safe["hermes_wake_enabled"] = False
        safe["hermes_wake_phrase"] = ""

    # LLM-specific enrichment so the summary screen + verify panel can
    # show "🔐 OAuth" vs "🔑 API Key" vs "🏠 Local" vs "❌ falta cred"
    # based on truth instead of DOM state.
    provider = (config.llm_provider or "").strip()
    if provider:
        env_key   = env_key_for(provider)
        hermes_env = read_hermes_dotenv()
        has_env   = bool(env_key) and bool(hermes_env.get(env_key) or os.environ.get(env_key))
        safe["llm_auth_mode"]       = (
            "entra" if provider == "azure-foundry" and azure_foundry_uses_entra()
            else effective_auth_mode_for(provider)
        )
        safe["llm_provider_label"]  = (get_provider(provider) or {}).get("name") or provider
        safe["has_llm_env_var"]     = has_env
        safe["has_llm_oauth"]       = has_hermes_auth_profile(provider)
        try:
            safe["llm_base_url"] = _validated_provider_base_url(
                read_provider_base_url(provider)
            )
        except ValueError:
            # Do not reflect malformed URLs (especially embedded credentials)
            # into browser state; the provider screen will request a clean one.
            safe["llm_base_url"] = ""
    else:
        safe["llm_auth_mode"]       = "none"
        safe["llm_provider_label"]  = ""
        safe["has_llm_env_var"]     = False
        safe["has_llm_oauth"]       = False
        safe["llm_base_url"]        = ""

    return JSONResponse(safe, headers={"Cache-Control": "no-store"})


# ────────────────────────────────────────────────────────
# IRIS Lifecycle — launch / stop / status
# ────────────────────────────────────────────────────────
#
# Three endpoints that let the wizard UI start the main IRIS agent
# without requiring the user to open a terminal. The heavy lifting
# (cross-platform detached spawn, PID file, port probe) lives in
# ``core/process_launcher.py`` so it can be reused by other surfaces
# (e.g. a future system-tray helper) without dragging FastAPI in.

@app.post("/api/launch-IRIS", include_in_schema=False)
@app.post("/api/iris/launch")
async def launch_iris_endpoint() -> JSONResponse:
    """Spawn ``python -m core.agent`` detached.

    Idempotent on the surface: if IRIS is already running we return
    the existing handle with ``status: "already_running"`` instead of
    400-ing, because the most common cause is a double-click on the
    Launch button.

    The personality → Hermes workspace sync runs inside the child's
    ``IRISAgent.__init__`` (see ``core/agent.py``), so launching is
    enough to propagate any wizard changes — no separate "save +
    sync" round-trip needed.
    """
    # Local import: keeps ``wizard.app`` importable on systems that
    # don't have the full IRIS runtime (e.g. someone running just the
    # health-check endpoints).
    from core.process_launcher import is_iris_running, launch_iris

    # The final wizard action promises a connected IRIS, not the intentionally
    # limited offline fallback. Start (or verify) Hermes first; a running Core
    # can reconnect on the next message, so this also repairs the common state
    # where IRIS was opened before its gateway.
    gateway_response = await start_gateway()
    try:
        gateway_payload = json.loads(gateway_response.body)
    except (TypeError, ValueError, UnicodeDecodeError):
        gateway_payload = {}
    gateway_status = str(gateway_payload.get("status") or "").lower()
    if gateway_response.status_code >= 400 or gateway_status not in {
        "ok", "success", "started", "running",
    }:
        message = str(
            gateway_payload.get("message")
            or "Hermes Gateway no quedó disponible; IRIS no se inició en modo sin conexión."
        )
        return JSONResponse({
            "status": "error",
            "code": gateway_payload.get("code") or "gateway_required",
            "message": message,
            "gateway": gateway_payload,
        }, status_code=gateway_response.status_code if gateway_response.status_code >= 400 else 409)

    existing = is_iris_running()
    if existing is not None:
        return JSONResponse({
            "status": "already_running",
            "pid": existing.pid,
            "port": existing.port,
            "log_path": existing.log_path,
            "started_at": existing.started_at,
            "gateway": gateway_status,
        })

    try:
        # Run in a thread so we don't block the event loop on the
        # spawn + readiness probe (up to ~6s on cold boot).
        handle = await asyncio.to_thread(launch_iris)
    except RuntimeError as exc:
        # launch_iris raises RuntimeError for race conditions and
        # boot failures — bubble up as 500 so the UI surfaces the
        # message verbatim.
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return JSONResponse({
        "status": "launched",
        "pid": handle.pid,
        "port": handle.port,
        "log_path": handle.log_path,
        "started_at": handle.started_at,
        "gateway": gateway_status,
    })


@app.post("/api/stop-IRIS", include_in_schema=False)
@app.post("/api/iris/stop")
async def stop_iris_endpoint() -> JSONResponse:
    """Terminate the running IRIS agent.

    Returns ``{ok: true, was_running: bool}``. ``was_running=false``
    when there was nothing to stop — also a success case for an
    idempotent UI button.
    """
    from core.process_launcher import stop_iris

    was_running = await asyncio.to_thread(stop_iris)
    return JSONResponse({"ok": True, "was_running": was_running})


@app.get("/api/IRIS-status", include_in_schema=False)
@app.get("/api/iris/status")
async def iris_status_endpoint() -> JSONResponse:
    """Return current IRIS status for the wizard's status badge.

    The wizard polls this every ~2s. Designed to be cheap: a PID
    liveness check + a single TCP connect against port 8765. No
    process listing, no network IO beyond loopback.
    """
    from core.process_launcher import is_iris_running

    handle = is_iris_running()
    if handle is None:
        return JSONResponse({
            "running": False,
            "pid": None,
            "port_open": False,
            "uptime_s": 0,
        })

    import time as _time
    return JSONResponse({
        "running": True,
        "pid": handle.pid,
        "port": handle.port,
        "port_open": True,  # is_iris_running already verified this
        "log_path": handle.log_path,
        "uptime_s": max(0, int(_time.time() - handle.started_at)),
    })


# ────────────────────────────────────────────────────────
# CLI Entrypoint (python -m wizard.app)
# ────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    import webbrowser
    import threading
    import time
    
    # Abrir el navegador tras esperar 1 segundo a que uvicorn inicie
    def open_browser():
        time.sleep(1.0)
        webbrowser.open("http://127.0.0.1:8000/ui/")
        
    threading.Thread(target=open_browser, daemon=True).start()
    
    print("🧙 Iniciando Setup Wizard de IRIS...")
    # The wizard can install programs and write credentials. LAN pairing is
    # intentionally limited to the finished IRIS Face; setup stays local.
    uvicorn.run("wizard.app:app", host="127.0.0.1", port=8000, reload=False)

