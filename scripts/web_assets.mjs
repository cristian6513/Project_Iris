/** Local web resources. Every copied input is explicit and must be a regular file. */
import { createHash } from 'node:crypto';
import { lstat, mkdir, mkdtemp, readFile, readdir, rename, rm, rmdir, writeFile } from 'node:fs/promises';
import { dirname, join, parse, relative, resolve } from 'node:path';

export const WEB_VERSIONS = Object.freeze({
    three: '0.170.0',
    '@pixiv/three-vrm': '3.5.5',
    '@pixiv/three-vrm-animation': '3.5.5',
    '@pixiv/three-vrm-springbone': '3.5.5',
    '@mediapipe/tasks-vision': '0.10.35',
    '@tauri-apps/api': '2.11.1',
    '@fontsource/outfit': '5.3.0',
    '@fontsource/syne': '5.3.0',
    '@fontsource/jetbrains-mono': '5.2.8',
    'qrcode-generator': '1.4.4',
    vite: '8.2.1',
});

export const WEB_ENTRYPOINTS = Object.freeze({
    face: 'face/index.html',
    companion: 'face/companion/index.html',
    wizard: 'wizard/ui/index.html',
    help: 'wizard/ui/help.html',
});

export const PUBLIC_IMAGES = Object.freeze([
    'os/iris-os-bg-v1.png', 'os/iris-profile-icon-v1.png',
    'os/theme-classic.png', 'os/theme-hacker.png', 'os/theme-sakura.png',
    'room/cyber-circular-rug-muted-v2.png', 'room/cyber-circular-rug-v1.png',
    'room/cyber-floor-panels-v1.png', 'room/cyber-lounge-rug-v1.png',
    'room/cyber-wall-complete-v1.png', 'room/cyber-wall-hex-v1.png',
    'room/cyber-wall-panels-d-v1.png', 'room/cyber-window-city-v2.png',
    'room/sakura-window-city-v1.png', 'room/hacker-window-city-v1.png',
    'room/classic-window-city-v1.png',
    // GPU-compressed copies the room loads first; the PNGs above stay as their fallback.
    'room/cyber-floor-panels-v1.ktx2', 'room/cyber-wall-complete-v1.ktx2',
    'room/cyber-window-city-v2.ktx2', 'room/sakura-window-city-v1.ktx2',
    'room/hacker-window-city-v1.ktx2', 'room/classic-window-city-v1.ktx2',
]);

// Factory furniture only. Do not copy arbitrary GLBs, avatars or Blender sources.
export const PUBLIC_ROOM_MODELS = Object.freeze([
    'room/bookshelf-v1.glb', 'room/sofa-v1.glb', 'room/table-v1.glb',
    'room/books-stack-v1.glb', 'room/book-v1.glb', 'room/bed-v1.glb', 'room/mug-pencils-v1.glb',
    'room/chair-v1.glb', 'room/desk-v1.glb', 'room/monitors-v1.glb',
    'room/tablet-v2.glb', 'room/pencils-loose-v1.glb', 'room/speaker-v1.glb',
    'room/keyboard-mouse-v1.glb', 'room/tower-lamp-v1.glb', 'room/ceiling-lamp-v1.glb',
    'room/shelf-v1.glb', 'room/plant-v1.glb',
]);
const ROOM_MODEL_PATHS = new Set(PUBLIC_ROOM_MODELS.map(name => `face/public/assets/${name}`));

const FONT_WEIGHTS = [
    ['outfit', 'Outfit', [300, 400, 500, 600, 700]],
    ['syne', 'Syne', [400, 500, 600, 700, 800]],
    ['jetbrains-mono', 'JetBrains Mono', [300, 400, 500, 600]],
];

const PINNED_WEB_NOTICES = [
    ['qrcode-generator', '1.4.4', 'qrcode-generator-MIT.txt', '3a850fa5f08101db6f40676c2786e10bd2cd5fff7b12ffdf1e0c434d4e49d90c'],
    ['fflate', '0.8.2', 'fflate-MIT.txt', '370679c457d36555127bbcefe8f57361f7852f04df0f2329a065f566f26f0d76'],
    ['mmd-parser', '1.0.4', 'mmd-parser-MIT.txt', '23c9323625b14f3064c0ceca73d75e6e84c98151dcab2f1eccb54254f5a310c2'],
];

export function sha256(bytes) {
    return createHash('sha256').update(bytes).digest('hex');
}

export function webPath(name) {
    if (typeof name !== 'string' || !name || /[\\:\x00-\x1f]/.test(name)
        || !/^(face\/|wizard\/ui\/)/.test(name)
        || name.split('/').some((part) => !part || part === '.' || part === '..'
            || /[ .]$/.test(part) || /^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?$/i.test(part))
        || /\.(map|vrm|fbx|bvh|exe|dll|env)$/i.test(name)
        || (/\.glb$/i.test(name) && !ROOM_MODEL_PATHS.has(name))) {
        throw new Error(`Invalid web asset path: ${name}`);
    }
    return name;
}

export function localBuildPath(value, label = 'path') {
    if (typeof value !== 'string' || !value || value.includes('\0') || value.startsWith('\\\\')) {
        throw new Error(`Invalid local build ${label}`);
    }
    const trimmed = value.replace(/[\\/]+$/, '');
    const remainder = trimmed.slice(parse(trimmed).root.length);
    if (!remainder || /[<>:"|?*\x00-\x1f]/.test(remainder)
        || remainder.split(/[\\/]/).some((part) => !part || part === '.' || part === '..'
            || /[ .]$/.test(part) || /^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?$/i.test(part))) {
        throw new Error(`Unsafe local build ${label}: ${value}`);
    }
    return resolve(trimmed);
}

export async function noLinkedPath(path) {
    const absolute = resolve(path);
    let current = parse(absolute).root;
    for (const part of absolute.slice(current.length).split(/[\\/]/).filter(Boolean)) {
        const names = await readdir(current).catch((error) => {
            if (error.code === 'ENOENT') return null;
            throw error;
        });
        if (!names) return;
        const aliases = names.filter((name) => name.toLowerCase() === part.toLowerCase());
        if (aliases.length > 1 || (aliases.length === 1 && aliases[0] !== part)) {
            throw new Error(`Aliased web asset path: ${absolute}`);
        }
        current = join(current, part);
        const info = await lstat(current).catch((error) => {
            if (error.code === 'ENOENT') return null;
            throw error;
        });
        if (info?.isSymbolicLink()) throw new Error(`Linked web asset path: ${current}`);
        if (info && aliases.length !== 1) throw new Error(`Aliased web asset path: ${absolute}`);
        if (!info) return;
    }
}

export async function readRegular(path) {
    await noLinkedPath(path);
    const info = await lstat(path);
    if (!info.isFile() || info.nlink !== 1) throw new Error(`Non-regular web asset: ${path}`);
    return readFile(path);
}

export async function readWebNotice(projectRoot, source) {
    const notice = PINNED_WEB_NOTICES.find((item) => item[2] === source);
    if (!notice) throw new Error(`Unknown web notice: ${source}`);
    const bytes = await readRegular(join(projectRoot, 'scripts/third-party', source));
    if (sha256(bytes) !== notice[3]) throw new Error(`Incorrect web license hash: ${notice[0]}`);
    return bytes;
}

export async function reserveOutput(path) {
    path = localBuildPath(path, 'output');
    await noLinkedPath(path);
    const info = await lstat(path).catch((error) => {
        if (error.code === 'ENOENT') return null;
        throw error;
    });
    if (info) throw new Error(`Web output must be new: ${path}`);
    await mkdir(dirname(resolve(path)), { recursive: true });
    await mkdir(path);
}

export async function validateContractOutput(path) {
    const target = localBuildPath(path, 'contract');
    await noLinkedPath(target);
    if (!(await lstat(dirname(target))).isDirectory()) throw new Error('Contract parent must exist');
    const info = await lstat(target).catch((error) => {
        if (error.code === 'ENOENT') return null;
        throw error;
    });
    if (info && (!info.isFile() || info.nlink !== 1)) throw new Error('Web contract must be a regular unlinked file');
    return target;
}

export async function publishWebContract(path, bytes) {
    const target = await validateContractOutput(path);
    const temp = await mkdtemp(join(dirname(target), '.iris-web-contract-'));
    const file = join(temp, 'contract.json');
    try {
        await writeFile(file, bytes, { flag: 'wx' });
        await validateContractOutput(target);
        await rename(file, target);
    } finally {
        await rm(file, { force: true });
        await rmdir(temp);
    }
}

export function stripNetworkFonts(html) {
    return html.replace(/\s*<link\b[^>]*(?:fonts\.googleapis\.com|fonts\.gstatic\.com)[^>]*>/gi, '');
}

export function localWizardText(text, filename) {
    if (filename.endsWith('.html')) {
        return stripNetworkFonts(text).replace('</head>',
            '    <link rel="stylesheet" href="/ui/vendor/fonts/fonts.css">\n</head>');
    }
    if (filename.endsWith('.css')) {
        return text.replace(/@import\s+url\([^)]*(?:fonts\.googleapis\.com|fonts\.gstatic\.com)[^)]*\)\s*;/gi, '');
    }
    return text;
}

export async function validateWebDependencies(projectRoot) {
    const lock = JSON.parse(await readRegular(join(projectRoot, 'companion/package-lock.json')));
    for (const [name, version] of Object.entries(WEB_VERSIONS)) {
        const installed = JSON.parse(await readRegular(join(projectRoot, 'companion/node_modules', name, 'package.json')));
        if (installed.version !== version || lock.packages?.[`node_modules/${name}`]?.version !== version) {
            throw new Error(`Incorrect web dependency: ${name}; expected ${version}`);
        }
    }
}

async function sourceFiles(root, folder, extensions) {
    const absolute = join(root, folder);
    await noLinkedPath(absolute);
    const names = (await readdir(absolute)).filter((name) => extensions.includes(name.split('.').at(-1))).sort();
    const result = new Map();
    for (const name of names) {
        const target = `${folder}/${name}`;
        webPath(target);
        result.set(target, await readRegular(join(absolute, name)));
    }
    return result;
}

export async function prepareWebResources(projectRoot) {
    const root = resolve(projectRoot);
    await validateWebDependencies(root);
    const files = new Map();
    const licenses = [];
    for (const [folder, extensions] of [
        ['face/js', ['js', 'json']], ['face/css', ['css']],
        ['face/companion', ['js', 'css']], ['wizard/ui', ['html', 'js', 'css']],
    ]) {
        for (const [name, bytes] of await sourceFiles(root, folder, extensions)) {
            const text = name === 'face/companion/companion.js'
                ? bytes.toString('utf8').replace("new URL('./mediapipe/', document.baseURI)",
                    "new URL('/face/vendor/mediapipe/', document.baseURI)") : null;
            files.set(name, name.startsWith('wizard/ui/')
                ? Buffer.from(localWizardText(bytes.toString('utf8'), name)) : text === null ? bytes : Buffer.from(text));
        }
    }
    // Companion's raw stylesheet is retained for source consumers. Its fonts
    // are copied from the same pinned packages used by the bundled entrypoint.
    for (const font of ['outfit-latin-400-normal.woff2', 'outfit-latin-600-normal.woff2',
        'outfit-latin-700-normal.woff2', 'syne-latin-600-normal.woff2',
        'syne-latin-700-normal.woff2', 'syne-latin-800-normal.woff2']) {
        const family = font.startsWith('outfit') ? 'outfit' : 'syne';
        const bytes = await readRegular(join(root, 'companion/node_modules/@fontsource', family, 'files', font));
        if (sha256(await readRegular(join(root, 'face/companion/fonts', font))) !== sha256(bytes)) {
            throw new Error(`Unexpected Companion font: ${font}`);
        }
        files.set(`face/companion/fonts/${font}`, bytes);
    }
    for (const image of [...PUBLIC_IMAGES, ...PUBLIC_ROOM_MODELS]) {
        files.set(`face/public/assets/${image}`, await readRegular(join(root, 'face/public/assets', image)));
    }
    const qr = await readRegular(join(root, 'companion/node_modules/qrcode-generator/qrcode.js'));
    if (sha256(qr) !== '18ae399f81182bc9de916e9c77b195df20cc58d6f2d55a62b085a299f1bf1780') {
        throw new Error('Incorrect local QR source hash');
    }
    files.set('face/vendor/qrcode/qrcode.mjs', Buffer.from(`${qr.toString('utf8')}\nexport default qrcode;\n`));
    for (const [family, , weights] of FONT_WEIGHTS) {
        const pkg = `@fontsource/${family}`;
        let css = '';
        const fontFiles = new Set();
        for (const weight of weights) {
            let part = (await readRegular(join(root, 'companion/node_modules', pkg, `${weight}.css`))).toString('utf8');
            // Keep the package's unicode ranges and every language subset; WOFF2
            // is available in all supported WebViews, so avoid redundant WOFF.
            part = part.replace(/,\s*url\([^)]*\.woff\)\s*format\('woff'\)/g, '');
            for (const match of part.matchAll(/url\(\.\/files\/([a-z0-9-]+\.woff2)\)/g)) fontFiles.add(match[1]);
            if (!part.includes('.woff2')) throw new Error(`Missing local font CSS: ${pkg}/${weight}`);
            css += part;
        }
        for (const name of fontFiles) {
            const bytes = await readRegular(join(root, 'companion/node_modules', pkg, 'files', name));
            for (const prefix of ['face', 'wizard/ui']) files.set(`${prefix}/vendor/fonts/files/${name}`, bytes);
        }
        for (const prefix of ['face', 'wizard/ui']) {
            const path = `${prefix}/vendor/fonts/${family}.css`;
            files.set(path, Buffer.from(css));
            const licensePath = `${prefix}/vendor/fonts/${family}-OFL-1.1.txt`;
            files.set(licensePath, await readRegular(join(root, 'companion/node_modules', pkg, 'LICENSE')));
        }
        licenses.push({ package: pkg, version: WEB_VERSIONS[pkg], license: 'OFL-1.1',
            files: ['face', 'wizard/ui'].map((prefix) => `${prefix}/vendor/fonts/${family}-OFL-1.1.txt`) });
    }
    for (const prefix of ['face', 'wizard/ui']) files.set(`${prefix}/vendor/fonts/fonts.css`,
        Buffer.from(FONT_WEIGHTS.map(([family]) => `@import url('./${family}.css');`).join('\n') + '\n'));
    for (const [name, version, source] of PINNED_WEB_NOTICES) {
        const bytes = await readWebNotice(root, source);
        const path = `face/vendor/licenses/${source}`;
        files.set(path, bytes);
        licenses.push({ package: name, version, license: 'MIT', files: [path] });
    }
    // Vite's emitted preload/modulepreload helpers include its runtime code.
    const runtimePackages = ['three', '@tauri-apps/api', 'vite',
        ...(await readdir(join(root, 'companion/node_modules/@pixiv'))).map((name) => `@pixiv/${name}`)];
    for (const pkg of runtimePackages.sort()) {
        const folder = join(root, 'companion/node_modules', pkg);
        const metadata = JSON.parse(await readRegular(join(folder, 'package.json')));
        if (pkg.startsWith('@pixiv/') && metadata.version !== '3.5.5') throw new Error(`Incorrect Pixiv dependency: ${pkg}`);
        const paths = [];
        for (const name of (await readdir(folder)).filter((name) => /^LICENSE/i.test(name)).sort()) {
            const path = `face/vendor/licenses/${pkg.replace(/[@/]/g, '_')}-${name}.txt`;
            files.set(path, await readRegular(join(folder, name)));
            paths.push(path);
        }
        if (!paths.length) throw new Error(`Missing web license: ${pkg}`);
        licenses.push({ package: pkg, version: metadata.version, license: metadata.license, files: paths });
    }
    return { files, licenses };
}

export async function writeWebResources(output, files) {
    for (const [name, bytes] of files) {
        webPath(name);
        const destination = join(output, name);
        await noLinkedPath(destination);
        await mkdir(dirname(destination), { recursive: true });
        await writeFile(destination, bytes);
    }
}

export async function inventoryWebFiles(output) {
    const files = [];
    const names = new Set();
    async function walk(folder) {
        await noLinkedPath(folder);
        for (const entry of await readdir(folder, { withFileTypes: true })) {
            const absolute = join(folder, entry.name);
            const name = relative(output, absolute).split('\\').join('/');
            if (entry.isSymbolicLink()) throw new Error(`Linked web asset path: ${name}`);
            if (name === 'face/animations' || name === 'face/web-assets.json') continue;
            if (name !== 'wizard/ui') webPath(name);
            if (entry.isDirectory()) await walk(absolute);
            else if (entry.isFile()) {
                if (names.has(name.toLowerCase())) throw new Error(`Duplicate web asset path: ${name}`);
                names.add(name.toLowerCase());
                const bytes = await readRegular(absolute);
                files.push({ path: name, size: bytes.length, sha256: sha256(bytes) });
            } else throw new Error(`Non-regular web asset: ${name}`);
        }
    }
    // Enumerate only these two mounts; unrelated candidate content is rejected.
    for (const name of await readdir(output)) {
        if (!['face', 'wizard'].includes(name)) throw new Error(`Unexpected web output: ${name}`);
        await walk(join(output, name));
    }
    return files.sort((a, b) => a.path < b.path ? -1 : a.path > b.path ? 1 : 0);
}

export async function assertLocalBootstrap(output, files) {
    const paths = new Set(files.map((item) => item.path));
    for (const path of Object.values(WEB_ENTRYPOINTS)) {
        if (!paths.has(path)) throw new Error(`Missing web entrypoint: ${path}`);
        const html = (await readRegular(join(output, path))).toString('utf8');
        if (/<(?:script|link)\b[^>]*(?:https?:|\/\/|importmap)/i.test(html)) {
            throw new Error(`Network bootstrap in web entrypoint: ${path}`);
        }
    }
    for (const item of files.filter((item) => item.path.endsWith('.css'))) {
        const css = (await readRegular(join(output, item.path))).toString('utf8');
        if (/@import[^;]*(?:https?:|\/\/)|url\(["']?(?:https?:|\/\/)/i.test(css)) {
            throw new Error(`Network font or CSS resource: ${item.path}`);
        }
    }
}
