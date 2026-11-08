import { createHash } from 'node:crypto';
import { lstat, mkdir, readFile, readdir, writeFile } from 'node:fs/promises';
import { dirname, join, parse, resolve } from 'node:path';

export const factoryContract = JSON.parse(await readFile(
    new URL('../../scripts/release_assets.json', import.meta.url), 'utf8',
));

function basename(name) {
    if (typeof name !== 'string' || !name || /[\\/:\x00-\x1f]/.test(name)
        || name === '.' || name === '..' || /[ .]$/.test(name)
        || /^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?$/i.test(name)) {
        throw new Error(`Invalid animation filename: ${name}`);
    }
    return name;
}

async function noLinks(path) {
    for (let current = resolve(path); current !== parse(current).root; current = dirname(current)) {
        const info = await lstat(current).catch((error) => {
            if (error.code === 'ENOENT') return null;
            throw error;
        });
        // Node reports Windows directory junctions as symbolic links, too.
        if (info?.isSymbolicLink()) throw new Error(`Linked animation path: ${current}`);
    }
}

async function readRegular(root, name) {
    const path = join(root, basename(name));
    await noLinks(path);
    if (!(await lstat(path)).isFile()) throw new Error(`Non-regular animation: ${name}`);
    return readFile(path);
}

export function releaseManifest(source, contract = factoryContract) {
    const manifest = structuredClone(source);
    for (const filename of Object.values(manifest.clips || {})) basename(filename);
    const clips = Object.fromEntries(Object.entries(contract.clips).map(([name, item]) => {
        basename(name);
        basename(item.filename);
        if (manifest.clips?.[name] !== item.filename) throw new Error(`Missing factory clip: ${name}`);
        return [name, item.filename];
    }));
    const available = new Set(Object.keys(clips));
    manifest.clips = clips;
    for (const field of ['idle_pool', 'return_idle_pool', 'ambient_poses', 'runtime_only', 'body_only_clips']) {
        manifest[field] = (manifest[field] || []).filter((name) => available.has(name));
    }
    const menu = new Set([...available, ...(contract.procedural_actions || [])]);
    manifest.menu_poses = (manifest.menu_poses || []).filter((name) => menu.has(name));
    manifest.pose_map = Object.fromEntries(Object.entries(manifest.pose_map || {})
        .filter(([, clip]) => available.has(clip)));
    for (const field of ['_source_mapping', 'facial_profiles']) {
        manifest[field] = Object.fromEntries(Object.entries(manifest[field] || {})
            .filter(([name]) => available.has(name)));
    }
    manifest.activity_clips = Object.fromEntries(Object.entries(manifest.activity_clips || {})
        .filter(([, value]) => available.has(value.clip)));
    if (!manifest.idle_pool.includes(contract.rest_pose)
        || !manifest.return_idle_pool.includes(contract.rest_pose)) {
        throw new Error('Missing mandatory rest pose');
    }
    for (const state of ['listening', 'thinking', 'speaking']) {
        if (!manifest.activity_clips[state]) throw new Error(`Missing animation state: ${state}`);
    }
    manifest._packaging_notice = 'Only explicitly approved factory animations are distributed.';
    return manifest;
}

export function releaseContexts(source, contract = factoryContract) {
    const document = structuredClone(source);
    const allowed = new Set([...Object.keys(contract.clips), ...(contract.procedural_actions || [])]);
    for (const mappings of Object.values(document.contexts || {})) {
        for (const item of Object.values(mappings)) {
            if (!allowed.has(item.animation)) {
                const fallback = contract.context_fallbacks?.[item.animation];
                if (!allowed.has(fallback)) throw new Error(`Missing context fallback: ${item.animation}`);
                item.animation = fallback;
            }
        }
    }
    return document;
}

export async function prepareAnimationAssets(source, contract = factoryContract) {
    if (contract.schema !== 1 || !contract.clips?.[contract.rest_pose]) {
        throw new Error('Unknown factory contract or missing rest pose');
    }
    const assets = new Map();
    const seen = new Set();
    for (const item of Object.values(contract.clips)) {
        const name = basename(item.filename);
        if (seen.has(name.toLowerCase())) throw new Error(`Duplicate factory filename: ${name}`);
        seen.add(name.toLowerCase());
        const payload = await readRegular(source, name);
        if (createHash('sha256').update(payload).digest('hex') !== item.sha256) {
            throw new Error(`Incorrect animation hash: ${name}`);
        }
        assets.set(name, payload);
    }
    if (contract.avatar) {
        // IRIS' own VRM, with its own licence (not MIT): pinned like the clips.
        const name = basename(contract.avatar.filename);
        const license = basename(contract.avatar.license);
        if (!/\.vrm$/i.test(name) || seen.has(name.toLowerCase()) || seen.has(license.toLowerCase())
            || name.toLowerCase() === license.toLowerCase() || !/^[0-9a-f]{64}$/.test(contract.avatar.sha256)) {
            throw new Error('Invalid factory avatar');
        }
        const payload = await readRegular(source, name);
        if (createHash('sha256').update(payload).digest('hex') !== contract.avatar.sha256) {
            throw new Error(`Incorrect avatar hash: ${name}`);
        }
        assets.set(name, payload);
        assets.set(license, await readRegular(source, license));
    }
    for (const [name, project] of [['manifest.json', releaseManifest], ['contexts.json', releaseContexts]]) {
        const document = project(JSON.parse(await readRegular(source, name)), contract);
        assets.set(name, Buffer.from(`${JSON.stringify(document, null, 2)}\n`));
    }
    assets.set('README.md', await readRegular(source, 'README.md'));
    assets.set('emotions.json', await readRegular(source, 'emotions.json'));
    return assets;
}

export async function copyAnimationAssets(source, target) {
    const assets = await prepareAnimationAssets(source);
    await noLinks(target);
    await mkdir(target, { recursive: true });
    for (const name of await readdir(target)) {
        if (!assets.has(name)) throw new Error(`Unexpected old animation in bundle: ${name}`);
        await noLinks(join(target, name));
        if (!(await lstat(join(target, name))).isFile()) throw new Error(`Non-regular output: ${name}`);
    }
    for (const [name, payload] of assets) await writeFile(join(target, name), payload);
}
