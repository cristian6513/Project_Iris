/**
 * IRIS WebRTC Host — Runs on the PC browser
 *
 * Partners with webrtc_remote.js running on the phone:
 *   - Receives camera + mic tracks FROM the phone
 *   - Sends avatar canvas video + TTS audio TO the phone
 *
 * Signaling is done through the existing WebSocket at /ws.
 *
 * Important changes vs. the original prototype:
 *   • Adds the canvas track BEFORE creating the SDP offer (the old
 *     code added it on `connectionstatechange`, which fires AFTER
 *     negotiation — so the offer went out with no media at all and
 *     the phone never saw the avatar).
 *   • Uses `ws.addListener` instead of overwriting `ws.onMessage` —
 *     keeps the main app handlers alive while WebRTC is active.
 *   • Waits for the phone's `rtc.init` before creating the offer, so
 *     the handshake order is deterministic regardless of which side
 *     opens its tab first.
 *   • Exposes `setOutputAudioEnabled` (mute / un-mute the avatar's
 *     audio going to the phone) and `stop()`.
 *   • Attaches the class to `window.HostClient` for the lazy
 *     `await import(...)` pattern in panel.js.
 */

const DEFAULT_ICE_SERVERS = [
    { urls: 'stun:stun.l.google.com:19302' },
    { urls: 'stun:stun1.l.google.com:19302' },
];

export class HostClient {
    constructor(ws, opts = {}) {
        /** @type {WebSocketManager} */
        this.ws = ws;
        /** @type {HTMLCanvasElement|null} */
        this.canvas = opts.canvas || null;
        /**
         * Optional MediaStream of the TTS / mixer output to forward
         * to the phone. If omitted the host only sends video and
         * relies on the phone's local browser audio (which it won't
         * have — the page is muted on mobile until tapped). Caller
         * can plug an AudioContext destination here.
         * @type {MediaStream|null}
         */
        this.audioStream = opts.audioStream || null;
        /** @type {RTCPeerConnection|null} */
        this.pc = null;
        /** @type {MediaStream|null} */
        this.canvasStream = null;

        this._iceServers = DEFAULT_ICE_SERVERS;
        this._connected = false;
        this._listening = false;
        this._sentOffer = false;
        this._wsUnsub = null;
        this._fps = opts.fps || 30;

        // Hooks consumers can wire to.
        this.onStateChange = null;
        this.onRemoteStream = null;
    }

    get connected() { return this._connected; }

    /**
     * Begin listening for a phone to connect. Idempotent — calling
     * twice does nothing on the second call.
     */
    async startListening() {
        if (this._listening) return;
        this._listening = true;

        try {
            this.pc = new RTCPeerConnection({ iceServers: this._iceServers });

            // 1. Attach canvas video BEFORE the offer is created, so
            //    the SDP advertises a video m=section. Adding tracks
            //    after `setLocalDescription` is too late — the remote
            //    would have no slot to receive.
            this._attachCanvasTrack();
            this._attachAudioTrack();

            // 2. Forward any incoming remote tracks (phone camera / mic).
            this.pc.ontrack = (event) => {
                const stream = event.streams?.[0];
                if (!stream) return;
                console.log('[webrtc-host] Received remote track:', stream.getTracks().map(t => t.kind));
                try { this.onRemoteStream?.(stream); } catch { /* ignore */ }
                window.dispatchEvent(new CustomEvent('IRIS:remote-stream', {
                    detail: { stream },
                }));
            };

            this.pc.onicecandidate = (event) => {
                if (event.candidate) {
                    this.ws.send('rtc.ice-candidate', {
                        candidate: event.candidate.candidate,
                        sdpMid: event.candidate.sdpMid,
                        sdpMLineIndex: event.candidate.sdpMLineIndex,
                    });
                }
            };

            this.pc.onconnectionstatechange = () => {
                const state = this.pc?.connectionState;
                console.log('[webrtc-host] Connection state:', state);
                this._connected = state === 'connected';
                try { this.onStateChange?.(state); } catch { /* ignore */ }
                if (this._connected) {
                    this._sendStreamReady();
                } else if (state === 'failed' || state === 'closed' || state === 'disconnected') {
                    window.dispatchEvent(new CustomEvent('IRIS:host-disconnected'));
                }
            };

            // 3. Subscribe (don't overwrite onMessage!).
            this._wsUnsub = this.ws.addListener((type, payload) => this._handleWS(type, payload));

            // 4. Announce ourselves. If the phone is already waiting,
            //    its 'rtc.init' will arrive shortly and trigger the
            //    offer. If we connect first, the phone's later
            //    'rtc.init' triggers it then.
            this.ws.send('rtc.init', { role: 'host', hasAudio: !!this.audioStream, hasVideo: !!this.canvas });
        } catch (err) {
            console.error('[webrtc-host] startListening failed:', err);
            this._listening = false;
            throw err;
        }
    }

    /**
     * Create and send an offer. Called when the phone signals
     * `rtc.init` (or manually for a fresh re-offer after media
     * changes). De-duplicated via `_sentOffer`.
     */
    async createAndSendOffer({ force = false } = {}) {
        if (!this.pc) return;
        if (this._sentOffer && !force) return;
        try {
            const offer = await this.pc.createOffer({
                offerToReceiveAudio: true,
                offerToReceiveVideo: true,
            });
            await this.pc.setLocalDescription(offer);
            this.ws.send('rtc.offer', { sdp: this.pc.localDescription.sdp });
            this._sentOffer = true;
        } catch (err) {
            console.error('[webrtc-host] createAndSendOffer failed:', err);
        }
    }

    /** Set the TTS / system audio stream after construction. */
    setAudioStream(stream) {
        this.audioStream = stream || null;
        this._attachAudioTrack();
    }

    /** Mute / un-mute the audio going to the phone. */
    setOutputAudioEnabled(enabled) {
        const tracks = this.audioStream?.getAudioTracks() || [];
        for (const t of tracks) t.enabled = !!enabled;
    }

    isOutputAudioEnabled() {
        const t = this.audioStream?.getAudioTracks()[0];
        return !!t && t.enabled;
    }

    stop() {
        try { this.ws.send('rtc.stop', {}); } catch { /* ignore */ }
        this._cleanup();
    }

    // ── WS signaling ─────────────────────────────────────────────

    _handleWS(type, payload) {
        switch (type) {
            case 'rtc.answer':
                this._handleAnswer(payload.sdp);
                break;
            case 'rtc.init':
                if (payload?.role === 'remote') {
                    console.log('[webrtc-host] Remote announced — sending offer');
                    this.createAndSendOffer();
                }
                break;
            case 'rtc.ice-candidate':
                if (this.pc && payload.candidate) {
                    this.pc.addIceCandidate(new RTCIceCandidate({
                        candidate: payload.candidate,
                        sdpMid: payload.sdpMid,
                        sdpMLineIndex: payload.sdpMLineIndex,
                    })).catch(err => console.warn('[webrtc-host] ICE add failed:', err));
                }
                break;
            case 'rtc.stop':
            case 'rtc.close':
                this._cleanup();
                break;
        }
    }

    async _handleAnswer(sdp) {
        if (!this.pc) return;
        try {
            await this.pc.setRemoteDescription(new RTCSessionDescription({ type: 'answer', sdp }));
        } catch (err) {
            console.error('[webrtc-host] _handleAnswer failed:', err);
        }
    }

    // ── Track plumbing ───────────────────────────────────────────

    _attachCanvasTrack() {
        if (!this.canvas || !this.pc) return;
        try {
            this.canvasStream = this.canvas.captureStream(this._fps);
            for (const track of this.canvasStream.getVideoTracks()) {
                this.pc.addTrack(track, this.canvasStream);
            }
        } catch (err) {
            console.warn('[webrtc-host] canvas capture failed:', err);
        }
    }

    _attachAudioTrack() {
        if (!this.audioStream || !this.pc) return;
        // Replace if a sender already exists; add fresh otherwise.
        const audioTrack = this.audioStream.getAudioTracks()[0];
        if (!audioTrack) return;
        const sender = this.pc.getSenders().find(s => s.track?.kind === 'audio');
        if (sender) {
            sender.replaceTrack(audioTrack).catch(err =>
                console.warn('[webrtc-host] replace audio failed:', err));
        } else {
            this.pc.addTrack(audioTrack, this.audioStream);
        }
    }

    _sendStreamReady() {
        this.ws.send('rtc.stream_ready', {});
    }

    _cleanup() {
        if (this._wsUnsub) {
            try { this._wsUnsub(); } catch { /* ignore */ }
            this._wsUnsub = null;
        }
        if (this.canvasStream) {
            this.canvasStream.getTracks().forEach(t => t.stop());
            this.canvasStream = null;
        }
        if (this.pc) {
            try { this.pc.close(); } catch { /* ignore */ }
            this.pc = null;
        }
        this._connected = false;
        this._listening = false;
        this._sentOffer = false;
        try { this.onStateChange?.('closed'); } catch { /* ignore */ }
        window.dispatchEvent(new CustomEvent('IRIS:host-disconnected'));
    }
}

if (typeof window !== 'undefined') {
    window.HostClient = HostClient;
}

