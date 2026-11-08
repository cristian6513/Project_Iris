/** Data-only twin of core/app_extension_contract.py. */
export const EXTENSION_SCHEMA = 'iris.app-extension/v1';
export const ARGUMENT_TYPES = Object.freeze(['string', 'integer', 'number', 'boolean', 'object', 'array']);
// Who may call an operation: both, only the app's own window and widgets, or only Iris.
export const AUDIENCES = Object.freeze(['all', 'ui', 'iris']);
const object = value => !!value && typeof value === 'object' && !Array.isArray(value);
const NAME = /^[a-z][a-z0-9_]{0,47}$/;
export function validateAppExtension(raw, sdk, errors) {
    if (!object(raw)) { errors.push('extension: debe ser un objeto'); return undefined; }
    if (sdk !== 2) errors.push('extension: requiere sdkVersion 2');
    for (const key of Object.keys(raw)) if (!['schema', 'python', 'skill', 'operations'].includes(key)) errors.push(`extension.${key}: campo desconocido`);
    if (raw.schema !== EXTENSION_SCHEMA) errors.push(`extension.schema: debe ser «${EXTENSION_SCHEMA}»`);
    if (Object.hasOwn(raw, 'python') && (typeof raw.python !== 'string' || !/^[a-z][a-z0-9_]{0,63}\.py$/.test(raw.python))) errors.push('extension.python: archivo .py de backend/, sin carpetas');
    const skill = Object.hasOwn(raw, 'skill') ? raw.skill : false;
    if (typeof skill !== 'boolean') errors.push('extension.skill: true o false');
    if (!raw.python && !skill) errors.push('extension: declara Python o una skill');
    const operations = Object.hasOwn(raw, 'operations') ? raw.operations : [], normalized = [], seen = new Set();
    if (!Array.isArray(operations) || operations.length > 16) errors.push('extension.operations: lista de hasta 16 operaciones');
    else for (const op of operations) {
        if (!object(op)) { errors.push('extension.operations: cada operación debe ser un objeto'); continue; }
        for (const key of Object.keys(op)) if (!['action', 'label', 'effect', 'arguments', 'audience'].includes(key)) errors.push(`extension.operations.${key}: campo desconocido`);
        if (typeof op.action !== 'string' || !NAME.test(op.action)) errors.push('extension.operations.action: identificador en minúsculas, hasta 48 caracteres');
        else if (seen.has(op.action)) errors.push('extension.operations.action: operación repetida');
        else seen.add(op.action);
        if (typeof op.label !== 'string' || !op.label.trim() || op.label.length > 80 || /[\u0000-\u001f\u007f]/.test(op.label)) errors.push('extension.operations.label: texto de 1 a 80 caracteres, sin controles');
        if (!['read', 'local', 'external'].includes(op.effect)) errors.push('extension.operations.effect: read, local o external');
        const args = Object.hasOwn(op, 'arguments') ? op.arguments : {};
        if (!object(args) || Object.keys(args).length > 16 || Object.entries(args).some(([key, value]) => !NAME.test(key) || !ARGUMENT_TYPES.includes(value))) errors.push('extension.operations.arguments: hasta 16 nombres con tipos string, integer, number, boolean, object o array');
        const audience = Object.hasOwn(op, 'audience') ? op.audience : 'all';
        if (!AUDIENCES.includes(audience)) errors.push('extension.operations.audience: all, ui o iris');
        // Omitted when it is the default, so earlier manifests normalize as before.
        normalized.push({ action: op.action, label: typeof op.label === 'string' ? op.label.trim() : op.label, effect: op.effect, arguments: args,
            ...(audience !== 'all' ? { audience } : {}) });
    }
    if (normalized.length && !raw.python) errors.push('extension.operations: requieren Python');
    return { schema: EXTENSION_SCHEMA, ...(Object.hasOwn(raw, 'python') ? { python: raw.python } : {}), skill, operations: normalized };
}
