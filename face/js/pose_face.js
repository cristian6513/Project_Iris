/** Temporary facial accents for body-only poses, shared by Face and Companion. */

// Every channel an emotion can drive. The first five are VRM presets; the rest exist on MMD models
// that have the morphs (pmx_expressions.js) and are skipped by faces without them.
const CHANNELS = Object.freeze(['happy', 'sad', 'angry', 'surprised', 'relaxed',
    'blush', 'heartEyes', 'starEyes', 'sweat', 'zzz', 'dubious', 'smallPupils', 'smirk']);
const mix = channels => Object.freeze({ ...Object.fromEntries(CHANNELS.map(name => [name, 0])), ...channels });

// Each emotion mixes several of them, so even a VRM with only the five presets shows more than one
// note (2026-11-24): shy is a small smile with a little sadness, nervous an awkward smile with
// worried brows and wide eyes. MMD models add blush, heart or star eyes, a sweat drop, ZZZ.
export const VRM_EMOTION_PRESETS = Object.freeze({
    neutral:     mix({ relaxed: 0.3 }),
    feliz:       mix({ happy: 0.9 }),
    triste:      mix({ sad: 0.8 }),
    pensativa:   mix({ sad: 0.2, relaxed: 0.5 }),
    sorprendida: mix({ surprised: 0.9 }),
    enojada:     mix({ angry: 0.8 }),
    avergonzada: mix({ happy: 0.35, sad: 0.2, relaxed: 0.35, blush: 1 }),
    'cariñosa':  mix({ happy: 0.6, relaxed: 0.55, blush: 0.5, heartEyes: 0.9 }),
    emocionada:  mix({ happy: 1, surprised: 0.35, starEyes: 0.9 }),
    confundida:  mix({ sad: 0.3, surprised: 0.3, angry: 0.12, dubious: 1 }),
    // A lopsided smile on MMD faces (smirk: one mouth corner); a VRM keeps a small even one.
    nerviosa:    mix({ happy: 0.15, sad: 0.35, surprised: 0.3, sweat: 1, smallPupils: 0.5, smirk: 0.6 }),
    somnolienta: mix({ sad: 0.15, relaxed: 0.6, zzz: 1 }),
});

// Eyelids an emotion lowers, on any face: heavy when sleepy, a little for shy, sad or angry eyes.
export const EMOTION_LIDS = Object.freeze({
    triste: 0.12, pensativa: 0.08, enojada: 0.1, avergonzada: 0.2, 'cariñosa': 0.12, somnolienta: 0.5,
});
export const EMOTION_LID_MAX = 0.55;

// Marks and eye shapes are pieces that are there or not: a pose's mood shows them whole.
const WHOLE_CHANNELS = new Set(['heartEyes', 'starEyes', 'sweat', 'zzz']);

const clamp = (v, min = 0, max = 1) => Math.min(max, Math.max(min, Number.isFinite(v) ? v : min));
const smooth = v => v * v * (3 - 2 * v);
const MOOD_DRIFT = 1.6;   // a mood at rest comes and goes slowly; a gesture's arrives with the gesture
const AGENT_MOOD = ['happy', 'sad', 'angry', 'surprised', 'blush', 'heartEyes', 'starEyes', 'sweat', 'zzz', 'dubious'];

/** A rest pose has no agent to give it a mood: it carries its own, which may change along the clip. */
export function moodAt(profile, time) {
    const moment = profile?.moments?.find(({ from, to }) => time >= from && time < to);
    return moment ?? profile;
}

/** How much room a rest mood has: none while she speaks or shows a mood the agent gave her. */
function restRoom(avatar) {
    if (avatar.poseFaceSpeaking) return 0;
    const agent = Math.max(...AGENT_MOOD.map(name => avatar.currentExpressions?.[name] || 0));
    return 1 - smooth(clamp(agent / 0.15));
}

/** Frame-clock blink: no timeout can reopen eyes belonging to a later clip. */
export class NaturalBlink {
    constructor(random = Math.random) {
        this.random = random;
        this.reset();
    }

    reset() {
        this.wait = 1.5 + this.random() * 2;
        this.elapsed = -1;
        this.value = 0;
    }

    update(dt, eyes) {
        dt = clamp(dt, 0, 0.1);
        if (this.elapsed < 0) {
            this.wait -= dt;
            if (this.wait > 0) return (this.value = 0);
            this.elapsed = 0;
            this.duration = clamp(eyes?.blinkDuration ?? 0.26, 0.18, 0.5);
        }
        this.elapsed += dt;
        const p = this.elapsed / this.duration;
        this.value = p < 0.32 ? smooth(clamp(p / 0.32))
            : p < 0.48 ? 1 : 1 - smooth(clamp((p - 0.48) / 0.52));
        if (p >= 1) {
            this.elapsed = -1;
            const gap = clamp(eyes?.blinkInterval ?? 4.5, 2, 8);
            this.wait = gap * (0.65 + this.random() * 0.7);
        }
        return this.value;
    }
}

export class PoseFace {
    constructor() {
        this.blend = 0;
        this.accents = {};
        this.mouth = 0;
        this.lids = 0;
    }

    /** Runs after the body mixer and before vrm.update; never writes bones. */
    apply(avatar, dt) {
        const em = avatar.vrm?.expressionManager;
        if (!em) return;
        this._marks(avatar);
        if (avatar.animationOwnsBlink) {
            // Authored facial animation owns these channels. Do not erase it.
            this.blend = this.mouth = this.lids = 0;
            this.accents = {};
            return;
        }
        const profile = avatar.poseFacialProfile;
        const mood = moodAt(profile, avatar.poseClipTime ?? 0);
        const preset = VRM_EMOTION_PRESETS[mood?.emotion];
        const room = profile?.moments ? restRoom(avatar) : 1;
        const strength = preset ? clamp(mood.strength ?? 0.45, 0, 0.7) * room : 0;
        const k = 1 - Math.exp(-(profile?.moments && room > 0.9 ? MOOD_DRIFT : 6) * clamp(dt, 0, 0.1));
        this.blend += (strength - this.blend) * k;
        const set = (name, value) => em.setValue(name, clamp(value));
        for (const name of Object.keys(VRM_EMOTION_PRESETS.neutral)) {
            const current = this.accents[name] || 0;
            const scale = WHOLE_CHANNELS.has(name) ? clamp(strength / 0.2) : strength;
            const next = current + ((preset?.[name] || 0) * scale - current) * k;
            this.accents[name] = next;
            // Read the base mood, not last frame's composed output: no feedback
            // or stuck smile, and a new response can change the mood mid-pose.
            // Preserve a same-frame accent (e.g. a speech-pause brow raise)
            // when the normal emotion pass refreshed this channel this frame.
            const base = Object.hasOwn(avatar.targetExpressions || {}, name)
                ? em.getValue(name) || 0 : avatar.currentExpressions?.[name] || 0;
            set(name, base * (1 - this.blend) + next);
        }
        const speaking = avatar.poseFaceSpeaking || avatar.mouthOpenness > 0.04;
        const open = speaking ? 0 : clamp(profile?.mouth?.open ?? 0, 0, 0.22);
        this.mouth += (open - this.mouth) * k;
        // Real speech takes priority immediately, even during a surprise pose.
        set('aa', Math.max(avatar.currentExpressions?.aa || 0, speaking ? 0 : this.mouth));

        // The pose's eyelids, or the emotion's (avatar.emotionLids), whichever is heavier.
        const lids = Math.max(clamp((mood?.lidClosure ?? profile?.eyes?.lidClosure ?? 0) * room, 0, 0.3),
            clamp(avatar.emotionLids ?? 0, 0, EMOTION_LID_MAX));
        this.lids += (lids - this.lids) * k;
        if (this.lids > 0.001) {
            const names = avatar.microLifeOwnsBlink
                ? (em.blinkExpressionNames || ['blink', 'blinkLeft', 'blinkRight']) : ['blink'];
            // Prefer one channel family; adding combined blink and L/R doubles
            // morph weights on many VRM 0 exports.
            const paired = names.includes('blinkLeft') && names.includes('blinkRight')
                && em.getExpression?.('blinkLeft')?.binds?.length
                && em.getExpression?.('blinkRight')?.binds?.length;
            for (const name of paired ? ['blinkLeft', 'blinkRight'] : ['blink']) {
                set(name, Math.max(em.getValue(name) || 0, this.lids));
            }
        }
    }

    /**
     * A pose's marks: `marks: [{ at, mark }]` in its facial profile pops an expression (the ……
     * while she reads, a blush as she waves) when the clip reaches `at`, every loop.
     */
    _marks(avatar) {
        const profile = avatar.poseFacialProfile;
        const time = avatar.poseClipTime ?? 0;
        if (profile !== this.markProfile) {
            this.markProfile = profile;
            this.markTime = time - 1e-6;
        }
        if (time < this.markTime) this.markTime = -1e-6;    // the clip looped
        for (const { at, mark } of profile?.marks ?? []) {
            if (this.markTime < at && time >= at) avatar.pulseExpression?.(mark);
        }
        this.markTime = time;
    }
}
