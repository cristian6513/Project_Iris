import { Bone, Object3D } from 'three';
import { MMDPhysics } from 'three/addons/animation/MMDPhysics.js';

const TRANSFORM_SIZE = 7;
const FURNITURE_SIZE = 10;
// Ammo takes a body's group and mask as 16-bit shorts, all of them MMD's. The furniture is in every
// group and meets every group: any body that collides with anything collides with it.
const EVERY_GROUP = -1;
const KINEMATIC = 2;
const NO_CONTACT_RESPONSE = 4;
const ACTIVE = 1;
const NEVER_SLEEPS = 4;
let physics, mesh, zero, inertia, solver, defaultSplitImpulse;
let dynamic = [];
let furniture = [];
let placed = null;
let place = null;
// Her hair (the physics bodies under her head) and, while she lies on the bed, held to it.
let hair = [];
let hairBones = [];
let holding = false;

function transformOf(Ammo, boxes, o) {
    place ??= { form: new Ammo.btTransform(), origin: new Ammo.btVector3(0, 0, 0), turn: new Ammo.btQuaternion(0, 0, 0, 1) };
    place.form.setIdentity();
    place.origin.setValue(boxes[o], boxes[o + 1], boxes[o + 2]);
    place.turn.setValue(boxes[o + 3], boxes[o + 4], boxes[o + 5], boxes[o + 6]);
    place.form.setOrigin(place.origin);
    place.form.setRotation(place.turn);
    return place.form;
}

/**
 * The room's furniture as kinematic boxes, put where it is around her on every step
 * (pmx_physics.js puts it in this mesh's space). Her physics runs in her own space, so when she
 * walks or turns the furniture moves around her. Bullet gives a kinematic body the speed of its
 * move, and the boxes were thrown at her clothes when she turned by the sofa or sat down. Each box
 * is placed twice, so Bullet sees it at rest even after a line of light.
 */
function placeFurniture(boxes) {
    const Ammo = globalThis.Ammo;
    const count = boxes ? boxes.length / FURNITURE_SIZE : 0;
    const same = count === furniture.length && furniture.every((item, i) =>
        item.half.every((value, k) => Math.abs(value - boxes[i * FURNITURE_SIZE + 7 + k]) <= value * 0.01 + 1e-4));
    if (!same) {
        for (const item of furniture) {
            physics.world.removeRigidBody(item.body);
            Ammo.destroy(item.body);
            Ammo.destroy(item.info);
            Ammo.destroy(item.state);
            Ammo.destroy(item.shape);
        }
        furniture = [];
        for (let i = 0; i < count; i++) {
            const o = i * FURNITURE_SIZE;
            const half = [boxes[o + 7], boxes[o + 8], boxes[o + 9]];
            const size = new Ammo.btVector3(...half);
            const shape = new Ammo.btBoxShape(size);
            Ammo.destroy(size);
            const state = new Ammo.btDefaultMotionState(transformOf(Ammo, boxes, o));
            const inertia = new Ammo.btVector3(0, 0, 0);
            const info = new Ammo.btRigidBodyConstructionInfo(0, state, shape, inertia);
            Ammo.destroy(inertia);
            info.set_m_friction(0.6);
            info.set_m_restitution(0);
            const body = new Ammo.btRigidBody(info);
            body.setCollisionFlags(body.getCollisionFlags() | 2);   // kinematic
            body.setActivationState(4);                               // never sleeps
            physics.world.addRigidBody(body, EVERY_GROUP, EVERY_GROUP);
            furniture.push({ body, shape, state, info, half });
        }
    } else if (count && !sameBoxes(boxes)) {
        furniture.forEach(({ body }, i) => {
            const form = transformOf(Ammo, boxes, i * FURNITURE_SIZE);
            body.getMotionState().setWorldTransform(form);
            // Bullet's speed for a kinematic body runs from its last place to this one: the second
            // call makes the last place this one too.
            body.setCenterOfMassTransform(form);
            body.setCenterOfMassTransform(form);
        });
    }
    placed = count ? Float32Array.from(boxes) : null;
}

function sameBoxes(boxes) {
    return placed?.length === boxes.length && placed.every((value, i) => Math.abs(value - boxes[i]) < 1e-5);
}

/** The physics bodies under the head bone, with the modelled pose of their bones (as at init). */
function findHair(parents, head) {
    if (!(head >= 0)) return;
    const under = index => {
        for (let parent = parents[index]; parent >= 0; parent = parents[parent]) if (parent === head) return true;
        return false;
    };
    hair = physics.bodies.filter(({ params }) => params.type !== 0 && params.boneIndex >= 0 && under(params.boneIndex))
        .map(rb => {
            const { width, height, depth, shapeType } = rb.params;
            // How far the body reaches from its centre: sphere, box or capsule.
            const reach = shapeType === 0 ? width : shapeType === 1 ? Math.hypot(width, height, depth) : width + height / 2;
            return { rb, reach, type: rb.params.type, flags: 0 };
        });
    hairBones = [...new Set(hair.map(({ rb }) => rb.params.boneIndex))].map(index => {
        const bone = mesh.skeleton.bones[index];
        return { bone, position: bone.position.clone(), quaternion: bone.quaternion.clone() };
    });
}

/**
 * Lying on the bed, her hair follows her head in its modelled shape. Its joints only bend a few
 * degrees a link, so it can't lie flat: the mattress pushed it up into an arc over her, or it got
 * caught under her and stayed tangled when she stood. Held, each body is like the ones that
 * follow a bone (type 0), and touches nothing: what sinks into the mattress stays hidden there.
 */
function holdHair() {
    for (const item of hair) {
        const { body, params } = item.rb;
        item.flags = body.getCollisionFlags();
        params.type = 0;
        body.setMassProps(0, zero);
        body.setCollisionFlags(item.flags | KINEMATIC | NO_CONTACT_RESPONSE);
        body.setActivationState(NEVER_SLEEPS);
    }
    holding = true;
}

function poseHair() {
    for (const { bone, position, quaternion } of hairBones) {
        bone.position.copy(position);
        bone.quaternion.copy(quaternion);
    }
    mesh.updateMatrixWorld(true);
    for (const { rb } of hair) rb.updateFromBone();
}

/** Free again from where it is held now, still. */
function releaseHair() {
    for (const item of hair) {
        const { body, params } = item.rb;
        params.type = item.type;
        body.getCollisionShape().calculateLocalInertia(params.weight, inertia);
        body.setMassProps(params.weight, inertia);
        body.updateInertiaTensor();
        body.setCollisionFlags(item.flags);
        body.forceActivationState(ACTIVE);
        body.clearForces();
        body.setLinearVelocity(zero);
        body.setAngularVelocity(zero);
    }
    holding = false;
}

/**
 * No held body in the furniture: let go inside the mattress, the hair would be thrown out of it.
 * While she sits up or stands by the bed it stays held, until it has room to fall.
 */
function hairClear() {
    if (!placed) return true;
    return hair.every(({ rb, reach }) => {
        const origin = rb.body.getCenterOfMassTransform().getOrigin();
        return !inFurniture(origin.x(), origin.y(), origin.z(), reach);
    });
}

function inFurniture(x, y, z, reach) {
    for (let o = 0; o < placed.length; o += FURNITURE_SIZE) {
        // Into the box's own frame: turned back by its turn (the conjugate).
        const dx = x - placed[o], dy = y - placed[o + 1], dz = z - placed[o + 2];
        const qx = -placed[o + 3], qy = -placed[o + 4], qz = -placed[o + 5], qw = placed[o + 6];
        const tx = 2 * (qy * dz - qz * dy), ty = 2 * (qz * dx - qx * dz), tz = 2 * (qx * dy - qy * dx);
        const lx = dx + qw * tx + (qy * tz - qz * ty);
        const ly = dy + qw * ty + (qz * tx - qx * tz);
        const lz = dz + qw * tz + (qx * ty - qy * tx);
        if (Math.abs(lx) < placed[o + 7] + reach && Math.abs(ly) < placed[o + 8] + reach
            && Math.abs(lz) < placed[o + 9] + reach) return true;
    }
    return false;
}

function setPose(transforms) {
    mesh.skeleton.bones.forEach((bone, index) => {
        const offset = index * TRANSFORM_SIZE;
        bone.position.fromArray(transforms, offset);
        bone.quaternion.fromArray(transforms, offset + 3);
    });
    mesh.updateMatrixWorld(true);
}

self.onmessage = async ({ data }) => {
    try {
        const { transforms } = data;
        if (data.kind === 'init') {
            const moduleUrl = new URL('ammo.module.js', data.assets).href;
            const [ammo, response] = await Promise.all([
                import(/* @vite-ignore */ moduleUrl),
                fetch(new URL('ammo.wasm.wasm', data.assets)),
            ]);
            if (!response.ok) throw new Error('No se pudo cargar el motor de físicas MMD.');
            // The pinned Emscripten loader writes this.Ammo while starting.
            globalThis.Ammo = await ammo.default.call(globalThis, { wasmBinary: await response.arrayBuffer() });
            mesh = new Object3D();
            const bones = data.parents.map(() => new Bone());
            data.parents.forEach((parent, index) => (bones[parent] || mesh).add(bones[index]));
            mesh.skeleton = { bones };
            setPose(transforms);
            physics = new MMDPhysics(mesh, data.bodies, data.constraints, { unitStep: data.step, maxStepNum: 2 });
            solver = physics.world.getSolverInfo();
            defaultSplitImpulse = solver.get_m_splitImpulse();
            zero = new globalThis.Ammo.btVector3(0, 0, 0);
            inertia = new globalThis.Ammo.btVector3(0, 0, 0);
            dynamic = [...new Set(data.bodies.filter(body => body.type !== 0 && body.boneIndex >= 0).map(body => body.boneIndex))];
            findHair(data.parents, data.head);
            self.postMessage({ ready: true });
            return;
        }

        setPose(transforms);
        placeFurniture(data.furniture ?? null);
        if (hair.length) {
            if (data.hold && !holding) holdHair();
            if (holding) poseHair();
            if (!data.hold && holding && hairClear()) releaseHair();
        }
        if (data.reset) {
            physics.reset();
            for (const { body, params } of physics.bodies) {
                if (params.type === 0) continue;
                body.clearForces();
                body.setLinearVelocity(zero);
                body.setAngularVelocity(zero);
            }
        }
        if (data.step > 0) physics.unitStep = data.step;          // the rate follows «Ahorro» live
        // Resolve deep overlaps without adding velocity when a room pose is reset. Steady
        // furniture contacts use the regular solver: split impulses kept skirt links oscillating.
        solver.set_m_splitImpulse(data.furniture?.length ? Boolean(data.reset) : defaultSplitImpulse);
        physics.update(data.delta);
        for (const index of dynamic) {
            const offset = index * TRANSFORM_SIZE;
            const bone = mesh.skeleton.bones[index];
            bone.position.toArray(transforms, offset);
            bone.quaternion.toArray(transforms, offset + 3);
            for (let i = offset; i < offset + TRANSFORM_SIZE; i++) {
                if (!Number.isFinite(transforms[i])) {
                    throw new Error('El modelo produjo una pose inválida durante la simulación MMD.');
                }
            }
        }
        self.postMessage({ transforms, epoch: data.epoch }, [transforms.buffer]);
    } catch (error) {
        self.postMessage({ error: error?.message || String(error) });
    }
};
