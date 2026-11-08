from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from core.personality import Personality
from core.workspace_sync import _build_soul_md
from wizard import iris_messaging_plugin as plugin
from wizard.app import app
from wizard.hermes_messaging_worker import public_platform
from wizard.messaging import _pairing_response, messaging


def envelope(text="Hola"):
    return json.dumps({"texto_hablado": text, "emocion": "feliz", "animacion_virtual": "iris_wave"})


@pytest.mark.parametrize(
    "value,expected",
    [
        (envelope(), "Hola"),
        ("```json\n" + envelope() + "\n```", "Hola"),
        (envelope() + "\nMEDIA:/tmp/example.png", "Hola\nMEDIA:/tmp/example.png"),
        (envelope('{"total": 4}'), '{"total": 4}'),
        ('{"total": 4}', '{"total": 4}'),
        ("Este es un ejemplo: " + envelope(), "Este es un ejemplo: " + envelope()),
        (envelope(""), plugin.FALLBACK),
        ('{"texto_hablado": "hola", "emocion": "feliz", "animacion_virtual":', plugin.FALLBACK),
    ],
)
def test_only_avatar_envelopes_are_unwrapped(value, expected):
    assert plugin.visible_message(value) == expected


def test_api_keeps_avatar_metadata_and_external_chat_keeps_plain_text():
    with patch.object(plugin, "_channels", return_value={"telegram", "new_plugin_channel"}):
        assert plugin.transform_reply(envelope(), platform="api_server") is None
        assert plugin.transform_reply(envelope(), platform="telegram") == "Hola"
        assert plugin.transform_reply(envelope(), platform="new_plugin_channel") == "Hola"
        assert plugin.transform_reply(envelope(), platform="cron") == "Hola"
        assert plugin.transform_reply('{"total": 4}', platform="telegram") is None


def test_shared_personality_does_not_force_avatar_format_but_iris_prompt_does():
    personality = Personality(name="Test")
    shared = _build_soul_md(personality)
    assert "SIEMPRE responde en JSON" not in shared
    assert "Formato según el destino" in shared
    assert '"texto_hablado"' not in shared
    assert '"texto_hablado"' in personality.get_soul()


def test_catalog_never_returns_even_part_of_stored_credentials():
    result = public_platform(
        {
            "id": "example",
            "env_vars": [
                {
                    "key": "EXAMPLE_TOKEN",
                    "is_set": True,
                    "required": True,
                    "is_password": True,
                    "redacted_value": "prefix-secret-suffix",
                    "value": "actual-secret",
                }
            ],
            "error_message": "provider said actual-secret",
        }
    )
    assert "secret" not in json.dumps(result)
    assert result["fields"][0]["is_set"] is True
    assert result["fields"][0]["is_password"] is True


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/api/messaging/platforms"),
        ("post", "/api/messaging/prepare"),
        ("post", "/api/messaging/restart"),
        ("put", "/api/messaging/platforms/telegram"),
        ("delete", "/api/messaging/whatsapp/pairing/fake"),
    ],
)
def test_foreign_origin_cannot_read_or_change_channels(method, path):
    with TestClient(
        app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 50000),
    ) as client, patch.object(messaging, "call", AsyncMock()) as call:
        kwargs = {"headers": {"origin": "https://foreign.example", "sec-fetch-site": "cross-site"}}
        if method == "put":
            kwargs["json"] = {"enabled": True}
        response = getattr(client, method)(path, **kwargs)
    assert response.status_code == 403
    call.assert_not_called()


def test_profile_and_arbitrary_commands_are_not_accepted():
    with TestClient(app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 1234)) as client:
        response = client.put("/api/messaging/platforms/telegram", json={"profile": "other"})
    assert response.status_code == 422


def test_qr_is_rendered_locally_and_raw_pairing_payload_is_not_returned():
    result = _pairing_response({"status": "waiting", "qr_payload": "fake-pairing-contents"})
    value = json.loads(result.body)
    assert value["qr_image"].startswith("data:image/svg+xml;base64,")
    assert "qr_payload" not in value
    assert result.headers["cache-control"] == "no-store"


def test_saving_is_separate_from_restart_and_returns_sync_warning():
    with (
        TestClient(app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 1234)) as client,
        patch.object(
            messaging, "call", AsyncMock(return_value={"ok": True, "needs_restart": True})
        ) as call,
        patch("wizard.messaging._sync_identity", AsyncMock(return_value="Reinicia Iris")),
    ):
        response = client.put(
            "/api/messaging/platforms/telegram", json={"env": {"TELEGRAM_BOT_TOKEN": "fake"}}
        )
    assert response.status_code == 200
    assert response.json()["warning"] == "Reinicia Iris"
    assert call.await_count == 1
    assert call.call_args.args[0] == "save"
