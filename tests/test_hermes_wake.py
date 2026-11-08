"""Focused regression tests for the native Hermes wake-word bridge."""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import yaml

from core.config import IRISConfig
from core.hermes_bridge import HermesBridge
from core.hermes_wake import (
    HermesGatewayError,
    HermesWakeRuntime,
    HermesWakeRuntimeUpdate,
    HermesWakeSettingsUpdate,
    _friendly_native_failure,
    _reconcile_wake_input_truth,
    configure_hermes_wake,
    infer_wake_mode,
    hermes_wake_snapshot,
    normalize_custom_wake_phrase,
    probe_hermes_wake_requirements,
    read_hermes_wake_settings,
    update_hermes_wake_runtime,
    update_hermes_wake_settings,
)


ROOT = Path(__file__).resolve().parents[1]


def _temporary_config(initial: dict | str):
    temporary = tempfile.TemporaryDirectory()
    path = Path(temporary.name) / "config.yaml"
    if isinstance(initial, str):
        path.write_text(initial, encoding="utf-8")
    else:
        path.write_text(yaml.safe_dump(initial, sort_keys=False), encoding="utf-8")
    return temporary, path


def test_hey_hermes_uses_native_bundled_engine_and_stays_off() -> None:
    temporary, path = _temporary_config({"model": "openrouter/test", "custom": {"keep": True}})
    try:
        with patch("core.hermes_wake.get_hermes_config_path", return_value=path):
            result = configure_hermes_wake("hey_hermes")
            document = yaml.safe_load(path.read_text(encoding="utf-8"))

        assert result["enabled"] is False
        assert result["configured"] is True
        assert result["mode"] == "hey_hermes"
        assert document["model"] == "openrouter/test"
        assert document["custom"] == {"keep": True}
        assert document["wake_word"]["provider"] == "openwakeword"
        assert document["wake_word"]["phrase"] == "hey hermes"
        assert document["wake_word"]["openwakeword"]["model"] == "hey_hermes"
        assert document["wake_word"]["capture"] == "local"
        assert document["wake_word"]["surface"] == "gui"
    finally:
        temporary.cleanup()


def test_missing_wake_block_exposes_defaults_without_claiming_configuration() -> None:
    temporary, path = _temporary_config({"model": {"provider": "openai-api"}})
    try:
        with patch("core.hermes_wake.get_hermes_config_path", return_value=path):
            result = read_hermes_wake_settings()

        assert result["configured"] is False
        assert result["enabled"] is False
        assert result["mode"] == "hey_hermes"
        assert result["phrase"] == "hey hermes"
    finally:
        temporary.cleanup()


def test_custom_wake_name_uses_native_sherpa_and_stays_off() -> None:
    temporary, path = _temporary_config({"model": "openrouter/test"})
    try:
        with patch("core.hermes_wake.get_hermes_config_path", return_value=path):
            result = configure_hermes_wake("custom", phrase="IRIS")
            document = yaml.safe_load(path.read_text(encoding="utf-8"))

        assert result["configured"] is True
        assert result["enabled"] is False
        assert result["mode"] == "custom"
        assert result["provider"] == "sherpa"
        assert result["phrase"] == "hey IRIS"
        assert document["model"] == "openrouter/test"
        assert document["wake_word"]["capture"] == "local"
        assert document["wake_word"]["surface"] == "gui"
        assert document["wake_word"]["profile_routing"] is True
    finally:
        temporary.cleanup()


def test_custom_wake_phrase_normalization_avoids_duplicate_prefixes() -> None:
    assert normalize_custom_wake_phrase("  Nova  ") == "hey Nova"
    assert normalize_custom_wake_phrase("HEY   Nova") == "hey Nova"


def test_invalid_custom_wake_name_never_modifies_hermes_config() -> None:
    temporary, path = _temporary_config({"wake_word": {"phrase": "hey nova"}})
    try:
        before = path.read_bytes()
        with patch("core.hermes_wake.get_hermes_config_path", return_value=path):
            try:
                configure_hermes_wake("custom", phrase="hey")
            except ValueError as exc:
                assert "después de" in str(exc)
            else:
                raise AssertionError("An empty custom wake name should be rejected")
        assert path.read_bytes() == before
    finally:
        temporary.cleanup()


def test_keep_does_not_replace_an_existing_native_custom_phrase() -> None:
    initial = {
        "wake_word": {
            "enabled": True,
            "provider": "sherpa",
            "phrase": "hey nova",
        },
    }
    temporary, path = _temporary_config(initial)
    try:
        before = path.read_text(encoding="utf-8")
        with patch("core.hermes_wake.get_hermes_config_path", return_value=path):
            result = configure_hermes_wake("keep")
        assert result["mode"] == "custom"
        assert path.read_text(encoding="utf-8") == before
    finally:
        temporary.cleanup()


def test_persisted_toggle_can_change_without_replacing_custom_phrase() -> None:
    initial = {"wake_word": {"enabled": True, "provider": "sherpa", "phrase": "hey nova"}}
    temporary, path = _temporary_config(initial)
    try:
        with patch("core.hermes_wake.get_hermes_config_path", return_value=path):
            result = configure_hermes_wake("keep", enabled=False)
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert result["enabled"] is False
        assert result["mode"] == "custom"
        assert document["wake_word"]["phrase"] == "hey nova"
        assert document["wake_word"]["provider"] == "sherpa"
    finally:
        temporary.cleanup()


def test_native_sensitivity_is_preserved_for_custom_phrases() -> None:
    initial = {"wake_word": {"enabled": False, "provider": "sherpa", "phrase": "hey nova"}}
    temporary, path = _temporary_config(initial)
    try:
        with patch("core.hermes_wake.get_hermes_config_path", return_value=path):
            result = configure_hermes_wake("keep", sensitivity=0.75)
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert result["mode"] == "custom"
        assert result["sensitivity"] == 0.75
        assert document["wake_word"]["phrase"] == "hey nova"
        assert document["wake_word"]["sensitivity"] == 0.75
    finally:
        temporary.cleanup()


def test_malformed_native_sensitivity_is_reported_as_hermes_default() -> None:
    for raw_value in ("not-a-number", ".nan", ".inf"):
        initial = {"wake_word": {"provider": "openwakeword", "sensitivity": raw_value}}
        temporary, path = _temporary_config(initial)
        try:
            with patch("core.hermes_wake.get_hermes_config_path", return_value=path):
                result = read_hermes_wake_settings()
            assert result["sensitivity"] == 0.6
        finally:
            temporary.cleanup()


def test_malformed_hermes_yaml_is_never_overwritten() -> None:
    temporary, path = _temporary_config("model: [broken\n")
    try:
        before = path.read_bytes()
        with patch("core.hermes_wake.get_hermes_config_path", return_value=path):
            try:
                configure_hermes_wake("hey_hermes")
            except ValueError as exc:
                assert "no se puede leer" in str(exc)
            else:
                raise AssertionError("Malformed Hermes YAML should be rejected")
        assert path.read_bytes() == before
    finally:
        temporary.cleanup()


def test_mode_inference_is_strict_and_does_not_mislabel_custom_profiles() -> None:
    assert infer_wake_mode({"provider": "openwakeword", "phrase": "hey hermes"}) == "hey_hermes"
    assert infer_wake_mode({"provider": "sherpa", "phrase": "hey coder"}) == "custom"


def test_native_detect_uses_hermes_voice_record_instead_of_browser_audio() -> None:
    events: list[tuple[str, dict]] = []

    async def handler(event_type: str, payload: dict) -> None:
        events.append((event_type, payload))

    runtime = HermesWakeRuntime(handler)
    runtime.request = AsyncMock(side_effect=[
        {"enabled": True},
        {"status": "recording"},
    ])

    asyncio.run(runtime._handle_native_event("wake.detected", {
        "phrase": "hey nova",
        "start_new_session": True,
    }))

    assert runtime.request.await_args_list[0].args[0] == "voice.toggle"
    assert runtime.request.await_args_list[1].args[0] == "voice.record"
    assert events[0][0] == "wake.detected"
    assert events[-1][0] == "voice.status"


def test_transcript_pauses_listener_until_iris_finishes_then_resumes() -> None:
    events: list[str] = []

    async def handler(event_type: str, _payload: dict) -> None:
        events.append(event_type)

    runtime = HermesWakeRuntime(handler)
    runtime._wake_capture_id = "detected-take"
    runtime.request = AsyncMock(side_effect=[
        {"paused": True},
        {"enabled": False},
        {"resumed": True},
    ])

    asyncio.run(runtime._handle_native_event("voice.transcript", {"text": "abre mi agenda"}))

    assert [call.args[0] for call in runtime.request.await_args_list] == [
        "wake.pause", "voice.toggle", "wake.resume",
    ]
    assert events == ["voice.transcript", "wake.resumed"]
    assert runtime.listening is True


def test_native_stop_phrase_never_reaches_the_agent_transcript_handler() -> None:
    events: list[tuple[str, dict]] = []

    async def handler(event_type: str, payload: dict) -> None:
        events.append((event_type, payload))

    runtime = HermesWakeRuntime(handler)
    runtime._wake_capture_id = "detected-take"
    runtime.request = AsyncMock(side_effect=[
        {"enabled": False},
        {"listening": True, "phrase": "hey hermes"},
    ])

    asyncio.run(runtime._handle_native_event("voice.transcript", {
        "text": "stop",
        "stop_phrase": True,
    }))

    assert [call.args[0] for call in runtime.request.await_args_list] == [
        "voice.toggle", "wake.status",
    ]
    assert [event_type for event_type, _ in events] == ["voice.stopped"]
    assert events[0][1]["stop_phrase"] is True
    assert runtime.listening is True


def test_wake_rotates_the_real_hermes_api_session() -> None:
    bridge = HermesBridge(IRISConfig(assistant_name="IRIS"))
    original = bridge.session_id

    first = bridge.start_new_session()
    second = bridge.start_new_session()

    assert first["profile_routed"] is True
    assert first["session_id"] != original
    assert second["session_id"] != first["session_id"]
    assert bridge._request_headers()["X-Hermes-Session-Id"] == second["session_id"]
    assert bridge._request_headers()["X-Hermes-Session-Key"] == "agent:main:iris:dm:iris"


def test_named_wake_profile_uses_only_the_documented_multiplex_route() -> None:
    temporary = tempfile.TemporaryDirectory()
    try:
        home = Path(temporary.name)
        profile_home = home / "profiles" / "coder"
        profile_home.mkdir(parents=True)
        (home / "config.yaml").write_text(
            "gateway:\n  multiplex_profiles: true\n",
            encoding="utf-8",
        )
        (profile_home / ".env").write_text("API_SERVER_KEY=profile-secret\n", encoding="utf-8")

        bridge = HermesBridge(IRISConfig(assistant_name="IRIS"))
        bridge._default_api_key = "default-secret"
        with patch("core.workspace_sync.get_hermes_home", return_value=home):
            routed = bridge.start_new_session("Coder")

        assert routed["profile_routed"] is True
        assert bridge.active_profile == "coder"
        assert bridge._api_path("/v1/chat/completions") == "/p/coder/v1/chat/completions"
        assert bridge._request_headers()["Authorization"] == "Bearer profile-secret"

        resumed = bridge.resume_session("api_session-01")
        assert resumed["session_id"] == "api_session-01"
        assert bridge.active_profile is None
        assert bridge._api_path("/v1/chat/completions") == "/v1/chat/completions"
        assert bridge._request_headers()["Authorization"] == "Bearer default-secret"
    finally:
        temporary.cleanup()


def test_named_wake_profile_fails_closed_without_multiplexing() -> None:
    temporary = tempfile.TemporaryDirectory()
    try:
        home = Path(temporary.name)
        (home / "profiles" / "coder").mkdir(parents=True)
        (home / "config.yaml").write_text("gateway:\n  multiplex_profiles: false\n", encoding="utf-8")
        bridge = HermesBridge(IRISConfig(assistant_name="IRIS"))
        with patch("core.workspace_sync.get_hermes_home", return_value=home):
            result = bridge.start_new_session("coder")

        assert result["profile_routed"] is False
        assert "multi" in result["warning"].lower()
        assert bridge.active_profile is None
        assert bridge._api_path("/v1/chat/completions") == "/v1/chat/completions"
    finally:
        temporary.cleanup()


def test_native_server_spawn_error_is_a_controlled_gateway_failure() -> None:
    temporary = tempfile.TemporaryDirectory()
    try:
        runtime = HermesWakeRuntime()
        with (
            patch("core.hermes_wake.resolve_hermes_cli", return_value="hermes"),
            # ensure_gateway asks hermes_command_prefix (core.hermes_runtime), which would look for a
            # real Hermes on this PC: the test names one, so it never depends on the machine.
            patch("core.hermes_wake.hermes_command_prefix", return_value=["hermes"]),
            patch("core.hermes_wake.get_hermes_repo", return_value=Path(temporary.name)),
            patch(
                "core.hermes_wake.asyncio.create_subprocess_exec",
                new=AsyncMock(side_effect=PermissionError("blocked")),
            ),
        ):
            try:
                asyncio.run(runtime.ensure_gateway())
            except HermesGatewayError as exc:
                assert "Reparar Hermes" in str(exc)
            else:
                raise AssertionError("Spawn failure should be converted to HermesGatewayError")
        assert runtime.state == "error"
        assert runtime.process is None
    finally:
        temporary.cleanup()


def test_client_capture_is_stopped_and_never_reported_as_listening() -> None:
    runtime = HermesWakeRuntime()
    runtime.request = AsyncMock(side_effect=[
        {"started": True, "capture": "client", "phrase": "hey hermes"},
        {"stopped": True},
    ])

    try:
        asyncio.run(runtime.start_listener(persist=True))
    except HermesGatewayError as exc:
        assert "no envía" in str(exc)
        assert "micrófono no se activó" in str(exc)
    else:
        raise AssertionError("Unsupported client capture must fail closed")

    assert [call.args[0] for call in runtime.request.await_args_list] == [
        "wake.start", "wake.stop",
    ]
    assert runtime.listening is False
    assert runtime.state == "blocked"


def test_invalid_wake_start_payload_stops_defensively_and_is_controlled() -> None:
    runtime = HermesWakeRuntime()
    runtime.request = AsyncMock(side_effect=["unexpected", {"stopped": True}])

    try:
        asyncio.run(runtime.start_listener(persist=True))
    except HermesGatewayError as exc:
        assert "respuesta inválida" in str(exc)
        assert "micrófono" in str(exc)
    else:
        raise AssertionError("A malformed wake.start result must fail closed")

    assert [call.args[0] for call in runtime.request.await_args_list] == [
        "wake.start", "wake.stop",
    ]
    assert runtime.listening is False
    assert runtime.state == "error"


def test_failed_iris_turn_still_releases_voice_and_resumes_wake() -> None:
    events: list[str] = []

    async def handler(event_type: str, _payload: dict) -> None:
        events.append(event_type)
        if event_type == "voice.transcript":
            raise RuntimeError("agent failed")

    runtime = HermesWakeRuntime(handler)
    runtime._wake_capture_id = "detected-take"
    runtime.request = AsyncMock(side_effect=[
        {"paused": True},
        {"enabled": False},
        {"resumed": True},
    ])

    try:
        asyncio.run(runtime._handle_native_event("voice.transcript", {"text": "haz algo"}))
    except RuntimeError as exc:
        assert str(exc) == "agent failed"
    else:
        raise AssertionError("The original IRIS turn failure should remain visible")

    assert [call.args[0] for call in runtime.request.await_args_list] == [
        "wake.pause", "voice.toggle", "wake.resume",
    ]
    assert events == ["voice.transcript", "wake.resumed"]
    assert runtime.listening is True
    assert runtime.state == "listening"


def test_failed_settings_save_restores_the_previous_listener() -> None:
    runtime = HermesWakeRuntime()
    runtime.listening = True

    async def stop_listener(*, persist: bool) -> dict:
        assert persist is False
        runtime.listening = False
        return {}

    async def start_listener(*, persist: bool) -> dict:
        assert persist is False
        runtime.listening = True
        return {"active": True}

    runtime.stop_listener = AsyncMock(side_effect=stop_listener)
    runtime.start_listener = AsyncMock(side_effect=start_listener)
    request = SimpleNamespace(
        headers={},
        client=SimpleNamespace(host="127.0.0.1"),
        url=SimpleNamespace(hostname="127.0.0.1"),
        app=SimpleNamespace(state=SimpleNamespace(hermes_wake=runtime)),
    )

    with patch("core.hermes_wake.configure_hermes_wake", side_effect=OSError("disk full")):
        response = asyncio.run(update_hermes_wake_settings(
            HermesWakeSettingsUpdate(mode="hey_hermes", enabled=True),
            request,
        ))

    assert response.status_code == 422
    assert json.loads(response.body)["status"] == "error"
    assert runtime.stop_listener.await_count == 1
    assert runtime.start_listener.await_count == 1
    assert runtime.listening is True


def test_settings_api_forwards_custom_name_without_enabling_capture() -> None:
    request = SimpleNamespace(
        headers={},
        client=SimpleNamespace(host="127.0.0.1"),
        url=SimpleNamespace(hostname="127.0.0.1"),
        app=SimpleNamespace(state=SimpleNamespace(hermes_wake=None)),
    )
    saved = {
        "enabled": False,
        "mode": "custom",
        "provider": "sherpa",
        "phrase": "hey Nova",
        "runtime": {"active": False},
    }
    with (
        patch("core.hermes_wake.configure_hermes_wake", return_value=saved) as configure,
        patch("core.hermes_wake.hermes_wake_snapshot", new=AsyncMock(return_value=saved)),
    ):
        response = asyncio.run(update_hermes_wake_settings(
            HermesWakeSettingsUpdate(
                mode="custom",
                enabled=False,
                sensitivity=0.65,
                phrase="Nova",
            ),
            request,
        ))

    assert response.status_code == 200
    configure.assert_called_once_with(
        "custom",
        enabled=False,
        sensitivity=0.65,
        phrase="Nova",
    )
    assert json.loads(response.body)["enabled"] is False


def test_settings_snapshot_uses_the_confirmed_iris_identity_as_suggestion() -> None:
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(hermes_wake=None)))
    with (
        patch("core.hermes_wake.read_hermes_wake_settings", return_value={
            "enabled": False,
            "mode": "hey_hermes",
            "phrase": "hey hermes",
        }),
        patch("core.hermes_wake.IRISConfig.load", return_value=SimpleNamespace(assistant_name="Luna")),
    ):
        snapshot = asyncio.run(hermes_wake_snapshot(request, probe=False))

    assert snapshot["suggested_name"] == "Luna"
    assert snapshot["runtime"]["active"] is False


def test_failed_runtime_stop_never_claims_the_microphone_was_released() -> None:
    runtime = HermesWakeRuntime()
    runtime.listening = True
    runtime.state = "listening"
    runtime.stop_listener = AsyncMock(side_effect=HermesGatewayError("stop failed"))
    request = SimpleNamespace(
        headers={},
        client=SimpleNamespace(host="127.0.0.1"),
        url=SimpleNamespace(hostname="127.0.0.1"),
        app=SimpleNamespace(state=SimpleNamespace(hermes_wake=runtime)),
    )

    response = asyncio.run(update_hermes_wake_runtime(
        HermesWakeRuntimeUpdate(active=False),
        request,
    ))
    body = json.loads(response.body)

    assert response.status_code == 503
    assert body["active"] is True
    assert body["state"] == "listening"
    assert body["detail"] == "stop failed"


def test_iris_config_no_longer_owns_wake_word_or_microphone_secrets() -> None:
    fields = IRISConfig.model_fields
    assert "wake_word" not in fields
    assert "wake_word_mode" not in fields
    assert "porcupine_access_key" not in fields
    assert "voice_listening_enabled" not in fields
    assert "voice_activation_phrase" not in fields


def test_removed_browser_detector_cannot_regress_back_into_the_bundle() -> None:
    assert not (ROOT / "core" / "wake_word.py").exists()
    assert not (ROOT / "core" / "voice_listening.py").exists()
    assert not (ROOT / "face" / "js" / "vad.js").exists()

    sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            ROOT / "core" / "hermes_wake.py",
            ROOT / "face" / "js" / "app.js",
            ROOT / "face" / "js" / "hermes_activity.js",
            ROOT / "face" / "js" / "panel.js",
            ROOT / "wizard" / "ui" / "wizard.js",
            ROOT / "wizard" / "ui" / "index.html",
            ROOT / "wizard" / "ui" / "help.html",
        )
    )
    assert "hola hermes" not in sources.casefold()
    assert "hola iris" not in sources.casefold()
    assert "hey " + "iris" not in sources.casefold()
    assert "voice-listening" not in sources
    assert "key-porcupine" not in sources
    assert "btn-vad" not in sources
    assert "/api/hermes-wake/runtime" in sources


def test_custom_native_phrase_is_preserved_in_advanced_settings_and_summary() -> None:
    advanced = (ROOT / "face" / "js" / "hermes_activity.js").read_text(encoding="utf-8")
    wizard = (ROOT / "wizard" / "ui" / "wizard.js").read_text(encoding="utf-8")
    websocket = (ROOT / "core" / "websocket_server.py").read_text(encoding="utf-8")

    assert 'value="keep" data-voice-custom-option' in advanced
    assert '<option value="custom">Hey + nombre personalizado · Sherpa</option>' in advanced
    assert 'data-voice-field="phrase"' in advanced
    assert "settings.mode === 'custom' ? 'keep'" in advanced
    assert "const currentName = settings.mode === 'custom'" in advanced
    # 2026-10-19: wake setup left the wizard (IRIS OS owns it); every save keeps the existing
    # phrase and preference, so a custom native phrase is never overwritten from there.
    assert "config.hermes_wake_mode = 'keep';" in wizard
    assert "config.hermes_wake_phrase = '';" in wizard
    assert "agent.bridge.start_new_session" in websocket


def test_wizard_wake_defaults_and_summary_do_not_overwrite_or_mislabel() -> None:
    from wizard.app import ConfigUpdate

    wizard = (ROOT / "wizard" / "ui" / "wizard.js").read_text(encoding="utf-8")
    assert ConfigUpdate().hermes_wake_mode == "keep"
    assert ConfigUpdate().hermes_wake_phrase == ""
    assert "config.hermes_wake_mode = 'keep';" in wizard  # the wizard has no wake step any more
    assert "hey_" + "iris" not in wizard
    assert "document.querySelectorAll('.radio-card').forEach(c => c.classList.remove('selected'))" not in wizard
    assert "document.querySelectorAll('label.radio-card').forEach(c => c.classList.remove('selected'))" not in wizard


def test_advanced_settings_expose_only_hermes_native_sensitivity() -> None:
    advanced = (ROOT / "face" / "js" / "hermes_activity.js").read_text(encoding="utf-8")
    styles = (ROOT / "face" / "css" / "styles.css").read_text(encoding="utf-8")
    assert 'data-voice-field="sensitivity"' in advanced
    assert "...(mode === 'custom' ? { phrase } : {})" in advanced
    assert "Más alta = más estricta" in advanced
    assert '.os-voice-phrase-field input[type="range"]' in styles
    assert '.os-voice-phrase-field[hidden]' in styles
    assert "accent-color: var(--iris-os-accent)" in styles


def test_wake_ui_reconciles_runtime_truth_after_failed_mutations() -> None:
    app_source = (ROOT / "face" / "js" / "app.js").read_text(encoding="utf-8")
    advanced = (ROOT / "face" / "js" / "hermes_activity.js").read_text(encoding="utf-8")
    agent_source = (ROOT / "core" / "agent.py").read_text(encoding="utf-8")

    assert "const previousActive = this.hermesWakeActive" in app_source
    assert "/api/hermes-wake/settings?probe=false" in app_source
    assert "/api/hermes-wake/settings?probe=false" in advanced
    assert "IRIS:hermes-wake-runtime-sync" in app_source
    assert "IRIS:hermes-wake-runtime-sync" in advanced
    assert 'await broadcast_to_face("hermes_wake_state", restored)' in agent_source


def test_native_runtime_uses_official_authenticated_headless_server() -> None:
    source = (ROOT / "core" / "hermes_wake.py").read_text(encoding="utf-8")
    assert '"serve"' in source
    assert "/api/ws?token=" in source
    assert "HERMES_DASHBOARD_SESSION_TOKEN" in source
    assert "tui_gateway.entry" not in source


def test_partial_hermes_install_gets_repair_hint_without_raw_traceback() -> None:
    hint = _friendly_native_failure(
        "Traceback: ModuleNotFoundError: No module named 'concurrent_log_handler'"
    )
    assert "Reparar Hermes" in hint
    assert "concurrent_log_handler" in hint
    assert "micrófono no se activó" in hint


def test_requirement_probe_accepts_hermes_020_json_from_stderr() -> None:
    payload = {
        "available": True,
        "provider": "openwakeword",
        "deps_available": False,
        "audio_available": False,
        "hint": "",
    }
    process = SimpleNamespace(
        returncode=0,
        stdout="",
        stderr=json.dumps(json.dumps(payload)) + "\n",
    )
    with (
        tempfile.TemporaryDirectory() as temp_dir,
        patch("core.hermes_wake.resolve_hermes_python", return_value="python"),
        patch("core.hermes_wake.get_hermes_repo", return_value=Path(temp_dir)),
        patch("core.hermes_wake.subprocess.run", return_value=process),
    ):
        result = probe_hermes_wake_requirements()

    assert result["available"] is True
    assert result["ready"] is False
    assert result["provider"] == "openwakeword"
    assert "instalar el motor local" in result["hint"]
    assert result["probe_returncode"] == 0


def test_live_input_error_overrides_optimistic_wake_probe() -> None:
    live, capabilities = _reconcile_wake_input_truth(
        {
            "active": False,
            "state": "off",
            "local_input_available": True,
            "input_device": {"selector": None, "error": "Error querying device -1"},
        },
        {"available": True, "audio_available": True, "ready": True},
    )

    assert live["local_input_available"] is False
    assert live["input_available"] is False
    assert capabilities["audio_available"] is False
    assert capabilities["ready"] is False
    assert "micrófono" in capabilities["hint"]


def test_stopped_wake_runtime_preserves_dependency_failure_in_snapshot() -> None:
    runtime = HermesWakeRuntime()
    runtime.state = "error"
    runtime.last_error = "No module named 'pypinyin'"
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(hermes_wake=runtime)))
    with (
        patch("core.hermes_wake.read_hermes_wake_settings", return_value={
            "enabled": True,
            "mode": "custom",
            "phrase": "hey Luna",
        }),
        patch("core.hermes_wake.IRISConfig.load", return_value=SimpleNamespace(assistant_name="Luna")),
        patch("core.hermes_wake.probe_hermes_wake_requirements", return_value={
            "available": True,
            "audio_available": True,
            "ready": True,
        }),
    ):
        snapshot = asyncio.run(hermes_wake_snapshot(request, probe=True))

    assert snapshot["runtime"]["state"] == "error"
    assert snapshot["runtime"]["error"] == "No module named 'pypinyin'"
    assert snapshot["capabilities"]["ready"] is False
    assert "Reparar Hermes" in snapshot["capabilities"]["hint"]


def test_websocket_transport_resolves_rpc_and_gateway_ready() -> None:
    class FakeWebSocket:
        close_code = None

        async def __aiter__(self):
            yield '{"jsonrpc":"2.0","id":7,"result":{"listening":false}}'
            yield (
                '{"jsonrpc":"2.0","method":"event","params":'
                '{"type":"gateway.ready","payload":{"change_events":true}}}'
            )

    async def scenario() -> None:
        runtime = HermesWakeRuntime()
        runtime.websocket = FakeWebSocket()
        future = asyncio.get_running_loop().create_future()
        runtime._pending[7] = future

        await runtime._read_websocket()

        assert future.result() == {"listening": False}
        assert runtime._ready.is_set()
        queued_type, queued_payload = runtime._events.get_nowait()
        assert queued_type == "gateway.ready"
        assert queued_payload == {"change_events": True}

    asyncio.run(scenario())


if __name__ == "__main__":
    for name, value in sorted(globals().items()):
        if name.startswith("test_") and callable(value):
            value()
            print(f"PASS {name}")
