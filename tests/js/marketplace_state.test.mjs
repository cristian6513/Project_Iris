import test from 'node:test';
import assert from 'node:assert/strict';

import {
    filterMarketplaceEntries,
    marketplaceEntries,
    marketplacePage,
    marketplaceSummary,
} from '../../face/js/marketplace_state.js';

const entries = marketplaceEntries(
    [
        { id: 'official/calendar', name: 'Calendar', installed: false, eligible: true, cli_command: 'hermes skills install calendar', category: 'official' },
        { id: 'local/files', name: 'Files', installed: true, eligible: true, category: 'local' },
    ],
    [
        { name: 'github', installed: true, transport: 'stdio', auth: 'oauth' },
        { name: 'notion', installed: false, transport: 'http', install_supported: true },
    ],
);

test('Marketplace combines skills and MCPs without losing their management action', () => {
    assert.deepEqual(marketplaceSummary(entries), { total: 4, skills: 2, mcps: 2, installed: 2 });
    assert.equal(entries.find((entry) => entry.name === 'Files')?.action, 'manage');
    assert.equal(entries.find((entry) => entry.name === 'Calendar')?.action, 'copy');
    assert.equal(entries.find((entry) => entry.name === 'notion')?.command, 'hermes mcp install notion');
});

test('Marketplace filters incrementally across type, state, category and text', () => {
    assert.deepEqual(
        filterMarketplaceEntries(entries, { kind: 'mcp' }).map((entry) => entry.name),
        ['github', 'notion'],
    );
    assert.deepEqual(
        filterMarketplaceEntries(entries, { state: 'installed' }).map((entry) => entry.name),
        ['Files', 'github'],
    );
    assert.deepEqual(
        filterMarketplaceEntries(entries, { category: 'official', query: 'cal' }).map((entry) => entry.name),
        ['Calendar'],
    );
});

test('Marketplace never synthesizes a shell command from an unsafe MCP name', () => {
    const [unsafe] = marketplaceEntries([], [{ name: 'demo\nwhoami', installed: false }]);
    assert.equal(unsafe.command, '');
    assert.equal(unsafe.action, 'details');
});

test('Marketplace namespaces equal source ids across Skills and MCP', () => {
    const entries = marketplaceEntries(
        [{ id: 'shared', name: 'Shared Skill' }],
        [{ id: 'shared', name: 'shared-mcp' }],
    );

    assert.deepEqual(entries.map((entry) => entry.id), ['skill/shared', 'mcp/shared']);
});

test('Marketplace pagination reports exactly what is visible', () => {
    const many = Array.from({ length: 125 }, (_, index) => ({ id: index }));
    const firstPage = marketplacePage(many, 80);
    const fullPage = marketplacePage(many, 160);

    assert.equal(firstPage.visible.length, 80);
    assert.equal(firstPage.total, 125);
    assert.equal(firstPage.remaining, 45);
    assert.equal(fullPage.visible.length, 125);
    assert.equal(fullPage.remaining, 0);
});
