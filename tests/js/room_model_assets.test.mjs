import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { PUBLIC_ROOM_MODELS, prepareWebResources, webPath } from '../../scripts/web_assets.mjs';

test('every factory furniture URL has an explicit web resource, while arbitrary models stay rejected', async () => {
    const source = await readFile(new URL('../../face/js/room.js', import.meta.url), 'utf8');
    const referenced = [...source.matchAll(/\w+Model: '\/face\/public\/assets\/([^']+\.glb)'/g)].map(m => m[1]);
    assert.ok(referenced.length > 0);
    assert.deepEqual([...PUBLIC_ROOM_MODELS].sort(), referenced.sort());
    for (const name of PUBLIC_ROOM_MODELS) assert.equal(webPath('face/public/assets/' + name), 'face/public/assets/' + name);
    for (const name of ['private.glb', '_draft.glb', 'iris.vrm', '../sofa-v1.glb', 'SOFA-V1.glb', 'sofa-v1.glb/secret.env']) {
        assert.throws(() => webPath('face/public/assets/room/' + name));
    }
    assert.throws(() => webPath('face/private/sofa-v1.glb'));
});

test('prepared web resources contain the unchanged, embedded GLB bytes of the factory furniture', async () => {
    const root = fileURLToPath(new URL('../../', import.meta.url));
    const { files } = await prepareWebResources(root);
    const models = [...files.keys()].filter(p => p.endsWith('.glb'));
    assert.equal(models.length, PUBLIC_ROOM_MODELS.length);
    for (const name of PUBLIC_ROOM_MODELS) {
        const path = 'face/public/assets/' + name, bytes = files.get(path);
        assert.deepEqual(bytes, await readFile(new URL('../../' + path, import.meta.url)));
        assert.equal(bytes.toString('ascii', 0, 4), 'glTF');
        assert.equal(bytes.readUInt32LE(8), bytes.length);
        const document = JSON.parse(bytes.toString('utf8', 20, 20 + bytes.readUInt32LE(12)).trim());
        assert.ok((document.buffers || []).every(b => !b.uri), 'models must not need an external buffer');
        assert.ok((document.images || []).every(i => i.bufferView !== undefined), 'model textures must be embedded');
    }
});
