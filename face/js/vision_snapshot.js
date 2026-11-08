/**
 * F03b: the photo Core asks this surface for when the user asks Iris to look
 * ("¿qué tengo en la mano?"). One request, one fresh frame, one answer.
 *
 * The request names its destination (the profile's vision model). When it
 * needs a confirmation, nothing happens until the user clicks "Tomar foto";
 * otherwise a visible indicator shows that a photo is being taken, with
 * Cancel. The camera is reused through the shared consumers (panel.js): a
 * camera that was off is opened only for this photo and released after it.
 * The frame is taken after the request, from a live and ready stream, scaled
 * to at most 1280 px, and sent once; nothing is stored here.
 */

import { irisConsent } from './iris_dialog.js?v=9';

const MAX_SIDE = 1280;
const JPEG_QUALITY = 0.85;
export const SNAPSHOT_CONSUMER = 'snapshot';

/** Each request opens the camera under its own name: releasing one photo never
 * turns off the camera another photo is still opening (Codex review, R07). */
export function snapshotConsumer(requestId) {
    return `${SNAPSHOT_CONSUMER}:${requestId}`;
}

function destinationLabel(destination = {}) {
    const model = String(destination.model || '').trim();
    const provider = String(destination.provider || '').trim();
    return model && provider ? `${model} (${provider})` : model || provider || 'tu modelo de visión';
}

/** One frame painted after this call, from a live stream; rejects with a reason code. */
export async function captureFrame(stream, {
    doc = globalThis.document,
    signal = null,
    timeoutMs = 8000,
    maxSide = MAX_SIDE,
    quality = JPEG_QUALITY,
} = {}) {
    const track = stream?.getVideoTracks?.()[0];
    if (!track || track.readyState !== 'live') throw Object.assign(new Error('no live camera'), { reason: 'unavailable' });
    const video = doc.createElement('video');
    video.muted = true;
    video.playsInline = true;
    video.srcObject = stream;
    const cleanup = () => {
        video.pause?.();
        video.srcObject = null;
    };
    try {
        await new Promise((resolve, reject) => {
            const timer = setTimeout(() => reject(Object.assign(new Error('timeout'), { reason: 'unavailable' })), timeoutMs);
            const abort = () => {
                clearTimeout(timer);
                reject(Object.assign(new Error('cancelled'), { reason: 'cancelled' }));
            };
            if (signal?.aborted) return abort();
            signal?.addEventListener?.('abort', abort, { once: true });
            const frame = () => {
                // A frame presented after this request: never a stale one.
                if (typeof video.requestVideoFrameCallback === 'function') {
                    video.requestVideoFrameCallback(() => {
                        clearTimeout(timer);
                        resolve();
                    });
                } else {
                    setTimeout(() => {
                        clearTimeout(timer);
                        resolve();
                    }, 150);
                }
            };
            video.addEventListener('playing', frame, { once: true });
            Promise.resolve(video.play?.()).catch(() => {});
        });
        const width = video.videoWidth;
        const height = video.videoHeight;
        if (!width || !height) throw Object.assign(new Error('no frame'), { reason: 'unavailable' });
        const scale = Math.min(1, maxSide / Math.max(width, height));
        const canvas = doc.createElement('canvas');
        canvas.width = Math.round(width * scale);
        canvas.height = Math.round(height * scale);
        canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);
        return { image: canvas.toDataURL('image/jpeg', quality), width: canvas.width, height: canvas.height };
    } finally {
        cleanup();
    }
}

/**
 * Coordinates Core's requests on this surface. ``send(result)`` answers Core,
 * ``acquire``/``release`` use the panel's shared camera consumers,
 * ``isCameraActive`` tells whether some consumer already holds it,
 * ``cameraError`` why the last opening failed, ``prompt`` shows the
 * confirmation or the indicator (``SnapshotPrompt``).
 */
export class SnapshotRequests {
    constructor({ send, acquire, release, isCameraActive, cameraError = () => '', prompt, capture = captureFrame } = {}) {
        Object.assign(this, { send, acquire, release, isCameraActive, cameraError, prompt, capture });
        this.active = null;
        this.seen = new Set();
    }

    async handle(payload = {}) {
        const requestId = String(payload.request_id || '');
        if (!requestId || this.seen.has(requestId)) return; // one answer per request
        this.seen.add(requestId);
        if (this.seen.size > 64) this.seen.delete(this.seen.values().next().value);
        this.cancel(this.active?.requestId, 'replaced'); // a newer request wins
        const controller = new AbortController();
        const current = { requestId, controller, cancelledByCore: false };
        this.active = current;
        const consumer = snapshotConsumer(requestId);
        let opened = false;
        let result = { status: 'cancelled' };
        try {
            const decision = await this.prompt.ask({
                requestId,
                question: String(payload.question || ''),
                destination: destinationLabel(payload.destination),
                needsConfirmation: payload.needs_confirmation !== false,
                canRemember: payload.can_remember === true,
                requestedBy: payload.requested_by === 'iris' ? 'iris' : 'user',
                signal: controller.signal,
            });
            if (controller.signal.aborted || !decision?.confirmed) return;
            const wasActive = this.isCameraActive();
            opened = true;
            const stream = await this.acquire(consumer);
            if (controller.signal.aborted) return;
            if (!stream) {
                result = { status: this.cameraError() === 'denied' ? 'camera_denied' : 'unavailable' };
                return;
            }
            this.prompt.capturing?.(requestId, wasActive);
            const frame = await this.capture(stream, { signal: controller.signal });
            if (controller.signal.aborted) return;
            result = { status: 'ok', image: frame.image, width: frame.width, height: frame.height,
                remember: decision.remember === true };
        } catch (error) {
            result = { status: error?.reason === 'cancelled' || controller.signal.aborted ? 'cancelled' : 'unavailable' };
        } finally {
            if (opened) this.release(consumer); // only this photo's: other consumers keep the camera
            this.prompt.close?.(requestId);
            if (this.active === current) this.active = null;
            // Core no longer waits for a request it cancelled itself.
            if (!current.cancelledByCore) this.send({ request_id: requestId, ...result });
        }
    }

    /** ``core``: Core cancelled it; ``user``/``replaced``: answered as cancelled. */
    cancel(requestId, reason = 'core') {
        if (!requestId || this.active?.requestId !== requestId) return;
        if (reason === 'core') this.active.cancelledByCore = true;
        this.active.controller.abort();
    }
}

/** The confirmation dialog and the capture indicator, in Face's overlay layer. */
export class SnapshotPrompt {
    constructor({ doc = globalThis.document, host = null, consent = irisConsent } = {}) {
        this.doc = doc;
        this.host = host || doc.body;
        this.node = null;
        this.requestId = '';
        this.consent = consent;
        this.controller = null;
        this.detachAbort = null;
    }

    async ask({ requestId, question, destination, needsConfirmation, canRemember, requestedBy = 'user', signal }) {
        this.close();
        this.requestId = requestId;
        const controller = this.controller = new AbortController();
        const abort = () => controller.abort();
        signal?.addEventListener('abort', abort, { once: true });
        this.detachAbort = () => signal?.removeEventListener('abort', abort);
        if (signal?.aborted) abort();
        let decision = { confirmed: true, remember: false };
        if (needsConfirmation) {
            // Core can cancel even while another Iris dialog owns the queue.
            const aborted = new Promise(resolve => {
                const cancel = () => resolve({ confirmed: false, remember: false });
                if (controller.signal.aborted) cancel();
                else controller.signal.addEventListener('abort', cancel, { once: true });
            });
            // Iris OS writes this text; the model only chose to ask (core.visual_requests).
            const byIris = requestedBy === 'iris';
            decision = await Promise.race([this.consent({
                title: byIris ? 'Iris pide ver tu cámara' : 'Iris quiere tomar una foto', icon: 'camera',
                message: byIris
                    ? `Iris decidió que necesita una foto para responder a «${question}». La tomará con la cámara y la enviará a ${destination}. Iris no la guarda.`
                    : `Para responder «${question}», Iris tomará una foto con la cámara y la enviará a ${destination}. Iris no la guarda.`,
                confirmLabel: 'Tomar foto', cancelLabel: 'Cancelar', dismissible: false,
                rememberLabel: canRemember ? `No volver a preguntar para fotos a ${destination}` : '',
                signal: controller.signal,
            }), aborted]);
        }
        if (controller.signal.aborted || this.controller !== controller || !decision?.confirmed) {
            return { confirmed: false, remember: false };
        }
        const doc = this.doc;
        const node = doc.createElement('section');
        node.className = 'vision-snapshot-prompt';
        node.setAttribute('role', 'status');
        node.setAttribute('aria-live', 'polite');
        const title = doc.createElement('strong');
        title.textContent = 'Preparando la cámara';
        const text = doc.createElement('p');
        text.textContent = `Para responder «${question}» se envía una foto a ${destination}.`;
        node.append(title, text);
        const actions = doc.createElement('div');
        actions.className = 'vision-snapshot-actions';
        const cancel = doc.createElement('button');
        cancel.type = 'button';
        cancel.textContent = 'Cancelar';
        actions.append(cancel);
        node.append(actions);
        this.host.append(node);
        this.node = node;
        this.status = text;
        cancel.addEventListener('click', () => {
            if (this.controller !== controller || controller.signal.aborted) return;
            controller.abort();
            this.onCancel?.(requestId);
        });
        return { confirmed: true, remember: canRemember === true && decision.remember === true };
    }

    capturing(requestId) {
        if (requestId !== this.requestId || !this.node) return;
        this.node.setAttribute('role', 'status');
        this.node.querySelector('strong').textContent = 'Tomando una foto';
        this.node.querySelector('.vision-snapshot-take')?.remove();
    }

    close(requestId = '') {
        if (requestId && requestId !== this.requestId) return;
        this.detachAbort?.();
        this.detachAbort = null;
        this.controller?.abort();
        this.controller = null;
        this.node?.remove();
        this.node = null;
        this.requestId = '';
    }
}
