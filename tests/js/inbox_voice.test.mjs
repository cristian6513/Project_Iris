import test from 'node:test';
import assert from 'node:assert/strict';
import { InboxVoicePlayer } from '../../face/js/inbox_voice.js';

function world() {
    const receipts = [], audios = [], revoked = [], timers = [];
    const context = { connected: true, surfaceId: 'face-one', status: 'idle',
        documentLike: { visibilityState: 'visible', hasFocus: () => true, activeElement: { tagName: 'BODY' } } };
    class Audio {
        constructor() { this.events = {}; this.plays = 0; this.pauses = 0; this.reject = false; }
        addEventListener(name, fn) { this.events[name] = fn; }
        play() { this.plays++; return this.reject ? Promise.reject(new Error('autoplay')) : Promise.resolve(); }
        pause() { this.pauses++; }
        fire(name) {
            let stopped = false;
            this.events[name]?.({ stopImmediatePropagation() { stopped = true; } });
            if (!stopped) this[`on${name}`]?.({});
        }
    }
    let reject = false;
    const player = new InboxVoicePlayer({ send: (receipt) => receipts.push(receipt), getContext: () => context,
        audioFactory: () => { const audio = new Audio(); audio.reject = reject; audios.push(audio); return audio; },
        urlApi: { createObjectURL: () => `blob:${audios.length}`, revokeObjectURL: (url) => revoked.push(url) },
        now: () => 1000, setTimer: (fn) => { timers.push(fn); return timers.length; }, clearTimer: () => {},
    });
    const payload = { id: 'a'.repeat(32), token: 'b'.repeat(32), surface_id: 'face-one', expires_at: 1060,
        audio: btoa('RIFFsynthetic'), mime: 'audio/wav', visemes: [] };
    return { player, context, payload, receipts, audios, timers, revoked, reject: () => { reject = true; } };
}

test('only actual ended playback produces spoken; duplicates never play again', () => {
    const w = world();
    assert.equal(w.player.present(w.payload), 'attempted');
    assert.equal(w.receipts.length, 0);
    w.audios[0].fire('playing');
    assert.equal(w.receipts.length, 0);
    w.audios[0].fire('ended');
    assert.deepEqual(w.receipts, [{ id: w.payload.id, token: w.payload.token, outcome: 'spoken' }]);
    assert.equal(w.player.present(w.payload), 'ignored');
    assert.equal(w.audios.length, 1);
});

for (const condition of ['hidden','unfocused','typing','chat','microphone','quiet','disconnected','expired']) {
    test(`a ${condition} surface never creates audio`, () => {
        const w = world();
        if (condition === 'hidden') w.context.documentLike.visibilityState = 'hidden';
        if (condition === 'unfocused') w.context.documentLike.hasFocus = () => false;
        if (condition === 'typing') w.context.documentLike.activeElement = { tagName: 'TEXTAREA' };
        if (condition === 'chat') w.context.status = 'thinking';
        if (condition === 'microphone') w.context.status = 'listening';
        if (condition === 'quiet') w.context.quiet = true;
        if (condition === 'disconnected') w.context.connected = false;
        if (condition === 'expired') w.payload.expires_at = 999;
        assert.equal(w.player.present(w.payload), 'deferred');
        assert.equal(w.audios.length, 0);
        assert.equal(w.receipts[0].outcome, 'failed');
    });
}

test('autoplay rejection is failed, never spoken and never retried', async () => {
    const w = world();
    w.reject();
    w.player.present(w.payload);
    await Promise.resolve(); await Promise.resolve();
    assert.equal(w.receipts[0].outcome, 'failed');
    assert.equal(w.player.present(w.payload), 'ignored');
});

test('starting a conversation or losing visibility stops playback as ambiguous', () => {
    const w = world();
    w.player.present(w.payload);
    w.audios[0].fire('playing');
    w.context.status = 'thinking';
    w.player.sync();
    assert.ok(w.audios[0].pauses > 0);
    assert.equal(w.receipts[0].outcome, 'ambiguous');
    w.audios[0].fire('ended');
    assert.equal(w.receipts.length, 1);
});

test('a pending play that is stopped is uncertain; a late playing event cannot revive it', () => {
    const w = world();
    w.player.present(w.payload);
    w.player.stop();
    assert.equal(w.receipts[0].outcome, 'ambiguous');
    w.audios[0].fire('playing');
    assert.equal(w.player.current, null);
    assert.equal(w.receipts.length, 1);
});

test('a timeout stops the utterance once; no effect on read or presentation state exists', () => {
    const w = world();
    w.player.present(w.payload);
    w.timers[0]();
    assert.equal(w.receipts[0].outcome, 'ambiguous');
    assert.deepEqual(Object.keys(w.receipts[0]).sort(), ['id','outcome','token']);
    w.player.stop();
    assert.equal(w.receipts.length, 1);
});

test('default timers keep the Window receiver rather than passing the player as this', () => {
    const originalSet = globalThis.setTimeout, originalClear = globalThis.clearTimeout;
    let armed = 0, cleared = 0;
    globalThis.setTimeout = function () { assert.ok(this === undefined || this === globalThis); armed++; return 1; };
    globalThis.clearTimeout = function () { assert.ok(this === undefined || this === globalThis); cleared++; };
    try {
        const player = new InboxVoicePlayer({ send() {}, now: () => 1000,
            getContext: () => ({ connected:true,surfaceId:'one',documentLike:{visibilityState:'visible',hasFocus:()=>true} }),
            audioFactory: () => ({ addEventListener() {}, pause() {}, play: () => Promise.resolve() }),
            urlApi: { createObjectURL: ()=>'blob:fake',revokeObjectURL() {} },
        });
        player.present({ id:'a'.repeat(32),token:'b'.repeat(32),surface_id:'one',expires_at:1060,
            audio:btoa('RIFFfake'),mime:'audio/wav' });
        player.stop();
        assert.equal(armed,1);
        assert.equal(cleared,1);
    } finally { globalThis.setTimeout=originalSet; globalThis.clearTimeout=originalClear; }
});
