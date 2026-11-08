"""Hermes owns code, dependency upgrades, backups and service restarts."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from core.hermes_runtime import get_hermes_home, get_hermes_repo, run_hermes_cli
from core import runtime_descriptor as runtime_policy


def _git_output(repo: Path, *args: str) -> str:
    runtime_policy.require_runtime_capability('external_hermes_update')
    try:
        result = subprocess.run(['git', *args], cwd=repo, capture_output=True,
                                text=True, encoding='utf8', errors='replace', timeout=10)
        return result.stdout.strip() if result.returncode == 0 else ''
    except (OSError, subprocess.TimeoutExpired):
        return ''


def hermes_version() -> str:
    runtime = runtime_policy.get_runtime()
    if runtime.packaged:
        # The included engine is pinned: its version needs no Hermes process (qa9).
        return f'Hermes Agent v{runtime.hermes.version} · {runtime.hermes.revision[:8]}'
    result = run_hermes_cli(['--version'], timeout=20)
    lines = (result.get('stdout') or '').splitlines()
    return lines[0] if result.get('success') and lines else ''


def hermes_update_snapshot() -> dict[str, Any]:
    runtime = runtime_policy.get_runtime()
    if runtime.packaged:
        return {'id': 'hermes-agent', 'label': 'Motor incluido de Iris',
                'version': runtime.hermes.version, 'current_ref': runtime.hermes.revision[:12],
                'latest_ref': '', 'status': 'managed', 'can_update': False,
                'detail': 'Hermes se entrega con Iris y se actualiza junto con Iris desde su canal.',
                'method': 'iris-bundle'}
    repo = get_hermes_repo()
    item = {'id': 'hermes-agent', 'label': 'Hermes Agent', 'path': str(repo),
            'version': hermes_version(), 'current_ref': _git_output(repo, 'rev-parse', 'HEAD')[:12],
            'latest_ref': '', 'status': 'unknown', 'can_update': False,
            'detail': 'No se pudo verificar Hermes.', 'method': 'hermes-cli'}
    result = run_hermes_cli(['update', '--check'], timeout=90)
    output = result.get('output') or result.get('error') or ''
    item['latest_ref'] = _git_output(repo, 'rev-parse', '@{upstream}')[:12]
    if not result.get('success'):
        item.update(status='error', detail=output[-3000:] or 'El actualizador oficial no pudo comprobar Hermes.')
    elif 'update available' in output.casefold():
        item.update(status='update_available', can_update=True,
                    detail='Actualización disponible. Hermes actualizará el código y sus dependencias con su mecanismo de respaldo y recuperación.')
    elif 'up to date' in output.casefold() or 'ahead of' in output.casefold():
        item.update(status='current', detail='Hermes confirma que esta instalación está actualizada.')
    else:
        item['detail'] = output[-3000:] or 'Hermes no devolvió un estado de actualización reconocible.'
    # Code may be current even though dependencies or service recovery failed.
    # Keep a retry available instead of stranding the user with a disabled button.
    receipt = _latest_receipt({})
    receipt_sha = str(receipt.get('post_update', {}).get('sha') or '')
    if (item['status'] == 'current' and item['current_ref']
            and receipt_sha.startswith(item['current_ref'])
            and receipt.get('outcome') in {'failed', 'partial'}):
        item.update(status='repair_required', can_update=True,
                    detail='El código está actualizado, pero la última instalación quedó incompleta. Pulsa Actualizar para reintentar la finalización con Hermes.')
    return item


def _receipt_state() -> dict[str, tuple[int, int]]:
    directory = get_hermes_home() / 'logs' / 'update_receipts'
    try:
        return {str(path): (path.stat().st_mtime_ns, path.stat().st_size) for path in directory.glob('*.json')}
    except OSError:
        return {}


def _latest_receipt(before: dict[str, tuple[int, int]]) -> dict[str, Any]:
    directory = get_hermes_home() / 'logs' / 'update_receipts'
    try:
        candidates = sorted(directory.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)
        for path in candidates:
            stamp = path.stat()
            if before.get(str(path)) == (stamp.st_mtime_ns, stamp.st_size):
                continue
            data = json.loads(path.read_text(encoding='utf8'))
            if isinstance(data, dict):
                return {'path': str(path), 'outcome': data.get('outcome'),
                        'stop_reason': data.get('stop_reason'), 'steps': data.get('steps', []),
                        'post_update': data.get('post_update') if isinstance(data.get('post_update'), dict) else {}}
    except (OSError, ValueError):
        pass
    return {}


def apply_hermes_update() -> dict[str, Any]:
    runtime_policy.require_runtime_capability('external_hermes_update')
    before = _receipt_state()
    # Never use git pull alone, --force or --force-venv. Hermes handles local
    # edits, optional dependencies and busy native modules using its own policy.
    # No time limit that could kill the installer midway through dependency sync.
    result = run_hermes_cli(['update', '--yes'], timeout=None)
    receipt = _latest_receipt(before)
    ok = bool(result.get('success')) and receipt.get('outcome') not in {
        'failed', 'partial', 'refused', 'running',
    }
    detail = result.get('output') or result.get('error') or ''
    if not ok and "another hermes.exe is running" in detail.casefold():
        detail = ("Cierra la consola de Hermes con /exit y vuelve a intentar la actualización. "
                  "Windows impide reemplazar hermes.exe mientras está abierto.\n\n" + detail)
    if receipt and not ok:
        detail += '\nResultado del actualizador: ' + str(receipt.get('outcome'))
    return {'label': 'Hermes Agent', 'ok': ok, 'method': 'hermes-cli',
            'detail': detail[-8000:] or ('Hermes actualizado.' if ok else 'La actualización de Hermes no se completó.'),
            'receipt': receipt, 'version': hermes_version()}
