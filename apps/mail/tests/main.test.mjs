import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createApp } from '../ui/main.js';
import { createMailClient } from '../ui/adapter.js';
import { accountStatus, messageDate } from '../ui/state.js';

class Node {
    constructor(tag) { Object.assign(this, { tag, children: [], listeners: {}, attributes: {}, dataset: {}, textContent: '', className: '', disabled: false, value: '', style: { setProperty() {} } }); }
    append(...nodes) { this.children.push(...nodes); }
    prepend(...nodes) { this.children.unshift(...nodes); }
    replaceChildren(...nodes) { this.children = nodes; }
    setAttribute(name, value) { this.attributes[name] = String(value); }
    addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
    fire(type, extra = {}) { for (const fn of this.listeners[type] || []) fn({ type, target: this, preventDefault() {}, ...extra }); }
    focus() { globalThis.document.activeElement = this; }
    contains(node) { return this.all().includes(node); }
    all() { return [this, ...this.children.flatMap((node) => node.all())]; }
    find(part) { return this.all().find((node) => node.dataset.part === part); }
    findAll(part) { return this.all().filter((node) => node.dataset.part === part); }
    key(key) { return this.all().find((node) => node.dataset.key === key); }
    text() { return this.all().map((node) => node.textContent).join(' '); }
}
globalThis.document = { activeElement: null, createElement: (tag) => new Node(tag) };
const ACCOUNT = { id: 'gacc_' + '1'.repeat(32), email: 'one@example.test', auth_state: 'connected', granted: true,
    sync: { state: 'idle', last_success_at: '2026-10-17T10:00:00Z', coverage: { max_messages: 200 } } };
const MESSAGE = { id: 'mail_' + '2'.repeat(40), account_id: ACCOUNT.id, subject: 'Reunión', sender: 'Cliente Orion <cliente@example.test>',
    to: ACCOUNT.email, date_ms: 1792245600000, snippet: 'Hola equipo', unread: true, starred: true, important: false };
const DATA = { schema: 'iris.mail/v1', available: true, read_only: true, accounts: [ACCOUNT], messages: [MESSAGE],
    warnings: [], total: 1, offset: 0, counts: { inbox: 1, all: 1 }, next_cursor: null };
const settle = () => new Promise((resolve) => setTimeout(resolve, 0));
const defer = () => { let resolve; const promise = new Promise((yes) => { resolve = yes; }); return { promise, resolve }; };
function fakeTimers() {
    let next = 0; const pending = new Map();
    return { setTimeout(fn, ms) { const key = ++next; pending.set(key, { fn, ms }); return key; },
        clearTimeout(key) { pending.delete(key); }, pending,
        async advance(ms) { for (const [key, item] of [...pending]) if (item.ms === ms) { pending.delete(key); await item.fn(); } await settle(); } };
}
function setup({ data = DATA, route } = {}) {
    const calls = [], opened = [], briefs = [], timers = fakeTimers();
    const fetchImpl = async (url, init = {}) => {
        const call = { url, init, method: init.method || 'GET', path: url.split('?')[0], params: new URL(url, 'http://local').searchParams,
            body: init.body ? JSON.parse(init.body) : null }; calls.push(call);
        const result = await route?.(call, calls) || [200, call.path === '/api/models/apps' ? {
            default: { provider: 'custom:test', model: 'classifier' }, inventory: { providers: [
                { id: 'custom:test', label: 'Mi proveedor', authenticated: true, models: ['classifier', 'classifier-2'] }] } }
            : call.path.endsWith('/body') ? { message_id: data.messages[0].id, account_id: data.messages[0].account_id,
            body: { schema: 'iris.mail-body/v1', text: 'Full text: ' + data.messages[0].snippet, source_format: 'plain' } }
            : call.path.includes('/messages/') ? { message: data.messages[0] } : data];
        return { ok: result[0] < 400, status: result[0], json: async () => result[1] };
    };
    const app = createApp({ openApp: (id) => opened.push(id), setBrief: (brief) => briefs.push(brief) }, { fetchImpl, timers });
    const root = new Node('div'); app.mount(root);
    return { app, root, calls, opened, briefs, timers };
}

test('mount is passive and opening reads only the local cache with no-store', async () => {
    const { app, root, calls } = setup(); assert.equal(calls.length, 0);
    await app.refresh({ opening: true });
    assert.equal(calls.length, 1); assert.equal(calls[0].method, 'GET'); assert.equal(calls[0].path, '/api/mail');
    assert.equal(calls[0].init.cache, 'no-store'); assert.match(root.text(), /Solo lectura|SOLO LECTURA/); app.dispose();
});

for (const enabled of [true,false]) test(`sync policy ${enabled} is visible without changing it`,async()=>{
    const {app,root,calls,opened}=setup({data:{...DATA,accounts:[{...ACCOUNT,sync_enabled:enabled}]}});
    await app.refresh({opening:true});
    assert.match(root.find('sync-policy').textContent,enabled?/cada 15 minutos/:/apagada/);
    assert.match(root.find('last-read').textContent,/Última lectura/);
    root.find('sync-settings').fire('click');
    assert.deepEqual(opened,['conexiones']);
    assert.ok(calls.every(call=>call.method==='GET'));
    app.dispose();
});

test('real message selection requests fresh detail and never marks the message read', async () => {
    const { app, root, calls } = setup(); await app.refresh({ opening: true });
    root.find('message').fire('click'); await settle();
    assert.equal(root.find('detail-title').textContent, MESSAGE.subject); assert.equal(root.find('message-text').textContent, 'Full text: ' + MESSAGE.snippet);
    assert.match(root.text(), /Sigue sin leer/);
    assert.deepEqual(calls.filter((call) => call.method === 'POST').map((call) => call.path), [`/api/mail/messages/${MESSAGE.id}/body`]); app.dispose();
});

test('all remote content is text, with no remote image, HTML or attachment element', async () => {
    const bad = { ...MESSAGE, subject: '<img src=https://tracker.test/p>', sender: '<svg onload=alert(1)>', snippet: '<script>bad()</script>' };
    const { app, root } = setup({ data: { ...DATA, messages: [bad] } }); await app.refresh({ opening: true }); root.find('message').fire('click'); await settle();
    assert.equal(root.find('detail-title').textContent, bad.subject); assert.equal(root.find('message-text').textContent, 'Full text: ' + bad.snippet);
    assert.ok(root.all().filter((node) => node.tag === 'img').every((node) => node.src.startsWith('/face/public/')));
    assert.equal(root.all().filter((node) => ['iframe', 'a', 'script', 'object'].includes(node.tag)).length, 0); app.dispose();
});

test('changing account cannot render a previous account detail arriving late', async () => {
    const wait = defer(); const { app, root } = setup({ route: (call) => call.path.includes('/messages/') ? wait.promise : null });
    await app.refresh({ opening: true }); root.find('message').fire('click');
    root.key('account:all').fire('click'); await settle(); wait.resolve([200, { message: MESSAGE }]); await settle();
    assert.equal(root.find('excerpt'), undefined); app.dispose();
});

test('out-of-order detail results cannot replace the latest selected message', async () => {
    const other = { ...MESSAGE, id: 'mail_' + '3'.repeat(40), subject: 'Segundo mensaje' };
    const wait = defer(); const { app, root } = setup({ data: { ...DATA, messages: [MESSAGE, other], total: 2 },
        route: (call) => call.path.endsWith(MESSAGE.id) ? wait.promise : call.path.endsWith(other.id) ? [200, { message: other }] : null });
    await app.refresh({ opening: true }); root.key(`message:${MESSAGE.id}`).fire('click'); root.key(`message:${other.id}`).fire('click'); await settle();
    wait.resolve([200, { message: MESSAGE }]); await settle(); assert.equal(root.find('detail-title').textContent, other.subject); app.dispose();
});

test('folder and status filter reset pagination and request actual local filters', async () => {
    const { app, root, calls } = setup(); await app.refresh({ opening: true });
    root.key('folder:starred').fire('click'); await settle(); assert.equal(calls.at(-1).params.get('folder'), 'starred');
    root.key('filter:unread').fire('click'); await settle(); assert.equal(calls.at(-1).params.get('filter'), 'unread');
    assert.equal(calls.at(-1).params.get('cursor'), ''); app.dispose();
});

test('search is debounced and keeps the current input and caret while typing', async () => {
    const { app, root, calls, timers } = setup(); await app.refresh({ opening: true });
    const search = root.find('search'); search.value = 'Orion'; search.focus(); search.fire('input');
    assert.equal(calls.length, 1); assert.equal(root.find('search'), search);
    await timers.advance(250); assert.equal(calls.at(-1).params.get('query'), 'Orion');
    assert.equal(globalThis.document.activeElement.dataset.key, 'search'); app.dispose();
});

test('next and previous use server cursor and its actual page counts', async () => {
    const { app, root, calls } = setup({ route: (call) => call.path === '/api/mail' ? [200, { ...DATA, total: 80,
        offset: call.params.get('cursor') ? 40 : 0, next_cursor: call.params.get('cursor') ? null : 'cursor-2' }] : null });
    await app.refresh({ opening: true }); root.find('next').fire('click'); await settle();
    assert.equal(calls.at(-1).params.get('cursor'), 'cursor-2'); assert.match(root.find('page-count').textContent, /41/);
    root.find('previous').fire('click'); await settle(); assert.equal(calls.at(-1).params.get('cursor'), ''); app.dispose();
});

test('a sync-changed cursor restarts at page one and explains the recovery', async () => {
    const { app, root, calls } = setup({ route: (call) => call.params.get('cursor')
        ? [409, { error: { code: 'page_changed', message: 'El correo cambió.' } }] : [200, { ...DATA, total: 80, next_cursor: 'old' }] });
    await app.refresh({ opening: true }); root.find('next').fire('click'); await settle(); await settle();
    assert.equal(calls.at(-1).params.get('cursor'), ''); assert.match(root.find('status-text').textContent, /correo cambió/); app.dispose();
});

test('an error while changing pages never displays the old page as the new one', async () => {
    const { app, root } = setup({ route: (call) => call.params.get('cursor') ? [503, { error: { message: 'Ocupado' } }]
        : [200, { ...DATA, next_cursor: 'next' }] });
    await app.refresh({ opening: true }); root.find('next').fire('click'); await settle();
    assert.equal(root.findAll('message').length, 0); assert.match(root.find('status-text').textContent, /Ocupado/);
    root.find('retry').fire('click'); await settle(); assert.equal(root.findAll('message').length, 1); app.dispose();
});

test('manual refresh requests Gmail only for the selected eligible accounts, once per click batch', async () => {
    const { app, root, calls } = setup({ route: (call) => call.method === 'POST' ? [202, { accepted: true, errors: [] }] : null });
    await app.refresh({ opening: true }); const sync = root.find('sync'); sync.fire('click'); sync.fire('click'); await settle();
    assert.deepEqual(calls.filter((call) => call.method === 'POST').map((call) => call.body), [{ account_ids: [ACCOUNT.id] }]);
    assert.match(root.find('status-text').textContent, /solicitada/); app.dispose();
});

test('an accepted HTTP response with accepted=false never becomes a successful update notice', async () => {
    const { app, root } = setup({ route: (call) => call.method === 'POST' ? [202, { accepted: false, errors: [{ code: 'busy' }] }] : null });
    await app.refresh({ opening: true }); root.find('sync').fire('click'); await settle();
    assert.match(root.find('status-text').textContent, /No se pudo solicitar/); app.dispose();
});

test('an account without permission cannot use a second account grant to sync', async () => {
    const second = { ...ACCOUNT, id: 'gacc_' + '4'.repeat(32), granted: false };
    const { app, root, calls } = setup({ data: { ...DATA, accounts: [ACCOUNT, second] } }); await app.refresh({ opening: true });
    root.key(`account:${second.id}`).fire('click'); await settle(); assert.equal(root.find('sync').disabled, true);
    root.find('sync').fire('click'); assert.equal(calls.filter((call) => call.method === 'POST').length, 0); app.dispose();
});

test('opening with no accounts offers Conexiones without initiating OAuth', async () => {
    const { app, root, opened, calls } = setup({ data: { ...DATA, accounts: [], messages: [], total: 0 } }); await app.refresh({ opening: true });
    assert.match(root.find('empty').textContent, /Conecta una cuenta/); root.find('connections').fire('click');
    assert.deepEqual(opened, ['conexiones']); assert.ok(calls.every((call) => call.method === 'GET')); app.dispose();
});

test('unavailable and failed initial reads are not represented as an empty inbox', async () => {
    for (const result of [[200, { ...DATA, available: false, messages: [], accounts: [] }], [503, { error: { message: 'No disponible' } }]]) {
        const { app, root } = setup({ route: () => result }); await app.refresh({ opening: true });
        assert.match(root.find('empty').textContent, /no está disponible|No se pudo consultar/); app.dispose();
    }
});

test('visual polling stops when hidden and starts only cache reads while visible', async () => {
    const { app, calls, timers } = setup(); await app.refresh({ opening: true }); assert.equal(timers.pending.size, 0);
    app.setVisibility({ visible: true }); await settle(); await timers.advance(30000); assert.ok(calls.length >= 2);
    assert.ok(calls.every((call) => call.method === 'GET'));
    app.setVisibility({ visible: false }); assert.equal(timers.pending.size, 0); app.dispose();
});

test('dispose aborts the pending cache read and a late response cannot restore content', async () => {
    const wait = defer(); const { app, root, calls } = setup({ route: () => wait.promise });
    const pending = app.refresh({ opening: true }); await settle(); app.dispose(); assert.equal(calls[0].init.signal.aborted, true);
    wait.resolve([200, DATA]); await pending; assert.equal(root.children.length, 0);
});

test('adapter disposal refuses further requests without touching the network', async () => {
    let calls = 0; const client = createMailClient({ fetchImpl: () => { calls += 1; } }); client.dispose();
    await assert.rejects(() => client.read({}), { name: 'AbortError' }); assert.equal(calls, 0);
});

test('account errors and malformed dates have honest labels', () => {
    assert.equal(accountStatus({ ...ACCOUNT, auth_state: 'reauth_required' }), 'Vuelve a conectar');
    assert.equal(messageDate(null), 'Fecha no disponible'); assert.equal(messageDate(1e30), 'Fecha no disponible');
});

test('before the first confirmed read the app says loading, never that the account is absent', () => {
    const { app, root, calls } = setup();
    assert.match(root.text(), /Leyendo/); assert.ok(!/Conecta una cuenta|no está disponible/.test(root.text()));
    assert.equal(calls.length, 0); app.dispose();
});

test('showing a window whose first read was interrupted retries the local read', async () => {
    const wait = defer(); let reads = 0;
    const { app, root } = setup({ route: () => ++reads === 1 ? wait.promise : [200, DATA] });
    const pending = app.refresh({ opening: true }); app.setVisibility({ visible: false }); app.setVisibility({ visible: true });
    await settle(); assert.equal(root.findAll('message').length, 1);
    wait.resolve([200, { ...DATA, messages: [] }]); await pending;
    assert.equal(root.findAll('message').length, 1); app.dispose();
});

test('an unchanged visual poll does not replace the button under an ongoing pointer click', async () => {
    const { app, root, timers } = setup(); await app.refresh({ opening: true });
    app.setVisibility({ visible: true }); await settle();
    const original = root.find('message'); await timers.advance(30000);
    assert.equal(root.find('message'), original); app.dispose();
});


test('hiding cancels a pending search debounce and returning reads the chosen query once', async () => {
    const { app, root, calls, timers } = setup(); await app.refresh({ opening: true });
    app.setVisibility({ visible: true }); await settle();
    const search = root.find('search'); search.value = 'latest'; search.fire('input');
    app.setVisibility({ visible: false }); const before = calls.length;
    await timers.advance(250); assert.equal(calls.length, before);
    app.setVisibility({ visible: true }); await settle();
    assert.equal(calls.at(-1).params.get('query'), 'latest'); app.dispose();
});
test('a detail interrupted by hiding cannot leave a permanent loading panel on return', async () => {
    const wait = defer();
    const { app, root } = setup({ route: (call) => call.path.includes('/messages/') ? wait.promise : null });
    await app.refresh({ opening: true }); app.setVisibility({ visible: true }); await settle();
    root.find('message').fire('click'); assert.match(root.text(), /Leyendo el detalle/);
    app.setVisibility({ visible: false }); app.setVisibility({ visible: true }); await settle();
    assert.ok(!/Leyendo el detalle/.test(root.text()));
    assert.equal(root.find('message').attributes['aria-pressed'], 'false');
    wait.resolve([200, { message: MESSAGE }]); await settle();
    assert.equal(root.find('excerpt'), undefined); app.dispose();
});

test('a body response cannot cross account selection and its request is aborted', async () => {
    const wait = defer();
    const { app, root, calls } = setup({ route: (call) => call.path.endsWith('/body') ? wait.promise : null });
    await app.refresh({ opening: true }); root.find('message').fire('click'); await settle();
    assert.match(root.text(), /Cargando el texto/);
    const request = calls.find((call) => call.path.endsWith('/body'));
    root.key('account:all').fire('click'); await settle();
    assert.equal(request.init.signal.aborted, true);
    wait.resolve([200, { message_id: MESSAGE.id, account_id: ACCOUNT.id, body: { schema: 'iris.mail-body/v1', text: 'PRIVATE OLD BODY' } }]);
    await settle(); assert.ok(!root.text().includes('PRIVATE OLD BODY')); app.dispose();
});

test('body failure preserves the local snippet and an explicit retry can recover', async () => {
    let tries = 0;
    const { app, root } = setup({ route: (call) => call.path.endsWith('/body') && ++tries === 1
        ? [503, { error: { code: 'offline', message: 'Sin conexión. El extracto sigue disponible.' } }] : null });
    await app.refresh({ opening: true }); root.find('message').fire('click'); await settle();
    assert.equal(root.find('excerpt').textContent, MESSAGE.snippet); assert.match(root.find('body-error').textContent, /Sin conexión/);
    root.find('body-retry').fire('click'); await settle();
    assert.equal(root.find('message-text').textContent, 'Full text: ' + MESSAGE.snippet); assert.equal(root.find('body-error'), undefined); app.dispose();
});

test('body HTML-derived text is never interpreted and a visual poll does not refetch it', async () => {
    const literal = '<img src=https://tracker.test/p><script>evil()</script>\nMás de 500 caracteres: ' + 'test '.repeat(200);
    const { app, root, calls, timers } = setup({ route: (call) => call.path.endsWith('/body') ? [200, {
        message_id: MESSAGE.id, account_id: ACCOUNT.id, body: { schema: 'iris.mail-body/v1', text: literal, source_format: 'html_text' } }] : null });
    await app.refresh({ opening: true }); app.setVisibility({ visible: true }); await settle();
    root.find('message').fire('click'); await settle(); assert.equal(root.find('message-text').textContent, literal);
    assert.match(root.text(), /Versión de texto/);
    assert.ok(root.all().filter((node) => node.tag === 'img').every((node) => node.src.startsWith('/face/public/')));
    await timers.advance(30000); assert.equal(root.find('message-text').textContent, literal);
    assert.equal(calls.filter((call) => call.path.endsWith('/body')).length, 1); app.dispose();
});

test('hiding clears even a completed body and returning does not silently read Gmail again', async () => {
    const { app, root, calls } = setup(); await app.refresh({ opening: true });
    app.setVisibility({ visible: true }); await settle(); root.find('message').fire('click'); await settle();
    assert.ok(root.find('message-text'));
    app.setVisibility({ visible: false }); app.setVisibility({ visible: true }); await settle();
    assert.equal(root.find('message-text'), undefined); assert.equal(root.find('message').attributes['aria-pressed'], 'false');
    assert.equal(calls.filter((call) => call.path.endsWith('/body')).length, 1); app.dispose();
});

test('empty and invalid full-body responses cannot masquerade as the same successful state', async () => {
    for (const body of [{ schema: 'iris.mail-body/v1', text: '', source_format: 'empty' }, { text: 'invalid' }]) {
        const { app, root } = setup({ route: (call) => call.path.endsWith('/body') ? [200, { message_id: MESSAGE.id, account_id: ACCOUNT.id, body }] : null });
        await app.refresh({ opening: true }); root.find('message').fire('click'); await settle();
        if (body.schema) assert.match(root.find('message-text').textContent, /cuerpo vacío/);
        else assert.ok(root.find('body-error'));
        app.dispose();
    }
});

const CLASS_ROUTE = { provider: 'custom:test', model: 'classifier', endpoint: 'http://127.0.0.1:9999/v1/', transport: 'chat_completions', fingerprint: 'f'.repeat(64) };
const CLASS_DATA = { schema: 'iris.mail-classification/v1', state: 'off', daily_used: 0, route: {}, results: [], options: {
    revision: 0, enabled: false, account_ids: [], folders: ['INBOX'], criteria: 'Compromisos personales', provider: 'custom:test', model: 'classifier',
    content: 'snippet', interval_minutes: 15, daily_limit: 10 } };

test('classification setup reads no provider or mail until opened, then requires route review and a new consent', async () => {
    const { app, root, calls } = setup({ route: (call) => call.path === '/api/mail/classification/probe' ? [200, CLASS_ROUTE]
        : call.path === '/api/mail/classification' ? [200, CLASS_DATA] : null });
    await app.refresh({ opening: true }); assert.equal(calls.length, 1);
    root.key('classification-toggle').fire('click'); await settle();
    assert.equal(calls.at(-1).method, 'GET'); assert.equal(root.key('classification-enable').disabled, true);
    const account = root.key(`classification:account:${ACCOUNT.id}`); account.checked = true; account.fire('change');
    root.key('classification-probe').fire('click'); await settle();
    assert.equal(calls.at(-1).path, '/api/mail/classification/probe'); assert.match(root.text(), /127.0.0.1:9999/);
    assert.equal(root.key('classification-enable').disabled, true);
    const consent = root.key('classification-consent'); consent.checked = true; consent.fire('change');
    assert.equal(root.key('classification-enable').disabled, false);
    root.key('classification-enable').fire('click'); await settle();
    const saved = calls.find((call) => call.method === 'PUT');
    assert.equal(saved.body.confirmed, true); assert.deepEqual(saved.body.options.account_ids, [ACCOUNT.id]);
    assert.equal(saved.body.options.content, 'snippet'); assert.deepEqual(saved.body.route, CLASS_ROUTE); app.dispose();
});

test('editing any consent field invalidates the reviewed route and disables the old approval immediately', async () => {
    const { app, root } = setup({ route: (call) => call.path.endsWith('/probe') ? [200, CLASS_ROUTE]
        : call.path === '/api/mail/classification' ? [200, { ...CLASS_DATA, options: { ...CLASS_DATA.options, account_ids: [ACCOUNT.id] } }] : null });
    await app.refresh({ opening: true }); root.key('classification-toggle').fire('click'); await settle();
    root.key('classification-probe').fire('click'); await settle();
    let consent = root.key('classification-consent'); consent.checked = true; consent.fire('change');
    const field = root.key('classification:criteria'); field.value = 'Criterio distinto'; field.fire('input');
    assert.equal(root.key('classification-enable').disabled, true);
    assert.equal(root.key('classification-consent').checked, false); assert.equal(root.key('classification-consent').disabled, true); app.dispose();
});

test('connected model selectors inherit only a blank choice and a model change discards consent', async () => {
    const { app, root, calls } = setup({ route: call => call.path.endsWith('/probe') ? [200, CLASS_ROUTE]
        : call.path === '/api/mail/classification' ? [200, { ...CLASS_DATA, options: {
            ...CLASS_DATA.options, provider: '', model: '', account_ids: [ACCOUNT.id] } }] : null });
    await app.refresh({ opening: true }); root.key('classification-toggle').fire('click'); await settle();
    assert.equal(root.find('classification-provider').tag, 'select');
    assert.equal(root.find('classification-model').value, 'classifier');
    assert.equal(calls.filter(c => c.method !== 'GET').length, 0);
    root.key('classification-probe').fire('click'); await settle();
    const consent = root.key('classification-consent'); consent.checked = true; consent.fire('change');
    const model = root.find('classification-model'); model.value = 'classifier-2'; model.fire('change');
    assert.equal(root.key('classification-consent'), undefined);
    assert.equal(root.key('classification-enable').disabled, true);
    assert.equal(calls.filter(c => c.method === 'PUT').length, 0); app.dispose();
});

test('pausing uses the current saved revision without new consent or provider probe', async () => {
    const { app, root, calls } = setup({ route: (call) => call.path === '/api/mail/classification' ? [200,
        { ...CLASS_DATA, options: { ...CLASS_DATA.options, revision: 7, enabled: true, account_ids: [ACCOUNT.id] } }] : null });
    await app.refresh({ opening: true }); root.key('classification-toggle').fire('click'); await settle();
    root.key('classification-pause').fire('click'); await settle();
    const saved = calls.find((call) => call.method === 'PUT');
    assert.equal(saved.body.options.enabled, false); assert.equal(saved.body.options.revision, 7); assert.equal(saved.body.confirmed, false);
    assert.equal(calls.filter((call) => call.path.endsWith('/probe')).length, 0); app.dispose();
});

test('AI output remains literal text and local corrections do not call Gmail or the classifier', async () => {
    const result = { id: 'f'.repeat(32), revision: 2, subject: '<img src=https://tracker.test>', state: 'completed',
        result: { label: 'incierto', reason: '<script>run()</script>', summary: 'Sugerencia inventada' } };
    const { app, root, calls } = setup({ route: (call) => call.path.includes('/classification') ? [200,
        call.path.endsWith('/correction') ? {} : { ...CLASS_DATA, results: [result] }] : null });
    await app.refresh({ opening: true }); root.key('classification-toggle').fire('click'); await settle();
    assert.match(root.text(), /<script>run\(\)<\/script>/);
    root.key(`correct:${result.id}:normal`).fire('click'); await settle();
    assert.deepEqual(calls.filter((call) => call.method === 'POST').map((call) => call.path), [`/api/mail/classification/${result.id}/correction`]);
    assert.deepEqual(calls.find((call) => call.path.endsWith('/correction')).body, { label: 'normal', revision: 2 }); app.dispose();
});

test('a running classification has an honest state and a quiet cache poll refreshes results without discarding a draft', async () => {
    let running=true;
    const {app,root,timers}=setup({route:call=>call.path==='/api/mail/classification' ? [200,{...CLASS_DATA,
        state:running?'running':'waiting',results:[{id:'a'.repeat(32),revision:1,subject:'test',state:running?'running':'completed',
            result:running?null:{label:'normal',reason:'Publicidad',summary:'Oferta inventada'}}]}] : null});
    await app.refresh({opening:true}); app.setVisibility({visible:true}); await settle();
    root.key('classification-toggle').fire('click'); await settle();
    assert.match(root.text(),/Clasificando/);
    const field=root.key('classification:criteria'); field.value='Mi criterio sin guardar'; field.fire('input');
    running=false; await timers.advance(30000);
    assert.equal(root.key('classification:criteria').value,'Mi criterio sin guardar');
    assert.match(root.text(),/normal · sugerencia/); app.dispose();
});

test('mail notice navigation focuses the cache page and selects only its explicit message without reading a body', async () => {
    const {app,root,calls}=setup(); await app.refresh({opening:true});
    app.navigate({account_id:ACCOUNT.id,message_id:MESSAGE.id}); await settle(); await settle();
    assert.equal(calls.find(call=>call.params.get('focus_id')).params.get('focus_id'),MESSAGE.id);
    assert.equal(root.find('detail-title').textContent,MESSAGE.subject);
    assert.equal(calls.filter(call=>call.method==='POST').length,0); app.dispose();
});
