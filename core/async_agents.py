"""IRIS manual run index and read-only projection of Hermes delegations.

Hermes owns execution. Its delegation ledger is read with mode=ro; only IDs
created by IRIS and their last known API snapshots are stored in our own DB.
"""
from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from core.config import get_iris_home
from core.response_text import visible_response_text
from wizard.hermes_config import get_hermes_home


def result_text(value: Any) -> str:
    return visible_response_text(value)


def _present_snapshot(snapshot: dict) -> dict:
    # Old completed runs may never be fetched again. Project them on read so
    # fixing presentation requires no data migration or changes to Hermes.
    return {**snapshot, **{
        key: result_text(snapshot[key]) for key in ('summary', 'result')
        if key in snapshot
    }}


class ManualRunStore:
    def __init__(self, path: Path | None = None):
        self.path = path or get_iris_home() / 'async_agents.db'

    def save(self, gateway: str, run: dict) -> None:
        if not run.get('id'):
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        snapshot = {key: value for key, value in run.items() if key != 'raw'}
        with closing(sqlite3.connect(self.path, timeout=5)) as conn, conn:
            conn.execute('CREATE TABLE IF NOT EXISTS manual_runs (gateway TEXT, id TEXT, snapshot TEXT, PRIMARY KEY(gateway,id))')
            conn.execute('INSERT INTO manual_runs VALUES (?,?,?) ON CONFLICT(gateway,id) DO UPDATE SET snapshot=excluded.snapshot',
                         (gateway, run['id'], json.dumps(snapshot, ensure_ascii=False)))

    def get(self, gateway: str, run_id: str) -> dict | None:
        if not self.path.is_file():
            return None
        with closing(sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True, timeout=5)) as conn:
            row = conn.execute('SELECT snapshot FROM manual_runs WHERE gateway=? AND id=?', (gateway, run_id)).fetchone()
            return _present_snapshot(json.loads(row[0])) if row else None

    def list(self, gateway: str, limit: int = 50) -> list[dict]:
        if not self.path.is_file():
            return []
        conn = sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True, timeout=5)
        try:
            rows = conn.execute('SELECT snapshot FROM manual_runs WHERE gateway=? ORDER BY rowid DESC LIMIT ?',
                                (gateway, min(max(limit, 1), 100))).fetchall()
            return [_present_snapshot(json.loads(row[0])) for row in rows]
        finally:
            conn.close()


def _object(raw: Any) -> dict:
    try:
        value = json.loads(raw or '{}')
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def _delegation(row: dict, *, detail: bool) -> dict:
    task = _object(row.get('task_json'))
    result = _object(row.get('result_json'))
    event = _object(row.get('event_json'))
    goals = task.get('goals') or event.get('goals') or []
    if not isinstance(goals, list):
        goals = []
    title = task.get('goal') or event.get('goal') or (goals[0] if goals else '') or 'Agente del chat'
    if not isinstance(title, str):
        title = result_text(title) or 'Agente del chat'
    children = result.get('results') or event.get('results') or []
    if not isinstance(children, list):
        children = []
    output = []
    for index, child in enumerate(children):
        if not isinstance(child, dict):
            continue
        goal = goals[index] if index < len(goals) else title
        output.append({'title': str(goal)[:500], 'status': str(child.get('status') or 'unknown'),
                       'text': result_text(child)})
    final = '\n\n'.join(
        (item['title'] + '\n' if len(output) > 1 else '') + item['text'] for item in output if item['text']
    ) or result_text(result) or result_text(event.get('error'))
    run = {'id': row['delegation_id'], 'source': 'chat', 'title': title[:500],
           'status': row.get('state') or 'unknown', 'agent': 'Delegado por el chat',
           'created_at': row.get('dispatched_at'), 'updated_at': row.get('completed_at') or row.get('updated_at'),
           'summary': final[:240], 'child_count': len(children) or len(goals) or 1,
           'delivery_state': row.get('delivery_state') or 'pending',
           'session_id': row.get('parent_session_id') or row.get('origin_session_id') or ''}
    if detail:
        run['result'] = final[:100_000]
        run['children'] = output
    return run


def chat_delegations(*, session_key: str, session_id: str, run_id: str = '',
                     db_path: Path | None = None, limit: int = 50,
                     profile: str = '', local: bool = True) -> dict:
    """Read delegations belonging to the current IRIS identity/session only.

    The stable session key includes the identity. It keeps its history visible
    across a /new; no other Hermes user, platform or profile is enumerated.
    """
    if not local:
        return {'success': False, 'runs': [], 'error': 'Las delegaciones del chat requieren el registro de Hermes en este equipo.'}
    if profile and profile != 'default' and not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', profile):
        return {'success': False, 'runs': [], 'error': 'No se pudo identificar el perfil de Hermes.'}
    home = get_hermes_home()
    if profile and profile != 'default':
        home = home / 'profiles' / profile
    path = db_path or home / 'state.db'
    empty = {'success': True, 'runs': [], 'scope': 'Chat de esta identidad'}
    if not session_key and not session_id:
        return {**empty, 'notice': 'Abre una conversación para ver sus agentes.'}
    if not path.is_file():
        return empty
    conn = None
    try:
        conn = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=2)
        conn.row_factory = sqlite3.Row
        columns = {r[1] for r in conn.execute('PRAGMA table_info(async_delegations)')}
        if not columns:
            return {**empty, 'notice': 'Hermes todavía no tiene delegaciones registradas.'}
        clauses, args = [], []
        if session_key and 'origin_session' in columns:
            clauses.append('origin_session=?'); args.append(session_key)
        for name in ('parent_session_id', 'origin_session_id'):
            if session_id and name in columns:
                clauses.append(f'{name}=?'); args.append(session_id)
        if not clauses or not {'delegation_id', 'state', 'dispatched_at'} <= columns:
            return {'success': False, 'runs': [], 'error': 'La versión de Hermes no permite leer este registro.'}
        wanted = ('delegation_id', 'state', 'dispatched_at', 'completed_at', 'updated_at',
                  'delivery_state', 'parent_session_id', 'origin_session_id', 'task_json', 'result_json', 'event_json')
        select = ','.join(name for name in wanted if name in columns)
        where = '(' + ' OR '.join(clauses) + ')'
        if run_id:
            where += ' AND delegation_id=?'; args.append(run_id)
        args.append(max(1, min(int(limit), 100)))
        rows = conn.execute(f'SELECT {select} FROM async_delegations WHERE {where} ORDER BY dispatched_at DESC LIMIT ?', args)
        return {**empty, 'runs': [_delegation(dict(r), detail=bool(run_id)) for r in rows]}
    except (sqlite3.Error, OSError):
        return {'success': False, 'runs': [], 'error': 'No se pudo consultar el registro local de Hermes. Vuelve a actualizar.'}
    finally:
        if conn is not None:
            conn.close()
