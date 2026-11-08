import assert from 'node:assert/strict';
import test from 'node:test';

import {
    capabilityCounts,
    groupCapabilities,
    normalizeCapabilitySnapshot,
} from '../../face/js/capability_center_state.js';

test('normalizes permission modes and runtime truth without inventing state', () => {
    const snapshot = normalizeCapabilitySnapshot({
        policy: {
            capabilities: [
                { id: 'camera.capture', label: 'Cámara', category: 'sensors', category_label: 'Sensores', mode: 'deny', runtime_enabled: true, tool_gate: { mapped: 1, enabled: 1, status: 'active' } },
                { id: 'files.read', label: 'Leer', category: 'data', category_label: 'Datos', mode: 'invalid' },
            ],
        },
        tools: [{ name: 'vision', enabled: true, source: 'builtin' }],
    });

    assert.equal(snapshot.capabilities[0].runtimeEnabled, true);
    assert.deepEqual(snapshot.capabilities[0].toolGate, { mapped: 1, enabled: 1, status: 'active' });
    assert.equal(snapshot.capabilities[1].runtimeEnabled, null);
    assert.equal(snapshot.capabilities[1].mode, 'ask');
    assert.deepEqual(snapshot.counts, { deny: 1, ask: 1, allow: 0 });
    assert.equal(snapshot.toolSummary.enabled, 1);
});

test('counts and groups preserve backend order', () => {
    const rows = [
        { id: 'one', category: 'sensors', categoryLabel: 'Sensores', mode: 'allow' },
        { id: 'two', category: 'data', categoryLabel: 'Datos', mode: 'ask' },
        { id: 'three', category: 'sensors', categoryLabel: 'Sensores', mode: 'deny' },
    ];

    assert.deepEqual(capabilityCounts(rows), { deny: 1, ask: 1, allow: 1 });
    assert.deepEqual(groupCapabilities(rows).map((group) => [group.id, group.items.length]), [
        ['sensors', 2],
        ['data', 1],
    ]);
});
