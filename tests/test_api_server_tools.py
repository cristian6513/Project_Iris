"""Verified API tool policy: profiles, aggregates, caching and failed preparation."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest
import yaml

import core.hermes_api_tools as policy
from core.background_tasks import LaunchOffers, launch_context
from core.hermes_runtime import keep_unusable_tools_off


@pytest.fixture
def worker(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text(
        yaml.safe_dump(
            {"model": {"default": "m"}, "platform_toolsets": {"telegram": ["delegation"]}}
        ),
        "utf-8",
    )
    monkeypatch.setattr(policy, "get_hermes_config_path", lambda: home / "config.yaml")
    monkeypatch.setattr(policy, "get_hermes_repo", lambda: tmp_path)
    monkeypatch.setattr(policy, "hermes_python_prefix", lambda: ["hermes-python", "-E", "-s"])
    monkeypatch.setattr(
        policy, "hermes_subprocess_env", lambda: {"HERMES_HOME": str(home), "PATH": "fixture"}
    )
    monkeypatch.setattr(policy, "hermes_timeout", lambda x: x)
    monkeypatch.setattr(policy, "_verified", {})
    calls = []

    def run(args, **kwargs):
        target = Path(kwargs["env"]["HERMES_HOME"])
        calls.append(target)
        cfg = yaml.safe_load((target / "config.yaml").read_text("utf-8")) or {}
        cfg.setdefault("platform_toolsets", {})["api_server"] = ["file", "terminal", "web"]
        (target / "config.yaml").write_text(yaml.safe_dump(cfg), "utf-8")
        hashes = {
            str(target / "config.yaml"): hashlib.sha256(
                (target / "config.yaml").read_bytes()
            ).hexdigest(),
            str(target / ".env"): hashlib.sha256((target / ".env").read_bytes()).hexdigest()
            if (target / ".env").exists()
            else None,
        }
        return subprocess.CompletedProcess(
            args, 0, "IRIS_API_TOOLS=" + json.dumps({"ok": True, "files": hashes}), ""
        )

    monkeypatch.setattr(policy.subprocess, "run", run)
    return home, calls


def test_first_use_verifies_and_repeated_use_costs_no_process(worker):
    home, calls = worker
    assert keep_unusable_tools_off()
    assert keep_unusable_tools_off()
    assert calls == [home]
    cfg = yaml.safe_load((home / "config.yaml").read_text("utf-8"))
    assert cfg["model"] == {"default": "m"}
    assert cfg["platform_toolsets"]["telegram"] == ["delegation"]


@pytest.mark.parametrize(
    "tools",
    [
        ["all"],
        ["*"],
        ["hermes-api-server"],
        ["file", "delegation"],
        ["file", "code_execution"],
        ["file", "terminal"],
    ],
)
def test_a_raw_name_list_is_never_accepted_without_verification(worker, tools):
    home, calls = worker
    (home / "config.yaml").write_text(
        yaml.safe_dump({"platform_toolsets": {"api_server": tools}}), "utf-8"
    )
    assert keep_unusable_tools_off()
    assert calls == [home]


def test_secondary_profile_has_its_own_verification(worker):
    home, calls = worker
    second = home / "profiles" / "second"
    second.mkdir(parents=True)
    (second / "config.yaml").write_text("model: private\n", "utf-8")
    assert keep_unusable_tools_off()
    assert keep_unusable_tools_off("second")
    assert keep_unusable_tools_off("second")
    assert calls == [home, second]
    assert yaml.safe_load((second / "config.yaml").read_text("utf-8"))["model"] == "private"


@pytest.mark.parametrize("profile", ["../outside", "/tmp/x", "..", "second/other"])
def test_invalid_profile_never_starts_a_worker(worker, profile):
    _, calls = worker
    assert keep_unusable_tools_off(profile) is False
    assert calls == []


@pytest.mark.parametrize("name", ["config.yaml", ".env"])
def test_config_and_environment_file_changes_invalidate_the_cache(worker, name):
    home, calls = worker
    assert keep_unusable_tools_off()
    path = home / name
    path.write_text((path.read_text("utf-8") if path.exists() else "") + "\n# changed\n", "utf-8")
    assert keep_unusable_tools_off()
    assert calls == [home, home]


def test_a_refused_verification_never_becomes_cached_success(worker, monkeypatch):
    _, calls = worker
    attempts = []

    def refused(args, **kwargs):
        attempts.append(True)
        return subprocess.CompletedProcess(args, 0, 'IRIS_API_TOOLS={"ok":false}', "")

    monkeypatch.setattr(policy.subprocess, "run", refused)
    assert not keep_unusable_tools_off()
    assert not keep_unusable_tools_off()
    assert len(attempts) == 2
    assert calls == []


def test_file_changed_after_worker_verification_is_not_accepted(worker, monkeypatch):
    home, _ = worker
    original = policy.subprocess.run

    def late_edit(*args, **kwargs):
        result = original(*args, **kwargs)
        (home / "config.yaml").write_text("platform_toolsets:\n  api_server: [all]\n", "utf-8")
        return result

    monkeypatch.setattr(policy.subprocess, "run", late_edit)
    assert not keep_unusable_tools_off()


def test_offer_rules_out_untracked_delegation():
    offer = LaunchOffers().open(
        epoch=1, route_token="t", profile="", session_id="s", request="hola"
    )
    text = launch_context(offer, "script.py")
    assert "delegate_task no sirve aquí" in text
    assert "una recomendación" in text
