/**
 * Quiet update notice (user's choice, 2026-11-18). A tester believed he had no update: Iris only
 * asked its channel when «Buscar actualizaciones» was pressed, deep in IRIS OS → Ajustes.
 *
 * Now Iris asks its update channel by itself, at most every 12 hours, and when a new version is
 * out it lights a dot on «Buscar actualizaciones» in the room's control center and on the button
 * that opens the control center. Nothing is downloaded or installed, and Iris doesn't say it: no
 * model or voice is spent. Only the installed app asks (it has a channel): in a development
 * checkout the same endpoint would fetch Git.
 */
export const UPDATE_NOTICE_KEY = 'iris.updateNotice';
export const UPDATE_CHECK_EVERY_MS = 12 * 60 * 60 * 1000;

/** Keep the interval for this session even when browser storage is blocked or full. */
export function createUpdateNoticeStorage(storage) {
    let memory = null;
    let fallback = !storage;
    return {
        getItem(key) {
            if (!fallback) {
                try { memory = storage.getItem(key); return memory; } catch { fallback = true; }
            }
            return memory;
        },
        setItem(key, value) {
            memory = value;
            if (!fallback) {
                try { storage.setItem(key, value); } catch { fallback = true; }
            }
        },
    };
}

export function updateAvailable(snapshot) {
    return snapshot?.summary === 'updates_available' && snapshot?.can_update === true;
}

function readCache(storage) {
    try { return JSON.parse(storage?.getItem(UPDATE_NOTICE_KEY) || 'null'); } catch { return null; }
}

function writeCache(storage, value) {
    try { storage?.setItem(UPDATE_NOTICE_KEY, JSON.stringify(value)); } catch { /* the dot just won't persist */ }
}

/** The last answer for this version of Iris (after an update it no longer applies), or null. */
export function cachedNotice({ storage, now = Date.now(), currentVersion = '' } = {}) {
    const cached = readCache(storage);
    if (!cached || typeof cached.at !== 'number' || cached.current !== currentVersion) return null;
    return { available: Boolean(cached.available), version: String(cached.version || ''), fresh: now - cached.at < UPDATE_CHECK_EVERY_MS };
}

/** Remembers a channel answer (from here or from Ajustes) and says whether an update is out. */
export function rememberSnapshot(snapshot, { storage, now = Date.now(), currentVersion = '' } = {}) {
    if (!snapshot || snapshot.ok === false || !['current', 'updates_available'].includes(snapshot.summary)) {
        return cachedNotice({ storage, now, currentVersion })?.available ?? false;
    }
    const available = updateAvailable(snapshot);
    writeCache(storage, { at: now, current: currentVersion, available, version: available ? String(snapshot?.available?.iris || '') : '' });
    return available;
}

/**
 * Asks the channel unless a fresh answer is remembered. Resolves { available, version, source }:
 * source is 'cache', 'channel', 'none' (no channel here: nothing asked) or 'error'.
 */
export async function checkForUpdates({ fetchImpl = globalThis.fetch, storage, now = Date.now(), currentVersion = '' } = {}) {
    const cached = cachedNotice({ storage, now, currentVersion });
    if (cached?.fresh) return { available: cached.available, version: cached.version, source: 'cache' };
    const keep = { available: cached?.available ?? false, version: cached?.version ?? '' };
    try {
        const status = await fetchImpl('/api/system-updates/status', { cache: 'no-store' });
        if (!status?.ok) return { ...keep, source: 'error' };
        const job = await status.json();
        // The installed app answers with a snapshot of its channel; a development checkout doesn't.
        if (!job?.snapshot || job.snapshot.summary === 'unavailable') return { available: false, version: '', source: 'none' };
        if (job.active) return { ...keep, source: 'error' };   // an update is being installed right now
        // Limit channel attempts too. An offline channel must not turn the hourly poll into a retry loop.
        writeCache(storage, { at: now, current: currentVersion, ...keep });
        const response = await fetchImpl('/api/system-updates/check', { cache: 'no-store' });
        if (!response?.ok) return { ...keep, source: 'error' };
        const snapshot = await response.json();
        if (snapshot?.ok === false || !['current', 'updates_available'].includes(snapshot?.summary)) {
            return { ...keep, source: 'error' };
        }
        const available = rememberSnapshot(snapshot, { storage, now, currentVersion });
        return { available, version: available ? String(snapshot?.available?.iris || '') : '', source: 'channel' };
    } catch {
        return { ...keep, source: 'error' };
    }
}

/** The dot on «Buscar actualizaciones» and on the control center's button; labels say why. */
export function showUpdateDot(available, { version = '', doc = globalThis.document } = {}) {
    const button = doc?.getElementById?.('panel-open-updates');
    const dot = button?.querySelector?.('.panel-shortcut-dot');
    if (dot) dot.hidden = !available;
    if (button) {
        const label = available ? `Buscar actualizaciones: hay una versión nueva${version ? ` (Iris ${version})` : ''}` : '';
        if (label) button.setAttribute('aria-label', label); else button.removeAttribute('aria-label');
        button.title = label || 'Abre IRIS OS en Ajustes y busca una versión nueva';
    }
    const toggle = doc?.getElementById?.('panel-toggle');
    toggle?.classList?.toggle('has-update', Boolean(available));
}
