/** Targeted, expiring local command; it cannot acquire or transfer a camera. */
const seen = new Set();
export function applyVisionCalibration(payload, { mocap, surfaceId, vision, now = Date.now() / 1000 } = {}) {
    if (!payload || !/^[a-f0-9]{32}$/.test(payload.id || '') || seen.has(payload.id)
        || payload.surface_id !== surfaceId || !['start', 'reset'].includes(payload.action)
        || !Number.isFinite(payload.created_at) || !Number.isFinite(payload.expires_at)
        || payload.expires_at - payload.created_at > 5 || payload.expires_at <= payload.created_at
        || now < payload.created_at || now > payload.expires_at) return false;
    seen.add(payload.id);
    if (seen.size > 256) seen.delete(seen.values().next().value);
    if (!vision?.enabled || Number(vision.paused_until || 0) > now || !mocap?.running) return false;
    mocap.configureVision(vision);
    return mocap.calibrate(payload.action) === true;
}
