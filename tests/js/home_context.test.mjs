import test from 'node:test';
import assert from 'node:assert/strict';
import { DesktopHome, HomePreferences } from '../../face/js/os_desktop_home.js';
import { DesktopWidgets } from '../../face/js/os_widgets.js';
import { renderWidgetPreview } from '../../face/js/os_widget_preview.js';
import { homeDocument } from '../fixtures/home_dom.mjs';

async function setup({ shortcuts = ['calendar'] } = {}) {
    const doc = homeDocument(), make = (tag, cls, parent) => { const n = doc.createElement(tag); n.className = cls; parent.append(n); return n; };
    const panel = make('main', 'hermes-workbench', doc.body), screen = make('section', 'os-home-screen', panel), root = make('section', 'os-widgets', screen);
    const drawer = make('aside', 'os-home-launcher', panel); make('header', '', drawer); make('div', 'os-launcher-grid', drawer);
    const entries = [{ id: 'calendar', name: 'Calendario', icon: 'calendar', widgets: [{ id: 'calendar.month', name: 'Calendario' }] }, { id: 'mail', name: 'Correo', icon: 'mail' }];
    let homeSaved = { schema: 'iris.desktop-home/v1', revision: 0, shortcuts, hidden_apps: [], order: [] };
    let widgetsSaved = { schema: 'iris.desktop-widgets/v1', revision: 0, side: 'right', widgets: [] };
    const calls = [], opened = [], mounts = [];
    const fetchImpl = async (url, init) => {
        calls.push({ url, ...init }); let value;
        if (url.endsWith('/home')) { if (init.method === 'PUT') homeSaved = { ...JSON.parse(init.body), revision: homeSaved.revision + 1 }; value = homeSaved; }
        else { if (init.method === 'PUT') widgetsSaved = { ...JSON.parse(init.body), revision: widgetsSaved.revision + 1 }; value = widgetsSaved; }
        return { ok: true, json: async () => structuredClone(value) };
    };
    const model = new HomePreferences({ fetchImpl });
    let home; const widgets = new DesktopWidgets({ root, documentLike: doc, integrated: true, fetchImpl, isVisible: () => false,
        factories: { 'calendar.month': () => { mounts.push('calendar'); return { dispose() {}, setVisibility() {} }; } }, onChange: () => home?.syncWidgets() });
    home = new DesktopHome({ root, drawer, model, widgets, documentLike: doc, icon: () => '<svg></svg>', openApp: (id) => opened.push(id),
        registry: { get: (id) => entries.find((e) => e.id === id), launcher: () => entries } });
    await Promise.all([model.load(), widgets.load()]);
    const menuAction = (name) => home.menu.querySelectorAll('button').find((n) => n.textContent.startsWith(name));
    const dispose = () => { home.dispose(); widgets.dispose(); model.dispose(); };
    return { doc, home, widgets, model, root, drawer, screen, panel, calls, opened, mounts, menuAction, dispose };
}

test('tiles have no ellipsis; click opens and right click or Shift+F10 opens the app menu without launching', async () => {
    const h = await setup(), launch = h.root.querySelector('[data-home-open="calendar"]');
    assert.equal(h.root.querySelector('.os-home-options'), null); assert.equal(h.drawer.querySelector('.os-home-options'), null);
    launch.click(); assert.deepEqual(h.opened, ['calendar']);
    launch.emit('contextmenu', { clientX: 60, clientY: 70 }); assert.equal(h.home.menu.getAttribute('aria-label'), 'Opciones de Calendario');
    assert.match(h.menuAction('Ocultar').textContent, /continuarán/);
    assert.equal(h.home.menu.style.left, '60px');
    h.home.menu.emit('keydown', { key: 'Escape' }); assert.equal(h.home.menu, null); assert.equal(h.doc.activeElement, launch);
    launch.emit('keydown', { key: 'F10', shiftKey: true }); assert.ok(h.home.menu); assert.deepEqual(h.opened, ['calendar']); h.dispose();
});
test('empty Home offers the three icon actions; editing has Listo and retains keyboard ordering controls', async () => {
    const h = await setup();
    h.widgets.list.emit('contextmenu');
    assert.deepEqual(h.home.menu.querySelectorAll('.os-home-menu-label').map((n) => n.textContent.split('Explora')[0].split('Mueve')[0]), ['Widgets', 'Editar inicio', 'Administrar aplicaciones']);
    assert.equal(h.home.menu.querySelectorAll('.os-home-menu-icon').length, 3);
    h.menuAction('Editar inicio').click(); assert.equal(h.home.editing, true); assert.equal(h.home.editButton.textContent, 'Listo');
    // Compact icon buttons that fit a dock-sized icon; the whole item drags, so there is no Mover handle.
    assert.equal(h.root.querySelector('[data-home-drag]'), null);
    assert.deepEqual(h.root.querySelectorAll('.os-home-control').map((n) => n.getAttribute('aria-label')),
        ['Anterior: Calendario', 'Siguiente: Calendario', 'Quitar: Calendario']);
    assert.ok(h.root.querySelector('[data-home-focus="remove:app:calendar"]'));
    h.home.editButton.click(); assert.equal(h.home.editing, false); assert.equal(h.home.editButton.textContent, 'Personalizar');
    h.home.editButton.click(); h.menuAction('Administrar').click(); assert.deepEqual(h.opened, ['app-manager']); h.dispose();
});
test('icons drag directly outside edit mode: the order shifts live, stays while saving and the release opens nothing', async () => {
    const frames = [globalThis.requestAnimationFrame, globalThis.cancelAnimationFrame];
    globalThis.requestAnimationFrame = () => 1; globalThis.cancelAnimationFrame = () => {};
    const h = await setup({ shortcuts: ['calendar', 'mail'] }), list = h.widgets.list;
    const order = () => list.children.map((n) => n.dataset.homeRef);
    const [calendar, mail] = list.children, launch = calendar.querySelector('[data-home-open="calendar"]');
    const pointer = (extra = {}) => ({ button: 0, isPrimary: true, pointerId: 1, pointerType: 'mouse', clientX: 10, clientY: 10, ...extra });
    assert.equal(h.home.editing, false);
    h.doc.elementFromPoint = () => mail.querySelector('strong');
    launch.emit('pointerdown', pointer()); h.doc.emit('pointermove', pointer({ clientX: 60 }));
    assert.deepEqual(order(), ['app:mail', 'app:calendar']);
    assert.equal(h.calls.filter((c) => c.method === 'PUT').length, 0);
    h.doc.emit('pointerup', pointer({ clientX: 60 }));
    h.home.syncWidgets(); assert.deepEqual(order(), ['app:mail', 'app:calendar'], 'no snap back while Core saves');
    launch.click(); assert.deepEqual(h.opened, [], 'releasing over the icon does not open it');
    await new Promise((resolve) => setTimeout(resolve, 0));
    const puts = h.calls.filter((c) => c.method === 'PUT');
    assert.equal(puts.length, 1); assert.deepEqual(JSON.parse(puts[0].body).order, ['app:mail', 'app:calendar']);
    assert.deepEqual(order(), ['app:mail', 'app:calendar']); assert.match(h.home.message.textContent, /guardado/);
    launch.click(); assert.deepEqual(h.opened, ['calendar']); h.dispose();
    [globalThis.requestAnimationFrame, globalThis.cancelAnimationFrame] = frames;
});
test('widget controls, edit handles and other desktop surfaces keep their own context interactions', async () => {
    const h = await setup(); h.home.toggleEditing();
    h.root.querySelector('.os-home-control').emit('contextmenu'); assert.equal(h.home.menu, null);
    h.widgets.openEditor({ anchor: h.home.editButton }); h.widgets.editor.emit('contextmenu'); assert.equal(h.home.menu, null);
    h.home.toolbar.emit('contextmenu'); assert.equal(h.home.menu, null); h.dispose();
});
test('the gallery is passive until explicit Add; app-specific preview never mounts the widget just by opening', async () => {
    const h = await setup(); const before = h.calls.length;
    h.root.querySelector('[data-home-open="calendar"]').emit('contextmenu'); h.menuAction('Agregar widget').click();
    assert.equal(h.widgets.editing, true); assert.equal(h.widgets.filterWidget, 'calendar.month');
    assert.equal(h.widgets.editor.querySelectorAll('.os-widget-preview').length, 1);
    assert.equal(h.calls.length, before); assert.deepEqual(h.mounts, []);
    const add = h.widgets.editor.querySelector('[data-widget-key="add:calendar.month"]'); add.focus(); add.click();
    await new Promise((r) => setImmediate(r));
    assert.deepEqual(h.mounts, ['calendar']); assert.equal(h.calls.filter((c) => c.method === 'PUT').length, 1);
    assert.equal(h.widgets.layout.widgets.length, 1);
    assert.equal(h.doc.activeElement.dataset.widgetKey, 'add:calendar.month');
    h.widgets.editor.emit('keydown', { key: 'Escape' }); assert.equal(h.widgets.editing, false); assert.equal(h.doc.activeElement, h.home.editButton); h.dispose();
});
test('gallery limits are authoritative; a full home still lets the user browse previews and remove an instance', async () => {
    const h = await setup(); for (let n = 0; n < 4; n++) await h.widgets.add('calendar.month');
    h.home.openWidgets(); const before = h.calls.length;
    const adds = h.widgets.editor.querySelectorAll('button').filter((n) => n.dataset.widgetKey?.startsWith('add:'));
    assert.equal(adds.length, 2); assert.ok(adds.every((n) => n.disabled)); adds[0].click(); assert.equal(h.calls.length, before);
    await h.widgets.remove(h.widgets.layout.widgets[0].instance_id);
    assert.equal(h.widgets.editor.querySelector('[data-widget-key="add:calendar.month"]').disabled, false);
    h.dispose();
});
test('hiding Home closes transient menus and gallery without discarding edit state or stored preferences', async () => {
    const h = await setup(); h.home.toggleEditing(); h.home.openHomeMenu(); h.home.setVisibility(false);
    assert.equal(h.home.menu, null); assert.equal(h.home.editing, true);
    h.home.setVisibility(true); h.home.openWidgets(); h.home.setVisibility(false); assert.equal(h.widgets.editing, false);
    assert.equal(h.calls.filter((c) => c.method === 'PUT').length, 0); h.dispose();
});
test('an open menu refreshes enabled actions after a late preference read without replacing its buttons', async () => {
    const h = await setup(); let finish;
    const saved = structuredClone(h.model.layout);
    h.model.fetchImpl = () => new Promise((resolve) => { finish = () => resolve({ ok: true, json: async () => saved }); });
    const loading = h.model.load(); h.home.openHomeMenu(); const edit = h.menuAction('Editar inicio');
    assert.equal(edit.disabled, true); finish(); await loading;
    assert.equal(h.menuAction('Editar inicio'), edit); assert.equal(edit.disabled, false);
    edit.click(); assert.equal(h.home.editing, true); h.dispose();
});
test('unknown previews have a passive fallback; all sample strings remain text, never markup or interactive controls', () => {
    const doc = homeDocument();
    for (const preview of [undefined, { layout: 'summary', title: '<img src="https://outside.invalid">', items: [{ label: '<script>', detail: 'Only sample' }] }]) {
        const node = renderWidgetPreview(doc, { name: 'Ejemplo', preview });
        assert.equal(node.querySelector('button'), null); assert.equal(node.querySelector('img'), null);
        assert.equal(node.getAttribute('role'), 'img'); assert.match(node.textContent, /datos de ejemplo/);
        if (preview) assert.match(node.textContent, /<img/);
    }
});
