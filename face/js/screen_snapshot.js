import { irisConfirm, irisImageConfirm } from './iris_dialog.js?v=9';
import { captureFrame } from './vision_snapshot.js?v=4';

const cancelled = () => Object.assign(new Error('cancelled'), { reason: 'cancelled' });
const stop = stream => stream?.getTracks?.().forEach(track => track.stop());

function abortable(promise, signal) {
    return new Promise((resolve, reject) => {
        const abort = () => reject(cancelled());
        if (signal.aborted) { reject(cancelled()); return; }
        signal.addEventListener('abort', abort, { once: true });
        Promise.resolve(promise).then(resolve, reject).finally(() => signal.removeEventListener('abort', abort));
    });
}

/** Screen selection is always initiated by a user click, never by a model tool. */
export class ScreenSnapshotRequests {
    constructor({ send, media = globalThis.navigator?.mediaDevices, confirm = irisConfirm,
        review = irisImageConfirm, capture = captureFrame, doc = globalThis.document } = {}) {
        Object.assign(this, { send, media, confirm, review, capture, doc });
        this.active = null;
        this.seen = new Set();
        doc?.defaultView?.addEventListener('pagehide', () => this.cancel());
    }

    cancel(id = '') {
        if (!id || this.active?.id === id) this.active?.controller.abort();
    }

    async handle(request) {
        if (!request?.request_id || !request.destination?.provider || !request.destination?.model) return;
        if (this.seen.has(request.request_id)) return;
        this.seen.add(request.request_id);
        if (this.seen.size > 128) this.seen.delete(this.seen.values().next().value);
        this.cancel();
        const state = { id: request.request_id, controller: new AbortController() };
        this.active = state;
        const { signal } = state.controller;
        const timeout = setTimeout(() => state.controller.abort(), Math.min(180000,
            Math.max(1000, Number(request.timeout_s || 180) * 1000)));
        let stream;
        const stopOnAbort = () => stop(stream);
        signal.addEventListener('abort', stopOnAbort, { once: true });
        const hidden = () => { if (this.doc?.visibilityState === 'hidden') state.controller.abort(); };
        this.doc?.addEventListener('visibilitychange', hidden);
        hidden();
        const reply = result => {
            if (!signal.aborted && this.active === state) this.send({ request_id: state.id, ...result });
        };
        try {
            if (!this.media?.getDisplayMedia) { reply({ status: 'unsupported' }); return; }
            const destination = `${request.destination.model} (${request.destination.provider})`;
            // Iris OS writes this text; the model only chose to ask (core.visual_requests).
            const byIris = request.requested_by === 'iris';
            const review = !byIris || request.review !== false;
            const next = review
                ? `Después podrás revisar la captura antes de enviarla a ${destination}.`
                : `La captura se enviará a ${destination} sin revisión, porque lo permitiste en Permisos.`;
            const accepted = await abortable(this.confirm({
                title: byIris ? 'Iris pide ver tu pantalla' : 'Compartir una captura de pantalla', icon: 'monitor',
                message: byIris
                    ? `Iris decidió que necesita ver tu pantalla para responder a «${String(request.question || '')}». Elige una ventana o pantalla. ${next} Compartirla no autoriza controlar el equipo.`
                    : `Elige una ventana o pantalla. ${next} Compartirla no autoriza controlar el equipo.`,
                confirmLabel: 'Elegir ventana o pantalla', cancelLabel: 'Cancelar', signal }), signal);
            if (!accepted || signal.aborted) { reply({ status: 'cancelled' }); return; }
            // The confirmation click supplies the transient activation required by the OS picker.
            const pending = this.media.getDisplayMedia({ video: true, audio: false });
            Promise.resolve(pending).then(late => { if (signal.aborted) stop(late); }, () => {});
            stream = await abortable(pending, signal);
            let frame;
            try {
                frame = await abortable(this.capture(stream, { signal, maxSide: 1920, quality: 0.9 }), signal);
            } finally {
                stop(stream); stream = null; // No stream remains active during review or analysis.
            }
            if (signal.aborted) return;
            if (!review) { reply({ status: 'ok', confirmed: true, image: frame.image }); return; }
            const approved = await abortable(this.review({ title: 'Revisar captura antes de enviar',
                icon: 'monitor', image: frame.image,
                message: `Esta es la única imagen que se enviará a ${destination}. Si hay información privada, cancela y elige otra ventana. Iris no guarda la captura como archivo.`,
                confirmLabel: 'Enviar esta captura', cancelLabel: 'Cancelar', signal }), signal);
            if (approved) reply({ status: 'ok', confirmed: true, image: frame.image });
            else reply({ status: 'cancelled' });
        } catch (error) {
            reply({ status: error.reason === 'cancelled' || error.name === 'NotAllowedError'
                ? 'cancelled' : 'unavailable' });
        } finally {
            stop(stream);
            clearTimeout(timeout);
            signal.removeEventListener('abort', stopOnAbort);
            this.doc?.removeEventListener('visibilitychange', hidden);
            if (this.active === state) this.active = null;
        }
    }
}
