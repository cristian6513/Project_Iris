import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { parseTransitionFile, validateTransition, validateTransitionLibrary, themeTransitions, exportTransition, TRANSITION_LIMITS } from '../../face/js/os_transition_format.js';
import { TRANSITION_EXAMPLE_CSS } from '../../face/js/os_transition_examples.js';
import { TransitionPlayer } from '../../face/js/os_transition_player.js';
import { TransitionStudio } from '../../face/js/os_transition_studio.js';
import { OS_THEME_DEFAULT } from '../../face/js/os_theme.js';
import { mkdtemp } from 'node:fs/promises';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { join } from 'node:path';

const example = () => parseTransitionFile(TRANSITION_EXAMPLE_CSS).transitions[0];
const css = body => `@keyframes sample { from { ${body} } to { transform: none; opacity: 1; } }`;
const flush = () => new Promise(resolve => setImmediate(resolve));

test('the shipped CSS example becomes standard keyframes and a self-contained, attributed JSON round trip', () => {
    const parsed = example();
    assert.equal(parsed.name, 'entrada-suave'); assert.equal(parsed.duration, 480);
    assert.equal(parsed.keyframes[0].opacity, 0);
    assert.equal(parsed.keyframes[0].transform, 'translateY(24px) scale(0.96)');
    assert.match(parsed.attribution, /IRIS OS/);
    assert.deepEqual(parseTransitionFile(exportTransition(parsed)).transitions[0], parsed);
});

test('all community examples use the same CSS/JSON contract and keep their complete attribution', async () => {
    for (const name of ['entrada-suave', 'zoom-sereno', 'giro-sutil']) {
        const base = new URL(`../../examples/transitions/${name}`, import.meta.url);
        const source = await readFile(new URL(`${base.href}.css`), 'utf8');
        const packaged = await readFile(new URL(`${base.href}.json`), 'utf8');
        const a = parseTransitionFile(source).transitions[0], b = parseTransitionFile(packaged).transitions[0];
        assert.deepEqual(a.keyframes, b.keyframes); assert.equal(a.duration, b.duration);
        assert.equal(b.id, name); assert.equal(b.license, 'MIT');
        assert.match(b.attribution, /Copyright \(c\) 2026 IRIS OS Contributors/);
        assert.match(b.attribution, /Permission is hereby granted/);
    }
});

test('the contributor CLI validates and converts with the shared reader and refuses to overwrite a package', async () => {
    const root = fileURLToPath(new URL('../../', import.meta.url));
    const output = join(await mkdtemp(join(root, 'artifacts', 'motion-tool-')), 'converted.json');
    const run = (...args) => spawnSync(process.execPath, ['scripts/transition_tool.mjs', ...args], { cwd: root, encoding: 'utf8' });
    const checked = run('check', 'examples/transitions/entrada-suave.css'); assert.equal(checked.status, 0, checked.stderr);
    assert.match(checked.stdout, /compatible/);
    const converted = run('convert', 'examples/transitions/entrada-suave.css', output, '--name', 'Compartido', '--author', 'Comunidad', '--license', 'MIT');
    assert.equal(converted.status, 0, converted.stderr);
    const before = await readFile(output, 'utf8'); assert.equal(parseTransitionFile(before).transitions[0].name, 'Compartido');
    assert.equal(run('convert', 'examples/transitions/zoom-sereno.css', output).status, 1);
    assert.equal(await readFile(output, 'utf8'), before);
});

test('individual CSS like Animista supports prefixed copies, grouped percentages and bounded cubic curves', () => {
    const frames = `0%, 20% { -webkit-transform: translate3d(0, -100px, 0); transform: translate3d(0, -100px, 0); opacity: 0; }
        70% { transform: scale3d(1.05, 1.05, 1); animation-timing-function: ease-in; } 100% { opacity: 1; transform: none; }`;
    const source = `.sample { -webkit-animation: sample .6s cubic-bezier(.2,.8,.2,1) both; animation: sample .6s cubic-bezier(.2,.8,.2,1) both; }
        @-webkit-keyframes sample { ${frames} } @keyframes sample { ${frames} }`;
    const [effect] = parseTransitionFile(source).transitions;
    assert.equal(effect.duration, 600); assert.equal(effect.keyframes.length, 4);
    assert.deepEqual(effect.keyframes.map(frame => frame.offset), [0, .2, .7, 1]);
    assert.equal(effect.keyframes[2].easing, 'ease-in');
});

test('CSS source is rejected completely for executable/global resources or unsupported visual properties', () => {
    for (const source of [
        '@import url(https://bad); ' + css('opacity: 0;'),
        '@font-face { src: url(https://bad); }',
        'body { opacity: 0; }' + css('opacity: 0;'),
        '.sample { animation: sample 1s; color: red; }' + css('opacity: 0;'),
        css('filter: blur(5px);'), css('transform: translateX(var(--x));'),
        css('transform: url(https://bad);'), css('transform: perspective(600px);'),
        css('display: none;'), css('background: url(https://bad);'), css('transform: constructor(0);'), css('transform: toString(0);'),
        css('opacity: 0; __proto__: x;'),
        '@keyframes sample { from { opacity: 0; }',
        '/* unclosed comment',
    ]) assert.throws(() => parseTransitionFile(source), undefined, source);
});

test('durations, loops, directions, offsets and transforms have explicit limits instead of silent truncation', () => {
    for (const source of [
        '.sample { animation: sample 1s infinite; }' + css('opacity: 0;'),
        '.sample { animation: sample 1s reverse; }' + css('opacity: 0;'),
        '.sample { animation: sample 1s 1s; }' + css('opacity: 0;'),
        '.sample { animation: sample 8s; }' + css('opacity: 0;'),
        css('transform: translateX(99999px);'), css('transform: scale(3);'), css('transform: rotate(999deg);'),
        css('transform: translate3d(0, 0, 5px);'), css('opacity: 2;'),
        '@keyframes sample { 30% { opacity: 0; } 100% { opacity: 1; } }',
        '@keyframes sample { 0% { opacity: 0; } 101% { opacity: 1; } }',
        '@keyframes sample { from { opacity: 0; } to { opacity: 1; } } @-webkit-keyframes sample { from { opacity: 1; } to { opacity: 1; } }',
    ]) assert.throws(() => parseTransitionFile(source), undefined, source);
    assert.throws(() => parseTransitionFile(' '.repeat(TRANSITION_LIMITS.bytes + 1)), /64/);
    assert.throws(() => parseTransitionFile(''), /vacío/);
});

test('JSON packages reject future versions, unknown actions, malformed frames and duplicate/excess libraries', () => {
    const valid = example();
    for (const invalid of [
        { ...valid, schema: 'iris.transition/v9' }, { ...valid, command: 'run' },
        { ...valid, keyframes: [{ ...valid.keyframes[0], opacity: '0' }, valid.keyframes[1]] },
        { ...valid, keyframes: [{ ...valid.keyframes[0], offset: .5 }, { ...valid.keyframes[1], offset: .4 }] },
        { ...valid, duration: NaN }, { ...valid, easing: 'steps(9999)' },
        { ...valid, keyframes: valid.keyframes.map(frame => ({ ...frame, transform: 'matrix(1,0,0,1,0,0)' })) },
        { ...valid, keyframes: [{ offset: 0, transform: 'none', transformOrigin: 'left right' }, valid.keyframes[1]] },
    ]) assert.throws(() => validateTransition(invalid));
    assert.throws(() => validateTransitionLibrary([valid, valid]), /repetidos/);
    assert.throws(() => validateTransitionLibrary(Array.from({ length: 21 }, (_, i) => ({ ...valid, id: `id-${i}` }))), /20/);
    assert.throws(() => themeTransitions({ transitionLibrary: null }, { strict: true }));
    assert.throws(() => themeTransitions({ transitionLibrary: [valid], desktopTransition: 'custom:missing' }, { strict: true }), /incluida/);
    assert.deepEqual(themeTransitions({ transitionLibrary: [valid], desktopTransition: 'custom:missing' }), { transitionLibrary: [valid], desktopTransition: 'none' });
    assert.deepEqual(themeTransitions({}), { transitionLibrary: [], desktopTransition: 'none' });
});

function animationTarget() {
    const animations = [];
    return { isConnected: true, style: {}, animations, animate(frames, timing) {
        let resolve, reject;
        const finished = new Promise((yes, no) => { resolve = yes; reject = no; });
        const animation = { frames, timing, finished, cancelled: 0, finish: resolve, cancel() { this.cancelled++; reject(new Error('cancelled')); } };
        animations.push(animation); return animation;
    } };
}
function events() { return { callbacks: new Map(), addEventListener(name, fn) { this.callbacks.set(name, fn); }, removeEventListener(name) { this.callbacks.delete(name); } }; }

test('one player owns effects per element, releases on finish and leaves geometry/style untouched', async () => {
    const player = new TransitionPlayer({ documentLike: null, media: null }), target = animationTarget();
    target.style.left = '42px'; target.style.width = '780px';
    assert.equal(player.play(target, example()).played, true);
    const first = target.animations[0]; player.play(target, example());
    assert.equal(first.cancelled, 1); await flush(); assert.equal(player.active.size, 1);
    assert.deepEqual(target.animations[1].timing, { duration: 480, easing: 'cubic-bezier(0.2, 0.8, 0.2, 1)', iterations: 1, fill: 'none' });
    target.animations[1].finish(); await flush(); assert.equal(player.active.size, 0);
    assert.deepEqual(target.style, { left: '42px', width: '780px' }); player.dispose();
});

test('reduced motion, hidden pages, cancellation and disposal stop effects; a late finish never cancels a newer one', async () => {
    const media = { ...events(), matches: false }, documentLike = { ...events(), visibilityState: 'visible' };
    let reduced = false;
    const player = new TransitionPlayer({ documentLike, media, reduced: () => reduced }), target = animationTarget();
    player.play(target, example()); media.matches = true; media.callbacks.get('change')();
    assert.equal(target.animations[0].cancelled, 1); assert.equal(player.play(target, example()).played, false);
    media.matches = false; player.play(target, example()); reduced = true;
    assert.equal(player.play(target, example()).played, false); reduced = false;
    player.play(target, example()); documentLike.visibilityState = 'hidden'; documentLike.callbacks.get('visibilitychange')();
    assert.equal(player.active.size, 0); assert.equal(player.play(target, example()).played, false);
    documentLike.visibilityState = 'visible'; player.dispose();
    assert.equal(media.callbacks.size, 0); assert.equal(documentLike.callbacks.size, 0);
    assert.equal(player.play(target, example()).played, false); await flush();
});

// Small DOM stand-in: exercises the controller's real file, edit, save and cancellation code.
class Node {
    constructor(tag = '') { Object.assign(this, { tag, value: '', children: [], textContent: '', hidden: false, style: {}, dataset: {}, handlers: {} }); }
    replaceChildren(...children) { this.children = children; }
    addEventListener(name, fn) { this.handlers[name] = fn; }
    removeEventListener(name) { delete this.handlers[name]; }
    contains(target) { return this.children.includes(target); }
}
function studioSetup({ persist = true, confirm } = {}) {
    const nodes = new Map(), buttons = new Map();
    for (const name of ['installed', 'info', 'status', 'file', 'candidate', 'source', 'draft', 'importer', 'name', 'author', 'license', 'duration', 'attribution']) nodes.set(name, new Node(name));
    for (const name of ['preview', 'export', 'remove', 'save']) buttons.set(name, new Node('button'));
    const sample = animationTarget(), root = new Node('root');
    root.querySelector = selector => selector === '.motion-sample' ? sample : selector.includes('data-motion-action') ? buttons.get(selector.match(/="([^"]+)/)[1]) : nodes.get(selector.match(/="([^"]+)/)?.[1]);
    let theme = { ...OS_THEME_DEFAULT }, commits = [], downloads = [];
    const studio = new TransitionStudio({ root, documentLike: { createElement: tag => new Node(tag) }, getTheme: () => theme,
        commit: value => { commits.push(value); if (!persist) return false; theme = value; return true; },
        download: (...args) => downloads.push(args), confirm: confirm || (async () => true) });
    studio.setVisibility(true);
    return { studio, nodes, sample, root, commits, downloads, get theme() { return theme; }, set theme(value) { theme = value; } };
}

test('CSS imports remain inert drafts; save generates a fresh id, selects it and exports exactly its data', async () => {
    const s = studioSetup(); s.studio.stage(TRANSITION_EXAMPLE_CSS);
    assert.equal(s.commits.length, 0); assert.equal(s.sample.animations.length, 0);
    s.nodes.get('name').value = '<img src=bad onerror=bad()> Mi efecto';
    await s.studio.action('preview-draft'); assert.equal(s.sample.animations.length, 1); assert.equal(s.commits.length, 0);
    await s.studio.action('save'); assert.equal(s.theme.transitionLibrary.length, 1);
    const saved = s.theme.transitionLibrary[0]; assert.match(saved.id, /^motion-/);
    assert.equal(s.theme.desktopTransition, `custom:${saved.id}`);
    assert.equal(s.nodes.get('installed').children.at(-1).textContent, saved.name);
    await s.studio.action('export'); assert.deepEqual(JSON.parse(s.downloads[0][0]), saved);
    assert.match(saved.attribution, /IRIS OS/); s.studio.dispose(); await flush();
});

test('invalid input, cancellation, failed persistence and capacity leave the existing theme untouched', async () => {
    const failed = studioSetup({ persist: false }); failed.studio.stage(TRANSITION_EXAMPLE_CSS);
    await assert.rejects(failed.studio.action('save'), /No se pudo guardar/);
    assert.equal(failed.theme.desktopTransition, 'none'); assert.equal(failed.studio.candidates.length, 1);
    failed.studio.stage(css('display: none;')); assert.equal(failed.studio.candidates.length, 0);
    assert.match(failed.nodes.get('status').textContent, /display/);
    failed.studio.stage(TRANSITION_EXAMPLE_CSS); await failed.studio.action('cancel');
    assert.equal(failed.commits.length, 1); assert.equal(failed.studio.candidates.length, 0);
    failed.studio.dispose();
    const full = studioSetup(); full.theme = { ...OS_THEME_DEFAULT, transitionLibrary: Array.from({ length: 20 }, (_, i) => ({ ...example(), id: `item-${i}` })) };
    full.studio.stage(TRANSITION_EXAMPLE_CSS); await assert.rejects(full.studio.action('save'), /20/);
    assert.equal(full.commits.length, 0); full.studio.dispose();
});

test('late file reads cannot replace a newer draft, an explicit cancel or a hidden/disposed importer', async () => {
    const s = studioSetup(); let answer;
    const first = s.studio.readFile({ size: 10, text: () => new Promise(resolve => { answer = resolve; }) });
    s.studio.stage(TRANSITION_EXAMPLE_CSS); answer(css('opacity: 0;')); await first;
    assert.equal(s.studio.candidates[0].name, 'entrada-suave');
    let late; const pending = s.studio.readFile({ size: 10, text: () => new Promise(resolve => { late = resolve; }) });
    s.studio.setVisibility(false); s.studio.setVisibility(true); late(TRANSITION_EXAMPLE_CSS); await pending;
    assert.equal(s.studio.candidates.length, 0); assert.equal(s.commits.length, 0);
    let touched = false; await s.studio.readFile({ size: 999999, text() { touched = true; } }); assert.equal(touched, false);
    s.studio.dispose();
});

test('removal re-reads current theme after confirmation and never overwrites an intervening selection', async () => {
    let decide;
    const s = studioSetup({ confirm: () => new Promise(resolve => { decide = resolve; }) });
    s.studio.stage(TRANSITION_EXAMPLE_CSS); await s.studio.action('save');
    const removal = s.studio.action('remove'); s.theme = { ...s.theme, desktopTransition: 'fade', accent: '#abcdef' }; decide(true); await removal;
    assert.equal(s.theme.desktopTransition, 'fade'); assert.equal(s.theme.accent, '#abcdef'); assert.equal(s.theme.transitionLibrary.length, 0);
    s.studio.dispose();
});

test('the real shell persists before applying and keeps prior motion live if the snapshot write fails', async () => {
    const source = await readFile(new URL('../../face/js/hermes_activity.js', import.meta.url), 'utf8');
    const method = name => source.match(new RegExp(`^    ${name}\\([^]*?^    \\}`, 'm'))[0];
    const create = new Function('themeTransitions', 'OS_THEME_USER_OVERRIDE_KEY', 'OS_THEME_STORAGE_KEY', 'localStorage', `return ({${method('_commitTransitionTheme')}})`);
    const writes = [], old = { ...OS_THEME_DEFAULT }, next = { ...old, transitionLibrary: [example()], desktopTransition: `custom:${example().id}` };
    let failWrite = true;
    const values = new Map([['override', 'false']]);
    const commit = create(themeTransitions, 'override', 'theme', { getItem: key => values.get(key) ?? null, removeItem: key => values.delete(key),
        setItem(key, value) { writes.push([key, value]); if (key === 'theme' && failWrite) throw new Error('quota'); values.set(key, value); } })._commitTransitionTheme;
    const shell = { _osTheme: old, applied: 0, _applyOsTheme() { this.applied++; }, _renderThemePanel() {} };
    assert.equal(commit.call(shell, next), false); assert.equal(shell._osTheme, old); assert.equal(shell.applied, 0);
    assert.equal(values.get('override'), 'false');
    failWrite = false; assert.equal(commit.call(shell, next), true); assert.equal(shell.applied, 1);
    assert.deepEqual(themeTransitions(JSON.parse(writes.at(-1)[1]), { strict: true }), themeTransitions(next, { strict: true }));
});

test('the shell plays the selected custom recipe on its active surface and cancels on theme/navigation changes', async () => {
    const source = await readFile(new URL('../../face/js/hermes_activity.js', import.meta.url), 'utf8');
    const method = name => source.match(new RegExp(`^    ${name}\\([^]*?^    \\}`, 'm'))[0];
    const real = new Function('OS_THEME_DEFAULT', `return ({${method('_playDesktopTransition')},${method('_cancelThemeMotion')}})`)(OS_THEME_DEFAULT);
    const target = animationTarget(), player = new TransitionPlayer({ documentLike: null, media: null });
    const oldDocument = globalThis.document;
    globalThis.document = { visibilityState: 'visible' };
    const item = example();
    const shell = { ...real, _open: true, _activeTab: 'desktop', _transitionPlayer: player,
        panel: { querySelector: () => target, classList: { remove() {} } },
        _osTheme: { ...OS_THEME_DEFAULT, transitionLibrary: [item], desktopTransition: `custom:${item.id}` } };
    try {
        shell._playDesktopTransition(); assert.equal(target.animations.length, 1);
        shell._osTheme = { ...shell._osTheme, reducedMotion: true }; shell._playDesktopTransition();
        assert.equal(player.active.size, 0); assert.equal(target.animations.length, 1);
        shell._osTheme.reducedMotion = false; globalThis.document.visibilityState = 'hidden'; shell._playDesktopTransition();
        assert.equal(target.animations.length, 1);
    } finally { globalThis.document = oldDocument; player.dispose(); }
    await flush();
});
