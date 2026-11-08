/** Minimal DOM adapter for behavioral tests of the real Home/Widgets controllers. */
export function homeDocument() {
    const doc = new Hub(); doc.defaultView = new Hub(); doc.visibilityState = 'visible';
    doc.createElement = (tag) => new Element(tag, doc); doc.createTextNode = (text) => { const n = new Element('#text', doc); n.textContent = text; return n; };
    doc.body = new Element('body', doc); doc.body.connected = true;
    return doc;
}
class Hub {
    constructor() { this.listeners = []; }
    addEventListener(type, fn, options = {}) { this.listeners.push({ type, fn, options }); }
    emit(type, extra = {}) {
        const ev = { target: this, key: '', clientX: 0, clientY: 0, defaultPrevented: false,
            preventDefault() { this.defaultPrevented = true; }, stopPropagation() { this.stopped = true; }, stopImmediatePropagation() { this.immediate = this.stopped = true; }, ...extra };
        const path = []; for (let n = this; n; n = n.parentElement) path.push(n);
        const run = (n, capture) => { for (const x of n.listeners) {
            if (x.type === type && !!x.options.capture === capture && !x.options.signal?.aborted) x.fn(ev);
            if (ev.immediate) break;
        } };
        for (const n of [...path].reverse()) { run(n, true); if (ev.stopped) return ev; }
        for (const n of path) { run(n, false); if (ev.stopped) break; }
        return ev;
    }
}
class Element extends Hub {
    constructor(tag, doc) {
        super(); Object.assign(this, { tag, document: doc, children: [], dataset: {}, attributes: {}, className: '', style: {}, disabled: false, hidden: false, text: '' });
        this.classList = { contains: (x) => this.className.split(' ').includes(x), add: (x) => this.classList.toggle(x, true), remove: (x) => this.classList.toggle(x, false),
            toggle: (x, value = !this.classList.contains(x)) => { this.className = [...this.className.split(' ').filter((c) => c && c !== x), ...(value ? [x] : [])].join(' '); } };
    }
    get isConnected() { return this.connected || !!this.parentElement?.isConnected; }
    get parentNode() { return this.parentElement; }
    get textContent() { return this.text + this.children.map((c) => c.textContent).join(''); }
    set textContent(value) { this.text = value; if (this.children) this.replaceChildren(); }
    setAttribute(name, value) { this.attributes[name] = String(value); }
    getAttribute(name) { return this.attributes[name] ?? null; }
    removeAttribute(name) { delete this.attributes[name]; }
    append(...nodes) { for (const n of nodes) { n.remove(); n.parentElement = this; this.children.push(n); } }
    prepend(...nodes) { for (const n of [...nodes].reverse()) { n.remove(); n.parentElement = this; this.children.unshift(n); } }
    after(n) { n.remove(); const p = this.parentElement; n.parentElement = p; p.children.splice(p.children.indexOf(this) + 1, 0, n); }
    before(n) { n.remove(); const p = this.parentElement; n.parentElement = p; p.children.splice(p.children.indexOf(this), 0, n); }
    remove() { if (this.parentElement) this.parentElement.children = this.parentElement.children.filter((n) => n !== this); this.parentElement = null; }
    replaceChildren(...nodes) { for (const n of this.children) n.parentElement = null; this.children = []; this.append(...nodes); }
    contains(n) { return this === n || this.children.some((c) => c.contains(n)); }
    matches(selector) {
        return selector.split(',').some((part) => {
            const s = part.trim(); if (s === 'button:not(:disabled)') return this.tag === 'button' && !this.disabled;
            if (s.startsWith('.')) return this.classList.contains(s.slice(1));
            if (s.startsWith('[')) { const [, key, value] = s.match(/^\[data-([a-z-]+)(?:="([^"]*)")?\]$/) || [];
                const name = key?.replace(/-([a-z])/g, (_, c) => c.toUpperCase()); return name in this.dataset && (value === undefined || this.dataset[name] === value); }
            return s === this.tag;
        });
    }
    closest(s) { return this.matches(s) ? this : this.parentElement?.closest(s) || null; }
    querySelectorAll(s) { return this.children.flatMap((c) => [...(c.matches(s) ? [c] : []), ...c.querySelectorAll(s)]); }
    querySelector(s) { return this.querySelectorAll(s)[0] || null; }
    focus() { this.document.activeElement = this; }
    click() { if (!this.disabled) this.emit('click'); }
    getBoundingClientRect() { return { left: 0, right: 400, top: 0, bottom: 40, width: 800, height: 600 }; }
    get offsetWidth() { return 280; }
    get offsetHeight() { return 300; }
}
