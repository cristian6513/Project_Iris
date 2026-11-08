/**
 * KTX2 textures for the page: GLB and VRM loaders (KHR_texture_basisu) and the room's own
 * pictures. One decoder serves them all, and nothing without KTX2 ever starts it. Its
 * transcoder ships in every build at /face/vendor/basis/ (scripts/basis_assets.mjs).
 */
import { KTX2Loader } from 'three/addons/loaders/KTX2Loader.js';

export const BASIS_TRANSCODER_PATH = '/face/vendor/basis/';

let shared = null;

/** The page's KTX2 decoder, once a renderer can pick the GPU format; null before that. */
export function sharedKTX2(renderer) {
    if (!renderer) return null;
    shared ??= new KTX2Loader().setTranscoderPath(BASIS_TRANSCODER_PATH).detectSupport(renderer);
    return shared;
}

/** `gltfLoader`, able to read KTX2 textures when there is a renderer. */
export function useKTX2(gltfLoader, renderer) {
    const ktx2 = sharedKTX2(renderer);
    return ktx2 ? gltfLoader.setKTX2Loader(ktx2) : gltfLoader;
}
