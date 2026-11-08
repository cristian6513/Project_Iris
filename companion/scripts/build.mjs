import { copyAnimationAssets } from './animation_assets.mjs';
import { prepareVisionAssets } from '../../scripts/vision_assets.mjs';
import { BASIS_FILES, prepareBasisTranscoder } from '../../scripts/basis_assets.mjs';
import { createHash } from 'node:crypto';
import { copyFile, mkdir, readFile, readdir } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const COMPANION_ROOT = resolve(fileURLToPath(new URL('..', import.meta.url)));
const PROJECT_ROOT = resolve(COMPANION_ROOT, '..');
const SOURCE = resolve(PROJECT_ROOT, 'face', 'animations');
const TARGET = resolve(COMPANION_ROOT, 'dist', 'face', 'animations');
const MEDIAPIPE_TARGET = resolve(COMPANION_ROOT, 'dist', 'face', 'companion', 'mediapipe');
const MEDIAPIPE_MODEL_SHA256 = '64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff';
// KTX2 textures: face/js/ktx2_support.js reads the transcoder from /face/vendor/basis/.
const BASIS_TARGET = resolve(COMPANION_ROOT, 'dist', 'face', 'vendor', 'basis');
// The book and the tablet her routines use: the room's own models.
const PROP_SOURCE = resolve(PROJECT_ROOT, 'face', 'public', 'assets', 'room');
const PROP_TARGET = resolve(COMPANION_ROOT, 'dist', 'face', 'public', 'assets', 'room');
const PROP_MODELS = ['book-v1.glb', 'tablet-v2.glb'];

// Wonderful VRMA Set forbids redistribution of its unmodified files. They can
// remain in a user's local face/animations directory, but must never enter a
// distributable native bundle.
export const NON_REDISTRIBUTABLE = new Set([
    'idle_smile.vrma',
    'idle_love.vrma',
    'idle_noble.vrma',
    'idle_weave.vrma',
    'think.vrma',
    'shy.vrma',
]);
async function copyLocalVisionRuntime() {
    await prepareVisionAssets({ projectRoot: PROJECT_ROOT, target: MEDIAPIPE_TARGET });
}

async function assertOfflineBundle() {
    const page = resolve(COMPANION_ROOT, 'dist', 'face', 'companion', 'index.html');
    const html = await readFile(page, 'utf8');
    if (/cdn\.jsdelivr\.net|fonts\.googleapis\.com|fonts\.gstatic\.com/i.test(html)) {
        throw new Error('Native companion still references a network bootstrap resource.');
    }
    const copied = new Set(await readdir(TARGET));
    for (const filename of NON_REDISTRIBUTABLE) {
        if (copied.has(filename)) throw new Error(`Prohibited animation copied: ${filename}`);
    }
    const visionFiles = await readdir(join(MEDIAPIPE_TARGET, 'wasm'));
    if (!visionFiles.includes('vision_wasm_internal.wasm')) {
        throw new Error('Native companion is missing the local MediaPipe WASM runtime.');
    }
    const model = await readFile(join(MEDIAPIPE_TARGET, 'face_landmarker.task'));
    if (model.byteLength < 1_000_000) {
        throw new Error('Native companion Face Landmarker model is missing or truncated.');
    }
    const digest = createHash('sha256').update(model).digest('hex');
    if (digest !== MEDIAPIPE_MODEL_SHA256) {
        throw new Error(`Unexpected Face Landmarker model digest: ${digest}`);
    }
    for (const file of BASIS_FILES) {
        const bytes = await readFile(join(BASIS_TARGET, file.path));
        if (createHash('sha256').update(bytes).digest('hex') !== file.sha256) {
            throw new Error(`Native companion has an unexpected Basis transcoder file: ${file.path}`);
        }
    }
}

await copyAnimationAssets(SOURCE, TARGET);
await mkdir(PROP_TARGET, { recursive: true });
for (const model of PROP_MODELS) await copyFile(join(PROP_SOURCE, model), join(PROP_TARGET, model));
await copyLocalVisionRuntime();
await prepareBasisTranscoder({ projectRoot: PROJECT_ROOT, target: BASIS_TARGET });
await assertOfflineBundle();
console.log('IRIS native bundle prepared: offline vision/runtime + license-safe animations.');
