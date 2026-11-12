"""The wizard's animation library offers what can really be replaced (QA23, 2026-11-11).

Each slot is one factory gesture or rest, grouped as the wizard shows them. Room sequences
(sitting, reading, the tablet, the bed, the desk, the window) are timed to furniture and props
and are never offered; the old empty slots are gone.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from wizard.app import _ANIMATION_SLOT_GROUP, ANIMATION_SLOT_GROUPS, ANIMATION_SLOTS

ROOT = Path(__file__).resolve().parents[1]


def test_every_slot_is_a_factory_gesture_shown_by_group():
    manifest = json.loads((ROOT / "face" / "animations" / "manifest.json").read_text(encoding="utf-8"))
    assert [group for group, _ in ANIMATION_SLOT_GROUPS] == ["Reposo", "Conversación", "Emociones", "Saludos y baile"]
    assert len(ANIMATION_SLOTS) == len(set(ANIMATION_SLOTS))
    for slot in ANIMATION_SLOTS:
        assert slot in manifest["clips"], f"{slot}: no factory clip to replace"
        assert _ANIMATION_SLOT_GROUP[slot]
    assert {"idle", "wave", "dance", "heart", "clap"} <= set(ANIMATION_SLOTS)


def test_room_sequences_and_the_old_empty_slots_are_not_offered():
    old = {"idle_smile", "idle_love", "idle_noble", "idle_weave", "look_around", "sit", "lie"}
    assert not old & set(ANIMATION_SLOTS)
    room = ("sofa_", "bed_", "desk_", "book_", "stand_", "window_", "pc_", "walk")
    assert not [slot for slot in ANIMATION_SLOTS if slot.startswith(room)]


def test_every_slot_has_its_spanish_label_in_the_wizard():
    source = (ROOT / "wizard" / "ui" / "wizard.js").read_text(encoding="utf-8")
    block = source[source.index("const _ANIM_SLOT_LABELS = {"):source.index("};", source.index("const _ANIM_SLOT_LABELS = {"))]
    labels = set(re.findall(r"^\s+([a-z_]+):", block, re.M))
    assert labels == set(ANIMATION_SLOTS)
