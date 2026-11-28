/**
 * Touch sounds (2026-11-27): a soft pop when a poked cheek springs back and a light springy note
 * when a pulled cheek is let go. All made with Web Audio: no sound files, nothing downloaded. Used
 * by touch_reactions.js in the room and the Companion. Petting is silent: the user heard the noise
 * brush it had first (and softer takes of it) and chose the pops and the releases alone.
 *
 * - Short and soft: no sound peaks above TOUCH_SOUND_CAP (gain) at full volume. A new pop cuts
 *   the one before (they never stack, and come one every 60 ms at most) and a quick run of them
 *   gets softer, so poking her many times is not noisy. No compressor: Chrome's adds its own
 *   make-up gain, and made them almost three times louder (measured offline, 2026-11-27).
 * - Settings: `volume()` is the room's «Volumen» slider (0 is silence), `enabled()` the «Sonidos
 *   de mimos» switch (saved per device as IRIS.touchSounds, on unless switched off), `reduced()`
 *   «Reducir movimiento»: then the pop and the release are softer.
 * - The AudioContext is made on the first touch (a click, so the browser lets it play).
 */
export const TOUCH_SOUND_CAP = 0.2;
export const TOUCH_SOUNDS_KEY = 'IRIS.touchSounds';
const POKE_GAP = 0.06;        // seconds between two pops, at least
const STREAK_SECONDS = 1.2;   // pops closer than this are a run, each one softer
const REDUCED_SHARE = 0.6;    // «Reducir movimiento»: this share of the level
const RELEASE_SHARE = 0.75;   // the release's share of the level, at full stretch

const clamp = (v, min = 0, max = 1) => Math.min(max, Math.max(min, Number.isFinite(v) ? v : min));

/** Whether touch sounds are on (saved per device; on unless switched off). */
export function touchSoundsEnabled(storage = globalThis.localStorage) {
    try { return storage?.getItem(TOUCH_SOUNDS_KEY) !== 'off'; } catch { return true; }
}

export function setTouchSoundsEnabled(on, storage = globalThis.localStorage) {
    try { storage?.setItem(TOUCH_SOUNDS_KEY, on ? 'on' : 'off'); } catch { /* private mode */ }
    return Boolean(on);
}

/**
 * The room's volume slider (0..1) as a share of the cap: 0 is silence, the default 0.15 about
 * half, 0.6 and up all of it.
 */
export function touchSoundLevel(volume) {
    const v = Number(volume);
    return Number.isFinite(v) && v > 0 ? Math.min(1, Math.sqrt(v / 0.6)) : 0;
}

/** Attack to `peak`, then an exponential fall to silence by `end` seconds after `t`. */
function envelope(param, t, peak, attack, end) {
    param.setValueAtTime(0, t);
    param.linearRampToValueAtTime(peak, t + attack);
    param.exponentialRampToValueAtTime(Math.max(1e-4, peak * 1e-3), t + end);
    param.setValueAtTime(0, t + end + 0.005);
}

export class TouchSounds {
    constructor({ volume = () => 0.15, enabled = () => touchSoundsEnabled(), reduced = () => false,
        AudioContextClass = globalThis.AudioContext ?? globalThis.webkitAudioContext,
        random = Math.random } = {}) {
        Object.assign(this, { volume, enabled, reduced, AudioContextClass, random });
        this.ctx = null;
        this.out = null;
        this._lastPoke = -Infinity;
        this._streak = 0;
    }

    _ask(read, fallback) {
        try { return read(); } catch { return fallback; }
    }

    get isReduced() { return Boolean(this._ask(this.reduced, false)); }

    /** The peak gain a sound may use now; 0 plays nothing. */
    level() {
        if (!this._ask(this.enabled, false)) return 0;
        return TOUCH_SOUND_CAP * touchSoundLevel(this._ask(this.volume, 0)) * (this.isReduced ? REDUCED_SHARE : 1);
    }

    _context() {
        if (!this.ctx) {
            if (!this.AudioContextClass) return null;
            try {
                this.ctx = new this.AudioContextClass();
                this.out = this.ctx.createGain();
                this.out.connect(this.ctx.destination);
            } catch (error) {
                console.warn('[touch] sin sonidos de mimos:', error?.message ?? error);
                this.AudioContextClass = null;
                this.ctx = null;
                return null;
            }
        }
        if (this.ctx.state === 'suspended') this.ctx.resume?.()?.catch?.(() => {});
        return this.ctx;
    }

    /** A soft pop: a short falling «blop». `strength` 0..1. True when it played. */
    poke(strength = 1) {
        const level = this.level() * clamp(strength);
        if (!(level > 0)) return false;
        const ctx = this._context();
        if (!ctx) return false;
        const t = ctx.currentTime;
        if (t - this._lastPoke < POKE_GAP) return false;
        this._streak = t - this._lastPoke < STREAK_SECONDS ? this._streak + 1 : 0;
        this._lastPoke = t;
        const peak = level * Math.max(0.5, 0.86 ** this._streak);
        // Each pop a little different, so a run of them does not sound mechanical.
        const pitch = 1 + (this.random() - 0.5) * 0.12;
        const osc = ctx.createOscillator(), gain = ctx.createGain();
        // The pop before, if it still sounds, gives way at once.
        this._lastPop?.gain.cancelScheduledValues(t);
        this._lastPop?.gain.setTargetAtTime(0, t, 0.008);
        this._lastPop = gain;
        osc.type = 'sine';
        osc.frequency.setValueAtTime(560 * pitch, t);
        osc.frequency.exponentialRampToValueAtTime(190 * pitch, t + 0.09);
        envelope(gain.gain, t, peak, 0.005, 0.12);
        osc.connect(gain);
        gain.connect(this.out);
        osc.start(t);
        osc.stop(t + 0.14);
        return true;
    }

    /** A pulled cheek let go: a light rising note with a short wobble, as big as the stretch. */
    release(stretch = 1) {
        const level = this.level() * RELEASE_SHARE * (0.5 + 0.5 * clamp(stretch));
        if (!(level > 0)) return false;
        const ctx = this._context();
        if (!ctx) return false;
        const t = ctx.currentTime;
        const osc = ctx.createOscillator(), gain = ctx.createGain();
        const wobble = ctx.createOscillator(), depth = ctx.createGain();
        osc.type = 'sine';
        osc.frequency.setValueAtTime(320, t);
        osc.frequency.exponentialRampToValueAtTime(640, t + 0.05);
        osc.frequency.exponentialRampToValueAtTime(540, t + 0.22);
        // The wobble of a cheek settling: a fast vibrato that dies away.
        wobble.type = 'sine';
        wobble.frequency.setValueAtTime(19, t);
        depth.gain.setValueAtTime(45, t);
        depth.gain.exponentialRampToValueAtTime(1, t + 0.22);
        wobble.connect(depth);
        depth.connect(osc.frequency);
        envelope(gain.gain, t, level, 0.006, 0.24);
        osc.connect(gain);
        gain.connect(this.out);
        for (const node of [osc, wobble]) { node.start(t); node.stop(t + 0.26); }
        return true;
    }

    dispose() {
        try { this.ctx?.close?.(); } catch { /* already closed */ }
        this.ctx = null;
        this.out = null;
    }
}
