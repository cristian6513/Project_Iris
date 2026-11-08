from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from core.capability_permissions import (
    CapabilityPermissionStore,
    approval_dry_run,
    capability_center_snapshot,
    parse_egress_status,
    parse_hermes_tools,
    run_security_audit,
    set_hermes_tool_enabled,
)
from core.capability_permissions import (
    test_mcp_server as probe_mcp_server,
)


def test_defaults_are_safe_and_allow_never_claims_to_bypass_hermes(tmp_path: Path) -> None:
    store = CapabilityPermissionStore(tmp_path / "permissions.json")

    snapshot = store.snapshot()
    assert (
        next(row for row in snapshot["capabilities"] if row["id"] == "camera.capture")["mode"]
        == "deny"
    )
    assert (
        next(row for row in snapshot["capabilities"] if row["id"] == "vision.context")["mode"]
        == "deny"
    )
    assert (
        next(row for row in snapshot["capabilities"] if row["id"] == "terminal.execute")["mode"]
        == "ask"
    )

    store.set_mode("terminal.execute", "allow")
    decision = store.evaluate("terminal.execute", trigger="automatic")
    assert decision["verdict"] == "allow"
    assert decision["sensor_activation"] is False
    assert decision["bypasses_hermes_approvals"] is False


def test_ask_requires_confirmation_and_deny_cannot_be_overridden(tmp_path: Path) -> None:
    store = CapabilityPermissionStore(tmp_path / "permissions.json")

    assert store.evaluate("files.write", trigger="manual")["verdict"] == "ask"
    assert store.evaluate("files.write", trigger="manual", confirmed=True)["verdict"] == "allow"
    store.set_mode("files.write", "deny")
    assert store.evaluate("files.write", trigger="manual", confirmed=True)["verdict"] == "deny"


def test_persistence_recovers_from_malformed_data_and_bounds_secret_free_audit(
    tmp_path: Path,
) -> None:
    path = tmp_path / "permissions.json"
    path.write_text("not-json", encoding="utf-8")
    store = CapabilityPermissionStore(path)

    assert store.snapshot()["counts"]["deny"] >= 2
    for index in range(180):
        store.record_event("probe", detail=f"safe event {index}")

    document = json.loads(path.read_text(encoding="utf-8"))
    assert len(document["audit"]) == 160
    assert "API_KEY" not in path.read_text(encoding="utf-8")


def test_persistence_recovers_from_structurally_invalid_policy_map(tmp_path: Path) -> None:
    path = tmp_path / "permissions.json"
    path.write_text('{"policies":["bad"],"audit":[]}', encoding="utf-8")

    snapshot = CapabilityPermissionStore(path).snapshot()

    assert len(snapshot["capabilities"]) > 0
    assert snapshot["counts"]["deny"] >= 2


def test_tool_and_egress_parsers_use_official_cli_output() -> None:
    tools = parse_hermes_tools(
        """Built-in toolsets (cli):
  ✓ enabled  web  🔍 Web Search
  ✗ disabled  stt  🎙 Speech-to-Text

Plugin toolsets (cli):
  ✗ disabled  a2a  🔌 A2A
"""
    )
    assert [(row["name"], row["enabled"], row["source"]) for row in tools] == [
        ("web", True, "builtin"),
        ("stt", False, "builtin"),
        ("a2a", False, "plugin"),
    ]

    egress = parse_egress_status(
        "Enabled           no\nProcess           (stopped)\nListening         no"
    )
    assert egress["enabled"] is False
    assert egress["listening"] is False
    assert egress["process"] == "(stopped)"


def test_snapshot_combines_local_policy_with_live_hermes_layers(tmp_path: Path) -> None:
    store = CapabilityPermissionStore(tmp_path / "permissions.json")
    results = {
        ("tools", "list"): {
            "success": True,
            "output": (
                "Built-in toolsets (cli):\n"
                "  ✓ enabled  vision  Vision\n"
                "  ✗ disabled  stt  Speech"
            ),
            "error": "",
        },
        ("egress", "status"): {"success": True, "output": "Enabled           no", "error": ""},
        ("--version",): {"success": True, "output": "Hermes Agent v0.21.0", "error": ""},
        ("approvals", "test", "--help"): {"success": True, "output": "usage", "error": ""},
        ("security", "audit", "--help"): {"success": True, "output": "usage", "error": ""},
    }

    def fake_cli(args, **_kwargs):
        return results[tuple(args)]

    with (
        patch("core.capability_permissions.run_hermes_cli", side_effect=fake_cli),
        patch("core.capability_permissions.resolve_hermes_cli", return_value="hermes.exe"),
        patch(
            "core.capability_permissions._public_mcp_snapshot",
            return_value={
                "available": True,
                "installed": [],
                "installed_count": 0,
                "enabled_count": 0,
                "source": "hermes-cli",
            },
        ),
    ):
        snapshot = capability_center_snapshot(
            {"reachable": True, "capabilities_available": True, "supports": {"toolsets": True}},
            store=store,
        )

    camera = next(
        row for row in snapshot["policy"]["capabilities"] if row["id"] == "camera.capture"
    )
    microphone = next(
        row for row in snapshot["policy"]["capabilities"] if row["id"] == "microphone.capture"
    )
    assert camera["runtime_enabled"] is None
    assert microphone["runtime_enabled"] is False
    assert snapshot["hermes"]["supports"]["toolsets"] is True
    assert snapshot["hermes"]["version"] == "Hermes Agent 0.21.0"
    assert camera["tool_gate"] == {"mapped": 0, "enabled": 0, "status": "local"}
    assert snapshot["guards"]["approvals"]["available"] is True
    assert snapshot["guards"]["security_audit"]["available"] is True
    assert snapshot["invariants"] == {
        "starts_sensors": False,
        "bypasses_hermes_approvals": False,
        "stores_secrets": False,
        "remote_administration": False,
    }


def test_pantheon_only_guards_degrade_on_older_hermes(tmp_path: Path) -> None:
    store = CapabilityPermissionStore(tmp_path / "permissions.json")
    results = {
        ("tools", "list"): {"success": True, "output": "", "error": ""},
        ("egress", "status"): {"success": False, "output": "", "error": "unknown command"},
        ("--version",): {"success": True, "output": "Hermes Agent v0.20.1", "error": ""},
        ("approvals", "test", "--help"): {
            "success": False,
            "output": "",
            "error": "unknown command",
        },
        ("security", "audit", "--help"): {
            "success": False,
            "output": "",
            "error": "unknown command",
        },
    }

    with (
        patch(
            "core.capability_permissions.run_hermes_cli",
            side_effect=lambda args, **_: results[tuple(args)],
        ),
        patch("core.capability_permissions.resolve_hermes_cli", return_value="hermes.exe"),
        patch(
            "core.capability_permissions._public_mcp_snapshot",
            return_value={
                "available": True,
                "installed": [],
                "installed_count": 0,
                "enabled_count": 0,
                "source": "hermes-cli",
            },
        ),
    ):
        snapshot = capability_center_snapshot(store=store)

    assert snapshot["hermes"]["installed"] is True
    assert snapshot["hermes"]["version"] == "Hermes Agent 0.20.1"
    assert snapshot["guards"]["approvals"]["available"] is False
    assert snapshot["guards"]["security_audit"]["available"] is False


def test_approval_probe_accepts_hermes_ask_exit_code_without_executing() -> None:
    result = {
        "success": False,
        "returncode": 2,
        "stdout": json.dumps(
            {
                "verdict": "ask-approval",
                "detail": "dangerous pattern",
                "rule": "dangerous-pattern",
                "normalized_variants": ["rm -rf target"],
            }
        ),
        "output": "",
        "error": "",
    }
    with patch("core.capability_permissions.run_hermes_cli", return_value=result) as cli:
        probe = approval_dry_run("rm -rf target")

    assert probe["success"] is True
    assert probe["verdict"] == "ask"
    assert probe["native_verdict"] == "ask-approval"
    assert cli.call_args.args[0][:4] == ["approvals", "test", "--json", "--"]


def test_approval_probe_normalizes_native_hardline_deny() -> None:
    with patch(
        "core.capability_permissions.run_hermes_cli",
        return_value={
            "success": False,
            "returncode": 3,
            "stdout": json.dumps(
                {
                    "verdict": "hardline-deny",
                    "detail": "hardline blocklist",
                    "rule": "recursive delete of root filesystem",
                    "normalized_variants": ["rm -rf /"],
                }
            ),
            "output": "",
            "error": "",
        },
    ):
        probe = approval_dry_run("rm -rf /")

    assert probe["success"] is True
    assert probe["verdict"] == "deny"
    assert probe["native_verdict"] == "hardline-deny"


def test_mcp_probe_does_not_trust_hermes_zero_exit_code_on_failure() -> None:
    with patch(
        "core.capability_permissions.run_hermes_cli",
        return_value={
            "success": True,
            "returncode": 0,
            "output": "✗ Server 'missing' not found in config.",
            "error": "",
        },
    ):
        probe = probe_mcp_server("missing")

    assert probe["success"] is False


def test_mcp_probe_requires_positive_connection_markers() -> None:
    with patch(
        "core.capability_permissions.run_hermes_cli",
        return_value={
            "success": True,
            "returncode": 0,
            "output": "✓ Connected (15ms)\n✓ Tools discovered: 3",
            "error": "",
        },
    ):
        probe = probe_mcp_server("healthy")

    assert probe["success"] is True


def test_tool_change_is_verified_against_live_inventory() -> None:
    before = {
        "success": True,
        "output": "Built-in toolsets (cli):\n  ✗ disabled  web  Web Search",
        "error": "",
    }
    changed = {"success": True, "output": "Enabled: web", "error": ""}
    after = {
        "success": True,
        "output": "Built-in toolsets (cli):\n  ✓ enabled  web  Web Search",
        "error": "",
    }
    with patch("core.capability_permissions.run_hermes_cli", side_effect=[before, changed, after]):
        result = set_hermes_tool_enabled("web", True)

    assert result["success"] is True
    assert result["enabled"] is True


def test_tool_change_does_not_trust_success_text_without_matching_inventory() -> None:
    disabled = {
        "success": True,
        "output": "Built-in toolsets (cli):\n  ✗ disabled  web  Web Search",
        "error": "",
    }
    changed = {"success": True, "output": "Enabled: web", "error": ""}
    with patch(
        "core.capability_permissions.run_hermes_cli", side_effect=[disabled, changed, disabled]
    ):
        result = set_hermes_tool_enabled("web", True)

    assert result["success"] is False
    assert result["message"].startswith("Hermes no confirmó el estado final")
    assert "Enabled: web" in result["message"]


def test_tool_change_rejects_unknown_name_before_mutating() -> None:
    with patch(
        "core.capability_permissions.run_hermes_cli",
        return_value={
            "success": True,
            "output": "Built-in toolsets (cli):\n  ✓ enabled  web  Web Search",
            "error": "",
        },
    ) as cli:
        result = set_hermes_tool_enabled("missing", True)

    assert result["success"] is False
    assert cli.call_count == 1


def test_snapshot_degrades_mapped_tools_to_unknown_when_inventory_fails(tmp_path: Path) -> None:
    results = {
        ("tools", "list"): {"success": False, "output": "", "error": "broken"},
        ("egress", "status"): {"success": True, "output": "Enabled           no", "error": ""},
        ("--version",): {"success": True, "output": "Hermes Agent v0.21.0", "error": ""},
        ("approvals", "test", "--help"): {"success": True, "output": "usage", "error": ""},
        ("security", "audit", "--help"): {"success": True, "output": "usage", "error": ""},
    }
    with (
        patch(
            "core.capability_permissions.run_hermes_cli",
            side_effect=lambda args, **_: results[tuple(args)],
        ),
        patch("core.capability_permissions.resolve_hermes_cli", return_value="hermes.exe"),
        patch(
            "core.capability_permissions._public_mcp_snapshot",
            return_value={
                "available": True,
                "installed": [],
                "installed_count": 0,
                "enabled_count": 0,
                "source": "hermes-cli",
            },
        ),
    ):
        snapshot = capability_center_snapshot(
            store=CapabilityPermissionStore(tmp_path / "permissions.json")
        )

    camera = next(
        row for row in snapshot["policy"]["capabilities"] if row["id"] == "camera.capture"
    )
    assert camera["runtime_enabled"] is None
    assert camera["tool_gate"]["status"] == "local"
    assert snapshot["hermes"]["tools_available"] is False


def test_security_audit_parses_real_hermes_schema_and_treats_findings_as_a_result() -> None:
    result = {
        "success": False,
        "returncode": 1,
        "stdout": json.dumps(
            {
                "total_components_scanned": 42,
                "finding_count": 2,
                "findings": [
                    {"package": "one", "severity": "CRITICAL"},
                    {"package": "two", "severity": "moderate"},
                ],
            }
        ),
        "output": "",
        "error": "",
    }
    with patch("core.capability_permissions.run_hermes_cli", return_value=result):
        probe = run_security_audit()

    assert probe["success"] is True
    assert probe["verdict"] == "warning"
    assert probe["summary"] == {
        "scanned": 42,
        "total": 2,
        "low": 0,
        "moderate": 1,
        "high": 0,
        "critical": 1,
    }
    assert "42 componentes" in probe["detail"]


def test_security_audit_rejects_malformed_or_transport_failure_output() -> None:
    with patch(
        "core.capability_permissions.run_hermes_cli",
        return_value={
            "success": False,
            "returncode": 2,
            "stdout": "",
            "output": "audit failed: offline",
            "error": "audit failed: offline",
        },
    ):
        probe = run_security_audit()

    assert probe["success"] is False
    assert probe["verdict"] == "error"
    assert "offline" in probe["detail"]


def test_api_mutations_require_explicit_confirmation(tmp_path: Path) -> None:
    from core.websocket_server import CapabilityPolicyRequest, capability_center_policy

    store = CapabilityPermissionStore(tmp_path / "permissions.json")
    with patch("core.websocket_server._capability_store", store):
        with pytest.raises(Exception) as exc_info:
            asyncio.run(
                capability_center_policy(
                    CapabilityPolicyRequest(
                        capability="files.read",
                        mode="allow",
                        confirm=False,
                    )
                )
            )
        response = asyncio.run(
            capability_center_policy(
                CapabilityPolicyRequest(
                    capability="files.read",
                    mode="allow",
                    confirm=True,
                )
            )
        )

    assert getattr(exc_info.value, "status_code", None) == 409
    assert response.status_code == 200
    assert (
        next(row for row in store.snapshot()["capabilities"] if row["id"] == "files.read")["mode"]
        == "allow"
    )


def test_face_shell_exposes_capability_center_as_a_real_application() -> None:
    root = Path(__file__).resolve().parents[1]
    activity = (root / "face/js/hermes_activity.js").read_text(encoding="utf-8")
    app_source = (root / "face/js/capability_center.js").read_text(encoding="utf-8")
    styles = (root / "face/css/styles.css").read_text(encoding="utf-8")

    assert "import { CapabilityCenterApp }" in activity
    # F09.1: registered by its manifest; the launcher is built from the registry.
    manifest = json.loads((root / "apps/permissions/app.json").read_text(encoding="utf-8"))
    # 2026-10-18: the launcher name is now in Spanish («Permisos»); the window is still Capability Center.
    assert (manifest["name"], manifest["presentation"]) == ("Permisos", "window")
    assert manifest["launcher"]["visible"] is True
    assert 'data-os-open="permissions"' in activity  # dashboard card
    assert 'data-workbench-view="permissions"' in activity
    assert "this._capabilityApp?.load?." in activity
    assert "/api/capability-center/policy" in app_source
    assert "NO EVITA APPROVALS" in app_source
    assert ".capability-center" in styles
    assert "container-name: capability-center" in styles
    assert ".capability-center button:disabled" in styles


def test_capability_center_javascript_has_valid_module_syntax() -> None:
    root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        ["node", "--check", str(root / "face/js/capability_center.js")],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
