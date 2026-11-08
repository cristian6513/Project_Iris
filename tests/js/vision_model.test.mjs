/**
 * F02 "Visión": the view model and the real component over a minimal DOM.
 *
 * The view lists only models Hermes does not reject for images, separates the
 * confirmed ones from the unknown ones, describes the route without claiming
 * more than the server said, and orders reads and writes like the selector app.
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import {
    choiceGroups,
    draftFromState,
    lastSummary,
    normalizeVisionState,
    payloadFor,
    routeSummary,
} from '../../face/js/vision_model_state.js';

function raw(overrides = {}) {
    return {
        profile: 'default',
        mode: 'auto',
        route: 'chat',
        configured: { provider: 'auto', model: '', custom_endpoint: false, image_input_mode: 'auto' },
        planned: { provider: 'openai-codex', model: 'gpt-5.5', available: true },
        chosen_vision: null,
        models: [
            { provider: 'openai-codex', provider_label: 'OpenAI Codex', model: 'gpt-5.5', vision: true },
            { provider: 'openai-codex', provider_label: 'OpenAI Codex', model: 'codex-texto', vision: false },
            { provider: 'local', provider_label: 'Local', model: 'llava', vision: null },
        ],
        writable: true,
        last: null,
        ...overrides,
    };
}

test('only models that may read images are offered, the unknown ones apart', () => {
    const state = normalizeVisionState(raw());
    const { groups, excluded } = choiceGroups(state, draftFromState(state));
    assert.deepEqual(groups.map((g) => [g.id, g.models.map((m) => m.model)]),
        [['vision', ['gpt-5.5']], ['unknown', ['llava']]]);
    assert.equal(excluded, 1);
});

test('a saved model the catalog no longer lists stays visible as missing', () => {
    const state = normalizeVisionState(raw({ mode: 'model', route: 'vision_model',
        configured: { provider: 'gone', model: 'viejo', custom_endpoint: false } }));
    const { groups } = choiceGroups(state, draftFromState(state));
    assert.ok(groups.find((g) => g.id === 'unknown').models.some((m) => m.model === 'viejo' && m.missing));
});

test('the route is described from the server answer, never assumed', () => {
    const auto = routeSummary(normalizeVisionState(raw()));
    assert.equal(auto.tone, 'ok');
    assert.match(auto.lines.join(' '), /viajan con tu mensaje/);
    assert.match(auto.lines.join(' '), /las analiza gpt-5\.5 \(openai-codex\)/);

    const none = routeSummary(normalizeVisionState(raw({ planned: { provider: '', model: '', available: false } })));
    assert.equal(none.tone, 'warn');
    assert.match(none.lines.join(' '), /no tiene ahora un modelo de visión/);

    const explicit = routeSummary(normalizeVisionState(raw({
        mode: 'model', route: 'vision_model', chosen_vision: null,
        configured: { provider: 'local', model: 'llava' },
        planned: { provider: 'local', model: 'llava', available: true },
    })));
    assert.match(explicit.lines[0], /las describe llava \(local\) y el chat recibe esa descripción/);
    assert.match(explicit.lines.join(' '), /no puede confirmar que este modelo lea imágenes/);

    const unavailable = routeSummary(normalizeVisionState(raw({
        mode: 'model', route: 'vision_model', chosen_vision: true,
        configured: { provider: 'openrouter', model: 'gemini' },
        planned: { provider: 'openrouter', model: '', available: false },
    })));
    assert.equal(unavailable.tone, 'warn');
    assert.match(unavailable.lines.join(' '), /Iris no enviará imágenes hasta que esté disponible/);

    const custom = routeSummary(normalizeVisionState(raw({ mode: 'model', route: 'vision_model',
        configured: { provider: 'auto', model: '', custom_endpoint: true } })));
    assert.match(custom.lines[0], /configurado fuera de Iris/);
});

test('payloads name the profile they were made for', () => {
    const state = normalizeVisionState(raw({ profile: 'coder' }));
    assert.deepEqual(payloadFor(state, { mode: 'auto', key: '' }), { profile: 'coder', mode: 'auto' });
    assert.deepEqual(payloadFor(state, { mode: 'model', key: 'local\u0000llava' }),
        { profile: 'coder', mode: 'model', provider: 'local', model: 'llava' });
});

test('the last analysis is reported as it happened', () => {
    const at = 1_000_000;
    const ok = normalizeVisionState(raw({ last: { ok: true, provider: 'local', model: 'llava', at } }));
    assert.equal(lastSummary(ok, (at + 180) * 1000), 'Última imagen: la analizó llava (local) hace 3 min.');
    const failed = normalizeVisionState(raw({ last: { ok: false, provider: 'local', model: 'llava', at } }));
    assert.match(lastSummary(failed, at * 1000), /no se pudo analizar con llava \(local\) hace un momento/);
});

// ── The component ─────────────────────────────────────────────────────────

class FakeNode {
    constructor(tag) {
        this.tagName = tag.toUpperCase();
        this.children = [];
        this.parentElement = null;
        this.listeners = {};
        this.attributes = {};
        this.dataset = {};
        this.disabled = false;
        this.checked = false;
        this.className = '';
        this.textContent = '';
        this.value = '';
        this.label = '';
        this.type = '';
        this.name = '';
    }
    get options() { return this.querySelectorAll('option'); }
    append(...nodes) { for (const node of nodes) { node.parentElement?.removeChild?.(node); node.parentElement = this; this.children.push(node); } }
    removeChild(node) { this.children = this.children.filter((child) => child !== node); node.parentElement = null; }
    replaceChildren(...nodes) { for (const child of this.children) child.parentElement = null; this.children = []; this.append(...nodes); }
    setAttribute(name, value) { this.attributes[name] = String(value); }
    addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
    dispatch(type) { for (const fn of this.listeners[type] || []) fn({ type, target: this }); }
    click() { if (!this.disabled) this.dispatch('click'); }
    querySelectorAll(tag) {
        const out = [];
        for (const child of this.children) {
            if (child.tagName === tag.toUpperCase()) out.push(child);
            out.push(...child.querySelectorAll(tag));
        }
        return out;
    }
    find(predicate) {
        for (const child of this.children) {
            if (predicate(child)) return child;
            const found = child.find(predicate);
            if (found) return found;
        }
        return null;
    }
}

globalThis.document = { activeElement: null, createElement: (tag) => new FakeNode(tag) };
const { VisionModelApp } = await import('../../face/js/vision_model_settings.js');

function controlledFetch() {
    const calls = [];
    const fetchImpl = (url, init = {}) => {
        let resolve;
        const promise = new Promise((done) => { resolve = done; });
        calls.push({ url, init, resolve });
        return promise;
    };
    return { calls, fetchImpl };
}

const reply = (body, status = 200) => ({ ok: status < 400, status, json: async () => body });
const settle = async () => { for (let i = 0; i < 10; i += 1) await Promise.resolve(); };
const button = (app, label) => app.surface.find((n) => n.tagName === 'BUTTON' && n.textContent === label);
const radio = (app, value) => app.surface.find((n) => n.tagName === 'INPUT' && n.value === value);

test('the tab reads once it is shown and saves the chosen model for that profile', async () => {
    const fetch = controlledFetch();
    const app = new VisionModelApp({ root: new FakeNode('div'), fetchImpl: fetch.fetchImpl });
    assert.equal(fetch.calls.length, 0); // building it asks Hermes nothing
    const loading = app.load(true);
    assert.equal(app.status.textContent, 'Consultando a Hermes… puede tardar unos segundos.');
    fetch.calls[0].resolve(reply(raw({ profile: 'coder' })));
    await loading;
    assert.equal(app.profileChip.textContent, 'Perfil de Hermes: coder');
    assert.equal(button(app, 'Guardar').disabled, true);

    const model = radio(app, 'model');
    model.checked = true;
    model.dispatch('change');
    assert.equal(app.draft.key, 'openai-codex\u0000gpt-5.5'); // first model that reads images
    app.select.value = 'local\u0000llava';
    app.select.dispatch('change');
    assert.equal(button(app, 'Guardar').disabled, false);

    const saving = app.save();
    assert.equal(fetch.calls[1].init.method, 'PUT');
    assert.deepEqual(JSON.parse(fetch.calls[1].init.body),
        { profile: 'coder', mode: 'model', provider: 'local', model: 'llava' });
    assert.equal(app.select.disabled, true); // nothing changes while Hermes writes
    fetch.calls[1].resolve(reply(raw({ profile: 'coder', mode: 'model', route: 'vision_model',
        configured: { provider: 'local', model: 'llava' }, planned: { provider: 'local', model: 'llava', available: true } })));
    await saving;
    assert.equal(app.status.textContent, 'Modelo de visión guardado en Hermes.');
    assert.equal(app.dirty, false);
});

test('a reload asked for during a save waits, and a profile switch is reported', async () => {
    const fetch = controlledFetch();
    const app = new VisionModelApp({ root: new FakeNode('div'), fetchImpl: fetch.fetchImpl });
    const loading = app.load(true);
    fetch.calls[0].resolve(reply(raw()));
    await loading;
    radio(app, 'model').checked = true;
    radio(app, 'model').dispatch('change');
    const saving = app.save();
    app.load(true); // the shell reloads meanwhile
    assert.equal(fetch.calls.length, 2); // deferred, not sent
    fetch.calls[1].resolve(reply({ detail: 'El perfil de Hermes cambió mientras se guardaba; vuelve a elegir el modelo de visión.' }, 409));
    await saving;
    await settle();
    assert.equal(fetch.calls.length, 3); // the deferred reload runs after the save
    assert.equal(fetch.calls[2].init.method, undefined);
    assert.match(app.status.textContent, /Consultando|Comprobando/);
    fetch.calls[2].resolve(reply(raw({ profile: 'coder' })));
    await settle();
    assert.equal(app.profileChip.textContent, 'Perfil de Hermes: coder');
});

test('a read-only profile keeps every control disabled and says why', async () => {
    const fetch = controlledFetch();
    const app = new VisionModelApp({ root: new FakeNode('div'), fetchImpl: fetch.fetchImpl });
    const loading = app.load(true);
    fetch.calls[0].resolve(reply(raw({ writable: false,
        reason: 'El Gateway de Hermes no es el de este equipo: su modelo de visión se cambia en ese Hermes.' })));
    await loading;
    assert.equal(radio(app, 'auto').disabled, true);
    assert.equal(radio(app, 'model').disabled, true);
    assert.equal(button(app, 'Guardar').disabled, true);
    assert.match(app.status.textContent, /no es el de este equipo/);
});

test('an older read cannot replace a newer one', async () => {
    const fetch = controlledFetch();
    const app = new VisionModelApp({ root: new FakeNode('div'), fetchImpl: fetch.fetchImpl });
    const first = app.load(true);
    const second = app.load(true, { refresh: true });
    assert.equal(fetch.calls[1].url, '/api/vision-model?refresh=true');
    fetch.calls[1].resolve(reply(raw({ profile: 'nuevo' })));
    await second;
    fetch.calls[0].resolve(reply(raw({ profile: 'viejo' })));
    await first;
    assert.equal(app.profileChip.textContent, 'Perfil de Hermes: nuevo');
});
