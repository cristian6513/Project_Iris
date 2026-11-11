// Avatar-owned resources only. Renderer, room, audio and surface controllers
// outlive a model. Leases protect resources shared by two live candidates.
const owners = new WeakMap();
const released = new WeakSet();

export function avatarResources(root) {
    const resources = new Set();
    const seen = new Set();
    const texture = value => {
        if (!value || typeof value !== 'object' || seen.has(value)) return;
        seen.add(value);
        if (value.isTexture) {
            resources.add(value);
            const images = Array.isArray(value.image) ? value.image : [value.image];
            for (const image of images) if (typeof image?.close === 'function') resources.add(image);
        } else if (Array.isArray(value)) {
            value.forEach(texture);
        } else if (Object.getPrototypeOf(value) === Object.prototype) {
            Object.values(value).forEach(texture);
        }
    };
    root?.traverse?.(node => {
        if (node.geometry) resources.add(node.geometry);
        // Skeleton.dispose owns its lazily created boneTexture.
        if (node.skeleton) resources.add(node.skeleton);
        for (const material of Array.isArray(node.material) ? node.material : [node.material]) {
            if (!material) continue;
            resources.add(material);
            Object.values(material).forEach(texture);
        }
    });
    return resources;
}

export function retainAvatarResources(root) {
    const resources = new Set();
    let disposed = false;
    const capture = () => {
        for (const resource of avatarResources(root)) {
            if (resources.has(resource)) continue;
            resources.add(resource);
            owners.set(resource, (owners.get(resource) || 0) + 1);
        }
    };
    capture();
    return {
        release(preserveRoots = []) {
            if (disposed) return;
            capture(); // Includes textures/materials created after the first frame.
            disposed = true;
            const preserved = new Set(preserveRoots.flatMap(item => [...avatarResources(item)]));
            for (const resource of resources) {
                const remaining = (owners.get(resource) || 1) - 1;
                if (remaining) owners.set(resource, remaining);
                else {
                    owners.delete(resource);
                    if (!preserved.has(resource) && !released.has(resource)) {
                        released.add(resource);
                        try {
                            if (typeof resource.dispose === 'function') resource.dispose();
                            else resource.close?.();
                        } catch (error) {
                            console.warn('[avatar] Resource cleanup failed:', error);
                        }
                    }
                }
            }
            resources.clear();
            root = null;
        },
    };
}

/** Also used for temporary scenes loaded solely to extract an animation. */
export function disposeAvatarTree(group, { preserveRoots = [], lease = null } = {}) {
    if (!group) return;
    group.removeFromParent?.();
    (lease || retainAvatarResources(group)).release(preserveRoots);
    group.clear?.();
}

/** One owner for latest-selection-wins across panel and drag/drop (including uploads). */
export class AvatarSwapCoordinator {
    constructor({ create, commit, release = manager => manager?.dispose?.() }) {
        Object.assign(this, { create, commit, release });
        this.generation = 0;
        this.disposed = false;
        this.pending = false;
    }

    begin() {
        const generation = ++this.generation;
        this.selectionAbort?.abort();
        this.selectionAbort = new AbortController();
        this.pending = true;
        return Object.assign(() => !this.disposed && generation === this.generation, {
            signal: this.selectionAbort.signal,
            finish: () => { if (generation === this.generation) this.pending = false; },
        });
    }

    async replace(url, { isCurrent = this.begin(), releaseUrl = null } = {}) {
        let candidate;
        try {
            if (!isCurrent() || this.disposed) return false;
            candidate = this.create(url);
            await candidate.init({ fallback: false });
            if (!isCurrent() || this.disposed) return false;
            if (!candidate.avatar?.group || !candidate.avatar.vrm) {
                throw new Error('El nuevo archivo no contiene un avatar VRM válido.');
            }
            // Commit is synchronous: no frame sees controllers split across rigs.
            const previous = this.commit(candidate);
            candidate = null;
            this.release(previous);
            return true;
        } catch (error) {
            if (isCurrent() && !this.disposed) throw error;
            return false;
        } finally {
            this.release(candidate);
            releaseUrl?.(); // Never revoke a URL owned by another component.
            isCurrent.finish?.();
        }
    }

    dispose() {
        this.disposed = true;
        this.selectionAbort?.abort();
        this.pending = false;
        this.generation++;
    }
}

/** Rebind surface controllers while preserving the live speech/audio session. */
export function commitAvatarReplacement(app, candidate, { createIdle, createSpeech }) {
    const previous = app.avatarManager;
    const oldIdle = app.idle;
    const idle = createIdle(candidate);
    const speech = app.speechAnimator || createSpeech(candidate);
    const oldGroup = previous?.avatar?.group;
    const group = candidate.avatar.group;
    if (oldGroup) {
        group.position.copy(oldGroup.position);
        // Leave the old model's breathing offset behind: microLife takes the new group's height
        // as its breathing base, so the offset of that moment would stay for good on each swap.
        group.position.y -= app.microLife?._lastBreathOffset || 0;
        group.rotation.copy(oldGroup.rotation);
        group.rotation.y += (candidate.avatar.restYaw || 0) - (previous.avatar.restYaw || 0);
        group.userData.stageVisualLiftApplied = oldGroup.userData.stageVisualLiftApplied;
    }
    idle.paused = Boolean(oldIdle?.paused || speech._talking);
    idle.setActivity?.(oldIdle?._activityState || (speech._talking ? 'speaking' : 'idle'));
    if (oldIdle?.params) Object.assign(idle.params, oldIdle.params);

    candidate.scene = app.scene;
    candidate.avatar.scene = app.scene;
    app.scene.add(group);
    app.avatarManager = candidate;
    app.applyAvatarStageLift?.();
    app.idle = idle;
    if (app.lipSync) app.lipSync.avatar = candidate;
    speech.avatarManager = candidate;
    speech._bones = null;
    speech._baseQuats = null;
    speech.lipSync = app.lipSync;
    app.speechAnimator = speech;
    if (app.microLife) {
        const listening = app.microLife._listening;
        app.microLife.avatarManager = candidate;
        app.microLife.resetForRigSwap();
        app.microLife.setListening?.(listening);
        candidate.avatar.microLifeOwnsBlink = true;
        candidate.avatar.microLifeOwnsBreathing = true;
    }
    if (app.sceneActions) {
        app.sceneActions.dropProps?.();
        const yawDelta = (candidate.avatar.restYaw || 0) - (previous?.avatar?.restYaw || 0);
        if (Number.isFinite(app.sceneActions._heldYaw)) app.sceneActions._heldYaw += yawDelta;
        const transition = app.sceneActions.hotspotTransition;
        if (transition) {
            transition.fromYaw += yawDelta;
            transition.toYaw += yawDelta;
        }
        app.sceneActions.avatar = candidate;
        app.sceneActions.setIdleSystem?.(idle);
    }
    oldIdle?.dispose?.();
    return previous;
}
