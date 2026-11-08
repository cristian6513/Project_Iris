/**
 * F07 Bandeja and the notice on return: the real components over a minimal DOM, against a fake Core.
 *
 * Opening or showing never marks anything read; actions name the revision they
 * saw; a source that cannot be read is said; Vision notices keep their own
 * actions; the notice gets exactly one receipt, deferred when the user is busy.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

class FakeNode {
    constructor(tag) {
        this.tagName = tag.toUpperCase();
        this.children = [];
        this.parentElement = null;
        this.listeners = {};
        this.attributes = {};
        this.dataset = {};
        this.disabled = false;
        this.hidden = false;
        this.open = false;
        this.className = '';
        this.textContent = '';
        this.value = '';
        this.type = '';
        this.focused = 0;
    }
    append(...nodes) { for (const node of nodes) { node.parentElement?.removeChild?.(node); node.parentElement = this; this.children.push(node); } }
    removeChild(node) { this.children = this.children.filter((child) => child !== node); node.parentElement = null; }
    replaceChildren(...nodes) { for (const child of this.children) child.parentElement = null; this.children = []; this.append(...nodes); }
    setAttribute(name, value) { this.attributes[name] = String(value); }
    getAttribute(name) { return this.attributes[name] ?? null; }
    addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
    dispatch(type, extra = {}) { for (const fn of this.listeners[type] || []) fn({ type, target: this, preventDefault() {}, ...extra }); }
    click() { if (!this.disabled) this.dispatch('click'); }
    focus() { this.focused += 1; globalThis.document.activeElement = this; }
    querySelectorAll(tag) {
        const out = [];
        for (const child of this.children) {
            if (child.tagName === tag.toUpperCase()) out.push(child);
            out.push(...child.querySelectorAll(tag));
        }
        return out;
    }
    find(predicate) {
        for (const child of this.children) {
            if (predicate(child)) return child;
            const found = child.find(predicate);
            if (found) return found;
        }
        return null;
    }
    texts() { return [this.textContent, ...this.children.flatMap((child) => child.texts())].filter(Boolean); }
}

const body = new FakeNode('body');
globalThis.document = { activeElement: null, visibilityState: 'visible', body, createElement: (tag) => new FakeNode(tag) };
const { InboxApp } = await import('../../face/js/inbox_app.js');
const { InboxNotice } = await import('../../face/js/inbox_notice.js');
const { noticeHeadline, normalizeNotice, filterItems, normalizeInbox, nextChangeAt } = await import('../../face/js/inbox_state.js');

const NOW = 1_800_000_000;
const A = 'a'.repeat(32);
const V = 'c'.repeat(32);
const automation = (extra = {}) => ({
    schema: 'iris.inbox-item/v1', key: `automation:default:${A}`, origin: 'automation', profile: 'default',
    source_id: A, source_revision: 1, title: 'Investigar al irme', text: 'Resumen sintético listo.',
    outcome: 'completed', delivery: { status: 'pending', revision: 3, expires_at: NOW + 3600 },
    actions: ['read', 'snooze', 'dismiss'], rule_id: 'b'.repeat(32), created_at: NOW - 60, updated_at: NOW - 10, ...extra,
});
const vision = (extra = {}) => ({
    schema: 'iris.inbox-item/v1', key: `vision:default:${V}`, origin: 'vision', profile: 'default', source_id: V,
    title: 'Pausa activa', text: 'Llevas 45 minutos.', outcome: 'notice', delivery: { status: 'pending', revision: 4 },
    actions: ['read'], created_at: NOW - 100, updated_at: NOW - 100, ...extra,
});
const snapshot = (extra = {}) => ({
    schema: 'iris.inbox/v1', server_now: NOW, items: [automation(), vision()], unread: 2,
    sources: { automation: { status: 'ok', message: '' }, vision: { status: 'ok', message: '', revision: 4 } },
    preferences: { available: true, revision: 0, return_notice: true, quiet_until: 0 }, ...extra,
});

function fakeCore(routes) {
    const calls = [];
    const fetchImpl = async (url, init = {}) => {
        calls.push({ url, method: init.method || 'GET', body: init.body ? JSON.parse(init.body) : null });
        const handler = routes[`${init.method || 'GET'} ${url}`] || routes[url];
        const [status, payload] = typeof handler === 'function' ? handler(init) : handler || [404, { detail: 'no' }];
        return { ok: status < 400, status, json: async () => payload };
    };
    return { calls, fetchImpl };
}

const settle = async () => { for (let i = 0; i < 20; i += 1) await Promise.resolve(); };
const buttonIn = (node, label) => node.find((n) => n.tagName === 'BUTTON' && n.textContent === label);

function make(routes, options = {}) {
    const core = fakeCore(routes);
    const timers = [];
    const opened = [];
    globalThis.document.activeElement = null;
    const app = new InboxApp({
        root: new FakeNode('div'), fetchImpl: core.fetchImpl, isVisible: options.isVisible || (() => true),
        onSummary: options.onSummary, onOpenVision: (id) => opened.push(['vision', id]),
        onOpenAutomations: (id) => opened.push(['automations', id]),
        setTimer: (fn, ms) => { timers.push({ fn, ms }); return timers.length; }, clearTimer: () => {},
    });
    return { app, core, timers, opened };
}

test('opening the inbox asks once and marks nothing read', async () => {
    const summaries = [];
    const { app, core } = make({ '/api/inbox': [200, snapshot()] }, { onSummary: (n) => summaries.push(n) });
    assert.equal(core.calls.length, 0);
    await app.load();
    assert.deepEqual(core.calls.map((c) => `${c.method} ${c.url}`), ['GET /api/inbox']);
    assert.deepEqual(summaries, [2]);
    assert.equal(app.counterValue.textContent, '02');
    const [first, second] = app.list.children;
    assert.equal(first.dataset.key, `automation:default:${A}`);
    assert.ok(buttonIn(first, 'Marcar leída') && buttonIn(first, 'Posponer') && buttonIn(first, 'Ver en Automatizaciones'));
    assert.deepEqual(second.querySelectorAll('button').map((b) => b.textContent), ['Abrir en Vision']); // Vision keeps its actions
    assert.ok(second.texts().some((t) => /vista Vision/.test(t)));
});

test('an action names the revision it saw; snooze sends the chosen delay', async () => {
    const posted = [];
    const { app } = make({
        '/api/inbox': [200, snapshot()],
        'POST /api/inbox/action': (init) => { posted.push(JSON.parse(init.body)); return [200, { changed: true, unread: 1 }]; },
    });
    await app.load();
    const card = app.list.children[0];
    const delay = card.find((n) => n.tagName === 'SELECT');
    delay.value = '240';
    delay.dispatch('change');
    buttonIn(card, 'Posponer').click();
    await settle();
    assert.deepEqual(posted, [{ key: `automation:default:${A}`, action: 'snooze', expected_revision: 3, minutes: 240 }]);
    assert.match(app.status.textContent, /Volverá sola; la tarea no se repite/);
});

test('a conflict is said and the current state is reloaded', async () => {
    let revision = 3;
    const { app, core } = make({
        '/api/inbox': () => [200, snapshot({ items: [automation({ delivery: { status: 'pending', revision } })] })],
        'POST /api/inbox/action': () => { revision = 5; return [409, { detail: { code: 'revision_conflict', message: 'x' } }]; },
    });
    await app.load();
    buttonIn(app.list.children[0], 'Marcar leída').click();
    await settle();
    assert.match(app.status.textContent, /Cambió en otro lugar/);
    assert.equal(app.state.items[0].delivery.revision, 5);
    assert.equal(core.calls.filter((c) => c.url === '/api/inbox').length, 2);
});

test('a source that could not be read is said, never shown as empty', async () => {
    const { app } = make({ '/api/inbox': [200, snapshot({ items: [],
        sources: { automation: { status: 'unavailable', message: 'El perfil contiene un enlace.' },
            vision: { status: 'ok', message: '', revision: 0 } } })] });
    await app.load();
    assert.equal(app.problems.hidden, false);
    assert.ok(app.problems.texts().includes('El perfil contiene un enlace.'));
    assert.ok(app.list.texts().includes('No se pudo leer todo'));
    assert.ok(!app.list.texts().includes('Nada por aquí'));
});

test('changes while hidden wait for the next showing; then one request', async () => {
    let visible = false;
    const { app, core } = make({ '/api/inbox': [200, snapshot()] }, { isVisible: () => visible });
    app.changed();
    app.changed();
    await settle();
    assert.equal(core.calls.length, 0);
    visible = true;
    app.syncVisibility();
    await settle();
    assert.equal(core.calls.length, 1);
    app.syncVisibility(); // nothing changed since
    await settle();
    assert.equal(core.calls.length, 1);
});

test('one refresh when a snooze comes back, none while hidden', async () => {
    let visible = true;
    const snoozed = automation({ delivery: { status: 'snoozed', snoozed_until: NOW + 600, revision: 4, expires_at: NOW + 9000 },
        actions: ['restore', 'read', 'dismiss'] });
    const { app, timers } = make({ '/api/inbox': [200, snapshot({ items: [snoozed] })] }, { isVisible: () => visible });
    await app.load();
    assert.equal(timers.length, 1);
    assert.ok(timers[0].ms > 590_000 && timers[0].ms < 610_000);
    assert.equal(nextChangeAt(app.state), NOW + 600);
    visible = false;
    app.syncVisibility();
    timers[0].fn();
    await settle();
    assert.equal(timers.length, 1);
    assert.ok(app.list.texts().includes('Nada por aquí')); // a snooze is not "Sin leer"…
    buttonIn(app.surface, 'Pospuestas').click();
    assert.ok(buttonIn(app.list.children[0], 'Mostrar ahora')); // …it is under «Pospuestas»
});

test('silence and the return notice switch name the preferences revision', async () => {
    const posted = [];
    const { app } = make({
        '/api/inbox': [200, snapshot()],
        'POST /api/inbox/preferences': (init) => { posted.push(JSON.parse(init.body)); return [200, snapshot()]; },
    });
    await app.load();
    buttonIn(app.side, 'Silenciar').click();
    await settle();
    buttonIn(app.side, 'Desactivar aviso al volver').click();
    await settle();
    assert.deepEqual(posted, [{ expected_revision: 0, quiet_minutes: 60 }, { expected_revision: 0, return_notice: false }]);
});

test('an older answer never overwrites a newer one; the filters keep running work in view', async () => {
    const pending = [];
    const app = new InboxApp({ root: new FakeNode('div'), isVisible: () => false,
        fetchImpl: () => new Promise((resolve) => pending.push(resolve)) });
    const first = app.load(true);
    const second = app.load(true);
    pending[1]({ ok: true, status: 200, json: async () => snapshot({ items: [automation({ title: 'Nueva' })] }) });
    await second;
    pending[0]({ ok: true, status: 200, json: async () => snapshot({ items: [automation({ title: 'Vieja' })] }) });
    await first;
    assert.equal(app.state.items[0].title, 'Nueva');
    const state = normalizeInbox(snapshot({ items: [automation({ outcome: 'running', delivery: {}, actions: [] }), vision()] }));
    assert.equal(state.unread, 1); // running is not unread…
    assert.equal(filterItems(state, { view: 'unread', origin: 'automation' }).length, 1); // …but stays in view
});

// ── The notice on return ─────────────────────────────────────────────────

const notice = (extra = {}) => ({
    schema: 'iris.inbox-presentation/v1', id: 'd'.repeat(32), token: 'e'.repeat(32), generation: 7, surface_id: 'face-1',
    results: [{ key: `automation:default:${A}`, title: 'Investigar al irme', outcome: 'completed', excerpt: 'Resumen.' },
        { key: `automation:default:${'f'.repeat(32)}`, title: 'Ordenar notas', outcome: 'failed', excerpt: 'Hermes no respondió.' }],
    running: [{ key: `automation:default:${'9'.repeat(32)}`, title: 'Investigación larga', state: 'running' }], ...extra,
});
const page = (extra = {}) => ({ visibilityState: 'visible', activeElement: null, ...extra });

test('the notice is shown once, gets one receipt and says failures and running work as they are', () => {
    const receipts = [];
    const host = new FakeNode('div');
    const n = new InboxNotice({ host, send: (r) => receipts.push(r), onOpenInbox: () => receipts.push('open'),
        setTimer: () => 1, clearTimer: () => {} });
    assert.equal(n.present(notice(), { surfaceId: 'face-1', documentLike: page() }), 'shown');
    assert.deepEqual(receipts, [{ id: 'd'.repeat(32), token: 'e'.repeat(32), generation: 7, shown: true }]);
    assert.ok(host.texts().includes('Mientras no estabas: 1 tarea terminó, 1 falló, 1 sigue en curso.'));
    assert.ok(host.texts().includes('Fallida') && host.texts().includes('En curso'));
    assert.equal(n.present(notice(), { surfaceId: 'face-1', documentLike: page() }), 'ignored'); // duplicate
    assert.equal(receipts.length, 1);
    buttonIn(host, 'Abrir bandeja').click();
    assert.deepEqual(receipts.slice(1), ['open']); // opening the inbox sends nothing else
    assert.equal(host.children.length, 0);
});

test('the notice is deferred, never shown, while hidden, typing, speaking or listening', () => {
    for (const [context, reason] of [
        [{ documentLike: page({ visibilityState: 'hidden' }) }, 'hidden'],
        [{ documentLike: page({ activeElement: { tagName: 'TEXTAREA' } }) }, 'typing'],
        [{ documentLike: page(), status: 'speaking' }, 'busy'],
        [{ documentLike: page(), status: 'listening' }, 'busy'],
    ]) {
        const receipts = [];
        const host = new FakeNode('div');
        const n = new InboxNotice({ host, send: (r) => receipts.push(r), setTimer: () => 1, clearTimer: () => {} });
        assert.equal(n.present(notice(), { surfaceId: 'face-1', ...context }), 'deferred');
        assert.deepEqual(receipts, [{ id: 'd'.repeat(32), token: 'e'.repeat(32), generation: 7, shown: false, reason }]);
        assert.equal(host.children.length, 0);
    }
});

test('a notice for another surface or malformed is not answered at all', () => {
    assert.equal(normalizeNotice(notice(), 'face-2'), null);
    assert.equal(normalizeNotice(notice({ token: 'x' }), 'face-1'), null);
    assert.equal(normalizeNotice(notice({ results: [], running: [] }), 'face-1'), null);
    const odd = normalizeNotice(notice({ results: [{ key: 'vision:default:' + V, title: 'x', outcome: 'completed' }],
        running: [] }), 'face-1');
    assert.equal(odd, null); // only automation results travel in this notice
    assert.equal(noticeHeadline({ results: [], running: [{}, {}] }), 'Mientras no estabas: 2 siguen en curso.');
});

test('the components set every text with textContent', () => {
    for (const file of ['inbox_app.js', 'inbox_notice.js', 'inbox_state.js']) {
        const source = readFileSync(new URL(`../../face/js/${file}`, import.meta.url), 'utf8');
        assert.ok(!source.includes('innerHTML'), file);
    }
});

test('Companion can name the actual destination without pretending to open the inbox directly', () => {
    const host = new FakeNode('div');
    const opened = [];
    const n = new InboxNotice({ host, openLabel: 'Abrir IRIS OS', onOpenInbox: () => opened.push('iris-os'),
        setTimer: () => 1, clearTimer: () => {} });
    n.present(notice(), { surfaceId: 'face-1', documentLike: page() });
    assert.equal(buttonIn(host, 'Abrir bandeja'), null);
    buttonIn(host, 'Abrir IRIS OS').click();
    assert.deepEqual(opened, ['iris-os']);
});
