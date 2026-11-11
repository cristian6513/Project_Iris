// PMX materials: MMD's ambient goes back through the colour texture instead of a flat glow.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const threeUrl = new URL('../../companion/node_modules/three/build/three.module.js', import.meta.url).href;
const THREE = await import(threeUrl);
const source = readFileSync(new URL('../../face/js/pmx_material.js', import.meta.url), 'utf8')
    .replace("from 'three'", `from '${threeUrl}'`);
const { PMX_AMBIENT_SHARE, useMmdAmbient } = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));

const linear = value => new THREE.Color().setRGB(value, value, value, THREE.SRGBColorSpace).r;

test('a textured material lights its ambient through its texture, at a quarter, so dark cloth stays dark', () => {
    const map = new THREE.Texture();
    // As MMDLoader leaves it: ambient × 0.2 as a flat glow.
    const material = new THREE.MeshPhongMaterial({ map, emissive: new THREE.Color().setRGB(0.6, 0.6, 0.6, THREE.SRGBColorSpace).multiplyScalar(0.2) });
    const version = material.version;
    useMmdAmbient([material], [{ ambient: [0.6, 0.6, 0.6] }]);
    assert.equal(PMX_AMBIENT_SHARE, 0.25);
    assert.equal(material.emissiveMap, map, 'the glow is multiplied by the colour texture');
    assert.ok(Math.abs(material.emissive.r - linear(0.6) * 0.25) < 1e-6);
    assert.ok(material.version > version, 'the shader is rebuilt with the emissive map');
});

test('an untextured material keeps a flat ambient, at the same quarter', () => {
    const material = new THREE.MeshPhongMaterial({ emissive: 0xffffff });
    useMmdAmbient(material, [{ ambient: [0.4, 0.2, 0.1] }]);
    assert.equal(material.emissiveMap, null);
    const expected = new THREE.Color().setRGB(0.4, 0.2, 0.1, THREE.SRGBColorSpace).multiplyScalar(0.25);
    assert.ok(material.emissive.equals(expected));
});

test('each material takes the ambient of the PMX material in its place; anything else is left alone', () => {
    const first = new THREE.MeshPhongMaterial({ map: new THREE.Texture() });
    const second = new THREE.MeshPhongMaterial();
    const third = new THREE.MeshBasicMaterial();
    const untouched = new THREE.MeshPhongMaterial({ emissive: 0x123456 });
    useMmdAmbient([first, second, third, untouched], [{ ambient: [1, 1, 1] }, { ambient: [0, 0, 0] }, { ambient: [1, 1, 1] }, { ambient: 'x' }]);
    assert.ok(Math.abs(first.emissive.r - 0.25) < 1e-6);
    assert.equal(second.emissive.getHex(), 0x000000);
    assert.equal(third.emissive, undefined, 'no emissive, nothing to change');
    assert.equal(untouched.emissive.getHex(), 0x123456);
    assert.doesNotThrow(() => useMmdAmbient([first], undefined));
});

test('the loader applies it once, right after building the mesh, with the parsed materials', () => {
    const loader = readFileSync(new URL('../../face/js/pmx_avatar.js', import.meta.url), 'utf8');
    assert.match(loader, /mesh = loader\.meshBuilder\.build\(.*\);\n\s*\/\/[^\n]*\n\s*useMmdAmbient\(mesh\.material, data\.materials\);/);
    assert.equal(loader.match(/useMmdAmbient\(/g).length, 1);
});
