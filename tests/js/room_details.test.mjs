import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { ROOM_DETAIL_PRESETS, frameRoomPreset } from '../../face/js/room_framing.js';

// CPU contracts run before npm ci in CI. Real Three.js geometry is exercised
// separately by the browser fixture; these stubs only capture resource inputs.
const source = await readFile(new URL('../../face/js/room_surface_details.js', import.meta.url), 'utf8');
const stub = `
const THREE = {
 DataTexture: class { constructor(data,width,height){this.image={data,width,height};this.repeat={set(x,y){this.x=x;this.y=y;}};} },
 SRGBColorSpace:'srgb',RepeatWrapping:'repeat',LinearFilter:'linear',LinearMipmapLinearFilter:'mipmap'
};
class RoundedBoxGeometry { constructor(...args){this.args=args;} }
`;
const details = await import('data:text/javascript;base64,' + Buffer.from(
    stub + source.replace(/^import .*;$/gm, ''),
).toString('base64'));

test('soft furniture reuses geometry and bounds the bevel on thin cloth', () => {
    const a = details.softBox(0.8, 0.02, 0.5);
    assert.equal(a, details.softBox(0.8, 0.02, 0.5));
    assert.ok(a.args[4] <= 0.01);
    assert.equal(a.args[3], 2);
});

test('fabric uses neutral albedo and small linear relief, not another theme tint', () => {
    const {map,bumpMap} = details.createFabricTextures();
    assert.equal(map.image.width,128); assert.equal(bumpMap.image.width,128);
    assert.equal(map.colorSpace,'srgb'); assert.equal(bumpMap.colorSpace,undefined);
    assert.notEqual(map,bumpMap);
    const bytes=map.image.data;
    for(let i=0;i<bytes.length;i+=4){
        assert.equal(bytes[i],bytes[i+1]);assert.equal(bytes[i],bytes[i+2]);
        assert.ok(bytes[i]>=220 && bytes[i]<=242);assert.equal(bytes[i+3],255);
    }
    assert.deepEqual(bytes,details.createFabricTextures().map.image.data);
});

test('contact shadows have a dark center and fully transparent perimeter', () => {
    const {image}=details.createContactShadowTexture();
    const alpha=(x,y)=>image.data[(y*64+x)*4+3];
    assert.equal(image.width,64);assert.ok(alpha(32,32)>250);
    for(let i=0;i<64;i++)for(const [x,y]of [[i,0],[i,63],[0,i],[63,i]])assert.equal(alpha(x,y),0);
    assert.ok(alpha(32,32)>alpha(48,32));assert.ok(alpha(48,32)>alpha(60,32));
});

test('seam shading ramps from clear at the bottom row to opaque at the seam row', () => {
    const {image}=details.createEdgeShadowTexture();
    const alpha=(x,y)=>image.data[(y*image.width+x)*4+3];
    assert.equal(image.height,64);
    for(let x=0;x<image.width;x++){assert.equal(alpha(x,0),0);assert.equal(alpha(x,63),255);}
    for(let y=1;y<64;y++)assert.ok(alpha(0,y)>=alpha(0,y-1));
    assert.ok(alpha(0,32)<128);
});

test('detail views target the furniture, not the empty space above it', () => {
    assert.ok(ROOM_DETAIL_PRESETS.sofaView.target[1]<0);
    assert.ok(ROOM_DETAIL_PRESETS.bedView.target[1]<0);
    assert.ok(ROOM_DETAIL_PRESETS.sofaView.position[2]>ROOM_DETAIL_PRESETS.sofaView.target[2]);
});

test('portrait framing preserves target and FOV without mutating saved presets', () => {
    for(const p of Object.values(ROOM_DETAIL_PRESETS)){
        const before=JSON.stringify(p),wide=frameRoomPreset(p,1.5),narrow=frameRoomPreset(p,390/844);
        assert.deepEqual(wide.position,p.position);assert.deepEqual(narrow.target,p.target);
        assert.equal(narrow.fov,p.fov);assert.ok(narrow.position[2]>wide.position[2]);
        narrow.target[0]=999;assert.equal(JSON.stringify(p),before);
    }
});

test('invalid viewport sizes produce finite, bounded framing', () => {
    for(const aspect of [0,-1,NaN,Infinity,0.01]){
        const p=frameRoomPreset(ROOM_DETAIL_PRESETS.sofaView,aspect);
        assert.ok(p.position.every(Number.isFinite));assert.ok(Math.abs(p.position[2])<40);
    }
});

test('resize reframes a selected inspection preset but never overrides a manual orbit', async () => {
    const app = await readFile(new URL('../../face/js/app.js', import.meta.url), 'utf8');
    const body = app.match(/_onResize\(\) \{([\s\S]*?)\n    \}\n\n    _animate/)[1];
    const calls=[];
    let resized=0;
    const state={camera:{updateProjectionMatrix(){}},graphicsQuality:{resize(){resized++;}},inspectorMode:true,_framedRoomPreset:'sofaView',transitionToInspectPreset(...args){calls.push(args);}};
    const resize = new Function('window',body);
    resize.call(state,{innerWidth:390,innerHeight:844});
    assert.deepEqual(calls,[['sofaView',{immediate:true}]]);
    assert.equal(state.camera.aspect,390/844);
    state._framedRoomPreset=null;resize.call(state,{innerWidth:1440,innerHeight:960});
    state._framedRoomPreset='bedView';state.inspectorMode=false;resize.call(state,{innerWidth:1440,innerHeight:960});
    assert.equal(calls.length,1);
    assert.equal(resized,3,'each resize updates the current quality budget');
});
