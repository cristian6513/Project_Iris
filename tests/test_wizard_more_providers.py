"""Provider step (2026-10-13): the main provider and more providers next to it.

/api/llm-setup says which provider Hermes runs by default and whether it passes the
Gateway's pre-flight, and which others already have a credential. /api/add-llm-provider
stores one more provider's credential and nothing else: the main provider, its model and
its auth choice stay as they are. Throwaway profiles; no network, no process.
"""

from __future__ import annotations

import hashlib
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from wizard import app as wizard

LOCAL = {"base_url": "http://127.0.0.1:8765", "client": ("127.0.0.1", 50123)}
ORIGIN = {"Origin": LOCAL["base_url"]}
ENV = "# hand-written note kept as is\nAPI_SERVER_ENABLED=true\nANTHROPIC_API_KEY=sk-ant-synthetic\n"
CONFIG = "model:\n  provider: anthropic\n  default: claude-synthetic\n"


@pytest.fixture
def profile(tmp_path, monkeypatch):
    monkeypatch.setenv("IRIS_HOME", str(tmp_path / "iris"))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    monkeypatch.delenv("IRIS_RUNTIME_DESCRIPTOR", raising=False)
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    hermes = tmp_path / "hermes"
    hermes.mkdir()
    return hermes


def configured(hermes):
    (hermes / ".env").write_text(ENV, encoding="utf-8", newline="\n")
    (hermes / "config.yaml").write_text(CONFIG, encoding="utf-8", newline="\n")
    return hermes


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def client():
    return TestClient(wizard.app, **LOCAL)


def test_the_main_provider_is_reported_with_its_readiness(profile):
    configured(profile)
    with client() as c:
        setup = c.get("/api/llm-setup").json()
    assert setup["primary"]["provider"] == "anthropic" and setup["primary"]["model"] == "claude-synthetic"
    assert setup["primary"]["ready"] is True and setup["additional"] == []
    assert "openai" in setup["addable"]
    for main_only in ("anthropic", "custom", "ollama-local", "lmstudio", "bedrock", "copilot-acp"):
        assert main_only not in setup["addable"], main_only
    assert "sk-ant-synthetic" not in str(setup)


def test_a_saved_provider_without_its_key_is_not_ready(profile):
    (profile / "config.yaml").write_text(CONFIG, encoding="utf-8")
    with client() as c:
        primary = c.get("/api/llm-setup").json()["primary"]
    assert primary["ready"] is False and primary["code"] == "missing_api_key" and primary["message"]


def test_a_fresh_profile_has_no_main_provider_and_nothing_can_be_added(profile):
    with client() as c:
        assert c.get("/api/llm-setup").json()["primary"] is None
        refused = c.post("/api/add-llm-provider", json={"provider": "openai", "api_key": "sk-x"}, headers=ORIGIN)
    assert refused.status_code == 400 and refused.json()["code"] == "missing_provider"
    assert not (profile / ".env").exists()


def test_adding_a_provider_writes_only_its_key(profile):
    configured(profile)
    config_before = digest(profile / "config.yaml")
    with client() as c:
        added = c.post("/api/add-llm-provider", json={"provider": "openai", "api_key": "sk-openai-synthetic"},
                       headers=ORIGIN)
        setup = c.get("/api/llm-setup").json()
    assert added.status_code == 200 and added.json()["auth"] == "api" and added.json()["hermes"] == "changed"
    assert digest(profile / "config.yaml") == config_before  # the main provider and its model stay
    env = (profile / ".env").read_text(encoding="utf-8")
    assert env.startswith(ENV)  # every line kept as written, the key appended
    assert "OPENAI_API_KEY=sk-openai-synthetic" in env and "IRIS_LLM_AUTH_SELECTION" not in env
    assert setup["primary"]["provider"] == "anthropic" and setup["primary"]["ready"] is True
    assert setup["additional"] == [{"provider": "openai", "name": "OpenAI", "auth": "api"}]
    assert "openai" in setup["addable"]  # its key can be replaced
    with client() as c:
        again = c.post("/api/add-llm-provider", json={"provider": "openai", "api_key": "sk-openai-synthetic"},
                       headers=ORIGIN)
    assert again.json()["hermes"] == "unchanged"


@pytest.mark.parametrize("body, code", [
    ({"provider": "anthropic", "api_key": "sk-x"}, "not_addable"),        # the main provider itself
    ({"provider": "custom", "api_key": "sk-x"}, "not_addable"),           # an endpoint is main-only
    ({"provider": "ollama-local"}, "not_addable"),                        # a local server too
    ({"provider": "no-such-provider", "api_key": "sk-x"}, "not_addable"),
    ({"provider": "Bad Id", "api_key": "sk-x"}, "invalid_provider"),
    ({"provider": "openai", "api_key": ""}, "missing_api_key"),
    ({"provider": "openai", "api_key": "sk-x\nAPI_SERVER_KEY=stolen"}, "invalid_api_key"),
    ({"provider": "openai", "api_key": "sk x"}, "invalid_api_key"),
    ({"provider": "nous"}, "missing_oauth"),                               # OAuth only, never signed in
])
def test_refused_additions_change_nothing(profile, body, code):
    configured(profile)
    before = {name: digest(profile / name) for name in (".env", "config.yaml")}
    with client() as c:
        refused = c.post("/api/add-llm-provider", json=body, headers=ORIGIN)
    assert refused.status_code == 400 and refused.json()["code"] == code, refused.json()
    assert {name: digest(profile / name) for name in (".env", "config.yaml")} == before


def test_unknown_fields_and_foreign_origins_are_refused(profile):
    configured(profile)
    with client() as c:
        extra = c.post("/api/add-llm-provider", json={"provider": "openai", "api_key": "sk-x", "model": "m"},
                       headers=ORIGIN)
        foreign = c.post("/api/add-llm-provider", json={"provider": "openai", "api_key": "sk-x"},
                         headers={"Origin": "https://foreign.example", "Sec-Fetch-Site": "cross-site"})
    assert extra.status_code == 422 and foreign.status_code == 403
    assert "OPENAI_API_KEY" not in (profile / ".env").read_text(encoding="utf-8")


def test_an_oauth_login_counts_as_another_provider(profile):
    configured(profile)

    def signed_in(provider, *, auth_type=None, home=None):
        return provider == "nous" and auth_type == "oauth"

    with patch("wizard.hermes_config.has_hermes_auth_profile", side_effect=signed_in), client() as c:
        added = c.post("/api/add-llm-provider", json={"provider": "nous", "use_oauth": True}, headers=ORIGIN)
        setup = c.get("/api/llm-setup").json()
    assert added.status_code == 200 and added.json()["auth"] == "oauth" and added.json()["hermes"] == "unchanged"
    assert {"provider": "nous", "name": setup["additional"][0]["name"], "auth": "oauth"} in setup["additional"]
    assert (profile / ".env").read_text(encoding="utf-8") == ENV


def test_the_key_writer_refuses_a_provider_without_a_key_variable(profile):
    from wizard.hermes_config import write_provider_api_key

    with pytest.raises(ValueError):
        write_provider_api_key("custom", "sk-x")
    assert not (profile / ".env").exists()


# ── Codex's review of these changes (2026-10-13, artifacts/qa11-wizard-oct13-review) ──


def _unreachable_local_server():
    """httpx.AsyncClient double for a local model server that is off."""
    import httpx
    from unittest.mock import AsyncMock

    client = AsyncMock()
    client.__aenter__.return_value = client
    client.__aexit__.return_value = False
    client.get.side_effect = httpx.ConnectError("synthetic local server is off")
    return client


@pytest.mark.parametrize("case", ["no-provider", "no-key", "local-server-off", "cannot-tell"])
def test_the_messaging_restart_refuses_before_a_ready_provider(profile, monkeypatch, case):
    # R02: «Reiniciar Hermes para aplicar» reached Hermes' own restart without the provider
    # pre-flight that «Iniciar gateway» runs. The route now asks the same question first.
    from unittest.mock import AsyncMock

    if case == "no-key":
        (profile / "config.yaml").write_text(CONFIG, encoding="utf-8")
    elif case == "local-server-off":
        (profile / "config.yaml").write_text(
            "model:\n  provider: custom\n  default: synthetic-model\n  base_url: http://127.0.0.1:11434/v1\n",
            encoding="utf-8")
        monkeypatch.setattr(wizard.httpx, "AsyncClient", lambda *a, **k: _unreachable_local_server())
    elif case == "cannot-tell":
        configured(profile)
        monkeypatch.setattr("wizard.messaging._restart_readiness",
                            AsyncMock(return_value={"ready": None, "code": "unknown", "message": ""}))
    worker = AsyncMock(side_effect=AssertionError("Hermes must not be restarted"))
    monkeypatch.setattr("wizard.messaging.messaging.call", worker)
    with client() as c:
        refused = c.post("/api/messaging/restart", headers=ORIGIN)
    assert refused.status_code == 409 and refused.json()["detail"]
    worker.assert_not_called()


def test_the_messaging_restart_works_once_the_provider_is_ready(profile, monkeypatch):
    from unittest.mock import AsyncMock

    configured(profile)
    worker = AsyncMock(return_value={"restart_started": True})
    monkeypatch.setattr("wizard.messaging.messaging.call", worker)
    with client() as c:
        restarted = c.post("/api/messaging/restart", headers=ORIGIN)
    assert restarted.status_code == 200 and restarted.json()["restart_started"] is True
    worker.assert_awaited_once_with("restart")


def test_a_provider_saved_as_main_meanwhile_keeps_its_own_key(profile, monkeypatch):
    # R03: adding OpenAI was checked against the main provider before the key writer took its
    # lock. If OpenAI became the main provider in that window, the late addition replaced the
    # new main provider's key. The writer now checks again under the lock the main writer uses.
    from wizard.hermes_config import write_hermes_config

    configured(profile)
    real_problem = wizard._additional_provider_problem
    switched = []

    def main_saved_in_between(wizard_id, main):
        answer = real_problem(wizard_id, main)  # passes: Anthropic is still the main provider
        if not switched:
            write_hermes_config(llm_provider="openai", llm_api_key="sk-main-new", llm_model="gpt-main")
            switched.append(True)
        return answer

    monkeypatch.setattr(wizard, "_additional_provider_problem", main_saved_in_between)
    with client() as c:
        late = c.post("/api/add-llm-provider", json={"provider": "openai", "api_key": "sk-additional"}, headers=ORIGIN)
    assert switched and late.status_code == 409 and late.json()["code"] == "became_main"
    env = (profile / ".env").read_text(encoding="utf-8")
    assert "OPENAI_API_KEY=sk-main-new" in env and "sk-additional" not in env
    assert "provider: openai" in (profile / "config.yaml").read_text(encoding="utf-8")


def test_the_wizard_serves_only_the_shared_iris_dialog_files(profile):
    # The setup wizard shows the same IRIS OS dialogs and icons as Iris; nothing else of Face
    # is exposed.
    with client() as c:
        script = c.get("/shared/iris_dialog.js")
        style = c.get("/shared/iris_dialog.css")
        icons = c.get("/shared/os_icons.js")
        other = c.get("/shared/app.js")
        escape = c.get("/shared/..%2Fjs%2Fapp.js")
    assert script.status_code == 200 and "irisConfirm" in script.text
    assert script.headers["content-type"].startswith("text/javascript")
    assert "./os_icons.js" in script.text
    assert style.status_code == 200 and ".iris-dialog" in style.text
    assert icons.status_code == 200 and "OS_ICON_PATHS" in icons.text
    assert icons.headers["content-type"].startswith("text/javascript")
    assert other.status_code == 404 and escape.status_code == 404
