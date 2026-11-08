#!/usr/bin/env node
/*
 * Despliegue al VPS por SSH, con versiones atómicas y vuelta atrás.
 *
 *   node pagina-iris/scripts/deploy.mjs              sube build/site como una versión nueva
 *   node pagina-iris/scripts/deploy.mjs --dry-run    muestra los comandos sin ejecutarlos
 *   node pagina-iris/scripts/deploy.mjs --rollback   vuelve a la versión anterior
 *   node pagina-iris/scripts/deploy.mjs --nginx      sube la configuración de nginx a ~/iris-os-nginx
 *
 * En el servidor:
 *   <path>/releases/20261002-104500/   una carpeta por despliegue
 *   <path>/current → releases/…        lo que sirve nginx (se cambia de golpe)
 *
 * Usa ssh, scp y tar del sistema (Windows 10/11 ya los trae). Autenticación solo
 * con clave SSH: este script nunca pide ni guarda contraseñas.
 */
import { spawnSync } from 'node:child_process';
import { existsSync, readFileSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const SITE_DIR = fileURLToPath(new URL('..', import.meta.url));
const BUILD_DIR = join(SITE_DIR, 'build');

const args = new Set(process.argv.slice(2));
for (const arg of args) {
    if (!['--dry-run', '--rollback', '--nginx'].includes(arg)) fail(`Opción desconocida: ${arg}`);
}
const dryRun = args.has('--dry-run');

function fail(message) {
    console.error(`\nError: ${message}`);
    process.exit(1);
}

// Comillas simples para el shell remoto (bash).
const q = (value) => `'${String(value).replace(/'/g, "'\\''")}'`;

function run(command, commandArgs, options = {}) {
    const shown = [command, ...commandArgs].join(' ');
    console.log(`$ ${shown}`);
    if (dryRun) return;
    const result = spawnSync(command, commandArgs, { stdio: 'inherit', ...options });
    if (result.error) fail(`No se pudo ejecutar ${command}: ${result.error.message}`);
    if (result.status !== 0) fail(`${command} terminó con código ${result.status}`);
}

const config = JSON.parse(readFileSync(join(SITE_DIR, 'site.config.json'), 'utf8'));
const deploy = config.deploy || {};
const localConfig = join(SITE_DIR, 'deploy.local.json');
let host;
let user;
try {
    ({ host, user } = JSON.parse(readFileSync(localConfig, 'utf8')));
} catch (error) {
    if (error.code === 'ENOENT') fail('Crea pagina-iris/deploy.local.json con el host y el usuario SSH.');
    fail('Revisa que pagina-iris/deploy.local.json contenga JSON válido.');
}
if (typeof host !== 'string' || !host.trim() || typeof user !== 'string' || !user.trim()) {
    fail('Completa host y user en pagina-iris/deploy.local.json.');
}
const path = String(deploy.path || '/var/www/iris-os').replace(/\/+$/, '');
if (!path.startsWith('/') || path.split('/').filter(Boolean).length < 2) {
    fail(`deploy.path debe ser una ruta absoluta concreta (p. ej. /var/www/iris-os), no ${path}`);
}
const keep = Math.max(2, Number(deploy.keepReleases) || 5);
const target = `${user.trim()}@${host.trim()}`;
const ssh = (script) => run('ssh', [target, script]);

if (args.has('--rollback')) {
    ssh([
        'set -e',
        `cd ${q(`${path}/releases`)}`,
        `cur=$(basename "$(readlink -f ${q(`${path}/current`)})")`,
        'prev=$(ls -1 | sort -r | grep -A1 -x "$cur" | sed -n 2p)',
        '[ -n "$prev" ] || { echo "No hay una versión anterior a $cur"; exit 1; }',
        `ln -sfn ${q(`${path}/releases/`)}"$prev" ${q(`${path}/current.tmp`)}`,
        `mv -Tf ${q(`${path}/current.tmp`)} ${q(`${path}/current`)}`,
        'echo "Versión activa: $prev (antes: $cur)"',
    ].join('; '));
    process.exit(0);
}

if (args.has('--nginx')) {
    const nginxDir = join(BUILD_DIR, 'nginx');
    if (!existsSync(join(nginxDir, 'iris-os.conf'))) fail('No hay configuración de nginx. Ejecuta antes el build.');
    ssh('mkdir -p ~/iris-os-nginx');
    run('scp', ['iris-os.conf', 'iris-os-security-headers.conf', `${target}:iris-os-nginx/`], { cwd: nginxDir });
    console.log(`
Configuración subida a ~/iris-os-nginx. Para instalarla, entra al servidor y ejecuta:

  sudo cp ~/iris-os-nginx/iris-os-security-headers.conf /etc/nginx/snippets/
  sudo cp ~/iris-os-nginx/iris-os.conf /etc/nginx/sites-available/iris-os.conf
  sudo ln -sf /etc/nginx/sites-available/iris-os.conf /etc/nginx/sites-enabled/iris-os.conf
  sudo nginx -t && sudo systemctl reload nginx
`);
    process.exit(0);
}

// Despliegue normal: solo builds hechos para la URL configurada.
const infoFile = join(BUILD_DIR, 'build-info.json');
if (!existsSync(infoFile)) fail('No hay build. Ejecuta antes: node pagina-iris/scripts/build.mjs');
const info = JSON.parse(readFileSync(infoFile, 'utf8'));
const configured = config.siteUrl ? new URL(config.siteUrl).href.replace(/\/?$/, '/') : '';
if (!configured || info.siteUrl !== configured) {
    fail(`El build es para ${info.siteUrl} pero site.config.json dice "${config.siteUrl}". Vuelve a hacer el build.`);
}

const now = new Date();
const pad = (n) => String(n).padStart(2, '0');
const stamp = `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`;
const archiveName = 'release.tar.gz';
const archive = join(BUILD_DIR, archiveName);
const release = `${path}/releases/${stamp}`;
// Fuera de releases/: si la subida falla, el archivo nunca parece una versión.
const upload = `${path}/.upload-${stamp}.tar.gz`;

console.log(`Desplegando ${info.siteUrl} (build ${info.builtAt}) en ${target}:${release}\n`);

// Rutas relativas a propósito: el tar de Git Bash interpreta "C:" como un host remoto.
run('tar', ['-czf', `../${archiveName}`, '.'], { cwd: join(BUILD_DIR, 'site') });
ssh(`mkdir -p ${q(release)}`);
run('scp', [archiveName, `${target}:${upload}`], { cwd: BUILD_DIR });
ssh([
    'set -e',
    `tar -xzf ${q(upload)} -C ${q(release)}`,
    `rm -f ${q(upload)}`,
    `chmod -R a+rX ${q(release)}`,
    `ln -sfn ${q(release)} ${q(`${path}/current.tmp`)}`,
    `mv -Tf ${q(`${path}/current.tmp`)} ${q(`${path}/current`)}`,
    `cd ${q(`${path}/releases`)}`,
    `for old in $(ls -1 | sort -r | tail -n +${keep + 1}); do [ "$old" = ${q(stamp)} ] || rm -rf -- "$old"; done`,
    `echo "Publicado: ${stamp}"`,
].join('; '));

if (!dryRun) rmSync(archive, { force: true });
console.log(`\nListo. Si algo va mal: node pagina-iris/scripts/deploy.mjs --rollback`);
