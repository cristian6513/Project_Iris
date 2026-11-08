/** Shared room details; no downloads, dynamic shadows or animation loops. */
import * as THREE from 'three';
import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js';

const softGeometry = new Map();
export function softBox(width, height, depth, radius = 0.06) {
    const bevel = Math.min(radius, Math.min(width, height, depth) * 0.45);
    const key = `${width}:${height}:${depth}:${bevel}`;
    if (!softGeometry.has(key)) {
        softGeometry.set(key, new RoundedBoxGeometry(width, height, depth, 2, bevel));
    }
    return softGeometry.get(key);
}

export function createFabricTextures() {
    const size = 128;
    const color = new Uint8Array(size * size * 4);
    const relief = new Uint8Array(color.length);
    for (let y = 0; y < size; y++) {
        for (let x = 0; x < size; x++) {
            const at = (y * size + x) * 4;
            // Neutral albedo: the theme tint is applied only in material.color.
            const thread = ((x >> 2) + (y >> 2)) % 2 ? x % 4 : y % 4;
            const value = 220 + thread * 6 + ((x * 13 + y * 7) % 5);
            color.set([value, value, value, 255], at);
            const height = 110 + thread * 12;
            relief.set([height, height, height, 255], at);
        }
    }
    const map = new THREE.DataTexture(color, size, size);
    const bumpMap = new THREE.DataTexture(relief, size, size);
    map.colorSpace = THREE.SRGBColorSpace;
    for (const texture of [map, bumpMap]) {
        texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
        texture.repeat.set(3, 3);
        texture.magFilter = THREE.LinearFilter;
        texture.minFilter = THREE.LinearMipmapLinearFilter;
        texture.generateMipmaps = true;
        texture.anisotropy = 4;
        texture.needsUpdate = true;
    }
    return { map, bumpMap };
}

/** Alpha ramp for seam shading: opaque along the top edge (v = 1), clear at v = 0. */
export function createEdgeShadowTexture() {
    const width = 4, height = 64, pixels = new Uint8Array(width * height * 4);
    for (let y = 0; y < height; y++) {
        const alpha = Math.round(255 * Math.pow(y / (height - 1), 2.2));
        for (let x = 0; x < width; x++) pixels[(y * width + x) * 4 + 3] = alpha;
    }
    const texture = new THREE.DataTexture(pixels, width, height);
    texture.minFilter = texture.magFilter = THREE.LinearFilter;
    texture.needsUpdate = true;
    return texture;
}

export function createContactShadowTexture() {
    const size = 64, pixels = new Uint8Array(size * size * 4);
    for (let y = 0; y < size; y++) {
        for (let x = 0; x < size; x++) {
            const dx = (x / (size - 1) - 0.5) * 2;
            const dy = (y / (size - 1) - 0.5) * 2;
            const radius = Math.pow(dx, 4) + Math.pow(dy, 4);
            pixels[(y * size + x) * 4 + 3] = Math.round(255 * Math.pow(Math.max(0, 1 - radius), 3));
        }
    }
    const texture = new THREE.DataTexture(pixels, size, size);
    texture.minFilter = texture.magFilter = THREE.LinearFilter;
    texture.needsUpdate = true;
    return texture;
}
