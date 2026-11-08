"""
IRIS Agent — Main Orchestrator

Coordinates IRIS turns:
    Hermes native wake/voice or typed chat → Think (Hermes)
    → Speak (TTS) → Animate (WebSocket → Face)

This is the main entry point for running IRIS.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import sys
from pathlib import Path
from collections.abc import Callable
from typing import Any

import uvicorn
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from core.animations import normalize_agent_animation
from core.config import IRISConfig
from core.fallback import FallbackEngine
from core.hermes_bridge import HermesBridge
from core.hermes_wake import HermesWakeRuntime, read_hermes_wake_settings
from core.memory import IRISMemory
from core.iris_skill_lifecycle import sync_app_builder_to_hermes
from core.request_security import is_loopback_url
from core.runtime_descriptor import get_runtime
from core.personality import PersonalityManager
from core.workspace_sync import sync_personality_to_workspace
from core.tts import TextToSpeech
from core.version import __version__ as IRIS_VERSION
from core.websocket_server import (
    app,
    audio_clients_connected,
    broadcast_to_face,
    current_turn,
    handle_hermes_wake_event,
    mount_static_files,
    send_animation,
    send_emotion,
    send_lip_sync,
    send_speech_end,
    send_speech_start,
    send_status,
    send_subtitle,
    send_tts_audio,
)

logger = logging.getLogger("IRIS")

# Force UTF-8 before Rich wraps the streams; older Windows consoles may
# otherwise crash when status panels contain non-ASCII characters.
if sys.platform == "win32":
    for _stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, OSError, ValueError):
            _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

console = Console()


class IRISAgent:
    """Main orchestrator for IRIS OS."""

    def __init__(self, config: IRISConfig | None = None) -> None:
        get_runtime()  # A supplied invalid descriptor fails before profile work.
        self.config = config or IRISConfig.load()
        self.personality = PersonalityManager(self.config)
        self.personality.load()
        # Keep Hermes workspace identity aligned with the selected IRIS
        # personality. Sync failures should not block boot.
        try:
            sync_personality_to_workspace(self.personality, self.config)
        except Exception as exc:  # pragma: no cover — defensive only
            logger.warning("Hermes workspace sync failed: %s", exc)
        self.memory = IRISMemory(self.config)
        self.bridge = HermesBridge(self.config, personality=self.personality)
        # F01: the bridge reports every turn's model outcome, cancelled ones too.
        self.bridge.on_model_turn = self._publish_model_turn
        self.tts = TextToSpeech(self.config)
        self.fallback = FallbackEngine()
        self._running = False
        self._session_id = "default"
        self._continuity_active = False
        self.app_builder_ready = False
        # Serialises ALL TTS playback so voice, subtitles, animation,
        # and lip-sync stay ordered even when websocket turns overlap.
        self._speak_lock = asyncio.Lock()
        # Fase 3 — interrupt-while-speaking. We track the asyncio task
        # currently driving an end-to-end turn (think → respond) so a
        # barge-in from the face can cancel it. Set by both the voice
        # loop in `run()` and by `_handle_user_text` in
        # websocket_server. ``None`` between turns; never holds more
        # than one task because turns are serialised through
        # _speak_lock.
        self._current_turn_task: asyncio.Task[Any] | None = None
        # Serialises writes/reads to ``_current_turn_task``. Both the
        # voice loop (run()) and the chat handler in websocket_server
        # mutate this pointer, and ``interrupt()`` reads + cancels it
        # from yet another task. Without the lock there was a tiny
        # window where two handlers could overwrite the field
        # concurrently (or interrupt could cancel a stale reference
        # whose owner had just cleared it).
        self._turn_lock = asyncio.Lock()
        # End-to-end queue for think → persistence → speech. The pointer lock
        # above only protects cancellation metadata; it does not serialize two
        # simultaneous WebSocket/wake turns by itself.
        self._conversation_lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Initialize all subsystems."""
        p = self.personality.personality
        console.print(Panel(
            Text.assemble(
                (f"{p.emoji}  {p.name} Robot v{IRIS_VERSION}\n", "bold gold1"),
                (f"   {p.backstory.replace('{name}', p.name)[:60]}...\n\n", "italic cyan"),
                ("   Initializing subsystems...", "dim"),
            ),
            border_style="gold1",
            title=f"[bold white]Project {p.name}[/]",
            subtitle=f"[dim]{p.emoji} {self.config.personality_template}[/]",
        ))
        console.print(f"  ✅ Personality: {p.name} {p.emoji}", style="green")

        # Initialize all modules
        await self.memory.initialize()
        console.print("  ✅ Memory system ready", style="green")

        if is_loopback_url(self.config.hermes_url):
            try:
                builder = await asyncio.to_thread(sync_app_builder_to_hermes)
                self.app_builder_ready = builder["ok"]
                if builder["status"] == "conflict":
                    logger.warning("%s", builder["detail"])
            except (OSError, ValueError) as exc:
                logger.warning("No se pudo sincronizar la skill de creación de apps: %s", exc)

        await self.bridge.initialize()
        if self.bridge.is_available:
            console.print("  ✅ Hermes connected", style="green")
        else:
            console.print("  ⚠️  Hermes offline — fallback mode", style="yellow")

        # Continuity Center stores only the selected Hermes session pointer,
        # never a parallel transcript. Restore it after both stores are ready
        # and only when Hermes confirms the session still exists.
        saved_session_id = await self.memory.get_preference(
            "continuity.active_session_id",
            "",
        )
        if saved_session_id:
            restored = await self.bridge.restore_session(saved_session_id)
            if restored:
                self._session_id = self.bridge.session_id
                self._continuity_active = True
                console.print("  ✅ Continuity session restored", style="green")
            elif self.bridge.is_available:
                await self.memory.save_preference("continuity.active_session_id", "")
                logger.info("Saved Continuity session no longer exists in Hermes")

        await self.tts.initialize()
        console.print(f"  ✅ Text-to-Speech ready ({self.config.tts_provider})", style="green")

        # Wake-word detection is owned by Hermes Agent. IRIS never opens a
        # parallel detector or microphone stream during boot.
        from core.hermes_wake import read_hermes_wake_settings
        try:
            native_wake = read_hermes_wake_settings()
        except ValueError as exc:
            native_wake = {"enabled": False, "phrase": "config inválida"}
            logger.warning("Hermes wake config is invalid: %s", exc)
        console.print(
            f"  {'✅' if native_wake['enabled'] else '➖'} Hermes wake word: "
            f"{'ON' if native_wake['enabled'] else 'OFF'} ({native_wake['phrase']})",
            style="green" if native_wake["enabled"] else "dim",
        )

        # Vision Assistant never opens a camera from Core. The visible Face or
        # Companion surface owns the single local stream and publishes only
        # derived presence signals after explicit opt-in.
        console.print(
            f"  {'✅' if self.config.vision_assistant_enabled else '➖'} Vision Assistant: "
            f"{'ON' if self.config.vision_assistant_enabled else 'OFF'} (local surface)",
            style="green" if self.config.vision_assistant_enabled else "dim",
        )

        # Mount static files for the face UI
        project_root = Path(__file__).parent.parent
        face_dir = project_root / "face"
        mount_static_files(face_dir)

        console.print()
        console.print(
            Panel(
                f"[bold gold1]{self.personality.get_greeting()}[/]\n\n"
                f"  {p.emoji} Character: {p.name}\n"
                f"  🧠 Brain: {'Hermes' if self.bridge.is_available else 'Offline'}\n"
                f"  👂 Hermes wake word: {'ON' if native_wake['enabled'] else 'OFF'}\n"
                f"  🔊 TTS: {self.config.tts_provider}\n"
                f"  📷 Vision: {'ON' if self.config.vision_assistant_enabled else 'OFF'}\n"
                f"  🎭 Avatar: http://localhost:{self.config.websocket_port}/face/\n",
                border_style="gold1",
                title=f"[bold white]{p.name} Online[/]",
            )
        )

    async def run(self) -> None:
        """Keep the agent alive; chat and native Hermes wake events drive turns."""
        self._running = True

        # Speak greeting — pulls from PersonalityManager so renaming
        # the assistant in the wizard / config propagates here without
        # editing code. Falls back to a generic line if the personality
        # template ever returns empty.
        greeting = (
            self.personality.get_greeting().strip()
            or f"¡Hola! Soy {self.personality.personality.name}."
        )
        await self._respond({
            "texto_hablado": greeting,
            "emocion": "feliz",
            "animacion_virtual": "saludar_feliz",
        })

        while self._running:
            await asyncio.sleep(0.5)

    async def _think(
        self,
        user_text: str,
        images: list[str] | None = None,
        reasoning_effort: str | None = None,
        *,
        snapshot: dict[str, Any] | None = None,
        turn_context: str = "",
    ) -> dict[str, Any]:
        """Send user input to Hermes and get IRIS's response.

        ``images`` (Fase 2.C) is an optional list of data-URL strings
        (``data:image/png;base64,…``). When non-empty they're forwarded
        to ``bridge.send_message`` which builds a multimodal payload.
        Voice turns never set this; chat input from the side panel
        does when the user dropped an image onto the avatar.

        ``reasoning_effort`` (per-turn override) maps to Hermes' negotiated
        ``model_options.reasoning`` contract. Accepts ``'none' | 'low' |
        'medium' | 'high'``. When ``None`` (voice turns), or when an older
        Hermes lacks that capability, Hermes uses the persisted
        ``agent.reasoning_effort`` from its config. Modern runtimes can thus
        disable reasoning just for multimodal turns on models that cannot
        combine reasoning and image input.
        """

        # Inject user-emotion context from the face layer's
        # EmotionClassifier (websocket_server.py stashes the most
        # recent snapshot on `app.state.user_emotion`). We only fold
        # in confident, fresh snapshots — stale or low-confidence
        # readings would lie to the LLM about how the user feels.
        emotion_ctx = self._build_emotion_context()
        if emotion_ctx:
            logger.info("🎭 Emotion context: %s", emotion_ctx)

        # Compose the side-channel context. `memory_summary` is the
        # bridge's catch-all "non-message context" param — it ends up
        # appended to the system prompt, not as a chat turn.
        # V0/V1 Vision Assistant never starts a turn or injects observation
        # text automatically. Existing explicit emotion context remains a
        # separate, camera-owner-only feature.
        # ``turn_context``: this local turn's one-time offers, to ask for a photo
        # or a screen capture (core.visual_requests) and to leave a task working
        # in the background (core.background_tasks); system prompt only.
        side_ctx = "\n\n".join(filter(None, [emotion_ctx, turn_context]))
        screen_unavailable =bool(snapshot and snapshot.get("source") == "screen" and "unavailable" in snapshot)
        if screen_unavailable:
            from core.screen_snapshot import no_image_context

            side_ctx = "\n\n".join(filter(None, [side_ctx, no_image_context(snapshot["unavailable"])]))
            images = None

        # Reconnect first, then route images once, then send once: a turn that
        # reconnected used to send its images straight to the chat, skipping
        # the vision model and a photo's authorised destination (Codex F02/F03
        # review, R01). Without Hermes no image goes anywhere.
        available = self.bridge.is_available or await self.bridge.reconnect()
        if screen_unavailable and snapshot.get("route_token") is not None:
            if self.bridge.route_context()["token"] != snapshot["route_token"]:
                raise asyncio.CancelledError()  # don't move the original question to another profile
        if not available:
            return self.fallback.process(user_text)
        if images:
            prepared = await self._route_turn_images(user_text, images, snapshot=snapshot)
            if "reply" in prepared:
                return prepared["reply"]
            user_text, images = prepared["text"], prepared["images"]

        # NO context injection here. We rely on Hermes's stable session (the
        # `user` field in claw_bridge consolidates every call into ONE Hermes
        # session) — which means the agent runner already remembers the prior
        # turns and prepends them to the prompt itself as "[Chat messages since
        # your last reply - for context]". If we ALSO sent
        # memory.get_context_window() in the `messages` array, the same turns
        # would travel twice: that was the 16k+ input-token bloat the user kept
        # seeing. Memory still records every turn locally; it is just not
        # replayed at the Hermes call site, nor on reconnect.
        response = await self.bridge.send_message(
            user_text,
            memory_summary=side_ctx,
            images=images,
            reasoning_effort=reasoning_effort,
            progress_callback=self._on_tool_progress,
        )

        # F01: the bridge already published this turn's model outcome
        # (``_publish_model_turn``); it does not belong in IRIS' reply.
        if isinstance(response, dict):
            response.pop("_model_turn", None)

        # Hermes may rotate a durable session during context compression. Keep
        # IRIS' local scope and persisted pointer on the effective id returned
        # in X-Hermes-Session-Id instead of resurrecting the parent on restart.
        if self._continuity_active and self.bridge.session_id != self._session_id:
            self._session_id = self.bridge.session_id
            try:
                await self.memory.save_preference(
                    "continuity.active_session_id",
                    self._session_id,
                )
            except Exception as exc:
                logger.warning("Could not persist rotated Continuity session: %s", exc)

        return response

    async def _route_turn_images(
        self, user_text: str, images: list[str], *, snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """F02: the profile's vision setting decides how images reach Hermes.

        With the automatic setting they travel with the turn, as before. With
        an explicit vision model the images go to that model first and the
        chat turn carries its description: Hermes' API server would otherwise
        hand them to a chat model that reads images (``core.vision_model``).
        A camera photo taken on request (F03b, ``snapshot``) always goes to the
        vision route the user was shown, and only there. The conversation's
        chat model (F01) is unchanged either way.
        """
        from core import vision_model

        route = self.bridge.route_context()
        home = vision_model.profile_home(route["profile"])
        if snapshot is None:
            settings = await asyncio.to_thread(vision_model.read_configured, home)
            if vision_model.image_route(settings) == "chat":
                return {"text": user_text, "images": images}
            prompt = vision_model.ATTACHMENT_PROMPT.format(question=(user_text or "sin texto")[:500])
            expected = None
        else:
            from core.vision_snapshot import OUTCOMES, SNAPSHOT_PROMPT

            if snapshot.get("source") == "screen":
                from core.screen_snapshot import OUTCOMES, SNAPSHOT_PROMPT

            # The permission may have been revoked since the photo was taken:
            # checked again right before the photo leaves (Codex review, R04).
            authorized = snapshot.get("authorized")
            if callable(authorized) and not await authorized():
                return {"reply": {"texto_hablado": OUTCOMES["permission_denied"], "emocion": "neutral",
                                  "animacion_virtual": "pensar"}}
            prompt = SNAPSHOT_PROMPT.format(question=(user_text or "¿qué ves?")[:300])
            expected = snapshot["destination"]
        try:
            result = await vision_model.analyze(home, images, prompt, expected=expected)
        except vision_model.VisionModelError as exc:
            vision_model.record_analysis(route["profile"], {"ok": False, "code": exc.code, **exc.route})
            return {"reply": {
                "texto_hablado": f"No pude analizar la imagen. {exc.message}",
                "emocion": "triste",
                "animacion_virtual": "pensar",
            }}
        vision_model.record_analysis(route["profile"], {"ok": True, **result["route"]})
        if self.bridge.route_context()["token"] != route["token"]:
            # Wake or Continuity switched the conversation meanwhile: the
            # description belongs to a turn that is no longer the active one.
            return {"reply": {
                "texto_hablado": "La conversación cambió mientras analizaba la imagen; no envié la descripción.",
                "emocion": "neutral",
                "animacion_virtual": "pensar",
            }}
        describe = vision_model.describe_snapshot_for_chat if snapshot is not None else vision_model.describe_for_chat
        if snapshot is not None and snapshot.get("source") == "screen":
            return {"text": describe(user_text, result["analyses"], result["route"], source="screen"), "images": None}
        return {"text": describe(user_text, result["analyses"], result["route"]), "images": None}

    def _build_emotion_context(self) -> str:
        """Read the last user-emotion snapshot from app.state and turn
        it into a one-line natural-language hint the LLM can act on.

        We only emit a hint when:
          - There IS a snapshot (camera was on at some point).
          - It's fresh (<5s old; the user might have moved or stopped
            looking at the camera since).
          - We're confident (>0.4); confidence is computed by the
            classifier as the gap between top class and runner-up.

        The hint is short on purpose — verbose state floods the
        system prompt and biases responses unnaturally."""
        import time as _time
        from core.websocket_server import app as _ws_app

        emo = getattr(_ws_app.state, "user_emotion", None)
        if not emo:
            return ""

        age = _time.time() - emo.get("timestamp", 0)
        if age > 5.0:
            return ""
        if emo.get("confidence", 0.0) < 0.4:
            return ""
        if emo.get("label") == "neutral":
            return ""  # no actionable signal

        # Spanish hint to match IRIS's primary language. Intensity gate
        # at 0.3 keeps "happy" from being announced for a polite micro-
        # smile; only meaningful affect bleeds into the prompt.
        intensity = emo.get("intensity", 0.0)
        if intensity < 0.3:
            return ""
        label_es = {
            "happy":     "alegre",
            "sad":       "triste",
            "surprised": "sorprendido",
            "angry":     "molesto",
            "fearful":   "ansioso",
            "disgusted": "incómodo",
            "confused":  "confundido",
        }.get(emo["label"], emo["label"])
        descriptor = "muy" if intensity > 0.7 else "ligeramente" if intensity < 0.5 else ""
        descriptor = (descriptor + " ") if descriptor else ""
        return f"[Contexto: el usuario se ve {descriptor}{label_es}.]"

    async def _respond(self, response: dict[str, Any], *,
                       still_relevant: Callable[[], bool] | None = None) -> bool:
        """Process IRIS's response: TTS + avatar animation.

        Event ordering is critical. The previous version emitted
        `speech_start` BEFORE `tts.speak()` returned — and TTS can
        take 1-3 s to generate audio (longer for NeuTTS first call,
        ~500 ms for Edge-TTS). That meant:
          • The speech-animator started body micro-motion 1-3 s
            before the avatar was making any sound.
          • Subtitles appeared while IRIS was visibly silent.
          • The chat bubble showed the line several seconds early.

        New order (held inside `_speak_lock` so overlapping websocket
        turns are serialised cleanly instead of interleaving audio):
          1. emotion + animation + scene_action — visual setup that
             can land BEFORE audio (face changes, scene lighting,
             clip starts) without looking weird.
          2. tts.speak() — blocking generation (1-3 s).
          3. ONLY NOW: speech_start (triggers animator), subtitle,
             lip_sync — all events that should be in lock-step with
             actual audio playback.
          4. tts.play_audio() — blocks until done.
          5. speech_end.

        Delegation is fired AFTER the lock is released so the heavy
        sub-agent work overlaps the next idle window instead of
        blocking the speaker.

        Result: animator + subtitle + lipsync all start within ~1
        frame of audio onset. Visually feels in sync.

        ``still_relevant`` (a background task's presentation) is checked after
        the audio is generated, before any text or sound leaves: if it no longer
        holds, nothing is emitted and False is returned (Codex B01).
        """
        spoken_text = response.get("texto_hablado", "")
        emotion = response.get("emocion", "neutral")
        # Provider JSON is untrusted even when it parses correctly.  Resolve
        # legacy names and reject invented/non-string clip ids before they
        # reach either avatar surface; both then receive the same canonical
        # animation event and an invalid value degrades visibly to idle.
        animation = normalize_agent_animation(response.get("animacion_virtual", "idle"))
        # TODO: futuro — `accion_escena` (avatar caminando/interactuando
        # con el cuarto 3D). Pendiente hasta que las animaciones del
        # walk-cycle se vean naturales. Por ahora la cámara queda fija.
        # escena = response.get("accion_escena", "ninguna")

        # Spoken text is optional, but IRIS no longer owns sub-agent
        # delegation fields. Hermes handles advanced delegation and
        # scheduled work inside its own runtime.
        if spoken_text:
            console.print(f"[gold1]IRIS ({emotion}): {spoken_text}[/]")
            async with self._speak_lock:
                # Phase 1 — visual setup (can happen during TTS generation).
                await send_emotion(emotion)
                # Scene action paused — reactivate when the 3D walk-cycle
                # animations are natural enough to ship. The scene-action
                # transport remains available in the server;
                # uncomment + restore the `escena` extraction above.
                # if escena and escena != "ninguna":
                #     await send_scene_action(escena)

                # Phase 2 — generate audio (blocks 0.5-3 s depending on TTS
                # provider). The avatar's face/scene changes from phase 1
                # are already visible by the time the audio is ready, which
                # gives the user a moment to read the upcoming intent.
                # FIX T3 — propagate emotion to the TTS layer so engines
                # that emit emotion tags (Fish Speech today; future
                # extensions tomorrow) actually receive it. Backwards-
                # compatible: speak() still accepts the single-arg form.
                audio_bytes, viseme_data = await self.tts.speak(
                    spoken_text, emotion=emotion,
                )
                if still_relevant is not None and not still_relevant():
                    return False  # the conversation or the window changed while it was generated

                # Phase 3 — audio-synced events. These all need to fire
                # immediately before play_audio() blocks so the front-end
                # state changes match the moment sound hits the speakers.
                # Three-field payload (matches the JSON contract); when
                # accion_escena gets reactivated (TODO: futuro), restore
                # it in the kwargs here.
                # Trigger animation after TTS generation so AI-selected
                # poses start in sync with IRIS's spoken response.
                await send_animation(animation)
                await send_speech_start(
                    spoken_text,
                    emotion,
                    animation=animation,
                )
                await send_subtitle(spoken_text)

                # Phase 4 — audio delivery. Decide once per turn whether
                # one registered audio surface or the server
                # (sounddevice) owns playback:
                #
                #   • At least one audio-capable surface connected → send
                #     the bytes to exactly one owner via `tts_audio`. The
                #     other avatar surfaces receive visemes without bytes.
                #     The owner plays them and
                #     anchors viseme timing to the audio element's
                #     `onplaying` event, killing the legacy ~100-400 ms
                #     lip-sync drift AND making IRIS audible on
                #     phone / LAN devices for the first time.
                #
                #   • No face client connected → fall back to
                #     server-side sounddevice playback (headless / voice
                #     -only mode still works). Visemes are sent via the
                #     classic `lip_sync` event since no browser is
                #     animating a mouth anyway.
                #
                # Doing BOTH would produce double audio on the host
                # machine (WebView/browser + speakers), so we gate strictly
                # on registered capabilities and verify delivery.
                has_audio_surface = audio_clients_connected() > 0
                # FIX T2 sentinel — `b"FALLBACK_FAILED"` means every TTS
                # provider in the fallback chain refused / errored.
                # Treat as no audio: visemes still ride.
                real_audio = audio_bytes and audio_bytes != b"FALLBACK_FAILED"

                # When the browser owns playback, we need to KNOW how long
                # the audio will play so the server doesn't fire
                # `speech_end` before the user hears IRIS finish. Without
                # this delay, the face's speech_end handler stops the
                # subtitle, halts body animation, and locks the final
                # emotion BEFORE the audio element has even started
                # emitting samples — IRIS's mouth visibly finishes a
                # second before her voice does. We estimate duration
                # from the raw audio bytes (decode → samples / sample
                # rate). Cheap (one decode, tiny overhead vs the
                # synthesis itself).
                playback_duration: float = 0.0
                if has_audio_surface and real_audio:
                    try:
                        from core.tts import decode_audio
                        samples, sample_rate = decode_audio(audio_bytes)
                        if sample_rate > 0 and len(samples) > 0:
                            playback_duration = len(samples) / float(sample_rate)
                    except Exception as e:
                        # Decode failure shouldn't block the turn — use
                        # a generous heuristic from the byte count
                        # instead (Edge-TTS ≈ 24 kbps MP3 ≈ 3 KB/sec).
                        playback_duration = max(0.5, len(audio_bytes) / 3000.0)
                        logger.debug("decode for duration failed: %s", e)

                if has_audio_surface and real_audio:
                    # Determine mime type from the first bytes.
                    # Edge-TTS / OpenAI TTS / ElevenLabs default to MP3.
                    # WAV starts with "RIFF"; OGG with "OggS".
                    head = audio_bytes[:4]
                    if head == b"RIFF":
                        mime = "audio/wav"
                    elif head == b"OggS":
                        mime = "audio/ogg"
                    else:
                        mime = "audio/mpeg"
                    import base64
                    delivered = await send_tts_audio({
                        "audio":   base64.b64encode(audio_bytes).decode("ascii"),
                        "mime":    mime,
                        "visemes": viseme_data or [],
                    }, wait_for_end=True, duration_s=playback_duration)
                    if not delivered:
                        # The selected owner disconnected during send. Do not
                        # lose the utterance: keep any remaining renderer's
                        # mouth moving and fall back to host speakers.
                        if viseme_data:
                            await send_lip_sync(viseme_data)
                        await self.tts.play_audio(audio_bytes)
                elif has_audio_surface and viseme_data:
                    # No audio (fallback exhausted) but we still have
                    # word boundaries — keep the mouth alive in sync
                    # with the subtitle so the avatar reads "muted but
                    # talking" instead of "frozen". Estimate duration
                    # from the last viseme's start+duration so the
                    # subtitle doesn't vanish before the mouth finishes.
                    last = viseme_data[-1] if viseme_data else None
                    silent_duration = (
                        (last.get("time", 0.0) + last.get("duration", 0.25))
                        if isinstance(last, dict) else 0.0
                    )
                    delivered = await send_tts_audio({
                        "audio":   "",
                        "mime":    "audio/mpeg",
                        "visemes": viseme_data,
                    })
                    if not delivered:
                        await send_lip_sync(viseme_data)
                    if silent_duration > 0 and delivered:
                        await asyncio.sleep(silent_duration)
                elif not has_audio_surface:
                    # Headless / voice-only path: server speakers + the
                    # legacy `lip_sync` event (no browser to render
                    # anyway, but keep the contract for any other
                    # listener — debug tooling, future logging).
                    if viseme_data:
                        await send_lip_sync(viseme_data)
                    if real_audio:
                        await self.tts.play_audio(audio_bytes)

                # Phase 5 — close the loop.
                await send_speech_end()
                await send_status("idle")
                if audio_bytes == b"FALLBACK_FAILED":
                    # Silence alone reads as a broken speaker. The reply is already on screen.
                    reason = getattr(self.tts, "last_failure", "")
                    await broadcast_to_face("voice_unavailable", {
                        "message": f"Iris no pudo hablar. {reason} Revisa la voz en Configuración.".replace("  ", " "),
                    })
        return True

    def _publish_model_turn(self, outcome: dict[str, Any]) -> None:
        """Broadcast what Hermes confirmed, refused or cancelled for a turn's model.

        Called by the bridge when the turn ends, before a cancellation
        propagates, so a barge-in never leaves the previous result showing.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(broadcast_to_face("model_turn", dict(outcome)))

    def _on_tool_progress(self, event: dict[str, Any]) -> None:
        """Forward Hermes' ``tool.progress`` SSE events to the face WS.

        Quick Win #1 — Hermes emits these every time a tool starts /
        progresses / finishes inside a chat completion. The face uses
        them to show "🔧 buscando en internet…" so the avatar isn't
        silent while the LLM does long work. We pluck the most useful
        fields and ship them as a ``tool_progress`` WebSocket event.

        Synchronous on purpose — the bridge calls us inline from the
        SSE-reading loop, so awaiting here would deadlock the stream.
        We schedule the broadcast with ``asyncio.create_task`` so the
        bridge can keep reading.

        Defensive: if the running loop is closed (IRIS shutting
        down) or no clients are connected, the broadcast helper
        no-ops — never let a UX hint kill the chat path.
        """
        try:
            payload = {
                # Tool name (e.g. "web_extract", "terminal"). Falls
                # back to the SSE event/object so unknown event shapes
                # still render as something useful in the UI.
                "tool": (
                    event.get("tool")
                    or event.get("name")
                    or event.get("function_name")
                    or event.get("event")
                    or event.get("object")
                    or "tool"
                ),
                # Hermes' chat-completions SSE uses "running" /
                # "completed"; the runs API uses event names such as
                # "tool.started" / "tool.completed".
                "status": (
                    event.get("status")
                    or event.get("state")
                    or event.get("event")
                    or "running"
                ),
                # Human-readable hint: URL, command, primary arg preview,
                # or Hermes' own label from the progress payload.
                "detail": (
                    event.get("detail")
                    or event.get("message")
                    or event.get("preview")
                    or event.get("label")
                    or ""
                ),
                "toolCallId": event.get("toolCallId") or event.get("tool_call_id"),
                "duration": event.get("duration"),
                # None outside a user's turn: a background result being presented.
                "turn": current_turn.get(),
            }
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return
            loop.create_task(broadcast_to_face("tool_progress", payload))
        except Exception as exc:
            logger.debug("_on_tool_progress dropped event: %s", exc)

    async def interrupt(self, *, reason: str = "user_speech") -> None:
        """Halt the current turn — Fase 3 (barge-in support).

        Called when the user starts speaking while IRIS is still
        talking (or still thinking). Three things happen, in this
        order, because the order matters:

          1. ``tts.stop()`` — interrupt sounddevice playback first
             so the user stops hearing IRIS's voice within ~50ms.
             Without this step the rest is invisible: whatever audio
             buffer is already in PortAudio keeps draining.
          2. Cancel the in-flight turn task. This also propagates
             into ``bridge.send_message`` if the LLM hasn't responded
             yet, so a delayed reply doesn't suddenly start speaking
             over the user 30 seconds later. Cancellation propagates
             via ``asyncio.CancelledError`` which the WS handler
             catches as a clean exit.
          3. Mark the partial turn in Hermes's session memory so
             the next time IRIS talks she knows the previous reply
             was cut short. Without this annotation the next user
             message would land in Hermes's session immediately
             after IRIS's incomplete turn, and the LLM has no way
             to tell the speech ended on the listener's terms vs
             her own.

        Safe to call when nothing is happening — each step is
        idempotent.
        """
        logger.info("⏹  Interrupt: %s", reason)

        # 1. Stop the speakers.
        try:
            await self.tts.stop()
        except Exception as exc:
            logger.debug("tts.stop() during interrupt: %s", exc)

        # 2. Cancel the active turn (think + respond) locally AND
        #    server-side. Quick Win #2 — without the bridge stop call,
        #    Hermes (and whichever upstream provider it's routing to)
        #    keeps the run going to completion, billing the user for
        #    tokens we throw away. Fire-and-forget: stop_current_run
        #    is bounded to a 5s timeout internally and tolerates 404s
        #    when the run already finished naturally.
        async with self._turn_lock:
            t = self._current_turn_task
            if t is not None and not t.done():
                t.cancel()
        try:
            await self.bridge.stop_current_run()
        except Exception as exc:
            logger.debug("bridge.stop_current_run during interrupt: %s", exc)

        # 3. Tell the face to clean up its speech-state UI. We send
        # speech_end unconditionally — sending it twice is harmless
        # (the front-end is idempotent on this event), sending zero
        # times leaves the avatar stuck in "speaking" pose.
        try:
            await send_speech_end()
            await send_status("idle")
        except Exception as exc:
            logger.debug("face cleanup during interrupt: %s", exc)

        # 4. Annotate memory so Hermes's session reflects reality.
        try:
            await self.memory.remember(
                "system",
                f"[IRIS fue interrumpida por el usuario — {reason}]",
                session_id=self._session_id,
            )
        except Exception as exc:
            logger.debug("memory annotation during interrupt: %s", exc)

    async def shutdown(self) -> None:
        """Graceful shutdown."""
        console.print("\n[yellow]Shutting down IRIS...[/]")
        self._running = False
        from core.websocket_server import _app_extensions
        await _app_extensions.close()
        await self.bridge.close()
        await self.tts.close()  # stops isolated extra workers (A08.5)
        await self.memory.close()
        console.print("[green]Goodbye! ⚔️[/]")


# Legacy extension alias.
AliceAgent = IRISAgent


def _install_proactor_noise_filter() -> None:
    """Silence the harmless `ConnectionResetError` flood from the
    Windows ProactorEventLoop.

    When a browser tab refreshes, navigates away, or a phone drops off
    the LAN, the OS forcibly closes the TCP socket. asyncio's
    ``_ProactorBasePipeTransport._call_connection_lost`` then tries a
    polite ``socket.shutdown(SHUT_RDWR)`` on the already-dead handle,
    Windows answers with ``WinError 10054`` ("Se ha forzado la
    interrupción de una conexión existente por el host remoto"), and
    the default loop exception handler prints the full traceback.
    The connection had already ended cleanly from our point of view —
    websocket_server logs ``🔌 Face layer disconnected`` exactly once
    and the clients reconnect a second later. The traceback is pure
    noise that scared more than one user.

    We install an exception handler that swallows ConnectionResetError
    *only* when it originates from a ProactorBasePipeTransport's
    cleanup path. Everything else is forwarded to the default handler
    so genuine bugs still surface.

    On non-Windows event loops this is a no-op.
    """
    import asyncio
    import sys
    if sys.platform != "win32":
        return

    default_handler = None

    def _filter(loop, context):
        exc = context.get("exception")
        # The traceback shows it comes from
        # `_ProactorBasePipeTransport._call_connection_lost`; we don't
        # have the original frame here, but the combination of
        # ConnectionResetError + WinError 10054 inside a "callback"
        # context is unique enough that filtering on those is safe.
        if isinstance(exc, ConnectionResetError):
            winerr = getattr(exc, "winerror", None)
            if winerr in (10053, 10054):
                return  # benign — client TCP closed abruptly
        if default_handler is not None:
            default_handler(loop, context)
        else:
            loop.default_exception_handler(context)

    loop = asyncio.get_event_loop()
    # Preserve whatever handler was already there so we layer cleanly.
    default_handler = loop.get_exception_handler()
    loop.set_exception_handler(_filter)


async def _run_agent() -> None:
    """Claim a compatible profile before constructing any mutable services."""
    from core.profile_migration import migrate_animation_profile
    from core.profile_paths import async_profile_writer, get_profile_paths

    paths = get_profile_paths()
    factory = get_runtime().assets_root / "animations"
    async with async_profile_writer(
        "core", paths=paths,
        prepare=lambda: migrate_animation_profile(factory, paths.animations),
    ):
        await _run_agent_services()


async def _run_agent_services() -> None:
    """Run the agent and the WebSocket server concurrently."""
    # Filter out the harmless Windows ProactorEventLoop noise BEFORE
    # we open any sockets — once a client connects and disconnects,
    # the asyncio internals fire the unhandled-callback path.
    _install_proactor_noise_filter()

    agent = IRISAgent()
    await agent.initialize()

    # Expose the live agent on the FastAPI app's state so the
    # `user_text` WebSocket handler in core.websocket_server can call
    # `agent._think(text)` directly. Without this wiring, typed chat
    # messages fell into a `getattr(app.state, "agent", None) is None`
    # branch and the user got the "El cerebro todavía no está listo"
    # error even when IRIS was running.
    app.state.agent = agent

    # Hermes owns wake detection + microphone capture through its native
    # gateway. The runtime stays dormant unless a prior explicit toggle left
    # wake_word.enabled on, or the user clicks IRIS' ear icon this session.
    hermes_wake = HermesWakeRuntime(handle_hermes_wake_event)
    app.state.hermes_wake = hermes_wake

    async def _restore_native_wake() -> None:
        try:
            enabled = read_hermes_wake_settings()["enabled"]
        except ValueError as exc:
            logger.warning("Hermes wake config is invalid: %s", exc)
            return
        if not enabled:
            return
        try:
            restored = await hermes_wake.start_listener(persist=False)
            # The browser can connect while Hermes is still loading.  Push
            # the completed auto-arm state instead of relying only on the
            # earlier WebSocket hello snapshot.
            await broadcast_to_face("hermes_wake_state", restored)
        except Exception as exc:
            logger.warning("Hermes wake word could not auto-arm: %s", exc)
            await handle_hermes_wake_event("wake.error", {"error": str(exc)})

    wake_restore_task = asyncio.create_task(
        _restore_native_wake(), name="Hermes-wake-auto-arm",
    )

    # F04–F06: automations observe Iris activity and run only rules the user
    # enabled, with "Permitir" in Capability Center. No rule exists by default.
    from core.websocket_server import run_automations

    automations_task = asyncio.create_task(run_automations(), name="Iris-automations")

    # Background tasks: long work Iris leaves running while the conversation goes on.
    from core.websocket_server import run_background_tasks

    background_task = asyncio.create_task(run_background_tasks(), name="Iris-background-tasks")

    # Notices installed apps schedule: shown when due, also with the app's window closed.
    from core.websocket_server import run_app_notices

    app_notices_task = asyncio.create_task(run_app_notices(), name="Iris-app-notices")

    # F10: Google connections. Dormant: no login and no read until the person asks in Conexiones.
    from core.websocket_server import run_google_connections

    google_task = asyncio.create_task(run_google_connections(), name="Iris-google-connections")

    # ── HTTPS for LAN + authenticated loopback for the native shell ─
    # Browsers gate `navigator.mediaDevices` (camera, mic, WebRTC) behind
    # a "secure context", which means HTTPS or localhost. A phone hitting
    # http://<LAN-IP>:PORT therefore can't use the mic/cam — Silero VAD
    # and WebRTC-based remote features can fail with a cryptic
    # "cannot read properties of undefined (reading 'getUserMedia')".
    # Localhost itself is a secure context, so a loopback-only install does
    # not need TLS (or a certificate warning). When LAN access is enabled we
    # generate HTTPS for remote devices and also expose the same FastAPI app
    # on a second HTTP listener bound *only* to 127.0.0.1. The Tauri shell
    # uses that private listener with its pairing token, avoiding any
    # dangerous "accept invalid certificates" WebView setting.
    from core.tls import ensure_tls_cert, detect_lan_ip
    lan_ip = detect_lan_ip()
    cert_path = key_path = None
    if agent.config.lan_access:
        extra_hosts = [lan_ip] if lan_ip else []
        cert_path, key_path = ensure_tls_cert(extra_hosts)
    tls_enabled = bool(cert_path and key_path)
    app.state.tls_enabled = tls_enabled

    # Build the uvicorn config — pass the cert paths only if we got
    # them. If TLS setup failed we still boot, just over plain HTTP, so
    # the user at least gets the UI on localhost.
    config_kwargs: dict[str, object] = {
        "host":      agent.config.websocket_host,
        "port":      agent.config.websocket_port,
        "log_level": "warning",
    }
    if tls_enabled:
        config_kwargs["ssl_certfile"] = str(cert_path)
        config_kwargs["ssl_keyfile"]  = str(key_path)

    scheme = "https" if tls_enabled else "http"
    port   = agent.config.websocket_port
    console.print(
        f"[bold green]Avatar UI:[/] {scheme}://localhost:{port}/face/"
        + (f"   ·   LAN: {scheme}://{lan_ip}:{port}/face/" if lan_ip else "")
    )
    if agent.config.lan_access and not tls_enabled:
        console.print(
            "[yellow]⚠ HTTPS no se pudo activar — la cámara y el micrófono "
            "del celular no van a funcionar por LAN. Revisá los logs.[/]"
        )

    config = uvicorn.Config(app, **config_kwargs)
    server = uvicorn.Server(config)

    companion_server: uvicorn.Server | None = None
    if tls_enabled:
        loopback_port = agent.config.companion_loopback_port
        if loopback_port == agent.config.websocket_port:
            loopback_port = 8766 if agent.config.websocket_port != 8766 else 8767
            logger.warning(
                "companion_loopback_port matched websocket_port; using %s",
                loopback_port,
            )
        companion_config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=loopback_port,
            log_level="warning",
        )
        companion_server = uvicorn.Server(companion_config)
        console.print(
            f"[dim]Desktop Companion:[/] http://127.0.0.1:"
            f"{loopback_port} (loopback autenticado)"
        )

    from core.companion_auth import (
        clear_companion_runtime,
        get_or_create_companion_token,
        publish_companion_runtime,
    )
    companion_api_port = (
        loopback_port if companion_server is not None else port
    )
    get_or_create_companion_token()
    publish_companion_runtime(
        api_port=companion_api_port,
        iris_os_url=f"{scheme}://127.0.0.1:{port}/face/",
    )

    # Handle shutdown signals
    loop = asyncio.get_event_loop()

    def _signal_handler() -> None:
        asyncio.ensure_future(agent.shutdown())
        server.should_exit = True
        if companion_server is not None:
            companion_server.should_exit = True

    if sys.platform != "win32":
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _signal_handler)

    # Run server and agent loop concurrently
    try:
        services = [server.serve(), agent.run()]
        if companion_server is not None:
            services.append(companion_server.serve())
        await asyncio.gather(*services)
    except KeyboardInterrupt:
        pass
    finally:
        clear_companion_runtime()
        if not wake_restore_task.done():
            wake_restore_task.cancel()
        automations_task.cancel()
        background_task.cancel()  # its runs keep their records; the next start recovers them
        app_notices_task.cancel()  # notices are stored; the next start shows what fell due
        google_task.cancel()
        await hermes_wake.close()
        await agent.shutdown()


def main() -> None:
    """Entry point for IRIS."""
    get_runtime()
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        asyncio.run(_run_agent())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

