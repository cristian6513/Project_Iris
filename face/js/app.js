import { AnimationLibrarySync } from './animation_library_sync.js';
/**
 * IRIS Face — Main Application (Full Implementation)
 *
 * Three.js scene with:
 * - PerspectiveCamera with subtle auto-orbit
 * - 3D Room environment with interactive furniture
 * - Gold + blue lighting rig with bloom post-processing
 * - AvatarManager: the person's VRM, or IRIS' (face/animations/iris.vrm)
 * - Room Actions: contextual animations (desk, bookshelf, effects)
 * - Idle System: natural resting animations
 * - Side Panel: camera feed, chat transcript, brain JSON
 * - ParticleSystem, LipSync, Subtitles, WebSocket
 * - Config received from backend at /api/config
 */

import { AvatarSwapCoordinator, commitAvatarReplacement } from './avatar_lifecycle.js?v=4';
import { requestDesktopSetup } from './desktop_host.js';
import * as THREE from 'three';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
import { guardBloom } from './bloom_guard.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

import { AvatarManager } from './avatar.js?v=23';
import { avatarUrl, IRIS_AVATAR_URL } from './avatar_state.js?v=4';
import { ParticleSystem } from './particles.js?v=2';
import { WebSocketManager } from './websocket.js?v=3';
import { ManualVoiceController } from './manual_voice.js?v=4';
import { SubtitleManager } from './subtitles.js?v=1';
import { LipSyncEngine } from './lipsync.js?v=3';

// ── New modules ──
import { RoomEnvironment } from './room.js?v=108';
import { bindRoomAtmosphereControls } from './room_atmosphere_controls.js?v=2';
import { ConversationCamera, ROOM_DETAIL_PRESETS, frameRoomPreset } from './room_framing.js?v=6';
import { RoomAvatarZoom } from './room_avatar_zoom.js?v=1';
import { RoomLife, RoomRoutines } from './room_routines.js?v=5';
import { SceneActionManager } from './scene_actions.js?v=58';
import { getRoomAction } from './room_actions.js?v=27';
import { FloatingBook, ThinkingCloud, DataStream, MemorySymbol } from './contextual_fx.js?v=2';
import { IdleSystem } from './idle_system.js?v=54';
import { SidePanel } from './panel.js?v=57';
import { RoomSurfaceVisibility } from './room_surface_visibility.js?v=1';
import { normalizeRoomText, ROOM_STATUS_LABELS } from './room_labels.js?v=1';
import { AmbientAudio } from './ambient_audio.js?v=2';
import { CameraMocap } from './camera_mocap.js?v=4';
import { applyVisionCalibration } from './vision_calibration_command.js?v=1';
import { buildVisionSignal } from './vision_signal_policy.js?v=2';
import { buildVisionActivity } from './vision_activity.js?v=1';
import { VisionPresentation } from './vision_presentation.js?v=2';
import { InboxNotice } from './inbox_notice.js?v=2';
import { InboxVoicePlayer } from './inbox_voice.js?v=1';
import { CalendarNotice } from './calendar_notice.js?v=6';
import { AppNotice } from './app_notice.js?v=1';
import { askIrisSender } from './app_ask_iris.js?v=3';
import { EmotionClassifier } from './emotion_classifier.js';
import { SpeechAnimator } from './speech_animator.js?v=5';
import { MicroLife } from './micro_life.js?v=10';
import { HermesActivityCanvas, refreshAppCatalog } from './hermes_activity.js?v=144';
import { loadInstalledApps } from './os_app_sources.js?v=4';
import { openChatSurface } from './open_chat_surface.js?v=1';
import { SnapshotPrompt, SnapshotRequests } from './vision_snapshot.js?v=4';
import { ScreenSnapshotRequests } from './screen_snapshot.js?v=2';
import { FrameLimiter } from './frame_limiter.js?v=2';
import { GraphicsQuality, avatarTextureLimit } from './graphics_quality.js?v=2';
import { prepareNotificationChatDraft } from './notification_chat_draft.js?v=1';
import { AnimationDirector } from './animation_director.js?v=6';
import { TtsAudioPlayer } from './tts_audio_player.js?v=4';
import { SpeechPresentation } from './speech_presentation.js?v=1';
import {
    buildSurfaceReadyPayload,
    buildSurfaceStatePayload,
    getOrCreateSurfaceId,
} from './surface_protocol.js?v=8';

// ── Constants ──
// Avatars are fitted to 2.5 units tall around y = 0, so their soles start at
// -1.25; this lifts them onto the circular rug (y -1.185) with a few
// millimetres to spare for idle clips that dip the hips.
const AVATAR_STAGE_VISUAL_LIFT = 0.07;

const INSPECT_CAMERA_PRESETS = Object.freeze({
    frontView: {
        label: 'Front',
        position: [0.2, 1.55, 6.85],
        target: [0.0, 0.55, -0.55],
        fov: 45,
        duration: 720,
    },
    // Side and back cameras stay inside the walls (x ±5.04, window z -4.4);
    // from outside, the wall boxes filled the whole view with black.
    leftView: {
        label: 'Left',
        position: [-4.5, 1.7, 0.35],
        target: [0.4, 0.5, -0.6],
        fov: 52,
        duration: 760,
    },
    rightView: {
        label: 'Right',
        position: [4.5, 1.7, 0.4],
        target: [-0.4, 0.5, -0.6],
        fov: 52,
        duration: 760,
    },
    backView: {
        label: 'Back',
        position: [0.0, 1.9, -3.95],
        target: [0.0, 0.35, 0.8],
        fov: 52,
        duration: 760,
    },
    topView: {
        label: 'Top',
        position: [0.15, 8.9, -0.2],
        target: [0.0, -0.15, -0.45],
        fov: 50,
        duration: 820,
    },
    isometricView: ROOM_DETAIL_PRESETS.isometricView,
    avatarCloseUp: {
        label: 'Avatar',
        position: [0.35, 1.35, 3.15],
        target: [0.02, 0.82, 0.0],
        fov: 36,
        duration: 620,
    },
    deskView: {
        label: 'Desk',
        position: [4.35, 1.25, -0.4],
        target: [2.55, 0.42, -2.75],
        fov: 42,
        duration: 680,
    },
    sofaView: ROOM_DETAIL_PRESETS.sofaView,
    bedView: ROOM_DETAIL_PRESETS.bedView,
    windowView: {
        label: 'Window',
        position: [0.0, 1.45, 3.15],
        target: [0.0, 1.0, -4.55],
        fov: 46,
        duration: 700,
    },
});

const DEFAULT_INSPECT_PRESET = 'isometricView';

const INSPECT_ZONE_LABELS = Object.freeze({
    overview: 'Vista general',
    platform: 'Plataforma del avatar',
    pc: 'Escritorio del PC',
    sofa: 'Sofá',
    bed: 'Cama',
    window: 'Ventana',
    bookshelf: 'Librero',
});

const INSPECT_ZONE_BY_PRESET = Object.freeze({
    frontView: 'overview',
    leftView: 'overview',
    rightView: 'overview',
    backView: 'overview',
    topView: 'overview',
    isometricView: 'overview',
    avatarCloseUp: 'platform',
    deskView: 'pc',
    sofaView: 'sofa',
    bedView: 'bed',
    windowView: 'window',
});

const INSPECT_ZONE_BY_HOTSPOT = Object.freeze({
    centerSpot: 'platform',
    platformSpot: 'platform',
    pcSpot: 'pc',
    pcSeatSpot: 'pc',
    sofaSpot: 'sofa',
    sofaSeatSpot: 'sofa',
    deskSeatSpot: 'pc',
    bedSpot: 'bed',
    bedLieSpot: 'bed',
    bedSeatSpot: 'bed',
    windowSpot: 'window',
    bookshelfSpot: 'bookshelf',
    bookReadSpot: 'bookshelf',
});

class IrisApp {
    constructor() {
        this.canvas = document.getElementById('IRIS-canvas');
        this.loadingScreen = document.getElementById('loading-screen');
        this.clock = new THREE.Clock();

        // Three.js core
        this.scene = null;
        this.camera = null;
        this.renderer = null;
        this.composer = null;
        this.orbitControls = null;
        this.inspectorMode = false;
        this.avatarZoom = new RoomAvatarZoom();
        this.roomLife = new RoomLife();
        this.inspectPresetTransition = null;
        this.activeInspectPreset = null;

        // Hermes owns wake detection and command capture in its native runtime.
        this.hermesWakeActive = false;
        this._wakeTogglePromise = null;

        // Modules
        /** @type {AvatarManager} */
        this.avatarManager = null;
        this.particles = null;
        this.ws = null;
        this.surfaceId = getOrCreateSurfaceId('face');
        this._surfaceProtocolBound = false;
        this._lastSurfaceActivityAt = 0;
        this._continuityHistoryFingerprint = '';
        this.visionAssistant = {
            enabled: false,
            paused_until: 0,
            state: 'off',
            cooldown_minutes: 25,
            retention_days: 7,
        };
        this._visionEmitAccum = 0;
        this.visionPresentation = new VisionPresentation();
        this.calendarNotice = new CalendarNotice({
            send: (receipt) => this.ws?.send?.('calendar_reminder_receipt', receipt),
            onOpenCalendar: (payload) => {
                this.activityCanvas?.openCalendar?.(payload);
            },
        });
        // Notices installed apps scheduled (core/app_notices.py), shown when due even with the app closed.
        this.appNotice = new AppNotice({
            send: (receipt) => this.ws?.send?.('app_notice_receipt', receipt),
            onOpenApp: (appId) => this.activityCanvas?.openApp?.(appId),
        });
        document.addEventListener('visibilitychange', () => {
            if (document.visibilityState === 'hidden') { this.calendarNotice.close(); this.appNotice.close(); }
        });
        // F07: the silent notice on return; its receipt tells Core whether it was really shown.
        this.inboxNotice = new InboxNotice({
            send: (receipt) => this.ws?.send?.('inbox_presentation_receipt', receipt),
            onOpenInbox: () => this.activityCanvas?.openInbox?.(),
        });
        this.inboxVoice = new InboxVoicePlayer({
            send: (receipt) => this.ws?.send?.('inbox_voice_receipt', receipt),
            getLipSync: () => this.lipSync, getSpeechAnimator: () => this.speechAnimator,
            getContext: () => ({ connected: this.ws?.connected === true, surfaceId: this.surfaceId,
                documentLike: document, status: this.manualVoice?.state.busy || this.hermesWakeActive ? 'listening' : this._visionStatus,
                chatBusy: !!this.calendarNotice?.current }),
        });
        this._visionCameraPending = false;
        this._visionCameraRequestToken = 0;
        this._visionResumeTimer = null;
        this._visionAnalyzerState = 'off';
        this.subtitles = null;
        this.lipSync = null;
        /** @type {TtsAudioPlayer} */
        this.ttsAudioPlayer = null;

        // New modules
        /** @type {RoomEnvironment} */
        this.room = null;
        /** @type {SceneActionManager} */
        this.sceneActions = null;
        /** @type {IdleSystem} */
        this.idle = null;
        /** @type {AnimationDirector} */
        this.animationDirector = null;
        /** @type {SidePanel} */
        this.panel = null;
        /** @type {AmbientAudio} */
        this.ambientAudio = null;

        // Local perception remains independent of the avatar pose.
        /** @type {CameraMocap|null} */
        this.mocap = null;
        this._cameraAnalysisGeneration = 0;

        // Contextual effects
        this.effects = {};

        // State
        this.currentEmotion = 'neutral';
        this.config = {
            render_fps_limit: 60,
            render_quality: 'balanced',
            avatar_vrm_path: '',
            room_enabled: true,
            room_theme: 'cyber',
        };
        this._frameLimiter = new FrameLimiter();
        this.graphicsQuality = new GraphicsQuality({
            getRenderer: () => this.renderer, getComposer: () => this.composer,
            getBloom: () => this._bloomPass, getRoom: () => this.room, updateStyle: true,
        });
        this.speechPresentation = new SpeechPresentation({
            getIdle: () => this.idle,
            getLipSync: () => this.lipSync,
            getSpeechAnimator: () => this.speechAnimator,
            getAudioPlayer: () => this.ttsAudioPlayer,
            applyExpression: emotion => {
                this.avatarManager?.setEmotion(emotion);
                this.microLife?.setEmotion(emotion);
            },
            onMood: emotion => this._showEmotion(emotion),
            onSpeaking: speaking => this._setSpeakingVisuals(speaking),
        });
    }

    // ────────────────────────────────────────────────────────
    //  Boot sequence
    // ────────────────────────────────────────────────────────

    applyAvatarStageLift() {
        const group = this.avatarManager?.avatar?.group;
        if (!group || group.userData.stageVisualLiftApplied) return;
        group.position.y += AVATAR_STAGE_VISUAL_LIFT;
        group.userData.stageVisualLiftApplied = true;
    }

    async init() {
        // 1. Try fetching config from backend (non-blocking)
        await this._fetchConfig();
        this._normalizeUiText();

        // 2. Three.js setup
        this._setupRenderer();
        this._setupScene();
        this._setupCamera();
        this._setupLights();
        this._setupPostProcessing();
        // Publish the app instance EARLY so the avatar loader can
        // reach `window.iris.renderer.capabilities.getMaxAnisotropy()`
        // when bumping VRM texture quality on first boot. The full
        // assignment at the end of init() still happens for everything
        // else; this is just a forward declaration of the renderer.
        window.iris = this;
        window.IRIS = this;

        // 3. Room environment
        if (this.config.room_enabled) {
            this.room = new RoomEnvironment(this.scene);
            await this.room.init();
            const activeRoom = document.querySelector('.room-btn.active')?.dataset.room
                || this.config.room_theme
                || 'cyber';
            this.config.room_theme = activeRoom;
            this.room.setTheme(activeRoom);
            this.graphicsQuality.resize();
        }

        // 4. Avatar: the person's own VRM, or IRIS' (also when theirs cannot be loaded)
        const textureLimit = () => avatarTextureLimit(this.config.render_quality);
        this.avatarManager = new AvatarManager(this.scene, avatarUrl(this.config), IRIS_AVATAR_URL, { textureLimit });
        try {
            await this.avatarManager.init();
            if (this.config.avatar_vrm_path && this.avatarManager.vrmUrl === IRIS_AVATAR_URL) {
                this._avatarNotice = 'No pude cargar tu modelo VRM. Te muestro a IRIS mientras tanto.';
            }
        } catch (error) {
            // Chat, voice and the desktop keep working without a model on stage.
            console.error('[avatar] No VRM could be loaded:', error);
            this._avatarNotice = 'No pude cargar el avatar. Iris sigue funcionando sin él.';
        }
        this.applyAvatarStageLift();
        this._avatarSwap = new AvatarSwapCoordinator({
            create: url => new AvatarManager(new THREE.Scene(), url, IRIS_AVATAR_URL, { textureLimit }),
            commit: candidate => {
                const previous = commitAvatarReplacement(this, candidate, {
                    createIdle: manager => new IdleSystem(manager),
                    createSpeech: manager => new SpeechAnimator(manager),
                });
                this.speechPresentation.syncAvatar();
                this.sceneActions?.prepareTeleport(this.renderer, this.camera);
                this.config.avatar_vrm_path = candidate.vrmUrl === IRIS_AVATAR_URL ? '' : candidate.vrmUrl;
                void (this.animationLibrary?.refresh() || this.idle.reloadManifest()).catch(error =>
                    console.warn('[avatar] Manifest loading failed:', error));
                return previous;
            },
        });
        window.addEventListener('beforeunload', () => {
            cancelAnimationFrame(this._animationFrame);
            this.speechPresentation.dispose();
            this.room?.windowBackdrop?.dispose();
            this.animationLibrary?.dispose();
            this._avatarSwap.dispose();
            this.idle?.dispose();
            this.avatarManager?.dispose();
            for (const pass of this.composer?.passes || []) pass.dispose?.();
            this.composer?.dispose();
            this.renderer?.dispose();
        });

        // 5. Particles (ambient)
        this.particles = new ParticleSystem(this.scene);
        this.particles.init();

        // 6. Contextual FX
        this.effects = {
            floatingBook: new FloatingBook(this.scene),
            thinkingCloud: new ThinkingCloud(this.scene),
            dataStream: new DataStream(this.scene),
            memorySymbol: new MemorySymbol(this.scene),
        };

        // 7. Scene Action Manager
        if (this.room) {
            this.sceneActions = new SceneActionManager(
                this.scene, this.avatarManager, this.room, this.effects,
                {
                    onEmotion: (emotion) => this._setEmotion(emotion),
                    onCameraPreset: (preset) => {
                        if (this.inspectorMode) this.transitionToInspectPreset(preset);
                    },
                    // A cut, while there is nobody to watch: a glide would have her appear out of frame.
                    onLanded: () => this.conversationCamera?.reset(),
                },
            );
        }

        // 8. Idle System
        this.idle = new IdleSystem(this.avatarManager);
        this.animationLibrary = new AnimationLibrarySync({ getIdle: () => this.idle, onError: message => this.subtitles?.show(message, 5000) });
        this.animationLibrary.start();
        this.sceneActions?.setIdleSystem?.(this.idle);
        if (this.sceneActions) {
            this.roomRoutines = new RoomRoutines({
                scenes: this.sceneActions,
                context: () => ({
                    life: this.roomLife.value,
                    reducedMotion: document.documentElement.dataset.irisReducedMotion === 'true'
                        || Boolean(window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches),
                    visible: document.visibilityState === 'visible'
                        && !document.body.classList.contains('IRIS-workbench-focus'),
                    exploring: this.inspectorMode,
                    activity: this.animationDirector?.activity.state ?? 'idle',
                }),
            });
        }
        this.animationDirector = new AnimationDirector({
            getIdle: () => this.idle,
            getAvatarManager: () => this.avatarManager,
            onMood: (clip) => this._syncFaceMoodToClip(clip),
            surface: 'face',
        });

        // 9. Subtitles & Lip sync
        this.subtitles = new SubtitleManager();
        this.lipSync = new LipSyncEngine(this.avatarManager);

        // 9b. Speech Animator — micro-motion overlaid while talking
        // (head bob, faster chest, shoulder sway, eyebrow emphasis)
        // so the avatar doesn't read as a flapping-mouth statue.
        // Activated by `speech_start`/`speech_end` events; per-frame
        // update runs after avatarManager.update so its writes
        // layer additively on top of clip animation.
        this.speechAnimator = new SpeechAnimator(this.avatarManager);
        this.speechAnimator.lipSync = this.lipSync;
        this.ttsAudioPlayer = new TtsAudioPlayer({
            getLipSync: () => this.lipSync,
            getSpeechAnimator: () => this.speechAnimator,
            onPlaybackResult: (payload) => this.ws?.send('tts_playback_result', payload),
        });

        // 9c. MicroLife — Tier 1 presence layer (gaze smoothing +
        // Poisson blinking + saccades + eyes-before-head + breathing).
        // Owns the avatar's blinker and breathing from this point on;
        // the legacy code paths inside VRMAvatar self-suppress when
        // `microLifeOwnsBlink` / `microLifeOwnsBreathing` flags flip.
        // Press `M` to toggle the live tuning HUD.
        this.microLife = new MicroLife({
            avatarManager: this.avatarManager,
            camera: this.camera,
        });
        this.microLife.wireSpeechEvents();
        // Hand off ownership so the legacy blinker / breathing in
        // avatar.js stop fighting the new system.
        if (this.avatarManager?.avatar) {
            this.avatarManager.avatar.microLifeOwnsBlink = true;
            this.avatarManager.avatar.microLifeOwnsBreathing = true;
        }
        // Hotkey: M opens / closes the tuning HUD. Tuning is live —
        // no reload needed when dragging sliders.
        //
        // Guard against typing-in-chat triggering the HUD: ignore the
        // key when the focused element is a text input, textarea or
        // contentEditable surface. Without this, writing "mañana" in
        // the chat would toggle the HUD on every "m".
        window.addEventListener('keydown', (ev) => {
            if (ev.key !== 'm' && ev.key !== 'M') return;
            const t = ev.target;
            const tag = (t?.tagName || '').toUpperCase();
            if (tag === 'INPUT' || tag === 'TEXTAREA' || t?.isContentEditable) return;
            if (this.microLife._hud) this.microLife.detachDebugHUD();
            else this.microLife.attachDebugHUD();
        });

        // 10. Side Panel
        this.panel = new SidePanel();
        if (this._avatarNotice) {
            this.subtitles.show(this._avatarNotice, 7000);
            this.panel.addMessage('system', this._avatarNotice);
        }
        this.panel.room = this.room;           // wire room for theme switching
        this.panel.avatarZoom = this.avatarZoom;
        this.panel.roomLife = this.roomLife;
        this.panel._moduleRefreshers.get('settings')?.();
        this.panel.canSendMessage = () => Boolean(this.ws?.connected);
        // Cancels the background task only; «Detener» keeps stopping the current reply.
        this.panel.onCancelBackgroundTask = id => this.ws?.send('background_task_cancel', { id });
        // Frees a start nobody could confirm, after the bar's explained second click.
        this.panel.onReleaseBackgroundTask = id => this.ws?.send('background_task_release', { id });
        this.panel.onSendMessage = (text, meta = {}) => {
            const images = Array.isArray(meta.images) ? meta.images.filter(Boolean) : [];
            const payload = {
                text: text || '',
                attachments: Array.isArray(meta.attachments) ? meta.attachments : [],
            };
            // F03b: the composer's camera button asks Iris to look now.
            if (meta.camera_question === true) payload.camera_question = true;
            if (images.length > 0) {
                payload.images = images;
                payload.reasoning_effort = meta.reasoning_effort || 'none';
            } else if (meta.reasoning_effort) {
                payload.reasoning_effort = meta.reasoning_effort;
            }
            const sent = this.ws?.send('user_text', payload) === true;
            if (sent) this.activityCanvas?.handleUserMessage?.(text || '');
            return sent;
        };
        this.panel.onExecuteCommand = (line, meta = {}) => {
            return this.ws?.send('hermes_command', {
                line,
                reasoning_effort: meta.reasoning_effort || this.panel.getReasoningEffort(),
            }) === true;
        };
        this.panel.onCommandAction = (detail = {}) => this._handleCommandAction(detail);
        this.panel.onCameraKillSwitch = () => {
            void this._disableVisionAssistantFromKillSwitch();
        };
        this.panel.onLaunchWizard = () => {
            if (requestDesktopSetup()) return;
            this.ws.send('launch_wizard', {});
        };
        window.addEventListener('IRIS:continuity-session-resumed', (event) => {
            this._replaceChatHistory(event.detail || {});
        });

        // F17: apps installed from Iris Store join the catalog before the desktop is built
        // (at most 4 s; without Core's answer only the bundled apps load).
        await loadInstalledApps();
        refreshAppCatalog();

        // 10a. Hermes Activity Canvas — the desk monitor becomes a
        // lightweight workbench showing whether Hermes is idle,
        // thinking, using tools/skills, or waiting on something.
        this.activityCanvas = new HermesActivityCanvas({
            room: this.room,
            camera: this.camera,
            renderer: this.renderer,
            initialTheme: this.config.visual_theme_selected ? this.config.visual_theme : '',
            onOpen: () => this._enterWorkbenchFocusMode(),
            onClose: () => this._exitWorkbenchFocusMode(),
            onOpenChat: () => openChatSurface(this),
            acquireCamera: (consumer, options) => this.panel?.acquireCamera?.(consumer, options),
            releaseCamera: (consumer) => this.panel?.releaseCamera?.(consumer),
            getCameraStream: () => this.panel?.camStream || null,
            getCameraError: () => this.panel?.cameraError || '',
            onVisionSettingsChanged: (state) => this._applyVisionAssistantState(state),
            onPrepareNotificationConversation: draft => prepareNotificationChatDraft(
                this.panel, draft, () => {
                    this.activityCanvas?.close?.();
                    this.panel?.toggle?.(true);
                },
            ),
            onFocusDesk: () => {
                if (this.inspectorMode) this.transitionToInspectPreset('deskView');
            },
        });
        // «Avisar a Iris»: the shell already showed what goes and from which app; Core frames it as
        // the app's information and answers each message. Once Core took it, the chat shows it as
        // the person's message, naming the app.
        this.askIris = askIrisSender({
            send: (message) => this.ws?.connected === true && this.ws.send('app_message', message) === true,
            onAccepted: (appId, appName, request) => {
                this.panel?.addMessage?.('user', `${appName}: ${request.text}`);
                this.activityCanvas?.handleUserMessage?.(request.text);
            },
        });
        this.activityCanvas.onAskIris = (appId, appName, request) => this.askIris.send(appId, appName, request);
        document.getElementById('btn-iris-os')?.addEventListener('click', () => {
            this.activityCanvas?.open?.();
        });
        // F17: after Iris Store reloads the page to add or remove an app, return to the desktop.
        this.activityCanvas?.resumeAfterReload?.();
        window.addEventListener('IRIS:cron-activity', (ev) => {
            this.activityCanvas?.pushEvent?.(ev.detail || {});
        });
        window.addEventListener('IRIS:hermes-activity', (ev) => {
            this.activityCanvas?.pushEvent?.(ev.detail || {});
        });
        window.addEventListener('IRIS:cron-delivery', (ev) => {
            const delivery = ev.detail || {};
            const name = delivery.name || 'Tarea programada';
            const text = delivery.text || '';
            this.activityCanvas?.pushEvent?.({
                state: 'job',
                label: 'Cron finalizado',
                detail: name,
                tool: 'Hermes cron',
            });
            if (text) {
                this._speakCronDelivery(delivery);
            }
        });

        // 10b. Ambient Audio
        this.ambientAudio = new AmbientAudio();
        await this.ambientAudio.init();
        this.panel.ambientAudio = this.ambientAudio;  // wire for toggle/volume
        if (this.room) {
            // Each room has its own sound; it follows every way of changing the room.
            this.ambientAudio.setRoomTheme(this.room.currentTheme);
            this.room.themeListeners.add((name) => this.ambientAudio?.setRoomTheme(name));
        }

        // 11. WebSocket
        this.ws = new WebSocketManager();
        // Expose for panel.js (WebRTC Host / Remote buttons read it).
        // Without this, `new HostClient(window._irisWs, …)` and
        // `new RemoteClient(window._irisWs)` blow up with TypeError.
        window._irisWs = this.ws;
        this.ws.onMessage = (type, payload) => this._handleMessage(type, payload);
        this.ws.onConnectionChange = (connected) => {
            this.manualVoice?.setConnected(connected);
            if (connected) void this.animationLibrary?.refresh();
            if (!connected) {
                this.animationDirector?.resetActivity();
                this.roomRoutines?.connectionLost();
            }
            this.panel?.setRuntimeConnection?.(connected);
            if (!connected) {
                this.speechPresentation.reset();
                this.subtitles?.hide();
                this._screenSnapshots?.cancel();
                this.calendarNotice.close();
                this._setWakeUiState({ active: this.hermesWakeActive, state: 'disconnected' });
                this._resolveCameraLease(false);
                if (this.panel?.camActive || this.panel?.camStarting) {
                    this.panel?._stopCamera?.({ releaseLease: false });
                }
            }
        };
        this.panel.requestCameraLease = (confirmed = false) => this._requestCameraLease({ confirmed });
        this.panel.releaseCameraLease = () => {
            this.ws?.send('camera_lease_release', {});
        };
        // F03b: one photo when Core asks for it (the user asked Iris to look).
        // The user's click (or a remembered destination) is the confirmation.
        this._snapshotPrompt = new SnapshotPrompt();
        this._snapshots = new SnapshotRequests({
            send: (result) => this.ws?.send('vision_snapshot_result', result),
            acquire: (consumer) => this.panel?.acquireCamera?.(consumer, { confirmed: true }) ?? Promise.resolve(null),
            release: (consumer) => this.panel?.releaseCamera?.(consumer),
            isCameraActive: () => Boolean(this.panel?.camActive),
            cameraError: () => this.panel?.cameraError || '',
            prompt: this._snapshotPrompt,
        });
        this._snapshotPrompt.onCancel = (requestId) => this._snapshots.cancel(requestId, 'user');
        this._screenSnapshots = new ScreenSnapshotRequests({
            send: result => this.ws?.send('screen_snapshot_result', result),
        });
        this.ws.connect();
        this._bindSurfaceProtocol();

        // 12. Drag-and-drop VRM loading
        this._setupDragAndDrop();

        // 13. Window resize (debounced — mobile virtual keyboards fire
        // dozens of resize events as they animate in/out, and doing a
        // full layout recompute on every one tanks scroll perf).
        this._resizeDebounceId = null;
        window.addEventListener('resize', () => {
            if (this._resizeDebounceId) clearTimeout(this._resizeDebounceId);
            this._resizeDebounceId = setTimeout(() => {
                this._resizeDebounceId = null;
                this._onResize();
            }, 150);
        });

        // 14. Quick identity change from side panel. Its avatar actions reserve
        // their place first, in the same order as drag and drop (Codex qa10, C2).
        window.addEventListener('IRIS:avatar-reserve', e => this._reserveAvatarSelection(e));
        window.addEventListener('IRIS:identity-change', e => this._handleIdentityChange(e));
        // A surface outside IRIS OS (the chat's model selector) opens one of its apps.
        window.addEventListener('IRIS:open-os-app', (event) => {
            const app = String(event.detail?.app || '').trim();
            if (app) this.activityCanvas?.openCommandSurface?.(app);
        });

        // 15. Inspector mode button
        document.getElementById('btn-inspector')?.addEventListener('click', () => {
            this._toggleInspectorMode();
        });
        document.getElementById('inspect-top-toggle')?.addEventListener('click', () => {
            this._toggleInspectorMode();
        });
        this._setupInspectPresetButtons();
        this._setupRoomHotspotButtons();

        // 16. Room selector HUD
        document.querySelectorAll('.room-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const roomName = btn.dataset.room;
                document.querySelectorAll('.room-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                if (this.room) {
                    this.config.room_theme = roomName;
                    this.room.setTheme(roomName);
                    // Sync the panel theme select too
                    const sel = document.getElementById('theme-select');
                    if (sel) sel.value = roomName;
                }
            });
        });

        // One quick microphone button. Wake remains in settings.
        this.manualVoice = new ManualVoiceController({
            button: document.getElementById('btn-hermes-voice'),
            notice: (text) => this.subtitles?.show(text, 5000),
            onState: (state) => {
                if (this.ws?.connected) this.animationDirector?.handleRuntimeEvent('hermes_voice_state', state);
                if (!this.activityCanvas) return;
                this.activityCanvas._manualVoiceRuntime = state;
                if (this.activityCanvas._open) this.activityCanvas._renderDashboard();
            },
        });
        this.manualVoice.setConnected(this.ws?.connected);
        window.addEventListener('IRIS:hermes-wake-toggle', () => this._toggleHermesWake());
        window.addEventListener('IRIS:hermes-wake-runtime-sync', (ev) => {
            this._setWakeUiState(ev.detail || {});
        });

        // Camera preview and local analysis have independent lifetimes.
        window.addEventListener('IRIS:camera-started', async (ev) => {
            const videoEl = ev.detail?.videoEl;
            if (!videoEl) return;
            const generation = ++this._cameraAnalysisGeneration;
            this.activityCanvas?.setSharedCameraStream?.(ev.detail?.stream || this.panel?.camStream || null);
            this._visionAnalyzerState = 'loading';
            this._renderVisionPrivacyIndicator();
            try {
                if (!this.mocap) this.mocap = new CameraMocap();
                await this.mocap.start(videoEl);
                if (generation !== this._cameraAnalysisGeneration || !this.panel?.camActive) return;
                this._visionAnalyzerState = this.mocap.running ? 'ready' : 'off';
                this._renderVisionPrivacyIndicator();
            } catch (err) {
                if (generation !== this._cameraAnalysisGeneration || !this.panel?.camActive) return;
                this._visionAnalyzerState = 'error';
                this._renderVisionPrivacyIndicator();
                console.warn('[vision] local analysis unavailable:', err);
                this.subtitles?.show('La cámara sigue activa; no se pudo preparar el análisis local.', 3500);
            }
        });
        window.addEventListener('IRIS:camera-stopped', () => {
            this._cameraAnalysisGeneration += 1;
            this.mocap?.stop();
            this._visionAnalyzerState = 'off';
            this.activityCanvas?.setSharedCameraStream?.(null);
            this._renderVisionPrivacyIndicator();
        });

        this.sceneActions?.prepareTeleport(this.renderer, this.camera);

        // 17. Hide loading screen
        setTimeout(() => this.loadingScreen.classList.add('hidden'), 1200);

        // 18. Start render loop
        this._animate();

        // 19. Expose to window for pose-menu and debug access
        window.iris = this;
        window.IRIS = this;

        console.log('IRIS face layer initialized');
    }

    _normalizeUiText() {
        normalizeRoomText(document, this.config.iris_version || '');
    }

    // ── Inspector Mode — Free orbit, no room ─────────────────

    _setupInspectPresetButtons() {
        this._roomAtmosphereControls?.dispose();
        this._roomAtmosphereControls = bindRoomAtmosphereControls(document.getElementById('inspect-room-atmosphere-controls'), () => this.room?.atmosphere);
        const tray = document.getElementById('inspect-camera-presets');
        if (tray) tray.querySelectorAll('[data-camera-preset]').forEach((btn) => {
            btn.addEventListener('click', () => {
                this.transitionToInspectPreset(btn.dataset.cameraPreset);
            });
        });
        document.getElementById('inspect-reset-camera')?.addEventListener('click', () => {
            this.transitionToInspectPreset(DEFAULT_INSPECT_PRESET);
        });
        document.getElementById('inspect-exit')?.addEventListener('click', () => {
            if (this.inspectorMode) this._toggleInspectorMode();
        });
        this._setInspectPresetHudVisible(false);
    }

    _enterWorkbenchFocusMode() {
        if (this.inspectorMode) {
            this._toggleInspectorMode();
        }
        this.panel?.close?.();
        this._roomSurfaceVisibility ??= new RoomSurfaceVisibility();
        this._roomSurfaceVisibility.hide(this.activityCanvas?.panel);
        document.body.classList.add('IRIS-workbench-focus');
    }

    _exitWorkbenchFocusMode() {
        document.body.classList.remove('IRIS-workbench-focus');
        this._roomSurfaceVisibility?.restore();
    }

    _setupRoomHotspotButtons() {
        document.querySelectorAll('[data-room-action]').forEach((btn) => {
            const roomAction = getRoomAction(btn.dataset.roomAction);
            if (roomAction?.enabled === false) {
                btn.classList.add('disabled-action');
                btn.setAttribute('aria-disabled', 'true');
                btn.title = `${roomAction.label || btn.textContent.trim()} aún no está disponible`;
            }
            btn.addEventListener('click', () => {
                const action = btn.dataset.roomAction;
                const roomAction = getRoomAction(action);
                if (roomAction?.enabled === false) {
                    this._setActiveInspectZone(btn.dataset.zone || 'overview');
                    return;
                }
                this.sceneActions?.execute?.(action);
                if (roomAction?.enabled !== false && roomAction?.expression && this.avatarManager) {
                    this._setEmotion(roomAction.expression);
                }
                if (this.inspectorMode && roomAction?.cameraPreset) {
                    this.transitionToInspectPreset(roomAction.cameraPreset);
                }
                this._setActiveRoomActionButton(action);
            });
        });
        this._setRoomHotspotHudVisible(false);
    }

    _setInspectPresetHudVisible(visible) {
        const hud = document.getElementById('inspect-room-hud');
        if (hud) {
            hud.hidden = !visible;
            hud.classList.toggle('active', visible);
        }
        document.getElementById('inspect-camera-presets')?.classList.toggle('active', visible);
        this._setRoomHotspotHudVisible(visible);
    }

    _setRoomHotspotHudVisible(visible) {
        document.getElementById('room-hotspot-actions')?.classList.toggle('active', visible);
    }

    _setActiveInspectPresetButton(name) {
        document.querySelectorAll('[data-camera-preset]').forEach((btn) => {
            btn.classList.toggle('active', btn.dataset.cameraPreset === name);
        });
        this.activeInspectPreset = name;
        this._setActiveInspectZone(INSPECT_ZONE_BY_PRESET[name] || 'overview', name);
    }

    _setActiveRoomActionButton(action) {
        const roomAction = getRoomAction(action);
        const actionId = roomAction?.id || action;
        document.querySelectorAll('[data-room-action]').forEach((btn) => {
            const btnAction = getRoomAction(btn.dataset.roomAction);
            btn.classList.toggle('active',
                btn.dataset.roomAction === action || btnAction?.id === actionId);
        });
        const zone = INSPECT_ZONE_BY_HOTSPOT[roomAction?.targetHotspot]
            || INSPECT_ZONE_BY_PRESET[roomAction?.cameraPreset]
            || document.querySelector(`[data-room-action="${action}"]`)?.dataset.zone
            || 'overview';
        this._setActiveInspectZone(zone);
    }

    _setActiveInspectZone(zone = 'overview', presetName = this.activeInspectPreset) {
        const normalized = zone || 'overview';
        document.querySelectorAll('[data-zone]').forEach((el) => {
            el.classList.toggle('zone-active', el.dataset.zone === normalized);
        });
        const label = document.getElementById('inspect-selected-zone');
        if (label) {
            const presetLabel = document
                .querySelector(`[data-camera-preset="${presetName}"]`)
                ?.textContent
                ?.trim();
            const zoneLabel = INSPECT_ZONE_LABELS[normalized] || INSPECT_ZONE_LABELS.overview;
            label.textContent = presetLabel ? `${zoneLabel} · ${presetLabel}` : zoneLabel;
        }
    }

    _ensureOrbitControls() {
        if (!this.orbitControls) {
            this.orbitControls = new OrbitControls(this.camera, this.renderer.domElement);
            this.orbitControls.enableDamping = true;
            this.orbitControls.dampingFactor = 0.06;
            this.orbitControls.minDistance = 0.8;
            this.orbitControls.maxDistance = 16;
            this.orbitControls.addEventListener('start', () => { this._framedRoomPreset = null; });
        }
        return this.orbitControls;
    }

    transitionToInspectPreset(name, opts = {}) {
        const sourcePreset = INSPECT_CAMERA_PRESETS[name];
        if (!sourcePreset || !this.camera) return false;
        const preset = ROOM_DETAIL_PRESETS[name]
            ? frameRoomPreset(sourcePreset, this.camera.aspect) : sourcePreset;
        this._framedRoomPreset = ROOM_DETAIL_PRESETS[name] ? name : null;

        const controls = this._ensureOrbitControls();
        const target = new THREE.Vector3(...preset.target);
        const position = new THREE.Vector3(...preset.position);
        controls.maxDistance = Math.max(16, position.distanceTo(target) * 1.05);
        const duration = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
            ? 0 : (opts.duration ?? preset.duration ?? 700);

        this.activeInspectPreset = name;
        this._setActiveInspectPresetButton(name);

        if (opts.immediate || duration <= 0) {
            this.camera.position.copy(position);
            this.camera.fov = preset.fov ?? this.camera.fov;
            this.camera.updateProjectionMatrix();
            controls.target.copy(target);
            this.camera.lookAt(target);
            controls.enabled = !!this.inspectorMode;
            controls.update();
            this.inspectPresetTransition = null;
            return true;
        }

        this.inspectPresetTransition = {
            startedAt: performance.now(),
            duration,
            fromPosition: this.camera.position.clone(),
            toPosition: position,
            fromTarget: controls.target.clone(),
            toTarget: target,
            fromFov: this.camera.fov,
            toFov: preset.fov ?? this.camera.fov,
        };
        controls.enabled = false;
        return true;
    }

    _updateInspectCameraTransition() {
        const transition = this.inspectPresetTransition;
        if (!transition || !this.camera) return false;

        const controls = this._ensureOrbitControls();
        const raw = (performance.now() - transition.startedAt) / transition.duration;
        const t = THREE.MathUtils.clamp(raw, 0, 1);
        const eased = t * t * (3 - 2 * t);

        this.camera.position.lerpVectors(
            transition.fromPosition,
            transition.toPosition,
            eased,
        );
        controls.target.lerpVectors(
            transition.fromTarget,
            transition.toTarget,
            eased,
        );
        this.camera.fov = THREE.MathUtils.lerp(transition.fromFov, transition.toFov, eased);
        this.camera.updateProjectionMatrix();
        this.camera.lookAt(controls.target);
        controls.update();

        if (t >= 1) {
            this.camera.position.copy(transition.toPosition);
            controls.target.copy(transition.toTarget);
            this.camera.fov = transition.toFov;
            this.camera.updateProjectionMatrix();
            this.camera.lookAt(controls.target);
            controls.enabled = !!this.inspectorMode;
            controls.update();
            this.inspectPresetTransition = null;
        }

        return true;
    }

    _toggleInspectorMode() {
        this.inspectorMode = !this.inspectorMode;
        this.avatarZoom?.setExploring(this.inspectorMode);
        // The rim-light panel only works from the conversation camera; seen
        // from Explore angles it floats as two translucent columns.
        const rimPanel = this.room?.getObject?.('avatar_soft_rim_panel');
        if (rimPanel) rimPanel.visible = !this.inspectorMode;
        const btn = document.getElementById('btn-inspector');

        if (this.inspectorMode) {
            this.activityCanvas?.close?.();
            this.panel?.close?.();
            // Phase 1 room redesign: keep the room visible in inspector
            // so the user can orbit the complete apartment shell, not just
            // the avatar against an empty background.
            if (this.room) this.room.setVisible(true);

            // Enable OrbitControls
            this._ensureOrbitControls();
            this.orbitControls.enabled = true;
            this._setInspectPresetHudVisible(true);
            this.transitionToInspectPreset(DEFAULT_INSPECT_PRESET);

            // Inspector wants to see details → boost exposure temporarily.
            // We stash the room-mode value so toggling back restores it.
            if (this.renderer) {
                this._savedExposure = this.renderer.toneMappingExposure;
                this.renderer.toneMappingExposure = 1.05;
            }

            btn?.classList.add('active-mode');
            document.getElementById('inspect-top-toggle')?.classList.add('active-mode');
            this.subtitles?.show('Modo Inspector activo - arrastra para orbitar', 2500);
            console.log('Inspector mode ON');

        } else {
            // Restore room
            if (this.room) this.room.setVisible(true);

            // Disable orbit
            if (this.orbitControls) this.orbitControls.enabled = false;
            this.inspectPresetTransition = null;
            this._setInspectPresetHudVisible(false);
            this._setActiveInspectPresetButton(null);
            this._setActiveRoomActionButton(null);
            this.activeInspectPreset = null;
            this._framedRoomPreset = null;

            // Restore default camera — closer than before so the avatar
            // takes up a larger portion of the frame (was 0,1.5,6).
            this.camera.position.set(0.34, 1.38, 4.95);
            this.camera.lookAt(0.08, 0.66, 0);
            this.camera.fov = 45;
            this.camera.updateProjectionMatrix();

            // Restore room-mode exposure
            if (this.renderer && typeof this._savedExposure === 'number') {
                this.renderer.toneMappingExposure = this._savedExposure;
            }

            // Cancel the trip/timer before rebasing the pose. A bare root snap
            // was overwritten on the next frame by movement or seated returns.
            this.sceneActions?.reset({ clearLocomotion: true });
            this.idle?.resetRoomPlacement({ paused: this.speechAnimator?._talking === true });
            this.conversationCamera?.reset();

            btn?.classList.remove('active-mode');
            document.getElementById('inspect-top-toggle')?.classList.remove('active-mode');
            this.subtitles?.show('Volviendo a la habitacion...', 1500);
            console.log('Inspector mode OFF');
        }
    }

    // ── Native Hermes wake-word toggle ──────────────────────

    async _handleCommandAction({ command = '', args = '', item = {} } = {}) {
        const action = item.action || command;
        const cleanArgs = String(args || '').trim().toLowerCase();
        if (action === 'palette') {
            this.panel?.openCommandPalette?.();
            return;
        }
        if (action === 'chat') {
            openChatSurface(this);
            return;
        }
        if (action === 'reasoning') {
            if (!cleanArgs) {
                this.panel?.addMessage?.('system', `Razonamiento actual: ${this.panel.getReasoningEffort()}. Usa /reasoning none|low|medium|high.`);
                return;
            }
            const effort = cleanArgs.split(/\s+/, 1)[0];
            if (!['none', 'low', 'medium', 'high'].includes(effort) || cleanArgs.includes('--global')) {
                this.panel?.addMessage?.('system', 'IRIS OS admite /reasoning none, low, medium o high. Los niveles exclusivos de Hermes CLI están marcados como no integrados.');
                return;
            }
            this.panel.setReasoningEffort(effort);
            this.panel?.addMessage?.('system', `Razonamiento cambiado a ${effort}.`);
            return;
        }
        if (action === 'model') {
            // F01: the selector shows Hermes' inventory and confirms the
            // model per turn; a typed model name cannot name its provider.
            this.activityCanvas?.close?.();
            this.panel?.toggle?.(true);
            this.panel?.modelSelector?.open?.();
            if (cleanArgs) {
                this.panel?.addMessage?.('system', 'Elige el modelo en el selector: así IRIS sabe de qué proveedor es y el motor confirma cuál respondió.');
            }
            return;
        }
        if (action === 'wake') {
            if (!cleanArgs || cleanArgs === 'status') {
                try {
                    const response = await fetch('/api/hermes-wake/settings?probe=false');
                    const snapshot = await response.json();
                    if (!response.ok) throw new Error(snapshot.detail || `HTTP ${response.status}`);
                    const active = !!snapshot.runtime?.active;
                    const phrase = snapshot.runtime?.phrase || snapshot.phrase || 'hey hermes';
                    this.panel?.addMessage?.('system', active ? `Escucha activa. Di “${phrase}”.` : `Escucha apagada. Frase configurada: “${phrase}”.`);
                } catch (error) {
                    this.panel?.addMessage?.('system', `No pude consultar la escucha: ${error?.message || error}`);
                }
                return;
            }
            if (!['on', 'off'].includes(cleanArgs)) {
                this.panel?.addMessage?.('system', 'Usa /wake on, /wake off o /wake status.');
                return;
            }
            const target = cleanArgs === 'on';
            await this._toggleHermesWake(target);
            return;
        }
        this.activityCanvas?.openCommandSurface?.(action);
    }

    async _toggleHermesWake(targetActive = !this.hermesWakeActive) {
        if (this._wakeTogglePromise) return this._wakeTogglePromise;
        const transition = this._performHermesWakeToggle(Boolean(targetActive));
        this._wakeTogglePromise = transition;
        try {
            return await transition;
        } finally {
            if (this._wakeTogglePromise === transition) this._wakeTogglePromise = null;
        }
    }

    async _performHermesWakeToggle(targetActive) {
        const previousActive = this.hermesWakeActive;
        if (targetActive) {
            this._setWakeUiState({ active: false, state: 'arming' });
            this.subtitles?.show('Preparando la escucha de IRIS…', 2600);
        }
        try {
            const response = await fetch('/api/hermes-wake/runtime', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ active: targetActive }),
            });
            const state = await response.json();
            if (!response.ok) {
                throw new Error(state.detail || state.error || state.hint || state.reason || 'No se pudo cambiar la escucha de IRIS.');
            }
            this._setWakeUiState(state);
            window.dispatchEvent(new CustomEvent('IRIS:hermes-wake-settings-changed', {
                detail: { enabled: targetActive, phrase: state.phrase, provider: state.provider },
            }));
            this.subtitles?.show(
                targetActive ? `Escucha activa · di “${state.phrase || 'hey hermes'}”` : 'Escucha de IRIS desactivada',
                2600,
            );
        } catch (error) {
            const message = error?.message || 'No pude controlar la escucha de IRIS.';
            let reconciled = {
                active: previousActive,
                state: previousActive ? 'listening' : 'error',
            };
            try {
                // A failed OFF request must never paint the ear as disabled
                // while Hermes may still own the microphone. Query runtime
                // truth without the slower dependency probe before rendering.
                const statusResponse = await fetch('/api/hermes-wake/settings?probe=false');
                const snapshot = await statusResponse.json();
                if (statusResponse.ok && snapshot.runtime) {
                    reconciled = { ...reconciled, ...snapshot.runtime };
                    window.dispatchEvent(new CustomEvent('IRIS:hermes-wake-settings-changed', {
                        detail: snapshot,
                    }));
                }
            } catch {
                // Preserve the last confirmed state when reconciliation is
                // unavailable; never assume that a microphone was released.
            }
            this._setWakeUiState({
                ...reconciled,
                state: reconciled.active ? (reconciled.state || 'listening') : 'error',
                error: message,
            });
            this.subtitles?.show(message, 4200);
        }
    }

    _setWakeUiState(payload = {}) {
        const active = !!payload.active;
        this.hermesWakeActive = active;
        window.dispatchEvent(new CustomEvent('IRIS:hermes-wake-state', { detail: payload }));
    }

    async _loadVisionAssistantState() {
        const version = this.activityCanvas?._visionStateVersion || 0;
        try {
            const response = await fetch('/api/vision-assistant/settings', { cache: 'no-store' });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const state = await response.json();
            if (version !== (this.activityCanvas?._visionStateVersion || 0)) return;
            this.activityCanvas?.applyVisionAssistantState?.(state);
        } catch (err) {
            console.warn('[vision] could not load local settings:', err);
        }
    }

    async _disableVisionAssistantFromKillSwitch() {
        const disabled = {
            ...this.visionAssistant,
            enabled: false,
            paused: false,
            paused_until: 0,
            state: 'off',
        };
        this.activityCanvas?.applyVisionAssistantState?.(disabled);
        try {
            const response = await fetch('/api/vision-assistant/settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ enabled: false, pause_minutes: 0 }),
            });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            this.activityCanvas?.applyVisionAssistantState?.(await response.json());
        } catch (err) {
            console.warn('[vision] kill switch could not persist settings:', err);
            this.subtitles?.show('Cámara cerrada; no pude guardar el apagado de Vision Assistant.', 3200);
        }
    }

    _applyVisionAssistantState(payload = {}) {
        const next = { ...(payload || {}) };
        const confirmed = next.camera_confirmation_granted === true;
        delete next.camera_confirmation_granted;
        this.visionAssistant = { ...this.visionAssistant, ...next };
        this._scheduleVisionAssistantResume();
        this._renderVisionPrivacyIndicator();
        this._syncVisionAssistantCamera({ confirmed });
    }

    _scheduleVisionAssistantResume() {
        if (this._visionResumeTimer) clearTimeout(this._visionResumeTimer);
        this._visionResumeTimer = null;
        const pausedUntil = Number(this.visionAssistant.paused_until || 0);
        const delay = pausedUntil * 1000 - Date.now();
        if (!this.visionAssistant.enabled || !Number.isFinite(delay) || delay <= 0) return;
        this._visionResumeTimer = setTimeout(() => {
            this._visionResumeTimer = null;
            const resumed = {
                ...this.visionAssistant,
                paused: false,
                paused_until: 0,
                state: 'waiting_camera',
            };
            // Update both surfaces immediately, then reconcile with Core. The
            // persisted timestamp has already expired, so no write is needed.
            this.activityCanvas?.applyVisionAssistantState?.(resumed);
            this._loadVisionAssistantState();
        }, delay + 100);
    }

    async _syncVisionAssistantCamera({ confirmed = false } = {}) {
        if (!this.panel) return;
        const paused = Number(this.visionAssistant.paused_until || 0) > Date.now() / 1000;
        if (!this.visionAssistant.enabled || paused || document.visibilityState === 'hidden') {
            this._visionCameraRequestToken += 1;
            this.panel.releaseCamera?.('vision-assistant');
            return;
        }
        const lease = this.visionAssistant.camera || {};
        const cameraVerdict = this.visionAssistant.permissions?.camera?.verdict;
        if (!confirmed && cameraVerdict && cameraVerdict !== 'allow') return;
        if (!confirmed && lease.active && lease.surface_id && lease.surface_id !== this.surfaceId) {
            return;
        }
        if (this._visionCameraPending) return;
        const requestToken = ++this._visionCameraRequestToken;
        this._visionCameraPending = true;
        try {
            await this.panel.acquireCamera?.('vision-assistant', { confirmed });
        } finally {
            this._visionCameraPending = false;
            const becamePaused = Number(this.visionAssistant.paused_until || 0) > Date.now() / 1000;
            const requestIsStale = requestToken !== this._visionCameraRequestToken;
            if (
                requestIsStale
                || !this.visionAssistant.enabled
                || becamePaused
                || document.visibilityState === 'hidden'
            ) {
                this.panel.releaseCamera?.('vision-assistant');
            }
            this._renderVisionPrivacyIndicator();
            // A pause may have expired while an older permission request was
            // settling. Re-evaluate once so the current state always wins.
            if (requestIsStale && this.visionAssistant.enabled && !becamePaused) {
                this._syncVisionAssistantCamera();
            }
        }
    }

    _renderVisionPrivacyIndicator() {
        const indicator = document.getElementById('vision-privacy-indicator');
        if (!indicator) return;
        const cameraActive = !!this.panel?.camActive;
        const paused = !!this.visionAssistant.enabled
            && Number(this.visionAssistant.paused_until || 0) > Date.now() / 1000;
        const analysisActive = !!this.visionAssistant.enabled && !paused && cameraActive;
        const state = analysisActive && this._visionAnalyzerState === 'ready'
            ? 'observing'
            : analysisActive && this._visionAnalyzerState === 'loading'
                ? 'loading'
                : analysisActive && this._visionAnalyzerState === 'error'
                    ? 'error'
                    : paused
                        ? 'paused'
                        : cameraActive
                            ? 'camera'
                            : 'waiting';
        indicator.hidden = !this.visionAssistant.enabled && !cameraActive;
        indicator.dataset.state = state;
        const label = indicator.querySelector('.vision-privacy-state');
        if (label) {
            const labels = {
                observing: 'ANALIZANDO',
                loading: 'CARGANDO',
                error: 'ERROR LOCAL',
                paused: 'PAUSADO',
                camera: 'CÁMARA',
                waiting: 'EN ESPERA',
            };
            label.textContent = labels[state] || 'EN ESPERA';
        }
    }


    async _fetchConfig() {
        const renderRevision = this._renderRevision || 0;
        try {
            const res = await fetch('/api/config');
            if (res.ok) {
                const data = await res.json();
                if (renderRevision !== (this._renderRevision || 0)) {
                    delete data.render_fps_limit;
                    delete data.render_quality;
                }
                this.config = { ...this.config, ...data };
                this._frameLimiter.setLimit(this.config.render_fps_limit);
                this.graphicsQuality?.setQuality(this.config.render_quality);
                
                // Prevent browser CORS/Local Resource errors:
                // Convert absolute OS paths (C:\...\my_model.glb) into the /avatars/ HTTP route
                if (this.config.avatar_vrm_path
                    && !this.config.avatar_vrm_path.startsWith('http')
                    && !this.config.avatar_vrm_path.startsWith('/')) {
                    const filename = this.config.avatar_vrm_path.split(/[/\\]/).pop();
                    if (filename) {
                        this.config.avatar_vrm_path = `/avatars/${filename}`;
                    }
                }
            }
        } catch {
            // Backend not running – use defaults
        }
    }


    // ────────────────────────────────────────────────────────
    //  Three.js setup
    // ────────────────────────────────────────────────────────

    _setupScene() {
        this.scene = new THREE.Scene();
        this.scene.fog = new THREE.FogExp2(0x08111d, 0.011);
    }

    _setupRenderer() {
        this.renderer = new THREE.WebGLRenderer({
            canvas: this.canvas,
            antialias: false, // The offscreen targets provide quality-dependent MSAA.
            alpha: false,
        });
        this.renderer.setSize(window.innerWidth, window.innerHeight);
        this.renderer.setPixelRatio(1); // GraphicsQuality applies the saved resolution after setup.
        this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
        // Was 1.3 → 0.95 → 0.7. VRM MToon shaders already boost their own
        // brightness; exposure above ~0.75 produces the "glowing avatar"
        // look that washes out face details.
        this.renderer.toneMappingExposure = 0.78;
        this.renderer.setClearColor(0x07070d, 1);
        this.renderer.outputColorSpace = THREE.SRGBColorSpace;
        this.renderer.shadowMap.enabled = false;
        this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
        this.renderer.localClippingEnabled = true;   // her jump, and only then: see teleport_effect.js
    }

    _setupCamera() {
        this.camera = new THREE.PerspectiveCamera(
            45, window.innerWidth / window.innerHeight, 0.1, 1000,
        );
        // Closer than the old (0, 1.5, 6) — the avatar is the subject,
        // the room is context. Camera sits at head height, 4.2u back.
        this.camera.position.set(0.34, 1.38, 4.95);
        this.camera.lookAt(0.08, 0.66, 0);
        this.avatarZoom.attachCamera(this.camera);
        this.conversationCamera = new ConversationCamera(this.camera);
    }

    _setupLights() {
        // Second pass of reduction — MToon already emits so much fill that
        // most external lights just wash out features. Keep the rig as a
        // tonal tint, not as the primary illumination.
        // Ambient
        this.scene.add(new THREE.AmbientLight(0xcfe6ff, 0.2));

        // Hemisphere
        const hemi = new THREE.HemisphereLight(0xcfefff, 0x2a1430, 0.24);
        this.scene.add(hemi);

        // Key — Main frontal light
        const key = new THREE.DirectionalLight(0xf2f8ff, 0.28);
        key.position.set(1.8, 3.2, 5);
        this.scene.add(key);

        // Fill — Subtle side fill
        const fill = new THREE.DirectionalLight(0x9fe4ff, 0.14);
        fill.position.set(-3.2, 1.15, 2.2);
        this.scene.add(fill);

        // Rim — Golden backlight for silhouette
        const rim = new THREE.PointLight(0xf36bce, 0.46, 12);
        rim.position.set(-0.5, 1.85, -3.2);
        this.scene.add(rim);

        // Subtle eye catch-lights
        for (const x of [-0.2, 0.2]) {
            const el = new THREE.PointLight(0xffffff, 0.14, 3);
            el.position.set(x, 0.5, 1.5);
            this.scene.add(el);
        }
    }

    _setupPostProcessing() {
        // Habilitar MSAA Nativo para evitar bordes pixelados (jagged edges)
        const renderTarget = new THREE.WebGLRenderTarget(
            window.innerWidth,
            window.innerHeight,
            // HalfFloat evita banding color banding. The stencil buffer lets
            // room.js draw the distant city only through the window opening.
            // The window uses stencil during scene rendering. Postprocessing
            // samples colour only; copying stencil is costly on ANGLE/D3D11.
            { samples: 2, type: THREE.HalfFloatType, stencilBuffer: true, resolveStencilBuffer: false }
        );

        this.composer = new EffectComposer(this.renderer, renderTarget);
        this.composer.addPass(new RenderPass(this.scene, this.camera));
        this._bloomPass = new UnrealBloomPass(
            new THREE.Vector2(window.innerWidth, window.innerHeight),
            0.3, 0.28, 0.9,
        );
        guardBloom(this._bloomPass);
        this.composer.addPass(this._bloomPass);
        // Keep the scene in the stencil-capable target even when bloom is off.
        // The scene targets hold linear colour: the output pass applies the
        // ACES exposure and sRGB encoding that bloom used to apply on screen.
        // A plain copy skipped both and left the room at about half brightness.
        this.composer.addPass(new OutputPass());
        this.graphicsQuality.setQuality(this.config.render_quality);
    }

    // ────────────────────────────────────────────────────────
    //  Drag-and-drop VRM
    // ────────────────────────────────────────────────────────

    _setupDragAndDrop() {
        // Avatar formats: only those that ALREADY carry a humanoid
        // skeleton when dropped. OBJ / FBX / STL are excluded on
        // purpose — the auto-rigger pipeline is fragile for arbitrary
        // meshes and the recommended source (VRoid Hub) only exports
        // VRM anyway. If the user needs to import an FBX rig they go
        // through the wizard's avatar step which has the proper
        // rig-model flow.
        const SUPPORTED = new Set(['vrm', 'glb', 'gltf']);
        // Image drag-drop belongs to the chat composer. The page-level
        // handler remains reserved for avatar files (.vrm/.glb/.gltf).
        const IMAGE_EXTS = new Set(['jpg', 'jpeg', 'png', 'webp', 'gif']);

        const body = document.body;
        // Skip the full-page drop overlay when the cursor is inside the
        // chat composer — that area has its OWN drop target (panel.js)
        // and showing both highlights at once is confusing. We check
        // by walking up from the actual event target.
        const isOverComposer = (e) => {
            const t = e.target;
            return t && typeof t.closest === 'function' && t.closest('#chat-composer');
        };
        body.addEventListener('dragover', (e) => {
            if (isOverComposer(e)) { body.classList.remove('drag-hover'); return; }
            e.preventDefault();
            body.classList.add('drag-hover');
        });
        body.addEventListener('dragleave', () => body.classList.remove('drag-hover'));
        body.addEventListener('drop', async (e) => {
            // Composer handler called stopPropagation, but defend in
            // depth — if the drop already attached files to the chat,
            // don't ALSO route the file to the avatar / one-shot image
            // pipelines below.
            if (isOverComposer(e)) { body.classList.remove('drag-hover'); return; }
            e.preventDefault();
            body.classList.remove('drag-hover');

            const file = e.dataTransfer?.files?.[0];
            if (!file) return;

            const ext = file.name.split('.').pop().toLowerCase();

            if (IMAGE_EXTS.has(ext)) {
                this.subtitles?.show(
                    `Suelta la imagen en el chat para que IRIS la analice con el modelo de visión.`,
                    5500,
                );
                return;
            }

            if (!SUPPORTED.has(ext)) {
                this.subtitles?.show(
                    `Formato .${ext} no soportado. Usa .vrm (recomendado) o .glb/.gltf con skeleton VRM.`,
                    5000,
                );
                return;
            }

            console.log(`Loading dropped avatar: ${file.name} (ext: ${ext})`);
            this.subtitles?.show(`Cargando "${file.name}"...`, 3000);

            // Load the avatar and rewire systems that hold a reference
            // to the previous one. The animation manifest is re-loaded
            // explicitly afterwards — without that, the fresh IdleSystem
            // starts with `_vrmaPlayer = null` and the LLM's
            // `animacion_virtual` calls all fall through to the
            // procedural head-only fallback, which is why drag-dropping
            // an avatar used to feel "alive but unresponsive".
            // Reserve the selection before upload; a slower earlier response
            // must not supersede a later panel selection or drop.
            const isCurrent = this._avatarSwap.begin();
            let url = '';
            let persisted = false;
            if (ext === 'vrm' || ext === 'glb') {
                try {
                    const form = new FormData();
                    form.append('file', file, file.name);
                    const response = await fetch('/api/avatar/upload', {
                        method: 'POST',
                        body: form,
                    });
                    const data = await response.json().catch(() => ({}));
                    if (!response.ok || data.status !== 'saved' || !data.avatar_url) {
                        throw new Error(data.detail || 'IRIS no pudo guardar el avatar.');
                    }
                    if (!isCurrent()) return;
                    url = data.avatar_url;
                    persisted = true;
                } catch (error) {
                    if (!isCurrent()) return;
                    isCurrent.finish?.();
                    console.warn('[avatar drop] persistent upload failed:', error);
                    this.subtitles?.show(error?.message || 'No pude guardar ese avatar.', 5500);
                    return;
                }
            } else {
                // A standalone .gltf can depend on external .bin/textures, so
                // it remains an explicit one-session preview.
                url = URL.createObjectURL(file);
                this.subtitles?.show('Vista previa GLTF: solo esta sesión.', 3500);
            }
            try {
                const changed = await this._replaceAvatar(url, {
                    isCurrent,
                    releaseUrl: persisted ? null : () => URL.revokeObjectURL(url),
                });
                if (!changed) return;
            } catch (err) {
                console.error('VRM load failed:', err);
                this.subtitles?.show(
                    `"${file.name}" no tiene skeleton VRM valido. ` +
                    `Exporta el archivo desde VRoid Studio o con el VRM Add-on de Blender.`,
                    6000,
                );
                this.panel?.addMessage(
                    'system',
                    `${file.name} no es un VRM humanoide. Prueba con un archivo .vrm de VRoid Hub.`,
                );
                return;
            }

            this.subtitles?.show(
                persisted ? `Avatar guardado: ${file.name}` : `Avatar listo para esta sesión: ${file.name}`,
                3500,
            );
            this.panel?.addMessage(
                'system',
                persisted ? `Avatar guardado: ${file.name}` : `Avatar temporal: ${file.name}`,
            );
        });
    }

    _reserveAvatarSelection(e) {
        if (e.detail && this._avatarSwap) e.detail.isCurrent = this._avatarSwap.begin();
    }

    _handleIdentityChange(e) {
        const { name, vrmUrl: newVrmUrl, isCurrent: reserved } = e.detail;
        if (name && this.subtitles) this.subtitles.show(`Hola, soy ${name}`, 2500);
        // A selection reserved before its requests (Quick Configuration) keeps
        // that place: a later drop or apply already won, and a late answer must
        // not start a new selection when it arrives (Codex qa10, C2).
        const reservation = typeof reserved === 'function' ? reserved : null;
        if (reservation && !reservation()) return;
        // A pending selection must also yield when the user reselects the visible model.
        const yieldsPending = reservation ? false : this._avatarSwap?.pending;
        // `vrmUrl` names the model to show; an empty one is IRIS'.
        const resource = typeof newVrmUrl === 'string' ? avatarUrl({}, IRIS_AVATAR_URL, newVrmUrl) : '';
        if (resource && (resource !== this.avatarManager?.vrmUrl || yieldsPending)) {
            const options = reservation ? { isCurrent: reservation } : {};
            return this._replaceAvatar(resource, options).catch(error => {
                console.warn('Avatar swap failed:', error);
                this.subtitles?.show(error.message || 'No pude cambiar el avatar.', 5000);
            });
        }
        // Already on screen: the reservation ends here (any pending one already yielded to it).
        reservation?.finish?.();
    }

    _replaceAvatar(url, options = {}) {
        return this._avatarSwap.replace(url, options);
    }

    async _speakCronDelivery(delivery = {}) {
        const name = delivery.name || 'Tarea programada';
        const text = delivery.text || '';
        if (!text) return;

        this.activityCanvas?.pushEvent?.({
            state: 'speaking',
            label: 'Cron en voz',
            detail: name,
            tool: 'IRIS TTS',
        });

        try {
            const res = await fetch('/api/cron/speak', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    name,
                    text,
                    emotion: delivery.emotion || 'neutral',
                    animation: delivery.animation || 'idle',
                }),
            });
            if (!res.ok) {
                const detail = await res.text().catch(() => '');
                throw new Error(detail || `HTTP ${res.status}`);
            }
        } catch (err) {
            console.warn('[cron] could not speak delivery:', err);
            this.panel?.addMessage?.('ai', `[Cron] ${name}\n${text}`, 'neutral');
            this.subtitles?.show?.(`Cron listo: ${name}`, 3000);
        }
    }

    // ────────────────────────────────────────────────────────
    //  Multi-surface protocol
    // ────────────────────────────────────────────────────────

    _bindSurfaceProtocol() {
        if (this._surfaceProtocolBound) return;
        this._surfaceProtocolBound = true;

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
                this._resolveCameraLease(false);
                if (this.panel?.camActive || this.panel?.camStarting) {
                    this.panel?._stopCamera?.({ releaseLease: true });
                }
            } else {
                this._syncVisionAssistantCamera();
            }
        });
        window.addEventListener('focus', () => sendState(false));
        window.addEventListener('blur', () => sendState(false));
        window.addEventListener('pointerdown', () => {
            // Pointer activity is used only to elect the TTS owner. Throttle
            // it so dragging a window or slider doesn't flood the socket.
            const now = performance.now();
            if (now - this._lastSurfaceActivityAt < 750) return;
            this._lastSurfaceActivityAt = now;
            this.ws?.send('surface_activity', {});
        }, { capture: true, passive: true });
    }

    _requestCameraLease({ confirmed = false } = {}) {
        if (!this.ws?.connected || document.visibilityState === 'hidden') {
            return Promise.resolve(false);
        }
        if (this._cameraLeasePromise) return this._cameraLeasePromise;
        let leaseResolve;
        const leasePromise = new Promise((resolve) => {
            leaseResolve = resolve;
        });
        this._cameraLeasePromise = leasePromise;
        this._cameraLeaseResolve = leaseResolve;
        this._cameraLeaseTimer = setTimeout(() => this._resolveCameraLease(false), 5000);
        const sent = this.ws.send('camera_lease_request', {
            surface_id: this.surfaceId,
            purpose: 'mocap_and_emotion',
            confirmed: Boolean(confirmed),
        });
        if (!sent) this._resolveCameraLease(false);
        return leasePromise;
    }

    _resolveCameraLease(granted) {
        if (!this._cameraLeaseResolve) return;
        clearTimeout(this._cameraLeaseTimer);
        const resolve = this._cameraLeaseResolve;
        this._cameraLeaseResolve = null;
        this._cameraLeaseTimer = null;
        this._cameraLeasePromise = null;
        const usable = Boolean(granted)
            && this.ws?.connected
            && document.visibilityState !== 'hidden';
        if (granted && !usable) this.ws?.send('camera_lease_release', {});
        resolve(usable);
    }

    // ────────────────────────────────────────────────────────
    //  WebSocket message handler
    // ────────────────────────────────────────────────────────

    _replaceChatHistory(payload = {}) {
        const messages = Array.isArray(payload.messages) ? payload.messages : [];
        // The backend broadcasts the switch to every surface while the
        // initiating Continuity window also publishes its local completion
        // event. Treat both paths as the same delivery so the chat and
        // subtitle are not needlessly replaced twice.
        const sessionId = String(payload.session_id || payload.sessionId || '').trim();
        const lastMessage = messages.at(-1) || {};
        const lastContent = String(lastMessage.content || '');
        const fingerprint = [
            sessionId,
            messages.length,
            String(lastMessage.role || ''),
            lastContent.length,
            lastContent.slice(0, 48),
            lastContent.slice(-48),
        ].join('|');
        if (fingerprint === this._continuityHistoryFingerprint) return;
        this._continuityHistoryFingerprint = fingerprint;
        this.panel?.replaceMessages?.(messages);
        this._chatHydrated = true;
        const title = String(payload.title || 'conversación de IRIS').trim();
        this.subtitles?.show(`Continuando: ${title}`, 2600);
    }

    _handleMessage(type, payload) {
        this.animationDirector?.handleRuntimeEvent(type, payload);
        switch (type) {
            case 'animation_catalog_changed':
                void this.animationLibrary?.refresh();
                break;
            case 'hello':
                this._updateStatus('connected');
                void this._fetchConfig();
                this._setWakeUiState({
                    active: !!payload.hermes_wake_active,
                    state: payload.hermes_wake_active ? 'listening' : 'off',
                });
                this.ws.send('surface_ready', buildSurfaceReadyPayload({
                    role: 'face',
                    surfaceId: this.surfaceId,
                    visible: document.visibilityState !== 'hidden',
                    focused: document.hasFocus?.() ?? true,
                    capabilities: {
                        renders_avatar: true,
                        plays_tts: true,
                        reports_tts_playback: true,
                        captures_camera: true,
                        accepts_chat: true,
                        controls_local_player: true,
                        presents_vision: true,
                        tunes_vision: true,
                        takes_snapshots: true,
                        takes_screen_snapshots: true,
                        presents_inbox: true,
                        speaks_inbox: true,
                        hosts_iris_apps: true,
                    },
                }));
                this._loadVisionAssistantState();
                this.panel?.modelSelector?.handleEvent('hello', payload);
                break;

            case 'surface_registered':
            case 'surface_state_ack':
                if (payload?.surface_id) this.surfaceId = payload.surface_id;
                // Transport acknowledgements are intentionally quiet. Future
                // Companion settings can expose ownership diagnostics.
                break;

            case 'camera_lease_result':
                if (this._cameraLeaseResolve) {
                    if (!payload.granted && payload.reason === 'permission_denied') {
                        this.subtitles?.show('La cámara está bloqueada en Capability Center.', 3200);
                    } else if (!payload.granted && payload.reason === 'permission_required') {
                        this.subtitles?.show('Confirma la cámara desde su botón o desde Capability Center.', 3200);
                    }
                    // F03: a policy refusal is "no permission", not a busy camera.
                    if (this.panel) this.panel.cameraLeaseDenied = !payload.granted && String(payload.reason || '').startsWith('permission_');
                    this._resolveCameraLease(Boolean(payload.granted));
                }
                break;

            case 'vision_snapshot_request':
                void this._snapshots?.handle(payload || {});
                break;

            case 'screen_snapshot_request':
                void this._screenSnapshots?.handle(payload || {});
                break;
            case 'screen_snapshot_cancel':
                this._screenSnapshots?.cancel(payload?.request_id);
                break;
            case 'visual_request_notice':
                // Iris looked through the camera or the screen to answer: always said in the chat.
                if (payload?.text) this.panel?.addMessage('system', String(payload.text));
                break;
            case 'background_tasks':
                this.panel?.showBackgroundTasks?.(payload || {});
                break;
            case 'background_task_notice':
                if (payload?.text) this.panel?.addMessage('system', String(payload.text));
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

            case 'vision_snapshot_cancel':
                this._snapshots?.cancel(payload?.request_id, 'core');
                break;

            case 'camera_lease_revoked':
                this._resolveCameraLease(false);
                if (this.panel?.camActive || this.panel?.camStarting) {
                    this.panel?._stopCamera?.({ releaseLease: false });
                    this.subtitles?.show(
                        payload.reason === 'capability-policy-changed'
                            ? 'La cámara se cerró por la política de Capability Center.'
                            : 'La cámara pasó a otra superficie de IRIS.',
                        3000,
                    );
                }
                break;

            case 'camera_lease_state':
                this.visionAssistant = {
                    ...this.visionAssistant,
                    camera: { ...(payload || {}) },
                };
                if (
                    this.panel?.camActive
                    && payload.active
                    && payload.surface_id !== this.surfaceId
                ) {
                    this.panel?._stopCamera?.({ releaseLease: false });
                } else if (!payload.active) {
                    this._syncVisionAssistantCamera();
                }
                this._renderVisionPrivacyIndicator();
                break;

            case 'vision_assistant_state':
                this.activityCanvas?.handleNotificationsChanged?.();
                this.activityCanvas?.applyVisionAssistantState?.(payload || {}, { notify: false });
                // The save endpoint broadcasts before returning its ephemeral
                // camera confirmation to the initiating surface. Ignore that
                // echo while its request is pending; the HTTP response will
                // apply the same state with the explicit confirmation.
                if (!this.activityCanvas?._visionSaving) {
                    this._applyVisionAssistantState(payload || {});
                }
                break;

            case 'vision_signal_ack':
                this.activityCanvas?._notificationApp?.observeVision?.(payload);
                if (payload?.accepted) {
                    this.activityCanvas?.applyVisionAssistantState?.({
                        ...this.visionAssistant,
                        state: 'observing',
                    });
                }
                break;

            case 'vision_assistant_notifications':
                if (payload?.experience) this.visionAssistant.experience = payload.experience;
                this.activityCanvas?.handleNotificationsChanged?.(payload);
                break;

            case 'vision_assistant_calibration':
                applyVisionCalibration(payload, { mocap: this.mocap,
                    surfaceId: this.surfaceId, vision: this.visionAssistant });
                break;

            case 'vision_assistant_event':
                this.activityCanvas?.pushVisionEvent?.(payload || {});
                break;

            case 'inbox_voice':
                this.inboxVoice?.present(payload);
                break;
            case 'inbox_voice_stop':
                this.inboxVoice?.stop();
                break;
            case 'inbox_voice_ack':
                break;
            case 'inbox_presentation':
                this.inboxNotice.present(payload, {
                    surfaceId: this.surfaceId, documentLike: document,
                    status: this.manualVoice?.state.busy ? 'listening' : this._visionStatus,
                    chatBusy: !!this.calendarNotice.current,
                });
                break;

            case 'calendar_reminder':
                void this.calendarNotice.present(payload, {
                    surfaceId: this.surfaceId, documentLike: document,
                    busy: () => !!this.inboxNotice.current || !!this.calendarNotice.current || this.manualVoice?.state.busy
                        || ['thinking', 'speaking', 'listening', 'tool'].includes(this._visionStatus),
                });
                break;

            case 'calendar_reminder_ack':
                break;

            case 'app_notice':
                // A silent card: Iris speaking does not hold it back, another card on screen does.
                void this.appNotice.present(payload, {
                    surfaceId: this.surfaceId, documentLike: document,
                    busy: () => !!this.inboxNotice.current || !!this.calendarNotice.current,
                });
                break;

            case 'app_notice_ack':
                break;

            case 'app_message_ack':
                this.askIris?.acknowledge(payload);
                break;

            case 'inbox_changed':
                this.activityCanvas?.handleInboxChanged?.(payload || {});
                break;

            case 'inbox_presentation_ack':
                break;

            case 'vision_assistant_presentation':
                void this.visionPresentation.present(payload, {
                    surfaceId: this.surfaceId, vision: this.visionAssistant,
                    status: this._visionStatus, documentLike: document,
                    reducedMotion: window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches,
                    idle: this.idle, director: this.animationDirector,
                });
                break;

            case 'vision_assistant_events_cleared':
                if (this.activityCanvas) {
                    this.activityCanvas._visionEvents = [];
                    this.activityCanvas._renderVisionAssistant?.();
                }
                break;

            case 'chat_history':
                // Backend replays the last ~30 turns when a new client
                // connects so a page reload doesn't wipe the chat. We
                // only render this once per page load — subsequent
                // reconnects (auto-retry on backend restart) shouldn't
                // duplicate. The panel's own messages array already
                // resets on construction, so a fresh page load is the
                // only legitimate moment to populate from history.
                if (this._chatHydrated) break;
                this._chatHydrated = true;
                for (const msg of (payload.messages || [])) {
                    // `role` arrives as 'user' / 'IRIS' from the
                    // backend; SidePanel.addMessage expects the same
                    // sender values, so it's a 1:1 forward.
                    this.panel?.addMessage(msg.role, msg.content, msg.emotion);
                }
                break;

            case 'chat_history_replace':
                // Continuity Center intentionally replaces the local visual
                // timeline after Core has switched the authenticated Hermes
                // transcript and the IRIS memory scope as one operation.
                this._replaceChatHistory(payload);
                this.panel?.modelSelector?.handleEvent(type, payload);
                break;

            case 'model_turn':
            case 'model_selection_changed':
                // F01: what Hermes confirmed for a turn, or a selection made
                // in another window; the selector re-reads Core's state.
                this.panel?.modelSelector?.handleEvent(type, payload);
                break;

            case 'speech_start':
                this.roomRoutines?.responseStarted();
                window.dispatchEvent(new CustomEvent('IRIS:speech-start', { detail: payload }));
                this.activityCanvas?.handleSpeechStart(payload);
                this.subtitles.show(payload.text);
                this.speechPresentation.start(payload);
                this.panel?.addMessage('IRIS', payload.text, payload.emotion);
                this.panel?.setBrainData(payload);

                // Contextual scene action
                if (payload.accion_escena && this.sceneActions) {
                    this.sceneActions.execute(payload.accion_escena);
                }
                break;

            case 'speech_end':
                window.dispatchEvent(new CustomEvent('IRIS:speech-end', { detail: payload }));
                this.activityCanvas?.handleSpeechEnd();
                // Speech started → tool indicators are stale; clear them
                // so the chip doesn't stick around forever.
                this.panel?.hideToolProgress?.();
                this.subtitles.hide();
                this.speechPresentation.end();
                this.sceneActions?.reset();
                break;

            case 'voice_unavailable':
                this.subtitles?.show(payload.message, 7000);
                break;

            case 'turn_started':
                this.roomRoutines?.turnStarted(payload.turn);
                break;

            case 'turn_ended':
                this.roomRoutines?.turnEnded(payload.turn);
                break;

            case 'emotion':
                this._setEmotion(payload.emotion);
                break;

            case 'animation': {
                const name = payload.name || 'idle';
                this._playLLMAnimation(name).catch(err => {
                    console.warn('[animation] playLLMAnimation threw:', err);
                });
                break;
            }

            case 'scene_action':
                if (this.sceneActions) {
                    // Format from websocket_server: { animacion_virtual, escena }
                    this.sceneActions.execute(
                        payload.animacion_virtual || payload.action
                    );
                }
                break;

            case 'lip_sync':
                // Server-only audio path (legacy): the server already
                // started playing through its own speakers; we just
                // animate the mouth using `performance.now()` as the
                // clock. Suffers from a small drift (~100-400ms) due
                // to the gap between this event arriving and the
                // server's PortAudio actually emitting samples — see
                // the `tts_audio` case below for the new path that
                // anchors visemes to the BROWSER's audio onset.
                this.lipSync.processVisemes(payload.visemes);
                break;

            case 'tts_audio':
                // Server forwarded the synthesized audio bytes for us
                // to play in the browser, so phone / LAN users hear
                // IRIS instead of just seeing her mouth move. The
                // payload shape is:
                //   { audio: base64-encoded bytes,
                //     mime:  'audio/mpeg' | 'audio/wav' | …,
                //     visemes: [{time, duration, text}, …] }
                // We anchor viseme timing to ``audio.onplaying`` — the
                // browser-level moment when sample emission actually
                // starts — which fixes the legacy ~100-400ms drift.
                this.inboxVoice?.stop();
                this.ttsAudioPlayer?.handle(payload);
                break;

            case 'subtitle':
                if (payload.final) this.subtitles.show(payload.text);
                else this.subtitles.update(payload.text);
                break;

            case 'status':
                this.activityCanvas?.handleStatus(payload.status);
                this._updateStatus(payload.status);
                this._toggleListening(payload.status === 'listening');
                break;

            case 'hermes_wake_state': {
                this.manualVoice?.showWakeState?.(payload);
                this._setWakeUiState(payload);
                if (payload.state === 'detected') {
                    this.subtitles?.show('Activación detectada · te escucho', 1800);
                } else if (payload.error) {
                    this.subtitles?.show(payload.error, 3000);
                }
                break;
            }

            case 'hermes_voice_state':
                this.manualVoice?.apply(payload);
                break;

            case 'voice_partial':
                this.manualVoice?.showPartial?.(payload);
                break;

            case 'voice_transcript':
                if (payload.error) {
                    this.subtitles?.show(payload.error, 2200);
                } else if (payload.accepted && payload.text?.trim()) {
                    // Recognition was already submitted by Core. Show the user
                    // message here, without sending it back a second time.
                    this.panel?.addMessage?.('user', payload.text.trim());
                    this.manualVoice?.showTranscript?.(payload);
                }
                break;

            case 'wizard_launched':
                this.subtitles.show("Abriendo el Asistente de Configuración...");
                setTimeout(() => this.subtitles.hide(), 4000);
                break;

            case 'hermes_command_result':
                this.panel?.addMessage?.('command', payload.text || 'El comando no devolvió información.');
                break;

            case 'iris_app_intent_closed':
                this.activityCanvas?.cancelAppIntent?.(payload.request_id);
                break;

            case 'iris_app_intent': {
                // F09.4: an operation of an IRIS OS app, or the confirmation of one Core runs.
                const fail = (error) => ({
                    schema: 'iris.app-result/v1',
                    request_id: String(payload?.request_id || ''),
                    status: 'error',
                    message: error?.message || 'La app no pudo ejecutar la acción.',
                    data: {},
                });
                Promise.resolve(this.activityCanvas?.handleAppIntent?.(payload) ?? fail(new Error('IRIS OS no está listo.')))
                    .catch(fail)
                    .then((result) => {
                        if (this.ws?.send('iris_app_result', result) !== true) {
                            console.warn('[IRIS OS] No se pudo entregar el resultado de la app a Core.');
                        }
                    });
                break;
            }

            case 'iris_player_intent':
                this.activityCanvas?.handlePlayerIntent?.(payload).then((result) => {
                    if (this.ws?.send('iris_player_result', result) !== true) {
                        console.warn('[IRIS Player] No se pudo entregar la confirmación a Core.');
                    }
                }).catch((error) => {
                    const sent = this.ws?.send('iris_player_result', {
                        schema: 'iris.app-result/v1',
                        request_id: String(payload?.request_id || ''),
                        status: 'error',
                        message: error?.message || 'IRIS Player no pudo ejecutar la acción.',
                        data: {},
                    });
                    if (sent !== true) {
                        console.warn('[IRIS Player] No se pudo entregar el error de ejecución a Core.');
                    }
                });
                break;

            case 'pong':
                break;

            case 'tool_progress':
                // Quick Win #1 — Hermes is running a tool (web search,
                // file read, code exec…). Show a transient indicator
                // so the user knows the avatar is doing real work
                // instead of "frozen". The panel renders a small chip
                // above the chat input that auto-clears on next
                // speech_start / speech_end.
                this.panel?.showToolProgress?.(payload);
                this.activityCanvas?.handleToolProgress(payload);
                this.roomRoutines?.toolRunning(payload.turn);
                break;

            default:
                console.log('[WS] Unknown:', type, payload);
        }
    }

    /**
     * Play a VRMA clip requested by the LLM (`animacion_virtual`).
     *
     * Robustness layer over ``idle.playClipExternal``:
     *   1. First try the literal clip name from the LLM.
     *   2. If that fails AND the IdleSystem has no player yet (lazy
     *      init didn't fire because bones weren't ready at the right
     *      frame), force a manifest reload and retry once.
     *   3. If THAT also fails (clip truly missing on disk, or no VRM
     *      humanoid at all), fall back to the procedural head burst.
     *
     * Logs intentionally verbose ONLY in the failure paths — happy
     * path is silent. The user surface (the avatar) is the real
     * indicator that things work.
     */
    async _playLLMAnimation(name) {
        return this.animationDirector?.play(name);
    }

    /** Match the avatar's facial expression to a VRMA clip so the
     *  body gesture and face mood read as one beat. The VRMA file
     *  itself only drives skeleton, not facial blendshapes. */
    _syncFaceMoodToClip(clip) {
        const profile = this.idle?._vrmaManifest?.facial_profiles?.[clip];
        if (profile) {
            // The shared pose layer adds a temporary accent and restores the
            // live response mood on exit. Do not replace that underlying mood.
            return;
        }
        // Route through `_setEmotion` (not `avatar.setEmotion` directly)
        // so the MicroLife state machine + particles + HUD all see the
        // change. Previously these three branches bypassed `_setEmotion`
        // and MicroLife stayed in 'idle' even while a 'think' clip
        // played — the eyes wandered as if there were no thinking
        // happening at all, contradicting the clip on screen.
        let emo = null;
        if (clip === 'wave' || clip === 'dance') emo = 'feliz';
        else if (clip === 'think')               emo = 'pensativa';
        else if (clip === 'shy')                 emo = 'sorprendida';
        if (emo) this._setEmotion(emo);
    }

    _setEmotion(emotion) {
        this.speechPresentation.setEmotion(emotion);
    }

    // The displayed mood is independent from the mouth's expression during TTS.
    _showEmotion(emotion) {
        this.currentEmotion = emotion;
        this.particles?.setMood(emotion);

        const emojis = { neutral:'ÁNIMO', feliz:'ALEGRÍA', triste:'TRISTEZA', pensativa:'PENSANDO', sorprendida:'SORPRESA', enojada:'ENOJO' };
        const el1 = document.getElementById('emotion-emoji');
        const el2 = document.getElementById('emotion-label');
        if (el1) el1.textContent = emojis[emotion] || 'ÁNIMO';
        if (el2) el2.textContent = emotion;

        // Drive the energy bar (was hard-pinned to 50% in CSS).
        this._updateEmotionEnergy(emotion);

        // Trigger bounce microanimation
        this._triggerEmotionBounce();
    }


    _updateStatus(status) {
        this._visionStatus = status;
        const dot = document.querySelector('.status-dot');
        const txt = document.querySelector('.status-text');
        if (dot) {
            dot.className = 'status-dot';
            const cls = { connected:'connected', idle:'connected', ready:'connected',
                          listening:'listening', thinking:'thinking', speaking:'connected' };
            if (cls[status]) dot.classList.add(cls[status]);
        }
        if (txt) txt.textContent = ROOM_STATUS_LABELS[status] || 'Sin estado';
    }

    _toggleListening(on) {
        document.getElementById('listening-indicator')?.classList.toggle('hidden', !on);
        this.microLife?.setListening?.(on);
    }

    // ────────────────────────────────────────────────────────
    //  Render loop
    // ────────────────────────────────────────────────────────

    _onResize() {
        const w = window.innerWidth, h = window.innerHeight;
        this.camera.aspect = w / h;
        this.camera.updateProjectionMatrix();
        this.graphicsQuality.resize();
        if (this.inspectorMode && this._framedRoomPreset) {
            this.transitionToInspectPreset(this._framedRoomPreset, { immediate: true });
        }
    }

    _animate() {
        this.inboxVoice?.sync();
        this._animationFrame = requestAnimationFrame(() => this._animate());
        if (this._frameLimiter && !this._frameLimiter.ready(performance.now(), document.visibilityState !== 'hidden')) return;
        // A suspended tab can return seconds later. Advance every animation
        // on the same bounded clock, including procedural motion and springs.
        const dt = Math.min(this.clock.getDelta(), 0.05);
        const t = this._animationElapsed = (this._animationElapsed || 0) + dt;

        if (this.inspectorMode && this.orbitControls) {
            // Inspector: preset transitions first, then user orbit controls.
            const transitioning = this._updateInspectCameraTransition();
            if (!transitioning) this.orbitControls.update();
        } else {
            // Normal: subtle automatic drift, or the view of the spot she goes to.
            this.conversationCamera?.update(dt, t, this.sceneActions?.framedSpot?.()?.view);
        }

        // Local emotion classification feeds context without copying the face.
        const mocapResult = this.mocap?.latest?.();
        if (mocapResult?.blendshapes) {
            if (!this._emotionClassifier) {
                this._emotionClassifier = new EmotionClassifier();
            }
            const e = this._emotionClassifier.classify(mocapResult.blendshapes);
            if (e) {
                this._latestEmotion = e;
                // Drive the engagement weight for IK lookAt — when the
                // user is paying attention (high arousal, forward
                // valence), IRIS locks gaze more tightly.
                this._engagement = THREE.MathUtils.clamp(
                    0.45 + 0.4 * e.arousal + 0.15 * Math.max(0, e.valence),
                    0.3, 0.95,
                );
                // Throttle the WebSocket emit to ~1 Hz — the LLM
                // doesn't need 30 updates/sec, and the bandwidth is
                // wasted on a value that smooths slowly anyway.
                const contextAllowed =
                    this.visionAssistant.permissions?.context?.verdict === 'allow';
                if (contextAllowed) {
                    this._emotionEmitAccum = (this._emotionEmitAccum ?? 0) + dt;
                    if (this._emotionEmitAccum >= 1.0) {
                        this._emotionEmitAccum = 0;
                        this.ws?.send?.('user_emotion', {
                            label: e.label,
                            valence: e.valence,
                            arousal: e.arousal,
                            confidence: e.confidence,
                            intensity: e.intensity,
                        });
                    }
                } else {
                    this._emotionEmitAccum = 0;
                }
                // Feed continuous params to idle (step 8 wires the
                // receiver). Safe no-op if idle ignores it.
                this.idle?.feedUserEmotion?.(e);
            }
        } else if (this._emotionClassifier) {
            // Camera off — release the classifier state so reactivating
            // doesn't carry over a stale label across sessions.
            this._emotionClassifier.reset();
            this._latestEmotion = null;
        }

        // Vision Assistant publishes only a tiny, derived presence signal.
        // MediaPipe remains the sole frame consumer; no canvas capture, image
        // bytes or audio enter this WebSocket path.
        const visionPaused = Number(this.visionAssistant.paused_until || 0) > Date.now() / 1000;
        this.mocap?.configureVision?.(this.visionAssistant);
        if (this.visionAssistant.enabled && !visionPaused) {
            this._visionEmitAccum += dt;
            if (this._visionEmitAccum >= 1.5) {
                this._visionEmitAccum = 0;
                this.ws?.send?.('vision_activity', buildVisionActivity({
                    documentLike: document,
                    status: this.manualVoice?.state.busy ? 'listening' : this._visionStatus,
                }));
                const signal = this.panel?.camActive && buildVisionSignal(this.mocap?.latestPresence?.(), {
                    nowMs: performance.now(),
                });
                if (signal) this.ws?.send?.('vision_signal', signal);
                if (this.panel?.camActive && this.mocap?.running) {
                    this.ws?.send?.('vision_sensor_diagnostics', this.mocap.diagnostics());
                }
            }
        } else {
            this._visionEmitAccum = 0;
        }

        this.microLife?.beginFrame();
        this.microLife?.update(dt, t);

        // Update all systems
        this.avatarManager.update(dt, t, { deferVRM: true });
        this.particles.update(dt, t);
        this.lipSync.update(dt);
        this.animationDirector?.updateActivity();
        this.idle?.update(dt, t);
        this.room?.update(dt, t);
        this.sceneActions?.update(dt, t);
        this.roomRoutines?.update();
        this.activityCanvas?.update(dt, t);

        // Authored gestures retain exclusive body ownership during speech.
        const explicitAnimationPlaying = Boolean(
            this.idle?.isExplicitAnimationActive?.()
            || this.idle?._vrmaExternalClip
            || this.idle?.currentPose,
        );
        if (explicitAnimationPlaying) {
            this.speechAnimator?.yieldToExternalClip?.();
        } else {
            this.speechAnimator?.update(dt, t);
        }

        // MicroLife post-pass goes LAST so its head/spine/hips writes
        // survive every other system that touches those bones (clip
        // animation in avatarManager, idle pose blender, speech-bob
        // overlay, etc). Earlier we ran applyPost right after
        // avatarManager.update, but SpeechAnimator/IdleSystem use
        // `bone.quaternion.copy(base).multiply(dq)` SET semantics that
        // overwrote everything MicroLife had written during speech and
        // idle poses — gaze-follow, head noise, and body sway were
        // silently disabled the whole time. Running last makes
        // MicroLife the canonical "final pass" for micro-presence.
        // Inspector mode still runs applyPost (head noise + body sway
        // keep the avatar from looking frozen while orbiting); only
        // head-follow self-disables inside _accumHeadFollow.
        this.microLife?.applyPost(dt);

        // Apply the complete pose once, then simulate spring bones and render.
        this.avatarManager.finalizeFrame(dt);
        this.composer.render();
    }

    // Trigger emotion bounce on the UI emoji
    _triggerEmotionBounce() {
        const emoji = document.getElementById('emotion-emoji');
        if (!emoji) return;
        emoji.classList.remove('bounce');
        void emoji.offsetWidth; // reflow
        emoji.classList.add('bounce');
        emoji.addEventListener('animationend', () => emoji.classList.remove('bounce'), { once: true });
    }

    /**
     * Toggle the cinematic speaking visuals (ring, halo, emotion-display
     * energy-ring). Single source of truth so the speech_start / speech_end
     * switch cases stay readable.
     *
     * The .active-ring-2 class is added 400ms after .active so the second
     * ring trails the first — purely a CSS animation-delay would work, but
     * if the ring is re-triggered before the previous cycle ends, the delay
     * doesn't reset. Toggling the class manually does.
     */
    _setSpeakingVisuals(speaking) {
        const ring  = document.getElementById('speaking-ring');
        const emo   = document.getElementById('emotion-display');
        const emoji = document.getElementById('emotion-emoji');
        if (speaking) {
            ring?.classList.add('active');
            // Schedule the second ring trailer. Tracked on the element
            // itself so a re-trigger before speech_end cancels the prior.
            clearTimeout(this._ring2Timer);
            this._ring2Timer = setTimeout(() => ring?.classList.add('active-ring-2'), 400);
            emo?.classList.add('speaking', 'active');
            emoji?.classList.add('speaking');
        } else {
            clearTimeout(this._ring2Timer);
            ring?.classList.remove('active', 'active-ring-2');
            emo?.classList.remove('speaking', 'active');
            emoji?.classList.remove('speaking');
        }
    }

    /**
     * Drive the emotion-energy bar (width: in styles.css it defaults to
     * 50% and never changes). Map the named emotion to a rough intensity
     * so the bar reflects mood — neutral resting, surprised/angry near
     * the top. Without this the bar is dead UI.
     */
    _updateEmotionEnergy(emotion) {
        const bar = document.getElementById('emotion-energy-bar');
        if (!bar) return;
        const levels = {
            neutral:     40,
            feliz:       75,
            triste:      25,
            pensativa:   55,
            sorprendida: 90,
            enojada:     95,
        };
        const pct = levels[emotion] ?? 50;
        bar.style.width = `${pct}%`;
    }

}

// ── Boot ──
const app = new IrisApp();
app.init().catch(err => {
    console.error('IRIS init failed:', err);
    const loading = document.getElementById('loading-screen');
    if (loading) {
        const message = document.createElement('p');
        message.style.color = '#ef4444';
        message.textContent = `Error: ${err?.message || err || 'fallo de inicio desconocido'}`;
        loading.replaceChildren(message);
    }
});

