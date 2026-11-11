from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

import pytest

from scripts.export_repositories import ExportError, Plan, apply_plan, changes_for, make_plan, rewrite_markdown, scan_secrets, sha, target_path


def simple_plan(payload):
    return Plan({'main': 'main'}, {'main': payload}, {})


def test_export_preserves_manual_changes_before_writing_any_other_file(tmp_path):
    plan = simple_plan({'one.py': b'first', 'two.py': b'second'})
    apply_plan(tmp_path, plan)
    (tmp_path / 'main/one.py').write_bytes(b'manual improvement')
    state = (tmp_path / '.iris-publication/state.json').read_bytes()
    with pytest.raises(ExportError, match='cambios manuales'):
        apply_plan(tmp_path, simple_plan({'one.py': b'new first', 'two.py': b'new second'}))
    assert (tmp_path / 'main/one.py').read_bytes() == b'manual improvement'
    assert (tmp_path / 'main/two.py').read_bytes() == b'second'
    assert (tmp_path / '.iris-publication/state.json').read_bytes() == state


def test_export_updates_and_retires_owned_files_with_recoverable_backups(tmp_path):
    apply_plan(tmp_path, simple_plan({'one.py': b'first', 'old.py': b'retired content'}))
    result = apply_plan(tmp_path, simple_plan({'one.py': b'new content'}))
    assert (tmp_path / 'main/one.py').read_bytes() == b'new content'
    assert not (tmp_path / 'main/old.py').exists()
    backup = tmp_path / '.iris-publication/backups'
    assert any(path.read_bytes() == b'first' for path in backup.rglob('one.py'))
    assert any(path.read_bytes() == b'retired content' for path in backup.rglob('old.py'))
    assert result['retired_with_backup'] == 1
    assert changes_for(tmp_path, simple_plan({'one.py': b'new content'})) == ([], [])


def test_export_rejects_unmanaged_files_and_never_copies_git_metadata(tmp_path):
    plan = simple_plan({'one.py': b'first'})
    apply_plan(tmp_path, plan)
    (tmp_path / 'main/.git').mkdir()
    (tmp_path / 'main/.git/config').write_text('local git metadata')
    assert changes_for(tmp_path, plan) == ([], [])
    (tmp_path / 'main/stray.txt').write_text('not from Alice')
    with pytest.raises(ExportError, match='Archivo no administrado'):
        apply_plan(tmp_path, plan)
    assert (tmp_path / 'main/stray.txt').read_text() == 'not from Alice'
    assert (tmp_path / 'main/.git/config').read_text() == 'local git metadata'


def test_export_rejects_escape_and_shared_destination_file(tmp_path):
    with pytest.raises(ExportError):
        target_path(tmp_path, 'main', '../../outside')
    external = tmp_path / 'outside.txt'
    external.write_bytes(b'keep me')
    (tmp_path / 'main').mkdir()
    os.link(external, tmp_path / 'main/one.py')
    with pytest.raises(ExportError, match='ordinario'):
        apply_plan(tmp_path, simple_plan({'one.py': b'overwrite'}))
    assert external.read_bytes() == b'keep me'


def test_secret_reports_do_not_disclose_the_value_and_exceptions_bind_the_whole_line():
    secret = 'sk-' + 'aB37' * 12
    line = ('key = "' + secret + '"').encode()
    findings = scan_secrets('core/config.py', line)
    assert findings and secret not in json.dumps(findings)
    assert findings[0]['path'] == 'core/config.py'
    exception = [{'path': 'core/config.py', 'rule': 'api_key', 'line_sha256': sha(line)}]
    assert scan_secrets('core/config.py', line, exception) == []
    assert scan_secrets('core/config.py', line + b' # changed', exception)
    assert scan_secrets('core/other.py', line, exception)


def test_export_adapts_multiline_links_outside_fenced_code():
    kept = b'[Guia que sigue\n disponible](guides/start.md)'
    fenced = b'\x60\x60\x60md\n[Ejemplo que conserva\n su ruta](audits/private.md)\n\x60\x60\x60'
    external = b'[Guia\n externa](https://example.org/docs)'
    internal = b'[Informe que ocupa\n dos lineas](audits/private.md)'
    document = b'\n\n'.join([kept, fenced, external, internal]) + b'\n'
    public, count = rewrite_markdown(document, 'docs/current.md', {'docs/guides/start.md'}, component='main')
    assert count == 1
    assert public == b'\n\n'.join([kept, fenced, external, b'Informe que ocupa dos lineas (informe interno no distribuido)']) + b'\n'


def test_selection_excludes_unignored_qa_and_preserves_pinned_notice_bytes(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    subprocess.run(['git', 'init', '-q'], cwd=source, check=True)
    subprocess.run(['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                    'commit', '-q', '--allow-empty', '-m', 'fixture'], cwd=source, check=True)
    content = {
        'core/engine.py': b'answer = 42\n',
        'desktop/installer/qa/client_secret_demo.json': b'personal data, deliberately not ignored',
        'scripts/runtime_notices/sample/LICENSE.md': b'# Notice\r\n[upstream](../../missing.md)\r\n',
        'tools/repositories/templates/apps-readme.md': b'# Apps\n',
        'tools/repositories/templates/transitions-readme.md': b'# Transitions\n',
        'tools/repositories/templates/overview.md': b'# Three repos\n',
        'tools/repositories/templates/kit-gitignore': b'*.log\n',
        'tools/repositories/templates/validar_app.py': b'# delegates to the official tool\n',
    }
    for name, data in content.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    policy = {'main_roots': ['core', 'desktop', 'scripts', 'tools'], 'main_files': [],
              'excluded_prefixes': ['desktop/installer/qa/'],
              'components': {'main': {'directory': 'main'}, 'apps': {'directory': 'apps', 'files': [], 'prefixes': []},
                             'transitions': {'directory': 'transitions', 'files': [], 'prefixes': []}}}
    plan = make_plan(source, policy)
    assert not any('client_secret' in name for payload in plan.files.values() for name in payload)
    name = 'scripts/runtime_notices/sample/LICENSE.md'
    assert plan.files['main'][name] == content[name]
    assert plan.files['main']['core/engine.py'] == content['core/engine.py']
