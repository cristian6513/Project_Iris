import { disposeAvatarTree } from './avatar_lifecycle.js';

/**
 * The VRM the Companion shows: the person's own, served by Core, or IRIS' (`irisUrl`,
 * from the Companion's own bundle, so it shows even before Core answers). `own` says
 * which; `key` changes when the model or its file does.
 */
export function companionAvatarSelection(config = {}, irisUrl = '') {
    let resource = String(config.avatar_vrm_path || '').trim();
    if (resource && !/^(https?:\/\/|\/|blob:)/.test(resource)) {
        resource = '/avatars/' + encodeURIComponent(resource.split(/[/\\]/).pop());
    }
    const revision = resource ? String(config.avatar_revision || '') : '';
    const key = JSON.stringify([resource || 'iris', revision]);
    if (revision && resource.startsWith('/') && !resource.startsWith('//')) {
        const url = new URL(resource, 'http://localhost');
        url.searchParams.set('v', revision);
        resource = url.pathname + url.search + url.hash;
    }
    return { resource: resource || irisUrl, own: Boolean(resource), key };
}

/** One reader/loader at a time. An event during a load supersedes its commit. */
export class CompanionAvatarSync {
    constructor({ readConfig, applyConfig, onError = () => {} }) {
        Object.assign(this, { readConfig, applyConfig, onError });
        this.generation = 0;
        this.pending = null;
        this.disposed = false;
    }

    request(invalidate = false) {
        if (this.disposed) return Promise.resolve();
        if (this.pending && !invalidate) return this.pending;
        this.generation++;
        if (!this.pending) {
            this.pending = this._drain().finally(() => { this.pending = null; });
        }
        return this.pending;
    }

    async _drain() {
        let seen;
        do {
            seen = this.generation;
            const isCurrent = () => !this.disposed && seen === this.generation;
            try {
                const config = await this.readConfig();
                if (isCurrent()) await this.applyConfig(config, isCurrent);
            } catch (error) {
                if (isCurrent()) this.onError(error);
            }
        } while (!this.disposed && seen !== this.generation);
    }

    dispose() {
        this.disposed = true;
        this.generation++;
    }
}

/** Release resources belonging exclusively to a retired or superseded model. */
export function disposeCompanionAvatar(manager) {
    if (typeof manager?.dispose === 'function') manager.dispose();
    else disposeAvatarTree(manager?.avatar?.group, { preserveRoots: [manager?.scene] });
}
