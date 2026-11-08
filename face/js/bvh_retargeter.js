/**
 * IRIS BVH Retargeter
 *
 * Loads a `.bvh` motion-capture file and rebuilds its AnimationClip so
 * the tracks target the current VRM's normalized humanoid bones. The
 * result is a plain `THREE.AnimationClip` that `VRMAnimationPlayer` can
 * feed into its existing mixer just like a `.vrma`.
 *
 * Why this exists:
 *    .vrma files are high-quality but rare. .bvh is the de-facto motion-
 *    capture interchange format and there are *huge* free libraries (CMU,
 *    Mixamo export, MMD, Truebones…). Supporting BVH unlocks most of the
 *    "any animation" use case without asking the user to run Blender.
 *
 * Known limitations:
 *    • Assumes the BVH rest pose is close to T-pose. Files authored in
 *      A-pose or with exotic rest poses will have offset errors in arms
 *      and legs. If the motion looks twisted, the source file is the
 *      culprit — try a different BVH.
 *    • Root translation (hip movement across the floor) is dropped for
 *      now: most idle/gesture animations are stationary and BVH unit
 *      conventions (cm vs. m) are inconsistent between sources. Anim
 *      plays with the avatar rooted in place.
 *    • Finger, eye and jaw bones are not in BVH mocap generally, so
 *      those keep whatever pose the VRM had — procedural fallback
 *      elsewhere in IRIS can still drive them.
 */

import { BVHLoader } from 'three/addons/loaders/BVHLoader.js';
import { retargetClipToVRMHumanoid } from './fbx_retargeter.js';


// ── BVH bone name → VRM humanoid bone key ────────────────────────────
//
// BVH has no standard skeleton. Every author names bones differently:
// biovision, CMU, Mixamo ("mixamorig:…"), MMD (Japanese), Truebones, etc.
// We normalize the BVH name (lowercase, strip common prefixes and
// punctuation) and look it up in this table.
//
// Keys on the right are the humanoid bone names @pixiv/three-vrm uses
// (https://github.com/pixiv/three-vrm — see VRMHumanBoneName).
const BVH_TO_VRM = Object.freeze({
    // ── Core ─────────────────────────────────────────────────────
    'hips':          'hips',
    'hip':           'hips',
    'pelvis':        'hips',
    'root':          'hips',
    'hipmaster':     'hips',
    'center':        'hips',        // MMD センター romanized

    'spine':         'spine',
    'torso':         'spine',
    'spine1':        'chest',
    'torso1':        'chest',
    'torso2':        'chest',
    'spine2':        'upperChest',
    'spine3':        'upperChest',
    'chest':         'chest',
    'upperchest':    'upperChest',

    'neck':          'neck',
    'neck1':         'neck',

    'head':          'head',
    'headtop':       'head',

    // ── Arms / left ──────────────────────────────────────────────
    'leftshoulder':  'leftShoulder',
    'leftcollar':    'leftShoulder',
    'lshoulder':     'leftShoulder',

    'leftarm':       'leftUpperArm',
    'leftupperarm':  'leftUpperArm',
    'larm':          'leftUpperArm',

    'leftforearm':   'leftLowerArm',
    'leftlowerarm':  'leftLowerArm',
    'leftelbow':     'leftLowerArm',
    'lforearm':      'leftLowerArm',

    'lefthand':      'leftHand',
    'leftwrist':     'leftHand',
    'lhand':         'leftHand',

    // ── Arms / right ─────────────────────────────────────────────
    'rightshoulder': 'rightShoulder',
    'rightcollar':   'rightShoulder',
    'rshoulder':     'rightShoulder',

    'rightarm':      'rightUpperArm',
    'rightupperarm': 'rightUpperArm',
    'rarm':          'rightUpperArm',

    'rightforearm':  'rightLowerArm',
    'rightlowerarm': 'rightLowerArm',
    'rightelbow':    'rightLowerArm',
    'rforearm':      'rightLowerArm',

    'righthand':     'rightHand',
    'rightwrist':    'rightHand',
    'rhand':         'rightHand',

    // ── Legs / left ──────────────────────────────────────────────
    'leftupleg':     'leftUpperLeg',
    'leftupperleg':  'leftUpperLeg',
    'lefthip':       'leftUpperLeg',
    'lefthipd':      'leftUpperLeg',
    'leftthigh':     'leftUpperLeg',
    'lthigh':        'leftUpperLeg',

    'leftleg':       'leftLowerLeg',
    'leftlowerleg':  'leftLowerLeg',
    'leftknee':      'leftLowerLeg',
    'leftkneed':     'leftLowerLeg',
    'leftshin':      'leftLowerLeg',
    'lshin':         'leftLowerLeg',

    'leftfoot':      'leftFoot',
    'leftfootd':     'leftFoot',
    'leftankle':     'leftFoot',
    'lfoot':         'leftFoot',

    'lefttoe':       'leftToes',
    'lefttoebase':   'leftToes',
    'lefttoes':      'leftToes',
    'ltoe':          'leftToes',

    // ── Legs / right ─────────────────────────────────────────────
    'rightupleg':    'rightUpperLeg',
    'rightupperleg': 'rightUpperLeg',
    'righthip':      'rightUpperLeg',
    'righthipd':     'rightUpperLeg',
    'rightthigh':    'rightUpperLeg',
    'rthigh':        'rightUpperLeg',

    'rightleg':      'rightLowerLeg',
    'rightlowerleg': 'rightLowerLeg',
    'rightknee':     'rightLowerLeg',
    'rightkneed':    'rightLowerLeg',
    'rightshin':     'rightLowerLeg',
    'rshin':         'rightLowerLeg',

    'rightfoot':     'rightFoot',
    'rightfootd':    'rightFoot',
    'rightankle':    'rightFoot',
    'rfoot':         'rightFoot',

    'righttoe':      'rightToes',
    'righttoebase':  'rightToes',
    'righttoes':     'rightToes',
    'rtoe':          'rightToes',
});


/**
 * Normalize a BVH bone name so lookups are robust across naming schemes.
 * Strips common author prefixes (`mixamorig:`, `bip01_`, `armature|`) and
 * anything non-alphanumeric.
 */
function normalizeBoneName(name) {
    return String(name || '')
        .toLowerCase()
        .replace(/^mixamorig[:|_-]?/, '')
        .replace(/^bip\d*[:|_-]?/, '')
        .replace(/^armature[:|_-]?/, '')
        .replace(/^\d+[:.|_-]?/, '') // Blender VRM imports often prefix bone order: "34.joint_RightArm"
        .replace(/^joint[:|_-]?/, '') // Carlotta-style VRM0 source bones
        .replace(/_end$/, '')   // BVHLoader appends "_End" to terminal bones
        .replace(/[^a-z0-9]/g, '');
}


/**
 * Map a BVH bone name to a VRM humanoid key, or `null` if unknown.
 * Exposed for debugging / tests.
 */
export function bvhBoneToVRMHumanoid(bvhName) {
    return BVH_TO_VRM[normalizeBoneName(bvhName)] || null;
}


/**
 * Load a `.bvh` file and retarget its animation onto `vrm`.
 *
 * @param {string} url       - URL pointing at the `.bvh` file
 * @param {object} vrm       - @pixiv/three-vrm VRM instance (must have `.humanoid`)
 * @param {string} clipName  - Name to assign to the resulting AnimationClip
 * @returns {Promise<{clip: THREE.AnimationClip, mapped: number, skipped: string[]}|null>}
 *          Resolves with the retargeted clip plus diagnostics, or `null`
 *          when the file can't be fetched / parsed / retargeted.
 */
export async function loadAndRetargetBVH(url, vrm, clipName) {
    if (!vrm || !vrm.humanoid) {
        console.warn('[BVH] cannot retarget — no VRM humanoid available');
        return null;
    }

    // ── 1. Fetch + parse ──────────────────────────────────────────
    const loader = new BVHLoader();
    let bvh;
    try {
        bvh = await new Promise((resolve, reject) => {
            loader.load(url, resolve, undefined, reject);
        });
    } catch (err) {
        if (err?.target?.status === 404) {
            console.info(`[BVH] '${clipName}' not found at ${url} (will use procedural fallback)`);
        } else {
            console.warn(`[BVH] failed to load ${url}:`, err);
        }
        return null;
    }

    const sourceClip = bvh?.clip;
    if (!sourceClip || !Array.isArray(sourceClip.tracks)) {
        console.warn(`[BVH] ${url} parsed but produced no AnimationClip`);
        return null;
    }

    // ── 2. Delegate to the shared retargeter ──────────────────────
    //
    // BVHLoader emits tracks named "<BoneName>.quaternion" (rotation) and
    // "<BoneName>.position" (only for the root hip, typically). The
    // exact same regex + BVH_TO_VRM lookup lives in
    // ``retargetClipToVRMHumanoid``, so we hand the source clip off to
    // it — same path FBX and GLB use. Keeps a single source of truth
    // for bone-mapping behaviour across all three loaders.
    return retargetClipToVRMHumanoid(sourceClip, vrm, clipName, 'BVH');
}

