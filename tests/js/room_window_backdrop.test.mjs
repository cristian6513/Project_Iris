import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { WindowBackdrop, WINDOW_BACKDROPS, WINDOW_BACKDROP_ASPECT, WINDOW_BACKDROP_SOURCE_ASPECT, fitBackdropTexture } from '../../face/js/room_window_backdrop.js';

function texture(name, width = 1834, height = 858) {
    const vector = () => ({ x: 0, y: 0, set(x, y) { Object.assign(this, { x, y }); } });
    return { name, image: { width, height }, offset: vector(), repeat: vector(), disposals: 0,
        dispose() { this.disposals++; } };
}
function setup() {
    const requests = [], warnings = [], initial = texture('initial');
    const material = { map: initial, userData: {}, stencilWrite: true, stencilRef: 1, depthTest: false };
    const backdrop = new WindowBackdrop({ material,
        loadTexture: url => new Promise((resolve, reject) => requests.push({ url, resolve, reject })),
        createFallback: theme => texture('fallback:' + theme, 1344, 512),
        logger: { warn: (...args) => warnings.push(args) },
    });
    return { backdrop, material, requests, warnings, initial };
}

test('only the selected landscape loads, in the existing stencil material', async () => {
    const h = setup(), loaded = texture('sakura');
    const pending = h.backdrop.setTheme('sakura');
    assert.equal(h.requests.length, 1);
    assert.equal(h.requests[0].url, WINDOW_BACKDROPS.sakura);
    assert.equal(h.material.map, h.initial, 'keep the last usable image during loading');
    h.requests[0].resolve(loaded);
    assert.equal(await pending, true);
    assert.equal(h.material.map, loaded);
    assert.equal(h.initial.disposals, 1);
    assert.deepEqual([h.material.stencilWrite, h.material.stencilRef, h.material.depthTest], [true, 1, false]);
    assert.equal(h.material.userData.assetUrl, WINDOW_BACKDROPS.sakura);
    assert.equal(h.backdrop.status, 'ready');
});

test('a late image or failure cannot replace the newer theme', async () => {
    for (const lateFailure of [false, true]) {
        const h = setup();
        const old = h.backdrop.setTheme('sakura'), latest = h.backdrop.setTheme('hacker');
        const green = texture('hacker'), stale = texture('sakura');
        h.requests[1].resolve(green); await latest;
        if (lateFailure) h.requests[0].reject(new Error('late error'));
        else h.requests[0].resolve(stale);
        assert.equal(await old, false);
        assert.equal(h.material.map, green);
        assert.equal(h.backdrop.theme, 'hacker');
        assert.equal(h.warnings.length, 0);
        if (!lateFailure) assert.equal(stale.disposals, 1);
    }
});

test('repeated selections coalesce, and A → slow B → A reuses A', async () => {
    const h = setup(), a = texture('A');
    const first = h.backdrop.setTheme('cyber');
    assert.equal(h.backdrop.setTheme('cyber'), first);
    h.requests[0].resolve(a); await first;
    const b = h.backdrop.setTheme('sakura');
    assert.equal(await h.backdrop.setTheme('cyber'), true);
    assert.equal(h.requests.length, 2, 'A was not loaded twice');
    const stale = texture('B'); h.requests[1].resolve(stale);
    assert.equal(await b, false);
    assert.equal(h.material.map, a); assert.equal(a.disposals, 0); assert.equal(stale.disposals, 1);
});

test('failure uses the selected theme fallback and permits a same-theme retry', async () => {
    const h = setup();
    const pending = h.backdrop.setTheme('classic');
    h.requests[0].reject(new Error('missing local image'));
    assert.equal(await pending, false);
    assert.equal(h.backdrop.status, 'fallback');
    assert.equal(h.material.map.name, 'fallback:classic');
    assert.equal(h.material.userData.assetUrl, '');
    const fallback = h.material.map, retry = h.backdrop.setTheme('classic');
    h.requests[1].resolve(texture('classic'));
    assert.equal(await retry, true); assert.equal(fallback.disposals, 1);
});

test('switching through all four themes keeps one active texture and disposes retired ones', async () => {
    const h = setup(), retired = [h.initial];
    for (let round = 0; round < 3; round++) {
        for (const theme of Object.keys(WINDOW_BACKDROPS)) {
            const t = texture(theme), pending = h.backdrop.setTheme(theme);
            h.requests.at(-1).resolve(t); await pending;
            assert.ok(retired.every(old => old.disposals === 1));
            assert.equal(t.disposals, 0); retired.push(t);
        }
    }
    h.backdrop.dispose();
    assert.ok(retired.every(t => t.disposals === 1));
    assert.equal(h.material.map, null);
});

test('disposal invalidates an in-flight decode and future selections', async () => {
    const h = setup(), pending = h.backdrop.setTheme('hacker');
    h.backdrop.dispose(); h.backdrop.dispose();
    const late = texture('late'); h.requests[0].resolve(late);
    assert.equal(await pending, false); assert.equal(late.disposals, 1);
    assert.equal(h.initial.disposals, 1); assert.equal(h.material.map, null);
    assert.equal(await h.backdrop.setTheme('classic'), false);
    for (const theme of ['__proto__', 'constructor', 'https://example.com/picture.png', '']) {
        assert.equal(await setup().backdrop.setTheme(theme), false);
    }
});

test('UV fitting uses the distant plane aspect, preserving image proportions', () => {
    for (const [width, height] of [[1834, 858], [1836, 857], [4096, 2048], [4000, 1000]]) {
        const t = texture('image', width, height);
        fitBackdropTexture(t);
        const visibleAspect = width * t.repeat.x / (height * t.repeat.y);
        assert.ok(Math.abs(visibleAspect - WINDOW_BACKDROP_ASPECT) < 1e-10);
        assert.ok(t.repeat.x > 0 && t.repeat.x <= 1 && t.repeat.y > 0 && t.repeat.y <= 1);
    }
    const original = texture('same-as-plane'); fitBackdropTexture(original);
    assert.deepEqual([original.repeat.x, original.repeat.y], [1, 1], 'the old window crop must not distort the city');
});

test('a KTX2 landscape stretched to 2048 x 1024 is framed by its picture proportions', async () => {
    for (const theme of Object.keys(WINDOW_BACKDROPS)) {
        assert.match(WINDOW_BACKDROPS[theme], /\.ktx2$/);
        const h = setup(), stretched = texture(theme, 2048, 1024), png = texture(theme, 1836, 857);
        const pending = h.backdrop.setTheme(theme); h.requests[0].resolve(stretched); await pending;
        assert.equal(stretched.userData.sourceAspect, WINDOW_BACKDROP_SOURCE_ASPECT[theme]);
        // Same crop as the PNG twin it stands for.
        png.userData = { sourceAspect: WINDOW_BACKDROP_SOURCE_ASPECT[theme] }; fitBackdropTexture(png);
        fitBackdropTexture(stretched);
        assert.ok(Math.abs(stretched.repeat.x - png.repeat.x) < 1e-12 && Math.abs(stretched.repeat.y - png.repeat.y) < 1e-12);
    }
});

test('the real room theme handler requests the landscape even after an image has loaded', async () => {
    const source = await readFile(new URL('../../face/js/room.js', import.meta.url), 'utf8');
    const block = source.match(/^    _applyTheme\([^]*?^    \}/m)?.[0];
    assert.ok(block);
    const requests = [];
    const document = { documentElement: { style: { setProperty() {} } } };
    const apply = new Function('ROOM_THEMES', 'THREE', 'document', 'return ({' + block.trim() + '})._applyTheme')(
        { sakura: { ambient: 1, gold: 2 } }, { Color: class {} }, document);
    const material = { userData: { assetUrl: WINDOW_BACKDROPS.cyber } };
    apply.call({ scene: {}, themeBindings: [{ material, backdrop: true }], group: { traverse() {} }, objects: {},
        windowBackdrop: { setTheme: theme => requests.push(theme) } }, 'sakura');
    assert.deepEqual(requests, ['sakura']);
});
