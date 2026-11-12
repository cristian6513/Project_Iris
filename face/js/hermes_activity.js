import { desktopStatus } from './os_desktop_status.js?v=2';
import { ThemeFonts } from './os_fonts.js?v=2';
import { FontStudio } from './os_font_studio.js?v=3';
import { publishDesktopTheme } from './desktop_host.js';
import { DesktopWidgets } from './os_widgets.js?v=12';
import { ThemeHomePreview } from './os_theme_home_preview.js?v=13';
import { TransitionStudio } from './os_transition_studio.js?v=1';
import { TransitionPlayer } from './os_transition_player.js?v=1';
import { themeTransitions } from './os_transition_format.js?v=1';
import { decodeThemeFile, encodeThemeFile, normalizeStoredTheme, managedThemeImage, THEME_FILE_MAX_BYTES } from './os_theme_file.js?v=3';
import { DesktopHome, HomePreferences } from './os_desktop_home.js?v=10';
import { OS_ICON_PATHS, osIconSvg } from './os_icons.js?v=3';
// F17: bundled apps plus the ones installed from Iris Store, read once when the desktop loads.
import { OS_APP_CATALOG, OS_APP_MODULES, OS_APP_STYLES, OS_WIDGET_CATALOG, INSTALLED_APPS, reloadInstalledApps } from './os_app_sources.js?v=4';
import { createAppHost } from './os_app_host.js?v=5';
import { createAppRegistry } from './os_app_registry.js?v=6';
import { OS_THEME_DEFAULT, OS_THEME_PRESETS } from './os_theme.js?v=3';
import { CAMERA_ERROR_MESSAGES, attachCameraPreview, hasLiveCameraStream } from './camera_preview.js?v=2';
import * as THREE from 'three';
import {
    filterMarketplaceEntries,
    marketplaceEntries,
    marketplacePage,
    marketplaceSummary,
} from './marketplace_state.js?v=2';
import { companionLocomotionStatus } from './companion_locomotion_policy.js?v=1';
import { IrisPlayerApp } from './iris_player.js?v=7';
import { ContinuityCenterApp } from './continuity_center.js?v=9';
import { ModelCatalogApp } from './model_catalog.js?v=2';
import { VisionModelApp } from './vision_model_settings.js?v=1';
import { RenderSettings } from './render_settings.js?v=5';
import { CapabilityCenterApp } from './capability_center.js?v=7';
import { NotificationCenterApp } from './notification_center.js?v=7';
import { InboxApp } from './inbox_app.js?v=6';
import { formatCronSchedule } from './routines_state.js?v=1';
import { asyncRunCapabilities } from './async_agents_state.js?v=1';
import {
    mergeWakeSettingsSnapshot,
    wakeReadinessState,
} from './wake_settings_state.js?v=1';
import { irisAlert, irisConfirm, irisChoice, irisForm, systemUpdateDialog } from './iris_dialog.js?v=9';
import { askIrisPreview, askIrisRequest, askIrisTrust } from './app_ask_iris.js?v=3';
import { appNoticeModes } from './app_notice.js?v=1';

/** The routine form: name, schedule and what Hermes must do (all three required). */
function routineForm(title, values, confirmLabel) {
    return {
        title, icon: 'bolt', confirmLabel,
        fields: [
            { name: 'name', label: 'Nombre de la rutina', value: values.name, required: true },
            { name: 'schedule', label: 'Cuándo', value: values.schedule, required: true,
              hint: 'Formato de Hermes: every 1h, every day at 9:00 o una expresión cron.' },
            { name: 'prompt', label: '¿Qué debe hacer IRIS?', value: values.prompt, required: true, multiline: true },
        ],
    };
}

const STATE_META = Object.freeze({
    idle:     { label: 'En espera', accent: '#6ed9ff', glow: 0x16304f, detail: 'IRIS en espera' },
    thinking: { label: 'Pensando',  accent: '#d4a017', glow: 0x6a4b13, detail: 'Analizando la solicitud' },
    speaking: { label: 'Hablando',  accent: '#ff8ed7', glow: 0x6c1d50, detail: 'IRIS respondiendo' },
    tool:     { label: 'Herramienta', accent: '#73f7ff', glow: 0x247a86, detail: 'Herramienta activa' },
    skill:    { label: 'Skill',    accent: '#b4ff8a', glow: 0x3d6c29, detail: 'Habilidad activa' },
    subagent: { label: 'Subagente', accent: '#c78bff', glow: 0x4b246c, detail: 'Subagente activo' },
    job:      { label: 'Rutina',      accent: '#f5b95a', glow: 0x6e4312, detail: 'Trabajo programado activo' },
    waiting:  { label: 'Esperando', accent: '#ffd77a', glow: 0x76511a, detail: 'Esperando permiso o datos' },
    done:     { label: 'Completado', accent: '#5cffad', glow: 0x155f3a, detail: 'Trabajo terminado' },
    error:    { label: 'Error',    accent: '#ff6b88', glow: 0x702034, detail: 'Algo necesita atención' },
});



const TOOL_TO_STATE = Object.freeze({
    skill: 'skill',
    skills: 'skill',
    cron: 'job',
    cronjob: 'job',
    schedule: 'job',
    reminder: 'job',
    delegate: 'subagent',
    delegate_task: 'subagent',
    subagent: 'subagent',
});

const TOOL_LABELS = Object.freeze({
    browser_navigate: 'Navegando',
    browser_click: 'Click en navegador',
    browser_type: 'Escribiendo',
    web_extract: 'Extrayendo web',
    web_search: 'Buscando web',
    delegate_task: 'Subagente',
    terminal: 'Terminal',
    read_file: 'Leyendo archivo',
    write_file: 'Escribiendo archivo',
    patch: 'Aplicando cambio',
    skills_list: 'Listando skills',
    skill_view: 'Leyendo skill',
    cronjob: 'Tarea cron',
});

const TOOL_DONE_LABELS = Object.freeze({
    browser_navigate: 'Navegación completa',
    browser_click: 'Click completado',
    browser_type: 'Texto enviado',
    web_extract: 'Extracción completa',
    web_search: 'Búsqueda completa',
    delegate_task: 'Subagente finalizado',
    terminal: 'Terminal finalizada',
    read_file: 'Lectura completa',
    write_file: 'Archivo escrito',
    patch: 'Cambio aplicado',
    skills_list: 'Skills listadas',
    skill_view: 'Skill leída',
    cronjob: 'Cron actualizado',
});

function clampTimeline(items, max = 48) {
    return items.length > max ? items.slice(items.length - max) : items;
}

function isCompletedStatus(status = '') {
    const normalized = String(status || '').toLowerCase();
    return normalized.includes('complete')
        || normalized.includes('done')
        || normalized.includes('success')
        || normalized.includes('finished')
        || normalized === 'tool.completed';
}

function inferStateFromTool(payload = {}) {
    const status = String(payload.status || '').toLowerCase();
    if (status.includes('error') || status.includes('fail')) return 'error';
    if (status.includes('wait') || status.includes('permission')) return 'waiting';

    const tool = String(payload.tool || payload.name || '').toLowerCase();
    for (const [needle, state] of Object.entries(TOOL_TO_STATE)) {
        if (tool.includes(needle)) {
            return isCompletedStatus(status) && state !== 'subagent' ? 'done' : state;
        }
    }
    if (isCompletedStatus(status)) return 'done';
    return 'tool';
}

function nowLabel() {
    return new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function el(tag, className, text = '') {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text) node.textContent = text;
    return node;
}

// «Avisar a Iris»: how long after the person's click in an app it may send its one message.
const APP_GESTURE_MS = 3000;

// F09.4: what an app action implies, said in the confirmation next to what it works on.
const APP_ACTION_EFFECTS = Object.freeze({
    read: 'Solo lee datos.',
    local: 'Cambia algo en este equipo.',
    external: 'Actúa fuera de este equipo.',
});

/** The confirmation text: the request, the object Core described from the validated arguments
 * (never text the model wrote freely) and the effect. Rendered as text by the dialog. */
function appActionMessage(lead, operation = {}) {
    const summary = typeof operation?.summary === 'string' ? operation.summary.trim().slice(0, 160) : '';
    const effect = APP_ACTION_EFFECTS[operation?.effect] || '';
    return [lead, summary && `${summary}.`, effect].filter(Boolean).join(' ');
}

function osIcon(name = 'core') {
    return `<span class="os-icon os-icon-${name}" aria-hidden="true">${osIconSvg(name)}</span>`;
}

const OS_THEME_STORAGE_KEY = 'irisOsTheme.v1';
const OS_THEME_LEGACY_STORAGE_KEYS = ['aliceHermesOsTheme.v1'];
const OS_THEME_INSTALLER_SOURCE_KEY = 'irisOsTheme.installerSource.v1';
const OS_THEME_USER_OVERRIDE_KEY = 'irisOsTheme.userOverride.v1';
const OS_WINDOW_STORAGE_KEY = 'irisOsWindows.v1';
// F17: one-shot note across a Store reload, so the page comes back to the desktop and the Store.
const OS_RELOAD_KEY = 'irisOs.reopenAfterReload';
// Window bar: narrowest it may be beside the dock, widest it grows, its height and the gap it keeps.
const OS_WINDOW_BAR = Object.freeze({ minWidth: 170, maxWidth: 260, height: 52, gap: 10 });
// The edges and corners that resize a window besides its 'se' grip (QA12).
const OS_WINDOW_EDGES = Object.freeze(['n', 's', 'e', 'w', 'ne', 'nw', 'sw']);
const OS_WINDOW_LEGACY_STORAGE_KEYS = ['aliceHermesOsWindows.v1'];
// F09.1: the apps come from the catalog the build generates from apps/<id>/app.json. The
// registry validates it again, so an invalid entry stays out and the rest keep working.
const createOsApps = () => createAppRegistry(OS_APP_CATALOG, {
    iconNames: Object.keys(OS_ICON_PATHS),
    onError: (error) => console.warn(`[IRIS OS] App «${error.id || error.index}» no cargada: ${error.errors.join('; ')}`),
});
let currentApps = createOsApps();
let catalogRevision = 0;
// F17: one stable registry object. Home, the app manager and the theme preview keep it, and it
// always answers with the current catalog, so an app installed from Iris Store shows up live.
const OS_APPS = Object.freeze({
    get errors() { return currentApps.errors; },
    get revision() { return catalogRevision; },
    get: (id) => currentApps.get(id),
    list: (options) => currentApps.list(options),
    launcher: () => currentApps.launcher(),
    windowTable: () => currentApps.windowTable(),
    hasOwnCode: (id) => currentApps.hasOwnCode(id),
    resolve: (target) => currentApps.resolve(target),
});
// Window apps by ID (title, icon, accent and initial size), read by the window manager.
let OS_WINDOW_APPS = OS_APPS.windowTable();
// The installed apps as apps see them: live, read-only.
const INSTALLED = Object.freeze({ get: (id) => INSTALLED_APPS.get(id), has: (id) => INSTALLED_APPS.has(id) });

/** F17: rebuild the registry from the current catalog (at start, and after Iris Store changes it). */
export function refreshAppCatalog() {
    currentApps = createOsApps();
    catalogRevision += 1;
    OS_WINDOW_APPS = OS_APPS.windowTable();
    return OS_APPS;
}

function normalizeToolName(tool = '', payload = {}) {
    const raw = String(tool || '').trim();
    if (!raw) return 'Hermes tool';
    if (isCompletedStatus(payload.status || payload.event)) {
        return TOOL_DONE_LABELS[raw] || TOOL_LABELS[raw] || raw
            .replace(/^tool\./, '')
            .replace(/_/g, ' ')
            .replace(/\b\w/g, (letter) => letter.toUpperCase());
    }
    return TOOL_LABELS[raw] || raw
        .replace(/^tool\./, '')
        .replace(/_/g, ' ')
        .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function normalizeToolDetail(payload = {}, state = 'tool') {
    let value = payload.detail
        || payload.message
        || payload.preview
        || payload.label
        || '';

    if (!value && isCompletedStatus(payload.status || payload.event)) {
        const rawTool = String(payload.tool || payload.name || '').toLowerCase();
        value = rawTool.includes('delegate') || rawTool.includes('subagent')
            ? 'Trabajo del subagente terminado'
            : 'Trabajo terminado';
    }
    if (!value) value = STATE_META[state]?.detail || '';

    const duration = Number(payload.duration);
    const durationLabel = Number.isFinite(duration) && duration > 0
        ? ` · ${duration.toFixed(duration >= 10 ? 1 : 2)}s`
        : '';
    return `${String(value).replace(/\s+/g, ' ').trim()}${durationLabel}`;
}

export class HermesActivityCanvas {
    constructor({
        room = null,
        camera = null,
        renderer = null,
        initialTheme = '',
        onFocusDesk = null,
        onOpen = null,
        onClose = null,
        onOpenChat = null,
        acquireCamera = null,
        releaseCamera = null,
        getCameraError = null,
        getCameraStream = null,
        onVisionSettingsChanged = null,
        onPrepareNotificationConversation = null,
    } = {}) {
        this.room = room;
        this.camera = camera;
        this.renderer = renderer;
        this.onFocusDesk = onFocusDesk;
        this.onOpen = onOpen;
        this.onClose = onClose;
        this.onOpenChat = onOpenChat;
        this.acquireCamera = acquireCamera;
        this.releaseCamera = releaseCamera;
        this.getCameraError = getCameraError;
        this.getCameraStream = getCameraStream;
        this.onVisionSettingsChanged = onVisionSettingsChanged;
        this.onPrepareNotificationConversation = onPrepareNotificationConversation;

        this.state = 'idle';
        this.currentTool = '';
        this.currentDetail = '';
        this.timeline = [];
        this.missionBoard = null;
        this.missionDetail = null;
        this._missionWorkers = [];
        this._missionWorkersError = '';
        this.missionError = '';
        this.missionDetailError = '';
        this._missionBoardSlug = '';
        this._hermesCapabilities = null;
        this._hermesCapabilitiesError = '';
        this._hermesCapabilitiesLoading = false;
        this._asyncRuns = [];
        this._chatAsyncRuns = [];
        this._asyncSource = 'manual';
        this._asyncSelections = { manual: '', chat: '' };
        this._asyncSourceStatus = {};
        this._asyncRunsAvailable = false;
        this._asyncRunsError = '';
        this._asyncRunsLoading = false;
        this._asyncRunDetail = null;
        this._asyncRunDetailError = '';
        this._asyncRunDetailLoading = false;
        this._asyncRunDetailRequest = null;
        this._selectedAsyncRunId = '';
        this._asyncRunAction = '';
        this._asyncRunPrompt = '';
        this._activeTab = 'desktop';
        this._cronJobs = [];
        this._cronOutputs = [];
        this._cronOutputsLoading = false;
        this._cronJobOutputs = [];
        this._selectedCronJobId = '';
        this._selectedCronOutputId = '';
        this._cronError = '';
        this._cronJobOutputsError = '';
        this._cronLoading = false;
        this._cronJobOutputsLoading = false;
        this._cronJobHistoryRequest = null;
        this._cronJobOutputsLoadedId = '';
        this._cronAction = '';
        this._gatewayStatus = null;
        this._gatewayLogs = null;
        this._gatewayError = '';
        this._gatewayLoading = false;
        this._gatewayLogsLoading = false;
        this._gatewayAction = '';
        this._gatewayLoadedAt = 0;
        this._gatewayLogsLoadedAt = 0;
        this._mcpCatalog = [];
        this._mcpSummary = null;
        this._mcpError = '';
        this._mcpLoading = false;
        this._skillsCatalog = [];
        this._skillsError = '';
        this._skillsLoading = false;
        this._skillsQuery = '';
        this._skillsOnlyEligible = false;
        this._skillsExpanded = '';
        this._skillsMarket = [];
        this._skillsMarketSummary = null;
        this._skillsMarketError = '';
        this._skillsMarketLoading = false;
        this._skillsMarketLoadedAt = 0;
        this._skillsMarketLastQuery = '';
        this._skillsMarketPage = 1;
        this._skillsMarketPages = 1;
        this._skillsMarketAbort = null;
        this._skillsMarketRequestToken = 0;
        this._skillsMarketSearchTimer = null;
        this._skillsMarketQuery = '';
        this._skillsMarketCategory = 'all';
        this._skillsMarketExpanded = '';
        this._marketplaceKind = 'all';
        this._marketplaceState = 'all';
        this._marketplaceNotice = '';
        this._marketplaceVisibleLimit = 80;
        this._runtimeStatus = null;
        this._runtimeStatusError = '';
        this._runtimeStatusLoadedAt = 0;
        this._systemMetrics = null;
        this._systemMetricsError = '';
        this._systemMetricsLoadedAt = 0;
        this._deviceAvailability = null;
        this._deviceAvailabilityLoadedAt = 0;
        this._cameraStream = null;
        this._visionSettings = {
            enabled: false,
            mode: 'silent',
            cooldown_minutes: 25,
            retention_days: 7,
            paused_until: 0,
            state: 'off',
            privacy: { automatic_media_upload: false, stored_media: false },
        };
        this._visionEvents = [];
        this._visionLoading = false;
        this._visionError = '';
        this._desktopWelcomeHidden = localStorage.getItem('irisDesktopWelcome.dismissed') === 'true'
            || localStorage.getItem('aliceHermesDesktopWelcome.dismissed') === 'true';
        this._desktopLauncherOpen = false;
        this._desktopMode = 'home';
        this._osWindows = new Map();
        this._appAdapters = this._createAppAdapters();
        // F09.2: apps with their own module load on demand and keep one instance each.
        this._appHost = createAppHost({
            // F17: read when an app loads, so apps installed while the desktop runs are found.
            modules: () => OS_APP_MODULES,
            styles: () => OS_APP_STYLES,
            installStyle: (id, css) => this._installAppStyle(id, css),
            makeContext: (id) => this._appContext(id),
            onError: (id, error) => console.warn(`[IRIS OS] La app ${id} no cargó:`, error),
        });
        this._windowListOpen = false; // the «+N» list of open windows in the window bar
        this._windowViewAnchors = new Map();
        this._windowLayout = this._loadWindowLayout();
        this._windowZ = 20;
        this._dragWindow = null;
        this._snapPreview = '';
        this._viewportResizeFrame = 0;
        this._sidebarCollapsed = localStorage.getItem('irisSidebar.collapsed') === 'true'
            || localStorage.getItem('aliceHermesSidebar.collapsed') === 'true';
        this._hermesDiagnostics = null;
        this._hermesDiagnosticsError = '';
        this._hermesDiagnosticsLoading = false;
        this._hermesDoctor = null;
        this._hermesDoctorError = '';
        this._hermesDoctorLoading = false;
        this._hermesDoctorLoadedAt = 0;
        this._osSummaryTimer = null;
        this._missionLoading = false;
        this._missionDetailLoading = false;
        this._missionPollTimer = null;
        this._asyncAgentsPollTimer = null;
        this._time = 0;
        this._monitorDrawElapsed = 0;
        this._monitorDirty = true;
        this._monitorDrawReduced = null;
        this._monitorReducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)');
        this._open = false;
        this._hoveredMonitor = false;
        this._themePanelOpen = false;
        this._osTheme = this._loadOsTheme(initialTheme);
        this._transitionPlayer = new TransitionPlayer({ reduced: () => !!this._osTheme.reducedMotion });
        this._systemUpdates = null;
        this._systemUpdateLoading = false;
        this._systemUpdateApplying = false;
        this._systemUpdateError = '';
        this._wakeSettings = {
            enabled: false,
            mode: 'hey_hermes',
            phrase: 'hey hermes',
            provider: 'openwakeword',
            capabilities: {},
        };
        this._wakeRuntime = { active: false, state: 'off' };
        this._wakeSettingsLoading = false;
        this._wakeSettingsError = '';
        this._wakeSettingsDirty = false;
        this._companionSettings = {
            companion_always_on_top: true,
            companion_scale: 1,
            companion_reduced_motion: false,
            companion_show_controls: true,
            companion_locomotion_enabled: false,
            companion_quiet_mode: false,
            companion_life: 'calm',
        };
        this._companionSettingsLoading = false;
        this._companionSettingsLoaded = false;
        this._companionSettingsError = '';
        this._companionSettingsDirty = false;
        this._companionLifecycle = null;
        this._companionLifecycleLoading = false;
        this._companionLifecycleError = '';
        this._companionLifecycleAction = '';
        this._companionLifecycleNotice = '';
        this._playerApp = null;
        this._continuityApp = null;
        this._capabilityApp = null;

        this._canvas = document.createElement('canvas');
        this._canvas.width = 768;
        this._canvas.height = 456;
        this._ctx = this._canvas.getContext('2d');
        this._texture = new THREE.CanvasTexture(this._canvas);
        this._texture.colorSpace = THREE.SRGBColorSpace;
        this._texture.anisotropy = 4;

        this._raycaster = new THREE.Raycaster();
        this._pointer = new THREE.Vector2();
        this._monitorMeshes = [];

        this._buildPanel();
        this._onViewportResize = () => {
            if (this._viewportResizeFrame) cancelAnimationFrame(this._viewportResizeFrame);
            this._viewportResizeFrame = requestAnimationFrame(() => {
                this._viewportResizeFrame = 0;
                this._reflowOpenWindows();
            });
        };
        window.addEventListener('resize', this._onViewportResize);
        const homeScreen = this.panel?.querySelector('.os-home-screen');
        if (homeScreen && typeof ResizeObserver === 'function') {
            // Sidebar or panel width can change the desktop without a window resize.
            this._windowBarObserver = new ResizeObserver((entries) => this._handleHomeScreenResize(entries));
            this._windowBarObserver.observe(homeScreen);
        }
        document.addEventListener('visibilitychange', () => this._handleAppVisibilityChange());
        this._applyOsTheme();
        this._bindMonitor();
        this.pushEvent({ state: 'idle', label: 'IRIS listo', detail: 'PC en espera' });
        this._drawMonitor();
        this._renderPanel();
    }

    _bindMonitor() {
        const screens = this.room?.getObject?.('monitor_screens')
            || [this.room?.getObject?.('monitor_screen')].filter(Boolean);
        this._monitorMeshes = screens.filter(Boolean);
        if (!this._monitorMeshes.length) return;

        this._monitorMeshes.forEach((screen, index) => {
            if (!screen.material) return;
            screen.userData.hermesActivityScreen = true;
            // The desk screens used to have decorative cyan/magenta line
            // meshes floating over them. Once the Activity Canvas owns the
            // texture, those lines read like UI glitches, so hide only these
            // old screen overlays and keep the monitor geometry intact.
            screen.traverse?.((child) => {
                if (!child?.userData?.holoLine) return;
                child.visible = false;
                if (child.material?.opacity !== undefined) {
                    child.material.opacity = 0;
                    child.material.needsUpdate = true;
                }
            });
            if (index === 0) {
                if (this.room?.bindMonitorTexture) this.room.bindMonitorTexture(screen.material, this._texture);
                else {
                    screen.material.map = this._texture;
                    screen.material.needsUpdate = true;
                }
            }
        });

        const dom = this.renderer?.domElement;
        if (!dom) return;
        dom.addEventListener('pointerdown', (ev) => this._onPointerDown(ev));
        dom.addEventListener('pointermove', (ev) => this._onPointerMove(ev));
    }

    _buildPanel() {
        const app = document.getElementById('IRIS-app') || document.body;
        this.panel = el('section', 'hermes-workbench hidden');
        this.panel.setAttribute('aria-hidden', 'true');
        this.panel.innerHTML = `
            <div class="workbench-shell">
                <header class="workbench-header">
                    <div class="workbench-brand">
                        <span class="os-mark">${osIcon('core')}</span>
                        <div>
                        <h2>IRIS OS</h2>
                        </div>
                    </div>
                    <div class="os-top-status" role="status" aria-live="polite" aria-atomic="true" data-tone="checking">
                        <span class="os-status-dot" aria-hidden="true"></span>
                        <strong class="os-status-readout">Comprobando conexión…</strong>
                    </div>
                    <div class="workbench-actions">
                        <div class="os-header-metrics">
                            <div>
                                <small class="os-date-readout">Today</small>
                                <strong class="os-time-readout">--:--</strong>
                            </div>
                        </div>
                        <button class="workbench-settings-toggle" type="button" aria-label="Ajustes" title="Ajustes">${osIcon('settings')}</button>
                        <button class="workbench-close os-power" type="button" aria-label="Apagar IRIS OS">${osIcon('power')}</button>
                    </div>
                </header>
                <aside class="os-profile-panel">
                    <div class="os-face-slot" aria-hidden="true"></div>
                    <strong>IRIS</strong>
                    <span>Asistente de IA</span>
                    <small class="os-profile-core-status">Comprobando</small>
                    <button class="os-profile-voice" type="button" data-voice-action="toggle" aria-label="Activar escucha de IRIS" aria-pressed="false" title="Activar escucha de IRIS">
                        ${osIcon('voice')}
                        <span data-voice-profile-label>Escucha apagada</span>
                        <small data-voice-profile-detail>El micrófono no se inicia solo</small>
                    </button>
                </aside>
                <nav class="workbench-tabs" aria-label="Vistas de IRIS OS">
                    <button class="active" type="button" data-workbench-tab="desktop" aria-label="Inicio" aria-current="page">${osIcon('desktop')}<span>Inicio</span></button>
                    <button type="button" data-os-desktop-mode="dashboard" aria-label="Panel">${osIcon('activity')}<span>Panel</span></button>
                    <button type="button" data-workbench-tab="settings" aria-label="Ajustes">${osIcon('settings')}<span>Ajustes</span></button>
                    <button type="button" data-workbench-tab="camera" aria-label="Cámara">${osIcon('camera')}<span>Cámara</span></button>
                    <button class="os-sidebar-toggle" type="button" aria-label="Contraer navegación" title="Contraer navegación">‹‹</button>
                </nav>
                <aside class="os-right-rail">
                    <div class="os-rail-card">
                        <span>Servicios conectados</span>
                        <strong>Servicios de Iris</strong>
                        <p>Estado de cámara, voz, memoria, habilidades y conexión de IA.</p>
                    </div>
                    <div class="os-service-list">
                        <div>${osIcon('camera')}<strong>Cámara</strong><em class="os-service-camera-status is-checking">Comprobando</em></div>
                        <div>${osIcon('voice')}<strong>Micrófono</strong><em class="os-service-mic-status is-checking">Comprobando</em></div>
                        <div>${osIcon('memory')}<strong>Memoria</strong><em class="os-service-memory-status is-checking">Comprobando</em></div>
                        <div>${osIcon('skills')}<strong>Skills</strong><em class="os-service-skills-status is-checking">Comprobando</em></div>
                        <div>${osIcon('gateway')}<strong>IA</strong><em class="os-service-gateway-status is-checking">Comprobando</em></div>
                    </div>
                </aside>
                <aside class="os-theme-panel hidden" aria-label="Ajustes de tema IRIS OS">
                    <header>
                        <div>
                            <span>Tema del sistema</span>
                            <strong>IRIS OS</strong>
                        </div>
                        <button class="os-theme-close" type="button" aria-label="Cerrar ajustes">${osIcon('settings')}</button>
                    </header>
                    <section class="os-theme-section">
                        <span>Tema incluido</span>
                        <div class="os-theme-preset-row">
                            <button type="button" data-os-theme-preset="iris">IRIS</button>
                            <button type="button" data-os-theme-preset="sunrise">Solar</button>
                            <button type="button" data-os-theme-preset="verdant">Verdant</button>
                            <button type="button" data-os-theme-preset="rose">Rose</button>
                        </div>
                    </section>
                    <section class="os-theme-section os-theme-colors">
                        <label><span>Acento</span><input type="color" data-os-theme-field="accent" value="#73f7ff"></label>
                        <label><span>Brillo</span><input type="color" data-os-theme-field="accent2" value="#ff74d4"></label>
                    </section>
                    <section class="os-theme-section">
                        <span>Fondo OS</span>
                        <div class="os-theme-segmented" data-theme-group="background">
                            <button type="button" data-os-theme-background="city">City</button>
                            <button type="button" data-os-theme-background="nebula">Nebula</button>
                            <button type="button" data-os-theme-background="grid">Grid</button>
                            <button type="button" data-os-theme-background="sunrise">Solar</button>
                        </div>
                        <button type="button" data-os-theme-action="browse">Buscar más fondos</button>
                    </section>
                    <section class="os-theme-section">
                        <span>Perfil</span>
                        <div class="os-theme-segmented" data-theme-group="profile">
                            <button type="button" data-os-theme-profile="iris">IRIS</button>
                            <button type="button" data-os-theme-profile="halo">Halo</button>
                            <button type="button" data-os-theme-profile="glass">Glass</button>
                        </div>
                        <button type="button" data-os-theme-action="profile-photo">Cambiar foto</button>
                    </section>
                    <footer>
                            <button class="os-theme-reset" type="button">Restaurar</button>
                    </footer>
                </aside>
                <div class="workbench-view active" data-workbench-view="desktop">
                    <div class="os-home-screen">
                        <section class="os-home-welcome">
                            <button class="os-home-dismiss" type="button" aria-label="Cerrar bienvenida">x</button>
                            <span class="os-panel-kicker os-home-readiness-kicker">Comprobando sistema</span>
                            <strong>Bienvenido a IRIS OS.</strong>
                            <p>El escritorio está listo. Los paneles avanzados cargan cuando los abres.</p>
                            <div class="os-home-status">
                                <i></i>
                                <span class="os-home-readiness-detail">Verificando IRIS OS y su motor de IA</span>
                            </div>
                        </section>
                        <section class="os-home-launcher" aria-label="IRIS OS apps">
                            <header>
                                <div>
                                    <span>IRIS OS</span>
                                    <strong>Tus aplicaciones</strong>
                                </div>
                                <button type="button" data-os-launcher-close aria-label="Cerrar aplicaciones">x</button>
                            </header>
                            <div class="os-launcher-grid">${this._launcherButtonsHtml()}
                            </div>
                        </section>
                        <section class="os-widgets" aria-label="Widgets del escritorio"></section>
                        <div class="os-window-layer" aria-label="Ventanas de Iris OS"></div>
                        <div class="os-window-taskbar" aria-label="Ventanas abiertas"></div>
                        <div class="os-snap-preview" aria-hidden="true"></div>
                    </div>
                    <div class="os-home-dock" aria-label="Navegación del escritorio">
                        <button type="button" data-os-chat aria-label="Hablar con Iris por chat" data-accent="magenta">
                            ${osIcon('chat')}
                            <strong>Chat</strong>
                        </button>
                        <button type="button" data-os-launcher="toggle" data-accent="cyan">
                            ${osIcon('apps')}
                            <strong>Aplicaciones</strong>
                        </button>
                        <button type="button" data-os-desktop-mode="home" data-accent="cyan">
                            ${osIcon('desktop')}
                            <strong>Inicio</strong>
                        </button>
                        <button type="button" data-os-desktop-mode="dashboard" data-accent="cyan">
                            ${osIcon('desktop')}
                            <strong>Panel</strong>
                        </button>
                    </div>
                    <div class="os-dashboard-content" role="region" aria-label="Contenido del Panel" tabindex="0">
                    <div class="os-hero os-active-skill-panel">
                        <span class="os-orb"></span>
                        <div>
                            <span class="os-panel-kicker">Operación actual</span>
                            <strong class="os-active-title">Escritorio de Iris</strong>
                            <p class="os-active-detail">Centro compacto para actividad, misiones, rutinas y herramientas.</p>
                            <div class="os-tool-chip-row">
                                <span class="os-tool-chip">Motor de IA</span>
                                <span class="os-tool-chip os-current-tool-chip">Herramienta: ninguna</span>
                                <span class="os-tool-chip os-mcp-chip">MCP: 0</span>
                            </div>
                        </div>
                        <div class="os-active-ring" style="--progress: 18%">
                            <span class="os-active-progress">18%</span>
                            <small>actividad</small>
                        </div>
                    </div>
                    <div class="os-app-grid">
                        <button class="os-app-card" type="button" data-os-open="agents" data-accent="magenta">
                            ${osIcon('agents')}
                            <strong>Agentes</strong>
                            <p class="os-agents-brief">Tareas del asistente</p>
                        </button>
                        <button class="os-app-card" type="button" data-os-open="activity" data-accent="cyan">
                            ${osIcon('activity')}
                            <strong>Actividad</strong>
                            <p class="os-activity-brief">Sin actividad reciente</p>
                        </button>
                        <button class="os-app-card" type="button" data-os-open="missions" data-accent="magenta">
                            ${osIcon('missions')}
                            <strong>Tablero de tareas</strong>
                            <p class="os-mission-brief">Tareas y proyectos</p>
                        </button>
                        <button class="os-app-card" type="button" data-os-open="routines" data-accent="amber">
                            ${osIcon('routines')}
                            <strong>Tareas programadas</strong>
                            <p class="os-cron-brief">Tareas por horario</p>
                        </button>
                        <button class="os-app-card" type="button" data-os-open="automations" data-accent="amber">
                            ${osIcon('automations')}
                            <strong>Automatizaciones</strong>
                            <p data-app-brief="automations">Tareas al no usar Iris</p>
                        </button>
                        <button class="os-app-card" type="button" data-os-open="doctor" data-accent="cyan">
                            ${osIcon('doctor')}
                            <strong>Diagnóstico</strong>
                            <p class="os-doctor-brief">Centro de diagnóstico</p>
                        </button>
                        <button class="os-app-card" type="button" data-os-open="tools" data-accent="cyan">
                            ${osIcon('tools')}
                            <strong>Herramientas MCP</strong>
                            <p class="os-mcp-brief">Catálogo local</p>
                        </button>
                        <button class="os-app-card" type="button" data-os-open="skills" data-accent="magenta">
                            ${osIcon('skills')}
                            <strong>Skills</strong>
                            <p class="os-skills-brief">Skills instaladas</p>
                        </button>
                        <button class="os-app-card" type="button" data-os-open="marketplace" data-accent="magenta">
                            ${osIcon('marketplace')}
                            <strong>Marketplace</strong>
                            <p class="os-marketplace-brief">Skills y MCP disponibles</p>
                        </button>
                        <button class="os-app-card" type="button" data-os-open="notifications" data-accent="cyan">
                            ${osIcon('notifications')}<strong>Notificaciones</strong>
                            <p data-app-brief="notifications">Bandeja local</p>
                        </button>
                        <button class="os-app-card" type="button" data-os-open="permissions" data-accent="amber">
                            ${osIcon('permissions')}
                            <strong>Permisos</strong>
                            <p class="os-capability-brief">Permisos y controles</p>
                        </button>
                        <button class="os-app-card" type="button" data-os-open="player" data-accent="magenta">
                            ${osIcon('player')}
                            <strong>IRIS Player</strong>
                            <p class="os-player-brief">Biblioteca local privada</p>
                        </button>
                        <button class="os-app-card" type="button" data-os-open="continuity" data-accent="cyan">
                            ${osIcon('continuity')}
                            <strong>Perfiles y sesiones</strong>
                            <p>Perfiles y sesiones sin mezclar contexto</p>
                        </button>
                    </div>
                    <div class="os-dashboard-grid">
                        <section class="os-dash-card os-pending-panel">
                            <header><span>Tareas pendientes</span><strong class="os-pending-count">0</strong></header>
                            <div class="os-compact-list os-pending-list"></div>
                        </section>
                        <section class="os-dash-card os-execution-panel">
                            <header><span>Última ejecución</span><strong class="os-last-state">En espera</strong></header>
                            <p class="os-last-execution">Sistema en espera.</p>
                        </section>
                        <section class="os-dash-card os-next-routine-panel">
                            <header><span>Próxima rutina</span><strong class="os-routine-state">Cron</strong></header>
                            <p class="os-next-routine">Sin rutina activa.</p>
                        </section>
                        <section class="os-dash-card os-activity-log-panel">
                            <header><span>Actividad reciente</span><strong class="os-log-live">En directo</strong></header>
                            <div class="os-compact-list os-desktop-log"></div>
                        </section>
                    </div>
                    <div class="os-lower-grid">
                        <section class="os-device-panel">
                            <header><span>Estado del equipo</span><strong>Este equipo</strong></header>
                            <div class="os-device-core">
                                <div class="os-holo-core" aria-hidden="true">
                                    <span></span><span></span><span></span>
                                </div>
                                <div class="os-device-stat"><small>CPU / Local</small><strong class="os-cpu-readout">N/D</strong></div>
                                <div class="os-device-stat"><small>Runtime</small><strong class="os-network-readout">Comprobando</strong></div>
                                <div class="os-device-stat"><small>Micrófono</small><strong class="os-mic-readout">Comprobando</strong></div>
                                <div class="os-device-stat"><small>Cámara</small><strong class="os-camera-readout">Comprobando</strong></div>
                            </div>
                        </section>
                        <section class="os-insights-panel">
                            <header><span>Recursos del equipo</span><strong class="os-metrics-state">Comprobando</strong></header>
                            <div class="os-metric-rings">
                                <div style="--metric:0%"><strong class="os-cpu-usage">N/D</strong><span>CPU</span></div>
                                <div style="--metric:0%"><strong class="os-memory-usage">N/D</strong><span>Memoria</span></div>
                                <div style="--metric:0%"><strong class="os-disk-usage">N/D</strong><span>Disco</span></div>
                            </div>
                            <div class="os-mood-synthesis">
                                <span>Resumen del sistema</span>
                                <p class="os-system-summary">Comprobando servicios locales.</p>
                            </div>
                        </section>
                    </div>
                    <div class="os-bottom-bar">
                        <span class="os-bottom-dot"></span>
                        <p class="os-footer-message">"No estoy aqui solo para responder. Estoy aqui para ayudarte a coordinar, recordar y crear."</p>
                        <strong>IRIS</strong>
                    </div>
                    </div>
                </div>
                <div class="workbench-view" data-workbench-view="camera">
                    <div class="os-camera-window">
                        <header>
                            <div>
                                <span class="mission-kicker">Vision Assistant · Local</span>
                                <strong>Presencia visual privada</strong>
                                <p>Apagado por defecto. La presencia se analiza en este equipo. Solo una foto que tú pidas, con el envío autorizado, va a tu modelo de visión.</p>
                            </div>
                            <div class="os-camera-actions">
                                <button class="os-refresh" type="button" data-camera-action="start">Abrir cámara</button>
                                <button class="os-refresh" type="button" data-camera-action="stop">Cerrar vista</button>
                            </div>
                        </header>
                        <div class="os-camera-stage">
                            <video class="os-camera-video" autoplay playsinline muted></video>
                            <div class="os-camera-placeholder">
                                ${osIcon('camera')}
                                <strong>Cámara en espera</strong>
                                <p>Abre la cámara para ver la señal local.</p>
                            </div>
                        </div>
                        <div class="os-status-line os-camera-status">Cámara en espera.</div>
                        <aside class="os-vision-controls">
                            <div class="os-vision-state-row">
                                <span class="os-vision-state" data-state="off">OFF</span>
                                <span>Procesamiento local</span>
                            </div>
                            <label class="os-vision-switch">
                                <span><strong>Activar Vision Assistant</strong><small>Solo presencia, ausencia y regreso.</small></span>
                                <input type="checkbox" data-vision-field="enabled">
                            </label>
                            <div class="os-vision-fields">
                                <label class="os-vision-field">
                                    <span>Cooldown</span>
                                    <select data-vision-field="cooldown_minutes">
                                        <option value="10">10 min</option><option value="25" selected>25 min</option>
                                        <option value="45">45 min</option><option value="60">60 min</option>
                                    </select>
                                </label>
                                <label class="os-vision-field">
                                    <span>Historial</span>
                                    <select data-vision-field="retention_days">
                                        <option value="0">No guardar</option><option value="1">1 día</option>
                                        <option value="7" selected>7 días</option><option value="30">30 días</option>
                                    </select>
                                </label>
                            </div>
                            <div class="os-vision-actions">
                                <button type="button" data-vision-action="pause">Pausar 30 min</button>
                                <button type="button" data-vision-action="resume">Reanudar</button>
                                <button type="button" data-vision-action="clear">Borrar historial</button>
                            </div>
                            <div class="os-vision-privacy">
                                <strong>PRIVACY LOCK</strong>
                                <span>Sin audio · sin imágenes guardadas · sin conversación automática</span>
                            </div>
                            <div class="os-subhead">Actividad resumida</div>
                            <div class="os-vision-events" aria-live="polite"></div>
                        </aside>
                    </div>
                </div>
                <div class="workbench-view" data-workbench-view="activity">
                    <div class="workbench-current">
                        <span class="workbench-pulse"></span>
                        <div>
                            <strong class="workbench-state">Idle</strong>
                            <p class="workbench-detail">IRIS en espera</p>
                        </div>
                    </div>
                    <div class="workbench-grid">
                        <div class="workbench-card">
                            <span class="workbench-card-label">Tool</span>
                            <strong class="workbench-tool">none</strong>
                        </div>
                        <div class="workbench-card">
                            <span class="workbench-card-label">Queue</span>
                            <strong class="workbench-count">0 eventos</strong>
                        </div>
                    </div>
                    <ol class="workbench-timeline"></ol>
                </div>
                <div class="workbench-view" data-workbench-view="agents">
                    <div class="os-section os-agents-section">
                        <div class="os-section-head">
                            <div>
                                <span class="mission-kicker">Agentes</span>
                                <strong>Centro de ejecuciones</strong>
                                <p>Dos formas de delegar. Un lugar para seguir el trabajo y leer sus resultados.</p>
                            </div>
                            <div class="os-section-actions">
                                <button class="os-refresh" type="button" data-agent-action="compose">Nueva tarea</button>
                                <button class="os-refresh" type="button" data-os-refresh="agents">Actualizar</button>
                            </div>
                        </div>
                        <div class="os-agents-sources" role="tablist" aria-label="Origen de las tareas">
                            <button type="button" role="tab" aria-selected="true" data-agent-source="manual">
                                <span class="os-agent-source-icon">${osIcon('tools')}</span>
                                <span><strong>Creados por ti</strong><small>Tareas que inicias desde este panel</small></span>
                                <b class="os-agent-source-count" data-source-count="manual">0</b>
                            </button>
                            <button type="button" role="tab" aria-selected="false" tabindex="-1" data-agent-source="chat">
                                <span class="os-agent-source-icon">${osIcon('agents')}</span>
                                <span><strong>Desde el chat</strong><small>Agentes que el asistente delega por ti</small></span>
                                <b class="os-agent-source-count" data-source-count="chat">0</b>
                            </button>
                        </div>
                        <div class="os-status-line os-agents-status" role="status" aria-live="polite">Cargando tareas...</div>
                        <div class="os-agents-topology">
                            <section class="os-agents-signal">
                                <span class="os-orb"></span>
                                <div>
                                    <span class="os-panel-kicker os-agents-origin">Creados por ti</span>
                                    <strong class="os-agents-capability">Detectando</strong>
                                    <p class="os-agents-capability-detail">IRIS consulta capabilities y runs del api-server local.</p>
                                </div>
                            </section>
                            <section class="os-agents-metrics">
                                <article><span>Tareas</span><strong class="os-agents-total">0</strong></article>
                                <article><span>Activos</span><strong class="os-agents-active">0</strong></article>
                                <article><span>En espera</span><strong class="os-agents-waiting">0</strong></article>
                                <article><span>Completados</span><strong class="os-agents-done">0</strong></article>
                            </section>
                        </div>
                        <div class="os-agents-console">
                            <section class="os-agents-list-panel">
                                <div class="os-subhead os-agents-list-title">Tus tareas</div>
                                <div class="os-list os-agents-list"></div>
                            </section>
                            <section class="os-agents-detail-panel">
                                <div class="os-agents-selected"></div>
                                <div class="os-agents-actions" aria-label="Controles de la tarea"></div>
                                <div class="os-agents-result" hidden></div>
                                <div class="os-subhead">Eventos</div>
                                <div class="os-list os-agents-events"></div>
                            </section>
                        </div>
                        <form class="os-agents-create">
                            <label class="os-agents-prompt-label">¿Qué quieres delegar?<input class="os-agents-prompt" type="text" maxlength="1200" placeholder="Ej.: compara estas opciones y prepara un resumen"></label>
                            <button type="submit">Crear tarea</button>
                        </form>
                    </div>
                </div>
                <div class="workbench-view" data-workbench-view="missions">
                    <div class="mission-board">
                        <div class="mission-board-topline">
                            <div>
                                <span class="mission-kicker">Tablero de tareas</span>
                                <strong class="mission-board-name">Tablero principal</strong>
                            </div>
                            <div class="mission-board-controls">
                                <select class="mission-board-select" aria-label="Tablero de tareas"></select>
                                <button class="mission-refresh" type="button">Actualizar</button>
                            </div>
                        </div>
                        <form class="mission-create">
                            <input class="mission-title-input" type="text" maxlength="180" placeholder="Nueva tarea para IRIS">
                            <input class="mission-assignee-input" type="text" maxlength="48" placeholder="perfil opcional">
                            <button type="submit">Crear</button>
                        </form>
                        <div class="mission-status">Cargando tablero...</div>
                        <div class="mission-columns"></div>
                        <aside class="mission-detail hidden" aria-live="polite"></aside>
                    </div>
                </div>
                <div class="workbench-view" data-workbench-view="routines">
                    <div class="os-section">
                        <div class="os-section-head">
                            <div>
                                <span class="mission-kicker">Por horario</span>
                                <strong>Tareas programadas</strong>
                            </div>
                            <div class="os-section-actions">
                                <button class="os-refresh" type="button" data-cron-action="create">Nueva rutina</button>
                                <button class="os-refresh" type="button" data-os-refresh="routines">Actualizar</button>
                            </div>
                        </div>
                        <div class="os-status-line os-cron-status">Cargando rutinas...</div>
                        <div class="os-cron-console">
                            <section class="os-cron-jobs-panel">
                                <div class="os-subhead">Rutinas activas</div>
                                <div class="os-list os-cron-list"></div>
                            </section>
                            <section class="os-cron-detail-panel">
                                <div class="os-cron-selected"></div>
                                <div class="os-cron-actions" aria-label="Controles de rutina"></div>
                                <div class="os-subhead">Historial de esta rutina</div>
                                <div class="os-list os-cron-job-output-list"></div>
                            </section>
                        </div>
                        <div class="os-subhead">Últimas entregas globales</div>
                        <div class="os-list os-cron-output-list"></div>
                    </div>
                </div>
                <div class="workbench-view" data-workbench-view="doctor">
                    <div class="os-section os-doctor-section">
                        <div class="os-section-head">
                            <div>
                                <span class="mission-kicker">Diagnóstico</span>
                                <strong>Centro de diagnóstico</strong>
                                <p>Revisión del motor de IA, conexión, tareas, habilidades e integraciones.</p>
                            </div>
                            <button class="os-refresh" type="button" data-os-refresh="doctor">Actualizar</button>
                        </div>
                        <div class="os-status-line os-doctor-status">Doctor listo para ejecutar.</div>
                        <div class="os-doctor-layout">
                            <section class="os-doctor-hero">
                                <span class="os-orb"></span>
                                <div>
                                    <span class="os-panel-kicker">Informe de salud</span>
                                    <strong class="os-doctor-title">Sin diagnóstico</strong>
                                    <p class="os-doctor-detail">Abre Diagnóstico para revisar la configuración local del motor de IA.</p>
                                </div>
                                <div class="os-doctor-score">
                                    <strong class="os-doctor-issue-count">0</strong>
                                    <span>avisos</span>
                                </div>
                            </section>
                            <section class="os-compatibility-ribbon" data-state="standby" aria-label="Compatibilidad de IRIS y Hermes">
                                <div class="os-compatibility-mark">IRIS<br><span>×</span><br>HERMES</div>
                                <div>
                                    <span>Compatibilidad del runtime</span>
                                    <strong class="os-compatibility-label">Pendiente de revisión</strong>
                                    <p class="os-compatibility-detail">Doctor verificará plataforma, instalación y versión antes de activar funciones avanzadas.</p>
                                </div>
                                <div class="os-compatibility-version">
                                    <span>Base IRIS</span>
                                    <strong class="os-compatibility-minimum">—</strong>
                                </div>
                            </section>
                            <div class="os-doctor-metrics">
                                <article><span>Críticos</span><strong class="os-doctor-critical">0</strong></article>
                                <article><span>Avisos</span><strong class="os-doctor-warnings">0</strong></article>
                                <article><span>Comprobaciones</span><strong class="os-doctor-check-total">0</strong></article>
                            </div>
                            <section class="os-doctor-actions" aria-label="Doctor quick actions"></section>
                            <section class="os-gateway-manager" aria-label="Servicio de IA">
                                <header>
                                    <div>
                                        <span>Servicio de IA</span>
                                        <strong class="os-gateway-state">Detectando...</strong>
                                    </div>
                                    <button class="os-refresh os-gateway-refresh" type="button" data-gateway-action="refresh">Actualizar</button>
                                </header>
                                <div class="os-gateway-grid">
                                    <article><span>Puerto</span><strong class="os-gateway-port">8642</strong></article>
                                    <article><span>Entrega</span><strong class="os-gateway-delivery">Pendiente</strong></article>
                                    <article><span>Cron</span><strong class="os-gateway-cron">Verificando</strong></article>
                                </div>
                                <div class="os-gateway-actions">
                                    <button type="button" data-gateway-action="start">Iniciar IRIS</button>
                                    <button type="button" data-gateway-action="stop">Detener IA</button>
                                    <button type="button" data-gateway-action="restart">Reiniciar IRIS</button>
                                    <button type="button" data-gateway-action="logs">Logs</button>
                                </div>
                                <p>Reinicia el asistente sin cerrar tus ventanas. Puede interrumpir una respuesta en curso.</p>
                                <pre class="os-gateway-log">Los logs del gateway apareceran aqui.</pre>
                            </section>
                            <section class="os-doctor-check-panel">
                                <header><span>Comprobaciones</span><strong class="os-doctor-check-state">En espera</strong></header>
                                <div class="os-doctor-checks"></div>
                            </section>
                            <section class="os-doctor-recommendation-panel">
                                <header><span>Recomendaciones</span><strong class="os-doctor-rec-count">0</strong></header>
                                <div class="os-doctor-recommendations"></div>
                            </section>
                        </div>
                    </div>
                </div>
                <div class="workbench-view" data-workbench-view="theme">
                    <div class="os-theme-studio">
                        <header class="os-theme-hero">
                            <div>
                                <span class="mission-kicker">Theme Studio</span>
                                <strong>Apariencia</strong>
                                <p>Personaliza la apariencia de IRIS OS.</p>
                            </div>
                        </header>
                        <div class="os-theme-main">
                        <section class="os-theme-presets os-theme-card">
                            <header><span>Temas incluidos</span></header>
                            <div class="os-theme-preset-grid">
                                <button class="os-theme-preset-card" type="button" data-os-theme-preset="iris" data-theme-art="cyber"><strong>Cyber</strong><span>Neón y futurista</span></button>
                                <button class="os-theme-preset-card" type="button" data-os-theme-preset="rose" data-theme-art="sakura"><strong>Sakura</strong><span>Suave y elegante</span></button>
                                <button class="os-theme-preset-card" type="button" data-os-theme-preset="verdant" data-theme-art="hacker"><strong>Hacker</strong><span>Matrix y terminal</span></button>
                                <button class="os-theme-preset-card" type="button" data-os-theme-preset="sunrise" data-theme-art="classic"><strong>Classic</strong><span>Limpio y profesional</span></button>
                                <button class="os-theme-preset-card is-custom" type="button" data-os-theme-action="custom"><i>+</i><strong>Personalizado</strong><span>Crea el tuyo</span></button>
                            </div>
                        </section>
                        <section class="os-theme-card os-transition-studio"></section>
                        <section class="os-theme-card os-font-studio"></section>
                        <section class="os-theme-card os-theme-tuning">
                            <header><span>Color y brillo</span></header>
                            <label class="os-theme-color-row"><i style="--dot:#00e5ff"></i><span>Color principal</span><input type="color" data-os-theme-field="accent" value="#73f7ff"><b class="os-theme-value" data-os-theme-readout="accent">#73F7FF</b></label>
                            <label class="os-theme-color-row"><i style="--dot:#ff4d9d"></i><span>Color secundario</span><input type="color" data-os-theme-field="accent2" value="#ff74d4"><b class="os-theme-value" data-os-theme-readout="accent2">#FF74D4</b></label>
                            <label class="os-theme-range-row"><span>Brillo del acento</span><input type="range" min="0" max="100" data-os-theme-field="accentGlow" value="80"><b data-os-theme-readout="accentGlow">80%</b></label>
                            <label class="os-theme-range-row"><span>Brillo de bordes</span><input type="range" min="0" max="100" data-os-theme-field="borderGlow" value="60"><b data-os-theme-readout="borderGlow">60%</b></label>
                            <label class="os-theme-range-row"><span>Opacidad de paneles</span><input type="range" min="30" max="95" data-os-theme-field="cardOpacity" value="65"><b data-os-theme-readout="cardOpacity">65%</b></label>
                            <label class="os-theme-range-row"><span>Desenfoque</span><input type="range" min="0" max="40" data-os-theme-field="blurIntensity" value="20"><b data-os-theme-readout="blurIntensity">20px</b></label>
                            <label class="os-theme-range-row"><span>Intensidad del neón</span><input type="range" min="0" max="100" data-os-theme-field="neonIntensity" value="85"><b data-os-theme-readout="neonIntensity">85%</b></label>
                            <label class="os-theme-range-row"><span>Oscurecer fondo</span><input type="range" min="0" max="95" data-os-theme-field="backgroundDim" value="70"><b data-os-theme-readout="backgroundDim">70%</b></label>
                        </section>
                        <section class="os-theme-card os-theme-backgrounds">
                            <header><span>Fondo</span></header>
                            <div class="os-theme-background-grid">
                                <button type="button" data-os-theme-background="city" data-theme-art="cyber"><strong>Cyberpunk</strong><span>Minimal</span></button>
                                <button type="button" data-os-theme-background="hacker" data-theme-art="hacker"><strong>Hacker</strong><span>Matrix</span></button>
                                <button type="button" data-os-theme-background="classic" data-theme-art="classic"><strong>Dark Classic</strong><span>Clean</span></button>
                                <button type="button" data-os-theme-background="sakura" data-theme-art="sakura"><strong>Sakura</strong><span>Dream</span></button>
                            </div>
                            <button class="os-theme-browse" type="button" data-os-theme-action="browse">Buscar más fondos</button>
                            <small>Sube una imagen desde tu equipo.</small>
                        </section>
                        <section class="os-theme-card os-theme-profile-editor">
                            <header><span>Perfil</span></header>
                            <div class="os-theme-profile-row">
                                <div class="os-theme-avatar-preview"><span></span></div>
                                <div>
                                    <strong>Foto del perfil</strong>
                                    <small>JPG, PNG o GIF. Máx. 2MB.</small>
                                    <button type="button" data-os-theme-action="profile-photo">Cambiar foto</button>
                                    <small>Insignia del perfil</small>
                                    <button type="button" data-os-theme-profile="halo">Seleccionar insignia</button>
                                </div>
                            </div>
                        </section>
                        <section class="os-theme-card os-theme-status-preview">
                            <header><span>Muestra del perfil</span></header>
                            <div class="os-theme-mini-profile">
                                <div class="os-theme-avatar-preview small"><span></span></div>
                                <div><strong>IRIS</strong><em>Vista de ejemplo</em><small>Asistente de IA</small></div>
                            </div>
                            <button type="button" data-os-theme-profile="halo">${osIcon('settings')} Usar insignia Halo</button>
                        </section>
                        <section class="os-theme-card os-theme-reset-zone">
                            <header><span>Restaurar tema</span></header>
                            <p>Restaura los valores recomendados del tema Cyber.</p>
                            <button type="button" data-os-theme-action="reset">Restaurar valores</button>
                        </section>
                        <section class="os-theme-card os-theme-export-zone">
                            <header><span>Compartir tema</span></header>
                            <p>Comparte colores, opciones y transiciones en JSON. Las imágenes y fuentes propias se añaden por separado.</p>
                            <div><button type="button" data-os-theme-action="export">Exportar tema</button><button type="button" data-os-theme-action="import">Importar tema</button></div>
                        </section>
                        </div>
                        <div class="os-theme-side">
                        <aside class="os-theme-options os-theme-card">
                            <header><span>Opciones de interfaz</span></header>
                            <label><div>${osIcon('desktop')}<span><strong>Modo compacto</strong><small>Reduce el espaciado para ver más contenido.</small></span></div><input type="checkbox" data-os-theme-field="compactMode"></label>
                            <label><div>${osIcon('tools')}<span><strong>Brillo animado</strong><small>Activa animaciones sutiles en la interfaz.</small></span></div><input type="checkbox" data-os-theme-field="animatedGlow"></label>
                            <label><div>${osIcon('activity')}<span><strong>Reducir movimiento</strong><small>Minimiza animaciones y efectos.</small></span></div><input type="checkbox" data-os-theme-field="reducedMotion"></label>
                            <label><div>${osIcon('doctor')}<span><strong>Paneles transparentes</strong><small>Aplica transparencia a los paneles.</small></span></div><input type="checkbox" data-os-theme-field="transparentCards"></label>
                            <label class="os-theme-range-row"><span>Intensidad del fondo animado</span><input type="range" min="0" max="100" data-os-theme-field="liveIntensity" value="70"><b data-os-theme-readout="liveIntensity">70%</b></label>
                        </aside>
                        <aside class="os-theme-live os-theme-card">
                            <header><span>Vista previa de Inicio</span>${osIcon('desktop')}</header>
                            <div class="os-theme-home-preview"></div>
                            <p>El fondo, los colores y tu perfil cambian al instante.</p>
                            <div class="os-theme-home-actions"><button type="button" data-os-theme-action="refresh-preview">Actualizar vista previa</button></div>
                        </aside>
                        </div>
                    </div>
                </div>
                <div class="workbench-view" data-workbench-view="tools">
                    <div class="os-section">
                        <div class="os-section-head">
                            <div>
                                <span class="mission-kicker">Hermes MCP Runtime</span>
                                <strong>MCP Tools instalados</strong>
                                <p>Administra los servidores que Hermes ya tiene configurados.</p>
                            </div>
                            <div class="os-section-actions">
                                <button class="os-refresh" type="button" data-os-open="marketplace">Explorar Marketplace</button>
                                <button class="os-refresh" type="button" data-os-refresh="tools">Actualizar</button>
                            </div>
                        </div>
                        <div class="os-status-line os-mcp-status">Cargando MCP instalados...</div>
                        <div class="os-list os-mcp-list"></div>
                    </div>
                </div>
                <div class="workbench-view" data-workbench-view="skills">
                    <div class="os-section os-skills-section">
                        <div class="os-section-head">
                            <div>
                                <span class="mission-kicker">Skills</span>
                                <strong>Skills instaladas</strong>
                                <p>Estado, compatibilidad y configuración de las capacidades que Hermes ya conoce.</p>
                            </div>
                            <div class="os-section-actions">
                                <button class="os-refresh" type="button" data-os-open="marketplace">Explorar Marketplace</button>
                                <button class="os-refresh" type="button" data-os-refresh="skills">Actualizar</button>
                            </div>
                        </div>
                        <div class="os-status-line os-skills-status">Cargando skills...</div>
                        <div class="os-skills-toolbar">
                            <input class="os-skills-search" type="search" placeholder="Buscar skill">
                            <label><input class="os-skills-only-eligible" type="checkbox"> listas</label>
                        </div>
                        <div class="os-skills-summary">
                            <article><span>Total</span><strong class="os-skills-total">0</strong></article>
                            <article><span>En espera</span><strong class="os-skills-ready">0</strong></article>
                            <article><span>Setup</span><strong class="os-skills-setup">0</strong></article>
                            <article><span>Fuentes</span><strong class="os-skills-sources">0</strong></article>
                        </div>
                        <div class="os-list os-skills-list"></div>
                    </div>
                </div>
                <div class="workbench-view" data-workbench-view="marketplace">
                    <div class="os-section os-marketplace-section">
                        <section class="os-marketplace-hero">
                            <div class="os-marketplace-signal" aria-hidden="true">
                                ${osIcon('marketplace')}
                                <span></span><span></span><span></span>
                            </div>
                            <div>
                                <span class="mission-kicker">AMPLÍA LO QUE IRIS PUEDE HACER</span>
                                <strong>Marketplace</strong>
                                <p>Explora habilidades y conexiones MCP. Revisa sus requisitos antes de añadirlas a Iris.</p>
                            </div>
                            <div class="os-marketplace-metrics" aria-label="Resumen del catálogo">
                                <article><span>Total</span><strong class="os-market-total">0</strong></article>
                                <article><span>Skills</span><strong class="os-market-skills">0</strong></article>
                                <article><span>MCP</span><strong class="os-market-mcps">0</strong></article>
                                <article><span>En Hermes</span><strong class="os-market-installed">0</strong></article>
                            </div>
                        </section>
                        <nav class="os-marketplace-kinds" aria-label="Tipo de extensión">
                            <button type="button" class="active" data-market-kind="all">Todo</button>
                            <button type="button" data-market-kind="skill">Skills</button>
                            <button type="button" data-market-kind="mcp">MCP</button>
                        </nav>
                        <section class="os-marketplace-toolbar">
                            <label class="os-marketplace-search">
                                ${osIcon('marketplace')}
                                <input class="os-market-search" type="search" placeholder="Buscar por nombre, función, fuente o categoría" autocomplete="off">
                            </label>
                            <select class="os-market-category" aria-label="Filtrar categoría">
                                <option value="all">Todas las categorías</option>
                            </select>
                            <select class="os-market-state" aria-label="Filtrar estado">
                                <option value="all">Todos los estados</option>
                                <option value="ready">Disponible</option>
                                <option value="installed">Instalado</option>
                                <option value="setup">Requiere configuración</option>
                            </select>
                            <button class="os-refresh" type="button" data-os-refresh="marketplace">Sincronizar</button>
                        </section>
                        <div class="os-status-line os-market-status">Leyendo catálogos de Hermes...</div>
                        <div class="os-marketplace-list"></div>
                        <footer class="os-marketplace-footnote">
                            <span>Explorar no instala nada ni conecta servicios.</span>
                            <strong>Las instalaciones se administran con el motor Hermes.</strong>
                        </footer>
                    </div>
                </div>
                <div class="workbench-view" data-workbench-view="player">
                    <div class="iris-player-host" aria-live="polite"></div>
                </div>
                <div class="workbench-view" data-workbench-view="continuity">
                    <div class="continuity-center-host" aria-live="polite"></div>
                </div>
                <div class="workbench-view" data-workbench-view="models">
                    <div class="models-tabs" role="tablist" aria-label="Modelos">
                        <button type="button" role="tab" id="models-tab-selector" aria-controls="models-pane-selector" aria-selected="true" data-models-tab="selector">Selector del chat</button>
                        <button type="button" role="tab" id="models-tab-vision" aria-controls="models-pane-vision" aria-selected="false" tabindex="-1" data-models-tab="vision">Visión</button>
                    </div>
                    <div class="model-catalog-host" id="models-pane-selector" role="tabpanel" aria-labelledby="models-tab-selector" data-models-pane="selector"></div>
                    <div class="vision-model-host" id="models-pane-vision" role="tabpanel" aria-labelledby="models-tab-vision" data-models-pane="vision" hidden></div>
                </div>
                <div class="workbench-view" data-workbench-view="notifications">
                    <div class="notifications-switch" role="tablist" aria-label="Notificaciones">
                        <button type="button" role="tab" id="notifications-tab-inbox" aria-controls="notifications-pane-inbox" data-notifications-tab="inbox" aria-selected="true">Bandeja</button>
                        <button type="button" role="tab" id="notifications-tab-vision" aria-controls="notifications-pane-vision" data-notifications-tab="vision" aria-selected="false" tabindex="-1">Vision</button>
                    </div>
                    <div class="inbox-host notifications-pane" id="notifications-pane-inbox" role="tabpanel" aria-labelledby="notifications-tab-inbox" data-notifications-pane="inbox"></div>
                    <div class="notification-center-host notifications-pane" id="notifications-pane-vision" role="tabpanel" aria-labelledby="notifications-tab-vision" data-notifications-pane="vision" hidden></div>
                </div>
${this._moduleViewsHtml()}
                <div class="workbench-view" data-workbench-view="permissions">
                    <div class="capability-center-host"></div>
                </div>
                <div class="workbench-view" data-workbench-view="settings">
                    <div class="os-section os-advanced-settings">
                        <div class="os-section-head">
                            <div>
                                <span class="mission-kicker">Configuración avanzada</span>
                                <strong>Centro de configuración</strong>
                                <p>Herramientas de mantenimiento de IRIS OS.</p>
                            </div>
                        </div>
                        <nav class="os-settings-jump" aria-label="Secciones de configuración">
                            <button type="button" data-settings-jump="settings-system">Sistema</button>
                            <button type="button" data-settings-jump="settings-performance">Rendimiento</button>
                            <button type="button" data-settings-jump="settings-connections">Cuentas</button>
                             <button type="button" data-settings-jump="settings-apps">Aplicaciones</button>
                            <button type="button" data-settings-jump="settings-companion">Companion</button>
                            <button type="button" data-settings-jump="settings-voice">Activación por voz</button>
                        </nav>
                        <div class="os-advanced-grid">
                            <section class="os-theme-card os-system-update-zone" id="settings-system">
                                <header><span>Actualizaciones del sistema</span><strong class="os-update-pill" data-update-summary>En espera</strong></header>
                                <p data-update-message>Comprueba IRIS OS y sus componentes antes de instalar cambios.</p>
                                <p class="os-update-last" data-update-last hidden></p>
                                <div class="os-update-components" data-update-components>
                                    <article><span>IRIS OS</span><strong>No verificado</strong><small>Esperando revision.</small></article>
                                    <article><span>Hermes Agent</span><strong>No verificado</strong><small>Esperando revision.</small></article>
                                </div>
                                <div class="os-update-actions">
                                    <button type="button" data-system-update-action="check">Buscar actualizaciones</button>
                                    <button type="button" data-system-update-action="apply" disabled>Actualizar</button>
                                    <button type="button" data-system-update-action="rollback" hidden>Volver a la versión anterior</button>
                                </div>
                                <details class="os-about">
                                    <summary>Acerca de IRIS OS</summary>
                                    <p>IRIS OS es un proyecto independiente creado por Cristian Betancourt. Integra <strong>Hermes Agent</strong>, desarrollado por <strong>Nous Research</strong>, como motor de agentes.</p>
                                    <p>Hermes Agent conserva su copyright y licencia MIT. Los avisos de cada componente incluido se consultan desde <strong>Licencias</strong> en la cabecera del asistente de configuración.</p>
                                </details>
                            </section>
                            <section class="os-theme-card os-connections-entry" id="settings-connections">
                                <header><span>Cuentas conectadas</span><strong class="os-update-pill">Google</strong></header>
                                <p>Conecta Google para que Calendario y Correo lean tus datos. Las cuentas, sus permisos y su desconexión se administran en Conexiones.</p>
                                <div class="os-update-actions">
                                    <button type="button" data-settings-open-app="conexiones">Abrir Conexiones</button>
                                </div>
                            </section>
                            <section class="os-theme-card os-render-settings" id="settings-performance"></section>
                            <section class="os-theme-card os-home-apps-entry" id="settings-apps">
                                <header><span>Inicio y aplicaciones</span><strong class="os-update-pill">Personalizar</strong></header>
                                <p>Recupera los iconos ocultos o añade accesos a tu inicio. Ocultar no detiene las funciones de una app.</p>
                                <div class="os-update-actions"><button type="button" data-settings-personalize>Personalizar inicio</button><button type="button" data-settings-open-app="app-manager">Administrar aplicaciones</button></div>
                            </section>
                            <section class="os-theme-card os-companion-settings" id="settings-companion" data-companion-settings-state="standby">
                                <header><span>Desktop Companion</span><strong class="os-update-pill" data-companion-settings-pill>OPT-IN</strong></header>
                                <p>Configura la presencia flotante instalada. Guardar no abre, instala ni añade el Companion al inicio.</p>
                                <div class="os-companion-signal" aria-label="Contrato de privacidad del Desktop Companion">
                                    <span><i></i><b>MIC</b><small>Solo escucha autorizada</small></span>
                                    <span><i></i><b>CAM</b><small>Clic + concesión exclusiva</small></span>
                                    <span><i></i><b>DATA</b><small>Sin vídeo ni audio capturado por WebSocket</small></span>
                                    <span><i></i><b>MOVE</b><small>Opt-in + área útil nativa</small></span>
                                </div>
                                <div class="os-companion-lifecycle" aria-label="Diagnóstico local del Desktop Companion">
                                    <div class="os-companion-telemetry">
                                        <article data-companion-channel="package"><span>PACKAGE</span><strong>WAIT</strong><small>Buscando instalador local…</small></article>
                                        <article data-companion-channel="app"><span>APP</span><strong>WAIT</strong><small>Comprobando instalación…</small></article>
                                        <article data-companion-channel="webview"><span>WEBVIEW</span><strong>WAIT</strong><small>Comprobando runtime…</small></article>
                                        <article data-companion-channel="core"><span>CORE</span><strong>WAIT</strong><small>Comprobando loopback…</small></article>
                                    </div>
                                    <label class="os-companion-setting-row os-companion-autostart-row">
                                        <span><strong>Iniciar con Windows</strong><small>Independiente del servicio de IA. Reversible y disponible solo después de instalar.</small></span>
                                        <input type="checkbox" data-companion-lifecycle-field="autostart" disabled>
                                    </label>
                                    <div class="os-update-actions os-companion-lifecycle-actions">
                                        <button type="button" data-companion-lifecycle-action="refresh">Diagnosticar</button>
                                        <button type="button" data-companion-lifecycle-action="install" disabled>Instalar Companion</button>
                                        <button type="button" data-companion-lifecycle-action="launch" disabled>Abrir Companion</button>
                                    </div>
                                    <div class="os-status-line os-companion-lifecycle-status" role="status" aria-live="polite">El diagnóstico no instala ni descarga nada.</div>
                                </div>
                                <label class="os-companion-setting-row">
                                    <span><strong>Mantener encima</strong><small>Se aplica en vivo si la ventana está ejecutándose.</small></span>
                                    <input type="checkbox" data-companion-field="companion_always_on_top">
                                </label>
                                <label class="os-companion-setting-row">
                                    <span><strong>Mostrar controles</strong><small>Escucha, cámara, chat e IRIS OS. Si un dispositivo está activo, la barra permanece visible.</small></span>
                                    <input type="checkbox" data-companion-field="companion_show_controls">
                                </label>
                                <label class="os-companion-setting-row">
                                    <span><strong>Movimiento reducido</strong><small>Detiene locomoción y suaviza el mocap visual.</small></span>
                                    <input type="checkbox" data-companion-field="companion_reduced_motion">
                                </label>
                                <div class="os-companion-motion-state" data-companion-motion-state="off">
                                    <span>MOTION CHANNEL</span>
                                    <strong data-companion-motion-code>OFF</strong>
                                    <small data-companion-motion-detail>La ventana permanece fija.</small>
                                </div>
                                <label class="os-companion-setting-row">
                                    <span><strong>Locomoción por escritorio</strong><small>Solo desplaza la ventana ante una animación JSON compatible; apagada por defecto.</small></span>
                                    <input type="checkbox" data-companion-field="companion_locomotion_enabled">
                                </label>
                                <label class="os-companion-setting-row">
                                    <span><strong>Quiet mode</strong><small>Bloquea el desplazamiento sin borrar la preferencia de locomoción.</small></span>
                                    <input type="checkbox" data-companion-field="companion_quiet_mode">
                                </label>
                                <label class="os-companion-setting-row">
                                    <span><strong>Vida propia</strong><small>Cuando no hablan, se estira, lee o usa la tablet. Si falta algo, descansa.</small></span>
                                    <select data-companion-field="companion_life">
                                        <option value="off">Apagada</option>
                                        <option value="calm">Tranquila</option>
                                        <option value="active">Activa</option>
                                    </select>
                                </label>
                                <label class="os-companion-scale-field">
                                    <span>Tamaño del avatar <output data-companion-scale-value>100%</output></span>
                                    <input type="range" min="0.75" max="1.5" step="0.05" value="1" data-companion-field="companion_scale">
                                </label>
                                <div class="os-update-actions">
                                    <button type="button" data-companion-action="save">Guardar preferencias</button>
                                </div>
                                <div class="os-status-line os-companion-save-status" role="status" aria-live="polite">Guardar no abre ni cambia el estado de cámara o micrófono.</div>
                            </section>
                            <section class="os-theme-card os-voice-settings" id="settings-voice" data-voice-settings-state="off">
                                <header><span>Activación por voz</span><strong class="os-update-pill" data-voice-settings-pill>OFF</strong></header>
                                <p>El motor de voz detecta la frase localmente y toma el micrófono solo después de una activación explícita.</p>
                                <label class="os-voice-switch-row">
                                    <span><strong>Restaurar escucha al iniciar</strong><small>Apagada por defecto. Esta preferencia no inicia una captura al guardarla.</small></span>
                                    <input type="checkbox" data-voice-field="enabled">
                                </label>
                                <label class="os-voice-phrase-field">
                                    <span>Frase nativa</span>
                                    <select data-voice-field="mode">
                                        <option value="hey_hermes">Hey Hermes · openWakeWord</option>
                                        <option value="custom">Hey + nombre personalizado · Sherpa</option>
                                        <option value="keep" data-voice-custom-option hidden>Conservar frase personalizada</option>
                                    </select>
                                </label>
                                <label class="os-voice-phrase-field" data-voice-custom-name-row hidden>
                                    <span>Nombre de activación</span>
                                    <div class="os-voice-name-editor">
                                        <span aria-hidden="true">Hey</span>
                                        <input type="text" maxlength="64" autocomplete="off" placeholder="Nombre del asistente o perfil" aria-label="Nombre después de Hey" data-voice-field="phrase">
                                    </div>
                                    <small>Si no lo cambias, se usa el nombre sugerido. El motor intentará usar Sherpa; guardar no abre el micrófono.</small>
                                </label>
                                <label class="os-voice-phrase-field">
                                    <span>Sensibilidad <output data-voice-sensitivity-value>60%</output></span>
                                    <input type="range" min="0" max="1" step="0.05" value="0.6" data-voice-field="sensitivity">
                                    <small>Más alta = más estricta y menos activaciones accidentales. El cambio se aplica localmente.</small>
                                </label>
                                <div class="os-voice-engine-state">
                                    <span data-voice-engine>Comprobando el motor de voz…</span>
                                    <small data-voice-engine-detail>El primer uso puede instalar el motor local elegido.</small>
                                </div>
                                <div class="os-update-actions">
                                    <button type="button" data-voice-action="toggle">Activar ahora</button>
                                    <button type="button" data-voice-action="save">Guardar</button>
                                </div>
                                <div class="os-status-line os-voice-save-status">Sin cambios pendientes.</div>
                            </section>
                        </div>
                    </div>
                </div>
            </div>
        `;
        app.appendChild(this.panel);
        this._playerApp = new IrisPlayerApp({
            root: this.panel.querySelector('.iris-player-host'),
            onPlayback: (snapshot) => this.room?.setPlayerPlayback?.(snapshot),
            onSummary: (summary) => {
                this.panel?.querySelectorAll('.os-player-brief').forEach((node) => {
                    node.textContent = summary;
                });
            },
        });
        this._homePreferences = new HomePreferences();
        this._widgets = new DesktopWidgets({
            root: this.panel.querySelector('.os-widgets'),
            isVisible: () => this._widgetsDesktopVisible(),
            isExposed: (root) => this._widgetExposed(root),
            integrated: true,
            services: { 'player.transport': Object.freeze({ player: this._playerApp.widgetService() }) },
            askIris: (appId, options) => this._appAskIris(appId, options),
            onChange: () => { this._desktopHome?.syncWidgets(); this._themeHomePreview?.render(); },
            openApp: (id, payload) => {
                this.openCommandSurface(id);
                if (payload) this._navigateApp(id, payload);
            },
        });
        this._desktopHome = new DesktopHome({
            root: this.panel.querySelector('.os-widgets'), drawer: this.panel.querySelector('.os-home-launcher'),
            model: this._homePreferences, widgets: this._widgets, registry: OS_APPS, icon: osIcon,
            openApp: (id) => this._openOsApp(id),
            showHome: () => { this._toggleDesktopLauncher(false); this._setDesktopMode('home'); },
        });
        this._themeHomePreview = new ThemeHomePreview({
            root: this.panel.querySelector('.os-theme-home-preview'), model: this._homePreferences,
            getWidgets: () => this._widgets.layout, registry: OS_APPS, catalog: OS_WIDGET_CATALOG, icon: osIcon,
        });
        this._transitionStudio = new TransitionStudio({
            root: this.panel.querySelector('.os-transition-studio'), getTheme: () => this._osTheme,
            commit: (next) => this._commitTransitionTheme(next),
        });
        this._themeFonts = new ThemeFonts({ root: this.panel, onStatus: (text) => this._fontStudio?.status(text) });
        this._fontStudio = new FontStudio({ root: this.panel.querySelector('.os-font-studio'), runtime: this._themeFonts,
            getTheme: () => this._osTheme, commit: (next) => this._commitTransitionTheme(next) });
        this._continuityApp = new ContinuityCenterApp({
            root: this.panel.querySelector('.continuity-center-host'),
        });
        this._modelCatalogApp = new ModelCatalogApp({
            root: this.panel.querySelector('.model-catalog-host'),
        });
        // F02: the vision model tab asks Hermes only once it is shown.
        this._visionModelApp = new VisionModelApp({
            root: this.panel.querySelector('.vision-model-host'),
        });
        this._modelsTab = 'selector';
        this._renderSettings = new RenderSettings(this.panel.querySelector('.os-render-settings'));
        const modelsTabs = this.panel.querySelector('.models-tabs');
        modelsTabs?.addEventListener('click', (event) => {
            const tab = event.target?.closest?.('[data-models-tab]');
            if (tab) this._showModelsTab(tab.dataset.modelsTab, { focus: false });
        });
        modelsTabs?.addEventListener('keydown', (event) => {
            if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
            event.preventDefault();
            const next = event.key === 'Home' ? 'selector' : event.key === 'End' ? 'vision'
                : this._modelsTab === 'selector' ? 'vision' : 'selector';
            this._showModelsTab(next, { focus: true });
        });
        // F07: the Notification Center window holds the general inbox ("Bandeja") and Vision's own view.
        this._notificationsTab = 'inbox';
        this._inboxUnread = null;
        this._visionUnread = null;
        this._notificationApp = new NotificationCenterApp({
            onPrepareConversation: this.onPrepareNotificationConversation,
            root: this.panel.querySelector('.notification-center-host'),
            isVisible: () => this._open && this._hasVisibleAppSurface('notifications')
                && this._notificationsTab === 'vision' && document.visibilityState !== 'hidden',
            onSummary: (unread) => {
                this._visionUnread = unread;
                this._renderNotificationBrief();
            },
        });
        this._watchAppGestures();
        this._inboxApp = new InboxApp({
            root: this.panel.querySelector('.inbox-host'),
            isVisible: () => this._open && this._hasVisibleAppSurface('notifications')
                && this._notificationsTab === 'inbox' && document.visibilityState !== 'hidden',
            onSummary: (unread) => {
                this._inboxUnread = unread;
                this._renderNotificationBrief();
            },
            onOpenCalendar: (payload) => {
                this.openCalendar(payload);
            },
            onOpenMail: (payload) => {
                this._openOsApp('mail');
                this._navigateApp('mail', payload);
            },
            onOpenApp: (appId) => this._openOsApp(appId),
            onOpenVision: () => this._showNotificationsTab('vision', { focus: true }),
            onOpenAutomations: (ruleId) => {
                this._openOsApp('automations');
                this._navigateApp('automations', { history: ruleId });
            },
        });
        const notificationsSwitch = this.panel.querySelector('.notifications-switch');
        notificationsSwitch?.addEventListener('click', (event) => {
            const button = event.target?.closest?.('[data-notifications-tab]');
            if (button) this._showNotificationsTab(button.dataset.notificationsTab);
        });
        notificationsSwitch?.addEventListener('keydown', (event) => {
            if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
            event.preventDefault();
            const next = event.key === 'Home' ? 'inbox' : event.key === 'End' ? 'vision'
                : this._notificationsTab === 'inbox' ? 'vision' : 'inbox';
            this._showNotificationsTab(next, { focus: true });
        });
        this._capabilityApp = new CapabilityCenterApp({
            root: this.panel.querySelector('.capability-center-host'),
            onSummary: (summary) => {
                this.panel?.querySelectorAll('.os-capability-brief').forEach((node) => {
                    node.textContent = summary;
                });
            },
        });
        this.panel.querySelector('.workbench-close')?.addEventListener('click', () => this.close());
        this.panel.querySelector('.os-profile-voice')?.addEventListener('click', () => {
            window.dispatchEvent(new CustomEvent('IRIS:hermes-wake-toggle'));
        });
        this.panel.querySelector('.workbench-settings-toggle')?.addEventListener('click', () => this._setTab('settings'));
        this.panel.querySelector('.os-theme-close')?.addEventListener('click', () => this._toggleThemePanel(false));
        this.panel.querySelector('.os-theme-reset')?.addEventListener('click', () => {
            this._resetOsTheme();
        });
        this.panel.querySelector('.os-theme-panel')?.addEventListener('input', (ev) => {
            const field = ev.target?.dataset?.osThemeField;
            if (!field) return;
            this._setThemeValue(field, ev.target.type === 'checkbox' ? !!ev.target.checked : ev.target.value || '');
        });
        this.panel.querySelector('.os-theme-panel')?.addEventListener('change', (ev) => {
            const field = ev.target?.dataset?.osThemeField;
            if (!field) return;
            this._setThemeValue(field, ev.target.type === 'checkbox' ? !!ev.target.checked : ev.target.value || '');
        });
        this.panel.querySelector('.os-theme-panel')?.addEventListener('click', (ev) => {
            const preset = ev.target?.closest?.('[data-os-theme-preset]')?.dataset?.osThemePreset;
            const background = ev.target?.closest?.('[data-os-theme-background]')?.dataset?.osThemeBackground;
            const profile = ev.target?.closest?.('[data-os-theme-profile]')?.dataset?.osThemeProfile;
            if (preset) this._setThemePreset(preset);
            if (background) this._setThemeValues({ background, backgroundUrl: '' });
            if (profile) this._setThemeValues({ profile, profileUrl: '' });
        });
        this.panel.querySelector('[data-workbench-view="theme"]')?.addEventListener('input', (ev) => {
            const field = ev.target?.dataset?.osThemeField;
            if (!field) return;
            this._setThemeValue(field, ev.target.type === 'checkbox' ? !!ev.target.checked : ev.target.value || '');
        });
        this.panel.querySelector('[data-workbench-view="theme"]')?.addEventListener('click', (ev) => {
            const preset = ev.target?.closest?.('[data-os-theme-preset]')?.dataset?.osThemePreset;
            const background = ev.target?.closest?.('[data-os-theme-background]')?.dataset?.osThemeBackground;
            const profile = ev.target?.closest?.('[data-os-theme-profile]')?.dataset?.osThemeProfile;
            const action = ev.target?.closest?.('[data-os-theme-action]')?.dataset?.osThemeAction;
            if (preset) this._setThemePreset(preset);
            if (background) this._setThemeValues({ background, backgroundUrl: '' });
            if (profile) this._setThemeValues({ profile, profileUrl: '' });
            if (action) this._runThemeAction(action);
        });
        this.panel.querySelector('[data-workbench-view="settings"]')?.addEventListener('click', (ev) => {
            const settingsTarget = ev.target?.closest?.('[data-settings-jump]')?.dataset?.settingsJump;
            if (settingsTarget) {
                this.panel.querySelector(`#${settingsTarget}`)?.scrollIntoView?.({ behavior: 'smooth', block: 'start' });
            }
            const updateAction = ev.target?.closest?.('[data-system-update-action]')?.dataset?.systemUpdateAction;
            if (updateAction) this._runSystemUpdateAction(updateAction);
            const openApp = ev.target?.closest?.('[data-settings-open-app]')?.dataset?.settingsOpenApp;
            if (openApp) this._openOsApp(openApp);
            if (ev.target?.closest?.('[data-settings-personalize]')) {
                this._desktopHome.showHome(); this._desktopHome.openHomeMenu();
            }
            const voiceAction = ev.target?.closest?.('[data-voice-action]')?.dataset?.voiceAction;
            if (voiceAction === 'toggle') window.dispatchEvent(new CustomEvent('IRIS:hermes-wake-toggle'));
            if (voiceAction === 'save') this._saveWakeSettings();
            const companionAction = ev.target?.closest?.('[data-companion-action]')?.dataset?.companionAction;
            if (companionAction === 'save') this._saveCompanionSettings();
            const lifecycleAction = ev.target?.closest?.('[data-companion-lifecycle-action]')?.dataset?.companionLifecycleAction;
            if (lifecycleAction) this._runCompanionLifecycleAction(lifecycleAction);
        });
        this.panel.querySelector('[data-workbench-view="settings"]')?.addEventListener('change', (ev) => {
            if (ev.target?.dataset?.voiceField) {
                this._wakeSettingsDirty = true;
                if (ev.target.dataset.voiceField === 'mode') this._syncWakeNameEditor();
                if (ev.target.dataset.voiceField === 'sensitivity') {
                    const output = this.panel.querySelector('[data-voice-sensitivity-value]');
                    if (output) output.textContent = `${Math.round(Number(ev.target.value || 0.6) * 100)}%`;
                }
                const status = this.panel.querySelector('.os-voice-save-status');
                if (status) status.textContent = 'Cambios sin guardar.';
            }
            if (ev.target?.dataset?.companionField) this._markCompanionSettingsDirty();
            if (ev.target?.dataset?.companionLifecycleField === 'autostart') {
                this._runCompanionLifecycleAction('autostart', Boolean(ev.target.checked));
            }
        });
        this.panel.querySelector('[data-workbench-view="settings"]')?.addEventListener('input', (ev) => {
            const field = ev.target?.dataset?.voiceField;
            if (!field) return;
            this._wakeSettingsDirty = true;
            if (field === 'sensitivity') {
                const output = this.panel.querySelector('[data-voice-sensitivity-value]');
                if (output) output.textContent = `${Math.round(Number(ev.target.value || 0.6) * 100)}%`;
            }
            const status = this.panel.querySelector('.os-voice-save-status');
            if (status) status.textContent = 'Cambios sin guardar.';
        });
        this.panel.querySelector('[data-workbench-view="settings"]')?.addEventListener('input', (ev) => {
            const field = ev.target?.dataset?.companionField;
            if (!field) return;
            if (field === 'companion_scale') {
                const output = this.panel.querySelector('[data-companion-scale-value]');
                if (output) output.textContent = `${Math.round(Number(ev.target.value || 1) * 100)}%`;
            }
            this._markCompanionSettingsDirty();
        });
        window.addEventListener('IRIS:hermes-wake-state', (ev) => {
            this._wakeRuntime = { ...this._wakeRuntime, ...(ev.detail || {}) };
            this._renderWakeSettings();
        });
        window.addEventListener('IRIS:hermes-wake-settings-changed', (ev) => {
            this._wakeSettings = mergeWakeSettingsSnapshot(this._wakeSettings, ev.detail || {});
            this._renderWakeSettings();
        });
        this.panel.querySelectorAll('[data-workbench-tab]').forEach((btn) => {
            btn.addEventListener('click', () => this._setTab(btn.dataset.workbenchTab || 'activity'));
        });
        this.panel.querySelector('.os-sidebar-toggle')?.addEventListener('click', () => this._toggleSidebar());
        this.panel.querySelector('[data-os-chat]')?.addEventListener('click', () => this.onOpenChat?.());
        this.panel.querySelectorAll('[data-os-open]').forEach((btn) => {
            btn.addEventListener('click', () => this._openOsApp(btn.dataset.osOpen || 'desktop'));
        });
        this.panel.querySelectorAll('[data-os-launcher]').forEach((btn) => {
            btn.addEventListener('click', () => this._toggleDesktopLauncher());
        });
        this.panel.querySelectorAll('[data-os-launcher-close]').forEach((btn) => {
            btn.addEventListener('click', () => this._toggleDesktopLauncher(false));
        });
        this.panel.querySelectorAll('[data-os-desktop-mode]').forEach((btn) => {
            btn.addEventListener('click', () => this._setDesktopMode(btn.dataset.osDesktopMode || 'home'));
        });
        this.panel.querySelector('.os-window-layer')?.addEventListener('pointerdown', (ev) => this._handleWindowPointerDown(ev));
        // Focus the clicked window before its app handles the click. An «Abrir»
        // button may focus another app; bubbling must not take that focus back.
        this.panel.querySelector('.os-window-layer')?.addEventListener('click', (ev) => this._handleWindowClick(ev), true);
        this.panel.addEventListener('keydown', (ev) => this._handleWorkbenchKeydown(ev));
        this.panel.addEventListener('click', (ev) => {
            const retry = ev.target?.closest?.('[data-app-retry]');
            if (retry) this._retryApp(retry.dataset.appRetry || '');
        });
        this.panel.querySelector('.os-window-taskbar')?.addEventListener('click', (ev) => this._handleWindowBarClick(ev));
        // The «+N» list closes when the person works anywhere else.
        this.panel.addEventListener('pointerdown', (ev) => {
            if (this._windowListOpen && !ev.target?.closest?.('.os-window-taskbar')) this._toggleWindowList(false);
        });
        window.addEventListener('pointermove', (ev) => this._handleWindowPointerMove(ev));
        window.addEventListener('pointerup', () => this._finishWindowDrag());
        this.panel.querySelector('.os-home-dismiss')?.addEventListener('click', () => this._dismissDesktopWelcome());
        this.panel.querySelector('.os-camera-window')?.addEventListener('click', (ev) => {
            const cameraButton = ev.target?.closest?.('[data-camera-action]');
            if (cameraButton && !cameraButton.disabled) {
                this._runCameraAction(cameraButton.dataset.cameraAction || '');
                return;
            }
            const visionButton = ev.target?.closest?.('[data-vision-action]');
            if (visionButton && !visionButton.disabled) {
                this._runVisionAction(visionButton.dataset.visionAction || '');
            }
        });
        this.panel.querySelector('.os-camera-window')?.addEventListener('change', (ev) => {
            const field = ev.target?.closest?.('[data-vision-field]');
            if (!field || field.disabled) return;
            const name = field.dataset.visionField || '';
            const value = field.type === 'checkbox' ? !!field.checked : Number(field.value);
            this._saveVisionSettings({ [name]: value });
        });
        this.panel.querySelector('[data-os-refresh="routines"]')?.addEventListener('click', () => this.loadRoutines(true));
        this.panel.querySelector('[data-os-refresh="doctor"]')?.addEventListener('click', () => this.loadHermesDoctor(true));
        this.panel.querySelector('[data-os-refresh="tools"]')?.addEventListener('click', () => this.loadMcpCatalog(true));
        this.panel.querySelector('[data-os-refresh="skills"]')?.addEventListener('click', () => this.loadSkills(true));
        this.panel.querySelector('[data-os-refresh="marketplace"]')?.addEventListener('click', () => this.loadMarketplace(true));
        this.panel.querySelector('[data-os-refresh="agents"]')?.addEventListener('click', () => this.loadAsyncAgents(true));
        const agentTabs = this.panel.querySelector('.os-agents-sources');
        agentTabs?.addEventListener('click', event => {
            const tab = event.target.closest('[data-agent-source]');
            if (tab) this._setAsyncSource(tab.dataset.agentSource);
        });
        agentTabs?.addEventListener('keydown', event => {
            if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
            event.preventDefault();
            const source = event.key === 'Home' ? 'manual' : event.key === 'End' ? 'chat'
                : this._asyncSource === 'chat' ? 'manual' : 'chat';
            this._setAsyncSource(source);
            agentTabs.querySelector('[data-agent-source="' + source + '"]')?.focus();
        });
        this.panel.querySelector('[data-workbench-view="agents"] .os-section-head')?.addEventListener('click', (ev) => {
            const btn = ev.target?.closest?.('[data-agent-action]');
            if (!btn || btn.disabled) return;
            this._runAsyncAgentAction(btn.dataset.agentAction || '', btn.dataset.runId || this._selectedAsyncRunId);
        });
        this.panel.querySelector('.os-agents-list')?.addEventListener('click', (ev) => {
            const btn = ev.target?.closest?.('[data-agent-action]');
            if (btn && !btn.disabled) {
                this._runAsyncAgentAction(btn.dataset.agentAction || '', btn.dataset.runId || '');
                return;
            }
            const card = ev.target?.closest?.('[data-run-id]');
            if (card) this._selectAsyncRun(card.dataset.runId || '');
        });
        this.panel.querySelector('.os-agents-actions')?.addEventListener('click', (ev) => {
            const btn = ev.target?.closest?.('[data-agent-action]');
            if (!btn || btn.disabled) return;
            this._runAsyncAgentAction(btn.dataset.agentAction || '', btn.dataset.runId || this._selectedAsyncRunId);
        });
        this.panel.querySelector('.os-agents-create')?.addEventListener('submit', (ev) => {
            ev.preventDefault();
            this._runAsyncAgentAction('create');
        });
        this.panel.querySelector('[data-workbench-view="routines"] .os-section-head')?.addEventListener('click', (ev) => {
            const btn = ev.target?.closest?.('[data-cron-action]');
            if (!btn || btn.disabled) return;
            this._runCronAction(btn.dataset.cronAction || '', btn.dataset.cronJobId || this._selectedCronJobId);
        });
        this.panel.querySelector('.os-doctor-actions')?.addEventListener('click', (ev) => {
            const btn = ev.target?.closest?.('[data-doctor-action]');
            if (!btn || btn.disabled) return;
            this._runDoctorAction(btn.dataset.doctorAction || '');
        });
        this.panel.querySelector('.os-gateway-manager')?.addEventListener('click', (ev) => {
            const btn = ev.target?.closest?.('[data-gateway-action]');
            if (!btn || btn.disabled) return;
            this._runGatewayAction(btn.dataset.gatewayAction || '');
        });
        this.panel.querySelector('.os-cron-list')?.addEventListener('click', (ev) => {
            const actionBtn = ev.target?.closest?.('[data-cron-action]');
            if (actionBtn && !actionBtn.disabled) {
                this._runCronAction(actionBtn.dataset.cronAction || '', actionBtn.dataset.cronJobId || '');
                return;
            }
            const card = ev.target?.closest?.('[data-cron-job-id]');
            if (card) this._selectCronJob(card.dataset.cronJobId || '');
        });
        this.panel.querySelector('.os-cron-actions')?.addEventListener('click', (ev) => {
            const btn = ev.target?.closest?.('[data-cron-action]');
            if (!btn || btn.disabled) return;
            this._runCronAction(btn.dataset.cronAction || '', btn.dataset.cronJobId || this._selectedCronJobId);
        });
        this.panel.querySelectorAll('.os-cron-output-list, .os-cron-job-output-list').forEach((list) => {
            list.addEventListener('click', (ev) => {
                const btn = ev.target?.closest?.('[data-cron-speak]');
                if (btn && !btn.disabled) {
                    this._speakCronOutput(btn.dataset.cronSpeak || btn.dataset.cronOutputId || '');
                    return;
                }
                const row = ev.target?.closest?.('[data-cron-output-id]');
                if (row && list.classList.contains('os-cron-output-list')) {
                    this._selectCronOutput(row.dataset.cronOutputId || '', row.dataset.cronJobId || '');
                }
            });
        });
        this.panel.querySelector('.os-skills-search')?.addEventListener('input', (ev) => {
            this._skillsQuery = ev.target?.value || '';
            this._renderSkills();
        });
        this.panel.querySelector('.os-skills-only-eligible')?.addEventListener('change', (ev) => {
            this._skillsOnlyEligible = !!ev.target?.checked;
            this._renderSkills();
        });
        this.panel.querySelector('.os-skills-list')?.addEventListener('click', (ev) => {
            const card = ev.target?.closest?.('.os-skill-row');
            if (!card) return;
            const id = card.dataset.skillId || '';
            this._skillsExpanded = this._skillsExpanded === id ? '' : id;
            this._renderSkills();
        });
        this.panel.querySelector('.os-market-search')?.addEventListener('input', (ev) => {
            this._skillsMarketQuery = ev.target?.value || '';
            this._marketplaceVisibleLimit = 80;
            this._renderSkillsMarketplace();
            window.clearTimeout?.(this._skillsMarketSearchTimer);
            const query = this._skillsMarketQuery.trim();
            this._skillsMarketSearchTimer = window.setTimeout?.(() => {
                this.loadSkillsMarketplace({
                    force: true,
                    defer: false,
                    page: 1,
                    append: false,
                    query,
                });
            }, 280);
        });
        this.panel.querySelector('.os-market-category')?.addEventListener('change', (ev) => {
            this._skillsMarketCategory = ev.target?.value || 'all';
            this._marketplaceVisibleLimit = 80;
            this._renderSkillsMarketplace();
        });
        this.panel.querySelector('.os-market-state')?.addEventListener('change', (ev) => {
            this._marketplaceState = ev.target?.value || 'all';
            this._marketplaceVisibleLimit = 80;
            this._renderSkillsMarketplace();
        });
        this.panel.querySelector('.os-marketplace-kinds')?.addEventListener('click', (ev) => {
            const button = ev.target?.closest?.('[data-market-kind]');
            if (!button) return;
            this._marketplaceKind = button.dataset.marketKind || 'all';
            this._skillsMarketCategory = 'all';
            this._marketplaceVisibleLimit = 80;
            this._renderSkillsMarketplace();
        });
        this.panel.querySelector('.os-marketplace-list')?.addEventListener('click', (ev) => {
            const loadMore = ev.target?.closest?.('[data-market-load-more]');
            if (loadMore) {
                const filtered = Number(loadMore.dataset.filteredRemaining || 0);
                if (filtered > 0) {
                    this._marketplaceVisibleLimit += 80;
                    this._renderSkillsMarketplace();
                } else {
                    this.loadSkillsMarketplace({
                        force: false,
                        defer: false,
                        page: this._skillsMarketPage + 1,
                        append: true,
                        query: '',
                    });
                }
                return;
            }
            const action = ev.target?.closest?.('[data-market-action]');
            if (action) {
                ev.stopPropagation();
                this._runMarketplaceAction(
                    action.dataset.marketAction || '',
                    action.dataset.marketKind || '',
                    action.dataset.marketCommand || '',
                    action.dataset.marketUrl || '',
                    action,
                );
                return;
            }
            const card = ev.target?.closest?.('.os-market-card');
            if (!card) return;
            const id = card.dataset.skillId || '';
            this._skillsMarketExpanded = this._skillsMarketExpanded === id ? '' : id;
            this._renderSkillsMarketplace();
        });
        this.panel.querySelector('.mission-refresh')?.addEventListener('click', () => this.loadMissions(true));
        this.panel.querySelector('.mission-board-select')?.addEventListener('change', (ev) => {
            this._missionBoardSlug = ev.target?.value || '';
            this.missionBoard = null;
            this.missionDetail = null;
            this.loadMissions(true);
        });
        this.panel.querySelector('.mission-create')?.addEventListener('submit', (ev) => {
            ev.preventDefault();
            this._createMission();
        });
        this._renderThemePanel();
        this._renderDesktopHome();
        this._renderSidebarState();
        this._renderCameraPreview();
    }

    async _loadCompanionSettings() {
        if (this._companionSettingsLoading || this._companionSettingsDirty) return;
        this._companionSettingsLoading = true;
        // Every refresh must earn a fresh successful snapshot. Otherwise a
        // failed second GET could leave an old form enabled and overwrite
        // preferences changed from another IRIS surface.
        this._companionSettingsLoaded = false;
        this._companionSettingsError = '';
        this._renderCompanionSettings();
        try {
            const response = await fetch('/api/companion/settings', { cache: 'no-store' });
            const data = await this._responseJson(response, 'Desktop Companion');
            this._companionSettings = { ...this._companionSettings, ...data };
            this._companionSettingsDirty = false;
            this._companionSettingsLoaded = true;
        } catch (error) {
            this._companionSettingsError = error?.message || 'No pude leer las preferencias del Companion.';
        } finally {
            this._companionSettingsLoading = false;
            this._renderCompanionSettings();
        }
    }

    async _loadCompanionLifecycle() {
        if (this._companionLifecycleLoading) return;
        this._companionLifecycleLoading = true;
        this._companionLifecycleError = '';
        this._renderCompanionLifecycle();
        try {
            const response = await fetch('/api/companion/lifecycle', { cache: 'no-store' });
            this._companionLifecycle = await this._responseJson(response, 'Diagnóstico del Companion');
        } catch (error) {
            this._companionLifecycle = null;
            this._companionLifecycleError = error?.message || 'No pude diagnosticar el Desktop Companion.';
        } finally {
            this._companionLifecycleLoading = false;
            this._renderCompanionLifecycle();
        }
    }

    async _runCompanionLifecycleAction(action, enabled = false) {
        if (this._companionLifecycleLoading || this._companionLifecycleAction) return;
        if (action === 'refresh') {
            this._companionLifecycleNotice = '';
            await this._loadCompanionLifecycle();
            return;
        }
        if (action === 'install' && this._companionLifecycle?.installation?.running) {
            action = 'stop';
        }
        const lifecycleAction = this._companionLifecycle?.action || '';
        if (action === 'install' && lifecycleAction === 'reinstall') {
            if (!await irisConfirm({
                title: 'Reinstalar el Companion', icon: 'update',
                message: 'Se reinstalará IRIS en ventana (Desktop Companion) con el paquete local validado.',
                question: '¿Deseas continuar?', confirmLabel: 'Reinstalar', confirmIcon: 'update',
            })) return;
        }
        const routes = {
            install: ['/api/companion/install', 'install-companion'],
            launch: ['/api/companion/launch', 'launch-companion'],
            stop: ['/api/companion/stop', 'stop-companion'],
            autostart: ['/api/companion/autostart', 'configure-companion-autostart'],
        };
        const route = routes[action];
        if (!route) return;
        this._companionLifecycleAction = action;
        this._companionLifecycleError = '';
        this._companionLifecycleNotice = '';
        this._renderCompanionLifecycle();
        try {
            const response = await fetch(route[0], {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-IRIS-Intent': route[1],
                },
                body: action === 'autostart' ? JSON.stringify({ enabled }) : undefined,
            });
            const data = await this._responseJson(response, 'Desktop Companion');
            this._companionLifecycle = data.lifecycle || this._companionLifecycle;
            await this._loadCompanionLifecycle();
            const warnings = Array.isArray(data.warnings) ? data.warnings.join(' ') : '';
            this._companionLifecycleNotice = action === 'launch' && data.status === 'focus_requested'
                ? `Companion ya estaba abierto; pedí a Windows traerlo al frente.${warnings ? ` ${warnings}` : ''}`
                : action === 'stop'
                    ? `Desktop Companion cerrado.${warnings ? ` ${warnings}` : ''}`
                    : warnings;
        } catch (error) {
            this._companionLifecycleError = error?.message || 'No pude completar la acción del Companion.';
            this._renderCompanionLifecycle();
        } finally {
            this._companionLifecycleAction = '';
            this._renderCompanionLifecycle();
        }
    }

    _renderCompanionLifecycle() {
        const card = this.panel?.querySelector('.os-companion-settings');
        if (!card) return;
        const report = this._companionLifecycle;
        const loading = this._companionLifecycleLoading || Boolean(this._companionLifecycleAction);
        const setChannel = (name, code, detail, state = '') => {
            const channel = card.querySelector(`[data-companion-channel="${name}"]`);
            if (!channel) return;
            channel.dataset.state = state || code.toLowerCase();
            const strong = channel.querySelector('strong');
            const small = channel.querySelector('small');
            if (strong) strong.textContent = code;
            if (small) small.textContent = detail;
        };
        if (!report) {
            for (const name of ['package', 'app', 'webview', 'core']) {
                setChannel(name, loading ? 'SCAN' : 'WAIT', loading ? 'Diagnóstico local en curso…' : 'Esperando diagnóstico.');
            }
        } else {
            const pkg = report.package || {};
            const install = report.installation || {};
            const webview = report.webview2 || {};
            const core = report.core || {};
            setChannel(
                'package',
                pkg.trusted ? 'VALID' : pkg.available ? 'BLOCK' : 'NONE',
                pkg.trusted
                    ? `${pkg.version || 'sin versión'} · ${pkg.signed ? 'firmado' : 'sin firma digital'}`
                    : (pkg.error || 'No hay paquete local empaquetado.'),
                pkg.trusted ? 'ready' : pkg.available ? 'blocked' : 'missing',
            );
            const processUnknown = install.running_known === false
                && (install.installed || install.process_check_required);
            setChannel(
                'app',
                install.running ? 'LIVE' : processUnknown ? 'CHECK' : install.installed ? 'READY' : install.state === 'damaged' ? 'REPAIR' : 'ABSENT',
                install.installed
                    ? `${install.version || 'versión desconocida'} · ${install.running ? 'ejecutándose' : processUnknown ? 'estado de proceso no comprobable' : 'cerrado'}`
                    : processUnknown ? 'Instalación incompleta · proceso no comprobable.' : install.state === 'damaged' ? 'Registro incompleto o ejecutable ausente.' : 'Todavía no está instalado.',
                install.installed && !processUnknown ? 'ready' : install.installed || install.state === 'damaged' ? 'blocked' : 'missing',
            );
            setChannel(
                'webview',
                webview.available ? 'READY' : 'MISS',
                webview.available
                    ? `WebView2 ${webview.version || 'detectado'}`
                    : report.package?.webview2_mode === 'downloadBootstrapper'
                        ? 'No detectado · el instalador puede requerir Internet.'
                        : 'WebView2 Runtime no detectado.',
                webview.available ? 'ready' : 'blocked',
            );
            setChannel('core', core.available ? 'LIVE' : 'IDLE', core.available ? `IRIS Core responde en loopback:${core.port}` : `Core no responde en loopback:${core.port}`, core.available ? 'ready' : 'missing');
        }

        const installButton = card.querySelector('[data-companion-lifecycle-action="install"]');
        const launchButton = card.querySelector('[data-companion-lifecycle-action="launch"]');
        const refreshButton = card.querySelector('[data-companion-lifecycle-action="refresh"]');
        const autostart = card.querySelector('[data-companion-lifecycle-field="autostart"]');
        const unsupportedPlatform = Boolean(report && report?.platform?.supported === false);
        const actionLabels = {
            install: 'Instalar Companion', repair: 'Reparar Companion', update: 'Actualizar Companion',
            reinstall: 'Reinstalar', close_required: 'Cierra el Companion', diagnostic_required: 'Diagnóstico incompleto', manual_migration_required: 'Requiere desinstalar', newer_repair_package_required: 'Paquete de reparación anterior', unavailable: unsupportedPlatform ? 'Solo Windows x64' : 'Paquete no disponible', ready: 'Instalado',
        };
        const lifecycleBlocked = loading || Boolean(this._companionLifecycleError);
        if (refreshButton) refreshButton.disabled = loading;
        if (installButton) {
            installButton.textContent = this._companionLifecycleAction === 'stop'
                ? 'Cerrando…'
                : report?.installation?.running
                    ? 'Cierra el Companion'
                    : this._companionLifecycleAction === 'install'
                        ? 'Instalando…'
                        : (actionLabels[report?.action] || 'Instalar Companion');
            installButton.disabled = lifecycleBlocked || !(
                report?.installation?.running
                || ['install', 'repair', 'update', 'reinstall'].includes(report?.action)
            );
        }
        if (launchButton) {
            launchButton.textContent = this._companionLifecycleAction === 'launch'
                ? 'Abriendo…'
                : report?.installation?.running ? 'Mostrar ventana' : 'Abrir Companion';
            launchButton.disabled = lifecycleBlocked
                || !report?.platform?.supported
                || !report?.installation?.installed
                || report?.installation?.running_known === false;
        }
        if (autostart) {
            autostart.checked = Boolean(report?.autostart?.enabled || report?.autostart?.stale);
            const autostartActive = Boolean(report?.autostart?.enabled || report?.autostart?.stale);
            autostart.disabled = lifecycleBlocked
                || (!report?.platform?.supported && !autostartActive)
                || !report?.autostart?.supported
                || (!report?.installation?.installed && !report?.autostart?.enabled && !report?.autostart?.stale);
        }
        const status = card.querySelector('.os-companion-lifecycle-status');
        if (status) {
            status.textContent = this._companionLifecycleError
                || this._companionLifecycleNotice
                || (this._companionLifecycleAction === 'install'
                    ? 'Windows está instalando el paquete local validado…'
                    : this._companionLifecycleAction === 'stop'
                        ? 'Cerrando la presencia flotante…'
                    : this._companionLifecycleAction === 'launch'
                        ? 'Abriendo la presencia flotante…'
                        : this._companionLifecycleAction === 'autostart'
                            ? 'Actualizando el inicio de Windows…'
                            : report?.action === 'manual_migration_required'
                                ? 'La instalación actual requiere desinstalación con permisos de administrador antes de migrar a la versión por usuario.'
                            : report?.action === 'newer_repair_package_required'
                                ? 'La instalación dañada declara una versión más nueva. Incluye un paquete igual o posterior para repararla sin downgrade.'
                            : unsupportedPlatform
                                ? 'Desktop Companion solo está disponible en Windows x64.'
                            : report?.autostart?.stale
                                ? 'El autoinicio apunta a una instalación anterior; desactívalo o vuelve a activarlo para reparar el destino.'
                            : report?.package?.trusted && report?.package?.signed === false
                                ? 'Paquete local verificado por SHA-256 · compilación actual sin firma digital.'
                                : report?.package?.error
                                    ? report.package.error
                                    : 'Diagnóstico local listo · esta comprobación no descargó ni compiló nada.');
        }
    }

    _markCompanionSettingsDirty() {
        if (this._companionSettingsLoading || !this._companionSettingsLoaded) return;
        this._companionSettingsDirty = true;
        this._renderCompanionMotionStatus(this._readCompanionForm());
        const status = this.panel?.querySelector('.os-companion-save-status');
        if (status) status.textContent = 'Cambios sin guardar · no se ha abierto ningún dispositivo.';
        const card = this.panel?.querySelector('.os-companion-settings');
        if (card) {
            card.dataset.companionSettingsState = 'dirty';
            const pill = card.querySelector('[data-companion-settings-pill]');
            if (pill) pill.textContent = 'DRAFT';
            const save = card.querySelector('[data-companion-action="save"]');
            if (save) save.disabled = false;
        }
    }

    _readCompanionForm() {
        const readChecked = (field) => Boolean(
            this.panel?.querySelector(`[data-companion-field="${field}"]`)?.checked,
        );
        const scaleInput = this.panel?.querySelector('[data-companion-field="companion_scale"]');
        const scale = Math.min(1.5, Math.max(0.75, Number(scaleInput?.value || 1)));
        return {
            companion_always_on_top: readChecked('companion_always_on_top'),
            companion_show_controls: readChecked('companion_show_controls'),
            companion_reduced_motion: readChecked('companion_reduced_motion'),
            companion_locomotion_enabled: readChecked('companion_locomotion_enabled'),
            companion_quiet_mode: readChecked('companion_quiet_mode'),
            companion_life: this.panel?.querySelector('[data-companion-field="companion_life"]')?.value || 'calm',
            companion_scale: scale,
        };
    }

    async _saveCompanionSettings() {
        if (this._companionSettingsLoading || !this._companionSettingsLoaded || !this.panel) return;
        const payload = this._readCompanionForm();

        this._companionSettings = { ...this._companionSettings, ...payload };
        this._companionSettingsLoading = true;
        this._companionSettingsError = '';
        this._renderCompanionSettings();
        try {
            const response = await fetch('/api/companion/settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            const data = await this._responseJson(response, 'Guardar Desktop Companion');
            this._companionSettings = { ...this._companionSettings, ...data };
            this._companionSettingsDirty = false;
        } catch (error) {
            this._companionSettingsError = error?.message || 'No pude guardar las preferencias del Companion.';
        } finally {
            this._companionSettingsLoading = false;
            this._renderCompanionSettings();
        }
    }

    _renderCompanionSettings() {
        if (!this.panel) return;
        const settings = this._companionSettings || {};
        const card = this.panel.querySelector('.os-companion-settings');
        if (!card) return;
        card.dataset.companionSettingsState = this._companionSettingsError
            ? 'error'
            : this._companionSettingsLoading
                ? 'loading'
                : this._companionSettingsDirty ? 'dirty' : 'ready';

        for (const field of [
            'companion_always_on_top',
            'companion_show_controls',
            'companion_reduced_motion',
            'companion_locomotion_enabled',
            'companion_quiet_mode',
        ]) {
            const input = card.querySelector(`[data-companion-field="${field}"]`);
            if (input) {
                input.checked = Boolean(settings[field]);
                input.disabled = this._companionSettingsLoading || !this._companionSettingsLoaded;
            }
        }
        const life = card.querySelector('[data-companion-field="companion_life"]');
        if (life) {
            life.value = settings.companion_life || 'calm';
            life.disabled = this._companionSettingsLoading || !this._companionSettingsLoaded;
        }
        const scale = Number.isFinite(Number(settings.companion_scale))
            ? Math.min(1.5, Math.max(0.75, Number(settings.companion_scale)))
            : 1;
        const scaleInput = card.querySelector('[data-companion-field="companion_scale"]');
        if (scaleInput) {
            scaleInput.value = String(scale);
            scaleInput.disabled = this._companionSettingsLoading || !this._companionSettingsLoaded;
        }
        const scaleOutput = card.querySelector('[data-companion-scale-value]');
        if (scaleOutput) scaleOutput.textContent = `${Math.round(scale * 100)}%`;
        this._renderCompanionMotionStatus(settings);

        const pill = card.querySelector('[data-companion-settings-pill]');
        if (pill) pill.textContent = this._companionSettingsError
            ? 'ERROR'
            : this._companionSettingsLoading ? 'SYNC' : this._companionSettingsDirty ? 'DRAFT' : 'LOCAL';
        const save = card.querySelector('[data-companion-action="save"]');
        if (save) {
            save.disabled = this._companionSettingsLoading || !this._companionSettingsLoaded || !this._companionSettingsDirty;
            save.textContent = this._companionSettingsLoading ? 'Guardando…' : 'Guardar preferencias';
        }
        const status = card.querySelector('.os-companion-save-status');
        if (status) status.textContent = this._companionSettingsError
            || (this._companionSettingsLoading
                ? 'Sincronizando preferencias locales…'
                : this._companionSettingsDirty
                    ? 'Cambios sin guardar · no se ha abierto ningún dispositivo.'
                    : 'Guardado · no se abrió ni cambió el estado de cámara o micrófono.');
    }

    _renderCompanionMotionStatus(settings = {}) {
        const card = this.panel?.querySelector('.os-companion-settings');
        const state = card?.querySelector('.os-companion-motion-state');
        if (!state) return;
        const status = companionLocomotionStatus(settings);
        state.dataset.companionMotionState = status.code.toLowerCase();
        const code = state.querySelector('[data-companion-motion-code]');
        const detail = state.querySelector('[data-companion-motion-detail]');
        if (code) code.textContent = status.code;
        if (detail) detail.textContent = status.detail;
    }

    async _loadWakeSettings() {
        this._wakeSettingsLoading = true;
        this._wakeSettingsError = '';
        this._renderWakeSettings();
        try {
            const response = await fetch('/api/hermes-wake/settings');
            const data = await this._responseJson(response, 'Activación de Hermes');
            this._wakeSettings = mergeWakeSettingsSnapshot(this._wakeSettings, data);
            this._wakeSettingsDirty = false;
            this._wakeRuntime = { ...this._wakeRuntime, ...(data.runtime || {}) };
            window.dispatchEvent(new CustomEvent('IRIS:hermes-wake-runtime-sync', {
                detail: data.runtime || {},
            }));
        } catch (error) {
            this._wakeSettingsError = error?.message || 'No pude leer la activación de Hermes.';
        } finally {
            this._wakeSettingsLoading = false;
            this._renderWakeSettings();
        }
    }

    _wakeNameFromPhrase(value = '') {
        return String(value || '').trim().replace(/^hey\s+/i, '').trim();
    }

    _suggestedWakeName() {
        const identityName = this.panel?.querySelector('#quick-name')
            || document.querySelector('#quick-name');
        return String(
            this._wakeSettings?.suggested_name
            || identityName?.value
            || identityName?.placeholder
            || 'IRIS'
        ).trim() || 'IRIS';
    }

    _syncWakeNameEditor() {
        if (!this.panel) return;
        const mode = this.panel.querySelector('[data-voice-field="mode"]')?.value || 'hey_hermes';
        const row = this.panel.querySelector('[data-voice-custom-name-row]');
        const input = this.panel.querySelector('[data-voice-field="phrase"]');
        if (row) row.hidden = mode !== 'custom';
        if (mode === 'custom' && input && !input.value.trim()) {
            input.value = this._suggestedWakeName();
        }
    }

    async _saveWakeSettings() {
        if (this._wakeSettingsLoading || !this.panel) return;
        const enabled = !!this.panel.querySelector('[data-voice-field="enabled"]')?.checked;
        const fallbackMode = this._wakeSettings?.mode === 'custom' ? 'keep' : 'hey_hermes';
        const mode = this.panel.querySelector('[data-voice-field="mode"]')?.value || fallbackMode;
        const phraseInput = this.panel.querySelector('[data-voice-field="phrase"]');
        const phrase = String(phraseInput?.value || '').trim();
        if (mode === 'custom' && !phrase) {
            this._wakeSettingsError = 'Escribe el nombre que irá después de «Hey».';
            const card = this.panel.querySelector('.os-voice-settings');
            if (card) card.dataset.voiceSettingsState = 'error';
            const status = this.panel.querySelector('.os-voice-save-status');
            if (status) status.textContent = this._wakeSettingsError;
            phraseInput?.focus();
            return;
        }
        const sensitivityInput = this.panel.querySelector('[data-voice-field="sensitivity"]');
        const parsedSensitivity = Number.parseFloat(sensitivityInput?.value ?? '0.6');
        const sensitivity = Number.isFinite(parsedSensitivity)
            ? Math.min(1, Math.max(0, parsedSensitivity))
            : 0.6;

        this._wakeSettingsLoading = true;
        this._wakeSettingsError = '';
        this._renderWakeSettings();
        try {
            const response = await fetch('/api/hermes-wake/settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    enabled,
                    mode,
                    sensitivity,
                    ...(mode === 'custom' ? { phrase } : {}),
                }),
            });
            const data = await this._responseJson(response, 'Guardar activación de Hermes');
            this._wakeSettings = mergeWakeSettingsSnapshot(this._wakeSettings, data);
            this._wakeSettingsDirty = false;
            this._wakeRuntime = { ...this._wakeRuntime, ...(data.runtime || {}) };
            window.dispatchEvent(new CustomEvent('IRIS:hermes-wake-runtime-sync', {
                detail: data.runtime || {},
            }));
            window.dispatchEvent(new CustomEvent('IRIS:hermes-wake-settings-changed', { detail: data }));
        } catch (error) {
            this._wakeSettingsError = error?.message || 'No pude guardar la activación de Hermes.';
            try {
                const statusResponse = await fetch('/api/hermes-wake/settings?probe=false');
                if (statusResponse.ok) {
                    const snapshot = await statusResponse.json();
                    this._wakeSettings = mergeWakeSettingsSnapshot(this._wakeSettings, snapshot);
                    this._wakeRuntime = { ...this._wakeRuntime, ...(snapshot.runtime || {}) };
                    window.dispatchEvent(new CustomEvent('IRIS:hermes-wake-runtime-sync', {
                        detail: snapshot.runtime || {},
                    }));
                }
            } catch {
                // Keep the last confirmed UI state if the backend itself is
                // unavailable; the error remains visible to the user.
            }
        } finally {
            this._wakeSettingsLoading = false;
            this._renderWakeSettings();
        }
    }

    _renderWakeSettings() {
        if (!this.panel) return;
        const settings = this._wakeSettings || {};
        const caps = settings.capabilities || {};
        const runtime = this._wakeRuntime || {};
        const active = !!runtime.active;
        const enabled = !!settings.enabled;
        const readiness = wakeReadinessState(settings, runtime);

        const card = this.panel.querySelector('.os-voice-settings');
        if (card) card.dataset.voiceSettingsState = this._wakeSettingsError || readiness.blocked
            ? 'error'
            : active
                ? 'active'
                : enabled && readiness.ready
                    ? 'ready'
                    : enabled
                        ? 'setup'
                        : 'off';
        const pill = this.panel.querySelector('[data-voice-settings-pill]');
        if (pill) pill.textContent = active
            ? 'LIVE'
            : readiness.blocked
                ? 'BLOCKED'
                : enabled && readiness.ready
                    ? 'READY'
                    : enabled
                        ? 'SETUP'
                        : 'OFF';

        const modeInput = this.panel.querySelector('[data-voice-field="mode"]');
        const customOption = this.panel.querySelector('[data-voice-custom-option]');
        if (customOption) {
            customOption.hidden = settings.mode !== 'custom';
            customOption.textContent = `Conservar · “${settings.phrase || 'frase personalizada'}”`;
        }
        const enabledInput = this.panel.querySelector('[data-voice-field="enabled"]');
        const phraseInput = this.panel.querySelector('[data-voice-field="phrase"]');
        const sensitivityInput = this.panel.querySelector('[data-voice-field="sensitivity"]');
        const sensitivity = Number.isFinite(Number(settings.sensitivity)) ? Number(settings.sensitivity) : 0.6;
        if (!this._wakeSettingsDirty) {
            if (enabledInput) enabledInput.checked = enabled;
            if (modeInput) modeInput.value = settings.mode === 'custom' ? 'keep' : (settings.mode || 'hey_hermes');
            if (phraseInput) {
                const currentName = settings.mode === 'custom'
                    ? this._wakeNameFromPhrase(settings.phrase || '')
                    : '';
                phraseInput.value = currentName || this._suggestedWakeName();
            }
            if (sensitivityInput) {
                sensitivityInput.value = String(Math.min(1, Math.max(0, sensitivity)));
            }
        }
        if (modeInput) modeInput.disabled = this._wakeSettingsLoading;
        if (phraseInput) {
            phraseInput.disabled = this._wakeSettingsLoading;
        }
        this._syncWakeNameEditor();
        if (sensitivityInput) {
            sensitivityInput.disabled = this._wakeSettingsLoading;
        }
        const sensitivityValue = this.panel.querySelector('[data-voice-sensitivity-value]');
        if (sensitivityValue && !this._wakeSettingsDirty) sensitivityValue.textContent = `${Math.round(sensitivity * 100)}%`;

        const engine = this.panel.querySelector('[data-voice-engine]');
        if (engine) engine.textContent = caps.installed === false
            ? 'Motor de voz no disponible'
            : `${settings.provider || 'Hermes'} · ${settings.phrase || 'hey hermes'}`;
        const engineDetail = this.panel.querySelector('[data-voice-engine-detail]');
        if (engineDetail) engineDetail.textContent = readiness.hint
            || (readiness.ready
                ? 'Motor local y micrófono listos.'
                : caps.available
                    ? 'El primer uso puede preparar el motor al activarlo; el micrófono todavía no está verificado.'
                    : 'El wake word no está disponible con la configuración actual.');

        const profile = this.panel.querySelector('.os-profile-voice');
        profile?.classList.toggle('active', active);
        profile?.setAttribute('aria-pressed', active ? 'true' : 'false');
        if (profile) {
            const label = active ? 'Desactivar escucha de IRIS' : 'Activar escucha de IRIS';
            profile.title = label;
            profile.setAttribute('aria-label', label);
        }
        const profileLabel = this.panel.querySelector('[data-voice-profile-label]');
        if (profileLabel) profileLabel.textContent = active
            ? 'IRIS escucha'
            : readiness.blocked
                ? 'Micrófono no disponible'
                : enabled && readiness.ready
                    ? 'Wake word listo'
                    : enabled
                        ? 'Wake word por preparar'
                        : 'Wake word apagado';
        const stateLabels = {
            listening: `Di “${settings.phrase || 'hey hermes'}”`,
            detected: 'Activación detectada · habla',
            recording: 'IRIS escucha tu orden…',
            processing: 'IRIS está respondiendo…',
            arming: 'Preparando motor local…',
            blocked: runtime.error || 'No se pudo abrir el micrófono',
            disconnected: 'IRIS Core desconectado',
        };
        const profileDetail = this.panel.querySelector('[data-voice-profile-detail]');
        if (profileDetail) profileDetail.textContent = active
            ? (stateLabels[runtime.state] || 'Wake word nativo activo')
            : readiness.blocked
                ? readiness.hint
                : enabled
                    ? 'Pulsa la oreja para escuchar ahora'
                    : 'El micrófono permanece cerrado';

        this.panel.querySelectorAll('.os-voice-settings [data-voice-action="toggle"]').forEach((button) => {
            button.textContent = active ? 'Desactivar ahora' : 'Activar ahora';
        });
        const saveButton = this.panel.querySelector('[data-voice-action="save"]');
        if (saveButton) saveButton.disabled = this._wakeSettingsLoading;
        const status = this.panel.querySelector('.os-voice-save-status');
        if (status) status.textContent = this._wakeSettingsError
            || (readiness.blocked ? readiness.hint : '')
            || (this._wakeSettingsLoading
                ? 'Guardando / comprobando…'
                : this._wakeSettingsDirty
                    ? 'Cambios sin guardar.'
                    : 'Escucha de IRIS · activación explícita.');
    }

    _loadOsTheme(installerPreset = '') {
        try {
            let raw = localStorage.getItem(OS_THEME_STORAGE_KEY);
            if (!raw) {
                for (const key of OS_THEME_LEGACY_STORAGE_KEYS) {
                    raw = localStorage.getItem(key);
                    if (raw) break;
                }
            }
            const requestedPreset = String(installerPreset || '').trim();
            const configuredPreset = requestedPreset === 'alice' ? 'iris' : requestedPreset;
            const storedInstallerPreset = localStorage.getItem(OS_THEME_INSTALLER_SOURCE_KEY) || '';
            const lastInstallerPreset = storedInstallerPreset === 'alice' ? 'iris' : storedInstallerPreset;
            const hasUserOverride = localStorage.getItem(OS_THEME_USER_OVERRIDE_KEY) === 'true';
            const shouldRefreshInstallerSeed = Boolean(
                raw
                && storedInstallerPreset
                && !hasUserOverride
                && configuredPreset
                && OS_THEME_PRESETS[configuredPreset]
                && configuredPreset !== lastInstallerPreset
            );
            if (shouldRefreshInstallerSeed) {
                const seeded = { ...OS_THEME_DEFAULT, ...OS_THEME_PRESETS[configuredPreset] };
                localStorage.setItem(OS_THEME_INSTALLER_SOURCE_KEY, configuredPreset);
                localStorage.setItem(OS_THEME_STORAGE_KEY, JSON.stringify(seeded));
                return seeded;
            }
            if (!raw) {
                if (configuredPreset && OS_THEME_PRESETS[configuredPreset]) {
                    const seeded = { ...OS_THEME_DEFAULT, ...OS_THEME_PRESETS[configuredPreset] };
                    localStorage.setItem(OS_THEME_INSTALLER_SOURCE_KEY, configuredPreset);
                    localStorage.setItem(OS_THEME_STORAGE_KEY, JSON.stringify(seeded));
                    return seeded;
                }
                return { ...OS_THEME_DEFAULT };
            }
            if (configuredPreset && OS_THEME_PRESETS[configuredPreset] && !storedInstallerPreset) {
                // A pre-existing OS theme is a user choice, not stale installer
                // state. Record its source without replacing it on migration.
                localStorage.setItem(OS_THEME_INSTALLER_SOURCE_KEY, configuredPreset);
            }
            const parsed = JSON.parse(raw);
            if (parsed?.profile === 'alice') parsed.profile = 'iris';
            return normalizeStoredTheme(parsed);
        } catch {
            return { ...OS_THEME_DEFAULT };
        }
    }

    _saveOsTheme() {
        try {
            localStorage.setItem(OS_THEME_STORAGE_KEY, JSON.stringify(this._osTheme));
            localStorage.setItem(OS_THEME_USER_OVERRIDE_KEY, 'true');
            return true;
        } catch {
            // Theme persistence is nice-to-have; the live UI should still update.
            return false;
        }
    }

    /** The snapshot write is the commit point: a failed write never changes live motion. */
    _commitTransitionTheme(next) {
        const theme = { ...next, ...themeTransitions(next, { strict: true }) };
        let previousOverride, markerWritten = false;
        try {
            previousOverride = localStorage.getItem(OS_THEME_USER_OVERRIDE_KEY);
            localStorage.setItem(OS_THEME_USER_OVERRIDE_KEY, 'true');
            markerWritten = true;
            localStorage.setItem(OS_THEME_STORAGE_KEY, JSON.stringify(theme));
        } catch {
            if (markerWritten) {
                try {
                    if (previousOverride === null) localStorage.removeItem(OS_THEME_USER_OVERRIDE_KEY);
                    else localStorage.setItem(OS_THEME_USER_OVERRIDE_KEY, previousOverride);
                } catch { /* The snapshot and live selection are still unchanged. */ }
            }
            return false;
        }
        this._osTheme = theme;
        this._applyOsTheme();
        this._renderThemePanel();
        return true;
    }

    _cssImageUrl(value = '', kind = '') {
        const clean = managedThemeImage(value, kind);
        if (!clean) return '';
        return `url("${clean}")`;
    }

    _themeBackgroundValue(theme = this._osTheme) {
        const custom = this._cssImageUrl(theme.backgroundUrl, 'background');
        if (custom) return custom;
        const maps = {
            city: 'url("/face/public/assets/os/iris-os-bg-v1.png")',
            sakura: 'url("/face/public/assets/os/theme-sakura.png")',
            hacker: 'url("/face/public/assets/os/theme-hacker.png")',
            classic: 'url("/face/public/assets/os/theme-classic.png")',
            nebula: 'url("/face/public/assets/os/theme-sakura.png")',
            grid: 'url("/face/public/assets/os/theme-hacker.png")',
            sunrise: 'url("/face/public/assets/os/theme-classic.png")',
        };
        return maps[theme.background] || maps.city;
    }

    _themeProfileValue(theme = this._osTheme) {
        const custom = this._cssImageUrl(theme.profileUrl, 'profile');
        if (custom) return custom;
        const maps = {
            iris: 'url("/face/public/assets/os/iris-profile-icon-v1.png")',
            alice: 'url("/face/public/assets/os/iris-profile-icon-v1.png")',
            halo: 'radial-gradient(circle at 50% 42%, rgba(255, 215, 122, 0.42), transparent 38%), radial-gradient(circle at 50% 54%, rgba(115, 247, 255, 0.32), transparent 50%), linear-gradient(135deg, #140915, #07131d)',
            glass: 'radial-gradient(circle at 36% 30%, rgba(92, 255, 173, 0.34), transparent 34%), radial-gradient(circle at 64% 70%, rgba(115, 247, 255, 0.24), transparent 42%), linear-gradient(135deg, #02110d, #05131f)',
        };
        return maps[theme.profile] || maps.iris;
    }

    _applyOsTheme(save = false) {
        if (!this.panel) return false;
        this._cancelThemeMotion?.();
        const theme = normalizeStoredTheme({ ...OS_THEME_DEFAULT, ...this._osTheme });
        this._themeRevision = (this._themeRevision || 0) + 1;
        this._osTheme = theme;
        this._themeFonts?.apply(theme);
        this.panel.style.setProperty('--iris-os-accent', theme.accent);
        this.panel.style.setProperty('--iris-os-accent-2', theme.accent2);
        this.panel.style.setProperty('--workbench-accent', theme.accent);
        this.panel.style.setProperty('--iris-os-bg-image', this._themeBackgroundValue(theme));
        this.panel.style.setProperty('--iris-profile-bg-image', this._themeProfileValue(theme));
        const cardOpacity = `${Number(theme.cardOpacity) / 100}`;
        const blurIntensity = `${Number(theme.blurIntensity)}px`;
        const neonIntensity = `${Number(theme.neonIntensity) / 100}`;
        const backgroundDim = `${Number(theme.backgroundDim) / 100}`;
        const liveIntensity = `${Number(theme.liveIntensity) / 100}`;
        const accentGlow = `${Number(theme.accentGlow) / 100}`;
        const borderGlow = `${Number(theme.borderGlow) / 100}`;
        const readyColor = theme.readyColor || theme.accent || '#5cffad';
        this.panel.style.setProperty('--iris-card-opacity', cardOpacity);
        this.panel.style.setProperty('--iris-blur-intensity', blurIntensity);
        this.panel.style.setProperty('--iris-neon-intensity', neonIntensity);
        this.panel.style.setProperty('--iris-background-dim', backgroundDim);
        this.panel.style.setProperty('--iris-live-intensity', liveIntensity);
        this.panel.style.setProperty('--iris-accent-glow', accentGlow);
        this.panel.style.setProperty('--iris-border-glow', borderGlow);
        this.panel.style.setProperty('--iris-ready-color', readyColor);
        this.panel.dataset.osBg = theme.background || 'city';
        this.panel.dataset.profileBg = theme.profile || 'iris';
        this.panel.dataset.compactMode = theme.compactMode ? 'true' : 'false';
        this.panel.dataset.animatedGlow = theme.animatedGlow ? 'true' : 'false';
        this.panel.dataset.reducedMotion = theme.reducedMotion ? 'true' : 'false';
        this.panel.dataset.transparentCards = theme.transparentCards ? 'true' : 'false';
        this.panel.dataset.desktopTransition = theme.desktopTransition || 'none';
        // Dialogs and other overlays live on <body>, outside the workbench: give them the
        // theme too (iris_dialog.css, command_palette.css, manual_voice.css).
        const root = globalThis.document?.documentElement;
        if (root?.style?.setProperty) {
            root.style.setProperty('--iris-theme-accent', theme.accent);
            root.style.setProperty('--iris-theme-accent-2', theme.accent2);
            root.style.setProperty('--iris-theme-ready', readyColor);
            if (root.dataset) root.dataset.irisReducedMotion = theme.reducedMotion ? 'true' : 'false';
        }
        publishDesktopTheme(theme);
        return save ? this._saveOsTheme() : true;
    }

    _renderThemePanel() {
        if (!this.panel) return;
        this._transitionStudio?.render();
        this._fontStudio?.render();
        const theme = { ...OS_THEME_DEFAULT, ...this._osTheme };
        this.panel.querySelectorAll('[data-os-theme-field]').forEach((input) => {
            const key = input.dataset.osThemeField;
            if (!key) return;
            if (input.type === 'checkbox') {
                input.checked = !!theme[key];
                return;
            }
            if (input.value !== String(theme[key] ?? '')) input.value = String(theme[key] ?? '');
            if (key === 'accent' || key === 'accent2') {
                input.closest('label')?.querySelector('i')?.style.setProperty('--dot', theme[key] || '');
            }
        });
        this.panel.querySelectorAll('[data-os-theme-readout]').forEach((node) => {
            const key = node.dataset.osThemeReadout;
            const value = String(theme[key] ?? '');
            if (key === 'accent' || key === 'accent2') node.textContent = value.toUpperCase();
            else if (key === 'blurIntensity') node.textContent = `${value}px`;
            else node.textContent = `${value}%`;
        });
        this.panel.querySelectorAll('[data-os-theme-preset]').forEach((btn) => {
            const preset = OS_THEME_PRESETS[btn.dataset.osThemePreset || ''];
            const active = preset
                && preset.accent === theme.accent
                && preset.accent2 === theme.accent2
                && preset.background === theme.background
                && preset.profile === theme.profile
                && !theme.backgroundUrl
                && !theme.profileUrl;
            btn.classList.toggle('active', !!active);
        });
        this.panel.querySelectorAll('[data-os-theme-background]').forEach((btn) => {
            btn.classList.toggle('active', btn.dataset.osThemeBackground === theme.background && !theme.backgroundUrl);
        });
        this.panel.querySelectorAll('[data-os-theme-profile]').forEach((btn) => {
            btn.classList.toggle('active', btn.dataset.osThemeProfile === theme.profile && !theme.profileUrl);
        });
        this.panel.querySelector('.os-theme-panel')?.classList.toggle('hidden', !this._themePanelOpen);
        this._renderSystemUpdates();
    }

    _renderDesktopStatus() {
        const root = this.panel?.querySelector('.os-top-status');
        const label = root?.querySelector('.os-status-readout');
        if (!root || !label) return;
        const status = desktopStatus({ state: this.state, runtimeChecked: this._runtimeStatusLoadedAt > 0,
            runtimeReady: this._runtimeStatus?.status === 'running' && !this._runtimeStatusError,
            wake: this._wakeRuntime, manualVoice: this._manualVoiceRuntime });
        if (label.textContent !== status.text) label.textContent = status.text;
        root.dataset.tone = status.tone;
        root.title = status.text;
    }

    _renderDesktopHome() {
        if (!this.panel) return;
        this.panel.dataset.desktopMode = this._desktopMode || 'home';
        this._renderDesktopNavigation();
        this.panel.querySelector('.os-home-welcome')?.classList.toggle('hidden', !!this._desktopWelcomeHidden);
        this.panel.querySelector('.os-home-launcher')?.classList.toggle('open', !!this._desktopLauncherOpen);
        this.panel.querySelector('[data-os-launcher="toggle"]')?.classList.toggle('active', !!this._desktopLauncherOpen);
        this._renderWindowBar();
        this._syncDesktopWidgets?.();
        const preview = this.panel.querySelector('.os-snap-preview');
        if (preview) preview.dataset.snap = this._snapPreview || '';
    }

    _renderDesktopNavigation() {
        const desktop = this._activeTab === 'desktop';
        const home = desktop && this._desktopMode !== 'dashboard';
        this.panel?.querySelectorAll('[data-workbench-tab], [data-os-desktop-mode]').forEach((button) => {
            const tab = button.dataset.workbenchTab, mode = button.dataset.osDesktopMode;
            const selected = tab ? (tab === 'desktop' ? home : tab === this._activeTab)
                : desktop && mode === (home ? 'home' : 'dashboard');
            button.classList.toggle('active', selected);
            if (selected) button.setAttribute('aria-current', 'page'); else button.removeAttribute('aria-current');
        });
    }

    /** The window bar (bottom left): the window in front and, with more open, a «+N» that lists
     * them all. One bar, so it never spreads over the dock (user QA, 2026-10-14). */
    _renderWindowBar() {
        const taskbar = this.panel?.querySelector('.os-window-taskbar');
        if (!taskbar) return;
        const z = (record) => Number(record.el?.style.zIndex || 0);
        // Most recent first: visible windows by stacking order, then minimized ones.
        const items = Array.from(this._osWindows.entries())
            .filter(([, record]) => record.el?.isConnected)
            .sort(([, a], [, b]) => (Number(!!a.minimized) - Number(!!b.minimized)) || (z(b) - z(a)));
        taskbar.classList.toggle('visible', items.length > 0);
        if (items.length < 2) this._windowListOpen = false;
        const front = items[0] && !items[0][1].minimized ? items[0][0] : '';
        const chip = ([id, record], { listed = false } = {}) => {
            const app = OS_WINDOW_APPS[id] || OS_WINDOW_APPS.activity;
            const title = this._escape(app.title);
            const state = record.minimized ? 'Minimizada' : id === front ? 'Al frente' : 'Abierta';
            return `
                <button type="button" data-window-task="${this._escape(id)}" class="${record.minimized ? 'minimized' : 'open'}"
                        title="${title} · ${state}">
                    ${osIcon(app.icon)}
                    <span>${title}</span>
                    ${listed ? `<small>${state}</small>` : ''}
                </button>
            `;
        };
        const others = items.length - 1;
        const more = others > 0 ? `
            <button type="button" class="os-window-more" data-window-more aria-expanded="${this._windowListOpen}"
                    aria-controls="os-window-list" aria-label="Ver las ${items.length} ventanas abiertas"
                    title="Ver las ${items.length} ventanas abiertas">+${others}</button>
        ` : '';
        const list = this._windowListOpen ? `
            <div class="os-window-list" id="os-window-list" role="group" aria-label="Ventanas abiertas">
                ${items.map((item) => chip(item, { listed: true })).join('')}
            </div>
        ` : '';
        const markup = `${items.length ? chip(items[0]) : ''}${more}${list}`;
        if (this._windowBar?.el !== taskbar || this._windowBar.markup !== markup) {
            taskbar.innerHTML = markup;
            this._windowBar = { el: taskbar, markup };
        }
        this._placeWindowBar();
    }

    _handleWindowBarClick(ev) {
        if (ev.target?.closest?.('[data-window-more]')) {
            this._toggleWindowList();
            return;
        }
        const btn = ev.target?.closest?.('[data-window-task]');
        if (!btn) return;
        this._windowListOpen = false;
        this._restoreWindow(btn.dataset.windowTask || '');
    }

    /** Where the window bar fits: beside the dock when there is room, otherwise just above it,
     * so it never covers a dock button (Codex W02, 2026-10-14). */
    _windowBarPlacement() {
        const taskbar = this.panel?.querySelector('.os-window-taskbar');
        const screenRect = this.panel?.querySelector('.os-home-screen')?.getBoundingClientRect();
        const dockRect = this.panel?.querySelector('.os-home-dock')?.getBoundingClientRect();
        if (!taskbar || !screenRect?.width || !dockRect?.height) return null;
        const left = parseFloat(globalThis.getComputedStyle?.(taskbar)?.left) || 16;
        const room = Math.floor(dockRect.left - screenRect.left - left - 12);
        const stacked = room < OS_WINDOW_BAR.minWidth;
        return {
            stacked,
            maxWidth: Math.floor(Math.min(OS_WINDOW_BAR.maxWidth, stacked ? screenRect.width - left * 2 : room)),
            dockReserve: Math.max(0, Math.ceil(screenRect.bottom - dockRect.top)),
            screenTop: screenRect.top,
        };
    }

    /** The desktop changed size (sidebar, panel, window): place the bar again and refit the
     * windows whenever the space really changed, even if the bar stays on the same side
     * (Codex R01, 2026-10-14). A hidden desktop (0 × 0) changes nothing. */
    _handleHomeScreenResize(entries = []) {
        const moved = this._placeWindowBar();
        const box = entries[entries.length - 1]?.contentRect;
        const size = box?.width > 0 && box?.height > 0 ? `${Math.round(box.width)}x${Math.round(box.height)}` : '';
        const resized = !!size && size !== this._homeScreenSize;
        if (size) this._homeScreenSize = size;
        // Coalesced per frame with the window resize handler.
        if (resized || moved) this._onViewportResize?.();
        this._syncDesktopWidgets?.();
    }

    /** Returns true when the bar moved between beside and above the dock. */
    _placeWindowBar() {
        const taskbar = this.panel?.querySelector('.os-window-taskbar');
        const placement = this._windowBarPlacement();
        if (!taskbar || !placement) return false;
        const moved = taskbar.classList.contains('stacked') !== placement.stacked;
        taskbar.classList.toggle('stacked', placement.stacked);
        taskbar.style.setProperty('--os-window-bar-max', `${placement.maxWidth}px`);
        taskbar.style.setProperty('--os-dock-reserve', `${placement.dockReserve}px`);
        // The list opens upward; it keeps inside the desktop above the bar.
        const barTop = taskbar.getBoundingClientRect?.().top;
        if (barTop) taskbar.style.setProperty('--os-window-list-max', `${Math.max(120, Math.floor(barTop - placement.screenTop - 20))}px`);
        return moved;
    }

    _toggleWindowList(force) {
        const open = typeof force === 'boolean' ? force : !this._windowListOpen;
        if (open === this._windowListOpen) return;
        this._windowListOpen = open && this._osWindows.size > 1;
        this._renderWindowBar();
        if (this._windowListOpen) this.panel?.querySelector('.os-window-list [data-window-task]')?.focus();
    }

    _renderSidebarState() {
        if (!this.panel) return;
        this.panel.dataset.sidebarCollapsed = this._sidebarCollapsed ? 'true' : 'false';
        const toggle = this.panel.querySelector('.os-sidebar-toggle');
        if (toggle) {
            toggle.textContent = this._sidebarCollapsed ? '››' : '‹‹';
            toggle.setAttribute('aria-label', this._sidebarCollapsed ? 'Expandir navegación' : 'Contraer navegación');
            toggle.title = this._sidebarCollapsed ? 'Expandir navegación' : 'Contraer navegación';
        }
    }

    _toggleSidebar() {
        this._sidebarCollapsed = !this._sidebarCollapsed;
        try {
            localStorage.setItem('irisSidebar.collapsed', this._sidebarCollapsed ? 'true' : 'false');
        } catch {
            // The sidebar can still toggle even if persistence is unavailable.
        }
        this._renderSidebarState();
    }

    _toggleDesktopLauncher(force) {
        this._desktopLauncherOpen = typeof force === 'boolean' ? force : !this._desktopLauncherOpen;
        if (this._desktopLauncherOpen) this._desktopMode = 'home';
        this._renderDesktopHome();
        if (this._desktopLauncherOpen) { void this._homePreferences?.load(); this._desktopHome?.drawerOpened(); }
        else this._desktopHome?.closeMenu(false);
    }

    _openOsApp(name = '') {
        const target = OS_APPS.resolve(name);
        if (target.kind === 'window') {
            this._openWindow(target.id);
            return;
        }
        this._setTab(target.kind === 'tab' ? target.id : 'desktop');
    }

    /** The launcher's buttons, from the registry (visible apps in their order). */
    _launcherButtonsHtml() {
        return OS_APPS.launcher().map((app) => `
                                <button type="button" data-os-open="${this._escapeAttr(app.id)}" data-accent="${this._escapeAttr(app.accent)}">
                                    ${osIcon(app.icon)}
                                    <strong>${this._escape(app.launcher.label || app.name)}</strong>
                                    <span data-app-brief="${this._escapeAttr(app.id)}">${this._escape(app.description)}</span>
                                </button>`).join('');
    }

    _loadWindowLayout() {
        try {
            let raw = localStorage.getItem(OS_WINDOW_STORAGE_KEY);
            if (!raw) {
                for (const key of OS_WINDOW_LEGACY_STORAGE_KEYS) {
                    raw = localStorage.getItem(key);
                    if (raw) break;
                }
            }
            const parsed = raw ? JSON.parse(raw) : {};
            return parsed && typeof parsed === 'object' ? parsed : {};
        } catch {
            return {};
        }
    }

    _saveWindowLayout() {
        try {
            localStorage.setItem(OS_WINDOW_STORAGE_KEY, JSON.stringify(this._windowLayout || {}));
        } catch {
            // Window layout persistence is optional.
        }
    }

    _windowLayoutFor(name = '', fallback = {}) {
        const saved = this._windowLayout?.[name] || {};
        return { ...fallback, ...saved };
    }

    _windowBottomClearance(layerHeight = 0) {
        const layerRect = this.panel?.querySelector('.os-window-layer')?.getBoundingClientRect();
        const dockRect = this.panel?.querySelector('.os-home-dock')?.getBoundingClientRect();
        if (layerRect && dockRect?.height > 0) {
            let top = dockRect.top;
            if (this._windowBarPlacement()?.stacked) top -= OS_WINDOW_BAR.height + OS_WINDOW_BAR.gap;
            return Math.max(92, Math.ceil(layerRect.bottom - top + 10));
        }
        return Number(layerHeight) < 760 ? 194 : 92;
    }

    _applyWindowGeometry(win, layout = {}) {
        if (!win) return;
        const layerRect = this.panel?.querySelector('.os-window-layer')?.getBoundingClientRect();
        const layerWidth = layerRect?.width || 900;
        const layerHeight = layerRect?.height || 650;
        const maxW = Math.max(160, layerWidth - 32);
        const bottomClearance = this._windowBottomClearance(layerHeight);
        const topInset = Math.min(18, Math.max(6, Math.round(layerWidth * 0.03)));
        const maxH = Math.max(140, layerHeight - bottomClearance - topInset);
        const minW = Math.min(360, maxW);
        const minH = Math.min(300, maxH);
        const width = Math.min(Math.max(Number(layout.width) || 780, minW), maxW);
        const height = Math.min(Math.max(Number(layout.height) || 560, minH), maxH);
        const left = Math.min(Math.max(Number(layout.left) || 0, 0), Math.max(0, (layerRect?.width || width) - width));
        const top = Math.min(
            Math.max(Number(layout.top) || 0, 0),
            Math.max(0, layerHeight - bottomClearance - height),
        );
        const maximized = !!layout.maximized;
        const docked = !maximized && !!layout.docked;
        win.style.left = `${left}px`;
        win.style.top = `${top}px`;
        win.style.width = `${width}px`;
        win.style.height = `${height}px`;
        win.classList.toggle('maximized', maximized);
        win.classList.toggle('docked', docked);
        if (maximized) this._applyMaximizedGeometry(win);
        if (docked) {
            const layerRect = this.panel?.querySelector('.os-window-layer')?.getBoundingClientRect();
            if (layerRect) {
                const dockAvailableW = Math.max(160, layerRect.width - 36);
                const dockTargetW = layerRect.width >= 720
                    ? Math.min(520, layerRect.width * 0.46)
                    : dockAvailableW;
                const dockMaxW = Math.max(160, Math.min(dockAvailableW, dockTargetW));
                const dockMaxH = Math.max(140, layerRect.height - this._windowBottomClearance(layerRect.height) - 18);
                const dockMinW = Math.min(360, dockMaxW);
                const dockMinH = Math.min(300, dockMaxH);
                const dockW = Math.min(Math.max(Number(layout.width) || layerRect.width * 0.36, dockMinW), dockMaxW);
                const dockH = Math.min(Math.max(Number(layout.height) || layerRect.height - 92, dockMinH), dockMaxH);
                win.style.left = `${Math.max(0, Math.round(layerRect.width - dockW - 18))}px`;
                win.style.top = '18px';
                win.style.width = `${Math.round(dockW)}px`;
                win.style.height = `${Math.round(dockH)}px`;
            }
        }
    }

    _reflowOpenWindows() {
        const bounds = this.panel?.querySelector('.os-window-layer')?.getBoundingClientRect();
        // Opening chat dispatches resize while OS is hidden. Zero is not a smaller desktop.
        if (!bounds?.width || !bounds?.height) return;
        this._osWindows.forEach((record, name) => {
            const win = record?.el;
            if (!win?.isConnected) return;
            // A window never moved has no saved geometry yet: keep where it is, not the corner.
            const current = {
                left: parseFloat(win.style.left) || 0,
                top: parseFloat(win.style.top) || 0,
                width: parseFloat(win.style.width) || undefined,
                height: parseFloat(win.style.height) || undefined,
            };
            const saved = { ...current, ...(this._windowLayout?.[name] || {}) };
            const maximized = !!record.maximized || win.classList.contains('maximized');
            const docked = !maximized && (!!record.docked || win.classList.contains('docked'));
            this._applyWindowGeometry(win, { ...saved, maximized, docked });
            this._syncWindowControlLabels(name);
        });
    }

    _syncWindowControlLabels(name = '') {
        const record = this._osWindows.get(name);
        const win = record?.el;
        const app = OS_WINDOW_APPS[name];
        if (!win || !app) return;
        const maximized = !!record.maximized || win.classList.contains('maximized');
        const docked = !!record.docked || win.classList.contains('docked');
        const maxButton = win.querySelector('[data-window-action="maximize"]');
        const dockButton = win.querySelector('[data-window-action="dock"]');
        if (maxButton) {
            const label = maximized ? `Restaurar ${app.title}` : `Maximizar ${app.title}`;
            maxButton.setAttribute('aria-label', label);
            maxButton.title = label;
            maxButton.setAttribute('aria-pressed', maximized ? 'true' : 'false');
        }
        if (dockButton) {
            const label = docked ? `Desanclar ${app.title}` : `Anclar ${app.title}`;
            dockButton.setAttribute('aria-label', label);
            dockButton.title = label;
            dockButton.setAttribute('aria-pressed', docked ? 'true' : 'false');
        }
    }

    _captureWindowLayout(name = '') {
        const record = this._osWindows.get(name);
        const win = record?.el;
        if (!win || win.classList.contains('maximized')) return;
        // A minimized window measures 0 × 0: closing it must not erase the size it had.
        if (record.minimized || win.classList.contains('minimized') || win.offsetWidth === 0) {
            this._windowLayout[name] = {
                ...(this._windowLayout[name] || {}), minimized: !!record.minimized, docked: !!record.docked,
            };
            this._saveWindowLayout();
            return;
        }
        this._windowLayout[name] = {
            ...(this._windowLayout[name] || {}),
            left: Math.round(parseFloat(win.style.left) || 0),
            top: Math.round(parseFloat(win.style.top) || 0),
            width: Math.round(win.offsetWidth || 0),
            height: Math.round(win.offsetHeight || 0),
            minimized: !!record.minimized,
            maximized: false,
            docked: !!record.docked,
        };
        this._saveWindowLayout();
    }

    _applyMaximizedGeometry(win) {
        const layerRect = this.panel?.querySelector('.os-window-layer')?.getBoundingClientRect();
        if (!win || !layerRect) return;
        const inset = Math.min(18, Math.max(6, Math.round(layerRect.width * 0.03)));
        win.style.left = `${inset}px`;
        win.style.top = `${inset}px`;
        win.style.width = `${Math.max(120, layerRect.width - inset * 2)}px`;
        win.style.height = `${Math.max(120, layerRect.height - inset - this._windowBottomClearance(layerRect.height))}px`;
    }

    _applySnapGeometry(win, zone = '') {
        const layerRect = this.panel?.querySelector('.os-window-layer')?.getBoundingClientRect();
        if (!win || !layerRect) return;
        const gap = 18;
        const taskbarSpace = this._windowBottomClearance(layerRect.height);
        const fullW = Math.max(120, layerRect.width - gap * 2);
        const fullH = Math.max(120, layerRect.height - gap - taskbarSpace);
        const halfW = Math.max(120, (layerRect.width - gap * 3) / 2);
        const halfH = Math.max(120, (layerRect.height - gap * 2 - taskbarSpace) / 2);
        const apply = (left, top, width, height) => {
            win.style.left = `${Math.round(left)}px`;
            win.style.top = `${Math.round(top)}px`;
            win.style.width = `${Math.round(width)}px`;
            win.style.height = `${Math.round(height)}px`;
        };
        if (zone === 'left') apply(gap, gap, halfW, fullH);
        else if (zone === 'right') apply(layerRect.width - halfW - gap, gap, halfW, fullH);
        else if (zone === 'top') apply(gap, gap, fullW, fullH);
        else if (zone === 'top-left') apply(gap, gap, halfW, halfH);
        else if (zone === 'top-right') apply(layerRect.width - halfW - gap, gap, halfW, halfH);
        else if (zone === 'bottom-left') apply(gap, gap + halfH + gap, halfW, halfH);
        else if (zone === 'bottom-right') apply(layerRect.width - halfW - gap, gap + halfH + gap, halfW, halfH);
    }

    _snapZoneFromPointer(ev) {
        const layerRect = this.panel?.querySelector('.os-window-layer')?.getBoundingClientRect();
        if (!layerRect) return '';
        const edge = 34;
        const x = ev.clientX - layerRect.left;
        const y = ev.clientY - layerRect.top;
        const nearLeft = x <= edge;
        const nearRight = x >= layerRect.width - edge;
        const nearTop = y <= edge;
        const nearBottom = y >= layerRect.height - 120;
        if (nearTop && nearLeft) return 'top-left';
        if (nearTop && nearRight) return 'top-right';
        if (nearBottom && nearLeft) return 'bottom-left';
        if (nearBottom && nearRight) return 'bottom-right';
        if (nearTop) return 'top';
        if (nearLeft) return 'left';
        if (nearRight) return 'right';
        return '';
    }

    _openWindow(name = '', { preserveMinimized = false } = {}) {
        const app = OS_WINDOW_APPS[name];
        if (!app || !this.panel) return;
        const returnFocus = document.activeElement;
        this._setDesktopMode('home', { animate: false });
        this._desktopLauncherOpen = false;
        const existing = this._osWindows.get(name);
        if (existing?.el?.isConnected) {
            existing.minimized = false;
            existing.el.classList.remove('minimized');
            this._windowLayout[name] = {
                ...(this._windowLayout[name] || {}),
                minimized: false,
                open: true,
            };
            this._saveWindowLayout();
            this._focusWindow(name, { moveFocus: true });
            if (this._open && globalThis.document?.visibilityState !== 'hidden') this._transitionPlayer?.playTheme(existing.el, this._osTheme);
            this._renderDesktopHome();
            this._loadWindowApp(name);
            this._syncAppPolling();
            return;
        }

        const layer = this.panel.querySelector('.os-window-layer');
        const view = this.panel.querySelector(`[data-workbench-view="${name}"]`);
        if (!layer || !view) {
            console.warn(`[IRIS OS] No se pudo abrir ${name}: superficie o capa de ventanas ausente.`);
            return;
        }

        const parent = view.parentElement;
        if (parent && !this._windowViewAnchors.has(name)) {
            this._windowViewAnchors.set(name, { parent, next: view.nextSibling });
        }

        const count = this._osWindows.size;
        const fallback = {
            left: 42 + (count % 4) * 34,
            top: 36 + (count % 3) * 28,
            width: app.window.width,
            height: app.window.height,
        };
        const layout = {
            ...this._windowLayoutFor(name, fallback),
            minimized: preserveMinimized && !!this._windowLayout?.[name]?.minimized,
        };
        const win = el('section', `os-window os-window-${name}`);
        const titleId = `os-window-title-${name}`;
        win.dataset.windowId = name;
        win.dataset.accent = app.accent || 'cyan';
        win.tabIndex = -1;
        win.setAttribute('role', 'dialog');
        win.setAttribute('aria-modal', 'false');
        win.setAttribute('aria-labelledby', titleId);
        win.style.zIndex = String(++this._windowZ);
        win.innerHTML = `
            <header class="os-window-titlebar" data-window-drag>
                <div>${osIcon(app.icon)}<strong id="${titleId}">${this._escape(app.title)}</strong></div>
                <nav aria-label="Controles de ${this._escapeAttr(app.title)}">
                    <button type="button" data-window-action="minimize" aria-label="Minimizar ${this._escapeAttr(app.title)}">_</button>
                    <button type="button" data-window-action="dock" aria-label="Anclar ${this._escapeAttr(app.title)}">|</button>
                    <button type="button" data-window-action="maximize" aria-label="Maximizar ${this._escapeAttr(app.title)}">□</button>
                    <button type="button" data-window-action="close" aria-label="Cerrar ${this._escapeAttr(app.title)}">x</button>
                </nav>
            </header>
            <div class="os-window-body"></div>
            <span class="os-window-resize" data-window-resize="se" aria-hidden="true"></span>
            ${OS_WINDOW_EDGES.map(edge => `<span class="os-window-edge os-window-edge-${edge}" data-window-resize="${edge}" aria-hidden="true"></span>`).join('')}
        `;
        this._applyWindowGeometry(win, layout);
        win.querySelector('.os-window-body')?.appendChild(view);
        view.classList.add('os-window-mounted', 'active');
        layer.appendChild(win);
        this._osWindows.set(name, {
            el: win,
            view,
            returnFocus,
            minimized: !!layout.minimized,
            maximized: !!layout.maximized,
            docked: !!layout.docked,
        });
        if (layout.maximized && layout.docked) {
            const record = this._osWindows.get(name);
            record.docked = false;
            this._windowLayout[name] = { ...layout, docked: false };
            this._saveWindowLayout();
        }
        win.classList.toggle('minimized', !!layout.minimized);
        if (!layout.minimized && this._open && globalThis.document?.visibilityState !== 'hidden') this._transitionPlayer?.playTheme(win, this._osTheme);
        this._syncWindowControlLabels(name);
        this._focusWindow(name, { moveFocus: true });
        this._renderDesktopHome();
        this._loadWindowApp(name);
        this._syncAppPolling();
        console.debug(`[IRIS OS] Ventana abierta: ${name}`);
    }

    _closeWindow(name = '') {
        const record = this._osWindows.get(name);
        if (!record) return;
        // F03: closing the window ends its own use of the camera. Vision
        // Assistant holds its own consumer, so the camera stays on only while
        // something else still uses it; minimizing keeps the view's consumer.
        if (name === 'camera') this._stopCameraPreview({ release: true });
        const { el: win, view } = record;
        this._transitionPlayer?.cancel(win);
        this._captureWindowLayout(name);
        if (this._windowLayout[name]) this._windowLayout[name].open = false;
        this._saveWindowLayout();
        const anchor = this._windowViewAnchors.get(name);
        view?.classList.remove('os-window-mounted', 'active');
        if (anchor?.parent?.isConnected) {
            if (anchor.next?.isConnected && anchor.next.parentElement === anchor.parent) anchor.parent.insertBefore(view, anchor.next);
            else anchor.parent.appendChild(view);
        }
        win?.remove();
        this._osWindows.delete(name);
        this._restoreDesktopFocus(record.returnFocus);
        this._renderDesktopHome();
        this._syncAppPolling();
    }

    _minimizeWindow(name = '') {
        const record = this._osWindows.get(name);
        if (!record?.el) return;
        this._transitionPlayer?.cancel(record.el);
        if (name === 'camera') this._stopCameraPreview();
        this._captureWindowLayout(name);
        record.minimized = true;
        record.el.classList.add('minimized');
        record.el.classList.remove('focused');
        this._windowLayout[name] = { ...(this._windowLayout[name] || {}), minimized: true, open: true };
        this._saveWindowLayout();
        this._restoreDesktopFocus(record.returnFocus);
        this._renderDesktopHome();
        this._syncAppPolling();
    }

    _restoreWindow(name = '') {
        const record = this._osWindows.get(name);
        if (!record?.el) return;
        this._activeTab = 'desktop';
        this._desktopMode = 'home';
        this._desktopLauncherOpen = false;
        if (this.panel) this.panel.dataset.activeTab = 'desktop';
        this.panel?.querySelectorAll('[data-workbench-tab]').forEach((btn) => {
            const selected = btn.dataset.workbenchTab === 'desktop';
            btn.classList.toggle('active', selected);
            if (selected) btn.setAttribute('aria-current', 'page');
            else btn.removeAttribute('aria-current');
        });
        this.panel?.querySelectorAll('[data-workbench-view]').forEach((view) => {
            view.classList.toggle('active', view.dataset.workbenchView === 'desktop');
        });
        record.minimized = false;
        record.el.classList.remove('minimized');
        if (this._open && globalThis.document?.visibilityState !== 'hidden') this._transitionPlayer?.playTheme(record.el, this._osTheme);
        if (record.el.classList.contains('docked')) {
            this._applyWindowGeometry(record.el, { ...(this._windowLayout[name] || {}), docked: true, maximized: false });
        } else if (record.el.classList.contains('maximized')) {
            this._applyMaximizedGeometry(record.el);
        }
        this._windowLayout[name] = { ...(this._windowLayout[name] || {}), minimized: false, open: true };
        this._saveWindowLayout();
        this._focusWindow(name, { moveFocus: true });
        this._renderDesktopHome();
        this._loadWindowApp(name);
        this._syncAppPolling();
    }

    _toggleMaximizeWindow(name = '') {
        const record = this._osWindows.get(name);
        const win = record?.el;
        if (!win) return;
        if (win.classList.contains('docked')) {
            win.classList.remove('docked');
            record.docked = false;
            this._windowLayout[name] = { ...(this._windowLayout[name] || {}), docked: false };
            this._applyWindowGeometry(win, { ...(this._windowLayout[name] || {}), docked: false, maximized: false });
        }
        if (win.classList.contains('maximized')) {
            win.classList.remove('maximized');
            record.maximized = false;
            this._applyWindowGeometry(win, { ...(this._windowLayout[name] || {}), maximized: false });
            this._windowLayout[name] = { ...(this._windowLayout[name] || {}), maximized: false, open: true };
        } else {
            this._captureWindowLayout(name);
            win.classList.add('maximized');
            record.maximized = true;
            this._applyMaximizedGeometry(win);
            this._windowLayout[name] = { ...(this._windowLayout[name] || {}), maximized: true, open: true };
        }
        this._saveWindowLayout();
        this._syncWindowControlLabels(name);
        this._focusWindow(name);
        this._renderDesktopHome();
    }

    _toggleDockWindow(name = '') {
        const record = this._osWindows.get(name);
        const win = record?.el;
        if (!win) return;
        if (win.classList.contains('docked')) {
            win.classList.remove('docked');
            record.docked = false;
            this._applyWindowGeometry(win, { ...(this._windowLayout[name] || {}), docked: false, maximized: false });
            this._windowLayout[name] = { ...(this._windowLayout[name] || {}), docked: false, open: true };
        } else {
            this._captureWindowLayout(name);
            win.classList.remove('maximized');
            win.classList.add('docked');
            record.docked = true;
            record.maximized = false;
            this._applyWindowGeometry(win, {
                ...(this._windowLayout[name] || {}),
                docked: true,
                maximized: false,
            });
            this._windowLayout[name] = { ...(this._windowLayout[name] || {}), docked: true, maximized: false, open: true };
        }
        this._saveWindowLayout();
        this._syncWindowControlLabels(name);
        this._focusWindow(name);
        this._renderDesktopHome();
    }

    _focusWindow(name = '', { moveFocus = false } = {}) {
        const record = this._osWindows.get(name);
        if (!record?.el) return;
        const ordered = Array.from(this._osWindows.entries())
            .filter(([, item]) => item.el?.isConnected)
            .sort((a, b) => Number(a[1].el.style.zIndex || 0) - Number(b[1].el.style.zIndex || 0));
        let nextZ = 20;
        ordered.forEach(([id, item]) => {
            item.el?.classList.remove('focused');
            if (id === name) return;
            item.el.style.zIndex = String(++nextZ);
            this._windowLayout[id] = { ...(this._windowLayout[id] || {}), z: nextZ, open: true };
        });
        record.el.classList.add('focused');
        record.el.style.zIndex = String(++nextZ);
        this._windowZ = nextZ;
        this._windowLayout[name] = { ...(this._windowLayout[name] || {}), z: nextZ, open: true };
        this._saveWindowLayout();
        this._renderWindowBar();
        if (moveFocus) record.el.focus({ preventScroll: true });
    }

    _restoreDesktopFocus(preferred = null) {
        const hiddenAncestor = preferred?.closest?.('.os-home-launcher:not(.open), [aria-hidden="true"], .hidden');
        const candidate = preferred?.isConnected && preferred.getClientRects?.().length && !hiddenAncestor
            ? preferred
            : this.panel?.querySelector('[data-os-launcher="toggle"]');
        candidate?.focus?.({ preventScroll: true });
    }

    _handleWorkbenchKeydown(ev) {
        if (ev.altKey && !ev.ctrlKey && !ev.metaKey && !ev.isComposing && ev.code === 'KeyA'
            && !ev.target?.closest?.('input, textarea, [contenteditable="true"]')) {
            ev.preventDefault(); this._setTab('desktop'); this._toggleDesktopLauncher(true); return;
        }
        if (ev.key !== 'Escape' || ev.defaultPrevented || ev.isComposing) return;
        if (this._windowListOpen) {
            this._toggleWindowList(false);
            this.panel?.querySelector('[data-window-more]')?.focus();
            ev.preventDefault();
            return;
        }
        if (this._desktopLauncherOpen) {
            this._toggleDesktopLauncher(false);
            this._restoreDesktopFocus();
            ev.preventDefault();
            return;
        }
        const win = ev.target?.closest?.('.os-window') || this.panel?.querySelector('.os-window.focused');
        const name = win?.dataset?.windowId || '';
        if (!name) return;
        this._closeWindow(name);
        ev.preventDefault();
    }

    _handleWindowClick(ev) {
        const win = ev.target?.closest?.('.os-window');
        if (!win) return;
        this._cancelThemeMotion?.();
        const id = win.dataset.windowId || '';
        this._focusWindow(id);
        const action = ev.target?.closest?.('[data-window-action]')?.dataset?.windowAction;
        if (action === 'close') this._closeWindow(id);
        if (action === 'minimize') this._minimizeWindow(id);
        if (action === 'dock') this._toggleDockWindow(id);
        if (action === 'maximize') this._toggleMaximizeWindow(id);
    }

    _handleWindowPointerDown(ev) {
        const resizeHandle = ev.target?.closest?.('[data-window-resize]');
        const titlebar = ev.target?.closest?.('[data-window-drag]');
        const win = ev.target?.closest?.('.os-window');
        if (!win) return;
        const id = win.dataset.windowId || '';
        this._focusWindow(id);
        if (resizeHandle && ev.button === 0 && !win.classList.contains('maximized')) {
            // QA12: every edge and corner resizes; the visible grip is the 'se' corner.
            const edge = resizeHandle.dataset.windowResize || 'se';
            const rect = win.getBoundingClientRect();
            const layerRect = this.panel?.querySelector('.os-window-layer')?.getBoundingClientRect();
            const docked = win.classList.contains('docked');
            const startLeft = rect.left - (layerRect?.left || 0);
            const startTop = rect.top - (layerRect?.top || 0);
            // A west or north edge can grow up to the layer's start; the others, to its end.
            const availableW = docked
                ? Math.max(160, (layerRect?.width || rect.width) - 36)
                : Math.max(160, edge.includes('w') ? startLeft + rect.width : (layerRect?.right || ev.clientX) - rect.left);
            const availableH = Math.max(
                140,
                edge.includes('n') ? startTop + rect.height
                    : (layerRect?.bottom || ev.clientY) - rect.top - this._windowBottomClearance(layerRect?.height || 0),
            );
            this._dragWindow = {
                id,
                mode: docked ? 'resize-docked' : 'resize',
                edge,
                pointerId: ev.pointerId,
                startX: ev.clientX,
                startY: ev.clientY,
                startLeft,
                startTop,
                startW: rect.width,
                startH: rect.height,
                minW: Math.min(360, availableW),
                minH: Math.min(300, availableH),
                maxW: availableW,
                maxH: availableH,
            };
            win.setPointerCapture?.(ev.pointerId);
            ev.preventDefault();
            return;
        }
        if (ev.target?.closest?.('[data-window-action]')) return;
        if (!titlebar || ev.button !== 0) return;
        if (win.classList.contains('maximized') || win.classList.contains('docked')) return;
        const rect = win.getBoundingClientRect();
        const layerRect = this.panel?.querySelector('.os-window-layer')?.getBoundingClientRect();
        this._dragWindow = {
            id,
            mode: 'move',
            pointerId: ev.pointerId,
            offsetX: ev.clientX - rect.left,
            offsetY: ev.clientY - rect.top,
            maxX: Math.max(0, (layerRect?.width || 0) - rect.width),
            maxY: Math.max(0, (layerRect?.height || 0) - rect.height),
            // Where the window was before the drag: «Restaurar» after a snap to the top returns here.
            origin: {
                left: Math.round(parseFloat(win.style.left) || 0),
                top: Math.round(parseFloat(win.style.top) || 0),
                width: Math.round(rect.width),
                height: Math.round(rect.height),
            },
        };
        win.setPointerCapture?.(ev.pointerId);
        ev.preventDefault();
    }

    _handleWindowPointerMove(ev) {
        if (!this._dragWindow || ev.pointerId !== this._dragWindow.pointerId) return;
        const record = this._osWindows.get(this._dragWindow.id);
        const layerRect = this.panel?.querySelector('.os-window-layer')?.getBoundingClientRect();
        if (!record?.el || !layerRect) return;
        if (this._dragWindow.mode === 'resize') {
            const { edge, startLeft, startTop } = this._dragWindow;
            const dx = ev.clientX - this._dragWindow.startX;
            const dy = ev.clientY - this._dragWindow.startY;
            // A west/north edge moves the window's start and keeps its far side in place.
            if (/[ew]/.test(edge)) {
                const width = Math.min(Math.max(this._dragWindow.minW, this._dragWindow.startW + (edge.includes('w') ? -dx : dx)), this._dragWindow.maxW);
                record.el.style.width = `${width}px`;
                if (edge.includes('w')) record.el.style.left = `${Math.round(startLeft + this._dragWindow.startW - width)}px`;
            }
            if (/[ns]/.test(edge)) {
                const height = Math.min(Math.max(this._dragWindow.minH, this._dragWindow.startH + (edge.includes('n') ? -dy : dy)), this._dragWindow.maxH);
                record.el.style.height = `${height}px`;
                if (edge.includes('n')) record.el.style.top = `${Math.round(startTop + this._dragWindow.startH - height)}px`;
            }
            return;
        }
        if (this._dragWindow.mode === 'resize-docked') {
            const rightGap = 18;
            // Docked to the right: the west side (and the grip, drawn bottom-left there) sets the width.
            const { edge } = this._dragWindow;
            const width = edge.includes('w') || edge === 'se'
                ? Math.min(Math.max(this._dragWindow.minW, this._dragWindow.startW - (ev.clientX - this._dragWindow.startX)), this._dragWindow.maxW)
                : this._dragWindow.startW;
            const height = edge.includes('s')
                ? Math.min(Math.max(this._dragWindow.minH, this._dragWindow.startH + (ev.clientY - this._dragWindow.startY)), this._dragWindow.maxH)
                : this._dragWindow.startH;
            record.el.style.width = `${width}px`;
            record.el.style.height = `${height}px`;
            record.el.style.left = `${Math.max(18, Math.round(layerRect.width - width - rightGap))}px`;
            return;
        }
        const snap = this._snapZoneFromPointer(ev);
        if (snap !== this._snapPreview) {
            this._snapPreview = snap;
            this._renderDesktopHome();
        }
        const x = Math.min(Math.max(0, ev.clientX - layerRect.left - this._dragWindow.offsetX), this._dragWindow.maxX);
        const y = Math.min(Math.max(0, ev.clientY - layerRect.top - this._dragWindow.offsetY), this._dragWindow.maxY);
        record.el.style.left = `${x}px`;
        record.el.style.top = `${y}px`;
    }

    _finishWindowDrag() {
        if (!this._dragWindow && !this._snapPreview) return;
        if (this._dragWindow?.id) {
            const id = this._dragWindow.id;
            const record = this._osWindows.get(id);
            if (record?.el && this._dragWindow.mode === 'move' && this._snapPreview === 'top') {
                // Snapping to the top IS maximizing (user QA, 2026-10-19): it used to fill the
                // desktop without the state, so □ then changed nothing in either direction.
                record.el.classList.remove('docked');
                record.docked = false;
                record.el.classList.add('maximized');
                record.maximized = true;
                this._applyMaximizedGeometry(record.el);
                this._windowLayout[id] = {
                    ...(this._windowLayout[id] || {}), ...(this._dragWindow.origin || {}),
                    docked: false, maximized: true, open: true,
                };
                this._saveWindowLayout();
                this._syncWindowControlLabels(id);
            } else {
                if (record?.el && this._dragWindow.mode === 'move' && this._snapPreview) {
                    record.el.classList.remove('maximized', 'docked');
                    record.maximized = false;
                    record.docked = false;
                    this._applySnapGeometry(record.el, this._snapPreview);
                    this._syncWindowControlLabels(id);
                }
                this._captureWindowLayout(id);
            }
        }
        this._snapPreview = '';
        this._dragWindow = null;
        this._renderDesktopHome();
    }

    _loadWindowApp(name = '') {
        if (!this._hasVisibleAppSurface(name)) return;
        this._loadApp(name, { opening: true });
    }

    /**
     * F09.1 adapters: how each app built into the shell loads its data, keyed by app ID. A new
     * app brings its own module instead (F09.2); the registry, not this table, says which apps
     * exist. `opening` is a window being opened or restored; `force` skips caches.
     */
    _createAppAdapters() {
        const reload = (load) => ({ load: ({ force }) => load(force) });
        return {
            activity: {}, // fed live by Hermes events
            agents: reload((force) => this.loadAsyncAgents(force)),
            missions: reload((force) => this.loadMissions(force)),
            routines: reload((force) => this.loadRoutines(force)),
            doctor: {
                load: ({ force, opening }) => {
                    this.loadGatewayStatus(force);
                    if (opening) this.loadHermesDoctor();
                },
            },
            tools: reload((force) => this.loadMcpCatalog(force)),
            skills: reload((force) => this.loadSkills(force)),
            marketplace: reload((force) => this.loadMarketplace(force)),
            notifications: { load: ({ force, opening }) => this._loadNotificationsTab(opening || force) },
            permissions: reload((force) => this._capabilityApp?.load?.(force)),
            player: { load: () => this._playerApp?.load?.() },
            continuity: reload((force) => this._continuityApp?.load?.(force)),
            models: { load: ({ force, opening }) => this._loadModelsTab(opening || force) },
            theme: {}, // reads the saved theme when it renders
            camera: reload((force) => this.loadVisionAssistant(force)),
            settings: {
                load: () => {
                    void this._checkSystemUpdates(false, { resumeOnly: true });
                    this._loadWakeSettings();
                    void this._renderSettings?.load();
                    this._loadCompanionSettings();
                    this._loadCompanionLifecycle();
                },
            },
        };
    }

    /** Views of the apps that have their own module: an empty root each, filled when mounted. */
    _moduleViewsHtml() {
        return OS_APPS.list().filter((app) => OS_APPS.hasOwnCode(app.id)).map((app) => `
                <div class="workbench-view os-app-view" data-workbench-view="${this._escapeAttr(app.id)}" data-app-view="${this._escapeAttr(app.id)}"></div>`).join('');
    }

    /** What an app with its own module may use from the shell: never the shell itself. */
    _appContext(id) {
        return {
            id,
            ...(id === 'app-manager' ? { desktopHome: this._homePreferences, appRegistry: OS_APPS, installedApps: INSTALLED,
                appNotices: appNoticeModes(), askIrisTrust } : {}),
            // F17: the Store sees the installed apps, draws the shell's icons and takes its changes
            // into the running desktop (a page reload stays only as the fallback).
            ...(id === 'store' ? { installedApps: INSTALLED, icon: (name) => osIconSvg(name), reload: () => this._reloadDesktop('store'),
                refreshApps: () => this.refreshInstalledApps(), appInUse: (target) => this._appInUse(target) } : {}),
            app: OS_APPS.get(id),
            isVisible: () => this._hasVisibleAppSurface(id),
            // Like a widget's: data for the target's navigate(), delivered once its module loaded.
            // A copy, so plain data only; the target app validates what it receives.
            openApp: (target, payload) => {
                const data = payload === undefined ? undefined : structuredClone(payload);
                this._openOsApp(target);
                if (data !== undefined) this._navigateApp(target, data);
            },
            setBrief: (text) => this.panel?.querySelectorAll(`[data-app-brief="${id}"]`).forEach((node) => {
                node.textContent = String(text ?? '');
            }),
            // F10.4: a yes/no question in Iris' own dialog, for an effect the app is about to cause.
            confirm: (options) => this._appConfirm(id, options),
            // «Avisar a Iris»: after a real click, shown in Iris' own dialog first.
            askIris: (options) => this._appAskIris(id, options),
        };
    }

    /**
     * F17: take the apps Iris Store installed, updated or removed into the running desktop, with
     * no page reload. New apps get their view and appear in the drawer and the widget gallery;
     * an updated app restarts alone (its window reopens with the new version, its widgets
     * remount); a removed app closes its windows and loses its views, styles, widgets and
     * shortcuts. If Core cannot be read, nothing changes and the Store offers «Reintentar».
     */
    async refreshInstalledApps() {
        let result;
        try {
            result = await reloadInstalledApps();
        } catch (error) {
            console.warn('[IRIS OS] No se pudo actualizar el catálogo de apps:', error);
            return { ok: false, error: error?.message || 'No se pudo actualizar el escritorio.' };
        }
        refreshAppCatalog();
        const { added, removed, updated } = result.changes;
        const reopen = updated.filter((id) => this._osWindows.has(id));
        for (const id of removed) this._retireInstalledApp(id, { remove: true });
        for (const id of updated) this._retireInstalledApp(id, { remove: false });
        for (const id of added) this._ensureAppView(id);
        // Widget stylesheets of every changed app go, mounted now or not: a reinstall or update
        // must never keep the previous version's styles (Codex L01).
        this._widgets?.purgeAppStyles?.([...removed, ...updated, ...added]);
        this._widgets?.restoreApps?.(added);
        this._homePreferences?.restoreApps?.(added);
        // Core already dropped the widgets and shortcuts of a removed app. They leave here at once,
        // even during a save (an older answer is filtered); Core is read again in the background
        // once nothing is pending, so the Store never waits on another request (Codex L02).
        if (removed.length) {
            for (const sync of [this._widgets?.forgetApps?.(removed), this._homePreferences?.forgetApps?.(removed)]) {
                Promise.resolve(sync).then((read) => {
                    if (read === false) console.warn('[IRIS OS] Se quitaron las apps; el escritorio se releerá en el próximo cambio.');
                }, (error) => console.warn('[IRIS OS] No se pudo releer el escritorio tras desinstalar:', error));
            }
        }
        for (const id of updated) this._widgets?.remountApp?.(id);
        this._widgets?.refreshCatalog?.();
        this._desktopHome?.render?.();
        this._renderDesktopHome();
        for (const id of reopen) this._loadApp(id, { opening: true });
        this._appHost?.get('app-manager')?.refresh?.();
        return { ok: true, added, removed, updated };
    }

    /** F17: whether an installed app is running now: a window, a loaded instance or a widget. */
    _appInUse(id) {
        return this._osWindows.has(id) || this._appHost?.state(id) === 'ready'
            || !!this._widgets?.layout?.widgets?.some((item) => item.widget_id.startsWith(`${id}.`));
    }

    /** Retire the running instance of an installed app; `remove` also takes away its window and view. */
    _retireInstalledApp(id, { remove }) {
        if (remove) this._closeWindow(id);
        else this._refreshWindowMetadata?.(id);
        this._appHost?.dispose(id);
        const view = this.panel?.querySelector(`[data-app-view="${id}"]`);
        view?.replaceChildren?.();
        if (remove) {
            view?.remove();
            this._windowViewAnchors.delete(id);
            if (this._windowLayout?.[id]) { delete this._windowLayout[id]; this._saveWindowLayout(); }
        }
        // The new version brings its own stylesheet; the removed app takes its own away.
        const style = this._appStyles?.get(id);
        if (style) {
            if (typeof CSSStyleSheet === 'function' && style instanceof CSSStyleSheet) {
                document.adoptedStyleSheets = document.adoptedStyleSheets.filter((sheet) => sheet !== style);
            } else style.remove?.();
            this._appStyles.delete(id);
        }
    }

    _refreshWindowMetadata(id) {
        const win = this._osWindows.get(id)?.el, app = OS_WINDOW_APPS[id];
        if (!win || !app) return;
        win.dataset.accent = app.accent || 'cyan';
        const title = win.querySelector('.os-window-titlebar strong');
        if (title) {
            title.textContent = app.title;
            const icon = title.parentElement?.querySelector('svg');
            if (icon) { const holder = document.createElement('span'); holder.innerHTML = osIcon(app.icon); icon.replaceWith(holder.firstElementChild); }
        }
        win.querySelector('.os-window-titlebar nav')?.setAttribute('aria-label', `Controles de ${app.title}`);
        for (const button of win.querySelectorAll('[data-window-action]')) {
            const verb = button.getAttribute('aria-label')?.split(' ')[0];
            if (verb) button.setAttribute('aria-label', `${verb} ${app.title}`);
        }
    }

    /** The empty view an installed app mounts in, beside the desktop's other views. */
    _ensureAppView(id) {
        if (!this.panel || !OS_APPS.hasOwnCode(id) || this.panel.querySelector(`[data-app-view="${id}"]`)) return;
        const container = this.panel.querySelector('[data-workbench-view="desktop"]')?.parentElement;
        if (!container) return;
        const view = document.createElement('div');
        view.className = 'workbench-view os-app-view';
        view.dataset.workbenchView = id;
        view.dataset.appView = id;
        container.append(view);
    }

    /** F17 fallback: reload the page and come back to the desktop and `appId`. */
    _reloadDesktop(appId) {
        try { globalThis.sessionStorage?.setItem(OS_RELOAD_KEY, appId); } catch { /* reloads into the room instead */ }
        globalThis.location?.reload();
    }

    /** Called once by app.js at start: reopen the desktop (and the app) a Store reload left. */
    resumeAfterReload() {
        let target = '';
        try {
            target = globalThis.sessionStorage?.getItem(OS_RELOAD_KEY) || '';
            globalThis.sessionStorage?.removeItem(OS_RELOAD_KEY);
        } catch { return false; }
        if (!target) return false;
        this.open();
        if (OS_APPS.get(target)) this._openOsApp(target);
        return true;
    }

    /** An app's question in the shared dialog: only texts and a tone pass, rendered as text. */
    _appConfirm(id, options = {}) {
        const text = (value, limit) => (typeof value === 'string' ? value.slice(0, limit) : '');
        return irisConfirm({
            title: text(options.title, 120) || OS_APPS.get(id)?.name || 'IRIS OS',
            message: text(options.message, 800),
            question: text(options.question, 200),
            confirmLabel: text(options.confirmLabel, 40) || 'Aceptar',
            cancelLabel: text(options.cancelLabel, 40) || 'Cancelar',
            tone: options.tone === 'danger' ? 'danger' : 'default',
        });
    }

    /**
     * «Avisar a Iris» from an app's window or widget. Only right after a real click by the person;
     * Iris' dialog shows the app, the line and the data before sending, unless the person chose to
     * trust that app. Goes to the current conversation as the app's information (Core frames it).
     * Answers `{ sent }` once Core took it, or `reason` («cancelled», «refused» with Core's
     * `message`, «offline») when it was not sent.
     */
    async _appAskIris(id, options = {}) {
        const request = askIrisRequest(options);
        const app = OS_APPS.get(id);
        if (!app) throw new Error('La app no está disponible.');
        // One message per action of the person in this app, even when it is trusted (Codex SDK02).
        if (!this._takeAppGesture(id)) throw new Error('«Avisar a Iris» necesita un clic de la persona en la app.');
        if (!askIrisTrust.has(id)) {
            const choice = await irisChoice({
                title: 'Avisar a Iris', ...askIrisPreview(app.name, request), question: '¿Enviarlo?',
                choices: [{ value: 'once', label: 'Enviar' }, { value: 'always', label: `Enviar siempre desde ${app.name}` }],
            });
            if (choice !== 'once' && choice !== 'always') return { sent: false, reason: 'cancelled' };
            if (choice === 'always') askIrisTrust.set(id, true);
        }
        return (await this.onAskIris?.(id, app.name, request)) ?? { sent: false, reason: 'offline' };
    }

    /**
     * «Avisar a Iris» needs the person's own action in that app (Codex SDK02): a trusted click, or
     * Enter or Space, inside the app's window or one of its widgets, a moment ago. Each one
     * authorizes one message. The page-wide user activation would also count a click anywhere on
     * the desktop, and would allow repeats.
     *
     * Enter or Space on a button (or Enter in a form) also makes the browser fire a click, with
     * `detail` 0. That click is the same press: it carries the key's gesture, spent or not, and
     * never a new one (Codex SDK02-R1). Holding the key down is one press too.
     */
    _watchAppGestures(documentLike = globalThis.document) {
        let press = null; // the last Enter or Space, whose click the browser may fire next
        const record = (event) => {
            if (!event.isTrusted) return;
            const now = Date.now();
            if (event.type === 'keydown') {
                if (!['Enter', ' '].includes(event.key)) { press = null; return; }
                if (event.repeat) { if (press) press.at = now; return; }
            } else if (event.detail === 0 && press && now - press.at <= APP_GESTURE_MS) {
                press.at = press.gesture.at = now;
                this._appGesture = press.gesture;
                return;
            }
            const surface = event.target?.closest?.('[data-app-view], [data-widget-app]');
            const app = surface?.dataset.appView || surface?.dataset.widgetApp || '';
            const gesture = app ? { app, at: now, spent: false } : null; // a click elsewhere clears it
            this._appGesture = gesture;
            press = event.type === 'keydown' && gesture ? { gesture, at: now } : null;
        };
        documentLike?.addEventListener?.('click', record, true);
        documentLike?.addEventListener?.('keydown', record, true);
    }

    /** Spend the person's last action, if it was in app ``id`` and recent: true once per action. */
    _takeAppGesture(id) {
        const gesture = this._appGesture;
        if (!gesture || gesture.spent || gesture.app !== id || Date.now() - gesture.at > APP_GESTURE_MS) return false;
        gesture.spent = true; // its click, if the browser fires one next, finds it spent
        return true;
    }

    /**
     * Load (once) and mount an app that has its own module in its view. Returns its controller,
     * or null when it failed (the view shows the error and «Reintentar») or was retired.
     */
    async _showModuleApp(id) {
        const view = this.panel?.querySelector(`[data-app-view="${id}"]`);
        if (!view) return null;
        if (this._appHost.state(id) !== 'ready') this._renderAppStatus(view, id, 'loading');
        try {
            await this._appHost.ensure(id);
        } catch (error) {
            if (error?.name !== 'AppDisposedError') this._renderAppStatus(view, id, 'error');
            return null;
        }
        // Closing the window while it loaded does not reopen it: the app mounts in its view,
        // wherever that view is now, and stays hidden until the person opens it again.
        return this._mountModuleApp(id);
    }

    /** Mount a ready app in its view (once) and return its controller, or null when it fails. */
    _mountModuleApp(id) {
        const view = this.panel?.querySelector(`[data-app-view="${id}"]`);
        if (!view || this._appHost.state(id) !== 'ready') return null;
        view.querySelector(':scope > .os-app-status')?.remove();
        let root = view;
        if (INSTALLED.has(id)) {
            // The shell moves and hides the view. Installed apps may style
            // their root without changing how the shell displays that view.
            root = view.firstElementChild;
            if (!root) {
                root = el('div', 'os-app-root');
                view.append(root);
            }
        }
        try {
            this._appHost.mount(id, root);
        } catch (error) {
            // The host retired the instance; whatever it drew is cleared and the error shown.
            console.warn(`[IRIS OS] La app ${id} no pudo mostrarse:`, error);
            view.replaceChildren?.();
            this._renderAppStatus(view, id, 'error');
            return null;
        }
        return this._appHost.get(id);
    }

    _renderAppStatus(view, id, kind) {
        const name = this._escape(OS_APPS.get(id)?.name || id);
        let status = view.querySelector(':scope > .os-app-status');
        if (!status) {
            status = document.createElement('div');
            status.className = 'os-app-status';
            view.prepend(status);
        }
        status.setAttribute('role', kind === 'error' ? 'alert' : 'status');
        status.dataset.kind = kind;
        status.innerHTML = kind === 'error'
            ? `<p>No se pudo abrir ${name}.</p><button type="button" data-app-retry="${this._escapeAttr(id)}">Reintentar</button>`
            : `<p>Cargando ${name}…</p>`;
    }

    /** «Reintentar» after a failed load: a new attempt, which the next open joins. */
    _retryApp(id) {
        if (this._appHost.state(id) !== 'error') return;
        this._appHost.retry(id).catch(() => {}); // the view reports the outcome
        this._loadApp(id, { opening: true });
    }

    /** Hand a navigation to an app with its own module once it is mounted. */
    _navigateApp(id, payload) {
        if (!OS_APPS.hasOwnCode(id)) return;
        void this._showModuleApp(id)
            .then((controller) => controller?.navigate?.(payload))
            .catch((error) => console.warn(`[IRIS OS] La app ${id} no pudo abrir ese punto:`, error));
    }

    /** An app's own stylesheet, adopted once by the page (F09.3); it scopes its rules itself. */
    _installAppStyle(id, css) {
        this._appStyles ||= new Map();
        if (this._appStyles.has(id)) return;
        if (typeof CSSStyleSheet === 'function' && 'adoptedStyleSheets' in document) {
            const sheet = new CSSStyleSheet();
            sheet.replaceSync(css);
            document.adoptedStyleSheets = [...document.adoptedStyleSheets, sheet];
            this._appStyles.set(id, sheet);
            return;
        }
        const style = document.createElement('style');
        style.dataset.appStyle = id;
        style.textContent = css;
        document.head.append(style);
        this._appStyles.set(id, style);
    }

    _syncModuleVisibility() {
        this._syncDesktopWidgets?.();
        this._themeHomePreview?.setVisibility(this._hasVisibleAppSurface('theme'));
        this._transitionStudio?.setVisibility(this._hasVisibleAppSurface('theme'));
        this._fontStudio?.setVisibility(this._hasVisibleAppSurface('theme'));
        this._desktopHome?.setVisibility?.(this._widgetsDesktopVisible());
        if (!this._hasVisibleAppSurface('theme')) this._themeImportSequence = (this._themeImportSequence || 0) + 1;
        if (!this._open || globalThis.document?.visibilityState === 'hidden') this._cancelThemeMotion?.();
        for (const app of OS_APPS.list()) {
            if (!OS_APPS.hasOwnCode(app.id)) continue;
            const visible = this._hasVisibleAppSurface(app.id);
            // A window restored while the page was hidden never loaded: it loads once it can be seen.
            if (visible && this._appHost?.state(app.id) === 'unloaded') this._loadApp(app.id, { opening: true });
            else this._appHost?.setVisibility(app.id, visible, 'shell');
        }
    }

    /** Load an app through its adapter. A failure stays in that app; the others keep working. */
    _loadApp(name = '', { force = false, opening = false } = {}) {
        if (OS_APPS.hasOwnCode(name)) {
            const opened = (controller) => {
                if (!controller) return;
                // Closed while it loaded: no late read, the app only learns it is hidden.
                if (!this._hasVisibleAppSurface(name)) {
                    this._appHost.setVisibility(name, false, 'closed');
                    return;
                }
                // Refresh first, then tell the app it is visible: one read when a window opens.
                const report = (error) => console.warn(`[IRIS OS] No se pudo actualizar ${name}:`, error);
                try {
                    const refreshed = controller.refresh?.({ force, opening });
                    if (typeof refreshed?.catch === 'function') refreshed.catch(report);
                } catch (error) {
                    report(error);
                }
                this._appHost.setVisibility(name, this._hasVisibleAppSurface(name), 'open');
            };
            // A ready app refreshes right away, before the shell reports visibility, as the
            // adapters do; only the first open waits for the module.
            if (this._appHost.state(name) === 'ready') opened(this._mountModuleApp(name));
            else void this._showModuleApp(name).then(opened);
            return;
        }
        const load = this._appAdapters?.[name]?.load;
        if (typeof load !== 'function') return;
        const report = (error) => console.warn(`[IRIS OS] No se pudo cargar ${name}:`, error);
        try {
            const result = load({ force, opening });
            if (typeof result?.catch === 'function') result.catch(report);
        } catch (error) {
            report(error);
        }
    }

    _loadNotificationsTab(force = false) {
        if (this._notificationsTab === 'vision') this._notificationApp?.load?.(force);
        else this._inboxApp?.load?.(force);
    }

    _showNotificationsTab(name, { focus = false } = {}) {
        const tab = name === 'vision' ? 'vision' : 'inbox';
        this._notificationsTab = tab;
        this.panel?.querySelectorAll('[data-notifications-tab]').forEach((button) => {
            const selected = button.dataset.notificationsTab === tab;
            button.setAttribute('aria-selected', String(selected));
            button.tabIndex = selected ? 0 : -1;
            if (selected && focus) button.focus?.({ preventScroll: true });
        });
        this.panel?.querySelectorAll('[data-notifications-pane]').forEach((pane) => {
            pane.hidden = pane.dataset.notificationsPane !== tab;
        });
        if (tab === 'vision' && this._hasVisibleAppSurface('notifications')) this._notificationApp?.load?.();
        this._inboxApp?.syncVisibility?.();
    }

    _renderNotificationBrief() {
        // The general count includes Vision's; Vision's own count stands in until the inbox has answered.
        const unread = this._inboxUnread ?? this._visionUnread;
        const text = unread === null || unread === undefined ? 'Bandeja local'
            : unread ? `${unread} sin leer` : 'Nada pendiente';
        this.panel?.querySelectorAll('[data-app-brief="notifications"]').forEach((node) => { node.textContent = text; });
    }

    /** F11: reveal the desktop before navigating to a calendar reminder. */
    openCalendar(payload) {
        this.openCommandSurface('calendar');
        this._navigateApp('calendar', payload);
    }

    /** An app notice's «Abrir <app>»: reveal the desktop with that app. Opening marks nothing read. */
    openApp(appId) {
        if (OS_APPS.get(appId)) this.openCommandSurface(appId);
    }

    /** F07: the return notice's «Abrir bandeja». Opening it marks nothing read. */
    openInbox() {
        this._notificationsTab = 'inbox';
        this.openCommandSurface('notifications');
        this._showNotificationsTab('inbox');
    }

    _showModelsTab(name, { focus = false } = {}) {
        const tab = name === 'vision' ? 'vision' : 'selector';
        this._modelsTab = tab;
        this.panel.querySelectorAll('[data-models-tab]').forEach((button) => {
            const active = button.dataset.modelsTab === tab;
            button.setAttribute('aria-selected', String(active));
            button.tabIndex = active ? 0 : -1;
            if (active && focus) button.focus();
        });
        this.panel.querySelectorAll('[data-models-pane]').forEach((pane) => {
            pane.hidden = pane.dataset.modelsPane !== tab;
        });
        this._loadModelsTab(false);
    }

    _loadModelsTab(force = false) {
        if (this._modelsTab === 'vision') this._visionModelApp?.load?.(force);
        else this._modelCatalogApp?.load?.(force);
    }

    /**
     * F09.4: an operation Core validated for an IRIS OS app. Core sends the reviewed label of
     * the operation, so the confirmation says what will happen; the model never confirms. With
     * `confirm_only` the operation runs in Core and this only asks the person.
     */
    async handleAppIntent(payload = {}) {
        const requestId = String(payload?.request_id || '');
        const reply = (status, message, data = {}) => ({ schema: 'iris.app-result/v1', request_id: requestId, status, message, data });
        const app = OS_APPS.get(payload?.app);
        const appName = app?.name || String(payload?.app || 'esta app');
        const label = String(payload?.operation?.label || 'usar esta app');
        this._appIntentControllers ||= new Map();
        const controller = new AbortController();
        this._appIntentControllers.set(requestId, controller);
        const expiresAt = Number(payload.expires_at);
        const timer = Number.isFinite(expiresAt) && expiresAt > 0
            ? setTimeout(() => controller.abort(), Math.max(0, expiresAt * 1000 - Date.now())) : null;
        try {
            let confirmed = null;
            let remember = false;
            if (payload?.permission?.requires_confirmation === true) {
                const options = {
                    title: appName, icon: 'shield',
                    message: appActionMessage(`IRIS quiere ${label} en ${appName}.`, payload?.operation),
                    question: '¿Permitir esta acción una vez?', confirmLabel: 'Permitir una vez',
                    signal: controller.signal,
                };
                if (payload.permission.remember_scope) {
                    const choice = await irisChoice({ ...options,
                        message: `${options.message}\n\n${payload.permission.remember_scope}\nPuedes revocarlo desde Permisos.`,
                        question: '¿Cómo quieres permitir esta acción?',
                        choices: [{ value: 'once', label: 'Permitir una vez' }, { value: 'always', label: 'Permitir siempre esta operación' }],
                    });
                    confirmed = choice === 'once' || choice === 'always';
                    remember = choice === 'always';
                } else {
                    confirmed = await irisConfirm(options);
                }
                if (!confirmed) return reply('denied', `La acción de ${appName} no fue confirmada en este equipo.`, { permission_confirmed: false });
            }
            if (controller.signal.aborted || (expiresAt > 0 && Date.now() >= expiresAt * 1000)) {
                return reply('denied', 'La solicitud caducó; la acción no se ejecutó.', { permission_confirmed: false });
            }
            const withConfirmation = (data) => (confirmed ? { ...data, permission_confirmed: true, ...(remember ? { permission_remember: true } : {}) } : data);
            if (payload?.confirm_only === true) return reply('ok', '', withConfirmation({}));
            if (!app || !OS_APPS.hasOwnCode(app.id)) return reply('unavailable', `${appName} no está disponible en IRIS OS.`);
            const appController = await this._showModuleApp(app.id);
            if (controller.signal.aborted) return reply('denied', 'La solicitud caducó; la acción no se ejecutó.');
            if (typeof appController?.handleIntent !== 'function') return reply('unavailable', `${appName} no acepta acciones de Iris.`);
            const answer = await appController.handleIntent({
                action: String(payload.action || ''),
                args: payload.args && typeof payload.args === 'object' ? payload.args : {},
                request_id: requestId,
            });
            const status = ['ok', 'needs_choice', 'unavailable', 'denied', 'error'].includes(answer?.status) ? answer.status : 'error';
            const data = answer?.data && typeof answer.data === 'object' ? answer.data : {};
            return reply(status, typeof answer?.message === 'string' ? answer.message : '', withConfirmation(data));
        } finally {
            if (timer !== null) clearTimeout(timer);
            if (this._appIntentControllers.get(requestId) === controller) this._appIntentControllers.delete(requestId);
        }
    }

    cancelAppIntent(requestId) {
        this._appIntentControllers?.get(String(requestId || ''))?.abort();
    }

    async handlePlayerIntent(payload = {}) {
        if (!this._playerApp?.handleIntent) {
            return {
                schema: 'iris.app-result/v1',
                request_id: String(payload?.request_id || ''),
                status: 'unavailable',
                message: 'IRIS Player no está disponible en esta superficie.',
                data: {},
            };
        }
        const requiresConfirmation = payload?.permission?.requires_confirmation === true;
        if (requiresConfirmation) {
            const approved = await irisConfirm({
                title: 'IRIS Player', icon: 'shield',
                message: appActionMessage('IRIS quiere controlar el reproductor local una vez.', payload?.operation),
                question: '¿Permitir esta acción?', confirmLabel: 'Permitir una vez',
            });
            if (!approved) {
                return {
                    schema: 'iris.app-result/v1',
                    request_id: String(payload?.request_id || ''),
                    status: 'denied',
                    message: 'La acción de IRIS Player no fue confirmada en este equipo.',
                    data: { permission_confirmed: false },
                };
            }
        }
        const result = await this._playerApp.handleIntent(payload);
        if (requiresConfirmation && result && typeof result === 'object') {
            result.data = {
                ...(result.data && typeof result.data === 'object' ? result.data : {}),
                permission_confirmed: true,
            };
        }
        return result;
    }

    _restorePersistedWindows() {
        if (!this.panel || this._restoredPersistedWindows) return;
        this._restoredPersistedWindows = true;
        Object.entries(this._windowLayout || {})
            .filter(([name, layout]) => OS_WINDOW_APPS[name] && layout?.open)
            .sort((a, b) => Number(a[1]?.z || 0) - Number(b[1]?.z || 0))
            .forEach(([name]) => this._openWindow(name, { preserveMinimized: true }));
    }

    _setDesktopMode(mode = 'home', { animate = true } = {}) {
        this._activeTab = 'desktop';
        this._desktopMode = mode === 'dashboard' ? 'dashboard' : 'home';
        this._desktopLauncherOpen = false;
        if (this.panel) this.panel.dataset.activeTab = 'desktop';
        this.panel?.querySelectorAll('[data-workbench-tab]').forEach((btn) => {
            const selected = btn.dataset.workbenchTab === 'desktop';
            btn.classList.toggle('active', selected);
            if (selected) btn.setAttribute('aria-current', 'page');
            else btn.removeAttribute('aria-current');
        });
        this.panel?.querySelectorAll('[data-workbench-view]').forEach((view) => {
            view.classList.toggle('active', view.dataset.workbenchView === 'desktop');
        });
        this._renderDesktopHome();
        if (animate) this._playDesktopTransition();
        else this._cancelThemeMotion?.();
        this._syncAppPolling();
    }

    _playDesktopTransition() {
        if (!this.panel) return;
        this._cancelThemeMotion();
        const theme = { ...OS_THEME_DEFAULT, ...this._osTheme };
        const transition = String(theme.desktopTransition || 'none');
        if (!this._open || document.visibilityState === 'hidden' || transition === 'none' || theme.reducedMotion || globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return;
        if (transition.startsWith('custom:')) {
            this._transitionPlayer?.playTheme(this.panel.querySelector(`[data-workbench-view="${this._activeTab || 'desktop'}"]`), theme);
            return;
        }
        this.panel.dataset.transitionPulse = String(Date.now());
        this.panel.classList.remove('os-transitioning');
        this._transitionFrame = window.requestAnimationFrame?.(() => {
            this._transitionFrame = null;
            this.panel?.classList.add('os-transitioning');
            this._transitionTimer = window.setTimeout?.(() => { this._transitionTimer = null; this.panel?.classList.remove('os-transitioning'); }, 720);
        });
    }

    _cancelThemeMotion() {
        this._transitionPlayer?.cancelAll();
        if (this._transitionFrame) window.cancelAnimationFrame?.(this._transitionFrame);
        if (this._transitionTimer) window.clearTimeout?.(this._transitionTimer);
        this._transitionFrame = null; this._transitionTimer = null;
        this.panel?.classList.remove('os-transitioning');
    }

    _dismissDesktopWelcome() {
        this._desktopWelcomeHidden = true;
        try {
            localStorage.setItem('irisDesktopWelcome.dismissed', 'true');
        } catch {
            // The card can still close even if persistence is unavailable.
        }
        this._renderDesktopHome();
    }

    _renderCameraPreview(message = '') {
        if (!this.panel) return;
        const video = this.panel.querySelector('.os-camera-video');
        const placeholder = this.panel.querySelector('.os-camera-placeholder');
        const status = this.panel.querySelector('.os-camera-status');
        const start = this.panel.querySelector('[data-camera-action="start"]');
        if (start) start.disabled = Boolean(this._cameraPreviewPending);
        const stream = hasLiveCameraStream(this._cameraStream) ? this._cameraStream : null;
        const visible = this._hasVisibleAppSurface('camera');
        if (!stream || !visible) {
            void attachCameraPreview(video, null);
            placeholder?.classList.remove('hidden');
            video?.classList.remove('active');
            if (status) status.textContent = message || 'Cámara en espera.';
            return;
        }
        video?.classList.add('active');
        if (status) status.textContent = message || 'Preparando vista de la cámara…';
        const generation = this._cameraPreviewGeneration || 0;
        void attachCameraPreview(video, stream).then(playing => {
            if (generation !== (this._cameraPreviewGeneration || 0)
                || this._cameraStream !== stream || !this._hasVisibleAppSurface('camera')) return;
            placeholder?.classList.toggle('hidden', playing);
            if (status) status.textContent = playing ? 'Cámara conectada.' : 'Pulsa Abrir cámara para reintentar la vista.';
        });
    }

    async _runCameraAction(action = '') {
        if (action === 'stop') {
            this._stopCameraPreview({ release: true });
            this._renderCameraPreview('Vista cerrada. Vision Assistant puede seguir usando la cámara si está activo.');
            return;
        }
        if (action !== 'start' || this._cameraPreviewPending) return;
        if (typeof this.acquireCamera !== 'function') {
            this._renderCameraPreview('El controlador de cámara no está disponible.');
            return;
        }
        const generation = this._cameraPreviewGeneration = (this._cameraPreviewGeneration || 0) + 1;
        this._cameraPreviewPending = true;
        this._renderCameraPreview('Conectando cámara…');
        try {
            const stream = await this.acquireCamera('os-camera', { confirmed: true });
            if (generation !== this._cameraPreviewGeneration) return;
            this._cameraStream = stream;
            const reason = stream ? '' : this.getCameraError?.() || 'error';
            this._renderCameraPreview(stream ? '' : CAMERA_ERROR_MESSAGES[reason] || CAMERA_ERROR_MESSAGES.error);
        } catch (err) {
            if (generation !== this._cameraPreviewGeneration) return;
            this._cameraStream = null;
            this._renderCameraPreview('No pude abrir la cámara: ' + (err?.message || err));
        } finally {
            if (generation === this._cameraPreviewGeneration) this._cameraPreviewPending = false;
            const start = this.panel?.querySelector('[data-camera-action="start"]');
            if (start) start.disabled = Boolean(this._cameraPreviewPending);
        }
    }

    setSharedCameraStream(stream = null) {
        this._cameraStream = stream;
        this._renderCameraPreview();
    }

    _stopCameraPreview({ release = false } = {}) {
        this._cameraPreviewGeneration = (this._cameraPreviewGeneration || 0) + 1;
        this._cameraPreviewPending = false;
        if (release) this.releaseCamera?.('os-camera');
        this._cameraStream = null;
        void attachCameraPreview(this.panel?.querySelector('.os-camera-video'), null);
    }

    async loadVisionAssistant(force = false) {
        if (this._visionLoading) return;
        if (!force && this._visionLoadedAt && Date.now() - this._visionLoadedAt < 5000) {
            this._renderVisionAssistant();
            return;
        }
        const version = this._visionStateVersion || 0;
        this._visionLoading = true;
        this._visionError = '';
        this._renderVisionAssistant();
        try {
            const [settingsResponse, eventsResponse] = await Promise.all([
                fetch('/api/vision-assistant/settings', { cache: 'no-store' }),
                fetch('/api/vision-assistant/events?limit=40', { cache: 'no-store' }),
            ]);
            if (!settingsResponse.ok) {
                throw new Error(await this._responseError(settingsResponse, 'Vision Assistant'));
            }
            if (!eventsResponse.ok) {
                throw new Error(await this._responseError(eventsResponse, 'Historial visual'));
            }
            const settings = await settingsResponse.json();
            if (version !== (this._visionStateVersion || 0)) return;
            this.applyVisionAssistantState(settings);
            const eventData = await eventsResponse.json();
            this._visionEvents = Array.isArray(eventData.events) ? eventData.events : [];
            this._visionLoadedAt = Date.now();
        } catch (err) {
            if (version === (this._visionStateVersion || 0)) this._visionError = err?.message || String(err);
        } finally {
            if (!this._visionSaving) this._visionLoading = false;
            this._renderVisionAssistant();
        }
    }

    applyVisionAssistantState(payload = {}, { notify = true } = {}) {
        this._visionStateVersion = (this._visionStateVersion || 0) + 1;
        const next = { ...(payload || {}) };
        const confirmed = next.camera_confirmation_granted === true;
        delete next.camera_confirmation_granted;
        this._visionSettings = { ...this._visionSettings, ...next };
        if (notify) this.onVisionSettingsChanged?.({
            ...this._visionSettings,
            ...(confirmed ? { camera_confirmation_granted: true } : {}),
        });
        this._renderVisionAssistant();
    }

    pushVisionEvent(event = {}) {
        if (!event?.type) return;
        this._visionEvents = [event, ...this._visionEvents.filter((row) => row.id !== event.id)].slice(0, 40);
        this._renderVisionAssistant();
    }

    async _saveVisionSettings(updates = {}) {
        const sequence = this._visionSaveSequence = (this._visionSaveSequence || 0) + 1;
        const previous = this._visionSettings;
        const version = this._visionStateVersion = (this._visionStateVersion || 0) + 1;
        this._visionSaving = true;
        this._visionLoading = true;
        this._visionError = '';
        // Keep the user's selection visible while saving; only the confirmed
        // response may notify the camera owner and start observation.
        this._visionSettings = { ...previous, ...updates };
        this._renderVisionAssistant();
        try {
            const requestPayload = updates.enabled === true
                ? { ...updates, confirm_camera: true } : updates;
            const response = await fetch('/api/vision-assistant/settings', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(requestPayload),
            });
            if (!response.ok) throw new Error(await this._responseError(response, 'Guardar Vision Assistant'));
            const state = await response.json();
            if (sequence !== this._visionSaveSequence) return;
            this.applyVisionAssistantState(state);
            this._visionLoadedAt = Date.now();
        } catch (err) {
            if (sequence !== this._visionSaveSequence) return;
            if (version === this._visionStateVersion) this._visionSettings = previous;
            this._visionError = err?.message || String(err);
        } finally {
            if (sequence === this._visionSaveSequence) {
                this._visionSaving = false;
                this._visionLoading = false;
                this._renderVisionAssistant();
            }
        }
    }

    async _runVisionAction(action = '') {
        if (action === 'pause') {
            await this._saveVisionSettings({ pause_minutes: 30 });
            return;
        }
        if (action === 'resume') {
            await this._saveVisionSettings({ pause_minutes: 0 });
            return;
        }
        if (action !== 'clear' || this._visionLoading) return;
        this._visionLoading = true;
        this._visionError = '';
        this._renderVisionAssistant();
        try {
            const response = await fetch('/api/vision-assistant/events', { method: 'DELETE' });
            if (!response.ok) throw new Error(await this._responseError(response, 'Borrar historial visual'));
            this._visionEvents = [];
        } catch (err) {
            this._visionError = err?.message || String(err);
        } finally {
            this._visionLoading = false;
            this._renderVisionAssistant();
        }
    }

    _renderVisionAssistant() {
        if (!this.panel) return;
        const settings = this._visionSettings || {};
        const paused = !!settings.enabled && Number(settings.paused_until || 0) > Date.now() / 1000;
        const state = !settings.enabled ? 'off' : paused ? 'paused' : String(settings.state || 'waiting_camera');
        const labels = {
            off: 'OFF', paused: 'PAUSADO', waiting_camera: 'ESPERANDO CÁMARA',
            camera_ready: 'CÁMARA LOCAL', observing: 'OBSERVANDO LOCAL',
        };
        const badge = this.panel.querySelector('.os-vision-state');
        if (badge) {
            badge.dataset.state = state;
            badge.textContent = labels[state] || state.toUpperCase();
        }
        const enabled = this.panel.querySelector('[data-vision-field="enabled"]');
        const cooldown = this.panel.querySelector('[data-vision-field="cooldown_minutes"]');
        const retention = this.panel.querySelector('[data-vision-field="retention_days"]');
        if (enabled) enabled.checked = !!settings.enabled;
        if (cooldown) cooldown.value = String(settings.cooldown_minutes ?? 25);
        if (retention) retention.value = String(settings.retention_days ?? 7);
        this.panel.querySelectorAll('[data-vision-field], [data-vision-action]').forEach((control) => {
            control.disabled = !!this._visionLoading;
        });
        const pauseButton = this.panel.querySelector('[data-vision-action="pause"]');
        const resumeButton = this.panel.querySelector('[data-vision-action="resume"]');
        if (pauseButton) pauseButton.disabled = this._visionLoading || !settings.enabled || paused;
        if (resumeButton) resumeButton.disabled = this._visionLoading || !paused;
        this._renderVisionEvents();
    }

    _renderVisionEvents() {
        const container = this.panel?.querySelector('.os-vision-events');
        if (!container) return;
        if (this._visionError) {
            container.innerHTML = `<p class="os-vision-empty error">${this._escape(this._visionError)}</p>`;
            return;
        }
        if (this._visionLoading && !this._visionEvents.length) {
            container.innerHTML = '<p class="os-vision-empty">Sincronizando estado local…</p>';
            return;
        }
        if (!this._visionEvents.length) {
            container.innerHTML = '<p class="os-vision-empty">Aún no hay eventos resumidos.</p>';
            return;
        }
        container.innerHTML = this._visionEvents.slice(0, 12).map((event) => {
            const stamp = new Date(Number(event.observed_at || 0) * 1000);
            const when = Number.isNaN(stamp.getTime()) ? '' : stamp.toLocaleTimeString([], {
                hour: '2-digit', minute: '2-digit',
            });
            const motionLabels = {
                still: 'sin movimiento',
                low: 'movimiento leve',
                active: 'movimiento activo',
            };
            const motion = motionLabels[event.motion] || 'sin movimiento';
            const cooldown = event.suppression_reason === 'cooldown' ? ' · en espera' : '';
            return `<article><span>${this._escape(when)}</span><strong>${this._escape(event.label || event.type)}</strong><small>${this._escape(motion)}${cooldown}</small></article>`;
        }).join('');
    }

    _toggleThemePanel(force = undefined) {
        this._themePanelOpen = typeof force === 'boolean' ? force : !this._themePanelOpen;
        this._renderThemePanel();
    }

    _setThemePreset(name = '') {
        const preset = OS_THEME_PRESETS[name];
        if (!preset) return;
        this._osTheme = { ...this._osTheme, ...preset, backgroundUrl: '', profileUrl: '' };
        this._applyOsTheme(true);
        this._renderThemePanel();
    }

    _setThemeValue(key = '', value = '') {
        if (!Object.prototype.hasOwnProperty.call(OS_THEME_DEFAULT, key)) return false;
        this._osTheme = { ...this._osTheme, [key]: value };
        const persisted = this._applyOsTheme(true);
        this._renderThemePanel();
        return persisted;
    }

    _setThemeValues(values = {}) {
        const next = { ...this._osTheme };
        Object.entries(values).forEach(([key, value]) => {
            if (Object.prototype.hasOwnProperty.call(OS_THEME_DEFAULT, key)) next[key] = value;
        });
        this._osTheme = next;
        this._applyOsTheme(true);
        this._renderThemePanel();
    }

    _resetOsTheme() {
        const next = { ...OS_THEME_DEFAULT, transitionLibrary: themeTransitions(this._osTheme).transitionLibrary };
        if (!this._commitTransitionTheme(next)) {
            void irisAlert({ title: 'Tema no restaurado', icon: 'warning', message: 'No se pudo guardar. Tu tema anterior se conserva.' });
        }
    }

    _runThemeAction(action = '') {
        if (action === 'refresh-preview') { void this._themeHomePreview?.refresh(); return; }
        if (action === 'reset') {
            this._resetOsTheme();
            return;
        }
        if (action === 'custom') {
            this._focusThemeField('accent');
            return;
        }
        if (action === 'browse') {
            this._pickThemeImage('backgroundUrl', { maxBytes: 3_200_000 });
            return;
        }
        if (action === 'profile-photo') {
            this._pickThemeImage('profileUrl', { maxBytes: 2_200_000 });
            return;
        }
        if (action === 'export') {
            this._exportTheme();
            return;
        }
        if (action === 'import') {
            this._importTheme();
        }
    }

    _focusThemeField(key = '') {
        const target = this.panel?.querySelector(`[data-workbench-view="theme"] [data-os-theme-field="${key}"]`);
        target?.focus?.();
        target?.scrollIntoView?.({ block: 'center', behavior: 'smooth' });
    }

    _pickThemeImage(field = '', { maxBytes = 2_200_000 } = {}) {
        if (!Object.prototype.hasOwnProperty.call(OS_THEME_DEFAULT, field)) return;
        const input = document.createElement('input');
        input.type = 'file';
        input.accept = 'image/png,image/jpeg,image/gif,image/webp';
        input.addEventListener('change', async () => {
            const file = input.files?.[0];
            if (!file) return;
            if (!String(file.type || '').startsWith('image/')) {
                await irisAlert({ title: 'Imagen no válida', icon: 'warning', message: 'Selecciona una imagen válida.' });
                return;
            }
            if (file.size > maxBytes) {
                await irisAlert({ title: 'Imagen demasiado grande', icon: 'warning',
                    message: `La imagen supera ${(maxBytes / 1_000_000).toFixed(1)} MB.` });
                return;
            }
            try {
                const form = new FormData();
                form.append('file', file, file.name);
                form.append('kind', field === 'profileUrl' ? 'profile' : 'background');
                const response = await fetch('/api/os/theme-image', {
                    method: 'POST',
                    body: form,
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok || data.status !== 'saved' || !data.url) {
                    throw new Error(data.detail || 'IRIS no pudo guardar esa imagen.');
                }
                if (!this._setThemeValue(field, data.url)) {
                    throw new Error('La imagen se guardó, pero la preferencia del tema no pudo persistirse.');
                }
            } catch (error) {
                await irisAlert({ title: 'No se guardó la imagen', icon: 'warning',
                    message: error?.message || 'No pude guardar esa imagen.' });
            }
        }, { once: true });
        input.click();
    }

    async _exportTheme() {
        const { payload, omittedImages, omittedFonts = [] } = encodeThemeFile(this._osTheme);
        if ((omittedImages.length || omittedFonts.length) && !await irisConfirm({ title: 'Exportar ajustes del tema',
            message: 'El archivo comparte colores, opciones y transiciones. No incluye imágenes ni fuentes subidas. Quien lo importe podrá añadir sus propios archivos.',
            confirmLabel: 'Exportar ajustes' })) return;
        const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = `iris-os-theme-${new Date().toISOString().slice(0, 10)}.json`;
        document.body.appendChild(link);
        link.click();
        link.remove();
        const release = setTimeout(() => URL.revokeObjectURL(url), 30_000);
        release?.unref?.();
    }

    _importTheme() {
        const sequence = this._themeImportSequence = (this._themeImportSequence || 0) + 1;
        const revision = this._themeRevision || 0;
        const current = () => sequence === this._themeImportSequence && revision === (this._themeRevision || 0)
            && this._hasVisibleAppSurface('theme');
        const input = document.createElement('input');
        input.type = 'file';
        input.accept = 'application/json,.json';
        input.addEventListener('change', async () => {
            const file = input.files?.[0];
            if (!file) return;
            try {
                if (file.size > THEME_FILE_MAX_BYTES) throw new Error('El archivo de tema es demasiado grande.');
                const source = await file.text();
                if (!current()) return;
                const { theme: next, omittedImages, omittedFonts = [], hasLibrary } = decodeThemeFile(source);
                if ((omittedImages.length || omittedFonts.length) && !await irisConfirm({ title: 'Importar sin archivos propios',
                    message: 'Este tema contiene referencias a imágenes o fuentes que el archivo no incluye. No las cargaré. Puedes importar sus otros ajustes y después añadir tus archivos desde el equipo.',
                    confirmLabel: 'Importar ajustes' })) return;
                if (!current()) return;
                if (!hasLibrary) next.transitionLibrary = themeTransitions(this._osTheme).transitionLibrary;
                if (!this._commitTransitionTheme(next)) throw new Error('No pude guardar ese tema. Se conserva el anterior.');
            } catch (error) {
                if (current()) await irisAlert({ title: 'Tema no importado', icon: 'warning', message: error.message || 'No pude importar ese tema.' });
            }
        }, { once: true });
        input.click();
    }

    async _runSystemUpdateAction(action = '') {
        if (action === 'check') {
            await this._checkSystemUpdates(true);
            return;
        }
        if (action === 'apply') {
            await this._applySystemUpdates();
        }
        if (action === 'rollback') {
            await this._applySystemUpdates('rollback');
        }
    }

    async _checkSystemUpdates(force = false, { resumeOnly = false } = {}) {
        if (this._systemUpdateLoading) return;
        this._systemUpdateLoading = true;
        this._systemUpdateError = '';
        this._renderSystemUpdates();
        try {
            const status = await fetch('/api/system-updates/status', { cache: 'no-store' });
            let job = null;
            if (status.ok) {
                job = await status.json();
                if (job.active || (resumeOnly && ['completed', 'failed'].includes(job.phase))) {
                    this._systemUpdateApplying = Boolean(job.active);
                    await this._followSystemUpdate(job);
                    return;
                }
            }
            if (resumeOnly) {
                // A08.8: the installed version reports its state (last activation,
                // rollback) without querying the channel.
                if (job?.snapshot && !this._systemUpdates) this._systemUpdates = job.snapshot;
                return;
            }
            const url = `/api/system-updates/check${force ? '?force=true' : ''}`;
            const res = await fetch(url, { cache: 'no-store' });
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            this._systemUpdates = await res.json();
        } catch (err) {
            this._systemUpdateError = err?.message || 'No se pudieron buscar actualizaciones.';
        } finally {
            this._systemUpdateLoading = false;
            this._systemUpdateApplying = false;
            this._renderSystemUpdates();
        }
    }

    async _followSystemUpdate(job = {}) {
        this._systemUpdateJob = job;
        while (job.active) {
            this._renderSystemUpdates();
            await new Promise(resolve => setTimeout(resolve, 1500));
            const response = await fetch('/api/system-updates/status', { cache: 'no-store' });
            if (!response.ok) throw new Error('No pude consultar el estado de la actualización. Pulsa Buscar actualizaciones para recuperarlo.');
            job = await response.json();
            this._systemUpdateJob = job;
        }
        this._systemUpdateJob = null;
        this._systemUpdates = job.snapshot || this._systemUpdates;
        if (job.ok === false) {
            this._systemUpdateError = job.message || 'La actualización no se completó.';
        } else if (job.message && this._systemUpdates) {
            this._systemUpdates.message = job.message;
        }
    }

    async _applySystemUpdates(kind = 'apply') {
        const rollback = kind === 'rollback';
        if (this._systemUpdateApplying || !(rollback ? this._systemUpdates?.can_rollback : this._systemUpdates?.can_update)) return;
        if (!await irisConfirm(systemUpdateDialog(this._systemUpdates, rollback))) return;
        this._systemUpdateApplying = true;
        this._systemUpdateError = '';
        this._renderSystemUpdates();
        try {
            const res = await fetch(rollback ? '/api/system-updates/rollback' : '/api/system-updates/apply', { method: 'POST' });
            const payload = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(payload?.detail || `HTTP ${res.status}`);
            await this._followSystemUpdate(payload);
        } catch (err) {
            this._systemUpdateError = err?.message || 'No se pudo instalar la actualizacion.';
        } finally {
            this._systemUpdateApplying = false;
            this._renderSystemUpdates();
        }
    }

    _renderSystemUpdates() {
        const root = this.panel?.querySelector('.os-system-update-zone');
        if (!root) return;
        const summary = root.querySelector('[data-update-summary]');
        const message = root.querySelector('[data-update-message]');
        const list = root.querySelector('[data-update-components]');
        const checkBtn = root.querySelector('[data-system-update-action="check"]');
        const applyBtn = root.querySelector('[data-system-update-action="apply"]');
        const rollbackBtn = root.querySelector('[data-system-update-action="rollback"]');
        const last = root.querySelector('[data-update-last]');
        const busy = this._systemUpdateLoading || this._systemUpdateApplying;
        const snapshot = this._systemUpdates;
        const state = this._systemUpdateError
            ? 'error'
            : this._systemUpdateApplying
                ? 'applying'
                : this._systemUpdateLoading
                    ? 'checking'
                    : snapshot?.summary || 'standby';

        root.dataset.updateState = state;
        if (summary) {
            summary.textContent = this._systemUpdateApplying
                ? 'Actualizando'
                : this._systemUpdateLoading
                    ? 'Comprobando'
                    : this._systemUpdateError
                        ? 'Error'
                        : this._updateSummaryLabel(snapshot?.summary);
        }
        if (message) {
            const job = this._systemUpdateJob;
            const progress = job?.progress?.total
                ? ` ${Math.min(100, Math.floor((job.progress.done / job.progress.total) * 100))} %`
                : '';
            message.textContent = this._systemUpdateError
                || (this._systemUpdateApplying && job?.message ? `${job.message}${progress}` : '')
                || snapshot?.message
                || 'Comprueba IRIS OS y sus componentes antes de instalar cambios.';
        }
        if (last) {
            // A08.8: what the previous activation did, shown after Iris restarts.
            const outcome = snapshot?.last_activation;
            last.hidden = !outcome?.message || this._systemUpdateApplying;
            last.textContent = outcome?.message || '';
            last.dataset.outcome = outcome?.outcome || '';
        }
        if (checkBtn) {
            checkBtn.disabled = busy;
            checkBtn.textContent = this._systemUpdateLoading ? 'Buscando...' : 'Buscar actualizaciones';
        }
        if (applyBtn) {
            applyBtn.disabled = busy || !snapshot?.can_update;
            applyBtn.textContent = this._systemUpdateApplying
                ? 'Actualizando...'
                : snapshot?.available?.version ? `Actualizar a ${snapshot.available.version}` : 'Actualizar';
        }
        if (rollbackBtn) {
            rollbackBtn.hidden = !snapshot?.can_rollback;
            rollbackBtn.disabled = busy;
            rollbackBtn.textContent = snapshot?.previous?.version
                ? `Volver a ${snapshot.previous.version}`
                : 'Volver a la versión anterior';
        }
        if (list) {
            const items = snapshot?.items?.length ? snapshot.items : [
                { label: 'IRIS OS', status: 'standby', detail: 'Esperando revisión.' },
                { label: 'Hermes Agent', status: 'standby', detail: 'Esperando revisión.' },
            ];
            list.innerHTML = items.map((item) => `
                <article data-state="${this._escape(item.status || 'unknown')}">
                    <span>${this._escape(item.label || 'Component')}</span>
                    <strong>${this._escape(this._updateItemLabel(item.status))}</strong>
                    <small>${this._escape(item.detail || '')}</small>
                    ${item.version || item.current_ref ? `<em>${this._escape([item.version, item.current_ref].filter(Boolean).join(' - '))}</em>` : ''}
                </article>
            `).join('');
        }
    }

    _updateSummaryLabel(summary = '') {
        if (summary === 'managed') return 'Administrado por Iris';
        if (summary === 'current') return 'Actualizado';
        if (summary === 'updates_available') return 'Actualización disponible';
        if (summary === 'blocked') return 'Cambios locales';
        if (summary === 'unknown') return 'Revisar';
        if (summary === 'unavailable') return 'No disponible';
        if (summary === 'updating') return 'Actualizando';
        if (summary === 'error') return 'Error';
        return 'En espera';
    }

    _updateItemLabel(status = '') {
        if (status === 'managed') return 'Incluido';
        if (status === 'current') return 'Actualizado';
        if (status === 'current_with_local_changes') return 'Actualizado · local';
        if (status === 'update_available') return 'Disponible';
        if (status === 'repair_required') return 'Finalización pendiente';
        if (status === 'blocked_local_changes') return 'Protegido';
        if (status === 'missing') return 'No instalado';
        if (status === 'error') return 'Error';
        if (status === 'standby') return 'No verificado';
        return 'No verificable';
    }

    _onPointerDown(ev) {
        if (!this._intersectsMonitor(ev)) return;
        ev.preventDefault();
        this.open();
        this.onFocusDesk?.();
    }

    _onPointerMove(ev) {
        const hit = this._intersectsMonitor(ev, false);
        if (hit === this._hoveredMonitor) return;
        this._hoveredMonitor = hit;
        if (this.renderer?.domElement) {
            this.renderer.domElement.style.cursor = hit ? 'pointer' : '';
        }
    }

    _intersectsMonitor(ev, consume = true) {
        if (!this.camera || !this.renderer || !this._monitorMeshes.length) return false;
        const rect = this.renderer.domElement.getBoundingClientRect();
        this._pointer.x = ((ev.clientX - rect.left) / rect.width) * 2 - 1;
        this._pointer.y = -(((ev.clientY - rect.top) / rect.height) * 2 - 1);
        this._raycaster.setFromCamera(this._pointer, this.camera);
        const hits = this._raycaster.intersectObjects(this._monitorMeshes, false);
        return hits.length > 0;
    }

    open() {
        const wasOpen = this._open;
        this._open = true;
        this.panel?.classList.remove('hidden');
        this.panel?.setAttribute('aria-hidden', 'false');
        if (!wasOpen) this.onOpen?.();
        if (this.panel) this.panel.dataset.activeTab = this._activeTab || 'desktop';
        this._renderSidebarState();
        this._renderPanel();
        this.loadOsSummary();
        this._loadActiveTab();
        this._restorePersistedWindows();
        this._syncMissionPolling();
        this._syncAsyncAgentsPolling();
        // Reopening the panel shows its windows again: their apps hear it (close() said hidden).
        this._syncModuleVisibility();
    }

    /** Open an IRIS OS destination from the global command palette. */
    openCommandSurface(target = 'desktop') {
        const destination = OS_APPS.resolve(target);
        this.open();
        if (destination.kind === 'internal' && destination.id === 'apps') {
            this._setTab('desktop');
            this._toggleDesktopLauncher(true);
            return;
        }
        this._openOsApp(destination.id);
    }

    close() {
        const wasOpen = this._open;
        this._open = false;
        this._syncModuleVisibility();
        this.panel?.classList.add('hidden');
        this.panel?.setAttribute('aria-hidden', 'true');
        this._stopCameraPreview();
        this._stopMissionPolling();
        this._stopAsyncAgentsPolling();
        if (this._osSummaryTimer) {
            clearTimeout(this._osSummaryTimer);
            this._osSummaryTimer = null;
        }
        if (wasOpen) this.onClose?.();
    }

    _setTab(name = 'activity') {
        // Any app (tab or window) can show as the workbench view; the shell's own places and
        // unknown names go to the desktop.
        const target = OS_APPS.resolve(name);
        const previousTab = this._activeTab;
        this._activeTab = target.kind === 'window' || target.kind === 'tab' ? target.id : 'desktop';
        if (previousTab === 'camera' && this._activeTab !== 'camera') {
            this._stopCameraPreview();
        }
        if (this._activeTab !== 'desktop' && this._osWindows.has(this._activeTab)) this._closeWindow(this._activeTab);
        if (this._activeTab === 'desktop') this._desktopMode = 'home';
        else this._desktopLauncherOpen = false;
        if (this.panel) this.panel.dataset.activeTab = this._activeTab;
        this.panel?.querySelectorAll('[data-workbench-tab]').forEach((btn) => {
            const selected = btn.dataset.workbenchTab === this._activeTab;
            btn.classList.toggle('active', selected);
            if (selected) btn.setAttribute('aria-current', 'page');
            else btn.removeAttribute('aria-current');
        });
        this.panel?.querySelectorAll('[data-workbench-view]').forEach((view) => {
            view.classList.toggle('active', view.dataset.workbenchView === this._activeTab);
        });
        this._loadActiveTab();
        this._renderDesktopHome();
        if (previousTab !== this._activeTab || this._activeTab === 'desktop') this._playDesktopTransition();
        this._syncMissionPolling();
        this._syncAsyncAgentsPolling();
        // Windows hide behind Ajustes/Cámara and show again on Desktop (Codex H02).
        this._syncModuleVisibility();
    }

    _loadActiveTab({ force = false } = {}) {
        if (!this._open) return;
        this._syncCameraPreview();
        if (this._activeTab !== 'desktop') this._loadApp(this._activeTab, { force });
    }

    _syncMissionPolling() {
        if (this._hasVisibleAppSurface('missions')) {
            this._startMissionPolling();
        } else {
            this._stopMissionPolling();
        }
    }

    _startMissionPolling() {
        if (this._missionPollTimer || !this._hasVisibleAppSurface('missions')) return;
        this._missionPollTimer = setInterval(() => {
            if (!this._hasVisibleAppSurface('missions')) {
                this._stopMissionPolling();
                return;
            }
            this.loadMissions();
        }, 12000);
    }

    _stopMissionPolling() {
        if (!this._missionPollTimer) return;
        clearInterval(this._missionPollTimer);
        this._missionPollTimer = null;
    }

    handleNotificationsChanged(summary = {}) {
        this._notificationApp?.changed(summary);
        this._inboxApp?.changed?.(); // Vision notices are also listed in the general inbox
    }

    /** F07: Core says the general inbox changed (a result finished, an action, a notice shown). */
    handleInboxChanged(payload = {}) {
        if (Number.isInteger(payload?.unread) && payload.unread >= 0) {
            this._inboxUnread = payload.unread;
            this._renderNotificationBrief();
        }
        this._inboxApp?.changed?.();
    }

    _widgetsDesktopVisible() {
        return this._open && document.visibilityState !== 'hidden' && this._activeTab === 'desktop'
            && this._desktopMode === 'home' && !this._desktopLauncherOpen;
    }

    _widgetExposed(root) {
        const rect = root?.getBoundingClientRect();
        const list = this.panel?.querySelector('.os-widgets-list')?.getBoundingClientRect();
        if (!rect?.width || !list?.height || rect.bottom <= list.top || rect.top >= list.bottom) return false;
        return ![...this._osWindows.values()].some((record) => {
            if (record.minimized || !record.el?.isConnected) return false;
            const box = record.el.getBoundingClientRect();
            return box.left <= rect.left && box.right >= rect.right && box.top <= Math.max(rect.top, list.top)
                && box.bottom >= Math.min(rect.bottom, list.bottom);
        });
    }

    _syncDesktopWidgets() {
        const root = this.panel?.querySelector('.os-widgets');
        const height = this.panel?.querySelector('.os-home-screen')?.getBoundingClientRect().height;
        if (root && height) root.style.setProperty('--widgets-bottom', `${this._windowBottomClearance(height)}px`);
        this._widgets?.syncVisibility();
        const homeVisible = this._widgetsDesktopVisible();
        if (homeVisible && !this._homeWasVisible) void this._homePreferences?.load();
        this._homeWasVisible = homeVisible;
    }

    _hasVisibleAppSurface(name = '') {
        if (!this._open || document.visibilityState === 'hidden') return false;
        // Floating apps live inside the home screen, hidden in dashboard/tab views.
        const record = this._osWindows.get(name);
        if (record) return this._activeTab === 'desktop' && this._desktopMode === 'home'
            && !!record.el?.isConnected && !record.minimized;
        if (this._activeTab === name) return true;
        return false;
    }

    _syncCameraPreview() {
        if (this._hasVisibleAppSurface('camera')) {
            const stream = this.getCameraStream?.() || null;
            const video = this.panel?.querySelector('.os-camera-video');
            if (this._cameraStream !== stream || video?.srcObject !== stream || video?.paused) {
                this.setSharedCameraStream(stream);
            }
        } else if (this._cameraStream || this._cameraPreviewPending) {
            this._stopCameraPreview();
        }
    }

    _syncAppPolling() {
        this._syncCameraPreview();
        this._syncMissionPolling();
        this._syncAsyncAgentsPolling();
        // F07: Automatizaciones and the inbox refresh only while really visible; Core runs the rules.
        this._syncModuleVisibility();
        this._inboxApp?.syncVisibility?.();
    }

    _handleAppVisibilityChange() {
        this._syncAppPolling();
        if (this._hasVisibleAppSurface('missions')) this.loadMissions();
        if (this._hasVisibleAppSurface('agents')) this.loadAsyncAgents();
        if (this._hasVisibleAppSurface('notifications')) this._loadNotificationsTab(false);
        if (this._open && document.visibilityState !== 'hidden') this._renderPanel();
    }

    _hasVisibleAsyncAgentsSurface() {
        return this._hasVisibleAppSurface('agents');
    }

    _syncAsyncAgentsPolling() {
        if (this._open && this._hasVisibleAsyncAgentsSurface()) {
            this._startAsyncAgentsPolling();
        } else {
            this._stopAsyncAgentsPolling();
        }
    }

    _startAsyncAgentsPolling() {
        if (this._asyncAgentsPollTimer || !this._open || !this._hasVisibleAsyncAgentsSurface()) return;
        this._asyncAgentsPollTimer = setInterval(() => {
            if (!this._open || !this._hasVisibleAsyncAgentsSurface()) {
                this._stopAsyncAgentsPolling();
                return;
            }
            this.loadAsyncAgents();
        }, 12000);
    }

    _stopAsyncAgentsPolling() {
        if (!this._asyncAgentsPollTimer) return;
        clearInterval(this._asyncAgentsPollTimer);
        this._asyncAgentsPollTimer = null;
    }

    _asyncRows(source = this._asyncSource || 'manual') {
        return source === 'chat' ? (this._chatAsyncRuns || []) : (this._asyncRuns || []);
    }

    _setAsyncSource(source) {
        if (!['manual', 'chat'].includes(source) || source === this._asyncSource) return;
        this._asyncSelections[this._asyncSource || 'manual'] = this._selectedAsyncRunId;
        this._asyncSource = source;
        this._asyncRunDetailRequest?.controller.abort();
        this._asyncRunDetailRequest = null;
        this._asyncRunDetailLoading = false;
        this._asyncRunDetail = null;
        this._asyncRunDetailError = '';
        const rows = this._asyncRows();
        const saved = this._asyncSelections[source];
        this._selectedAsyncRunId = rows.some(run => run.id === saved) ? saved : (rows[0]?.id || '');
        this._renderAsyncAgents();
        if (this._selectedAsyncRunId) void this.loadAsyncRunDetail(this._selectedAsyncRunId, true);
    }

    async loadAsyncAgents(force = false) {
        if (this._asyncRunsLoading) {
            if (force) this._asyncRefreshPending = true;
            return;
        }
        if (!force && this._asyncRunsLoadedAt && Date.now() - this._asyncRunsLoadedAt < 8000) return;
        this._asyncRunsLoading = true;
        this._asyncRunsError = '';
        this._renderAsyncAgents();
        const fetchJson = async url => {
            const res = await fetch(url, { cache: 'no-store' });
            if (!res.ok) throw new Error(await this._responseError(res, 'Tareas de IRIS'));
            return res.json();
        };
        try {
            const suffix = force ? '?force=true' : '';
            const [caps, snapshot] = await Promise.allSettled([
                fetchJson('/api/hermes/capabilities' + suffix),
                fetchJson('/api/hermes/async-agents' + suffix),
            ]);
            this._hermesCapabilities = caps.status === 'fulfilled' ? caps.value : { success: false };
            if (snapshot.status === 'rejected') throw snapshot.reason;
            const data = snapshot.value;
            if (!data.success) throw new Error(data.error || 'No se pudo actualizar el panel.');
            if (this._asyncScope !== undefined && this._asyncScope !== data.scope) {
                this._asyncSelections.chat = '';
                if (this._asyncSource === 'chat') {
                    this._asyncRunDetailRequest?.controller.abort();
                    this._asyncRunDetailRequest = null;
                    this._asyncRunDetailLoading = false;
                    this._asyncRunDetail = null;
                    this._selectedAsyncRunId = '';
                }
            }
            this._asyncScope = data.scope;
            this._asyncSourceStatus = { manual: data.manual || {}, chat: data.chat || {} };
            if (data.manual?.success) this._asyncRuns = data.manual.runs || [];
            if (data.chat?.success) this._chatAsyncRuns = data.chat.runs || [];
            else this._chatAsyncRuns = []; // Never retain a different identity's rows.
            this._asyncRunsAvailable = true;
            const rows = this._asyncRows();
            if (!rows.some(run => run.id === this._selectedAsyncRunId)) {
                this._selectedAsyncRunId = rows[0]?.id || '';
                this._asyncRunDetailRequest?.controller.abort();
                this._asyncRunDetailRequest = null;
                this._asyncRunDetailLoading = false;
                this._asyncRunDetail = null;
            }
            this._asyncRunsLoadedAt = Date.now();
            if (this._selectedAsyncRunId) await this.loadAsyncRunDetail(this._selectedAsyncRunId, force);
        } catch (err) {
            this._asyncRunsError = err?.message || String(err);
        } finally {
            this._asyncRunsLoading = false;
            this._renderAsyncAgents();
            this._renderDashboard();
            if (this._asyncRefreshPending) {
                this._asyncRefreshPending = false;
                void this.loadAsyncAgents(true);
            }
        }
    }

    async loadAsyncRunDetail(runId = '', force = false) {
        const id = String(runId || '').trim();
        const source = this._asyncSource || 'manual';
        if (!id || id !== this._selectedAsyncRunId) return;
        if (this._asyncRunDetailLoading && this._asyncRunDetailRequest?.id === id && this._asyncRunDetailRequest?.source === source) return;
        this._asyncRunDetailRequest?.controller.abort();
        const request = { id, source, controller: new AbortController() };
        this._asyncRunDetailRequest = request;
        const isCurrent = () => this._asyncRunDetailRequest === request && this._selectedAsyncRunId === id && (this._asyncSource || 'manual') === source;
        this._asyncRunDetailLoading = true;
        this._asyncRunDetailError = '';
        this._renderAsyncAgents();
        try {
            const kind = source === 'chat' ? 'delegations' : 'runs';
            const url = `/api/hermes/${kind}/${encodeURIComponent(id)}${force ? '?force=true' : ''}`;
            const res = await fetch(url, { cache: 'no-store', signal: request.controller.signal });
            if (!res.ok) throw new Error(await this._responseError(res, 'Detalle de la tarea'));
            const data = await res.json();
            if (!data.success) throw new Error(data.error || 'No pude leer la tarea.');
            if (isCurrent()) {
                const key = source === 'chat' ? '_chatAsyncRuns' : '_asyncRuns';
                const previous = (this[key] || []).find(run => run.id === id);
                if (data.run) {
                    data.run = { ...data.run, title: previous?.title || data.run.title };
                    this[key] = (this[key] || []).map(run => run.id === id ? { ...run, ...data.run } : run);
                }
                this._asyncRunDetail = data;
                this._renderDashboard?.();
            }
        } catch (err) {
            if (isCurrent() && err?.name !== 'AbortError') this._asyncRunDetailError = err?.message || String(err);
        } finally {
            if (this._asyncRunDetailRequest === request) {
                this._asyncRunDetailRequest = null;
                this._asyncRunDetailLoading = false;
                this._renderAsyncAgents();
            }
        }
    }

    _selectAsyncRun(runId = '') {
        const id = String(runId || '').trim();
        if (!id || this._selectedAsyncRunId === id) return;
        this._selectedAsyncRunId = id;
        this._asyncSelections[this._asyncSource || 'manual'] = id;
        this._asyncRunDetail = null;
        this.loadAsyncRunDetail(id, true);
        this._renderAsyncAgents();
    }

    async _runAsyncAgentAction(action = '', runId = '') {
        const normalized = String(action || '').trim();
        if (!normalized || this._asyncRunAction) return;
        if (normalized === 'refresh') { await this.loadAsyncAgents(true); return; }
        if (normalized === 'compose') {
            this._setAsyncSource('manual');
            this.panel?.querySelector('.os-agents-prompt')?.focus();
            return;
        }
        if (normalized === 'inspect') { this._selectAsyncRun(runId); return; }
        if (normalized === 'create') {
            if (this._asyncSource === 'chat') return;
            const input = this.panel?.querySelector('.os-agents-prompt');
            const prompt = String(input?.value || '').trim();
            if (!prompt) { input?.focus?.(); return; }
            this._asyncRunAction = 'create';
            this._asyncRunsError = '';
            this._renderAsyncAgents();
            try {
                const res = await fetch('/api/hermes/runs', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ prompt, input: prompt, source: 'iris-os' }),
                });
                const data = await res.json().catch(() => ({}));
                if (!res.ok || !data.success) throw new Error(data.error || data.detail || `HTTP ${res.status}`);
                if (input) input.value = '';
                const run = data.run || {};
                if (run.id) {
                    this._asyncRuns = [run, ...this._asyncRuns.filter(existing => existing.id !== run.id)];
                    this._asyncSelections.manual = run.id;
                    if (this._asyncSource === 'manual') {
                        this._asyncRunDetailRequest?.controller.abort();
                        this._asyncRunDetailRequest = null;
                        this._asyncRunDetailLoading = false;
                        this._asyncRunDetail = null;
                        this._selectedAsyncRunId = run.id;
                    }
                }
                this.pushEvent({ state: 'subagent', label: 'Tarea manual creada', detail: run.title || prompt, tool: 'Async Agents' });
                this._asyncRunsLoadedAt = 0;
                if (data.warning) this._asyncRunsError = data.warning;
                else await this.loadAsyncAgents(true);
            } catch (err) {
                this._asyncRunsError = err?.message || String(err);
            } finally {
                this._asyncRunAction = '';
                this._renderAsyncAgents();
            }
            return;
        }
        if (normalized === 'stop' && this._asyncSource !== 'chat') {
            const id = String(runId || this._selectedAsyncRunId || '').trim();
            if (!id) return;
            this._asyncRunAction = `stop:${id}`;
            this._asyncRunDetailError = '';
            this._renderAsyncAgents();
            try {
                const res = await fetch(`/api/hermes/runs/${encodeURIComponent(id)}/stop`, { method: 'POST' });
                const data = await res.json().catch(() => ({}));
                if (!res.ok || !data.success) throw new Error(data.error || data.detail || `HTTP ${res.status}`);
                this.pushEvent({ state: 'waiting', label: 'Detención solicitada', detail: id, tool: 'Async Agents' });
                this._asyncRunsLoadedAt = 0;
                await this.loadAsyncAgents(true);
            } catch (err) {
                this._asyncRunDetailError = err?.message || String(err);
            } finally {
                this._asyncRunAction = '';
                this._renderAsyncAgents();
            }
        }
    }

    async loadMissions(force = false) {
        if (this._missionLoading) return;
        if (!force && this.missionBoard && Date.now() - (this._missionLoadedAt || 0) < 15000) {
            return;
        }
        this._missionLoading = true;
        this.missionError = '';
        this._renderMissionBoard();
        try {
            const params = new URLSearchParams();
            if (this._missionBoardSlug) params.set('board', this._missionBoardSlug);
            const url = params.toString() ? `/api/kanban/board?${params}` : '/api/kanban/board';
            const workerUrl = params.toString() ? `/api/kanban/workers/active?${params}` : '/api/kanban/workers/active';
            const [boardResult, workerResult] = await Promise.allSettled([
                fetch(url),
                fetch(workerUrl, { cache: 'no-store' }),
            ]);
            if (boardResult.status !== 'fulfilled') {
                throw new Error(boardResult.reason?.message || String(boardResult.reason));
            }
            const res = boardResult.value;
            if (!res.ok) {
                throw new Error(await this._responseError(res, 'Tablero de misiones'));
            }
            this.missionBoard = await res.json();
            this._missionWorkersError = '';
            if (workerResult.status === 'fulfilled' && workerResult.value.ok) {
                const workerData = await workerResult.value.json();
                this._missionWorkers = Array.isArray(workerData.workers) ? workerData.workers : [];
            } else if (workerResult.status === 'fulfilled') {
                this._missionWorkers = [];
                this._missionWorkersError = await this._responseError(workerResult.value, 'Workers de Hermes');
            } else {
                this._missionWorkers = [];
                this._missionWorkersError = workerResult.reason?.message || String(workerResult.reason);
            }
            if (!this._missionBoardSlug && this.missionBoard?.board) {
                this._missionBoardSlug = this.missionBoard.board;
            }
            this._missionLoadedAt = Date.now();
        } catch (err) {
            this.missionError = err?.message || String(err);
        } finally {
            this._missionLoading = false;
            this._renderMissionBoard();
        }
    }

    async loadOsSummary() {
        if (this._osSummaryTimer) {
            clearTimeout(this._osSummaryTimer);
            this._osSummaryTimer = null;
        }
        this.loadHermesDiagnostics();
        this.loadGatewayStatus();
        this.loadRuntimeStatus();
        this.loadSystemMetrics();
        this.loadDeviceAvailability();
    }

    async loadRuntimeStatus(force = false) {
        if (!force && this._runtimeStatusLoadedAt && Date.now() - this._runtimeStatusLoadedAt < 10000) return;
        this._runtimeStatusError = '';
        try {
            const res = await fetch('/api/status', { cache: 'no-store' });
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            this._runtimeStatus = await res.json();
        } catch (err) {
            this._runtimeStatus = null;
            this._runtimeStatusError = err?.message || String(err);
        } finally {
            this._runtimeStatusLoadedAt = Date.now();
            this._renderDashboard();
        }
    }

    async loadSystemMetrics(force = false) {
        if (!force && this._systemMetricsLoadedAt && Date.now() - this._systemMetricsLoadedAt < 10000) return;
        this._systemMetricsError = '';
        try {
            const res = await fetch('/api/system-metrics', { cache: 'no-store' });
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            this._systemMetrics = await res.json();
        } catch (err) {
            this._systemMetrics = null;
            this._systemMetricsError = err?.message || String(err);
        } finally {
            this._systemMetricsLoadedAt = Date.now();
            this._renderDashboard();
        }
    }

    async loadDeviceAvailability(force = false) {
        if (!force && this._deviceAvailabilityLoadedAt && Date.now() - this._deviceAvailabilityLoadedAt < 30000) return;
        const mediaDevices = navigator?.mediaDevices;
        if (!mediaDevices?.enumerateDevices) {
            this._deviceAvailability = { supported: false, microphones: null, cameras: null };
            this._deviceAvailabilityLoadedAt = Date.now();
            this._renderDashboard();
            return;
        }
        try {
            const devices = await mediaDevices.enumerateDevices();
            this._deviceAvailability = {
                supported: true,
                microphones: devices.filter(device => device.kind === 'audioinput').length,
                cameras: devices.filter(device => device.kind === 'videoinput').length,
            };
        } catch (err) {
            this._deviceAvailability = { supported: false, microphones: null, cameras: null, error: err?.message || String(err) };
        } finally {
            this._deviceAvailabilityLoadedAt = Date.now();
            this._renderDashboard();
        }
    }

    async loadHermesDiagnostics(force = false) {
        if (this._hermesDiagnosticsLoading) return;
        if (!force && this._hermesDiagnosticsLoadedAt && Date.now() - this._hermesDiagnosticsLoadedAt < 30000) return;
        this._hermesDiagnosticsLoading = true;
        this._hermesDiagnosticsError = '';
        try {
            const url = force ? '/api/hermes/diagnostics?force=true' : '/api/hermes/diagnostics';
            const res = await fetch(url, { cache: 'no-store' });
            if (!res.ok) throw new Error(await this._responseError(res, 'Diagnóstico de Hermes'));
            this._hermesDiagnostics = await res.json();
            this._hermesDiagnosticsLoadedAt = Date.now();
        } catch (err) {
            this._hermesDiagnosticsError = err?.message || String(err);
        } finally {
            this._hermesDiagnosticsLoading = false;
            this._renderDashboard();
        }
    }

    async loadHermesDoctor(force = false) {
        if (this._hermesDoctorLoading) return;
        if (!force && this._hermesDoctorLoadedAt && Date.now() - this._hermesDoctorLoadedAt < 25000) return;
        this._hermesDoctorLoading = true;
        this._hermesDoctorError = '';
        this._renderHermesDoctor();
        try {
            const url = force ? '/api/hermes/doctor?force=true' : '/api/hermes/doctor';
            const res = await fetch(url, { cache: 'no-store' });
            if (!res.ok) throw new Error(await this._responseError(res, 'Diagnóstico'));
            this._hermesDoctor = await res.json();
            this._hermesDoctorLoadedAt = Date.now();
            if (force) {
                const summary = this._hermesDoctor?.summary || {};
                this.pushEvent({
                    state: this._hermesDoctor?.overall_status === 'error' ? 'error' : this._hermesDoctor?.overall_status === 'warning' ? 'waiting' : 'done',
                    label: 'Diagnóstico',
                    detail: summary.label || 'Revisión completa',
                    tool: 'Setup Center',
                });
            }
        } catch (err) {
            this._hermesDoctorError = err?.message || String(err);
        } finally {
            this._hermesDoctorLoading = false;
            this._renderHermesDoctor();
            this._renderDashboard();
        }
    }

    async loadGatewayStatus(force = false) {
        if (this._gatewayLoading) return;
        if (!force && this._gatewayLoadedAt && Date.now() - this._gatewayLoadedAt < 10000) {
            this._loadVisibleGatewayLogs();
            return;
        }
        this._gatewayLoading = true;
        this._gatewayError = '';
        this._renderGatewayManager();
        try {
            const res = await fetch('/api/gateway-status', { cache: 'no-store' });
            if (!res.ok) throw new Error(await this._responseError(res, 'Estado del Gateway'));
            this._gatewayStatus = await res.json();
            this._gatewayLoadedAt = Date.now();
            this._loadVisibleGatewayLogs(force);
        } catch (err) {
            this._gatewayError = err?.message || String(err);
        } finally {
            this._gatewayLoading = false;
            this._renderGatewayManager();
            this._renderDashboard();
        }
    }

    async loadGatewayLogs(force = false) {
        if (this._gatewayLogsLoading) return;
        if (!force && this._gatewayLogsLoadedAt && Date.now() - this._gatewayLogsLoadedAt < 12000) return;
        this._gatewayLogsLoading = true;
        try {
            const res = await fetch('/api/gateway-logs?limit=90', { cache: 'no-store' });
            if (!res.ok) throw new Error(await this._responseError(res, 'Logs del Gateway'));
            this._gatewayLogs = await res.json();
            this._gatewayLogsLoadedAt = Date.now();
        } catch (err) {
            this._gatewayError = `Logs: ${err?.message || String(err)}`;
        } finally {
            this._gatewayLogsLoading = false;
            this._renderGatewayManager();
        }
    }

    _loadVisibleGatewayLogs(force = false) {
        if (this._hasVisibleAppSurface('doctor')) this.loadGatewayLogs(force);
    }

    async _runGatewayAction(action = '') {
        const normalized = String(action || '').trim();
        if (!normalized || this._gatewayAction) return;
        if (normalized === 'refresh') {
            this._gatewayLoadedAt = 0;
            this._gatewayLogsLoadedAt = 0;
            await Promise.allSettled([this.loadGatewayStatus(true), this.loadHermesDoctor(true)]);
            return;
        }
        if (normalized === 'logs') {
            this._gatewayLogsLoadedAt = 0;
            await this.loadGatewayLogs(true);
            this._renderGatewayManager();
            return;
        }
        const endpoint = normalized === 'start'
            ? '/api/start-gateway'
            : normalized === 'stop'
                ? '/api/stop-gateway'
                : normalized === 'restart'
                    ? '/api/restart-gateway'
                    : '';
        if (!endpoint) return;
        this._gatewayAction = normalized;
        this._gatewayError = '';
        this._renderGatewayManager();
        try {
            const res = await fetch(endpoint, { method: 'POST' });
            const data = await res.json().catch(() => ({}));
            if (!res.ok || data.status === 'error') {
                throw new Error(data.message || data.detail || `HTTP ${res.status}`);
            }
            this.pushEvent({
                state: normalized === 'stop' ? 'waiting' : 'job',
                label: `Gateway ${normalized}`,
                detail: data.message || data.detail || 'Accion completada',
                tool: 'Gateway Manager',
            });
        } catch (err) {
            this._gatewayError = err?.message || String(err);
        } finally {
            this._gatewayAction = '';
            this._gatewayLoadedAt = 0;
            this._gatewayLogsLoadedAt = 0;
            await Promise.allSettled([
                this.loadGatewayStatus(true),
                this.loadHermesDiagnostics(true),
                this.loadHermesDoctor(true),
                this.loadRoutines(true),
            ]);
            this._renderGatewayManager();
        }
    }

    async _runDoctorAction(actionId = '') {
        const action = String(actionId || '').trim();
        if (!action) return;
        if (action === 'refresh_doctor') {
            await this.loadHermesDoctor(true);
            return;
        }
        if (action !== 'start_gateway') return;

        this._hermesDoctorError = '';
        this._renderHermesDoctor();
        try {
            await this._runGatewayAction('start');
        } catch (err) {
            this._hermesDoctorError = `Start gateway: ${err?.message || String(err)}`;
        } finally {
            this._hermesDoctorLoadedAt = 0;
            await Promise.allSettled([
                this.loadGatewayStatus(true),
                this.loadHermesDiagnostics(true),
                this.loadHermesDoctor(true),
                this.loadRoutines(true),
            ]);
            this._renderHermesDoctor();
        }
    }

    async loadRoutines(force = false) {
        if (this._cronLoading) return;
        if (!force && this._cronLoadedAt && Date.now() - this._cronLoadedAt < 20000) {
            this._loadSelectedCronHistory();
            return;
        }
        this._cronLoading = true;
        this._cronError = '';
        this._renderRoutines();
        try {
            const jobsRes = await fetch('/api/cron/jobs?include_disabled=true');
            if (!jobsRes.ok) throw new Error(await this._responseError(jobsRes, 'Tareas programadas'));
            const jobsData = await jobsRes.json();
            this._cronJobs = Array.isArray(jobsData.jobs) ? jobsData.jobs : [];
            const previousId = this._selectedCronJobId;
            if (!this._selectedCronJobId && !this._selectedCronOutputId && this._cronJobs.length) {
                this._selectedCronJobId = this._cronJobId(this._cronJobs[0]);
                this._selectedCronOutputId = '';
            }
            if (this._selectedCronJobId && !this._selectedCronOutputId && !this._cronJobs.some(job => this._cronJobId(job) === this._selectedCronJobId)) {
                this._selectedCronJobId = this._cronJobs.length ? this._cronJobId(this._cronJobs[0]) : '';
            }
            if (previousId !== this._selectedCronJobId || !this._selectedCronJobId) this._resetCronJobHistory();
            this._cronLoadedAt = Date.now();
            this._loadSelectedCronHistory(force);
        } catch (err) {
            this._cronError = err?.message || String(err);
        } finally {
            this._cronLoading = false;
            this._renderRoutines();
            this._renderDashboard();
        }
        this.loadCronOutputs(force);
    }

    async loadCronOutputs(force = false) {
        if (this._cronOutputsLoading) return;
        if (!force && this._cronOutputsLoadedAt && Date.now() - this._cronOutputsLoadedAt < 20000) return;
        this._cronOutputsLoading = true;
        this._renderRoutines();
        try {
            const res = await fetch('/api/cron/outputs?limit=8');
            if (!res.ok) throw new Error(await this._responseError(res, 'Salidas de cron'));
            const data = await res.json();
            this._cronOutputs = Array.isArray(data.outputs) ? data.outputs : [];
            this._cronOutputsLoadedAt = Date.now();
        } catch (err) {
            this._cronError = `outputs: ${err?.message || String(err)}`;
        } finally {
            this._cronOutputsLoading = false;
            this._renderRoutines();
            this._renderDashboard();
        }
    }

    async loadCronJobHistory(jobId = '', force = false) {
        const id = String(jobId || '').trim();
        if (!id || id !== this._selectedCronJobId) return;
        if (this._cronJobOutputsLoading && this._cronJobHistoryRequest?.id === id) return;
        if (!force && id === this._cronJobOutputsLoadedId && this._cronJobOutputsLoadedAt && Date.now() - this._cronJobOutputsLoadedAt < 20000) return;
        this._cronJobHistoryRequest?.controller.abort();
        const request = { id, controller: new AbortController() };
        this._cronJobHistoryRequest = request;
        const isCurrent = () => this._cronJobHistoryRequest === request && this._selectedCronJobId === id;
        this._cronJobOutputsLoading = true;
        this._cronJobOutputsError = '';
        this._renderRoutines();
        try {
            const res = await fetch(`/api/cron/jobs/${encodeURIComponent(id)}/outputs?limit=12`, { cache: 'no-store', signal: request.controller.signal });
            if (!res.ok) throw new Error(await this._responseError(res, 'Historial de la rutina'));
            const data = await res.json();
            if (!isCurrent()) return;
            this._cronJobOutputs = Array.isArray(data.outputs) ? data.outputs : [];
            this._cronJobOutputsLoadedId = id;
            this._cronJobOutputsLoadedAt = Date.now();
        } catch (err) {
            if (isCurrent() && err?.name !== 'AbortError') {
                this._cronJobOutputsError = err?.message || String(err);
                this._cronJobOutputs = [];
                this._cronJobOutputsLoadedAt = 0;
            }
        } finally {
            if (this._cronJobHistoryRequest === request) {
                this._cronJobHistoryRequest = null;
                this._cronJobOutputsLoading = false;
                this._renderRoutines();
            }
        }
    }

    _loadSelectedCronHistory(force = false) {
        if (this._hasVisibleAppSurface('routines') && this._selectedCronJobId && !this._selectedCronOutputId) {
            this.loadCronJobHistory(this._selectedCronJobId, force);
        }
    }

    _resetCronJobHistory() {
        this._cronJobHistoryRequest?.controller.abort();
        this._cronJobHistoryRequest = null;
        this._cronJobOutputs = [];
        this._cronJobOutputsLoadedAt = 0;
        this._cronJobOutputsLoadedId = '';
        this._cronJobOutputsLoading = false;
        this._cronJobOutputsError = '';
    }

    _selectCronJob(jobId = '') {
        const id = String(jobId || '').trim();
        if (!id || (id === this._selectedCronJobId && !this._selectedCronOutputId)) return;
        this._selectedCronJobId = id;
        this._selectedCronOutputId = '';
        this._resetCronJobHistory();
        this._renderRoutines();
        this.loadCronJobHistory(id, true);
    }

    _selectCronOutput(outputId = '', jobId = '') {
        const id = String(outputId || '').trim();
        if (!id) return;
        this._selectedCronOutputId = id;
        this._selectedCronJobId = String(jobId || '').trim();
        this._resetCronJobHistory();
        this._renderRoutines();
    }

    async _runCronAction(action = '', jobId = '') {
        const normalized = String(action || '').trim();
        if (!normalized || this._cronAction) return;
        if (normalized === 'refresh') {
            this._cronLoadedAt = 0;
            await this.loadRoutines(true);
            return;
        }
        if (normalized === 'start_gateway') {
            this._cronAction = normalized;
            this._cronError = '';
            this._renderRoutines();
            try {
                await this._runGatewayAction('start');
                this.pushEvent({
                    state: 'job',
                    label: 'Gateway iniciado',
                    detail: 'Cron puede ejecutar rutinas en segundo plano',
                    tool: 'Hermes Gateway',
                });
            } catch (err) {
                this._cronError = `gateway: ${err?.message || String(err)}`;
            } finally {
                this._cronAction = '';
                this._cronLoadedAt = 0;
                await this.loadRoutines(true);
                this.loadHermesDoctor(true);
                this._renderRoutines();
            }
            return;
        }
        if (normalized === 'create') {
            const answer = await irisForm(routineForm('Nueva rutina', {
                name: 'Nueva tarea programada', schedule: 'every 1h', prompt: '',
            }, 'Crear rutina'));
            if (!answer) return;
            const { name, schedule, prompt } = answer;
            this._cronAction = normalized;
            this._cronError = '';
            this._renderRoutines();
            try {
                const res = await fetch('/api/cron/jobs', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name, schedule, prompt }),
                });
                const data = await res.json().catch(() => ({}));
                if (!res.ok || data.success === false) {
                    throw new Error(data.detail || data.error || data.message || `HTTP ${res.status}`);
                }
                this.pushEvent({
                    state: 'job',
                    label: 'Rutina creada',
                    detail: name || schedule,
                    tool: 'Hermes Cron',
                });
            } catch (err) {
                this._cronError = `create: ${err?.message || String(err)}`;
            } finally {
                this._cronAction = '';
                this._cronLoadedAt = 0;
                await this.loadRoutines(true);
                this._renderRoutines();
            }
            return;
        }
        const id = String(jobId || this._selectedCronJobId || '').trim();
        if (!id) return;
        if (normalized === 'edit') {
            const job = this._cronJobs.find(item => this._cronJobId(item) === id) || {};
            const answer = await irisForm(routineForm('Editar rutina', {
                name: this._cronJobName(job), schedule: this._cronScheduleLabel(job),
                prompt: String(job.prompt || job.body || ''),
            }, 'Guardar cambios'));
            if (!answer) return;
            const { name, schedule, prompt } = answer;
            this._cronAction = normalized;
            this._cronError = '';
            this._renderRoutines();
            try {
                const res = await fetch(`/api/cron/jobs/${encodeURIComponent(id)}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name, schedule, prompt }),
                });
                const data = await res.json().catch(() => ({}));
                if (!res.ok || data.success === false) {
                    throw new Error(data.detail || data.error || data.message || `HTTP ${res.status}`);
                }
                this.pushEvent({
                    state: 'done',
                    label: 'Cron actualizado',
                    detail: name || id,
                    tool: 'Hermes Cron',
                });
            } catch (err) {
                this._cronError = `edit: ${err?.message || String(err)}`;
            } finally {
                this._cronAction = '';
                this._cronLoadedAt = 0;
                await this.loadRoutines(true);
                this._renderRoutines();
            }
            return;
        }
        const endpoint = normalized === 'pause'
            ? `/api/cron/jobs/${encodeURIComponent(id)}/pause`
            : normalized === 'resume'
                ? `/api/cron/jobs/${encodeURIComponent(id)}/resume`
                : normalized === 'run'
                    ? `/api/cron/jobs/${encodeURIComponent(id)}/run`
                    : normalized === 'delete'
                        ? `/api/cron/jobs/${encodeURIComponent(id)}`
                        : '';
        if (!endpoint) return;
        if (normalized === 'delete') {
            const job = this._cronJobs.find(item => this._cronJobId(item) === id) || {};
            const ok = await irisConfirm({
                title: 'Eliminar rutina', icon: 'trash', tone: 'danger',
                message: `Se eliminará la rutina «${this._cronJobName(job)}» del motor de IA.`,
                question: '¿Deseas continuar?', confirmLabel: 'Eliminar', confirmIcon: 'trash',
            });
            if (!ok) return;
        }
        this._cronAction = normalized;
        this._cronError = '';
        this._renderRoutines();
        try {
            const res = await fetch(endpoint, { method: normalized === 'delete' ? 'DELETE' : 'POST' });
            const data = await res.json().catch(() => ({}));
            if (!res.ok || data.success === false) {
                throw new Error(data.detail || data.error || data.message || `HTTP ${res.status}`);
            }
            this.pushEvent({
                state: normalized === 'run' ? 'job' : 'done',
                label: `Cron ${normalized}`,
                detail: this._cronJobName(this._cronJobs.find(job => this._cronJobId(job) === id) || { id }),
                tool: 'Hermes Cron',
            });
            if (normalized === 'delete') this._selectedCronJobId = '';
        } catch (err) {
            this._cronError = `${normalized}: ${err?.message || String(err)}`;
        } finally {
            this._cronAction = '';
            this._cronLoadedAt = 0;
            await this.loadRoutines(true);
            this._renderRoutines();
        }
    }

    async _speakCronOutput(outputId = '') {
        const output = [...this._cronOutputs, ...this._cronJobOutputs].find(item => item.id === outputId);
        if (!output?.text) return;
        try {
            const response = await fetch('/api/cron/speak', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    text: output.text,
                    name: output.name || output.job_id || 'Tarea programada',
                    emotion: output.has_error ? 'concerned' : 'neutral',
                    animation: 'idle',
                }),
            });
            if (!response.ok) {
                throw new Error(await this._responseError(response, 'Narración de la rutina'));
            }
        } catch (err) {
            this._cronError = `speak: ${err?.message || String(err)}`;
            this._renderRoutines();
        }
    }

    async loadMcpCatalog(force = false) {
        if (this._mcpLoading) return;
        if (!force && this._mcpLoadedAt && Date.now() - this._mcpLoadedAt < 45000) return;
        this._mcpLoading = true;
        this._mcpError = '';
        this._renderMcpCatalog();
        try {
            const url = force ? '/api/mcp/catalog?force=true' : '/api/mcp/catalog';
            const res = await fetch(url);
            if (!res.ok) throw new Error(await this._responseError(res, 'Catálogo MCP'));
            const data = await res.json();
            this._mcpSummary = data;
            this._mcpCatalog = Array.isArray(data.catalog) ? data.catalog : [];
            this._mcpLoadedAt = Date.now();
        } catch (err) {
            this._mcpError = err?.message || String(err);
        } finally {
            this._mcpLoading = false;
            this._renderMcpCatalog();
            this._renderSkillsMarketplace();
            this._renderDashboard();
        }
    }

    async loadSkills(force = false) {
        if (this._skillsLoading) return;
        if (!force && this._skillsLoadedAt && Date.now() - this._skillsLoadedAt < 60000) return;
        this._skillsLoading = true;
        this._skillsError = '';
        this._renderSkills();
        try {
            const res = await fetch(`/api/skills/list${force ? '?force=true' : ''}`, { cache: 'no-store' });
            if (!res.ok) throw new Error(await this._responseError(res, 'Skills de Hermes'));
            const data = await res.json();
            this._skillsCatalog = Array.isArray(data.skills) ? data.skills : [];
            this._skillsLoadedAt = Date.now();
        } catch (err) {
            this._skillsError = err?.message || String(err);
        } finally {
            this._skillsLoading = false;
            this._renderSkills();
            this._renderDashboard();
        }
    }

    async loadMarketplace(force = false) {
        await Promise.all([
            this.loadSkillsMarketplace({ force, defer: false }),
            this.loadMcpCatalog(force),
        ]);
        this._renderSkillsMarketplace();
    }

    async loadSkillsMarketplace({
        force = false,
        defer = true,
        page = 1,
        append = false,
        query = this._skillsMarketQuery,
    } = {}) {
        const normalizedQuery = String(query || '').trim();
        if (
            !force
            && !append
            && page === 1
            && normalizedQuery === this._skillsMarketLastQuery
            && this._skillsMarketLoadedAt
            && Date.now() - this._skillsMarketLoadedAt < 300000
        ) return;
        if (defer) {
            await new Promise(resolve => setTimeout(resolve, 180));
            if (!this._open || !this._hasVisibleAppSurface('marketplace')) return;
        }
        this._skillsMarketAbort?.abort?.();
        const controller = new AbortController();
        const requestToken = ++this._skillsMarketRequestToken;
        this._skillsMarketAbort = controller;
        this._skillsMarketLoading = true;
        this._skillsMarketError = '';
        this._renderSkillsMarketplace();
        try {
            const params = new URLSearchParams({
                page: String(Math.max(1, Number(page) || 1)),
                size: '60',
                source: 'all',
            });
            if (normalizedQuery) params.set('q', normalizedQuery);
            if (force) params.set('force', 'true');
            const res = await fetch(`/api/skills/marketplace?${params}`, {
                cache: 'no-store',
                signal: controller.signal,
            });
            if (!res.ok) throw new Error(await this._responseError(res, 'Marketplace de skills'));
            const data = await res.json();
            if (requestToken !== this._skillsMarketRequestToken) return;
            this._skillsMarketSummary = data || {};
            const incoming = Array.isArray(data.catalog) ? data.catalog : [];
            if (append && !normalizedQuery) {
                const merged = new Map(this._skillsMarket.map((entry) => [entry.id || entry.identifier || entry.name, entry]));
                incoming.forEach((entry) => merged.set(entry.id || entry.identifier || entry.name, entry));
                this._skillsMarket = [...merged.values()];
            } else {
                this._skillsMarket = incoming;
            }
            this._skillsMarketPage = Number(data?.hub?.hub_page || page || 1);
            this._skillsMarketPages = Number(data?.hub?.hub_pages || 1);
            this._skillsMarketLastQuery = normalizedQuery;
            if (data.success === false) {
                this._skillsMarketError = data.error || 'Marketplace no disponible.';
            }
            this._skillsMarketLoadedAt = Date.now();
        } catch (err) {
            if (err?.name === 'AbortError') return;
            this._skillsMarketError = err?.message || String(err);
        } finally {
            if (requestToken === this._skillsMarketRequestToken) {
                this._skillsMarketLoading = false;
                this._skillsMarketAbort = null;
                this._renderSkillsMarketplace();
                this._renderDashboard();
            }
        }
    }

    async _createMission() {
        const titleInput = this.panel?.querySelector('.mission-title-input');
        const assigneeInput = this.panel?.querySelector('.mission-assignee-input');
        const title = titleInput?.value?.trim() || '';
        const assignee = assigneeInput?.value?.trim() || '';
        if (!title || this._missionLoading) return;
        this._missionLoading = true;
        this.missionError = '';
        this._renderMissionBoard();
        try {
            const payload = { title, assignee };
            if (this._missionBoardSlug) payload.board = this._missionBoardSlug;
            const res = await fetch('/api/kanban/tasks', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            if (!res.ok) {
                throw new Error(await this._responseError(res, 'Crear misión'));
            }
            titleInput.value = '';
            if (assigneeInput) assigneeInput.value = '';
            this.pushEvent({
                state: 'job',
                label: 'Mision creada',
                detail: title,
                tool: 'Hermes kanban',
            });
            this._missionLoading = false;
            await this.loadMissions(true);
        } catch (err) {
            this.missionError = err?.message || String(err);
        } finally {
            this._missionLoading = false;
            this._renderMissionBoard();
        }
    }

    async _openMissionDetail(taskId = '') {
        const id = String(taskId || '').trim();
        if (!id || this._missionDetailLoading) return;
        this._missionDetailLoading = true;
        this.missionDetailError = '';
        this.missionDetail = { task: { id, title: 'Cargando mision...' } };
        this._renderMissionBoard();
        try {
            const params = new URLSearchParams();
            if (this._missionBoardSlug) params.set('board', this._missionBoardSlug);
            const query = params.toString() ? `?${params}` : '';
            const res = await fetch(`/api/kanban/tasks/${encodeURIComponent(id)}${query}`);
            if (!res.ok) {
                throw new Error(await this._responseError(res, 'Detalle de la misión'));
            }
            this.missionDetail = await res.json();
        } catch (err) {
            this.missionDetailError = err?.message || String(err);
        } finally {
            this._missionDetailLoading = false;
            this._renderMissionBoard();
        }
    }

    _closeMissionDetail() {
        this.missionDetail = null;
        this.missionDetailError = '';
        this._renderMissionBoard();
    }

    handleStatus(status) {
        const normalized = String(status || '').toLowerCase();
        if (normalized === 'thinking') {
            this.pushEvent({ state: 'thinking', label: 'IRIS pensando', detail: 'IRIS está procesando la solicitud' });
        } else if (normalized === 'listening') {
            this.pushEvent({ state: 'waiting', label: 'Escuchando', detail: 'Esperando entrada del usuario' });
        } else if (normalized === 'idle' || normalized === 'connected' || normalized === 'ready') {
            this.pushEvent({ state: 'idle', label: 'Sistema listo', detail: 'Sin tareas activas' });
        }
    }

    handleUserMessage(text = '') {
        const summary = String(text || '').replace(/\s+/g, ' ').trim().slice(0, 92);
        this.pushEvent({
            state: 'thinking',
            label: 'Tarea enviada al motor de IA',
            detail: summary ? `Esperando actividad real: ${summary}` : 'Esperando actividad real del runtime',
            tool: 'Hermes runtime',
        });
    }

    handleSpeechStart(payload = {}) {
        this.pushEvent({
            state: 'speaking',
            label: 'IRIS respondiendo',
            detail: payload.text || payload.emotion || 'Respuesta en curso',
            tool: payload.animacion_virtual ? `anim: ${payload.animacion_virtual}` : '',
        });
    }

    handleSpeechEnd() {
        if (this.state === 'speaking') {
            this.pushEvent({ state: 'done', label: 'Respuesta finalizada', detail: 'IRIS terminó de hablar' });
        }
    }

    handleToolProgress(payload = {}) {
        const state = inferStateFromTool(payload);
        const rawTool = payload.tool || payload.name || payload.event || 'Hermes tool';
        const tool = normalizeToolName(rawTool, payload);
        const detail = normalizeToolDetail(payload, state);
        this.pushEvent({
            state,
            label: tool,
            detail,
            tool,
            rawStatus: payload.status || '',
        });
    }

    pushEvent({ state = 'tool', label = '', detail = '', tool = '', rawStatus = '' } = {}) {
        const nextState = STATE_META[state] ? state : 'tool';
        const last = this.timeline[this.timeline.length - 1];
        if (
            last
            && last.state === nextState
            && last.label === label
            && last.detail === (detail || STATE_META[nextState].detail)
            && Date.now() - (last.ts || 0) < 1200
        ) {
            return;
        }
        this.state = nextState;
        this.currentTool = tool || (nextState === 'idle' ? 'none' : label);
        this.currentDetail = detail || STATE_META[nextState].detail;
        if (!['idle', 'speaking', 'done'].includes(nextState)) {
            this._lastNonIdleState = nextState;
            this._lastNonIdleTool = this.currentTool;
            this._lastNonIdleDetail = this.currentDetail;
        }
        this.timeline = clampTimeline([
            ...this.timeline,
            {
                id: `${Date.now()}-${Math.random().toString(16).slice(2)}`,
                ts: Date.now(),
                time: nowLabel(),
                state: nextState,
                label: label || STATE_META[nextState].label,
                detail: this.currentDetail,
                tool: this.currentTool,
                rawStatus,
            },
        ]);
        this._applyMonitorMaterials();
        this._drawMonitor();
        this._renderPanel();
    }

    update(delta = 0) {
        const dt = Number.isFinite(delta) ? Math.max(0, Math.min(delta, 1)) : 0;
        this._time += dt;
        this._monitorDrawElapsed = Math.min(1, this._monitorDrawElapsed + dt);
        if ((typeof document !== 'undefined' && document.hidden) || this.room?.group?.visible === false) {
            this._monitorDirty = true;
            return;
        }
        const reduced = this._monitorReducedMotion?.matches === true;
        const interval = this.state === 'idle' ? 0.5 : (this.room?.quality === 'low' ? 0.25 : 1 / 12);
        // Status events still draw immediately. Only decorative animation is
        // paced here; no catch-up loop or extra timer when a page wakes up.
        if (this._monitorDirty || reduced !== this._monitorDrawReduced
            || (!reduced && this._monitorDrawElapsed + 1e-8 >= interval)) {
            this._drawMonitor();
        }
    }

    _applyMonitorMaterials() {
        const meta = STATE_META[this.state] || STATE_META.idle;
        const active = this.state !== 'idle';
        for (const screen of this._monitorMeshes) {
            if (!screen?.material) continue;
            screen.userData.monitorActive = active;
            if (screen.material.emissive) {
                screen.material.emissive.set(meta.glow);
                screen.material.emissiveIntensity = active ? 0.82 : 0.24;
            }
            if (screen.material.opacity !== undefined) {
                screen.material.opacity = active ? 0.96 : 0.86;
            }
        }
        const glow = this.room?.getObject?.('monitor_glow');
        if (glow) {
            glow.color?.set(meta.glow);
            const intensity = active ? 1.28 : 0.72;
            if (this.room?.setLightIntensity) this.room.setLightIntensity(glow, intensity);
            else glow.intensity = intensity;
            glow.distance = active ? 4.2 : 3.2;
        }
    }

    _drawMonitor() {
        this._monitorDirty = true;
        if ((typeof document !== 'undefined' && document.hidden) || this.room?.group?.visible === false) return;
        const ctx = this._ctx;
        if (!ctx || !this._monitorMeshes.length) return;
        const w = this._canvas.width;
        const h = this._canvas.height;
        const meta = STATE_META[this.state] || STATE_META.idle;
        const reduced = this._monitorReducedMotion?.matches === true;
        const time = reduced ? 0 : this._time;
        const pulse = 0.5 + Math.sin(time * 3.2) * 0.5;

        ctx.clearRect(0, 0, w, h);
        const bg = ctx.createLinearGradient(0, 0, w, h);
        bg.addColorStop(0, '#03111b');
        bg.addColorStop(0.45, '#061626');
        bg.addColorStop(1, '#120618');
        ctx.fillStyle = bg;
        ctx.fillRect(0, 0, w, h);

        ctx.globalAlpha = 0.14;
        ctx.strokeStyle = meta.accent;
        ctx.lineWidth = 1;
        for (let x = 36; x < w; x += 54) {
            ctx.beginPath();
            ctx.moveTo(x, 0);
            ctx.lineTo(x - 80, h);
            ctx.stroke();
        }
        ctx.globalAlpha = 1;

        ctx.fillStyle = 'rgba(255,255,255,0.78)';
        ctx.font = '600 24px Outfit, sans-serif';
        ctx.fillText('ACTIVIDAD DE IRIS', 42, 54);
        ctx.fillStyle = meta.accent;
        ctx.font = '800 54px Syne, sans-serif';
        ctx.fillText(meta.label.toUpperCase(), 42, 125);

        ctx.strokeStyle = meta.accent;
        ctx.lineWidth = 3;
        ctx.globalAlpha = 0.45 + pulse * 0.25;
        ctx.beginPath();
        ctx.arc(w - 136, 116, 52 + pulse * 7, 0, Math.PI * 2);
        ctx.stroke();
        ctx.globalAlpha = 0.22;
        ctx.beginPath();
        ctx.arc(w - 136, 116, 82, -time * 0.4, Math.PI * 1.35 - time * 0.4);
        ctx.stroke();
        ctx.globalAlpha = 1;

        ctx.fillStyle = 'rgba(188,238,255,0.86)';
        ctx.font = '500 22px Outfit, sans-serif';
        const detail = (this.currentDetail || meta.detail).slice(0, 64);
        ctx.fillText(detail, 42, 168);

        const recent = this.timeline.slice(-4).reverse();
        let y = 230;
        for (const item of recent) {
            const itemMeta = STATE_META[item.state] || STATE_META.tool;
            ctx.fillStyle = 'rgba(8,18,29,0.72)';
            ctx.fillRect(42, y - 28, w - 84, 44);
            ctx.fillStyle = itemMeta.accent;
            ctx.fillRect(42, y - 28, 4, 44);
            ctx.fillStyle = 'rgba(255,255,255,0.72)';
            ctx.font = '600 17px Outfit, sans-serif';
            ctx.fillText(`${item.time}  ${item.label}`.slice(0, 54), 60, y);
            y += 52;
        }

        this._texture.needsUpdate = true;
        this._monitorDrawElapsed = 0;
        this._monitorDirty = false;
        this._monitorDrawReduced = reduced;
    }

    _renderPanel() {
        if (!this.panel) return;
        if (!this._open) return;
        this._renderDesktopStatus();
        const meta = STATE_META[this.state] || STATE_META.idle;
        this.panel.style.setProperty('--workbench-state-accent', meta.accent);
        this.panel.style.setProperty('--workbench-accent', this._osTheme?.accent || meta.accent);
        const stateEl = this.panel.querySelector('.workbench-state');
        const detailEl = this.panel.querySelector('.workbench-detail');
        const toolEl = this.panel.querySelector('.workbench-tool');
        const countEl = this.panel.querySelector('.workbench-count');
        const listEl = this.panel.querySelector('.workbench-timeline');
        if (stateEl) stateEl.textContent = meta.label;
        if (detailEl) detailEl.textContent = this.currentDetail || meta.detail;
        if (toolEl) toolEl.textContent = this.currentTool || 'none';
        if (countEl) countEl.textContent = `${this.timeline.length} eventos`;
        if (listEl) {
            listEl.innerHTML = this.timeline.slice(-12).reverse().map((item) => {
                const itemMeta = STATE_META[item.state] || STATE_META.tool;
                return `
                    <li style="--event-color:${itemMeta.accent}">
                        <span>${item.time}</span>
                        <div>
                            <strong>${this._escape(item.label)}</strong>
                            <p>${this._escape(item.detail || '')}</p>
                        </div>
                    </li>
                `;
            }).join('');
        }
        this._renderVisibleAppSurfaces();
    }

    _renderVisibleAppSurfaces() {
        if (this._activeTab === 'desktop') this._renderDashboard();
        if (this._hasVisibleAppSurface('camera')) this._renderVisionAssistant();
        if (this._hasVisibleAppSurface('routines')) this._renderRoutines();
        if (this._hasVisibleAppSurface('doctor')) {
            this._renderHermesDoctor();
            this._renderGatewayManager();
        }
        if (this._hasVisibleAppSurface('tools')) this._renderMcpCatalog();
        if (this._hasVisibleAppSurface('skills')) this._renderSkills();
        if (this._hasVisibleAppSurface('marketplace')) this._renderSkillsMarketplace();
        if (this._hasVisibleAppSurface('player')) {
            this._playerApp?.setHermesConnected?.(this._gatewayRuntimeState().healthy === true);
        }
        if (this._hasVisibleAppSurface('missions')) this._renderMissionBoard();
    }

    _gatewayRuntimeState() {
        const status = this._gatewayStatus || {};
        const healthStatus = Number(status.health_status || status.healthStatus || 0);
        let healthy = status.healthy === true || healthStatus === 200;
        let running = status.running === true || healthy || status.open === true;
        const cronSummary = this._hermesDiagnostics?.commands?.cron?.summary || {};
        const diagnosticsGateway = String(this._hermesDiagnostics?.status?.gateway_service || '').trim();
        const diagnosticsLower = diagnosticsGateway.toLowerCase();
        const diagnosticsRunning = cronSummary.gateway_running === true
            || diagnosticsLower.includes('ready')
            || diagnosticsLower.includes('running')
            || diagnosticsLower.includes('activo')
            || diagnosticsLower.includes('health ok')
            || diagnosticsLower.includes('linked');
        const diagnosticsStopped = cronSummary.gateway_running === false
            || diagnosticsLower.includes('stopped')
            || diagnosticsLower.includes('detenido')
            || diagnosticsLower.includes('down');
        const hasProbe = !!this._gatewayStatus
            && ('running' in status || 'healthy' in status || 'health_status' in status || 'healthStatus' in status);
        if (!hasProbe && diagnosticsRunning) {
            running = true;
            healthy = true;
        }
        const stopped = hasProbe ? (!running && !healthy) : diagnosticsStopped;
        const label = this._gatewayError
            ? 'Error'
            : this._gatewayAction
                ? `${({ start: 'Iniciando IRIS…', restart: 'Reiniciando IRIS…', stop: 'Deteniendo el servicio de IA…' })[this._gatewayAction] || 'Procesando…'}`
                : running
                    ? (healthy ? 'Ready' : 'Port only')
                    : stopped
                        ? 'Stopped'
                        : diagnosticsGateway
                            ? diagnosticsGateway.split(/[(-]/)[0].trim()
                            : 'Not checked';
        return {
            status,
            healthStatus,
            healthy,
            running,
            stopped,
            diagnosticsStopped,
            diagnosticsGateway,
            cronSummary,
            label,
        };
    }

    _renderDashboard() {
        if (!this.panel) return;
        const meta = STATE_META[this.state] || STATE_META.idle;
        const last = this.timeline[this.timeline.length - 1];
        const missionTasks = Array.isArray(this.missionBoard?.tasks) ? this.missionBoard.tasks : [];
        const pendingMissions = missionTasks.filter(t => !['done', 'archived'].includes(t.status));
        const activeMissions = pendingMissions.length;
        const activeCronJobs = this._cronJobs.filter(job => !job.disabled && job.enabled !== false);
        const activeCron = activeCronJobs.length;
        const installedMcp = this._mcpSummary?.installed_count ?? this._mcpCatalog.filter(entry => entry.installed).length;
        const mcpSource = this._mcpSummary?.source_mode === 'hermes-cli'
            ? 'Hermes CLI'
            : this._mcpSummary?.source_mode === 'filesystem-fallback'
                ? 'fallback local'
                : 'Hermes';
        const totalSkills = this._skillsCatalog.length;
        const readySkills = this._skillsCatalog.filter(skill => skill.eligible).length;
        const asyncRuns = [...(this._asyncRuns || []), ...(this._chatAsyncRuns || [])];
        const activeAsyncRuns = asyncRuns.filter(run => ['running', 'waiting'].includes(this._runState(run))).length;
        const progressByState = {
            idle: 18,
            waiting: 32,
            thinking: 52,
            tool: 64,
            subagent: 72,
            job: 76,
            speaking: 86,
            done: 100,
        };
        const operationProgress = progressByState[this.state] ?? 48;
        const projectLoad = this._projectLoadPercent(operationProgress, {
            activeMissions,
            activeCron,
            installedMcp,
            totalSkills,
        });
        const activeTitle = this.currentTool && this.currentTool !== 'none'
            ? this.currentTool
            : (this.state === 'idle' ? 'Escritorio de Iris' : meta.label);
        const activeDetail = this.currentDetail || meta.detail || 'Sistema en espera.';
        const activeTool = this.currentTool && this.currentTool !== 'none' ? this.currentTool : 'ninguna';
        const footerMessage = this.state === 'idle'
            ? 'IRIS OS en calma. Lista para coordinar actividad, rutinas y herramientas.'
            : this.state === 'speaking'
                ? `IRIS está respondiendo: ${activeDetail}`.slice(0, 132)
                : this.state === 'done'
                    ? `Última operación completada: ${activeDetail}`.slice(0, 132)
                    : `Operación activa: ${activeTool} · ${activeDetail}`.slice(0, 132);
        const now = new Date();
        const dateLabel = now.toLocaleDateString('es-CO', { weekday: 'short', month: 'short', day: '2-digit' }).replace('.', '');
        const timeLabel = now.toLocaleTimeString('es-CO', { hour: '2-digit', minute: '2-digit' });
        const gatewayRuntime = this._gatewayRuntimeState();
        const runtimeChecked = this._runtimeStatusLoadedAt > 0;
        const metricsChecked = this._systemMetricsLoadedAt > 0;
        const runtimeReady = this._runtimeStatus?.status === 'running' && !this._runtimeStatusError;
        const gatewayReady = gatewayRuntime.running && gatewayRuntime.healthy && !gatewayRuntime.stopped && !this._gatewayError;
        const metricPercent = (key) => {
            const value = Number(this._systemMetrics?.[key]);
            return this._systemMetrics?.available && Number.isFinite(value)
                ? Math.min(100, Math.max(0, Math.round(value)))
                : null;
        };
        const cpuUsage = metricPercent('cpu_percent');
        const memoryUsage = metricPercent('memory_percent');
        const diskUsage = metricPercent('disk_percent');
        const deviceStatus = this._deviceAvailability || {};
        const microphoneCount = Number.isFinite(deviceStatus.microphones) ? deviceStatus.microphones : null;
        const cameraCount = Number.isFinite(deviceStatus.cameras) ? deviceStatus.cameras : null;
        const microphoneActive = !!this._wakeRuntime?.active || !!this._manualVoiceRuntime?.active;
        const cameraActive = !!this._cameraStream;
        const setText = (selector, value) => {
            this.panel.querySelectorAll(selector).forEach((node) => {
                if (node.textContent !== String(value)) node.textContent = value;
            });
        };
        const setHtml = (selector, value) => {
            this.panel.querySelectorAll(selector).forEach((node) => {
                node.innerHTML = value;
            });
        };
        setText('.os-activity-brief', last ? `${last.label}: ${last.detail}`.slice(0, 78) : 'Sin actividad reciente');
        setText('.os-agents-brief', this._asyncRunsError
            ? 'No se pudieron actualizar las tareas'
            : activeAsyncRuns
                ? `${activeAsyncRuns} tareas activas`
                : asyncRuns.length
                    ? `${asyncRuns.length} tareas recientes`
                    : 'Tareas manuales y del chat');
        setText('.os-mission-brief', this.missionError ? 'Kanban sin respuesta' : `${activeMissions} misiones activas`);
        const cronGatewayDown = gatewayRuntime.stopped;
        const gatewayLabel = gatewayRuntime.label;

        setText('.os-cron-brief', this._cronError
            ? 'Cron sin respuesta'
            : cronGatewayDown
                ? 'Gateway detenido. Cron no dispara rutinas'
                : `${activeCron} rutinas activas`);
        setText('.os-mcp-brief', this._mcpError ? 'Catálogo no disponible' : `${this._mcpCatalog.length} disponibles · ${installedMcp} instalados (${mcpSource})`);
        setText('.os-skills-brief', this._skillsError
            ? 'Habilidades no disponibles'
            : totalSkills
                ? `${readySkills}/${totalSkills} listas · el asistente decide cuándo usarlas`
                : 'Skills instaladas');
        const marketplaceCount = this._skillsMarket.length + this._mcpCatalog.length;
        setText('.os-marketplace-brief', this._skillsMarketError && this._mcpError
            ? 'Catálogo no disponible'
            : `${marketplaceCount} extensiones descubiertas`);
        const doctorSummary = this._hermesDoctor?.summary || {};
        const doctorIssueCount = Number(doctorSummary.issue_count || 0);
        setText('.os-doctor-brief', this._hermesDoctorError
            ? 'Doctor sin respuesta'
            : this._hermesDoctorLoading
                ? 'Ejecutando diagnóstico...'
                : this._hermesDoctor
                    ? `${doctorSummary.label || 'Revisado'} · ${doctorIssueCount} avisos`
                    : 'Centro de diagnóstico');
        setText('.os-state-readout', meta.label);
        this._renderDesktopStatus();
        setText('.os-tool-readout', activeTool);
        setText('.os-queue-readout', `${this.timeline.length}`);
        setText('.os-active-title', activeTitle);
        setText('.os-active-detail', activeDetail);
        setText('.os-active-progress', `${projectLoad}%`);
        setText('.os-current-tool-chip', `Herramienta: ${activeTool}`);
        setText('.os-mcp-chip', `MCP: ${installedMcp}/${this._mcpCatalog.length}`);
        setText('.os-pending-count', `${activeMissions}`);
        setText('.os-date-readout', dateLabel);
        setText('.os-time-readout', timeLabel);
        setText('.os-cpu-readout', cpuUsage === null ? 'N/D' : `${cpuUsage}%`);
        setText('.os-cpu-usage', cpuUsage === null ? 'N/D' : `${cpuUsage}%`);
        setText('.os-memory-usage', memoryUsage === null ? 'N/D' : `${memoryUsage}%`);
        setText('.os-disk-usage', diskUsage === null ? 'N/D' : `${diskUsage}%`);
        setText('.os-metrics-state', this._systemMetrics?.available ? 'Local' : metricsChecked ? 'No disponible' : 'Comprobando');
        setText('.os-network-readout', runtimeReady ? 'En línea local' : runtimeChecked ? 'Sin conexión' : 'Comprobando');
        setText('.os-mic-readout', microphoneActive ? 'Escuchando' : microphoneCount === null ? 'N/D' : microphoneCount > 0 ? 'Apagado' : 'No detectado');
        setText('.os-camera-readout', cameraActive ? 'Activa' : cameraCount === null ? 'N/D' : cameraCount > 0 ? 'Apagada' : 'No detectada');
        setText('.os-profile-core-status', runtimeReady ? 'En línea local' : runtimeChecked ? 'Sin conexión' : 'Comprobando');
        this.panel.querySelectorAll('.os-profile-core-status').forEach((node) => {
            node.classList.remove('is-ready', 'is-offline', 'is-checking');
            node.classList.add(runtimeReady ? 'is-ready' : runtimeChecked ? 'is-offline' : 'is-checking');
        });
        setText('.os-home-readiness-kicker', gatewayReady ? 'Sistema listo' : runtimeReady ? 'IRIS OS local' : runtimeChecked ? 'Sin conexión local' : 'Comprobando sistema');
        setText('.os-home-readiness-detail', gatewayReady ? 'IRIS OS y su motor de IA disponibles' : runtimeReady ? 'La conexión de IA no está lista' : runtimeChecked ? 'No se pudo verificar el runtime' : 'Consultando servicios locales');
        setText('.os-system-summary', gatewayReady
            ? 'IRIS OS y su motor de IA están disponibles.'
            : runtimeReady
                ? 'IRIS OS está activo; la conexión de IA requiere atención.'
                : runtimeChecked
                    ? 'El runtime local no respondió. Algunas funciones no están disponibles.'
                    : 'Comprobando el runtime y los servicios locales.');
        setText('.os-footer-message', footerMessage);
        const gatewayServiceState = this._gatewayError
            ? 'error'
            : gatewayRuntime.running && gatewayRuntime.healthy && !gatewayRuntime.stopped
                ? 'ready'
                : gatewayRuntime.stopped
                    ? 'stopped'
                    : gatewayRuntime.running
                        ? 'warning'
                    : 'unknown';
        const gatewayServiceLabel = gatewayServiceState === 'ready'
            ? 'Listo'
            : gatewayServiceState === 'stopped'
                ? 'Detenido'
                : gatewayLabel;
        setText('.os-service-gateway-status', gatewayServiceLabel);
        this.panel.querySelectorAll('.os-service-gateway-status').forEach((node) => {
            node.classList.remove('is-ready', 'is-warning', 'is-stopped', 'is-error', 'is-linked', 'is-unknown', 'is-checking');
            node.classList.add(`is-${gatewayServiceState}`);
        });

        const setServiceState = (selector, label, state) => {
            setText(selector, label);
            this.panel.querySelectorAll(selector).forEach((node) => {
                node.classList.remove('is-ready', 'is-warning', 'is-stopped', 'is-error', 'is-linked', 'is-unknown', 'is-checking');
                node.classList.add(`is-${state}`);
            });
        };
        setServiceState('.os-service-camera-status', cameraActive ? 'Activa' : cameraCount === null ? 'N/D' : cameraCount > 0 ? 'Apagada' : 'No detectada', cameraActive ? 'ready' : cameraCount === null ? 'unknown' : cameraCount > 0 ? 'stopped' : 'warning');
        setServiceState('.os-service-mic-status', microphoneActive ? 'Escuchando' : microphoneCount === null ? 'N/D' : microphoneCount > 0 ? 'Apagado' : 'No detectado', microphoneActive ? 'ready' : microphoneCount === null ? 'unknown' : microphoneCount > 0 ? 'stopped' : 'warning');
        setServiceState('.os-service-memory-status', runtimeReady ? 'Disponible' : runtimeChecked ? 'Sin conexión' : 'Comprobando', runtimeReady ? 'ready' : runtimeChecked ? 'error' : 'checking');
        setServiceState('.os-service-skills-status', this._skillsError ? 'No disponible' : runtimeReady ? (totalSkills ? `${totalSkills} cargadas` : 'Disponible') : runtimeChecked ? 'Sin conexión' : 'Comprobando', this._skillsError || (runtimeChecked && !runtimeReady) ? 'error' : runtimeReady ? 'ready' : 'checking');

        this.panel.querySelectorAll('.os-active-ring').forEach((node) => {
            node.style.setProperty('--progress', `${projectLoad}%`);
        });
        this.panel.querySelectorAll('.os-metric-rings div').forEach((node, index) => {
            const value = [cpuUsage, memoryUsage, diskUsage][index] ?? 0;
            node.style.setProperty('--metric', `${value}%`);
        });

        setHtml('.os-pending-list', pendingMissions.length
            ? pendingMissions.slice(0, 4).map((task) => {
                const title = task.title || task.name || task.id || 'Tarea de IRIS';
                const status = task.status || task.column || 'pending';
                return `<article><b>${this._escape(title)}</b><span>${this._escape(status)}</span></article>`;
            }).join('')
            : '<article class="is-empty"><b>Sin misiones pendientes</b><span>clear</span></article>');

        const completed = [...this.timeline].reverse().find(item => ['done', 'job'].includes(item.state)) || last;
        setText('.os-last-state', completed?.state === 'done' ? 'Completado' : (completed?.state ? 'Activo' : 'Listo'));
        setText('.os-last-execution', completed
            ? `${completed.label}: ${completed.detail || 'sin detalle'}`.slice(0, 110)
            : 'Sistema en espera.');

        const nextRoutine = this._nextCronJob();
        const nextRoutineName = nextRoutine?.name || nextRoutine?.title || nextRoutine?.id || '';
        const nextRoutineSchedule = nextRoutine?.schedule || nextRoutine?.cron || nextRoutine?.when || '';
        setText('.os-routine-state', activeCron ? 'Programado' : 'Cron');
        setText('.os-next-routine', nextRoutineName
            ? `${nextRoutineName} · ${this._cronNextLabel(nextRoutine)}${nextRoutineSchedule ? ` (${nextRoutineSchedule})` : ''}`.slice(0, 120)
            : 'Sin rutina activa.');

        setHtml('.os-desktop-log', this.timeline.length
            ? this.timeline.slice(-5).reverse().map((item) => {
                const itemMeta = STATE_META[item.state] || STATE_META.tool;
                return `<article style="--event-color:${itemMeta.accent}"><b>${this._escape(item.label)}</b><span>${this._escape(item.time)}</span></article>`;
            }).join('')
            : '<article class="is-empty"><b>Sin eventos recientes</b><span>idle</span></article>');
    }

    _projectLoadPercent(operationProgress = 18, context = {}) {
        const memory = performance?.memory || null;
        const used = Number(memory?.usedJSHeapSize || 0);
        const limit = Number(memory?.jsHeapSizeLimit || 0);
        const memoryPressure = limit > 0
            ? Math.min(96, Math.max(8, Math.round((used / limit) * 100)))
            : null;
        const activityPressure = Math.min(96, Math.max(
            12,
            Math.round(
                operationProgress * 0.62
                + Number(context.activeMissions || 0) * 3
                + Number(context.activeCron || 0) * 3
                + Number(context.installedMcp || 0) * 4
                + (Number(context.totalSkills || 0) ? 4 : 0)
            )
        ));
        if (memoryPressure === null) return activityPressure;
        return Math.min(96, Math.max(8, Math.round(memoryPressure * 0.68 + activityPressure * 0.32)));
    }

    _cronJobId(job = {}) {
        return String(job.id || job.job_id || job.name || job.title || '').trim();
    }

    _cronJobName(job = {}) {
        return String(job.name || job.title || job.prompt || job.id || job.job_id || 'Tarea programada').trim();
    }

    _cronNextTime(job = {}) {
        const value = job.next_run_at || job.next_run || job.next || job.next_fire_time || job.run_at || '';
        if (!value) return Number.POSITIVE_INFINITY;
        const numeric = Number(value);
        if (Number.isFinite(numeric) && numeric > 0) {
            return numeric > 9999999999 ? numeric : numeric * 1000;
        }
        const parsed = Date.parse(String(value));
        return Number.isFinite(parsed) ? parsed : Number.POSITIVE_INFINITY;
    }

    _nextCronJob() {
        return this._cronJobs
            .filter(job => this._cronEnabled(job))
            .slice()
            .sort((a, b) => this._cronNextTime(a) - this._cronNextTime(b))[0] || null;
    }

    _cronEnabled(job = {}) {
        return !(job.disabled || job.enabled === false || String(job.status || '').toLowerCase() === 'paused');
    }

    _cronJobState(job = {}) {
        if (!this._cronEnabled(job)) return 'paused';
        if (job.last_delivery_error || job.delivery_error || job.error) return 'error';
        const raw = String(job.status || job.state || job.last_status || '').toLowerCase();
        if (raw.includes('error') || raw.includes('fail')) return 'error';
        if (raw.includes('running') || raw.includes('active')) return raw;
        return 'active';
    }

    _cronScheduleLabel(job = {}) {
        return formatCronSchedule(job);
    }

    _cronNextLabel(job = {}) {
        const value = job.next_run || job.next_run_at || job.next || job.next_fire_time || '';
        if (!value) return 'Sin próxima ejecución reportada';
        const numeric = Number(value);
        if (Number.isFinite(numeric) && numeric > 1000) {
            const ms = numeric > 9999999999 ? numeric : numeric * 1000;
            return new Date(ms).toLocaleString([], { dateStyle: 'short', timeStyle: 'short' });
        }
        return String(value);
    }

    _cronLastLabel(job = {}) {
        const value = job.last_run_at || job.last_run || job.updated_at || job.completed_at || '';
        if (!value) return 'Sin historial reportado';
        const numeric = Number(value);
        if (Number.isFinite(numeric) && numeric > 1000) {
            const ms = numeric > 9999999999 ? numeric : numeric * 1000;
            return new Date(ms).toLocaleString([], { dateStyle: 'short', timeStyle: 'short' });
        }
        return String(value);
    }

    _cronDeliveryLabel(job = {}) {
        if (job.last_delivery_error || job.delivery_error) return `Error: ${job.last_delivery_error || job.delivery_error}`;
        return job.delivery_status || job.deliver || job.output_status || 'Entrega local/Hermes';
    }

    _cronOutputRow(output = {}, compact = false) {
        const name = output.job_name || output.name || output.id || 'Entrega';
        const summary = output.summary || output.text || output.result || output.path || 'Resultado guardado por Hermes';
        const status = output.status || (output.has_error ? 'error' : 'completed');
        const delivery = output.delivery_status || (output.has_error ? 'error' : 'local-output');
        const selected = output.id && output.id === this._selectedCronOutputId;
        return `
            <article class="os-cron-output-row ${compact ? 'compact' : ''} ${selected ? 'selected' : ''}" data-state="${this._escape(status)}" data-cron-output-id="${this._escapeAttr(output.id || '')}" data-cron-job-id="${this._escapeAttr(output.job_id || '')}">
                <span>${status === 'error' ? 'ERR' : 'OUT'}</span>
                <div>
                    <strong>${this._escape(name)}</strong>
                    <p>${this._escape(String(summary).slice(0, compact ? 110 : 180))}</p>
                    <small>
                        <em>${this._escape(output.run_time || output.time || 'sin fecha')}</em>
                        <b>${this._escape(delivery)}</b>
                    </small>
                </div>
                ${output.text ? `<button type="button" data-cron-speak="${this._escapeAttr(output.id || '')}">Narrar</button>` : ''}
            </article>
        `;
    }

    _renderRoutines() {
        if (!this.panel) return;
        const status = this.panel.querySelector('.os-cron-status');
        const jobsList = this.panel.querySelector('.os-cron-list');
        const selectedEl = this.panel.querySelector('.os-cron-selected');
        const actionEl = this.panel.querySelector('.os-cron-actions');
        const jobOutputsList = this.panel.querySelector('.os-cron-job-output-list');
        const outputsList = this.panel.querySelector('.os-cron-output-list');
        const gatewayRuntime = this._gatewayRuntimeState();
        const cronGatewayDown = gatewayRuntime.stopped;
        if (status) {
            status.classList.toggle('error', !!this._cronError || cronGatewayDown);
            status.textContent = this._cronError
                ? `No pude leer cron: ${this._cronError}`
                : cronGatewayDown
                    ? 'Gateway detenido: las rutinas se pueden crear/listar, pero no se ejecutan solas.'
                : this._cronLoading
                    ? 'Sincronizando tareas programadas…'
                    : this._cronOutputsLoading
                        ? `${this._cronJobs.length} routines · leyendo entregas recientes...`
                        : `${this._cronJobs.length} routines · ${this._cronOutputs.length} recent outputs`;
        }
        if (jobsList) {
            if (this._cronError) {
                jobsList.innerHTML = '<div class="os-empty">El programador de tareas no respondió. Revisa la conexión de IA.</div>';
        } else if (!this._cronJobs.length && !this._cronLoading) {
                jobsList.innerHTML = '<div class="os-empty">Sin rutinas todavía. Usa Nueva rutina o créalas desde Command Center por chat.</div>';
            } else {
                jobsList.innerHTML = this._cronJobs.slice(0, 8).map((job) => {
                    const id = this._cronJobId(job);
                    const name = this._cronJobName(job);
                    const state = this._cronJobState(job);
                    const selected = id === this._selectedCronJobId;
                    const schedule = this._cronScheduleLabel(job);
                    return `
                        <article class="os-row os-cron-job-card ${selected ? 'selected' : ''}" data-state="${this._escape(state)}" data-cron-job-id="${this._escapeAttr(id)}">
                            <span>${state === 'active' ? 'ON' : 'PA'}</span>
                            <div>
                                <strong>${this._escape(name)}</strong>
                                <p>${this._escape(schedule)}</p>
                                <small>Next: ${this._escape(this._cronNextLabel(job))}</small>
                                <small>Entrega: ${this._escape(this._cronDeliveryLabel(job)).slice(0, 72)}</small>
                            </div>
                        </article>
                    `;
                }).join('');
            }
        }
        const selectedJob = this._cronJobs.find(job => this._cronJobId(job) === this._selectedCronJobId) || null;
        const selectedOutput = !selectedJob && this._selectedCronOutputId
            ? this._cronOutputs.find(output => output.id === this._selectedCronOutputId) || null
            : null;
        if (selectedEl) {
            if (!selectedJob && !selectedOutput) {
                selectedEl.innerHTML = '<div class="os-empty">Selecciona una rutina activa para editarla, o una entrega histórica para inspeccionarla.</div>';
            } else if (selectedOutput) {
                selectedEl.innerHTML = `
                    <article class="os-cron-selected-card" data-state="historical">
                        <span>HIST</span>
                        <div>
                            <strong>${this._escape(selectedOutput.name || selectedOutput.job_id || 'Entrega histórica')}</strong>
                            <p>Esta entrega existe en cron/output, pero la rutina ya no está en jobs.json.</p>
                            <small>Job ID histórico: ${this._escape(selectedOutput.job_id || 'desconocido')}</small>
                            <small>Fecha: ${this._escape(selectedOutput.run_time || 'sin fecha')}</small>
                            <small>Editar/eliminar no aplica a entregas históricas.</small>
                        </div>
                    </article>
                `;
            } else {
                const state = this._cronJobState(selectedJob);
                selectedEl.innerHTML = `
                    <article class="os-cron-selected-card" data-state="${this._escape(state)}">
                        <span>${this._cronEnabled(selectedJob) ? 'RUN' : 'PAUSED'}</span>
                        <div>
                            <strong>${this._escape(this._cronJobName(selectedJob))}</strong>
                            <p>${this._escape(this._cronScheduleLabel(selectedJob))}</p>
                            <small>Próxima ejecución: ${this._escape(this._cronNextLabel(selectedJob))}</small>
                            <small>Última ejecución: ${this._escape(this._cronLastLabel(selectedJob))}</small>
                            <small>Entrega: ${this._escape(this._cronDeliveryLabel(selectedJob)).slice(0, 120)}</small>
                        </div>
                    </article>
                `;
            }
        }
        if (actionEl) {
            const busy = this._cronAction ? ' disabled' : '';
            const globalControls = `
                <button class="is-primary" type="button" data-cron-action="create"${busy}>Nueva rutina</button>
                <button class="is-muted" type="button" data-cron-action="refresh"${busy}>Actualizar</button>
                ${cronGatewayDown ? `<button class="is-primary" type="button" data-cron-action="start_gateway"${busy}>Iniciar IRIS</button>` : ''}
            `;
            if (!selectedJob) {
                actionEl.innerHTML = `
                    <div class="os-cron-action-strip">
                        ${globalControls}
                        <button type="button" disabled>Ejecutar ahora</button>
                        <button type="button" disabled>Pausar</button>
                        <button type="button" disabled>Editar</button>
                        <button type="button" disabled>Eliminar</button>
                    </div>
                    ${selectedOutput ? '<p class="os-inline-hint">Seleccionaste una entrega histórica. Para editar/eliminar debe existir una rutina activa en jobs.json.</p>' : ''}
                `;
            } else {
                const id = this._cronJobId(selectedJob);
                const enabled = this._cronEnabled(selectedJob);
                actionEl.innerHTML = `
                    <div class="os-cron-action-strip">
                        ${globalControls}
                        <button type="button" data-cron-action="run" data-cron-job-id="${this._escapeAttr(id)}"${busy}>Ejecutar ahora</button>
                        <button type="button" data-cron-action="${enabled ? 'pause' : 'resume'}" data-cron-job-id="${this._escapeAttr(id)}"${busy}>${enabled ? 'Pausar' : 'Reanudar'}</button>
                        <button type="button" data-cron-action="edit" data-cron-job-id="${this._escapeAttr(id)}"${busy}>Editar</button>
                        <button class="is-danger" type="button" data-cron-action="delete" data-cron-job-id="${this._escapeAttr(id)}"${busy}>Eliminar</button>
                    </div>
                `;
            }
        }
        if (jobOutputsList) {
            if (this._cronJobOutputsError) {
                jobOutputsList.innerHTML = `<div class="os-empty">No pude leer historial: ${this._escape(this._cronJobOutputsError)}</div>`;
            } else if (this._cronJobOutputsLoading) {
                jobOutputsList.innerHTML = '<div class="os-empty">Leyendo historial de rutina...</div>';
            } else if (!this._cronJobOutputs.length) {
                jobOutputsList.innerHTML = '<div class="os-empty">Sin entregas para esta rutina todavía.</div>';
            } else {
                jobOutputsList.innerHTML = this._cronJobOutputs.slice(0, 6).map(output => this._cronOutputRow(output)).join('');
            }
        }
        if (outputsList) {
            if (this._cronOutputsLoading) {
                outputsList.innerHTML = '<div class="os-empty">Leyendo entregas históricas...</div>';
            } else if (!this._cronOutputs.length) {
                outputsList.innerHTML = '<div class="os-empty">Aun no hay entregas locales.</div>';
            } else {
                outputsList.innerHTML = this._cronOutputs.slice(0, 5).map(output => this._cronOutputRow(output, true)).join('');
            }
        }
    }

    _renderGatewayManager() {
        if (!this.panel) return;
        const runtime = this._gatewayRuntimeState();
        const status = runtime.status || {};
        const running = runtime.running;
        const healthy = runtime.healthy;
        const manager = this.panel.querySelector('.os-gateway-manager');
        const stateEl = this.panel.querySelector('.os-gateway-state');
        const portEl = this.panel.querySelector('.os-gateway-port');
        const deliveryEl = this.panel.querySelector('.os-gateway-delivery');
        const cronEl = this.panel.querySelector('.os-gateway-cron');
        const logEl = this.panel.querySelector('.os-gateway-log');
        const actions = this.panel.querySelector('.os-gateway-actions');
        const stateName = this._gatewayError ? 'error' : running && healthy ? 'running' : running ? 'warning' : 'stopped';
        if (manager) manager.dataset.state = stateName;
        if (stateEl) {
            stateEl.textContent = this._gatewayError
                ? `Error: ${this._gatewayError}`
                : this._gatewayAction
                    ? `${({ start: 'Iniciando IRIS…', restart: 'Reiniciando IRIS…', stop: 'Deteniendo el servicio de IA…' })[this._gatewayAction] || 'Procesando…'}`
                    : this._gatewayLoading
                        ? 'Verificando...'
                        : running
                            ? (healthy ? 'Running - Health OK' : 'Port only - revisa health')
                            : 'Stopped';
            stateEl.dataset.state = stateName;
        }
        if (portEl) {
            portEl.textContent = runtime.healthStatus
                ? `${status.port || 8642} / health ${runtime.healthStatus}`
                : String(status.port || 8642);
        }
        if (deliveryEl) {
            const recent = this._cronOutputs?.[0];
            const cronWithDeliveryError = this._cronJobs.find(job => job.last_delivery_error || job.delivery_error);
            deliveryEl.textContent = recent
                ? (recent.has_error ? 'Error reciente' : recent.delivery_status || 'Local output')
                : cronWithDeliveryError
                    ? `Error: ${cronWithDeliveryError.last_delivery_error || cronWithDeliveryError.delivery_error}`.slice(0, 120)
                : 'Sin entregas';
        }
        if (cronEl) {
            cronEl.textContent = running
                ? `${this._cronJobs.filter(job => this._cronEnabled(job)).length} activas`
                : 'No ejecuta';
        }
        if (actions) {
            actions.querySelectorAll('button').forEach((btn) => {
                const action = btn.dataset.gatewayAction || '';
                btn.disabled = !!this._gatewayAction
                    || (action === 'start' && running)
                    || ((action === 'stop' || action === 'restart') && !running);
            });
        }
        if (logEl) {
            const lines = Array.isArray(this._gatewayLogs?.lines) ? this._gatewayLogs.lines : [];
            const sources = Array.isArray(this._gatewayLogs?.sources)
                ? this._gatewayLogs.sources.filter(source => source.exists).map(source => source.label).join(', ')
                : '';
            const hint = this._gatewayLogs?.message || (running ? 'Gateway activo. Sin logs recientes.' : 'Gateway detenido. Usa Iniciar para levantarlo.');
            logEl.textContent = this._gatewayError
                ? this._gatewayError
                : status.health_error && running && status.healthy !== true
                    ? `Health: ${status.health_error}`
                : lines.length
                    ? `${sources ? `Sources: ${sources}\n` : ''}${lines.slice(-16).join('\n')}`
                    : hint;
        }
    }

    _renderHermesDoctor() {
        if (!this.panel) return;
        const report = this._hermesDoctor || null;
        const rawSummary = report?.summary || {};
        const gatewayRuntime = this._gatewayRuntimeState();
        const gatewayReady = !!(gatewayRuntime.running && gatewayRuntime.healthy && !gatewayRuntime.stopped && !this._gatewayError);
        const rawChecks = Array.isArray(report?.checks) ? report.checks : [];
        const rawRecommendations = Array.isArray(report?.recommendations) ? report.recommendations : [];
        const quickActions = Array.isArray(report?.quick_actions) ? report.quick_actions : [];
        const isGatewayCronIssue = (item = {}) => {
            const text = [
                item.id,
                item.label,
                item.title,
                item.summary,
                item.detail,
                item.message,
                item.recommendation,
            ].filter(Boolean).join(' ').toLowerCase();
            const concernsRuntime = text.includes('gateway') || text.includes('cron') || text.includes('routine') || text.includes('rutina');
            const saysStopped = text.includes('detenido') || text.includes('stopped') || text.includes('not running')
                || text.includes('no se ejecut') || text.includes('no dispar') || text.includes('no trigger')
                || text.includes('will not fire') || text.includes('apagado');
            return concernsRuntime && saysStopped;
        };
        const checks = gatewayReady
            ? rawChecks.map((check) => isGatewayCronIssue(check)
                ? {
                    ...check,
                    status: 'ok',
                    summary: 'Gateway activo y respondiendo health 200.',
                    detail: 'Gateway activo y respondiendo health 200.',
                    recommendation: '',
                }
                : check)
            : rawChecks;
        const recommendations = gatewayReady
            ? rawRecommendations.filter((rec) => !isGatewayCronIssue(typeof rec === 'string' ? { detail: rec } : rec))
            : rawRecommendations;
        const criticalCount = checks.filter(check => ['critical', 'error', 'failed', 'fail'].includes(String(check.status || '').toLowerCase())).length;
        const warningCount = checks.filter(check => ['warning', 'warn'].includes(String(check.status || '').toLowerCase())).length;
        const summary = {
            ...rawSummary,
            issue_count: criticalCount + warningCount,
            critical_count: criticalCount,
            warning_count: warningCount,
        };
        const statusValue = report ? (criticalCount ? 'error' : warningCount ? 'warning' : 'ok') : '';
        const status = this.panel.querySelector('.os-doctor-status');
        const actions = this.panel.querySelector('.os-doctor-actions');
        const checksList = this.panel.querySelector('.os-doctor-checks');
        const recList = this.panel.querySelector('.os-doctor-recommendations');
        const compatibility = report?.compatibility || {};
        const compatibilitySummary = compatibility.summary || {};
        const compatibilityVersion = compatibility.version || {};
        const compatibilityRibbon = this.panel.querySelector('.os-compatibility-ribbon');
        const setText = (selector, value) => {
            this.panel.querySelectorAll(selector).forEach((node) => {
                if (node.textContent !== String(value)) node.textContent = value;
            });
        };

        setText('.os-doctor-title', report ? (summary.label || 'Motor de IA revisado') : 'Sin diagnóstico');
        setText('.os-doctor-detail', report
            ? (summary.headline || summary.description || 'Revisión local completada sin exponer secretos.')
            : 'Ejecuta Diagnóstico para revisar el motor de IA sin mostrar secretos ni tokens.');
        setText('.os-doctor-issue-count', `${summary.issue_count ?? 0}`);
        setText('.os-doctor-critical', `${summary.critical_count ?? 0}`);
        setText('.os-doctor-warnings', `${summary.warning_count ?? 0}`);
        setText('.os-doctor-check-total', `${checks.length || 0}`);
        setText('.os-doctor-check-state', statusValue ? statusValue.toUpperCase() : 'Standby');
        setText('.os-doctor-rec-count', `${recommendations.length || 0}`);
        setText('.os-compatibility-label', report
            ? (compatibilitySummary.label || 'Compatibilidad sin datos')
            : 'Pendiente de revisión');
        setText('.os-compatibility-detail', report
            ? (compatibilitySummary.headline || 'IRIS no pudo completar la evaluación de compatibilidad.')
            : 'Doctor verificará plataforma, instalación y versión antes de activar funciones avanzadas.');
        setText('.os-compatibility-minimum', report
            ? `≥ ${compatibilityVersion.minimum || compatibilitySummary.minimum_version || '—'}`
            : '—');
        if (compatibilityRibbon) {
            compatibilityRibbon.dataset.state = report
                ? (compatibility.overall_status || 'warning')
                : 'standby';
        }

        if (status) {
            status.classList.toggle('error', !!this._hermesDoctorError || statusValue === 'error');
            status.classList.toggle('is-warning', !this._hermesDoctorError && statusValue === 'warning');
            status.textContent = this._hermesDoctorError
                ? `Doctor no respondió: ${this._hermesDoctorError}`
                : this._hermesDoctorLoading
                    ? 'Ejecutando diagnóstico…'
                    : report
                        ? `${summary.label || 'Revisión completa'} · ${summary.issue_count ?? 0} avisos`
                        : 'Doctor listo para ejecutar.';
        }

        if (actions) {
            const startGateway = gatewayReady ? null : quickActions.find(action => action?.id === 'start_gateway');
            const actionButtons = [
                '<button class="os-refresh os-doctor-refresh" type="button" data-doctor-action="refresh_doctor">Actualizar</button>',
                startGateway
                    ? `<button type="button" data-doctor-action="start_gateway"${startGateway.enabled === false ? ' disabled' : ''}>${'Iniciar IRIS'}</button>`
                    : '',
            ].filter(Boolean).join('');
            actions.innerHTML = actionButtons || '<span class="os-doctor-muted">Sin acciones rápidas.</span>';
        }

        if (checksList) {
            if (this._hermesDoctorError) {
                checksList.innerHTML = '<div class="os-empty">El diagnóstico no respondió. Revisa la conexión de IA o la instalación local.</div>';
            } else if (!checks.length && !this._hermesDoctorLoading) {
                checksList.innerHTML = '<div class="os-empty">Ejecuta Diagnóstico para revisar el motor de IA.</div>';
            } else {
                checksList.innerHTML = checks.map((check) => {
                    const checkStatus = String(check.status || 'info').toLowerCase();
                    const title = check.label || check.id || 'check';
                    const detail = check.summary || check.detail || check.message || '';
                    const rec = check.recommendation || '';
                    return `
                        <article class="os-doctor-check" data-state="${this._escape(checkStatus)}">
                            <span>${this._escape(checkStatus.slice(0, 2).toUpperCase())}</span>
                            <div>
                                <strong>${this._escape(title)}</strong>
                                <p>${this._escape(detail)}</p>
                                ${rec ? `<small>${this._escape(rec)}</small>` : ''}
                            </div>
                        </article>
                    `;
                }).join('');
            }
        }

        if (recList) {
            if (!recommendations.length) {
                recList.innerHTML = report
                    ? '<div class="os-empty">Sin recomendaciones críticas. El motor de IA está estable.</div>'
                    : '<div class="os-empty">Las recomendaciones aparecerán después del diagnóstico.</div>';
            } else {
                recList.innerHTML = recommendations.map((rec) => {
                    const item = typeof rec === 'string' ? { title: 'Sugerencia', detail: rec } : (rec || {});
                    const severity = String(item.severity || item.status || 'info').toLowerCase();
                    return `
                        <article class="os-doctor-recommendation" data-state="${this._escape(severity)}">
                            <strong>${this._escape(item.title || item.label || 'Recomendación')}</strong>
                            <p>${this._escape(item.detail || item.message || item.summary || '')}</p>
                        </article>
                    `;
                }).join('');
            }
        }
    }

    _renderMcpCatalog() {
        if (!this.panel) return;
        const status = this.panel.querySelector('.os-mcp-status');
        const list = this.panel.querySelector('.os-mcp-list');
        const installedEntries = this._mcpCatalog.filter((entry) => entry.installed);
        if (status) {
            status.classList.toggle('error', !!this._mcpError);
            const installed = this._mcpSummary?.installed_count ?? installedEntries.length;
            const sourceLabel = this._mcpSummary?.source_mode === 'hermes-cli'
                ? 'Hermes CLI'
                : this._mcpSummary?.source_mode === 'filesystem-fallback'
                    ? 'fallback local'
                    : 'Hermes';
            status.textContent = this._mcpError
                ? `No pude leer MCP: ${this._mcpError}`
                : this._mcpLoading
                    ? 'Leyendo MCP configurados en Hermes...'
                    : `${sourceLabel}: ${installed} instalados · ${this._mcpCatalog.length} en catálogo`;
        }
        if (!list) return;
        if (this._mcpError) {
            list.innerHTML = '<div class="os-empty">El catálogo MCP no respondió. Actualiza Hermes o reinicia IRIS.</div>';
            return;
        }
        if (!installedEntries.length && !this._mcpLoading) {
            list.innerHTML = '<div class="os-empty">No hay MCP instalados. Explora Marketplace para revisar opciones y requisitos reales.</div>';
            return;
        }
        list.innerHTML = installedEntries.map((entry) => {
            const state = entry.enabled === false ? 'paused' : 'installed';
            const desc = entry.description || `${entry.transport || 'unknown'} · auth ${entry.auth || 'none'}`;
            return `
                <article class="os-row" data-state="${this._escape(state)}">
                    <span>${entry.enabled === false ? 'OFF' : 'ON'}</span>
                    <div>
                        <strong>${this._escape(entry.name || 'mcp')}</strong>
                        <p>${this._escape(desc)}</p>
                    </div>
                </article>
            `;
        }).join('');
    }

    _renderSkills() {
        if (!this.panel) return;
        const skills = this._skillsCatalog || [];
        const total = skills.length;
        const ready = skills.filter(skill => skill.eligible).length;
        const setup = total - ready;
        const sources = new Set(skills.map(skill => skill.source || 'local')).size;
        const status = this.panel.querySelector('.os-skills-status');
        const list = this.panel.querySelector('.os-skills-list');

        const setText = (selector, value) => {
            this.panel.querySelectorAll(selector).forEach((node) => {
                if (node.textContent !== String(value)) node.textContent = value;
            });
        };
        setText('.os-skills-total', `${total}`);
        setText('.os-skills-ready', `${ready}`);
        setText('.os-skills-setup', `${setup}`);
        setText('.os-skills-sources', `${sources}`);

        if (status) {
            status.classList.toggle('error', !!this._skillsError);
            status.textContent = this._skillsError
                ? `No pude leer skills: ${this._skillsError}`
                : this._skillsLoading
                    ? 'Sincronizando habilidades…'
                    : `${total} skills detectadas · ${ready} ready · ${setup} requieren configuración`;
        }
        if (!list) return;
        if (this._skillsError) {
            list.innerHTML = '<div class="os-empty">El catálogo de habilidades no respondió. Revisa la conexión de IA en Diagnóstico.</div>';
            return;
        }
        if (!total && !this._skillsLoading) {
            list.innerHTML = '<div class="os-empty">No hay Skills instaladas. Explora Marketplace para descubrir capacidades compatibles.</div>';
            return;
        }

        const query = this._skillsQuery.trim().toLowerCase();
        const filtered = skills.filter((skill) => {
            if (this._skillsOnlyEligible && !skill.eligible) return false;
            if (!query) return true;
            return `${skill.name || ''} ${skill.description || ''} ${skill.source || ''}`.toLowerCase().includes(query);
        });

        if (!filtered.length && !this._skillsLoading) {
            list.innerHTML = '<div class="os-empty">Ninguna skill coincide con el filtro.</div>';
            return;
        }

        list.innerHTML = filtered.map((skill) => {
            const name = skill.name || 'skill';
            const source = skill.source || 'local';
            const state = skill.eligible ? 'ready' : 'setup';
            const desc = skill.description || 'Sin descripción disponible.';
            const id = name || source;
            const expanded = this._skillsExpanded === id;
            return `
                <article class="os-skill-row" data-state="${this._escape(state)}" data-expanded="${expanded ? 'true' : 'false'}" data-skill-id="${this._escapeAttr(id)}">
                    <span class="os-skill-emoji">${this._escape(skill.emoji || '*')}</span>
                    <div>
                        <strong>${this._escape(name)}</strong>
                        <p>${this._escape(desc)}</p>
                        <small>
                            <b>${this._escape(source)}</b>
                            <em>${skill.eligible ? 'Lista para usar' : 'requiere configuración'}</em>
                            <em>uso automático</em>
                        </small>
                    </div>
                    <span class="os-skill-state-chip">${skill.eligible ? 'Lista' : 'Configurar'}</span>
                </article>
            `;
        }).join('');
    }

    _renderSkillsMarketplace() {
        if (!this.panel) return;
        const entries = marketplaceEntries(this._skillsMarket, this._mcpCatalog);
        const summary = marketplaceSummary(entries);
        const status = this.panel.querySelector('.os-market-status');
        const list = this.panel.querySelector('.os-marketplace-list');
        const categorySelect = this.panel.querySelector('.os-market-category');
        const stateSelect = this.panel.querySelector('.os-market-state');
        const kind = this._marketplaceKind || 'all';
        const categoryEntries = kind === 'all'
            ? entries
            : entries.filter((entry) => entry.kind === kind);

        const categories = [...new Set(categoryEntries.map((entry) => entry.category || 'general'))]
            .sort((a, b) => String(a).localeCompare(String(b)));

        if (categorySelect) {
            const current = this._skillsMarketCategory || 'all';
            const nextHtml = [
                '<option value="all">Todas las categorías</option>',
                ...categories.map((category) =>
                    `<option value="${this._escapeAttr(category)}">${this._escape(category)}</option>`
                ),
            ].join('');
            if (categorySelect.innerHTML !== nextHtml) {
                categorySelect.innerHTML = nextHtml;
            }
            categorySelect.value = categories.includes(current) ? current : 'all';
            this._skillsMarketCategory = categorySelect.value || 'all';
        }
        if (stateSelect && stateSelect.value !== this._marketplaceState) {
            stateSelect.value = this._marketplaceState || 'all';
        }
        this.panel.querySelectorAll('[data-market-kind]').forEach((button) => {
            const active = button.dataset.marketKind === kind;
            button.classList.toggle('active', active);
            button.setAttribute('aria-pressed', active ? 'true' : 'false');
        });

        const setText = (selector, value) => {
            this.panel.querySelectorAll(selector).forEach((node) => {
                if (node.textContent !== String(value)) node.textContent = value;
            });
        };
        const activeQuery = this._skillsMarketQuery.trim();
        const advertisedSkills = activeQuery
            ? summary.skills
            : Math.max(summary.skills, Number(this._skillsMarketSummary?.available_count || 0));
        const advertisedTotal = advertisedSkills + summary.mcps;
        const metricNumber = (value) => Number(value || 0) >= 10_000
            ? new Intl.NumberFormat('es-CO', { notation: 'compact', maximumFractionDigits: 1 })
                .format(value).replace(/\s+/g, '')
            : Number(value || 0).toLocaleString('es-CO');
        setText('.os-market-total', metricNumber(advertisedTotal));
        setText('.os-market-skills', metricNumber(advertisedSkills));
        setText('.os-market-mcps', `${summary.mcps}`);
        setText('.os-market-installed', `${summary.installed}`);

        const filtered = filterMarketplaceEntries(entries, {
            kind,
            state: this._marketplaceState,
            category: this._skillsMarketCategory,
            query: this._skillsMarketQuery,
        });
        const page = marketplacePage(filtered, this._marketplaceVisibleLimit);

        const remoteHasMore = (
            !activeQuery
            && this._skillsMarketPage < this._skillsMarketPages
        );
        if (status) {
            const errors = [this._skillsMarketError, this._mcpError].filter(Boolean);
            status.classList.toggle('error', errors.length > 0);
            status.textContent = this._marketplaceNotice
                || (this._skillsMarketLoading || this._mcpLoading
                    ? 'Sincronizando habilidades y conexiones MCP…'
                    : errors.length
                        ? `Catálogo parcial: ${errors.join(' · ')}`
                        : activeQuery
                            ? `${page.total} coincidencias para “${activeQuery}” · mostrando ${page.visible.length}${this._skillsMarketSummary?.hub?.search_limited ? ' · primeros resultados de Hermes' : ''}`
                            : `${advertisedSkills.toLocaleString('es-CO')} skills en Hermes · ${this._skillsMarket.length} cargadas · ${summary.installed} ya instalados`);
        }
        if (!list) return;
        if (!entries.length && (this._skillsMarketError || this._mcpError)) {
            list.innerHTML = '<div class="os-empty">Marketplace no pudo leer los catálogos. Abre Diagnóstico y vuelve a sincronizar.</div>';
            return;
        }
        if (!entries.length && !this._skillsMarketLoading && !this._mcpLoading) {
            list.innerHTML = '<div class="os-empty">El motor de IA todavía no anunció habilidades ni conexiones MCP disponibles.</div>';
            return;
        }
        if (!filtered.length && !this._skillsMarketLoading) {
            list.innerHTML = '<div class="os-empty">Ninguna extensión coincide con la búsqueda y filtros actuales.</div>';
            return;
        }

        const cards = page.visible.map((entry) => {
            const state = entry.state || 'setup';
            const stateLabel = state === 'installed'
                ? 'Instalado'
                : state === 'ready' ? 'Disponible' : 'Configurar';
            const tags = Array.isArray(entry.tags) ? entry.tags.slice(0, 4) : [];
            const id = entry.id || entry.name || '';
            const expanded = this._skillsMarketExpanded === id;
            const homepage = this._safeExternalUrl(entry.homepage);
            const actionLabel = entry.action === 'manage'
                ? 'Administrar'
                : entry.action === 'copy' ? 'Copiar comando' : 'Ver requisitos';
            const action = entry.action === 'docs' ? 'details' : entry.action;
            const requirements = entry.requires
                || (entry.kind === 'mcp' ? `Transporte ${entry.transport || 'managed'} · auth ${entry.auth || 'none'}` : 'Consulta la ficha de Hermes antes de instalar.');
            return `
                <article class="os-market-card" data-kind="${this._escapeAttr(entry.kind)}" data-state="${this._escapeAttr(state)}" data-expanded="${expanded ? 'true' : 'false'}" data-skill-id="${this._escapeAttr(id)}">
                    <header>
                        <span class="os-skill-emoji">${this._escape(entry.emoji || (entry.kind === 'mcp' ? 'MC' : 'SK'))}</span>
                        <div>
                            <strong>${this._escape(entry.name || entry.kindLabel)}</strong>
                            <small>${this._escape(entry.kindLabel)} · ${this._escape(entry.category || 'general')}</small>
                        </div>
                        <em>${stateLabel}</em>
                    </header>
                    <p>${this._escape(entry.description || `Extensión ${entry.kindLabel} anunciada por Hermes.`)}</p>
                    <div class="os-market-tags">
                        ${tags.map((tag) => `<b>${this._escape(tag)}</b>`).join('')}
                    </div>
                    ${expanded ? `
                        <section class="os-market-details">
                            <span>Estado real: ${this._escape(stateLabel)}</span>
                            <span>Fuente: ${this._escape(entry.source || 'Hermes')}</span>
                            <span>Requisitos: ${this._escape(requirements)}</span>
                            ${entry.command ? `<span>CLI: ${this._escape(entry.command)}</span>` : ''}
                            ${homepage ? `<a href="${homepage}" target="_blank" rel="noopener noreferrer">Documentación</a>` : ''}
                        </section>
                    ` : ''}
                    <footer>
                        <code>${this._escape(entry.command || (entry.installed ? 'Administrado por Hermes' : 'Revisa requisitos'))}</code>
                        <button type="button" data-market-action="${this._escapeAttr(action)}" data-market-kind="${this._escapeAttr(entry.kind)}" data-market-command="${this._escapeAttr(entry.command)}">${actionLabel}</button>
                    </footer>
                </article>
            `;
        }).join('');
        const loadMore = page.remaining
            ? `<button class="os-market-load-more" type="button" data-market-load-more data-filtered-remaining="${page.remaining}">Mostrar ${Math.min(80, page.remaining)} más <small>${page.remaining} cargadas pendientes</small></button>`
            : remoteHasMore
                ? `<button class="os-market-load-more" type="button" data-market-load-more data-filtered-remaining="0">Cargar 60 más <small>página ${this._skillsMarketPage + 1} de ${this._skillsMarketPages.toLocaleString('es-CO')}</small></button>`
                : '';
        list.innerHTML = cards + loadMore;
    }

    async _runMarketplaceAction(action = '', kind = '', command = '', _url = '', button = null) {
        if (action === 'manage') {
            this._openWindow(kind === 'mcp' ? 'tools' : 'skills');
            return;
        }
        if (action === 'details') {
            const card = button?.closest?.('.os-market-card');
            if (!card) return;
            const id = card.dataset.skillId || '';
            this._skillsMarketExpanded = this._skillsMarketExpanded === id ? '' : id;
            this._renderSkillsMarketplace();
            return;
        }
        if (action !== 'copy' || !command) return;
        try {
            if (navigator.clipboard?.writeText) {
                await navigator.clipboard.writeText(command);
            } else {
                const input = document.createElement('textarea');
                try {
                    input.value = command;
                    input.setAttribute('readonly', '');
                    input.style.position = 'fixed';
                    input.style.opacity = '0';
                    document.body.appendChild(input);
                    input.select();
                    if (!document.execCommand?.('copy')) throw new Error('clipboard-unavailable');
                } finally {
                    input.remove();
                }
            }
            this._marketplaceNotice = 'Comando copiado. Ejecútalo en Hermes CLI para conservar sus confirmaciones y diagnóstico.';
        } catch {
            this._marketplaceNotice = `No pude copiar automáticamente. Comando: ${command}`;
        }
        this._renderSkillsMarketplace();
        window.setTimeout?.(() => {
            this._marketplaceNotice = '';
            this._renderSkillsMarketplace();
        }, 4200);
    }

    _runState(run = {}) {
        const value = String(run.status || '').toLowerCase();
        if (['started', 'dispatched', 'finalizing', 'stopping', 'stalling'].includes(value) || value.includes('run') || value.includes('progress') || value.includes('active')) return 'running';
        if (value.includes('wait') || value.includes('approval') || value.includes('queued')) return 'waiting';
        if (['stalled', 'interrupted', 'stopped'].includes(value) || value.includes('fail') || value.includes('error') || value.includes('cancel')) return 'error';
        if (value.includes('done') || value.includes('complete') || value.includes('success') || value.includes('finish')) return 'done';
        return 'unknown';
    }

    _runStateLabel(state = '') {
        return ({
            running: 'Activo',
            waiting: 'En espera',
            done: 'Completado',
            error: 'Error',
            unknown: 'Sin estado',
        })[state] || state || 'Sin estado';
    }

    _runTimeLabel(value) {
        if (!value) return '';
        const ts = Number(value);
        const date = Number.isFinite(ts)
            ? new Date(ts > 10_000_000_000 ? ts : ts * 1000)
            : new Date(String(value));
        if (Number.isNaN(date.getTime())) return '';
        return date.toLocaleString([], { hour: '2-digit', minute: '2-digit', day: '2-digit', month: 'short' });
    }

    _renderAsyncAgents() {
        if (!this.panel) return;
        const node = selector => this.panel.querySelector(selector);
        const text = (selector, value) => { if (node(selector)) node(selector).textContent = value; };
        const source = this._asyncSource || 'manual';
        const chat = source === 'chat';
        const rows = this._asyncRows();
        const caps = asyncRunCapabilities(this._hermesCapabilities || {});
        const info = this._asyncSourceStatus?.[source] || {};
        const error = this._asyncRunsError || info.error || '';
        const unavailable = !chat && !caps.canCreate
            ? (!caps.online ? 'El servicio de IA está detenido o no responde. Puedes consultar el historial guardado.' : 'Esta versión de Hermes no permite crear tareas desde el panel.') : '';
        const count = state => rows.filter(run => this._runState(run) === state).length;
        for (const tab of this.panel.querySelectorAll('[data-agent-source]')) {
            const selected = tab.dataset.agentSource === source;
            tab.setAttribute('aria-selected', String(selected));
            tab.tabIndex = selected ? 0 : -1;
        }
        for (const kind of ['manual', 'chat']) {
            const list = this._asyncRows(kind);
            const active = list.filter(run => ['running', 'waiting'].includes(this._runState(run))).length;
            text(`[data-source-count="${kind}"]`, String(list.length));
            const badge = node(`[data-source-count="${kind}"]`);
            if (badge) { badge.dataset.active = String(active > 0); badge.title = `${active} en curso`; }
        }
        text('.os-agents-origin', chat ? 'Desde el chat' : 'Creados por ti');
        text('.os-agents-capability', chat ? 'El trabajo que delega tu asistente' : 'Tú defines la tarea');
        text('.os-agents-capability-detail', chat
            ? 'Agentes del chat de esta identidad. Consulta sus estados y resultados, aunque sigas conversando.'
            : 'Crea una tarea independiente y vuelve a su resultado cuando quieras. El seguimiento se conserva al recargar.');
        text('.os-agents-list-title', chat ? 'Delegaciones del chat' : 'Tus tareas');
        text('.os-agents-total', String(rows.length));
        text('.os-agents-active', String(count('running')));
        text('.os-agents-waiting', String(count('waiting')));
        text('.os-agents-done', String(count('done')));
        text('.os-agents-status', error || (this._asyncRunsLoading ? 'Actualizando tareas…'
            : unavailable || info.notice || (rows.length ? `${count('running') + count('waiting')} en curso · ${count('done')} completadas` : 'Todavía no hay tareas en esta sección.')));
        node('.os-agents-status')?.classList.toggle('error', !!error);
        if (node('.os-agents-create')) node('.os-agents-create').hidden = chat;
        if (node('.os-agents-create button')) node('.os-agents-create button').disabled = !!this._asyncRunAction || !caps.canCreate;
        if (node('.os-agents-prompt')) node('.os-agents-prompt').disabled = !!this._asyncRunAction || !caps.canCreate;
        const create = node('[data-agent-action="compose"]');
        if (create) { create.hidden = chat; create.disabled = !caps.canCreate; }
        const list = node('.os-agents-list');
        // Avoid replacing focused cards on an unchanged background poll.
        const markup = rows.length ? rows.map(run => {
            const state = this._runState(run), selected = run.id === this._selectedAsyncRunId;
            return `<button type="button" class="os-agent-run-card ${selected ? 'selected' : ''}" data-run-id="${this._escape(run.id)}" data-state="${state}" aria-pressed="${selected}">
                <span>${osIcon(chat ? 'agents' : 'tools')}</span><span class="os-agent-run-copy">
                <strong>${this._escape(run.title || 'Tarea de IRIS')}</strong>
                <span class="os-agent-run-preview">${this._escape(String(run.summary || (chat ? 'Delegado por el asistente' : 'Tarea manual')).slice(0, 140))}</span>
                <small>${this._escape(this._runTimeLabel(run.created_at))}${run.child_count > 1 ? ` · ${run.child_count} agentes` : ''}</small></span>
                <span class="os-agent-state">${run.stale ? 'Sin actualizar' : this._runStateLabel(state)}</span></button>`;
        }).join('') : `<div class="os-empty os-agents-empty">${osIcon(chat ? 'agents' : 'tools')}<strong>${chat ? 'Tu asistente aún no ha delegado tareas' : 'Tu próxima tarea empieza aquí'}</strong><p>${chat ? 'Pídele en el chat que trabaje con agentes en segundo plano. Sus tareas aparecerán aquí.' : 'Describe un trabajo en el campo de abajo. Puedes seguir usando IRIS mientras se ejecuta.'}</p></div>`;
        if (list && list._asyncMarkup !== markup) {
            list.innerHTML = markup;
            list._asyncMarkup = markup;
        }
        const detail = this._asyncRunDetail?.run || rows.find(run => run.id === this._selectedAsyncRunId);
        const selected = node('.os-agents-selected');
        if (selected) selected.innerHTML = this._asyncRunDetailError
            ? `<div class="os-empty">${this._escape(this._asyncRunDetailError)}</div>`
            : !detail ? '<div class="os-empty">Selecciona una tarea para ver su resultado.</div>'
            : `<article class="os-agent-selected-card" data-state="${this._runState(detail)}"><span>${this._runStateLabel(this._runState(detail))}</span><strong>${this._escape(detail.title || detail.id)}</strong><small>${chat ? 'Delegada desde el chat' : 'Creada por ti'} · ${this._escape(this._runTimeLabel(detail.created_at))}</small></article>`;
        const actions = node('.os-agents-actions');
        if (actions) actions.innerHTML = detail && !chat && caps.canStop && ['running', 'waiting'].includes(this._runState(detail))
            ? `<button class="is-danger" type="button" data-agent-action="stop" data-run-id="${this._escape(detail.id)}" ${this._asyncRunAction ? 'disabled' : ''}>Detener tarea</button>` : '';
        const result = node('.os-agents-result');
        if (result) {
            result.hidden = !detail;
            const output = this._asyncRunDetail?.run?.result || '';
            const html = `<div class="os-subhead">Resultado</div><div class="os-agent-result-text">${this._escape(output || (this._asyncRunDetailError ? 'No se pudo cargar el resultado. Pulsa Actualizar para reintentar.' : this._asyncRunDetailLoading ? 'Cargando resultado…' : ['running', 'waiting'].includes(this._runState(detail || {})) ? 'El agente está trabajando. Su resultado aparecerá aquí.' : 'El motor de IA no publicó un resultado para esta tarea.'))}</div>`;
            if (result.innerHTML !== html) result.innerHTML = html;
        }
        const events = node('.os-agents-events');
        if (events) {
            const items = this._asyncRunDetail?.events || [];
            events.innerHTML = items.length ? items.slice(-18).reverse().map(event => `<article class="os-agent-event"><span>${this._escape(event.kind || 'Evento')}</span><p>${this._escape(event.message || '')}</p><small>${this._escape(this._runTimeLabel(event.created_at))}</small></article>`).join('') : '<div class="os-empty">Sin eventos adicionales.</div>';
        }
    }

    _renderMissionBoard() {
        if (!this.panel) return;
        const boardName = this.panel.querySelector('.mission-board-name');
        const boardSelect = this.panel.querySelector('.mission-board-select');
        const statusEl = this.panel.querySelector('.mission-status');
        const columnsEl = this.panel.querySelector('.mission-columns');
        const submitBtn = this.panel.querySelector('.mission-create button');
        if (submitBtn) submitBtn.disabled = this._missionLoading;

        const board = this.missionBoard;
        if (boardName) {
            boardName.textContent = board?.board ? `${board.board} board` : 'Tablero de tareas';
        }
        if (boardSelect && board?.boards) {
            const boards = Array.isArray(board.boards) && board.boards.length
                ? board.boards.map((entry) => typeof entry === 'string' ? entry : entry.slug || entry.name).filter(Boolean)
                : [board.board || 'default'];
            const uniqueBoards = [...new Set(boards)];
            boardSelect.innerHTML = uniqueBoards.map((slug) =>
                `<option value="${this._escape(slug)}">${this._escape(slug)}</option>`
            ).join('');
            boardSelect.value = this._missionBoardSlug || board.board || uniqueBoards[0] || '';
        }
        if (statusEl) {
            if (this.missionError) {
                statusEl.textContent = `No pude leer Kanban: ${this.missionError}`;
                statusEl.classList.add('error');
            } else {
                statusEl.classList.remove('error');
                const tasks = board?.tasks || [];
                const active = tasks.filter(t => !['done', 'archived'].includes(t.status)).length;
                const workers = Array.isArray(this._missionWorkers) ? this._missionWorkers.length : 0;
                statusEl.textContent = this._missionLoading
                    ? 'Sincronizando el tablero de tareas…'
                    : `${active} misiones activas · ${tasks.length} visibles · ${workers} workers`;
            }
        }
        if (!columnsEl) return;
        if (this.missionError) {
            columnsEl.innerHTML = '<div class="mission-empty">El tablero de tareas no respondió. Comprueba el motor de IA y su conexión.</div>';
            return;
        }
        const tasks = Array.isArray(board?.tasks) ? board.tasks : [];
        if (!tasks.length && !this._missionLoading) {
            columnsEl.innerHTML = '<div class="mission-empty">Sin misiones todavía. Crea una o pídele a IRIS que delegue trabajo duradero.</div>';
            return;
        }

        const columns = [
            ['triage', 'Triage'],
            ['todo', 'Todo'],
            ['ready', 'Ready'],
            ['running', 'Running'],
            ['blocked', 'Blocked'],
            ['done', 'Done'],
        ];
        const byStatus = new Map(columns.map(([id]) => [id, []]));
        for (const task of tasks) {
            const key = byStatus.has(task.status) ? task.status : 'todo';
            byStatus.get(key).push(task);
        }

        columnsEl.innerHTML = columns.map(([id, label]) => {
            const group = byStatus.get(id) || [];
            return `
                <section class="mission-column" data-status="${id}">
                    <header>
                        <span>${label}</span>
                        <strong>${group.length}</strong>
                    </header>
                    <div class="mission-stack">
                        ${group.slice(0, 8).map(task => this._missionCard(task)).join('') ||
                            '<div class="mission-column-empty">quiet</div>'}
                    </div>
                </section>
            `;
        }).join('');
        columnsEl.onclick = (ev) => {
            const card = ev.target?.closest?.('.mission-card');
            const taskId = card?.dataset?.taskId;
            if (taskId) this._openMissionDetail(taskId);
        };
        this._renderMissionDetail();
    }

    _missionCard(task = {}) {
        const assignee = task.assignee || 'sin perfil';
        const childCount = task.child_count ?? task.children_count ?? (Array.isArray(task.children) ? task.children.length : 0);
        const runCount = task.run_count ?? task.runs_count ?? (Array.isArray(task.runs) ? task.runs.length : 0);
        const meta = [
            assignee,
            task.priority ? `P${task.priority}` : '',
            childCount ? `${childCount} subtareas` : '',
            runCount ? `${runCount} runs` : '',
            task.current_step_key ? `fase ${task.current_step_key}` : '',
        ].filter(Boolean).join(' · ');
        const signal = task.status === 'running'
            ? 'live'
            : task.status === 'blocked'
                ? 'needs input'
                : task.status;
        const phase = task.phase || task.current_phase || task.current_step_key || task.workspace_kind || task.tenant || 'mission';
        return `
            <article class="mission-card" data-status="${this._escape(task.status || '')}" data-task-id="${this._escape(task.id || '')}">
                <div class="mission-card-row">
                    <span class="mission-id">${this._escape(task.id || '')}</span>
                    <span class="mission-signal">${this._escape(signal)}</span>
                </div>
                <strong>${this._escape(task.title || 'Untitled mission')}</strong>
                <p>${this._escape(meta)}</p>
                <small>${this._escape(phase)}</small>
            </article>
        `;
    }

    _renderMissionDetail() {
        const detailEl = this.panel?.querySelector('.mission-detail');
        if (!detailEl) return;
        const detail = this.missionDetail;
        if (!detail && !this.missionDetailError) {
            detailEl.classList.add('hidden');
            detailEl.innerHTML = '';
            return;
        }
        detailEl.classList.remove('hidden');
        if (this.missionDetailError) {
            detailEl.innerHTML = `
                <button class="mission-detail-close" type="button" aria-label="Cerrar">x</button>
                <strong>No pude abrir la mision</strong>
                <p>${this._escape(this.missionDetailError)}</p>
            `;
            detailEl.querySelector('.mission-detail-close')?.addEventListener('click', () => this._closeMissionDetail());
            return;
        }
        const task = detail?.task || {};
        const runs = Array.isArray(detail?.runs) ? detail.runs.slice(-4).reverse() : [];
        const events = Array.isArray(detail?.events) ? detail.events.slice(-6).reverse() : [];
        const artifacts = Array.isArray(detail?.artifacts) ? detail.artifacts.slice(0, 5) : [];
        const activeRun = detail?.active_run || null;
        const summary = detail?.latest_summary || task.result || task.body || 'Sin resumen todavía.';
        const family = [
            task.parent_count ? `${task.parent_count} padres` : '',
            task.child_count ? `${task.child_count} subtareas` : '',
            activeRun ? `run activo ${activeRun.id || activeRun.step_key || ''}`.trim() : '',
        ].filter(Boolean).join(' · ');
        detailEl.innerHTML = `
            <button class="mission-detail-close" type="button" aria-label="Cerrar">x</button>
            <div class="mission-detail-head">
                <span>${this._escape(task.id || '')}</span>
                <strong>${this._escape(task.title || 'Mision')}</strong>
                <p>${this._escape(task.status || 'unknown')} ${task.assignee ? `- ${this._escape(task.assignee)}` : ''}</p>
                ${family ? `<p>${this._escape(family)}</p>` : ''}
            </div>
            <div class="mission-detail-summary">${this._escape(summary).slice(0, 520)}</div>
            <div class="mission-detail-grid">
                <section>
                    <h3>Runs</h3>
                    ${runs.map((run) => `
                        <p><b>${this._escape(run.status || run.outcome || 'run')}</b> ${this._escape(run.profile || 'Hermes')} ${run.error ? `- ${this._escape(run.error).slice(0, 80)}` : ''}</p>
                    `).join('') || '<p>Sin ejecuciones aun.</p>'}
                </section>
                <section>
                    <h3>Events</h3>
                    ${events.map((event) => `
                        <p><b>${this._escape(event.kind || 'event')}</b> ${this._escape(this._formatMissionTime(event.created_at))}</p>
                    `).join('') || '<p>Sin eventos aun.</p>'}
                </section>
                <section>
                    <h3>Artifacts</h3>
                    ${artifacts.map((artifact) => `
                        <p><b>${this._escape(artifact.name || artifact.title || artifact.type || 'artifact')}</b> ${this._escape(artifact.path || artifact.url || artifact.id || '').slice(0, 80)}</p>
                    `).join('') || '<p>Sin artifacts reportados.</p>'}
                </section>
            </div>
        `;
        detailEl.querySelector('.mission-detail-close')?.addEventListener('click', () => this._closeMissionDetail());
    }

    _formatMissionTime(value) {
        const ts = Number(value || 0);
        if (!Number.isFinite(ts) || ts <= 0) return '';
        return new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    }

    async _responseError(response, context = 'Solicitud') {
        const status = Number(response?.status) || 0;
        const fallback = `${context}: HTTP ${status || 'desconocido'}`;
        let raw = '';
        try {
            raw = await response.text();
        } catch {
            return fallback;
        }
        const text = String(raw || '').trim();
        if (!text) return fallback;
        try {
            const data = JSON.parse(text);
            const detail = data?.detail || data?.message || data?.error;
            if (typeof detail === 'string' && detail.trim()) {
                return `${context}: ${detail.trim().slice(0, 240)}`;
            }
        } catch {
            // Non-JSON responses are handled below.
        }
        if (/^\s*<!doctype\s+html|^\s*<html|<body[\s>]/i.test(text)) {
            return fallback;
        }
        return `${context}: ${text.replace(/\s+/g, ' ').slice(0, 240)}`;
    }

    async _responseJson(response, context = 'Solicitud') {
        if (!response?.ok) {
            throw new Error(await this._responseError(response, context));
        }
        try {
            return await response.json();
        } catch {
            throw new Error(`${context}: respuesta inválida del backend.`);
        }
    }

    _escape(value) {
        return String(value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    _escapeAttr(value) {
        return this._escape(value);
    }

    _safeExternalUrl(value) {
        const raw = String(value || '').trim();
        if (!raw) return '';
        try {
            const url = new URL(raw);
            if (url.protocol !== 'http:' && url.protocol !== 'https:') return '';
            return this._escapeAttr(url.href);
        } catch {
            return '';
        }
    }
}
