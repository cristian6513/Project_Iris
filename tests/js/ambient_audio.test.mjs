// Ambient sound (2026-10-13, qa11 user QA: «el sonido ambiente es como un pitido»). The old bed
// was a 55 Hz and a 3.2 kHz sine over a 2 s noise loop that clicked at every turn; the new one
// is filtered noise only, per room, with seamless loops and ramps from the current value.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { AmbientAudio, ROOM_SOUNDS, noiseSamples } from '../../face/js/ambient_audio.js';

function seeded(seed = 7) {
    let state = seed;
    return () => {
        state = (state * 1664525 + 1013904223) % 4294967296;
        return state / 4294967296;
    };
}

class FakeParam {
    constructor(value = 1) { this.value = value; this.events = []; }
    setValueAtTime(value, time) { this.events.push(['set', value, time]); this.value = value; }
    linearRampToValueAtTime(value, time) { this.events.push(['ramp', value, time]); this.value = value; }
    cancelScheduledValues(time) { this.events.push(['cancel', time]); }
}

class FakeNode {
    constructor(kind) { this.kind = kind; this.outputs = []; this.started = []; this.stopped = []; }
    connect(target) { this.outputs.push(target); return target; }
    disconnect() { this.outputs = []; }
    start(time) { this.started.push(time); }
    stop(time) { this.stopped.push(time); }
}

class FakeContext {
    constructor() { this.currentTime = 10; this.sampleRate = 8000; this.state = 'running'; this.nodes = []; this.destination = new FakeNode('destination'); }
    track(node) { this.nodes.push(node); return node; }
    createGain() { const n = new FakeNode('gain'); n.gain = new FakeParam(1); return this.track(n); }
    createBiquadFilter() { const n = new FakeNode('filter'); n.frequency = new FakeParam(350); return this.track(n); }
    createOscillator() { const n = new FakeNode('oscillator'); n.frequency = new FakeParam(440); return this.track(n); }
    createBufferSource() { return this.track(new FakeNode('source')); }
    createBuffer(channels, length) { const data = new Float32Array(length); return { length, getChannelData: () => data }; }
}

async function playing(room = 'cyber') {
    const audio = new AmbientAudio({ storage: () => null });
    await audio.init(FakeContext);
    audio.setRoomTheme(room);
    audio.enable();
    return audio;
}

test('the bed is noise only: every oscillator is a sub-audible sway, never a tone', async () => {
    for (const room of Object.keys(ROOM_SOUNDS)) {
        const audio = await playing(room);
        const oscillators = audio.ctx.nodes.filter((node) => node.kind === 'oscillator');
        assert.equal(oscillators.length, 1, room);
        assert.ok(oscillators.every((node) => node.frequency.value < 1), room);
        assert.equal(audio.ctx.nodes.filter((node) => node.kind === 'source').length, 2, room);
    }
});

test('noise loops have no jump at the seam', () => {
    for (const kind of ['brown', 'white']) {
        const data = noiseSamples(4000, kind, seeded());
        let largest = 0;
        for (let i = 1; i < data.length; i++) largest = Math.max(largest, Math.abs(data[i] - data[i - 1]));
        assert.ok(Math.abs(data[0] - data[data.length - 1]) <= largest, kind);
    }
    // Brown noise moves slowly, so its old raw loop end showed as a click.
    const brown = noiseSamples(4000, 'brown', seeded(3));
    let typical = 0;
    for (let i = 1; i < brown.length; i++) typical += Math.abs(brown[i] - brown[i - 1]);
    assert.ok(Math.abs(brown[0] - brown.at(-1)) < (typical / brown.length) * 8);
});

test('starting, stopping and changing room ramp from the current value', async () => {
    const audio = await playing('cyber');
    const bus = audio.layers.bus.gain;
    assert.deepEqual(bus.events.slice(-2), [['set', 0, 10], ['ramp', 1, 12.5]]);
    audio.enable(); // a second click while playing adds nothing
    assert.equal(audio.ctx.nodes.filter((node) => node.kind === 'source').length, 2);

    const before = audio.ctx.nodes.length;
    const cutoff = audio.layers.rumbleFilter.frequency;
    assert.equal(audio.setRoomTheme('hacker'), true);
    assert.deepEqual(cutoff.events.slice(-2), [['set', ROOM_SOUNDS.cyber.rumble.cutoff, 10], ['ramp', ROOM_SOUNDS.hacker.rumble.cutoff, 10.8]]);
    assert.equal(audio.ctx.nodes.length, before, 'a room change glides, it does not rebuild');
    assert.equal(audio.setRoomTheme('nope'), false);

    const layers = audio.layers;
    audio.disable();
    assert.equal(audio.layers, null);
    assert.deepEqual(layers.bus.gain.events.slice(-3), [['cancel', 10], ['set', 1, 10], ['ramp', 0, 11]]);
    assert.ok([layers.rumbleSource, layers.airSource, layers.sway].every((node) => node.stopped.length === 1));
});

test('volume glides and the saved room and volume come back', async () => {
    const saved = new Map([['IRIS.ambientVolume', '0.4'], ['IRIS.roomTheme', 'sakura']]);
    const storage = { getItem: (key) => saved.get(key) ?? null, setItem: (key, value) => saved.set(key, value) };
    const audio = new AmbientAudio({ storage: () => storage });
    assert.equal(audio.volume, 0.4);
    assert.equal(audio.room, 'sakura');
    await audio.init(FakeContext);
    audio.setVolume(2);
    assert.equal(audio.volume, 1);
    assert.deepEqual(audio.masterGain.gain.events.slice(-1), [['ramp', 1, 10.08]]);
    assert.equal(saved.get('IRIS.ambientVolume'), '1');
});
