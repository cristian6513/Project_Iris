/**
 * F07 Automatizaciones: the real component over a minimal DOM, against a fake Core.
 *
 * Rules are created paused, enabling needs a confirmation, the permission is
 * never touched, the draft survives a conflict, late answers are discarded,
 * and it refreshes only while visible. Every text is set with textContent.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

class FakeNode {
    constructor(tag) {
        this.tagName = tag.toUpperCase();
        this.children = [];
        this.parentElement = null;
        this.listeners = {};
        this.attributes = {};
        this.dataset = {};
        this.disabled = false;
        this.hidden = false;
        this.checked = false;
        this.open = false;
        this.className = '';
        this.textContent = '';
        this.value = '';
        this.label = '';
        this.type = '';
        this.name = '';
        this.title = '';
        this.focused = 0;
    }
    append(...nodes) { for (const node of nodes) { node.parentElement?.removeChild?.(node); node.parentElement = this; this.children.push(node); } }
    removeChild(node) { this.children = this.children.filter((child) => child !== node); node.parentElement = null; }
    remove() { this.parentElement?.removeChild(this); }
    replaceChildren(...nodes) { for (const child of this.children) child.parentElement = null; this.children = []; this.append(...nodes); }
    setAttribute(name, value) { this.attributes[name] = String(value); }
    getAttribute(name) { return this.attributes[name] ?? null; }
    addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
    dispatch(type, extra = {}) { for (const fn of this.listeners[type] || []) fn({ type, target: this, preventDefault() {}, ...extra }); }
    click() { if (!this.disabled) this.dispatch('click'); }
    focus() { this.focused += 1; globalThis.document.activeElement = this; } // as a browser does
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
    texts() { return [this.textContent, ...this.children.flatMap((child) => child.texts())].filter(Boolean); }
}

globalThis.document = { activeElement: null, createElement: (tag) => new FakeNode(tag) };
const { AutomationApp } = await import('../../face/js/automation_app.js');

const RULE = {
    id: 'a'.repeat(32), revision: 1, enabled: false, title: 'Investigar al irme',
    trigger: { source: 'iris_idle', threshold_s: 300 },
    execution: { prompt: 'Resume lo que te encargué.', profile: 'default', provider: '', model: '', timeout_s: 900,
        max_runs_per_day: 4, tool_allowlist: [], token_budget: 0 },
    delivery: { voice: false }, problem: '', updated_at: 1,
};
const overview = (extra = {}) => ({ rules: [RULE], occurrences: [], permission: 'ask', ready_sources: ['iris_idle'],
    sources: { iris_idle: { status: 'known', condition: 'idle', confirmed_s: 60 } }, ...extra });
const selector = { supported: true, conversation: { mode: 'default' }, default: { provider: 'openai-codex', model: 'gpt-5.5' },
    inventory: { providers: [{ id: 'openai-codex', label: 'OpenAI Codex', authenticated: true, models: ['gpt-5.5'] }] } };

function fakeCore(routes) {
    const calls = [];
    const fetchImpl = async (url, init = {}) => {
        calls.push({ url, method: init.method || 'GET', body: init.body ? JSON.parse(init.body) : null });
        const handler = routes[`${init.method || 'GET'} ${url}`] || routes[url];
        const [status, body] = typeof handler === 'function' ? handler(init) : handler || [404, { detail: 'no' }];
        return { ok: status < 400, status, json: async () => body };
    };
    return { calls, fetchImpl };
}

const settle = async () => { for (let i = 0; i < 20; i += 1) await Promise.resolve(); };
const byText = (app, tag, label) => app.surface.find((n) => n.tagName === tag && n.textContent === label);
const button = (app, label) => byText(app, 'BUTTON', label);

function make(routes, options = {}) {
    const core = fakeCore(routes);
    const timers = [];
    globalThis.document.activeElement = null;
    const app = new AutomationApp({
        root: new FakeNode('div'), fetchImpl: core.fetchImpl, isVisible: options.isVisible || (() => true),
        confirmImpl: options.confirm || (() => true), onOpenApp: options.onOpenApp,
        setTimer: (fn, ms) => { timers.push({ fn, ms }); return timers.length; }, clearTimer: () => {},
    });
    return { app, core, timers };
}

test('constructing the window asks Core nothing; showing it loads once', async () => {
    const { app, core } = make({ '/api/automations': [200, overview()] });
    assert.equal(core.calls.length, 0);
    await app.load();
    assert.deepEqual(core.calls.map((c) => c.url), ['/api/automations']);
    assert.ok(app.surface.texts().includes('Investigar al irme'));
    assert.ok(app.surface.texts().includes('En pausa'));
    assert.ok(app.surface.texts().some((t) => /Preguntar/.test(t)));
});

test('Windows observation needs explicit consent, carries its revision and never changes the permission', async () => {
    let enabled = false;
    const confirmations = [];
    const { app, core } = make({
        '/api/automations': () => [200, overview({ presence: { supported: true, revision: enabled ? 1 : 0, desktop_idle: enabled } })],
        'PUT /api/automations/preferences': (init) => { enabled = JSON.parse(init.body).desktop_idle; return [200, {}]; },
    }, { confirm: (message) => { confirmations.push(message); return true; } });
    await app.load();
    assert.equal(core.calls.length, 1);
    button(app, 'Activar observación de Windows').click();
    await settle();
    assert.equal(confirmations.length, 1);
    assert.match(confirmations[0], /No lee lo que escribes ni usa la cámara/);
    const write = core.calls.find((c) => c.method === 'PUT');
    assert.deepEqual(write.body, { expected_revision: 0, desktop_idle: true, confirm: true });
    assert.ok(button(app, 'Desactivar observación de Windows'));
    assert.equal(app.state.permission, 'ask');
});

test('cancelling native consent sends no write', async () => {
    const { app, core } = make({ '/api/automations': [200, overview({ presence: { supported: true, revision: 3, desktop_idle: false } })] },
        { confirm: () => false });
    await app.load();
    await app.setPresence(true);
    assert.equal(core.calls.length, 1);
});

test('a refresh restores keyboard focus to the same preference control', async () => {
    const { app } = make({ '/api/automations': [200, overview({ presence: { supported:true,voice_supported:true,
        revision:0,desktop_idle:false,return_voice:false,focus:false } })] });
    await app.load();
    button(app,'Activar entregas por voz').focus();
    await app.load(true);
    assert.equal(globalThis.document.activeElement,button(app,'Activar entregas por voz'));
});

test('voice consent names the configured voice service and does not enable tasks or Windows observation', async () => {
    let voice = false;
    const confirmations = [];
    const { app, core } = make({
        '/api/automations': () => [200, overview({ presence: { supported: true, voice_supported: true,
            revision: voice ? 1 : 0, desktop_idle: false, return_voice: voice, focus: false } })],
        'PUT /api/automations/preferences': (init) => { voice = JSON.parse(init.body).return_voice; return [200, {}]; },
    }, { confirm: (message) => { confirmations.push(message); return true; } });
    await app.load();
    await app.setVoicePreference('return_voice', true);
    assert.match(confirmations[0], /servicio externo/);
    assert.deepEqual(core.calls.find((c) => c.method === 'PUT').body,
        { expected_revision: 0, return_voice: true, confirm: true });
    assert.equal(app.state.presence.desktopIdle, false);
    assert.equal(app.state.rules[0].enabled, false);
    assert.ok(button(app, 'Desactivar entregas por voz'));
});

test('a new rule is saved paused with what Core expects, and never changes the permission', async () => {
    const created = [];
    const { app, core } = make({
        '/api/automations': [200, overview()],
        '/api/models/selector': [200, selector],
        'POST /api/automations/rules': (init) => { created.push(JSON.parse(init.body)); return [200, { ...RULE, id: 'b'.repeat(32) }]; },
    });
    await app.load();
    button(app, 'Nueva regla').click();
    await settle();
    assert.equal(app.form.hidden, false);
    assert.ok(core.calls.some((c) => c.url === '/api/models/selector'));
    const type = (name, value) => { app.inputs[name].value = value; app.inputs[name].dispatch('input'); };
    type('title', 'Resumen del día');
    type('prompt', 'Resume mis notas (sintético).');
    type('thresholdMinutes', '10');
    app.modelSelect.value = app.modelSelect.find((n) => n.tagName === 'OPTION' && n.textContent === 'gpt-5.5').value;
    app.modelSelect.dispatch('change');
    app.form.dispatch('submit');
    await settle();
    assert.equal(created.length, 1);
    assert.equal(created[0].title, 'Resumen del día');
    assert.deepEqual(created[0].trigger, { source: 'iris_idle', threshold_s: 600 });
    assert.equal(created[0].execution.model, 'gpt-5.5');
    assert.ok(!('enabled' in created[0]));
    assert.ok(!core.calls.some((c) => c.url.includes('capability')));
    assert.equal(app.form.hidden, true);
    assert.match(app.status.textContent, /Guardada en pausa/);
});

test('an invalid draft is not sent and its errors are shown', async () => {
    const { app, core } = make({ '/api/automations': [200, overview()], '/api/models/selector': [200, selector] });
    await app.load();
    button(app, 'Nueva regla').click();
    await settle();
    app.form.dispatch('submit');
    await settle();
    assert.ok(!core.calls.some((c) => c.method === 'POST'));
    assert.equal(app.fields.title.error.textContent, 'Ponle un nombre.');
    assert.equal(app.inputs.title.getAttribute('aria-invalid'), 'true');
    assert.match(app.status.textContent, /Revisa los campos/);
});

test('enabling needs an explicit confirmation and sends the revision it read', async () => {
    let answer = false;
    const puts = [];
    const { app } = make({
        '/api/automations': [200, overview()],
        [`PUT /api/automations/rules/${RULE.id}`]: (init) => { puts.push(JSON.parse(init.body));
            return [200, { ...RULE, enabled: true, revision: 2, warning: 'Para que se ejecute sin ti, pon «Automatizaciones» en «Permitir»' }]; },
    }, { confirm: () => answer });
    await app.load();
    button(app, 'Activar').click();
    await settle();
    assert.equal(puts.length, 0); // declined: nothing sent
    answer = true;
    button(app, 'Activar').click();
    await settle();
    assert.deepEqual(puts, [{ expected_revision: 1, enabled: true, confirm: true }]);
    assert.match(app.status.textContent, /Permitir/);
});

test('a conflicting edit keeps the typed draft and reloads the latest version', async () => {
    let revision = 1;
    const { app } = make({
        '/api/automations': () => [200, overview({ rules: [{ ...RULE, revision }] })],
        '/api/models/selector': [200, selector],
        [`PUT /api/automations/rules/${RULE.id}`]: () => { revision = 3;
            return [409, { detail: { code: 'revision_conflict', message: 'La regla cambió' } }]; },
    });
    await app.load();
    button(app, 'Editar').click();
    await settle();
    app.inputs.prompt.value = 'Mi texto nuevo';
    app.inputs.prompt.dispatch('input');
    app.form.dispatch('submit');
    await settle();
    assert.match(app.status.textContent, /cambió en otro lugar/);
    assert.equal(app.form.hidden, false);
    assert.equal(app.inputs.prompt.value, 'Mi texto nuevo'); // never lost
    assert.equal(app.draft.prompt, 'Mi texto nuevo');
    assert.equal(app.state.rules[0].revision, 3);
});

test('a refresh keeps the typed draft and gives the focus back to the same control', async () => {
    const { app } = make({ '/api/automations': [200, overview({ rules: [{ ...RULE, enabled: true }] })],
        '/api/models/selector': [200, selector] });
    await app.load();
    const old = button(app, 'Pausar');
    old.focus();
    await app.load(true);
    const pause = button(app, 'Pausar');
    assert.notEqual(pause, old); // the list was rebuilt…
    assert.equal(globalThis.document.activeElement, pause); // …and the keyboard stays on «Pausar»
    globalThis.document.activeElement = null;
    button(app, 'Editar').click();
    await settle();
    app.inputs.prompt.value = 'A medio escribir';
    app.inputs.prompt.dispatch('input');
    await app.load(true);
    assert.equal(app.inputs.prompt.value, 'A medio escribir');
    assert.equal(app.draft.prompt, 'A medio escribir');
});

test('a refresh that arrives during a save waits and runs once afterwards', async () => {
    let release;
    const { app, core } = make({
        '/api/automations': [200, overview()],
        '/api/models/selector': [200, selector],
        [`PUT /api/automations/rules/${RULE.id}`]: () => [200, { ...RULE, revision: 2 }],
    });
    const fetchImpl = app.fetch;
    app.fetch = (url, init = {}) => (init.method === 'PUT'
        ? new Promise((resolve) => { release = () => resolve(fetchImpl(url, init)); }) : fetchImpl(url, init));
    await app.load();
    button(app, 'Editar').click();
    await settle();
    app.inputs.title.value = 'Otro nombre';
    app.inputs.title.dispatch('input');
    app.form.dispatch('submit');
    await settle();
    const reads = () => core.calls.filter((c) => c.url === '/api/automations').length;
    const before = reads();
    await app.load(true); // the periodic refresh lands while saving
    assert.equal(reads(), before);
    assert.equal(app.status.textContent, 'Guardando…');
    release();
    await settle();
    assert.equal(reads(), before + 1);
    assert.match(app.status.textContent, /Cambios guardados/);
});

test('an older answer never overwrites a newer one', async () => {
    const pending = [];
    const app = new AutomationApp({
        root: new FakeNode('div'), isVisible: () => false,
        fetchImpl: () => new Promise((resolve) => pending.push(resolve)),
    });
    const first = app.load(true);
    const second = app.load(true);
    pending[1]({ ok: true, status: 200, json: async () => overview({ rules: [{ ...RULE, title: 'Nueva' }] }) });
    await second;
    pending[0]({ ok: true, status: 200, json: async () => overview({ rules: [{ ...RULE, title: 'Vieja' }] }) });
    await first;
    assert.equal(app.state.rules[0].title, 'Nueva');
});

test('it refreshes only while visible, and hiding stops the timer', async () => {
    let visible = false;
    const { app, timers } = make({ '/api/automations': [200, overview({ rules: [{ ...RULE, enabled: true }] })] },
        { isVisible: () => visible });
    await app.load(true);
    assert.equal(timers.length, 0); // loaded while hidden (the shell asked): no polling
    visible = true;
    app.syncVisibility();
    await settle();
    assert.equal(timers.length, 1);
    assert.equal(timers[0].ms, 20000);
    visible = false;
    app.syncVisibility();
    timers[0].fn(); // a timer that fires after hiding asks nothing
    await settle();
    assert.equal(timers.length, 1);
});

test('cancelling says what really happened; a stopping run is not cancelled again', async () => {
    const occurrence = (state, id, runId = '') => ({ id, rule_id: RULE.id, rule_revision: 1, state, error: '', run_id: runId,
        created_at: 1, updated_at: 2 });
    const { app } = make({
        '/api/automations': [200, overview({ occurrences: [occurrence('running', 'c'.repeat(32), 'run_1'),
            occurrence('stopping', 'd'.repeat(32))] })],
        [`POST /api/automations/occurrences/${'c'.repeat(32)}/cancel`]: [200, { result: 'stop_requested' }],
    });
    await app.load();
    app._showTab('history', { focus: false });
    const buttons = app.historyList.querySelectorAll('button');
    assert.deepEqual(buttons.map((b) => b.textContent), ['Pedir que se detenga']); // stopping has no cancel
    buttons[0].click();
    await settle();
    assert.match(app.status.textContent, /Aparecerá detenida cuando Hermes lo confirme/);
    assert.ok(app.historyList.texts().includes('Deteniéndose'));
    const old = app.historyList.querySelectorAll('button')[0];
    old.focus();
    await app.load(true);
    const again = app.historyList.querySelectorAll('button')[0];
    assert.notEqual(again, old);
    assert.equal(globalThis.document.activeElement, again);
});

test('a store that cannot be opened is said, and nothing can be created', async () => {
    const { app } = make({ '/api/automations': [200, overview({ rules: [], store_problem: 'El perfil contiene un enlace.' })] });
    await app.load();
    assert.match(app.status.textContent, /no está disponible: El perfil contiene un enlace/);
    assert.equal(button(app, 'Nueva regla').disabled, true);
});

test('a failed refresh keeps the last answer visible and says it is not updated', async () => {
    let fail = false;
    const { app } = make({ '/api/automations': () => (fail ? [500, { detail: 'Core se reinicia' }] : [200, overview()]) });
    await app.load();
    fail = true;
    await app.load(true);
    assert.ok(app.surface.texts().includes('Investigar al irme'));
    assert.match(app.status.textContent, /Core se reinicia Se muestra lo último/);
});

test('Capability Center opens through the shell; texts never go through innerHTML', async () => {
    const opened = [];
    const { app } = make({ '/api/automations': [200, overview()] }, { onOpenApp: (name) => opened.push(name) });
    await app.load();
    button(app, 'Abrir Capability Center').click();
    assert.deepEqual(opened, ['permissions']);
    const source = readFileSync(new URL('../../face/js/automation_app.js', import.meta.url), 'utf8');
    assert.ok(!source.includes('innerHTML'));
});

test('F09.2: as an app module it mounts in its view, reads Core once per open and retires cleanly', async () => {
    const { createApp } = await import('../../face/js/automation_app.js');
    const core = fakeCore({ '/api/automations': [200, overview()] });
    const original = globalThis.fetch;
    globalThis.fetch = core.fetchImpl;
    try {
        let visible = false;
        const briefs = [];
        const controller = createApp({ isVisible: () => visible, setBrief: (text) => briefs.push(text) });
        const root = new FakeNode('div');
        controller.mount(root);
        assert.equal(root.children.length, 1);
        assert.equal(root.children[0].className, 'automation-host');
        assert.equal(core.calls.length, 0, 'mounting asks Core nothing');
        // Open: the shell refreshes, then reports it visible (no await in between).
        visible = true;
        controller.refresh({ opening: true });
        controller.setVisibility({ visible: true });
        await settle();
        assert.deepEqual(core.calls.map((c) => c.url), ['/api/automations'], 'one read per open');
        assert.deepEqual(briefs, ['Sin reglas activas']);
        // Minimize and restore: hidden stops it; restoring reads once more.
        visible = false;
        controller.setVisibility({ visible: false });
        visible = true;
        controller.refresh({ opening: true });
        controller.setVisibility({ visible: true });
        await settle();
        assert.equal(core.calls.length, 2);
        // The inbox's «Ver en Automatizaciones» lands on that rule's history.
        controller.navigate({ history: RULE.id });
        const historyTab = root.find((node) => node.tagName === 'BUTTON' && node.textContent === 'Historial');
        assert.equal(historyTab?.getAttribute('aria-selected'), 'true');
        controller.dispose();
        controller.dispose();
        assert.equal(root.children.length, 0, 'retiring removes its view');
    } finally {
        globalThis.fetch = original;
    }
});
