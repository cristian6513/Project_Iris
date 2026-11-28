// Her eyes turn back to the centre as her lids close (2026-11-26): turned 10 degrees with the lids
// closed, IRIS's iris slid out over the lid at the outer corner (the «3 a. m.» video).
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import { closeGazeWithLids, lidClosure } from '../../face/js/lid_gaze.js';

/** A three-vrm look-at as far as lid_gaze uses it: update() applies yaw/pitch only when they changed. */
function fakeVrm(values = {}) {
    const turns = [];
    const applier = { applyYawPitch(yaw, pitch) { turns.push([yaw, pitch]); } };
    const look = {
        applier, autoUpdate: true, _yaw: 0, _pitch: 0, _needsUpdate: false,
        get yaw() { return this._yaw; }, set yaw(v) { this._yaw = v; this._needsUpdate = true; },
        get pitch() { return this._pitch; }, set pitch(v) { this._pitch = v; this._needsUpdate = true; },
        update() { if (this._needsUpdate) { this._needsUpdate = false; this.applier.applyYawPitch(this._yaw, this._pitch); } },
    };
    const expressionManager = { getValue: (name) => (name in values ? values[name] : null) };
    return { vrm: { lookAt: look, expressionManager }, look, values, turns };
}

test('the gaze is scaled by how open the lids are', () => {
    const { vrm, look, values, turns } = fakeVrm({ blink: 0 });
    assert.equal(closeGazeWithLids(vrm), true);
    look.yaw = 90; look.pitch = -20; look.update(1 / 60);
    values.blink = 0.5; look.yaw = 90; look.update(1 / 60);
    values.blink = 1; look.yaw = 90; look.update(1 / 60);
    assert.deepEqual(turns, [[90, -20], [45, -10], [0, -0]]);
});

test('a wink or one eye closing first counts as closing', () => {
    assert.equal(lidClosure({ getValue: (n) => ({ blinkLeft: 0.8, blinkRight: 0.1 })[n] ?? null }), 0.8);
    assert.equal(lidClosure({ getValue: (n) => ({ blink: 0.3, blinkRight: 0.6 })[n] ?? null }), 0.6);
    assert.equal(lidClosure({ getValue: () => null }), 0);
    assert.equal(lidClosure({ getValue: () => 1.4 }), 1);
});

test('a held gaze follows the lids even when nothing turns the eyes that frame', () => {
    // A clip holds the head: autoUpdate is off and no one sets yaw/pitch, so three-vrm applies nothing.
    const { vrm, look, values, turns } = fakeVrm({ blink: 0 });
    closeGazeWithLids(vrm);
    look.yaw = 60; look.update(1 / 60);
    look.update(1 / 60);
    assert.equal(turns.length, 1, 'lids still: nothing to apply again');
    values.blink = 0.75; look.update(1 / 60);
    values.blink = 1; look.update(1 / 60);
    values.blink = 0; look.update(1 / 60);
    assert.deepEqual(turns.map(([yaw]) => yaw), [60, 15, 0, 60]);
});

test('wrapping twice is harmless and a VRM without a look-at applier is left alone', () => {
    const { vrm, look, values, turns } = fakeVrm({ blink: 0.5 });
    closeGazeWithLids(vrm);
    assert.equal(closeGazeWithLids(vrm), true);
    look.yaw = 40; look.update(1 / 60);
    assert.deepEqual(turns, [[20, 0]], 'scaled once, not twice');
    values.blink = 0.5;
    assert.equal(closeGazeWithLids({ lookAt: null, expressionManager: {} }), false);
    assert.equal(closeGazeWithLids({ lookAt: { applier: {}, update() {} }, expressionManager: {} }), false);
    assert.equal(closeGazeWithLids({ lookAt: { applier: { applyYawPitch() {} }, update() {} } }), false);
});

test('every VRM avatar gets it when it loads', () => {
    const avatar = readFileSync(new URL('../../face/js/avatar.js', import.meta.url), 'utf8');
    assert.match(avatar, /import \{ closeGazeWithLids \} from '\.\/lid_gaze\.js\?v=\d+';/);
    const load = avatar.indexOf('this.vrm = vrm;');
    assert.ok(load > 0 && avatar.indexOf('closeGazeWithLids(vrm);', load) - load < 200,
        'closeGazeWithLids(vrm) right where the loaded VRM is kept');
});
