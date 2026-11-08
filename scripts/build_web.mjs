/** Build a new, inventoried offline web candidate; never replace a preview. */
import { writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { copyAnimationAssets } from '../companion/scripts/animation_assets.mjs';
import {
    WEB_ENTRYPOINTS, WEB_VERSIONS, assertLocalBootstrap, inventoryWebFiles,
    localBuildPath, prepareWebResources, publishWebContract, readRegular, reserveOutput,
    stripNetworkFonts, validateContractOutput, writeWebResources,
} from './web_assets.mjs';
import { prepareVisionAssets } from './vision_assets.mjs';
import { BASIS_LICENSE, prepareBasisTranscoder } from './basis_assets.mjs';

const PROJECT_ROOT = resolve(fileURLToPath(new URL('..', import.meta.url)));

export function faceBuildConfig(projectRoot, output) {
    const modules = join(projectRoot, 'companion/node_modules');
    return {
        configFile: false,
        root: join(projectRoot, 'face'),
        base: '/face/',
        publicDir: false,
        plugins: [{
            name: 'iris-local-web-bootstrap',
            transformIndexHtml: { order: 'pre', handler: (html) => stripNetworkFonts(html)
                .replace(/\s*<script\s+type=["']importmap["'][^>]*>[\s\S]*?<\/script>/gi, '') },
            transform(code, id) {
                if (id.split('?')[0] === join(projectRoot, 'face/js/avatar.js').split('\\').join('/')) {
                    // The fallback keeps the A04 served path instead of creating
                    // a second, uncontracted binary in Vite's chunk directory.
                    return code.replace("new URL('../animations/iris_idle.vrma', import.meta.url).href",
                        "'/face/animations/iris_idle.vrma'");
                }
                if (id.split('?')[0] === join(projectRoot, 'face/companion/companion.js').split('\\').join('/')) {
                    return code.replace("new URL('./mediapipe/', document.baseURI)",
                        "new URL('/face/vendor/mediapipe/', document.baseURI)");
                }
            },
        }, {
            name: 'iris-local-web-fonts',
            transformIndexHtml: { order: 'post', handler: (html) => html.replace('</head>',
                '    <link rel="stylesheet" href="/face/vendor/fonts/fonts.css">\n</head>') },
        }],
        resolve: { alias: [
            { find: '@mediapipe/tasks-vision', replacement: join(modules, '@mediapipe/tasks-vision/vision_bundle.mjs') },
            ...['@pixiv/three-vrm-animation', '@pixiv/three-vrm-springbone', '@pixiv/three-vrm', '@tauri-apps/api']
                .map((find) => ({ find, replacement: join(modules, find) })),
            { find: 'three/addons', replacement: join(modules, 'three/examples/jsm') },
            { find: 'three', replacement: join(modules, 'three/build/three.module.js') },
        ] },
        build: {
            outDir: join(output, 'face'), emptyOutDir: false,
            target: ['es2022', 'chrome105', 'safari13'], sourcemap: false,
            rollupOptions: { input: {
                face: join(projectRoot, 'face/index.html'),
                companion: join(projectRoot, 'face/companion/index.html'),
            } },
        },
    };
}

export async function buildWeb({ projectRoot = PROJECT_ROOT, output, contract, verifyContract } = {}) {
    if (!output) throw new Error('An explicit new --output directory is required');
    const root = localBuildPath(projectRoot, 'projectRoot');
    const target = localBuildPath(output, 'output');
    const contractPath = contract ? localBuildPath(contract, 'contract') : null;
    const verifiedPath = verifyContract ? localBuildPath(verifyContract, 'verifyContract') : null;
    if (contractPath) await validateContractOutput(contractPath);
    if (verifiedPath) await readRegular(verifiedPath);
    const resources = await prepareWebResources(root);
    // Read and validate dependencies and allowlisted resources before reserving
    // output. No directory, model, avatar or profile is recursively copied.
    await reserveOutput(target);
    const { build } = await import(pathToFileURL(join(root, 'companion/node_modules/vite/dist/node/index.js')).href);
    await build(faceBuildConfig(root, target));
    await writeWebResources(target, resources.files);
    await copyAnimationAssets(join(root, 'face/animations'), join(target, 'face/animations'));
    await prepareVisionAssets({ projectRoot: root, target: join(target, 'face/vendor/mediapipe') });
    await prepareBasisTranscoder({ projectRoot: root, target: join(target, 'face/vendor/basis') });
    const files = await inventoryWebFiles(target);
    await assertLocalBootstrap(target, files);
    const manifest = {
        schema: 1, component: 'iris-local-web', versions: WEB_VERSIONS,
        entrypoints: WEB_ENTRYPOINTS,
        licenses: [...resources.licenses, { package: '@mediapipe/tasks-vision',
            version: WEB_VERSIONS['@mediapipe/tasks-vision'], license: 'Apache-2.0',
            files: ['face/vendor/mediapipe/MediaPipe-Apache-2.0.txt'] },
        { ...BASIS_LICENSE, files: ['face/vendor/basis/BasisUniversal-Apache-2.0.txt'] }],
        files,
    };
    const bytes = JSON.stringify(manifest, null, 2) + '\n';
    await writeFile(join(target, 'face/web-assets.json'), bytes, { flag: 'wx' });
    if (verifiedPath && !(await readRegular(verifiedPath)).equals(Buffer.from(bytes))) {
        throw new Error('Web candidate does not match the trusted release contract');
    }
    if (contractPath) {
        // Publishing this trust contract is explicit. Rebuilds without the flag
        // never silently change what the release verifier accepts.
        await publishWebContract(contractPath, bytes);
    }
    return { root: target, manifest };
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
    const args = process.argv.slice(2);
    const options = {};
    while (args.length) {
        const key = args.shift();
        if (!['--output', '--contract', '--verify-contract'].includes(key) || !args.length) throw new Error(`Invalid build argument: ${key}`);
        options[key === '--verify-contract' ? 'verifyContract' : key.slice(2)] = args.shift();
    }
    const result = await buildWeb(options);
    console.log(`IRIS local web candidate: ${result.root} (${result.manifest.files.length} verified files).`);
}
