// The sparkles burst around her head as soft round dots (2026-11-26). In video 6 they were flat
// squares (no map) in a 2x2 box at the scene origin, so in the room they floated in the middle of it,
// and their per-point size did nothing because PointsMaterial ignores a size attribute.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const threeUrl = new URL('../../companion/node_modules/three/build/three.module.js', import.meta.url).href;
const THREE = await import(threeUrl);
const source = readFileSync(new URL('../../face/js/particles.js', import.meta.url), 'utf8')
    .replace("from 'three'", `from '${threeUrl}'`);
const { ParticleSystem, SPARKLE_NEAREST, SPARKLE_SPREAD, createSparkleTexture, scalePointsPerVertex, sparkleOffset } =
    await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));

const FRAME = 1 / 60;

/** A canvas as far as createSparkleTexture uses one: it records the gradient and the fill. */
function fakeDocument() {
    const drawn = { gradients: [], fills: [] };
    const ctx = {
        fillStyle: null,
        createRadialGradient(...circles) {
            const gradient = { circles, stops: [], addColorStop(offset, color) { this.stops.push([offset, color]); } };
            drawn.gradients.push(gradient);
            return gradient;
        },
        fillRect(...rect) { drawn.fills.push({ rect, style: this.fillStyle }); },
    };
    const canvas = { width: 0, height: 0, getContext: (kind) => (kind === '2d' ? ctx : null) };
    return { doc: { createElement: (tag) => (tag === 'canvas' ? canvas : null) }, canvas, drawn };
}

function sparklesAround(getAnchor) {
    const system = new ParticleSystem(new THREE.Scene(), { getAnchor });
    system.init();
    return system;
}

/** Each sparkle's place in the world. */
function worldPoints(system) {
    const { position } = system.sparkles.geometry.attributes;
    const points = [];
    for (let i = 0; i < position.count; i++) {
        points.push(new THREE.Vector3().fromBufferAttribute(position, i).add(system.sparkles.position));
    }
    return points;
}

function alpha(color) {
    return Number(/rgba\([^)]*,\s*([\d.]+)\)/.exec(color)[1]);
}

test('they burst around her head wherever she is in the room, not around the scene origin', () => {
    const head = new THREE.Vector3(3.1, 0.2, 2.0); // on the bed
    const system = sparklesAround(target => target.copy(head));
    assert.equal(system.sparkles.visible, false, 'unlit, they are not drawn at all');
    system.setMood('hablando');
    assert.equal(system.sparkles.visible, true);
    assert.deepEqual(system.sparkles.position.toArray(), head.toArray());
    const points = worldPoints(system);
    assert.equal(points.length, 72);
    for (const point of points) {
        const across = Math.hypot(point.x - head.x, point.z - head.z);
        assert.ok(across >= SPARKLE_SPREAD.innerRadius - 1e-6 && across <= SPARKLE_SPREAD.outerRadius + 1e-6,
            `${across.toFixed(3)} from her head, outside her face and within reach`);
        assert.ok(point.y >= head.y - SPARKLE_SPREAD.below - 1e-6 && point.y <= head.y + SPARKLE_SPREAD.above + 1e-6);
        assert.ok(point.distanceTo(new THREE.Vector3(0, 1, 0)) > 2, 'nowhere near the middle of the room');
    }
    // The culling sphere covers where they are now, not where they were made.
    const sphere = system.sparkles.geometry.boundingSphere;
    assert.ok(sphere && sphere.radius <= Math.hypot(SPARKLE_SPREAD.outerRadius, SPARKLE_SPREAD.below) + 1e-6);
});

test('before an avatar has loaded they burst where the old box was centred', () => {
    const system = sparklesAround(() => null);
    system.setMood('emocionada');
    assert.deepEqual(system.sparkles.position.toArray(), [0, 1, 0]);
    const failing = sparklesAround(() => { throw new Error('no humanoid yet'); });
    const warn = console.warn;
    console.warn = () => {};
    try { failing.setSpeaking(true); } finally { console.warn = warn; }
    assert.deepEqual(failing.sparkles.position.toArray(), [0, 1, 0], 'a broken anchor costs only the placement');
});

test('lit, they go with her as she walks, smoothing out the small sways of her head', () => {
    const head = new THREE.Vector3(3.1, 0.2, 2.0);
    const system = sparklesAround(target => target.copy(head));
    system.setMood('hablando');
    head.set(1.0, 0.6, -0.5);
    system.update(FRAME, 0);
    const moved = system.sparkles.position.clone();
    assert.ok(moved.distanceTo(new THREE.Vector3(3.1, 0.2, 2.0)) > 0, 'they start to follow');
    assert.ok(moved.distanceTo(head) > 2, 'one frame does not snap them onto her');
    for (let t = FRAME; t < 1.2; t += FRAME) system.update(FRAME, t);
    assert.ok(system.sparkles.position.distanceTo(head) < 0.05, 'a second later they are around her again');
});

test('the timing and fade are as before: lit for 1.5 s, then fading out and shrinking', () => {
    const system = sparklesAround(target => target.set(3.1, 0.2, 2.0));
    system.setMood('hablando');
    const material = system.sparkles.material;
    const scales = system.sparkles.geometry.attributes.sparkleScale.array;
    assert.equal(material.opacity, 0.8);
    assert.ok([...scales].every(scale => scale >= 0.5 && scale <= 2), 'each point gets its own size');
    const before = [...scales];
    let t = 0;
    while (t < 1.45) { system.update(FRAME, t); t += FRAME; }
    assert.equal(material.opacity, 0.8, 'still lit at 1.45 s');
    assert.deepEqual([...scales], before, 'and still full size');
    for (let i = 0; i < 6; i++) { system.update(FRAME, t); t += FRAME; }
    assert.ok(material.opacity < 0.8 && material.opacity > 0, 'fading after 1.5 s');
    assert.ok(scales.every((scale, i) => scale < before[i]), 'and shrinking: the size attribute is used now');
    while (system.sparkles.visible && t < 3) { system.update(FRAME, t); t += FRAME; }
    assert.equal(system.sparkles.visible, false, 'gone, they leave the frame');
    assert.equal(material.opacity, 0);
    assert.ok(t > 1.85 && t < 2.0, `gone at ${t.toFixed(2)} s, 0.4 s after the fade starts`);
});

test('the points shader of this three reads the per-point size and stops growing them up close', () => {
    const system = sparklesAround(() => null);
    const geometry = system.sparkles.geometry;
    assert.ok(geometry.getAttribute('sparkleScale'), 'a per-point scale');
    assert.equal(geometry.getAttribute('size'), undefined, 'no size attribute that nothing reads');
    const shader = { vertexShader: THREE.ShaderLib.points.vertexShader };
    const warn = console.warn;
    console.warn = (message) => assert.fail(`unexpected warning: ${message}`);
    try { system.sparkles.material.onBeforeCompile(shader); } finally { console.warn = warn; }
    assert.match(shader.vertexShader, /^attribute float sparkleScale;\n/);
    assert.match(shader.vertexShader, /gl_PointSize = size \* sparkleScale;/);
    assert.doesNotMatch(shader.vertexShader, /gl_PointSize = size;/);
    // Farther away they still shrink; nearer than SPARKLE_NEAREST they keep the size they have there.
    assert.match(shader.vertexShader, /if \( isPerspective \) gl_PointSize \*= \( scale \/ max\( - mvPosition\.z, 1\.5000 \) \);/);
    assert.equal(SPARKLE_NEAREST, 1.5);
    assert.notEqual(system.sparkles.material.customProgramCacheKey(), new THREE.PointsMaterial().customProgramCacheKey());
});

test('a points shader it does not recognise is left as it is', () => {
    const material = scalePointsPerVertex(new THREE.PointsMaterial(), { nearest: 1.5 });
    const shader = { vertexShader: 'void main() { gl_PointSize = 4.0; }' };
    const warnings = [];
    const warn = console.warn;
    console.warn = (message) => warnings.push(message);
    try { material.onBeforeCompile(shader); } finally { console.warn = warn; }
    assert.equal(shader.vertexShader, 'void main() { gl_PointSize = 4.0; }');
    assert.equal(warnings.length, 2, 'one warning for each line it could not find');
});

test('without `nearest` the distance shrink is three\'s own', () => {
    const shader = { vertexShader: THREE.ShaderLib.points.vertexShader };
    scalePointsPerVertex(new THREE.PointsMaterial()).onBeforeCompile(shader);
    assert.match(shader.vertexShader, /if \( isPerspective \) gl_PointSize \*= \( scale \/ - mvPosition\.z \);/);
    assert.match(shader.vertexShader, /gl_PointSize = size \* sparkleScale;/);
});

test('each sparkle is a soft round dot: bright in the middle, clear at the edges and corners', () => {
    const { doc, canvas, drawn } = fakeDocument();
    const texture = createSparkleTexture(doc, 64);
    assert.ok(texture.isTexture && texture.image === canvas);
    assert.equal(canvas.width, 64);
    assert.equal(canvas.height, 64);
    const [gradient] = drawn.gradients;
    assert.deepEqual(gradient.circles, [32, 32, 0, 32, 32, 32], 'centred, out to the edge of the square');
    const stops = gradient.stops;
    assert.equal(stops[0][0], 0);
    assert.equal(alpha(stops[0][1]), 1);
    assert.equal(stops.at(-1)[0], 1);
    assert.equal(alpha(stops.at(-1)[1]), 0, 'past the circle (the corners) nothing shows');
    assert.ok(stops.every(([, color], i) => i === 0 || alpha(color) < alpha(stops[i - 1][1])), 'it only fades outwards');
    assert.ok(stops.every(([, color]) => /^rgba\(255,255,255,/.test(color)), 'white, so the gold comes from the material');
    assert.deepEqual(drawn.fills, [{ rect: [0, 0, 64, 64], style: gradient }]);
    assert.equal(createSparkleTexture(undefined), null, 'no canvas in Node: no texture, no crash');
});

test('the sparkles use the dot, blended as light', () => {
    const { doc } = fakeDocument();
    const previous = globalThis.document;
    globalThis.document = doc;
    let system;
    try { system = sparklesAround(() => null); } finally {
        if (previous === undefined) delete globalThis.document; else globalThis.document = previous;
    }
    const material = system.sparkles.material;
    assert.ok(material.map?.isTexture, 'a round sprite, not the default square');
    assert.equal(material.transparent, true);
    assert.equal(material.blending, THREE.AdditiveBlending);
    assert.equal(material.depthWrite, false);
});

test('a sparkle offset stays in its ring', () => {
    const lowest = sparkleOffset(() => 0);
    assert.deepEqual(lowest.map(v => Number(v.toFixed(6))), [SPARKLE_SPREAD.innerRadius, -SPARKLE_SPREAD.below, 0]);
    const highest = sparkleOffset(() => 0.999999);
    assert.ok(Math.hypot(highest[0], highest[2]) <= SPARKLE_SPREAD.outerRadius);
    assert.ok(highest[1] <= SPARKLE_SPREAD.above);
});

test('the room gives them her head', () => {
    const app = readFileSync(new URL('../../face/js/app.js', import.meta.url), 'utf8');
    assert.match(app, /new ParticleSystem\(this\.scene, \{ getAnchor: target => this\._sparkleAnchor\(target\) \}\)/);
    assert.match(app, /_sparkleAnchor\(target\) \{\s+const head = this\.avatarManager\?\.avatar\?\.vrm\?\.humanoid\?\.getRawBoneNode\?\.\('head'\);\s+return head \? head\.getWorldPosition\(target\) : null;/);
});
