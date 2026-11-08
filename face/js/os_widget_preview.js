import { osIconSvg } from './os_icons.js';

/** Catalog data only: no widget factory, service, timer, URL, audio or account access. */
export function renderWidgetPreview(documentLike, definition) {
    const node = (tag, cls, text = '') => { const n = documentLike.createElement(tag); n.className = cls; n.textContent = text; return n; };
    const sample = definition.preview;
    const kind = ['agenda', 'player', 'summary'].includes(sample?.layout) ? sample.layout : 'summary';
    const root = node('div', `os-widget-preview os-widget-preview-${kind}`);
    root.setAttribute('role', 'img'); root.setAttribute('aria-label', `Vista previa de ${definition.name}. Datos de ejemplo.`);
    // Visible sample labels remain readable, but the illustration adds no fake controls to Tab.
    const body = node('div', 'os-widget-preview-body'); body.setAttribute('aria-hidden', 'true');
    const mark = node('span', 'os-widget-preview-icon'); mark.innerHTML = osIconSvg(kind === 'agenda' ? 'calendar' : kind === 'player' ? 'player' : 'apps');
    body.append(mark, node('strong', 'os-widget-preview-title', sample?.title || definition.name));
    if (sample?.subtitle) body.append(node('span', 'os-widget-preview-subtitle', sample.subtitle));
    for (const item of (sample?.items || []).slice(0, 3)) {
        const row = node('div', 'os-widget-preview-row');
        row.append(node('strong', '', item.label));
        if (item.detail) row.append(node('span', '', item.detail));
        body.append(row);
    }
    if (kind === 'player') {
        const progress = node('div', 'os-widget-preview-progress'); progress.append(node('span', ''));
        const transport = node('div', 'os-widget-preview-transport');
        for (const icon of ['skip', 'player', 'skip']) { const span = node('span', ''); span.innerHTML = osIconSvg(icon); transport.append(span); }
        body.append(progress, transport);
    }
    root.append(body, node('span', 'os-widget-preview-sample', 'Vista previa · datos de ejemplo'));
    return root;
}
