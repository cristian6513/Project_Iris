import test from 'node:test';
import assert from 'node:assert/strict';
import { graphicsQuality, graphicsPolicy, GraphicsQuality, avatarTextureLimit } from '../../face/js/graphics_quality.js';

function harness() {
    const targets = [0, 1].map(() => ({ samples: 2, stencilBuffer: true, disposals: 0, dispose() { this.disposals++; } }));
    const renderer = { capabilities: { maxSamples: 4 }, setPixelRatio(value) { this.ratio=value; },
        setSize(w,h,style) { this.size=[w,h,style]; } };
    const composer = { renderTarget1: targets[0], renderTarget2: targets[1],
        setPixelRatio(ratio) { this.ratio=ratio; }, setSize(w,h) { this.size=[w,h]; } };
    const bloom = { enabled: true }, room = {};
    const h = { renderer, composer, targets, bloom, room, dpr: 2, size: [1000, 600] };
    h.control = new GraphicsQuality({ getRenderer:()=>h.renderer,getComposer:()=>h.composer,
        getBloom:()=>bloom,getRoom:()=>room,getDeviceRatio:()=>h.dpr,getSize:()=>h.size,updateStyle:true });
    return h;
}

test('quality budgets bound resolution and MSAA independently from display density', () => {
    for(const [name,ratio,samples,bloom] of [['saving',1,0,false],['balanced',1.5,2,true],['high',2,4,true]]){
        const p=graphicsPolicy(name,3,8);
        assert.deepEqual([p.pixelRatio,p.samples,p.bloom],[ratio,samples,bloom]);
    }
    assert.equal(graphicsPolicy('saving',1).pixelRatio,.75);
    assert.equal(graphicsPolicy('high',1,2).samples,2);
    assert.equal(graphicsPolicy('high',1,0).samples,0);
    for(const bad of ['ultra',null,'__proto__','constructor',undefined])assert.equal(graphicsQuality(bad),'balanced');
    for(const bad of [NaN,Infinity,-1,0])assert.equal(graphicsPolicy('high',bad).pixelRatio,1);
});

test('saving disables bloom and reduces buffers without replacing renderer, avatar or stencil', () => {
    const h=harness(),identity=h.renderer;
    h.control.setQuality('saving');
    assert.equal(h.renderer,identity);
    assert.deepEqual(h.renderer.size,[1000,600,true]);
    assert.equal(h.renderer.ratio,1);assert.equal(h.composer.ratio,1);
    assert.equal(h.bloom.enabled,false);assert.equal(h.room.quality,'low');
    assert.ok(h.targets.every(t=>t.samples===0&&t.stencilBuffer&&t.disposals===1));
    h.control.setQuality('high');
    assert.equal(h.bloom.enabled,true);assert.equal(h.renderer.ratio,2);
    assert.ok(h.targets.every(t=>t.samples===4&&t.stencilBuffer&&t.disposals===2));
});

test('same-quality resize preserves multisample buffers and respects a new monitor density', () => {
    const h=harness();h.control.setQuality('balanced');
    assert.ok(h.targets.every(t=>t.disposals===0));
    h.dpr=1;h.size=[720,480];h.control.resize();
    assert.equal(h.renderer.ratio,1);assert.deepEqual(h.composer.size,[720,480]);
    assert.ok(h.targets.every(t=>t.disposals===0));
});

test('preferences arriving before WebGL setup are retained and applied when it is ready', () => {
    const h=harness(),r=h.renderer,c=h.composer;h.renderer=h.composer=null;
    assert.equal(h.control.setQuality('saving'),null);
    h.renderer=r;h.composer=c;h.control.resize();
    assert.equal(h.bloom.enabled,false);assert.equal(h.targets[0].samples,0);
});

test('only saving limits avatar textures, and unknown qualities fall back to balanced', () => {
    assert.equal(avatarTextureLimit('saving'),1024);
    assert.equal(avatarTextureLimit('balanced'),0);
    assert.equal(avatarTextureLimit('high'),0);
    for(const bad of ['ultra',null,'__proto__',undefined])assert.equal(avatarTextureLimit(bad),0);
});
