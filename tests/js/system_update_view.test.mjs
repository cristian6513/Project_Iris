import test from 'node:test';
import assert from 'node:assert/strict';
import { systemUpdateProgress } from '../../face/js/system_update_view.js';

test('download uses actual bytes and the percentage of that download', () => {
    const format = (done) => systemUpdateProgress({ phase: 'downloading', progress: { done, total: 508635913 } });
    assert.deepEqual(format(251200000), {
        title: 'Descargando actualización',
        detail: '251,2 MB de 508,6 MB',
        percent: 49,
    });
    assert.equal(format(0).percent, 0);
    assert.equal(format(508635913).percent, 100);
});

test('file phases restart their measurement and final checks have no estimated percentage', () => {
    for (const phase of ['extracting', 'verifying']) {
        assert.equal(systemUpdateProgress({ phase, progress: { done: 0, total: 12550 } }).percent, 0);
        assert.match(systemUpdateProgress({ phase, progress: { done: 12000, total: 12550 } }).detail, /archivos$/);
    }
    for (const phase of ['verifying', 'finalizing', 'activating', 'updating']) {
        assert.equal(systemUpdateProgress({ phase }).percent, null);
    }
    assert.equal(systemUpdateProgress({ phase: 'verifying', operation: 'rollback' }).title, 'Verificando la versión anterior');
    assert.match(systemUpdateProgress({ phase: 'activating' }).detail, /volverá a abrirse/);
});
