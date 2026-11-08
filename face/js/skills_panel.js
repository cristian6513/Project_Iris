// Skills Panel — Fase 2.A
//
// Surfaces Hermes' skill catalogue and lets the user check which
// ones IRIS should be aware of. Enabled skills are persisted in
// ~/.iris/enabled_skills.json (server-side) and injected into the
// system prompt by core/skills_bridge.py on every chat turn.
//
// Wiring:
//   • Click 🧰 button     → openModal() → fetch /api/skills/list
//   • Toggle checkbox     → POST /api/skills/toggle
//   • Click ❓            → expand inline description
//   • ESC / backdrop      → close
//
// Reuses .memory-* classes from styles.css since the visual language
// (modal, dark panel, gold buttons) matches by design.

import { irisAlert } from './iris_dialog.js?v=9';
(function () {
    'use strict';

    const state = {
        skills: [],         // server response, mutated in-place on toggle
        searchDebounce: null,
    };

    const els = {};

    function $(id) { return document.getElementById(id); }

    function openModal() {
        const m = els.modal;
        if (!m) return;
        m.classList.remove('hidden');
        m.setAttribute('aria-hidden', 'false');
        refresh();
        document.addEventListener('keydown', onKeyDown);
    }

    function closeModal() {
        const m = els.modal;
        if (!m) return;
        m.classList.add('hidden');
        m.setAttribute('aria-hidden', 'true');
        document.removeEventListener('keydown', onKeyDown);
    }

    function onKeyDown(e) {
        if (e.key === 'Escape') closeModal();
    }

    async function refresh() {
        els.stats.innerHTML = '<span>Cargando catálogo…</span>';
        els.list.innerHTML = '<p class="memory-empty">Consultando habilidades…</p>';
        try {
            const res = await fetch('/api/skills/list', { cache: 'no-store' });
            if (!res.ok) {
                const txt = await res.text().catch(() => '');
                throw new Error(`HTTP ${res.status} ${txt.slice(0, 200)}`);
            }
            const data = await res.json();
            state.skills = Array.isArray(data.skills) ? data.skills : [];
            render();
        } catch (err) {
            els.list.innerHTML =
                `<p class="memory-empty" style="color:#fca5a5">` +
                `Error cargando skills:<br><code>${escapeHtml(err.message || err)}</code></p>`;
            els.stats.innerHTML = '<span>—</span>';
        }
    }

    function render() {
        const total = state.skills.length;
        const enabled = state.skills.filter(s => s.enabled).length;
        const eligible = state.skills.filter(s => s.eligible).length;
        els.stats.innerHTML =
            `<span><strong>${total}</strong> skills · ` +
            `<strong>${eligible}</strong> elegibles · ` +
            `<strong>${enabled}</strong> habilitadas</span>`;

        const q = (els.search.value || '').trim().toLowerCase();
        const onlyEligible = els.onlyEligible.checked;
        const onlyEnabled = els.onlyEnabled.checked;

        const filtered = state.skills.filter(s => {
            if (onlyEligible && !s.eligible) return false;
            if (onlyEnabled && !s.enabled) return false;
            if (q && !(`${s.name} ${s.description}`.toLowerCase().includes(q))) return false;
            return true;
        });

        if (filtered.length === 0) {
            els.list.innerHTML = '<p class="memory-empty">Ninguna skill coincide con el filtro.</p>';
            return;
        }

        els.list.innerHTML = filtered.map(renderSkill).join('');

        // Wire up toggle handlers (delegate would be cleaner but per-row
        // listener is easy to reason about and the list is small).
        els.list.querySelectorAll('.skill-toggle').forEach(cb => {
            cb.addEventListener('change', onToggleChange);
        });
        els.list.querySelectorAll('.skill-help').forEach(b => {
            b.addEventListener('click', onHelpClick);
        });
    }

    function renderSkill(s) {
        const eligibilityBadge = s.eligible
            ? '<span class="skill-badge ok">✓ listo</span>'
            : '<span class="skill-badge warn">⚠ requiere setup</span>';
        const sourceBadge = (s.source === 'hermes-bundled')
            ? '<span class="skill-badge dim">bundled</span>'
            : `<span class="skill-badge dim">${escapeHtml(s.source || '')}</span>`;
        const emoji = s.emoji || '🧩';
        return `
            <div class="memory-turn skill-row${s.enabled ? ' enabled' : ''}" data-name="${escapeAttr(s.name)}">
                <label class="skill-main">
                    <input type="checkbox" class="skill-toggle"
                           data-name="${escapeAttr(s.name)}"
                           ${s.enabled ? 'checked' : ''}
                           ${(!s.eligible && !s.enabled) ? '' : ''} />
                    <span class="skill-emoji">${escapeHtml(emoji)}</span>
                    <span class="skill-name">${escapeHtml(s.name)}</span>
                    ${eligibilityBadge}
                    ${sourceBadge}
                    <button class="skill-help" data-name="${escapeAttr(s.name)}" title="Ver descripción">?</button>
                </label>
                <div class="skill-desc hidden" id="desc-${cssId(s.name)}">${escapeHtml(s.description || 'Sin descripción.')}</div>
            </div>
        `;
    }

    async function onToggleChange(e) {
        const name = e.target.dataset.name;
        const enabled = !!e.target.checked;
        // Optimistic update — flip state immediately, roll back on error.
        const skill = state.skills.find(s => s.name === name);
        if (skill) skill.enabled = enabled;
        const row = e.target.closest('.skill-row');
        if (row) row.classList.toggle('enabled', enabled);

        try {
            const res = await fetch('/api/skills/toggle', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name, enabled }),
            });
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            window.dispatchEvent(new CustomEvent('IRIS:skills-changed', {
                detail: { name, enabled },
            }));
            // Update count in stats.
            render(); // re-render keeps filters/search consistent
        } catch (err) {
            // Roll back.
            if (skill) skill.enabled = !enabled;
            e.target.checked = !enabled;
            if (row) row.classList.toggle('enabled', !enabled);
            void irisAlert({ title: 'No se guardó la preferencia', icon: 'warning',
                message: `No se pudo guardar la preferencia: ${err.message || err}` });
        }
    }

    function onHelpClick(e) {
        e.preventDefault();
        const name = e.target.dataset.name;
        const desc = document.getElementById(`desc-${cssId(name)}`);
        if (desc) desc.classList.toggle('hidden');
    }

    function escapeHtml(s) {
        return String(s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }
    function escapeAttr(s) {
        return escapeHtml(s);
    }
    function cssId(s) {
        return String(s).replace(/[^a-zA-Z0-9_-]/g, '_');
    }

    // Boot
    function init() {
        els.modal = $('skills-modal');
        els.btn = $('btn-skills');
        els.close = $('skills-close');
        els.refresh = $('skills-refresh');
        els.stats = $('skills-stats');
        els.list = $('skills-list');
        els.search = $('skills-search');
        els.onlyEligible = $('skills-only-eligible');
        els.onlyEnabled = $('skills-only-enabled');

        if (!els.modal || !els.btn) return; // panel not in DOM (older layout)

        els.btn.addEventListener('click', openModal);
        els.close.addEventListener('click', closeModal);
        els.refresh.addEventListener('click', refresh);
        // Backdrop click closes.
        els.modal.querySelector('.memory-backdrop').addEventListener('click', closeModal);

        // Filter inputs (debounced search).
        els.search.addEventListener('input', () => {
            clearTimeout(state.searchDebounce);
            state.searchDebounce = setTimeout(render, 150);
        });
        els.onlyEligible.addEventListener('change', render);
        els.onlyEnabled.addEventListener('change', render);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();

