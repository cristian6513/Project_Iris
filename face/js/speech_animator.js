/**
 * IRIS Speech Animator — body micro-motion + synthetic mouth while talking.
 *
 * The lipsync engine drives the mouth IF the TTS provider returned
 * visemes (edge-tts does, NeuTTS/Kokoro/OpenAI don't). For everyone
 * else, we drive `mouthOpenness` ourselves from a smoothed random
 * envelope at typical syllabic rate so the avatar at least LOOKS
 * like it's articulating, even when no real viseme stream is
 * available. The lipsync engine takes priority when active — we
 * only fill in when its `_smoothedMouth` is essentially zero.
 *
 * Body motion strategy (rewrite):
 *   The previous version used `bone.quaternion.multiply(deltaQ)`
 *   which accumulates across frames whenever no clip mixer is
 *   actively writing the bone — produced visible drift after a
 *   few seconds, especially on the arms. The new version:
 *
 *     1. On `start()`, snapshot each target bone's CURRENT
 *        quaternion as a stable "base pose" for this utterance.
 *     2. Each frame during speech, write
 *        `bone.quaternion = base * deltaQ` (SET, not multiply).
 *     3. On `stop()`, fade `deltaQ` toward identity over ~400ms,
 *        then stop touching the bones — releasing control back
 *        to the mixer.
 *
 *   This guarantees no accumulation: deltaQ each frame is computed
 *   from absolute phase, base is constant during the utterance,
 *   so the bone oscillates AROUND base rather than drifting away.
 *
 * Scope (changed from the first pass):
 *   • Animates head + chest only. Arms are deliberately NOT
 *     touched — they have their own clip animations (saludar_feliz,
 *     etc.) and the previous shoulder roll fought with rest pose
 *     orientations on stylized rigs (looked like arms going
 *     "backwards"). Subtle chest lift already implies shoulders.
 *   • Amplitudes cut ~50%, frequencies cut ~40%. The first version
 *     was too aggressive — looked like IRIS was conducting an
 *     orchestra. Now reads as gentle conversational presence.
 */

import * as THREE from 'three';


// ── Tunables (tuned down from v1 after user feedback) ────────────

// Frequencies (Hz). Slower than v1 — speech rhythm is closer to
// 1-2 Hz "head settle" than 2-3 Hz "nodding agreement". Tighter
// rates feel manic; ~1.4 Hz reads as natural conversational presence.
const HEAD_BOB_FREQ      = 1.4;
const CHEST_BREATH_FREQ  = 1.1;

// Amplitudes (radians). Cut ~50% from v1. At full envelope these
// peak at ~1° on each axis — visible but not theatrical.
const HEAD_BOB_PITCH_AMP = 0.018;   // ~1.0°
const HEAD_BOB_YAW_AMP   = 0.012;   // ~0.7°
const CHEST_BREATH_AMP   = 0.012;   // ~0.7°

// Synthetic mouth envelope when no real visemes are arriving.
// 5 Hz matches typical syllabic rate for Spanish and English
// conversational speech. Random walk + sin envelope produces a
// "speaking burst" pattern instead of pure sine (which sounds
// like humming).
const MOUTH_FALLBACK_FREQ_LOW  = 4.0;
const MOUTH_FALLBACK_FREQ_HIGH = 7.5;
const MOUTH_FALLBACK_PEAK      = 0.55;   // never fully gape

// Eyebrow micro-emphasis (kept from v1 — well-received). Pulses
// the `surprised` expression briefly every 2-4.5 s for emphasis.
const EMPHASIS_MIN_INTERVAL = 2.0;
const EMPHASIS_MAX_INTERVAL = 4.5;
const EMPHASIS_PEAK         = 0.15;     // gentler than v1's 0.18

// Activation envelope.
const FADE_IN_DURATION  = 0.30;
const FADE_OUT_DURATION = 0.50;


// ── Class ────────────────────────────────────────────────────────

export class SpeechAnimator {
    /** @param {object} avatarManager - AvatarManager instance. */
    constructor(avatarManager) {
        this.avatarManager = avatarManager;

        this._talking  = false;
        this._envelope = 0;            // 0..1 fade
        this._fadeRate = 0;            // signed

        // Phase clocks per channel.
        this._phaseHead   = Math.random() * Math.PI * 2;
        this._phaseChest  = Math.random() * Math.PI * 2;
        this._phaseMouth  = 0;
        this._mouthFreq   = MOUTH_FALLBACK_FREQ_LOW;
        this._mouthFreqTimer = 0;       // re-rolls every ~0.3s for variety
        this._syntheticMouthEnabled = true;
        this.lipSync = null;

        // Eyebrow emphasis scheduler.
        this._nextEmphasisAt = 0;
        this._emphasisActive = 0;
        this._emphasisDecay  = 0;

        // Bone refs + per-utterance base quaternions. Captured in
        // `start()` and used as the stable reference each frame so
        // we don't accumulate. `_baseQuats[boneName]` is the bone's
        // pose at the moment speech started — typically the rest
        // pose plus any continuous breath, since clips are paused.
        this._bones     = null;
        this._baseQuats = null;

        // Scratch math objects (no per-frame GC).
        this._dq = new THREE.Quaternion();
        this._eu = new THREE.Euler(0, 0, 0, 'YXZ');
        this._tq = new THREE.Quaternion();
    }

    /** Activate speech motion. Idempotent. */
    start() {
        const wasTalking = this._talking;
        this._talking  = true;
        this._fadeRate = 1.0 / Math.max(0.0001, FADE_IN_DURATION);
        if (!wasTalking) {
            // Snapshot the bone base poses ONCE per utterance so the
            // micro-motion oscillates around a stable reference.
            this._captureBasePoses();
            this._scheduleNextEmphasis(0);
        }
    }

    /** Deactivate; envelope decays then we release the bones. */
    stop() {
        this._talking  = false;
        this._fadeRate = -1.0 / Math.max(0.0001, FADE_OUT_DURATION);
    }

    setSyntheticMouthEnabled(enabled) {
        this._syntheticMouthEnabled = !!enabled;
    }

    /**
     * Temporarily yield bone ownership to a full-body VRMA clip.
     *
     * The app skips `update()` while a one-shot animation is active,
     * but IRIS may still be speaking when the clip finishes. If we keep
     * the head/chest base quaternions captured before the clip, the next
     * speech frame writes `oldBase * tinyBob` over the clip's final pose,
     * which reads like the avatar correcting itself back to center.
     * Clearing the bases makes the next update recapture the current pose.
     */
    yieldToExternalClip() {
        this._baseQuats = null;
    }

    /** Per-frame driver. Runs after avatarManager.update(). */
    update(delta, elapsed) {
        // Advance fade envelope.
        this._envelope = THREE.MathUtils.clamp(
            this._envelope + this._fadeRate * delta, 0, 1,
        );
        if (this._envelope <= 0 && !this._talking) {
            // Fully released — drop the base poses so the next start()
            // re-captures fresh (in case the avatar was swapped).
            this._baseQuats = null;
            return;
        }

        const vrm = this.avatarManager?.avatar?.vrm;
        if (!vrm?.humanoid) return;

        this._ensureBones(vrm);
        if (!this._bones) return;

        // First frame after start() with bones available: snapshot.
        if (!this._baseQuats) this._captureBasePoses();
        if (!this._baseQuats) return;

        const env = this._envelope;

        // Phase advances.
        this._phaseHead  += delta * HEAD_BOB_FREQ      * Math.PI * 2;
        this._phaseChest += delta * CHEST_BREATH_FREQ  * Math.PI * 2;

        // Compute small-angle deltas.
        const headPitch = Math.sin(this._phaseHead)         * HEAD_BOB_PITCH_AMP * env;
        const headYaw   = Math.sin(this._phaseHead * 0.7)   * HEAD_BOB_YAW_AMP   * env;
        const chestPitch = Math.sin(this._phaseChest)       * CHEST_BREATH_AMP   * env;

        const avatar = this.avatarManager.avatar;
        const headGestureActive = !!avatar?._headAnim;
        if (headGestureActive) {
            // Short procedural nod/shake/tilt gestures own the head bone.
            // Keep mouth/lipsync alive below, but stop writing head/chest so
            // the gesture does not look like it is being pulled back to center.
            this._baseQuats = null;
        } else {
            this._writeBoneFromBase('head',  headPitch,  headYaw, 0);
            this._writeBoneFromBase('chest', chestPitch, 0,       0);
        }

        // ── Synthetic mouth (only fires when no real lipsync is
        //    driving). Real lipsync writes to mouthOpenness via
        //    avatar.setMouthOpen(); we read that to decide whether
        //    we're needed. If the LipSync engine is producing values
        //    >0.05, we yield. Otherwise we fill in. ──
        const realMouth = avatar?.mouthOpenness ?? 0;
        const lipSyncDriving = !!this.lipSync?.isDrivingMouth?.();
        if (this._talking && this._syntheticMouthEnabled && !lipSyncDriving && realMouth < 0.05) {
            // Vary the rate a little so it doesn't loop audibly the same.
            this._mouthFreqTimer -= delta;
            if (this._mouthFreqTimer <= 0) {
                this._mouthFreqTimer = 0.2 + Math.random() * 0.4;
                this._mouthFreq = MOUTH_FALLBACK_FREQ_LOW
                    + Math.random() * (MOUTH_FALLBACK_FREQ_HIGH - MOUTH_FALLBACK_FREQ_LOW);
            }
            this._phaseMouth += delta * this._mouthFreq * Math.PI * 2;
            // Two-frequency mix gives "speaking burst" feel — base
            // syllable wave times a slower amplitude envelope so the
            // mouth doesn't pulse uniformly.
            const base    = (Math.sin(this._phaseMouth) + 1) * 0.5;        // 0..1
            const accent  = (Math.sin(this._phaseMouth * 0.31) + 1) * 0.5; // 0..1
            const value = MOUTH_FALLBACK_PEAK * env * base * (0.4 + 0.6 * accent);
            avatar?.setMouthOpen?.(Math.max(0, Math.min(1, value)));
        }

        // Eyebrow emphasis bumps.
        if (this._talking && elapsed >= this._nextEmphasisAt) {
            this._scheduleNextEmphasis(elapsed);
            this._emphasisActive = EMPHASIS_PEAK * env;
            this._emphasisDecay  = EMPHASIS_PEAK / 0.25;
            try {
                vrm.expressionManager?.setValue?.('surprised', this._emphasisActive);
            } catch { /* expression missing — ignore */ }
        }
        if (this._emphasisActive > 0) {
            this._emphasisActive = Math.max(
                0, this._emphasisActive - this._emphasisDecay * delta,
            );
            try {
                vrm.expressionManager?.setValue?.('surprised', this._emphasisActive);
            } catch { /* ignore */ }
        }
    }

    // ── Internals ────────────────────────────────────────────────

    _ensureBones(vrm) {
        if (this._bones && this._bones.head) return;
        const h = vrm.humanoid;
        const get = (name) => (
            h.getNormalizedBoneNode?.(name) ?? h.getBoneNode?.(name) ?? null
        );
        this._bones = {
            head:  get('head'),
            chest: get('chest') ?? get('upperChest') ?? get('spine'),
        };
    }

    _captureBasePoses() {
        if (!this._bones?.head) return;
        this._baseQuats = {};
        for (const [name, bone] of Object.entries(this._bones)) {
            if (bone) this._baseQuats[name] = bone.quaternion.clone();
        }
    }

    /**
     * Write `base[name] * deltaQ(pitch, yaw, roll)` to the named
     * bone. SET, not multiply, so no accumulation across frames.
     */
    _writeBoneFromBase(name, pitch, yaw, roll) {
        const bone = this._bones?.[name];
        const base = this._baseQuats?.[name];
        if (!bone || !base) return;
        if (Math.abs(pitch) < 1e-5 && Math.abs(yaw) < 1e-5 && Math.abs(roll) < 1e-5) {
            // Restore base when delta is essentially zero — happens
            // during fade-out so the bone returns cleanly to its
            // starting pose before we release it.
            bone.quaternion.copy(base);
            return;
        }
        this._eu.set(pitch, yaw, roll, 'YXZ');
        this._dq.setFromEuler(this._eu);
        // bone = base * delta
        bone.quaternion.copy(base).multiply(this._dq);
    }

    _scheduleNextEmphasis(elapsed) {
        const span = EMPHASIS_MAX_INTERVAL - EMPHASIS_MIN_INTERVAL;
        this._nextEmphasisAt = elapsed + EMPHASIS_MIN_INTERVAL + Math.random() * span;
    }
}

