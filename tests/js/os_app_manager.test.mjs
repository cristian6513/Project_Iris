import test from 'node:test';
import assert from 'node:assert/strict';
import { createApp } from '../../face/js/os_app_manager.js';

test('manager keeps keyboard focus through a delayed visibility save and leaves unrelated functions alone', async () => {
    const previous = globalThis.document;
    const doc = { activeElement: null, createElement: (tag) => new Node(tag) };
    class Node {
        constructor(tag) { this.tag = tag; this.children = []; this.dataset = {}; this.classList = { add() {} }; this.events = {}; }
        append(...nodes) { this.children.push(...nodes); }
        replaceChildren(...nodes) { this.children = nodes; }
        setAttribute() {}
        all() { return [this, ...this.children.flatMap((node) => node.all())]; }
        contains(node) { return this.all().includes(node); }
        querySelectorAll() { return this.all().filter((node) => node.dataset.managerKey); }
        addEventListener(name, fn) { this.events[name] = fn; }
        focus() { if (!this.disabled) doc.activeElement = this; }
    }
    let notify, finish, reads = 0;
    const model = { layout: { shortcuts: [], hidden_apps: [] }, busy: false, error: '',
        subscribe(fn) { notify = fn; return () => { notify = null; }; },
        async load() { reads++; },
        async hide(id, hidden) {
            this.busy = true; notify(); await new Promise((resolve) => { finish = resolve; });
            this.layout = { ...this.layout, hidden_apps: hidden ? [id] : [] }; this.busy = false; notify();
        },
    };
    globalThis.document = doc;
    try {
        const root = new Node('div'), opened = [];
        const registry = { list: () => [{ id: 'calendar', name: 'Calendario', launcher: { visible: true } }], get: () => true };
        const app = createApp({ desktopHome: model, appRegistry: registry, openApp: (id) => opened.push(id) });
        app.mount(root); app.setVisibility({ visible: true });
        const find = (key) => root.querySelectorAll().find((node) => node.dataset.managerKey === key);
        const hide = find('hide:calendar'); hide.focus(); hide.events.click();
        assert.equal(find('hide:calendar').disabled, true);
        finish(); await new Promise((resolve) => setImmediate(resolve));
        assert.equal(doc.activeElement, find('hide:calendar'));
        assert.equal(doc.activeElement.textContent, 'Mostrar en el cajón');
        assert.deepEqual(model.layout.shortcuts, []);
        const stable = find('hide:calendar'); app.refresh(); assert.equal(find('hide:calendar'), stable);
        find('accounts:calendar').events.click(); assert.deepEqual(opened, ['conexiones']);
        assert.equal(reads, 1); app.dispose(); assert.equal(notify, null);
    } finally { globalThis.document = previous; }
});

test('F17: an app installed or removed from Iris Store shows up in the manager without reopening it', () => {
    const previous = globalThis.document;
    class Node {
        constructor(tag) { this.tag = tag; this.children = []; this.dataset = {}; this.classList = { add() {} }; this.events = {}; this.textContent = ''; }
        append(...nodes) { this.children.push(...nodes); }
        replaceChildren(...nodes) { this.children = nodes; }
        setAttribute() {}
        all() { return [this, ...this.children.flatMap((node) => node.all())]; }
        contains(node) { return this.all().includes(node); }
        querySelectorAll() { return this.all().filter((node) => node.dataset.managerKey); }
        addEventListener(name, fn) { this.events[name] = fn; }
        focus() {}
    }
    globalThis.document = { activeElement: null, createElement: (tag) => new Node(tag) };
    try {
        const apps = [{ id: 'calendar', name: 'Calendario', launcher: { visible: true } }];
        let revision = 0;
        const registry = { list: () => apps, get: (id) => apps.find((app) => app.id === id), get revision() { return revision; } };
        const installed = new Map();
        const model = { layout: { shortcuts: [], hidden_apps: [] }, busy: false, error: '', subscribe: () => () => {}, load: async () => {} };
        const opened = [];
        const app = createApp({ desktopHome: model, appRegistry: registry, installedApps: installed, openApp: (id) => opened.push(id) });
        const root = new Node('div');
        app.mount(root); app.setVisibility({ visible: true });
        const names = () => root.all().filter((node) => node.tag === 'h3').map((node) => node.textContent);
        assert.deepEqual(names(), ['Calendario']);
        apps.push({ id: 'enfoque', name: 'Enfoque', launcher: { visible: true } }); installed.set('enfoque', { version: '0.1.0' }); revision++;
        app.refresh();
        assert.deepEqual(names(), ['Calendario', 'Enfoque'], 'the same layout, a new catalog revision: redrawn');
        assert.ok(root.all().some((node) => node.textContent === 'Instalada desde archivo · versión 0.1.0'));
        root.querySelectorAll().find((node) => node.dataset.managerKey === 'store:enfoque').events.click();
        assert.deepEqual(opened, ['store']);
    } finally {
        globalThis.document = previous;
    }
});
