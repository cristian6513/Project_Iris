import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { layoutGameBubble } from '../../face/js/game_bubble.js';

test('a visible game bubble reserves space below the capture indicator and above the renderer', () => {
    const properties = {}, body = { dataset: { gameSession: 'active' } };
    const doc = { body, defaultView: { innerHeight: 620 },
        documentElement: { style: { setProperty: (name, value) => { properties[name] = value; } } },
        getElementById: id => id === 'game-mode-indicator' ? { getBoundingClientRect: () => ({ bottom: 124 }) }
            : { hidden: false, getBoundingClientRect: () => ({ bottom: 280 }) } };
    layoutGameBubble(doc);
    assert.equal(properties['--game-bubble-top'], '134px');
    assert.equal(properties['--game-avatar-top'], '290px');
    assert.equal(body.dataset.gameSpeech, 'visible');
    body.dataset.gameSession = 'off'; layoutGameBubble(doc);
    assert.equal(body.dataset.gameSpeech, 'hidden');
});

test('game reading time grows with text and is bounded; regular Companion keeps its existing delay', () => {
    const source = readFileSync(new URL('../../face/companion/companion.js', import.meta.url), 'utf8');
    const block = source.match(/^    _scheduleSpeechHide\([^]*?^    \}/m)[0];
    const cleared = [], timers = [];
    const schedule = new Function('clearTimeout', 'setTimeout', `return ({${block.trim()}})._scheduleSpeechHide;`)(
        id => cleared.push(id), (fn, ms) => { timers.push({ fn, ms }); return timers.length; });
    const app = { gameMode: { state: { active: true } }, speechText: { textContent: 'x'.repeat(300) }, _hideSpeech() {} };
    schedule.call(app); assert.equal(timers.at(-1).ms, 16500);
    app.speechText.textContent = 'x'.repeat(1400); schedule.call(app);
    assert.equal(timers.at(-1).ms, 25000); assert.equal(cleared.at(-1), 1);
    app.gameMode.state.active = false; schedule.call(app); assert.equal(timers.at(-1).ms, 4200);
});
