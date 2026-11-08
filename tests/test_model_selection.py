"""F01: per-conversation model store, inventory, turn plan and outcome rules."""

from __future__ import annotations

import json

import pytest

from core import model_selection as ms


# ── store ────────────────────────────────────────────────────────────────


def test_store_keeps_selection_per_profile_and_conversation(tmp_path):
    store = ms.ConversationModelStore(tmp_path / "models.json")
    store.set_selection(None, "s1", "fake-b", "beta-1")
    store.set_selection("coder", "s1", "fake-a", "alpha-2")

    reloaded = ms.ConversationModelStore(tmp_path / "models.json")
    assert reloaded.selection(None, "s1") == {"provider": "fake-b", "model": "beta-1"}
    assert reloaded.selection("default", "s1") == {"provider": "fake-b", "model": "beta-1"}
    assert reloaded.selection("coder", "s1") == {"provider": "fake-a", "model": "alpha-2"}
    assert reloaded.selection(None, "s2") is None


def test_clearing_returns_the_conversation_to_the_default_and_keeps_its_outcome(tmp_path):
    store = ms.ConversationModelStore(tmp_path / "models.json")
    store.set_selection(None, "s1", "fake-b", "beta-1")
    store.record_outcome(None, "s1", {"status": "completed"})
    store.clear_selection(None, "s1")
    assert store.selection(None, "s1") is None
    assert store.entry(None, "s1")["last"] == {"status": "completed"}
    store.clear_selection(None, "never-seen")  # no-op, no file churn
    assert set(json.loads((tmp_path / "models.json").read_text("utf-8"))["conversations"]) == {"default|s1"}


def test_rotation_copy_carries_the_selection_to_the_new_session(tmp_path):
    store = ms.ConversationModelStore(tmp_path / "models.json")
    store.set_selection(None, "old", "fake-b", "beta-1")
    store.copy(None, "old", "new")
    assert store.selection(None, "new") == {"provider": "fake-b", "model": "beta-1"}
    assert store.selection(None, "old") == {"provider": "fake-b", "model": "beta-1"}
    store.copy(None, "missing", "other")
    assert store.selection(None, "other") is None


def test_store_is_bounded_and_forgets_the_oldest_conversations(tmp_path, monkeypatch):
    monkeypatch.setattr(ms, "_MAX_ENTRIES", 3)
    clock = iter(range(100, 200))
    monkeypatch.setattr(ms.time, "time", lambda: next(clock))
    store = ms.ConversationModelStore(tmp_path / "models.json")
    for index in range(5):
        store.set_selection(None, f"s{index}", "fake-a", "alpha-1")
    kept = json.loads((tmp_path / "models.json").read_text("utf-8"))["conversations"]
    assert sorted(kept) == ["default|s2", "default|s3", "default|s4"]


def test_unreadable_store_falls_back_to_defaults_and_keeps_the_file(tmp_path):
    path = tmp_path / "models.json"
    path.write_text("{not json", encoding="utf-8")
    store = ms.ConversationModelStore(path)
    assert store.selection(None, "s1") is None
    assert (tmp_path / "models.json.corrupt").read_text("utf-8") == "{not json"
    store.set_selection(None, "s1", "fake-a", "alpha-1")
    assert ms.ConversationModelStore(path).selection(None, "s1") == {"provider": "fake-a", "model": "alpha-1"}


def test_stored_values_are_revalidated_on_read(tmp_path):
    path = tmp_path / "models.json"
    path.write_text(json.dumps({"conversations": {
        "default|s1": {"selection": {"provider": "bad provider", "model": "x"}},
        "default|s2": {"selection": {"provider": "fake-a", "model": "line\nbreak"}},
    }}), encoding="utf-8")
    store = ms.ConversationModelStore(path)
    assert store.selection(None, "s1") is None
    assert store.selection(None, "s2") is None


# ── default model ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("yaml_text", "expected"),
    [
        ("model:\n  provider: fake-a\n  default: alpha-1\n", {"provider": "fake-a", "model": "alpha-1"}),
        ("model:\n  provider: fake-a\n  model: alpha-2\n", {"provider": "fake-a", "model": "alpha-2"}),
        ("model: gpt-x\n", {"provider": "", "model": "gpt-x"}),
        ("model:\n  provider: fake-a\n", {"provider": "fake-a", "model": ""}),
        ("other: 1\n", {"provider": "", "model": ""}),
        ("model: [unclosed\n", {"provider": "", "model": ""}),
    ],
)
def test_default_model_is_read_like_the_gateway(tmp_path, monkeypatch, yaml_text, expected):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text(yaml_text, encoding="utf-8")
    assert ms.read_default_model() == expected


def test_named_profile_reads_its_own_config(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text("model:\n  provider: fake-a\n  default: alpha-1\n", encoding="utf-8")
    profile = tmp_path / "profiles" / "coder"
    profile.mkdir(parents=True)
    (profile / "config.yaml").write_text("model:\n  provider: fake-b\n  default: beta-1\n", encoding="utf-8")
    assert ms.read_default_model("coder") == {"provider": "fake-b", "model": "beta-1"}
    assert ms.read_default_model(None) == {"provider": "fake-a", "model": "alpha-1"}


# ── inventory ────────────────────────────────────────────────────────────

OPTIONS = {
    "provider": "fake-a",
    "model": "alpha-1",
    "providers": [
        {"slug": "moa", "name": "Mixture of Agents", "models": ["default"], "is_current": False, "authenticated": True},
        {"slug": "anthropic", "name": "Anthropic", "models": [], "is_current": False, "authenticated": False,
         "source": "canonical", "warning": "paste ANTHROPIC_API_KEY to activate"},
        {"slug": "fake-b", "name": "Fake B", "models": [{"id": "beta-1"}, "beta-1", ""], "is_current": False,
         "authenticated": True},
        {"slug": "fake-a", "name": "Fake A", "models": ["alpha-1", "alpha-2"], "is_current": True, "authenticated": True},
        {"slug": "expired", "name": "Expired", "models": ["e-1"], "is_current": False, "authenticated": False},
        {"slug": "bad slug", "models": ["x"], "authenticated": True},
    ],
}


def test_inventory_keeps_authenticated_providers_with_models():
    inventory = ms.normalize_inventory(OPTIONS)
    assert [row["id"] for row in inventory["providers"]] == ["fake-b", "fake-a"]
    assert inventory["providers"][0]["models"] == ["beta-1"]
    assert inventory["providers"][1]["current"] is True
    assert inventory["default"] == {"provider": "fake-a", "model": "alpha-1"}


@pytest.mark.parametrize("pending", [False, True])
def test_plan_locks_survive_normalization_and_cannot_be_selected(pending):
    inventory = ms.normalize_inventory({"providers": [{"slug": "nous", "authenticated": True,
        "models": ["free", "paid"], "free_tier_pending": pending, "unavailable_models": ["paid"]}]})
    with pytest.raises(ms.SelectionError) as failure:
        ms.validate_selection(inventory, "nous", "paid")
    assert failure.value.code == ("plan_pending" if pending else "model_not_in_plan")
    if not pending:
        ms.validate_selection(inventory, "nous", "free")


def test_inventory_keeps_the_current_provider_even_without_credentials():
    options = {"provider": "openai-codex", "model": "gpt-5", "providers": [
        {"slug": "openai-codex", "name": "OpenAI Codex", "models": ["gpt-5"], "is_current": True,
         "authenticated": False, "warning": "Configured provider is not authenticated"},
    ]}
    row = ms.normalize_inventory(options)["providers"][0]
    assert row["authenticated"] is False and "not authenticated" in row["warning"]
    with pytest.raises(ms.SelectionError) as raised:
        ms.validate_selection(ms.normalize_inventory(options), "openai-codex", "gpt-5")
    assert raised.value.code == "provider_without_credentials"


def test_moa_shows_only_when_it_is_the_current_mode():
    options = {"providers": [{"slug": "moa", "name": "MoA", "models": ["default"], "is_current": True, "authenticated": True}]}
    assert [row["id"] for row in ms.normalize_inventory(options)["providers"]] == ["moa"]


@pytest.mark.parametrize("payload", [None, [], {"providers": "x"}, {"providers": [1, None]}])
def test_inventory_tolerates_malformed_payloads(payload):
    assert ms.normalize_inventory(payload)["providers"] == []


@pytest.mark.parametrize(
    ("provider", "model", "code"),
    [
        ("fake-a", "alpha-9", "unknown_model"),
        ("fake-z", "zeta", "unknown_provider"),
        ("", "alpha-1", "invalid_selection"),
        ("fake-a", "", "invalid_selection"),
        ("fake-a", "x" * 300, "invalid_selection"),
    ],
)
def test_selection_must_be_an_inventory_row(provider, model, code):
    with pytest.raises(ms.SelectionError) as raised:
        ms.validate_selection(ms.normalize_inventory(OPTIONS), provider, model)
    assert raised.value.code == code


def test_valid_selection_passes():
    ms.validate_selection(ms.normalize_inventory(OPTIONS), "fake-b", "beta-1")


# ── turn plan and outcome ────────────────────────────────────────────────


def _service(tmp_path, default=("fake-a", "alpha-1")):
    return ms.ModelSelectionService(
        ms.ConversationModelStore(tmp_path / "models.json"),
        default_reader=lambda profile: {"provider": default[0], "model": default[1]},
    )


def test_custom_conversation_locks_provider_and_model(tmp_path):
    service = _service(tmp_path)
    service.store.set_selection(None, "s1", "fake-b", "beta-1")
    plan = service.turn_plan(None, "s1")
    assert plan["mode"] == "custom"
    assert plan["fields"] == {"provider": "fake-b", "model": "beta-1", "require_model_lock": True}


def test_default_conversation_locks_the_current_default_model_only(tmp_path):
    plan = _service(tmp_path).turn_plan(None, "s1")
    assert plan["mode"] == "default" and plan["provider"] == "fake-a"
    # No provider: equal to the global model, Hermes keeps its global runtime.
    assert plan["fields"] == {"model": "alpha-1", "require_model_lock": True}


def test_without_a_default_model_the_turn_carries_no_lock(tmp_path):
    plan = _service(tmp_path, default=("fake-a", "")).turn_plan(None, "s1")
    assert plan["fields"] == {}


def _runtime(model="beta-1", lock="confirmed"):
    return ms.parse_runtime({"provider": "custom", "model": model, "route_source": "raw_request",
                             "model_lock": lock, "requested": {"provider": "fake-b", "model": "beta-1"}})


def test_outcome_confirms_only_a_completed_turn_on_the_planned_model(tmp_path):
    service = _service(tmp_path)
    service.store.set_selection(None, "s1", "fake-b", "beta-1")
    plan = service.turn_plan(None, "s1")
    assert service.outcome(plan, _runtime(), status="completed")["confirmed"] is True
    assert service.outcome(plan, _runtime(), status="failed", error="404")["confirmed"] is False
    assert service.outcome(plan, _runtime(), status="cancelled")["confirmed"] is False
    assert service.outcome(plan, _runtime(lock="accepted"), status="completed")["confirmed"] is False
    assert service.outcome(plan, _runtime(model="other"), status="completed")["confirmed"] is False
    assert service.outcome(plan, None, status="completed")["confirmed"] is False


def test_outcome_without_a_lock_is_never_confirmed(tmp_path):
    plan = _service(tmp_path, default=("fake-a", "")).turn_plan(None, "s1")
    outcome = ms.ModelSelectionService.outcome(plan, _runtime(), status="completed")
    assert outcome["confirmed"] is False
    assert outcome["effective"]["model"] == "beta-1"


def test_runtime_parser_drops_unsafe_values():
    parsed = ms.parse_runtime({"provider": "x y", "model": "m\nx", "model_lock": "confirmed\x00",
                               "requested": "nope"})
    assert parsed == {"provider": "", "model": "", "route_source": "", "model_lock": "confirmed",
                      "requested": {"provider": "", "model": ""}}
    assert ms.parse_runtime("x") is None


@pytest.mark.parametrize("document", ['{"conversations": ["bad"]}', '{"conversations": "x"}', '[]', '{"schema": 1}'])
def test_store_with_the_wrong_shape_is_set_aside(tmp_path, document):
    path = tmp_path / "models.json"
    path.write_text(document, encoding="utf-8")
    store = ms.ConversationModelStore(path)
    assert store.selection(None, "s1") is None
    assert (tmp_path / "models.json.corrupt").read_text("utf-8") == document
