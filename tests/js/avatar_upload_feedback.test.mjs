import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import { test } from 'node:test';

const source = fs.readFileSync(new URL('../../wizard/ui/wizard.js', import.meta.url), 'utf8');
for (const name of ['uploadModel', 'handleAvatarUpload']) {
    for (const status of [400, 413, 415]) {
        test(`${name} preserves selection and permits retry after HTTP ${status}`, async () => {
            const start = source.indexOf(`async function ${name}(`);
            const end = source.indexOf('\n}', start) + 2;
            const panel = { style: {}, innerHTML: '' };
            const input = { value: 'bad.vrm' };
            const state = { avatar_vrm_path: 'previous.vrm', uploadedModel: 'previous.vrm' };
            const context = vm.createContext({
                document: { getElementById: id => id === 'model-file' ? input : panel },
                modelFileInput: input, wizardState: state,
                FormData: class { append() {} },
                escapeHtml: value => value,
                fetch: async () => ({ ok: false, status, json: async () => ({ detail: 'Subida rechazada' }) }),
            });
            vm.runInContext(source.slice(start, end), context);
            await context[name]({ name: 'bad.vrm' });
            assert.equal(state.avatar_vrm_path, 'previous.vrm');
            assert.equal(state.uploadedModel, 'previous.vrm');
            assert.match(panel.innerHTML, /Subida rechazada/);
            assert.doesNotMatch(panel.innerHTML, /Modelo subido|Modelo cargado/);
            assert.equal(input.value, '');
        });
    }
}
