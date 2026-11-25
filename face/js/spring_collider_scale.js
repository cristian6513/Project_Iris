/**
 * Hair colliders at the avatar's real size.
 *
 * avatar.js scales every model to the same height; IRIS is drawn 1.46 times her file's size.
 * three-vrm (3.5.5) moves colliders with that scale but reads their radii, and each joint's hit
 * radius, as world lengths, so they keep the file's size. The head capsule that keeps IRIS's fringe
 * off her forehead ended up a third too small, and the fringe sank into her skin, up to 18 mm while
 * she sat up on the bed, or when the breeze blew against her face (2026-11-24).
 *
 * Only the springs that hang from the head (hair, head accessories) get the scale, with the
 * colliders that only they use. Clothing springs keep today's sizes: the skirt, the coat and
 * seat_drape.js were tuned with them, and scaling them too opens the coat and lifts the skirt
 * when she sits. Applied once per spring manager; returns what changed.
 */

const applied = new WeakMap();

function hangsFrom(bone, ancestor) {
    for (let node = bone?.parent; node; node = node.parent) {
        if (node === ancestor) return true;
    }
    return false;
}

function worldScale(object) {
    if (!object) return 1;
    object.updateWorldMatrix?.(true, false);
    const e = object.matrixWorld?.elements;
    if (!e) return 1;
    // The length of each axis column; avatar.js scales all three alike.
    const sx = Math.hypot(e[0], e[1], e[2]), sy = Math.hypot(e[4], e[5], e[6]), sz = Math.hypot(e[8], e[9], e[10]);
    return (sx + sy + sz) / 3;
}

export function scaleHairColliders(vrm) {
    const manager = vrm?.springBoneManager;
    const joints = manager?.joints;
    const head = vrm?.humanoid?.getRawBoneNode?.('head');
    const none = { scale: 1, joints: 0, colliders: 0, shared: 0 };
    if (!joints?.size || !head) return none;
    if (applied.has(manager)) return applied.get(manager);
    const scale = worldScale(vrm.scene);
    if (!Number.isFinite(scale) || scale <= 0 || Math.abs(scale - 1) < 1e-3) return { ...none, scale };

    const hair = [], others = [];
    for (const joint of joints) (hangsFrom(joint.bone, head) ? hair : others).push(joint);
    const body = new Set();
    for (const joint of others) for (const group of joint.colliderGroups || []) for (const c of group.colliders || []) body.add(c);

    const done = new Set();
    let scaled = 0, shared = 0;
    for (const joint of hair) {
        if (Number.isFinite(joint.settings?.hitRadius)) joint.settings.hitRadius *= scale;
        for (const group of joint.colliderGroups || []) {
            for (const collider of group.colliders || []) {
                if (done.has(collider)) continue;
                done.add(collider);
                if (body.has(collider)) { shared++; continue; }
                // Spheres and capsules have a radius; planes and the room's boxes do not.
                if (Number.isFinite(collider.shape?.radius)) { collider.shape.radius *= scale; scaled++; }
            }
        }
    }
    const result = { scale, joints: hair.length, colliders: scaled, shared };
    applied.set(manager, result);
    return result;
}
