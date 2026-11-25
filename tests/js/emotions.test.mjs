// Iris's twelve emotions (2026-11-24): one list for the agent, the face and the body.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { EMOTIONS, EMOTION_NAMES, canonicalEmotion, emotionBase } from '../../face/js/emotions.js';
import { EMOTION_LIDS, EMOTION_LID_MAX, PoseFace, VRM_EMOTION_PRESETS } from '../../face/js/pose_face.js';

const FIRST_SIX = ['neutral', 'feliz', 'triste', 'pensativa', 'sorprendida', 'enojada'];
const NEWER = ['avergonzada', 'cariñosa', 'emocionada', 'confundida', 'nerviosa', 'somnolienta'];

test('the face knows the same twelve emotions Core lets the agent name', () => {
    const core = readFileSync(new URL('../../core/emotions.py', import.meta.url), 'utf8');
    const tuple = core.match(/IRIS_EMOTIONS[^=]*=\s*\(([^)]*)\)/)?.[1];
    assert.ok(tuple, 'IRIS_EMOTIONS found in core/emotions.py');
    const names = [...tuple.matchAll(/"([^"]+)"/g)].map((m) => m[1]);
    assert.deepEqual(names, [...FIRST_SIX, ...NEWER]);
    assert.deepEqual(EMOTION_NAMES, names);
    assert.deepEqual(Object.keys(VRM_EMOTION_PRESETS), names);
});

test('each emotion has a label, an energy and a base among the first six', () => {
    for (const name of EMOTION_NAMES) {
        const e = EMOTIONS[name];
        assert.ok(e.label && e.energy >= 0 && e.energy <= 100, name);
        assert.ok(FIRST_SIX.includes(e.base), `${name} → ${e.base}`);
    }
    for (const name of FIRST_SIX) assert.equal(emotionBase(name), name);
    assert.equal(emotionBase('cariñosa'), 'feliz');
    assert.equal(emotionBase('somnolienta'), 'pensativa');
});

test('any spelling the agent may use lands on one of the twelve', () => {
    assert.equal(canonicalEmotion('cariñosa'), 'cariñosa');
    assert.equal(canonicalEmotion('carinosa'), 'cariñosa');
    assert.equal(canonicalEmotion('  CARIÑOSA '), 'cariñosa');
    assert.equal(canonicalEmotion('avergonzado'), 'avergonzada');
    assert.equal(canonicalEmotion('tímida'), 'avergonzada');
    assert.equal(canonicalEmotion('con sueño'), 'somnolienta');
    assert.equal(canonicalEmotion('sorprendido'), 'sorprendida');
    assert.equal(canonicalEmotion('happy-ish'), 'neutral');
    assert.equal(canonicalEmotion(undefined), 'neutral');
});

test('every mix drives the same channels, so moods decay cleanly between them', () => {
    const channels = Object.keys(VRM_EMOTION_PRESETS.neutral);
    for (const [name, preset] of Object.entries(VRM_EMOTION_PRESETS)) {
        assert.deepEqual(Object.keys(preset), channels, name);
        for (const value of Object.values(preset)) assert.ok(value >= 0 && value <= 1, name);
    }
    // The first six keep their tuned mixes; the newer ones mix several VRM presets.
    assert.equal(VRM_EMOTION_PRESETS.feliz.happy, 0.9);
    assert.equal(VRM_EMOTION_PRESETS.triste.sad, 0.8);
    for (const name of NEWER) {
        const vrmNotes = ['happy', 'sad', 'angry', 'surprised', 'relaxed'].filter((c) => VRM_EMOTION_PRESETS[name][c] > 0);
        assert.ok(vrmNotes.length >= 2, `${name} mixes ${vrmNotes}`);
    }
    for (const [name, lids] of Object.entries(EMOTION_LIDS)) {
        assert.ok(EMOTION_NAMES.includes(name) && lids > 0 && lids <= EMOTION_LID_MAX, name);
    }
});

function poseFixture() {
    const values = {}, pulses = [], face = new PoseFace();
    const avatar = {
        currentExpressions: { relaxed: 0.3 }, mouthOpenness: 0,
        pulseExpression: (name) => pulses.push(name),
        vrm: { expressionManager: { setValue: (n, v) => { values[n] = v; }, getValue: (n) => values[n] || 0 } },
    };
    const step = (dt = 1 / 60) => { values.blink = 0; face.apply(avatar, dt); };
    return { avatar, values, pulses, step };
}

test('a pose\'s marks pop when its clip reaches them, again on every loop', () => {
    const { avatar, pulses, step } = poseFixture();
    avatar.poseFacialProfile = { emotion: null, strength: 0, marks: [{ at: 3.5, mark: 'markDots' }], eyes: {} };
    for (const t of [0.5, 2, 3.4]) { avatar.poseClipTime = t; step(); }
    assert.deepEqual(pulses, []);
    avatar.poseClipTime = 3.6; step();
    avatar.poseClipTime = 5; step();
    assert.deepEqual(pulses, ['markDots']);
    avatar.poseClipTime = 0.2; step();        // looped
    avatar.poseClipTime = 3.55; step();
    assert.deepEqual(pulses, ['markDots', 'markDots']);
});

test('a sleepy rest pose shows its ZZZ whole and lowers her eyelids', () => {
    const { avatar, values, step } = poseFixture();
    avatar.poseFacialProfile = { emotion: 'somnolienta', strength: 0.55, eyes: { lidClosure: 0.3 } };
    avatar.poseClipTime = 1;
    for (let i = 0; i < 300; i++) step();
    assert.ok(values.zzz > 0.95, `ZZZ ${values.zzz}`);
    assert.ok(values.relaxed > 0.3, 'and her face relaxes');
    assert.ok(values.blink > 0.25, `eyelids ${values.blink}`);
});

test('the agent\'s emotion lowers her eyelids on any face', () => {
    const { avatar, values, step } = poseFixture();
    avatar.emotionLids = 0.5;                  // somnolienta, from avatar.js
    for (let i = 0; i < 300; i++) step();
    assert.ok(values.blink > 0.45 && values.blink <= EMOTION_LID_MAX, `${values.blink}`);
});

test('the avatar panel names what the face has, vowels together, the wink on either kind of face', async () => {
    const { faceGestures } = await import('../../face/js/emotions.js');
    assert.deepEqual(faceGestures(['aa', 'ih', 'ou', 'ee', 'oh', 'blink', 'happy', 'blinkLeft', 'blinkRight']),
        ['parpadeo', 'vocales', 'feliz', 'guiño']);
    assert.deepEqual(faceGestures(['aa', 'blink', 'smirk', 'wink', 'zzz']), ['parpadeo', 'boca', 'ZZZ', 'sonrisa de lado', 'guiño']);
    assert.deepEqual(faceGestures([]), []);
});
