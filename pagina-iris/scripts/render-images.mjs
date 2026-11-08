#!/usr/bin/env node
/*
 * Regenera las imágenes de marca de la página a partir de sus plantillas:
 *   brand/og-image.html      → assets/og-image.jpg      (1200×630, para redes)
 *   brand/iris-icono.png     → favicon.ico, assets/icon-192.png, assets/icon-512.png (círculo,
 *                              transparente fuera del anillo), apple-touch-icon.png y
 *                              assets/icon-maskable-512.png (círculo sobre fondo oscuro)
 *
 * Requiere Chrome o Edge (modo headless) y ffmpeg. Solo hace falta ejecutarlo
 * cuando cambien las plantillas; las imágenes generadas se versionan.
 *
 * Con --redes genera en cambio las imágenes de los perfiles sociales (redes/, no se publica):
 *   brand/social-perfil-logo.html → redes/perfil-logo.png        (1080×1080, todas las redes)
 *   brand/social-perfil-iris.html → redes/perfil-iris.png        (800×800, alternativa)
 *   brand/social-youtube.html     → redes/youtube-banner.jpg     (2560×1440, zona segura 1546×423)
 *     (los dos banners usan de fondo brand/iris-arte.webp, la ilustración sin texto)
 *   brand/social-x.html           → redes/x-cabecera.jpg         (1500×500)
 *
 *   node pagina-iris/scripts/render-images.mjs
 *   node pagina-iris/scripts/render-images.mjs --redes
 *   CHROME_PATH="C:/ruta/chrome.exe" node pagina-iris/scripts/render-images.mjs
 */
import { spawn } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, rmSync } from 'node:fs';
import { createServer } from 'node:http';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

const SITE_DIR = fileURLToPath(new URL('..', import.meta.url));

const MIME = {
    '.html': 'text/html; charset=utf-8',
    '.svg': 'image/svg+xml',
    '.webp': 'image/webp',
    '.woff2': 'font/woff2',
    '.css': 'text/css; charset=utf-8',
};

function findChrome() {
    const candidates = [
        process.env.CHROME_PATH,
        'C:/Program Files/Google/Chrome/Application/chrome.exe',
        'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
        '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
        '/usr/bin/google-chrome',
        '/usr/bin/chromium',
    ].filter(Boolean);
    const found = candidates.find((path) => existsSync(path));
    if (!found) throw new Error('No encuentro Chrome ni Edge. Indica la ruta con CHROME_PATH.');
    return found;
}

// Asíncrono a propósito: Chrome pide los archivos a nuestro propio servidor,
// así que el proceso de Node no puede quedarse bloqueado esperándolo.
function run(command, args, timeoutMs = 60000) {
    return new Promise((resolve, reject) => {
        const child = spawn(command, args, { stdio: ['ignore', 'ignore', 'pipe'] });
        let stderr = '';
        child.stderr.on('data', (chunk) => { stderr += chunk; });
        const timer = setTimeout(() => {
            child.kill();
            reject(new Error(`${command} tardó más de ${timeoutMs / 1000} s`));
        }, timeoutMs);
        child.on('error', (error) => {
            clearTimeout(timer);
            reject(error);
        });
        child.on('close', (code) => {
            clearTimeout(timer);
            if (code === 0) resolve();
            else reject(new Error(`${command} terminó con código ${code}\n${stderr}`));
        });
    });
}

// Servidor estático mínimo: Chrome no carga fuentes desde file:// por CORS.
function serve() {
    const server = createServer(async (request, response) => {
        const path = decodeURIComponent(new URL(request.url, 'http://localhost').pathname);
        const file = join(SITE_DIR, path);
        if (!file.startsWith(SITE_DIR)) {
            response.writeHead(403).end();
            return;
        }
        try {
            const body = await readFile(file);
            const ext = path.slice(path.lastIndexOf('.'));
            response.writeHead(200, { 'Content-Type': MIME[ext] || 'application/octet-stream' }).end(body);
        } catch {
            response.writeHead(404).end();
        }
    });
    return new Promise((resolve) => server.listen(0, '127.0.0.1', () => resolve(server)));
}

function screenshot(chrome, profileDir, url, width, height, output) {
    return run(chrome, [
        '--headless=new',
        '--disable-gpu',
        '--hide-scrollbars',
        '--no-first-run',
        '--no-default-browser-check',
        `--user-data-dir=${profileDir}`,
        '--force-device-scale-factor=1',
        '--default-background-color=00000000',
        '--virtual-time-budget=5000',
        `--window-size=${width},${height}`,
        `--screenshot=${output}`,
        url,
    ]);
}

function ffmpeg(args) {
    return run('ffmpeg', ['-loglevel', 'error', '-y', ...args]);
}

const chrome = findChrome();
const work = mkdtempSync(join(tmpdir(), 'iris-images-'));
const profileDir = join(work, 'profile');
const server = await serve();
const base = `http://127.0.0.1:${server.address().port}`;

const SOCIAL = [
    { template: 'social-perfil-logo.html', width: 1080, height: 1080, output: 'perfil-logo.png', args: [] },
    { template: 'social-perfil-iris.html', width: 800, height: 800, output: 'perfil-iris.png', args: [] },
    { template: 'social-youtube.html', width: 2560, height: 1440, output: 'youtube-banner.jpg', args: ['-q:v', '2'] },
    { template: 'social-x.html', width: 1500, height: 500, output: 'x-cabecera.jpg', args: ['-q:v', '2'] },
];

async function renderSocial() {
    mkdirSync(join(SITE_DIR, 'redes'), { recursive: true });
    for (const image of SOCIAL) {
        const shot = join(work, `${image.output}.png`);
        await screenshot(chrome, profileDir, `${base}/brand/${image.template}`, image.width, image.height, shot);
        await ffmpeg(['-i', shot, ...image.args, join(SITE_DIR, 'redes', image.output)]);
    }
    console.log(`Imágenes de redes generadas en redes/: ${SOCIAL.map((image) => image.output).join(', ')}.`);
}

// El retrato de IRIS (1254×1254): el anillo está centrado en (626.5, 627) y brilla hasta
// el radio 613; más allá solo queda un resplandor tenue que en pestañas claras parece un
// borde oscuro. Fuera de 615.5 px el fondo pasa a transparente, con un borde suavizado de 1 px.
const PORTRAIT = join(SITE_DIR, 'brand', 'iris-icono.png');
const CIRCLE = "format=rgba,geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='255*clip(615.5-hypot(X-626.5,Y-627),0,1)'";
const BACKGROUND = '0x020711';   // theme_color de site.webmanifest

// Círculo centrado sobre fondo opaco: iOS no admite transparencia y Android
// recorta los iconos maskable a la zona segura (un círculo del 80 %).
function onBackground(circle, size, inner, output) {
    return ffmpeg(['-i', circle, '-filter_complex',
        `color=c=${BACKGROUND}:s=${size}x${size}[bg];[0:v]scale=${inner}:${inner}:flags=lanczos[fg];` +
        '[bg][fg]overlay=(W-w)/2:(H-h)/2:shortest=1,format=rgb24',
        '-frames:v', '1', output]);
}

async function renderSite() {
    const og = join(work, 'og.png');
    const circle = join(work, 'circle.png');

    await screenshot(chrome, profileDir, `${base}/brand/og-image.html`, 1200, 630, og);
    await ffmpeg(['-i', PORTRAIT, '-vf', CIRCLE, circle]);

    const out = (path) => join(SITE_DIR, path);
    await ffmpeg(['-i', og, '-q:v', '3', out('assets/og-image.jpg')]);
    await ffmpeg(['-i', circle, '-vf', 'scale=512:512:flags=lanczos', out('assets/icon-512.png')]);
    await ffmpeg(['-i', circle, '-vf', 'scale=192:192:flags=lanczos', out('assets/icon-192.png')]);
    await ffmpeg(['-i', circle, '-vf', 'scale=32:32:flags=lanczos', out('favicon.ico')]);
    await onBackground(circle, 512, 410, out('assets/icon-maskable-512.png'));
    await onBackground(circle, 180, 164, out('apple-touch-icon.png'));

    console.log('Imágenes regeneradas: og-image.jpg, favicon.ico, apple-touch-icon.png e iconos de la app.');
}

try {
    await (process.argv.includes('--redes') ? renderSocial() : renderSite());
} finally {
    server.close();
    rmSync(work, { recursive: true, force: true });
}
