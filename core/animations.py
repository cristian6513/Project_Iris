"""
IRIS — Animation Catalog awareness.

The LLM emits ``animacion_virtual`` to pick a VRMA clip on the avatar.
Without telling the model what clips actually exist, it had to guess
from prior turns or invent names that the face layer silently dropped.

This module reads ``face/animations/manifest.json`` (the same file the
upload-vrma endpoint maintains and the face layer's idle system
consumes) and exposes:

* ``get_available_animations()`` — list of ``{"name", "description"}``
  dicts, one per clip the front-end knows how to play.
* ``format_animation_catalog_for_prompt()`` — formatted block to splice
  into the system prompt so the LLM picks valid names.

Caching strategy: compare the manifest content on each catalog read. Atomic
replacements with identical timestamps still invalidate across processes.
Uncommitted version files never become catalog entries through fallback.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from pathlib import Path

from core.animation_store import MANAGED_FILENAME, AnimationStoreError, read_animation_manifest

logger = logging.getLogger("IRIS.animations")


# Resolve the animations directory once. ``core/animations.py`` lives
# inside ``core/`` so ``parent.parent`` is the project root.
_ANIM_DIR = Path(__file__).resolve().parent.parent / "face" / "animations"
_MANIFEST_PATH = _ANIM_DIR / "manifest.json"
_DEFAULT_ANIM_DIR = _ANIM_DIR
_DEFAULT_MANIFEST_PATH = _MANIFEST_PATH


# Cache state — refreshed lazily on content/revision changes across processes.
_cache_token: bytes | None = None
_cache_clips: list[dict[str, str]] = []
_cache_source: tuple[str, ...] | None = None

# Room sequences and poses reserved for the local menu stay out of the agent
# catalog even when an old profile replaces runtime_only.
_LOCAL_ONLY_ANIMATIONS = frozenset({
    "jump",
    "sit", "walk_in_place", "pc_sit_down_v2", "pc_seated_idle_v2",
    "sofa_sit_down", "sofa_seated_idle", "sofa_stand_up", "walk", "walk_2",
    "bed_lie_down", "bed_lying_idle", "bed_get_up",
    "bed_roll_prone", "bed_prone_idle", "bed_roll_supine",
    "bed_sit_down", "bed_seated_idle", "bed_stand_up", "bed_sit_up",
    "book_take", "book_read_idle", "book_return",
    "sofa_read_open", "sofa_read_idle", "sofa_read_close",
    "bed_read_open", "bed_read_idle", "bed_read_close",
    "sofa_tablet_open", "sofa_tablet_idle", "sofa_tablet_close",
    "bed_tablet_open", "bed_tablet_idle", "bed_tablet_close",
    "desk_sit_down", "desk_seated_idle", "desk_stand_up",
    "desk_type_open", "desk_type_idle", "desk_type_close",
    "stand_read_open", "stand_read_idle", "stand_read_close",
    "stand_tablet_open", "stand_tablet_idle", "stand_tablet_close",
    "window_lean_in", "window_look_idle", "window_lean_out",
    "desk_edge_idle",
})


# Hand-written descriptions for the clips we ship. Keyed by clip name.
# Anything missing falls back to a humanised version of the clip name.
_BUILTIN_DESCRIPTIONS: dict[str, str] = {
    "wave": "saludo amistoso",
    "think": "pensativa, mano en el mentón",
    "nod": "asentir",
    "shake": "negar",
    "dance": "bailar (adorable o tipo idol)",
    "hair_touch": "arreglarse el pelo",
    "shy": "timidez o vergüenza, por ejemplo ante un halago",
    "sit": "sentarse",
    "lie": "tumbarse",
    "stretch": "estirarse",
    "look_around": "mirar alrededor",
    "idle": "pose de idle calmada",
    "idle_casual": "idle casual, cuerpo entero",
    "idle_smile": "idle sonriente",
    "idle_love": "idle cariñosa",
    "idle_noble": "idle noble / elegante",
    "idle_weave": "idle conectada / suave",
    "idle_lazy": "descansar, hacer pereza; gesto ocasional",
    "wave_big": "saludo amplio y expresivo",
    "confirm": "confirmar que algo quedó listo",
    "confused": "no entendí, pedir una aclaración",
    "celebrate": "celebrar un logro",
    "angry": "mostrar enojo",
    "surprised": "mostrar sorpresa",
    "attention": "llamar la atención",
    "point": "señalar",
    # Added in November 2026. Several gestures play one of their variants at random.
    "sad": "tristeza o pena; lamentar algo o acompañar una mala noticia",
    "laugh": "reírse de algo gracioso",
    "scared": "susto o miedo",
    "clap": "aplaudir, felicitar",
    "heart": "corazón con las manos: cariño o agradecimiento",
    "apologize": "pedir disculpas con una pequeña reverencia",
    "shrug": "encogerse de hombros: no lo sé",
    "idea": "¡ya sé!, se le ocurre una idea",
    "annoyed": "fastidio: no me molestes, apartar algo con la mano",
    "wave_cute": "saludo adorable",
    "wave_flirty": "saludo coqueto, para una charla juguetona o si te lo piden",
    "dance_sexy": "baile sensual, para una charla juguetona o si te lo piden",
    "idle_yawn": "bostezar, tener sueño",
    "idle_waiting": "esperar con impaciencia, golpeando el pie",
}


# Older personalities and repair fallbacks used Spanish action names before
# the manifest became the canonical catalog.  Keep accepting them at the
# backend boundary, but convert them to the exact clip ids every surface uses.
_LEGACY_ANIMATION_ALIASES: dict[str, str] = {
    "saludar_feliz": "wave",
    "pensar": "think",
    "asentir": "nod",
    "negar": "shake",
    "celebracion": "celebrate",
    "enojo": "angry",
    "sorpresa": "surprised",
    "listo": "confirm",
    "confirmar": "confirm",
    "no_entendi": "confused",
    "señalar": "point",
}

# These names have deliberate bone-only fallbacks in AnimationDirector and
# remain playable even when their optional full-body clip is absent.
_SAFE_PROCEDURAL_ANIMATIONS = frozenset(
    {
        "idle",
        "nod",
        "shake",
        "think",
        "wave",
        "look_around",
        "look_window",
    }
)


def _humanise(name: str) -> str:
    """Fallback description: turn ``idle_casual`` into ``Idle casual``."""
    cleaned = re.sub(r"[_\-]+", " ", name).strip()
    return cleaned[:1].upper() + cleaned[1:] if cleaned else name


def _list_vrma_fallback() -> list[dict[str, str]]:
    """Last-resort: enumerate ``*.vrma`` files when no manifest exists.

    Keeps the LLM informed even if the manifest got deleted or never
    got written (fresh install before any upload).
    """
    try:
        return [
            {"name": p.stem, "description": _humanise(p.stem)}
            for p in sorted(_ANIM_DIR.glob("*.vrma"))
            if not MANAGED_FILENAME.fullmatch(p.name) and p.stem not in _LOCAL_ONLY_ANIMATIONS
        ]
    except OSError:
        return []


def _reload_from_manifest(
    raw: bytes,
    *,
    confirmed_names: set[str] | None = None,
) -> list[dict[str, str]]:
    """Parse ``manifest.json`` into ``[{name, description}, ...]``.

    The manifest stores ``clips: {name: filename}``. Descriptions are
    not present in the canonical schema, so we synthesise them from a
    hand-written table (``_BUILTIN_DESCRIPTIONS``) and fall back to a
    humanised clip name when the clip is user-uploaded.
    """
    try:
        data = json.loads(raw)
    except ValueError as exc:
        logger.warning("manifest.json invalid (%s); falling back", exc)
        return _list_vrma_fallback()

    if not isinstance(data, dict):
        return _list_vrma_fallback()
    clips = data.get("clips") or {}
    if not isinstance(clips, dict):
        return _list_vrma_fallback()

    out: list[dict[str, str]] = []
    runtime_only = data.get("runtime_only", [])
    if not isinstance(runtime_only, list):
        runtime_only = []
    # A gesture's variants play when the gesture is named; the agent sees the gesture only.
    # This does not rely on runtime_only, which an old user manifest may replace.
    variants = data.get("variants")
    variant_only = {
        clip
        for gesture, members in (variants.items() if isinstance(variants, dict) else ())
        if isinstance(members, list)
        for clip in members
        if isinstance(clip, str) and clip != gesture and clip not in variants
    }
    for name, filename in clips.items():
        if (
            not isinstance(name, str)
            or not name
            or name in runtime_only
            or name in variant_only
            or name in _LOCAL_ONLY_ANIMATIONS
        ):
            continue
        present = (
            name in confirmed_names
            if confirmed_names is not None
            else isinstance(filename, str) and (_ANIM_DIR / filename).exists()
        )
        if not isinstance(filename, str) or not present:
            logger.debug("Skipping animation %r: missing file %r", name, filename)
            continue
        desc = _BUILTIN_DESCRIPTIONS.get(name) or _humanise(name)
        out.append({"name": name, "description": desc})
    out.sort(key=lambda d: d["name"])
    return out


def get_available_animations() -> list[dict[str, str]]:
    """Return the catalog, refreshing on content changes even at equal mtime.

    The cache is process-local. Content includes the committed revision; an
    update by another process is visible on the next read. Missing/invalid
    manifests fall back to legacy files only, excluding managed versions.
    Read outages keep the last confirmed catalog and retry on the next call.
    """
    global _cache_token, _cache_clips, _cache_source

    # Synchronous callers on the event loop must never sleep on a busy file.
    # They keep the confirmed cache until their next call; worker threads can retry.
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        timeout = None
    else:
        timeout = 0.0

    legacy_fixture = _ANIM_DIR != _DEFAULT_ANIM_DIR or _MANIFEST_PATH != _DEFAULT_MANIFEST_PATH
    confirmed_names = None
    if legacy_fixture:
        source = (str(_ANIM_DIR.absolute()), str(_MANIFEST_PATH.absolute()))
    else:
        from core.animation_library import get_animation_library

        library = get_animation_library()
        source = (str(library.factory), str(library.user))
    if source != _cache_source:
        _cache_source, _cache_token, _cache_clips = source, None, []
    try:
        if legacy_fixture:
            raw = read_animation_manifest(_MANIFEST_PATH, timeout=timeout)
        else:
            manifest = library.merged_manifest(timeout=timeout)
            raw = json.dumps(manifest, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
            confirmed_names = set(manifest["clips"])
    except FileNotFoundError:
        _cache_clips = _list_vrma_fallback() if legacy_fixture else list(_cache_clips)
        _cache_token = None
        return list(_cache_clips)
    except (OSError, AnimationStoreError) as exc:
        # Retain the last confirmed catalog through a read outage, including
        # after invalidation. Next call retries; no fallback aliases or orphans
        # should replace the committed catalog because a file was briefly busy.
        logger.warning("Animation manifest unavailable; retaining catalog: %s", exc)
        return list(_cache_clips)
    except ValueError as exc:
        logger.warning("Animation manifest invalid; using legacy fallback: %s", exc)
        if not legacy_fixture:
            logger.warning("Merged animation library invalid; retaining catalog: %s", exc)
            return list(_cache_clips)
        _cache_clips = _list_vrma_fallback()
        _cache_token = None
        return list(_cache_clips)
    token = hashlib.sha256(raw).digest()

    if _cache_token is None or token != _cache_token:
        _cache_clips = _reload_from_manifest(raw, confirmed_names=confirmed_names)
        _cache_token = token

    return list(_cache_clips)


def _invalidate_animation_cache() -> None:
    """Force the next ``get_available_animations()`` call to reload.

    Called after successful uploads before synchronizing Hermes.
    """
    global _cache_token
    _cache_token = None


def format_animation_catalog_for_prompt() -> str:
    """Render the catalog as a block ready to splice into the SOUL.

    Format:

        ANIMACIONES VRMA DISPONIBLES:
        - wave: saludo amistoso
        - think: pensativa, mano en el mentón
        ...

        Para reproducir una, emití en tu respuesta JSON:
          "animacion_virtual": "<nombre>"

    Returns an empty string when no clips are available — the caller
    can concatenate unconditionally without producing an orphan
    header.
    """
    clips = get_available_animations()
    if not clips:
        return ""

    lines = ["\n\nANIMACIONES VRMA DISPONIBLES:"]
    for clip in clips:
        lines.append(f"- {clip['name']}: {clip['description']}")
    lines.append("")
    lines.append("Para reproducir una, emití en tu respuesta JSON:")
    lines.append('  "animacion_virtual": "<nombre>"')
    return "\n".join(lines)


def normalize_agent_animation(value: object) -> str:
    """Return a playable catalog name for an agent-provided value.

    Prompt instructions reduce hallucinated animation names, but provider
    responses are still untrusted input.  The renderers intentionally keep
    room actions and UI-triggered poses separate; this validator therefore
    accepts only live manifest clips plus the legacy aliases above.  Unknown,
    empty, or non-string values safely degrade to ``idle``.
    """
    if not isinstance(value, str):
        return "idle"
    requested = value.strip()
    if not requested:
        return "idle"
    canonical = _LEGACY_ANIMATION_ALIASES.get(requested, requested)
    if canonical in _LOCAL_ONLY_ANIMATIONS:
        return "idle"
    if canonical in _SAFE_PROCEDURAL_ANIMATIONS:
        return canonical
    available = {clip["name"] for clip in get_available_animations()}
    if canonical in available:
        return canonical
    logger.warning("Ignoring unknown agent animation %r; using idle", requested)
    return "idle"


__all__ = [
    "get_available_animations",
    "format_animation_catalog_for_prompt",
    "normalize_agent_animation",
    "_invalidate_animation_cache",
]


async def synchronize_animation_upload(personality=None) -> dict:
    """Refresh catalog/SOUL after commit; a sync failure cannot undo the upload."""
    import asyncio

    _invalidate_animation_cache()

    def sync():
        if personality is not None:
            from core.workspace_sync import sync_personality_to_workspace

            return sync_personality_to_workspace(personality)
        from core.config import IRISConfig
        from core.identity_api import sync_hermes_identity_from_config

        return sync_hermes_identity_from_config(IRISConfig.load())

    try:
        result = await asyncio.to_thread(sync)
        if any(value == "error" for value in result.values()):
            raise RuntimeError("Workspace synchronization returned an error")
        status = "synced" if result.get("SOUL.md") in {"changed", "unchanged"} else "skipped"
        return {"sync_status": status, "warnings": []}
    except Exception:
        logger.exception("Animation committed; Hermes synchronization pending")
        return {
            "sync_status": "pending",
            "warnings": ["Animación guardada; la sincronización con Hermes sigue pendiente."],
        }
