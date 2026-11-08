import assert from 'node:assert/strict';
import { link, mkdir, mkdtemp, readFile, realpath, rm, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { createQRCodeLoader } from '../../face/js/local_qr.js';
import { buildWeb, faceBuildConfig } from '../../scripts/build_web.mjs';
import {
    PUBLIC_IMAGES, PUBLIC_ROOM_MODELS, WEB_ENTRYPOINTS, WEB_VERSIONS, assertLocalBootstrap,
    inventoryWebFiles, localBuildPath, localWizardText, prepareWebResources, publishWebContract,
    readRegular, readWebNotice,
    reserveOutput, sha256, webPath,
} from '../../scripts/web_assets.mjs';

const root = resolve(fileURLToPath(new URL('../..', import.meta.url)));
let prepared;
const resources = () => prepared ||= prepareWebResources(root);

async function withTemp(action) {
    // Windows TEMP can itself be an 8.3 alias. Fixtures use its canonical
    // location so the production resolver can continue rejecting aliases.
    const temporaryRoot = await realpath(tmpdir());
    const temp = await mkdtemp(join(temporaryRoot, 'iris-local-web-'));
    try { await action(temp); }
    finally {
        assert.ok(resolve(temp).startsWith(resolve(temporaryRoot) + '\\')
            || resolve(temp).startsWith(resolve(temporaryRoot) + '/'));
        await rm(temp, { recursive: true, force: true });
    }
}

test('QR imports concurrently once and returns the same pinned factory', async () => {
    let calls = 0;
    const factory = () => {};
    const load = createQRCodeLoader(async () => { calls++; return { default: factory }; });
    const a = load();
    const b = load();
    assert.equal(a, b);
    assert.deepEqual(await Promise.all([a, b]), [factory, factory]);
    assert.equal(await load(), factory);
    assert.equal(calls, 1);
});

test('QR retry recovers after a failed or invalid module without reloading the page', async () => {
    let calls = 0;
    const urls = [];
    const factory = () => {};
    const load = createQRCodeLoader(async (url) => {
        urls.push(url);
        calls++;
        if (calls === 1) throw new Error('temporary local read failure');
        if (calls === 2) return {};
        return { default: factory };
    });
    await assert.rejects(load(), /temporary local read failure/);
    await assert.rejects(load(), /QR local no disponible/);
    assert.equal(await load(), factory);
    assert.equal(calls, 3);
    assert.deepEqual(urls, ['/face/vendor/qrcode/qrcode.mjs',
        '/face/vendor/qrcode/qrcode.mjs?retry=1', '/face/vendor/qrcode/qrcode.mjs?retry=2']);
});

test('vendored QR ESM generates an image entirely in memory', async () => {
    const { files } = await resources();
    const qrBytes = files.get('face/vendor/qrcode/qrcode.mjs');
    const module = await import('data:text/javascript;base64,' + qrBytes.toString('base64'));
    const qr = module.default(0, 'M');
    qr.addData('http://192.168.0.2:8765/face/');
    qr.make();
    assert.match(qr.createImgTag(4, 8), /^<img src="data:image\/gif;base64,/);
    assert.ok(qr.getModuleCount() > 20);
});

test('Wizard keeps classic scripts, inline handlers and explicit external links', () => {
    const original = '<head><link rel="preconnect" href="https://fonts.googleapis.com">'
        + '<link href="https://fonts.googleapis.com/css2?family=Syne" rel="stylesheet"></head>'
        + '<button onclick="nextStep()">Next</button><script src="/ui/wizard.js?v=55"></script>'
        + '<a href="https://example.com/docs">Docs</a>';
    const local = localWizardText(original, 'index.html');
    assert.ok(!local.includes('fonts.googleapis.com'));
    assert.ok(local.includes('/ui/vendor/fonts/fonts.css'));
    assert.ok(local.includes('onclick="nextStep()"'));
    assert.ok(local.includes('<script src="/ui/wizard.js?v=55"></script>'));
    assert.ok(local.includes('https://example.com/docs'));
    assert.equal(localWizardText("@import url('https://fonts.googleapis.com/css2?family=Syne');\nbody{color:red}", 'wizard.css'),
        '\nbody{color:red}');
});

test('resources preserve backend theme contracts and only approved public images and factory furniture', async () => {
    const { files } = await resources();
    assert.equal(files.get('face/js/os_theme.js').toString(), await readFile(join(root, 'face/js/os_theme.js'), 'utf8'));
    for (const source of ['camera_mocap.js', 'vision_motion.js', 'vision_capture_tuning.js']) {
        assert.ok(files.has('face/js/' + source));
    }
    assert.match(files.get('face/companion/companion.js').toString(), /new URL\('\/face\/vendor\/mediapipe\/', document.baseURI\)/);
    assert.deepEqual([...files.keys()].filter((name) => name.startsWith('face/public/')).sort(),
        [...PUBLIC_IMAGES, ...PUBLIC_ROOM_MODELS].map((name) => 'face/public/assets/' + name).sort());
    assert.ok(![...files.keys()].some((name) => /models|animations|\.map$|\.vrm$/.test(name)));
});

test('Face and standalone Wizard have separate local fonts with original unicode ranges', async () => {
    const { files, licenses } = await resources();
    for (const prefix of ['face', 'wizard/ui']) {
        for (const family of ['outfit', 'syne', 'jetbrains-mono']) {
            const css = files.get(`${prefix}/vendor/fonts/${family}.css`).toString();
            assert.match(css, /unicode-range:/);
            assert.ok(!/https?:\/\//.test(css));
            for (const match of css.matchAll(/url\(\.\/files\/([^)]*)\)/g)) {
                assert.ok(files.has(`${prefix}/vendor/fonts/files/${match[1]}`));
            }
        }
    }
    for (const license of licenses) for (const name of license.files) {
        assert.ok(files.has(name), 'missing license ' + name);
        assert.ok(files.get(name).length > 500);
    }
    const vite = licenses.find((item) => item.package === 'vite');
    assert.equal(vite.version, WEB_VERSIONS.vite);
    assert.equal(vite.license, 'MIT');
    assert.match(files.get(vite.files[0]).toString(), /Vite core license/);
    assert.equal(WEB_VERSIONS['qrcode-generator'], '1.4.4');
    assert.equal(WEB_VERSIONS['@fontsource/jetbrains-mono'], '5.2.8');
});

test('Face build preserves /face assets, disables public copying and keeps separate entries', () => {
    const config = faceBuildConfig(root, join(root, 'test-new-output'));
    assert.equal(config.base, '/face/');
    assert.equal(config.publicDir, false);
    assert.equal(config.build.emptyOutDir, false);
    assert.equal(config.build.sourcemap, false);
    assert.deepEqual(Object.keys(config.build.rollupOptions.input), ['face', 'companion']);
    const transform = config.plugins[0].transform;
    const source = "const url = new URL('../animations/iris_idle.vrma', import.meta.url).href;";
    assert.equal(transform(source, join(root, 'face/js/avatar.js').split('\\').join('/') + '?v=5'),
        "const url = '/face/animations/iris_idle.vrma';");
    assert.equal(transform("const base = new URL('./mediapipe/', document.baseURI);",
        join(root, 'face/companion/companion.js').split('\\').join('/')),
    "const base = new URL('/face/vendor/mediapipe/', document.baseURI);");
});

for (const name of ['../face/index.html', '/face/index.html', 'face/../secret.json',
    'face\\vendor\\qr.js', 'face/C:/qr.js', 'wizard/ui/CON.txt', 'face/avatar.vrm',
    'face/public/models/private.glb', 'face/chunks/file.js.map', 'face/secret.env']) {
    test(`web paths reject ${name}`, () => assert.throws(() => webPath(name), /Invalid web asset/));
}

test('new output reservation preserves an existing candidate', async () => {
    await withTemp(async (temp) => {
        const output = join(temp, 'candidate');
        await reserveOutput(output);
        await writeFile(join(output, 'marker'), 'previous');
        await assert.rejects(reserveOutput(output), /must be new/);
        assert.equal(await readFile(join(output, 'marker'), 'utf8'), 'previous');
    });
});

test('raw build arguments reject traversal, ADS, device names and aliases before writes', async () => {
    for (const name of ['../candidate', 'artifacts/../candidate', 'artifacts/./candidate',
        'artifacts//candidate', 'artifacts/candidate:ads', 'artifacts/CON']) {
        assert.throws(() => localBuildPath(name, 'output'), /Unsafe local build/);
    }
    await withTemp(async (temp) => {
        const source = join(temp, 'Source');
        await mkdir(source);
        await writeFile(join(source, 'qr.js'), 'source');
        await assert.rejects(reserveOutput(join(temp, 'source/new')), /Aliased web asset/);
        assert.deepEqual(await import('node:fs/promises').then((fs) => fs.readdir(source)), ['qr.js']);
    });
});

test('input files and fresh output reject junctions and hardlinks', async () => {
    await withTemp(async (temp) => {
        const source = join(temp, 'source');
        await mkdir(source);
        await writeFile(join(source, 'qr.js'), 'source');
        const linked = join(temp, 'linked');
        await symlink(source, linked, process.platform === 'win32' ? 'junction' : 'dir');
        try {
            await assert.rejects(readRegular(join(linked, 'qr.js')), /Linked web asset/);
            await assert.rejects(reserveOutput(join(linked, 'new')), /Linked web asset/);
        } finally { await rm(linked, { force: true }); }
        await link(join(source, 'qr.js'), join(temp, 'hardlinked.js'));
        await assert.rejects(readRegular(join(temp, 'hardlinked.js')), /Non-regular web asset/);
    });
});

test('contract publication refuses hardlinks without changing the original bytes', async () => {
    await withTemp(async (temp) => {
        const original = join(temp, 'original.json');
        const alias = join(temp, 'release.json');
        await writeFile(original, 'previous');
        await link(original, alias);
        const output = join(temp, 'never-created');
        await assert.rejects(buildWeb({ projectRoot: root, output, contract: alias }), /regular unlinked/);
        await assert.rejects(readFile(join(output, 'face/index.html')), { code: 'ENOENT' });
        await assert.rejects(publishWebContract(alias, Buffer.from('candidate')), /regular unlinked/);
        assert.equal(await readFile(original, 'utf8'), 'previous');
        assert.equal(await readFile(alias, 'utf8'), 'previous');
        const fresh = join(temp, 'fresh.json');
        await publishWebContract(fresh, Buffer.from('first'));
        await publishWebContract(fresh, Buffer.from('second'));
        assert.equal(await readFile(fresh, 'utf8'), 'second');
        assert.deepEqual(await import('node:fs/promises').then((fs) => fs.readdir(temp)),
            ['fresh.json', 'original.json', 'release.json']);
    });
});

test('hashed notices reject CRLF conversion before they can enter an output', async () => {
    await withTemp(async (temp) => {
        const name = 'qrcode-generator-MIT.txt';
        const bytes = await readWebNotice(root, name);
        const folder = join(temp, 'scripts/third-party');
        await mkdir(folder, { recursive: true });
        await writeFile(join(folder, name), bytes);
        assert.deepEqual(await readWebNotice(temp, name), bytes);
        await writeFile(join(folder, name), bytes.toString('utf8').replace(/\n/g, '\r\n'));
        await assert.rejects(readWebNotice(temp, name), /Incorrect web license hash/);
        await assert.rejects(readWebNotice(temp, '../unknown.txt'), /Unknown web notice/);
    });
});

test('checkout attributes preserve hashed notices and binary Vision bytes', async () => {
    const attributes = await readFile(join(root, '.gitattributes'), 'utf8');
    assert.match(attributes, /\/companion\/third-party\/\*\.txt text eol=lf/);
    assert.match(attributes, /\/scripts\/third-party\/\*\.txt text eol=lf/);
    assert.match(attributes, /^\*\.task binary$/m);
    assert.match(attributes, /^\*\.wasm binary$/m);
    for (const name of ['qrcode-generator-MIT.txt', 'fflate-MIT.txt']) {
        assert.ok(!(await readFile(join(root, 'scripts/third-party', name), 'utf8')).includes('\r'));
    }
});

test('inventory hashes exact bytes and excludes only A04 assets and its own manifest', async () => {
    await withTemp(async (temp) => {
        for (const name of [...Object.values(WEB_ENTRYPOINTS), 'face/animations/private.vrma', 'face/web-assets.json']) {
            await mkdir(dirname(join(temp, name)), { recursive: true });
            await writeFile(join(temp, name), '<head></head>');
        }
        const files = await inventoryWebFiles(temp);
        assert.deepEqual(files.map((item) => item.path), Object.values(WEB_ENTRYPOINTS).sort());
        assert.ok(files.every((item) => item.sha256 === sha256(Buffer.from('<head></head>')) && item.size === 13));
        await assertLocalBootstrap(temp, files);
        await writeFile(join(temp, 'face/private.vrm'), 'private');
        await assert.rejects(inventoryWebFiles(temp), /Invalid web asset/);
    });
});

test('bootstrap verifier rejects remote scripts and fonts in the built output', async () => {
    await withTemp(async (temp) => {
        for (const name of Object.values(WEB_ENTRYPOINTS)) {
            await mkdir(dirname(join(temp, name)), { recursive: true });
            await writeFile(join(temp, name), '<head></head>');
        }
        await writeFile(join(temp, WEB_ENTRYPOINTS.face), '<script src="https://cdn.example/app.js"></script>');
        await assert.rejects(assertLocalBootstrap(temp, await inventoryWebFiles(temp)), /Network bootstrap/);
        await writeFile(join(temp, WEB_ENTRYPOINTS.face), '<head></head>');
        await writeFile(join(temp, 'wizard/ui/remote.css'), "@import url('https://fonts.example/style');");
        await assert.rejects(assertLocalBootstrap(temp, await inventoryWebFiles(temp)), /Network font or CSS resource/);
    });
});
