"""Executed as a file by Hermes' Python; deliberately imports no Iris modules."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sys
from pathlib import Path


def _bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _hashes(paths: list[Path]) -> dict[str, str | None]:
    return {
        str(path): hashlib.sha256(data).hexdigest() if (data := _bytes(path)) is not None else None
        for path in paths
    }


def repair(blocked: list[str]) -> dict:
    from hermes_cli import managed_scope
    from hermes_cli.config import (
        atomic_config_write,
        is_managed,
        require_readable_config_before_write,
    )
    from hermes_cli.config_effective import load_user_config_effective
    from hermes_cli.plugins import discover_plugins
    from hermes_cli.tools_config import _configurable_subset_of, _get_platform_tools
    from toolsets import resolve_toolset

    discover_plugins()
    home = Path(os.environ["HERMES_HOME"]).resolve()
    config_path = home / "config.yaml"
    # Track the candidate even when it does not yet exist, so adding a managed
    # policy invalidates Core's cache too. Resolve relative paths in this process.
    managed = Path(
        os.environ.get("HERMES_MANAGED_DIR") or managed_scope._DEFAULT_MANAGED_DIR
    ).resolve()
    dependencies = [config_path, home / ".env", managed / "config.yaml", managed / ".env"]
    forbidden = {tool for name in blocked for tool in resolve_toolset(name)}
    if not forbidden:
        return {"ok": False}

    def effective():
        config = load_user_config_effective(config_path, fail_closed=True)
        selected = _get_platform_tools(config, "api_server", include_default_mcp_servers=False)
        return selected, {tool for name in selected for tool in resolve_toolset(name)}

    for _ in range(3):
        before = _hashes(dependencies)
        raw = require_readable_config_before_write(config_path)
        selected, tools = effective()
        if not tools.intersection(forbidden):
            after = _hashes(dependencies)
            if before == after:
                return {"ok": True, "files": after}
            continue
        if is_managed() or managed_scope.is_key_managed("platform_toolsets.api_server"):
            return {"ok": False}
        # Preserve safe explicit entries (including MCP names). Expand only a
        # composite that actually contributes a forbidden tool. The official
        # reverse mapper retains the supported categories, not the aggregate.
        safe = {name for name in selected if not set(resolve_toolset(name)).intersection(forbidden)}
        for name in selected - safe:
            safe.update(
                _configurable_subset_of(set(resolve_toolset(name)) - forbidden, "api_server")
            )
        safe.difference_update(blocked)
        if any(set(resolve_toolset(name)).intersection(forbidden) for name in safe):
            return {"ok": False}
        replacement = copy.deepcopy(raw)
        platforms = replacement.get("platform_toolsets")
        if platforms is None:
            platforms = replacement["platform_toolsets"] = {}
        if not isinstance(platforms, dict):
            return {"ok": False}
        platforms["api_server"] = sorted(safe)
        if _hashes(dependencies) != before:
            continue  # Keep a model/channel edit made during the resolution.
        atomic_config_write(config_path, replacement)
        verified_before = _hashes(dependencies)
        _, remaining = effective()
        verified_after = _hashes(dependencies)
        if verified_before == verified_after and not remaining.intersection(forbidden):
            return {"ok": True, "files": verified_after}
    return {"ok": False}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hermes-root", required=True)
    parser.add_argument("--blocked", nargs="+", required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(Path(args.hermes_root).resolve()))
    try:
        payload = repair(args.blocked)
    except Exception:
        # Parsing errors can quote secret-bearing YAML; never return their text.
        payload = {"ok": False}
    print("IRIS_API_TOOLS=" + json.dumps(payload))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
