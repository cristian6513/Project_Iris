/**
 * IRIS breeze: a soft, gusting air current over the VRM's hair and clothing, so the avatar
 * does not look frozen while it waits (the user's idea after qa11).
 *
 * The VRM's spring bones (three-vrm) already simulate hair, skirts and ribbons; each joint
 * pulls its tail along `gravityDir * gravityPower` plus a stiffness pull back to its rest
 * axis. The breeze adds a slowly turning, world-space wind to that pull, every frame and
 * before `vrm.update()`; a small flutter, offset per joint, keeps neighbouring locks from
 * moving as one block.
 *
 * Softness has a floor that holds for any model: the joint's restoring pull is never weaker
 * than |stiffness − gravity| (the two may point opposite ways), and the push is a share of
 * that floor, so at rest a joint leans at most ~4.6° from where it would hang without wind.
 * A joint whose stiffness and gravity nearly cancel gets almost no wind (Codex review B01:
 * a stiffness-only push swung such a joint 79°). Long chains add their joints' leans up, so
 * hair tips drift more than roots. Sudden changes make a chain whip, so the breeze fades in
 * and out over seconds, and a bouncy joint (drag under the default 0.4) gets a share of the
 * wind in proportion: on the native solver an 8-joint chain bends at most ~6° per joint, an
 * unusually bouncy one (drag 0.1) under 5°, against 20–131° without this. Very long and very
 * bouncy chains still flick at their last segments, as they do whenever the avatar moves.
 *
 * Only hair, clothing and accessories (tails and animal ears included) get the breeze: body
 * physics (breasts, buttocks, thighs, cheeks) and humanoid bones are left alone (B02). The
 * authored gravity is restored exactly when the breeze turns off.
 */

// Soft by design: at the top of a gust a joint leans at most atan(0.07 × 1.14) ≈ 4.6°.
export const BREEZE_LEAN = 0.07;
export const BREEZE_KEY = 'IRIS.avatarBreeze.v1';

// From the room's side, slightly towards the camera, so the sway reads from the front.
const HEADING = Math.atan2(0.35, 1);
const WANDER = 0.35; // radians the direction drifts either way
const FLUTTER = 0.14; // share of the push that ripples quickly
const FADE_IN = 3; // seconds
const FADE_OUT = 1.2;
// Secondary physics that belongs to the body, not to hair or clothing.
const BODY_PHYSICS = /breast|bust|boob|oppai|chest|nipple|butt|glute|belly|thigh|cheek|胸|おっぱい|尻|太もも/i;
const REDUCED_MOTION = '(prefers-reduced-motion: reduce)';

export const prefersReducedMotion = () => globalThis.matchMedia?.(REDUCED_MOTION)?.matches === true;

/** Call back when the system's «reduce motion» changes; returns the way to stop listening. */
export function onReducedMotionChange(callback) {
    const query = globalThis.matchMedia?.(REDUCED_MOTION);
    query?.addEventListener?.('change', callback);
    return () => query?.removeEventListener?.('change', callback);
}

/** 0.2..1 gusts: three slow sines that never repeat in step, softened at the bottom. */
export function gust(time) {
    const raw = 0.6 * Math.sin(time * 0.31) + 0.3 * Math.sin(time * 0.73 + 1.3) + 0.1 * Math.sin(time * 1.9 + 0.4);
    const level = 0.5 + 0.5 * raw;
    return 0.2 + 0.8 * level * level;
}

/** The wind's direction at a moment (world space, unit length) and its gust level. */
export function windAt(time) {
    const heading = HEADING + WANDER * Math.sin(time * 0.11 + 0.7);
    const x = Math.cos(heading), y = 0.04, z = Math.sin(heading);
    const length = Math.hypot(x, y, z);
    return { x: x / length, y: y / length, z: z / length, gust: gust(time) };
}

export function readBreezePreference(storage = () => globalThis.localStorage) {
    try {
        return storage()?.getItem(BREEZE_KEY) !== 'off';
    } catch {
        return true;
    }
}

// One switch for every avatar (the Command Center toggle), kept in memory and in storage.
const preference = { enabled: null };

export function breezeEnabled() {
    if (preference.enabled === null) preference.enabled = readBreezePreference();
    return preference.enabled;
}

export function setBreezeEnabled(enabled, storage = () => globalThis.localStorage) {
    preference.enabled = Boolean(enabled);
    try { storage()?.setItem(BREEZE_KEY, preference.enabled ? 'on' : 'off'); } catch {}
    return preference.enabled;
}

export class AvatarBreeze {
    constructor({
        isEnabled = breezeEnabled,
        reducedMotion = prefersReducedMotion,
    } = {}) {
        this._isEnabled = isEnabled;
        this._reducedMotion = reducedMotion;
        this.time = 0;
        this._joints = new Map(); // joint → authored { dir: {x, y, z}, power, floor, phase, calm }
        this._applied = false;
        this._fade = 0; // 0..1, how much of the breeze is blowing
    }

    /** Take the VRM's hair and clothing spring joints; their authored gravity is the rest state. */
    attach(vrm) {
        this.detach();
        const joints = vrm?.springBoneManager?.joints;
        if (!joints) return 0;
        const humanoid = new Set(Object.values(vrm.humanoid?.humanBones || {}).map((bone) => bone?.node).filter(Boolean));
        const springBones = new Set([...joints].map((joint) => joint?.bone).filter(Boolean));
        for (const joint of joints) {
            const settings = joint?.settings;
            if (!settings?.gravityDir || !Number.isFinite(settings.gravityPower)) continue;
            if (!joint.bone || humanoid.has(joint.bone) || isBodyPhysics(joint.bone, springBones)) continue;
            const { x, y, z } = settings.gravityDir;
            const stiffness = Number.isFinite(settings.stiffness) ? Math.max(0, settings.stiffness) : 1;
            const gravity = Math.hypot(x, y, z) * settings.gravityPower;
            const phase = this._joints.size * 0.37; // neighbours in a chain ripple, not in step
            // A bouncy joint (drag under the default 0.4) gets a share of the wind in proportion.
            const drag = Number.isFinite(settings.dragForce) ? settings.dragForce : 0.4;
            const calm = Math.min(1, Math.max(0.05, drag / 0.4));
            this._joints.set(joint, {
                dir: { x, y, z }, power: settings.gravityPower, floor: Math.abs(stiffness - gravity), phase, calm,
            });
        }
        return this._joints.size;
    }

    detach() {
        this._restore();
        this._joints.clear();
        this._fade = 0;
    }

    get active() {
        return this._isEnabled() && !this._reducedMotion() && this._joints.size > 0;
    }

    /** Call once per frame, before vrm.update(delta). */
    apply(delta) {
        const step = Number.isFinite(delta) ? Math.max(0, Math.min(0.25, delta)) : 0;
        const active = this.active;
        this._fade = active ? Math.min(1, this._fade + step / FADE_IN) : Math.max(0, this._fade - step / FADE_OUT);
        if (this._fade === 0) {
            this._restore();
            return;
        }
        this.time += step;
        const wind = windAt(this.time);
        for (const [joint, { dir, power: authored, floor, phase, calm }] of this._joints) {
            const ripple = 1 + FLUTTER * Math.sin(this.time * 5.3 + phase);
            const push = BREEZE_LEAN * floor * calm * wind.gust * ripple * this._fade;
            const x = dir.x * authored + wind.x * push;
            const y = dir.y * authored + wind.y * push;
            const z = dir.z * authored + wind.z * push;
            const power = Math.hypot(x, y, z);
            if (power > 1e-6) joint.settings.gravityDir.set(x / power, y / power, z / power);
            joint.settings.gravityPower = power;
        }
        this._applied = true;
    }

    _restore() {
        if (!this._applied) return;
        for (const [joint, { dir, power }] of this._joints) {
            joint.settings.gravityDir.set(dir.x, dir.y, dir.z);
            joint.settings.gravityPower = power;
        }
        this._applied = false;
    }
}

/** A joint is body physics when it, or the spring chain it hangs from, is named like a body part. */
function isBodyPhysics(bone, springBones) {
    for (let node = bone; node && springBones.has(node); node = node.parent) {
        if (BODY_PHYSICS.test(node.name || '')) return true;
    }
    return false;
}
