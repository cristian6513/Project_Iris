// Setup wizard icons (2026-10-13): the wizard shows the IRIS OS line icons (face/js/os_icons.js)
// through its own explicit marks, <wz-icon> and setIconText(). After Codex's review
// (wizard-icons-oct13-review, P3) no text is interpreted: a name like «🐟 Alice 🧠», a model
// label or a provider error keeps its characters, while «⏳ Instalando…» still gets its icon.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { OS_ICON_PATHS, osIconSvg } from '../../face/js/os_icons.js';
import { WIZARD_ICONS, iconForToken } from '../../wizard/ui/wizard_icon_tokens.js';
import { register } from 'node:module';

// The wizard loads the shared icons from /shared/ (wizard/app.py); here they come from face/js.
const sharedIcons = new URL('../../face/js/os_icons.js', import.meta.url).href;
register(`data:text/javascript,${encodeURIComponent(`export async function resolve(specifier, context, next) {
    if (specifier.startsWith('/shared/os_icons.js')) return { url: ${JSON.stringify(sharedIcons)}, shortCircuit: true };
    return next(specifier, context);
}`)}`);

const read = (path) => readFileSync(new URL(`../../${path}`, import.meta.url), 'utf8');
const EMOJI = /[\u{1F1E6}-\u{1F1FF}\u{1F300}-\u{1FAFF}☀-➿⭐⬆↔-⇿⌀-⏿]/gu;
// Text symbols the wizard writes as text on purpose: voice genders and arrows in comments.
const TEXT_SYMBOLS = new Set(['♀', '♂', '⇒', '↗', '⌕']);

// A tiny DOM: enough for setIconText() and the <wz-icon> element.
class FakeNode {
    constructor(tag = '#text', text = '') { this.tag = tag; this.children = []; this.textValue = text; this.dataset = {}; this.attributes = {}; }
    get textContent() { return this.tag === '#text' ? this.textValue : this.children.map((c) => c.textContent).join(''); }
    set textContent(value) { this.children = [new FakeNode('#text', String(value))]; }
    replaceChildren(...nodes) { this.children = nodes.map((n) => (typeof n === 'string' ? new FakeNode('#text', n) : n)); }
    setAttribute(name, value) { this.attributes[name] = String(value); }
    getAttribute(name) { return this.attributes[name] ?? null; }
    set innerHTML(value) { this.html = value; this.children = []; }
}

function wizardHelpers() {
    const source = read('wizard/ui/wizard.js');
    const start = source.indexOf('const LEADING_ICON');
    const end = source.indexOf('// IRIS OS dialogs');
    assert.ok(start > 0 && end > start);
    const escapeHtml = (value) => String(value).replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
    const document = { createElement: (tag) => new FakeNode(tag) };
    return new Function('document', 'escapeHtml', `${source.slice(start, end)}; return { setIconText, iconLabelHtml, uiIcon };`)(document, escapeHtml);
}

test('the wizard labels its own icons; the rest of a label, data included, stays text', () => {
    const { setIconText, iconLabelHtml, uiIcon } = wizardHelpers();
    const el = new FakeNode('div');
    setIconText(el, '⏳ Instalando…');
    assert.equal(el.children[0].tag, 'wz-icon');
    assert.equal(el.children[0].textContent, '⏳');
    assert.equal(el.children[1].textContent, ' Instalando…');

    setIconText(el, '❌ Error: el modelo 🐟 Alice 🧠 no responde');
    assert.equal(el.children.length, 2);
    assert.equal(el.children[1].textContent, ' Error: el modelo 🐟 Alice 🧠 no responde');

    setIconText(el, '🔒');
    assert.equal(el.children.length, 1);
    setIconText(el, 'Alice 🧠');
    assert.deepEqual(el.children.map((c) => c.tag), ['#text']);
    assert.equal(el.textContent, 'Alice 🧠');

    assert.equal(iconLabelHtml('⭐ Populares'), '<wz-icon>⭐</wz-icon> Populares');
    assert.equal(iconLabelHtml('<b>🐟</b>'), '&#60;b&#62;🐟&#60;/b&#62;');
    assert.equal(uiIcon('✅'), '<wz-icon>✅</wz-icon>');
});

test('<wz-icon> draws only the wizard\'s tokens and named icons; nothing else is read', async () => {
    const defined = {};
    globalThis.HTMLElement = class extends FakeNode { constructor() { super('wz-icon'); } };
    globalThis.customElements = { get: (name) => defined[name], define: (name, cls) => { defined[name] = cls; } };
    try {
        const { WizardIcon } = await import('../../wizard/ui/wizard_icons.js?test');
        assert.equal(defined['wz-icon'], WizardIcon);
        const busy = Object.assign(new WizardIcon(), {});
        busy.textContent = '⏳';
        busy.connectedCallback();
        assert.equal(busy.html, osIconSvg('clock'));
        assert.equal(busy.dataset.tone, 'busy');
        assert.equal(busy.getAttribute('aria-hidden'), 'true');

        const named = new WizardIcon();
        named.setAttribute('name', 'identity');
        named.textContent = '🎭';
        named.connectedCallback();
        assert.equal(named.html, osIconSvg('identity'));

        const foreign = new WizardIcon();
        foreign.textContent = '🐟';
        foreign.connectedCallback();
        assert.equal(foreign.html, undefined, 'an unknown emoji stays as it is');
        assert.equal(foreign.textContent, '🐟');
    } finally {
        delete globalThis.HTMLElement;
        delete globalThis.customElements;
    }
    const module = read('wizard/ui/wizard_icons.js');
    for (const reader of ['TreeWalker', 'MutationObserver', 'childNodes', 'splitIcons']) {
        assert.ok(!module.includes(reader), `${reader}: the icons never read the page's text`);
    }
});

test('every token points to an icon of the shared set', () => {
    for (const [emoji, [icon]] of WIZARD_ICONS) assert.ok(OS_ICON_PATHS[icon], `${emoji} → ${icon}`);
    assert.deepEqual(iconForToken('⚠️'), ['warning', 'warn']);
    assert.equal(iconForToken('🐟'), null);
    for (const name of read('wizard/ui/index.html').matchAll(/<wz-icon name="([\w-]+)"/g)) {
        assert.ok(OS_ICON_PATHS[name[1]], name[1]);
    }
});

test('the wizard\'s HTML marks every icon; its scripts only label their own strings', () => {
    const html = read('wizard/ui/index.html');
    const bare = html.replace(/<wz-icon[^>]*>[^<]*<\/wz-icon>/g, '').replace(/<script[^]*?<\/script>/g, '');
    const loose = [...bare.matchAll(EMOJI)].map((m) => m[0]).filter((s) => !TEXT_SYMBOLS.has(s));
    assert.deepEqual(loose, [], 'an emoji in the HTML outside <wz-icon>');
    for (const [, token] of html.matchAll(/<wz-icon>([^<]*)<\/wz-icon>/g)) assert.ok(iconForToken(token), token);
    for (const [, option, group] of html.matchAll(/<option[^>]*>([^<]*)<|<optgroup label="([^"]*)"/g)) {
        const label = option ?? group;
        const symbols = [...label.matchAll(EMOJI)].map((m) => m[0]).filter((s) => !TEXT_SYMBOLS.has(s));
        assert.deepEqual(symbols, [], `an option cannot hold an icon: ${label}`);
    }

    const wizard = read('wizard/ui/wizard.js');
    for (const [, token] of wizard.matchAll(/<wz-icon>([^<$]*)<\/wz-icon>/g)) assert.ok(iconForToken(token), token);
    // Data goes through escapeHtml and is never handed to an icon helper.
    assert.doesNotMatch(wizard, /escapeHtml\([^)]*[\u{1F300}-\u{1FAFF}☀-➿]/u);
    assert.doesNotMatch(wizard, /setIconText\([^,]+,\s*(?:d|data|msg|e|err|error)\.\w+\)/);
    assert.ok(html.indexOf('/shared/iris_dialog.js') < html.indexOf('/ui/wizard_icons.js'));
    assert.match(read('wizard/ui/wizard_icons.js'), /from '\/shared\/os_icons\.js\?v=\d+'/);
});

test('the dialogs and the desktop draw the same IRIS OS mark', () => {
    // The dialog badge is the «IRIS OS» mark next to the desktop title, lines included.
    assert.match(OS_ICON_PATHS.core, /M12 3v5m0 8v5/);
    assert.equal(osIconSvg('iris'), osIconSvg('core'));
    assert.equal(osIconSvg('gear'), osIconSvg('settings'));
    assert.match(read('face/js/iris_dialog.js'), /import \{ osIconSvg \} from '\.\/os_icons\.js\?v=\d+'/);
    assert.match(read('face/js/hermes_activity.js'), /import \{[^}]*\bosIconSvg\b[^}]*\} from '\.\/os_icons\.js\?v=\d+'/);
    assert.doesNotMatch(read('face/js/iris_dialog.js'), /const ICONS/);
});
