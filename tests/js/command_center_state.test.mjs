import test from 'node:test';
import assert from 'node:assert/strict';

import {
    commandCenterSummary,
    gatewayViewFromProbe,
} from '../../face/js/command_center_state.js';

test('Gateway requires a healthy /health response before showing online', () => {
    const healthy = gatewayViewFromProbe({ running: true, healthy: true });
    const occupied = gatewayViewFromProbe({ running: true, healthy: false, health_error: 'invalid service' });
    const stopped = gatewayViewFromProbe({ running: false, healthy: false });
    const remote = gatewayViewFromProbe({ managed: false, running: true, healthy: true });

    assert.equal(healthy.state, 'online');
    assert.equal(healthy.action, 'restart');
    assert.equal(occupied.state, 'attention');
    assert.equal(occupied.action, 'probe');
    assert.equal(stopped.state, 'offline');
    assert.equal(stopped.action, 'start');
    assert.equal(remote.state, 'online');
    assert.equal(remote.action, 'probe');
});

test('Command Center never claims to be live while Core is disconnected', () => {
    assert.deepEqual(
        commandCenterSummary({ runtimeConnected: false, gatewayState: 'online' }),
        { state: 'offline', label: 'Reconectando' },
    );
    assert.deepEqual(
        commandCenterSummary({ runtimeConnected: true, gatewayState: 'online' }),
        { state: 'online', label: 'Todo listo' },
    );
    assert.deepEqual(
        commandCenterSummary({ runtimeConnected: true, gatewayState: 'offline' }),
        { state: 'ready', label: 'IRIS OS abierto · IA sin conexión' },
    );
});
