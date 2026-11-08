// F09.2: the app host loads each app with its own module once, keeps it while its window closes,
// retries only on request and retires it for good only on dispose. The shell mounts it in its
// view, hands it navigations and visibility, and never reopens a window closed while it loaded.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { OS_APP_CATALOG } from '../../face/js/os_app_catalog.js';
import { AppDisposedError, createAppHost } from '../../face/js/os_app_host.js';
import { createAppRegistry } from '../../face/js/os_app_registry.js';
import { AutomationApp } from '../../face/js/automation_app.js';

const shell = readFileSync(new URL('../../face/js/hermes_activity.js', import.meta.url), 'utf8');
const OS_APPS = createAppRegistry(OS_APP_CATALOG);

function method(name, deps = {}) {
    const block = shell.match(new RegExp(`^    (?:async )?${name}\\([^]*?^    \\}`, 'm'))?.[0];
    assert.ok(block, `${name} must exist`);
    return new Function(...Object.keys(deps), `return ({${block}})`)(...Object.values(deps))[name];
}

function deferred() {
    let resolve;
    let reject;
    const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
    return { promise, resolve, reject };
}

const flush = () => new Promise((resolve) => setImmediate(resolve));

/** A module whose import the test releases, and a log of what its controllers hear. */
function fakeModule() {
    const log = [];
    const imports = [];
    let created = 0;
    const module = {
        createApp(context) {
            created += 1;
            const n = created;
            log.push(`create:${n}`);
            return {
                context,
                mount: (root) => log.push(`mount:${n}:${root.id}`),
                setVisibility: ({ visible }) => log.push(`visible:${n}:${visible}`),
                refresh: ({ force, opening }) => log.push(`refresh:${n}:${force}:${opening}`),
                navigate: (payload) => log.push(`navigate:${n}:${JSON.stringify(payload)}`),
                dispose: () => log.push(`dispose:${n}`),
            };
        },
    };
    const load = () => {
        const pending = deferred();
        imports.push(pending);
        return pending.promise;
    };
    return { log, imports, module, load, created: () => created };
}

test('concurrent opens share one import and one instance', async () => {
    const fake = fakeModule();
    const host = createAppHost({ modules: { notes: fake.load } });
    const a = host.ensure('notes');
    const b = host.ensure('notes');
    assert.equal(host.state('notes'), 'loading');
    assert.equal(fake.imports.length, 1, 'one import for both');
    assert.equal(a, b, 'both wait for the same load');
    fake.imports[0].resolve(fake.module);
    const [first, second] = await Promise.all([a, b]);
    assert.equal(first, second);
    assert.equal(fake.created(), 1);
    assert.equal(await host.ensure('notes'), first, 'a ready app is reused');
    assert.equal(fake.imports.length, 1);
});

test('a failed load stays failed until an explicit retry', async () => {
    const fake = fakeModule();
    const errors = [];
    const host = createAppHost({ modules: { notes: fake.load }, onError: (id) => errors.push(id) });
    const first = host.ensure('notes');
    fake.imports[0].reject(new Error('offline'));
    await assert.rejects(first, /offline/);
    assert.equal(host.state('notes'), 'error');
    const repeated = host.ensure('notes');
    assert.equal(fake.imports.length, 1, 'ensure does not retry on its own');
    await assert.rejects(repeated, /offline/);
    const again = host.retry('notes');
    assert.equal(host.state('notes'), 'loading');
    fake.imports[1].resolve(fake.module);
    await again;
    assert.equal(host.state('notes'), 'ready');
    assert.deepEqual(errors, ['notes']);
});

test('a module without createApp, a bad controller or no module at all fail locally', async () => {
    const host = createAppHost({
        modules: {
            empty: async () => ({}),
            partial: async () => ({ createApp: () => ({ mount() {} }) }),
        },
    });
    await assert.rejects(host.ensure('empty'), /no exporta createApp/);
    await assert.rejects(host.ensure('partial'), /mount\(\) y dispose\(\)/);
    await assert.rejects(host.ensure('missing'), /no tiene un módulo registrado/);
    assert.equal(host.state('empty'), 'error');
});

test('an app\'s stylesheet loads with its module and is installed before the app exists', async () => {
    const fake = fakeModule();
    const order = [];
    const installed = [];
    const host = createAppHost({
        modules: { notes: async () => { order.push('module'); return { createApp: (ctx) => { order.push('create'); return fake.module.createApp(ctx); } }; } },
        styles: { notes: async () => { order.push('style'); return { default: '.notes { color: red; }' }; }, broken: async () => ({ default: 42 }) },
        installStyle: (id, css) => { order.push('install'); installed.push([id, css]); },
    });
    await host.ensure('notes');
    assert.deepEqual(order, ['module', 'style', 'install', 'create']);
    assert.deepEqual(installed, [['notes', '.notes { color: red; }']]);
    const broken = createAppHost({ modules: { broken: async () => fake.module }, styles: { broken: async () => ({ default: 42 }) } });
    await assert.rejects(broken.ensure('broken'), /hoja de estilos de broken no es válida/);
    assert.equal(broken.state('broken'), 'error');
});

test('the shell adopts each app stylesheet once', () => {
    const adopted = [];
    class FakeSheet { replaceSync(css) { this.css = css; } }
    const fakeDocument = {
        get adoptedStyleSheets() { return adopted.slice(); },
        set adoptedStyleSheets(list) { adopted.splice(0, adopted.length, ...list); },
    };
    const state = {};
    const install = method('_installAppStyle', { document: fakeDocument, CSSStyleSheet: FakeSheet });
    install.call(state, 'notes', '.notes {}');
    install.call(state, 'notes', '.notes {}');
    install.call(state, 'clock', '.clock {}');
    assert.deepEqual(adopted.map((sheet) => sheet.css), ['.notes {}', '.clock {}']);
});

test('disposing while it loads discards the late instance; dispose is idempotent', async () => {
    const fake = fakeModule();
    const host = createAppHost({ modules: { notes: fake.load } });
    const pending = host.ensure('notes');
    assert.equal(host.dispose('notes'), true);
    fake.imports[0].resolve(fake.module);
    await assert.rejects(pending, AppDisposedError);
    assert.deepEqual(fake.log, [], 'the module was never instantiated for a retired load');
    // A new open starts a fresh instance.
    const next = host.ensure('notes');
    fake.imports[1].resolve(fake.module);
    await next;
    host.mount('notes', { id: 'view' });
    assert.equal(host.dispose('notes'), true);
    assert.equal(host.dispose('notes'), false);
    assert.deepEqual(fake.log, ['create:1', 'mount:1:view', 'dispose:1']);
    assert.equal(host.state('notes'), 'unloaded');
});

test('mount happens once per root; visibility reaches a mounted app once per change', async () => {
    const fake = fakeModule();
    let shellVisible = true;
    const host = createAppHost({ modules: { notes: fake.load }, makeContext: () => ({ isVisible: () => shellVisible }) });
    host.setVisibility('notes', true, 'shell'); // reported before it exists: remembered, not delivered
    const pending = host.ensure('notes');
    fake.imports[0].resolve(fake.module);
    const controller = await pending;
    const root = { id: 'view' };
    assert.equal(host.mount('notes', root), true);
    assert.equal(host.mount('notes', root), false);
    host.setVisibility('notes', true, 'open');
    host.setVisibility('notes', true, 'shell');
    host.setVisibility('notes', false, 'minimized');
    assert.deepEqual(fake.log, ['create:1', 'mount:1:view', 'visible:1:true', 'visible:1:false']);
    // The app asks the shell live, and a retired instance always reads as hidden.
    assert.equal(controller.context.isVisible(), true);
    shellVisible = false;
    assert.equal(controller.context.isVisible(), false);
    shellVisible = true;
    host.dispose('notes');
    assert.equal(controller.context.isVisible(), false);
    assert.equal(controller.context.signal.aborted, true);
});

/** The shell's real methods over a minimal panel, a real host and the registry. */
function shellHarness({ modules }) {
    const views = {};
    const viewFor = (id) => (views[id] ||= {
        id: `view-${id}`, status: null,
        querySelector(selector) { return selector === ':scope > .os-app-status' ? this.status : null; },
        prepend(node) { this.status = node; node.remove = () => { this.status = null; }; },
    });
    const document = { createElement: () => ({ dataset: {}, setAttribute(k, v) { this[k] = v; }, innerHTML: '' }) };
    const visible = new Set();
    const state = {
        panel: { querySelector: (selector) => viewFor(selector.match(/data-app-view="([^"]+)"/)?.[1]) },
        _escape: String, _escapeAttr: String,
        _hasVisibleAppSurface: (id) => visible.has(id),
        _appAdapters: {},
    };
    const deps = { OS_APPS, document, console: { warn() {} } };
    for (const name of ['_loadApp', '_showModuleApp', '_mountModuleApp', '_renderAppStatus', '_retryApp', '_navigateApp', '_syncModuleVisibility', '_appContext']) {
        state[name] = method(name, deps);
    }
    state._appHost = createAppHost({ modules, makeContext: (id) => state._appContext(id) });
    return { state, views, visible, viewFor };
}

test('opening Automatizaciones: loading, one mount, refresh, then visible; reopening reuses it', async () => {
    const fake = fakeModule();
    const { state, visible, viewFor } = shellHarness({ modules: { automations: fake.load } });
    visible.add('automations');
    state._loadApp('automations', { opening: true });
    state._loadApp('automations', { opening: true }); // a second open while it loads
    await flush();
    assert.equal(viewFor('automations').status.dataset.kind, 'loading');
    assert.equal(viewFor('automations').status.role, 'status');
    fake.imports[0].resolve(fake.module);
    await flush();
    assert.equal(viewFor('automations').status, null, 'the loading note is gone once mounted');
    assert.deepEqual(fake.log, [
        'create:1', 'mount:1:view-automations',
        'refresh:1:false:true', 'visible:1:true', 'refresh:1:false:true',
    ]);
    // Closing is not disposing: the next open reuses the same instance, no new import. It
    // refreshes at once, before the shell's visibility sync, so the app reads Core only once.
    visible.delete('automations');
    state._syncModuleVisibility();
    visible.add('automations');
    state._loadApp('automations', { opening: true });
    assert.deepEqual(fake.log.slice(5), ['visible:1:false', 'refresh:1:false:true', 'visible:1:true'], 'synchronous, no await');
    state._syncModuleVisibility(); // what _openWindow / _restoreWindow do next
    await flush();
    assert.equal(fake.imports.length, 1);
    assert.equal(fake.created(), 1);
    assert.equal(fake.log.length, 8, 'the sync after it adds nothing');
});

test('a window restored while the page was hidden loads once it can be seen (F10.4)', async () => {
    const fake = fakeModule();
    const { state, visible } = shellHarness({ modules: { automations: fake.load } });
    state._syncModuleVisibility(); // restored with the page hidden: nothing loads
    assert.equal(fake.imports.length, 0);
    visible.add('automations'); // the page is shown: the same sync now loads it, once
    state._syncModuleVisibility();
    state._syncModuleVisibility();
    fake.imports[0].resolve(fake.module);
    await flush();
    assert.equal(fake.imports.length, 1);
    assert.deepEqual(fake.log, ['create:1', 'mount:1:view-automations', 'refresh:1:false:true', 'visible:1:true']);
});

test('closed while it loaded: it mounts hidden, with no late read and no window reopened', async () => {
    const fake = fakeModule();
    const { state, visible } = shellHarness({ modules: { automations: fake.load } });
    visible.add('automations');
    state._loadApp('automations', { opening: true });
    visible.delete('automations'); // the person closed the window
    state._syncModuleVisibility();
    fake.imports[0].resolve(fake.module);
    await flush();
    assert.deepEqual(fake.log, ['create:1', 'mount:1:view-automations', 'visible:1:false']);
    assert.ok(!fake.log.some((entry) => entry.startsWith('refresh')), 'no read for a window that is gone');
});

test('a failed load shows «Reintentar» in its view; retrying mounts it', async () => {
    const fake = fakeModule();
    const { state, visible, viewFor } = shellHarness({ modules: { automations: fake.load } });
    visible.add('automations');
    state._loadApp('automations', { opening: true });
    fake.imports[0].reject(new Error('Core no responde'));
    await flush();
    const status = viewFor('automations').status;
    assert.equal(status.dataset.kind, 'error');
    assert.equal(status.role, 'alert');
    assert.match(status.innerHTML, /No se pudo abrir Automatizaciones\..*data-app-retry="automations">Reintentar/);
    state._retryApp('automations');
    assert.equal(fake.imports.length, 2);
    fake.imports[1].resolve(fake.module);
    await flush();
    assert.equal(viewFor('automations').status, null);
    assert.deepEqual(fake.log, ['create:1', 'mount:1:view-automations', 'refresh:1:false:true', 'visible:1:true']);
    state._retryApp('automations'); // nothing to retry once it works
    assert.equal(fake.imports.length, 2);
});

test('a navigation waits for the app to mount (the inbox opening a rule history)', async () => {
    const fake = fakeModule();
    const { state, visible } = shellHarness({ modules: { automations: fake.load } });
    visible.add('automations');
    state._loadApp('automations', { opening: true });
    state._navigateApp('automations', { history: 'rule-1' });
    state._navigateApp('player', { history: 'x' }); // apps without a module are not navigated this way
    fake.imports[0].resolve(fake.module);
    await flush();
    assert.deepEqual(fake.log, [
        'create:1', 'mount:1:view-automations', 'refresh:1:false:true', 'visible:1:true',
        'navigate:1:{"history":"rule-1"}',
    ]);
});

test('the context an app receives is narrow: identity, visibility, opening apps and its brief', () => {
    const briefs = [{ textContent: '' }, { textContent: '' }];
    const opened = [];
    const state = {
        panel: { querySelectorAll: (selector) => (selector === '[data-app-brief="automations"]' ? briefs : []) },
        _hasVisibleAppSurface: (id) => id === 'automations',
        _openOsApp: (id) => opened.push(id),
    };
    const asked = [];
    state._appConfirm = (id, options) => { asked.push([id, options]); return Promise.resolve(true); };
    const context = method('_appContext', { OS_APPS }).call(state, 'automations');
    assert.deepEqual(Object.keys(context).sort(), ['app', 'askIris', 'confirm', 'id', 'isVisible', 'openApp', 'setBrief']);
    context.confirm({ title: 'T' });
    assert.deepEqual(asked, [['automations', { title: 'T' }]], 'the shell asks, in its own dialog');
    assert.equal(context.app.name, 'Automatizaciones');
    assert.equal(context.isVisible(), true);
    const navigated = [];
    state._navigateApp = (id, payload) => navigated.push([id, payload]);
    context.openApp('permissions');
    assert.deepEqual(navigated, [], 'no data, no navigation');
    // Like a widget, a window may open another app at a point: a copy reaches its navigate().
    const payload = { task: { id: 't1', title: 'Informe' } };
    context.openApp('enfoque', payload);
    payload.task.title = 'cambiado después';
    assert.deepEqual(navigated, [['enfoque', { task: { id: 't1', title: 'Informe' } }]]);
    assert.throws(() => context.openApp('enfoque', { run() {} }), /could not be cloned|DataCloneError/i,
                  'plain data only, refused before opening anything');
    context.setBrief('2 activas');
    assert.deepEqual(opened, ['permissions', 'enfoque']);
    assert.deepEqual(briefs.map((node) => node.textContent), ['2 activas', '2 activas']);
});

test("an app's question goes to Iris' dialog with texts and a tone only (F10.4)", async () => {
    const asked = [];
    const appConfirm = method('_appConfirm', { OS_APPS, irisConfirm: (options) => { asked.push(options); return Promise.resolve(true); } });
    assert.equal(await appConfirm.call({}, 'conexiones', { title: 'x'.repeat(300), message: 'm', tone: 'danger',
                                                            confirmLabel: 'Sí', html: '<b>', onConfirm: () => {} }), true);
    assert.deepEqual(asked[0], { title: 'x'.repeat(120), message: 'm', question: '', confirmLabel: 'Sí',
                                 cancelLabel: 'Cancelar', tone: 'danger' }, 'nothing but texts and a tone');
    await appConfirm.call({}, 'conexiones', { tone: 'otro', title: 7 });
    assert.equal(asked[1].tone, 'default');
    assert.equal(asked[1].title, 'Conexiones', "the app's name when it gives no title");
});

// The shell's module-level helper that writes the confirmation text.
const appActionMessage = new Function(`${shell.match(/^const APP_ACTION_EFFECTS = [^]*?^\}\);$/m)[0]}
${shell.match(/^function appActionMessage\([^]*?^\}$/m)[0]}
return appActionMessage;`)();

test('F09.4: an app action is confirmed with Core\'s label, then reaches the app, loading it if needed', async () => {
    const asked = [];
    let say = true;
    const deps = { OS_APPS, appActionMessage, irisConfirm: async (options) => { asked.push(options.message); return say; } };
    const intents = [];
    const loads = [];
    const controller = {
        handleIntent: async (intent) => { intents.push(intent); return { status: 'ok', message: 'Hecho.', data: { rule: 'r1' } }; },
    };
    const state = { _showModuleApp: async (id) => { loads.push(id); return controller; } };
    const handle = method('handleAppIntent', deps);
    const intent = (extra = {}) => ({
        request_id: 'req-1', app: 'automations', action: 'pause_rule', args: { rule: 'r1' },
        operation: { label: 'pausar una regla' }, permission: { requires_confirmation: true }, ...extra,
    });

    const done = await handle.call(state, intent());
    assert.deepEqual(asked, ['IRIS quiere pausar una regla en Automatizaciones.']);
    assert.deepEqual(loads, ['automations'], 'the app loads on demand');
    assert.deepEqual(intents, [{ action: 'pause_rule', args: { rule: 'r1' }, request_id: 'req-1' }]);
    assert.deepEqual(done, { schema: 'iris.app-result/v1', request_id: 'req-1', status: 'ok', message: 'Hecho.', data: { rule: 'r1', permission_confirmed: true } });

    say = false;
    const refused = await handle.call(state, intent());
    assert.equal(refused.status, 'denied');
    assert.deepEqual(refused.data, { permission_confirmed: false });
    assert.equal(intents.length, 1, 'a «no» never reaches the app');

    // Core runs it itself: the surface only asks.
    say = true;
    const confirmOnly = await handle.call(state, intent({ confirm_only: true, app: 'mi-servicio' }));
    assert.deepEqual([confirmOnly.status, confirmOnly.data], ['ok', { permission_confirmed: true }]);
    assert.equal(intents.length, 1);
    assert.match(asked.at(-1), /en mi-servicio\.$/, 'an app without a window here is still named');

    // Allowed without asking: no dialog, no confirmation flag.
    const allowed = await handle.call(state, intent({ permission: { requires_confirmation: false } }));
    assert.equal(asked.length, 3);
    assert.deepEqual(allowed.data, { rule: 'r1' });

    const unknown = await handle.call(state, intent({ app: 'correo', permission: {} }));
    assert.equal(unknown.status, 'unavailable');
    const legacy = await handle.call(state, intent({ app: 'player', permission: {} }));
    assert.equal(legacy.status, 'unavailable', 'shell views do not take app actions');
    controller.handleIntent = async () => ({ status: 'done!', data: 'nope' });
    const odd = await handle.call(state, intent({ permission: {} }));
    assert.deepEqual([odd.status, odd.data], ['error', {}], 'an invented status is an error');
    delete controller.handleIntent;
    const silent = await handle.call(state, intent({ permission: {} }));
    assert.match(silent.message, /no acepta acciones de Iris/);
});

test('the confirmation names the object and the effect, so two requests never look the same (Codex A03)', async () => {
    const asked = [];
    const deps = { OS_APPS, appActionMessage, irisConfirm: async (options) => { asked.push(options.message); return false; } };
    const handle = method('handleAppIntent', deps);
    const intent = (summary, effect) => ({
        request_id: 'r', app: 'automations', action: 'pause_rule', args: {},
        operation: { label: 'pausar una regla', summary, effect }, permission: { requires_confirmation: true },
    });
    await handle.call({}, intent('Regla: «Mañana»', 'local'));
    await handle.call({}, intent('Regla: «Todos los días»', 'local'));
    await handle.call({}, intent('', 'external'));
    assert.deepEqual(asked, [
        'IRIS quiere pausar una regla en Automatizaciones. Regla: «Mañana». Cambia algo en este equipo.',
        'IRIS quiere pausar una regla en Automatizaciones. Regla: «Todos los días». Cambia algo en este equipo.',
        'IRIS quiere pausar una regla en Automatizaciones. Actúa fuera de este equipo.',
    ]);
    // Player's own dialog shows the same detail.
    const playerAsked = [];
    const player = method('handlePlayerIntent', { appActionMessage, irisConfirm: async (options) => { playerAsked.push(options.message); return false; } });
    await player.call({ _playerApp: { handleIntent() {} } }, { request_id: 'p', permission: { requires_confirmation: true }, operation: { summary: 'Volumen al 35 %', effect: 'local' } });
    assert.deepEqual(playerAsked, ['IRIS quiere controlar el reproductor local una vez. Volumen al 35 %. Cambia algo en este equipo.']);
});

test('closing a window never disposes an app: Player keeps playing and module apps keep state', () => {
    for (const name of ['_closeWindow', '_minimizeWindow', 'close']) {
        assert.doesNotMatch(method(name).toString(), /dispose/, `${name} does not dispose`);
    }
    assert.doesNotMatch(method('_createAppAdapters').toString(), /player:[^\n]*(?:stop|pause|dispose)/);
    // The panel closing reports «hidden» to the apps with their own module.
    assert.match(method('close').toString(), /this\._open = false;\s*this\._syncModuleVisibility\(\);/);
});


test('an app that fails to mount shows its error, and «Reintentar» really retries (Codex H01)', async () => {
    let attempts = 0;
    let disposed = 0;
    const controller = {
        mount() { attempts += 1; if (attempts === 1) throw new Error('render failed'); },
        dispose() { disposed += 1; },
        refresh() { throw new Error('refresh failed'); },
    };
    const { state, visible, viewFor } = shellHarness({ modules: { automations: async () => ({ createApp: () => controller }) } });
    visible.add('automations');
    assert.equal(await state._showModuleApp('automations'), null, 'resolves, never throws to the shell');
    assert.equal(state._appHost.state('automations'), 'error');
    assert.equal(disposed, 1, 'the half-made instance is retired');
    assert.equal(viewFor('automations').status.dataset.kind, 'error');
    // The next open does not throw either; «Reintentar» builds a new instance that mounts.
    assert.doesNotThrow(() => state._loadApp('automations', { opening: true }));
    await flush();
    state._retryApp('automations');
    await flush();
    assert.equal(attempts, 2);
    assert.equal(state._appHost.state('automations'), 'ready');
    assert.equal(viewFor('automations').status, null);
    // A refresh that throws stays in the app.
    assert.doesNotThrow(() => state._loadApp('automations', { opening: true }));
});

async function automationsVisibility(back) {
    // The real AutomationApp, a fake Core with one active rule and deterministic timers.
    const view = { id: 'view', querySelector() { return null; } };
    const timers = new Map();
    let timerId = 0;
    let reads = 0;
    const transitions = [];
    const document = { visibilityState: 'visible', createElement: () => ({ dataset: {}, setAttribute() {}, innerHTML: '' }) };
    const state = {
        _open: true, _activeTab: 'desktop', _desktopMode: 'home',
        _osWindows: new Map([['automations', { el: { isConnected: true }, minimized: false }]]),
        panel: { dataset: {}, querySelector: () => view, querySelectorAll: () => [] },
        _syncCameraPreview() {}, _renderDesktopHome() {}, _playDesktopTransition() {},
        _syncMissionPolling() {}, _syncAsyncAgentsPolling() {}, _appAdapters: { settings: { load() {} } },
    };
    const deps = { OS_APPS, document, console: { warn() {} } };
    for (const name of ['_setTab', '_setDesktopMode', '_hasVisibleAppSurface', '_loadActiveTab', '_loadApp', '_syncModuleVisibility', '_syncAppPolling']) {
        state[name] = method(name, deps);
    }
    const app = new AutomationApp({
        root: null, isVisible: () => state._hasVisibleAppSurface('automations'),
        fetchImpl: async () => { reads += 1; return { ok: true, json: async () => ({
            rules: [{ id: 'a'.repeat(32), enabled: true, trigger: { source: 'iris_idle', threshold_s: 300 }, execution: { prompt: 'hola' } }], occurrences: [],
        }) }; },
        setTimer: (fn) => { timerId += 1; timers.set(timerId, fn); return timerId; },
        clearTimer: (id) => timers.delete(id),
    });
    app.root = view;
    app.render = () => {};
    state._appHost = createAppHost({ modules: { automations: async () => ({ createApp: () => ({
        mount() {}, dispose() { app.stop(); },
        setVisibility({ visible }) { transitions.push(visible); app.syncVisibility(); },
    }) }) } });
    await state._appHost.ensure('automations');
    state._appHost.mount('automations', view);
    await app.load(true);
    state._appHost.setVisibility('automations', true);
    assert.deepEqual([reads, timers.size], [1, 1]);

    const [, pendingPoll] = timers.entries().next().value;
    state._setTab('settings');
    assert.deepEqual(transitions, [true, false], 'hiding behind Ajustes is reported');
    assert.deepEqual([reads, timers.size], [1, 0], 'hidden: its refresh stops at once');
    pendingPoll(); // a poll that was already due finds it hidden: it reads nothing
    assert.deepEqual([reads, timers.size], [1, 0]);

    if (back === 'sidebar') state._setTab('desktop');
    else state._setDesktopMode('home');
    await flush();
    const result = { reads, timers: timers.size, transitions };
    app.stop();
    return result;
}

for (const back of ['sidebar', 'home']) {
    test(`Automatizaciones refreshes again after Ajustes and back by ${back} (Codex H02)`, async () => {
        const result = await automationsVisibility(back);
        assert.deepEqual(result.transitions, [true, false, true]);
        assert.equal(result.reads, 2, 'coming back reads Core once');
        assert.equal(result.timers, 1, 'and schedules its refresh again');
    });
}

test('reopening the panel tells its apps they are visible again', () => {
    assert.match(method('open').toString(), /this\._syncModuleVisibility\(\);\s*\}$/);
});

test('F17: with a function table the host finds an app installed while the desktop runs, and an update loads anew', async () => {
    const table = {};
    const host = createAppHost({ modules: () => table });
    await assert.rejects(host.ensure('demo'), /no tiene un módulo registrado/);
    const first = fakeModule();
    table.demo = first.load; // installed from Iris Store now
    const loading = host.retry('demo');
    first.imports[0].resolve(first.module);
    assert.equal(typeof (await loading).mount, 'function');
    // An update: the shell retires the instance; the next load takes the new module.
    host.dispose('demo');
    const second = fakeModule();
    table.demo = second.load;
    const again = host.ensure('demo');
    second.imports[0].resolve(second.module);
    await again;
    assert.deepEqual(first.log, ['create:1', 'dispose:1']);
    assert.equal(second.created(), 1);
});
