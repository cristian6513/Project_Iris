import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { openChatSurface } from '../../face/js/open_chat_surface.js';
import { SidePanel } from '../../face/js/panel.js';

test('opening chat leaves the desktop first, opens the existing chat module and focuses its unchanged draft', () => {
    const calls = [], draft = { value: 'Borrador que se conserva', focus() { calls.push('focus'); } };
    const panel = { chatInput: draft, _openCommandModule: (name) => calls.push(name), openChat: SidePanel.prototype.openChat };
    const app = { activityCanvas: { close() { calls.push('close-os'); } }, panel };
    openChatSurface(app);
    assert.deepEqual(calls, ['close-os', 'chat', 'focus']); assert.equal(draft.value, 'Borrador que se conserva');
    openChatSurface(app); assert.deepEqual(calls.slice(3), ['close-os', 'chat', 'focus']);
});
test('chat opens its section even if another section was expanded, without calling device or send actions', () => {
    const states = new Map([['chat', true], ['camera', false]]), calls = [];
    const section = (name) => ({ classList: { contains: () => states.get(name), add() { states.set(name, true); } } });
    const chat = section('chat'), camera = section('camera');
    const panel = { sections: { chat, camera }, isOpen: false, toggle(open) { assert.equal(open, true); this.isOpen = open; },
        panel: { querySelectorAll: () => [chat,camera] },
        _syncCommandSectionAccessibility() {},
        _toggleCommandSection(target) { assert.equal(target, chat); states.set('chat', false); states.set('camera', true); },
        _openCommandModule: SidePanel.prototype._openCommandModule,
        chatInput: { focus() { calls.push('focus'); } },
        acquireCamera() { throw new Error('Camera must remain off'); }, onSendMessage() { throw new Error('No send while opening'); } };
    SidePanel.prototype.openChat.call(panel);
    assert.equal(panel.isOpen,true); assert.equal(states.get('chat'),false); assert.deepEqual(calls,['focus']);
    SidePanel.prototype.openChat.call(panel); assert.equal(panel.isOpen,true); assert.equal(states.get('camera'),true);
});
test('the dock and the /chat command use the same existing conversation bridge', () => {
    const source = readFileSync(new URL('../../face/js/app.js', import.meta.url),'utf8');
    const shell = readFileSync(new URL('../../face/js/hermes_activity.js', import.meta.url),'utf8');
    assert.match(source,/onOpenChat: \(\) => openChatSurface\(this\)/);
    const branch=source.match(/if \(action === 'chat'\) \{([^]*?)\n        \}/)[1];
    const calls=[]; new Function('openChatSurface',branch).call({panel:{}},()=>calls.push('open'));
    assert.deepEqual(calls,['open']);
    assert.match(shell,/<button[^>]*data-os-chat[^>]*aria-label="Hablar con Iris por chat"/);
    assert.match(shell,/querySelector\('\[data-os-chat\]'\).*this\.onOpenChat\?\.\(\)/);
});
