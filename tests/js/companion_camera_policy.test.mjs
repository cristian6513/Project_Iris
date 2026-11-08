import assert from 'node:assert/strict';
import test from 'node:test';

import {
    buildCompanionEmotionPayload,
    COMPANION_CAMERA_CONSTRAINTS,
} from '../../face/js/companion_camera_policy.js';

test('companion camera constraints never request microphone audio', () => {
    assert.equal(COMPANION_CAMERA_CONSTRAINTS.audio, false);
    assert.equal(COMPANION_CAMERA_CONSTRAINTS.video.frameRate.max, 24);
    assert.ok(Object.isFrozen(COMPANION_CAMERA_CONSTRAINTS));
});

test('emotion payload is a bounded derived-only snapshot', () => {
    const payload = buildCompanionEmotionPayload({
        label: 'Happy<script>',
        valence: 9,
        arousal: -2,
        confidence: 0.8,
        intensity: Number.NaN,
        scores: { happy: 1 },
        frame: 'data:image/png;base64,forbidden',
    });

    assert.deepEqual(payload, {
        label: 'happyscript',
        valence: 1,
        arousal: 0,
        confidence: 0.8,
        intensity: 0,
    });
    assert.equal('scores' in payload, false);
    assert.equal('frame' in payload, false);
});
