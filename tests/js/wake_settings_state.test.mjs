import assert from 'node:assert/strict';
import test from 'node:test';

import {
    mergeWakeSettingsSnapshot,
    wakeReadinessState,
} from '../../face/js/wake_settings_state.js';

test('saving wake settings preserves the last confirmed capability probe', () => {
    const current = { enabled: false, capabilities: { ready: true, provider: 'sherpa' } };
    const merged = mergeWakeSettingsSnapshot(current, { enabled: true, capabilities: {} });
    assert.equal(merged.enabled, true);
    assert.deepEqual(merged.capabilities, current.capabilities);
});

test('a native input-device error overrides an optimistic dependency probe', () => {
    const state = wakeReadinessState(
        { capabilities: { ready: true, audio_available: true } },
        { active: false, state: 'off', input_device: { error: 'Error querying device -1' } },
    );
    assert.equal(state.blocked, true);
    assert.equal(state.ready, false);
    assert.match(state.hint, /micrófono/i);
});

test('ready remains truthful when Hermes has no runtime error', () => {
    assert.deepEqual(
        wakeReadinessState({ capabilities: { ready: true } }, { active: false, state: 'off' }),
        { blocked: false, ready: true, hint: '' },
    );
});
