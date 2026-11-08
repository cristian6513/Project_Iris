"""Room locomotion stays playable without becoming an agent-selected gesture."""
from pathlib import Path
import json

import pytest

from core import animations
from core.animation_library import AnimationLibrary

ROOT = Path(__file__).resolve().parents[1]
ROOM_CLIPS = (
    "sit", "walk_in_place", "pc_sit_down_v2", "pc_seated_idle_v2",
    "sofa_sit_down", "sofa_seated_idle", "sofa_stand_up", "walk",
    "bed_lie_down", "bed_lying_idle", "bed_get_up",
    "bed_sit_down", "bed_seated_idle", "bed_stand_up", "bed_sit_up",
    "book_take", "book_read_idle", "book_return",
    "sofa_read_open", "sofa_read_idle", "sofa_read_close",
    "bed_read_open", "bed_read_idle", "bed_read_close",
    "sofa_tablet_open", "sofa_tablet_idle", "sofa_tablet_close",
    "bed_tablet_open", "bed_tablet_idle", "bed_tablet_close",
    "desk_sit_down", "desk_seated_idle", "desk_stand_up",
    "desk_type_open", "desk_type_idle", "desk_type_close",
    "stand_read_open", "stand_read_idle", "stand_read_close",
    "stand_tablet_open", "stand_tablet_idle", "stand_tablet_close",
    "window_lean_in", "window_look_idle", "window_lean_out",
)
# Played through a sequence, never named by a pose.
SEQUENCE_CLIPS = (
    "pc_sit_down_v2", "sofa_sit_down", "sofa_stand_up", "walk",
    "bed_lie_down", "bed_get_up", "bed_sit_down", "bed_stand_up", "bed_sit_up",
    "book_take", "book_return",
    "sofa_read_open", "sofa_read_close", "bed_read_open", "bed_read_close",
    "sofa_tablet_open", "sofa_tablet_close", "bed_tablet_open", "bed_tablet_close",
    "desk_sit_down", "desk_stand_up", "desk_type_open", "desk_type_close",
    "stand_read_open", "stand_read_close", "stand_tablet_open", "stand_tablet_close",
    "window_lean_in", "window_lean_out",
)


@pytest.mark.parametrize("name", ROOM_CLIPS)
@pytest.mark.parametrize("legacy_overlay", [False, True])
def test_factory_room_clip_remains_available_only_to_room(tmp_path, monkeypatch, name, legacy_overlay):
    factory = ROOT / "face" / "animations"
    library = AnimationLibrary(factory, tmp_path / "profile" / "animations")
    monkeypatch.setattr("core.animation_library.get_animation_library", lambda: library)
    monkeypatch.setattr(animations, "_cache_source", None)
    monkeypatch.setattr(animations, "_cache_token", None)
    monkeypatch.setattr(animations, "_cache_clips", [])

    if legacy_overlay:
        library.user.mkdir(parents=True)
        (library.user / "manifest.json").write_text(
            json.dumps({"clips": {}, "runtime_only": ["listen", "talk"]}), encoding="utf-8"
        )
    manifest = library.merged_manifest()
    # Room playback still resolves the original asset and pose mappings.
    assert name in manifest["clips"]
    assert library.resolve_file(manifest["clips"][name]) == factory / manifest["clips"][name]
    assert name in manifest["pose_map"].values() or name in SEQUENCE_CLIPS

    catalog = {clip["name"] for clip in animations.get_available_animations()}
    assert name not in catalog
    prompt = animations.format_animation_catalog_for_prompt()
    assert f"- {name}:" not in prompt
    assert animations.normalize_agent_animation(name) == "idle"
    # Original conversational gestures survive the same real catalog path.
    expected = set(manifest["_source_mapping"]) - {"listen", "talk"}
    assert catalog == expected
    assert animations.normalize_agent_animation("celebrate") == "celebrate"
    assert animations.normalize_agent_animation("confirm") == "confirm"
    assert "- wave:" in prompt
    assert library.user.parent.exists() is legacy_overlay
