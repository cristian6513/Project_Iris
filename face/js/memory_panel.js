/**
 * Memory Panel — face UI's 🧠 modal.
 *
 * Lets the user:
 *   • Browse local SQLite memory (filtered by date / keyword / session)
 *   • Multi-select past turns and "remind IRIS" — packages them as a
 *     priming summary block, sends it to Hermes, IRIS acknowledges
 *     verbally via TTS.
 *   • Delete selected turns from local memory.
 *   • Wipe everything (local).
 *   • Delete every persisted Hermes session (separate destructive button).
 *
 * Why this lives outside app.js:
 *   The modal is self-contained (own DOM, own fetch logic). Gluing it
 *   into app.js would mean threading another ~250 lines into the
 *   already-large class-based controller. As a separate module it's
 *   easier to remove if we ever change shape, and it loads later in
 *   the page so it doesn't block the avatar boot.
 *
 * Token estimator note:
 *   We estimate ~4 chars/token (a stable approximation for Spanish +
 *   Latin scripts). When the selection sum crosses 2k tokens we tint
 *   yellow, 5k red — visual nudge before the user pushes a chunk that
 *   would burn an entire context window on a reminder.
 */
import { irisConfirm } from './iris_dialog.js?v=9';

(() => {
    'use strict';

    const $ = (id) => document.getElementById(id);

    // ── DOM refs ────────────────────────────────────────────────
    const btnOpen      = $('btn-memory');
    const modal        = $('memory-modal');
    const btnClose     = $('memory-close');
    const statsEl      = $('memory-stats');
    const rangeSel     = $('memory-range');
    const searchInput  = $('memory-search');
    const allSessChk   = $('memory-allsessions');
    const listEl       = $('memory-list');
    const selCountEl   = $('memory-selection-count');
    const tokenEstEl   = $('memory-token-estimate');
    const btnSelectAll = $('memory-select-all');
    const btnSelectNone= $('memory-select-none');
    const btnRecall    = $('memory-recall');
    const btnDelete    = $('memory-delete');
    const btnClearAll  = $('memory-clear-all');
    const btnResetHermes = $('memory-reset-hermes');

    if (!btnOpen || !modal) {
        console.warn('[memory_panel] DOM nodes missing — modal disabled.');
        return;
    }

    // ── State ───────────────────────────────────────────────────
    /** @type {Array<{id:number, timestamp:number, role:string, content:string, emotion:string}>} */
    let turns = [];
    /** @type {Set<number>} */
    const selected = new Set();
    let searchDebounce = 0;

    // ── Open / close ────────────────────────────────────────────
    btnOpen.addEventListener('click', () => openModal());
    btnClose.addEventListener('click', () => closeModal());
    modal.querySelector('.memory-backdrop')?.addEventListener('click', () => closeModal());
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && !modal.classList.contains('hidden')) closeModal();
    });

    function openModal() {
        modal.classList.remove('hidden');
        modal.setAttribute('aria-hidden', 'false');
        refresh();
    }
    function closeModal() {
        modal.classList.add('hidden');
        modal.setAttribute('aria-hidden', 'true');
        selected.clear();
    }

    // ── Filters ─────────────────────────────────────────────────
    rangeSel.addEventListener('change', refresh);
    allSessChk.addEventListener('change', refresh);
    searchInput.addEventListener('input', () => {
        clearTimeout(searchDebounce);
        searchDebounce = setTimeout(refresh, 300);
    });

    function buildFilterParams() {
        const params = new URLSearchParams();
        const days = parseInt(rangeSel.value, 10);
        if (days > 0) {
            const from = (Date.now() / 1000) - days * 86400;
            params.set('from_ts', String(from));
        }
        const q = (searchInput.value || '').trim();
        if (q) params.set('q', q);
        if (allSessChk.checked) params.set('all_sessions', 'true');
        params.set('limit', '500');
        return params;
    }

    // ── Fetch + render ──────────────────────────────────────────
    async function refresh() {
        listEl.innerHTML = '<p class="memory-empty">Cargando…</p>';
        try {
            const [statsRes, listRes] = await Promise.all([
                fetch('/api/memory/stats'),
                fetch('/api/memory/list?' + buildFilterParams().toString()),
            ]);
            if (!statsRes.ok || !listRes.ok) {
                throw new Error(`HTTP ${statsRes.status}/${listRes.status}`);
            }
            const stats = await statsRes.json();
            const data  = await listRes.json();
            renderStats(stats, data.total);
            renderList(data.turns || []);
        } catch (e) {
            listEl.innerHTML = `<p class="memory-empty">Error: ${escapeHtml(e.message)}</p>`;
        }
    }

    function renderStats(stats, filteredTotal) {
        const total = stats.count || 0;
        const oldest = stats.oldest_ts ? new Date(stats.oldest_ts * 1000) : null;
        const parts = [`${total} turnos en total`];
        if (oldest) parts.push(`desde ${oldest.toLocaleDateString()}`);
        parts.push(`${filteredTotal} en este filtro`);
        statsEl.textContent = parts.join(' · ');
    }

    function renderList(rows) {
        turns = rows;
        if (!rows.length) {
            listEl.innerHTML = '<p class="memory-empty">Nada coincide con el filtro.</p>';
            updateSelectionUI();
            return;
        }
        const html = rows.map(t => renderTurn(t)).join('');
        listEl.innerHTML = html;
        // Wire checkboxes + row clicks.
        listEl.querySelectorAll('.memory-turn').forEach(node => {
            const id = parseInt(node.dataset.id, 10);
            const cb = node.querySelector('input[type="checkbox"]');
            const sync = () => {
                if (cb.checked) { selected.add(id); node.classList.add('selected'); }
                else            { selected.delete(id); node.classList.remove('selected'); }
                updateSelectionUI();
            };
            cb.addEventListener('change', sync);
            node.addEventListener('click', (e) => {
                if (e.target === cb) return;
                cb.checked = !cb.checked;
                sync();
            });
        });
        // Re-apply prior selection on re-render.
        for (const id of selected) {
            const node = listEl.querySelector(`[data-id="${id}"]`);
            if (node) {
                node.classList.add('selected');
                node.querySelector('input').checked = true;
            }
        }
        updateSelectionUI();
    }

    function renderTurn(t) {
        const when = new Date(t.timestamp * 1000);
        const stamp = when.toLocaleString();
        const role = t.role === 'alice' ? 'IRIS' : (t.role === 'user' ? 'Tú' : t.role);
        const roleClass = `role-${t.role}`;
        const content = (t.content || '').slice(0, 600);
        return `
            <label class="memory-turn" data-id="${t.id}">
                <input type="checkbox" />
                <div class="memory-turn-body">
                    <div class="memory-turn-meta">
                        <span class="memory-turn-role ${roleClass}">${escapeHtml(role)}</span>
                        <span>${escapeHtml(stamp)}</span>
                        ${t.emotion && t.emotion !== 'neutral' ? `<span>· ${escapeHtml(t.emotion)}</span>` : ''}
                    </div>
                    <div class="memory-turn-content">${escapeHtml(content)}</div>
                </div>
            </label>
        `;
    }

    // ── Selection handling ──────────────────────────────────────
    btnSelectAll.addEventListener('click', () => {
        for (const t of turns) selected.add(t.id);
        listEl.querySelectorAll('.memory-turn').forEach(n => {
            n.classList.add('selected');
            n.querySelector('input').checked = true;
        });
        updateSelectionUI();
    });
    btnSelectNone.addEventListener('click', () => {
        selected.clear();
        listEl.querySelectorAll('.memory-turn').forEach(n => {
            n.classList.remove('selected');
            n.querySelector('input').checked = false;
        });
        updateSelectionUI();
    });

    function updateSelectionUI() {
        const count = selected.size;
        selCountEl.textContent = `${count} ${count === 1 ? 'turno seleccionado' : 'turnos seleccionados'}`;
        const tokens = estimateTokens();
        tokenEstEl.textContent = `~${tokens.toLocaleString()} tokens`;
        tokenEstEl.classList.toggle('warn',   tokens >= 2000 && tokens < 5000);
        tokenEstEl.classList.toggle('danger', tokens >= 5000);
        const has = count > 0;
        btnRecall.disabled = !has;
        btnDelete.disabled = !has;
    }

    function estimateTokens() {
        // ~4 chars/token for Spanish prose. Add 50 token overhead for
        // the priming wrapper text the server adds.
        let chars = 0;
        for (const t of turns) {
            if (selected.has(t.id)) chars += (t.content || '').length + 30;
        }
        return Math.ceil(chars / 4) + 50;
    }

    // ── Action handlers ─────────────────────────────────────────

    btnRecall.addEventListener('click', async () => {
        if (!selected.size) return;
        const tokens = estimateTokens();
        if (tokens > 5000 && !await irisConfirm({
            title: 'Recordar a IRIS', icon: 'info',
            message: `Vas a enviar unos ${tokens} tokens como recordatorio a tu proveedor de IA.`,
            question: '¿Deseas continuar?', confirmLabel: 'Enviar recordatorio',
        })) return;
        setLoading(btnRecall, '⏳ Enviando…');
        try {
            const res = await fetch('/api/memory/recall', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ turn_ids: Array.from(selected) }),
            });
            if (!res.ok) {
                const txt = await res.text();
                throw new Error(`HTTP ${res.status}: ${txt.slice(0, 200)}`);
            }
            const data = await res.json();
            flashStatus(`✓ ${data.turns_count} turnos recordados — IRIS respondió.`);
            // Close modal so the user sees the avatar's TTS reply.
            setTimeout(closeModal, 600);
        } catch (e) {
            flashStatus(`✗ Error: ${e.message}`, true);
        } finally {
            restore(btnRecall, '💬 Recordar a IRIS');
        }
    });

    btnDelete.addEventListener('click', async () => {
        if (!selected.size) return;
        if (!await irisConfirm({
            title: 'Borrar de la memoria', icon: 'trash', tone: 'danger',
            message: `Se borrarán ${selected.size} turno(s) de la memoria local.`,
            question: '¿Deseas continuar?', confirmLabel: 'Borrar', confirmIcon: 'trash',
        })) return;
        setLoading(btnDelete, '⏳ Borrando…');
        try {
            // Use the clear endpoint with a turn_ids body via DELETE — the
            // server has a separate selective endpoint via POST. Actually
            // we don't have one — we'll fall back to clearing per-id by
            // calling a delete via a one-off body. Server-side we expose
            // selective delete through the memory module; let's add it
            // inline through the existing clear endpoint pattern.
            const res = await fetch('/api/memory/delete-selected', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ turn_ids: Array.from(selected) }),
            });
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const data = await res.json();
            flashStatus(`✓ ${data.deleted} turnos borrados.`);
            selected.clear();
            await refresh();
        } catch (e) {
            flashStatus(`✗ Error: ${e.message}`, true);
        } finally {
            restore(btnDelete, '🗑️ Borrar seleccionados');
        }
    });

    btnClearAll.addEventListener('click', async () => {
        const allSess = allSessChk.checked;
        const scope = allSess ? 'all' : 'session';
        const label = allSess ? 'TODA la memoria de TODAS las sesiones' : 'la sesión actual';
        if (!await irisConfirm({
            title: 'Limpiar memoria', icon: 'trash', tone: 'danger',
            message: `Vas a borrar ${label}. Esta acción no se puede deshacer.`,
            question: '¿Seguro?', confirmLabel: 'Borrar', confirmIcon: 'trash',
        })) return;
        if (allSess && !await irisConfirm({
            title: 'Última confirmación', icon: 'warning', tone: 'danger',
            message: 'Se borrará todo el historial de todas las sesiones.',
            question: '¿Borrarlo de verdad?', confirmLabel: 'Borrar todo', confirmIcon: 'trash',
        })) return;
        setLoading(btnClearAll, '⏳ Borrando…');
        try {
            const params = new URLSearchParams({ scope, confirm: 'true' });
            const res = await fetch('/api/memory/clear?' + params.toString(), {
                method: 'DELETE',
            });
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const data = await res.json();
            flashStatus(`✓ ${data.deleted} turnos borrados.`);
            selected.clear();
            await refresh();
        } catch (e) {
            flashStatus(`✗ Error: ${e.message}`, true);
        } finally {
            restore(btnClearAll, '⚠️ Limpiar todo');
        }
    });

    if (btnResetHermes) btnResetHermes.addEventListener('click', async () => {
        if (!await irisConfirm({
            title: 'Borrar sesiones de Hermes', icon: 'trash', tone: 'danger',
            message: 'Se borrarán todas las sesiones guardadas por Hermes, no solo la conversación actual. La memoria local de IRIS no se toca.',
            question: '¿Deseas continuar?', confirmLabel: 'Borrar sesiones', confirmIcon: 'trash',
        })) return;
        if (!await irisConfirm({
            title: 'Última confirmación', icon: 'warning', tone: 'danger',
            message: 'Las sesiones de Hermes no se pueden recuperar después.',
            question: '¿Borrarlas de verdad?', confirmLabel: 'Borrar sesiones', confirmIcon: 'trash',
        })) return;
        setLoading(btnResetHermes, '⏳ Reseteando…');
        try {
            const res = await fetch('/api/memory/reset-hermes', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ confirm_all: true }),
            });
            if (!res.ok) {
                const txt = await res.text();
                throw new Error(`HTTP ${res.status}: ${txt.slice(0, 200)}`);
            }
            flashStatus('✓ Sesiones persistidas de Hermes eliminadas.');
        } catch (e) {
            flashStatus(`✗ Error: ${e.message}`, true);
        } finally {
            restore(btnResetHermes, 'Borrar sesiones de Hermes');
        }
    });

    // ── Helpers ─────────────────────────────────────────────────
    function setLoading(btn, label) {
        btn.dataset.original = btn.textContent;
        btn.textContent = label;
        btn.disabled = true;
    }
    function restore(btn, fallback) {
        btn.textContent = btn.dataset.original || fallback;
        btn.disabled = false;
        updateSelectionUI();   // re-evaluate disabled state from selection
    }
    function flashStatus(msg, isError = false) {
        statsEl.textContent = msg;
        statsEl.style.color = isError ? '#ff8a8a' : '#8aff9a';
        setTimeout(() => {
            statsEl.style.color = '';
            // Re-fetch stats so the user sees the updated count after
            // clear/delete operations.
            fetch('/api/memory/stats').then(r => r.json()).then(s =>
                renderStats(s, turns.length)
            ).catch(() => {});
        }, 2200);
    }
    function escapeHtml(s) {
        return String(s).replace(/[&<>"']/g, c => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
        }[c]));
    }
})();
