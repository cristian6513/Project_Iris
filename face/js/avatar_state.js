/** IRIS' own VRM: a factory asset beside her animations (scripts/release_assets.json). */
export const IRIS_AVATAR_FILE = 'iris.vrm';
// Bump with every new factory model (its hash changes in release_assets.json), so no
// browser cache keeps showing the previous one. 11: MToon conversion of Ligera v10.
// 12: jacket and panties on the skin's weights, the skin sealed under them removed.
export const IRIS_AVATAR_REVISION = '12';
export const IRIS_AVATAR_URL = `/face/animations/${IRIS_AVATAR_FILE}?v=${IRIS_AVATAR_REVISION}`;

export function isPmxArchive(resource) {
    return typeof resource === 'string' && /\.zip$/i.test(resource.split(/[?#]/, 1)[0]);
}

/** The VRM to show: the person's own when they chose one, else IRIS'. */
export function avatarUrl(config = {}, irisUrl = IRIS_AVATAR_URL, override = '') {
    return String(override || config.avatar_vrm_path || '').trim() || irisUrl;
}
