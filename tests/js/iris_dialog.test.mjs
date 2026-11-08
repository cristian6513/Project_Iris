// IRIS OS dialogs (2026-10-13, after the qa11 QA): Iris-styled confirmations replace the
// browser's «Una página incrustada en 127.0.0.1 dice…». Without a DOM they fall back to the
// browser functions, so modules whose tests stub window.confirm keep working.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';

const { irisAlert, irisConfirm, irisConsent, irisChoice, irisForm, systemUpdateDialog } = await import('../../face/js/iris_dialog.js');

test('consent fallback can approve once but cannot remember a permission', async () => {
    globalThis.window = { confirm: () => true };
    try {
        assert.deepEqual(await irisConsent({message:'Tomar foto',rememberLabel:'Recordar'}),{confirmed:true,remember:false});
        const aborted = new AbortController(); aborted.abort();
        assert.deepEqual(await irisConsent({signal:aborted.signal}),{confirmed:false,remember:false});
    } finally { delete globalThis.window; }
});

test('an aborted question never opens, and the fallback cannot choose a persistent permission', async () => {
    let called = 0;
    globalThis.window = { confirm() { called++; return true; } };
    try {
        const controller = new AbortController(); controller.abort();
        assert.equal(await irisConfirm({ signal: controller.signal }), false);
        assert.equal(await irisChoice({ signal: controller.signal }), null);
        assert.equal(called, 0);
        assert.equal(await irisChoice({ choices: [{ value: 'once' }, { value: 'always' }] }), 'once');
    } finally { delete globalThis.window; }
});

test('without a DOM a confirmation asks the browser with the whole text', async () => {
    const asked = [];
    globalThis.window = { confirm: (text) => { asked.push(text); return true; } };
    try {
        assert.equal(await irisConfirm({ title: 'X', message: 'Se borrará.', question: '¿Seguro?' }), true);
        globalThis.window.confirm = () => false;
        assert.equal(await irisConfirm({ message: 'Otra' }), false);
        assert.deepEqual(asked, ['Se borrará. ¿Seguro?']);
    } finally {
        delete globalThis.window;
    }
    assert.equal(await irisConfirm({ message: 'sin navegador' }), false);  // nothing to ask: never a yes
});

test('without a DOM a form asks field by field and a cancel returns null', async () => {
    const answers = ['Mi rutina', 'every 1h', null];
    globalThis.window = { prompt: () => answers.shift(), alert: () => {} };
    try {
        const fields = [{ name: 'name' }, { name: 'schedule' }, { name: 'prompt' }];
        assert.equal(await irisForm({ fields }), null);
        answers.push(' a ', ' b ', ' c ');
        assert.deepEqual({ ...await irisForm({ fields }) }, { name: 'a', schedule: 'b', prompt: 'c' });
        await irisAlert({ message: 'ok' });
    } finally {
        delete globalThis.window;
    }
});

test('going back shows the current and the previous version, as in the mockup', () => {
    const dialog = systemUpdateDialog({
        items: [{ id: 'iris-os', version: '0.9.3' }],
        previous: { id: 'abc', version: '0.9.2', hermes_version: '0.21.6' },
    }, true);
    assert.equal(dialog.title, 'Actualización del sistema');
    assert.equal(dialog.message, 'IRIS se cerrará y volverá a abrirse con la versión 0.9.2.');
    assert.equal(dialog.question, '¿Deseas continuar?');
    assert.deepEqual(dialog.versions, { from: '0.9.3', to: '0.9.2' });
    assert.equal(dialog.confirmLabel, 'Volver a 0.9.2');
    assert.equal(dialog.details.label, 'Ver cambios');
    assert.ok(dialog.details.items.some((item) => item.includes('Hermes Agent 0.21.6')));
    assert.ok(dialog.details.items.some((item) => item.includes('se conservan')));
});

test('an update shows its notes and size; missing data never invents a version', () => {
    const update = systemUpdateDialog({
        items: [{ id: 'iris-os', version: '0.9.2' }],
        available: { version: '0.9.3', hermes_version: '0.21.6', size: 412 * 1048576, notes: 'Novedades.' },
    });
    assert.deepEqual(update.versions, { from: '0.9.2', to: '0.9.3' });
    assert.equal(update.confirmLabel, 'Confirmar actualización');
    assert.equal(update.details.text, 'Novedades.');
    assert.ok(update.details.items.includes('Descarga de 412 MB.'));
    const unknown = systemUpdateDialog({}, true);
    assert.equal(unknown.versions, null);
    assert.equal(unknown.message, 'IRIS se cerrará y volverá a abrirse con la versión anterior.');
    assert.equal(systemUpdateDialog({ available: { version: '1.0.0' } }).details.text, 'Esta versión no trae notas.');
});

test('no IRIS OS module opens a browser dialog any more', () => {
    const folder = new URL('../../face/js/', import.meta.url);
    const offenders = [];
    for (const name of readdirSync(folder).filter((file) => file.endsWith('.js') && file !== 'iris_dialog.js')) {
        const source = readFileSync(new URL(name, folder), 'utf8');
        const lines = source.split('\n');
        lines.forEach((line, index) => {
            if (/^\s*(\/\/|\*)/.test(line)) return;
            if (/(^|[^\w.$])(window\.)?(confirm|alert|prompt)\s*\(/.test(line) || /window\.(confirm|alert|prompt)\b/.test(line)) {
                offenders.push(`${name}:${index + 1}`);
            }
        });
    }
    assert.deepEqual(offenders, []);
});

test('the setup wizard uses the same dialogs; only its fallback calls the browser', () => {
    const folder = new URL('../../wizard/ui/', import.meta.url);
    const offenders = [];
    for (const name of readdirSync(folder).filter((file) => file.endsWith('.js'))) {
        readFileSync(new URL(name, folder), 'utf8').split('\n').forEach((line, index) => {
            if (/^\s*(\/\/|\*)/.test(line)) return;
            const fallback = /window\.confirm\(\[options\.message|window\.alert\(options\.message/.test(line);
            if (!fallback && (/(^|[^\w.$])(window\.)?(confirm|alert|prompt)\s*\(/.test(line))) offenders.push(`${name}:${index + 1}`);
        });
    }
    assert.deepEqual(offenders, []);
    const html = readFileSync(new URL('index.html', folder), 'utf8');
    assert.ok(html.indexOf('/shared/iris_dialog.js') < html.indexOf('/ui/wizard.js'));
    assert.match(html, /<link rel="stylesheet" href="\/shared\/iris_dialog\.css\?v=\d+">/);
});
