from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest

from core.hermes_compatibility import (
    IRIS_MIN_HERMES_VERSION,
    hermes_compatibility,
    parse_hermes_version,
    platform_support,
)
from core.hermes_doctor import hermes_doctor
from core.hermes_runtime import resolve_hermes_cli


def test_parse_hermes_version_is_tolerant_of_cli_banners() -> None:
    assert parse_hermes_version("Hermes Agent v0.19.0") == (0, 19, 0)
    assert parse_hermes_version(
        "Hermes Agent v0.21.0 (2026.8.31) · upstream f709bd88"
    ) == (0, 21, 0)
    assert parse_hermes_version("Hermes version 0.18.7 — ready") == (0, 18, 7)
    assert parse_hermes_version("no version available") is None


def test_platform_support_marks_intel_macos_as_unsupported() -> None:
    support = platform_support("Darwin", "x86_64")
    assert support["status"] == "unsupported"
    assert support["supported"] is False


def test_platform_support_identifies_termux_and_nix_as_tier_2() -> None:
    termux = platform_support(
        "Linux",
        "aarch64",
        {"TERMUX_VERSION": "0.119", "PREFIX": "/data/data/com.termux/files/usr"},
    )
    nix = platform_support("Linux", "x86_64", {"IN_NIX_SHELL": "pure"})

    assert termux["status"] == "best_effort"
    assert termux["tier"] == "Tier 2"
    assert nix["status"] == "best_effort"
    assert nix["tier"] == "Tier 2"


@pytest.mark.parametrize(
    "version_text",
    (
        "Hermes Agent v0.20.0",
        "Hermes Agent v0.21.0 (2026.8.31) · upstream f709bd88",
    ),
)
def test_compatibility_accepts_the_validated_installation(version_text: str) -> None:
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        home = root / "home"
        repo = home / "hermes-agent"
        repo.mkdir(parents=True)
        config = home / "config.yaml"
        config.write_text("agent: {}\n", encoding="utf-8")
        support = {
            "status": "supported",
            "tier": "Tier 1",
            "label": "Windows compatible",
            "detail": "Supported.",
            "system": "Windows",
            "architecture": "x86_64",
            "supported": True,
        }
        with (
            patch("core.hermes_compatibility.platform_support", return_value=support),
            patch(
                "core.hermes_compatibility.resolve_hermes_cli",
                return_value="C:/Hermes/hermes.exe",
            ),
            patch("core.hermes_compatibility.get_hermes_home", return_value=home),
            patch("core.hermes_compatibility.get_hermes_repo", return_value=repo),
            patch("core.hermes_compatibility.get_hermes_config_path", return_value=config),
            patch(
                "core.hermes_compatibility.run_hermes_cli",
                return_value={"success": True, "output": version_text},
            ),
        ):
            report = hermes_compatibility()

    assert report["overall_status"] == "ready"
    assert report["version"]["minimum"] == ".".join(map(str, IRIS_MIN_HERMES_VERSION))


def test_compatibility_warns_outside_the_validated_release_family() -> None:
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        repo = root / "hermes-agent"
        repo.mkdir()
        config = root / "config.yaml"
        config.write_text("agent: {}\n", encoding="utf-8")
        support = {
            "status": "supported",
            "tier": "Tier 1",
            "label": "Linux compatible",
            "detail": "Supported.",
            "system": "Linux",
            "architecture": "x86_64",
            "supported": True,
        }
        with (
            patch("core.hermes_compatibility.platform_support", return_value=support),
            patch("core.hermes_compatibility.resolve_hermes_cli", return_value="/usr/local/bin/hermes"),
            patch("core.hermes_compatibility.get_hermes_home", return_value=root),
            patch("core.hermes_compatibility.get_hermes_repo", return_value=repo),
            patch("core.hermes_compatibility.get_hermes_config_path", return_value=config),
            patch("core.hermes_compatibility.run_hermes_cli") as run_cli,
        ):
            report = hermes_compatibility(
                version_result={"success": True, "output": "Hermes Agent v0.22.0"},
            )

    run_cli.assert_not_called()
    assert report["overall_status"] == "warning"
    assert report["feature_contract"]["strategy"] == "runtime_probe"


def test_doctor_embeds_the_iris_compatibility_report() -> None:
    with TemporaryDirectory() as temp_dir:
        home = Path(temp_dir)
        repo = home / "hermes-agent"
        repo.mkdir()
        config = home / "config.yaml"
        env = home / ".env"
        config.write_text("agent: {}\n", encoding="utf-8")
        compatibility = {
            "overall_status": "ready",
            "summary": {"label": "Compatible con IRIS", "headline": "Runtime validado."},
            "checks": [],
            "recommendations": [],
        }
        cli_result = {
            "success": True,
            "returncode": 0,
            "output": "Gateway Service: running\nNo scheduled jobs",
            "error": "",
            "cli_path": "C:/Hermes/hermes.exe",
        }
        with (
            patch(
                "core.hermes_doctor.hermes_compatibility",
                return_value=compatibility,
            ) as compatibility_probe,
            patch("core.hermes_doctor.get_hermes_home", return_value=home),
            patch("core.hermes_doctor.get_hermes_repo", return_value=repo),
            patch("core.hermes_doctor.get_hermes_config_path", return_value=config),
            patch("core.hermes_doctor.get_hermes_env_path", return_value=env),
            patch("core.hermes_doctor.resolve_hermes_cli", return_value="C:/Hermes/hermes.exe"),
            patch("core.hermes_doctor.run_hermes_cli", return_value=cli_result),
            patch(
                "core.hermes_doctor._kanban_probe",
                return_value={"success": True, "error": "", "counts": {}, "task_count": 0},
            ),
        ):
            report = hermes_doctor()

    compatibility_probe.assert_called_once()
    assert compatibility_probe.call_args.kwargs["version_result"] == cli_result
    assert report["compatibility"] == compatibility
    assert report["summary"]["label"] == "Hermes listo"
    env_check = next(check for check in report["checks"] if check["id"] == "env_file")
    assert env_check["status"] == "info"
    assert env_check not in report["issues"]


def test_managed_launcher_wins_over_an_old_global_path_entry() -> None:
    with TemporaryDirectory() as temp_dir:
        repo = Path(temp_dir) / "hermes-agent"
        managed = repo / "venv" / "Scripts" / "hermes.exe"
        managed.parent.mkdir(parents=True)
        managed.write_bytes(b"")
        with (
            patch("core.hermes_runtime._candidate_repos", return_value=[repo]),
            patch("core.hermes_runtime.shutil.which", return_value="C:/Python/Scripts/hermes.exe"),
            patch("core.hermes_runtime.Path.home", return_value=Path(temp_dir) / "home"),
        ):
            resolved = resolve_hermes_cli()

    assert resolved == str(managed)
