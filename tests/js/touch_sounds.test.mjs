// Touch sounds (2026-11-27): the pop and the release of «Mimos», made with Web Audio; petting is silent.
// A recording fake AudioContext checks when sound plays, how loud it may get, and the settings.
import assert from 'node:assert/strict';
import test from 'node:test';
import {
    TOUCH_SOUND_CAP, TOUCH_SOUNDS_KEY, TouchSounds, setTouchSoundsEnabled, touchSoundLevel, touchSoundsEnabled,
} from '../../face/js/touch_sounds.js';

/** An AudioParam that remembers its automation and its largest scheduled value. */
class Param {
    constructor(value = 0) { this.value = value; this.events = []; }
    setValueAtTime(v, t) { this.events.push(['set', v, t]); return this; }
    linearRampToValueAtTime(v, t) { this.events.push(['linear', v, t]); return this; }
    exponentialRampToValueAtTime(v, t) { this.events.push(['exp', v, t]); return this; }
    setTargetAtTime(v, t, tau) { this.events.push(['target', v, t, tau]); return this; }
    cancelScheduledValues(t) { this.events.push(['cancel', t]); return this; }
    /** The largest value it is scheduled to reach (its resting value when nothing is scheduled). */
    get peak() {
        const scheduled = this.events.filter((e) => e[0] !== 'cancel').map((e) => e[1]);
        return scheduled.length ? Math.max(...scheduled) : this.value;
    }
}

class Node {
    constructor(ctx, kind) { Object.assign(this, { ctx, kind, outputs: [] }); ctx.nodes.push(this); }
    connect(target) { this.outputs.push(target); return target; }
    disconnect() { this.outputs = []; }
}

function fakeContext() {
    const made = [];
    class FakeAudioContext {
        constructor() {
            this.nodes = [];
            this.currentTime = 0;
            this.sampleRate = 8000;
            this.state = 'running';
            this.destination = new Node(this, 'destination');
            made.push(this);
        }
        createGain() { const n = new Node(this, 'gain'); n.gain = new Param(1); return n; }
        createOscillator() {
            const n = new Node(this, 'oscillator');
            n.frequency = new Param(440);
            n.start = (t) => { n.started = t; };
            n.stop = (t) => { n.stopped = t; };
            return n;
        }
        createDynamicsCompressor() { throw new Error('no compressor: Chrome adds make-up gain'); }
        resume() { this.state = 'running'; return Promise.resolve(); }
        close() { this.state = 'closed'; return Promise.resolve(); }
    }
    return { FakeAudioContext, made };
}

const kinds = (ctx, kind) => ctx.nodes.filter((n) => n.kind === kind);
/**
 * The loudest gain a sound's own envelope reaches: not the shared output (it stays at 1), nor the
 * release's vibrato depth (it drives a frequency, in Hz).
 */
const loudest = (ctx) => Math.max(...kinds(ctx, 'gain')
    .filter((g) => g !== ctx.nodes[1] && g.outputs.every((o) => o instanceof Node)).map((g) => g.gain.peak));

function setup(options = {}) {
    const { FakeAudioContext, made } = fakeContext();
    const sounds = new TouchSounds({
        AudioContextClass: FakeAudioContext, volume: () => 1, enabled: () => true, random: () => 0.5, ...options,
    });
    return { sounds, made, ctx: () => made[0] };
}

test('nothing is made before the first touch, and a pop is one short sine under the cap', () => {
    const { sounds, made, ctx } = setup();
    assert.equal(made.length, 0, 'no AudioContext until a touch (the browser allows sound after a click)');
    assert.equal(sounds.poke(1), true);
    assert.equal(made.length, 1);
    const [osc] = kinds(ctx(), 'oscillator');
    assert.equal(osc.type, 'sine');
    assert.ok(osc.stopped - osc.started <= 0.15, 'short');
    assert.ok(loudest(ctx()) <= TOUCH_SOUND_CAP + 1e-9, `at most the cap (${loudest(ctx())})`);
    assert.ok(Math.abs(loudest(ctx()) - TOUCH_SOUND_CAP) < 1e-9, 'and the cap at full volume and strength');
});

test('the room volume rules them: 0 is silence, the default about half, never more than the cap', () => {
    assert.equal(touchSoundLevel(0), 0);
    assert.equal(touchSoundLevel(Number.NaN), 0);
    assert.ok(Math.abs(touchSoundLevel(0.15) - 0.5) < 1e-9, 'the default ambient volume');
    assert.equal(touchSoundLevel(0.6), 1);
    assert.equal(touchSoundLevel(1), 1, 'louder settings do not go past the cap');
    const quiet = setup({ volume: () => 0 });
    assert.equal(quiet.sounds.poke(1), false);
    assert.equal(quiet.made.length, 0, 'muted: not even a context');
    const soft = setup({ volume: () => 0.15 });
    soft.sounds.poke(1);
    assert.ok(Math.abs(loudest(soft.ctx()) - TOUCH_SOUND_CAP * 0.5) < 1e-9);
});

test('the «Sonidos de mimos» switch: on unless switched off, saved per device', () => {
    const store = new Map();
    const storage = { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, v) };
    assert.equal(touchSoundsEnabled(storage), true, 'on by default');
    setTouchSoundsEnabled(false, storage);
    assert.equal(store.get(TOUCH_SOUNDS_KEY), 'off');
    assert.equal(touchSoundsEnabled(storage), false);
    const off = setup({ enabled: () => false });
    assert.equal(off.sounds.poke(1), false);
    assert.equal(off.sounds.release(1), false);
    assert.equal(off.made.length, 0);
    assert.equal(touchSoundsEnabled({ getItem: () => { throw new Error('private'); } }), true, 'storage errors leave them on');
});

test('quick pokes never stack: one pop every 60 ms at most, each one of a run softer, the last cut', () => {
    const { sounds, ctx } = setup();
    sounds.poke(1);
    assert.equal(sounds.poke(1), false, 'a second pop in the same instant is dropped');
    const peaks = [];
    for (let k = 1; k <= 6; k++) {
        ctx().currentTime = k * 0.12;
        assert.equal(sounds.poke(1), true);
        const gains = kinds(ctx(), 'gain').slice(1);
        peaks.push(gains.at(-1).gain.peak);
        assert.ok(gains.at(-2).gain.events.some((e) => e[0] === 'cancel'), 'the pop before gives way');
    }
    for (let i = 1; i < peaks.length; i++) assert.ok(peaks[i] <= peaks[i - 1] + 1e-12, 'softer and softer');
    assert.ok(peaks.at(-1) >= TOUCH_SOUND_CAP * 0.5 - 1e-9, 'but never below half');
    ctx().currentTime = 5;
    sounds.poke(1);
    assert.ok(Math.abs(kinds(ctx(), 'gain').at(-1).gain.peak - TOUCH_SOUND_CAP) < 1e-9, 'after a pause, full again');
});

test('a release is softer than a pop and follows the stretch; petting has no sound', () => {
    const { sounds, ctx } = setup();
    sounds.release(1);
    const strong = loudest(ctx());
    assert.ok(strong < TOUCH_SOUND_CAP && strong > TOUCH_SOUND_CAP * 0.5);
    sounds.release(0.2);
    assert.ok(kinds(ctx(), 'gain').at(-2).gain.peak < strong, 'a small stretch, a smaller sound');
    // The user heard a noise brush while petting (and softer takes) and chose silence (2026-11-27).
    assert.equal(sounds.pet, undefined, 'no petting sound');
});

test('«Reducir movimiento»: the pop and the release are softer', () => {
    const { sounds, ctx } = setup({ reduced: () => true });
    sounds.poke(1);
    assert.ok(loudest(ctx()) < TOUCH_SOUND_CAP * 0.7, 'softer pop');
    sounds.dispose();
    assert.equal(ctx().state, 'closed');
});

test('without Web Audio nothing breaks', () => {
    const sounds = new TouchSounds({ AudioContextClass: undefined, volume: () => 1, enabled: () => true });
    assert.equal(sounds.poke(1), false);
    const failing = new TouchSounds({ AudioContextClass: class { constructor() { throw new Error('blocked'); } }, volume: () => 1, enabled: () => true });
    const warn = console.warn;
    console.warn = () => {};
    try { assert.equal(failing.poke(1), false); } finally { console.warn = warn; }
    assert.equal(failing.release(1), false, 'and it does not try again');
});
