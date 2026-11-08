"""Focused contracts for agent-selected avatar animations."""

from __future__ import annotations

import json
from pathlib import Path

from core.animations import normalize_agent_animation

ROOT = Path(__file__).resolve().parents[1]


def test_agent_animation_accepts_safe_procedural_clips_and_legacy_aliases() -> None:
    assert normalize_agent_animation(" wave ") == "wave"
    assert normalize_agent_animation("saludar_feliz") == "wave"
    assert normalize_agent_animation("pensar") == "think"
    assert normalize_agent_animation("asentir") == "nod"
    assert normalize_agent_animation("negar") == "shake"


def test_agent_animation_rejects_invented_or_malformed_values() -> None:
    assert normalize_agent_animation("teletransportarse_a_la_luna") == "idle"
    assert normalize_agent_animation(42) == "idle"
    assert normalize_agent_animation(None) == "idle"
    assert normalize_agent_animation("   ") == "idle"


def test_agent_execution_normalizes_the_animation_before_broadcast() -> None:
    source = (ROOT / "core" / "agent.py").read_text(encoding="utf-8")

    assert 'normalize_agent_animation(response.get("animacion_virtual", "idle"))' in source


def test_missing_personality_prompt_still_receives_live_animation_catalog() -> None:
    source = (ROOT / "core" / "hermes_bridge.py").read_text(encoding="utf-8")
    fallback = source.split("# Minimal fallback if no personality loaded.", 1)[1].split(
        "if memory_summary:", 1
    )[0]

    assert "+ _JSON_ENFORCEMENT" in fallback
    assert "+ format_animation_catalog_for_prompt()" in fallback


def test_animation_loader_can_retry_transient_failures() -> None:
    source = (ROOT / "face" / "js" / "vrm_animation_loader.js").read_text(
        encoding="utf-8"
    )

    assert "const attemptKey = String(url);" in source
    assert "this._attempted.has(attemptKey)" in source
    # Native blob URLs need the manifest's explicit format before URL fallback.
    assert "const lower = format ? '.' + format : attemptKey.split('?')[0].toLowerCase();" in source
    assert "_modulePromise = null;" in source


def test_micro_life_owns_blink_without_competing_with_legacy_blink() -> None:
    app_source = (ROOT / "face" / "js" / "app.js").read_text(encoding="utf-8")
    avatar_source = (ROOT / "face" / "js" / "avatar.js").read_text(
        encoding="utf-8"
    )
    micro_life_source = (ROOT / "face" / "js" / "micro_life.js").read_text(
        encoding="utf-8"
    )

    assert "this.avatarManager.avatar.microLifeOwnsBlink = true" in app_source
    assert "this.microLifeOwnsBlink || this.animationOwnsBlink" in avatar_source
    assert "this.microLifeOwnsBreathing || this.animationOwnsRootMotion" in avatar_source
    assert "this._suspendBlinksForAnimation();" in micro_life_source
    assert "this._resumeBlinksAfterAnimation();" in micro_life_source
    assert "this._suspendBreathingForAnimation();" in micro_life_source


def test_explicit_animations_have_one_lifecycle_and_one_set_of_motion_writers() -> None:
    idle_source = (ROOT / "face" / "js" / "idle_system.js").read_text(
        encoding="utf-8"
    )
    scene_source = (ROOT / "face" / "js" / "scene_actions.js").read_text(
        encoding="utf-8"
    )
    app_source = (ROOT / "face" / "js" / "app.js").read_text(encoding="utf-8")
    companion_source = (ROOT / "face" / "companion" / "companion.js").read_text(
        encoding="utf-8"
    )

    assert "managePoseLifecycle: true" in idle_source
    assert "this._vrmaPoseManagedByMixer === this.currentPose" in idle_source
    assert "this._returningToIdle = false;" in idle_source
    assert "this._boneReturning = false;" in idle_source
    assert "this._setAnimationBlinkSuppressed(true);" in idle_source
    assert "this._setAnimationBlinkSuppressed(false);" in idle_source
    assert "this._animationOwnershipClaimed === next" in idle_source
    assert "const explicitAnimationActive = Boolean(" in scene_source
    assert "this.idleSystem?.isExplicitAnimationActive?.()" in scene_source
    assert "if (explicitAnimationPlaying)" in app_source
    assert "this.speechAnimator?.yieldToExternalClip?.();" in app_source
    assert "mocapRigger" not in app_source
    assert "this.idle?.isExplicitAnimationActive?.()" in companion_source


def test_gestures_return_only_to_calm_idle_clips() -> None:
    manifest = json.loads(
        (ROOT / "face" / "animations" / "manifest.json").read_text(encoding="utf-8")
    )
    idle_source = (ROOT / "face" / "js" / "idle_system.js").read_text(
        encoding="utf-8"
    )

    assert manifest["return_idle_pool"] == ["idle"]
    assert set(manifest["return_idle_pool"]) <= set(manifest["clips"])
    assert "this._vrmaManifest?.return_idle_pool" in idle_source
    assert "name === 'idle' || name === 'idle_casual'" in idle_source


def test_catalog_refreshes_after_another_writer_even_with_identical_mtime(tmp_path, monkeypatch):
    import io
    import os

    from test_animation_store import glb

    from core import animations
    from core.animation_store import AnimationStore

    (tmp_path / "wave.vrma").write_bytes(glb("old"))
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"clips":{"wave":"wave.vrma"}}', encoding="utf-8")
    monkeypatch.setattr(animations, "_ANIM_DIR", tmp_path)
    monkeypatch.setattr(animations, "_MANIFEST_PATH", manifest)
    monkeypatch.setattr(animations, "_cache_token", None)
    monkeypatch.setattr(animations, "_cache_clips", [])
    assert [clip["name"] for clip in animations.get_available_animations()] == ["wave"]
    timestamp = manifest.stat().st_mtime_ns
    # Direct store call simulates a different process: no cache invalidation hook.
    AnimationStore(tmp_path).save_upload(io.BytesIO(glb()), "new.glb", "celebrate")
    os.utime(manifest, ns=(timestamp, timestamp))
    assert [clip["name"] for clip in animations.get_available_animations()] == ["celebrate", "wave"]


def test_catalog_fallback_never_advertises_uncommitted_version_files(tmp_path, monkeypatch):
    import hashlib

    from test_animation_store import glb

    from core import animations

    (tmp_path / "legacy.vrma").write_bytes(glb("old"))
    (tmp_path / f"wave.{hashlib.sha256(glb()).hexdigest()}.vrma").write_bytes(glb())
    monkeypatch.setattr(animations, "_ANIM_DIR", tmp_path)
    monkeypatch.setattr(animations, "_MANIFEST_PATH", tmp_path / "manifest.json")
    monkeypatch.setattr(animations, "_cache_token", None)
    monkeypatch.setattr(animations, "_cache_clips", [])
    assert [clip["name"] for clip in animations.get_available_animations()] == ["legacy"]
    (tmp_path / "manifest.json").write_bytes(b"{broken")
    assert [clip["name"] for clip in animations.get_available_animations()] == ["legacy"]


def test_existing_authored_animation_containers_pass_minimal_validation():
    from core.animation_store import _validate_format

    manifest = json.loads((ROOT / "face" / "animations" / "manifest.json").read_bytes())
    for filename in manifest["clips"].values():
        path = ROOT / "face" / "animations" / filename
        _validate_format(path.read_bytes(), path.suffix[1:].lower())
