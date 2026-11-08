export function createApp(context) {
    let root;
    return {
        mount(target) {
            root = target;
            const panel = document.createElement('section');
            panel.className = 'iris-draft-panel';
            const title = document.createElement('h2');
            title.textContent = context.app.name;
            const note = document.createElement('p');
            note.textContent = 'Ejemplo de ventana para tu app.';
            panel.append(title, note);
            root.replaceChildren(panel);
        },
        dispose() {
            root?.replaceChildren();
            root = null;
        },
    };
}
