"""Hermes discovery and adapter regression tests for the IRIS OS skill."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import core.iris_skill_lifecycle as lifecycle

ROOT = Path(__file__).resolve().parents[1]


def _load_adapter():
    path = ROOT / ".hermes" / "skills" / "iris-os" / "scripts" / "control_player.py"
    spec = importlib.util.spec_from_file_location("iris_player_skill_adapter", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_skill_sync_installs_updates_and_preserves_foreign_destination(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = ROOT / ".hermes" / "skills" / "iris-os"
    monkeypatch.setattr(lifecycle, "bundled_iris_skill_source", lambda: source)
    hermes_home = tmp_path / "hermes"

    installed = lifecycle.sync_iris_skill_to_hermes(hermes_home=hermes_home)
    current = lifecycle.sync_iris_skill_to_hermes(hermes_home=hermes_home)

    assert installed["status"] == "installed"
    assert current["status"] == "current"
    assert (hermes_home / "skills" / "iris-os" / "SKILL.md").is_file()

    foreign_home = tmp_path / "foreign"
    foreign = foreign_home / "skills" / "iris-os"
    foreign.mkdir(parents=True)
    (foreign / "SKILL.md").write_text(
        "---\nname: iris-os\nauthor: another-user\n---\n",
        encoding="utf-8",
    )

    conflict = lifecycle.sync_iris_skill_to_hermes(hermes_home=foreign_home)
    assert conflict["status"] == "conflict"
    assert "another-user" in (foreign / "SKILL.md").read_text(encoding="utf-8")


def test_adapter_prefers_runtime_port_and_drops_legacy_request_id(
    tmp_path: Path,
    monkeypatch,
) -> None:
    adapter = _load_adapter()
    iris_home = tmp_path / "iris"
    iris_home.mkdir()
    (iris_home / "config.json").write_text(json.dumps({"websocket_port": 8765}), encoding="utf-8")
    monkeypatch.setenv("IRIS_HOME", str(iris_home))
    monkeypatch.setenv("IRIS_WEBSOCKET_PORT", "9876")
    payload_path = tmp_path / "request.json"
    payload_path.write_text(json.dumps({
        "schema": "iris.app-intent/v1",
        "app": "player",
        "action": "pause",
        "args": {},
        "request_id": "legacy-channel-id",
    }), encoding="utf-8")

    assert adapter._iris_port() == 9876
    assert "request_id" not in adapter._read_payload(payload_path)


def test_app_builder_skill_is_discoverable_in_the_active_hermes_home(tmp_path):
    home = tmp_path / "hermes"
    home.mkdir()
    assert lifecycle.sync_app_builder_to_hermes(hermes_home=home)["status"] == "installed"
    destination = home / "skills" / "iris-app-builder"
    assert "name: iris-app-builder" in (destination / "SKILL.md").read_text("utf-8")
    for name in lifecycle.APP_TEMPLATE_FILES:
        assert (destination / "template" / name).is_file()
    assert lifecycle.sync_app_builder_to_hermes(hermes_home=home)["status"] == "current"


def test_app_builder_context_only_for_a_local_user_turn(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from core import websocket_server

    monkeypatch.setattr(websocket_server, "get_iris_home", lambda: tmp_path)
    agent = SimpleNamespace(app_builder_ready=True)
    context = websocket_server._app_builder_context(agent, local_origin=True, app_message=False)
    assert "iris-app-builder" in context
    assert "pack <id-del-borrador>" in context
    assert not websocket_server._app_builder_context(agent, local_origin=False, app_message=False)
    assert not websocket_server._app_builder_context(agent, local_origin=True, app_message=True)
    agent.app_builder_ready = False
    assert not websocket_server._app_builder_context(agent, local_origin=True, app_message=False)
