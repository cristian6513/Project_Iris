import { test } from 'node:test';
import assert from 'node:assert/strict';
import { OS_THEME_DEFAULT, OS_THEME_PRESETS } from '../../face/js/os_theme.js';
import { publishDesktopTheme } from '../../face/js/desktop_host.js';

test('desktop shares the existing four Theme Studio palettes', () => {
    assert.deepEqual(Object.keys(OS_THEME_PRESETS).sort(), ['iris', 'rose', 'sunrise', 'verdant']);
    assert.equal(OS_THEME_DEFAULT.accent, OS_THEME_PRESETS.iris.accent);
    assert.equal(OS_THEME_DEFAULT.background, 'city');
});
test('Theme Studio can publish visual changes to its validated desktop parent', () => {
    const messages = [];
    const theme = { ...OS_THEME_DEFAULT, accent: '#abc123', reducedMotion: true };
    const host = {
        location: { href: 'http://127.0.0.1:8766/face/?desktop_origin=http%3A%2F%2F127.0.0.1%3A9010' },
        parent: { postMessage: (...args) => messages.push(args) },
    };
    assert.equal(publishDesktopTheme(theme, host), true);
    assert.deepEqual(messages, [[{ type: 'iris.desktop.theme', theme }, 'http://127.0.0.1:9010']]);
});

test('the licenses module inherits every manual edition palette (A08.6)', async () => {
    const { readFileSync } = await import('node:fs');
    const css = readFileSync(new URL('../../wizard/ui/help.css', import.meta.url), 'utf8');
    const block = css.slice(css.indexOf('10 / NOTICES'));
    assert.ok(block.length > 200);
    // Colors come from the edition tokens, so Cyber/Sakura/Hacker/Classic all apply.
    assert.match(block, /var\(--manual-cyan\)/);
    assert.doesNotMatch(block, /#[0-9a-fA-F]{3,8}\b/);
    for (const preset of ['rose', 'verdant', 'sunrise']) {
        assert.match(css, new RegExp(`data-manual-theme="${preset}"`));
    }
});
