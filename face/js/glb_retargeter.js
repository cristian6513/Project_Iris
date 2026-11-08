/**
 * IRIS GLB / GLTF Animation Retargeter
 *
 * Loads a `.glb` (or `.gltf`) file as an ANIMATION CLIP source — NOT
 * as an avatar mesh. Picks the first embedded AnimationClip and
 * rewrites its quaternion tracks so they target the current VRM's
 * normalized humanoid bones.
 *
 * Why GLB:
 *    Blender exports `.glb` natively (no FBX add-on needed), Mixamo
 *    sometimes ships GLB variants, and many GitHub mocap repos
 *    distribute GLB because it's the open Khronos format. This loader
 *    treats GLB the same way as FBX: rotation tracks only, T-pose
 *    rest assumed, translation/scale dropped.
 *
 * Distinguish from the avatar GLB path:
 *    `avatar.js` loads `.glb` files as AVATARS (the user's character
 *    geometry) and auto-plays the first embedded clip as the idle
 *    animation. This module is the OPPOSITE: the GLB file contains
 *    animation only, the avatar comes from a separate VRM, and we
 *    retarget the GLB's bones onto the avatar's humanoid skeleton.
 *
 *    The two paths are distinguished by location: GLB files in
 *    `face/animations/` (referenced from `manifest.json`) are treated
 *    as animation sources here; GLB files dropped into the wizard /
 *    avatar uploader are treated as avatars.
 *
 * Bone mapping:
 *    Shares the bone-name table with `bvh_retargeter.js` and
 *    `fbx_retargeter.js` via the exported `retargetClipToVRMHumanoid`
 *    helper. Mixamo's `mixamorig:` prefix, Blender's plain bone names
 *    (Hips/Spine/Head/…), and standard humanoid conventions are all
 *    handled by the shared lookup.
 */

import { disposeAvatarTree } from './avatar_lifecycle.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { retargetClipToVRMHumanoid } from './fbx_retargeter.js?v=3';


/**
 * Load a `.glb` / `.gltf` file and retarget its animation onto `vrm`.
 *
 * @param {string} url       - URL pointing at the `.glb` / `.gltf` file
 * @param {object} vrm       - @pixiv/three-vrm VRM instance (must have `.humanoid`)
 * @param {string} clipName  - Name to assign to the resulting AnimationClip
 * @returns {Promise<{clip: THREE.AnimationClip, mapped: number, skipped: string[]}|null>}
 *          Resolves with the retargeted clip plus diagnostics, or `null`
 *          when the file can't be fetched / parsed / retargeted.
 */
export async function loadAndRetargetGLB(url, vrm, clipName) {
    if (!vrm || !vrm.humanoid) {
        console.warn('[GLB] cannot retarget — no VRM humanoid available');
        return null;
    }

    // ── 1. Fetch + parse ──────────────────────────────────────────
    const loader = new GLTFLoader();
    let gltf;
    try {
        gltf = await new Promise((resolve, reject) => {
            loader.load(url, resolve, undefined, reject);
        });
    } catch (err) {
        if (err?.target?.status === 404) {
            console.info(`[GLB] '${clipName}' not found at ${url} (will use procedural fallback)`);
        } else {
            console.warn(`[GLB] failed to load ${url}:`, err);
        }
        return null;
    }

    try {
        const anims = Array.isArray(gltf?.animations) ? gltf.animations : [];
        if (anims.length === 0) {
            console.warn(
                `[GLB] ${url}: file parsed but contains no animations. ` +
                `If you exported from Blender, make sure the "Animation" ` +
                `option is enabled in the glTF export settings.`,
            );
            return null;
        }
        if (anims.length > 1) {
            console.info(
                `[GLB] ${url}: ${anims.length} clips found, using the first ` +
                `('${anims[0].name || 'unnamed'}'). ` +
                `Split into separate files if you need the others.`,
            );
        }
        const sourceClip = anims[0];

        // ── 2. Retarget via the shared helper ────────────────────────
        return retargetClipToVRMHumanoid(sourceClip, vrm, clipName, 'GLB');
    } finally {
        for (const scene of new Set(gltf.scenes || [gltf.scene])) disposeAvatarTree(scene, { preserveRoots: [vrm.scene] });
    }
}

