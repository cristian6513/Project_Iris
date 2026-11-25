import test from 'node:test';
import assert from 'node:assert/strict';
import { GameCapture } from '../../face/js/game_capture.js';

const flush = async () => { for (let i = 0; i < 30; i++) await Promise.resolve(); };

test('commentary is separate consent, with explicit cadence, quota warning and validated options', async () => {
    const dialogs = [];
    const h = harness({ confirm: async options => { dialogs.push(options); return true; } });
    await h.active();
    h.c.update({ ...h.c.state, session_id: h.c.id, destination: { provider: 'fixture', model: 'vision' } });
    await h.c.commentary();
    const last = h.sent.at(-1).payload;
    assert.deepEqual(last, { command: 'commentary', session_id: 'session-one', enabled: true,
        confirmed: true, interval: 45, limit: 100, style: 'tips' });
    assert.match(dialogs.at(-1).message, /cada 45 s.*vision \(fixture\)/);
    assert.match(dialogs.at(-1).question, /mucho más rápido de lo normal, incluso si Iris decide quedarse callada/);
    assert.match(dialogs.at(-1).details.text, /100 consultas.*llevas 0/);
    assert.equal(dialogs.at(-1).cancelLabel, 'Solo cuando pregunte');
    assert.equal(h.frames.length, 0);
    const commands = h.sent.length;
    for (const interval of [19, 301, 1.5]) await h.c.commentary({ interval });
    assert.equal(h.sent.length, commands);
    h.c.dispose();
});

test('declining auto consent keeps manual mode, pausing needs no new consent', async () => {
    const h = harness(); await h.active();
    h.c.confirm = async () => false;
    const commands = h.sent.length;
    await h.c.commentary(); assert.equal(h.sent.length, commands);
    h.c.update({ ...h.c.state, commentary: { enabled: true } });
    h.c.confirm = () => { throw Error('must not ask when pausing'); };
    await h.c.commentary(); assert.equal(h.sent.at(-1).payload.enabled, false);
    h.c.dispose();
});

test('exiting during auto consent aborts it and late approval never reactivates the session', async () => {
    const h = harness(); await h.active(); let approve, signal;
    h.c.confirm = options => { signal = options.signal; return new Promise(r => { approve = r; }); };
    const work = h.c.commentary(); await flush();
    assert.equal(h.c.commentaryPending, true);
    h.c.stop(); assert.equal(signal.aborted, true); approve(true); await work;
    assert.equal(h.sent.some(m => m.payload.command === 'commentary' && m.payload.enabled), false);
    assert.equal(h.c.commentaryPending, false); h.c.dispose();
});

test('muted sessions cannot enable auto and unmuting alone sends no auto command', async () => {
    const h = harness(); await h.active();
    h.c.update({ ...h.c.state, muted: true }); await h.c.commentary();
    h.c.mute(); assert.equal(h.sent.at(-1).payload.command, 'mute');
    assert.equal(h.sent.some(m => m.payload.command === 'commentary'), false);
    h.c.dispose();
});
function harness(overrides = {}) {
    const sent = [], states = [], frames = [], timers = new Map(); let seq = 0;
    const track = Object.assign(new EventTarget(), { readyState: 'live', muted: false, label: 'Juego ficticio',
        stops: 0, stop() { this.stops++; }, getSettings: () => ({ displaySurface: 'window' }) });
    const stream = { getTracks: () => [track], getVideoTracks: () => [track], getAudioTracks: () => [] };
    const doc = Object.assign(new EventTarget(), { visibilityState: 'visible', defaultView: new EventTarget() });
    const c = new GameCapture({ doc, send: (type, payload) => { sent.push({ type, payload }); return true; },
        onState: s => states.push(s), confirm: async () => true,
        media: { getDisplayMedia: async args => {
            assert.equal(args.audio, false);
            assert.deepEqual(args.video.frameRate, { ideal: 2, max: 5 });
            return stream;
        } },
        capture: async value => { frames.push(value); return { image: 'data:image/jpeg;base64,FAKE' }; },
        timers: { setTimeout(fn, ms) { timers.set(++seq, { fn, ms }); return seq; }, clearTimeout(id) { timers.delete(id); } },
        ...overrides });
    const prepared = () => ({ request_id: c.request, session_id: 'session-one', destination: { provider: 'fixture', model: 'vision' } });
    async function active() {
        c.prepare({ game: 'Prueba', spoilerFree: true }); await c.prepared(prepared());
        c.update({ active: true, session_id: 'session-one', game: 'Prueba', analyses: 0, muted: false });
    }
    return { c, sent, states, track, stream, doc, timers, frames, prepared, active };
}

test('activation is explicit, window-only, audio off; no frame until a Core request', async () => {
    const h = harness(); await h.active();
    assert.equal(h.frames.length, 0);
    assert.equal(h.sent[0].payload.command, 'prepare');
    assert.equal(h.sent[1].payload.command, 'start');
    assert.equal(h.sent[1].payload.surface, 'window');
    assert.equal(h.sent[1].payload.spoiler_free, true);
    assert.equal(h.c.state.active, true);
    h.c.dispose(); assert.equal(h.track.stops, 1); assert.equal(h.timers.size, 0);
});

test('fresh frames reuse the selected stream; duplicates and foreign sessions are ignored', async () => {
    const h = harness(); await h.active();
    await h.c.take({ request_id: 'r1', session_id: 'wrong' });
    await h.c.take({ request_id: 'r1', session_id: 'session-one' });
    await h.c.take({ request_id: 'r1', session_id: 'session-one' });
    await h.c.take({ request_id: 'r2', session_id: 'session-one' });
    assert.equal(h.frames.length, 2); assert.equal(h.frames[0], h.stream);
    assert.equal(h.track.stops, 0);
    assert.equal(h.sent.filter(x => x.type === 'game_session_frame_result').length, 2);
    h.c.dispose();
});

test('declining consent never opens the OS picker', async () => {
    const h = harness({ confirm: async () => false, media: { getDisplayMedia: () => { throw Error('must not open'); } } });
    h.c.prepare(); await h.c.prepared(h.prepared());
    assert.equal(h.c.stream, null); assert.equal(h.sent.at(-1).payload.command, 'stop'); h.c.dispose();
});

test('late OS selection after exit is closed and never activates Core', async () => {
    let finish; const h = harness({ media: { getDisplayMedia: () => new Promise(r => { finish = r; }) } });
    h.c.prepare(); const running = h.c.prepared(h.prepared()); await flush();
    h.c.stop(); finish(h.stream); await running;
    assert.equal(h.track.stops, 1); assert.equal(h.c.stream, null);
    assert.equal(h.sent.some(x => x.payload.command === 'start'), false); h.c.dispose();
});

test('monitor, unknown surface and unwanted audio are refused with tracks released', async () => {
    for (const kind of ['monitor', undefined, 'audio']) {
        const h = harness();
        h.track.getSettings = () => ({ displaySurface: kind === 'audio' ? 'window' : kind });
        if (kind === 'audio') h.stream.getAudioTracks = () => [{}];
        h.c.prepare(); await h.c.prepared(h.prepared());
        assert.equal(h.track.stops, 1); assert.equal(h.c.state.active, false);
        assert.equal(h.sent.some(x => x.payload.command === 'start'), false); h.c.dispose();
    }
});

test('late capture result after exit cannot send an image', async () => {
    let finish; const h = harness({ capture: () => new Promise(r => { finish = r; }) }); await h.active();
    const running = h.c.take({ request_id: 'late', session_id: 'session-one' }); await flush();
    h.c.stop(); finish({ image: 'data:image/jpeg;base64,FAKE' }); await running;
    assert.equal(h.sent.some(x => x.type === 'game_session_frame_result'), false); h.c.dispose();
});

test('hide, end, track mute and Core revocation all release the OS stream', async () => {
    for (const reason of ['hide', 'end', 'track-muted', 'core']) {
        const h = harness(); await h.active();
        if (reason === 'hide') { h.doc.visibilityState = 'hidden'; h.doc.dispatchEvent(new Event('visibilitychange')); }
        if (reason === 'end') h.track.dispatchEvent(new Event('ended'));
        if (reason === 'track-muted') h.track.dispatchEvent(new Event('mute'));
        if (reason === 'core') h.c.update({ active: false, session_id: 'session-one', message: 'Permiso revocado' });
        assert.equal(h.c.stream, null); assert.equal(h.track.stops, 1); assert.equal(h.c.state.active, false); h.c.dispose();
    }
});

test('muting retains capture; stale text and stale prepared replies are ignored', async () => {
    const text = []; const h = harness({ onText: value => text.push(value) }); await h.active();
    h.c.mute(); assert.equal(h.sent.at(-1).payload.muted, true); assert.equal(h.track.stops, 0);
    h.c.answer({ session_id: 'wrong', text: 'stale' });
    h.c.answer({ session_id: 'session-one', text: 'respuesta' }); assert.deepEqual(text, ['respuesta']);
    h.c.stop(); await h.c.prepared({ session_id: 'old', request_id: 'old' });
    assert.equal(h.c.stream, null); h.c.dispose();
});

test('a previous session stop does not discard a new preparation', async () => {
    const h = harness(); h.c.prepare(); const request = h.c.request;
    h.c.update({ active: false, session_id: 'previous-session' });
    assert.equal(h.c.request, request); assert.equal(h.c.state.preparing, true); h.c.dispose();
});

test('preparation deadline cancels without keeping a persistent grant', () => {
    const h = harness(); h.c.prepare();
    [...h.timers.values()].find(x => x.ms === 180000).fn();
    assert.equal(h.c.state.preparing, false); assert.equal(h.sent.at(-1).payload.command, 'cancel_prepare'); h.c.dispose();
});

test('revocation during consent aborts the dialog and never opens the OS picker', async () => {
    let signal; let pickers = 0;
    const h = harness({
        confirm: options => new Promise(resolve => {
            signal = options.signal;
            signal.addEventListener('abort', () => resolve(false), { once: true });
        }),
        media: { getDisplayMedia: async () => { pickers++; return h.stream; } },
    });
    h.c.prepare(); const running = h.c.prepared(h.prepared()); await flush();
    h.c.update({ active: false, session_id: 'session-one', message: 'Permiso revocado' });
    await running; assert.equal(signal.aborted, true); assert.equal(pickers, 0); h.c.dispose();
});
