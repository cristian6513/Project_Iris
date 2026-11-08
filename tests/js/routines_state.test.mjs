import assert from 'node:assert/strict';
import test from 'node:test';

import { formatCronSchedule } from '../../face/js/routines_state.js';

test('Routines prefers the display text returned by current Hermes cron jobs', () => {
    assert.equal(formatCronSchedule({
        schedule: { kind: 'once', run_at: '2026-08-27T17:40:00-05:00', display: 'once at 2026-08-27 17:40' },
        schedule_display: 'once at 2026-08-27 17:40',
    }), 'once at 2026-08-27 17:40');
});

test('Routines never renders a structured schedule as object Object', () => {
    assert.equal(formatCronSchedule({
        schedule: { kind: 'interval', run_at: '2026-09-01T09:00:00Z' },
    }), 'interval · 2026-09-01T09:00:00Z');
    assert.notEqual(formatCronSchedule({ schedule: { times: 2 } }), '[object Object]');
});

test('Routines keeps legacy scalar schedules compatible', () => {
    assert.equal(formatCronSchedule({ cron: '0 9 * * 1-5' }), '0 9 * * 1-5');
    assert.equal(formatCronSchedule({}), 'schedule pendiente');
});
