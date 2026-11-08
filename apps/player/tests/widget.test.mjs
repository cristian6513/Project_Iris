import test from 'node:test';
import assert from 'node:assert/strict';
import { IrisPlayerApp } from '../../../face/js/iris_player.js';
import { createWidget } from '../ui/widget.js';

class Node {
    constructor(tag) { Object.assign(this, { tag, children: [], dataset: {}, attributes: {}, listeners: {}, textContent: '' }); }
    append(...nodes) { this.children.push(...nodes); }
    replaceChildren(...nodes) { this.children = nodes; }
    setAttribute(key, value) { this.attributes[key] = String(value); }
    addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
    click() { if (!this.disabled) for (const fn of this.listeners.click || []) fn(); }
    all() { return [this, ...this.children.flatMap(node => node.all())]; }
    part(key) { return this.all().find(node => node.dataset.playerWidget === key); }
    text() { return this.all().map(node => node.textContent).join(' '); }
    focus() { doc.activeElement = this; }
}
const doc = { createElement: tag => new Node(tag), activeElement: null };
class Audio extends EventTarget {
    src = ''; paused = true; ended = false; readyState = 0; duration = 120; currentTime = 0;
    plays = 0; pauses = 0; loads = 0; fail = false; delayed = false;
    getAttribute() { return this.src; }
    removeAttribute() { this.src = ''; }
    load() { this.loads++; }
    emit(type, props = {}) { Object.assign(this, props); this.dispatchEvent(new Event(type)); }
    play() {
        this.plays++;
        if (this.fail) return Promise.reject(Error('blocked'));
        if (this.delayed) return new Promise(() => {});
        this.emit('play', { paused: false, readyState: 3 }); this.emit('playing'); return Promise.resolve();
    }
    pause() { this.pauses++; this.emit('pause', { paused: true }); }
}
const library = { configured: true, folder_name: 'Carpeta elegida', tracks: [
    { id: 'one', title: '<img src=x onerror=bad()>', artist: 'Artista local', stream_url: '/api/player/tracks/one/stream' },
    { id: 'two', title: 'Segunda canción', artist: 'Artista local', stream_url: '/api/player/tracks/two/stream' },
] };
const flush = () => new Promise(resolve => setImmediate(resolve));
function setup(t, payload = library) {
    const before = { fetch: globalThis.fetch, localStorage: globalThis.localStorage, window: globalThis.window };
    t.after(() => Object.assign(globalThis, before));
    const calls = [], store = new Map();
    globalThis.localStorage = { getItem: key => store.get(key) ?? null, setItem: (key, value) => store.set(key, value), removeItem: key => store.delete(key) };
    globalThis.window = { clearTimeout, setTimeout: () => 0 };
    globalThis.fetch = async (url, init) => { calls.push({ url, init }); return { ok: true, json: async () => payload }; };
    const audio = new Audio(), root = { innerHTML: '', querySelector: selector => selector === '.iris-player-audio' ? audio : null, addEventListener() {} };
    const owner = new IrisPlayerApp({ root }); const service = owner.widgetService();
    const instances = [];
    function widget() {
        const root = new Node('section'), opened = [], abort = new AbortController();
        const controller = createWidget({ root, services: { player: service }, openApp: id => opened.push(id), signal: abort.signal }, { documentLike: doc });
        instances.push(controller); return { root, controller, opened, abort };
    }
    t.after(() => { for (const widget of instances) widget.dispose(); owner._playbackObserver.dispose(); });
    return { owner, audio, service, calls, widget };
}

test('two widgets load one local library and never create media, pick folders or autoplay', async t => {
    const { widget, calls, audio, owner } = setup(t); const a = widget(), b = widget();
    assert.equal(calls.length, 0); assert.equal(owner._widgetListeners.size, 0);
    a.controller.setVisibility(true); b.controller.setVisibility(true); await flush();
    assert.equal(calls.length, 1); assert.equal(calls[0].url, '/api/player/library');
    assert.equal(audio.plays, 0); assert.equal(audio.loads, 0);
    assert.ok(!a.root.all().some(node => ['audio', 'iframe', 'img'].includes(node.tag)));
    a.root.part('open').click(); assert.deepEqual(a.opened, ['player']);
});

test('widget, app controls and the real skill handler use the same audio and observable state', async t => {
    const { widget, owner, audio } = setup(t); const a = widget(), b = widget();
    a.controller.setVisibility(true); b.controller.setVisibility(true); await flush();
    const toggle = a.root.part('play-pause'); toggle.focus(); toggle.click(); await flush();
    assert.equal(audio.plays, 1); assert.equal(b.root.part('status').textContent, 'Reproduciendo');
    assert.equal(a.root.part('play-pause'), toggle); assert.equal(doc.activeElement, toggle);
    assert.equal(a.root.part('title').textContent, library.tracks[0].title);
    audio.emit('timeupdate', { currentTime: 42 }); assert.equal(b.root.part('progress').value, 42);
    assert.match(b.root.part('time').textContent, /0:42/);
    await owner._runAction('next'); assert.equal(a.root.part('title').textContent, 'Segunda canción');
    const result = await owner.handleIntent({ schema: 'iris.app-intent/v1', request_id: 'skill-test', app: 'player', action: 'pause', args: {} });
    assert.equal(result.status, 'ok'); assert.equal(b.root.part('status').textContent, 'En pausa');
    assert.equal(audio.paused, true); assert.equal(audio.plays, 2);
});

test('hide/removal detach view listeners without stopping shared playback; a late read cannot revive a widget', async t => {
    const { widget, audio, owner } = setup(t); const a = widget(); a.controller.setVisibility(true); await flush();
    a.root.part('play-pause').click(); await flush(); a.controller.setVisibility(false);
    assert.equal(owner._widgetListeners.size, 0); assert.equal(audio.paused, false);
    const prior = a.root.part('time').textContent; audio.emit('timeupdate', { currentTime: 77 }); assert.equal(a.root.part('time').textContent, prior);
    a.controller.setVisibility(true); await flush(); assert.match(a.root.part('time').textContent, /1:17/);
    a.abort.abort(); a.controller.dispose(); audio.emit('timeupdate', { currentTime: 80 });
    assert.equal(a.root.children.length, 0); assert.equal(owner._widgetListeners.size, 0); assert.equal(audio.paused, false);
    const b = widget(); let finish;
    owner._loaded = false; globalThis.fetch = () => new Promise(resolve => { finish = resolve; });
    b.controller.setVisibility(true); b.controller.dispose(); finish({ ok: true, json: async () => library }); await flush();
    assert.equal(b.root.children.length, 0); assert.equal(owner._widgetListeners.size, 0);
});

test('one pending transport command blocks other widgets; unsupported operations are inert', async t => {
    const { widget, audio, service } = setup(t); const a = widget(), b = widget();
    a.controller.setVisibility(true); b.controller.setVisibility(true); await flush(); audio.delayed = true;
    const command = service.command('next'); await flush();
    assert.equal(b.root.part('next').disabled, true); b.root.part('next').click();
    assert.equal(await service.command('pick-folder'), false); assert.equal(await service.command('next'), false);
    assert.equal(audio.plays, 1); audio.emit('playing', { paused: false, readyState: 3 }); await command; await flush();
    assert.equal(b.root.part('next').disabled, false);
});

test('missing folder, empty library, read failure and blocked audio have honest recovery states', async t => {
    const env = setup(t, { configured: false, tracks: [] }); const a = env.widget(); a.controller.setVisibility(true); await flush();
    assert.match(a.root.text(), /Elige una carpeta/); assert.equal(a.root.part('play-pause').disabled, true);
    env.owner.meta.configured = true; env.owner.render(); assert.match(a.root.text(), /no contiene canciones/);
    env.owner._loaded = false;
    globalThis.fetch = async () => ({ ok: false, status: 503, json: async () => ({ detail: 'Lectura fallida' }) });
    await env.owner.load(); assert.match(a.root.text(), /Lectura fallida/); assert.equal(a.root.part('retry').hidden, false);
    globalThis.fetch = async () => ({ ok: true, json: async () => library }); a.root.part('retry').click(); await flush();
    assert.equal(a.root.part('play-pause').disabled, false); env.audio.fail = true;
    a.root.part('play-pause').click(); await flush(); assert.equal(env.service.read().status, 'paused');
    assert.match(a.root.text(), /No se pudo completar/); assert.doesNotMatch(a.root.part('status').textContent, /^Reproduciendo$/);
});

test('the service is a bounded display projection; unavailable surfaces cannot acquire playback', async t => {
    const { service, widget } = setup(t); const a = widget(); a.controller.setVisibility(true); await flush();
    assert.deepEqual(Object.keys(service), ['read', 'subscribe', 'load', 'command']);
    assert.ok(Object.isFrozen(service)); assert.ok(Object.isFrozen(service.read()));
    assert.ok(!/stream|path|token|folder_name/.test(JSON.stringify(service.read())));
    const root = new Node('section'); const absent = createWidget({ root }, { documentLike: doc });
    absent.setVisibility(true); assert.match(root.text(), /no está disponible/); assert.equal(root.part('next').disabled, true); absent.dispose();
});
