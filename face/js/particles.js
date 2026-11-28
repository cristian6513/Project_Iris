/**
 * IRIS Particles — Ambient floating-particle field
 *
 * Gold (#D4A017) and blue (#A8D8EA) floating particles
 * that react to IRIS's emotional state with:
 * - Emotion-driven colors and intensity
 * - Fire/sparkle effects for strong emotions
 * - Aura rings for active states
 * - Breathing glow pulse
 */

import * as THREE from 'three';

const PARTICLE_COUNT = 320;
const SPARKLE_COUNT = 72;
const GOLD = new THREE.Color(0xD4A017);
const BLUE = new THREE.Color(0xA8D8EA);
const RED = new THREE.Color(0xff4444);
const PINK = new THREE.Color(0xff9ccf);

// The sparkles burst around her head (2026-11-26). They used to fill a 2x2 box at the scene
// origin, so in the room (Iris on the bed at x≈3.1, say) they floated in the middle of it.
export const SPARKLE_SPREAD = {
    // Around the head, in world units: outside her face, out to beside her shoulders.
    innerRadius: 0.22, outerRadius: 0.6,
    // From her chest to just above her hair.
    below: 0.45, above: 0.3,
};
// Where they burst before an avatar has loaded: the middle of the old box.
const SPARKLE_DEFAULT_ANCHOR = new THREE.Vector3(0, 1, 0);
// How quickly they catch up when she moves while they are lit (per second).
const SPARKLE_FOLLOW_RATE = 4;
// Closer to the camera than this they stop growing: in video 6's close shots (camera 1 to 1.8
// from her face) one passing in front of her would otherwise cover her eye.
export const SPARKLE_NEAREST = 1.5;

/**
 * A soft round dot. Without a map PointsMaterial draws each point as a flat square, and near the
 * camera the gold sparkles read as orange squares (video 6, 2026-11-26). White, with the fall-off
 * in alpha, so the material colour still tints it. Null where there is no canvas (Node).
 */
export function createSparkleTexture(doc = globalThis.document, size = 64) {
    const canvas = doc?.createElement?.('canvas');
    if (!canvas) return null;
    canvas.width = size;
    canvas.height = size;
    const ctx = canvas.getContext?.('2d');
    if (!ctx) return null;
    const r = size / 2;
    const glow = ctx.createRadialGradient(r, r, 0, r, r, r);
    glow.addColorStop(0, 'rgba(255,255,255,1)');
    glow.addColorStop(0.18, 'rgba(255,255,255,0.9)');
    glow.addColorStop(0.42, 'rgba(255,255,255,0.32)');
    glow.addColorStop(0.72, 'rgba(255,255,255,0.06)');
    glow.addColorStop(1, 'rgba(255,255,255,0)');
    ctx.fillStyle = glow;
    ctx.fillRect(0, 0, size, size);
    return new THREE.CanvasTexture(canvas);
}

/**
 * PointsMaterial has one `size` for every point and ignores a per-vertex size attribute. This
 * multiplies `size` by the per-point `attribute`, and with `nearest` measures the distance
 * shrink from no closer than that (both lines are three 0.170's points.glsl). If a later three
 * changes a line, that part is left out instead of breaking the frame.
 */
export function scalePointsPerVertex(material, { attribute = 'sparkleScale', nearest = 0 } = {}) {
    const sizeLine = 'gl_PointSize = size;';
    const shrinkLine = 'if ( isPerspective ) gl_PointSize *= ( scale / - mvPosition.z );';
    material.onBeforeCompile = (shader) => {
        let vertex = shader.vertexShader;
        if (vertex.includes(sizeLine)) {
            vertex = `attribute float ${attribute};\n` + vertex.replace(sizeLine, `gl_PointSize = size * ${attribute};`);
        } else {
            console.warn(`particles.js: no «${sizeLine}» in this three's points shader; the sparkles keep one size.`);
        }
        if (nearest > 0 && vertex.includes(shrinkLine)) {
            vertex = vertex.replace(shrinkLine,
                `if ( isPerspective ) gl_PointSize *= ( scale / max( - mvPosition.z, ${nearest.toFixed(4)} ) );`);
        } else if (nearest > 0) {
            console.warn('particles.js: no distance shrink line in this three\'s points shader; near sparkles are not capped.');
        }
        shader.vertexShader = vertex;
    };
    material.customProgramCacheKey = () => `points-scaled-by-${attribute}-nearest-${nearest}`;
    return material;
}

/** One sparkle's place around the anchor: a ring outside her face, from her chest to above her hair. */
export function sparkleOffset(random = Math.random, spread = SPARKLE_SPREAD) {
    const angle = random() * Math.PI * 2;
    const radius = spread.innerRadius + random() * (spread.outerRadius - spread.innerRadius);
    return [
        Math.cos(angle) * radius,
        -spread.below + random() * (spread.below + spread.above),
        Math.sin(angle) * radius,
    ];
}

export class ParticleSystem {
    /**
     * @param {THREE.Scene} scene
     * @param {{getAnchor?: (target: THREE.Vector3) => THREE.Vector3|null}} options - where the
     *   sparkles burst: her head in the world, written into `target`; null before she loads
     */
    constructor(scene, { getAnchor = () => null } = {}) {
        this.scene = scene;
        this.getAnchor = getAnchor;
        this._anchor = new THREE.Vector3();
        this.points = null;
        this.pointsSecondary = null;
        this.auraRing = null;
        this.geometry = null;
        this.velocities = null;
        this.basePositions = null;
        this.mood = 'neutral'; // affects speed and spread
        this.moodIntensity = 1.0;
        this.isSpeaking = false;
        this._particleFrame = 0;
    }

    init() {
        this._createMainParticles();
        this._createAuraRing();
        this._createSparkles();
    }

    _createMainParticles() {
        this.geometry = new THREE.BufferGeometry();
        const positions = new Float32Array(PARTICLE_COUNT * 3);
        const colors = new Float32Array(PARTICLE_COUNT * 3);
        const sizes = new Float32Array(PARTICLE_COUNT);
        this.basePositions = new Float32Array(PARTICLE_COUNT * 3);
        this.velocities = new Float32Array(PARTICLE_COUNT * 3);

        for (let i = 0; i < PARTICLE_COUNT; i++) {
            // Distribute in a large sphere around the avatar
            const radius = 3 + Math.random() * 8;
            const theta = Math.random() * Math.PI * 2;
            const phi = Math.random() * Math.PI;

            const x = radius * Math.sin(phi) * Math.cos(theta);
            const y = radius * Math.sin(phi) * Math.sin(theta) - 1;
            const z = radius * Math.cos(phi);

            positions[i * 3] = x;
            positions[i * 3 + 1] = y;
            positions[i * 3 + 2] = z;

            this.basePositions[i * 3] = x;
            this.basePositions[i * 3 + 1] = y;
            this.basePositions[i * 3 + 2] = z;

            // Random velocity
            this.velocities[i * 3] = (Math.random() - 0.5) * 0.005;
            this.velocities[i * 3 + 1] = (Math.random() - 0.5) * 0.003 + 0.002; // slight upward drift
            this.velocities[i * 3 + 2] = (Math.random() - 0.5) * 0.005;

            // Color: mix of gold and blue
            const color = Math.random() > 0.4 ? GOLD : BLUE;
            const dimFactor = 0.3 + Math.random() * 0.7;
            colors[i * 3] = color.r * dimFactor;
            colors[i * 3 + 1] = color.g * dimFactor;
            colors[i * 3 + 2] = color.b * dimFactor;

            // Size
            sizes[i] = 1 + Math.random() * 3;
        }

        this.geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
        this.geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));
        this.geometry.setAttribute('size', new THREE.BufferAttribute(sizes, 1));

        const material = new THREE.PointsMaterial({
            size: 0.04,
            vertexColors: true,
            transparent: true,
            opacity: 0.6,
            blending: THREE.AdditiveBlending,
            depthWrite: false,
            sizeAttenuation: true,
        });

        this.points = new THREE.Points(this.geometry, material);
        this.scene.add(this.points);
    }

    _createAuraRing() {
        // Vertical aura ring around avatar
        const ringGeo = new THREE.RingGeometry(0.8, 1.2, 64);
        const ringMat = new THREE.MeshBasicMaterial({
            color: GOLD,
            transparent: true,
            opacity: 0,
            side: THREE.DoubleSide,
            blending: THREE.AdditiveBlending,
        });
        this.auraRing = new THREE.Mesh(ringGeo, ringMat);
        this.auraRing.rotation.x = -Math.PI / 2;
        this.auraRing.position.y = -0.5;
        this.auraRing.scale.set(0.1, 0.1, 0.1);
        this.scene.add(this.auraRing);
    }

    _createSparkles() {
        // Sparkle particles for active states. Positions are offsets from her head: the Points
        // object itself is moved to the head when they burst (_triggerSparkles).
        const sparkleGeo = new THREE.BufferGeometry();
        const sparklePos = new Float32Array(SPARKLE_COUNT * 3);
        const sparkleScales = new Float32Array(SPARKLE_COUNT);

        for (let i = 0; i < SPARKLE_COUNT; i++) {
            sparklePos.set(sparkleOffset(), i * 3);
            sparkleScales[i] = 0;
        }

        sparkleGeo.setAttribute('position', new THREE.BufferAttribute(sparklePos, 3));
        // Read by the shader through scalePointsPerVertex: PointsMaterial alone ignores it.
        sparkleGeo.setAttribute('sparkleScale', new THREE.BufferAttribute(sparkleScales, 1));

        const sparkleMat = scalePointsPerVertex(new THREE.PointsMaterial({
            size: 0.06,
            color: GOLD,
            map: createSparkleTexture(),
            transparent: true,
            opacity: 0,
            blending: THREE.AdditiveBlending,
            depthWrite: false,
            sizeAttenuation: true,
        }), { nearest: SPARKLE_NEAREST });

        this.sparkles = new THREE.Points(sparkleGeo, sparkleMat);
        this.sparkles.visible = false;
        this.scene.add(this.sparkles);
    }

    /** Her head in the world now, or the old box's middle before an avatar has loaded. */
    _sparkleAnchor() {
        let anchor = null;
        try {
            anchor = this.getAnchor(this._anchor);
        } catch (error) {
            console.warn('particles.js: no anchor for the sparkles:', error);
        }
        return anchor ? this._anchor.copy(anchor) : this._anchor.copy(SPARKLE_DEFAULT_ANCHOR);
    }

    setMood(emotion) {
        this.mood = emotion;
        
        // Adjust behavior based on mood
        switch (emotion) {
            case 'feliz':
                this.moodIntensity = 1.5;
                this._setParticleColors(GOLD, 0.8);
                break;
            case 'triste':
                this.moodIntensity = 0.5;
                this._setParticleColors(BLUE, 0.4);
                break;
            case 'pensativa':
                this.moodIntensity = 0.8;
                this._setParticleColors(BLUE, 0.6);
                break;
            case 'sorprendida':
                this.moodIntensity = 2.0;
                this._setParticleColors(GOLD, 1.0);
                break;
            case 'enojada':
                this.moodIntensity = 1.8;
                this._setParticleColors(RED, 0.9);
                break;
            // The newer emotions (emotions.js), 2026-11-24.
            case 'avergonzada':
                this.moodIntensity = 0.8;
                this._setParticleColors(PINK, 0.5);
                break;
            case 'cariñosa':
                this.moodIntensity = 1.2;
                this._setParticleColors(PINK, 0.8);
                break;
            case 'emocionada':
                this.moodIntensity = 2.0;
                this._setParticleColors(GOLD, 1.0);
                this._triggerSparkles();
                break;
            case 'confundida':
                this.moodIntensity = 0.9;
                this._setParticleColors(BLUE, 0.6);
                break;
            case 'nerviosa':
                this.moodIntensity = 1.5;
                this._setParticleColors(BLUE, 0.75);
                break;
            case 'somnolienta':
                this.moodIntensity = 0.4;
                this._setParticleColors(BLUE, 0.3);
                break;
            case 'hablando':
                this.moodIntensity = 1.3;
                this._setParticleColors(GOLD, 0.9);
                this._triggerSparkles();
                break;
            default:
                this.moodIntensity = 1.0;
                this._setParticleColors(GOLD, 0.6);
        }
    }

    _setParticleColors(color, brightness) {
        if (!this.points) return;
        const colors = this.geometry.attributes.color.array;
        for (let i = 0; i < PARTICLE_COUNT; i++) {
            const dimFactor = brightness * (0.5 + Math.random() * 0.5);
            colors[i * 3] = color.r * dimFactor;
            colors[i * 3 + 1] = color.g * dimFactor;
            colors[i * 3 + 2] = color.b * dimFactor;
        }
        this.geometry.attributes.color.needsUpdate = true;
    }

    _triggerSparkles() {
        if (!this.sparkles) return;
        const geometry = this.sparkles.geometry;
        const positions = geometry.attributes.position.array;
        const scales = geometry.attributes.sparkleScale.array;
        for (let i = 0; i < SPARKLE_COUNT; i++) {
            positions.set(sparkleOffset(), i * 3);
            scales[i] = 0.5 + Math.random() * 1.5;
        }
        geometry.attributes.position.needsUpdate = true;
        geometry.attributes.sparkleScale.needsUpdate = true;
        geometry.computeBoundingSphere();
        this.sparkles.position.copy(this._sparkleAnchor());
        this.sparkles.visible = true;
        this.sparkles.material.opacity = 0.8;
        this._sparkleTimer = 0;
    }

    setSpeaking(speaking) {
        this.isSpeaking = speaking;
        if (speaking) {
            this._triggerSparkles();
        }
    }

    update(delta, elapsed) {
        if (!this.geometry) return;

        const positions = this.geometry.attributes.position.array;
        const speed = delta * this.moodIntensity;
        const shouldUpdateParticles = this.isSpeaking || this.moodIntensity > 1.2 || ((this._particleFrame++ & 1) === 0);

        if (shouldUpdateParticles) {
            const basePositions = this.basePositions;
            const velocities = this.velocities;
            const resetDistanceSq = 9;
            for (let i = 0; i < PARTICLE_COUNT; i++) {
                const i3 = i * 3;

                // Move particle
                positions[i3] += velocities[i3] * speed * 60;
                positions[i3 + 1] += velocities[i3 + 1] * speed * 60;
                positions[i3 + 2] += velocities[i3 + 2] * speed * 60;

                // Add floating wave motion
                positions[i3] += Math.sin(elapsed * 0.5 + i * 0.1) * 0.001 * this.moodIntensity;
                positions[i3 + 1] += Math.cos(elapsed * 0.3 + i * 0.05) * 0.001 * this.moodIntensity;

                // Respawn if too far from center
                const dx = positions[i3] - basePositions[i3];
                const dy = positions[i3 + 1] - basePositions[i3 + 1];
                const dz = positions[i3 + 2] - basePositions[i3 + 2];
                const distSq = dx * dx + dy * dy + dz * dz;

                if (distSq > resetDistanceSq) {
                    // Reset to base position with slight randomness
                    positions[i3] = basePositions[i3] + (Math.random() - 0.5) * 0.5;
                    positions[i3 + 1] = basePositions[i3 + 1] + (Math.random() - 0.5) * 0.5;
                    positions[i3 + 2] = basePositions[i3 + 2] + (Math.random() - 0.5) * 0.5;
                }
            }

            this.geometry.attributes.position.needsUpdate = true;
        }

        // Subtle rotation of entire particle system
        if (this.points) {
            this.points.rotation.y = elapsed * 0.02;
        }

        // Update aura ring
        this._updateAuraRing(elapsed);

        // Update sparkles
        this._updateSparkles(delta, elapsed);
    }

    _updateAuraRing(elapsed) {
        if (!this.auraRing) return;

        // Show aura when mood is active or speaking
        const showAura = this.moodIntensity > 1.2 || this.isSpeaking;
        const targetOpacity = showAura ? 0.15 + Math.sin(elapsed * 3) * 0.05 : 0;
        const targetScale = showAura ? 1 + Math.sin(elapsed * 2) * 0.1 : 0.1;

        this.auraRing.material.opacity = THREE.MathUtils.lerp(
            this.auraRing.material.opacity, targetOpacity, 0.1
        );
        this.auraRing.scale.x = THREE.MathUtils.lerp(this.auraRing.scale.x, targetScale, 0.1);
        this.auraRing.scale.z = THREE.MathUtils.lerp(this.auraRing.scale.z, targetScale, 0.1);

        // Rotate aura slowly
        this.auraRing.rotation.z = elapsed * 0.3;
    }

    _updateSparkles(delta, elapsed) {
        if (!this.sparkles) return;

        if (this._sparkleTimer !== undefined) {
            this._sparkleTimer += delta;
            if (this._sparkleTimer > 1.5) {
                // Fade out sparkles
                this.sparkles.material.opacity = Math.max(0, this.sparkles.material.opacity - delta * 2);
                const scales = this.sparkles.geometry.attributes.sparkleScale.array;
                for (let i = 0; i < SPARKLE_COUNT; i++) {
                    scales[i] = Math.max(0, scales[i] - delta * 2);
                }
                this.sparkles.geometry.attributes.sparkleScale.needsUpdate = true;
                if (this.sparkles.material.opacity <= 0.001) {
                    this.sparkles.material.opacity = 0;
                    this.sparkles.visible = false;
                    this._sparkleTimer = undefined;
                    return;
                }
            }
        }

        if (this.sparkles.material.opacity <= 0.001) return;

        // Lit, they go with her: a walk carries them along, the head's small sways are smoothed out.
        const follow = 1 - Math.exp(-SPARKLE_FOLLOW_RATE * Math.max(0, delta));
        this.sparkles.position.lerp(this._sparkleAnchor(), follow);

        // Floating sparkle motion
        const positions = this.sparkles.geometry.attributes.position.array;
        for (let i = 0; i < SPARKLE_COUNT; i++) {
            positions[i * 3 + 1] += Math.sin(elapsed * 3 + i) * 0.002;
        }
        this.sparkles.geometry.attributes.position.needsUpdate = true;
    }
}

