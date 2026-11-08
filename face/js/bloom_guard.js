/**
 * One pixel brighter than the render target can hold becomes infinite: a specular peak on a glossy
 * surface is enough. The bloom then blurs it into a black block over the room, some frames long.
 * So what enters the bloom is kept to a brightness it can blur. The image itself is left alone.
 */
const READ = 'vec4 texel = texture2D( tDiffuse, vUv );';
const BRIGHTEST = '16.0';     // well past anything the bloom's threshold cares about

export function guardBloom(pass) {
    const material = pass.materialHighPassFilter;
    if (!material.fragmentShader.includes(READ)) throw Error('The bloom shader changed: review bloom_guard.js');
    material.fragmentShader = material.fragmentShader.replace(READ, `${READ}
            if ( any( isnan( texel ) ) ) texel = vec4( 0.0 );
            texel = clamp( texel, vec4( 0.0 ), vec4( ${BRIGHTEST} ) );`);
    material.needsUpdate = true;
    return pass;
}
