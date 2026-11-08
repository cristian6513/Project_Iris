/**
 * IRIS MicroLife — Tier 1 "presence" pass
 * ===========================================================
 *
 * Drives the avatar's gaze, blinks, and breathing with the small
 * irregularities that separate "AI character" from "alguien". See the
 * design discussion that motivated this module — Tier 1 was specced
 * as: look-at smoothing → blinking irregular → saccades → eyes-before
 * -head → breathing, with the cardinal rule "controla amplitudes,
 * microvariación constante".
 *
 * Architecture
 * ------------
 * Single class. Each frame the app calls `update(dt, t)`. Internally we
 * run a small state machine:
 *
 *     idle  ──speech-start──>  speaking
 *     idle  ──setEmotion('pensativa')──>  thinking
 *     speaking ──speech-end──>  idle
 *     thinking ──setEmotion(*not pensativa*)──>  idle
 *
 * Per state we adjust amplitudes for everything (saccade rate, blink
 * rate, breathing depth) without changing the underlying mechanism.
 * The mechanisms themselves are state-agnostic so the only thing
 * tuning is data, not control flow.
 *
 * Gaze cascade (this is what produces "eyes lead the head")
 * ---------------------------------------------------------
 *
 *   focusTarget  →  eyeTarget  →  headTarget
 *   (camera +     (smoothed     (smoothed
 *    saccade)     ~150ms)       ~250ms)
 *
 * - `focusTarget` is the canonical "what IRIS wants to look at" world
 *   point. Updated instantly whenever the saccade target changes.
 * - `eyeTarget` damps toward `focusTarget` quickly. Drives the VRM's
 *   built-in look-at (which rotates the eyeballs themselves — the SDK
 *   handles all the eye-bone math, we just move a dummy Object3D).
 * - `headTarget` damps toward `eyeTarget`, NOT `focusTarget`. That one
 *   extra link is what gives "eyes get there first". Drives the IK
 *   solver's `lookAtWorld`, which rotates the head + neck bones.
 *
 * Why critically-damped lerp
 * --------------------------
 * `THREE.MathUtils.damp(current, target, lambda, dt)` is a critically
 * damped exponential approach. No overshoot, framerate-independent,
 * cheap. `lambda = ln(2) / halfLife`, so for a settling time of ~150ms
 * we use lambda ≈ 7, and for ~250ms we use lambda ≈ 4. The user-facing
 * tunables expose half-life directly because that's the number the
 * docs talk about; we convert internally.
 *
 * Blinking
 * --------
 * - Inter-blink interval: drawn from an exponential distribution
 *   (`-mean * ln(1 - random())`), so the time between blinks is
 *   Poisson — irregular by construction, no `setInterval` ever.
 * - Mean varies by state: idle 4.5s, speaking 6.0s, thinking 5.5s.
 *   These match human resting / focused / pensive rates.
 * - Each blink is a small 3-phase ramp: close 80ms → hold 50ms → open
 *   120ms. We integrate every frame instead of using `setTimeout`,
 *   which would lose precision under load.
 * - Left and right lids run independently with a 15-35ms offset. Very
 *   subtle but it kills the "mechanical sync" tell.
 * - 10% chance of a follow-up blink ~140ms after the first ("double
 *   blink"). Above 12% it stops looking like life and starts looking
 *   like a tic.
 * - Thinking state slightly raises the BASELINE lid closure (~0.15)
 *   between blinks — the "narrowed eyes while pondering" tell.
 *
 * Breathing
 * ---------
 * Tiny sine wave on the root group's Y position (~1 cm) plus an even
 * smaller chest-bone rotation if the VRM exposes one. Frequency and
 * amplitude are state-modulated; the wave phase is preserved across
 * state changes so we don't get a visible "hiccup" when emotions flip.
 *
 * Public surface
 * --------------
 *   const ml = new MicroLife({ avatarManager, camera });
 *   ml.setEmotion('pensativa');     // adjusts state machine
 *   ml.setSpeaking(true);           // wired from IRIS:speech-start
 *   ml.update(dt, t);               // called every frame in app.js
 *   ml.attachDebugHUD();            // adds an in-page panel of sliders
 *   ml.destroy();                   // detach listeners, clean up DOM
 */

// Resolve through the page import map so every visual subsystem shares the
// same Three.js instance and revision. Mixing CDN revisions breaks instanceof
// checks and produces duplicate-engine warnings at runtime.
import * as THREE from 'three';

const { MathUtils } = THREE;

// ─── Tunables ─────────────────────────────────────────────────────
//
// Conservative defaults per the "controla amplitudes" principle. The
// debug HUD edits this object live — anything you put here will show
// up as a slider.
//
// Convention: time constants in SECONDS, angles in RADIANS, position
// offsets in METERS (three.js world units, which for IRIS is 1 m).
export const DEFAULT_TUNABLES = {
    // Gaze cascade
    eyeHalfLife:      0.10,   // damping half-life for eye target
    headHalfLife:     0.18,   // damping half-life for head target
    // Saccade behavior in IDLE state
    saccadeMeanGap:   2.2,    // seconds between saccades (Poisson mean)
    saccadeMinGap:    0.6,    // never less than this
    saccadeMaxAngle:  0.052,  // soft idle micro-saccade
    saccadeRefocus:   0.11,   // larger "look back at user" movement
    saccadeRefocusP:  0.08,   // uncommon, so the gaze does not wander
    saccadeRefocusCenterP: 0.72,
    saccadeConeX:     0.115,  // keep gaze inside a believable frontal cone
    saccadeConeY:     0.075,
    saccadeBlinkGuard: 0.13,  // no new blink while a saccade is settling
    directSaccadeScale: 0.32,
    // Saccade behavior in SPEAKING state. The user spec was "eyes fix
    // on the user while talking"; keep micro-drift but make it nearly
    // invisible (~0.7°) and very rare so the gaze locks on the camera.
    saccadeMeanGapSpeaking: 8.0,
    saccadeMaxAngleSpeaking: 0.007,
    // Saccade behavior in THINKING state (looks up/away more often)
    saccadeMeanGapThinking: 2.4,
    saccadeMaxAngleThinking: 0.072,
    saccadeThinkingBias:    new THREE.Vector2(-0.052, 0.035), // gentle up-left
    thoughtfulLookawayX:   -0.078,
    thoughtfulLookawayY:    0.046,
    // Blinking
    blinkMeanGapIdle:     4.5,
    blinkMeanGapSpeaking: 6.0,
    blinkMeanGapThinking: 5.5,
    blinkMinGap:          1.2,
    blinkCloseTime:       0.080,  // 80ms close
    blinkHoldTime:        0.050,  // 50ms held closed
    blinkOpenTime:        0.120,  // 120ms reopen
    // Thinking-state blinks are slower AND deeper — the lid lingers
    // closed about 2× longer and the closure/release are more relaxed.
    // Multiplied INTO close/hold/open above when `state === 'thinking'`.
    blinkThinkingScale:   1.6,
    blinkAsymMin:         0.020,  // floor: at least 20ms offset (never 0)
    blinkAsymMax:         0.040,  // up to 40ms offset between L and R
    doubleBlinkChance:    0.075,
    doubleBlinkDelay:     0.14,
    thinkingLidBaseline:  0.15,
    // Breathing
    breathAmpIdle:      0.010,
    breathAmpHappy:     0.011,
    breathAmpSpeaking:  0.008,
    breathAmpThinking:  0.014,
    breathFreqIdle:     0.25,    // Hz — ~15 breaths/min
    breathFreqHappy:    0.32,
    breathFreqSpeaking: 0.30,
    breathFreqThinking: 0.19,
    // Master kill switches (debug)
    enableGaze:      true,
    enableBlink:     true,
    enableBreathing: true,
    // Head follow now uses direct writes to the normalized head/neck
    // bones (see `_applyHeadFollow`) instead of the IK solver. The IK
    // path has a known bug where `_aimBoneAt` assumes head-local +Z
    // is forward, which breaks once the avatar group has its 180°
    // restYaw applied. Direct writes compute yaw/pitch in world space
    // relative to the avatar's current facing direction and write a
    // small delta on the bone — rest pose of the normalized bone IS
    // identity, so this is safe across VRM rigs.
    // Default OFF — the user observed the head consistently biased
    // toward the camera direction (which was offset, making the head
    // look perpetually turned-left). The eye lookAt via VRM SDK
    // already provides the "she's looking at you" effect cleanly via
    // the eyebones alone; the head-follow layer on top fights with
    // VRMA clip animations and reads as a constant pose correction.
    // Re-enable via HUD toggle if you want to A/B test.
    enableHeadFollow:    false,
    headFollowWeight:    0.55,    // 0..1 — how much of full aim to apply
    headFollowYawMax:    0.35,    // ~20° head yaw cap (per side)
    headFollowPitchMax:  0.22,    // ~12.5° head pitch cap
    neckFollowRatio:     0.40,    // 40% of the rotation goes to neck

    // Micro head noise — the small involuntary jitter every human head
    // does because of vestibular feedback + breathing + heartbeat.
    // Without it the head reads as "perfectly fixed on a stick" even
    // when the rest of the avatar is moving.
    enableHeadNoise:   true,
    headNoiseYawAmp:   0.006,   // tiny, non-nervous presence
    headNoisePitchAmp: 0.005,
    headNoiseRollAmp:  0.0035,
    headNoiseFreq:     0.36,    // cycles per second of the slow drift

    // Body sway — slow Perlin on the spine + hips so the whole body
    // doesn't read as a statue while idle. Humans shift weight every
    // ~5-8 seconds; we run the noise much slower than the head's so
    // the two systems don't beat against each other rhythmically.
    enableBodySway:    true,
    bodySwayYawAmp:    0.007,
    bodySwayPitchAmp:  0.0035,
    bodySwayRollAmp:   0.005,
    bodySwayFreq:      0.105,

    // Speaking pauses — small involuntary motions during the gaps in
    // speech. Detecting "the avatar paused" is approximated by watching
    // the mouth openness drop sustained-low while in the speaking
    // state. When that triggers, one of several short reactions fires.
    enableSpeakingPauses: true,
    pauseMouthThreshold:  0.06,   // openness below this counts as quiet
    pauseDwellTime:       0.32,   // need this many seconds of quiet
    pauseCooldown:        1.5,    // after a reaction, don't fire again
    pauseSaccadeP:        0.22,   // probability of a small look-away
    pauseBrowP:           0.18,   // probability of a brief brow raise
    pauseNodP:            0.10,   // rare, tiny "I'm with you" nod
    pauseLookAwayAngle:   0.06,   // ~3.5° drift to the side
    pauseNodAmp:          0.020,
    pauseNodDuration:     0.56,

    // ── Tier 3 ────────────────────────────────────────────────────

    // Weight shifting — periodic commit to "weight on left foot"
    // vs "weight on right foot" vs "centered". Unlike body sway
    // (continuous Perlin), this is a state machine that picks a side
    // and HOLDS it for 6-12 seconds, then transitions to a different
    // one. Realised as a hip-roll + counter-spine so the shoulders
    // stay level — looks like a real weight shift without needing IK
    // on the legs.
    enableWeightShift:     true,
    weightShiftAmp:        0.020,
    weightShiftHoldMin:    8.0,     // min seconds at one side
    weightShiftHoldMax:    15.0,    // max seconds at one side
    weightShiftRiseTime:   1.25,    // half-life for committing to new side
    weightShiftCounter:    0.7,     // 70% of hip roll cancelled at spine
    activeAnimationPresenceScale: 0.22,
    presenceMoodScales: {
        neutral:  { breathAmp: 1.00, breathFreq: 1.00, head: 0.72, sway: 0.62, weight: 0.62, pose: 1.00 },
        listening:{ breathAmp: 0.95, breathFreq: 0.95, head: 0.52, sway: 0.45, weight: 0.45, pose: 0.75 },
        happy:    { breathAmp: 1.05, breathFreq: 1.10, head: 0.92, sway: 0.82, weight: 0.78, pose: 1.00 },
        thinking: { breathAmp: 1.18, breathFreq: 0.82, head: 0.48, sway: 0.42, weight: 0.35, pose: 0.85 },
        speaking: { breathAmp: 0.82, breathFreq: 1.00, head: 0.22, sway: 0.24, weight: 0.08, pose: 0.00 },
    },

    // Procedural posing — small persistent rotations on chest +
    // shoulders driven by the current emotion. Feliz lifts the chest
    // and shoulders back; triste slumps them; pensativa is mostly
    // neutral. This is what gives "she's HAPPY" not just "her face is
    // happy" — the body language layer.
    enableProceduralPose:  true,
    poseTransitionTime:    1.8,     // half-life for emotion → pose

    // Lip idle complex — micro-movements of lips during silence.
    // Fires only in 'idle' state with mouth closed. Uses VRM visemes
    // OTHER than `aa` (which lipsync owns) so it doesn't fight TTS.
    enableLipIdle:         true,
    lipIdleMinGap:         8.0,     // min seconds between flickers
    lipIdleMaxGap:         18.0,    // max
    lipIdleDuration:       0.45,    // each flicker lasts ~450ms
    lipIdleAmp:            0.18,    // peak viseme weight — subtle

    // Contextual gestures — automatic micro-reactions that fire when
    // emotion or state changes. NOT the same as `playAnimation` (those
    // are LLM-driven full clips); these are tiny accents.
    enableContextualGestures: true,
    gestureChestAmp:       0.05,    // chest bump on emotion ramp-up
    gestureShoulderAmp:    0.03,    // shoulder lift on surprise/feliz
    // Min seconds between contextual gesture fires. Without this,
    // an LLM that flips emotions rapidly (feliz → triste → feliz →
    // sorprendida turn after turn) would stack impulses to peak
    // amplitude on every transition — reads as twitchy. The
    // cooldown lets a previous impulse decay before a new one lands.
    gestureMinInterval:    0.9,
};


// ─── Helpers ──────────────────────────────────────────────────────

/**
 * 1-D value-noise sampler. Cheap "Perlin-ish" output: smooth, bounded,
 * and the same input always returns the same value. We don't need
 * gradient noise quality for body micro-motion — just continuous,
 * pseudo-random oscillation that doesn't repeat visibly.
 *
 * Internally: interpolate between hash-derived random values at
 * integer steps with a smoothstep curve, then sum two octaves so the
 * output has both a slow drift and a faster wobble.
 */
function _hash(x) {
    // Mulberry32-ish 32-bit hash. Deterministic, no allocations.
    let h = (Math.floor(x) | 0) * 0x9E3779B1;
    h = ((h ^ (h >>> 16)) * 0x85EBCA6B) | 0;
    h = ((h ^ (h >>> 13)) * 0xC2B2AE35) | 0;
    h ^= h >>> 16;
    // Map to [-1, 1].
    return ((h & 0xFFFFFFFF) / 0x80000000) - 1;
}
function smoothNoise1D(x) {
    const i = Math.floor(x);
    const f = x - i;
    const s = f * f * (3 - 2 * f);   // smoothstep
    const a = _hash(i);
    const b = _hash(i + 1);
    return a + (b - a) * s;
}
function fractalNoise1D(x) {
    // Two octaves — first dominates (slow drift), second adds wobble.
    return smoothNoise1D(x) * 0.75 + smoothNoise1D(x * 2.7 + 13.1) * 0.25;
}


function poissonSample(mean) {
    // Inverse-CDF for exponential distribution. Math.random() can
    // return 0 (extremely rare) — clamp to avoid log(0) = -Infinity.
    const u = Math.max(1e-9, 1 - Math.random());
    return -mean * Math.log(u);
}

function halfLifeToLambda(halfLife) {
    // damp() uses ln(2)/halfLife. Math.LN2 ≈ 0.693.
    return halfLife > 1e-6 ? Math.LN2 / halfLife : 1000;
}

// VRM expression names vary by file. We try multiple known names in
// order until one sticks. Pixiv's VRM 1.0 schema standardizes on
// lowercase `blink`, `blinkLeft`, `blinkRight`; older 0.x files often
// use `Blink_L` / `Blink_R` or capitalized `Blink`.
const BLINK_L_CANDIDATES = ['blinkLeft', 'blink_l', 'blink_L', 'Blink_L'];
const BLINK_R_CANDIDATES = ['blinkRight', 'blink_r', 'blink_R', 'Blink_R'];
const BLINK_SINGLE       = ['blink', 'Blink'];
// Poses whose eyes stay on what she is doing, in degrees of pitch, instead of finding the camera.
const HELD_GAZE = Object.freeze({ down: -16, screen: -4 });
const DIRECT_FOCUS_EMOTIONS = new Set(['feliz', 'triste', 'sorprendida', 'enojada']);


// ─── MicroLife ────────────────────────────────────────────────────

export class MicroLife {
    /**
     * @param {object} opts
     * @param {AvatarManager} opts.avatarManager
     * @param {THREE.Camera}  opts.camera
     * @param {object}        [opts.tunables]   merged over DEFAULT_TUNABLES
     */
    constructor({ avatarManager, camera, tunables } = {}) {
        this.avatarManager = avatarManager;
        this.camera        = camera;
        this.t = Object.assign({}, DEFAULT_TUNABLES, tunables || {});

        // State machine
        this.state = 'idle';   // 'idle' | 'speaking' | 'thinking'
        this.emotion = 'neutral';

        // Gaze cascade — all world-space Vector3
        this._focusTarget = new THREE.Vector3();
        this._eyeTarget   = new THREE.Vector3();
        this._headTarget  = new THREE.Vector3();
        this._initializedTargets = false;

        // Current saccade offset (added to camera position). Persists
        // between saccades — the gaze drifts to a new offset and stays
        // there until the next saccade picks a new one.
        this._saccadeOffset = new THREE.Vector2(0, 0);
        this._saccadeOffsetTarget = new THREE.Vector2(0, 0);
        this._timeToNextSaccade = 0.5;  // first one fires after 0.5s
        this._saccadeInFlight = false;
        this._saccadeSettleTimer = 0;
        this._stableEyeContactUntil = 0;

        // Dummy object that VRM look-at follows. We position it at
        // _eyeTarget every frame; the VRM SDK does the rest.
        this._lookAtDummy = new THREE.Object3D();
        this._lookAtDummy.name = 'IRISMicroLife_LookAtTarget';
        this._lookAtAttached = false;

        // Blink state — left and right run independently
        this._blinkL = { value: 0, phase: 'wait', t: 0, gap: 1.2, scheduledDouble: false };
        this._blinkR = { value: 0, phase: 'wait', t: 0, gap: 1.2, scheduledDouble: false };
        this._blinkExpressionNames = null;  // resolved lazily on first frame
        this._timeToNextBlink = 0.8;
        this._blinkAsym = 0;                // current L vs R offset in seconds
        this._blinkSuppressedByAnimation = false;

        // Breathing
        this._breathPhase = 0;     // accumulates over time
        this._lastBreathOffset = 0;  // initialized so the re-baseline
                                     // check in _updateBreathing can't
                                     // arithmetic against `undefined`
                                     // on frame 2.
        this._breathingSuppressedByAnimation = false;

        // Smoothed head-follow rotation. Kept on the instance (not the
        // bone) because the bone's rotation is overwritten by the clip
        // animation each frame — if we damped against the bone we'd
        // damp away from our target on every clip pass and never get
        // there. State here gives us a continuous low-pass on the
        // gaze-derived target, applied as a quaternion delta in
        // `applyPost` after the clip has written.
        this._smoothedHeadYaw   = 0;
        this._smoothedHeadPitch = 0;
        this._smoothedNeckYaw   = 0;
        this._smoothedNeckPitch = 0;
        this._lastDt = 1 / 60;

        // Captured "base" quaternions for the bones MicroLife writes
        // to. Why: `bone.quaternion.multiply(delta)` accumulates if
        // nothing else writes to the bone between our frames. In
        // theory the clip animation (mixer) overwrites bones each
        // frame; in PRACTICE most VRMs are in idle pose with no clip
        // targeting head/spine, so the bone is left at MicroLife's
        // last multiplied value and we wind into the clamp limit
        // after a few seconds. Capturing the base ONCE on first
        // touch (or after avatar swap) and using
        // `bone.quaternion.copy(base).multiply(delta)` makes the
        // write idempotent — drift impossible by construction.
        //
        // Stored per-bone-name. On avatar swap the swap code clears
        // this map so we re-capture for the new rig.
        this._baseQuats = new Map();

        // Speaking-pause detection state. We watch mouth openness
        // (LipSync writes this on the avatar) and trigger micro-
        // reactions when it stays low for a moment mid-speech.
        this._quietSince     = 0;     // seconds of sustained silence
        this._lastPauseAt    = -10;   // absolute postTime of last reaction
        this._pauseBrowBoost = 0;     // active brow-raise value (decays)
        this._speechNod = { active: false, elapsed: 0, duration: 0, amp: 0 };
        this._presenceAnimationScale = 1;

        // Weight shifting state. Side is -1 (weight on left foot),
        // 0 (centered), or +1 (right foot). `_weightSwitchAt` is the
        // absolute postTime when we'll next pick a new side.
        // `_weightCurrent` is the smoothed value driving the bones.
        this._weightSide      = 0;
        this._weightCurrent   = 0;
        this._weightSwitchAt  = 0;    // 0 sentinel triggers init on first call

        // Procedural posing state — `_poseCurrent` interpolates toward
        // the target pose for the active emotion. Stored as a small
        // dict so we can damp each channel independently.
        this._poseCurrent = { chestPitch: 0, shoulderLift: 0 };

        // Lip idle state.
        this._lipIdleTimer    = 6 + Math.random() * 4;   // first one in 6-10s
        this._lipIdleActive   = null;

        // Contextual gesture state.
        this._gestureChest    = 0;     // damps back to 0 after a hit
        this._gestureShoulder = 0;
        this._lastEmotion     = 'neutral';
        this._lastGestureAt   = -10;   // postTime of last fire (cooldown)

        // Scratch Vector3s reused across `_updateGaze` and
        // `_accumHeadFollow` to avoid 4-5 fresh allocations per
        // frame. At 60fps that's ~300 objects/sec dodged — modest
        // CPU savings + meaningful reduction in GC pauses on long
        // sessions.
        this._scratchRight     = new THREE.Vector3();
        this._scratchUp        = new THREE.Vector3();
        this._scratchHeadWorld = new THREE.Vector3();
        this._scratchLocalDir  = new THREE.Vector3();

        // For state-change "speech-start" detection — keep a flag so
        // external code that already dispatches events on the window
        // can call setSpeaking() without us double-tracking.
        this._speaking = false;
        this._listening = false;

        // Listeners (so destroy() can detach cleanly)
        this._wiredListeners = [];

        // Debug HUD elements (only allocated if attachDebugHUD called)
        this._hud = null;
    }

    // ── Public state controls ─────────────────────────────────────

    setEmotion(name) {
        const next = name || 'neutral';
        // Detect a real change (not a redundant re-emit of the same
        // emotion) so contextual gestures only fire once per
        // transition. The LLM often emits the same emotion several
        // turns in a row.
        if (next !== this._lastEmotion && this.t.enableContextualGestures) {
            this._fireContextualGesture(this._lastEmotion, next);
        }
        this._lastEmotion = next;
        this.emotion = next;
        this._refreshState();
    }

    /**
     * Small reflexive micro-motion on the chest / shoulders that
     * fires the instant an emotion changes. Lasts ~0.5s and decays
     * naturally — it's not a sustained pose (that's procedural
     * posing's job), just an "exhale" / "intake of breath" punctuation
     * mark on the transition.
     */
    _fireContextualGesture(from, to) {
        // Cooldown: prevent rapid emotion churn from stacking impulses
        // at peak amplitude (~4.6° transient chest + ~4.3° shoulders)
        // every transition. Wait for the previous impulse to decay at
        // least a half-life before firing the next one.
        const now = this._postTime || 0;
        if (now - this._lastGestureAt < this.t.gestureMinInterval) return;
        this._lastGestureAt = now;

        let chest = 0, shoulder = 0;

        if (to === 'feliz' || to === 'sorprendida') {
            chest    =  this.t.gestureChestAmp;       // chest expands
            shoulder =  this.t.gestureShoulderAmp;    // shoulders lift
        } else if (to === 'triste') {
            chest    = -this.t.gestureChestAmp * 0.7; // sigh / collapse
            shoulder = -this.t.gestureShoulderAmp * 0.6;
        } else if (to === 'enojada') {
            chest    =  this.t.gestureChestAmp * 0.6;
            shoulder =  this.t.gestureShoulderAmp * 1.2; // tense up
        } else if (to === 'neutral') {
            // Releasing back to baseline — the reflex is roughly the
            // INVERSE of the previous emotion's punctuation, scaled
            // down (a release is softer than the original commitment).
            // Earlier this branch tried to compute via `to`, which is
            // always 'neutral' here, so it always evaluated to 0 — a
            // silent dead-code path the audit caught.
            const RELEASE = {
                feliz:       { c:  this.t.gestureChestAmp    * 0.25,
                               s: -this.t.gestureShoulderAmp * 0.25 },
                sorprendida: { c:  this.t.gestureChestAmp    * 0.30,
                               s: -this.t.gestureShoulderAmp * 0.30 },
                triste:      { c: -this.t.gestureChestAmp    * 0.20,
                               s:  this.t.gestureShoulderAmp * 0.20 },
                enojada:     { c: -this.t.gestureChestAmp    * 0.25,
                               s: -this.t.gestureShoulderAmp * 0.35 },
                pensativa:   { c:  0, s: 0 },
            };
            const r = RELEASE[from] || { c: 0, s: 0 };
            chest = r.c; shoulder = r.s;
        }

        this._gestureChest    = chest;
        this._gestureShoulder = shoulder;
    }

    setSpeaking(speaking) {
        const wasSpeaking = !!this._speaking;
        this._speaking = !!speaking;
        // SpeechAnimator continues writing chest/upperChest for ~0.5s
        // after speech_end while its fade-out envelope decays. If we
        // re-enable MicroLife's procedural pose the instant speech_end
        // fires, both writers collide again for the duration of the
        // fade. Stamp a "post-speech grace window" timestamp so the
        // applyPost gate can keep yielding chest writes until the
        // fade is complete. Half-life ~0.6s mirrors SpeechAnimator's
        // 0.5s fade-out plus a safety margin.
        if (wasSpeaking && !this._speaking) {
            this._postSpeechGraceUntil = (performance.now() / 1000) + 0.6;
        }
        this._refreshState();
    }

    setListening(listening) {
        this._listening = !!listening;
        this._refreshState();
    }

    /** Internal: derive `state` from `emotion` + `_speaking`. Speaking
     *  trumps thinking (you keep eye contact while talking) which trumps
     *  idle. */
    _refreshState() {
        const prevState = this.state;
        if (this._speaking) {
            this.state = 'speaking';
        } else if (this.emotion === 'pensativa') {
            this.state = 'thinking';
        } else if (this._listening) {
            this.state = 'listening';
        } else {
            this.state = 'idle';
        }
        if (this.state !== prevState) {
            if (this.state === 'thinking') {
                this._triggerThoughtfulLookaway();
            } else if (this.state === 'speaking') {
                this._stabilizeEyeContact(1.4);
            }
        }
        if (DIRECT_FOCUS_EMOTIONS.has(this.emotion)) {
            this._stabilizeEyeContact(1.2);
        }
        // Soft nudge: if the next saccade is more than ~1.5s away,
        // pull it in a bit so the new state's cadence becomes visible
        // soon. Don't snap to a hard floor (e.g. 0.4s) — rapid emotion
        // changes used to cascade into a twitch-burst of saccades
        // every time the LLM flipped emotions in successive turns.
        if (this._timeToNextSaccade > 1.5) {
            this._timeToNextSaccade = 0.8 + Math.random() * 0.6;
        }
    }

    _presenceNow() {
        return this._postTime || 0;
    }

    _stabilizeEyeContact(seconds = 1.2) {
        const until = this._presenceNow() + seconds;
        this._stableEyeContactUntil = Math.max(this._stableEyeContactUntil || 0, until);
        this._saccadeOffsetTarget.multiplyScalar(0.35);
        this._timeToNextSaccade = Math.max(this._timeToNextSaccade, 0.9);
    }

    _triggerThoughtfulLookaway() {
        const jitterX = (Math.random() - 0.5) * 0.018;
        const jitterY = (Math.random() - 0.5) * 0.012;
        this._setSaccadeTarget(
            this.t.thoughtfulLookawayX + jitterX,
            this.t.thoughtfulLookawayY + jitterY,
            true
        );
        this._timeToNextSaccade = 1.2 + Math.random() * 0.9;
    }

    /**
     * Clear every piece of state that references the OLD rig (cached
     * bone bases, smoothed bone angles, mid-flight animation phases,
     * etc) so a freshly-loaded VRM starts cleanly. Call from BOTH
     * drag-drop swap and identity-change swap in app.js — earlier each
     * site had its own ad-hoc reset block and they diverged: drag-drop
     * cleared 14 fields, identity-change cleared 5. New bones inherited
     * the old rig's smoothed angles and the captured-base map, which
     * produced a 1-frame visual snap and could leave the brow-boost
     * "stuck" on the new face.
     */
    resetForRigSwap() {
        // Re-resolve against the new VRM next frame
        this._blinkExpressionNames = null;
        this._lookAtAttached       = false;
        this._lookAtDummy?.parent?.remove(this._lookAtDummy);
        this._initializedTargets   = false;

        // Breathing baseline depends on the new group's Y position.
        this._breathBaseY          = undefined;
        this._lastBreathOffset     = 0;
        this._breathPhase          = 0;
        this._breathingSuppressedByAnimation = false;

        // Blink lid state machines — a blink mid-flight on the old VRM
        // would otherwise resume mid-phase on the new one.
        this._blinkL = { value: 0, phase: 'wait', t: 0, gap: 1.2, scheduledDouble: false };
        this._blinkR = { value: 0, phase: 'wait', t: 0, gap: 1.2, scheduledDouble: false };
        this._timeToNextBlink = 0.8;
        this._blinkSuppressedByAnimation = false;

        // Saccade scheduler
        this._timeToNextSaccade    = 0.5;
        this._saccadeOffset.set(0, 0);
        this._saccadeOffsetTarget.set(0, 0);
        this._snapSaccade          = false;
        this._saccadeInFlight      = false;
        this._saccadeSettleTimer   = 0;
        this._stableEyeContactUntil = 0;

        // Captured rest quaternions on the old rig are meaningless on
        // the new one.
        this._baseQuats?.clear();
        this._frameDeltas?.clear();

        // Smoothed head-follow angles
        this._smoothedHeadYaw   = 0;
        this._smoothedHeadPitch = 0;
        this._smoothedNeckYaw   = 0;
        this._smoothedNeckPitch = 0;

        // Tier 2 — speaking pause
        this._quietSince     = 0;
        this._lastPauseAt    = -10;
        this._pauseBrowBoost = 0;
        this._speechNod      = { active: false, elapsed: 0, duration: 0, amp: 0 };
        this._presenceAnimationScale = 1;
        this._listening      = false;

        // Tier 3 — weight shift, procedural pose, lip idle, gestures
        this._weightSide      = 0;
        this._weightCurrent   = 0;
        this._weightSwitchAt  = 0;
        this._poseCurrent     = { chestPitch: 0, shoulderLift: 0 };
        this._lipIdleTimer    = 6 + Math.random() * 4;
        this._lipIdleActive   = null;
        this._gestureChest    = 0;
        this._gestureShoulder = 0;
        this._lastEmotion     = this.emotion || 'neutral';
        this._lastGestureAt   = -10;
    }

    /** Convenience: wire window-level speech events automatically. */
    wireSpeechEvents() {
        const onStart = () => this.setSpeaking(true);
        const onEnd   = () => this.setSpeaking(false);
        window.addEventListener('IRIS:speech-start', onStart);
        window.addEventListener('IRIS:speech-end',   onEnd);
        this._wiredListeners.push(['IRIS:speech-start', onStart]);
        this._wiredListeners.push(['IRIS:speech-end',   onEnd]);
    }

    // ── Main update ───────────────────────────────────────────────

    update(dt, _t) {
        const avatar = this.avatarManager?.avatar;
        if (!avatar) return;
        const animationOwnsFace = Boolean(avatar.animationOwnsBlink);
        const animationOwnsBody = Boolean(avatar.animationOwnsRootMotion);

        // We support both VRMAvatar and (very gracefully) procedural —
        // if there's no `vrm` field we just drive what we can.
        const vrm = avatar.vrm || null;

        // Lazy: attach the VRM look-at dummy to the scene the first
        // time we have a target. The SDK needs a stable Object3D; we
        // use one we own so nothing else can move it under us.
        if (vrm && !this._lookAtAttached) {
            this._attachLookAt(vrm);
        }

        // Initialize the gaze targets the first frame so we don't
        // damp from (0,0,0) — that produces a "stares at floor then
        // snaps up" jump.
        if (!this._initializedTargets && this.camera) {
            this._focusTarget.copy(this.camera.position);
            this._eyeTarget.copy(this.camera.position);
            this._headTarget.copy(this.camera.position);
            this._initializedTargets = true;
        }

        if (this.t.enableGaze) this._updateGaze(dt);
        if (this.t.enableBlink) {
            if (animationOwnsFace) this._suspendBlinksForAnimation();
            else {
                this._resumeBlinksAfterAnimation();
                this._updateBlinks(dt);
            }
        }
        if (this.t.enableBreathing) {
            if (animationOwnsBody) this._suspendBreathingForAnimation();
            else {
                this._resumeBreathingAfterAnimation();
                this._updateBreathing(dt);
            }
        }
        if (animationOwnsFace) {
            // Authored clips own their expression/bone beat. Keep only real
            // speech lip-sync (a separate system), not MicroLife accents.
            this._quietSince = 0;
            this._pauseBrowBoost = 0;
            this._speechNod.active = false;
            const em = avatar.vrm?.expressionManager;
            if (this._lipIdleActive) {
                try { em?.setValue(this._lipIdleActive.viseme, 0); } catch {}
                this._lipIdleActive = null;
            }
        } else {
            if (this.t.enableSpeakingPauses) this._updateSpeakingPauses(dt);
            if (this.t.enableLipIdle) this._updateLipIdle(dt);
        }

        if (this._hud) this._refreshHUDValues();
    }

    /**
     * Detect short mid-speech silences and react with subtle micro-
     * motions. Without this, IRIS sounds like she's reading off a
     * teleprompter: continuous yapping with zero gaps for her own
     * thought to land. Real conversation has these tiny "uhh, let me
     * think" beats where the eyes drift and the brows lift just a
     * touch — that's what we emulate.
     *
     * Signal source: the avatar's `mouthOpenness` field (LipSync writes
     * it from the audio analyser). Below `pauseMouthThreshold` for
     * `pauseDwellTime` seconds, while in the `speaking` state, fires
     * one reaction, then a cooldown begins so we don't stack them.
     */
    _updateSpeakingPauses(dt) {
        // Only relevant while she's actually mid-sentence.
        if (this.state !== 'speaking') {
            this._quietSince = 0;
            this._pauseBrowBoost = MathUtils.damp(this._pauseBrowBoost, 0, 4, dt);
            return;
        }
        const openness = this.avatarManager?.avatar?.mouthOpenness ?? 0;
        if (openness < this.t.pauseMouthThreshold) {
            this._quietSince += dt;
        } else {
            this._quietSince = 0;
        }

        const now = this._postTime || 0;
        const cooled = (now - this._lastPauseAt) > this.t.pauseCooldown;
        if (this._quietSince > this.t.pauseDwellTime && cooled) {
            this._fireSpeakingPause();
            this._lastPauseAt = now;
            this._quietSince = 0;
        }

        // Brow boost from a previous pause decays toward 0. The
        // ACTUAL write to the expression manager happens in
        // `applyPost` — if we wrote here, the avatar's own
        // `_updateExpressions` (which lerps `currentExpressions` toward
        // `targetExpressions`) runs AFTER us inside `vrm.update`/
        // `avatarManager.update`, and overwrites the brow boost back
        // toward the emotion preset's `surprised` value (usually 0).
        // Net result: invisible flicker. Writing in `applyPost` lands
        // AFTER all of that and survives.
        this._pauseBrowBoost = MathUtils.damp(this._pauseBrowBoost, 0, 2, dt);
    }

    _fireSpeakingPause() {
        const roll = Math.random();
        if (roll < this.t.pauseSaccadeP) {
            // Look-away saccade: nudge the eye target slightly off
            // the user, ballistic (instant snap). The natural Poisson
            // saccade clock will bring it back to center within a
            // second or two.
            const theta = (Math.random() < 0.5 ? -1 : 1) *
                          (0.55 + Math.random() * 0.45); // ~0.55..1.0 (mostly sideways)
            const r = this.t.pauseLookAwayAngle;
            this._setSaccadeTarget(Math.cos(theta) * r, Math.sin(theta) * r * 0.35, true);
            // Schedule the return saccade to come a bit sooner than
            // normal so the look-away doesn't last too long.
            this._timeToNextSaccade = 0.9 + Math.random() * 0.6;
        } else if (roll < this.t.pauseSaccadeP + this.t.pauseBrowP) {
            // Brow flicker — a brief lift, then natural decay.
            this._pauseBrowBoost = 0.18 + Math.random() * 0.12;
        } else if (roll < this.t.pauseSaccadeP + this.t.pauseBrowP + this.t.pauseNodP) {
            this._startSpeakingNod();
        }
        // Remaining probability mass = "no visible reaction" → the
        // pause itself reads as natural silence. Important: NOT every
        // pause should produce motion, or you end up with a twitchy
        // avatar that looks anxious.
    }

    _startSpeakingNod() {
        if (this.state !== 'speaking') return;
        const idle = (window.iris || window.IRIS)?.idle;
        const avatar = this.avatarManager?.avatar;
        if (avatar?._headAnim || idle?._vrmaActive || idle?.currentPose) return;
        this._speechNod = {
            active: true,
            elapsed: 0,
            duration: this.t.pauseNodDuration * (0.85 + Math.random() * 0.3),
            amp: this.t.pauseNodAmp * (0.8 + Math.random() * 0.35),
        };
    }

    // ── Gaze ──────────────────────────────────────────────────────

    _updateGaze(dt) {
        if (!this.camera) return;

        // 1. Maybe pick a new saccade.
        this._timeToNextSaccade -= dt;
        if (this._timeToNextSaccade <= 0) this._pickSaccade();

        // 2. Smooth the saccade offset toward its target. Two regimes:
        //    - micro-saccades (<6° amplitude) damp over ~50ms so they
        //      feel smooth instead of jittery,
        //    - refocus saccades (the flagged big ones from
        //      `_pickSaccade`) snap directly to target, mimicking the
        //      ballistic ~30-50ms eye movement humans actually make.
        if (this._snapSaccade) {
            this._saccadeOffset.copy(this._saccadeOffsetTarget);
            this._snapSaccade = false;
        } else {
            const saccadeLambda = halfLifeToLambda(0.05);
            this._saccadeOffset.x = MathUtils.damp(
                this._saccadeOffset.x, this._saccadeOffsetTarget.x, saccadeLambda, dt);
            this._saccadeOffset.y = MathUtils.damp(
                this._saccadeOffset.y, this._saccadeOffsetTarget.y, saccadeLambda, dt);
        }
        if (this._saccadeSettleTimer > 0) {
            this._saccadeSettleTimer = Math.max(0, this._saccadeSettleTimer - dt);
        }
        const settleDx = this._saccadeOffset.x - this._saccadeOffsetTarget.x;
        const settleDy = this._saccadeOffset.y - this._saccadeOffsetTarget.y;
        this._saccadeInFlight =
            this._saccadeSettleTimer > 0 || ((settleDx * settleDx + settleDy * settleDy) > 0.000003);

        // 3. Compute focus target in world coords: camera position +
        //    a perpendicular nudge in camera-local up/right by the
        //    current saccade offset. The offset is in radians
        //    (eye angle); we approximate by treating it as a distance
        //    at the camera's range. For typical room-scale distances
        //    (2–4 m) this is indistinguishable from proper conversion.
        const distance = Math.max(0.5,
            this.camera.position.distanceTo(this._avatarHeadWorld()));
        // Camera's right and up axes for offsetting laterally.
        const right = this._scratchRight.set(1, 0, 0).applyQuaternion(this.camera.quaternion);
        const up    = this._scratchUp.set(0, 1, 0).applyQuaternion(this.camera.quaternion);
        this._focusTarget.copy(this.camera.position)
            .addScaledVector(right, this._saccadeOffset.x * distance)
            .addScaledVector(up,    this._saccadeOffset.y * distance);

        // 4. Cascade: eye target follows focus, head follows eyes.
        const eyeLambda  = halfLifeToLambda(this.t.eyeHalfLife);
        const headLambda = halfLifeToLambda(this.t.headHalfLife);
        this._eyeTarget.x = MathUtils.damp(this._eyeTarget.x, this._focusTarget.x, eyeLambda, dt);
        this._eyeTarget.y = MathUtils.damp(this._eyeTarget.y, this._focusTarget.y, eyeLambda, dt);
        this._eyeTarget.z = MathUtils.damp(this._eyeTarget.z, this._focusTarget.z, eyeLambda, dt);
        this._headTarget.x = MathUtils.damp(this._headTarget.x, this._eyeTarget.x, headLambda, dt);
        this._headTarget.y = MathUtils.damp(this._headTarget.y, this._eyeTarget.y, headLambda, dt);
        this._headTarget.z = MathUtils.damp(this._headTarget.z, this._eyeTarget.z, headLambda, dt);

        // 5. Apply.
        this._lookAtDummy.position.copy(this._eyeTarget);
        // The VRM SDK reads `matrixWorld` from the look-at target the
        // same frame we wrote the position, and `vrm.update()` runs
        // BEFORE the renderer's auto-update pass — so we have to force
        // the world matrix now or the gaze lags one frame.
        this._lookAtDummy.updateMatrixWorld(true);
        // Head follow is deferred to `applyPost()` (called by app.js
        // AFTER avatarManager.update). Writing to the head bone here
        // would be obliterated by the mixer.update / vrm.update passes
        // that run inside avatarManager.update — those rewrite the
        // bone matrices from the active clip.
    }

    /**
     * Post-step that runs AFTER `avatarManager.update()`. Anything that
     * writes to humanoid bones (head, neck, hips…) lives here so the
     * clip animation pass inside the avatar doesn't trample it.
     *
     * Call order in app.js per frame:
     *
     *   microLife.update(dt, t)          // gaze targets, blink curves,
     *                                    // breathing offset — all the
     *                                    // stuff that doesn't fight
     *                                    // with vrm/mixer
     *   avatarManager.update(dt, t)      // mixer + vrm.update +
     *                                    // _applyHeadAnim
     *   microLife.applyPost(dt)          // ← we're here: head follow,
     *                                    // micro head noise, body sway
     */
    applyPost(dt) {
        this._lastDt = Math.max(1e-4, dt);   // damp lambda math hates 0
        this._postTime = (this._postTime || 0) + this._lastDt;
        if (!this.avatarManager?.avatar) return;

        // ── Yield to one-shot VRMA clips ──────────────────────────
        // When the LLM emits `animacion_virtual: "wave"` (or dance,
        // think, shy, etc.), the idle system plays a one-shot VRMA
        // clip that animates head + spine + arms via the mixer. Our
        // head-follow / head-noise / body-sway / weight-shift /
        // procedural-pose were stacking deltas ON TOP of that clip,
        // producing the glitchy "she's trying to correct her head /
        // body mid-wave" effect the user reported.
        //
        // Detection: `IdleSystem._vrmaExternalClip` is non-null while
        // a one-shot is playing (it's the clip name). The LOOPING
        // idle clip uses a different field, so we don't yield on
        // that — idle's continuous motion is fine to layer on top of.
        //
        // While yielded:
        //   - SKIP the bone-affecting `_accum*` calls
        //   - Damp our internal smoothed state toward 0 so when the
        //     clip ends and we resume, the next-frame delta is small
        //     and the transition is smooth instead of a snap.
        const idle = (window.iris || window.IRIS)?.idle;
        const externalClipName = idle?._vrmaExternalClip || null;
        const externalClipActive = !!externalClipName;
        const proceduralPoseActive = !!idle?.currentPose;
        const explicitAnimationActive = Boolean(
            idle?.isExplicitAnimationActive?.()
            || externalClipActive
            || proceduralPoseActive,
        );
        const stableDeskPose = String(idle?.currentPose || '').startsWith('pc_seated_idle');
        const anyPoseActive = explicitAnimationActive || !!idle?._vrmaActive;
        this._presenceAnimationScale = anyPoseActive
            ? MathUtils.clamp(this.t.activeAnimationPresenceScale ?? 0.22, 0, 1)
            : 1;

        // The SDK look-at applier moves eye bones/eye expressions, not the
        // head. Body-only poses can keep eye contact while the authored head,
        // neck and torso stay untouched. Other clips keep exclusive gaze.
        const avatar = this.avatarManager.avatar;
        const vrmLookAt = avatar.vrm?.lookAt;
        if (vrmLookAt) {
            const gaze = avatar.poseFacialProfile?.eyes?.gaze;
            const freeEyes = gaze === 'natural' && !avatar.animationOwnsBlink;
            const heldPitch = HELD_GAZE[gaze];
            vrmLookAt.autoUpdate = heldPitch === undefined && (freeEyes || (!stableDeskPose && !explicitAnimationActive));
            if (heldPitch !== undefined) {
                vrmLookAt.yaw = 0;
                vrmLookAt.pitch = heldPitch;
            }
        }

        if (explicitAnimationActive) {
            // Damp every smoothed state to 0 so that when the clip
            // ends and we resume writing, the first-frame deltas are
            // tiny and the transition is smooth instead of a snap.
            const decay = halfLifeToLambda(0.25);
            this._smoothedHeadYaw   = MathUtils.damp(this._smoothedHeadYaw,   0, decay, dt);
            this._smoothedHeadPitch = MathUtils.damp(this._smoothedHeadPitch, 0, decay, dt);
            this._smoothedNeckYaw   = MathUtils.damp(this._smoothedNeckYaw,   0, decay, dt);
            this._smoothedNeckPitch = MathUtils.damp(this._smoothedNeckPitch, 0, decay, dt);
            this._weightCurrent     = MathUtils.damp(this._weightCurrent,     0, decay, dt);
            this._poseCurrent.chestPitch   = MathUtils.damp(this._poseCurrent.chestPitch,   0, decay, dt);
            this._poseCurrent.shoulderLift = MathUtils.damp(this._poseCurrent.shoulderLift, 0, decay, dt);
            this._gestureChest    = MathUtils.damp(this._gestureChest,    0, decay, dt);
            this._gestureShoulder = MathUtils.damp(this._gestureShoulder, 0, decay, dt);
            this._speechNod.active = false;
            // Invalidate cached bone bases — when the clip ends, the
            // bones will be at the clip's final pose, and our saved
            // bases (from before the clip started) would produce a
            // wrong "base * delta" composition. Clearing forces
            // re-capture from the post-clip pose on the next frame.
            this._baseQuats?.clear();
        }
        // While the clip is active we skip the body-bone writers and
        // jump straight to the pause-brow expression write at the
        // bottom. The clip drives head/spine/hips itself.
        // Any explicit animation — including its return/crossfade — owns its
        // body bones. Body-only clips retain facial expression and blinking.
        const skipBoneAccum = explicitAnimationActive || stableDeskPose;
        // Compute every bone's accumulated delta first, then write
        // each bone ONCE. Two writers to the same bone (head-follow +
        // head-noise) would otherwise blow each other away because
        // `_applyBoneDelta` always resets to `base * delta` — the
        // second call's delta would erase the first call's effect.
        // Collecting deltas per-bone, multiplying them together, then
        // doing a single `_applyBoneDelta` per bone is the only way
        // both can coexist.
        this._frameDeltas = this._frameDeltas || new Map();
        this._frameDeltas.clear();
        // All body-bone accumulators are gated by `skipBoneAccum`
        // so they're a no-op while a one-shot VRMA clip is playing.
        if (!skipBoneAccum) {
            if (this.t.enableGaze && this.t.enableHeadFollow) {
                this._accumHeadFollow();
            }
            if (this.t.enableHeadNoise) {
                this._accumHeadNoise();
            }
            this._accumSpeakingNod();
            if (this.t.enableBodySway) {
                this._accumBodySway();
            }
            if (this.t.enableWeightShift) {
                this._accumWeightShift();
            }
            // Procedural pose (resting chest/shoulder posture driven
            // by current emotion) is SUPPRESSED while SpeechAnimator
            // is actively writing the chest. Both writers target
            // `upperChest` / `chest` with their own captured base
            // quaternions — running them concurrently means whichever
            // executes LATER stomps the earlier one's contribution.
            // applyPost runs after speechAnimator.update, so without
            // this gate MicroLife consistently erased SpeechAnimator's
            // breathing emphasis. The grace window after speech_end
            // covers SpeechAnimator's 0.5s fade-out so we don't snap
            // back into procedural pose while it's still decaying.
            const inSpeechGrace = this._postSpeechGraceUntil
                && (performance.now() / 1000) < this._postSpeechGraceUntil;
            if (this.t.enableProceduralPose && !this._speaking && !inSpeechGrace) {
                this._accumProceduralPose();
            }
            // Write the accumulated deltas to bones.
            for (const [name, info] of this._frameDeltas) {
                this._applyBoneDelta(info.bone, name, info.delta);
            }
        }

        // Speaking-pause brow flicker is written here (NOT inside
        // `_updateSpeakingPauses`) because writing earlier in the
        // frame would be lerped away by `_updateExpressions` running
        // inside `avatarManager.update` toward the emotion preset's
        // `surprised` target (usually 0). Doing it as part of the
        // final `applyPost` pass means it lands AFTER the smoothing.
        if (this.t.enableSpeakingPauses && this._pauseBrowBoost > 0.01) {
            try {
                const em = this.avatarManager.avatar.vrm?.expressionManager;
                if (em) {
                    // ADD to whatever the emotion smoothing left there
                    // instead of replacing it — that way a `sorprendida`
                    // emotion plus a pause flicker stack cleanly, and
                    // a flicker during `neutral` simply raises the
                    // brow from 0 to the boost amount.
                    const current = em.getValue?.('surprised') ?? 0;
                    em.setValue('surprised',
                        Math.min(1, current + this._pauseBrowBoost));
                }
            } catch {}
        }
    }

    /**
     * Helper: stash a delta for a bone. Multiple callers can stack;
     * deltas compose with quaternion multiplication.
     *
     * IMPORTANT: the cache key is derived from `bone.name`, NOT from
     * the caller's `name` argument. Reason: on rigs that lack some
     * optional bones (`upperChest`, `chest`), several systems fall
     * back to `spine` — body-sway uses `spine`, procedural-pose uses
     * `chest_pose` → both call this with the SAME bone object but
     * DIFFERENT keys, and the write loop in `applyPost` would end up
     * calling `_applyBoneDelta` twice on the same bone with
     * independent bases. The second call wipes the first. Using
     * `bone.name` as the key bucket guarantees a single combined
     * write per bone regardless of who called us.
     *
     * The `name` argument is kept only as a fallback if `bone.name`
     * is empty (which shouldn't happen on standard VRMs, but is
     * defensive against procedurally-created bones).
     */
    _stackDelta(bone, name, q) {
        const key = bone.name || name;
        const existing = this._frameDeltas.get(key);
        if (!existing) {
            // Clone so subsequent stacks don't mutate the caller's
            // shared `_headDelta` / `_noiseDelta` / `_swayDelta`.
            this._frameDeltas.set(key, { bone, delta: q.clone() });
        } else {
            existing.delta.multiply(q);
        }
    }

    _presenceProfile() {
        const profiles = this.t.presenceMoodScales || {};
        if (this.state === 'speaking') return profiles.speaking || {};
        if (this.state === 'thinking' || this.emotion === 'pensativa') return profiles.thinking || {};
        if (this.state === 'listening') return profiles.listening || profiles.neutral || {};
        if (this.emotion === 'feliz' || this.emotion === 'sorprendida') return profiles.happy || {};
        return profiles.neutral || {};
    }

    _presenceLayerScale(channel) {
        const profile = this._presenceProfile();
        const base = profile[channel] ?? 1;
        return base * (this._presenceAnimationScale ?? 1);
    }

    /**
     * Slow Perlin sway on the spine / hips. Different frequency from
     * head noise so the two don't sync into a single readable rhythm.
     *
     * We target `spine` first (closer to center of mass — more natural
     * weight-shift hinge); if the rig doesn't expose `spine`, we fall
     * back to `hips`. Writing to `hips` is more dramatic because it
     * carries the whole upper body, so amplitudes are scaled down
     * when that's where we land.
     */
    _accumBodySway() {
        const avatar = this.avatarManager?.avatar;
        const vrm = avatar?.vrm;
        if (!vrm?.humanoid) return;
        // Yield while a procedural head gesture (nod/shake/tilt) is in
        // flight — those are short bursts and body sway riding on top
        // looks like a competing twist. Matches the head-yield guards
        // in `_accumHeadFollow` / `_accumHeadNoise`.
        if (avatar._headAnim) return;
        let bone = vrm.humanoid.getNormalizedBoneNode?.('spine');
        let amplitudeScale = 1.0;
        if (!bone) {
            bone = vrm.humanoid.getNormalizedBoneNode?.('hips');
            amplitudeScale = 0.6;   // hips moves everything, so dial down
        }
        if (!bone) return;

        const t = (this._postTime || 0) * this.t.bodySwayFreq;
        const presenceScale = this._presenceLayerScale('sway');
        const yaw   = fractalNoise1D(t + 3.50) * this.t.bodySwayYawAmp   * amplitudeScale * presenceScale;
        const pitch = fractalNoise1D(t + 9.10) * this.t.bodySwayPitchAmp * amplitudeScale * presenceScale;
        const roll  = fractalNoise1D(t + 22.7) * this.t.bodySwayRollAmp  * amplitudeScale * presenceScale;

        if (!this._swayDelta) {
            this._swayDelta = new THREE.Quaternion();
            this._swayEuler = new THREE.Euler(0, 0, 0, 'YXZ');
        }
        this._swayEuler.set(pitch, yaw, roll, 'YXZ');
        this._swayDelta.setFromEuler(this._swayEuler);
        // Bone name is dynamic ('spine' vs 'hips'); cache it on the
        // fly so the dot-product re-capture heuristic still applies.
        const cacheKey = bone === vrm.humanoid.getNormalizedBoneNode?.('spine')
            ? 'spine' : 'hips';
        this._stackDelta(bone, cacheKey, this._swayDelta);
    }

    /**
     * Periodic commit to a weight-bearing side. Unlike body sway
     * which is continuous noise, this picks ONE of {-1, 0, +1} and
     * holds it for several seconds, then transitions to a different
     * one. Realised as a small Z-axis roll on the hips with a counter
     * roll on the spine, so the shoulders stay roughly level — looks
     * like the avatar genuinely shifted her weight without needing
     * leg IK.
     *
     * Sign convention: `weightCurrent = +1` means weight bearing on
     * RIGHT foot → hips drop on the LEFT side → hip Z-rotation
     * negative (a roll where the right side comes up). Inverse for -1.
     */
    _accumWeightShift() {
        const avatar = this.avatarManager?.avatar;
        const vrm = avatar?.vrm;
        if (!vrm?.humanoid) return;
        if (avatar._headAnim) return;   // yield during nod/shake/tilt
        const hips  = vrm.humanoid.getNormalizedBoneNode?.('hips');
        const spine = vrm.humanoid.getNormalizedBoneNode?.('spine');
        if (!hips) return;
        const presenceScale = this._presenceLayerScale('weight');

        // First-call init: pick the first commit after a short delay
        // so the avatar boots in a neutral pose rather than snapping
        // immediately to a side.
        const now = this._postTime || 0;
        if (!this._weightSwitchAt) {
            this._weightSwitchAt = now + 4.0;
        }

        // Time to commit to a different side?
        if (presenceScale > 0.12 && now >= this._weightSwitchAt) {
            const candidates = [-1, 0, 1].filter(s => s !== this._weightSide);
            this._weightSide = candidates[Math.floor(Math.random() * candidates.length)];
            const holdMin = Math.min(this.t.weightShiftHoldMin, this.t.weightShiftHoldMax);
            const holdMax = Math.max(this.t.weightShiftHoldMin, this.t.weightShiftHoldMax);
            const hold = holdMin + Math.random() * (holdMax - holdMin);
            this._weightSwitchAt = now + hold;
        } else if (presenceScale <= 0.12) {
            this._weightSide = 0;
            this._weightSwitchAt = Math.max(this._weightSwitchAt, now + 1.5);
        }

        // Smooth toward the target side. The transition takes ~1.5s
        // total (= half-life × ~2) so it's a deliberate "settle into
        // the new pose" rather than a snap.
        const lambda = halfLifeToLambda(this.t.weightShiftRiseTime);
        this._weightCurrent = MathUtils.damp(
            this._weightCurrent, this._weightSide, lambda, this._lastDt);

        // Apply: hips roll (Z axis), spine counter-roll.
        const hipRoll = -this._weightCurrent * this.t.weightShiftAmp * presenceScale;
        const spineCounter = -hipRoll * this.t.weightShiftCounter;

        if (!this._weightDelta) {
            this._weightDelta = new THREE.Quaternion();
            this._weightEuler = new THREE.Euler(0, 0, 0, 'YXZ');
        }
        this._weightEuler.set(0, 0, hipRoll, 'YXZ');
        this._weightDelta.setFromEuler(this._weightEuler);
        this._stackDelta(hips, 'hips', this._weightDelta);
        if (spine) {
            this._weightEuler.set(0, 0, spineCounter, 'YXZ');
            this._weightDelta.setFromEuler(this._weightEuler);
            this._stackDelta(spine, 'spine', this._weightDelta);
        }
    }

    /**
     * Smooth persistent rotations on chest + shoulders driven by the
     * current emotion. Adds the "body language" half of expressivity
     * on top of the facial blendshapes:
     *
     *   feliz       → chest expands, shoulders back
     *   triste      → chest sinks,   shoulders forward
     *   enojada     → chest pushed forward, shoulders TENSE up
     *   sorprendida → chest up,     shoulders up
     *   pensativa   → nearly neutral
     *
     * Contextual-gesture impulses (the brief "reflex" when emotion
     * CHANGES) add on top via `_gestureChest` / `_gestureShoulder`,
     * which decay separately back to zero in this same method.
     */
    _accumProceduralPose() {
        const avatar = this.avatarManager?.avatar;
        const vrm = avatar?.vrm;
        if (!vrm?.humanoid) return;
        if (avatar._headAnim) return;   // yield during nod/shake/tilt

        // Sustained pose target per emotion. Values intentionally
        // small — anything bigger reads as a forced acting pose.
        //
        // NOTE on amplitudes: a previous version had feliz shoulderLift
        // at 0.020 and pensativa chestPitch at 0.008, both BELOW the
        // perceptual threshold given the constant body-sway noise
        // (~0.6° spine roll continuously). Bumped to ranges that
        // actually read on the avatar without crossing into "acting"
        // territory (still all <3°).
        const POSE = {
            feliz:       { chestPitch: -0.035, shoulderLift:  0.030 },
            triste:      { chestPitch:  0.045, shoulderLift: -0.025 },
            enojada:     { chestPitch: -0.020, shoulderLift:  0.040 },
            sorprendida: { chestPitch: -0.030, shoulderLift:  0.045 },
            pensativa:   { chestPitch:  0.018, shoulderLift: -0.008 },
            neutral:     { chestPitch:  0,     shoulderLift:  0      },
        };
        const target = POSE[this.emotion] || POSE.neutral;
        const presenceScale = this._presenceLayerScale('pose');

        // Slow transition between emotions — body language doesn't
        // snap like the face does.
        const lambda = halfLifeToLambda(this.t.poseTransitionTime);
        this._poseCurrent.chestPitch = MathUtils.damp(
            this._poseCurrent.chestPitch, target.chestPitch, lambda, this._lastDt);
        this._poseCurrent.shoulderLift = MathUtils.damp(
            this._poseCurrent.shoulderLift, target.shoulderLift, lambda, this._lastDt);

        // Contextual reflex impulses on top — they ride decaying
        // from whatever `_fireContextualGesture` set toward zero.
        // 1.3s half-life: visible peak, then fades into the sustained
        // pose so the two layers don't pop.
        const decayLambda = halfLifeToLambda(1.3);
        this._gestureChest    = MathUtils.damp(this._gestureChest,    0, decayLambda, this._lastDt);
        this._gestureShoulder = MathUtils.damp(this._gestureShoulder, 0, decayLambda, this._lastDt);

        const chestPitch    = (this._poseCurrent.chestPitch + this._gestureChest) * presenceScale;
        const shoulderLift  = (this._poseCurrent.shoulderLift + this._gestureShoulder) * presenceScale;

        // Apply. Prefer `upperChest` if the rig has it; some VRMs
        // don't expose it (especially compact rigs from older VRoid
        // versions) — fall back to `chest`, then `spine`.
        const chestBone =
            vrm.humanoid.getNormalizedBoneNode?.('upperChest') ||
            vrm.humanoid.getNormalizedBoneNode?.('chest') ||
            vrm.humanoid.getNormalizedBoneNode?.('spine');
        if (chestBone) {
            if (!this._poseDelta) {
                this._poseDelta = new THREE.Quaternion();
                this._poseEuler = new THREE.Euler(0, 0, 0, 'YXZ');
            }
            this._poseEuler.set(chestPitch, 0, 0, 'YXZ');
            this._poseDelta.setFromEuler(this._poseEuler);
            // Cache key is dynamic but matters for the re-capture
            // heuristic — use a stable name.
            this._stackDelta(chestBone, 'chest_pose', this._poseDelta);
        }

        // Shoulders: lift the leftShoulder and rightShoulder bones by
        // rotating around their local Z axis. The sign needed depends
        // on which side (mirror them).
        const ls = vrm.humanoid.getNormalizedBoneNode?.('leftShoulder');
        const rs = vrm.humanoid.getNormalizedBoneNode?.('rightShoulder');
        if (!this._shoulderDelta) {
            this._shoulderDelta = new THREE.Quaternion();
            this._shoulderEuler = new THREE.Euler(0, 0, 0, 'YXZ');
        }
        if (ls) {
            // Left shoulder lifts when Z rotation is negative
            // (rotates the bone tip downward away from neck) — but
            // for the NORMALIZED bone, the convention is the opposite
            // sign on left vs right. Empirically: +Z lifts left.
            this._shoulderEuler.set(0, 0, shoulderLift, 'YXZ');
            this._shoulderDelta.setFromEuler(this._shoulderEuler);
            this._stackDelta(ls, 'leftShoulder', this._shoulderDelta);
        }
        if (rs) {
            this._shoulderEuler.set(0, 0, -shoulderLift, 'YXZ');
            this._shoulderDelta.setFromEuler(this._shoulderEuler);
            this._stackDelta(rs, 'rightShoulder', this._shoulderDelta);
        }
    }

    /**
     * Micro-movements of the lips during idle silence. Real people
     * don't keep their lips perfectly still when they're not
     * speaking — they purse them, dampen them, slight smile-pull,
     * etc. Without this layer the mouth reads as a painted-on shape.
     *
     * Constraints:
     *   - Only fires during `state === 'idle'` AND `mouthOpenness ~ 0`
     *     (so lipsync owns the mouth whenever there's audio).
     *   - Uses visemes `ih`, `oh`, `ou` — NEVER `aa`, because lipsync
     *     writes `aa` and we'd fight it.
     *   - Tiny amplitude (`lipIdleAmp` defaults to 0.18, peaks of a
     *     full mouth viseme are 1.0 — so we're at ~18%).
     *   - Bell-shaped envelope so the on/off is smooth, not a step.
     */
    _updateLipIdle(dt) {
        const avatar = this.avatarManager?.avatar;
        const em = avatar?.vrm?.expressionManager;
        if (!em) return;

        // Suspend lip idle whenever the mouth is actually being used,
        // and reset any active flicker so we don't carry residue
        // into speech.
        const mouthOpen = avatar.mouthOpenness ?? 0;
        if (this.state !== 'idle' || mouthOpen > 0.04) {
            if (this._lipIdleActive) {
                // Cancel cleanly — let the viseme drop to 0.
                try { em.setValue(this._lipIdleActive.viseme, 0); } catch {}
                this._lipIdleActive = null;
            }
            return;
        }

        // Tick the scheduler when nothing is active.
        if (!this._lipIdleActive) {
            this._lipIdleTimer -= dt;
            if (this._lipIdleTimer <= 0) {
                const visemes = ['ih', 'oh', 'ou'];
                this._lipIdleActive = {
                    viseme:   visemes[Math.floor(Math.random() * visemes.length)],
                    elapsed:  0,
                    duration: this.t.lipIdleDuration * (0.7 + Math.random() * 0.6),
                };
                this._lipIdleTimer = this.t.lipIdleMinGap +
                    Math.random() * (this.t.lipIdleMaxGap - this.t.lipIdleMinGap);
            }
            return;
        }

        // Drive the active flicker. Bell shape: weight = sin(πt) so
        // it rises smoothly, peaks at half-way, falls smoothly.
        this._lipIdleActive.elapsed += dt;
        const t = this._lipIdleActive.elapsed / this._lipIdleActive.duration;
        if (t >= 1) {
            try { em.setValue(this._lipIdleActive.viseme, 0); } catch {}
            this._lipIdleActive = null;
            return;
        }
        const weight = Math.sin(t * Math.PI) * this.t.lipIdleAmp;
        try { em.setValue(this._lipIdleActive.viseme, weight); } catch {}
    }

    /**
     * Tiny non-repeating wobble on the head bone. Three independent
     * noise channels (pitch, yaw, roll), each phased differently so
     * they never line up into a single readable pattern. Amplitudes
     * are intentionally below the threshold of conscious perception —
     * the goal is to kill the "head bolted to a pole" tell without
     * making the avatar look nervous.
     *
     * Layered AFTER `_applyHeadFollow`, so noise rides on top of the
     * gaze-derived aim. While a head GESTURE is in flight we yield
     * (same guard as head-follow) to avoid stepping on it.
     */
    _accumHeadNoise() {
        const avatar = this.avatarManager?.avatar;
        const vrm = avatar?.vrm;
        if (!vrm?.humanoid) return;
        if (avatar._headAnim) return;
        const head = vrm.humanoid.getNormalizedBoneNode?.('head');
        if (!head) return;

        // Each axis samples a different region of the noise field so
        // pitch, yaw, roll are uncorrelated. The offsets are arbitrary
        // — picked from a hashing-style "looks random enough" set.
        const t = this._postTime * this.t.headNoiseFreq;
        const presenceScale = this._presenceLayerScale('head');
        const pitch = fractalNoise1D(t + 0.00)  * this.t.headNoisePitchAmp * presenceScale;
        const yaw   = fractalNoise1D(t + 17.31) * this.t.headNoiseYawAmp   * presenceScale;
        const roll  = fractalNoise1D(t + 41.77) * this.t.headNoiseRollAmp  * presenceScale;

        if (!this._noiseDelta) {
            this._noiseDelta = new THREE.Quaternion();
            this._noiseEuler = new THREE.Euler(0, 0, 0, 'YXZ');
        }
        this._noiseEuler.set(pitch, yaw, roll, 'YXZ');
        this._noiseDelta.setFromEuler(this._noiseEuler);
        // Stack onto the same 'head' bucket as head-follow so we end
        // up with one combined delta (followQ * noiseQ) and one
        // `_applyBoneDelta` write per frame.
        this._stackDelta(head, 'head', this._noiseDelta);
    }

    _accumSpeakingNod() {
        const nod = this._speechNod;
        const avatar = this.avatarManager?.avatar;
        const vrm = avatar?.vrm;
        if (!nod?.active || !vrm?.humanoid || avatar?._headAnim) return;
        const head = vrm.humanoid.getNormalizedBoneNode?.('head');
        if (!head) {
            nod.active = false;
            return;
        }

        nod.elapsed += this._lastDt;
        const u = Math.min(1, nod.elapsed / Math.max(0.001, nod.duration));
        const arc = Math.sin(u * Math.PI);
        const pitch = -nod.amp * arc * this._presenceLayerScale('head');

        if (!this._speechNodDelta) {
            this._speechNodDelta = new THREE.Quaternion();
            this._speechNodEuler = new THREE.Euler(0, 0, 0, 'YXZ');
        }
        this._speechNodEuler.set(pitch, 0, 0, 'YXZ');
        this._speechNodDelta.setFromEuler(this._speechNodEuler);
        this._stackDelta(head, 'head', this._speechNodDelta);

        if (u >= 1) {
            nod.active = false;
            nod.elapsed = 0;
        }
    }

    /** Remove only our previous overlay before any animation writer runs. */
    beginFrame() {
        for (const entry of this._baseQuats.values()) {
            // A gesture may have been applied between frames. Keep that new
            // pose; only undo an orientation that still equals our output.
            if (1 - Math.abs(entry.bone.quaternion.dot(entry.output)) < 1e-12) {
                entry.bone.quaternion.copy(entry.base);
            }
        }
        this._baseQuats.clear();
    }

    /** Compose with this frame's authored pose, including sub-degree motion. */
    _applyBoneDelta(bone, name, delta) {
        let entry = this._baseQuats.get(name);
        if (!entry) {
            entry = { bone, base: bone.quaternion.clone(), output: new THREE.Quaternion() };
            this._baseQuats.set(name, entry);
        }
        bone.quaternion.copy(entry.base).multiply(delta).normalize();
        entry.output.copy(bone.quaternion);
    }

    /**
     * Drive the head + neck bones to aim at `_headTarget`.
     *
     * Why this exists when there's already an IK solver:
     *   `ik.js#_solveLookAt` calls `_aimBoneAt(head, target, +Z)` —
     *   it assumes the head bone's local-forward axis is +Z. That's
     *   only true on rigs whose avatar group hasn't been re-oriented.
     *   IRIS rotates the group by `restYaw` (typically 180°) so the
     *   character faces the camera, which means the head's parent
     *   frame is now flipped relative to world. The IK solver tries
     *   to compensate with a ~180° rotation, hits its yaw clamp, and
     *   the avatar visibly twists.
     *
     * What we do instead:
     *   1. Pick the head bone via the NORMALIZED humanoid API
     *      (three-vrm rebases the rest pose to identity for these).
     *   2. Compute yaw/pitch entirely in world space: the angle from
     *      the avatar's current forward direction to the direction
     *      pointing at `_headTarget`.
     *   3. Clamp both angles to comfort ranges.
     *   4. Split the rotation between neck and head (40/60) and write
     *      ABSOLUTE local rotations on the normalized bones. Rest is
     *      identity, so this composes correctly.
     *   5. Skip entirely while a head-gesture (nod/shake/tilt) is in
     *      flight, so `_applyHeadAnim` in avatar.js owns the head
     *      during those moments.
     */
    _accumHeadFollow() {
        const avatar = this.avatarManager?.avatar;
        const vrm = avatar?.vrm;
        if (!vrm?.humanoid) return;
        // Yield to active gesture animations (waveloop, nod, shake…).
        if (avatar._headAnim) return;
        // Inspector mode: the camera is being orbited by the user via
        // OrbitControls, so `_focusTarget = camera.position` swings
        // wildly each frame. Eye gaze tracking still looks natural
        // (the avatar maintains eye contact as you orbit — nice
        // touch), but the head bone would try to follow the swinging
        // camera and get stuck at the yaw clamp limit, twisting
        // permanently to one side. So we skip ONLY head-follow here;
        // the rest of MicroLife keeps running.
        if ((window.iris || window.IRIS)?.inspectorMode) return;

        const head = vrm.humanoid.getNormalizedBoneNode?.('head');
        const neck = vrm.humanoid.getNormalizedBoneNode?.('neck');
        if (!head) return;

        // Compute yaw/pitch in the HEAD-PARENT's local frame, not in
        // world. The original implementation used the avatar group's
        // world forward, which is correct on avatars whose `restYaw`
        // is 0 but flips the pitch sign on avatars whose group is
        // rotated 180° to face the camera (the `restYaw = π` case).
        // Going via the parent's local frame is hierarchy-agnostic:
        // whatever rotations apply along group→hips→…→neck, the
        // parent.worldToLocal call inverts them and our angles end up
        // in the same coordinate system as `head.quaternion`.
        const parent = head.parent;
        if (!parent) return;
        parent.updateWorldMatrix(true, false);

        // Where is the target in head-parent local space?
        // Use a scratch vector so we don't allocate every frame.
        // `worldToLocal` writes in-place, so we copy first then call.
        const localTarget = parent.worldToLocal(
            this._scratchLocalDir.copy(this._headTarget));
        // And where is the head bone's own origin? Its `position` IS
        // its offset within the parent — that's local already, no
        // transform needed.
        const dir = localTarget.sub(head.position);
        if (dir.lengthSq() < 1e-6) return;
        dir.normalize();

        // In the normalized humanoid rest pose, the head's natural
        // forward IS +Z in its own local frame (three-vrm guarantees
        // this). Yaw rotates around the bone's local Y axis; pitch
        // around its local X. atan2(x, z) gives the yaw to bring +Z
        // onto the dir vector projected to the horizontal plane;
        // asin(y) gives the pitch.
        let yaw = Math.atan2(dir.x, dir.z);
        // Wrap into [-π, π] just in case the avatar is parented to a
        // rotated rig (re-checks the head-on edge cases).
        while (yaw >  Math.PI) yaw -= Math.PI * 2;
        while (yaw < -Math.PI) yaw += Math.PI * 2;
        const pitch = -Math.asin(MathUtils.clamp(dir.y, -1, 1));

        // Clamp + weight.
        const w = this.t.headFollowWeight;
        const yawClamped = MathUtils.clamp(yaw,
            -this.t.headFollowYawMax, this.t.headFollowYawMax) * w;
        const pitchClamped = MathUtils.clamp(pitch,
            -this.t.headFollowPitchMax, this.t.headFollowPitchMax) * w;

        // Split between neck and head. Neck does part of the work so
        // the result looks like a real head-turn (whole upper body
        // hinges slightly), not a puppet whose head spins on a pole.
        const neckRatio = this.t.neckFollowRatio;
        const neckYaw   = yawClamped   * neckRatio;
        const neckPitch = pitchClamped * neckRatio;
        const headYaw   = yawClamped   - neckYaw;
        const headPitch = pitchClamped - neckPitch;

        // Damp INSTANCE state toward target. We don't damp the bone's
        // rotation directly because the clip pass overwrites it every
        // frame — damping against an overwritten value just bounces
        // around the clip pose. Keeping the smoothed values on `this`
        // gives a continuous low-pass that survives clip writes.
        const headLambda = halfLifeToLambda(0.06);
        this._smoothedHeadYaw   = MathUtils.damp(this._smoothedHeadYaw,   headYaw,   headLambda, this._lastDt);
        this._smoothedHeadPitch = MathUtils.damp(this._smoothedHeadPitch, headPitch, headLambda, this._lastDt);
        this._smoothedNeckYaw   = MathUtils.damp(this._smoothedNeckYaw,   neckYaw,   headLambda, this._lastDt);
        this._smoothedNeckPitch = MathUtils.damp(this._smoothedNeckPitch, neckPitch, headLambda, this._lastDt);

        // Apply as a quaternion delta ON TOP of the clip pose. The clip
        // has already written its keyframed head rotation this frame;
        // multiplying lets that bobbing / breathing show through while
        // we steer the overall aim toward the user. Rotation order
        // 'YXZ' matches the IK solver's convention (yaw first, then
        // pitch) so the result composes naturally with humanoid
        // animation data.
        if (!this._headDelta) {
            this._headDelta = new THREE.Quaternion();
            this._neckDelta = new THREE.Quaternion();
            this._tmpEuler = new THREE.Euler(0, 0, 0, 'YXZ');
        }
        this._tmpEuler.set(this._smoothedHeadPitch, this._smoothedHeadYaw, 0, 'YXZ');
        this._headDelta.setFromEuler(this._tmpEuler);
        this._stackDelta(head, 'head', this._headDelta);
        if (neck) {
            this._tmpEuler.set(this._smoothedNeckPitch, this._smoothedNeckYaw, 0, 'YXZ');
            this._neckDelta.setFromEuler(this._tmpEuler);
            this._stackDelta(neck, 'neck', this._neckDelta);
        }
    }

    _setSaccadeTarget(x, y, snap = false) {
        const coneX = Math.max(0.001, this.t.saccadeConeX || 0.115);
        const coneY = Math.max(0.001, this.t.saccadeConeY || 0.075);
        let clampedX = MathUtils.clamp(x, -coneX, coneX);
        let clampedY = MathUtils.clamp(y, -coneY, coneY);
        const ellipse = (clampedX * clampedX) / (coneX * coneX) +
                        (clampedY * clampedY) / (coneY * coneY);
        if (ellipse > 1) {
            const scale = 1 / Math.sqrt(ellipse);
            clampedX *= scale;
            clampedY *= scale;
        }
        this._saccadeOffsetTarget.set(clampedX, clampedY);
        this._snapSaccade = !!snap;
        this._saccadeSettleTimer = Math.max(
            this._saccadeSettleTimer || 0,
            this.t.saccadeBlinkGuard || 0.12
        );
    }

    _pickSaccade() {
        let mean, maxAngle, refocusP, refocusAngle, bias;
        const directFocus = (this._presenceNow() || 0) < (this._stableEyeContactUntil || 0);
        switch (this.state) {
            case 'speaking':
                mean = this.t.saccadeMeanGapSpeaking;
                maxAngle = this.t.saccadeMaxAngleSpeaking;
                refocusP = 0;
                refocusAngle = this.t.saccadeRefocus * 0.35;
                bias = null;
                break;
            case 'thinking':
                mean = this.t.saccadeMeanGapThinking;
                maxAngle = this.t.saccadeMaxAngleThinking;
                refocusP = 0.14;
                refocusAngle = this.t.saccadeRefocus;
                bias = this.t.saccadeThinkingBias;
                break;
            default:
                mean = this.t.saccadeMeanGap;
                maxAngle = this.t.saccadeMaxAngle;
                refocusP = this.t.saccadeRefocusP;
                refocusAngle = this.t.saccadeRefocus;
                bias = null;
        }
        if (directFocus) {
            maxAngle *= this.t.directSaccadeScale || 0.3;
            refocusP = 0;
            bias = null;
        }

        // Poisson interval with hard floor.
        this._timeToNextSaccade = Math.max(this.t.saccadeMinGap, poissonSample(mean));

        // Decide if this is a small drift or a bigger refocus.
        const big = Math.random() < refocusP;
        const mag = big ? refocusAngle : maxAngle;

        let x = 0;
        let y = 0;
        if (big && Math.random() < (this.t.saccadeRefocusCenterP ?? 0.7)) {
            // Refocuses mostly return to the user instead of choosing
            // a dramatic new direction. That keeps IRIS attentive.
            x = 0;
            y = 0;
        } else {
            // Triangular-ish frontal cone sampling: horizontal motion
            // is slightly more common than vertical, and both stay
            // near center. This reads like living attention, not noise.
            const softness = Math.sqrt(Math.random());
            x = (Math.random() * 2 - 1) * mag * softness;
            y = (Math.random() * 2 - 1) * mag * 0.58 * softness;
        }
        if (bias) {
            x += bias.x;
            y += bias.y;
        }
        // Real saccades complete in ~30-50ms regardless of amplitude;
        // smoothly damping over 50ms half-life makes the bigger
        // refocuses (≥6°) read as "smooth pursuit" tracking instead.
        // Flag a snap for those so the offset jumps to target on the
        // next frame, the way ballistic eye movement actually works.
        this._setSaccadeTarget(x, y, big);
    }

    _avatarHeadWorld() {
        // Returns a scratch vector — callers must NOT cache the
        // returned reference (we overwrite it next frame). Saves one
        // allocation per `_updateGaze` call.
        const vrm = this.avatarManager?.avatar?.vrm;
        const head = vrm?.humanoid?.getNormalizedBoneNode?.('head');
        if (head) {
            head.getWorldPosition(this._scratchHeadWorld);
            return this._scratchHeadWorld;
        }
        const group = this.avatarManager?.avatar?.group;
        if (group) {
            group.getWorldPosition(this._scratchHeadWorld);
        } else {
            this._scratchHeadWorld.set(0, 0, 0);
        }
        return this._scratchHeadWorld;
    }

    _attachLookAt(vrm) {
        if (!vrm?.lookAt) return;
        // We need the dummy in the scene graph so renderer auto-updates
        // its world matrix every frame (the VRM SDK reads matrixWorld
        // when computing eye gaze). Add to the scene root; the avatar
        // group rotates/translates but the dummy doesn't follow —
        // intentional, since the user's face stays in world space, not
        // avatar space.
        const parent = this.avatarManager?.avatar?.group?.parent;
        if (!parent) {
            // Avatar isn't attached to a scene yet. Without a parent
            // the dummy's matrixWorld would never auto-update (renderer
            // only walks the scene graph) and the SDK's gaze math would
            // see the identity matrix. Stay un-attached so we retry on
            // the next frame.
            return;
        }
        parent.add(this._lookAtDummy);
        try {
            vrm.lookAt.target = this._lookAtDummy;
            this._lookAtAttached = true;
        } catch (e) {
            console.warn('[micro-life] VRM look-at attach failed:', e);
            // Roll back so we don't claim attachment that didn't stick.
            parent.remove(this._lookAtDummy);
        }
    }

    // ── Blinks ────────────────────────────────────────────────────

    _resolveBlinkExpressions() {
        const expr = this.avatarManager?.avatar?.vrm?.expressionManager;
        if (!expr) return null;
        // Some exporters list empty L/R presets beside a usable combined blink.
        const names = new Set(expr.expressions
            .filter(e => !Array.isArray(e.binds) || e.binds.length > 0)
            .map(e => e.expressionName));
        const pick = (candidates) => candidates.find(n => names.has(n)) || null;

        const left  = pick(BLINK_L_CANDIDATES);
        const right = pick(BLINK_R_CANDIDATES);
        const single = pick(BLINK_SINGLE);
        // Prefer L/R when both exist (asymmetric blinks). Otherwise
        // fall back to the single 'blink' expression.
        if (left && right) return { mode: 'lr', left, right };
        if (single)        return { mode: 'single', single };
        return null;
    }

    _writeBlinkValues(value) {
        if (!this._blinkExpressionNames) {
            this._blinkExpressionNames = this._resolveBlinkExpressions();
        }
        const cfg = this._blinkExpressionNames;
        const em = this.avatarManager?.avatar?.vrm?.expressionManager;
        if (!cfg || !em) return;
        try {
            if (cfg.mode === 'lr') {
                em.setValue(cfg.left, value);
                em.setValue(cfg.right, value);
            } else {
                em.setValue(cfg.single, value);
            }
        } catch {}
    }

    _suspendBlinksForAnimation() {
        if (this._blinkSuppressedByAnimation) return;
        this._blinkSuppressedByAnimation = true;
        this._blinkL = { value: 0, phase: 'wait', t: 0, gap: 1.2, scheduledDouble: false };
        this._blinkR = { value: 0, phase: 'wait', t: 0, gap: 1.2, scheduledDouble: false };
        // One boundary write clears a blink that was already in flight. Do
        // not write again until the clip ends: it may author closed eyelids.
        this._writeBlinkValues(0);
    }

    _resumeBlinksAfterAnimation() {
        if (!this._blinkSuppressedByAnimation) return;
        this._blinkSuppressedByAnimation = false;
        // Avoid an immediate blink on the first idle frame after a clip.
        this._timeToNextBlink = Math.max(
            this.t.blinkMinGap,
            1.0 + Math.random() * 1.8,
        );
    }

    _suspendBreathingForAnimation() {
        if (this._breathingSuppressedByAnimation) return;
        const group = this.avatarManager?.avatar?.group;
        if (group && this._lastBreathOffset) {
            group.position.y -= this._lastBreathOffset;
        }
        this._lastBreathOffset = 0;
        this._breathBaseY = group?.position.y;
        this._breathingSuppressedByAnimation = true;
    }

    _resumeBreathingAfterAnimation() {
        if (!this._breathingSuppressedByAnimation) return;
        const group = this.avatarManager?.avatar?.group;
        this._breathingSuppressedByAnimation = false;
        this._lastBreathOffset = 0;
        this._breathBaseY = group?.position.y;
    }

    _meanBlinkGap() {
        const interval = this.avatarManager?.avatar?.poseFacialProfile?.eyes?.blinkInterval;
        if (Number.isFinite(interval)) return MathUtils.clamp(interval, 2, 8);
        switch (this.state) {
            case 'speaking': return this.t.blinkMeanGapSpeaking;
            case 'thinking': return this.t.blinkMeanGapThinking;
            default:         return this.t.blinkMeanGapIdle;
        }
    }

    _updateBlinks(dt) {
        if (!this._blinkExpressionNames) {
            this._blinkExpressionNames = this._resolveBlinkExpressions();
            if (!this._blinkExpressionNames) return;  // no blink expr on this VRM
        }

        // 1. Schedule new blinks via a Poisson-spaced "trigger clock".
        this._timeToNextBlink -= dt;
        if (this._timeToNextBlink <= 0) {
            if (this._saccadeInFlight) {
                this._timeToNextBlink = 0.08 + Math.random() * 0.08;
            } else {
                this._triggerBlink();
                this._timeToNextBlink = Math.max(
                    this.t.blinkMinGap, poissonSample(this._meanBlinkGap()));
            }
        }

        // 2. Advance each lid's small state machine.
        this._advanceLid(this._blinkL, dt);
        this._advanceLid(this._blinkR, dt);

        // 3. Compute baseline lid closure (thinking = narrowed eyes).
        const baseline = this.state === 'thinking' ? this.t.thinkingLidBaseline : 0;

        // 4. Write to the VRM expression manager.
        // NOTE: emotion-aware blink attenuation was tried here and
        // disabled because it suppressed blinks too aggressively
        // (zeroed them out entirely on some VRMs). TODO: revisit with
        // per-rig calibration or use VRM's `overrideBlink` policy.
        const final = (v) => Math.min(1, Math.max(baseline, v));
        const cfg = this._blinkExpressionNames;
        const set = (name, value) => {
            try { this.avatarManager.avatar.vrm.expressionManager.setValue(name, value); } catch {}
        };
        if (cfg.mode === 'lr') {
            set(cfg.left,  final(this._blinkL.value));
            set(cfg.right, final(this._blinkR.value));
        } else {
            // Average L and R so the single expression carries the
            // intent. We still drive separate state machines so we can
            // upgrade silently if a future model has L/R.
            const avg = (this._blinkL.value + this._blinkR.value) * 0.5;
            set(cfg.single, final(avg));
        }
    }

    _triggerBlink() {
        // Asymmetry: each blink picks a random L/R desync. There's a
        // FLOOR (`blinkAsymMin`) so the offset is never zero — perfectly
        // synced blinks read as mechanical, which is exactly the tell
        // we're trying to avoid. The side that gets delayed is also
        // randomized so we don't always start the same eye first.
        const span = Math.max(0, this.t.blinkAsymMax - this.t.blinkAsymMin);
        const magnitude = this.t.blinkAsymMin + Math.random() * span;
        const sign = Math.random() < 0.5 ? -1 : 1;
        this._blinkAsym = sign * magnitude;
        // `_blinkAsym > 0` ⇒ L is delayed, R fires first (and vice versa).
        const lDelay = this._blinkAsym > 0 ?  this._blinkAsym : 0;
        const rDelay = this._blinkAsym < 0 ? -this._blinkAsym : 0;
        this._kickLid(this._blinkL, lDelay);
        this._kickLid(this._blinkR, rDelay);
    }

    _kickLid(lid, delay) {
        // Capture pacing at onset so switching poses cannot snap a closing lid.
        const duration = this.avatarManager?.avatar?.poseFacialProfile?.eyes?.blinkDuration;
        lid.scale = Number.isFinite(duration)
            ? MathUtils.clamp(duration, 0.18, 0.5) / 0.25
            : this.state === 'thinking' ? this.t.blinkThinkingScale : 1;
        lid.phase = 'pre';
        // Countdown semantics: 't' starts negative and `_advanceLid`
        // increments it each frame; when it crosses 0 the lid begins
        // closing. The original code stored `+delay` which is already
        // >= 0 on the very next frame, so the L/R desync was a no-op.
        lid.t = -delay;
        lid.scheduledDouble = Math.random() < this.t.doubleBlinkChance;
    }

    _advanceLid(lid, dt) {
        lid.t += dt;
        // State-modulated durations: when "thinking", each phase
        // stretches so the blink itself feels slow and pensive (not
        // just less frequent). This is what the spec called the "blink
        // más lento cuando piensa" — depth (via baseline) AND speed.
        const scale = lid.scale ?? 1;
        const closeTime = this.t.blinkCloseTime * scale;
        const holdTime  = this.t.blinkHoldTime  * scale;
        const openTime  = this.t.blinkOpenTime  * scale;
        switch (lid.phase) {
            case 'wait':
                // idle — value stays at 0 (rest)
                lid.value = 0;
                break;
            case 'pre': {
                // 't' counts up from `-delay`. Once it crosses zero
                // we begin the close phase, carrying any overshoot
                // into the close timer so the total close duration
                // stays constant regardless of framerate.
                if (lid.t >= 0) {
                    const overshoot = lid.t;
                    lid.phase = 'close';
                    lid.t = overshoot;
                }
                break;
            }
            case 'close':
                lid.value = Math.min(1, lid.t / closeTime);
                if (lid.t >= closeTime) {
                    lid.phase = 'hold';
                    lid.t = 0;
                    lid.value = 1;
                }
                break;
            case 'hold':
                lid.value = 1;
                if (lid.t >= holdTime) {
                    lid.phase = 'open';
                    lid.t = 0;
                }
                break;
            case 'open':
                lid.value = 1 - Math.min(1, lid.t / openTime);
                if (lid.t >= openTime) {
                    lid.phase = 'wait';
                    lid.value = 0;
                    lid.t = 0;
                    // Maybe schedule a follow-up blink for the double.
                    // Only schedule on the L lid so we don't double-fire.
                    if (lid === this._blinkL && lid.scheduledDouble) {
                        lid.scheduledDouble = false;
                        // Queue a synchronized re-trigger after the delay.
                        // We piggyback on the main scheduler by reducing
                        // the time-to-next-blink to the double delay.
                        if (this._timeToNextBlink > this.t.doubleBlinkDelay) {
                            this._timeToNextBlink = this.t.doubleBlinkDelay;
                        }
                    }
                }
                break;
        }
    }

    // ── Breathing ─────────────────────────────────────────────────

    _updateBreathing(dt) {
        let amp, freq;
        switch (this.state) {
            case 'speaking': amp = this.t.breathAmpSpeaking; freq = this.t.breathFreqSpeaking; break;
            case 'thinking': amp = this.t.breathAmpThinking; freq = this.t.breathFreqThinking; break;
            default:
                if (this.emotion === 'feliz' || this.emotion === 'sorprendida') {
                    amp = this.t.breathAmpHappy;
                    freq = this.t.breathFreqHappy;
                } else {
                    amp = this.t.breathAmpIdle;
                    freq = this.t.breathFreqIdle;
                }
        }
        const profile = this._presenceProfile();
        const idle = (window.iris || window.IRIS)?.idle;
        const animationScale = (idle?._vrmaActive || idle?.currentPose)
            ? MathUtils.clamp(this.t.activeAnimationPresenceScale ?? 0.22, 0, 1)
            : 1;
        amp *= (profile.breathAmp ?? 1) * animationScale;
        freq *= (profile.breathFreq ?? 1);
        this._breathPhase += dt * freq * Math.PI * 2;
        const offset = Math.sin(this._breathPhase) * amp;

        const group = this.avatarManager?.avatar?.group;
        if (group) {
            // Subtle vertical drift. The avatar's idle/walk systems may
            // also write to position.y — to avoid fighting them we add
            // an OFFSET stored separately and undone next frame.
            //
            // Strategy: snapshot the "non-breathing" base on first call,
            // then each frame restore base then add our offset. If
            // something else writes position.y between frames we read it
            // and treat THAT as the new base — best-effort coexistence.
            if (this._breathBaseY === undefined) {
                this._breathBaseY = group.position.y;
            } else {
                const expected = this._breathBaseY + this._lastBreathOffset;
                if (Math.abs(group.position.y - expected) > 1e-4) {
                    // Someone else (idle/walk) moved it — re-baseline.
                    this._breathBaseY = group.position.y - this._lastBreathOffset;
                }
            }
            group.position.y = this._breathBaseY + offset;
            this._lastBreathOffset = offset;
        }
    }

    // ── Debug HUD ─────────────────────────────────────────────────

    attachDebugHUD() {
        if (this._hud) return;
        const root = document.createElement('div');
        root.id = 'IRIS-microlife-hud';
        root.style.cssText = `
            position: fixed; right: 14px; bottom: 14px; z-index: 9999;
            background: rgba(12, 12, 20, 0.92); color: #e8e8ef;
            font: 11px/1.35 system-ui, sans-serif;
            padding: 10px 12px; border-radius: 8px;
            border: 1px solid rgba(255,255,255,0.12);
            width: 270px; max-height: 70vh; overflow: auto;
            box-shadow: 0 8px 28px rgba(0,0,0,0.45);
            backdrop-filter: blur(6px);
        `;
        const title = document.createElement('div');
        title.style.cssText = 'font-weight:600;letter-spacing:.04em;margin-bottom:6px;color:#ffd66e;';
        title.textContent = 'MicroLife — Tiers 1·2·3';
        root.appendChild(title);

        const stateLine = document.createElement('div');
        stateLine.style.cssText = 'margin-bottom:8px;opacity:.85';
        root.appendChild(stateLine);

        // Toggle group — one button per major subsystem so the user
        // can A/B-compare with each layer on/off without reloading.
        // Order roughly matches the Tier numbering (foundational
        // first, layered features after).
        const toggles = [
            ['enableGaze',             'Gaze'],
            ['enableBlink',            'Blink'],
            ['enableBreathing',        'Breath'],
            ['enableHeadFollow',       'Head follow'],
            ['enableHeadNoise',        'Head noise'],
            ['enableBodySway',         'Body sway'],
            ['enableSpeakingPauses',   'Pauses'],
            ['enableWeightShift',      'Weight shift'],
            ['enableProceduralPose',   'Pose'],
            ['enableLipIdle',          'Lip idle'],
            ['enableContextualGestures','Gestures'],
        ];
        const toggleRow = document.createElement('div');
        toggleRow.style.cssText = 'display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px';
        for (const [key, label] of toggles) {
            const b = document.createElement('button');
            b.textContent = label;
            b.dataset.key = key;
            b.style.cssText = `
                font: 10px system-ui; padding: 3px 7px; border-radius: 4px;
                border: 1px solid rgba(255,255,255,0.2); background: transparent;
                color: inherit; cursor: pointer;
            `;
            b.addEventListener('click', () => {
                this.t[key] = !this.t[key];
                this._refreshHUDValues();
            });
            toggleRow.appendChild(b);
        }
        root.appendChild(toggleRow);

        // Sliders for the most-tuned numbers.
        const sliderDefs = [
            ['eyeHalfLife',           0.02, 0.40, 0.005],
            ['headHalfLife',          0.05, 0.60, 0.01],
            ['saccadeMeanGap',        0.5,  6.0,  0.1],
            ['saccadeMaxAngle',       0.01, 0.20, 0.005],
            ['saccadeRefocus',        0.05, 0.40, 0.01],
            ['blinkMeanGapIdle',      1.5, 10.0,  0.1],
            ['blinkCloseTime',        0.03, 0.20, 0.005],
            ['blinkOpenTime',         0.04, 0.30, 0.005],
            ['doubleBlinkChance',     0.0,  0.30, 0.01],
            ['thinkingLidBaseline',   0.0,  0.40, 0.01],
            ['breathAmpIdle',         0.002, 0.04, 0.001],
            ['breathAmpHappy',        0.002, 0.04, 0.001],
            ['breathAmpThinking',     0.002, 0.04, 0.001],
            ['breathAmpSpeaking',     0.002, 0.04, 0.001],
            ['breathFreqIdle',        0.10,  0.60, 0.01],
            ['breathFreqHappy',       0.10,  0.70, 0.01],
            ['breathFreqThinking',    0.08,  0.50, 0.01],
            ['headNoiseYawAmp',       0.000, 0.02, 0.001],
            ['headNoisePitchAmp',     0.000, 0.02, 0.001],
            ['bodySwayYawAmp',        0.000, 0.03, 0.001],
            ['bodySwayRollAmp',       0.000, 0.03, 0.001],
            ['weightShiftAmp',        0.000, 0.05, 0.001],
            ['weightShiftHoldMin',    4.0,  20.0, 0.5],
            ['weightShiftHoldMax',    6.0,  24.0, 0.5],
        ];
        for (const [key, min, max, step] of sliderDefs) {
            const row = document.createElement('label');
            row.style.cssText = 'display:flex;gap:6px;align-items:center;margin:3px 0;';
            const lab = document.createElement('span');
            lab.style.cssText = 'flex:1;opacity:.85';
            lab.textContent = key;
            const val = document.createElement('span');
            val.style.cssText = 'min-width:50px;text-align:right;color:#ffd66e';
            val.textContent = this.t[key].toFixed(3);
            const input = document.createElement('input');
            input.type = 'range';
            input.min = min;
            input.max = max;
            input.step = step;
            input.value = this.t[key];
            input.style.cssText = 'flex:1.4;min-width:90px';
            input.addEventListener('input', () => {
                this.t[key] = parseFloat(input.value);
                val.textContent = this.t[key].toFixed(3);
            });
            row.appendChild(lab);
            row.appendChild(input);
            row.appendChild(val);
            root.appendChild(row);
        }

        document.body.appendChild(root);
        this._hud = { root, stateLine, toggles: toggleRow };
        this._refreshHUDValues();
    }

    _refreshHUDValues() {
        if (!this._hud) return;
        const ml = this;
        this._hud.stateLine.textContent =
            `state: ${ml.state}   emo: ${ml.emotion}   next blink in ${ml._timeToNextBlink.toFixed(2)}s   ` +
            `next saccade ${ml._timeToNextSaccade.toFixed(2)}s`;
        for (const btn of this._hud.toggles.children) {
            const key = btn.dataset.key;
            btn.style.background = this.t[key] ? 'rgba(255,214,110,0.18)' : 'transparent';
            btn.style.borderColor = this.t[key]
                ? 'rgba(255,214,110,0.6)'
                : 'rgba(255,255,255,0.2)';
        }
    }

    detachDebugHUD() {
        this._hud?.root?.remove();
        this._hud = null;
    }

    // ── Teardown ──────────────────────────────────────────────────

    destroy() {
        for (const [name, fn] of this._wiredListeners) {
            window.removeEventListener(name, fn);
        }
        this._wiredListeners.length = 0;
        this._lookAtDummy?.parent?.remove(this._lookAtDummy);
        this.detachDebugHUD();
    }
}

