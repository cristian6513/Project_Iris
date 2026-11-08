"""F07: the general inbox envelope and its projections, with fixtures only."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.automation_contracts import AutomationRule, new_id
from core.automation_store import AutomationStore
from core.inbox_contracts import InboxDelivery, InboxItem, from_automation, from_vision, inbox_key

NOW = 1_800_000_000.0


def item(**overrides):
    source_id = overrides.pop("source_id", "a" * 32)
    origin = overrides.pop("origin", "automation")
    profile = overrides.pop("profile", "default")
    data = {"schema": "iris.inbox-item/v1", "key": inbox_key(origin, profile, source_id), "origin": origin,
            "profile": profile, "source_id": source_id, "title": "Investigar al irme", "text": "Resumen listo.",
            "outcome": "completed", "created_at": NOW, "updated_at": NOW, **overrides}
    return InboxItem.model_validate(data)


def test_identity_is_composite_and_checked():
    a = item(origin="automation")
    b = item(origin="vision", outcome="notice")
    c = item(profile="coder")
    assert len({a.key, b.key, c.key}) == 3  # same id, three different items
    with pytest.raises(ValidationError):
        item(key="automation:default:" + "b" * 32)  # a key that does not match its parts


@pytest.mark.parametrize("origin", ["gmail", "remote"])
def test_reserved_and_unknown_origins_are_refused(origin):
    with pytest.raises(ValidationError):
        item(origin=origin)


def test_the_envelope_is_strict_and_delivery_facts_are_separate():
    with pytest.raises(ValidationError):
        item(extra="x")
    with pytest.raises(ValidationError):
        InboxDelivery(status="read")  # read needs when
    with pytest.raises(ValidationError):
        InboxDelivery(status="snoozed")  # snoozed needs until when
    failed_unread = item(outcome="failed", delivery={"status": "pending"})
    assert failed_unread.outcome == "failed" and failed_unread.delivery.status == "pending"


@pytest.fixture
def store(tmp_path):
    clock = {"now": NOW}
    store = AutomationStore(tmp_path / "automations.sqlite3", clock=lambda: clock["now"])
    rule = store.create_rule(AutomationRule.model_validate({
        "id": new_id(), "title": "Investigar al irme", "enabled": True,
        "trigger": {"source": "iris_idle", "threshold_s": 300}, "execution": {"prompt": "Tarea sintética"},
        "created_at": NOW, "updated_at": NOW}))
    return store, rule


def occurrence_in(store, rule, key, state):
    occurrence, _ = store.record_candidate(rule.id, 1, key)
    if state == "candidate":
        return occurrence
    claimed = store.claim(occurrence.id, "core-1")
    if state == "claimed":
        return claimed
    if state == "cancelled":
        return store.advance(occurrence.id, "cancelled", token=claimed.claim.token, error="regla editada")
    store.admit(occurrence.id, claimed.claim.token, body={"input": "x"}, idempotency_key="k-" + occurrence.id,
                context={}, max_parallel=5, quota_since=0)
    if state in ("running", "ambiguous", "stopping"):
        return store.advance(occurrence.id, state)
    return store.get_occurrence(occurrence.id)


@pytest.mark.parametrize("state", ["candidate", "claimed", "cancelled"])
def test_unsent_occurrences_never_reach_the_inbox(store, state):
    store, rule = store
    occurrence = occurrence_in(store, rule, f"k:{state}", state)
    assert from_automation(profile="default", rule_title=rule.title, occurrence=occurrence) is None


@pytest.mark.parametrize("state, outcome", [("submitted", "running"), ("running", "running"),
                                            ("ambiguous", "unknown"), ("stopping", "unknown")])
def test_unfinished_work_is_shown_with_its_real_state(store, state, outcome):
    store, rule = store
    occurrence = occurrence_in(store, rule, f"k:{state}", state)
    projected = from_automation(profile="default", rule_title=rule.title, occurrence=occurrence)
    assert projected.outcome == outcome and projected.delivery.status == "pending"
    assert "Completada" not in projected.text and projected.text  # never a success it does not know


@pytest.mark.parametrize("status", ["completed", "failed"])
def test_a_finished_occurrence_projects_its_stored_result_and_delivery(store, status):
    store, rule = store
    occurrence = occurrence_in(store, rule, f"k:{status}", "running")
    finished, result, delivery = store.finish(occurrence.id, status=status, text=f"Texto {status}")
    projected = from_automation(profile="default", rule_title=rule.title, occurrence=finished, result=result,
                                delivery=delivery)
    assert projected.key == f"automation:default:{finished.id}"
    assert projected.outcome == status and projected.text == f"Texto {status}"
    assert projected.delivery.status == "pending" and projected.source_revision == 1
    read = store.mark_delivery(result.id, "read")
    again = from_automation(profile="default", rule_title=rule.title, occurrence=finished, result=result, delivery=read)
    assert again.delivery.status == "read" and again.delivery.read_at == read.read_at


def test_vision_notices_are_wrapped_not_copied():
    notice = {"schema": "iris.notification/v1", "source": "vision", "id": "c" * 32, "title": "Pausa activa",
              "body": "Llevas 45 minutos.", "unread": True, "status": "active", "created_at": NOW}
    wrapped = from_vision(profile="default", notice=notice)
    assert wrapped.origin == "vision" and wrapped.outcome == "notice" and wrapped.delivery.status == "pending"
    assert wrapped.key == "vision:default:" + "c" * 32
    assert from_vision(profile="default", notice={**notice, "source": "hermes"}) is None  # never a Hermes result
    snoozed = from_vision(profile="default", notice={**notice, "status": "snoozed", "snoozed_until": NOW + 600})
    assert snoozed.delivery.status == "snoozed" and snoozed.delivery.snoozed_until == NOW + 600
    assert from_vision(profile="default", notice={**notice, "status": "snoozed"}) is None  # no until: not invented
