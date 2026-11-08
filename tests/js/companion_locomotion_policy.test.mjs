import assert from 'node:assert/strict';
import test from 'node:test';

import {
    companionLocomotionDecision,
    companionLocomotionStatus,
} from '../../face/js/companion_locomotion_policy.js';

test('desktop locomotion is local-native and opt-in', () => {
    assert.deepEqual(companionLocomotionDecision(), {
        allowed: false,
        reason: 'disabled',
    });
    assert.equal(companionLocomotionDecision({ enabled: true }).reason, 'native-required');
    assert.equal(companionLocomotionDecision({ enabled: true, native: true }).allowed, true);
});

test('desktop locomotion pauses for user and assistant activity', () => {
    const ready = { enabled: true, native: true };
    assert.equal(companionLocomotionDecision({ ...ready, hovered: true }).reason, 'pointer-hover');
    assert.equal(companionLocomotionDecision({ ...ready, quietMode: true }).reason, 'quiet-mode');
    assert.equal(companionLocomotionDecision({ ...ready, reducedMotion: true }).reason, 'reduced-motion');
    assert.equal(companionLocomotionDecision({ ...ready, visible: false }).reason, 'hidden');
    for (const operationalState of ['listening', 'thinking', 'tool', 'speaking', 'error', 'offline']) {
        assert.equal(
            companionLocomotionDecision({ ...ready, operationalState }).allowed,
            false,
            operationalState,
        );
    }
});

test('settings status makes precedence explicit', () => {
    assert.equal(companionLocomotionStatus({}).code, 'OFF');
    assert.equal(companionLocomotionStatus({
        companion_locomotion_enabled: true,
        companion_quiet_mode: true,
    }).code, 'QUIET');
    assert.equal(companionLocomotionStatus({
        companion_locomotion_enabled: true,
        companion_quiet_mode: true,
        companion_reduced_motion: true,
    }).code, 'BLOCKED');
    assert.equal(companionLocomotionStatus({ companion_locomotion_enabled: true }).code, 'READY');
});
