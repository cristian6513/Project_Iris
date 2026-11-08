import test from 'node:test';
import assert from 'node:assert/strict';
import { createApp } from '../ui/main.js';

class Node {
    constructor(tag) { Object.assign(this, { tag, children: [], listeners: {}, attributes: {}, dataset: {}, textContent: '',
        className: '', disabled: false, checked: false, value: '' }); }
    append(...nodes) { this.children.push(...nodes); }
    replaceChildren(...nodes) { this.children = nodes; }
    setAttribute(key, value) { this.attributes[key] = String(value); }
    addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
    fire(type) { for (const fn of this.listeners[type] || []) fn({ type, target: this }); }
    focus() { globalThis.document.activeElement = this; }
    all() { return [this, ...this.children.flatMap(node => node.all())]; }
    find(part) { return this.all().find(node => node.dataset.part === part); }
    text() { return this.all().map(node => node.textContent).join(' '); }
}
const TEXT = JSON.stringify({ installed: { client_id: 'test.apps.googleusercontent.com', client_secret: 'synthetic-secret' } });
const settle = () => new Promise(resolve => setTimeout(resolve, 0));

function setup({ config = {}, answer = false, put = null } = {}) {
    globalThis.document = { activeElement: null, createElement: tag => new Node(tag) };
    let client = { configured: false, source: 'none', revision: 0, can_change: true, error: null, ...config };
    const calls = [], confirms = [];
    const app = createApp({ confirm: async opts => { confirms.push(opts); return answer; } }, {
        fetchImpl: async (url, init = {}) => {
            const method = init.method || 'GET';
            const body = init.body ? JSON.parse(init.body) : null;
            calls.push({ url, method, body });
            let status = 200, value;
            if (method === 'PUT') {
                if (put) [status, value] = await put(body);
                else value = client = { ...client, configured: true, source: 'personal', revision: client.revision + 1 };
            } else if (method === 'DELETE') value = client = { ...client, configured: false, source: 'none', revision: client.revision + 1 };
            else value = { available: client.configured, accounts: [], session: null, client };
            return { ok: status < 400, status, json: async () => value };
        },
    });
    const root = new Node('div'); app.mount(root);
    return { app, root, calls, confirms };
}

test('a missing personal client offers import/paste and a guide; opening authorizes nothing', async () => {
    const { app, root, calls } = setup(); await app.refresh();
    assert.ok(root.find('client-file') && root.find('client-text'));
    assert.match(root.find('client-title').textContent, /Configura/);
    assert.match(root.find('client-help').text(), /Aplicación de escritorio/);
    assert.equal(root.find('client-console').href, 'https://console.cloud.google.com/apis/credentials');
    assert.equal(root.find('connect'), undefined);
    assert.deepEqual(calls.map(c => c.method), ['GET']); app.dispose();
});

test('pasting and saving configures the client, clears the draft and never starts OAuth or reading', async () => {
    const { app, root, calls } = setup(); await app.refresh();
    root.find('client-text').value = TEXT; root.find('client-text').fire('input');
    root.find('client-save').fire('click'); root.find('client-save').fire('click');
    await settle(); await settle();
    const writes = calls.filter(c => c.method !== 'GET');
    assert.deepEqual(writes, [{ url: '/api/connections/google/client', method: 'PUT', body: { revision: 0, json_text: TEXT } }]);
    assert.equal(root.find('client-text'), undefined);
    assert.match(root.find('client-title').textContent, /configurado/);
    assert.ok(root.find('connect'));
    assert.ok(!root.text().includes('synthetic-secret'));
    assert.ok(!calls.some(c => c.url.endsWith('/sessions') || c.url.endsWith('/sync')));
    app.dispose();
});

test('a file is read locally; selecting it does not save until the explicit button', async () => {
    const { app, root, calls } = setup(); await app.refresh();
    const file = root.find('client-file'); file.files = [{ name: 'client.json', size: TEXT.length, text: async () => TEXT }];
    file.fire('change'); await settle();
    assert.equal(root.find('client-text').value, TEXT);
    assert.equal(root.find('client-filename').textContent, 'client.json');
    assert.deepEqual(calls.map(c => c.method), ['GET']);
    root.find('client-save').fire('click'); await settle(); await settle();
    assert.equal(calls.filter(c => c.method === 'PUT').length, 1); app.dispose();
});

test('oversized files are rejected before reading or uploading them', async () => {
    const { app, root, calls } = setup(); await app.refresh();
    let read = false;
    const file = root.find('client-file'); file.files = [{ name: 'too-large.json', size: 65537, text: () => { read = true; } }];
    file.fire('change'); await settle();
    assert.equal(read, false); assert.match(root.find('status').textContent, /64 KB/);
    assert.deepEqual(calls.map(c => c.method), ['GET']); app.dispose();
});

test('invalid pasted JSON never leaves the form and shows a generic explanation', async () => {
    for (const text of ['secret-not-json', '{"web":{}}', '{"type":"service_account"}', 'x'.repeat(65537)]) {
        const { app, root, calls } = setup(); await app.refresh();
        root.find('client-text').value = text; root.find('client-text').fire('input');
        root.find('client-save').fire('click'); await settle();
        assert.deepEqual(calls.map(c => c.method), ['GET']);
        assert.match(root.find('status').textContent, /Aplicación de escritorio/);
        assert.ok(!root.find('status').textContent.includes('secret-not-json')); app.dispose();
    }
});

test('connected accounts block the editor; the old configuration is kept', async () => {
    const { app, root, calls } = setup({ config: { configured: true, can_change: false, revision: 4 } }); await app.refresh();
    assert.equal(root.find('client-edit').disabled, true);
    assert.equal(root.find('client-remove').disabled, true);
    assert.equal(root.find('client-text'), undefined);
    assert.match(root.find('client-blocked').textContent, /desconecta/);
    assert.deepEqual(calls.map(c => c.method), ['GET']); app.dispose();
});

test('server rejection retains the draft and never announces configuration success', async () => {
    const { app, root } = setup({ put: async () => [409, { error: { code: 'client_in_use', message: 'Desconecta tus cuentas.' } }] });
    await app.refresh(); root.find('client-text').value = TEXT; root.find('client-text').fire('input');
    root.find('client-save').fire('click'); await settle(); await settle();
    assert.equal(root.find('client-text').value, TEXT);
    assert.equal(root.find('connect'), undefined);
    assert.equal(root.find('status').textContent, 'Desconecta tus cuentas.'); app.dispose();
});

test('removing a saved configuration requires the Iris confirmation and its revision', async () => {
    for (const answer of [false, true]) {
        const { app, root, calls, confirms } = setup({ answer, config: { configured: true, revision: 3 } });
        await app.refresh(); root.find('client-remove').fire('click'); await settle(); await settle();
        assert.equal(confirms[0].title, 'Quitar configuración de Google');
        const deletes = calls.filter(c => c.method === 'DELETE');
        assert.equal(deletes.length, answer ? 1 : 0);
        if (answer) { assert.deepEqual(deletes[0].body, { revision: 3 }); assert.ok(root.find('client-text')); }
        app.dispose();
    }
});

test('a pending file read cannot restore credentials after the app is disposed', async () => {
    const { app, root, calls } = setup(); await app.refresh();
    let release;
    const file = root.find('client-file'); file.files = [{ name: 'client.json', size: TEXT.length,
        text: () => new Promise(resolve => { release = resolve; }) }];
    file.fire('change'); app.dispose(); release(TEXT); await settle();
    assert.equal(root.children.length, 0); assert.deepEqual(calls.map(c => c.method), ['GET']);
});

test('editing the client waits for Save or Cancel before offering another login', async () => {
    const { app, root, calls } = setup({ config: { configured: true, revision: 2 } }); await app.refresh();
    root.find('client-edit').fire('click');
    assert.equal(root.find('connect'), undefined);
    assert.equal(root.find('client-text').value, '', 'saved credentials are never copied back to the form');
    root.find('client-text').value = TEXT; root.find('client-text').fire('input');
    root.find('client-cancel').fire('click');
    assert.ok(root.find('connect'));
    assert.equal(root.find('client-text'), undefined);
    assert.deepEqual(calls.map(c => c.method), ['GET']); app.dispose();
});
