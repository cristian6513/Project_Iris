import * as THREE from 'three';

const LABELS = { idle: 'EN ESPERA', loading: 'CARGANDO', playing: 'REPRODUCIENDO', paused: 'EN PAUSA', error: 'ERROR DE AUDIO' };
const cleanText = value => typeof value === 'string'
    ? value.replace(/[\x00-\x1f\x7f]/g, ' ').trim().slice(0, 96) : '';

/** Passive room furniture. Receives text/state only, never media URLs or audio. */
export class RoomMediaDisplay {
    constructor(screen, indicators = []) {
        this.indicators = indicators;
        this.state = { status: 'idle', muted: false, title: '', artist: '' };
        this._motionQuery = globalThis.matchMedia?.('(prefers-reduced-motion: reduce)');
        this.canvas = document.createElement('canvas');
        this.canvas.width = 768;
        this.canvas.height = 480;
        this.ctx = this.canvas.getContext('2d');
        this.texture = new THREE.CanvasTexture(this.canvas);
        this.texture.colorSpace = THREE.SRGBColorSpace;
        this.texture.minFilter = THREE.LinearFilter;
        this.texture.generateMipmaps = false;
        screen.material.map = this.texture;
        screen.material.toneMapped = false;
        screen.material.needsUpdate = true;
    }

    setState(snapshot = {}) {
        const status = Object.hasOwn(LABELS, snapshot?.status) ? snapshot.status : 'idle';
        const next = {
            status, muted: snapshot?.muted === true,
            title: status === 'idle' ? '' : cleanText(snapshot?.title),
            artist: status === 'idle' ? '' : cleanText(snapshot?.artist),
        };
        if (JSON.stringify(next) === JSON.stringify(this.state)) return;
        this.state = next;
        this.draw();
        this.update(0, false);
    }

    setTheme(theme) {
        this.theme = theme;
        this.draw();
    }

    draw() {
        const c = this.ctx, theme = this.theme;
        if (!c || !theme) return;
        const hex = n => '#' + n.toString(16).padStart(6, '0');
        const { status, muted, title, artist } = this.state;
        const accent = hex(status === 'error' ? theme.amber : theme.cyan);
        c.fillStyle = hex(theme.ambient); c.fillRect(0, 0, 768, 480);
        c.strokeStyle = hex(theme.wallPanel); c.lineWidth = 3;
        c.strokeRect(14, 14, 740, 452);
        c.fillStyle = accent; c.fillRect(40, 44, 6, 38);
        c.font = 'bold 30px monospace'; c.fillText('IRIS PLAYER', 64, 73);
        c.fillStyle = hex(theme.linen); c.font = '18px monospace';
        c.fillText('LOCAL / EN ESTE NAVEGADOR', 42, 111);
        c.fillStyle = accent; c.font = 'bold 22px monospace';
        c.fillText(status === 'playing' && muted ? 'REPRODUCIENDO / SILENCIO' : LABELS[status], 42, 184);
        const fit = (text, maxWidth) => {
            if (c.measureText(text).width <= maxWidth) return text;
            while (text.length && c.measureText(text + '…').width > maxWidth) text = text.slice(0, -1);
            return text + '…';
        };
        c.fillStyle = '#edf4f6'; c.font = 'bold 34px monospace';
        c.fillText(fit(title || (status === 'idle' ? 'Tu música, tu espacio.' : 'IRIS Player'), 680), 42, 258);
        c.fillStyle = hex(theme.linen); c.font = '23px monospace';
        c.fillText(fit(artist || (status === 'idle' ? 'Abre Player para elegir una canción.' : 'Controles disponibles en IRIS OS'), 680), 42, 304);
        c.strokeStyle = hex(theme.wallPanel); c.beginPath(); c.moveTo(42, 350); c.lineTo(726, 350); c.stroke();
        // Static state marks, not a fabricated spectrum or an audio analyser.
        c.fillStyle = accent;
        for (let i = 0; i < 7; i++) {
            const h = status === 'playing' && !muted ? 12 + (3 - Math.abs(3 - i)) * 9 : 6;
            c.fillRect(44 + i * 14, 419 - h, 6, h);
        }
        c.font = '18px monospace'; c.fillStyle = hex(theme.linen);
        c.fillText('ESTADO DEL REPRODUCTOR', 176, 417);
        this.texture.needsUpdate = true;
    }

    update(elapsed, allowMotion = true) {
        const active = this.state.status === 'playing' && !this.state.muted;
        const motion = active && allowMotion && !this._motionQuery?.matches;
        const time = Number.isFinite(elapsed) ? elapsed : 0;
        for (const light of this.indicators) {
            light.material.opacity = active ? (motion ? 0.55 + Math.sin(time * 1.5) * 0.12 : 0.55)
                : this.state.status === 'error' ? 0.7 : 0.2;
        }
    }
}
