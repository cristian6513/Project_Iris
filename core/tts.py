"""
IRIS TTS — Text-to-Speech Selector (Production)

Providers:
    • edge-tts    —  Free, Spanish-native (es-MX-DaliaNeural), requires internet
    • coqui       —  Open-source, runs 100 % local, moderate quality
    • elevenlabs  —  Premium API, best quality
    • hf-s2s      —  Qwen3-TTS handler from Hugging Face speech-to-speech

Audio playback pipeline (cross-platform):
    1. Provider generates audio (MP3 or WAV bytes)
    2. Packaged decodes to PCM via the private, bounded FFmpeg. Development uses:
       a) `pydub` (if installed) — handles MP3/WAV/OGG
       b) `ffmpeg` subprocess   — universal fallback
       c) `wave` stdlib         — WAV-only last resort
    3. Play PCM through `sounddevice` (no pop-up windows, no external players)

All providers also return optional viseme / word-boundary data for lip-sync.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import subprocess
import wave
from pathlib import Path
from typing import Any

import numpy as np
import sounddevice as sd

from core.config import IRISConfig, get_iris_home
from core.runtime_descriptor import get_runtime
from core.speech_text import text_for_speech
from core.tts_engines import (  # noqa: F401 - re-exported provider API
    HF_S2S_QWEN_MODELS,
    HF_S2S_QWEN_SPEAKERS,
    CoquiTTSProvider,
    HFSpeechToSpeechQwenProvider,
    KokoroTTSProvider,
    NeuTTSProvider,
    TTSProvider,
    _hf_s2s_device,
    _kokoro_lang_code,
    _load_hf_s2s_qwen_runtime,
)

logger = logging.getLogger("IRIS.tts")

# What an engine is built from; any other saved setting leaves it running.
_VOICE_SETTING_PREFIXES = (
    "tts_", "edge_tts_", "elevenlabs_", "minimax_", "fish_",
    "coqui_", "kokoro_", "neutts_", "hf_s2s_",
)


def _is_voice_setting(name: str) -> bool:
    return name == "language" or name.startswith(_VOICE_SETTING_PREFIXES)


def _voice_settings(config: IRISConfig) -> dict[str, Any]:
    return {name: value for name, value in config.model_dump().items() if _is_voice_setting(name)}


# ════════════════════════════════════════════════════════════
# Audio decode helpers
# ════════════════════════════════════════════════════════════

def _decode_with_pydub(audio_bytes: bytes) -> tuple[np.ndarray, int] | None:
    """Decode any audio format → numpy int16 array + sample rate."""
    try:
        from pydub import AudioSegment

        seg = AudioSegment.from_file(io.BytesIO(audio_bytes))
        seg = seg.set_channels(1)           # mono
        seg = seg.set_sample_width(2)       # 16-bit
        sr = seg.frame_rate
        samples = np.frombuffer(seg.raw_data, dtype=np.int16)
        return samples, sr
    except ImportError:
        return None
    except Exception as e:
        logger.debug("pydub decode failed: %s", e)
        return None


def _decode_with_ffmpeg(audio_bytes: bytes) -> tuple[np.ndarray, int] | None:
    """Decode using external ffmpeg binary → PCM int16, 24 kHz mono."""
    sr = 24_000
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error",
                "-i", "pipe:0",
                "-f", "s16le", "-acodec", "pcm_s16le",
                "-ar", str(sr), "-ac", "1",
                "pipe:1",
            ],
            input=audio_bytes,
            capture_output=True,
            timeout=30,
        )
        if result.returncode != 0:
            logger.debug("ffmpeg error: %s", result.stderr.decode()[:200])
            return None
        pcm = np.frombuffer(result.stdout, dtype=np.int16)
        return pcm, sr
    except FileNotFoundError:
        return None
    except Exception as e:
        logger.debug("ffmpeg decode failed: %s", e)
        return None


def _decode_wav_stdlib(audio_bytes: bytes) -> tuple[np.ndarray, int] | None:
    """Decode a WAV file using Python's stdlib wave module."""
    try:
        with wave.open(io.BytesIO(audio_bytes), "rb") as wf:
            sr = wf.getframerate()
            raw = wf.readframes(wf.getnframes())
            sw = wf.getsampwidth()
        if sw == 2:
            samples = np.frombuffer(raw, dtype=np.int16)
        elif sw == 1:
            samples = (np.frombuffer(raw, dtype=np.uint8).astype(np.int16) - 128) * 256
        else:
            return None
        return samples, sr
    except Exception:
        return None


def decode_audio(audio_bytes: bytes) -> tuple[np.ndarray, int]:
    """
    Decode audio bytes (MP3, WAV, OGG, etc.) → numpy int16 array + sample rate.

    Packaged uses the private, bounded decoder. Development tries
    pydub → ffmpeg → stdlib wave, in order.
    Raises RuntimeError if all fail.
    """
    if get_runtime().packaged:
        from core.audio_decoder import decode_private_audio

        return decode_private_audio(audio_bytes)

    # Fast path: check if it's a plain WAV
    if audio_bytes[:4] == b"RIFF":
        result = _decode_wav_stdlib(audio_bytes)
        if result is not None:
            return result

    # Try pydub (handles MP3, OGG, etc. via ffmpeg backend)
    result = _decode_with_pydub(audio_bytes)
    if result is not None:
        return result

    # Try raw ffmpeg
    result = _decode_with_ffmpeg(audio_bytes)
    if result is not None:
        return result

    # Last resort: maybe it's WAV after all
    result = _decode_wav_stdlib(audio_bytes)
    if result is not None:
        return result

    raise RuntimeError(
        "Cannot decode audio. Install one of:\n"
        "  pip install pydub     (recommended)\n"
        "  choco install ffmpeg  (Windows) or  apt install ffmpeg (Linux)"
    )


# ── Edge-TTS ─────────────────────────────────────────────

class EdgeTTSProvider(TTSProvider):
    """Microsoft Edge TTS — free, high-quality, Spanish-native."""

    def __init__(self, voice: str = "es-MX-DaliaNeural") -> None:
        self.voice = voice
        # Tracked at init: if the `edge_tts` package isn't installed,
        # we MUST refuse synthesize calls cleanly instead of crashing
        # mid-conversation. The previous code logged a warning at
        # init time and then did an unguarded ``import edge_tts``
        # inside synthesize() — the ImportError would propagate up
        # through agent._respond and silently kill the turn, leaving
        # the avatar in "thinking" forever with no audio and no
        # error surfaced to the user.
        self._available: bool = False
        self.last_error = ""

    async def initialize(self) -> None:
        try:
            import edge_tts  # noqa: F401
            self._available = True
            logger.info("Edge-TTS ready — voice: %s", self.voice)
        except ImportError:
            self._available = False
            logger.error("edge-tts not installed.  Run:  pip install edge-tts")

    @staticmethod
    def _failure_reason(exc: BaseException) -> str:
        """A short cause for the user. Never the exception text: its URL carries a token."""
        status = getattr(exc, "status", None)
        if status == 403:
            return ("Microsoft rechazó la conexión (HTTP 403). Suele pasar cuando el componente "
                    "de voz de IRIS quedó desactualizado o la hora del equipo no es correcta.")
        if isinstance(status, int):
            return f"El servicio de voz de Microsoft respondió con un error (HTTP {status})."
        if isinstance(exc, (OSError, asyncio.TimeoutError)) or "Connector" in type(exc).__name__:
            return "No se pudo conectar con el servicio de voz de Microsoft. Comprueba la conexión a internet."
        if type(exc).__name__ == "NoAudioReceived":
            return "Microsoft no devolvió audio para esta voz o frase."
        return f"Edge-TTS falló ({type(exc).__name__})."

    async def synthesize(self, text: str) -> tuple[bytes, list[dict[str, Any]]]:
        self.last_error = ""
        if not self._available:
            # Fail fast with a logged warning and empty bytes so the
            # caller's `if audio_bytes:` guard kicks in cleanly. The
            # avatar will still emit `speech_start` / `speech_end` and
            # the chat bubble shows — just no audio.
            logger.warning(
                "Edge-TTS synthesize called but package isn't available. "
                "Returning empty audio. Install with `pip install edge-tts`.",
            )
            self.last_error = "El componente de voz Edge-TTS no está instalado en IRIS."
            return b"", []
        import edge_tts

        # FIX T1 — resource leak. Each `edge_tts.Communicate(...)` builds
        # an aiohttp ClientSession + websocket connection. The upstream
        # library does NOT close them automatically when the object is
        # garbage-collected; under rapid back-to-back utterances (or when
        # stream() raises mid-flight on a network drop) we leak sockets
        # until the asyncio event loop eventually reaps them. Wrap the
        # iteration in try/finally and call `.close()` explicitly.
        #
        # We probe for the cleanup method dynamically because edge-tts
        # versions disagree on the API surface:
        #   - some expose async `close()`
        #   - some expose async `aclose()`
        #   - some are usable as `async with` context managers (via
        #     __aenter__/__aexit__) — older versions are not.
        # The defensive ladder below handles all three without forcing a
        # version pin.
        communicate = edge_tts.Communicate(text, self.voice)
        audio_chunks: list[bytes] = []
        viseme_data: list[dict[str, Any]] = []

        try:
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio_chunks.append(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    viseme_data.append({
                        "time": chunk["offset"] / 10_000_000,       # 100 ns → seconds
                        "duration": chunk["duration"] / 10_000_000,
                        "text": chunk.get("text", ""),
                    })
        except Exception as exc:
            # The caller already turns a failure into empty audio; keep why.
            self.last_error = self._failure_reason(exc)
            logger.error("Edge-TTS failed: %s (%s)", self.last_error, type(exc).__name__)
            return b"", []
        finally:
            # Try the most explicit shutdown path first. We swallow
            # cleanup errors so a flaky network teardown can't mask the
            # original exception (if any) raised inside the iterator.
            closer = getattr(communicate, "close", None) or getattr(
                communicate, "aclose", None,
            )
            if closer is not None:
                try:
                    result = closer()
                    if asyncio.iscoroutine(result):
                        await result
                except Exception as exc:
                    logger.debug("edge-tts communicate close failed: %s", exc)

        audio = b"".join(audio_chunks)
        logger.info(
            "Edge-TTS: %d bytes, %d word boundaries.",
            len(audio), len(viseme_data),
        )
        return audio, viseme_data


# ── ElevenLabs ───────────────────────────────────────────

class ElevenLabsTTSProvider(TTSProvider):
    """ElevenLabs — premium, best quality."""

    def __init__(self, api_key: str, voice_id: str) -> None:
        self.api_key = api_key
        self.voice_id = voice_id

    async def initialize(self) -> None:
        if not self.api_key:
            logger.warning("ElevenLabs API key not configured (IRIS_ELEVENLABS_API_KEY).")
        else:
            logger.info("ElevenLabs ready — voice: %s", self.voice_id)

    async def synthesize(self, text: str) -> tuple[bytes, list[dict[str, Any]]]:
        if not self.api_key:
            logger.error("ElevenLabs: no API key.")
            return b"", []

        import httpx

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.post(
                    f"https://api.elevenlabs.io/v1/text-to-speech/{self.voice_id}",
                    headers={
                        "xi-api-key": self.api_key,
                        "Content-Type": "application/json",
                    },
                    json={
                        "text": text,
                        "model_id": "eleven_multilingual_v2",
                        "voice_settings": {
                            "stability": 0.5,
                            "similarity_boost": 0.75,
                        },
                    },
                )

            if r.status_code == 200:
                logger.info("ElevenLabs: %d bytes.", len(r.content))
                return r.content, []
            else:
                logger.error("ElevenLabs %d: %s", r.status_code, r.text[:200])
                return b"", []

        except Exception as e:
            logger.error("ElevenLabs failed: %s", e)
            return b"", []


# ── MiniMax ───────────────────────────────────────────────

class MiniMaxTTSProvider(TTSProvider):
    """MiniMax TTS — high-quality, multilingual, 300+ voices, streaming."""

    API_URL = "https://api.minimax.io/v1/t2a_v2"

    def __init__(
        self,
        api_key: str,
        voice_id: str = "Spanish_Wiselady",
        model: str = "speech-2.8-hd",
    ) -> None:
        self.api_key = api_key
        self.voice_id = voice_id
        self.model = model

    async def initialize(self) -> None:
        if not self.api_key:
            logger.warning("MiniMax API key not configured (IRIS_MINIMAX_API_KEY).")
        else:
            logger.info(
                "MiniMax TTS ready — model: %s, voice: %s",
                self.model, self.voice_id,
            )

    async def synthesize(self, text: str) -> tuple[bytes, list[dict[str, Any]]]:
        if not self.api_key:
            logger.error("MiniMax: no API key.")
            return b"", []

        import httpx

        payload = {
            "model": self.model,
            "text": text,
            "stream": False,
            "language_boost": "auto",
            "output_format": "hex",
            "voice_setting": {
                "voice_id": self.voice_id,
                "speed": 1.0,
                "vol": 1.0,
                "pitch": 0,
            },
            "audio_setting": {
                "sample_rate": 32000,
                "bitrate": 128000,
                "format": "mp3",
                "channel": 1,
            },
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.post(
                    self.API_URL,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )

            if r.status_code == 200:
                data = r.json()

                # T2A v2 returns hex when output_format="hex". Decoding this
                # value as base64 produces non-empty but corrupt audio.
                audio_hex = data.get("data", {}).get("audio", "")
                if audio_hex:
                    try:
                        audio_bytes = bytes.fromhex(audio_hex)
                    except ValueError:
                        logger.error("MiniMax: invalid hex audio payload.")
                        return b"", []
                    logger.info("MiniMax TTS: %d bytes.", len(audio_bytes))
                    return audio_bytes, []

                # Some endpoints return raw audio bytes directly
                if r.headers.get("content-type", "").startswith("audio/"):
                    logger.info("MiniMax TTS: %d bytes.", len(r.content))
                    return r.content, []

                logger.error("MiniMax: unexpected response format.")
                return b"", []
            else:
                logger.error("MiniMax %d: %s", r.status_code, r.text[:200])
                return b"", []

        except Exception as e:
            logger.error("MiniMax failed: %s", e)
            return b"", []


class FishAudioTTSProvider(TTSProvider):
    """Hosted Fish Audio: explicit API key/model, no local weights or server."""

    def __init__(self, api_key: str, voice_id: str, model: str = "s2.1-pro-free") -> None:
        self.api_key, self.voice_id, self.model = api_key, voice_id, model
        self.last_error = ""

    async def initialize(self) -> None:
        # Opening the wizard/agent never generates speech or consumes credit.
        pass

    async def synthesize(self, text: str) -> tuple[bytes, list[dict[str, Any]]]:
        from core.fish_audio import FishAudioError, synthesize

        self.last_error = ""
        try:
            return await synthesize(self.api_key, self.voice_id, self.model, text), []
        except FishAudioError as exc:
            self.last_error = str(exc)
            logger.warning("Fish Audio: %s", self.last_error)
            return b"", []


# ── Fish Speech ──────────────────────────────────────────

class FishSpeechTTSProvider(TTSProvider):
    """
    Fish Audio S2 — open-source TTS served by a separate API process.

    Requires a running Fish Speech server:
        docker compose --profile server up

    Docs: https://speech.fish.audio/inference/#server-inference

    Emotion → prompt-tag mapping used by IRIS:
        neutral   → (no tag)
        feliz     → [excited]
        triste    → [sad]
        pensativa → [thoughtful]
        sorprendida → [surprised]
        enojada   → [angry]
    """

    EMOTION_TAGS: dict[str, str] = {
        "neutral":      "",
        "feliz":        "[excited] ",
        "triste":       "[sad] ",
        "pensativa":    "[thoughtful] ",
        "sorprendida":  "[surprised] ",
        "enojada":      "[angry] ",
    }

    def __init__(
        self,
        api_url: str = "http://127.0.0.1:8080",
        reference_audio: str = "",
        reference_text: str = "",
        reference_id: str = "",
        emotion: str = "neutral",
        format: str = "wav",
    ) -> None:
        self.api_url    = api_url.rstrip("/")
        self.ref_audio  = reference_audio   # path to .wav for voice cloning
        self.ref_text   = reference_text
        self.reference_id = reference_id
        self.emotion    = emotion
        self.format     = format
        self._client: Any = None

    async def initialize(self) -> None:
        try:
            import httpx  # noqa: F401
            # Quick health check
            async with httpx.AsyncClient(timeout=3.0) as cl:
                r = await cl.get(f"{self.api_url}/v1/health")
                if r.status_code == 200:
                    logger.info("Fish Speech server ready at %s", self.api_url)
                else:
                    logger.warning(
                        "Fish Speech at %s returned %d — will attempt synthesis anyway.",
                        self.api_url, r.status_code,
                    )
        except ImportError:
            logger.error("httpx not installed — run: pip install httpx")
        except Exception:
            logger.warning(
                "Fish Speech server not found at %s. "
                "Start its API server using the official Fish Speech instructions.",
                self.api_url,
            )

    def set_emotion(self, emotion: str) -> None:
        """Update emotion so next synthesize call uses the right tag."""
        self.emotion = emotion

    async def synthesize(self, text: str) -> tuple[bytes, list[dict[str, Any]]]:
        import httpx

        # Prepend emotion tag
        tag = self.EMOTION_TAGS.get(self.emotion, "")
        tagged_text = f"{tag}{text}"

        payload: dict[str, Any] = {
            "text": tagged_text,
            "format": self.format,
            "streaming": False,
        }

        # Fish S2 accepts either a server-managed reference id or an inline
        # ``references`` entry containing both audio and its exact transcript.
        # Sending the legacy ``reference_audio`` scalar was silently ignored
        # by current servers and made cloning sound like the default speaker.
        if self.reference_id:
            payload["reference_id"] = self.reference_id
        elif self.ref_audio and self.ref_text and Path(self.ref_audio).is_file():
            import base64
            ref_bytes = Path(self.ref_audio).read_bytes()
            payload["references"] = [{
                "audio": base64.b64encode(ref_bytes).decode(),
                "text": self.ref_text,
            }]

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.post(
                    f"{self.api_url}/v1/tts",
                    json=payload,
                )

            if r.status_code == 200:
                audio_bytes = r.content
                logger.info(
                    "Fish Speech [%s]: %d chars → %d bytes audio",
                    self.emotion, len(text), len(audio_bytes),
                )
                return audio_bytes, []   # Fish Speech doesn't return viseme data
            else:
                logger.error("Fish Speech %d: %s", r.status_code, r.text[:200])
                return b"", []

        except Exception as e:
            logger.error("Fish Speech synthesis failed: %s", e)
            return b"", []


# ── Local engines in approved profile extras (A08.5) ────────────

LOCAL_ENGINE_PROVIDERS: dict[str, type[TTSProvider]] = {
    "coqui": CoquiTTSProvider,
    "kokoro": KokoroTTSProvider,
    "neutts": NeuTTSProvider,
    "hf-s2s": HFSpeechToSpeechQwenProvider,
}


class IsolatedExtraTTSProvider(TTSProvider):
    """Run a local engine in its extra's isolated worker, never inside Core.

    The worker loads the same ``core/tts_engines`` provider with only the
    extra's packages on ``sys.path``. A crashed or timed-out worker returns
    empty audio, so the selector's explicit fallback policy still applies;
    the next utterance starts a fresh worker.
    """

    LOAD_TIMEOUT = 900.0   # first use may download model weights into the profile
    SYNTH_TIMEOUT = 180.0
    # Upper bound for an engine's declared budget (seconds per character).
    MAX_SECONDS_PER_CHAR = 3.0

    def __init__(self, engine: str, options: dict[str, str], environment: Any) -> None:
        self.engine = engine
        self.options = options
        self.environment = environment
        self._worker: Any = None
        self._lock = asyncio.Lock()
        self._seconds_per_char = 0.0
        # Readable reason of the last failure, for the wizard's preview.
        self.last_error = ""

    @classmethod
    def max_synth_timeout(cls, text: str) -> float:
        """Longest time a synthesis of ``text`` may take with any declared budget."""
        return max(cls.SYNTH_TIMEOUT, len(text) * cls.MAX_SECONDS_PER_CHAR)

    def _synth_timeout(self, text: str) -> float:
        return max(self.SYNTH_TIMEOUT, len(text) * self._seconds_per_char)

    async def _ensure_loaded(self) -> bool:
        from core.extras import ExtrasError, ExtraWorker, ProfilePathError

        if self._worker is not None and self._worker.running:
            return True
        worker = ExtraWorker(self.environment)
        try:
            await worker.start()
            reply = await worker.request(
                {"op": "load", "engine": self.engine, "options": self.options},
                timeout=self.LOAD_TIMEOUT,
            )
        except asyncio.CancelledError:
            await worker.close()
            raise
        except (ExtrasError, ProfilePathError, OSError, TimeoutError) as exc:
            # ProfilePathError: a cache now aliases a folder outside the profile.
            await worker.close()
            logger.error("Extra %s failed to start: %s", self.engine, exc)
            self.last_error = str(exc) or type(exc).__name__
            return False
        if reply.get("ok") is not True:
            await worker.close()
            logger.error("Extra %s could not load: %s %s", self.engine,
                         reply.get("error"), reply.get("detail", ""))
            self.last_error = str(reply.get("detail") or reply.get("error") or "")[:500]
            return False
        budget = reply.get("seconds_per_char", 0.0)
        if isinstance(budget, (int, float)) and 0 < budget <= self.MAX_SECONDS_PER_CHAR:
            self._seconds_per_char = float(budget)
        self.last_error = ""
        self._worker = worker
        logger.info("TTS %s ready in its isolated extra environment.", self.engine)
        return True

    async def initialize(self) -> None:
        async with self._lock:
            await self._ensure_loaded()

    async def synthesize(self, text: str) -> tuple[bytes, list[dict[str, Any]]]:
        from core.extras import ExtrasError

        async with self._lock:
            if not await self._ensure_loaded():
                return b"", []
            try:
                reply = await self._worker.request(
                    {"op": "synthesize", "text": text}, timeout=self._synth_timeout(text),
                )
            except (ExtrasError, OSError, TimeoutError) as exc:
                logger.error("Extra %s synthesis failed: %s", self.engine, exc)
                self.last_error = (
                    "La síntesis superó el tiempo disponible." if isinstance(exc, TimeoutError)
                    else str(exc) or type(exc).__name__
                )
                return b"", []
        if reply.get("ok") is not True:
            logger.error("Extra %s produced no audio: %s %s", self.engine,
                         reply.get("error"), reply.get("detail", ""))
            self.last_error = str(reply.get("detail") or reply.get("error") or "")[:500]
            return b"", []
        self.last_error = ""
        try:
            audio = base64.b64decode(reply.get("audio", ""), validate=True)
        except ValueError:
            logger.error("Extra %s returned invalid audio.", self.engine)
            return b"", []
        visemes = reply.get("visemes")
        return audio, visemes if isinstance(visemes, list) else []

    async def close(self) -> None:
        async with self._lock:
            worker, self._worker = self._worker, None
            if worker is not None:
                await worker.close()


class UnavailableExtraTTSProvider(TTSProvider):
    """Local engine Iris will not load: its extra is missing, or this PC cannot run it."""

    def __init__(self, engine: str, reason: str = "") -> None:
        self.engine = engine
        self.last_error = reason

    async def initialize(self) -> None:
        if self.last_error:
            logger.error("TTS %s not loaded: %s", self.engine, self.last_error)
            return
        logger.error(
            "TTS %s requires its approved extra. Install or repair it from the "
            "IRIS wizard; the bundled runtime does not contain local engines.",
            self.engine,
        )

    async def synthesize(self, text: str) -> tuple[bytes, list[dict[str, Any]]]:
        return b"", []


# ════════════════════════════════════════════════════════════
# TextToSpeech — unified selector with proper audio playback
# ════════════════════════════════════════════════════════════

class TextToSpeech:
    """TTS selector with cross-platform audio playback via sounddevice."""

    def __init__(self, config: IRISConfig) -> None:
        self.config = config
        self._provider: TTSProvider | None = None
        # FIX T2 — cached fallback engine. Built lazily on first miss so
        # we don't pay the import / event-loop cost for users whose
        # primary engine works fine. Once constructed, reused across
        # every fallback for the lifetime of the agent.
        self._fallback_provider: TTSProvider | None = None
        self._fallback_init_failed: bool = False
        # The Wizard saves voices while this process keeps running; see
        # _follow_saved_voice().
        self._saved_config_stamp = self._config_stamp()
        self._voice_reload_lock = asyncio.Lock()
        self.last_failure = ""    # why the last reply stayed silent, safe to show
        # FIX T4 — track in-flight playback so stop() can cancel it.
        # ``sd.stop()`` aborts the PortAudio stream, but the executor
        # thread that called sd.play() is still blocked in sd.wait().
        # Holding the Future lets us .cancel() it and confirm the
        # thread has unwound before subsequent synthesize/play calls
        # start a new stream.
        self._playback_future: asyncio.Future[None] | None = None

    async def initialize(self) -> None:
        name = self.config.tts_provider

        if name == "edge-tts":
            self._provider = EdgeTTSProvider(self.config.edge_tts_voice)
        elif name == "elevenlabs":
            self._provider = ElevenLabsTTSProvider(
                self.config.elevenlabs_api_key,
                self.config.elevenlabs_voice_id,
            )
        elif name == "minimax":
            self._provider = MiniMaxTTSProvider(
                self.config.minimax_api_key,
                self.config.minimax_voice_id,
                self.config.minimax_tts_model,
            )
        elif name == "fish-audio":
            self._provider = FishAudioTTSProvider(
                self.config.fish_audio_api_key, self.config.fish_audio_voice_id,
                self.config.fish_audio_model,
            )
        elif name == "fish-speech":
            self._provider = FishSpeechTTSProvider(
                api_url=getattr(self.config, "fish_speech_url", "http://127.0.0.1:8080"),
                reference_audio=getattr(self.config, "fish_speech_ref_audio", ""),
                reference_text=getattr(self.config, "fish_speech_ref_text", ""),
                reference_id=getattr(self.config, "fish_speech_reference_id", ""),
                format=getattr(self.config, "fish_speech_format", "wav"),
            )
        elif name in LOCAL_ENGINE_PROVIDERS:
            self._provider = self._local_engine_provider(name)
        else:
            logger.error("Unknown TTS provider %r; speech remains disabled.", name)
            return

        await self._provider.initialize()
        logger.info("TTS ready: %s", name)

    def _local_engine_options(self, name: str) -> dict[str, str]:
        """Constructor options shared by in-process and isolated local engines."""
        c = self.config
        if name == "coqui":
            return {
                "model_name": c.coqui_model,
                "reference_audio": getattr(c, "coqui_ref_audio", ""),
                "language": getattr(c, "coqui_language", "es"),
            }
        if name == "kokoro":
            # The selected Kokoro voice id determines the pipeline language.
            return {
                "voice": getattr(c, "kokoro_voice", "ef_dora"),
                "lang": getattr(c, "language", "es"),
            }
        if name == "neutts":
            # A reference WAV and exact transcript are required; the wizard captures both.
            return {
                "model_repo": getattr(
                    c, "neutts_model_repo", "neuphonic/neutts-nano-spanish-q4-gguf",
                ),
                "codec_repo": getattr(c, "neutts_codec_repo", "neuphonic/neucodec"),
                "ref_audio": getattr(c, "neutts_ref_audio", ""),
                "ref_text": getattr(c, "neutts_ref_text", ""),
                "device": getattr(c, "neutts_device", "cpu"),
            }
        # Reuse only Hugging Face speech-to-speech's Qwen3-TTS handler.
        # Hermes remains the single microphone, STT, LLM and tool owner.
        return {
            "model_name": getattr(c, "hf_s2s_model", "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice"),
            "speaker": getattr(c, "hf_s2s_speaker", "Aiden"),
            "language": getattr(c, "hf_s2s_language", "spanish"),
            "reference_audio": getattr(c, "hf_s2s_ref_audio", ""),
            "reference_text": getattr(c, "hf_s2s_ref_text", ""),
            "device": getattr(c, "hf_s2s_device", "auto"),
        }

    def _local_engine_provider(self, name: str) -> TTSProvider:
        """Prefer the approved profile extra; packaged never imports engines into Core."""
        options = self._local_engine_options(name)
        # A saved or migrated voice this PC cannot run is refused before any load
        # (which could download gigabytes of weights); its reason reaches the UI.
        try:
            from core.voice_requirements import blocked_reason

            reason = blocked_reason(name, options.get("model_name", ""), get_iris_home())
        except Exception as exc:  # the check must never stop Core
            logger.debug("Voice requirements unavailable: %s", exc)
            reason = ""
        if reason:
            return UnavailableExtraTTSProvider(name, reason)
        environment = None
        try:
            from core.extras import resolve_environment

            environment = resolve_environment(name)
        except Exception as exc:  # an unreadable profile must not stop Core
            logger.error("Extra environment for %s is unavailable: %s", name, exc)
        if environment is not None:
            return IsolatedExtraTTSProvider(name, options, environment)
        if get_runtime().packaged:
            return UnavailableExtraTTSProvider(name)
        # Development compatibility: packages installed in the source venv.
        return LOCAL_ENGINE_PROVIDERS[name](**options)

    async def close(self) -> None:
        """Stop isolated extra workers owned by this selector."""
        await self._close_providers(self._provider, self._fallback_provider)

    @staticmethod
    async def _close_each(providers: tuple[TTSProvider | None, ...]) -> None:
        for provider in providers:
            closer = getattr(provider, "close", None)
            if closer is not None:
                try:
                    await closer()
                except Exception as exc:
                    logger.debug("TTS provider close failed: %s", exc)

    @classmethod
    async def _close_providers(cls, *providers: TTSProvider | None) -> None:
        """Every engine closes even if the turn stops meanwhile: a worker left behind holds its model."""
        closing = asyncio.ensure_future(cls._close_each(providers))
        stopped: asyncio.CancelledError | None = None
        while not closing.done():
            try:
                await asyncio.shield(closing)
            except asyncio.CancelledError as exc:
                stopped = exc
        if stopped is not None:
            raise stopped

    @staticmethod
    def _config_stamp() -> tuple[int, int] | None:
        try:
            status = (get_iris_home() / "config.json").stat()
        except Exception as exc:  # an unreadable profile must not stop a reply
            logger.debug("Saved settings unavailable: %s", exc)
            return None
        return status.st_mtime_ns, status.st_size

    @staticmethod
    def _read_saved_config() -> IRISConfig:
        # One read. IRISConfig recovers a damaged field with its default, and the
        # default engine is remote: that must not replace the voice the user chose.
        data = json.loads((get_iris_home() / "config.json").read_text(encoding="utf-8"))
        if not isinstance(data, dict) or "tts_provider" not in data:
            raise ValueError("config.json has no voice")
        saved, recovered = IRISConfig.from_saved(data)
        if any(_is_voice_setting(name) for name in recovered):
            raise ValueError("config.json has damaged voice settings")
        return saved

    async def _follow_saved_voice(self) -> None:
        """Rebuild the engine when a save changed the voice.

        The Wizard only writes config.json and Core keeps running behind it,
        so a voice chosen there would otherwise wait for a restart nobody asked
        for. A saved voice that cannot start stays silent with a notice: it
        never goes back to the voice the user replaced.
        """
        async with self._voice_reload_lock:
            stamp = self._config_stamp()
            if stamp is None or stamp == self._saved_config_stamp:
                return
            try:
                saved = await asyncio.to_thread(self._read_saved_config)
            except (OSError, ValueError):
                # Retried on the next reply, once the file reads again.
                logger.warning("Saved settings are unreadable; keeping the current voice.")
                return
            if self._provider is None or _voice_settings(saved) != _voice_settings(self.config):
                await self._use_saved_voice(saved)
            self._saved_config_stamp = stamp

    async def _use_saved_voice(self, saved: IRISConfig) -> None:
        # The old engine goes first: two local models may not fit in memory at once.
        replaced = (self._provider, self._fallback_provider)
        self._provider = None
        self._fallback_provider = None
        self._fallback_init_failed = False
        await self._close_providers(*replaced)

        previous, self.config = self.config, saved
        try:
            await self.initialize()
        except asyncio.CancelledError:
            # Stopped while loading: the next reply starts this voice again.
            self.config = previous
            unfinished, self._provider = self._provider, None
            await self._close_providers(unfinished)
            raise
        except Exception as exc:
            failed, self._provider = self._provider, None
            logger.error("Saved voice %s could not start: %s", saved.tts_provider, exc)
            await self._close_providers(failed)

    def set_emotion(self, emotion: str) -> None:
        """Propagate emotion to the active provider (Fish Speech uses it for tags)."""
        if isinstance(self._provider, FishSpeechTTSProvider):
            self._provider.set_emotion(emotion)

    @property
    def last_error(self) -> str:
        """Readable reason the primary engine last failed, when it reports one."""
        return str(getattr(self._provider, "last_error", "") or "")

    async def _ensure_fallback(self) -> TTSProvider | None:
        """FIX T2 — lazily build and cache the Edge-TTS fallback engine.

        Edge-TTS is chosen as the optional fallback because it needs no
        API key, has native Spanish voices, and is already IRIS's
        default. If it ALSO fails to import we mark the flag so we
        don't retry every turn — second failure means edge-tts is
        actually missing from the install and no retry will fix it.
        """
        if self._fallback_provider is not None:
            return self._fallback_provider
        if self._fallback_init_failed:
            return None
        try:
            fb = EdgeTTSProvider(self.config.edge_tts_voice)
            await fb.initialize()
            # `initialize()` does not raise on missing edge_tts; it sets
            # `_available = False`. Treat that as a hard failure so we
            # short-circuit future calls.
            if not getattr(fb, "_available", False):
                self._fallback_init_failed = True
                return None
            self._fallback_provider = fb
            return fb
        except Exception as exc:
            logger.error("Fallback Edge-TTS init failed: %s", exc)
            self._fallback_init_failed = True
            return None

    async def speak(
        self,
        text: str,
        emotion: str | None = None,
    ) -> tuple[bytes, list[dict[str, Any]]]:
        """Synthesise text → (audio_bytes, viseme_data).

        FIX T3 — accepts optional ``emotion`` so callers (agent._respond)
        can propagate it to engines that support emotion tags
        (Fish Speech today; future engines can extend `set_emotion`).
        Backwards-compatible: ``speak(text)`` still works.

        A failed engine remains failed by default. Edge-TTS is tried only
        when ``tts_allow_remote_fallback`` was explicitly enabled, because
        changing from a local/provider-specific engine to a remote service
        otherwise breaks the user's privacy and provider choice.
        """
        # Preserve the original reply in chat/subtitles; only the provider sees
        # this spoken projection. The optional fallback reuses the same text.
        self.last_failure = ""
        text = text_for_speech(text)
        if not text:
            return b"", []    # nothing to say: silence is the right answer here

        await self._follow_saved_voice()

        if not self._provider:
            logger.error("TTS not initialised.")
            return self._no_voice()

        # Propagate emotion before synth so the engine picks up the tag.
        if emotion is not None:
            self.set_emotion(emotion)

        try:
            audio, visemes = await self._provider.synthesize(text)
        except Exception as exc:
            logger.error("Primary TTS raised: %s", exc)
            audio, visemes = b"", []

        if audio:
            return audio, visemes

        # Primary returned empty. If the primary IS edge-tts, no
        # fallback is possible — there's nothing freer/more-available
        # to fall back to.
        if isinstance(self._provider, EdgeTTSProvider):
            logger.warning(
                "Edge-TTS primary returned empty audio; no fallback available."
            )
            return self._no_voice()

        if not getattr(self.config, "tts_allow_remote_fallback", False):
            logger.error(
                "Primary TTS %s failed; remote Edge-TTS fallback is disabled.",
                type(self._provider).__name__,
            )
            return self._no_voice()

        primary_name = type(self._provider).__name__
        logger.warning(
            "Primary engine %s failed (empty audio), falling back to edge-tts.",
            primary_name,
        )

        fb = await self._ensure_fallback()
        if fb is None:
            logger.error("Edge-TTS fallback unavailable. Synthesis failed entirely.")
            return self._no_voice()

        try:
            fb_audio, fb_visemes = await fb.synthesize(text)
        except Exception as exc:
            logger.error("Fallback Edge-TTS also raised: %s", exc)
            return self._no_voice()

        if not fb_audio:
            logger.error("Fallback Edge-TTS also returned empty audio.")
            return self._no_voice()

        return fb_audio, fb_visemes

    def _no_voice(self) -> tuple[bytes, list]:
        """There was something to say and no engine said it. Only a provider's own message is shown."""
        self.last_failure = getattr(self._provider, "last_error", "") or ""
        return b"FALLBACK_FAILED", []

    async def stop(self) -> None:
        """Halt any audio currently playing through the speakers (Fase 3).

        Used by ``IRISAgent.interrupt()`` when the user starts
        talking mid-TTS (barge-in). ``sd.stop()`` causes the blocking
        ``sd.wait()`` inside ``play_audio`` to return immediately, so
        the executor thread unwinds and the awaiting coroutine
        resolves cleanly. Safe to call when nothing is playing — it's
        a no-op in that case.

        FIX T4 — also actively cancel the in-flight playback Future and
        wait briefly for the executor thread to drain. Without this, a
        new synthesize() can race with the still-unwinding executor
        thread (sd.play queued buffers + sd.stop) and produce a fraction
        of the previous utterance bleeding into the new one.
        """
        try:
            sd.stop()
        except Exception as exc:  # PortAudio can throw odd errors on race
            logger.debug("sd.stop() raised: %s", exc)

        # Drain the playback future if there's one in flight. We give
        # it a short grace window — sd.stop() should make sd.wait()
        # return within ~1 frame (~20 ms typically). If the executor
        # is still blocked after that we move on; the thread will
        # eventually unwind and a fresh sd.play() call will pre-empt
        # any residual buffer anyway.
        fut = self._playback_future
        self._playback_future = None
        if fut is not None and not fut.done():
            try:
                await asyncio.wait_for(asyncio.shield(fut), timeout=0.25)
            except asyncio.TimeoutError:
                logger.debug("Playback executor did not drain within 250 ms.")
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.debug("Playback future await raised: %s", exc)

    async def play_audio(self, audio_bytes: bytes) -> None:
        """
        Decode any audio format and play through speakers via sounddevice.

        No pop-up windows, no external media players.
        Works on Windows, Linux, and macOS.
        """
        if not audio_bytes:
            return
        # FIX T2 — sentinel returned by speak() when both primary and
        # fallback engines failed. Treat as "nothing to play".
        if audio_bytes == b"FALLBACK_FAILED":
            logger.warning("play_audio: FALLBACK_FAILED sentinel — skipping playback.")
            return

        def _play() -> None:
            try:
                samples, sr = decode_audio(audio_bytes)

                # Convert int16 → float32 normalised  (sounddevice wants float)
                float_audio = samples.astype(np.float32) / 32768.0

                sd.play(float_audio, samplerate=sr)
                sd.wait()   # block until playback finishes

            except RuntimeError as e:
                logger.error("Audio decode error: %s", e)
            except sd.PortAudioError as e:
                logger.error("Speaker error: %s  — are speakers connected?", e)
            except Exception as e:
                logger.error("Playback error: %s", e)

        # FIX T4 — wrap the executor call in a tracked Future so stop()
        # can wait for the thread to drain instead of leaving it
        # dangling when a barge-in lands.
        loop = asyncio.get_event_loop()
        fut = loop.run_in_executor(None, _play)
        self._playback_future = asyncio.ensure_future(fut)
        try:
            await self._playback_future
        finally:
            # Clear only if we are still the active playback; stop()
            # may have already replaced/cleared it.
            if self._playback_future is fut or self._playback_future is None:
                self._playback_future = None

