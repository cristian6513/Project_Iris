/**
 * The Companion's routines. It has no room and nowhere to walk, so RoomRoutines runs unchanged and
 * this stands in for the room's SceneActionManager: each order becomes what she can do standing
 * where she is, or a plain rest pose if its object or clip is missing.
 */
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { conjuredBook, conjuredTablet } from './conjured_props.js';
import { CONJURED_BOOK, CONJURED_TABLET, ConjuredProp, HeldProps } from './hand_prop.js';

export const STAND_INS = Object.freeze({
    read_center: 'stand_reading',
    tablet_center: 'stand_tablet',
    sit_sofa: 'stretch',
    read_sofa: 'stand_reading',
    tablet_sofa: 'stand_tablet',
    sit_bed: 'stretch',
    read_bed: 'stand_reading',
    tablet_bed: 'stand_tablet',
    sit_desk: 'stretch',
    use_pc: 'stand_tablet',
    look_window: 'idle_lazy',
    lie_bed: 'idle_lazy',
});
const PLAIN = 'idle_lazy';
const OBJECT_FOR = Object.freeze({ stand_reading: 'conjured', stand_tablet: 'tablet' });
const LEAVE = 'return_center';
// No room lights fall on the tablet here, so its own glow is all that shows: at the room's level it
// reads as a flat coloured card.
const GLOW_HERE = 0.4;

export class CompanionBody {
    constructor({ getIdle, hasProp = () => false }) {
        this.getIdle = getIdle;
        this.hasProp = hasProp;
        this.orders = 0;          // no orders here: nothing can overrule a routine but speech
        this.waiting = null;      // what she takes up once she has put the last thing away
    }

    heldByOrder() {
        return false;
    }

    isWalking() {
        return false;
    }

    isBusy() {
        return Boolean(this.waiting || this.getIdle()?.isGettingUp?.());
    }

    /** What this order is here: its stand-in, or the plain pose, or nothing at all. */
    pose(order) {
        const idle = this.getIdle();
        const wanted = STAND_INS[order];
        if (!wanted || !idle) return null;
        const needed = OBJECT_FOR[wanted];
        if (idle.canHold?.(wanted) && (!needed || this.hasProp(needed))) return wanted;
        return idle.canHold?.(PLAIN) ? PLAIN : null;
    }

    canRun(order) {
        return Boolean(this.pose(order));
    }

    execute(order) {
        const idle = this.getIdle();
        if (order === LEAVE) {
            this.waiting = null;
            return idle?.isSeated?.() ? idle.standUp() : true;
        }
        const pose = this.pose(order);
        if (!pose) return false;
        if (idle.isSeated(pose)) return true;
        if (!idle.isSeated()) return idle.trigger(pose);
        this.waiting = pose;
        return idle.standUp();
    }

    freeHands() {
        this.waiting = null;
        return this.getIdle()?.putAway?.() ?? false;
    }

    update() {
        const idle = this.getIdle();
        if (!this.waiting || !idle || idle.isSeated() || idle.isGettingUp?.()) return;
        const pose = this.waiting;
        this.waiting = null;
        idle.trigger(pose);
    }
}

/** The book and the tablet for a surface with no room to borrow them from. */
export class CompanionProps {
    constructor({ scene, modelBase, accent = '#7fe9ff' }) {
        this.scene = scene;
        this.modelBase = modelBase;
        this.accent = accent;
        this.held = new HeldProps({});
        this.holder = null;
    }

    async load(loader = new GLTFLoader()) {
        const std = ({ emissive, ...params }) => new THREE.MeshStandardMaterial({
            ...params, emissive: new THREE.Color(emissive === 'cyan' ? this.accent : emissive ?? 0),
        });
        // One missing file costs that object only: she rests instead.
        const load = (name) => loader.loadAsync(`${this.modelBase}/${name}`).then((gltf) => gltf.scene, () => null);
        const [book, tablet] = await Promise.all([load('book-v1.glb'), load('tablet-v2.glb')]);
        this.holder = new THREE.Group();
        this.holder.name = 'companion_props';
        this.scene.add(this.holder);
        const entries = {};
        if (book) {
            const conjured = conjuredBook(book, std);
            this.holder.add(conjured);
            entries.conjured = { prop: new ConjuredProp(conjured, CONJURED_BOOK), hand: CONJURED_BOOK.hand };
        }
        const body = tablet?.getObjectByName('tablet_body'), screen = tablet?.getObjectByName('tablet_screen');
        if (body && screen) {
            const conjured = conjuredTablet(body, screen, std);
            conjured.traverse((child) => { if (child.isMesh) child.material.userData.glow *= GLOW_HERE; });
            this.holder.add(conjured);
            entries.tablet = { prop: new ConjuredProp(conjured, { ...CONJURED_TABLET, replaces: null }), hand: CONJURED_TABLET.hand };
        }
        this.held = new HeldProps(entries);
    }

    has(id) {
        return this.held.has(id);
    }

    update(delta, vrm, state) {
        this.held.update(delta, vrm, state);
    }

    drop() {
        this.held.drop();
    }
}
