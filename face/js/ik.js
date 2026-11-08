/**
 * IRIS IK Runtime — bone-level inverse kinematics for VRM avatars.
 *
 * Three solvers, picked per use-case for clarity over generality:
 *
 *   • lookAt(target, weight)
 *       Two-bone aim: neck → head. Computes the rotation each bone
 *       needs to point the head's forward axis at `target`, with
 *       per-axis clamps that match human comfort (yaw ±60°, pitch
 *       ±45°). Cheap, analytic, runs every frame.
 *
 *   • reach(side, target, weight)
 *       Three-bone analytic IK over the arm chain
 *       (upperArm → lowerArm → hand). Closed-form using the law of
 *       cosines — predictable elbow plane, no iteration jitter.
 *
 *   • point(side, target, weight)
 *       Same chain as `reach` but the hand stays open and the index
 *       finger axis is the one we aim. Internally just `reach()`
 *       with a different target offset (we add 8 cm along the
 *       hand's local +X so the fingertip lands on `target`).
 *
 * Why analytic for arm + lookAt:
 *   FABRIK is excellent for long chains but overkill for 2-3 bones.
 *   Analytic IK gives exact, jitter-free results in O(1) per bone.
 *
 * Coordinate-space conventions:
 *   `target` is always world-space. The solvers handle the world ↔
 *   local conversion using each bone's parentMatrixWorld so the
 *   avatar can be parented anywhere in the scene without breaking.
 *
 * Animation ownership:
 *   The avatar update loop suspends IK while an explicit animation owns
 *   the affected bones, including the transition back to idle.
 *
 * Usage:
 *   import { IKSolver } from './ik.js';
 *   const ik = new IKSolver(vrm);
 *   ik.lookAtWorld(camera.position, 0.8);   // aim head
 *   ik.reach('right', cupPosition, 1.0);     // grab a cup
 *   ik.solve();                              // commit rotations
 */

import * as THREE from 'three';


// ── Tunables ─────────────────────────────────────────────────────

// Per-axis clamps for the head/neck aim. These match comfortable
// human range for someone sitting in front of a screen — exceeding
// them produces uncanny "neck snap" tracking that ruins immersion.
const NECK_YAW_LIMIT   = THREE.MathUtils.degToRad(35);   // neck contributes a portion
const NECK_PITCH_LIMIT = THREE.MathUtils.degToRad(25);
const HEAD_YAW_LIMIT   = THREE.MathUtils.degToRad(45);   // head finishes the aim
const HEAD_PITCH_LIMIT = THREE.MathUtils.degToRad(35);

// Distribution between neck and head when aiming. 0.4 means neck
// does 40% of the rotation and head finishes 60% — feels natural;
// pure-head rotation looks puppet-like.
const NECK_AIM_RATIO = 0.4;


// ── Helpers ──────────────────────────────────────────────────────

const _v0 = new THREE.Vector3();
const _v1 = new THREE.Vector3();
const _v2 = new THREE.Vector3();
const _q0 = new THREE.Quaternion();
const _q1 = new THREE.Quaternion();
const _m0 = new THREE.Matrix4();
const _e0 = new THREE.Euler(0, 0, 0, 'YXZ');

/**
 * Resolve a humanoid bone by name across both `getNormalizedBoneNode`
 * (VRM 1.0) and `getBoneNode` (VRM 0.x), returning null if absent.
 */
function getBone(humanoid, name) {
    return (
        humanoid?.getNormalizedBoneNode?.(name) ??
        humanoid?.getBoneNode?.(name) ??
        null
    );
}

/**
 * Build a local-space rotation that, when applied to `bone`, makes
 * its forward axis (`localForward`) aim at `targetWorld`. Returns
 * a fresh Quaternion in local space (relative to bone's parent).
 *
 * Why not use Object3D.lookAt directly: lookAt sets the world
 * orientation from scratch — it ignores the bone's rest pose and
 * any inherited rotation, which produces twisted, inverted, or
 * floor-flipped results on humanoid skeletons that have rest
 * orientations that aren't world-aligned.
 */
function _aimBoneAt(bone, targetWorld, localForward, outQuat, weight = 1) {
    // Target direction in world space.
    bone.getWorldPosition(_v0);
    _v1.copy(targetWorld).sub(_v0).normalize();
    if (_v1.lengthSq() < 1e-8) {
        outQuat.identity();
        return;
    }

    // Bring the target direction into bone's PARENT local space —
    // we want a rotation that's expressed in the same space as
    // bone.quaternion, otherwise compounding it with the rest pose
    // produces double-rotation artifacts.
    const parent = bone.parent;
    if (parent) {
        parent.updateWorldMatrix(true, false);
        _m0.copy(parent.matrixWorld).invert();
        _v1.transformDirection(_m0);
    }

    // Compose the rotation from "bone's local forward" to "target
    // direction in parent space".
    outQuat.setFromUnitVectors(localForward, _v1);
    if (weight < 0.9999) {
        outQuat.slerp(_q1.identity(), 1 - weight);
    }
}


// ── IK Solver class ──────────────────────────────────────────────

export class IKSolver {
    /**
     * @param {object} vrm - @pixiv/three-vrm instance with .humanoid.
     */
    constructor(vrm) {
        this.vrm = vrm;
        this._humanoid = vrm?.humanoid ?? null;

        // Pending rotations queued by the public API (`lookAtWorld`,
        // `reach`, `point`). Flushed in `solve()` at end-of-frame so
        // multiple intents can layer (e.g. lookAt + reach).
        this._queue = [];

        // Cache the bone refs we use most. VRM bones are stable for
        // the lifetime of the avatar.
        this._cacheBones();

        // Rest-pose quaternions (the avatar's natural T-pose). Used
        // to blend IK out smoothly when the caller passes weight<1
        // and as the "no-target" fallback for `lookAtWorld(null)`.
        this._restPose = new Map();
        this._captureRestPose();
    }

    _cacheBones() {
        const h = this._humanoid;
        if (!h) {
            this.bones = {};
            return;
        }
        this.bones = {
            neck:           getBone(h, 'neck'),
            head:           getBone(h, 'head'),
            leftUpperArm:   getBone(h, 'leftUpperArm'),
            leftLowerArm:   getBone(h, 'leftLowerArm'),
            leftHand:       getBone(h, 'leftHand'),
            rightUpperArm:  getBone(h, 'rightUpperArm'),
            rightLowerArm:  getBone(h, 'rightLowerArm'),
            rightHand:      getBone(h, 'rightHand'),
            leftUpperLeg:   getBone(h, 'leftUpperLeg'),
            leftLowerLeg:   getBone(h, 'leftLowerLeg'),
            leftFoot:       getBone(h, 'leftFoot'),
            rightUpperLeg:  getBone(h, 'rightUpperLeg'),
            rightLowerLeg:  getBone(h, 'rightLowerLeg'),
            rightFoot:      getBone(h, 'rightFoot'),
        };
    }

    _captureRestPose() {
        for (const [name, bone] of Object.entries(this.bones)) {
            if (bone) this._restPose.set(name, bone.quaternion.clone());
        }
    }

    // ── Public intents ───────────────────────────────────────────

    /**
     * Aim the head (with neck contributing a portion) at a world-space
     * point. `weight` 0..1 fades between rest pose and full aim.
     *
     * The caller must respect explicit animation ownership of head/neck.
     */
    lookAtWorld(targetWorld, weight = 1.0) {
        if (!targetWorld || !this.bones.head) return;
        this._queue.push({ kind: 'lookAt', target: targetWorld.clone(), weight });
    }

    /**
     * Move the hand on `side` ('left' | 'right') so it lands at
     * `targetWorld`. Uses analytic 2-bone IK over upperArm + lowerArm.
     */
    reach(side, targetWorld, weight = 1.0) {
        if (!targetWorld) return;
        this._queue.push({ kind: 'reach', side, target: targetWorld.clone(), weight });
    }

    /**
     * Point the index finger axis at `targetWorld`. Same chain as
     * `reach` but offsets the IK target ~8cm beyond the hand so the
     * fingertip — not the wrist — lands on the target.
     */
    point(side, targetWorld, weight = 1.0) {
        if (!targetWorld) return;
        // 8cm forward of the hand along its local +X (the typical VRM
        // index-finger axis). We can't know the exact fingertip without
        // walking the finger chain, so this approximation is robust.
        const offset = 0.08;
        const adjusted = targetWorld.clone();
        const hand = this.bones[side === 'left' ? 'leftHand' : 'rightHand'];
        if (hand) {
            hand.updateWorldMatrix(true, false);
            _v0.setFromMatrixPosition(hand.matrixWorld);
            _v1.copy(targetWorld).sub(_v0).normalize();
            adjusted.copy(targetWorld).addScaledVector(_v1, -offset);
        }
        this._queue.push({ kind: 'reach', side, target: adjusted, weight });
    }

    /**
     * Drop the queue without applying. Useful in transitions where you
     * want IK to release for a frame before re-engaging.
     */
    cancel() {
        this._queue.length = 0;
    }

    /**
     * Apply all queued intents to the VRM bones. Call once per frame
     * AFTER the animation mixer / clip update, so we layer on top
     * of the active clip's pose instead of getting overwritten.
     */
    solve() {
        if (!this._queue.length) return;
        for (const intent of this._queue) {
            switch (intent.kind) {
                case 'lookAt': this._solveLookAt(intent.target, intent.weight); break;
                case 'reach':  this._solveReach(intent.side, intent.target, intent.weight); break;
            }
        }
        this._queue.length = 0;
    }

    // ── Solvers ──────────────────────────────────────────────────

    /**
     * Two-bone aim: neck does NECK_AIM_RATIO of the work, head does
     * the rest. Each bone is independently clamped per-axis.
     *
     * The procedure:
     *   1. Compute the FULL rotation needed at the head bone if it
     *      were doing the entire aim by itself.
     *   2. Decompose that rotation into yaw/pitch/roll using YXZ
     *      Euler order (matches comfortable human articulation).
     *   3. Clamp per-axis to safety bounds; assign NECK_AIM_RATIO
     *      to the neck and the remainder to the head.
     */
    _solveLookAt(targetWorld, weight) {
        const neck = this.bones.neck;
        const head = this.bones.head;
        if (!head) return;

        // Get the rotation the HEAD would need (if it were the only
        // bone aiming) — ignoring rest pose, in head-parent space.
        // VRM rest pose has the head's local +Z facing forward.
        _aimBoneAt(head, targetWorld, _v2.set(0, 0, 1), _q0, 1.0);

        // Decompose into Euler so we can split it into neck + head
        // contributions and clamp each axis.
        _e0.setFromQuaternion(_q0, 'YXZ');
        let yaw   = _e0.y;
        let pitch = _e0.x;
        const roll = 0;  // we don't roll the head; humans rarely tilt

        // Split between neck and head.
        const neckYaw   = THREE.MathUtils.clamp(yaw   * NECK_AIM_RATIO, -NECK_YAW_LIMIT,   NECK_YAW_LIMIT);
        const neckPitch = THREE.MathUtils.clamp(pitch * NECK_AIM_RATIO, -NECK_PITCH_LIMIT, NECK_PITCH_LIMIT);
        const headYaw   = THREE.MathUtils.clamp(yaw   - neckYaw,        -HEAD_YAW_LIMIT,   HEAD_YAW_LIMIT);
        const headPitch = THREE.MathUtils.clamp(pitch - neckPitch,      -HEAD_PITCH_LIMIT, HEAD_PITCH_LIMIT);

        // Apply, layered on top of the rest pose. We multiply the IK
        // rotation by the bone's current quaternion (already includes
        // any clip animation) so animation + IK coexist instead of
        // one stomping the other. Weight blends back toward rest.
        if (neck) {
            _e0.set(neckPitch, neckYaw, roll, 'YXZ');
            _q0.setFromEuler(_e0);
            const restNeck = this._restPose.get('neck') ?? neck.quaternion;
            // Blend rest * ikRotation by `weight`.
            _q1.copy(restNeck).multiply(_q0);
            neck.quaternion.slerp(_q1, weight);
        }
        _e0.set(headPitch, headYaw, roll, 'YXZ');
        _q0.setFromEuler(_e0);
        const restHead = this._restPose.get('head') ?? head.quaternion;
        _q1.copy(restHead).multiply(_q0);
        head.quaternion.slerp(_q1, weight);
    }

    /**
     * Analytic 2-bone IK over upperArm + lowerArm so the hand lands
     * at `targetWorld`. The third bone (hand) keeps its rest rotation
     * — wrist orientation is a separate concern, callers can override
     * it after `solve()` if needed.
     *
     * Closed-form algorithm:
     *   Given upperArm (A), lowerArm (B), hand (C) with bone lengths
     *   L1 = |AB|, L2 = |BC|, target T:
     *     d = |AT|, clamped to [|L1-L2|, L1+L2]   (preserves limb)
     *     interior elbow angle α = acos((L1²+L2²-d²) / (2·L1·L2))
     *     shoulder angle β = atan2(...) using the elbow-plane normal
     *   We pick the elbow plane from the cross of (target-shoulder)
     *   with the avatar's UP — this puts the elbow naturally below
     *   horizontal targets and bent backward for high targets.
     */
    _solveReach(side, targetWorld, weight) {
        const isLeft = side === 'left';
        const upper = this.bones[isLeft ? 'leftUpperArm' : 'rightUpperArm'];
        const lower = this.bones[isLeft ? 'leftLowerArm' : 'rightLowerArm'];
        const hand  = this.bones[isLeft ? 'leftHand'      : 'rightHand'];
        if (!upper || !lower || !hand) return;

        // World positions of the chain joints in their CURRENT pose.
        upper.updateWorldMatrix(true, false);
        lower.updateWorldMatrix(true, false);
        hand .updateWorldMatrix(true, false);
        const pUpper = _v0.setFromMatrixPosition(upper.matrixWorld).clone();
        const pLower = _v1.setFromMatrixPosition(lower.matrixWorld).clone();
        const pHand  = _v2.setFromMatrixPosition(hand.matrixWorld ).clone();

        const L1 = pUpper.distanceTo(pLower);
        const L2 = pLower.distanceTo(pHand);
        if (L1 < 1e-4 || L2 < 1e-4) return;

        const toTarget = targetWorld.clone().sub(pUpper);
        let d = toTarget.length();
        if (d < 1e-4) return;

        // Clamp distance to keep limb intact (no over-stretch).
        const dMax = (L1 + L2) * 0.999;
        const dMin = Math.abs(L1 - L2) * 1.001;
        d = THREE.MathUtils.clamp(d, dMin, dMax);
        toTarget.normalize();

        // Elbow-plane normal: pick a stable reference (avatar's local
        // up, transformed to world). Cross with toTarget gives the
        // axis around which the elbow bends — this defines whether
        // the elbow points down (default for low targets) or to the
        // side (for high targets, like reaching for the sky).
        const upWorld = new THREE.Vector3(0, 1, 0);
        // For a side bias, lean the up reference slightly toward the
        // body midline so elbows naturally point outward instead of
        // crossing the chest. ~30° lean is enough to feel right.
        const lateralBias = isLeft ? -0.5 : 0.5;
        upWorld.x += lateralBias * 0.5;
        upWorld.normalize();

        // Solve angles. Using law of cosines: at the shoulder, the
        // angle between upper-arm direction and toTarget is φ; at
        // the elbow, the interior angle is α (we want π - α as the
        // bend, since rest is "straight = π").
        const cosAlpha = THREE.MathUtils.clamp(
            (L1 * L1 + L2 * L2 - d * d) / (2 * L1 * L2), -1, 1
        );
        const alpha = Math.acos(cosAlpha);   // interior elbow angle
        const elbowBend = Math.PI - alpha;   // how much to flex from straight

        const cosPhi = THREE.MathUtils.clamp(
            (L1 * L1 + d * d - L2 * L2) / (2 * L1 * d), -1, 1
        );
        const phi = Math.acos(cosPhi);

        // Now we know:
        //   shoulder rotation = aim along toTarget, then rotate by phi
        //                       around the elbow-plane normal
        //   elbow rotation    = bend by elbowBend around the same normal
        //
        // Build them as quaternions in WORLD space, then convert to
        // each bone's local space via parent inversion.
        const planeNormal = new THREE.Vector3().crossVectors(toTarget, upWorld);
        if (planeNormal.lengthSq() < 1e-6) planeNormal.set(1, 0, 0);  // degenerate fallback
        planeNormal.normalize();

        // Shoulder world-space target direction for upper-arm.
        const shoulderDir = toTarget.clone().applyAxisAngle(planeNormal, phi);

        // Convert directions into each bone's PARENT-local space, then
        // build setFromUnitVectors quaternions. The "rest forward" of
        // a VRM upperArm is roughly along the bone toward its child;
        // we read that from the current rest pose to be model-agnostic.
        const restDirUpper = pLower.clone().sub(pUpper).normalize();
        const restDirLower = pHand.clone().sub(pLower).normalize();

        // Upper-arm: rotate from restDirUpper → shoulderDir (parent space).
        const upperParent = upper.parent;
        if (upperParent) upperParent.updateWorldMatrix(true, false);
        const restDirUpperLocal = restDirUpper.clone();
        const shoulderDirLocal  = shoulderDir.clone();
        if (upperParent) {
            _m0.copy(upperParent.matrixWorld).invert();
            restDirUpperLocal.transformDirection(_m0);
            shoulderDirLocal.transformDirection(_m0);
        }
        const upperRot = new THREE.Quaternion().setFromUnitVectors(
            restDirUpperLocal.normalize(),
            shoulderDirLocal.normalize(),
        );

        // Lower-arm: rotate by elbowBend around the plane normal,
        // expressed in upper-arm-local space (since lowerArm.parent ≈
        // upperArm; the elbow's local axis is parent-space).
        const planeNormalLocal = planeNormal.clone();
        upper.updateWorldMatrix(true, false);
        _m0.copy(upper.matrixWorld).invert();
        planeNormalLocal.transformDirection(_m0).normalize();
        const elbowRot = new THREE.Quaternion().setFromAxisAngle(
            planeNormalLocal, elbowBend
        );

        // Apply with weight. Layered on top of rest pose so the bone's
        // animation-driven offsets aren't completely lost.
        const restUpperQ = this._restPose.get(isLeft ? 'leftUpperArm' : 'rightUpperArm');
        const restLowerQ = this._restPose.get(isLeft ? 'leftLowerArm' : 'rightLowerArm');
        if (restUpperQ) {
            _q0.copy(restUpperQ).multiply(upperRot);
            upper.quaternion.slerp(_q0, weight);
        } else {
            upper.quaternion.slerp(upperRot, weight);
        }
        if (restLowerQ) {
            _q0.copy(restLowerQ).multiply(elbowRot);
            lower.quaternion.slerp(_q0, weight);
        } else {
            lower.quaternion.slerp(elbowRot, weight);
        }
    }
}
