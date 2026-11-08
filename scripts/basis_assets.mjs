/** The local Basis Universal build for KTX2Loader and the Companion CSP. */
import { createHash } from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const DEFAULT_ROOT = fileURLToPath(new URL('..', import.meta.url));
const THREE = 'companion/node_modules/three';
const THREE_VERSION = '0.170.0';

/** Every build serves these at /face/vendor/basis/ (face/js/ktx2_support.js). */
export const BASIS_FILES = Object.freeze([
    { source: 'companion/vendor/basis/basis_transcoder.js', path: 'basis_transcoder.js',
        sha256: 'f20ec081f83abc120fe0340148932e1a467714eaead15a1d7e5f701d2c7ad4f0' },
    { source: 'companion/vendor/basis/basis_transcoder.wasm', path: 'basis_transcoder.wasm',
        sha256: 'fcedbec9f363e7ce4bfc6c1a97325c4a035f24c72d17403c3ea84561daf60914' },
    { source: 'companion/third-party/BasisUniversal-Apache-2.0.txt', path: 'BasisUniversal-Apache-2.0.txt',
        sha256: '8d4125b7304054beeded88cc32c3ea7de7c7b310b33647616c9a905887944f20' },
]);

export const BASIS_LICENSE = Object.freeze({
    package: 'basis_universal', version: '1.50.0.2', license: 'Apache-2.0',
});

const digest = (bytes) => createHash('sha256').update(bytes).digest('hex');

/**
 * Copy the pinned transcoder and its notice into `target`. A file already there is kept only
 * when it has the same bytes; anything else stops the build.
 */
export async function prepareBasisTranscoder({ projectRoot = DEFAULT_ROOT, target } = {}) {
    if (!target) throw new Error('A target directory for the Basis transcoder is required');
    const root = resolve(projectRoot);
    const three = JSON.parse(await readFile(join(root, THREE, 'package.json'), 'utf8'));
    if (three.version !== THREE_VERSION) throw new Error(`Unexpected three.js for the Basis transcoder: ${three.version}`);
    const sources = await Promise.all(BASIS_FILES.map(async (file) => {
        const bytes = await readFile(join(root, file.source));
        if (digest(bytes) !== file.sha256) throw new Error(`Unexpected Basis transcoder file: ${file.source}`);
        return { file, bytes };
    }));
    await mkdir(target, { recursive: true });
    const files = [];
    for (const { file, bytes } of sources) {
        const destination = join(target, file.path);
        try {
            await writeFile(destination, bytes, { flag: 'wx' });
        } catch (error) {
            if (error.code !== 'EEXIST') throw error;
            if (digest(await readFile(destination)) !== file.sha256) {
                throw new Error(`A different ${file.path} is already in ${target}`);
            }
        }
        files.push({ path: file.path, size: bytes.length, sha256: file.sha256 });
    }
    return files;
}

// Source preview: node scripts/basis_assets.mjs face/vendor/basis
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
    const target = process.argv[2];
    if (!target) throw new Error('Usage: node scripts/basis_assets.mjs <target directory>');
    const files = await prepareBasisTranscoder({ target: resolve(target) });
    console.log(`Basis transcoder ready in ${target} (${files.length} files).`);
}
