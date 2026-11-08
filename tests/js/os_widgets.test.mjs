import test from 'node:test';
import assert from 'node:assert/strict';
import { DesktopWidgets, widgetLayout } from '../../face/js/os_widgets.js';

class Node {
    constructor(tag) { Object.assign(this, { tag, children: [], listeners: {}, attributes: {}, dataset: {}, disabled: false, textContent: '', hidden: false }); }
    append(...nodes) { for (const node of nodes) { node.remove(); node.parent = this; this.children.push(node); } }
    remove() { if (this.parent) this.parent.children = this.parent.children.filter((item) => item !== this); this.parent = null; }
    get parentNode() { return this.parent; }
    replaceChildren(...nodes) { for (const node of this.children) node.parent = null; this.children = []; this.append(...nodes); }
    setAttribute(name, value) { this.attributes[name] = String(value); }
    addEventListener(type, action) { (this.listeners[type] ||= []).push(action); }
    fire(type, fields = {}) { for (const action of this.listeners[type] || []) action({ target: this, stopPropagation() {}, preventDefault() {}, ...fields }); }
    focus() { doc.activeElement = this; }
    all() { return [this, ...this.children.flatMap((node) => node.all())]; }
    contains(node) { return this.all().includes(node); }
    querySelector(selector) { return selector === 'button' ? this.all().find((item) => item.tag === 'button')
        : this.all().find((item) => item.dataset.widgetKey === selector.match(/data-widget-key="([^"]+)"/)?.[1]); }
    key(value) { return this.all().find((item) => item.dataset.widgetKey === value); }
}
const doc = { activeElement: null, createElement: (tag) => new Node(tag) };
const initial = () => ({ schema: 'iris.desktop-widgets/v1', revision: 0, side: 'right', widgets: [] });
const instance = (n) => ({ instance_id: `wdg_${String(n).padStart(32, '0')}`, widget_id: 'calendar.month', config_version: 1, size: 'compact', calendars: [] });
const flush = () => new Promise((resolve) => setImmediate(resolve));
function setup({ data = initial(), factories, route, services = {} } = {}) {
    let saved = structuredClone(data), visible = true;
    const calls = [], events = [], root = new Node('section');
    const widgetFactories = factories || { 'calendar.month': ({ root, config }) => {
        root.textContent = 'Calendario real'; events.push(['mount', config.instance_id]);
        return { configure: (value) => events.push(['configure', value]), setVisibility: (value) => events.push(['visible', value]),
            dispose: () => events.push(['dispose', config.instance_id]) };
    } };
    const host = new DesktopWidgets({ root, documentLike: doc, factories: widgetFactories, services, isVisible: () => visible,
        fetchImpl: async (url, init) => {
            calls.push({ url, ...init });
            if (route) return route(url, init, saved);
            if (init.method === 'PUT') saved = { ...JSON.parse(init.body), revision: saved.revision + 1 };
            return { ok: true, json: async () => structuredClone(saved) };
        } });
    return { host, root, calls, events, setVisible(value) { visible = value; host.syncVisibility(); } };
}

test('a widget host is passive until the desktop is visible; reopening loads profile preferences', async () => {
    const { host, calls, setVisible } = setup();
    assert.equal(calls.length, 0);
    setVisible(false); assert.equal(calls.length, 0);
    setVisible(true); await flush(); assert.equal(calls.length, 1); assert.equal(calls[0].method, 'GET');
    setVisible(false); setVisible(true); await flush(); assert.equal(calls.length, 2); host.dispose();
});

test('a reviewed Player service is injected only into its widget, never borrowed by Calendar', async () => {
    const player = Object.freeze({ read() {} }), received = [];
    const factory = (context) => { received.push(context.services); return { setVisibility() {}, dispose() {} }; };
    const { host } = setup({ data: { ...initial(), widgets: [instance(1), { ...instance(2), widget_id: 'player.transport' }] },
        services: { 'player.transport': Object.freeze({ player }) },
        factories: { 'calendar.month': factory, 'player.transport': factory } });
    await host.load(); await flush();
    assert.deepEqual(received[0], {}); assert.equal(received[1].player, player);
    host.dispose();
});

test('integrated home keeps the original widget instance and delegates ordering to the shared grid', async () => {
    const { host, root, events } = setup(); let changes = 0;
    host.integrated = true; host.onChange = () => { changes++; };
    await host.load(); await host.add('calendar.month'); await flush();
    const id = host.layout.widgets[0].instance_id;
    host.editing = true; host.renderControls();
    assert.equal(root.key(`up:${id}`), undefined);
    assert.equal(root.key(`down:${id}`), undefined);
    assert.ok(root.key(`remove:${id}`));
    assert.ok(changes >= 3); assert.equal(events.filter(([kind]) => kind === 'mount').length, 1);
    host.homeEditing = true; host.syncVisibility();
    assert.equal(events.at(-1)[1], false); host.dispose();
});

test('refreshing widget preferences does not detach or reorder a mixed home that is already mounted', async () => {
    const { host } = setup({ data: { ...initial(), widgets: [instance(1), instance(2)] } });
    host.integrated = true; await host.load(); await flush();
    const a = host.cards.get(instance(1).instance_id).root, b = host.cards.get(instance(2).instance_id).root;
    const shortcut = new Node('article'); host.list.append(b, shortcut, a);
    host.reconcile(); assert.deepEqual(host.list.children, [b, shortcut, a]);
    host.dispose();
});

test('add, reorder, configure and remove persist exact instances and keep window layout separate', async () => {
    const { host, root, calls, events } = setup(); await host.load();
    await host.add('calendar.month'); await flush();
    await host.add('calendar.month'); await flush();
    assert.equal(host.cards.size, 2);
    const [a, b] = host.layout.widgets;
    await host.move(b.instance_id, -1);
    assert.deepEqual(host.layout.widgets.map((item) => item.instance_id), [b.instance_id, a.instance_id]);
    assert.equal(events.filter(([type]) => type === 'mount').length, 2, 'reorder never creates another instance');
    await host.configure(a.instance_id, [`cal_${'a'.repeat(32)}`]);
    assert.deepEqual(host.layout.widgets[1].calendars, [`cal_${'a'.repeat(32)}`]);
    await host.remove(a.instance_id);
    assert.equal(host.cards.size, 1); assert.ok(events.some(([type, id]) => type === 'dispose' && id === a.instance_id));
    assert.ok(calls.every((call) => call.url === '/api/desktop/widgets'));
    assert.equal(root.dataset.side, 'right'); host.dispose();
});

test('profile errors and incompatible layouts do not silently restore or save defaults', async () => {
    for (const value of [{ ...initial(), schema: 'future/v2' }, { ...initial(), widgets: [{ ...instance(1), config_version: -1 }] }]) {
        const { host, calls } = setup({ data: value }); await host.load();
        assert.equal(host.layout, null); assert.match(host.error, /no compatibles/);
        await host.add('calendar.month'); assert.equal(calls.length, 1); host.dispose();
    }
});

test('a removed app stays as an unavailable, removable widget and never imports code', async () => {
    const { host, events } = setup({ data: { ...initial(), widgets: [{ ...instance(1), widget_id: 'unknown.widget' }] } });
    await host.load(); await flush();
    assert.equal(host.cards.size, 1); assert.equal(events.length, 0);
    assert.match([...host.cards.values()][0].root.children[0].textContent, /no está disponible/);
    await host.remove(instance(1).instance_id); assert.equal(host.layout.widgets.length, 0); host.dispose();
});

test('a save conflict reloads the actual layout and never retries the write', async () => {
    const actual = { ...initial(), revision: 4, side: 'left' };
    const { host, calls } = setup({ route: async (url, init) => init.method === 'PUT'
        ? { ok: false, status: 409, json: async () => ({ error: { message: 'Cambió en otra ventana.' } }) }
        : { ok: true, json: async () => calls.length === 1 ? initial() : actual } });
    await host.load(); await host.add('calendar.month');
    assert.equal(host.layout.revision, 4); assert.match(host.error, /otra ventana/);
    assert.equal(calls.filter((call) => call.method === 'PUT').length, 1); host.dispose();
});

test('keyboard ordering restores focus, Escape closes personalization and hidden widgets stop', async () => {
    const { host, root, setVisible, events } = setup({ data: { ...initial(), widgets: [instance(1), instance(2)] } });
    await host.load(); await flush(); host.editing = true; host.renderControls();
    root.key(`up:${instance(2).instance_id}`).focus(); await host.move(instance(2).instance_id, -1);
    assert.equal(doc.activeElement.dataset.widgetKey, `down:${instance(2).instance_id}`);
    host.editor.fire('keydown', { key: 'Escape' }); assert.equal(host.editing, false);
    assert.equal(doc.activeElement.dataset.widgetKey, 'customize');
    setVisible(false); assert.equal(events.at(-1)[1], false); host.dispose();
});

test('one failed widget stays local; removal during import disposes the late instance', async () => {
    let finish;
    const delayed = new Promise((resolve) => { finish = resolve; }); let disposed = 0;
    const { host } = setup({ data: { ...initial(), widgets: [instance(1)] }, factories: { 'calendar.month': () => delayed } });
    await host.load(); await host.remove(instance(1).instance_id);
    finish({ configure() {}, setVisibility() {}, dispose() { disposed++; } }); await flush();
    assert.equal(host.cards.size, 0); assert.equal(disposed, 1); host.dispose();
});

test('saving disables competing actions; no optimistic success before the server responds', async () => {
    let finish; const delayed = new Promise((resolve) => { finish = resolve; });
    const { host, calls } = setup({ route: async (url, init) => init.method === 'PUT' ? delayed : { ok: true, json: async () => initial() } });
    await host.load(); const saving = host.add('calendar.month'); await flush();
    assert.equal(host.cards.size, 0); assert.equal(host.busy, true);
    await host.add('calendar.month'); assert.equal(calls.length, 2);
    finish({ ok: true, json: async () => ({ ...initial(), revision: 1, widgets: [instance(1)] }) });
    await saving; assert.equal(host.layout.revision, 1); host.dispose();
});

for (const broken of ['configure', 'setVisibility', 'dispose']) {
    test(`a widget whose ${broken} throws stays local and never prevents other widgets cleaning up`, async () => {
        const signals = [], disposed = [];
        const { host } = setup({ data: { ...initial(), widgets: [instance(1), instance(2)] }, factories: {
            'calendar.month': ({ config, signal }) => {
                signals.push(signal);
                const bad = config.instance_id === instance(1).instance_id;
                return {
                    configure() { if (bad && broken === 'configure') throw new Error('bad configure'); },
                    setVisibility() { if (bad && broken === 'setVisibility') throw new Error('bad visibility'); },
                    dispose() { disposed.push(config.instance_id); if (bad && broken === 'dispose') throw new Error('bad dispose'); },
                };
            },
        } });
        await host.load(); await flush();
        assert.doesNotThrow(() => host.syncVisibility());
        if (broken !== 'dispose') assert.equal(host.cards.get(instance(1).instance_id).controller, null);
        assert.ok(host.cards.get(instance(2).instance_id).controller);
        assert.doesNotThrow(() => host.dispose());
        assert.equal(disposed.length, 2);
        assert.ok(signals.every(signal => signal?.aborted === true));
    });
}

test('F17: an updated app remounts only its own widgets, and an older mount that ends late is discarded', async () => {
    const events = [];
    let version = 1, release = null;
    const controller = (v) => ({ configure() {}, setVisibility() {}, dispose: () => events.push(`dispose:${v}`) });
    const factories = { 'calendar.month': () => {
        const v = version; events.push(`mount:${v}`);
        return v === 1 ? new Promise((resolve) => { release = () => resolve(controller(1)); }) : controller(v);
    } };
    const h = setup({ data: { ...initial(), widgets: [instance(1)] }, factories });
    await h.host.load(); await flush();
    version = 2; h.host.remountApp('calendar'); await flush();
    release(); await flush(); // the version-1 mount answers after the remount
    assert.deepEqual(events, ['mount:1', 'mount:2', 'dispose:1'], 'the late old controller is disposed, never kept');
    h.host.remountApp('player'); await flush();
    assert.equal(events.length, 3, 'widgets of other apps are untouched');
    version = 3; h.host.remountApp('calendar'); await flush();
    assert.deepEqual(events.slice(3), ['dispose:2', 'mount:3']);
    h.host.dispose();
});

test('F17 (Codex L02): uninstalling during a save drops the widgets at once; the older save reply cannot bring them back', async () => {
    let hold = null, stale = null, uninstalled = false, releaseRead = null;
    const h = setup({ data: { ...initial(), widgets: [instance(1)] }, route: async (url, init, saved) => {
        if (init.method === 'PUT') {
            stale = { ...JSON.parse(init.body), revision: 1 }; // accepted by Core before the uninstall…
            await new Promise((resolve) => { hold = resolve; }); // …but its answer arrives late
            return { ok: true, json: async () => structuredClone(stale) };
        }
        if (uninstalled) await new Promise((resolve) => { releaseRead = resolve; }); // Core's clean answer, held for the check below
        return { ok: true, json: async () => (uninstalled ? { ...initial(), revision: 2 } : { ...initial(), widgets: [instance(1)] }) };
    } });
    await h.host.load(); await flush();
    assert.equal(h.host.cards.size, 1);
    const save = h.host.save([...h.host.layout.widgets], 'left'); await flush();
    let read = null;
    uninstalled = true; // Core uninstalls the app and drops its instance (revision 2)
    const forgotten = h.host.forgetApps(['calendar']).then((value) => { read = value; });
    assert.equal(h.host.cards.size, 0, 'gone at once, without waiting for the save');
    assert.ok(h.events.some(([kind]) => kind === 'dispose'));
    hold(); await save; await flush();
    assert.ok(releaseRead, 'the re-read waited for the save and is now in flight');
    assert.equal(h.host.cards.size, 0, 'the older reply is filtered, before the answer of Core arrives');
    assert.deepEqual(h.host.layout.widgets, []);
    releaseRead(); await forgotten;
    assert.equal(read, true, 'Core is read again once nothing is pending');
    assert.equal(h.host.layout.revision, 2);
    assert.equal(h.host.retiredApps.size, 0, 'after Core\'s own answer, a reinstall may add the widget again');
    h.host.dispose();
});

test('F17 (Codex L01): an app\'s widget stylesheets go on uninstall or update, mounted or not; other apps keep theirs', () => {
    const h = setup();
    const removed = [];
    const sheet = (id) => ({ dataset: { widgetStyle: id }, remove: () => removed.push(id) });
    h.host.styleSheets.set('demo.timer', sheet('demo.timer'));
    h.host.styleSheets.set('demo.other', sheet('demo.other'));
    h.host.styleSheets.set('calendar.month', sheet('calendar.month'));
    h.host.purgeAppStyles(['demo']);
    assert.deepEqual(removed.sort(), ['demo.other', 'demo.timer']);
    assert.deepEqual([...h.host.styleSheets.keys()], ['calendar.month']);
    h.host.restoreApps(['demo']);
    h.host.dispose();
});

test('F17 (Codex L01): a reinstalled app\'s widget mounts with its own new stylesheet, not the one left by the old version', async () => {
    const { readFile } = await import('node:fs/promises');
    const { homeDocument } = await import('../fixtures/home_dom.mjs');
    const source = await readFile(new URL('../../face/js/os_widgets.js', import.meta.url), 'utf8');
    const doc = homeDocument(); doc.head = doc.createElement('head');
    let version = 1;
    const widget = { instance_id: `wdg_${'a'.repeat(32)}`, widget_id: 'demo.timer', config_version: 1, size: 'compact', calendars: [] };
    let stored = { ...initial(), widgets: [widget] };
    // The real class, with synthetic catalog tables standing for an installed app.
    const Host = new Function('WIDGET_CATALOG', 'OS_WIDGET_MODULES', 'OS_WIDGET_STYLES', 'renderWidgetPreview', 'widgetLayout',
        `${source.slice(source.indexOf('export class DesktopWidgets')).replace('export class', 'class')}\nreturn DesktopWidgets;`)(
        { 'demo.timer': { name: 'Demo', app: 'demo', configVersion: 1, size: 'compact' } },
        { 'demo.timer': async () => ({ createWidget: ({ root }) => { root.textContent = `v${version}`; return { setVisibility() {}, configure() {}, dispose() {} }; } }) },
        { 'demo.timer': async () => ({ default: `.demo { color: ${version === 1 ? 'red' : 'green'}; }` }) }, () => doc.createElement('div'), widgetLayout);
    const host = new Host({ root: doc.createElement('section'), documentLike: doc, isVisible: () => false,
        fetchImpl: async () => ({ ok: true, json: async () => structuredClone(stored) }) });
    await host.load(); await flush();
    assert.deepEqual(doc.head.children.map((node) => node.textContent), ['.demo { color: red; }']);
    stored = { ...initial(), revision: 1 };
    await host.forgetApps(['demo']); // uninstalled from Iris Store: its card and its stylesheet go
    assert.equal(host.cards.size, 0);
    assert.deepEqual(doc.head.children, []);
    version = 2; stored = { ...initial(), revision: 2, widgets: [widget] }; // installed again and the widget added back
    host.restoreApps(['demo']);
    await host.load(); await flush();
    assert.deepEqual(doc.head.children.map((node) => node.textContent), ['.demo { color: green; }']);
    host.dispose();
});
