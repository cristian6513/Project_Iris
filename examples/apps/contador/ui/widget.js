import { createCounter } from './view.js';
export function createWidget(context) {
    const controller = createCounter({ ...context, widget: true });
    controller.mount(context.root);
    return { setVisibility: visible => controller.setVisibility({ visible }), configure() {}, dispose: () => controller.dispose() };
}
