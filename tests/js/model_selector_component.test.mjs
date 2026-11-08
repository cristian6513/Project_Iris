/**
 * F01: the real ModelSelector component over a minimal DOM.
 *
 * Covers what the pure state tests cannot: keyboard focus across re-renders
 * and requests, one write at a time, and writes that must not follow the user
 * into another conversation (Codex review 2026-10-08, R2 and R7).
 */
import test from 'node:test';
import assert from 'node:assert/strict';

// ── minimal DOM ──────────────────────────────────────────────────────────
class FakeNode {
    constructor(tag) {
        this.tagName = tag.toUpperCase();
        this.children = [];
        this.parentElement = null;
        this.listeners = {};
        this.attributes = {};
        this.dataset = {};
        this.style = {};
        this.hidden = false;
        this.disabled = false;
        this.checked = false;
        this.className = '';
        this.textContent = '';
        this.value = '';
        this.name = '';
        this.type = '';
        this.tabIndex = 0;
    }
    get isConnected() {
        let node = this;
        while (node.parentElement) node = node.parentElement;
        return node === globalThis.document?.documentElement;
    }
    append(...nodes) { for (const node of nodes) { node.parentElement?.removeChild?.(node); node.parentElement = this; this.children.push(node); } }
    prepend(node) { node.parentElement = this; this.children.unshift(node); }
    removeChild(node) { this.children = this.children.filter((child) => child !== node); node.parentElement = null; }
    replaceChildren(...nodes) {
        const doc = globalThis.document;
        for (const child of this.children) {
            if (child.contains(doc.activeElement)) doc.activeElement = doc.body;
            child.parentElement = null;
        }
        this.children = [];
        this.append(...nodes);
    }
    contains(node) { return node === this || this.children.some((child) => child.contains(node)); }
    setAttribute(name, value) { this.attributes[name] = String(value); }
    getAttribute(name) { return this.attributes[name]; }
    addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
    dispatch(type, init = {}) {
        const event = { type, target: this, key: init.key, stopPropagation() { this.stopped = true; } };
        let node = this;
        while (node && !event.stopped) {
            for (const fn of node.listeners[type] || []) fn(event);
            node = node.parentElement;
        }
    }
    click() { if (!this.disabled) this.dispatch('click'); }
    focus() {
        if (this.disabled || !this.isConnected) return;
        let node = this;
        while (node) { if (node.hidden) return; node = node.parentElement; }
        globalThis.document.activeElement = this;
    }
    closest() { return null; }
    getBoundingClientRect() { return { left: 20, top: 600, bottom: 628, width: 340 }; }
    matches(selector) {
        if (selector.startsWith('.')) return this.className.split(/\s+/).includes(selector.slice(1));
        return this.tagName === selector.toUpperCase();
    }
    querySelectorAll(selector) {
        const out = [];
        for (const child of this.children) {
            if (child.matches(selector)) out.push(child);
            out.push(...child.querySelectorAll(selector));
        }
        return out;
    }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}

function installDom() {
    const documentElement = new FakeNode('html');
    const body = new FakeNode('body');
    documentElement.append(body);
    const document = {
        documentElement, body, activeElement: body, listeners: {},
        createElement: (tag) => new FakeNode(tag),
        addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); },
    };
    globalThis.document = document;
    globalThis.window = { innerWidth: 1280, innerHeight: 820, addEventListener() {} };
    const host = new FakeNode('div');
    body.append(host);
    return { document, host };
}

const { ModelSelector } = await import('../../face/js/model_selector.js');

// ── helpers ──────────────────────────────────────────────────────────────
function payload({ session = 'IRIS-iris', context = 'default:0', mode = 'custom', provider = 'fake-b', model = 'beta-1' } = {}) {
    return {
        supported: true, busy: false, default_writable: true, default_reason: '',
        conversation: { session_id: session, profile: 'default', context, mode, provider, model, state: 'pending', last: null },
        default: { provider: 'fake-a', model: 'alpha-1' },
        inventory: { error: '', providers: [
            { id: 'fake-a', label: 'Fake A', current: true, authenticated: true, models: ['alpha-1', 'alpha-2'] },
            { id: 'fake-b', label: 'Fake B', current: false, authenticated: true, models: ['beta-1'] },
        ] },
    };
}

function deferred() {
    let resolve;
    const promise = new Promise((done) => { resolve = done; });
    return { promise, resolve };
}

const reply = (body, ok = true) => ({ ok, json: async () => body });

async function settle() {
    for (let i = 0; i < 20; i += 1) await Promise.resolve();
}

/** A fetch whose responses the test releases one by one. */
function controlledFetch() {
    const calls = [];
    const fetchImpl = (url, init = {}) => {
        const call = { url, method: init.method || 'GET', body: init.body ? JSON.parse(init.body) : null, ...deferred() };
        calls.push(call);
        return call.promise;
    };
    return { calls, fetchImpl };
}

function radio(selector, value) {
    return selector.options.querySelectorAll('input').find((input) => input.value === value);
}

const SEP = '\u0000';

// ── tests ────────────────────────────────────────────────────────────────
test('opening moves focus into the dialog at once and onto the chosen option when loaded', async () => {
    const { document, host } = installDom();
    const net = controlledFetch();
    const selector = new ModelSelector({ host, fetchImpl: net.fetchImpl });
    selector.pill.focus();
    selector.open();
    assert.equal(document.activeElement, selector.dialog, 'focus is inside the dialog while loading');
    net.calls[0].resolve(reply(payload()));
    await settle();
    assert.equal(document.activeElement, radio(selector, `fake-b${SEP}beta-1`));
});

test('choosing keeps focus inside and returns it to the same option after the write', async () => {
    const { document, host } = installDom();
    const net = controlledFetch();
    const selector = new ModelSelector({ host, fetchImpl: net.fetchImpl });
    selector.open();
    net.calls[0].resolve(reply(payload()));
    await settle();
    const alpha2 = radio(selector, `fake-a${SEP}alpha-2`);
    alpha2.focus();
    alpha2.checked = true;
    alpha2.dispatch('change');
    await settle();
    assert.equal(net.calls[1].method, 'PUT');
    assert.deepEqual(net.calls[1].body, { mode: 'custom', provider: 'fake-a', model: 'alpha-2', session_id: 'IRIS-iris', context: 'default:0' });
    assert.equal(document.activeElement, selector.dialog, 'disabled while writing: focus held on the dialog');
    net.calls[1].resolve(reply(payload({ provider: 'fake-a', model: 'alpha-2' })));
    await settle();
    assert.equal(document.activeElement, radio(selector, `fake-a${SEP}alpha-2`));
});

test('a refresh during the confirmation keeps focus on Cancelar; Cancelar returns to the action', async () => {
    const { document, host } = installDom();
    const net = controlledFetch();
    const selector = new ModelSelector({ host, fetchImpl: net.fetchImpl });
    selector.open();
    net.calls[0].resolve(reply(payload()));
    await settle();
    selector.defaultBox.querySelector('.model-default-btn').click();
    const cancel = selector.defaultBox.querySelector('.model-default-cancel');
    assert.equal(document.activeElement, cancel, 'the safe choice has focus');
    selector.handleEvent('model_selection_changed', {});
    await settle();
    assert.equal(document.activeElement, selector.dialog);
    net.calls[1].resolve(reply(payload()));
    await settle();
    const cancelAgain = selector.defaultBox.querySelector('.model-default-cancel');
    assert.ok(cancelAgain, 'the confirmation survives a refresh');
    assert.equal(document.activeElement, cancelAgain);
    cancelAgain.click();
    assert.equal(document.activeElement, selector.defaultBox.querySelector('.model-default-btn'));
});

test('confirming twice sends one write, and the button is disabled while it runs', async () => {
    const { host } = installDom();
    const net = controlledFetch();
    const selector = new ModelSelector({ host, fetchImpl: net.fetchImpl });
    selector.open();
    net.calls[0].resolve(reply(payload()));
    await settle();
    selector.defaultBox.querySelector('.model-default-btn').click();
    const confirm = selector.defaultBox.querySelector('.model-default-confirm');
    confirm.click();
    confirm.click();
    await settle();
    assert.equal(net.calls.filter((call) => call.method === 'PUT').length, 1);
    assert.equal(selector.defaultBox.querySelector('.model-default-confirm')?.disabled ?? true, true);
});

test('a default change that finishes after a conversation switch does not touch the new conversation', async () => {
    const { host } = installDom();
    const net = controlledFetch();
    const selector = new ModelSelector({ host, fetchImpl: net.fetchImpl });
    selector.open();
    net.calls[0].resolve(reply(payload()));
    await settle();
    selector.defaultBox.querySelector('.model-default-btn').click();
    selector.defaultBox.querySelector('.model-default-confirm').click();
    await settle();
    const put = net.calls[1];
    assert.deepEqual(put.body, { provider: 'fake-b', model: 'beta-1', context: 'default:0' });
    // Wake switched conversation while Hermes wrote the default.
    put.resolve(reply(payload({ session: 'IRIS-voice-2', context: 'default:1', provider: 'fake-c', model: 'gamma-1' })));
    await settle();
    assert.equal(net.calls.filter((call) => call.method === 'PUT').length, 1, 'no write chained to the new conversation');
});

test('after a default change in the same conversation, only that conversation follows the default', async () => {
    const { host } = installDom();
    const net = controlledFetch();
    const selector = new ModelSelector({ host, fetchImpl: net.fetchImpl });
    selector.open();
    net.calls[0].resolve(reply(payload()));
    await settle();
    selector.defaultBox.querySelector('.model-default-btn').click();
    selector.defaultBox.querySelector('.model-default-confirm').click();
    await settle();
    net.calls[1].resolve(reply({ ...payload(), default: { provider: 'fake-b', model: 'beta-1' } }));
    await settle();
    const chained = net.calls[2];
    assert.equal(chained.url, '/api/models/conversation');
    assert.deepEqual(chained.body, { mode: 'default', session_id: 'IRIS-iris', context: 'default:0' });
});

test('a remote Gateway shows why its default cannot be changed here', async () => {
    const { host } = installDom();
    const net = controlledFetch();
    const selector = new ModelSelector({ host, fetchImpl: net.fetchImpl });
    selector.open();
    net.calls[0].resolve(reply({ ...payload(), default_writable: false,
        default_reason: 'El Gateway de Hermes no es el de este equipo: su modelo predeterminado se cambia en ese Hermes.' }));
    await settle();
    assert.equal(selector.defaultBox.querySelector('.model-default-btn'), null);
    assert.ok(selector.defaultBox.querySelectorAll('p').some((p) => /no es el de este equipo/.test(p.textContent)));
});

test('Escape closes and returns focus to the pill', async () => {
    const { document, host } = installDom();
    const net = controlledFetch();
    const selector = new ModelSelector({ host, fetchImpl: net.fetchImpl });
    selector.open();
    net.calls[0].resolve(reply(payload()));
    await settle();
    document.activeElement.dispatch('keydown', { key: 'Escape' });
    assert.equal(selector.isOpen, false);
    assert.equal(document.activeElement, selector.pill);
});

// ── Codex recheck 2026-10-08 (C3): reads and writes that cross a conversation switch ──
const B = { session: 'IRIS-voice-2', context: 'default:1', mode: 'default', provider: 'fake-a', model: 'alpha-1' };
const STALE = 'La conversación activa cambió mientras se guardaba; vuelve a elegir el modelo.';

const pendingReads = (net) => net.calls.filter((call) => call.method === 'GET' && !call.done);
const answer = (call, response) => { call.done = true; call.resolve(response); };
const puts = (net) => net.calls.filter((call) => call.method === 'PUT');

function assertUsableOn(selector, document, context) {
    assert.equal(selector.state.conversation.context, context);
    assert.equal(selector.busy, false);
    assert.equal(selector.refreshBtn.disabled, false);
    const inputs = selector.options.querySelectorAll('input');
    assert.ok(inputs.length > 0 && inputs.every((input) => !input.disabled), 'every option can be chosen');
    assert.ok(selector.dialog.contains(document.activeElement), 'keyboard focus stays in the dialog');
}

async function confirmDefault(net, selector) {
    net.calls[0].done = true;
    net.calls[0].resolve(reply(payload()));
    await settle();
    selector.defaultBox.querySelector('.model-default-btn').click();
    selector.defaultBox.querySelector('.model-default-confirm').click();
    await settle();
    return net.calls[1];
}

for (const readsFirst of [true, false]) {
    test(`a default change answered for A after Wake moved to B leaves the selector usable on B (reads ${readsFirst ? 'before' : 'after'} the reply)`, async () => {
        const { document, host } = installDom();
        const net = controlledFetch();
        const selector = new ModelSelector({ host, fetchImpl: net.fetchImpl });
        selector.open();
        const put = await confirmDefault(net, selector);
        selector.handleEvent('model_selection_changed', { default: true }); // Core's event: read #1
        selector.handleEvent('chat_history_replace', {}); // Wake: read #2
        await settle();
        const reads = pendingReads(net);
        assert.equal(reads.length, 2);
        const putReply = () => answer(put, reply({ ...payload(), default: { provider: 'fake-b', model: 'beta-1' } }));
        if (!readsFirst) putReply();
        await settle();
        for (const read of reads) answer(read, reply(payload(B)));
        await settle();
        if (readsFirst) putReply();
        await settle();
        for (const read of pendingReads(net)) answer(read, reply(payload(B)));
        await settle();
        assert.equal(puts(net).length, 1, 'nothing chained: the conversation shown is no longer A');
        assertUsableOn(selector, document, 'default:1');
    });
}

for (const wakeReadFirst of [true, false]) {
    test(`a chained write refused with 409 recovers on the active conversation (Wake read ${wakeReadFirst ? 'before' : 'after'} the 409)`, async () => {
        const { document, host } = installDom();
        const net = controlledFetch();
        const selector = new ModelSelector({ host, fetchImpl: net.fetchImpl });
        selector.open();
        const put = await confirmDefault(net, selector);
        answer(put, reply({ ...payload(), default: { provider: 'fake-b', model: 'beta-1' } }));
        await settle();
        const chained = puts(net)[1];
        assert.deepEqual(chained.body, { mode: 'default', session_id: 'IRIS-iris', context: 'default:0' });
        selector.handleEvent('chat_history_replace', {}); // Wake reached Core before the chained write
        await settle();
        const [wakeRead] = pendingReads(net);
        if (wakeReadFirst) {
            answer(wakeRead, reply(payload(B)));
            await settle();
        }
        answer(chained, reply({ detail: STALE }, false));
        await settle();
        for (const read of pendingReads(net)) answer(read, reply(payload(B)));
        await settle();
        assert.equal(puts(net).length, 2, 'B was never written');
        assertUsableOn(selector, document, 'default:1');
        assert.match(selector.status.textContent, /vuelve a elegir/);
    });
}

test('the usual order (Core event read still running when the reply arrives) still chains for A', async () => {
    const { host } = installDom();
    const net = controlledFetch();
    const selector = new ModelSelector({ host, fetchImpl: net.fetchImpl });
    selector.open();
    const put = await confirmDefault(net, selector);
    selector.handleEvent('model_selection_changed', { default: true });
    await settle();
    answer(put, reply({ ...payload(), default: { provider: 'fake-b', model: 'beta-1' } }));
    await settle();
    assert.equal(puts(net).length, 1, 'waits for the read that is still running');
    for (const read of pendingReads(net)) answer(read, reply({ ...payload(), default: { provider: 'fake-b', model: 'beta-1' } }));
    await settle();
    assert.equal(puts(net).length, 2);
    assert.deepEqual(puts(net)[1].body, { mode: 'default', session_id: 'IRIS-iris', context: 'default:0' });
});

test('once every request has settled the selector is never left busy, even if a write superseded a read', async () => {
    const { document, host } = installDom();
    const net = controlledFetch();
    const selector = new ModelSelector({ host, fetchImpl: net.fetchImpl });
    selector.open();
    answer(net.calls[0], reply(payload()));
    await settle();
    selector.handleEvent('model_turn', {}); // a read is running...
    await settle();
    const [read] = pendingReads(net);
    selector._choose(`fake-a${SEP}alpha-2`); // ...when a write starts (no UI path does this today)
    await settle();
    answer(puts(net)[0], reply(payload({ provider: 'fake-a', model: 'alpha-2' })));
    await settle();
    answer(read, reply(payload()));
    await settle();
    assert.equal(selector.state.conversation.model, 'alpha-2', 'the older read does not replace the write');
    assertUsableOn(selector, document, 'default:0');
});
