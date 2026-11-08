/** Rendering policy shared by Face and Companion, independent from their FPS cap. */
export const GRAPHICS_QUALITIES = Object.freeze({
    saving: Object.freeze({ label: 'Ahorro', scale: 0.75, maxRatio: 1, samples: 0, bloom: false, avatarTextures: 1024 }),
    balanced: Object.freeze({ label: 'Equilibrado', scale: 1, maxRatio: 1.5, samples: 2, bloom: true, avatarTextures: 0 }),
    high: Object.freeze({ label: 'Alta calidad', scale: 1, maxRatio: 2, samples: 4, bloom: true, avatarTextures: 0 }),
});

export function graphicsQuality(value) {
    return Object.hasOwn(GRAPHICS_QUALITIES, value) ? value : 'balanced';
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
        getDeviceRatio = () => globalThis.devicePixelRatio || 1,
        getSize = () => [globalThis.innerWidth, globalThis.innerHeight], updateStyle = false } = {}) {
        Object.assign(this, { getRenderer, getComposer, getBloom, getRoom, getDeviceRatio, getSize, updateStyle });
        this.quality = 'balanced';
        this.applied = null;
    }

    setQuality(value) {
        this.quality = graphicsQuality(value);
        return this.resize();
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
