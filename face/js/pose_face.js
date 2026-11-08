/** Temporary facial accents for body-only poses, shared by Face and Companion. */
export const VRM_EMOTION_PRESETS = {
    neutral:     { happy: 0, sad: 0, angry: 0, surprised: 0, relaxed: 0.3 },
    feliz:       { happy: 0.9, sad: 0, angry: 0, surprised: 0, relaxed: 0 },
    triste:      { happy: 0, sad: 0.8, angry: 0, surprised: 0, relaxed: 0 },
    pensativa:   { happy: 0, sad: 0.2, angry: 0, surprised: 0, relaxed: 0.5 },
    sorprendida: { happy: 0, sad: 0, angry: 0, surprised: 0.9, relaxed: 0 },
    enojada:     { happy: 0, sad: 0, angry: 0.8, surprised: 0, relaxed: 0 },
};

const clamp = (v, min = 0, max = 1) => Math.min(max, Math.max(min, Number.isFinite(v) ? v : min));
const smooth = v => v * v * (3 - 2 * v);
const MOOD_DRIFT = 1.6;   // a mood at rest comes and goes slowly; a gesture's arrives with the gesture
const AGENT_MOOD = ['happy', 'sad', 'angry', 'surprised'];

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
            const next = current + ((preset?.[name] || 0) * strength - current) * k;
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

        const lids = (mood?.lidClosure ?? profile?.eyes?.lidClosure ?? 0) * room;
        this.lids += (clamp(lids, 0, 0.3) - this.lids) * k;
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
}
