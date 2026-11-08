// Her jump: a line of light over her body, whatever her position, and nothing left behind.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const threeUrl = new URL('../../companion/node_modules/three/build/three.module.js', import.meta.url).href;
const THREE = await import(threeUrl);
const text = await readFile(new URL('../../face/js/teleport_effect.js', import.meta.url), 'utf8');
const { TELEPORT, TeleportEffect, bodySpan, teleportStage } = await import(
    `data:text/javascript;base64,${Buffer.from(text.replace("from 'three'", `from '${threeUrl}'`)).toString('base64')}`);

function body(bones = { head: [0, 1.6, 0], hips: [0, 0.9, 0], leftFoot: [-0.1, 0, 0], rightFoot: [0.1, 0, 0] }) {
    const root = new THREE.Group();
    const nodes = {};
    for (const [name, position] of Object.entries(bones)) {
        nodes[name] = new THREE.Object3D();
        nodes[name].position.set(...position);
        root.add(nodes[name]);
    }
    const skin = new THREE.Mesh(new THREE.BufferGeometry(), new THREE.MeshBasicMaterial());
    const layers = new THREE.Mesh(new THREE.BufferGeometry(), [new THREE.MeshBasicMaterial(), new THREE.ShaderMaterial()]);
    const book = new THREE.Mesh(new THREE.BufferGeometry(), new THREE.MeshBasicMaterial());
    book.userData.conjured = true;
    root.add(skin, layers, book);
    root.updateMatrixWorld(true);
    return {
        root,
        book,
        mine: [skin.material, ...layers.material],
        vrm: { humanoid: { getRawBoneNode: (name) => nodes[name] ?? null } },
        at: (name) => nodes[name].getWorldPosition(new THREE.Vector3()),
    };
}

function jump(person = body()) {
    const effect = new TeleportEffect({ scene: new THREE.Scene(), floorY: -1 });
    const seen = { land: 0, settle: 0 };
    const started = effect.start({
        vrm: person.vrm,
        root: person.root,
        land: () => {
            seen.land += 1;
            seen.landedHidden = !person.root.visible;
            person.root.position.set(5, 0, 5);
            person.root.updateMatrixWorld(true);
        },
        settle: () => { seen.settle += 1; },
    });
    assert.equal(started, true);
    return { effect, person, seen, run: (seconds) => { for (let t = 0; t < seconds - 1e-9; t += 1 / 60) effect.update(1 / 60); } };
}

test('she is gone halfway through, and the whole jump takes about a second', () => {
    const { leave, gone, arrive } = TELEPORT;
    assert.ok(Math.abs(leave + gone + arrive - 1.2) < 0.01);
    assert.equal(teleportStage(0).stage, 'leave');
    assert.equal(teleportStage(0).wiped, 0);
    assert.equal(teleportStage(leave + 0.01).stage, 'gone');
    assert.equal(teleportStage(leave + gone + 0.01).stage, 'arrive');
    assert.equal(teleportStage(leave + gone + arrive).stage, 'done');
});

test('the line runs along her body: down when she stands, along the bed when she lies', () => {
    const standing = bodySpan(body().vrm);
    assert.ok(standing.along.distanceTo(new THREE.Vector3(0, -1, 0)) < 1e-6);
    assert.ok(standing.from.y > 1.6 && standing.to.y < 0, 'past her hair and her feet');
    const lying = bodySpan(body({ head: [0, 0.6, -1], hips: [0, 0.6, 0], leftFoot: [-0.1, 0.6, 1], rightFoot: [0.1, 0.6, 1] }).vrm);
    assert.ok(lying.along.distanceTo(new THREE.Vector3(0, 0, 1)) < 1e-6);
    assert.equal(bodySpan(body({ head: [0, 1, 0] }).vrm), null, 'no skeleton, no jump');
});

test('leaving, the line takes her from the head down, and not what she holds', () => {
    const { effect, person, run } = jump();
    for (const material of person.mine) assert.deepEqual(material.clippingPlanes, effect.planes);
    assert.equal(person.book.material.clippingPlanes, null, 'it goes in its own flash');
    assert.equal(person.mine[2].clipping, true, 'a shader only cuts if it is told to');
    run(TELEPORT.leave * 0.6);
    assert.ok(effect.plane.distanceToPoint(person.at('head')) < 0, 'her head has gone');
    assert.ok(effect.plane.distanceToPoint(person.at('leftFoot')) > 0, 'her feet have not');
});

test('she lands once, out of sight, and appears the same way', () => {
    const { effect, person, seen, run } = jump();
    run(TELEPORT.leave + 0.05);
    assert.equal(seen.land, 1);
    assert.equal(seen.landedHidden, true);
    assert.equal(person.root.visible, false);
    assert.equal(person.mine[0].clippingPlanes, null, 'a landing may replace what she wears');
    run(TELEPORT.gone + TELEPORT.arrive * 0.5 - 0.05);
    assert.equal(person.root.visible, true);
    assert.equal(seen.settle, 1);
    assert.ok(effect.plane.distanceToPoint(person.at('head')) > 0, 'her head is back');
    assert.ok(effect.plane.distanceToPoint(person.at('leftFoot')) < 0, 'her feet are not yet');
    assert.ok(effect.line.position.x > 4, 'at her new place');
    run(TELEPORT.arrive);
    assert.equal(effect.active, false);
    assert.equal(seen.land, 1);
    for (const material of person.mine) assert.equal(material.clippingPlanes, null);
});

test('when it is all over nothing is left on the stage', () => {
    const { effect, run } = jump();
    run(4);
    assert.equal(effect.group.visible, false);
});

test('stopped before she lands she never moved, and she is whole', () => {
    const { effect, person, seen, run } = jump();
    run(0.2);
    assert.deepEqual(effect.cancel(), { landed: false });
    assert.equal(effect.active, false);
    assert.equal(seen.land, 0);
    assert.equal(person.root.visible, true);
    assert.equal(person.mine[0].clippingPlanes, null);
    run(1);
    assert.equal(seen.land, 0, 'nothing is left waiting');
});

test('stopped once out of sight she is all there, where she landed', () => {
    const { effect, person, seen, run } = jump();
    run(TELEPORT.leave + 0.05);
    assert.deepEqual(effect.cancel(), { landed: true });
    assert.equal(seen.land, 1);
    assert.equal(person.root.visible, true);
    assert.equal(person.root.position.x, 5);
});

test('every jump draws its line, not just the first', () => {
    const { effect, person, run } = jump();
    run(2);
    assert.equal(effect.start({ vrm: person.vrm, root: person.root, land() {} }), true);
    assert.equal(effect.line.visible, true);
});

test('only one jump at a time', () => {
    const { effect, person } = jump();
    assert.equal(effect.start({ vrm: person.vrm, root: person.root, land() {} }), false);
});

test('preparing draws with the wipe on, cuts nothing and leaves no trace', () => {
    const person = body();
    const effect = new TeleportEffect({ scene: new THREE.Scene() });
    let drawn = 0;
    effect.prepare(person.root, () => {
        drawn += 1;
        assert.deepEqual(person.mine[0].clippingPlanes, effect.planes);
        assert.ok(effect.plane.distanceToPoint(person.at('head')) > 0);
        assert.ok(effect.plane.distanceToPoint(person.at('leftFoot')) > 0);
        assert.equal(effect.group.visible, true, 'its own shaders too');
    });
    assert.equal(drawn, 1);
    assert.equal(person.mine[0].clippingPlanes, null);
    assert.equal(effect.group.visible, false);
    assert.equal(effect.active, false);
});
