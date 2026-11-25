// PMX materials: MMD's ambient goes back through the colour texture instead of a flat glow.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const threeUrl = new URL('../../companion/node_modules/three/build/three.module.js', import.meta.url).href;
const THREE = await import(threeUrl);
const source = readFileSync(new URL('../../face/js/pmx_material.js', import.meta.url), 'utf8')
    .replace("from 'three'", `from '${threeUrl}'`);
const { OVERLAY_CLEAR_SHARE, PMX_AMBIENT_SHARE, overlayMaterials, useMmdAmbient, useOverlayAlpha } = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));

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

// 2026-11-24: Feixue's ！ and ？ marks rendered as dark boxes: MMDLoader had left their material opaque.
function overlayMesh() {
    // Two quads: material 0 (skin, opaque texture) and material 1 (a mark on a see-through atlas).
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(new Array(8 * 3).fill(0), 3));
    geometry.setAttribute('uv', new THREE.Float32BufferAttribute([0, 0, 0.5, 0, 0.5, 0.5, 0, 0.5, 0.5, 0.5, 1, 0.5, 1, 1, 0.5, 1], 2));
    geometry.setIndex([0, 1, 2, 0, 2, 3, 4, 5, 6, 4, 6, 7]);
    geometry.addGroup(0, 6, 0);
    geometry.addGroup(6, 6, 1);
    const skin = new THREE.MeshPhongMaterial({ map: new THREE.Texture(), name: 'Face' });
    const mark = new THREE.MeshPhongMaterial({ map: new THREE.Texture(), name: 'Facial' });
    return { mesh: new THREE.Mesh(geometry, [skin, mark]), skin, mark };
}
// The atlas: clear above u, v = 0.5 except a small opaque symbol in its middle.
const atlasAlpha = (texture, u, v) => (u > 0.5 && v > 0.5 && !(Math.abs(u - 0.75) < 0.05 && Math.abs(v - 0.75) < 0.05) ? 0 : 255);

test('an overlay piece on a see-through texture blends; skin it does not move is never looked at', () => {
    const { mesh, skin, mark } = overlayMesh();
    const changed = useOverlayAlpha(mesh, new Set([4, 5, 6, 7]), atlasAlpha);
    assert.deepEqual(changed, ['Facial']);
    assert.equal(mark.transparent, true);
    assert.equal(mark.depthWrite, false);
    assert.ok(mark.alphaTest > 0 && mark.alphaTest < 0.05, 'fully clear pixels are dropped, soft blush is kept');
    assert.equal(skin.transparent, false);
});

test('an overlay on an opaque texture, or none at all, changes nothing', () => {
    const { mesh, mark } = overlayMesh();
    assert.deepEqual(useOverlayAlpha(mesh, new Set([4, 5, 6, 7]), () => 255), []);
    assert.equal(mark.transparent, false);
    assert.deepEqual(useOverlayAlpha(mesh, new Set(), atlasAlpha), []);
    assert.equal(overlayMaterials(null, new Set([1]), atlasAlpha).size, 0);
    assert.equal(OVERLAY_CLEAR_SHARE, 0.25);
});
