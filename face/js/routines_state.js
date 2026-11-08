function clean(value = '') {
    return String(value ?? '').trim();
}

export function formatCronSchedule(job = {}) {
    const explicit = clean(job.schedule_display);
    if (explicit) return explicit;

    const schedule = job.schedule;
    if (schedule && typeof schedule === 'object' && !Array.isArray(schedule)) {
        const display = clean(schedule.display);
        if (display) return display;
        const kind = clean(schedule.kind);
        const runAt = clean(schedule.run_at || schedule.runAt || schedule.at);
        if (kind && runAt) return `${kind} · ${runAt}`;
        if (runAt) return runAt;
        const expression = clean(schedule.expression || schedule.cron || schedule.value);
        if (expression) return expression;
        if (kind) return kind;
    }

    for (const candidate of [schedule, job.cron, job.when, job.repeat, job.next_run]) {
        if (candidate === null || candidate === undefined || candidate === '') continue;
        if (typeof candidate === 'string' || typeof candidate === 'number') {
            const value = clean(candidate);
            if (value) return value;
        }
        if (candidate && typeof candidate === 'object' && !Array.isArray(candidate)) {
            const value = clean(candidate.display || candidate.expression || candidate.value);
            if (value) return value;
        }
    }
    return 'schedule pendiente';
}
