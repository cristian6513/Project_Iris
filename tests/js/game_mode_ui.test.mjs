import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { comboFromKeyEvent, displayCombo, gameModeView, gameWelcome, presetFor,
    readGamePreferences, saveGamePreferences, GAME_PREFERENCES_KEY } from '../../face/js/game_mode_ui.js';
import { validGameKeys, GAME_KEYS } from '../../face/js/game_shortcuts.js';
import { GameCapture } from '../../face/js/game_capture.js';

const active = (extra = {}) => ({ active: true, session_id: 's', game: 'Expedition 33', analyses: 3, muted: false,
    spoiler_free: true, commentary: { enabled: false, status: 'off', interval: 45, limit: 100, style: 'tips' }, ...extra });

test('before a session the panel teaches the steps and says when it is unavailable', () => {
    const idle = gameModeView({}, { available: false });
    assert.equal(idle.view, 'start');
    assert.equal(idle.startLabel, 'Elegir ventana del juego');
    assert.equal(idle.status, 'Disponible cuando Iris esté conectada.');
    assert.equal(idle.askStep, 'Con «Hablar» o escribiendo en el chat.');
    const keys = gameModeView({}, { keys: { ...GAME_KEYS, enabled: true } });
    assert.equal(keys.askStep, 'Con «Hablar», el chat o Ctrl + Alt + F8.');
    assert.equal(keys.status, '');
    assert.equal(gameModeView({ preparing: true }).startLabel, 'Esperando la ventana…');
    assert.equal(gameModeView({ message: 'Selección cancelada.' }, { available: false }).status, 'Selección cancelada.');
});

test('a session reads as «Mirando · juego» with consultas, never «intentos de análisis»', () => {
    const v = gameModeView(active());
    assert.equal(v.view, 'playing');
    assert.equal(v.title, 'Mirando · Expedition 33');
    assert.equal(v.card, 'Sin spoilers · 3 consultas en esta partida');
    assert.equal(v.bar, 'a petición · 3 consultas');
    assert.equal(v.tone, 'ready');
    assert.equal(v.auto, 'Apagados. Iris solo habla si le preguntas.');
    assert.equal(v.autoOn, false);
    assert.equal(v.mute, 'Silenciar');
    assert.equal(gameModeView(active({ analyses: 1, game: '' })).card, 'Sin spoilers · 1 consulta en esta partida');
    assert.equal(gameModeView(active({ game: '' })).title, 'Mirando · tu juego');
    assert.equal(gameModeView(active({ spoiler_free: false })).card, '3 consultas en esta partida');
    for (const view of [v, gameModeView({})]) assert.doesNotMatch(JSON.stringify(view), /intentos|análisis/);
});

test('commentary, mute and limits change the card tone, the bar and the switch summary', () => {
    const on = { enabled: true, status: 'waiting', interval: 20, limit: 50, style: 'chat' };
    let v = gameModeView(active({ commentary: on }));
    assert.equal(v.auto, 'Cada 20 s · charla.'); assert.equal(v.autoOn, true);
    assert.equal(v.bar, 'comentarios cada 20 s · 3 consultas');
    v = gameModeView(active({ commentary: { ...on, status: 'analysing' } }));
    assert.equal(v.tone, 'busy'); assert.equal(v.auto, 'Mirando el juego…');
    assert.equal(v.bar, 'mirando el juego… · 3 consultas');
    v = gameModeView(active({ commentary: { ...on, enabled: false, status: 'limit' } }));
    assert.equal(v.tone, 'warn'); assert.equal(v.auto, 'En pausa: llegaste al máximo de 50 consultas.');
    assert.equal(v.bar, 'límite alcanzado · 3 consultas');
    v = gameModeView(active({ muted: true, commentary: { ...on, enabled: false, status: 'paused' } }));
    assert.equal(v.tone, 'muted'); assert.equal(v.mute, 'Activar voz');
    assert.equal(v.auto, 'Activa la voz para usarlos.');
    assert.equal(v.card, 'Sin spoilers · 3 consultas en esta partida · respuestas por texto');
    assert.equal(v.bar, 'sin voz · 3 consultas');
    v = gameModeView(active(), { pending: true });
    assert.equal(v.auto, 'Esperando tu confirmación…'); assert.equal(v.autoOn, true);
});

test('the shortcut hint and the first welcome follow whether shortcuts are on', () => {
    assert.equal(gameModeView(active(), { keys: { ...GAME_KEYS, enabled: true, active: true } }).keyHint,
        'Sin salir del juego: Ctrl + Alt + F8');
    assert.match(gameModeView(active(), { keys: { ...GAME_KEYS } }).keyHint, /activa los atajos en Más opciones/);
    assert.equal(gameWelcome({}), 'Listo. Pregúntame lo que quieras sobre tu juego: pulsa «Hablar» o escríbeme en el chat.');
    assert.match(gameWelcome({ ...GAME_KEYS, enabled: true }), /usa Ctrl \+ Alt \+ F8\.$/);
});

test('captured keys come out in the format GameShortcuts accepts, using physical codes', () => {
    const key = (code, mods = {}, keyName = 'x') => comboFromKeyEvent({ code, key: keyName, ...mods });
    assert.deepEqual(key('F8', { ctrlKey: true, altKey: true }), { waiting: false, combo: 'Ctrl+Alt+F8' });
    // Spanish AltGr gives «@» for Ctrl+Alt+Q; the physical code still says Q.
    assert.equal(key('KeyQ', { ctrlKey: true, altKey: true }, '@').combo, 'Ctrl+Alt+Q');
    assert.equal(key('Digit5', { ctrlKey: true, shiftKey: true }).combo, 'Ctrl+Shift+5');
    assert.deepEqual(comboFromKeyEvent({ key: 'Alt', code: 'AltLeft', altKey: true, ctrlKey: true }), { waiting: true, combo: '' });
    for (const [code, mods] of [['F8', { ctrlKey: true }], ['F12', { ctrlKey: true, altKey: true }],
        ['KeyA', { ctrlKey: true, altKey: true, metaKey: true }], ['Space', { ctrlKey: true, altKey: true }]]) {
        assert.equal(key(code, mods).combo, '', code);
    }
    const captured = { help: key('KeyH', { ctrlKey: true, altKey: true }).combo, mute: GAME_KEYS.mute, exit: GAME_KEYS.exit };
    assert.equal(validGameKeys(captured), true);
    assert.equal(displayCombo('Ctrl+Alt+F10'), 'Ctrl + Alt + F10');
});

test('commentary choices are three presets, remembered without touching anything else', () => {
    assert.equal(presetFor('interval', 60), 45); assert.equal(presetFor('interval', 300), 90);
    assert.equal(presetFor('interval', 'x'), 45); assert.equal(presetFor('limit', 0), 0);
    assert.equal(presetFor('limit', 1000), 100); assert.equal(presetFor('style', 'rap'), 'tips');
    const store = new Map();
    const storage = { getItem: k => store.get(k) ?? null, setItem: (k, v) => store.set(k, v) };
    assert.deepEqual(readGamePreferences(storage), { interval: 45, limit: 100, style: 'tips' });
    assert.deepEqual(saveGamePreferences(storage, { interval: 20, limit: 0, style: 'chat', extra: 'x' }),
        { interval: 20, limit: 0, style: 'chat' });
    assert.deepEqual([...store.keys()], [GAME_PREFERENCES_KEY]);
    assert.deepEqual(readGamePreferences(storage), { interval: 20, limit: 0, style: 'chat' });
    assert.deepEqual(readGamePreferences({ getItem: () => '{broken' }), { interval: 45, limit: 100, style: 'tips' });
    assert.deepEqual(readGamePreferences({ getItem() { throw Error('blocked'); } }), { interval: 45, limit: 100, style: 'tips' });
});

test('the window notice is short, names the destination and keeps the details one tap away', async () => {
    const dialogs = [];
    const doc = Object.assign(new EventTarget(), { visibilityState: 'visible', defaultView: new EventTarget() });
    const c = new GameCapture({ doc, send: () => true, confirm: async options => { dialogs.push(options); return false; },
        timers: { setTimeout: () => 1, clearTimeout() {} } });
    c.prepare({ game: 'Prueba' });
    await c.prepared({ request_id: c.request, session_id: 'one', destination: { provider: 'fixture', model: 'vision' } });
    const [notice] = dialogs;
    assert.equal(notice.title, 'Compartir la ventana del juego');
    assert.match(notice.message, /solo la ventana que elijas, y solo cuando le preguntes/);
    assert.match(notice.question, /vision \(fixture\)/);
    assert.equal(notice.details.label, 'Más detalles');
    assert.match(notice.details.text, /imágenes temporales.*políticas/);
    assert.equal(notice.confirmLabel, 'Elegir ventana');
    c.dispose();
});

test('the Companion page keeps every control the panel logic looks up', () => {
    const html = readFileSync(new URL('../../face/companion/index.html', import.meta.url), 'utf8');
    const source = readFileSync(new URL('../../face/js/game_capture.js', import.meta.url), 'utf8');
    const ids = new Set([...source.matchAll(/\$\('([a-z0-9-]+)'\)/g)].map(m => m[1]));
    for (const name of ['help', 'mute', 'exit']) ids.add(`game-key-${name}`);
    ids.delete('voice-manual'); ids.delete('speech-dismiss');
    assert.ok(ids.size > 25, `found only ${ids.size} ids`);
    for (const id of ids) assert.match(html, new RegExp(`id="${id}"`), id);
    for (const name of ['interval', 'limit', 'style']) assert.match(html, new RegExp(`name="game-${name}"`));
    assert.match(html, /<span id="surface-label">Companion<\/span>/);
    assert.doesNotMatch(html, /experimental|Desktop presence|intentos de análisis|Mantener a petición/);
});

test('the game panel only paints with the theme tokens', () => {
    const css = readFileSync(new URL('../../face/companion/companion.css', import.meta.url), 'utf8');
    const block = css.slice(css.indexOf('/* Game mode: «Antes de empezar»'));
    assert.ok(block.length > 2000);
    // Fixed colours only as the dark ink mixed into the accent and plain black/white tints.
    const literals = [...block.matchAll(/#[0-9a-f]{3,8}\b/gi)].map(m => m[0].toLowerCase());
    assert.deepEqual([...new Set(literals)].sort(), ['#03060c', '#ffffff']);
    assert.match(css, /--companion-warn: #ffc45c;/);
});
