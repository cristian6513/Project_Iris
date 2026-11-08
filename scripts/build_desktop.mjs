/** Windows development preview: deliberately not a standalone public installer. */
import { spawnSync } from 'node:child_process';
import { copyFileSync, mkdirSync, writeFileSync, existsSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { homedir } from 'node:os';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
if (process.platform !== 'win32') {
    throw new Error('La vista previa distribuible se verifica en Windows. Linux sigue pendiente.');
}
const target = resolve(process.env.CARGO_TARGET_DIR || join(root, 'desktop/src-tauri/target'));
const cargo = process.env.CARGO || join(homedir(), '.cargo/bin/cargo.exe');
// Same remapping as scripts/build_staging.py: panic locations would otherwise
// record local folders (and the user name in them) inside Iris.exe.
const remaps = [
    [process.env.CARGO_HOME || join(homedir(), '.cargo'), '/cargo'],
    [process.env.RUSTUP_HOME || join(homedir(), '.rustup'), '/rustup'],
    [root, '/iris'], [target, '/target'],
].map(([source, label]) => `--remap-path-prefix=${resolve(source)}=${label}`);
const buildEnv = { ...process.env, CARGO_TARGET_DIR: target, CARGO_ENCODED_RUSTFLAGS: remaps.join('\x1f') };
delete buildEnv.RUSTFLAGS;
const result = spawnSync(cargo, [
    'build', '--release', '--locked', '--features', 'custom-protocol',
    '--manifest-path', 'desktop/src-tauri/Cargo.toml',
], { cwd: root, stdio: 'inherit', env: buildEnv });
if (result.error) throw result.error;
if (result.status !== 0) process.exit(result.status || 1);
const output = join(root, 'dist/desktop');
mkdirSync(output, { recursive: true });
const python = join(root, '.venv/Scripts/python.exe');
if (!existsSync(python)) throw new Error('Falta el entorno Python de desarrollo de Iris.');
copyFileSync(join(target, 'release/iris-desktop.exe'), join(output, 'Iris.exe'));
copyFileSync(join(root, 'desktop/third-party/Syne-OFL-1.1.txt'), join(output, 'Syne-OFL-1.1.txt'));
writeFileSync(join(output, 'runtime.json'), JSON.stringify({
    schema_version: 1, mode: 'development',
    project_root: relative(output, root), python: relative(output, python),
}, null, 2) + '\n');
writeFileSync(join(output, 'LEEME.txt'),
    'IRIS DESKTOP — VISTA PREVIA PARA WINDOWS\r\n\r\n' +
    'Abre Iris.exe. No hace falta abrir una consola.\r\n' +
    'Esta vista previa utiliza el proyecto, Python y Hermes instalados en este PC.\r\n' +
    'Conserva esta carpeta dentro de dist y no la distribuyas como instalador autónomo.\r\n' +
    'Cerrar la ventana la oculta en la bandeja. Salir de Iris Desktop cierra la aplicación;\r\n' +
    'los servicios de Iris y Hermes continúan para Companion, rutinas y mensajería.\r\n');
console.log(`Vista previa creada: ${join(output, 'Iris.exe')}`);
