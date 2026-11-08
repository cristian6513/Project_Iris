"""The final wizard action must launch a connected IRIS runtime."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.responses import JSONResponse

from wizard.app import launch_iris_endpoint


def test_launch_does_not_silently_start_iris_when_gateway_fails() -> None:
    gateway = JSONResponse(
        {"status": "error", "code": "missing_oauth", "message": "OAuth pendiente"},
        status_code=409,
    )
    with (
        patch("wizard.app.start_gateway", new=AsyncMock(return_value=gateway)),
        patch("core.process_launcher.launch_iris") as launch,
    ):
        response = asyncio.run(launch_iris_endpoint())

    assert response.status_code == 409
    assert json.loads(response.body)["code"] == "missing_oauth"
    launch.assert_not_called()


def test_launch_starts_gateway_before_spawning_iris() -> None:
    gateway = JSONResponse({"status": "running"})
    handle = SimpleNamespace(
        pid=123,
        port=8765,
        log_path="iris.log",
        started_at="now",
    )
    with (
        patch("wizard.app.start_gateway", new=AsyncMock(return_value=gateway)) as start,
        patch("core.process_launcher.is_iris_running", return_value=None),
        patch("core.process_launcher.launch_iris", return_value=handle) as launch,
    ):
        response = asyncio.run(launch_iris_endpoint())

    assert response.status_code == 200
    assert json.loads(response.body)["gateway"] == "running"
    start.assert_awaited_once()
    launch.assert_called_once()
