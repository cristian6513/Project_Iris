/**
 * IRIS Subtitles — Speech Text Overlay
 * 
 * Displays IRIS's speech as text with golden highlight animation.
 */

export class SubtitleManager {
    constructor() {
        this.container = document.getElementById('subtitle-text');
        this.hideTimeout = null;
    }

    show(text) {
        if (!this.container) return;
        const safeText = String(text ?? '');

        // Clear previous timeout
        if (this.hideTimeout) {
            clearTimeout(this.hideTimeout);
        }

        // Build the highlight with DOM text nodes. Speech comes from an LLM
        // and must never be interpreted as markup (event attributes included).
        if (safeText.length > 0) {
            const highlight = document.createElement('span');
            highlight.className = 'highlight';
            highlight.textContent = safeText.charAt(0);
            this.container.replaceChildren(highlight, document.createTextNode(safeText.slice(1)));
        } else {
            this.container.textContent = safeText;
        }

        // Show with animation
        this.container.classList.add('visible');

        // Auto-hide after a calculated duration based on text length
        const duration = Math.max(3000, safeText.length * 60); // ~60ms per character
        this.hideTimeout = setTimeout(() => this.hide(), duration);
    }

    hide() {
        if (!this.container) return;
        this.container.classList.remove('visible');
    }

    update(text) {
        // For real-time streaming updates
        if (!this.container) return;
        this.container.textContent = text;
        this.container.classList.add('visible');
    }
}

