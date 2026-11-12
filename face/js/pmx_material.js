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
