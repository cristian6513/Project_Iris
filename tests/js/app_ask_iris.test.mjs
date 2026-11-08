/** «Avisar a Iris»: a real click, Iris' own preview, and what reaches the chat. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { askIrisPreview, askIrisRequest, askIrisSender, askIrisTrust } from '../../face/js/app_ask_iris.js';

class Storage {
    constructor() { this.values = new Map(); }
    getItem(key) { return this.values.get(key) ?? null; }
    setItem(key, value) { this.values.set(key, String(value)); }
}

test('an app sends a short line and small plain JSON, never a command', () => {
    assert.deepEqual(askIrisRequest({ text: '  Te toca ' }), { text: 'Te toca' });
    assert.deepEqual(askIrisRequest({ text: 'Te toca', data: { fen: 'x', at: new Date(0) } }),
        { text: 'Te toca', data: { fen: 'x', at: '1970-01-01T00:00:00.000Z' } }, 'plain JSON, as Core will read it');
    for (const bad of [{}, { text: '' }, { text: 'x'.repeat(301) }, { text: '/reset' }, { text: 'Hola', data: 'x'.repeat(5000) },
        { text: 'Hola', data: () => 1 }]) {
        assert.throws(() => askIrisRequest(bad), TypeError, JSON.stringify(bad));
    }
    const cycle = {}; cycle.self = cycle;
    assert.throws(() => askIrisRequest({ text: 'Hola', data: cycle }));
});

test("the shell checks the data as Core will, so an approved message is not refused after the dialog", () => {
    // Fits in compact UTF-8 (2 bytes per ñ) but not in Core's measure (6 per ñ, spaces after , and :).
    assert.ok(new TextEncoder().encode(JSON.stringify({ t: 'ñ'.repeat(1000) })).length < 4096);
    for (const data of [{ t: 'ñ'.repeat(1000) }, { t: '😀'.repeat(400) }, Array.from({ length: 129 }, () => 0),
        Object.fromEntries(Array.from({ length: 65 }, (_, i) => [`k${i}`, 0])), [[[[[[[[[1]]]]]]]]], { n: 1e16 },
        { [`k${'x'.repeat(120)}`]: 1 }]) {
        assert.throws(() => askIrisRequest({ text: 'Hola', data }), TypeError, JSON.stringify(data).slice(0, 60));
    }
    assert.ok(askIrisRequest({ text: 'Hola', data: [[[[[[[[1]]]]]]]] }), 'eight levels are fine');
    assert.throws(() => askIrisRequest({ text: 'Tab\tdentro' }), TypeError, 'Core refuses control characters');
    assert.deepEqual(askIrisRequest({ text: 'Dos\nlíneas' }), { text: 'Dos\nlíneas' });
    assert.ok(askIrisRequest({ text: '😀'.repeat(300) }), 'Core counts characters, not UTF-16 units');
});

test("an app hears «sent» only when Core takes the message, and Core's reason when it refuses", async () => {
    const wire = [], chat = [], timers = new Map();
    let ids = 0, connected = true;
    const sender = askIrisSender({
        send: (message) => connected && wire.push(message) > 0,
        onAccepted: (...args) => chat.push(args),
        newId: () => String(++ids).padStart(32, 'a'),
        setTimer: (run) => { const id = Symbol('timer'); timers.set(id, run); return id; },
        clearTimer: (id) => timers.delete(id),
    });
    const ask = (text, data) => sender.send('iris-chess', 'Iris Chess', { text, ...(data ? { data } : {}) });

    const taken = ask('Te toca', { ultima: 'e2e4' });
    assert.deepEqual(wire[0], { request_id: `${'a'.repeat(31)}1`, app_id: 'iris-chess', text: 'Te toca', data: { ultima: 'e2e4' } });
    assert.equal(chat.length, 0, 'nothing in the chat before Core answers');
    sender.acknowledge({ request_id: wire[0].request_id, accepted: true });
    assert.deepEqual(await taken, { sent: true });
    assert.deepEqual(chat, [['iris-chess', 'Iris Chess', { text: 'Te toca', data: { ultima: 'e2e4' } }]]);

    const refused = ask('Desde otro equipo');
    sender.acknowledge({ request_id: wire[1].request_id, accepted: false, message: '«Avisar a Iris» solo funciona en este equipo.' });
    assert.deepEqual(await refused, { sent: false, reason: 'refused', message: '«Avisar a Iris» solo funciona en este equipo.' });
    sender.acknowledge({ request_id: wire[1].request_id, accepted: true });
    assert.equal(chat.length, 1, 'a late or repeated answer changes nothing');

    const silent = ask('Sin respuesta');
    assert.equal(timers.size, 1);
    [...timers.values()][0]();
    assert.deepEqual(await silent, { sent: false, reason: 'offline' });
    timers.clear();
    sender.acknowledge({ request_id: wire[2].request_id, accepted: true });
    assert.deepEqual(chat.at(-1)?.[2], { text: 'Sin respuesta' }, 'Core took it late: the chat still shows what Iris got');
    sender.acknowledge({ request_id: wire[2].request_id, accepted: true });
    assert.equal(chat.length, 2);

    connected = false;
    assert.deepEqual(await ask('Sin conexión'), { sent: false, reason: 'offline' });
    assert.equal(timers.size, 0);
});

test('the preview names the app, the line, the data and how Iris takes it', () => {
    const preview = askIrisPreview('Iris Chess', { text: 'Te toca', data: { ultima: 'e2e4' } });
    assert.equal(preview.message, 'Iris Chess enviará a Iris, en la conversación actual: «Te toca», con datos de la app. '
        + 'Iris lo recibe como información de la app, no como órdenes.');
    assert.deepEqual(preview.details, { label: 'Ver los datos de la app', text: '{\n  "ultima": "e2e4"\n}' });
    assert.equal(askIrisPreview('X', { text: 'a' }).details, undefined, 'no data, nothing to unfold');
    // Everything that will be sent can be read first: the details scroll (Codex SDK04).
    const long = { blob: 'y'.repeat(3900), final: 'campo final' };
    assert.equal(askIrisPreview('X', { text: 'a', data: long }).details.text, JSON.stringify(long, null, 2));
});

test('trusting an app is remembered per browser and can be undone', () => {
    const storage = new Storage();
    assert.equal(askIrisTrust.has('iris-chess', storage), false);
    askIrisTrust.set('iris-chess', true, storage);
    assert.deepEqual(askIrisTrust.list(storage), ['iris-chess']);
    askIrisTrust.set('iris-chess', false, storage);
    assert.deepEqual(askIrisTrust.list(storage), []);
    storage.setItem('irisOs.askIris.trusted.v1', '{"no": "lista"}');
    assert.deepEqual(askIrisTrust.list(storage), [], 'a damaged value trusts nobody');
});

async function shell(deps) {
    const source = await readFile(new URL('../../face/js/hermes_activity.js', import.meta.url), 'utf8');
    const grab = (name) => source.match(new RegExp(`^    (?:async )?${name}\\([^]*?^    \\}`, 'm'))[0];
    const names = Object.keys(deps);
    const code = ['_appAskIris', '_takeAppGesture', '_watchAppGestures'].map(grab).join(',\n');
    return new Function(...names, `return ({${code}})`)(...names.map((name) => deps[name]));
}

/**
 * A trusted click (as the browser makes it) on a node inside one surface of the desktop. A mouse
 * click has `detail` 1; the click a browser fires for Enter or Space has 0.
 */
function clickIn(listeners, surface, { trusted = true, type = 'click', key = '', detail = type === 'click' ? 1 : 0,
    repeat = false } = {}) {
    const target = { closest: () => surface };
    for (const listener of listeners[type] || []) listener({ isTrusted: trusted, type, key, detail, repeat, target });
}

test("one click in the app sends one message, after Iris' dialog unless the person trusts the app (Codex SDK02)", async () => {
    const storage = new Storage(), asked = [], sent = [], listeners = {};
    const trust = { has: (id) => askIrisTrust.has(id, storage), set: (id, value) => askIrisTrust.set(id, value, storage) };
    let choice = 'once';
    const methods = await shell({ OS_APPS: { get: (id) => (id === 'iris-chess' ? { name: 'Iris Chess' } : null) },
        askIrisRequest, askIrisPreview, askIrisTrust: trust, APP_GESTURE_MS: 3000,
        irisChoice: async (options) => { asked.push(options); return choice; } });
    const view = { ...methods, onAskIris: (...args) => { sent.push(args); return Promise.resolve({ sent: true }); } };
    view._watchAppGestures({ addEventListener: (type, listener, capture) => { assert.equal(capture, true); (listeners[type] ||= []).push(listener); } });
    const chess = { dataset: { appView: 'iris-chess' } }, widget = { dataset: { widgetApp: 'iris-chess' } };
    const desktop = null, other = { dataset: { appView: 'calendar' } };
    const ask = (text = 'Te toca', data) => view._appAskIris('iris-chess', { text, ...(data ? { data } : {}) });

    await assert.rejects(() => ask(), /clic de la persona/, 'no click');
    clickIn(listeners, desktop);
    await assert.rejects(() => ask(), /clic/, 'a click on the desktop is not a click in the app');
    clickIn(listeners, other);
    await assert.rejects(() => ask(), /clic/, 'nor one in another app');
    clickIn(listeners, chess, { trusted: false });
    await assert.rejects(() => ask(), /clic/, 'nor one a script made');
    assert.equal(asked.length, 0);

    clickIn(listeners, chess);
    assert.deepEqual(await ask('Te toca', { ultima: 'e2e4' }), { sent: true });
    assert.equal(asked[0].title, 'Avisar a Iris');
    assert.match(asked[0].message, /Iris Chess enviará a Iris/);
    assert.equal(asked[0].details.label, 'Ver los datos de la app');
    assert.deepEqual(asked[0].choices.map((item) => item.value), ['once', 'always']);
    assert.deepEqual(sent[0], ['iris-chess', 'Iris Chess', { text: 'Te toca', data: { ultima: 'e2e4' } }]);
    await assert.rejects(() => ask('Otra vez'), /clic/, 'the click was spent');

    choice = null;
    clickIn(listeners, chess);
    assert.deepEqual(await ask(), { sent: false, reason: 'cancelled' });
    choice = 'always';
    clickIn(listeners, widget);
    assert.deepEqual(await ask(), { sent: true }, "a click in the app's widget counts too");
    const before = asked.length;
    clickIn(listeners, chess, { type: 'keydown', key: 'Enter' });
    assert.deepEqual(await ask('Con teclado'), { sent: true });
    assert.equal(asked.length, before, 'a trusted app skips the dialog');
    // Trusted or not: a timer reusing one click sends once (the reproduction sent twice).
    clickIn(listeners, chess);
    assert.deepEqual(await ask('Primero'), { sent: true });
    await assert.rejects(() => ask('Desde un temporizador'), /clic/);
    clickIn(listeners, chess, { type: 'keydown', key: 'a' });
    await assert.rejects(() => ask(), /clic/, 'typing is not an action to send');
    assert.equal(sent.length, 4);

    clickIn(listeners, chess);
    const refused = { sent: false, reason: 'refused', message: '«Avisar a Iris» solo funciona en este equipo.' };
    assert.deepEqual(await methods._appAskIris.call({ ...view, onAskIris: async () => refused }, 'iris-chess', { text: 'x' }),
        refused, "the app hears Core's answer");
    clickIn(listeners, chess);
    assert.deepEqual(await methods._appAskIris.call({ ...view, onAskIris: undefined }, 'iris-chess', { text: 'x' }),
        { sent: false, reason: 'offline' });
    await assert.rejects(() => view._appAskIris('otra', { text: 'x' }), /no está disponible/);
});

test("Enter and the click the browser fires for it are one press: one message (Codex SDK02-R1)", async () => {
    const sent = [], listeners = {};
    const methods = await shell({ OS_APPS: { get: () => ({ name: 'Iris Chess' }) }, askIrisRequest, askIrisPreview,
        askIrisTrust: { has: () => true, set() {} }, APP_GESTURE_MS: 3000, irisChoice: async () => 'once' });
    const view = { ...methods, onAskIris: (...args) => { sent.push(args); return { sent: true }; } };
    view._watchAppGestures({ addEventListener: (type, listener) => (listeners[type] ||= []).push(listener) });
    const chess = { dataset: { appView: 'iris-chess' } };
    const ask = (text) => view._appAskIris('iris-chess', { text });
    const enter = (options = {}) => clickIn(listeners, chess, { type: 'keydown', key: 'Enter', ...options });
    const keyClick = (surface = chess) => clickIn(listeners, surface, { detail: 0 });

    // The app sends from its own Enter handler; the browser's click comes after, then a timer.
    enter();
    assert.deepEqual(await ask('Desde Enter'), { sent: true });
    keyClick();
    await assert.rejects(() => ask('Desde un temporizador'), /clic/, 'the derived click renews nothing');

    // A button that sends from its click: the key's gesture is still there for that click.
    enter();
    keyClick();
    assert.deepEqual(await ask('Desde el clic del botón'), { sent: true });
    await assert.rejects(() => ask('Otra vez'), /clic/);

    // Enter in a form: the browser clicks the form's default button, elsewhere in the same app.
    enter();
    assert.deepEqual(await ask('Desde el campo'), { sent: true });
    keyClick({ dataset: { appView: 'iris-chess' } });
    await assert.rejects(() => ask('Envío del formulario'), /clic/);

    // Space clicks when it is released; holding a key down is one press, not many.
    clickIn(listeners, chess, { type: 'keydown', key: ' ' });
    assert.deepEqual(await ask('Con espacio'), { sent: true });
    clickIn(listeners, chess, { type: 'keydown', key: ' ', repeat: true });
    keyClick();
    await assert.rejects(() => ask('Mantenida'), /clic/);
    enter();
    assert.deepEqual(await ask('Mantengo Enter'), { sent: true });
    for (let i = 0; i < 3; i += 1) {
        enter({ repeat: true });
        keyClick();
        await assert.rejects(() => ask('Repetición'), /clic/);
    }

    // A second real press, a mouse click, or an activation without a key each count once.
    enter();
    assert.deepEqual(await ask('Segunda pulsación'), { sent: true });
    clickIn(listeners, chess);
    assert.deepEqual(await ask('Con el ratón'), { sent: true });
    keyClick();
    assert.deepEqual(await ask('Lector de pantalla'), { sent: true }, 'no key before it: a new action of the person');
    assert.equal(sent.length, 8);
});

test('a click in the app expires after a moment', async () => {
    const listeners = {};
    const methods = await shell({ OS_APPS: { get: () => ({ name: 'Iris Chess' }) }, askIrisRequest, askIrisPreview,
        askIrisTrust: { has: () => true, set() {} }, APP_GESTURE_MS: 3000, irisChoice: async () => 'once' });
    const view = { ...methods, onAskIris: async () => ({ sent: true }) };
    view._watchAppGestures({ addEventListener: (type, listener) => (listeners[type] ||= []).push(listener) });
    const realNow = Date.now;
    try {
        let now = 1_000_000;
        Date.now = () => now;
        clickIn(listeners, { dataset: { appView: 'iris-chess' } });
        now += 3001;
        await assert.rejects(() => view._appAskIris('iris-chess', { text: 'Tarde' }), /clic/);
    } finally {
        Date.now = realNow;
    }
});
