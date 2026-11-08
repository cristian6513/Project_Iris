import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const threeUrl=new URL('../../companion/node_modules/three/build/three.module.js',import.meta.url).href;
const addon=name=>new URL('../../companion/node_modules/three/examples/jsm/loaders/'+name,import.meta.url).href;
const THREE=await import(threeUrl);
const {GLTFLoader}=await import(addon('GLTFLoader.js'));
const source=name=>readFile(new URL('../../face/js/'+name,import.meta.url),'utf8');
const moduleUrl=text=>'data:text/javascript;base64,'+Buffer.from(text).toString('base64');
const bvhUrl=moduleUrl((await source('bvh_retargeter.js')).replace("from './avatar_lifecycle.js'", `from '${new URL('../../face/js/avatar_lifecycle.js', import.meta.url).href}'`)
    .replace("from 'three'",`from '${threeUrl}'`)
    .replace("from 'three/addons/loaders/BVHLoader.js'",`from '${addon('BVHLoader.js')}'`)
    .replace("import { retargetClipToVRMHumanoid } from './fbx_retargeter.js';",''));
const {retargetClipToVRMHumanoid}=await import(moduleUrl((await source('fbx_retargeter.js'))
    .replace("from './avatar_lifecycle.js'", `from '${new URL('../../face/js/avatar_lifecycle.js', import.meta.url).href}'`)
    .replace("from 'three'",`from '${threeUrl}'`)
    .replace("from 'three/addons/loaders/FBXLoader.js'",`from '${addon('FBXLoader.js')}'`)
    .replace("from './bvh_retargeter.js'",`from '${bvhUrl}'`)));
const {VRMAnimationPlayer}=await import(moduleUrl((await source('vrm_animation_loader.js'))
    .replace("from './avatar_lifecycle.js'", `from '${new URL('../../face/js/avatar_lifecycle.js', import.meta.url).href}'`)
    .replace("from 'three'",`from '${threeUrl}'`)
    .replace("from 'three/addons/loaders/GLTFLoader.js'",`from '${addon('GLTFLoader.js')}'`)));

async function cubicClip() {
    // A real glTF CUBICSPLINE sampler: zero tangents around each quaternion.
    // No meshes, images, network requests or browser mocks are involved.
    const end=new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1,0,0),0.8);
    const binary=Buffer.from(new Float32Array([0,1,
        0,0,0,0, 0,0,0,1, 0,0,0,0,
        0,0,0,0, ...end.toArray(), 0,0,0,0]).buffer);
    const document={asset:{version:'2.0'},buffers:[{byteLength:binary.length}],
        bufferViews:[{buffer:0,byteOffset:0,byteLength:8},{buffer:0,byteOffset:8,byteLength:96}],
        accessors:[{bufferView:0,componentType:5126,count:2,type:'SCALAR',min:[0],max:[1]},
            {bufferView:1,componentType:5126,count:6,type:'VEC4'}],
        nodes:[{name:'Hips'}],scenes:[{nodes:[0]}],scene:0,
        animations:[{samplers:[{input:0,output:1,interpolation:'CUBICSPLINE'}],
            channels:[{sampler:0,target:{node:0,path:'rotation'}}]}]};
    const json=JSON.stringify(document), chunk=Buffer.from(json.padEnd(Math.ceil(json.length/4)*4,' '));
    const glb=Buffer.alloc(12+8+chunk.length+8+binary.length);
    glb.writeUInt32LE(0x46546c67,0);glb.writeUInt32LE(2,4);glb.writeUInt32LE(glb.length,8);
    glb.writeUInt32LE(chunk.length,12);glb.writeUInt32LE(0x4e4f534a,16);chunk.copy(glb,20);
    const offset=20+chunk.length;
    glb.writeUInt32LE(binary.length,offset);glb.writeUInt32LE(0x004e4942,offset+4);binary.copy(glb,offset+8);
    const result=await new GLTFLoader().parseAsync(glb.buffer.slice(glb.byteOffset,glb.byteOffset+glb.byteLength),'');
    return result.animations[0];
}

test('GLB retargeting preserves cubic rotations and snapshot fades contain only values',async()=>{
    const sourceClip=await cubicClip();
    const scene=new THREE.Group(),hips=new THREE.Bone();hips.name='Normalized_Hips';scene.add(hips);
    const vrm={scene,humanoid:{getNormalizedBoneNode:name=>name==='hips'?hips:null}};
    const {clip}=retargetClipToVRMHumanoid(sourceClip,vrm,'sit','GLB');
    const player=new VRMAnimationPlayer(vrm);
    player.clips.set('sit',clip);player.actions.set('sit',player.mixer.clipAction(clip));
    player.play('sit',{loop:false,fadeIn:0});player.update(0.4);
    const expected=new THREE.Quaternion().fromArray(sourceClip.tracks[0].createInterpolant().evaluate(0.4));
    assert.ok(hips.quaternion.angleTo(expected)<0.001);
    const before=hips.quaternion.clone().normalize();
    player.play('sit',{loop:true,fadeIn:0.3});player.update(1e-6);
    assert.ok(before.angleTo(hips.quaternion.clone().normalize())<0.001);
    assert.equal(player._transitionAction.getClip().tracks[0].getValueSize(),4);
    assert.equal(player._transitionAction.getClip().tracks[0].createInterpolant.isInterpolantFactoryMethodGLTFCubicSpline,undefined);
    for(let t=0;t<3;t+=1/60){
        player.update(1/60);
        assert.ok(Math.abs(hips.quaternion.length()-1)<1e-5,'cubic tangents cannot become zero quaternions');
    }
    player.stop(0.2);player.update(0.3);
    assert.equal(player.mixer.stats.actions.inUse,0);
});
