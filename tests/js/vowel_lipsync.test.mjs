// Vowel shapes while she speaks (2026-11-24): the text she says gives her mouth a, e, i, o, u
// instead of one «a» for everything, on VRM and MMD avatars alike.
import assert from 'node:assert/strict';
import test from 'node:test';
import { VISEMES, layout, mouthAt, vowelUnits } from '../../face/js/vowel_track.js';
import { LipSyncEngine } from '../../face/js/lipsync.js';

const spell = (text) => vowelUnits(text).map((u) => (u.kind === 'vowel' ? u.shape.toUpperCase()
    : u.kind === 'closed' ? '|' : u.kind === 'space' ? '_' : u.kind === 'pause' ? '.' : u.shape)).join('');

test('Spanish spelling becomes vowels, closed lips and pauses', () => {
    assert.equal(spell('Hola'), 'oOaA');
    assert.equal(spell('¿Qué hora es?'), 'eE_oOaA_Ee');               // the u of «que» is silent
    assert.equal(spell('Está bien. ¿Qué necesitas?'), 'EaaA_|IEe.eE_eEeEiIaAa');
    assert.equal(spell('pingüino'), '|IuuUIoO');                       // ü keeps its sound
    assert.equal(spell('hoy y mañana'), 'oOI_I_|AaAaA');               // y alone or at the end is «i»
    assert.equal(spell('  ...  '), '');
});

test('a laid-out track says which vowel is on her lips, fading into the next', () => {
    const track = layout(vowelUnits('Hola Iris'));
    assert.ok(Math.abs(track.at(-1).end - 1) < 1e-9);
    const shapes = [0.15, 0.4, 0.62, 0.95].map((p) => mouthAt(track, p).shape);
    assert.ok(shapes[0].oh > 0.8, 'o');
    assert.ok(shapes[1].aa > 0.9, 'a');
    assert.ok(shapes[2].ih > 0.8, 'i');
    assert.ok(shapes[3].ih > 0.8, 'final i');
    for (const shape of shapes) for (const key of Object.keys(shape)) assert.ok(VISEMES.includes(key));
    // A closed consonant nearly shuts the mouth; a pause lets nothing through.
    const m = layout(vowelUnits('ama'));
    assert.ok(mouthAt(m, 0.5).open < 0.2);
    assert.equal(mouthAt(layout([]), 0.5), null);
});

// A fake avatar records what lip sync sends.
function engine() {
    const sent = { open: [], shape: [] };
    const avatar = { setMouthOpen: (v) => sent.open.push(v), setMouthShape: (s) => sent.shape.push(s && { ...s }) };
    return { lip: new LipSyncEngine(avatar), sent };
}

test('with word timings (Edge TTS), each word moves through its own vowels', () => {
    const { lip, sent } = engine();
    const realNow = performance.now;
    let now = 1000;
    performance.now = () => now;
    try {
        lip.processVisemes([{ time: 0, duration: 0.5, text: 'Hola' }, { time: 0.6, duration: 0.4, text: 'Iris' }]);
        const seen = new Set();
        for (let t = 0; t <= 1.0; t += 0.02) {
            now = 1000 + t * 1000;
            lip.update(1 / 60);
            const shape = sent.shape.at(-1);
            if (shape) for (const [k, v] of Object.entries(shape)) if (v > 0.5) seen.add(k);
        }
        assert.ok(seen.has('oh') && seen.has('aa') && seen.has('ih'), [...seen].join(','));
        lip.stop();
        assert.equal(sent.shape.at(-1), null, 'stopping hands the mouth back');
    } finally {
        performance.now = realNow;
    }
});

test('without timings, the audio leads and the sentence gives the vowels', () => {
    const { lip, sent } = engine();
    const audio = { currentTime: 0, duration: 1 };
    lip.audioReactive = true;
    lip.analyser = { frequencyBinCount: 8, getByteFrequencyData: (buf) => buf.fill(200) };
    lip.audioTrack = layout(vowelUnits('uuu iii'));
    lip.audioElement = audio;
    audio.currentTime = 0.15;
    for (let i = 0; i < 20; i++) lip.update(1 / 60);
    assert.ok(sent.shape.at(-1).ou > 0.6, JSON.stringify(sent.shape.at(-1)));
    audio.currentTime = 0.85;
    for (let i = 0; i < 20; i++) lip.update(1 / 60);
    assert.ok(sent.shape.at(-1).ih > 0.6, JSON.stringify(sent.shape.at(-1)));
    assert.ok(sent.open.at(-1) > 0.3, 'the audio level still opens the mouth');
});

test('the audio player hands the spoken text to audio-led lip sync', async () => {
    const source = (await import('node:fs')).readFileSync(new URL('../../face/js/tts_audio_player.js', import.meta.url), 'utf8');
    assert.match(source, /startAudioReactive\?\.\(audio, \{ text: typeof payload\.text === 'string' \? payload\.text : '' \}\)/);
    const agent = (await import('node:fs')).readFileSync(new URL('../../core/agent.py', import.meta.url), 'utf8');
    assert.match(agent, /"text":\s+spoken_text,/);
});
