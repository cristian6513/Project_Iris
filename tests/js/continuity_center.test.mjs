import test from 'node:test';
import assert from 'node:assert/strict';

import { ContinuityCenterApp } from '../../face/js/continuity_center.js';

test('shows a neutral loading diagnosis before the first Hermes snapshot', () => {
    const app = new ContinuityCenterApp({ root: null, fetchImpl: null });

    app.loading = true;
    app._loaded = false;
    assert.match(app._modeMarkup(), /Consultando perfiles/);
    assert.doesNotMatch(app._modeMarkup(), /desconectado/i);

    app._loaded = true;
    app.snapshot.mode = 'native';
    assert.match(app._modeMarkup(), /Hermes nativo/);
});
