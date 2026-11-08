import test from 'node:test';
import assert from 'node:assert/strict';
import { homePreviewItems, ThemeHomePreview } from '../../face/js/os_theme_home_preview.js';
import { readFile } from 'node:fs/promises';

class Node {
    constructor(tag) { Object.assign(this, { tag, children: [], dataset: {}, attributes: {}, textContent: '' }); }
    append(...nodes) { this.children.push(...nodes); }
    replaceChildren(...nodes) { this.children = nodes; }
    setAttribute(key, value) { this.attributes[key] = String(value); }
    all() { return [this, ...this.children.flatMap(n => n.all())]; }
    text() { return this.all().map(n => n.textContent).join(' '); }
}
const registry = { get: id => ['player', 'theme', 'calendar'].includes(id) ? { id, name: id, icon: id, launcher: {} } : null };
const catalog = { 'player.transport': { name: 'IRIS Player', app: 'player', size: 'compact', configVersion: 1 } };
const instance = { instance_id: `wdg_${'a'.repeat(32)}`, widget_id: 'player.transport', config_version: 1, size: 'compact', calendars: [] };
const home = () => ({ schema: 'iris.desktop-home/v1', revision: 0, shortcuts: ['theme', 'player'], hidden_apps: [], order: [] });
const widgets = () => ({ schema: 'iris.desktop-widgets/v1', revision: 0, side: 'right', widgets: [instance] });
const flush = () => new Promise(resolve => setImmediate(resolve));
function setup({ layout = home(), data = widgets(), fail = false, fetchImpl } = {}) {
    const listeners = new Set(), calls = [];
    const model = { layout, error: '', loads: 0,
        subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); },
        async load() { this.loads++; }, notify() { for (const fn of listeners) fn(); } };
    const root = new Node('section'); let liveWidgets = null;
    const preview = new ThemeHomePreview({ root, model, registry, catalog, icon: name => `<svg data-icon="${name}"></svg>`,
        documentLike: { createElement: tag => new Node(tag) }, getWidgets: () => liveWidgets,
        fetchImpl: async (url, init) => { calls.push({ url, ...init }); return fetchImpl ? fetchImpl(url, init)
            : { ok: !fail, json: async () => structuredClone(data) }; } });
    return { preview, root, model, calls, listeners, live: value => { liveWidgets = value; preview.render(); } };
}

test('the miniature uses the same mixed saved order, including hidden icons pinned on Home', () => {
    const layout = { ...home(), hidden_apps: ['player'], order: [`widget:${instance.instance_id}`, 'app:player'] };
    const items = homePreviewItems(layout, widgets(), registry, catalog);
    assert.deepEqual(items.map(item => item.ref), [`widget:${instance.instance_id}`, 'app:player', 'app:theme']);
    assert.equal(items[0].name, 'IRIS Player');
    assert.equal(homePreviewItems({ ...home(), shortcuts: ['removed'] }, { ...widgets(), widgets: [{ ...instance, config_version: 999 }] }, registry, catalog)[0].name, 'App no disponible');
    assert.equal(homePreviewItems(home(), { ...widgets(), widgets: [{ ...instance, config_version: 999 }] }, registry, catalog).at(-1).name, 'Widget no disponible');
});

test('construction/hidden renders are passive; visibility reads only home/widget preferences once', async () => {
    const { preview, root, model, calls } = setup();
    preview.render(); assert.equal(calls.length, 0); assert.equal(model.loads, 0);
    preview.setVisibility(true); const reading = preview.refresh(); await reading;
    assert.equal(calls.length, 1); assert.equal(model.loads, 1);
    assert.equal(calls[0].url, '/api/desktop/widgets'); assert.equal(calls[0].method, undefined);
    assert.ok(root.all().every(n => !['button', 'a', 'input', 'audio', 'video', 'iframe', 'img'].includes(n.tag)));
    assert.match(root.text(), /sin activarlos/); assert.ok(!/64%|MCP catalog|Reproduciendo/.test(root.text()));
    preview.setVisibility(true); assert.equal(calls.length, 1); preview.dispose();
});

test('home edits and newer widget preferences update the visible preview without mounting widgets or fetching', async () => {
    const { preview, root, model, calls, live } = setup(); preview.setVisibility(true); await flush();
    model.layout = { ...home(), shortcuts: ['calendar'], revision: 1 }; model.notify();
    assert.match(root.text(), /calendar/); assert.doesNotMatch(root.text(), /theme/);
    live({ ...widgets(), revision: 1, widgets: [] });
    assert.doesNotMatch(root.text(), /IRIS Player/); assert.equal(calls.length, 1);
    const frame = root.children[0]; preview.render(); assert.equal(root.children[0], frame, 'unchanged data do not rebuild the miniature');
    preview.dispose();
});

test('a deliberately empty home remains empty, while read failures are never shown as an empty home', async () => {
    const empty = setup({ layout: { ...home(), shortcuts: [] }, data: { ...widgets(), widgets: [] } });
    empty.preview.setVisibility(true); assert.doesNotMatch(empty.root.text(), /Tu inicio está libre/); await flush();
    assert.match(empty.root.text(), /Tu inicio está libre/); empty.preview.dispose();
    const broken = setup({ layout: null, fail: true }); broken.model.error = 'No se pudo leer el Inicio';
    broken.preview.setVisibility(true); await flush();
    assert.doesNotMatch(broken.root.text(), /Tu inicio está libre/); assert.match(broken.root.text(), /no disponible|No se pudo/); broken.preview.dispose();
});

test('hiding or disposal aborts the private read; a late answer cannot overwrite a new visible generation', async () => {
    const answers = [];
    const { preview, root, calls, listeners } = setup({ fetchImpl: () => new Promise(resolve => answers.push(resolve)) });
    preview.setVisibility(true); const first = preview.pending.promise; preview.setVisibility(false);
    assert.equal(calls[0].signal.aborted, true); preview.setVisibility(true);
    answers[1]({ ok: true, json: async () => ({ ...widgets(), revision: 2, widgets: [] }) }); await flush();
    answers[0]({ ok: true, json: async () => widgets() }); await first;
    assert.doesNotMatch(root.text(), /IRIS Player/); assert.equal(preview.widgets.revision, 2);
    preview.setVisibility(false); preview.setVisibility(true); preview.dispose();
    assert.equal(calls[2].signal.aborted, true); assert.equal(listeners.size, 0);
    answers[2]({ ok: true, json: async () => widgets() }); await flush();
    assert.equal(root.children.length, 0);
});

test('long/untrusted app names remain text and a large Home has an explicit miniature limit', async () => {
    const { preview, root, model } = setup();
    registry.get = id => ({ id, name: '<img onerror=bad()>', icon: 'apps' });
    try {
        model.layout = { ...home(), shortcuts: Array.from({ length: 12 }, (_, i) => `item-${i}`) };
        preview.setVisibility(true); await flush();
        assert.equal(root.all().filter(n => n.dataset.previewRef).length, 8);
        assert.match(root.text(), /5 elementos más/); assert.match(root.text(), /<img onerror=bad\(\)>/);
        assert.ok(!root.all().some(n => n.tag === 'img'));
    } finally { registry.get = id => ['player', 'theme', 'calendar'].includes(id) ? { id, name: id, icon: id, launcher: {} } : null; preview.dispose(); }
});

test('the shell replaces the old fake activity card and shares visibility/actions with the actual preview', async () => {
    const source = await readFile(new URL('../../face/js/hermes_activity.js', import.meta.url), 'utf8');
    assert.doesNotMatch(source, /os-theme-preview-hero|os-theme-preview-rings/);
    const extract = name => source.match(new RegExp(`^    ${name}\\([^]*?^    \\}`, 'm'))[0];
    const events = [];
    const sync = new Function('OS_APPS', `return ({${extract('_syncModuleVisibility')}})` )({ list: () => [] })._syncModuleVisibility;
    sync.call({ _themeHomePreview: { setVisibility: value => events.push(value) }, _hasVisibleAppSurface: id => id === 'theme' });
    assert.deepEqual(events, [true]);
    const run = new Function(`return ({${extract('_runThemeAction')}})`)()._runThemeAction;
    run.call({ _themeHomePreview: { refresh: () => events.push('refresh') } }, 'refresh-preview');
    assert.deepEqual(events, [true, 'refresh']);
});
