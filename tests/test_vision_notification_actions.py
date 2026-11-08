"""Step 4: reversible local choices and explicit, unsent chat drafts."""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from pydantic import ValidationError

from core.config import IRISConfig
from core.notification_actions import NotificationConflictError
from core.vision_assistant import VisionAssistantRuntime
from core.vision_notifications import (
    NotificationActionRequest,
    NotificationInbox,
    NotificationPreferenceRequest,
)
from core.vision_policy import ACTIVITY_FLAGS, VisionPolicySettings

SETTINGS = VisionPolicySettings(long_session_seconds=60)


def signal(runtime, tick, settings=SETTINGS):
    runtime.update_activity(
        {"visible": True, **dict.fromkeys(ACTIVITY_FLAGS, False)},
        surface_id="face",
        monotonic_now=float(tick),
    )
    return runtime.ingest(
        {"present": True, "motion": "still", "confidence": 0.9},
        surface_id="face",
        enabled=True,
        paused_until=0,
        cooldown_minutes=25,
        retention_days=7,
        now=1000.0 + tick,
        monotonic_now=float(tick),
        policy_settings=settings,
    )


@pytest.fixture
def ready(tmp_path):
    runtime = VisionAssistantRuntime(tmp_path / "events.json")
    runtime.set_notification_availability(True, wall=1000.0, tick=0.0)
    for tick in range(0, 62, 2):
        signal(runtime, tick)
    return runtime


def action(runtime, note, name, **fields):
    return NotificationActionRequest(
        id=note["id"],
        action=name,
        confirm=True,
        expected_revision=runtime._inbox.revision,
        **fields,
    )


def pref(runtime, name, **fields):
    return NotificationPreferenceRequest(
        action=name, confirm=True, expected_revision=runtime._inbox.revision, **fields
    )


def snapshot(runtime, tick=63):
    return runtime.notification_snapshot(retention_days=7, wall=1000.0 + tick, tick=float(tick))


def test_dismiss_restore_survives_restart_and_preserves_receipt(ready):
    note = signal(ready, 62)["notification"]
    ready.act_on_notification(
        action(ready, note, "dismiss"), retention_days=7, wall=1063.0, tick=63.0
    )
    assert snapshot(ready)["unread"] == 0
    assert snapshot(ready)["items"][0]["status"] == "dismissed"
    with patch("core.vision_assistant.time.time", return_value=1063.0):
        restarted = VisionAssistantRuntime(ready.event_path)
    assert snapshot(restarted)["items"][0]["status"] == "dismissed"
    restarted.act_on_notification(
        action(restarted, note, "restore"), retention_days=7, wall=1064.0, tick=64.0
    )
    assert snapshot(restarted, 64)["unread"] == 1
    saved = json.loads(restarted.state_path.read_text(encoding="utf-8"))
    assert len(saved["policy"]["receipts"]) == 1
    assert saved["inbox"]["items"][0]["candidate"]["candidate_id"] == note["id"]


@pytest.mark.parametrize("minutes", [10, 30, 60])
def test_snooze_reuses_same_note_and_does_not_consume_budget(ready, minutes):
    note = signal(ready, 62)["notification"]
    ready.act_on_notification(
        action(ready, note, "snooze", minutes=minutes), retention_days=7, wall=1063.0, tick=63.0
    )
    saved = ready.state_path.read_bytes()
    assert snapshot(ready)["items"][0]["status"] == "snoozed"
    assert snapshot(ready)["unread"] == 0
    assert ready._notification_blocks(1064.0)["long_session"] == "notification_snoozed"
    assert snapshot(ready, 63 + minutes * 60 - 1)["unread"] == 0
    due = snapshot(ready, 63 + minutes * 60)
    assert due["unread"] == 1
    assert due["items"][0]["status"] == "active"
    assert due["items"][0]["id"] == note["id"]
    assert ready.state_path.read_bytes() == saved  # read-time resurfacing, no second receipt
    assert snapshot(ready, 0)["items"][0]["status"] == "snoozed"  # backward wall clock


def test_replayed_or_concurrent_mutation_cannot_extend_delay(ready):
    note = signal(ready, 62)["notification"]
    request = action(ready, note, "snooze", minutes=10)

    async def race():
        return await asyncio.gather(
            *[
                asyncio.to_thread(
                    ready.act_on_notification, request, retention_days=7, wall=1063.0, tick=63.0
                )
                for _ in range(2)
            ],
            return_exceptions=True,
        )

    result = asyncio.run(race())
    assert sum(isinstance(item, NotificationConflictError) for item in result) == 1
    with pytest.raises(NotificationConflictError):
        ready.act_on_notification(request, retention_days=7, wall=1200.0, tick=200.0)
    assert ready._inbox.items[0].snoozed_until == 1663.0


def test_muting_is_reversible_and_does_not_change_camera_or_budget(ready):
    ready.set_notification_preferences(pref(ready, "mute", minutes=30), wall=1061.0, tick=61.0)
    result = signal(ready, 62)
    assert result["notification"] is None
    assert result["v2"]["decision"]["reason"] == "notifications_muted"
    assert result["v2"]["budget"]["used_last_hour"] == 0
    assert ready._notification_blocks(2861.0) == {"returned": "notification_kind_disabled"}
    ready.set_notification_preferences(pref(ready, "unmute"), wall=1063.0, tick=63.0)
    assert signal(ready, 64)["notification"] is not None


def test_disabled_high_priority_kind_does_not_starve_other_rule(ready):
    ready.set_notification_preferences(
        pref(ready, "disable_kind", kind="low_head_motion"), wall=1061.0, tick=61.0
    )
    settings = VisionPolicySettings(
        long_session_seconds=60, low_head_session_seconds=60, min_motion_window_seconds=30
    )
    result = signal(ready, 62, settings)
    assert result["notification"]["kind"] == "long_session"
    assert result["v2"]["evaluations"][0]["decision"]["reason"] == "notification_kind_disabled"


def test_clear_retention_and_new_delivery_keep_disabled_preferences(ready):
    ready.set_notification_preferences(
        pref(ready, "disable_kind", kind="low_head_motion"), wall=1061.0, tick=61.0
    )
    signal(ready, 62)
    with patch("core.vision_assistant.time.time", return_value=1063.0):
        ready.clear_notifications()
    assert ready._inbox.preferences.disabled_kinds == ["low_head_motion"]
    ready.notification_snapshot(retention_days=0, wall=1064.0, tick=64.0)
    with patch("core.vision_assistant.time.time", return_value=1064.0):
        restarted = VisionAssistantRuntime(ready.event_path)
    assert restarted._inbox.preferences.disabled_kinds == ["low_head_motion"]
    restarted.set_notification_preferences(
        pref(restarted, "enable_kind", kind="low_head_motion"), wall=1065.0, tick=65.0
    )
    assert restarted._inbox.preferences.disabled_kinds == []


@pytest.mark.parametrize("operation", ["dismiss", "snooze", "mute", "disable_kind"])
def test_failed_action_rolls_back_memory_and_disk(ready, operation):
    note = signal(ready, 62)["notification"]
    before = ready.state_path.read_bytes()
    inbox_before = ready._inbox
    with patch("core.vision_assistant.os.replace", side_effect=OSError("locked")):
        with pytest.raises(OSError):
            if operation in {"dismiss", "snooze"}:
                request = action(
                    ready, note, operation, **({"minutes": 30} if operation == "snooze" else {})
                )
                ready.act_on_notification(request, retention_days=7, wall=1063.0, tick=63.0)
            else:
                request = pref(
                    ready,
                    operation,
                    **({"minutes": 30} if operation == "mute" else {"kind": "long_session"}),
                )
                ready.set_notification_preferences(request, wall=1063.0, tick=63.0)
    assert ready._inbox == inbox_before
    assert ready.state_path.read_bytes() == before


@pytest.mark.parametrize(
    "update",
    [
        {"confirm": False},
        {"confirm": 1},
        {"confirm": "true"},
        {"expected_revision": True},
        {"expected_revision": -1},
        {"minutes": 0},
        {"minutes": True},
        {"minutes": 11},
        {"minutes": "30"},
        {"action": "execute"},
        {"action": "dismiss"},
        {"image": "private"},
    ],
)
def test_action_contract_rejects_ambiguous_or_unsafe_fields(update):
    payload = {
        "id": "a" * 32,
        "action": "snooze",
        "minutes": 30,
        "confirm": True,
        "expected_revision": 0,
    }
    with pytest.raises(ValidationError):
        NotificationActionRequest.model_validate({**payload, **update})


@pytest.mark.parametrize(
    "fields",
    [
        {"action": "disable_kind", "kind": "returned"},
        {"action": "disable_kind"},
        {"action": "unmute", "minutes": 30},
        {"action": "mute", "minutes": True},
        {"action": "mute", "minutes": 30, "kind": "long_session"},
    ],
)
def test_preferences_are_strict(fields):
    with pytest.raises(ValidationError):
        NotificationPreferenceRequest.model_validate(
            {"confirm": True, "expected_revision": 0, **fields}
        )


def test_step3_state_migrates_without_reset(ready):
    note = signal(ready, 62)["notification"]
    old = ready._inbox.model_dump(by_alias=True)
    old.pop("preferences")
    for item in old["items"]:
        item.pop("dismissed_at")
        item.pop("snoozed_until")
    restored = NotificationInbox.model_validate(old)
    assert restored.items[0].candidate.candidate_id == note["id"]
    assert restored.preferences.disabled_kinds == []
    assert restored.items[0].public(wall=1062.0)["status"] == "active"


def test_draft_is_unsent_generic_and_rejects_unavailable_notes(ready):
    note = signal(ready, 62)["notification"]
    before = ready.state_path.read_bytes()
    draft = ready.notification_draft(note["id"], retention_days=7, wall=1063.0)
    assert draft["sent"] is False
    assert draft["contains_observation_data"] is False
    assert not any(word in draft["text"] for word in ["confianza", "cámara", "1062", "0.9"])
    assert ready.state_path.read_bytes() == before
    ready.act_on_notification(
        action(ready, note, "dismiss"), retention_days=7, wall=1063.0, tick=63.0
    )
    with pytest.raises(NotificationConflictError):
        ready.notification_draft(note["id"], retention_days=7, wall=1064.0)
    with pytest.raises(KeyError):
        ready.notification_draft(note["id"], retention_days=0, wall=1064.0)


def test_http_actions_conflict_and_no_agent_dispatch(ready, monkeypatch):
    from core import websocket_server as server

    note = signal(ready, 62)["notification"]
    monkeypatch.setattr(server, "_vision_assistant", ready)
    monkeypatch.setattr(server.IRISConfig, "load", lambda: IRISConfig())
    monkeypatch.setattr(server.app.state, "agent", None, raising=False)
    dispatch = AsyncMock(side_effect=AssertionError("No automatic user_text"))
    monkeypatch.setattr(server, "_handle_user_text", dispatch)

    async def requests():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server.app, client=("127.0.0.1", 5000)),
            base_url="http://127.0.0.1",
        ) as client:
            body = {"id": note["id"], "confirm": True}
            before = ready.state_path.read_bytes()
            response = await client.post("/api/notifications/conversation", json=body)
            assert response.status_code == 200
            assert response.json()["draft"]["sent"] is False
            assert ready.state_path.read_bytes() == before
            request = action(ready, note, "snooze", minutes=10).model_dump()
            assert (await client.post("/api/notifications/action", json=request)).status_code == 200
            assert (await client.post("/api/notifications/action", json=request)).status_code == 409
            assert (
                await client.post("/api/notifications/conversation", json=body)
            ).status_code == 409
            request["expected_revision"] = ready._inbox.revision
            request["id"] = "f" * 32
            assert (await client.post("/api/notifications/action", json=request)).status_code == 404
            response = await client.post(
                "/api/notifications/preferences", json=pref(ready, "mute", minutes=30).model_dump()
            )
            assert response.status_code == 200
            assert response.json()["vision"]["enabled"] is False
            response = await client.post(
                "/api/notifications/preferences",
                json={"action": "unmute", "confirm": 1, "expected_revision": 0},
            )
            assert response.status_code == 422
            response = await client.post(
                "/api/notifications/conversation",
                json=body,
                headers={"Origin": "https://evil.example"},
            )
            assert response.status_code == 403
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server.app, client=("192.168.1.10", 5000)),
            base_url="http://127.0.0.1",
        ) as remote:
            assert (
                await remote.post("/api/notifications/conversation", json=body)
            ).status_code == 403

    with patch("core.vision_assistant.time.time", return_value=1063.0):
        asyncio.run(requests())
    dispatch.assert_not_awaited()
