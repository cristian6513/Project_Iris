/**
 * IRIS FBX Retargeter
 *
 * Loads an `.fbx` file with three.js' FBXLoader, picks the first
 * embedded AnimationClip, and rewrites its quaternion tracks so they
 * target the current VRM's normalized humanoid bones.
 *
 * Why FBX:
 *    FBX is THE de-facto animation interchange format. Mixamo alone
 *    ships ~2,600 free humanoid animations as FBX, no Blender required.
 *    Unity Asset Store "free" packs almost always include the .fbx
 *    sources alongside their .anim files. Rokoko, Adobe Mocap and
 *    Truebones all export FBX. This single parser unlocks most of the
 *    free mocap corpus on the web.
 *
 * Relationship to bvh_retargeter.js:
 *    The bone-mapping concern is identical — "given a bone name, what
 *    VRM humanoid slot does it belong to?" — so we import
 *    `bvhBoneToVRMHumanoid` from there instead of duplicating the
 *    table. Author prefixes like `mixamorig:` are already normalized
 *    away by that function, so Mixamo FBX lands cleanly on VRM bones.
 *
 * Known limitations:
 *    • Assumes the FBX rest pose is T-pose (true for Mixamo). A-pose
 *      sources produce arm offsets — try a different animation or
 *      rotate shoulders manually in the source tool.
 *    • Finger, eye and jaw curves are skipped for now — most gesture
 *      packs don't animate fingers, and procedural fallbacks elsewhere
 *      in IRIS handle idle finger poses anyway.
 *    • Root translation (hip displacement) is dropped. FBX mixes m/cm
 *      inconsistently and most usable clips are stationary anyway.
 *    • If an FBX contains multiple AnimationClips we take the first
 *      and log the rest. Mixamo exports one clip per file, so this is
 *      the common case.
 */

import { disposeAvatarTree } from './avatar_lifecycle.js';
import * as THREE from 'three';
import { FBXLoader } from 'three/addons/loaders/FBXLoader.js';
import { bvhBoneToVRMHumanoid } from './bvh_retargeter.js';


/**
 * Retarget a source AnimationClip onto a VRM's humanoid skeleton.
 *
 * Extracted from ``loadAndRetargetFBX`` so the GLB / GLTF animation
 * loader (``glb_retargeter.js``) can reuse the same bone mapping logic
 * without duplicating it. The source clip's rest pose is assumed to be
 * T-pose; track names follow the ``<BoneName>.<property>`` convention
 * that GLTFLoader, FBXLoader and BVHLoader all produce.
 *
 * Drops translation/scale tracks (only humanoid rotations retarget
 * cleanly across rigs) and finger/jaw/eye tracks (most clips don't
 * animate them, and procedural fallbacks handle those bones already).
 *
 * @param {THREE.AnimationClip} sourceClip
 * @param {object} vrm        - @pixiv/three-vrm VRM with ``humanoid``
 * @param {string} clipName   - Name to assign to the resulting clip
 * @param {string} sourceLabel - For log messages (e.g. "FBX" / "GLB")
 * @returns {{clip, mapped, skipped}|null}
 */
export function retargetClipToVRMHumanoid(sourceClip, vrm, clipName, sourceLabel = 'CLIP') {
    if (!vrm?.humanoid) {
        console.warn(`[${sourceLabel}] cannot retarget — no VRM humanoid available`);
        return null;
    }
    if (!Array.isArray(sourceClip?.tracks) || sourceClip.tracks.length === 0) {
        console.warn(`[${sourceLabel}] source clip has no tracks`);
        return null;
    }

    const newTracks = [];
    const skipped = [];
    let mapped = 0;

    for (const track of sourceClip.tracks) {
        const match = /^(.+?)\.(quaternion|position|scale)$/.exec(track.name);
        if (!match) continue;
        const [, rawBoneName, property] = match;

        if (property !== 'quaternion') {
            // Translation / scale dropped on purpose — they can't be
            // applied meaningfully across rigs without proper IK.
            continue;
        }

        const humanoidKey = bvhBoneToVRMHumanoid(rawBoneName);
        if (!humanoidKey) {
            skipped.push(rawBoneName);
            continue;
        }

        const node =
            (vrm.humanoid.getNormalizedBoneNode?.(humanoidKey)) ||
            (vrm.humanoid.getRawBoneNode?.(humanoidKey)) ||
            null;
        if (!node || !node.name) {
            skipped.push(rawBoneName);
            continue;
        }

        const newTrack = track.clone();
        newTrack.name = `${node.name}.quaternion`;
        // glTF CUBICSPLINE stores [in tangent, value, out tangent] per key.
        // Rebuilding it as a linear track treated tangents as rotations,
        // including zero quaternions. Preserve the loader's sampler too:
        // KeyframeTrack.clone() does not copy custom interpolant factories.
        newTrack.createInterpolant = track.createInterpolant;
        newTracks.push(newTrack);
        mapped += 1;
    }

    if (newTracks.length === 0) {
        // Friendlier diagnostic — almost all real-world failures here are
        // one of three categories. Print the first few unmapped names so
        // the user can paste them into a bug report / fix the export.
        const sample = skipped.slice(0, 6).join(', ');
        console.warn(
            `[${sourceLabel}] no humanoid bones matched. ` +
            `Verifica que el archivo de animación tenga un esqueleto humanoide ` +
            `(Mixamo, VRoid, Blender humanoid rig…). ` +
            (sample ? `Huesos no reconocidos: ${sample}${skipped.length > 6 ? '…' : ''}` : '') +
            `\nSi son nombres no estándar, podés añadirlos a BVH_TO_VRM en bvh_retargeter.js.`,
        );
        return null;
    }

    const clip = new THREE.AnimationClip(
        clipName,
        sourceClip.duration,
        newTracks,
    );

    console.log(
        `[${sourceLabel}] retargeted '${clipName}' (${clip.duration.toFixed(2)}s) — ` +
        `${mapped} bone(s) mapped` +
        (skipped.length ? `, ${skipped.length} skipped: ${skipped.slice(0, 5).join(', ')}${skipped.length > 5 ? '…' : ''}` : ''),
    );

    return { clip, mapped, skipped };
}


/**
 * Load an `.fbx` file and retarget its animation onto `vrm`.
 *
 * @param {string} url       - URL pointing at the `.fbx` file
 * @param {object} vrm       - @pixiv/three-vrm VRM instance (must have `.humanoid`)
 * @param {string} clipName  - Name to assign to the resulting AnimationClip
 * @returns {Promise<{clip: THREE.AnimationClip, mapped: number, skipped: string[]}|null>}
 *          Resolves with the retargeted clip plus diagnostics, or `null`
 *          when the file can't be fetched / parsed / retargeted.
 */
export async function loadAndRetargetFBX(url, vrm, clipName) {
    if (!vrm || !vrm.humanoid) {
        console.warn('[FBX] cannot retarget — no VRM humanoid available');
        return null;
    }

    // ── 1. Fetch + parse ──────────────────────────────────────────
    const loader = new FBXLoader();
    let fbx;
    try {
        fbx = await new Promise((resolve, reject) => {
            loader.load(url, resolve, undefined, reject);
        });
    } catch (err) {
        if (err?.target?.status === 404) {
            console.info(`[FBX] '${clipName}' not found at ${url} (will use procedural fallback)`);
        } else {
            console.warn(`[FBX] failed to load ${url}:`, err);
        }
        return null;
    }

    try {
        const anims = Array.isArray(fbx?.animations) ? fbx.animations : [];
        if (anims.length === 0) {
            console.warn(
                `[FBX] ${url}: file parsed but contains no animations. ` +
                `This usually means the FBX was exported as "mesh only" — ` +
                `re-export from the source tool with animation enabled.`,
            );
            return null;
        }
        if (anims.length > 1) {
            console.info(
                `[FBX] ${url}: ${anims.length} clips found, using the first ` +
                `('${anims[0].name || 'unnamed'}'). ` +
                `Split into separate files if you need the others.`,
            );
        }
        const sourceClip = anims[0];
        if (!Array.isArray(sourceClip.tracks) || sourceClip.tracks.length === 0) {
            console.warn(`[FBX] ${url}: first clip has no tracks`);
            return null;
        }

        // Rebuild quaternion tracks targeting VRM humanoid nodes via the
        // shared retargeter. Same logic is used by glb_retargeter.js so
        // FBX, GLB and any future humanoid-clip format produce identical
        // bone mappings.
        return retargetClipToVRMHumanoid(sourceClip, vrm, clipName, 'FBX');
    } finally {
        disposeAvatarTree(fbx, { preserveRoots: [vrm.scene] });
    }
}

