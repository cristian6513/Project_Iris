import assert from 'node:assert/strict';
import test from 'node:test';
import * as THREE from '../../companion/node_modules/three/build/three.module.js';
import { limitedSize, limitTextures } from '../../face/js/texture_limit.js';

const image = (width, height) => ({ width, height, closed: false, close() { this.closed = true; } });
const resizer = () => {
    const calls = [];
    const resize = async (source, width, height) => { calls.push([source, width, height]); return image(width, height); };
    return { calls, resize };
};

test('the longest side is fitted to the limit and the aspect kept', () => {
    assert.deepEqual(limitedSize(2048, 2048, 1024), [1024, 1024]);
    assert.deepEqual(limitedSize(2048, 1024, 1024), [1024, 512]);
    assert.deepEqual(limitedSize(1365, 2048, 1024), [683, 1024]);
    assert.deepEqual(limitedSize(1024, 1024, 1024), [1024, 1024]);
    assert.deepEqual(limitedSize(512, 300, 1024), [512, 300]);
    for (const off of [0, -1, NaN, undefined]) assert.deepEqual(limitedSize(2048, 2048, off), [2048, 2048]);
});

test('large images shrink once, shared ones included, and the originals are released', async () => {
    const big = image(2048, 2048), small = image(512, 512), wide = image(4096, 1024);
    const color = new THREE.Texture(big), emissive = new THREE.Texture(), normal = new THREE.Texture(small);
    emissive.source = color.source;
    const shade = new THREE.Texture(wide);
    const standard = new THREE.MeshStandardMaterial({ map: color, emissiveMap: emissive, normalMap: normal });
    // MToon-style: the texture only lives in a uniform.
    const toon = new THREE.ShaderMaterial({ uniforms: { shadeMultiplyTexture: { value: shade } } });
    const root = new THREE.Group();
    root.add(new THREE.Mesh(new THREE.BufferGeometry(), standard), new THREE.Mesh(new THREE.BufferGeometry(), [toon, standard]));
    const versions = [color.version, emissive.version, shade.version];
    const { calls, resize } = resizer();

    assert.equal(await limitTextures(root, 1024, { resize }), 2);
    assert.deepEqual(calls.map(([, w, h]) => [w, h]), [[1024, 1024], [1024, 256]]);
    assert.deepEqual([color.image.width, emissive.image.width, shade.image.width, normal.image.width], [1024, 1024, 1024, 512]);
    assert.equal(color.image, emissive.image, 'a shared source stays shared');
    assert.ok(big.closed && wide.closed && !small.closed);
    assert.ok(color.version > versions[0] && emissive.version > versions[1] && shade.version > versions[2]);
});

test('no limit leaves the model alone and a failed resize keeps that image', async () => {
    const big = image(2048, 2048), other = image(2048, 2048);
    const a = new THREE.Texture(big), b = new THREE.Texture(other);
    const root = new THREE.Mesh(new THREE.BufferGeometry(), new THREE.MeshStandardMaterial({ map: a, emissiveMap: b }));
    const { calls, resize } = resizer();
    assert.equal(await limitTextures(root, 0, { resize }), 0);
    assert.equal(calls.length, 0);

    const warn = console.warn;
    console.warn = () => {};
    try {
        const flaky = async (source, width, height) => {
            if (source === big) throw new Error('decode failed');
            return image(width, height);
        };
        assert.equal(await limitTextures(root, 1024, { resize: flaky }), 1);
    } finally {
        console.warn = warn;
    }
    assert.equal(a.image, big);
    assert.ok(!big.closed);
    assert.equal(b.image.width, 1024);
});

test('a GPU-compressed texture drops its mip levels over the limit instead of being resized', async () => {
    const mips = [2048, 1024, 512, 256].map(size => ({ width: size, height: size, data: new Uint8Array(size) }));
    const big = new THREE.CompressedTexture(mips, 2048, 2048, THREE.RGBA_BPTC_Format);
    const small = new THREE.CompressedTexture(mips.slice(2), 512, 512, THREE.RGBA_BPTC_Format);
    const root = new THREE.Mesh(new THREE.BufferGeometry(), new THREE.MeshStandardMaterial({ map: big, emissiveMap: small }));
    const version = big.version;
    const { calls, resize } = resizer();
    assert.equal(await limitTextures(root, 1024, { resize }), 1);
    assert.equal(calls.length, 0, 'nothing is decoded or re-encoded');
    assert.deepEqual(big.mipmaps.map(m => m.width), [1024, 512, 256]);
    assert.deepEqual([big.image.width, big.image.height], [1024, 1024]);
    assert.ok(big.version > version);
    assert.deepEqual(small.mipmaps.map(m => m.width), [512, 256]);
});
