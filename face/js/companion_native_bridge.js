/** Browser/Tauri boundary for IRIS Desktop Companion.
 *
 * Browser mode keeps using same-origin URLs. Native mode obtains a private
 * loopback endpoint and pairing token from Rust; the token is never persisted
 * in localStorage or rendered into the document.
 */

const TAURI_MARKER = '__TAURI_INTERNALS__';

export function isTauriRuntime(scope = globalThis) {
    return Boolean(scope && scope[TAURI_MARKER]);
}

export function companionWebSocketProtocols(token) {
    const clean = String(token || '').trim();
    return clean ? ['iris-companion-v1', `iris-token.${clean}`] : [];
}

// Packaged fallback for startup/offline. Live revisions come from Core's snapshot.
export function localAnimationBase(base = globalThis.document?.baseURI) {
    return new URL('../animations/', base || 'http://localhost/face/companion/').toString().replace(/\/$/, '');
}

function joinApiUrl(base, path) {
    const suffix = String(path || '');
    if (/^https?:\/\//i.test(suffix)) return suffix;
    return `${String(base || '').replace(/\/$/, '')}/${suffix.replace(/^\//, '')}`;
}

function browserOpenIrisOs() {
    const irisWindow = globalThis.open?.('/face/', 'iris-os');
    irisWindow?.focus?.();
}

export async function createCompanionBridge(scope = globalThis) {
    if (!isTauriRuntime(scope)) {
        return {
            native: false,
            apiBase: '',
            wsUrl: null,
            wsProtocols: [],
            token: '',
            preferences: {},
            apiFetch: (path, options = {}) => fetch(path, options),
            coreAssetUrl: async (path) => String(path || ''),
            releaseCoreAssetUrl() {},
            dispose() {},
            openIrisOs: async () => browserOpenIrisOs(),
            hideCompanion: async () => ({ hidden: false, reason: 'native-required' }),
            startWindowDrag: async () => ({ started: false, reason: 'native-required' }),
            setAlwaysOnTop: async () => {},
            startWindowWalk: async () => ({ started: false, reason: 'native-required' }),
            cancelWindowWalk: async () => {},
        };
    }

    const { invoke } = await import('@tauri-apps/api/core');
    const bootstrap = await invoke('companion_bootstrap');
    let apiBase = String(bootstrap?.apiBase || '').replace(/\/$/, '');
    let token = String(bootstrap?.token || '');
    if (!apiBase || !bootstrap?.wsUrl || !token) {
        throw new Error('IRIS Companion no recibió un emparejamiento local válido.');
    }

    const objectUrls = new Set();
    const bridge = {
        native: true,
        apiBase,
        wsUrl: String(bootstrap.wsUrl),
        wsProtocols: companionWebSocketProtocols(token),
        token,
        preferences: bootstrap.preferences || {},
        async refreshBootstrap() {
            const next = await invoke('companion_bootstrap');
            const nextBase = String(next?.apiBase || '').replace(/\/$/, '');
            const nextToken = String(next?.token || '');
            const nextWs = String(next?.wsUrl || '');
            if (!nextBase || !nextToken || !nextWs) {
                throw new Error('IRIS Companion no recibió un endpoint local válido.');
            }
            const changed = nextBase !== apiBase || nextWs !== this.wsUrl || nextToken !== token;
            apiBase = nextBase;
            token = nextToken;
            this.apiBase = nextBase;
            this.wsUrl = nextWs;
            this.token = nextToken;
            this.wsProtocols = companionWebSocketProtocols(nextToken);
            this.preferences = next.preferences || {};
            return changed;
        },
        apiFetch(path, options = {}) {
            const headers = new Headers(options.headers || {});
            headers.set('X-IRIS-Companion-Token', token);
            return fetch(joinApiUrl(apiBase, path), { ...options, headers });
        },
        async coreAssetUrl(path) {
            const response = await this.apiFetch(path, { cache: 'no-store' });
            if (!response.ok) throw new Error(`IRIS Core rechazó el recurso (HTTP ${response.status}).`);
            const objectUrl = URL.createObjectURL(await response.blob());
            objectUrls.add(objectUrl);
            return objectUrl;
        },
        releaseCoreAssetUrl(url) {
            if (objectUrls.delete(url)) URL.revokeObjectURL(url);
        },
        openIrisOs: () => invoke('open_iris_os'),
        hideCompanion: () => invoke('hide_companion'),
        startWindowDrag: () => invoke('start_window_drag'),
        setAlwaysOnTop: (enabled) => invoke('set_always_on_top', { enabled: Boolean(enabled) }),
        startWindowWalk: (durationMs = 3100) => invoke('start_window_walk', {
            durationMs: Math.round(Number(durationMs) || 3100),
        }),
        cancelWindowWalk: () => invoke('cancel_window_walk'),
        dispose() {
            for (const url of objectUrls) URL.revokeObjectURL(url);
            objectUrls.clear();
        },
    };
    return bridge;
}
