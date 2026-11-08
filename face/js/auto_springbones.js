/**
 * IRIS Auto-SpringBones — runtime cloth-physics detection
 *
 * Many VRMs come with skirt/hair/ribbon bone chains in the skeleton but
 * ship WITHOUT `VRMC_springBone` extension declarations (common when the
 * model was exported from an older tool, re-saved, or manually rigged in
 * Blender without adding the VRM springbone UI). Without declarations the
 * springbone manager has 0 active joints and cloth stays rigidly pinned.
 *
 * This module scans the VRM skeleton after load for bone chains whose
 * names match known cloth patterns and dynamically creates springbone
 * joints for them — giving them natural oscillation without any manual
 * config from the user.
 *
 * It NEVER creates bones that don't exist; if a model has no skirt bones
 * at all (e.g. the ripped `chisa_wuthering_waves.glb` case), this module
 * silently does nothing.
 */

import * as THREE from 'three';

// Name patterns for each cloth category. Matched case-insensitively.
// Order matters: more specific patterns first so they claim bones before
// generic ones.
const CLOTH_PATTERNS = [
    { name: 'skirt',    regex: /skirt|スカート|スカト/i,                 preset: 'skirt'    },
    { name: 'hair',     regex: /^hair|_hair|髪|ponytail|pony_?tail|sidelock|bang/i, preset: 'hair' },
    { name: 'ribbon',   regex: /ribbon|リボン|bow(?!el)/i,              preset: 'light'    },
    { name: 'tie',      regex: /\btie\b|necktie|scarf|collar(?!bone)/i, preset: 'light'    },
    { name: 'jacket',   regex: /jacket|coat|cape|cloak|hem|skirttail/i, preset: 'heavy'    },
    { name: 'tail',     regex: /^tail|_tail|しっぽ|尾/i,                 preset: 'heavy'    },
    { name: 'sleeve',   regex: /sleeve|袖|cuff/i,                       preset: 'medium'   },
    { name: 'chest',    regex: /breast|bust|chest_?phys/i,              preset: 'bounce'   },
    { name: 'accessory',regex: /accessory|charm|tag|strap|belt_?loose/i, preset: 'light'   },
];

// Physical presets keyed by category. Values picked to look natural on
// anime characters at ~1.6m scale.
const PRESETS = {
    hair:    { stiffness: 1.0, dragForce: 0.4, gravityPower: 0.15, gravityDir: new THREE.Vector3(0, -1, 0), hitRadius: 0.02 },
    skirt:   { stiffness: 0.8, dragForce: 0.5, gravityPower: 0.7,  gravityDir: new THREE.Vector3(0, -1, 0), hitRadius: 0.03 },
    heavy:   { stiffness: 1.2, dragForce: 0.6, gravityPower: 0.8,  gravityDir: new THREE.Vector3(0, -1, 0), hitRadius: 0.04 },
    medium:  { stiffness: 1.0, dragForce: 0.5, gravityPower: 0.5,  gravityDir: new THREE.Vector3(0, -1, 0), hitRadius: 0.03 },
    light:   { stiffness: 0.8, dragForce: 0.4, gravityPower: 0.2,  gravityDir: new THREE.Vector3(0, -1, 0), hitRadius: 0.02 },
    bounce:  { stiffness: 1.5, dragForce: 0.7, gravityPower: 0.1,  gravityDir: new THREE.Vector3(0, -1, 0), hitRadius: 0.03 },
};

/**
 * Classify a bone name into a cloth category.
 * @param {string} name
 * @returns {string|null} category key (skirt/hair/...) or null
 */
function classifyBone(name) {
    if (!name) return null;
    for (const p of CLOTH_PATTERNS) {
        if (p.regex.test(name)) return p.preset;
    }
    return null;
}

/**
 * Walk down a linear bone chain (follow the first child recursively).
 * Stops when a bone has 0 children or branches (>1 child).
 * Returns the array including the starting bone.
 */
function collectLinearChain(root) {
    const chain = [root];
    let cursor = root;
    while (cursor.children && cursor.children.length === 1) {
        const child = cursor.children[0];
        if (!child.isBone && !(child.type === 'Bone')) break;
        chain.push(child);
        cursor = child;
    }
    return chain;
}

/**
 * Collect all bone names already covered by existing springbone joints
 * so we don't double-register.
 */
function collectExistingJointBones(springBoneManager) {
    const set = new Set();
    if (!springBoneManager?.joints) return set;
    for (const joint of springBoneManager.joints) {
        if (joint.bone?.name) set.add(joint.bone.name);
    }
    return set;
}

/**
 * Main entry point. Call after VRM load.
 *
 * @param {object} vrm - @pixiv/three-vrm VRM instance
 * @returns {Promise<{added: number, chains: Array<{category:string, bones:string[]}>}>}
 */
export async function autoRegisterSpringBones(vrm) {
    if (!vrm?.scene) return { added: 0, chains: [] };

    // Lazy-load the springbone module so we access the constructor classes.
    // We import from the same CDN the main VRM bundle uses so versions match.
    let VRMSpringBoneJoint;
    try {
        const mod = await import('@pixiv/three-vrm-springbone');
        VRMSpringBoneJoint = mod.VRMSpringBoneJoint;
    } catch (e) {
        // Some builds of three-vrm include springbone classes on the main module.
        try {
            const mainMod = await import('@pixiv/three-vrm');
            VRMSpringBoneJoint = mainMod.VRMSpringBoneJoint;
        } catch (e2) {
            console.warn('[auto-springbones] Could not import VRMSpringBoneJoint class — skipping', e, e2);
            return { added: 0, chains: [] };
        }
    }

    if (!VRMSpringBoneJoint) {
        console.warn('[auto-springbones] VRMSpringBoneJoint not exported by module — skipping');
        return { added: 0, chains: [] };
    }

    // Ensure the VRM has a springbone manager. If none was created by the
    // loader (i.e. the file had no springbone extension at all), we create
    // an empty one.
    let manager = vrm.springBoneManager;
    if (!manager) {
        try {
            const sbModule = await import('@pixiv/three-vrm-springbone');
            const VRMSpringBoneManager = sbModule.VRMSpringBoneManager;
            if (VRMSpringBoneManager) {
                manager = new VRMSpringBoneManager();
                vrm.springBoneManager = manager;
            }
        } catch (e) {
            console.warn('[auto-springbones] No springbone manager and could not create one', e);
            return { added: 0, chains: [] };
        }
    }
    if (!manager) return { added: 0, chains: [] };

    // Authored spring settings and collider groups are model-specific. In
    // particular, zero gravity and high stiffness can intentionally hold bangs
    // outside the face. Apply presets only when creating missing joints below.
    const existingBones = collectExistingJointBones(manager);

    // Walk all bones, find chain roots matching cloth patterns whose
    // parents do NOT match (so we only start chains once).
    const chainRoots = [];
    vrm.scene.traverse((obj) => {
        if (!obj.isBone && obj.type !== 'Bone') return;
        const cat = classifyBone(obj.name);
        if (!cat) return;
        const parent = obj.parent;
        const parentCat = parent ? classifyBone(parent.name) : null;
        if (parentCat === cat) return; // not a root, parent in same chain
        chainRoots.push({ bone: obj, category: cat });
    });

    const addedChains = [];
    let addedJoints = 0;

    for (const { bone: root, category } of chainRoots) {
        const chain = collectLinearChain(root);
        if (chain.length < 2) continue; // need at least root + 1 child to swing

        const preset = PRESETS[category] || PRESETS.medium;

        // Create joints for each bone EXCEPT the very last tip (the tip is
        // used as the child offset reference of the previous joint).
        for (let i = 0; i < chain.length - 1; i++) {
            const parentBone = chain[i];
            const childBone  = chain[i + 1];
            if (existingBones.has(parentBone.name)) continue;

            try {
                const joint = new VRMSpringBoneJoint(
                    parentBone,
                    childBone,
                    { ...preset, gravityDir: preset.gravityDir.clone() },
                    [], // no collider groups (model likely has none defined)
                );
                if (typeof manager.addJoint === 'function') {
                    manager.addJoint(joint);
                } else if (manager.joints && typeof manager.joints.add === 'function') {
                    manager.joints.add(joint);
                } else {
                    // Fallback: manager.joints may be a plain Set reference
                    manager.joints?.add?.(joint);
                }
                addedJoints++;
                existingBones.add(parentBone.name);
            } catch (e) {
                console.warn(`[auto-springbones] Failed to create joint for ${parentBone.name}`, e);
            }
        }

        addedChains.push({ category, bones: chain.map(b => b.name) });
    }

    // Capture rest state of all newly added joints so sim starts neutral.
    try {
        if (addedJoints > 0 && typeof manager.setInitState === 'function') {
            manager.setInitState();
        }
    } catch (e) {
        // non-fatal
    }

    if (addedJoints > 0) {
        console.log(
            `[auto-springbones] Registered ${addedJoints} joints across ` +
            `${addedChains.length} chains:`,
            addedChains,
        );
    } else {
        console.log('[auto-springbones] No additional spring joints needed.');
    }

    return { added: addedJoints, chains: addedChains };
}

