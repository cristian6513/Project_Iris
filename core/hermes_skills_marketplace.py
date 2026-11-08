"""Read Hermes' optional skills catalogue for IRIS OS.

Hermes owns skill execution. IRIS only presents the catalogue, shows
which skills are already installed, and gives the user a clean control
surface for the next installation phase.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from core.hermes_runtime import get_hermes_repo, run_hermes_cli
from core.skills_bridge import (
    _parse_frontmatter,
    _platform_eligible,
    list_installed_skills,
)


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-")


def _short_code(name: str) -> str:
    parts = re.findall(r"[A-Za-z0-9]+", name)
    if len(parts) >= 2:
        return (parts[0][0] + parts[1][0]).upper()
    compact = re.sub(r"[^A-Za-z0-9]", "", name)
    return (compact[:2] or "SK").upper()


def _hermes_repo_root() -> Path | None:
    candidates: list[Path] = [get_hermes_repo()]
    for candidate in candidates:
        if (candidate / "optional-skills").exists():
            return candidate
    return None


def _as_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in re.split(r"[,;]", str(value)) if item.strip()]


def _metadata_tags(meta: dict[str, Any]) -> list[str]:
    tags = _as_list(meta.get("tags"))
    hermes_meta = meta.get("metadata")
    if isinstance(hermes_meta, dict):
        hermes_meta = hermes_meta.get("hermes")
    if isinstance(hermes_meta, dict):
        tags.extend(_as_list(hermes_meta.get("tags")))
    seen: set[str] = set()
    unique: list[str] = []
    for tag in tags:
        key = tag.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(tag)
    return unique[:7]


def _first_paragraph(text: str) -> str:
    body = re.sub(r"^---.*?---", "", text, flags=re.S).strip()
    for block in re.split(r"\n\s*\n", body):
        cleaned = re.sub(r"^#+\s*", "", block.strip())
        if cleaned and not cleaned.startswith("|") and not cleaned.startswith("```"):
            return re.sub(r"\s+", " ", cleaned)[:220]
    return ""


def _installed_index() -> set[str]:
    installed = list_installed_skills(refresh=True)
    names: set[str] = set()
    for skill in installed:
        name = str(skill.get("name") or "").strip()
        path = str(skill.get("path") or "").strip()
        if name:
            names.add(_slug(name))
        if path:
            names.add(_slug(Path(path).parent.name))
    return names


def _clean_cli_text(value: str) -> str:
    value = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", value or "")
    return re.sub(r"\s+", " ", value.replace("…", "...")).strip()


def _safe_skill_identifier(value: str) -> str:
    identifier = _clean_cli_text(value)
    if any(char.isspace() for char in identifier):
        return ""
    if re.fullmatch(r"[A-Za-z0-9._@+:/-]+", identifier):
        return identifier
    return ""


def _entry_from_hermes_browse_row(
    row: dict[str, str],
    installed_names: set[str],
) -> dict[str, Any] | None:
    name = _clean_cli_text(row.get("name", ""))
    identifier = _safe_skill_identifier(row.get("identifier", ""))
    if not name and identifier:
        name = identifier.rsplit("/", 1)[-1]
    if not name:
        return None
    if name.endswith(("…", "...")) and identifier:
        name = identifier.rsplit("/", 1)[-1]
    source = _clean_cli_text(row.get("source", "")) or "hermes"
    trust = _clean_cli_text(row.get("trust", ""))
    description = _clean_cli_text(row.get("description", "")) or "Hermes Skill Hub entry."
    command_identifier = identifier or _safe_skill_identifier(name)
    identity = identifier or f"{_slug(source) or 'hermes'}/{_slug(name)}"
    installed = any(
        candidate in installed_names
        for candidate in {_slug(name), _slug(identity), _slug(identity.rsplit("/", 1)[-1])}
    )
    is_official = source.lower() == "official" or "official" in trust.lower()
    state = "installed" if installed else "ready"
    return {
        "id": identity,
        "identifier": identifier,
        "name": name,
        "emoji": _short_code(name),
        "description": description,
        "category": source,
        "source": f"Hermes Skills Hub - {source}",
        "trust": trust,
        "installed": installed,
        "eligible": True,
        "state": state,
        "tags": [tag for tag in [source, "official" if is_official else "community"] if tag],
        "requires": "",
        "homepage": "https://hermes-agent.nousresearch.com/docs/skills",
        "path": "",
        "install_command": f"/skills install {command_identifier}" if command_identifier else "",
        "cli_command": f"hermes skills install {command_identifier}" if command_identifier else "",
        "install_supported": False,
    }


def _entry_from_hermes_search_row(
    row: dict[str, Any],
    installed_names: set[str],
) -> dict[str, Any] | None:
    return _entry_from_hermes_browse_row(
        {
            "name": str(row.get("name") or ""),
            "identifier": str(row.get("identifier") or ""),
            "source": str(row.get("source") or ""),
            "trust": str(row.get("trust_level") or row.get("trust") or ""),
            "description": str(row.get("description") or ""),
        },
        installed_names,
    )


def _parse_hermes_browse(output: str, installed_names: set[str]) -> dict[str, Any]:
    rows: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    total = 0
    page = 1
    pages = 1
    sources: dict[str, int] = {}

    header_match = re.search(r"\((\d+)\s+skills loaded,\s+page\s+(\d+)/(\d+)\)", output)
    if header_match:
        total = int(header_match.group(1))
        page = int(header_match.group(2))
        pages = int(header_match.group(3))

    sources_match = re.search(r"^\s*Sources:\s*(.+)$", output, flags=re.M)
    if sources_match:
        for name, count in re.findall(r"([\w-]+):\s*(\d+)", sources_match.group(1)):
            sources[name] = int(count)

    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line.startswith("│"):
            continue
        cells = [cell.strip() for cell in line.strip("│").split("│")]
        if len(cells) < 6:
            continue
        first = cells[0]
        if first == "#" or first.startswith("─"):
            continue
        if first.isdigit():
            if current:
                rows.append(current)
            current = {
                "name": cells[1],
                "description": cells[2],
                "source": cells[3],
                "trust": cells[4],
                "identifier": cells[5],
            }
            continue
        if current:
            if cells[1]:
                current["name"] = f"{current.get('name', '')}{cells[1]}".strip()
            if cells[2]:
                current["description"] = f"{current.get('description', '')} {cells[2]}".strip()
            if cells[5]:
                wrapped_identifier = f"{current.get('identifier', '')}{cells[5]}"
                current["identifier"] = wrapped_identifier.replace(" ", "")

    if current:
        rows.append(current)

    catalog = [
        entry for row in rows
        if (entry := _entry_from_hermes_browse_row(row, installed_names))
    ]
    return {
        "catalog": catalog,
        "hub_count": total or len(catalog),
        "hub_page": page,
        "hub_pages": pages,
        "hub_sources": sources,
    }


def _list_hermes_hub_page(page: int = 1, size: int = 60, source: str = "all") -> dict[str, Any]:
    safe_sources = {
        "all",
        "official",
        "skills-sh",
        "well-known",
        "github",
        "clawhub",
        "lobehub",
        "browse-sh",
        "nvidia",
        "openai",
        "anthropic",
        "huggingface",
        "voltagent",
        "gstack",
        "minimax",
    }
    source = source if source in safe_sources else "all"
    page = max(1, min(int(page or 1), 100_000))
    size = max(1, min(int(size or 60), 100))
    installed_names = _installed_index()
    result = run_hermes_cli(
        ["skills", "browse", "--page", str(page), "--size", str(size), "--source", source],
        timeout=45,
    )
    if not result.get("success"):
        return {
            "success": False,
            "catalog": [],
            "error": str(result.get("error") or "Hermes Skills Hub did not respond"),
            "source": "hermes-cli",
        }
    output = str(result.get("output") or "")
    parsed = _parse_hermes_browse(output, installed_names)
    parsed.update({
        "success": True,
        "source": "hermes-cli",
        "source_filter": source,
    })
    return parsed


def _parse_hermes_search(output: str, installed_names: set[str]) -> list[dict[str, Any]]:
    """Parse the stable ``skills search --json`` projection tolerantly."""
    clean_output = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", output or "").strip()
    start = clean_output.find("[")
    if start < 0:
        return []
    try:
        rows, _ = json.JSONDecoder().raw_decode(clean_output[start:])
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(rows, list):
        return []
    return [
        entry
        for row in rows
        if isinstance(row, dict)
        and (entry := _entry_from_hermes_search_row(row, installed_names))
    ]


def _search_hermes_hub(query: str, size: int = 60, source: str = "all") -> dict[str, Any]:
    safe_sources = {
        "all", "official", "skills-sh", "well-known", "github", "clawhub",
        "lobehub", "browse-sh", "nvidia", "openai", "anthropic", "huggingface",
        "voltagent", "gstack", "minimax",
    }
    source = source if source in safe_sources else "all"
    limit = max(1, min(int(size or 60), 100))
    normalized_query = " ".join(str(query or "").split())[:120]
    if not normalized_query:
        return _list_hermes_hub_page(page=1, size=limit, source=source)
    installed_names = _installed_index()
    result = run_hermes_cli(
        ["skills", "search", normalized_query, "--source", source, "--limit", str(limit), "--json"],
        timeout=45,
    )
    if not result.get("success"):
        return {
            "success": False,
            "catalog": [],
            "error": str(result.get("error") or "Hermes Skills Hub search did not respond"),
            "source": "hermes-cli",
            "query": normalized_query,
        }
    catalog = _parse_hermes_search(str(result.get("output") or ""), installed_names)
    return {
        "success": True,
        "catalog": catalog,
        "hub_count": len(catalog),
        "hub_page": 1,
        "hub_pages": 1,
        "hub_sources": {},
        "source": "hermes-cli",
        "source_filter": source,
        "query": normalized_query,
        "search_limited": len(catalog) >= limit,
    }


def _entry_from_skill_md(
    skill_md: Path,
    root: Path,
    installed_names: set[str],
) -> dict[str, Any] | None:
    try:
        text = skill_md.read_text(encoding="utf-8", errors="replace")
        meta = _parse_frontmatter(text)
        rel = skill_md.relative_to(root)
    except OSError:
        return None

    skill_parts = rel.parts[:-1]
    if not skill_parts:
        return None
    category = skill_parts[0]
    skill_key = "/".join(skill_parts)
    name = str(meta.get("name") or skill_md.parent.name).strip()
    if not name:
        return None

    eligible = _platform_eligible(meta.get("platforms"))
    installed = _slug(name) in installed_names or _slug(skill_md.parent.name) in installed_names
    state = "installed" if installed else ("ready" if eligible else "setup")
    description = str(meta.get("description") or "").strip() or _first_paragraph(text)

    return {
        "id": f"official/{skill_key}",
        "name": name,
        "emoji": str(meta.get("emoji") or _short_code(name)),
        "description": description or "Hermes optional skill.",
        "category": category,
        "source": "Hermes optional",
        "installed": installed,
        "eligible": eligible,
        "state": state,
        "tags": _metadata_tags(meta),
        "requires": str(meta.get("requires") or ""),
        "homepage": str(meta.get("homepage") or ""),
        "path": str(skill_md),
        "install_command": f"/skills install official/{skill_key}",
        "cli_command": f"hermes skills install official/{skill_key}",
        "install_supported": False,
    }


def list_skills_marketplace(
    page: int = 1,
    size: int = 60,
    source: str = "all",
    query: str = "",
) -> dict[str, Any]:
    repo = _hermes_repo_root()
    installed_names = _installed_index()
    installed_count = len(list_installed_skills(refresh=False))
    normalized_query = " ".join(str(query or "").split())[:120]
    hub = (
        _search_hermes_hub(normalized_query, size=size, source=source)
        if normalized_query
        else _list_hermes_hub_page(page=page, size=size, source=source)
    )
    if not repo:
        return {
            "success": bool(hub.get("success")),
            "catalog": hub.get("catalog", []),
            "installed_count": installed_count,
            "available_count": hub.get("hub_count", len(hub.get("catalog", []))),
            "ready_count": sum(1 for entry in hub.get("catalog", []) if entry.get("eligible")),
            "setup_count": 0,
            "categories": sorted({
                entry.get("category", "hermes") for entry in hub.get("catalog", [])
            }),
            "hub": hub,
            "query": normalized_query,
            "remote_source": "hermes-cli",
            "error": "" if hub.get("success") else "Hermes Skills Hub was not available.",
        }

    optional_root = repo / "optional-skills"
    catalog: list[dict[str, Any]] = []
    for skill_md in sorted(optional_root.rglob("SKILL.md")):
        entry = _entry_from_skill_md(skill_md, optional_root, installed_names)
        if entry and (
            not normalized_query
            or normalized_query.casefold() in " ".join(
                str(entry.get(field) or "")
                for field in ("name", "description", "category", "source")
            ).casefold()
        ):
            catalog.append(entry)

    merged: dict[str, dict[str, Any]] = {}
    for entry in catalog:
        key = str(entry.get("id") or _slug(entry.get("name", ""))).casefold()
        merged[key] = entry
    for entry in hub.get("catalog", []):
        key = str(entry.get("id") or _slug(entry.get("name", ""))).casefold()
        if not key:
            continue
        if key in merged:
            merged[key] = {
                **entry,
                **merged[key],
                "source": entry.get("source") or merged[key].get("source"),
                "category": entry.get("category") or merged[key].get("category"),
                "trust": entry.get("trust", ""),
            }
        else:
            merged[key] = entry

    combined = sorted(merged.values(), key=lambda item: str(item.get("name", "")).lower())
    categories = sorted({entry.get("category") or "general" for entry in combined})
    optional_installed = sum(1 for entry in combined if entry.get("installed"))
    hub_count = int(hub.get("hub_count") or 0)
    available_count = max(hub_count, len(combined))
    return {
        "success": True,
        "catalog": combined,
        "available_count": available_count,
        "visible_count": len(combined),
        "installed_count": installed_count,
        "optional_installed_count": optional_installed,
        "ready_count": sum(1 for entry in combined if entry.get("eligible")),
        "setup_count": sum(1 for entry in combined if not entry.get("eligible")),
        "categories": categories,
        "source_root": str(optional_root),
        "hub": hub,
        "query": normalized_query,
        "remote_source": "hermes-cli" if hub.get("success") else "local-optional",
        "hub_error": "" if hub.get("success") else hub.get("error", ""),
        "install_supported": False,
        "install_note": (
            "Hermes owns installation. IRIS shows the exact /skills install command "
            "and can wire confirmed one-click installs next."
        ),
    }
