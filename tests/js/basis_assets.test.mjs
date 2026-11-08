import assert from 'node:assert/strict';
import test from 'node:test';
import { createHash } from 'node:crypto';
import { copyFile, mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { BASIS_FILES, BASIS_LICENSE, prepareBasisTranscoder } from '../../scripts/basis_assets.mjs';

const sha = bytes => createHash('sha256').update(bytes).digest('hex');

test('the pinned transcoder and its notice are copied byte for byte', async () => {
    const target = await mkdtemp(join(tmpdir(), 'iris-basis-'));
    try {
        const files = await prepareBasisTranscoder({ target });
        assert.deepEqual(files.map(f => f.path), ['basis_transcoder.js', 'basis_transcoder.wasm', 'BasisUniversal-Apache-2.0.txt']);
        for (const file of BASIS_FILES) assert.equal(sha(await readFile(join(target, file.path))), file.sha256);
        assert.equal(BASIS_LICENSE.license, 'Apache-2.0');
        // Running it again over the same files is fine; a changed file stops the build.
        await prepareBasisTranscoder({ target });
        await writeFile(join(target, 'basis_transcoder.js'), 'tampered');
        await assert.rejects(prepareBasisTranscoder({ target }), /A different basis_transcoder.js is already in/);
    } finally {
        await rm(target, { recursive: true, force: true });
    }
});

test('another three.js package is refused before anything is copied', async () => {
    const project = await mkdtemp(join(tmpdir(), 'iris-basis-root-'));
    try {
        await mkdir(join(project, 'companion/node_modules/three'), { recursive: true });
        await writeFile(join(project, 'companion/node_modules/three/package.json'), JSON.stringify({ version: '0.171.0' }));
        await assert.rejects(prepareBasisTranscoder({ projectRoot: project, target: join(project, 'out') }), /Unexpected three\.js/);
        await assert.rejects(readFile(join(project, 'out', 'basis_transcoder.js')), /ENOENT/);
    } finally {
        await rm(project, { recursive: true, force: true });
    }
});

test('a changed vendored WASM prevents publishing a mixed transcoder pair', async () => {
    const project = await mkdtemp(join(tmpdir(), 'iris-basis-root-'));
    const target = join(project, 'out');
    try {
        const three = 'companion/node_modules/three/package.json';
        for (const source of [three, ...BASIS_FILES.map(file => file.source)]) {
            const destination = join(project, source);
            await mkdir(dirname(destination), { recursive: true });
            await copyFile(new URL(`../../${source}`, import.meta.url), destination);
        }
        const wasm = BASIS_FILES.find(file => file.path === 'basis_transcoder.wasm');
        await writeFile(join(project, wasm.source), 'tampered');
        await assert.rejects(prepareBasisTranscoder({ projectRoot: project, target }), /Unexpected Basis transcoder file/);
        await assert.rejects(readFile(join(target, 'basis_transcoder.js')), /ENOENT/);
    } finally {
        await rm(project, { recursive: true, force: true });
    }
});
