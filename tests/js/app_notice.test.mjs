/** App notices in Face: what an app may ask for, the shell's client, the card and the Bandeja. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { AppNotice, appNoticeBody, appNoticeModes, appNoticesFor } from '../../face/js/app_notice.js';
import { withAppCaller } from '../../face/js/os_app_client.js';
import { normalizeInbox, sourceProblems, ORIGIN_LABELS } from '../../face/js/inbox_state.js';

class Node {
    constructor(tag) {
        this.tagName = tag.toUpperCase(); this.children = []; this.listeners = {}; this.attributes = {};
        this.className = ''; this.textContent = ''; this.dataset = {};
    }
    append(...nodes) { this.children.push(...nodes); }
    replaceChildren(...nodes) { this.children = [...nodes]; }
    setAttribute(key, value) { this.attributes[key] = value; }
    addEventListener(type, callback) { (this.listeners[type] ||= []).push(callback); }
    click() { for (const callback of this.listeners.click || []) callback({}); }
    find(predicate) { return this.children.find(predicate) || this.children.map((node) => node.find(predicate)).find(Boolean); }
    texts() { return [this.textContent, ...this.children.flatMap((node) => node.texts())].filter(Boolean); }
}

const ID = 'a'.repeat(32), TOKEN = 'b'.repeat(32), INSTALLATION = `ins_${'e'.repeat(32)}`;
const reply = (data, status = 200) => ({ ok: status < 400, status, json: async () => data });

test('what an app asks for is checked in the page; Core counts seconds', () => {
    assert.deepEqual(appNoticeBody('enfoque', { title: 'Fin', at: 1_800_000_000_000 }),
        { app: 'enfoque', title: 'Fin', text: '', at: 1_800_000_000, key: '' });
    assert.equal(appNoticeBody('enfoque', { title: 'Fin', at: new Date(2_000_000) }).at, 2000);
    assert.equal(appNoticeBody('enfoque', { title: 'Ya' }).at, 0, 'no time means now');
    for (const bad of [{}, { title: '' }, { title: 'x'.repeat(121) }, { title: 'x', text: 'y'.repeat(1001) },
        { title: 'x', at: -5 }, { title: 'x', at: '10' }, { title: 'x', key: '../a' }]) {
        assert.throws(() => appNoticeBody('enfoque', bad), TypeError, JSON.stringify(bad));
    }
    assert.throws(() => appNoticeBody('Mal', { title: 'x' }), TypeError);
});

test("the shell binds the client to the app that owns the context, never the notice's word", async () => {
    const requests = [];
    const client = appNoticesFor('enfoque', INSTALLATION, async (url, options = {}) => {
        requests.push([url, options.method || 'GET', options.body ? JSON.parse(options.body) : null]);
        if (url === '/api/app-notices') return reply({ status: 'scheduled', mode: 'inbox', notice: { id: ID, due_at: 1_800_000_000 } });
        if (url.startsWith('/api/app-notices/scheduled/')) return reply({ notices: [{ id: ID, key: 'p', title: 'T', due_at: 1_800_000_000 }] });
        return reply({ cancelled: true });
    });
    assert.deepEqual(await client.notify({ title: 'Fin', key: 'p', at: 1_800_000_000_000 }),
        { status: 'scheduled', id: ID, at: 1_800_000_000_000, shown: false });
    assert.deepEqual(requests[0], ['/api/app-notices', 'POST', { app: 'enfoque', title: 'Fin', text: '', at: 1_800_000_000, key: 'p',
        installation: INSTALLATION }], 'the installation of this view: a leftover view of another version schedules nothing');
    assert.equal(await client.cancelNotice('p'), true);
    assert.deepEqual(requests[1].slice(0, 2), ['/api/app-notices/enfoque/p', 'DELETE']);
    assert.deepEqual(await client.scheduledNotices(), [{ id: ID, key: 'p', title: 'T', at: 1_800_000_000_000 }]);
    await assert.rejects(() => client.cancelNotice('../x'), TypeError);
    const refused = appNoticesFor('enfoque', INSTALLATION, async () => reply({ detail: { code: 'off', message: 'Desactivaste los avisos de esta app.' } }, 409));
    await assert.rejects(() => refused.notify({ title: 'Fin' }), (error) => error.code === 'off' && /Desactivaste/.test(error.message));
    await assert.rejects(() => appNoticesFor('enfoque', undefined, async () => reply({})).notify({ title: 'Fin' }), TypeError);
});

test('every installed app gets notices; only an app with Python also gets call', async () => {
    let context;
    const factory = { createApp: (value) => { context = value; return {}; } };
    const entry = `/api/app-packages/files/ins_${'c'.repeat(32)}/main.js`;
    withAppCaller(factory, 'createApp', 'enfoque', entry, { python: false }).createApp({ signal: null });
    assert.deepEqual(['notify', 'cancelNotice', 'scheduledNotices'].map((key) => typeof context[key]), ['function', 'function', 'function']);
    assert.equal(context.call, undefined);
    withAppCaller(factory, 'createApp', 'pendientes', entry).createApp({ signal: null });
    assert.equal(typeof context.call, 'function');
    assert.ok(Object.isFrozen(context));
});

test("the person's choice per app goes through the shell's client", async () => {
    const requests = [];
    const modes = appNoticeModes(async (url, options = {}) => { requests.push([url, options.method || 'GET', options.body]);
        return reply(url.endsWith('/modes') ? { modes: { enfoque: 'inbox' } } : { mode: 'off' }); });
    assert.deepEqual(await modes.load(), { enfoque: 'inbox' });
    assert.equal(await modes.set('enfoque', 'off'), 'off');
    assert.deepEqual(requests[1], ['/api/app-notices/modes/enfoque', 'PUT', JSON.stringify({ mode: 'off' })]);
});

function card({ fetchImpl } = {}) {
    const host = new Node('div'), receipts = [], opened = [], timers = [];
    globalThis.document = { body: new Node('body'), visibilityState: 'visible', createElement: (tag) => new Node(tag) };
    const component = new AppNotice({ host, send: (receipt) => receipts.push(receipt),
        onOpenApp: (id) => opened.push(id), setTimer: (fn, ms) => { timers.push(ms); return 1; }, clearTimer: () => {},
        fetchImpl: fetchImpl || (async () => reply({ id: ID, app_id: 'enfoque', app_name: 'Enfoque', title: 'Pomodoro terminado',
            text: 'Toca descansar.', due_at: 1 })) });
    return { component, host, receipts, opened, timers };
}

const intent = { notice_id: ID, token: TOKEN, surface_id: 'desk' };
const where = { surfaceId: 'desk', documentLike: { visibilityState: 'visible' } };

test('the card reads its text from the route, names the app Core named and signs it was drawn', async () => {
    const { component, host, receipts, opened, timers } = card();
    assert.equal(await component.present({ ...intent, title: 'texto del websocket' }, where), 'shown');
    assert.deepEqual(receipts, [{ ...intent, shown: true }]);
    const texts = host.texts();
    assert.ok(texts.includes('ENFOQUE') && texts.includes('Pomodoro terminado') && texts.includes('Toca descansar.'));
    assert.ok(!texts.includes('texto del websocket'));
    assert.equal(host.children[0].dataset.app, 'enfoque');
    assert.equal(timers[0], 30000);
    host.find((node) => node.textContent === 'Abrir Enfoque').click();
    assert.deepEqual(opened, ['enfoque']);
    assert.equal(host.children.length, 0, 'opening closes the card');
    assert.equal(await component.present(intent, where), 'ignored', 'one intent, one card');
});

test('a deferred card comes back in the same window; a drawn or in-progress one is not taken twice (Codex SDK01)', async () => {
    const { component, receipts, host } = card();
    let busy = true;
    const options = { ...where, busy: () => busy };
    assert.equal(await component.present(intent, options), 'deferred');
    busy = false;
    const again = { ...intent, token: 'c'.repeat(32) };
    assert.equal(await component.present(again, options), 'shown', 'Core offers it again with a new token');
    assert.deepEqual(receipts, [{ ...intent, shown: false }, { ...again, shown: true }]);
    assert.ok(host.texts().includes('Pomodoro terminado'));
    assert.equal(await component.present({ ...intent, token: 'd'.repeat(32) }, options), 'ignored', 'drawn once');
    let finish;
    const slow = card({ fetchImpl: () => new Promise((resolve) => { finish = resolve; }) });
    const first = slow.component.present(intent, where);
    assert.equal(await slow.component.present({ ...intent, token: 'c'.repeat(32) }, where), 'ignored', 'one in progress');
    finish(reply({ id: ID, app_id: 'enfoque', app_name: 'Enfoque', title: 'T', text: '', due_at: 1 }));
    assert.equal(await first, 'shown');
});

test('another card on screen, a hidden page or a foreign surface defer or ignore it', async () => {
    const { component, receipts } = card();
    assert.equal(await component.present(intent, { ...where, busy: () => true }), 'deferred');
    assert.deepEqual(receipts, [{ ...intent, shown: false }]);
    const second = card();
    assert.equal(await second.component.present(intent, { ...where, surfaceId: 'other' }), 'ignored');
    assert.equal(await second.component.present({ ...intent, notice_id: 'b'.repeat(32) }, { ...where, documentLike: { visibilityState: 'hidden' } }), 'deferred');
    const broken = card({ fetchImpl: async () => reply({ id: 'c'.repeat(32), app_id: 'enfoque' }) });
    assert.equal(await broken.component.present(intent, where), 'invalid');
    assert.deepEqual(broken.receipts, [{ ...intent, shown: false }]);
});

test('the Bandeja shows app notices by app name, with their actions, and background tasks keep theirs', () => {
    const base = { schema: 'iris.inbox-item/v1', profile: 'default', source_revision: 0, title: 'Pomodoro terminado', text: '',
        delivery: { status: 'pending', revision: 0 }, created_at: 1, updated_at: 1 };
    const state = normalizeInbox({ items: [
        { ...base, key: `app:default:${ID}`, origin: 'app', source_id: ID, outcome: 'notice', app_id: 'enfoque',
          app_name: 'Enfoque', actions: ['read', 'dismiss'] },
        { ...base, key: `app:default:${'d'.repeat(32)}`, origin: 'app', source_id: 'd'.repeat(32), outcome: 'notice' },
        { ...base, key: `background:default:${'e'.repeat(32)}`, origin: 'background', source_id: 'e'.repeat(32),
          outcome: 'completed', actions: ['read', 'dismiss'] },
    ], sources: { app: { status: 'unavailable', message: '' }, background: { status: 'ok' } } });
    assert.equal(state.items.length, 2, 'an app notice without its app is left out');
    const app = state.items.find((item) => item.origin === 'app');
    assert.deepEqual([app.appId, app.appName, app.actions], ['enfoque', 'Enfoque', ['read', 'dismiss']]);
    assert.deepEqual(state.items.find((item) => item.origin === 'background').actions, ['read', 'dismiss']);
    assert.equal(ORIGIN_LABELS.app, 'APP');
    assert.ok(sourceProblems(state).some((line) => /avisos de las apps/.test(line)));
});
