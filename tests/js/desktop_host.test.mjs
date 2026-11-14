import { test } from 'node:test';
import assert from 'node:assert/strict';
import { publishDesktopFaceState, requestDesktopSetup } from '../../face/js/desktop_host.js';

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
    assert.equal(requestDesktopSetup(host, 'voice'), true);
    assert.deepEqual(messages.at(-1), [{ type: 'iris.desktop.configure', section: 'voice' }, 'http://127.0.0.1:9210']);
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

test('embedded Face reports startup to its validated Desktop origin for the current load', () => {
    const messages = [];
    const host = {
        location: { href: 'http://127.0.0.1:8766/face/?desktop_origin=http%3A%2F%2F127.0.0.1%3A9210' },
        parent: { postMessage: (...args) => messages.push(args) },
    };
    assert.equal(publishDesktopFaceState('ready', host), false);
    host.location.href += '&desktop_load=attempt-one';
    assert.equal(publishDesktopFaceState('ready', host), true);
    assert.equal(publishDesktopFaceState('error', host), true);
    assert.deepEqual(messages, ['ready', 'error'].map(status => [
        { type: 'iris.desktop.face-state', id: 'attempt-one', status }, 'http://127.0.0.1:9210',
    ]));
    host.location.href = 'http://127.0.0.1:8766/face/?desktop_origin=https://example.com&desktop_load=attempt-one';
    assert.equal(publishDesktopFaceState('ready', host), false);
    assert.equal(messages.length, 2);
});
