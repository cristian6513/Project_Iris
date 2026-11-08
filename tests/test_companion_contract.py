"""Static contract checks for the shared Desktop Companion foundation."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_face_registers_capabilities_instead_of_legacy_ready_only() -> None:
    app_source = (ROOT / "face" / "js" / "app.js").read_text(encoding="utf-8")
    protocol = (ROOT / "face" / "js" / "surface_protocol.js").read_text(encoding="utf-8")

    assert "this.ws.send('surface_ready'" in app_source
    assert "this.ws.send('face_ready')" not in app_source
    for capability in (
        "renders_avatar",
        "plays_tts",
        "captures_camera",
        "accepts_chat",
        "controls_local_player",
    ):
        assert capability in app_source
        assert capability in protocol


def test_animation_context_file_keeps_room_adaptation_out_of_llm_json() -> None:
    document = json.loads(
        (ROOT / "face" / "animations" / "contexts.json").read_text(encoding="utf-8")
    )
    companion = document["contexts"]["desktop_companion"]

    assert companion["go_computer"]["animation"] == "think"
    assert companion["walk_tour"]["movement"] == "window-walk"
    assert "animacion_virtual" in document["_comment"]


def test_shared_animation_and_audio_logic_are_not_duplicated_in_app() -> None:
    app_source = (ROOT / "face" / "js" / "app.js").read_text(encoding="utf-8")

    assert "new AnimationDirector" in app_source
    assert "new TtsAudioPlayer" in app_source
    assert "const ANIM_TO_VRMA" not in app_source
    assert "_handleTtsAudio(payload)" not in app_source


def test_animation_lifecycle_handles_retrigger_and_companion_pose_rebase() -> None:
    idle_source = (ROOT / "face" / "js" / "idle_system.js").read_text(encoding="utf-8")
    companion = (ROOT / "face" / "companion" / "companion.js").read_text(
        encoding="utf-8"
    )

    one_shot = idle_source.split("_startOneShotClip(clip, clipKey", 1)[1].split(
        "_startLoopingClip", 1
    )[0]
    assert "if (mixer && startedAction)" in one_shot
    assert "startedAction !== previousAction" not in one_shot
    assert "managePoseLifecycle = false" in one_shot
    assert "this._vrmaPoseManagedByMixer = ownedPose" in one_shot
    assert "this.speechAnimator?.yieldToExternalClip?.();" in companion
    assert "this.idle?.isExplicitAnimationActive?.()" in companion


def test_explicit_procedural_animation_keeps_body_ownership_during_speech() -> None:
    idle_source = (ROOT / "face" / "js" / "idle_system.js").read_text(encoding="utf-8")
    app_source = (ROOT / "face" / "js" / "app.js").read_text(encoding="utf-8")
    micro_life = (ROOT / "face" / "js" / "micro_life.js").read_text(encoding="utf-8")

    assert "this.currentPose && !this._manualPoseActive" in idle_source
    assert "this.paused && !(this.currentPose && this._manualPoseActive)" in idle_source
    assert "this.idle?.isExplicitAnimationActive?.()" in app_source
    assert "const proceduralPoseActive = !!idle?.currentPose;" in micro_life
    assert "const skipBoneAccum = explicitAnimationActive || stableDeskPose" in micro_life
    assert "idle?.isExplicitAnimationActive?.()" in micro_life


def test_vrm_runtime_imports_are_bundler_resolvable() -> None:
    html = (ROOT / "face" / "index.html").read_text(encoding="utf-8")
    avatar = (ROOT / "face" / "js" / "avatar.js").read_text(encoding="utf-8")
    animations = (ROOT / "face" / "js" / "vrm_animation_loader.js").read_text(
        encoding="utf-8"
    )
    springbones = (ROOT / "face" / "js" / "auto_springbones.js").read_text(
        encoding="utf-8"
    )

    assert '"@pixiv/three-vrm":' in html
    assert '"@pixiv/three-vrm-animation":' in html
    assert "import('@pixiv/three-vrm')" in avatar
    assert "import('@pixiv/three-vrm-animation')" in animations
    assert "cdn.jsdelivr.net" not in avatar
    assert "cdn.jsdelivr.net" not in animations
    assert "cdn.jsdelivr.net" not in springbones


def test_browser_companion_is_an_isolated_transparent_surface() -> None:
    html = (ROOT / "face" / "companion" / "index.html").read_text(encoding="utf-8")
    css = (ROOT / "face" / "companion" / "companion.css").read_text(encoding="utf-8")
    source = (ROOT / "face" / "companion" / "companion.js").read_text(encoding="utf-8")

    for control in (
        "voice-manual",
        "camera-toggle",
        "chat-toggle",
        "open-iris-os",
        "hide-companion",
    ):
        assert f'id="{control}"' in html
    assert "background: transparent" in css
    assert "role: 'desktop_companion'" in source
    assert "new AvatarManager" in source
    assert "new AnimationDirector" in source
    assert "new TtsAudioPlayer" in source
    assert "room.js" not in source
    # Live AA uses a minimal offscreen/output pipeline; no room or bloom.
    assert "new GraphicsQuality" in source
    assert "new OutputPass" in source
    assert "UnrealBloomPass" not in source
    assert "OrbitControls" not in source
    # Core state must not wait behind a potentially large VRM download.
    assert source.index("this.ws.connect();") < source.index("await this._avatarSync.request();")
    assert "const avatarReady = Boolean(this.avatarManager && this.ttsAudioPlayer)" in source


def test_companion_never_captures_browser_audio_and_uses_native_hermes_voice() -> None:
    source = (ROOT / "face" / "companion" / "companion.js").read_text(encoding="utf-8")
    policy = (ROOT / "face" / "js" / "companion_camera_policy.js").read_text(encoding="utf-8")

    manual = (ROOT / "face" / "js" / "manual_voice.js").read_text(encoding="utf-8")
    assert "new ManualVoiceController" in source
    assert "/api/hermes-voice/runtime" in manual
    assert "/api/hermes-wake/runtime" not in source + manual
    assert "wake-toggle" not in (ROOT / "face" / "companion" / "index.html").read_text(encoding="utf-8")
    assert "camera_lease_request" in source
    assert "camera_lease_release" in source
    assert "navigator.mediaDevices.getUserMedia" in source
    assert "COMPANION_CAMERA_CONSTRAINTS" in source
    assert "audio: false" in policy
    assert "audio: true" not in source + policy
    assert "sendBinary" not in source
    assert "toDataURL" not in source
    assert "ImageData" not in source


def test_companion_camera_emits_only_local_derived_signals() -> None:
    source = (ROOT / "face" / "companion" / "companion.js").read_text(encoding="utf-8")
    policy = (ROOT / "face" / "js" / "companion_camera_policy.js").read_text(encoding="utf-8")

    assert "purpose: 'mocap_and_emotion'" in source
    assert "import('@mediapipe/tasks-vision')" in source
    assert "buildCompanionEmotionPayload(emotion)" in source
    assert "this.ws?.send?.('user_emotion'" in source
    assert "this.wakeActive" in source and "this.cameraStream" in source
    assert "_syncControlRailVisibility" in source
    for field in ("label", "valence", "arousal", "confidence", "intensity"):
        assert field in policy
    for forbidden in ("frame", "image", "scores"):
        assert f"{forbidden}:" not in policy
    assert "const sent = this.ws.send('camera_lease_request'" in source
    assert "this._cameraLeaseTimer = setTimeout" in source
    assert "}, 5000);" in source
    assert "this._clearCameraLeaseTimer();" in source


def test_face_and_companion_share_the_exclusive_camera_contract() -> None:
    app_source = (ROOT / "face" / "js" / "app.js").read_text(encoding="utf-8")
    panel_source = (ROOT / "face" / "js" / "panel.js").read_text(encoding="utf-8")

    assert (
        "this.panel.requestCameraLease = (confirmed = false) => "
        "this._requestCameraLease({ confirmed })"
    ) in app_source
    assert "case 'camera_lease_revoked':" in app_source
    assert "document.visibilityState === 'hidden'" in app_source
    lease_request = "leaseGranted = await this.requestCameraLease(confirmed)"
    assert lease_request in panel_source
    assert panel_source.index(lease_request) < panel_source.index(
        "navigator.mediaDevices.getUserMedia"
    )
    assert "this.releaseCameraLease?.()" in panel_source


def test_companion_assets_are_included_in_python_package() -> None:
    project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert '"companion/*.html"' in project
    assert '"companion/*.css"' in project
    assert '"companion/*.js"' in project


def test_companion_window_locomotion_is_gated_and_cancelable() -> None:
    source = (ROOT / "face" / "companion" / "companion.js").read_text(encoding="utf-8")
    policy = (ROOT / "face" / "js" / "companion_locomotion_policy.js").read_text(
        encoding="utf-8"
    )
    bridge = (ROOT / "face" / "js" / "companion_native_bridge.js").read_text(
        encoding="utf-8"
    )

    assert "companionLocomotionDecision" in source
    assert "pointerenter" in source and "_cancelWindowWalk" in source
    assert "document.visibilityState !== 'hidden'" in source
    assert "startWindowWalk" in bridge and "cancelWindowWalk" in bridge
    assert "startWindowDrag" in bridge and "hideCompanion" in bridge
    assert "this.bridge.startWindowDrag()" in source
    for reason in ("disabled", "reduced-motion", "quiet-mode", "pointer-hover", "hidden"):
        assert reason in policy


def test_companion_lifecycle_is_explicit_in_wizard_and_advanced_settings() -> None:
    wizard_html = (ROOT / "wizard" / "ui" / "index.html").read_text(encoding="utf-8")
    wizard_js = (ROOT / "wizard" / "ui" / "wizard.js").read_text(encoding="utf-8")
    settings_js = (ROOT / "face" / "js" / "hermes_activity.js").read_text(encoding="utf-8")
    panel_js = (ROOT / "face" / "js" / "panel.js").read_text(encoding="utf-8")

    for surface in (wizard_js, settings_js, panel_js):
        assert "/api/companion/lifecycle" in surface
        assert "X-IRIS-Intent" in surface
        assert "install-companion" in surface
        assert "launch-companion" in surface
        assert "stop-companion" in surface
        assert "configure-companion-autostart" in surface
    assert 'id="wizard-companion-autostart"' in wizard_html
    assert "Este diagnóstico no instala, descarga ni compila nada" in wizard_html
    assert "Independiente del servicio de IA" in settings_js
    assert "data-companion-lifecycle-action=\"install\"" in settings_js
    assert "get('preview') === 'companion'" in wizard_js
    assert "Sin guardar configuración" in wizard_js
    assert "_wizardCompanionReadOnly && action !== 'refresh'" in wizard_js
    assert "Boolean(event.target?.checked)" in wizard_js
    assert "diagnostic_required: 'Diagnóstico incompleto'" in wizard_js
    assert "report?.package?.trusted && report?.package?.signed === false" in wizard_js
    assert "this._companionSettingsLoading || this._companionSettingsDirty" in settings_js
    assert "this._companionLifecycle = null" in settings_js
    assert "report?.platform?.supported" in wizard_js
    assert "report?.platform?.supported" in settings_js
    assert "manual_migration_required" in wizard_js
    assert "manual_migration_required" in settings_js
    assert "newer_repair_package_required" in wizard_js
    assert "newer_repair_package_required" in settings_js
    assert "process_check_required" in wizard_js
    assert "process_check_required" in settings_js
    assert "focus_requested" in wizard_js
    assert "focus_requested" in settings_js
    assert "this._companionSettingsLoaded = false" in settings_js
    assert "!this._companionSettingsLoaded" in settings_js
    assert "...payload" in settings_js
    assert "report?.autostart?.enabled || report?.autostart?.stale" in wizard_js
    assert "report?.autostart?.enabled || report?.autostart?.stale" in settings_js
    assert "IRIS en ventana" in panel_js
    assert "Segundo plano" in panel_js
