/** Load the pinned local QR module once; a failed load remains retryable. */
export function createQRCodeLoader(moduleLoader = (url) => import(/* @vite-ignore */ url)) {
    let pending = null;
    let nextAttempt = 0;
    return () => {
        if (!pending) {
            const attempt = nextAttempt++;
            // A query on retries avoids a failed browser module-map entry.
            // All attempts still fetch the same inventoried local file.
            const url = '/face/vendor/qrcode/qrcode.mjs' + (attempt ? `?retry=${attempt}` : '');
            pending = Promise.resolve().then(() => moduleLoader(url)).then((module) => {
                const factory = module.default;
                if (typeof factory !== 'function') throw new Error('generador QR local no disponible');
                return factory;
            }).catch((error) => {
                pending = null;
                throw error;
            });
        }
        return pending;
    };
}

export const loadQRCode = createQRCodeLoader();
