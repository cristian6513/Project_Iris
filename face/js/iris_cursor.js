function irisCursorImage(accent) {
    // Theme Studio supplies a hex color. Keep imported values out of the image markup.
    const color = /^#[0-9a-f]{6}$/i.test(accent) ? accent : '#73f7ff';
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24">
        <path fill="#f5fbff" stroke="#0a1422" stroke-width="1.6" stroke-linejoin="round"
            d="M2 2v16.3l4.4-4.1 3 6.5 3-1.4-3-6.4 6-.2Z"/>
        <path fill="${color}" d="M3.8 5.2v8.9l2.5-2.6 4.4-.4Z"/>
    </svg>`;
    return `url("data:image/svg+xml,${encodeURIComponent(svg)}") 2 2, auto`;
}

export function installIrisCursor(document) {
    const root = document.documentElement;
    const effects = document.defaultView.matchMedia(
        '(any-pointer: fine) and (prefers-reduced-motion: no-preference) and (forced-colors: none)',
    );
    let pulse, animation;
    document.addEventListener('click', (event) => {
        if (!effects.matches || root.dataset.irisCursorEffect !== 'true' || !event.isTrusted
            || event.pointerType !== 'mouse' || event.detail === 0) return;
        if (event.target.closest?.('input, textarea, select, [contenteditable], :disabled, [aria-disabled="true"], '
            + '[data-window-resize], .iris-orbit-cursor, .os-home-reordering')) return;

        if (!pulse) {
            pulse = document.createElement('span');
            pulse.className = 'iris-cursor-pulse';
            pulse.setAttribute('aria-hidden', 'true');
            document.body.append(pulse);
        }
        animation?.cancel();
        pulse.style.left = `${event.clientX}px`;
        pulse.style.top = `${event.clientY}px`;
        animation = pulse.animate([
            { opacity: 0.8, scale: 0.55 },
            { opacity: 0, scale: 1.35 },
        ], { duration: 220, easing: 'ease-out' });
    }, { capture: true, passive: true });
    effects.addEventListener('change', () => { if (!effects.matches) animation?.cancel(); });

    return (theme) => {
        root.dataset.irisCursor = theme.systemCursor ? 'system' : 'iris';
        const showEffect = !theme.systemCursor && !theme.reducedMotion && theme.animatedGlow;
        root.dataset.irisCursorEffect = String(showEffect);
        if (!showEffect) animation?.cancel();
        root.style.setProperty('--iris-cursor-arrow', irisCursorImage(theme.accent));
    };
}
