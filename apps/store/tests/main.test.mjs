// Iris Store (F17). Run with `node --test apps/store/tests/*.test.mjs`. A tiny DOM and a fake Core:
// no real package is installed and nothing leaves the test.
import { test } from 'node:test';
import assert from 'node:assert/strict';

class Node {
    constructor(tag) {
        Object.assign(this, { tag, children: [], listeners: {}, attributes: {}, dataset: {}, textContent: '', className: '', value: '', disabled: false });
        this.classList = { add: (name) => { this.className += ` ${name}`; }, remove: (name) => { this.className = this.className.replace(` ${name}`, ''); } };
    }
    append(...nodes) { this.children.push(...nodes); }
    replaceChildren(...nodes) { this.children = nodes; }
    setAttribute(name, value) { this.attributes[name] = String(value); }
    getAttribute(name) { return this.attributes[name] ?? null; }
    addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
    async fire(type, event = {}) { for (const fn of this.listeners[type] || []) await fn({ type, target: this, preventDefault() {}, ...event }); }
    click() { if (!this.disabled) return this.fire('click'); }
    focus() { globalThis.document.activeElement = this; }
    all() { return [this, ...this.children.flatMap((child) => child.all())]; }
    find(part) { return this.all().find((node) => node.dataset.part === part); }
    text() { return this.all().map((node) => node.textContent).join(' '); }
    querySelectorAll(selector) { return selector === 'button' ? this.all().filter((node) => node.tag === 'button') : []; }
    querySelector(selector) { const part = /^\[data-part="(.+)"\]$/.exec(selector)?.[1]; return part ? this.find(part) || null : null; }
}
globalThis.document = { activeElement: null, createElement: (tag) => new Node(tag) };

const { createApp, formatSize, folderEntries, toBase64, WARNING, MAX_FOLDER_FILES } = await import('../ui/main.js');

const APP = { id: 'demo', name: 'Demo', version: '0.1.0', description: 'App de prueba', icon: 'sparkle', accent: 'magenta',
    widgets: [{ id: 'demo.timer', name: 'Demo' }], size: 2048, files: 6, ignored: ['README.md'], risks: [], current: null };
const INSTALLED = { id: 'demo', version: '0.1.0', installed_at: '2026-10-20T10:00:00+00:00', size: 2048,
    manifest: { id: 'demo', name: 'Demo', icon: 'sparkle', widgets: [{ id: 'demo.timer', name: 'Demo' }] }, entry: '/api/app-packages/files/ins_x/main.js', widgets: {} };

function core(routes = {}) {
    const calls = [];
    let installed = [];
    globalThis.fetch = async (url, init = {}) => {
        const method = init.method || 'GET';
        calls.push({ url, method, init });
        const custom = routes[`${method} ${url}`];
        if (custom) return custom(init);
        const reply = (status, data) => ({ ok: status < 300, status, json: async () => data });
        if (method === 'GET' && url === '/api/app-packages') return reply(200, { apps: installed });
        if (method === 'POST' && url === '/api/app-packages/inspect') return reply(200, { token: 't'.repeat(43), app: APP });
        if (method === 'POST' && url === '/api/app-packages/install') { installed = [INSTALLED]; return reply(200, { installed: INSTALLED }); }
        if (method === 'DELETE' && url === '/api/app-packages/demo') { installed = []; return reply(200, { removed: { id: 'demo', version: '0.1.0' }, desktop_cleaned: true }); }
        return reply(404, { detail: 'No encontrado' });
    };
    return calls;
}

async function mounted(context = {}) {
    const asked = [];
    const app = createApp({ confirm: async (options) => { asked.push(options); return true; }, icon: (name) => `<svg data-icon="${name}"></svg>`, ...context });
    const root = new Node('div');
    app.mount(root);
    await app.refresh({ opening: true });
    return { app, root, asked };
}

const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

test('SDK 2 review names Python, skill and operations, and the confirmation includes them', async () => {
    const extension = { python: 'main.py', skill: 'iris-app-demo', operations: [{ action: 'status', label: 'Consultar estado', effect: 'read' }] };
    core({ 'POST /api/app-packages/inspect': async () => ({ ok: true, json: async () => ({ token: 't'.repeat(43), app: { ...APP, extension } }) }) });
    const { root, asked } = await mounted();
    await chooseZip(root);
    assert.match(root.find('components').text(), /Python/);
    assert.match(root.find('component-skill').textContent, /Hermes/);
    assert.match(root.find('component-actions').textContent, /Consultar estado/);
    await root.find('install').click(); await settle();
    assert.match(asked[0].message, /código Python/);
    assert.match(asked[0].message, /skill/);
});

test('deleting Python app data is opt-in and the destructive confirmation names it', async () => {
    const app = { ...INSTALLED, manifest: { ...INSTALLED.manifest, extension: { python: 'main.py' } } };
    const calls = core({ 'GET /api/app-packages': async () => ({ ok: true, json: async () => ({ apps: [app] }) }),
        'DELETE /api/app-packages/demo?keep_data=false': async () => ({ ok: true, json: async () => ({ desktop_cleaned: true }) }) });
    const { root, asked } = await mounted();
    assert.equal(root.find('remove-data-demo').checked, false);
    root.find('remove-data-demo').checked = true;
    await root.find('remove-data-demo').fire('change');
    await root.find('uninstall-demo').click(); await settle();
    assert.match(asked[0].message, /eliminarán también sus datos/);
    assert.equal(calls.filter(call => call.method === 'DELETE')[0].url, '/api/app-packages/demo?keep_data=false');
});

async function chooseZip(root, file = { name: 'demo.zip', size: 10 }) {
    const input = root.find('pick-zip-input');
    input.files = [file];
    await input.fire('change');
    await settle();
}

function folderFile(path, text = 'x') {
    return { webkitRelativePath: path, name: path.split('/').at(-1), size: text.length,
        arrayBuffer: async () => new TextEncoder().encode(text).buffer };
}

function fileEntry(name, text = 'x') {
    return { name, isFile: true, file: (resolve) => resolve(folderFile(name, text)) };
}

function directoryEntry(name, entries, visited = []) {
    return { name, isDirectory: true, createReader() {
        visited.push(name);
        let offset = 0;
        return { readEntries(resolve) {
            const batch = entries.slice(offset, offset + 100);
            offset += batch.length;
            resolve(batch);
        } };
    } };
}

async function dropFolder(root, entry) {
    await root.find('drop').fire('drop', { dataTransfer: { items: [{ kind: 'file', webkitGetAsEntry: () => entry }], files: [] } });
    await settle();
}

test('helpers: sizes in Spanish, folders without hidden or dependency files, base64 of bytes', () => {
    assert.equal(formatSize(900), '900 B');
    assert.equal(formatSize(2048), '2 KB');
    assert.equal(formatSize(3 * 1024 * 1024), '3 MB');
    const files = ['demo/app.json', 'demo/.git/config', 'demo/node_modules/x/y.js', '__MACOSX/demo/._app.json', 'demo/ui/main.js']
        .map((path) => ({ webkitRelativePath: path, name: path.split('/').at(-1) }));
    assert.deepEqual(folderEntries(files).map((entry) => entry.path), ['demo/app.json', 'demo/ui/main.js']);
    assert.equal(toBase64(new Uint8Array([104, 111, 108, 97])), 'aG9sYQ==');
});

test('a .zip is reviewed with the warning, and installs only after the person says yes in Iris\' dialog', async () => {
    const calls = core();
    const { root, asked } = await mounted();
    assert.match(root.find('installed-empty').textContent, /Todavía no/);
    await chooseZip(root);
    const inspect = calls.find((call) => call.url.endsWith('/inspect'));
    assert.equal(inspect.init.headers['Content-Type'], 'application/zip');
    assert.equal(root.find('review-title').textContent, 'Demo');
    assert.equal(globalThis.document.activeElement, root.find('review-title'), 'the review takes the focus');
    assert.match(root.find('warning').textContent, /mismo acceso que Iris/);
    assert.match(root.find('risks-none').textContent, /No encontramos/);
    assert.match(root.find('review-widgets').textContent, /un widget: Demo/);
    assert.equal(calls.filter((call) => call.url.endsWith('/install')).length, 0, 'nothing installs by choosing a file');
    await root.find('install').click();
    await settle();
    assert.equal(asked.length, 1);
    assert.equal(asked[0].message, WARNING);
    assert.equal(asked[0].confirmLabel, 'Instalar');
    const install = calls.find((call) => call.url.endsWith('/install'));
    assert.deepEqual(JSON.parse(install.init.body), { token: 't'.repeat(43) });
    // This context has no refreshApps (an older shell): the reload button stays as the way in.
    assert.match(root.find('status').textContent, /ya está en Aplicaciones\. Sus widgets .* Recarga el escritorio para aplicar el cambio/);
    assert.ok(root.find('reload'));
    assert.ok(root.find('installed-demo'));
    assert.match(root.find('row-reload').textContent, /Recarga/);
});

test('F17: with a live shell the app is usable at once — no reload button, «Abrir» takes the focus', async () => {
    const calls = core();
    const refreshes = [];
    const installed = new Set();
    const opened = [];
    const { root } = await mounted({ installedApps: { has: (id) => installed.has(id) }, openApp: (id) => opened.push(id),
        refreshApps: async () => { refreshes.push(true); installed.add('demo'); return { ok: true, added: ['demo'], removed: [], updated: [] }; } });
    await chooseZip(root);
    await root.find('install').click();
    await settle();
    assert.equal(refreshes.length, 1);
    assert.match(root.find('status').textContent, /^«Demo» ya está en Aplicaciones\. Sus widgets están en Personalizar → Widgets\.$/);
    assert.equal(root.find('reload'), undefined, 'nothing to reload');
    assert.equal(globalThis.document.activeElement, root.find('open-demo'));
    await root.find('open-demo').click();
    assert.deepEqual(opened, ['demo']);
    assert.equal(calls.filter((call) => call.url === '/api/app-packages').length >= 2, true, 'the list is read again');
});

test('F17: if the desktop cannot take the change, «Reintentar» tries again and reload stays as the last resort', async () => {
    core();
    let attempts = 0;
    const reloads = [];
    const { root } = await mounted({ reload: () => reloads.push(true),
        refreshApps: async () => (++attempts === 1 ? { ok: false, error: 'offline' } : { ok: true, added: ['demo'], removed: [], updated: [] }) });
    await chooseZip(root);
    await root.find('install').click();
    await settle();
    assert.match(root.find('status').textContent, /ya está en Aplicaciones.*Pero el escritorio no se pudo actualizar/);
    assert.equal(globalThis.document.activeElement, root.find('retry-desktop'));
    assert.ok(root.find('reload'), 'the last resort is offered too');
    await root.find('retry-desktop').click();
    await settle();
    assert.equal(attempts, 2);
    assert.equal(root.find('retry-desktop'), undefined);
    assert.match(root.find('status').textContent, /^«Demo» ya está en Aplicaciones/);
    assert.equal(reloads.length, 0);
});

test('F17: updating an app that is open says it will restart; a closed one is not mentioned', async () => {
    for (const [inUse, mentioned] of [[true, true], [false, false]]) {
        core({ 'POST /api/app-packages/inspect': async () => ({ ok: true, status: 200, json: async () => ({ token: 't'.repeat(43), app: { ...APP, current: { version: '0.0.9' } } }) }) });
        const asked = [];
        const { root } = await mounted({ confirm: async (options) => { asked.push(options); return true; }, appInUse: (id) => inUse && id === 'demo',
            refreshApps: async () => ({ ok: true, added: [], removed: [], updated: ['demo'] }) });
        await chooseZip(root);
        await root.find('install').click();
        await settle();
        assert.equal(/está abierta: se reiniciará/.test(asked[0].message), mentioned);
        assert.ok(asked[0].message.startsWith(WARNING));
        assert.match(root.find('status').textContent, mentioned ? /se actualizó y se reinició con la nueva versión/ : /^«Demo» se actualizó\.$/);
    }
});

test('saying no, a non-zip file or an error from Core never installs anything', async () => {
    const calls = core({ 'POST /api/app-packages/inspect': async () => ({ ok: false, status: 422,
        json: async () => ({ error: { code: 'reserved_id', message: 'El id «calendar» pertenece a una app incluida con Iris.' } }) }) });
    const { root } = await mounted({ confirm: async () => false });
    await chooseZip(root, { name: 'foto.png', size: 10 });
    assert.match(root.find('error').textContent, /Elige un archivo \.zip/);
    assert.equal(calls.filter((call) => call.url.endsWith('/inspect')).length, 0);
    await chooseZip(root);
    assert.match(root.find('error').textContent, /pertenece a una app incluida/);
    assert.equal(globalThis.document.activeElement, root.find('error'));
    assert.equal(root.find('review'), undefined);
    const later = core();
    const second = await mounted({ confirm: async () => false });
    await chooseZip(second.root);
    await second.root.find('install').click();
    await settle();
    assert.equal(later.filter((call) => call.url.endsWith('/install')).length, 0, 'declining sends nothing');
    assert.ok(second.root.find('review'), 'declining keeps the review');
});

test('a review says whether it installs, updates or reinstalls the same version', async () => {
    for (const [current, expected, label] of [[null, /^Versión 0\.1\.0$/, 'Instalar'], [{ version: '0.0.9' }, /reemplaza la 0\.0\.9/, 'Actualizar'],
        [{ version: '0.1.0' }, /ya instalada: se reinstalará/, 'Actualizar']]) {
        core({ 'POST /api/app-packages/inspect': async () => ({ ok: true, status: 200, json: async () => ({ token: 't'.repeat(43), app: { ...APP, current } }) }) });
        const { root } = await mounted();
        await chooseZip(root);
        assert.match(root.find('review-version').textContent, expected);
        assert.equal(root.find('install').textContent, label);
    }
});

test('a folder is sent as its files (base64), without .git or other hidden folders', async () => {
    const calls = core();
    const { root } = await mounted();
    const file = (path, text) => ({ webkitRelativePath: path, name: path.split('/').at(-1), size: text.length,
        arrayBuffer: async () => new TextEncoder().encode(text).buffer });
    const input = root.find('pick-folder-input');
    assert.equal(input.webkitdirectory, true);
    input.files = [file('demo/app.json', '{}'), file('demo/.git/HEAD', 'ref'), file('demo/ui/main.js', 'export function createApp() {}')];
    await input.fire('change');
    await settle();
    const inspect = calls.find((call) => call.url.endsWith('/inspect'));
    assert.equal(inspect.init.headers['Content-Type'], 'application/json');
    const body = JSON.parse(inspect.init.body);
    assert.deepEqual(body.files.map((item) => item.path), ['demo/app.json', 'demo/ui/main.js']);
    assert.equal(atob(body.files[0].data), '{}');
    assert.ok(root.find('review'));
});

for (const ignored of ['.git', '.cache', 'node_modules', '__MACOSX']) {
    test(`dropping a folder skips ${ignored} before reading or counting its files, just like the picker`, async () => {
        const calls = core();
        const { root, app } = await mounted();
        const visited = [];
        const entries = [directoryEntry(ignored, Array.from({ length: MAX_FOLDER_FILES + 1 }, (_, index) => fileEntry(`ignored-${index}`)), visited),
            fileEntry('app.json', '{}'), directoryEntry('ui', [fileEntry('main.js', 'export function createApp() {}')], visited)];
        await dropFolder(root, directoryEntry('demo', entries, visited));
        const dropped = calls.find((call) => call.url.endsWith('/inspect'));
        assert.ok(dropped, root.find('error')?.textContent || 'the dropped folder must reach inspection');
        assert.ok(!visited.includes(ignored), 'an ignored directory must not even be opened');
        const droppedFiles = JSON.parse(dropped.init.body).files;
        assert.deepEqual(droppedFiles.map((item) => item.path), ['demo/app.json', 'demo/ui/main.js']);

        const input = root.find('pick-folder-input');
        input.files = [...Array.from({ length: MAX_FOLDER_FILES + 1 }, (_, index) => folderFile(`demo/${ignored}/ignored-${index}`)),
            folderFile('demo/app.json', '{}'), folderFile('demo/ui/main.js', 'export function createApp() {}')];
        await input.fire('change');
        await settle();
        const inspections = calls.filter((call) => call.url.endsWith('/inspect'));
        assert.equal(inspections.length, 2, 'the picker must submit its own inspection too');
        assert.deepEqual(JSON.parse(inspections.at(-1).init.body).files, droppedFiles);
        app.dispose();
    });
}

test('dropping folders still admits 400 useful files and refuses 401, across reader batches', async () => {
    for (const count of [MAX_FOLDER_FILES, MAX_FOLDER_FILES + 1]) {
        const calls = core();
        const { root, app } = await mounted();
        await dropFolder(root, directoryEntry('demo', Array.from({ length: count }, (_, index) => fileEntry(`file-${index}.js`))));
        const inspected = calls.filter((call) => call.url.endsWith('/inspect'));
        if (count === MAX_FOLDER_FILES) {
            assert.equal(inspected.length, 1);
            assert.equal(JSON.parse(inspected[0].init.body).files.length, MAX_FOLDER_FILES);
        } else {
            assert.equal(inspected.length, 0);
            assert.match(root.find('error').textContent, /más de 400 archivos/);
        }
        app.dispose();
    }
});

test('an older review that answers late never replaces the newer one', async () => {
    let release;
    const calls = core({ 'POST /api/app-packages/inspect': () => new Promise((resolve) => {
        if (!release) release = () => resolve({ ok: true, status: 200, json: async () => ({ token: 'o'.repeat(43), app: { ...APP, name: 'Vieja' } }) });
        else resolve({ ok: true, status: 200, json: async () => ({ token: 'n'.repeat(43), app: { ...APP, name: 'Nueva' } }) });
    }) });
    const { root } = await mounted();
    const first = chooseZip(root);
    await chooseZip(root);
    release(); await first; await settle();
    assert.equal(root.find('review-title').textContent, 'Nueva');
    assert.equal(calls.filter((call) => call.url.endsWith('/inspect')).length, 2);
});

test('uninstalling asks first, then removes the app, its widgets and its shortcut', async () => {
    const calls = core();
    const reloads = [];
    const opened = [];
    const { root, asked } = await mounted({ installedApps: new Map([['demo', { version: '0.1.0' }]]),
        openApp: (id) => opened.push(id), reload: () => reloads.push(true) });
    await chooseZip(root);
    await root.find('install').click();
    await settle();
    await root.find('open-demo').click();
    assert.deepEqual(opened, ['demo'], 'an app this session already loaded opens directly');
    await root.find('uninstall-demo').click();
    await settle();
    assert.match(asked.at(-1).message, /sus widgets y su acceso del inicio/);
    assert.ok(calls.some((call) => call.method === 'DELETE' && call.url === '/api/app-packages/demo'));
    assert.match(root.find('status').textContent, /se desinstaló/);
    assert.match(root.find('installed-empty').textContent, /Todavía no/);
    await root.find('reload').click();
    assert.equal(reloads.length, 1);
});

test('it retires cleanly: a late answer after closing changes nothing', async () => {
    let release;
    core({ 'POST /api/app-packages/inspect': () => new Promise((resolve) => { release = () => resolve({ ok: true, status: 200, json: async () => ({ token: 't'.repeat(43), app: APP }) }); }) });
    const { app, root } = await mounted();
    const pending = chooseZip(root);
    app.dispose();
    release(); await pending; await settle();
    assert.equal(root.children.length, 0);
});
