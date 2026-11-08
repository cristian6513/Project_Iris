import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { commitAvatarReplacement } from '../../face/js/avatar_lifecycle.js';
const threeUrl=new URL('../../companion/node_modules/three/build/three.module.js',import.meta.url).href;
const THREE=await import(threeUrl);
const src=fs.readFileSync(new URL('../../face/js/micro_life.js',import.meta.url),'utf8').replace("from 'three'",`from '${threeUrl}'`);
const {MicroLife}=await import('data:text/javascript;base64,'+Buffer.from(src).toString('base64'));
function fixture(){
 const bone=new THREE.Bone();bone.name='hips';
 const micro=Object.create(MicroLife.prototype);micro._baseQuats=new Map();
 return {bone,micro};
}
test('slow clip rotations survive the Face presence overlay on every frame',()=>{
 const {bone,micro}=fixture();
 const delta=new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1,0,0),0.002);
 const axis=new THREE.Vector3(0,1,0);
 for(let frame=0;frame<180;frame++){
  micro.beginFrame?.();
  const authored=new THREE.Quaternion().setFromAxisAngle(axis,frame*0.001);
  bone.quaternion.copy(authored);
  micro._applyBoneDelta(bone,bone.name,delta);
  assert.ok(bone.quaternion.angleTo(authored.clone().multiply(delta))<1e-6,`frame ${frame} must not freeze on the previous base`);
 }
});
test('a constant mixer track does not accumulate presence or retain it after the layer yields',()=>{
 const {bone,micro}=fixture();
 const base=new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0,1,0),0.3);
 const delta=new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1,0,0),0.004);
 bone.quaternion.copy(base);
 for(let i=0;i<100;i++){micro.beginFrame?.();micro._applyBoneDelta(bone,bone.name,delta);}
 assert.ok(bone.quaternion.angleTo(base.clone().multiply(delta))<1e-6);
 micro.beginFrame?.();
 assert.ok(bone.quaternion.angleTo(base)<1e-6,'yielding to a clip must remove the previous frame overlay');
});
test('an external pose written between frames is preserved',()=>{
 const {bone,micro}=fixture();
 micro._applyBoneDelta(bone,bone.name,new THREE.Quaternion());
 const newPose=new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0,1,0),0.001);
 bone.quaternion.copy(newPose);micro.beginFrame?.();
 assert.ok(bone.quaternion.angleTo(newPose)<1e-6);
});

test('body-only animation keeps blink running while suspending root breathing',()=>{
 const {micro}=fixture(),calls=[];
 micro.avatarManager={avatar:{animationOwnsBlink:false,animationOwnsRootMotion:true}};
 micro.t={enableBlink:true,enableBreathing:true};
 micro._resumeBlinksAfterAnimation=()=>calls.push('resumeBlink');
 micro._updateBlinks=()=>calls.push('blink');
 micro._suspendBlinksForAnimation=()=>calls.push('suspendBlink');
 micro._suspendBreathingForAnimation=()=>calls.push('suspendBreathing');
 micro._resumeBreathingAfterAnimation=()=>calls.push('resumeBreathing');
 micro._updateBreathing=()=>calls.push('breathing');
 micro.update(1/60,0);
 assert.deepEqual(calls,['resumeBlink','blink','suspendBreathing']);
});

test('rig commit clears old MicroLife bones and look target while preserving its listeners and activity',()=>{
 const scene=new THREE.Scene(), old={avatar:{group:new THREE.Group()}}, next={avatar:{group:new THREE.Group()}};
 const micro=new MicroLife({avatarManager:old,camera:new THREE.PerspectiveCamera()});
 const bone=new THREE.Bone();old.avatar.group.add(bone,micro._lookAtDummy);scene.add(old.avatar.group);
 micro._applyBoneDelta(bone,'hips',new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1,0,0),0.01));
 micro.setListening(true);micro.setSpeaking(true);
 const listener={event:'speech-start',handler:()=>{}};micro._wiredListeners.push(listener);
 micro._blinkL.phase='closing';micro._blinkL.value=0.8;
 const app={scene,avatarManager:old,microLife:micro,speechAnimator:{_talking:true},idle:{dispose(){}}};
 commitAvatarReplacement(app,next,{createIdle:()=>({setActivity(){},params:{}})});
 assert.equal(micro._baseQuats.size,0);assert.equal(micro._frameDeltas?.size||0,0);
 assert.equal(micro._lookAtDummy.parent,null);assert.equal(micro._blinkL.value,0);
 assert.equal(micro._listening,true);assert.equal(micro._speaking,true);
 assert.deepEqual(micro._wiredListeners,[listener]);
 assert.equal(next.avatar.microLifeOwnsBlink,true);assert.equal(next.avatar.microLifeOwnsBreathing,true);
 const previous=bone.quaternion.clone();micro.beginFrame();
 assert.ok(bone.quaternion.angleTo(previous)<1e-7,'retired bones cannot receive another presence write');
});
