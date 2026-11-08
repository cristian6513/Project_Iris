import test from 'node:test';
import assert from 'node:assert/strict';
import { RoomAtmosphere, ROOM_ATMOSPHERE_KEY, ROOM_ATMOSPHERES } from '../../face/js/room_atmosphere.js';

function setup(saved) {
    const values = new Map(saved === undefined ? [] : [[ROOM_ATMOSPHERE_KEY, saved]]), writes = [];
    const storage = { getItem: key => values.get(key) ?? null, setItem: (key, value) => { writes.push([key,value]); values.set(key,value); } };
    const controller = new RoomAtmosphere({ storage: () => storage, reducedMotion: () => false });
    const lights = {};
    for (const role of ['ambient','desk','lounge','bed','accent','avatar']) {
        lights[role] = { intensity: 2, visible: true, color: 'unchanged' };
        controller.register(lights[role], role);
    }
    return { controller, lights, storage, writes };
}

test('new or invalid preference keeps original lighting without writing storage', () => {
    for (const saved of [undefined,'','auto','__proto__','constructor','night;anything']) {
        const { controller, lights, writes } = setup(saved);
        assert.equal(controller.mode,'original'); assert.equal(writes.length,0);
        assert.ok(Object.values(lights).every(l=>l.intensity===2));
    }
});

test('saved atmosphere applies before first frame, without compounding or changing colors', () => {
    const {controller,lights,writes}=setup('night');
    assert.equal(lights.desk.intensity,0.6);assert.equal(lights.avatar.intensity,2);
    controller.register(lights.desk,'desk');
    for(let i=0;i<4;i++)controller.setMode('night',{immediate:true});
    assert.equal(lights.desk.intensity,0.6);assert.equal(lights.desk.color,'unchanged');
    assert.equal(writes.at(-1)[0],ROOM_ATMOSPHERE_KEY);
    controller.setMode('original',{immediate:true});
    assert.ok(Object.values(lights).every(l=>l.intensity===2));
});

test('manual transitions are bounded and interrupted transitions start from the visible state', () => {
    const {controller,lights}=setup();
    controller.setMode('night');assert.equal(lights.desk.intensity,2);
    controller.update(0.325);assert.ok(Math.abs(lights.desk.intensity-1.3)<1e-9);
    controller.setMode('work');assert.ok(Math.abs(lights.desk.intensity-1.3)<1e-9);
    controller.update(NaN);controller.update(-5);assert.ok(Math.abs(lights.desk.intensity-1.3)<1e-9);
    controller.update(100);assert.equal(lights.desk.intensity,2.9);
    for(const mode of Object.keys(ROOM_ATMOSPHERES)){
        controller.setMode(mode,{immediate:true});assert.equal(lights.avatar.intensity,2);
    }
});

test('scene actions update base intensity and survive switching back to original', () => {
    const {controller,lights}=setup('night');
    controller.setBaseIntensity(lights.accent,1.04);assert.equal(lights.accent.intensity,0.26);
    controller.setMode('work',{immediate:true});assert.equal(lights.accent.intensity,1.04*0.8);
    controller.setMode('original',{immediate:true});assert.equal(lights.accent.intensity,1.04);
    assert.equal(controller.setBaseIntensity(lights.desk,NaN),false);
    assert.equal(controller.setBaseIntensity(lights.desk,-1),false);
    lights.desk.visible=false;controller.setMode('relax',{immediate:true});assert.equal(lights.desk.visible,false);
});

test('reduced motion applies immediately and can stop an in-flight transition', () => {
    let reduced=true;const c=new RoomAtmosphere({storage:()=>null,reducedMotion:()=>reduced}),light={intensity:2};
    c.register(light,'desk');c.setMode('night');assert.equal(light.intensity,0.6);
    reduced=false;c.setMode('original');c.update(0.1);assert.ok(light.intensity<2);
    reduced=true;c.update(0);assert.equal(light.intensity,2);
});

test('blocked storage still changes lighting and reports session-only persistence', () => {
    const c=new RoomAtmosphere({storage:()=>{throw Error('Blocked');},reducedMotion:()=>false}),light={intensity:2};
    c.register(light,'desk');assert.equal(c.mode,'original');
    c.setMode('night',{immediate:true});assert.equal(light.intensity,0.6);assert.equal(c.snapshot().persisted,false);
    assert.equal(c.setMode('__proto__'),false);assert.equal(c.mode,'night');
});

test('subscribers see manual changes, detach cleanly, and cannot modify presets', () => {
    const {controller}=setup();const states=[];const stop=controller.subscribe(s=>states.push(s));
    controller.setMode('relax');assert.equal(states.at(-1).mode,'relax');assert.ok(Object.isFrozen(states[0]));
    stop();controller.setMode('work');assert.equal(states.length,2);
    assert.ok(Object.isFrozen(ROOM_ATMOSPHERES.work.weights));
});

// qa11 user QA (2026-10-13): «el ambiente de luz no veo cambios». Most of the room is unlit, so
// the presets also scale each area's surfaces: unlit colour and emissive glow.
function surfaces(saved) {
    const c = new RoomAtmosphere({ storage: () => (saved ? { getItem: () => saved, setItem() {} } : null), reducedMotion: () => false });
    const led = { isMeshBasicMaterial: true, color: { r: 0.5, g: 1, b: 0.25 } };
    const desk = { emissiveIntensity: 0.4, color: { r: 1, g: 1, b: 1 } };
    const metal = { emissiveIntensity: 0, color: { r: 0.1, g: 0.1, b: 0.1 } };
    return { c, led, desk, metal };
}

test('presets scale unlit colour and glow by area, and Original leaves them untouched', () => {
    const { c, led, desk, metal } = surfaces();
    c.registerSurface(led, 'accent'); c.registerSurface(desk, 'desk'); c.registerSurface(metal, 'desk');
    assert.deepEqual(led.color, { r: 0.5, g: 1, b: 0.25 }); assert.equal(desk.emissiveIntensity, 0.4);
    c.setMode('night', { immediate: true });
    const night = ROOM_ATMOSPHERES.night.surfaces;
    assert.ok(Math.abs(led.color.g - night.accent) < 1e-9); assert.ok(Math.abs(desk.emissiveIntensity - 0.4 * night.desk) < 1e-9);
    assert.equal(metal.emissiveIntensity, 0); assert.equal(metal.color.r, 0.1);
    assert.equal(c.surfaceWeight(desk), night.desk); assert.equal(c.surfaceWeight({}), 1);
    c.setMode('work', { immediate: true }); assert.ok(desk.emissiveIntensity > 0.4);
    c.setMode('original', { immediate: true });
    assert.deepEqual(led.color, { r: 0.5, g: 1, b: 0.25 }); assert.equal(desk.emissiveIntensity, 0.4);
    for (const mode of Object.keys(ROOM_ATMOSPHERES)) assert.equal(ROOM_ATMOSPHERES[mode].surfaces.avatar, 1);
});

test('a saved preset applies to surfaces as they register, a shared material follows the ambient', () => {
    const { c, led, desk } = surfaces('night');
    c.registerSurface(led, 'window'); assert.ok(Math.abs(led.color.r - 0.5 * ROOM_ATMOSPHERES.night.surfaces.window) < 1e-9);
    c.registerSurface(desk, 'desk'); c.registerSurface(desk, 'bed');
    assert.ok(Math.abs(desk.emissiveIntensity - 0.4 * ROOM_ATMOSPHERES.night.surfaces.ambient) < 1e-9);
});

test('a theme change lands on unscaled colours and is scaled again, with no drift', () => {
    const { c, led, desk } = surfaces();
    c.registerSurface(led, 'accent'); c.registerSurface(desk, 'desk');
    c.setMode('night', { immediate: true });
    let seen = null;
    c.rebaseSurfaces(() => { seen = { ...led.color }; led.color.r = 1; led.color.g = 0.2; led.color.b = 0.6; });
    assert.deepEqual(seen, { r: 0.5, g: 1, b: 0.25 }, 'the theme sees the unscaled colour');
    const k = ROOM_ATMOSPHERES.night.surfaces.accent;
    assert.ok(Math.abs(led.color.r - k) < 1e-9 && Math.abs(led.color.g - 0.2 * k) < 1e-9);
    for (let i = 0; i < 3; i++) c.rebaseSurfaces(() => {});
    c.setMode('original', { immediate: true });
    assert.ok(Math.abs(led.color.r - 1) < 1e-9 && Math.abs(led.color.g - 0.2) < 1e-9 && desk.emissiveIntensity === 0.4);
});

test('a glow that arrives later takes its area weight', () => {
    const { c, metal } = surfaces('relax');
    c.registerSurface(metal, 'lounge');
    assert.equal(c.setSurfaceEmissive(metal, 0.2), true);
    assert.ok(Math.abs(metal.emissiveIntensity - 0.2 * ROOM_ATMOSPHERES.relax.surfaces.lounge) < 1e-9);
    assert.equal(c.setSurfaceEmissive(metal, NaN), false);
    const loose = { emissiveIntensity: 0 }; c.setSurfaceEmissive(loose, 0.3); assert.equal(loose.emissiveIntensity, 0.3);
});

test('the room registers its surfaces by area, keeps screens out and rescales after a theme', async () => {
    const { readFileSync } = await import('node:fs');
    const room = readFileSync(new URL('../../face/js/room.js', import.meta.url), 'utf8');
    for (const area of ['DeskArea', 'LoungeArea', 'BedArea', 'WindowCityBackdrop', 'NeonTrim', 'AvatarPlatform']) {
        assert.match(room, new RegExp(`${area}: '`));
    }
    assert.match(room, /this\._registerAtmosphereSurfaces\(\);/);
    assert.match(room, /material\.toneMapped === false\s*\|\| material\.map\?\.isCanvasTexture/);
    assert.match(room, /this\.atmosphere\.rebaseSurfaces\(\(\) => this\._applyTheme\(themeName\)\)/);
    assert.match(room, /\* this\.atmosphere\.surfaceWeight\(child\.material\)/);
    assert.match(room, /this\.atmosphere\.setSurfaceEmissive\(material, opts\.emissiveIntensity\)/);
});
