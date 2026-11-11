import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { zipSync } from '../../companion/node_modules/three/examples/jsm/libs/fflate.module.js';

const vendor = new URL('../../companion/node_modules/three/examples/jsm/libs/fflate.module.js', import.meta.url);
const source = readFileSync(new URL('../../face/js/pmx_archive.js', import.meta.url), 'utf8')
    .replace("'three/addons/libs/fflate.module.js'", JSON.stringify(vendor.href));
const { archiveKey, openPmxPackage } = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));

test('a legacy Chinese ZIP lists every variant before inflating any member', () => {
    const bytes = zipSync({
        'ABCD/ABCD.pmx': Uint8Array.of(1, 2, 3),
        'ABCD/ABCD_stage2.pmx': Uint8Array.of(4, 5, 6),
    }, { level: 0 });
    const placeholder = Buffer.from('ABCD');
    const name = Buffer.from([0xe7, 0xb3, 0xd1, 0xa9]); // 绯雪 in GBK
    const buffer = Buffer.from(bytes);
    for (let i = buffer.indexOf(placeholder); i >= 0; i = buffer.indexOf(placeholder, i + placeholder.length)) {
        name.copy(buffer, i);
    }

    const pkg = openPmxPackage(buffer, { forImport: true });
    assert.deepEqual(pkg.models.map(entry => entry.name), ['绯雪/绯雪.pmx', '绯雪/绯雪_stage2.pmx']);
    const [first, second] = pkg.models;
    buffer[first.dataOffset] ^= 1;
    assert.deepEqual([...pkg.read(second)], [4, 5, 6]);
    assert.throws(() => pkg.read(first), /archivo dañado/);
});

// A ZIP whose names were written on Japanese or Chinese Windows: no UTF-8 flag, raw code page bytes.
function legacyZip(files, bytes) {
    const buffer = Buffer.from(zipSync(files, { level: 0 }));
    const placeholder = Buffer.from('ABCD');
    for (let i = buffer.indexOf(placeholder); i >= 0; i = buffer.indexOf(placeholder, i + placeholder.length)) {
        Buffer.from(bytes).copy(buffer, i);
    }
    return buffer;
}

test('a Japanese ZIP reads its Shift_JIS names as Japanese', () => {
    const buffer = legacyZip({ 'ABCD/ABCD.pmx': Uint8Array.of(1), 'ABCD/tex.png': Uint8Array.of(2) }, [0x83, 0x7e, 0x83, 0x4e]);
    const pkg = openPmxPackage(buffer);
    assert.equal(pkg.encoding, 'shift_jis');
    assert.deepEqual(pkg.encodings, ['shift_jis', 'gb18030']);
    assert.deepEqual(pkg.models.map(entry => entry.name), ['ミク/ミク.pmx']);
    assert.equal(pkg.texture(pkg.models[0], 'tex.png')?.name, 'ミク/tex.png');
    assert.deepEqual(openPmxPackage(buffer, { encoding: 'gb18030' }).models.map(entry => entry.name), ['儈僋/儈僋.pmx']);
});

test('the reading in which the model finds its textures wins when both code pages read the names', () => {
    // 初音 in Shift_JIS, with no kana to tell it from Chinese.
    const buffer = legacyZip({ 'ABCD/model.pmx': Uint8Array.of(1), 'ABCD/tex/ABCD.png': Uint8Array.of(2) },
        [0x8f, 0x89, 0x89, 0xb9]);
    assert.equal(openPmxPackage(buffer).encoding, 'gb18030');
    const seen = [];
    const pkg = openPmxPackage(buffer, { texturesOf: content => { seen.push([...content]); return ['tex\\初音.png', '']; } });
    assert.equal(pkg.encoding, 'shift_jis');
    assert.deepEqual(seen, [[1]]);
    assert.equal(pkg.texture(pkg.models[0], 'tex\\初音.png')?.name, '初音/tex/初音.png');
    assert.equal(openPmxPackage(buffer, { texturesOf: () => ['missing.png'] }).encoding, 'gb18030');
});

test('every variant counts: the first may name its textures in ASCII and the next in Japanese', () => {
    // 初音 in Shift_JIS again. Variant a.pmx draws with tex/a.png, b.pmx with 初音.png.
    const buffer = legacyZip({
        'ABCD/a.pmx': Uint8Array.of(1), 'ABCD/b.pmx': Uint8Array.of(2),
        'ABCD/tex/a.png': Uint8Array.of(3), 'ABCD/ABCD.png': Uint8Array.of(4),
    }, [0x8f, 0x89, 0x89, 0xb9]);
    const texturesOf = content => (content[0] === 1 ? ['tex/a.png'] : ['初音.png']);
    const pkg = openPmxPackage(buffer, { forImport: true, texturesOf });
    assert.equal(pkg.encoding, 'shift_jis');
    const [first, second] = pkg.models;
    assert.equal(pkg.texture(first, 'tex/a.png')?.name, '初音/tex/a.png');
    assert.equal(pkg.texture(second, '初音.png')?.name, '初音/初音.png');
});

test('UTF-8 names need no guessing', () => {
    const pkg = openPmxPackage(zipSync({ '初音/model.pmx': Uint8Array.of(1) }), { texturesOf: () => assert.fail('parsed') });
    assert.equal(pkg.encoding, 'utf-8');
    assert.deepEqual(pkg.encodings, []);
    assert.deepEqual(pkg.models.map(entry => entry.name), ['初音/model.pmx']);
});

test('archive entries cannot escape the ZIP or collide after normalizing case', () => {
    const model = Uint8Array.of(1);
    assert.throws(() => openPmxPackage(zipSync({ '../a.pmx': model })), /ruta inválida/);
    assert.throws(() => openPmxPackage(zipSync({ 'A.pmx': model, 'a.PMX': model })), /rutas duplicadas/);
    assert.equal(archiveKey('model/../tex/Color.PNG', { texture: true }), 'tex/color.png');
    assert.throws(() => archiveKey('model/../../outside.png', { texture: true }), /ruta inválida/);
});
