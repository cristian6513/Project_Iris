"""F09.1: the command palette served by Core and the app registry name the same places.

Core keeps its own command list (with Hermes' commands and IRIS' internal ones); this checks
that every IRIS destination it offers is an app of apps/<id>/app.json, one of the shell's own
places or an action the client handles itself, so the two lists cannot drift apart.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from core import hermes_commands

ROOT = Path(__file__).resolve().parents[1]
SHELL_DESTINATIONS = {"desktop", "apps"}


def _apps() -> dict[str, dict]:
    apps = {}
    for manifest_path in sorted((ROOT / "apps").glob("*/app.json")):
        if manifest_path.parent.name.startswith(("_", ".")):
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["id"] == manifest_path.parent.name
        apps[manifest["id"]] = manifest
    return apps


def _client_actions() -> set[str]:
    # Actions face/js/app.js resolves before handing the rest to IRIS OS.
    app_js = (ROOT / "face" / "js" / "app.js").read_text(encoding="utf-8")
    return set(re.findall(r"if \(action === '([a-z]+)'\) \{", app_js))


def test_every_palette_destination_is_a_registered_app_or_a_known_place() -> None:
    apps = _apps()
    handled = _client_actions()
    assert {"palette", "chat", "model", "reasoning", "wake"} <= handled
    offered = {entry["action"] for entry in hermes_commands._IRIS_COMMANDS}
    offered |= set(hermes_commands._CLIENT_ACTIONS.values())
    unknown = sorted(action for action in offered if action not in apps and action not in SHELL_DESTINATIONS and action not in handled)
    assert unknown == [], f"Comandos de la paleta sin destino registrado: {unknown}"


def test_app_ids_never_take_the_shell_or_client_names() -> None:
    reserved = SHELL_DESTINATIONS | _client_actions()
    assert not reserved & set(_apps())
