// PMX physics data is checked before it reaches the worker (pmx_physics.js validPhysics). MMD
// modellers set huge masses on purpose: a 2026-11-25 model hangs its hair chains from 4.7e13 at the
// root down to 1.3e6, with 錘 weights at the tips. The old 1e6 bound on every number turned off all
// of that model's physics. A mass must be finite and not negative; other numbers keep the bound.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const source = readFileSync(new URL('../../face/js/pmx_physics.js', import.meta.url), 'utf8');
const block = source.slice(0, source.indexOf('/** `modelled`'));
assert.ok(block.includes('function validPhysics'), 'validPhysics found before createPmxPhysics');
const { validPhysics } = await import('data:text/javascript;base64,' + Buffer.from(`${block}\nexport { validPhysics };`).toString('base64'));

const body = (overrides = {}) => ({
    width: 0.1, height: 0.2, depth: 0.1, weight: 1, friction: 0.5, restitution: 0, positionDamping: 0.5, rotationDamping: 0.5,
    type: 1, shapeType: 0, boneIndex: 0, groupIndex: 0, groupTarget: 0xffff, position: [0, 0, 0], rotation: [0, 0, 0],
    ...overrides,
});
const joint = { rigidBodyIndex1: 0, rigidBodyIndex2: 1, position: [0, 0, 0], rotation: [0, 0, 0],
    translationLimitation1: [0, 0, 0], translationLimitation2: [0, 0, 0], rotationLimitation1: [0, 0, 0],
    rotationLimitation2: [0, 0, 0], springPosition: [0, 0, 0], springRotation: [0, 0, 0] };
const valid = (...bodies) => validPhysics(bodies, [joint], 4);

test('a hair chain with huge masses, as modelled, is simulated', () => {
    assert.equal(valid(body({ weight: 4.7e13 }), body({ weight: 1.3e6 })), true);
});

test('a mass that is not a number, infinite, negative or absurd is still refused', () => {
    for (const weight of [NaN, Infinity, -1, 1e16]) assert.equal(valid(body({ weight }), body()), false, String(weight));
});

test('huge masses are simulated at 1e6: as they are they broke the hair into a curtain', () => {
    // Measured 2026-11-25: with the 4.7e13 hair masses the hair stretched to the waist over her face;
    // capped at 1e6, 2000 or 100 it kept its modelled shape. The test models weigh at most 2000.
    assert.match(source, /const SIM_MASS = 1e6;/);
    assert.match(source, /weight: Math\.min\(body\.weight, SIM_MASS\),/);
    const sent = source.slice(source.indexOf('const bodies = rigidBodies.map'));
    assert.ok(sent.includes('transforms, bodies, constraints'), 'the capped bodies are what the worker gets');
});

test('other numbers keep the 1e6 bound', () => {
    assert.equal(valid(body({ width: 2e6 }), body()), false);
    assert.equal(valid(body({ friction: 2e6 }), body()), false);
});
