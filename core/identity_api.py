"""
IRIS Identity API — shared router for quick identity edits.

The side panel's "🪪 Identidad" section and the setup wizard both need
to patch IRIS's name, personality and avatar without
rewriting the full config. This module exposes those operations as a
FastAPI `APIRouter` so it can be mounted on:

  * `wizard/app.py`                    — port 8000, setup flow
  * `core/websocket_server.py`         — port 8765, runtime face UI

That way the side panel can POST to /api/update-identity at whatever
backend it's talking to without needing a second CORS target.

What lives here:
  - `IdentityUpdate`         — Pydantic partial-patch model.
  - `sync_hermes_identity_from_config()` — resolves the same runtime
                                           personality used by IRIS and
                                           mirrors it into Hermes' workspace.
  - Two HTTP routes:
      POST /api/update-identity
      GET  /api/current-identity
"""

from __future__ import annotations

import json
import re
from typing import Any
from uuid import uuid4

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from core.config import IRISConfig, get_iris_home
from core.file_lock import interprocess_file_lock
from core.identity_profiles import (
    MAX_PERSONALITY_TRAITS,
    TRAIT_CATALOG,
    apply_profile_to_config,
    export_profile_document,
    history_profile,
    list_identity_history,
    normalize_trait_ids,
    profile_from_config,
    record_identity_history,
    selected_trait_details,
    trait_warnings,
)
from core.personality import (
    PersonalityManager,
    SUPPORTED_PERSONALITY_TEMPLATES,
    normalize_personality_template,
)
from core.workspace_sync import sync_personality_to_workspace


router = APIRouter(tags=["identity"])


# ────────────────────────────────────────────────────────
# Models
# ────────────────────────────────────────────────────────

class IdentityUpdate(BaseModel):
    """Partial identity patch.

    Every field is optional so callers send only what the user changed.
    Empty string semantics:
      * assistant_name, personality_template → ignored
        (treated as "leave alone") because an empty identity is useless.
      * custom_personality → saved (empty string clears the backstory).
      * avatar_vrm_path → saved (empty string clears the path: IRIS' own VRM).
    """
    assistant_name: str | None = Field(None, max_length=48)
    personality_template: str | None = Field(None, max_length=32)
    personality_instructions: str | None = Field(None, max_length=4000)
    personality_traits: list[str] | None = Field(None, max_length=MAX_PERSONALITY_TRAITS)
    custom_backstory: str | None = Field(None, max_length=4000)
    include_iris_backstory: bool | None = None
    legacy_personality_file_enabled: bool | None = None
    custom_personality: str | None = Field(None, max_length=4000)  # legacy client
    avatar_vrm_path: str | None = None


class IdentityPreviewRequest(BaseModel):
    """The identity-only slice used by local and Hermes previews."""

    model_config = ConfigDict(extra="forbid")

    assistant_name: str = Field("IRIS", min_length=1, max_length=48)
    personality_template: str = Field("iris", max_length=32)
    personality_instructions: str = Field("", max_length=4000)
    personality_traits: list[str] = Field(default_factory=list, max_length=MAX_PERSONALITY_TRAITS)
    custom_backstory: str = Field("", max_length=4000)
    include_iris_backstory: bool = True

    @field_validator("assistant_name")
    @classmethod
    def _clean_assistant_name(cls, value: str) -> str:
        cleaned = " ".join((value or "").split())
        if not cleaned:
            raise ValueError("Escribe un nombre para tu asistente.")
        return cleaned


class IdentityProfileImport(BaseModel):
    """JSON profile wrapper; strict field validation happens after unwrap."""

    model_config = ConfigDict(extra="forbid")
    document: dict[str, Any]


# ────────────────────────────────────────────────────────
# Routes
# ────────────────────────────────────────────────────────

def sync_hermes_identity_from_config(cfg: IRISConfig) -> dict[str, str]:
    """Build one authoritative persona and mirror it into Hermes' workspace."""
    manager = PersonalityManager(cfg)
    manager.load()
    return sync_personality_to_workspace(manager, cfg)


def _identity_validation_error(cfg: IRISConfig) -> tuple[str, str] | None:
    """Keep wizard, quick panel, import and restore semantics identical."""
    if not " ".join((cfg.assistant_name or "").split()):
        return "invalid_assistant_name", "Escribe un nombre para tu asistente."
    template = normalize_personality_template(cfg.personality_template)
    if template not in SUPPORTED_PERSONALITY_TEMPLATES:
        return (
            "unsupported_personality",
            "La personalidad seleccionada no está soportada.",
        )
    if template == "custom" and not (
        (cfg.personality_instructions or "").strip()
        or normalize_trait_ids(cfg.personality_traits)
        or (cfg.custom_personality or "").strip()
    ):
        return (
            "empty_custom_personality",
            "Elige al menos un rasgo o describe la personalidad de tu personaje.",
        )
    return None


def _preview_config(payload: IdentityPreviewRequest) -> IRISConfig:
    cfg = IRISConfig.load()
    cfg.assistant_name = " ".join(payload.assistant_name.split())
    cfg.personality_template = normalize_personality_template(payload.personality_template)
    cfg.personality_instructions = payload.personality_instructions.strip()
    cfg.personality_traits = normalize_trait_ids(payload.personality_traits)
    cfg.custom_backstory = payload.custom_backstory.strip()
    cfg.include_iris_backstory = payload.include_iris_backstory
    # The preview represents the visible wizard, never a silent legacy file.
    cfg.legacy_personality_file_enabled = False
    cfg.custom_personality = ""
    return cfg


def _identity_preview(cfg: IRISConfig) -> dict[str, Any]:
    manager = PersonalityManager(cfg)
    personality = manager.load()
    soul = manager.get_soul()
    warnings = trait_warnings(cfg.personality_traits)
    if len(soul) > 12000:
        warnings.append(
            "La identidad es extensa y consumirá contexto en cada conversación; considera resumirla."
        )
    return {
        "assistant_name": personality.name,
        "greeting": manager.get_greeting(),
        "soul": soul,
        "backstory": personality.backstory,
        "selected_traits": selected_trait_details(cfg.personality_traits),
        "warnings": warnings,
        "characters": len(soul),
        # Useful orientation, never presented as billing-grade token counting.
        "estimated_tokens": max(1, round(len(soul) / 4)),
        "memory_note": (
            "El backstory define la identidad permanente del personaje. "
            "La memoria conversacional se almacena por separado y no se exporta con este perfil."
        ),
    }


def _apply_identity_to_running_agent(agent: Any, cfg: IRISConfig) -> bool:
    """Swap the live prompt source after a quick-settings identity save."""
    if agent is None:
        return False
    manager = PersonalityManager(cfg)
    manager.load()
    for field_name in (
        "assistant_name",
        "personality_template",
        "personality_instructions",
        "personality_traits",
        "custom_backstory",
        "include_iris_backstory",
        "legacy_personality_file_enabled",
        "custom_personality",
    ):
        setattr(agent.config, field_name, getattr(cfg, field_name))
    agent.personality = manager
    if getattr(agent, "bridge", None) is not None:
        agent.bridge._personality = manager
    return True


async def _notify_avatar_change(request: Request) -> None:
    """Notify connected surfaces after a successful save, without private data."""
    notify = getattr(request.app.state, "notify_avatar_change", None)
    if callable(notify):
        await notify()


@router.post("/api/update-identity")
async def update_identity(patch: IdentityUpdate, request: Request) -> JSONResponse:
    with interprocess_file_lock(get_iris_home() / "identity.lock"):
        return await _update_identity_locked(patch, request)


async def _update_identity_locked(patch: IdentityUpdate, request: Request) -> JSONResponse:
    """Patch the identity slice of ~/.iris/config.json in place.

    Only the fields the caller sent are changed — the rest of the
    config (LLM keys, TTS voice, channels, etc) is left untouched.
    That's what distinguishes this from /api/save-config, which
    expects a full `ConfigUpdate` payload.
    """
    cfg = IRISConfig.load()
    previous_cfg = cfg.model_copy(deep=True)
    changed: dict[str, Any] = {}
    warnings: list[str] = []

    if patch.assistant_name is not None and patch.assistant_name.strip():
        cfg.assistant_name = patch.assistant_name.strip()
        changed["assistant_name"] = cfg.assistant_name
    if patch.personality_template is not None and patch.personality_template.strip():
        normalized_template = normalize_personality_template(patch.personality_template)
        if normalized_template not in SUPPORTED_PERSONALITY_TEMPLATES:
            return JSONResponse(
                {"status": "error", "message": "La personalidad seleccionada no está soportada."},
                status_code=400,
            )
        cfg.personality_template = normalized_template
        changed["personality_template"] = cfg.personality_template
    if patch.personality_instructions is not None:
        cfg.personality_instructions = patch.personality_instructions.strip()
        cfg.custom_personality = ""
        changed["personality_instructions"] = cfg.personality_instructions
    if patch.personality_traits is not None:
        requested_traits = patch.personality_traits
        cfg.personality_traits = normalize_trait_ids(requested_traits)
        if len(requested_traits) != len(cfg.personality_traits):
            warnings.append("Se omitieron rasgos desconocidos o repetidos.")
        changed["personality_traits"] = cfg.personality_traits
    if patch.custom_backstory is not None:
        cfg.custom_backstory = patch.custom_backstory.strip()
        cfg.custom_personality = ""
        changed["custom_backstory"] = cfg.custom_backstory
    if patch.include_iris_backstory is not None:
        cfg.include_iris_backstory = patch.include_iris_backstory
        changed["include_iris_backstory"] = cfg.include_iris_backstory
    if patch.legacy_personality_file_enabled is not None:
        cfg.legacy_personality_file_enabled = patch.legacy_personality_file_enabled
        changed["legacy_personality_file_enabled"] = cfg.legacy_personality_file_enabled
    if patch.custom_personality is not None:
        # Compatibility for a cached/older UI. New clients use the two
        # explicit fields above.
        cfg.custom_personality = patch.custom_personality.strip()
        changed["custom_personality"] = cfg.custom_personality
    if patch.avatar_vrm_path is not None:
        cfg.avatar_vrm_path = patch.avatar_vrm_path
        changed["avatar_vrm_path"] = cfg.avatar_vrm_path

    if not changed:
        return JSONResponse(
            {"status": "noop", "message": "No identity fields supplied."},
            status_code=200,
        )

    personality_fields = {
        "personality_template",
        "personality_instructions",
        "personality_traits",
        "custom_backstory",
        "include_iris_backstory",
        "legacy_personality_file_enabled",
        "custom_personality",
    }
    visible_personality_fields = personality_fields - {"legacy_personality_file_enabled"}
    if (
        visible_personality_fields.intersection(changed)
        and "legacy_personality_file_enabled" not in changed
        and cfg.legacy_personality_file_enabled
    ):
        cfg.legacy_personality_file_enabled = False
        changed["legacy_personality_file_enabled"] = False
    if personality_fields.intersection(changed):
        validation_error = _identity_validation_error(cfg)
        if validation_error:
            code, message = validation_error
            return JSONResponse(
                {"status": "error", "code": code, "message": message},
                status_code=400,
            )

    record_identity_history(
        previous_cfg,
        source="quick-settings",
        if_different_from=cfg,
    )
    cfg.save()
    await _notify_avatar_change(request)

    try:
        sync_hermes_identity_from_config(cfg)
    except Exception as e:
        warnings.append(f"Hermes identity sync failed: {e}")

    live_applied = _apply_identity_to_running_agent(
        getattr(request.app.state, "agent", None),
        cfg,
    )

    return JSONResponse({
        "status": "saved",
        "changed": changed,
        "warnings": warnings,
        "live_applied": live_applied,
        "path": str(get_iris_home() / "config.json"),
    })


@router.get("/api/current-identity")
async def current_identity() -> JSONResponse:
    """Return just the identity slice so the side panel can preload
    its inputs on mount without parsing the full config payload.
    """
    cfg = IRISConfig.load()
    return JSONResponse({
        "assistant_name": cfg.assistant_name,
        "personality_template": cfg.personality_template,
        "personality_instructions": cfg.personality_instructions,
        "personality_traits": cfg.personality_traits,
        "custom_backstory": cfg.custom_backstory,
        "include_iris_backstory": cfg.include_iris_backstory,
        "legacy_personality_file_enabled": cfg.legacy_personality_file_enabled,
        "custom_personality": cfg.custom_personality,
        "avatar_vrm_path": cfg.avatar_vrm_path,
        "avatar_vrm_configured": bool(str(cfg.avatar_vrm_path or "").strip()),
        "language": cfg.language,
    })


@router.get("/api/identity-options")
async def identity_options() -> JSONResponse:
    """Return the authoritative trait recipes and migration state."""
    cfg = IRISConfig.load()
    legacy_path = get_iris_home() / "personality.json"
    iris_personality = PersonalityManager(
        IRISConfig(
            **{
                **cfg.model_dump(),
                "assistant_name": cfg.assistant_name or "IRIS",
                "personality_template": "iris",
                "personality_instructions": "",
                "personality_traits": [],
                "include_iris_backstory": True,
                "legacy_personality_file_enabled": False,
            }
        )
    ).load()
    return JSONResponse({
        "traits": [dict(item) for item in TRAIT_CATALOG],
        "max_traits": MAX_PERSONALITY_TRAITS,
        "iris_backstory": iris_personality.backstory.replace(
            "{name}", iris_personality.name
        ),
        "legacy_file": {
            "detected": legacy_path.is_file(),
            "active": cfg.legacy_personality_file_enabled,
            "path": str(legacy_path) if legacy_path.is_file() else "",
        },
        "memory_note": (
            "El backstory pertenece a la identidad. La memoria de conversaciones "
            "se guarda por separado y no se incluye al exportar un perfil."
        ),
    })


@router.post("/api/identity-preview")
async def preview_identity(payload: IdentityPreviewRequest) -> JSONResponse:
    """Build the exact local SOUL prompt without contacting a provider."""
    cfg = _preview_config(payload)
    validation_error = _identity_validation_error(cfg)
    if validation_error:
        code, message = validation_error
        return JSONResponse(
            {"status": "error", "code": code, "message": message},
            status_code=400,
        )
    return JSONResponse({"status": "ready", **_identity_preview(cfg)})


@router.post("/api/identity-preview/sample")
async def sample_identity_with_hermes(payload: IdentityPreviewRequest) -> JSONResponse:
    """Generate one explicitly requested, low-token sample through Hermes."""
    cfg = _preview_config(payload)
    validation_error = _identity_validation_error(cfg)
    if validation_error:
        code, message = validation_error
        return JSONResponse(
            {"status": "error", "code": code, "message": message},
            status_code=400,
        )
    preview = _identity_preview(cfg)
    try:
        from wizard.hermes_config import (
            resolve_api_server_key,
            resolve_local_api_server_url,
        )

        token = resolve_api_server_key(cfg.hermes_api_key, cfg.hermes_url)
        base_url = resolve_local_api_server_url(cfg.hermes_url)
        headers = {
            # A fresh preview must not inherit a previous sample's conversation
            # or make an old personality appear to still be active.
            "X-Hermes-Session-Id": f"iris-identity-preview-{uuid4().hex}",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        body = {
            "model": cfg.hermes_model,
            "messages": [
                {
                    "role": "system",
                    "content": preview["soul"]
                    + "\n\nPara esta prueba no uses herramientas ni memoria; responde solamente a la petición.",
                },
                {
                    "role": "user",
                    "content": "Preséntate en una o dos frases y explica cómo me ayudarías.",
                },
            ],
            "temperature": 0.7,
            "max_tokens": 180,
            "stream": False,
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{base_url}/v1/chat/completions",
                json=body,
                headers=headers,
            )
        if response.status_code != 200:
            return JSONResponse(
                {
                    "status": "error",
                    "message": "Hermes no pudo generar la muestra.",
                    "detail": response.text[:300],
                },
                status_code=502,
            )
        content = (
            response.json().get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        spoken = content
        cleaned = (content or "").strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
        try:
            parsed = json.loads(cleaned)
            if isinstance(parsed, dict) and parsed.get("texto_hablado"):
                spoken = str(parsed["texto_hablado"])
        except json.JSONDecodeError:
            pass
        return JSONResponse({
            "status": "generated",
            "sample": (spoken or "").strip(),
            "provider_request": True,
        })
    except Exception as exc:
        return JSONResponse(
            {
                "status": "unavailable",
                "message": "Hermes no está disponible para generar la muestra.",
                "detail": str(exc),
            },
            status_code=503,
        )


def _profile_export_response(cfg: IRISConfig) -> JSONResponse:
    safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "-", cfg.assistant_name).strip("-") or "IRIS"
    return JSONResponse(
        export_profile_document(cfg),
        headers={
            "Content-Disposition": f'attachment; filename="{safe_name}-identity.json"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/api/identity-profile/export")
async def export_identity_profile() -> JSONResponse:
    """Export the currently persisted profile for non-wizard clients."""
    return _profile_export_response(IRISConfig.load())


@router.post("/api/identity-profile/export")
async def export_identity_profile_draft(payload: IdentityPreviewRequest) -> JSONResponse:
    """Export the visible wizard draft plus saved voice/avatar/theme fields."""
    cfg = _preview_config(payload)
    validation_error = _identity_validation_error(cfg)
    if validation_error:
        code, message = validation_error
        return JSONResponse(
            {"status": "error", "code": code, "message": message},
            status_code=400,
        )
    return _profile_export_response(cfg)


@router.post("/api/identity-profile/import")
async def import_identity_profile(
    payload: IdentityProfileImport,
    request: Request,
) -> JSONResponse:
    with interprocess_file_lock(get_iris_home() / "identity.lock"):
        return await _import_identity_profile_locked(payload, request)


async def _import_identity_profile_locked(
    payload: IdentityProfileImport,
    request: Request,
) -> JSONResponse:
    current = IRISConfig.load()
    try:
        candidate, warnings = apply_profile_to_config(current, payload.document)
    except (ValueError, ValidationError) as exc:
        return JSONResponse(
            {"status": "error", "message": str(exc)},
            status_code=400,
        )
    candidate.assistant_name = " ".join(candidate.assistant_name.split())
    candidate.personality_template = normalize_personality_template(
        candidate.personality_template
    )
    validation_error = _identity_validation_error(candidate)
    if validation_error:
        code, message = validation_error
        return JSONResponse(
            {"status": "error", "code": code, "message": message},
            status_code=400,
        )
    record_identity_history(current, source="profile-import", if_different_from=candidate)
    candidate.legacy_personality_file_enabled = False
    candidate.custom_personality = ""
    candidate.save()
    await _notify_avatar_change(request)
    try:
        sync_hermes_identity_from_config(candidate)
    except Exception as exc:
        warnings.append(f"No se pudo sincronizar la identidad con Hermes: {exc}")
    live_applied = _apply_identity_to_running_agent(
        getattr(request.app.state, "agent", None), candidate
    )
    return JSONResponse({
        "status": "imported",
        "profile": profile_from_config(candidate),
        "warnings": warnings,
        "live_applied": live_applied,
    })


@router.get("/api/identity-history")
async def identity_history() -> JSONResponse:
    return JSONResponse({"history": list_identity_history(), "limit": 12})


@router.post("/api/identity-history/{entry_id}/restore")
async def restore_identity_history(entry_id: str, request: Request) -> JSONResponse:
    with interprocess_file_lock(get_iris_home() / "identity.lock"):
        return await _restore_identity_history_locked(entry_id, request)


async def _restore_identity_history_locked(entry_id: str, request: Request) -> JSONResponse:
    profile = history_profile(entry_id)
    if profile is None:
        return JSONResponse(
            {"status": "error", "message": "La versión de identidad ya no existe."},
            status_code=404,
        )
    current = IRISConfig.load()
    try:
        candidate, warnings = apply_profile_to_config(current, profile)
    except (ValueError, ValidationError) as exc:
        return JSONResponse({"status": "error", "message": str(exc)}, status_code=400)
    candidate.assistant_name = " ".join(candidate.assistant_name.split())
    candidate.personality_template = normalize_personality_template(
        candidate.personality_template
    )
    validation_error = _identity_validation_error(candidate)
    if validation_error:
        code, message = validation_error
        return JSONResponse(
            {"status": "error", "code": code, "message": message},
            status_code=400,
        )
    record_identity_history(current, source="before-restore", if_different_from=candidate)
    candidate.legacy_personality_file_enabled = False
    candidate.save()
    await _notify_avatar_change(request)
    try:
        sync_hermes_identity_from_config(candidate)
    except Exception as exc:
        warnings.append(f"No se pudo sincronizar la identidad con Hermes: {exc}")
    live_applied = _apply_identity_to_running_agent(
        getattr(request.app.state, "agent", None), candidate
    )
    return JSONResponse({
        "status": "restored",
        "profile": profile_from_config(candidate),
        "warnings": warnings,
        "live_applied": live_applied,
    })


@router.post("/api/identity-legacy/import")
async def import_legacy_personality(request: Request) -> JSONResponse:
    with interprocess_file_lock(get_iris_home() / "identity.lock"):
        return await _import_legacy_personality_locked(request)


async def _import_legacy_personality_locked(request: Request) -> JSONResponse:
    """Make a formerly silent personality.json override visible and explicit."""
    path = get_iris_home() / "personality.json"
    if not path.is_file():
        return JSONResponse(
            {"status": "error", "message": "No se encontró personality.json."},
            status_code=404,
        )
    try:
        if path.stat().st_size > 1_000_000:
            raise ValueError("El archivo de personalidad es demasiado grande.")
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("personality.json no contiene un objeto válido.")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return JSONResponse({"status": "error", "message": str(exc)}, status_code=400)

    current = IRISConfig.load()
    candidate = current.model_copy(deep=True)
    warnings: list[str] = []
    legacy_traits = data.get("traits") if isinstance(data.get("traits"), list) else []
    authored = str(data.get("personality_instructions") or "").strip()
    if not authored and legacy_traits:
        authored = "\n".join(f"- {str(item).strip()}" for item in legacy_traits if str(item).strip())
    legacy_name = " ".join(str(data.get("name") or "").split())[:48]
    if legacy_name:
        candidate.assistant_name = legacy_name
    else:
        warnings.append(
            "El archivo antiguo no tenía un nombre válido; se conservó el nombre actual."
        )
    candidate.personality_template = "custom"
    candidate.personality_instructions = authored or "Amigable, útil y clara."
    candidate.personality_traits = []
    candidate.custom_backstory = str(data.get("backstory") or "").strip()[:4000]
    candidate.legacy_personality_file_enabled = False
    candidate.custom_personality = ""
    validation_error = _identity_validation_error(candidate)
    if validation_error:
        code, message = validation_error
        return JSONResponse(
            {"status": "error", "code": code, "message": message},
            status_code=400,
        )
    record_identity_history(current, source="legacy-import", if_different_from=candidate)
    candidate.save()
    await _notify_avatar_change(request)
    try:
        sync_hermes_identity_from_config(candidate)
    except Exception as exc:
        warnings.append(f"No se pudo sincronizar la identidad con Hermes: {exc}")
    live_applied = _apply_identity_to_running_agent(
        getattr(request.app.state, "agent", None), candidate
    )
    return JSONResponse({
        "status": "imported",
        "profile": profile_from_config(candidate),
        "warnings": warnings,
        "live_applied": live_applied,
    })


