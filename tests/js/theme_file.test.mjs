import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { OS_THEME_DEFAULT, OS_THEME_PRESETS } from '../../face/js/os_theme.js';
import { decodeThemeFile, encodeThemeFile, managedThemeImage, normalizeStoredTheme, THEME_FILE_MAX_BYTES } from '../../face/js/os_theme_file.js';
import { themeTransitions, parseTransitionFile } from '../../face/js/os_transition_format.js';
import { TRANSITION_EXAMPLE_CSS } from '../../face/js/os_transition_examples.js';

const wrapped = theme => ({ version: 1, exportedAt: '2026-10-18T17:00:00.000Z', theme });
const decode = value => decodeThemeFile(JSON.stringify(value));
const remote = 'https://images.example.invalid/track?recipient=fixture';
const recipe = () => parseTransitionFile(TRANSITION_EXAMPLE_CSS).transitions[0];

test('real v1 exports and raw legacy preferences preserve IDs, booleans, zero and numeric string representation', () => {
    const input = { ...OS_THEME_DEFAULT, ...OS_THEME_PRESETS.verdant, blurIntensity: 0, accentGlow: '0', reducedMotion: false };
    for (const payload of [input, wrapped(input)]) {
        const { theme, omittedImages } = decode(payload);
        assert.equal(theme.background, 'hacker'); assert.equal(theme.profile, 'iris');
        assert.equal(theme.blurIntensity, '0'); assert.equal(theme.accentGlow, '0');
        assert.equal(theme.reducedMotion, false); assert.deepEqual(omittedImages, []);
    }
    assert.equal(decode({ profile: 'alice' }).theme.profile, 'iris');
    // QA12: every theme shows the IRIS portrait, also old Sakura themes and the Sakura preset.
    assert.equal(decode({ profile: 'sakura' }).theme.profile, 'iris');
    assert.equal(OS_THEME_PRESETS.rose.profile, 'iris');
});

test('empty, future, malformed, unknown and oversized files cannot become a theme', () => {
    for (const input of [{}, [], null, 123, { version: 999, theme: { accent: '#123456' } },
        { version: '1', theme: { accent: '#123456' } }, { version: 1, theme: {} },
        { theme: { accent: '#123456' } }, { version: 1, theme: [], exportedAt: 'bad' },
        { ...wrapped({ accent: '#123456' }), scripts: ['bad'] }, { accent: '#123456', dangerous: remote },
        wrapped({ backgroundUrl: remote }), wrapped({ accent: '#123456', profileUrl: {} }),
        wrapped({ accent: '#123456', transitionLibrary: null }),
    ]) assert.throws(() => decode(input), undefined, JSON.stringify(input));
    assert.throws(() => decodeThemeFile('{'), /JSON/);
    assert.throws(() => decodeThemeFile(' '.repeat(THEME_FILE_MAX_BYTES + 1)), /grande/);
});

test('types, range limits and enums are validated before any image handling or application', () => {
    for (const input of [
        { accent: 'url(https://bad)' }, { readyColor: 7 }, { reducedMotion: 'false' },
        { compactMode: 0 }, { blurIntensity: -1 }, { neonIntensity: 101 }, { cardOpacity: 0 },
        { accentGlow: '' }, { accentGlow: 'NaN' }, { accentGlow: true },
        { background: 'cyber' }, { profile: remote }, { profileUrl: '\u0000' },
        { desktopTransition: 'downloaded-but-absent' },
    ]) assert.throws(() => decode(wrapped(input)), undefined, JSON.stringify(input));
    const values = decode(wrapped({ accentGlow: 0, borderGlow: '0', neonIntensity: 0,
        backgroundDim: '0', liveIntensity: 0, blurIntensity: '0' })).theme;
    for (const field of ['accentGlow', 'borderGlow', 'neonIntensity', 'backgroundDim', 'liveIntensity', 'blurIntensity']) assert.equal(values[field], '0');
});

test('import never preserves image references, including relative resources belonging to another profile', () => {
    for (const url of [remote, '//images.example.invalid/track', 'http://127.0.0.1:9/other', '/api/os/theme-image/background?v=123',
        '/api/os/theme-image/profile?v=123', 'data:image/svg+xml,bad', 'blob:foreign', '/arbitrary/endpoint', 'file:///C:/private.png',
        'x"); background-image:url(https://bad)', '/face/public/assets/os/iris-os-bg-v1.png']) {
        const result = decode(wrapped({ accent: '#123456', backgroundUrl: url, profileUrl: url }));
        assert.equal(result.theme.backgroundUrl, ''); assert.equal(result.theme.profileUrl, '');
        assert.deepEqual(result.omittedImages, ['backgroundUrl', 'profileUrl']);
    }
});

test('local rendering and restored snapshots allow only exact same-kind resources from the upload API', () => {
    assert.equal(managedThemeImage('/api/os/theme-image/background?v=123', 'background'), '/api/os/theme-image/background?v=123');
    assert.equal(managedThemeImage('/api/os/theme-image/profile', 'profile'), '/api/os/theme-image/profile');
    for (const value of [remote, '//bad', '/api/os/theme-image/profile?v=123', '/api/os/theme-image/background/../profile',
        '/api/os/theme-image/background?v=123&redirect=https://bad', '/api/os/theme-image/background?v=fixture',
        '/api/os/theme-image/background?v=123#x', '/api/os/theme-image/background%3fv=123', '/other', 'data:image/png,aaa',
        '/api/os/theme-image/background?v=123\n']) {
        assert.equal(managedThemeImage(value, 'background'), '', value);
    }
    const restored = normalizeStoredTheme({ ...OS_THEME_DEFAULT, accent: 'bad', blurIntensity: 0,
        backgroundUrl: remote, profileUrl: '/api/os/theme-image/profile?v=42', reducedMotion: 'false' });
    assert.equal(restored.accent, OS_THEME_DEFAULT.accent); assert.equal(restored.blurIntensity, '0');
    assert.equal(restored.backgroundUrl, ''); assert.equal(restored.profileUrl, '/api/os/theme-image/profile?v=42');
    assert.equal(restored.reducedMotion, false);
});

test('export is an autonomous preferences JSON, keeps validated transitions, and carries neither image URLs nor bytes', () => {
    const item = recipe();
    const value = { ...OS_THEME_DEFAULT, backgroundUrl: '/api/os/theme-image/background?v=123', profileUrl: remote,
        transitionLibrary: [item], desktopTransition: `custom:${item.id}` };
    const result = encodeThemeFile(value, '2026-10-18T17:00:00.000Z');
    assert.deepEqual(Object.keys(result.payload), ['version', 'exportedAt', 'theme']);
    assert.deepEqual(result.omittedImages, ['backgroundUrl', 'profileUrl']);
    assert.equal(result.payload.theme.backgroundUrl, ''); assert.equal(result.payload.theme.profileUrl, '');
    assert.doesNotMatch(JSON.stringify(result.payload), /example.invalid|theme-image|"assets"/);
    assert.deepEqual(decode(result.payload).theme, result.payload.theme);
    assert.equal(value.backgroundUrl, '/api/os/theme-image/background?v=123');
});

const source = await readFile(new URL('../../face/js/hermes_activity.js', import.meta.url), 'utf8');
const method = name => source.match(new RegExp(`^    (?:async )?${name}\\([^]*?^    \\}`, 'm'))[0];
function setup({ approve = false, persistence = true, confirm } = {}) {
    const listeners = [], notices = [], questions = [], stored = new Map(), css = new Map(), published = [], scheduled = [], revoked = [];
    let file, blob;
    const document = { createElement(tag) { return tag === 'input' ? { get files() { return [file]; }, addEventListener(_, fn) { listeners.push(fn); }, click() {} }
        : { click() {}, remove() {} }; }, body: { appendChild() {} } };
    const localStorage = { getItem: key => stored.get(key) ?? null, removeItem: key => stored.delete(key), setItem(key, value) {
        if (key === 'theme' && !persistence) throw new Error('quota'); stored.set(key, value);
    } };
    const deps = { document, localStorage, OS_THEME_DEFAULT, OS_THEME_PRESETS, themeTransitions, normalizeStoredTheme, managedThemeImage,
        decodeThemeFile, encodeThemeFile, THEME_FILE_MAX_BYTES, OS_THEME_STORAGE_KEY: 'theme', OS_THEME_USER_OVERRIDE_KEY: 'override',
        OS_THEME_INSTALLER_SOURCE_KEY: 'installer', OS_THEME_LEGACY_STORAGE_KEYS: [],
        irisAlert: async value => notices.push(value), irisConfirm: async value => { questions.push(value); return confirm ? confirm(value) : approve; },
        publishDesktopTheme: value => published.push(value), Blob, OS_APPS: { list: () => [] },
        URL: { createObjectURL(value) { blob = value; return 'blob:fixture'; }, revokeObjectURL: value => revoked.push(value) },
        setTimeout(fn) { scheduled.push(fn); return { unref() {} }; },
    };
    const methods = new Function(...Object.keys(deps), `return ({${['_importTheme', '_exportTheme', '_commitTransitionTheme', '_cssImageUrl',
        '_themeBackgroundValue', '_themeProfileValue', '_applyOsTheme', '_loadOsTheme', '_syncModuleVisibility'].map(method).join(',')}})`)(...Object.values(deps));
    const shell = { ...methods, visible: true, _osTheme: { ...OS_THEME_DEFAULT, ...OS_THEME_PRESETS.rose },
        panel: { dataset: {}, style: { setProperty: (k, v) => css.set(k, v) }, classList: { remove() {} } },
        _hasVisibleAppSurface() { return this.visible; }, _renderThemePanel() {} };
    const choose = (value, { text, size } = {}) => {
        const contents = JSON.stringify(value); file = { size: size ?? contents.length, text: text || (async () => contents) };
        shell._importTheme(); return listeners.at(-1)();
    };
    return { shell, choose, listeners, notices, questions, stored, css, published, scheduled, revoked, get blob() { return blob; } };
}

test('actual import cancels image references by default; explicit acceptance applies only validated preferences', async () => {
    const attack = wrapped({ accent: '#123456', backgroundUrl: remote, profileUrl: remote });
    const denied = setup(); const old = denied.shell._osTheme; await denied.choose(attack);
    assert.equal(denied.shell._osTheme, old); assert.equal(denied.stored.size, 0); assert.equal(denied.questions.length, 1);
    const accepted = setup({ approve: true }); await accepted.choose(attack);
    assert.equal(accepted.shell._osTheme.accent, '#123456'); assert.equal(accepted.shell._osTheme.backgroundUrl, '');
    assert.equal(accepted.shell._osTheme.profileUrl, '');
    assert.doesNotMatch(JSON.stringify([...accepted.css.values()]), /example.invalid/);
    assert.doesNotMatch(accepted.stored.get('theme'), /example.invalid/); assert.equal(accepted.published.length, 1);
    assert.deepEqual(accepted.notices, []);
});

test('invalid imports and a failed snapshot write leave live state/CSS untouched and explain failure', async () => {
    for (const invalid of [{}, { version: 2, theme: { accent: '#123456' } }, wrapped({ accent: 'bad', backgroundUrl: remote })]) {
        const s = setup({ approve: true }), previous = s.shell._osTheme;
        await s.choose(invalid); assert.equal(s.shell._osTheme, previous); assert.equal(s.css.size, 0);
        assert.equal(s.questions.length, 0); assert.equal(s.notices.length, 1);
    }
    const failed = setup({ persistence: false }), previous = failed.shell._osTheme;
    await failed.choose(wrapped({ accent: '#123456' })); assert.equal(failed.shell._osTheme, previous);
    assert.equal(failed.css.size, 0); assert.match(failed.notices[0].message, /guardar/);
});

test('the actual applicator preserves numeric zeros; stale stored remote URLs are blocked before publishing/rendering', () => {
    const s = setup();
    s.stored.set('theme', JSON.stringify({ ...OS_THEME_DEFAULT, backgroundUrl: remote, profileUrl: remote }));
    const loaded = s.shell._loadOsTheme(); assert.equal(loaded.backgroundUrl, ''); assert.equal(loaded.profileUrl, '');
    s.shell._osTheme = { ...loaded, backgroundUrl: remote, profileUrl: remote, blurIntensity: 0, accentGlow: 0,
        borderGlow: '0', neonIntensity: 0, liveIntensity: 0, backgroundDim: '0' };
    s.shell._applyOsTheme();
    assert.equal(s.css.get('--iris-blur-intensity'), '0px');
    for (const key of ['accent-glow', 'border-glow', 'neon-intensity', 'live-intensity', 'background-dim']) assert.equal(s.css.get(`--iris-${key}`), '0');
    assert.doesNotMatch(JSON.stringify([...s.css.values()]), /example.invalid/);
    assert.equal(s.published[0].backgroundUrl, ''); assert.equal(s.shell._cssImageUrl(remote, 'background'), '');
    assert.equal(s.shell._cssImageUrl('/api/os/theme-image/background?v=123', 'background'), 'url("/api/os/theme-image/background?v=123")');
});

test('old themes keep the installed transition library; oversized input is rejected before reading', async () => {
    const s = setup(); const item = recipe(); s.shell._osTheme.transitionLibrary = [item];
    await s.choose(wrapped({ accent: '#123456' })); assert.deepEqual(s.shell._osTheme.transitionLibrary, [item]);
    const next = s.shell._osTheme; let read = false;
    await s.choose({}, { size: THEME_FILE_MAX_BYTES + 1, text: async () => { read = true; return '{}'; } });
    assert.equal(read, false); assert.equal(s.shell._osTheme, next);
});

test('a later import or manual theme edit wins over a slow read or pending image confirmation', async () => {
    const s = setup(); let complete;
    const pending = s.choose({}, { text: () => new Promise(resolve => { complete = resolve; }) });
    await s.choose(wrapped({ accent: '#abcdef' })); complete(JSON.stringify(wrapped({ accent: '#123456' }))); await pending;
    assert.equal(s.shell._osTheme.accent, '#abcdef');
    let decide; const prompt = setup({ confirm: () => new Promise(resolve => { decide = resolve; }) });
    const waiting = prompt.choose(wrapped({ accent: '#123456', backgroundUrl: remote })); await Promise.resolve();
    prompt.shell._osTheme = { ...prompt.shell._osTheme, accent: '#abcdef' }; prompt.shell._applyOsTheme();
    decide(true); await waiting; assert.equal(prompt.shell._osTheme.accent, '#abcdef'); assert.equal(prompt.stored.has('theme'), false);
});

test('actual export explains omitted images, includes no URLs, and keeps its Blob alive for the download', async () => {
    const cancelled = setup(); cancelled.shell._osTheme.backgroundUrl = '/api/os/theme-image/background?v=123';
    await cancelled.shell._exportTheme(); assert.equal(cancelled.blob, undefined); assert.equal(cancelled.questions.length, 1);
    const s = setup({ approve: true }); s.shell._osTheme.backgroundUrl = '/api/os/theme-image/background?v=123';
    await s.shell._exportTheme(); const payload = JSON.parse(await s.blob.text());
    assert.equal(payload.version, 1); assert.equal(payload.theme.backgroundUrl, ''); assert.equal(payload.theme.accentGlow, '65');
    assert.equal(s.revoked.length, 0); s.scheduled[0](); assert.deepEqual(s.revoked, ['blob:fixture']);
});

test('closing and reopening Theme Studio invalidates the old file read even when visible again', async () => {
    const s = setup(); let complete;
    const previous = s.shell._osTheme;
    const pending = s.choose({}, { text: () => new Promise(resolve => { complete = resolve; }) });
    s.shell.visible = false; s.shell._syncModuleVisibility();
    s.shell.visible = true; s.shell._syncModuleVisibility();
    complete(JSON.stringify(wrapped({ accent: '#123456' }))); await pending;
    assert.equal(s.shell._osTheme, previous); assert.equal(s.stored.size, 0);
    assert.equal(s.css.size, 0); assert.equal(s.notices.length, 0);
});
