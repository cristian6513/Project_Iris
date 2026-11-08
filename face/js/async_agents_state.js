function featureEnabled(payload = {}, names = []) {
    const maps = [payload, payload?.features, payload?.supports, payload?.capabilities]
        .filter(value => value && typeof value === 'object');
    return maps.some(map => names.some(name => map?.[name] === true));
}

function endpointMethod(payload = {}, names = []) {
    const endpoints = payload?.endpoints;
    if (!endpoints || typeof endpoints !== 'object') return '';
    for (const name of names) {
        const declared = endpoints[name];
        if (declared && typeof declared === 'object') {
            const method = String(declared.method || '').trim().toUpperCase();
            if (method) return method;
        }
    }
    return '';
}

export function asyncRunCapabilities(snapshot = {}) {
    const online = snapshot?.success === true;
    const advertised = snapshot?.capabilities || {};
    const supports = snapshot?.supports || {};
    const runsMethod = endpointMethod(advertised, ['runs']);
    const canCreate = online && (
        supports.run_submission === true
        || featureEnabled(advertised, ['run_submission', 'run_create'])
        || runsMethod === 'POST'
    );
    const canList = online && (
        supports.run_list === true
        || featureEnabled(advertised, ['run_list', 'runs_list', 'run_listing'])
        || endpointMethod(advertised, ['run_list', 'runs_list']) === 'GET'
        || runsMethod === 'GET'
    );
    const canInspect = online && (
        supports.run_status === true
        || featureEnabled(advertised, ['run_status'])
        || endpointMethod(advertised, ['run_status', 'run']) === 'GET'
    );
    const canStop = online && (
        supports.run_stop === true
        || featureEnabled(advertised, ['run_stop'])
        || endpointMethod(advertised, ['run_stop']) === 'POST'
    );

    const preciseContract = Boolean(runsMethod || canCreate || canList || canInspect || canStop);
    const legacyList = online && !preciseContract && supports.runs === true;
    return {
        online,
        canCreate,
        canList: canList || legacyList,
        canInspect,
        canStop,
        available: canCreate || canList || canInspect || canStop || legacyList,
    };
}
