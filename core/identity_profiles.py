"""Portable identity profiles, trait recipes and local revision history.

This module deliberately contains no provider credentials.  Exported profile
documents may contain local asset paths (VRM/reference WAV) so a same-machine
backup can reconnect them, but imports validate those paths before applying
them and never promise to copy the underlying asset.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4


logger = logging.getLogger("IRIS.identity_profiles")


MAX_PERSONALITY_TRAITS = 6
HISTORY_LIMIT = 12


TRAIT_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "id": "warm",
        "label": "Cálida",
        "glyph": "♡",
        "description": "Cercana sin exagerar la emoción.",
        "prompt": "Mantén un trato cálido y humano; reconoce el tono del usuario sin resultar aduladora ni invasiva.",
        "conflicts": [],
    },
    {
        "id": "direct",
        "label": "Directa",
        "glyph": "→",
        "description": "Va al punto con claridad.",
        "prompt": "Prioriza la respuesta útil y ve al punto; evita preámbulos innecesarios sin perder cortesía.",
        "conflicts": [],
    },
    {
        "id": "concise",
        "label": "Concisa",
        "glyph": "—",
        "description": "Respuestas breves por defecto.",
        "prompt": "Responde de forma breve por defecto y amplía solamente cuando la complejidad o el usuario lo requieran.",
        "conflicts": ["detailed"],
    },
    {
        "id": "detailed",
        "label": "Detallista",
        "glyph": "≡",
        "description": "Explica pasos y matices.",
        "prompt": "Explica los pasos, supuestos y matices importantes; estructura la información para que siga siendo fácil de recorrer.",
        "conflicts": ["concise"],
    },
    {
        "id": "technical",
        "label": "Técnica",
        "glyph": "⌘",
        "description": "Precisa y rigurosa con conceptos.",
        "prompt": "Usa terminología técnica precisa cuando aporte valor y tradúcela a lenguaje claro si el usuario no muestra familiaridad.",
        "conflicts": [],
    },
    {
        "id": "creative",
        "label": "Creativa",
        "glyph": "✦",
        "description": "Propone caminos poco obvios.",
        "prompt": "Explora alternativas originales y conexiones poco obvias, pero diferencia con claridad una idea creativa de un hecho verificado.",
        "conflicts": [],
    },
    {
        "id": "curious",
        "label": "Curiosa",
        "glyph": "?",
        "description": "Pregunta cuando cambia el resultado.",
        "prompt": "Haz preguntas breves cuando una respuesta dependa de información que realmente cambie el resultado; no interrogues por rutina.",
        "conflicts": [],
    },
    {
        "id": "humorous",
        "label": "Con humor",
        "glyph": ":)",
        "description": "Humor ligero y contextual.",
        "prompt": "Usa humor ligero y contextual cuando encaje; evítalo en situaciones sensibles, urgentes o de alto riesgo.",
        "conflicts": [],
    },
    {
        "id": "patient",
        "label": "Paciente",
        "glyph": "∞",
        "description": "Acompaña sin juzgar.",
        "prompt": "Explica de nuevo con paciencia y cambia de enfoque si algo no quedó claro; nunca ridiculices una duda.",
        "conflicts": [],
    },
    {
        "id": "proactive",
        "label": "Proactiva",
        "glyph": "+",
        "description": "Anticipa el siguiente paso útil.",
        "prompt": "Anticipa riesgos y siguientes pasos útiles, pero pide permiso antes de acciones externas, costosas o difíciles de revertir.",
        "conflicts": [],
    },
    {
        "id": "formal",
        "label": "Formal",
        "glyph": "◇",
        "description": "Sobria y profesional.",
        "prompt": "Mantén un registro profesional, sobrio y respetuoso; evita muletillas y exceso de familiaridad.",
        "conflicts": ["casual"],
    },
    {
        "id": "casual",
        "label": "Casual",
        "glyph": "~",
        "description": "Natural y relajada.",
        "prompt": "Habla de forma natural y relajada, con vocabulario cotidiano, sin perder precisión ni respeto.",
        "conflicts": ["formal"],
    },
)

_TRAIT_BY_ID = {item["id"]: item for item in TRAIT_CATALOG}


# The document represents the whole visible character profile while excluding
# API keys, tokens and Hermes provider credentials by construction.
PROFILE_FIELDS: tuple[str, ...] = (
    "assistant_name",
    "personality_template",
    "personality_instructions",
    "personality_traits",
    "custom_backstory",
    "include_iris_backstory",
    "visual_theme",
    "visual_theme_selected",
    "tts_provider",
    "tts_allow_remote_fallback",
    "edge_tts_voice",
    "coqui_model",
    "coqui_ref_audio",
    "coqui_language",
    "elevenlabs_voice_id",
    "minimax_voice_id",
    "minimax_tts_model",
    "fish_speech_url",
    "fish_speech_ref_audio",
    "fish_speech_ref_text",
    "fish_speech_reference_id",
    "fish_speech_format",
    "kokoro_voice",
    "neutts_model_repo",
    "neutts_codec_repo",
    "neutts_ref_audio",
    "neutts_ref_text",
    "neutts_device",
    "hf_s2s_model",
    "hf_s2s_speaker",
    "hf_s2s_language",
    "hf_s2s_ref_audio",
    "hf_s2s_ref_text",
    "hf_s2s_device",
    "avatar_vrm_path",
)

_LOCAL_ASSET_FIELDS = {
    "avatar_vrm_path",
    "coqui_ref_audio",
    "fish_speech_ref_audio",
    "neutts_ref_audio",
    "hf_s2s_ref_audio",
}


def normalize_trait_ids(values: Iterable[Any] | None) -> list[str]:
    """Return known, unique trait ids in catalog order with a hard cap."""
    requested = {
        str(value).strip().lower()
        for value in (values or [])
        if str(value).strip().lower() in _TRAIT_BY_ID
    }
    return [
        item["id"] for item in TRAIT_CATALOG
        if item["id"] in requested
    ][:MAX_PERSONALITY_TRAITS]


def selected_trait_details(values: Iterable[Any] | None) -> list[dict[str, Any]]:
    """Return defensive copies suitable for API responses and previews."""
    return [dict(_TRAIT_BY_ID[trait_id]) for trait_id in normalize_trait_ids(values)]


def trait_warnings(values: Iterable[Any] | None) -> list[str]:
    selected = set(normalize_trait_ids(values))
    warnings: list[str] = []
    seen: set[tuple[str, str]] = set()
    for trait_id in selected:
        trait = _TRAIT_BY_ID[trait_id]
        for conflict in trait["conflicts"]:
            if conflict not in selected:
                continue
            pair = tuple(sorted((trait_id, conflict)))
            if pair in seen:
                continue
            seen.add(pair)
            warnings.append(
                f"{_TRAIT_BY_ID[pair[0]]['label']} y {_TRAIT_BY_ID[pair[1]]['label']} "
                "pueden competir entre sí; las instrucciones escritas tendrán prioridad."
            )
    return warnings


def compose_personality_instructions(
    authored: str,
    trait_ids: Iterable[Any] | None,
) -> str:
    """Compile trait recipes and free text into the exact runtime directive."""
    selected = selected_trait_details(trait_ids)
    authored = (authored or "").strip()
    # Preserve the exact legacy/custom text when there are no recipes.  This
    # avoids changing existing prompts merely because the trait feature exists.
    if not selected:
        return authored
    blocks: list[str] = []
    if selected:
        lines = [f"- {item['label']}: {item['prompt']}" for item in selected]
        blocks.append("Rasgos seleccionados por el usuario:\n" + "\n".join(lines))
    if authored:
        blocks.append("Instrucciones escritas por el usuario:\n" + authored)
    return "\n\n".join(blocks)


def profile_from_config(config: Any) -> dict[str, Any]:
    profile = {
        field: getattr(config, field)
        for field in PROFILE_FIELDS
        if hasattr(config, field)
    }
    profile["personality_traits"] = normalize_trait_ids(
        profile.get("personality_traits")
    )
    if (
        profile.get("personality_template") == "custom"
        and not str(profile.get("personality_instructions") or "").strip()
        and not profile["personality_traits"]
    ):
        legacy_text = str(getattr(config, "custom_personality", "") or "").strip()
        if legacy_text:
            profile["personality_instructions"] = legacy_text
    return profile


def export_profile_document(config: Any) -> dict[str, Any]:
    return {
        "format": "iris.identity-profile",
        "version": 1,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "profile": profile_from_config(config),
        "security": {
            "credentials_included": False,
            "local_assets_embedded": False,
        },
        "notes": [
            "Las API keys, tokens y credenciales OAuth nunca se exportan.",
            "Las rutas de VRM y audios se conservan como referencia, pero los archivos no se incluyen.",
        ],
    }


def parse_profile_document(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("El perfil debe ser un objeto JSON.")
    if payload.get("format") == "iris.identity-profile":
        if payload.get("version") != 1:
            raise ValueError("Esta versión del perfil de IRIS no está soportada.")
        payload = payload.get("profile")
    if not isinstance(payload, dict):
        raise ValueError("El documento no contiene una sección profile válida.")
    unknown = set(payload) - set(PROFILE_FIELDS)
    if unknown:
        raise ValueError(
            "El perfil contiene campos no admitidos: " + ", ".join(sorted(unknown))
        )
    return dict(payload)


def apply_profile_to_config(config: Any, payload: Any) -> tuple[Any, list[str]]:
    """Return a validated config copy and portability warnings."""
    from core.config import IRISConfig

    profile = parse_profile_document(payload)
    warnings: list[str] = []
    if "personality_template" in profile:
        from core.personality import (
            SUPPORTED_PERSONALITY_TEMPLATES,
            normalize_personality_template,
        )

        normalized_template = normalize_personality_template(
            str(profile.get("personality_template") or "")
        )
        if normalized_template not in SUPPORTED_PERSONALITY_TEMPLATES:
            raise ValueError("La personalidad del perfil no está soportada.")
        profile["personality_template"] = normalized_template
    if "personality_traits" in profile:
        requested_traits = profile.get("personality_traits")
        if not isinstance(requested_traits, list):
            raise ValueError("personality_traits debe ser una lista.")
        normalized_traits = normalize_trait_ids(requested_traits)
        if len(requested_traits) > len(normalized_traits):
            warnings.append("Se omitieron rasgos desconocidos o repetidos del perfil.")
        profile["personality_traits"] = normalized_traits

    missing_assets: set[str] = set()
    for field in _LOCAL_ASSET_FIELDS:
        raw_path = str(profile.get(field) or "").strip()
        if raw_path and not Path(raw_path).expanduser().is_file():
            missing_assets.add(field)
            profile.pop(field, None)
    if missing_assets:
        warnings.append(
            "Algunos archivos locales del perfil no existen en este equipo y no se aplicaron: "
            + ", ".join(sorted(missing_assets))
            + "."
        )

    voice_provider = str(profile.get("tts_provider") or "")
    unusable_clone = (
        (voice_provider == "neutts" and "neutts_ref_audio" in missing_assets)
        or (
            voice_provider == "hf-s2s"
            and str(profile.get("hf_s2s_model") or "").endswith("-Base")
            and "hf_s2s_ref_audio" in missing_assets
        )
        or (
            voice_provider == "fish-speech"
            and "fish_speech_ref_audio" in missing_assets
            and not str(profile.get("fish_speech_reference_id") or "").strip()
        )
    )
    if unusable_clone:
        profile.pop("tts_provider", None)
        warnings.append(
            "No se cambió el motor de voz porque su muestra clonada no está disponible en este equipo."
        )
    if voice_provider in {"elevenlabs", "minimax"}:
        warnings.append(
            "El perfil no incluye credenciales del proveedor de voz; se conservaron las existentes del equipo."
        )

    merged = {**config.model_dump(), **profile}
    return IRISConfig(**merged).inherit_loaded_snapshot(config), warnings


def profiles_equal(first: Any, second: Any) -> bool:
    return profile_from_config(first) == profile_from_config(second)


def _history_path() -> Path:
    from core.config import get_iris_home
    return get_iris_home() / "identity_history.json"


def _read_history() -> list[dict[str, Any]]:
    path = _history_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict) and isinstance(item.get("profile"), dict)]


def _atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(data, indent=2, ensure_ascii=False)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink(missing_ok=True)


def record_identity_history(
    config: Any,
    *,
    source: str,
    if_different_from: Any | None = None,
) -> dict[str, Any] | None:
    """Store the state *before* a change, deduplicated and bounded."""
    if if_different_from is not None and profiles_equal(config, if_different_from):
        return None
    profile = profile_from_config(config)
    fingerprint = hashlib.sha256(
        json.dumps(profile, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:16]
    history = _read_history()
    if history and history[0].get("fingerprint") == fingerprint:
        return None
    entry = {
        "id": uuid4().hex,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "source": (source or "identity").strip()[:40],
        "fingerprint": fingerprint,
        "summary": {
            "assistant_name": profile.get("assistant_name", "IRIS"),
            "personality_template": profile.get("personality_template", "iris"),
            "traits": profile.get("personality_traits", []),
        },
        "profile": profile,
    }
    try:
        _atomic_write_json(_history_path(), [entry, *history][:HISTORY_LIMIT])
    except OSError as exc:
        # Revision history is a recovery convenience. A read-only home must
        # not block the identity change the user explicitly requested.
        logger.warning("Could not persist identity history: %s", exc)
        return None
    return entry


def list_identity_history() -> list[dict[str, Any]]:
    """Return metadata only; full snapshots stay local until restored."""
    return [
        {
            "id": item.get("id", ""),
            "saved_at": item.get("saved_at", ""),
            "source": item.get("source", "identity"),
            "summary": item.get("summary", {}),
        }
        for item in _read_history()
        if item.get("id")
    ]


def history_profile(entry_id: str) -> dict[str, Any] | None:
    for item in _read_history():
        if item.get("id") == entry_id:
            return dict(item["profile"])
    return None
