/**
 * IRIS Emotion Classifier — ARKit blendshapes → semantic emotion
 *
 * Takes the 52-channel ARKit blendshape bundle that MediaPipe Face
 * Landmarker produces (`mouthSmileLeft`, `browInnerUp`, …) and reduces
 * it to a small, conversationally-useful set of emotional categories
 * the LLM can act on.
 *
 * Why heuristic rules instead of an ML model:
 *   • The mapping ARKit→Ekman is well-understood — combinations like
 *     "smile + raised cheeks" = happy, "brow down + frown" = angry,
 *     "raised brows + open jaw" = surprised — are textbook FACS
 *     (Facial Action Coding System).
 *   • A trained classifier here would buy us ~5% more accuracy at
 *     the cost of an extra ~5MB ONNX model and 2-4ms per frame.
 *     Not worth it for a single-camera assistant.
 *   • Heuristics are auditable: when IRIS misreads "I'm focused" as
 *     "angry" we can tweak the brow-down threshold instead of
 *     re-training. Critical for trust during early UX iteration.
 *
 * Output shape:
 *   {
 *     label:      'happy' | 'sad' | 'surprised' | 'angry' | 'fearful'
 *               | 'disgusted' | 'confused' | 'neutral',
 *     intensity:  0..1     // how strongly the emotion reads
 *     confidence: 0..1     // how certain we are vs the runner-up
 *     valence:    -1..+1   // negative ↔ positive (smoothed)
 *     arousal:    0..1     // calm ↔ excited (smoothed)
 *     scores:     { happy, sad, …, neutral }   // raw per-class scores
 *   }
 *
 * Smoothing:
 *   Raw frame-to-frame readings jitter a lot (MediaPipe is per-frame,
 *   no temporal model). We exp-smooth valence/arousal at α=0.15 so
 *   IRIS's "engagement" param doesn't twitch every blink. The label
 *   itself uses hysteresis: once locked into a class it needs the
 *   runner-up to lead by 0.15 for ≥4 frames before switching, which
 *   prevents the label from flickering between "neutral" and "happy"
 *   when the user is at a borderline smile.
 *
 * Usage:
 *   const clf = new EmotionClassifier();
 *   // every camera_mocap frame:
 *   const e = clf.classify(blendshapes);
 *   if (e) idle.feedUserEmotion(e);
 */


// ── Tunables ─────────────────────────────────────────────────────

// Exponential smoothing for valence/arousal. Lower = more inertia.
// At 30 fps a value of 0.15 gives a ~250ms time constant.
const VA_SMOOTH = 0.15;

// Label hysteresis. The new top-class must beat the current label by
// at least HYSTERESIS_MARGIN for HYSTERESIS_FRAMES consecutive frames
// before we switch. Prevents flicker between neutral↔happy at low
// smile intensities.
const HYSTERESIS_MARGIN = 0.15;
const HYSTERESIS_FRAMES = 4;

// Score floor below which we don't trust ANY emotion read and just
// say "neutral". MediaPipe regularly emits scores in 0.05-0.10 range
// at rest from face asymmetry; reading those as emotional signal
// gives false positives.
const NOISE_FLOOR = 0.18;


// ── Helpers ──────────────────────────────────────────────────────

const clip = (v) => Math.max(0, Math.min(1, v || 0));

// Linear ramp: returns 0 below `from`, 1 above `to`, linear in between.
// Used to give each input blendshape a soft "deadzone" so micro-noise
// at rest doesn't activate the score.
const ramp = (v, from, to) => {
    if (v <= from) return 0;
    if (v >= to)   return 1;
    return (v - from) / (to - from);
};


// ── Per-class score functions ────────────────────────────────────
//
// Each function takes the ARKit bundle and returns a 0..1 score. The
// shapes referenced are the standard Apple ARKit names:
//   https://developer.apple.com/documentation/arkit/arfaceanchor/blendshapelocation
//
// Score functions are intentionally simple — sums and minima — so
// behavior is predictable. A symmetric expression (smile on both
// sides, brows up evenly) scores higher than an asymmetric one
// (smirk, raised eyebrow), which matches how humans rate the
// "intensity" of an emotion.

function scoreHappy(b) {
    const smile = (clip(b.mouthSmileLeft) + clip(b.mouthSmileRight)) * 0.5;
    const cheek = (clip(b.cheekSquintLeft) + clip(b.cheekSquintRight)) * 0.5;
    // Genuine smile (Duchenne marker) has BOTH mouth-corners up AND
    // cheek-squint. Mouth alone = polite smile; cheek alone = squint.
    return ramp(smile, 0.15, 0.75) * (0.6 + 0.4 * ramp(cheek, 0.05, 0.4));
}

function scoreSad(b) {
    const frown = (clip(b.mouthFrownLeft) + clip(b.mouthFrownRight)) * 0.5;
    const browIn = clip(b.browInnerUp);                    // worried-brow shape
    const browDown = (clip(b.browDownLeft) + clip(b.browDownRight)) * 0.5;
    // Sad: corners down + inner brows raised. Brow-down (anger marker)
    // suppresses the score so we don't false-positive on furrowed
    // concentration faces.
    return ramp(frown, 0.15, 0.7) * (0.6 + 0.4 * ramp(browIn, 0.1, 0.5))
         * (1 - 0.5 * ramp(browDown, 0.4, 0.9));
}

function scoreSurprised(b) {
    const jaw = clip(b.jawOpen);
    const browUp = clip(b.browInnerUp);                    // raised brows
    const browOuter = (clip(b.browOuterUpLeft) + clip(b.browOuterUpRight)) * 0.5;
    // Surprise = open mouth + raised brows. Jaw alone is "speaking",
    // brows alone is "asking a question". The combination is the tell.
    return Math.min(1,
        ramp(jaw, 0.35, 0.85) * (0.4 + 0.3 * browUp + 0.3 * browOuter)
    );
}

function scoreAngry(b) {
    const browDown = (clip(b.browDownLeft) + clip(b.browDownRight)) * 0.5;
    const noseSneer = (clip(b.noseSneerLeft) + clip(b.noseSneerRight)) * 0.5;
    const mouthPress = (clip(b.mouthPressLeft) + clip(b.mouthPressRight)) * 0.5;
    // Anger: brows pulled down + nose sneer + lips pressed. Each one
    // alone reads differently (concentration, disgust, doubt) — the
    // combination is what tips it to "angry".
    return ramp(browDown, 0.4, 0.9)
         * (0.5 + 0.25 * ramp(noseSneer, 0.1, 0.5)
                + 0.25 * ramp(mouthPress, 0.15, 0.6));
}

function scoreFearful(b) {
    const browUp = clip(b.browInnerUp);
    const eyeWideL = clip(b.eyeWideLeft);
    const eyeWideR = clip(b.eyeWideRight);
    const mouthStretch = (clip(b.mouthStretchLeft) + clip(b.mouthStretchRight)) * 0.5;
    // Fear: tight raised brows + wide eyes + mouth stretched horizontally.
    return ramp(browUp, 0.3, 0.8)
         * ramp((eyeWideL + eyeWideR) * 0.5, 0.2, 0.7)
         * (0.5 + 0.5 * ramp(mouthStretch, 0.1, 0.5));
}

function scoreDisgusted(b) {
    const noseSneer = (clip(b.noseSneerLeft) + clip(b.noseSneerRight)) * 0.5;
    const mouthUpper = clip(b.mouthUpperUpLeft) + clip(b.mouthUpperUpRight);
    // Disgust: nose sneer + raised upper lip. Distinct from anger by
    // the lip-raise (anger presses lips together).
    return ramp(noseSneer, 0.2, 0.8) * (0.6 + 0.4 * ramp(mouthUpper, 0.1, 0.6));
}

function scoreConfused(b) {
    // Asymmetric brow-up (one brow up, one down) is the classic
    // "confused" or "skeptical" tell. Symmetric movements cancel out.
    const browAsym = Math.abs(
        clip(b.browOuterUpLeft) - clip(b.browOuterUpRight)
    );
    const mouthFrownAsym = Math.abs(
        clip(b.mouthFrownLeft) - clip(b.mouthFrownRight)
    );
    return ramp(browAsym, 0.1, 0.5)
         * (0.6 + 0.4 * ramp(mouthFrownAsym, 0.05, 0.3));
}


// ── Classifier ──────────────────────────────────────────────────

const LABELS = ['happy','sad','surprised','angry','fearful','disgusted','confused','neutral'];

// Valence (positive ↔ negative) and arousal (calm ↔ excited) anchors
// per emotion — used to drive continuous params downstream (idle's
// `mood` and `energy` fields).
const VA_TABLE = {
    happy:      { valence:  0.8, arousal: 0.7 },
    sad:        { valence: -0.8, arousal: 0.2 },
    surprised:  { valence:  0.1, arousal: 0.9 },
    angry:      { valence: -0.7, arousal: 0.85 },
    fearful:    { valence: -0.6, arousal: 0.85 },
    disgusted:  { valence: -0.5, arousal: 0.5 },
    confused:   { valence: -0.1, arousal: 0.4 },
    neutral:    { valence:  0.0, arousal: 0.3 },
};


export class EmotionClassifier {
    constructor() {
        // Smoothed valence/arousal — these are what downstream
        // consumers (idle params, LLM context) should read.
        this.valence = 0;
        this.arousal = 0.3;

        // Hysteresis state for the label.
        this.label = 'neutral';
        this._candidate = 'neutral';
        this._candidateFrames = 0;
    }

    /**
     * Score one frame of blendshapes and return the current emotion
     * snapshot. Returns null if the input is empty/invalid.
     */
    classify(blendshapes) {
        if (!blendshapes || typeof blendshapes !== 'object') return null;

        // Compute per-class raw scores.
        const scores = {
            happy:      scoreHappy(blendshapes),
            sad:        scoreSad(blendshapes),
            surprised:  scoreSurprised(blendshapes),
            angry:      scoreAngry(blendshapes),
            fearful:    scoreFearful(blendshapes),
            disgusted:  scoreDisgusted(blendshapes),
            confused:   scoreConfused(blendshapes),
        };
        // Neutral is the residual — if no other class scores high,
        // we're in neutral. This avoids a separate "neutral" detector
        // that would otherwise need its own deadzone gymnastics.
        const maxOther = Math.max(...Object.values(scores));
        scores.neutral = Math.max(0, 1 - maxOther);

        // Pick top class above the noise floor.
        let topLabel = 'neutral';
        let topScore = scores.neutral;
        let runnerUp = 0;
        for (const lbl of LABELS) {
            const s = scores[lbl];
            if (s > topScore) { runnerUp = topScore; topScore = s; topLabel = lbl; }
            else if (s > runnerUp) { runnerUp = s; }
        }
        if (topScore < NOISE_FLOOR) {
            topLabel = 'neutral';
            topScore = scores.neutral;
        }

        // Hysteresis on the label so it doesn't flicker.
        if (topLabel === this.label) {
            this._candidate = topLabel;
            this._candidateFrames = 0;
        } else if (topLabel === this._candidate
                   && (topScore - runnerUp) >= HYSTERESIS_MARGIN) {
            this._candidateFrames++;
            if (this._candidateFrames >= HYSTERESIS_FRAMES) {
                this.label = topLabel;
                this._candidateFrames = 0;
            }
        } else {
            this._candidate = topLabel;
            this._candidateFrames = 1;
        }

        // Smooth valence/arousal toward the active label's anchor.
        const va = VA_TABLE[this.label] ?? VA_TABLE.neutral;
        this.valence += (va.valence - this.valence) * VA_SMOOTH;
        this.arousal += (va.arousal - this.arousal) * VA_SMOOTH;

        // Confidence: relative gap between top and runner-up.
        const confidence = topScore <= 0
            ? 0
            : Math.min(1, Math.max(0, (topScore - runnerUp) / topScore));

        return {
            label:      this.label,
            intensity:  topScore,
            confidence,
            valence:    this.valence,
            arousal:    this.arousal,
            scores,
        };
    }

    /** Reset to neutral — useful when the camera goes off. */
    reset() {
        this.label = 'neutral';
        this._candidate = 'neutral';
        this._candidateFrames = 0;
        this.valence = 0;
        this.arousal = 0.3;
    }
}

