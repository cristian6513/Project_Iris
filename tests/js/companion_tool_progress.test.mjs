import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

// Exercise the real event handler without booting WebGL, sensors or Core.
const source = readFileSync(new URL('../../face/companion/companion.js', import.meta.url), 'utf8');
const block = source.match(/^    _handleMessage\([^]*?^    \}/m)?.[0];
assert.ok(block);
const handle = new Function(`return ({${block.trim()}})._handleMessage;`)();

for (const status of ['running', 'completed', 'failed']) {
    test(`tool ${status} updates the header without impersonating a spoken reply`, () => {
        const headers = [], bubbles = [], chat = [];
        const app = {
            _setOperationalState: state => headers.push(state),
            _showSpeech: text => bubbles.push(text),
            _appendChat: (...message) => chat.push(message),
        };
        handle.call(app, 'tool_progress', {
            tool: 'terminal', status,
            label: 'python -c "from datetime import datetime"',
            detail: 'private/path command arguments',
        });
        assert.deepEqual(headers, ['tool']);
        assert.deepEqual(bubbles, []);
        assert.deepEqual(chat, []);
    });
}

test('a normal subtitle still reaches the speech bubble after tool progress', () => {
    const bubbles = [];
    const app = { _setOperationalState() {}, _showSpeech: text => bubbles.push(text) };
    handle.call(app, 'tool_progress', { tool: 'unknown-extension', detail: 'internal preview' });
    handle.call(app, 'subtitle', { text: 'Hola, aquí tienes la respuesta.' });
    assert.deepEqual(bubbles, ['Hola, aquí tienes la respuesta.']);
});

test('turns reach the routines by number, and the end of speech closes none', () => {
    const seen = [];
    const routines = {
        turnStarted: turn => seen.push(['started', turn]),
        toolRunning: turn => seen.push(['tool', turn]),
        turnEnded: turn => seen.push(['ended', turn]),
    };
    const app = { routines, _setOperationalState() {}, speechPresentation: { end() {} }, _hideSpeech() {} };
    handle.call(app, 'turn_started', { turn: 7 });
    handle.call(app, 'tool_progress', { turn: 7 });
    handle.call(app, 'speech_end', {});
    clearTimeout(app._bubbleTimer);
    handle.call(app, 'turn_ended', { turn: 7 });
    assert.deepEqual(seen, [['started', 7], ['tool', 7], ['ended', 7]]);
});

test('a reply that could not be spoken says so', () => {
    const shown = [];
    handle.call({ _showError: message => shown.push(message) }, 'voice_unavailable', { message: 'Iris no pudo hablar.' });
    assert.deepEqual(shown, ['Iris no pudo hablar.']);
});
