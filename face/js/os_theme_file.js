/** Theme JSON v1: visual preferences only. Imported image references never reach CSS. */
import { OS_THEME_DEFAULT } from './os_theme.js?v=3';
import { themeTransitions } from './os_transition_format.js?v=1';
import { validFont, FONT_ID } from './os_fonts.js?v=2';

export const THEME_FILE_MAX_BYTES = 600_000;
const own = (value, key) => Object.prototype.hasOwnProperty.call(value, key);
const record = value => !!value && typeof value === 'object' && !Array.isArray(value);
const fail = message => { throw new Error(message); };
const ranges = { accentGlow: [0, 100], borderGlow: [0, 100], cardOpacity: [30, 95], blurIntensity: [0, 40],
    neonIntensity: [0, 100], backgroundDim: [0, 95], liveIntensity: [0, 100] };
const flags = ['compactMode', 'animatedGlow', 'reducedMotion', 'transparentCards'];
const images = ['backgroundUrl', 'profileUrl'];
const keys = Object.keys(OS_THEME_DEFAULT);

function field(key, value) {
    if (key === 'textFont' || key === 'titleFont') {
        if (!validFont(value)) fail('La tipografía del tema no es compatible.');
        return value;
    }
    if (own(ranges, key)) {
        if (typeof value !== 'number' && (typeof value !== 'string' || !/^(?:\d+(?:\.\d+)?|\.\d+)$/.test(value.trim()))) fail(`El valor de ${key} no es un número válido.`);
        const n = Number(value), [min, max] = ranges[key];
        if (!Number.isFinite(n) || n < min || n > max) fail(`${key} debe estar entre ${min} y ${max}.`);
        return String(n); // Keep the existing v1 string representation, including "0".
    }
    if (['accent', 'accent2', 'readyColor'].includes(key)) {
        if (typeof value !== 'string' || !/^#[0-9a-f]{6}$/i.test(value)) fail(`El color de ${key} no es válido.`);
        return value;
    }
    if (flags.includes(key)) {
        if (typeof value !== 'boolean') fail(`${key} debe ser verdadero o falso.`);
        return value;
    }
    if (key === 'background') {
        if (!['city', 'sakura', 'hacker', 'classic', 'nebula', 'grid', 'sunrise'].includes(value)) fail('Fondo incluido no reconocido.');
        return value;
    }
    if (key === 'profile') {
        if (!['iris', 'alice', 'sakura', 'halo', 'glass'].includes(value)) fail('Imagen de perfil incluida no reconocida.');
        // Every theme shows the IRIS portrait; older themes may still name the retired ones.
        return value === 'alice' || value === 'sakura' ? 'iris' : value;
    }
    if (images.includes(key)) {
        if (typeof value !== 'string' || value.length > 4096 || /[\u0000-\u001f]/.test(value)) fail('La referencia de imagen no es válida.');
        return value.trim();
    }
    return value; // Motion is checked together, with its library, below.
}

/** Only the exact upload API resources, never absolute, protocol-relative or arbitrary local URLs. */
export function managedThemeImage(value, kind) {
    if (typeof value !== 'string' || value.length > 128 || /[\u0000-\u0020\u007f]/.test(value) || !['background', 'profile'].includes(kind)) return '';
    return new RegExp(`^/api/os/theme-image/${kind}(?:\\?v=\\d{1,24})?$`).test(value) ? value : '';
}

/** Read old local snapshots defensively, keeping valid preferences and uploaded images. */
export function normalizeStoredTheme(value) {
    const input = record(value) ? value : {}, result = { ...OS_THEME_DEFAULT };
    for (const key of keys) {
        if (!own(input, key)) continue;
        if (['desktopTransition', 'transitionLibrary'].includes(key)) { result[key] = input[key]; continue; }
        try { result[key] = field(key, input[key]); } catch { /* fall back only for the invalid field */ }
    }
    result.backgroundUrl = managedThemeImage(result.backgroundUrl, 'background');
    result.profileUrl = managedThemeImage(result.profileUrl, 'profile');
    return { ...result, ...themeTransitions(result) };
}

export function decodeThemeFile(source) {
    if (typeof source !== 'string' || new TextEncoder().encode(source).byteLength > THEME_FILE_MAX_BYTES) fail('El archivo de tema es demasiado grande.');
    let parsed;
    try { parsed = JSON.parse(source.replace(/^\uFEFF/, '')); } catch { fail('El archivo no contiene un tema JSON válido.'); }
    if (!record(parsed)) fail('El archivo no contiene un objeto de tema.');
    let input = parsed;
    if (own(parsed, 'version') || own(parsed, 'theme')) {
        if (parsed.version !== 1 || !record(parsed.theme)) fail('Versión o formato de tema no compatible.');
        if (Object.keys(parsed).some(key => !['version', 'exportedAt', 'theme'].includes(key))) fail('El archivo contiene campos de paquete no compatibles.');
        if (own(parsed, 'exportedAt') && (typeof parsed.exportedAt !== 'string' || parsed.exportedAt.length > 60 || Number.isNaN(Date.parse(parsed.exportedAt)))) fail('Fecha de exportación no válida.');
        input = parsed.theme;
    }
    if (!Object.keys(input).length || !Object.keys(input).some(key => keys.includes(key) && !images.includes(key))) fail('El archivo no incluye ajustes de tema.');
    if (Object.keys(input).some(key => !keys.includes(key))) fail('El tema contiene un ajuste no reconocido.');
    const theme = { ...OS_THEME_DEFAULT };
    for (const key of Object.keys(input)) theme[key] = field(key, input[key]);
    Object.assign(theme, themeTransitions(theme, { strict: true }));
    const omittedImages = images.filter(key => theme[key] !== '');
    // Even an upload URL belongs to its original profile: it cannot identify the recipient's image.
    for (const key of images) theme[key] = '';
    const omittedFonts = ['textFont', 'titleFont'].filter(key => FONT_ID.test(theme[key]));
    for (const key of omittedFonts) theme[key] = OS_THEME_DEFAULT[key];
    return { theme, omittedImages, omittedFonts, hasLibrary: own(input, 'transitionLibrary') };
}

export function encodeThemeFile(value, exportedAt = new Date().toISOString()) {
    const theme = normalizeStoredTheme(value);
    const omittedImages = images.filter(key => typeof value?.[key] === 'string' && value[key].trim());
    for (const key of images) theme[key] = '';
    const omittedFonts = ['textFont', 'titleFont'].filter(key => FONT_ID.test(theme[key]));
    for (const key of omittedFonts) theme[key] = OS_THEME_DEFAULT[key];
    return { payload: { version: 1, exportedAt, theme }, omittedImages, omittedFonts };
}
