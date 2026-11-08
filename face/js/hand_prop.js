/**
 * An object of the room that IRIS can take in her hand and put back.
 *
 * It moves to her hand when the clip reaches the moment she takes it, and settles into its grip
 * while the hand carries it. The hand is the normalized bone, so a grip fits any avatar. Clips say
 * when through `idle.heldProp()`: this only follows. Without a hand it stays where it is.
 */

import * as THREE from 'three';

const SETTLE_SECONDS = 0.7;
// Things keep their size whatever the avatar's: a grip is in IRIS' scale, the rig's the reference.
const REFERENCE_SCALE = 1.4594;
const handScale = new THREE.Vector3();
const fit = (hand) => REFERENCE_SCALE / hand.getWorldScale(handScale).x;
const HALF_TURN = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), Math.PI);

/** The grip on this hand. A VRM 0's bones look the other way, half a turn from a VRM 1's. */
function gripOn(hand, grip, halfTurned) {
    const position = grip.position.clone().multiplyScalar(fit(hand));
    const quaternion = grip.quaternion.clone();
    if (halfTurned) {
        position.applyQuaternion(HALF_TURN);
        quaternion.premultiply(HALF_TURN);
    }
    return { position, quaternion };
}
const APPEAR_SECONDS = 0.3;
const APPEAR_FLASH = 2.2;
const pose = (position, quaternion) => ({
    position: new THREE.Vector3(...position),
    quaternion: new THREE.Quaternion(...quaternion),
});

// The book rides on the right half: the wrist lies on its cover, the fingers towards the spine.
// Same numbers as WRIST_ON_BOOK in the clip's script, seen from the hand.
export const BOOK = Object.freeze({
    name: 'bookshelf_book',
    hand: 'rightHand',
    grip: pose([-0.215, -0.02, 0.04], [1, 0, 0, 0]),
    hinge: 'libro_izquierda',
    openAngle: THREE.MathUtils.degToRad(155),
});

// The book that appears in her hands. It lies on the right palm, fingers towards its top, the
// wrist under the middle of the cover. Same numbers as WRIST_ON_BOOK in build_read.py.
export const CONJURED_BOOK = Object.freeze({
    name: 'conjured_book',
    hand: 'rightHand',
    grip: Object.freeze({
        position: new THREE.Vector3(-0.06, -0.02, -0.075),
        quaternion: new THREE.Quaternion().setFromRotationMatrix(new THREE.Matrix4().makeBasis(
            new THREE.Vector3(0, 0, 1), new THREE.Vector3(0, -1, 0), new THREE.Vector3(1, 0, 0),
        )),
    }),
    hinge: 'libro_izquierda',
    openAngle: BOOK.openAngle,
});

// The tablet, its screen off, on her left hand: fingers up its side, thumb on the glass side.
// Same numbers as GRIP_X, GRIP_TOP and GRIP_BACK in build_tablet.py. Modelled in room units,
// so `scale` undoes the avatar's own. While she holds it the desk's stand stays empty.
export const CONJURED_TABLET = Object.freeze({
    name: 'conjured_tablet',
    hand: 'leftHand',
    scale: 1 / 1.4594,
    replaces: 'desk_tablet_panel',
    grip: Object.freeze({
        position: new THREE.Vector3(0.05, -0.1635, 0.012),
        quaternion: new THREE.Quaternion().setFromRotationMatrix(new THREE.Matrix4().makeBasis(
            new THREE.Vector3(0, -1, 0), new THREE.Vector3(1, 0, 0), new THREE.Vector3(0, 0, 1),
        )),
    }),
});

/** Something that appears in her hand out of nothing, with a flash, and goes the same way. */
export class ConjuredProp {
    constructor(object, { grip, hinge = null, openAngle = 0, scale = 1, replaces = null }) {
        this.object = object;
        this.home = object.parent;
        this.grip = grip;
        this.hinge = hinge ? object.getObjectByName(hinge) : null;
        this.openAngle = openAngle;
        this.scale = scale;
        this.size = 1;
        this.replaces = replaces;
        this.shown = 0;
        this.open = 0;
        this.materials = [];
        object.traverse((child) => { if (child.isMesh) this.materials.push(child.material); });
        object.visible = false;
        object.userData.conjured = true;
    }

    update(dt, hand, state, halfTurned = false) {
        const held = Boolean(hand && state?.held);
        if (held && this.object.parent !== hand) {
            hand.add(this.object);
            this.size = fit(hand);
            const { position, quaternion } = gripOn(hand, this.grip, halfTurned);
            this.object.position.copy(position);
            this.object.quaternion.copy(quaternion);
        }
        if (state) this.open = state.open;
        this.shown = THREE.MathUtils.clamp(this.shown + (held ? dt : -dt) / APPEAR_SECONDS, 0, 1);
        const size = THREE.MathUtils.smoothstep(this.shown, 0, 1);
        this.object.visible = this.shown > 0;
        this.object.scale.setScalar(Math.max(size * this.scale * this.size, 0.001));
        if (this.replaces) this.replaces.visible = !this.object.visible;
        for (const material of this.materials) {
            material.emissiveIntensity = THREE.MathUtils.lerp(APPEAR_FLASH, material.userData.glow || 0, size);
        }
        if (this.hinge) this.hinge.rotation.z = this.open * this.openAngle;
        if (!this.object.visible && this.object.parent !== this.home) this.home.add(this.object);
    }

    /** Gone at once: the hand that holds it is about to go. */
    drop() {
        this.shown = 0;
        this.object.visible = false;
        if (this.replaces) this.replaces.visible = true;
        this.home.add(this.object);
    }
}

/** Everything an avatar can hold, each on its hand, following the clip. */
export class HeldProps {
    constructor(entries) {
        this.entries = entries;
    }

    has(id) {
        return Boolean(this.entries[id]);
    }

    update(delta, vrm, state) {
        const halfTurned = String(vrm?.meta?.metaVersion) === '0';
        for (const [id, { prop, hand }] of Object.entries(this.entries)) {
            prop.update(delta, vrm?.humanoid?.getNormalizedBoneNode?.(hand), state?.id === id ? state : null, halfTurned);
        }
    }

    drop() {
        for (const { prop } of Object.values(this.entries)) prop.drop();
    }
}

export class HandProp {
    constructor(object, { grip, hinge = null, openAngle = 0 }) {
        this.object = object;
        this.home = object.parent;
        this.rest = { position: object.position.clone(), quaternion: object.quaternion.clone() };
        this.grip = grip;
        this.hinge = hinge ? object.getObjectByName(hinge) : null;
        this.openAngle = openAngle;
        this.held = false;
        this.settled = 1;
        this.from = null;
    }

    update(dt, hand, state, halfTurned = false) {
        const held = Boolean(hand && state?.held);
        if (held !== this.held) this._move(held ? hand : this.home, held, halfTurned);
        if (this.settled < 1) {
            this.settled = Math.min(1, this.settled + dt / SETTLE_SECONDS);
            const ease = THREE.MathUtils.smoothstep(this.settled, 0, 1);
            const to = held ? this.gripNow : this.rest;
            this.object.position.lerpVectors(this.from.position, to.position, ease);
            this.object.quaternion.slerpQuaternions(this.from.quaternion, to.quaternion, ease);
        }
        if (this.hinge) this.hinge.rotation.z = (state?.open ?? 0) * this.openAngle;
    }

    /** Back in its place at once: the hand that holds it is about to go. */
    drop() {
        if (this.held) this._move(this.home, false);
        this.settled = 1;
        this.object.position.copy(this.rest.position);
        this.object.quaternion.copy(this.rest.quaternion);
        if (this.hinge) this.hinge.rotation.z = 0;
    }

    _move(parent, held, halfTurned = false) {
        parent.attach(this.object);
        if (held) this.gripNow = gripOn(parent, this.grip, halfTurned);
        this.held = held;
        this.settled = 0;
        this.from = { position: this.object.position.clone(), quaternion: this.object.quaternion.clone() };
    }
}
