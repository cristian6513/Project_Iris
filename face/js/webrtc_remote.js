/**
 * IRIS WebRTC Remote Client — Runs on the phone
 *
 * Establishes a P2P WebRTC connection to the PC host and:
 *   - Sends camera + microphone tracks TO the PC
 *   - Receives avatar video + TTS audio FROM the PC
 *
 * Signaling is done through the existing WebSocket at /ws.
 *
 * Important changes vs. the original prototype:
 *   • Uses `ws.addListener` instead of overwriting `ws.onMessage`,
 *     so the main app's message handler keeps working (speech,
 *     animation, brain panel, etc).
 *   • Camera and mic can be toggled on / off independently after
 *     connecting (`setCameraEnabled`, `setMicEnabled`). Toggling
 *     disables the track instead of renegotiating SDP — instant and
 *     reversible.
 *   • Front / back camera switch via `switchCamera()` (mobile).
 *   • Module attaches the class to `window.RemoteClient` so the
 *     lazy `await import(...)` pattern in panel.js works without
 *     juggling the module's namespace.
 *
 * Usage:
 *   import { RemoteClient } from './webrtc_remote.js';
 *   const client = new RemoteClient(wsManager);
 *   await client.connect({ video: true, audio: true });
 *   // …
 *   client.setCameraEnabled(false);   // pause cam, keep mic
 *   client.stop();                    // tear everything down
 */

const DEFAULT_ICE_SERVERS = [
    { urls: 'stun:stun.l.google.com:19302' },
    { urls: 'stun:stun1.l.google.com:19302' },
];

export class RemoteClient {
    constructor(ws) {
        /** @type {WebSocketManager} */
        this.ws = ws;
        /** @type {RTCPeerConnection|null} */
        this.pc = null;
        /** @type {MediaStream|null} */
        this.localStream = null;

        this._iceServers = DEFAULT_ICE_SERVERS;
        this._connected = false;
        this._facingMode = 'user';

        // Unsubscribe handle returned by ws.addListener — used to
        // detach cleanly when the client is stopped.
        this._wsUnsub = null;

        // ondisconnect / onstatechange hooks consumers can set.
        this.onStateChange = null;
    }

    get connected() { return this._connected; }
    get hasVideo()  { return !!this.localStream?.getVideoTracks().length; }
    get hasAudio()  { return !!this.localStream?.getAudioTracks().length; }

    /**
     * Start camera + mic, open the peer connection and wait for the
     * host's offer.
     *
     * @param {object} opts
     * @param {boolean} [opts.video=true]   Capture and send camera.
     * @param {boolean} [opts.audio=true]   Capture and send microphone.
     * @param {'user'|'environment'} [opts.facingMode='user']
     */
    async connect(opts = {}) {
        const {
            video = true,
            audio = true,
            facingMode = 'user',
        } = opts;

        this._facingMode = facingMode;

        // 1. Capture local media. We ask for whatever the caller wants
        //    enabled; missing pieces are added later via `setCameraEnabled`
        //    / `setMicEnabled` (those re-call getUserMedia for the
        //    track that wasn't acquired up-front).

        // Secure-context guard. Browsers expose `navigator.mediaDevices`
        // ONLY over HTTPS or on localhost. A phone hitting IRIS at
        // `http://<LAN-IP>:8000` will see it as `undefined` and calling
        // `.getUserMedia` would explode with the cryptic "Cannot read
        // properties of undefined (reading 'getUserMedia')". Surface a
        // clear message instead.
        if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
            throw new Error(
                'Tu navegador no permite cámara/micrófono porque la página no es segura. ' +
                'Abrí IRIS por HTTPS o desde localhost. Para usar el celular por LAN, ' +
                'serví IRIS con un certificado (mkcert) o un túnel HTTPS (ngrok/cloudflared).'
            );
        }

        const constraints = {
            video: video ? this._videoConstraints(facingMode) : false,
            audio: audio ? this._audioConstraints() : false,
        };
        try {
            this.localStream = await navigator.mediaDevices.getUserMedia(constraints);
        } catch (err) {
            // Friendlier failures so the panel can show them verbatim.
            const reason = err?.name === 'NotAllowedError'
                ? 'Permiso de cámara/micrófono denegado en este navegador.'
                : err?.name === 'NotFoundError'
                ? 'No se encontró cámara o micrófono en este dispositivo.'
                : (err?.message || String(err));
            const wrapped = new Error(reason);
            wrapped.cause = err;
            throw wrapped;
        }

        // 2. Peer connection.
        this.pc = new RTCPeerConnection({ iceServers: this._iceServers });

        for (const track of this.localStream.getTracks()) {
            this.pc.addTrack(track, this.localStream);
        }

        this.pc.ontrack = (event) => {
            // Phone receives BOTH a video track (avatar canvas) and an
            // audio track (TTS). They may arrive on different streams
            // depending on how the host bundles them — handle each.
            for (const stream of event.streams || []) {
                if (stream.getVideoTracks().length)  this._attachRemoteVideo(stream);
                if (stream.getAudioTracks().length)  this._attachRemoteAudio(stream);
            }
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
            console.log('[webrtc-remote] Connection state:', state);
            this._connected = state === 'connected';
            try { this.onStateChange?.(state); } catch (e) { /* ignore */ }
            if (this._connected) {
                window.dispatchEvent(new CustomEvent('IRIS:remote-connected'));
            } else if (state === 'failed' || state === 'closed' || state === 'disconnected') {
                window.dispatchEvent(new CustomEvent('IRIS:remote-disconnected'));
            }
        };

        // 3. Subscribe to WS signaling — DO NOT overwrite onMessage.
        this._wsUnsub = this.ws.addListener((type, payload) => this._handleWS(type, payload));

        // 4. Tell the host we're ready. Host will respond with an offer.
        this.ws.send('rtc.init', {
            role: 'remote',
            hasAudio: this.hasAudio,
            hasVideo: this.hasVideo,
        });
    }

    // ── Track toggles ────────────────────────────────────────────

    /**
     * Enable / disable the outgoing camera. If the camera was not
     * acquired at `connect()` time, this acquires it now and
     * renegotiates by replacing the sender's track.
     */
    async setCameraEnabled(enabled) {
        const tracks = this.localStream?.getVideoTracks() || [];
        if (tracks.length) {
            // Cheap path: just flip `enabled`. No renegotiation needed.
            for (const t of tracks) t.enabled = !!enabled;
            return;
        }
        if (!enabled) return; // nothing to do
        // Slow path: acquire a video track now and attach it.
        await this._addTrackOnTheFly('video');
    }

    /** Same idea for the microphone. */
    async setMicEnabled(enabled) {
        const tracks = this.localStream?.getAudioTracks() || [];
        if (tracks.length) {
            for (const t of tracks) t.enabled = !!enabled;
            return;
        }
        if (!enabled) return;
        await this._addTrackOnTheFly('audio');
    }

    isCameraEnabled() {
        const t = this.localStream?.getVideoTracks()[0];
        return !!t && t.enabled && t.readyState === 'live';
    }

    isMicEnabled() {
        const t = this.localStream?.getAudioTracks()[0];
        return !!t && t.enabled && t.readyState === 'live';
    }

    /** Toggle front / back camera (mobile). Returns the new facingMode. */
    async switchCamera() {
        const oldTrack = this.localStream?.getVideoTracks()[0];
        if (!oldTrack) return this._facingMode;

        const nextMode = this._facingMode === 'user' ? 'environment' : 'user';
        try {
            const newStream = await navigator.mediaDevices.getUserMedia({
                video: this._videoConstraints(nextMode),
                audio: false,
            });
            const newTrack = newStream.getVideoTracks()[0];
            // Replace on the sender so the SDP doesn't need to renegotiate.
            const sender = this.pc?.getSenders().find(s => s.track?.kind === 'video');
            if (sender) await sender.replaceTrack(newTrack);
            // Swap in localStream so toggles & cleanup target the new track.
            this.localStream.removeTrack(oldTrack);
            oldTrack.stop();
            this.localStream.addTrack(newTrack);
            this._facingMode = nextMode;
        } catch (err) {
            console.warn('[webrtc-remote] switchCamera failed:', err);
        }
        return this._facingMode;
    }

    // ── Lifecycle ────────────────────────────────────────────────

    stop() {
        // Tell the host first so it can clean up before our WS notice
        // races the page close.
        try { this.ws.send('rtc.stop', {}); } catch { /* ignore */ }
        this._cleanup();
    }

    // ── WS signaling ─────────────────────────────────────────────

    _handleWS(type, payload) {
        switch (type) {
            case 'rtc.offer':
                this._handleOffer(payload.sdp);
                break;
            case 'rtc.ice-candidate':
                if (this.pc && payload.candidate) {
                    this.pc.addIceCandidate(new RTCIceCandidate({
                        candidate: payload.candidate,
                        sdpMid: payload.sdpMid,
                        sdpMLineIndex: payload.sdpMLineIndex,
                    })).catch(err => console.warn('[webrtc-remote] ICE add failed:', err));
                }
                break;
            case 'rtc.stream_ready':
                console.log('[webrtc-remote] Host ready for streams');
                break;
            case 'rtc.close':
            case 'rtc.stop':
                this._cleanup();
                break;
        }
    }

    async _handleOffer(sdp) {
        if (!this.pc) return;
        try {
            await this.pc.setRemoteDescription(new RTCSessionDescription({ type: 'offer', sdp }));
            const answer = await this.pc.createAnswer();
            await this.pc.setLocalDescription(answer);
            this.ws.send('rtc.answer', { sdp: this.pc.localDescription.sdp });
        } catch (err) {
            console.error('[webrtc-remote] _handleOffer failed:', err);
        }
    }

    // ── Helpers ──────────────────────────────────────────────────

    _videoConstraints(facingMode) {
        return {
            width: { ideal: 640 },
            height: { ideal: 480 },
            frameRate: { ideal: 30, max: 30 },
            facingMode: { ideal: facingMode },
        };
    }

    _audioConstraints() {
        return {
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true,
        };
    }

    async _addTrackOnTheFly(kind) {
        try {
            const constraints = kind === 'video'
                ? { video: this._videoConstraints(this._facingMode), audio: false }
                : { audio: this._audioConstraints(), video: false };
            const stream = await navigator.mediaDevices.getUserMedia(constraints);
            const track = stream.getTracks()[0];
            if (!track) return;
            // Add to localStream so future toggles see it.
            this.localStream ??= new MediaStream();
            this.localStream.addTrack(track);
            // Find an existing sender of this kind (created from an
            // m=section the host has already negotiated) and replace
            // its track. If none exists we add a fresh one — the
            // browser will mark the PC as renegotiation-needed.
            const sender = this.pc?.getSenders().find(
                s => s.track === null || s.track?.kind === kind
            );
            if (sender && (sender.track === null || sender.track?.kind === kind)) {
                await sender.replaceTrack(track);
            } else {
                this.pc?.addTrack(track, this.localStream);
            }
        } catch (err) {
            console.warn(`[webrtc-remote] failed to add ${kind} on the fly:`, err);
        }
    }

    _attachRemoteVideo(stream) {
        let video = document.getElementById('remote-video');
        if (!video) {
            video = document.createElement('video');
            video.id = 'remote-video';
            video.autoplay = true;
            video.playsInline = true;
            video.muted = false;
            video.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;object-fit:cover;z-index:-1;';
            document.body.appendChild(video);
        }
        if (video.srcObject !== stream) video.srcObject = stream;
    }

    _attachRemoteAudio(stream) {
        let audio = document.getElementById('remote-audio');
        if (!audio) {
            audio = document.createElement('audio');
            audio.id = 'remote-audio';
            audio.autoplay = true;
            audio.style.cssText = 'display:none';
            document.body.appendChild(audio);
        }
        if (audio.srcObject !== stream) audio.srcObject = stream;
    }

    _cleanup() {
        if (this._wsUnsub) {
            try { this._wsUnsub(); } catch { /* ignore */ }
            this._wsUnsub = null;
        }
        if (this.localStream) {
            this.localStream.getTracks().forEach(t => t.stop());
            this.localStream = null;
        }
        if (this.pc) {
            try { this.pc.close(); } catch { /* ignore */ }
            this.pc = null;
        }
        document.getElementById('remote-video')?.remove();
        document.getElementById('remote-audio')?.remove();
        this._connected = false;
        try { this.onStateChange?.('closed'); } catch { /* ignore */ }
        window.dispatchEvent(new CustomEvent('IRIS:remote-disconnected'));
    }
}

// Expose for the lazy `await import(...)` pattern in panel.js: the
// panel checks `window.RemoteClient` after the import resolves.
if (typeof window !== 'undefined') {
    window.RemoteClient = RemoteClient;
}
