export function createWidget({ root, config, openApp }) {
    const button = document.createElement('button');
    button.className = 'iris-draft-open';
    button.type = 'button';
    button.textContent = 'Abrir app';
    // Widget ids have the form <app-id>.<widget-name>.
    button.onclick = () => openApp(config.widget_id.split('.')[0]);
    root.replaceChildren(button);
    return {
        setVisibility() {},
        configure() {},
        dispose() {
            button.onclick = null;
            root.replaceChildren();
        },
    };
}
