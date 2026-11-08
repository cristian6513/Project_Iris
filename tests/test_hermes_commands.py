"""Regression coverage for the IRIS OS Hermes command surface."""

from __future__ import annotations

from pathlib import Path

from core import hermes_commands
from core import skills_bridge


ROOT = Path(__file__).resolve().parents[1]


def _registry_fixture():
    return [
        {
            "name": "status",
            "description": "Show status",
            "category": "Session",
            "aliases": [],
            "subcommands": [],
        },
        {
            "name": "reasoning",
            "description": "Manage reasoning",
            "category": "Configuration",
            "aliases": [],
            "subcommands": ["none", "low", "medium", "high"],
        },
        {
            "name": "yolo",
            "description": "Disable approvals",
            "category": "Configuration",
            "aliases": [],
            "subcommands": [],
        },
        {
            "name": "version",
            "description": "Show version",
            "category": "Info",
            "aliases": ["v"],
            "subcommands": [],
        },
        {
            "name": "model",
            "description": "Switch model",
            "category": "Configuration",
            "aliases": [],
            "subcommands": [],
        },
    ]


def test_catalogue_overlays_truthful_iris_availability(monkeypatch) -> None:
    monkeypatch.setattr(hermes_commands, "_installed_registry", lambda: (_registry_fixture(), "test-registry"))
    monkeypatch.setattr(hermes_commands, "_skill_commands", lambda _reserved: [])
    monkeypatch.setattr(hermes_commands, "resolve_hermes_cli", lambda: "hermes")

    catalogue = hermes_commands.hermes_command_catalogue(refresh=True)
    by_name = {row["name"]: row for row in catalogue["commands"]}

    assert catalogue["source"] == "test-registry"
    assert by_name["status"]["execution"] == "server"
    assert by_name["status"]["available"] is True
    assert by_name["reasoning"]["execution"] == "client"
    assert by_name["reasoning"]["integrated_subcommands"] == ["none", "low", "medium", "high"]
    # F01: /model opens the selector instead of being "terminal only".
    assert (by_name["model"]["execution"], by_name["model"]["action"]) == ("client", "model")
    assert by_name["yolo"]["execution"] == "terminal"
    assert by_name["yolo"]["available"] is False
    assert by_name["apps"]["source"] == "IRIS"
    assert by_name["apps"]["available"] is True


def test_terminal_only_command_never_reaches_cli(monkeypatch) -> None:
    command = hermes_commands._normalise_registry_row(_registry_fixture()[2])
    monkeypatch.setattr(
        hermes_commands,
        "hermes_command_catalogue",
        lambda: {"commands": [command], "source": "test", "updated_at": 0},
    )
    monkeypatch.setattr(
        hermes_commands,
        "run_hermes_cli",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("CLI must not run")),
    )

    result = hermes_commands.execute_hermes_command("/yolo")

    assert result["ok"] is False
    assert "ejecutor equivalente seguro" in result["text"]


def test_read_only_commands_do_not_promise_a_missing_hermes_runtime() -> None:
    command = hermes_commands._normalise_registry_row(_registry_fixture()[0], runtime_available=False)

    assert command["available"] is False
    assert command["availability"] == "Hermes no instalado"
    assert "no está instalado" in command["reason"]


def test_read_only_command_uses_fixed_argv_and_rejects_arguments(monkeypatch) -> None:
    command = hermes_commands._normalise_registry_row(_registry_fixture()[3])
    calls = []
    monkeypatch.setattr(
        hermes_commands,
        "hermes_command_catalogue",
        lambda: {"commands": [command], "source": "test", "updated_at": 0},
    )
    monkeypatch.setattr(
        hermes_commands,
        "run_hermes_cli",
        lambda argv, **kwargs: calls.append((argv, kwargs)) or {"success": True, "output": "Hermes 0.20.0"},
    )

    ok = hermes_commands.execute_hermes_command("/v")
    rejected = hermes_commands.execute_hermes_command("/version --delete")

    assert ok["ok"] is True
    assert calls == [(["--version"], {"timeout": 15})]
    assert rejected["ok"] is False
    assert "no acepta argumentos" in rejected["text"]


def test_enabled_skill_becomes_agent_turn_but_preserves_slash_history(monkeypatch) -> None:
    skill = {
        "name": "demo-skill",
        "aliases": [],
        "available": True,
        "execution": "skill",
    }
    monkeypatch.setattr(
        hermes_commands,
        "hermes_command_catalogue",
        lambda: {"commands": [skill], "source": "test", "updated_at": 0},
    )

    result = hermes_commands.execute_hermes_command('/demo-skill "revisa este archivo"')

    assert result["ok"] is True
    assert result["kind"] == "agent_prompt"
    assert "demo-skill" in result["prompt"]
    assert '"revisa este archivo"' in result["prompt"]
    assert result["memory_text"] == '/demo-skill "revisa este archivo"'


def test_skill_commands_skip_invalid_names_and_registry_aliases(monkeypatch) -> None:
    monkeypatch.setattr(
        skills_bridge,
        "list_installed_skills",
        lambda: [
            {"name": "v", "enabled": True, "eligible": True},
            {"name": "bad/name", "enabled": True, "eligible": True},
            {"name": "safe-skill", "enabled": False, "eligible": True},
        ],
    )

    rows = hermes_commands._skill_commands({"v"})

    assert [row["name"] for row in rows] == ["safe-skill"]
    assert rows[0]["available"] is False
    assert "Activa esta skill" in rows[0]["reason"]


def test_command_ui_is_lazy_dynamic_and_does_not_send_slashes_as_normal_chat() -> None:
    html = (ROOT / "face" / "index.html").read_text(encoding="utf-8")
    panel = (ROOT / "face" / "js" / "panel.js").read_text(encoding="utf-8")
    palette = (ROOT / "face" / "js" / "command_palette.js").read_text(encoding="utf-8")
    server = (ROOT / "core" / "websocket_server.py").read_text(encoding="utf-8")

    assert 'href="css/command_palette.css?v=2"' in html
    assert "Ctrl+K paleta" in html
    assert "if (text.startsWith('/'))" in panel
    slash_guard = panel[panel.index("if (text.startsWith('/'))"):panel.index("// Hermes Desktop-style flow")]
    assert "this.commandPalette?.executeLine(text)" in slash_guard
    assert "this.onSendMessage" not in slash_guard
    assert "/api/hermes/commands" in palette
    assert "this.loadCatalogue();" in palette
    assert "event.ctrlKey || event.metaKey" in palette
    assert "Solo terminal" in palette
    assert "No integrado" in palette
    assert "item.integrated_subcommands" in palette
    assert "IRIS:skills-changed" in palette
    assert 'elif msg_type == "hermes_command":' in server
    assert 'elif msg_type == "user_text":' in server


def test_command_palette_exposes_keyboard_and_accessibility_contracts() -> None:
    palette = (ROOT / "face" / "js" / "command_palette.js").read_text(encoding="utf-8")
    activity = (ROOT / "face" / "js" / "hermes_activity.js").read_text(encoding="utf-8")

    for key in ("ArrowDown", "ArrowUp", "Tab", "Enter", "Escape"):
        assert key in palette
    assert 'role="dialog"' in palette
    assert 'role="listbox"' in palette
    assert "aria-selected" in palette
    assert "openCommandSurface(target = 'desktop')" in activity
