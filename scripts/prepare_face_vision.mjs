/** Reuse the pinned Companion runtime in a fresh local Face staging directory. */
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { prepareVisionAssets } from './vision_assets.mjs';

export { prepareVisionAssets } from './vision_assets.mjs';

export async function main(args = process.argv.slice(2)) {
    const projectRoot = fileURLToPath(new URL('..', import.meta.url));
    if (args.length > 2 || (args.length && (args[0] !== '--target' || !args[1]))) {
        throw new Error('Usage: node scripts/prepare_face_vision.mjs [--target NEW_DIRECTORY]');
    }
    if (args[1] && /(^|[\\/])\.\.?([\\/]|$)/.test(args[1])) throw new Error('Target traversal rejected');
    const target = args[1]
        ? (path.isAbsolute(args[1]) ? args[1] : path.join(projectRoot, args[1]))
        : path.join(projectRoot, 'face/vendor/mediapipe');
    const manifest = await prepareVisionAssets({ projectRoot, target });
    console.log(`Prepared verified local Vision ${manifest.mediapipe_version}: ${target}`);
    return manifest;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
    main().catch(error => { console.error(error.message); process.exitCode = 1; });
}
