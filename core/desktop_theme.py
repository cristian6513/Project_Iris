"""Cache the visual shell of Theme Studio, never its accounts or app state."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from core.config import get_iris_home


class DesktopTheme(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    accent: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    accent2: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    ready_color: str = Field(default="#5cffad", pattern=r"^#[0-9a-fA-F]{6}$")
    background: Literal["city", "sakura", "hacker", "classic", "nebula", "grid", "sunrise"] = "city"
    profile: Literal["iris", "alice", "sakura", "halo", "glass"] = "iris"
    card_opacity: int = Field(default=88, ge=30, le=95)
    blur_intensity: int = Field(default=24, ge=0, le=40)
    background_dim: int = Field(default=80, ge=0, le=95)
    accent_glow: int = Field(default=80, ge=0, le=100)
    border_glow: int = Field(default=60, ge=0, le=100)
    neon_intensity: int = Field(default=80, ge=0, le=100)
    reduced_motion: bool = False
    animated_glow: bool = True
    transparent_cards: bool = True
    compact_mode: bool = False


def visual_preset() -> str:
    """Read only the visual preference; never serialize IRISConfig credentials."""
    try:
        value = json.loads((get_iris_home() / "config.json").read_text("utf-8"))
        preset = value.get("visual_theme", "iris")
        return preset if preset in {"iris", "rose", "verdant", "sunrise"} else "iris"
    except (OSError, ValueError, AttributeError):
        return "iris"


def read_theme_cache() -> dict:
    preset = visual_preset()
    try:
        value = json.loads((get_iris_home() / "desktop_theme.json").read_text("utf-8"))
        if value.get("source_preset") == preset:
            return {
                "preset": preset,
                "theme": DesktopTheme.model_validate(value["theme"]).model_dump(by_alias=True),
            }
    except (OSError, ValueError, TypeError, AttributeError, KeyError):
        pass
    return {"preset": preset, "theme": None}


def save_theme_cache(theme: DesktopTheme) -> None:
    home = get_iris_home()
    target = home / "desktop_theme.json"
    temporary: Path = home / f".desktop-theme-{uuid.uuid4().hex}.tmp"
    try:
        temporary.write_text(
            json.dumps(
                {
                    "source_preset": visual_preset(),
                    "theme": theme.model_dump(by_alias=True),
                }
            ),
            encoding="utf-8",
        )
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
