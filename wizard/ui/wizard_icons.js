/* Setup wizard icons: the same IRIS OS line icons as the desktop (/shared/os_icons.js).
 *
 * The wizard marks each of its icons explicitly: <wz-icon>⏳</wz-icon> in its HTML and
 * setIconText() for its labels (wizard.js), or <wz-icon name="identity"> for a named one.
 * This element draws that mark as the IRIS OS icon; no other text is ever read, so what the
 * person writes, a model's name or a provider's error keep their characters (Codex,
 * wizard-icons-oct13-review). If the module does not load, the emoji inside still reads.
 */
import { osIconSvg, OS_ICON_PATHS } from '/shared/os_icons.js?v=2';
import { iconForToken } from './wizard_icon_tokens.js?v=3';

export class WizardIcon extends HTMLElement {
    connectedCallback() {
        if (this.dataset.drawn) return;
        const named = this.getAttribute('name');
        const entry = named && OS_ICON_PATHS[named] ? [named, this.getAttribute('tone') || ''] : iconForToken(this.textContent);
        if (!entry) return; // not one of the wizard's icons: the emoji stays
        const [icon, tone] = entry;
        this.setAttribute('aria-hidden', 'true');
        if (tone) this.dataset.tone = tone;
        this.innerHTML = osIconSvg(icon);
        this.dataset.drawn = icon;
    }
}

if (!customElements.get('wz-icon')) customElements.define('wz-icon', WizardIcon);
