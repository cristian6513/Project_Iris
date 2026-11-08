/**
 * IRIS ambient sound: a soft procedural bed for the room (Web Audio, no audio files).
 *
 * Only filtered noise, never a steady tone: the old 55 Hz hum and 3.2 kHz «presence» sines
 * read as a whistle (qa11), and their 2 s noise loop clicked at every turn. Each room has its
 * own colour (rain and city for Cyberpunk, server air for Hacker, a breeze for Sakura, a quiet
 * study for Clásico). The noise loops are seamless and every change ramps from the current
 * value, so nothing pops.
 */

// rumble: low noise under everything; air: the band that gives each room its texture;
// sway: how much the air breathes (a sub-audible LFO, ~one breath every 12 s).
export const ROOM_SOUNDS = Object.freeze({
    cyber: Object.freeze({ rumble: { cutoff: 170, gain: 0.5 }, air: { low: 650, high: 5200, gain: 0.3 }, sway: 0.3 }),
    hacker: Object.freeze({ rumble: { cutoff: 120, gain: 0.7 }, air: { low: 1100, high: 3600, gain: 0.14 }, sway: 0.12 }),
    sakura: Object.freeze({ rumble: { cutoff: 230, gain: 0.32 }, air: { low: 320, high: 2300, gain: 0.3 }, sway: 0.42 }),
    classic: Object.freeze({ rumble: { cutoff: 140, gain: 0.42 }, air: { low: 420, high: 1700, gain: 0.13 }, sway: 0.16 }),
});

const SWAY_HZ = 0.083;
const FADE_IN = 2.5;
const FADE_OUT = 1;
const GLIDE = 0.8; // seconds for a room change to settle

/** Seamless noise: the loop's tail fades into its head, so the seam has no jump. */
export function noiseSamples(length, kind, random = Math.random, fade = Math.floor(length / 8)) {
    const raw = new Float32Array(length + fade);
    let last = 0;
    for (let i = 0; i < raw.length; i++) {
        const white = random() * 2 - 1;
        if (kind === 'brown') {
            last = (last + 0.02 * white) / 1.02;
            raw[i] = last * 3.5;
        } else raw[i] = white * 0.5;
    }
    const data = raw.slice(0, length);
    for (let i = 0; i < fade; i++) {
        const t = i / fade;
        // Equal-power blend: uncorrelated noise keeps its level through the seam.
        data[i] = raw[length + i] * Math.cos(t * Math.PI / 2) + raw[i] * Math.sin(t * Math.PI / 2);
    }
    return data;
}

export class AmbientAudio {
    constructor({ storage = () => globalThis.localStorage } = {}) {
        this.ctx = null;
        this.enabled = false;
        this.masterGain = null;
        this.layers = null;
        this.volume = 0.15; // Low ambient volume
        this.room = 'cyber';
        try {
            const rawVolume = storage()?.getItem('IRIS.ambientVolume');
            const savedVolume = rawVolume === null || rawVolume === undefined ? Number.NaN : Number(rawVolume);
            if (Number.isFinite(savedVolume) && savedVolume >= 0 && savedVolume <= 1) this.volume = savedVolume;
            const savedRoom = storage()?.getItem('IRIS.roomTheme');
            if (Object.hasOwn(ROOM_SOUNDS, savedRoom ?? '')) this.room = savedRoom;
        } catch {}
        this._storage = storage;
    }

    async init(AudioContextClass = globalThis.AudioContext || globalThis.webkitAudioContext) {
        try {
            this.ctx = new AudioContextClass();
            this.masterGain = this.ctx.createGain();
            this.masterGain.gain.value = this.volume;
            this.masterGain.connect(this.ctx.destination);
        } catch (e) {
            console.warn('🔇 Web Audio not available:', e.message);
        }
    }

    enable() {
        if (!this.ctx) return;
        // Browsers start audio suspended until a click; this toggle is that click.
        if (this.ctx.state === 'suspended') this.ctx.resume?.()?.catch?.(() => {});
        this.enabled = true;
        this._start();
    }

    disable() {
        this.enabled = false;
        this._stop();
    }

    toggle() {
        this.enabled ? this.disable() : this.enable();
        return this.enabled;
    }

    setVolume(v) {
        this.volume = Math.max(0, Math.min(1, v));
        if (this.masterGain) this._glide(this.masterGain.gain, this.volume, 0.08);
        try { this._storage()?.setItem('IRIS.ambientVolume', String(this.volume)); } catch {}
    }

    /** Follow the room theme; while playing, the sound glides to the new colour. */
    setRoomTheme(name) {
        if (!Object.hasOwn(ROOM_SOUNDS, name ?? '')) return false;
        this.room = name;
        if (this.layers) this._tune(ROOM_SOUNDS[name], GLIDE);
        return true;
    }

    // ── Graph ──────────────────────────────────────────

    _start() {
        if (this.layers) return;
        const ctx = this.ctx;
        const sound = ROOM_SOUNDS[this.room];
        const bus = ctx.createGain();
        bus.connect(this.masterGain);

        const rumbleSource = this._loop('brown', 7);
        const rumbleFilter = ctx.createBiquadFilter();
        rumbleFilter.type = 'lowpass';
        const rumbleGain = ctx.createGain();
        rumbleSource.connect(rumbleFilter).connect(rumbleGain).connect(bus);

        const airSource = this._loop('white', 9);
        const airHigh = ctx.createBiquadFilter();
        airHigh.type = 'highpass';
        const airLow = ctx.createBiquadFilter();
        airLow.type = 'lowpass';
        const airGain = ctx.createGain();
        airSource.connect(airHigh).connect(airLow).connect(airGain).connect(bus);

        // The air breathes: a sub-audible sine moves its level a little.
        const sway = ctx.createOscillator();
        sway.type = 'sine';
        sway.frequency.value = SWAY_HZ;
        const swayDepth = ctx.createGain();
        sway.connect(swayDepth).connect(airGain.gain);

        this.layers = { bus, rumbleSource, rumbleFilter, rumbleGain, airSource, airHigh, airLow, airGain, sway, swayDepth };
        this._tune(sound, 0);
        const now = ctx.currentTime;
        bus.gain.cancelScheduledValues?.(now);
        bus.gain.setValueAtTime(0, now);
        bus.gain.linearRampToValueAtTime(1, now + FADE_IN);
        for (const node of [rumbleSource, airSource, sway]) node.start(now);
    }

    _stop() {
        const layers = this.layers;
        if (!layers) return;
        this.layers = null;
        const now = this.ctx.currentTime;
        const gain = layers.bus.gain;
        gain.cancelScheduledValues?.(now);
        gain.setValueAtTime(gain.value, now);
        gain.linearRampToValueAtTime(0, now + FADE_OUT);
        for (const node of [layers.rumbleSource, layers.airSource, layers.sway]) {
            try { node.stop(now + FADE_OUT + 0.05); } catch { /* already stopped */ }
        }
        setTimeout(() => { try { layers.bus.disconnect(); } catch {} }, (FADE_OUT + 0.2) * 1000);
    }

    _tune(sound, seconds) {
        const l = this.layers;
        this._glide(l.rumbleFilter.frequency, sound.rumble.cutoff, seconds);
        this._glide(l.rumbleGain.gain, sound.rumble.gain, seconds);
        this._glide(l.airHigh.frequency, sound.air.low, seconds);
        this._glide(l.airLow.frequency, sound.air.high, seconds);
        this._glide(l.airGain.gain, sound.air.gain, seconds);
        this._glide(l.swayDepth.gain, sound.air.gain * sound.sway, seconds);
    }

    _glide(param, value, seconds) {
        const now = this.ctx.currentTime;
        param.cancelScheduledValues?.(now);
        if (seconds <= 0) {
            param.setValueAtTime(value, now);
            return;
        }
        param.setValueAtTime(param.value, now);
        param.linearRampToValueAtTime(value, now + seconds);
    }

    _loop(kind, seconds) {
        const length = Math.floor(this.ctx.sampleRate * seconds);
        const buffer = this.ctx.createBuffer(1, length, this.ctx.sampleRate);
        buffer.getChannelData(0).set(noiseSamples(length, kind));
        const source = this.ctx.createBufferSource();
        source.buffer = buffer;
        source.loop = true;
        return source;
    }
}
