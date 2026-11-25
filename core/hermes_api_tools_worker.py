"""Executed as a file by Hermes' Python; deliberately imports no Iris modules.

Repairs Iris' API tool selection and, best effort, keeps Nous' free-tier guest off while the
profile never chose (``keep_nous_guest_off``, Hermes 0.23+).
"""

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


def keep_nous_guest_off(config_path: Path, dependencies: list[Path]) -> None:
    """Write ``nous.guest: false`` while the key is unset (Hermes 0.23+).

    Hermes 0.23 dropped the launch gate of Nous' free tier: with ``nous.guest`` unset, the
    first hosted-connector action mints an anonymous Nous identity (a portal request). Up to
    0.21 that needed the desktop app's opt-in, so Iris keeps it off for profiles that never
    chose; the wizard writes the same default. An explicit value, a managed key or a config
    edited meanwhile is left alone. Best effort: the tool policy below never depends on it.
    """
    from hermes_cli import managed_scope
    from hermes_cli.config import atomic_config_write, is_managed, require_readable_config_before_write

    if is_managed() or managed_scope.is_key_managed("nous.guest"):
        return
    before = _hashes(dependencies)
    if before[str(config_path)] is None:
        return  # No profile config yet: the wizard's first write carries the default.
    raw = require_readable_config_before_write(config_path)
    nous = raw.get("nous")
    if nous is not None and not isinstance(nous, dict):
        return
    if isinstance(nous, dict) and "guest" in nous:
        return
    replacement = copy.deepcopy(raw)
    replacement["nous"] = {**(nous or {}), "guest": False}
    if _hashes(dependencies) != before:
        return  # Keep an edit made meanwhile; the next turn tries again.
    atomic_config_write(config_path, replacement)


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
    try:
        keep_nous_guest_off(config_path, dependencies)
    except Exception:
        pass  # Never blocks a turn; the verified hashes below include any write it made.

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
