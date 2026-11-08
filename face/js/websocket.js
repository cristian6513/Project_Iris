/**
 * IRIS WebSocket Client — Full Implementation
 *
 * - Auto-detects backend URL
 * - Reconnects with exponential backoff (up to 30 s)
 * - Keep-alive ping every 15 s
 * - Dispatches typed events to the app layer
 */

export class WebSocketManager {
    /**
     * @param {string|null} url  Override URL. Auto-detected if null.
     */
    constructor(url = null, protocols = []) {
        const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
        const host  = location.hostname || 'localhost';
        const port  = location.port || '8765';
        this.url = url || `${proto}//${host}:${port}/ws`;
        this.protocols = Array.isArray(protocols) ? protocols.filter(Boolean) : [];

        /** @type {WebSocket|null} */
        this.ws = null;

        /**
         * Primary message callback (the main app handler). Set this
         * before calling connect().
         *
         * NOTE: Secondary consumers (WebRTC remote/host, future plugins…)
         * MUST NOT reassign this — they would clobber the main app and
         * silently break speech / animations / brain panel. Use
         * `addListener(fn)` / `removeListener(fn)` instead, which fan
         * out alongside `onMessage`.
         *
         * @type {(type:string, payload:object)=>void|null}
         */
        this.onMessage = null;

        /** @type {Set<(type:string, payload:object)=>void>} */
        this._listeners = new Set();

        /**
         * Connection-change callback (main app).
         * @type {(connected:boolean)=>void|null}
         */
        this.onConnectionChange = null;

        this._reconnectDelay = 1000;
        this._maxReconnectDelay = 30_000;
        this._reconnectTimer = null;
        this._pingInterval = null;
        this._shouldReconnect = true;
        this._connected = false;
    }

    get connected() { return this._connected; }

    connect() {
        this._shouldReconnect = true;
        if (this.ws && (this.ws.readyState === WebSocket.CONNECTING || this.ws.readyState === WebSocket.OPEN)) {
            return;
        }
        this._clearReconnectTimer();
        try {
            const socket = this.protocols.length
                ? new WebSocket(this.url, this.protocols)
                : new WebSocket(this.url);
            this.ws = socket;

            socket.onopen = () => {
                if (this.ws !== socket) return;
                console.log('🔗 Connected to IRIS core');
                this._connected = true;
                this._reconnectDelay = 1000; // reset backoff
                this._startPing();
                this._updateUI(true);
                this._fanoutConn(true);
            };

            socket.onmessage = ({ data }) => {
                if (this.ws !== socket) return;
                try {
                    const msg = JSON.parse(data);
                    this._fanoutMessage(msg.type, msg.payload || {});
                } catch {
                    console.warn('Bad WS message:', data.slice(0, 120));
                }
            };

            socket.onclose = () => {
                if (this.ws !== socket) return;
                this.ws = null;
                console.log('🔌 Disconnected');
                this._connected = false;
                this._stopPing();
                this._updateUI(false);
                this._fanoutConn(false);
                if (this._shouldReconnect) this._scheduleReconnect();
            };

            socket.onerror = () => {
                // onclose fires right after — handled there
            };
        } catch {
            if (this._shouldReconnect) this._scheduleReconnect();
        }
    }

    /**
     * Send a typed message to the backend.
     * @param {string} type
     * @param {object} payload
     */
    send(type, payload = {}) {
        if (this.ws?.readyState === WebSocket.OPEN) {
            try {
                this.ws.send(JSON.stringify({ type, payload }));
            } catch (error) {
                console.warn('[ws] send failed:', error);
                return false;
            }
            return true;
        }
        return false;
    }

    disconnect() {
        this._shouldReconnect = false;
        this._clearReconnectTimer();
        this._stopPing();
        const socket = this.ws;
        const wasConnected = this._connected;
        this.ws = null;
        this._connected = false;
        socket?.close();
        if (wasConnected) {
            this._updateUI(false);
            this._fanoutConn(false);
        }
    }

    /**
     * Register a secondary message listener. Called alongside the
     * primary `onMessage` for every WS message. Returns an
     * unsubscribe function — call it (or `removeListener(fn)`) when
     * the consumer is torn down.
     */
    addListener(fn) {
        if (typeof fn === 'function') this._listeners.add(fn);
        return () => this.removeListener(fn);
    }

    removeListener(fn) { this._listeners.delete(fn); }

    // ── Internal ──

    _fanoutMessage(type, payload) {
        try { this.onMessage?.(type, payload); }
        catch (e) { console.warn('[ws] primary onMessage threw:', e); }
        for (const fn of this._listeners) {
            try { fn(type, payload); }
            catch (e) { console.warn('[ws] listener threw:', e); }
        }
    }

    _fanoutConn(connected) {
        try { this.onConnectionChange?.(connected); }
        catch (e) { console.warn('[ws] primary onConnectionChange threw:', e); }
    }

    _scheduleReconnect() {
        if (!this._shouldReconnect || this._reconnectTimer !== null) return;
        const delay = this._reconnectDelay;
        console.log(`Reconnecting in ${(delay / 1000).toFixed(1)}s…`);
        this._reconnectTimer = setTimeout(() => {
            this._reconnectTimer = null;
            if (!this._shouldReconnect) return;
            this._reconnectDelay = Math.min(this._reconnectDelay * 1.5, this._maxReconnectDelay);
            this.connect();
        }, delay);
    }

    _clearReconnectTimer() {
        if (this._reconnectTimer !== null) {
            clearTimeout(this._reconnectTimer);
            this._reconnectTimer = null;
        }
    }

    _startPing() {
        this._stopPing();
        this._pingInterval = setInterval(() => this.send('ping'), 15_000);
    }

    _stopPing() {
        if (this._pingInterval) {
            clearInterval(this._pingInterval);
            this._pingInterval = null;
        }
    }

    _updateUI(connected) {
        const dot = document.querySelector('.status-dot');
        const txt = document.querySelector('.status-text');
        if (dot) dot.className = `status-dot ${connected ? 'connected' : ''}`;
        if (txt) txt.textContent = connected ? 'Connected' : 'Reconnecting…';
    }
}
