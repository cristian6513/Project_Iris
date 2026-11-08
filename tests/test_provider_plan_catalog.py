"""QA12 Q03/Q04: the model list keeps every model and shows what the account's plan allows.

Q03: providers Hermes discovers kept only ten models; the rest vanished from the wizard.
Q04: a free Nous account saw the paid models as usable, and not the free ones Hermes offers
after signing in. Synthetic rows and fake Hermes modules; no account, no network.
"""

from __future__ import annotations

import re
import sys
import types
from unittest.mock import patch, AsyncMock

import pytest
from fastapi.testclient import TestClient

from wizard import app as wizard
from wizard import provider_sync
from wizard.provider_sync import _convert_hermes_inventory_payload
from wizard.providers import _authoritative_provider_models, _synthesize_discovered_provider

LOCAL = {"base_url": "http://127.0.0.1:8765", "client": ("127.0.0.1", 50123)}
ORIGIN = {"Origin": LOCAL["base_url"]}


def nous_row(**plan):
    return {"providers": [{"slug": "nous", "authenticated": True, **plan,
                           "models": [{"id": "free-model"}, {"id": "paid-model"}]}]}


def test_a_free_plan_keeps_paid_models_listed_but_locked():
    rows = {m["id"]: m for m in _convert_hermes_inventory_payload(
        nous_row(free_tier=True, unavailable_models=["paid-model"]))["nous"]}
    assert rows["free-model"]["available"] is True and "locked" not in rows["free-model"]
    assert rows["paid-model"]["available"] is False
    assert (rows["paid-model"]["locked"], rows["paid-model"]["lock_reason"]) == (True, "plan")


def test_an_unknown_plan_locks_every_model_until_it_is_checked():
    rows = _convert_hermes_inventory_payload(nous_row(free_tier_pending=True, unavailable_models=["free-model", "paid-model"]))["nous"]
    assert all(m["locked"] and m["lock_reason"] == "pending" and not m["available"] for m in rows)


def test_nous_models_cannot_be_selected_before_sign_in():
    payload = nous_row()
    payload["providers"][0]["authenticated"] = False
    rows = _convert_hermes_inventory_payload(payload)["nous"]
    assert all(m["locked"] and m["lock_reason"] == "auth" and not m["available"] for m in rows)


def test_a_paid_plan_or_another_provider_has_no_locks():
    rows = _convert_hermes_inventory_payload(nous_row(free_tier=False, unavailable_models=[]))["nous"]
    assert all(m["available"] and "locked" not in m for m in rows)


def test_the_lock_reaches_the_wizard_payload():
    hermes = _convert_hermes_inventory_payload(nous_row(free_tier=True, unavailable_models=["paid-model"]))["nous"]
    static = [{"value": "paid-model", "label": "Paid", "tier": "premium", "locked": True}]
    merged = {m["value"]: m for m in _authoritative_provider_models(static, hermes)}
    assert merged["paid-model"]["locked"] is True and merged["paid-model"]["lock_reason"] == "plan"
    assert "locked" not in merged["free-model"]
    # A curated entry never keeps a stale lock once Hermes says the model is usable.
    open_rows = _convert_hermes_inventory_payload(nous_row())["nous"]
    assert "locked" not in {m["value"]: m for m in _authoritative_provider_models(static, open_rows)}["paid-model"]


def test_a_discovered_provider_keeps_all_its_models():
    models = [{"id": f"model-{i:02}", "name": f"Model {i:02}", "context": 1000, "input": "text", "tags": [],
               "available": True, "missing": False} for i in range(25)]
    models[3]["locked"], models[3]["lock_reason"] = True, "plan"
    row = _synthesize_discovered_provider("opencode", models, {"label": "OpenCode", "env_vars": ["EXAMPLE_KEY"]})
    assert sorted(m["value"] for m in row["models"]) == sorted(m["id"] for m in models)
    assert {m["value"]: m for m in row["models"]}["model-03"]["locked"] is True


def _probe_function(name):
    source = provider_sync.__loader__.get_source(provider_sync.__name__)
    probe = source[source.index("    probe = r'''"):]
    start = probe.index(f"def {name}(")
    end = probe.index("\n\n\n", start)
    return probe[start:end]


@pytest.fixture
def fake_hermes(monkeypatch):
    """Just the Hermes 0.21.6 functions the probe calls after a Nous sign-in."""
    calls = {}
    pricing = {"free-model": {"prompt": "0"}, "paid-model": {"prompt": "1"}}

    def union(tier):
        def add(ids, prices, portal, *, force_refresh=False):
            calls["union"] = (tier, portal)
            calls["recommendations_refreshed"] = force_refresh
            return list(ids) + [f"portal/{tier}-pick:free" if tier == "free" else "portal/paid-pick"], dict(prices)
        return add

    def account_info(force_fresh):
        calls["fresh"] = force_fresh
        if calls.get("failure"):
            raise OSError("account service unavailable")
        return types.SimpleNamespace(logged_in=not calls.get("signed_out"),
            error="unavailable" if calls.get("error") else None,
            paid_service_access=None if calls.get("unknown") else not calls["free"])

    modules = {
        "hermes_cli.nous_account": types.SimpleNamespace(get_nous_portal_account_info=account_info),
        "hermes_cli.auth": types.SimpleNamespace(get_provider_auth_state=lambda slug: {"portal_base_url": "https://portal.example"}),
        "hermes_cli.models": types.SimpleNamespace(
            check_nous_free_tier=lambda force_fresh: calls.setdefault("fresh", force_fresh) and calls["free"],
            partition_nous_models_by_tier=lambda ids, prices, free_tier: (
                [m for m in ids if m.endswith(":free") or m == "free-model"],
                [m for m in ids if not (m.endswith(":free") or m == "free-model")]),
            union_with_nous_on_sale_models=lambda ids, prices: list(ids) + ["portal/on-sale"],
            union_with_portal_free_recommendations=union("free"),
            union_with_portal_paid_recommendations=union("paid"),
        ),
        "hermes_cli.models_pricing": types.SimpleNamespace(
            get_pricing_for_provider=lambda slug: pricing, nous_policy_allowed_ids=lambda: None,
            restrict_to_nous_policy=lambda ids, allowed, rescue_empty: ids),
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setitem(sys.modules, "hermes_cli", types.SimpleNamespace())
    namespace = {}
    exec(_probe_function("nous_plan"), namespace)
    return namespace["nous_plan"], calls


def test_a_free_nous_account_gets_the_free_models_hermes_offers_first(fake_hermes):
    nous_plan, calls = fake_hermes
    calls["free"] = True
    plan = nous_plan(["paid-model", "free-model"])
    assert calls["fresh"] is True and calls["union"] == ("free", "https://portal.example")
    assert calls["recommendations_refreshed"] is True
    assert plan["free_tier"] is True
    assert plan["models"] == ["free-model", "portal/free-pick:free", "paid-model"]
    assert plan["unavailable_models"] == ["paid-model"]


def test_a_paid_nous_account_also_sees_the_models_on_sale(fake_hermes):
    nous_plan, calls = fake_hermes
    calls["free"] = False
    plan = nous_plan(["paid-model", "free-model"])
    assert plan == {"models": ["paid-model", "free-model", "portal/paid-pick", "portal/on-sale"],
                    "free_tier": False, "unavailable_models": []}


@pytest.mark.parametrize("flag", ["unknown", "error", "signed_out", "failure"])
def test_an_unknown_nous_plan_is_not_assumed_to_be_a_paid_account(fake_hermes, flag):
    nous_plan, calls = fake_hermes
    calls["free"], calls[flag] = True, True
    result = nous_plan(["free-model", "paid-model"])
    assert result["free_tier_pending"] is True
    assert result["unavailable_models"] == ["free-model", "paid-model"]


def test_a_free_account_without_pricing_is_not_shown_all_paid_models(fake_hermes, monkeypatch):
    nous_plan, calls = fake_hermes
    calls["free"] = True
    monkeypatch.setattr(sys.modules["hermes_cli.models_pricing"], "get_pricing_for_provider", lambda slug: {})
    assert nous_plan(["paid-model"])["free_tier_pending"] is True


def test_the_plan_check_runs_only_on_a_refresh_of_a_signed_in_nous_row():
    source = provider_sync.__loader__.get_source(provider_sync.__name__)
    assert 'if slug == "nous" and source_row.get("authenticated") and __REFRESH__:' in source
    assert re.search(r'"models": converted,\n\s+\*\*plan,', source)


@pytest.mark.parametrize("model, code", [("paid-model", 400), ("free-model", None)])
def test_saving_a_model_outside_the_plan_is_refused(tmp_path, monkeypatch, model, code):
    monkeypatch.setenv("IRIS_HOME", str(tmp_path / "iris"))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    catalog = _convert_hermes_inventory_payload(nous_row(free_tier=True, unavailable_models=["paid-model"]))
    with patch("wizard.provider_sync.load_snapshot", return_value=(catalog, {})):
        error = wizard._plan_locked_model_error("nous", model, True)
        if code is None:
            assert error is None
            return
        assert error["code"] == "model_not_in_plan"
        with patch("wizard.hermes_config.has_hermes_auth_profile", return_value=True), \
             patch("wizard.provider_models.discover_models", new=AsyncMock(return_value={"status": "ok", "models": [{"value": "free-model"}]})):
            response = TestClient(wizard.app, **LOCAL).post(
                "/api/save-llm-selection", headers=ORIGIN,
                json={"provider": "nous", "model": model, "use_oauth": True})
    assert response.status_code == code
    assert response.json()["code"] == "model_not_in_plan"
    assert not (tmp_path / "hermes" / "config.yaml").exists()
