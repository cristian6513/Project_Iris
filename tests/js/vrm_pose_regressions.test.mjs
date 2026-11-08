import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

// Use the same pinned Three.js as Companion, including its real mixer and
// property bindings. No WebGL, network, camera or Core process is needed.
const threeUrl = new URL('../../companion/node_modules/three/build/three.module.js', import.meta.url).href;
const gltfUrl = new URL('../../companion/node_modules/three/examples/jsm/loaders/GLTFLoader.js', import.meta.url).href;
const THREE = await import(threeUrl);
const source = async name => readFile(new URL('../../face/js/' + name, import.meta.url), 'utf8');
let moduleIndex = 0;
const moduleUrl = text => 'data:text/javascript;base64,' + Buffer.from(text + '\n//# sourceURL=vrm-regression-module-' + (++moduleIndex) + '.mjs').toString('base64');
const playerUrl = moduleUrl((await source('vrm_animation_loader.js'))
    .replace("from './avatar_lifecycle.js'", `from '${new URL('../../face/js/avatar_lifecycle.js', import.meta.url).href}'`)
    .replace("from 'three'", `from '${threeUrl}'`)
    .replace("from 'three/addons/loaders/GLTFLoader.js'", `from '${gltfUrl}'`));
const { VRMAnimationPlayer } = await import(playerUrl);
const { IdleSystem } = await import(moduleUrl((await source('idle_system.js'))
    .replace("from './avatar_lifecycle.js'", `from '${new URL('../../face/js/avatar_lifecycle.js', import.meta.url).href}'`)
    .replace("from 'three'", `from '${threeUrl}'`)
    .replace(/from '\.\/vrm_animation_loader\.js[^']*'/, `from '${playerUrl}'`)));

function fixture() {
    const scene = new THREE.Group();
    const head = new THREE.Bone(); head.name = 'Normalized_Head'; scene.add(head);
    const arm = new THREE.Bone(); arm.name = 'Normalized_LeftUpperArm'; scene.add(arm);
    const vrm = { scene, humanoid: { getNormalizedBoneNode: name => ({ head, leftUpperArm: arm })[name] || null } };
    const player = new VRMAnimationPlayer(vrm);
    const avatar = { avatar: { vrm, group: scene, restYaw: 0 } };
    const idle = new IdleSystem(avatar);
    idle._vrmaPlayer = player;
    idle._vrmaManifest = { clips: {}, pose_map: {}, return_idle_pool: [] };
    idle._vrmaInitAttempted = true;
    idle._ensureBoneRefs();
    idle.nextAction = 100;
    function clip(name, angle, { duration = 3, bone = head, endAngle = angle, uuid = false } = {}) {
        const q = a => new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), a).toArray();
        const track = new THREE.QuaternionKeyframeTrack(`${uuid ? bone.uuid : bone.name}.quaternion`, [0, duration], [...q(angle), ...q(endAngle)]);
        const c = new THREE.AnimationClip(name, duration, [track]);
        player.clips.set(name, c); player.actions.set(name, player.mixer.clipAction(c));
        return c;
    }
    function advance(seconds, step = 1 / 60) {
        for (let t = 0; t < seconds - 1e-9; t += step) idle.update(Math.min(step, seconds - t), t);
    }
    return { scene, head, arm, player, idle, clip, advance };
}

test('an external gesture cannot be interrupted by the random pose timer', () => {
    const { idle, player, clip, advance } = fixture();
    clip('wave', 0.8, { duration: 20 });
    idle._selectPose = () => ({ name: 'subtle_nod', duration: 900 });
    idle.nextAction = 0;
    assert.equal(idle.playClipExternal('wave'), true);
    advance(2);
    assert.equal(player.currentName, 'wave');
    assert.equal(idle._vrmaExternalClip, 'wave');
    assert.equal(idle.currentPose, null);
});

test('stop finishes on animation time even after idle clears its active flag', () => {
    const { idle, player, clip, advance } = fixture();
    clip('wave', 0.8);
    idle.playClipExternal('wave'); advance(0.5);
    idle._detachVrmaFinishedHandler();
    player.stop(0.2); idle._vrmaActive = false; idle._vrmaExternalClip = null;
    advance(0.5);
    assert.equal(player.actions.get('wave').isScheduled(), false);
    assert.equal(player.mixer.stats.actions.inUse, 0);
});

for (const repeat of [false, true]) {
    test(`interrupting a crossfade ${repeat ? 'with the same clip' : 'with a third clip'} starts from the visible pose`, () => {
        const { player, head, clip } = fixture();
        clip('a', 1.4); clip('b', -0.8); clip('c', 0.2);
        player.play('a', { fadeIn: 0 }); player.update(0.8);
        player.play('b', { fadeIn: 0.6 }); player.update(0.1);
        const before = head.quaternion.clone();
        player.play(repeat ? 'b' : 'c', { fadeIn: 0.6 });
        player.update(1e-6);
        assert.ok(before.angleTo(head.quaternion) < 0.001, 'transition must not jump to rest or the old target');
        player.update(2);
        assert.equal(player.mixer.stats.actions.inUse, 1, 'only the current clip remains scheduled');
    });
}

test('clips with different lengths retain their authored playback speed', () => {
    const { player, clip } = fixture();
    clip('short', 0.5, { duration: 1 }); clip('long', -0.2, { duration: 10 });
    player.play('short'); player.update(0.5);
    player.play('long', { fadeIn: 0.5 }); player.update(0.1);
    assert.ok(Math.abs(player.current.time - 0.1) < 1e-8);
});

test('head ownership resolves normalized names and UUID track bindings', () => {
    const { player, clip, arm } = fixture();
    clip('head_name', 0.2); clip('head_uuid', 0.2, { uuid: true });
    clip('arm_only', 0.2, { bone: arm });
    assert.equal(player.clipAnimatesHead('head_name'), true);
    assert.equal(player.clipAnimatesHead('head_uuid'), true);
    assert.equal(player.clipAnimatesHead('arm_only'), false);
});

test('a procedural pose interpolates rotations along the short quaternion arc', () => {
    const { idle, head } = fixture();
    head.rotation.set(0, 0, Math.PI - 0.01);
    idle._boneTargets.head.set(0, 0, -Math.PI + 0.01);
    const before = head.quaternion.clone();
    idle._applyBoneTargets(0.5);
    assert.ok(before.angleTo(head.quaternion) < 0.02);
});

test('finished gestures keep exclusive ownership for the complete adaptive return fade', () => {
    const { idle, player, clip, advance } = fixture();
    clip('wave', 2.4, { duration: 1 }); clip('idle', -1.5);
    idle._vrmaManifest.return_idle_pool = ['idle'];
    idle.playClipExternal('wave', { fadeOut: 0.3 }); advance(1);
    assert.equal(player.currentName, 'idle');
    advance(0.36);
    assert.equal(idle.isExplicitAnimationActive(), true);
    advance(0.5);
    assert.equal(idle.isExplicitAnimationActive(), false);
});

test('a finished event hands off from the actual final keyframe', () => {
    const { idle, player, head, clip, advance } = fixture();
    clip('gesture', 0, { duration: 1, endAngle: 1 });
    clip('idle', -1);
    idle._vrmaManifest.return_idle_pool = ['idle'];
    idle.playClipExternal('gesture', { fadeIn: 0 });
    advance(1);
    assert.equal(player.currentName, 'idle');
    const final = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), 1);
    assert.ok(head.quaternion.angleTo(final) < 0.001);
});

test('without a return-idle asset a gesture releases every binding and restores rest', () => {
    const { idle, player, head, clip, advance } = fixture();
    clip('gesture', 1.5, { duration: 0.5 });
    idle.playClipExternal('gesture');
    advance(1.5);
    assert.equal(player.mixer.stats.actions.inUse, 0);
    assert.equal(idle.isExplicitAnimationActive(), false);
    assert.ok(head.quaternion.angleTo(new THREE.Quaternion()) < 0.001);
});

test('a partial clip releases omitted bones smoothly, even across repeated interruptions', () => {
    const { player, head, arm, clip } = fixture();
    clip('head', 1.4); clip('arm', -0.8, { bone: arm });
    player.play('head', { fadeIn: 0 }); player.update(0.2);
    const before = head.quaternion.clone();
    player.play('arm', { fadeIn: 0.4 }); player.update(1e-6);
    assert.ok(head.quaternion.angleTo(before) < 0.001);
    player.update(1);
    assert.ok(head.quaternion.angleTo(new THREE.Quaternion()) < 0.001);
    for (let i = 0; i < 100; i++) {
        player.play(i % 2 ? 'head' : 'arm'); player.update(0.02);
        assert.ok(player.mixer.stats.actions.inUse <= 2);
        assert.ok(player.mixer.stats.actions.total <= player.clips.size + 1);
    }
    player.stop(0.3); player.update(0); // suspended frame: no wall-clock cleanup
    assert.ok(player.transitionRemaining > 0);
    player.update(0.5);
    assert.equal(player.mixer.stats.actions.inUse, 0);
    assert.equal(player.mixer.stats.actions.total, player.clips.size);
});

test('stop eases the pose out instead of holding it and letting go at the end', () => {
    const { player, head, clip } = fixture();
    clip('hold', 1);
    player.play('hold', { fadeIn: 0 }); player.update(0.2);
    player.stop(0.4);
    const rest = new THREE.Quaternion(), previous = head.quaternion.clone();
    let biggest = 0, halfway = 0;
    for (let i = 1; i <= 26; i++) {
        player.update(1 / 60);
        biggest = Math.max(biggest, previous.angleTo(head.quaternion));
        previous.copy(head.quaternion);
        if (i === 12) halfway = head.quaternion.angleTo(rest);
    }
    assert.ok(halfway > 0.25 && halfway < 0.75, `halfway it is at ${halfway} rad`);
    assert.ok(biggest < 0.15, `one frame moved ${biggest} rad`);
    assert.ok(head.quaternion.angleTo(rest) < 0.001);
    assert.equal(player.mixer.stats.actions.inUse, 0);
});

function activityFixture() {
    const f = fixture();
    f.clip('idle', 0);
    f.clip('listen', 0, { duration: 1, endAngle: 1.2 });
    f.clip('think', 0.5);
    f.clip('talk', -0.3);
    f.idle._vrmaManifest.return_idle_pool = ['idle'];
    f.idle._vrmaManifest.activity_clips = {
        listening: { clip: 'listen', loop: false },
        thinking: { clip: 'think', loop: true },
        speaking: { clip: 'talk', loop: true },
    };
    return f;
}

test('listening enters once and holds its final pose beyond the clip duration', () => {
    const { idle, player, head, advance } = activityFixture();
    idle.nextAction = 0;
    assert.equal(idle.setActivity('listening'), true);
    advance(1.5);
    const held = head.quaternion.clone();
    for (let i = 0; i < 30; i++) {
        idle.setActivity('listening'); advance(1);
    }
    assert.equal(player.currentName, 'listen');
    assert.equal(player.current.time, 1);
    assert.equal(player.current.paused, true);
    assert.equal(idle.isExplicitAnimationActive(), true);
    assert.ok(head.quaternion.angleTo(held) < 1e-6);
});

test('listening switches to thinking and releases to authored idle without snapping', () => {
    const { idle, player, head, advance } = activityFixture();
    idle.setActivity('listening'); advance(2);
    const before = head.quaternion.clone();
    idle.setActivity('thinking'); advance(1e-6);
    assert.ok(head.quaternion.angleTo(before) < 0.001);
    advance(5);
    assert.equal(player.currentName, 'think');
    idle.setActivity('idle'); advance(1);
    assert.equal(player.currentName, 'idle');
    assert.equal(idle.isExplicitAnimationActive(), false);
    assert.equal(player.mixer.stats.actions.inUse, 1);
});

test('a response gesture finishes during speech then resumes the talking clip', () => {
    const { idle, player, clip, advance } = activityFixture();
    clip('wave', 0.8, { duration: 1 });
    idle.setActivity('speaking'); advance(1);
    idle.playClipExternal('wave');
    for (let i = 0; i < 30; i++) { idle.setActivity('speaking'); advance(1 / 60); }
    assert.equal(player.currentName, 'wave');
    advance(0.6);
    assert.equal(player.currentName, 'talk');
    idle.setActivity('idle'); advance(1);
    assert.equal(player.currentName, 'idle');
    assert.equal(idle._vrmaExternalClip, null);
});

test('capture interrupts a gesture and cancels its previous finish callback', () => {
    const { idle, player, clip, advance } = activityFixture();
    clip('wave', -1.0, { duration: 0.7 });
    idle.playClipExternal('wave'); advance(0.2);
    idle.setActivity('listening'); advance(5);
    assert.equal(player.currentName, 'listen');
    idle.setActivity('idle'); advance(1);
    assert.equal(player.currentName, 'idle');
    assert.equal(idle._vrmaFinishedHandler, null);
});

test('runtime activity requested before assets load is applied when the player becomes ready', () => {
    const { idle, player, advance } = activityFixture();
    idle._vrmaPlayer = null;
    assert.equal(idle.setActivity('listening'), false);
    idle._vrmaPlayer = player;
    assert.equal(idle.setActivity('listening'), true);
    advance(3);
    assert.equal(player.currentName, 'listen');
});



test('body-only poses preserve blinking while retaining exclusive root ownership', () => {
    const {idle,clip,advance}=fixture();
    clip('face_clip',0.5);clip('body_clip',0.3);clip('idle',0);
    idle._vrmaManifest.body_only_clips=['body_clip','idle'];
    idle._vrmaManifest.return_idle_pool=['idle'];
    const avatar=idle.avatar.avatar;
    idle.playClipExternal('face_clip');
    assert.equal(avatar.animationOwnsBlink,true);
    idle.playClipExternal('body_clip');
    assert.equal(avatar.animationOwnsBlink,false);
    assert.equal(avatar.animationOwnsRootMotion,true);
    advance(5);
    assert.equal(avatar.animationOwnsBlink,false);
    assert.equal(avatar.animationOwnsRootMotion,false);
});
