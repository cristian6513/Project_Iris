/**
 * Smaller copies of a model's large textures, made right after it loads and before WebGL
 * uploads them (render quality «Ahorro»). Each image is resized once even when several
 * textures or materials share it. The file on disk is never touched.
 */

/** `width` x `height` fitted so its longest side is at most `max`, keeping the aspect. */
export function limitedSize(width, height, max) {
    const longest = Math.max(width, height);
    if (!(max > 0) || !(longest > max)) return [width, height];
    const scale = max / longest;
    return [Math.max(1, Math.round(width * scale)), Math.max(1, Math.round(height * scale))];
}

// Same pixels the GLTFLoader decoded: straight alpha and no colour conversion.
function browserResize(image, width, height) {
    return createImageBitmap(image, { resizeWidth: width, resizeHeight: height, resizeQuality: 'high',
        premultiplyAlpha: 'none', colorSpaceConversion: 'none' });
}

/** Image sources of `root`'s materials, each with the textures that read it. */
function textureSources(root) {
    const sources = new Map();
    const add = (value) => {
        if (!value?.isTexture || !value.source?.data) return;
        if (!sources.has(value.source)) sources.set(value.source, new Set());
        sources.get(value.source).add(value);
    };
    root.traverse((node) => {
        for (const material of [].concat(node.material || [])) {
            Object.values(material).forEach(add);
            // MToon keeps its textures in shader uniforms.
            Object.values(material.uniforms || {}).forEach((uniform) => add(uniform?.value));
        }
    });
    return sources;
}

/**
 * A GPU-compressed texture (KTX2) carries its smaller sizes as mip levels: drop the levels
 * over `max` instead of resizing. Returns whether it changed.
 */
function dropLargeMips(texture, max) {
    const mips = texture.mipmaps;
    let first = 0;
    while (first < mips.length - 1 && Math.max(mips[first].width, mips[first].height) > max) first++;
    if (!first) return false;
    texture.mipmaps = mips.slice(first);
    texture.image = { ...texture.image, width: texture.mipmaps[0].width, height: texture.mipmaps[0].height };
    texture.needsUpdate = true;
    return true;
}

/**
 * Resize every image of `root` whose longest side is over `max` pixels and return how many
 * changed. `max` 0 leaves the model as it is. An image that cannot be resized stays full size.
 */
export async function limitTextures(root, max, { resize = browserResize } = {}) {
    if (!(max > 0) || !root) return 0;
    let changed = 0;
    for (const [source, textures] of textureSources(root)) {
        const compressed = [...textures].filter((texture) => texture.isCompressedTexture && texture.mipmaps?.length);
        if (compressed.length) {
            for (const texture of compressed) if (dropLargeMips(texture, max)) changed++;
            continue;
        }
        const image = source.data;
        const [width, height] = limitedSize(image.width, image.height, max);
        if (width === image.width && height === image.height) continue;
        try {
            source.data = await resize(image, width, height);
        } catch (error) {
            console.warn('[textures] Kept a texture at full size:', error);
            continue;
        }
        image.close?.();
        for (const texture of textures) texture.needsUpdate = true;
        changed++;
    }
    return changed;
}
