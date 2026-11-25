import * as THREE from 'three';

/**
 * How a PMX model's materials take the room's light.
 *
 * MMD lights a material with its ambient colour times its texture, plus the light on its diffuse
 * colour. Three.js' MMDLoader keeps the ambient as a flat glow instead (`emissive`, at 0.2 when
 * there is a texture), which no texture darkens: in the room's dim light black cloth turned grey
 * and every colour paled. Here the glow goes back through the colour texture (`emissiveMap`), as
 * in MMD, at a quarter of the ambient: the room's own ambient lights already light the model, and
 * an MMD scene has no other ambient light. The model's toon ramps are left as it chose them.
 */
export const PMX_AMBIENT_SHARE = 0.25;

/** Applies it once to a freshly built PMX mesh: `materials` in the order of the parsed PMX's. */
export function useMmdAmbient(materials, sources, share = PMX_AMBIENT_SHARE) {
    [materials].flat().forEach((material, index) => {
        const ambient = sources?.[index]?.ambient;
        if (!material?.emissive || !Array.isArray(ambient) || ambient.length < 3) return;
        material.emissive.setRGB(ambient[0], ambient[1], ambient[2], THREE.SRGBColorSpace).multiplyScalar(share);
        material.emissiveMap = material.map ?? null;
        material.needsUpdate = true;
    });
}

/**
 * Expression overlays (blush, tears, the ！ and ？ marks: pmx_expressions.js) are small pieces with
 * see-through textures. Three.js' MMDLoader decides a material's transparency from a few texture
 * samples per triangle and can miss it: Feixue's marks came out as dark boxes (2026-11-24). For the
 * materials of the triangles those overlays move, the texture is sampled more closely (corners,
 * edge midpoints and centre of each triangle); when at least a quarter is see-through, the
 * material blends, writes no depth, and drops fully clear pixels. Skin and clothes are not looked at.
 */
export const OVERLAY_CLEAR_SHARE = 0.25;
const SAMPLE_WEIGHTS = [[1, 0, 0], [0, 1, 0], [0, 0, 1], [0.5, 0.5, 0], [0, 0.5, 0.5], [0.5, 0, 0.5], [1 / 3, 1 / 3, 1 / 3]];
const MAX_TRIANGLES = 400;

/** Material indices whose overlay triangles show mostly through: `alphaAt(materialIndex, u, v)` → 0..255. */
export function overlayMaterials(geometry, overlayVertices, alphaAt) {
    const index = geometry?.index, uv = geometry?.attributes?.uv;
    const found = new Set();
    if (!index || !uv || !overlayVertices?.size) return found;
    for (const group of geometry.groups ?? []) {
        let clear = 0, total = 0, triangles = 0;
        for (let k = group.start; k < group.start + group.count && triangles < MAX_TRIANGLES; k += 3) {
            const corners = [index.getX(k), index.getX(k + 1), index.getX(k + 2)];
            if (!corners.some(vertex => overlayVertices.has(vertex))) continue;
            triangles++;
            for (const [a, b, c] of SAMPLE_WEIGHTS) {
                const u = a * uv.getX(corners[0]) + b * uv.getX(corners[1]) + c * uv.getX(corners[2]);
                const v = a * uv.getY(corners[0]) + b * uv.getY(corners[1]) + c * uv.getY(corners[2]);
                const alpha = alphaAt(group.materialIndex, u, v);
                if (alpha === null || alpha === undefined) continue;
                total++;
                if (alpha < 128) clear++;
            }
        }
        if (total && clear / total >= OVERLAY_CLEAR_SHARE) found.add(group.materialIndex);
    }
    return found;
}

/** A texture's alpha at (u, v), 0..255, or null when its pixels cannot be read. */
function textureReader() {
    const readers = new Map();
    return (texture, u, v) => {
        const image = texture?.image;
        if (!image?.width || !image?.height) return null;
        let read = readers.get(image);
        if (!read) {
            if (image.data && image.data.length === image.width * image.height * 4) {
                read = (x, y) => image.data[(y * image.width + x) * 4 + 3];
            } else {
                const canvas = globalThis.document?.createElement?.('canvas');
                const context = canvas?.getContext?.('2d', { willReadFrequently: true });
                if (!context) return null;
                canvas.width = image.width; canvas.height = image.height;
                try { context.drawImage(image, 0, 0); } catch { return null; }
                read = (x, y) => context.getImageData(x, y, 1, 1).data[3];
            }
            readers.set(image, read);
        }
        const wrap = value => value - Math.floor(value);
        const x = Math.min(image.width - 1, Math.floor(wrap(u) * image.width));
        const y = Math.min(image.height - 1, Math.floor((texture.flipY ? 1 - wrap(v) : wrap(v)) * image.height));
        return read(x, y);
    };
}

/** Applies it once the PMX's textures have loaded. Returns the names of the materials changed. */
export function useOverlayAlpha(mesh, overlayVertices, alphaOf = textureReader()) {
    const materials = [mesh?.material].flat();
    const changed = [];
    const picked = overlayMaterials(mesh?.geometry, overlayVertices, (i, u, v) => {
        const material = materials[i];
        if (!material?.map || material.transparent) return null;
        return alphaOf(material.map, u, v);
    });
    for (const i of picked) {
        const material = materials[i];
        material.transparent = true;
        material.depthWrite = false;
        material.alphaTest = Math.max(material.alphaTest || 0, 0.02);
        material.needsUpdate = true;
        changed.push(material.name || String(i));
    }
    if (changed.length) console.log('[PMX] transparencia en piezas de expresión:', changed.join(', '));
    return changed;
}
