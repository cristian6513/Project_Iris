import test from 'node:test';
import assert from 'node:assert/strict';
import { BackgroundTaskBar, normalizeBackgroundTasks } from '../../face/js/background_task_bar.js';

class Node {
    constructor(tag) { Object.assign(this, { tagName: tag.toUpperCase(), children: [], listeners: {}, attributes: {},
        dataset: {}, textContent: '', className: '', disabled: false, parentNode: null }); }
    append(...nodes) { for (const node of nodes) { node.parentNode = this; this.children.push(node); } }
    insertBefore(node, before) { node.parentNode = this; this.children.splice(this.children.indexOf(before), 0, node); }
    remove() { if (this.parentNode) this.parentNode.children = this.parentNode.children.filter(c => c !== this); this.parentNode = null; }
    setAttribute(name, value) { this.attributes[name] = String(value); }
    addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
    click() { if (!this.disabled) (this.listeners.click || []).forEach(fn => fn({})); }
}
const doc = { createElement: tag => new Node(tag) };
const ID = 'a'.repeat(32);
const task = (state = 'running', extra = {}) => ({ tasks: [{ id: ID, title: 'revisar noticias', state, ...extra }] });

test('only well-formed active tasks are shown, without request or result text', () => {
    assert.deepEqual(normalizeBackgroundTasks({ tasks: [
        { id: ID, title: ' revisar noticias ', state: 'running', request: 'privado', result: 'x' },
        { id: 'no-hex', title: 't', state: 'running' }, { id: 'b'.repeat(32), title: 't', state: 'completed' },
        { id: 'c'.repeat(32), title: '', state: 'running' }] }),
    [{ id: ID, title: 'revisar noticias', state: 'running' }]);
    assert.deepEqual(normalizeBackgroundTasks(null), []);
});

test('the bar says what Iris is working on, goes above the input and leaves when it ends', () => {
    const host = new Node('div'), input = new Node('div');
    host.append(input);
    const bar = new BackgroundTaskBar({ host, before: input, doc });
    bar.update(task());
    assert.equal(host.children[0], bar.node);
    assert.equal(bar.text.textContent, 'Trabajando en: revisar noticias');
    assert.equal(bar.node.attributes.role, 'status');
    assert.equal(bar.button.attributes['aria-label'], 'Cancelar la tarea en segundo plano «revisar noticias»');
    bar.update({ tasks: [] });
    assert.equal(bar.node, null);
    assert.deepEqual(host.children, [input]);
});

test('Cancelar asks Core once for that task and shows it is stopping', () => {
    const asked = [], host = new Node('div');
    const bar = new BackgroundTaskBar({ host, doc, onCancel: id => asked.push(id) });
    bar.update(task());
    bar.button.click();
    bar.button.click();
    assert.deepEqual(asked, [ID]);
    assert.equal(bar.button.disabled, true);
    assert.equal(bar.text.textContent, 'Cancelando: revisar noticias…');
    bar.update(task('running'));  // a refresh before Core answers keeps the stopping look
    assert.equal(bar.node.dataset.state, 'stopping');
    bar.update(task('stopping'));
    assert.equal(bar.text.textContent, 'Cancelando: revisar noticias…');
});

test('titles are text, never markup', () => {
    const bar = new BackgroundTaskBar({ host: new Node('div'), doc });
    bar.update({ tasks: [{ id: ID, title: '<img src=x onerror=alert(1)>', state: 'running' }] });
    assert.equal(bar.text.textContent, 'Trabajando en: <img src=x onerror=alert(1)>');
    assert.equal(bar.text.children.length, 0);
});

test('a start still being confirmed says so and can be cancelled', () => {
    const asked = [];
    const bar = new BackgroundTaskBar({ host: new Node('div'), doc, onCancel: id => asked.push(id) });
    bar.update(task('uncertain'));
    assert.equal(bar.text.textContent, 'Comprobando si empezó: revisar noticias');
    bar.button.click();
    assert.deepEqual(asked, [ID]);
    assert.equal(bar.text.textContent, 'Cancelando: revisar noticias…');
});

test('an unresolved start is released only after a second, explained click', () => {
    const released = [], cancelled = [];
    const bar = new BackgroundTaskBar({ host: new Node('div'), doc, onRelease: id => released.push(id),
        onCancel: id => cancelled.push(id) });
    bar.update(task('unresolved'));
    assert.equal(bar.text.textContent, 'Sin confirmar: «revisar noticias» puede seguir en el motor de IA');
    assert.equal(bar.button.textContent, 'Liberar');
    bar.button.click();
    assert.deepEqual(released, []);
    assert.equal(bar.text.textContent,
        'Si «revisar noticias» empezó, puede seguir en el motor de IA. ¿Liberar para otras tareas?');
    assert.equal(bar.button.textContent, 'Sí, liberar');
    bar.update(task('unresolved'));  // a refresh keeps the question open
    bar.button.click();
    assert.deepEqual(released, [ID]);
    assert.deepEqual(cancelled, []);
});
