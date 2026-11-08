import assert from 'node:assert/strict';
import test from 'node:test';

import { asyncRunCapabilities } from '../../face/js/async_agents_state.js';

test('Hermes 0.20 run submission is not mistaken for a global run list', () => {
    const state = asyncRunCapabilities({
        success: true,
        supports: { runs: true },
        capabilities: {
            features: { run_submission: true, run_status: true, run_stop: true },
            endpoints: {
                runs: { method: 'POST', path: '/v1/runs' },
                run_status: { method: 'GET', path: '/v1/runs/{run_id}' },
                run_stop: { method: 'POST', path: '/v1/runs/{run_id}/stop' },
            },
        },
    });
    assert.deepEqual(state, {
        online: true,
        canCreate: true,
        canList: false,
        canInspect: true,
        canStop: true,
        available: true,
    });
});

test('an explicit GET runs endpoint enables the global list', () => {
    const state = asyncRunCapabilities({
        success: true,
        capabilities: { endpoints: { runs: { method: 'GET', path: '/v1/runs' } } },
    });
    assert.equal(state.canList, true);
    assert.equal(state.canCreate, false);
});

test('legacy supports.runs remains compatible only without a precise contract', () => {
    assert.equal(asyncRunCapabilities({ success: true, supports: { runs: true } }).canList, true);
    assert.equal(asyncRunCapabilities({ success: false, supports: { runs: true } }).available, false);
});

test('submission-only support stays available without promising status or listing', () => {
    const state = asyncRunCapabilities({
        success: true,
        capabilities: {
            features: { run_submission: true },
            endpoints: { runs: { method: 'POST', path: '/v1/runs' } },
        },
    });
    assert.equal(state.available, true);
    assert.equal(state.canCreate, true);
    assert.equal(state.canList, false);
    assert.equal(state.canInspect, false);
});
