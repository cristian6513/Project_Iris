import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const threeUrl = new URL('../../companion/node_modules/three/build/three.module.js', import.meta.url).href;
const gltfUrl = new URL('../../companion/node_modules/three/examples/jsm/loaders/GLTFLoader.js', import.meta.url).href;
const THREE = await import(threeUrl);
const source = name => readFile(new URL('../../face/js/' + name, import.meta.url), 'utf8');
const playerSource = (await source('vrm_animation_loader.js'))
    .replace("from './avatar_lifecycle.js'", `from '${new URL('../../face/js/avatar_lifecycle.js', import.meta.url).href}'`)
    .replace("from 'three'", `from '${threeUrl}'`)
    .replace("from 'three/addons/loaders/GLTFLoader.js'", `from '${gltfUrl}'`);
const { VRMAnimationPlayer } = await import('data:text/javascript;base64,' + Buffer.from(playerSource).toString('base64'));

function playerFixture({ seamless = false } = {}) {
    const scene = new THREE.Group(), hips = new THREE.Bone();
    hips.name = 'hips'; hips.position.y = 1; scene.add(hips);
    const player = new VRMAnimationPlayer({ scene, humanoid: {} });
    const q = angle => new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0,1,0), angle).toArray();
    const clip = new THREE.AnimationClip('idle', 2, [
        new THREE.VectorKeyframeTrack(hips.uuid + '.position', [0, 1, 2],
            [0,1,0, 0.05,0.95,0, ...(seamless ? [0,1,0] : [0.1,0.9,0])]),
        new THREE.QuaternionKeyframeTrack(hips.uuid + '.quaternion', [0, 1, 2],
            [...q(0), ...q(0.15), ...q(seamless ? 0 : 0.3)]),
    ]);
    player.clips.set('idle', clip); player.actions.set('idle', player.mixer.clipAction(clip));
    return { player, hips };
}

test('repeating a non-seamless clip preserves the visible hip position and rotation', () => {
    const { player, hips } = playerFixture();
    player.play('idle', { loop:true, fadeIn:0 }); player.update(1.999999);
    for (let cycle = 0; cycle < 5; cycle++) {
        const position = hips.position.clone(), rotation = hips.quaternion.clone();
        player.update(0.000002);
        assert.ok(position.distanceTo(hips.position) < 0.0001);
        assert.ok(rotation.normalize().angleTo(hips.quaternion.clone().normalize()) < 0.0001);
        assert.ok(player.transitionRemaining > 0);
        let previous = hips.position.clone();
        for (let i=0;i<60;i++) {
            player.update(1/60);
            assert.ok(previous.distanceTo(hips.position)<0.015, 'return to first frame must be gradual');
            previous.copy(hips.position);
        }
        assert.equal(player.mixer.stats.actions.inUse, 1);
        assert.ok(Math.abs(player.current.time - 1.000001)<1e-6, 'authored speed is preserved');
        player.update(0.999998);
    }
    player.stop(0.2); player.update(0.3);
    assert.equal(player.mixer.stats.actions.inUse, 0);
});

test('authored seamless loops keep their original timing without extra fades', () => {
    const { player } = playerFixture({ seamless:true });
    player.play('idle', { loop:true, fadeIn:0 }); player.update(2.1);
    assert.equal(player.transitionRemaining, 0);
    assert.equal(player.mixer.stats.actions.inUse, 1);
    assert.ok(Math.abs(player.current.time - 0.1)<1e-8);
});

test('one-shot clips hold their authored final position instead of looping', () => {
    const { player, hips } = playerFixture();
    let finished=0; player.mixer.addEventListener('finished',()=>finished++);
    player.play('idle',{loop:false,fadeIn:0}); player.update(2.1);
    assert.ok(hips.position.distanceTo(new THREE.Vector3(0.1,0.9,0))<1e-6);
    assert.equal(player.transitionRemaining,0); assert.equal(finished,1);
});

async function method(file, name, dependencies = {}) {
    const block = (await source(file)).match(new RegExp('^    ' + name + '\\([^]*?^    \\}', 'm'))?.[0];
    assert.ok(block);
    return new Function(...Object.keys(dependencies), 'return ({' + block.trim() + '})')(...Object.values(dependencies))[name];
}

test('a room trip pauses during a gesture, then continues from the same position and keeps root height', async () => {
    const update = await method('scene_actions.js','update',{THREE});
    const transition = await method('scene_actions.js','_updateHotspotTransition',{THREE,lerpAngle:(a,b,t)=>a+Math.atan2(Math.sin(b-a),Math.cos(b-a))*t});
    const group = new THREE.Group(); group.position.y = 0.047;
    let explicit=false;
    const state={avatar:{avatar:{group}},effects:{},teleportEffect:{update(){}},idleSystem:{isExplicitAnimationActive:()=>explicit},
        hotspotTransition:{elapsed:0,duration:1000,fromPosition:new THREE.Vector3(),toPosition:new THREE.Vector3(3,0,2),fromYaw:0,toYaw:1},
        _updateHotspotTransition:transition,_updateWalkCycle(){},_updateActionLookAt(){},_playHotspotPose(){}};
    update.call(state,0.4,0.4); const before=group.position.clone();
    explicit=true; update.call(state,30,30.4);
    assert.ok(group.position.equals(before));
    explicit=false; update.call(state,0.001,30.401);
    assert.ok(group.position.distanceTo(before)<0.01, 'must not teleport after a long gesture');
    assert.equal(group.position.y,0.047);
    update.call(state,0.6,31);
    assert.equal(group.position.x,3); assert.equal(group.position.z,2);
    assert.equal(group.position.y,0.047);
});

test('Face resumes a stalled frame on bounded simulation time', async () => {
    const animate = await method('app.js','_animate',{requestAnimationFrame(){},THREE});
    const steps=[], times=[];
    const update=(dt,t)=>{steps.push(dt);times.push(t);};
    const state={clock:{getDelta:()=>30},camera:{position:{},lookAt(){}},visionAssistant:{enabled:false},
        avatarManager:{update,finalizeFrame:dt=>steps.push(dt)},particles:{update},lipSync:{update},
        composer:{render(){}},microLife:{beginFrame(){},update,applyPost:dt=>steps.push(dt)}};
    animate.call(state); animate.call(state);
    assert.ok(steps.every(dt=>dt===0.05));
    assert.ok(times.filter(Number.isFinite).every(t=>t<=0.1));
    assert.equal(state._animationElapsed,0.1);
});
