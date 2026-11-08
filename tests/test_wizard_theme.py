from __future__ import annotations

import asyncio
import fnmatch
import json
import tomllib
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import ValidationError

from core.config import IRISConfig
from wizard.app import (
    ConfigUpdate,
    VisualThemeUpdate,
    _merge_config_update,
    save_visual_theme,
    app as wizard_app,
)


ROOT = Path(__file__).parents[1]


def test_visual_theme_defaults_do_not_override_existing_theme_studio() -> None:
    config = IRISConfig()

    assert config.visual_theme == "iris"
    assert config.visual_theme_selected is False


def test_visual_theme_endpoint_accepts_only_real_theme_studio_presets() -> None:
    for theme in ("iris", "rose", "verdant", "sunrise"):
        assert VisualThemeUpdate(theme=theme).theme == theme

    try:
        VisualThemeUpdate(theme="claude-purple")
    except ValidationError:
        pass
    else:
        raise AssertionError("an unknown visual edition was accepted")

    try:
        VisualThemeUpdate(theme="rose", unexpected=True)
    except ValidationError:
        pass
    else:
        raise AssertionError("the visual-theme endpoint silently ignored an unknown field")


def test_visual_theme_save_is_small_and_does_not_rebuild_hermes_config() -> None:
    existing = IRISConfig(
        visual_theme="iris",
        visual_theme_selected=False,
        hermes_url="http://127.0.0.1:9999",
        hardware_enabled=True,
    )

    with (
        patch("wizard.app.IRISConfig.load", return_value=existing),
        patch.object(IRISConfig, "save", autospec=True) as save_mock,
    ):
        response = asyncio.run(save_visual_theme(VisualThemeUpdate(theme="rose")))

    payload = json.loads(response.body)
    assert response.status_code == 200
    assert payload == {"status": "saved", "theme": "rose"}
    assert existing.visual_theme_selected is True
    assert existing.hermes_url == "http://127.0.0.1:9999"
    assert existing.hardware_enabled is True
    save_mock.assert_called_once_with(existing)


def test_visual_theme_save_reports_config_load_failure_as_json() -> None:
    with patch("wizard.app.IRISConfig.load", side_effect=OSError("read denied")):
        response = asyncio.run(save_visual_theme(VisualThemeUpdate(theme="rose")))

    payload = json.loads(response.body)
    assert response.status_code == 500
    assert payload["status"] == "error"
    assert "guardar" in payload["message"]


def test_wizard_rejects_dns_rebinding_mutations() -> None:
    attacker = TestClient(
        wizard_app,
        base_url="http://attacker.example:8000",
        client=("127.0.0.1", 51999),
    )
    response = attacker.post(
        "/api/save-visual-theme",
        json={"theme": "rose"},
        headers={"Origin": "http://attacker.example:8000"},
    )

    assert response.status_code == 403


def test_config_save_uses_a_complete_atomic_json_replacement() -> None:
    with TemporaryDirectory() as temp_dir:
        home = Path(temp_dir)
        with patch("core.config.get_iris_home", return_value=home):
            IRISConfig(visual_theme="rose", visual_theme_selected=True).save()

        payload = json.loads((home / "config.json").read_text(encoding="utf-8"))
        assert payload["visual_theme"] == "rose"
        assert payload["visual_theme_selected"] is True
        assert list(home.glob("*.tmp")) == []


def test_global_wizard_save_preserves_unowned_settings_and_hidden_secrets() -> None:
    existing = IRISConfig(
        assistant_name="IRIS",
        visual_theme="iris",
        visual_theme_selected=False,
        lan_access=True,
        hardware_enabled=True,
        serial_port="COM42",
        perception_fps=7.5,
        elevenlabs_api_key="stored-secret",
    )
    update = ConfigUpdate(
        assistant_name="NOVA",
        visual_theme="verdant",
        visual_theme_selected=True,
        elevenlabs_api_key="",
    )

    merged = _merge_config_update(update, existing)

    assert merged.assistant_name == "NOVA"
    assert merged.visual_theme == "verdant"
    assert merged.visual_theme_selected is True
    assert merged.lan_access is True
    assert merged.hardware_enabled is True
    assert merged.serial_port == "COM42"
    assert merged.perception_fps == 7.5
    assert merged.elevenlabs_api_key == "stored-secret"


def test_config_contract_rejects_the_old_silently_ignored_hermes_token_name() -> None:
    try:
        ConfigUpdate(hermes_token="would-have-been-discarded")
    except ValidationError:
        pass
    else:
        raise AssertionError("ConfigUpdate accepted the obsolete hermes_token field")


def test_wizard_and_help_reference_the_four_production_editions() -> None:
    index = (ROOT / "wizard" / "ui" / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "wizard" / "ui" / "wizard.js").read_text(encoding="utf-8")
    help_page = (ROOT / "wizard" / "ui" / "help.html").read_text(encoding="utf-8")
    face = (ROOT / "face" / "js" / "hermes_activity.js").read_text(encoding="utf-8")

    for theme in ("iris", "rose", "verdant", "sunrise"):
        assert f'value="{theme}"' in index
    assert "/api/save-visual-theme" in script
    assert "visualThemeSaveQueue" in script
    assert "visualThemeTouched" in script
    assert "hermes_api_key: hermesToken" in script
    assert "hermes_token: hermesToken" not in script
    assert 'input[name="visual_theme"]' in script
    assert "irisOsTheme.installerSource.v1" in face
    assert "irisOsTheme.userOverride.v1" in face
    assert "'/api/os/theme-image'" in face
    assert "readAsDataURL(file)" not in face
    assert "Google (Gemini)</td><td>API Key</td>" in help_page
    assert "no instala Ollama" in help_page


def test_distribution_metadata_includes_the_wizard_and_face_assets() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    setup = project["tool"]["setuptools"]

    assert "face*" in setup["packages"]["find"]["include"]
    samples = {
        "wizard": (
            "ui/index.html", "ui/wizard.css", "ui/wizard.js",
            "ui/vendor/fonts/fonts.css",
            "ui/vendor/fonts/files/syne-latin-700-normal.woff2",
        ),
        "face": (
            "index.html", "css/styles.css", "js/app.js", "assets/face.js",
            "assets/face.css", "web-assets.json",
            "vendor/qrcode/qrcode.mjs", "vendor/mediapipe/vision_bundle.mjs",
            "vendor/mediapipe/wasm/vision_wasm_internal.wasm",
            "vendor/fonts/files/outfit-latin-400-normal.woff2",
            "public/assets/os/background.png",
        ),
    }
    for package, resources in samples.items():
        patterns = setup["package-data"][package]
        for resource in resources:
            assert any(fnmatch.fnmatchcase(resource, pattern) for pattern in patterns), resource


def test_wizard_openapi_contract_can_be_generated() -> None:
    from wizard.app import app

    schema = app.openapi()

    assert "/api/install-hermes-stream" in schema["paths"]
    assert "/api/save-llm-selection" in schema["paths"]
    assert "/api/test-tts-audio" in schema["paths"]
    assert "/api/hermes-wake/settings" in schema["paths"]
    assert "/api/hermes-wake/runtime" in schema["paths"]


def test_wizard_lifecycle_status_badge_uses_the_real_html_id() -> None:
    index = (ROOT / "wizard" / "ui" / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "wizard" / "ui" / "wizard.js").read_text(encoding="utf-8")
    styles = (ROOT / "wizard" / "ui" / "wizard.css").read_text(encoding="utf-8")

    assert 'id="iris-status-badge"' in index
    assert "getElementById('iris-status-badge')" in script
    assert "getElementById('IRIS-status-badge')" not in script
    assert ".iris-status-badge" in styles
    assert ".IRIS-status-badge" not in styles
