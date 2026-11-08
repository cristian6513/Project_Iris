import test from 'node:test';
import assert from 'node:assert/strict';
import { IrisPlayerApp } from '../../face/js/iris_player.js';

test('new Player volume uses 80%, while an explicitly stored zero stays silent', t => {
    const previous = globalThis.localStorage;
    t.after(() => { globalThis.localStorage = previous; });
    for (const [stored, expected] of [[null, 0.8], ['', 0.8], ['invalid', 0.8], ['0', 0], ['0.4', 0.4], ['2', 1], ['-1', 0]]) {
        globalThis.localStorage = { getItem: () => stored };
        assert.equal(new IrisPlayerApp().state.volume, expected, `Stored volume: ${stored}`);
    }
});
