import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { CAMERA_ERROR_MESSAGES, attachCameraPreview, cameraErrorReason, hasLiveCameraStream } from '../../face/js/camera_preview.js';

const sources = Object.fromEntries(['panel', 'hermes_activity', 'app'].map(name =>
    [name, fs.readFileSync(new URL('../../face/js/' + name + '.js', import.meta.url), 'utf8')]));
function method(file, name, dependencies = {}) {
    const block = sources[file].match(new RegExp('^    (?:async )?' + name + '\\([^]*?^    \\}', 'm'))?.[0];
    assert.ok(block, name);
    return new Function(...Object.keys(dependencies), 'return ({' + block.trim() + '})')(...Object.values(dependencies))[name];
}
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };
const tick = () => new Promise(resolve => setImmediate(resolve));
function stream() {
    const track = Object.assign(new EventTarget(), { readyState: 'live', stops: 0,
        stop() { this.readyState = 'ended'; this.stops++; } });
    return { getTracks: () => [track], getVideoTracks: () => [track], track };
}
function video() {
    return { srcObject: null, paused: true, readyState: 0, plays: 0,
        play() { this.paused = false; this.readyState = 2; this.plays++; return Promise.resolve(); },
        pause() { this.paused = true; } };
}
function panelHarness() {
    const requests = [], events = [], button = {}, document = { visibilityState: 'visible', getElementById: () => button };
    const deps = { document, attachCameraPreview, hasLiveCameraStream, CustomEvent, CAMERA_ERROR_MESSAGES, cameraErrorReason,
        navigator: { mediaDevices: { getUserMedia() { const d = deferred(); requests.push(d); return d.promise; } } },
        window: { dispatchEvent: e => events.push(e.type) } };
    const state = { videoEl: video(), camStream: null, camActive: false, camStarting: false,
        _cameraConsumers: new Set(), _cameraRequestToken: 0, releases: 0, kills: 0,
        requestCameraLease: async () => true, releaseCameraLease() { this.releases++; },
        onCameraKillSwitch() { this.kills++; }, _setModuleStatus() {}, messages: [], cameraError: '',
        addMessage(role, text) { this.messages.push(text); } };
    for (const name of ['showCamera', 'toggleCamera', 'acquireCamera', 'releaseCamera', '_startCamera', '_stopCamera', '_syncCameraControls']) state[name] = method('panel', name, deps);
    return { state, requests, events, document, button };
}

test('reopening a paused preview resumes the same stream', async () => {
    const v = video(), s = stream();
    assert.equal(await attachCameraPreview(v, s), true);
    v.pause();
    assert.equal(await attachCameraPreview(v, s), true);
    assert.equal(v.plays, 2); assert.equal(v.srcObject, s); assert.equal(s.track.stops, 0);
    await attachCameraPreview(v, null);
    assert.equal(v.srcObject, null); assert.equal(s.track.stops, 0);
});

test('aborted video playback leaves capture owned by its consumers', async () => {
    const v = video(), s = stream(); v.play = async () => { throw new Error('AbortError'); };
    assert.equal(await attachCameraPreview(v, s), false);
    assert.equal(hasLiveCameraStream(s), true);
});

test('camera launcher opens an existing view without disabling Vision Assistant', async () => {
    const h = panelHarness(), s = stream();
    const start = h.state.showCamera(); await tick(); h.requests[0].resolve(s); await start;
    h.state.videoEl.pause(); await h.state.showCamera();
    assert.equal(h.requests.length, 1); assert.equal(h.state.kills, 0);
    assert.equal(h.state.videoEl.paused, false); assert.equal(h.state.camStream, s);
    await h.state.toggleCamera();
    assert.equal(h.state.kills, 1); assert.equal(s.track.stops, 1);
});

test('panel and assistant share one pending hardware request', async () => {
    const h = panelHarness(), s = stream();
    const a = h.state.acquireCamera('panel'), b = h.state.acquireCamera('vision-assistant');
    await tick(); assert.equal(h.requests.length, 1);
    h.requests[0].resolve(s); assert.deepEqual(await Promise.all([a, b]), [s, s]);
    h.state.releaseCamera('panel'); assert.equal(s.track.stops, 0);
    h.state.releaseCamera('vision-assistant'); assert.equal(s.track.stops, 1);
});

test('a late permission result cannot replace or stop a newer capture', async () => {
    const h = panelHarness(), old = stream(), current = stream();
    const first = h.state.acquireCamera('panel'); await tick();
    h.state._stopCamera();
    const second = h.state.acquireCamera('panel'); await tick();
    h.requests[1].resolve(current); await second;
    h.requests[0].resolve(old); assert.equal(await first, null);
    assert.equal(h.state.camStream, current); assert.equal(h.state.videoEl.srcObject, current);
    assert.equal(current.track.stops, 0); assert.equal(old.track.stops, 1);
    assert.equal(h.state.releases, 1); assert.ok(h.state._cameraConsumers.has('panel'));
});

test('permission accepted after the browser hides does not retain a camera', async () => {
    const h = panelHarness(), s = stream(); const start = h.state.acquireCamera('panel');
    await tick(); h.document.visibilityState = 'hidden'; h.requests[0].resolve(s); await start;
    assert.equal(s.track.stops, 1); assert.equal(h.state.camActive, false);
});

test('closing a camera preview leaves capture alive; explicit close releases its consumer', () => {
    const s = stream(), v = video(); v.srcObject = s;
    const state = { _cameraStream: s, panel: { querySelector: () => v }, released: [], releaseCamera(k) { this.released.push(k); } };
    const stop = method('hermes_activity', '_stopCameraPreview', { attachCameraPreview });
    stop.call(state); assert.deepEqual(state.released, []); assert.equal(s.track.stops, 0);
    assert.equal(v.srcObject, null);
    stop.call(state, { release: true }); assert.deepEqual(state.released, ['os-camera']);
});

function settingsHarness() {
    const requests = [], notified = [];
    const fetch = (url, options) => { const d = deferred(); requests.push({ ...d, url, options }); return d.promise; };
    const state = { _visionSettings: { enabled: false }, _visionEvents: [], _renderVisionAssistant() {},
        onVisionSettingsChanged: x => notified.push(x), _responseError: async () => 'save denied' };
    for (const name of ['applyVisionAssistantState', 'loadVisionAssistant', '_saveVisionSettings']) state[name] = method('hermes_activity', name, { fetch });
    const reply = data => ({ ok: true, json: async () => data });
    return { state, requests, notified, reply };
}

test('late settings reads cannot turn off a freshly enabled assistant', async () => {
    const h = settingsHarness(), reading = h.state.loadVisionAssistant();
    h.state.applyVisionAssistantState({ enabled: true });
    h.requests[0].resolve(h.reply({ enabled: false })); h.requests[1].resolve(h.reply({ events: [] }));
    await reading; assert.equal(h.state._visionSettings.enabled, true); assert.equal(h.notified.length, 1);
});

test('save keeps the selected checkbox and an older GET cannot end its loading state', async () => {
    const h = settingsHarness(), reading = h.state.loadVisionAssistant(), saving = h.state._saveVisionSettings({ enabled: true });
    assert.equal(h.state._visionSettings.enabled, true); assert.equal(h.notified.length, 0);
    h.requests[0].resolve(h.reply({ enabled: false })); h.requests[1].resolve(h.reply({ events: [] }));
    await reading; assert.equal(h.state._visionLoading, true);
    h.requests[2].resolve(h.reply({ enabled: true, camera_confirmation_granted: true })); await saving;
    assert.equal(h.state._visionLoading, false); assert.equal(h.state._visionSettings.enabled, true);
    assert.equal(h.notified[0].camera_confirmation_granted, true);
    assert.equal(h.state._visionSettings.camera_confirmation_granted, undefined);
    h.state.applyVisionAssistantState({ state: 'observing' });
    assert.equal(h.notified[1].camera_confirmation_granted, undefined);
});

test('a rejected save restores the last confirmed setting', async () => {
    const h = settingsHarness(), saving = h.state._saveVisionSettings({ enabled: true });
    h.requests[0].resolve({ ok: false }); await saving;
    assert.equal(h.state._visionSettings.enabled, false); assert.equal(h.state._visionError, 'save denied');
    assert.equal(h.notified.length, 0);
});

test('mirror controls and rig writes are absent from both runtime surfaces', () => {
    const companion = fs.readFileSync(new URL('../../face/companion/companion.js', import.meta.url), 'utf8');
    const html = fs.readFileSync(new URL('../../face/index.html', import.meta.url), 'utf8');
    assert.doesNotMatch(sources.app + sources.panel + companion + html, /MocapRigger|mocapRigger|IRIS:mirror-mode|mirror-toggle|mirrorMode/);
});

// ── F03: transitions of the shared camera ───────────────────────────────

test('each failure to open the camera keeps its own reason and message', async () => {
    for (const [name, reason] of [['NotAllowedError', 'denied'], ['NotReadableError', 'busy'],
        ['NotFoundError', 'missing'], ['OverconstrainedError', 'missing'], ['TypeError', 'error']]) {
        const h = panelHarness();
        const start = h.state.acquireCamera('panel'); await tick();
        h.requests[0].reject(Object.assign(new Error('x'), { name }));
        assert.equal(await start, null);
        assert.equal(h.state.cameraError, reason, name);
        assert.deepEqual(h.state.messages, [CAMERA_ERROR_MESSAGES[reason]]);
        assert.equal(h.state.camActive, false); assert.equal(h.state._cameraConsumers.size, 0);
        assert.equal(h.state.releases, 1); // the lease granted for this attempt is returned
    }
    assert.equal(cameraErrorReason(null), 'error');
});

test('a refused lease and a camera that ends are told apart, and success clears the reason', async () => {
    const h = panelHarness(); h.state.requestCameraLease = async () => false;
    assert.equal(await h.state.acquireCamera('panel'), null);
    assert.equal(h.state.cameraError, 'lease'); assert.equal(h.requests.length, 0); // no hardware request
    h.state.requestCameraLease = async () => true;
    const s = stream(); const start = h.state.acquireCamera('panel'); await tick();
    h.requests[0].resolve(s); await start;
    assert.equal(h.state.cameraError, '');
    s.track.dispatchEvent(new Event('ended')); // unplugged, or the PC suspended
    assert.equal(h.state.cameraError, 'ended'); assert.equal(h.state.camActive, false);
    assert.equal(h.state.messages.at(-1), CAMERA_ERROR_MESSAGES.ended);
});

test('closing the Camera window releases its consumer; Vision Assistant keeps its own', async () => {
    const h = panelHarness(), s = stream();
    const view = h.state.acquireCamera('os-camera'), assistant = h.state.acquireCamera('vision-assistant');
    await tick(); h.requests[0].resolve(s); await Promise.all([view, assistant]);
    const shell = { _osWindows: new Map([['camera', { el: { remove() {} }, view: { classList: { remove() {} } } }]]),
        _windowViewAnchors: new Map(), _windowLayout: {}, _captureWindowLayout() {}, _saveWindowLayout() {},
        _restoreDesktopFocus() {}, _renderDesktopHome() {}, _syncAppPolling() {},
        panel: { querySelector: () => video() }, releaseCamera: (consumer) => h.state.releaseCamera(consumer) };
    shell._stopCameraPreview = method('hermes_activity', '_stopCameraPreview', { attachCameraPreview });
    method('hermes_activity', '_closeWindow').call(shell, 'camera');
    assert.equal(s.track.stops, 0); // the assistant still uses it
    assert.deepEqual([...h.state._cameraConsumers], ['vision-assistant']);
    h.state.releaseCamera('vision-assistant');
    assert.equal(s.track.stops, 1); // nobody else: the camera turns off
});

test('closing the Camera window with only its own view turns the camera off', async () => {
    const h = panelHarness(), s = stream();
    const view = h.state.acquireCamera('os-camera'); await tick(); h.requests[0].resolve(s); await view;
    const shell = { _osWindows: new Map([['camera', { el: { remove() {} }, view: { classList: { remove() {} } } }]]),
        _windowViewAnchors: new Map(), _windowLayout: {}, _captureWindowLayout() {}, _saveWindowLayout() {},
        _restoreDesktopFocus() {}, _renderDesktopHome() {}, _syncAppPolling() {},
        panel: { querySelector: () => video() }, releaseCamera: (consumer) => h.state.releaseCamera(consumer) };
    shell._stopCameraPreview = method('hermes_activity', '_stopCameraPreview', { attachCameraPreview });
    method('hermes_activity', '_closeWindow').call(shell, 'camera');
    assert.equal(s.track.stops, 1); assert.equal(h.state.camActive, false);
});
