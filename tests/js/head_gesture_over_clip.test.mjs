import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

// A nod on top of a clip that holds the head still: the mixer does not rewrite that value, so a
// nod left on the bone was nodded again every frame (Codex, 2026-11-20: some 102 degrees over the
// still stretch of opening the book). The clip's rotation is given back after vrm.update.
const THREE = await import(new URL('../../companion/node_modules/three/build/three.module.js', import.meta.url).href);
const source = (await readFile(new URL('../../face/js/avatar.js', import.meta.url), 'utf8'))
    .split('export class VRMAvatar')[1];
function method(name) {
    const block = source.match(new RegExp('^    ' + name + '\\([^]*?^    \\}', 'm'))?.[0];
    assert.ok(block, name);
    return new Function('return ({' + block + '})')()[name];
}
const angle = (a, b) => 2 * Math.acos(Math.min(1, Math.abs(a.dot(b))));

function fixture(type) {
    const head = new THREE.Object3D();
    head.rotation.set(0.12, -0.2, 0.05);
    const clip = head.quaternion.clone();
    const rendered = [];
    const state = {
        vrm: {
            humanoid: { getNormalizedBoneNode: (name) => (name === 'head' ? head : null) },
            update() { rendered.push(head.quaternion.clone()); },
        },
        _headAnim: { type, startTime: performance.now(), duration: 1e9, over: true },
        _applyHeadAnim: method('_applyHeadAnim'),
        _restoreHeadClip: method('_restoreHeadClip'),
        finalizeFrame: method('finalizeFrame'),
    };
    return { head, clip, rendered, state };
}

for (const [type, peak] of [['nod', 0.25], ['nod_big', 0.4], ['tilt', 0.2], ['shake', 0.3]]) {
    test(`a ${type} over a still clip is drawn around the clip's pose and never piles up`, () => {
        const { head, clip, rendered, state } = fixture(type);
        for (let frame = 0; frame < 90; frame += 1) {
            // Through the gesture, as if 90 frames covered most of it.
            state._headAnim.startTime = performance.now() - (frame / 100) * state._headAnim.duration;
            state.finalizeFrame(1 / 60);
            assert.ok(head.quaternion.equals(clip), `frame ${frame}: the clip's rotation is back`);
        }
        const worst = Math.max(...rendered.map((q) => angle(q, clip)));
        assert.ok(worst > 0.05, 'the gesture is shown');
        assert.ok(worst <= peak + 1e-6, `at most the gesture itself: ${worst.toFixed(3)} rad`);
    });
}

test('a gesture without a clip under it still sets the head and is not restored', () => {
    const { head, clip, state } = fixture('nod');
    state._headAnim.over = false;
    state._headAnim.startTime = performance.now() - 0.5 * state._headAnim.duration;
    state._applyHeadAnim();
    state.vrm.update();
    state._restoreHeadClip();
    assert.equal(head.quaternion.equals(clip), false);
});
