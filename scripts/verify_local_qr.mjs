/** QR recovery smoke for an isolated, caller-owned browser page and QA server. */
import assert from 'node:assert/strict';

export async function verifyLocalQrRecovery(page, origin) {
    const localOrigin = new URL(origin).origin;
    const attempts = [];
    const pattern = '**/face/vendor/qrcode/qrcode.mjs*';
    const networkPattern = '**/api/network-info';
    const qrRoute = async (route) => {
        const url = new URL(route.request().url());
        assert.equal(url.origin, localOrigin);
        attempts.push(url.toString());
        if (attempts.length === 1) await route.abort('failed');
        else await route.continue();
    };
    const networkRoute = (route) => route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ avatar_url: 'http://192.168.0.2:8765/face/',
            lan_access: true, lan_reachable: true, bound_for_lan: true }),
    });
    await page.route(pattern, qrRoute);
    await page.route(networkPattern, networkRoute);
    try {
        await page.goto(localOrigin + '/face/', { waitUntil: 'domcontentloaded' });
        await page.waitForFunction(() => window.iris?.panel, null, { timeout: 120000 });
        if (!await page.locator('#side-panel').evaluate((el) => el.classList.contains('open'))) {
            await page.locator('#panel-toggle').click();
        }
        const section = page.locator('#panel-lan');
        const header = page.locator('#panel-lan .panel-section-header');
        if (!await section.evaluate((el) => el.classList.contains('collapsed'))) await header.click();
        await header.click();
        await page.waitForFunction(() => document.querySelector('#lan-qr')?.textContent
            .includes('QR local no disponible'), null, { timeout: 30000 });
        assert.equal(attempts.length, 1);
        const firstFailure = await page.locator('#lan-qr').textContent();
        // Closing and reopening Devices is the user's normal refresh action.
        // It does not change the LAN preference or invoke an endpoint writer.
        await header.click();
        await header.click();
        await page.waitForFunction(() => document.querySelector('#lan-qr img')?.src
            .startsWith('data:image/'), null, { timeout: 30000 });
        assert.equal(attempts.length, 2);
        assert.equal(new URL(attempts[0]).search, '');
        assert.equal(new URL(attempts[1]).search, '?retry=1');
        const qrImage = await page.locator('#lan-qr img').getAttribute('src');
        return { firstFailure, attempts, recovered: true,
            qrImageType: qrImage.slice(0, qrImage.indexOf(',')), qrDataUrlLength: qrImage.length,
            expectedFailedRequest: attempts[0] };
    } finally {
        await page.unroute(pattern, qrRoute);
        await page.unroute(networkPattern, networkRoute);
    }
}
