// One pixel too bright must not turn into a black block over the room.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { guardBloom } from '../../face/js/bloom_guard.js';

const shader = await readFile(new URL('../../companion/node_modules/three/examples/jsm/shaders/LuminosityHighPassShader.js', import.meta.url), 'utf8');
const three = await readFile(new URL('../../companion/node_modules/three/examples/jsm/postprocessing/UnrealBloomPass.js', import.meta.url), 'utf8');

test('the guard applies to the bloom this version of three really uses', () => {
    assert.match(three, /fragmentShader: highPassShader\.fragmentShader/);
    const pass = { materialHighPassFilter: { fragmentShader: shader, needsUpdate: false } };
    guardBloom(pass);
    assert.equal(pass.materialHighPassFilter.needsUpdate, true);
    assert.match(pass.materialHighPassFilter.fragmentShader, /texture2D\( tDiffuse, vUv \);\s+if \( any\( isnan\( texel \) \) \)/);
    assert.match(pass.materialHighPassFilter.fragmentShader, /clamp\( texel, vec4\( 0\.0 \), vec4\( 16\.0 \) \)/);
});

test('a bloom it cannot guard stops the page in development, not the frame in production', () => {
    assert.throws(() => guardBloom({ materialHighPassFilter: { fragmentShader: 'void main() {}' } }), /review bloom_guard/);
});

test('the room uses it', async () => {
    const app = await readFile(new URL('../../face/js/app.js', import.meta.url), 'utf8');
    assert.match(app, /guardBloom\(this\._bloomPass\);\s+this\.composer\.addPass\(this\._bloomPass\);/);
});
