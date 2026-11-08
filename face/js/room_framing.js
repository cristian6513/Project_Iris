/** Inspection framing only; leaves the conversation camera and avatar targets alone. */
export const ROOM_DETAIL_PRESETS = Object.freeze({
    isometricView: Object.freeze({ label: 'Iso', position: [6.2, 3.5, 10.4], target: [0, -0.12, -0.55], fov: 44, duration: 720, minimumAspect: 1.5 }),
    // Chosen so the whole modelled sofa / bed stays in frame from 16:9 down to portrait
    // (narrower viewports back the camera off through minimumAspect).
    sofaView: Object.freeze({ label: 'Sofa', position: [-2.4, 1.5, 4.5], target: [-3.3, -0.65, 1.3], fov: 42, duration: 680, minimumAspect: 1.5 }),
    bedView: Object.freeze({ label: 'Bed', position: [-0.5, 1.6, 3.3], target: [3.7, -0.65, 1.97], fov: 42, duration: 680, minimumAspect: 1.5 }),
});

export function frameRoomPreset(preset, aspect) {
    const ratio = Number.isFinite(aspect) && aspect > 0 ? Math.max(0.35, aspect) : 1.5;
    const minimumAspect = preset.minimumAspect ?? 1.0;
    const distance = Math.max(1, minimumAspect / ratio);
    return {
        ...preset,
        position: preset.position.map((value, i) => preset.target[i] + (value - preset.target[i]) * distance),
        target: [...preset.target],
    };
}

const CONVERSATION_HOME = Object.freeze({ position: [0.34, 1.38, 4.95], target: [0.08, 0.66, 0] });
const SPOT_VIEW_SECONDS = 3;

/** The camera outside Explorar: home with a slow drift, or the view of the spot she goes to. */
export class ConversationCamera {
    constructor(camera) {
        this.camera = camera;
        this.blend = 0;
        this.view = null;
    }

    /** Home at once: she has just been placed there. */
    reset() {
        this.blend = 0;
        this.view = null;
    }

    update(dt, t, spotView) {
        if (spotView) this.view = spotView;
        this.blend = Math.min(1, Math.max(0, this.blend + (spotView ? dt : -dt) / SPOT_VIEW_SECONDS));
        const ease = this.blend * this.blend * (3 - 2 * this.blend);
        const framed = this.view ? frameRoomPreset(this.view, this.camera.aspect) : CONVERSATION_HOME;
        const mix = (from, to, amount = ease) => from.map((value, i) => value + (to[i] - value) * amount);
        const via = this.view?.via;
        const arc = (from, to) => from.map((value, i) => (1 - ease) ** 2 * value + 2 * ease * (1 - ease) * via[i] + ease ** 2 * to[i]);
        const [x, y, z] = CONVERSATION_HOME.position;
        const home = [x + Math.sin(t * 0.06) * 0.08, y + Math.sin(t * 0.04) * 0.025, z];
        this.camera.position.set(...(via ? arc(home, framed.position) : mix(home, framed.position)));
        // On an arc she has to stay in frame: the camera turns to her ahead of arriving.
        this.camera.lookAt(...mix(CONVERSATION_HOME.target, framed.target, via ? Math.min(1, ease * 1.8) : ease));
    }
}
