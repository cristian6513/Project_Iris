/** Prepare an inventoried local Vision runtime in a new staging directory. */
import { createHash } from 'node:crypto';
import { lstat, mkdir, readFile, readdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const DEFAULT_ROOT = fileURLToPath(new URL('..', import.meta.url));
const PINS_FILE = new URL('./vision_assets.json', import.meta.url);
const PACKAGE_SOURCE = 'companion/node_modules/@mediapipe/tasks-vision/package.json';
const WASM_SOURCE = 'companion/node_modules/@mediapipe/tasks-vision/wasm';
const WINDOWS_DEVICE = /^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i;

function assertSegments(value, label, { absolute = false } = {}) {
    if (typeof value !== 'string' || !value || value.includes('\0')) {
        throw new Error(`${label} must be a nonempty path`);
    }
    const remainder = absolute ? value.slice(path.parse(value).root.length) : value;
    if ((!absolute && (path.posix.isAbsolute(value) || path.win32.isAbsolute(value)))
        || /[<>:"|?*]/.test(remainder) || (!absolute && value.includes('\\'))
        || value.startsWith('\\\\')) {
        throw new Error(`${label} has an unsafe path`);
    }
    const segments = remainder.split(/[\\/]/);
    if (segments.some(segment => !segment || segment === '.' || segment === '..'
        || /[.\s]$/.test(segment) || WINDOWS_DEVICE.test(segment))) {
        throw new Error(`${label} has a traversal or path alias`);
    }
}

function absolutePath(value, label) {
    if (!path.isAbsolute(value ?? '') || (process.platform === 'win32' && !/^[a-z]:[\\/]/i.test(value))) {
        throw new Error(`${label} must be absolute`);
    }
    const trimmed = value.replace(/[\\/]+$/, '');
    assertSegments(trimmed, label, { absolute: true });
    return path.normalize(trimmed);
}

/** Validate every existing component, including junctions and case aliases. */
async function inspectPath(absolute, { missing = false } = {}) {
    const root = path.parse(absolute).root;
    let current = root;
    const parts = absolute.slice(root.length).split(path.sep).filter(Boolean);
    for (let index = 0; index < parts.length; index++) {
        const segment = parts[index];
        const names = await readdir(current);
        const aliases = names.filter(name => name.toLowerCase() === segment.toLowerCase());
        if (aliases.length > 1 || (aliases.length === 1 && aliases[0] !== segment)) {
            throw new Error(`Path alias rejected: ${absolute}`);
        }
        current = path.join(current, segment);
        let stat;
        try { stat = await lstat(current); }
        catch (error) {
            if (missing && error.code === 'ENOENT') return null;
            throw error;
        }
        if (aliases.length !== 1) throw new Error(`Path alias rejected: ${absolute}`);
        if (stat.isSymbolicLink()) throw new Error(`Link rejected: ${current}`);
        if (index < parts.length - 1 && !stat.isDirectory()) {
            throw new Error(`Directory required: ${current}`);
        }
        if (index === parts.length - 1) return stat;
    }
    throw new Error('A filesystem root cannot be a Vision resource or target');
}

function validatePins(pins) {
    if (pins.schema !== 1 || pins.component !== 'iris-local-vision'
        || pins.mediapipe_version !== '0.10.35' || pins.license !== 'Apache-2.0'
        || !Array.isArray(pins.files) || pins.files.length !== 10) {
        throw new Error('Invalid Vision resource contract');
    }
    const sources = new Set(), outputs = new Set(['manifest.json']);
    for (const entry of pins.files) {
        assertSegments(entry.source, 'Vision source');
        const sourceKey = entry.source.toLowerCase();
        if (sources.has(sourceKey)) throw new Error('Vision source alias');
        sources.add(sourceKey);
        if (!Number.isSafeInteger(entry.size) || entry.size <= 0
            || !/^[0-9a-f]{64}$/.test(entry.sha256)) throw new Error('Invalid Vision digest');
        if (entry.path) {
            assertSegments(entry.path, 'Vision output');
            const outputKey = entry.path.toLowerCase();
            if (outputs.has(outputKey)) throw new Error('Vision output alias');
            outputs.add(outputKey);
        }
    }
    const model = pins.files.find(entry => entry.path === 'face_landmarker.task');
    if (model?.sha256 !== pins.model?.sha256 || !sources.has(PACKAGE_SOURCE)) {
        throw new Error('Invalid Vision model or package contract');
    }
}

/**
 * All source bytes, versions, hashes and paths are checked before any writes.
 * An existing target is never removed or overwritten, including a preview.
 * The returned files inventory excludes its own manifest.json.
 */
export async function prepareVisionAssets({ projectRoot = DEFAULT_ROOT, target } = {}) {
    const root = absolutePath(projectRoot, 'projectRoot');
    const destination = absolutePath(target, 'target');
    const rootStat = await inspectPath(root);
    if (!rootStat?.isDirectory()) throw new Error('projectRoot must be a directory');
    if (await inspectPath(destination, { missing: true })) {
        throw new Error(`Vision target must be a new directory: ${destination}`);
    }
    await inspectPath(fileURLToPath(PINS_FILE));
    const pins = JSON.parse(await readFile(PINS_FILE, 'utf8'));
    validatePins(pins);
    const wasmDir = path.join(root, ...WASM_SOURCE.split('/'));
    await inspectPath(wasmDir);
    const wasmNames = (await readdir(wasmDir)).sort();
    const expectedWasm = pins.files.filter(entry => entry.path?.startsWith('wasm/'))
        .map(entry => path.posix.basename(entry.path)).sort();
    if (JSON.stringify(wasmNames) !== JSON.stringify(expectedWasm)) {
        throw new Error('Unexpected or missing Vision WASM resources');
    }
    const inputs = [];
    for (const entry of pins.files) {
        const source = path.join(root, ...entry.source.split('/'));
        const stat = await inspectPath(source);
        if (!stat?.isFile() || stat.nlink !== 1) throw new Error(`Regular unlinked file required: ${entry.source}`);
        const bytes = await readFile(source);
        if (entry.source === PACKAGE_SOURCE) {
            const pkg = JSON.parse(bytes.toString('utf8'));
            if (pkg.name !== '@mediapipe/tasks-vision' || pkg.version !== pins.mediapipe_version
                || pkg.license !== pins.license) throw new Error('Incompatible MediaPipe package version or license');
        }
        if (bytes.length !== entry.size
            || createHash('sha256').update(bytes).digest('hex') !== entry.sha256) {
            throw new Error(`Vision SHA-256 mismatch: ${entry.source}`);
        }
        const after = await inspectPath(source);
        if (!after.isFile() || after.nlink !== 1 || after.ino !== stat.ino || after.dev !== stat.dev
            || after.size !== stat.size || after.mtimeMs !== stat.mtimeMs) {
            throw new Error(`Vision input changed while reading: ${entry.source}`);
        }
        inputs.push({ entry, bytes });
    }
    // Repeat target inspection after source validation. mkdir without recursive
    // on the final component refuses a target created by another builder.
    if (await inspectPath(destination, { missing: true })) throw new Error('Vision target already exists');
    await mkdir(path.dirname(destination), { recursive: true });
    await inspectPath(path.dirname(destination));
    await mkdir(destination);
    await mkdir(path.join(destination, 'wasm'));
    const files = [];
    for (const { entry, bytes } of inputs) {
        if (!entry.path) continue;
        const output = path.join(destination, ...entry.path.split('/'));
        await writeFile(output, bytes, { flag: 'wx' });
        files.push({ path: entry.path, size: entry.size, sha256: entry.sha256 });
    }
    const manifest = {
        schema: pins.schema, component: pins.component, mediapipe_version: pins.mediapipe_version,
        npm_integrity: pins.npm_integrity, license: pins.license,
        provenance: pins.provenance, model: pins.model, files,
    };
    await writeFile(path.join(destination, 'manifest.json'), JSON.stringify(manifest, null, 2) + '\n', { flag: 'wx' });
    return manifest;
}
