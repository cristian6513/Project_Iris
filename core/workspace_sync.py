"""
Hermes Workspace Sync — Project IRIS

Writes IRIS's personality into Hermes' workspace .md files so Hermes
itself uses the same identity IRIS does. This matters for two reasons:

  1. **Consistency across channels.** Hermes injects ``SOUL.md``,
     ``IDENTITY.md`` and ``USER.md`` into its own startup context.
     Without this sync, anyone who talks to Hermes outside IRIS
     (Telegram, Discord, the Hermes CLI itself) gets the generic
     Hermes persona instead of the configured assistant.

  2. **Token budget.** Hermes already reads ``SOUL.md`` for chat
     completions, so IRIS can stop sending ``personality.get_soul()``
     in every request once we confirm token reduction. That's
     ~800 tokens off the system message on each turn. We confirm
     empirically before flipping that switch in ``hermes_bridge.py``.

Design rules (set with the user, not negotiable):

  • **Idempotent** — read-before-write. The function returns a status
    dict per file (``changed`` / ``unchanged`` / ``skipped`` /
    ``deleted``) so the caller can log diffs without spamming on
    every boot.

  • **Empty = empty** — if the user left a field blank in the wizard
    (most relevant: ``backstory``), the corresponding section is
    omitted entirely. We do **not** invent biography to fill space.
    The whole point of letting users blank a field is so the avatar
    doesn't pretend to be someone they're not.

  • **USER.md is sacred** — the user may have hand-edited it (e.g.
    with their preferred name). Never overwrite if it already has content; only
    create when missing.

  • **Migration safety** — Hermes ships its own SOUL.md template on
    install. We overwrite it on first sync because IRIS's SOUL is
    what the user actually configured.

The sync is called from ``IRISAgent.__init__`` so any change to
personality (preset switch, custom name in wizard) propagates on next
launch without a separate "save" step.

"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Literal

from core.personality import (
    PERSONALITY_MEMORY_RULES,
    Personality,
    PersonalityManager,
)

logger = logging.getLogger("IRIS.workspace_sync")

FileStatus = Literal["changed", "unchanged", "skipped", "deleted", "error"]


# ════════════════════════════════════════════════════════════
# Paths
# ════════════════════════════════════════════════════════════

def get_hermes_home() -> Path:
    """Return the path to Hermes' home directory.

    Resolution order:

      1. ``HERMES_HOME`` env var if set.
      2. **Windows**: ``%LOCALAPPDATA%\\hermes`` when it has a real
         config (``config.yaml`` or ``.env``). Hermes' ``install.ps1``
         drops the workspace there, NOT under ``~/.hermes``. Getting
         this wrong silently breaks SOUL/IDENTITY sync because IRIS
         writes to a phantom ``~/.hermes`` while Hermes reads from
         LOCALAPPDATA — same root cause as the chat-401 bug we fixed
         in ``wizard/hermes_config.py``.
      3. **POSIX**: ``$XDG_CONFIG_HOME/hermes`` if populated, else
         ``~/.hermes``.
      4. Final fallback: ``~/.hermes`` (works on every OS — never
         raises — but won't be the right place on a stock Windows
         install if step 2's existence check failed).

    We do **not** create the directory if it doesn't exist — the
    absence is a meaningful signal that Hermes isn't installed yet,
    and callers should skip the sync rather than litter the FS.
    """
    # One canonical resolver prevents the wizard, wake runtime and workspace
    # sync from creating different Hermes homes on a fresh Windows install.
    from wizard.hermes_config import get_hermes_home as resolve_hermes_home

    return resolve_hermes_home()


def get_hermes_workspace() -> Path:
    """Return the path where workspace .md files live.

    Hermes stores them at the root of ``~/.hermes/`` (SOUL.md,
    USER.md, IDENTITY.md sit alongside config.yaml).
    """
    return get_hermes_home()


# ════════════════════════════════════════════════════════════
# Content builders — each returns the file body OR None to skip
# ════════════════════════════════════════════════════════════

def _build_soul_md(personality: Personality) -> str:
    """Build SOUL.md from the personality.

    Sections that depend on user-fillable fields are omitted when
    those fields are empty. The shared identity does not impose the
    avatar wire format on human messaging channels.
    """
    p = personality
    name = p.name
    parts: list[str] = [f"# {name} — SOUL {p.emoji}".rstrip()]

    # Identity / backstory. EMPTY = EMPTY — we do not invent.
    backstory = (p.backstory or "").strip()
    if backstory:
        backstory = backstory.replace("{name}", name)
        parts.append("## Identidad")
        parts.append(backstory)

    instructions = (p.personality_instructions or "").strip()
    if instructions:
        parts.append("## Personalización elegida por el usuario")
        parts.append(
            "Estas instrucciones ajustan o sustituyen los rasgos base si "
            "existe algún conflicto:\n"
            + instructions.replace("{name}", name)
        )

    # Language is always known (defaults to "es").
    if p.language:
        parts.append("## Idioma principal")
        parts.append(p.language)

    # Traits — only render if there's at least one.
    traits = [t.strip() for t in (p.traits or []) if t and t.strip()]
    if traits:
        parts.append("## Rasgos de personalidad")
        parts.extend(f"- {t}" for t in traits)

    # SOUL is shared with human messaging channels. The avatar JSON belongs to
    # HermesBridge's per-request system message, not to this shared identity.
    parts.append("## Formato según el destino")
    parts.append(
        "En conversaciones de mensajería, CLI o escritorio, responde con texto "
        "natural y conserva el formato solicitado por el usuario. No envuelvas "
        "esas respuestas en campos de voz, emoción o animación. "
        "Solo utiliza el contrato JSON del avatar cuando la petición del sistema "
        "de Iris lo exija explícitamente; esa petición incluye su esquema y "
        "el catálogo de animaciones. La personalidad se mantiene en ambos casos."
    )

    # Keep direct Hermes clients (CLI/TUI/Desktop/channels) under the same
    # identity-vs-memory contract used by IRIS' runtime prompt.
    parts.append("## Memoria")
    parts.extend(f"- {rule}" for rule in PERSONALITY_MEMORY_RULES)

    # IRIS's face/voice bridge only consumes the visible avatar JSON.
    # Hermes owns tools, sub-agents, and scheduled work internally.
    parts.append("## Capacidades avanzadas")
    parts.append(
        "Hermes gestiona las herramientas, subagentes y tareas programadas. "
        "Presenta su resultado al usuario sin exponer envoltorios internos. "
        "En la ruta del avatar, no añadas campos `delegate`, `delegates`, "
        "`cron` ni `scheduled_task` al contrato de Iris."
    )

    # Boundaries — only render non-empty list.
    boundaries = [b.strip() for b in (p.boundaries or []) if b and b.strip()]
    if boundaries:
        parts.append("## Límites")
        parts.extend(f"- {b}" for b in boundaries)

    parts.append(
        "## Contexto visual\n"
        f"Si recibes un bloque `[Visión: ...]`, úsalo naturalmente. {name} **ve** "
        "lo que el sensor describe; no expliques el sistema, sólo reacciona como "
        "lo haría una persona."
    )

    return "\n\n".join(parts).rstrip() + "\n"


def _build_identity_md(personality: Personality, avatar: str = "") -> str:
    """Build IDENTITY.md from the personality.

    Short "who am I" card Hermes shows in its CLI sidebar. Empty
    fields are written as blank values so the file remains parseable
    but doesn't lie about, say, an avatar that doesn't exist.
    """
    p = personality
    backstory = (p.backstory or "").strip().replace("{name}", p.name)
    backstory_block = f"\n## Backstory\n\n{backstory}\n" if backstory else ""
    return (
        f"# IDENTITY.md — Who Am I?\n"
        f"\n"
        f"- **Name:** {p.name}\n"
        f"- **Creature:** AI assistant (Project IRIS)\n"
        f"- **Vibe:** {(p.traits[0] if p.traits else '').strip()}\n"
        f"- **Emoji:** {p.emoji}\n"
        f"- **Avatar:** {avatar}\n"
        f"{backstory_block}\n"
        f"---\n"
        f"\n"
        f"_Synced from Project IRIS. Editing this by hand is fine; the next\n"
        f"IRIS launch will overwrite differences if the wizard config changes._\n"
    )


def _build_user_md_default() -> str:
    """USER.md fallback when none exists.

    Leaves placeholders so the user can fill them. We never overwrite
    an existing USER.md — that file belongs to the user.
    """
    return (
        "- **Nombre:** \n"
        "- **¿Cómo llamarlo?:** \n"
    )


# ════════════════════════════════════════════════════════════
# Idempotent write helper
# ════════════════════════════════════════════════════════════

def _write_if_changed(path: Path, content: str) -> FileStatus:
    """Write ``content`` to ``path`` only if it differs from current.

    Returns:
        "changed"   — file was created or its content was updated
        "unchanged" — current content already matches
        "error"     — IO failed; logged, NOT raised (sync is best-effort)
    """
    try:
        if path.exists():
            current = path.read_text(encoding="utf-8")
            if current == content:
                return "unchanged"
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{path.name}.iris-",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if temporary and temporary.exists():
                temporary.unlink(missing_ok=True)
        return "changed"
    except OSError as exc:
        logger.warning("Failed to write %s: %s", path, exc)
        return "error"


# ════════════════════════════════════════════════════════════
# Public API
# ════════════════════════════════════════════════════════════

def sync_personality_to_workspace(
    personality: PersonalityManager | Personality,
    config: Any | None = None,
) -> dict[str, FileStatus]:
    """Sync the active personality to Hermes' workspace .md files.

    Args:
        personality: Either a ``PersonalityManager`` (preferred) or a
            raw ``Personality`` dataclass. We accept both because the
            agent wires up a manager but tests may want to drop in a
            bare personality without instantiating a manager.
        config: Reserved for future use (e.g. workspace path override
            when Hermes isn't at the default location). Currently
            unused — the workspace path is derived from ``HERMES_HOME``
            or ``~/.hermes/``.

    Returns:
        Mapping ``{filename: status}`` where ``status`` is one of
        ``changed`` / ``unchanged`` / ``skipped`` / ``error``.
        ``skipped`` means the file was intentionally not touched
        (e.g. USER.md already has content).

    Notes:
        Best-effort only — IO errors are logged but never raised. A
        broken sync should never prevent IRIS from booting.
    """
    if isinstance(personality, PersonalityManager):
        p = personality.personality
    else:
        p = personality

    workspace = get_hermes_workspace()
    if not workspace.exists():
        logger.info(
            "Hermes home not found at %s — skipping sync. Install Hermes "
            "first via the wizard's setup step.",
            workspace,
        )
        return {
            "SOUL.md":     "skipped",
            "IDENTITY.md": "skipped",
            "USER.md":     "skipped",
        }

    summary: dict[str, FileStatus] = {}

    # SOUL.md — IRIS's full system prompt for Hermes.
    summary["SOUL.md"] = _write_if_changed(
        workspace / "SOUL.md",
        _build_soul_md(p),
    )

    # IDENTITY.md — short "who am I" card.
    summary["IDENTITY.md"] = _write_if_changed(
        workspace / "IDENTITY.md",
        _build_identity_md(
            p,
            # Without a model of the person's own, IRIS shows her included VRM.
            avatar=(
                getattr(config, "avatar_vrm_path", "") or "IRIS (VRM incluido)"
            ) if config is not None else "",
        ),
    )

    # USER.md — only create if missing or empty; user owns this file.
    user_md = workspace / "USER.md"
    if user_md.exists() and user_md.read_text(encoding="utf-8").strip():
        summary["USER.md"] = "skipped"
    else:
        summary["USER.md"] = _write_if_changed(user_md, _build_user_md_default())

    # Compact one-liner so boot logs don't drown in this.
    diff = {k: v for k, v in summary.items() if v not in ("unchanged", "skipped")}
    if diff:
        logger.info("Workspace sync: %s", diff)
    else:
        logger.debug("Workspace sync: no changes (%s)", summary)

    return summary


__all__ = [
    "sync_personality_to_workspace",
    "get_hermes_workspace",
    "get_hermes_home",
]
