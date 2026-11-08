/** A manual utterance via Hermes; never a browser recorder or wake detector. */
export class ManualVoiceController {
    constructor({ button, fetchImpl = (...args) => fetch(...args), notice = () => {}, onState = () => {} }) {
        this.button = button;
        this.fetch = fetchImpl;
        this.notice = notice;
        this.onState = onState;
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
        this.settingsReady = false;
        this.settingsPending = false;
        this.consented = false;
        this.confirming = false;
        this.panel = document.createElement('section');
        this.panel.className = 'manual-voice-panel';
        this.panel.hidden = true;
        this.panel.setAttribute('aria-label', 'Hablar con IRIS');
        this.panel.innerHTML = `<strong>VOZ · HERMES</strong><p role="status" aria-live="polite"></p>
            <small>Una intervención · Transcripción configurada (local o remota). Wake se gestiona en Configuración.</small>
            <label class="voice-streaming-option"><input type="checkbox" data-streaming> Transcribir mientras hablo</label>
            <small data-streaming-hint>Opción de este perfil, también para la activación por voz. Puede enviar audio al proveedor STT mientras hablas. Si no admite directo o falla, se intenta la transcripción final habitual.</small>
            <aside class="voice-partial" hidden><small>TRANSCRIPCIÓN PROVISIONAL · aún no enviada</small><p data-partial></p></aside>
            <div><button type="button" data-confirm>Hablar ahora</button><button type="button" data-cancel>Cancelar</button></div>`;
        document.body.append(this.panel);
        this.message = this.panel.querySelector('p');
        this.confirmButton = this.panel.querySelector('[data-confirm]');
        this.cancelButton = this.panel.querySelector('[data-cancel]');
        this.streamingInput = this.panel.querySelector('[data-streaming]');
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
        document.addEventListener('keydown', (event) => {
            if (event.key === 'Escape' && !this.panel.hidden) void this.cancel();
        });
        document.addEventListener('visibilitychange', () => {
            if (document.hidden) void this.cancel();
        });
        window.addEventListener('pagehide', () => { void this.cancel(); });
        this.render();
    }

    get ownsCapture() { return this.state.client_id === this.clientId; }

    setConnected(connected) {
        this.connected = Boolean(connected);
        if (!connected) { this.clearPartial(); this.wakeCapture = ''; }
        if (connected) {
            this.state.revision = -1;
            void this.refresh();
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
        this.transcript = String(payload.text || '').trim();
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

    async saveStreaming(enabled) {
        if (!this.settingsReady || this.state.busy || this.settingsPending || this.pending) { this.render(); return; }
        this.settingsPending = true; this.failure = ''; this.render();
        try {
            const response = await this.fetch('/api/hermes-voice/settings', { method: 'POST',
                headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ streaming: !!enabled }) });
            const result = await response.json();
            if (!response.ok || typeof result.streaming !== 'boolean') throw Error(typeof result.detail === 'string' ? result.detail : 'No se pudo guardar la opción de transcripción.');
            this.streaming = result.streaming;
        } catch (error) { this.failure = error.message; await this.refresh(); }
        finally { this.settingsPending = false; this.render(); }
    }

    async refresh() {
        try {
            const response = await this.fetch('/api/hermes-voice/status', { cache: 'no-store' });
            if (response.ok) {
                const result = await response.json();
                if (result.settings) {
                    this.settingsReady = !result.settings.error;
                    this.streaming = result.settings.streaming === true;
                    if (result.settings.error) this.failure = result.settings.error;
                }
                this.apply(result);
            }
        } catch { /* No capture is started by a status read. */ }
    }

    toggle() {
        if (!this.connected || this.pending) return;
        if (this.state.busy) {
            if (this.ownsCapture && this.state.state === 'recording') void this.command('stop');
            return;
        }
        {
            this.confirming = true;
            this.render();
            this.confirmButton.focus();
        }
    }

    async start() {
        if (!this.connected || this.pending || this.settingsPending || this.state.busy || document.hidden) return;
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
                throw new Error(typeof result.detail === 'string' ? result.detail : 'No se pudo cambiar el micrófono.');
            }
        } catch (error) {
            this.failure = error.message || 'No pude confirmar el estado del micrófono.';
            this.notice(this.failure);
            // Never assume OFF or repeat an uncertain recording request.
            void this.refresh();
        } finally {
            this.requests -= 1;
            this.pending = this.requests > 0;
            this.render();
        }
    }

    async cancel() {
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

    render() {
        const themeRoot = document.querySelector('.hermes-workbench') || document.documentElement;
        const style = globalThis.getComputedStyle?.(themeRoot);
        const accent = style?.getPropertyValue('--companion-accent').trim()
            || style?.getPropertyValue('--iris-os-accent').trim();
        if (accent) {
            this.panel.style.setProperty('--voice-accent', accent);
            this.button.style.setProperty('--voice-accent', accent);
        }
        const { state, busy } = this.state;
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
        if (label) label.textContent = recording ? 'Enviar' : 'Hablar';
        this.confirmButton.hidden = !this.confirming;
        this.confirmButton.disabled = this.settingsPending || this.pending || !this.connected;
        this.streamingInput.checked = this.streaming;
        this.streamingInput.disabled = !this.settingsReady || busy || this.pending || this.settingsPending || !this.connected || !!this.wakeCapture;
        this.partialBox.hidden = !this.partial;
        this.partialMessage.textContent = this.partial;
        this.cancelButton.textContent = this.confirming || (own && busy && state !== 'processing') ? 'Cancelar' : 'Cerrar';
        const messages = {
            arming: 'Preparando el micrófono…', recording: 'Te escucho. Pulsa Enviar o termina de hablar; Escape cancela.',
            stopping: 'Terminando la captura…', transcribing: 'Transcribiendo…',
            processing: 'Enviando el texto a la conversación…', cancelling: 'Cancelando la grabación…',
        };
        this.message.textContent = this.confirming
            ? 'El micrófono se abrirá solo para esta intervención. El audio puede ir al proveedor STT configurado. ¿Comenzar?'
            : error || (busy && !own ? 'El micrófono está en uso en otra ventana.' : messages[state] || 'Micrófono apagado.');
        if (this.wakeCapture && this.partial && !error) this.message.textContent = 'Hey Hermes · te escucho. El texto puede cambiar hasta terminar.';
        if (this.transcript && !error && !this.confirming) {
            this.message.textContent = 'Texto enviado: «' + this.transcript + '»';
        }
        this.panel.hidden = document.hidden || !(this.confirming || busy || error || this.transcript || this.partial);
        this.onState(this.state);
    }
}
