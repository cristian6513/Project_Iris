"""Iris' adaptations to Hermes 0.23.3 (QA27, 2026-11-20) that work on 0.21 as well.

The end-to-end checks ran on the real 0.23.3 runtime (docs/audits/
iris_hermes_0233_integracion_claude_2026-11-20.md); these keep each adaptation in place.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


# ── wizard/hermes_config.py: defaults Iris writes while the user has not chosen


@pytest.fixture
def hermes_home(tmp_path, monkeypatch):
    monkeypatch.setenv("IRIS_HOME", str(tmp_path / "iris"))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    monkeypatch.delenv("IRIS_RUNTIME_DESCRIPTOR", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    home = tmp_path / "hermes"
    home.mkdir()
    return home


def _write(home, text=None):
    from wizard.hermes_config import write_hermes_config

    if text is not None:
        (home / "config.yaml").write_text(text, encoding="utf-8", newline="\n")
    write_hermes_config(llm_provider="openai", llm_api_key="sk-sintetica", llm_model="gpt-prueba")
    return yaml.safe_load((home / "config.yaml").read_text("utf-8"))


def test_a_new_profile_keeps_session_cleanup_and_the_nous_guest_off(hermes_home):
    config = _write(hermes_home)
    assert config["sessions"]["auto_prune"] is False
    assert config["sessions"]["auto_archive"] is False
    assert config["nous"]["guest"] is False


def test_explicit_choices_made_in_hermes_are_kept(hermes_home):
    config = _write(hermes_home, (
        "sessions:\n  auto_prune: true\n  retention_days: 30\n"
        "nous:\n  guest: true\n  portal_base_url: https://portal.example.test\n"))
    assert config["sessions"] == {"auto_prune": True, "retention_days": 30, "auto_archive": False}
    assert config["nous"] == {"guest": True, "portal_base_url": "https://portal.example.test"}


# ── wizard/hermes_messaging_worker.py: field validation in both Hermes signatures


def _messaging_worker():
    spec = importlib.util.spec_from_file_location("iris_messaging_worker_0233", ROOT / "wizard/hermes_messaging_worker.py")
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    return worker


class _Stop(Exception):
    pass


@pytest.mark.parametrize("hermes", ["0.21", "0.23"])
def test_save_validates_fields_with_the_signature_of_the_running_hermes(monkeypatch, hermes):
    worker = _messaging_worker()
    seen = []

    def validate(rules, key, value):
        seen.append((rules, key, value))
        raise _Stop

    native = SimpleNamespace(
        _require_platform=lambda pid: {"env_vars": ["TELEGRAM_BOT_TOKEN"], "required_env": []},
        _validate_messaging_env_value=validate)
    if hermes == "0.23":
        native._env_value_rules = lambda pid: {"TELEGRAM_BOT_TOKEN": (str.isdigit, "bad")}

    class Update:
        def __init__(self, **data):
            self.profile, self.env, self.clear_env, self.enabled = None, data.get("env", {}), [], False

    hermes_cli = ModuleType("hermes_cli")
    hermes_cli.__version__ = hermes
    web_routers = ModuleType("hermes_cli.web_routers")
    web_routers.messaging = native
    web_models = ModuleType("hermes_cli.web_models")
    web_models.MessagingPlatformUpdate = Update
    web_models.WhatsAppOnboardingApply = web_models.WhatsAppOnboardingStart = Update
    support = ModuleType("hermes_cli.web_server_messaging")
    hermes_cli.web_routers, hermes_cli.web_models, hermes_cli.web_server_messaging = web_routers, web_models, support
    for name, module in (("hermes_cli", hermes_cli), ("hermes_cli.web_routers", web_routers),
                         ("hermes_cli.web_routers.messaging", native), ("hermes_cli.web_models", web_models),
                         ("hermes_cli.web_server_messaging", support)):
        monkeypatch.setitem(sys.modules, name, module)
    try:
        asyncio.run(worker.dispatch("save", {"platform": "telegram",
                                             "update": {"env": {"TELEGRAM_BOT_TOKEN": " 123 "}}}))
    except _Stop:
        pass
    expected = {"TELEGRAM_BOT_TOKEN": (str.isdigit, "bad")} if hermes == "0.23" else "telegram"
    assert seen == [(expected, "TELEGRAM_BOT_TOKEN", "123")]


# ── wizard/iris_messaging_plugin.py: the delivery wrapper finds Hermes' dispatcher


@pytest.mark.parametrize("location", ["gateway.outbound.send", "tools.send_message_tool"])
def test_the_plugin_wraps_the_dispatcher_where_this_hermes_keeps_it(monkeypatch, location):
    sender = ModuleType(location)
    sender._handle_send = lambda args: args
    sender._send_to_platform = lambda *args, **kwargs: None
    package, _, name = location.rpartition(".")
    if location == "gateway.outbound.send":
        outbound = ModuleType("gateway.outbound")
        outbound.send = sender
        monkeypatch.setitem(sys.modules, "gateway", ModuleType("gateway"))
        monkeypatch.setitem(sys.modules, "gateway.outbound", outbound)
    else:
        tools = ModuleType("tools")
        tools.send_message_tool = sender
        monkeypatch.setitem(sys.modules, "tools", tools)
        # Hermes 0.21 has no gateway.outbound package.
        monkeypatch.setitem(sys.modules, "gateway.outbound", None)
    monkeypatch.setitem(sys.modules, location, sender)
    spec = importlib.util.spec_from_file_location("iris_plugin_0233", ROOT / "wizard/iris_messaging_plugin.py")
    plugin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plugin)
    plugin.register(SimpleNamespace(register_hook=lambda *_args: None))
    assert getattr(sender._handle_send, "_iris_original", None) is not None
    assert getattr(sender._send_to_platform, "_iris_original", None) is not None


# ── core/hermes_vision_worker.py: the no-fallback rung takes any signature


def test_the_vision_guard_replaces_the_rung_whatever_its_arguments(monkeypatch):
    auxiliary = ModuleType("agent.auxiliary_client")
    auxiliary._ladder_provider_fallback = None
    agent = ModuleType("agent")
    agent.auxiliary_client = auxiliary
    monkeypatch.setitem(sys.modules, "agent", agent)
    monkeypatch.setitem(sys.modules, "agent.auxiliary_client", auxiliary)
    spec = importlib.util.spec_from_file_location("iris_vision_worker_0233", ROOT / "core/hermes_vision_worker.py")
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    worker._without_provider_fallback()
    rung = auxiliary._ladder_provider_fallback

    def run(generator):
        try:
            next(generator)
        except StopIteration as stop:
            return stop.value
        raise AssertionError("the guard yielded")

    assert run(rung("error", "route")) is None                    # Hermes 0.21
    assert run(rung("failure", "route", rung_spent=True)) is None  # Hermes 0.23


# ── Nous guest default before Hermes starts (Codex review 2026-11-20): channels and scheduled
# tasks can reach a hosted connector before the first Iris chat.


@pytest.mark.real_nous_guest_default
@pytest.mark.parametrize("text, result, guest", [
    ("model:\n  default: x\n", "written", False),
    ("", "written", False),
    ("nous:\n  portal_base_url: https://portal.example.test\n", "written", False),
    ("nous:\n  guest: true\n", "kept", True),
    ("nous:\n  guest: false\n", "kept", False),
])
def test_the_guest_default_is_written_once_and_an_explicit_choice_is_kept(hermes_home, text, result, guest):
    from wizard.hermes_config import ensure_nous_guest_default

    (hermes_home / "config.yaml").write_text(text, encoding="utf-8", newline="\n")
    assert ensure_nous_guest_default() == result
    config = yaml.safe_load((hermes_home / "config.yaml").read_text("utf-8"))
    assert config["nous"]["guest"] is guest
    if "portal_base_url" in text:
        assert config["nous"]["portal_base_url"] == "https://portal.example.test"
    if "model" in text:
        assert config["model"] == {"default": "x"}


@pytest.mark.real_nous_guest_default
@pytest.mark.parametrize("text", ["model: [unclosed\n", "- a list\n", "nous: custom\n"])
def test_an_unreadable_or_unexpected_config_is_never_rewritten(hermes_home, text):
    from wizard.hermes_config import ensure_nous_guest_default

    (hermes_home / "config.yaml").write_text(text, encoding="utf-8", newline="\n")
    assert ensure_nous_guest_default() == "skipped"
    assert (hermes_home / "config.yaml").read_text("utf-8") == text


@pytest.mark.real_nous_guest_default
def test_a_profile_without_config_is_left_to_the_wizard(hermes_home):
    from wizard.hermes_config import ensure_nous_guest_default

    assert ensure_nous_guest_default() == "absent"
    assert not (hermes_home / "config.yaml").exists()


def test_the_gateway_start_applies_the_default_even_when_hermes_is_already_up(hermes_home, monkeypatch):
    import wizard.app as wizard_app
    from wizard import hermes_config, health_check

    calls = []
    monkeypatch.setattr(hermes_config, "ensure_nous_guest_default", lambda: calls.append("guest") or "written")
    monkeypatch.setattr(hermes_config, "read_hermes_dotenv", lambda: {})

    async def up(*_args, **_kwargs):
        calls.append("probe")
        return {"ok": True}

    monkeypatch.setattr(health_check, "check_gateway_port", up)
    monkeypatch.setattr(health_check, "check_gateway_health", up)
    response = asyncio.run(wizard_app._start_gateway_impl())
    assert b'"already_running":true' in response.body.replace(b" ", b"")
    assert calls[0] == "guest"  # before Iris even looks at the port


def test_a_failing_guest_default_never_blocks_the_gateway(monkeypatch):
    import wizard.app as wizard_app
    from wizard import hermes_config

    def broken():
        raise RuntimeError("config.yaml holds secret-looking text")

    monkeypatch.setattr(hermes_config, "ensure_nous_guest_default", broken)
    assert asyncio.run(wizard_app._ensure_nous_guest_default()) == "failed"


def test_wake_applies_the_default_before_hermes_serve(hermes_home, monkeypatch, tmp_path):
    from unittest.mock import AsyncMock, patch

    from core.hermes_wake import HermesGatewayError, HermesWakeRuntime
    from wizard import hermes_config

    order = []
    monkeypatch.setattr(hermes_config, "ensure_nous_guest_default", lambda: order.append("guest") or "written")

    async def spawn(*_args, **_kwargs):
        order.append("serve")
        raise PermissionError("blocked")

    with (
        patch("core.hermes_wake.resolve_hermes_cli", return_value="hermes"),
        patch("core.hermes_wake.hermes_command_prefix", return_value=["hermes"]),
        patch("core.hermes_wake.get_hermes_repo", return_value=tmp_path),
        patch("core.hermes_wake.asyncio.create_subprocess_exec", new=AsyncMock(side_effect=spawn)),
    ):
        with pytest.raises(HermesGatewayError):
            asyncio.run(HermesWakeRuntime().ensure_gateway())
    assert order == ["guest", "serve"]
