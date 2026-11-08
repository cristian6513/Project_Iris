import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { DesktopHome, HomePreferences, homeLayout, orderedHomeRefs, hidingDescription, drawerApps } from '../../face/js/os_desktop_home.js';
const initial = () => ({ schema: 'iris.desktop-home/v1', revision: 0, shortcuts: ['theme', 'player'], hidden_apps: [], order: [] });
function setup(route) {
    let saved = initial(); const calls = [];
    const model = new HomePreferences({ fetchImpl: async (url, init) => {
        calls.push({ url, ...init });
        if (route) return route(init, calls);
        if (init.method === 'PUT') saved = { ...JSON.parse(init.body), revision: saved.revision + 1 };
        return { ok: true, json: async () => structuredClone(saved) };
    } });
    return { model, calls };
}
test('pin is unique; hide never removes a shortcut and empty home remains empty after reload', async () => {
    const { model, calls } = setup(); await model.load();
    await model.pin('calendar'); await model.pin('calendar');
    assert.equal(calls.filter((call) => call.method === 'PUT').length, 1);
    await model.hide('calendar', true); assert.ok(model.layout.shortcuts.includes('calendar'));
    for (const id of [...model.layout.shortcuts]) await model.remove(id);
    await model.load(); assert.deepEqual(model.layout.shortcuts, []);
    assert.deepEqual(model.layout.hidden_apps, ['calendar']);
    assert.ok(calls.every((call) => call.url === '/api/desktop/home'));
});
test('a saved interleaving orders the real widget IDs with shortcuts, ignoring old missing refs', async () => {
    const { model } = setup(); await model.load(); const id = 'wdg_' + '1'.repeat(32);
    await model.move(`widget:${id}`, -1, [id]);
    assert.deepEqual(orderedHomeRefs(model.layout, [id]), ['app:theme', `widget:${id}`, 'app:player']);
    assert.deepEqual(orderedHomeRefs(model.layout, []), ['app:theme', 'app:player']);
});
test('a revision conflict reloads actual preferences and never automatically repeats the write', async () => {
    const actual = { ...initial(), revision: 5, shortcuts: [] };
    const { model, calls } = setup(async (init, calls) => init.method === 'PUT'
        ? { ok: false, status: 409, json: async () => ({ error: { message: 'Cambió en otra ventana' } }) }
        : { ok: true, json: async () => calls.length === 1 ? initial() : actual });
    await model.load(); assert.equal(await model.pin('calendar'), false);
    assert.deepEqual(model.layout.shortcuts, []); assert.equal(model.layout.revision, 5);
    assert.match(model.error, /otra ventana/); assert.equal(calls.filter((call) => call.method === 'PUT').length, 1);
});
test('corrupt reads never invent defaults or allow writes', async () => {
    const { model, calls } = setup(async () => ({ ok: true, json: async () => ({ schema: 'future/v2' }) }));
    await model.load(); await model.pin('calendar');
    assert.equal(model.layout, null); assert.match(model.error, /compatible/); assert.equal(calls.length, 1);
});
test('busy state refuses another edit and reload until the authoritative save finishes', async () => {
    let finish; const { model, calls } = setup(async (init) => init.method === 'GET'
        ? { ok: true, json: async () => initial() }
        : new Promise((resolve) => { finish = () => resolve({ ok: true, json: async () => ({ ...JSON.parse(init.body), revision: 1 }) }); }));
    await model.load(); const pending = model.pin('calendar');
    assert.equal(model.busy, true); await model.hide('mail', true); await model.load(); assert.equal(calls.length, 2);
    assert.ok(!model.layout.shortcuts.includes('calendar')); finish(); await pending;
    assert.ok(model.layout.shortcuts.includes('calendar')); assert.equal(model.busy, false);
});
test('dispose discards a late read and aborts the request', async () => {
    let finish; const { model, calls } = setup(() => new Promise((resolve) => { finish = resolve; }));
    const pending = model.load(); model.dispose(); finish({ ok: true, json: async () => initial() }); await pending;
    assert.equal(model.layout, null); assert.equal(calls[0].signal.aborted, true);
});
test('malformed, duplicate and future fields cannot be interpreted as home entries', () => {
    for (const change of [{ shortcuts: ['../path'] }, { shortcuts: ['theme', 'theme'] }, { hidden_apps: [true] },
        { order: ['script:run'] }, { order: ['app:mail'] }, { revision: true }]) assert.throws(() => homeLayout({ ...initial(), ...change }));
});
test('hiding explains continued optional work; app manager is always recoverable from Settings', async () => {
    assert.match(hidingDescription('mail'), /que tengas activados/);
    assert.match(hidingDescription('calendar'), /continuarán/);
    assert.match(hidingDescription('player'), /sigue funcionando/);
    const source = await readFile(new URL('../../face/js/hermes_activity.js', import.meta.url), 'utf8');
    assert.match(source, /data-settings-open-app="app-manager"/);
    const manager = await readFile(new URL('../../face/js/os_app_manager.js', import.meta.url), 'utf8');
    assert.match(manager, /Incluida con Iris/); assert.match(manager, /Gestionar cuentas en Conexiones/);
    assert.doesNotMatch(manager, /fetch\(|\.uninstall\(|\.disconnect\(/);
});

test('drawer search prioritizes names, ignores accents and hides icons without changing the registry', () => {
    const entries = [
        { id: 'conexiones', name: 'Conexiones', description: 'Calendario y Correo' },
        { id: 'calendar', name: 'Calendario', description: 'Tu agenda' },
        { id: 'camera', name: 'Cámara', description: 'Vista local' },
    ];
    const registry = { launcher: () => entries.slice() };
    assert.deepEqual(drawerApps(registry, initial(), 'calendario').map((app) => app.id), ['calendar', 'conexiones']);
    assert.deepEqual(drawerApps(registry, initial(), 'camara').map((app) => app.id), ['camera']);
    assert.deepEqual(drawerApps(registry, { ...initial(), hidden_apps: ['calendar'] }, 'calendario').map((app) => app.id), ['conexiones']);
    assert.equal(entries.length, 3);
});

test('keyboard focus follows the other direction at the end of the ordering range', () => {
    const previous = { disabled: true, dataset: { homeFocus: '-1:app:theme' }, focus() { throw new Error('disabled'); } };
    let focused = '';
    const next = { disabled: false, dataset: { homeFocus: '1:app:theme' }, focus() { focused = 'next'; } };
    DesktopHome.prototype.restoreFocus.call({ root: { querySelectorAll: () => [previous, next] }, grid: { querySelectorAll: () => [] } }, '-1:app:theme');
    assert.equal(focused, 'next');
});

test('an asynchronous save remembers the ordering control while it is disabled and restores it on completion', () => {
    let active = '-1:app:player', restored = '';
    const view = { model: { layout: { ...initial(), shortcuts: [] }, busy: true }, disabled: true, editing: true,
        editButton: { setAttribute() {} }, message: { replaceChildren() {} }, shortcutNodes: new Map(),
        focusKey: () => active, syncWidgets() {}, renderDrawer() {}, restoreFocus(key) { restored = key; } };
    DesktopHome.prototype.render.call(view);
    assert.equal(view.pendingFocus, '-1:app:player'); assert.equal(restored, '');
    active = ''; view.disabled = false; view.model.busy = false;
    DesktopHome.prototype.render.call(view);
    assert.equal(restored, '-1:app:player'); assert.equal(view.pendingFocus, '');
});

test('the real shell passes the shared home model only to its reviewed app manager', async () => {
    const source = await readFile(new URL('../../face/js/hermes_activity.js', import.meta.url), 'utf8');
    const code = source.match(/^    _appContext\([^]*?^    \}/m)[0];
    const registry = { get: (id) => ({ id }) }, model = {}, installed = { has: (id) => id === 'enfoque' };
    const notices = { load() {}, set() {} }, trust = { list: () => [] };
    const method = new Function('OS_APPS', 'INSTALLED', 'osIconSvg', 'appNoticeModes', 'askIrisTrust', `return ({${code}})._appContext`)(
        registry, installed, (name) => `<svg data-icon="${name}">`, () => notices, trust);
    const calls = [];
    const view = { _homePreferences: model, refreshInstalledApps: () => calls.push('refresh'), _appInUse: (id) => calls.push(`in-use:${id}`) };
    assert.equal(method.call(view, 'app-manager').desktopHome, model);
    assert.equal(method.call(view, 'app-manager').appRegistry, registry);
    assert.equal(method.call(view, 'app-manager').installedApps, installed);
    assert.equal(method.call(view, 'calendar').desktopHome, undefined);
    // Each installed app's notices and «Avisar a Iris» trust are managed only there.
    assert.equal(method.call(view, 'app-manager').appNotices, notices);
    assert.equal(method.call(view, 'app-manager').askIrisTrust, trust);
    for (const key of ['appNotices', 'askIrisTrust']) assert.equal(method.call(view, 'calendar')[key], undefined, key);
    // F17: only Iris Store learns the installed apps, gets the shell's icon set and may update the desktop.
    const store = method.call(view, 'store');
    assert.equal(store.installedApps, installed);
    assert.equal(store.icon('download'), '<svg data-icon="download">');
    store.refreshApps(); store.appInUse('enfoque');
    assert.deepEqual(calls, ['refresh', 'in-use:enfoque']);
    for (const key of ['installedApps', 'icon', 'refreshApps', 'appInUse', 'reload']) assert.equal(method.call(view, 'calendar')[key], undefined, key);
});

test('F17: the shell takes Store changes in live — new views, retired apps and their styles, reopened updates', async () => {
    const source = await readFile(new URL('../../face/js/hermes_activity.js', import.meta.url), 'utf8');
    const grab = (name) => source.match(new RegExp(`^    (?:async )?${name}\\([^]*?^    \\}`, 'm'))[0];
    const ids = new Set(['demo', 'nuevo']);
    const registry = { hasOwnCode: (id) => ids.has(id), get: (id) => (ids.has(id) ? { id } : null) };
    const log = [];
    let changes = { added: ['nuevo'], removed: ['viejo'], updated: ['demo'] };
    const methods = new Function('OS_APPS', 'reloadInstalledApps', 'refreshAppCatalog', 'document', 'CSSStyleSheet',
        `return ({${grab('refreshInstalledApps')}, ${grab('_retireInstalledApp')}, ${grab('_ensureAppView')}})`)(
        registry, async () => { log.push('reload'); if (!changes) throw new Error('offline'); return { changes }; }, () => log.push('catalog'),
        { createElement: () => ({ dataset: {} }), adoptedStyleSheets: [] }, undefined);
    const removedView = { replaceChildren: () => log.push('clear:viejo'), remove: () => log.push('view-removed:viejo') };
    const demoView = { replaceChildren: () => log.push('clear:demo'), remove: () => log.push('view-removed:demo') };
    const container = { append: (view) => log.push(`view-added:${view.dataset.appView}`) };
    const shell = { ...methods, _osWindows: new Map([['viejo', {}], ['demo', {}]]), _windowViewAnchors: new Map([['viejo', {}]]),
        _windowLayout: { viejo: { open: true }, demo: { open: true } }, _saveWindowLayout: () => log.push('layout-saved'),
        _appStyles: new Map([['viejo', { remove: () => log.push('style-removed:viejo') }], ['demo', { remove: () => log.push('style-removed:demo') }]]),
        panel: { querySelector: (selector) => ({ '[data-app-view="viejo"]': removedView, '[data-app-view="demo"]': demoView,
            '[data-workbench-view="desktop"]': { parentElement: container } })[selector] || null },
        _closeWindow: (id) => { log.push(`closed:${id}`); shell._osWindows.delete(id); },
        _appHost: { dispose: (id) => log.push(`disposed:${id}`), get: () => null },
        _widgets: { forgetApps: async (ids) => log.push(`widgets-forget:${ids}`), purgeAppStyles: (ids) => log.push(`purge:${ids}`),
            restoreApps: (ids) => log.push(`widgets-restore:${ids}`), remountApp: (id) => log.push(`remount:${id}`), refreshCatalog: () => log.push('gallery') },
        _homePreferences: { forgetApps: async (ids) => log.push(`home-forget:${ids}`), restoreApps: (ids) => log.push(`home-restore:${ids}`) },
        _desktopHome: { render: () => log.push('home-render') },
        _renderDesktopHome: () => {}, _loadApp: (id) => log.push(`reopen:${id}`) };
    assert.deepEqual(await shell.refreshInstalledApps(), { ok: true, added: ['nuevo'], removed: ['viejo'], updated: ['demo'] });
    for (const step of ['closed:viejo', 'disposed:viejo', 'view-removed:viejo', 'style-removed:viejo', 'disposed:demo', 'clear:demo',
        'style-removed:demo', 'view-added:nuevo', 'purge:viejo,demo,nuevo', 'widgets-restore:nuevo', 'home-restore:nuevo',
        'widgets-forget:viejo', 'home-forget:viejo', 'remount:demo', 'gallery', 'reopen:demo']) assert.ok(log.includes(step), step);
    assert.ok(log.indexOf('purge:viejo,demo,nuevo') < log.indexOf('remount:demo'), 'old widget styles go before the new version mounts (Codex L01)');
    assert.ok(!log.includes('closed:demo') && !log.includes('view-removed:demo'), 'an update keeps its window and view');
    assert.ok(log.indexOf('catalog') > log.indexOf('reload') && log.indexOf('catalog') < log.indexOf('view-added:nuevo'),
        'the registry is rebuilt from the new list before any app is added or reopened');
    assert.equal(shell._windowLayout.viejo, undefined); assert.ok(shell._windowLayout.demo);
    assert.ok(log.indexOf('reopen:demo') > log.indexOf('remount:demo'), 'the updated app reopens after everything is in place');
    // Core unreadable: nothing changes and the Store can offer «Reintentar».
    changes = null; log.length = 0;
    const failed = await shell.refreshInstalledApps();
    assert.equal(failed.ok, false); assert.deepEqual(log, ['reload']);
});

test('F17: a Store reload comes back once to the desktop and the Store, never on a later load', async () => {
    const source = await readFile(new URL('../../face/js/hermes_activity.js', import.meta.url), 'utf8');
    const grab = (name) => source.match(new RegExp(`^    ${name}\\([^]*?^    \\}`, 'm'))[0];
    const key = source.match(/const OS_RELOAD_KEY = '([^']+)'/)[1];
    const store = new Map(), reloads = [], opened = [];
    const realStorage = globalThis.sessionStorage, realLocation = globalThis.location;
    globalThis.sessionStorage = { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, v), removeItem: (k) => store.delete(k) };
    globalThis.location = { reload: () => reloads.push(true) };
    try {
        const methods = new Function('OS_APPS', 'OS_RELOAD_KEY', `return ({${grab('_reloadDesktop')}, ${grab('resumeAfterReload')}})`)(
            { get: (id) => (id === 'store' ? { id } : null) }, key);
        const shell = { ...methods, open: () => opened.push('desktop'), _openOsApp: (id) => opened.push(id) };
        shell._reloadDesktop('store');
        assert.deepEqual(reloads, [true]);
        assert.equal(shell.resumeAfterReload(), true);
        assert.deepEqual(opened, ['desktop', 'store']);
        assert.equal(shell.resumeAfterReload(), false, 'one-shot: an ordinary later load stays in the room');
        assert.deepEqual(opened, ['desktop', 'store']);
    } finally {
        globalThis.sessionStorage = realStorage; globalThis.location = realLocation;
    }
});

test('F17 (Codex L02): uninstalling during a home save drops the shortcut at once and Core is read once the save ends', async () => {
    let hold = null, uninstalled = false, reads = 0;
    const withDemo = { ...initial(), shortcuts: ['theme', 'demo'], order: ['app:demo', 'app:theme'] };
    const { model } = setup(async (init) => {
        if (init.method === 'PUT') {
            const accepted = { ...JSON.parse(init.body), revision: 1 }; // saved before the uninstall, answered late
            await new Promise((resolve) => { hold = resolve; });
            return { ok: true, json: async () => accepted };
        }
        reads += 1;
        return { ok: true, json: async () => (uninstalled ? { ...initial(), shortcuts: ['theme', 'player'], revision: 2 } : withDemo) };
    });
    await model.load();
    const pending = model.pin('player');
    assert.equal(model.busy, true);
    uninstalled = true;
    let read = null;
    const forgotten = model.forgetApps(['demo']).then((value) => { read = value; });
    assert.deepEqual(model.layout.shortcuts, ['theme'], 'gone at once');
    assert.deepEqual(model.layout.order, ['app:theme']);
    hold(); await pending;
    assert.ok(!model.layout.shortcuts.includes('demo'), 'the late save reply is filtered');
    await forgotten;
    assert.equal(read, true);
    assert.deepEqual(model.layout.shortcuts, ['theme', 'player'], 'Core\'s own home, read after the save');
    assert.equal(model.layout.revision, 2);
    assert.equal(reads, 2, 'one read at start, one after the save');
    assert.equal(model.retiredApps.size, 0);
});
