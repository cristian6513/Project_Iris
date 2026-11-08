"""Local TTS engines that may run in a profile extra environment.

Kokoro, Coqui, NeuTTS and Qwen3-TTS depend on large optional packages. This
module imports only the standard library and NumPy so Core can use it in
development and ``core/extras_worker.py`` can load it by file path inside an
isolated interpreter whose ``sys.path`` contains only one approved extra.
Never import ``core.*`` here: that would break the isolation boundary.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import logging
import os
import tempfile
import wave
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger("IRIS.tts")


# ════════════════════════════════════════════════════════════
# TTS Provider ABC
# ════════════════════════════════════════════════════════════

class TTSProvider(ABC):
    @abstractmethod
    async def initialize(self) -> None: ...

    @abstractmethod
    async def synthesize(self, text: str) -> tuple[bytes, list[dict[str, Any]]]:
        """Return (audio_bytes, viseme_data)."""
        ...


# ── Coqui TTS ────────────────────────────────────────────

class CoquiTTSProvider(TTSProvider):
    """Coqui TTS — open-source, 100 % local.

    Single-speaker models work without a reference. Voice cloning is only
    enabled when a reference is configured; in that case the wizard switches
    to XTTS v2 and this provider passes ``speaker_wav`` + ``language`` exactly
    as the maintained Coqui API requires.
    """

    def __init__(
        self,
        model_name: str = "tts_models/es/css10/vits",
        reference_audio: str = "",
        language: str = "es",
    ) -> None:
        self.model_name = model_name
        self.reference_audio = reference_audio
        self.language = language
        self._tts: Any = None

    async def initialize(self) -> None:
        def _load() -> Any:
            try:
                from TTS.api import TTS
                tts = TTS(model_name=self.model_name, progress_bar=False)
                logger.info("Coqui TTS loaded: %s", self.model_name)
                return tts
            except ImportError:
                logger.error("Coqui not installed.  Run:  pip install 'IRIS-robot[coqui]'")
                return None
            except Exception as e:
                logger.error("Coqui load failed: %s", e)
                return None

        self._tts = await asyncio.get_event_loop().run_in_executor(None, _load)

    async def synthesize(self, text: str) -> tuple[bytes, list[dict[str, Any]]]:
        if self._tts is None:
            return b"", []

        def _speak() -> bytes:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                path = f.name
            try:
                kwargs: dict[str, Any] = {"text": text, "file_path": path}
                if self.reference_audio:
                    ref_path = Path(self.reference_audio)
                    if not ref_path.is_file():
                        logger.error("Coqui XTTS reference not found: %s", ref_path)
                        return b""
                    kwargs.update({
                        "speaker_wav": str(ref_path),
                        "language": self.language or "es",
                    })
                self._tts.tts_to_file(**kwargs)
                return Path(path).read_bytes()
            finally:
                Path(path).unlink(missing_ok=True)

        audio = await asyncio.get_event_loop().run_in_executor(None, _speak)
        logger.info("Coqui TTS: %d bytes.", len(audio))
        return audio, []       # Coqui doesn't provide word boundaries


# ── Kokoro (local) ───────────────────────────────────────
#
# Kokoro-82M is a compact, Apache-2.0 text-to-speech model that runs
# locally through its official Python inference library.
#
# Why we ship it even though we already have Coqui:
#   - Coqui requires `torch` and downloads the selected model on first use.
#   - Kokoro has 82 M parameters and predefined voices for multiple
#     languages. Non-English phonemisation can require system eSpeak-NG.
#
# Voice IDs follow Kokoro's own scheme:
#   af_heart       — American English female
#   af_bella       — American English female (softer)
#   am_adam        — American male
#   ef_dora        — Spanish (eu) female
#   em_santa       — Spanish (eu) male
#   jf_alpha       — Japanese female
#   (full list: https://huggingface.co/hexgrad/Kokoro-82M)
#
# The user picks one in the wizard's TTS step. We store the voice as
# `config.kokoro_voice`.

_KOKORO_LEGACY_VOICE_IDS = {
    "ef_alice": "af_heart",
    "ef_bella": "af_bella",
    "ef_sarah": "af_sarah",
}


def use_relative_espeak_data() -> bool:
    """Worker-only: let eSpeak-NG reach its bundled data from a non-ASCII profile.

    On Windows eSpeak-NG opens its data with narrow paths, so with a profile
    such as ``C:\\Users\\José`` it silently falls back to a non-existent build
    path and lists no voices. Running from the ``espeakng_loader`` directory
    with ``ESPEAK_DATA_PATH='.'`` lets Windows resolve the relative path with
    Unicode APIs. This changes the process directory, so only the isolated
    extras worker may call it, never Core.
    """
    if os.name != "nt":
        return False
    try:
        import espeakng_loader  # type: ignore
        from misaki import espeak  # type: ignore # noqa: F401 - sets an absolute path on import
        from phonemizer.backend.espeak.wrapper import EspeakWrapper  # type: ignore
    except ImportError:
        return False
    data = Path(espeakng_loader.get_data_path())
    if str(data).isascii():
        return False
    os.chdir(data.parent)
    os.environ["ESPEAK_DATA_PATH"] = "."
    os.environ.pop("PHONEMIZER_ESPEAK_DATA_PATH", None)
    EspeakWrapper.set_data_path(None)
    logger.info("eSpeak-NG uses its bundled data through a relative path.")
    return True


def _kokoro_lang_code(voice: str, language_hint: str = "es") -> str:
    """Derive Kokoro's pipeline language from its own voice id."""
    prefix = (voice or "").split("_", 1)[0][:1].lower()
    if prefix in {"a", "b", "e", "f", "h", "i", "j", "p", "z"}:
        return prefix
    return {
        "en": "a", "en-us": "a", "en-gb": "b",
        "es": "e", "es-es": "e", "es-mx": "e",
        "ja": "j", "jp": "j", "zh": "z", "zh-cn": "z",
        "fr": "f", "hi": "h", "pt": "p", "it": "i",
    }.get((language_hint or "es").lower(), "e")


class KokoroTTSProvider(TTSProvider):
    """Kokoro-82M TTS — small, local, Apache-2.0.

    Runs entirely on-device via the official `kokoro` Python package.
    No API key or server is required.

    We keep the model in-process as `self._pipeline` because Kokoro's
    first call is slow (graph compilation); warming it in
    `initialize()` makes the first user sentence respond on par with
    subsequent ones.
    """

    def __init__(self, voice: str = "ef_dora", lang: str = "es") -> None:
        # Migrate ids written by older wizard builds, then derive KPipeline's
        # language from the selected voice instead of the app UI language.
        self.voice = _KOKORO_LEGACY_VOICE_IDS.get(voice, voice)
        self.lang = lang
        self._pipeline: Any = None
        self._sample_rate = 24000  # Kokoro's native SR

    async def initialize(self) -> None:
        def _load() -> Any:
            try:
                # The `kokoro` package (pip install kokoro>=0.9) exposes
                # KPipeline, which handles tokenisation and inference.
                from kokoro import KPipeline  # type: ignore

                # lang_code: 'a' = American English, 'e' = Spanish (EU),
                #            'b' = British, 'j' = Japanese, 'z' = Mandarin.
                lang_code = _kokoro_lang_code(self.voice, self.lang)

                pipe = KPipeline(lang_code=lang_code)
                logger.info(
                    "Kokoro-82M ready — voice: %s, lang_code: %s",
                    self.voice, lang_code,
                )
                return pipe
            except ImportError:
                logger.error(
                    "kokoro not installed.  Run:  pip install kokoro soundfile"
                )
                return None
            except Exception as e:
                logger.error("Kokoro load failed: %s", e)
                return None

        self._pipeline = await asyncio.get_event_loop().run_in_executor(None, _load)

    async def synthesize(self, text: str) -> tuple[bytes, list[dict[str, Any]]]:
        if self._pipeline is None:
            logger.error("Kokoro: pipeline not initialised.")
            return b"", []

        def _speak() -> bytes:
            import io
            import wave

            # KPipeline yields (graphemes, phonemes, audio) tuples —
            # one per sentence chunk. We concatenate all chunks into a
            # single waveform. Audio comes back as a 1-D float32 numpy
            # array at self._sample_rate.
            audio_chunks: list[np.ndarray] = []
            try:
                for _gra, _pho, audio in self._pipeline(
                    text, voice=self.voice, speed=1.0,
                ):
                    if audio is None:
                        continue
                    # Kokoro can return either torch tensors or numpy
                    # arrays depending on version — normalise to numpy.
                    if hasattr(audio, "detach"):
                        audio = audio.detach().cpu().numpy()
                    audio_chunks.append(np.asarray(audio, dtype=np.float32))
            except Exception as e:
                logger.error("Kokoro inference failed: %s", e)
                return b""

            if not audio_chunks:
                return b""

            waveform = np.concatenate(audio_chunks)
            # Convert float32 [-1..1] → int16 PCM wrapped in a WAV
            # container so play_audio()'s decoder path handles it like
            # any other sample.
            pcm16 = np.clip(waveform * 32767.0, -32768, 32767).astype(np.int16)
            buf = io.BytesIO()
            with wave.open(buf, "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)  # int16
                wav.setframerate(self._sample_rate)
                wav.writeframes(pcm16.tobytes())
            return buf.getvalue()

        audio_bytes = await asyncio.get_event_loop().run_in_executor(None, _speak)
        logger.info("Kokoro TTS: %d bytes.", len(audio_bytes))
        # Kokoro doesn't expose word boundaries — viseme data is empty.
        # The lipsync pipeline falls back to amplitude-based mouth
        # opening when viseme_data is [], which is fine for this engine.
        return audio_bytes, []


# ── NeuTTS (Neuphonic) ───────────────────────────────────

class NeuTTSProvider(TTSProvider):
    """NeuTTS — on-device voice cloning with 3-second reference audio.

    Wraps `neuphonic/neutts` (`pip install neutts[all]`). The pipeline encodes a one-time
    reference clip into speaker codes and reuses them across every
    subsequent synthesise call, so cloning happens *once* in
    `initialize()` rather than per request.

    What we get vs. our other local TTS engines:
      • Voice cloning from a short reference; the exact language support
        depends on the selected NeuTTS model.
      • A GGUF backbone that can run on CPU, with performance depending on
        model size, quantisation and hardware.
      • Apache-2.0 (Air variant) or NeuTTS Open License 1.0 (Nano).

    Watermark: every output carries Perth Perceptual Threshold
    watermarking by default. We don't disable it — it's part of the
    model's terms and a sensible safeguard for AI voice abuse.

    Reference audio:
      • Required at config-time. Without `ref_audio` + `ref_text` the
        provider reports that it is unavailable. It never changes to a
        remote provider without the user's explicit fallback setting.
      • Best results: 3-15 s, clean, single-speaker, mono, 16-44 kHz.
    """

    def __init__(
        self,
        model_repo: str = "neuphonic/neutts-nano-spanish-q4-gguf",
        codec_repo: str = "neuphonic/neucodec",
        ref_audio: str  = "",
        ref_text: str   = "",
        device: str     = "cpu",
    ) -> None:
        self.model_repo = model_repo
        self.codec_repo = codec_repo
        self.ref_audio  = ref_audio
        self.ref_text   = ref_text
        # PyTorch names Apple Silicon ``mps`` and exposes ROCm through the
        # ``cuda`` device API. Keep accepting legacy wizard labels while
        # passing valid device strings to NeuTTS.
        self.device     = {"metal": "mps", "rocm": "cuda"}.get(device, device)
        self._tts: Any  = None
        self._ref_codes: Any = None
        self._sample_rate = 24000   # NeuTTS native SR

    async def initialize(self) -> None:
        # Resolve at init time so any later config edits require
        # an IRIS restart (which is fine — NeuTTS keeps the encoded
        # codes in memory anyway).
        ref_audio, ref_text = self._resolve_reference()
        if not ref_audio or not ref_text:
            logger.error(
                "NeuTTS: no reference audio + text configured. "
                "Record or upload an authorised 3-15 second sample and "
                "its exact transcript in the IRIS wizard."
            )
            return

        def _load() -> tuple[Any, Any] | None:
            try:
                from neutts import NeuTTS  # type: ignore
            except ImportError:
                logger.error(
                    "neutts not installed. Run: pip install 'neutts[all]'"
                )
                return None

            try:
                tts = NeuTTS(
                    backbone_repo=self.model_repo,
                    backbone_device=self.device,
                    codec_repo=self.codec_repo,
                    codec_device=self.device,
                )
                # Encode the reference exactly ONCE — these codes are a
                # speaker fingerprint, not a per-utterance computation.
                # Subsequent infer() calls reuse them at zero extra cost.
                ref_codes = tts.encode_reference(ref_audio)
                logger.info(
                    "NeuTTS ready — model: %s, ref: %s (%s)",
                    self.model_repo, ref_audio, self.device,
                )
                return tts, ref_codes
            except Exception as e:
                logger.error("NeuTTS init failed: %s", e)
                return None

        result = await asyncio.get_event_loop().run_in_executor(None, _load)
        if result is None:
            return
        self._tts, self._ref_codes = result

    def _resolve_reference(self) -> tuple[str, str]:
        """Return an explicit valid user reference or ``('', '')``."""
        if self.ref_audio and self.ref_text:
            p = Path(self.ref_audio)
            if p.exists():
                return str(p), self.ref_text
            logger.warning(
                "NeuTTS: configured ref_audio %r does not exist on disk.",
                self.ref_audio,
            )

        return "", ""

    async def synthesize(self, text: str) -> tuple[bytes, list[dict[str, Any]]]:
        if self._tts is None or self._ref_codes is None:
            logger.warning("NeuTTS: unavailable or missing a usable reference.")
            return b"", []

        def _speak() -> bytes:
            import io
            import wave
            try:
                wav = self._tts.infer(text, self._ref_codes, self.ref_text)
                # NeuTTS returns either a numpy array or a torch tensor —
                # normalise to numpy float32, then PCM16 wrapped in WAV
                # so play_audio() handles it uniformly with our other engines.
                if hasattr(wav, "detach"):
                    wav = wav.detach().cpu().numpy()
                wav = np.asarray(wav, dtype=np.float32)
                if wav.ndim > 1:
                    # Take first channel if stereo (rare; the model is mono).
                    wav = wav[0] if wav.shape[0] < wav.shape[1] else wav[:, 0]
                pcm16 = np.clip(wav * 32767.0, -32768, 32767).astype(np.int16)
                buf = io.BytesIO()
                with wave.open(buf, "wb") as w:
                    w.setnchannels(1)
                    w.setsampwidth(2)
                    w.setframerate(self._sample_rate)
                    w.writeframes(pcm16.tobytes())
                return buf.getvalue()
            except Exception as e:
                logger.error("NeuTTS inference failed: %s", e)
                return b""

        audio = await asyncio.get_event_loop().run_in_executor(None, _speak)
        logger.info("NeuTTS: %d bytes.", len(audio))
        # No native viseme output — the lipsync pipeline falls back to
        # amplitude-based mouth opening which is good enough for clean
        # studio voices.
        return audio, []


# ── Hugging Face speech-to-speech / Qwen3-TTS ──────────

HF_S2S_QWEN_MODELS = {
    "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",
    "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice",
    "Qwen/Qwen3-TTS-12Hz-0.6B-Base",
    "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
}

HF_S2S_QWEN_SPEAKERS = {
    "Vivian", "Serena", "Uncle_Fu", "Dylan", "Eric",
    "Ryan", "Aiden", "Ono_Anna", "Sohee",
}


def _load_hf_s2s_qwen_runtime() -> tuple[type[Any], type[Any]]:
    """Load the reviewed upstream handler behind a narrow compatibility seam.

    ``speech-to-speech`` is still alpha, so keeping the imports here makes a
    future upstream layout change fail as a controlled optional-engine error
    instead of breaking IRIS at module import time.
    """
    from speech_to_speech.TTS.qwen3_tts_handler import Qwen3TTSHandler
    from speech_to_speech.pipeline.messages import TTSInput

    return Qwen3TTSHandler, TTSInput


def _patch_qwen_tts_rope() -> None:
    """Let qwen-tts-hf 0.1.1.post1 load under the locked Transformers 5.x.

    Its compatibility initializer reads ``config.rope_theta``; Transformers 5
    moved that value into ``config.rope_parameters`` (MimiConfig, the speech
    tokenizer), so every Qwen3-TTS load failed with an AttributeError. The
    wrapper copies the value across and is a no-op once upstream reads it.
    """
    import qwen_tts._transformers_compat as compat
    from transformers.modeling_rope_utils import ROPE_INIT_FUNCTIONS

    original = compat._default_rope_parameters
    if getattr(original, "_iris_rope_parameters", False):
        return

    def default_rope_parameters(config: Any, device: Any = None, seq_len: Any = None,
                                layer_type: Any = None) -> Any:
        if getattr(config, "rope_theta", None) is None:
            theta = (getattr(config, "rope_parameters", None) or {}).get("rope_theta")
            if theta is not None:
                object.__setattr__(config, "rope_theta", theta)
        return original(config, device, seq_len, layer_type)

    default_rope_parameters._iris_rope_parameters = True  # type: ignore[attr-defined]
    compat._default_rope_parameters = default_rope_parameters
    compat.patch_transformers_rope_registry()
    ROPE_INIT_FUNCTIONS["default"] = default_rope_parameters


def _load_qwen_tts_model(model_name: str) -> Any:
    """Load a Qwen3-TTS checkpoint through ``qwen_tts`` on CPU (float32)."""
    import torch

    _patch_qwen_tts_rope()
    from qwen_tts import Qwen3TTSModel

    return Qwen3TTSModel.from_pretrained(model_name, device_map="cpu", dtype=torch.float32)


def _speech_chunks(text: str, limit: int = 220) -> list[str]:
    """Sentences grouped up to ``limit`` characters: shorter generations stay bounded."""
    import re

    pieces = [piece.strip() for piece in re.split(r"(?<=[.!?¿¡;:…])\s+|\n+", text) if piece.strip()]
    chunks: list[str] = []
    for piece in pieces:
        while len(piece) > limit:
            cut = piece.rfind(" ", 0, limit)
            cut = cut if cut > limit // 2 else limit
            chunks.append(piece[:cut].strip())
            piece = piece[cut:].strip()
        if chunks and len(chunks[-1]) + 1 + len(piece) <= limit:
            chunks[-1] = f"{chunks[-1]} {piece}"
        elif piece:
            chunks.append(piece)
    return chunks


class _QwenTTSDirect:
    """Qwen3-TTS through ``qwen_tts`` itself, for CPU-only Windows.

    Upstream speech-to-speech uses faster-qwen3-tts there, whose torch backend
    requires CUDA graphs; the approved lock's torch is the CPU wheel and the
    qwentts.cpp (GGML) backend publishes no Windows wheels, so that handler
    can never load. This keeps the handler's ``process`` contract: int16 PCM
    chunks, one per sentence group, at ``sample_rate``.
    """

    def __init__(self, model: Any, *, speaker: str, language: str, clone_prompt: Any = None) -> None:
        self.model = model
        self.speaker = speaker
        self.language = language
        self.clone_prompt = clone_prompt
        self.sample_rate = 24_000

    def process(self, message: Any) -> Any:
        for chunk in _speech_chunks(message.text):
            if self.clone_prompt is not None:
                wavs, rate = self.model.generate_voice_clone(
                    text=chunk, language=self.language, voice_clone_prompt=self.clone_prompt,
                    non_streaming_mode=True,
                )
            else:
                wavs, rate = self.model.generate_custom_voice(
                    text=chunk, speaker=self.speaker, language=self.language, non_streaming_mode=True,
                )
            self.sample_rate = int(rate)
            audio = np.clip(np.asarray(wavs[0], dtype=np.float32).reshape(-1), -1.0, 1.0)
            yield (audio * 32767.0).astype(np.int16)


class _DirectInput:
    """Message shape shared with speech-to-speech's ``TTSInput``."""

    def __init__(self, text: str, language_code: str = "es") -> None:
        self.text = text
        self.language_code = language_code


def _hf_s2s_device(requested: str) -> tuple[str, str, str]:
    """Resolve upstream device/backend/quantization conservatively."""
    import platform

    value = (requested or "auto").strip().lower()
    system = platform.system().lower()
    if value == "metal":
        value = "mps"
    elif value == "rocm":
        # PyTorch exposes ROCm through its CUDA-compatible API.
        value = "cuda"
    elif value == "auto":
        if system == "darwin" and platform.machine().lower() in {"arm64", "aarch64"}:
            value = "mps"
        else:
            try:
                import torch

                value = "cuda" if bool(torch.cuda.is_available()) else "cpu"
            except (ImportError, OSError, RuntimeError):
                value = "cpu"

    # The reviewed Windows package installs the faster PyTorch backend without
    # the optional qwentts.cpp wheel. Linux CPU/CUDA can use the documented
    # GGML path; Apple Silicon is remapped to MLX internally by upstream.
    backend = "torch" if system == "windows" else "ggml"
    quantization = "Q4_K_M" if value == "cpu" and backend == "ggml" else "BF16"
    return value, backend, quantization


class HFSpeechToSpeechQwenProvider(TTSProvider):
    """Qwen3-TTS through Hugging Face's modular speech-to-speech project.

    IRIS deliberately embeds only the upstream TTS handler. Hermes remains the
    one LLM/tool/session runtime, and Hermes' native wake/STT path remains the
    one microphone owner. This avoids a second competing voice-agent loop.

    On Windows without CUDA (always, with the approved CPU torch wheel) the
    upstream handler cannot load, so the same checkpoint runs through
    ``qwen_tts`` directly (``_QwenTTSDirect``). It is slow: about 12x real time
    for 0.6B and 17x for 1.7B on a Ryzen 5 5600G, so it declares a per-character
    synthesis budget that the isolated wrapper uses as its timeout. The budget
    keeps a 2x margin: the first sentence after a fresh 4 GB download measured
    more than twice as slow as later ones.
    """

    # Seconds of synthesis allowed per character; 0 keeps the wrapper default.
    seconds_per_char = 0.0

    def __init__(
        self,
        model_name: str = "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",
        speaker: str = "Aiden",
        language: str = "spanish",
        reference_audio: str = "",
        reference_text: str = "",
        device: str = "auto",
    ) -> None:
        self.model_name = model_name
        self.speaker = speaker
        self.language = language
        self.reference_audio = reference_audio
        self.reference_text = reference_text
        self.requested_device = device
        self._handler: Any = None
        self._input_type: type[Any] | None = None

    async def initialize(self) -> None:
        def _load() -> tuple[Any, type[Any]] | None:
            if self.model_name not in HF_S2S_QWEN_MODELS:
                logger.error("HF speech-to-speech: unsupported Qwen3-TTS model %r", self.model_name)
                return None
            if "Base" in self.model_name:
                ref = Path(self.reference_audio).expanduser() if self.reference_audio else None
                if not ref or not ref.is_file() or not self.reference_text.strip():
                    logger.error(
                        "Qwen3-TTS Base necesita la muestra clonada (WAV) y su transcripción exacta; "
                        "vuelve a clonar la voz."
                    )
                    return None
            elif self.speaker not in HF_S2S_QWEN_SPEAKERS:
                logger.error("HF speech-to-speech: unsupported Qwen3-TTS speaker %r", self.speaker)
                return None

            try:
                from queue import Queue
                from threading import Event

                device, backend, quantization = _hf_s2s_device(self.requested_device)
                if device == "cpu" and backend == "torch":
                    model = _load_qwen_tts_model(self.model_name)
                    prompt = None
                    if "Base" in self.model_name:
                        # Encode the reference once; every sentence reuses it.
                        prompt = model.create_voice_clone_prompt(
                            ref_audio=str(Path(self.reference_audio).expanduser()),
                            ref_text=self.reference_text,
                        )
                    self.seconds_per_char = 3.0 if "1.7B" in self.model_name else 2.0
                    logger.info(
                        "Qwen3-TTS ready on CPU through qwen_tts — model=%s (slow without CUDA)",
                        self.model_name,
                    )
                    return _QwenTTSDirect(
                        model, speaker=self.speaker, language=self.language, clone_prompt=prompt,
                    ), _DirectInput

                handler_type, input_type = _load_hf_s2s_qwen_runtime()
                with contextlib.suppress(ImportError):
                    # faster-qwen3-tts loads qwen_tts too; MLX on macOS does not.
                    _patch_qwen_tts_rope()
                dtype = "float32" if device == "cpu" and backend == "torch" else "auto"
                should_listen = Event()
                should_listen.set()
                handler = handler_type(
                    Event(),
                    Queue(),
                    Queue(),
                    setup_kwargs={
                        "should_listen": should_listen,
                        "model_name": self.model_name,
                        "device": device,
                        "dtype": dtype,
                        "backend": backend,
                        "ggml_quantization": quantization,
                        "speaker": self.speaker,
                        "language": self.language,
                        "ref_audio": self.reference_audio or None,
                        "ref_text": self.reference_text,
                        "non_streaming_mode": True,
                    },
                )
                logger.info(
                    "HF speech-to-speech Qwen3-TTS ready — model=%s device=%s backend=%s",
                    self.model_name,
                    device,
                    backend,
                )
                return handler, input_type
            except ImportError as exc:
                logger.error(
                    "Falta un componente de Qwen3-TTS (%s). Repara HF Qwen3-TTS desde el asistente.", exc,
                )
                return None
            except Exception as exc:
                logger.error("Qwen3-TTS no pudo cargar %s: %s: %s", self.model_name, type(exc).__name__, exc)
                return None

        result = await asyncio.get_event_loop().run_in_executor(None, _load)
        if result is not None:
            self._handler, self._input_type = result

    async def synthesize(self, text: str) -> tuple[bytes, list[dict[str, Any]]]:
        if self._handler is None or self._input_type is None:
            return b"", []

        def _speak() -> bytes:
            try:
                message = self._input_type(text=text, language_code="es")
                pcm_chunks: list[np.ndarray] = []
                for chunk in self._handler.process(message):
                    if isinstance(chunk, bytes):
                        if chunk:
                            pcm_chunks.append(np.frombuffer(chunk, dtype=np.int16))
                    elif hasattr(chunk, "tobytes"):
                        pcm_chunks.append(np.asarray(chunk, dtype=np.int16).reshape(-1))
                if not pcm_chunks:
                    return b""
                pcm = np.concatenate(pcm_chunks).astype(np.int16, copy=False)
                output = io.BytesIO()
                with wave.open(output, "wb") as wav:
                    wav.setnchannels(1)
                    wav.setsampwidth(2)
                    # The upstream handler streams 16 kHz; qwen_tts reports its own rate.
                    wav.setframerate(int(getattr(self._handler, "sample_rate", 16_000)))
                    wav.writeframes(pcm.tobytes())
                return output.getvalue()
            except Exception as exc:
                logger.error("Qwen3-TTS no pudo generar el audio: %s: %s", type(exc).__name__, exc)
                return b""

        audio = await asyncio.get_event_loop().run_in_executor(None, _speak)
        return audio, []
