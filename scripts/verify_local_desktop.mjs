/** QA12: the exported desktop lost its dock (stylesheet order) and no check opened it.
 * Opens IRIS OS the way a user does and uses the dock: every button visible and hit by a
 * click at its centre, «Aplicaciones» opens the launcher and «Chat» the conversation. */
import assert from 'node:assert/strict';
import path from 'node:path';

const DOCK = ['Chat', 'Aplicaciones', 'Inicio', 'Panel'];

export async function verifyLocalDesktop(page, output) {
    await page.locator('#btn-iris-os').click();
    await page.waitForFunction(() => window.iris?.activityCanvas?._open
        && document.querySelector('.hermes-workbench .os-home-dock') !== null, null, { timeout: 30000 });
    await page.waitForTimeout(900);  // the opening transition
    const dock = await page.evaluate(() => [...document.querySelectorAll('.os-home-dock > button')].map(button => {
        const rect = button.getBoundingClientRect();
        const hit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
        return { label: button.textContent.trim(), display: getComputedStyle(button.parentElement).display,
            width: Math.round(rect.width), height: Math.round(rect.height), hit: !!hit && button.contains(hit) };
    }));
    assert.deepEqual(dock.map(button => button.label), DOCK, 'Dock buttons');
    for (const button of dock) {
        assert.ok(button.display !== 'none' && button.width >= 40 && button.height >= 40 && button.hit,
            `Dock button «${button.label}» cannot be clicked: ${JSON.stringify(button)}`);
    }
    await page.waitForFunction(() => {
        const model = window.iris?.activityCanvas?._homePreferences;
        return model && !model.loading && (model.layout || model.error);
    }, null, { timeout: 10000 });
    const home = await page.evaluate(async () => {
        const model = window.iris.activityCanvas._homePreferences;
        const before = model.layout ? structuredClone(model.layout) : null;
        const rendered = [...document.querySelectorAll('.os-home-screen .os-home-tile [data-home-open]')].map(n => n.dataset.homeOpen);
        if (!before || model.error || before.shortcuts.length < 2) return { error: model.error, before, rendered };
        const ref = 'app:' + before.shortcuts[0];
        const saved = await model.move(ref, 1, []);
        const expected = structuredClone(model.layout);
        await model.load();
        return { error: model.error, before, rendered, saved, expected, reread: structuredClone(model.layout) };
    });
    assert.equal(home.error, '', 'Home API must return real preferences, not a generic fixture response');
    assert.ok(home.before?.shortcuts.length >= 2, 'Fresh profile home shortcuts');
    assert.deepEqual([...home.rendered].sort(), [...home.before.shortcuts].sort(), 'Home shortcuts rendered');
    assert.ok(home.saved, 'Home reorder saved');
    assert.equal(home.reread.revision, home.before.revision + 1, 'Preference revision increments');
    assert.deepEqual(home.reread, home.expected, 'Home reorder survives an API reread');
    assert.equal(home.reread.order[1], 'app:' + home.before.shortcuts[0]);
    await page.screenshot({ path: path.join(output, 'desktop-home.png') });

    await page.locator('.os-home-dock [data-os-launcher="toggle"]').click();
    await page.waitForFunction(() => document.querySelector('.os-home-launcher')?.classList.contains('open'),
        null, { timeout: 10000 });
    // Headless Edge renders a frame only when asked, so the fade-in finishes with this capture.
    await page.waitForTimeout(400);
    await page.screenshot({ path: path.join(output, 'desktop-apps.png') });
    const launcher = await page.evaluate(() => {
        const drawer = document.querySelector('.os-home-launcher');
        const rect = drawer.getBoundingClientRect();
        // The personalized home lists its tiles (data-home-open); the default grid, data-os-open.
        const apps = [...drawer.querySelectorAll('[data-home-open], [data-os-open]')].filter(app => {
            const box = app.getBoundingClientRect();
            const hit = document.elementFromPoint(box.left + box.width / 2, box.top + Math.min(box.height / 2, 40));
            return box.width > 0 && !!hit && app.contains(hit);
        });
        return { width: Math.round(rect.width), height: Math.round(rect.height),
            opacity: Number(getComputedStyle(drawer).opacity), apps: apps.map(app => app.dataset.homeOpen || app.dataset.osOpen) };
    });
    assert.ok(launcher.width > 0 && launcher.height > 0 && launcher.opacity > 0.9 && launcher.apps.length > 0,
        `«Aplicaciones» did not show the apps: ${JSON.stringify(launcher)}`);
    await page.keyboard.press('Escape');
    await page.waitForFunction(() => !document.querySelector('.os-home-launcher')?.classList.contains('open'),
        null, { timeout: 10000 });

    await page.locator('.os-home-dock [data-os-chat]').click();
    await page.waitForFunction(() => !window.iris?.activityCanvas?._open
        && document.querySelector('#side-panel')?.classList.contains('open'), null, { timeout: 10000 });
    return { dock, home, launcher, chat: true };
}
