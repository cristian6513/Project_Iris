import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

import {
    animationContextsFromDocument,
    resolveAnimationContext,
} from '../../face/js/animation_context_resolver.js';
import { AnimationDirector } from '../../face/js/animation_director.js';
import { TtsAudioPlayer } from '../../face/js/tts_audio_player.js';

const contextDocument = JSON.parse(await readFile(
    new URL('../../face/animations/contexts.json', import.meta.url),
    'utf8',
));
const contexts = animationContextsFromDocument(contextDocument);

test('Face preserves canonical and legacy animation semantics', () => {
    assert.deepEqual(resolveAnimationContext('saludar_feliz'), {
        requestedName: 'saludar_feliz',
        canonicalName: 'wave',
        animation: 'wave',
        movement: null,
        reason: '',
        adapted: false,
        surface: 'face',
    });
    assert.equal(resolveAnimationContext('custom_uploaded').animation, 'custom_uploaded');
});

test('Desktop Companion adapts room actions after receiving the original name', () => {
    const desk = resolveAnimationContext('go_computer', {
        surface: 'desktop_companion',
        contexts,
    });
    assert.equal(desk.requestedName, 'go_computer');
    assert.equal(desk.animation, 'think');
    assert.equal(desk.reason, 'no-room-desk');
    assert.equal(desk.adapted, true);

    const walk = resolveAnimationContext('walk_tour', {
        surface: 'desktop_companion',
        contexts,
    });
    assert.equal(walk.animation, 'walk_in_place');
    assert.equal(walk.movement, 'window-walk');

    const safeStationaryMappings = new Map([
        ['pc_seated_idle', 'think'],
        ['sit_chair', 'idle_casual'],
        ['lie_bed', 'idle_casual'],
        ['touch_books', 'look_around'],
    ]);
    for (const [requestedName, animation] of safeStationaryMappings) {
        const resolved = resolveAnimationContext(requestedName, {
            surface: 'desktop_companion',
            contexts,
        });
        assert.equal(resolved.animation, animation, requestedName);
        assert.equal(resolved.movement, null, requestedName);
    }
});

test('AnimationDirector uses live dependency getters after avatar replacement', async () => {
    const oldIdle = { trigger: () => false, playClipExternal: () => false };
    const played = [];
    const newIdle = {
        trigger: () => false,
        playClipExternal: (name) => {
            played.push(name);
            return true;
        },
    };
    let currentIdle = oldIdle;
    const director = new AnimationDirector({
        getIdle: () => currentIdle,
        getAvatarManager: () => null,
        logger: { info() {}, warn() {} },
    });
    currentIdle = newIdle;

    const result = await director.play('pensar');

    assert.equal(result.status, 'clip');
    assert.deepEqual(played, ['think']);
});

test('AnimationDirector keeps the procedural shake fallback', async () => {
    const fallback = [];
    const director = new AnimationDirector({
        getIdle: () => ({ trigger: () => false, playClipExternal: () => false }),
        getAvatarManager: () => ({ playAnimation: (name) => fallback.push(name) }),
        logger: { info() {}, warn() {} },
    });

    const result = await director.play('negar');

    assert.equal(result.animation, 'shake');
    assert.equal(result.status, 'procedural-avatar');
    assert.deepEqual(fallback, ['negar']);
});

test('AnimationDirector uses safe head tilt when packaged think clip is absent', async () => {
    const attempts = [];
    const idle = {
        trigger(name) {
            attempts.push(name);
            return name === 'head_tilt';
        },
        playClipExternal: () => false,
        reloadManifest: async () => {},
    };
    const director = new AnimationDirector({
        getIdle: () => idle,
        getAvatarManager: () => null,
        logger: { info() {}, warn() {} },
    });

    const result = await director.play('think');
    assert.equal(result.status, 'procedural-pose');
    assert.equal(result.fallback, 'head_tilt');
    assert.deepEqual(attempts.slice(-2), ['thinking_pose', 'head_tilt']);
});

test('AnimationDirector keeps wave visible when the VRMA clip cannot load', async () => {
    const attempts = [];
    const fallbacks = [];
    const idle = {
            trigger: (name) => {
                attempts.push(name);
                return name === 'subtle_nod';
            },
            playClipExternal: () => false,
            reloadManifest: async () => {},
        };
    const director = new AnimationDirector({
        getIdle: () => idle,
        getAvatarManager: () => ({
            playAnimation: (name) => fallbacks.push(name),
        }),
        logger: { info() {}, warn() {} },
    });

    const result = await director.play('wave');

    assert.equal(result.status, 'procedural-pose');
    assert.equal(result.fallback, 'subtle_nod');
    assert.deepEqual(attempts.slice(-2), ['wave_hand', 'subtle_nod']);
    assert.deepEqual(fallbacks, []);
});

test('TtsAudioPlayer handles viseme-only delivery without constructing audio', () => {
    const received = [];
    const synthetic = [];
    const player = new TtsAudioPlayer({
        getLipSync: () => ({ processVisemes: (items) => received.push(items) }),
        getSpeechAnimator: () => ({
            setSyntheticMouthEnabled: (enabled) => synthetic.push(enabled),
        }),
        audioFactory: () => {
            throw new Error('audio should not be constructed');
        },
    });
    const visemes = [{ time: 0, duration: 0.2, text: 'hola' }];

    assert.deepEqual(player.handle({ audio: '', visemes }), { status: 'visemes-only' });
    assert.deepEqual(received, [visemes]);
    assert.deepEqual(synthetic, [false]);
});

test('TtsAudioPlayer recognizes the exact provider-failure sentinel', () => {
    const received = [];
    const player = new TtsAudioPlayer({
        getLipSync: () => ({ processVisemes: (items) => received.push(items) }),
        getSpeechAnimator: () => ({ setSyntheticMouthEnabled() {} }),
        audioFactory: () => {
            throw new Error('sentinel must not construct audio');
        },
        logger: { warn() {} },
    });
    const visemes = [{ time: 0, text: 'fallback' }];

    const result = player.handle({
        audio: Buffer.from('FALLBACK_FAILED').toString('base64'),
        visemes,
    });

    assert.deepEqual(result, { status: 'provider-failed' });
    assert.deepEqual(received, [visemes]);
});

test('TtsAudioPlayer starts audio-reactive lip sync and releases the audio URL', () => {
    const calls = [];
    const revoked = [];
    const fakeAudio = {
        play() {
            calls.push('play');
            return Promise.resolve();
        },
        pause() {
            calls.push('pause');
        },
    };
    const lipSync = {
        stop() {
            calls.push('stop');
        },
        stopAudioReactive() {
            calls.push('stop-audio-reactive');
        },
        startAudioReactive(audio) {
            assert.equal(audio, fakeAudio);
            calls.push('start-audio-reactive');
        },
    };
    const player = new TtsAudioPlayer({
        getLipSync: () => lipSync,
        getSpeechAnimator: () => ({
            setSyntheticMouthEnabled: (enabled) => calls.push(`synthetic:${enabled}`),
        }),
        audioFactory: (url) => {
            assert.equal(url, 'blob:iris-tts');
            return fakeAudio;
        },
        urlApi: {
            createObjectURL: () => 'blob:iris-tts',
            revokeObjectURL: (url) => revoked.push(url),
        },
    });

    assert.deepEqual(player.handle({
        audio: Buffer.from('valid audio bytes').toString('base64'),
        visemes: [],
        mime: 'audio/mpeg',
    }), { status: 'play-requested' });
    assert.equal(player.audio, fakeAudio);
    assert.ok(calls.includes('synthetic:false'));

    fakeAudio.onplaying();
    assert.ok(calls.includes('start-audio-reactive'));

    fakeAudio.onended();
    assert.equal(player.audio, null);
    assert.equal(player.objectUrl, null);
    assert.deepEqual(revoked, ['blob:iris-tts']);
});
