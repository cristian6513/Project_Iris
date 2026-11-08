import json

import pytest
from pydantic import ValidationError

from core.desktop_theme import DesktopTheme, read_theme_cache, save_theme_cache


def test_cache_preserves_only_visual_values_and_survives_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("IRIS_HOME", str(tmp_path))
    (tmp_path / "config.json").write_text(json.dumps({"visual_theme": "rose", "unrelated": "kept"}))
    theme = DesktopTheme.model_validate(
        {
            "accent": "#ff8ed7",
            "accent2": "#c78bff",
            "background": "sakura",
            "reducedMotion": True,
            "api_key": "must-not-be-saved",
            "unknown": "ignored",
        }
    )
    save_theme_cache(theme)
    cache = read_theme_cache()
    assert cache["preset"] == "rose"
    assert cache["theme"]["accent"] == "#ff8ed7"
    assert cache["theme"]["reducedMotion"] is True
    assert "must-not-be-saved" not in (tmp_path / "desktop_theme.json").read_text()
    assert json.loads((tmp_path / "config.json").read_text())["unrelated"] == "kept"


def test_new_installer_preset_replaces_previous_desktop_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("IRIS_HOME", str(tmp_path))
    save_theme_cache(DesktopTheme(accent="#73f7ff", accent2="#ff74d4"))
    (tmp_path / "config.json").write_text('{"visual_theme":"verdant"}')
    assert read_theme_cache() == {"preset": "verdant", "theme": None}


def test_corrupt_cache_falls_back_without_overwriting_theme(tmp_path, monkeypatch):
    monkeypatch.setenv("IRIS_HOME", str(tmp_path))
    path = tmp_path / "desktop_theme.json"
    path.write_text("broken cache")
    assert read_theme_cache() == {"preset": "iris", "theme": None}
    assert path.read_text() == "broken cache"


@pytest.mark.parametrize("value", ["url(https://untrusted.test)", "red;display:none", "#fff"])
def test_cache_rejects_css_as_a_color(value):
    with pytest.raises(ValidationError):
        DesktopTheme(accent=value, accent2="#ff74d4")
