import test from 'node:test';
import assert from 'node:assert/strict';
import { HomePreferences } from '../../face/js/os_desktop_home.js';
import { DesktopWidgets } from '../../face/js/os_widgets.js';
import { homeDocument } from '../fixtures/home_dom.mjs';

const tick = () => new Promise((resolve) => setImmediate(resolve));
const deferred = () => { let resolve; const promise = new Promise((done) => { resolve = done; }); return { promise, resolve }; };
const widget = (app, digit) => ({ instance_id: `wdg_${digit.repeat(32)}`, widget_id: `${app}.${app === 'calendar' ? 'month' : 'transport'}`,
    config_version: 1, size: 'compact', calendars: [] });

function setup(kind) {
    const calendar = widget('calendar', 'a'), player = widget('player', 'b');
    const initial = kind === 'home'
        ? { schema: 'iris.desktop-home/v1', revision: 0, shortcuts: ['calendar', 'player'], hidden_apps: ['calendar'], order: ['app:calendar', 'app:player'] }
        : { schema: 'iris.desktop-widgets/v1', revision: 0, side: 'right', widgets: [calendar, player] };
    let stored = structuredClone(initial), nextHeld = null;
    const calls = [];
    const fetchImpl = async (url, init) => {
        calls.push(init.method);
        const held = nextHeld; nextHeld = null;
        if (init.method === 'PUT' && !held?.conflict) stored = { ...JSON.parse(init.body), revision: stored.revision + 1 };
        const snapshot = structuredClone(stored);
        if (held) { held.started.resolve(); await held.release.promise; }
        return held?.conflict
            ? { ok: false, status: 409, json: async () => ({ error: { message: 'Cambió en otra ventana.' } }) }
            : { ok: true, json: async () => snapshot };
    };
    const documentLike = homeDocument(); documentLike.head = documentLike.createElement('head');
    const factory = () => ({ configure() {}, setVisibility() {}, dispose() {} });
    const model = kind === 'home' ? new HomePreferences({ fetchImpl })
        : new DesktopWidgets({ root: documentLike.createElement('section'), documentLike, fetchImpl, isVisible: () => false,
            factories: { 'calendar.month': factory, 'player.transport': factory } });
    return {
        model, calls,
        hold(conflict = false) {
            const held = { conflict, started: deferred(), release: deferred() }; nextHeld = held;
            return { started: held.started.promise, release: () => held.release.resolve() };
        },
        remove() {
            stored = kind === 'home' ? { ...stored, revision: stored.revision + 1, shortcuts: ['player'], hidden_apps: [], order: ['app:player'] }
                : { ...stored, revision: stored.revision + 1, widgets: [player] };
            return model.forgetApps(['calendar']);
        },
        save: () => kind === 'home' ? model.pin('theme') : model.save(model.layout.widgets, 'left'),
        addAgain: () => kind === 'home' ? model.pin('calendar') : model.add('calendar.month'),
        check(present) {
            if (kind === 'home') {
                assert.equal(model.layout.shortcuts.includes('calendar'), present);
                assert.ok(model.layout.shortcuts.includes('player'), 'unrelated shortcut remains');
                if (!present) {
                    assert.ok(!model.layout.hidden_apps.includes('calendar'), 'old hidden state is not restored');
                    assert.ok(!model.layout.order.includes('app:calendar'), 'old position is not restored');
                }
            } else {
                assert.equal(model.layout.widgets.some((item) => item.widget_id === 'calendar.month'), present);
                assert.equal([...model.cards.values()].some((card) => card.id === calendar.instance_id), false,
                    'the retired widget instance must never remount');
                assert.ok(model.cards.has(player.instance_id), 'unrelated widget remains mounted');
            }
        },
        hasCalendar: () => kind === 'home' ? model.layout.shortcuts.includes('calendar')
            : model.layout.widgets.some((item) => item.widget_id === 'calendar.month'),
    };
}

for (const kind of ['home', 'widgets']) {
    for (const operation of ['save', 'read', 'conflict-read']) {
        test(`F17 L03 ${kind}: reinstall does not revive an old ${operation} response`, async (t) => {
            const h = setup(kind); t.after(() => h.model.dispose()); await h.model.load(); await tick();
            const pendingResponse = h.hold(operation === 'conflict-read');
            const operationPromise = operation === 'read' ? h.model.load() : h.save();
            await pendingResponse.started;
            if (operation === 'conflict-read') {
                // The conflict fallback GET starts before uninstall and returns after reinstall.
                const conflictRead = h.hold(); pendingResponse.release(); await conflictRead.started;
                pendingResponse.release = conflictRead.release;
            }
            const cleanup = h.remove(); h.check(false);
            h.model.restoreApps(['calendar']);
            const reread = h.hold(); // keep reconciliation from masking admission of an old reply
            pendingResponse.release(); await operationPromise; await reread.started;
            h.check(false);
            reread.release(); assert.equal(await cleanup, true); h.check(false);
            assert.equal(await h.addAgain(), true, 'a deliberate new addition is still allowed');
            await tick(); h.check(true);
            assert.equal(h.calls.filter((method) => method === 'PUT').length, operation === 'read' ? 1 : 2,
                'no background write retries');
        });
    }
    test(`F17 L03 ${kind}: each later uninstall also invalidates pending work`, async (t) => {
        const h = setup(kind); t.after(() => h.model.dispose()); await h.model.load();
        await h.remove(); h.model.restoreApps(['calendar']); await h.addAgain();
        const held = h.hold(); const pending = h.save(); await held.started;
        const cleanup = h.remove(); h.model.restoreApps(['calendar']);
        const reread = h.hold(); held.release(); await pending; await reread.started;
        h.check(false); reread.release(); await cleanup;
        await h.addAgain(); await tick(); assert.equal(h.hasCalendar(), true);
    });
}
