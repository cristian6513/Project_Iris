/**
 * Her body while she lies on the bed, for avatars other than IRIS.
 *
 * Clips move the hips as far as they move IRIS', scaled to the avatar's own hip height
 * (three-vrm). The furniture is laid out for IRIS, so an avatar whose hips stand higher (longer
 * legs, heels) also sits and lies that much higher: 10 cm over the mattress at 15 %. Sitting,
 * that keeps the feet of her longer legs on the floor. Lying, nothing rests on the floor, and her
 * hips take the place IRIS' would. Nothing changes for avatars within 3 % of IRIS.
 *
 * The skin follows the hips' world position under the normalized rig's root (three-vrm), so the
 * root is scaled: clips and their fades keep reading and writing the hips as the clip has them.
 *
 * Hips in IRIS' place are not enough: bodies differ. Measured in the room (2026-11-10), other
 * avatars' trunks rested up to 4 cm higher or lower than IRIS', and on their backs their heels
 * sank 7 to 9 cm into the mattress (longer legs at the clip's angle, heeled shoes). So the first
 * time she is held lying on her back, and on her front, `measure` reads where her skin is (the
 * lowest 1 % of the trunk's and of the feet's). From then on:
 *  - her trunk rests where IRIS' does, on her back and on her front (the root moves up or down);
 *  - on her back, a heel lower than IRIS' trunk is lifted to it, turning the leg at the hip by
 *    the smallest angle that does it.
 * Each applies as much as her hips face that way (on her side, neither), so the rolls blend them.
 * Arms are left alone: what sank there was clothing (lace cuffs hanging from the forearm).
 */

/** How high IRIS' hips stand over her soles in the room (iris.vrm 12, fitted to the room). */
export const IRIS_HIPS_HEIGHT = 1.289;

/**
 * Where IRIS' trunk rests on the bed, over the floor of her spot (room units): the lowest 1 % of
 * its skin, held on her back (bed_lying_idle) and on her front (bed_prone_idle, as it starts).
 */
export const IRIS_TRUNK_LOW = Object.freeze({ back: -0.6292, front: -0.5805 });

const TRUNK = ['hips', 'spine', 'chest', 'upperChest', 'neck', 'leftShoulder', 'rightShoulder', 'leftUpperLeg', 'rightUpperLeg'];
const FEET = ['leftFoot', 'rightFoot', 'leftToes', 'rightToes'];
const LEGS = Object.freeze([['leftUpperLeg', 'leftFoot'], ['rightUpperLeg', 'rightFoot']]);
const NOTHING = 0.01;          // room units (0.7 cm): closer than this to IRIS is IRIS
const MEASURE_STRIDE_LIMIT = 20000;

const clamp01 = value => (value > 0 ? Math.min(value, 1) : 0);
const smoothstep = (value, from, to) => {
    const t = clamp01((value - from) / (to - from));
    return t * t * (3 - 2 * t);
};

/** How much her hips face up (on her back) and down (on her front): `down` in her hips' frame. */
export function lyingFacing(down, flip = false) {
    // A VRM 0 model looks the other way in its own space (and so do the clips written to it).
    const forward = flip ? -down.z : down.z;
    return { back: smoothstep(-forward, 0.55, 0.9), front: smoothstep(forward, 0.55, 0.9) };
}

/** The turn (radians) that lifts the far end of a limb from `from` to `to` by `lift`. */
export function liftAngle(from, to, lift) {
    const dx = to.x - from.x, dy = to.y - from.y, dz = to.z - from.z;
    const length = Math.hypot(dx, dy, dz);
    if (length < 1e-6 || lift <= 0) return 0;
    const elevation = Math.asin(Math.max(-1, Math.min(1, dy / length)));
    return Math.asin(Math.max(-1, Math.min(1, dy / length + lift / length))) - elevation;
}

export class LyingHips {
    constructor(vrm) {
        this.vrm = vrm;
        this.root = vrm?.humanoid?.normalizedHumanBonesRoot ?? null;
        const height = (vrm?.humanoid?.normalizedRestPose?.hips?.position?.[1] ?? 0) * (vrm?.scene?.scale?.y ?? 1);
        const factor = height > 0 ? IRIS_HIPS_HEIGHT / height : 1;
        this.factor = Math.abs(factor - 1) < 0.03 ? 1 : factor;
        this.flip = String(vrm?.meta?.metaVersion) === '0';
        // Per way of lying, once measured: the trunk's rise and, on her back, the legs' lift.
        this.fit = { back: null, front: null };
        this.gain = { back: 0, front: 0 };
        this.measured = { back: null, front: null };
        this._masks = null;
        this._legRotations = new Map();
    }

    _bone(name) {
        return this.vrm?.humanoid?.getNormalizedBoneNode?.(name) ?? null;
    }

    _vector() {
        return new this.root.position.constructor();
    }

    _sceneScale() {
        return this.vrm.scene?.getWorldScale?.(this._vector())?.y || 1;
    }

    /** How much her hips face up and down now. */
    facing() {
        const hips = this._bone('hips');
        if (!hips || !this.root) return { back: 0, front: 0 };
        const turn = hips.getWorldQuaternion(new this.root.quaternion.constructor()).invert();
        return lyingFacing(this._vector().set(0, -1, 0).applyQuaternion(turn), this.flip);
    }

    /**
     * 0 the body as the clip has it, 1 lying where IRIS' would. `delta` eases in what a new
     * measurement adds.
     */
    set(weight, delta = 1 / 60) {
        if (!this.root) return;
        // Replace an earlier lift if the weight is set again before this frame is drawn.
        if (this._legRotations.size) this.restoreLegs();
        const w = Number.isFinite(weight) ? clamp01(weight) : 0;
        if (this.factor !== 1) this.root.scale.setScalar(1 + w * (this.factor - 1));
        if (!this.fit.back && !this.fit.front) return;
        for (const kind of ['back', 'front']) if (this.fit[kind]) this.gain[kind] = clamp01(this.gain[kind] + delta * 2);
        const facing = w > 0 ? this.facing() : { back: 0, front: 0 };
        const share = kind => w * facing[kind] * this.gain[kind];
        const rise = (this.fit.back?.rise ?? 0) * share('back') + (this.fit.front?.rise ?? 0) * share('front');
        this.root.position.y = rise / this._sceneScale();
        const legs = (this.fit.back?.legs ?? 0) * share('back');
        if (legs <= 0) return;
        this.root.updateWorldMatrix(true, true);
        for (const [joint, end] of LEGS) this._lift(joint, end, legs);
    }

    /** Turn the limb at `joint` so its end `end` rises by `lift` (world units). */
    _lift(jointName, endName, lift) {
        const joint = this._bone(jointName), end = this._bone(endName);
        if (!joint?.parent || !end) return;
        const from = joint.getWorldPosition(this._vector()), to = end.getWorldPosition(this._vector());
        const angle = liftAngle(from, to, lift);
        if (!angle) return;
        const axis = this._vector().subVectors(to, from).cross(this._vector().set(0, 1, 0));
        if (axis.lengthSq() < 1e-10) return;
        const Quaternion = this.root.quaternion.constructor;
        const turn = new Quaternion().setFromAxisAngle(axis.normalize(), angle);
        const world = joint.getWorldQuaternion(new Quaternion()).premultiply(turn);
        this._legRotations.set(joint, joint.quaternion.clone());
        joint.quaternion.copy(joint.parent.getWorldQuaternion(new Quaternion()).invert().multiply(world));
        joint.updateWorldMatrix(false, true);
    }

    /** Restore the clip's rotations after the corrected pose has reached the raw bones. */
    restoreLegs() {
        for (const [joint, rotation] of this._legRotations) joint.quaternion.copy(rotation);
        this._legRotations.clear();
    }

    /** Which vertices belong to the trunk and the feet, by the bone that moves them most. */
    _regions() {
        if (this._masks) return this._masks;
        const humanoid = this.vrm.humanoid;
        const region = new Map();
        for (const [index, list] of [[0, TRUNK], [1, FEET]]) {
            for (const bone of list) {
                const node = humanoid.getRawBoneNode?.(bone);
                if (node) region.set(node, index);
            }
        }
        const masks = [];
        this.vrm.scene.traverse(mesh => {
            if (!mesh.isSkinnedMesh) return;
            const index = mesh.geometry?.attributes?.skinIndex, weight = mesh.geometry?.attributes?.skinWeight;
            if (!index || !weight) return;
            const of = new Int8Array(index.count).fill(-1);
            for (let i = 0; i < index.count; i++) {
                let best = 0, at = 0;
                for (let k = 0; k < 4; k++) {
                    const w = weight.getComponent(i, k);
                    if (w > best) { best = w; at = index.getComponent(i, k); }
                }
                of[i] = region.get(mesh.skeleton.bones[at]) ?? -1;
            }
            masks.push({ mesh, of });
        });
        return (this._masks = masks);
    }

    /** Where her skin is now: the lowest 1 % of the trunk's and the feet's over her spot's floor. */
    _lows() {
        const scene = this.vrm.scene;
        scene.updateMatrixWorld(true);
        const masks = this._regions();
        const total = masks.reduce((sum, { of }) => sum + of.length, 0);
        const stride = Math.max(1, Math.ceil(total / MEASURE_STRIDE_LIMIT));
        const heights = [[], []];
        const point = this._vector();
        for (const { mesh, of } of masks) {
            mesh.skeleton.update();
            for (let i = 0; i < of.length; i += stride) {
                if (of[i] < 0) continue;
                mesh.getVertexPosition(i, point).applyMatrix4(mesh.matrixWorld);
                heights[of[i]].push(point.y);
            }
        }
        const floor = (scene.parent ?? scene).getWorldPosition(this._vector()).y;
        const low = values => {
            if (!values.length) return null;
            values.sort((a, b) => a - b);
            return values[Math.floor(0.01 * (values.length - 1))] - floor;
        };
        return { trunk: low(heights[0]), feet: low(heights[1]) };
    }

    /**
     * Read where her body rests, held lying on her back (`back`) or front (`front`), once. Done
     * where the clip already has her hips in IRIS' place, before anything here moves her.
     */
    measure(kind) {
        if (!this.root || !(kind in this.fit) || this.measured[kind]) return this.fit[kind] ?? null;
        const offset = this.root.position.y * this._sceneScale();
        const lows = this._lows();
        if (lows.trunk === null) return null;
        const trunk = lows.trunk - offset, feet = lows.feet === null ? null : lows.feet - offset;
        this.measured[kind] = { trunk, feet };
        const target = IRIS_TRUNK_LOW[kind];
        const rise = Math.abs(target - trunk) < NOTHING ? 0 : target - trunk;
        const fit = { rise };
        if (kind === 'back') {
            // A heel may sink as far as IRIS' trunk does, no further.
            const lift = feet === null ? 0 : target - (feet + rise);
            fit.legs = lift > NOTHING ? lift : 0;
        }
        this.fit[kind] = rise || fit.legs ? fit : null;
        return this.fit[kind];
    }
}
