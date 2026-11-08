// QA12: the installed desktop had no dock. Vite bundles face/index.html's relative
// stylesheets into one file it links at the end of <head>, and leaves absolute links where
// they are. os_shell.css (absolute) sat in <head> before that bundle, so styles.css won
// an equally specific !important rule and hid the dock. These checks keep the source order
// and the installed order the same, and the dock out of the rule that hides the rest.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const html = readFileSync(new URL('../../face/index.html', import.meta.url), 'utf8');
const styles = readFileSync(new URL('../../face/css/styles.css', import.meta.url), 'utf8');

const stylesheets = [...html.matchAll(/<link\b[^>]*rel="stylesheet"[^>]*href="([^"]+)"[^>]*>/g)]
    .map((match) => ({ href: match[1], index: match.index }))
    .filter(({ href }) => !/^https?:/.test(href));

test('stylesheets Vite leaves in place load after the bundle it puts in <head>', () => {
    const bodyStart = html.indexOf('<body');
    const absolute = stylesheets.filter(({ href }) => href.startsWith('/'));
    assert.deepEqual(absolute.map(({ href }) => href.split('?')[0]), ['/face/css/os_shell.css', '/face/css/os_fonts.css']);
    for (const link of absolute) {
        assert.ok(link.index > bodyStart, `${link.href} must be in <body>, after the bundled stylesheets`);
    }
    const lastRelative = Math.max(...stylesheets.filter(({ href }) => !href.startsWith('/')).map(({ index }) => index));
    assert.ok(Math.min(...absolute.map(({ index }) => index)) > lastRelative, 'os_shell.css and os_fonts.css load last');
});

test('the rule that hides the rest of the desktop never hides the dock', () => {
    const rule = styles.match(/\.workbench-view\[data-workbench-view="desktop"\] > :not\(\.os-home-screen\)([^{]*)\{\s*display:\s*none !important/);
    assert.ok(rule, 'the desktop hide rule is still in styles.css');
    assert.match(rule[1], /:not\(\.os-home-dock\)/);
});
