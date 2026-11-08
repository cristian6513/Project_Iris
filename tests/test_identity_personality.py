"""Small regression tests for IRIS identity, personality and backstory."""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from pydantic import ValidationError

from core.config import IRISConfig
from core.identity_api import (
    IdentityProfileImport,
    IdentityPreviewRequest,
    IdentityUpdate,
    _apply_identity_to_running_agent,
    export_identity_profile_draft,
    import_identity_profile,
    import_legacy_personality,
    preview_identity,
    sync_hermes_identity_from_config,
    update_identity,
)
from core.identity_profiles import (
    apply_profile_to_config,
    compose_personality_instructions,
    export_profile_document,
    history_profile,
    list_identity_history,
    record_identity_history,
    trait_warnings,
)
from core.personality import PERSONALITY_MEMORY_RULES, PRESETS, PersonalityManager
from wizard.app import ConfigUpdate
from starlette.requests import Request


ROOT = Path(__file__).resolve().parents[1]


def _load_personality(config: IRISConfig):
    with tempfile.TemporaryDirectory() as temporary:
        with patch("core.config.get_iris_home", return_value=Path(temporary)):
            return PersonalityManager(config).load()


def test_alice_legacy_id_resolves_the_real_iris_preset() -> None:
    personality = _load_personality(IRISConfig(
        assistant_name="IRIS",
        personality_template="alice",
    ))

    assert personality is not PRESETS["IRIS"]
    assert personality.traits == PRESETS["IRIS"].traits
    assert "asistente visual nacida como una interfaz experimental" in personality.backstory


def test_iris_backstory_can_be_disabled_without_losing_custom_tone() -> None:
    personality = _load_personality(IRISConfig(
        assistant_name="Nova",
        personality_template="alice",
        personality_instructions="Responde con humor ligero y ejemplos breves.",
        include_iris_backstory=False,
    ))
    soul = personality.get_soul()

    assert personality.backstory == ""
    assert "asistente visual nacida como una interfaz experimental" not in soul
    assert "humor ligero" in soul
    assert "tienen prioridad" not in soul
    assert "ajustan o sustituyen" in soul


def test_custom_braces_are_literal_and_backstory_is_optional() -> None:
    personality = _load_personality(IRISConfig(
        assistant_name="Nova",
        personality_template="custom",
        personality_instructions='Explica JSON como {"modo": "calmo"}.',
        custom_backstory="",
    ))
    soul = personality.get_soul()

    assert '{"modo": "calmo"}' in soul
    assert "## Identidad" not in soul
    assert "## Personalización elegida por el usuario" in soul


def test_multiple_traits_compile_their_real_prompts_into_soul() -> None:
    personality = _load_personality(IRISConfig(
        assistant_name="Nova",
        personality_template="custom",
        personality_traits=["warm", "technical", "direct"],
    ))
    soul = personality.get_soul()

    assert "Mantén un trato cálido y humano" in soul
    assert "terminología técnica precisa" in soul
    assert "ve al punto" in soul
    assert "Rasgos seleccionados por el usuario" in soul


def test_detailed_custom_personality_has_no_hidden_concise_rule() -> None:
    personality = _load_personality(IRISConfig(
        assistant_name="Nova",
        personality_template="custom",
        personality_traits=["detailed"],
    ))
    soul = personality.get_soul()

    assert "Explica los pasos" in soul
    assert "Mantén las respuestas concisas" not in soul


def test_authored_personality_follows_trait_recipes_and_wins_on_conflict() -> None:
    compiled = compose_personality_instructions(
        "Cuando te lo pidan, explica cada paso con detalle.",
        ["concise", "warm"],
    )

    assert compiled.index("Rasgos seleccionados") < compiled.index("Instrucciones escritas")
    assert compiled.endswith("explica cada paso con detalle.")


def test_conflicting_trait_recipes_are_visible_before_save() -> None:
    warnings = trait_warnings(["concise", "detailed", "formal", "casual"])

    assert len(warnings) == 2
    assert any("Concisa" in warning and "Detallista" in warning for warning in warnings)
    assert any("Formal" in warning and "Casual" in warning for warning in warnings)


def test_legacy_combined_personality_migrates_without_data_loss() -> None:
    personality = _load_personality(IRISConfig(
        assistant_name="Legacy",
        personality_template="custom",
        custom_personality="Serena, curiosa y directa.",
    ))

    assert personality.personality_instructions == "Serena, curiosa y directa."
    assert "Serena, curiosa y directa." in personality.get_soul()


def test_stale_legacy_custom_text_does_not_leak_into_official_iris() -> None:
    personality = _load_personality(IRISConfig(
        assistant_name="IRIS",
        personality_template="alice",
        custom_personality="Historia antigua que pertenecía a otro personaje.",
    ))

    assert personality.personality_instructions == ""
    assert "Historia antigua" not in personality.get_soul()


def test_renaming_a_preset_does_not_mutate_the_global_template() -> None:
    original_name = PRESETS["jarvis"].name
    first = _load_personality(IRISConfig(
        assistant_name="Nova",
        personality_template="jarvis",
    ))
    second = _load_personality(IRISConfig(
        assistant_name="Jarvis",
        personality_template="jarvis",
    ))

    assert first.name == "Nova"
    assert second.name == "Jarvis"
    assert PRESETS["jarvis"].name == original_name


def test_hand_edited_personality_still_honors_the_wizard_name() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        home = Path(temporary)
        (home / "personality.json").write_text(
            json.dumps({"name": "Old", "backstory": "Historia"}),
            encoding="utf-8",
        )
        with patch("core.config.get_iris_home", return_value=home):
            personality = PersonalityManager(IRISConfig(
                assistant_name="Nueva",
                personality_template="custom",
            )).load()

    assert personality.name == "Nueva"


def test_hand_edited_personality_no_longer_silently_overrides_wizard() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        home = Path(temporary)
        (home / "personality.json").write_text(
            json.dumps({
                "name": "Old",
                "backstory": "Historia antigua",
                "personality_instructions": "Siempre solemne.",
            }),
            encoding="utf-8",
        )
        with patch("core.config.get_iris_home", return_value=home):
            personality = PersonalityManager(IRISConfig(
                assistant_name="Nueva",
                personality_template="custom",
                personality_instructions="Alegre y breve.",
            )).load()

    assert personality.name == "Nueva"
    assert personality.backstory == ""
    assert "Alegre y breve" in personality.personality_instructions
    assert "Siempre solemne" not in personality.personality_instructions


def test_legacy_personality_file_is_available_only_in_explicit_compatibility_mode() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        home = Path(temporary)
        (home / "personality.json").write_text(
            json.dumps({"name": "Old", "backstory": "Historia antigua"}),
            encoding="utf-8",
        )
        with patch("core.config.get_iris_home", return_value=home):
            personality = PersonalityManager(IRISConfig(
                assistant_name="Nueva",
                personality_template="custom",
                legacy_personality_file_enabled=True,
            )).load()

    assert personality.name == "Nueva"
    assert personality.backstory == "Historia antigua"


def test_config_sync_writes_one_consistent_identity_and_soul() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        home = Path(temporary)
        cfg = IRISConfig(
            assistant_name="Nova",
            personality_template="alice",
            personality_instructions="Más directa y técnica.",
            include_iris_backstory=False,
        )
        with (
            patch("core.config.get_iris_home", return_value=home),
            patch("core.workspace_sync.get_hermes_home", return_value=home),
        ):
            result = sync_hermes_identity_from_config(cfg)

        soul = (home / "SOUL.md").read_text(encoding="utf-8")
        identity = (home / "IDENTITY.md").read_text(encoding="utf-8")

    assert result["SOUL.md"] == "changed"
    assert "# Nova" in soul
    assert "Más directa y técnica." in soul
    assert "asistente visual nacida como una interfaz experimental" not in soul
    for rule in PERSONALITY_MEMORY_RULES:
        assert rule in soul
    assert "**Name:** Nova" in identity
    assert "## Backstory" not in identity


def test_quick_identity_swap_updates_the_live_bridge_prompt_source() -> None:
    old_cfg = IRISConfig(assistant_name="IRIS", personality_template="alice")
    agent = SimpleNamespace(
        config=old_cfg,
        personality=None,
        bridge=SimpleNamespace(_personality=None),
    )
    new_cfg = IRISConfig(
        assistant_name="Nova",
        personality_template="custom",
        personality_instructions="Curiosa y concisa.",
    )

    with tempfile.TemporaryDirectory() as temporary:
        with patch("core.config.get_iris_home", return_value=Path(temporary)):
            assert _apply_identity_to_running_agent(agent, new_cfg) is True

    assert agent.config.assistant_name == "Nova"
    assert agent.personality.name == "Nova"
    assert agent.bridge._personality is agent.personality
    assert "Curiosa y concisa." in agent.bridge._personality.get_soul()


def test_quick_identity_endpoint_normalizes_legacy_mode_and_applies_live() -> None:
    cfg = IRISConfig(assistant_name="IRIS", personality_template="alice")
    agent = SimpleNamespace(
        config=cfg.model_copy(deep=True),
        personality=None,
        bridge=SimpleNamespace(_personality=None),
    )
    request = Request({
        "type": "http",
        "method": "POST",
        "path": "/api/update-identity",
        "headers": [],
        "app": SimpleNamespace(state=SimpleNamespace(agent=agent)),
    })
    patch_data = IdentityUpdate(
        assistant_name="Nova",
        personality_template="companion",
        personality_instructions="Serena y precisa.",
        include_iris_backstory=False,
    )

    with (
        patch("core.identity_api.IRISConfig.load", return_value=cfg),
        patch.object(IRISConfig, "save", return_value=None),
        patch("core.identity_api.sync_hermes_identity_from_config", return_value={}),
        tempfile.TemporaryDirectory() as temporary,
        patch("core.config.get_iris_home", return_value=Path(temporary)),
    ):
        response = asyncio.run(update_identity(patch_data, request))

    body = json.loads(response.body)
    assert response.status_code == 200
    assert body["live_applied"] is True
    assert body["changed"]["personality_template"] == "iris"
    assert agent.personality.name == "Nova"
    assert "Serena y precisa." in agent.bridge._personality.get_soul()


def test_identity_endpoint_rejects_an_empty_custom_personality() -> None:
    cfg = IRISConfig(assistant_name="IRIS", personality_template="alice")
    request = Request({
        "type": "http",
        "method": "POST",
        "path": "/api/update-identity",
        "headers": [],
        "app": SimpleNamespace(state=SimpleNamespace(agent=None)),
    })
    patch_data = IdentityUpdate(
        personality_template="custom",
        personality_instructions="",
        personality_traits=[],
        custom_personality="",
    )

    with patch("core.identity_api.IRISConfig.load", return_value=cfg):
        response = asyncio.run(update_identity(patch_data, request))

    body = json.loads(response.body)
    assert response.status_code == 400
    assert body["code"] == "empty_custom_personality"


def test_local_preview_uses_traits_without_contacting_hermes() -> None:
    payload = IdentityPreviewRequest(
        assistant_name="Luna",
        personality_template="custom",
        personality_traits=["creative", "patient"],
        custom_backstory="Protege una biblioteca orbital.",
    )
    with tempfile.TemporaryDirectory() as temporary:
        with (
            patch("core.identity_api.IRISConfig.load", return_value=IRISConfig()),
            patch("core.config.get_iris_home", return_value=Path(temporary)),
        ):
            response = asyncio.run(preview_identity(payload))

    body = json.loads(response.body)
    assert response.status_code == 200
    assert body["status"] == "ready"
    assert "alternativas originales" in body["soul"]
    assert "cambia de enfoque" in body["soul"]
    assert "Protege una biblioteca orbital" in body["soul"]
    assert body["estimated_tokens"] > 0


def test_profile_export_excludes_secrets_and_conversation_memory() -> None:
    cfg = IRISConfig(
        assistant_name="Nova",
        personality_traits=["warm", "direct"],
        elevenlabs_api_key="secret-voice-key",
        hermes_api_key="secret-hermes-key",
        memory_db_path="C:/private/memory.db",
    )
    document = export_profile_document(cfg)
    encoded = json.dumps(document)

    assert document["security"]["credentials_included"] is False
    assert "secret-voice-key" not in encoded
    assert "secret-hermes-key" not in encoded
    assert "memory_db_path" not in encoded
    assert document["profile"]["personality_traits"] == ["warm", "direct"]


def test_profile_import_preserves_credentials_and_rejects_unknown_fields() -> None:
    current = IRISConfig(
        assistant_name="IRIS",
        elevenlabs_api_key="keep-me",
        hermes_api_key="keep-hermes",
    )
    candidate, warnings = apply_profile_to_config(current, {
        "assistant_name": "Luna",
        "personality_template": "custom",
        "personality_traits": ["creative"],
        "tts_provider": "elevenlabs",
        "elevenlabs_voice_id": "voice-01",
    })

    assert candidate.assistant_name == "Luna"
    assert candidate.elevenlabs_api_key == "keep-me"
    assert candidate.hermes_api_key == "keep-hermes"
    assert any("credenciales" in warning for warning in warnings)
    try:
        apply_profile_to_config(current, {"assistant_name": "Luna", "api_key": "bad"})
    except ValueError as exc:
        assert "campos no admitidos" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("Profile import accepted an unknown credential field")


def test_identity_history_is_local_restorable_and_deduplicated() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        home = Path(temporary)
        first = IRISConfig(assistant_name="IRIS", personality_traits=["warm"])
        second = IRISConfig(assistant_name="Nova", personality_traits=["technical"])
        with patch("core.config.get_iris_home", return_value=home):
            entry = record_identity_history(first, source="test", if_different_from=second)
            duplicate = record_identity_history(first, source="test", if_different_from=second)
            history = list_identity_history()
            restored = history_profile(entry["id"] if entry else "")

    assert entry is not None
    assert duplicate is None
    assert len(history) == 1
    assert restored is not None
    assert restored["assistant_name"] == "IRIS"


def test_identity_payloads_have_backend_length_limits() -> None:
    try:
        ConfigUpdate(assistant_name="x" * 49)
    except ValidationError:
        pass
    else:  # pragma: no cover - makes the assertion runner independent
        raise AssertionError("ConfigUpdate accepted an overlong assistant name")

    try:
        IdentityUpdate(personality_instructions="x" * 4001)
    except ValidationError:
        pass
    else:  # pragma: no cover
        raise AssertionError("IdentityUpdate accepted oversized instructions")

    try:
        IdentityPreviewRequest(assistant_name="   ")
    except ValidationError:
        pass
    else:  # pragma: no cover
        raise AssertionError("IdentityPreviewRequest accepted a whitespace-only name")


def test_unknown_personality_is_rejected_and_existing_config_recovers() -> None:
    current = IRISConfig(assistant_name="IRIS", personality_template="alice")
    request = Request({
        "type": "http",
        "method": "POST",
        "path": "/api/identity-profile/import",
        "headers": [],
        "app": SimpleNamespace(state=SimpleNamespace(agent=None)),
    })
    payload = IdentityProfileImport(document={
        "assistant_name": "Nova",
        "personality_template": "unsupported-mode",
        "personality_instructions": "Clara.",
    })

    with tempfile.TemporaryDirectory() as temporary:
        home = Path(temporary)
        with (
            patch("core.identity_api.IRISConfig.load", return_value=current),
            patch.object(IRISConfig, "save", return_value=None) as save_mock,
            patch("core.config.get_iris_home", return_value=home),
        ):
            response = asyncio.run(import_identity_profile(payload, request))

        body = json.loads(response.body)
        assert response.status_code == 400
        assert "no está soportada" in body["message"]
        assert not save_mock.called

        legacy_candidate, _ = apply_profile_to_config(current, {
            "assistant_name": "Legacy",
            "personality_template": "companion",
        })
        assert legacy_candidate.personality_template == "iris"

        (home / "config.json").write_text(json.dumps({
            "assistant_name": "Recovered",
            "personality_template": "unsupported-mode",
        }), encoding="utf-8")
        with patch("core.config.get_iris_home", return_value=home):
            recovered = IRISConfig.load()

    assert recovered.assistant_name == "Recovered"
    assert recovered.personality_template == "iris"


def test_unknown_personality_draft_export_is_rejected() -> None:
    payload = IdentityPreviewRequest(
        assistant_name="Nova",
        personality_template="unsupported-mode",
        personality_instructions="Clara.",
    )
    with patch("core.identity_api.IRISConfig.load", return_value=IRISConfig()):
        response = asyncio.run(export_identity_profile_draft(payload))

    body = json.loads(response.body)
    assert response.status_code == 400
    assert body["code"] == "unsupported_personality"


def test_duplicate_traits_are_normalized_with_a_warning() -> None:
    cfg = IRISConfig(assistant_name="IRIS", personality_template="alice")
    request = Request({
        "type": "http",
        "method": "POST",
        "path": "/api/update-identity",
        "headers": [],
        "app": SimpleNamespace(state=SimpleNamespace(agent=None)),
    })
    with tempfile.TemporaryDirectory() as temporary:
        with (
            patch("core.identity_api.IRISConfig.load", return_value=cfg),
            patch.object(IRISConfig, "save", return_value=None),
            patch("core.identity_api.sync_hermes_identity_from_config", return_value={}),
            patch("core.config.get_iris_home", return_value=Path(temporary)),
        ):
            response = asyncio.run(update_identity(
                IdentityUpdate(personality_traits=["warm", "warm"]),
                request,
            ))

    body = json.loads(response.body)
    assert response.status_code == 200
    assert body["changed"]["personality_traits"] == ["warm"]
    assert any("repetidos" in warning for warning in body["warnings"])


def test_legacy_import_preserves_current_name_when_old_name_is_blank() -> None:
    current = IRISConfig(assistant_name="IRIS", personality_template="alice")
    request = Request({
        "type": "http",
        "method": "POST",
        "path": "/api/identity-legacy/import",
        "headers": [],
        "app": SimpleNamespace(state=SimpleNamespace(agent=None)),
    })
    with tempfile.TemporaryDirectory() as temporary:
        home = Path(temporary)
        (home / "personality.json").write_text(json.dumps({
            "name": "   ",
            "backstory": "Historia antigua",
        }), encoding="utf-8")
        with (
            patch("core.identity_api.get_iris_home", return_value=home),
            patch("core.identity_api.IRISConfig.load", return_value=current),
            patch.object(IRISConfig, "save", return_value=None),
            patch("core.identity_api.sync_hermes_identity_from_config", return_value={}),
            patch("core.config.get_iris_home", return_value=home),
        ):
            response = asyncio.run(import_legacy_personality(request))

    body = json.loads(response.body)
    assert response.status_code == 200
    assert body["profile"]["assistant_name"] == "IRIS"
    assert any("nombre válido" in warning for warning in body["warnings"])


def test_wizard_identity_ui_separates_personality_from_backstory_safely() -> None:
    html = (ROOT / "wizard" / "ui" / "index.html").read_text(encoding="utf-8")
    js = (ROOT / "wizard" / "ui" / "wizard.js").read_text(encoding="utf-8")

    assert 'id="assistant-name"' in html
    assert 'id="iris-personality-instructions"' in html
    assert 'id="custom-personality-instructions"' in html
    assert 'id="include-iris-backstory"' in html
    assert 'id="identity-trait-grid"' in html
    assert 'id="identity-prompt-preview"' in html
    assert 'id="identity-history-list"' in html
    assert 'id="identity-profile-file"' in html
    assert "Cada rasgo añade una instrucción real al prompt" in html
    assert "no se exporta con el perfil" in html
    assert "Usar como guía" in html
    assert "<strong>Personalidad *</strong>" not in html
    assert "Opcional si eliges uno o más rasgos combinables" in html
    assert "No afecta el costo ni los tokens" not in html
    assert "custom_backstory: personality === 'custom'" in js
    assert "${escapeHtml(resolvedAssistantName)}" in js
    assert "${escapeHtml(customBackstory.slice(0, 80))}" in js
    assert "validateIdentityStep()" in js
    assert "personality_traits: personalityTraits" in js
    assert "/api/identity-preview/sample" in js
    assert "/api/identity-profile/import" in js
    import re
    version = re.search(r'/ui/wizard\.js\?v=(\d+)', html)
    assert version and int(version[1]) >= 86
    personality_handler = js[js.index("// Handle personality toggle"):js.index("// Handle avatar type toggle")]
    assert "scheduleIdentityPreview();" in personality_handler
    assert "identityPreviewRequestId" in js


def test_quick_panel_keeps_only_avatar_and_room() -> None:
    # qa9: name, personality and backstory moved out of Quick Configuration;
    # they are edited in Configuración (the wizard), never half-saved here.
    panel = (ROOT / "face" / "js" / "panel.js").read_text(encoding="utf-8")
    server = (ROOT / "core" / "websocket_server.py").read_text(encoding="utf-8")
    settings = panel[panel.index("_buildSettingsSection() {"):panel.index("// ── Desktop Companion Section")]

    for removed in ("quick-name", "quick-personality", "quick-custom-backstory",
                    "quick-include-iris-backstory", "btn-launch-wizard", "personality_instructions"):
        assert removed not in settings
    # Always a VRM (2026-10-28): the model on stage, the person's own or IRIS', no kinds to pick.
    assert 'id="quick-avatar-model"' in settings and 'id="theme-select"' in settings
    assert 'id="quick-avatar"' not in settings and "Aplicar avatar" not in settings
    assert "await avatarActions.useIris()" in settings and "await avatarActions.upload(file)" in settings
    # The actions (face/js/avatar_intent.js, tested in tests/js/avatar_intent.test.mjs)
    # clear only the model path, and show IRIS once Core kept that.
    actions = (ROOT / "face" / "js" / "avatar_intent.js").read_text(encoding="utf-8")
    iris_block = actions[actions.index("async useIris()"):actions.index("async upload(file)")]
    assert "JSON.stringify({ avatar_vrm_path: '' })" in iris_block
    assert iris_block.index("ok = res.ok") < iris_block.index("dispatch(")
    import re
    source = (ROOT / "face" / "js" / "app.js").read_text(encoding="utf-8")
    panel_version = re.search(r"panel\.js\?v=(\d+)", source)
    assert panel_version and int(panel_version[1]) >= 31
    html = (ROOT / "face" / "index.html").read_text(encoding="utf-8")
    version = re.search(r'js/app\.js\?v=(\d+)', html)
    assert version and int(version[1]) >= 196
    assert 'elif msg_type == "set_personality":' in server


def test_master_doc_describes_real_hermes_personality_sync() -> None:
    master = (ROOT / "docs" / "iris_os_master.md").read_text(encoding="utf-8")

    assert "sincroniza nombre, personalidad, backstory y reglas de memoria" in master
    assert "falta sincronización explícita con perfiles/SOUL de Hermes" not in master
