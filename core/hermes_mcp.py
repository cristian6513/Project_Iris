"""
Hermes MCP catalog bridge.

IRIS only reads Hermes' MCP state here. Installs, OAuth, API keys and tool
probing stay inside Hermes so the avatar UI never writes credentials or mutates
the control plane by accident.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.hermes_runtime import (
    get_hermes_config_path,
    get_hermes_home,
    get_hermes_repo,
    run_hermes_cli,
)


def _hermes_repo() -> Path:
    return get_hermes_repo()


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(path.read_text(encoding="utf-8", errors="replace"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _transport_label(config: dict[str, Any]) -> str:
    if config.get("url"):
        return "http"
    if config.get("command"):
        return "stdio"
    return "unknown"


def _catalog_entry_from_manifest(manifest: Path, installed: dict[str, Any]) -> dict[str, Any] | None:
    raw = _read_yaml(manifest)
    name = str(raw.get("name") or manifest.parent.name).strip()
    if not name:
        return None

    transport = raw.get("transport") if isinstance(raw.get("transport"), dict) else {}
    auth = raw.get("auth") if isinstance(raw.get("auth"), dict) else {}
    tools = raw.get("tools") if isinstance(raw.get("tools"), dict) else {}
    default_tools = tools.get("default_enabled")
    if not isinstance(default_tools, list):
        default_tools = []

    installed_cfg = installed.get(name) if isinstance(installed.get(name), dict) else None
    enabled = True
    include_tools: list[str] = []
    if installed_cfg:
        raw_enabled = installed_cfg.get("enabled", True)
        enabled = str(raw_enabled).lower() not in {"false", "0", "no"}
        tools_cfg = installed_cfg.get("tools")
        if isinstance(tools_cfg, dict) and isinstance(tools_cfg.get("include"), list):
            include_tools = [str(x) for x in tools_cfg["include"]]

    return {
        "name": name,
        "description": str(raw.get("description") or "").strip(),
        "source": str(raw.get("source") or "").strip(),
        "transport": str(transport.get("type") or "unknown"),
        "auth": str(auth.get("type") or "none"),
        "installed": bool(installed_cfg),
        "enabled": enabled,
        "default_tools": [str(x) for x in default_tools],
        "enabled_tools": include_tools,
        "post_install": str(raw.get("post_install") or "").strip(),
    }


def _installed_entry(name: str, config: dict[str, Any], catalog_names: set[str]) -> dict[str, Any]:
    tools_cfg = config.get("tools") if isinstance(config.get("tools"), dict) else {}
    include = tools_cfg.get("include") if isinstance(tools_cfg, dict) else []
    if not isinstance(include, list):
        include = []
    raw_enabled = config.get("enabled", True)
    return {
        "name": name,
        "description": "",
        "source": "",
        "transport": _transport_label(config),
        "auth": str(config.get("auth") or "none"),
        "installed": True,
        "enabled": str(raw_enabled).lower() not in {"false", "0", "no"},
        "default_tools": [],
        "enabled_tools": [str(x) for x in include],
        "post_install": "",
        "external": name not in catalog_names,
    }


def _catalog_entry_from_cli(name: str, status: str, description: str) -> dict[str, Any]:
    installed_states = {"installed", "enabled", "configured", "active", "linked"}
    enabled_states = {"enabled", "configured", "active", "linked"}
    state = status.lower().strip()
    return {
        "name": name,
        "description": description.strip(),
        "source": "Hermes MCP catalog",
        "transport": "managed",
        "auth": "unknown",
        "installed": state in installed_states,
        "enabled": state in enabled_states,
        "default_tools": [],
        "enabled_tools": [],
        "post_install": "",
        "status": status,
        "install_command": f"hermes mcp install {name}",
        "install_supported": True,
    }


def _parse_catalog_line(line: str) -> tuple[str, str, str] | None:
    line = line.strip()
    if not line:
        return None

    for sep in (chr(0x2502), chr(0x2503)):
        line = line.replace(sep, "|")

    if set(line) <= {"-", "+", "|", " ", "=", ":"}:
        return None

    lower = line.lower()
    if lower.startswith(("name ", "install ", "server ", "mcp ")):
        return None

    if "|" in line:
        cells = [cell.strip() for cell in line.strip("|").split("|") if cell.strip()]
        if len(cells) < 2:
            return None
        name = cells[0]
        status = cells[1]
        description = " - ".join(cells[2:])
    else:
        parts = line.split(None, 2)
        if len(parts) < 2:
            return None
        name, status = parts[0], parts[1]
        description = parts[2] if len(parts) > 2 else ""

    if not name or not status or name.lower() in {"name", "server", "mcp"}:
        return None
    if status.lower() in {"status", "description", "transport"}:
        return None
    return name, status, description


def _list_mcp_catalog_cli() -> dict[str, Any] | None:
    """Read the official Hermes MCP catalog when the CLI is available."""
    result = run_hermes_cli(["mcp", "catalog"], timeout=20)
    if not result.get("success"):
        return None

    catalog: list[dict[str, Any]] = []
    for raw in str(result.get("output") or "").splitlines():
        parsed = _parse_catalog_line(raw)
        if not parsed:
            continue
        name, status, description = parsed
        catalog.append(_catalog_entry_from_cli(name, status, description))

    if not catalog:
        return None

    installed_count = sum(1 for entry in catalog if entry.get("installed"))
    enabled_count = sum(1 for entry in catalog if entry.get("installed") and entry.get("enabled"))
    return {
        "success": True,
        "supports_catalog": True,
        "catalog": catalog,
        "catalog_count": len(catalog),
        "installed_count": installed_count,
        "enabled_count": enabled_count,
        "hermes_home": str(get_hermes_home()),
        "optional_mcps_dir": "",
        "source_mode": "hermes-cli",
        "message": "Hermes MCP catalog loaded from CLI",
    }


def list_mcp_catalog() -> dict[str, Any]:
    """Return Hermes MCP catalog + installed server state in UI-safe form."""
    repo = _hermes_repo()
    optional_dir = repo / "optional-mcps"
    config_path = get_hermes_config_path()
    config = _read_yaml(config_path)
    installed = config.get("mcp_servers")
    if not isinstance(installed, dict):
        installed = {}

    catalog: list[dict[str, Any]] = []
    if optional_dir.exists():
        for manifest in sorted(optional_dir.glob("*/manifest.yaml")):
            entry = _catalog_entry_from_manifest(manifest, installed)
            if entry:
                catalog.append(entry)

    catalog_names = {entry["name"] for entry in catalog}
    for name, server_cfg in sorted(installed.items()):
        if name in catalog_names or not isinstance(server_cfg, dict):
            continue
        catalog.append(_installed_entry(str(name), server_cfg, catalog_names))

    if not catalog:
        cli_catalog = _list_mcp_catalog_cli()
        if cli_catalog:
            return cli_catalog

    installed_count = sum(1 for entry in catalog if entry.get("installed"))
    enabled_count = sum(1 for entry in catalog if entry.get("installed") and entry.get("enabled"))
    return {
        "success": True,
        "supports_catalog": optional_dir.exists(),
        "catalog": catalog,
        "catalog_count": len(catalog),
        "installed_count": installed_count,
        "enabled_count": enabled_count,
        "hermes_home": str(get_hermes_home()),
        "optional_mcps_dir": str(optional_dir),
        "source_mode": "filesystem-fallback",
        "message": "Hermes MCP catalog loaded" if optional_dir.exists() else "Hermes MCP catalog directory not found",
    }

