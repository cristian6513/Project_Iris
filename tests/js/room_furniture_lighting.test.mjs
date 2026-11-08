import test from 'node:test';
import assert from 'node:assert/strict';
import { Color, MeshStandardMaterial, ShaderLib, Texture } from '../../companion/node_modules/three/build/three.module.js';
import { addFurnitureRim } from '../../face/js/room_furniture_lighting.js';
import { RoomAtmosphere, ROOM_ATMOSPHERES } from '../../face/js/room_atmosphere.js';

test('the material keeps its texture and compile hook; all colours share the same rim program', () => {
    const texture = new Texture(), material = new MeshStandardMaterial({ map: texture }), color = new Color(0x8defff);
    let calls = 0;
    material.onBeforeCompile = function (shader) { assert.equal(this, material); calls++; shader.defines = { PREVIOUS_HOOK: 1 }; };
    const rim = addFurnitureRim(material, color);
    const key = material.customProgramCacheKey();
    const shader = { uniforms: {}, fragmentShader: ShaderLib.standard.fragmentShader };
    material.onBeforeCompile(shader, {});
    assert.equal(calls, 1); assert.equal(shader.defines.PREVIOUS_HOOK, 1);
    assert.equal(shader.uniforms.irisFurnitureRimColor.value, color);
    assert.equal(shader.uniforms.irisFurnitureRimWeight, rim.weight);
    assert.ok(shader.fragmentShader.indexOf('float irisFurnitureFacing') > shader.fragmentShader.indexOf('#include <normal_fragment_maps>'));
    assert.ok(shader.fragmentShader.indexOf('float irisFurnitureFacing') < shader.fragmentShader.indexOf('#include <lights_physical_fragment>'));
    color.setHex(0xff5fb7);
    assert.equal(shader.uniforms.irisFurnitureRimColor.value.getHex(), 0xff5fb7);
    assert.equal(material.customProgramCacheKey(), key);
    assert.equal(material.emissiveIntensity, 1); assert.equal(material.map, texture);
    assert.equal(addFurnitureRim(material, new Color(0), 9), rim, 're-registering cannot stack the shader');
    const other = new MeshStandardMaterial();
    // A different hook must not collide with this material's program.
    addFurnitureRim(other, new Color(0));
    assert.notEqual(other.customProgramCacheKey(), key);
});

test('a saved Night preset dims the rim before its first render and restores the exact original', () => {
    const atmosphere = new RoomAtmosphere({ storage: () => ({ getItem: () => 'night', setItem() {} }) });
    for (const role of ['lounge', 'bed']) {
        const material = new MeshStandardMaterial({ emissive: 0xffffff, emissiveIntensity: .1 });
        const rim = addFurnitureRim(material, new Color(0xffffff), .12);
        atmosphere.registerSurface(material, role);
        const shader = { uniforms: {}, fragmentShader: ShaderLib.standard.fragmentShader };
        material.onBeforeCompile(shader, {});
        assert.equal(shader.uniforms.irisFurnitureRimWeight.value, ROOM_ATMOSPHERES.night.surfaces[role]);
        for (const mode of ['original', 'relax', 'work', 'night', 'original', 'night']) {
            atmosphere.setMode(mode, { immediate: true });
            assert.equal(rim.weight.value, ROOM_ATMOSPHERES[mode].surfaces[role]);
            assert.equal(rim.intensity.value, .12, 'the base edge strength never compounds');
            assert.equal(material.emissiveIntensity, .1 * ROOM_ATMOSPHERES[mode].surfaces[role]);
        }
    }
});

test('rim-only materials fade smoothly and a theme change cannot brighten a dimmed area', () => {
    const atmosphere = new RoomAtmosphere({ storage: () => null, reducedMotion: () => false });
    const material = new MeshStandardMaterial({ emissiveIntensity: 0 });
    const rim = addFurnitureRim(material, new Color(0x8defff));
    atmosphere.registerSurface(material, 'lounge');
    atmosphere.setMode('night'); atmosphere.update(.325);
    assert.ok(Math.abs(rim.weight.value - (1 + ROOM_ATMOSPHERES.night.surfaces.lounge) / 2) < 1e-9);
    atmosphere.update(1);
    atmosphere.rebaseSurfaces(() => rim.color.value.setHex(0xff5fb7));
    assert.equal(rim.weight.value, ROOM_ATMOSPHERES.night.surfaces.lounge);
    assert.equal(rim.color.value.getHex(), 0xff5fb7);
    atmosphere.setMode('original', { immediate: true });
    assert.equal(rim.weight.value, 1); assert.equal(material.emissiveIntensity, 0);
});
