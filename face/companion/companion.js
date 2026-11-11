import { AnimationLibrarySync } from '../js/animation_library_sync.js';
import * as THREE from 'three';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';

import { AvatarManager } from '../js/avatar.js?v=29';
import { IRIS_AVATAR_FILE, IRIS_AVATAR_REVISION, isPmxArchive } from '../js/avatar_state.js?v=5';
import { companionAvatarSelection, CompanionAvatarSync, disposeCompanionAvatar } from '../js/companion_avatar_sync.js?v=2';
import {
    createCompanionBridge,
    localAnimationBase,
} from '../js/companion_native_bridge.js?v=4';
import { AnimationDirector } from '../js/animation_director.js?v=6';
import { loadAnimationContexts } from '../js/animation_context_resolver.js?v=2';
import {
    buildCompanionEmotionPayload,
    COMPANION_CAMERA_CONSTRAINTS,
} from '../js/companion_camera_policy.js?v=2';
import { buildVisionSignal } from '../js/vision_signal_policy.js?v=2';
import { buildVisionActivity } from '../js/vision_activity.js?v=1';
import { VisionPresentation } from '../js/vision_presentation.js?v=2';
import { applyVisionCalibration } from '../js/vision_calibration_command.js?v=1';
import {
    companionStateForStatus,
    shouldPlaySnapshotAnimation,
} from '../js/companion_policy.js?v=2';
import { companionLocomotionDecision } from '../js/companion_locomotion_policy.js?v=2';
import { CompanionBody, CompanionProps } from '../js/companion_body.js';
import { RoomRoutines } from '../js/room_routines.js?v=7';
import { IdleSystem } from '../js/idle_system.js?v=62';
import { LipSyncEngine } from '../js/lipsync.js?v=3';
import { SpeechAnimator } from '../js/speech_animator.js?v=5';
import {
    buildSurfaceReadyPayload,
    buildSurfaceStatePayload,
    getOrCreateSurfaceId,
} from '../js/surface_protocol.js?v=8';
import { TtsAudioPlayer } from '../js/tts_audio_player.js?v=4';
import { SpeechPresentation } from '../js/speech_presentation.js?v=1';
import { InboxNotice } from '../js/inbox_notice.js?v=2';
import { InboxVoicePlayer } from '../js/inbox_voice.js?v=1';
import { WebSocketManager } from '../js/websocket.js?v=3';
import { ManualVoiceController } from '../js/manual_voice.js?v=3';
import { FrameLimiter } from '../js/frame_limiter.js?v=2';
import { GraphicsQuality, avatarTextureLimit, graphicsQuality } from '../js/graphics_quality.js?v=3';
import { ScreenSnapshotRequests } from '../js/screen_snapshot.js?v=2';
import { BackgroundTaskBar } from '../js/background_task_bar.js?v=3';

const THEME_STORAGE_KEY = 'irisOsTheme.v1';
const THEME_DEFAULT = Object.freeze({
    accent: '#73f7ff',
    accent2: '#ff74d4',
    readyColor: '#5cffad',
    reducedMotion: false,
});
const THEME_PRESETS = Object.freeze({
    iris: THEME_DEFAULT,
    sunrise: { accent: '#9ed8ff', accent2: '#73f7ff', readyColor: '#9de7ff' },
    verdant: { accent: '#5cffad', accent2: '#0eff8f', readyColor: '#5cffad' },
    rose: { accent: '#ff8ed7', accent2: '#c78bff', readyColor: '#ff8ed7' },
});
const ACTIVE_WAKE_STATES = new Set(['listening', 'detected', 'recording', 'processing']);
const STATUS_LABELS = Object.freeze({
    connected: 'En línea',
    idle: 'Disponible',
    listening: 'Escuchando',
    recording: 'Escuchando orden',
    processing: 'Procesando',
    thinking: 'Pensando',
    tool: 'Usando herramienta',
    tool_running: 'Usando herramienta',
    speaking: 'Respondiendo',
    error: 'Requiere atención',
    offline: 'Sin conexión',
});

function cleanAssistantName(value) {
    const clean = String(value || '').trim().replace(/\s+/g, ' ');
    return clean.slice(0, 48) || 'IRIS';
}

function safeThemeValue(value, fallback) {
    return typeof value === 'string' && /^#[0-9a-f]{6}$/i.test(value.trim())
        ? value.trim()
        : fallback;
}

function readTheme(config = {}) {
    const themeId = config.visual_theme === 'alice' ? 'iris' : config.visual_theme;
    const preset = THEME_PRESETS[themeId] || THEME_DEFAULT;
    let stored = {};
    try {
        const parsed = JSON.parse(localStorage.getItem(THEME_STORAGE_KEY) || '{}');
        if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) stored = parsed;
    } catch {}
    const reducedMotion = Boolean(
        config.companion_reduced_motion
        || config.reducedMotion
        || stored.reducedMotion
        || preset.reducedMotion,
    );
    return {
        ...THEME_DEFAULT,
        ...preset,
        ...stored,
        accent: safeThemeValue(stored.accent, preset.accent || THEME_DEFAULT.accent),
        accent2: safeThemeValue(stored.accent2, preset.accent2 || THEME_DEFAULT.accent2),
        readyColor: safeThemeValue(
            stored.readyColor,
            preset.readyColor || THEME_DEFAULT.readyColor,
        ),
        reducedMotion,
    };
}

function truncateText(value, max = 420) {
    const text = String(value || '').trim();
    return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

class IrisCompanionApp {
    constructor() {
        this.canvas = document.getElementById('companion-canvas');
        this.stage = document.getElementById('avatar-stage');
        this.loading = document.getElementById('loading-state');
        this.statusText = document.querySelector('.status-text');
        this.speechBubble = document.getElementById('speech-bubble');
        this.speechText = document.getElementById('speech-text');
        this.speechKicker = document.getElementById('speech-kicker');
        this.chatPanel = document.getElementById('companion-chat');
        this.chatLog = document.getElementById('chat-log');
        this.chatInput = document.getElementById('chat-input');
        this.cameraButton = document.getElementById('camera-toggle');
        this.cameraPrivacy = document.getElementById('camera-privacy');
        this.cameraPreview = document.getElementById('camera-preview');
        this.cameraPrivacyLabel = document.getElementById('camera-privacy-label');
        this.dragRegion = document.querySelector('.signal-header');
        this.hideButton = document.getElementById('hide-companion');

        this.surfaceId = getOrCreateSurfaceId('desktop_companion');
        this.config = { avatar_vrm_path: '', visual_theme: 'iris' };
        this.theme = THEME_DEFAULT;
        this.assistantName = 'IRIS';
        this.clock = new THREE.Clock();
        this.scene = null;
        this.camera = null;
        this.renderer = null;
        this.avatarManager = null;
        this.idle = null;
        this.lipSync = null;
        this.speechAnimator = null;
        this.animationDirector = null;
        this.ttsAudioPlayer = null;
        this.ws = null;
        this.bridge = null;

        this.wakeActive = false;
        this.wakeState = 'off';
        this.wakePhrase = 'hey hermes';
        this.showControlsPreference = true;
        this.locomotionEnabled = false;
        this.quietMode = false;
        this.life = 'calm';
        this.inboxNotice = new InboxNotice({
            send: (receipt) => this.ws?.send?.('inbox_presentation_receipt', receipt),
            openLabel: 'Abrir IRIS OS',
            onOpenInbox: () => this.bridge?.openIrisOs?.().catch(() => this._showError('No pude abrir IRIS OS. Usa su acceso directo y abre la Bandeja.')),
        });
        this.inboxVoice = new InboxVoicePlayer({
            send: (receipt) => this.ws?.send?.('inbox_voice_receipt', receipt),
            getLipSync: () => this.lipSync, getSpeechAnimator: () => this.speechAnimator,
            getContext: () => ({ connected: this.ws?.connected === true, surfaceId: this.surfaceId,
                documentLike: document, quiet: this.quietMode,
                status: this.manualVoice?.state.busy || this.wakeActive ? 'listening' : document.body.dataset.operationalState }),
        });
        this.cameraStream = null;
        this.cameraLeasePending = false;
        this._cameraRequestVisionOwned = false;
        this._cameraStartedByVision = false;
        this._cameraLeaseTimer = null;
        this.mocap = null;
        this.emotionClassifier = null;
        this._cameraAnalysisGeneration = 0;
        this._lastMocapTimestamp = -1;
        this._emotionEmitAccum = 0;
        this._visionEmitAccum = 0;
        this.visionAssistant = { enabled: false, paused_until: 0, state: 'off' };
        this.visionPresentation = new VisionPresentation();
        this._visionResumeTimer = null;
        this.speechPresentation = new SpeechPresentation({
            getIdle: () => this.idle,
            getLipSync: () => this.lipSync,
            getSpeechAnimator: () => this.speechAnimator,
            getAudioPlayer: () => this.ttsAudioPlayer,
            applyExpression: emotion => this.avatarManager?.setEmotion?.(emotion),
        });
        this._bubbleTimer = null;
        this._movementTimer = null;
        this._movementGeneration = 0;
        this._movementPending = false;
        this._windowWalkActive = false;
        this._pointerHovered = false;
        this._lastAnimation = { name: '', at: 0 };
        this._chatHydrated = false;
        this._lastSurfaceActivityAt = 0;
        this._animationFrame = 0;
        this._frameLimiter = new FrameLimiter();
        this.graphicsQuality = new GraphicsQuality({
            getRenderer: () => this.renderer, getComposer: () => this.composer,
            getSize: () => [this.stage.clientWidth, this.stage.clientHeight],
            getAvatarPhysics: () => this.avatarManager?.avatar?.vrm?.pmxPhysics,
        });
        this._screenSnapshots = new ScreenSnapshotRequests({
            send: result => this.ws?.send('screen_snapshot_result', result),
        });
        // «Trabajando en …» over the avatar; its Cancelar stops the background task only.
        this._backgroundBar = new BackgroundTaskBar({
            host: this.stage, onCancel: id => this.ws?.send('background_task_cancel', { id }),
            onRelease: id => this.ws?.send('background_task_release', { id }),
        });
        this._rebootstrapPromise = null;
        this._appliedAvatarKey = '';
        this._avatarSyncError = '';
        this._avatarSync = new CompanionAvatarSync({
            readConfig: () => this._readAvatarConfig(),
            applyConfig: (config, isCurrent) => this._applyAvatarConfig(config, isCurrent),
            onError: error => {
                const message = 'No pude actualizar el avatar de Companion: ' + (error?.message || String(error));
                if (message !== this._avatarSyncError) this._showError(message);
                this._avatarSyncError = message;
            },
        });
    }

    async init() {
        this.bridge = await createCompanionBridge();
        this.config = { ...this.config, ...(this.bridge.preferences || {}) };
        await this._fetchConfig();
        this._applyIdentityAndTheme();
        this._setupThree();
        this._setupRoutines();

        // Transport and privacy controls must become truthful immediately.
        // A large/custom VRM can take many seconds to fetch and parse; delaying
        // WebSocket setup behind it made a healthy Core look disconnected.
        this._setupControls();
        this._setupTransport();
        this._setupLifecycle();
        this._onResize();
        this._animate();
        this._refreshWakeState();
        this.ws.connect();

        // AvatarManager consults window.iris for renderer capabilities while
        // sharpening VRM textures. Publish this small compatible surface first.
        window.iris = this;
        window.IRIS = this;

        await this._avatarSync.request();
        if (!this.avatarManager) {
            // Offline startup still needs a visible surface: IRIS, from the Companion's
            // own bundle. A reconnect retries the saved model through the same path.
            try {
                await this._applyAvatarConfig({ avatar_vrm_path: '' }, () => true);
            } catch (error) {
                this._showError('No pude cargar el avatar. Companion sigue funcionando sin él.');
                console.error('[companion] No VRM could be loaded:', error);
            }
        }
        this.ttsAudioPlayer = new TtsAudioPlayer({
            getLipSync: () => this.lipSync,
            getSpeechAnimator: () => this.speechAnimator,
            onPlaybackResult: (payload) => this.ws?.send('tts_playback_result', payload),
        });
        this.animationDirector = new AnimationDirector({
            getIdle: () => this.idle,
            getAvatarManager: () => this.avatarManager,
            surface: 'desktop_companion',
        });
        this.animationDirector.setContexts(await loadAnimationContexts(
            `${localAnimationBase()}/contexts.json`,
        ));

        this.loading?.classList.add('is-ready');
        if (this.ws?.connected) this._announceSurfaceReady();
    }

    async _fetchConfig() {
        const renderRevision = this._renderRevision || 0;
        try {
            const response = await this.bridge.apiFetch('/api/config', { cache: 'no-store' });
            if (!response.ok) return;
            const config = await response.json();
            if (renderRevision !== (this._renderRevision || 0)) {
                delete config.render_fps_limit;
                delete config.render_quality;
            }
            if (config && typeof config === 'object') this.config = { ...this.config, ...config };
            this._frameLimiter.setLimit(this.config.render_fps_limit);
            this.graphicsQuality?.setQuality(this.config.render_quality);
        } catch {
            // Static preview and backend-offline mode intentionally use defaults.
        }
    }

    /** The saved avatar's config. A live render change that arrives meanwhile wins over the
     * reply's render preferences, or the model would load with the old texture limit. */
    async _readAvatarConfig() {
        const renderRevision = this._renderRevision || 0;
        const response = await this.bridge.apiFetch('/api/config', { cache: 'no-store' });
        if (!response.ok) throw new Error('No pude consultar el avatar guardado.');
        const config = await response.json();
        if (config && typeof config === 'object' && renderRevision !== (this._renderRevision || 0)) {
            delete config.render_fps_limit;
            delete config.render_quality;
        }
        return config;
    }

    _applyIdentityAndTheme() {
        this.assistantName = cleanAssistantName(this.config.assistant_name);
        this.theme = readTheme(this.config);
        this.avatarRimLight?.color.set(this.theme.accent2);
        const root = document.documentElement;
        root.style.setProperty('--companion-accent', this.theme.accent);
        root.style.setProperty('--companion-accent-2', this.theme.accent2);
        root.style.setProperty('--companion-ready', this.theme.readyColor);
        const preferences = { ...(this.bridge?.preferences || {}), ...this.config };
        // Saved Core settings outrank the native startup snapshot on refresh.
        for (const [camel, snake] of [
            ['alwaysOnTop', 'companion_always_on_top'], ['scale', 'companion_scale'],
            ['reducedMotion', 'companion_reduced_motion'], ['showControls', 'companion_show_controls'],
            ['locomotionEnabled', 'companion_locomotion_enabled'], ['quietMode', 'companion_quiet_mode'],
            ['life', 'companion_life'],
        ]) {
            if (this.config[snake] !== undefined) preferences[camel] = this.config[snake];
        }
        this._applyCompanionPreferences(preferences);
        document.getElementById('assistant-name').textContent = this.assistantName;
        document.getElementById('chat-assistant-name').textContent = this.assistantName;
        this.speechKicker.textContent = this.assistantName;
        document.title = `${this.assistantName} Companion`;
    }

    _applyCompanionPreferences(preferences = {}) {
        const read = (camel, snake, fallback) => preferences[camel]
            ?? preferences[snake]
            ?? fallback;
        const reducedMotion = Boolean(read(
            'reducedMotion',
            'companion_reduced_motion',
            this.theme.reducedMotion,
        ));
        const showControls = Boolean(read(
            'showControls',
            'companion_show_controls',
            true,
        ));
        const scaleValue = Number(read('scale', 'companion_scale', 1));
        const scale = Number.isFinite(scaleValue)
            ? Math.min(1.5, Math.max(0.75, scaleValue))
            : 1;
        const alwaysOnTop = Boolean(read(
            'alwaysOnTop',
            'companion_always_on_top',
            true,
        ));
        const locomotionEnabled = Boolean(read(
            'locomotionEnabled',
            'companion_locomotion_enabled',
            false,
        ));
        const quietMode = Boolean(read(
            'quietMode',
            'companion_quiet_mode',
            false,
        ));

        document.body.dataset.reducedMotion = reducedMotion ? 'true' : 'false';
        document.body.dataset.locomotion = locomotionEnabled ? 'enabled' : 'disabled';
        document.body.dataset.quietMode = quietMode ? 'true' : 'false';
        this.showControlsPreference = showControls;
        this.locomotionEnabled = locomotionEnabled;
        this.quietMode = quietMode;
        this.life = read('life', 'companion_life', this.life);
        document.documentElement.style.setProperty('--companion-avatar-scale', String(scale));
        this._syncControlRailVisibility();
        if (!locomotionEnabled || quietMode || reducedMotion) this._cancelWindowWalk();

        if (this.bridge?.native && this._appliedAlwaysOnTop !== alwaysOnTop) {
            this._appliedAlwaysOnTop = alwaysOnTop;
            this.bridge.setAlwaysOnTop(alwaysOnTop).catch(() => {
                this._appliedAlwaysOnTop = null;
                this._showError('No pude cambiar el modo siempre visible de la ventana.');
            });
        }
    }

    _syncControlRailVisibility() {
        const visible = Boolean(
            this.showControlsPreference
            || this.manualVoice?.state.busy
            || this.wakeActive
            || this.cameraStream
            || this.cameraLeasePending,
        );
        document.body.dataset.controls = visible ? 'visible' : 'hidden';
        const rail = document.querySelector('.signal-rail');
        if (rail) rail.hidden = !visible;
    }

    _setupThree() {
        this.scene = new THREE.Scene();
        this.camera = new THREE.PerspectiveCamera(44, 1, 0.1, 100);
        this.camera.position.set(0.34, 1.38, 4.8);
        this.camera.lookAt(0.08, 0.68, 0);

        this.renderer = new THREE.WebGLRenderer({
            canvas: this.canvas,
            antialias: false,
            alpha: true,
            premultipliedAlpha: false,
            powerPreference: 'high-performance',
        });
        this.renderer.setClearColor(0x000000, 0);
        this.renderer.setPixelRatio(1);
        this.renderer.outputColorSpace = THREE.SRGBColorSpace;
        this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
        this.renderer.toneMappingExposure = 0.78;
        const target = new THREE.WebGLRenderTarget(1, 1, { type: THREE.HalfFloatType, samples: 2 });
        this.composer = new EffectComposer(this.renderer, target);
        this.composer.addPass(new RenderPass(this.scene, this.camera));
        // Match direct rendering's tone mapping and output colour; keep alpha intact.
        const output = new OutputPass();
        output.material.blending = THREE.NoBlending;
        this.composer.addPass(output);
        this.graphicsQuality.setQuality(this.config.render_quality);

        // Neutral fill keeps the VRM colours legible. The theme tints the rim.
        this.scene.add(new THREE.AmbientLight(0xe8edff, 0.34));
        this.scene.add(new THREE.HemisphereLight(0xe9f3ff, 0x342331, 0.43));
        const key = new THREE.DirectionalLight(0xfff4f4, 0.56);
        key.position.set(1.8, 2.4, 5);
        this.scene.add(key);
        const fill = new THREE.DirectionalLight(0xe1efff, 0.24);
        fill.position.set(-3.2, 1.15, 2.2);
        this.scene.add(fill);
        this.avatarRimLight = new THREE.PointLight(this.theme.accent2, 0.95, 6.3);
        this.avatarRimLight.position.set(-0.28, 1.55, -2.35);
        this.scene.add(this.avatarRimLight);
    }

    _setupControls() {
        this.dragRegion?.addEventListener('pointerdown', (event) => {
            if (
                !this.bridge?.native
                || !event.isPrimary
                || event.button !== 0
                || event.target?.closest?.('button, input, select, textarea, a')
            ) return;
            this._cancelWindowWalk();
            this.bridge.startWindowDrag().catch(() => {
                this._showError('No pude mover la ventana. Intenta arrastrar de nuevo desde la barra superior.');
            });
        });
        this.hideButton?.addEventListener('click', async () => {
            if (!this.bridge?.native) {
                this._showError('Ocultar está disponible en la aplicación de escritorio.');
                return;
            }
            this._cancelWindowWalk();
            this._setChatOpen(false);
            await this.manualVoice?.cancel();
            this.ttsAudioPlayer?.stop?.();
            this._stopLocalCamera({ releaseLease: true, reason: 'Cámara liberada al ocultar.' });
            try {
                await this.bridge.hideCompanion();
            } catch {
                this._showError('No pude ocultar la ventana. También puedes hacerlo desde el icono de IRIS en la bandeja.');
            }
        });
        this.manualVoice = new ManualVoiceController({
            button: document.getElementById('voice-manual'),
            fetchImpl: (...args) => this.bridge.apiFetch(...args),
            notice: (text) => this._showError(text),
            onState: (state) => {
                if (this.ws?.connected) this.animationDirector?.handleRuntimeEvent('hermes_voice_state', state);
                this._syncControlRailVisibility();
            },
        });
        this.manualVoice.setConnected(this.ws?.connected);
        this.cameraButton.addEventListener('click', () => this._toggleCamera());
        document.getElementById('chat-toggle').addEventListener('click', () => {
            this._setChatOpen(this.chatPanel.hidden);
        });
        document.getElementById('close-chat').addEventListener('click', () => {
            this._setChatOpen(false);
        });
        document.getElementById('chat-form').addEventListener('submit', (event) => {
            event.preventDefault();
            this._sendChat();
        });
        document.getElementById('open-iris-os').addEventListener('click', () => {
            this._markInteraction();
            this.bridge.openIrisOs().catch(() => {
                this._showError('No pude abrir IRIS OS. Verifica que IRIS Core esté activo.');
            });
        });
        window.addEventListener('keydown', (event) => {
            if (event.key === 'Escape' && !this.chatPanel.hidden) this._setChatOpen(false);
        });
        document.body.addEventListener('pointerenter', () => {
            this._pointerHovered = true;
            this._cancelWindowWalk();
        });
        document.body.addEventListener('pointerleave', () => {
            this._pointerHovered = false;
        });
    }

    /** Her routines, from the room's, with what she can do standing here. */
    _setupRoutines() {
        this.props = new CompanionProps({
            scene: this.scene,
            modelBase: new URL('../public/assets/room', `${localAnimationBase()}/`).href.replace(/\/$/, ''),
            accent: this.theme.accent,
        });
        this.body = new CompanionBody({ getIdle: () => this.idle, hasProp: (id) => this.props.has(id) });
        this.routines = new RoomRoutines({
            scenes: this.body,
            context: () => ({
                life: this.quietMode ? 'off' : this.life,
                reducedMotion: document.body.dataset.reducedMotion === 'true',
                visible: document.visibilityState !== 'hidden',
                exploring: false,
                activity: this.animationDirector?.activity.state ?? 'idle',
            }),
        });
        void this.props.load().catch((error) => console.warn('[companion] props unavailable:', error));
    }

    _setupTransport() {
        this.ws = new WebSocketManager(this.bridge.wsUrl, this.bridge.wsProtocols);
        this.ws.onMessage = (type, payload) => this._handleMessage(type, payload || {});
        this.ws.onConnectionChange = (connected) => {
            this.manualVoice?.setConnected(connected);
            if (!connected) this.animationDirector?.resetActivity();
            if (!connected) this._screenSnapshots?.cancel();
            if (!connected) this.routines?.connectionLost();
            if (connected) {
                this._setOperationalState('connected');
                void this._rehydrateAfterConnect();
            } else {
                this.speechPresentation.reset();
                clearTimeout(this._bubbleTimer);
                this._hideSpeech();
                this._setOperationalState('offline');
                this._stopLocalCamera({ releaseLease: false, reason: 'IRIS Core se desconectó.' });
                void this._refreshNativeEndpoint();
            }
        };
    }

    async _refreshNativeEndpoint() {
        if (!this.bridge?.native || this._rebootstrapPromise) return this._rebootstrapPromise;
        this._rebootstrapPromise = (async () => {
            try {
                const changed = await this.bridge.refreshBootstrap?.();
                if (!changed || !this.ws) return;
                this.ws.disconnect();
                this.ws.url = this.bridge.wsUrl;
                this.ws.protocols = this.bridge.wsProtocols;
                this.ws.connect();
            } catch {
                // Core may still be starting. WebSocket backoff will trigger
                // another snapshot after the next failed attempt.
            }
        })();
        try {
            return await this._rebootstrapPromise;
        } finally {
            this._rebootstrapPromise = null;
        }
    }

    async _applyAvatarConfig(config, isCurrent) {
        this.config = { ...this.config, ...config };
        const quality = graphicsQuality(this.config.render_quality);
        if (this.graphicsQuality && this.graphicsQuality.quality !== quality) {
            this.graphicsQuality.setQuality(quality);
        }
        this._applyIdentityAndTheme();
        const irisUrl = `${localAnimationBase()}/${IRIS_AVATAR_FILE}?v=${IRIS_AVATAR_REVISION}`;
        const selection = companionAvatarSelection(this.config, irisUrl);
        if (this.avatarManager && this._appliedAvatarKey === selection.key) return;

        // Load off-screen so an unavailable model cannot erase the current one.
        const staging = new THREE.Scene();
        let url = selection.resource;
        let candidate;
        try {
            if (this.bridge.native && selection.own && url.startsWith('/')) url = await this.bridge.coreAssetUrl(url);
            if (!isCurrent()) return;
            candidate = new AvatarManager(staging, url, irisUrl,
                { textureLimit: () => avatarTextureLimit(this.config.render_quality), pmxBlob: isPmxArchive(selection.resource) });
            await candidate.init({ fallback: !this.avatarManager });
            if (!isCurrent()) return;
            // The person's model could not be loaded and IRIS shows instead.
            const failedVrm = selection.own && candidate.vrmUrl !== url;

            const previous = this.avatarManager;
            const oldIdle = this.idle;
            const speaking = this.speechPresentation.speaking;
            const syntheticMouth = this.speechAnimator?._syntheticMouthEnabled ?? true;
            this._cancelWindowWalk();
            const activity = oldIdle?._activityState;
            oldIdle?.dispose?.();
            candidate.scene = this.scene;
            candidate.avatar.scene = this.scene;
            this.scene.add(candidate.avatar.group);
            this.avatarManager = candidate;
            this.graphicsQuality?.applyAvatarPhysics();
            this.props?.drop();   // on the old avatar's hand, about to go
            this.idle = new IdleSystem(candidate, { animationBaseUrl: localAnimationBase() });
            if (!this.animationLibrary) {
                this.animationLibrary = new AnimationLibrarySync({ getIdle: () => this.idle, bridge: this.bridge, onError: message => this._showError(message) });
                this.animationLibrary.start();
            } else {
                void this.animationLibrary.refresh();
            }
            this.idle.setActivity?.(activity || (speaking ? 'speaking' : 'idle'));
            if (this.lipSync) this.lipSync.avatar = candidate;
            else this.lipSync = new LipSyncEngine(candidate);
            this.speechAnimator = new SpeechAnimator(candidate);
            this.speechAnimator.lipSync = this.lipSync;
            this.speechAnimator.setSyntheticMouthEnabled(syntheticMouth);
            this.speechPresentation.syncAvatar();
            this._lastAnimation = { name: '', at: 0 };
            this._appliedAvatarKey = failedVrm ? '' : selection.key;
            disposeCompanionAvatar(previous);
            candidate = null; // Ownership transferred to the visible scene.
            if (failedVrm) this._showError('No pude cargar tu VRM. Mostrando a IRIS.');
            else this._avatarSyncError = '';
        } finally {
            disposeCompanionAvatar(candidate);
            // Native avatar bytes are only needed while GLTFLoader parses them.
            if (url !== selection.resource) this.bridge.releaseCoreAssetUrl?.(url);
        }
    }

    async _rehydrateAfterConnect() {
        await this._fetchConfig();
        await this._avatarSync.request();
        void this.animationLibrary?.refresh();
        await this._refreshWakeState();
        await this._loadVisionAssistantState();
    }

    _setupLifecycle() {
        const sendState = (interaction = false) => {
            this.inboxVoice?.sync();
            this.ws?.send('surface_state', buildSurfaceStatePayload({
                visible: document.visibilityState !== 'hidden',
                focused: document.hasFocus?.() ?? true,
                interaction,
            }));
        };
        document.addEventListener('visibilitychange', () => {
            sendState(false);
            if (document.visibilityState === 'hidden') {
                this._cancelWindowWalk();
                this._stopLocalCamera({ releaseLease: true, reason: 'Cámara liberada al ocultar.' });
            } else {
                this._syncVisionAssistantCamera();
                void this._avatarSync.request();
            }
        });
        window.addEventListener('focus', () => {
            sendState(false);
            void this._avatarSync.request();
        });
        // Reconcile writes from the separate setup wizard and missed events.
        this._avatarConfigTimer = setInterval(() => {
            if (this.ws?.connected && document.visibilityState !== 'hidden') void this._avatarSync.request();
        }, 15000);
        window.addEventListener('blur', () => sendState(false));
        window.addEventListener('resize', () => this._onResize());
        // The controls also resize the stage without changing the window.
        const stageResize = new ResizeObserver(() => this._onResize());
        stageResize.observe(this.stage);
        window.addEventListener('pointerdown', () => this._markInteraction(), {
            capture: true,
            passive: true,
        });
        window.addEventListener('beforeunload', () => {
            stageResize.disconnect();
            if (this.cameraStream || this.cameraLeasePending) {
                this.ws?.send('camera_lease_release', {});
                this._stopLocalPerception();
                this._stopCameraTracks();
            }
            this.inboxVoice?.stop();
            this.inboxNotice?.close();
            this.speechPresentation.dispose();
            clearTimeout(this._bubbleTimer);
            this._cancelWindowWalk();
            this.ws?.disconnect();
            this.animationLibrary?.dispose();
            this._avatarSync.dispose();
            this.idle?.dispose?.();
            disposeCompanionAvatar(this.avatarManager);
            clearInterval(this._avatarConfigTimer);
            this.bridge?.dispose?.();
            clearTimeout(this._visionResumeTimer);
            cancelAnimationFrame(this._animationFrame);
            for (const pass of this.composer?.passes || []) pass.dispose?.();
            this.composer?.dispose();
            this.renderer?.dispose();
        });
    }

    _markInteraction() {
        const now = performance.now();
        if (now - this._lastSurfaceActivityAt < 600) return;
        this._lastSurfaceActivityAt = now;
        this.ws?.send('surface_activity', {});
    }

    _handleMessage(type, payload) {
        this.animationDirector?.handleRuntimeEvent(type, payload);
        switch (type) {
            case 'inbox_presentation':
                this.inboxNotice.present(payload, { surfaceId: this.surfaceId, documentLike: document,
                    quiet: this.quietMode, status: this.manualVoice?.state.busy ? 'listening' : document.body.dataset.operationalState });
                break;
            case 'inbox_voice':
                this.inboxVoice.present(payload);
                break;
            case 'inbox_voice_stop':
                this.inboxVoice.stop();
                break;
            case 'inbox_voice_ack':
            case 'inbox_presentation_ack':
                break;
            case 'animation_catalog_changed':
                void this.animationLibrary?.refresh();
                break;
            case 'hello':
                this._setOperationalState('idle');
                this._setWakeState({
                    active: Boolean(payload.hermes_wake_active),
                    state: payload.hermes_wake_active ? 'listening' : 'off',
                });
                this._announceSurfaceReady();
                this._loadVisionAssistantState();
                break;

            case 'surface_registered':
            case 'surface_state_ack':
                if (payload?.surface_id) this.surfaceId = payload.surface_id;
                break;

            case 'chat_history':
                if (this._chatHydrated) break;
                this._chatHydrated = true;
                for (const item of (payload.messages || []).slice(-6)) {
                    this._appendChat(item.role, item.content);
                }
                break;

            case 'animation':
                this._playAnimation(payload.name || 'idle', true);
                break;

            case 'speech_start': {
                this.speechPresentation.start(payload);
                this._showSpeech(payload.text || '');
                this._appendChat('IRIS', payload.text || '');
                this._setOperationalState('speaking');

                const snapshotName = payload.animacion_virtual || '';
                if (shouldPlaySnapshotAnimation(snapshotName, this._lastAnimation)) {
                    this._playAnimation(snapshotName, false);
                }
                break;
            }

            case 'speech_end':
                this.speechPresentation.end();
                this._setOperationalState('idle');
                clearTimeout(this._bubbleTimer);
                this._bubbleTimer = setTimeout(() => this._hideSpeech(), 4200);
                break;

            case 'voice_unavailable':
                this._showError(payload.message);
                break;

            case 'turn_started':
                this.routines?.turnStarted(payload.turn);
                break;

            case 'turn_ended':
                this.routines?.turnEnded(payload.turn);
                break;

            case 'emotion':
                this.speechPresentation.setEmotion(payload.emotion);
                break;

            case 'lip_sync':
                this.lipSync?.processVisemes?.(Array.isArray(payload.visemes) ? payload.visemes : []);
                break;

            case 'tts_audio':
                this.inboxVoice.stop();
                this.ttsAudioPlayer?.handle(payload);
                break;

            case 'subtitle':
                if (payload.text) this._showSpeech(payload.text);
                break;

            case 'status':
                this._handleRuntimeStatus(payload.status);
                break;

            case 'tool_progress':
                // Tool previews can contain commands or private arguments.
                // Keep progress in the header; the bubble is for Iris' reply.
                this._setOperationalState('tool');
                this.routines?.toolRunning(payload.turn);
                break;

            case 'hermes_voice_state':
                this.manualVoice?.apply(payload);
                this._syncControlRailVisibility();
                break;

            case 'hermes_wake_state':
                this.manualVoice?.showWakeState?.(payload);
                this._setWakeState(payload);
                if (payload.state === 'detected') this._showSpeech('Activación detectada · te escucho');
                if (payload.error) this._showError(payload.error);
                break;

            case 'vision_assistant_state':
                this._visionStateVersion = (this._visionStateVersion || 0) + 1;
                this.visionAssistant = { ...this.visionAssistant, ...(payload || {}) };
                this._scheduleVisionAssistantResume();
                this._syncVisionAssistantCamera();
                break;

            case 'vision_assistant_event':
                // Presence history is informational, including cached V1 events.
                break;

            case 'vision_assistant_notifications':
                if (payload?.experience) this.visionAssistant.experience = payload.experience;
                break;

            case 'vision_assistant_calibration':
                applyVisionCalibration(payload, { mocap: this.mocap,
                    surfaceId: this.surfaceId, vision: this.visionAssistant });
                break;

            case 'vision_assistant_presentation':
                void this.visionPresentation.present(payload, {
                    surfaceId: this.surfaceId, vision: this.visionAssistant,
                    status: document.body.dataset.operationalState, documentLike: document,
                    quiet: this.quietMode,
                    reducedMotion: document.body.dataset.reducedMotion === 'true'
                        || window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches,
                    idle: this.idle, director: this.animationDirector,
                });
                break;

            case 'voice_transcript':
                this.manualVoice?.showTranscript?.(payload);
                if (payload.error) this._showError(payload.error);
                break;

            case 'voice_partial':
                this.manualVoice?.showPartial?.(payload);
                break;

            case 'camera_lease_result':
                this._handleCameraLeaseResult(payload);
                break;

            case 'camera_lease_revoked':
                this._stopLocalCamera({
                    releaseLease: false,
                    reason: payload.reason === 'capability-policy-changed'
                        ? 'La cámara se cerró por la política de Capability Center.'
                        : 'La cámara pasó a otra superficie de IRIS.',
                });
                break;

            case 'camera_lease_state':
                this.visionAssistant = {
                    ...this.visionAssistant,
                    camera: { ...(payload || {}) },
                };
                if (this.cameraStream && payload.active && payload.surface_id !== this.surfaceId) {
                    this._stopLocalCamera({ releaseLease: false, reason: 'Cámara reasignada.' });
                } else if (!payload.active) {
                    this._syncVisionAssistantCamera();
                }
                break;

            case 'avatar_config_changed':
                void this._avatarSync.request(true);
                break;

            case 'screen_snapshot_request':
                void this._screenSnapshots?.handle(payload || {});
                break;
            case 'screen_snapshot_cancel':
                this._screenSnapshots?.cancel(payload?.request_id);
                break;
            case 'visual_request_notice':
                // Iris looked through the camera or the screen to answer.
                if (payload?.text) this._setOperationalState('tool', String(payload.text));
                break;
            case 'background_tasks':
                this._backgroundBar?.update(payload || {});
                break;
            case 'background_task_notice':
                if (payload?.text) this._appendChat('system', String(payload.text));
                break;
            case 'render_preferences':
                this._renderRevision = (this._renderRevision || 0) + 1;
                if (payload.render_fps_limit !== undefined) {
                    this.config.render_fps_limit = payload.render_fps_limit;
                    this._frameLimiter?.setLimit(payload.render_fps_limit);
                }
                if (payload.render_quality !== undefined) {
                    this.config.render_quality = payload.render_quality;
                    this.graphicsQuality?.setQuality(payload.render_quality);
                }
                break;

            case 'companion_preferences':
                this.config = { ...this.config, ...payload };
                this._applyCompanionPreferences(payload);
                break;

            case 'hermes_command_result':
                this._appendChat('system', payload.text || 'El comando no devolvió información.');
                break;

            case 'protocol_error':
                this._showError(payload.message || 'IRIS rechazó el mensaje.');
                break;

            default:
                break;
        }
    }

    _announceSurfaceReady() {
        const avatarReady = Boolean(this.avatarManager && this.ttsAudioPlayer);
        this.ws?.send('surface_ready', buildSurfaceReadyPayload({
            role: 'desktop_companion',
            surfaceId: this.surfaceId,
            visible: document.visibilityState !== 'hidden',
            focused: document.hasFocus?.() ?? true,
            capabilities: {
                renders_avatar: avatarReady,
                presents_vision: avatarReady,
                tunes_vision: true,
                plays_tts: avatarReady,
                reports_tts_playback: true,
                presents_inbox: true,
                speaks_inbox: avatarReady,
                captures_camera: true,
                accepts_chat: true,
                takes_screen_snapshots: true,
            },
        }));
    }

    async _playAnimation(name, dedicatedEvent) {
        const cleanName = String(name || '').trim() || 'idle';
        if (dedicatedEvent) this._lastAnimation = { name: cleanName, at: performance.now() };
        try {
            const result = await this.animationDirector?.play(cleanName);
            if (result?.movement === 'window-walk') void this._playWindowWalk();
        } catch (error) {
            console.warn('[companion] animation failed:', error);
        }
    }

    _locomotionDecision() {
        // With something in her hands she stays put, whatever a clip asks of the window.
        if (this.idle?.isSeated?.()) return { allowed: false, reason: 'holding' };
        return companionLocomotionDecision({
            enabled: this.locomotionEnabled,
            reducedMotion: document.body.dataset.reducedMotion === 'true',
            quietMode: this.quietMode,
            hovered: this._pointerHovered,
            visible: document.visibilityState !== 'hidden',
            native: Boolean(this.bridge?.native),
            operationalState: document.body.dataset.operationalState || 'idle',
        });
    }

    async _playWindowWalk() {
        if (!this._locomotionDecision().allowed) return;
        const generation = ++this._movementGeneration;
        this._movementPending = true;
        let result;
        try {
            result = await this.bridge.startWindowWalk(3100);
        } catch (error) {
            if (generation === this._movementGeneration) {
                console.warn('[companion] native locomotion failed:', error);
            }
            return;
        } finally {
            if (generation === this._movementGeneration) this._movementPending = false;
        }
        if (generation !== this._movementGeneration || !this._locomotionDecision().allowed) {
            this.bridge.cancelWindowWalk().catch(() => {});
            return;
        }
        if (!result?.started) return;

        this._windowWalkActive = true;
        clearTimeout(this._movementTimer);
        this.stage.classList.remove('is-window-walking');
        this.stage.dataset.walkDirection = result.direction || 'right';
        requestAnimationFrame(() => this.stage.classList.add('is-window-walking'));
        this._movementTimer = setTimeout(() => {
            this.stage.classList.remove('is-window-walking');
            this._windowWalkActive = false;
        }, Math.max(900, Number(result.durationMs) || 3100) + 100);
    }

    _cancelWindowWalk() {
        ++this._movementGeneration;
        this._movementPending = false;
        this._windowWalkActive = false;
        clearTimeout(this._movementTimer);
        this.stage?.classList.remove('is-window-walking');
        if (this.bridge?.native) this.bridge.cancelWindowWalk().catch(() => {});
    }

    _handleRuntimeStatus(status) {
        const state = companionStateForStatus(status);
        if (state) this._setOperationalState(state);
    }

    _setOperationalState(state, customLabel = '') {
        const clean = STATUS_LABELS[state] ? state : 'idle';
        document.body.dataset.operationalState = clean;
        if (this.statusText) this.statusText.textContent = customLabel || STATUS_LABELS[clean];
        if ((this._windowWalkActive || this._movementPending) && !this._locomotionDecision().allowed) {
            this._cancelWindowWalk();
        }
    }

    _showSpeech(text) {
        const clean = truncateText(text);
        if (!clean) return;
        clearTimeout(this._bubbleTimer);
        this.speechText.textContent = clean;
        this.speechBubble.hidden = false;
    }

    _hideSpeech() {
        this.speechBubble.hidden = true;
        this.speechText.textContent = '';
    }

    _showError(message) {
        this._showSpeech(message || 'Algo necesita atención.');
        clearTimeout(this._bubbleTimer);
        this._bubbleTimer = setTimeout(() => this._hideSpeech(), 4600);
        if (this.ws?.connected) this._setOperationalState('error');
        setTimeout(() => {
            if (this.ws?.connected && document.body.dataset.operationalState === 'error') {
                this._setOperationalState('idle');
            }
        }, 4200);
    }

    _setChatOpen(open) {
        const next = Boolean(open);
        this.chatPanel.hidden = !next;
        const button = document.getElementById('chat-toggle');
        button.setAttribute('aria-expanded', next ? 'true' : 'false');
        if (next) {
            this._markInteraction();
            requestAnimationFrame(() => this.chatInput.focus());
        }
    }

    _appendChat(role, text) {
        const clean = truncateText(text, 1200);
        if (!clean) return;
        const message = document.createElement('div');
        message.className = 'chat-message';
        message.dataset.role = role === 'user' ? 'user' : role === 'system' ? 'system' : 'IRIS';
        message.textContent = clean;
        this.chatLog.appendChild(message);
        while (this.chatLog.children.length > 12) this.chatLog.firstElementChild?.remove();
        this.chatLog.scrollTop = this.chatLog.scrollHeight;
    }

    _sendChat() {
        const text = this.chatInput.value.trim();
        if (!text) return;
        if (!this.ws?.connected) {
            this._showError('IRIS Core está desconectado. Abre IRIS OS para iniciar el servicio.');
            return;
        }
        const sent = text.startsWith('/')
            ? this.ws.send('hermes_command', { line: text })
            : this.ws.send('user_text', { text });
        if (!sent) {
            this._showError('No pude enviar el mensaje. Reintentando conexión…');
            return;
        }
        this._appendChat('user', text);
        this.chatInput.value = '';
        this._setOperationalState('thinking');
        this._markInteraction();
    }

    async _refreshWakeState() {
        try {
            const response = await this.bridge.apiFetch(
                '/api/hermes-wake/settings?probe=false',
                { cache: 'no-store' },
            );
            const snapshot = await response.json();
            if (!response.ok) throw new Error(snapshot.detail || `HTTP ${response.status}`);
            this.wakePhrase = snapshot.runtime?.phrase || snapshot.phrase || this.wakePhrase;
            this._setWakeState(snapshot.runtime || { active: false, state: 'off' });
        } catch {
            // WebSocket hello still provides the last confirmed active bit.
        }
    }

    async _loadVisionAssistantState() {
        const version = this._visionStateVersion || 0;
        try {
            const response = await this.bridge.apiFetch(
                '/api/vision-assistant/settings',
                { cache: 'no-store' },
            );
            const snapshot = await response.json();
            if (!response.ok) throw new Error(snapshot.detail || `HTTP ${response.status}`);
            if (version !== (this._visionStateVersion || 0)) return;
            this.visionAssistant = { ...this.visionAssistant, ...snapshot };
            this._scheduleVisionAssistantResume();
            this._syncVisionAssistantCamera();
        } catch (err) {
            console.warn('[companion vision] settings unavailable:', err);
        }
    }

    _scheduleVisionAssistantResume() {
        clearTimeout(this._visionResumeTimer);
        this._visionResumeTimer = null;
        if (!this.visionAssistant.enabled) return;
        const delayMs = (Number(this.visionAssistant.paused_until || 0) * 1000) - Date.now();
        if (!Number.isFinite(delayMs) || delayMs <= 0) return;
        this._visionResumeTimer = setTimeout(() => {
            this._visionResumeTimer = null;
            void this._loadVisionAssistantState();
        }, Math.min(delayMs + 150, 2_147_000_000));
    }

    _setWakeState(payload = {}) {
        const active = Boolean(payload.active);
        const state = String(payload.state || (active ? 'listening' : 'off'));
        this.wakeActive = active;
        this.wakeState = state;
        if (payload.phrase) this.wakePhrase = payload.phrase;
        this._syncControlRailVisibility();
        if (ACTIVE_WAKE_STATES.has(state) && ['detected', 'recording'].includes(state)) {
            this._setOperationalState('listening');
        } else if (state === 'processing') {
            this._setOperationalState('thinking');
        }
    }

    _toggleCamera() {
        this._markInteraction();
        if (this.cameraStream || this.cameraLeasePending) {
            this._stopLocalCamera({ releaseLease: true, reason: 'Cámara local desactivada.' });
            return;
        }
        this._requestCamera({ confirmed: true, visionOwned: false });
    }

    _requestCamera({ confirmed = false, visionOwned = false } = {}) {
        if (this.cameraStream || this.cameraLeasePending) return;
        if (!this.ws?.connected) {
            if (!visionOwned) this._showError('IRIS Core debe estar conectado antes de solicitar la cámara.');
            return;
        }
        this.cameraLeasePending = true;
        this._cameraRequestVisionOwned = Boolean(visionOwned);
        this.cameraButton.dataset.state = 'requesting';
        this.cameraButton.disabled = true;
        const sent = this.ws.send('camera_lease_request', {
            surface_id: this.surfaceId,
            purpose: 'mocap_and_emotion',
            confirmed: Boolean(confirmed),
        });
        if (!sent) {
            this.cameraLeasePending = false;
            this._cameraRequestVisionOwned = false;
            this._setCameraUi(false, 'error');
            if (!visionOwned) this._showError('No pude enviar la solicitud de cámara a IRIS Core.');
            return;
        }
        this._clearCameraLeaseTimer();
        this._cameraLeaseTimer = setTimeout(() => {
            if (!this.cameraLeasePending) return;
            this.cameraLeasePending = false;
            const quietFailure = this._cameraRequestVisionOwned;
            this._cameraRequestVisionOwned = false;
            // Core may have granted the lease even if its response was lost.
            // Release is idempotent and prevents a hidden orphan owner from
            // blocking the main IRIS surface.
            this.ws?.send('camera_lease_release', {});
            this._setCameraUi(false, quietFailure ? 'off' : 'error');
            if (!quietFailure) this._showError('IRIS Core no respondió a tiempo a la solicitud de cámara.');
        }, 5000);
    }

    _syncVisionAssistantCamera() {
        const paused = Number(this.visionAssistant.paused_until || 0) > Date.now() / 1000;
        const shouldOwnCamera = Boolean(
            this.visionAssistant.enabled
            && !paused
            && document.visibilityState !== 'hidden',
        );
        if (!shouldOwnCamera) {
            if (this._cameraStartedByVision || this._cameraRequestVisionOwned) {
                this._stopLocalCamera({ releaseLease: true });
            }
            return;
        }
        const lease = this.visionAssistant.camera || {};
        const cameraVerdict = this.visionAssistant.permissions?.camera?.verdict;
        if (cameraVerdict && cameraVerdict !== 'allow') return;
        if (lease.active && lease.surface_id && lease.surface_id !== this.surfaceId) return;
        if (!this.cameraStream && !this.cameraLeasePending) {
            this._requestCamera({ confirmed: false, visionOwned: true });
        }
    }

    async _handleCameraLeaseResult(payload = {}) {
        this._clearCameraLeaseTimer();
        if (!payload.granted) {
            if (payload.released) {
                this.cameraLeasePending = false;
                this._setCameraUi(false, 'off');
                return;
            }
            if (this.cameraLeasePending) {
                const quietFailure = this._cameraRequestVisionOwned;
                this.cameraLeasePending = false;
                this._cameraRequestVisionOwned = false;
                this._setCameraUi(false, quietFailure ? 'off' : 'error');
                if (!quietFailure) {
                    const detail = payload.reason === 'permission_denied'
                        ? 'La cámara está bloqueada en Capability Center.'
                        : payload.reason === 'permission_required'
                            ? 'La cámara necesita confirmación explícita.'
                            : 'IRIS no pudo conceder el uso exclusivo de la cámara.';
                    this._showError(detail);
                }
            }
            return;
        }
        if (!this.cameraLeasePending) {
            // A late grant after the user cancelled must be released, never opened.
            this.ws?.send('camera_lease_release', {});
            return;
        }
        try {
            if (!navigator.mediaDevices?.getUserMedia) {
                throw new Error('Este WebView no permite acceso local a la cámara.');
            }
            const stream = await navigator.mediaDevices.getUserMedia(
                COMPANION_CAMERA_CONSTRAINTS,
            );
            if (!this.cameraLeasePending || document.visibilityState === 'hidden') {
                for (const track of stream.getTracks()) track.stop();
                this.ws?.send('camera_lease_release', {});
                return;
            }
            this.cameraStream = stream;
            for (const track of stream.getVideoTracks?.() || []) {
                track.addEventListener('ended', () => {
                    if (this.cameraStream === stream) {
                        this._stopLocalCamera({
                            releaseLease: true,
                            reason: 'La cámara dejó de estar disponible.',
                        });
                    }
                }, { once: true });
            }
            this.cameraPreview.srcObject = stream;
            await this.cameraPreview.play().catch(() => {});
            this._cameraStartedByVision = this._cameraRequestVisionOwned;
            this._cameraRequestVisionOwned = false;
            this.cameraLeasePending = false;
            this._setCameraUi(true, 'active');
            this._setCameraPrivacyState('loading');
            this._showSpeech('Cámara activa · análisis local, sin enviar vídeo');
            this._startLocalPerception().catch((error) => {
                if (this.cameraStream !== stream) return;
                console.warn('[companion] local camera analysis unavailable:', error);
                this._setCameraPrivacyState('preview');
            });
        } catch (error) {
            this.cameraLeasePending = false;
            this._cameraRequestVisionOwned = false;
            this._cameraStartedByVision = false;
            this.ws?.send('camera_lease_release', {});
            this._setCameraUi(false, 'error');
            this._showError(error?.message || 'No pude abrir la cámara local.');
        }
    }

    _stopCameraTracks() {
        for (const track of this.cameraStream?.getTracks?.() || []) track.stop();
        this.cameraStream = null;
        if (this.cameraPreview) this.cameraPreview.srcObject = null;
    }

    _clearCameraLeaseTimer() {
        if (this._cameraLeaseTimer) clearTimeout(this._cameraLeaseTimer);
        this._cameraLeaseTimer = null;
    }

    _stopLocalCamera({ releaseLease = true, reason = '' } = {}) {
        const hadCamera = Boolean(this.cameraStream || this.cameraLeasePending);
        this._stopLocalPerception();
        this._stopCameraTracks();
        this._clearCameraLeaseTimer();
        this.cameraLeasePending = false;
        this._cameraRequestVisionOwned = false;
        this._cameraStartedByVision = false;
        if (releaseLease) this.ws?.send('camera_lease_release', {});
        this._setCameraUi(false, 'off');
        if (reason && hadCamera) this._showSpeech(reason);
    }

    _setCameraUi(active, state) {
        this.cameraButton.disabled = false;
        this.cameraButton.dataset.state = state;
        this.cameraButton.setAttribute('aria-pressed', active ? 'true' : 'false');
        this.cameraButton.setAttribute(
            'aria-label',
            active ? 'Desactivar cámara local' : 'Activar cámara local privada',
        );
        this.cameraPrivacy.hidden = !active;
        if (!active) this._setCameraPrivacyState('off');
        this._syncControlRailVisibility();
    }

    _setCameraPrivacyState(state) {
        if (!this.cameraPrivacy || !this.cameraPrivacyLabel) return;
        const labels = {
            off: 'Cámara local · sin vídeo enviado',
            loading: 'Preparando análisis local · aún sin señales',
            analysis: 'Vídeo local · señales resumidas a IRIS Core',
            preview: 'Vista local · análisis no disponible',
        };
        const clean = labels[state] ? state : 'preview';
        this.cameraPrivacy.dataset.analysisState = clean;
        this.cameraPrivacyLabel.textContent = labels[clean];
    }

    async _startLocalPerception() {
        const generation = ++this._cameraAnalysisGeneration;
        const [vision, cameraModule, emotionModule] = await Promise.all([
            import('@mediapipe/tasks-vision'),
            import('../js/camera_mocap.js?v=4'),
            import('../js/emotion_classifier.js'),
        ]);
        if (generation !== this._cameraAnalysisGeneration || !this.cameraStream) return;

        if (!this.mocap) {
            const base = new URL('./mediapipe/', document.baseURI);
            this.mocap = new cameraModule.CameraMocap({
                moduleLoader: async () => vision,
                wasmBase: new URL('wasm/', base).toString(),
                modelUrl: new URL('face_landmarker.task', base).toString(),
            });
        }
        if (!this.emotionClassifier) {
            this.emotionClassifier = new emotionModule.EmotionClassifier();
        }

        await this.mocap.start(this.cameraPreview);
        if (generation !== this._cameraAnalysisGeneration || !this.cameraStream) {
            return;
        }
        this._lastMocapTimestamp = -1;
        this._emotionEmitAccum = 0;
        this._visionEmitAccum = 0;
        this._setCameraPrivacyState('analysis');
    }

    _stopLocalPerception() {
        this._cameraAnalysisGeneration += 1;
        this.mocap?.stop?.();
        this.emotionClassifier?.reset?.();
        this._lastMocapTimestamp = -1;
        this._emotionEmitAccum = 0;
        this._visionEmitAccum = 0;
    }

    _updateLocalPerception(delta) {
        const visionPaused = Number(this.visionAssistant.paused_until || 0) > Date.now() / 1000;
        this.mocap?.configureVision?.(this.visionAssistant);
        if (this.visionAssistant.enabled && !visionPaused) {
            this._visionEmitAccum += delta;
            if (this._visionEmitAccum >= 1.5) {
                this._visionEmitAccum = 0;
                this.ws?.send?.('vision_activity', buildVisionActivity({
                    documentLike: document,
                    status: this.manualVoice?.state.busy ? 'listening' : document.body.dataset.operationalState,
                    quiet: this.quietMode,
                }));
                const signal = this.cameraStream && buildVisionSignal(this.mocap?.latestPresence?.(), {
                    nowMs: performance.now(),
                });
                if (signal) this.ws?.send?.('vision_signal', signal);
                if (this.cameraStream && this.mocap?.running) {
                    this.ws?.send?.('vision_sensor_diagnostics', this.mocap.diagnostics());
                }
            }
        } else {
            this._visionEmitAccum = 0;
        }
        if (!this.cameraStream || !this.emotionClassifier) return;
        this._emotionEmitAccum += delta;
        const result = this.mocap?.latest?.();
        if (!result?.blendshapes || result.timestamp === this._lastMocapTimestamp) return;
        this._lastMocapTimestamp = result.timestamp;
        const emotion = this.emotionClassifier.classify(result.blendshapes);
        if (!emotion) return;
        this.idle?.feedUserEmotion?.(emotion);
        const contextAllowed =
            this.visionAssistant.permissions?.context?.verdict === 'allow';
        if (contextAllowed && this._emotionEmitAccum >= 1) {
            this._emotionEmitAccum = 0;
            this.ws?.send?.('user_emotion', buildCompanionEmotionPayload(emotion));
        } else if (!contextAllowed) {
            this._emotionEmitAccum = 0;
        }
    }

    _onResize() {
        const layout = this.graphicsQuality.resize();
        if (!layout) return;
        this.camera.aspect = layout.width / layout.height;
        this.camera.updateProjectionMatrix();
    }

    _animate() {
        this.inboxVoice?.sync();
        this._animationFrame = requestAnimationFrame(() => this._animate());
        if (this._frameLimiter && !this._frameLimiter.ready(performance.now(), document.visibilityState !== 'hidden')) return;
        if (document.visibilityState === 'hidden') return;
        const delta = Math.min(this.clock.getDelta(), 0.05);
        const elapsed = this.clock.getElapsedTime();
        this.avatarManager?.update(delta, elapsed, { deferVRM: true });
        this.lipSync?.update(delta);
        this.animationDirector?.updateActivity();
        this.idle?.update(delta, elapsed);
        // In this order: what is waiting for her hands is dropped before it can start.
        this.routines?.update();
        this.body?.update();
        this.props?.update(delta, this.avatarManager?.avatar?.vrm, this.idle?.heldProp?.());
        this._updateLocalPerception(delta);
        const explicitAnimationActive = Boolean(
            this.idle?.isExplicitAnimationActive?.()
            || this.idle?._vrmaExternalClip
            || this.idle?.currentPose,
        );
        if (explicitAnimationActive) {
            // The clip mixer has replaced the head/chest pose captured when
            // speech began.  Discard that stale base just like Face does, so
            // speech micro-motion resumes from the post-clip pose instead of
            // snapping back toward the pre-clip orientation.
            this.speechAnimator?.yieldToExternalClip?.();
        } else {
            this.speechAnimator?.update(delta, elapsed);
        }
        const avatar = this.avatarManager?.avatar;
        if (avatar?.vrm?.lookAt) {
            avatar.vrm.lookAt.target = this.camera;
            avatar.vrm.lookAt.autoUpdate = !avatar.animationOwnsBlink
                && avatar.poseFacialProfile?.eyes?.gaze === 'natural';
        }
        this.avatarManager?.finalizeFrame(delta);
        this.composer?.render();
    }
}

const companion = new IrisCompanionApp();
companion.init().catch((error) => {
    console.error('[companion] boot failed:', error);
    companion.loading?.classList.add('is-ready');
    companion._showError('No pude materializar el avatar. Revisa el diagnóstico de IRIS OS.');
});

window.irisCompanion = companion;
