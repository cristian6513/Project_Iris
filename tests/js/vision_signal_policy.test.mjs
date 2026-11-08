import test from 'node:test';
import assert from 'node:assert/strict';

import { buildVisionSignal } from '../../face/js/vision_signal_policy.js';


test('unavailable or stale analysis never becomes an absence signal', () => {
    assert.equal(buildVisionSignal(null, { nowMs: 100 }), null);
    assert.equal(buildVisionSignal({ present: false, timestamp: 100 }, { nowMs: 2001 }), null);
    assert.equal(buildVisionSignal({ timestamp: 100 }, { nowMs: 101 }), null);
});


test('completed MediaPipe observations preserve present and absent states', () => {
    assert.deepEqual(
        buildVisionSignal({ present: false, timestamp: 100 }, { nowMs: 101 }),
        { present: false, confidence: 0.7, motion: 'still' },
    );
    assert.deepEqual(
        buildVisionSignal({ present: true, timestamp: 100, motion: 'active' }, { nowMs: 101 }),
        { present: true, confidence: 0.92, motion: 'active' },
    );
    assert.deepEqual(
        buildVisionSignal({ present: true, timestamp: 100, motion: 'invalid' }, { nowMs: 101 }),
        { present: true, confidence: 0.92, motion: 'low' },
    );
});
