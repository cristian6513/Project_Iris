// IRIS sits on the sofa: the clips in the manifest, the walk around the furniture, and a seat
// that lasts until she is sent somewhere else.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile, stat } from 'node:fs/promises';
import { ROOM_OBJECT_ACTIONS, getRoomActionCommand } from '../../face/js/room_actions.js';
import { ROOM_HOTSPOTS } from '../../face/js/room_hotspots.js';
import { ConversationCamera } from '../../face/js/room_framing.js';

const threeUrl = new URL('../../companion/node_modules/three/build/three.module.js', import.meta.url).href;
const gltfUrl = new URL('../../companion/node_modules/three/examples/jsm/loaders/GLTFLoader.js', import.meta.url).href;
const THREE = await import(threeUrl);
const faceUrl = name => new URL('../../face/js/' + name, import.meta.url);
const source = name => readFile(faceUrl(name), 'utf8');
const moduleUrl = text => 'data:text/javascript;base64,' + Buffer.from(text).toString('base64');
const lifecycleUrl = faceUrl('avatar_lifecycle.js').href;
const playerUrl = moduleUrl((await source('vrm_animation_loader.js'))
    .replace("from './avatar_lifecycle.js'", `from '${lifecycleUrl}'`)
    .replace("from 'three'", `from '${threeUrl}'`)
    .replace("from 'three/addons/loaders/GLTFLoader.js'", `from '${gltfUrl}'`));
const { VRMAnimationPlayer } = await import(playerUrl);
const { IdleSystem } = await import(moduleUrl((await source('idle_system.js'))
    .replace("from './avatar_lifecycle.js'", `from '${lifecycleUrl}'`)
    .replace("from 'three'", `from '${threeUrl}'`)
    .replace(/from '\.\/vrm_animation_loader\.js[^']*'/, `from '${playerUrl}'`)));
const handPropUrl = moduleUrl((await source('hand_prop.js')).replace("from 'three'", `from '${threeUrl}'`));
const { ConjuredProp, HandProp } = await import(handPropUrl);
const teleportUrl = moduleUrl((await source('teleport_effect.js')).replace("from 'three'", `from '${threeUrl}'`));
const { SceneActionManager } = await import(moduleUrl((await source('scene_actions.js'))
    .replace("from 'three'", `from '${threeUrl}'`)
    .replace(/from '\.\/teleport_effect\.js[^']*'/, `from '${teleportUrl}'`)
    .replace(/from '\.\/hand_prop\.js[^']*'/, `from '${handPropUrl}'`)
    .replace(/from '\.\/room_hotspots\.js[^']*'/, `from '${faceUrl('room_hotspots.js').href}'`)
    .replace(/from '\.\/room_actions\.js[^']*'/, `from '${faceUrl('room_actions.js').href}'`)));

// Measured on the real meshes (room.js, iris.vrm): the top and base of the coffee table, the
// right arm of the sofa, the front of its seat, and how far her boots reach from the avatar's root.
const TABLE = { minX: -4.3, maxX: -2.3, minZ: 2.28, maxZ: 3.04 };
const SOFA_FRONT_Z = 1.852;
const SOFA_ARM = { minX: -1.98, maxX: -1.35, maxZ: 1.81 };
const TOE_REACH = 0.307;
const HEEL_REACH = 0.053;
const LEG_HALF_WIDTH = 0.1;

const seat = ROOM_HOTSPOTS.sofaSeatSpot;
const sitAction = ROOM_OBJECT_ACTIONS.sofa.actions.find(a => a.id === 'sitOnSofa_idle');

function seatFixture() {
    const scene = new THREE.Group();
    const head = new THREE.Bone(); head.name = 'Normalized_Head'; scene.add(head);
    const raw = Object.fromEntries(Object.entries({ head: 1.6, hips: 0.9, leftFoot: 0, rightFoot: 0 }).map(([name, y]) => {
        const bone = new THREE.Object3D();
        bone.position.y = y;
        return [name, bone];
    }));
    const vrm = { scene, humanoid: { getNormalizedBoneNode: name => (name === 'head' ? head : null), getRawBoneNode: name => raw[name] ?? null } };
    const player = new VRMAnimationPlayer(vrm);
    const group = new THREE.Group();
    const avatarManager = { avatar: { vrm, group, restYaw: 0 } };
    const idle = new IdleSystem(avatarManager);
    idle._vrmaPlayer = player;
    idle._vrmaManifest = { clips: {}, pose_map: { sofa_seated: 'sofa_seated_idle', bed_lying: 'bed_lying_idle', bed_seated: 'bed_seated_idle', book_reading: 'book_read_idle', sofa_reading: 'sofa_read_idle', bed_reading: 'bed_read_idle', sofa_tablet: 'sofa_tablet_idle', bed_tablet: 'bed_tablet_idle', desk_seated: 'desk_seated_idle', desk_typing: 'desk_type_idle', stand_reading: 'stand_read_idle', stand_tablet: 'stand_tablet_idle', window_looking: 'window_look_idle' }, return_idle_pool: ['idle'],
        activity_clips: { listening: { clip: 'listen', loop: false }, speaking: { clip: 'talk', loop: true } } };
    idle._vrmaInitAttempted = true;
    idle._ensureBoneRefs();
    idle.nextAction = 100;
    for (const name of ['idle', 'wave', 'listen', 'talk', 'walk', 'sofa_sit_down', 'sofa_seated_idle', 'sofa_stand_up',
        'bed_lie_down', 'bed_lying_idle', 'bed_get_up', 'bed_sit_down', 'bed_seated_idle', 'bed_stand_up', 'bed_sit_up',
        'book_take', 'book_read_idle', 'book_return', 'sofa_read_open', 'sofa_read_idle', 'sofa_read_close',
        'bed_read_open', 'bed_read_idle', 'bed_read_close', 'sofa_tablet_open', 'sofa_tablet_idle', 'sofa_tablet_close',
        'bed_tablet_open', 'bed_tablet_idle', 'bed_tablet_close', 'desk_sit_down', 'desk_seated_idle', 'desk_stand_up',
        'desk_type_open', 'desk_type_idle', 'desk_type_close', 'stand_read_open', 'stand_read_idle', 'stand_read_close', 'stand_tablet_open', 'stand_tablet_idle', 'stand_tablet_close',
        'window_lean_in', 'window_look_idle', 'window_lean_out']) {
        const q = new THREE.Quaternion().toArray();
        const track = new THREE.QuaternionKeyframeTrack(`${head.name}.quaternion`, [0, 1], [...q, ...q]);
        const clip = new THREE.AnimationClip(name, 1, [track]);
        player.clips.set(name, clip);
        player.actions.set(name, player.mixer.clipAction(clip));
    }
    const scenes = new SceneActionManager(new THREE.Group(), avatarManager, null, {}, { idleSystem: idle });
    const advance = (seconds, step = 1 / 30) => {
        for (let t = 0; t < seconds - 1e-9; t += step) {
            idle.update(step, t);
            scenes.update(step, t);
        }
    };
    return { idle, player, scenes, group, advance };
}

test('the seat is a room-only pose with its three clips on disk and a face free to blink', async () => {
    const manifest = JSON.parse(await readFile(new URL('../../face/animations/manifest.json', import.meta.url), 'utf8'));
    assert.equal(manifest.pose_map.sofa_seated, 'sofa_seated_idle');
    for (const name of ['sofa_sit_down', 'sofa_seated_idle', 'sofa_stand_up', 'walk', 'bed_lie_down', 'bed_lying_idle', 'bed_get_up',
        'bed_sit_down', 'bed_seated_idle', 'bed_stand_up', 'bed_sit_up', 'book_take', 'book_read_idle', 'book_return',
        'sofa_read_open', 'sofa_read_idle', 'sofa_read_close', 'bed_read_open', 'bed_read_idle', 'bed_read_close',
        'sofa_tablet_open', 'sofa_tablet_idle', 'sofa_tablet_close', 'bed_tablet_open', 'bed_tablet_idle', 'bed_tablet_close',
        'desk_sit_down', 'desk_seated_idle', 'desk_stand_up', 'desk_type_open', 'desk_type_idle', 'desk_type_close',
        'stand_read_open', 'stand_read_idle', 'stand_read_close', 'stand_tablet_open', 'stand_tablet_idle', 'stand_tablet_close',
        'window_lean_in', 'window_look_idle', 'window_lean_out']) {
        const file = new URL('../../face/animations/' + manifest.clips[name], import.meta.url);
        assert.ok((await stat(file)).size > 1000, name);
        assert.ok(manifest.runtime_only.includes(name), `${name} must stay out of the agent's catalog`);
        assert.ok(manifest.body_only_clips.includes(name), `${name} must leave her blink alone`);
        const held = name === 'desk_type_idle' ? 'screen' : /(read|tablet)_idle$/.test(name) ? 'down' : 'natural';
        assert.equal(manifest.facial_profiles[name].eyes.gaze, held);
        assert.ok(!manifest.menu_poses.includes(name));
    }
});

test('sit_sofa walks to the seat and stays there', () => {
    assert.equal(getRoomActionCommand('sit_sofa').action.id, 'sitOnSofa_idle');
    assert.equal(sitAction.targetHotspot, 'sofaSeatSpot');
    assert.equal(sitAction.animationName, 'sofa_seated');
    assert.equal(sitAction.endMode, 'stay');
    assert.equal(seat.defaultPoseOrAnimation, 'sofa_seated');
});

test('standing at the seat, her boots fit between the sofa and the coffee table', () => {
    const [, , z] = seat.position;
    assert.ok(z + TOE_REACH < TABLE.minZ, `toes at ${z + TOE_REACH}`);
    assert.ok(z - HEEL_REACH > SOFA_FRONT_Z, `heels at ${z - HEEL_REACH}`);
});

test('the walk to the seat and back goes around the sofa arm and the table', () => {
    const { scenes } = seatFixture();
    const inTable = p => p.x > TABLE.minX && p.x < TABLE.maxX && p.z + LEG_HALF_WIDTH > TABLE.minZ && p.z < TABLE.maxZ;
    const inArm = p => p.x > SOFA_ARM.minX && p.x < SOFA_ARM.maxX && p.z - LEG_HALF_WIDTH < SOFA_ARM.maxZ;
    const inSeat = p => p.x < SOFA_ARM.minX && p.z - LEG_HALF_WIDTH < SOFA_FRONT_Z - 0.01;
    const check = (from, to, label) => {
        const route = scenes._planRoute(scenes._routeTarget, from, to, 0, 0, 0);
        assert.ok(route, label);
        for (let u = 0; u <= 1; u += 0.01) {
            const p = route.curve.getPointAt(u);
            assert.ok(!inTable(p) && !inArm(p) && !inSeat(p), `${label} crosses furniture at ${p.x.toFixed(2)}, ${p.z.toFixed(2)}`);
        }
    };
    const center = new THREE.Vector3(...ROOM_HOTSPOTS.centerSpot.position);
    const seatPoint = new THREE.Vector3(...seat.position);
    scenes._routeTarget = seat;
    check(center, seatPoint, 'in');
    scenes._routeTarget = ROOM_HOTSPOTS.centerSpot;
    scenes._atSpot = seat;
    check(seatPoint, center, 'out');
});

test('every walk takes as long as its distance, only a shuffle on the spot stays direct', () => {
    const { scenes } = seatFixture();
    const from = new THREE.Vector3();
    assert.equal(scenes._planRoute(ROOM_HOTSPOTS.pcSpot, from, new THREE.Vector3(0.2, 0, 0.1), 0, 0, 0), null);
    const near = scenes._planRoute(ROOM_HOTSPOTS.pcSpot, from, new THREE.Vector3(1, 0, 1), 0, 0, 0);
    const far = scenes._planRoute(ROOM_HOTSPOTS.pcSpot, from, new THREE.Vector3(2, 0, 2), 0, 0, 0);
    assert.ok(Math.abs(near.length - Math.SQRT2) < 1e-3, 'a trip with no way points goes straight');
    assert.ok(Math.abs((far.walk - near.walk) - 1000 * Math.SQRT2 / scenes.moveSpeed) < 1);
});

test('she walks with the clip at the speed she moves, and speech waits for her to arrive', () => {
    const { idle, player, scenes, group, advance } = seatFixture();
    idle.update(1 / 30, 0);
    scenes.execute('standNearBookshelf');
    const { turnIn, walk, turnOut, length } = scenes.hotspotTransition.route;
    advance((turnIn * 0.6 + 700) / 1000);
    assert.equal(player.currentName, 'walk');
    assert.equal(idle.isWalking(), true);
    const before = group.position.clone();
    advance(0.5);
    const moved = group.position.distanceTo(before) / 0.5;
    assert.ok(Math.abs(moved - scenes.moveSpeed) < 0.15, `walking at ${moved}`);
    assert.ok(Math.abs(player.current.getEffectiveTimeScale() - scenes.moveSpeed / 1.0945) < 0.05, 'feet match the floor');
    idle.setActivity('speaking');
    advance(0.2);
    assert.equal(player.currentName, 'walk', 'she keeps walking while she talks');
    assert.ok(length > 1);
    advance((walk + turnOut) / 1000);
    assert.equal(scenes.hotspotTransition, null);
    assert.equal(idle.isWalking(), false);
    assert.equal(player.currentName, 'talk', 'arriving hands over to the speech clip');
});

test('without the walk clip she falls back to the procedural gait', () => {
    const { idle, player, scenes, advance } = seatFixture();
    player.clips.delete('walk');
    player.actions.delete('walk');
    idle.update(1 / 30, 0);
    scenes.execute('standNearBookshelf');
    advance(1.5);
    assert.notEqual(player.currentName, 'walk');
    assert.ok(scenes.hotspotTransition, 'she still walks');
});

test('a timed action counts from her arrival, not from the order', () => {
    const { scenes, advance } = seatFixture();
    const timed = Object.values(ROOM_OBJECT_ACTIONS).flatMap(o => o.actions).find(a => a.duration && a.targetHotspot);
    assert.ok(timed);
    const started = [];
    scenes._startActionTimer = (action) => started.push(action?.id);
    scenes.execute(timed.id);
    assert.deepEqual(started, []);
    const { turnIn, walk, turnOut } = scenes.hotspotTransition?.route || { turnIn: 0, walk: 900, turnOut: 0 };
    advance((turnIn + walk + turnOut) / 1000 + 0.2);
    assert.deepEqual(started, [timed.id]);
});

test('her eyes stay drawn: bounds measured standing do not fit a seated pose', async () => {
    assert.match(await source('avatar.js'), /vrm\.scene\.traverse\(\(obj\) => \{ if \(obj\.isMesh\) obj\.frustumCulled = false; \}\);/);
});

test('IRIS walks in, sits, holds the seat through speech and gestures, stands up, and only then walks away', () => {
    const { idle, player, scenes, group, advance } = seatFixture();
    idle.update(1 / 30, 0);
    assert.equal(scenes.execute('sit_sofa'), true);
    const elapsed = () => scenes.hotspotTransition?.elapsed;
    advance(1);
    const walked = elapsed();
    assert.equal(scenes.execute('sit_sofa'), true, 'a second press keeps her walking');
    assert.equal(elapsed(), walked);

    const { turnIn, walk, turnOut } = scenes.hotspotTransition.route;
    advance((turnIn * 0.6 + walk + turnOut - elapsed()) / 1000 + 0.1);
    assert.equal(scenes.hotspotTransition, null);
    assert.ok(Math.abs(group.position.x - seat.position[0]) < 1e-6 && Math.abs(group.position.z - seat.position[2]) < 1e-6);
    assert.ok(Math.abs(group.rotation.y - seat.rotation[1]) < 1e-6, 'her back to the sofa');
    assert.equal(idle.isSeated('sofa_seated'), true);
    assert.equal(player.currentName, 'sofa_sit_down');
    advance(1.2);
    assert.equal(player.currentName, 'sofa_seated_idle', 'the clip hands over to the loop');

    // Nothing but a walk gets her up.
    assert.equal(idle.playClipExternal('wave'), true);
    assert.equal(idle.trigger('stretch'), true);
    idle.setActivity('listening');
    idle.setActivity('speaking');
    idle.setActivity('idle');
    scenes.reset();
    assert.equal(scenes.execute('sit_sofa'), true);
    advance(0.5);
    assert.equal(player.currentName, 'sofa_seated_idle');
    assert.equal(idle.currentPose, 'sofa_seated');

    scenes.execute('return_center');
    assert.equal(player.currentName, 'sofa_stand_up');
    assert.equal(idle.currentPose, null);
    advance(0.5);
    assert.ok(Math.abs(group.position.x - seat.position[0]) < 1e-6, 'she does not slide while getting up');
    advance(7.5);
    assert.equal(player.currentName, 'idle');
    assert.equal(scenes.hotspotTransition, null);
    assert.ok(Math.hypot(group.position.x, group.position.z) < 1e-6);
});

test('the end of a speech does not strand her on the way to a place she stays in', () => {
    const { scenes } = seatFixture();
    scenes.execute('sit_sofa');
    scenes.reset();
    assert.ok(scenes.hotspotTransition, 'still walking to the seat');
    scenes.reset({ clearLocomotion: true });
    assert.equal(scenes.hotspotTransition, null, 'leaving the Room does cancel it');
    assert.equal(scenes._atSpot, null);

    scenes.goToHotspot('windowSpot');
    scenes.reset();
    assert.equal(scenes.hotspotTransition, null, 'a walk with no action ends with the speech, as before');
});

test('without the clip, as in a package that leaves it out, she only walks to the spot', () => {
    const { idle, player, scenes, advance } = seatFixture();
    player.clips.delete('sofa_seated_idle');
    player.actions.delete('sofa_seated_idle');
    scenes.execute('sit_sofa');
    advance(6);
    assert.equal(idle.currentPose, null);
    assert.equal(idle.isSeated('sofa_seated'), false);
});

test('sitting turns the rest of the skirt\'s front panels over her thighs, and standing restores it', async () => {
    const springUrl = new URL('../../companion/node_modules/@pixiv/three-vrm-springbone/lib/three-vrm-springbone.module.js', import.meta.url).href;
    const { VRMSpringBoneJoint } = await import(moduleUrl((await readFile(new URL(springUrl), 'utf8')).replaceAll('from "three"', `from "${threeUrl}"`)));
    const { SeatDrape } = await import(moduleUrl((await source('seat_drape.js')).replace("from 'three'", `from '${threeUrl}'`)));
    const hips = new THREE.Bone(); hips.name = 'hips';
    const bone = name => { const b = new THREE.Bone(); b.name = name; return b; };
    const front = bone('skirt_frontL'), next = bone('skirt_front01L'), tip = bone('skirt_front01L_end');
    const back = bone('skirt_backL'), backTip = bone('skirt_back01L');
    hips.add(front); front.add(next); next.add(tip); hips.add(back); back.add(backTip);
    for (const child of [next, tip, backTip]) child.position.set(0, -0.1, 0);
    hips.updateMatrixWorld(true);
    const joints = new Set([[front, next], [next, tip], [back, backTip]].map(([b, c]) => {
        const joint = new VRMSpringBoneJoint(b, c, { stiffness: 2.5, dragForce: 0.8, hitRadius: 0.02 }, []);
        joint.setInitState();
        return joint;
    }));
    const [frontJoint, , backJoint] = joints;
    const rest = frontJoint._initialLocalRotation.clone();
    const backRest = backJoint._initialLocalRotation.clone();
    const drape = new SeatDrape({ springBoneManager: { joints }, humanoid: { getRawBoneNode: () => hips } });
    assert.equal(drape.panels.length, 2, 'the back panels and any other avatar keep their rest');

    drape.set(1);
    for (let i = 0; i < 240; i++) {
        for (const joint of joints) joint.update(1 / 30);
        hips.updateMatrixWorld(true);
    }
    const end = tip.getWorldPosition(new THREE.Vector3());
    assert.ok(end.z > 0.1, `the front panel reaches towards the knees (${end.z.toFixed(3)})`);
    assert.ok(backJoint._initialLocalRotation.equals(backRest));

    drape.set(0);
    assert.ok(frontJoint._initialLocalRotation.angleTo(rest) < 1e-6);
});

test('the camera joins her late on the way to the seat and leaves with the next order', () => {
    const { idle, scenes, advance } = seatFixture();
    idle.update(1 / 30, 0);
    assert.equal(scenes.framedSpot(), null);
    scenes.execute('sit_sofa');
    const { turnIn, walk, turnOut } = scenes.hotspotTransition.route;
    advance((turnIn * 0.6 + walk * 0.7) / 1000);
    assert.equal(scenes.framedSpot(), null, 'not while she is on her feet and far from the seat');
    advance(walk * 0.25 / 1000);
    assert.equal(scenes.framedSpot(), seat, 'it joins her for the last stretch');
    advance((walk * 0.05 + turnOut) / 1000 + 1.5);
    assert.equal(scenes.framedSpot(), seat);
    scenes.reset();
    assert.equal(scenes.framedSpot(), seat, 'the end of a speech leaves her sitting');

    scenes.execute('return_center');
    assert.equal(scenes.framedSpot()?.view, undefined, 'it is home before she reaches the view');
});

test('sent elsewhere halfway out, she does not go back to the way she left by', () => {
    const { idle, scenes, group, advance } = seatFixture();
    idle.update(1 / 30, 0);
    group.position.set(seat.position[0], group.position.y, seat.position[2]);
    scenes._atSpot = seat;
    scenes.execute('return_center');
    advance(1);
    scenes.execute('standNearBookshelf');
    assert.equal(scenes._atSpot, null);
});

function cameraFixture(aspect = 16 / 9) {
    const camera = new THREE.PerspectiveCamera(45, aspect, 0.1, 1000);
    return { camera, view: new ConversationCamera(camera) };
}

test('with nowhere to go the conversation camera keeps its slow drift around home', () => {
    const { camera, view } = cameraFixture();
    view.update(1 / 30, 0, undefined);
    assert.deepEqual(camera.position.toArray(), [0.34, 1.38, 4.95]);
    view.update(1 / 30, 10, undefined);
    assert.ok(Math.abs(camera.position.x - (0.34 + Math.sin(0.6) * 0.08)) < 1e-9);
    assert.ok(Math.abs(camera.position.y - (1.38 + Math.sin(0.4) * 0.025)) < 1e-9);
    assert.equal(camera.position.z, 4.95);
});

test('the camera eases to a spot, frames her there, and eases home the same way', () => {
    const { camera, view } = cameraFixture();
    const step = (seconds, spot) => { for (let t = 0; t < seconds; t += 1 / 30) view.update(1 / 30, 0, spot); };
    step(1.5, seat.view);
    assert.ok(view.blend > 0.4 && view.blend < 0.6, 'no jump');
    const between = camera.position.x;
    assert.ok(between < 0.34 && between > seat.view.position[0]);
    step(1.6, seat.view);
    assert.equal(view.blend, 1);
    assert.ok(camera.position.distanceTo(new THREE.Vector3(...seat.view.position)) < 1e-9);

    // Her head, hips and boots at the seat all land inside the frame.
    camera.updateMatrixWorld(true);
    for (const point of [[-2.65, 0.45, 1.05], [-2.63, -0.49, 1.36], [-2.6, -1.15, 2.2]]) {
        const ndc = new THREE.Vector3(...point).project(camera);
        assert.ok(Math.abs(ndc.x) < 0.9 && Math.abs(ndc.y) < 0.96, `${point} at ${ndc.x.toFixed(2)}, ${ndc.y.toFixed(2)}`);
    }

    step(3.1, undefined);
    assert.equal(view.blend, 0);
    assert.deepEqual(camera.position.toArray(), [0.34, 1.38, 4.95]);
});

test('a narrow window backs the seat view off, and leaving Explorar resets it at once', () => {
    const { camera, view } = cameraFixture(0.9);
    for (let t = 0; t < 3.1; t += 1 / 30) view.update(1 / 30, 0, seat.view);
    assert.ok(camera.position.z > seat.view.position[2] + 1, 'further back than in 16:9');
    view.reset();
    view.update(1 / 30, 0, undefined);
    assert.deepEqual(camera.position.toArray(), [0.34, 1.38, 4.95]);
});

const bed = ROOM_HOTSPOTS.bedLieSpot;
const BED_EDGE_X = 2.64;

test('lie_bed walks to the bedside and stays lying down', () => {
    const action = getRoomActionCommand('lie_bed').action;
    assert.equal(action.id, 'lieOnBed');
    assert.equal(action.enabled, true);
    assert.equal(action.canInterrupt, true);
    assert.equal(action.targetHotspot, 'bedLieSpot');
    assert.equal(action.animationName, 'bed_lying');
    assert.equal(action.endMode, 'stay');
    assert.equal(bed.defaultPoseOrAnimation, 'bed_lying');
});

test('standing at the bedside she has her back to the bed and her heels clear of it', () => {
    const [x] = bed.position;
    assert.ok(x + HEEL_REACH < BED_EDGE_X, `heels at ${x + HEEL_REACH}`);
    assert.ok(Math.abs(bed.rotation[1] + Math.PI / 2) < 1e-9, 'facing the room');
});

test('IRIS lies down, stays through speech and gestures, gets up, and only then walks away', () => {
    const { idle, player, scenes, group, advance } = seatFixture();
    idle.update(1 / 30, 0);
    scenes.execute('lie_bed');
    const { turnIn, walk, turnOut } = scenes.hotspotTransition.route;
    advance((turnIn * 0.6 + walk + turnOut) / 1000 + 0.2);
    assert.equal(scenes.hotspotTransition, null);
    assert.ok(Math.abs(group.position.x - bed.position[0]) < 1e-6 && Math.abs(group.position.z - bed.position[2]) < 1e-6);
    assert.ok(Math.abs(group.rotation.y + Math.PI / 2) < 1e-6);
    assert.equal(idle.isSeated('bed_lying'), true);
    assert.equal(player.currentName, 'bed_lie_down');
    advance(1.2);
    assert.equal(player.currentName, 'bed_lying_idle');

    assert.equal(idle.playClipExternal('wave'), true);
    idle.setActivity('listening');
    idle.setActivity('speaking');
    idle.setActivity('idle');
    scenes.reset();
    advance(0.5);
    assert.equal(player.currentName, 'bed_lying_idle');

    scenes.execute('return_center');
    assert.equal(player.currentName, 'bed_get_up');
    advance(0.5);
    assert.ok(Math.abs(group.position.x - bed.position[0]) < 1e-6, 'she does not slide while getting up');
    advance(6);
    assert.equal(player.currentName, 'idle');
    assert.ok(Math.hypot(group.position.x, group.position.z) < 1e-6);
});

test('the skirt rests on her thighs only while they are raised', () => {
    const { idle, player, advance } = seatFixture();
    idle.update(1 / 30, 0);
    assert.equal(idle._seatDrapeWeight(), 0);
    const clip = (name, time) => { player.play(name, { loop: false, fadeIn: 0 }); player.current.time = time; };
    clip('bed_lie_down', 0.3);
    assert.equal(idle._seatDrapeWeight(), 0, 'on her feet');
    clip('bed_lie_down', 2.2);
    assert.equal(idle._seatDrapeWeight(), 1, 'sitting on the edge');
    clip('bed_lie_down', 4.6);
    assert.equal(idle._seatDrapeWeight(), 0, 'legs straight on the bed');
    clip('sofa_sit_down', 2);
    assert.equal(idle._seatDrapeWeight(), 1);
    idle.currentPose = 'sofa_seated';
    clip('sofa_seated_idle', 1);
    assert.equal(idle._seatDrapeWeight(), 1, 'held on the sofa');
    idle.currentPose = 'bed_lying';
    clip('bed_lying_idle', 1);
    assert.equal(idle._seatDrapeWeight(), 0, 'held lying down');
    advance(0);
});

test('the camera waits for her to be nearly up before it leaves', () => {
    const { idle, player, scenes } = seatFixture();
    idle.update(1 / 30, 0);
    scenes._atSpot = bed;
    idle.currentPose = 'bed_lying';
    player.clips.get('bed_get_up').duration = 4;
    scenes.execute('return_center');
    assert.equal(scenes.framedSpot(), bed, 'she has just started');
    player.current.time = 3;
    assert.notEqual(scenes.framedSpot(), bed, 'nearly on her feet');
});

test('she does not walk away from a spot to come in by its way point', () => {
    const { scenes } = seatFixture();
    const to = new THREE.Vector3(...bed.position);
    const close = new THREE.Vector3(2.15, 0, 1.6);
    const near = scenes._planRoute(bed, close, to, 0, 0, 0);
    assert.ok(Math.abs(near.length - close.distanceTo(to)) < 0.01, 'straight there from beside the bed');
    const far = scenes._planRoute(bed, new THREE.Vector3(), to, 0, 0, 0);
    assert.ok(far.length > new THREE.Vector3().distanceTo(to));
});

const edge = ROOM_HOTSPOTS.bedSeatSpot;

test('sit_bed sits her on the edge and she stays there, at the same place she lies down from', () => {
    const action = getRoomActionCommand('sit_bed').action;
    assert.equal(action.id, 'sitOnBed_idle');
    assert.equal(action.targetHotspot, 'bedSeatSpot');
    assert.equal(action.animationName, 'bed_seated');
    assert.equal(action.endMode, 'stay');
    assert.deepEqual(edge.position, bed.position);
    assert.deepEqual(edge.rotation, bed.rotation);
    assert.deepEqual(edge.view, bed.view, 'one follows the other without moving the camera');
});

test('from the edge of the bed she lies back without getting up, and the skirt goes with it', () => {
    const { idle, player, scenes, advance } = seatFixture();
    idle.update(1 / 30, 0);
    scenes.execute('sit_bed');
    const { turnIn, walk, turnOut } = scenes.hotspotTransition.route;
    advance((turnIn * 0.6 + walk + turnOut) / 1000 + 0.2);
    assert.equal(player.currentName, 'bed_sit_down');
    advance(1.2);
    assert.equal(player.currentName, 'bed_seated_idle');
    assert.equal(idle._seatDrapeWeight(), 1);

    player.clips.get('bed_lie_down').duration = 5.1;
    scenes.execute('lie_bed');
    assert.equal(scenes.hotspotTransition, null, 'no walk');
    assert.equal(player.currentName, 'bed_lie_down');
    assert.ok(Math.abs(player.current.time - 2.3) < 0.05, 'it picks up where she already is');
    assert.equal(idle.isSeated('bed_lying'), true);
    assert.equal(scenes.framedSpot(), bed);
    assert.equal(idle._seatDrapeWeight(), 1);
});

test('lying down, she sits up onto the edge without getting up or going anywhere', () => {
    const { idle, player, scenes, group, advance } = seatFixture();
    idle.update(1 / 30, 0);
    scenes.execute('lie_bed');
    const { turnIn, walk, turnOut } = scenes.hotspotTransition.route;
    advance((turnIn * 0.6 + walk + turnOut) / 1000 + 1.4);
    assert.equal(player.currentName, 'bed_lying_idle');
    scenes.execute('sit_bed');
    assert.equal(player.currentName, 'bed_sit_up');
    assert.equal(scenes.hotspotTransition, null, 'no walk');
    assert.equal(scenes.framedSpot(), edge);
    assert.equal(idle._seatDrapeWeight(), 0, 'legs still straight');
    advance(1.2);
    assert.equal(player.currentName, 'bed_seated_idle');
    assert.ok(Math.abs(group.position.x - edge.position[0]) < 1e-6 && Math.abs(group.position.z - edge.position[2]) < 1e-6);
    assert.equal(idle.isSeated('bed_seated'), true);
    assert.equal(idle._seatDrapeWeight(), 1);
});

test('a trip with no distance turns her without steps on the spot', () => {
    const { idle, player, scenes, group, advance } = seatFixture();
    idle.update(1 / 30, 0);
    group.position.set(bed.position[0], group.position.y, bed.position[2]);
    scenes.goToHotspot('bedLieSpot', { roomAction: null });
    assert.equal(scenes.hotspotTransition.route, null);
    advance(0.4);
    assert.notEqual(player.currentName, 'walk');
});

function bookFixture() {
    const shelf = new THREE.Group(), hand = new THREE.Group(), left = new THREE.Group();
    left.name = 'cover';
    const object = new THREE.Group();
    object.add(left);
    object.position.set(1, 2, 3);
    object.scale.setScalar(1.5);
    shelf.add(object);
    hand.position.set(5, 0, 0);
    hand.scale.setScalar(1.4594);
    new THREE.Group().add(shelf, hand);
    const grip = { position: new THREE.Vector3(-0.2, 0, 0), quaternion: new THREE.Quaternion(1, 0, 0, 0) };
    return { shelf, hand, left, object, prop: new HandProp(object, { grip, hinge: 'cover', openAngle: 2 }), grip };
}

test('read_book walks her beside the shelf, facing the room', () => {
    const action = getRoomActionCommand('read_book').action;
    assert.equal(action.id, 'readBook');
    assert.equal(action.enabled, true);
    assert.equal(action.targetHotspot, 'bookReadSpot');
    assert.equal(action.animationName, 'book_reading');
    assert.equal(action.endMode, 'stay');
    const spot = ROOM_HOTSPOTS.bookReadSpot;
    assert.ok(spot.position[0] > -4.57, 'in front of the shelf');
    assert.ok(Math.abs(spot.rotation[1]) < 0.5, 'her face to the room');
});

test('she takes the book at the moment of the clip, reads it open, and lets it go when she puts it back', () => {
    const { idle, player, advance } = seatFixture();
    idle.update(1 / 30, 0);
    assert.equal(idle.heldProp(), null, 'hands free');
    const clip = (name, time) => { player.play(name, { loop: false, fadeIn: 0 }); player.current.time = time; };
    idle.currentPose = 'book_reading';
    clip('book_take', 1);
    assert.equal(idle.heldProp().held, false, 'still reaching');
    clip('book_take', 1.3);
    assert.deepEqual(idle.heldProp(), { id: 'shelf', held: true, open: 0 });
    clip('book_take', 3.3);
    assert.equal(idle.heldProp().open, 1);
    clip('book_read_idle', 2);
    assert.deepEqual(idle.heldProp(), { id: 'shelf', held: true, open: 1 });
    idle.currentPose = null;
    clip('book_return', 0.05);
    assert.deepEqual(idle.heldProp(), { id: 'shelf', held: true, open: 1 });
    clip('book_return', 1);
    assert.deepEqual(idle.heldProp(), { id: 'shelf', held: true, open: 0 });
    clip('book_return', 2);
    assert.equal(idle.heldProp().held, false, 'back on the shelf');
    clip('idle', 0);
    assert.equal(idle.heldProp(), null);
    advance(0);
});

test('reading holds through speech and gestures, and the walk away waits for the book to go back', () => {
    const { idle, player, scenes, advance } = seatFixture();
    idle.update(1 / 30, 0);
    scenes.execute('read_book');
    const { turnIn, walk, turnOut } = scenes.hotspotTransition.route;
    advance((turnIn * 0.6 + walk + turnOut) / 1000 + 1.2);
    assert.equal(player.currentName, 'book_read_idle');
    assert.equal(idle.playClipExternal('wave'), true);
    idle.setActivity('speaking');
    idle.setActivity('idle');
    scenes.reset();
    assert.equal(player.currentName, 'book_read_idle');
    scenes.execute('return_center');
    assert.equal(player.currentName, 'book_return');
    assert.equal(scenes.hotspotTransition.elapsed, 0, 'she stays until it is back');
});

test('the prop keeps its place in the world as it changes hands, then settles into the grip', () => {
    const { shelf, hand, left, object, prop, grip } = bookFixture();
    const world = () => object.getWorldPosition(new THREE.Vector3());
    shelf.parent.updateMatrixWorld(true);
    const start = world();
    prop.update(0.016, hand, { held: true, open: 0 });
    assert.equal(object.parent, hand);
    assert.ok(world().distanceTo(start) < 0.01, 'no jump at the moment she takes it');
    for (let i = 0; i < 60; i++) prop.update(0.05, hand, { held: true, open: 0.5 });
    assert.ok(object.position.distanceTo(grip.position) < 1e-6);
    assert.ok(Math.abs(object.getWorldScale(new THREE.Vector3()).x - 1.5) < 1e-6, 'the size it has in the room');
    assert.equal(left.rotation.z, 1, 'half open, by the clip');
    for (let i = 0; i < 60; i++) prop.update(0.05, hand, null);
    assert.equal(object.parent, shelf);
    assert.deepEqual(object.position.toArray(), [1, 2, 3]);
    assert.equal(left.rotation.z, 0, 'closed on the shelf');
});

test('with no avatar to hold it the prop stays put, and leaving an avatar returns it at once', () => {
    const { shelf, hand, object, prop } = bookFixture();
    prop.update(0.016, null, { held: true, open: 1 });
    assert.equal(object.parent, shelf);
    prop.update(0.016, hand, { held: true, open: 1 });
    assert.equal(object.parent, hand);
    prop.drop();
    assert.equal(object.parent, shelf);
    assert.deepEqual(object.position.toArray(), [1, 2, 3]);
    assert.equal(prop.held, false);
});

const arrive = (scenes, advance) => {
    const { turnIn, walk, turnOut } = scenes.hotspotTransition.route;
    advance((turnIn * 0.6 + walk + turnOut) / 1000 + 0.2);
};

test('read_sofa and read_bed walk her to the seat, sit her down, and open the book from there', () => {
    for (const [order, id, seat, sit, open, loop] of [
        ['read_sofa', 'readOnSofa', 'sofa_reading', 'sofa_sit_down', 'sofa_read_open', 'sofa_read_idle'],
        ['read_bed', 'readOnBed', 'bed_reading', 'bed_sit_down', 'bed_read_open', 'bed_read_idle'],
    ]) {
        assert.equal(getRoomActionCommand(order).action.id, id);
        const { idle, player, scenes, advance } = seatFixture();
        idle.update(1 / 30, 0);
        scenes.execute(order);
        arrive(scenes, advance);
        assert.equal(player.currentName, sit);
        assert.equal(idle.isSeated(seat), true);
        assert.equal(idle.heldProp(), null, 'no book while she sits down');
        advance(1);
        assert.equal(player.currentName, open);
        advance(1.1);
        assert.equal(player.currentName, loop);
        assert.deepEqual(idle.heldProp(), { id: 'conjured', held: true, open: 1 });
    }
});

test('seated, she opens the book where she is, also with the bookshelf button, and puts it away', () => {
    const { idle, player, scenes, advance } = seatFixture();
    idle.update(1 / 30, 0);
    scenes.execute('sit_sofa');
    arrive(scenes, advance);
    advance(1.2);
    assert.equal(player.currentName, 'sofa_seated_idle');

    scenes.execute('read_book');
    assert.equal(scenes.hotspotTransition, null, 'she stays on the sofa');
    assert.equal(player.currentName, 'sofa_read_open');
    assert.equal(scenes.framedSpot(), seat);
    assert.equal(idle.isSeated('sofa_reading'), true);
    advance(1.1);
    assert.equal(player.currentName, 'sofa_read_idle');

    scenes.execute('sit_sofa');
    assert.equal(scenes.hotspotTransition, null);
    assert.equal(player.currentName, 'sofa_read_close');
    assert.equal(idle._seatDrapeWeight(), 1, 'the skirt stays over her thighs');
    advance(1.1);
    assert.equal(player.currentName, 'sofa_seated_idle');
    assert.equal(idle.heldProp(), null);
});

test('reading seated, she puts the book away before getting up and walking off', () => {
    const { idle, player, scenes, group, advance } = seatFixture();
    idle.update(1 / 30, 0);
    scenes.execute('read_bed');
    arrive(scenes, advance);
    advance(2.2);
    assert.equal(player.currentName, 'bed_read_idle');
    scenes.execute('return_center');
    assert.equal(player.currentName, 'bed_read_close');
    assert.equal(idle.isGettingUp(1.5), true, 'closing the book counts the time to rise as well');
    assert.equal(idle.isGettingUp(2.4), false);
    advance(1.1);
    assert.equal(player.currentName, 'bed_stand_up');
    assert.ok(Math.abs(group.position.x - bed.position[0]) < 1e-6, 'still at the bed');
    advance(1.2);
    assert.ok(Math.abs(group.position.x - bed.position[0]) > 0.01 || player.currentName === 'walk' || scenes.hotspotTransition, 'on her way');
});

test('from reading on the bed she lies down, and from lying down she sits up and reads', () => {
    const { idle, player, scenes, advance } = seatFixture();
    idle.update(1 / 30, 0);
    scenes.execute('read_bed');
    arrive(scenes, advance);
    advance(2.2);
    player.clips.get('bed_lie_down').duration = 5.1;
    scenes.execute('lie_bed');
    assert.equal(player.currentName, 'bed_read_close', 'the book goes first');
    advance(1.1);
    assert.equal(player.currentName, 'bed_lie_down');
    assert.equal(idle.isSeated('bed_lying'), true);
    advance(4);
    assert.equal(player.currentName, 'bed_lying_idle');

    scenes.execute('read_bed');
    assert.equal(scenes.hotspotTransition, null, 'no walk');
    assert.equal(player.currentName, 'bed_sit_up');
    advance(1.1);
    assert.equal(player.currentName, 'bed_read_open');
    assert.equal(idle.isSeated('bed_reading'), true);
});

test('spoken to as the book closes on the way to lying down, she stays sitting with her hands free', () => {
    const { idle, player, scenes, advance } = seatFixture();
    idle.update(1 / 30, 0);
    scenes.execute('read_bed');
    arrive(scenes, advance);
    advance(2.2);
    scenes.execute('lie_bed');
    assert.equal(player.currentName, 'bed_read_close');
    scenes.freeHands();
    advance(1.1);
    assert.equal(idle.isSeated('bed_seated'), true);
    assert.equal(player.currentName, 'bed_seated_idle', 'not lying down');
    assert.equal(scenes.freeHands(), false, 'nothing left to put away');
});

test('at the window she walks up to the glass, rests a hand on it and looks out with her back to the room', () => {
    const { idle, player, scenes, group, advance } = seatFixture();
    const spot = ROOM_HOTSPOTS.windowSpot;
    idle.update(1 / 30, 0);
    assert.equal(getRoomActionCommand('look_window').action.animationName, 'window_looking');
    assert.equal(scenes.canRun('look_window'), true);
    assert.equal(scenes.hasBackToRoom(), false);
    scenes.execute('look_window', { origin: 'routine' });
    arrive(scenes, advance);
    assert.deepEqual([group.position.x, group.position.z], [spot.position[0], spot.position[2]]);
    assert.ok(Math.abs(Math.abs(group.rotation.y) - Math.PI) < 1e-6, 'facing the glass');
    assert.equal(player.currentName, 'window_lean_in');
    assert.equal(scenes.hasBackToRoom(), true);
    advance(1.2);
    assert.equal(player.currentName, 'window_look_idle');
    assert.equal(idle.isSeated('window_looking'), true);
    assert.equal(scenes.framedSpot(), spot, 'the camera comes behind her');
});

test('spoken to at the window she lets go and comes back to the middle', () => {
    const { idle, player, scenes, group, advance } = seatFixture();
    idle.update(1 / 30, 0);
    scenes.execute('look_window', { origin: 'routine' });
    arrive(scenes, advance);
    advance(1.2);
    scenes.execute('return_center', { origin: 'routine' });
    assert.equal(player.currentName, 'window_lean_out');
    advance(1.2);
    assert.ok(scenes.hotspotTransition, 'walking');
    arrive(scenes, advance);
    assert.deepEqual([group.position.x, group.position.z], [0, 0]);
    assert.equal(scenes.hasBackToRoom(), false);
});

test('a package without the window clips never starts it', () => {
    const { player, scenes } = seatFixture();
    player.actions.delete('window_look_idle');
    player.clips.delete('window_look_idle');
    assert.equal(scenes.canRun('look_window'), false);
});

test('from a seat a routine took her to, she jumps to the middle of the room with what she holds', () => {
    const { idle, player, scenes, group, advance } = seatFixture();
    let landed = 0;
    scenes.onLanded = () => { landed += 1; };
    idle.update(1 / 30, 0);
    scenes.execute('read_sofa', { origin: 'routine' });
    arrive(scenes, advance);
    advance(2.2);
    const [x, , z] = [group.position.x, 0, group.position.z];
    assert.equal(scenes.canTeleport(), true);
    assert.equal(scenes.teleportHome(), true);
    assert.equal(scenes.isBusy(), true);
    assert.equal(scenes.canTeleport(), false, 'one at a time');
    advance(0.4);
    assert.deepEqual([group.position.x, group.position.z], [x, z], 'she leaves from where she is');
    assert.equal(landed, 0);
    advance(0.2);
    assert.deepEqual([group.position.x, group.position.z], [0, 0]);
    assert.equal(group.rotation.y, 0);
    assert.equal(idle.isSeated(), false);
    assert.equal(idle.heldProp()?.held ?? false, false);
    assert.equal(player.currentName, 'idle');
    assert.equal(landed, 1);
    assert.equal(scenes.framedSpot(), null, 'the camera comes home');
    advance(1);
    assert.equal(group.visible, true);
    assert.equal(scenes.isBusy(), false);
});

test('she does not jump out of a seat she was sent to', () => {
    const { idle, scenes, advance } = seatFixture();
    idle.update(1 / 30, 0);
    scenes.execute('read_sofa');
    arrive(scenes, advance);
    advance(2.2);
    assert.equal(scenes.canTeleport(), false);
    assert.equal(scenes.teleportHome(), false);
});

test('standing, walking or in Ahorro there is no jump', () => {
    const { idle, scenes, advance } = seatFixture();
    idle.update(1 / 30, 0);
    assert.equal(scenes.canTeleport(), false, 'standing');
    scenes.execute('read_sofa', { origin: 'routine' });
    advance(0.1);
    assert.equal(scenes.canTeleport(), false, 'on her way');
    arrive(scenes, advance);
    advance(2.2);
    assert.equal(scenes.canTeleport(), true);
    scenes.room = { quality: 'low', getObject: () => null };
    assert.equal(scenes.canTeleport(), false, 'Ahorro');
});

test('an order during the jump has her stay and do as she is told', () => {
    const { idle, scenes, group, advance } = seatFixture();
    idle.update(1 / 30, 0);
    scenes.execute('sit_sofa', { origin: 'routine' });
    arrive(scenes, advance);
    advance(1.2);
    const x = group.position.x;
    scenes.teleportHome();
    advance(0.2);
    scenes.execute('read_sofa');
    assert.equal(scenes.teleportEffect.active, false);
    assert.equal(group.visible, true);
    advance(1);
    assert.equal(group.position.x, x, 'she never left');
    assert.equal(idle.isSeated('sofa_reading'), true);
});

test('a new avatar in the middle of a jump does not inherit it', () => {
    const { idle, scenes, group, advance } = seatFixture();
    idle.update(1 / 30, 0);
    scenes.execute('sit_sofa', { origin: 'routine' });
    arrive(scenes, advance);
    advance(1.2);
    scenes.teleportHome();
    advance(0.55);
    assert.equal(group.visible, false);
    scenes.dropProps();
    assert.equal(group.visible, true);
    assert.equal(scenes.teleportEffect.active, false);
});

test('a book that appears: nothing, then a flash in her hand, then gone the same way', () => {
    const home = new THREE.Group(), hand = new THREE.Group(), cover = new THREE.Group();
    hand.scale.setScalar(1.4594);
    cover.name = 'cover';
    const object = new THREE.Group();
    const mesh = new THREE.Mesh(new THREE.BufferGeometry(), new THREE.MeshStandardMaterial());
    object.add(mesh, cover);
    home.add(object);
    const grip = { position: new THREE.Vector3(1, 0, 0), quaternion: new THREE.Quaternion() };
    const prop = new ConjuredProp(object, { grip, hinge: 'cover', openAngle: 2 });
    assert.equal(object.visible, false);
    prop.update(0.016, hand, { id: 'conjured', held: false, open: 0.5 });
    assert.equal(object.visible, false, 'not yet');
    assert.equal(object.parent, home);

    prop.update(0.1, hand, { id: 'conjured', held: true, open: 0.5 });
    assert.equal(object.parent, hand);
    assert.equal(object.visible, true);
    assert.ok(object.scale.x > 0 && object.scale.x < 1, 'it grows');
    assert.ok(mesh.material.emissiveIntensity > 0.5, 'it flashes');
    assert.equal(cover.rotation.z, 1, 'half open, on her palms');
    assert.deepEqual(object.position.toArray(), [1, 0, 0]);
    prop.update(0.5, hand, { id: 'conjured', held: true, open: 1 });
    assert.equal(object.scale.x, 1);
    assert.equal(mesh.material.emissiveIntensity, 0);

    prop.update(0.5, hand, { id: 'conjured', held: false, open: 0.5 });
    assert.equal(object.visible, false);
    assert.equal(object.parent, home, 'gone from her hand');
    assert.equal(cover.rotation.z, 1);
});

test('from one seat to another the camera goes home for the walk and meets her at the end', () => {
    const { idle, scenes, advance } = seatFixture();
    idle.update(1 / 30, 0);
    scenes.execute('sit_sofa');
    arrive(scenes, advance);
    advance(1.2);
    assert.equal(scenes.framedSpot(), seat);
    scenes.execute('sit_bed');
    advance(1.5);
    const { walk } = scenes.hotspotTransition.route;
    advance(walk * 0.4 / 1000);
    assert.equal(scenes.framedSpot()?.view, undefined, 'not left behind on the sofa');
    advance(walk * 0.5 / 1000);
    assert.equal(scenes.framedSpot(), edge);
});

test('tablet_sofa and tablet_bed sit her down and bring the tablet to her hands', () => {
    for (const [order, id, seatName, open, loop] of [
        ['tablet_sofa', 'tabletOnSofa', 'sofa_tablet', 'sofa_tablet_open', 'sofa_tablet_idle'],
        ['tablet_bed', 'tabletOnBed', 'bed_tablet', 'bed_tablet_open', 'bed_tablet_idle'],
    ]) {
        assert.equal(getRoomActionCommand(order).action.id, id);
        const { idle, player, scenes, advance } = seatFixture();
        idle.update(1 / 30, 0);
        scenes.execute(order);
        arrive(scenes, advance);
        assert.equal(idle.isSeated(seatName), true);
        assert.equal(idle.heldProp(), null, 'not while she sits down');
        advance(1);
        assert.equal(player.currentName, open);
        assert.equal(idle.heldProp().id, 'tablet');
        advance(1.1);
        assert.equal(player.currentName, loop);
        assert.deepEqual(idle.heldProp(), { id: 'tablet', held: true, open: 1 });
    }
});

test('the tablet appears and goes away at the moments the clips bring her hands to it', () => {
    const { idle, player } = seatFixture();
    idle.update(1 / 30, 0);
    const clip = (name, time) => { player.play(name, { loop: false, fadeIn: 0 }); player.current.time = time; };
    clip('sofa_tablet_open', 1.2);
    assert.equal(idle.heldProp().held, false, 'the hands are still apart');
    clip('sofa_tablet_open', 1.5);
    assert.equal(idle.heldProp().held, true);
    clip('sofa_tablet_close', 1);
    assert.equal(idle.heldProp().held, true);
    clip('sofa_tablet_close', 1.3);
    assert.equal(idle.heldProp().held, false);
});

test('she changes from the book to the tablet on the same seat, putting one away first', () => {
    const { idle, player, scenes, advance } = seatFixture();
    idle.update(1 / 30, 0);
    scenes.execute('read_sofa');
    arrive(scenes, advance);
    advance(2.2);
    assert.equal(player.currentName, 'sofa_read_idle');
    scenes.execute('tablet_sofa');
    assert.equal(scenes.hotspotTransition, null, 'no walk');
    assert.equal(player.currentName, 'sofa_read_close');
    advance(1.1);
    assert.equal(player.currentName, 'sofa_tablet_open');
    assert.equal(idle.isSeated('sofa_tablet'), true);
    advance(1.1);
    assert.equal(player.currentName, 'sofa_tablet_idle');
});

test('the tablet leaves the stand on the desk while she holds it, and comes back', () => {
    const home = new THREE.Group(), hand = new THREE.Group(), panel = new THREE.Group();
    hand.scale.setScalar(1.4594);
    const object = new THREE.Group();
    object.add(new THREE.Mesh(new THREE.BufferGeometry(), new THREE.MeshStandardMaterial()));
    home.add(object);
    const grip = { position: new THREE.Vector3(), quaternion: new THREE.Quaternion() };
    const prop = new ConjuredProp(object, { grip, scale: 0.5, replaces: panel });
    prop.update(0.016, hand, { id: 'tablet', held: false });
    assert.equal(panel.visible, true);
    prop.update(0.5, hand, { id: 'tablet', held: true });
    assert.equal(panel.visible, false, 'gone from the desk');
    assert.equal(object.scale.x, 0.5, 'its own size');
    prop.update(0.5, hand, null);
    assert.equal(panel.visible, true, 'back on its stand');
    prop.update(0.5, hand, { id: 'tablet', held: true });
    prop.drop();
    assert.equal(panel.visible, true);
    assert.equal(object.parent, home);
});

function chairFixture() {
    const fixture = seatFixture();
    const chair = new THREE.Group();
    chair.position.z = 0.92;
    fixture.scenes.room = { getObject: name => (name === 'chair' ? chair : null) };
    fixture.idle.update(1 / 30, 0);
    const clip = (name, time) => { fixture.player.play(name, { loop: false, fadeIn: 0 }); fixture.player.current.time = time; };
    return { ...fixture, chair, clip };
}

test('use_pc rolls the chair back as she comes, sits her and starts typing', () => {
    assert.equal(getRoomActionCommand('use_pc').action.id, 'usePC_idle');
    const { idle, player, scenes, advance, chair } = chairFixture();
    scenes.execute('use_pc');
    advance(1.2);
    assert.ok(Math.abs(chair.position.z - 1.82) < 1e-6, 'out of her way before she gets there');
    arrive(scenes, advance);
    assert.equal(idle.isSeated('desk_typing'), true);
    advance(2.2);
    assert.equal(player.currentName, 'desk_type_idle');
    assert.equal(chair.position.z, 0.92, 'home, with her on it');
});

test('the chair moves with the clips, on the frames she rides it', () => {
    const { idle, clip } = chairFixture();
    assert.equal(idle.chairPlace(), null);
    for (const [time, place] of [[2, 1], [2.567, 1], [3.267, 0.5], [3.967, 0], [4.2, 0]]) {
        clip('desk_sit_down', time);
        assert.ok(Math.abs(idle.chairPlace() - place) < 1e-3, `sitting at ${time}`);
    }
    for (const [time, place] of [[0, 0], [0.834, 0.5], [1.5, 1], [3, 1]]) {
        clip('desk_stand_up', time);
        assert.ok(Math.abs(idle.chairPlace() - place) < 1e-3, `rising at ${time}`);
    }
});

test('the chair waits until she has left its way, then rolls home', () => {
    const { group, scenes, advance, chair, idle } = chairFixture();
    group.position.set(2.766, 0, -1.605);
    advance(1.2);
    assert.ok(Math.abs(chair.position.z - 1.82) < 1e-6, 'she is standing where it goes');
    assert.equal(idle.chairPlace(), null);
    group.position.set(1.2, 0, -1.3);
    advance(0.5);
    assert.ok(chair.position.z < 1.82 && chair.position.z > 0.92, 'on its way');
    advance(0.8);
    assert.equal(chair.position.z, 0.92);
    assert.equal(scenes.hotspotTransition, null);
});

test('she changes between typing and sitting without standing or moving the chair', () => {
    const { idle, player, scenes, advance, chair, group } = chairFixture();
    scenes.execute('use_pc');
    arrive(scenes, advance);
    advance(2.2);
    assert.equal(player.currentName, 'desk_type_idle');
    const where = group.position.clone();
    scenes.execute('sit_desk');
    assert.equal(scenes.hotspotTransition, null, 'no walk');
    assert.equal(player.currentName, 'desk_type_close');
    advance(1.1);
    assert.equal(idle.isSeated('desk_seated'), true);
    assert.equal(player.currentName, 'desk_seated_idle');
    assert.ok(group.position.equals(where));
    assert.equal(chair.position.z, 0.92);
});

test('the close view of the desk joins her once she is low and leaves before she rises', () => {
    const { scenes, advance, idle, clip } = chairFixture();
    scenes.execute('use_pc');
    arrive(scenes, advance);
    const spot = scenes._atSpot;
    assert.equal(spot.id, 'deskSeatSpot');
    clip('desk_sit_down', 0.5);
    assert.equal(scenes.framedSpot(), null, 'standing, her head is out of that frame');
    clip('desk_sit_down', 1.4);
    assert.equal(scenes.framedSpot(), spot);
    clip('desk_type_idle', 1);
    assert.equal(scenes.framedSpot(), spot);
    clip('desk_stand_up', 0.2);
    assert.equal(scenes.framedSpot(), null);
    assert.equal(idle.isLowInSeat(), false);
});

test('a view with a way round keeps the camera off the straight line and ends on the view', () => {
    const { camera, view } = cameraFixture();
    const spot = { position: [4, 1, -3], target: [3, 0, -2], via: [6, 1, -2] };
    for (let i = 0; i < 45; i++) view.update(1 / 30, 0, spot);
    const straightX = 0.34 + (4 - 0.34) * 0.5;
    assert.ok(camera.position.x > straightX + 1, 'out to the side of her');
    for (let i = 0; i < 60; i++) view.update(1 / 30, 0, spot);
    assert.deepEqual(camera.position.toArray(), [4, 1, -3]);
});

const settle = (scenes, advance) => { for (let i = 0; i < 900 && scenes.hotspotTransition; i++) advance(1 / 30); };

test('read_center and tablet_center take the thing out standing where she is, at the centre', () => {
    for (const [order, id, pose, open, loop, prop] of [
        ['read_center', 'readStanding', 'stand_reading', 'stand_read_open', 'stand_read_idle', 'conjured'],
        ['tablet_center', 'tabletStanding', 'stand_tablet', 'stand_tablet_open', 'stand_tablet_idle', 'tablet'],
    ]) {
        assert.equal(getRoomActionCommand(order).action.id, id);
        const { idle, player, scenes, group, advance } = seatFixture();
        idle.update(1 / 30, 0);
        scenes.execute(order);
        settle(scenes, advance);
        assert.equal(idle.isSeated(pose), true);
        assert.equal(group.position.length(), 0, 'she has not moved');
        assert.ok([open, loop].includes(player.currentName));
        advance(1.1);
        assert.equal(player.currentName, loop);
        assert.equal(idle.heldProp().id, prop);
        assert.equal(idle.chairPlace(), null, 'the chair is not hers here');
    }
});

test('standing, she puts one thing away before taking the other, and Centro puts it away', () => {
    const { idle, player, scenes, advance } = seatFixture();
    idle.update(1 / 30, 0);
    scenes.execute('read_center');
    settle(scenes, advance);
    advance(1.1);
    assert.equal(player.currentName, 'stand_read_idle');
    scenes.execute('tablet_center');
    assert.equal(player.currentName, 'stand_read_close');
    settle(scenes, advance);
    advance(1.1);
    assert.equal(idle.isSeated('stand_tablet'), true);
    assert.equal(player.currentName, 'stand_tablet_idle');
    scenes.execute('return_center');
    assert.equal(player.currentName, 'stand_tablet_close');
    settle(scenes, advance);
    advance(1.5);
    assert.equal(idle.isSeated(), false);
    assert.equal(idle.heldProp(), null);
});

test('from a seat she walks to the centre first and takes it out there', () => {
    const { idle, scenes, group, advance } = seatFixture();
    idle.update(1 / 30, 0);
    scenes.execute('sit_sofa');
    settle(scenes, advance);
    advance(2);
    assert.equal(idle.isSeated('sofa_seated'), true);
    scenes.execute('read_center');
    assert.equal(idle.isSeated(), false, 'she gets up first');
    settle(scenes, advance);
    assert.equal(idle.isSeated('stand_reading'), true);
    assert.ok(group.position.length() < 0.01);
});

test('an order holds her and a routine does not, and every order is counted', () => {
    const { idle, scenes, advance } = seatFixture();
    idle.update(1 / 30, 0);
    scenes.execute('sit_sofa', { origin: 'routine' });
    settle(scenes, advance);
    advance(2);
    assert.equal(idle.isSeated('sofa_seated'), true);
    assert.equal(scenes.heldByOrder(), false);
    assert.equal(scenes.orders, 0);
    scenes.execute('sit_sofa');
    assert.equal(scenes.heldByOrder(), true, 'told to sit where she already sits: now it is an order');
    assert.equal(scenes.orders, 1);
    scenes.execute('return_center');
    settle(scenes, advance);
    advance(2);
    assert.equal(scenes.heldByOrder(), false);
});

test('freeHands puts away what she holds and leaves her in her place', () => {
    for (const [order, held, seat] of [['read_sofa', 'sofa_reading', 'sofa_seated'], ['tablet_bed', 'bed_tablet', 'bed_seated'],
        ['use_pc', 'desk_typing', 'desk_seated'], ['lie_bed', 'bed_lying', 'bed_seated']]) {
        const { idle, scenes, group, advance } = seatFixture();
        idle.update(1 / 30, 0);
        scenes.execute(order, { origin: 'routine' });
        settle(scenes, advance);
        advance(3);
        assert.equal(idle.isSeated(held), true, order);
        const where = group.position.clone();
        assert.equal(scenes.freeHands(), true, order);
        advance(3);
        assert.equal(idle.isSeated(seat), true, order);
        assert.ok(group.position.equals(where), order);
    }
});

test('freeHands waits for a clip on its way in, so nothing is cut in half', () => {
    const { idle, player, scenes, advance } = seatFixture();
    idle.update(1 / 30, 0);
    scenes.execute('read_center', { origin: 'routine' });
    settle(scenes, advance);
    assert.equal(player.currentName, 'stand_read_open');
    assert.equal(scenes.freeHands(), false);
    assert.equal(player.currentName, 'stand_read_open');
    advance(1.2);
    assert.equal(scenes.freeHands(), true);
    assert.equal(player.currentName, 'stand_read_close');
});

test('standing, freeHands puts it away and she is free', () => {
    const { idle, scenes, player, advance } = seatFixture();
    idle.update(1 / 30, 0);
    scenes.execute('read_center', { origin: 'routine' });
    settle(scenes, advance);
    advance(2);
    scenes.freeHands();
    assert.equal(player.currentName, 'stand_read_close');
    advance(1.5);
    assert.equal(idle.isSeated(), false);
    assert.equal(scenes.freeHands(), false, 'nothing left to put away');
});

test('a seated gesture stays out of the body, and those in the head go on top of the clip', () => {
    const { idle, player, scenes, advance } = seatFixture();
    idle.update(1 / 30, 0);
    scenes.execute('sit_sofa');
    settle(scenes, advance);
    advance(2);
    const heads = [];
    idle.avatar.avatar._triggerHeadAnim = (...args) => heads.push(args);
    const clip = player.currentName;
    assert.equal(idle.playClipExternal('nod'), true);
    assert.equal(idle.trigger('shake'), true);
    assert.equal(idle.playClipExternal('wave'), true);
    assert.equal(player.currentName, clip);
    assert.deepEqual(heads, [['nod', 700, { over: true }], ['shake', 700, { over: true }]]);
});

test('before any clip has loaded she is not getting up, and nothing breaks asking', () => {
    const { idle } = seatFixture();
    idle._vrmaPlayer = null;
    assert.equal(idle.isGettingUp(), false);
    assert.equal(idle.isGettingUp(2.4), false);
});

test('canHold wants every clip of a seat', () => {
    const { idle, player } = seatFixture();
    idle.update(1 / 30, 0);
    assert.equal(idle.canHold('sofa_reading'), true);
    player.clips.delete('sofa_read_close');
    player.actions.delete('sofa_read_close');
    assert.equal(idle.canHold('sofa_reading'), false);
    assert.equal(idle.canHold('sofa_seated'), true);
});

test('canRun says no to what a package without the room clips could only half do', () => {
    const { idle, player, scenes } = seatFixture();
    idle.update(1 / 30, 0);
    assert.equal(scenes.canRun('read_sofa'), true);
    assert.equal(scenes.canRun('nonsense'), false);
    player.clips.delete('sofa_read_idle');
    player.actions.delete('sofa_read_idle');
    assert.equal(scenes.canRun('read_sofa'), false);
});

test('a prop keeps its size and its place in the grip whatever the size of the hand', () => {
    const world = (object) => object.getWorldScale(new THREE.Vector3()).x;
    const home = new THREE.Group();
    const sizes = [1.4594, 1.5447].map((scale) => {
        const hand = new THREE.Group();
        hand.scale.setScalar(scale);
        const object = new THREE.Group();
        object.add(new THREE.Mesh(new THREE.BufferGeometry(), new THREE.MeshStandardMaterial()));
        home.add(object);
        const prop = new ConjuredProp(object, { grip: { position: new THREE.Vector3(0.1, 0, 0), quaternion: new THREE.Quaternion() }, scale: 1 / 1.4594 });
        prop.update(1, hand, { id: 'tablet', held: true });
        prop.update(1, hand, { id: 'tablet', held: true });
        hand.updateWorldMatrix(true, true);
        return [world(object), object.getWorldPosition(new THREE.Vector3()).x];
    });
    assert.ok(Math.abs(sizes[0][0] - 1) < 1e-6);
    assert.ok(Math.abs(sizes[1][0] - 1) < 1e-6);
    assert.ok(Math.abs(sizes[0][1] - sizes[1][1]) < 1e-6);
});

test('hands follow IRIS only while a clip has them on something', () => {
    const { idle, player } = seatFixture();
    idle.update(1 / 30, 0);
    const at = (name, time) => { player.play(name, { loop: false, fadeIn: 0 }); player.current.time = time; return idle._handReachWeight(); };
    assert.equal(at('idle', 0), 0);
    assert.equal(at('sofa_sit_down', 0.5), 0);
    assert.equal(at('sofa_read_open', 0.1), 0);
    assert.equal(at('sofa_read_open', 0.9), 1);
    assert.equal(at('sofa_read_idle', 0.3), 1);
    assert.equal(at('stand_tablet_idle', 0.3), 1);
    assert.equal(at('desk_type_idle', 0.3), 1);
    assert.equal(at('book_read_idle', 0.3), 1);
    const end = player.clips.get('sofa_read_close').duration;
    assert.equal(at('sofa_read_close', end - 0.95), 1);
    assert.equal(at('sofa_read_close', end - 0.1), 0);
});

test('on a VRM 0 hand a grip goes half a turn round, because its bones look the other way', () => {
    const home = new THREE.Group(), hand = new THREE.Group();
    hand.scale.setScalar(1.4594);
    const object = new THREE.Group();
    object.add(new THREE.Mesh(new THREE.BufferGeometry(), new THREE.MeshStandardMaterial()));
    home.add(object);
    const grip = { position: new THREE.Vector3(0.1, 0.2, 0.3), quaternion: new THREE.Quaternion() };
    const prop = new ConjuredProp(object, { grip });
    prop.update(0.1, hand, { id: 'conjured', held: true, open: 0 }, true);
    assert.ok(object.position.distanceTo(new THREE.Vector3(-0.1, 0.2, -0.3)) < 1e-6);
    const half = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), Math.PI);
    assert.ok(Math.abs(Math.abs(object.quaternion.dot(half)) - 1) < 1e-6);
});
