/** F10 review regressions: real controller, synthetic DOM/API/clock; no Google or network. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { createApp } from '../ui/main.js';

class Element {
    constructor(tag) {
        Object.assign(this, { tag, children: [], listeners: {}, dataset: {}, attributes: {},
            textContent: '', className: '', disabled: false });
    }
    append(...nodes) { this.children.push(...nodes); }
    replaceChildren(...nodes) { this.children = nodes; }
    setAttribute(name, value) { this.attributes[name] = String(value); }
    addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
    fire(type) { for (const fn of this.listeners[type] || []) fn({ type, target: this }); }
    focus() { globalThis.document.activeElement = this; }
    all() { return [this, ...this.children.flatMap((node) => node.all())]; }
    find(part) { return this.all().find((node) => node.dataset.part === part); }
}
const flush = async () => { for (let i = 0; i < 40; i++) await Promise.resolve(); };
const deferred = () => { let resolve; const promise = new Promise((done) => { resolve = done; }); return { promise, resolve }; };
const response = (body, status = 200) => ({ ok: status < 400, status, json: async () => body });
const active = (id = 'gses_first') => ({ id, state: 'awaiting_user', services: ['calendar.read'],
    authorization_url: 'https://accounts.google.com/o/oauth2/v2/auth?state=synthetic', error: null });
const snapshot = (session = null) => response({ available: true, accounts: [], session });
const missing = () => response({ error: { code: 'not_found', message: 'No existe.' } }, 404);

async function fixture(run) {
    const previousDocument = globalThis.document;
    const previousTimeout = globalThis.setTimeout;
    const previousClear = globalThis.clearTimeout;
    const timers = new Map();
    const calls = [];
    let next = 0;
    let handler = () => snapshot(active());
    globalThis.document = { activeElement: null, createElement: (tag) => new Element(tag) };
    globalThis.setTimeout = (fn) => { timers.set(++next, fn); return next; };
    globalThis.clearTimeout = (id) => timers.delete(id);
    const app = createApp({}, { fetchImpl: async (url, options = {}) => {
        const path = url.slice('/api/connections/google'.length);
        const call = { path, method: options.method || 'GET' };
        calls.push(call);
        return handler(call);
    }, requestId: () => 'request-synthetic' });
    const root = new Element('div');
    app.mount(root);
    const tick = async () => {
        const [id, fn] = timers.entries().next().value || [];
        assert.ok(fn, 'a poll is scheduled');
        timers.delete(id);
        fn();
        await flush();
    };
    try {
        await app.refresh({ opening: true });
        app.setVisibility({ visible: true });
        await run({ app, root, calls, timers, tick, route: (fn) => { handler = fn; } });
    } finally {
        app.dispose();
        globalThis.document = previousDocument;
        globalThis.setTimeout = previousTimeout;
        globalThis.clearTimeout = previousClear;
    }
}

for (const state of ['denied', 'expired', 'failed', 'cancelled', 'committed', 'partial']) {
    test(`R08: ${state} is drawn immediately even while unchanged accounts are still loading`, async () => {
        await fixture(async ({ root, timers, tick, route }) => {
            const refresh = deferred();
            route(({ path }) => path ? response({ ...active(), state,
                error: { message: `Resultado sintético: ${state}.` } }) : refresh.promise);
            await tick();
            assert.equal(root.find('session'), undefined, 'the waiting panel is gone before the refresh returns');
            assert.equal(root.find('cancel'), undefined);
            assert.ok(root.find('connect'), 'another connection is offered');
            assert.match(root.find('status').textContent, state === 'cancelled' ? /cancelada/
                : state === 'committed' ? /Cuenta conectada/
                : state === 'partial' ? /no concedió todos los permisos/ : new RegExp(state));
            assert.equal(timers.size, 0, 'no polling of a terminal');
            refresh.resolve(snapshot());
            await flush();
            assert.ok(root.find('connect'), 'the unchanged account snapshot keeps the terminal visible');
            assert.ok(root.find('status').textContent);
        });
    });
}

test('R09: a fresh snapshot without a session clears the attempt after a Core restart', async () => {
    await fixture(async ({ app, root, timers, calls, route }) => {
        route(() => snapshot());
        await app.refresh({ force: true });
        assert.ok(root.find('connect'));
        assert.equal(root.find('session'), undefined);
        assert.match(root.find('status').textContent, /ya no encuentra esa autorización/);
        assert.doesNotMatch(root.find('status').textContent, /cancelada/);
        assert.equal(timers.size, 0);
        assert.equal(calls.filter(({ path }) => path).length, 0, 'no request for the lost attempt');
    });
});

for (const path of ['poll', 'cancel']) {
    test(`R09: ${path} 404 reconciles with one snapshot and stops polling the missing attempt`, async () => {
        await fixture(async ({ root, timers, calls, tick, route }) => {
            route(({ path }) => path ? missing() : snapshot());
            if (path === 'poll') await tick();
            else { root.find('cancel').fire('click'); await flush(); }
            assert.ok(root.find('connect'));
            assert.equal(root.find('session'), undefined);
            assert.equal(timers.size, 0);
            assert.equal(calls.filter(({ path }) => !path).length, 2, 'initial state and a reconciliation');
            assert.match(root.find('status').textContent, /ya no encuentra esa autorización/);
        });
    });
}

test('R09: even an inconsistent snapshot cannot keep polling an id just reported missing', async () => {
    await fixture(async ({ root, timers, tick, route }) => {
        route(({ path }) => path ? missing() : snapshot(active()));
        await tick();
        assert.ok(root.find('connect'));
        assert.equal(timers.size, 0);
        assert.match(root.find('status').textContent, /ya no encuentra esa autorización/);
    });
});

test('R09: a missing attempt can reconcile to a different active authorization', async () => {
    await fixture(async ({ root, timers, tick, route }) => {
        route(({ path }) => path ? missing() : snapshot(active('gses_other')));
        await tick();
        assert.ok(root.find('session'));
        assert.equal(root.find('connect'), undefined);
        assert.equal(timers.size, 1, 'the replacement authorization is polled');
    });
});

test('R09: a network error preserves the active attempt and allows a later poll to complete', async () => {
    await fixture(async ({ root, timers, tick, route }) => {
        route(() => { throw new Error('Red temporalmente no disponible.'); });
        await tick();
        assert.ok(root.find('session'));
        assert.equal(root.find('connect'), undefined);
        assert.match(root.find('status').textContent, /Red temporalmente no disponible/);
        assert.doesNotMatch(root.find('status').textContent, /cancelada|ya no encuentra/);
        assert.equal(timers.size, 1);
        route(({ path }) => path ? response({ ...active(), state: 'cancelled' }) : snapshot());
        await tick();
        assert.ok(root.find('connect'));
        assert.match(root.find('status').textContent, /cancelada/);
    });
});

test('R09: a failed reconciliation is an error, not a fabricated cancellation; retry reads Core', async () => {
    await fixture(async ({ app, root, tick, route }) => {
        route(({ path }) => {
            if (path) return missing();
            throw new Error('Core no responde.');
        });
        await tick();
        assert.match(root.find('status').textContent, /Core no responde/);
        assert.doesNotMatch(root.find('status').textContent, /cancelada|ya no encuentra/);
        assert.ok(root.find('retry'));
        route(() => snapshot());
        root.find('retry').fire('click');
        await flush();
        assert.ok(root.find('connect'));
        assert.equal(root.find('session'), undefined);
        app.setVisibility({ visible: false });
    });
});

test('R09: a failed cancel request does not announce cancellation or discard the attempt', async () => {
    await fixture(async ({ root, timers, route }) => {
        route(() => { throw new Error('No se pudo enviar la cancelación.'); });
        root.find('cancel').fire('click');
        await flush();
        assert.ok(root.find('session'));
        assert.ok(root.find('cancel'));
        assert.equal(root.find('connect'), undefined);
        assert.match(root.find('status').textContent, /No se pudo enviar la cancelación/);
        assert.doesNotMatch(root.find('status').textContent, /Autorización cancelada/);
        assert.equal(timers.size, 1);
    });
});

test('an old snapshot cannot clear an authorization started after that snapshot request', async () => {
    await fixture(async ({ app, root, timers, route }) => {
        route(() => snapshot());
        await app.refresh({ force: true });
        const stale = deferred();
        route(({ method }) => method === 'POST' ? response(active('gses_new'), 202) : stale.promise);
        const refreshing = app.refresh({ force: true });
        root.find('connect').fire('click');
        await flush();
        stale.resolve(snapshot());
        await refreshing;
        assert.ok(root.find('session'));
        assert.equal(root.find('connect'), undefined);
        assert.equal(timers.size, 1, 'the new attempt remains live, rather than only leaving its old DOM behind');
    });
});

for (const late of ['poll', 'cancel']) {
    test(`a late ${late} response cannot finish a newer authorization`, async () => {
        await fixture(async ({ root, tick, route }) => {
            const stale = deferred();
            let pollCount = 0;
            route(({ path, method }) => {
                if (!path) return snapshot();
                if (path === '/sessions' && method === 'POST') return response(active('gses_new'), 202);
                if (path.endsWith('/cancel')) return late === 'cancel' ? stale.promise : response({ ...active(), state: 'cancelled' });
                pollCount += 1;
                return late === 'poll' && pollCount === 1 ? stale.promise : response({ ...active(), state: 'denied' });
            });
            if (late === 'poll') { await tick(); root.find('cancel').fire('click'); }
            else { root.find('cancel').fire('click'); await flush(); await tick(); }
            await flush();
            assert.ok(root.find('connect'));
            root.find('connect').fire('click');
            await flush();
            stale.resolve(response({ ...active(), state: 'cancelled' }));
            await flush();
            assert.ok(root.find('session'), 'the new attempt remains active');
            assert.equal(root.find('connect'), undefined);
            assert.equal(root.find('status').textContent, '', 'the old result does not overwrite the new attempt');
        });
    });
}
