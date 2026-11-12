from __future__ import annotations

import copy
import sys
from types import ModuleType

import pytest
import yaml

from core.hermes_api_tools_worker import repair


@pytest.fixture
def hermes(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("HERMES_MANAGED_DIR", raising=False)
    mapping = {
        "file": {"write_file", "read_file"},
        "terminal": {"terminal"},
        "web": {"search"},
        "delegation": {"delegate_task"},
        "code_execution": {"execute_code"},
    }

    def resolve(name):
        return (
            set().union(*mapping.values())
            if name in {"all", "*", "hermes-api-server"}
            else mapping.get(name, set())
        )

    def load(path, **kwargs):
        data = yaml.safe_load(path.read_text("utf-8")) or {}
        if not isinstance(data, dict):
            raise ValueError("invalid config")
        return data

    def selected(cfg, platform, **kwargs):
        return set((cfg.get("platform_toolsets") or {}).get(platform, ["hermes-api-server"]))

    def expand(names, platform):
        return {key for key, value in mapping.items() if value <= names}

    def write(path, data):
        path.write_text(yaml.safe_dump(data, allow_unicode=True), "utf-8")

    objects = {
        "hermes_cli": {},
        "hermes_cli.managed_scope": {
            "_DEFAULT_MANAGED_DIR": tmp_path / "managed",
            "is_key_managed": lambda key: False,
        },
        "hermes_cli.config": {
            "atomic_config_write": write,
            "is_managed": lambda: False,
            "require_readable_config_before_write": load,
        },
        "hermes_cli.config_effective": {"load_user_config_effective": load},
        "hermes_cli.plugins": {"discover_plugins": lambda: None},
        "hermes_cli.tools_config": {
            "_configurable_subset_of": expand,
            "_get_platform_tools": selected,
        },
        "toolsets": {"resolve_toolset": resolve},
    }
    mods = {}
    for name, attrs in objects.items():
        m = ModuleType(name)
        m.__dict__.update(attrs)
        mods[name] = m
        monkeypatch.setitem(sys.modules, name, m)
    mods["hermes_cli"].managed_scope = mods["hermes_cli.managed_scope"]
    original = {
        "model": {"default": "modelo ñ", "provider": "fixture"},
        "agent": {"disabled_toolsets": ["memory"]},
        "platform_toolsets": {"api_server": ["all"], "telegram": ["delegation"]},
        "other": {"nested": ["keep"]},
    }
    (home / "config.yaml").write_text(yaml.safe_dump(original, allow_unicode=True), "utf-8")
    return home, mods, original


@pytest.mark.parametrize(
    "tools",
    [
        ["all"],
        ["*"],
        ["file", "all"],
        ["hermes-api-server"],
        ["file", "delegation", "code_execution"],
    ],
)
def test_normalization_preserves_every_other_setting(hermes, tools):
    home, _, original = hermes
    original["platform_toolsets"]["api_server"] = tools
    (home / "config.yaml").write_text(yaml.safe_dump(original, allow_unicode=True), "utf-8")
    result = repair(["delegation", "code_execution"])
    assert result["ok"]
    after = yaml.safe_load((home / "config.yaml").read_text("utf-8"))
    safe = after["platform_toolsets"].pop("api_server")
    expected = copy.deepcopy(original)
    expected["platform_toolsets"].pop("api_server")
    assert after == expected
    assert "file" in safe
    assert not set(safe) & {"all", "*", "hermes-api-server", "delegation", "code_execution"}


def test_managed_restriction_is_not_overwritten(hermes):
    home, mods, _ = hermes
    mods["hermes_cli.managed_scope"].is_key_managed = lambda key: True
    before = (home / "config.yaml").read_bytes()
    assert not repair(["delegation", "code_execution"])["ok"]
    assert (home / "config.yaml").read_bytes() == before


def test_model_edit_during_resolution_is_kept(hermes):
    home, mods, _ = hermes
    original = mods["hermes_cli.tools_config"]._configurable_subset_of
    changed = []

    def expand(names, platform):
        if not changed:
            changed.append(True)
            cfg = yaml.safe_load((home / "config.yaml").read_text("utf-8"))
            cfg["model"]["default"] = "new-model"
            (home / "config.yaml").write_text(yaml.safe_dump(cfg), "utf-8")
        return original(names, platform)

    mods["hermes_cli.tools_config"]._configurable_subset_of = expand
    assert repair(["delegation", "code_execution"])["ok"]
    assert (
        yaml.safe_load((home / "config.yaml").read_text("utf-8"))["model"]["default"] == "new-model"
    )


def test_post_write_verification_rejects_an_ineffective_writer(hermes):
    _, mods, _ = hermes
    mods["hermes_cli.config"].atomic_config_write = lambda path, data: None
    assert not repair(["delegation", "code_execution"])["ok"]
