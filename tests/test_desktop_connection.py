from unittest.mock import AsyncMock, Mock

import pytest

from core import desktop


@pytest.mark.asyncio
async def test_new_profile_does_not_attach_to_another_profiles_iris(tmp_path, monkeypatch):
    monkeypatch.setenv("IRIS_HOME", str(tmp_path))
    client = Mock()
    monkeypatch.setattr(desktop.httpx, "AsyncClient", client)
    assert await desktop.probe_iris(None) is None
    client.assert_not_called()


@pytest.mark.asyncio
async def test_core_probe_uses_the_private_profile_token(tmp_path, monkeypatch):
    monkeypatch.setenv("IRIS_HOME", str(tmp_path))
    (tmp_path / "companion_token").write_text("private-profile-token")
    monkeypatch.setattr(desktop, "candidate_ports", lambda _: [9876])
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.get.return_value = Mock(status_code=403, json=lambda: {"detail": "Invalid token"})
    factory = Mock(return_value=client)
    monkeypatch.setattr(desktop.httpx, "AsyncClient", factory)
    assert await desktop.probe_iris(None) is None
    assert factory.call_args.kwargs["headers"] == {
        "Origin": "http://tauri.localhost",
        "X-IRIS-Companion-Token": "private-profile-token",
    }


@pytest.mark.asyncio
async def test_three_failed_probes_mark_connection_lost(monkeypatch):
    runtime = desktop.DesktopRuntime()
    runtime._config = object()
    runtime.update("ready", "Ready", face_url="http://127.0.0.1:9876/face/")
    monkeypatch.setattr(desktop, "probe_iris", AsyncMock(return_value=None))
    for _ in range(2):
        runtime._next_probe = 0
        assert (await runtime.snapshot())["phase"] == "ready"
    runtime._next_probe = 0
    snapshot = await runtime.snapshot()
    assert snapshot["phase"] == "error"
    assert snapshot["face_url"] == ""
