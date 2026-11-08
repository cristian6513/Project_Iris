# IRIS OS - Voice, wake words, and digital presence research

> **Referencia histórica/de diseño.** Puede describir rutas, versiones o
> funciones del corte original. Para uso actual, consulta [Estado actual](estado-actual.md)
> y las [guías](README.md); no se altera aquí la evidencia histórica.

Updated: 2026-08-10

This document extends `iris_os_master.md`. It records current voice capabilities,
candidate technologies, product decisions, and claims that must be verified before
they become dependencies or user-facing options.

The 2026-08-10 review cross-checked IRIS against the locally installed Hermes Agent
0.20.0 source and current upstream repositories. Statements below now distinguish
implemented behavior from research; a target is not a product promise.

## Status labels

- **Current**: already part of the project or its supported configuration flow.
- **Candidate**: worth evaluating in an isolated prototype.
- **Watch**: interesting announcement, but no verified release or integration plan.
- **Unverified**: a social-media claim without an official repository, model card, or
  license confirmation.

## Product direction

IRIS should feel present without being invasive:

```
Microphone
  -> local wake detection / push-to-talk
  -> local voice activity detection
  -> speech-to-text
  -> Hermes Agent or configured LLM
  -> streamed text-to-speech
  -> audio playback + IRIS avatar state
```

The avatar layer is separate from the voice loop. It receives simple states such as
`idle`, `listening`, `thinking`, `speaking`, and `interrupted`; it must not
block speech recognition, model responses, or the OS shell.

## Wake and listening modes

Voice activation remains **off by default**. The user must select one of these modes:

1. **Push to talk**
   - Hermes supports `voice.record`, but IRIS does not currently expose a dedicated
     push-to-talk control in its face/desktop UI.
   - Typed chat remains the reliable no-microphone fallback.

2. **Wake word: Hey Hermes**
   - Hermes' bundled openWakeWord mode and recommended compatibility path.

3. **Profile-name activation**
   - IRIS now exposes an editable `Hey + name` field backed by Hermes' Sherpa
     configuration. Existing custom phrases remain untouched until the user
     explicitly selects the editable mode and saves.
   - Hermes can route enrolled profile phrases through its native Sherpa engine.
   - IRIS preserves and routes an already-enrolled profile, but the IRIS UI does not
     yet create or manage those enrollments.

Target controls and audited status:

- Wake mode selector and visible microphone state: **implemented**.
- Software stop through the ear/toggle: **implemented**; a failed stop preserves the
  last confirmed active state instead of claiming the microphone was released.
- Sensitivity: **implemented in advanced settings**, using Hermes' native 0..1
  threshold. Higher is stricter for the supported IRIS presets.
- Cooldown: **provided by Hermes as a fixed two-second detector cooldown**; IRIS does
  not expose a fake configurable control.
- Dedicated activation test: **not implemented**. “Activate now” starts the real
  listener and persists explicit consent; it is not labelled as a disposable test.
- A short privacy explanation:

  > While wake mode is active, Hermes detects the phrase locally. Only after a match
  > does it record a command, whose transcription follows the configured STT provider.

- Barge-in: **backend cancellation exists, but voice-driven barge-in is not wired to
  a visible IRIS microphone action**. The detector is paused while IRIS responds so
  the speakers cannot wake it; do not describe spoken interruption as complete.
- Dedicated wake activity log: **not implemented**. The generic Activity Log must not
  be described as an audio log, and IRIS does not persist wake audio.

## Hermes Agent upstream notes

The installed Hermes Agent 0.20.0 source confirms the native `wake.start`,
`wake.stop`, `wake.status`, `wake.pause`, `wake.resume`, `voice.toggle`, and
`voice.record` gateway methods used by IRIS. Its wake module defaults to disabled,
detects locally, exposes sensitivity, applies confirmation frames and a fixed cooldown,
and supports Sherpa profile routing. IRIS deliberately reuses those contracts instead
of maintaining a second detector or browser audio stream.

## Voice stack candidates

### Current configuration baseline

Maintain the current Wizard voice choices until a replacement passes functional,
licensing, hardware, and Spanish-quality checks. A new engine must not replace a
working choice merely because it is newer.

### Hugging Face speech-to-speech

Repository supplied for investigation:

- https://github.com/huggingface/speech-to-speech

**Status: Partially integrated as an optional TTS engine.**

The current official repository is Apache-2.0 and packages a modular VAD → STT → LLM
→ TTS pipeline with an OpenAI Realtime-compatible WebSocket API. Its component list
has changed substantially: current defaults include Parakeet TDT, an OpenAI-compatible
LLM backend and Qwen3-TTS; older implementations such as MeloTTS are archived. The
repository documents Spanish support, CPU/CUDA paths for several components and
Apple Silicon paths, but that does not prove a good Windows AMD experience.

IRIS pins the reviewed `speech-to-speech==0.2.12` alpha release and embeds only its
Qwen3-TTS handler. It exposes the official 0.6B/1.7B CustomVoice checkpoints with
nine preset speakers and the matching Base checkpoints for reference cloning. The
Base path stores a normalized 3–15 second WAV plus its exact transcript. CustomVoice
does not pretend to clone: selecting «Clonar» switches to Base at the same model size.
Spanish is supported, but none of the nine preset speakers is native Spanish; the
Wizard says so instead of presenting Aiden or another preset as a Spanish voice.

This deliberately does **not** start the upstream VAD → STT → LLM → TTS Realtime
server. Hermes remains the only microphone, wake-word, session, LLM and tool owner,
which avoids competing audio readers and duplicate agent state.

Current fit for IRIS:

- Optional local Spanish TTS with preset voices or authorized reference cloning.
- A narrow adapter seam around the alpha handler, so an upstream import change fails
  as an unavailable optional engine instead of breaking IRIS startup.
- An explicit heavy-model warning: first use downloads weights, CPU may be slow, and
  Windows AMD acceleration is not promised.
- The component is not bundled into the aggregate project extra. A Windows/Python
  3.11 dry resolution succeeded but required a large dependency tree and a shared
  Hugging Face Hub version change; installation therefore remains an explicit button.
  On macOS, the Wizard blocks coexistence with NeuTTS 1.4.1 because their exact
  Transformers requirements conflict.

Still required before calling it hardware-validated:

1. Test Windows CPU and the real acceleration path for AMD hardware with downloaded
   weights; the current development machine has no CUDA route.
2. Benchmark Spanish TTS quality and clone similarity with authorized samples.
3. Measure cold start, warm start, response-start latency, RAM, VRAM, and CPU use.
4. Re-review imports and setup parameters before moving beyond the pinned alpha.
5. Keep the full upstream Realtime pipeline isolated unless Hermes exposes a clear
   component boundary that prevents duplicate sessions and microphone ownership.

### Echo-TTS / EchoDiT

**Status: Official repository verified; unsuitable for Wizard integration today.**

Official source: https://github.com/jordandare/echo-tts

The release now provides inference code and weights for a reference-conditioned TTS
model. The repository requires Python 3.10+ and a CUDA GPU with at least 8 GB VRAM.
Code is mostly MIT, but the weights and generated audio are CC-BY-NC-SA-4.0 because
of the Fish Speech S1-DAC dependency. Blockwise generation exists, while streaming is
explicitly not included in the current code. These license and hardware constraints
make it a research benchmark, not a selectable IRIS provider.

Before reconsidering it, validate:

- Spanish capability and voice terms;
- non-CUDA/AMD support and consumer latency;
- streaming latency and VRAM needs;
- reproducible installation on Windows.

### Wake-word engines

| Candidate | Role | Decision |
| --- | --- | --- |
| Hermes openWakeWord | Bundled `Hey Hermes` detector | Preferred built-in path; no external key |
| Hermes Sherpa | Open-vocabulary local wake word | Native profile routing; IRIS preserves existing enrolled phrases |

## Digital presence and real-time avatar research

### Social posts to treat carefully

The supplied posts describe a project called `GPT-Live`, simultaneous voice
conversation, and digital humans that use projects such as LiveTalking, MuseTalk, and
Wav2Lip.

**Status: Mixed.** The claimed GPT-Live/OpenAI announcement remains unverified, but
the referenced projects have identifiable upstream repositories: `lipku/LiveTalking`
(Apache-2.0), `TMElyralab/MuseTalk`, and `Rudrabha/Wav2Lip`. LiveTalking documents a
heavy Linux/CUDA-oriented streaming stack and MuseTalk/Wav2Lip bring their own model
and usage constraints. None is integrated into IRIS, and none should be inferred from
the unverified social post.

### Product direction that remains valid independently

IRIS does not need a video-generation stack to feel alive. The first useful embodied
version can use the locally rendered VRM avatar and a small state machine:

- `idle`
- `listening`
- `thinking`
- `speaking`
- `muted`
- `interrupted`
- `working`
- `away`

Later, a TTS engine may provide timings or phoneme/viseme events to drive mouth
shapes. This should be an enhancement, not a prerequisite for reliable voice.

Potential later research, each independently licensed and tested:

- LiveTalking-style streaming avatar rendering;
- MuseTalk/Wav2Lip-style lip synchronization;
- local emotion and expression blending;
- a desktop-resident IRIS companion that stays available without forcing chat open.

Avoid a heavy real-time video pipeline in the first public release. The custom IRIS
VRM plus lightweight state and viseme animation is more compatible with the OS design,
more private, and easier to recover when the voice stack fails.

## Hardware and performance policy

Reference development machine:

- AMD Radeon RX 9060 XT, 16 GB VRAM
- Ryzen 5 5600G
- Windows

Do not assume a repository supports this system simply because it supports a GPU.
Many voice projects document CUDA first. Every candidate needs an actual Windows test
for CPU mode and its supported AMD path, such as a validated ONNX, DirectML, ROCm, or
vendor-supported runtime where applicable.

Benchmark record for every candidate:

| Measure | Why it matters |
| --- | --- |
| Cold and warm startup | The OS should not feel stalled when voice opens |
| Voice activity detection delay | Affects whether IRIS feels attentive |
| STT real-time factor and Spanish accuracy | Determines usable conversation |
| Time to first spoken response | Primary perceived latency metric |
| Barge-in success rate | Prevents the assistant from talking over the user |
| CPU, RAM, VRAM, and temperature | Prevents background features from degrading the OS |
| Failure recovery | Device loss and model errors must return to a clear ready state |

## Phased implementation plan

### Phase A - Reliable interaction

1. Expose Hermes push-to-talk only if it can be done without a competing microphone
   owner. **Open.**
2. Add a clear voice session state to the shell and avatar. **Partial:** listening,
   detected, recording, processing, speaking and idle are represented; muted and
   interrupted remain incomplete as voice-driven states.
3. Local VAD is provided by Hermes voice capture. Response cancellation exists in the
   backend; a real spoken barge-in trigger remains open.
4. Measure the existing voice engines before adding another runtime.

### Phase B - Wake mode

1. Use Hermes' bundled `Hey Hermes` compatibility mode. **Complete.**
2. Keep profile routing under Hermes' native configuration. **Complete/partial:**
   routing is honored and IRIS provides an editable wake-name field; profile
   catalog management and real microphone/model validation remain native to Hermes.
3. Sensitivity, visible state, software stop and privacy copy are complete. Hermes owns
   the fixed cooldown. A dedicated non-persistent activation test remains open.

### Phase C - Pipeline evaluation

1. Benchmark the optional Qwen3-TTS component against current Wizard providers on the
   defined table. **Integration complete; hardware benchmark open.**
2. Keep the full Hugging Face Realtime pipeline as isolated research, not a second
   agent runtime inside IRIS.
3. Promote the option beyond “partial” only after real-device Spanish tests.

### Phase D - Avatar presence

1. Connect voice state to the IRIS VRM avatar.
2. Add light idle, listening, thinking, speaking, and interruption animations.
3. Add viseme/phoneme support only when a selected TTS engine exposes trustworthy
   timing data.

### Phase E - Advanced visual speech

1. Evaluate external lip-sync or real-time digital-human projects only with a specific
   upstream repository, license review, performance test, and opt-in privacy model.
2. Keep the lightweight VRM fallback available at all times.

## Risks and guardrails

- Never activate passive listening by default.
- Never promise local processing when a selected provider sends audio remotely.
- Never add an unverified social-media project to the public dependency list.
- Do not bundle models whose redistribution or voice license is unclear.
- Keep voice, camera, and Vision Assistant permissions independent.
- A voice engine failure must not take down the OS shell, chat, camera, or updater.
- Do not make the avatar's visual animation a dependency for the underlying response.

## Follow-up research needed

1. Measure false accepts and microphone compatibility for Hermes' native profile
   phrases across Windows, Linux and macOS.
2. Add real-device tests for sensitivity changes and failed/disconnected microphones.
3. Decide whether a temporary, non-persistent activation test can be built strictly
   through Hermes' supported gateway without rewriting consent behind the user.
4. Benchmark the integrated Qwen3-TTS component on real CPU/CUDA/Metal hardware; keep
   Echo-TTS and the full Hugging Face Realtime pipeline isolated.
5. Verify GPT-Live independently; do not use adjacent projects as evidence for it.
