/**
 * Shared metadata contract for IRIS presentation surfaces.
 *
 * This transport metadata is independent from the LLM response JSON. It lets
 * the backend distinguish Face, Desktop Companion and remote/observer clients
 * so only one of them owns TTS or a physical capture device.
 */

export const SURFACE_ROLES = Object.freeze([
    'face',
    'desktop_companion',
    'remote',
    'legacy_face',
    'observer',
]);

export const SURFACE_CAPABILITIES = Object.freeze([
    'renders_avatar',
    'plays_tts',
    'reports_tts_playback',
    'captures_camera',
    'accepts_chat',
    'controls_local_player',
    'presents_vision',
    'tunes_vision',
    'takes_snapshots',
    'takes_screen_snapshots',
    'presents_inbox',
    'speaks_inbox',
    'hosts_iris_apps',
]);

function safeRole(role) {
    return SURFACE_ROLES.includes(role) ? role : 'observer';
}

function randomSuffix() {
    try {
        if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
    } catch {}
    return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}

export function getOrCreateSurfaceId(role, storage = globalThis.sessionStorage) {
    const normalRole = safeRole(role);
    const key = `IRIS.surfaceId.${normalRole}`;
    try {
        const existing = storage?.getItem?.(key);
        if (existing) return existing;
        const created = `${normalRole}-${randomSuffix()}`;
        storage?.setItem?.(key, created);
        return created;
    } catch {
        return `${normalRole}-${randomSuffix()}`;
    }
}

export function normalizeCapabilities(capabilities = {}) {
    const normalized = {};
    for (const name of SURFACE_CAPABILITIES) {
        if (typeof capabilities[name] === 'boolean') normalized[name] = capabilities[name];
    }
    return normalized;
}

export function buildSurfaceReadyPayload({
    role,
    surfaceId,
    visible = true,
    focused = false,
    capabilities = {},
} = {}) {
    return {
        role: safeRole(role),
        surface_id: String(surfaceId || getOrCreateSurfaceId(role)),
        visible: Boolean(visible),
        focused: Boolean(focused),
        capabilities: normalizeCapabilities(capabilities),
    };
}

export function buildSurfaceStatePayload({
    visible = true,
    focused = false,
    interaction = false,
} = {}) {
    return {
        visible: Boolean(visible),
        focused: Boolean(focused),
        interaction: Boolean(interaction),
    };
}
