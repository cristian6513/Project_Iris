/** Bind a shared local stream without restarting capture when a view reopens. */
export function hasLiveCameraStream(stream) {
    return Boolean(stream?.getVideoTracks?.().some(track => track.readyState === 'live'));
}

export async function attachCameraPreview(video, stream) {
    if (!video) return false;
    if (!stream) {
        video.pause?.();
        video.srcObject = null;
        return false;
    }
    video.muted = true;
    video.autoplay = true;
    video.playsInline = true;
    if (video.srcObject !== stream) video.srcObject = stream;
    try {
        // Reparenting or hiding a video can pause it even with autoplay set.
        if (video.paused || video.readyState < 2) await video.play();
        return video.srcObject === stream && !video.paused;
    } catch {
        // A closing/replaced view may abort play(). Capture and other views
        // still own the stream; the next explicit opening retries playback.
        return false;
    }
}

/**
 * F03: why the camera could not be opened, from the browser's error name.
 * Each state has its own message: a cancelled permission is not a busy
 * camera, and neither is evidence that the user is absent.
 */
export function cameraErrorReason(error) {
    const name = String(error?.name || '');
    if (name === 'NotAllowedError' || name === 'SecurityError' || name === 'PermissionDeniedError') return 'denied';
    if (name === 'NotReadableError' || name === 'TrackStartError' || name === 'AbortError') return 'busy';
    if (name === 'NotFoundError' || name === 'DevicesNotFoundError' || name === 'OverconstrainedError') return 'missing';
    return 'error';
}

export const CAMERA_ERROR_MESSAGES = Object.freeze({
    denied: 'No hay permiso para usar la cámara, o se canceló la solicitud. Revisa Capability Center y la privacidad de cámara de Windows.',
    busy: 'Otra aplicación está usando la cámara. Ciérrala y vuelve a intentarlo.',
    missing: 'No encuentro ninguna cámara conectada.',
    lease: 'La cámara la usa otra ventana de Iris, o IRIS Core está desconectado.',
    ended: 'La cámara dejó de estar disponible (se desconectó o el equipo se suspendió).',
    error: 'No se pudo acceder a la cámara.',
});
