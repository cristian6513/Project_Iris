import { SttLanguageField, liveTextNote, saveSttSettings } from './stt_language.js?v=1';

const STREAMING_HINT = 'Opción de este perfil, también para la activación por voz. Puede enviar audio al proveedor STT mientras hablas. Si no admite directo o falla, se intenta la transcripción final habitual.';

/** Local controls for Hermes microphone capture. */
export class ManualVoiceController {
    constructor({ button, fetchImpl = (...args) => fetch(...args), notice = () => {}, onState = () => {},
        sendConversation = null, openPermissions = null, openVoiceSettings = null,
        focusFallback = () => {} } = {}) {
        this.button = button;
        this.fetch = fetchImpl;
        this.notice = notice;
        this.onState = onState;
        this.focusFallback = focusFallback;
        this.sendConversation = sendConversation;
        this.conversationAvailable = false;
        this.conversation = null;
        this.voiceNotice = null;
        this.voiceNoticeTimer = null;
        this.noticeActions = {
            microphone_blocked: { label: 'Abrir Permisos', open: openPermissions },
            voice_unavailable: { label: 'Abrir ajustes de voz', open: openVoiceSettings },
        };
        this.clientId = crypto.randomUUID(); // Per window, never shared via storage.
        this.state = { state: 'off', busy: false, revision: -1 };
        this.connected = false;
        this.pending = false;
        this.failure = '';
        this.transcript = '';
        this.partial = '';
        this.partialSequence = 0;
        this.discardPartials = false;
        this.wakeCapture = '';
        this.streaming = false;
        this.sttSettings = null;
        this.settingsReady = false;
        this.settingsPending = false;
        this.consented = false;
        this.confirming = false;
        this.panel = document.createElement('section');
        this.panel.className = 'manual-voice-panel';
        this.panel.hidden = true;
        this.panel.setAttribute('aria-label', 'Hablar con IRIS');
        this.panel.innerHTML = `<strong>VOZ · HERMES</strong><p role="status" aria-live="polite"></p>
            <small>El audio usa la transcripción de Hermes, local o remota según tu configuración.</small>
            <details class="voice-advanced"><summary>Opciones avanzadas</summary>
            <label class="voice-language-option"><span>Idioma al hablar</span><select data-stt-language></select></label>
            <small data-stt-language-note></small>
            <label class="voice-streaming-option"><input type="checkbox" data-streaming> Transcribir mientras hablo</label>
            <small data-streaming-hint></small>
            </details>
            <aside class="voice-partial" hidden><small>TRANSCRIPCIÓN PROVISIONAL · aún no enviada</small><p data-partial></p></aside>
            <div><button type="button" data-confirm>Hablar ahora</button><button type="button" data-converse hidden>Iniciar conversación</button><button type="button" data-cancel>Cancelar</button></div>`;
        document.body.append(this.panel);
        this.message = this.panel.querySelector('p');
        this.confirmButton = this.panel.querySelector('[data-confirm]');
        this.converseButton = this.panel.querySelector('[data-converse]');
        this.cancelButton = this.panel.querySelector('[data-cancel]');
        this.streamingInput = this.panel.querySelector('[data-streaming]');
        this.streamingHint = this.panel.querySelector('[data-streaming-hint]');
        this.languageField = new SttLanguageField({ select: this.panel.querySelector('[data-stt-language]'),
            note: this.panel.querySelector('[data-stt-language-note]'), onChoose: (language) => { void this.saveLanguage(language); } });
        this.partialBox = this.panel.querySelector('.voice-partial');
        this.partialMessage = this.panel.querySelector('[data-partial]');
        this.streamingInput.addEventListener('change', () => { void this.saveStreaming(this.streamingInput.checked); });
        this.button.addEventListener('click', () => this.toggle());
        this.confirmButton.addEventListener('click', () => {
            this.consented = true;
            this.confirming = false;
            void this.start();
        });
        this.cancelButton.addEventListener('click', () => this.cancel());
        this.converseButton?.addEventListener('click', () => this.startConversation());
        if (sendConversation) {
            this.conversationBar = document.createElement('section');
            this.conversationBar.className = 'voice-conversation-bar';
            this.conversationBar.hidden = true;
            this.conversationBar.setAttribute('aria-label', 'Conversación por voz');
            this.conversationBar.innerHTML = `<p role="status" aria-live="polite"></p><button type="button" data-end-conversation>Terminar</button>
                <aside class="voice-conversation-partial" hidden><small>Transcripción provisional</small><p data-conversation-partial></p></aside>
                <div class="voice-conversation-notice" hidden><p aria-live="polite" data-voice-notice></p>
                    <div><button type="button" data-voice-action hidden></button><button type="button" data-close-notice>Cerrar aviso</button></div>
                </div>`;
            this.conversationStatus = this.conversationBar.querySelector('[role="status"]');
            this.conversationPartial = this.conversationBar.querySelector('[data-conversation-partial]');
            this.conversationPreview = this.conversationBar.querySelector('.voice-conversation-partial');
            this.endConversationButton = this.conversationBar.querySelector('[data-end-conversation]');
            this.voiceNoticeBox = this.conversationBar.querySelector('.voice-conversation-notice');
            this.voiceNoticeText = this.conversationBar.querySelector('[data-voice-notice]');
            this.voiceNoticeAction = this.conversationBar.querySelector('[data-voice-action]');
            this.closeVoiceNotice = this.conversationBar.querySelector('[data-close-notice]');
            this.endConversationButton.addEventListener('click', () => this.endConversation());
            this.voiceNoticeAction.addEventListener('click', () => this.openNoticeSettings());
            this.closeVoiceNotice.addEventListener('click', () => this.clearNotice(true));
            document.body.append(this.conversationBar);
        }
        document.addEventListener('keydown', (event) => {
            if (event.key !== 'Escape') return;
            if (this.voiceNotice && !this.conversation) this.clearNotice(true);
            else if (this.conversation || !this.panel.hidden) void this.cancel();
        });
        document.addEventListener('visibilitychange', () => {
            if (document.hidden) void this.cancel();
        });
        window.addEventListener('pagehide', () => { void this.cancel(); });
        this.render();
    }

    get ownsCapture() { return this.state.client_id === this.clientId; }
    get closingConversation() { return this.conversation?.phase === 'ending'; }

    setConnected(connected) {
        this.connected = Boolean(connected);
        if (!connected) { this.clearPartial(); this.wakeCapture = ''; this.conversationAvailable = false; }
        if (connected) {
            this.state.revision = -1;
            void this.refresh();
        } else if (this.conversation) {
            this.conversation = null; // Core also ends its socket-owned session.
            this.transcript = '';
            this.showNotice('Se perdió la conexión. La conversación por voz terminó.');
        } else if (this.ownsCapture && this.state.busy) {
            void this.cancel();
        }
        this.render();
    }

    apply(state) {
        if (!state || !Number.isFinite(state.revision) || state.revision < this.state.revision) return;
        if (state.capture_id !== this.state.capture_id || !['recording', 'stopping', 'transcribing'].includes(state.state)) this.clearPartial();
        this.state = state;
        this.render();
    }

    showTranscript(payload) {
        if (payload.mode !== 'manual') { this.clearPartial(); this.wakeCapture = ''; this.render(); return; }
        if (payload.mode !== 'manual' || !payload.accepted || !this.ownsCapture) return;
        if (payload.capture_id && payload.capture_id !== this.state.capture_id) return;
        if (!this.conversation) this.transcript = String(payload.text || '').trim();
        this.clearPartial();
        this.render();
    }

    clearPartial() { this.partial = ''; this.partialSequence = 0; }

    showWakeState(payload) {
        if (payload.state === 'detected') {
            this.clearPartial(); this.transcript = '';
            this.discardPartials = false;
            this.wakeCapture = payload.capture_id || '';
        } else if (!['recording', 'transcribing', 'detected'].includes(payload.state)) {
            this.clearPartial(); this.wakeCapture = '';
        }
        this.render();
    }

    showPartial(payload) {
        if (!this.connected || document.hidden || this.transcript || this.discardPartials) return;
        if (payload.mode === 'manual') {
            if (!this.ownsCapture || payload.client_id !== this.clientId || payload.capture_id !== this.state.capture_id
                || !['recording', 'stopping', 'transcribing'].includes(this.state.state)) return;
        } else if (payload.mode !== 'wake' || !this.wakeCapture || payload.capture_id !== this.wakeCapture) return;
        if (!Number.isSafeInteger(payload.sequence) || payload.sequence <= this.partialSequence) return;
        this.partialSequence = payload.sequence;
        this.partial = typeof payload.text === 'string' ? payload.text.slice(0, 4096).trim() : '';
        this.render();
    }

    saveStreaming(enabled) { return this.saveSettings({ streaming: !!enabled }); }

    /** «Idioma al hablar» (stt_language.js): Hermes uses it from the next recording. */
    saveLanguage(language) { return this.saveSettings({ language }); }

    async saveSettings(changes) {
        if (!this.settingsReady || this.state.busy || this.settingsPending || this.pending) { this.render(); return; }
        this.settingsPending = true; this.failure = ''; this.render();
        try {
            this.applySettings({ ...this.sttSettings, ...await saveSttSettings(this.fetch, changes) });
        } catch (error) { this.failure = error.message; await this.refresh(); }
        finally { this.settingsPending = false; this.render(); }
    }

    applySettings(settings) {
        this.sttSettings = settings;
        this.settingsReady = !settings.error;
        this.streaming = settings.streaming === true;
        if (settings.error) this.failure = settings.error;
    }

    async refresh() {
        try {
            const response = await this.fetch('/api/hermes-voice/status', { cache: 'no-store' });
            if (response.ok) {
                const result = await response.json();
                if (result.settings) this.applySettings(result.settings);
                this.apply(result);
            }
        } catch { /* No capture is started by a status read. */ }
    }

    toggle() {
        if (!this.connected || this.pending) return;
        if (this.conversation) { this.endConversationButton?.focus(); return; }
        if (this.state.busy) {
            if (this.ownsCapture && this.state.state === 'recording') void this.command('stop');
            return;
        }
        this.clearNotice();
        {
            this.confirming = true;
            this.render();
            this.confirmButton.focus();
        }
    }

    async start() {
        if (this.conversation || !this.connected || this.pending || this.settingsPending || this.state.busy || document.hidden) return;
        this.transcript = '';
        this.discardPartials = false;
        this.clearPartial(); this.wakeCapture = '';
        this.state = { ...this.state, state: 'arming', busy: true, error: '',
            client_id: this.clientId, capture_id: crypto.randomUUID() };
        await this.command('start');
        if (document.hidden && this.ownsCapture && this.state.busy) await this.cancel();
    }

    async command(action) {
        const captureId = this.state.capture_id;
        this.failure = '';
        this.requests = (this.requests || 0) + 1;
        this.pending = true;
        this.render();
        try {
            const response = await this.fetch('/api/hermes-voice/runtime', {
                method: 'POST', keepalive: true,
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ action, client_id: this.clientId,
                    capture_id: captureId, confirmed: action === 'start' }),
            });
            const result = await response.json();
            this.apply(result);
            if (!response.ok) {
                // A rejected start (permission/validation) did not arm capture.
                if (action === 'start' && !Number.isFinite(result.revision)) {
                    this.state = { ...this.state, state: 'error', busy: false };
                }
                const error = new Error(typeof result.detail === 'string' ? result.detail : 'No se pudo cambiar el micrófono.');
                if (result.code === 'microphone_blocked') error.code = result.code;
                throw error;
            }
        } catch (error) {
            this.failure = error.message || 'No pude confirmar el estado del micrófono.';
            if (error.code && this.conversationBar) {
                const message = this.failure;
                this.failure = '';
                this.showNotice(message, error.code);
            } else this.notice(this.failure);
            // Never assume OFF or repeat an uncertain recording request.
            void this.refresh();
        } finally {
            this.requests -= 1;
            this.pending = this.requests > 0;
            this.render();
        }
    }

    async cancel() {
        if (this.conversation) {
            if (!this.closingConversation || this.conversation.mic_uncertain) this.endConversation();
            return;
        }
        this.confirming = false;
        this.discardPartials = true;
        this.clearPartial(); this.wakeCapture = '';
        if (this.ownsCapture && this.state.busy && this.state.state !== 'processing') {
            await this.command('cancel');
        } else {
            this.failure = '';
            this.transcript = '';
            this.state.error = '';
            this.panel.hidden = true;
        }
    }

    startConversation() {
        if (!this.conversationAvailable || !this.sendConversation || !this.confirming || !this.connected || this.pending
            || this.settingsPending || this.state.busy || document.hidden) return;
        const id = crypto.randomUUID();
        this.confirming = false;
        this.failure = '';
        this.transcript = '';
        this.clearPartial();
        this.clearNotice();
        this.discardPartials = false;
        this.conversation = { id, phase: 'arming' };
        if (!this.sendConversation({ action: 'start', id, client_id: this.clientId, confirmed: true })) {
            this.conversation = null;
            this.failure = 'No pude conectar con Core para iniciar la conversación.';
        }
        this.render();
        if (this.conversation) this.endConversationButton?.focus();
    }

    setConversationAvailable(available) {
        this.conversationAvailable = Boolean(available && this.sendConversation);
        this.render();
    }

    endConversation() {
        if (!this.conversation) return true;
        this.clearPartial();
        this.conversation.phase = 'ending';
        const sent = this.sendConversation({ action: 'end', id: this.conversation.id, client_id: this.clientId });
        if (!sent) {
            this.conversation.mic_uncertain = true;
            this.conversation.reason = 'No pude enviar el cierre. Reintenta o cierra esta ventana.';
        }
        this.render();
        return Boolean(sent);
    }

    applyConversation(payload) {
        if (payload?.id !== this.conversation?.id || !this.conversation) return;
        if (this.conversation.phase === 'ending' && !['ending', 'off'].includes(payload.phase)) return;
        if (payload.phase === 'off') {
            const restoreFocus = document.activeElement === this.endConversationButton;
            this.conversation = null;
            this.clearPartial();
            this.transcript = '';
            if (payload.reason) this.showNotice(payload.reason, payload.code);
            this.render();
            if (restoreFocus && this.voiceNotice) {
                (this.voiceNoticeAction.hidden ? this.closeVoiceNotice : this.voiceNoticeAction).focus();
            } else if (restoreFocus) this.restoreFocus();
        } else {
            this.conversation = payload;
            this.render();
        }
    }

    showNotice(text, code = '') {
        if (!text) return;
        if (!this.conversationBar) { this.notice(text); return; }
        clearTimeout(this.voiceNoticeTimer);
        this.voiceNotice = { text, code };
        if (!this.noticeActions[code]?.open) {
            this.voiceNoticeTimer = setTimeout(() => {
                if (!this.voiceNoticeBox.contains(document.activeElement)) this.clearNotice();
            }, 7000);
        }
        this.render();
    }

    clearNotice(restoreFocus = false) {
        if (!this.voiceNotice) return;
        clearTimeout(this.voiceNoticeTimer);
        this.voiceNotice = null;
        this.render();
        if (restoreFocus) this.restoreFocus();
    }

    restoreFocus() {
        if (this.conversation) this.endConversationButton.focus();
        else if (this.button.getClientRects().length) this.button.focus();
        else this.focusFallback();
    }

    async openNoticeSettings() {
        const entry = this.voiceNotice;
        const action = this.noticeActions[entry?.code]?.open;
        if (!action) return;
        if (entry.code === 'voice_unavailable' && !this.endConversation()) return;
        try {
            if (await action() === false) throw new Error('Settings unavailable');
            if (this.voiceNotice === entry) this.clearNotice(document.activeElement === this.voiceNoticeAction);
        } catch {
            if (this.voiceNotice === entry) this.showNotice('No pude abrir esta sección. Ábrela desde IRIS OS.', entry.code);
        }
    }

    render() {
        const themeRoot = document.querySelector('.hermes-workbench') || document.documentElement;
        const style = globalThis.getComputedStyle?.(themeRoot);
        const accent = style?.getPropertyValue('--companion-accent').trim()
            || style?.getPropertyValue('--iris-os-accent').trim();
        if (accent) {
            this.panel.style.setProperty('--voice-accent', accent);
            this.button.style.setProperty('--voice-accent', accent);
            this.conversationBar?.style.setProperty('--voice-accent', accent);
        }
        const { state, busy } = this.state;
        const conversation = this.conversation;
        const error = this.failure || this.state.error;
        const own = this.ownsCapture;
        const recording = own && state === 'recording';
        const capturing = own && ['arming', 'recording', 'stopping', 'unknown'].includes(state);
        this.button.disabled = !this.connected || this.pending || this.settingsPending || (busy && !recording);
        this.button.classList.toggle('active-mode', capturing);
        this.button.dataset.state = own && busy ? (recording ? 'recording' : state) : 'off';
        this.button.setAttribute('aria-pressed', String(capturing));
        this.button.setAttribute('aria-label', recording ? 'Terminar y enviar la grabación' : 'Hablar con IRIS');
        this.button.title = recording ? 'Terminar y enviar · Escape para cancelar' : 'Hablar directamente · Wake está en Configuración';
        const label = this.button.querySelector('[data-voice-label]');
        if (label) label.textContent = conversation ? 'Voz activa' : recording ? 'Enviar' : 'Hablar';
        if (conversation) {
            this.button.disabled = false;
            this.button.setAttribute('aria-label', 'Conversación por voz activa');
            this.button.title = 'Conversación por voz · Escape para terminar';
        }
        this.confirmButton.hidden = !this.confirming;
        this.confirmButton.disabled = this.settingsPending || this.pending || !this.connected;
        if (this.converseButton) {
            this.converseButton.hidden = !this.conversationAvailable || !this.confirming;
            this.converseButton.disabled = this.confirmButton.disabled;
        }
        const settingsLocked = !this.settingsReady || busy || this.pending || this.settingsPending || !this.connected || !!this.wakeCapture;
        this.streamingInput.checked = this.streaming;
        this.streamingInput.disabled = settingsLocked;
        this.streamingHint.textContent = liveTextNote(this.sttSettings) || STREAMING_HINT;
        this.languageField.render(this.sttSettings, { disabled: settingsLocked });
        this.partialBox.hidden = !this.partial;
        this.partialMessage.textContent = this.partial;
        this.cancelButton.textContent = this.confirming || (own && busy && state !== 'processing') ? 'Cancelar' : 'Cerrar';
        const messages = {
            arming: 'Preparando el micrófono…', recording: 'Te escucho. Pulsa Enviar o termina de hablar; Escape cancela.',
            stopping: 'Terminando la captura…', transcribing: 'Transcribiendo…',
            processing: 'Enviando el texto a la conversación…', cancelling: 'Cancelando la grabación…',
        };
        this.message.textContent = this.confirming
            ? (this.conversationAvailable
                ? 'El audio puede ir al proveedor STT configurado. En conversación, Iris vuelve a escuchar después de responder.'
                : 'El micrófono se abrirá solo para esta intervención. El audio puede ir al proveedor STT configurado. ¿Comenzar?')
            : error || (busy && !own ? 'El micrófono está en uso en otra ventana.' : messages[state] || 'Micrófono apagado.');
        if (this.wakeCapture && this.partial && !error) this.message.textContent = 'Hey Hermes · te escucho. El texto puede cambiar hasta terminar.';
        if (this.transcript && !error && !this.confirming) {
            this.message.textContent = 'Texto enviado: «' + this.transcript + '»';
        }
        this.panel.hidden = document.hidden || Boolean(conversation || this.voiceNotice) || !(this.confirming || busy || error || this.transcript || this.partial);
        if (this.conversationBar) {
            this.conversationBar.hidden = !(conversation || this.voiceNotice) || document.hidden;
            this.conversationBar.dataset.phase = conversation?.phase || 'off';
            const messages = {
                arming: 'Preparando micrófono…', listening: 'Micrófono activo · Te escucho',
                transcribing: 'Transcribiendo…', replying: 'Preparando respuesta · Micrófono en pausa',
                speaking: 'Iris está hablando · Micrófono en pausa', ending: 'Cerrando micrófono…',
            };
            const text = conversation?.mic_uncertain ? conversation.reason : messages[conversation?.phase] || '';
            if (this.conversationStatus.textContent !== text) this.conversationStatus.textContent = text;
            this.conversationStatus.hidden = !conversation;
            this.endConversationButton.hidden = !conversation;
            const preview = ['listening', 'transcribing'].includes(conversation?.phase) ? this.partial : '';
            this.conversationPreview.hidden = !preview;
            this.conversationPartial.textContent = preview;
            this.endConversationButton.textContent = conversation?.mic_uncertain ? 'Reintentar cierre' : 'Terminar';
            this.endConversationButton.disabled = this.closingConversation && !conversation.mic_uncertain;
            this.voiceNoticeBox.hidden = !this.voiceNotice;
            const noticeText = this.voiceNotice?.text || '';
            if (this.voiceNoticeText.textContent !== noticeText) this.voiceNoticeText.textContent = noticeText;
            const action = this.noticeActions[this.voiceNotice?.code];
            this.voiceNoticeAction.hidden = !action?.open;
            this.voiceNoticeAction.textContent = action?.label || '';
        }
        this.onState(this.state);
    }
}
