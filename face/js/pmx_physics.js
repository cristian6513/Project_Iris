const ASSETS = '/face/vendor/ammo/';
const MAX_BODIES = 1200;
const MAX_JOINTS = 2400;
const TRANSFORM_SIZE = 7;

const bodyNumbers = ['width', 'height', 'depth', 'weight', 'friction', 'restitution', 'positionDamping', 'rotationDamping'];
const jointVectors = ['position', 'rotation', 'translationLimitation1', 'translationLimitation2',
    'rotationLimitation1', 'rotationLimitation2', 'springPosition', 'springRotation'];
const FURNITURE_SIZE = 10;     // centre xyz, turn xyzw, half extents xyz, in the mesh's own space
// Held hair eases into its modelled shape over this many physics replies (about a third of a second).
const HOLD_EASE = 0.2;
const HOLD_EASE_STEPS = 20;
const bounded = value => Number.isFinite(value) && Math.abs(value) <= 1e6;

/**
 * The room's furniture (world boxes, furniture_colliders.js) in the PMX mesh's own space, where its
 * MMD physics runs: each box turns with her and scales with her (she walks and turns; the
 * furniture stays). Packed `FURNITURE_SIZE` numbers a box.
 */
export function furnitureInMeshSpace(boxes, matrixWorld) {
    const elements = matrixWorld.elements;
    // Columns of the mesh's matrix: its uniform scale is the length of any of them.
    const scale = Math.hypot(elements[4], elements[5], elements[6]) || 1;
    const inverse = matrixWorld.clone().invert();
    const out = new Float32Array(boxes.length * FURNITURE_SIZE);
    const m = inverse.elements;
    // The rotation part of the inverse, normalized, as a quaternion (the mesh only ever yaws).
    const r00 = m[0] * scale, r01 = m[4] * scale, r02 = m[8] * scale;
    const r10 = m[1] * scale, r11 = m[5] * scale, r12 = m[9] * scale;
    const r20 = m[2] * scale, r21 = m[6] * scale, r22 = m[10] * scale;
    const q = quaternionOf(r00, r01, r02, r10, r11, r12, r20, r21, r22);
    boxes.forEach((box, i) => {
        const cx = (box.min[0] + box.max[0]) / 2, cy = (box.min[1] + box.max[1]) / 2, cz = (box.min[2] + box.max[2]) / 2;
        const x = m[0] * cx + m[4] * cy + m[8] * cz + m[12];
        const y = m[1] * cx + m[5] * cy + m[9] * cz + m[13];
        const z = m[2] * cx + m[6] * cy + m[10] * cz + m[14];
        out.set([x, y, z, ...q,
            (box.max[0] - box.min[0]) / 2 / scale, (box.max[1] - box.min[1]) / 2 / scale, (box.max[2] - box.min[2]) / 2 / scale],
        i * FURNITURE_SIZE);
    });
    return out;
}

/** A rotation matrix as [x, y, z, w]. */
function quaternionOf(m11, m12, m13, m21, m22, m23, m31, m32, m33) {
    const trace = m11 + m22 + m33;
    if (trace > 0) {
        const s = 0.5 / Math.sqrt(trace + 1);
        return [(m32 - m23) * s, (m13 - m31) * s, (m21 - m12) * s, 0.25 / s];
    }
    if (m11 > m22 && m11 > m33) {
        const s = 2 * Math.sqrt(1 + m11 - m22 - m33);
        return [0.25 * s, (m12 + m21) / s, (m13 + m31) / s, (m32 - m23) / s];
    }
    if (m22 > m33) {
        const s = 2 * Math.sqrt(1 + m22 - m11 - m33);
        return [(m12 + m21) / s, 0.25 * s, (m23 + m32) / s, (m13 - m31) / s];
    }
    const s = 2 * Math.sqrt(1 + m33 - m11 - m22);
    return [(m13 + m31) / s, (m23 + m32) / s, 0.25 * s, (m21 - m12) / s];
}
const validVector = value => Array.isArray(value) && value.length === 3 && value.every(bounded);

function validPhysics(bodies, joints, boneCount) {
    if (bodies.length > MAX_BODIES || joints.length > MAX_JOINTS) return false;
    return bodies.every(body =>
        bodyNumbers.every(key => bounded(body[key]) && body[key] >= 0)
        && [0, 1, 2].includes(body.type) && [0, 1, 2].includes(body.shapeType)
        && Number.isInteger(body.boneIndex) && body.boneIndex >= -1 && body.boneIndex < boneCount
        && Number.isInteger(body.groupIndex) && body.groupIndex >= 0 && body.groupIndex < 16
        && Number.isInteger(body.groupTarget) && body.groupTarget >= 0 && body.groupTarget <= 0xffff
        && validVector(body.position) && validVector(body.rotation)
    ) && joints.every(joint =>
        [joint.rigidBodyIndex1, joint.rigidBodyIndex2].every(index =>
            Number.isInteger(index) && index >= 0 && index < bodies.length
        ) && jointVectors.every(key => validVector(joint[key]))
    );
}

/** `modelled`: each bone's [position, quaternion] as the PMX has it, before the arms go to the T-pose. */
export async function createPmxPhysics(mesh, humanoid, modelled = null) {
    const { rigidBodies, constraints } = mesh.geometry.userData.MMD;
    const bones = mesh.skeleton.bones;
    if (!validPhysics(rigidBodies, constraints, bones.length)) {
        throw new Error('El modelo PMX trae físicas no compatibles o supera el límite de simulación.');
    }

    const human = new Set(Object.values(humanoid.humanBones).map(bone => bone.node));
    const bodies = rigidBodies.map(body => body.type !== 0 && human.has(bones[body.boneIndex])
        ? { ...body, type: 0 } : body);
    const dynamic = [...new Set(bodies.filter(body => body.type !== 0 && body.boneIndex >= 0).map(body => body.boneIndex))];
    if (!dynamic.length) return null;
    const parents = new Map(bones.map((bone, index) => [bone, index]));
    const rest = dynamic.map(index => ({
        bone: bones[index], position: bones[index].position.clone(), rotation: bones[index].quaternion.clone(),
    }));
    // Her hair: what physics moves under the head bone. Lying on the bed it is held to her head as
    // modelled (holdHair, pmx_physics_worker.js), and eased into that shape here.
    const headBone = humanoid.humanBones.head?.node ?? null;
    const head = parents.get(headBone) ?? -1;
    const underHead = bone => { for (let up = bone.parent; up; up = up.parent) if (up === headBone) return true; return false; };
    const hair = new Set(headBone ? dynamic.filter(index => underHead(bones[index])) : []);
    const eased = { position: bones[0].position.clone(), quaternion: bones[0].quaternion.clone() };
    let hold = false;
    let easing = 0;
    // Heavy models may run at half rate in «Ahorro» (graphics_quality.js, through setRate).
    const heavy = bodies.length > 300 || constraints.length > 500;
    let hz = 65;
    let step = 1 / hz;
    const worker = new Worker(new URL('./pmx_physics_worker.js', import.meta.url), { type: 'module' });
    let transforms = new Float32Array(bones.length * TRANSFORM_SIZE);
    let pending = 0;
    let epoch = 0;
    let needsReset = true;
    let active = true;
    let initialized = false;

    function readPose(pose = null) {
        bones.forEach((bone, index) => {
            const offset = index * TRANSFORM_SIZE;
            const [position, quaternion] = pose?.[index] ?? [bone.position, bone.quaternion];
            position.toArray(transforms, offset);
            quaternion.toArray(transforms, offset + 3);
        });
    }

    function dispose() {
        if (!active) return;
        active = false;
        worker.onmessage = null;
        worker.onerror = null;
        worker.terminate();
        transforms = null;
    }

    await new Promise((resolve, reject) => {
        const timeout = setTimeout(() => fail(new Error('Las físicas MMD tardaron demasiado en iniciar.')), 15_000);

        function fail(error) {
            if (!active) return;
            clearTimeout(timeout);
            dispose();
            if (!initialized) reject(error);
            else {
                for (const { bone, position, rotation } of rest) {
                    bone.position.copy(position);
                    bone.quaternion.copy(rotation);
                }
                console.warn('[PMX] Se detuvieron las físicas.', error);
            }
        }

        worker.onerror = event => fail(new Error(event.message || 'Falló el proceso de físicas MMD.'));
        worker.onmessage = ({ data }) => {
            if (!active) return;
            if (data.error) {
                fail(new Error(data.error));
            } else if (data.ready) {
                clearTimeout(timeout);
                initialized = true;
                resolve();
            } else {
                transforms = data.transforms;
                if (data.epoch !== epoch) return;
                const ease = easing > 0 ? HOLD_EASE : 1;
                if (easing > 0) easing--;
                for (const index of dynamic) {
                    const offset = index * TRANSFORM_SIZE;
                    if (ease < 1 && hair.has(index)) {
                        bones[index].position.lerp(eased.position.fromArray(transforms, offset), ease);
                        bones[index].quaternion.slerp(eased.quaternion.fromArray(transforms, offset + 3), ease);
                        continue;
                    }
                    bones[index].position.fromArray(transforms, offset);
                    bones[index].quaternion.fromArray(transforms, offset + 3);
                }
            }
        };

        // Each rigid body keeps its offset from its bone as measured here, so this is the modelled
        // pose: the first step then resets every body onto the bones as they are posed now.
        readPose(modelled);
        worker.postMessage({
            kind: 'init', assets: new URL(ASSETS, document.baseURI).href,
            parents: bones.map(bone => parents.get(bone.parent) ?? -1),
            transforms, bodies, constraints, step, head,
        }, [transforms.buffer]);
        transforms = new Float32Array(bones.length * TRANSFORM_SIZE);
    });

    let furniture = null;
    return {
        heavy, bodies: bodies.length, joints: constraints.length,
        get hz() { return hz; },
        get active() { return active; },
        /** Steps per second: 65, or 32.5 for a heavy model in «Ahorro» (graphics_quality.js). */
        setRate(next) {
            if (!(next > 0) || next === hz) return;
            hz = next;
            step = 1 / hz;
            pending = 0;
        },
        /** The room's furniture as world boxes ({min, max}): her hair and clothes rest on it. */
        setFurniture(boxes) {
            furniture = Array.isArray(boxes) && boxes.length ? boxes.map(box => ({ min: [...box.min], max: [...box.max] })) : null;
        },
        update(delta) {
            if (!active || !(delta > 0)) return;
            pending = Math.min(pending + delta, step * 2);
            if (!transforms || pending < step) return;
            const steps = Math.floor(pending / step);
            pending -= steps * step;
            readPose();
            mesh.updateWorldMatrix(true, false);
            const placed = furniture ? furnitureInMeshSpace(furniture, mesh.matrixWorld) : null;
            worker.postMessage({
                kind: 'step', transforms, delta: steps * step, step, reset: needsReset, epoch, furniture: placed, hold,
            }, [transforms.buffer]);
            transforms = null;
            needsReset = false;
        },
        /**
         * Lying on the bed: her hair follows her head as modelled, eased into that shape. Let go,
         * it falls free from there, once no part of it is in the furniture.
         */
        holdHair(on) {
            on = Boolean(on) && hair.size > 0;
            if (on === hold) return;
            hold = on;
            if (on) easing = HOLD_EASE_STEPS;
        },
        reset() { needsReset = true; pending = 0; epoch++; },
        dispose,
    };
}
