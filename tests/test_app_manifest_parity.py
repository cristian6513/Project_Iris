"""F17: Core's manifest check (core/app_manifest.py) answers exactly like face/js/os_app_manifest.js.

Both run on the same manifests: every real app, the templates and edge cases where Python and
JavaScript differ by default (trim, UTF-16 lengths, 1.0 versus true, null versus a missing key).
"""
import copy
import json
import os
import subprocess
from pathlib import Path

import pytest

from core.app_manifest import validate_app_manifest
from core.widget_catalog import ICON_NAMES

ROOT = Path(__file__).resolve().parents[1]
BASE = {
    'schema': 'iris.app-manifest/v1', 'sdkVersion': 1, 'id': 'demo', 'name': 'Demo', 'version': '0.1.0',
    'description': 'App de prueba', 'icon': 'sparkle', 'accent': 'magenta', 'presentation': 'window',
    'window': {'width': 420, 'height': 520}, 'launcher': {'visible': True, 'order': 910},
    'ui': {'entry': 'main.js', 'style': 'style.css'},
    'widgets': [{'id': 'demo.timer', 'name': 'Demo', 'entry': 'widget.js', 'style': 'widget.css', 'size': 'compact',
                 'configVersion': 1, 'preview': {'layout': 'summary', 'title': 'Ejemplo', 'subtitle': 'Prueba',
                                                 'items': [{'label': '25:00', 'detail': 'Enfoque'}]}}],
}
MISSING = object()


def variant(path, value):
    data = copy.deepcopy(BASE)
    target = data
    for key in path[:-1]:
        target = target[key]
    if value is MISSING:
        target.pop(path[-1], None)
    else:
        target[path[-1]] = value
    return data


CASES = [
    BASE, [1, 2], None, 'texto',
    variant(['schema'], MISSING), variant(['sdkVersion'], 3), variant(['sdkVersion'], 1.0), variant(['sdkVersion'], True),
    variant(['sdkVersion'], 3.0), variant(['sdkVersion'], 0), variant(['id'], 'Bad'), variant(['id'], 'apps'), variant(['id'], 7),
    variant(['name'], '   '), variant(['name'], 'Demo\x1f'), variant(['name'], 'x' * 41), variant(['name'], '\U0001F600' * 20),
    variant(['name'], '\U0001F600' * 21), variant(['name'], ' ﻿Demo﻿ '), variant(['name'], '\x1cDemo'),
    variant(['version'], '1.2'), variant(['version'], '١.٢.٣'), variant(['version'], '1.2.3-beta.1'),
    variant(['description'], None), variant(['icon'], 'nope'), variant(['icon'], MISSING), variant(['accent'], None),
    variant(['accent'], MISSING), variant(['accent'], 'red'), variant(['presentation'], 'tab'),
    {**variant(['presentation'], 'tab'), 'window': None}, {k: v for k, v in variant(['presentation'], 'tab').items() if k != 'window'},
    variant(['window'], MISSING), variant(['window', 'width'], 359), variant(['window', 'width'], 400.0),
    variant(['window', 'height'], '500'), variant(['window', 'depth'], 2), variant(['launcher'], None), variant(['launcher'], MISSING),
    variant(['launcher'], []), variant(['launcher', 'visible'], 'yes'), variant(['launcher', 'visible'], None),
    variant(['launcher', 'order'], 10000), variant(['launcher', 'order'], None), variant(['launcher', 'label'], ''),
    variant(['launcher', 'label'], 'Etiqueta'), variant(['ui'], {}), variant(['ui'], {'entry': 'main.js', 'adapter': 'legacy'}),
    variant(['ui'], {'module': 'x.js'}), variant(['ui'], {'module': 'X.js'}), variant(['ui'], {'adapter': 'legacy', 'style': 'a.css'}),
    variant(['ui'], {'entry': 'main.js', 'style': None}), variant(['ui'], {'entry': None}), variant(['ui'], {'adapter': 'nuevo'}),
    variant(['requestedCapabilities'], ['apps.actions', 'apps.actions']), variant(['requestedCapabilities'], ['Bad id']),
    variant(['services'], None), variant(['services'], ['x'] * 17), variant(['widgets'], None), variant(['widgets'], []),
    variant(['widgets'], [BASE['widgets'][0]] * 2), variant(['widgets'], [copy.deepcopy(BASE['widgets'][0]) for _ in range(5)]),
    variant(['widgets'], ['no']), variant(['extra'], 1),
]
for key, value in [('id', 'otra.timer'), ('id', 'demo.'), ('id', 'demo.Timer'), ('style', None), ('style', 'w.CSS'),
                   ('size', 'large'), ('configVersion', 1.0), ('configVersion', 2), ('configVersion', True), ('name', ''),
                   ('preview', None), ('preview', []), ('extra', True)]:
    data = copy.deepcopy(BASE)
    data['widgets'][0][key] = value
    CASES.append(data)
for key, value in [('layout', 'grid'), ('subtitle', None), ('subtitle', MISSING), ('items', [{'label': 'a'}] * 4),
                   ('items', [{'label': 'a', 'detail': None}]), ('items', ['x']), ('items', None), ('title', 'x' * 61)]:
    data = copy.deepcopy(BASE)
    if value is MISSING:
        data['widgets'][0]['preview'].pop(key)
    else:
        data['widgets'][0]['preview'][key] = value
    CASES.append(data)
CASES.append({**copy.deepcopy(BASE), 'id': 'Mal', 'widgets': [copy.deepcopy(BASE['widgets'][0])]})  # JS: prefix «undefined.»
CASES += [json.loads(path.read_text(encoding='utf-8')) for path in sorted((ROOT / 'apps').glob('*/app.json'))]
EXTENSION = {'schema': 'iris.app-extension/v1', 'python': 'main.py', 'skill': True,
             'operations': [{'action': 'status', 'label': 'Consultar', 'effect': 'read', 'arguments': {}}]}
for extension in (EXTENSION, None, [], False, {},
                  {**EXTENSION, 'python': '../main.py'}, {**EXTENSION, 'python': None},
                  {**EXTENSION, 'skill': 'yes'}, {**EXTENSION, 'extra': 1},
                  {**EXTENSION, 'operations': None}, {**EXTENSION, 'operations': []},
                  {**EXTENSION, 'operations': [EXTENSION['operations'][0]] * 2},
                  {**EXTENSION, 'operations': ['bad']},
                  {**EXTENSION, 'operations': [{'action': 'x', 'label': ' ﻿Consultar﻿ ', 'effect': 'read', 'arguments': {'n': 'integer'}}]},
                  {**EXTENSION, 'operations': [{'action': 'x', 'label': 1, 'effect': [], 'arguments': {'n': []}}]},
                  *({**EXTENSION, 'operations': [{**EXTENSION['operations'][0], 'audience': audience}]}
                    for audience in ('all', 'ui', 'iris', 'app', None, ['ui'])),
                  {'schema': 'iris.app-extension/v1', 'skill': True}):
    CASES.append({**copy.deepcopy(BASE), 'sdkVersion': 2, 'extension': extension})
CASES.append({**copy.deepcopy(BASE), 'extension': EXTENSION})

SCRIPT = """
import { validateAppManifest } from './face/js/os_app_manifest.js';
import { OS_ICON_PATHS } from './face/js/os_icons.js';
let input = '';
process.stdin.setEncoding('utf8');
for await (const chunk of process.stdin) input += chunk;
const iconNames = Object.keys(OS_ICON_PATHS);
process.stdout.write(JSON.stringify(JSON.parse(input).map((raw) => validateAppManifest(raw, { iconNames }))));
"""


@pytest.fixture(scope='module')
def js_results():
    env = {key: value for key, value in os.environ.items() if not key.startswith('NODE_TEST')}
    reply = subprocess.run(['node', '--input-type=module', '-e', SCRIPT], cwd=ROOT, env=env, capture_output=True,
                           input=json.dumps(CASES, ensure_ascii=False).encode('utf-8'), timeout=60, check=True)
    return json.loads(reply.stdout.decode('utf-8'))


def test_every_case_is_answered_the_same_by_both_validators(js_results):
    assert len(js_results) == len(CASES) > 100
    for index, raw in enumerate(CASES):
        assert validate_app_manifest(raw, icon_names=ICON_NAMES) == js_results[index], (index, raw)


def test_the_cases_cover_both_answers(js_results):
    """Control: the parity is not vacuous — valid, invalid and incompatible manifests are all present."""
    assert sum(result['ok'] for result in js_results) >= 25
    assert sum(not result['ok'] for result in js_results) >= 60
    assert any(result['incompatible'] for result in js_results)
