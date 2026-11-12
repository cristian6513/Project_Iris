/**
 * IRIS Avatar — a VRM or an MMD humanoid with a shared animation interface
 * (face/animations/iris.vrm). Full .vrm loading with @pixiv/three-vrm, 52 ARKit
 * blendshapes. The geometric and particle avatars were removed (2026-10-28).
 */

import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { disposeAvatarTree, retainAvatarResources } from './avatar_lifecycle.js?v=4';
import { limitTextures } from './texture_limit.js?v=2';
import { SeatDrape } from './seat_drape.js';
import { LyingHips } from './lying_hips.js?v=4';
import { ArmReach } from './arm_reach.js';
import { useKTX2 } from './ktx2_support.js?v=2';
import { autoRegisterSpringBones } from './auto_springbones.js';
import { AvatarBreeze } from './avatar_breeze.js?v=4';
import { IKSolver } from './ik.js';
import { PoseFace, NaturalBlink, VRM_EMOTION_PRESETS } from './pose_face.js';
import { VRMAnimationPlayer } from './vrm_animation_loader.js?v=10';
import { isPmxArchive } from './avatar_state.js?v=5';


// ════════════════════════════════════════════════════════════
// AvatarManager — the visible VRM and its replacements
// ════════════════════════════════════════════════════════════

export class AvatarManager {
    /**
     * @param {THREE.Scene} scene
     * @param {string} url - the VRM to show: the person's own or IRIS'
     * @param {string} irisUrl - IRIS' VRM, shown instead when `url` cannot be loaded
     * @param {{textureLimit?: () => number}} options - longest texture side for each model
     *   it loads (render quality «Ahorro»); 0 keeps the textures as they come
     */
    constructor(scene, url, irisUrl = url, { textureLimit = () => 0, pmxBlob = false } = {}) {
        this.scene = scene;
        this.vrmUrl = url;
        this.irisUrl = irisUrl;
        this.textureLimit = textureLimit;
        this.pmxBlob = pmxBlob;
        /** @type {VRMAvatar|null} */
        this.avatar = null;
        this._generation = 0;
        this._disposed = false;
        this._resources = null;
    }

    /** With `fallback`, a model that cannot be loaded gives way to IRIS (`vrmUrl` says which one shows). */
    async init({ fallback = true } = {}) {
        return this._replace(this.vrmUrl, fallback && !this.avatar);
    }

    async _load(staging, url) {
        const candidate = new VRMAvatar(staging);
        try {
            if (!url) throw new Error('Missing VRM URL.');
            await candidate.init(url, { pmx: isPmxArchive(url) || (this.pmxBlob && url.startsWith('blob:')) });
            if (!candidate.vrm) throw new Error('Invalid VRM avatar.');
            // Before WebGL sees them, so the full-size images never reach the GPU.
            await limitTextures(candidate.group, this.textureLimit());
            return candidate;
        } catch (error) {
            this._disposeInstance(candidate);
            throw error;
        }
    }

    async _replace(url, fallback) {
        if (this._disposed) throw new Error('AvatarManager is disposed.');
        const generation = ++this._generation;
        const staging = new THREE.Scene();
        let candidate;
        try {
            try {
                candidate = await this._load(staging, url);
            } catch (error) {
                if (!fallback || !this.irisUrl || url === this.irisUrl
                    || this._disposed || generation !== this._generation) throw error;
                console.warn('VRM load failed, showing IRIS instead:', error);
                candidate = await this._load(staging, this.irisUrl);
                url = this.irisUrl;
            }
            if (this._disposed || generation !== this._generation) return false;
            const previous = this.avatar;
            const previousResources = this._resources;
            const resources = retainAvatarResources(candidate.group);
            if (previous?.group) {
                candidate.group.position.copy(previous.group.position);
                candidate.group.rotation.copy(previous.group.rotation);
                candidate.group.rotation.y += (candidate.restYaw || 0) - (previous.restYaw || 0);
                candidate.group.userData.stageVisualLiftApplied = previous.group.userData.stageVisualLiftApplied;
            }
            candidate.scene = this.scene;
            this.scene.add(candidate.group);
            this.avatar = candidate;
            this._resources = resources;
            this.vrmUrl = url;
            candidate = null;
            this._disposeInstance(previous, previousResources);
            return true;
        } finally {
            this._disposeInstance(candidate);
        }
    }

    _disposeInstance(avatar, lease = null) {
        if (!avatar) return;
        avatar.dispose?.();
        disposeAvatarTree(avatar.group, { preserveRoots: [this.scene], lease });
        avatar.vrm = null;
        avatar.scene = null;
    }

    dispose() {
        if (this._disposed) return;
        this._disposed = true;
        this._generation++;
        this._disposeInstance(this.avatar, this._resources);
        this.avatar = null;
        this._resources = null;
    }

    // ── Delegated API ──
    setEmotion(e)       { this.avatar?.setEmotion(e); }
    setMouthOpen(v)     { this.avatar?.setMouthOpen(v); }
    playAnimation(n)    { this.avatar?.playAnimation(n); }
    update(delta, time, options) { this.avatar?.update(delta, time, options); }
    finalizeFrame(delta) { this.avatar?.finalizeFrame?.(delta); }

    // ── IK passthrough ──
    lookAt(target, weight = 1.0)        { this.avatar?.lookAt?.(target, weight); }
    reach(side, target, weight = 1.0)   { this.avatar?.reach?.(side, target, weight); }
    point(side, target, weight = 1.0)   { this.avatar?.point?.(side, target, weight); }
    cancelIK()                          { this.avatar?.cancelIK?.(); }
}


// ════════════════════════════════════════════════════════════
// VRMAvatar — Full VRM model with blendshapes
// ════════════════════════════════════════════════════════════

export class VRMAvatar {
    constructor(scene) {
        this.scene = scene;
        this.group = new THREE.Group();
        this.vrm = null;          // VRM instance
        this.mixer = null;        // AnimationMixer
        this.clock = new THREE.Clock();

        // State
        this.mouthOpenness = 0;
        this.currentExpressions = {};
        this.targetExpressions = {};
        this.blinkTimer = 0;
        this.nextBlink = 3;
        this.isBlinking = false;
        this.animationOwnsBlink = false;
        this.animationOwnsRootMotion = false;
        this._blinkTimeout = null;
        this._naturalBlink = new NaturalBlink();
        this._poseFace = new PoseFace();
        this.poseFacialProfile = null;
        this.poseFaceSpeaking = false;
        // A soft breeze over the hair and clothing spring bones (avatar_breeze.js).
        this.breeze = new AvatarBreeze();

        // Baseline yaw that makes the avatar face the camera. Consumers
        // (scene_actions, etc.) should add movement headings to this value.
        this.restYaw = 0;

        // Head animation state. Used by playAnimation() to produce
        // visible nod/shake/tilt gestures on the VRM's head BONE
        // (not the avatar's root group — that one is overwritten every
        // frame by IdleSystem._updatePose, which is why the old
        // setTimeout-on-group approach silently did nothing).
        //
        // Shape: { type, startTime, duration } or null when idle.
        this._headAnim = null;
    }

    /** Start a time-based head gesture. Overrides any in-flight one. */
    _triggerHeadAnim(type, duration = 700, { over = false } = {}) {
        this._headAnim = {
            type,
            startTime: performance.now(),
            duration,
            over,    // on top of a clip that writes the head every frame
        };
    }

    /**
     * Apply the in-flight head animation to the VRM head bone. Runs at
     * the end of the normalized-pose pass, after IK. finalizeFrame()
     * subsequently transfers that pose to the rendered skeleton.
     *
     * Uses ``sin(πt)`` as the base curve — smooth rise/fall, no jerks
     * at start or end. Shake uses ``sin(3πt)`` for a two-cycle wiggle.
     * The head bone keeps whatever final value the curve lands on; the
     * next idle frame's natural pose blending pulls it back to neutral.
     */
    _applyHeadAnim() {
        if (!this._headAnim || !this.vrm?.humanoid) return;
        const head = this.vrm.humanoid.getNormalizedBoneNode?.('head');
        if (!head) {
            this._headAnim = null;   // VRM without head bone — give up
            return;
        }
        const now = performance.now();
        const t = (now - this._headAnim.startTime) / this._headAnim.duration;
        if (t >= 1) {
            // Animation finished — let the head settle. Don't snap
            // rotation to 0 here; @pixiv/three-vrm's expression /
            // look-at updates will smooth it out next frame.
            this._headAnim = null;
            return;
        }

        const arc = Math.sin(t * Math.PI);
        const set = (axis, value) => { head.rotation[axis] = value + (this._headAnim.over ? head.rotation[axis] : 0); };
        switch (this._headAnim.type) {
            case 'nod':      set('x', -0.25 * arc); break;
            case 'nod_big':  set('x', -0.4  * arc); break;
            case 'shake':    set('y', 0.3 * Math.sin(t * Math.PI * 3)); break;
            case 'tilt':     set('z', 0.2 * arc); break;
        }
    }

    async init(url, { pmx = false } = {}) {
        // Dynamic import — only loaded if VRM mode is selected
        const vrmModule = await import('@pixiv/three-vrm');
        const { VRMLoaderPlugin, VRMUtils } = vrmModule;
        // VRMSpringBoneLoaderPlugin may be exported separately in some builds
        const VRMSpringBoneLoaderPlugin = vrmModule.VRMSpringBoneLoaderPlugin ?? null;

        return new Promise((resolve, reject) => {
            const gltfLoader = useKTX2(new GLTFLoader(), (window.iris || window.IRIS)?.renderer);
            gltfLoader.register((parser) => {
                const opts = {};
                if (VRMSpringBoneLoaderPlugin) {
                    opts.springBonePlugin = new VRMSpringBoneLoaderPlugin(parser);
                }
                return new VRMLoaderPlugin(parser, opts);
            });

            const loadAndScale = async (scene, vrmInstance) => {
                let box = new THREE.Box3().setFromObject(scene);
                let size = box.getSize(new THREE.Vector3());

                // Up-axis auto-correction: ONLY for generic GLB files.
                // VRM files are always Y-up by spec, and the bounding-box
                // heuristic misfires on characters with wide accessories
                // (wings, capes, big skirts) — their X-width can exceed
                // their Y-height, which would wrongly flag them as "X-up"
                // and tip the avatar onto its side.
                if (!vrmInstance) {
                    if (size.z > size.y && size.z >= size.x) {
                        // Z-up model (common Blender export)
                        scene.rotation.x = -Math.PI / 2;
                        scene.updateMatrixWorld(true);
                        box.setFromObject(scene);
                        size = box.getSize(new THREE.Vector3());
                    } else if (size.x > size.y && size.x >= size.z) {
                        // X-up model (very unusual — keep for safety)
                        scene.rotation.z = Math.PI / 2;
                        scene.updateMatrixWorld(true);
                        box.setFromObject(scene);
                        size = box.getSize(new THREE.Vector3());
                    }
                }

                const centre = box.getCenter(new THREE.Vector3());
                const targetHeight = 2.5; // fit in view
                const scale = targetHeight / (size.y || 1);
                scene.scale.setScalar(scale);
                scene.position.y = -centre.y * scale;
                scene.position.x = -centre.x * scale;

                this.group.add(scene);
                this.scene.add(this.group);

                // Orient the avatar to face the camera. For VRMs we derive the
                // facing direction from the shoulder axis instead of assuming a
                // fixed convention, because exports from different tools bake
                // different rotations into the file.
                if (vrmInstance) {
                    this.group.updateMatrixWorld(true);
                    // NOTE: `vrmInstance` here is just a boolean flag from
                    // the call site; the real VRM object lives on `this.vrm`.
                    const yaw = this._computeFacingYaw(this.vrm);
                    // Apply correction on the outer group so it is always a
                    // world-space yaw, independent of any local rotations on
                    // the inner scene (e.g. Z-up X-flip).
                    this.restYaw = yaw !== null ? yaw : 0;
                    this.group.rotation.y = this.restYaw;
                    console.log(`[Avatar] restYaw = ${this.restYaw.toFixed(3)} rad (${(this.restYaw * 180 / Math.PI).toFixed(1)}°)`);

                    // Second-line defence: eye-depth verification. The
                    // shoulder-axis heuristic above can fail on VRMs with
                    // arms drooped straight down (axis ill-defined) or
                    // asymmetric A-pose arms. Borrowed from clawatar
                    // (src/vrm-loader.ts auto_correct_facing_direction):
                    // after applying restYaw, check whether the eye bones
                    // ended up IN FRONT of the head bone relative to the
                    // camera. If they're behind, the avatar got flipped
                    // 180° wrong — slap another π on top.
                    this.group.updateMatrixWorld(true);
                    const flipped = this._verifyFacingByEyeDepth(this.vrm);
                    if (flipped) {
                        this.restYaw += Math.PI;
                        this.group.rotation.y = this.restYaw;
                        this.group.updateMatrixWorld(true);
                        console.log('[Avatar] eye-depth check found avatar facing AWAY — applied +180°');
                    }

                    // Retarget the authored neutral pose before capturing any
                    // idle/IK/physics baseline. Never force model-specific arm angles.
                    await this._applyRestPose(this.vrm);

                    // CRITICAL: recapture springbone rest state AFTER we
                    // rotated the group and applied the A-pose. The VRM
                    // loader plugin calls setInitState() during parse,
                    // BEFORE our 180° yaw flip — so on its books the
                    // whole skeleton has "teleported" 180° around Y in
                    // one frame, which triggers a huge impulse and the
                    // hair/cloth swings wildly forward on first update.
                    // Re-capture now (with matrices freshly updated) so
                    // physics starts from equilibrium.
                    this.group.updateMatrixWorld(true);
                    try {
                            const sbm = this.vrm.springBoneManager;
                            if (sbm?.setInitState) {
                                sbm.setInitState();
                            }
                            // Reset per-joint velocities too (some builds expose
                            // this as `reset()` on each joint or on the manager).
                            if (typeof sbm?.reset === 'function') {
                                sbm.reset();
                            } else if (sbm?.joints) {
                                for (const j of sbm.joints) {
                                    if (typeof j.reset === 'function') j.reset();
                                }
                            }
                    } catch (e) {
                        console.warn('[Avatar] springbone re-init failed:', e);
                    }
                }
            };

            const onModel = async (gltf) => {
                    try {
                        this.group.add(gltf.scene); // Own resources even if preparation fails.
                        const vrm = gltf.userData.vrm;
                        if (vrm) {
                            // three-vrm v3 deprecated removeUnnecessaryJoints;
                            // combineSkeletons is its supported performance path.
                            if (!pmx) {
                                if (typeof VRMUtils.combineSkeletons === 'function') {
                                    VRMUtils.combineSkeletons(gltf.scene);
                                } else {
                                    VRMUtils.removeUnnecessaryJoints?.(gltf.scene);
                                }
                                VRMUtils.removeUnnecessaryVertices(gltf.scene);
                            }
                            // A skinned mesh's bounds are measured once, in the first pose. Seated,
                            // her eyes are 0.75 away from theirs and a close camera culled them.
                            vrm.scene.traverse((obj) => { if (obj.isMesh) obj.frustumCulled = false; });
                            this.vrm = vrm;
                            this.group.visible = false;
                            await loadAndScale(vrm.scene, true);
                            this.group.visible = true;
                            // Texture quality pass. Three.js + GLTFLoader create
                            // textures with `anisotropy = 1` and (sometimes)
                            // wrong color space — that's why first-boot loads
                            // look subtly pixelated on the avatar's face and
                            // accessories, while a drag-drop reload "magically"
                            // looks crisper (the GPU re-samples with warmer
                            // mipmap caches, masking the underlying setting).
                            // Bump anisotropy to the GPU max and confirm sRGB
                            // on color slots so the look is identical from the
                            // very first frame.
                            try {
                                this._enhanceTextures(vrm.scene);
                            } catch (e) {
                                console.warn('[Avatar] texture enhancement failed:', e);
                            }
                            const hasSprings = !!vrm.springBoneManager;
                            const exprs = this._listExpressions().join(', ') || 'none';
                            console.log(`✅ VRM loaded — spring bones: ${hasSprings}, expressions: ${exprs}`);
                            if (!pmx && !hasSprings) console.warn('⚠️  No spring bone manager found — physics inactive');

                            // Runtime auto-registration of cloth springbones for
                            // models that have skirt/hair/ribbon bones but did
                            // not declare them in the VRMC_springBone extension.
                            // No-op on models without cloth bones at all.
                            if (!pmx) {
                                await autoRegisterSpringBones(vrm).catch(e =>
                                    console.warn('[auto-springbones] failed:', e)
                                );
                            }
                            this.breeze.attach(vrm);

                            // ── DIAGNOSTIC DUMP: all bones + springbone joints ──
                            // Useful when investigating skirt/hair jiggle rigs,
                            // but it's pure noise in normal operation. Gated
                            // behind `window.iris.debug` — set that flag from
                            // the console when you need the dump.
                            if ((window.iris || window.IRIS)?.debug) {
                                try {
                                    const allBones = [];
                                    vrm.scene.traverse((obj) => {
                                        if (obj.isBone) allBones.push(obj.name);
                                    });
                                    console.group('[VRM diagnostic]');
                                    console.log(`Total bones: ${allBones.length}`);
                                    // Filter for skirt / hair / cloth-likely names
                                    const clothish = allBones.filter(n =>
                                        /skirt|hair|ribbon|coat|jacket|cloth|bust|breast|tail|ponytail/i.test(n)
                                    );
                                    console.log('Possible cloth/secondary bones:', clothish);
                                    console.log('All bones:', allBones);
                                    // Springbone joints (if any)
                                    const sbm = vrm.springBoneManager;
                                    if (sbm?.joints) {
                                        const jointNames = Array.from(sbm.joints).map(j => j.bone?.name).filter(Boolean);
                                        console.log(`SpringBone joints active: ${jointNames.length}`, jointNames);
                                    }
                                    console.groupEnd();
                                } catch (e) {
                                    console.warn('[VRM diagnostic] failed:', e);
                                }
                            }
                        } else {
                            // Standard GLB/GLTF — no VRM extension detected
                            this.vrm = null;
                            await loadAndScale(gltf.scene, false);
                            console.warn('⚠️  Loaded as STATIC GLB (no VRMC_vrm extension found). Physics inactive.');
                        }

                        // Auto-play embedded animations ONLY for non-VRM GLBs.
                        //
                        // Many VRM files ship with baked preview clips (T-pose,
                        // idle loop, sample pose) that the author embedded for
                        // their editor. If we auto-play them, the mixer writes
                        // to the humanoid bones every frame and overrides our
                        // procedural rest pose + the idle_system animations —
                        // which is why the character appeared stuck with arms
                        // raised in a V above her head.
                        //
                        // For VRM we only play clips explicitly (VRMA loader
                        // driven by idle_system.trigger()).
                        if (!vrm && gltf.animations && gltf.animations.length > 0) {
                            this.mixer = new THREE.AnimationMixer(gltf.scene);
                            const idle = this.mixer.clipAction(gltf.animations[0]);
                            idle.play();
                        } else if (vrm && gltf.animations && gltf.animations.length > 0) {
                            console.log(
                                `[Avatar] VRM ships with ${gltf.animations.length} embedded ` +
                                `animation clip(s) — ignoring (use VRMA for poses instead).`,
                            );
                        }
                        // Spin up the IK solver once the VRM is fully loaded.
                        // Skipped for plain GLBs (no humanoid skeleton → no
                        // bone names to target); the solver no-ops gracefully
                        // if any required bones are absent, so we still create
                        // it for partially-rigged VRMs.
                        if (vrm?.humanoid) {
                            try {
                                this._ikSolver = new IKSolver(vrm);
                                console.log('[Avatar] IK solver ready');
                            } catch (e) {
                                console.warn('[Avatar] IK solver init failed:', e);
                                this._ikSolver = null;
                            }
                        }
                        resolve();
                    } catch (error) {
                        reject(error);
                    }
            };
            if (pmx) {
                const load = async () => {
                    const response = await fetch(url, { cache: 'no-store' });
                    if (!response.ok) throw new Error('No se pudo leer el ZIP MMD guardado.');
                    const { loadPmxAvatar } = await import('./pmx_avatar.js');
                    const vrm = await loadPmxAvatar(new Uint8Array(await response.arrayBuffer()), { simulate: true });
                    await onModel({ scene: vrm.scene, userData: { vrm }, animations: [] });
                };
                load().catch(reject);
            } else {
                gltfLoader.load(url, onModel, undefined, reject);
            }
        });
    }

    /**
     * Walk the VRM scene and upgrade every texture's quality knobs to
     * match what the renderer can actually do. Two things to fix:
     *
     *   1. **Anisotropic filtering.** Three.js + GLTFLoader create
     *      textures with `anisotropy = 1` regardless of the renderer's
     *      `maxAnisotropy` (~16 on any modern GPU). On the very first
     *      frames after boot this reads as soft / pixelated edges on
     *      surfaces seen at glancing angles — exactly the "looks better
     *      after I drag-drop the same file" effect.
     *
     *   2. **Color space.** GLTFLoader sets `colorSpace = SRGBColorSpace`
     *      on textures it identifies as albedo (`map`, `emissiveMap`),
     *      but VRM MToon shaders also use a `shadeMultiplyTexture` and a
     *      `rimMultiplyTexture` which are technically color and need
     *      sRGB; the loader leaves those as linear and the result is
     *      desaturated face shading until something else refreshes the
     *      texture state. We coerce them explicitly so the look is
     *      identical from frame 0 instead of warming up.
     *
     * Idempotent — running it twice does nothing the second time.
     */
    _enhanceTextures(root) {
        const renderer = (window.iris || window.IRIS)?.renderer;
        const maxAniso = renderer?.capabilities?.getMaxAnisotropy?.() ?? 8;
        // Names of texture slots that carry COLOR data and should be
        // sRGB-encoded on the GPU. The rest (normal maps, metalness,
        // roughness, occlusion, matcap) stay linear.
        const COLOR_SLOTS = new Set([
            'map', 'emissiveMap',
            'shadeMultiplyTexture', 'rimMultiplyTexture',
            'matcapTexture', 'sphereAddTexture',
        ]);

        root.traverse((obj) => {
            const mat = obj.material;
            if (!mat) return;
            // A mesh can have either a single material or an array.
            const materials = Array.isArray(mat) ? mat : [mat];
            for (const m of materials) {
                for (const key of Object.keys(m)) {
                    const value = m[key];
                    if (!value || !value.isTexture) continue;
                    if (value.anisotropy < maxAniso) {
                        value.anisotropy = maxAniso;
                        value.needsUpdate = true;
                    }
                    if (COLOR_SLOTS.has(key) &&
                        value.colorSpace !== THREE.SRGBColorSpace) {
                        value.colorSpace = THREE.SRGBColorSpace;
                        value.needsUpdate = true;
                    }
                }
                m.needsUpdate = true;
            }
        });
    }

    /** Apply the supplied VRMA on the normalized humanoid, then release its loader. */
    async _applyRestPose(vrmInstance) {
        if (!vrmInstance?.humanoid) return false;
        const player = new VRMAnimationPlayer(vrmInstance);
        try {
            const url = new URL('../animations/iris_idle.vrma', import.meta.url).href;
            if (!await player.load('__rest', url)) return false;
            const applied = player.applyPose('__rest');
            vrmInstance.humanoid.update();
            vrmInstance.scene.updateMatrixWorld(true);
            return applied;
        } catch (error) {
            console.warn('[Avatar] Could not apply authored rest pose:', error);
            return false;
        } finally {
            player.dispose();
        }
    }

    dispose() {
        clearTimeout(this._blinkTimeout);
        this._blinkTimeout = null;
        this._headAnim = null;
        this.vrm?.disposePhysics?.();
        this.breeze?.detach();
        this.cancelIK();
        this._ikSolver = null;
        if (this.mixer) {
            const root = this.mixer.getRoot();
            this.mixer.stopAllAction();
            this.mixer.uncacheRoot(root);
            this.mixer = null;
        }
    }

    /**
     * Derive the yaw rotation needed to make the character face the camera
     * (Three.js camera default looks down -Z toward origin, so "facing camera"
     * means the character's forward vector should be -Z).
     *
     * Strategy: read world positions of leftUpperArm and rightUpperArm. The
     * shoulder line from right→left should point along +X when facing camera.
     * Returns the yaw delta in radians, or null if bones aren't available.
     */
    /**
     * Eye-depth-based facing check. Returns ``true`` when the avatar
     * is facing AWAY from the camera (i.e. needs a 180° flip on top
     * of whatever ``_computeFacingYaw`` already produced).
     *
     * Anatomically, eyes are always in front of the head bone. After
     * applying ``restYaw`` we expect the eye bones' world positions
     * to have a smaller Z than the head bone (camera looks down -Z,
     * so "in front" = lower Z). If eyes end up BEHIND the head along
     * Z, the model is faced away.
     *
     * Falls back to head-quaternion forward axis test when the
     * humanoid doesn't expose ``leftEye``/``rightEye`` bones (common
     * for stylized VRMs that bake eyes into morph targets only).
     *
     * Borrowed from clawatar/src/vrm-loader.ts.
     */
    _verifyFacingByEyeDepth(vrmInstance) {
        const humanoid = vrmInstance?.humanoid;
        if (!humanoid) return false;
        const getBone = (name) =>
            humanoid.getNormalizedBoneNode?.(name)
            ?? humanoid.getBoneNode?.(name);

        const head = getBone('head');
        if (!head) return false;
        const headPos = new THREE.Vector3();
        head.getWorldPosition(headPos);

        const leftEye  = getBone('leftEye');
        const rightEye = getBone('rightEye');
        if (!leftEye || !rightEye) {
            // No eye bones — common for stylized VRMs that bake eye
            // movement into morph targets only. We DON'T fall back to
            // a head-forward-axis test here: that test requires us to
            // know the head bone's local "forward" convention, which
            // varies between VRM 0.x / VRM 1.0 / @pixiv/three-vrm
            // normalization. Getting that wrong would spuriously
            // 180°-flip valid avatars. When eye bones are unavailable
            // we trust the shoulder-axis correction from
            // `_computeFacingYaw` alone and skip this safety net.
            return false;
        }
        const lp = new THREE.Vector3();
        const rp = new THREE.Vector3();
        leftEye.getWorldPosition(lp);
        rightEye.getWorldPosition(rp);
        const eyeCenterZ = (lp.z + rp.z) * 0.5;
        // Three.js default camera sits on +Z looking toward origin.
        // "In front of head, closer to camera" therefore means LARGER
        // world Z (toward +Z). If eyes ended up FURTHER from camera
        // than the head bone (eyeZ < headZ), the avatar is facing
        // AWAY — flag for the 180° flip.
        //
        // Caveat: this is a world-space heuristic that assumes the
        // load-time camera is at +Z. We validate that's true in the
        // call site (camera not yet rotated by user input during the
        // VRM load callback). Don't move this check to a context
        // where the camera might have been re-oriented.
        return eyeCenterZ < headPos.z - 0.005;
    }

    _computeFacingYaw(vrmInstance) {
        const humanoid = vrmInstance?.humanoid;
        if (!humanoid) return null;
        const leftArm  = humanoid.getNormalizedBoneNode?.('leftUpperArm')
                      ?? humanoid.getBoneNode?.('leftUpperArm');
        const rightArm = humanoid.getNormalizedBoneNode?.('rightUpperArm')
                      ?? humanoid.getBoneNode?.('rightUpperArm');
        if (!leftArm || !rightArm) return null;

        const lp = new THREE.Vector3();
        const rp = new THREE.Vector3();
        leftArm.getWorldPosition(lp);
        rightArm.getWorldPosition(rp);

        const axis = new THREE.Vector3().subVectors(lp, rp);
        axis.y = 0;
        if (axis.lengthSq() < 1e-6) return null;
        axis.normalize();

        // Current shoulder-axis yaw in world XZ plane
        const currentAngle = Math.atan2(axis.z, axis.x);
        // Want axis to point along +X → target angle 0
        return -currentAngle;
    }

    // ── Public API (the AvatarManager delegates to these) ──

    setEmotion(name) {
        const preset = VRM_EMOTION_PRESETS[name] || VRM_EMOTION_PRESETS.neutral;
        // Snapshot the OUTGOING expression weights so `_updateExpressions`
        // can leak a residual (the "expression leaks" Tier 2 feature) —
        // when feliz → neutral, the smile doesn't snap to zero; it
        // drops to a floor first, lingers, and then decays. Without
        // this snapshot the decay would happen from `current` (whatever
        // value the lerp is at right this frame) which makes the
        // residual look erratic if the emotion changed mid-blend.
        this._lastEmotionPeak = { ...this.currentExpressions };
        // Bump the "leak timer" so the floor-hold phase runs from now.
        this._emotionLeakStartedAt = performance.now() / 1000;
        this.targetExpressions = { ...preset };
        this._currentEmotionName = name || 'neutral';
    }

    /** Her hair and clothes start again from where she is now, after a jump. */
    settleSprings() {
        this.group?.updateMatrixWorld(true);
        this.vrm?.springBoneManager?.reset?.();
        this.vrm?.resetPhysics?.();
    }

    /** How much the hands follow IRIS' this frame: 0 while no clip touches anything. */
    setHandReach(weight) {
        if (!this.vrm) return;
        if (this._armReach?.vrm !== this.vrm) this._armReach = new ArmReach(this.vrm, this.group);
        this._armReach.weight = weight;
    }

    /** How far her skirt rests over her thighs: 0 standing, 1 seated. */
    setSeatDrape(weight) {
        if (!this.vrm) return;
        if (this._seatDrape?.vrm !== this.vrm) {
            this._seatDrape = new SeatDrape(this.vrm);
            this._seatDrape.vrm = this.vrm;
        }
        this._seatDrape.set(weight);
    }

    /** How far her back rests on the bed: her hips then take IRIS' place, whatever her size. */
    setLyingHips(weight, delta) {
        if (!this.vrm) return;
        if (this._lyingHips?.vrm !== this.vrm) this._lyingHips = new LyingHips(this.vrm);
        this._lyingHips.set(weight, delta);
    }

    /** Lying on the bed: a PMX avatar's hair keeps its modelled shape (pmx_physics.js). */
    holdHair(on) {
        this.vrm?.holdHair?.(on);
    }

    /** Held lying on her back or front: once, read where her body rests (LyingHips). */
    measureLyingHips(kind) {
        if (!this.vrm) return null;
        if (this._lyingHips?.vrm !== this.vrm) this._lyingHips = new LyingHips(this.vrm);
        return this._lyingHips.measure(kind);
    }

    setMouthOpen(v) {
        this.mouthOpenness = Math.max(0, Math.min(1, v));
    }

    playAnimation(name) {
        // Each gesture combines:
        //   • a facial expression (instant)
        //   • a head-bone movement queued via _triggerHeadAnim()
        // Animations affect the VRM's `head` humanoid bone — NOT the
        // root group — so they layer on top of IdleSystem's body sway
        // instead of being overwritten by it next frame.
        const map = {
            idle:          () => {},  // no-op — natural idle continues
            saludar_feliz: () => {
                this.setEmotion('feliz');
                this._triggerHeadAnim('nod_big', 800);
            },
            pensar:        () => {
                this.setEmotion('pensativa');
                this._triggerHeadAnim('tilt', 1200);
            },
            asentir:       () => this._triggerHeadAnim('nod', 600),
            negar:         () => this._triggerHeadAnim('shake', 800),
        };
        (map[name] || (() => {}))();
    }

    update(delta, elapsed, { deferVRM = false } = {}) {
        // Mixer always runs (for embedded animation clips in any GLB/VRM)
        if (this.mixer) this.mixer.update(delta);

        if (!this.vrm) {
            // Standard GLB (no VRM skeleton) — keep basic life animations
            this._updateBreathing(elapsed);
            return;
        }

        // Smooth expression interpolation
        this._updateExpressions(delta);

        // Lip sync → aa viseme
        this._updateMouth(delta);

        // Blinking
        this._updateBlink(delta);

        // Subtle breathing via hips bone
        this._updateBreathing(elapsed);

        // Resolve queued IK before the final normalized-pose transfer.
        // Authored clips own the body, so stale intents must be discarded.
        if (this.animationOwnsRootMotion) this._ikSolver?.cancel();
        else this._ikSolver?.solve();

        // Head gesture animator (nod/shake/tilt for playAnimation).
        // Runs AFTER IK so the gesture's head-bone rotation isn't
        // immediately overridden by a queued lookAt — gestures are
        // short (≤1.2s) and should fully express, then bow out.
        if (!this.animationOwnsRootMotion && !this._headAnim?.over) this._applyHeadAnim();
        if (!deferVRM) this.finalizeFrame(delta);
    }

    /** Transfer the final normalized pose before simulating hair/clothing. */
    finalizeFrame(delta) {
        try {
            // A gesture on top of a clip goes after the clip has written this frame's pose.
            if (this._headAnim?.over) this._applyHeadAnim();
            this._armReach?.apply();
            this._poseFace?.apply(this, delta);
            this.breeze?.apply(delta);
            this.vrm?.update(delta);
        } finally {
            this._lyingHips?.restoreLegs();
        }
    }

    // ── IK Public API ──
    //
    // Each call queues an intent on the IK solver; intents are flushed
    // once per frame in `update()`. Intents are NOT sticky — callers
    // must re-queue every frame to keep the bone aimed/reached. This
    // matches how clip animation works and gives smooth release when
    // the caller stops calling.
    //
    // Explicit animations own their bones; update() gates queued IK while
    // a gesture or its return transition is active.

    /** Aim the head at a world-space point. */
    lookAt(targetWorld, weight = 1.0) {
        this._ikSolver?.lookAtWorld(targetWorld, weight);
    }

    /** Move a hand to land at a world-space point. side: 'left'|'right'. */
    reach(side, targetWorld, weight = 1.0) {
        this._ikSolver?.reach(side, targetWorld, weight);
    }

    /** Aim the index-finger axis at a world-space point. */
    point(side, targetWorld, weight = 1.0) {
        this._ikSolver?.point(side, targetWorld, weight);
    }

    /** Drop pending IK intents without applying them. */
    cancelIK() {
        this._ikSolver?.cancel();
    }

    // ── Private VRM helpers ──

    _listExpressions() {
        if (!this.vrm?.expressionManager) return [];
        return this.vrm.expressionManager.expressions.map(e => e.expressionName);
    }

    _setExpression(name, value) {
        if (!this.vrm?.expressionManager) return;
        try {
            this.vrm.expressionManager.setValue(name, value);
        } catch { /* blendshape not found — skip */ }
    }

    _updateExpressions(dt) {
        // Two emotional smoothing improvements layered here — see the
        // Tier 2 spec for context:
        //
        // 1. MOOD PERSISTENCE — when an expression's TARGET drops
        //    (typically because the LLM replied with `emocion:
        //    neutral` after a `feliz`), we slow the descent so the
        //    feeling doesn't cut in half a second. Rise stays snappy
        //    (excitement should appear fast) but fall has a longer
        //    half-life.
        //
        // 2. EXPRESSION LEAKS — instead of decaying directly to 0,
        //    the curve eases to a "floor" first (~30% of the peak),
        //    holds there for ~1.2s, then continues to zero. That
        //    residue is what gives the avatar emotional continuity
        //    between turns. Without it, every `feliz → neutral` looks
        //    like the smile got switched off with a relay.
        //
        // We don't apply leaks to viseme expressions (`aa`, `oh`) —
        // those are speech and must respond instantly. Leak logic only
        // applies to the keys present in VRM_EMOTION_PRESETS.
        // Two-stage emotional decay (matches Tier 2 spec):
        //
        //   peak ─fast drop─► floor (~30%) ─hold 1.2s─► ─slow fade─► 0
        //
        // The original constants had this inverted (slow approach to
        // floor, faster fade afterward) which produced a "continuous
        // dim" instead of the "drop, linger, fade" shape that gives
        // mood persistence its character.
        //
        // Speeds are lerp coefficients in per-second. Half-life for
        // a lerp with speed `s` is `ln(2)/s`, so:
        //   RISE          s=4   → half-life ~170ms (excited fast)
        //   FALL_TO_FLOOR s=3.5 → ~200ms to reach the floor zone
        //   FALL_AFTER    s=0.45 → ~1.5s half-life ≈ 6-8s to reach 0
        //                        (matches spec "fall ~6-8s")
        const FLOOR_RATIO       = 0.30;
        const FLOOR_HOLD_SEC    = 1.2;
        const RISE_SPEED        = 4;
        const FALL_SPEED        = 3.5;   // fast to floor
        const FALL_SPEED_AFTER  = 0.45;  // slow drift to zero

        const leakStart = this._emotionLeakStartedAt ?? 0;
        const elapsed = (performance.now() / 1000) - leakStart;
        const inFloorHold = elapsed < FLOOR_HOLD_SEC;

        for (const [name, target] of Object.entries(this.targetExpressions)) {
            const current = this.currentExpressions[name] || 0;
            let effectiveTarget = target;

            // Only the recognized "emotion" channels participate in
            // leak behavior; visemes pass through untouched.
            const peak = this._lastEmotionPeak?.[name] ?? 0;
            const isEmotionChannel = (name in (VRM_EMOTION_PRESETS.neutral)) ||
                                     (name in (VRM_EMOTION_PRESETS.feliz));

            if (isEmotionChannel && target < peak - 0.01) {
                // The target has dropped from this channel's previous
                // peak — we're in a "fading" state for this expression.
                // During the hold window, don't let `current` go below
                // the floor; after the window, let it continue to its
                // real target.
                if (inFloorHold) {
                    effectiveTarget = Math.max(target, peak * FLOOR_RATIO);
                }
            }

            const rising = effectiveTarget > current;
            const speed = (rising
                ? RISE_SPEED
                : (inFloorHold ? FALL_SPEED : FALL_SPEED_AFTER)) * dt;
            const next = current + (effectiveTarget - current) * speed;
            this.currentExpressions[name] = next;
            this._setExpression(name, next);
        }
    }

    _updateMouth(dt) {
        // Map mouth openness → VRM "aa" viseme (open mouth)
        const current = this.currentExpressions['aa'] || 0;
        const target = this.mouthOpenness;
        const next = current + (target - current) * 8 * dt;
        this.currentExpressions['aa'] = next;
        this._setExpression('aa', next);

        // "oh" is the round-mouth viseme used during speech to fill out
        // the audio. When mouthOpenness is essentially zero (silence /
        // idle), we MUST NOT write `oh = 0` here — MicroLife's lip-idle
        // layer uses `oh` for the subtle micro-purse it fires during
        // idle, and an unconditional write here was wiping that signal
        // every frame, making lip-idle invisible. Threshold matches the
        // one MicroLife uses to decide if speech is active.
        const SILENCE_THRESHOLD = 0.04;
        if (this.mouthOpenness > SILENCE_THRESHOLD) {
            const ohTarget = this.mouthOpenness * 0.3;
            this._setExpression('oh', ohTarget);
            this._speechOhActive = true;
        } else if (this._speechOhActive) {
            this._setExpression('oh', 0);
            this._speechOhActive = false;
        }
    }

    /**
     * Give an explicit pose/clip exclusive ownership of the eyelids.
     *
     * Some authored animations intentionally keep the eyes closed.  A legacy
     * timeout completing halfway through such a clip used to write `blink=0`
     * and briefly force the eyes open.  Cancel the timeout and reset its
     * state at the ownership boundary; the animation mixer is then the only
     * writer until IdleSystem releases it.
     */
    setAnimationBlinkSuppressed(suppressed, ownsBody = suppressed) {
        const next = Boolean(suppressed);
        const body = Boolean(ownsBody);
        const blinkChanged = this.animationOwnsBlink !== next;
        if (!blinkChanged && this.animationOwnsRootMotion === body) return;
        this.animationOwnsBlink = next;
        this.animationOwnsRootMotion = body;
        if (blinkChanged) {
            if (this._blinkTimeout) {
                clearTimeout(this._blinkTimeout);
                this._blinkTimeout = null;
            }
            this._naturalBlink?.reset();
            this.isBlinking = false;
            this.blinkTimer = 0;
            this.nextBlink = next ? Number.POSITIVE_INFINITY : 1.4 + Math.random() * 2.2;
            // A body-only transition must not restart an ongoing blink.
            this._setExpression('blink', 0);
        }
        // Companion does not mount MicroLife; its legacy breathing writer
        // otherwise keeps forcing the avatar root toward a sine-wave Y while
        // a clip or return transition owns that same root.
        if (this.animationOwnsRootMotion && !this.microLifeOwnsBreathing) {
            this.group.position.y = 0;
        }
    }

    _updateBlink(dt) {
        if (this.microLifeOwnsBlink || this.animationOwnsBlink) return;
        const value = this._naturalBlink.update(dt, this.poseFacialProfile?.eyes);
        this.isBlinking = value > 0;
        const em = this.vrm?.expressionManager;
        // Combined blink is standard; rigs with only separate lids work too.
        if (em?.getExpression('blink')?.binds?.length) this._setExpression('blink', value);
        else {
            this._setExpression('blinkLeft', value);
            this._setExpression('blinkRight', value);
        }
    }

    _updateBreathing(t) {
        // Yielded to MicroLife (see _updateBlink comment). MicroLife
        // owns a state-modulated breathing rhythm and writes to
        // group.position.y directly, with a re-baselining trick so it
        // can coexist with idle/walk systems that also touch the root.
        if (this.microLifeOwnsBreathing || this.animationOwnsRootMotion) return;
        // Subtle vertical sway on the root
        this.group.position.y = Math.sin(t * 1.2) * 0.01;
    }

    /** @deprecated Routes through _triggerHeadAnim('nod') now.
     *
     *  The old implementation rotated ``this.group`` (the avatar root)
     *  via setTimeout, but IdleSystem._updatePose overwrites that
     *  rotation every frame — so the nod was invisible from the
     *  moment the avatar entered idle (which it always is when not
     *  walking). Bone-based animator works because it touches the
     *  ``head`` humanoid bone, which idle doesn't touch. */
    _headNod() {
        this._triggerHeadAnim('nod', 600);
    }

    /** @deprecated Same idle-overwrite story as _headNod. */
    _headShake() {
        this._triggerHeadAnim('shake', 800);
    }
}

