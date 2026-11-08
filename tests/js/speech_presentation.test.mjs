import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { SpeechPresentation } from '../../face/js/speech_presentation.js';

function clock() {
    let next = 0;
    const callbacks = new Map(), pending = new Set();
    return {
        setTimeout(fn, ms) { const id = ++next; callbacks.set(id, { fn, ms }); pending.add(id); return id; },
        clearTimeout(id) { pending.delete(id); },
        fire(id = next) { pending.delete(id); callbacks.get(id)?.fn(); },
        get last() { return next; },
        callbacks, pending,
    };
}

function harness() {
    const timers = clock(), moods = [], expressions = [], speaking = [], calls = [];
    function rig(name) {
        return {
            idle: { pause: () => calls.push(name + ':pause'), resume: () => calls.push(name + ':resume'),
                feedIrisEmotion: mood => calls.push(name + ':idle:' + mood) },
            lip: { stop: () => calls.push(name + ':lip-stop'), stopAudioReactive: () => calls.push(name + ':reactive-stop') },
            animator: { start: () => calls.push(name + ':start'), stop: () => calls.push(name + ':stop'),
                setSyntheticMouthEnabled: value => calls.push(name + ':mouth:' + value) },
        };
    }
    const h = { timers, moods, expressions, speaking, calls, rig, current: rig('A') };
    h.controller = new SpeechPresentation({ timers,
        getIdle: () => h.current.idle, getLipSync: () => h.current.lip,
        getSpeechAnimator: () => h.current.animator,
        getAudioPlayer: () => ({ stop: () => calls.push('audio:stop') }),
        applyExpression: emotion => expressions.push(emotion),
        onMood: emotion => moods.push(emotion), onSpeaking: value => speaking.push(value),
    });
    return h;
}

test('the mood is shown at speech start while the mouth remains free for lipsync', () => {
    const h = harness();
    h.controller.start({ emotion: 'feliz' });
    assert.equal(h.controller.speaking, true);
    assert.deepEqual(h.moods, ['feliz']);
    assert.deepEqual(h.expressions, ['neutral']);
    assert.deepEqual(h.speaking, [true]);
    assert.ok(h.calls.includes('A:pause'));
    assert.ok(h.calls.includes('A:start'));
    assert.ok(h.calls.includes('A:mouth:false'));
    assert.ok(!h.calls.includes('audio:stop'), 'starting does not replace the audio owner');
});

test('speech end releases audio/lips and holds the expression for 3.5 seconds', () => {
    const h = harness();
    h.controller.start({ emotion: 'triste' });
    assert.equal(h.controller.end(), true);
    assert.equal(h.controller.speaking, false);
    assert.equal(h.expressions.at(-1), 'triste');
    assert.equal(h.moods.at(-1), 'triste');
    assert.equal(h.timers.callbacks.get(h.timers.last).ms, 3500);
    for (const call of ['A:lip-stop', 'A:reactive-stop', 'audio:stop', 'A:stop', 'A:mouth:true', 'A:resume']) {
        assert.ok(h.calls.includes(call), call);
    }
    h.timers.fire();
    assert.equal(h.expressions.at(-1), 'neutral');
    assert.equal(h.moods.at(-1), 'neutral');
});

test('an already queued decay cannot overwrite the next spoken response', () => {
    const h = harness();
    h.controller.start({ emotion: 'feliz' }); h.controller.end();
    const oldTimer = h.timers.last;
    h.controller.start({ emotion: 'pensativa' });
    assert.equal(h.timers.pending.size, 0);
    h.timers.fire(oldTimer); // simulate a callback queued before clearTimeout
    assert.equal(h.moods.at(-1), 'pensativa');
    assert.equal(h.expressions.at(-1), 'neutral');
    h.controller.end(); h.timers.fire(oldTimer);
    assert.equal(h.expressions.at(-1), 'pensativa');
    h.timers.fire();
    assert.equal(h.moods.at(-1), 'neutral');
});

test('explicit emotions supersede a previous hold without forcing the mouth during speech', () => {
    const h = harness();
    h.controller.start({ emotion: 'feliz' }); h.controller.end();
    const oldTimer = h.timers.last;
    h.controller.setEmotion('sorprendida'); h.timers.fire(oldTimer);
    assert.equal(h.expressions.at(-1), 'sorprendida');
    h.controller.start({ emotion: 'feliz' });
    h.controller.setEmotion('pensativa');
    assert.equal(h.moods.at(-1), 'pensativa');
    assert.equal(h.expressions.at(-1), 'neutral');
    h.controller.end();
    assert.equal(h.expressions.at(-1), 'pensativa');
});

test('duplicate end events neither stop audio again nor extend the hold', () => {
    const h = harness();
    assert.equal(h.controller.end(), false);
    h.controller.start({ emotion: 'feliz' }); h.controller.end();
    const count = h.calls.length, timer = h.timers.last;
    assert.equal(h.controller.end(), false);
    assert.equal(h.calls.length, count);
    assert.equal(h.timers.last, timer);
});

test('disconnect releases speech and invalidates old callbacks, then a new response can start', () => {
    const h = harness();
    h.controller.start({ emotion: 'feliz' }); h.controller.end();
    const timer = h.timers.last;
    h.controller.start({ emotion: 'triste' }); h.controller.reset();
    assert.equal(h.controller.speaking, false);
    assert.equal(h.moods.at(-1), 'neutral');
    assert.equal(h.speaking.at(-1), false);
    assert.equal(h.timers.pending.size, 0);
    h.controller.start({ emotion: 'pensativa' }); h.timers.fire(timer);
    assert.equal(h.moods.at(-1), 'pensativa');
    assert.equal(h.controller.speaking, true);
});

test('replacing a speaking avatar restores neutral expression without restarting audio or visemes', () => {
    const h = harness();
    h.controller.start({ emotion: 'feliz' });
    h.current = h.rig('B'); h.calls.length = 0;
    h.controller.syncAvatar();
    assert.equal(h.expressions.at(-1), 'neutral');
    assert.ok(h.calls.includes('B:pause'));
    assert.ok(h.calls.includes('B:start'));
    assert.ok(!h.calls.some(call => call.startsWith('A:') || call.startsWith('audio:') || call.includes('mouth:')),
        'preserves the player-selected mouth mode and the current audio');
    h.controller.end();
    assert.ok(h.calls.includes('B:resume'));
    assert.equal(h.expressions.at(-1), 'feliz');
});

test('replacing a resting avatar applies the current emotion, and its timer resets the new avatar', () => {
    const h = harness();
    h.controller.start({ emotion: 'feliz' }); h.controller.end();
    h.current = h.rig('B'); h.calls.length = 0;
    h.controller.syncAvatar();
    assert.equal(h.expressions.at(-1), 'feliz');
    assert.ok(!h.calls.includes('B:start'));
    h.timers.fire();
    assert.equal(h.expressions.at(-1), 'neutral');
    assert.ok(h.calls.includes('B:idle:neutral'));
});

test('a speech received while the avatar loads is applied when the rig becomes available', () => {
    const h = harness();
    h.current = {};
    h.controller.start({ emotion: 'feliz' });
    h.current = h.rig('B');
    h.controller.syncAvatar();
    assert.ok(h.calls.includes('B:start'));
    assert.equal(h.expressions.at(-1), 'neutral');
    h.controller.end();
    assert.equal(h.expressions.at(-1), 'feliz');
});

test('disposal is final and clears scheduled work', () => {
    const h = harness();
    h.controller.start({ emotion: 'feliz' }); h.controller.end();
    const timer = h.timers.last;
    h.controller.dispose();
    const count = h.calls.length, moods = h.moods.length;
    h.timers.fire(timer); h.controller.start({ emotion: 'triste' });
    h.controller.setEmotion('feliz'); h.controller.syncAvatar(); h.controller.reset(); h.controller.dispose();
    assert.equal(h.controller.end(), false);
    assert.equal(h.calls.length, count); assert.equal(h.moods.length, moods);
    assert.equal(h.timers.pending.size, 0);
});

function method(source, name, deps = {}) {
    const block = source.match(new RegExp('^    (?:async )?' + name + '\\([^]*?^    \\}', 'm'))?.[0];
    assert.ok(block, name);
    return new Function(...Object.keys(deps), 'return ({' + block.trim() + '})')(...Object.values(deps))[name];
}

for (const surface of ['face', 'companion']) {
    test(`${surface}: real message handling uses the shared state and leaves media completion to the player`, () => {
        const source = readFileSync(new URL(surface === 'face' ? '../../face/js/app.js' : '../../face/companion/companion.js', import.meta.url), 'utf8');
        const h = harness(), labels = { 'emotion-emoji': {}, 'emotion-label': {} }, transcripts = [], visemes = [];
        const document = { getElementById: id => labels[id] };
        const handle = method(source, '_handleMessage', { document, window: { dispatchEvent() {} },
            CustomEvent: class {}, shouldPlaySnapshotAnimation: () => false, clearTimeout() {}, setTimeout() {} });
        const app = {
            speechPresentation: h.controller,
            subtitles: { show() {}, hide() {} },
            panel: { addMessage: (...args) => transcripts.push(args), setBrainData() {} },
            _showSpeech() {}, _appendChat: (...args) => transcripts.push(args), _setOperationalState() {},
            _setEmotion: mood => h.controller.setEmotion(mood),
            lipSync: { processVisemes: items => visemes.push(items) },
            inboxVoice: { stop() {} },
            ttsAudioPlayer: { handle: payload => { app.audioPayload = payload; } },
        };
        if (surface === 'face') {
            app._updateEmotionEnergy = () => {}; app._triggerEmotionBounce = () => {};
            h.controller.onMood = mood => method(source, '_showEmotion', { document }).call(app, mood);
        }
        handle.call(app, 'speech_start', { text: 'Hola', emotion: 'feliz' });
        assert.equal(h.controller.emotion, 'feliz');
        assert.equal(h.expressions.at(-1), 'neutral');
        assert.equal(transcripts[0][1], 'Hola');
        if (surface === 'face') assert.equal(labels['emotion-label'].textContent, 'feliz');
        handle.call(app, 'lip_sync', { visemes: [{ time: 0, value: 'a' }] });
        assert.equal(visemes.length, 1);
        handle.call(app, 'tts_audio', { playback_id: 'same-owner', audio: 'test' });
        assert.equal(app.audioPayload.playback_id, 'same-owner');
        assert.equal(h.controller.speaking, true, 'audio arrival does not end the visual speech');
        handle.call(app, 'speech_end', {});
        assert.equal(h.controller.speaking, false);
        assert.equal(h.expressions.at(-1), 'feliz');
        handle.call(app, 'emotion', { emotion: 'pensativa' });
        assert.equal(h.expressions.at(-1), 'pensativa');
    });
}
