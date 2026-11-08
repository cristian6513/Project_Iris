/** Calendar's visual component over a fake guarded route: a receipt means rendered, never read. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { CalendarNotice } from '../../face/js/calendar_notice.js';

class Node {
    constructor(tag) {
        this.tagName = tag.toUpperCase(); this.children = []; this.listeners = {}; this.attributes = {};
        this.className = ''; this.textContent = ''; this.focused = false;
    }
    append(...nodes) { this.children.push(...nodes); }
    replaceChildren(...nodes) { this.children = [...nodes]; }
    setAttribute(key, value) { this.attributes[key] = value; }
    addEventListener(type, callback) { (this.listeners[type] ||= []).push(callback); }
    dispatch(type, extra = {}) { for (const callback of this.listeners[type] || []) callback({ ...extra }); }
    click() { this.dispatch('click'); }
    focus() { this.focused = true; }
    find(predicate) { return this.children.find(predicate) || this.children.map((node) => node.find(predicate)).find(Boolean); }
    texts() { return [this.textContent, ...this.children.flatMap((node) => node.texts())].filter(Boolean); }
}

const ID = 'a'.repeat(32), NEXT = 'b'.repeat(32), TOKEN = 'c'.repeat(32), EVENT = `event_${'d'.repeat(32)}`;
const NOW = 1_792_116_000;
const intent = (extra = {}) => ({ notice_id: ID, token: TOKEN, surface_id: 'face-test', ...extra });
const value = (extra = {}) => ({ id: ID, event_id: EVENT, date: '2026-10-15', title: 'Reunión sintética',
    text: 'Empieza ahora.', join_url: 'https://meet.google.com/abc-defg-hij', expires_at: NOW + 300, ...extra });
const context = (extra = {}) => ({ surfaceId: 'face-test', documentLike: { visibilityState: 'visible' }, ...extra });
const reply = (data = value(), status = 200) => ({ ok: status < 400, status, json: async () => data });
const button = (host, text) => host.find((node) => node.tagName === 'BUTTON' && node.textContent === text);

function create({ fetchImpl, host = new Node('div'), send, open } = {}) {
    const requests = [], receipts = [], timers = [], clears = [], opened = [];
    const document = { body: new Node('body'), visibilityState: 'visible',
        createElement: (tag) => new Node(tag), activeElement: { tagName: 'INPUT' } };
    globalThis.document = document;
    const component = new CalendarNotice({ host, now: () => NOW,
        send: (receipt) => { receipts.push({ receipt, texts: host?.texts() || [] }); send?.(receipt); return true; },
        onOpenCalendar: (target) => { opened.push(target); open?.(target); },
        fetchImpl: async (...args) => { requests.push(args); return fetchImpl ? fetchImpl(...args) : reply(); },
        setTimer: (callback, ms) => { timers.push({ callback, ms }); return timers.length; },
        clearTimer: (timer) => clears.push(timer),
    });
    return { component, host, document, requests, receipts, timers, clears, opened };
}

test('a shown receipt is sent only after the guarded text is rendered, with no read mutation or focus', async () => {
    const { component, host, requests, receipts, timers, document } = create();
    const active = document.activeElement;
    assert.equal(await component.present(intent({ title: 'untrusted websocket text' }), context()), 'shown');
    assert.deepEqual(requests, [[`/api/calendar/notices/${ID}`, { cache: 'no-store' }]]);
    assert.deepEqual(receipts.map((entry) => entry.receipt), [intent({ shown: true })]);
    assert.ok(receipts[0].texts.includes('Reunión sintética') && receipts[0].texts.includes('Empieza ahora.'));
    assert.ok(!host.texts().includes('untrusted websocket text'));
    assert.equal(document.activeElement, active);
    assert.equal(host.children[0].attributes.role, 'status');
    assert.equal(host.children[0].attributes['aria-live'], 'polite');
    assert.equal(timers[0].ms, 30000);
});

test('hidden, unknown visibility and busy surfaces defer with a false receipt and no fetch', async () => {
    for (const extra of [{ documentLike: { visibilityState: 'hidden' } },
        { documentLike: {} }, { busy: true }]) {
        const { component, requests, receipts, host } = create();
        assert.equal(await component.present(intent(), context(extra)), 'deferred');
        assert.deepEqual(receipts.map((entry) => entry.receipt), [intent({ shown: false })]);
        assert.equal(requests.length, 0); assert.equal(host.children.length, 0);
    }
});

test('typing in a field defers the calendar card, including typing that begins during its fetch', async () => {
    const first = create();
    const doc = { visibilityState: 'visible', activeElement: { tagName: 'TEXTAREA' } };
    assert.equal(await first.component.present(intent(), context({ documentLike: doc })), 'deferred');
    assert.equal(first.requests.length, 0);
    let release;
    const second = create({ fetchImpl: () => new Promise((resolve) => { release = resolve; }) });
    doc.activeElement = null;
    const pending = second.component.present(intent(), context({ documentLike: doc }));
    doc.activeElement = { tagName: 'INPUT', type: 'text' };
    release(reply());
    assert.equal(await pending, 'deferred');
    assert.equal(second.host.children.length, 0);
    assert.equal(second.receipts[0].receipt.shown, false);
});

test('malformed or another surface intent is ignored without fetching or answering', async () => {
    for (const extra of [{ notice_id: 'bad' }, { token: 'bad' }, { surface_id: 'other' },
        { notice_id: 'A'.repeat(32) }]) {
        const { component, requests, receipts } = create();
        assert.equal(await component.present(intent(extra), context()), 'ignored');
        assert.equal(requests.length, 0); assert.equal(receipts.length, 0);
    }
});

test('a duplicate in flight fetches once and only gets the original receipt', async () => {
    let resolve;
    const { component, requests, receipts } = create({ fetchImpl: () => new Promise((done) => { resolve = done; }) });
    const first = component.present(intent(), context());
    assert.equal(await component.present(intent(), context()), 'ignored');
    resolve(reply());
    assert.equal(await first, 'shown');
    assert.equal(requests.length, 1); assert.equal(receipts.length, 1);
    assert.equal(await component.present(intent(), context()), 'ignored');
});

test('a failed request, an unsuccessful response or invalid JSON acknowledges false', async () => {
    for (const fetchImpl of [() => { throw new Error('offline'); }, () => reply(null, 404),
        () => ({ ok: true, json: async () => { throw new Error('bad json'); } })]) {
        const { component, receipts, host } = create({ fetchImpl });
        assert.equal(await component.present(intent(), context()), 'unavailable');
        assert.deepEqual(receipts.map((entry) => entry.receipt), [intent({ shown: false })]);
        assert.equal(host.children.length, 0);
    }
});

test('invalid, expired, mismatched and impossible date source values cannot get a shown receipt', async () => {
    for (const extra of [{ id: NEXT }, { event_id: 'evt_' + 'd'.repeat(32) }, { date: '2026-02-30' },
        { date: '0000-01-01' }, { date: '2026-1-1' }, { expires_at: NOW }, { expires_at: NOW - 1 },
        { expires_at: Infinity }, { expires_at: 'later' }]) {
        const { component, receipts, host } = create({ fetchImpl: () => reply(value(extra)) });
        assert.equal(await component.present(intent(), context()), 'invalid', JSON.stringify(extra));
        assert.equal(receipts[0].receipt.shown, false); assert.equal(host.children.length, 0);
    }
});

test('a surface hidden while the guarded fetch was pending does not display the response', async () => {
    let resolve;
    const { component, receipts, host } = create({ fetchImpl: () => new Promise((done) => { resolve = done; }) });
    const documentLike = { visibilityState: 'visible' };
    const pending = component.present(intent(), context({ documentLike }));
    documentLike.visibilityState = 'hidden'; resolve(reply());
    assert.equal(await pending, 'deferred'); assert.equal(receipts[0].receipt.shown, false);
    assert.equal(host.children.length, 0);
});

test('a live busy getter blocks before fetching and is rechecked if chat starts during fetch', async () => {
    const initial = create();
    assert.equal(await initial.component.present(intent(), context({ busy: () => true })), 'deferred');
    assert.equal(initial.requests.length, 0);
    assert.equal(initial.receipts[0].receipt.shown, false);

    let resolve, busy = false;
    const run = create({ fetchImpl: () => new Promise((done) => { resolve = done; }) });
    const pending = run.component.present(intent(), context({ busy: () => busy }));
    assert.equal(run.requests.length, 1);
    busy = true; resolve(reply());
    assert.equal(await pending, 'deferred');
    assert.equal(run.receipts[0].receipt.shown, false);
    assert.equal(run.host.children.length, 0);
});

test('a current-notice busy getter retains the visible reminder instead of replacing it', async () => {
    const { component, host, requests, receipts } = create();
    const live = context({ busy: () => Boolean(component.current) });
    assert.equal(await component.present(intent(), live), 'shown');
    assert.equal(await component.present(intent({ notice_id: NEXT }), live), 'deferred');
    assert.equal(component.current, ID);
    assert.ok(host.texts().includes('Reunión sintética'));
    assert.equal(requests.length, 1);
    assert.deepEqual(receipts.map((entry) => [entry.receipt.notice_id, entry.receipt.shown]), [[ID, true], [NEXT, false]]);
});

test('a newer request wins and an older answer cannot replace its visible card', async () => {
    const pending = [];
    const { component, receipts, host } = create({ fetchImpl: () => new Promise((done) => pending.push(done)) });
    const first = component.present(intent(), context());
    const second = component.present(intent({ notice_id: NEXT }), context());
    pending[1](reply(value({ id: NEXT, title: 'Nueva' })));
    assert.equal(await second, 'shown');
    pending[0](reply(value({ title: 'Antigua' })));
    assert.equal(await first, 'deferred');
    assert.ok(host.texts().includes('Nueva') && !host.texts().includes('Antigua'));
    assert.deepEqual(receipts.map((entry) => [entry.receipt.notice_id, entry.receipt.shown]), [[NEXT, true], [ID, false]]);
});

test('closing invalidates a pending request; its later answer stays unshown', async () => {
    let resolve;
    const { component, receipts, host } = create({ fetchImpl: () => new Promise((done) => { resolve = done; }) });
    const pending = component.present(intent(), context());
    component.close(); resolve(reply());
    assert.equal(await pending, 'deferred'); assert.equal(receipts[0].receipt.shown, false);
    assert.equal(host.children.length, 0);
});

test('rendering failure is a false receipt and never claims that the notice was shown', async () => {
    const broken = new Node('div');
    broken.replaceChildren = (...children) => { if (children.length) throw new Error('render failure'); };
    const { component, receipts } = create({ host: broken });
    assert.equal(await component.present(intent(), context()), 'unavailable');
    assert.deepEqual(receipts.map((entry) => entry.receipt), [intent({ shown: false })]);
});

test('safe join links use a separate noopener anchor and the notice text never becomes a URL', async () => {
    const { component, host } = create();
    assert.equal(await component.present(intent(), context()), 'shown');
    const anchor = host.find((node) => node.tagName === 'A');
    assert.equal(anchor.href, 'https://meet.google.com/abc-defg-hij');
    assert.equal(anchor.target, '_blank'); assert.equal(anchor.rel, 'noopener noreferrer');
    for (const join_url of ['', null, 'http://meet.google.com/room', 'javascript:alert(1)', 'https://127.0.0.1/',
        'https://8.8.8.8/', 'https://2130706433/', 'https://[::1]/', 'https://host.local/',
        'https://user:pass@meet.google.com/room', 'https://meet.google.com:8443/', 'https://bad_domain.com/']) {
        const run = create({ fetchImpl: () => reply(value({ join_url, text: 'https://meet.google.com/from-text' })) });
        assert.equal(await run.component.present(intent(), context()), 'shown');
        assert.equal(run.host.find((node) => node.tagName === 'A'), undefined, String(join_url));
    }
});

test('opening Calendar uses the validated source destination and only closes the card', async () => {
    const { component, host, requests, receipts, opened } = create();
    await component.present(intent(), context());
    button(host, 'Ver Calendario').click();
    assert.deepEqual(opened, [{ event_id: EVENT, date: '2026-10-15' }]);
    assert.equal(host.children.length, 0); assert.equal(requests.length, 1); assert.equal(receipts.length, 1);
    assert.equal(receipts[0].receipt.shown, true);
    assert.ok(!('read' in receipts[0].receipt));
});

test('close, Escape and expiry timer change no delivery facts or read state', async () => {
    for (const how of ['close', 'escape', 'timer']) {
        const { component, host, requests, receipts, timers } = create({ fetchImpl: () => reply(value({ expires_at: NOW + 10 })) });
        await component.present(intent(), context());
        assert.equal(timers[0].ms, 10000);
        if (how === 'close') button(host, '×').click();
        if (how === 'escape') host.children[0].dispatch('keydown', { key: 'Escape' });
        if (how === 'timer') timers[0].callback();
        assert.equal(host.children.length, 0); assert.equal(requests.length, 1); assert.equal(receipts.length, 1);
    }
});

test('remote title and text stay plain text, with bounded lengths', async () => {
    const title = '<img src=x onerror=alert(1)>' + 'x'.repeat(200);
    const text = '<script>alert(1)</script>' + 'y'.repeat(2100);
    const { component, host } = create({ fetchImpl: () => reply(value({ title, text })) });
    await component.present(intent(), context());
    assert.ok(host.texts().includes(title.slice(0, 120)));
    assert.ok(host.texts().includes(text.slice(0, 2000)));
    assert.equal(host.find((node) => node.tagName === 'IMG' || node.tagName === 'SCRIPT'), undefined);
});

// Review 2026-10-19: the app builds CalendarNotice with its default timers. Stored bare and called
// as this.setTimer(...), a browser's setTimeout/clearTimeout throw «Illegal invocation», so close()
// failed inside _render and every reminder ended as 'unavailable' before showing anything.
test('with its default timers the notice renders where timers demand their own receiver', async () => {
    const realSet = globalThis.setTimeout, realClear = globalThis.clearTimeout;
    const strict = (real) => function browserTimer(...args) {
        if (this !== undefined && this !== globalThis) throw new TypeError('Illegal invocation');
        return real(...args);
    };
    globalThis.setTimeout = strict(realSet); globalThis.clearTimeout = strict(realClear);
    try {
        const host = new Node('div'), receipts = [];
        globalThis.document = { body: new Node('body'), visibilityState: 'visible',
            createElement: (tag) => new Node(tag), activeElement: { tagName: 'INPUT' } };
        const component = new CalendarNotice({ host, now: () => NOW, onOpenCalendar: () => {},
            send: (receipt) => { receipts.push(receipt); return true; }, fetchImpl: async () => reply() });
        assert.equal(await component.present(intent(), context()), 'shown');
        assert.deepEqual(receipts, [intent({ shown: true })]);
        assert.ok(host.texts().includes('Reunión sintética'));
        component.close();  // also what a hidden page does (app.js visibilitychange)
        assert.equal(host.children.length, 0);
    } finally {
        globalThis.setTimeout = realSet; globalThis.clearTimeout = realClear;
    }
});
