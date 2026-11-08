import { test } from 'node:test';
import assert from 'node:assert/strict';
import { requestDesktopSetup } from '../../face/js/desktop_host.js';

test('ordinary browser keeps existing wizard action', () => {
    const host = {}; host.parent = host;
    assert.equal(requestDesktopSetup(host), false);
});
test('framed Face sends only a navigation request to the exact desktop origin', () => {
    const messages = [];
    const host = {
        location: { href: 'http://127.0.0.1:8766/face/?desktop_origin=http%3A%2F%2F127.0.0.1%3A9210' },
        parent: { postMessage: (...args) => messages.push(args) },
    };
    assert.equal(requestDesktopSetup(host), true);
    assert.deepEqual(messages, [[{ type: 'iris.desktop.configure' }, 'http://127.0.0.1:9210']]);
});
test('external, malformed and credentialed parent URLs receive no message', () => {
    for (const url of ['https://evil.test', 'http://127.0.0.1.evil.test:80', 'http://u:p@127.0.0.1:9999', 'broken', 'http://127.0.0.1:9999/path']) {
        const host = {
            location: { href: `http://127.0.0.1:8766/face/?desktop_origin=${encodeURIComponent(url)}` },
            parent: { postMessage: () => assert.fail('unexpected cross-origin message') },
        };
        assert.equal(requestDesktopSetup(host), false);
    }
});
