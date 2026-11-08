/**
 * F03b: the photo Core asks Face for. One request, one fresh frame, one
 * answer; nothing before the user's click unless a destination was remembered;
 * the camera consumer opened for the photo is released after it.
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import { SnapshotPrompt, SnapshotRequests, captureFrame, snapshotConsumer } from '../../face/js/vision_snapshot.js';

const settle = async () => { for (let i = 0; i < 20; i += 1) await Promise.resolve(); };

function liveStream() {
    const track = { readyState: 'live' };
    return { getVideoTracks: () => [track], track };
}

function harness({ decision = { confirmed: true, remember: false }, stream = liveStream(), cameraOn = false,
    capture = async () => ({ image: 'data:image/jpeg;base64,AAA', width: 640, height: 480 }), cameraError = '' } = {}) {
    const sent = [];
    const consumers = [];
    const prompts = [];
    let resolveAsk = null;
    const prompt = {
        ask(options) {
            prompts.push(options);
            if (decision !== 'wait') return Promise.resolve(decision);
            // Like SnapshotPrompt: an aborted request stops asking.
            return new Promise((resolve) => {
                resolveAsk = resolve;
                options.signal?.addEventListener('abort', () => resolve({ confirmed: false }), { once: true });
            });
        },
        capturing() {},
        close() {},
    };
    const requests = new SnapshotRequests({
        send: (result) => sent.push(result),
        acquire: async (consumer) => { consumers.push(['acquire', consumer]); return stream; },
        release: (consumer) => consumers.push(['release', consumer]),
        isCameraActive: () => cameraOn,
        cameraError: () => cameraError,
        prompt,
        capture,
    });
    return { requests, sent, consumers, prompts, answer: (value) => resolveAsk(value) };
}

const request = (id = 'snap-1', extra = {}) => ({ request_id: id, question: '¿qué tengo en la mano?',
    destination: { provider: 'openai-codex', model: 'gpt-5.5' }, needs_confirmation: true, can_remember: true, ...extra });

test('a confirmed request opens the camera for the photo, answers once and releases it', async () => {
    const h = harness();
    await h.requests.handle(request());
    assert.deepEqual(h.sent, [{ request_id: 'snap-1', status: 'ok', image: 'data:image/jpeg;base64,AAA',
        width: 640, height: 480, remember: false }]);
    assert.deepEqual(h.consumers, [['acquire', 'snapshot:snap-1'], ['release', 'snapshot:snap-1']]);
    assert.equal(h.prompts[0].destination, 'gpt-5.5 (openai-codex)');
    assert.equal(h.prompts[0].needsConfirmation, true);
    await h.requests.handle(request()); // the same request again: no second photo
    assert.equal(h.sent.length, 1);
});

test('declining sends cancelled without touching the camera', async () => {
    const h = harness({ decision: { confirmed: false } });
    await h.requests.handle(request());
    assert.deepEqual(h.sent, [{ request_id: 'snap-1', status: 'cancelled' }]);
    assert.deepEqual(h.consumers, []);
});

test('Core cancelling while the user decides takes no photo and sends nothing back', async () => {
    const h = harness({ decision: 'wait' });
    const handling = h.requests.handle(request());
    await settle();
    h.requests.cancel('snap-1', 'core');
    h.answer({ confirmed: true });
    await handling;
    assert.deepEqual(h.sent, []);
    assert.deepEqual(h.consumers, []);
});

test('cancelling during the capture releases the camera and answers cancelled', async () => {
    let release;
    const h = harness({ capture: (stream, { signal }) => new Promise((resolve, reject) => {
        release = resolve;
        signal.addEventListener('abort', () => reject(Object.assign(new Error('x'), { reason: 'cancelled' })));
    }) });
    const handling = h.requests.handle(request());
    await settle();
    h.requests.cancel('snap-1', 'user');
    await handling;
    assert.deepEqual(h.sent, [{ request_id: 'snap-1', status: 'cancelled' }]);
    assert.deepEqual(h.consumers.at(-1), ['release', snapshotConsumer('snap-1')]);
    assert.equal(typeof release, 'function');
});

test('a camera that cannot open is reported as denied or unavailable, never as a photo', async () => {
    const denied = harness({ stream: null, cameraError: 'denied' });
    await denied.requests.handle(request());
    assert.deepEqual(denied.sent, [{ request_id: 'snap-1', status: 'camera_denied' }]);
    const busy = harness({ stream: null, cameraError: 'busy' });
    await busy.requests.handle(request());
    assert.deepEqual(busy.sent, [{ request_id: 'snap-1', status: 'unavailable' }]);
    const broken = harness({ capture: async () => { throw Object.assign(new Error('x'), { reason: 'unavailable' }); } });
    await broken.requests.handle(request());
    assert.deepEqual(broken.sent, [{ request_id: 'snap-1', status: 'unavailable' }]);
});

test('a newer request replaces a pending one, which answers cancelled', async () => {
    const h = harness({ decision: 'wait' });
    const first = h.requests.handle(request('snap-1'));
    await settle();
    const second = h.requests.handle(request('snap-2'));
    await first;
    assert.deepEqual(h.sent, [{ request_id: 'snap-1', status: 'cancelled' }]);
    h.answer({ confirmed: true, remember: true });
    await second;
    assert.equal(h.sent[1].request_id, 'snap-2');
    assert.equal(h.sent[1].remember, true);
});

// ── The frame ─────────────────────────────────────────────────────────────

class FakeVideo {
    constructor() { this.listeners = {}; this.videoWidth = 1920; this.videoHeight = 1080; this.paused = true; }
    addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
    play() { this.paused = false; setTimeout(() => (this.listeners.playing || []).forEach((fn) => fn()), 0); return Promise.resolve(); }
    pause() { this.paused = true; }
    requestVideoFrameCallback(fn) { this.frames = (this.frames || 0) + 1; setTimeout(fn, 0); }
}

function fakeDoc() {
    const made = [];
    return {
        made,
        createElement(tag) {
            const node = tag === 'video' ? new FakeVideo() : {
                width: 0, height: 0, drawn: null,
                getContext: () => ({ drawImage: (...args) => { node.drawn = args; } }),
                toDataURL: (type, quality) => `data:${type};q=${quality};w=${node.width};h=${node.height}`,
            };
            made.push(node);
            return node;
        },
    };
}

test('the frame is taken after the request, scaled to 1280 px, and the video is let go', async () => {
    const doc = fakeDoc();
    const frame = await captureFrame(liveStream(), { doc });
    assert.equal(frame.width, 1280); assert.equal(frame.height, 720);
    assert.equal(frame.image, 'data:image/jpeg;q=0.85;w=1280;h=720');
    const video = doc.made[0];
    assert.equal(video.frames, 1); // waited for a frame presented after the request
    assert.equal(video.srcObject, null); assert.equal(video.paused, true);
});

test('no live track or no picture is unavailable; an abort is cancelled', async () => {
    await assert.rejects(captureFrame({ getVideoTracks: () => [{ readyState: 'ended' }] }, { doc: fakeDoc() }),
        (error) => error.reason === 'unavailable');
    const blank = fakeDoc();
    const original = blank.createElement;
    blank.createElement = (tag) => {
        const node = original(tag);
        if (tag === 'video') { node.videoWidth = 0; node.videoHeight = 0; }
        return node;
    };
    await assert.rejects(captureFrame(liveStream(), { doc: blank }), (error) => error.reason === 'unavailable');
    const controller = new AbortController();
    controller.abort();
    await assert.rejects(captureFrame(liveStream(), { doc: fakeDoc(), signal: controller.signal }),
        (error) => error.reason === 'cancelled');
});

// ── The prompt ────────────────────────────────────────────────────────────

class Node {
    constructor(tag) { this.tagName = tag.toUpperCase(); this.children = []; this.listeners = {}; this.attributes = {};
        this.textContent = ''; this.className = ''; this.type = ''; this.checked = false; this.parent = null; }
    append(...nodes) { for (const n of nodes) { if (typeof n === 'object') n.parent = this; this.children.push(n); } }
    remove() { if (this.parent) this.parent.children = this.parent.children.filter((c) => c !== this); this.parent = null; }
    setAttribute(name, value) { this.attributes[name] = String(value); }
    getAttribute(name) { return this.attributes[name]; }
    addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
    removeEventListener() {}
    click() { (this.listeners.click || []).forEach((fn) => fn({})); }
    focus() { this.focused = true; }
    querySelector(selector) {
        for (const child of this.children) {
            if (typeof child !== 'object') continue;
            if (selector === 'strong' && child.tagName === 'STRONG') return child;
            if (selector.startsWith('.') && child.className === selector.slice(1)) return child;
            const found = child.querySelector(selector);
            if (found) return found;
        }
        return null;
    }
    find(predicate) {
        for (const child of this.children) {
            if (typeof child !== 'object') continue;
            if (predicate(child)) return child;
            const found = child.find(predicate);
            if (found) return found;
        }
        return null;
    }
}

function promptDoc() {
    const body = new Node('body');
    return { body, createElement: (tag) => new Node(tag), createTextNode: (text) => text };
}

test('the dialog names the destination and waits for "Tomar foto"', async () => {
    const doc = promptDoc();
    let options, choose;
    const prompt = new SnapshotPrompt({ doc, consent: value => { options = value; return new Promise(resolve => { choose = resolve; }); } });
    const asking = prompt.ask({ requestId: 'snap-1', question: '¿qué tengo en la mano?', destination: 'gpt-5.5 (openai-codex)',
        needsConfirmation: true, canRemember: true });
    assert.equal(doc.body.children.length, 0, 'no capture indicator before consent');
    assert.match(options.message, /la enviará a gpt-5\.5 \(openai-codex\)\. Iris no la guarda/);
    assert.match(options.rememberLabel, /No volver a preguntar/);
    assert.equal(options.confirmLabel, 'Tomar foto');
    choose({ confirmed: true, remember: true });
    assert.deepEqual(await asking, { confirmed: true, remember: true });
    const dialog = doc.body.children[0];
    prompt.capturing('snap-1');
    assert.equal(dialog.getAttribute('role'), 'status');
    prompt.close('snap-1');
    assert.equal(doc.body.children.length, 0);
});

test('replacing or closing a pending common consent cannot create a late capture indicator', async () => {
    const doc = promptDoc();
    let choose;
    const prompt = new SnapshotPrompt({ doc, consent: () => new Promise(resolve => { choose = resolve; }) });
    const asking = prompt.ask({requestId:'old',question:'mira esto',destination:'QA',needsConfirmation:true,canRemember:false});
    const old = choose;
    const next = await prompt.ask({requestId:'new',question:'mira esto',destination:'QA',needsConfirmation:false,canRemember:false});
    assert.deepEqual(next,{confirmed:true,remember:false});
    old({confirmed:true,remember:true});
    assert.deepEqual(await asking,{confirmed:false,remember:false});
    assert.equal(doc.body.children.length,1);
    prompt.close('new');
    assert.equal(doc.body.children.length,0);
});

test('Core aborts a photo promptly even when its consent is queued behind another dialog',async()=>{
    const doc=promptDoc(),controller=new AbortController();
    const prompt=new SnapshotPrompt({doc,consent:()=>new Promise(()=>{})});
    const asking=prompt.ask({requestId:'queued',question:'mira esto',destination:'QA',needsConfirmation:true,signal:controller.signal});
    controller.abort();
    assert.deepEqual(await asking,{confirmed:false,remember:false});
    assert.equal(doc.body.children.length,0);prompt.close();
});

test('a remembered destination shows an indicator with Cancel instead of asking', async () => {
    const doc = promptDoc();
    const prompt = new SnapshotPrompt({ doc });
    const cancelled = [];
    prompt.onCancel = (id) => cancelled.push(id);
    const decision = await prompt.ask({ requestId: 'snap-2', question: 'mira esto', destination: 'gpt-5.5 (openai-codex)',
        needsConfirmation: false, canRemember: false });
    assert.deepEqual(decision, { confirmed: true, remember: false });
    const indicator = doc.body.children[0];
    assert.equal(indicator.getAttribute('role'), 'status');
    assert.equal(indicator.find((n) => n.tagName === 'INPUT'), null);
    indicator.find((n) => n.tagName === 'BUTTON' && n.textContent === 'Cancelar').click();
    assert.deepEqual(cancelled, ['snap-2']);
});

// ── R07: consecutive photos with the panel's real camera consumers ─────────

import fs from 'node:fs';
import { CAMERA_ERROR_MESSAGES, attachCameraPreview, cameraErrorReason, hasLiveCameraStream } from '../../face/js/camera_preview.js';

const panelSource = fs.readFileSync(new URL('../../face/js/panel.js', import.meta.url), 'utf8');
function panelMethod(name, deps) {
    const block = panelSource.match(new RegExp('^    (?:async )?' + name + '\\([^]*?^    \\}', 'm'))?.[0];
    assert.ok(block, name);
    return new Function(...Object.keys(deps), 'return ({' + block.trim() + '})')(...Object.values(deps))[name];
}

function realPanel() {
    const pending = [];
    const deps = { document: { visibilityState: 'visible', getElementById: () => ({}) }, attachCameraPreview, hasLiveCameraStream,
        CustomEvent: class { constructor(type) { this.type = type; } }, CAMERA_ERROR_MESSAGES, cameraErrorReason,
        navigator: { mediaDevices: { getUserMedia() { let resolve; const p = new Promise((r) => { resolve = r; }); pending.push(resolve); return p; } } },
        window: { dispatchEvent() {} } };
    const panel = { videoEl: { srcObject: null, play: async () => {}, pause() {} }, camStream: null, camActive: false,
        camStarting: false, _cameraConsumers: new Set(), _cameraRequestToken: 0, cameraError: '', cameraLeaseDenied: false,
        requestCameraLease: async () => true, releaseCameraLease() {}, _setModuleStatus() {}, addMessage() {} };
    for (const name of ['acquireCamera', 'releaseCamera', '_startCamera', '_stopCamera', '_syncCameraControls']) {
        panel[name] = panelMethod(name, deps);
    }
    return { panel, pending };
}

function trackStream() {
    const track = Object.assign(new EventTarget(), { readyState: 'live', stop() { this.readyState = 'ended'; } });
    return { getTracks: () => [track], getVideoTracks: () => [track], track };
}

test('a photo replaced while the camera opens does not turn it off for its successor', async () => {
    const { panel, pending } = realPanel();
    const sent = [];
    const captured = [];
    const requests = new SnapshotRequests({
        send: (result) => sent.push(result),
        acquire: (consumer) => panel.acquireCamera(consumer, { confirmed: true }),
        release: (consumer) => panel.releaseCamera(consumer),
        isCameraActive: () => panel.camActive,
        prompt: { ask: async () => ({ confirmed: true, remember: false }), capturing() {}, close() {} },
        capture: async (stream) => {
            captured.push(stream.getVideoTracks()[0].readyState);
            if (stream.getVideoTracks()[0].readyState !== 'live') throw Object.assign(new Error('ended'), { reason: 'unavailable' });
            return { image: 'data:image/jpeg;base64,AAA', width: 640, height: 480 };
        },
    });
    const first = requests.handle(request('snap-1'));
    await settle();
    assert.equal(pending.length, 1); // the camera is opening for the first photo
    const second = requests.handle(request('snap-2')); // replaces it
    await settle();
    const stream = trackStream();
    pending[0](stream);
    await Promise.all([first, second]);
    assert.deepEqual(sent.map((r) => [r.request_id, r.status]), [['snap-1', 'cancelled'], ['snap-2', 'ok']]);
    assert.deepEqual(captured, ['live']);
    assert.equal(stream.track.readyState, 'ended'); // both released: nobody else uses the camera
    assert.equal(panel._cameraConsumers.size, 0);
});

test('a photo releases only its own consumer: Vision Assistant keeps the camera', async () => {
    const { panel, pending } = realPanel();
    const assistant = panel.acquireCamera('vision-assistant');
    await settle();
    const stream = trackStream();
    pending[0](stream);
    await assistant;
    const requests = new SnapshotRequests({
        send() {}, acquire: (consumer) => panel.acquireCamera(consumer, { confirmed: true }),
        release: (consumer) => panel.releaseCamera(consumer), isCameraActive: () => panel.camActive,
        prompt: { ask: async () => ({ confirmed: true }), capturing() {}, close() {} },
        capture: async () => ({ image: 'data:image/jpeg;base64,AAA', width: 640, height: 480 }),
    });
    await requests.handle(request('snap-3'));
    assert.equal(stream.track.readyState, 'live');
    assert.deepEqual([...panel._cameraConsumers], ['vision-assistant']);
});

test('a photo Iris asks for says so in its own words and never offers to remember', async () => {
    const h = harness();
    await h.requests.handle(request('snap-iris', { requested_by: 'iris', can_remember: false }));
    assert.equal(h.prompts[0].requestedBy, 'iris');
    assert.equal(h.prompts[0].canRemember, false);
    const doc = promptDoc();
    let options;
    const prompt = new SnapshotPrompt({ doc, consent: value => { options = value; return Promise.resolve({ confirmed: false }); } });
    await prompt.ask({ requestId: 'snap-iris', question: 'dame tu opinión', destination: 'gpt-5.5 (openai-codex)',
        needsConfirmation: true, canRemember: false, requestedBy: 'iris' });
    assert.equal(options.title, 'Iris pide ver tu cámara');
    assert.match(options.message, /^Iris decidió que necesita una foto para responder a «dame tu opinión»/);
    assert.equal(options.rememberLabel, '');
});
