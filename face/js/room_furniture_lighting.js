/** A narrow, view-dependent edge light on existing furniture materials.
 * No extra meshes, lights, textures or render passes. RoomAtmosphere owns weight.
 */
export function addFurnitureRim(material, color, intensity = 0.12) {
    if (material.userData.furnitureRim) return material.userData.furnitureRim;
    const rim = {
        color: { value: color },
        intensity: { value: intensity },
        weight: { value: 1 },
    };
    material.userData.furnitureRim = rim;
    const compile = material.onBeforeCompile;
    const key = material.customProgramCacheKey();
    material.onBeforeCompile = function (shader, renderer) {
        compile.call(this, shader, renderer);
        shader.uniforms.irisFurnitureRimColor = rim.color;
        shader.uniforms.irisFurnitureRimIntensity = rim.intensity;
        shader.uniforms.irisFurnitureRimWeight = rim.weight;
        shader.fragmentShader = shader.fragmentShader.replace('#include <common>', `#include <common>
uniform vec3 irisFurnitureRimColor;
uniform float irisFurnitureRimIntensity;
uniform float irisFurnitureRimWeight;`)
            .replace('#include <emissivemap_fragment>', `#include <emissivemap_fragment>
// The colour is confined to grazing angles, not spread over the leather/linen.
float irisFurnitureFacing = abs(dot(normal, normalize(vViewPosition)));
float irisFurnitureEdge = smoothstep(0.78, 0.98, 1.0 - irisFurnitureFacing);
totalEmissiveRadiance += irisFurnitureRimColor * irisFurnitureRimIntensity
    * irisFurnitureRimWeight * irisFurnitureEdge;`);
    };
    material.customProgramCacheKey = () => `${key}|iris-furniture-rim-v1`;
    material.needsUpdate = true;
    return rim;
}
