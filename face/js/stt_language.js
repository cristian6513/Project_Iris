/**
 * «Idioma al hablar»: the language Hermes transcribes speech in (`stt.language`).
 *
 * Shared by the «VOZ · HERMES» dialog (manual_voice.js, in IRIS OS and the Companion) and IRIS OS
 * Ajustes (hermes_activity.js). Core accepts exactly this list (core/hermes_wake.py,
 * STT_LANGUAGE_CHOICES); "" is Hermes' auto-detect. Hermes reads config.yaml again for every
 * recording, so a change applies to the next one without restarting anything.
 */
export const STT_LANGUAGES = Object.freeze([
    { code: 'es', label: 'Español' },
    { code: 'en', label: 'English' },
    { code: 'pt', label: 'Português' },
    { code: 'fr', label: 'Français' },
    { code: 'de', label: 'Deutsch' },
    { code: 'it', label: 'Italiano' },
    { code: 'ja', label: '日本語' },
    { code: '', label: 'Detección automática' },
].map(Object.freeze));

export const STT_DEFAULT_LANGUAGE = 'es';

const escapeHtml = (value) => String(value).replace(/[&<>"']/g, (char) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));

/** Core answered with usable settings (not an unreadable config.yaml or an older Core). */
export function sttSettingsReady(settings) {
    return Boolean(settings) && !settings.error && typeof settings.language === 'string';
}

/** The list for a select: IRIS' languages, plus a value from the profile shown as is. */
export function sttLanguageOptions(current) {
    if (typeof current !== 'string' || STT_LANGUAGES.some((option) => option.code === current)) return [...STT_LANGUAGES];
    return [{ code: current, label: `${current} (de tu perfil)` }, ...STT_LANGUAGES];
}

export function sttLanguageLabel(code) {
    return STT_LANGUAGES.find((option) => option.code === code)?.label ?? code;
}

/** When the choice applies, or why it does not: a per-provider language in the profile wins. */
export function sttLanguageNote(settings) {
    if (!sttSettingsReady(settings)) return '';
    const override = typeof settings.language_override === 'string' ? settings.language_override : '';
    if (override) return `Tu perfil fija «${override}» para este transcriptor y manda sobre esta opción.`;
    return 'Se aplica desde la próxima grabación, sin reiniciar.';
}

/** Hermes shows text while you speak only with OpenAI, xAI or ElevenLabs, never with local whisper. */
export function liveTextNote(settings) {
    if (!sttSettingsReady(settings) || settings.live_text !== false) return '';
    return 'Con el transcriptor local no hay texto en vivo: solo con OpenAI, xAI o ElevenLabs. El texto llega al terminar de hablar.';
}

/** POST one or both options; resolves with Core's settings or throws its message. */
export async function saveSttSettings(fetchImpl, changes) {
    const response = await fetchImpl('/api/hermes-voice/settings', { method: 'POST',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(changes) });
    let result = {};
    try { result = await response.json(); } catch { /* the message below covers it */ }
    if (!response.ok || typeof result?.streaming !== 'boolean') {
        throw new Error(typeof result?.detail === 'string' ? result.detail : 'No se pudo guardar la opción de transcripción.');
    }
    return result;
}

/** A select (and an optional note) bound to these settings; the host owns the markup and saving. */
export class SttLanguageField {
    constructor({ select, note = null, onChoose = () => {} }) {
        this.select = select;
        this.note = note;
        this.optionsHtml = '';
        select.addEventListener('change', () => onChoose(select.value));
    }

    render(settings, { disabled = false } = {}) {
        const ready = sttSettingsReady(settings);
        const value = ready ? settings.language : STT_DEFAULT_LANGUAGE;
        const html = sttLanguageOptions(value)
            .map((option) => `<option value="${escapeHtml(option.code)}">${escapeHtml(option.label)}</option>`).join('');
        if (html !== this.optionsHtml) {
            this.select.innerHTML = html;
            this.optionsHtml = html;
        }
        this.select.value = value;
        this.select.disabled = disabled || !ready;
        if (this.note) this.note.textContent = sttLanguageNote(settings);
    }
}
