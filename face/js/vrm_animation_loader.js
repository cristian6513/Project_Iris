/**
 * IRIS VRM Animation (VRMA) Loader
 *
 * VRMA (.vrma) is Pixiv's standardized animation format that retargets
 * automatically to ANY VRM humanoid. Animations authored once work on
 * any character without manual bone remapping.
 *
 * Usage:
 *    const loader = new VRMAnimationPlayer(vrm);
 *    await loader.load('wave', '/face/animations/wave.vrma');
 *    loader.play('wave', { loop: false, fadeIn: 0.3 });
 *    // in your update loop:
 *    loader.update(delta);
 *
 * This module treats missing / 404 files gracefully — the caller can
 * check `.hasAnimation(name)` before trying to play, and fall back to
 * procedural bone manipulation when an animation isn't available yet.
 */

import * as THREE from 'three';
import { disposeAvatarTree } from './avatar_lifecycle.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';

// Lazy-loaded references to the @pixiv/three-vrm-animation classes.
let _vrmAnimPlugin = null;
let _createClip = null;
let _lookAtProxyClass = null;
let _modulePromise = null;

async function ensureModule() {
    if (_vrmAnimPlugin && _createClip) return;
    if (!_modulePromise) {
        _modulePromise = import('@pixiv/three-vrm-animation');
    }
    let mod;
    try {
        mod = await _modulePromise;
    } catch (error) {
        // A transient CDN/network failure must not poison every future
        // manifest reload with the same permanently rejected Promise.
        _modulePromise = null;
        throw error;
    }
    _vrmAnimPlugin = mod.VRMAnimationLoaderPlugin;
    _createClip = mod.createVRMAnimationClip;
    _lookAtProxyClass = mod.VRMLookAtQuaternionProxy || null;
    if (!_vrmAnimPlugin || !_createClip) {
        throw new Error(
            '@pixiv/three-vrm-animation module did not expose ' +
            'VRMAnimationLoaderPlugin / createVRMAnimationClip'
        );
    }
}

function ensureLookAtProxy(vrm) {
    if (!_lookAtProxyClass || !vrm?.lookAt || !vrm?.scene) return;
    if (vrm.scene.getObjectByName?.('lookAtQuaternionProxy')) return;
    const proxy = new _lookAtProxyClass(vrm.lookAt);
    proxy.name = 'lookAtQuaternionProxy';
    vrm.scene.add(proxy);
}

// An eased fade reaches 1.5 times the speed of a linear one at its middle, so it runs longer to match.
const EASED_FADE_STRETCH = 1.4;

export class VRMAnimationPlayer {
    /**
     * @param {object} vrm - @pixiv/three-vrm VRM instance (must have .scene + .humanoid)
     */
    constructor(vrm) {
        if (!vrm?.scene || !vrm?.humanoid) {
            throw new Error('VRMAnimationPlayer requires a loaded VRM with humanoid');
        }
        this.vrm = vrm;
        this._disposed = false;
        this._generation = 0;
        this.mixer = new THREE.AnimationMixer(vrm.scene);
        /** @type {Map<string, THREE.AnimationClip>} */
        this.clips = new Map();
        /** @type {Map<string, THREE.AnimationAction>} */
        this.actions = new Map();
        /** @type {THREE.AnimationAction|null} */
        this.current = null;
        this.currentName = null;
        this._transitionAction = null;
        this._transitionEndsAt = 0;
        this._loopBlend = false;
        this._loopWeight = 1;
        this._pendingLoop = null;
        this._onLoop = event => {
            if (event.action !== this.current || !this._loopBlend) return;
            // Three emits loop before writing the wrapped sample. Preserve the
            // visible pose now, then blend AFTER the mixer's binding pass.
            this._pendingLoop = {
                snapshot: this._captureTransitionPose(),
                duration: Math.min(0.35 + this._measurePoseDistance(this.current) * 0.35,
                    this.current.getClip().duration * 0.25),
            };
        };
        this.mixer.addEventListener('loop', this._onLoop);

        // Track which URLs we already attempted to load so repeated calls
        // for a missing file don't spam the network.
        this._attempted = new Set();
        this._revisions = new Map();
        this._formats = new Map();
        this._requests = new Map();
        this._loads = new Map();
        this._candidates = new Set();
        this._retiredClips = new Set();
    }

    /**
     * Load an animation file from `url` and register it under `name`.
     *
     * Dispatches by extension:
     *   .vrma           → native VRM Animation (preferred, auto-retargets)
     *   .bvh            → generic BVH mocap, retargeted via bvh_retargeter.js
     *   .fbx            → FBX (Mixamo, Unity exports…), via fbx_retargeter.js
     *   .glb / .gltf    → Blender / Sketchfab exports, via glb_retargeter.js
     *   (default)       → fall back to the .vrma loader; suitable for
     *                     files served without an extension
     *
     * Returns true on success, false if the file wasn't found or failed.
     */
    async load(name, url, options = {}) {
        if (this._disposed) return false;
        const revision = String(options.revision ?? url);
        const format = options.format?.toLowerCase() || null;
        if (format && !['vrma', 'bvh', 'fbx', 'glb', 'gltf'].includes(format)) return false;
        const identity = JSON.stringify([revision, format]);
        if (this.clips.has(name) && (options.revision == null
            || (this._revisions.get(name) === revision && this._formats.get(name) === format))) {
            this._requests.delete(name);
            this._loads.delete(name);
            return true;
        }
        const pending = this._loads.get(name);
        if (pending?.identity === identity) {
            // A newer manifest can share bytes while changing their metadata.
            pending.request.options = options;
            return pending.promise;
        }
        const request = { options };
        this._requests.set(name, request);
        const current = () => !this._disposed && this._requests.get(name) === request
            && (request.options.isCurrent?.() ?? true);
        const promise = (async () => {
            let asset, candidate;
            try {
                if (!current()) return false;
                asset = options.resolveAsset ? await options.resolveAsset() : { url };
                if (!current()) return false;
                candidate = new VRMAnimationPlayer(this.vrm);
                this._candidates.add(candidate);
                if (!await candidate._loadCandidate(name, asset.url, format) || !current()) return false;
                const clip = candidate.clips.get(name);
                if (!clip?.tracks.length || !Number.isFinite(clip.duration) || clip.duration < 0) return false;
                if (clip.duration === 0) clip.duration = 1 / 24;
                this._installClip(name, clip);
                this._revisions.set(name, revision);
                this._formats.set(name, format);
                request.options.onCommit?.();
                return true;
            } catch (error) {
                if (current()) console.warn('[VRMA] replacement failed:', name, error);
                return false;
            } finally {
                candidate?.dispose();
                this._candidates.delete(candidate);
                asset?.release?.();
                if (this._requests.get(name) === request) this._loads.delete(name);
            }
        })();
        this._loads.set(name, { identity, request, promise });
        return promise;
    }

    _installClip(name, clip) {
        const previous = this.actions.get(name);
        const active = previous && previous === this.current;
        this.clips.set(name, clip);
        this.actions.set(name, this.mixer.clipAction(clip));
        if (active) {
            const loop = previous.loop !== THREE.LoopOnce;
            const weight = this._loopWeight;
            // play captures the visible pose BEFORE it stops outgoing bindings.
            this.play(name, { loop, weight, fadeIn: 0.3 });
            this._retiredClips.add(previous.getClip());
            this.mixer.dispatchEvent({ type: 'action_replaced', previous, action: this.current });
        } else if (previous) {
            previous.stop();
            this.mixer.uncacheClip(previous.getClip());
        }
    }

    remove(name) {
        this._requests.delete(name);
        this._loads.delete(name);
        const action = this.actions.get(name);
        if (action === this.current && action) {
            this.stop(0.3); // Saved authored rest is the final fallback.
            this._retiredClips.add(action.getClip());
        } else if (action) {
            action.stop();
            this.mixer.uncacheClip(action.getClip());
        }
        this.actions.delete(name);
        this.clips.delete(name);
        this._revisions.delete(name);
        this._formats.delete(name);
    }

    async _loadCandidate(name, url, format) {

        if (this._disposed) return false;
        const generation = this._generation;
        if (this.clips.has(name)) return true;
        // Track the FULL URL, including its cache-buster. Repeating the exact
        // same request is suppressed, while reloadManifest's new ?v=...
        // timestamp is a genuine retry after a transient 404/CDN failure.
        const attemptKey = String(url);
        if (this._attempted.has(attemptKey)) return false;
        this._attempted.add(attemptKey);

        // Dispatch by extension on the path so query strings
        // (?v=timestamp) don't confuse the detection.
        const lower = format ? '.' + format : attemptKey.split('?')[0].toLowerCase();
        if (lower.endsWith('.bvh')) {
            return await this._loadBVH(name, url);
        }
        if (lower.endsWith('.fbx')) {
            return await this._loadFBX(name, url);
        }
        if (lower.endsWith('.glb') || lower.endsWith('.gltf')) {
            return await this._loadGLB(name, url);
        }

        try {
            await ensureModule();
        } catch (e) {
            console.warn('[VRMA] could not load animation module:', e);
            return false;
        }

        if (this._disposed || generation !== this._generation) return false;
        return await new Promise((resolve) => {
            const loader = new GLTFLoader();
            loader.register((parser) => new _vrmAnimPlugin(parser));
            loader.load(
                url,
                (gltf) => {
                    try {
                        if (this._disposed || generation !== this._generation) { resolve(false); return; }
                        const vrmAnim = gltf.userData?.vrmAnimations?.[0];
                        if (!vrmAnim) {
                            console.warn(`[VRMA] ${url} has no vrmAnimations in userData`);
                            resolve(false);
                            return;
                        }
                        try {
                            // Required by three-vrm-animation for look-at tracks.
                            // Creating it explicitly avoids the library's fallback
                            // warning and keeps every loaded clip on the same path.
                            ensureLookAtProxy(this.vrm);
                            const clip = _createClip(vrmAnim, this.vrm);
                            // A single-frame VRMA is a valid held pose. Give its
                            // action positive duration so Three never loops modulo 0.
                            if (clip.duration === 0 && clip.tracks.length) clip.duration = 1 / 24;
                            clip.name = name;
                            this.clips.set(name, clip);
                            const action = this.mixer.clipAction(clip);
                            this.actions.set(name, action);
                            console.log(`[VRMA] loaded '${name}' (${clip.duration.toFixed(2)}s) from ${url}`);
                            resolve(true);
                        } catch (e) {
                            console.warn(`[VRMA] createVRMAnimationClip failed for ${name}:`, e);
                            resolve(false);
                        }
                    } finally {
                        for (const scene of new Set(gltf.scenes || [gltf.scene])) {
                            disposeAvatarTree(scene, { preserveRoots: [this.vrm?.scene] });
                        }
                    }
                },
                undefined,
                (err) => {
                    // File missing / CORS / network — log softly and continue.
                    // This is expected when the /face/animations/ folder is
                    // empty; callers should fall back to procedural poses.
                    if (err?.target?.status === 404) {
                        console.info(`[VRMA] '${name}' not found at ${url} (will use procedural fallback)`);
                    } else {
                        console.warn(`[VRMA] failed to load ${url}:`, err);
                    }
                    resolve(false);
                },
            );
        });
    }

    /**
     * Load a `.bvh` file and register it under `name`. BVH has no
     * standardized skeleton, so `bvh_retargeter.js` maps its bones onto
     * the VRM humanoid via a name table and drives the *normalized* bone
     * nodes — the same ones `@pixiv/three-vrm-animation` writes to — so
     * the VRM's built-in rest-pose correction does the heavy lifting.
     *
     * Returns true on success, false when the file is missing or no bones
     * could be mapped.
     */
    async _loadBVH(name, url) {
        const generation = this._generation;
        if (this._disposed) return false;
        let loadAndRetargetBVH;
        try {
            ({ loadAndRetargetBVH } = await import('./bvh_retargeter.js?v=2'));
        } catch (e) {
            console.warn('[VRMA] failed to load bvh_retargeter module:', e);
            return false;
        }

        if (this._disposed || generation !== this._generation) return false;
        const result = await loadAndRetargetBVH(url, this.vrm, name);
        if (!result || this._disposed || generation !== this._generation) return false;

        // Register just like a .vrma clip — downstream code (play, stop,
        // hasAnimation) is extension-agnostic from here on.
        this.clips.set(name, result.clip);
        this.actions.set(name, this.mixer.clipAction(result.clip));
        return true;
    }

    /**
     * Load a `.fbx` file and register it under `name`. FBX is the most
     * common free-mocap format on the web — Mixamo (~2,600 free
     * animations), Unity Asset Store free packs, Rokoko exports, etc.
     *
     * `fbx_retargeter.js` reuses the BVH bone-name table (which already
     * normalizes Mixamo's `mixamorig:` prefix) and drives the VRM's
     * normalized bones so the same clip works on any avatar.
     *
     * Returns true on success, false when the file is missing, has no
     * animations, or no bones could be mapped.
     */
    async _loadFBX(name, url) {
        const generation = this._generation;
        if (this._disposed) return false;
        let loadAndRetargetFBX;
        try {
            ({ loadAndRetargetFBX } = await import('./fbx_retargeter.js?v=3'));
        } catch (e) {
            console.warn('[VRMA] failed to load fbx_retargeter module:', e);
            return false;
        }

        if (this._disposed || generation !== this._generation) return false;
        const result = await loadAndRetargetFBX(url, this.vrm, name);
        if (!result || this._disposed || generation !== this._generation) return false;

        this.clips.set(name, result.clip);
        this.actions.set(name, this.mixer.clipAction(result.clip));
        return true;
    }

    /**
     * Load a `.glb` / `.gltf` file as an ANIMATION CLIP source and
     * register it under `name`. Blender is the most common producer
     * here (native glTF export); Sketchfab mocap packs also distribute
     * GLB. The GLB's first AnimationClip is retargeted onto the VRM
     * via the same humanoid bone table the FBX path uses.
     *
     * Note: this is for STANDALONE animation files placed under
     * `face/animations/`. GLB AVATARS (full character mesh with
     * embedded clips) take a different path through `avatar.js` —
     * those are loaded into the scene, not into this clip library.
     */
    async _loadGLB(name, url) {
        const generation = this._generation;
        if (this._disposed) return false;
        let loadAndRetargetGLB;
        try {
            ({ loadAndRetargetGLB } = await import('./glb_retargeter.js?v=3'));
        } catch (e) {
            console.warn('[VRMA] failed to load glb_retargeter module:', e);
            return false;
        }

        if (this._disposed || generation !== this._generation) return false;
        const result = await loadAndRetargetGLB(url, this.vrm, name);
        if (!result || this._disposed || generation !== this._generation) return false;

        this.clips.set(name, result.clip);
        this.actions.set(name, this.mixer.clipAction(result.clip));
        return true;
    }

    /**
     * Try to load a whole manifest of animations in parallel.
     * `manifest` is { name: url, ... }. Returns the set of names that
     * actually loaded.
     */
    async loadManifest(manifest) {
        const results = await Promise.all(
            Object.entries(manifest).map(async ([name, url]) => {
                const ok = await this.load(name, url);
                return ok ? name : null;
            }),
        );
        return new Set(results.filter(Boolean));
    }

    hasAnimation(name) {
        return this.actions.has(name);
    }

    /** Set a retargeted pose directly, without an action restoring old bindings. */
    applyPose(name, time = 0) {
        if (this._disposed) return false;
        const clip = this.clips.get(name);
        if (!clip?.tracks.length) return false;
        for (const track of clip.tracks) {
            const binding = THREE.PropertyBinding.create(this.vrm.scene, track.name);
            binding.setValue(track.createInterpolant().evaluate(time), 0);
            binding.unbind();
        }
        return true;
    }

    /**
     * Returns true if the loaded clip contains keyframe tracks for the
     * head / neck / upper spine area. User-uploaded VRMA files often
     * only include arm and hand tracks (the user only authored a wave
     * or dance motion), so the head bone is left UNTRACKED by the
     * mixer during playback.
     *
     * MicroLife uses this to decide whether to disable
     * `vrm.lookAt.autoUpdate` during the clip:
     *   - clip HAS head tracks → autoUpdate=false so the SDK doesn't
     *     overwrite the clip's head animation each frame.
     *   - clip has NO head tracks → leave autoUpdate=true so the SDK
     *     keeps the head naturally tracking the camera while the body
     *     waves; otherwise the head would freeze in whatever pose it
     *     was in when the clip started.
     */
    clipAnimatesHead(name) {
        const clip = this.clips.get(name);
        if (!clip || !clip.tracks) return false;
        const nodes = ['head', 'neck', 'upperChest'].map(
            bone => this.vrm.humanoid.getNormalizedBoneNode?.(bone),
        ).filter(Boolean);
        for (const track of clip.tracks) {
            const parsed = THREE.PropertyBinding.parseTrackName(track.name);
            const node = THREE.PropertyBinding.findNode(this.vrm.scene, parsed.nodeName);
            if (nodes.includes(node) || node?.name === 'lookAtQuaternionProxy') return true;
        }
        return false;
    }

    /**
     * Measure the average angular distance between the avatar's current
     * pose and the first frame of an animation clip. Used to scale the
     * crossfade duration in ``play()`` so far-apart pose transitions
     * fade slower instead of snapping. Returns a normalized score in
     * ``[0..1]`` (saturates at ~85° average per bone).
     *
     * Walks the clip's ``.quaternion`` tracks, reads the first
     * quaternion of each, finds the corresponding live bone by name,
     * and accumulates the half-angle between the two quaternions
     * (``acos(|dot|)``). The mean over all matched bones is then
     * mapped through ``min(1, mean / 1.5 rad)``.
     */
    _measurePoseDistance(action) {
        if (!this.vrm || !this.vrm.scene) return 0;
        let clip;
        try {
            clip = action.getClip();
        } catch {
            return 0;
        }
        if (!clip || !Array.isArray(clip.tracks) || clip.tracks.length === 0) return 0;

        let total = 0;
        let count = 0;
        const _q1 = this._distQ1 || (this._distQ1 = new THREE.Quaternion());
        const _q2 = this._distQ2 || (this._distQ2 = new THREE.Quaternion());

        for (const track of clip.tracks) {
            if (!track || typeof track.name !== 'string') continue;
            // Quaternion tracks land as "BoneName.quaternion". Position
            // tracks are uninteresting for pose-snap risk.
            if (!track.name.endsWith('.quaternion')) continue;
            const values = track.values;
            if (!values || values.length < 4) continue;

            const boneName = track.name.slice(0, -'.quaternion'.length);
            // Track names from VRMA / FBX retargeters end up in many
            // shapes:
            //   • Raw normalized name:      "head"
            //   • GLTF path:                "root/spine/head"
            //   • Mixamo-prefixed:          "mixamorig:Head" or
            //                               "Armature/mixamorig:Head"
            //   • Other retargeter prefixes (J_Bip_, RL_, …)
            // Look up the live bone by trying the tail unchanged
            // first, then stripping common animation-tool prefixes.
            // Without the prefix strip, FBX clips on normalized VRMs
            // would silently miss every bone — distance stays 0,
            // pose-aware crossfade silently disabled.
            const tail = boneName.includes('/')
                ? boneName.split('/').pop()
                : boneName;
            let node = THREE.PropertyBinding.findNode(this.vrm.scene, boneName)
                || this.vrm.scene.getObjectByName(tail);
            if (!node) {
                // Try stripping a Mixamo-style "prefix:" namespace
                // (e.g. "mixamorig:Head" → "Head"). Cheap and covers
                // the most common case.
                const colon = tail.indexOf(':');
                if (colon !== -1) {
                    const stripped = tail.slice(colon + 1);
                    node = this.vrm.scene.getObjectByName(stripped);
                    // Many normalized VRM rigs use lowercase bone
                    // names ("head") whereas Mixamo uses TitleCase
                    // ("Head"). Try the lowercased variant too.
                    if (!node) {
                        node = this.vrm.scene.getObjectByName(
                            stripped.charAt(0).toLowerCase() + stripped.slice(1),
                        );
                    }
                }
            }
            if (!node || !node.quaternion) continue;

            _q1.fromArray(track.createInterpolant().evaluate(0)).normalize();
            _q2.copy(node.quaternion);
            // dot ∈ [-1..1]; |dot| because q and -q represent the same
            // rotation. Clamp before acos to dodge floating-point >1.
            const dot = Math.min(1, Math.abs(_q1.dot(_q2)));
            // Half-angle between the two quaternions (full rotation
            // angle is 2*acos(dot), but for "how far" we just want a
            // monotonic distance — half-angle keeps numbers small and
            // proportional).
            const angle = Math.acos(dot);
            total += angle;
            count++;
        }

        if (count === 0) return 0;
        const mean = total / count;
        // ~1.5 rad ≈ 85° mean — anything further saturates. We're not
        // trying to be physically accurate, just a sane scaling factor.
        return Math.min(1, mean / 1.5);
    }

    /**
     * Play a loaded animation, optionally cross-fading from the current one.
     * @param {string} name
     * @param {object} opts
     * @param {boolean} [opts.loop=true]
     * @param {number}  [opts.fadeIn=0.3]
     * @param {number}  [opts.weight=1.0]
     * @returns {boolean} true if the action started
     */
    play(name, opts = {}) {
        if (this._disposed) return false;
        const action = this.actions.get(name);
        if (!action) return false;
        this._pendingLoop = null;
        this._loopBlend = opts.loop !== false && this._hasLoopSeam(action.getClip());
        this._loopWeight = opts.weight ?? 1;
        const requestedFade = Math.max(0, opts.fadeIn ?? 0.3);
        const fadeIn = requestedFade === 0 ? 0
            : (requestedFade + this._measurePoseDistance(action) * 0.35) * EASED_FADE_STRETCH;
        // A new fade starts from the rendered pose, including an interrupted
        // fade or a replay of the SAME action. Resetting/fading the outgoing
        // actions instead jumps to their targets or the saved rest pose.
        const snapshot = fadeIn > 0 ? this._captureTransitionPose(action) : null;
        this.mixer.stopAllAction();
        this._clearTransition();
        if (snapshot) this._startTransition(snapshot, fadeIn);

        action.reset();
        action.setEffectiveTimeScale(1);
        action.setLoop(opts.loop !== false ? THREE.LoopRepeat : THREE.LoopOnce, Infinity);
        action.clampWhenFinished = opts.loop === false;
        action.setEffectiveWeight(opts.weight ?? 1);
        if (snapshot) this._easeIn(action, opts.weight ?? 1, fadeIn);
        action.play();
        this.current = action;
        this.currentName = name;
        // stopAllAction restores bindings synchronously. Sample the new
        // blend at weight zero before another subsystem can see that reset.
        this.mixer.update(0);
        return true;
    }

    _hasLoopSeam(clip) {
        return clip.tracks.some(track => {
            const interpolant = track.createInterpolant();
            const first = Array.from(interpolant.evaluate(0));
            const last = interpolant.evaluate(clip.duration);
            if (track.ValueTypeName === 'quaternion') {
                const a = new THREE.Quaternion().fromArray(first).normalize();
                const b = new THREE.Quaternion().fromArray(last).normalize();
                return a.angleTo(b) > 0.001;
            }
            return first.some((value, index) => Math.abs(value - last[index]) > 0.0001);
        });
    }

    /** Remaining blend time on the same clock that drives the mixer. */
    get transitionRemaining() {
        if (this._disposed) return 0;
        return Math.max(0, this._transitionEndsAt - this.mixer.time);
    }

    _captureTransitionPose(nextAction = null) {
        const tracks = new Map();
        for (const action of [this._transitionAction, this.current, nextAction]) {
            for (const track of action?.getClip().tracks || []) tracks.set(track.name, track);
        }
        const heldTracks = [];
        for (const track of tracks.values()) {
            const binding = THREE.PropertyBinding.create(this.vrm.scene, track.name);
            const valueSize = track.getValueSize()
                / (track.createInterpolant.isInterpolantFactoryMethodGLTFCubicSpline ? 3 : 1);
            const values = new Float32Array(valueSize);
            binding.getValue(values, 0);
            binding.unbind();
            const held = track.clone();
            held.times = new Float32Array([0]);
            held.values = values;
            // A held pose contains values only, never cubic tangents.
            held.setInterpolation(THREE.InterpolateLinear);
            heldTracks.push(held);
        }
        return heldTracks.length ? new THREE.AnimationClip('__pose_transition', 1, heldTracks) : null;
    }

    _startTransition(snapshot, duration) {
        this._transitionAction = this.mixer.clipAction(snapshot);
        this._transitionAction.setLoop(THREE.LoopRepeat, Infinity);
        this._transitionAction.setEffectiveWeight(1).play();
        this._transitionEndsAt = this.mixer.time + duration;
    }

    /** The new clip takes over gently and the old pose lets go gently: a linear fade stops dead.
     *  With no new clip, `action` is null and the old pose eases into the saved rest. */
    _easeIn(action, weight, duration) {
        action?.setEffectiveWeight(0);
        this._ease = { action, weight, from: this.mixer.time, duration };
    }

    _applyEase(time) {
        const ease = this._ease;
        if (!ease) return;
        const t = Math.min(1, Math.max(0, (time - ease.from) / ease.duration));
        const eased = t * t * (3 - 2 * t);
        ease.action?.setEffectiveWeight(ease.weight * eased);
        this._transitionAction?.setEffectiveWeight(1 - eased);
        if (t >= 1) this._ease = null;
    }

    _clearTransition() {
        if (this._transitionAction) {
            const clip = this._transitionAction.getClip();
            this._transitionAction.stop();
            this.mixer.uncacheClip(clip);
            this._transitionAction = null;
        }
        this._ease = null;
        this._transitionEndsAt = 0;
        for (const clip of this._retiredClips) this.mixer.uncacheClip(clip);
        this._retiredClips.clear();
    }

    /** Fade to the saved rest pose; retire actions on mixer time, never a timer. */
    stop(fadeOut = 0.3) {
        if (this._disposed) return;
        this._pendingLoop = null;
        this._loopBlend = false;
        const snapshot = fadeOut > 0 ? this._captureTransitionPose() : null;
        this.mixer.stopAllAction();
        this._clearTransition();
        this.current = null;
        this.currentName = null;
        if (snapshot) {
            this._startTransition(snapshot, fadeOut);
            this._easeIn(null, 0, fadeOut);
            this.mixer.update(0);
        }
    }

    /** Pump even during a stop fade, until all property bindings are released. */
    update(delta) {
        if (this._disposed) return;
        this._applyEase(this.mixer.time + delta);
        this.mixer.update(delta);
        if (this._pendingLoop) {
            const { snapshot, duration } = this._pendingLoop;
            this._pendingLoop = null;
            this._clearTransition();
            if (snapshot && duration > 0) {
                this._startTransition(snapshot, duration);
                this._easeIn(this.current, this._loopWeight, duration);
                this.mixer.update(0);
            }
        }
        if (this._transitionAction && this.transitionRemaining <= 1e-8) {
            this._clearTransition();
        }
    }
    /** Invalidate pending loads before releasing the mixer's bindings. */
    dispose() {
        if (this._disposed) return;
        this._disposed = true;
        this._generation++;
        for (const candidate of this._candidates) candidate.dispose();
        this._candidates.clear();
        this._requests.clear();
        this._loads.clear();
        this._revisions.clear();
        this._formats.clear();
        this.mixer.removeEventListener('loop', this._onLoop);
        this.mixer.stopAllAction();
        this._clearTransition();
        for (const clip of this.clips.values()) this.mixer.uncacheClip(clip);
        this.mixer.uncacheRoot(this.vrm.scene);
        this.actions.clear();
        this.clips.clear();
        this._attempted.clear();
        this._pendingLoop = null;
        this.current = null;
        this.currentName = null;
        this._onLoop = null;
        this.vrm = null;
        this.mixer = null;
    }

}
