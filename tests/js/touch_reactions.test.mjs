// Touch reactions (2026-11-27): petting her head, poking and pulling her cheeks, on any VRM or PMX.
// The springs, the mood, the face recipe, the cheek weights and the shader patch are checked here;
// the rig and the controller run end to end on a skinned sphere «head».
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { selectExpressions } from '../../face/js/pmx_expressions.js';

const threeUrl = new URL('../../companion/node_modules/three/build/three.module.js', import.meta.url).href;
const THREE = await import(threeUrl);
const source = readFileSync(new URL('../../face/js/touch_reactions.js', import.meta.url), 'utf8')
    .replace("from 'three'", `from '${threeUrl}'`);
const touch = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
const {
    ANNOY_POKES, ANNOY_WINDOW, CHEEK_TOP, STARTLE_AFFECTION, TOUCH_SHADER_KEY, TOUCH_SHAPE, TouchMood, TouchReactions, TouchRig,
    cheekCentre, cheekWeight, faceFrame, patchTouchMaterial, patchTouchShader, rayTriangles, springStep, toFrame, touchFace,
    touchUniforms, touchZone,
} = touch;

test('the spring is integrated exactly: many small steps land where one big step does', () => {
    let [x, v] = [1, 0];
    for (let i = 0; i < 60; i++) [x, v] = springStep(x, v, 0, 1 / 60);
    const [x1, v1] = springStep(1, 0, 0, 1);
    assert.ok(Math.abs(x - x1) < 1e-9 && Math.abs(v - v1) < 1e-9, `${x} vs ${x1}`);
    // Overdamped and critical springs too (the head's lean uses other constants).
    for (const [omega, decay] of [[3, 5], [4, 4]]) {
        let [a, b] = [1, 0.5];
        for (let i = 0; i < 30; i++) [a, b] = springStep(a, b, 0.2, 1 / 30, omega, decay);
        const [c, d] = springStep(1, 0.5, 0.2, 1, omega, decay);
        assert.ok(Math.abs(a - c) < 1e-9 && Math.abs(b - d) < 1e-9, `${omega}/${decay}`);
    }
});

test('a released cheek bounces past rest once and settles (ω 22, decay 4.6)', () => {
    let [x, v] = [0.28, 0], lowest = Infinity;
    for (let t = 0; t < 1.5; t += 1 / 60) {
        [x, v] = springStep(x, v, 0, 1 / 60);
        lowest = Math.min(lowest, x);
    }
    assert.ok(lowest < -0.05, `the cheek should spring back past rest, lowest ${lowest}`);
    assert.ok(Math.abs(x) < 1e-3, `settled at ${x}`);
});

test(`more than ${ANNOY_POKES} pokes within ${ANNOY_WINDOW} s annoy her, then she calms down`, () => {
    const mood = new TouchMood();
    for (let i = 0; i < ANNOY_POKES; i++) { assert.equal(mood.poke(), false); mood.update(0.4); }
    assert.equal(mood.annoyed, 0);
    assert.equal(mood.poke(), true, 'the fifth poke annoys her');
    assert.equal(mood.annoyed, 1);
    assert.equal(mood.poke(), false, 'only the poke that starts it says so');
    for (let t = 0; t < 2; t += 0.1) mood.update(0.1);
    assert.equal(mood.annoyed, 1, 'she stays cross a moment after the last poke');
    for (let t = 0; t < 8; t += 0.1) mood.update(0.1);
    assert.equal(mood.annoyed, 0);
    const slow = new TouchMood();
    for (let i = 0; i < 10; i++) { slow.poke(); slow.update(0.8); }
    assert.equal(slow.annoyed, 0, 'pokes spread out never add up');
});

test('petting makes her affectionate and calms her faster', () => {
    const petted = new TouchMood(), left = new TouchMood();
    for (const mood of [petted, left]) for (let i = 0; i <= ANNOY_POKES; i++) mood.poke();
    for (let t = 0; t < 3; t += 0.1) {
        petted.pet(0.1, 1); petted.update(0.1);
        left.update(0.1);
    }
    assert.ok(petted.affection > 0.9, `affection ${petted.affection}`);
    assert.ok(petted.annoyed < left.annoyed, `${petted.annoyed} < ${left.annoyed}`);
    for (let t = 0; t < 20; t += 0.1) petted.update(0.1);
    assert.equal(petted.affection, 0);
});

const hasAll = (...names) => (name) => names.includes(name);

test('petted: the expression that shuts her eyes does it, and the blink gives way', () => {
    const face = touchFace({ closed: 1 }, hasAll('relaxed', 'happy', 'blink', 'blush', 'heartEyes'), (n) => (n === 'relaxed' ? 1 : 0.4));
    assert.ok(face.set.relaxed > 0.9);
    assert.equal(face.scale.blink, 0);
    assert.equal(face.scale.heartEyes, 0, 'heart eyes need open eyes');
    assert.ok(face.set.blush > 0.8 && face.shader.blush === 0, 'a model with a blush uses it');
});

test('petted, on a face whose smile leaves the eyes open (IRIS): the lids close and the shader blushes', () => {
    const face = touchFace({ closed: 1 }, hasAll('relaxed', 'happy', 'blink'), (n) => (n === 'happy' ? 0.19 : 0.04));
    assert.ok(face.set.blink > 0.8);
    assert.ok(face.set.happy > 0);
    assert.equal(face.scale.blink, undefined);
    assert.ok(face.shader.blush > 0.7);
});

test('annoyed: angry, a pout from the cheek-puff morph or the shader, happiness put away', () => {
    const mmd = touchFace({ annoyed: 1 }, hasAll('angry', 'cheekPuff', 'happy', 'jitome'));
    assert.ok(mmd.set.angry > 0.8 && mmd.set.cheekPuff > 0.9);
    assert.equal(mmd.scale.happy, 0);
    const vrm = touchFace({ annoyed: 1 }, hasAll('angry', 'happy', 'blink'));
    assert.ok(vrm.shader.puff > 0.7, 'without a puff morph the shader puffs her cheeks');
    assert.ok(mmd.shader.puff > 0 && mmd.shader.puff < vrm.shader.puff * 0.7, 'with one it only adds volume to the morph');
    const speaking = touchFace({ annoyed: 1, speaking: 1 }, hasAll('angry'));
    assert.ok(speaking.shader.puff < vrm.shader.puff * 0.5, 'the vowels lead while she speaks');
    const calming = touchFace({ annoyed: 0.5 }, hasAll('angry', 'jitome'));
    assert.ok(calming.set.jitome > calming.set.angry, 'a glare as she calms down');
});

test('the poked side flinches: MMD wink morphs, or a VRM eyelid', () => {
    assert.ok(touchFace({ flinchL: 1 }, hasAll('winkLeft', 'blinkLeft')).set.winkLeft > 0.5);
    const vrm = touchFace({ flinchR: 1 }, hasAll('blinkLeft', 'blinkRight'));
    assert.ok(vrm.set.blinkRight > 0.5 && !vrm.set.blinkLeft);
});

test('cheek weights: cheeks move, the face a little, eyes, lips and the back of the head not at all', () => {
    const frame = { E: 1, center: [0, 0.3, -0.9] };
    const eye = { x: 0.5, y: 0, ax: 0.3, ay: 0.2 };
    const eyes = { left: eye, right: { ...eye, x: -0.5 } };
    const mouth = { x: 0, y: -1.05, ax: 0.3, ay: 0.14 };
    const at = (x, y, z, lip = 0) => cheekWeight(frame, x, y, z, { eyes, mouth, lip });
    const [cx, cy] = cheekCentre(eye);
    assert.ok(at(cx, cy, 0.2)[0] > 0.8, 'the middle of a cheek moves (almost) fully');
    assert.ok(at(cx, cy, 0.2)[1] > 0.8, 'and is where the blush goes');
    assert.equal(at(0.5, 0, 0.3)[0], 0, 'eye');
    assert.equal(at(0.5, -0.22, 0.3)[0], 0, 'just under the eye: the skin over the eye white stays');
    assert.equal(at(0, -1.05, 0.4)[0], 0, 'lips');
    assert.equal(at(0.25, -1.0, 0.35, 0.8)[0], 0, 'a vertex the «a» moves a lot');
    assert.equal(at(0, 0, -1.8)[0], 0, 'back of the head');
    const forehead = at(0, 0.6, 0.2)[0];
    assert.ok(forehead > 0.1 && forehead < 0.4, `forehead ${forehead}`);
    assert.equal(at(0, 1.4, -0.2)[0], 0, 'top of the head');
});

test('touch zones: top of the head pets, cheeks by side, the rest of the face pokes', () => {
    assert.equal(touchZone(0, 1.2, 0), 'top');
    assert.equal(touchZone(0.3, 0.3, -0.5, { onHair: true }), 'top');
    assert.equal(touchZone(0.55, -0.6, 0.2), 'cheekL');
    assert.equal(touchZone(-0.55, -0.6, 0.2), 'cheekR');
    assert.equal(touchZone(0, 0.4, 0.4), 'face');
    assert.equal(touchZone(0, -0.6, -1.8), 'head', 'the back of the head below the crown is no touch');
    // Depth is read against the head's box, so eye bones set far back (IRIS) still work.
    assert.equal(touchZone(0, 0.4, 1.0, { center: 0.25, front: 1.24 }), 'face');
    assert.equal(touchZone(0, -0.3, -0.5, { center: 0.25, front: 1.24 }), 'head');
});

// 2026-11-27, polish: Lily's and Mornye's locks over their cheeks did nothing (or petted her).
test('hair over a cheek, below her eyes and inside the cheek band, is that cheek', () => {
    assert.equal(touchZone(1.22, -0.5, -0.5, { onHair: true }), 'cheekL', 'a lock at the side of the cheek');
    assert.equal(touchZone(-1.22, -0.5, -0.5, { onHair: true }), 'cheekR');
    assert.equal(touchZone(1.22, -0.5, -0.5), 'head', 'bare skin that far out is not the cheek');
    assert.equal(touchZone(1.6, -0.5, -0.5, { onHair: true }), 'head', 'hair beside the face is not the cheek');
    assert.equal(touchZone(0.6, 0.3, 0, { onHair: true }), 'top', 'hair above the eyes still pets her');
    assert.equal(touchZone(0.6, CHEEK_TOP + 0.05, 0.2, { onHair: true }), 'face', 'at eye height it is not the cheek');
});

test('a poke or a pull wakes her from petting: the smile that shuts her eyes gives way', () => {
    const mood = new TouchMood();
    for (let t = 0; t < 4; t += 0.1) mood.pet(0.1, 1);
    assert.ok(mood.affection > 0.9);
    mood.startle();
    assert.ok(mood.affection <= STARTLE_AFFECTION && mood.affectionHold === 0, 'most of the affection goes, without the hold');
    // Carlotta: her «happy» shuts her eyes (closure 0.54 measured); IRIS's does not (0.19).
    const carlotta = (name) => (name === 'happy' ? 0.54 : 0.18);
    const has = hasAll('happy', 'relaxed', 'blink', 'blinkLeft', 'blinkRight');
    const woken = touchFace({ alert: 1, affection: 0.3, flinchL: 0.8 }, has, carlotta);
    assert.ok(woken.scale.happy <= 0.1, 'an eye-shutting smile is put away while the finger is on her');
    assert.equal(woken.set.happy, undefined, 'and touch does not raise it either');
    assert.equal(woken.scale.relaxed, undefined, 'an expression that leaves the eyes open is left alone');
    assert.ok(woken.set.blinkLeft > 0.3, 'the poked side still flinches');
    const iris = touchFace({ alert: 1, affection: 0.3 }, has, (name) => (name === 'happy' ? 0.19 : 0.04));
    assert.equal(iris.scale.happy, undefined, 'IRIS keeps her smile: it does not shut her eyes');
    const petted = touchFace({ closed: 1, alert: 1 }, has, carlotta);
    assert.ok(!(petted.set.happy > 0.1), 'a finger on her cheek wins over the petting closed eyes');
});

test('the face frame faces where her eyelids and lips are, even with eye bones at her neck', () => {
    const V = (x, y, z) => new THREE.Vector3(x, y, z);
    // Eye bones level with the middle of the head (IRIS's are as far back as her neck).
    const eyeLeft = V(0.03, 1.5, 0), eyeRight = V(-0.03, 1.5, 0);
    const frame = faceFrame({ eyeLeft, eyeRight, centroid: V(0, 1.5, 0.005), front: V(0, 1.47, 0.08), headPoints: [V(0, 1.5, 0.1), V(0, 1.5, -0.1)] });
    assert.ok(frame.forward.z > 0.99, `forward ${frame.forward.toArray()}`);
    assert.ok(frame.right.x > 0.99);
    const local = toFrame(frame, V(0.03, 1.5, 0.06));
    assert.deepEqual([+local.x.toFixed(6), +local.y.toFixed(6), +local.z.toFixed(6)], [0.5, 0, 1]);
});

const MTOON_LIKE = {
    vertexShader: '#include <common>\nvoid main() {\n#include <beginnormal_vertex>\n#include <morphnormal_vertex>\n#include <skinnormal_vertex>\n#include <begin_vertex>\n#include <morphtarget_vertex>\n#include <skinning_vertex>\n}',
    // MToon writes its final colour in postCorrection(), above main().
    fragmentShader: 'void postCorrection() {\n#include <tonemapping_fragment>\n#include <colorspace_fragment>\n}\nvoid main() {\ngl_FragColor = vec4(1.0);\npostCorrection();\n}',
};

test('the shader patch moves skin before skinning and blushes before colour space', () => {
    const shader = { ...MTOON_LIKE };
    assert.equal(patchTouchShader(shader), true);
    const v = shader.vertexShader, f = shader.fragmentShader;
    assert.ok(v.indexOf('attribute vec2 touchWeight') < v.indexOf('void main()'));
    assert.ok(v.indexOf('irisTouchOffset(position, normal') > v.indexOf('#include <morphnormal_vertex>'));
    assert.ok(v.indexOf('irisTouchOffset(position, normal') < v.indexOf('#include <skinnormal_vertex>'), 'normals tilt before they are skinned');
    assert.ok(v.indexOf('transformed += irisTouchMove') < v.indexOf('#include <skinning_vertex>'));
    assert.ok(f.indexOf('varying vec2 vIrisTouch') < f.indexOf('void postCorrection'), 'declared before the function that uses it');
    assert.ok(f.indexOf('irisBlush') < f.indexOf('#include <colorspace_fragment>'));
    assert.ok(f.includes('#ifndef OUTLINE'), 'outlines are not blushed');
    assert.ok(v.indexOf('varying float vIrisTouchPress') < v.indexOf('void main()'), 'the dimple shadow is passed on');
    assert.ok(f.indexOf('varying float vIrisTouchPress') < f.indexOf('void postCorrection') && f.indexOf('uniform vec4 irisTouchStyle') < f.indexOf('void postCorrection'));
    assert.ok(f.indexOf('vIrisTouchPress * irisTouchStyle.w') > f.indexOf('#ifndef OUTLINE'), 'and never darkens the outline');
    const broken = { vertexShader: 'void main() {}', fragmentShader: 'void main() {}' };
    assert.equal(patchTouchShader(broken), false);
    assert.equal(broken.vertexShader, 'void main() {}');
});

test('a patched material keeps its own compile step and gets its own program', () => {
    const material = new THREE.MeshBasicMaterial();
    const calls = [];
    material.onBeforeCompile = () => calls.push('own');
    material.customProgramCacheKey = () => 'mtoon:defines';
    const uniforms = { irisTouchShape: { value: new THREE.Vector4() } };
    assert.equal(patchTouchMaterial(material, uniforms), true);
    assert.equal(patchTouchMaterial(material, uniforms), false, 'once only');
    assert.equal(material.customProgramCacheKey(), `mtoon:defines|${TOUCH_SHADER_KEY}`);
    const shader = { ...MTOON_LIKE, uniforms: {} };
    material.onBeforeCompile(shader);
    assert.deepEqual(calls, ['own']);
    assert.equal(shader.uniforms.irisTouchShape, uniforms.irisTouchShape);
});

test('ray against triangles: the nearest hit in front of the origin', () => {
    const tris = new Float32Array([-1, -1, 0, 1, -1, 0, 0, 1, 0, -1, -1, -2, 1, -1, -2, 0, 1, -2]);
    const hit = rayTriangles(new THREE.Vector3(0, 0, 5), new THREE.Vector3(0, 0, -2), tris);
    assert.equal(hit.index, 0);
    assert.ok(Math.abs(hit.t - 2.5) < 1e-9, 'with a direction of length 2, t counts in its units');
    assert.equal(rayTriangles(new THREE.Vector3(5, 5, 5), new THREE.Vector3(0, 0, -1), tris).index, -1);
    const under = rayTriangles(new THREE.Vector3(0, 0, 5), new THREE.Vector3(0, 0, -2), tris, { t: Infinity, index: -1 }, Uint8Array.from([1, 0]));
    assert.equal(under.index, 1, 'skipped triangles (the hair) let the ray reach the one behind');
});

test('the dent is soft: wide, shallow, partly tilted normals and a round shadow', () => {
    // Up close the old dent read as a thin dark crease on IRIS (2026-11-27).
    assert.ok(TOUCH_SHAPE.pokeRadius >= 0.4 && TOUCH_SHAPE.pokeDepth <= 0.25);
    assert.ok(TOUCH_SHAPE.tilt < 1 && TOUCH_SHAPE.rim < 0.22 && TOUCH_SHAPE.shade > 0);
    const style = touchUniforms().irisTouchStyle.value;
    assert.deepEqual([style.x, style.y, style.z, style.w], [TOUCH_SHAPE.tilt, TOUCH_SHAPE.aside, TOUCH_SHAPE.rim, TOUCH_SHAPE.shade]);
});

// 2026-11-27, the user's find in Blender: on Lily, (○｀ε´○) with もぐもぐ puffs her cheeks.
test('MMD cheek puff: the model own puff, or (○｀ε´○) and もぐもぐ together', () => {
    const morph = (name, index) => ({ name, type: 1, elements: [{ index, position: [1, 0, 0] }] });
    const lily = { morphs: [morph('(○｀ε´○)', 1), morph('もぐもぐ', 2), morph('ウィンク', 3), morph('ウィンク右', 4), morph('じと目', 5), morph('はぅ', 6)], metadata: {} };
    const used = selectExpressions(lily).used;
    assert.deepEqual(used.cheekPuff, ['(○｀ε´○)', 'もぐもぐ']);
    assert.deepEqual([used.winkLeft, used.winkRight, used.jitome, used.squeeze], [['ウィンク'], ['ウィンク右'], ['じと目'], ['はぅ']]);
    assert.ok(lily.morphs.some((m) => m.name === 'cheekPuffMouth'), 'a mouth part: it gives way while she speaks');
    const feixue = { morphs: [morph('頬を膨', 1), morph('もぐもぐ', 2), morph('#', 3)], metadata: {} };
    const own = selectExpressions(feixue).used;
    assert.deepEqual(own.cheekPuff, ['頬を膨']);
    assert.deepEqual(own.markAnger, ['#']);
});

/** A skinned sphere «head» with eye bones, facing +Z (her left at +X), and a mock humanoid. */
function sphereAvatar({ hair = false } = {}) {
    const head = new THREE.Bone(), neck = new THREE.Bone(), leftEye = new THREE.Bone(), rightEye = new THREE.Bone();
    neck.position.set(0, 1.4, 0);
    head.position.set(0, 0.06, 0);
    leftEye.position.set(0.03, 0.08, 0.06);
    rightEye.position.set(-0.03, 0.08, 0.06);
    neck.add(head); head.add(leftEye, rightEye);
    const geometry = new THREE.SphereGeometry(0.1, 48, 32).translate(0, 1.54, 0);
    const count = geometry.getAttribute('position').count;
    geometry.setAttribute('skinIndex', new THREE.Uint16BufferAttribute(new Uint16Array(count * 4), 4));
    geometry.setAttribute('skinWeight', new THREE.Float32BufferAttribute(new Float32Array(count * 4).map((_, i) => (i % 4 === 0 ? 1 : 0)), 4));
    const material = new THREE.MeshBasicMaterial();
    const mesh = new THREE.SkinnedMesh(geometry, material);
    const scene = new THREE.Group();
    scene.add(neck, mesh);
    const skeleton = new THREE.Skeleton([head, neck, leftEye, rightEye]);
    let lock = null;
    if (hair) {
        // A lock of hair hanging just in front of her left cheek (her left is +X).
        const plane = new THREE.PlaneGeometry(0.04, 0.03, 6, 6).translate(0.032, 1.503, 0.095);
        const n = plane.getAttribute('position').count;
        plane.setAttribute('skinIndex', new THREE.Uint16BufferAttribute(new Uint16Array(n * 4), 4));
        plane.setAttribute('skinWeight', new THREE.Float32BufferAttribute(new Float32Array(n * 4).map((_, i) => (i % 4 === 0 ? 1 : 0)), 4));
        lock = new THREE.SkinnedMesh(plane, Object.assign(new THREE.MeshBasicMaterial({ side: THREE.DoubleSide }), { name: 'Hair_front' }));
        scene.add(lock);
    }
    scene.updateMatrixWorld(true);
    mesh.bind(skeleton);
    lock?.bind(skeleton);
    const values = new Map();
    const expression = (name) => (['blink', 'angry', 'happy', 'relaxed', 'blinkLeft', 'blinkRight'].includes(name) ? { binds: [{ primitives: [], index: 0, weight: 1 }] } : null);
    const normalizedHead = new THREE.Object3D();
    const normalizedRoot = new THREE.Object3D();
    normalizedRoot.add(normalizedHead);
    scene.add(normalizedRoot);
    const bones = { head, neck, leftEye, rightEye };
    const vrm = {
        scene,
        humanoid: { getRawBoneNode: (n) => bones[n] ?? null, getNormalizedBoneNode: (n) => (n === 'head' ? normalizedHead : null) },
        expressionManager: { getExpression: expression, getValue: (n) => (expression(n) ? values.get(n) ?? 0 : null), setValue: (n, v) => values.set(n, v) },
        springBoneManager: null,
    };
    return { avatar: { vrm, pulseExpression: () => false }, mesh, values, normalizedHead };
}

test('the rig finds the face of any humanoid: frame, contact zones and cheek weights', () => {
    const { avatar, mesh } = sphereAvatar();
    const rig = TouchRig.build(avatar);
    assert.ok(rig, 'built');
    assert.ok(rig.frame.forward.z > 0.99 && rig.frame.right.x > 0.99, 'facing +Z, her left at +X');
    assert.ok(Math.abs(rig.frame.E - 0.06) < 1e-6);
    const weights = mesh.geometry.getAttribute('touchWeight');
    assert.ok(weights, 'weights measured');
    assert.ok(mesh.material.userData.irisTouch, 'material patched');
    const front = (x, y) => rig.pick(new THREE.Ray(new THREE.Vector3(x, y, 1), new THREE.Vector3(0, 0, -1)));
    const [cx, cy] = cheekCentre(rig.eyes.left);
    const cheek = front(cx * 0.06, 1.54 + cy * 0.06);
    assert.equal(cheek?.zone, 'cheekL');
    assert.equal(front(-cx * 0.06, 1.54 + cy * 0.06)?.zone, 'cheekR');
    assert.equal(rig.pick(new THREE.Ray(new THREE.Vector3(0, 2, 0), new THREE.Vector3(0, -1, 0)))?.zone, 'top');
    assert.equal(front(0.5, 1.54), null, 'a miss');
});

test('before any touch nothing moves: no phantom pokes, pulls or puff', () => {
    // THREE.Vector4() defaults to w = 1: four phantom pokes flattened IRIS's face from load until the
    // first touch (2026-11-27). The rest values must be zero, and written even with nothing going on.
    const rest = touchUniforms();
    assert.ok(rest.irisTouchPoke.value.every((p) => p.w === 0), 'pokes start with amplitude 0');
    assert.ok(rest.irisTouchDelta.value.every((d) => d.lengthSq() === 0), 'no pull');
    assert.equal(rest.irisTouchShape.value.y, 0, 'no puff');
    const { avatar } = sphereAvatar();
    const controller = new TouchReactions({ canvas: null, camera: new THREE.PerspectiveCamera(), getAvatar: () => avatar, now: () => 0 });
    controller.update();
    const uniforms = controller.rig.uniforms;
    for (const p of uniforms.irisTouchPoke.value) p.w = 1; // as a material kept from an earlier rig might hold
    controller.rig = null;
    controller.update();
    avatar.touch.apply(avatar, 1 / 60);
    avatar.touch.restore();
    assert.ok(controller.rig.uniforms.irisTouchPoke.value.every((p) => p.w === 0), 'an idle frame after the rig is built writes the rest values');
});

test('the controller: a poke dents the cheek and springs back; every value it wrote goes back', () => {
    const { avatar, values, normalizedHead } = sphereAvatar();
    const camera = new THREE.PerspectiveCamera(30, 1, 0.01, 10);
    camera.position.set(0, 1.54, 0.8);
    camera.lookAt(0, 1.54, 0);
    let now = 0;
    const canvas = { getBoundingClientRect: () => ({ left: 0, top: 0, width: 400, height: 400 }), style: {} };
    const moods = [];
    const controller = new TouchReactions({ canvas: null, camera, getAvatar: () => avatar, onMood: (m) => moods.push(m), now: () => now });
    controller.canvas = canvas;
    controller.update();
    assert.equal(avatar.touch, controller.layer, 'the avatar calls it around vrm.update');
    const rig = controller.rig;
    const [cx, cy] = cheekCentre(rig.eyes.left);
    const screen = new THREE.Vector3(cx * 0.06, 1.54 + cy * 0.06, 0.09).project(camera);
    const event = { clientX: (screen.x + 1) * 200, clientY: (1 - screen.y) * 200, pointerId: 1, pointerType: 'mouse', button: 0, preventDefault() {}, stopImmediatePropagation() {} };
    const frame = () => { avatar.touch.apply(avatar, 1 / 60); const w = rig.uniforms.irisTouchPoke.value.reduce((sum, p) => sum + p.w, 0); avatar.touch.restore(); now += 1 / 60; return w; };
    values.set('blinkLeft', 0.1);
    controller._down(event);
    assert.equal(controller.gesture?.kind, 'press');
    let deepest = 0;
    for (let i = 0; i < 6; i++) deepest = Math.max(deepest, frame());
    assert.ok(deepest > 0, 'the cheek is dented');
    controller._up(event);
    let lowest = 0;
    for (let i = 0; i < 90; i++) lowest = Math.min(lowest, frame());
    assert.ok(lowest < 0, 'it bounces back past rest');
    assert.equal(values.get('blinkLeft'), 0.1, 'the eyelid it closed for the flinch is given back');
    assert.ok(normalizedHead.quaternion.equals(new THREE.Quaternion()), 'and the head');
    // Five more quick pokes: she is annoyed, through the emotion system.
    for (let k = 0; k < 5; k++) { controller._down(event); frame(); controller._up(event); for (let i = 0; i < 6; i++) frame(); }
    assert.deepEqual(moods, ['enojada']);
    for (let i = 0; i < 60 * 9; i++) frame();
    assert.deepEqual(moods, ['enojada', null], 'and gives it back once calm');
});

/** A controller on the sphere head, seen from the front by a 400×400 «canvas». */
function sphereController(options = {}, avatarOptions = {}) {
    const sphere = sphereAvatar(avatarOptions);
    const camera = new THREE.PerspectiveCamera(30, 1, 0.01, 10);
    camera.position.set(0, 1.54, 0.8);
    camera.lookAt(0, 1.54, 0);
    camera.updateMatrixWorld();
    const clock = { now: 0 };
    const moods = [], calls = [];
    const sounds = { poke: (s) => calls.push(['poke', s]), release: (s) => calls.push(['release', s]) };
    const controller = new TouchReactions({ canvas: null, camera, getAvatar: () => sphere.avatar, onMood: (m) => moods.push(m), now: () => clock.now, sounds, ...options });
    controller.canvas = { getBoundingClientRect: () => ({ left: 0, top: 0, width: 400, height: 400 }), style: {} };
    controller.update();
    const screen = (x, y, z) => { const v = new THREE.Vector3(x, y, z).project(camera); return { x: (v.x + 1) * 200, y: (1 - v.y) * 200 }; };
    const event = (at, id = 1) => ({ clientX: at.x, clientY: at.y, pointerId: id, pointerType: 'mouse', button: 0, preventDefault() {}, stopImmediatePropagation() {} });
    const frame = () => { sphere.avatar.touch.apply(sphere.avatar, 1 / 60); const seen = new Map(sphere.values); sphere.avatar.touch.restore(); clock.now += 1 / 60; return seen; };
    return { ...sphere, camera, clock, moods, calls, controller, screen, event, frame };
}

test('a lock of hair over her cheek: the touch is that cheek, and the dent goes on the skin under it', () => {
    const { controller, screen, event, frame, calls } = sphereController({}, { hair: true });
    const rig = controller.rig;
    const [cx, cy] = cheekCentre(rig.eyes.left);
    const at = screen(cx * 0.06, 1.54 + cy * 0.06, 0.09);
    const ray = controller._rayAt(at.x, at.y);
    const hit = rig.pick(ray);
    assert.equal(hit?.zone, 'cheekL');
    assert.equal(hit.onHair, true, 'the ray met the hair first');
    assert.ok(hit.bind.z < 0.092, `the touch is on the skin under the lock (z ${hit.bind.z.toFixed(4)}), not on the hair (0.095)`);
    controller._down(event(at));
    assert.equal(controller.gesture?.kind, 'press', 'a poke, not petting');
    for (let i = 0; i < 4; i++) frame();
    controller._up(event(at));
    frame();
    assert.ok(controller.levels.flinchL > 0, 'the poked side flinches');
    assert.deepEqual(calls.filter(([kind]) => kind === 'poke').length, 1, 'and it pops');
});

test('the press follows the finger without overshooting; the release still bounces', () => {
    const { controller, screen, event, frame } = sphereController();
    const [cx, cy] = cheekCentre(controller.rig.eyes.left);
    const at = screen(cx * 0.06, 1.54 + cy * 0.06, 0.09);
    controller._down(event(at));
    let deepest = 0;
    for (let i = 0; i < 40; i++) { frame(); deepest = Math.max(deepest, controller.pokes[0]?.x ?? 0); }
    assert.ok(deepest <= TOUCH_SHAPE.pokeDepth + 1e-6, `held: at most the press depth (${deepest} > ${TOUCH_SHAPE.pokeDepth} sank 1.5× and looked like a crease)`);
    assert.ok(deepest > TOUCH_SHAPE.pokeDepth * 0.98, 'and it gets there');
    controller._up(event(at));
    let lowest = 0;
    for (let i = 0; i < 60; i++) { frame(); lowest = Math.min(lowest, controller.pokes[0]?.x ?? 0); }
    assert.ok(lowest < -0.05 * TOUCH_SHAPE.pokeDepth, 'let go, the cheek springs back past rest');
});

test('after petting she is affectionate; a poke ends that mood at once and her eyes open', () => {
    const { controller, screen, event, frame, moods, values, calls } = sphereController();
    const rig = controller.rig;
    rig.closure = (name) => (name === 'happy' ? 0.6 : 0.1); // her «happy» shuts her eyes (Carlotta)
    const top = screen(0, 1.635, 0.02);
    controller._down(event(top));
    assert.equal(controller.gesture?.kind, 'pet');
    for (let i = 0; i < 60 * 3; i++) {
        controller._move({ ...event({ x: top.x + Math.sin(i / 6) * 20, y: top.y }) });
        frame();
    }
    controller._up(event(top));
    assert.deepEqual(calls, [], 'petting is silent: the user chose the pops and the releases alone');
    for (let i = 0; i < 30; i++) frame();
    assert.deepEqual(moods, ['cariñosa']);
    // The emotion system shows «cariñosa»: her eye-shutting smile at 0.6.
    values.set('happy', 0.6);
    const [cx, cy] = cheekCentre(rig.eyes.left);
    const cheek = screen(cx * 0.06, 1.54 + cy * 0.06, 0.09);
    controller._down(event(cheek));
    assert.deepEqual(moods, ['cariñosa', null], 'the touch-owned «cariñosa» ends with the poke');
    let seen;
    for (let i = 0; i < 8; i++) seen = frame();
    assert.ok(seen.get('happy') < 0.15, `while the finger is on her the smile gives way (happy ${seen.get('happy')})`);
    assert.equal(values.get('happy'), 0.6, 'and the emotion keeps its own value');
    controller._up(event(cheek));
    for (let i = 0; i < 120; i++) seen = frame();
    assert.ok(seen.get('happy') > 0.55, 'a moment later it is the emotion system\'s again');
});

test('a pull let go plays the release, as big as the stretch', () => {
    const { controller, screen, event, frame, calls } = sphereController();
    const [cx, cy] = cheekCentre(controller.rig.eyes.right);
    const at = screen(cx * 0.06, 1.54 + cy * 0.06, 0.09);
    controller._down(event(at));
    for (let k = 1; k <= 12; k++) { controller._move(event({ x: at.x - k * 5, y: at.y })); frame(); }
    assert.equal(controller.gesture?.kind, 'pull');
    for (let i = 0; i < 20; i++) frame();
    controller._up(event({ x: at.x - 60, y: at.y }));
    const release = calls.find(([kind]) => kind === 'release');
    assert.ok(release && release[1] > 0.5, `release ${release?.[1]}`);
    assert.equal(calls.filter(([kind]) => kind === 'poke').length, 0, 'a pull is not a pop');
});

test('the Companion tolerance: a press just beside her head still touches her', () => {
    const exact = sphereController();
    const near = sphereController({ tolerance: 16 });
    // 7 px above the top of the sphere's silhouette.
    const top = exact.screen(0, 1.64, 0);
    const above = { clientX: top.x, clientY: top.y - 9 };
    assert.equal(exact.controller.pickAt(above), null, 'missed without a tolerance');
    const hit = near.controller.pickAt(above);
    assert.equal(hit?.zone, 'top', 'a touch with one');
    assert.ok(hit.near > 0, 'found by the rays around the pointer');
    assert.equal(near.controller.pickAt({ clientX: 20, clientY: 20 }), null, 'far from her: nothing, the window still moves');
});

test('two quick presses on her head are a double click (the Companion close-up)', () => {
    let doubles = 0;
    const { controller, screen, event, clock } = sphereController({ onDoubleTouch: () => doubles++ });
    const top = screen(0, 1.635, 0.02);
    controller._down(event(top)); controller._up(event(top));
    clock.now += 0.2;
    controller._down(event({ x: top.x + 3, y: top.y })); controller._up(event(top));
    assert.equal(doubles, 1);
    clock.now += 0.2;
    controller._down(event(top)); controller._up(event(top));
    assert.equal(doubles, 1, 'a third press starts again');
    clock.now += 1;
    controller._down(event(top)); controller._up(event(top));
    assert.equal(doubles, 1, 'slow presses are not a double click');
});
