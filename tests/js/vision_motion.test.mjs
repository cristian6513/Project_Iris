import test from 'node:test';
import assert from 'node:assert/strict';

import { finiteMatrix, HeadMotionEstimator } from '../../face/js/vision_motion.js';

const identity = () => [
    1, 0, 0, 0,
    0, 1, 0, 0,
    0, 0, 1, 0,
    0, 0, 0, 1,
];

test('motion estimator stays still for a stable pose and detects a clear move', () => {
    const estimator = new HeadMotionEstimator();
    assert.equal(estimator.update(identity(), 100), 'still');
    assert.equal(estimator.update(identity(), 133), 'still');

    const moved = identity();
    moved[0] = 0.72;
    moved[2] = 0.55;
    moved[12] = 2.5;
    assert.equal(estimator.update(moved, 166), 'active');
});

test('invalid matrices are ignored and reset removes previous motion', () => {
    const estimator = new HeadMotionEstimator();
    assert.equal(finiteMatrix([1, 2]), null);
    assert.equal(estimator.update([1, 2], 100), 'still');
    estimator.update(identity(), 120);
    estimator.reset();
    assert.equal(estimator.update(identity(), 140), 'still');
});
