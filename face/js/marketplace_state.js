function clean(value = '') {
    return String(value || '').trim();
}

function cleanCommand(value = '') {
    return clean(value).replace(/[\r\n]+/g, ' ');
}

function skillEntry(entry = {}) {
    const installed = Boolean(entry.installed);
    const eligible = entry.eligible !== false;
    const command = cleanCommand(entry.cli_command || entry.install_command);
    const sourceId = clean(entry.id || entry.name || 'unknown');
    return {
        ...entry,
        id: sourceId.startsWith('skill/') ? sourceId : `skill/${sourceId}`,
        kind: 'skill',
        kindLabel: 'Skill',
        category: clean(entry.category || 'general'),
        source: clean(entry.source || 'Hermes Skills Hub'),
        installed,
        state: installed ? 'installed' : (eligible ? 'ready' : 'setup'),
        command,
        action: installed ? 'manage' : (command ? 'copy' : (entry.homepage ? 'docs' : 'details')),
    };
}

function mcpEntry(entry = {}) {
    const name = clean(entry.name || 'mcp');
    const sourceId = clean(entry.id || name);
    const installed = Boolean(entry.installed);
    const safeCliName = /^[A-Za-z0-9._/-]+$/.test(name);
    const command = cleanCommand(
        entry.install_command
        || entry.post_install
        || (!installed && safeCliName && entry.install_supported === true
            ? `hermes mcp install ${name}`
            : ''),
    );
    const transport = clean(entry.transport || 'managed');
    const configurable = Boolean(command || entry.source || entry.description);
    return {
        ...entry,
        id: sourceId.startsWith('mcp/') ? sourceId : `mcp/${sourceId}`,
        name,
        kind: 'mcp',
        kindLabel: 'MCP',
        category: transport,
        source: clean(entry.source || 'Hermes MCP Catalog'),
        installed,
        eligible: configurable,
        state: installed ? 'installed' : (configurable ? 'ready' : 'setup'),
        command,
        tags: [transport, clean(entry.auth || 'none')].filter(Boolean),
        action: installed ? 'manage' : (command ? 'copy' : (entry.homepage ? 'docs' : 'details')),
    };
}

export function marketplaceEntries(skills = [], mcps = []) {
    return [
        ...(Array.isArray(skills) ? skills.map(skillEntry) : []),
        ...(Array.isArray(mcps) ? mcps.map(mcpEntry) : []),
    ];
}

export function marketplaceSummary(entries = []) {
    const list = Array.isArray(entries) ? entries : [];
    return {
        total: list.length,
        skills: list.filter((entry) => entry.kind === 'skill').length,
        mcps: list.filter((entry) => entry.kind === 'mcp').length,
        installed: list.filter((entry) => entry.installed).length,
    };
}

export function marketplacePage(entries = [], limit = 80) {
    const list = Array.isArray(entries) ? entries : [];
    const safeLimit = Math.max(1, Math.floor(Number(limit) || 80));
    const visible = list.slice(0, safeLimit);
    return {
        visible,
        total: list.length,
        remaining: Math.max(0, list.length - visible.length),
    };
}

export function filterMarketplaceEntries(entries = [], filters = {}) {
    const kind = clean(filters.kind || 'all').toLowerCase();
    const state = clean(filters.state || 'all').toLowerCase();
    const category = clean(filters.category || 'all').toLowerCase();
    const query = clean(filters.query).toLowerCase();
    return (Array.isArray(entries) ? entries : []).filter((entry) => {
        if (kind !== 'all' && entry.kind !== kind) return false;
        if (state !== 'all' && entry.state !== state) return false;
        if (category !== 'all' && clean(entry.category).toLowerCase() !== category) return false;
        if (!query) return true;
        const haystack = [
            entry.name,
            entry.description,
            entry.category,
            entry.source,
            entry.kindLabel,
            ...(Array.isArray(entry.tags) ? entry.tags : []),
        ].map(clean).join(' ').toLowerCase();
        return haystack.includes(query);
    });
}
