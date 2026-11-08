"""
IRIS configuration management

Loads settings from ~/.iris/config.json and environment variables.
Cross-platform: Windows (%USERPROFILE%) and Linux (~).
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, PrivateAttr, ValidationError, field_validator
from pydantic_settings import (
    BaseSettings,
    DotEnvSettingsSource,
    EnvSettingsSource,
    SettingsConfigDict,
)

from core.identity_profiles import normalize_trait_ids
from core.vision_policy import VisionPolicySettings


_CONFIG_THREAD_LOCK = threading.RLock()


def _copy_missing_home_entries(
    source: Path,
    destination: Path,
    *,
    overwrite: bool = False,
) -> None:
    """Merge a former home, optionally repairing a known partial copy."""
    destination.mkdir(parents=True, exist_ok=True)
    for entry in source.rglob("*"):
        relative = entry.relative_to(source)
        target = destination / relative
        if entry.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif overwrite or not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w+b",
                    dir=target.parent,
                    prefix=f".{target.name}.",
                    suffix=".tmp",
                    delete=False,
                ) as handle:
                    temporary = Path(handle.name)
                    with entry.open("rb") as source_handle:
                        shutil.copyfileobj(source_handle, handle)
                    handle.flush()
                    os.fsync(handle.fileno())
                shutil.copystat(entry, temporary)
                os.replace(temporary, target)
            finally:
                if temporary:
                    temporary.unlink(missing_ok=True)


def _migrated_profile_path(value: str, legacy_homes: tuple[Path, ...], iris_dir: Path) -> str:
    """Map an absolute path below a former profile to its canonical copy."""
    if not value:
        return value
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        return value
    for legacy in legacy_homes:
        try:
            relative = candidate.resolve(strict=False).relative_to(legacy.resolve(strict=False))
        except (OSError, ValueError):
            continue
        return str(iris_dir / relative)
    return value


def _rewrite_profile_value(value: Any, legacy_homes: tuple[Path, ...], iris_dir: Path) -> Any:
    if isinstance(value, dict):
        return {
            # This marker deliberately retains the former executable path so
            # a copy migration can detect a native process still running
            # there. It is process metadata, never a launch target.
            key: item if key == "legacy_executable" else _rewrite_profile_value(
                item,
                legacy_homes,
                iris_dir,
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_rewrite_profile_value(item, legacy_homes, iris_dir) for item in value]
    if isinstance(value, str):
        return _migrated_profile_path(value, legacy_homes, iris_dir)
    return value


def _rewrite_profile_json(path: Path, legacy_homes: tuple[Path, ...], iris_dir: Path) -> None:
    """Rewrite only absolute references that were rooted in a former home."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
        return
    rewritten = _rewrite_profile_value(document, legacy_homes, iris_dir)
    if rewritten == document:
        return
    if path.name == "companion_install.json" and isinstance(document, dict) and isinstance(rewritten, dict):
        former_executable = str(document.get("executable") or "")
        if former_executable and Path(former_executable).is_file():
            # A copy fallback can leave the former Companion running. Keep its
            # exact path only for process detection; it is never launched.
            rewritten["legacy_executable"] = former_executable
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(rewritten, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def _rewrite_legacy_autostart(legacy_homes: tuple[Path, ...], iris_dir: Path) -> None:
    appdata = os.environ.get("APPDATA", "").strip()
    if not appdata:
        return
    script_path = (
        Path(appdata)
        / "Microsoft"
        / "Windows"
        / "Start Menu"
        / "Programs"
        / "Startup"
        / "IRIS-Desktop-Companion.vbs"
    )
    if not script_path.is_file():
        return
    content = ""
    encoding = ""
    for candidate_encoding in ("utf-16", "utf-8-sig", "utf-8"):
        try:
            content = script_path.read_text(encoding=candidate_encoding)
            encoding = candidate_encoding
            break
        except (OSError, UnicodeError):
            continue
    if not encoding:
        return
    rewritten = content
    for legacy in legacy_homes:
        rewritten = rewritten.replace(str(legacy), str(iris_dir))
    if rewritten != content:
        script_path.write_text(rewritten, encoding=encoding)


def _repair_migrated_profile_references(
    iris_dir: Path,
    legacy_homes: tuple[Path, ...],
) -> None:
    # These documents contain every persisted IRIS path. The recursive mapper
    # also covers future voice/avatar fields without touching external paths.
    for name in ("config.json", "companion_install.json", "companion_installing.json"):
        _rewrite_profile_json(iris_dir / name, legacy_homes, iris_dir)
    _rewrite_legacy_autostart(legacy_homes, iris_dir)


@contextmanager
def _config_write_lock():
    """Serialize config writes across Core, wizard and native surfaces."""
    with _CONFIG_THREAD_LOCK:
        path = get_iris_home() / "config.lock"
        handle = path.open("a+b")
        platform_lock = ""
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                platform_lock = "windows"
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                platform_lock = "posix"
            yield
        finally:
            if platform_lock:
                handle.seek(0)
                if platform_lock == "windows":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()


def get_iris_home() -> Path:
    """Return IRIS' canonical home, migrating former directory names once."""
    from core.profile_paths import validate_profile_home

    explicit = os.environ.get("IRIS_HOME", "").strip()
    if explicit:
        iris_dir = Path(explicit).expanduser()
        if not iris_dir.is_absolute():
            raise ValueError("IRIS_HOME debe ser una ruta absoluta o comenzar por ~/")
        iris_dir = validate_profile_home(iris_dir)
        iris_dir.mkdir(parents=True, exist_ok=True)
        return iris_dir

    base = Path.home()
    iris_dir = validate_profile_home(base / ".iris")
    legacy_homes = (base / ".alice", base / ".IRIS")
    relevant_legacy_homes = tuple(
        legacy for legacy in legacy_homes
        if legacy.exists() and legacy.resolve(strict=False) != iris_dir.resolve(strict=False)
    )
    if relevant_legacy_homes:
        from core.profile_paths import assert_legacy_home_migration_idle

        assert_legacy_home_migration_idle(iris_dir, *relevant_legacy_homes)
    migration_marker = iris_dir / ".legacy-migration-incomplete"
    if not iris_dir.exists():
        for legacy in legacy_homes:
            if not legacy.exists() or legacy.resolve(strict=False) == iris_dir.resolve(
                strict=False
            ):
                continue
            try:
                legacy.replace(iris_dir)
            except OSError:
                # Another IRIS process may have completed the atomic rename
                # after our exists() check. Treat that as success. Only copy
                # while the source still exists.
                if not iris_dir.exists() and legacy.exists():
                    # Write-ahead marker: never start a non-atomic fallback
                    # copy unless a later process can identify and repair a
                    # partial destination after power loss or disk failure.
                    try:
                        iris_dir.mkdir(parents=True, exist_ok=True)
                        migration_marker.write_text(legacy.name, encoding="utf-8")
                    except OSError:
                        return legacy
                    try:
                        shutil.copytree(legacy, iris_dir, dirs_exist_ok=True)
                    except OSError:
                        if legacy.exists():
                            # Every process keeps using the complete former
                            # profile until a later overwrite repair succeeds.
                            return legacy
            break
    iris_dir.mkdir(parents=True, exist_ok=True)

    # Some preview builds created ``.IRIS`` before the former profile
    # profile was migrated. Merge only missing entries so a current IRIS
    # config always wins while credentials, memories and voice assets survive.
    try:
        partial_source_name = migration_marker.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, OSError, UnicodeError):
        partial_source_name = ""
    merge_complete = True
    fallback_legacy = next(
        (legacy for legacy in legacy_homes if legacy.name == partial_source_name),
        None,
    )
    for legacy in legacy_homes:
        if not legacy.exists() or legacy.resolve(strict=False) == iris_dir.resolve(strict=False):
            continue
        try:
            _copy_missing_home_entries(
                legacy,
                iris_dir,
                overwrite=legacy.name == partial_source_name,
            )
        except OSError:
            # The canonical directory remains usable; leave the source intact
            # so migration can be retried on a later writable launch.
            merge_complete = False
            fallback_legacy = fallback_legacy or legacy
            continue
    if merge_complete:
        migration_marker.unlink(missing_ok=True)
        try:
            _repair_migrated_profile_references(iris_dir, legacy_homes)
        except OSError:
            # Migration is best effort: never make an otherwise healthy
            # profile unusable merely because one optional reference is
            # read-only.
            pass
    elif migration_marker.exists() and fallback_legacy:
        return fallback_legacy
    return iris_dir


# Fields that live in Hermes' workspace (config.yaml + .env + auth.json)
# and are NEVER persisted in IRIS' local config. Defined at module scope
# instead of inside IRISConfig because Pydantic v2 reinterprets
# underscore-prefixed class attributes as PrivateAttr descriptors — so
# ``cls._HERMES_OWNED_FIELDS`` from a classmethod returns the descriptor,
# not the frozenset, and iterating over it raises TypeError. That blew up
# IRISConfig.load() silently (the broad ``except Exception`` reset
# ``data = {}`` and every field fell back to its default), which is the
# "wizard shows stale defaults" bug. Module-level dodges the issue.
_HERMES_OWNED_FIELDS: frozenset[str] = frozenset({
    "llm_provider", "llm_model", "llm_api_key", "llm_use_oauth",
})


def _replace_config_file(config_path: Path, data: dict[str, Any]) -> None:
    """Atomically replace config.json; call with ``_config_write_lock`` held."""
    serialized = json.dumps(data, indent=2, ensure_ascii=False)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=config_path.parent,
            prefix=f".{config_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, config_path)
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink(missing_ok=True)


# Answers recorded on their own: the rest of config.json stays byte for byte.
_PROFILE_FLAGS = frozenset({"hermes_security_acknowledged"})


def set_profile_flag(name: str, value: bool) -> None:
    """Set one boolean in config.json without rewriting any other key.

    ``IRISConfig.save`` also normalises the file (for example, it drops the
    legacy LLM fields that Hermes owns); a recorded answer must not do that.
    An unreadable config.json is reported, never replaced.
    """
    if name not in _PROFILE_FLAGS:
        raise ValueError(f"Unknown profile flag: {name}")
    config_path = get_iris_home() / "config.json"
    with _config_write_lock():
        data: dict[str, Any] = {}
        if config_path.exists():
            parsed = json.loads(config_path.read_text(encoding="utf-8"))
            if not isinstance(parsed, dict):
                raise ValueError("config.json is not an object")
            data = parsed
        data[name] = bool(value)
        _replace_config_file(config_path, data)


ENGINE_CONSENT_MESSAGE = (
    "Antes de iniciar el motor, acepta el aviso de seguridad de Hermes en Configuración."
)


def engine_consent_missing(config: IRISConfig | None = None) -> bool:
    """True while the bundled engine must not start or restart (A08.6).

    In the packaged runtime Hermes runs only after the profile accepted its
    security notice, whichever path asks: Wizard, Desktop, Face or the
    channels panel. Development keeps its own installer flow.
    """
    from core.runtime_descriptor import get_runtime

    if not get_runtime().packaged:
        return False
    config = config if config is not None else IRISConfig.load()
    return not getattr(config, "hermes_security_acknowledged", False)


def _derive_llm_from_hermes() -> dict[str, Any]:
    """Read Hermes' on-disk config and return the LLM credential slice.

    Hermes (``~/.hermes/config.yaml`` + ``.env`` + ``auth.json``) is the
    single source of truth for the LLM provider/model/credentials. IRIS
    consults this on every ``IRISConfig.load()`` instead of storing the
    values redundantly in its own config.json.

    Returns a dict with the four ``llm_*`` keys. Any field we can't
    determine gets a sensible empty default — never raises.

    Imports the wizard helpers lazily so this module stays usable when
    the wizard package isn't installed (some lightweight IRIS tools
    only need core.* and shouldn't pull in wizard.providers).
    """
    empty = {"llm_provider": "", "llm_model": "", "llm_api_key": "", "llm_use_oauth": False}
    try:
        from wizard.hermes_config import (
            _read_config_yaml, read_hermes_dotenv, has_hermes_auth_profile,
        )
        from wizard.providers import (
            wizard_id_for_hermes_slug, env_key_for, hermes_id_for,
        )
    except ImportError:
        return empty

    try:
        yaml_data = _read_config_yaml()
        env_data  = read_hermes_dotenv()
    except Exception:
        return empty

    model_section = (yaml_data.get("model") if isinstance(yaml_data, dict) else None) or {}
    hermes_slug   = (model_section.get("provider") or "").strip()
    hermes_model  = (model_section.get("default")  or "").strip()
    if not hermes_slug:
        return empty

    wizard_id = wizard_id_for_hermes_slug(
        hermes_slug,
        base_url=str(model_section.get("base_url") or ""),
    )
    env_key = env_key_for(wizard_id) or f"{hermes_slug.upper().replace('-', '_')}_API_KEY"
    api_key = (env_data.get(env_key, "") or "").strip()
    if hermes_slug == "custom":
        # Hermes 0.20 keeps an explicit custom endpoint credential beside its
        # base_url. It is read only in memory and remains excluded from IRIS'
        # persisted config and browser responses.
        api_key = (model_section.get("api_key") or api_key or "").strip()

    # A distinct OAuth slug in model.provider is an explicit persisted
    # selection and must win over an older API key left in .env so the user
    # can switch back later (openai-api ↔ openai-codex, xai ↔ xai-oauth).
    api_slug = hermes_id_for(wizard_id, use_oauth=False)
    oauth_slug = hermes_id_for(wizard_id, use_oauth=True)
    explicit_oauth_slug = bool(oauth_slug != api_slug and hermes_slug == oauth_slug)
    explicit_api_slug = bool(oauth_slug != api_slug and hermes_slug == api_slug)
    auth_marker = str(env_data.get("IRIS_LLM_AUTH_SELECTION") or "").strip().lower()
    explicit_marker_oauth = auth_marker == f"{hermes_slug}|oauth"
    explicit_marker_api = auth_marker == f"{hermes_slug}|api"

    has_oauth = False
    for cand in {hermes_id_for(wizard_id, use_oauth=True), hermes_id_for(wizard_id), hermes_slug}:
        if cand and has_hermes_auth_profile(cand, auth_type="oauth"):
            has_oauth = True
            break

    return {
        "llm_provider":  wizard_id,
        "llm_model":     hermes_model,
        "llm_api_key":   "" if (explicit_oauth_slug or explicit_marker_oauth) else api_key,
        # Explicit split-provider slugs win in BOTH directions. Without the
        # API-side guard, an old openai-codex/xai-oauth profile made a freshly
        # selected openai-api/xai config look like OAuth whenever its key was
        # still pending.
        "llm_use_oauth": bool(
            explicit_oauth_slug
            or explicit_marker_oauth
            or (
                not explicit_api_slug
                and not explicit_marker_api
                and has_oauth
                and not api_key
            )
        ),
    }


class IRISConfig(BaseSettings):
    """IRIS configuration with migration support for former env names."""

    model_config = SettingsConfigDict(
        env_prefix="IRIS_",
        env_file=".env",
        extra="ignore",
    )
    _loaded_snapshot: dict[str, Any] = PrivateAttr(default_factory=dict)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        legacy_environment = EnvSettingsSource(settings_cls, env_prefix="ALICE_")
        legacy_dotenv = DotEnvSettingsSource(
            settings_cls,
            env_file=settings_cls.model_config.get("env_file"),
            env_prefix="ALICE_",
        )
        return (
            env_settings,
            legacy_environment,
            init_settings,
            dotenv_settings,
            legacy_dotenv,
            file_secret_settings,
        )

    def inherit_loaded_snapshot(self, source: "IRISConfig") -> "IRISConfig":
        """Mark a rebased candidate so save() patches only changed fields."""
        self._loaded_snapshot = source.model_dump(
            exclude={"memory_db_path"} | _HERMES_OWNED_FIELDS
        )
        return self

    # --- Identity & Personality ---
    assistant_name: str = Field("IRIS", min_length=1, max_length=48)
    language: str = "es"
    personality_template: str = "iris"
    # Personality and biography are separate on purpose. Earlier wizard
    # builds stored both in ``custom_personality``; the manager still reads
    # that legacy field when the new fields are empty so existing profiles
    # migrate without losing their text.
    personality_instructions: str = Field("", max_length=4000)
    personality_traits: list[str] = Field(default_factory=list, max_length=6)
    custom_backstory: str = Field("", max_length=4000)
    include_iris_backstory: bool = True
    # Old advanced builds allowed ~/.alice/personality.json to replace the
    # wizard silently.  It is now opt-in so the visible profile is the source
    # of truth; the wizard can import the legacy file explicitly.
    legacy_personality_file_enabled: bool = False
    custom_personality: str = Field("", max_length=4000)  # legacy combined field

    @field_validator("assistant_name")
    @classmethod
    def _clean_assistant_name(cls, value: str) -> str:
        cleaned = " ".join((value or "").split())
        if not cleaned:
            raise ValueError("assistant_name cannot be empty")
        return cleaned

    @field_validator("personality_traits")
    @classmethod
    def _clean_personality_traits(cls, value: list[str]) -> list[str]:
        return normalize_trait_ids(value)

    @field_validator("personality_template")
    @classmethod
    def _clean_personality_template(cls, value: str) -> str:
        # Import lazily to avoid coupling config module initialization to the
        # personality engine. Existing legacy aliases are canonicalized;
        # unknown values become a recoverable validation error in ``load``.
        from core.personality import (
            SUPPORTED_PERSONALITY_TEMPLATES,
            normalize_personality_template,
        )

        normalized = normalize_personality_template(value)
        if normalized not in SUPPORTED_PERSONALITY_TEMPLATES:
            raise ValueError("unsupported personality template")
        return normalized

    # --- Visual edition (selected by the OS installer) ---
    # IDs intentionally match Theme Studio's real preset keys.  The separate
    # boolean preserves existing users' local Theme Studio customization:
    # only an explicit installer choice is allowed to seed/replace it.
    visual_theme: Literal["iris", "sunrise", "verdant", "rose"] = "iris"
    visual_theme_selected: bool = False

    # Hermes' security notice, accepted in the setup Wizard (A08.6). It lives in
    # the profile: Iris Desktop serves the Wizard from a new origin each launch,
    # so browser storage would forget the answer.
    hermes_security_acknowledged: bool = False

    @field_validator("visual_theme", mode="before")
    @classmethod
    def _migrate_visual_theme(cls, value: Any) -> Any:
        return "iris" if str(value or "").casefold() == "alice" else value
    # --- Hermes Agent (Nous Research) ---
    # Hermes' api-server adapter exposes an OpenAI-compatible HTTP
    # endpoint at port 8642 by default. The wizard's
    # `wizard/hermes_config.py` writes API_SERVER_ENABLED=true and
    # API_SERVER_KEY=<token> into ~/.hermes/.env on first sync —
    # without those the api-server platform won't start.
    hermes_url: str = "http://127.0.0.1:8642"
    hermes_api_key: str = ""              # API_SERVER_KEY (Bearer token)
    hermes_model: str = "hermes-agent"    # literal magic string for /v1/chat/completions
    use_picoclaw: bool = False            # legacy: lightweight fallback engine
    picoclaw_binary: str = "picoclaw"

    # --- LLM Provider (what the wizard writes into ~/.hermes/config.yaml) ---
    # The wizard captures these separately so it can rebuild the
    # "<provider>/<model>" string for Hermes' own config.yaml.
    llm_provider: str = ""          # e.g. "anthropic", "ollama", "openrouter"
    llm_api_key: str = ""           # stored as <PROVIDER>_API_KEY in ~/.hermes/.env
    llm_model: str = ""             # e.g. "claude-opus-4-6", "llama3.3", "auto"
    llm_use_oauth: bool = False

    # --- Messaging Channels (optional, passed through to Hermes) ---
    channels: list[str] = Field(default_factory=list)
    telegram_bot_token: str = ""
    discord_bot_token: str = ""
    slack_bot_token: str = ""

    # --- Speech-to-Text ---
    stt_provider: Literal["whisper-api", "whisper-local", "google"] = "whisper-local"
    whisper_model: str = "base"
    whisper_api_key: str = ""
    whisper_api_url: str = "https://api.openai.com/v1/audio/transcriptions"

    # --- Text-to-Speech ---
    tts_provider: Literal[
        "edge-tts", "coqui", "elevenlabs", "minimax", "fish-speech", "kokoro", "neutts",
        "hf-s2s", "fish-audio",
    ] = "edge-tts"
    # Never switch a selected voice engine to a remote service silently.
    # Users can explicitly opt in to Edge-TTS as a runtime fallback.
    tts_allow_remote_fallback: bool = False
    edge_tts_voice: str = "es-MX-DaliaNeural"
    coqui_model: str = "tts_models/es/css10/vits"
    coqui_ref_audio: str = ""
    coqui_language: str = "es"
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = ""
    minimax_api_key: str = ""
    fish_audio_api_key: str = ""
    fish_audio_voice_id: str = ""
    fish_audio_voice_name: str = ""   # shown in the wizard; the id is what Fish uses
    fish_audio_model: Literal["s2.1-pro-free", "s2.1-pro", "s2-pro"] = "s2.1-pro-free"
    # MiniMax default: official Spanish system voice. The original
    # `male-qn-qingse` is Chinese and `es-MX-CarmenNeural` belongs to
    # Edge-TTS, not MiniMax. Users can still override it in the wizard.
    minimax_voice_id: str = "Spanish_Wiselady"
    minimax_tts_model: str = "speech-2.8-hd"   # or speech-2.8-turbo for low latency
    fish_speech_url: str = "http://127.0.0.1:8080"  # Local Fish Speech server
    fish_speech_ref_audio: str = ""             # Path to .wav for voice cloning
    fish_speech_ref_text: str = ""              # Exact transcript for inline cloning
    fish_speech_reference_id: str = ""          # Optional voice already registered in Fish
    fish_speech_format: str = "wav"             # Output format: wav, mp3
    # Kokoro-82M — small local TTS (Apache-2.0). Voice IDs combine
    # language code + speaker, e.g. `af_*` (American English female),
    # `ef_*` (Spanish female), `jf_*` (Japanese female). Full list at
    # https://huggingface.co/hexgrad/Kokoro-82M.
    # Default Spanish female so first-run matches the app's `language="es"`.
    # Users who want English can change to an `af_*`/`am_*` voice.
    kokoro_voice: str = "ef_dora"

    # NeuTTS (neuphonic/neutts) — on-device TTS with 3-second voice
    # cloning. Two model variants:
    #   • neutts-air     (Apache-2.0, ~360M params, full quality)
    #   • neutts-nano    (NeuTTS Open License 1.0, ~120M params, fastest)
    # Both ship in GGUF (Q4/Q8) for llama-cpp-python inference; pair with
    # NeuCodec (proprietary, open-sourced) or its ONNX decoder.
    # `ref_audio` + `ref_text` are REQUIRED — without a reference, NeuTTS
    # has no speaker to clone. The wizard therefore blocks this selection
    # until the user records or uploads an authorised reference.
    # The default is the Spanish Nano GGUF. NeuTTS-Air is English-only;
    # presenting it as multilingual in a Spanish-first wizard produced
    # poor or unusable pronunciation. The full NeuCodec is intentional:
    # the ONNX *decoder-only* repo cannot encode a newly recorded WAV.
    neutts_model_repo:  str = "neuphonic/neutts-nano-spanish-q4-gguf"
    neutts_codec_repo:  str = "neuphonic/neucodec"
    neutts_ref_audio:   str = ""   # absolute path to .wav (3-15s, mono, 16-44kHz)
    neutts_ref_text:    str = ""   # exact transcript of ref_audio
    neutts_device:      Literal["cpu", "cuda", "metal", "rocm"] = "cpu"

    # Hugging Face speech-to-speech 0.2.12 / Qwen3-TTS. IRIS reuses only
    # the upstream TTS handler so Hermes remains the single agent, session
    # and tool runtime. CustomVoice provides preset speakers; selecting a
    # Base model enables local cloning from WAV + exact transcript.
    hf_s2s_model: str = "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice"
    hf_s2s_speaker: str = "Aiden"
    hf_s2s_language: str = "spanish"
    hf_s2s_ref_audio: str = ""
    hf_s2s_ref_text: str = ""
    hf_s2s_device: Literal["auto", "cpu", "cuda", "metal", "rocm"] = "auto"

    # --- Server ---
    # Loopback is the safe default. LAN exposure is an explicit opt-in and
    # the runtime toggle keeps this value in sync with ``lan_access``.
    websocket_host: str = "127.0.0.1"
    websocket_port: int = Field(default=8765, ge=1024, le=65535)
    # Allows an explicitly local container proxy to reach Core without also
    # enabling IRIS' unauthenticated LAN surface or TLS mode.
    container_proxy_mode: bool = False

    # When LAN access uses HTTPS, the packaged desktop companion talks to this
    # separate loopback-only HTTP listener. ``localhost`` is a secure browser
    # context and this avoids weakening certificate validation in the WebView.
    companion_loopback_port: int = Field(default=8766, ge=1024, le=65535)

    # Native companion preferences. The shell is optional and never launches
    # merely because IRIS Core starts.
    companion_enabled: bool = False
    companion_autostart: bool = False
    companion_always_on_top: bool = True
    companion_scale: float = Field(default=1.0, ge=0.75, le=1.5)
    companion_reduced_motion: bool = False
    companion_show_controls: bool = True
    companion_locomotion_enabled: bool = False
    companion_quiet_mode: bool = False
    companion_life: Literal["off", "calm", "active"] = "calm"

    # LAN-access opt-in. When True, IRIS binds to 0.0.0.0 so other
    # devices on the same Wi-Fi can open it, and Hermes' api-server is
    # configured with API_SERVER_HOST=0.0.0.0. Both processes require a
    # restart after changing the value. See `/api/lan-access` in
    # core/websocket_server.py for the runtime toggle endpoint.
    lan_access: bool = False
    lan_pairing_token: str = ""

    # --- Memory ---
    memory_db_path: str = ""

    # --- Avatar ---
    # Always a VRM: the person's own, or IRIS' when this is empty
    # (face/animations/iris.vrm, a factory asset). The old geometric and particle
    # avatars were removed; a saved `avatar_type` is ignored like any unknown key.
    avatar_vrm_path: str = ""
    render_fps_limit: Literal[0, 30, 60] = 60  # 0: display refresh rate, without an Iris cap
    render_quality: Literal["saving", "balanced", "high"] = "balanced"

    # --- Vision Assistant (local, explicit opt-in) ---
    # Legacy perception_* keys remain loadable for old config files, but Core
    # no longer opens a camera. Face/Companion owns the exclusive MediaStream
    # and publishes only bounded, derived signals.
    perception_enabled: bool = False
    perception_camera: int = 0
    perception_yolo: bool = True
    perception_mediapipe: bool = True
    perception_fps: float = 2.0
    vision_assistant_enabled: bool = False
    vision_assistant_mode: Literal["silent"] = "silent"
    vision_assistant_cooldown_minutes: int = Field(default=25, ge=1, le=180)
    vision_assistant_log_retention_days: int = Field(default=7, ge=0, le=30)
    vision_assistant_paused_until: float = Field(default=0.0, ge=0.0)
    vision_assistant_policy: VisionPolicySettings = Field(default_factory=VisionPolicySettings)

    # --- Hardware ---
    hardware_enabled: bool = False
    serial_port: str = ""
    serial_baudrate: int = 9600

    def get_memory_db_path(self) -> Path:
        """Get the SQLite database path."""
        if self.memory_db_path:
            path = Path(self.memory_db_path)
            from core.runtime_descriptor import get_runtime

            runtime = get_runtime()
            if runtime.packaged:
                from core.profile_paths import ProfilePathError

                if not path.is_absolute():
                    raise ProfilePathError("La base de memoria debe usar una ruta absoluta.")
                canonical = path.resolve(strict=False)
                if canonical.is_relative_to(runtime.base):
                    raise ProfilePathError("La memoria debe quedar fuera de la instalación.")
                if path.is_symlink() or (path.exists() and (
                    not path.is_file() or path.stat().st_nlink != 1
                )):
                    raise ProfilePathError("La base de memoria no puede usar un archivo enlazado.")
                return canonical
            return path
        home = get_iris_home()
        target = home / "iris_memory.db"
        legacy = home / "alice_memory.db"
        if not target.exists() and legacy.exists():
            try:
                legacy.replace(target)
            except OSError:
                return legacy
        return target

    # NOTE: ``_HERMES_OWNED_FIELDS`` is defined at MODULE scope (see top
    # of this file), not here. Pydantic v2 turns underscore-prefixed
    # class attributes into PrivateAttr descriptors, which broke the
    # save/load round-trip silently.

    def save(self) -> None:
        """Save current config to ~/.iris/config.json.

        API keys ARE saved since ~/.iris/ is in the user's home directory
        and is NOT tracked by git. The wizard needs to persist them.

        EXCEPT for the LLM credential fields (provider/model/key/oauth) —
        those live in Hermes' workspace (~/.hermes/) and the wizard
        writes them there via wizard.hermes_config.write_hermes_config().
        Persisting them here too caused subtle drift bugs.
        """
        config_path = get_iris_home() / "config.json"
        desired = self.model_dump(exclude={"memory_db_path"} | _HERMES_OWNED_FIELDS)
        with _config_write_lock():
            data = desired
            if self._loaded_snapshot:
                current: dict[str, Any] | None = None
                try:
                    parsed = json.loads(config_path.read_text(encoding="utf-8"))
                    if isinstance(parsed, dict):
                        current = parsed
                except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
                    pass
                if current is not None:
                    for key in _HERMES_OWNED_FIELDS:
                        current.pop(key, None)
                    sentinel = object()
                    changes = {
                        key: value
                        for key, value in desired.items()
                        if self._loaded_snapshot.get(key, sentinel) != value
                    }
                    data = {**current, **changes}
            _replace_config_file(config_path, data)
            self._loaded_snapshot = desired

    @classmethod
    def load(cls) -> "IRISConfig":
        """Load config from ~/.iris/config.json, falling back to defaults.

        LLM credential fields are hydrated from Hermes' on-disk config
        (config.yaml + .env + auth.json) on every load — they are NEVER
        read from config.json. Stale values written by older versions of
        the wizard are silently discarded.
        """
        config_path = get_iris_home() / "config.json"
        data: dict[str, Any] = {}
        if config_path.exists():
            try:
                data = json.loads(config_path.read_text(encoding="utf-8"))
                # Strip any stale Hermes-owned fields a previous wizard
                # version might have written. Source of truth = Hermes.
                for k in _HERMES_OWNED_FIELDS:
                    data.pop(k, None)
            except (json.JSONDecodeError, Exception):
                data = {}
        return cls.from_saved(data)[0]

    @classmethod
    def from_saved(cls, data: dict[str, Any]) -> tuple["IRISConfig", set[str]]:
        """One parsed config.json as settings, and the fields recovered with defaults."""
        data = {key: value for key, value in data.items() if key not in _HERMES_OWNED_FIELDS}
        invalid_fields: set[str] = set()
        try:
            cfg = cls(**data)
        except ValidationError as exc:
            # Existing installations may contain a stale or hand-edited value
            # for one field. Preserve every valid setting and default only the
            # fields Pydantic identified as invalid instead of making IRIS
            # unusable because of the whole file.
            invalid_fields = {
                str(error["loc"][0])
                for error in exc.errors()
                if error.get("loc")
            }
            recovered = {
                key: value for key, value in data.items()
                if key not in invalid_fields
            }
            cfg = cls(**recovered)

        # Older configs were written while 0.0.0.0 was the default even
        # though LAN access was marked off. Enforce the opt-in invariant at
        # load time so an upgrade cannot silently keep exposing IRIS.
        if not cfg.lan_access and not cfg.container_proxy_mode and cfg.websocket_host in {"0.0.0.0", "::"}:
            cfg.websocket_host = "127.0.0.1"
        elif cfg.lan_access and cfg.websocket_host in {"127.0.0.1", "localhost", "::1"}:
            cfg.websocket_host = "0.0.0.0"

        # Hydrate the Hermes-owned slice from Hermes' actual files. Best
        # effort — if Hermes isn't installed yet (first-run) we leave the
        # defaults (empty strings + False) and the wizard's Modo Completo
        # flow will populate them when the user picks a provider.
        try:
            derived = _derive_llm_from_hermes()
            for k, v in derived.items():
                setattr(cfg, k, v)
        except Exception:
            # Never let Hermes-introspection failure block config loading.
            pass

        cfg._loaded_snapshot = cfg.model_dump(
            exclude={"memory_db_path"} | _HERMES_OWNED_FIELDS
        )

        return cfg, invalid_fields


# Import compatibility for extensions built before the IRIS rename. Runtime
# code uses IRISConfig; removing this alias would strand existing integrations.
AliceConfig = IRISConfig
