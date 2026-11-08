// F17: the desktop's app list = bundled catalog + apps installed from a file (Iris Store).
import test from 'node:test';
import assert from 'node:assert/strict';
import { mergeAppSources, readInstalledApps, compareInstalledApps, OS_APP_CATALOG, INSTALLED_APPS } from '../../face/js/os_app_sources.js';
import { OS_APP_CATALOG as BUNDLED } from '../../face/js/os_app_catalog.js';

const INS = 'ins_' + 'a'.repeat(32);
const file = (name) => `/api/app-packages/files/${INS}/${name}`;
const manifest = (changes = {}) => ({
    schema: 'iris.app-manifest/v1', sdkVersion: 1, id: 'demo', name: 'Demo', version: '0.1.0', description: 'App de prueba',
    icon: 'sparkle', accent: 'magenta', presentation: 'window', window: { width: 420, height: 520 },
    launcher: { visible: true, order: 910 }, ui: { entry: 'main.js', style: 'style.css' }, requestedCapabilities: [], services: [],
    widgets: [{ id: 'demo.timer', name: 'Demo', entry: 'widget.js', size: 'compact', configVersion: 1 }], ...changes,
});
const entry = (changes = {}) => ({ id: 'demo', version: '0.1.0', installed_at: '2026-10-20T10:00:00+00:00', size: 900,
    manifest: manifest(), entry: file('main.js'), style: file('style.css.js'), widgets: { 'demo.timer': { entry: file('widget.js') } }, ...changes });
const bundled = () => ({
    catalog: [manifest({ id: 'calendar', name: 'Calendario', widgets: undefined })], modules: { calendar: () => 'calendar' }, styles: {},
    widgets: { 'calendar.month': { name: 'Calendario' } }, widgetModules: {}, widgetStyles: {},
});

test('outside a page (tests, build) there are no installed apps and the bundled catalog is unchanged', async () => {
    assert.deepEqual(await readInstalledApps(), []);
    assert.deepEqual(OS_APP_CATALOG, BUNDLED);
    assert.equal(INSTALLED_APPS.size, 0);
});

test('an installed app joins the catalog, its modules load only from Core\'s package files', async () => {
    const imported = [];
    const merged = mergeAppSources(bundled(), [entry()], { importer: (url) => { imported.push(url); return url; } });
    assert.deepEqual(merged.catalog.map((app) => app.id), ['calendar', 'demo']);
    assert.equal(await merged.modules.demo(), file('main.js'));
    assert.equal(await merged.styles.demo(), file('style.css.js'));
    assert.equal(await merged.widgetModules['demo.timer'](), file('widget.js'));
    assert.deepEqual(merged.widgets['demo.timer'], { name: 'Demo', app: 'demo', size: 'compact', configVersion: 1 });
    assert.deepEqual(merged.installed.get('demo'), { version: '0.1.0', installedAt: '2026-10-20T10:00:00+00:00', size: 900, installation: file('main.js') });
    assert.equal(merged.modules.calendar(), 'calendar', 'bundled loaders stay as they were');
    assert.deepEqual(imported, [file('main.js'), file('style.css.js'), file('widget.js')]);
});

test('a package that could take over a bundled app or load code from elsewhere is left out, the rest keeps working', () => {
    const cases = {
        'bundled id': entry({ id: 'calendar', manifest: manifest({ id: 'calendar', widgets: undefined }), widgets: {} }),
        'id mismatch': entry({ id: 'other' }),
        'outside url': entry({ entry: 'https://evil.example/main.js' }),
        'other route': entry({ entry: '/face/js/hermes_activity.js' }),
        'climbing path': entry({ entry: `/api/app-packages/files/${INS}/../../x.js` }),
        'widget url': entry({ widgets: { 'demo.timer': { entry: 'data:text/javascript,alert(1)' } } }),
        'missing widget': entry({ widgets: {} }),
        'invalid manifest': entry({ manifest: manifest({ extra: true }) }),
        'shell module': entry({ manifest: manifest({ ui: { module: 'automation_app.js' }, widgets: undefined }), widgets: {} }),
    };
    for (const [name, bad] of Object.entries(cases)) {
        const warnings = [];
        const merged = mergeAppSources(bundled(), [bad, { ...entry(), id: 'demo2', manifest: manifest({ id: 'demo2', widgets: undefined }), widgets: {} }],
            { importer: (url) => url, warn: (text) => warnings.push(text) });
        assert.deepEqual(merged.catalog.map((app) => app.id), ['calendar', 'demo2'], name);
        assert.equal(warnings.length, 1, name);
        assert.equal(merged.modules.calendar(), 'calendar', name);
    }
});

test('two packages with the same id: the first wins, the second is reported', () => {
    const warnings = [];
    const merged = mergeAppSources(bundled(), [entry(), entry()], { importer: (url) => url, warn: (text) => warnings.push(text) });
    assert.deepEqual(merged.catalog.map((app) => app.id), ['calendar', 'demo']);
    assert.match(warnings[0], /ya pertenece/);
});

test('Core unavailable or answering badly means no installed apps, never a broken desktop', async () => {
    globalThis.location = { href: 'http://127.0.0.1/face/' };
    try {
        assert.deepEqual(await readInstalledApps(async () => ({ ok: false, json: async () => ({}) })), []);
        assert.deepEqual(await readInstalledApps(async () => { throw new TypeError('offline'); }), []);
        assert.deepEqual(await readInstalledApps(async () => ({ ok: true, json: async () => ({ apps: 'no' }) })), []);
        const apps = [entry()];
        assert.deepEqual(await readInstalledApps(async (url, init) => {
            assert.equal(url, '/api/app-packages'); assert.equal(init.cache, 'no-store');
            return { ok: true, json: async () => ({ apps }) };
        }), apps);
    } finally {
        delete globalThis.location;
    }
});

test('F17: a refresh tells added, removed and updated apps apart by their installation', () => {
    const app = (installation) => ({ version: '1.0.0', installation });
    const before = new Map([['a', app('ins_1')], ['b', app('ins_2')], ['c', app('ins_3')]]);
    const after = new Map([['a', app('ins_1')], ['c', app('ins_9')], ['d', app('ins_4')]]);
    assert.deepEqual(compareInstalledApps(before, after), { added: ['d'], removed: ['b'], updated: ['c'] });
    assert.deepEqual(compareInstalledApps(after, after), { added: [], removed: [], updated: [] });
});

test('F17: a strict read fails loudly, so a refresh never mistakes a failure for «every app was removed»', async () => {
    globalThis.location = { href: 'http://127.0.0.1/face/' };
    try {
        for (const fetchImpl of [async () => ({ ok: false, status: 503, json: async () => ({}) }), async () => { throw new TypeError('offline'); },
            async () => ({ ok: true, json: async () => ({ apps: 'no' }) })]) {
            await assert.rejects(readInstalledApps(fetchImpl, { strict: true }), /no pudo leer las apps instaladas/);
            assert.deepEqual(await readInstalledApps(fetchImpl), [], 'at start a failure still means only the bundled apps');
        }
    } finally {
        delete globalThis.location;
    }
});

test('every importer uses one specifier for os_app_sources.js, so the desktop shares a single list', async () => {
    const { readdir, readFile } = await import('node:fs/promises');
    const folder = new URL('../../face/js/', import.meta.url);
    const specifiers = new Set();
    for (const name of await readdir(folder)) {
        if (!name.endsWith('.js')) continue;
        for (const match of (await readFile(new URL(name, folder), 'utf8')).matchAll(/['"]\.\/os_app_sources\.js(\?v=\d+)?['"]/g)) specifiers.add(match[0]);
    }
    assert.ok(specifiers.size === 1, [...specifiers].join(', '));
});

// Last in this file: it changes the module's state for the rest of the process.
test('app.js loads the installed apps once; every importer then reads the merged list (live bindings)', async () => {
    // The same specifier as the shell's importers: another «?v=» would be another instance with its own state.
    const sources = await import('../../face/js/os_app_sources.js?v=4');
    const widgets = await import('../../face/js/os_widgets.js');
    assert.equal(widgets.WIDGET_CATALOG['demo.timer'], undefined);
    globalThis.location = { href: 'http://127.0.0.1/face/' };
    try {
        let calls = 0;
        const fetchImpl = async () => { calls++; return { ok: true, json: async () => ({ apps: [entry()] }) }; };
        await sources.loadInstalledApps(fetchImpl);
        await sources.loadInstalledApps(fetchImpl);
        assert.equal(calls, 1, 'read once per page');
        assert.deepEqual(sources.OS_APP_CATALOG.at(-1).id, 'demo');
        assert.ok(sources.INSTALLED_APPS.has('demo'));
        assert.equal(typeof sources.OS_APP_MODULES.demo, 'function');
        assert.deepEqual(widgets.WIDGET_CATALOG['demo.timer'], { name: 'Demo', app: 'demo', size: 'compact', configVersion: 1 },
            'os_widgets sees the same list without another request');
    } finally {
        delete globalThis.location;
    }
});

// After the test above (same module instance): refreshing while the desktop runs.
test('F17: Iris Store refreshes the live list without a reload; a failed refresh leaves it as it was', async () => {
    const sources = await import('../../face/js/os_app_sources.js?v=4');
    const widgets = await import('../../face/js/os_widgets.js');
    globalThis.location = { href: 'http://127.0.0.1/face/' };
    const updatedFile = (name) => `/api/app-packages/files/ins_${'b'.repeat(32)}/${name}`;
    const updated = entry({ version: '0.2.0', entry: updatedFile('main.js'), style: updatedFile('style.css.js'),
        manifest: manifest({ version: '0.2.0' }), widgets: { 'demo.timer': { entry: updatedFile('widget.js') } } });
    const other = { ...entry(), id: 'otra', manifest: manifest({ id: 'otra', widgets: undefined }), widgets: {} };
    const reply = (apps) => async () => ({ ok: true, json: async () => ({ apps }) });
    try {
        const first = await sources.reloadInstalledApps(reply([updated, other]));
        assert.deepEqual(first.changes, { added: ['otra'], removed: [], updated: ['demo'] });
        assert.equal(sources.INSTALLED_APPS.get('demo').version, '0.2.0');
        await assert.rejects(sources.reloadInstalledApps(async () => { throw new TypeError('offline'); }));
        assert.ok(sources.INSTALLED_APPS.has('otra'), 'a failed refresh keeps the current list');
        const gone = await sources.reloadInstalledApps(reply([]));
        assert.deepEqual(gone.changes, { added: [], removed: ['demo', 'otra'], updated: [] });
        assert.equal(sources.OS_APP_CATALOG.some((app) => app.id === 'demo'), false);
        assert.equal(widgets.WIDGET_CATALOG['demo.timer'], undefined, 'the widget gallery loses the removed widget too');
    } finally {
        delete globalThis.location;
    }
});
