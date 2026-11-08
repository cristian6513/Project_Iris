import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

import { defineConfig } from 'vite';

const COMPANION_ROOT = dirname(fileURLToPath(import.meta.url));
const PROJECT_ROOT = resolve(COMPANION_ROOT, '..');

function stripNetworkBootstrap() {
    return {
        name: 'iris-native-offline-bootstrap',
        enforce: 'pre',
        transformIndexHtml: {
            order: 'pre',
            handler(html) {
                return html
                    .replace(/\s*<link[^>]+(?:fonts\.googleapis|fonts\.gstatic)[^>]*>/gi, '')
                    .replace(/\s*<script\s+type=["']importmap["'][^>]*>[\s\S]*?<\/script>/gi, '');
            },
        },
    };
}

export default defineConfig({
    root: PROJECT_ROOT,
    base: './',
    plugins: [stripNetworkBootstrap()],
    resolve: {
        // Source modules remain shared under face/. Their nearest parent has
        // no node_modules directory, so point bare imports at this shell's
        // pinned dependency tree instead of relying on a repository-global
        // installation.
        alias: [
            {
                find: '@mediapipe/tasks-vision',
                replacement: resolve(COMPANION_ROOT, 'node_modules', '@mediapipe', 'tasks-vision', 'vision_bundle.mjs'),
            },
            {
                find: '@pixiv/three-vrm-animation',
                replacement: resolve(COMPANION_ROOT, 'node_modules', '@pixiv', 'three-vrm-animation'),
            },
            {
                find: '@pixiv/three-vrm-springbone',
                replacement: resolve(COMPANION_ROOT, 'node_modules', '@pixiv', 'three-vrm-springbone'),
            },
            {
                find: '@pixiv/three-vrm',
                replacement: resolve(COMPANION_ROOT, 'node_modules', '@pixiv', 'three-vrm'),
            },
            {
                find: '@tauri-apps/api',
                replacement: resolve(COMPANION_ROOT, 'node_modules', '@tauri-apps', 'api'),
            },
            {
                find: 'three/addons',
                replacement: resolve(COMPANION_ROOT, 'node_modules', 'three', 'examples', 'jsm'),
            },
            {
                find: 'three',
                replacement: resolve(COMPANION_ROOT, 'node_modules', 'three', 'build', 'three.module.js'),
            },
        ],
    },
    clearScreen: false,
    server: {
        host: '127.0.0.1',
        port: 1420,
        strictPort: true,
    },
    build: {
        outDir: resolve(COMPANION_ROOT, 'dist'),
        emptyOutDir: true,
        target: ['es2022', 'chrome105', 'safari13'],
        sourcemap: false,
        rollupOptions: {
            input: resolve(PROJECT_ROOT, 'face', 'companion', 'index.html'),
        },
    },
});
