// Tests of the service template's interface, against a fake service. They take the app's id from
// app.json and find elements by data-part, so renaming the app does not break them.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const ID = JSON.parse(readFileSync(new URL('../app.json', import.meta.url), 'utf8')).id;

class Node {
    constructor(tag) { Object.assign(this, { tag, children: [], listeners: {}, attributes: {}, dataset: {}, textContent: '', className: '' }); }
    append(...nodes) { this.children.push(...nodes); }
    replaceChildren(...nodes) { this.children = nodes; }
    setAttribute(name, value) { this.attributes[name] = String(value); }
    addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
    all() { return [this, ...this.children.flatMap((child) => child.all())]; }
    find(part) { return this.all().find((node) => node.dataset.part === part); }
}
globalThis.document = { createElement: (tag) => new Node(tag) };

const { createApp } = await import('../ui/main.js');

function deferred() {
    let resolve;
    const promise = new Promise((yes) => { resolve = yes; });
    return { promise, resolve };
}
const answer = (status, body) => ({ ok: status < 400, status, json: async () => body });

test('it reads its own service and says where the data comes from', async () => {
    const urls = [];
    const app = createApp({ id: ID }, {
        fetchImpl: async (url) => { urls.push(url); return answer(200, { items: [{ id: 'a', title: 'A', detail: 'x' }], source: 'ejemplo' }); },
    });
    const root = new Node('div');
    app.mount(root);
    await app.refresh({ opening: true });
    assert.deepEqual(urls, [`/api/apps/${ID}/items`]);
    assert.equal(root.find('status').textContent, '1 · datos de ejemplo');
    assert.equal(root.find('list').children.length, 1);
    await app.refresh({ opening: true });
    assert.equal(urls.length, 1, 'already loaded: opening again does not read');
});

test('an action from Iris marks the element it names, and only one the app listed', async () => {
    const items = [{ id: 'a', title: 'A', detail: '' }, { id: 'b', title: 'B', detail: '' }];
    const app = createApp({ id: ID }, { fetchImpl: async () => answer(200, { items, source: 'ejemplo' }) });
    const root = new Node('div');
    app.mount(root);
    const shown = await app.handleIntent({ action: 'show_item', args: { item: 'b' } }); // loads first
    assert.deepEqual(shown, { status: 'ok', message: 'Elemento mostrado.', data: { item: 'b' } });
    assert.deepEqual(root.find('list').children.map((row) => row.attributes['aria-current'] ?? null), [null, 'true']);
    assert.equal((await app.handleIntent({ action: 'show_item', args: { item: 'zz' } })).status, 'error');
    assert.equal((await app.handleIntent({ action: 'delete_all', args: {} })).status, 'error');
});

test('a service that Core has not mounted is said plainly', async () => {
    const app = createApp({ id: ID }, { fetchImpl: async () => answer(404, { detail: 'Not Found' }) });
    const root = new Node('div');
    app.mount(root);
    await app.refresh({ opening: true });
    assert.equal(root.find('status').textContent, 'El servicio de esta app no está activo en Iris. Pulsa «Actualizar».');
});

test('a late answer is dropped, an error is said, and retiring cancels the read', async () => {
    const pending = [];
    const signals = [];
    const app = createApp({ id: ID }, {
        fetchImpl: (url, init) => { signals.push(init.signal); const next = deferred(); pending.push(next); return next.promise; },
    });
    const root = new Node('div');
    app.mount(root);
    const first = app.refresh({ force: true });
    const second = app.refresh({ force: true });
    pending[1].resolve(answer(409, { detail: 'El archivo items.json del perfil no se pudo leer.' }));
    await second;
    pending[0].resolve(answer(200, { items: [{ id: 'old', title: 'Viejo' }], source: 'perfil' }));
    await first;
    assert.match(root.find('status').textContent, /no se pudo leer\. Pulsa «Actualizar»/);
    assert.equal(signals[0].aborted, true, 'the older read was cancelled');
    void app.refresh({ force: true });
    app.dispose();
    assert.equal(signals[2].aborted, true);
    assert.equal(root.children.length, 0);
});
