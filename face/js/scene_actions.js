/**
 * IRIS Scene Actions — Contextual Animations Manager
 *
 * Maps action names (from LLM JSON) to avatar movement,
 * object activation, and visual effects in the 3D room.
 */

import * as THREE from 'three';
import { ROOM_HOTSPOTS, getHotspotForAction } from './room_hotspots.js?v=30';
import { getRoomActionCommand } from './room_actions.js?v=30';
import { BOOK, CONJURED_BOOK, CONJURED_TABLET, ConjuredProp, HandProp, HeldProps } from './hand_prop.js';
import { TeleportEffect } from './teleport_effect.js?v=2';

function lerpAngle(from, to, t) {
    const delta = Math.atan2(Math.sin(to - from), Math.cos(to - from));
    return from + delta * t;
}

const ROUTE_POINT = new THREE.Vector3();
const ROUTE_TANGENT = new THREE.Vector3();

// A trip shorter than this is a shuffle on the spot, not a walk.
const ROOM_FLOOR_Y = -1.19;
const LANDING_FADE = 0.05;    // out of sight: she is simply standing when she appears
const CHAIR_ROLL = 0.9;       // room units, ROLL_UNITS in build_chair.py
const CHAIR_SECONDS = 1.1;
const CHAIR_CLEARANCE = 1.0;  // she has left its way
const SHORT_TRIP = 0.35;
// How far from a spot, in room units, the camera sets off to its view.
const VIEW_JOINS_AT = 0.55;
// Milliseconds of soft start and stop in a walk.
const WALK_RAMP = 450;

/** Share of a path walked after `t` ms: constant speed, with a soft start and stop. */
function travelled(t, total, ramp) {
    if (t <= 0) return 0;
    if (t >= total) return 1;
    const cruise = 1 / (total - ramp);
    if (t < ramp) return cruise * t * t / (2 * ramp);
    const left = total - t;
    if (left < ramp) return 1 - cruise * left * left / (2 * ramp);
    return cruise * (t - ramp / 2);
}

/** Milliseconds to turn in place through this angle. */
function turnTime(fromYaw, toYaw) {
    return 280 + 420 * Math.abs(Math.atan2(Math.sin(toYaw - fromYaw), Math.cos(toYaw - fromYaw))) / Math.PI;
}

// ── Action definitions ─────────────────────────────────────

// ════════════════════════════════════════════════════════════
// SceneActionManager
// ════════════════════════════════════════════════════════════

export class SceneActionManager {
    /**
     * @param {THREE.Scene} scene
     * @param {object} avatarManager  - AvatarManager instance
     * @param {object} room           - RoomEnvironment instance
     * @param {object} effects        - { floatingBook, thinkingCloud, dataStream, memorySymbol }
     */
    constructor(scene, avatarManager, room, effects, options = {}) {
        this.scene = scene;
        this.avatar = avatarManager;
        this.room = room;
        this.effects = effects;
        this.idleSystem = options.idleSystem || null;
        this.onEmotion = options.onEmotion || null;
        this.onCameraPreset = options.onCameraPreset || null;
        this.onLanded = options.onLanded || null;
        this.reducedMotion = options.reducedMotion ?? (() => false);

        this.currentAction = null;
        this.currentRoomAction = null;
        this.currentHotspot = null;
        this.isMoving = false;
        this.moveTarget = new THREE.Vector3();
        this.moveSpeed = 1.1; // units per second (slow enough to see legs)
        this.hotspotTransition = null;
        this.actionLookAtTarget = null;
        this.actionLookAtWeight = 0;
        this._heldYaw = null;
        this._atSpot = null;   // the spot she is standing in; she leaves it by its approach
        this.orders = 0;       // counts orders, so a routine can tell it was overruled
        this.commandOrigin = 'order';
        this.teleportEffect = new TeleportEffect({ scene, floorY: ROOM_FLOOR_Y });

        this.actionQueue = [];
        this.actionTimer = null;

        // Monitor activated state
        this._monitorActive = false;

        // Walk cycle state
        this._walkPhase = 0;   // advances while moving
        this._walkBlend = 0;   // 0 idle → 1 walking, eased
    }

    setIdleSystem(idleSystem) {
        this.idleSystem = idleSystem;
    }

    /**
     * Execute a scene action by name
     * @param {string} actionName - room action id or legacy action alias
     */
    execute(actionName, { origin = 'order' } = {}) {
        // A routine never outranks an order: see the priorities in room_routines.js.
        this.commandOrigin = origin;
        if (origin !== 'routine') this.orders += 1;
        this.teleportEffect.cancel();
        const command = getRoomActionCommand(actionName);
        const action = command?.action;
        const hotspot = action?.targetHotspot
            ? ROOM_HOTSPOTS[action.targetHotspot]
            : getHotspotForAction(actionName);
        if (!action) {
            if (hotspot) return this.goToHotspot(hotspot.id);
            console.warn(`[SceneAction] Unknown action: ${actionName}`);
            return false;
        }
        if (!action.enabled) {
            console.info(`[SceneAction] Disabled room action: ${action.id}`);
            return false;
        }
        if (this.currentRoomAction && !this.currentRoomAction.canInterrupt) {
            console.info(`[SceneAction] Action ${this.currentRoomAction.id} cannot be interrupted.`);
            return false;
        }
        // Asked again while seated or on the way: nothing to restart.
        if (this.hotspotTransition?.roomAction?.id === action.id
            || this.idleSystem?.isSeated?.(action.animationName)) {
            return true;
        }

        // Clear previous action effects
        this._clearAllEffects();
        this._deactivateAll();
        if (this.actionTimer) clearTimeout(this.actionTimer);

        // Seated, she reads where she is instead of going to the bookshelf.
        const reading = action.animationName === 'book_reading' ? this.idleSystem?.readingSeat?.() : null;
        // Between seats joined by a clip she stays put: sitting, lying down, reading.
        if (hotspot && this.idleSystem?.shiftSeat?.(reading ?? action.animationName)) {
            this.currentAction = actionName;
            this.currentRoomAction = action;
            if (!reading) {
                this.currentHotspot = hotspot.id;
                this._atSpot = hotspot;
            }
            return true;
        }

        this.currentAction = actionName;
        this.currentRoomAction = action;
        this._setActionLookAt(action);

        // Start moving avatar
        if (hotspot) {
            this.goToHotspot(hotspot.id, { actionName, roomAction: action, skipClear: true });
        } else if (action.targetPos) {
            this.moveTarget.copy(action.targetPos);
            this.isMoving = true;
        }

        // Activate room object
        if (command.activate) {
            this._activateObject(command.activate);
        }

        // Show effect
        if (command.effect) {
            const effectPos = hotspot
                ? new THREE.Vector3(...hotspot.position)
                : action.targetPos;
            this._showEffect(command.effect, effectPos);
        }

        // A timed action counts from her arrival: the walk takes as long as the distance.
        if (!hotspot) this._startActionTimer(action);
        return true;
    }

    /** She stays because she was told to, not because a routine took her there. */
    heldByOrder() {
        if (this.commandOrigin === 'routine') return false;
        return Boolean(this.idleSystem?.isSeated?.() || this.hotspotTransition?.roomAction?.endMode === 'stay');
    }

    isWalking() {
        return Boolean(this.hotspotTransition || this.isMoving);
    }

    /** Something is under way that a routine must not cut: a walk, a timed action, getting up. */
    isBusy() {
        return this.isWalking() || this.teleportEffect.active || Boolean(this.currentRoomAction?.duration)
            || Boolean(this.idleSystem?.isGettingUp?.());
    }

    /** Can this order be played out? A package without the room clips must not walk her around. */
    canRun(actionName) {
        const action = getRoomActionCommand(actionName)?.action;
        return Boolean(action?.enabled && this.idleSystem?.canHold?.(action.animationName)
            && (!ROOM_HOTSPOTS[action.targetHotspot]?.teleport || this._canUseLight()));
    }

    /** Something in hand, or lying down: back to where she can talk. */
    freeHands() {
        return this.idleSystem?.putAway?.() ?? false;
    }

    /** She can't answer like this: she has to come away from where she stands. */
    hasBackToRoom() {
        return Boolean(this._atSpot?.backToRoom);
    }

    _canUseLight() {
        return Boolean(this.avatar?.avatar?.vrm && this.room?.quality !== 'low' && !this.reducedMotion());
    }

    /** A conversation jump needs a place a routine took her to. */
    canTeleport() {
        return Boolean(this._canUseLight() && this.commandOrigin === 'routine'
            && this.idleSystem?.isSeated?.() && !this.isBusy());
    }

    /** Speech can interrupt a routine's effect. An explicit room order keeps priority. */
    cancelTeleport() {
        if (this.commandOrigin === 'routine') return this.teleportEffect.cancel();
    }

    /** To the middle of the room, in a line of light, with whatever she holds. */
    teleportHome() {
        const avatar = this.avatar?.avatar;
        if (!this.canTeleport()) return false;
        return this.teleportEffect.start({
            vrm: avatar.vrm,
            root: avatar.group,
            color: this.room?.accent?.(),
            land: () => {
                this._props?.drop();
                this.reset({ clearLocomotion: true });
                this.idleSystem?.resetRoomPlacement?.({ fade: LANDING_FADE });
                this.onLanded?.();
            },
            settle: () => avatar.settleSprings?.(),
        });
    }

    /**
     * Use the line of light when allowed, otherwise apply the placement immediately.
     */
    _inLight(land) {
        const avatar = this.avatar?.avatar;
        const settle = () => avatar?.settleSprings?.();
        if (this._canUseLight() && this.teleportEffect.start({
            vrm: avatar.vrm, root: avatar.group, color: this.room?.accent?.(), land, settle,
        })) return true;
        land();
        settle();
        return true;
    }

    /** Onto a spot she can't walk onto (`teleport`): she lands there already in its pose. */
    _lightIn(hotspot, roomAction, position, yaw) {
        return this._inLight(() => {
            const group = this.avatar.avatar.group;
            this._props?.drop();
            this.idleSystem?.resetRoomPlacement?.({ fade: LANDING_FADE });
            group.position.set(position.x, group.position.y, position.z);
            group.rotation.y = yaw;
            this._heldYaw = roomAction?.endMode === 'stay' ? yaw : null;
            this._atSpot = hotspot;
            this.idleSystem?.resume?.();
            this._startActionTimer?.(roomAction);
            this._playHotspotPose(roomAction || hotspot);
        });
    }

    /** Down from such a spot: the light takes her to the middle of the room, then `then`. */
    _lightOut(then) {
        return this._inLight(() => {
            this._props?.drop();
            this.reset({ clearLocomotion: true });
            this.idleSystem?.resetRoomPlacement?.({ fade: LANDING_FADE });
            then();
        });
    }

    /** Link the shaders of the jump while the page loads: they would stall its first frame. */
    prepareTeleport(renderer, camera) {
        const group = this.avatar?.avatar?.group;
        if (!group || !renderer || !camera) return;
        const target = new THREE.WebGLRenderTarget(4, 4);
        const previous = renderer.getRenderTarget();
        this.teleportEffect.prepare(group, () => {
            renderer.setRenderTarget(target);
            renderer.render(this.scene, camera);
            renderer.setRenderTarget(previous);
        });
        target.dispose();
    }

    _startActionTimer(action) {
        if (!action?.duration) return;
        this.actionTimer = setTimeout(() => {
            this._finishAction();
        }, action.duration);
    }

    goToHotspot(hotspotId, opts = {}) {
        const hotspot = ROOM_HOTSPOTS[hotspotId];
        if (!hotspot || !hotspot.enabled) {
            console.warn(`[SceneAction] Hotspot unavailable: ${hotspotId}`);
            return false;
        }

        const avatar = this.avatar?.avatar;
        const group = avatar?.group;
        if (!group) return false;

        // A seat she reached in a line of light (the desk edge) has no way down: the light takes
        // her to the middle of the room, and the trip starts from there.
        if (this.idleSystem?.leavesByLight?.() && !opts.fromLight) {
            if (this._atSpot === hotspot) return true; // already there
            return this._lightOut(() => this.goToHotspot(hotspotId, { ...opts, fromLight: true }));
        }

        if (!opts.skipClear) {
            this._clearAllEffects();
            this._deactivateAll();
            if (this.actionTimer) clearTimeout(this.actionTimer);
        }

        const roomAction = opts.roomAction || null;
        this.currentHotspot = hotspot.id;
        this.currentAction = opts.actionName || roomAction?.id || hotspot.id;
        this.currentRoomAction = roomAction || this.currentRoomAction;
        if (roomAction) this._setActionLookAt(roomAction);
        this.isMoving = false;
        if (this.hotspotTransition?.elapsed > 0) this._atSpot = null; // she had already left it
        // Into a spot she can't walk onto she leaves as she is, seated or not: no getting up.
        if (!hotspot.teleport) this.idleSystem?.standUp?.();
        this.idleSystem?.pause?.();

        const targetPos = new THREE.Vector3(...hotspot.position);
        targetPos.y = group.position.y;
        const restYaw = avatar?.restYaw ?? 0;
        let targetYaw = restYaw + (hotspot.rotation?.[1] || 0);
        if (roomAction?.faceTarget && Array.isArray(roomAction.lookAtTarget)) {
            const faceTarget = new THREE.Vector3(...roomAction.lookAtTarget);
            const dir = new THREE.Vector3().subVectors(faceTarget, targetPos);
            if (dir.lengthSq() > 1e-6) {
                targetYaw = restYaw + Math.atan2(dir.x, dir.z);
            }
        }
        if (typeof roomAction?.facingYawOffset === 'number') {
            targetYaw += roomAction.facingYawOffset;
        }
        if (hotspot.teleport) {
            this.hotspotTransition = null;
            this._lightIn(hotspot, roomAction, targetPos, targetYaw);
        } else {
            this.hotspotTransition = {
                hotspot,
                elapsed: 0,
                duration: opts.duration ?? 900,
                fromPosition: group.position.clone(),
                toPosition: targetPos,
                fromYaw: group.rotation.y,
                toYaw: targetYaw,
                roomAction,
                route: this._planRoute(hotspot, group.position, targetPos, group.rotation.y, targetYaw, restYaw),
            };
        }

        const expression = roomAction?.expression ?? hotspot.defaultExpression;
        if (expression) {
            if (this.onEmotion) this.onEmotion(expression);
            else this.avatar?.setEmotion?.(expression);
        }
        const cameraPreset = roomAction?.cameraPreset ?? hotspot.cameraPreset;
        if (cameraPreset && this.onCameraPreset) {
            this.onCameraPreset(cameraPreset, roomAction || hotspot);
        }

        return true;
    }

    /**
     * The path of a walk, timed by distance and through the way points of the spot she leaves
     * and the one she heads to. Null for a shuffle on the spot.
     */
    _planRoute(hotspot, from, to, fromYaw, toYaw, restYaw) {
        if (from.distanceTo(to) < SHORT_TRIP) return null;
        const point = ([x, , z]) => new THREE.Vector3(x, from.y, z);
        const out = [...(this._atSpot?.approach || [])].reverse().map(point);
        // She does not walk away from a spot to come in by its way point.
        const into = (hotspot.approach || []).map(point).filter(way => way.distanceTo(to) < from.distanceTo(to));
        const ways = [...out, ...into].filter(way => way.distanceTo(from) > 0.1 && way.distanceTo(to) > 0.1);
        const curve = new THREE.CatmullRomCurve3([from.clone(), ...ways, to.clone()], false, 'centripetal');
        const length = curve.getLength();
        // The walk of this trip (a variant may be slower) sets its pace: feet stay on the floor.
        const { clip: walkClip = 'walk', pace = 1 } = this.idleSystem?.chooseWalk?.() ?? {};
        const heading = (u) => {
            curve.getTangentAt(u, ROUTE_TANGENT);
            return restYaw + Math.atan2(ROUTE_TANGENT.x, ROUTE_TANGENT.z);
        };
        return {
            curve,
            heading,
            length,
            walkClip,
            turnIn: turnTime(fromYaw, heading(0)),
            walk: 1000 * length / (this.moveSpeed * pace) + WALK_RAMP,
            turnOut: turnTime(heading(1), toYaw),
        };
    }

    /** Turn to set off, follow the path's tangent, turn to the final yaw. True on arrival. */
    _followRoute(transition, group, delta) {
        const { curve, heading, length, turnIn, walk, turnOut } = transition.route;
        const { smoothstep } = THREE.MathUtils;
        const elapsed = transition.elapsed;
        const walkStart = turnIn * 0.6;
        const walkEnd = walkStart + walk;
        const progress = travelled(elapsed - walkStart, walk, Math.min(WALK_RAMP, walk / 2));
        const settling = smoothstep(elapsed, walkEnd, walkEnd + turnOut);
        transition.speed = delta > 0 ? (progress - (transition.progress || 0)) * length / delta : 0;
        transition.progress = progress;
        curve.getPointAt(progress, ROUTE_POINT);
        group.position.x = ROUTE_POINT.x;
        group.position.z = ROUTE_POINT.z;
        if (elapsed < turnIn) {
            group.rotation.y = lerpAngle(transition.fromYaw, heading(progress), smoothstep(elapsed, 0, turnIn));
        } else if (elapsed < walkEnd) {
            // She leans into a bend rather than snapping to the path's direction.
            group.rotation.y = lerpAngle(group.rotation.y, heading(progress), Math.min(delta * 6, 1));
        } else {
            transition.settleFrom ??= group.rotation.y;
            group.rotation.y = lerpAngle(transition.settleFrom, transition.toYaw, settling);
        }
        // The gait runs through the last turn, so she steps around instead of sliding.
        transition.walkProgress = Math.min(progress, 0.9);
        return elapsed >= walkEnd + turnOut;
    }

    /**
     * The spot for the camera to frame. It leaves as she finishes getting up and joins her at the
     * end of a walk: a seat's view fits her seated, and earlier it would cut off her head.
     */
    framedSpot() {
        const trip = this.hotspotTransition;
        const near = !trip || this.idleSystem?.isGettingUp?.(2.4) ? this._atSpot
            : !trip.route || (1 - (trip.progress || 0)) * trip.route.length < VIEW_JOINS_AT ? trip.hotspot : null;
        return near?.closeView && !this.idleSystem?.isLowInSeat?.() ? null : near;
    }

    /** Clear current action and return to idle */
    reset({ clearLocomotion = false } = {}) {
        this._clearAllEffects();
        this._deactivateAll();
        this.isMoving = false;
        // A walk to a place she stays in ends there, even if the speech that asked for it ends first.
        if (clearLocomotion || this.hotspotTransition?.roomAction?.endMode !== 'stay') {
            if (this.hotspotTransition) this._atSpot = null;
            this.hotspotTransition = null;
        }
        this.currentAction = null;
        this.currentRoomAction = null;
        this.currentHotspot = null;
        this._clearActionLookAt();
        this._heldYaw = null;
        if (this.actionTimer) clearTimeout(this.actionTimer);
        this.actionTimer = null;
        if (clearLocomotion) {
            this._atSpot = null;
            const group = this.avatar?.avatar?.group;
            if (group && this._lastBounce) group.position.y -= this._lastBounce;
            this._lastBounce = 0;
            this._walkBlend = 0;
            this._walkPhase = 0;
        }
    }

    /** What she holds follows the clip playing, whatever else owns the frame. */
    _updateProps(delta) {
        const object = (config) => this.room?.getObject?.(config.name);
        if (!this._props) {
            if (![BOOK, CONJURED_BOOK, CONJURED_TABLET].every(object)) return;
            this._props = new HeldProps({
                shelf: { prop: new HandProp(object(BOOK), BOOK), hand: BOOK.hand },
                conjured: { prop: new ConjuredProp(object(CONJURED_BOOK), CONJURED_BOOK), hand: CONJURED_BOOK.hand },
                tablet: {
                    prop: new ConjuredProp(object(CONJURED_TABLET), {
                        ...CONJURED_TABLET, replaces: this.room.getObject(CONJURED_TABLET.replaces),
                    }),
                    hand: CONJURED_TABLET.hand,
                },
            });
        }
        // On a jump she lets go at once: what she conjured goes in its own flash, ahead of the line.
        this._props.update(delta, this.avatar?.avatar?.vrm, this.teleportEffect.active ? null : this.idleSystem?.heldProp?.());
    }

    /** The chair keeps out of her way: it stays back while she is at its spot or coming to it. */
    _updateChair(delta) {
        const chair = this.room?.getObject?.('chair');
        const group = this.avatar?.avatar?.group;
        if (!chair || !group) return;
        this._chairHome ??= chair.position.z;
        const placed = this.idleSystem?.chairPlace?.() ?? null;
        if (placed !== null) {
            this._chairOut = placed;
        } else {
            const [x, , z] = ROOM_HOTSPOTS.deskSeatSpot.position;
            const near = Math.hypot(group.position.x - x, group.position.z - z) < CHAIR_CLEARANCE;
            const wanted = this.hotspotTransition?.hotspot?.rollsChair || near ? 1 : 0;
            const step = delta / CHAIR_SECONDS;
            this._chairOut = THREE.MathUtils.clamp((this._chairOut ?? 0) + Math.sign(wanted - (this._chairOut ?? 0)) * step, 0, 1);
        }
        const eased = placed !== null ? placed : THREE.MathUtils.smoothstep(this._chairOut, 0, 1);
        chair.position.z = this._chairHome + CHAIR_ROLL * eased;
    }

    /** Everything back in its place: the avatar is about to be replaced. */
    dropProps() {
        this.teleportEffect.cancel();
        this._props?.drop();
    }

    /** Call each frame */
    update(delta, elapsed) {
        this.teleportEffect.update(delta);
        this._updateProps?.(delta);
        this._updateChair?.(delta);
        const avatar = this.avatar?.avatar;
        const group = avatar?.group;
        const restYaw = avatar?.restYaw ?? 0;

        // An explicit animation owns root yaw and humanoid bones. Scene
        // Actions used to keep drifting the avatar toward `restYaw`, applying
        // action look-at and clearing walk bones after IdleSystem had already
        // posed them. That produced the intermittent "tries to return / do
        // something else" tug. Freeze locomotion overlays until the explicit
        // beat ends; room effects may continue independently.
        const explicitAnimationActive = Boolean(
            this.idleSystem?.isExplicitAnimationActive?.()
            || this.idleSystem?._vrmaExternalClip
            || this.idleSystem?.currentPose,
        ) && !this.idleSystem?.isWalking?.();
        if (explicitAnimationActive) {
            if (group && this._lastBounce) group.position.y -= this._lastBounce;
            this._walkBlend = 0;
            this._walkPhase = 0;
            this._lastBounce = 0;
            for (const fx of Object.values(this.effects)) {
                if (fx && fx.active) fx.update(delta, elapsed);
            }
            return;
        }

        if (this._updateHotspotTransition(group, delta)) {
            this._updateActionLookAt();
            this._updateWalkCycle(delta, avatar);
            for (const fx of Object.values(this.effects)) {
                if (fx && fx.active) fx.update(delta, elapsed);
            }
            return;
        }

        // Constant-velocity avatar movement (so we have time to see the
        // walk cycle, instead of an exponential lerp that arrives instantly)
        if (this.isMoving && group) {
            const current = group.position;
            const target = this.moveTarget;
            const remaining = new THREE.Vector3().subVectors(target, current);
            const dist = remaining.length();
            const step = this.moveSpeed * delta;

            if (dist <= step + 0.001) {
                current.copy(target);
                this.isMoving = false;
            } else {
                remaining.normalize().multiplyScalar(step);
                current.add(remaining);
            }

            // Face direction of movement. Add restYaw so the character's
            // intrinsic facing direction is preserved.
            if (dist > 0.15) {
                const dir = new THREE.Vector3().subVectors(target, current).normalize();
                const heading = Math.atan2(dir.x, dir.z);
                group.rotation.y = lerpAngle(
                    group.rotation.y, restYaw + heading, Math.min(delta * 4, 1)
                );
            }
        } else if (group) {
            // Idle: drift back to rest facing so the character returns to
            // camera after any movement-driven rotation. Room actions that
            // stay at a prop (PC/window) hold their final yaw instead.
            const targetIdleYaw = this.currentRoomAction?.endMode === 'stay' && Number.isFinite(this._heldYaw)
                ? this._heldYaw
                : restYaw;
            group.rotation.y = lerpAngle(group.rotation.y, targetIdleYaw, Math.min(delta * 4, 1));
        }

        // Walk-cycle bone animation (only when VRM humanoid exists).
        this._updateWalkCycle(delta, avatar);
        this._updateActionLookAt();

        // Update effects
        for (const fx of Object.values(this.effects)) {
            if (fx && fx.active) fx.update(delta, elapsed);
        }
    }

    /**
     * Drive leg + arm swing while moving. Blend in/out smoothly so the
     * pose melts back to the avatar's rest pose when movement stops.
     */
    _updateWalkCycle(delta, avatar) {
        const vrm = avatar?.vrm;
        if (!vrm?.humanoid) return;

        // Ease blend factor toward current moving state. Hotspot transitions
        // turn IRIS first, then walk, so the gait waits until translation
        // actually starts instead of marching in place robotically.
        const transitionWalk = this.hotspotTransition?.walkProgress ?? 0;
        const walkingNow = this.isMoving || (transitionWalk > 0.02 && transitionWalk < 0.995);
        const speed = this.isMoving ? this.moveSpeed : (this.hotspotTransition?.speed ?? 0);
        const walkClip = this.hotspotTransition?.route?.walkClip;
        if (this.idleSystem?.setWalking?.(walkingNow, speed, walkClip)) return;
        const targetBlend = walkingNow ? 1 : 0;
        this._walkBlend += (targetBlend - this._walkBlend) * Math.min(delta * 5, 1);

        const get = (name) =>
            vrm.humanoid.getNormalizedBoneNode?.(name) ?? vrm.humanoid.getBoneNode?.(name);

        if (!walkingNow && this.idleSystem?.currentPose) {
            if (this.avatar?.avatar?.group && this._lastBounce) {
                this.avatar.avatar.group.position.y -= this._lastBounce;
            }
            this._walkBlend = 0;
            this._walkPhase = 0;
            this._lastBounce = 0;
            return;
        }

        // Even when fully idle, write 0s for one extra frame to clear any
        // residual offsets, then bail out.
        if (this._walkBlend < 0.005 && !walkingNow) {
            if (this._walkPhase !== 0) {
                // If IdleSystem just started an interaction pose, do not
                // write zeros over its seated/working bones. The transition
                // already faded the walk blend down; clearing counters is
                // enough to stop the gait without stomping the new pose.
                if (!this.idleSystem?.currentPose) {
                    ['leftUpperLeg','rightUpperLeg','leftLowerLeg','rightLowerLeg','hips']
                        .forEach(n => { const b = get(n); if (b) b.rotation.x = b.rotation.z = 0; });
                    const lUp = get('leftUpperArm');
                    const rUp = get('rightUpperArm');
                    if (lUp) lUp.rotation.x = 0;
                    if (rUp) rUp.rotation.x = 0;
                }
                this._walkPhase = 0;
                if (avatar.group && this._lastBounce) avatar.group.position.y -= this._lastBounce;
                this._lastBounce = 0;
            }
            return;
        }

        this._walkPhase += delta * 5.4; // stride rate

        const s = Math.sin(this._walkPhase);
        const c = Math.cos(this._walkPhase);
        const lStep = s;
        const rStep = -s;
        const lKnee = Math.max(0, Math.sin(this._walkPhase + Math.PI * 0.32));
        const rKnee = Math.max(0, Math.sin(this._walkPhase + Math.PI * 1.32));
        const b = this._walkBlend;

        // Legs: smaller hip swing plus explicit knee bend reads more like a
        // walk cycle and less like stiff sliding at room scale.
        const lUL = get('leftUpperLeg');
        const rUL = get('rightUpperLeg');
        const lLL = get('leftLowerLeg');
        const rLL = get('rightLowerLeg');
        const lFoot = get('leftFoot');
        const rFoot = get('rightFoot');
        if (lUL) lUL.rotation.x =  lStep * 0.44 * b;
        if (rUL) rUL.rotation.x =  rStep * 0.44 * b;
        if (lLL) lLL.rotation.x =  lKnee * 0.82 * b;
        if (rLL) rLL.rotation.x =  rKnee * 0.82 * b;
        if (lFoot) lFoot.rotation.x = -lKnee * 0.24 * b;
        if (rFoot) rFoot.rotation.x = -rKnee * 0.24 * b;

        // Arms counter-swing (added on top of rest A-pose)
        const lUp = get('leftUpperArm');
        const rUp = get('rightUpperArm');
        const lLow = get('leftLowerArm');
        const rLow = get('rightLowerArm');
        if (lUp) lUp.rotation.x = -lStep * 0.26 * b;
        if (rUp) rUp.rotation.x = -rStep * 0.26 * b;
        if (lLow) lLow.rotation.x = Math.max(0, lStep) * 0.14 * b;
        if (rLow) rLow.rotation.x = Math.max(0, rStep) * 0.14 * b;

        // Hip sway, torso counter-rotation, and a tiny head counter-turn keep
        // the walk from looking like a single rigid object sliding forward.
        const hips  = get('hips');
        const spine = get('spine');
        const chest = get('chest') || get('upperChest') || spine;
        const neck = get('neck');
        const head = get('head');
        if (hips)  hips.rotation.z = c * 0.035 * b;
        if (spine) spine.rotation.y = -s * 0.025 * b;
        if (chest) {
            chest.rotation.y = -s * 0.040 * b;
            chest.rotation.z = -c * 0.025 * b;
        }
        if (neck) neck.rotation.y = s * 0.020 * b;
        if (head) head.rotation.y = s * 0.025 * b;

        // Subtle vertical bounce
        if (this.avatar?.avatar?.group) {
            const bounce = Math.abs(s) * 0.018 * b;
            this.avatar.avatar.group.position.y += bounce - (this._lastBounce || 0);
            this._lastBounce = bounce;
        }
    }

    _updateHotspotTransition(group, delta = 0) {
        const transition = this.hotspotTransition;
        if (!transition || !group) return false;

        // Only advance while locomotion owns the avatar. Wall-clock time kept
        // running during a gesture and teleported the avatar on the next frame.
        transition.elapsed = (transition.elapsed || 0) + Math.max(0, delta) * 1000;
        let arrived;
        if (transition.route) {
            arrived = this._followRoute(transition, group, delta);
        } else {
            const raw = transition.duration > 0 ? transition.elapsed / transition.duration : 1;
            const t = THREE.MathUtils.clamp(raw, 0, 1);
            const turnT = THREE.MathUtils.clamp(t / 0.24, 0, 1);
            const walkT = THREE.MathUtils.clamp((t - 0.16) / 0.84, 0, 1);
            const turnEase = turnT * turnT * (3 - 2 * turnT);
            const walkEase = walkT * walkT * (3 - 2 * walkT);
            // With nowhere to go she only turns: no steps on the spot.
            transition.walkProgress = transition.fromPosition.distanceTo(transition.toPosition) > 0.01 ? walkEase : 0;

            // Horizontal travel must not overwrite breathing/bounce on root Y.
            group.position.x = THREE.MathUtils.lerp(transition.fromPosition.x, transition.toPosition.x, walkEase);
            group.position.z = THREE.MathUtils.lerp(transition.fromPosition.z, transition.toPosition.z, walkEase);
            group.rotation.y = lerpAngle(
                transition.fromYaw,
                transition.toYaw,
                turnEase,
            );

            const vrm = this.avatar?.avatar?.vrm;
            if (vrm?.humanoid && turnT < 1) {
                const get = (name) =>
                    vrm.humanoid.getNormalizedBoneNode?.(name) ?? vrm.humanoid.getBoneNode?.(name);
                const deltaYaw = Math.atan2(
                    Math.sin(transition.toYaw - transition.fromYaw),
                    Math.cos(transition.toYaw - transition.fromYaw),
                );
                const lead = THREE.MathUtils.clamp(deltaYaw, -0.48, 0.48) * (1 - turnEase);
                const spine = get('spine');
                const chest = get('chest') || get('upperChest');
                const neck = get('neck');
                const head = get('head');
                if (spine) spine.rotation.y = lead * 0.18;
                if (chest) chest.rotation.y = lead * 0.28;
                if (neck) neck.rotation.y = lead * 0.38;
                if (head) head.rotation.y = lead * 0.48;
            }
            arrived = t >= 1;
        }

        if (arrived) {
            group.position.x = transition.toPosition.x;
            group.position.z = transition.toPosition.z;
            group.rotation.y = transition.toYaw;
            this.hotspotTransition = null;
            this._heldYaw = transition.roomAction?.endMode === 'stay' ? transition.toYaw : null;
            this._atSpot = transition.hotspot || null;
            this.idleSystem?.resume?.();
            this._startActionTimer?.(transition.roomAction);
            this._playHotspotPose(transition.roomAction || transition.hotspot);
        }

        return true;
    }

    _playHotspotPose(actionOrHotspot) {
        const pose = actionOrHotspot?.animationName ?? actionOrHotspot?.defaultPoseOrAnimation;
        if (!pose || pose === 'idle') {
            this.idleSystem?.resume?.();
            return;
        }

        // Room actions may use internal poses that are intentionally hidden
        // from the public pose menu (label: null). Do not gate them through
        // availablePoses(); trigger() is the source of truth and already
        // blocks unsafe poses that require a real VRMA clip.
        const playedPose = this.idleSystem?.trigger?.(pose);
        if (playedPose) return;

        const clipName = {
            thinking_pose: 'think',
            look_around: 'look_around',
            look_window: 'look_around',
            window_looking: 'look_around',
            sit_chair: 'sit',
            wave_hand: 'wave',
            pose_noble: 'idle_noble',
            subtle_nod: 'nod',
            lie_bed: 'lie',
        }[pose] || pose;
        const playedClip = this.idleSystem?.playClipExternal?.(clipName, {
            fadeIn: 0.25,
            fadeOut: 0.3,
        });
        if (playedClip) return;

        const fallbackAnimation = actionOrHotspot?.fallbackAnimation || {
            thinking_pose: 'pensar',
            look_around: 'pensar',
            look_window: 'pensar',
            window_looking: 'pensar',
            sit_chair: 'idle',
            pose_noble: 'saludar_feliz',
            subtle_nod: 'idle',
        }[pose] || 'idle';
        this.avatar?.playAnimation?.(fallbackAnimation);
    }

    // ── Private helpers ────────────────────────────────────

    _showEffect(effectName, position) {
        const posMap = {
            floatingBook: new THREE.Vector3(
                (position?.x || 0) + 0.5,
                1.2,
                (position?.z || 0),
            ),
            thinkingCloud: new THREE.Vector3(
                (position?.x || 0),
                2.0,
                (position?.z || 0),
            ),
            dataStream: new THREE.Vector3(
                (position?.x || 0) + 0.3,
                0.5,
                (position?.z || 0) - 0.2,
            ),
            memorySymbol: new THREE.Vector3(
                (position?.x || 0),
                1.5,
                (position?.z || 0),
            ),
        };

        const fx = this.effects[effectName];
        if (fx) {
            fx.show(posMap[effectName] || position);
        }
    }

    _clearAllEffects() {
        for (const fx of Object.values(this.effects)) {
            if (fx && fx.active) fx.hide();
        }
    }

    _activateObject(name) {
        if (name === 'monitor') {
            this._setMonitor(true);
        } else if (name === 'pc_workstation') {
            this._setMonitor(true);
            this._setDeskHolograms(true);
        } else if (name === 'window_cinematic') {
            this._setWindowCinematic(true);
        }
    }

    _deactivateAll() {
        this._setMonitor(false);
        this._setDeskHolograms(false);
        this._setWindowCinematic(false);
    }

    _setRoomLight(light, intensity) {
        if (this.room?.setLightIntensity) this.room.setLightIntensity(light, intensity);
        else if (light) light.intensity = intensity;
    }

    _setMonitor(on) {
        const screens = this.room?.getObject('monitor_screens')
            || [this.room?.getObject('monitor_screen')].filter(Boolean);
        for (const screen of screens) {
            if (!screen?.material) continue;
            screen.userData.monitorActive = on;
            if (screen.material.emissive) {
                screen.material.emissive.set(on ? 0x2a8aff : 0x0a1a2a);
                screen.material.emissiveIntensity = on ? 0.72 : 0.12;
            }
            if (screen.material.color) {
                screen.material.color.set(on ? 0xa9f7ff : 0x7fefff);
                screen.material.opacity = on ? 0.96 : 0.78;
            }
        }
        const glow = this.room?.getObject('monitor_glow');
        if (glow) {
            this._setRoomLight(glow, on ? 1.35 : 0.82);
            glow.distance = on ? 4.2 : 3.5;
        }
        this._monitorActive = on;
    }

    _setDeskHolograms(on) {
        const holograms = this.room?.getObject('desk_holograms');
        if (!holograms) return;
        holograms.visible = true;
        holograms.traverse?.((child) => {
            if (!child.userData) return;
            if (child.userData.pcHologram || child.userData.pcHologramLine) {
                child.userData.pcActive = on;
            }
        });
    }

    _setWindowCinematic(on) {
        const cityWash = this.room?.cityCyanWash;
        if (cityWash) {
            this._setRoomLight(cityWash, on ? 1.04 : 0.74);
            cityWash.distance = on ? 8.8 : 8.0;
        }
        const backWall = this.room?.backWallUp;
        if (backWall) {
            this._setRoomLight(backWall, on ? 0.72 : 0.58);
            backWall.distance = on ? 6.8 : 6.2;
        }
    }

    _setActionLookAt(action) {
        if (Array.isArray(action?.lookAtTarget) && (action.lookAtWeight ?? 0.42) > 0) {
            this.actionLookAtTarget = new THREE.Vector3(...action.lookAtTarget);
            this.actionLookAtWeight = action.lookAtWeight ?? 0.42;
        } else {
            this._clearActionLookAt();
        }
    }

    _updateActionLookAt() {
        if (!this.actionLookAtTarget) return;
        this.avatar?.lookAt?.(this.actionLookAtTarget, this.actionLookAtWeight);
    }

    _clearActionLookAt() {
        this.actionLookAtTarget = null;
        this.actionLookAtWeight = 0;
        this.avatar?.cancelIK?.();
    }

    _finishAction() {
        const finishedAction = this.currentRoomAction;
        this._clearAllEffects();
        this._deactivateAll();
        this.currentAction = null;
        this.currentRoomAction = null;
        this._clearActionLookAt();
        if (finishedAction?.returnExpression) {
            if (this.onEmotion) this.onEmotion(finishedAction.returnExpression);
            else this.avatar?.setEmotion?.(finishedAction.returnExpression);
        }

        if (finishedAction?.endMode === 'stay') {
            this.isMoving = false;
            return;
        }

        // Return to center for legacy timed actions.
        this._heldYaw = null;
        this.moveTarget.set(0, 0, 0);
        this.isMoving = true;
    }
}

