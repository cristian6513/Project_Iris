// IRIS OS window bar (user QA, 2026-10-14): with several windows open the list showed on both
// sides of the dock and, past three, ran over its icons. Now one bar at the bottom left shows
// the window in front and a «+N» that lists them all.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const source = readFileSync(new URL('../../face/js/hermes_activity.js', import.meta.url), 'utf8');
const styles = readFileSync(new URL('../../face/css/styles.css', import.meta.url), 'utf8');
const OS_WINDOW_BAR = Function(`return ${source.match(/const OS_WINDOW_BAR = (Object\.freeze\(\{[^}]*\}\));/)[1]}`)();

function method(name) {
    const block = source.match(new RegExp(`^    ${name}\\([^]*?^    \\}`, 'm'))?.[0];
    assert.ok(block, `${name} must exist`);
    const OS_WINDOW_APPS = {
        player: { title: 'IRIS Player', icon: 'player' }, theme: { title: 'Theme Studio', icon: 'palette' },
        skills: { title: 'Skills Hub', icon: 'skills' }, doctor: { title: 'Hermes Doctor', icon: 'doctor' },
        activity: { title: 'Activity Log', icon: 'activity' },
    };
    const osIcon = (icon) => `<i data-icon="${icon}"></i>`;
    return new Function('OS_WINDOW_APPS', 'osIcon', 'OS_WINDOW_BAR', `return ({${block}})`)(OS_WINDOW_APPS, osIcon, OS_WINDOW_BAR)[name];
}

function classes(initial = []) {
    const set = new Set(initial);
    return {
        set,
        contains: (c) => set.has(c),
        add: (...c) => c.forEach((x) => set.add(x)),
        remove: (...c) => c.forEach((x) => set.delete(x)),
        toggle(c, on) { if (on ?? !set.has(c)) set.add(c); else set.delete(c); },
    };
}

const rect = (left, top, width, height) => ({ left, top, width, height, right: left + width, bottom: top + height });

// geometry: { screen, dock, layer } rects as the browser reported them (Codex W02 table).
function bar(windows, geometry = null) {
    let writes = 0;
    let markup = '';
    const vars = {};
    const taskbar = {
        get innerHTML() { return markup; },
        set innerHTML(value) { writes += 1; markup = value; },
        classList: classes(),
        style: { setProperty: (k, v) => { vars[k] = v; } },
        getBoundingClientRect: () => rect(0, 0, 0, 0),
    };
    const nodes = {
        '.os-window-taskbar': taskbar,
        '.os-home-screen': geometry && { getBoundingClientRect: () => geometry.screen },
        '.os-home-dock': geometry && { getBoundingClientRect: () => geometry.dock },
        '.os-window-layer': geometry && { getBoundingClientRect: () => geometry.layer || geometry.screen },
    };
    const records = new Map(windows.map(([id, z, minimized = false, focused = false]) => [id, {
        minimized,
        el: {
            isConnected: true, style: { zIndex: String(z) },
            classList: classes([...(focused ? ['focused'] : []), ...(minimized ? ['minimized'] : [])]),
        },
    }]));
    const restored = [];
    const activity = {
        panel: { dataset: {}, querySelector: (selector) => nodes[selector] || null, querySelectorAll: () => [] },
        _osWindows: records, _windowListOpen: false, _windowLayout: {},
        _escape: (value) => String(value).replace(/[&<>"]/g, ''),
        _restoreWindow: (id) => restored.push(id),
        _captureWindowLayout() {}, _saveWindowLayout() {}, _restoreDesktopFocus() {}, _syncAppPolling() {},
    };
    for (const name of [
        '_renderWindowBar', '_toggleWindowList', '_handleWindowBarClick', '_finishWindowDrag', '_renderDesktopHome',
        '_minimizeWindow', '_windowBarPlacement', '_placeWindowBar', '_windowBottomClearance', '_renderDesktopNavigation',
    ]) activity[name] = method(name);
    activity._renderWindowBar();
    const html = () => taskbar.innerHTML.split('<div class="os-window-list"')[0];
    const leads = () => [...html().matchAll(/data-window-task="([^"]+)"/g)].map((m) => m[1]);
    const listed = () => [...(taskbar.innerHTML.split('<div class="os-window-list"')[1] || '')
        .matchAll(/data-window-task="([^"]+)"[^>]*title="[^"]*· ([^"]+)"/g)].map((m) => [m[1], m[2]]);
    return { activity, taskbar, html, leads, listed, restored, vars, writes: () => writes };
}

// A click as the browser delivers it: the target node must still be the one that was pressed.
const clickOn = (selector) => ({ target: { closest: (s) => (s === selector ? { dataset: {} } : null) } });
const clickTask = (id) => ({ target: { closest: (s) => (s === '[data-window-task]' ? { dataset: { windowTask: id } } : null) } });

test('one window: just its chip, no «+N»', () => {
    const { taskbar, leads, html } = bar([['player', 21, false, true]]);
    assert.ok(taskbar.classList.contains('visible'));
    assert.deepEqual(leads(), ['player']);
    assert.doesNotMatch(html(), /data-window-more/);
});

test('several windows: the one in front leads, the rest wait behind «+N»', () => {
    const { leads, html, activity, taskbar, listed } = bar([
        ['player', 21], ['theme', 24, false, true], ['skills', 22], ['doctor', 26, true], ['activity', 23],
    ]);
    // The minimized doctor has the highest z, but the window in front is theme.
    assert.deepEqual(leads(), ['theme']);
    assert.match(html(), />\+4<\/button>/);
    assert.match(html(), /aria-expanded="false"/);
    activity._toggleWindowList();
    assert.deepEqual(listed(), [
        ['theme', 'Al frente'], ['activity', 'Abierta'], ['skills', 'Abierta'], ['player', 'Abierta'], ['doctor', 'Minimizada'],
    ], 'most recent first, minimized last');
    assert.match(taskbar.innerHTML, /aria-expanded="true"/);
    activity._toggleWindowList(false);
    assert.doesNotMatch(taskbar.innerHTML, /class="os-window-list"/);
});

test('mouse on «+N»: pointerup without a drag keeps the pressed button, so the click opens the list (Codex W01)', () => {
    const { activity, taskbar, writes } = bar([['player', 21], ['theme', 22, false, true], ['skills', 23]]);
    const before = writes();
    // pointerdown → pointerup (window listener) → click: nothing may rebuild the bar in between.
    activity._finishWindowDrag();
    activity._renderDesktopHome();
    assert.equal(writes(), before, 'an unchanged bar is not rewritten');
    activity._handleWindowBarClick(clickOn('[data-window-more]'));
    assert.equal(activity._windowListOpen, true);
    assert.match(taskbar.innerHTML, /aria-expanded="true"/);
    // Picking a row with the mouse restores it and closes the list.
    activity._handleWindowBarClick(clickTask('player'));
    assert.equal(activity._windowListOpen, false);
});

test('a real drag still finishes and re-renders the desktop', () => {
    const { activity } = bar([['player', 21, false, true]]);
    let rendered = 0;
    activity._renderDesktopHome = () => { rendered += 1; };
    activity._dragWindow = { id: 'gone', mode: 'move' };
    activity._finishWindowDrag();
    assert.equal(activity._dragWindow, null);
    assert.equal(rendered, 1);
    activity._finishWindowDrag();
    assert.equal(rendered, 1, 'an idle pointerup does nothing');
});

test('the taskbar handles chips on click only, for mouse and keyboard alike', () => {
    assert.match(source, /\.os-window-taskbar'\)\?\.addEventListener\('click', \(ev\) => this\._handleWindowBarClick\(ev\)\)/);
    assert.doesNotMatch(source, /\.os-window-taskbar'\)\?\.addEventListener\('pointerdown'/);
});

test('minimizing the window in front hands «Al frente» to the next visible one (Codex W03)', () => {
    const { activity, leads, listed } = bar([['player', 25, false, true], ['doctor', 24], ['theme', 23]]);
    activity._minimizeWindow('player');
    const player = activity._osWindows.get('player').el;
    assert.equal(player.classList.contains('focused'), false, 'Escape must not close a hidden window');
    assert.deepEqual(leads(), ['doctor']);
    activity._toggleWindowList(true);
    assert.deepEqual(listed(), [['doctor', 'Al frente'], ['theme', 'Abierta'], ['player', 'Minimizada']]);
});

test('beside the dock when there is room, above it when not (Codex W02)', () => {
    // 1600 × 860: room to the left of the dock.
    const wide = bar([['player', 21], ['theme', 22]], { screen: rect(277, 300, 1262, 535), dock: rect(600, 720.4, 616, 90) });
    assert.equal(wide.taskbar.classList.contains('stacked'), false);
    assert.equal(wide.vars['--os-window-bar-max'], `${OS_WINDOW_BAR.maxWidth}px`);
    // 1024 × 768 and 390 × 844: the dock leaves no room beside it, so the bar goes above it.
    for (const [screen, dock] of [
        [rect(243, 260, 754, 478), rect(312, 630.6, 616, 90)],
        [rect(69, 200, 314, 635), rect(81, 579, 290, 238)],
    ]) {
        const tight = bar([['player', 21], ['theme', 22]], { screen, dock });
        assert.equal(tight.taskbar.classList.contains('stacked'), true);
        assert.equal(tight.vars['--os-dock-reserve'], `${Math.ceil(screen.bottom - dock.top)}px`);
        const max = parseInt(tight.vars['--os-window-bar-max'], 10);
        assert.ok(max <= OS_WINDOW_BAR.maxWidth && max <= screen.width - 32);
        // Windows stop above the bar as well as the dock.
        const clearance = tight.activity._windowBottomClearance();
        assert.equal(clearance, Math.ceil(screen.bottom - (dock.top - OS_WINDOW_BAR.height - OS_WINDOW_BAR.gap) + 10));
    }
    // Beside the dock the bar never reaches it.
    const placement = wide.activity._windowBarPlacement();
    assert.ok(277 + 16 + placement.maxWidth + 12 <= 600);
});

test('when the bar changes sides the windows refit, and a window never moved stays put', () => {
    const geometry = { screen: rect(277, 300, 1262, 535), dock: rect(600, 720.4, 616, 90) };
    const { activity } = bar([['player', 21], ['theme', 22]], geometry);
    assert.equal(activity._placeWindowBar(), false, 'same place: nothing to refit');
    geometry.screen = rect(243, 260, 754, 478); // the sidebar opened: no room beside the dock
    geometry.dock = rect(312, 630.6, 616, 90);
    assert.equal(activity._placeWindowBar(), true);
    assert.equal(activity._placeWindowBar(), false);
    assert.match(source, /new ResizeObserver\(\(entries\) => this\._handleHomeScreenResize\(entries\)\)/);

    const applied = {};
    activity._reflowOpenWindows = method('_reflowOpenWindows');
    activity._applyWindowGeometry = (win, layout) => { applied[win.id] = layout; };
    activity._syncWindowControlLabels = () => {};
    const win = (id, style) => ({ id, isConnected: true, style, classList: classes() });
    activity._osWindows = new Map([
        ['notifications', { el: win('notifications', { left: '42px', top: '36px', width: '1120px', height: '680px' }) }],
        ['player', { el: win('player', { left: '42px', top: '36px', width: '900px', height: '600px' }) }],
    ]);
    activity._windowLayout = { notifications: { z: 22, open: true }, player: { left: 300, top: 80, width: 800, height: 500 } };
    activity._reflowOpenWindows();
    assert.deepEqual([applied.notifications.left, applied.notifications.top, applied.notifications.width], [42, 36, 1120]);
    assert.deepEqual([applied.player.left, applied.player.top, applied.player.width], [300, 80, 800], 'saved geometry still wins');
});

test('the sidebar resizes the desktop: windows refit even when the bar keeps its side (Codex R01)', () => {
    const observed = (width, height) => [{ contentRect: { width, height } }];
    for (const [label, screens] of [
        // Beside → beside: 1600 × 860, sidebar folded then unfolded.
        ['beside', [rect(135, 300, 1404, 535), rect(277, 300, 1262, 535)]],
        // Above → above: 1024 × 768.
        ['above', [rect(150, 260, 850, 478), rect(243, 260, 754, 478)]],
    ]) {
        const geometry = { screen: screens[0], dock: rect(screens[0].left + (screens[0].width - 616) / 2, 720, 616, 90) };
        const { activity } = bar([['player', 21], ['theme', 22]], geometry);
        let refits = 0;
        activity._onViewportResize = () => { refits += 1; };
        activity._handleHomeScreenResize = method('_handleHomeScreenResize');
        activity._handleHomeScreenResize(observed(screens[0].width, screens[0].height));
        const side = activity.panel.querySelector('.os-window-taskbar').classList.contains('stacked');
        assert.equal(refits, 1, `${label}: first real size fits the windows`);
        activity._handleHomeScreenResize(observed(screens[0].width, screens[0].height));
        assert.equal(refits, 1, `${label}: same size, nothing to do`);
        geometry.screen = screens[1];
        geometry.dock = rect(screens[1].left + (screens[1].width - 616) / 2, 720, 616, 90);
        activity._handleHomeScreenResize(observed(screens[1].width, screens[1].height));
        assert.equal(activity.panel.querySelector('.os-window-taskbar').classList.contains('stacked'), side, `${label}: the bar keeps its side`);
        assert.equal(refits, 2, `${label}: narrower desktop refits the windows`);
        // Hidden desktop (dashboard mode) and coming back at the same size change nothing.
        activity._handleHomeScreenResize(observed(0, 0));
        activity._handleHomeScreenResize(observed(screens[1].width, screens[1].height));
        assert.equal(refits, 2, `${label}: hide and show at the same size`);
    }
});

test('one bar only: the tab strip beside the dock is gone', () => {
    assert.doesNotMatch(source, /os-window-tabstrip|data-window-tab\b/);
    assert.doesNotMatch(styles, /\.os-window-tabstrip/);
    // The bar opens its list over the windows (they stack at 21..29).
    const rule = styles.match(/\.os-window-taskbar \{[^}]*\}/)?.[0] || '';
    assert.match(rule, /z-index: 35;/);
    assert.match(rule, /max-width: var\(--os-window-bar-max/);
    assert.match(styles, /\.os-window-taskbar\.stacked \{\s*bottom: calc\(var\(--os-dock-reserve/);
    assert.match(source, /if \(this\._windowListOpen\) \{\s*this\._toggleWindowList\(false\);/);
});

// User QA 2026-10-19: dragging a window to the top filled the desktop without the maximized
// state, so □ then «maximized» it at the same size and «restored» it to that same size.
function snapDesk() {
    const geometry = { screen: rect(277, 80, 1262, 760), dock: rect(600, 740, 616, 90), layer: rect(277, 80, 1262, 760) };
    const { activity } = bar([['player', 21, false, true]], geometry);
    const el = {
        isConnected: true, offsetWidth: 800, offsetHeight: 500,
        style: { zIndex: '21', left: '300px', top: '80px', width: '800px', height: '500px' }, classList: classes(['focused']),
    };
    const record = { el, minimized: false, maximized: false, docked: false };
    activity._osWindows = new Map([['player', record]]);
    const applied = [];
    for (const name of ['_applyMaximizedGeometry', '_applySnapGeometry', '_captureWindowLayout', '_toggleMaximizeWindow', '_closeWindow'])
        activity[name] = method(name);
    activity._applyWindowGeometry = (win, layout) => applied.push(layout);
    activity._syncWindowControlLabels = () => {}; activity._focusWindow = () => {};
    activity._transitionPlayer = null; activity._windowViewAnchors = new Map();
    return { activity, record, el, applied };
}

test('snapping a window to the top maximizes it, and □ restores the size it had before the drag', () => {
    const { activity, record, el, applied } = snapDesk();
    activity._dragWindow = { id: 'player', mode: 'move', origin: { left: 300, top: 80, width: 800, height: 500 } };
    activity._snapPreview = 'top';
    activity._finishWindowDrag();
    assert.equal(record.maximized, true);
    assert.ok(el.classList.contains('maximized'));
    assert.deepEqual(activity._windowLayout.player, {
        left: 300, top: 80, width: 800, height: 500, docked: false, maximized: true, open: true,
    });
    activity._toggleMaximizeWindow('player');
    assert.equal(record.maximized, false);
    assert.equal(el.classList.contains('maximized'), false);
    const restore = applied.at(-1);
    assert.deepEqual([restore.left, restore.top, restore.width, restore.height, restore.maximized], [300, 80, 800, 500, false]);
});

test('snapping to a side is still a half, not a maximize', () => {
    const { activity, record, el } = snapDesk();
    activity._dragWindow = { id: 'player', mode: 'move', origin: { left: 300, top: 80, width: 800, height: 500 } };
    activity._snapPreview = 'left';
    activity._finishWindowDrag();
    assert.equal(record.maximized, false);
    assert.equal(el.classList.contains('maximized'), false);
    assert.equal(activity._windowLayout.player.maximized, false);
});

test('closing a minimized window keeps the size it had', () => {
    const { activity, record, el } = snapDesk();
    activity._windowLayout.player = { left: 300, top: 80, width: 800, height: 500 };
    record.minimized = true; el.classList.add('minimized'); el.offsetWidth = 0; el.offsetHeight = 0;
    el.remove = () => {};
    activity._closeWindow('player');
    assert.deepEqual([activity._windowLayout.player.width, activity._windowLayout.player.height], [800, 500]);
    assert.equal(activity._windowLayout.player.open, false);
});

test('a resize while OS is hidden for chat never clamps windows to fallback dimensions', () => {
    const geometry = { screen: rect(0,0,0,0), dock: rect(0,0,0,0) };
    const { activity } = bar([['theme',21]],geometry); const applied=[];
    const win=activity._osWindows.get('theme').el;
    win.style={left:'42px',top:'36px',width:'920px',height:'620px'};
    activity._applyWindowGeometry=(el,layout)=>applied.push(layout);
    activity._syncWindowControlLabels=()=>{};
    const reflow=method('_reflowOpenWindows');
    reflow.call(activity); assert.equal(applied.length,0); assert.equal(win.style.width,'920px');
    geometry.screen=rect(200,80,1100,700);
    reflow.call(activity); assert.equal(applied.length,1); assert.equal(applied[0].width,920);
});
