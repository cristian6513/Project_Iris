import { applyThemeSnapshot, receiveFaceTheme, receiveWizardPreset, setThemeView } from './desktop_theme.js';

const $ = (id) => document.getElementById(id);
const frame = $('workspace');
let state = { phase: 'idle', face_url: '' };
let view = 'home';
let enterWhenReady = true;
let pollTimer;
let disposed = false;

export function safeFaceUrl(raw) {
    try {
        const url = new URL(raw);
        return url.protocol === 'http:' && url.hostname === '127.0.0.1'
            && url.port && !url.username && !url.password && url.pathname === '/face/'
            ? url : null;
    } catch { return null; }
}

function render() {
    applyThemeSnapshot(state.visual);
    $('message').textContent = state.message || 'Conectando…';
    $('connection').textContent = state.phase === 'ready' ? 'Conectado' :
        state.phase === 'error' ? 'Necesita atención' : state.phase === 'setup' ? 'Configura tu asistente' : 'Preparando tu espacio';
    // The preview label belongs to the checkout; an installed Iris shows its version.
    if (state.runtime?.mode === 'packaged') {
        $('build-label').textContent = state.runtime.iris_version ? `VERSIÓN ${state.runtime.iris_version}` : 'INSTALADO';
    }
    $('log').hidden = !state.log_path;
    $('log').textContent = state.log_path ? `Registro: ${state.log_path}` : '';
    $('retry').hidden = !['error', 'idle'].includes(state.phase);
    $('setup').hidden = state.phase !== 'setup';
    $('enter').hidden = state.phase !== 'ready';
    $('return').hidden = view !== 'setup';
    const phaseIndex = ['checking', 'hermes', 'iris', 'ready'].indexOf(state.phase);
    document.querySelectorAll('[data-phase]').forEach((row, index) => {
        row.classList.toggle('active', index === phaseIndex);
        row.classList.toggle('done', index < phaseIndex);
    });
}

function showHome() {
    enterWhenReady = false;
    view = 'home';
    setThemeView(view);
    frame.hidden = true;
    // Dispose the old surface: camera/microphone and audio ownership are released.
    frame.removeAttribute('src');
    $('welcome').hidden = false;
    render();
}

function showFrame(url, nextView) {
    view = nextView;
    setThemeView(view);
    frame.src = url;
    frame.hidden = false;
    $('welcome').hidden = true;
    render();
}

function showSetup() {
    enterWhenReady = false;
    showFrame('/ui/?desktop=1', 'setup');
}

function showFace() {
    const url = safeFaceUrl(state.face_url);
    if (!url) return;
    enterWhenReady = false;
    url.searchParams.set('desktop_origin', location.origin);
    showFrame(url.href, 'face');
}

async function start() {
    showHome();
    enterWhenReady = true;
    $('retry').disabled = true;
    try {
        const response = await fetch('/desktop/start', { method: 'POST' });
        if (!response.ok) throw new Error('No se pudo contactar con el servicio de inicio.');
        state = await response.json();
        render();
    } catch (error) {
        state = { ...state, phase: 'error', message: error.message };
        render();
    } finally {
        $('retry').disabled = false;
    }
}

async function poll() {
    try {
        const response = await fetch('/desktop/state', { cache: 'no-store' });
        if (!response.ok) throw new Error('El servicio de Iris no responde. Cierra y vuelve a abrir Iris.');
        state = await response.json();
        render();
        if (enterWhenReady && state.phase === 'ready') showFace();
    } catch {
        $('connection').textContent = 'Sin conexión con el servicio de inicio';
    } finally {
        if (!disposed) pollTimer = setTimeout(poll, view === 'home' ? 700 : 4000);
    }
}

window.addEventListener('message', (event) => {
    if (event.source !== frame.contentWindow || !event.data || typeof event.data !== 'object') return;
    if (event.data.type === 'iris.desktop.theme' && view === 'face'
        && event.origin === safeFaceUrl(state.face_url)?.origin) receiveFaceTheme(event.data.theme);
    if (event.data.type === 'iris.desktop.preset' && view === 'setup'
        && event.origin === location.origin) receiveWizardPreset(event.data.preset);
    if (event.data.type === 'iris.desktop.configure' && view === 'face'
        && event.origin === safeFaceUrl(state.face_url)?.origin) showSetup();
    if (event.data.type === 'iris.desktop.launch' && view === 'setup'
        && event.origin === location.origin) void start();
});
$('configure').addEventListener('click', showSetup);
$('setup').addEventListener('click', showSetup);
$('home').addEventListener('click', showHome);
$('retry').addEventListener('click', start);
$('return').addEventListener('click', start);
$('enter').addEventListener('click', start);
window.addEventListener('pagehide', () => { disposed = true; clearTimeout(pollTimer); });
await start();
void poll();
