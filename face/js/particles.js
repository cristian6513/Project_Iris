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

export class ParticleSystem {
    constructor(scene) {
        this.scene = scene;
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
        // Sparkle particles for active states
        const sparkleGeo = new THREE.BufferGeometry();
        const sparklePos = new Float32Array(SPARKLE_COUNT * 3);
        const sparkleSizes = new Float32Array(SPARKLE_COUNT);

        for (let i = 0; i < SPARKLE_COUNT; i++) {
            sparklePos[i * 3] = (Math.random() - 0.5) * 2;
            sparklePos[i * 3 + 1] = Math.random() * 2 - 0.5;
            sparklePos[i * 3 + 2] = (Math.random() - 0.5) * 2;
            sparkleSizes[i] = 0;
        }

        sparkleGeo.setAttribute('position', new THREE.BufferAttribute(sparklePos, 3));
        sparkleGeo.setAttribute('size', new THREE.BufferAttribute(sparkleSizes, 1));

        const sparkleMat = new THREE.PointsMaterial({
            size: 0.06,
            color: GOLD,
            transparent: true,
            opacity: 0,
            blending: THREE.AdditiveBlending,
            depthWrite: false,
            sizeAttenuation: true,
        });

        this.sparkles = new THREE.Points(sparkleGeo, sparkleMat);
        this.scene.add(this.sparkles);
        this._sparkleBasePositions = new Float32Array(sparklePos);
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
        const positions = this.sparkles.geometry.attributes.position.array;
        const sizes = this.sparkles.geometry.attributes.size.array;
        for (let i = 0; i < SPARKLE_COUNT; i++) {
            positions[i * 3] = this._sparkleBasePositions[i * 3] + (Math.random() - 0.5) * 0.5;
            positions[i * 3 + 1] = Math.random() * 2;
            positions[i * 3 + 2] = this._sparkleBasePositions[i * 3 + 2] + (Math.random() - 0.5) * 0.5;
            sizes[i] = 0.5 + Math.random() * 1.5;
        }
        this.sparkles.geometry.attributes.position.needsUpdate = true;
        this.sparkles.geometry.attributes.size.needsUpdate = true;
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
                const sizes = this.sparkles.geometry.attributes.size.array;
                for (let i = 0; i < SPARKLE_COUNT; i++) {
                    sizes[i] = Math.max(0, sizes[i] - delta * 2);
                }
                this.sparkles.geometry.attributes.size.needsUpdate = true;
                if (this.sparkles.material.opacity <= 0.001) {
                    this.sparkles.material.opacity = 0;
                    this._sparkleTimer = undefined;
                    return;
                }
            }
        }

        if (this.sparkles.material.opacity <= 0.001) return;

        // Floating sparkle motion
        const positions = this.sparkles.geometry.attributes.position.array;
        for (let i = 0; i < SPARKLE_COUNT; i++) {
            positions[i * 3 + 1] += Math.sin(elapsed * 3 + i) * 0.002;
        }
        this.sparkles.geometry.attributes.position.needsUpdate = true;
    }
}

