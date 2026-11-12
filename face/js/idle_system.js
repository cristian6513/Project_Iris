/**
 * IRIS Idle System — Natural resting animations
 *
 * Two layers:
 *  • Root-group motion (position / rotation of the whole avatar)
 *  • VRM bone-level motion (head, neck, chest, spine, shoulders, arms, hands)
 *
 * When a VRM avatar is loaded, bone-level channels drive subtle anime-style
 * gestures (breathing, head sway, looking around, thinking pose, waving,
 * hair-touch, stretch). For procedural / static GLB fallback, the same
 * logical poses are applied as group rotations.
 */

import * as THREE from 'three';
import { VRMAnimationPlayer } from './vrm_animation_loader.js?v=10';

// Idle pose definitions.
//
// `tier` classifies each pose by what rig it needs to look good:
//   • 'safe'   → head/neck/spine only; works on any rig, no clipping risk
//   • 'risky'  → moves arms or legs; procedural fallback may clip cloth
//                against the body on models without skirt/jacket bones.
//                Hidden from the menu and skipped in random rotation
//                UNLESS a VRMA clip exists for the pose.
//   • 'custom' → always shown; defined purely in our code, no rig assumption
//
// `label` is the Spanish UI label for the pose menu button.
// Room units per second each walk clip covers at speed 1 on the default avatar: `walk` 0.75 m/s,
// `walk_2` (Tripo's catwalk, rebuilt as a room cycle) 0.58 m/s.
const WALK_CLIP_SPEEDS = Object.freeze({ walk: 1.0945, walk_2: 0.847 });
const WALK_CLIP_SPEED = WALK_CLIP_SPEEDS.walk;

// Seats with clips of their own: `trigger` sits her into the loop, `standUp` plays her out.
// `drape` is how far the skirt rests over her thighs: while held, and at [second, weight]
// through each clip, following the thighs as they rise and fall. `joins` takes her here
// straight from another seat, by a clip from second `at`, with no need to get up first.
// `prop` is what she holds: the clips it comes `in` and goes `out` with and its `loop`, the
// second she `take`s and `release`s it, and how it `open`s and `close`s.
// `reads` is the seat she reads in from here. A reading seat rests on its `base`: she gets
// there `then` opens the book, and `closes` it before she stands.
const SOFA_DRAPE = Object.freeze({ held: 1, sit: [[0.5, 0], [1.4, 1]], stand: [[0.5, 1], [1.3, 0]] });
const BED_EDGE_DRAPE = Object.freeze({ held: 1, sit: [[0.7, 0], [1.6, 1]], stand: [[0.3, 1], [1.2, 0]] });
const READ_SEATED = Object.freeze({
    id: 'conjured', take: 2.23, open: [[2.3, 0.516], [3.03, 1]], close: [[0.245, 1], [0.92, 0.516]], release: 1.04,
});
// The chair rolls back to let her in and she rides it to the desk: 1 out, 0 home. Same frames
// as ROLL_IN and ROLL_BACK in build_chair.py, so she never slides on the seat.
const DESK_CHAIR = Object.freeze({ sit: [[2.567, 1], [3.967, 0]], stand: [[0.167, 0], [1.5, 1]], low: 1.3 });
const DESK_DRAPE = Object.freeze({ held: 1, sit: [[0.5, 0], [1.4, 1]], stand: [[1.9, 1], [2.7, 0]] });
const SEATED_HEAD_GESTURES = Object.freeze({
    nod: 'nod', subtle_nod: 'nod', confirm: 'nod', shake: 'shake', think: 'tilt', head_tilt: 'tilt', confused: 'tilt',
});
const STANDING_DRAPE = Object.freeze({ held: 0, sit: [[0, 0]], stand: [[0, 0]] });
const USE_TABLET = Object.freeze({ id: 'tablet', take: 1.37, open: [[0, 0]], close: [[0, 0]], release: 1.17 });
const BED_LYING_DRAPE = Object.freeze({
    held: 0,
    sit: [[0.7, 0], [1.6, 1], [3.0, 1], [4.2, 0]],
    stand: [[0.7, 0], [1.7, 1], [2.8, 1], [3.5, 0]],
});
const BED_LYING_LIES = Object.freeze({ held: 1, sit: [[2.6, 0], [4.6, 1]], stand: [[0.4, 1], [2.0, 0]] });
// From how far she lies back a PMX avatar's hair is held to her head (pmx_physics.js holdHair).
const HAIR_HOLD_FROM = 0.02;
// Rolling back from face down, wherever her feet are in the loop: they come down to where the roll
// starts over this long (s), the stretch where her hips have not begun to turn. 0.2 s flicked
// her shins through some 85 degrees.
const PRONE_EXIT_FADE = 0.6;

const SEATS = Object.freeze({
    sofa_seated: Object.freeze({
        sit: 'sofa_sit_down',
        stand: 'sofa_stand_up',
        reads: 'sofa_reading',
        joins: Object.freeze({
            sofa_reading: Object.freeze({ clip: 'sofa_read_close', at: 0, drape: [[0, 1]] }),
            sofa_tablet: Object.freeze({ clip: 'sofa_tablet_close', at: 0, drape: [[0, 1]] }),
        }),
        drape: SOFA_DRAPE,
    }),
    sofa_reading: Object.freeze({
        sit: 'sofa_sit_down',
        then: 'sofa_read_open',
        closes: 'sofa_read_close',
        stand: 'sofa_stand_up',
        base: 'sofa_seated',
        joins: Object.freeze({ sofa_seated: Object.freeze({ clip: 'sofa_read_open', at: 0 }) }),
        drape: SOFA_DRAPE,
        prop: Object.freeze({ ...READ_SEATED, in: 'sofa_read_open', loop: 'sofa_read_idle', out: 'sofa_read_close' }),
    }),
    desk_seated: Object.freeze({
        sit: 'desk_sit_down',
        stand: 'desk_stand_up',
        joins: Object.freeze({ desk_typing: Object.freeze({ clip: 'desk_type_close', at: 0, drape: [[0, 1]] }) }),
        drape: DESK_DRAPE,
        chair: DESK_CHAIR,
    }),
    desk_typing: Object.freeze({
        sit: 'desk_sit_down',
        then: 'desk_type_open',
        closes: 'desk_type_close',
        stand: 'desk_stand_up',
        base: 'desk_seated',
        joins: Object.freeze({ desk_seated: Object.freeze({ clip: 'desk_type_open', at: 0 }) }),
        drape: DESK_DRAPE,
        chair: DESK_CHAIR,
        touch: Object.freeze({ in: 'desk_type_open', loop: 'desk_type_idle', out: 'desk_type_close' }),
    }),
    // Standing, like the shelf's book: one clip in and one out, nothing to sit on.
    stand_reading: Object.freeze({
        sit: 'stand_read_open',
        stand: 'stand_read_close',
        drape: STANDING_DRAPE,
        prop: Object.freeze({ ...READ_SEATED, in: 'stand_read_open', loop: 'stand_read_idle', out: 'stand_read_close' }),
    }),
    // Her back to the room and a hand on the glass.
    window_looking: Object.freeze({
        sit: 'window_lean_in',
        stand: 'window_lean_out',
        drape: STANDING_DRAPE,
        touch: Object.freeze({ in: 'window_lean_in', loop: 'window_look_idle', out: 'window_lean_out' }),
    }),
    stand_tablet: Object.freeze({
        sit: 'stand_tablet_open',
        stand: 'stand_tablet_close',
        drape: STANDING_DRAPE,
        prop: Object.freeze({ ...USE_TABLET, in: 'stand_tablet_open', loop: 'stand_tablet_idle', out: 'stand_tablet_close' }),
    }),
    sofa_tablet: Object.freeze({
        sit: 'sofa_sit_down',
        then: 'sofa_tablet_open',
        closes: 'sofa_tablet_close',
        stand: 'sofa_stand_up',
        base: 'sofa_seated',
        joins: Object.freeze({ sofa_seated: Object.freeze({ clip: 'sofa_tablet_open', at: 0 }) }),
        drape: SOFA_DRAPE,
        prop: Object.freeze({ ...USE_TABLET, in: 'sofa_tablet_open', loop: 'sofa_tablet_idle', out: 'sofa_tablet_close' }),
    }),
    bed_tablet: Object.freeze({
        sit: 'bed_sit_down',
        then: 'bed_tablet_open',
        closes: 'bed_tablet_close',
        stand: 'bed_stand_up',
        base: 'bed_seated',
        joins: Object.freeze({ bed_seated: Object.freeze({ clip: 'bed_tablet_open', at: 0 }) }),
        drape: BED_EDGE_DRAPE,
        prop: Object.freeze({ ...USE_TABLET, in: 'bed_tablet_open', loop: 'bed_tablet_idle', out: 'bed_tablet_close' }),
    }),
    bed_reading: Object.freeze({
        sit: 'bed_sit_down',
        then: 'bed_read_open',
        closes: 'bed_read_close',
        stand: 'bed_stand_up',
        base: 'bed_seated',
        joins: Object.freeze({ bed_seated: Object.freeze({ clip: 'bed_read_open', at: 0 }) }),
        drape: BED_EDGE_DRAPE,
        prop: Object.freeze({ ...READ_SEATED, in: 'bed_read_open', loop: 'bed_read_idle', out: 'bed_read_close' }),
    }),
    book_reading: Object.freeze({
        sit: 'book_take',
        stand: 'book_return',
        drape: Object.freeze({ held: 0, sit: [[0, 0]], stand: [[0, 0]] }),
        prop: Object.freeze({
            id: 'shelf', in: 'book_take', loop: 'book_read_idle', out: 'book_return',
            take: 1.17, open: [[2.43, 0], [3.17, 1]], close: [[0.12, 1], [0.8, 0]], release: 1.9,
        }),
    }),
    bed_seated: Object.freeze({
        sit: 'bed_sit_down',
        stand: 'bed_stand_up',
        reads: 'bed_reading',
        joins: Object.freeze({
            bed_lying: Object.freeze({ clip: 'bed_sit_up', at: 0, drape: [[0.7, 0], [1.7, 1]], lies: [[0.4, 1], [2.0, 0]] }),
            bed_reading: Object.freeze({ clip: 'bed_read_close', at: 0, drape: [[0, 1]] }),
            bed_tablet: Object.freeze({ clip: 'bed_tablet_close', at: 0, drape: [[0, 1]] }),
        }),
        drape: BED_EDGE_DRAPE,
    }),
    bed_lying: Object.freeze({
        sit: 'bed_lie_down',
        stand: 'bed_get_up',
        joins: Object.freeze({
            bed_seated: Object.freeze({ clip: 'bed_lie_down', at: 2.3 }),
            bed_prone: Object.freeze({ clip: 'bed_roll_supine', at: 0, drape: [[0, 0]], lies: [[0, 1]], fade: PRONE_EXIT_FADE }),
        }),
        drape: BED_LYING_DRAPE,
        // How far her back rests on the bed, as her hips tip back from upright to flat
        // (LyingHips in lying_hips.js). Same shape as `drape`. `rests`: on what, once held.
        lies: BED_LYING_LIES,
        rests: 'back',
    }),
    // Face down, her feet swinging (Tripo's two takes in one loop). She lies down on her back
    // first and rolls over (`then`), and rolls back before she gets up (`closes`).
    bed_prone: Object.freeze({
        sit: 'bed_lie_down',
        then: 'bed_roll_prone',
        closes: 'bed_roll_supine',
        closesFade: PRONE_EXIT_FADE,
        stand: 'bed_get_up',
        base: 'bed_lying',
        joins: Object.freeze({ bed_lying: Object.freeze({ clip: 'bed_roll_prone', at: 0, drape: [[0, 0]], lies: [[0, 1]] }) }),
        drape: BED_LYING_DRAPE,
        lies: BED_LYING_LIES,
        rests: 'front',
    }),
    // On the edge of the desk, swinging her legs (Tripo's take). No clip takes her up there:
    // she comes and goes in a line of light (`teleport`, scene_actions.js). Her feet hang, so
    // her hips keep IRIS' height on the desk whatever the avatar (`lies`).
    desk_edge: Object.freeze({
        teleport: true,
        drape: Object.freeze({ held: 1, sit: [[0, 1]], stand: [[0, 1]] }),
        lies: Object.freeze({ held: 1, sit: [[0, 1]], stand: [[0, 1]] }),
    }),
});

/** The value at `time` of a [second, value] curve, easing between its points. */
function curveAt(points, time) {
    let [from, value] = points[0];
    for (const [to, next] of points.slice(1)) {
        if (time < to) return value + (next - value) * THREE.MathUtils.smoothstep(time, from, to);
        [from, value] = [to, next];
    }
    return value;
}

const IDLE_POSES = [
    // Authored Iris library; the manifest chooses visible and ambient poses.
    { name: 'idle', tier: 'risky', weight: 0, duration: 3000, continuous: false, label: 'Reposo base' },
    { name: 'idle_casual', tier: 'risky', weight: 12, duration: 3000, continuous: false, label: 'Reposo' },
    { name: 'idle_lazy', tier: 'risky', weight: 3, duration: 3000, continuous: false, label: 'Descansar' },
    { name: 'wave', tier: 'risky', weight: 0, duration: 3000, continuous: false, label: 'Saludar' },
    { name: 'wave_big', tier: 'risky', weight: 0, duration: 3000, continuous: false, label: 'Saludo amplio' },
    { name: 'nod', tier: 'risky', weight: 0, duration: 3000, continuous: false, label: 'Asentir' },
    { name: 'shake', tier: 'risky', weight: 0, duration: 3000, continuous: false, label: 'Negar' },
    { name: 'think', tier: 'risky', weight: 0, duration: 3000, continuous: false, label: 'Pensar' },
    { name: 'listen', tier: 'risky', weight: 0, duration: 3000, continuous: false, label: 'Escuchar' },
    { name: 'talk', tier: 'risky', weight: 0, duration: 3000, continuous: false, label: 'Conversar' },
    { name: 'confirm', tier: 'risky', weight: 0, duration: 3000, continuous: false, label: 'Confirmar' },
    { name: 'confused', tier: 'risky', weight: 0, duration: 3000, continuous: false, label: 'No entendí' },
    { name: 'celebrate', tier: 'risky', weight: 0, duration: 3000, continuous: false, label: 'Celebrar' },
    { name: 'angry', tier: 'risky', weight: 0, duration: 3000, continuous: false, label: 'Enojo' },
    { name: 'surprised', tier: 'risky', weight: 0, duration: 3000, continuous: false, label: 'Sorpresa' },
    { name: 'attention', tier: 'risky', weight: 0, duration: 3000, continuous: false, label: 'Llamar la atención' },
    { name: 'point', tier: 'risky', weight: 0, duration: 3000, continuous: false, label: 'Señalar' },
    { name: 'stretch', tier: 'risky', weight: 1, duration: 3000, continuous: false, label: 'Estirarse' },
    // Added in November 2026 from Tripo motions. Several gestures above also play variants
    // (manifest `variants`); weight is only for the rest clips she picks on her own.
    { name: 'idle_yawn', tier: 'risky', weight: 2, duration: 3000, continuous: false, label: 'Bostezar' },
    { name: 'hair_touch', tier: 'risky', weight: 3, duration: 3000, continuous: false, label: 'Arreglarse el pelo' },
    { name: 'idle_waiting', tier: 'risky', weight: 1, duration: 3000, continuous: false, label: 'Esperar' },
    { name: 'sad', tier: 'risky', weight: 0, duration: 3000, continuous: false, label: 'Triste' },
    { name: 'laugh', tier: 'risky', weight: 0, duration: 3000, continuous: false, label: 'Reír' },
    { name: 'shy', tier: 'risky', weight: 0, duration: 3000, continuous: false, label: 'Tímida' },
    { name: 'scared', tier: 'risky', weight: 0, duration: 3000, continuous: false, label: 'Asustada' },
    { name: 'clap', tier: 'risky', weight: 0, duration: 3000, continuous: false, label: 'Aplaudir' },
    { name: 'heart', tier: 'risky', weight: 0, duration: 3000, continuous: false, label: 'Corazón' },
    { name: 'apologize', tier: 'risky', weight: 0, duration: 3000, continuous: false, label: 'Disculparse' },
    { name: 'shrug', tier: 'risky', weight: 0, duration: 3000, continuous: false, label: 'No sé' },
    { name: 'idea', tier: 'risky', weight: 0, duration: 3000, continuous: false, label: '¡Ya sé!' },
    { name: 'annoyed', tier: 'risky', weight: 0, duration: 3000, continuous: false, label: 'No me molestes' },
    { name: 'wave_cute', tier: 'risky', weight: 0, duration: 3000, continuous: false, label: 'Saludo adorable' },
    { name: 'wave_flirty', tier: 'risky', weight: 0, duration: 3000, continuous: false, label: 'Saludo coqueto' },
    { name: 'dance', tier: 'risky', weight: 0, duration: 3000, continuous: false, label: 'Bailar' },
    { name: 'dance_sexy', tier: 'risky', weight: 0, duration: 3000, continuous: false, label: 'Baile sexy' },
    { name: 'jump', tier: 'risky', weight: 0, duration: 3000, continuous: false, label: 'Salto de alegría' },
    // Continuous background animations (not selectable)
    { name: 'breathe',       tier: 'safe',  weight: 0,  duration: 0,    continuous: true,  label: null },
    { name: 'blink',         tier: 'safe',  weight: 0,  duration: 0,    continuous: true,  label: null },

    // ── Safe: only head / neck / spine ─────────────────────
    { name: 'micro_head',    tier: 'safe',  weight: 22, duration: 1800, continuous: false, label: 'Mover cabeza' },
    { name: 'look_around',   tier: 'safe',  weight: 12, duration: 2500, continuous: false, label: 'Mirar alrededor' },
    // weight_shift ('Inclinar') — disabled: tilted too aggressively on
    // tall rigs, user reported it looked off. weight=0 drops it from
    // random rotation; label=null hides it from the pose selector menu.
    { name: 'weight_shift',  tier: 'safe',  weight: 0,  duration: 1400, continuous: false, label: null },
    { name: 'head_tilt',     tier: 'safe',  weight: 10, duration: 2000, continuous: false, label: 'Ladear cabeza' },
    { name: 'look_window',   tier: 'safe',  weight: 4,  duration: 4000, continuous: false, label: 'Mirar ventana' },
    { name: 'look_down',     tier: 'safe',  weight: 7,  duration: 1800, continuous: false, label: 'Mirar abajo' },
    { name: 'subtle_nod',    tier: 'safe',  weight: 6,  duration: 900,  continuous: false, label: 'Asentir' },
    { name: 'shy_look_away', tier: 'safe',  weight: 3,  duration: 2400, continuous: false, label: 'Tímida' },

    // ── Risky: move arms or legs, need VRMA (or proper cloth rig) ────
    { name: 'pc_focus_idle', tier: 'safe',  weight: 0,  duration: 18000, continuous: false, label: null },
    { name: 'pc_seated_idle', tier: 'safe',  weight: 0, duration: 86400000, continuous: false, label: null },
    { name: 'sofa_seated',   tier: 'custom', weight: 0, duration: Infinity, continuous: false, label: null },
    { name: 'bed_lying',     tier: 'custom', weight: 0, duration: Infinity, continuous: false, label: null },
    { name: 'bed_prone',     tier: 'custom', weight: 0, duration: Infinity, continuous: false, label: null },
    { name: 'bed_seated',   tier: 'custom', weight: 0, duration: Infinity, continuous: false, label: null },
    { name: 'book_reading',  tier: 'custom', weight: 0, duration: Infinity, continuous: false, label: null },
    { name: 'sofa_reading',  tier: 'custom', weight: 0, duration: Infinity, continuous: false, label: null },
    { name: 'bed_reading',   tier: 'custom', weight: 0, duration: Infinity, continuous: false, label: null },
    { name: 'sofa_tablet',   tier: 'custom', weight: 0, duration: Infinity, continuous: false, label: null },
    { name: 'bed_tablet',    tier: 'custom', weight: 0, duration: Infinity, continuous: false, label: null },
    { name: 'desk_seated',   tier: 'custom', weight: 0, duration: Infinity, continuous: false, label: null },
    { name: 'desk_typing',   tier: 'custom', weight: 0, duration: Infinity, continuous: false, label: null },
    { name: 'stand_reading', tier: 'custom', weight: 0, duration: Infinity, continuous: false, label: null },
    { name: 'stand_tablet',  tier: 'custom', weight: 0, duration: Infinity, continuous: false, label: null },
    { name: 'window_looking', tier: 'custom', weight: 0, duration: Infinity, continuous: false, label: null },
    { name: 'desk_edge',     tier: 'custom', weight: 0, duration: Infinity, continuous: false, label: null },
    { name: 'thinking_pose', tier: 'risky', weight: 6,  duration: 3200, continuous: false, label: 'Pensar' },
    { name: 'stretch_arms',  tier: 'risky', weight: 3,  duration: 2800, continuous: false, label: 'Estirar' },
    { name: 'wave_hand',     tier: 'risky', weight: 3,  duration: 2200, continuous: false, label: 'Saludar' },
    { name: 'sit_chair',     tier: 'risky', weight: 2,  duration: 6000, continuous: false, label: 'Sentarse' },
    { name: 'lie_bed',       tier: 'risky', weight: 1,  duration: 8000, continuous: false, label: 'Acostarse' },

    // ── Wonderful VRMA Set (4 idol-style 3-second poses) ───────────
    // Each gets its own selector button; they also feed the idle pool.
    { name: 'pose_smile',    tier: 'risky', weight: 0,  duration: 3000, continuous: false, label: '😊 Sonreír' },
    { name: 'pose_love',     tier: 'risky', weight: 0,  duration: 3000, continuous: false, label: '💖 Adorable' },
    { name: 'pose_noble',    tier: 'risky', weight: 0,  duration: 3000, continuous: false, label: '✨ Elegante' },
    { name: 'pose_weave',    tier: 'risky', weight: 0,  duration: 3000, continuous: false, label: '🤝 Entrelazar' },

    // ── Custom: scene interactions, shown and played only with a VRMA clip ──
    // go_computer, go_bed and touch_books have no procedural version: a clip
    // plays where she stands, and room_hotspots.js / room_actions.js are what
    // take her to the furniture.
    { name: 'walk_tour',   tier: 'custom', weight: 0, duration: 6000, continuous: false, label: 'Caminar' },
    { name: 'greet',       tier: 'custom', weight: 0, duration: 2400, continuous: false, label: '👋 Saludar' },
    { name: 'go_computer', tier: 'custom', weight: 0, duration: 7000, continuous: false, label: '💻 Al computador' },
    { name: 'go_bed',      tier: 'custom', weight: 0, duration: 7500, continuous: false, label: '🛏️ A la cama' },
    { name: 'touch_books', tier: 'custom', weight: 0, duration: 6500, continuous: false, label: '📚 Tocar libros' },
];

// Bones we touch during idle; cached once, cleared on pose change.
// Legs are included so kneeling / sitting poses actually move the legs.
const HUMANOID_BONES = [
    'hips', 'spine', 'chest', 'upperChest', 'neck', 'head',
    'leftShoulder', 'leftUpperArm', 'leftLowerArm', 'leftHand',
    'rightShoulder', 'rightUpperArm', 'rightLowerArm', 'rightHand',
    'leftUpperLeg', 'leftLowerLeg', 'leftFoot',
    'rightUpperLeg', 'rightLowerLeg', 'rightFoot',
];

export class IdleSystem {
    /**
     * @param {object} avatarManager - AvatarManager instance
     * @param {object} options
     * @param {string} options.animationBaseUrl - Manifest/clip base for this surface.
     */
    constructor(avatarManager, { animationBaseUrl = '/face/animations' } = {}) {
        this.avatar = avatarManager;
        this._disposed = false;
        this._generation = 0;
        this._manifestAbort = new AbortController();
        this._vrmaInitPromise = null;
        this._reloadPromise = null;
        this._manifestGeneration = 0;
        this._manifestRevision = null;
        this._manifestRequest = null;
        this.animationBaseUrl = String(animationBaseUrl || '/face/animations').replace(/\/+$/, '');
        this.active = true;
        this.paused = false;

        // ── Continuous behavior parameters ──
        //
        // The "phase + style" pattern from neural-motion-matching,
        // adapted: instead of training a network to learn the mapping
        // from these knobs to motion, we hand-author how they modulate
        // the existing pose system. Cheaper, predictable, and gives us
        // 80% of the perceived "alive" effect.
        //
        //   energy:     0..1  rest ↔ alert. Drives gesture frequency
        //                     (high energy = shorter random intervals)
        //                     and pose duration (high = snappier).
        //   engagement: 0..1  daydreaming ↔ locked on user. Read by the
        //                     IK lookAt weight in app.js for gaze focus.
        //   mood:      -1..+1 sad ↔ happy. Biases pose selection — see
        //                     `_moodBiasFor()` — toward upbeat or
        //                     subdued gestures.
        //   fidget:     0..1  still ↔ restless. Reserved for future use
        //                     (will scale procedural micro-jitter once
        //                     we wire that into _updatePose).
        //
        // These are continuously updated by `feedUserEmotion()` (camera
        // emotion classifier) and `feedIrisEmotion()` (LLM response's
        // `emocion` field). All four default to neutral middle-ground
        // so unmounted/unused param sources don't drag the avatar in
        // any particular direction.
        this.params = {
            energy:     0.5,
            engagement: 0.6,
            mood:       0.0,
            fidget:     0.4,
        };
        // Decay-toward-default so a one-off emotional spike doesn't
        // get stuck at the extreme forever. Half-life ≈ 8 seconds.
        this._paramDecay = 0.087;  // per second toward defaults

        this.timer = 0;
        this.nextAction = this._randomInterval();

        this.currentPose = null;
        this._manualPoseActive = false;
        this.poseTimer = 0;
        this.poseDuration = 0;

        this._savedRotation = new THREE.Euler();
        this._savedPosition = new THREE.Vector3();

        // Smooth return-to-idle (root)
        this._returningToIdle = false;
        this._returnTimer = 0;
        this._returnDuration = 0.45;
        this._returnFromPos = new THREE.Vector3();
        this._returnFromRot = new THREE.Euler();

        // Bone state
        this._boneRefs = null;          // { head: Node, neck: Node, ... }
        this._boneRest = null;          // { head: Euler, ... } rest rotations
        this._boneTargets = null;       // { head: Euler target, ... }
        this._boneReturnFrom = null;    // snapshot at pose-end
        this._boneReturning = false;
        this._boneReturnTimer = 0;

        this._totalWeight = IDLE_POSES
            .filter(p => !p.continuous)
            .reduce((sum, p) => sum + p.weight, 0);

        // VRMA animation player (lazy-initialized when the VRM is ready)
        /** @type {VRMAnimationPlayer|null} */
        this._vrmaPlayer = null;
        /** @type {object|null} */
        this._vrmaManifest = null;
        this._vrmaInitAttempted = false;
        this._vrmaActive = false; // true while a VRMA clip is playing a pose

        // Pending "return to idle" callback. Tracks the listener we
        // attached to the mixer so a follow-up external clip can
        // cancel it cleanly before its predecessor's natural end.
        /** @type {Function|null} */
        this._vrmaFinishedHandler = null;
        this._pendingVrmaFinish = null;
        /** @type {string|null} */
        this._vrmaExternalClip = null;
        /** @type {string|null} */
        this._vrmaPoseManagedByMixer = null;
        this._animationTransitionRemaining = 0;
        this._animationOwnershipClaimed = false;
        this._activityState = 'idle';
        this._activityClip = null;
    }

    /** Body-only clips leave blinking and speech expressions available. */
    _setAnimationBlinkSuppressed(suppressed) {
        const next = Boolean(suppressed);
        const avatar = this.avatar?.avatar;
        if (!avatar) return;
        const clip = this._vrmaPlayer?.currentName;
        const bodyOnly = this._vrmaManifest?.body_only_clips?.includes(clip) === true;
        const ownsBlink = next && !bodyOnly;
        // Shared by Face and Companion, including automatic voice/ambient poses.
        avatar.poseFacialProfile = bodyOnly
            ? this._vrmaManifest?.facial_profiles?.[clip] || null : null;
        avatar.poseFaceSpeaking = this._activityState === 'speaking';
        if (this._animationOwnershipClaimed === next
            && avatar.animationOwnsBlink === ownsBlink
            && avatar.animationOwnsRootMotion === next) return;
        this._animationOwnershipClaimed = next;
        if (next) {
            avatar._headAnim = null;
            avatar.cancelIK?.();
        }
        avatar.setAnimationBlinkSuppressed?.(ownsBlink, next);
        // Custom avatars and MicroLife read the same independent flags.
        avatar.animationOwnsBlink = ownsBlink;
        avatar.animationOwnsRootMotion = next;
    }

    /** True while a pose/clip OR its hand-off back to idle owns motion. */
    isExplicitAnimationActive() {
        return Boolean(
            this._vrmaExternalClip
            || this.currentPose
            || this._returningToIdle
            || this._boneReturning
            || this._animationTransitionRemaining > 0
            || this._vrmaPlayer?.transitionRemaining > 0,
        );
    }

    _releaseAnimationOwnershipIfSettled() {
        if (!this.isExplicitAnimationActive()) {
            this._setAnimationBlinkSuppressed(false);
        }
    }

    /**
     * Try to initialize the VRMA player + load the animations manifest.
     * Called lazily the first time we have a VRM and bones available.
     * Failures are silent — we just fall back to procedural poses.
     */
    _initVRMAPlayer() {
        if (this._disposed) return Promise.resolve();
        if (this._vrmaInitPromise) return this._vrmaInitPromise;
        if (this._vrmaInitAttempted) return Promise.resolve();
        this._vrmaInitAttempted = true;
        this._vrmaInitPromise = this._loadInitialManifest(this._generation)
            .finally(() => { this._vrmaInitPromise = null; });
        return this._vrmaInitPromise;
    }

    async _loadInitialManifest(generation) {
        if (!this.avatar?.avatar?.vrm?.humanoid) { this._emitPosesChanged(); return; }
        const manifestGeneration = this._manifestGeneration;
        try {
            const res = await fetch(this.animationBaseUrl + '/manifest.json', { cache: 'no-store', signal: this._manifestAbort.signal });
            if (!res.ok) throw Error('manifest.json ' + res.status);
            const manifest = await res.json();
            if (this._disposed || generation !== this._generation || manifestGeneration !== this._manifestGeneration) return;
            await this.applyManifest(this._localSnapshot(manifest));
        } catch (error) {
            if (!this._disposed) console.info('[idle] Manifest unavailable; keeping the valid pose.', error);
        }
    }

    _localSnapshot(manifest) {
        const resources = Object.fromEntries(Object.entries(manifest.clips || {}).map(([name, filename]) => [name, {
            filename, revision: filename, format: filename.split('.').pop().toLowerCase(),
            url: this.animationBaseUrl + '/' + filename,
        }]));
        return { manifest, resources, revision: 'local:' + JSON.stringify(manifest) };
    }

    reloadManifest() {
        if (this._disposed) return Promise.resolve();
        if (this.refreshLibrary) return this.refreshLibrary();
        if (this._reloadPromise) return this._reloadPromise;
        this._reloadPromise = (async () => {
            if (this._vrmaInitPromise) return this._vrmaInitPromise;
            if (!this._vrmaInitAttempted) return this._initVRMAPlayer();
            return this._loadInitialManifest(this._generation);
        })().finally(() => { this._reloadPromise = null; });
        return this._reloadPromise;
    }

    applyManifest(snapshot, resolveAsset = null) {
        if (this._disposed || this._manifestRevision === snapshot.revision) return Promise.resolve({ failed: [] });
        if (this._manifestRequest?.revision === snapshot.revision) return this._manifestRequest.promise;
        this._manifestRevision = null;
        const generation = ++this._manifestGeneration;
        const promise = this._applyManifest(snapshot, resolveAsset, generation);
        const request = { revision: snapshot.revision, promise };
        this._manifestRequest = request;
        promise.finally(() => { if (this._manifestRequest === request) this._manifestRequest = null; }).catch(() => {});
        return promise;
    }

    async _applyManifest(snapshot, resolveAsset, generation) {
        const current = () => !this._disposed && generation === this._manifestGeneration;
        const vrm = this.avatar?.avatar?.vrm;
        if (!vrm?.humanoid) return { failed: [] };
        this._vrmaInitAttempted = true;
        if (!this._vrmaPlayer) this._vrmaPlayer = new VRMAnimationPlayer(vrm);
        const player = this._vrmaPlayer;
        const previous = this._vrmaManifest || { clips: {} };
        const manifest = snapshot.manifest;
        const resources = snapshot.resources;
        const accepted = new Set();
        for (const [name, resource] of Object.entries(resources)) {
            if (player._revisions.get(name) === resource.revision
                && player._formats.get(name) === resource.format) accepted.add(name);
        }
        // Metadata follows each successful clip commit. Failed/pending clips retain
        // their previous facial ownership/profile and semantic mapping.
        const publish = () => {
            if (!current()) return;
            const effective = structuredClone(manifest);
            effective.clips = { ...manifest.clips };
            effective.body_only_clips = [...(manifest.body_only_clips || [])];
            effective.facial_profiles = { ...(manifest.facial_profiles || {}) };
            effective.pose_map = { ...(manifest.pose_map || {}) };
            effective.activity_clips = { ...(manifest.activity_clips || {}) };
            for (const name of Object.keys(resources)) {
                if (accepted.has(name)) continue;
                if (previous.clips?.[name]) effective.clips[name] = previous.clips[name];
                else delete effective.clips[name];
                effective.body_only_clips = effective.body_only_clips.filter(n => n !== name);
                if (previous.body_only_clips?.includes(name)) effective.body_only_clips.push(name);
                if (previous.facial_profiles?.[name]) effective.facial_profiles[name] = previous.facial_profiles[name];
                else delete effective.facial_profiles[name];
            }
            // Reconcile both omitted and changed keys. Never restore a mapping
            // to a withdrawn clip or override a newly accepted target.
            const reconcile = (next, old, clipOf) => {
                for (const key of new Set([...Object.keys(next), ...Object.keys(old || {})])) {
                    const target = clipOf(next[key]);
                    const retained = clipOf(old?.[key]);
                    if (accepted.has(target)) continue;
                    if (Object.hasOwn(effective.clips, retained)
                        && (target != null || !accepted.has(retained))) next[key] = old[key];
                    else delete next[key];
                }
            };
            reconcile(effective.pose_map, previous.pose_map, value => value);
            reconcile(effective.activity_clips, previous.activity_clips, value => value?.clip);
            this._vrmaManifest = effective;
            this._setAnimationBlinkSuppressed(this.isExplicitAnimationActive());
            this._emitPosesChanged();
        };
        let removedActive = false;
        for (const name of [...player.clips.keys()]) {
            if (Object.hasOwn(resources, name)) continue;
            if (player.currentName === name) removedActive = true;
            player.remove(name);
        }
        publish();
        if (removedActive) {
            this._detachVrmaFinishedHandler();
            this.currentPose = null;
            this._manualPoseActive = false;
            this._returnToIdleClipAfterGesture();
        }
        const results = await Promise.all(Object.entries(resources).map(async ([name, resource]) => {
            const ok = await player.load(name, resource.url, {
                revision: resource.revision, format: resource.format, isCurrent: current,
                resolveAsset: resolveAsset ? () => resolveAsset(resource) : null,
                onCommit: () => { accepted.add(name); publish(); },
            });
            return ok ? null : name;
        }));
        if (!current()) return { failed: [] };
        const failed = results.filter(Boolean);
        if (!failed.length) this._manifestRevision = snapshot.revision;
        this._vrmaInitAttempted = true;
        if (!player.current) this._returnToIdleClipAfterGesture(0.3);
        publish();
        return { failed };
    }

    /**
     * Resolve a legacy procedural pose name → VRMA clip name via the manifest.
     * Returns the clip name if a VRMA is loaded for it, otherwise null.
     */
    _vrmaClipFor(poseName) {
        if (!this._vrmaPlayer || !this._vrmaManifest) return null;
        const mapped = this._vrmaManifest.pose_map?.[poseName] || poseName;
        return this._vrmaPlayer.hasAnimation(mapped) ? mapped : null;
    }

    /** Runtime activities persist until their real capture/response state ends. */
    setActivity(state = 'idle') {
        if (this._disposed) return false;
        const next = ['listening', 'thinking', 'speaking'].includes(state) ? state : 'idle';
        const changed = this._activityState !== next;
        this._activityState = next;
        if (!this._vrmaPlayer) return false;
        // She finishes the walk first: arriving hands over to the activity's clip.
        if (this.isWalking()) return false;
        if (next === 'idle') {
            if (this._activityClip) {
                this._activityClip = null;
                this._returnToIdleClipAfterGesture(0.3);
            }
            return false;
        }
        if (this._activityClip && this._activityClip === this._vrmaPlayer.currentName && !changed) {
            return true;
        }
        // A requested gesture may finish during speech; the activity resumes
        // from its completion callback. Actual microphone capture has priority.
        if (!this._activityClip && (SEATS[this.currentPose] || (next !== 'listening'
            && (this._vrmaExternalClip || (this.currentPose && this._manualPoseActive))))) {
            return false;
        }
        return this._startActivityClip(next);
    }

    /**
     * The clip a gesture plays this time: one of its `variants` in the manifest, drawn by
     * `variant_weights` (1 when absent). The one it played last weighs a quarter, so a gesture
     * seldom repeats yet its first choice stays first; `repeat` keeps its full weight.
     * A gesture without loaded variants plays its own clip.
     */
    _variantFor(name, { repeat = false } = {}) {
        const options = (this._vrmaManifest?.variants?.[name] || [])
            .filter(clip => typeof clip === 'string' && this._vrmaPlayer?.hasAnimation(clip));
        if (!options.length) return name;
        const last = this._lastVariants?.get(name);
        const weights = this._vrmaManifest?.variant_weights || {};
        const weightOf = clip => {
            const weight = Number(weights[clip]);
            const base = Number.isFinite(weight) && weight > 0 ? weight : 1;
            return clip === last && !repeat && options.length > 1 ? base / 4 : base;
        };
        let draw = Math.random() * options.reduce((sum, clip) => sum + weightOf(clip), 0);
        const clip = options.find(option => (draw -= weightOf(option)) < 0) ?? options.at(-1);
        (this._lastVariants ??= new Map()).set(name, clip);
        return clip;
    }

    _startActivityClip(state) {
        const config = this._vrmaManifest?.activity_clips?.[state];
        if (!config || !this._vrmaPlayer?.hasAnimation(config.clip)) return false;
        // Each response speaks with one of the variants, kept for the whole response. The
        // favourite may speak response after response.
        const clip = this._variantFor(config.clip, { repeat: true });
        this._detachVrmaFinishedHandler();
        this.currentPose = null;
        this._manualPoseActive = false;
        this._returningToIdle = false;
        this._boneReturning = false;
        this._vrmaPoseManagedByMixer = null;
        this._animationTransitionRemaining = 0;
        if (!this._vrmaPlayer.play(clip, { loop: config.loop === true, fadeIn: 0.3 })) {
            return false;
        }
        this._activityClip = clip;
        this._vrmaExternalClip = clip;
        this._vrmaActive = true;
        this._setAnimationBlinkSuppressed(true);
        // Non-looping listening actions clamp on their final sample. There is
        // deliberately no finished handler returning them to idle.
        return true;
    }

    pause() {
        this.paused = true;
        // A pose explicitly requested by Hermes/UI owns the body until it
        // completes, including during speech. Random ambient poses should be
        // dismissed so SpeechAnimator can take over cleanly.
        if (this.currentPose && !this._manualPoseActive) {
            const group = this.avatar?.avatar?.group;
            if (group) {
                this._returningToIdle = true;
                this._returnTimer = 0;
                this._returnFromPos.copy(group.position);
                this._returnFromRot.copy(group.rotation);
            }
            if (this._vrmaExternalClip) {
                this._detachVrmaFinishedHandler();
                this._vrmaPlayer?.stop(0.2);
                this._vrmaActive = false;
                this._vrmaExternalClip = null;
                this._vrmaPoseManagedByMixer = null;
            }
            this._snapshotBonesForReturn();
            this.currentPose = null;
            this._manualPoseActive = false;
            this.poseTimer = 0;
        }
    }

    /**
     * Play a VRMA clip on-demand from outside the random idle rotation.
     *
     * Used by ``app.js`` when the LLM emits ``animacion_virtual`` —
     * the wave / think / nod / shake clips already loaded in the
     * VRMAnimationPlayer are full-body animations, so reusing them here
     * gives the avatar a proper greeting/nod/etc. instead of just the
     * head-bone wiggle that `VRMAvatar.playAnimation` produces.
     *
     * Returns ``true`` if the clip was queued (caller should NOT also
     * trigger the procedural fallback), ``false`` if the clip isn't
     * loaded (caller falls back to head-bone procedural).
     *
     * Doesn't fight the idle state machine: any in-flight idle pose is
     * cleared first, and the player's mixer ticks via the normal
     * update() path (which keeps running even while paused, so this
     * works during speech turns too).
     */
    playClipExternal(clipName, { fadeIn = 0.2, fadeOut = 0.3 } = {}) {
        if (SEATS[this.currentPose]) return this._gestureInSeat(clipName);
        if (!clipName || !this._vrmaPlayer) return false;
        if (!this._vrmaPlayer.hasAnimation?.(clipName)) return false;

        // Drop any in-progress idle pose so its bone targets don't
        // blend with the new clip. The pose state machine will pick
        // up cleanly from `resume()` after speech ends.
        if (this.currentPose) {
            this._snapshotBonesForReturn();
            this.currentPose = null;
            this._manualPoseActive = false;
            this.poseTimer = 0;
        }

        // A previous procedural pose may still be blending its root/bones
        // back to rest. It must not keep doing so underneath the new clip.
        this._returningToIdle = false;
        this._boneReturning = false;
        this._returnTimer = 0;
        this._boneReturnTimer = 0;
        this._vrmaPoseManagedByMixer = null;
        this._animationTransitionRemaining = 0;

        // All four steps (detach previous handler, play, set flags,
        // wire finished handler) live in the helper — guarantees the
        // _vrmaExternalClip flag never gets forgotten on any call path.
        this.timer = 0;
        this.nextAction = this._randomInterval();
        return this._startOneShotClip(clipName, clipName, { fadeIn, fadeOut });
    }

    /** Pick a calm clip from return_idle_pool (or the neutral entries
     * in idle_pool) and cross-fade into it. Expressive ambient clips must
     * not run immediately after a one-shot gesture: that reads as a second,
     * unintended action and makes the avatar look as if it is being pulled
     * away from its final pose. */
    _returnToIdleClipAfterGesture(fadeOut = 0.3) {
        if (!this._vrmaPlayer) return;
        if (this._startActivityClip(this._activityState)) return;
        this._activityClip = null;
        const configuredReturnPool = Array.isArray(this._vrmaManifest?.return_idle_pool)
            ? this._vrmaManifest.return_idle_pool
            : [];
        const neutralFallbackPool = Array.isArray(this._vrmaManifest?.idle_pool)
            ? this._vrmaManifest.idle_pool.filter(name => name === 'idle' || name === 'idle_casual')
            : [];
        const pool = configuredReturnPool.length ? configuredReturnPool : neutralFallbackPool;
        const candidates = pool.filter(n => this._vrmaPlayer.hasAnimation?.(n));
        const pick = candidates.length
            ? candidates[Math.floor(Math.random() * candidates.length)]
            : (this._vrmaPlayer.hasAnimation?.('idle') ? 'idle' : null);

        if (pick) {
            // Loop the idle so the avatar keeps breathing instead of
            // freezing at the end of a one-shot.
            this._startLoopingClip(pick, { fadeIn: fadeOut, fadeOut: 0.25 });
        } else {
            // No idle clip loaded — fade out the held pose so the rig
            // returns to T-pose / rest rather than locking on the
            // gesture's last frame.
            this._vrmaPlayer.stop(fadeOut);
            this._vrmaActive = false;
            this._vrmaExternalClip = null;
            this._vrmaPoseManagedByMixer = null;
            this._animationTransitionRemaining = Math.max(0.05, fadeOut + 0.05);
            this._setAnimationBlinkSuppressed(true);
        }
    }

    /** Detach the one-shot mixer listener (no-op if not attached). */
    _detachVrmaFinishedHandler() {
        if (this._vrmaFinishedHandler && this._vrmaPlayer?.mixer) {
            this._vrmaPlayer.mixer.removeEventListener(
                'finished', this._vrmaFinishedHandler,
            );
        }
        if (this._vrmaReplacementHandler) this._vrmaPlayer?.mixer?.removeEventListener('action_replaced', this._vrmaReplacementHandler);
        this._vrmaReplacementHandler = null;
        this._vrmaFinishedHandler = null;
        this._pendingVrmaFinish = null;
    }

    /**
     * Single chokepoint for starting a ONE-SHOT VRMA clip (gestures,
     * dances, user-triggered animations). Centralises the four things
     * every caller used to repeat verbatim:
     *
     *   1. Detach any previous ``finished`` listener (would otherwise
     *      fire on the new clip's identity-mismatch and silently leak).
     *   2. Call ``_vrmaPlayer.play(clip, …)``.
     *   3. Set BOTH ``_vrmaActive = true`` AND ``_vrmaExternalClip = clipName``.
     *      The second flag is what gates MicroLife / SpeechAnimator /
     *      secondary animation layers from writing bones on top of the clip. Missing
     *      this set is exactly the "head centering glitch on uploaded
     *      animations" bug we hunted for seven rounds; using this
     *      helper makes it impossible to repeat.
     *   4. Wire the finished handler so the rig returns to a looping
     *      idle clip instead of freezing on the last keyframe.
     *
     * ``clipKey`` is the canonical name we expose to the rest of the
     * codebase via ``_vrmaExternalClip``. ``clip`` is whatever the
     * player accepts as the first arg to ``play()`` (usually the same
     * string but kept distinct for callers that want a verbose name
     * while hitting an obscure clip id).
     */
    _startOneShotClip(clip, clipKey, {
        fadeIn = 0.25,
        fadeOut = 0.25,
        managePoseLifecycle = false,
        then = null,
    } = {}) {
        if (!this._vrmaPlayer) return false;
        clip = this._variantFor(clip);
        this._detachVrmaFinishedHandler();
        // Cancel any stale procedural return before the mixer takes ownership.
        // Otherwise `_lerpBonesToRest()` keeps pulling against the new clip.
        this._returningToIdle = false;
        this._boneReturning = false;
        this._returnTimer = 0;
        this._boneReturnTimer = 0;
        this._animationTransitionRemaining = 0;
        // The player's boolean return is the authority: on failure it leaves
        // ``current`` pointing at the old action, so never wire a handler;
        // on success ``current`` is the action that was just reset/replayed.
        const ok = this._vrmaPlayer.play(clip, { loop: false, fadeIn, fadeOut });
        if (!ok) {
            console.warn('[idle] _startOneShotClip: player rejected', clip);
            return false;
        }
        this._vrmaActive = true;
        this._vrmaExternalClip = clipKey;
        this._activityClip = null;
        this._activityClip = null;
        const ownedPose = managePoseLifecycle ? this.currentPose : null;
        this._vrmaPoseManagedByMixer = ownedPose;
        this._animationTransitionRemaining = 0;
        this._setAnimationBlinkSuppressed(true);
        const mixer = this._vrmaPlayer.mixer;
        let startedAction = this._vrmaPlayer.current;
        // Re-triggering the SAME clip legitimately reuses the same
        // AnimationAction.  Always attach a fresh identity-filtered handler
        // after a successful play; skipping it for a reused action leaves the
        // avatar clamped on the final frame with `_vrmaExternalClip` set.
        if (mixer && startedAction) {
            const handler = (event) => {
                // ``finished`` fires for every non-looping action on
                // this mixer; identity-filter so a stale finish from
                // a previous clip can't yank us out of THIS one.
                if (event.action !== startedAction) return;
                // The mixer emits finished BEFORE applying its final sample.
                // Change actions only after update() completes its binding pass.
                this._pendingVrmaFinish = () => {
                    this._detachVrmaFinishedHandler();
                    // VRMA-backed menu/random poses are governed by the mixer's
                    // real duration, not the approximate duration in IDLE_POSES.
                    // Clearing the pose here prevents its old timer from stopping
                    // the idle clip that we are about to start.
                    if (ownedPose && this.currentPose === ownedPose) {
                        this.currentPose = null;
                        this._manualPoseActive = false;
                        this.poseTimer = 0;
                    }
                    this._vrmaPoseManagedByMixer = null;
                    if (then) then();
                    else this._returnToIdleClipAfterGesture(fadeOut);
                };
            };
            mixer.addEventListener('finished', handler);
            this._vrmaFinishedHandler = handler;
            this._vrmaReplacementHandler = event => {
                if (event.previous === startedAction) {
                    startedAction = event.action;
                    this._pendingVrmaFinish = null;
                }
            };
            mixer.addEventListener('action_replaced', this._vrmaReplacementHandler);
        }
        return true;
    }

    /**
     * Single chokepoint for LOOPING idle clips (the "return to idle"
     * after a gesture, or the boot-time ambient loop). Loops have
     * NO finished handler — they loop forever — and they clear
     * ``_vrmaExternalClip`` so MicroLife / SpeechAnimator resume
     * writing their procedural touches on top.
     */
    _startLoopingClip(clip, { fadeIn = 0.3, fadeOut = 0.25 } = {}) {
        if (!this._vrmaPlayer) return false;
        this._detachVrmaFinishedHandler();
        if (!this._vrmaPlayer.play(clip, { loop: true, fadeIn, fadeOut })) return false;
        this._boneReturning = false;
        this._vrmaActive = true;
        this._vrmaExternalClip = null;
        this._activityClip = null;
        this._vrmaPoseManagedByMixer = null;
        // Keep all secondary writers paused through the actual crossfade.
        this._animationTransitionRemaining = Math.max(fadeIn, fadeOut) + 0.05;
        this._setAnimationBlinkSuppressed(true);
        return true;
    }

    _startLoopingInteractionClip(clip, clipKey, { fadeIn = 0.3, fadeOut = 0.25 } = {}) {
        if (!this._vrmaPlayer) return false;
        this._detachVrmaFinishedHandler();
        const ok = this._vrmaPlayer.play(clip, { loop: true, fadeIn, fadeOut });
        if (!ok) {
            console.warn('[idle] _startLoopingInteractionClip: player rejected', clip);
            return false;
        }
        this._vrmaActive = true;
        this._vrmaExternalClip = clipKey;
        this._activityClip = null;
        this._vrmaPoseManagedByMixer = null;
        this._animationTransitionRemaining = 0;
        this._setAnimationBlinkSuppressed(true);
        return true;
    }

    _startSeatedSequence(seatedClip, { sitClip = 'pc_sit_down', clipKey = 'pc_seated_idle', thenClip = null, sitFade = 0.28 } = {}) {
        if (!this._vrmaPlayer) return false;
        if (!this._vrmaPlayer.hasAnimation?.(sitClip)) {
            return this._startLoopingInteractionClip(seatedClip, clipKey, {
                fadeIn: 0.45,
                fadeOut: 0.25,
            });
        }

        this._detachVrmaFinishedHandler();
        const ok = this._vrmaPlayer.play(sitClip, {
            loop: false,
            fadeIn: sitFade ?? 0.28,
            fadeOut: 0.18,
        });
        if (!ok) {
            return this._startLoopingInteractionClip(seatedClip, clipKey, {
                fadeIn: 0.45,
                fadeOut: 0.25,
            });
        }

        this._vrmaActive = true;
        this._vrmaExternalClip = clipKey;
        this._activityClip = null;
        this._vrmaPoseManagedByMixer = null;
        this._animationTransitionRemaining = 0;
        this._setAnimationBlinkSuppressed(true);
        const mixer = this._vrmaPlayer.mixer;
        let startedAction = this._vrmaPlayer.current;
        if (mixer && startedAction) {
            const handler = (event) => {
                if (event.action !== startedAction) return;
                this._pendingVrmaFinish = () => {
                    this._detachVrmaFinishedHandler();
                    if (thenClip) {
                        this._startSeatedSequence(seatedClip, { sitClip: thenClip, clipKey });
                        return;
                    }
                    this._startLoopingInteractionClip(seatedClip, clipKey, {
                        fadeIn: 0.18,
                        fadeOut: 0.25,
                    });
                };
            };
            mixer.addEventListener('finished', handler);
            this._vrmaFinishedHandler = handler;
            this._vrmaReplacementHandler = event => {
                if (event.previous === startedAction) {
                    startedAction = event.action;
                    this._pendingVrmaFinish = null;
                }
            };
            mixer.addEventListener('action_replaced', this._vrmaReplacementHandler);
        }
        return true;
    }

    isWalking() {
        return this._walking === true && this._vrmaPlayer?.currentName === (this._walkClip || 'walk');
    }

    /**
     * Pick the clip and relative pace together so the room can keep them on the route.
     */
    chooseWalk() {
        const player = this._vrmaPlayer;
        // The plain walk most trips (variant_weights), not turn and turn about.
        const clip = player?.hasAnimation?.('walk') ? this._variantFor('walk', { repeat: true }) : 'walk';
        const pace = (WALK_CLIP_SPEEDS[clip] ?? WALK_CLIP_SPEED) / WALK_CLIP_SPEED;
        return { clip, pace };
    }

    /**
     * Start or stop the walk clip. `speed` is in room units per second, so a foot on the
     * ground stands still in the room. False without the clip: the caller walks her.
     */
    setWalking(walking, speed = 0, plannedClip = 'walk') {
        const player = this._vrmaPlayer;
        if (!player?.hasAnimation?.('walk')) return false;
        if (walking) {
            const clip = player.hasAnimation(plannedClip) ? plannedClip : 'walk';
            if (player.currentName !== clip && !this._startLoopingClip(clip, { fadeIn: 0.22, fadeOut: 0.2 })) return false;
            this._walkClip = clip;
            this._walking = true;
            player.current.setEffectiveTimeScale(THREE.MathUtils.clamp(speed / (WALK_CLIP_SPEEDS[clip] ?? WALK_CLIP_SPEED), 0.35, 1.5));
        } else if (this._walking) {
            this._walking = false;
            if (player.currentName === this._walkClip) this._returnToIdleClipAfterGesture(0.25);
        }
        return true;
    }

    /** What she holds, as the clip playing says, or null with her hands free. */
    heldProp() {
        const player = this._vrmaPlayer;
        const time = player?.current?.time ?? 0;
        for (const { prop } of Object.values(SEATS)) {
            if (!prop) continue;
            const { id } = prop;
            if (player?.currentName === prop.in) return { id, held: time >= prop.take, open: curveAt(prop.open, time) };
            if (player?.currentName === prop.out) return { id, held: time < prop.release, open: curveAt(prop.close, time) };
            if (player?.currentName === prop.loop) return { id, held: true, open: 1 };
        }
        return null;
    }

    /** Still getting up from a seat, with more than `seconds` of it to go? */
    isGettingUp(seconds = 0) {
        const player = this._vrmaPlayer;
        const name = player?.currentName;
        if (!name) return false;   // nothing playing yet, or no clips loaded at all
        const seats = Object.values(SEATS);
        const closing = seats.find(seat => seat.closes === name);
        if (!closing && !seats.some(seat => seat.stand === name)) return false;
        const rise = closing ? player.clips.get(closing.stand)?.duration ?? 0 : 0;
        return player.current.getClip().duration - player.current.time + rise > seconds;
    }

    /** Is she in a seat she can only leave in a line of light? */
    leavesByLight() {
        return Boolean(SEATS[this.currentPose]?.teleport);
    }

    /** Is she held in this seat? Without a name: in any seat. */
    isSeated(name = this.currentPose) {
        return Boolean(SEATS[name]) && this.currentPose === name;
    }

    /** The seat she would read in from where she sits, if any. */
    readingSeat() {
        const seat = SEATS[this.currentPose]?.reads;
        return seat && this._vrmaClipFor(seat) ? seat : null;
    }

    /** Are all the clips of this pose loaded? */
    canHold(name) {
        const seat = SEATS[name];
        const loop = this._vrmaClipFor(name);
        if (!loop) return false;
        return !seat || [loop, seat.sit, seat.then, seat.closes, seat.stand].filter(Boolean)
            .every(clip => this._vrmaPlayer?.hasAnimation?.(clip));
    }

    /**
     * Put away what she holds, or sit up: where she can talk. She stays in her place. Only
     * from the loop: a clip on its way in is left to finish, so the caller asks again.
     */
    putAway() {
        this._shiftAfterClosing = null;
        const seat = SEATS[this.currentPose];
        if (!seat || this._vrmaPlayer?.currentName !== this._vrmaClipFor(this.currentPose)) return false;
        if (seat.base) return this.shiftSeat(seat.base);
        if (seat.prop) return this.standUp();
        return this.currentPose === 'bed_lying' ? this.shiftSeat('bed_seated') : false;
    }

    /** A gesture asked for in a seat: the body stays, and those that are in the head still show. */
    _gestureInSeat(name) {
        const head = SEATED_HEAD_GESTURES[name];
        if (head) this.avatar?.avatar?._triggerHeadAnim?.(head, 700, { over: true });
        return true;
    }

    /** Go straight to this seat from the one she is in, when clips join the two. */
    shiftSeat(name) {
        const seat = SEATS[name];
        const seated = seat && this._vrmaClipFor(name);
        if (!seated) return false;
        const here = SEATS[this.currentPose];
        // From reading to a seat joined to where she sits: the book goes away first.
        if (here?.base && seat.joins?.[here.base] && !seat.joins[this.currentPose]) {
            this.currentPose = null;
            this._shiftAfterClosing = name;
            return this._startOneShotClip(here.closes, here.closes, {
                fadeIn: here.closesFade ?? 0.2,
                then: () => {
                    this.currentPose = here.base;
                    if (this._shiftAfterClosing === name) this.shiftSeat(name);
                    // Spoken to meanwhile (putAway): she stays there with her hands free.
                    else this._startLoopingInteractionClip(this._vrmaClipFor(here.base), here.base, { fadeIn: 0.18, fadeOut: 0.25 });
                    this._shiftAfterClosing = null;
                },
            });
        }
        // To reading from a seat joined to the one she reads in: that one first, then the book.
        const through = seat.base ? SEATS[seat.base].joins?.[this.currentPose] : null;
        const step = seat.joins?.[this.currentPose] ?? through;
        if (!step || !this._vrmaPlayer.hasAnimation(step.clip)) return false;
        this.currentPose = name;
        this._startSeatedSequence(seated, {
            sitClip: step.clip, clipKey: name, thenClip: through && step === through ? seat.then : null, sitFade: step.fade,
        });
        this._vrmaPlayer.current.time = step.at;
        return true;
    }

    /**
     * Leave a seat through its stand-up clip. A walk that is waiting starts from the clip's
     * last frame, because SceneActions holds it while a clip owns the body.
     */
    standUp() {
        const seat = SEATS[this.currentPose];
        // A seat reached in a line of light has no clip to get up: the room takes her away.
        if (!seat || seat.teleport) return false;
        this.currentPose = null;
        this._manualPoseActive = false;
        this.poseTimer = 0;
        const rise = () => this._startOneShotClip(seat.stand, seat.stand, { fadeIn: 0.2, fadeOut: 0.25 });
        return seat.closes ? this._startOneShotClip(seat.closes, seat.closes, { fadeIn: seat.closesFade ?? 0.2, then: rise }) : rise();
    }

    resume() {
        this.paused = false;
        this.timer = 0;
        this.nextAction = this._randomInterval();
    }

    /** Explicit Room exit: clear pose ownership and rebase all root returns.
     * Audio and Hermes keep running; callers preserve the speech pause. */
    resetRoomPlacement({ paused = this.paused, fade = 0.3 } = {}) {
        const avatar = this.avatar?.avatar;
        const group = avatar?.group;
        if (!group) return false;
        const height = (this.currentPose || this._returningToIdle)
            ? this._savedPosition.y : group.position.y;
        const hadPose = !!(this.currentPose || this._vrmaExternalClip);
        this._detachVrmaFinishedHandler();
        this._snapshotBonesForReturn();
        this.currentPose = null;
        this._manualPoseActive = false;
        this.poseTimer = 0;
        this._vrmaPoseManagedByMixer = null;
        if (hadPose) this._returnToIdleClipAfterGesture(fade);
        this._returningToIdle = false;
        this._returnTimer = 0;
        group.position.set(0, height, 0);
        group.rotation.set(0, Number.isFinite(avatar.restYaw) ? avatar.restYaw : 0, 0);
        this._savedPosition.copy(group.position);
        this._savedRotation.copy(group.rotation);
        this._returnFromPos.copy(group.position);
        this._returnFromRot.copy(group.rotation);
        // Bone return still needs exclusive ownership, even without a VRMA.
        this._animationTransitionRemaining = Math.max(this._animationTransitionRemaining, 0.5);
        this._setAnimationBlinkSuppressed(true);
        this.resume();
        this.paused = paused;
        return true;
    }

    /**
     * Manually trigger a named pose (for UI buttons, voice commands, etc.).
     * Returns true if the pose was found and started, false otherwise.
     */
    trigger(name) {
        // A standing gesture would lift her out of the seat in one crossfade. She stays
        // seated, and the gesture's mood still reaches her face.
        if (SEATS[this.currentPose]) return this._gestureInSeat(name);
        const pose = IDLE_POSES.find(p => p.name === name && !p.continuous);
        if (!pose) return false;
        const group = this.avatar?.avatar?.group;
        if (!group) return false;
        const clip = this._vrmaClipFor(pose.name);
        const playableClip = clip;

        // Block body-movement poses (arms/legs/walking) unless a VRMA clip
        // is available. The procedural fallbacks look broken on most rigs
        // (arms swing backwards, cloth clips through legs), so we require
        // baked animation files to enable them.
        if ((pose.tier === 'risky' || pose.tier === 'custom') &&
            !playableClip) {
            console.warn(
                `[idle] Pose "${pose.name}" blocked: no VRMA clip at ` +
                `face/animations/${pose.name}.vrma. Drop the file in and reload.`
            );
            return false;
        }

        // Interrupt any ongoing pose or return animation
        this._returningToIdle = false;
        this._boneReturning = false;
        this._animationTransitionRemaining = 0;
        if (this._vrmaActive) {
            this._detachVrmaFinishedHandler();
            if (!playableClip) {
                // Procedural poses need a clean skeleton. When another clip
                // will start, keep the current action alive so `play()` can
                // crossfade from it instead of fading up from bind pose.
                this._vrmaPlayer?.stop(0.2);
                this._vrmaActive = false;
                this._vrmaExternalClip = null;
                this._vrmaPoseManagedByMixer = null;
            }
        }

        this.currentPose = pose.name;
        this._manualPoseActive = true;
        this.poseTimer = 0;
        this.poseDuration = pose.duration;
        this._captureReturnBaseline(group);
        this._setAnimationBlinkSuppressed(true);

        // Prefer a VRMA clip if one is available for this pose.
        // Routing through `_startOneShotClip` guarantees the
        // `_vrmaExternalClip` flag gets set (the fix for the
        // "head centering glitch on UI-triggered uploads") AND the
        // finished handler is wired so we return to idle cleanly.
        if (playableClip) {
            if (pose.name === 'pc_seated_idle') {
                this._startSeatedSequence(playableClip);
            } else if (SEATS[pose.name]) {
                const seat = SEATS[pose.name];
                this._startSeatedSequence(playableClip, { sitClip: seat.sit, clipKey: pose.name, thenClip: seat.then });
            } else if (pose.name === 'pc_focus_idle') {
                this._startLoopingInteractionClip(playableClip, pose.name, {
                    fadeIn: 0.28,
                    fadeOut: 0.25,
                });
            } else {
                this._startOneShotClip(playableClip, pose.name, {
                    managePoseLifecycle: true,
                });
            }
        }

        // Reset timer so the next random pose doesn't fire immediately after
        this.timer = 0;
        this.nextAction = this._randomInterval();
        return true;
    }

    /**
     * Poses the UI should currently show buttons for. Each entry has:
     *   { name, label, tier, hasVRMA }
     *
     * Rules:
     *   • 'safe' poses are always included (procedural fallback is clean)
     *   • 'risky' and 'custom' poses are included ONLY if a VRMA clip is
     *     loaded for them — procedural body-movement animations on rigs
     *     without proper cloth/arm physics look broken (arms backwards,
     *     cloth clipping), so we hide them until the user drops a .vrma
     *     file into face/animations/.
     */
    availablePoses() {
        const out = [];
        for (const p of IDLE_POSES) {
            if (p.continuous) continue;
            if (!p.label) continue; // unnamed / internal
            const menu = this._vrmaManifest?.menu_poses;
            if (Array.isArray(menu) && !menu.includes(p.name)) continue;
            const hasVRMA = !!this._vrmaClipFor(p.name);
            if (p.tier === 'safe') {
                out.push({ name: p.name, label: p.label, tier: p.tier, hasVRMA });
            } else if ((p.tier === 'risky' || p.tier === 'custom') && hasVRMA) {
                out.push({ name: p.name, label: p.label, tier: p.tier, hasVRMA });
            }
        }
        return out;
    }

    /** Fires a named event when the availability list changes */
    _emitPosesChanged() {
        if (this._disposed) return;
        try {
            window.dispatchEvent(new CustomEvent('IRIS:posesChanged', {
                detail: { poses: this.availablePoses() },
            }));
        } catch (_) { /* noop in non-browser envs */ }
    }

    update(delta, elapsed) {
        if (!this.active) return;

        const group = this.avatar?.avatar?.group;
        if (!group) return;
        this.avatar.avatar.poseClipTime = this._vrmaPlayer?.current?.time ?? 0;

        // Decay continuous params toward defaults — keeps a one-shot
        // emotion spike from pinning IRIS's mood forever.
        this._decayParams(delta);

        this._ensureBoneRefs();

        // Lazy-init VRMA player once the VRM is available
        if (!this._vrmaInitAttempted && this._hasBones()) {
            this._initVRMAPlayer();
        }
        // If this avatar doesn't have bones (procedural / particle), we'll
        // never init VRMA — emit once so the pose menu still populates with
        // the safe subset instead of sitting on "Cargando…".
        if (!this._vrmaInitAttempted && !this._hasBones() && !this._emittedInitialPoses) {
            this._emittedInitialPoses = true;
            this._emitPosesChanged();
        }

        // Stop fades still need mixer time after _vrmaActive becomes false.
        this._vrmaPlayer?.update(delta);
        const finish = this._pendingVrmaFinish;
        this._pendingVrmaFinish = null;
        finish?.();
        this.avatar?.avatar?.setSeatDrape?.(this._seatDrapeWeight());
        // Eased, so a lie-down cut short does not drop her hips in one frame.
        this._lying = THREE.MathUtils.damp(this._lying ?? 0, this._lyingWeight(), 8, delta);
        // Held lying on her back or front with her hips in IRIS' place: read where her body
        // rests, once per avatar and way of lying (LyingHips fits her body to IRIS' from then on).
        const rests = SEATS[this.currentPose]?.rests;
        if (rests && this._lying > 0.98 && this._vrmaPlayer?.currentName === this._vrmaClipFor(this.currentPose)) {
            this.avatar?.avatar?.measureLyingHips?.(rests);
        }
        this.avatar?.avatar?.setLyingHips?.(this._lying < 1e-3 ? 0 : this._lying, delta);
        this.avatar?.avatar?.holdHair?.(this._holdsHair());
        this.avatar?.avatar?.setHandReach?.(this._handReachWeight());

        // Smooth return runs even while paused (so interrupting lie_bed still
        // brings the character back upright during speech).
        if (this._returningToIdle) {
            this._returnTimer += delta;
            const t = Math.min(this._returnTimer / this._returnDuration, 1);
            const ease = 1 - (1 - t) * (1 - t);
            group.position.lerpVectors(this._returnFromPos, this._savedPosition, ease);
            group.rotation.x = THREE.MathUtils.lerp(this._returnFromRot.x, this._savedRotation.x, ease);
            group.rotation.z = THREE.MathUtils.lerp(this._returnFromRot.z, this._savedRotation.z, ease);
            // Y rotation: only restore if we're driving the root directly
            // (procedural mode). For VRM, leave rotation.y alone — scene_actions
            // already drifts it back to the avatar's restYaw.
            if (!this._hasBones()) {
                group.rotation.y = THREE.MathUtils.lerp(this._returnFromRot.y, this._savedRotation.y, ease);
            }
            if (t >= 1) this._returningToIdle = false;
        }

        if (this._boneReturning) {
            this._boneReturnTimer += delta;
            const t = Math.min(this._boneReturnTimer / this._returnDuration, 1);
            const ease = 1 - (1 - t) * (1 - t);
            this._lerpBonesToRest(ease);
            if (t >= 1) this._boneReturning = false;
        }

        if (this._animationTransitionRemaining > 0) {
            this._animationTransitionRemaining = Math.max(
                0,
                this._animationTransitionRemaining - delta,
            );
        }
        this._setAnimationBlinkSuppressed(this.isExplicitAnimationActive());
        this._releaseAnimationOwnershipIfSettled();

        // Pausing suppresses ambient/random motion, not a gesture explicitly
        // selected by Hermes or the user. This lets `animacion_virtual`
        // remain visible while IRIS is speaking.
        if (this.paused && !(this.currentPose && this._manualPoseActive)) return;

        if (this.currentPose) {
            // A one-shot VRMA uses the mixer's real `finished` event. The
            // catalog duration is only an estimate and must not race it.
            if (
                this._vrmaPoseManagedByMixer === this.currentPose
                && this._vrmaExternalClip
            ) {
                return;
            }
            this.poseTimer += delta * 1000;
            // When a VRMA clip is driving the bones, skip the procedural
            // `_setBone` code — the mixer owns the rotations for this pose.
            if (!this._vrmaActive) {
                this._updatePose(group, delta, elapsed);
            } else {
                this._applyExternalPoseRootMotion(group);
            }

            if (this.poseTimer >= this.poseDuration) {
                this._endPose(group);
            }
            return;
        }

        // External clips have no currentPose. Do not let ambient scheduling
        // interrupt them, or the hand-off that follows their final frame.
        if (this.isExplicitAnimationActive()) return;
        this.timer += delta;
        if (this.timer >= this.nextAction) {
            this._triggerRandomPose(group);
            this.timer = 0;
            this.nextAction = this._randomInterval();
        }
    }

    // ── Private ────────────────────────────────────────────

    _applyExternalPoseRootMotion(group) {
        if (!group || !String(this.currentPose || '').startsWith('pc_seated_idle')) return;

        const poseSeconds = this.poseTimer / 1000;
        const enter = Math.min(poseSeconds / 0.85, 1);
        const exit = this.poseTimer > this.poseDuration - 800
            ? Math.max(0, (this.poseDuration - this.poseTimer) / 800)
            : 1;
        const ease = (enter * enter * (3 - 2 * enter)) * exit;
        const seatDrop = 0.50;
        group.position.y = this._savedPosition.y - seatDrop * ease;
    }

    /** Is her head low enough for a close view of the seat? Sitting down from `low`, not rising. */
    isLowInSeat() {
        const player = this._vrmaPlayer;
        for (const seat of Object.values(SEATS)) {
            if (!seat.chair) continue;
            if (player?.currentName === seat.stand) return false;
            if (player?.currentName === seat.sit) return (player.current?.time ?? 0) >= seat.chair.low;
        }
        return this.isSeated();
    }

    /** Where a clip has the desk chair, 1 out and 0 home, or null if no clip is moving it. */
    chairPlace() {
        const player = this._vrmaPlayer;
        for (const seat of Object.values(SEATS)) {
            if (!seat.chair) continue;
            if (player?.currentName === seat.sit) return curveAt(seat.chair.sit, player.current?.time ?? 0);
            if (player?.currentName === seat.stand) return curveAt(seat.chair.stand, player.current?.time ?? 0);
        }
        return SEATS[this.currentPose]?.chair ? 0 : null;
    }

    /** 1 while a clip has her hands on something, fading at the clip's way in and out. */
    _handReachWeight() {
        const player = this._vrmaPlayer;
        const name = player?.currentName, current = player?.current;
        if (!name || !current) return 0;
        for (const seat of Object.values(SEATS)) {
            const touch = seat.prop ?? seat.touch;
            if (!touch) continue;
            if (name === touch.loop) return 1;
            if (name === touch.in) return THREE.MathUtils.smoothstep(current.time, 0.2, 0.9);
            if (name === touch.out) {
                const end = current.getClip().duration;
                return 1 - THREE.MathUtils.smoothstep(current.time, end - 0.9, end - 0.2);
            }
        }
        return 0;
    }

    /** 0 with her thighs down, 1 with them raised. */
    _seatDrapeWeight() {
        const player = this._vrmaPlayer;
        const time = player?.current?.time ?? 0;
        for (const seat of Object.values(SEATS)) {
            if (seat.sit && player?.currentName === seat.sit) return curveAt(seat.drape.sit, time);
            if (seat.stand && player?.currentName === seat.stand) return curveAt(seat.drape.stand, time);
            for (const join of Object.values(seat.joins || {})) {
                if (join.drape && player?.currentName === join.clip) return curveAt(join.drape, time);
            }
        }
        return SEATS[this.currentPose]?.drape.held ?? 0;
    }

    /**
     * Lying on her back, a PMX avatar's hair keeps its modelled shape (pmx_physics.js holdHair):
     * its stiff joints arced it over the mattress or caught it under her. Face down it rests on
     * her back and falls free; the desk edge's `lies` only lifts her hips.
     */
    _holdsHair() {
        const seat = SEATS[this.currentPose];
        if (seat?.teleport || !(this._lyingWeight() > HAIR_HOLD_FROM)) return false;
        if (seat?.rests !== 'front') return true;
        const name = this._vrmaPlayer?.currentName;
        return name === seat.sit || name === seat.closes || name === seat.stand;
    }

    /** 0 upright, 1 with her back on the bed. */
    _lyingWeight() {
        const player = this._vrmaPlayer;
        const name = player?.currentName, time = player?.current?.time ?? 0;
        for (const seat of Object.values(SEATS)) {
            if (seat.lies && seat.sit && name === seat.sit) return curveAt(seat.lies.sit, time);
            if (seat.lies && seat.stand && name === seat.stand) return curveAt(seat.lies.stand, time);
            for (const join of Object.values(seat.joins || {})) {
                if (join.lies && name === join.clip) return curveAt(join.lies, time);
            }
        }
        return SEATS[this.currentPose]?.lies?.held ?? 0;
    }

    _randomInterval() {
        // Energy modulates gesture cadence:
        //   energy = 0.0  →  slow ≈ 8-22 s   (calm, daydreamy)
        //   energy = 0.5  →  base ≈ 4-14 s   (default — old behavior)
        //   energy = 1.0  →  brisk ≈ 2-7 s   (alert, expressive)
        // With six rest clips she fidgeted at the old 4-14 s: 8-20 s at the default energy.
        const energy = this.params?.energy ?? 0.5;
        const baseMin = 6 + (1 - energy) * 4;     // 6 → 10
        const span    = 8 + (1 - energy) * 8;     // 8 → 16
        return baseMin + Math.random() * span;
    }

    /**
     * Random-pose selection. Risky poses (arms/legs) are excluded unless
     * a VRMA clip is loaded for them — running the procedural fallback
     * would clip cloth on most rigs, so we quietly skip them instead.
     *
     * Mood (-1..+1) re-weights eligible poses toward emotionally-matching
     * gestures. We multiply each pose's base weight by `_moodBiasFor()`
     * BEFORE the weighted-random draw — so happy mood doesn't make
     * `look_down` impossible (still 30%-relative-to-its-base), just
     * less likely than `pose_smile` in that mood. The base catalog
     * stays as the source of truth; we just lean it.
     */
    _selectPose() {
        const mood = this.params?.mood ?? 0;
        const eligible = IDLE_POSES.filter(p => {
            if (p.continuous) return false;
            if (p.weight <= 0)  return false;
            const ambient = this._vrmaManifest?.ambient_poses;
            if (Array.isArray(ambient) && !ambient.includes(p.name)) return false;
            if (p.tier === 'risky' && !this._vrmaClipFor(p.name)) return false;
            return true;
        });
        if (eligible.length === 0) return null;

        const biased = eligible.map(p => ({
            pose: p,
            weight: p.weight * this._moodBiasFor(p.name, mood) * this._restBiasFor(p.name, mood),
        }));
        const totalWeight = biased.reduce((s, b) => s + b.weight, 0);
        if (totalWeight <= 0) return eligible[0];   // mood collapsed all weights — fallback

        let r = Math.random() * totalWeight;
        for (const b of biased) {
            r -= b.weight;
            if (r <= 0) return b.pose;
        }
        return eligible[0];
    }

    /**
     * Multiplier on pose `name`'s base weight given current `mood`
     * (-1 = sad ↔ +1 = happy). Returns 1.0 at mood=0 (no bias).
     *
     * Tuning rules:
     *   • Upbeat poses (smile/love/wave/dance/stretch/greet)
     *     — boosted at high mood, suppressed at low.
     *   • Withdrawn poses (look_down/shy_look_away/look_window)
     *     — boosted at low mood, suppressed at high.
     *   • Neutral poses (head_tilt/look_around/micro_head/subtle_nod)
     *     — flat 1.0 at all moods (always available).
     */
    /**
     * The rest clips answer the moment: she yawns at night and after a long quiet, taps her
     * foot when her question goes unanswered or the quiet drags on, and touches her hair when
     * she is happy. `restContext()` (set by the room) gives `quiet` seconds without a person,
     * `asked` seconds since a question of hers still unanswered (or null) and the local `hour`.
     */
    _restBiasFor(name, mood) {
        const { quiet = 0, asked = null, hour = new Date().getHours() } = this.restContext?.() ?? {};
        if (name === 'idle_yawn') {
            const night = hour >= 23 || hour < 6;
            return (night ? 3 : 1) * (quiet >= 300 ? 2 : quiet < 60 ? 0.5 : 1);
        }
        if (name === 'idle_waiting') {
            if (asked !== null && asked >= 15) return 6;
            return quiet >= 120 ? 2 : quiet < 30 ? 0.3 : 1;
        }
        if (name === 'hair_touch') return mood > 0.3 ? 2 : 1;
        return 1;
    }

    _moodBiasFor(name, mood) {
        const upbeat = new Set([
            'pose_smile', 'pose_love', 'pose_noble', 'pose_weave',
            'wave_hand', 'dance', 'stretch_arms', 'greet',
        ]);
        const withdrawn = new Set([
            'look_down', 'shy_look_away', 'look_window', 'lie_bed',
            'sit_chair', 'thinking_pose',
        ]);
        if (upbeat.has(name))    return 1 + Math.max(0, mood) * 1.2;
        if (withdrawn.has(name)) return 1 + Math.max(0, -mood) * 1.2;
        return 1.0;
    }

    // ── Continuous params API ────────────────────────────────────

    /**
     * Update the params from an EmotionClassifier output (the user's
     * emotion as read from the webcam). Maps:
     *   user.valence → mood       (the user's face contagiously shifts ours)
     *   user.arousal → energy     (excited user → expressive IRIS)
     * Confidence-gated: low-confidence reads barely move the params.
     */
    feedUserEmotion(emotion) {
        if (!emotion || typeof emotion !== 'object') return;
        const conf = Math.max(0, Math.min(1, emotion.confidence || 0));
        if (conf < 0.2) return;
        const moodTarget   = (emotion.valence ?? 0) * 0.6;   // damped — we don't fully copy the user
        const energyTarget = (emotion.arousal ?? 0.3);
        // Smooth toward target with `conf` as the weight — uncertain
        // reads barely move the params, confident reads move them ~30%.
        const k = 0.3 * conf;
        this.params.mood   += (moodTarget   - this.params.mood)   * k;
        this.params.energy += (energyTarget - this.params.energy) * k;
        // Engagement: any confident, fresh face read = the user is
        // looking at the camera = present. Slowly raise toward 0.9.
        this.params.engagement += (0.9 - this.params.engagement) * 0.1;
    }

    /**
     * Update params from IRIS's own LLM-emitted emotion (the
     * `emocion` field in her JSON response). Stronger weight than
     * the user's emotion since this is IRIS's intentional state.
     */
    feedIrisEmotion(label) {
        const map = {
            happy:     { mood:  0.7, energy: 0.7  },
            joy:       { mood:  0.7, energy: 0.7  },
            sad:       { mood: -0.7, energy: 0.25 },
            angry:     { mood: -0.4, energy: 0.8  },
            surprised: { mood:  0.2, energy: 0.85 },
            curious:   { mood:  0.3, energy: 0.65 },
            confused:  { mood: -0.1, energy: 0.45 },
            excited:   { mood:  0.6, energy: 0.85 },
            calm:      { mood:  0.2, energy: 0.35 },
            neutral:   { mood:  0.0, energy: 0.5  },
        };
        const target = map[label?.toLowerCase?.()] ?? null;
        if (!target) return;
        // Stronger blend than user emotion (IRIS's own state is
        // intentional, not contagious).
        const k = 0.5;
        this.params.mood   += (target.mood   - this.params.mood)   * k;
        this.params.energy += (target.energy - this.params.energy) * k;
    }

    /**
     * Decay all params toward their neutral defaults. Called inside
     * update() each frame so a one-shot spike (e.g. user laughs once,
     * then leaves the camera) doesn't pin IRIS in "happy mode" forever.
     */
    _decayParams(dt) {
        const p = this.params;
        const k = this._paramDecay * dt;
        p.energy     += (0.5 - p.energy)     * k;
        p.engagement += (0.6 - p.engagement) * k * 0.5;  // engagement decays slower
        p.mood       += (0.0 - p.mood)       * k;
        p.fidget     += (0.4 - p.fidget)     * k;
    }

    _triggerRandomPose(group) {
        const pose = this._selectPose();
        if (!pose) return; // no eligible pose (e.g. all risky and no VRMAs)
        const clip = this._vrmaClipFor(pose.name);
        if (!clip && this._vrmaActive) {
            this._detachVrmaFinishedHandler();
            this._vrmaPlayer?.stop(0.2);
            this._vrmaActive = false;
            this._vrmaExternalClip = null;
            this._vrmaPoseManagedByMixer = null;
        }
        this.currentPose = pose.name;
        this._manualPoseActive = false;
        this.poseTimer = 0;
        this.poseDuration = pose.duration;
        this._captureReturnBaseline(group);
        this._setAnimationBlinkSuppressed(true);

        // If we have a VRMA clip for this pose, play it instead of the
        // procedural bone code. The procedural `_updatePose` branch becomes
        // a no-op while VRMA is active because the mixer owns the bones.
        //
        // Was previously calling `_vrmaPlayer.play` directly without
        // setting `_vrmaExternalClip` or wiring the finished handler —
        // a latent instance of the same bug that hit the `trigger()`
        // path. Routing through the helper keeps every clip start
        // consistent across the file.
        if (clip) {
            this._startOneShotClip(clip, pose.name, {
                managePoseLifecycle: true,
            });
        }
    }

    /**
     * Snapshot position / rotation we should return to after a pose ends.
     * For rotation.y we prefer the avatar's restYaw (so the return lands
     * facing-camera) rather than whatever transient heading scene_actions
     * happened to have set when the pose began.
     */
    _captureReturnBaseline(group) {
        this._savedRotation.copy(group.rotation);
        this._savedPosition.copy(group.position);
        const restYaw = this.avatar?.avatar?.restYaw;
        if (typeof restYaw === 'number') {
            this._savedRotation.y = restYaw;
        }
    }

    _ensureBoneRefs() {
        if (this._boneRefs !== null) return;

        const vrm = this.avatar?.avatar?.vrm;
        if (!vrm?.humanoid) {
            this._boneRefs = {};
            return;
        }

        const refs = {};
        const rest = {};
        const targets = {};
        for (const name of HUMANOID_BONES) {
            const node = vrm.humanoid.getNormalizedBoneNode(name);
            if (node) {
                refs[name] = node;
                rest[name] = node.rotation.clone();
                targets[name] = node.rotation.clone();
            }
        }
        this._boneRefs = refs;
        this._boneRest = rest;
        this._boneTargets = targets;
    }

    _hasBones() {
        return this._boneRefs && Object.keys(this._boneRefs).length > 0;
    }

    _setBone(name, x, y, z) {
        const bone = this._boneRefs?.[name];
        const rest = this._boneRest?.[name];
        if (!bone || !rest) return;
        this._boneTargets[name].set(rest.x + x, rest.y + y, rest.z + z);
    }

    _resetBoneTargetsToRest() {
        if (!this._hasBones()) return;
        for (const name of Object.keys(this._boneRefs)) {
            this._boneTargets[name].copy(this._boneRest[name]);
        }
    }

    _applyBoneTargets(alpha) {
        if (!this._hasBones()) return;
        for (const name of Object.keys(this._boneRefs)) {
            const b = this._boneRefs[name];
            const t = this._boneTargets[name];
            const target = this._targetQuaternion || (this._targetQuaternion = new THREE.Quaternion());
            target.setFromEuler(t);
            b.quaternion.slerp(target, alpha);
        }
    }

    _snapshotBonesForReturn() {
        if (!this._hasBones()) return;
        this._boneReturnFrom = {};
        for (const name of Object.keys(this._boneRefs)) {
            this._boneReturnFrom[name] = this._boneRefs[name].quaternion.clone();
            this._boneTargets[name].copy(this._boneRest[name]);
        }
        this._boneReturning = true;
        this._boneReturnTimer = 0;
    }

    _lerpBonesToRest(ease) {
        if (!this._boneReturnFrom) return;
        for (const name of Object.keys(this._boneRefs)) {
            const from = this._boneReturnFrom[name];
            const rest = this._boneRest[name];
            const b = this._boneRefs[name];
            const target = this._targetQuaternion || (this._targetQuaternion = new THREE.Quaternion());
            target.setFromEuler(rest);
            b.quaternion.slerpQuaternions(from, target, ease);
        }
    }

    _updatePose(group, delta, elapsed) {
        const t = this.poseTimer / this.poseDuration; // 0→1
        const boneMode = this._hasBones();

        switch (this.currentPose) {
            case 'micro_head': {
                const swayY = Math.sin(t * Math.PI) * 0.06;
                const swayX = Math.sin(t * Math.PI * 0.8) * 0.03;
                if (boneMode) {
                    this._setBone('head', swayX * 0.6, swayY * 0.6, 0);
                    this._setBone('neck', swayX * 0.4, swayY * 0.4, 0);
                } else {
                    group.rotation.y = this._savedRotation.y + swayY * 0.5;
                    group.rotation.x = this._savedRotation.x + swayX * 0.5;
                }
                break;
            }

            case 'look_around': {
                const swayY = Math.sin(t * Math.PI * 2) * 0.18;
                if (boneMode) {
                    this._setBone('head', 0, swayY * 0.55, 0);
                    this._setBone('neck', 0, swayY * 0.45, 0);
                } else {
                    group.rotation.y = this._savedRotation.y + swayY * 0.45;
                }
                break;
            }

            case 'weight_shift': {
                const shift = Math.sin(t * Math.PI);
                if (boneMode) {
                    this._setBone('hips', 0, 0, shift * 0.04);
                    this._setBone('spine', 0, 0, -shift * 0.02);
                    this._setBone('chest', 0, 0, -shift * 0.015);
                } else {
                    group.position.x = shift * 0.03;
                    group.rotation.z = this._savedRotation.z + shift * 0.01;
                }
                break;
            }

            case 'head_tilt': {
                const tilt = Math.sin(t * Math.PI) * 0.22;
                if (boneMode) {
                    this._setBone('head', 0, 0, tilt);
                    this._setBone('neck', 0, 0, tilt * 0.4);
                } else {
                    group.rotation.z = this._savedRotation.z + tilt * 0.2;
                }
                break;
            }

            case 'look_window': {
                const ease = Math.sin(t * Math.PI);
                if (boneMode) {
                    this._setBone('head', 0, -0.35 * ease, 0);
                    this._setBone('neck', 0, -0.2 * ease, 0);
                    this._setBone('chest', 0, -0.1 * ease, 0);
                } else {
                    group.rotation.y = THREE.MathUtils.lerp(this._savedRotation.y, -0.15, ease);
                }
                break;
            }

            case 'look_down': {
                // This rig has INVERTED head-pitch convention: +X tips head
                // BACKWARD (looks up). So to look DOWN we need NEGATIVE X.
                const down = Math.sin(t * Math.PI) * 0.28;
                if (boneMode) {
                    this._setBone('head', -down, 0, 0);
                    this._setBone('neck', -down * 0.5, 0, 0);
                } else {
                    group.rotation.x = this._savedRotation.x + Math.sin(t * Math.PI) * 0.05;
                }
                break;
            }

            case 'subtle_nod': {
                // Nod = chin dips TOWARD chest. With inverted head convention,
                // chin-down = NEGATIVE X. Keep the sinusoid so it recovers.
                const nod = Math.sin(t * Math.PI * 2) * 0.12;
                if (boneMode) {
                    this._setBone('head', -nod, 0, 0);
                    this._setBone('neck', -nod * 0.5, 0, 0);
                } else {
                    group.rotation.x = this._savedRotation.x - nod * 0.25;
                }
                break;
            }

            case 'thinking_pose': {
                // Hand to chin. On this rig:
                //   • upper-arm +X  = arm swings FORWARD
                //   • forearm   +X  = elbow BENDS (hand toward face)
                //   • head      -X  = chin tips DOWN
                const ease = Math.sin(t * Math.PI);
                if (boneMode) {
                    this._setBone('rightUpperArm',  1.3 * ease, -0.2 * ease, 0);
                    this._setBone('rightLowerArm',  2.0 * ease, 0, 0);
                    this._setBone('rightHand',     -0.1 * ease, 0, 0);
                    this._setBone('head',           0.08 * ease, 0.04 * ease, 0.08 * ease);
                    this._setBone('neck',           0.04 * ease, 0.02 * ease, 0.04 * ease);
                } else {
                    group.rotation.x = this._savedRotation.x - 0.05 * ease;
                    group.rotation.z = this._savedRotation.z + 0.03 * ease;
                }
                break;
            }

            case 'hair_touch': {
                // Right arm raises forward then bends elbow so the hand
                // passes next to the head. X-signs follow the inverted
                // convention: +X forward on upper, +X bend on forearm.
                const ease = Math.sin(t * Math.PI);
                if (boneMode) {
                    this._setBone('rightUpperArm',  1.6 * ease, -0.3 * ease, 0);
                    this._setBone('rightLowerArm',  1.6 * ease,  0.3 * ease, 0);
                    this._setBone('rightHand',     -0.1 * ease, 0, 0);
                    this._setBone('head',           0, -0.05 * ease, 0.04 * ease);
                } else {
                    group.rotation.z = this._savedRotation.z + 0.02 * ease;
                }
                break;
            }

            case 'stretch_arms': {
                // Both arms stretch up overhead. Mirror convention on X:
                //   left -X, right +X for symmetric forward swing;
                //   +Z raises right, -Z raises left (each toward T-pose and up).
                const ease = Math.sin(t * Math.PI);
                if (boneMode) {
                    this._setBone('leftUpperArm',  -0.3 * ease, 0,  -1.6 * ease);
                    this._setBone('rightUpperArm',  0.3 * ease, 0,   1.6 * ease);
                    this._setBone('leftLowerArm',   0.2 * ease, 0,  0);
                    this._setBone('rightLowerArm',  0.2 * ease, 0,  0);
                    this._setBone('chest',          0.1 * ease, 0,  0);
                    this._setBone('head',           0.08 * ease, 0, 0);
                } else {
                    group.position.y = this._savedPosition.y + 0.02 * ease;
                }
                break;
            }

            case 'wave_hand': {
                // Raise RIGHT arm overhead for a wave.
                // Right arm rest z = -1.25 (A-pose down). Z=0 is T-pose
                // (horizontal lateral). To get arm OVERHEAD we need final
                // z ≈ +π/2 ≈ +1.57, so delta z ≈ +2.8. With z=+0.5 before,
                // the arm was barely at horizontal — that's why the wave
                // looked like a sideways stretch, not a proper overhead wave.
                const raise = Math.sin(Math.min(t / 0.3, 1) * Math.PI * 0.5);
                const wave  = Math.sin(t * Math.PI * 6);
                const out   = t < 0.8 ? 1 : (1 - (t - 0.8) / 0.2);
                if (boneMode) {
                    // Upper arm: overhead raise (+Z 2.7) + slight forward (+X)
                    this._setBone('rightUpperArm',  0.35 * raise * out, 0,  2.7 * raise * out);
                    // Forearm: bend elbow so hand waves by the side of the head
                    this._setBone('rightLowerArm',  0.9 * raise * out, wave * 0.35 * out, 0);
                    this._setBone('rightHand',      0, wave * 0.5 * out, 0);
                    this._setBone('head',           0, 0.08 * raise * out, 0);
                } else {
                    group.rotation.y = this._savedRotation.y + wave * 0.02 * out;
                }
                break;
            }

            case 'shy_look_away': {
                // Shy = look DOWN and away. On this rig head/neck -X = chin down.
                const ease = Math.sin(t * Math.PI);
                if (boneMode) {
                    this._setBone('head',  -0.15 * ease,  0.22 * ease, -0.08 * ease);
                    this._setBone('neck',  -0.07 * ease,  0.10 * ease, -0.04 * ease);
                    this._setBone('chest', -0.04 * ease,  0.04 * ease, 0);
                    this._setBone('leftShoulder',  -0.12 * ease, 0, 0);
                    this._setBone('rightShoulder', -0.12 * ease, 0, 0);
                } else {
                    group.rotation.x = this._savedRotation.x + 0.03 * ease;
                    group.rotation.y = this._savedRotation.y + 0.1 * ease;
                }
                break;
            }

            case 'pc_focus_idle': {
                // Desk focus pose: quiet workstation presence without hand IK.
                // The body reads as attentive while avoiding keyboard/finger
                // contact that would look uncanny without a baked animation.
                const enter = Math.min(t / 0.18, 1);
                const exit = t > 0.86 ? Math.max(0, 1 - (t - 0.86) / 0.14) : 1;
                const ease = (enter * enter * (3 - 2 * enter)) * exit;
                const breath = Math.sin(elapsed * 1.35) * 0.012 * ease;
                const micro = Math.sin(elapsed * 0.72) * 0.010 * ease;
                if (boneMode) {
                    this._setBone('hips',  -0.012 * ease, 0, 0);
                    this._setBone('spine', -0.028 * ease + breath, 0.010 * micro, 0);
                    this._setBone('chest', -0.020 * ease + breath, 0.014 * micro, 0);
                    this._setBone('neck',  -0.018 * ease, 0.035 * ease + micro, 0);
                    this._setBone('head',  -0.038 * ease, 0.070 * ease + micro, -0.006 * ease);
                    this._setBone('leftUpperArm',  -0.08 * ease, 0, -0.015 * ease);
                    this._setBone('rightUpperArm',  0.08 * ease, 0,  0.015 * ease);
                    this._setBone('leftLowerArm',  -0.06 * ease, 0, 0);
                    this._setBone('rightLowerArm',  0.06 * ease, 0, 0);
                } else {
                    group.rotation.x = this._savedRotation.x - 0.018 * ease;
                    group.rotation.y = this._savedRotation.y + 0.045 * ease;
                }
                break;
            }

            case 'pc_seated_idle': {
                // Fallback only: keep this procedural PC seat pose available
                // while the verified Blender/VRMA desk animation is polished.
                // Do not delete it; if a future clip fails to load, IRIS can
                // still sit near the PC without breaking the interaction.
                // Seated workstation idle. This is intentionally conservative:
                // it sits IRIS into the chair and suggests focused computer use
                // through posture and tiny hand pulses, without pretending to do
                // precise keyboard IK.
                // This pose can run for a long time, so the entry must be based
                // on real seconds rather than normalized pose progress.
                const poseSeconds = this.poseTimer / 1000;
                const enter = Math.min(poseSeconds / 0.85, 1);
                const exit = this.poseTimer > this.poseDuration - 800
                    ? Math.max(0, (this.poseDuration - this.poseTimer) / 800)
                    : 1;
                const ease = (enter * enter * (3 - 2 * enter)) * exit;
                const breath = Math.sin(elapsed * 1.25) * 0.010 * ease;
                const handPulse = Math.sin(elapsed * 5.6) * 0.035 * ease;
                const attention = Math.sin(elapsed * 0.55) * 0.010 * ease;

                group.position.y = this._savedPosition.y - 0.62 * ease;

                if (boneMode) {
                    // Conservative seated lower body. Keep this intentionally
                    // under-posed until we have a verified VRMA/Blender clip;
                    // Carlotta's skirt and accessory rig exaggerate full bends.
                    this._setBone('hips',          -0.20 * ease, 0, 0);
                    this._setBone('leftUpperLeg',   1.46 * ease, 0, 0.022 * ease);
                    this._setBone('rightUpperLeg',  1.46 * ease, 0, -0.022 * ease);
                    this._setBone('leftLowerLeg',  -1.50 * ease, 0, 0);
                    this._setBone('rightLowerLeg', -1.50 * ease, 0, 0);
                    this._setBone('leftFoot',       0.30 * ease, 0, 0);
                    this._setBone('rightFoot',      0.30 * ease, 0, 0);

                    // Upper body leans toward the monitor, then breathes.
                    this._setBone('spine', -0.050 * ease + breath, 0.010 * attention, 0);
                    this._setBone('chest', -0.038 * ease + breath, 0.012 * attention, 0);
                    this._setBone('neck',  -0.020 * ease, 0, 0);
                    this._setBone('head',  -0.028 * ease, 0, 0);

                    // Resting near keyboard. Small pulses imply activity without
                    // forcing fingers onto exact key positions.
                    this._setBone('leftUpperArm',  -0.62 * ease, -0.03 * ease, -0.04 * ease);
                    this._setBone('rightUpperArm',  0.62 * ease,  0.03 * ease,  0.04 * ease);
                    this._setBone('leftLowerArm',  -0.78 * ease + handPulse * 0.25, 0.05 * ease, 0);
                    this._setBone('rightLowerArm',  0.78 * ease - handPulse * 0.25, -0.05 * ease, 0);
                    this._setBone('leftHand',       0.34 * ease + handPulse * 0.25, 0.02 * ease, 0);
                    this._setBone('rightHand',     -0.34 * ease - handPulse * 0.25, -0.02 * ease, 0);
                } else {
                    group.rotation.x = this._savedRotation.x - 0.04 * ease;
                }
                break;
            }

            case 'sit_chair': {
                // VRM path: walk to desk, lean forward from the TORSO (hips
                // + spine + chest) without bending the legs. The model has
                // no skirt bones, so any leg rotation clips the skirt into
                // the thighs. Limiting motion to the torso keeps the silhouette
                // clean while still reading as "interacting with the desk".
                if (boneMode) {
                    const chairSpot = new THREE.Vector3(1.8, this._savedPosition.y, -2.0);
                    const restYaw = this.avatar?.avatar?.restYaw ?? 0;

                    if (t < 0.25) {
                        // Walk to the chair — only subtle leg swing, kept
                        // small so the skirt doesn't flare into the body.
                        const walkT = t / 0.25;
                        group.position.lerpVectors(this._savedPosition, chairSpot, walkT);
                        const dir = new THREE.Vector3().subVectors(chairSpot, this._savedPosition);
                        const heading = Math.atan2(dir.x, dir.z);
                        group.rotation.y = THREE.MathUtils.lerp(
                            this._savedRotation.y, restYaw + heading, walkT
                        );
                        const phase = elapsed * 6;
                        const s = Math.sin(phase);
                        const swing = walkT < 0.5 ? walkT * 2 : (1 - walkT) * 2;
                        this._setBone('leftUpperLeg',   s * 0.25 * swing, 0, 0);
                        this._setBone('rightUpperLeg', -s * 0.25 * swing, 0, 0);
                        this._setBone('leftLowerLeg',   Math.max(0, -s) * 0.3 * swing, 0, 0);
                        this._setBone('rightLowerLeg',  Math.max(0,  s) * 0.3 * swing, 0, 0);
                        this._setBone('leftUpperArm',   s * 0.25 * swing, 0, 0);
                        this._setBone('rightUpperArm',  s * 0.25 * swing, 0, 0);
                    } else if (t < 0.75) {
                        // Torso-only lean (NO legs). Character stays standing
                        // but bends forward from the hips to "examine" the desk.
                        const leanT = (t - 0.25) / 0.15;
                        const e = 1 - (1 - Math.min(leanT, 1)) * (1 - Math.min(leanT, 1));
                        group.position.copy(chairSpot);
                        const dir = new THREE.Vector3().subVectors(chairSpot, this._savedPosition);
                        group.rotation.y = restYaw + Math.atan2(dir.x, dir.z);
                        // Torso bends forward ~30° (torso +X = forward lean on
                        // this rig, same as head -X = look-down). Neck/head
                        // counter-rotate so the face keeps looking ahead.
                        this._setBone('hips',         -0.25 * e, 0, 0);
                        this._setBone('spine',        -0.18 * e, 0, 0);
                        this._setBone('chest',        -0.10 * e, 0, 0);
                        this._setBone('neck',          0.12 * e, 0, 0);
                        this._setBone('head',          0.08 * e, 0, 0);
                        // Arms reach forward. Mirrored convention on X:
                        //   left arm forward = -X, right arm forward = +X.
                        this._setBone('leftUpperArm',  -0.5 * e, 0, 0);
                        this._setBone('rightUpperArm',  0.5 * e, 0, 0);
                        this._setBone('leftLowerArm',   0.4 * e, 0, 0);
                        this._setBone('rightLowerArm',  0.4 * e, 0, 0);
                    } else {
                        // Stand up and walk back — blend torso bones back
                        const walkT = (t - 0.75) / 0.25;
                        const kEase = (1 - walkT) * (1 - walkT);
                        group.position.lerpVectors(chairSpot, this._savedPosition, walkT);
                        const dir = new THREE.Vector3().subVectors(this._savedPosition, chairSpot);
                        const heading = Math.atan2(dir.x, dir.z);
                        group.rotation.y = THREE.MathUtils.lerp(
                            restYaw + heading, this._savedRotation.y, walkT
                        );
                        const phase = elapsed * 6;
                        const s = Math.sin(phase) * walkT;
                        const w = 1 - kEase;
                        this._setBone('leftUpperLeg',   s * 0.25 * w, 0, 0);
                        this._setBone('rightUpperLeg', -s * 0.25 * w, 0, 0);
                        this._setBone('leftLowerLeg',  Math.max(0, -s) * 0.3 * w, 0, 0);
                        this._setBone('rightLowerLeg', Math.max(0,  s) * 0.3 * w, 0, 0);
                        this._setBone('hips',         -0.25 * kEase, 0, 0);
                        this._setBone('spine',        -0.18 * kEase, 0, 0);
                        this._setBone('chest',        -0.10 * kEase, 0, 0);
                        this._setBone('neck',          0.12 * kEase, 0, 0);
                        this._setBone('head',          0.08 * kEase, 0, 0);
                        this._setBone('leftUpperArm', -0.5 * kEase - s * 0.35 * w, 0, 0);
                        this._setBone('rightUpperArm', 0.5 * kEase + s * 0.35 * w, 0, 0);
                        this._setBone('leftLowerArm',  0.4 * kEase, 0, 0);
                        this._setBone('rightLowerArm', 0.4 * kEase, 0, 0);
                    }
                } else {
                    // Procedural fallback keeps the original cinematic
                    const chairPos = new THREE.Vector3(2.0, -0.1, -2.5);
                    const chairRot = -Math.PI / 4 + Math.PI;
                    if (t < 0.2) {
                        const walkT = t / 0.2;
                        group.position.lerpVectors(this._savedPosition, chairPos, walkT);
                        group.rotation.y = THREE.MathUtils.lerp(this._savedRotation.y, chairRot, walkT);
                    } else if (t < 0.75) {
                        group.position.copy(chairPos);
                        group.position.y = -0.25;
                        group.rotation.y = chairRot;
                        group.rotation.x = -0.08;
                        group.position.y += Math.sin(elapsed * 1.2) * 0.003;
                    } else {
                        const standT = (t - 0.75) / 0.25;
                        group.position.lerpVectors(chairPos, this._savedPosition, standT);
                        group.position.y = THREE.MathUtils.lerp(-0.25, this._savedPosition.y, standT);
                        group.rotation.x = THREE.MathUtils.lerp(-0.08, this._savedRotation.x, standT);
                        group.rotation.y = THREE.MathUtils.lerp(chairRot, this._savedRotation.y, standT);
                    }
                }
                break;
            }

            // ── Custom scene-interaction movements ──────────
            // Pattern for all: WALK in (0..0.25) → ACT (0.25..0.75) → WALK back (0.75..1).
            // Uses the torso-only lean pattern from sit_chair for
            // clipping-free motion on any rig.

            case 'walk_tour': {
                // Walk a gentle arc around the room center and return.
                // Shows the full walk-cycle without targeting a room object.
                const restYaw = this.avatar?.avatar?.restYaw ?? 0;
                const center = this._savedPosition;
                // Arc: go to a point 1.5m in front-left, then to 1.5m front-right, then home.
                const a = new THREE.Vector3(center.x - 1.2, center.y, center.z - 0.8);
                const b = new THREE.Vector3(center.x + 1.2, center.y, center.z - 0.8);

                let from, to;
                if (t < 0.35)       { from = center; to = a; }
                else if (t < 0.70)  { from = a;      to = b; }
                else                { from = b;      to = center; }

                const segT = t < 0.35 ? t / 0.35
                           : t < 0.70 ? (t - 0.35) / 0.35
                           :            (t - 0.70) / 0.30;

                group.position.lerpVectors(from, to, segT);
                const dir = new THREE.Vector3().subVectors(to, from);
                if (dir.lengthSq() > 1e-6) {
                    const heading = Math.atan2(dir.x, dir.z);
                    group.rotation.y = THREE.MathUtils.lerp(
                        group.rotation.y, restYaw + heading, delta * 5
                    );
                }

                if (boneMode) {
                    // Natural biped walk: slower cadence, counter-twisting
                    // chest, pelvis roll, vertical hip bob.
                    const phase = elapsed * 5.2;
                    const s = Math.sin(phase);
                    const bob = Math.cos(phase * 2);
                    const twist = Math.sin(phase - 0.4);
                    this._setBone('leftUpperLeg',   s * 0.5,  0, 0);
                    this._setBone('rightUpperLeg', -s * 0.5,  0, 0);
                    this._setBone('leftLowerLeg',   Math.max(0, -s) * 0.7, 0, 0);
                    this._setBone('rightLowerLeg',  Math.max(0,  s) * 0.7, 0, 0);
                    this._setBone('leftUpperArm',   s * 0.32, 0, 0);
                    this._setBone('rightUpperArm',  s * 0.32, 0, 0);
                    this._setBone('leftLowerArm',   Math.max(0, -s) * 0.22, 0, 0);
                    this._setBone('rightLowerArm',  Math.max(0,  s) * 0.22, 0, 0);
                    this._setBone('hips',   0, 0, -s * 0.05);
                    this._setBone('spine',  0, twist * 0.07, s * 0.025);
                    this._setBone('chest',  0, twist * 0.10, s * 0.02);
                    this._setBone('neck',   0, -twist * 0.035, 0);
                    this._setBone('head',   0, -twist * 0.045, 0);
                    group.position.y = this._savedPosition.y + bob * 0.012;
                }
                break;
            }

            case 'greet': {
                // Standing wave facing camera. On this rig: upper-arm +X =
                // forward (not back), forearm +X = elbow bend.
                // Z axis raises the arm up from A-pose (+ on right, - on left).
                const raise = Math.sin(Math.min(t / 0.2, 1) * Math.PI * 0.5);
                const wave  = Math.sin(t * Math.PI * 7);
                const out   = t < 0.85 ? 1 : Math.max(0, 1 - (t - 0.85) / 0.15);
                if (boneMode) {
                    // Same correction as wave_hand — delta Z of +2.7 brings
                    // right arm overhead (final z ≈ +1.45), not just horizontal.
                    this._setBone('rightUpperArm',  0.4 * raise * out,  0.1 * raise * out,  2.7 * raise * out);
                    this._setBone('rightLowerArm',  0.8 * raise * out,  wave * 0.5 * out,   0);
                    this._setBone('rightHand',      0,                  wave * 0.5 * out,   0);
                    this._setBone('head',           0, 0.08 * raise * out, 0.04 * raise * out);
                    this._setBone('chest',          0, 0.05 * raise * out, 0);
                } else {
                    group.rotation.y = this._savedRotation.y + wave * 0.03 * out;
                }
                break;
            }

            case 'lie_bed': {
                // For VRM, replace the "fly horizontal" cinematic with a
                // tired stretch + yawn (head back, arms up, slight knee bend).
                if (boneMode) {
                    const ease = Math.sin(t * Math.PI);
                    const yawn = Math.sin(t * Math.PI * 2) * 0.5 + 0.5; // 0..1
                    // Arms stretch overhead — mirrored X, symmetric Z to raise
                    this._setBone('leftUpperArm',  -0.4 * ease, 0, -1.6 * ease);
                    this._setBone('rightUpperArm',  0.4 * ease, 0,  1.6 * ease);
                    this._setBone('leftLowerArm',   0.4 * ease, 0, 0);
                    this._setBone('rightLowerArm',  0.4 * ease, 0, 0);
                    // Head tips BACK for yawn (+X = look up)
                    this._setBone('head',           0.3 * ease, 0, 0);
                    this._setBone('neck',           0.15 * ease, 0, 0);
                    this._setBone('chest',          0.08 * ease, 0, 0);
                    // Legs stay (small backward kick for stretch look)
                    this._setBone('leftUpperLeg',  -0.15 * ease, 0, 0);
                    this._setBone('rightUpperLeg', -0.15 * ease, 0, 0);
                    // Open mouth for the yawn at mid-pose
                    if (this.avatar?.avatar?.setMouthOpen) {
                        this.avatar.avatar.setMouthOpen(yawn * ease * 0.6);
                    }
                } else {
                    const bedPos = new THREE.Vector3(3.0, 0, 1.5);
                    const lyingPos = new THREE.Vector3(3.0, -0.65, 1.5);
                    if (t < 0.15) {
                        const walkT = t / 0.15;
                        group.position.lerpVectors(this._savedPosition, bedPos, walkT);
                        group.rotation.y = THREE.MathUtils.lerp(this._savedRotation.y, -Math.PI / 2, walkT);
                    } else if (t < 0.25) {
                        const lieT = (t - 0.15) / 0.1;
                        group.position.lerpVectors(bedPos, lyingPos, lieT);
                        group.rotation.x = THREE.MathUtils.lerp(0, -Math.PI / 2, lieT);
                        group.rotation.y = -Math.PI / 2;
                    } else if (t < 0.8) {
                        group.position.copy(lyingPos);
                        group.rotation.x = -Math.PI / 2;
                        group.rotation.y = -Math.PI / 2;
                        group.position.y = lyingPos.y + Math.sin(elapsed * 0.8) * 0.005;
                    } else if (t < 0.9) {
                        const upT = (t - 0.8) / 0.1;
                        group.position.lerpVectors(lyingPos, bedPos, upT);
                        group.rotation.x = THREE.MathUtils.lerp(-Math.PI / 2, 0, upT);
                    } else {
                        const returnT = (t - 0.9) / 0.1;
                        group.position.lerpVectors(bedPos, this._savedPosition, returnT);
                        group.rotation.y = THREE.MathUtils.lerp(-Math.PI / 2, this._savedRotation.y, returnT);
                    }
                }
                break;
            }
        }

        // Smoothly interpolate bone transforms toward targets (only affects
        // poses that touch bones; group-only poses skip this path trivially).
        if (boneMode) this._applyBoneTargets(Math.min(delta * 10, 1));
    }

    _endPose(group) {
        // Reset mouth (lie_bed yawn leaves it open)
        if (this.avatar?.avatar?.setMouthOpen) {
            this.avatar.avatar.setMouthOpen(0);
        }

        this.currentPose = null;
        this._manualPoseActive = false;
        this.poseTimer = 0;
        this._vrmaPoseManagedByMixer = null;

        // If a VRMA was driving, fade it out. The mixer stops writing to
        // bones after the fade and our procedural system resumes control.
        if (this._vrmaActive) {
            this._detachVrmaFinishedHandler();
            this._vrmaPlayer?.stop(0.25);
            this._vrmaActive = false;
            this._vrmaExternalClip = null;
        }
        // Keep ownership through the smooth root/bone return below. The
        // update loop releases blink/micro-motion once both transitions end.

        this._returningToIdle = true;
        this._returnTimer = 0;
        this._returnFromPos.copy(group.position);
        this._returnFromRot.copy(group.rotation);

        this._snapshotBonesForReturn();
    }
    dispose() {
        if (this._disposed) return;
        this._disposed = true;
        this._generation++;
        this._manifestGeneration++;
        this._manifestRequest = null;
        this.refreshLibrary = null;
        this.active = false;
        this._manifestAbort.abort();
        this._detachVrmaFinishedHandler();
        this._setAnimationBlinkSuppressed(false);
        this._vrmaPlayer?.dispose();
        this._vrmaPlayer = null;
        this._vrmaManifest = null;
        this._vrmaExternalClip = null;
        this._vrmaPoseManagedByMixer = null;
        this._activityClip = null;
        this._boneRefs = null;
        this._boneRest = null;
        this._boneTargets = null;
        this._boneReturnFrom = null;
        this.avatar = null;
    }

}

