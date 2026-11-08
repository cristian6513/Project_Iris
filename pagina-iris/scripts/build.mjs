#!/usr/bin/env node
/*
 * Build de producción de la página de IRIS OS. Solo usa módulos de Node.
 *
 *   node pagina-iris/scripts/build.mjs                    usa siteUrl de site.config.json
 *   node pagina-iris/scripts/build.mjs --site-url URL     sobrescribe la URL (pruebas)
 *   node pagina-iris/scripts/build.mjs --out DIR          carpeta de salida (por defecto build/)
 *
 * Resultado:
 *   build/site/     lo que se publica en el servidor
 *   build/nginx/    configuración de nginx generada para el dominio
 *   build/build-info.json
 *
 * Qué hace:
 *   1. Copia solo los archivos publicables.
 *   2. Pone un hash de contenido en el nombre de assets, fuentes, CSS y JS
 *      (caché de un año sin riesgo) y reescribe todas las referencias.
 *   3. Convierte en absolutas las URLs de SEO (canonical, Open Graph, JSON-LD) y añade
 *      los perfiles de redes de site.config.json (enlaces, sameAs y twitter:site).
 *   4. Genera robots.txt, sitemap.xml y, si hay contacto, .well-known/security.txt.
 *   5. Comprueba que no falte ningún archivo y que nada rompa la CSP estricta.
 *   6. Precomprime los archivos de texto en .gz y .br.
 *   7. Genera la configuración de nginx desde deploy/nginx/.
 */
import { createHash } from 'node:crypto';
import {
    cpSync, existsSync, mkdirSync, readdirSync, readFileSync, renameSync, rmSync, statSync, writeFileSync,
} from 'node:fs';
import { basename, dirname, extname, join, relative, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { brotliCompressSync, constants as zlib, gzipSync } from 'node:zlib';

const SITE_DIR = fileURLToPath(new URL('..', import.meta.url));

// Lo único que se publica. Todo lo demás (scripts, deploy, brand, README) se queda fuera.
const PUBLISH = [
    'index.html', '404.html', 'favicon.ico', 'apple-touch-icon.png', 'site.webmanifest',
    'css', 'js', 'assets', 'fonts',
];
// Orden importante: primero lo que no referencia a nada, después quien lo referencia.
const FINGERPRINT_DIRS = ['assets', 'fonts', 'css', 'js'];
const NOT_FINGERPRINTED = /^LICENSE/i;
const TEXT_EXT = new Set(['.html', '.css', '.js', '.svg', '.webmanifest', '.xml', '.txt', '.json']);
const DANGEROUS_SINKS = /\b(innerHTML|outerHTML|insertAdjacentHTML|document\.write)\b|\beval\(|new Function\(/;

class BuildError extends Error {}

function parseArgs(argv) {
    const args = {};
    for (let i = 0; i < argv.length; i += 1) {
        const arg = argv[i];
        if (arg === '--site-url') args.siteUrl = argv[++i];
        else if (arg === '--out') args.out = argv[++i];
        else if (arg === '--quiet') args.quiet = true;
        else throw new BuildError(`Opción desconocida: ${arg}`);
    }
    return args;
}

function normalizeSiteUrl(value) {
    if (!value) {
        throw new BuildError(
            'Falta la URL del sitio. Ponla en pagina-iris/site.config.json → "siteUrl" '
            + '(por ejemplo "https://iris-os.cloud/") o pásala con --site-url.',
        );
    }
    let url;
    try {
        url = new URL(value);
    } catch {
        throw new BuildError(`siteUrl no es una URL válida: ${value}`);
    }
    if (url.protocol !== 'https:') throw new BuildError(`siteUrl debe usar https: ${value}`);
    if (url.search || url.hash) throw new BuildError(`siteUrl no puede llevar ? ni #: ${value}`);
    if (!url.pathname.endsWith('/')) url.pathname += '/';
    return url;
}

function walk(dir) {
    const files = [];
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
        const full = join(dir, entry.name);
        if (entry.isDirectory()) files.push(...walk(full));
        else files.push(full);
    }
    return files;
}

const toPosix = (path) => path.split(sep).join('/');
const read = (path) => readFileSync(path, 'utf8');
const write = (path, content) => {
    mkdirSync(dirname(path), { recursive: true });
    writeFileSync(path, content);
};

function contentHash(buffer) {
    return createHash('sha256').update(buffer).digest('hex').slice(0, 10);
}

function replaceAll(text, map) {
    // Las rutas más largas primero para no reemplazar un prefijo de otra.
    const keys = [...map.keys()].sort((a, b) => b.length - a.length);
    let out = text;
    for (const key of keys) out = out.split(key).join(map.get(key));
    return out;
}

function fingerprint(siteOut, renames) {
    for (const dir of FINGERPRINT_DIRS) {
        const root = join(siteOut, dir);
        if (!existsSync(root)) continue;
        for (const file of walk(root)) {
            const rel = toPosix(relative(siteOut, file));
            if (NOT_FINGERPRINTED.test(basename(file))) continue;
            if (TEXT_EXT.has(extname(file))) write(file, replaceAll(read(file), renames));
            const ext = extname(file);
            const hashed = `${rel.slice(0, -ext.length)}.${contentHash(readFileSync(file))}${ext}`;
            renameSync(file, join(siteOut, hashed));
            renames.set(rel, hashed);
        }
    }
}

function absolutize(html, siteUrl) {
    const abs = (value) => new URL(value, siteUrl).href;
    const start = html.indexOf('<!-- seo:start');
    const end = html.indexOf('<!-- seo:end -->');
    if (start < 0 || end < 0) throw new BuildError('index.html no tiene el bloque <!-- seo:start --> … <!-- seo:end -->.');
    let block = html.slice(start, end);
    block = block
        .replace(/(<link rel="canonical" href=")([^"]+)(")/, (_, a, v, b) => a + abs(v) + b)
        .replace(/(<meta property="og:(?:url|image)" content=")([^"]+)(")/g, (_, a, v, b) => a + abs(v) + b)
        .replace(/(<meta name="twitter:image" content=")([^"]+)(")/, (_, a, v, b) => a + abs(v) + b)
        .replace(/("(?:url|image|logo|@id)":\s*")([^"]+)(")/g, (_, a, v, b) => a + abs(v) + b);
    return html.slice(0, start) + block + html.slice(end);
}

// Perfiles de redes de site.config.json → "social". Vacío = todavía no existe y no se muestra.
// Solo se aceptan URLs de perfil de cada red, sin ? ni #, para no publicar un enlace equivocado.
const SOCIAL = [
    { id: 'instagram', label: 'Instagram', host: /^(?:www\.)?instagram\.com$/, path: /^\/[A-Za-z0-9._]{1,30}\/?$/ },
    { id: 'tiktok', label: 'TikTok', host: /^(?:www\.)?tiktok\.com$/, path: /^\/@[A-Za-z0-9._]{2,24}\/?$/ },
    { id: 'youtube', label: 'YouTube', host: /^(?:www\.)?youtube\.com$/, path: /^\/@[A-Za-z0-9._-]{3,30}\/?$/ },
    { id: 'x', label: 'X', host: /^(?:www\.)?(?:x|twitter)\.com$/, path: /^\/[A-Za-z0-9_]{1,15}\/?$/ },
];

function socialProfiles(social = {}) {
    const unknown = Object.keys(social).filter((key) => !SOCIAL.some((network) => network.id === key));
    if (unknown.length) throw new BuildError(`Redes desconocidas en site.config.json → social: ${unknown.join(', ')}`);
    const profiles = [];
    for (const network of SOCIAL) {
        const value = (social[network.id] || '').trim();
        if (!value) continue;
        let url;
        try {
            url = new URL(value);
        } catch {
            throw new BuildError(`social.${network.id} no es una URL: ${value}`);
        }
        if (url.protocol !== 'https:' || !network.host.test(url.hostname) || !network.path.test(url.pathname)
            || url.search || url.hash || url.username || url.password || url.port) {
            throw new BuildError(`social.${network.id} no es la URL de un perfil de ${network.label}: ${value}`);
        }
        profiles.push({ ...network, url: url.href, handle: url.pathname.replace(/^\/@?|\/$/g, '') });
    }
    return profiles;
}

function applySocial(html, profiles) {
    const marker = /\n[ \t]*<!-- social:links[^\n]*-->/;
    if (!marker.test(html)) throw new BuildError('index.html no tiene el marcador <!-- social:links … -->.');
    if (!html.includes('"sameAs": []')) throw new BuildError('index.html no tiene "sameAs": [] en el JSON-LD.');
    html = html.replace('"sameAs": []', `"sameAs": ${JSON.stringify(profiles.map((profile) => profile.url))}`);
    const x = profiles.find((profile) => profile.id === 'x');
    if (x) {
        html = html.replace(/(\n([ \t]*)<meta name="twitter:card"[^\n]*)/,
            (_, line, indent) => `${line}\n${indent}<meta name="twitter:site" content="@${x.handle}">`);
    }
    if (!profiles.length) return html.replace(marker, '');
    return html.replace(marker, (match) => {
        const indent = match.match(/\n([ \t]*)/)[1];
        const items = profiles.map((profile) => `${indent}    <li><a class="btn" href="${profile.url}" rel="me noopener" `
            + `target="_blank">${profile.label}</a></li>`);
        return [
            '', `${indent}<h4>Síguenos</h4>`, `${indent}<ul class="social-links" aria-label="IRIS OS en redes sociales">`,
            ...items, `${indent}</ul>`,
        ].join('\n');
    });
}

// Página de apoyo de site.config.json → "support". Vacía = la tarjeta «Donar» sigue en «Próximamente».
const KOFI = { host: /^(?:www\.)?ko-fi\.com$/, path: /^\/[A-Za-z0-9_]{3,40}\/?$/ };

function supportPage(support = {}) {
    const unknown = Object.keys(support).filter((key) => key !== 'kofi');
    if (unknown.length) throw new BuildError(`Claves desconocidas en site.config.json → support: ${unknown.join(', ')}`);
    const value = (support.kofi || '').trim();
    if (!value) return null;
    let url;
    try {
        url = new URL(value);
    } catch {
        throw new BuildError(`support.kofi no es una URL: ${value}`);
    }
    if (url.protocol !== 'https:' || !KOFI.host.test(url.hostname) || !KOFI.path.test(url.pathname)
        || url.search || url.hash || url.username || url.password || url.port) {
        throw new BuildError(`support.kofi no es la URL de una página de Ko-fi: ${value}`);
    }
    return url.href;
}

function applySupport(html, kofi) {
    const marker = '<span class="soon" data-support="kofi">Próximamente</span>';
    if (!html.includes(marker)) throw new BuildError('index.html no tiene la tarjeta «Donar» con data-support="kofi".');
    if (!kofi) return html;
    return html.replace(marker, `<a class="feat-link" href="${kofi}" rel="noopener" target="_blank">`
        + 'Apoyar en Ko-fi <svg class="ico"><use href="#i-arrow"/></svg></a>');
}

function injectAfterCharset(html, tag) {
    const charset = '<meta charset="utf-8">';
    if (!html.includes(charset)) throw new BuildError('Falta <meta charset="utf-8"> para inyectar etiquetas.');
    return html.replace(charset, `${charset}\n    ${tag}`);
}

// Referencias locales que deben existir en la salida.
function localRefs(file, text) {
    const refs = [];
    const ext = extname(file);
    if (ext === '.html') {
        for (const match of text.matchAll(/\s(?:src|href)="([^"]+)"/g)) refs.push(match[1]);
    } else if (ext === '.css') {
        for (const match of text.matchAll(/url\(\s*['"]?([^'")]+)['"]?\s*\)/g)) refs.push(match[1]);
    } else if (ext === '.webmanifest') {
        for (const icon of JSON.parse(text).icons || []) refs.push(icon.src);
    } else if (ext === '.js') {
        for (const match of text.matchAll(/['"`]((?:assets|fonts|css|js)\/[^'"`]+)['"`]/g)) refs.push(match[1]);
    }
    // Fuera: URLs absolutas, data:, anclas y referencias internas de un data URI (%23 = #).
    return refs.filter((ref) => !/^(?:[a-z]+:|\/\/|#|%23)/i.test(ref));
}

function verify(siteOut, siteUrl) {
    const problems = [];
    const files = walk(siteOut);
    for (const file of files) {
        const ext = extname(file);
        if (!TEXT_EXT.has(ext)) continue;
        const rel = toPosix(relative(siteOut, file));
        const text = read(file);

        if (text.includes('?v=')) problems.push(`${rel}: quedan sufijos ?v= (el hash ya versiona)`);
        if (/\{\{[A-Z_]+\}\}/.test(text)) problems.push(`${rel}: queda un marcador {{…}} sin rellenar`);
        if (/(src|href|url\()\s*=?\s*["']?http:\/\//.test(text)) problems.push(`${rel}: referencia insegura http://`);

        if (ext === '.html') {
            if (/\sstyle="/.test(text)) problems.push(`${rel}: atributo style en línea (lo bloquea la CSP)`);
            if (/\son[a-z]+="/i.test(text)) problems.push(`${rel}: manejador on…= en línea (lo bloquea la CSP)`);
            for (const match of text.matchAll(/<script\b([^>]*)>/g)) {
                const attrs = match[1];
                if (!/\ssrc="/.test(attrs) && !/type="application\/ld\+json"/.test(attrs)) {
                    problems.push(`${rel}: <script> en línea (lo bloquea la CSP)`);
                }
            }
            if (/fonts\.(googleapis|gstatic)\.com/.test(text)) problems.push(`${rel}: carga fuentes de Google (la CSP solo permite 'self')`);
        }
        if (ext === '.js' && DANGEROUS_SINKS.test(text)) {
            problems.push(`${rel}: usa innerHTML/eval u otro sink que bloquea Trusted Types`);
        }

        for (const ref of localRefs(file, text)) {
            const clean = ref.split(/[?#]/)[0];
            if (!clean || clean === './') continue;
            const base = ext === '.html' || ext === '.webmanifest' || ext === '.js' ? siteOut : dirname(file);
            const target = clean.startsWith('/') ? join(siteOut, clean.slice(1)) : join(base, clean);
            if (!existsSync(target)) problems.push(`${rel}: referencia rota → ${ref}`);
        }
    }

    const index = read(join(siteOut, 'index.html'));
    if (!index.includes(`<link rel="canonical" href="${siteUrl.href}">`)) problems.push('index.html: canonical no apunta a siteUrl');
    const ogImage = index.match(/<meta property="og:image" content="([^"]+)">/);
    if (!ogImage || !ogImage[1].startsWith(siteUrl.href)) problems.push('index.html: og:image no es una URL absoluta del sitio');

    if (problems.length) throw new BuildError(`La verificación falló:\n  - ${problems.join('\n  - ')}`);
}

function precompress(siteOut) {
    let count = 0;
    for (const file of walk(siteOut)) {
        if (!TEXT_EXT.has(extname(file))) continue;
        const raw = readFileSync(file);
        if (raw.length < 512) continue;
        const gz = gzipSync(raw, { level: 9 });
        const br = brotliCompressSync(raw, {
            params: { [zlib.BROTLI_PARAM_QUALITY]: 11, [zlib.BROTLI_PARAM_SIZE_HINT]: raw.length },
        });
        if (gz.length < raw.length) writeFileSync(`${file}.gz`, gz);
        if (br.length < raw.length) writeFileSync(`${file}.br`, br);
        count += 1;
    }
    return count;
}

function renderNginx(outDir, siteUrl, config) {
    const host = siteUrl.hostname;
    const deploy = config.deploy || {};
    const root = (deploy.path || '/var/www/iris-os').replace(/\/+$/, '');
    // Dominio raíz (iris-os.cloud) → también www, que redirige al dominio sin www.
    const isApex = host.split('.').length === 2;
    const withWww = typeof deploy.www === 'boolean' ? deploy.www : isApex;
    const wwwBlock = withWww ? `
# https://www.${host} → https://${host}
server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name www.${host};

    ssl_certificate     /etc/letsencrypt/live/${host}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/${host}/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;

    return 301 https://${host}$request_uri;
}
` : '';
    const template = read(join(SITE_DIR, 'deploy', 'nginx', 'iris-os.conf.template'));
    const conf = template
        .replaceAll('{{HOST}}', host)
        .replaceAll('{{SERVER_NAMES}}', withWww ? `${host} www.${host}` : host)
        .replaceAll('{{ROOT}}', root)
        .replace('{{WWW_REDIRECT}}', wwwBlock);
    if (/\{\{[A-Z_]+\}\}/.test(conf)) throw new BuildError('La plantilla de nginx tiene marcadores sin rellenar.');
    const nginxOut = join(outDir, 'nginx');
    write(join(nginxOut, 'iris-os.conf'), conf);
    cpSync(join(SITE_DIR, 'deploy', 'nginx', 'iris-os-security-headers.conf'), join(nginxOut, 'iris-os-security-headers.conf'));
    return { host, withWww };
}

function formatBytes(bytes) {
    return bytes > 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(2)} MB` : `${(bytes / 1024).toFixed(1)} KB`;
}

export function build(options = {}) {
    const config = JSON.parse(read(join(SITE_DIR, 'site.config.json')));
    const siteUrl = normalizeSiteUrl(options.siteUrl || process.env.IRIS_SITE_URL || config.siteUrl);
    const indexable = config.indexable !== false;
    const social = socialProfiles(options.social ?? config.social ?? {});  // antes de tocar build/
    const kofi = supportPage(options.support ?? config.support ?? {});
    const outDir = options.out || join(SITE_DIR, 'build');
    const siteOut = join(outDir, 'site');
    const log = options.quiet ? () => {} : (...parts) => console.log(...parts);
    const warnings = [];

    rmSync(outDir, { recursive: true, force: true });
    mkdirSync(siteOut, { recursive: true });
    for (const entry of PUBLISH) {
        const source = join(SITE_DIR, entry);
        if (!existsSync(source)) throw new BuildError(`Falta ${entry} en pagina-iris/.`);
        cpSync(source, join(siteOut, entry), { recursive: true });
    }

    // 1-2) Huellas y referencias.
    const renames = new Map();
    fingerprint(siteOut, renames);
    for (const name of ['index.html', '404.html', 'site.webmanifest']) {
        const file = join(siteOut, name);
        write(file, replaceAll(read(file), renames).replace(/\?v=\d+/g, ''));
    }

    // 3) SEO absoluto, <base> del 404 y modo no indexable.
    let index = applySupport(applySocial(absolutize(read(join(siteOut, 'index.html')), siteUrl), social), kofi);
    if (!indexable) index = injectAfterCharset(index, '<meta name="robots" content="noindex, nofollow">');
    write(join(siteOut, 'index.html'), index);

    let notFound = read(join(siteOut, '404.html'))
        .replace(/\n\s*<!-- build\.mjs añade aquí <base>[^\n]*-->/, '');
    notFound = injectAfterCharset(notFound, `<base href="${siteUrl.pathname}">`);
    write(join(siteOut, '404.html'), notFound);

    // 4) robots.txt, sitemap.xml y security.txt.
    const today = new Date().toISOString().slice(0, 10);
    if (siteUrl.pathname !== '/') {
        warnings.push(`El sitio vive en ${siteUrl.pathname}: robots.txt solo cuenta en la raíz del dominio y la plantilla de nginx asume la raíz.`);
    }
    if (indexable) {
        write(join(siteOut, 'robots.txt'), `User-agent: *\nAllow: /\n\nSitemap: ${siteUrl.href}sitemap.xml\n`);
        write(join(siteOut, 'sitemap.xml'), [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
            '  <url>',
            `    <loc>${siteUrl.href}</loc>`,
            `    <lastmod>${today}</lastmod>`,
            '  </url>',
            '</urlset>',
            '',
        ].join('\n'));
    } else {
        write(join(siteOut, 'robots.txt'), 'User-agent: *\nDisallow: /\n');
        warnings.push('indexable es false: los buscadores no indexarán la web (robots.txt Disallow + meta noindex).');
    }

    if (config.securityContact) {
        const contact = config.securityContact.includes('@') && !config.securityContact.includes(':')
            ? `mailto:${config.securityContact}`
            : config.securityContact;
        const expires = new Date(Date.now() + 365 * 24 * 3600 * 1000).toISOString().replace(/\.\d{3}Z$/, 'Z');
        write(join(siteOut, '.well-known', 'security.txt'), [
            `Contact: ${contact}`,
            `Expires: ${expires}`,
            'Preferred-Languages: es, en',
            `Canonical: ${siteUrl.href}.well-known/security.txt`,
            '',
        ].join('\n'));
    } else {
        warnings.push('Sin securityContact en site.config.json: no se genera .well-known/security.txt.');
    }

    // 5) Verificación antes de dar el build por bueno.
    verify(siteOut, siteUrl);

    // 6) Precompresión para gzip_static / brotli_static.
    const compressed = precompress(siteOut);

    // 7) nginx.
    const nginx = renderNginx(outDir, siteUrl, config);

    const info = {
        siteUrl: siteUrl.href,
        indexable,
        social: Object.fromEntries(social.map((profile) => [profile.id, profile.url])),
        support: kofi ? { kofi } : {},
        builtAt: new Date().toISOString(),
        files: walk(siteOut).filter((file) => !/\.(gz|br)$/.test(file)).length,
    };
    write(join(outDir, 'build-info.json'), `${JSON.stringify(info, null, 4)}\n`);

    // Peso de la primera visita (lo que se descarga antes de interactuar).
    const indexHtml = read(join(siteOut, 'index.html'));
    const initial = ['index.html'];
    for (const match of indexHtml.matchAll(/<(?:link rel="(?:stylesheet|preload)"|script)[^>]*(?:href|src)="([^"]+)"/g)) {
        initial.push(match[1]);
    }
    let raw = 0;
    let wire = 0;
    for (const rel of initial) {
        const file = join(siteOut, rel);
        raw += statSync(file).size;
        wire += existsSync(`${file}.br`) ? statSync(`${file}.br`).size : statSync(file).size;
    }

    log(`\nBuild listo para ${siteUrl.href}`);
    log(`  ${toPosix(relative(process.cwd(), siteOut))}  ${info.files} archivos, ${compressed} precomprimidos`);
    log(`  ${toPosix(relative(process.cwd(), join(outDir, 'nginx')))}  configuración para ${nginx.host}${nginx.withWww ? ` (+ www.${nginx.host})` : ''}`);
    log(`  Primera visita: ${formatBytes(raw)} (${formatBytes(wire)} comprimido)`);
    for (const warning of warnings) log(`  Aviso: ${warning}`);
    return { outDir, siteOut, info, warnings };
}

const samePath = (a, b) => (process.platform === 'win32' ? a.toLowerCase() === b.toLowerCase() : a === b);
const invokedDirectly = Boolean(process.argv[1]) && samePath(fileURLToPath(import.meta.url), process.argv[1]);
if (invokedDirectly) {
    try {
        build(parseArgs(process.argv.slice(2)));
    } catch (error) {
        if (error instanceof BuildError) {
            console.error(`\nError: ${error.message}`);
            process.exit(1);
        }
        throw error;
    }
}

export { BuildError };
