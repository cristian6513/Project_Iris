/** The distant plane's aspect, independent from the window opening. */
export const WINDOW_BACKDROP_ASPECT = 1834 / 858;
// KTX2 (GPU-compressed), each with a PNG twin of the same name for when KTX2 cannot load.
export const WINDOW_BACKDROPS = Object.freeze({
    cyber: '/face/public/assets/room/cyber-window-city-v2.ktx2',
    sakura: '/face/public/assets/room/sakura-window-city-v1.ktx2',
    hacker: '/face/public/assets/room/hacker-window-city-v1.ktx2',
    classic: '/face/public/assets/room/classic-window-city-v1.ktx2',
});
/** The pictures' own proportions: the KTX2 copies are stretched to 2048 x 1024 for the GPU. */
export const WINDOW_BACKDROP_SOURCE_ASPECT = Object.freeze({
    cyber: 1834 / 858, sakura: 1836 / 857, hacker: 1836 / 857, classic: 1836 / 857,
});

/** Cover the actual landscape plane without stretching the image. */
export function fitBackdropTexture(texture, aspect = WINDOW_BACKDROP_ASPECT) {
    const image = texture?.image;
    if (!image?.width || !image?.height || !aspect) return texture;
    const source = texture.userData?.sourceAspect || image.width / image.height;
    texture.offset.set(0, 0);
    texture.repeat.set(1, 1);
    if (source > aspect) {
        texture.repeat.x = aspect / source;
        texture.offset.x = (1 - texture.repeat.x) / 2;
    } else if (source < aspect) {
        texture.repeat.y = source / aspect;
        texture.offset.y = (1 - texture.repeat.y) / 2;
    }
    return texture;
}

/** Own one displayed texture; late loads can never replace a newer theme. */
export class WindowBackdrop {
    constructor({ material, loadTexture, createFallback, prepareTexture = fitBackdropTexture, logger = console }) {
        Object.assign(this, { material, loadTexture, createFallback, prepareTexture, logger });
        this.theme = '';
        this.status = 'idle';
        this.ready = Promise.resolve(false);
        this._revision = 0;
        this._disposed = false;
    }

    setTheme(theme) {
        if (this._disposed || !Object.hasOwn(WINDOW_BACKDROPS, theme)) return Promise.resolve(false);
        if (this.theme === theme && (this.status === 'loading' || this.status === 'ready')) return this.ready;
        this.theme = theme;
        const revision = ++this._revision;
        const url = WINDOW_BACKDROPS[theme];
        // A → slow B → A can reuse A without decoding it again. B is invalidated.
        if (this.material.userData.assetUrl === url && this.material.map) {
            this.status = 'ready';
            this.ready = Promise.resolve(true);
            return this.ready;
        }
        this.status = 'loading';
        this.ready = this._load(theme, url, revision);
        return this.ready;
    }

    async _load(theme, url, revision) {
        let texture;
        try {
            texture = await this.loadTexture(url);
            if (this._disposed || revision !== this._revision) {
                texture.dispose();
                return false;
            }
            (texture.userData ??= {}).sourceAspect = WINDOW_BACKDROP_SOURCE_ASPECT[theme];
            this.prepareTexture(texture);
            this._replace(texture, url);
            this.status = 'ready';
            return true;
        } catch (error) {
            if (texture && texture !== this.material.map) texture.dispose();
            if (this._disposed || revision !== this._revision) return false;
            const fallback = this.createFallback(theme);
            this.prepareTexture(fallback);
            this._replace(fallback, '');
            this.status = 'fallback';
            this.logger.warn?.('[room] Window landscape failed; using the theme fallback:', theme, error);
            return false;
        }
    }

    _replace(texture, url) {
        const previous = this.material.map;
        this.material.map = texture;
        this.material.userData.assetUrl = url;
        this.material.needsUpdate = true;
        if (previous !== texture) previous?.dispose?.();
    }

    dispose() {
        if (this._disposed) return;
        this._disposed = true;
        ++this._revision;
        this._replace(null, '');
        this.status = 'disposed';
    }
}
