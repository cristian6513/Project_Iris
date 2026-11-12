"""
Skills Bridge — read user-enabled Hermes skills and turn them into
a system-prompt block.

The face UI lets the user check which Hermes skills IRIS should be
aware of (Fase 2.A). That selection is persisted in
``~/.iris/enabled_skills.json``. At every chat turn the bridge calls
``get_enabled_skills_block()`` and appends the returned markdown to
the system prompt — so the LLM knows which capabilities exist and can
mention them naturally ("puedo enviarte una nota a Apple Notes…")
instead of denying it has the tool.

Why a separate module:

  • The endpoint that *toggles* the flag lives in ``websocket_server``;
    the *consumer* that reads the catalogue lives in ``claw_bridge``.
    Both end up needing the same "load names + look up descriptions"
    logic; pulling it into one file avoids divergence (and a circular
    import between ``websocket_server`` and ``claw_bridge``).

  • Calling the Hermes CLI from inside ``send_message`` would slow
    every turn by ~200 ms. Instead we cache the catalogue for 60 s —
    skills don't change between user breaths, and the user explicitly
    refreshes when they open the panel.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import re
import time
from pathlib import Path
from typing import Any

from core.hermes_runtime import resolve_hermes_cli
from core.workspace_sync import get_hermes_home

logger = logging.getLogger("IRIS.skills_bridge")


# ════════════════════════════════════════════════════════════
# Paths
# ════════════════════════════════════════════════════════════

def _enabled_skills_path() -> Path:
    from core.config import get_iris_home
    return get_iris_home() / "enabled_skills.json"


def _read_enabled_names() -> set[str]:
    p = _enabled_skills_path()
    if not p.exists():
        return set()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return {str(x) for x in data}
    except (OSError, ValueError) as exc:
        logger.debug("Could not read enabled skills: %s", exc)
    return set()


# ════════════════════════════════════════════════════════════
# Catalogue cache
# ════════════════════════════════════════════════════════════

_CACHE: dict[str, Any] = {"ts": 0.0, "by_name": {}}
_CACHE_TTL_S = 60.0


def _resolve_hermes_cli() -> str | None:
    """Return the Hermes CLI path when it is discoverable.

    Kept as a compatibility wrapper for callers that imported the old helper.
    """
    return resolve_hermes_cli()


def _parse_frontmatter(text: str) -> dict[str, Any]:
    """Parse the small YAML front matter block from a Hermes SKILL.md."""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    raw = text[3:end].strip()
    try:
        import yaml  # type: ignore
        data = yaml.safe_load(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        # Conservative fallback: enough for name/description/category.
        out: dict[str, Any] = {}
        for line in raw.splitlines():
            m = re.match(r"^\s*([A-Za-z0-9_-]+)\s*:\s*(.*?)\s*$", line)
            if not m:
                continue
            key, value = m.group(1), m.group(2).strip()
            if (
                len(value) >= 2
                and value[0] == value[-1]
                and value[0] in {"'", '"'}
            ):
                value = value[1:-1]
            out[key] = value
        return out


def _platform_eligible(platforms: Any) -> bool:
    if not platforms:
        return True
    if isinstance(platforms, str):
        values = re.findall(r"[A-Za-z0-9_-]+", platforms.lower())
    elif isinstance(platforms, list):
        values = [str(x).lower() for x in platforms]
    else:
        return True
    if "all" in values or "any" in values:
        return True
    current = "windows" if os.name == "nt" else "linux"
    if os.name != "nt" and platform.system().lower() == "darwin":
        current = "macos"
    return current in values


def _load_catalogue_from_filesystem() -> dict[str, dict[str, Any]]:
    """Read installed Hermes skills directly from ``SKILL.md`` files."""
    skills_root = get_hermes_home() / "skills"
    if not skills_root.exists():
        return {}

    by_name: dict[str, dict[str, Any]] = {}
    for skill_md in skills_root.rglob("SKILL.md"):
        try:
            rel = skill_md.relative_to(skills_root)
            category = rel.parts[0] if len(rel.parts) > 1 else ""
            text = skill_md.read_text(encoding="utf-8", errors="replace")
            meta = _parse_frontmatter(text)
            name = str(meta.get("name") or skill_md.parent.name).strip()
            if not name:
                continue
            hermes_meta = meta.get("metadata", {})
            if isinstance(hermes_meta, dict):
                hermes_meta = hermes_meta.get("hermes", {})
            else:
                hermes_meta = {}
            if isinstance(hermes_meta, dict):
                category = str(hermes_meta.get("category") or category)
            by_name[name] = {
                "name": name,
                "emoji": str(meta.get("emoji") or ""),
                "description": str(meta.get("description") or "").strip(),
                "eligible": _platform_eligible(meta.get("platforms")),
                "disabled": False,
                "source": str(meta.get("source") or "builtin"),
                "category": category,
                "homepage": str(meta.get("homepage") or ""),
                "path": str(skill_md),
            }
        except OSError as exc:
            logger.debug("Could not read Hermes skill %s: %s", skill_md, exc)
    return by_name


def _load_catalogue() -> dict[str, dict[str, Any]]:
    """Load installed Hermes skills and index them by skill name.

    Hermes' CLI currently lists skills as a terminal table in some versions,
    so the stable source of truth is the installed ``SKILL.md`` catalogue
    under Hermes home.
    """
    now = time.time()
    if _CACHE["by_name"] and (now - _CACHE["ts"]) < _CACHE_TTL_S:
        return _CACHE["by_name"]
    by_name = _load_catalogue_from_filesystem()
    if by_name:
        _CACHE["by_name"] = by_name
        _CACHE["ts"] = now
        return by_name

    logger.warning("Skills catalogue unavailable at %s", get_hermes_home() / "skills")
    return _CACHE["by_name"]


def list_installed_skills(*, refresh: bool = False) -> list[dict[str, Any]]:
    """Return installed skills in UI-friendly shape."""
    if refresh:
        invalidate_cache()
    enabled = _read_enabled_names()
    skills = []
    for name, meta in sorted(_load_catalogue().items()):
        row = dict(meta)
        row["enabled"] = name in enabled
        skills.append(row)
    return skills


def get_enabled_skills_block() -> str:
    """Return a markdown block describing the enabled skills.

    Returns an empty string when no skills are enabled (or when the
    catalogue is unavailable) so callers can ``+=`` it unconditionally
    without worrying about extra whitespace.
    """
    enabled = _read_enabled_names()
    if not enabled:
        return ""
    catalogue = _load_catalogue()
    lines: list[str] = []
    for name in sorted(enabled):
        meta = catalogue.get(name)
        if not meta:
            # Enabled but missing from catalogue (skill uninstalled?).
            # Skip silently — the LLM doesn't need to know about
            # broken entries.
            continue
        emoji = meta.get("emoji", "").strip()
        desc = (meta.get("description") or "").strip()
        prefix = f"{emoji} " if emoji else ""
        # First sentence is enough — full descriptions can run paragraphs.
        first_sentence = desc.split(". ")[0].rstrip(".")
        if first_sentence:
            lines.append(f"- {prefix}**{name}** — {first_sentence}.")
        else:
            lines.append(f"- {prefix}**{name}**")

    if not lines:
        return ""

    return (
        "\n\n## Skills disponibles (vía Hermes)\n"
        "Puedes mencionar estas capacidades naturalmente cuando el "
        "usuario pida algo que encaje. Si la skill aplica, deja que Hermes "
        "resuelva la herramienta o subrutina dentro de su propio runtime; "
        "IRIS no debe emitir campos `delegate` en su JSON visible.\n"
        + "\n".join(lines)
    )


def invalidate_cache() -> None:
    """Force the next ``get_enabled_skills_block`` to refetch.

    Called from the toggle endpoint so a UI checkbox click takes effect
    immediately rather than within the 60 s TTL window.
    """
    _CACHE["ts"] = 0.0


__all__ = [
    "get_enabled_skills_block",
    "invalidate_cache",
    "list_installed_skills",
    "_resolve_hermes_cli",
]

