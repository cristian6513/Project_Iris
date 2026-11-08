import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createAppModelPicker } from '../../face/js/app_model_picker.js';

class Node {
    constructor(tag) { Object.assign(this, { tag, dataset: {}, children: [], listeners: {}, attributes: {} }); }
    append(...nodes) { this.children.push(...nodes); }
    setAttribute(key, value) { this.attributes[key] = value; }
    addEventListener(key, fn) { this.listeners[key] = fn; }
    find(part) { return this.dataset.part === part ? this : this.children.map(n => n.find(part)).find(Boolean); }
}
const row = (id, models, extra = {}) => ({ id, label: id, authenticated: true, models, ...extra });
const DATA = { default: { provider: 'configured', model: 'chosen' }, inventory: { providers: [
    row('configured', ['chosen', 'other']), row('nous', ['free', 'paid'], { unavailable_models: ['paid'] }),
    row('not-connected', ['hidden'], { authenticated: false })] } };
function setup({ selection = { provider: '', model: '' }, respond = async () => DATA } = {}) {
    const calls = [], changes = [];
    const picker = createAppModelPicker({ document: { createElement: tag => new Node(tag) },
        prefix: 'test', providerPart: 'provider', modelPart: 'model', changed() {},
        getSelection: () => selection, onSelection: next => { selection = next; changes.push(next); },
        fetchImpl: async (url, init) => { calls.push({ url, init }); return { ok: true, json: respond }; } });
    return { picker, calls, changes, selection: () => selection };
}

test('creation is passive; explicit load reads only the inventory and inherits the configured default', async () => {
    const t = setup(); assert.equal(t.calls.length, 0); await t.picker.load();
    assert.deepEqual(t.selection(), DATA.default); assert.equal(t.picker.valid(), true);
    assert.equal(t.calls.length, 1); assert.equal(t.calls[0].url, '/api/models/apps');
    assert.equal(t.calls[0].init.cache, 'no-store'); assert.equal(t.calls[0].init.method, undefined);
    assert.ok(!t.picker.render().find('provider').children.some(n => n.value === 'not-connected'));
    t.picker.dispose();
});

test('saved app selection wins over the chat default; changes to provider cannot inherit its model', async () => {
    const t = setup({ selection: { provider: 'configured', model: 'other' } }); await t.picker.load();
    assert.equal(t.selection().model, 'other');
    const field = t.picker.render().find('provider'); field.value = 'nous'; field.listeners.change();
    assert.deepEqual(t.selection(), { provider: 'nous', model: '' });
    const model = t.picker.render().find('model');
    assert.equal(model.children.find(n => n.value === 'paid').disabled, true);
    model.value = 'paid'; model.listeners.change(); assert.equal(t.selection().model, '');
    model.value = 'free'; model.listeners.change(); assert.equal(t.picker.valid(), true);
    t.picker.dispose();
});

test('a saved missing model remains visible as unavailable, without quietly substituting another', async () => {
    const t = setup({ selection: { provider: 'configured', model: 'removed' } }); await t.picker.load();
    assert.equal(t.picker.valid(), false); assert.equal(t.selection().model, 'removed');
    assert.equal(t.picker.render().find('model-missing').disabled, true); assert.equal(t.changes.length, 0);
    t.picker.dispose();
});

test('refresh invalidates previous consent and respects a new plan lock; failures remain retryable', async () => {
    let fail = false, locked = false;
    const t = setup({ respond: async () => { if (fail) throw new Error('offline'); return locked
        ? { ...DATA, inventory: { providers: [row('configured', ['chosen', 'other'], { unavailable_models: ['chosen'] })] } } : DATA; } });
    await t.picker.load(); locked = true; await t.picker.load({ refresh: true });
    assert.equal(t.calls[1].url, '/api/models/apps?refresh=true'); assert.equal(t.changes.length, 2);
    assert.equal(t.picker.valid(), false); assert.equal(t.selection().model, 'chosen');
    fail = true; await t.picker.load({ refresh: true }); assert.equal(t.picker.valid(), false);
    assert.match(t.picker.render().find('test-catalog-status').textContent, /No se pudo/);
    fail = false; locked = false; await t.picker.load({ refresh: true }); assert.equal(t.picker.valid(), true);
    t.picker.dispose();
});

test('an offline engine is not presented as an account with no configured providers', async () => {
    const t = setup({ respond: async () => ({ ...DATA, reason: 'hermes_offline' }) }); await t.picker.load();
    assert.match(t.picker.render().find('test-catalog-status').textContent, /Comprueba que Iris esté iniciada/);
    assert.deepEqual(t.selection(), { provider: '', model: '' }); t.picker.dispose();
});

test('an answer after disposal cannot change the app selection', async () => {
    let resolve; const pending = new Promise(yes => resolve = yes);
    const t = setup({ respond: () => pending }); const loading = t.picker.load();
    t.picker.dispose(); assert.equal(t.calls[0].init.signal.aborted, true);
    resolve(DATA); await loading; assert.equal(t.changes.length, 0);
});
