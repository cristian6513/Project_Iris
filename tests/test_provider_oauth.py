from __future__ import annotations

import asyncio
import builtins
import json
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from core.config import _derive_llm_from_hermes
from core.hermes_bridge import _diagnose_empty_response
from wizard.app import LLMSelectionUpdate, app, save_llm_selection, start_gateway
from wizard.hermes_config import (
    _write_config_yaml,
    azure_foundry_uses_entra,
    has_hermes_auth_profile,
    resolve_api_server_key,
    resolve_local_api_server_url,
    write_hermes_config,
)
from wizard.health_check import check_provider_credentials
from wizard.providers import (
    auth_mode_for,
    effective_auth_mode_for,
    get_provider,
    hermes_id_for,
    merged_providers_payload,
    providers_payload,
    warning_for,
    wizard_id_for_hermes_slug,
)
from wizard.provider_sync import (
    _SNAPSHOT_SCHEMA,
    _convert_hermes_manifest,
    _convert_hermes_inventory_payload,
    _discover_hermes_inventory,
    _merge_catalogs,
    _read_hermes_cache_or_none,
    _read_snapshot_file,
    _write_runtime_snapshot,
    fetch_live_catalog,
    hermes_catalog,
    local_hermes_provider_metadata,
)


def _capture_writer(**kwargs):
    env: dict[str, str] = {}
    config: dict[str, object] = {}
    written: dict[str, dict] = {}

    def write_env(value):
        written["env"] = value

    def write_yaml(value):
        written["yaml"] = value

    with (
        patch("wizard.hermes_config._read_env_file", return_value=env),
        patch("wizard.hermes_config._write_env_file", side_effect=write_env),
        patch("wizard.hermes_config._read_config_yaml", return_value=config),
        patch("wizard.hermes_config._write_config_yaml", side_effect=write_yaml),
    ):
        result = write_hermes_config(**kwargs)
    return result, written.get("env", env), written.get("yaml", config)


def test_openai_api_and_oauth_use_different_hermes_providers() -> None:
    assert hermes_id_for("openai") == "openai-api"
    assert hermes_id_for("openai", use_oauth=True) == "openai-codex"

    _, api_env, api_yaml = _capture_writer(
        llm_provider="openai",
        llm_model="gpt-5.4",
        llm_api_key="sk-test",
        llm_use_oauth=False,
    )
    assert api_env["OPENAI_API_KEY"] == "sk-test"
    assert api_yaml["model"]["provider"] == "openai-api"

    _, oauth_env, oauth_yaml = _capture_writer(
        llm_provider="openai",
        llm_model="gpt-5.4",
        llm_api_key="stale-key-must-not-be-written",
        llm_use_oauth=True,
    )
    assert "OPENAI_API_KEY" not in oauth_env
    assert oauth_yaml["model"]["provider"] == "openai-codex"


@pytest.mark.parametrize("use_oauth", [True, False])
def test_same_slug_anthropic_auth_choice_survives_restart(use_oauth: bool) -> None:
    _, env, yaml = _capture_writer(
        llm_provider="anthropic",
        llm_model="claude-sonnet-5",
        llm_api_key="old-key-kept-for-switching",
        llm_use_oauth=use_oauth,
    )
    # OAuth deliberately keeps a previously stored API key available for a
    # future switch; the non-secret selection marker must still win.
    env["ANTHROPIC_API_KEY"] = "old-key-kept-for-switching"
    with (
        patch("wizard.hermes_config._read_config_yaml", return_value=yaml),
        patch("wizard.hermes_config.read_hermes_dotenv", return_value=env),
        patch("wizard.hermes_config.has_hermes_auth_profile", return_value=True),
    ):
        derived = _derive_llm_from_hermes()

    assert env["IRIS_LLM_AUTH_SELECTION"] == (
        "anthropic|oauth" if use_oauth else "anthropic|api"
    )
    assert derived["llm_use_oauth"] is use_oauth
    assert derived["llm_api_key"] == ("" if use_oauth else "old-key-kept-for-switching")


def test_discovered_provider_uses_runtime_slug_and_env_key() -> None:
    runtime = {
        "deepinfra": {
            "env_vars": ("DEEPINFRA_API_KEY",),
            "oauth_capable": False,
        }
    }
    with patch("wizard.provider_sync.local_hermes_provider_metadata", return_value=runtime):
        _, env, yaml = _capture_writer(
            llm_provider="oc-deepinfra",
            llm_model="auto",
            llm_api_key="di-test",
        )

    assert env["DEEPINFRA_API_KEY"] == "di-test"
    assert yaml["model"]["provider"] == "deepinfra"


def test_google_does_not_offer_fake_oauth_and_xai_does() -> None:
    assert auth_mode_for("google") == "api"
    assert auth_mode_for("xai") == "api+oauth"
    assert hermes_id_for("xai", use_oauth=True) == "xai-oauth"


def test_specialized_provider_auth_is_not_mislabelled_as_api_or_local() -> None:
    assert auth_mode_for("bedrock") == "aws"
    assert auth_mode_for("copilot-acp") == "external"
    assert get_provider("azure-foundry")["base_url_required"] is True
    assert get_provider("custom")["base_url_required"] is True


def test_lmstudio_key_is_optional_and_runtime_auth_matches_the_ui() -> None:
    assert auth_mode_for("lmstudio") == "api-optional"
    assert get_provider("lmstudio")["env_key"] == "LM_API_KEY"

    runtime = {
        "openai-api": {"env_vars": ("OPENAI_API_KEY",), "oauth_capable": False},
    }
    assert effective_auth_mode_for("openai", runtime) == "api"
    # An optional local key must never become mandatory merely because the
    # installed Hermes adapter advertises its environment variable.
    assert effective_auth_mode_for(
        "lmstudio",
        {"lmstudio": {"env_vars": ("LM_API_KEY",), "oauth_capable": False}},
    ) == "api-optional"


def test_custom_provider_round_trip_preserves_known_local_endpoint() -> None:
    assert wizard_id_for_hermes_slug(
        "custom", base_url="http://127.0.0.1:11434/v1/",
    ) == "ollama-local"
    assert wizard_id_for_hermes_slug(
        "custom", base_url="http://127.0.0.1:8000/v1",
    ) == "vllm"
    assert wizard_id_for_hermes_slug(
        "custom", base_url="https://private.example/v1",
    ) == "custom"


def test_hermes_auto_provider_never_becomes_a_fake_open_catalog_api_provider() -> None:
    assert wizard_id_for_hermes_slug("auto") == ""


def test_azure_foundry_persists_its_required_endpoint() -> None:
    _, env, yaml = _capture_writer(
        llm_provider="azure-foundry",
        llm_model="gpt-4o",
        llm_api_key="azure-test",
        base_url="https://sample.services.ai.azure.com/models",
    )

    assert env["AZURE_FOUNDRY_API_KEY"] == "azure-test"
    assert env["AZURE_FOUNDRY_BASE_URL"] == "https://sample.services.ai.azure.com/models"
    assert yaml["model"]["provider"] == "azure-foundry"
    assert yaml["model"]["base_url"] == "https://sample.services.ai.azure.com/models"


def test_custom_endpoint_uses_optional_inline_key_not_fake_env_var() -> None:
    _, env, yaml = _capture_writer(
        llm_provider="custom",
        llm_model="my-model",
        llm_api_key="optional-secret",
        base_url="http://127.0.0.1:9000/v1",
    )

    assert auth_mode_for("custom") == "api-optional"
    assert "CUSTOM_API_KEY" not in env
    assert yaml["model"]["api_key"] == "optional-secret"
    assert yaml["model"]["base_url"] == "http://127.0.0.1:9000/v1"


def test_gateway_token_prefers_existing_hermes_value_over_default() -> None:
    with (
        patch.dict("os.environ", {}, clear=True),
        patch("wizard.hermes_config._read_env_file", return_value={"API_SERVER_KEY": "live-secret"}),
    ):
        assert resolve_api_server_key("") == "live-secret"
        assert resolve_api_server_key("explicit-secret") == "explicit-secret"
        assert resolve_api_server_key("", "https://hermes.example/api") == ""


def test_local_gateway_url_uses_hermes_port_but_preserves_remote_url() -> None:
    with patch("wizard.hermes_config._read_env_file", return_value={"API_SERVER_PORT": "9123"}):
        assert resolve_local_api_server_url("http://127.0.0.1:8642") == "http://127.0.0.1:9123"
        assert resolve_local_api_server_url("https://hermes.example/api") == "https://hermes.example/api"
        with pytest.raises(ValueError, match="HTTPS"):
            resolve_local_api_server_url("http://hermes.example/api")


def test_fresh_windows_install_uses_one_canonical_hermes_home(tmp_path: Path) -> None:
    local = tmp_path / "LocalAppData"
    with (
        patch("wizard.hermes_config.os.name", "nt"),
        patch("wizard.hermes_config.Path.home", return_value=tmp_path / "User"),
        patch.dict("os.environ", {"LOCALAPPDATA": str(local)}, clear=True),
    ):
        from wizard.hermes_config import get_hermes_home
        from core.workspace_sync import get_hermes_home as get_workspace_home

        assert get_hermes_home() == local / "hermes"
        assert get_workspace_home() == local / "hermes"


def test_existing_azure_entra_configuration_is_recognized() -> None:
    with patch(
        "wizard.hermes_config._read_config_yaml",
        return_value={"model": {"provider": "azure-foundry", "auth_mode": "entra_id"}},
    ):
        assert azure_foundry_uses_entra() is True


def test_explicit_oauth_slug_beats_stale_api_key_when_loading() -> None:
    with (
        patch(
            "wizard.hermes_config._read_config_yaml",
            return_value={"model": {"provider": "openai-codex", "default": "gpt-5.4"}},
        ),
        patch("wizard.hermes_config.read_hermes_dotenv", return_value={"OPENAI_API_KEY": "old"}),
        patch("wizard.hermes_config.has_hermes_auth_profile", return_value=True),
    ):
        result = _derive_llm_from_hermes()

    assert result["llm_provider"] == "openai"
    assert result["llm_use_oauth"] is True
    assert result["llm_api_key"] == ""


def test_explicit_api_slug_beats_stale_oauth_profile_when_loading() -> None:
    with (
        patch(
            "wizard.hermes_config._read_config_yaml",
            return_value={"model": {"provider": "openai-api", "default": "gpt-5.4"}},
        ),
        patch("wizard.hermes_config.read_hermes_dotenv", return_value={}),
        patch("wizard.hermes_config.has_hermes_auth_profile", return_value=True),
    ):
        result = _derive_llm_from_hermes()

    assert result["llm_provider"] == "openai"
    assert result["llm_use_oauth"] is False
    assert result["llm_api_key"] == ""


def test_api_credential_does_not_count_as_oauth_profile() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        home = Path(temp_dir)
        (home / "auth.json").write_text(
            json.dumps({
                "version": 1,
                "credential_pool": {
                    "anthropic": [{"auth_type": "api", "api_key": "secret"}],
                },
            }),
            encoding="utf-8",
        )
        with patch("wizard.hermes_config.get_hermes_home", return_value=home):
            assert has_hermes_auth_profile("anthropic") is True
            assert has_hermes_auth_profile("anthropic", auth_type="api") is True
            assert has_hermes_auth_profile("anthropic", auth_type="oauth") is False


def test_oauth_profile_is_matched_by_type() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        home = Path(temp_dir)
        (home / "auth.json").write_text(
            json.dumps({
                "version": 1,
                "credential_pool": {
                    "openai-codex": [{"auth_type": "oauth", "access_token": "secret"}],
                },
            }),
            encoding="utf-8",
        )
        with patch("wizard.hermes_config.get_hermes_home", return_value=home):
            assert has_hermes_auth_profile("openai-codex", auth_type="oauth") is True
            assert has_hermes_auth_profile("openai-codex", auth_type="api") is False


def test_empty_response_diagnostic_does_not_call_api_profile_oauth() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        home = Path(temp_dir)
        (home / "config.yaml").write_text(
            "model:\n  provider: anthropic\n  default: claude-sonnet-4-6\n",
            encoding="utf-8",
        )
        (home / ".env").write_text("API_SERVER_ENABLED=true\n", encoding="utf-8")
        (home / "auth.json").write_text(
            json.dumps({
                "credential_pool": {
                    "anthropic": [{"auth_type": "api", "api_key": "secret"}],
                },
            }),
            encoding="utf-8",
        )
        with patch("core.workspace_sync.get_hermes_home", return_value=home):
            message = _diagnose_empty_response()

    assert "no encuentro credenciales" in message
    assert "ANTHROPIC_API_KEY" in message


def test_provider_health_check_uses_oauth_slug_and_type() -> None:
    calls: list[tuple[str, str | None]] = []

    def profile_exists(provider: str, *, auth_type: str | None = None) -> bool:
        calls.append((provider, auth_type))
        return provider == "openai-codex" and auth_type == "oauth"

    with patch("wizard.hermes_config.has_hermes_auth_profile", side_effect=profile_exists):
        result = asyncio.run(check_provider_credentials(provider="openai", api_key=""))

    assert result["ok"] is True
    assert ("openai-codex", "oauth") in calls


def test_provider_health_check_does_not_accept_a_stale_key_for_selected_oauth() -> None:
    with (
        patch("wizard.hermes_config.has_hermes_auth_profile", return_value=False),
        patch.dict("os.environ", {"OPENAI_API_KEY": "stale-key"}),
    ):
        result = asyncio.run(check_provider_credentials(
            provider="openai",
            api_key="",
            use_oauth=True,
        ))

    assert result["ok"] is False
    assert "eligió OAuth" in result["detail"]


def test_gateway_preflight_does_not_use_stale_oauth_for_selected_api() -> None:
    cfg = SimpleNamespace(
        llm_provider="openai",
        llm_model="gpt-5.4",
        llm_api_key="",
        llm_use_oauth=False,
        hermes_url="http://127.0.0.1:8642",
    )
    with (
        patch("wizard.app.IRISConfig.load", return_value=cfg),
        patch("wizard.health_check.check_gateway_port", return_value={"ok": False}),
        patch("wizard.hermes_config.read_hermes_dotenv", return_value={}),
        patch("wizard.hermes_config.has_hermes_auth_profile", return_value=True),
    ):
        response = asyncio.run(start_gateway())

    body = json.loads(response.body)
    assert response.status_code == 200
    assert body["code"] == "missing_api_key"


def test_gateway_preflight_does_not_use_stale_key_for_selected_oauth() -> None:
    cfg = SimpleNamespace(
        llm_provider="openai",
        llm_model="gpt-5.4",
        llm_api_key="",
        llm_use_oauth=True,
        hermes_url="http://127.0.0.1:8642",
    )
    with (
        patch("wizard.app.IRISConfig.load", return_value=cfg),
        patch("wizard.health_check.check_gateway_port", return_value={"ok": False}),
        patch(
            "wizard.hermes_config.read_hermes_dotenv",
            return_value={"OPENAI_API_KEY": "stale-key"},
        ),
        patch("wizard.hermes_config.has_hermes_auth_profile", return_value=False),
    ):
        response = asyncio.run(start_gateway())

    body = json.loads(response.body)
    assert response.status_code == 200
    assert body["code"] == "missing_oauth"


def test_save_selection_rejects_oauth_for_api_only_provider() -> None:
    response = asyncio.run(save_llm_selection(LLMSelectionUpdate(
        provider="google",
        model="gemini-2.5-flash",
        use_oauth=True,
    )))
    body = json.loads(response.body)
    assert response.status_code == 400
    assert body["code"] == "oauth_not_supported"


def test_save_selection_rejects_empty_provider_instead_of_defaulting() -> None:
    response = asyncio.run(save_llm_selection(LLMSelectionUpdate(
        provider="",
        model="claude-sonnet-4-6",
        use_oauth=True,
    )))
    body = json.loads(response.body)
    assert response.status_code == 400
    assert body["code"] == "missing_provider"


def test_save_selection_reports_hermes_write_failure() -> None:
    cfg = SimpleNamespace(
        llm_provider="openrouter",
        llm_model="old-model",
        llm_api_key="",
        llm_use_oauth=False,
        hermes_api_key="",
        save=lambda: None,
    )
    with (
        patch("wizard.app.IRISConfig.load", return_value=cfg),
        patch("wizard.provider_sync.local_hermes_provider_metadata", return_value={
            "openrouter": {"env_vars": (), "oauth_capable": True, "auth_type": ""},
        }),
        patch("wizard.hermes_config.write_hermes_config", side_effect=OSError("disk full")),
    ):
        response = asyncio.run(save_llm_selection(LLMSelectionUpdate(
            provider="openrouter",
            model="new-model",
            api_key="test-key",
            use_oauth=False,
            base_url="https://openrouter.ai/api/v1",
        )))

    body = json.loads(response.body)
    assert response.status_code == 500
    assert body["status"] == "error"
    assert body["code"] == "hermes_config_write_failed"
    assert body["hermes"]["ok"] is False


def test_yaml_writer_never_reports_success_without_pyyaml() -> None:
    real_import = builtins.__import__

    def import_without_yaml(name, *args, **kwargs):
        if name == "yaml":
            raise ImportError("simulated missing dependency")
        return real_import(name, *args, **kwargs)

    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = Path(temp_dir) / "config.yaml"
        with (
            patch("wizard.hermes_config.get_config_yaml_path", return_value=config_path),
            patch("builtins.__import__", side_effect=import_without_yaml),
        ):
            try:
                _write_config_yaml({"model": {"provider": "openai-api"}})
            except RuntimeError as exc:
                assert "PyYAML" in str(exc)
            else:
                raise AssertionError("Missing PyYAML must make the Hermes write fail")

        assert not config_path.exists()


def test_save_selection_missing_key_does_not_overwrite_hermes() -> None:
    cfg = SimpleNamespace(
        llm_provider="openrouter",
        llm_model="working-model",
        llm_api_key="working-key",
        llm_use_oauth=False,
        hermes_api_key="",
        save=lambda: None,
    )
    with (
        patch("wizard.app.IRISConfig.load", return_value=cfg),
        patch("wizard.hermes_config.read_hermes_dotenv", return_value={}),
        patch("wizard.hermes_config.write_hermes_config") as writer,
    ):
        response = asyncio.run(save_llm_selection(LLMSelectionUpdate(
            provider="openai",
            model="gpt-5.4",
            api_key="",
            use_oauth=False,
        )))

    body = json.loads(response.body)
    assert response.status_code == 400
    assert body["code"] == "missing_api_key"
    assert cfg.llm_provider == "openrouter"
    assert cfg.llm_model == "working-model"
    writer.assert_not_called()


def test_save_selection_missing_oauth_does_not_overwrite_hermes() -> None:
    cfg = SimpleNamespace(
        llm_provider="openrouter",
        llm_model="working-model",
        llm_api_key="working-key",
        llm_use_oauth=False,
        hermes_api_key="",
        save=lambda: None,
    )
    with (
        patch("wizard.app.IRISConfig.load", return_value=cfg),
        patch("wizard.hermes_config.has_hermes_auth_profile", return_value=False),
        patch("wizard.hermes_config.write_hermes_config") as writer,
    ):
        response = asyncio.run(save_llm_selection(LLMSelectionUpdate(
            provider="nous",
            model="hermes-4-405b",
            use_oauth=True,
        )))

    body = json.loads(response.body)
    assert response.status_code == 400
    assert body["code"] == "missing_oauth"
    assert cfg.llm_provider == "openrouter"
    assert cfg.llm_model == "working-model"
    writer.assert_not_called()


def test_save_selection_omitted_auth_flag_preserves_existing_oauth() -> None:
    cfg = SimpleNamespace(
        llm_provider="openai",
        llm_model="gpt-5.4",
        llm_api_key="",
        llm_use_oauth=True,
        hermes_api_key="",
        save=lambda: None,
    )
    with (
        patch("wizard.app.IRISConfig.load", return_value=cfg),
        patch("wizard.hermes_config.has_hermes_auth_profile", return_value=True),
        patch("wizard.hermes_config.write_hermes_config", return_value={}) as writer,
    ):
        response = asyncio.run(save_llm_selection(LLMSelectionUpdate(
            provider="openai",
            model="gpt-5.4",
        )))

    assert response.status_code == 200
    assert cfg.llm_use_oauth is True
    assert writer.call_args.kwargs["llm_use_oauth"] is True


def test_save_selection_requires_azure_endpoint_before_overwrite() -> None:
    cfg = SimpleNamespace(
        llm_provider="openrouter",
        llm_model="working-model",
        llm_api_key="working-key",
        llm_use_oauth=False,
        hermes_api_key="",
        save=lambda: None,
    )
    with (
        patch("wizard.app.IRISConfig.load", return_value=cfg),
        patch("wizard.hermes_config.write_hermes_config") as writer,
    ):
        response = asyncio.run(save_llm_selection(LLMSelectionUpdate(
            provider="azure-foundry",
            model="gpt-4o",
            api_key="azure-key",
            base_url="",
        )))

    body = json.loads(response.body)
    assert response.status_code == 400
    assert body["code"] == "missing_base_url"
    assert cfg.llm_provider == "openrouter"
    writer.assert_not_called()


def test_save_selection_rejects_dead_local_runtime_before_overwrite() -> None:
    cfg = SimpleNamespace(
        llm_provider="openrouter",
        llm_model="working-model",
        llm_api_key="working-key",
        llm_use_oauth=False,
        hermes_api_key="",
        save=lambda: None,
    )
    unavailable = {
        "status": "error",
        "code": "local_server_unavailable",
        "message": "Servidor local no disponible.",
    }
    with (
        patch("wizard.app.IRISConfig.load", return_value=cfg),
        patch("wizard.app._local_provider_preflight", return_value=unavailable),
        patch("wizard.hermes_config.write_hermes_config") as writer,
    ):
        response = asyncio.run(save_llm_selection(LLMSelectionUpdate(
            provider="vllm",
            model="local-model",
        )))

    body = json.loads(response.body)
    assert response.status_code == 400
    assert body["code"] == "local_server_unavailable"
    assert cfg.llm_provider == "openrouter"
    writer.assert_not_called()


def test_frontend_never_uses_anthropic_as_an_operational_fallback() -> None:
    source = (Path(__file__).parents[1] / "wizard" / "ui" / "wizard.js").read_text(encoding="utf-8")
    source += (Path(__file__).parents[1] / "wizard" / "ui" / "provider_login_ui.js").read_text(encoding="utf-8")

    # Every operation (including a first run) requires an explicit provider;
    # the recommendation comes from hardware/config analysis, never a brand.
    assert "|| 'anthropic'" not in source
    assert "function selectedLLMProvider()" in source
    assert "function _restoreSavedLLMModelIntoDOM()" in source
    assert "modelSelect.value = '__custom__'" in source
    assert "if (customInput) customInput.value = previous" in source
    assert "_setProviderModelOptions(provider, keepOAuth, sameProvider)" in source
    assert "const keepOAuth = isOAuthOnly || (" in source
    assert "wizardState.oauth_authenticated_provider = provider" in source
    assert "link.rel = 'noopener noreferrer'" in source
    assert 'wrap.innerHTML = `<a href="${url}"' not in source
    assert "wizardState.llm_provider || 'anthropic'" not in source
    assert "savedConfig.llm_provider || 'anthropic'" not in source
    assert "cfg.llm_use_oauth || cfg.has_llm_oauth" not in source
    assert "(hasOAuth || domUseOAuth)" not in source
    assert "key === 'anthropic'" not in source
    assert "Cambiar proveedor / modelo" in source
    assert "await _originalCheckHermes()" in source
    # AWS and external CLIs are labelled as such, never as an API key (llm_setup.js).
    setup = (Path(__file__).parents[1] / "wizard" / "ui" / "llm_setup.js").read_text(encoding="utf-8")
    assert "aws: 'Credenciales de AWS'" in setup and "external: 'CLI externa'" in setup
    assert "IrisLlmSetup.authLabel(primary.auth)" in source


def test_provider_catalog_refreshes_daily_and_repaints_without_reload() -> None:
    source = (Path(__file__).parents[1] / "wizard" / "ui" / "wizard.js").read_text(encoding="utf-8")

    assert "function _catalogSnapshotNeedsRefresh(snapshot)" in source
    assert "snap.catalog_source !== 'installed_inventory'" in source
    assert "startCatalogRefresh({ automatic: true })" in source
    assert "void _enterProviderCatalogSection()" in source
    assert "function _isProviderCatalogSectionActive()" in source
    assert "cancelCatalogRefresh({ resetAutomaticAttempt: true })" in source
    assert "load.then(release, release)" in source
    assert "await loadProvidersFromBackend()" in source
    assert "updateLLMProviderUI(activeProvider)" in source
    assert "const hasSeparateOAuthCatalog = !!LLM_OAUTH_CATALOG[provider]" in source
    assert "LLM_MODELS = {};" in source
    assert "!useDetectedOllama && modelSelect.value === '__custom__'" in source
    assert "isDualAuth\n        && sameProvider" in source
    assert "if (window._catalogRefreshState === run)" in source
    assert "_catalogRefreshGeneration !== run.generation" in source
    assert "fetch('/api/providers', { cache: 'no-store' })" in source
    assert "await loadProviderCatalog()" not in source
    assert "Se conserva la última lista válida" in source
    assert "El stream terminó sin confirmar si el catálogo fue actualizado" in source
    assert "catálogos de modelos" in source
    assert "${evt.providers_count} proveedores" not in source
    oauth_matrix = source[
        source.index("const oauthToggle = document.getElementById('use-oauth')"):
        source.index("// Update API-key help on every switch")
    ]
    key_link_matrix = source[
        source.index("const keyLink = document.getElementById('llm-key-link')"):
        source.index("// Update radio card selection visuals")
    ]
    assert "} else if (isDualAuth) {" in oauth_matrix
    assert "} else if (isDualAuth) {" not in key_link_matrix


def test_provider_refresh_rejects_a_hermes_cache_older_than_one_day() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        cache = Path(temp_dir) / "model_catalog.json"
        cache.write_text(json.dumps({"providers": {"openrouter": {"models": []}}}), encoding="utf-8")
        modified = cache.stat().st_mtime

        with (
            patch("wizard.provider_sync._hermes_cache_path", return_value=cache),
            patch("wizard.provider_sync.time.time", return_value=modified + 86401),
        ):
            assert _read_hermes_cache_or_none() is None

        with (
            patch("wizard.provider_sync._hermes_cache_path", return_value=cache),
            patch("wizard.provider_sync.time.time", return_value=modified + 86399),
        ):
            assert _read_hermes_cache_or_none() is not None


def test_provider_snapshot_write_is_atomic_and_leaves_no_shared_temp_file() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        snapshot = Path(temp_dir) / "provider_catalog_snapshot.json"
        catalog = {"openrouter": [{"id": "model", "name": "Model"}]}
        with patch("wizard.provider_sync._runtime_snapshot_path", return_value=snapshot):
            metadata = _write_runtime_snapshot(
                catalog,
                "Hermes test catalog",
                "installed_inventory",
                provider_metadata={
                    "openrouter": {
                        "auth_type": "api_key",
                        "env_vars": ["OPENROUTER_API_KEY"],
                    },
                },
            )

        saved = json.loads(snapshot.read_text(encoding="utf-8"))
        assert saved["schema"] == _SNAPSHOT_SCHEMA
        assert saved["catalog"] == catalog
        assert saved["provider_metadata"]["openrouter"]["env_vars"] == [
            "OPENROUTER_API_KEY",
        ]
        assert saved["model_count"] == 1
        assert metadata["path"] == str(snapshot)
        assert list(Path(temp_dir).glob("*.tmp")) == []


def test_provider_snapshot_recalculates_inconsistent_model_count() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        snapshot = Path(temp_dir) / "provider_catalog_snapshot.json"
        snapshot.write_text(json.dumps({
            "schema": _SNAPSHOT_SCHEMA,
            "snapshot_date": "2026-08-08",
            "hermes_version": "Hermes test",
            "model_count": "not-a-number",
            "catalog": {
                "openrouter": [{"id": "one"}, {"id": "two"}],
                "ollama": [{"id": "local"}],
            },
        }), encoding="utf-8")

        loaded = _read_snapshot_file(snapshot)
        assert loaded is not None
        _catalog, metadata = loaded
        assert metadata["model_count"] == 3


def test_provider_auth_metadata_reloads_without_spawning_hermes() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        snapshot = Path(temp_dir) / "provider_catalog_snapshot.json"
        with patch("wizard.provider_sync._runtime_snapshot_path", return_value=snapshot):
            _write_runtime_snapshot(
                {"openai-api": [{"id": "gpt-test"}]},
                "Hermes test",
                "installed_inventory",
                provider_metadata={
                    "openai-api": {
                        "auth_type": "api_key",
                        "env_vars": ["OPENAI_API_KEY"],
                    },
                },
            )
            with (
                patch("wizard.provider_sync._provider_meta_cache", None),
                patch("wizard.provider_sync._discover_local_hermes_providers") as probe,
            ):
                metadata = local_hermes_provider_metadata(allow_probe=False)

        assert metadata["openai-api"]["env_vars"] == ("OPENAI_API_KEY",)
        probe.assert_not_called()


def test_fresh_provider_list_replaces_removed_bundled_models() -> None:
    bundled = {
        "anthropic": [
            {"id": "claude-current"},
            {"id": "claude-removed"},
        ],
        "bundled-only": [{"id": "fallback"}],
    }
    fresh = {"anthropic": [{"id": "claude-current", "tags": ["fresh"]}]}

    merged = _merge_catalogs(bundled, fresh)

    assert merged["anthropic"] == fresh["anthropic"]
    assert merged["bundled-only"] == bundled["bundled-only"]


def test_legacy_bundled_snapshot_is_never_treated_as_current_inventory() -> None:
    stale = {"anthropic": [{"id": "claude-removed"}]}
    with (
        patch(
            "wizard.provider_sync.load_snapshot",
            return_value=(stale, {"source": "bundled", "snapshot_schema": 2}),
        ),
        patch.dict("wizard.provider_sync._cache", {}, clear=True),
    ):
        assert asyncio.run(hermes_catalog()) == {}


def test_legacy_manifest_deduplicates_model_ids() -> None:
    catalog = _convert_hermes_manifest({
        "providers": {
            "openrouter": {
                "models": [
                    {"id": "vendor/model"},
                    {"id": "vendor/model", "description": "recommended"},
                ],
            },
        },
    })

    assert [model["id"] for model in catalog["openrouter"]] == ["vendor/model"]


def test_official_hermes_inventory_is_deduplicated_and_preserves_metadata() -> None:
    catalog = _convert_hermes_inventory_payload({
        "providers": [
            {
                "slug": "openai-api",
                "authenticated": True,
                "models": [
                    {
                        "id": "gpt-5.6-sol",
                        "name": "GPT-5.6 Sol",
                        "context": 272000,
                        "input": "text+image",
                        "tags": ["reasoning"],
                    },
                    {"id": "gpt-5.6-sol"},
                    {
                        "id": "gpt-image-only",
                        "name": "Image generator",
                        "output": ["image"],
                    },
                ],
            },
            {"slug": "azure-foundry", "models": []},
        ],
    })

    assert list(catalog) == ["openai-api", "azure-foundry"]
    assert [model["id"] for model in catalog["openai-api"]] == ["gpt-5.6-sol"]
    assert catalog["openai-api"][0]["available"] is True
    assert catalog["openai-api"][0]["context"] == 272000
    assert catalog["openai-api"][0]["input"] == "text+image"
    assert catalog["azure-foundry"] == []


def test_inventory_probe_ignores_stdout_noise_and_never_requires_gateway() -> None:
    payload = {
        "hermes_version": "2026.8.0",
        "providers": [{
            "slug": "minimax-oauth",
            "authenticated": False,
            "models": [{"id": "MiniMax-M3", "name": "MiniMax M3"}],
        }],
    }
    completed = SimpleNamespace(
        returncode=0,
        stdout="Hermes startup notice\nIRIS_HERMES_INVENTORY=" + json.dumps(payload),
        stderr="",
    )
    with (
        patch(
            "wizard.provider_sync._resolve_hermes_python",
            return_value=Path("hermes-python.exe"),
        ),
        patch("wizard.provider_sync._hermes_probe_kwargs", return_value={}),
        patch("wizard.provider_sync.subprocess.run", return_value=completed) as runner,
    ):
        catalog, metadata = _discover_hermes_inventory(refresh=True)

    assert [model["id"] for model in catalog["minimax-oauth"]] == ["MiniMax-M3"]
    assert metadata["hermes_version"] == "2026.8.0"
    assert "-c" in runner.call_args.args[0]


def test_inventory_probe_terminates_hermes_when_refresh_is_cancelled() -> None:
    class FakeProcess:
        args = ["hermes-python.exe", "-c", "probe"]
        returncode = None
        terminated = False

        def terminate(self) -> None:
            self.terminated = True

        def kill(self) -> None:
            self.terminated = True

        def wait(self, timeout=None) -> int:
            self.returncode = -15
            return self.returncode

    process = FakeProcess()
    cancelled = threading.Event()
    cancelled.set()
    with (
        patch(
            "wizard.provider_sync._resolve_hermes_python",
            return_value=Path("hermes-python.exe"),
        ),
        patch("wizard.provider_sync._hermes_probe_kwargs", return_value={}),
        patch("wizard.provider_sync.subprocess.Popen", return_value=process),
    ):
        catalog, metadata = _discover_hermes_inventory(
            refresh=True,
            cancel_event=cancelled,
        )

    assert catalog == {}
    assert metadata["error"] == "cancelled"
    assert process.terminated is True


@pytest.mark.parametrize(
    "stderr",
    [
        "ImportError: cannot import name 'load_picker_context' from 'hermes_cli.inventory'",
        "TypeError: build_model_options_payload() got an unexpected keyword argument 'refresh'",
    ],
)
def test_inventory_probe_recognizes_older_compatible_hermes(stderr: str) -> None:
    completed = SimpleNamespace(returncode=1, stdout="", stderr=stderr)
    with (
        patch(
            "wizard.provider_sync._resolve_hermes_python",
            return_value=Path("hermes-python.exe"),
        ),
        patch("wizard.provider_sync._hermes_probe_kwargs", return_value={}),
        patch("wizard.provider_sync.subprocess.run", return_value=completed),
    ):
        catalog, metadata = _discover_hermes_inventory(refresh=True)

    assert catalog == {}
    assert metadata["inventory_supported"] is False


def test_runtime_snapshot_from_append_only_schema_is_invalidated() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        snapshot = Path(temp_dir) / "provider_catalog_snapshot.json"
        snapshot.write_text(json.dumps({
            "schema": 2,
            "snapshot_date": "2026-08-13",
            "catalog": {"anthropic": [{"id": "claude-3-removed"}]},
        }), encoding="utf-8")

        assert _read_snapshot_file(snapshot) is None


def test_refresh_prefers_installed_hermes_inventory_over_partial_manifest() -> None:
    inventory = {"deepseek": [{
        "key": "deepseek/deepseek-v4-pro", "id": "deepseek-v4-pro",
        "name": "DeepSeek V4 Pro", "input": "text", "context": 0,
        "local": False, "available": False, "missing": False, "tags": [],
    }]}
    with tempfile.TemporaryDirectory() as temp_dir:
        snapshot = Path(temp_dir) / "provider_catalog_snapshot.json"
        with (
            patch(
                "wizard.provider_sync._discover_hermes_inventory",
                return_value=(inventory, {
                    "hermes_version": "test",
                    "providers_count": 1,
                    "provider_metadata": {
                        "deepseek": {
                            "auth_type": "api_key",
                            "env_vars": ("DEEPSEEK_API_KEY",),
                        },
                    },
                }),
            ),
            patch("wizard.provider_sync._runtime_snapshot_path", return_value=snapshot),
            patch("wizard.provider_sync._fetch_remote_catalog") as remote,
            patch.dict("wizard.provider_sync._cache", {}, clear=True),
        ):
            catalog, metadata = asyncio.run(fetch_live_catalog())

    assert catalog == inventory
    assert metadata["source"] == "installed_inventory"
    assert metadata["catalog_source"] == "installed_inventory"
    assert metadata["provider_metadata"]["deepseek"]["env_vars"] == (
        "DEEPSEEK_API_KEY",
    )
    remote.assert_not_called()


def test_broken_supported_inventory_does_not_stamp_partial_manifest_as_fresh() -> None:
    with (
        patch(
            "wizard.provider_sync._discover_hermes_inventory",
            return_value=({}, {"inventory_supported": True, "error": "timeout"}),
        ),
        patch("wizard.provider_sync._fetch_remote_catalog") as remote,
    ):
        with pytest.raises(TimeoutError):
            asyncio.run(fetch_live_catalog())

    remote.assert_not_called()


def test_legacy_manifest_snapshot_keeps_auth_metadata_without_slow_get() -> None:
    raw = {
        "version": 1,
        "updated_at": "2026-08-20T00:00:00Z",
        "providers": {
            "openrouter": {"models": [{"id": "openrouter/auto"}]},
        },
    }
    runtime = {
        "openrouter": {
            "auth_type": "api_key",
            "env_vars": ("OPENROUTER_API_KEY",),
            "oauth_capable": False,
        },
    }
    with tempfile.TemporaryDirectory() as temp_dir:
        snapshot = Path(temp_dir) / "provider_catalog_snapshot.json"
        with (
            patch(
                "wizard.provider_sync._discover_hermes_inventory",
                return_value=({}, {"inventory_supported": False}),
            ),
            patch("wizard.provider_sync._read_hermes_cache_or_none", return_value=raw),
            patch("wizard.provider_sync._read_bundled_for_merge", return_value=({}, {})),
            patch("wizard.provider_sync._discover_local_hermes_providers", return_value=runtime),
            patch("wizard.provider_sync._runtime_snapshot_path", return_value=snapshot),
            patch.dict("wizard.provider_sync._cache", {}, clear=True),
        ):
            _catalog, metadata = asyncio.run(fetch_live_catalog())

        saved = json.loads(snapshot.read_text(encoding="utf-8"))

    assert metadata["source"] == "hermes_cache"
    assert saved["provider_metadata"]["openrouter"]["env_vars"] == [
        "OPENROUTER_API_KEY",
    ]


def test_static_provider_fallback_never_promises_model_ids() -> None:
    payload = providers_payload()
    assert payload["degraded"] is True
    assert payload["selectable_model_count"] == 0
    assert all(provider["models"] == [] for provider in payload["providers"])
    html = (Path(__file__).parents[1] / "wizard" / "ui" / "index.html").read_text(
        encoding="utf-8"
    )
    assert "No se detectaron modelos de Ollama" in html
    assert '<option value="gemma3:4b">' not in html


def test_oauth_stream_rejects_external_browser_origins_before_spawning() -> None:
    with TestClient(app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 50000)) as client:
        response = client.get(
            "/api/auth-provider-stream?provider=anthropic&auth_type=oauth",
            headers={
                "origin": "https://evil.example",
                "sec-fetch-site": "cross-site",
            },
        )

    assert response.status_code == 403
    assert "cross-site" in response.json()["detail"]


def test_provider_warnings_and_help_match_real_auth_modes() -> None:
    assert warning_for("google") is None
    assert warning_for("oc-google-gemini-cli")["level"] == "warn"
    assert "Codex" in warning_for("openai")["title"]
    assert "Claude Code" in warning_for("anthropic")["body"]
    assert get_provider("anthropic")["url"] == "https://platform.claude.com/settings/keys"

    help_source = (Path(__file__).parents[1] / "wizard" / "ui" / "help.html").read_text(encoding="utf-8")
    assert "Google (Gemini)</td><td>API Key</td>" in help_source
    assert "Google (Gemini)</td><td>API Key o OAuth</td>" not in help_source
    assert "Claude Platform API o flujo compatible con Claude Code" in help_source


def test_minimax_oauth_never_claims_to_authorize_tts_or_voice_cloning() -> None:
    provider = get_provider("minimax-oauth")
    hint = str(provider.get("hint") or "")
    assert "OAuth para chat" in hint
    assert "API key de MiniMax aparte" in hint


def test_merged_catalog_only_synthesizes_locally_confirmed_providers() -> None:
    model = {
        "key": "provider/auto", "id": "auto", "name": "Auto",
        "input": "text", "context": 0, "local": False,
        "available": False, "missing": False, "tags": [],
    }
    catalog = {
        "google": [model],
        "openai": [model],
        "openai-codex": [{**model, "key": "openai-codex/gpt-codex", "id": "gpt-codex", "name": "GPT Codex"}],
        "mistral": [model],  # Model group, not a selectable local provider.
    }
    runtime = {
        "gemini": {"env_vars": ("GOOGLE_API_KEY",), "oauth_capable": False},
        "openai-api": {"env_vars": ("OPENAI_API_KEY",), "oauth_capable": False},
        "openai-codex": {"env_vars": (), "oauth_capable": True},
        "deepinfra": {
            "env_vars": ("DEEPINFRA_API_KEY",),
            "oauth_capable": False,
            "auth_type": "api_key",
        },
        "vertex": {"env_vars": (), "oauth_capable": False, "auth_type": "vertex"},
        "moa": {"env_vars": (), "oauth_capable": False, "auth_type": ""},
    }

    async def fake_catalog():
        return catalog

    with (
        patch("wizard.provider_sync.hermes_catalog", side_effect=fake_catalog),
        patch("wizard.provider_sync.local_hermes_provider_metadata", return_value=runtime),
    ):
        payload = asyncio.run(merged_providers_payload())

    providers = {item["id"]: item for item in payload["providers"]}
    assert providers["google"]["auth"] == "api"
    assert providers["openai"]["auth"] == "api+oauth"
    assert providers["openai"]["oauth_models"][0]["value"] == "gpt-codex"
    assert providers["oc-deepinfra"]["models"] == []
    assert "oc-mistral" not in providers
    assert "oc-vertex" not in providers
    assert "oc-moa" not in providers


def test_current_hermes_slugs_replace_static_openai_and_google_models() -> None:
    def entry(slug: str, model_id: str) -> dict[str, object]:
        return {
            "key": f"{slug}/{model_id}", "id": model_id, "name": model_id,
            "input": "text", "context": 0, "local": False,
            "available": False, "missing": False, "tags": [],
        }

    catalog = {
        "openai-api": [entry("openai-api", "gpt-5.6-sol")],
        "openai-codex": [entry("openai-codex", "gpt-5.6-sol")],
        "gemini": [entry("gemini", "gemini-3.6-flash")],
        "qwen-oauth": [],
        "custom": [entry("custom", "active-endpoint-model")],
    }
    runtime = {
        "openai-api": {"env_vars": ("OPENAI_API_KEY",), "oauth_capable": False},
        "openai-codex": {"env_vars": (), "oauth_capable": True},
        "gemini": {"env_vars": ("GOOGLE_API_KEY",), "oauth_capable": False},
        "qwen-oauth": {"env_vars": (), "oauth_capable": True},
        "custom": {"env_vars": (), "oauth_capable": False, "auth_type": "noauth"},
    }

    async def fake_catalog():
        return catalog

    with (
        patch("wizard.provider_sync.hermes_catalog", side_effect=fake_catalog),
        patch("wizard.provider_sync.local_hermes_provider_metadata", return_value=runtime),
    ):
        payload = asyncio.run(merged_providers_payload())

    providers = {item["id"]: item for item in payload["providers"]}
    assert [model["value"] for model in providers["openai"]["models"]] == ["gpt-5.6-sol"]
    assert [model["value"] for model in providers["openai"]["oauth_models"]] == ["gpt-5.6-sol"]
    assert [model["value"] for model in providers["google"]["models"]] == ["gemini-3.6-flash"]
    assert providers["qwen-oauth"]["models"] == []
    assert providers["anthropic"]["models"] == []
    assert [model["value"] for model in providers["custom"]["models"]] == [
        "active-endpoint-model",
    ]
    assert "active-endpoint-model" not in {
        model["value"] for model in providers["ollama-local"]["models"]
    }


# ── A08.9 QA on a clean Windows 11: a first OpenAI Codex login was "not found" ──

def _codex_singleton(auth_mode="chatgpt", tokens=None):
    """The store Hermes 0.21 writes on a first Codex login: no credential_pool entry."""
    return {
        "version": 1,
        "providers": {
            "openai-codex": {
                "tokens": {"access_token": "test-access", "refresh_token": "test-refresh"} if tokens is None else tokens,
                "last_refresh": "2026-10-07T22:23:27.117629Z",
                "auth_mode": auth_mode,
            },
        },
        "active_provider": "openai-codex",
    }


@pytest.mark.parametrize("store, oauth", [
    (_codex_singleton(), True),
    (_codex_singleton(auth_mode="api_key"), False),
    (_codex_singleton(tokens={}), False),
    # Hermes' _read_codex_tokens rejects anything but both tokens as non-empty text.
    (_codex_singleton(tokens={"access_token": "test-access"}), False),
    (_codex_singleton(tokens={"refresh_token": "test-refresh"}), False),
    (_codex_singleton(tokens={"access_token": "  ", "refresh_token": "test-refresh"}), False),
    (_codex_singleton(tokens={"access_token": "test-access", "refresh_token": "\t"}), False),
    (_codex_singleton(tokens={"access_token": 123, "refresh_token": "test-refresh"}), False),
    (_codex_singleton(tokens={"access_token": "test-access", "refresh_token": ["test-refresh"]}), False),
    (_codex_singleton(tokens="test-access"), False),
], ids=["pair", "api-key-mode", "empty", "access-only", "refresh-only", "blank-access", "blank-refresh",
        "number", "list", "not-a-dict"])
def test_a_first_codex_login_kept_as_provider_state_counts_as_oauth(tmp_path, store, oauth) -> None:
    (tmp_path / "auth.json").write_text(json.dumps(store), encoding="utf-8")
    assert has_hermes_auth_profile("openai-codex", auth_type="oauth", home=tmp_path) is oauth
    assert has_hermes_auth_profile("openai-codex", auth_type="api", home=tmp_path) is False
    with patch("wizard.hermes_config.get_hermes_home", return_value=tmp_path):
        assert has_hermes_auth_profile("openai-codex", auth_type="oauth") is oauth


def test_empty_response_diagnostic_sees_a_first_codex_login(tmp_path) -> None:
    (tmp_path / "config.yaml").write_text("model:\n  provider: openai-codex\n  default: gpt-5.6\n", encoding="utf-8")
    (tmp_path / ".env").write_text("API_SERVER_ENABLED=true\n", encoding="utf-8")
    (tmp_path / "auth.json").write_text(json.dumps(_codex_singleton()), encoding="utf-8")
    with patch("core.workspace_sync.get_hermes_home", return_value=tmp_path), \
            patch("wizard.hermes_config.get_hermes_home", side_effect=AssertionError("the bridge passes its home")):
        message = _diagnose_empty_response()
    assert "no veo un perfil guardado" not in message and "no encuentro credenciales" not in message
    assert "autenticado con OAuth" in message


# ── A08.9: the final summary asks what the launch will ask ──

def _saved(provider, *, api_key="", use_oauth=False):
    return SimpleNamespace(llm_provider=provider, llm_model="model", llm_api_key=api_key, llm_use_oauth=use_oauth,
                           hermes_url="http://127.0.0.1:8642")


@pytest.mark.parametrize("cfg, oauth_login, expected", [
    (_saved(""), False, "missing_provider"),
    (_saved("openai"), False, "missing_api_key"),
    (_saved("openai", api_key="test-key"), False, ""),
    (_saved("openai", use_oauth=True), False, "missing_oauth"),
    (_saved("openai", use_oauth=True), True, ""),
], ids=["no-provider", "no-key", "key", "oauth-pending", "oauth-done"])
def test_the_summary_and_the_gateway_agree_on_whether_the_ai_is_ready(cfg, oauth_login, expected) -> None:
    from wizard.app import _ai_readiness

    with (
        patch("wizard.app.IRISConfig.load", return_value=cfg),
        patch("wizard.health_check.check_gateway_port", return_value={"ok": False}),
        patch("wizard.hermes_config.read_hermes_dotenv", return_value={}),
        patch("wizard.hermes_config.has_hermes_auth_profile", return_value=oauth_login),
        patch.dict("os.environ", {"OPENAI_API_KEY": ""}),
    ):
        ai = asyncio.run(_ai_readiness())
        gateway = json.loads(asyncio.run(start_gateway()).body) if expected not in {"", "missing_provider"} else None

    assert ai["ready"] is (expected == "")
    assert ai["code"] == expected
    if gateway is not None:  # the same refusal, word for word, that the launch will show
        assert (gateway["code"], gateway["message"]) == (ai["code"], ai["message"])


def test_the_summary_stays_neutral_when_it_cannot_check_the_provider() -> None:
    from wizard.app import _ai_readiness

    with patch("wizard.app.IRISConfig.load", side_effect=OSError("profile unreadable")):
        ai = asyncio.run(_ai_readiness())
    assert ai == {"ready": None, "code": "unknown", "message": ""}
