import test from 'node:test';
import assert from 'node:assert/strict';

import { avatarUrl, IRIS_AVATAR_FILE, IRIS_AVATAR_REVISION, IRIS_AVATAR_URL } from '../../face/js/avatar_state.js';

test("without a model of the person's own, IRIS' factory VRM shows", () => {
    assert.equal(IRIS_AVATAR_FILE, 'iris.vrm');
    // Versioned, so a new factory model never comes from an old cache (11: MToon).
    assert.equal(IRIS_AVATAR_REVISION, '12');
    assert.equal(IRIS_AVATAR_URL, '/face/animations/iris.vrm?v=12');
    assert.equal(avatarUrl({}), IRIS_AVATAR_URL);
    assert.equal(avatarUrl({ avatar_vrm_path: '   ' }), IRIS_AVATAR_URL);
    assert.equal(avatarUrl({}, '/bundle/iris.vrm'), '/bundle/iris.vrm', 'the Companion passes its own copy');
});

test("the person's saved model wins, and a live replacement wins over it", () => {
    const config = { avatar_vrm_path: '/avatars/saved.vrm' };
    assert.equal(avatarUrl(config), '/avatars/saved.vrm');
    assert.equal(avatarUrl(config, IRIS_AVATAR_URL, '/avatars/new.vrm'), '/avatars/new.vrm');
});

test('a config saved before the geometric and particle avatars were removed shows IRIS', () => {
    assert.equal(avatarUrl({ avatar_type: 'procedural', avatar_vrm_path: '', particle_style: 'jarvis' }),
        IRIS_AVATAR_URL);
    assert.equal(avatarUrl({ avatar_type: 'particle' }), IRIS_AVATAR_URL);
});
