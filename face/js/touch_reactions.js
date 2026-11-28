/**
 * Touch reactions (2026-11-27): petting her head, poking and pulling her cheeks, on any VRM or
 * PMX (MMD) avatar. Shared by the room and the Desktop Companion; Iris's own file is never
 * changed, she is one VRM among others.
 *
 * - Contact: the pointer's ray is tested against the triangles that move with her head, in the
 *   space they were modelled in (one matrix per mesh instead of skinning every vertex).
 * - Skin: a vertex-shader patch (onBeforeCompile) moves `transformed` before skinning: up to four
 *   gaussian dents with the volume pushed aside, a one- or two-handed pull, and a puff. Each
 *   vertex has a weight, measured when the avatar arrives: cheeks move most, the face a little,
 *   the eyes, the lips, the hair and the back of the head not at all.
 * - Spring: every dent and pull is an exactly integrated damped spring (ω 22, decay 4.6), so the
 *   cheek bounces back when let go.
 * - Face: MMD models use their own morphs (pmx_expressions.js roles: cheekPuff, jitome, squeeze,
 *   markAnger, winkLeft/Right, blush, relaxed…); what a model lacks falls back on the shader
 *   (puff, blush tint). VRM uses its expressions (happy, relaxed, angry, blink…) and the shader.
 * - Mood: more than four pokes within three seconds annoy her (pout, puffed cheeks, angry, she
 *   turns away); petting makes her content (eyes closed in a smile, blush, her head leans into
 *   the hand) and, after it, affectionate. Both fade. A poke or a pull wakes her from that mood:
 *   the touch-owned «cariñosa» ends and, while the finger is on her, any smile that shuts her eyes
 *   gives way (Carlotta's «happy» does, 2026-11-27).
 * - Hair: a lock over a cheek, below her eyes, counts as that cheek; the dent goes on the skin
 *   under it (Lily, Mornye).
 * - Sound and reach: `sounds` (touch_sounds.js) gets the pop and the release (petting is silent);
 *   `tolerance` widens the target around her head (the Companion's small window), and
 *   `onDoubleTouch` reports a double click on her head.
 * - No fights: everything is written just before vrm.update and given back right after (the
 *   same rule as ArmReach.restore and _restoreHeadClip), so blinks, gaze, visemes and clips keep
 *   their own values; expressions are only raised (max) or, while her eyes are closed or she is
 *   annoyed, lowered for that frame.
 */
import * as THREE from 'three';

export const TOUCH_SPRING = Object.freeze({ omega: 22, decay: 4.6 });
// The finger drives the press, so the skin follows it without overshooting (critical damping); it
// is the release that bounces. With the bouncy spring both ways a held poke first sank 1.5 times
// its depth, and that was when it looked like a crease (IRIS, 2026-11-27).
export const PRESS_SPRING = Object.freeze({ omega: 24, decay: 24 });
export const ANNOY_POKES = 4;      // more pokes than this…
export const ANNOY_WINDOW = 3;     // …within this many seconds annoy her
const ANNOY_HOLD = 2.4;            // seconds she stays fully annoyed after the last poke
const CALM_SECONDS = 4.5;          // then she calms down over this long
const AFFECTION_RISE = 0.5;        // per second of petting (more with a moving hand)
const AFFECTION_HOLD = 2.5;
const AFFECTION_FADE = 8;
export const STARTLE_AFFECTION = 0.3; // a poke or a pull leaves at most this much of the petting mood
export const MAX_POKES = 4;
const MAX_PULLS = 2;

// Sizes in eye distances (E): the gap between her eye centres, measured per model.
// The dent was made wider, shallower and softer lit on 2026-11-27: up close it read as a thin dark
// crease on IRIS (radius 0.34, depth 0.28, full normal tilt, rim 0.22, and a press that overshot).
export const TOUCH_SHAPE = Object.freeze({
    pokeRadius: 0.42,      // gaussian radius of a dent
    pokeDepth: 0.24,       // how deep a held finger presses
    pullRadius: 0.5,       // how wide a pinch takes the skin
    pullMax: 0.32,         // the farthest a cheek stretches
    pullLift: 0.15,        // a pinched cheek also comes forward this share of the pull
    puff: 0.18,            // full cheek puff, along the skin's normal
    // How the dent is lit: the share of its slope the normals follow (all of it makes a toon
    // shadow line), the skin pushed aside and the lifted rim (in units of the depth), and a soft
    // round shadow under the finger, so it reads as a dimple.
    tilt: 0.6,
    aside: 0.3,
    rim: 0.14,
    shade: 0.45,
});

const clamp = (v, min = 0, max = 1) => Math.min(max, Math.max(min, Number.isFinite(v) ? v : min));
const smoothstep = (a, b, v) => { const t = clamp((v - a) / (b - a)); return t * t * (3 - 2 * t); };

/**
 * One step of a damped spring pulled to `target`: x'' = -ω²(x - target) - 2·decay·x', integrated
 * exactly (no step-size error, any dt). Returns [x, v].
 */
export function springStep(x, v, target, dt, omega = TOUCH_SPRING.omega, decay = TOUCH_SPRING.decay) {
    if (!(dt > 0)) return [x, v];
    const y0 = x - target;
    const e = Math.exp(-decay * dt);
    const w2 = omega * omega - decay * decay;
    if (w2 > 1e-9) {
        const w = Math.sqrt(w2), c = Math.cos(w * dt), s = Math.sin(w * dt);
        const b = (v + decay * y0) / w;
        return [target + e * (y0 * c + b * s), e * ((b * w - decay * y0) * c - (y0 * w + decay * b) * s)];
    }
    if (w2 < -1e-9) {
        const q = Math.sqrt(-w2), r1 = -decay + q, r2 = -decay - q;
        const c1 = (v - r2 * y0) / (r1 - r2), c2 = y0 - c1;
        const e1 = Math.exp(r1 * dt), e2 = Math.exp(r2 * dt);
        return [target + c1 * e1 + c2 * e2, r1 * c1 * e1 + r2 * c2 * e2];
    }
    const b = v + decay * y0;
    return [target + (y0 + b * dt) * e, (b - decay * (y0 + b * dt)) * e];
}

/** Her mood from touch: pokes annoy her, petting makes her content and then affectionate. */
export class TouchMood {
    constructor() {
        this.time = 0;
        this.pokes = [];
        this.annoyed = 0;
        this.hold = 0;
        this.affection = 0;
        this.affectionHold = 0;
    }

    /** A poke. True when this one annoys her (it starts the pout). */
    poke() {
        this.pokes.push(this.time);
        this.pokes = this.pokes.filter(t => this.time - t <= ANNOY_WINDOW);
        this.affection *= 0.8;
        if (this.pokes.length > ANNOY_POKES) {
            const starts = this.annoyed < 0.5;
            this.annoyed = 1;
            this.hold = ANNOY_HOLD;
            return starts;
        }
        // Poking her while she is still cross keeps her cross.
        if (this.annoyed > 0) {
            this.annoyed = Math.min(1, this.annoyed + 0.3);
            this.hold = Math.max(this.hold, ANNOY_HOLD * 0.6);
        }
        return false;
    }

    /**
     * A finger on her cheek (a poke or a pull) wakes her from the petting mood: most of the
     * affection goes at once, without the hold, so its smile does not keep her eyes shut.
     */
    startle() {
        this.affection = Math.min(this.affection, STARTLE_AFFECTION);
        this.affectionHold = 0;
    }

    /** Petted for `dt` seconds; `stroke` 0..1 is how much the hand moves. Petting calms her too. */
    pet(dt, stroke = 0) {
        this.affection = Math.min(1, this.affection + dt * AFFECTION_RISE * (0.4 + 0.6 * clamp(stroke)));
        this.affectionHold = AFFECTION_HOLD;
        if (this.annoyed > 0) {
            this.hold = Math.max(0, this.hold - dt * 2);
            if (this.hold === 0) this.annoyed = Math.max(0, this.annoyed - dt * 2 / CALM_SECONDS);
        }
    }

    update(dt) {
        if (!(dt > 0)) return;
        this.time += dt;
        if (this.hold > 0) this.hold = Math.max(0, this.hold - dt);
        else this.annoyed = Math.max(0, this.annoyed - dt / CALM_SECONDS);
        if (this.affectionHold > 0) this.affectionHold = Math.max(0, this.affectionHold - dt);
        else this.affection = Math.max(0, this.affection - dt / AFFECTION_FADE);
    }

    get calm() { return this.annoyed === 0 && this.affection === 0; }
}

// Channels that need open eyes, or fight a content face.
const OPEN_EYED = ['heartEyes', 'starEyes', 'surprised', 'smallPupils'];
const LIDS = ['blink', 'blinkLeft', 'blinkRight'];
// An expression that shuts more than this share of the eye (against the blink) closes her eyes.
const EYE_SHUTTING = 0.4;

/**
 * What touch adds to her face this frame. `levels`: closed (eyes shut by petting), alert (a finger
 * on her cheek: a poke or a pull), affection, annoyed, flinchL/flinchR (the poked side's eye),
 * puff (a poke's puff), pull, speaking (0..1). `has(name)` says whether the avatar has an
 * expression; `closure(name)` how much of the eye it shuts at full weight (0..1). Returns { set:
 * raise to at least, scale: lower by, shader: { puff, blush } } where the shader gets what no
 * expression could show.
 */
export function touchFace(levels, has, closure = () => 0) {
    const set = {}, scale = {}, shader = { puff: 0, blush: 0 };
    const raise = (name, w) => {
        if (!has(name)) return false;
        if (w > 0.001) set[name] = Math.min(1, Math.max(set[name] ?? 0, w));
        return true;
    };
    const lower = (name, f) => { if (has(name)) scale[name] = Math.min(scale[name] ?? 1, clamp(f)); };
    const { closed: petted = 0, affection = 0, annoyed = 0, flinchL = 0, flinchR = 0, puff = 0, pull = 0, speaking = 0 } = levels;
    const alert = clamp(levels.alert ?? 0);
    // A finger on her cheek wakes her: the petting's closed eyes give way to it.
    const closed = petted * (1 - alert);
    const shuts = name => closure(name) > EYE_SHUTTING;

    // Petted: eyes shut in a smile. The expression that shuts the eyes best does it; if none does,
    // the lids close and the expression only shapes the smile.
    if (closed > 0.001) {
        const closer = ['relaxed', 'happy'].filter(has).sort((a, b) => closure(b) - closure(a))[0];
        const shut = closer ? closure(closer) : 0;
        if (closer && shut > 0.5) {
            raise(closer, closed * Math.min(1, 0.95 / shut));
            for (const name of LIDS) lower(name, 1 - closed);
        } else {
            if (closer) raise(closer, closed * 0.7);
            if (!raise('blink', closed * 0.85)) { raise('blinkLeft', closed * 0.85); raise('blinkRight', closed * 0.85); }
        }
        for (const name of [...OPEN_EYED, 'sad', 'angry']) lower(name, 1 - closed);
    }
    // Content, then affectionate: a smile that lingers.
    raise('happy', Math.max(closed * 0.3, affection * 0.45) * (1 - annoyed) * (shuts('happy') ? 1 - alert : 1));
    const blush = Math.max(closed * 0.9, affection * 0.75, annoyed * 0.35);
    if (!raise('blush', blush)) shader.blush = blush * 0.85;

    // Annoyed: angry brows and mouth, a pout, then a half-lidded glare while she calms down.
    if (annoyed > 0.001) {
        raise('angry', annoyed * annoyed * 0.85);
        const glare = 4 * annoyed * (1 - annoyed);
        if (!raise('jitome', glare * 0.75)) raise('blink', glare * 0.22);
        for (const name of ['happy', 'relaxed', ...OPEN_EYED]) lower(name, 1 - annoyed);
    }
    // Woken by a finger: a smile that shuts her eyes (Carlotta's «happy», MMD's 笑い) gives way while
    // it lasts, whoever set it (the emotion system's «cariñosa» or «feliz» too), so her look and
    // the poked side's flinch show. Only for those frames; the emotion keeps its own value.
    if (alert > 0.001) for (const name of ['happy', 'relaxed']) if (shuts(name)) lower(name, 1 - 0.9 * alert);
    // The pout: MMD's puff morph shapes the mouth, and the shader adds the volume most of those
    // morphs barely give the cheeks (Lily's (○｀ε´○) + もぐもぐ, 2026-11-27); without a morph the
    // shader does it all.
    const pout = Math.max(annoyed * 0.95, puff * 0.6);
    shader.puff = raise('cheekPuff', pout) ? Math.max(annoyed * 0.45, puff * 0.15) : Math.max(annoyed * 0.8, puff * 0.25);
    // A pulled cheek hurts a little: worried brows.
    raise('sad', pull * 0.35);
    // The poked side's eye flinches: MMD's one-eyed wink morphs, or a VRM's separate lids.
    if (!raise('winkLeft', flinchL * 0.6)) raise('blinkLeft', flinchL * 0.55);
    if (!raise('winkRight', flinchR * 0.6)) raise('blinkRight', flinchR * 0.55);
    // While she speaks the vowels lead: MMD's puff morph gives way on its own (pmx_avatar.js).
    shader.puff *= 1 - 0.7 * clamp(speaking);
    return { set, scale, shader };
}

// ── Geometry: where her face is ─────────────────────────────────────────────

const PHYSICS_FREE_EYE = /(^|[^a-z])(eye|eyes)([^a-z]|$)|目|眼|瞳/i;
const HAIR_MATERIAL = /hair|髪|髮|发|kami|cabello|pelo|ahoge|アホ毛|前髪|後髪|ponytail|bang|fringe/i;
const EYE_MATERIAL = /(^|[^a-z])(eye|eyes|eyeball|pupil|sclera|esclera|ojo|ojos|eyelash|lash|eyebrow|brow|ceja|pestañ)|目|眼|瞳|白目|まつ|睫|眉|ハイライト|高光|highlight/i;

function morphDeltaOf(mesh, expression, vertex, out) {
    out.set(0, 0, 0);
    const geometry = mesh.geometry;
    const relative = geometry.morphTargetsRelative;
    const position = geometry.getAttribute('position');
    for (const bind of expression?.binds ?? []) {
        if (!bind.primitives?.includes?.(mesh)) continue;
        const target = geometry.morphAttributes?.position?.[bind.index];
        if (!target) continue;
        const w = Number.isFinite(bind.weight) ? bind.weight : 1;
        let x = target.getX(vertex), y = target.getY(vertex), z = target.getZ(vertex);
        if (!relative) { x -= position.getX(vertex); y -= position.getY(vertex); z -= position.getZ(vertex); }
        out.x += x * w; out.y += y * w; out.z += z * w;
    }
    return out;
}

/** The bones that move with the head and are not hair or cloth physics. */
function headRigidBones(skeleton, head, physicsBones) {
    const rigid = new Set();
    skeleton.bones.forEach((bone, index) => {
        for (let up = bone; up; up = up.parent) {
            if (up === head) {
                if (bone === head || !physicsBones.has(bone)) rigid.add(index);
                return;
            }
            if (physicsBones.has(up)) return;
        }
    });
    return rigid;
}

function rigidWeights(mesh, rigid) {
    const index = mesh.geometry.getAttribute('skinIndex'), weight = mesh.geometry.getAttribute('skinWeight');
    const count = mesh.geometry.getAttribute('position').count;
    const out = new Float32Array(count);
    if (!index || !weight) return out;
    for (let v = 0; v < count; v++) {
        let w = 0;
        for (let c = 0; c < 4; c++) if (rigid.has(index.getComponent(v, c))) w += weight.getComponent(v, c);
        out[v] = w;
    }
    return out;
}

function materialsPerVertex(mesh, test) {
    const geometry = mesh.geometry;
    const count = geometry.getAttribute('position').count;
    const flags = new Uint8Array(count);
    const materials = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
    const index = geometry.index;
    const groups = Array.isArray(mesh.material) && geometry.groups.length
        ? geometry.groups : [{ start: 0, count: index ? index.count : count, materialIndex: 0 }];
    for (const group of groups) {
        const material = materials[group.materialIndex ?? 0];
        if (!material || !test(material)) continue;
        for (let i = group.start; i < group.start + group.count; i++) flags[index ? index.getX(i) : i] = 1;
    }
    return flags;
}

/**
 * The face frame in bind space (where the meshes were bound), from her eyes and the volume of her
 * head: `eyeMid`, `E` (eye gap), unit `right` (towards her left), `up`, `forward`, and the head's
 * box in those axes (`box`, in E around `eyeMid`).
 */
export function faceFrame({ eyeLeft, eyeRight, up = new THREE.Vector3(0, 1, 0), headPoints, centroid, front = null }) {
    const eyeMid = eyeLeft.clone().add(eyeRight).multiplyScalar(0.5);
    const across = eyeLeft.clone().sub(eyeRight);
    const E = across.length();
    if (!(E > 0)) return null;
    const upAxis = up.clone().normalize();
    const right = across.addScaledVector(upAxis, -across.dot(upAxis)).normalize();
    const forward = new THREE.Vector3().crossVectors(upAxis, right).normalize();
    // Her face is in front of the middle of her head: `front` is a point on it (the eyelids and
    // lips her blink and «a» move). Eye bones alone are not enough: IRIS's sit as far back as her
    // neck (2026-11-27), and the frame came out facing backwards.
    if (centroid && (front ?? eyeMid).clone().sub(centroid).dot(forward) < 0) forward.negate();
    const frame = { eyeMid, E, right, up: upAxis, forward };
    const box = { min: [Infinity, Infinity, Infinity], max: [-Infinity, -Infinity, -Infinity] };
    const local = new THREE.Vector3();
    for (const point of headPoints ?? []) {
        toFrame(frame, point, local);
        const v = [local.x, local.y, local.z];
        for (let i = 0; i < 3; i++) { box.min[i] = Math.min(box.min[i], v[i]); box.max[i] = Math.max(box.max[i], v[i]); }
    }
    frame.box = Number.isFinite(box.min[0]) ? box : { min: [-1.4, -2, -2.4], max: [1.4, 2, 0.8] };
    frame.center = [0, 1, 2].map(i => (frame.box.min[i] + frame.box.max[i]) / 2);
    return frame;
}

/** A bind-space point in the face frame, in eye distances: x to her left, y up, z forward. */
export function toFrame(frame, point, out = new THREE.Vector3()) {
    const dx = point.x - frame.eyeMid.x, dy = point.y - frame.eyeMid.y, dz = point.z - frame.eyeMid.z;
    const along = axis => (dx * axis.x + dy * axis.y + dz * axis.z) / frame.E;
    return out.set(along(frame.right), along(frame.up), along(frame.forward));
}

/** Back from the face frame to bind space. */
export function fromFrame(frame, x, y, z, out = new THREE.Vector3()) {
    return out.copy(frame.eyeMid).addScaledVector(frame.right, x * frame.E)
        .addScaledVector(frame.up, y * frame.E).addScaledVector(frame.forward, z * frame.E);
}

/** The middle of a cheek, below and a little outside an eye ellipse (frame coordinates). */
export const cheekCentre = (eye) => [eye.x * 1.08, eye.y - eye.ay - 0.38];

const DEFAULT_EYES = Object.freeze({ left: { x: 0.5, y: 0, ax: 0.3, ay: 0.24 }, right: { x: -0.5, y: 0, ax: 0.3, ay: 0.24 } });
const DEFAULT_MOUTH = Object.freeze({ x: 0, y: -1.05, ax: 0.3, ay: 0.14 });

/**
 * The cheek weights of one vertex at frame coordinates (x, y, z): [mobility, blush]. `eyes` and
 * `mouth` are ellipses measured from the blink and «aa» morphs; `lip` is how much «aa» moves this
 * very vertex (0..1 of the most it moves any).
 */
export function cheekWeight(frame, x, y, z, { eyes = DEFAULT_EYES, mouth = DEFAULT_MOUTH, lip = 0 } = {}) {
    // Only the front of her head: the direction from the middle of her head must face forward.
    const [cx, cy, cz] = frame.center ?? [0, 0.3, -0.9];
    const dx = x - cx, dy = y - cy, dz = z - cz;
    const front = smoothstep(0.1, 0.5, dz / Math.max(1e-6, Math.hypot(dx * 0.8, dy * 0.6, dz)));
    const lateral = 1 - smoothstep(0.95, 1.25, Math.abs(x));
    const faceBox = (1 - smoothstep(0.6, 0.95, y)) * smoothstep(-1.75, -1.35, y);
    let cheek = 0, blush = 0;
    for (const eye of [eyes.left, eyes.right]) {
        const [ccx, ccy] = cheekCentre(eye);
        const r2 = (x - ccx) ** 2 + (y - ccy) ** 2;
        cheek = Math.max(cheek, Math.exp(-r2 / (2 * 0.36 * 0.36)));
        blush = Math.max(blush, Math.exp(-((x - ccx) ** 2 / (2 * 0.27 * 0.27) + (y - ccy + 0.02) ** 2 / (2 * 0.2 * 0.2))));
    }
    // The eyes stay still, with more room below them: the skin under the lower lid covers the
    // eye white, and dented it showed a white crescent (IRIS, 2026-11-27).
    let eyeKeep = 1;
    for (const eye of [eyes.left, eyes.right]) {
        const below = y < eye.y ? 0.1 : 0;
        const d = Math.hypot((x - eye.x) / (eye.ax * 1.2 + 0.06), (y - eye.y) / (eye.ay * 1.25 + 0.06 + below));
        eyeKeep = Math.min(eyeKeep, smoothstep(1, 1.55, d));
    }
    const md = Math.hypot((x - mouth.x) / (mouth.ax * 1.2 + 0.06), (y - mouth.y) / (mouth.ay * 1.5 + 0.06));
    const lipKeep = smoothstep(0.9, 1.7, md) * (1 - smoothstep(0.12, 0.45, lip));
    const gate = front * lateral * faceBox * eyeKeep;
    return [gate * lipKeep * Math.max(0.22, cheek), gate * blush];
}

/** Below this height (frame y, eye distances) a touch is under her eyes: cheek height. */
export const CHEEK_TOP = -0.08;
// A lock of hair over a cheek counts as the cheek this far out (the skin's band is 1.15 wide).
const HAIR_CHEEK_BAND = 1.3;

/**
 * Classify a touch at frame coordinates: 'top' (petting), 'cheekL', 'cheekR', 'face' or 'head'.
 * Depth is read against her head's own box (`center` to `front` along z), not the eye bones, which
 * some rigs put far back. Hair at cheek height, in front of the middle of her head, is the cheek
 * it covers.
 */
export function touchZone(x, y, z, { onHair = false, center = -0.9, front = 0.8 } = {}) {
    const depth = (z - center) / Math.max(1e-6, front - center);
    if (y > 0.8 || (onHair && y > 0.15)) return 'top';
    const band = onHair ? HAIR_CHEEK_BAND : 1.15;
    if (y < CHEEK_TOP && y > -1.6 && Math.abs(x) > 0.16 && Math.abs(x) < band && depth > (onHair ? -0.4 : -0.15)) return x > 0 ? 'cheekL' : 'cheekR';
    if (depth > 0.3 && y > -1.7) return 'face';
    return y > 0.15 ? 'top' : 'head';
}

// ── The shader patch ───────────────────────────────────────────────────────

const VERTEX_PARS = /* glsl */`
attribute vec2 touchWeight;
uniform vec4 irisTouchPoke[${MAX_POKES}];
uniform vec3 irisTouchPokeN[${MAX_POKES}];
uniform vec4 irisTouchGrab[${MAX_PULLS}];
uniform vec3 irisTouchDelta[${MAX_PULLS}];
uniform vec4 irisTouchShape;
uniform vec4 irisTouchStyle;
varying vec2 vIrisTouch;
varying float vIrisTouchPress;
vec3 irisTouchOffset(vec3 p, vec3 n, inout vec3 tilt, inout float press) {
    float m = touchWeight.x;
    vec3 offset = vec3(0.0);
    if (m <= 0.0) return offset;
    float r = irisTouchShape.x;
    float inv = 1.0 / (r * r);
    for (int i = 0; i < ${MAX_POKES}; i++) {
        float a = irisTouchPoke[i].w;
        if (a == 0.0) continue;
        vec3 pn = irisTouchPokeN[i];
        vec3 d = p - irisTouchPoke[i].xyz;
        float h = dot(d, pn);
        vec3 t = d - pn * h;
        float t2 = dot(t, t) * inv;
        float depth = exp(-h * h * inv * 0.5);
        float g = exp(-t2 * 0.5) * depth;
        float tl = sqrt(dot(t, t)) + 1e-6;
        // The dent, and the skin it pushed aside: outward along the skin, lifted round the rim.
        float lift = exp(-t2 / (2.0 * 1.44)) * depth;
        float aside = a * irisTouchStyle.y * sqrt(t2) * exp(-t2 / (2.0 * 1.69)) * depth;
        float rim = a * irisTouchStyle.z * t2 * lift;
        offset += (-pn * a * g + (t / tl) * aside + pn * rim) * m;
        // The normal follows the slope of the dent and of its rim, only in part: toon shading
        // turns a steep slope into a hard shadow line (a dark crease on IRIS, 2026-11-27).
        float slope = g + irisTouchStyle.z * (2.0 - t2 / 1.44) * lift;
        tilt -= irisTouchStyle.x * a * m * slope * t * inv;
        // How pressed this skin is (depth over radius): the soft shadow of a dimple, not a line.
        press += m * g * max(a, 0.0) * sqrt(inv);
    }
    for (int j = 0; j < ${MAX_PULLS}; j++) {
        vec3 delta = irisTouchDelta[j];
        if (dot(delta, delta) == 0.0) continue;
        vec3 d = p - irisTouchGrab[j].xyz;
        float rho = irisTouchGrab[j].w;
        offset += delta * exp(-dot(d, d) / (2.0 * rho * rho)) * m;
    }
    offset += n * irisTouchShape.y * m * m;
    return offset;
}
`;

const FRAGMENT_PARS = /* glsl */`
uniform vec4 irisTouchShape;
uniform vec4 irisTouchStyle;
varying vec2 vIrisTouch;
varying float vIrisTouchPress;
`;

const FRAGMENT_BLUSH = /* glsl */`
#ifndef OUTLINE
    if (irisTouchShape.w > 0.5) {
        gl_FragColor.rgb = mix(gl_FragColor.rgb, vec3(vIrisTouch.x, 0.08, vIrisTouch.y), 0.85);
    } else {
        float irisBlush = clamp(vIrisTouch.y * irisTouchShape.z, 0.0, 1.0);
        gl_FragColor.rgb = mix(gl_FragColor.rgb, gl_FragColor.rgb * vec3(1.02, 0.62, 0.64) + vec3(0.07, 0.005, 0.02), irisBlush * 0.8);
        // The dimple's shadow: warm and round, deepest under the finger.
        gl_FragColor.rgb *= vec3(1.0) - clamp(vIrisTouchPress * irisTouchStyle.w, 0.0, 0.35) * vec3(0.55, 0.78, 0.72);
    }
#endif
`;

export const TOUCH_SHADER_KEY = 'iris-touch-v2';

/** Shader text with the touch deformation in; unchanged (and false) when an anchor is missing. */
export function patchTouchShader(shader) {
    const vertex = shader.vertexShader, fragment = shader.fragmentShader;
    const anchors = [vertex.includes('#include <morphnormal_vertex>'), vertex.includes('#include <skinning_vertex>'),
        fragment.includes('#include <colorspace_fragment>')];
    if (anchors.includes(false)) return false;
    // Declarations go first: MToon writes its final colour in a function above main().
    shader.vertexShader = VERTEX_PARS + vertex
        .replace('#include <morphnormal_vertex>', '#include <morphnormal_vertex>\n'
            + 'vec3 irisTouchTilt = vec3(0.0);\nfloat irisTouchPress = 0.0;\n'
            + 'vec3 irisTouchMove = irisTouchOffset(position, normal, irisTouchTilt, irisTouchPress);\n'
            + 'objectNormal = normalize(objectNormal + irisTouchTilt);\nvIrisTouch = touchWeight;\nvIrisTouchPress = irisTouchPress;')
        .replace('#include <skinning_vertex>', 'transformed += irisTouchMove;\n#include <skinning_vertex>');
    shader.fragmentShader = FRAGMENT_PARS + fragment
        .replace('#include <colorspace_fragment>', FRAGMENT_BLUSH + '#include <colorspace_fragment>');
    return true;
}

/**
 * The shader's inputs at rest. Every poke starts with amplitude 0: THREE.Vector4() is (0, 0, 0, 1),
 * and with that default four phantom pokes at the model's origin flattened every VRM's face from
 * the moment it loaded until the first touch (white crescents under IRIS's eyes, 2026-11-27).
 */
export function touchUniforms() {
    return {
        irisTouchPoke: { value: Array.from({ length: MAX_POKES }, () => new THREE.Vector4(0, 0, 0, 0)) },
        irisTouchPokeN: { value: Array.from({ length: MAX_POKES }, () => new THREE.Vector3(0, 0, 1)) },
        irisTouchGrab: { value: Array.from({ length: MAX_PULLS }, () => new THREE.Vector4(0, 0, 0, 1)) },
        irisTouchDelta: { value: Array.from({ length: MAX_PULLS }, () => new THREE.Vector3()) },
        irisTouchShape: { value: new THREE.Vector4(1, 0, 0, 0) },
        irisTouchStyle: { value: new THREE.Vector4(TOUCH_SHAPE.tilt, TOUCH_SHAPE.aside, TOUCH_SHAPE.rim, TOUCH_SHAPE.shade) },
    };
}

/** Give `material` the touch deformation; the program cache keeps patched and plain apart. */
export function patchTouchMaterial(material, uniforms) {
    if (!material || material.userData?.irisTouch) return false;
    const compile = material.onBeforeCompile;
    const key = material.customProgramCacheKey;
    material.onBeforeCompile = function (shader, renderer) {
        compile?.call(this, shader, renderer);
        if (patchTouchShader(shader)) Object.assign(shader.uniforms, uniforms);
    };
    material.customProgramCacheKey = function () { return `${key?.call(this) ?? ''}|${TOUCH_SHADER_KEY}`; };
    material.userData.irisTouch = true;
    material.userData.irisTouchUniforms = uniforms;
    material.needsUpdate = true;
    return true;
}

// ── The rig: one avatar's face, contact and deformation ───────────────────

const _m = new THREE.Matrix4(), _n = new THREE.Matrix4(), _v = new THREE.Vector3(), _w = new THREE.Vector3();
const _q = new THREE.Quaternion(), _p = new THREE.Quaternion(), _d = new THREE.Quaternion();

/**
 * Möller–Trumbore against a flat triangle list; returns the nearest t or Infinity. `skip[i]` set
 * leaves triangle i out (the hair, to find the skin under it).
 */
export function rayTriangles(origin, dir, tris, best = { t: Infinity, index: -1 }, skip = null) {
    const ox = origin.x, oy = origin.y, oz = origin.z, dx = dir.x, dy = dir.y, dz = dir.z;
    for (let i = 0; i < tris.length; i += 9) {
        if (skip?.[i / 9]) continue;
        const ax = tris[i], ay = tris[i + 1], az = tris[i + 2];
        const e1x = tris[i + 3] - ax, e1y = tris[i + 4] - ay, e1z = tris[i + 5] - az;
        const e2x = tris[i + 6] - ax, e2y = tris[i + 7] - ay, e2z = tris[i + 8] - az;
        const px = dy * e2z - dz * e2y, py = dz * e2x - dx * e2z, pz = dx * e2y - dy * e2x;
        const det = e1x * px + e1y * py + e1z * pz;
        if (Math.abs(det) < 1e-12) continue;
        const inv = 1 / det;
        const tx = ox - ax, ty = oy - ay, tz = oz - az;
        const u = (tx * px + ty * py + tz * pz) * inv;
        if (u < 0 || u > 1) continue;
        const qx = ty * e1z - tz * e1y, qy = tz * e1x - tx * e1z, qz = tx * e1y - ty * e1x;
        const v = (dx * qx + dy * qy + dz * qz) * inv;
        if (v < 0 || u + v > 1) continue;
        const t = (e2x * qx + e2y * qy + e2z * qz) * inv;
        if (t > 1e-6 && t < best.t) { best.t = t; best.index = i / 9; }
    }
    return best;
}

export class TouchRig {
    /** Measures `avatar` (a VRMAvatar): null when it has no head or eyes to work with. */
    static build(avatar) {
        try {
            const rig = new TouchRig(avatar);
            return rig.ready ? rig : null;
        } catch (error) {
            console.warn('[touch] no se pudo preparar el avatar para los mimos:', error);
            return null;
        }
    }

    constructor(avatar) {
        this.avatar = avatar;
        // Sizes and lighting of the dents (TOUCH_SHAPE); one rig can be given others to compare.
        this.shape = TOUCH_SHAPE;
        const vrm = avatar.vrm;
        this.vrm = vrm;
        this.head = vrm?.humanoid?.getRawBoneNode?.('head');
        this.ready = false;
        if (!this.head) return;
        const physicsBones = new Set();
        for (const joint of vrm.springBoneManager?.joints ?? []) if (joint.bone) physicsBones.add(joint.bone);
        const meshes = [];
        vrm.scene.traverse(object => { if (object.isSkinnedMesh && object.skeleton?.bones.includes(this.head)) meshes.push(object); });
        for (const mesh of meshes) {
            const rigidBodies = mesh.geometry.userData?.MMD?.rigidBodies ?? [];
            for (const body of rigidBodies) if (body.type !== 0 && mesh.skeleton.bones[body.boneIndex]) physicsBones.add(mesh.skeleton.bones[body.boneIndex]);
        }
        this.meshes = [];
        const points = [];
        const centroid = new THREE.Vector3();
        let pointCount = 0;
        for (const mesh of meshes) {
            const headIndex = mesh.skeleton.bones.indexOf(this.head);
            const rigid = headRigidBones(mesh.skeleton, this.head, physicsBones);
            const weights = rigidWeights(mesh, rigid);
            const position = mesh.geometry.getAttribute('position');
            let count = 0;
            for (let v = 0; v < weights.length; v++) {
                if (weights[v] < 0.5) continue;
                count++;
                _v.fromBufferAttribute(position, v).applyMatrix4(mesh.bindMatrix);
                centroid.add(_v); pointCount++;
                if (v % 7 === 0) points.push(_v.clone());
            }
            if (count < 30) continue;
            this.meshes.push({ mesh, headIndex, weights });
        }
        if (!this.meshes.length) return;
        centroid.multiplyScalar(1 / pointCount);
        this._buildFrame(points, centroid);
        if (!this.frame) return;
        this._buildPicking();
        this._buildWeights();
        this.ready = true;
    }

    /** Head-rigid transform of `entry`'s mesh: geometry space to world, this frame. */
    _geoToWorld(entry, out) {
        const { mesh, headIndex } = entry;
        return out.multiplyMatrices(mesh.skeleton.bones[headIndex].matrixWorld, mesh.skeleton.boneInverses[headIndex]).multiply(mesh.bindMatrix);
    }

    /** World to bind space through the head (rigid), for any world point near her head. */
    worldToBind(point, out = new THREE.Vector3()) {
        const { mesh, headIndex } = this.meshes[0];
        _m.multiplyMatrices(mesh.skeleton.bones[headIndex].matrixWorld, mesh.skeleton.boneInverses[headIndex]).invert();
        return out.copy(point).applyMatrix4(_m);
    }

    bindToWorld(point, out = new THREE.Vector3()) {
        const { mesh, headIndex } = this.meshes[0];
        _m.multiplyMatrices(mesh.skeleton.bones[headIndex].matrixWorld, mesh.skeleton.boneInverses[headIndex]);
        return out.copy(point).applyMatrix4(_m);
    }

    /** A bind-space direction in the world (no translation). */
    bindDirToWorld(dir, out = new THREE.Vector3()) {
        const { mesh, headIndex } = this.meshes[0];
        _m.multiplyMatrices(mesh.skeleton.bones[headIndex].matrixWorld, mesh.skeleton.boneInverses[headIndex]);
        return out.copy(dir).transformDirection(_m);
    }

    worldDirToBind(dir, out = new THREE.Vector3()) {
        const { mesh, headIndex } = this.meshes[0];
        _m.multiplyMatrices(mesh.skeleton.bones[headIndex].matrixWorld, mesh.skeleton.boneInverses[headIndex]).invert();
        return out.copy(dir).transformDirection(_m);
    }

    _expression(name) {
        const expression = this.vrm.expressionManager?.getExpression?.(name);
        return expression?.binds?.length ? expression : null;
    }

    /** Where the vertices an expression moves most are, in frame coordinates, per side. */
    _morphRegion(name, split) {
        const expression = this._expression(name);
        if (!expression) return null;
        const samples = [];
        let most = 0;
        for (const { mesh } of this.meshes) {
            const position = mesh.geometry.getAttribute('position');
            const linear = _n.copy(mesh.bindMatrix).setPosition(0, 0, 0);
            for (let v = 0; v < position.count; v++) {
                const delta = morphDeltaOf(mesh, expression, v, _w).applyMatrix4(linear).length();
                if (delta <= 0) continue;
                most = Math.max(most, delta);
                samples.push([delta, _v.fromBufferAttribute(position, v).applyMatrix4(mesh.bindMatrix).clone()]);
            }
        }
        if (!(most > 0)) return null;
        const strong = samples.filter(([delta]) => delta > most * 0.35).map(([, p]) => p);
        if (!this.frame) return { strong, most };
        const groups = split ? [strong.filter(p => toFrame(this.frame, p).x > 0), strong.filter(p => toFrame(this.frame, p).x <= 0)] : [strong];
        return groups.map(group => {
            if (group.length < 3) return null;
            const local = group.map(p => toFrame(this.frame, p));
            const xs = local.map(p => p.x), ys = local.map(p => p.y);
            const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
            return { x: (x0 + x1) / 2, y: (y0 + y1) / 2, ax: Math.max(0.08, (x1 - x0) / 2), ay: Math.max(0.05, (y1 - y0) / 2) };
        });
    }

    _buildFrame(points, centroid) {
        const humanoid = this.vrm.humanoid;
        const eyeBones = ['leftEye', 'rightEye'].map(name => humanoid.getRawBoneNode?.(name));
        let eyeLeft, eyeRight;
        this.head.updateWorldMatrix(true, true);
        if (eyeBones.every(Boolean)) {
            [eyeLeft, eyeRight] = eyeBones.map(bone => this.worldToBind(bone.getWorldPosition(new THREE.Vector3())));
        } else {
            // No eye bones: the eyelids the blink moves, split by her side (her left arm's side).
            const region = this._morphRegion('blink', false);
            const arm = humanoid.getRawBoneNode?.('leftUpperArm')?.getWorldPosition(new THREE.Vector3());
            if (!region?.strong?.length || !arm) return;
            const headAt = this.worldToBind(this.head.getWorldPosition(new THREE.Vector3()));
            const side = this.worldToBind(arm).sub(headAt).normalize();
            const left = region.strong.filter(p => p.clone().sub(headAt).dot(side) > 0);
            const right = region.strong.filter(p => p.clone().sub(headAt).dot(side) <= 0);
            if (left.length < 3 || right.length < 3) return;
            const mean = list => list.reduce((sum, p) => sum.add(p), new THREE.Vector3()).multiplyScalar(1 / list.length);
            [eyeLeft, eyeRight] = [mean(left), mean(right)];
        }
        // Bind space is upright (glTF and MMD are Y-up): her neck to her head confirms it.
        const neck = humanoid.getRawBoneNode?.('neck');
        let up = new THREE.Vector3(0, 1, 0);
        if (neck) {
            const along = this.worldToBind(this.head.getWorldPosition(new THREE.Vector3()))
                .sub(this.worldToBind(neck.getWorldPosition(new THREE.Vector3())));
            if (along.lengthSq() > 0 && along.normalize().y < 0.7) up = along;
        }
        const features = [...(this._morphRegion('blink', false)?.strong ?? []), ...(this._morphRegion('aa', false)?.strong ?? [])];
        const front = features.length ? features.reduce((sum, p) => sum.add(p), new THREE.Vector3()).multiplyScalar(1 / features.length) : null;
        this.frame = faceFrame({ eyeLeft, eyeRight, up, headPoints: points, centroid, front });
        if (!this.frame) return;
        // Every head vertex in face coordinates, once: the weights and the eye measures read them.
        const local = new THREE.Vector3();
        for (const entry of this.meshes) {
            const { mesh, weights } = entry;
            const position = mesh.geometry.getAttribute('position');
            entry.local = new Float32Array(position.count * 3).fill(NaN);
            for (let v = 0; v < position.count; v++) {
                if (weights[v] < 0.3) continue;
                toFrame(this.frame, _v.fromBufferAttribute(position, v).applyMatrix4(mesh.bindMatrix), local);
                entry.local[3 * v] = local.x; entry.local[3 * v + 1] = local.y; entry.local[3 * v + 2] = local.z;
            }
        }
        const eyes = this._morphRegion('blink', true);
        this.eyes = { left: eyes?.[0] ?? DEFAULT_EYES.left, right: eyes?.[1] ?? DEFAULT_EYES.right };
        this.mouth = this._morphRegion('aa', false)?.[0] ?? DEFAULT_MOUTH;
    }

    _buildPicking() {
        for (const entry of this.meshes) {
            const { mesh, weights } = entry;
            const geometry = mesh.geometry;
            const position = geometry.getAttribute('position');
            const index = geometry.index;
            const hair = materialsPerVertex(mesh, material => HAIR_MATERIAL.test(material.name ?? ''));
            const count = index ? index.count : position.count;
            const tris = [], hairTris = [];
            for (let i = 0; i + 2 < count; i += 3) {
                const a = index ? index.getX(i) : i, b = index ? index.getX(i + 1) : i + 1, c = index ? index.getX(i + 2) : i + 2;
                if (weights[a] < 0.5 || weights[b] < 0.5 || weights[c] < 0.5) continue;
                for (const v of [a, b, c]) tris.push(position.getX(v), position.getY(v), position.getZ(v));
                hairTris.push(hair[a] && hair[b] && hair[c] ? 1 : 0);
            }
            entry.tris = new Float32Array(tris);
            entry.hairTris = Uint8Array.from(hairTris);
            const box = new THREE.Box3();
            for (let i = 0; i < tris.length; i += 3) box.expandByPoint(_v.set(tris[i], tris[i + 1], tris[i + 2]));
            entry.sphere = box.getBoundingSphere(new THREE.Sphere());
        }
    }

    _buildWeights() {
        const lip = this._expression('aa');
        let lipMost = 0;
        const lipDeltas = this.meshes.map(({ mesh }) => {
            const count = mesh.geometry.getAttribute('position').count;
            const out = new Float32Array(count);
            if (!lip) return out;
            const linear = _n.copy(mesh.bindMatrix).setPosition(0, 0, 0);
            for (let v = 0; v < count; v++) {
                out[v] = morphDeltaOf(mesh, lip, v, _w).applyMatrix4(linear).length();
                lipMost = Math.max(lipMost, out[v]);
            }
            return out;
        });
        // A material patched before (the rig measured again for the same avatar) keeps the uniforms
        // its shader was built with.
        const materials = this.meshes.flatMap(({ mesh }) => (Array.isArray(mesh.material) ? mesh.material : [mesh.material]));
        this.uniforms = materials.map(material => material?.userData?.irisTouchUniforms).find(Boolean) ?? touchUniforms();
        this.patched = [];
        // Eyeballs never dent: whatever hangs from the eye bones, or is named as an eye or hair
        // (IRIS's «OJOS» mesh draws with «iris_texture», which no name rule can tell from her name).
        const eyeBones = ['leftEye', 'rightEye'].map(name => this.vrm.humanoid.getRawBoneNode?.(name)).filter(Boolean);
        this.meshes.forEach((entry, i) => {
            const { mesh, weights, local } = entry;
            const position = mesh.geometry.getAttribute('position');
            if (EYE_MATERIAL.test(mesh.name ?? '') || HAIR_MATERIAL.test(mesh.name ?? '')) return;
            const skip = materialsPerVertex(mesh, material => HAIR_MATERIAL.test(material.name ?? '') || EYE_MATERIAL.test(material.name ?? ''));
            const eyeIndices = new Set();
            mesh.skeleton.bones.forEach((bone, index) => {
                for (let up = bone; up; up = up.parent) if (eyeBones.includes(up)) { eyeIndices.add(index); return; }
            });
            const onEyes = eyeIndices.size ? rigidWeights(mesh, eyeIndices) : null;
            const data = new Float32Array(position.count * 2);
            let moving = 0;
            for (let v = 0; v < position.count; v++) {
                if (skip[v] || weights[v] < 0.5 || onEyes?.[v] > 0.3) continue;
                const [m, blush] = cheekWeight(this.frame, local[3 * v], local[3 * v + 1], local[3 * v + 2],
                    { eyes: this.eyes, mouth: this.mouth, lip: lipMost > 0 ? lipDeltas[i][v] / lipMost : 0 });
                const rigid = smoothstep(0.5, 0.9, weights[v]);
                data[2 * v] = m * rigid;
                data[2 * v + 1] = blush * rigid;
                if (m > 0.05) moving++;
            }
            if (!moving) return;
            // Every mesh of one avatar shares the space its skeleton was bound in; one that does
            // not is left still rather than dented in the wrong place.
            if (this.patched.length && !this.patched[0].mesh.bindMatrix.equals(mesh.bindMatrix)) return;
            mesh.geometry.setAttribute('touchWeight', new THREE.BufferAttribute(data, 2));
            const materials = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
            for (const material of materials) patchTouchMaterial(material, this.uniforms);
            this.patched.push(entry);
        });
        const bind = (this.patched[0] ?? this.meshes[0]).mesh.bindMatrix;
        this.toGeo = bind.clone().invert();
        this.geoScale = 1 / new THREE.Vector3().setFromMatrixColumn(bind, 0).length();
    }

    /** How much `name` at full weight shuts her eyes, against the blink (0..1), measured once. */
    closure(name) {
        this._closures ??= new Map();
        if (this._closures.has(name)) return this._closures.get(name);
        const measure = expression => {
            if (!expression) return 0;
            let total = 0;
            for (const eye of [this.eyes.left, this.eyes.right]) {
                let down = 0, rise = 0;
                for (const { mesh, local } of this.meshes) {
                    const linear = _n.copy(mesh.bindMatrix).setPosition(0, 0, 0);
                    for (let v = 0; v < local.length / 3; v++) {
                        const x = local[3 * v], y = local[3 * v + 1];
                        if (!(Math.abs(x - eye.x) <= eye.ax * 1.2 && Math.abs(y - eye.y) <= eye.ay * 1.6)) continue;
                        const dy = morphDeltaOf(mesh, expression, v, _w).applyMatrix4(linear).dot(this.frame.up) / this.frame.E;
                        if (y >= eye.y) down = Math.max(down, -dy); else rise = Math.max(rise, dy);
                    }
                }
                total += down + rise;
            }
            return total / 2;
        };
        const blink = measure(this._expression('blink')) || (measure(this._expression('blinkLeft')) + measure(this._expression('blinkRight')));
        const value = blink > 0 ? clamp(measure(this._expression(name)) / blink) : 0;
        this._closures.set(name, value);
        return value;
    }

    /**
     * The nearest touch of a world ray on her head: { zone, bind, world, local, distance, onHair }
     * or null. A lock of hair over a cheek, below her eyes, is that cheek, and the touch lands on
     * the skin under it (Lily, Mornye, 2026-11-27): the dent may stay hidden, the reaction shows.
     */
    pick(ray) {
        let best = null, skin = null;
        const origin = new THREE.Vector3(), dir = new THREE.Vector3();
        for (const entry of this.meshes) {
            this._geoToWorld(entry, _m);
            _n.copy(_m).invert();
            origin.copy(ray.origin).applyMatrix4(_n);
            // Not normalised: t stays the world distance along the ray, comparable across meshes.
            dir.copy(ray.direction).applyMatrix4(_n.setPosition(0, 0, 0));
            const s = entry.sphere;
            const oc = origin.clone().sub(s.center);
            const b = oc.dot(dir), c = oc.lengthSq() - s.radius * s.radius, a = dir.lengthSq();
            if (b * b - a * c < 0) continue;
            const hit = rayTriangles(origin, dir, entry.tris);
            if (hit.index < 0) continue;
            const onHair = entry.hairTris[hit.index] === 1;
            if (!best || hit.t < best.distance) best = { entry, distance: hit.t, geo: origin.clone().addScaledVector(dir, hit.t), onHair };
            // The skin under the hair: the nearest triangle that is not hair.
            const under = onHair ? rayTriangles(origin, dir, entry.tris, { t: Infinity, index: -1 }, entry.hairTris) : hit;
            if (under.index >= 0 && (!skin || under.t < skin.distance)) skin = { entry, distance: under.t, geo: origin.clone().addScaledVector(dir, under.t), onHair: false };
        }
        if (!best) return null;
        const touch = this._touchAt(best, ray);
        if (best.onHair && skin && touch.local.y < CHEEK_TOP) {
            const under = this._touchAt(skin, ray);
            if (under.zone === 'cheekL' || under.zone === 'cheekR') return { ...under, onHair: true };
        }
        return touch;
    }

    _touchAt(found, ray) {
        const bind = found.geo.clone().applyMatrix4(found.entry.mesh.bindMatrix);
        const local = toFrame(this.frame, bind);
        return {
            zone: touchZone(local.x, local.y, local.z, { onHair: found.onHair, center: this.frame.center[2], front: this.frame.box.max[2] }),
            bind, local, world: ray.at(found.distance, new THREE.Vector3()), distance: found.distance, onHair: found.onHair,
        };
    }

    /** The face frame in the world, this frame: { center, right, up, forward, E }. */
    worldFrame() {
        const f = this.frame;
        const center = this.bindToWorld(fromFrame(f, 0, -0.45, 0));
        const right = this.bindDirToWorld(f.right), up = this.bindDirToWorld(f.up), forward = this.bindDirToWorld(f.forward);
        const E = this.bindToWorld(fromFrame(f, 1, 0, 0)).distanceTo(this.bindToWorld(fromFrame(f, 0, 0, 0)));
        return { center, right, up, forward, E };
    }

    has(name) { return Boolean(this._expression(name)); }

    /** Bind-space pokes and pulls into the shader's geometry space. */
    writeUniforms({ pokes, pulls, puff, blush, debug }) {
        if (!this.patched.length) return;
        const u = this.uniforms, f = this.frame, s = this.geoScale, shape = this.shape;
        const linear = _n.copy(this.toGeo).setPosition(0, 0, 0);
        pokes.forEach((poke, i) => {
            const target = u.irisTouchPoke.value[i];
            if (!poke || Math.abs(poke.x) < 1e-5) { target.w = 0; return; }
            _v.copy(poke.center).applyMatrix4(this.toGeo);
            target.set(_v.x, _v.y, _v.z, poke.x * f.E * s);
            u.irisTouchPokeN.value[i].copy(poke.normal).applyMatrix4(linear).normalize();
        });
        pulls.forEach((pull, i) => {
            const grab = u.irisTouchGrab.value[i], delta = u.irisTouchDelta.value[i];
            if (!pull || pull.x.lengthSq() < 1e-10) { delta.set(0, 0, 0); return; }
            _v.copy(pull.grab).applyMatrix4(this.toGeo);
            grab.set(_v.x, _v.y, _v.z, shape.pullRadius * f.E * s);
            delta.copy(pull.x).multiplyScalar(f.E).applyMatrix4(linear);
        });
        u.irisTouchShape.value.set(shape.pokeRadius * f.E * s, puff * shape.puff * f.E * s, blush, debug ? 1 : 0);
        u.irisTouchStyle.value.set(shape.tilt, shape.aside, shape.rim, shape.shade);
    }
}

// ── The controller ─────────────────────────────────────────────────────────

const PET_DELAY = 0.12;            // seconds of contact before her eyes start to close
const DRAG_PIXELS = 7;             // a press that moves farther becomes a pull
const LEAN_SPRING = [7.5, 4.2];    // her head: softer than the skin
const CLOSE_UP_SECONDS = 0.9;
const CLOSE_UP_HEIGHT = 6;         // eye distances the close-up shows from top to bottom
const ALERT_SECONDS = 0.9;         // a poke keeps her eyes open this long
const DOUBLE_SECONDS = 0.4;        // two presses on her head this close in time…
const DOUBLE_PIXELS = 24;          // …and on screen are a double click

/**
 * Pointer handling, springs and mood for the avatar `getAvatar()` returns (a VRMAvatar). The
 * avatar calls `touch.apply` and `touch.restore` around vrm.update (avatar.js finalizeFrame).
 *
 * `active` is the room's «Mimos» mode (close-up camera, hover cursor); `alwaysOn` lets a touch
 * that lands on her head or face work without it (Companion, and the room's normal view). A press
 * anywhere else is left to the page (window drag, orbit controls, hotspots). `tolerance` (pixels)
 * also takes a press that misses her head by that much; `onDoubleTouch(hit)` hears two quick
 * presses on her; `sounds` (touch_sounds.js) plays the pop and the release.
 */
export class TouchReactions {
    constructor({ canvas, camera, getAvatar, onMood = () => {}, onTouch = () => {}, onDoubleTouch = null, sounds = null,
        tolerance = 0, alwaysOn = true, now = () => performance.now() / 1000 } = {}) {
        Object.assign(this, { canvas, camera, getAvatar, onMood, onTouch, onDoubleTouch, sounds, tolerance, alwaysOn, now });
        this.active = false;
        this.mood = new TouchMood();
        this.pokes = Array.from({ length: MAX_POKES }, () => null);
        this.pulls = Array.from({ length: MAX_PULLS }, () => null);
        this.gesture = null;
        this.raycaster = new THREE.Raycaster();
        this.levels = { closed: 0, alert: 0, flinchL: 0, flinchR: 0, puff: 0, pull: 0 };
        this._alertUntil = -Infinity;
        this.lean = { roll: [0, 0], pitch: [0, 0], yaw: [0, 0] };
        this.closeUp = { blend: 0, center: null };
        this.debug = false;
        this._pulses = [];
        this._ownMood = null;
        this._saved = new Map();
        this._headRestore = null;
        this.layer = { apply: (avatar, dt) => this._apply(avatar, dt), restore: () => this._restore() };
        if (canvas) this._listen(canvas);
    }

    _listen(canvas) {
        this._handlers = {
            pointerdown: event => this._down(event),
            pointermove: event => this._move(event),
            pointerup: event => this._up(event),
            pointercancel: event => this._up(event, true),
            lostpointercapture: event => this._up(event, true),
        };
        for (const [type, handler] of Object.entries(this._handlers)) canvas.addEventListener(type, handler, { capture: true });
    }

    dispose() {
        if (this.canvas && this._handlers) {
            for (const [type, handler] of Object.entries(this._handlers)) this.canvas.removeEventListener(type, handler, { capture: true });
        }
        this._handlers = null;
        const avatar = this.rig?.avatar;
        if (avatar?.touch === this.layer) avatar.touch = null;
        this.rig = null;
    }

    /** The «Mimos» mode: close-up camera and a hand cursor over her head. */
    setActive(on) {
        this.active = Boolean(on);
        if (!this.active) this._setCursor('');
        return this.active;
    }

    /** Keeps the avatar's hook on the current avatar, and the rig measured for it. */
    _attach() {
        const avatar = this.getAvatar?.();
        if (!avatar?.vrm) return null;
        if (avatar.touch !== this.layer) avatar.touch = this.layer;
        if (this.rig?.avatar !== avatar || this.rig?.vrm !== avatar.vrm) {
            this.rig = TouchRig.build(avatar);
            this.pokes.fill(null);
            this.pulls.fill(null);
            this.gesture = null;
            // Write the rest values once even if nothing happens: uniforms a material kept from an
            // earlier rig may still hold the last touch.
            this._dirty = true;
            if (this.rig) console.log(`[touch] avatar listo: ${this.rig.patched.length} malla(s) con cachetes, E=${this.rig.frame.E.toFixed(4)}`);
        }
        return this.rig;
    }

    /** Call once per frame from the page: hooks the current avatar (cheap when nothing changed). */
    update() { this._attach(); }

    _ray(event) { return this._rayAt(event.clientX, event.clientY); }

    _rayAt(x, y) {
        const rect = this.canvas.getBoundingClientRect();
        const ndc = new THREE.Vector2(((x - rect.left) / rect.width) * 2 - 1, -((y - rect.top) / rect.height) * 2 + 1);
        this.raycaster.setFromCamera(ndc, this.camera);
        return this.raycaster.ray;
    }

    /** What a pointer at (clientX, clientY) would touch, or null. */
    pickAt(event) {
        const rig = this._attach();
        if (!rig || !this.camera) return null;
        this.camera.updateMatrixWorld();
        return this._pickNear(rig, event.clientX, event.clientY);
    }

    /**
     * The touch under (x, y) or, with a `tolerance`, the one just beside it: two rings of rays
     * (half the tolerance, then all of it) and, on the nearest ring that finds her, the zone most
     * of its rays agree on. A small head on the desktop (Companion) is hard to hit exactly.
     */
    _pickNear(rig, x, y) {
        const center = rig.pick(this._rayAt(x, y));
        if ((center && center.zone !== 'head') || !(this.tolerance > 0)) return center;
        for (const radius of [this.tolerance / 2, this.tolerance]) {
            const hits = [];
            for (let k = 0; k < 8; k++) {
                const angle = k * Math.PI / 4;
                const hit = rig.pick(this._rayAt(x + Math.cos(angle) * radius, y + Math.sin(angle) * radius));
                if (hit && hit.zone !== 'head') hits.push(hit);
            }
            if (!hits.length) continue;
            const votes = new Map();
            for (const hit of hits) votes.set(hit.zone, (votes.get(hit.zone) ?? 0) + 1);
            const zone = [...votes].sort((a, b) => b[1] - a[1])[0][0];
            return { ...hits.find(hit => hit.zone === zone), near: radius };
        }
        return center;
    }

    _consume(event) {
        event.preventDefault();
        event.stopImmediatePropagation();
    }

    _down(event) {
        if ((!this.active && !this.alwaysOn) || this.gesture) return;
        if (event.pointerType === 'mouse' && event.button !== 0) return;
        const hit = this.pickAt(event);
        if (!hit || hit.zone === 'head') return;
        this._consume(event);
        try { this.canvas.setPointerCapture?.(event.pointerId); } catch { /* synthetic events */ }
        const at = { x: event.clientX, y: event.clientY, time: this.now() };
        const last = this._lastDown;
        this._lastDown = at;
        const double = Boolean(last && at.time - last.time < DOUBLE_SECONDS && Math.hypot(at.x - last.x, at.y - last.y) < DOUBLE_PIXELS);
        if (double) this._lastDown = null;
        if (hit.zone === 'top') {
            this.gesture = { kind: 'pet', id: event.pointerId, start: at, last: at, hit, since: this.now(), stroke: 0, speed: 0 };
            this._setCursor('grabbing');
        } else {
            const slot = this._press(hit);
            this.gesture = { kind: 'press', id: event.pointerId, start: at, hit, slot, both: event.shiftKey };
            this._setCursor('grabbing');
        }
        this.onTouch(this.gesture.kind, hit.zone);
        if (double) {
            try { this.onDoubleTouch?.(hit); } catch (error) { console.warn('[touch] onDoubleTouch:', error); }
        }
    }

    _move(event) {
        const gesture = this.gesture;
        if (!gesture || event.pointerId !== gesture.id) {
            if (this.active && !this.gesture && event.pointerType !== 'touch') this._hover = event;
            return;
        }
        this._consume(event);
        const now = this.now();
        if (gesture.kind === 'pet') {
            const dt = Math.max(1e-3, now - gesture.last.time);
            const px = Math.hypot(event.clientX - gesture.last.x, event.clientY - gesture.last.y);
            gesture.speed = gesture.speed * 0.7 + 0.3 * (px / dt);
            gesture.last = { x: event.clientX, y: event.clientY, time: now };
            gesture.pending = event;
            return;
        }
        const moved = Math.hypot(event.clientX - gesture.start.x, event.clientY - gesture.start.y);
        if (gesture.kind === 'press' && moved > DRAG_PIXELS) {
            this._releasePoke(gesture.slot, { quiet: true });
            gesture.kind = 'pull';
            gesture.pulls = this._startPull(gesture.hit, gesture.both || event.shiftKey);
            this.onTouch('pull', gesture.hit.zone);
        }
        if (gesture.kind === 'pull') gesture.pending = event;
    }

    _up(event, cancelled = false) {
        const gesture = this.gesture;
        if (!gesture || event.pointerId !== gesture.id) return;
        if (!cancelled) this._consume(event);
        this.gesture = null;
        try { this.canvas.releasePointerCapture?.(event.pointerId); } catch { /* already released */ }
        this._setCursor(this.active ? 'grab' : '');
        if (gesture.kind === 'press') {
            this._releasePoke(gesture.slot);
            this._registerPoke(gesture.hit);
        } else if (gesture.kind === 'pull') {
            let stretch = 0;
            for (const pull of gesture.pulls ?? []) {
                if (!pull) continue;
                stretch = Math.max(stretch, pull.x.length() / TOUCH_SHAPE.pullMax);
                pull.target.set(0, 0, 0);
            }
            // The cheek snaps back: a light sound, as big as the stretch.
            if (stretch > 0.15) this.sounds?.release?.(clamp(stretch));
            if (this.now() - gesture.start.time > 0.6) this._registerPoke(gesture.hit, { quiet: true });
        } else if (gesture.kind === 'pet') {
            // Petted long enough: affectionate once she opens her eyes.
            if (this.mood.affection > 0.55) this._setMood('cariñosa');
        }
    }

    _press(hit) {
        let slot = this.pokes.findIndex(poke => !poke);
        if (slot < 0) slot = this.pokes.reduce((best, poke, i) => (Math.abs(poke.x) < Math.abs(this.pokes[best].x) ? i : best), 0);
        const rig = this.rig;
        // The skin's outward direction at the touch: from the middle of her head through it, level.
        // With the slope of the cheek in it, the finger pushed the skin up into the lower lid and
        // a white crescent of eye showed under it (IRIS, 2026-11-27).
        const center = fromFrame(rig.frame, ...rig.frame.center);
        const out = hit.bind.clone().sub(center);
        out.addScaledVector(rig.frame.up, -out.dot(rig.frame.up));
        const normal = (out.lengthSq() > 0 ? out.normalize() : out.copy(rig.frame.forward)).lerp(rig.frame.forward, 0.35).normalize();
        this.pokes[slot] = { center: hit.bind.clone(), normal, x: 0, v: 0, target: rig.shape.pokeDepth, holdUntil: this.now() + 0.11, zone: hit.zone };
        this._startle();
        return slot;
    }

    /**
     * A finger on her cheek wakes her from petting: the «cariñosa» that touch gave her ends (an
     * emotion the agent set is the agent's), most of the affection goes, and her eyes stay open
     * while the finger is on her and a moment after (Carlotta, whose «happy» shuts them).
     */
    _startle() {
        this.mood.startle();
        this._alertUntil = this.now() + ALERT_SECONDS;
        if (this._ownMood === 'cariñosa') this._setMood(null);
    }

    _releasePoke(slot, { quiet = false } = {}) {
        const poke = this.pokes[slot];
        if (!poke) return;
        poke.release = true;
        if (quiet) poke.holdUntil = 0;
    }

    _registerPoke(hit, { quiet = false } = {}) {
        const side = hit.zone === 'cheekL' ? 'flinchL' : hit.zone === 'cheekR' ? 'flinchR' : null;
        const annoys = this.mood.poke();
        if (side) this._pulse(side, 0.32, annoys ? 1 : 0.8);
        if (!quiet) {
            this._pulse('puff', 0.65, 1, 0.12);
            // The cheek springs back as the finger leaves: a soft pop.
            this.sounds?.poke?.(annoys ? 1 : 0.8);
        }
        // A small push: her head gives way to the finger.
        const sign = hit.local.x > 0 ? -1 : 1;
        this.lean.yaw[1] += sign * 0.9;
        this.lean.pitch[1] -= 0.3;
        if (annoys) {
            const avatar = this.rig?.avatar;
            avatar?.pulseExpression?.('squeeze', 0.7);
            avatar?.pulseExpression?.('markAnger', 1.8);
            this._turnAway = sign;
            this._setMood('enojada');
        }
    }

    _startPull(hit, both) {
        const rig = this.rig;
        const pulls = [];
        const make = (grab) => ({ grab, x: new THREE.Vector3(), v: new THREE.Vector3(), target: new THREE.Vector3() });
        this.pulls[0] = make(hit.bind.clone());
        pulls.push(this.pulls[0]);
        this.pulls[1] = null;
        if (both) {
            const l = hit.local;
            this.pulls[1] = make(fromFrame(rig.frame, -l.x, l.y, l.z));
            pulls.push(this.pulls[1]);
        }
        this._startle();
        const grabWorld = rig.bindToWorld(hit.bind);
        this._pullPlane = new THREE.Plane().setFromNormalAndCoplanarPoint(this.camera.getWorldDirection(new THREE.Vector3()).negate(), grabWorld);
        this._pullFrom = grabWorld;
        return pulls;
    }

    _setCursor(cursor) {
        if (this.canvas?.style && this._cursor !== cursor) {
            this.canvas.style.cursor = cursor;
            this._cursor = cursor;
        }
    }

    _pulse(name, seconds, level = 1, delay = 0) {
        this._pulses = this._pulses.filter(p => p.name !== name);
        this._pulses.push({ name, t: -delay, seconds, level });
    }

    _setMood(emotion) {
        if (this._ownMood === emotion) return;
        this._ownMood = emotion;
        try { this.onMood(emotion); } catch (error) { console.warn('[touch] onMood:', error); }
    }

    /** The frame's gesture work, before the face is composed. */
    _step(dt) {
        const rig = this.rig, gesture = this.gesture;
        const now = this.now();
        let petting = false;
        if (gesture?.kind === 'pet') {
            const event = gesture.pending;
            gesture.pending = null;
            if (event) {
                const hit = this._pickNear(rig, event.clientX, event.clientY);
                gesture.onHead = Boolean(hit && (hit.zone === 'top' || hit.zone === 'head'));
                if (hit) gesture.hit = hit;
            } else if (gesture.onHead === undefined) gesture.onHead = true;
            gesture.speed *= Math.exp(-dt * 3);
            petting = gesture.onHead && now - gesture.since > PET_DELAY;
            if (petting) this.mood.pet(dt, clamp(gesture.speed / 600));
        }
        if (gesture?.kind === 'pull' && gesture.pending) {
            const point = this._ray(gesture.pending).intersectPlane(this._pullPlane, new THREE.Vector3());
            gesture.pending = null;
            if (point) {
                const deltaWorld = point.sub(this._pullFrom);
                const delta = rig.worldDirToBind(deltaWorld.clone().normalize()).multiplyScalar(deltaWorld.length())
                    .multiplyScalar(1 / rig.bindToWorld(fromFrame(rig.frame, 1, 0, 0)).distanceTo(rig.bindToWorld(rig.frame.eyeMid)));
                // In eye distances, at most pullMax, and the cheek comes forward with it.
                if (delta.length() > TOUCH_SHAPE.pullMax) delta.setLength(TOUCH_SHAPE.pullMax);
                const lift = rig.frame.forward.clone().multiplyScalar(delta.length() * TOUCH_SHAPE.pullLift);
                const local = delta.clone().add(lift);
                this.pulls[0]?.target.copy(local);
                // The other hand mirrors it across her face.
                if (this.pulls[1]) {
                    const across = local.dot(rig.frame.right);
                    this.pulls[1].target.copy(local).addScaledVector(rig.frame.right, -2 * across);
                }
            }
        }
        // Springs: pokes (held, then let go to bounce) and pulls.
        this.pokes.forEach((poke, i) => {
            if (!poke) return;
            const pressed = !(poke.release && now >= poke.holdUntil);
            const spring = pressed ? PRESS_SPRING : TOUCH_SPRING;
            [poke.x, poke.v] = springStep(poke.x, poke.v, pressed ? poke.target : 0, dt, spring.omega, spring.decay);
            if (poke.release && now >= poke.holdUntil && Math.abs(poke.x) < 1e-4 && Math.abs(poke.v) < 1e-3) this.pokes[i] = null;
        });
        let pulling = 0;
        this.pulls.forEach((pull, i) => {
            if (!pull) return;
            for (const axis of ['x', 'y', 'z']) [pull.x[axis], pull.v[axis]] = springStep(pull.x[axis], pull.v[axis], pull.target[axis], dt);
            pulling = Math.max(pulling, pull.x.length() / TOUCH_SHAPE.pullMax);
            const held = this.gesture?.kind === 'pull' && this.gesture.pulls?.includes(pull);
            if (!held && pull.target.lengthSq() === 0 && pull.x.lengthSq() < 1e-8 && pull.v.lengthSq() < 1e-6) this.pulls[i] = null;
        });
        // A long pull counts as poking her.
        if (this.gesture?.kind === 'pull') {
            this.gesture.held = (this.gesture.held ?? 0) + dt;
            if (this.gesture.held > 1.2) { this.gesture.held = 0; this._registerPoke(this.gesture.hit, { quiet: true }); }
        }
        this.mood.update(dt);
        if (this._ownMood === 'enojada' && this.mood.annoyed < 0.15) this._setMood(this.mood.affection > 0.4 ? 'cariñosa' : null);
        if (this._ownMood === 'cariñosa' && this.mood.affection < 0.2 && !petting) this._setMood(null);

        const ease = (value, target, rise, fall) => value + (target - value) * (1 - Math.exp(-dt * (target > value ? rise : fall)));
        this.levels.closed = ease(this.levels.closed, petting ? 1 : 0, 7, 5);
        const fingerOn = this.gesture?.kind === 'press' || this.gesture?.kind === 'pull' || now < this._alertUntil;
        this.levels.alert = ease(this.levels.alert, fingerOn ? 1 : 0, 18, 3.5);
        this.levels.pull = ease(this.levels.pull, pulling, 10, 6);
        const side = this.gesture?.kind === 'pull' ? (this.gesture.hit.zone === 'cheekL' ? 'flinchL' : this.gesture.hit.zone === 'cheekR' ? 'flinchR' : null) : null;
        for (const name of ['flinchL', 'flinchR', 'puff']) this.levels[name] = 0;
        if (side) this.levels[side] = 0.6 * clamp(pulling * 2);
        this._pulses = this._pulses.filter(pulse => {
            pulse.t += dt;
            if (pulse.t < 0) return true;
            const u = pulse.t / pulse.seconds;
            if (u >= 1) return false;
            const level = (u < 0.25 ? u / 0.25 : 1 - (u - 0.25) / 0.75) * pulse.level;
            this.levels[pulse.name] = Math.max(this.levels[pulse.name] ?? 0, level);
            return true;
        });

        // Her head: leans into the petting hand, turns away when annoyed.
        let roll = 0, pitch = 0, yaw = 0;
        if (petting) {
            const x = clamp(gesture.hit?.local?.x ?? 0, -1.5, 1.5);
            const sway = Math.sin(now * 2.4) * 0.025 * clamp(gesture.speed / 500);
            roll = (0.07 + 0.05 * Math.abs(x)) * (x >= 0 ? 1 : -1) * this.levels.closed + sway;
            pitch = 0.06 * this.levels.closed;
        }
        const a = this.mood.annoyed;
        if (a > 0) {
            yaw += (this._turnAway ?? 1) * 0.2 * smoothstep(0.2, 0.8, a);
            pitch -= 0.06 * smoothstep(0.3, 0.9, a);
        }
        for (const [name, target] of [['roll', roll], ['pitch', pitch], ['yaw', yaw]]) {
            const state = this.lean[name];
            [state[0], state[1]] = springStep(state[0], state[1], target, dt, ...LEAN_SPRING);
        }
    }

    _busy() {
        return Boolean(this.gesture) || this.pokes.some(Boolean) || this.pulls.some(Boolean) || this._pulses.length > 0
            || !this.mood.calm || this.levels.closed > 1e-3 || this.levels.pull > 1e-3 || this.levels.alert > 1e-3
            || Object.values(this.lean).some(([x, v]) => Math.abs(x) > 1e-4 || Math.abs(v) > 1e-3);
    }

    /** Called by the avatar just before vrm.update. */
    _apply(avatar, dt) {
        const rig = this.rig;
        if (!rig || rig.avatar !== avatar || !(dt >= 0)) return;
        // Hover cursor in «Mimos» mode: a hand over her head, a pointer over her face; once a frame.
        if (this._hover) {
            if (this.active && !this.gesture) {
                const hit = rig.pick(this._ray(this._hover));
                this._setCursor(!hit || hit.zone === 'head' ? '' : hit.zone === 'top' ? 'grab' : 'pointer');
            }
            this._hover = null;
        }
        if (!this._busy()) {
            if (this._dirty || this.debug) { rig.writeUniforms({ pokes: this.pokes, pulls: this.pulls, puff: 0, blush: 0, debug: this.debug }); this._dirty = false; }
            return;
        }
        this._dirty = true;
        this._step(Math.min(dt, 0.05));
        const em = avatar.vrm?.expressionManager;
        const speaking = Math.max(...['aa', 'ih', 'ou', 'ee', 'oh'].map(name => em?.getValue?.(name) || 0));
        const face = touchFace({ ...this.levels, affection: this.mood.affection, annoyed: this.mood.annoyed, speaking },
            name => rig.has(name), name => rig.closure(name));
        if (em) this._writeFace(em, face);
        rig.writeUniforms({ pokes: this.pokes, pulls: this.pulls, puff: face.shader.puff, blush: face.shader.blush, debug: this.debug });
        this._leanHead(avatar);
    }

    _writeFace(em, face) {
        this._saved.clear();
        const save = (name, value) => { if (!this._saved.has(name)) this._saved.set(name, value); };
        for (const [name, factor] of Object.entries(face.scale)) {
            const value = em.getValue(name);
            if (!Number.isFinite(value) || factor >= 1) continue;
            save(name, value);
            em.setValue(name, value * factor);
        }
        for (const [name, weight] of Object.entries(face.set)) {
            const value = em.getValue(name);
            if (!Number.isFinite(value) || value >= weight) continue;
            save(name, value);
            em.setValue(name, weight);
        }
    }

    _leanHead(avatar) {
        const [roll] = this.lean.roll, [pitch] = this.lean.pitch, [yaw] = this.lean.yaw;
        if (Math.abs(roll) + Math.abs(pitch) + Math.abs(yaw) < 1e-4) return;
        const head = avatar.vrm?.humanoid?.getNormalizedBoneNode?.('head');
        if (!head?.parent) return;
        const f = this.rig.worldFrame();
        // Rotations in the world: roll moves the top of her head to her left (+), pitch tips her
        // face down (+), yaw turns her face to her left (+).
        _d.setFromAxisAngle(_v.crossVectors(f.up, f.right).normalize(), roll);
        _d.multiply(_q.setFromAxisAngle(_v.crossVectors(f.up, f.forward).normalize(), pitch));
        _d.multiply(_q.setFromAxisAngle(_v.crossVectors(f.forward, f.right).normalize(), yaw));
        this._headRestore = { head, rotation: head.quaternion.clone() };
        head.getWorldQuaternion(_q);
        head.parent.getWorldQuaternion(_p).invert();
        head.quaternion.copy(_p.multiply(_d).multiply(_q));
    }

    /** Called by the avatar right after vrm.update: every value goes back to its owner. */
    _restore() {
        const em = this.rig?.avatar?.vrm?.expressionManager;
        if (em) for (const [name, value] of this._saved) em.setValue(name, value);
        this._saved.clear();
        if (this._headRestore) {
            this._headRestore.head.quaternion.copy(this._headRestore.rotation);
            this._headRestore = null;
        }
    }

    /**
     * The «Mimos» close-up: blends the camera from where the page put it this frame to a view of
     * her face, and back when the mode ends. Call after the page's own camera update.
     */
    updateCamera(dt, { reducedMotion = false } = {}) {
        const camera = this.camera, rig = this.rig;
        const target = this.active && rig ? 1 : 0;
        const step = reducedMotion ? 1 : dt / CLOSE_UP_SECONDS;
        this.closeUp.blend = clamp(this.closeUp.blend + (target ? step : -step));
        if (!camera || !rig || this.closeUp.blend <= 0) { if (!target) this.closeUp.center = null; return false; }
        const f = rig.worldFrame();
        // Follow her face smoothly: her breathing and small head moves should not shake the view.
        const k = 1 - Math.exp(-dt / 0.35);
        this.closeUp.center ??= f.center.clone();
        this.closeUp.forward ??= f.forward.clone();
        this.closeUp.center.lerp(f.center, k);
        const flat = f.forward.clone().setY(f.forward.y * 0.3).normalize();
        this.closeUp.forward.lerp(flat, k).normalize();
        const fov = THREE.MathUtils.degToRad(camera.fov) / 2;
        const zoom = camera.zoom || 1;
        // Her head and a little of her shoulders: about six eye distances from top to bottom.
        const distance = (f.E * CLOSE_UP_HEIGHT / 2) * zoom / Math.tan(fov);
        const position = this.closeUp.center.clone().addScaledVector(this.closeUp.forward, distance).addScaledVector(f.up, f.E * 0.25);
        const look = new THREE.Matrix4().lookAt(position, this.closeUp.center, new THREE.Vector3(0, 1, 0));
        const want = new THREE.Quaternion().setFromRotationMatrix(look);
        const b = this.closeUp.blend, eased = b * b * (3 - 2 * b);
        camera.position.lerp(position, eased);
        camera.quaternion.slerp(want, eased);
        camera.updateMatrixWorld();
        return true;
    }
}
