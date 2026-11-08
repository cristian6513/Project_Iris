/** Pure policies used by the browser Companion surface. */

export function shouldPlaySnapshotAnimation(snapshotName, lastDedicated = {}, {
    now = globalThis.performance?.now?.() ?? Date.now(),
    dedupeWindowMs = 1600,
} = {}) {
    const snapshot = String(snapshotName || '').trim();
    if (!snapshot) return false;
    const previous = String(lastDedicated?.name || '').trim();
    const previousAt = Number(lastDedicated?.at);
    if (snapshot !== previous || !Number.isFinite(previousAt)) return true;
    return Math.max(0, Number(now) - previousAt) >= Math.max(0, Number(dedupeWindowMs));
}

export function companionStateForStatus(status) {
    const clean = String(status || '').trim().toLowerCase();
    if (['thinking', 'processing'].includes(clean)) return 'thinking';
    if (['tool', 'tool_running'].includes(clean)) return 'tool';
    if (['listening', 'recording'].includes(clean)) return 'listening';
    if (clean === 'speaking') return 'speaking';
    if (clean === 'error') return 'error';
    if (['idle', 'ready', 'connected', 'done'].includes(clean)) return 'idle';
    return null;
}
