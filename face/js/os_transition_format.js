/** A bounded CSS-keyframe reader. Source CSS is data: never mounted as a stylesheet. */
export const TRANSITION_SCHEMA = 'iris.transition/v1';
export const TRANSITION_LIMITS = Object.freeze({ bytes: 65_536, library: 20, frames: 32, effects: 8 });
export const BUILTIN_TRANSITIONS = Object.freeze(['none', 'fade', 'slide', 'zoom', 'glitch', 'iris']);
const own = (o, k) => Object.prototype.hasOwnProperty.call(o, k);
const fail = message => { throw new Error(message); };
const record = value => value && typeof value === 'object' && !Array.isArray(value);
const number = (value, min, max, label) => {
    if (typeof value !== 'number' || !Number.isFinite(value) || value < min || value > max) fail(`${label}: usa un número entre ${min} y ${max}.`);
    return value;
};
function text(value, max, label, required = false) {
    if (typeof value !== 'string' || value.length > max || /[\u0000-\u0008\u000b\u000c\u000e-\u001f]/.test(value)) fail(`${label} no es válido.`);
    const result = value.trim();
    if (required && !result) fail(`Falta ${label.toLowerCase()}.`);
    return result;
}
function keys(value, allowed, label) {
    if (!record(value) || Object.keys(value).some(key => !allowed.includes(key))) fail(`${label}: formato o campo no compatible.`);
}
const NUM = '[+-]?(?:\\d+(?:\\.\\d*)?|\\.\\d+)';
const scalar = new RegExp(`^(${NUM})(px|%|deg)?$`, 'i');
function component(value, kind) {
    const match = value.trim().match(scalar);
    if (!match) fail(`Transformación no compatible: ${value}.`);
    const n = Number(match[1]), unit = (match[2] || '').toLowerCase();
    const valid = kind === 'scale' ? !unit && Math.abs(n) <= 2
        : kind === 'angle' ? (unit === 'deg' || n === 0 && !unit) && Math.abs(n) <= 360
        : (unit === 'px' && Math.abs(n) <= 2000) || (unit === '%' && Math.abs(n) <= 120) || !unit && n === 0;
    if (!valid) fail(`Valor fuera del formato admitido: ${value}.`);
    return `${n}${unit}`;
}

export function transitionTransform(value) {
    const source = text(value, 512, 'Transformación', true);
    if (source === 'none') return source;
    const functions = [...source.matchAll(/([a-zA-Z][a-zA-Z0-9]*)\(([^()]*)\)/g)];
    if (!functions.length || functions.length > 8 || source.replace(/([a-zA-Z][a-zA-Z0-9]*)\(([^()]*)\)/g, '').trim()) fail('Usa solo desplazamiento, escala y giro compatibles.');
    return functions.map(([, fn, args]) => {
        const spec = { translate: [1, 2, 'length'], translateX: [1, 1, 'length'], translateY: [1, 1, 'length'],
            translate3d: [3, 3, 'length'], scale: [1, 2, 'scale'], scaleX: [1, 1, 'scale'], scaleY: [1, 1, 'scale'],
            scale3d: [3, 3, 'scale'], rotate: [1, 1, 'angle'], rotateZ: [1, 1, 'angle'] }[fn];
        if (!Array.isArray(spec)) fail(`La transformación ${fn} todavía no es compatible.`);
        const values = args.split(',');
        if (values.length < spec[0] || values.length > spec[1]) fail(`Argumentos no válidos en ${fn}.`);
        const normalized = values.map(v => component(v, spec[2]));
        if (fn === 'translate3d' && !/^0(?:px|%)?$/.test(normalized[2]) || fn === 'scale3d' && normalized[2] !== '1') fail('Esta versión admite movimientos en dos dimensiones.');
        return `${fn}(${normalized.join(', ')})`;
    }).join(' ');
}

export function transitionEasing(value) {
    const source = text(value, 100, 'Curva', true);
    if (['linear', 'ease', 'ease-in', 'ease-out', 'ease-in-out'].includes(source)) return source;
    const match = source.match(/^cubic-bezier\(([^()]*)\)$/);
    const values = match?.[1].split(',').map(v => /^[-+\d.]+$/.test(v.trim()) ? Number(v) : NaN);
    if (!values || values.length !== 4 || values.some(n => !Number.isFinite(n)) || values[0] < 0 || values[0] > 1 || values[2] < 0 || values[2] > 1 || values[1] < -2 || values[1] > 2 || values[3] < -2 || values[3] > 2) fail('Curva no compatible: usa ease, linear o cubic-bezier acotada.');
    return `cubic-bezier(${values.join(', ')})`;
}

function origin(value) {
    const source = text(value, 80, 'Origen', true);
    const tokens = source.split(/\s+/);
    if (tokens.length > 2 || tokens.some(v => !/^(?:center|top|bottom|left|right|(?:\d+(?:\.\d+)?|\.\d+)%)$/.test(v) || v.endsWith('%') && Number(v.slice(0, -1)) > 100)) fail('Origen no compatible: usa posiciones o porcentajes de 0 a 100.');
    // Ask the platform for semantic keyword validity at playback; keep the portable grammar narrow.
    if (tokens.filter(v => ['left', 'right'].includes(v)).length > 1 || tokens.filter(v => ['top', 'bottom'].includes(v)).length > 1) fail('Origen de transformación no válido.');
    if (tokens.length === 2) {
        const normal = /^(?:left|right|center|[\d.]+%)$/.test(tokens[0]) && /^(?:top|bottom|center|[\d.]+%)$/.test(tokens[1]);
        const reversedKeywords = /^(?:top|bottom|center)$/.test(tokens[0]) && /^(?:left|right|center)$/.test(tokens[1]);
        if (!normal && !reversedKeywords) fail('Orden del origen no compatible.');
    }
    return source;
}

export function validateTransition(value) {
    keys(value, ['schema', 'id', 'name', 'author', 'license', 'attribution', 'duration', 'easing', 'keyframes'], 'Transición');
    if (value.schema !== TRANSITION_SCHEMA) fail('Versión de transición no compatible.');
    if (typeof value.id !== 'string' || !/^[a-z][a-z0-9-]{0,63}$/.test(value.id)) fail('Identificador de transición no válido.');
    if (!Array.isArray(value.keyframes) || value.keyframes.length < 2 || value.keyframes.length > TRANSITION_LIMITS.frames) fail('Usa entre 2 y 32 fotogramas.');
    let previous = -1;
    const keyframes = value.keyframes.map(frame => {
        keys(frame, ['offset', 'opacity', 'transform', 'transformOrigin', 'easing'], 'Fotograma');
        const offset = number(frame.offset, 0, 1, 'Posición del fotograma');
        if (offset <= previous) fail('Los fotogramas deben estar ordenados y sin posiciones repetidas.');
        previous = offset;
        const result = { offset };
        if (own(frame, 'opacity')) result.opacity = number(frame.opacity, 0, 1, 'Opacidad');
        if (own(frame, 'transform')) result.transform = transitionTransform(frame.transform);
        if (own(frame, 'transformOrigin')) result.transformOrigin = origin(frame.transformOrigin);
        if (own(frame, 'easing')) result.easing = transitionEasing(frame.easing);
        if (Object.keys(result).length < 2) fail('Cada fotograma debe describir un movimiento u opacidad.');
        return result;
    });
    if (keyframes[0].offset !== 0 || keyframes.at(-1).offset !== 1) fail('Incluye un fotograma inicial (0%) y uno final (100%).');
    return { schema: TRANSITION_SCHEMA, id: value.id, name: text(value.name, 80, 'Nombre', true),
        author: text(value.author ?? '', 120, 'Autor'), license: text(value.license ?? '', 120, 'Licencia'),
        attribution: text(value.attribution ?? '', 4096, 'Atribución'), duration: number(value.duration, 80, 2000, 'Duración'),
        easing: transitionEasing(value.easing ?? 'ease-out'), keyframes };
}

export function validateTransitionLibrary(value = []) {
    if (!Array.isArray(value) || value.length > TRANSITION_LIMITS.library) fail('Puedes guardar hasta 20 transiciones.');
    const library = value.map(validateTransition);
    if (new Set(library.map(item => item.id)).size !== library.length) fail('Hay identificadores de transición repetidos.');
    return library;
}

/** Legacy themes have no library. Untrusted snapshots must never reach Element.animate raw. */
export function themeTransitions(theme, { strict = false } = {}) {
    let library;
    try { library = validateTransitionLibrary(own(theme, 'transitionLibrary') ? theme.transitionLibrary : []); }
    catch (error) { if (strict) throw error; library = []; }
    let selected = own(theme, 'desktopTransition') ? theme.desktopTransition : 'none';
    if (!BUILTIN_TRANSITIONS.includes(selected) && !library.some(item => `custom:${item.id}` === selected)) {
        if (strict) fail('La transición elegida no está incluida en el tema.');
        selected = 'none';
    }
    return { transitionLibrary: library, desktopTransition: selected };
}

// Minimal tokenizer for the deliberately restricted interchange grammar. No CSSOM, eval or DOM.
function blocks(source) {
    const result = [];
    let i = 0;
    while (i < source.length) {
        while (/\s/.test(source[i] || '') && i < source.length) i++;
        if (i === source.length) break;
        const open = source.indexOf('{', i);
        if (open < 0) fail('CSS incompleto: falta un bloque de fotogramas.');
        const header = source.slice(i, open).trim();
        let depth = 1, end = open + 1;
        for (; end < source.length && depth; end++) {
            if (source[end] === '{') depth++;
            if (source[end] === '}') depth--;
            if (depth > 2) fail('CSS anidado no compatible.');
        }
        if (depth || !header) fail('CSS incompleto: revisa las llaves.');
        result.push({ header, body: source.slice(open + 1, end - 1) });
        if (result.length > 64) fail('Exporta solo las animaciones que quieras importar.');
        i = end;
    }
    return result;
}
function declarations(body) {
    const result = Object.create(null);
    for (const declaration of body.split(';')) {
        if (!declaration.trim()) continue;
        const colon = declaration.indexOf(':');
        if (colon < 1) fail('Declaración CSS incompleta.');
        const name = declaration.slice(0, colon).trim().replace(/^-webkit-/, '');
        const value = declaration.slice(colon + 1).trim();
        if (own(result, name) && result[name] !== value) fail(`Declaración repetida y diferente: ${name}.`);
        result[name] = value;
    }
    return result;
}
const words = source => source.match(/[^\s()]+\([^()]*\)|[^\s]+/g) || [];
function timing(decls) {
    const result = {};
    for (const [key, value] of Object.entries(decls)) {
        if (!['animation', 'animation-name', 'animation-duration', 'animation-timing-function', 'animation-delay', 'animation-fill-mode', 'animation-iteration-count', 'animation-direction'].includes(key)) fail(`La regla CSS contiene ${key}, que no se importa.`);
        result[key] = value;
    }
    if (result.animation) {
        const parts = words(result.animation), times = [];
        for (const part of parts) {
            if (/^(?:\d+(?:\.\d*)?|\.\d+)(?:ms|s)$/.test(part)) times.push(part);
            else if (/^(?:ease|ease-in|ease-out|ease-in-out|linear|cubic-bezier\()/.test(part)) result['animation-timing-function'] ??= part;
            else if (['both', 'forwards', 'backwards', 'none'].includes(part)) result['animation-fill-mode'] ??= part;
            else if (part === 'normal') result['animation-direction'] ??= part;
            else if (part === '1') result['animation-iteration-count'] ??= part;
            else if (/^[a-zA-Z_][\w-]*$/.test(part) && !['infinite', 'reverse', 'alternate', 'alternate-reverse', 'running', 'paused'].includes(part)) {
                if (result['animation-name'] && result['animation-name'] !== part) fail('Exporta una animación por regla CSS.');
                result['animation-name'] = part;
            } else fail(`Opción de animación no compatible: ${part}.`);
        }
        if (times.length > 2) fail('Tiempos de animación no válidos.');
        result['animation-duration'] ??= times[0]; result['animation-delay'] ??= times[1];
    }
    const milliseconds = (v, fallback) => v === undefined ? fallback : /^(?:\d+(?:\.\d*)?|\.\d+)(?:ms|s)$/.test(v) ? parseFloat(v) * (v.endsWith('ms') ? 1 : 1000) : fail('Tiempo CSS no compatible.');
    if (milliseconds(result['animation-delay'], 0) !== 0 || result['animation-iteration-count'] && result['animation-iteration-count'] !== '1'
        || result['animation-direction'] && result['animation-direction'] !== 'normal') fail('Usa una ejecución, sin retraso y con dirección normal.');
    if (result['animation-fill-mode'] && !['none', 'both', 'forwards', 'backwards'].includes(result['animation-fill-mode'])) fail('Relleno CSS no compatible.');
    return { cssName: result['animation-name'], duration: milliseconds(result['animation-duration'], 420), easing: transitionEasing(result['animation-timing-function'] || 'ease-out') };
}

export function parseTransitionFile(source) {
    if (typeof source !== 'string' || new TextEncoder().encode(source).byteLength > TRANSITION_LIMITS.bytes) fail('El archivo supera 64 KiB.');
    source = source.trim().replace(/^\uFEFF/, '');
    if (!source) fail('El archivo está vacío.');
    if (source.startsWith('{') || source.startsWith('[')) {
        let parsed;
        try { parsed = JSON.parse(source); } catch { fail('El paquete JSON está incompleto o no es válido.'); }
        return { transitions: [validateTransition(parsed)], kind: 'json' };
    }
    const comments = source.match(/\/\*[\s\S]*?\*\//g) || [];
    const attribution = comments.map(c => c.slice(2, -2).trim()).join('\n\n');
    if (attribution.length > 4096) fail('Los comentarios superan el límite de atribución; conserva el aviso en un paquete individual.');
    const css = source.replace(/\/\*[\s\S]*?\*\//g, '');
    if (/[\\\u0000<>]/.test(css)) fail('CSS no compatible: usa fotogramas sin escapes ni contenido HTML.');
    const effects = new Map(), settings = new Map();
    for (const block of blocks(css)) {
        const keyframe = block.header.match(/^@(?:-webkit-)?keyframes\s+([a-zA-Z_][\w-]{0,63})$/);
        if (!keyframe) {
            if (!/^\.[a-zA-Z_][\w-]*(?:\s*,\s*\.[a-zA-Z_][\w-]*)*$/.test(block.header)) fail('Solo se admiten @keyframes y clases con sus tiempos de animación.');
            const options = timing(declarations(block.body));
            if (!options.cssName || settings.has(options.cssName) && JSON.stringify(settings.get(options.cssName)) !== JSON.stringify(options)) fail('La regla debe identificar una sola animación y sus tiempos.');
            settings.set(options.cssName, options); continue;
        }
        const frames = new Map();
        for (const frame of blocks(block.body)) {
            const decls = declarations(frame.body), values = {};
            for (const [key, value] of Object.entries(decls)) {
                if (key === 'transform') values.transform = transitionTransform(value);
                else if (key === 'opacity') values.opacity = /^\d*\.?\d+$/.test(value) ? number(Number(value), 0, 1, 'Opacidad') : fail('Opacidad CSS no válida.');
                else if (key === 'transform-origin') values.transformOrigin = origin(value);
                else if (key === 'animation-timing-function') values.easing = transitionEasing(value);
                else fail(`El fotograma contiene ${key}, que todavía no es compatible.`);
            }
            for (const position of frame.header.split(',').map(v => v.trim())) {
                const offset = position === 'from' ? 0 : position === 'to' ? 1 : /^(?:\d+(?:\.\d*)?|\.\d+)%$/.test(position) ? Number(position.slice(0, -1)) / 100 : fail('Posición CSS no válida.');
                number(offset, 0, 1, 'Posición');
                frames.set(offset, { ...frames.get(offset), ...values, offset });
                if (frames.size > TRANSITION_LIMITS.frames) fail('La animación supera 32 fotogramas.');
            }
        }
        const keyframes = [...frames.values()].sort((a, b) => a.offset - b.offset);
        if (effects.has(keyframe[1]) && JSON.stringify(effects.get(keyframe[1])) !== JSON.stringify(keyframes)) fail('Las versiones prefijada y normal de la animación son diferentes.');
        effects.set(keyframe[1], keyframes);
        if (effects.size > TRANSITION_LIMITS.effects) fail('Importa hasta ocho animaciones por archivo CSS.');
    }
    if (!effects.size) fail('No encontré un bloque @keyframes.');
    for (const name of settings.keys()) if (!effects.has(name)) fail(`Faltan los fotogramas de ${name}.`);
    return { kind: 'css', transitions: [...effects].map(([name, keyframes], index) => validateTransition({
        schema: TRANSITION_SCHEMA, id: `import-${index}`, name, author: '', license: '', attribution,
        duration: settings.get(name)?.duration ?? 420, easing: settings.get(name)?.easing ?? 'ease-out', keyframes,
    })) };
}

export function exportTransition(value) { return `${JSON.stringify(validateTransition(value), null, 2)}\n`; }
