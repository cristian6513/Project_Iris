from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def _workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_ci_uses_read_only_pinned_actions_without_secrets() -> None:
    workflow = _workflow()

    assert "permissions:\n  contents: read" in workflow
    assert "persist-credentials: false" in workflow
    assert "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1" in workflow
    assert "astral-sh/setup-uv@c771a70e6277c0a99b617c7a806ffedaca235ff9" in workflow
    assert "actions/setup-node@820762786026740c76f36085b0efc47a31fe5020" in workflow
    assert "secrets." not in workflow
    assert "continue-on-error" not in workflow


def test_ci_runs_every_automated_release_gate() -> None:
    workflow = _workflow()

    expected_commands = (
        "uv sync --locked --extra dev",
        "uv run --no-sync pytest -q",
        "uv build",
        "uv run --no-sync python scripts/verify_distribution.py dist",
        "node --test tests/js/*.test.mjs",
        "npm --prefix companion ci",
        "npm --prefix companion run build",
        "cargo test --locked --manifest-path companion/src-tauri/Cargo.toml",
    )
    for command in expected_commands:
        assert command in workflow


def test_ci_covers_primary_runtime_and_portability_floor() -> None:
    workflow = _workflow()

    assert "os: windows-latest\n            python-version: \"3.11\"" in workflow
    assert "os: ubuntu-latest\n            python-version: \"3.13\"" in workflow
    assert (ROOT / ".node-version").read_text(encoding="utf-8").strip() == "22"
    rust_toolchain = (ROOT / "rust-toolchain.toml").read_text(encoding="utf-8")
    assert 'channel = "1.97.1"' in rust_toolchain


def test_ci_has_manual_and_change_driven_entry_points() -> None:
    workflow = _workflow()

    assert "push:" in workflow
    assert "pull_request:" in workflow
    assert "workflow_dispatch:" in workflow
    assert workflow.count("timeout-minutes:") == 3
