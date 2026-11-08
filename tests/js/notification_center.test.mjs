import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { NotificationCenterApp, normalizeNotifications } from '../../face/js/notification_center.js';

const note = {
    schema: 'iris.notification/v1', source: 'vision', id: 'a'.repeat(32),
    title: '<img src=x onerror=alert(1)>', body: '<script>private</script>', unread: true,
    created_at: 1000, explanation: { observed_seconds: 2700, threshold_seconds: 2700, confidence: 0.9 },
};
const payload = { items: [note], revision: 1, storage_available: true, retention_days: 7 };

test('normalization limits rows and never accepts unknown source or media', () => {
    const state = normalizeNotifications({ items: [note, note, { ...note, id: 'no' },
        { ...note, id: 'b'.repeat(32), source: 'remote' }], audio: 'private' });
    assert.equal(state.items.length, 1);
    assert.equal(state.unread, 1);
    assert.equal('audio' in state, false);
    assert.equal(normalizeNotifications().storageAvailable, false);
});

test('render escapes text, never opens chat, and retains explicit read semantics', () => {
    const root = { innerHTML: '', addEventListener() {}, querySelectorAll() { return []; } };
    const app = new NotificationCenterApp({ root });
    app.state = normalizeNotifications(payload);
    app.loaded = true;
    app.render();
    assert.ok(root.innerHTML.includes('&lt;img'));
    assert.ok(!root.innerHTML.includes('<script>'));
    assert.ok(root.innerHTML.includes('Marcar leído'));
    assert.ok(root.innerHTML.includes('¿Por qué apareció?'));
    assert.ok(!root.innerHTML.includes('data-notify-action="chat"'));
});

test('construction and closed-window events never poll the backend', async () => {
    const original = globalThis.fetch;
    let calls = 0;
    globalThis.fetch = async () => { calls++; return { ok: true, json: async () => payload }; };
    try {
        const counts = [];
        const app = new NotificationCenterApp({ onSummary: n => counts.push(n) });
        app.changed({ unread: 1 });
        assert.equal(calls, 0);
        assert.deepEqual(counts, [1]);
        await app.load();
        assert.equal(calls, 1);
        await app.load();
        assert.equal(calls, 1);
        app.changed({ unread: 2 });
        assert.equal(calls, 1);
    } finally { globalThis.fetch = original; }
});

test('failed writes do not mark unread notes as read', async () => {
    const original = globalThis.fetch;
    globalThis.fetch = async () => ({ ok: false, json: async () => ({ detail: 'Disco bloqueado' }) });
    try {
        const app = new NotificationCenterApp();
        app.state = normalizeNotifications(payload);
        await app._request('/read', { id: note.id, read: true });
        assert.equal(app.state.unread, 1);
        assert.equal(app.error, 'Disco bloqueado');
    } finally { globalThis.fetch = original; }
});

test('updates during a request are coalesced, no parallel duplicate fetches', async () => {
    const original = globalThis.fetch;
    let release;
    let calls = 0;
    globalThis.fetch = () => {
        calls++;
        if (calls > 1) return Promise.resolve({ ok: true, json: async () => payload });
        return new Promise(resolve => { release = resolve; });
    };
    try {
        const app = new NotificationCenterApp({ isVisible: () => true });
        const first = app.load();
        for (let i = 0; i < 10; i++) app.changed({ unread: 1 });
        assert.equal(calls, 1);
        release({ ok: true, json: async () => payload });
        await first;
        await new Promise(resolve => setImmediate(resolve));
        assert.equal(calls, 2);
        assert.equal(app.state.unread, 1);
    } finally { globalThis.fetch = original; }
});

test('OS registration includes launch, refresh, restore and silent socket updates', () => {
    const shell = readFileSync(new URL('../../face/js/hermes_activity.js', import.meta.url), 'utf8');
    const app = readFileSync(new URL('../../face/js/app.js', import.meta.url), 'utf8');
    // F09.1: the app is registered by its manifest; the shell keeps its view and load adapter.
    const manifest = JSON.parse(readFileSync(new URL('../../apps/notifications/app.json', import.meta.url), 'utf8'));
    assert.equal(manifest.name, 'Notificaciones');
    assert.equal(manifest.presentation, 'window');
    assert.ok(shell.includes('data-workbench-view="notifications"'));
    assert.ok(shell.includes("notifications: { load: ({ force, opening }) => this._loadNotificationsTab(opening || force) }"));
    assert.ok(app.includes("case 'vision_assistant_notifications':"));
    const module = readFileSync(new URL('../../face/js/notification_center.js', import.meta.url), 'utf8');
    assert.ok(!module.includes('setInterval('));
    assert.ok(!module.includes('new Notification('));
    assert.ok(!module.includes('new Audio('));
});

test('visible availability expiry updates without polling and zero retention blocks opt-in', () => {
    const root = { innerHTML: '', addEventListener() {}, querySelectorAll() { return []; },
        querySelector() { return null; } };
    const app = new NotificationCenterApp({ root, isVisible: () => true });
    app.loaded = true;
    app.state = normalizeNotifications({ ...payload, vision: { enabled: true },
        availability: { available: true, expires_at: Date.now()/1000+900 } });
    app.render();
    assert.ok(root.innerHTML.includes('Terminar disponibilidad'));
    app.state.availability.expires_at = 1;
    app.observeVision();
    assert.ok(!root.innerHTML.includes('Terminar disponibilidad'));
    app.state.retentionDays = 0;
    app.render();
    assert.ok(root.innerHTML.includes('data-notify-action="availability" disabled'));
    assert.ok(root.innerHTML.includes('No se guardarán nuevos avisos'));
});
