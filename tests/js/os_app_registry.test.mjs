// F09.1: one app registry built from apps/<id>/app.json feeds the shell's windows, tabs,
// launcher and loading, keeping every current ID, size and launcher entry.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { cp, mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { OS_APP_CATALOG, OS_APP_MODULES } from '../../face/js/os_app_catalog.js';
import { validateAppCatalog, validateAppManifest } from '../../face/js/os_app_manifest.js';
import { createAppRegistry } from '../../face/js/os_app_registry.js';
import { OS_ICON_PATHS } from '../../face/js/os_icons.js';
import { buildAppCatalog } from '../../scripts/build_app_catalog.mjs';

const ROOT = fileURLToPath(new URL('../..', import.meta.url));
const iconNames = Object.keys(OS_ICON_PATHS);
const shell = await readFile(join(ROOT, 'face/js/hermes_activity.js'), 'utf8');
const registry = createAppRegistry(OS_APP_CATALOG, { iconNames });

function method(name, deps = {}) {
    const block = shell.match(new RegExp(`^    ${name}\\([^]*?^    \\}`, 'm'))?.[0];
    assert.ok(block, `${name} must exist`);
    return new Function(...Object.keys(deps), `return ({${block}})`)(...Object.values(deps))[name];
}

const base = (overrides = {}) => ({
    schema: 'iris.app-manifest/v1', sdkVersion: 1, id: 'notes', name: 'Notas', version: '0.1.0',
    description: 'Notas rápidas', icon: 'notes', accent: 'cyan', presentation: 'window',
    window: { width: 780, height: 560 }, launcher: { visible: true, order: 500 }, ui: { adapter: 'legacy' },
    ...overrides,
});

test('the generated catalog matches apps/<id>/app.json', async () => {
    const { text } = await buildAppCatalog(ROOT);
    assert.equal(await readFile(join(ROOT, 'face/js/os_app_catalog.js'), 'utf8'), text,
        'run: node scripts/build_app_catalog.mjs');
    assert.deepEqual(registry.errors, []);
});

test('every current app keeps its ID, presentation and initial size', () => {
    const sizes = {
        activity: [780, 560], missions: [780, 560], routines: [780, 560], doctor: [780, 560], tools: [780, 560],
        agents: [920, 620], skills: [920, 620], marketplace: [920, 620], models: [920, 620], theme: [920, 620],
        automations: [1120, 680], notifications: [1120, 680], permissions: [1120, 680], player: [1120, 680], continuity: [1120, 680],
        conexiones: [780, 600],  // F10.4
        calendar: [1180, 720],  // F11
        mail: [1180, 720],  // F13
        'app-manager': [780, 600], // Home personalization
        store: [760, 640],  // F17 Iris Store
    };
    const windows = registry.windowTable();
    assert.deepEqual(Object.keys(windows).sort(), Object.keys(sizes).sort(), 'the registered app windows');
    for (const [id, [width, height]] of Object.entries(sizes)) assert.deepEqual(windows[id].window, { width, height }, id);
    assert.deepEqual(registry.list({ presentation: 'tab' }).map((app) => app.id).sort(), ['camera', 'settings']);
    assert.equal(windows.theme.icon, 'palette');
    assert.equal(windows.player.title, 'IRIS Player');
});

test('the launcher keeps its order and labels', () => {
    assert.deepEqual(registry.launcher().map((app) => app.launcher.label || app.name), [
        'Agentes', 'Actividad', 'Tablero de tareas', 'Tareas programadas', 'Automatizaciones', 'Diagnóstico', 'Herramientas MCP',
        'Skills', 'Marketplace', 'Iris Store', 'Notificaciones', 'Permisos', 'Conexiones', 'Calendario', 'Correo', 'IRIS Player', 'Perfiles y sesiones',
        'Modelos', 'Theme Studio', 'Cámara', 'Administrar aplicaciones', 'Ajustes',
    ]);
    const escape = (value) => String(value).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
    const osIcon = (name) => `<i data-icon="${name}"></i>`;
    const html = method('_launcherButtonsHtml', { OS_APPS: registry, osIcon }).call({ _escape: escape, _escapeAttr: escape });
    assert.equal(html.match(/data-os-open=/g).length, 22);
    assert.match(html, /data-os-open="automations" data-accent="amber">\s*<i data-icon="automations"><\/i>\s*<strong>Automatizaciones<\/strong>\s*<span data-app-brief="automations">Tareas al no usar Iris<\/span>/);
});

test('destinations: app windows, app tabs, the shell\'s own places and unknown names', () => {
    assert.equal(registry.resolve('player').kind, 'window');
    assert.equal(registry.resolve('camera').kind, 'tab');
    assert.equal(registry.resolve(' Settings ').id, 'settings');
    assert.deepEqual(registry.resolve('apps'), { kind: 'internal', id: 'apps' });
    assert.deepEqual(registry.resolve(''), { kind: 'internal', id: 'desktop' });
    assert.deepEqual(registry.resolve('correo'), { kind: 'unknown', id: 'correo' });
});

test('a manifest is data only: unknown fields, paths and permissions-as-grants are refused', () => {
    assert.equal(validateAppManifest(base(), { iconNames }).ok, true);
    const refused = (overrides, pattern) => {
        const result = validateAppManifest(base(overrides), { iconNames });
        assert.equal(result.ok, false, JSON.stringify(overrides));
        assert.match(result.errors.join('\n'), pattern);
    };
    refused({ entry: 'https://example.com/app.js' }, /entry: campo desconocido/);
    refused({ ui: { adapter: 'legacy', entry: 'main.js' } }, /solo uno de/);
    refused({ ui: { adapter: 'legacy', script: 'x.js' } }, /ui\.script: campo desconocido/);
    // F09.3: the app's own ui/ folder names its entry module and one stylesheet, as bare names.
    assert.equal(validateAppManifest(base({ ui: { entry: 'main.js', style: 'style.css' } }), { iconNames }).ok, true);
    refused({ ui: { entry: '../core/agent.py' } }, /ui\.entry/);
    refused({ ui: { entry: 'sub/main.js' } }, /ui\.entry/);
    refused({ ui: { entry: 'main.js', style: '../face/css/styles.css' } }, /ui\.style/);
    refused({ ui: { module: 'notes_app.js', style: 'style.css' } }, /ui\.style: solo para apps con «entry»/);
    refused({ ui: { adapter: 'module' } }, /ui\.adapter/);
    // F09.2: an app with its own module names a bare file of face/js, never a path or URL.
    assert.equal(validateAppManifest(base({ ui: { module: 'notes_app.js' } }), { iconNames }).ok, true);
    refused({ ui: { module: '../core/agent.py' } }, /ui\.module/);
    refused({ ui: { module: 'https://example.com/app.js' } }, /ui\.module/);
    refused({ ui: { module: 'apps/notes.js' } }, /ui\.module/);
    refused({ ui: { adapter: 'legacy', module: 'notes_app.js' } }, /solo uno de/);
    refused({ ui: {} }, /solo uno de/);
    refused({ id: 'apps' }, /reservado/);
    refused({ id: 'Correo' }, /id:/);
    refused({ icon: 'unknownicon' }, /no existe en os_icons/);
    refused({ presentation: 'tab' }, /window: solo para presentation/);
    refused({ window: undefined }, /window: obligatorio/);
    refused({ window: { width: 5000, height: 560 } }, /window\.width/);
    refused({ accent: 'red' }, /accent/);
    refused({ requestedCapabilities: ['camera', 'camera'] }, /repetido/);
    refused({ name: 'Notas\u0007' }, /control/);
    const newer = validateAppManifest(base({ sdkVersion: 3 }), { iconNames });
    assert.equal(newer.ok, false);
    assert.equal(newer.incompatible, true);
});

test('apps with their own code (module or ui folder) get a view root; shell views do not', () => {
    const apps = createAppRegistry([
        base({ id: 'notes', ui: { entry: 'main.js', style: 'style.css' } }),
        base({ id: 'clock', icon: 'clock', ui: { module: 'clock_app.js' } }),
        base({ id: 'board', icon: 'missions' }),
    ], { iconNames });
    assert.deepEqual(['notes', 'clock', 'board', 'missing'].map((id) => apps.hasOwnCode(id)), [true, true, false, false]);
    const views = method('_moduleViewsHtml', { OS_APPS: apps }).call({ _escapeAttr: String });
    assert.deepEqual([...views.matchAll(/data-app-view="([^"]+)"/g)].map((match) => match[1]), ['notes', 'clock']);
});

test('one bad or duplicated app stays out; the rest of the catalog keeps working', () => {
    const errors = [];
    const apps = createAppRegistry([
        base(), base({ id: 'clock', icon: 'clock' }), base({ id: 'clock', icon: 'clock', name: 'Reloj 2' }),
        base({ id: 'broken', icon: 'nope' }), base({ id: 'future', sdkVersion: 9 }),
    ], { iconNames, onError: (error) => errors.push(error.id) });
    assert.deepEqual(apps.list().map((app) => app.id), ['notes']);
    assert.deepEqual(errors.sort(), ['broken', 'clock', 'clock', 'future']);
    assert.equal(apps.resolve('clock').kind, 'unknown', 'a duplicated ID picks neither entry');
    assert.equal(validateAppCatalog('not a list').apps.length, 0);
});

test('every place the shell opens resolves in the registry, and every app has a view and adapter', () => {
    for (const [, target] of shell.matchAll(/data-os-open="([a-z-]+)"/g)) {
        assert.notEqual(registry.resolve(target).kind, 'unknown', `data-os-open="${target}"`);
    }
    for (const [, target] of shell.matchAll(/data-workbench-tab="([a-z-]+)"/g)) {
        assert.notEqual(registry.resolve(target).kind, 'unknown', `data-workbench-tab="${target}"`);
    }
    const adapters = method('_createAppAdapters').call({});
    const legacy = registry.list().filter((app) => app.ui.adapter === 'legacy');
    for (const app of legacy) {
        assert.ok(shell.includes(`data-workbench-view="${app.id}"`), `${app.id} has a view`);
        assert.ok(Object.hasOwn(adapters, app.id), `${app.id} has an adapter`);
    }
    assert.deepEqual(Object.keys(adapters).sort(), legacy.map((app) => app.id).sort(), 'adapters only for apps whose view lives in the shell');
    // Apps with their own module (in face/js or their ui/ folder): a build-chosen import and a generated view.
    const modular = registry.list().filter((app) => app.ui.module || app.ui.entry).map((app) => app.id).sort();
    assert.deepEqual(Object.keys(OS_APP_MODULES).sort(), modular);
    assert.ok(modular.includes('automations'));
    const views = method('_moduleViewsHtml', { OS_APPS: registry }).call({ _escapeAttr: String });
    for (const id of modular) {
        assert.match(views, new RegExp(`data-workbench-view="${id}" data-app-view="${id}"`));
        assert.ok(!shell.includes(`data-workbench-view="${id}">`), `${id} has no hand-written view`);
    }
});

test('opening resolves through the registry: windows, tabs, launcher and unknown names', () => {
    const calls = [];
    const shellState = {
        open: () => calls.push('open'),
        _openWindow: (id) => calls.push(`window:${id}`),
        _setTab: (id) => calls.push(`tab:${id}`),
        _toggleDesktopLauncher: (on) => calls.push(`launcher:${on}`),
    };
    shellState._openOsApp = method('_openOsApp', { OS_APPS: registry });
    const open = method('openCommandSurface', { OS_APPS: registry });
    for (const target of ['player', 'camera', 'apps', 'correo', 'desktop']) open.call(shellState, target);
    assert.deepEqual(calls, [
        'open', 'window:player', 'open', 'tab:camera', 'open', 'tab:desktop', 'launcher:true',
        'open', 'tab:desktop', 'open', 'tab:desktop',
    ]);
});

test('loading goes through the app adapter; a failure stays in that app', async () => {
    const calls = [];
    const warnings = [];
    const state = {
        loadGatewayStatus: (force) => calls.push(`status:${force}`),
        loadHermesDoctor: () => calls.push('doctor'),
        _loadModelsTab: (force) => calls.push(`models:${force}`),
        loadMissions: () => { throw new Error('boom'); },
        loadSkills: () => Promise.reject(new Error('late boom')),
    };
    state._appAdapters = method('_createAppAdapters').call(state);
    const load = method('_loadApp', { OS_APPS: registry, console: { warn: (...args) => warnings.push(args[0]) } });
    load.call(state, 'doctor', { opening: true });
    load.call(state, 'doctor', { force: true });
    load.call(state, 'models', { opening: true });
    load.call(state, 'models', {});
    load.call(state, 'missions');
    load.call(state, 'skills');
    load.call(state, 'activity');
    load.call(state, 'correo');
    await new Promise((resolve) => setImmediate(resolve));
    assert.deepEqual(calls, ['status:false', 'doctor', 'status:true', 'models:true', 'models:false']);
    assert.equal(warnings.length, 2, 'the sync throw and the rejected promise are reported, not thrown');
    // Opening or restoring a window loads it as «opening»; a hidden one loads nothing.
    const loads = [];
    const windowState = { _hasVisibleAppSurface: (id) => id !== 'hidden', _loadApp: (id, options) => loads.push([id, options]) };
    method('_loadWindowApp').call(windowState, 'doctor');
    method('_loadWindowApp').call(windowState, 'hidden');
    assert.deepEqual(loads, [['doctor', { opening: true }]]);
});

test('the build refuses a bad catalog instead of skipping apps', async () => {
    const root = await mkdtemp(join(tmpdir(), 'iris-apps-'));
    try {
        await mkdir(join(root, 'face/js'), { recursive: true });
        for (const file of ['os_app_manifest.js', 'os_app_extension.js', 'os_icons.js']) await cp(join(ROOT, 'face/js', file), join(root, 'face/js', file));
        const write = async (folder, data) => {
            await mkdir(join(root, 'apps', folder), { recursive: true });
            await writeFile(join(root, 'apps', folder, 'app.json'), typeof data === 'string' ? data : JSON.stringify(data));
        };
        await write('notes', base());
        await write('_plantilla', { not: 'an app' }); // templates are not apps
        await writeFile(join(root, 'apps', 'README.md'), '# Apps\n'); // nor is documentation
        assert.equal((await buildAppCatalog(root)).apps.length, 1);
        await write('reloj', base({ id: 'clock', icon: 'clock' }));
        await assert.rejects(buildAppCatalog(root), /apps\/reloj: el id «clock» debe coincidir con la carpeta/);
        await write('reloj', '{ not json');
        await assert.rejects(buildAppCatalog(root), /apps\/reloj\/app\.json: JSON inválido/);
        await write('reloj', base({ id: 'reloj', icon: 'unknownicon' }));
        await assert.rejects(buildAppCatalog(root), /apps\/reloj\/app\.json: icon: «unknownicon» no existe/);
        // A module must exist in face/js and export createApp; the import carries its digest.
        await write('reloj', base({ id: 'reloj', icon: 'clock', ui: { module: 'reloj_app.js' } }));
        await assert.rejects(buildAppCatalog(root), /ui\.module «reloj_app\.js» no existe en face\/js/);
        await writeFile(join(root, 'face/js/reloj_app.js'), 'export const nothing = 1;\n');
        await assert.rejects(buildAppCatalog(root), /face\/js\/reloj_app\.js no exporta createApp/);
        await writeFile(join(root, 'face/js/reloj_app.js'), 'export function createApp() { return {}; }\n');
        const { text } = await buildAppCatalog(root);
        assert.match(text, /"reloj": \(\) => import\("\.\/reloj_app\.js\?v=[0-9a-f]{10}"\)/);
        assert.doesNotMatch(text, /"notes": \(\) => import/, 'legacy apps have no module import');
    } finally {
        await rm(root, { recursive: true, force: true });
    }
});
