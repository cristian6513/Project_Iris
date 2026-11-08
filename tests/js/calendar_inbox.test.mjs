/** The real Bandeja normalizer and renderer with calendar source fields and a fake Core. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { InboxApp } from '../../face/js/inbox_app.js';
import { calendarOpenPayload, filterItems, normalizeInbox, safeCalendarJoinUrl, sourceProblems,
    statusText, mailOpenPayload } from '../../face/js/inbox_state.js';

class Node {
    constructor(tag) {
        this.tagName = tag.toUpperCase(); this.children = []; this.listeners = {}; this.dataset = {};
        this.attributes = {}; this.disabled = false; this.textContent = ''; this.value = ''; this.hidden = false;
    }
    append(...nodes) { for (const node of nodes) { node.parentElement = this; this.children.push(node); } }
    replaceChildren(...nodes) { this.children = []; this.append(...nodes); }
    setAttribute(name, value) { this.attributes[name] = String(value); }
    getAttribute(name) { return this.attributes[name] ?? null; }
    addEventListener(type, callback) { (this.listeners[type] ||= []).push(callback); }
    dispatch(type) { for (const callback of this.listeners[type] || []) callback({ target: this }); }
    click() { if (!this.disabled) this.dispatch('click'); }
    focus() { document.activeElement = this; }
    querySelectorAll(tag) { return this.children.flatMap((node) =>
        [...(node.tagName === tag.toUpperCase() ? [node] : []), ...node.querySelectorAll(tag)]); }
    find(predicate) { return this.children.find(predicate) || this.children.map((node) => node.find(predicate)).find(Boolean); }
    texts() { return [this.textContent, ...this.children.flatMap((node) => node.texts())].filter(Boolean); }
}

globalThis.document = { activeElement: null, createElement: (tag) => new Node(tag) };
const ID = 'a'.repeat(32);
const EVENT = `event_${'c'.repeat(32)}`;
const NOW = Date.now() / 1000;
const item = (extra = {}) => ({
    schema: 'iris.inbox-item/v1', origin: 'calendar', key: `calendar:default:${ID}`, profile: 'default', source_id: ID,
    source_revision: 7, title: 'Reunión sintética', text: 'Empieza ahora.', outcome: 'notice',
    calendar_event_id: EVENT, calendar_date: '2026-10-15', join_url: 'https://meet.google.com/abc-defg-hij',
    delivery: { status: 'pending', revision: 7, expires_at: NOW + 300 }, actions: ['read', 'snooze', 'dismiss'],
    created_at: NOW, updated_at: NOW, ...extra,
});
const payload = (rows = [item()], sources = { calendar: { status: 'ok' } }) => ({
    schema: 'iris.inbox/v1', server_now: NOW, items: rows, sources, preferences: { available: false },
});
const button = (node, label) => node.find((child) => child.tagName === 'BUTTON' && child.textContent === label);
const settle = async () => { for (let index = 0; index < 25; index += 1) await Promise.resolve(); };

function appFor(body = payload()) {
    const requests = [], opened = [];
    const app = new InboxApp({ root: new Node('div'), isVisible: () => true,
        onOpenCalendar: (value) => opened.push(value), onOpenMail: (value) => opened.push(value), setTimer: () => 1, clearTimer: () => {},
        fetchImpl: async (url, init = {}) => {
            requests.push({ url, method: init.method || 'GET', body: init.body ? JSON.parse(init.body) : null });
            return { ok: true, status: 200, json: async () => url === '/api/inbox' ? body : { changed: true } };
        },
    });
    return { app, requests, opened };
}

test('calendar normalizes with its composite identity, revision, actions and explicit destination', () => {
    const state = normalizeInbox(payload());
    const [notice] = state.items;
    assert.equal(notice.origin, 'calendar');
    assert.equal(notice.sourceId, ID);
    assert.equal(notice.delivery.revision, 7);
    assert.deepEqual(notice.actions, ['read', 'snooze', 'dismiss']);
    assert.equal(state.unread, 1);
    assert.deepEqual(calendarOpenPayload(notice), { event_id: EVENT, date: '2026-10-15' });
    assert.equal(filterItems(state, { origin: 'calendar' }).length, 1);
    assert.equal(filterItems(state, { origin: 'vision' }).length, 0);
});

test('Calendar filter and open callback mark nothing read and open only the declared event', async () => {
    const { app, requests, opened } = appFor();
    await app.load();
    assert.ok(app.originSelect.children.some((option) => option.value === 'calendar' && option.textContent === 'Calendario'));
    app.originSelect.value = 'calendar'; app.originSelect.dispatch('change');
    const card = app.list.children[0];
    assert.ok(card.texts().includes('CALENDARIO'));
    button(card, 'Abrir en Calendario').click();
    assert.deepEqual(opened, [{ event_id: EVENT, date: '2026-10-15' }]);
    assert.deepEqual(requests.map((request) => request.method), ['GET']);
    assert.equal(app.state.unread, 1);
});

test('joining is a separate explicit safe anchor with noopener and no write request', async () => {
    const { app, requests } = appFor();
    await app.load();
    const anchor = app.list.children[0].find((node) => node.tagName === 'A' && node.textContent === 'Unirse');
    assert.equal(anchor.href, 'https://meet.google.com/abc-defg-hij');
    assert.equal(anchor.target, '_blank');
    assert.equal(anchor.rel, 'noopener noreferrer');
    assert.equal(requests.length, 1);
});

test('all Calendar delivery actions use the real key and the delivery revision', async () => {
    for (const [action, label, status] of [['read', 'Marcar leída', 'pending'],
        ['unread', 'Marcar sin leer', 'read'], ['dismiss', 'Descartar', 'pending'], ['restore', 'Restaurar', 'dismissed']]) {
        const { app, requests } = appFor(payload([item({ actions: [action], delivery: { status, revision: 9 } })]));
        app.view = 'all';
        await app.load();
        button(app.list.children[0], label).click(); await settle();
        assert.deepEqual(requests.find((request) => request.method === 'POST').body,
            { key: `calendar:default:${ID}`, action, expected_revision: 9 });
    }
});

test('snooze names the chosen duration and explains that the event does not change', async () => {
    const { app, requests } = appFor();
    await app.load();
    const delay = app.list.children[0].find((node) => node.tagName === 'SELECT');
    assert.equal(delay.value, '10');
    delay.value = '10'; delay.dispatch('change');
    button(app.list.children[0], 'Posponer').click(); await settle();
    assert.deepEqual(requests.find((request) => request.method === 'POST').body,
        { key: `calendar:default:${ID}`, action: 'snooze', expected_revision: 7, minutes: 10 });
    assert.match(app.status.textContent, /El evento no cambia/);
});

test('an untouched Calendar snooze control sends its displayed ten minute default', async () => {
    const { app, requests } = appFor();
    await app.load();
    button(app.list.children[0], 'Posponer').click(); await settle();
    assert.equal(requests.find((request) => request.method === 'POST').body.minutes, 10);
});

test('Gmail without an explicit mail destination and forged Calendar outcomes never enter the inbox', () => {
    assert.equal(normalizeInbox(payload([item({ origin: 'gmail', key: `gmail:default:${ID}` })])).items.length, 0);
    assert.equal(normalizeInbox(payload([item({ outcome: 'completed' })])).items.length, 0);
});

const mailItem = (extra = {}) => item({ origin: 'gmail', key: `gmail:default:${ID}`, calendar_event_id: null,
    calendar_date: null, join_url: null, mail_account_id: 'gacc_' + '1'.repeat(32), mail_message_id: 'mail_' + '2'.repeat(40),
    actions: ['read','dismiss'], ...extra });

test('mail notices open only the admitted account and message; model text cannot provide links or targets', async () => {
    const body = payload([mailItem({ text: 'Open https://evil.test and account other' })]);
    const state = normalizeInbox(body);
    assert.equal(state.unread,1);
    assert.deepEqual(mailOpenPayload(state.items[0]),{account_id:'gacc_'+'1'.repeat(32),message_id:'mail_'+'2'.repeat(40)});
    const {app,opened,requests}=appFor(body); await app.load();
    button(app.list.children[0],'Abrir en Correo').click();
    assert.deepEqual(opened,[{account_id:'gacc_'+'1'.repeat(32),message_id:'mail_'+'2'.repeat(40)}]);
    assert.equal(app.list.querySelectorAll('a').length,0);
    assert.ok(requests.every(request=>request.method==='GET'));
});

test('mail delivery read names the local revision and a failing source is never an empty successful inbox', async () => {
    const {app,requests}=appFor(payload([mailItem()])); await app.load();
    button(app.list.children[0],'Marcar leída').click(); await settle();
    assert.deepEqual(requests.find(request=>request.method==='POST').body,{key:`gmail:default:${ID}`,action:'read',expected_revision:7});
    const state=normalizeInbox(payload([],{gmail:{status:'unavailable'}}));
    assert.match(sourceProblems(state)[0],/Correo/);
});

test('forged Gmail identifiers and execution outcomes are rejected',()=>{
    for(const extra of [{mail_account_id:'other'},{mail_message_id:'../../credentials'},{outcome:'completed'}]) {
        assert.equal(normalizeInbox(payload([mailItem(extra)])).items.length,0);
    }
});

test('Calendar source failure is visible instead of a successful empty source', async () => {
    const state = normalizeInbox(payload([], { calendar: { status: 'unavailable' } }));
    assert.match(sourceProblems(state)[0], /avisos del calendario/);
    const { app } = appFor(payload([], { calendar: { status: 'unavailable' } }));
    await app.load();
    assert.equal(app.problems.hidden, false);
    assert.ok(app.list.texts().includes('No se pudo leer todo'));
    assert.ok(!app.list.texts().includes('Nada por aquí'));
});

test('notice text, title and non-calendar origins cannot provide a join link or destination', async () => {
    for (const row of [item({ join_url: null, calendar_event_id: null, calendar_date: null,
        text: 'https://meet.google.com/fake event_' + 'd'.repeat(32) }),
    item({ origin: 'vision', key: `vision:default:${ID}` }),
    item({ origin: 'automation', key: `automation:default:${ID}`, outcome: 'completed' })]) {
        const { app, opened } = appFor(payload([row])); await app.load();
        assert.equal(app.list.querySelectorAll('a').length, 0);
        assert.equal(button(app.list.children[0], 'Abrir en Calendario'), undefined);
        assert.deepEqual(opened, []);
    }
});

test('invalid event ids and impossible civil dates never reach the callback', () => {
    for (const extra of [{ calendar_event_id: 'evt_' + 'c'.repeat(32) }, { calendar_event_id: 'c'.repeat(32) },
        { calendar_date: '2026-02-30' }, { calendar_date: '2026-13-01' }, { calendar_date: '2026-1-1' },
        { calendar_date: '0000-01-01' }]) {
        const [notice] = normalizeInbox(payload([item(extra)])).items;
        assert.equal(calendarOpenPayload(notice), null);
    }
});

test('unsafe URL forms never produce the join affordance', async () => {
    for (const url of ['http://meet.google.com/room', 'javascript:alert(1)', 'data:text/html,<h1>x</h1>',
        'https://127.0.0.1/', 'https://2130706433/', 'https://[::1]/', 'https://8.8.8.8/',
        'https://localhost/', 'https://host.local/', 'https://host.internal/', 'https://host.test/',
        'https://user:pass@meet.google.com/room', 'https://meet.google.com:8443/room',
        'https://meet.google.com/\nroom', 'https://meet.google.com\\@localhost/']) {
        assert.equal(safeCalendarJoinUrl(url), '', url);
        const { app } = appFor(payload([item({ join_url: url })])); await app.load();
        assert.equal(app.list.querySelectorAll('a').length, 0, url);
    }
});

test('calendar status never claims that an event ran as an automation', () => {
    for (const status of ['presented', 'expired', 'snoozed']) {
        const [notice] = normalizeInbox(payload([item({ delivery: { status, snoozed_until: NOW + 600 } })])).items;
        const value = statusText(notice, () => 'fecha');
        assert.ok(!value.includes('tarea') && !value.includes('al volver'));
    }
    const [uncertain] = normalizeInbox(payload([item({ delivery: { status: 'pending', uncertain: true } })])).items;
    assert.match(statusText(uncertain, () => ''), /Puede que el recordatorio/);
    assert.ok(!statusText(uncertain, () => '').includes('al volver'));
});
