"""Durable silent delivery: notification and budget share one atomic checkpoint."""

import asyncio
import json
from unittest.mock import patch

import httpx
import pytest
from pydantic import ValidationError

from core.config import IRISConfig
from core.vision_assistant import VisionAssistantRuntime
from core.vision_notifications import NotificationAvailabilityRequest, NotificationInbox
from core.vision_policy import ACTIVITY_FLAGS, VisionPolicySettings

FREE = {"visible": True, **dict.fromkeys(ACTIVITY_FLAGS, False)}
POLICY = VisionPolicySettings(long_session_seconds=60)


def signal(runtime, tick, *, activity=FREE, retention=7):
    runtime.update_activity(activity, surface_id="face", monotonic_now=float(tick))
    return runtime.ingest(
        {"present": True, "motion": "still", "confidence": 0.9},
        surface_id="face",
        enabled=True,
        paused_until=0,
        cooldown_minutes=25,
        retention_days=retention,
        now=1000.0 + tick,
        monotonic_now=float(tick),
        policy_settings=POLICY,
    )


def prepare(tmp_path, *, available=True):
    runtime = VisionAssistantRuntime(tmp_path / "events.json")
    if available:
        runtime.set_notification_availability(True, wall=1000.0, tick=0.0)
    for tick in range(0, 62, 2):
        signal(runtime, tick)
    return runtime


def inbox(runtime, tick=62.0, retention=7):
    return runtime.notification_snapshot(retention_days=retention, wall=1000.0 + tick, tick=tick)


def test_atomic_silent_delivery_survives_restart(tmp_path):
    runtime = prepare(tmp_path)
    result = signal(runtime, 62)
    note = result["notification"]
    assert note["delivery"] == "inbox"
    assert result["v2"]["decision"]["outcome"] == "delivered"
    assert result["v2"]["budget"]["used_last_hour"] == 1
    assert result["event"] is None
    assert inbox(runtime)["unread"] == 1
    saved = json.loads(runtime.state_path.read_text(encoding="utf-8"))
    assert saved["inbox"]["items"][0]["candidate"]["candidate_id"] == note["id"]
    assert saved["policy"]["receipts"][0]["candidate_id"] == note["id"]
    for tick in (64, 66, 68):
        assert signal(runtime, tick)["notification"] is None
    with patch("core.vision_assistant.time.time", return_value=1070.0):
        restarted = VisionAssistantRuntime(runtime.event_path)
    recovered = restarted.notification_snapshot(retention_days=7, wall=1070.0, tick=0)
    assert recovered["items"][0]["id"] == note["id"]
    assert recovered["availability"]["available"] is False


def test_disk_failure_rolls_back_then_retries(tmp_path):
    runtime = prepare(tmp_path)
    previous = runtime.state_path.read_bytes()
    with patch("core.vision_assistant.os.replace", side_effect=OSError("locked")):
        result = signal(runtime, 62)
    assert result["notification"] is None
    assert result["v2"]["decision"]["reason"] == "notification_storage"
    assert runtime._policy.checkpoint(wall=1062.0, tick=62)["receipts"] == []
    assert runtime._inbox.items == []
    assert runtime.state_path.read_bytes() == previous
    assert not list(tmp_path.glob("*.tmp"))
    for tick in range(64, 122, 2):
        assert signal(runtime, tick)["notification"] is None
    assert signal(runtime, 122)["notification"] is not None


@pytest.mark.parametrize(
    "content",
    [
        "{",
        "[]",
        '{"schema":"future/v9"}',
        '{"schema":"iris.vision-runtime/v2","inbox":{"items":[]}}',
        '{"schema":"iris.vision-runtime/v2","policy":{"audio":"raw"}}',
    ],
)
def test_corrupt_state_preserved_and_delivery_closed(tmp_path, content):
    path = tmp_path / "events_state.json"
    path.write_text(content, encoding="utf-8")
    runtime = prepare(tmp_path)
    result = signal(runtime, 62)
    assert result["notification"] is None
    assert result["v2"]["decision"]["reason"] == "notification_storage"
    assert inbox(runtime)["storage_available"] is False
    assert path.read_text(encoding="utf-8") == content


def test_read_and_retention_preserve_budget(tmp_path):
    runtime = prepare(tmp_path)
    note = signal(runtime, 62)["notification"]
    runtime.update_notification(note["id"], read=True, wall=1062.0, tick=62)
    assert inbox(runtime)["unread"] == 0
    revision = runtime._inbox.revision
    runtime.update_notification(note["id"], read=True, wall=1063.0, tick=63)
    assert runtime._inbox.revision == revision
    runtime.update_notification(note["id"], read=False, wall=1064.0, tick=64)
    assert inbox(runtime, 64)["unread"] == 1
    assert inbox(runtime, 64, retention=0)["items"] == []
    saved = json.loads(runtime.state_path.read_text(encoding="utf-8"))
    assert saved["inbox"]["items"] == []
    assert saved["policy"]["receipts"][0]["candidate_id"] == note["id"]
    with pytest.raises(KeyError):
        runtime.update_notification("f" * 32, read=True, wall=1064.0, tick=64)


def test_read_failure_preserves_unread(tmp_path):
    runtime = prepare(tmp_path)
    note = signal(runtime, 62)["notification"]
    with patch("core.vision_assistant.os.replace", side_effect=OSError("locked")):
        with pytest.raises(OSError):
            runtime.update_notification(note["id"], read=True, wall=1063.0, tick=63)
    assert inbox(runtime, 63)["unread"] == 1


def test_stale_runtime_cannot_overwrite_newer_inbox(tmp_path):
    first = prepare(tmp_path)
    stale = VisionAssistantRuntime(first.event_path)
    note = signal(first, 62)["notification"]
    with pytest.raises(OSError, match="Otra instancia"):
        stale.clear_notifications()
    saved = json.loads(first.state_path.read_text(encoding="utf-8"))
    assert saved["inbox"]["items"][0]["candidate"]["candidate_id"] == note["id"]


def test_concurrent_signals_deliver_once(tmp_path):
    runtime = prepare(tmp_path)

    async def race():
        return await asyncio.gather(*[asyncio.to_thread(signal, runtime, 62) for _ in range(4)])

    results = asyncio.run(race())
    assert sum(result["notification"] is not None for result in results) == 1
    assert len(runtime._inbox.items) == 1


@pytest.mark.parametrize(
    "available,retention,reason",
    [
        (False, 7, "availability_required"),
        (True, 0, "history_disabled"),
    ],
)
def test_gates_do_not_spend_budget(tmp_path, available, retention, reason):
    runtime = prepare(tmp_path, available=available)
    result = signal(runtime, 62, retention=retention)
    assert result["notification"] is None
    assert result["v2"]["decision"]["reason"] == reason
    assert inbox(runtime)["items"] == []


def test_availability_expires_and_never_overrides_positive_evidence(tmp_path):
    runtime = VisionAssistantRuntime(tmp_path / "events.json")
    runtime.set_notification_availability(True, wall=1000.0, tick=0.0)
    runtime._policy_clock = (1000.0, 0.0)
    runtime.update_activity({"visible": True}, surface_id="face", monotonic_now=0)
    assert runtime._activity_context(("face",), 0).meeting is False
    runtime.update_activity({"visible": True, "typing": True}, surface_id="other", monotonic_now=0)
    assert runtime._activity_context(("face", "other"), 0).typing is True
    assert runtime._activity_context(("face",), 9).visible is None
    assert runtime.availability(wall=1900.0, tick=900.0)["available"] is False
    runtime.set_notification_availability(True, wall=2000.0, tick=1000.0)
    assert runtime.availability(wall=2200.0, tick=1002.0)["available"] is False
    runtime.set_notification_availability(True, wall=3000.0, tick=2000.0)
    runtime.reset_observation()
    assert runtime.availability(wall=3000.0, tick=2000.0)["available"] is False


@pytest.mark.parametrize(
    "payload",
    [
        {"available": True},
        {"available": True, "confirm": False},
        {"available": True, "confirm": 1},
        {"available": "true", "confirm": True},
        {"available": True, "confirm": True, "image": "private"},
    ],
)
def test_confirmation_is_strict(payload):
    with pytest.raises(ValidationError):
        NotificationAvailabilityRequest.model_validate(payload)


def test_inbox_rejects_media():
    with pytest.raises(ValidationError):
        NotificationInbox.model_validate({"audio": "raw"})


def test_http_is_local_and_never_enables_vision(tmp_path, monkeypatch):
    from core import websocket_server as server

    runtime = VisionAssistantRuntime(tmp_path / "events.json")
    monkeypatch.setattr(server, "_vision_assistant", runtime)
    monkeypatch.setattr(server.IRISConfig, "load", lambda: IRISConfig())
    monkeypatch.setattr(server.app.state, "agent", None, raising=False)

    async def requests():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server.app, client=("127.0.0.1", 5000)),
            base_url="http://127.0.0.1",
        ) as client:
            response = await client.get("/api/notifications")
            assert response.status_code == 200
            assert response.json()["items"] == []
            assert response.json()["vision"]["enabled"] is False
            response = await client.post(
                "/api/notifications/availability", json={"available": True, "confirm": True}
            )
            assert response.status_code == 409
            response = await client.post("/api/notifications/clear", json={"confirm": False})
            assert response.status_code == 422
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server.app, client=("192.168.1.10", 5000)),
            base_url="http://127.0.0.1",
        ) as remote:
            assert (await remote.get("/api/notifications")).status_code == 403

    asyncio.run(requests())
    assert not runtime.state_path.exists()


def test_inbox_is_bounded_and_zero_retention_keeps_future_clock_rows_out(tmp_path):
    runtime = prepare(tmp_path)
    signal(runtime, 62)
    original = runtime._inbox.items[0]
    notes = [
        original.model_copy(
            update={
                "candidate": original.candidate.model_copy(update={"candidate_id": f"{i:032x}"})
            }
        )
        for i in range(105)
    ]
    bounded = NotificationInbox().changed(notes)
    assert len(bounded.items) == 100
    assert bounded.items[0].candidate.candidate_id == f"{5:032x}"
    assert bounded.retained(days=7, wall=0.0) == bounded.items
    assert bounded.retained(days=0, wall=0.0) == []


def test_corruption_after_start_is_not_overwritten(tmp_path):
    runtime = prepare(tmp_path)
    signal(runtime, 62)
    saved = json.loads(runtime.state_path.read_text(encoding="utf-8"))
    saved["policy"] = {"unexpected": "damaged"}
    content = json.dumps(saved)
    runtime.state_path.write_text(content, encoding="utf-8")
    with pytest.raises(OSError, match="no es verificable"):
        runtime.clear_notifications()
    assert runtime.state_path.read_text(encoding="utf-8") == content
    assert not inbox(runtime)["storage_available"]


def test_receipts_and_notes_load_from_same_file_version(tmp_path, monkeypatch):
    runtime = prepare(tmp_path)
    before = runtime.state_path.read_text(encoding="utf-8")
    note = signal(runtime, 62)["notification"]
    after = runtime.state_path.read_text(encoding="utf-8")
    path_type = type(runtime.state_path)
    original = path_type.read_text
    reads = []

    def read(path, *args, **kwargs):
        if path == runtime.state_path:
            reads.append(path)
            return before if len(reads) <= 2 else after
        return original(path, *args, **kwargs)

    monkeypatch.setattr(path_type, "read_text", read)
    restarted = VisionAssistantRuntime(runtime.event_path)
    assert restarted._inbox.items[0].candidate.candidate_id == note["id"]
    receipt = restarted._policy.checkpoint(wall=1062.0, tick=62)["receipts"][0]
    assert receipt["candidate_id"] == note["id"]


def test_http_read_clear_and_history_reset_broadcasts(tmp_path, monkeypatch):
    from core import websocket_server as server

    runtime = prepare(tmp_path)
    note = signal(runtime, 62)["notification"]
    monkeypatch.setattr(server, "_vision_assistant", runtime)
    monkeypatch.setattr(server.IRISConfig, "load", lambda: IRISConfig())
    monkeypatch.setattr(server.app.state, "agent", None, raising=False)
    broadcasts = []

    async def broadcast(kind, payload):
        broadcasts.append((kind, payload))

    monkeypatch.setattr(server, "broadcast_to_face", broadcast)

    async def requests():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server.app, client=("127.0.0.1", 5000)),
            base_url="http://127.0.0.1",
        ) as client:
            response = await client.post("/api/notifications/read", json={"id": note["id"]})
            assert response.status_code == 200
            assert response.json()["unread"] == 0
            response = await client.post("/api/notifications/read", json={"id": "f" * 32})
            assert response.status_code == 404
            assert (await client.delete("/api/vision-assistant/events")).status_code == 200
            assert (await client.get("/api/notifications")).json()["items"] == []
            assert (
                await client.post("/api/notifications/clear", json={"confirm": True})
            ).status_code == 200

    with patch("core.vision_assistant.time.time", return_value=1062.0):
        asyncio.run(requests())
    summaries = [body for kind, body in broadcasts if kind == "vision_assistant_notifications"]
    assert len(summaries) == 3
    assert all(body["unread"] == 0 and "items" not in body for body in summaries)
