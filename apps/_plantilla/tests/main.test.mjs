// Tests of the template app: run with `node --test apps/<id>/tests/*.test.mjs`.
// They find elements by data-part, so renaming the app's classes does not break them.
// A tiny stand-in for the DOM is enough to check what the app shows and when it works.
import { test } from 'node:test';
import assert from 'node:assert/strict';

class Node {
    constructor(tag) { Object.assign(this, { tag, children: [], listeners: {}, attributes: {}, dataset: {}, textContent: '', className: '', value: '' }); }
    append(...nodes) { this.children.push(...nodes); }
    replaceChildren(...nodes) { this.children = nodes; }
    setAttribute(name, value) { this.attributes[name] = String(value); }
    getAttribute(name) { return this.attributes[name] ?? null; }
    addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
    fire(type) { for (const fn of this.listeners[type] || []) fn({ type, target: this }); }
    focus() { globalThis.document.activeElement = this; }
    all() { return [this, ...this.children.flatMap((child) => child.all())]; }
    find(part) { return this.all().find((node) => node.dataset.part === part); }
}
globalThis.document = { activeElement: null, createElement: (tag) => new Node(tag) };

const { createApp, filterItems, briefText, EXAMPLE_ITEMS } = await import('../ui/main.js');

test('the search keeps the elements that contain every word', () => {
    assert.deepEqual(filterItems(EXAMPLE_ITEMS, 'segundo texto').map((item) => item.id), ['dos']);
    assert.equal(filterItems(EXAMPLE_ITEMS, 'nada así').length, 0);
    assert.equal(briefText([1]), '1 elemento');
});

test('it mounts, loads on open, tells the launcher, navigates and retires cleanly', async () => {
    const briefs = [];
    const app = createApp({ setBrief: (text) => briefs.push(text) });
    const root = new Node('div');
    app.mount(root);
    assert.equal(root.find('status').textContent, 'Cargando…');
    await app.refresh({ opening: true });
    assert.equal(root.find('list').children.length, 3);
    assert.deepEqual(briefs, ['3 elementos']);
    app.navigate({ item: 'dos' });
    assert.match(root.find('detail').textContent, /Otro dato de ejemplo/);
    // Choosing with the keyboard redraws the list; the chosen element keeps the focus.
    root.find('list').children[0].fire('click');
    assert.equal(globalThis.document.activeElement, root.find('list').children[0]);
    assert.equal(globalThis.document.activeElement.getAttribute('aria-pressed'), 'true');
    const search = root.find('search');
    search.value = 'tercer';
    search.fire('input');
    assert.equal(root.find('list').children.length, 1);
    app.setVisibility({ visible: true });
    app.setVisibility({ visible: false }); // hidden: its clock stops
    app.dispose();
    assert.equal(root.children.length, 0);
});
