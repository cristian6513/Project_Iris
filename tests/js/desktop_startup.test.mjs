import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { setImmediate } from 'node:timers/promises';

test('a room keeps its load while hidden and expires only after five visible minutes', async t => {
    t.mock.timers.enable({ apis: ['setTimeout'] });
    class Element extends EventTarget {
        hidden = true;
        inert = false;
        textContent = '';
        src = '';
        contentWindow = {};
        removeAttribute(name) { delete this[name]; }
        contains() { return false; }
    }
    const elements = Object.fromEntries(
        ['workspace', 'welcome', 'message', 'connection', 'log', 'retry', 'setup', 'enter', 'return', 'configure', 'home']
            .map(id => [id, new Element()]),
    );
    const window = new EventTarget();
    const document = Object.assign(new EventTarget(), {
        getElementById: id => elements[id], querySelectorAll: () => [], hidden: false,
    });
    let now = 0;
    const advance = ms => { now += ms; t.mock.timers.tick(ms); };
    const setHidden = hidden => {
        document.hidden = hidden;
        document.dispatchEvent(new Event('visibilitychange'));
    };
    const origin = 'http://127.0.0.1:49152';
    const face = 'http://127.0.0.1:49153';
    const globals = {
        window,
        document,
        location: { origin },
        performance: { now: () => now },
    };
    for (const [key, value] of Object.entries(globals)) {
        const previous = Object.getOwnPropertyDescriptor(globalThis, key);
        Object.defineProperty(globalThis, key, { value, configurable: true });
        t.after(() => {
            if (previous) Object.defineProperty(globalThis, key, previous);
            else delete globalThis[key];
        });
    }

    const requests = [];
    t.mock.method(globalThis, 'fetch', async url => {
        requests.push(url);
        return { ok: true, json: async () => ({ phase: 'ready', face_url: face + '/face/' }) };
    });
    const desktop = await readFile(new URL('../../wizard/ui/desktop.js', import.meta.url), 'utf8');
    const theme = 'data:text/javascript,' + encodeURIComponent(
        'export const applyThemeSnapshot = () => {}, receiveFaceTheme = () => {}, receiveWizardPreset = () => {}, setThemeView = () => {};',
    );
    const script = desktop.replace("'./desktop_theme.js'", JSON.stringify(theme));
    await import('data:text/javascript;base64,' + Buffer.from(script).toString('base64'));
    await setImmediate();
    t.after(() => window.dispatchEvent(new Event('pagehide')));

    const { workspace, welcome, retry, message } = elements;
    const src = workspace.src;
    const load = new URL(src).searchParams.get('desktop_load');
    assert.ok(load);
    assert.equal(workspace.inert, true);

    advance(45_000);
    setHidden(true);
    advance(600_000);
    await setImmediate();
    assert.equal(workspace.src, src);
    assert.equal(retry.hidden, true);
    setHidden(false);

    advance(14_999);
    await setImmediate();
    assert.equal(retry.hidden, true);
    advance(1);
    assert.equal(retry.hidden, false);
    assert.match(message.textContent, /Sigue cargando/);
    assert.equal(workspace.src, src);
    assert.equal(workspace.hidden, false);

    const count = requests.length;
    retry.dispatchEvent(new Event('click'));
    await setImmediate();
    assert.deepEqual(requests.slice(count), ['/desktop/state']);
    assert.equal(workspace.src, src);

    const sendReady = id => window.dispatchEvent(Object.assign(new Event('message'), {
        source: workspace.contentWindow, origin: face, data: { type: 'iris.desktop.face-state', id, status: 'ready' },
    }));
    sendReady('outdated');
    assert.equal(welcome.hidden, false);
    advance(10_000);
    await setImmediate();
    sendReady(load);
    assert.equal(welcome.hidden, true);
    assert.equal(workspace.inert, false);
    assert.equal(workspace.src, src);

    elements.home.dispatchEvent(new Event('click'));
    elements.enter.dispatchEvent(new Event('click'));
    await setImmediate();
    const stuck = workspace.src;
    assert.notEqual(stuck, src);
    advance(60_000);
    advance(60_000);
    setHidden(true);
    advance(600_000);
    await setImmediate();
    assert.equal(workspace.src, stuck);
    assert.match(message.textContent, /Sigue cargando/);
    setHidden(false);

    advance(179_999);
    await setImmediate();
    assert.equal(workspace.src, stuck);
    advance(1);
    assert.equal(workspace.src, undefined);
    assert.equal(retry.hidden, false);
    assert.match(message.textContent, /5 minutos/);

    retry.dispatchEvent(new Event('click'));
    await setImmediate();
    assert.ok(workspace.src);
    assert.notEqual(workspace.src, stuck);
    assert.equal(requests.filter(url => url === '/desktop/start').length, 3);
});
