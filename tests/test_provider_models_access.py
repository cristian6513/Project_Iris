"""Post-auth catalogs: account/method isolation, complete free lists, no credential writes."""
import asyncio
import json
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

from wizard import app as wizard
from wizard import provider_models as catalogs
from wizard.provider_models_probe import PROBE_SOURCE


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("IRIS_HOME", str(tmp_path / "iris"))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    return TestClient(wizard.app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 43123))


def post(client, **body):
    return client.post("/api/provider-models", json=body, headers={"Origin": "http://127.0.0.1:8765"})


@pytest.mark.parametrize("provider,oauth,slug", [("openai", False, "openai-api"), ("openai", True, "openai-codex"),
                                                ("xai", False, "xai"), ("xai", True, "xai-oauth"),
                                                ("opencode-zen", False, "opencode-zen")])
def test_method_selects_exact_adapter_and_never_saves(client, monkeypatch, tmp_path, provider, oauth, slug):
    discover = AsyncMock(return_value={"status": "ok", "source": "hermes", "models": [{"value": "only-this-access", "label": "Model"}]})
    monkeypatch.setattr(catalogs, "discover_models", discover)
    monkeypatch.setattr("wizard.hermes_config.has_hermes_auth_profile", lambda *args, **kwargs: True)
    response = post(client, provider=provider, use_oauth=oauth, api_key="draft-private")
    assert response.status_code == 200
    assert response.json()["provider"] == provider and response.json()["use_oauth"] is oauth
    assert discover.call_args.kwargs["slug"] == slug
    assert discover.call_args.kwargs["api_key"] == ("" if oauth else "draft-private")
    assert "draft-private" not in response.text
    assert response.headers["Cache-Control"] == "no-store, private"
    assert not list(tmp_path.rglob("config.*")) and not list(tmp_path.rglob(".env"))


def test_no_session_no_catalog_and_no_cross_provider_key(client, monkeypatch):
    discover = AsyncMock()
    monkeypatch.setattr(catalogs, "discover_models", discover)
    monkeypatch.setattr("wizard.hermes_config.has_hermes_auth_profile", lambda *a, **kw: False)
    assert post(client, provider="nous", use_oauth=True).json()["code"] == "missing_oauth"
    assert post(client, provider="openrouter", api_key="oc_sk_synthetic_not_a_key").json()["code"] == "invalid_credentials"
    assert post(client, provider="unregistered", api_key="draft").status_code == 400
    discover.assert_not_called()


def test_remote_peer_cannot_query_credentials(client, monkeypatch):
    discover = AsyncMock()
    monkeypatch.setattr(catalogs, "discover_models", discover)
    remote = TestClient(wizard.app, base_url="http://127.0.0.1:8765", client=("192.168.1.20", 43123))
    assert post(remote, provider="openrouter", api_key="draft").status_code == 403
    discover.assert_not_called()


def test_a_different_providers_saved_key_is_never_reused(client, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(wizard.IRISConfig, "load", lambda: SimpleNamespace(llm_provider="openai", llm_api_key="another-private-key"))
    monkeypatch.setattr("wizard.hermes_config.read_hermes_dotenv", lambda: {"OPENAI_API_KEY": "another-private-key"})
    discover = AsyncMock()
    monkeypatch.setattr(catalogs, "discover_models", discover)
    response = post(client, provider="openrouter")
    assert response.status_code == 400 and response.json()["code"] == "missing_api_key"
    assert "another-private-key" not in response.text
    discover.assert_not_called()


def test_saving_nous_uses_the_current_account_instead_of_a_stale_snapshot(client, monkeypatch):
    monkeypatch.setattr("wizard.hermes_config.has_hermes_auth_profile", lambda *a, **kw: True)
    monkeypatch.setattr("wizard.hermes_config.read_provider_base_url", lambda provider: "")
    monkeypatch.setattr("wizard.provider_sync.load_snapshot", lambda: ({"nous": [{"id": "new-free", "locked": True, "lock_reason": "plan"}]}, {}))
    discover = AsyncMock(return_value={"status": "ok", "models": [{"value": "new-free"}]})
    monkeypatch.setattr(catalogs, "discover_models", discover)
    writes = []
    monkeypatch.setattr("wizard.hermes_config.write_hermes_config", lambda **kw: writes.append(kw) or {})
    response = client.post("/api/save-llm-selection", headers={"Origin": "http://127.0.0.1:8765"},
                           json={"provider": "nous", "model": "new-free", "use_oauth": True})
    assert response.status_code == 200 and response.json()["status"] == "saved"
    assert writes[0]["llm_model"] == "new-free"
    discover.assert_awaited_once_with(slug="nous", use_oauth=True)


@pytest.mark.parametrize("endpoint,payload", [
    ("/api/save-llm-selection", {"provider": "nous", "model": "previous", "use_oauth": True}),
    ("/api/save-config", {"assistant_name": "IRIS", "hermes_mode": "full", "llm_provider": "nous", "llm_model": "previous", "llm_use_oauth": True}),
])
def test_unknown_nous_plan_is_not_saved(client, monkeypatch, endpoint, payload):
    monkeypatch.setattr("wizard.hermes_config.has_hermes_auth_profile", lambda *a, **kw: True)
    monkeypatch.setattr(catalogs, "discover_models", AsyncMock(return_value={"status": "error", "code": "plan_unknown"}))
    writes = []
    monkeypatch.setattr("wizard.hermes_config.write_hermes_config", lambda **kw: writes.append(kw) or {})
    response = client.post(endpoint, headers={"Origin": "http://127.0.0.1:8765"}, json=payload)
    assert response.status_code == 400 and response.json()["code"] == "plan_unknown"
    assert not writes


@pytest.fixture
def http_catalog(monkeypatch):
    calls = []
    response = {"status": 200, "data": {"data": []}}
    def handler(request):
        calls.append(request)
        return httpx.Response(response["status"], json=response["data"], headers={"Location": "https://other.example/models"})
    real_client = httpx.AsyncClient
    monkeypatch.setattr(catalogs.httpx, "AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    return response, calls


@pytest.mark.asyncio
async def test_openrouter_lists_all_user_models_including_free_without_inference(http_catalog):
    response, calls = http_catalog
    response["data"] = {"data": [{"id": f"vendor/model-{i}" + (":free" if i % 2 else ""),
                                  "supported_parameters": ["tools"], "pricing": {"prompt": "0" if i % 2 else "1", "completion": "0"}}
                                 for i in range(130)] + [{"id": "image", "architecture": {"output_modalities": ["image"]}},
                                                        {"id": "no-tools", "supported_parameters": []}]}
    result = await catalogs.discover_models(slug="openrouter", api_key="draft", base_url="https://openrouter.ai/api/v1")
    assert result["status"] == "ok" and result["source"] == "account"
    assert len(result["models"]) == 130 and sum(m["free"] for m in result["models"]) == 65
    assert result["models"][0]["free"] is True
    assert str(calls[0].url) == "https://openrouter.ai/api/v1/models/user"
    assert calls[0].headers["authorization"] == "Bearer draft" and calls[0].method == "GET"
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status,code", [(401, "invalid_credentials"), (403, "invalid_credentials"), (302, "catalog_unavailable"), (500, "catalog_unavailable")])
async def test_errors_never_become_a_reference_success_or_follow_redirects(http_catalog, status, code):
    response, calls = http_catalog
    response.update(status=status, data={"error": "Bearer draft private server message"})
    result = await catalogs.discover_models(slug="opencode-zen", api_key="draft", base_url="https://opencode.ai/zen/v1")
    assert result == {"status": "error", "code": code}
    assert len(calls) == 1 and "draft" not in json.dumps(result)


@pytest.mark.asyncio
async def test_zen_public_catalog_does_not_claim_validated_credentials(http_catalog):
    response, calls = http_catalog
    response["data"] = {"data": [{"id": "sample-free"}, {"id": "sample-paid"}]}
    result = await catalogs.discover_models(slug="opencode-zen", api_key="draft", base_url="https://opencode.ai/zen/v1")
    assert result["source"] == "public" and result["models"][0]["value"] == "sample-free"
    assert str(calls[0].url) == "https://opencode.ai/zen/v1/models"


@pytest.mark.asyncio
async def test_openrouter_browser_login_uses_its_pkce_key_and_the_same_complete_catalog(http_catalog, tmp_path, monkeypatch):
    monkeypatch.setattr("wizard.hermes_config.get_hermes_home", lambda: tmp_path)
    (tmp_path / "auth.json").write_text(json.dumps({"credential_pool": {"openrouter": [
        {"auth_type": "api_key", "source": "manual", "access_token": "unrelated-api-key"},
        {"auth_type": "api_key", "source": "manual:openrouter_pkce", "access_token": "browser-key"},
    ]}}), encoding="utf-8")
    response, calls = http_catalog
    response["data"] = {"data": [{"id": "free:free", "supported_parameters": ["tools"]}]}
    result = await catalogs.discover_models(slug="openrouter", use_oauth=True, api_key="ignored", base_url="https://ignored.example")
    assert result["source"] == "account" and len(result["models"]) == 1
    assert calls[0].headers["Authorization"] == "Bearer browser-key"
    assert str(calls[0].url) == "https://openrouter.ai/api/v1/models/user"
    assert "browser-key" not in json.dumps(result)


@pytest.mark.asyncio
async def test_openrouter_browser_method_never_borrows_an_ordinary_api_key(http_catalog, tmp_path, monkeypatch):
    monkeypatch.setattr("wizard.hermes_config.get_hermes_home", lambda: tmp_path)
    (tmp_path / "auth.json").write_text(json.dumps({"credential_pool": {"openrouter": [
        {"auth_type": "api_key", "source": "manual", "access_token": "ordinary-key"},
    ]}}), encoding="utf-8")
    result = await catalogs.discover_models(slug="openrouter", use_oauth=True, api_key="ordinary-key")
    assert result == {"status": "error", "code": "missing_oauth"}
    assert not http_catalog[1]


@pytest.mark.asyncio
async def test_cancelling_private_probe_reaps_process(monkeypatch):
    monkeypatch.setattr("wizard.provider_sync._resolve_hermes_python", lambda: "private-python")
    monkeypatch.setattr("wizard.provider_sync._hermes_python_probe_command", lambda *a: ["private-python", "-c", "code"])
    monkeypatch.setattr("wizard.provider_sync._hermes_probe_kwargs", lambda *a: {"timeout": 40})
    entered = asyncio.Event()
    class Process:
        returncode = None
        killed = False
        async def communicate(self, body):
            assert b'"api_key": "draft"' in body
            entered.set()
            await asyncio.Event().wait()
        def kill(self): self.killed = True
        async def wait(self): self.returncode = -1
    process = Process()
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    task = asyncio.create_task(catalogs.discover_models(slug="openai-api", api_key="draft"))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    assert process.killed and process.returncode == -1
    assert "draft" not in str(spawn.call_args)


@pytest.mark.parametrize("paid,error,transient", [(False, False, False), (True, False, False), (None, False, False), (False, True, False), (False, True, True),
                                                (False, '[SSL: CERTIFICATE_VERIFY_FAILED] private-provider-detail', False)])
def test_nous_uses_fresh_plan_and_recommendations_without_fixed_model_count(monkeypatch, paid, error, transient):
    calls = []
    def module(name, **members):
        obj = ModuleType(name)
        obj.__dict__.update(members)
        monkeypatch.setitem(sys.modules, name, obj)
    def union(ids, pricing, portal, *, force_refresh):
        calls.append((portal, force_refresh))
        return ids + [f"free-{i}:free" for i in range(7)], {"fresh": True}
    module("hermes_cli", __path__=[])
    module("hermes_cli.models", provider_model_ids=lambda slug, **kw: ["paid"],
           partition_nous_models_by_tier=lambda ids, prices, **kw: ([m for m in ids if m.endswith(":free")], ["paid"]),
           union_with_portal_free_recommendations=union, union_with_portal_paid_recommendations=union)
    module("hermes_cli.auth", get_provider_auth_state=lambda slug: {"portal_base_url": "https://portal.example"})
    module("hermes_cli.chat_catalog", without_generation_models=lambda ids: ids)
    account_queries = []
    def account_info(*, force_fresh):
        account_queries.append(force_fresh)
        return SimpleNamespace(logged_in=True, error=error if not transient or len(account_queries) == 1 else False, paid_service_access=paid)
    module("hermes_cli.nous_account", get_nous_portal_account_info=account_info)
    module("hermes_cli.models_pricing", get_pricing_for_provider=lambda slug: {}, nous_policy_allowed_ids=lambda: None,
           restrict_to_nous_policy=lambda ids, *a, **kw: ids)
    namespace = {"__name__": "probe_test"}
    exec(PROBE_SOURCE, namespace)
    result = namespace["discover"]({"slug": "nous", "use_oauth": True})
    assert account_queries == ([True, True] if error else [True])
    if paid is None or (error and not transient):
        reason = "tls_certificate_untrusted" if "certificate_verify_failed" in str(error).lower() else "account_lookup_failed" if error else "access_unknown"
        assert result == {"status": "error", "code": "plan_unknown", "reason": reason}
        assert 'private-provider-detail' not in json.dumps(result)
        assert not calls
    else:
        assert len(result["models"]) == (8 if paid else 7)
        assert ("paid" in [m["value"] for m in result["models"]]) is paid
        assert calls == [("https://portal.example", True)]


def test_nous_tls_failure_has_actionable_sanitized_copy(client, monkeypatch):
    monkeypatch.setattr('wizard.hermes_config.has_hermes_auth_profile', lambda *a, **kw: True)
    discover = AsyncMock(return_value={'status': 'error', 'code': 'plan_unknown', 'reason': 'tls_certificate_untrusted'})
    monkeypatch.setattr(catalogs, 'discover_models', discover)
    response = post(client, provider='nous', use_oauth=True)
    assert response.status_code == 200
    result = response.json()
    assert result['status'] == 'error'
    assert result['code'] == 'plan_unknown'
    assert 'certificado HTTPS' in result['message']
    assert 'La sesión está iniciada' in result['message']
    assert 'Nous no respondió' not in result['message']
