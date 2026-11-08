// Avatar breeze (2026-10-13, the user's idea after qa11): a soft wind over the VRM's hair and
// clothing spring bones, so the avatar does not look frozen. Codex's review (avatar-breeze-
// oct13-review) found a stiffness-only push could swing a joint 79° on the native solver and
// that body physics also got wind; these tests run the real three-vrm solver for both.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import * as THREE from '../../companion/node_modules/three/build/three.module.js';
import {
    VRMSpringBoneJoint, VRMSpringBoneManager,
} from '../../companion/node_modules/@pixiv/three-vrm-springbone/lib/three-vrm-springbone.module.js';
import {
    AvatarBreeze, BREEZE_KEY, BREEZE_LEAN, breezeEnabled, gust, readBreezePreference, setBreezeEnabled, windAt,
} from '../../face/js/avatar_breeze.js';

const DEG = 180 / Math.PI;
const vec = (x, y, z) => ({ x, y, z, set(a, b, c) { this.x = a; this.y = b; this.z = c; } });
const stub = (name, stiffness, power = 0.1, parent = null) => ({
    bone: { name, parent }, settings: { gravityDir: vec(0, -1, 0), gravityPower: power, stiffness, dragForce: 0.4 },
});

/** A real spring chain on the native solver, hanging down (or standing up) from the root. */
function nativeChain({ stiffness = 1, gravity = 0, count = 1, up = false, drag = 0.4, name = 'hair' } = {}) {
    const scene = new THREE.Group();
    const bones = Array.from({ length: count + 1 }, (_, i) => Object.assign(new THREE.Bone(), { name: i ? `${name}_${i}` : name }));
    scene.add(bones[0]);
    for (let i = 1; i <= count; i++) {
        bones[i].position.set(0, up ? 0.1 : -0.1, 0);
        bones[i - 1].add(bones[i]);
    }
    scene.updateMatrixWorld(true);
    const manager = new VRMSpringBoneManager();
    for (let i = 0; i < count; i++) {
        manager.addJoint(new VRMSpringBoneJoint(bones[i], bones[i + 1], {
            stiffness, gravityPower: gravity, gravityDir: new THREE.Vector3(0, -1, 0), dragForce: drag,
        }));
    }
    manager.setInitState();
    return { bones: bones.slice(0, -1), manager, vrm: { springBoneManager: manager } };
}

function swing(options, { enabled = true, seconds = 40 } = {}) {
    const chain = nativeChain(options);
    const breeze = new AvatarBreeze({ isEnabled: () => enabled, reducedMotion: () => false });
    const attached = breeze.attach(chain.vrm);
    const rest = new THREE.Quaternion();
    const max = chain.bones.map(() => 0);
    for (let frame = 0; frame < seconds * 60; frame++) {
        breeze.apply(1 / 60);
        chain.manager.update(1 / 60);
        chain.bones.forEach((bone, i) => { max[i] = Math.max(max[i], bone.quaternion.angleTo(rest) * DEG); });
    }
    return { attached, max };
}

test('the wind turns slowly and gusts between a fifth and full strength', () => {
    let low = Infinity, high = -Infinity;
    for (let t = 0; t < 600; t += 0.05) {
        const g = gust(t);
        low = Math.min(low, g); high = Math.max(high, g);
        const w = windAt(t);
        assert.ok(Math.abs(Math.hypot(w.x, w.y, w.z) - 1) < 1e-9);
        assert.ok(w.x > 0.7, 'mostly from the side, so it reads from the front');
    }
    assert.ok(low >= 0.2 && low < 0.25 && high <= 1 && high > 0.95);
    assert.ok(Math.atan(BREEZE_LEAN * 1.14) * DEG < 5, 'soft: a few degrees per joint at most');
});

test('on the native solver a single joint never leans more than ~4°, whatever its settings', () => {
    // Codex B01: stiffness 1 against an opposing gravity of 0.99 used to reach 79°.
    const cases = [
        { up: true, stiffness: 1, gravity: 0.99 }, { stiffness: 0.001 }, { stiffness: 0.1 }, { stiffness: 1 },
        { stiffness: 100 }, { stiffness: 1, gravity: 0.7 }, { stiffness: 1, gravity: 3 }, { stiffness: 0, gravity: 0.15 },
        { up: true, stiffness: 3, gravity: 0.1 },
    ];
    for (const options of cases) {
        const { attached, max } = swing(options);
        assert.equal(attached, 1);
        assert.ok(max[0] < 5, `${JSON.stringify(options)} leaned ${max[0].toFixed(2)}°`);
    }
    assert.ok(swing({ up: true, stiffness: 1, gravity: 0.99 }).max[0] < 3);
    assert.deepEqual(swing({ stiffness: 1 }, { enabled: false }).max, [0]);
});

test('long chains fade in and stay soft: a few degrees per joint, the tip a little more', () => {
    const hair = swing({ count: 8 }).max;
    assert.ok(hair.every((angle) => angle < 7), hair.map((a) => a.toFixed(1)).join(' '));
    const stiffHair = swing({ count: 8, stiffness: 3, gravity: 0.12, drag: 0.8 }).max;
    assert.ok(stiffHair.every((angle) => angle < 5));
    // An unusually bouncy chain (drag 0.1) whipped to 131° at its tip; now it stays under 6°.
    const bouncy = swing({ count: 8, drag: 0.1 }).max;
    assert.ok(bouncy.every((angle) => angle < 6), bouncy.map((a) => a.toFixed(1)).join(' '));
});

test('only hair and clothing get the breeze: body physics and humanoid bones are left alone', () => {
    const body = swing({ name: 'breast_phys', count: 2 });
    assert.equal(body.attached, 0);
    assert.deepEqual(body.max, [0, 0]);
    // A body chain's child keeps out too, though only its root is named like a body part.
    const root = stub('Bone_Chest001', 3);
    const child = stub('tip_001', 3, 0.1, root.bone);
    const hair = stub('Fhair_A_2', 3);
    const head = stub('J_Bip_C_Head', 3);
    const vrm = {
        springBoneManager: { joints: new Set([root, child, hair, head]) },
        humanoid: { humanBones: { head: { node: head.bone } } },
    };
    const breeze = new AvatarBreeze({ isEnabled: () => true, reducedMotion: () => false });
    assert.equal(breeze.attach(vrm), 1);
});

test('turning it off fades out and restores the authored gravity exactly', () => {
    const hair = stub('hair_a', 1, 0.1);
    const loose = stub('hair_b', 0.5, 0);
    const state = { enabled: true, reduced: false };
    const breeze = new AvatarBreeze({ isEnabled: () => state.enabled, reducedMotion: () => state.reduced });
    breeze.attach({ springBoneManager: { joints: new Set([hair, loose]) } });
    const authored = [hair, loose].map((j) => ({ ...j.settings.gravityDir, power: j.settings.gravityPower }));
    const exact = () => [hair, loose].every((j, i) => j.settings.gravityPower === authored[i].power
        && j.settings.gravityDir.x === authored[i].x && j.settings.gravityDir.y === authored[i].y
        && j.settings.gravityDir.z === authored[i].z);
    for (let i = 0; i < 240; i++) breeze.apply(1 / 60);
    assert.equal(exact(), false);
    state.enabled = false;
    breeze.apply(1 / 60);
    assert.equal(exact(), false, 'the wind dies down instead of stopping dead');
    for (let i = 0; i < 90; i++) breeze.apply(1 / 60);
    assert.equal(exact(), true);
    state.enabled = true; state.reduced = true;
    for (let i = 0; i < 90; i++) breeze.apply(1 / 60);
    assert.equal(exact(), true, 'reduced motion keeps it still');
    state.reduced = false;
    for (let i = 0; i < 60; i++) breeze.apply(1 / 60);
    breeze.detach();
    assert.equal(exact(), true, 'detaching (a model change) restores at once');
    assert.equal(breeze.active, false);
});

test('one saved switch for every avatar, on unless turned off', () => {
    const saved = new Map();
    const storage = () => ({ getItem: (key) => saved.get(key) ?? null, setItem: (key, value) => saved.set(key, value) });
    assert.equal(readBreezePreference(storage), true);
    assert.equal(setBreezeEnabled(false, storage), false);
    assert.equal(saved.get(BREEZE_KEY), 'off');
    assert.equal(breezeEnabled(), false);
    assert.equal(readBreezePreference(storage), false);
    assert.equal(setBreezeEnabled(true, storage), true);
    assert.equal(readBreezePreference(() => { throw new Error('blocked'); }), true);
});

test('the VRM applies it before simulating hair; the switch shows when Windows pauses it', () => {
    const avatar = readFileSync(new URL('../../face/js/avatar.js', import.meta.url), 'utf8');
    assert.match(avatar, /this\.breeze\?\.apply\(delta\);\s*this\.vrm\?\.update\(delta\);/);
    assert.match(avatar, /autoRegisterSpringBones\(vrm\)[^]*?this\.breeze\.attach\(vrm\);/);
    assert.match(avatar, /this\.breeze\?\.detach\(\);/);
    const panel = readFileSync(new URL('../../face/js/panel.js', import.meta.url), 'utf8');
    assert.match(panel, /id="btn-avatar-breeze"/);
    assert.match(panel, /setBreezeEnabled\(!breezeEnabled\(\)\)/);
    // Codex B03: with «reduce motion» the button says PAUSA and a status note explains why.
    assert.match(panel, /paused \? 'PAUSA'/);
    assert.match(panel, /En pausa: Windows pide reducir el movimiento\./);
    assert.match(panel, /onReducedMotionChange\(renderBreeze\)/);
    // The avatar and the panel share one module instance (same URL), so one switch drives both.
    const version = (source) => source.match(/avatar_breeze\.js\?v=(\d+)/)?.[1];
    assert.equal(version(avatar), version(panel));
});
