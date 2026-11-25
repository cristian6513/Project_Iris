import test from 'node:test';
import assert from 'node:assert/strict';
import { GameShortcuts, GAME_KEYS, validGameKeys } from '../../face/js/game_shortcuts.js';

function harness(options = {}) {
    const calls = [], actions = [], states = [], saved = [];
    let listener;
    const c = new GameShortcuts({ onAction: a => actions.push(a), onState: s => states.push(s),
        storage: { getItem: () => JSON.stringify(GAME_KEYS), setItem: (...v) => saved.push(v) }, ...options });
    const bridge = { native: true, setGameHotkeys: async (...args) => { calls.push(args); },
        onWindowEvent: async (_, fn) => { listener = fn; } };
    return { c, bridge, calls, actions, states, saved, fire: p => listener({ payload: p }) };
}

test('stored key names never enable global shortcuts; no events outside an active session', async () => {
    const h = harness(); await h.c.connect(h.bridge);
    await h.c.setSession('one');
    h.fire({ session_id: 'one', action: 'help' }); assert.deepEqual(h.actions, []);
    assert.equal(h.calls.at(-1)[1], null);
    await h.c.configure(true, GAME_KEYS);
    h.fire({ session_id: 'other', action: 'exit' });
    h.fire({ session_id: 'one', action: 'help' });
    h.fire({ session_id: 'one', action: 'help' });
    h.fire({ session_id: 'one', action: 'mute' });
    assert.deepEqual(h.actions, ['help', 'mute']);
    await h.c.setSession('');
    h.fire({ session_id: 'one', action: 'exit' });
    assert.equal(h.calls.at(-1)[1], null); assert.equal(h.actions.length, 2);
});

test('rejects duplicate-equivalent, reserved and bare keys', () => {
    assert.equal(validGameKeys(GAME_KEYS), true);
    for (const help of ['M', 'Ctrl+M', 'Ctrl+Alt+F12', 'Win+Alt+F8', 'Ctrl+Ctrl+A', '', null]) {
        assert.equal(validGameKeys({ ...GAME_KEYS, help }), false);
    }
    assert.equal(validGameKeys({ ...GAME_KEYS, mute: 'Alt+Ctrl+F8' }), false);
});

test('late registration cannot reactivate a session after exit; updates are serialized', async () => {
    const h = harness(); await h.c.connect(h.bridge); await h.c.setSession('one');
    let release;
    h.bridge.setGameHotkeys = async (...args) => { h.calls.push(args); if (args[1]) await new Promise(r => { release = r; }); };
    const enabling = h.c.configure(true, GAME_KEYS);
    for (let i = 0; i < 5; i++) await Promise.resolve();
    const stopping = h.c.setSession('');
    h.fire({ session_id: 'one', action: 'help' }); assert.deepEqual(h.actions, []);
    release(); await enabling; await stopping;
    assert.equal(h.c.active, false); assert.equal(h.calls.at(-1)[1], null);
});

test('conflict is visible, clears native keys and never reports active success', async () => {
    const h = harness(); await h.c.connect(h.bridge); await h.c.setSession('one');
    h.bridge.setGameHotkeys = async (session, keys) => { h.calls.push([session, keys]); if (keys) throw Error('Combinación ocupada'); };
    await h.c.configure(true, GAME_KEYS);
    assert.equal(h.c.enabled, false); assert.equal(h.c.active, false);
    assert.match(h.states.at(-1).message, /ocupada/); assert.equal(h.calls.at(-1)[1], null);
});

test('bad settings clear previous keys and browser mode explains native availability', async () => {
    const h = harness(); await h.c.connect(h.bridge); await h.c.setSession('one');
    await h.c.configure(true, GAME_KEYS);
    await h.c.configure(true, { ...GAME_KEYS, exit: 'M' });
    assert.equal(h.calls.at(-1)[1], null); assert.equal(h.c.enabled, false);
    await h.c.connect({ native: false });
    assert.match(h.states.at(-1).message, /Windows/);
});

test('disposal releases keys and erases session eligibility immediately', async () => {
    const h = harness(); await h.c.connect(h.bridge); await h.c.setSession('one');
    await h.c.configure(true, GAME_KEYS); const done = h.c.dispose();
    h.fire({ session_id: 'one', action: 'help' }); assert.deepEqual(h.actions, []);
    await done; assert.equal(h.calls.at(-1)[1], null);
});
