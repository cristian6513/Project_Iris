// Tests of Conexiones (F10.4) against a scripted API: run with `node --test apps/conexiones/tests/*.test.mjs`.
// Elements are found by data-part, so the app's classes can change without breaking them.
import { test } from 'node:test';
import assert from 'node:assert/strict';

class Node {
    constructor(tag) {
        Object.assign(this, { tag, children: [], listeners: {}, attributes: {}, dataset: {}, textContent: '', className: '',
            disabled: false, checked: false });
    }
    append(...nodes) { this.children.push(...nodes); }
    replaceChildren(...nodes) { this.children = nodes; }
    setAttribute(name, value) { this.attributes[name] = String(value); }
    addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
    fire(type) { for (const fn of this.listeners[type] || []) fn({ type, target: this }); }
    focus() { globalThis.document.activeElement = this; }
    all() { return [this, ...this.children.flatMap((child) => child.all())]; }
    find(part) { return this.all().find((node) => node.dataset.part === part); }
    findAll(part) { return this.all().filter((node) => node.dataset.part === part); }
    text() { return this.all().map((node) => node.textContent).join(' '); }
}
globalThis.document = { activeElement: null, createElement: (tag) => new Node(tag) };

const { createApp, relativeTime } = await import('../ui/main.js');

const URL = 'https://accounts.google.com/o/oauth2/v2/auth?client_id=sintetico&state=s';
const ACCOUNT = {
    id: 'gacc_' + 'a'.repeat(32), label: 'ana@example.com', email: 'ana@example.com', auth_state: 'partial', error: null,
    generation: 1, revision: 3, remote_revocation: 'not_requested',
    services: { 'calendar.read': { granted: true, enabled: false, availability: 'not_checked' },
                'gmail.read': { granted: false, enabled: false, availability: 'permission_missing' } },
    sync: { 'calendar.read': { state: 'idle', last_success_at: new Date(Date.now() - 5 * 60000).toISOString(), records: 12,
                               next_attempt_at: null, error: null, coverage: { calendars: 2 } } },
};

/** A scripted /api/connections/google: answers by «METHOD path», records every call. */
function api(routes) {
    const calls = [];
    const fetchImpl = async (url, init = {}) => {
        const method = init.method || 'GET';
        const path = url.replace('/api/connections/google', '') || '/';
        calls.push({ method, path, body: init.body ? JSON.parse(init.body) : undefined });
        const route = routes[`${method} ${path}`];
        const [status, body] = typeof route === 'function' ? route(calls.at(-1)) : (route || [404, {}]);
        return { ok: status < 400, status, json: async () => body };
    };
    return { calls, fetchImpl };
}

const snapshot = (fields = {}) => [200, { schema: 'iris.google-connections/v1', available: true, accounts: [], session: null, ...fields }];
const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

function open(routes, extra = {}) {
    const server = api(routes);
    const confirms = [];
    const briefs = [];
    const app = createApp({ id: 'conexiones', setBrief: (text) => briefs.push(text),
                            confirm: async (options) => { confirms.push(options); return extra.answer ?? false; } },
                          { fetchImpl: server.fetchImpl, requestId: () => 'peticion-0001' });
    const root = new Node('div');
    app.mount(root);
    return { app, root, server, confirms, briefs };
}

test('opening reads the state once and starts nothing', async () => {
    const { app, root, server, briefs } = open({ 'GET /': snapshot() });
    await app.refresh({ opening: true });
    app.setVisibility({ visible: true });
    await settle();
    assert.deepEqual(server.calls.map((call) => call.method), ['GET'], 'no authorization, no read');
    assert.ok(root.find('connect'), 'connecting is offered, only on a click');
    assert.match(root.find('new-title').textContent, /Conectar Google/);
    assert.deepEqual(briefs, ['Sin cuentas']);
    app.dispose();
});

test('without a client in this installation it says so and offers nothing', async () => {
    const { app, root } = open({ 'GET /': snapshot({ available: false }) });
    await app.refresh({ opening: true });
    assert.match(root.find('unavailable').textContent, /no está disponible/);
    assert.equal(root.find('connect'), undefined);
    app.dispose();
});

test('an error is said, not shown as an empty list, and «Reintentar» reads again', async () => {
    let first = true;
    const { app, root, server } = open({ 'GET /': () => (first ? (first = false, [503, { error: { code: 'client_unavailable', message: 'No disponible.' } }]) : snapshot()) });
    await app.refresh({ opening: true });
    assert.match(root.find('status').textContent, /No disponible\. Pulsa «Reintentar»/);
    assert.equal(root.find('connect'), undefined);
    root.find('retry').fire('click');
    await settle();
    assert.equal(server.calls.length, 2);
    assert.ok(root.find('connect'));
    app.dispose();
});

test('connecting sends only the chosen services, once, and waits for Google honestly', async () => {
    const session = { id: 'gses_' + 'b'.repeat(32), state: 'awaiting_user', services: ['calendar.read', 'gmail.read'],
                      account_id: null, authorization_url: URL, expires_at: new Date(Date.now() + 600000).toISOString(),
                      error: null, result_account_id: null };
    const { app, root, server } = open({ 'GET /': snapshot(), 'POST /sessions': [202, session] });
    await app.refresh({ opening: true });
    const gmail = root.findAll('choice-input').find((node) => node.dataset.key === 'new-choice:gmail.read');
    gmail.checked = true;
    gmail.fire('change');
    root.find('connect').fire('click');
    root.find('connect').fire('click'); // a double click
    await settle();
    const posts = server.calls.filter((call) => call.method === 'POST');
    assert.deepEqual(posts, [{ method: 'POST', path: '/sessions',
                               body: { services: ['calendar.read', 'gmail.read'], request_id: 'peticion-0001' } }]);
    assert.match(root.find('session-title').textContent, /Esperando autorización/);
    const link = root.find('consent');
    assert.equal(link.href, URL);
    assert.equal(link.target, '_blank');
    assert.equal(link.rel, 'noopener noreferrer');
    assert.equal(root.find('connect'), undefined, 'one authorization at a time');
    app.dispose();
});

test('only a Google consent page becomes a link', async () => {
    const session = { id: 'gses_' + 'c'.repeat(32), state: 'awaiting_user', services: ['calendar.read'],
                      authorization_url: 'https://accounts.google.com.evil.example/auth', expires_at: null, error: null };
    const { app, root } = open({ 'GET /': snapshot({ session }) });
    await app.refresh({ opening: true });
    assert.equal(root.find('consent'), undefined);
    assert.match(root.find('session').text(), /Preparando/);
    app.dispose();
});

test('the wait is polled only while visible, and the end is said and the accounts read again', async () => {
    const id = 'gses_' + 'd'.repeat(32);
    let state = 'awaiting_user';
    const { app, root, server } = open({
        'GET /': () => snapshot(state === 'awaiting_user'
            ? { session: { id, state, services: ['calendar.read'], authorization_url: URL, error: null } }
            : { accounts: [ACCOUNT] }),
        [`GET /sessions/${id}`]: () => [200, { id, state, services: ['calendar.read'], authorization_url: null, error: null }],
    });
    const realTimeout = globalThis.setTimeout;
    const pending = [];
    globalThis.setTimeout = (fn) => { pending.push(fn); return pending.length; };
    globalThis.clearTimeout = (index) => { if (index) pending[index - 1] = null; };
    try {
        await app.refresh({ opening: true });
        assert.equal(pending.filter(Boolean).length, 0, 'hidden: no polling');
        app.setVisibility({ visible: true });
        assert.equal(pending.filter(Boolean).length, 1);
        state = 'partial';
        await pending.at(-1)();
        await new Promise((resolve) => realTimeout(resolve, 0));
        assert.match(root.find('status').textContent, /no concedió todos los permisos/);
        assert.ok(server.calls.some((call) => call.path === `/sessions/${id}`));
        assert.match(root.find('auth').textContent, /Permisos parciales/);
        app.setVisibility({ visible: false });
        assert.equal(pending.filter(Boolean).length, 0, 'hidden again: the timer is cleared');
    } finally {
        globalThis.setTimeout = realTimeout;
        globalThis.clearTimeout = clearTimeout;
        app.dispose();
    }
});

test('an account shows permissions as granted and how far it was read, in numbers', async () => {
    const { app, root, briefs } = open({ 'GET /': snapshot({ accounts: [ACCOUNT] }) });
    await app.refresh({ opening: true });
    const [calendar, gmail] = root.findAll('service');
    assert.match(calendar.text(), /Permiso concedido/);
    assert.match(calendar.text(), /Última lectura hace 5 min · 12 eventos/);
    assert.match(gmail.text(), /Sin permiso/);
    assert.equal(gmail.find('periodic'), undefined, 'nothing to enable without a permission');
    assert.deepEqual(briefs, ['1 cuenta']);
    assert.equal(relativeTime(new Date(Date.now() - 3 * 3600000).toISOString()), 'hace 3 h');
    app.dispose();
});

test('periodic reading is turned on with the revision read; a conflict reloads', async () => {
    let conflict = true;
    const { app, root, server } = open({
        'GET /': snapshot({ accounts: [ACCOUNT] }),
        [`PATCH /accounts/${ACCOUNT.id}/services`]: () => (conflict
            ? (conflict = false, [409, { error: { code: 'revision_conflict', message: 'Otra ventana cambió esta cuenta; vuelve a cargarla.' } }])
            : [200, { ...ACCOUNT, revision: 4 }]),
    });
    await app.refresh({ opening: true });
    const toggle = root.find('periodic-input');
    toggle.checked = true;
    toggle.fire('change');
    await settle();
    await settle();
    const patch = server.calls.find((call) => call.method === 'PATCH');
    assert.deepEqual(patch.body, { services: ['calendar.read'], revision: 3 });
    assert.match(root.find('status').textContent, /Otra ventana cambió/);
    assert.equal(server.calls.filter((call) => call.method === 'GET').length, 2, 'read again after the conflict');
    app.dispose();
});

test('«Actualizar datos» asks Core for a read and shows it running', async () => {
    const { app, root, server } = open({
        'GET /': snapshot({ accounts: [ACCOUNT] }),
        [`POST /accounts/${ACCOUNT.id}/sync`]: [202, { 'calendar.read': { state: 'running', records: 12 } }],
    });
    await app.refresh({ opening: true });
    root.find('sync').fire('click');
    await settle();
    assert.deepEqual(server.calls.at(-1), { method: 'POST', path: `/accounts/${ACCOUNT.id}/sync`, body: {} });
    assert.equal(root.find('sync').textContent, 'Leyendo…');
    assert.equal(root.find('sync').disabled, true);
    app.dispose();
});

test('disconnecting asks in Iris\' dialog with its exact effect; «no» changes nothing', async () => {
    for (const answer of [false, true]) {
        const { app, root, server, confirms } = open({
            'GET /': snapshot({ accounts: [ACCOUNT] }),
            [`POST /accounts/${ACCOUNT.id}/disconnect`]: [200, { ...ACCOUNT, auth_state: 'disconnected', remote_revocation: 'pending' }],
        }, { answer });
        await app.refresh({ opening: true });
        root.find('disconnect').fire('click');
        const revoke = root.findAll('choice-input').find((node) => node.dataset.key === `revoke:${ACCOUNT.id}`);
        revoke.checked = true;
        revoke.fire('change');
        root.find('confirm-disconnect').fire('click');
        await settle();
        await settle();
        assert.equal(confirms.length, 1);
        assert.equal(confirms[0].tone, 'danger');
        assert.match(confirms[0].message, /revoque el acceso de Iris/);
        assert.match(confirms[0].message, /Los datos ya leídos se conservan/);
        const posted = server.calls.filter((call) => call.method === 'POST');
        if (!answer) {
            assert.deepEqual(posted, [], 'cancelled in the dialog: nothing sent');
        } else {
            assert.deepEqual(posted[0].body, { revoke_remote: true, clear_cache: false, revision: 3 });
        }
        app.dispose();
    }
});

test('retiring the app cancels its read in flight and draws nothing late', async () => {
    const signals = [];
    let release;
    const app = createApp({ id: 'conexiones' }, { fetchImpl: (url, init) => {
        signals.push(init.signal);
        return new Promise((resolve) => { release = resolve; });
    } });
    const root = new Node('div');
    app.mount(root);
    const reading = app.refresh({ opening: true });
    app.dispose();
    assert.equal(signals[0].aborted, true, 'the read in flight was cancelled');
    release({ ok: true, status: 200, json: async () => snapshot()[1] });
    await reading;
    assert.equal(root.children.length, 0, 'a late answer is not drawn');
});

test('a poll that brings nothing new does not redraw, so a click in progress is never lost', async () => {
    const id = 'gses_' + 'e'.repeat(32);
    const session = { id, state: 'awaiting_user', services: ['calendar.read'], authorization_url: URL, error: null };
    const { app, root, server } = open({ 'GET /': snapshot({ session }), [`GET /sessions/${id}`]: [200, session] });
    const realTimeout = globalThis.setTimeout;
    const pending = [];
    globalThis.setTimeout = (fn) => { pending.push(fn); return pending.length; };
    globalThis.clearTimeout = () => {};
    try {
        await app.refresh({ opening: true });
        app.setVisibility({ visible: true });
        const cancel = root.find('cancel');
        const polls = server.calls.length;
        pending.at(-1)();
        await new Promise((resolve) => realTimeout(resolve, 0));
        await new Promise((resolve) => realTimeout(resolve, 0));
        assert.equal(server.calls.length, polls + 1, 'the poll ran');
        assert.equal(root.find('cancel'), cancel, 'the same button: nothing was redrawn');
    } finally {
        globalThis.setTimeout = realTimeout;
        globalThis.clearTimeout = clearTimeout;
        app.dispose();
    }
});

test("a disconnected account is removed only when Iris' dialog says yes", async () => {
    const gone = { ...ACCOUNT, auth_state: 'disconnected', remote_revocation: 'failed', sync: {} };
    const { app, root, server } = open({ 'GET /': snapshot({ accounts: [gone] }) }, { answer: false });
    await app.refresh({ opening: true });
    root.find('forget').fire('click');
    await settle();
    await settle();
    assert.ok(!server.calls.some((call) => call.method === 'DELETE'), 'no: nothing removed');
    app.dispose();
});

test('a disconnected account shows no permissions in use and can be removed after Iris asks', async () => {
    const gone = { ...ACCOUNT, auth_state: 'disconnected', remote_revocation: 'failed', sync: {} };
    const { app, root, server, confirms } = open({
        'GET /': snapshot({ accounts: [gone] }),
        [`DELETE /accounts/${ACCOUNT.id}`]: [200, { id: ACCOUNT.id, forgotten: true }],
    }, { answer: true });
    await app.refresh({ opening: true });
    assert.equal(root.find('services'), undefined);
    assert.match(root.find('revocation').textContent, /Google no revocó el acceso/);
    assert.equal(root.find('disconnect'), undefined);
    root.find('forget').fire('click');
    await settle();
    await settle();
    assert.equal(confirms[0].tone, 'danger');
    assert.ok(server.calls.some((call) => call.method === 'DELETE' && call.path === `/accounts/${ACCOUNT.id}`));
    app.dispose();
});
