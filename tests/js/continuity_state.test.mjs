import test from 'node:test';
import assert from 'node:assert/strict';

import {
    continuityDefaultProfile,
    continuityModeCopy,
    continuityTimeLabel,
    filterContinuitySessions,
    normalizeContinuitySnapshot,
} from '../../face/js/continuity_state.js';

const payload = {
    success: true,
    mode: 'native',
    active_profile: 'work',
    gateway_profile: 'default',
    has_more: true,
    current_session_id: 'session-2',
    profiles: [
        { name: 'default', is_default: true, model: 'openai/gpt-5.6' },
        { name: 'work', active: true, model: 'nous/hermes-4', gateway: 'running' },
    ],
    sessions: [
        {
            id: 'session-1',
            title: 'Informe mensual',
            preview: 'Analizando métricas',
            profile: 'work',
            model: 'nous/hermes-4',
            source: 'api_server',
            message_count: 12,
            workspace_id: 'ws_a',
            workspace_label: 'IRIS',
            last_active: 1000,
            archived: true,
        },
        {
            id: 'session-2',
            title: 'Corregir reproductor',
            profile: 'work',
            workspace_id: 'ws_b',
            workspace_label: 'Player',
            last_active: 2000,
            pinned: true,
            is_current: true,
        },
    ],
    workspaces: [
        { id: 'ws_a', label: 'IRIS', profile: 'work', session_count: 1 },
        { id: 'ws_b', label: 'Player', profile: 'work', session_count: 1 },
    ],
    supports: {
        profiles: true,
        sessions: true,
        session_update: true,
        session_messages: true,
        session_create: true,
        session_fork: true,
        resume_in_iris: true,
    },
};

test('normalizes only the Continuity Center client contract', () => {
    const state = normalizeContinuitySnapshot({
        ...payload,
        private_path: 'C:\\Users\\secret',
        sessions: [...payload.sessions, { id: '', title: 'invalid' }],
    });

    assert.equal(state.mode, 'native');
    assert.equal(state.gatewayProfile, 'default');
    assert.equal(state.hasMore, true);
    assert.equal(state.profiles.length, 2);
    assert.equal(state.sessions.length, 2);
    assert.equal(state.counts.sessions, 2);
    assert.equal(state.sessions[1].pinned, true);
    assert.equal(state.supports.sessionMessages, true);
    assert.equal(state.supports.sessionCreate, true);
    assert.equal(state.supports.sessionFork, true);
    assert.equal(state.supports.resumeInIris, true);
    assert.equal('private_path' in state, false);
});

test('keeps the Gateway session scope even when it is not a local profile', () => {
    const state = normalizeContinuitySnapshot({
        mode: 'partial',
        active_profile: 'default',
        gateway_profile: 'remote',
        profile_scope: 'remote',
        sessions: [{ id: 'remote-1', profile: 'remote' }],
    });

    assert.equal(state.profileScope, 'remote');
    assert.equal(state.sessions[0].profile, 'remote');
    assert.equal(continuityDefaultProfile(state), 'all');
});

test('prefers the running Gateway profile over a different sticky profile', () => {
    const state = normalizeContinuitySnapshot(payload);

    assert.equal(continuityDefaultProfile(state), 'default');
    assert.equal(continuityDefaultProfile(state, 'work'), 'work');
});

test('filters profile, workspace, text and archived sessions deterministically', () => {
    const { sessions } = normalizeContinuitySnapshot(payload);

    assert.deepEqual(filterContinuitySessions(sessions, { profile: 'work' }).map(({ id }) => id), ['session-2']);
    assert.deepEqual(filterContinuitySessions(sessions, { profile: 'work', includeArchived: true }).map(({ id }) => id), ['session-1', 'session-2']);
    assert.deepEqual(filterContinuitySessions(sessions, { workspace: 'ws_b' }).map(({ id }) => id), ['session-2']);
    assert.deepEqual(filterContinuitySessions(sessions, { query: 'métricas', includeArchived: true }).map(({ id }) => id), ['session-1']);
});

test('formats time and connectivity modes without inventing data', () => {
    assert.equal(continuityTimeLabel(0), 'Sin actividad registrada');
    assert.equal(continuityTimeLabel(940, 1000), 'Hace 1 min');
    assert.deepEqual(continuityModeCopy('native'), { label: 'Hermes nativo', tone: 'ready' });
    assert.deepEqual(continuityModeCopy('partial'), { label: 'Cobertura parcial', tone: 'partial' });
    assert.deepEqual(continuityModeCopy('unknown'), { label: 'Hermes desconectado', tone: 'offline' });
});
