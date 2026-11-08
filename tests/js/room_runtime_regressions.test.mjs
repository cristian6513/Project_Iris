import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {RoomAtmosphere} from '../../face/js/room_atmosphere.js';

// Exercise actual method bodies without booting Core, WebGL, audio or sensors.
async function method(file, name, dependencies = {}) {
    const source = await readFile(new URL('../../face/js/' + file, import.meta.url), 'utf8');
    const block = source.match(new RegExp('^    ' + name + '\\([^]*?^    \\}', 'm'))?.[0];
    assert.ok(block, name + ' must exist');
    return new Function(...Object.keys(dependencies), 'return ({' + block.trim() + '})')(...Object.values(dependencies))[name];
}
function vector(x = 0, y = 0, z = 0) {
    return {x,y,z,set(x,y,z){Object.assign(this,{x,y,z});return this;},copy(v){return this.set(v.x,v.y,v.z);}};
}

test('Hermes activity respects the manual atmosphere, including after its transition', async () => {
    const light = {intensity:0.58,color:{set(){}},distance:3};
    const atmosphere = new RoomAtmosphere({storage:()=>null});
    atmosphere.register(light,'desk');
    atmosphere.setMode('night',{immediate:true});
    const room = {getObject:()=>light,setLightIntensity:(l,v)=>atmosphere.setBaseIntensity(l,v)};
    const apply = await method('hermes_activity.js','_applyMonitorMaterials',{STATE_META:{idle:{glow:1},thinking:{glow:2}}});
    const state = {_monitorMeshes:[],room,state:'thinking'};
    apply.call(state);
    assert.equal(light.intensity,1.28*0.3);
    state.state='idle';apply.call(state);
    assert.equal(light.intensity,0.72*0.3);
    atmosphere.setMode('original',{immediate:true});
    assert.equal(light.intensity,0.72);
    // Older/mock rooms remain supported.
    delete room.setLightIntensity;state.state='thinking';apply.call(state);
    assert.equal(light.intensity,1.28);
});

test('leaving Inspector cancels the pending trip before recentering the avatar', async () => {
    const toggle = await method('app.js','_toggleInspectorMode',{document:{getElementById:()=>null}});
    const events=[];
    const state={inspectorMode:true,camera:{position:vector(),lookAt(){},updateProjectionMatrix(){}},
        sceneActions:{hotspotTransition:{},reset(){this.hotspotTransition=null;events.push('reset');}},
        idle:{resetRoomPlacement(options){assert.equal(state.sceneActions.hotspotTransition,null);events.push(options);}},
        speechAnimator:{_talking:true},
        _setInspectPresetHudVisible(){},_setActiveInspectPresetButton(){},_setActiveRoomActionButton(){}};
    toggle.call(state);
    assert.deepEqual(events,['reset',{paused:true}]);
    assert.equal(state.inspectorMode,false);
    assert.equal(state.inspectPresetTransition,null);
});

test('recentring a seated pose restores its standing height without replaying its return', async () => {
    const reset = await method('idle_system.js','resetRoomPlacement');
    const group={position:vector(-3,-0.5,1),rotation:vector(0.1,2,0.2)};
    const calls=[];
    const state={avatar:{avatar:{group,restYaw:Math.PI}},currentPose:'pc_seated_idle',
        _savedPosition:vector(-3,0.035,1),_savedRotation:vector(),_returnFromPos:vector(),_returnFromRot:vector(),
        _returningToIdle:true,_manualPoseActive:true,_vrmaExternalClip:'pc_seated_idle',_animationTransitionRemaining:0,
        _snapshotBonesForReturn(){calls.push('bones');},_detachVrmaFinishedHandler(){calls.push('detach');},
        _returnToIdleClipAfterGesture(){this._vrmaExternalClip=null;calls.push('idle');},
        _setAnimationBlinkSuppressed(v){calls.push(v);},resume(){this.paused=false;}};
    assert.equal(reset.call(state,{paused:true}),true);
    assert.deepEqual([group.position.x,group.position.y,group.position.z],[0,0.035,0]);
    assert.deepEqual([group.rotation.x,group.rotation.y,group.rotation.z],[0,Math.PI,0]);
    assert.equal(state.currentPose,null);assert.equal(state._returningToIdle,false);
    assert.equal(state._manualPoseActive,false);assert.equal(state._vrmaExternalClip,null);
    assert.equal(state.paused,true);assert.ok(calls.includes('detach'));assert.ok(calls.includes('bones'));
    for (const axis of ['x','y','z']) assert.equal(state._savedPosition[axis],group.position[axis]);
    assert.equal(reset.call({avatar:null}),false);
});

test('a full Room reset clears movement, delayed callbacks and walk bounce exactly once', async () => {
    const cleared=[];
    const reset=await method('scene_actions.js','reset',{clearTimeout:id=>cleared.push(id)});
    const group={position:vector(1,0.045,2)};
    const state={avatar:{avatar:{group}},actionTimer:42,hotspotTransition:{},isMoving:true,
        _lastBounce:0.01,_walkBlend:0.8,_walkPhase:2,
        _clearAllEffects(){},_deactivateAll(){},_clearActionLookAt(){}};
    reset.call(state,{clearLocomotion:true});
    assert.ok(Math.abs(group.position.y-0.035)<1e-9);
    assert.equal(state.hotspotTransition,null);assert.equal(state.isMoving,false);
    assert.equal(state._walkBlend,0);assert.equal(state._walkPhase,0);assert.equal(state._lastBounce,0);
    assert.equal(state.actionTimer,null);assert.deepEqual(cleared,[42]);
    reset.call(state,{clearLocomotion:true});assert.deepEqual(cleared,[42]);
    assert.ok(Math.abs(group.position.y-0.035)<1e-9);
});
