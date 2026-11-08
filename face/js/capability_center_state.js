export const PERMISSION_MODES = Object.freeze({
    deny: { label: 'Bloquear', tone: 'deny' },
    ask: { label: 'Preguntar', tone: 'ask' },
    allow: { label: 'Permitir', tone: 'allow' },
});

const clean = (value, fallback = '') => String(value ?? fallback).trim();

export function capabilityCounts(capabilities = []) {
    const counts = { deny: 0, ask: 0, allow: 0 };
    for (const item of capabilities) {
        const mode = clean(item?.mode).toLowerCase();
        if (Object.hasOwn(counts, mode)) counts[mode] += 1;
    }
    return counts;
}

export function normalizeCapabilitySnapshot(payload = {}) {
    const source = payload && typeof payload === 'object' ? payload : {};
    const policy = source.policy && typeof source.policy === 'object' ? source.policy : {};
    const capabilities = (Array.isArray(policy.capabilities) ? policy.capabilities : [])
        .map((item) => ({
            id: clean(item?.id),
            label: clean(item?.label, 'Capacidad'),
            category: clean(item?.category, 'other'),
            categoryLabel: clean(item?.category_label, 'Otros'),
            description: clean(item?.description),
            note: clean(item?.note),
            risk: clean(item?.risk, 'medium'),
            mode: Object.hasOwn(PERMISSION_MODES, clean(item?.mode)) ? clean(item.mode) : 'ask',
            runtimeEnabled: typeof item?.runtime_enabled === 'boolean' ? item.runtime_enabled : null,
            hermesTools: Array.isArray(item?.hermes_tools) ? item.hermes_tools.map(clean).filter(Boolean) : [],
            toolGate: item?.tool_gate && typeof item.tool_gate === 'object' ? {
                mapped: Number(item.tool_gate.mapped || 0),
                enabled: Number(item.tool_gate.enabled || 0),
                status: clean(item.tool_gate.status, 'local'),
            } : { mapped: 0, enabled: 0, status: 'local' },
        }))
        .filter((item) => item.id);
    const tools = (Array.isArray(source.tools) ? source.tools : [])
        .map((item) => ({
            name: clean(item?.name),
            description: clean(item?.description, 'Toolset de Hermes'),
            source: clean(item?.source, 'builtin'),
            enabled: item?.enabled === true,
        }))
        .filter((item) => item.name);
    const mcp = source.mcp && typeof source.mcp === 'object' ? source.mcp : {};
    const audit = (Array.isArray(policy.audit) ? policy.audit : []).map((item) => ({
        at: clean(item?.at),
        kind: clean(item?.kind, 'event'),
        capability: clean(item?.capability),
        verdict: clean(item?.verdict),
        detail: clean(item?.detail),
    }));
    return {
        schema: clean(source.schema),
        generatedAt: clean(source.generated_at),
        capabilities,
        counts: capabilityCounts(capabilities),
        presets: Array.isArray(policy.presets) ? policy.presets.map(clean).filter(Boolean) : [],
        audit,
        actionGrants: (Array.isArray(policy.action_grants) ? policy.action_grants : []).map(item => ({
            id: clean(item?.id), app: clean(item?.app), action: clean(item?.action), label: clean(item?.label),
        })).filter(item => /^[a-f0-9]{64}$/.test(item.id)),
        hermes: source.hermes && typeof source.hermes === 'object' ? source.hermes : {},
        guards: source.guards && typeof source.guards === 'object' ? source.guards : {},
        tools,
        toolSummary: {
            total: tools.length,
            enabled: tools.filter((item) => item.enabled).length,
            disabled: tools.filter((item) => !item.enabled).length,
        },
        mcp: {
            available: mcp.available === true,
            installed: Array.isArray(mcp.installed) ? mcp.installed : [],
            installedCount: Number(mcp.installed_count || 0),
            enabledCount: Number(mcp.enabled_count || 0),
        },
        invariants: source.invariants && typeof source.invariants === 'object' ? source.invariants : {},
    };
}

export function groupCapabilities(capabilities = []) {
    const groups = [];
    const byId = new Map();
    for (const capability of capabilities) {
        const key = capability.category || 'other';
        if (!byId.has(key)) {
            const group = { id: key, label: capability.categoryLabel || 'Otros', items: [] };
            byId.set(key, group);
            groups.push(group);
        }
        byId.get(key).items.push(capability);
    }
    return groups;
}
