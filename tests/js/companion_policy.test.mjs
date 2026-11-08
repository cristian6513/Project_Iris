import assert from 'node:assert/strict';
import test from 'node:test';

import {
    companionStateForStatus,
    shouldPlaySnapshotAnimation,
} from '../../face/js/companion_policy.js';

test('deduplicates the speech snapshot when the dedicated animation just arrived', () => {
    const previous = { name: 'go_computer', at: 1000 };
    assert.equal(
        shouldPlaySnapshotAnimation('go_computer', previous, { now: 2200 }),
        false,
    );
    assert.equal(
        shouldPlaySnapshotAnimation('go_computer', previous, { now: 2600 }),
        true,
    );
});

test('allows a different or missing dedicated animation snapshot', () => {
    assert.equal(
        shouldPlaySnapshotAnimation('wave', { name: 'think', at: 500 }, { now: 600 }),
        true,
    );
    assert.equal(shouldPlaySnapshotAnimation('', { name: 'wave', at: 500 }), false);
    assert.equal(shouldPlaySnapshotAnimation('wave', {}, { now: 600 }), true);
});

test('maps backend runtime statuses without inventing unknown states', () => {
    assert.equal(companionStateForStatus('processing'), 'thinking');
    assert.equal(companionStateForStatus('tool_running'), 'tool');
    assert.equal(companionStateForStatus('recording'), 'listening');
    assert.equal(companionStateForStatus('done'), 'idle');
    assert.equal(companionStateForStatus('unexpected-provider-state'), null);
});
