"""Safe projections for IRIS Continuity Center.

Hermes remains the source of truth for profiles, sessions, memory and project
context.  This module only parses the official CLI/API outputs and returns a
small, secret-free view for IRIS OS; it never opens Hermes' ``state.db``.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Any

from core.response_text import visible_response_text

_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_PROFILE_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")
_SESSION_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.:@-]{0,191}$")
_EMPTY_CELLS = {"", "-", "—", "none", "null", "n/a"}


def valid_profile_name(value: str) -> bool:
    return bool(_PROFILE_NAME_RE.fullmatch(str(value or "").strip()))


def valid_session_id(value: str) -> bool:
    return bool(_SESSION_ID_RE.fullmatch(str(value or "").strip()))


def _clean_cell(value: Any, *, maximum: int = 160) -> str:
    text = " ".join(str(value or "").replace("\x00", "").split()).strip()
    if text.casefold() in _EMPTY_CELLS:
        return ""
    return text[:maximum]


def _nonnegative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _timestamp(value: Any) -> float:
    try:
        parsed = float(value or 0)
    except (TypeError, ValueError, OverflowError):
        text = str(value or "").strip()
        if not text:
            return 0.0
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
        except (TypeError, ValueError, OverflowError):
            return 0.0
    return parsed if 0 <= parsed < float("inf") else 0.0


def parse_profile_list(output: str) -> list[dict[str, Any]]:
    """Parse ``hermes profile list`` without depending on private modules."""
    rows: list[dict[str, Any]] = []
    for raw_line in _ANSI_RE.sub("", str(output or "")).splitlines():
        line = raw_line.strip()
        if not line or set(line) <= {"─", "-", " ", "\t"}:
            continue
        if line.casefold().startswith("profile "):
            continue
        active = line.startswith(("◆", "*"))
        if active:
            line = line[1:].strip()
        cells = re.split(r"\s{2,}", line)
        name = _clean_cell(cells[0], maximum=64)
        if not valid_profile_name(name):
            continue
        model = _clean_cell(cells[1]) if len(cells) > 1 else ""
        gateway = _clean_cell(cells[2], maximum=40) if len(cells) > 2 else ""
        alias = _clean_cell(cells[3], maximum=80) if len(cells) > 3 else ""
        distribution = _clean_cell(cells[4], maximum=120) if len(cells) > 4 else ""
        provider = model.split("/", 1)[0] if "/" in model else ""
        rows.append({
            "name": name,
            "is_default": name.casefold() == "default",
            "active": active,
            "model": model,
            "provider": provider,
            "gateway": gateway.casefold() or "unknown",
            "has_alias": bool(alias),
            "distribution": distribution,
        })
    if rows and not any(row["active"] for row in rows):
        for row in rows:
            if row["is_default"]:
                row["active"] = True
                break
    return rows


def _session_rows(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("data", "sessions", "items", "results"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    nested = payload.get("data")
    if isinstance(nested, dict):
        return _session_rows(nested)
    return []


def _message_rows(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("data", "messages", "items", "results"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    nested = payload.get("data")
    if isinstance(nested, dict):
        return _message_rows(nested)
    return []


def _message_text(value: Any, *, maximum: int = 12_000) -> str:
    """Project user-visible text without exposing data URLs or tool payloads."""
    if isinstance(value, str):
        return value.replace("\x00", "").strip()[:maximum]
    if not isinstance(value, list):
        return ""
    chunks: list[str] = []
    for part in value:
        if isinstance(part, str):
            chunks.append(part)
            continue
        if not isinstance(part, dict):
            continue
        kind = str(part.get("type") or "").casefold()
        text = part.get("text") or part.get("content")
        if isinstance(text, str) and kind in {"", "text", "input_text", "output_text"}:
            chunks.append(text)
        elif kind in {"image", "image_url", "input_image"}:
            chunks.append("[Imagen adjunta]")
    return "\n".join(chunk.strip() for chunk in chunks if chunk.strip()).strip()[:maximum]


def normalize_messages(payload: Any, *, limit: int = 120) -> list[dict[str, Any]]:
    """Return the safe chat transcript IRIS may render after a resume.

    System prompts, reasoning, tool calls and tool results remain owned by
    Hermes.  Continuity Center needs only the human/assistant conversation to
    replace the visible chat without leaking operational or secret-bearing
    rows into the browser.
    """
    messages: list[dict[str, Any]] = []
    for item in _message_rows(payload):
        if not isinstance(item, dict):
            continue
        raw_role = str(item.get("role") or "").casefold()
        if raw_role not in {"user", "assistant", "iris"}:
            continue
        content = (
            _message_text(item.get("content")) if raw_role == "user"
            else visible_response_text(item.get("content"), maximum=12_000)
        )
        if not content:
            continue
        messages.append({
            "role": "user" if raw_role == "user" else "IRIS",
            "content": content,
            "emotion": "neutral",
            "timestamp": _timestamp(item.get("timestamp")),
        })
    bounded = max(1, min(int(limit or 120), 200))
    return messages[-bounded:]


def _workspace_projection(row: dict[str, Any]) -> tuple[str, str]:
    raw = _clean_cell(row.get("git_repo_root") or row.get("cwd"), maximum=4096)
    if not raw:
        return "", ""
    normalized = raw.replace("\\", "/").rstrip("/")
    label = normalized.rsplit("/", 1)[-1] or "Workspace"
    opaque_id = hashlib.sha256(normalized.casefold().encode("utf-8")).hexdigest()[:16]
    return f"ws_{opaque_id}", _clean_cell(label, maximum=100)


def normalize_sessions(
    payload: Any,
    *,
    default_profile: str = "default",
    current_session_id: str = "",
) -> list[dict[str, Any]]:
    sessions: list[dict[str, Any]] = []
    for item in _session_rows(payload):
        if not isinstance(item, dict):
            continue
        session_id = _clean_cell(item.get("id") or item.get("session_id"), maximum=192)
        if not valid_session_id(session_id):
            continue
        profile = _clean_cell(item.get("profile") or default_profile, maximum=64)
        if not valid_profile_name(profile):
            profile = default_profile if valid_profile_name(default_profile) else "default"
        workspace_id, workspace_label = _workspace_projection(item)
        sessions.append({
            "id": session_id,
            "title": _clean_cell(item.get("title"), maximum=140) or "Sesión sin título",
            "preview": _clean_cell(item.get("preview"), maximum=260),
            "profile": profile,
            "model": _clean_cell(item.get("model"), maximum=160),
            "source": _clean_cell(item.get("source"), maximum=48) or "Hermes",
            "message_count": _nonnegative_int(item.get("message_count")),
            "input_tokens": _nonnegative_int(item.get("input_tokens")),
            "output_tokens": _nonnegative_int(item.get("output_tokens")),
            "last_active": _timestamp(
                item.get("last_active") or item.get("ended_at") or item.get("started_at")
            ),
            "archived": bool(item.get("archived")),
            "pinned": bool(item.get("pinned")),
            "workspace_id": workspace_id,
            "workspace_label": workspace_label,
            "is_current": bool(current_session_id and session_id == current_session_id),
        })
    sessions.sort(key=lambda row: row["last_active"], reverse=True)
    return sessions


def summarize_workspaces(sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for session in sessions:
        workspace_id = str(session.get("workspace_id") or "")
        if not workspace_id:
            continue
        profile = str(session.get("profile") or "default")
        key = (profile, workspace_id)
        current = grouped.setdefault(key, {
            "id": workspace_id,
            "label": str(session.get("workspace_label") or "Workspace"),
            "profile": profile,
            "session_count": 0,
            "last_active": 0.0,
        })
        current["session_count"] += 1
        current["last_active"] = max(current["last_active"], float(session.get("last_active") or 0))
    return sorted(grouped.values(), key=lambda item: item["last_active"], reverse=True)


def _gateway_profile(profiles: list[dict[str, Any]], gateway_model: str) -> str:
    """Infer the profile served by the configured Gateway without opening its DB."""
    running = [row for row in profiles if row.get("gateway") == "running"]
    clean_model = _clean_cell(gateway_model, maximum=160).casefold()
    if clean_model:
        matching = [
            row for row in running
            if _clean_cell(row.get("model"), maximum=160).casefold() == clean_model
        ]
        if len(matching) == 1:
            return str(matching[0]["name"])
    if len(running) == 1:
        return str(running[0]["name"])
    active_running = [row for row in running if row.get("active")]
    return str(active_running[0]["name"]) if len(active_running) == 1 else ""


def continuity_snapshot(
    *,
    profile_output: str,
    session_payload: Any,
    sessions_available: bool,
    session_update_available: bool,
    session_messages_available: bool = False,
    session_create_available: bool = False,
    session_fork_available: bool = False,
    session_continuity_available: bool = False,
    current_session_id: str = "",
    gateway_model: str = "",
    session_profile_fallback: str = "gateway",
    error: str = "",
) -> dict[str, Any]:
    profiles = parse_profile_list(profile_output)
    active_profile = next((row["name"] for row in profiles if row["active"]), "default")
    gateway_profile = _gateway_profile(profiles, gateway_model)
    session_profile = gateway_profile or (
        session_profile_fallback
        if valid_profile_name(session_profile_fallback)
        else "gateway"
    )
    sessions = normalize_sessions(
        session_payload,
        default_profile=session_profile,
        current_session_id=current_session_id,
    ) if sessions_available else []
    workspaces = summarize_workspaces(sessions)
    profiles_available = bool(profiles)
    if sessions_available and profiles_available:
        mode = "native"
    elif sessions_available or profiles_available:
        mode = "partial"
    else:
        mode = "offline"
    return {
        "success": bool(profiles_available or sessions_available),
        "mode": mode,
        "active_profile": active_profile,
        "gateway_profile": gateway_profile or (session_profile if sessions_available else ""),
        "current_session_id": current_session_id,
        "profiles": profiles,
        "sessions": sessions,
        "workspaces": workspaces,
        "counts": {
            "profiles": len(profiles),
            "sessions": len(sessions),
            "workspaces": len(workspaces),
        },
        "has_more": bool(
            isinstance(session_payload, dict) and session_payload.get("has_more") is True
        ),
        "supports": {
            "profiles": profiles_available,
            "profile_activation": profiles_available,
            "sessions": sessions_available,
            "session_update": session_update_available,
            "session_messages": session_messages_available,
            "session_create": session_create_available,
            "session_fork": session_fork_available,
            "workspace_metadata": bool(workspaces),
            "resume_in_iris": bool(
                sessions_available
                and session_messages_available
                and session_continuity_available
            ),
        },
        "notice": (
            "IRIS muestra perfiles y sesiones administrados por Hermes; "
            "no duplica su memoria ni su base de datos."
            if mode == "native"
            else "Continuity Center funciona parcialmente hasta que Hermes exponga "
            "todas las capacidades necesarias."
            if mode == "partial"
            else "Hermes no está disponible. Tus perfiles y sesiones no fueron modificados."
        ),
        "error": _clean_cell(error, maximum=300),
    }


__all__ = [
    "continuity_snapshot",
    "normalize_messages",
    "normalize_sessions",
    "parse_profile_list",
    "summarize_workspaces",
    "valid_profile_name",
    "valid_session_id",
]
