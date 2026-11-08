#!/usr/bin/env node
/*
 * Vista previa local que se comporta como el nginx de producción.
 *
 *   node pagina-iris/scripts/preview.mjs             sirve build/site (haz antes el build)
 *   node pagina-iris/scripts/preview.mjs --source    sirve el código fuente sin caché (desarrollo)
 *   node pagina-iris/scripts/preview.mjs --port 4174
 *
 * Aplica las cabeceras de deploy/nginx/iris-os-security-headers.conf, la misma
 * caché por rutas, los .br/.gz precomprimidos y la página 404. Como corre en http,
 * omite HSTS y upgrade-insecure-requests (solo tienen sentido con https).
 */
import { existsSync, readFileSync, statSync } from 'node:fs';
import { createServer } from 'node:http';
import { extname, join, normalize } from 'node:path';
import { fileURLToPath } from 'node:url';

const SITE_DIR = fileURLToPath(new URL('..', import.meta.url));

const MIME = {
    '.html': 'text/html; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.js': 'text/javascript; charset=utf-8',
    '.json': 'application/json; charset=utf-8',
    '.webmanifest': 'application/manifest+json; charset=utf-8',
    '.xml': 'application/xml; charset=utf-8',
    '.txt': 'text/plain; charset=utf-8',
    '.svg': 'image/svg+xml',
    '.webp': 'image/webp',
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.ico': 'image/x-icon',
    '.woff2': 'font/woff2',
};

function parseArgs(argv) {
    const args = { port: 4174, source: false };
    for (let i = 0; i < argv.length; i += 1) {
        if (argv[i] === '--port') args.port = Number(argv[++i]);
        else if (argv[i] === '--source') args.source = true;
        else throw new Error(`Opción desconocida: ${argv[i]}`);
    }
    return args;
}

// Lee las líneas add_header "Nombre" "valor" del snippet de nginx.
function securityHeaders() {
    const snippet = readFileSync(join(SITE_DIR, 'deploy', 'nginx', 'iris-os-security-headers.conf'), 'utf8');
    const headers = {};
    for (const match of snippet.matchAll(/^add_header\s+(\S+)\s+"([^"]*)"\s+always;/gm)) {
        headers[match[1]] = match[2];
    }
    delete headers['Strict-Transport-Security'];
    if (headers['Content-Security-Policy']) {
        headers['Content-Security-Policy'] = headers['Content-Security-Policy']
            .replace(/\s*upgrade-insecure-requests;?/, '');
    }
    return headers;
}

function cacheControl(path, source) {
    if (source) return 'no-store';
    if (/^\/(assets|css|js|fonts)\//.test(path)) return 'public, max-age=31536000, immutable';
    if (/^\/(favicon\.ico|favicon\.svg|apple-touch-icon\.png|site\.webmanifest|robots\.txt|sitemap\.xml)$/.test(path)) {
        return 'public, max-age=86400';
    }
    return 'no-cache';
}

const args = parseArgs(process.argv.slice(2));
const root = args.source ? SITE_DIR : join(SITE_DIR, 'build', 'site');
if (!existsSync(join(root, 'index.html'))) {
    console.error('No hay build. Ejecuta antes: node pagina-iris/scripts/build.mjs');
    process.exit(1);
}
const headers = securityHeaders();

function resolveFile(urlPath) {
    const clean = normalize(decodeURIComponent(urlPath)).replace(/^([/\\])+/, '');
    const file = join(root, clean);
    if (!file.startsWith(root)) return null;
    if (existsSync(file) && statSync(file).isDirectory()) return join(file, 'index.html');
    return file;
}

const server = createServer((request, response) => {
    const url = new URL(request.url, 'http://localhost');
    const send = (status, file, extraHeaders = {}) => {
        const ext = extname(file);
        let body = readFileSync(file);
        const out = {
            ...headers,
            'Content-Type': MIME[ext] || 'application/octet-stream',
            'Cache-Control': cacheControl(url.pathname, args.source),
            ...extraHeaders,
        };
        // Igual que gzip_static/brotli_static: si existe la versión comprimida, se usa.
        const accepts = String(request.headers['accept-encoding'] || '');
        if (!args.source && /\bbr\b/.test(accepts) && existsSync(`${file}.br`)) {
            body = readFileSync(`${file}.br`);
            out['Content-Encoding'] = 'br';
            out.Vary = 'Accept-Encoding';
        } else if (!args.source && /\bgzip\b/.test(accepts) && existsSync(`${file}.gz`)) {
            body = readFileSync(`${file}.gz`);
            out['Content-Encoding'] = 'gzip';
            out.Vary = 'Accept-Encoding';
        }
        response.writeHead(status, out);
        response.end(request.method === 'HEAD' ? undefined : body);
    };

    if (request.method !== 'GET' && request.method !== 'HEAD') {
        response.writeHead(405, { ...headers, Allow: 'GET, HEAD' }).end();
        return;
    }
    const segments = url.pathname.split('/');
    const hidden = segments.some((part) => part.startsWith('.') && part !== '.well-known');
    const file = hidden ? null : resolveFile(url.pathname);
    if (file && existsSync(file) && statSync(file).isFile() && !/\.(gz|br)$/.test(file)) {
        send(200, file);
    } else {
        send(404, join(root, '404.html'), { 'Cache-Control': 'no-cache' });
    }
});

server.listen(args.port, '127.0.0.1', () => {
    const mode = args.source ? 'código fuente, sin caché' : 'build de producción';
    console.log(`Vista previa (${mode}) en http://127.0.0.1:${args.port}/`);
    console.log(`Cabeceras: ${Object.keys(headers).join(', ')}`);
});

process.on('SIGINT', () => server.close(() => process.exit(0)));
process.on('SIGTERM', () => server.close(() => process.exit(0)));
