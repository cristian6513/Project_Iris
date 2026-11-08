/**
 * «Trabajando en …» for the task Iris left running in the background
 * (core.background_tasks), with its own «Cancelar». This button stops the
 * background task only; «Detener» stops the current reply, never this.
 *
 * An ``unresolved`` task is a start Core could not confirm and may no longer
 * ask about (asking could create the work). It keeps its place until the user
 * releases it here, in two steps, knowing it may still run in the AI engine.
 */

const ID = /^[0-9a-f]{32}$/;
// ``uncertain``: Core is still confirming with the AI engine that the task started.
const STATES = new Set(['launching', 'uncertain', 'running', 'stopping', 'unresolved']);

/** Core's ``background_tasks`` payload, keeping only well-formed active tasks. */
export function normalizeBackgroundTasks(payload) {
    const rows = Array.isArray(payload?.tasks) ? payload.tasks : [];
    return rows.filter(row => ID.test(String(row?.id || '')) && STATES.has(row?.state)
        && typeof row?.title === 'string' && row.title.trim())
        .map(row => ({ id: row.id, title: row.title.trim().slice(0, 80), state: row.state }));
}

export class BackgroundTaskBar {
    /** ``host``: where the bar lives; ``before``: the node it goes in front of (optional). */
    constructor({ host, before = null, doc = globalThis.document, onCancel = () => {}, onRelease = () => {} } = {}) {
        Object.assign(this, { host, before, doc, onCancel, onRelease });
        this.node = null;
        this.task = null;
        this.cancelling = new Set();
        this.confirming = null;  // the id whose release waits for the second click
    }

    update(payload) {
        const [task] = normalizeBackgroundTasks(payload);  // Core runs one at a time
        if (!task) {
            this.node?.remove();
            Object.assign(this, { node: null, task: null, confirming: null });
            this.cancelling.clear();
            return;
        }
        if (!this.node) this.node = this._build();
        if (this.confirming !== task.id || task.state !== 'unresolved') this.confirming = null;
        this.task = task;
        this._render();
        if (!this.node.parentNode && this.host) {
            if (this.before && this.before.parentNode === this.host) this.host.insertBefore(this.node, this.before);
            else this.host.append(this.node);
        }
    }

    _render() {
        const { id, title, state } = this.task;
        const unresolved = state === 'unresolved';
        const stopping = !unresolved && (state === 'stopping' || this.cancelling.has(id));
        this.node.dataset.taskId = id;
        this.node.dataset.state = stopping ? 'stopping' : state;
        if (unresolved) {
            this.text.textContent = this.confirming === id
                ? `Si «${title}» empezó, puede seguir en el motor de IA. ¿Liberar para otras tareas?`
                : `Sin confirmar: «${title}» puede seguir en el motor de IA`;
            this.button.textContent = this.confirming === id ? 'Sí, liberar' : 'Liberar';
            this.button.disabled = false;
            this.button.setAttribute('aria-label', this.confirming === id
                ? `Confirmar: liberar «${title}» aunque pueda seguir en el motor de IA`
                : `Liberar «${title}», que no se pudo confirmar`);
            return;
        }
        this.text.textContent = stopping ? `Cancelando: ${title}…`
            : state === 'uncertain' ? `Comprobando si empezó: ${title}` : `Trabajando en: ${title}`;
        this.button.textContent = 'Cancelar';
        this.button.disabled = stopping;
        this.button.setAttribute('aria-label', `Cancelar la tarea en segundo plano «${title}»`);
    }

    _click() {
        const task = this.task;
        if (!task || !ID.test(task.id)) return;
        if (task.state === 'unresolved') {
            if (this.confirming !== task.id) {
                this.confirming = task.id;  // first click: say what releasing means
            } else {
                this.confirming = null;
                this.button.disabled = true;
                this.onRelease(task.id);
                return;
            }
        } else {
            if (this.cancelling.has(task.id)) return;
            this.cancelling.add(task.id);
            this.onCancel(task.id);
        }
        this._render();
    }

    _build() {
        const node = this.doc.createElement('div');
        node.className = 'background-task-bar';
        node.setAttribute('role', 'status');
        node.setAttribute('aria-live', 'polite');
        const pulse = this.doc.createElement('span');
        pulse.className = 'background-task-pulse';
        pulse.setAttribute('aria-hidden', 'true');
        this.text = this.doc.createElement('span');
        this.text.className = 'background-task-text';
        this.button = this.doc.createElement('button');
        this.button.type = 'button';
        this.button.className = 'background-task-cancel';
        this.button.addEventListener('click', () => this._click());
        node.append(pulse, this.text, this.button);
        return node;
    }
}
