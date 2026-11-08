import test from 'node:test';
import assert from 'node:assert/strict';
import { TtsAudioPlayer } from '../../face/js/tts_audio_player.js';

function setup() {
    const calls = [], audios = [], revoked = [], receipts = [];
    const player = new TtsAudioPlayer({
        getLipSync: () => ({ processVisemes: (v) => calls.push(v[0]?.text),
            stop() {}, stopAudioReactive: () => calls.push('stop-reactive') }),
        getSpeechAnimator: () => ({ setSyntheticMouthEnabled: () => calls.push('synthetic') }),
        audioFactory: () => {
            const audio = { pause() {}, play() { return new Promise((resolve,reject)=>{this.reject=reject;}); } };
            audios.push(audio); return audio;
        },
        urlApi: { createObjectURL: () => `blob:${audios.length}`, revokeObjectURL: (url) => revoked.push(url) },
        logger: { warn() {} },
        onPlaybackResult: value => receipts.push(value),
    });
    const payload = (text) => ({ audio:btoa('RIFFsynthetic'),mime:'audio/wav',visemes:[{time:0,text}] });
    return { player,calls,audios,revoked,receipts,payload };
}

test('a delayed start never confirms completion; ended does so exactly once', () => {
    const w = setup();
    w.player.handle({ ...w.payload('whole response'), playback_id: 'turn-one' });
    assert.deepEqual(w.receipts, []);
    w.audios[0].onplaying();
    assert.deepEqual(w.receipts, []);
    w.audios[0].onended();
    w.player.stop();
    assert.deepEqual(w.receipts, [{ playback_id: 'turn-one', status: 'ended' }]);
});

test('interruption and playback rejection notify Core, but old callbacks do not finish a newer turn', async () => {
    const w = setup();
    w.player.handle({ ...w.payload('old'), playback_id: 'one' });
    w.player.handle({ ...w.payload('new'), playback_id: 'two' });
    w.audios[0].onended();
    w.audios[1].reject(new Error('device'));
    await Promise.resolve(); await Promise.resolve();
    w.player.stop();
    assert.deepEqual(w.receipts, [
        { playback_id: 'one', status: 'stopped' }, { playback_id: 'two', status: 'failed' },
    ]);
});

test('late media events from a stopped voice cannot change the next owners lip sync', () => {
    const w=setup();
    w.player.handle(w.payload('old'));
    const old=w.audios[0];
    w.player.stop();
    w.calls.length=0;
    old.onplaying(); old.onerror(new Error('late')); old.onended();
    assert.deepEqual(w.calls,[]);
});

test('an old tracks end or error cannot stop the replacement track', () => {
    const w=setup();
    w.player.handle(w.payload('old'));
    const old=w.audios[0];
    w.player.handle(w.payload('new'));
    w.audios[1].onplaying();
    assert.ok(w.calls.includes('new'));
    w.calls.length=0;
    old.onended(); old.onerror(new Error('late'));
    assert.deepEqual(w.calls,[]);
    assert.equal(w.player.audio,w.audios[1]);
});

test('a rejected old play promise cannot restore its old visemes after stop', async () => {
    const w=setup();
    w.player.handle(w.payload('old'));
    w.player.stop();
    w.calls.length=0;
    w.audios[0].reject(new Error('late rejection'));
    await Promise.resolve(); await Promise.resolve();
    assert.deepEqual(w.calls,[]);
});
