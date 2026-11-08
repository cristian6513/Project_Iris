import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';

// The module imports three's KTX2Loader through the page's import map; here it is replaced by
// a recording stand-in so only IRIS' wiring is under test.
const source = readFileSync(new URL('../../face/js/ktx2_support.js', import.meta.url), 'utf8')
    .replace(/^import .*$/m, '').replace(/^export /gm, '');
function load() {
    const made = [];
    class KTX2Loader {
        constructor() { made.push(this); this.detected = []; }
        setTranscoderPath(path) { this.path = path; return this; }
        detectSupport(renderer) { this.detected.push(renderer); return this; }
    }
    const api = new Function('KTX2Loader', `${source}; return { useKTX2, sharedKTX2, BASIS_TRANSCODER_PATH };`)(KTX2Loader);
    return { ...api, made };
}
const gltf = () => ({ setKTX2Loader(loader) { this.ktx2 = loader; return this; } });

test('without a renderer the loader is left as it is and no decoder starts', () => {
    const { useKTX2, made } = load();
    const loader = gltf();
    assert.equal(useKTX2(loader, null), loader);
    assert.equal(loader.ktx2, undefined);
    assert.equal(made.length, 0);
});

test('every loader shares one decoder that reads the local transcoder', () => {
    const { useKTX2, BASIS_TRANSCODER_PATH, made } = load();
    const renderer = {}, a = gltf(), b = gltf();
    assert.equal(useKTX2(a, renderer), a);
    useKTX2(b, renderer);
    assert.equal(made.length, 1);
    assert.equal(a.ktx2, b.ktx2);
    assert.equal(made[0].path, BASIS_TRANSCODER_PATH);
    assert.equal(BASIS_TRANSCODER_PATH, '/face/vendor/basis/');
    assert.deepEqual(made[0].detected, [renderer]);
});

test('the room pictures get the same decoder as the models', () => {
    const { useKTX2, sharedKTX2, made } = load();
    const renderer = {}, a = gltf();
    assert.equal(sharedKTX2(null), null);
    useKTX2(a, renderer);
    assert.equal(sharedKTX2(renderer), a.ktx2);
    assert.equal(made.length, 1);
});
