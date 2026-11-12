import { createHash } from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = fileURLToPath(new URL('..', import.meta.url));
const VENDOR = 'companion/node_modules/three/examples/jsm/libs/';
const FILES = [
    ['ammo.module.js', VENDOR + 'ammo.wasm.js', '3fa63c584d4732f72b954c5ea105f1faca99559c95c57ce7bb738d3497f98228'],
    ['ammo.wasm.wasm', VENDOR + 'ammo.wasm.wasm', '16be07b989963bddebfef66eab28a4387536eb37014d9df1531dab380bd96a93'],
    ['Ammo-Zlib.txt', 'companion/third-party/Ammo-Zlib.txt', 'f9a230c1a7f6cb856a80b31041e8c0228c5eece8ef6f564b79b3bc8097927a97'],
];

export const AMMO_LICENSE = {
    package: 'ammo.js', version: 'three-r170', license: 'Zlib',
    source: 'https://github.com/mrdoob/three.js/tree/r170/examples/jsm/libs',
    files: ['face/vendor/ammo/Ammo-Zlib.txt'],
};

const digest = bytes => createHash('sha256').update(bytes).digest('hex');

export async function prepareAmmoAssets({ projectRoot = ROOT, target }) {
    const assets = await Promise.all(FILES.map(async ([name, source, sha256]) => {
        const bytes = await readFile(join(projectRoot, source));
        if (digest(bytes) !== sha256) throw new Error('Unexpected Ammo asset: ' + source);
        const output = name === 'ammo.module.js'
            ? Buffer.concat([bytes, Buffer.from('\n// Iris adaptation: export the unchanged Ammo factory for module workers.\nexport default Ammo;\n')])
            : bytes;
        return [name, output];
    }));
    await mkdir(target, { recursive: true });
    for (const [name, bytes] of assets) {
        const destination = join(target, name);
        try {
            await writeFile(destination, bytes, { flag: 'wx' });
        } catch (error) {
            if (error.code !== 'EEXIST' || !(await readFile(destination)).equals(bytes)) throw error;
        }
    }
}

// Source preview: node scripts/ammo_assets.mjs face/vendor/ammo
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
    if (!process.argv[2]) throw new Error('Usage: node scripts/ammo_assets.mjs <target directory>');
    await prepareAmmoAssets({ target: resolve(process.argv[2]) });
}
