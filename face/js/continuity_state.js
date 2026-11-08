const safeText = (value, max = 180) => String(value ?? '').replace(/\s+/g, ' ').trim().slice(0, max);

const safeCount = (value) => {
    const parsed = Number(value);
    return Number.isFinite(parsed) && parsed > 0 ? Math.floor(parsed) : 0;
};

const safeTimestamp = (value) => {
    const parsed = Number(value);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : 0;
};

export function normalizeContinuitySnapshot(payload = {}) {
    const profiles = Array.isArray(payload?.profiles) ? payload.profiles : [];
    const sessions = Array.isArray(payload?.sessions) ? payload.sessions : [];
    const workspaces = Array.isArray(payload?.workspaces) ? payload.workspaces : [];
    const normalizedProfiles = profiles.map((profile) => ({
        name: safeText(profile?.name, 64) || 'default',
        active: profile?.active === true,
        isDefault: profile?.is_default === true,
        model: safeText(profile?.model, 160),
        provider: safeText(profile?.provider, 80),
        gateway: safeText(profile?.gateway, 40) || 'unknown',
        distribution: safeText(profile?.distribution, 120),
    }));
    const normalizedSessions = sessions.map((session) => ({
        id: safeText(session?.id, 192),
        title: safeText(session?.title, 140) || 'Sesión sin título',
        preview: safeText(session?.preview, 260),
        profile: safeText(session?.profile, 64)
            || safeText(payload?.gateway_profile, 64)
            || safeText(payload?.active_profile, 64)
            || 'gateway',
        model: safeText(session?.model, 160),
        source: safeText(session?.source, 48) || 'Hermes',
        messageCount: safeCount(session?.message_count),
        inputTokens: safeCount(session?.input_tokens),
        outputTokens: safeCount(session?.output_tokens),
        lastActive: safeTimestamp(session?.last_active),
        archived: session?.archived === true,
        pinned: session?.pinned === true,
        workspaceId: safeText(session?.workspace_id, 80),
        workspaceLabel: safeText(session?.workspace_label, 100),
        isCurrent: session?.is_current === true,
    })).filter(({ id }) => id);
    const normalizedWorkspaces = workspaces.map((workspace) => ({
        id: safeText(workspace?.id, 80),
        label: safeText(workspace?.label, 100) || 'Workspace',
        profile: safeText(workspace?.profile, 64) || 'default',
        sessionCount: safeCount(workspace?.session_count),
        lastActive: safeTimestamp(workspace?.last_active),
    })).filter(({ id }) => id);
    return {
        success: payload?.success === true,
        mode: ['native', 'partial', 'offline'].includes(payload?.mode) ? payload.mode : 'offline',
        activeProfile: safeText(payload?.active_profile, 64) || 'default',
        gatewayProfile: safeText(payload?.gateway_profile, 64),
        profileScope: payload?.profile_scope === 'remote' ? 'remote' : 'local',
        currentSessionId: safeText(payload?.current_session_id, 192),
        hermesVersion: safeText(payload?.hermes_version, 120),
        apiReachable: payload?.api_reachable === true,
        capabilitiesAvailable: payload?.capabilities_available === true,
        hasMore: payload?.has_more === true,
        profiles: normalizedProfiles,
        sessions: normalizedSessions,
        workspaces: normalizedWorkspaces,
        counts: {
            profiles: normalizedProfiles.length,
            sessions: normalizedSessions.length,
            workspaces: normalizedWorkspaces.length,
        },
        supports: {
            profiles: payload?.supports?.profiles === true,
            profileActivation: payload?.supports?.profile_activation === true,
            sessions: payload?.supports?.sessions === true,
            sessionUpdate: payload?.supports?.session_update === true,
            sessionMessages: payload?.supports?.session_messages === true,
            sessionCreate: payload?.supports?.session_create === true,
            sessionFork: payload?.supports?.session_fork === true,
            workspaceMetadata: payload?.supports?.workspace_metadata === true,
            resumeInIris: payload?.supports?.resume_in_iris === true,
        },
        notice: safeText(payload?.notice, 320),
        error: safeText(payload?.error, 320),
    };
}

export function filterContinuitySessions(sessions = [], filters = {}) {
    const query = safeText(filters.query, 180).toLocaleLowerCase();
    const profile = safeText(filters.profile, 64);
    const workspace = safeText(filters.workspace, 80);
    const includeArchived = filters.includeArchived === true;
    return sessions.filter((session) => {
        if (!includeArchived && session.archived) return false;
        if (profile && profile !== 'all' && session.profile !== profile) return false;
        if (workspace && workspace !== 'all' && session.workspaceId !== workspace) return false;
        if (!query) return true;
        return [session.title, session.preview, session.model, session.source, session.workspaceLabel]
            .some((value) => String(value || '').toLocaleLowerCase().includes(query));
    });
}

export function continuityDefaultProfile(snapshot = {}, current = 'all') {
    const profiles = Array.isArray(snapshot?.profiles) ? snapshot.profiles : [];
    const hasProfile = (name) => profiles.some((profile) => profile?.name === name);
    if (current !== 'all' && hasProfile(current)) return current;
    if (hasProfile(snapshot?.gatewayProfile)) return snapshot.gatewayProfile;
    if (Array.isArray(snapshot?.sessions) && snapshot.sessions.length) return 'all';
    if (hasProfile(snapshot?.activeProfile)) return snapshot.activeProfile;
    return 'all';
}

export function continuityTimeLabel(timestamp, now = Date.now() / 1000) {
    const value = Number(timestamp || 0);
    if (!Number.isFinite(value) || value <= 0) return 'Sin actividad registrada';
    const seconds = Math.max(0, now - value);
    if (seconds < 60) return 'Ahora';
    if (seconds < 3600) return `Hace ${Math.floor(seconds / 60)} min`;
    if (seconds < 86400) return `Hace ${Math.floor(seconds / 3600)} h`;
    if (seconds < 604800) return `Hace ${Math.floor(seconds / 86400)} d`;
    return new Date(value * 1000).toLocaleDateString([], { day: '2-digit', month: 'short', year: 'numeric' });
}

export function continuityModeCopy(mode = 'offline') {
    if (mode === 'native') return { label: 'Hermes nativo', tone: 'ready' };
    if (mode === 'partial') return { label: 'Cobertura parcial', tone: 'partial' };
    return { label: 'Hermes desconectado', tone: 'offline' };
}
