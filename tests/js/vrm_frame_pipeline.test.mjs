import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const source = (await readFile(new URL('../../face/js/avatar.js', import.meta.url), 'utf8'))
    .split('export class VRMAvatar')[1];
function method(name) {
    const block = source.match(new RegExp('^    ' + name + '\\([^]*?^    \\}', 'm'))?.[0];
    assert.ok(block, name);
    return new Function('return ({' + block + '})')()[name];
}
const update = method('update');
const finalizeFrame = method('finalizeFrame');
function fixture() {
    const calls = [];
    const state = {
        normalizedPose: 0, renderedPose: 0,
        vrm: { update() { calls.push('vrm'); state.renderedPose = state.normalizedPose; } },
        _updateExpressions() {}, _updateMouth() {}, _updateBlink() {}, _updateBreathing() {},
        _ikSolver: { solve() { calls.push('ik'); }, cancel() { calls.push('cancel-ik'); } },
        _applyHeadAnim() { calls.push('head'); }, finalizeFrame,
    };
    return { state, calls };
}

test('deferred VRM update transfers the current frame pose once after all writers', () => {
    const { state, calls } = fixture();
    update.call(state, 1 / 60, 1, { deferVRM: true });
    assert.equal(calls.includes('vrm'), false, 'physics must wait for IdleSystem/MicroLife');
    state.normalizedPose = 0.7; // current frame mixer + overlays
    state.finalizeFrame(1 / 60);
    assert.equal(state.renderedPose, 0.7);
    assert.equal(calls.filter(c => c === 'vrm').length, 1);
});

test('standalone avatar update remains complete without the deferred option', () => {
    const { state, calls } = fixture();
    update.call(state, 1 / 60, 1);
    assert.deepEqual(calls, ['ik', 'head', 'vrm']);
});

test('queued IK and legacy head gestures cannot write into an explicit pose', () => {
    const { state, calls } = fixture();
    state.animationOwnsRootMotion = true;
    update.call(state, 1 / 60, 1, { deferVRM: true });
    assert.deepEqual(calls, ['cancel-ik']);
});

test('both rendering surfaces finalize the VRM after their final bone writers', async () => {
    for (const [file, writer, render] of [
        ['../../face/js/app.js', 'this.microLife?.applyPost(dt)', 'this.composer.render()'],
        ['../../face/companion/companion.js', 'this.speechAnimator?.update(delta, elapsed)', 'this.composer?.render()'],
    ]) {
        const text = await readFile(new URL(file, import.meta.url), 'utf8');
        const finalize = text.lastIndexOf('finalizeFrame(');
        assert.ok(text.includes('{ deferVRM: true }'), file);
        assert.ok(text.lastIndexOf(writer) < finalize && finalize < text.lastIndexOf(render), file);
    }
});
