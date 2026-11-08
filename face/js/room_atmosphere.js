/** Manual lighting only: no clock, audio, permissions or network side effects.
 *
 * Most of what the room shows is unlit (textures, LED coves, the city window), so the lights
 * alone barely changed the picture (qa11: «no veo cambios», Noche dimmed it about 5%). Each
 * preset therefore also scales the room's surfaces by area: the colour of unlit materials and
 * the glow of emissive ones. The avatar never changes, and neither do screens.
 */
export const ROOM_ATMOSPHERE_KEY = 'IRIS.roomAtmosphere.v1';
const preset = (label, description, weights, surfaces) => Object.freeze({
    label, description, weights: Object.freeze(weights), surfaces: Object.freeze(surfaces),
});
export const ROOM_ATMOSPHERES = Object.freeze({
    original: preset('Original', 'Iluminación original del tema.',
        { ambient: 1, desk: 1, lounge: 1, bed: 1, accent: 1, avatar: 1 },
        { ambient: 1, desk: 1, lounge: 1, bed: 1, accent: 1, window: 1, avatar: 1 }),
    work: preset('Trabajo', 'Más luz en el escritorio, descanso discreto.',
        { ambient: 1.1, desk: 1.45, lounge: 0.7, bed: 0.65, accent: 0.8, avatar: 1 },
        { ambient: 1.15, desk: 1.8, lounge: 0.6, bed: 0.5, accent: 0.85, window: 1, avatar: 1 }),
    relax: preset('Descanso', 'Luz centrada en sofá y cama; escritorio tenue.',
        { ambient: 0.8, desk: 0.55, lounge: 1.2, bed: 1.1, accent: 0.55, avatar: 1 },
        { ambient: 0.68, desk: 0.34, lounge: 1.3, bed: 1.25, accent: 0.58, window: 0.8, avatar: 1 }),
    night: preset('Noche', 'Luz ambiental tenue, sin cambiar la hora o el sonido.',
        { ambient: 0.45, desk: 0.3, lounge: 0.35, bed: 0.55, accent: 0.25, avatar: 1 },
        { ambient: 0.3, desk: 0.24, lounge: 0.28, bed: 0.42, accent: 0.36, window: 0.7, avatar: 1 }),
});
export const isRoomAtmosphere = value => typeof value === 'string' && Object.hasOwn(ROOM_ATMOSPHERES, value);

export class RoomAtmosphere {
    constructor({ storage = () => globalThis.localStorage, reducedMotion = null } = {}) {
        this._storage = storage;
        const motionQuery = globalThis.matchMedia?.('(prefers-reduced-motion: reduce)');
        this._reducedMotion = reducedMotion || (() => motionQuery?.matches === true);
        this.mode = 'original';
        this.persisted = null;
        try {
            const saved = storage()?.getItem(ROOM_ATMOSPHERE_KEY);
            if (isRoomAtmosphere(saved)) { this.mode = saved; this.persisted = true; }
        } catch { /* Keep the scene usable when storage is blocked. */ }
        this._weights = { ...ROOM_ATMOSPHERES[this.mode].weights };
        this._surfaceWeights = { ...ROOM_ATMOSPHERES[this.mode].surfaces };
        this._lights = new Map();
        this._surfaces = new Map();
        this._listeners = new Set();
        this._transition = null;
    }

    register(light, role = 'ambient') {
        if (!light || !Number.isFinite(light.intensity)) return;
        if (this._lights.has(light)) return; // Never capture an already scaled value.
        this._lights.set(light, { base: light.intensity, role: Object.hasOwn(this._weights, role) ? role : 'ambient' });
        this._apply();
    }

    setBaseIntensity(light, value) {
        if (!light || !Number.isFinite(value) || value < 0) return false;
        const entry = this._lights.get(light);
        if (entry) { entry.base = value; light.intensity = value * this._weights[entry.role]; }
        else light.intensity = value;
        return true;
    }

    /** A room material: unlit ones scale their colour, lit ones their emissive glow. A material
     * shared by two areas follows the room's ambient weight. */
    registerSurface(material, role = 'ambient') {
        if (!material) return;
        const known = this._surfaces.get(material);
        if (known) {
            if (known.role !== role) known.role = 'ambient';
            this._applySurface(material, known);
            return;
        }
        // A material without glow yet keeps its area: a texture may give it one later.
        const entry = { role: Object.hasOwn(this._surfaceWeights, role) ? role : 'ambient', kind: null };
        this._capture(material, entry);
        this._surfaces.set(material, entry);
        this._applySurface(material, entry);
    }

    /** The current weight of a material's area, for code that animates its glow. */
    surfaceWeight(material) {
        const entry = this._surfaces.get(material);
        return entry ? this._surfaceWeights[entry.role] : 1;
    }

    /** A new glow decided elsewhere (a texture arriving later) becomes the unscaled value. */
    setSurfaceEmissive(material, value) {
        if (!material || !Number.isFinite(value) || value < 0) return false;
        const entry = this._surfaces.get(material);
        if (entry && entry.kind !== 'color') {
            entry.kind = 'emissive';
            entry.base = value;
            this._applySurface(material, entry);
        } else material.emissiveIntensity = value;
        return true;
    }

    /** Run a theme change on unscaled surfaces, then scale its new colours. */
    rebaseSurfaces(change) {
        for (const [material, entry] of this._surfaces) this._restore(material, entry);
        try {
            change();
        } finally {
            for (const [material, entry] of this._surfaces) {
                this._capture(material, entry);
                this._applySurface(material, entry);
            }
        }
    }

    snapshot() {
        return Object.freeze({ mode: this.mode, persisted: this.persisted, ...ROOM_ATMOSPHERES[this.mode] });
    }

    subscribe(listener) {
        this._listeners.add(listener);
        listener(this.snapshot());
        return () => this._listeners.delete(listener);
    }

    setMode(mode, { immediate = false } = {}) {
        if (!isRoomAtmosphere(mode)) return false;
        this.mode = mode;
        try {
            const storage = this._storage();
            if (!storage) throw Error('Storage unavailable');
            storage.setItem(ROOM_ATMOSPHERE_KEY, mode);
            this.persisted = true;
        } catch { this.persisted = false; }
        this._transition = { from: { ...this._weights }, fromSurfaces: { ...this._surfaceWeights }, elapsed: 0 };
        if (immediate || this._reducedMotion()) this.update(1);
        for (const listener of this._listeners) {
            try { listener(this.snapshot()); } catch { /* An optional UI cannot stop lighting. */ }
        }
        return true;
    }

    update(delta) {
        const transition = this._transition;
        if (!transition) return;
        transition.elapsed += Number.isFinite(delta) ? Math.max(0, Math.min(1, delta)) : 0;
        const t = this._reducedMotion() ? 1 : Math.min(1, transition.elapsed / 0.65);
        const eased = t * t * (3 - 2 * t), { weights, surfaces } = ROOM_ATMOSPHERES[this.mode];
        const blend = (current, from, target) => {
            for (const role of Object.keys(target)) current[role] = t === 1 ? target[role]
                : from[role] + (target[role] - from[role]) * eased;
        };
        blend(this._weights, transition.from, weights);
        blend(this._surfaceWeights, transition.fromSurfaces, surfaces);
        if (t === 1) this._transition = null;
        this._apply();
    }

    _apply() {
        for (const [light, { base, role }] of this._lights) light.intensity = base * this._weights[role];
        for (const [material, entry] of this._surfaces) this._applySurface(material, entry);
    }

    _capture(material, entry) {
        if (material.isMeshBasicMaterial && material.color) {
            entry.kind = 'color';
            entry.base = { r: material.color.r, g: material.color.g, b: material.color.b };
        } else if (Number.isFinite(material.emissiveIntensity) && material.emissiveIntensity > 0) {
            entry.kind = 'emissive';
            entry.base = material.emissiveIntensity;
        }
    }

    _restore(material, entry) {
        if (entry.kind === 'color') Object.assign(material.color, entry.base);
        else if (entry.kind === 'emissive') material.emissiveIntensity = entry.base;
    }

    _applySurface(material, entry) {
        const weight = this._surfaceWeights[entry.role];
        const rim = material.userData?.furnitureRim;
        if (rim) rim.weight.value = weight;
        if (entry.kind === 'color') {
            material.color.r = entry.base.r * weight;
            material.color.g = entry.base.g * weight;
            material.color.b = entry.base.b * weight;
        } else if (entry.kind === 'emissive') material.emissiveIntensity = entry.base * weight;
    }
}
