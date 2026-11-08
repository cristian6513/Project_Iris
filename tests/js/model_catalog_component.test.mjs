/**
 * "Modelos del selector": the real component over a minimal DOM (Codex qa10 review, R4).
 *
 * A save in progress must not lose an edit made meanwhile, a read that started
 * before a save must not bring the old choice back, and keyboard focus must
 * survive the re-renders of a reload.
 */
import test from 'node:test';
import assert from 'node:assert/strict';

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
        this.open = false;
        this.className = '';
        this.textContent = '';
        this.value = '';
        this.type = '';
        this.tabIndex = 0;
    }
    get isConnected() {
        let node = this;
        while (node.parentElement) node = node.parentElement;
        return node === globalThis.document?.documentElement;
    }
    append(...nodes) { for (const node of nodes) { node.parentElement?.removeChild?.(node); node.parentElement = this; this.children.push(node); } }
    removeChild(node) { this.children = this.children.filter((child) => child !== node); node.parentElement = null; }
    replaceChildren(...nodes) {
        const doc = globalThis.document;
        for (const child of this.children) {
            if (child.contains(doc.activeElement)) doc.activeElement = doc.body; // as a browser does
            child.parentElement = null;
        }
        this.children = [];
        this.append(...nodes);
    }
    contains(node) { return node === this || this.children.some((child) => child.contains(node)); }
    setAttribute(name, value) { this.attributes[name] = String(value); }
    addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
    dispatch(type) {
        const event = { type, target: this };
        let node = this;
        while (node) {
            for (const fn of node.listeners[type] || []) fn(event);
            node = node.parentElement;
        }
    }
    click() {
        if (this.disabled) return;
        if (this.type === 'checkbox') {
            this.checked = !this.checked;
            this.dispatch('change');
            return;
        }
        this.dispatch('click');
    }
    focus() {
        if (this.disabled || !this.isConnected) return;
        globalThis.document.activeElement = this;
    }
    closest(selector) {
        let node = this;
        while (node) {
            if (selector === 'button[data-action]' && node.tagName === 'BUTTON' && node.dataset.action) return node;
            node = node.parentElement;
        }
        return null;
    }
    querySelectorAll(tag) {
        const out = [];
        for (const child of this.children) {
            if (child.tagName === tag.toUpperCase()) out.push(child);
            out.push(...child.querySelectorAll(tag));
        }
        return out;
    }
}

function installDom() {
    const documentElement = new FakeNode('html');
    const body = new FakeNode('body');
    documentElement.append(body);
    globalThis.document = { documentElement, body, activeElement: body, createElement: (tag) => new FakeNode(tag) };
    const root = new FakeNode('div');
    body.append(root);
    return root;
}

const { ModelCatalogApp } = await import('../../face/js/model_catalog.js');

function payload(visible = null) {
    return {
        supported: true,
        conversation: { session_id: 's', context: 'c', mode: 'default', state: 'pending' },
        default: { provider: 'fake-a', model: 'alpha-1' },
        inventory: { error: '', providers: [
            { id: 'fake-a', label: 'Fake A', authenticated: true, models: ['alpha-1', 'alpha-2'] },
            { id: 'fake-b', label: 'Fake B', authenticated: true, models: ['beta-1', 'beta-2'] },
        ] },
        visible,
    };
}

function deferred() {
    let resolve;
    const promise = new Promise((done) => { resolve = done; });
    return { promise, resolve };
}

function controlledFetch() {
    const calls = [];
    const fetchImpl = (url, init = {}) => {
        const call = { url, method: init.method || 'GET', body: init.body ? JSON.parse(init.body) : null, ...deferred() };
        calls.push(call);
        return call.promise;
    };
    return { calls, fetchImpl };
}

const reply = (body) => ({ ok: true, json: async () => body });

async function settle() {
    for (let i = 0; i < 30; i += 1) await Promise.resolve();
}

function checkbox(app, value) {
    return app.groups.querySelectorAll('input').find((input) => input.dataset.value.endsWith(`\u0000${value}`));
}

function providerButton(app, provider, action) {
    return app.groups.querySelectorAll('button').find((node) => node.dataset.provider === provider
        && node.dataset.action === action);
}

async function loaded(fetch, app) {
    const load = app.load(true);
    fetch.calls.at(-1).resolve(reply(payload()));
    await load;
}

test('nothing can change the draft while it is being saved', async () => {
    const fetch = controlledFetch();
    const app = new ModelCatalogApp({ root: installDom(), fetchImpl: fetch.fetchImpl });
    await loaded(fetch, app);
    checkbox(app, 'alpha-2').click(); // hide alpha-2
    assert.equal(app.dirty, true);
    const saving = app.save();
    await settle();
    // Codex's interleaving: "Ocultar todos" of a provider during the pending PUT.
    providerButton(app, 'fake-b', 'provider-none').click();
    checkbox(app, 'beta-1')?.click();
    app.groups.dispatch?.('change');
    assert.ok(providerButton(app, 'fake-b', 'provider-none').disabled);
    assert.ok(checkbox(app, 'beta-1').disabled);
    const sent = fetch.calls.at(-1);
    assert.equal(sent.method, 'PUT');
    sent.resolve(reply({ visible: sent.body.models }));
    await saving;
    assert.equal(app.dirty, false);
    assert.equal(app.groups.querySelectorAll('input').filter((input) => input.checked).length, 3);
    assert.ok(checkbox(app, 'beta-1').checked && checkbox(app, 'beta-2').checked);
});

test('a reload asked for during a save waits and cannot bring the old choice back', async () => {
    const fetch = controlledFetch();
    const app = new ModelCatalogApp({ root: installDom(), fetchImpl: fetch.fetchImpl });
    await loaded(fetch, app);
    checkbox(app, 'alpha-2').click();
    const saving = app.save();
    await settle();
    const put = fetch.calls.at(-1);
    // The shell reloads the app while the PUT is pending.
    const reload = app.load(true);
    assert.equal(fetch.calls.at(-1), put, 'no read starts during a save');
    put.resolve(reply({ visible: put.body.models }));
    await saving;
    await settle();
    const read = fetch.calls.at(-1);
    assert.equal(read.method, 'GET');
    read.resolve(reply(payload(put.body.models)));
    await reload;
    await settle();
    assert.equal(checkbox(app, 'alpha-2').checked, false);
    assert.equal(app.dirty, false);
    assert.match(app.status.textContent, /Guardado/);
});

test('a save cannot start while a read is in flight, and the edit survives that read', async () => {
    const fetch = controlledFetch();
    const app = new ModelCatalogApp({ root: installDom(), fetchImpl: fetch.fetchImpl });
    await loaded(fetch, app);
    checkbox(app, 'alpha-2').click();
    const reload = app.load(true);
    const read = fetch.calls.at(-1);
    assert.ok(app.saveBtn.disabled);
    app.saveBtn.click();
    await app.save();
    assert.equal(fetch.calls.at(-1), read, 'no PUT overlaps the read');
    read.resolve(reply(payload(null)));
    await reload;
    // The unsaved edit is kept over the freshly read "all models" choice.
    assert.equal(checkbox(app, 'alpha-2').checked, false);
    assert.equal(app.dirty, true);
    assert.equal(app.saveBtn.disabled, false);
});

test('keyboard focus stays on the same checkbox across a reload', async () => {
    const fetch = controlledFetch();
    const app = new ModelCatalogApp({ root: installDom(), fetchImpl: fetch.fetchImpl });
    await loaded(fetch, app);
    checkbox(app, 'beta-1').focus();
    assert.equal(document.activeElement, checkbox(app, 'beta-1'));
    const reload = app.load(true);
    fetch.calls.at(-1).resolve(reply(payload()));
    await reload;
    assert.equal(document.activeElement, checkbox(app, 'beta-1'), 'not lost to <body>');
    // The same for a provider's bulk button.
    providerButton(app, 'fake-a', 'provider-none').focus();
    const again = app.load(true);
    fetch.calls.at(-1).resolve(reply(payload()));
    await again;
    assert.equal(document.activeElement, providerButton(app, 'fake-a', 'provider-none'));
});
