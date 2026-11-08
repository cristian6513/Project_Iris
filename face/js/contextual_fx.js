/**
 * IRIS Contextual FX — Visual Effects for Scene Actions
 *
 * FloatingBook   → book that materialises, opens, emits golden pages
 * ThinkingCloud  → orbiting particles above avatar head
 * DataStream     → vertical flowing data lines (Matrix-style)
 * MemorySymbol   → database/cloud icon with pulse
 */

import * as THREE from 'three';

const GOLD = new THREE.Color(0xD4A017);
const BLUE = new THREE.Color(0xA8D8EA);

// ════════════════════════════════════════════════════════════
// FloatingBook
// ════════════════════════════════════════════════════════════

export class FloatingBook {
    constructor(scene) {
        this.scene = scene;
        this.group = new THREE.Group();
        this.group.name = 'floating_book';
        this.group.visible = false;
        this.active = false;
        this.phase = 0; // 0=hidden, 1=rising, 2=open, 3=closing
        this._buildBook();
        this.scene.add(this.group);
    }

    _buildBook() {
        // Book cover
        const coverMat = new THREE.MeshStandardMaterial({
            color: 0x8b2c2c, roughness: 0.5, metalness: 0.1,
        });
        const cover = new THREE.Mesh(new THREE.BoxGeometry(0.4, 0.5, 0.05), coverMat);
        this.group.add(cover);
        this.cover = cover;

        // Pages (inner, lighter)
        const pageMat = new THREE.MeshStandardMaterial({
            color: 0xf5e6c8,
            emissive: 0xD4A017,
            emissiveIntensity: 0.2,
            roughness: 0.8,
        });
        const pages = new THREE.Mesh(new THREE.BoxGeometry(0.35, 0.45, 0.04), pageMat);
        pages.position.z = 0.005;
        this.group.add(pages);
        this.pages = pages;

        // Glow particles around book
        const particleGeo = new THREE.BufferGeometry();
        const pCount = 50;
        const pPos = new Float32Array(pCount * 3);
        for (let i = 0; i < pCount; i++) {
            pPos[i * 3]     = (Math.random() - 0.5) * 0.8;
            pPos[i * 3 + 1] = (Math.random() - 0.5) * 0.8;
            pPos[i * 3 + 2] = (Math.random() - 0.5) * 0.3;
        }
        particleGeo.setAttribute('position', new THREE.BufferAttribute(pPos, 3));
        const pMat = new THREE.PointsMaterial({
            color: GOLD, size: 0.015, transparent: true, opacity: 0.6,
            blending: THREE.AdditiveBlending, depthWrite: false,
        });
        this.particles = new THREE.Points(particleGeo, pMat);
        this.group.add(this.particles);
    }

    show(position = new THREE.Vector3(0, 1.2, 0)) {
        this.group.position.copy(position);
        this.group.position.y -= 0.5;
        this.group.visible = true;
        this.active = true;
        this.phase = 1;
        this.group.scale.setScalar(0.01);
    }

    hide() {
        this.phase = 3;
    }

    update(delta, elapsed) {
        if (!this.active) return;

        if (this.phase === 1) {
            // Rising
            this.group.position.y += delta * 0.8;
            this.group.scale.lerp(new THREE.Vector3(1, 1, 1), delta * 3);
            if (this.group.scale.x > 0.95) this.phase = 2;
        } else if (this.phase === 2) {
            // Open — float and glow
            this.group.rotation.y = Math.sin(elapsed * 0.5) * 0.1;
            this.group.position.y += Math.sin(elapsed * 1.5) * 0.001;
            this.pages.material.emissiveIntensity = 0.2 + Math.sin(elapsed * 3) * 0.1;
            this.particles.rotation.y = elapsed * 0.3;
        } else if (this.phase === 3) {
            // Closing
            this.group.scale.multiplyScalar(1 - delta * 3);
            if (this.group.scale.x < 0.05) {
                this.group.visible = false;
                this.active = false;
                this.phase = 0;
            }
        }
    }
}

// ════════════════════════════════════════════════════════════
// ThinkingCloud — Orbiting particles above head
// ════════════════════════════════════════════════════════════

export class ThinkingCloud {
    constructor(scene) {
        this.scene = scene;
        this.group = new THREE.Group();
        this.group.name = 'thinking_cloud';
        this.group.visible = false;
        this.active = false;
        this._build();
        this.scene.add(this.group);
    }

    _build() {
        // Orbiting dots
        const count = 24;
        const geo = new THREE.BufferGeometry();
        const pos = new Float32Array(count * 3);
        const colors = new Float32Array(count * 3);

        for (let i = 0; i < count; i++) {
            const angle = (i / count) * Math.PI * 2;
            const r = 0.3 + Math.random() * 0.1;
            pos[i * 3]     = Math.cos(angle) * r;
            pos[i * 3 + 1] = (Math.random() - 0.5) * 0.15;
            pos[i * 3 + 2] = Math.sin(angle) * r;

            const c = Math.random() > 0.5 ? GOLD : BLUE;
            colors[i * 3]     = c.r;
            colors[i * 3 + 1] = c.g;
            colors[i * 3 + 2] = c.b;
        }

        geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
        geo.setAttribute('color', new THREE.BufferAttribute(colors, 3));

        const mat = new THREE.PointsMaterial({
            size: 0.03, vertexColors: true, transparent: true, opacity: 0.8,
            blending: THREE.AdditiveBlending, depthWrite: false,
        });

        this.points = new THREE.Points(geo, mat);
        this.group.add(this.points);

        // Center glow sphere
        const glowMat = new THREE.MeshBasicMaterial({
            color: GOLD, transparent: true, opacity: 0.15,
        });
        const glow = new THREE.Mesh(new THREE.SphereGeometry(0.12, 16, 16), glowMat);
        this.group.add(glow);
        this.glow = glow;
    }

    show(position = new THREE.Vector3(0, 2.0, 0)) {
        this.group.position.copy(position);
        this.group.visible = true;
        this.active = true;
        this.group.scale.setScalar(0.01);
    }

    hide() {
        this.active = false;
        this.group.visible = false;
    }

    update(delta, elapsed) {
        if (!this.active) return;

        this.group.scale.lerp(new THREE.Vector3(1, 1, 1), delta * 3);
        this.points.rotation.y = elapsed * 1.5;
        this.points.rotation.x = Math.sin(elapsed * 0.5) * 0.1;
        this.glow.material.opacity = 0.1 + Math.sin(elapsed * 4) * 0.05;
    }
}

// ════════════════════════════════════════════════════════════
// DataStream — Vertical flowing data lines
// ════════════════════════════════════════════════════════════

export class DataStream {
    constructor(scene) {
        this.scene = scene;
        this.group = new THREE.Group();
        this.group.name = 'data_stream';
        this.group.visible = false;
        this.active = false;
        this.lines = [];
        this._build();
        this.scene.add(this.group);
    }

    _build() {
        const lineCount = 12;
        for (let i = 0; i < lineCount; i++) {
            const points = [];
            const x = (Math.random() - 0.5) * 0.8;
            const z = (Math.random() - 0.5) * 0.3;
            const segments = 8;

            for (let s = 0; s <= segments; s++) {
                points.push(new THREE.Vector3(
                    x + (Math.random() - 0.5) * 0.02,
                    s * 0.08,
                    z,
                ));
            }

            const geo = new THREE.BufferGeometry().setFromPoints(points);
            const lineMat = new THREE.LineBasicMaterial({
                color: Math.random() > 0.5 ? GOLD : BLUE,
                transparent: true,
                opacity: 0.4 + Math.random() * 0.3,
            });
            const line = new THREE.Line(geo, lineMat);
            line.userData.speed = 0.5 + Math.random() * 1.0;
            line.userData.offset = Math.random() * 10;
            this.lines.push(line);
            this.group.add(line);
        }
    }

    show(position = new THREE.Vector3(0, 0.5, 0)) {
        this.group.position.copy(position);
        this.group.visible = true;
        this.active = true;
    }

    hide() {
        this.active = false;
        this.group.visible = false;
    }

    update(delta, elapsed) {
        if (!this.active) return;

        for (const line of this.lines) {
            const pos = line.geometry.attributes.position;
            const speed = line.userData.speed;
            const off = line.userData.offset;

            for (let i = 0; i < pos.count; i++) {
                let y = pos.getY(i);
                y += delta * speed;
                if (y > 0.8) y -= 0.8;
                pos.setY(i, y);

                // Subtle horizontal wave
                const x = pos.getX(i);
                pos.setX(i, x + Math.sin(elapsed * 3 + off + i) * 0.0003);
            }
            pos.needsUpdate = true;

            // Pulse opacity
            line.material.opacity = 0.3 + Math.sin(elapsed * 2 + off) * 0.15;
        }
    }
}

// ════════════════════════════════════════════════════════════
// MemorySymbol — Database icon with pulse
// ════════════════════════════════════════════════════════════

export class MemorySymbol {
    constructor(scene) {
        this.scene = scene;
        this.group = new THREE.Group();
        this.group.name = 'memory_symbol';
        this.group.visible = false;
        this.active = false;
        this._build();
        this.scene.add(this.group);
    }

    _build() {
        const mat = new THREE.MeshStandardMaterial({
            color: BLUE, emissive: BLUE, emissiveIntensity: 0.5,
            transparent: true, opacity: 0.7,
            metalness: 0.5, roughness: 0.3,
        });

        // Stack of cylinders = database icon
        for (let i = 0; i < 3; i++) {
            const disk = new THREE.Mesh(new THREE.CylinderGeometry(0.12, 0.12, 0.04, 16), mat.clone());
            disk.position.y = i * 0.06;
            this.group.add(disk);
        }

        // Glow ring
        const ringMat = new THREE.MeshBasicMaterial({
            color: BLUE, transparent: true, opacity: 0.2,
            side: THREE.DoubleSide,
        });
        const ring = new THREE.Mesh(new THREE.RingGeometry(0.15, 0.25, 32), ringMat);
        ring.rotation.x = -Math.PI / 2;
        ring.position.y = -0.02;
        this.group.add(ring);
        this.ring = ring;
    }

    show(position = new THREE.Vector3(0, 1.5, 0)) {
        this.group.position.copy(position);
        this.group.visible = true;
        this.active = true;
        this.group.scale.setScalar(0.01);
    }

    hide() {
        this.active = false;
        this.group.visible = false;
    }

    update(delta, elapsed) {
        if (!this.active) return;

        this.group.scale.lerp(new THREE.Vector3(1, 1, 1), delta * 4);
        this.group.rotation.y = elapsed * 0.8;
        this.group.position.y += Math.sin(elapsed * 2) * 0.001;

        if (this.ring) {
            this.ring.material.opacity = 0.15 + Math.sin(elapsed * 3) * 0.08;
        }
    }
}

