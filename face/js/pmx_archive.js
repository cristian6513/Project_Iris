import { inflateSync } from 'three/addons/libs/fflate.module.js';

const MiB = 1024 * 1024;
export const PMX_AVATAR_LIMIT = 64 * MiB;
export const PMX_PACKAGE_LIMIT = 128 * MiB;
export const PMX_AVATAR_EXPANDED_LIMIT = 128 * MiB;
const MAX_ENTRY_BYTES = 32 * MiB;
const MAX_ENTRIES = 256;

const utf8 = new TextDecoder('utf-8', { fatal: true });
// ZIPs made on Japanese or Chinese Windows name their files in that system's code page without
// saying which, and Shift_JIS names usually decode as GB18030 without an error, and vice versa.
const LEGACY = new Map(['shift_jis', 'gb18030'].map(label => [label, new TextDecoder(label, { fatal: true })]));
const crcTable = Uint32Array.from({ length: 256 }, (_, byte) => {
    let crc = byte;
    for (let bit = 0; bit < 8; bit++) crc = (crc >>> 1) ^ ((crc & 1) ? 0xedb88320 : 0);
    return crc;
});

function crc32(bytes) {
    let crc = 0xffffffff;
    for (let i = 0; i < bytes.length; i++) crc = crcTable[(crc ^ bytes[i]) & 0xff] ^ (crc >>> 8);
    return (crc ^ 0xffffffff) >>> 0;
}

function utf8Name(bytes, flags) {
    try {
        return utf8.decode(bytes);
    } catch {
        if (flags & 0x800) throw new Error('El ZIP MMD contiene un nombre UTF-8 inválido.');
        return null;
    }
}

function decodes(decoder, bytes) {
    try { return decoder.decode(bytes); } catch { return null; }
}

/**
 * The code pages that read every name not in UTF-8. Shift_JIS goes first when it reads them as
 * Japanese: more kana than the half-width ones that Chinese names turn into.
 */
function legacyEncodings(names) {
    const labels = [...LEGACY.keys()].filter(label => names.every(bytes => decodes(LEGACY.get(label), bytes) !== null));
    if (labels.length < 2) return labels;
    const japanese = names.map(bytes => LEGACY.get('shift_jis').decode(bytes)).join('');
    const kana = (japanese.match(/[\u3040-\u30ff]/g) ?? []).length;
    const halfWidth = (japanese.match(/[\uff61-\uff9f]/g) ?? []).length;
    return kana > halfWidth ? labels : labels.reverse();
}

export const extensionOf = name => name.split('.').at(-1).toLowerCase();

export function archiveKey(name, { texture = false } = {}) {
    const parts = [];
    for (const part of name.replaceAll('\\', '/').split('/')) {
        if (texture && part === '.') continue;
        if (texture && part === '..' && parts.length) {
            parts.pop();
            continue;
        }
        if (!part || part === '.' || part === '..' || /[:\u0000-\u001f]/.test(part)) {
            throw new Error('El ZIP MMD contiene una ruta inválida.');
        }
        parts.push(part);
    }
    if (!parts.length) throw new Error('El ZIP MMD contiene una ruta inválida.');
    return parts.join('/').toLowerCase();
}

function zipDirectory(bytes) {
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    const word = offset => view.getUint16(offset, true);
    const dword = offset => view.getUint32(offset, true);
    // A comment can itself contain the end signature. Its length must reach EOF.
    const minimum = Math.max(0, bytes.length - 22 - 65535);
    let end = bytes.length - 22;
    while (end >= minimum && (dword(end) !== 0x06054b50 || end + 22 + word(end + 20) !== bytes.length)) end--;
    if (end < minimum) throw new Error('No se pudo leer el ZIP MMD.');
    const count = word(end + 10);
    const start = dword(end + 16);
    if (word(end + 4) || word(end + 6) || word(end + 8) !== count || count > MAX_ENTRIES
        || start + dword(end + 12) !== end) {
        throw new Error('El índice del ZIP MMD no es compatible.');
    }
    return { count, start, end, word, dword };
}

/**
 * Read the index only. Local entries are inflated on demand after selection.
 * Names not in UTF-8 are read with one of `encodings`, the code pages that read them all:
 * `encoding` when given, else the one in which the models find most of the textures that
 * `texturesOf(pmxBytes)` lists. `encoding` is 'utf-8' when every name is.
 */
export function openPmxPackage(bytes, { forImport = false, encoding = null, texturesOf = null } = {}) {
    const limit = forImport ? PMX_PACKAGE_LIMIT : PMX_AVATAR_LIMIT;
    if (bytes.length > limit) throw new Error('El ZIP MMD supera el límite de ' + (limit / MiB) + ' MiB.');
    const { count, start, end, word, dword } = zipDirectory(bytes);
    const records = [];
    let offset = start;
    for (let index = 0; index < count; index++) {
        if (offset + 46 > end || dword(offset) !== 0x02014b50) throw new Error('El índice del ZIP MMD está dañado.');
        const flags = word(offset + 8);
        const method = word(offset + 10);
        const size = dword(offset + 24);
        const compressed = dword(offset + 20);
        const crc = dword(offset + 16);
        const local = dword(offset + 42);
        const nameStart = offset + 46;
        const nameEnd = nameStart + word(offset + 28);
        const next = nameEnd + word(offset + 30) + word(offset + 32);
        const symlink = (dword(offset + 38) >>> 28) === 0xa;
        if (next > end || nameEnd - nameStart > 1024 || flags & 1 || symlink || word(offset + 34)
            || ![0, 8].includes(method) || local + 30 > start || dword(local) !== 0x04034b50
            || word(local + 6) !== flags || word(local + 8) !== method) {
            throw new Error('El ZIP MMD contiene un archivo dañado o no compatible.');
        }
        const rawName = bytes.subarray(nameStart, nameEnd);
        const localNameLength = word(local + 26);
        const dataOffset = local + 30 + localNameLength + word(local + 28);
        if (dataOffset + compressed > start || localNameLength !== rawName.length
            || rawName.some((byte, i) => bytes[local + 30 + i] !== byte)
            || (method === 0 && compressed !== size)) {
            throw new Error('El ZIP MMD contiene un archivo dañado.');
        }
        records.push({ rawName, name: utf8Name(rawName, flags), size, compressed, crc, method, dataOffset });
        offset = next;
    }
    if (offset !== end) throw new Error('El índice del ZIP MMD está dañado.');

    const legacy = records.filter(record => record.name === null);
    const encodings = legacy.length ? legacyEncodings(legacy.map(record => record.rawName)) : [];
    const read = ({ dataOffset, compressed, method, size, crc }) => {
        const source = bytes.subarray(dataOffset, dataOffset + compressed);
        const data = method === 0 ? source : inflateSync(source, { out: new Uint8Array(size + 1) });
        if (data.length !== size || crc32(data) !== crc) throw new Error('El ZIP MMD contiene un archivo dañado.');
        return data;
    };
    const named = label => ({ ...nameEntries(records, label, forImport), encoding: label, encodings, read });
    if (!legacy.length) return named('utf-8');
    if (encodings.includes(encoding)) return named(encoding);
    if (!encodings.length) return named('latin1');
    const first = named(encodings[0]);
    if (encodings.length < 2 || !texturesOf) return first;

    // Both code pages read the names: keep the one in which the models find most of their
    // textures. Every model counts: a variant may name its textures in ASCII and the next in kana.
    // The models come in the same order in every reading.
    const textures = first.models.map(model => {
        try { return texturesOf(read(model)).filter(Boolean); } catch { return []; }
    });
    const found = pkg => pkg.models.reduce((sum, model, index) => sum + textures[index].filter(texture => {
        try { return Boolean(pkg.texture(model, texture)); } catch { return false; }
    }).length, 0);
    let best = first;
    let most = found(first);
    for (const label of encodings.slice(1)) {
        let candidate;
        try { candidate = named(label); } catch { continue; }
        const count = found(candidate);
        if (count > most) [best, most] = [candidate, count];
    }
    return best;
}

function nameEntries(records, label, forImport) {
    const entries = new Map();
    const models = [];
    let expanded = 0;
    for (const { rawName, name: readable, ...record } of records) {
        const name = (readable ?? (LEGACY.has(label) ? LEGACY.get(label).decode(rawName) : String.fromCharCode(...rawName)))
            .replaceAll('\\', '/');
        const directory = name.endsWith('/');
        const key = archiveKey(directory ? name.slice(0, -1) : name);
        if (entries.has(key)) throw new Error('El ZIP MMD contiene rutas duplicadas.');
        expanded += record.size;
        if (record.size > MAX_ENTRY_BYTES || expanded > PMX_AVATAR_EXPANDED_LIMIT * (forImport ? 2 : 1)) {
            throw new Error('El contenido del ZIP MMD supera el límite permitido.');
        }
        const entry = { name, key, ...record, directory };
        entries.set(key, entry);
        if (!directory && extensionOf(name) === 'pmx') models.push(entry);
    }
    if (!models.length) throw new Error('El ZIP debe contener un modelo PMX con sus texturas.');
    return {
        entries, models, expanded,
        /** The entry of a texture path as written in `model`, relative to the model. */
        texture(model, path) {
            return entries.get(archiveKey(model.name.slice(0, model.name.lastIndexOf('/') + 1) + path, { texture: true }));
        },
    };
}
