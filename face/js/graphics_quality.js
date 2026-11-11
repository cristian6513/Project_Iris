/** Rendering policy shared by Face and Companion, independent from their FPS cap. */
export const GRAPHICS_QUALITIES = Object.freeze({
    saving: Object.freeze({ label: 'Ahorro', scale: 0.75, maxRatio: 1, samples: 0, bloom: false, avatarTextures: 1024, heavyPmxHz: 32.5 }),
    balanced: Object.freeze({ label: 'Equilibrado', scale: 1, maxRatio: 1.5, samples: 2, bloom: true, avatarTextures: 0, heavyPmxHz: 65 }),
    high: Object.freeze({ label: 'Alta calidad', scale: 1, maxRatio: 2, samples: 4, bloom: true, avatarTextures: 0, heavyPmxHz: 65 }),
});

export function graphicsQuality(value) {
    return Object.hasOwn(GRAPHICS_QUALITIES, value) ? value : 'balanced';
}

/**
 * MMD physics steps per second for a PMX avatar. MMD models are made for 65 (three.js' default).
 * At 32.5 the dense cloth of heavy models stretched 3-4 times more and tore into triangles
 * (Feixue's front panel, Lily's fringe and skirts, 2026-11-10). Only «Ahorro» trades that for
 * less CPU, and only on heavy models (pmx_physics.js `heavy`).
 */
export const PMX_PHYSICS_HZ = 65;

export function pmxPhysicsRate(value, physics) {
    return physics?.heavy ? GRAPHICS_QUALITIES[graphicsQuality(value)].heavyPmxHz : PMX_PHYSICS_HZ;
}

/** Longest side, in pixels, for a VRM's textures when it loads; 0 keeps them as they come. */
export function avatarTextureLimit(value) {
    return GRAPHICS_QUALITIES[graphicsQuality(value)].avatarTextures;
}

export function graphicsPolicy(value, deviceRatio = 1, maxSamples = 4) {
    const quality = graphicsQuality(value), preset = GRAPHICS_QUALITIES[quality];
    const ratio = Number.isFinite(deviceRatio) && deviceRatio > 0 ? deviceRatio : 1;
    const supported = Number.isFinite(maxSamples) ? Math.max(0, maxSamples) : 0;
    return { quality, pixelRatio: Math.max(0.5, Math.min(ratio * preset.scale, preset.maxRatio)),
        samples: Math.min(preset.samples, supported), bloom: preset.bloom };
}

/** Resize existing render targets; never recreate the WebGL context or the VRM. */
export class GraphicsQuality {
    constructor({ getRenderer, getComposer, getBloom = () => null, getRoom = () => null,
        getAvatarPhysics = () => null, getDeviceRatio = () => globalThis.devicePixelRatio || 1,
        getSize = () => [globalThis.innerWidth, globalThis.innerHeight], updateStyle = false } = {}) {
        Object.assign(this, { getRenderer, getComposer, getBloom, getRoom, getAvatarPhysics, getDeviceRatio, getSize, updateStyle });
        this.quality = 'balanced';
        this.applied = null;
    }

    setQuality(value) {
        this.quality = graphicsQuality(value);
        this.applyAvatarPhysics();
        return this.resize();
    }

    /** The PMX avatar's physics rate for this quality, after it loads and on every change (a VRM has none). */
    applyAvatarPhysics() {
        const physics = this.getAvatarPhysics();
        physics?.setRate?.(pmxPhysicsRate(this.quality, physics));
    }

    resize() {
        const renderer = this.getRenderer(), composer = this.getComposer();
        if (!renderer || !composer) return null;
        const policy = graphicsPolicy(this.quality, this.getDeviceRatio(), renderer.capabilities.maxSamples);
        const size = this.getSize();
        const width = Number.isFinite(size[0]) ? Math.max(1, size[0]) : 1;
        const height = Number.isFinite(size[1]) ? Math.max(1, size[1]) : 1;
        for (const target of [composer.renderTarget1, composer.renderTarget2]) {
            if (target.samples !== policy.samples) {
                target.samples = policy.samples;
                target.dispose(); // Drop the old multisample buffers; preserve stencil/depth settings.
            }
        }
        renderer.setPixelRatio(policy.pixelRatio);
        renderer.setSize(width, height, this.updateStyle);
        composer.setPixelRatio(policy.pixelRatio);
        composer.setSize(width, height);
        const bloom = this.getBloom();
        if (bloom) bloom.enabled = policy.bloom;
        const room = this.getRoom();
        if (room) room.quality = this.quality === 'saving' ? 'low' : 'high';
        this.applied = { ...policy, width, height };
        return this.applied;
    }
}
