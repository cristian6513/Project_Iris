/**
 * She leaves in a line of light. It wipes her from head to feet, whatever her position, sparks
 * fall from it and a ring opens on the floor; where she lands she appears the same way. The wipe
 * is one clipping plane on her materials, which needs its own shaders: `prepare` links them while
 * the page loads, so that they don't stall the frame she starts to vanish on.
 */
import * as THREE from 'three';

export const TELEPORT = Object.freeze({ leave: 0.5, gone: 0.14, arrive: 0.56 });

const SPARKS = 120;
const SPARKS_PER_SECOND = 170;
const LINE_RADIUS = 0.62;     // room units: past her elbows and through a seated knee
const LINE_GLOW = 1.5;
const FLOOR_RING_SECONDS = 0.62;
const FLOOR_RING_REACH = [0.45, 1.5];
const OVER_HEAD = 0.45;       // of hips to head: her hair stands above that bone
const PAST_FEET = 0.22;

const ease = (t) => t * t * (3 - 2 * t);

/** Where in the jump she is, and how far the line has gone in that part. */
export function teleportStage(elapsed) {
    const { leave, gone, arrive } = TELEPORT;
    if (elapsed < leave) return { stage: 'leave', wiped: ease(elapsed / leave) };
    if (elapsed < leave + gone) return { stage: 'gone', wiped: 1 };
    if (elapsed < leave + gone + arrive) return { stage: 'arrive', wiped: ease((elapsed - leave - gone) / arrive) };
    return { stage: 'done', wiped: 1 };
}

/** From over her head to past her feet, along her body: sitting tilts it, lying lays it down. */
export function bodySpan(vrm) {
    const at = (name) => vrm.humanoid.getRawBoneNode(name)?.getWorldPosition(new THREE.Vector3());
    const head = at('head'), hips = at('hips'), left = at('leftFoot'), right = at('rightFoot');
    if (!head || !hips || !left || !right) return null;
    const feet = left.clone().add(right).multiplyScalar(0.5);
    const along = feet.clone().sub(head).normalize();
    const margin = head.distanceTo(hips);
    return {
        along,
        from: head.clone().addScaledVector(along, -margin * OVER_HEAD),
        to: feet.addScaledVector(along, margin * PAST_FEET),
    };
}

/** Her own materials. What she holds has its own way of leaving: see `heldProp` in scene_actions. */
function wipedMaterials(root, found = new Set()) {
    if (root.userData.conjured) return found;
    if (root.isMesh) for (const material of [root.material].flat()) if (material) found.add(material);
    for (const child of root.children) wipedMaterials(child, found);
    return found;
}

const LIGHT_VERTEX = /* glsl */`
    varying vec2 vUv;
    void main() {
        vUv = uv;
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
    }`;
const RING_FRAGMENT = /* glsl */`
    uniform vec3 color;
    uniform float power;
    varying vec2 vUv;
    void main() {
        float r = length(vUv - 0.5) * 2.0;
        float rim = exp(-pow((r - 0.8) / 0.045, 2.0));
        float haze = exp(-pow(r / 0.7, 2.0)) * 0.26;
        float inside = smoothstep(1.0, 0.86, r);
        gl_FragColor = vec4(mix(color, vec3(1.0), rim * 0.55) * (rim + haze) * inside * power, 1.0);
    }`;

function lightMaterial(fragmentShader, color) {
    return new THREE.ShaderMaterial({
        uniforms: { color: { value: color }, power: { value: 0 } },
        vertexShader: LIGHT_VERTEX,
        fragmentShader,
        blending: THREE.AdditiveBlending,
        transparent: true,
        depthWrite: false,
        side: THREE.DoubleSide,
    });
}

class Sparks {
    constructor(color) {
        this.position = new Float32Array(SPARKS * 3);
        this.fade = new Float32Array(SPARKS);
        this.velocity = new Float32Array(SPARKS * 3);
        this.life = new Float32Array(SPARKS);
        this.lived = new Float32Array(SPARKS);
        this.next = 0;
        this.owed = 0;
        const geometry = new THREE.BufferGeometry();
        geometry.setAttribute('position', new THREE.BufferAttribute(this.position, 3));
        geometry.setAttribute('fade', new THREE.BufferAttribute(this.fade, 1));
        this.points = new THREE.Points(geometry, new THREE.ShaderMaterial({
            uniforms: { color: { value: color } },
            vertexShader: /* glsl */`
                attribute float fade;
                varying float vFade;
                void main() {
                    vFade = fade;
                    vec4 view = modelViewMatrix * vec4(position, 1.0);
                    gl_PointSize = clamp((24.0 + 26.0 * fade) / -view.z, 2.0, 9.0);
                    gl_Position = projectionMatrix * view;
                }`,
            fragmentShader: /* glsl */`
                uniform vec3 color;
                varying float vFade;
                void main() {
                    float dot = smoothstep(0.5, 0.0, length(gl_PointCoord - 0.5));
                    gl_FragColor = vec4(mix(color, vec3(1.0), 0.25) * dot * dot * vFade * 1.6, 1.0);
                }`,
            blending: THREE.AdditiveBlending,
            transparent: true,
            depthWrite: false,
        }));
        this.points.frustumCulled = false;
    }

    /** From the line, thrown outwards and on along her body. */
    emit(centre, along, delta) {
        this.owed += delta * SPARKS_PER_SECOND;
        const side = new THREE.Vector3(), other = new THREE.Vector3();
        side.set(along.y, along.z, along.x).cross(along).normalize();
        other.copy(along).cross(side);
        for (; this.owed >= 1; this.owed -= 1) {
            const i = this.next;
            this.next = (i + 1) % SPARKS;
            const angle = Math.random() * Math.PI * 2;
            const reach = LINE_RADIUS * (0.55 + Math.random() * 0.45);
            const cos = Math.cos(angle), sin = Math.sin(angle);
            for (let axis = 0; axis < 3; axis++) {
                const k = ['x', 'y', 'z'][axis];
                const out = side[k] * cos + other[k] * sin;
                this.position[i * 3 + axis] = centre[k] + out * reach;
                this.velocity[i * 3 + axis] = out * (0.25 + Math.random() * 0.5) + along[k] * (0.35 + Math.random() * 0.9);
            }
            this.life[i] = 0.35 + Math.random() * 0.45;
            this.lived[i] = 0;
        }
    }

    update(delta) {
        for (let i = 0; i < SPARKS; i++) {
            if (this.life[i] <= 0) continue;
            this.lived[i] += delta;
            const left = 1 - this.lived[i] / this.life[i];
            if (left <= 0) {
                this.life[i] = 0;
                this.fade[i] = 0;
                continue;
            }
            this.fade[i] = left;
            this.velocity[i * 3 + 1] -= 0.9 * delta;
            for (let axis = 0; axis < 3; axis++) this.position[i * 3 + axis] += this.velocity[i * 3 + axis] * delta;
        }
        this.points.geometry.attributes.position.needsUpdate = true;
        this.points.geometry.attributes.fade.needsUpdate = true;
    }

    get alive() {
        return this.life.some((life) => life > 0);
    }

    clear() {
        this.life.fill(0);
        this.fade.fill(0);
        this.owed = 0;
    }
}

export class TeleportEffect {
    constructor({ scene, floorY = 0 }) {
        this.floorY = floorY;
        this.color = new THREE.Color(0x7fe9ff);
        this.plane = new THREE.Plane();
        this.planes = [this.plane];
        this.job = null;
        this.floorRingAge = Infinity;

        const disc = new THREE.PlaneGeometry(2, 2);
        this.line = new THREE.Mesh(disc, lightMaterial(RING_FRAGMENT, this.color));
        this.floorRing = new THREE.Mesh(disc, lightMaterial(RING_FRAGMENT, this.color));
        this.line.frustumCulled = this.floorRing.frustumCulled = false;
        this.floorRing.rotation.x = -Math.PI / 2;
        this.sparks = new Sparks(this.color);
        this.group = new THREE.Group();
        this.group.name = 'teleport_effect';
        this.group.add(this.line, this.floorRing, this.sparks.points);
        this.group.visible = false;
        scene.add(this.group);
    }

    get active() {
        return Boolean(this.job);
    }

    /** `draw` renders one frame with the wipe on and nothing cut, which links every shader here. */
    prepare(root, draw) {
        const materials = this._hold(root);
        this.plane.set(new THREE.Vector3(0, 1, 0), 1e4);
        this.group.visible = true;
        draw();
        this.group.visible = false;
        this._release(materials);
    }

    /**
     * @param {object} body `vrm` and `root`, the object that moves
     * @param {Function} land takes her to her new place, out of sight
     * @param {Function} settle after the landing pose took over, with her still out of sight
     */
    start({ vrm, root, color, land, settle }) {
        const span = bodySpan(vrm);
        if (this.job || !span) return false;
        if (color) this.color.copy(color);
        this.job = { vrm, root, land, settle, span, elapsed: 0, stage: 'leave', materials: this._hold(root) };
        this.group.visible = this.line.visible = true;
        this._wipe(0);
        this._openFloorRing(span.to);
        return true;
    }

    update(delta) {
        const job = this.job;
        if (job) {
            job.elapsed += delta;
            const { stage, wiped } = teleportStage(job.elapsed);
            if (stage === 'done') this._finish();
            else {
                if (stage !== job.stage) this._enter(stage);
                if (stage !== 'gone') {
                    this._wipe(wiped);
                    this.sparks.emit(this.line.position, job.span.along, delta);
                }
            }
        }
        this._updateFloorRing(delta);
        this.sparks.update(delta);
        if (!this.job) this.group.visible = this.sparks.alive || this.floorRingAge < FLOOR_RING_SECONDS;
    }

    /**
     * Something has to take her body at once. Before she lands she is back in her place and
     * nothing moved; after it she is just all there. What the light threw out dies by itself.
     */
    cancel() {
        const job = this.job;
        if (!job) return;
        this.job = null;
        job.root.visible = true;
        this._release(job.materials);
        this.line.visible = false;
        return { landed: job.stage !== 'leave' };
    }

    _enter(stage) {
        const job = this.job;
        if (job.stage === 'leave') {
            this._release(job.materials);
            job.materials = [];
            job.root.visible = false;
            this.line.visible = false;
            job.land();
        }
        if (stage === 'arrive') {
            job.settle?.();
            job.span = bodySpan(job.vrm) ?? job.span;
            job.materials = this._hold(job.root);
            job.root.visible = true;
            this.line.visible = true;
            this._openFloorRing(job.span.to);
        }
        job.stage = stage;
    }

    _finish() {
        const job = this.job;
        this.job = null;
        job.root.visible = true;
        this._release(job.materials);
        this.line.visible = false;
    }

    _hold(root) {
        const materials = [...wipedMaterials(root)];
        for (const material of materials) {
            if (material.isShaderMaterial) material.clipping = true;
            material.clippingPlanes = this.planes;
        }
        return materials;
    }

    _release(materials) {
        for (const material of materials) material.clippingPlanes = null;
    }

    _wipe(wiped) {
        const { span, stage } = this.job;
        const at = this.line.position.lerpVectors(span.from, span.to, wiped);
        // Leaving, what is past the line toward her feet stays. Arriving, what is toward her head.
        this.plane.setFromNormalAndCoplanarPoint(stage === 'arrive' ? span.along.clone().negate() : span.along, at);
        this.line.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, 1), span.along);
        this.line.scale.setScalar(LINE_RADIUS);
        this.line.material.uniforms.power.value = LINE_GLOW * Math.min(1, Math.min(wiped, 1 - wiped) * 8 + 0.15);
    }

    _openFloorRing(over) {
        this.floorRing.position.set(over.x, this.floorY + 0.015, over.z);
        this.floorRingAge = 0;
    }

    _updateFloorRing(delta) {
        this.floorRingAge += delta;
        const age = Math.min(this.floorRingAge / FLOOR_RING_SECONDS, 1);
        const [near, far] = FLOOR_RING_REACH;
        this.floorRing.visible = age < 1;
        this.floorRing.scale.setScalar(near + (far - near) * ease(age));
        this.floorRing.material.uniforms.power.value = (1 - age) * (1 - age) * 1.2;
    }
}
