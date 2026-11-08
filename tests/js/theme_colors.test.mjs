// Theme colours (2026-10-13, after the qa11 QA): every IRIS OS app, the IRIS dialogs, the setup
// wizard and the manual follow the chosen theme. A fixed cyan or magenta in their rules shows the
// IRIS colours under Rose, Verdant or Solar, so these guards fail on any new one.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { normalizeStoredTheme } from '../../face/js/os_theme_file.js';

const read = (path) => readFileSync(new URL(`../../${path}`, import.meta.url), 'utf8');
const COLOR = /#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b|rgba?\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*(?:,\s*[\d.]+\s*)?\)/g;
const FALLBACK = /var\(--[\w-]+,\s*(?:#[0-9a-fA-F]{3,6}|rgba?\([^()]*\))\s*\)/g;

function rgbOf(token) {
    if (token.startsWith('#')) {
        const hex = token.length === 4 ? [...token.slice(1)].map((c) => c + c).join('') : token.slice(1);
        return [0, 2, 4].map((i) => parseInt(hex.slice(i, i + 2), 16));
    }
    return token.match(/[\d.]+/g).slice(0, 3).map(Number);
}

// Cyan/sky or magenta/pink strong enough to read as the IRIS palette (not near-white text).
function themeHue(token) {
    const [r, g, b] = rgbOf(token).map((c) => c / 255);
    const max = Math.max(r, g, b);
    const min = Math.min(r, g, b);
    const l = (max + min) / 2;
    if (max === min) return null;
    const d = max - min;
    const s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
    let h = max === r ? (g - b) / d + (g < b ? 6 : 0) : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
    h *= 60;
    // Pale tints count only when strongly saturated (#9df4ff); cool greys are neutral text.
    if (s < 0.45 || l < 0.3 || l > 0.88 || (l > 0.8 && s < 0.85)) return null;
    if (h >= 170 && h <= 215) return 'cyan';
    if (h >= 290 && h <= 340) return 'magenta';
    return null;
}

// Innermost rules as { selector, body, line }; @media/@supports blocks are walked into.
function rules(text) {
    const out = [];
    const stack = [];
    let start = 0;
    for (let i = 0; i < text.length; i += 1) {
        if (text.startsWith('/*', i)) {
            i = text.indexOf('*/', i) + 1;
            if (!stack.length || stack.at(-1).kind !== 'rule') start = i + 1;
            continue;
        }
        const ch = text[i];
        if (ch === '{') {
            const selector = text.slice(start, i).trim();
            stack.push({ kind: selector.startsWith('@') ? 'at' : 'rule', selector, from: i + 1 });
            start = i + 1;
        } else if (ch === '}') {
            const block = stack.pop();
            if (block?.kind === 'rule') {
                out.push({ selector: block.selector.replace(/\s+/g, ' '), body: text.slice(block.from, i),
                    line: text.slice(0, block.from).split('\n').length });
            }
            start = i + 1;
        } else if (ch === ';' && (!stack.length || stack.at(-1).kind === 'at')) {
            start = i + 1;
        }
    }
    return out;
}

function fixedColours(path, { inScope = () => true, skipSelector = () => false, allowed = () => false } = {}) {
    const found = [];
    for (const { selector, body, line } of rules(read(path))) {
        const selectors = selector.split(',').map((s) => s.trim()).filter(Boolean);
        if (!selectors.length || !selectors.every(inScope) || selectors.some(skipSelector)) continue;
        for (const declaration of body.split(';')) {
            const name = declaration.split(':')[0].trim();
            for (const token of declaration.replace(FALLBACK, '').match(COLOR) || []) {
                if (themeHue(token) && !allowed(name, token)) found.push(`${path}:${line} ${selector} → ${name}: ${token}`);
            }
        }
    }
    return found;
}

const sameColour = (token, ...rgb) => rgbOf(token).every((c, i) => c === rgb[i]);

test('IRIS OS apps take their accents from the theme', () => {
    // Rules that only apply inside the IRIS OS desktop (.hermes-workbench); the room, the
    // Command Center and the loader keep their own palette.
    const inScope = (s) => /\.os-|\.workbench|\.hermes-workbench|\.cap-|\.capability-center|\.mission-columns/.test(s);
    const allowed = (name, token) => ['--iris-os-accent', '--iris-os-accent-2', '--workbench-accent'].includes(name)
        // Errors and «Restaurar» keep their warning pink in every theme.
        || sameColour(token, 255, 142, 215) || sameColour(token, 255, 74, 163);
    assert.deepEqual(fixedColours('face/css/styles.css', { inScope, allowed }), []);
    for (const sheet of ['inbox', 'automations', 'notifications', 'iris_player', 'async_agents', 'iris_os_responsive']) {
        assert.deepEqual(fixedColours(`face/css/${sheet}.css`), [], sheet);
    }
    // Shared tokens (Capability Center, Notification Center, Modelos) are remapped to the theme.
    const workbench = rules(read('face/css/styles.css'))
        .filter((rule) => rule.selector === '.hermes-workbench' && rule.body.includes('--os-cyan'));
    assert.equal(workbench.length, 1);
    assert.match(workbench[0].body, /--os-cyan: var\(--iris-os-accent\)/);
    assert.match(workbench[0].body, /--os-magenta: var\(--iris-os-accent-2\)/);
    assert.match(workbench[0].body, /--os-line: color-mix\(in srgb, var\(--iris-os-accent\)/);
});

test('the theme reaches <html>, so dialogs and overlays outside the desktop follow it', () => {
    const source = read('face/js/hermes_activity.js');
    const block = source.match(/^ {4}_applyOsTheme\([^]*?^ {4}\}/m)?.[0];
    assert.ok(block);
    const html = { style: new Map(), dataset: {} };
    html.style.setProperty = html.style.set.bind(html.style);
    const panel = { style: new Map(), dataset: {} };
    panel.style.setProperty = panel.style.set.bind(panel.style);
    const published = [];
    const previous = globalThis.document;
    globalThis.document = { documentElement: html };
    try {
        const apply = new Function('OS_THEME_DEFAULT', 'publishDesktopTheme', 'normalizeStoredTheme', `return ({${block}})`)(
            { accent: '#73f7ff', accent2: '#ff74d4', readyColor: '#5cffad' }, (theme) => published.push(theme), normalizeStoredTheme,
        )._applyOsTheme;
        const activity = {
            panel, _osTheme: { accent: '#ff8ed7', accent2: '#c78bff', readyColor: '#ff8ed7', reducedMotion: true },
            _themeBackgroundValue: () => 'none', _themeProfileValue: () => 'none', _saveOsTheme: () => true,
        };
        assert.equal(apply.call(activity), true);
    } finally {
        globalThis.document = previous;
    }
    assert.equal(html.style.get('--iris-theme-accent'), '#ff8ed7');
    assert.equal(html.style.get('--iris-theme-accent-2'), '#c78bff');
    assert.equal(html.style.get('--iris-theme-ready'), '#ff8ed7');
    assert.equal(html.dataset.irisReducedMotion, 'true');
    assert.equal(panel.style.get('--iris-os-accent'), '#ff8ed7');
    assert.equal(published.length, 1);

    // The dialog reads the theme live in CSS (IRIS OS first, then the wizard's edition).
    const dialog = read('face/css/iris_dialog.css');
    assert.match(dialog, /--dlg-a: var\(--iris-theme-accent, var\(--companion-accent, var\(--installer-accent, #73f7ff\)\)\)/);
    assert.match(dialog, /--dlg-b: var\(--iris-theme-accent-2, var\(--companion-accent-2, var\(--installer-accent-2, #ff74d4\)\)\)/);
    assert.doesNotMatch(read('face/js/iris_dialog.js'), /--dlg-a/);
    assert.match(read('face/css/command_palette.css'), /--os-cyan: var\(--iris-theme-accent, #8defff\)/);
});

test('the setup wizard and the manual follow the chosen edition', () => {
    // Kept: the edition definitions themselves and the blue of «running» states.
    const editions = (s) => /^(:root|body|body\[data-installer-theme=[^\]]+\])$|data-edition-preview=/.test(s);
    const allowed = (name, token) => sameColour(token, 96, 165, 250);
    assert.deepEqual(fixedColours('wizard/ui/wizard.css', { skipSelector: editions, allowed }), []);
    assert.match(read('wizard/ui/wizard.css'), /--cyan: var\(--installer-accent\);/);
    // The edition cards show each edition's own colour on purpose.
    const manual = (s) => /^(:root|body\.help-page\[data-manual-theme=[^\]]+\])$|^\.manual-theme\.(cyber|sakura|hacker|classic) i$/.test(s);
    assert.deepEqual(fixedColours('wizard/ui/help.css', { skipSelector: manual }), []);
    assert.doesNotMatch(read('wizard/ui/index.html'), /rgba\(168, ?216, ?234/);
});
