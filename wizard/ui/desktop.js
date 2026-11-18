import { applyThemeSnapshot, receiveFaceTheme, receiveWizardPreset, setThemeView } from './desktop_theme.js';

const $ = (id) => document.getElementById(id);
const frame = $('workspace');
const welcome = $('welcome');
const FACE_SLOW_AFTER_MS = 60_000;
const FACE_LOAD_TIMEOUT_MS = 5 * 60_000;
let state = { phase: 'idle', face_url: '' };
let view = 'home';
let enterWhenReady = true;
let faceLoad = null;
let faceError = '';
let pollTimer = null;
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
    const phase = faceError ? 'error' : faceLoad ? 'iris' : state.phase;
    const message = faceError || (faceLoad?.slow
        ? 'La habitación está tardando más de lo esperado. Sigue cargando y se abrirá cuando esté lista.'
        : faceLoad ? 'Cargando la habitación y el avatar…' : state.message) || 'Conectando…';
    if ($('message').textContent !== message) $('message').textContent = message;
    $('connection').textContent = phase === 'ready' ? 'Conectado' :
        phase === 'error' ? 'Necesita atención' : phase === 'setup' ? 'Configura tu asistente' : 'Preparando tu espacio';
    // The preview label belongs to the checkout; an installed Iris shows its version.
    if (state.runtime?.mode === 'packaged') {
        $('build-label').textContent = state.runtime.iris_version ? `VERSIÓN ${state.runtime.iris_version}` : 'INSTALADO';
    }
    $('log').hidden = !state.log_path;
    $('log').textContent = state.log_path ? `Registro: ${state.log_path}` : '';
    $('retry').hidden = !faceLoad?.slow && !['error', 'idle'].includes(phase);
    $('setup').hidden = phase !== 'setup';
    $('enter').hidden = phase !== 'ready';
    $('return').hidden = view !== 'setup';
    const phaseIndex = ['checking', 'hermes', 'iris', 'ready'].indexOf(phase);
    document.querySelectorAll('[data-phase]').forEach((row, index) => {
        row.classList.toggle('active', index === phaseIndex);
        row.classList.toggle('done', index < phaseIndex);
    });
}

function clearFaceLoad() {
    clearTimeout(faceLoad?.timer);
    faceLoad = null;
    frame.inert = false;
}

function showHome(error = '') {
    clearFaceLoad();
    faceError = error;
    enterWhenReady = false;
    view = 'home';
    setThemeView(view);
    frame.hidden = true;
    // Dispose the old surface: camera/microphone and audio ownership are released.
    frame.removeAttribute('src');
    welcome.hidden = false;
    render();
}

function showSetup(section) {
    clearFaceLoad();
    faceError = '';
    enterWhenReady = false;
    view = 'setup';
    setThemeView(view);
    frame.src = section === 'voice' ? '/ui/?desktop=1&section=voice' : '/ui/?desktop=1';
    frame.hidden = false;
    welcome.hidden = true;
    render();
}

function loadFace() {
    const url = safeFaceUrl(state.face_url);
    if (!url) {
        showHome('No se encontró la dirección local de la habitación. Vuelve a comprobar.');
        return;
    }
    enterWhenReady = false;
    const id = crypto.randomUUID();
    url.searchParams.set('desktop_origin', location.origin);
    url.searchParams.set('desktop_load', id);
    faceLoad = { id, origin: url.origin, timer: null, slow: false, visibleSince: null, visibleMs: 0 };
    watchFaceLoad();
    // Keep the frame in the viewport so it can render behind the welcome screen.
    frame.inert = true;
    frame.src = url.href;
    frame.hidden = false;
    render();
}

function watchFaceLoad() {
    const load = faceLoad;
    if (!load) return;
    clearTimeout(load.timer);
    load.timer = null;
    const now = performance.now();
    if (load.visibleSince !== null) {
        load.visibleMs += now - load.visibleSince;
        load.visibleSince = null;
    }
    if (document.hidden) return;
    if (load.visibleMs >= FACE_LOAD_TIMEOUT_MS) {
        showHome('La habitación no terminó de cargar tras 5 minutos de espera. Vuelve a comprobar para iniciar una carga nueva.');
        return;
    }
    if (!load.slow && load.visibleMs >= FACE_SLOW_AFTER_MS) {
        load.slow = true;
        render();
    }
    load.visibleSince = now;
    const next = load.slow ? FACE_LOAD_TIMEOUT_MS : FACE_SLOW_AFTER_MS;
    load.timer = setTimeout(() => {
        if (faceLoad === load) watchFaceLoad();
    }, Math.ceil(next - load.visibleMs));
}

function revealFace() {
    const restoreFocus = welcome.contains(document.activeElement);
    clearFaceLoad();
    view = 'face';
    setThemeView(view);
    welcome.hidden = true;
    if (restoreFocus) frame.focus();
    render();
}

async function start() {
    showHome();
    enterWhenReady = true;
    $('retry').disabled = true;
    try {
        const response = await fetch('/desktop/start', { method: 'POST' });
        if (!response.ok) throw new Error('No se pudo contactar con el servicio de inicio.');
        state = await response.json();
        if (enterWhenReady && state.phase === 'ready') loadFace();
        else render();
    } catch (error) {
        state = { ...state, phase: 'error', message: error.message };
        render();
    } finally {
        $('retry').disabled = false;
    }
}

async function poll() {
    pollTimer = null;
    try {
        const response = await fetch('/desktop/state', { cache: 'no-store' });
        if (!response.ok) throw new Error('El servicio de Iris no responde. Cierra y vuelve a abrir Iris.');
        state = await response.json();
        if (faceLoad && ['error', 'setup'].includes(state.phase)) showHome();
        else if (enterWhenReady && state.phase === 'ready') loadFace();
        else render();
    } catch {
        $('connection').textContent = 'Sin conexión con el servicio de inicio';
    } finally {
        if (!disposed) pollTimer = setTimeout(poll, view === 'home' ? 700 : 4000);
    }
}

function retry() {
    if (!faceLoad) return start();
    // A slow Face still owns its iframe. Refresh the service status without navigating it.
    if (pollTimer === null) return;
    clearTimeout(pollTimer);
    return poll();
}

window.addEventListener('message', (event) => {
    if (event.source !== frame.contentWindow || !event.data || typeof event.data !== 'object') return;
    if (event.data.type === 'iris.desktop.face-state' && faceLoad
        && event.origin === faceLoad.origin && event.data.id === faceLoad.id) {
        if (event.data.status === 'ready') revealFace();
        else if (event.data.status === 'error') {
            showHome('No se pudo cargar la habitación. Vuelve a comprobar o revisa el registro.');
        }
    }
    if (event.data.type === 'iris.desktop.theme' && view === 'face'
        && event.origin === safeFaceUrl(state.face_url)?.origin) receiveFaceTheme(event.data.theme);
    if (event.data.type === 'iris.desktop.preset' && view === 'setup'
        && event.origin === location.origin) receiveWizardPreset(event.data.preset);
    if (event.data.type === 'iris.desktop.configure' && view === 'face'
        && event.origin === safeFaceUrl(state.face_url)?.origin) showSetup(event.data.section);
    if (event.data.type === 'iris.desktop.launch' && view === 'setup'
        && event.origin === location.origin) void start();
});
$('configure').addEventListener('click', showSetup);
$('setup').addEventListener('click', showSetup);
$('home').addEventListener('click', () => showHome());
$('retry').addEventListener('click', retry);
$('return').addEventListener('click', start);
$('enter').addEventListener('click', start);
document.addEventListener('visibilitychange', watchFaceLoad);
window.addEventListener('pagehide', () => { disposed = true; clearTimeout(pollTimer); clearFaceLoad(); });
await start();
void poll();
