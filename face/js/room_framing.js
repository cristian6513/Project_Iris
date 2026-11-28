/** Inspection framing only; leaves the conversation camera and avatar targets alone. */
export const ROOM_DETAIL_PRESETS = Object.freeze({
    isometricView: Object.freeze({ label: 'Iso', position: [6.2, 3.5, 10.4], target: [0, -0.12, -0.55], fov: 44, duration: 720, minimumAspect: 1.5 }),
    // Chosen so the whole modelled sofa / bed stays in frame from 16:9 down to portrait
    // (narrower viewports back the camera off through minimumAspect, then widen the lens).
    sofaView: Object.freeze({ label: 'Sofa', position: [-2.4, 1.5, 4.5], target: [-3.3, -0.65, 1.3], fov: 42, duration: 680, minimumAspect: 1.5 }),
    bedView: Object.freeze({ label: 'Bed', position: [-0.5, 1.6, 3.3], target: [3.7, -0.65, 1.97], fov: 42, duration: 680, minimumAspect: 1.5 }),
});

/**
 * Where a camera can stand in the room (room.js; its group has no transform): inside the side
 * walls (inner faces x ±5.04), in front of the window glass (z -4.62), under the ceiling ribs
 * (y 2.95) and over the floor (y -1.2), with room for the near plane. The room has no front
 * wall: there the limit is the home camera's own depth (z 4.95); further out, a view looking
 * down shows the void past the floor's edge (z 4.15).
 */
export const ROOM_CAMERA_BOUNDS = Object.freeze({
    min: Object.freeze([-4.8, -0.9, -4.4]),
    max: Object.freeze([4.8, 2.75, 4.95]),
});
// The shell itself: a view placed beyond it is an outside view.
const ROOM_SHELL = Object.freeze({ min: Object.freeze([-5.04, -1.2, -4.62]), max: Object.freeze([5.04, 3.05, 4.95]) });
// The widest lens a framing may open: the corners of the frame 45° off its axis. The normal
// 45° camera in 16:9 has them at 40°; a 9:16 screen reaches 82° vertically, 50° across.
const MAX_HALF_DIAGONAL = Math.PI / 4;
const DEFAULT_FOV = 45;
const DEG = Math.PI / 180;

const inside = (point, { min, max }) => point.every((value, i) => value >= min[i] && value <= max[i]);

/**
 * How many times its own distance a view can back off along its line before it leaves the
 * room; never less than 1, where it was placed. A view placed outside the room on purpose
 * (the Iso overview looks in through the open front) has no walls behind it: Infinity.
 */
function roomReach(position, target, bounds) {
    if (!inside(position, ROOM_SHELL)) return { reach: Infinity, limits: null };
    const limits = {
        min: position.map((value, i) => Math.min(bounds.min[i], value)),
        max: position.map((value, i) => Math.max(bounds.max[i], value)),
    };
    let reach = Infinity;
    position.forEach((value, i) => {
        const offset = value - target[i];
        if (Math.abs(offset) < 1e-9) return;
        reach = Math.min(reach, ((offset > 0 ? limits.max[i] : limits.min[i]) - target[i]) / offset);
    });
    return { reach, limits };
}

/** `point` moved onto the room's camera bounds, axis by axis. */
export function clampToRoom(point, bounds = ROOM_CAMERA_BOUNDS) {
    return point.map((value, i) => Math.min(bounds.max[i], Math.max(bounds.min[i], value)));
}

/**
 * A view framed for `aspect`. Narrower than `minimumAspect`, the camera backs off along its
 * line so the same width around the target still fits. A view inside the room backs off only
 * as far as the walls, the ceiling, the floor and the home camera's depth allow; the rest a
 * wider lens makes up (`fov`, vertical degrees), up to MAX_HALF_DIAGONAL. In 16:9 nothing
 * changes. On a 9:16 screen the desk view alone used to end up behind the walls (QA30).
 */
export function frameRoomPreset(preset, aspect, { fov = preset.fov ?? DEFAULT_FOV, bounds = ROOM_CAMERA_BOUNDS } = {}) {
    const ratio = Number.isFinite(aspect) && aspect > 0 ? Math.max(0.35, aspect) : 1.5;
    const minimumAspect = preset.minimumAspect ?? 1.0;
    const wanted = Math.max(1, minimumAspect / ratio);
    const { reach, limits } = roomReach(preset.position, preset.target, bounds);
    const distance = Math.min(wanted, reach);
    const position = distance === 1 ? [...preset.position]
        : preset.position.map((value, i) => preset.target[i] + (value - preset.target[i]) * distance);
    let framedFov = fov;
    if (distance < wanted) {
        // The same half-width at her distance: tan(half fov) · aspect · distance stays put.
        const tangent = Math.tan(fov * DEG / 2) * wanted / distance;
        const widest = Math.max(Math.tan(fov * DEG / 2), Math.tan(MAX_HALF_DIAGONAL) / Math.hypot(1, ratio));
        framedFov = 2 * Math.atan(Math.min(tangent, widest)) / DEG;
    }
    return {
        ...preset,
        // Exactly on a wall it stopped at, not a rounding error past it.
        position: limits ? clampToRoom(position, limits) : position,
        target: [...preset.target],
        fov: framedFov,
    };
}

const CONVERSATION_HOME = Object.freeze({ position: [0.34, 1.38, 4.95], target: [0.08, 0.66, 0] });
// room_avatar_zoom.js: 1 + 100 * 0.015 at the end of the slider.
const MAX_AVATAR_ZOOM = 2.5;
const SPOT_VIEW_SECONDS = 3;

/** The camera outside Explorar: home with a slow drift, or the view of the spot she goes to. */
export class ConversationCamera {
    constructor(camera) {
        this.camera = camera;
        this.blend = 0;
        this.view = null;
        // The lens at home. A spot framed on a narrow screen may open it (frameRoomPreset);
        // it closes again on the way home.
        this.homeFov = camera.fov;
    }

    /** Home at once: she has just been placed there. */
    reset() {
        this.blend = 0;
        this.view = null;
    }

    /**
     * `focus`: her head in the world, when known. The avatar zoom (camera.zoom, Configuración
     * rápida) closes in on the point the camera looks at; that point moves towards her head as
     * the zoom grows, all the way at the most (2.5x). A view framed on her whole body otherwise
     * lost her head first, and the zoom never followed her when she walked.
     *
     * `closeUp`: how far the «Mimos» close-up (touch_reactions.js, 0-1) has come. It frames her
     * face with the normal lens: a wider one this close would bulge it.
     */
    update(dt, t, spotView, focus = null, closeUp = 0) {
        if (spotView) this.view = spotView;
        this.blend = Math.min(1, Math.max(0, this.blend + (spotView ? dt : -dt) / SPOT_VIEW_SECONDS));
        const ease = this.blend * this.blend * (3 - 2 * this.blend);
        const framed = this.view
            ? frameRoomPreset(this.view, this.camera.aspect, { fov: this.homeFov })
            : { ...CONVERSATION_HOME, fov: this.homeFov };
        const mix = (from, to, amount = ease) => from.map((value, i) => value + (to[i] - value) * amount);
        const via = this.view?.via;
        const arc = (from, to) => from.map((value, i) => (1 - ease) ** 2 * value + 2 * ease * (1 - ease) * via[i] + ease ** 2 * to[i]);
        const [x, y, z] = CONVERSATION_HOME.position;
        const home = [x + Math.sin(t * 0.06) * 0.08, y + Math.sin(t * 0.04) * 0.025, z];
        // `via` may stand beyond a wall (it only bends the path): the camera itself stays inside.
        this.camera.position.set(...clampToRoom(via ? arc(home, framed.position) : mix(home, framed.position)));
        const near = Math.min(1, Math.max(0, closeUp));
        const fov = this.homeFov + (framed.fov - this.homeFov) * ease * (1 - near * near * (3 - 2 * near));
        if (this.camera.fov !== fov) {
            this.camera.fov = fov;
            this.camera.updateProjectionMatrix();
        }
        // On an arc she has to stay in frame: the camera turns to her ahead of arriving.
        const target = mix(CONVERSATION_HOME.target, framed.target, via ? Math.min(1, ease * 1.8) : ease);
        const pull = focus ? Math.min(1, Math.max(0, ((this.camera.zoom || 1) - 1) / (MAX_AVATAR_ZOOM - 1))) : 0;
        this.camera.lookAt(...(pull > 0 ? mix(target, focus, pull) : target));
    }
}
