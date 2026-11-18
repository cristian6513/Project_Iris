// The quiet update notice (user's choice, 2026-11-18): Iris asks its channel by itself, at most
// every 12 h, and a new version lights a dot in the control center. Nothing is downloaded and
// nothing is spoken.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

import {
    UPDATE_CHECK_EVERY_MS,
    UPDATE_NOTICE_KEY,
    cachedNotice,
    checkForUpdates,
    createUpdateNoticeStorage,
    rememberSnapshot,
    showUpdateDot,
    updateAvailable,
} from '../../face/js/update_notice.js?v=2';

const AVAILABLE = { summary: 'updates_available', can_update: true, available: { iris: '0.9.5' } };
const CURRENT = { summary: 'current', can_update: false };

function memoryStorage(initial = {}) {
    const data = new Map(Object.entries(initial));
    return { getItem: (k) => data.get(k) ?? null, setItem: (k, v) => data.set(k, String(v)), data };
}

/** Core: status says whether this install has a channel; check answers from it. */
function fakeCore({ snapshot = { summary: 'standby' }, active = false, check = AVAILABLE, statusOk = true } = {}) {
    const calls = [];
    const fetchImpl = async (url) => {
        calls.push(url);
        if (url.endsWith('/status')) return { ok: statusOk, json: async () => (snapshot ? { active, snapshot } : { active, phase: 'idle' }) };
        return { ok: true, json: async () => check };
    };
    return { fetchImpl, calls };
}

test('only a release the install can take counts as an update', () => {
    assert.equal(updateAvailable(AVAILABLE), true);
    assert.equal(updateAvailable(CURRENT), false);
    assert.equal(updateAvailable({ summary: 'updates_available', can_update: false }), false);
    assert.equal(updateAvailable(null), false);
});

test('the installed app asks its channel and remembers the answer for this version', async () => {
    const core = fakeCore(), storage = memoryStorage();
    const result = await checkForUpdates({ fetchImpl: core.fetchImpl, storage, now: 1000, currentVersion: '0.9.4' });
    assert.deepEqual(result, { available: true, version: '0.9.5', source: 'channel' });
    assert.deepEqual(core.calls, ['/api/system-updates/status', '/api/system-updates/check']);
    assert.deepEqual(JSON.parse(storage.data.get(UPDATE_NOTICE_KEY)), { at: 1000, current: '0.9.4', available: true, version: '0.9.5' });
});

test('a fresh answer is reused: at most one channel check every 12 hours', async () => {
    const storage = memoryStorage({ [UPDATE_NOTICE_KEY]: JSON.stringify({ at: 0, current: '0.9.4', available: true, version: '0.9.5' }) });
    const core = fakeCore();
    const soon = await checkForUpdates({ fetchImpl: core.fetchImpl, storage, now: UPDATE_CHECK_EVERY_MS - 1, currentVersion: '0.9.4' });
    assert.equal(soon.source, 'cache');
    assert.deepEqual(core.calls, []);
    const later = await checkForUpdates({ fetchImpl: core.fetchImpl, storage, now: UPDATE_CHECK_EVERY_MS + 1, currentVersion: '0.9.4' });
    assert.equal(later.source, 'channel');
});

test('after updating, the remembered answer no longer applies', () => {
    const storage = memoryStorage({ [UPDATE_NOTICE_KEY]: JSON.stringify({ at: 0, current: '0.9.4', available: true, version: '0.9.5' }) });
    assert.equal(cachedNotice({ storage, now: 1, currentVersion: '0.9.5' }), null);
    assert.deepEqual(cachedNotice({ storage, now: 1, currentVersion: '0.9.4' }), { available: true, version: '0.9.5', fresh: true });
});

test('a development checkout or an install without a channel asks nothing', async () => {
    for (const snapshot of [null, { summary: 'unavailable' }]) {
        const core = fakeCore({ snapshot });
        const result = await checkForUpdates({ fetchImpl: core.fetchImpl, storage: memoryStorage(), currentVersion: '1.0.0' });
        assert.equal(result.source, 'none');
        assert.equal(result.available, false);
        assert.deepEqual(core.calls, ['/api/system-updates/status'], 'no /check: in development it would fetch Git');
    }
});

test('an install in progress, an error or no Core keep the last answer and download nothing', async () => {
    const storage = memoryStorage({ [UPDATE_NOTICE_KEY]: JSON.stringify({ at: 0, current: '0.9.4', available: true, version: '0.9.5' }) });
    const now = UPDATE_CHECK_EVERY_MS * 2;
    const busy = fakeCore({ active: true });
    assert.deepEqual(await checkForUpdates({ fetchImpl: busy.fetchImpl, storage, now, currentVersion: '0.9.4' }), { available: true, version: '0.9.5', source: 'error' });
    assert.deepEqual(busy.calls, ['/api/system-updates/status']);
    const offline = async () => { throw new Error('Core is not up'); };
    assert.equal((await checkForUpdates({ fetchImpl: offline, storage, now, currentVersion: '0.9.4' })).available, true);
    for (const url of [...busy.calls]) assert.doesNotMatch(url, /apply|download/);
});

test('a check from Ajustes updates the memory as well', () => {
    const storage = memoryStorage();
    assert.equal(rememberSnapshot(CURRENT, { storage, now: 5, currentVersion: '0.9.4' }), false);
    assert.equal(JSON.parse(storage.data.get(UPDATE_NOTICE_KEY)).available, false);
});

test('channel errors retain the known release and limit failed attempts for 12 hours', async () => {
    for (const failure of ['http', 'snapshot', 'offline']) {
        const storage = memoryStorage();
        rememberSnapshot(AVAILABLE, { storage, now: 0, currentVersion: '0.9.4' });
        const calls = [];
        const fetchImpl = async url => {
            calls.push(url);
            if (url.endsWith('/status')) return { ok: true, json: async () => ({ snapshot: { summary: 'standby' } }) };
            if (failure === 'offline') throw new Error('offline');
            return { ok: failure !== 'http', json: async () => ({ summary: 'error', can_update: false }) };
        };
        const options = { storage, fetchImpl, currentVersion: '0.9.4' };
        assert.deepEqual(await checkForUpdates({ ...options, now: UPDATE_CHECK_EVERY_MS }), {
            available: true, version: '0.9.5', source: 'error',
        });
        const cached = await checkForUpdates({ ...options, now: UPDATE_CHECK_EVERY_MS + 3600000 });
        assert.equal(cached.source, 'cache');
        assert.equal(cached.available, true);
        assert.equal(calls.filter(url => url.endsWith('/check')).length, 1);
        await checkForUpdates({ ...options, now: 2 * UPDATE_CHECK_EVERY_MS });
        assert.equal(calls.filter(url => url.endsWith('/check')).length, 2);
    }
});

test('an inconclusive manual check keeps the release label and its original timestamp', () => {
    const storage = memoryStorage();
    rememberSnapshot(AVAILABLE, { storage, now: 1, currentVersion: '0.9.4' });
    const before = storage.getItem(UPDATE_NOTICE_KEY);
    for (const snapshot of [{ summary: 'error' }, { summary: 'updating' }, null]) {
        assert.equal(rememberSnapshot(snapshot, { storage, now: 5, currentVersion: '0.9.4' }), true);
        assert.equal(storage.getItem(UPDATE_NOTICE_KEY), before);
        assert.equal(cachedNotice({ storage, currentVersion: '0.9.4' }).version, '0.9.5');
    }
    rememberSnapshot(CURRENT, { storage, now: 6, currentVersion: '0.9.4' });
    assert.equal(cachedNotice({ storage, currentVersion: '0.9.4' }).available, false);
});

test('blocked or full storage still limits channel checks during the session', async () => {
    const failures = [
        null,
        { getItem() { throw new Error('blocked'); }, setItem() { throw new Error('blocked'); } },
        { getItem() { return null; }, setItem() { throw new Error('quota'); } },
    ];
    for (const persistent of failures) {
        const storage = createUpdateNoticeStorage(persistent);
        const core = fakeCore();
        const options = { storage, fetchImpl: core.fetchImpl, currentVersion: '0.9.4' };
        assert.equal((await checkForUpdates({ ...options, now: 1 })).source, 'channel');
        const second = await checkForUpdates({ ...options, now: 3600001 });
        assert.deepEqual(second, { available: true, version: '0.9.5', source: 'cache' });
        assert.equal(core.calls.filter(url => url.endsWith('/check')).length, 1);
        await checkForUpdates({ ...options, now: UPDATE_CHECK_EVERY_MS + 1 });
        assert.equal(core.calls.filter(url => url.endsWith('/check')).length, 2);
    }
});

test('the dot and its labels: the button, and the control center tab even when closed', () => {
    const attrs = new Map(), classes = new Set();
    const dot = { hidden: true };
    const button = {
        title: '', querySelector: (s) => (s === '.panel-shortcut-dot' ? dot : null),
        setAttribute: (k, v) => attrs.set(k, v), removeAttribute: (k) => attrs.delete(k),
    };
    const toggle = { classList: { toggle: (c, on) => (on ? classes.add(c) : classes.delete(c)) } };
    const doc = { getElementById: (id) => ({ 'panel-open-updates': button, 'panel-toggle': toggle })[id] ?? null };
    showUpdateDot(true, { version: '0.9.5', doc });
    assert.equal(dot.hidden, false);
    assert.equal(attrs.get('aria-label'), 'Buscar actualizaciones: hay una versión nueva (Iris 0.9.5)');
    assert.ok(classes.has('has-update'));
    showUpdateDot(false, { doc });
    assert.equal(dot.hidden, true);
    assert.equal(attrs.has('aria-label'), false);
    assert.equal(classes.has('has-update'), false);
});

test('the room starts it after the first frame, and Iris never announces it', async () => {
    const app = await readFile(new URL('../../face/js/app.js', import.meta.url), 'utf8');
    const html = await readFile(new URL('../../face/index.html', import.meta.url), 'utf8');
    const activity = await readFile(new URL('../../face/js/hermes_activity.js', import.meta.url), 'utf8');
    const start = app.slice(app.indexOf('    _startUpdateNotice() {'), app.indexOf('    async init() {'));
    assert.match(start, /this\._firstFrameShown\.then\(/);
    assert.match(start, /setTimeout\(check, 30_000\)/);
    assert.match(start, /addEventListener\('IRIS:system-updates'/);
    assert.doesNotMatch(start, /subtitles|addMessage|speak|tts|ws\?\.send/, 'a dot only: no message, voice or model');
    assert.match(app, /this\._startUpdateNotice\(\);/);
    assert.match(html, /id="panel-open-updates"[^>]*>Buscar actualizaciones<span class="panel-shortcut-dot" aria-hidden="true" hidden><\/span>/);
    assert.match(activity, /dispatchEvent\(new CustomEvent\('IRIS:system-updates', \{ detail: this\._systemUpdates \}\)\)/);
});
