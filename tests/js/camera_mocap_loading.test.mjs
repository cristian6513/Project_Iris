import test from 'node:test';
import assert from 'node:assert/strict';

let moduleNumber = 0;
async function freshLoader() {
    return (await import(`../../face/js/camera_mocap.js?loading-test=${++moduleNumber}`)).loadMediaPipe;
}

function visionMock({ create = async () => ({ detectForVideo() {} }), fileset = {} } = {}) {
    const paths = [], options = [];
    return {
        paths, options,
        module: {
            FilesetResolver: { forVisionTasks: async base => { paths.push(base); return fileset; } },
            FaceLandmarker: { createFromOptions: async (resolved, opts) => { options.push(opts); return create(resolved, opts); } },
        },
    };
}

test('a transient MediaPipe load failure can be retried without a page reload', async () => {
    const loadMediaPipe = await freshLoader();
    await assert.rejects(
        loadMediaPipe({ moduleLoader: async () => { throw new Error('offline'); } }),
        /offline/,
    );

    const landmarker = { detectForVideo() {} };
    const loaded = await loadMediaPipe({
        moduleLoader: async () => ({
            FilesetResolver: {
                forVisionTasks: async () => ({ wasm: 'ready' }),
            },
            FaceLandmarker: {
                createFromOptions: async () => landmarker,
            },
        }),
        wasmBase: 'local-wasm',
        modelUrl: 'local-model',
    });
    assert.equal(loaded, landmarker);
});

test('default module, WASM and model paths remain local for injected loaders', async () => {
    const loadMediaPipe = await freshLoader();
    const mock = visionMock();
    let bundle;
    await loadMediaPipe({ moduleLoader: async url => { bundle = url; return mock.module; } });
    assert.equal(bundle, '/face/vendor/mediapipe/vision_bundle.mjs');
    assert.deepEqual(mock.paths, ['/face/vendor/mediapipe/wasm']);
    assert.equal(mock.options[0].baseOptions.modelAssetPath, '/face/vendor/mediapipe/face_landmarker.task');
});

test('a missing default local bundle fails locally and remains retryable', async () => {
    const loadMediaPipe = await freshLoader();
    // Node cannot resolve the browser's local absolute module URL. A remote
    // fallback would instead fail with ERR_UNSUPPORTED_ESM_URL_SCHEME.
    await assert.rejects(loadMediaPipe(), error => error.code === 'ERR_MODULE_NOT_FOUND');
    const mock = visionMock();
    assert.ok(await loadMediaPipe({ moduleLoader: async () => mock.module }));
});

test('Companion module and resource overrides survive GPU fallback to CPU', async () => {
    const loadMediaPipe = await freshLoader();
    const landmarker = { detectForVideo() {} }, fileset = { wasm: 'local-companion' };
    const mock = visionMock({ fileset, create: async (resolved, options) => {
        assert.equal(resolved, fileset);
        if (options.baseOptions.delegate === 'GPU') throw new Error('GPU unavailable');
        return landmarker;
    } });
    assert.equal(await loadMediaPipe({ moduleLoader: async () => mock.module,
        wasmBase: 'http://tauri.localhost/face/companion/mediapipe/wasm/',
        modelUrl: 'http://tauri.localhost/face/companion/mediapipe/face_landmarker.task',
    }), landmarker);
    assert.deepEqual(mock.paths, ['http://tauri.localhost/face/companion/mediapipe/wasm/']);
    assert.deepEqual(mock.options.map(value => value.baseOptions.delegate), ['GPU', 'CPU']);
    assert.ok(mock.options.every(value => value.baseOptions.modelAssetPath ===
        'http://tauri.localhost/face/companion/mediapipe/face_landmarker.task'));
});

test('both delegate failures and an invalid local bundle can be retried', async () => {
    const loadMediaPipe = await freshLoader();
    await assert.rejects(loadMediaPipe({ moduleLoader: async () => ({}) }), /missing FaceLandmarker/);
    const broken = visionMock({ create: async () => { throw new Error('delegate unavailable'); } });
    await assert.rejects(loadMediaPipe({ moduleLoader: async () => broken.module }), /both GPU and CPU/);
    assert.deepEqual(broken.options.map(value => value.baseOptions.delegate), ['GPU', 'CPU']);
    const ready = visionMock();
    assert.ok(await loadMediaPipe({ moduleLoader: async () => ready.module }));
});

test('concurrent opening and reopening share one module load and one landmarker', async () => {
    const loadMediaPipe = await freshLoader();
    let resolveModule, loads = 0;
    const deferred = new Promise(resolve => { resolveModule = resolve; });
    const mock = visionMock();
    const options = { moduleLoader: async () => { loads++; return deferred; } };
    const first = loadMediaPipe(options), second = loadMediaPipe(options);
    resolveModule(mock.module);
    const [a, b] = await Promise.all([first, second]);
    assert.equal(a, b);
    assert.equal(await loadMediaPipe(options), a);
    assert.equal(loads, 1);
    assert.equal(mock.options.length, 1);
});

test('WASM resolver failure is retryable using the same local resources', async () => {
    const loadMediaPipe = await freshLoader();
    await assert.rejects(loadMediaPipe({ moduleLoader: async () => ({
        FaceLandmarker: {}, FilesetResolver: { forVisionTasks: async () => { throw new Error('local wasm missing'); } },
    }) }), /local wasm missing/);
    const mock = visionMock();
    assert.ok(await loadMediaPipe({ moduleLoader: async () => mock.module }));
});
