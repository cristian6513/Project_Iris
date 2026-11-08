"""Historical and background replies must expose the answer, not its envelope."""
import asyncio
import json
import sqlite3
from unittest.mock import AsyncMock, Mock

import pytest

from core.async_agents import ManualRunStore, chat_delegations, result_text
from core import async_agents_api
from core.hermes_continuity import normalize_messages
from core.hermes_cron import _extract_response_text, list_cron_outputs
from core import websocket_server as ws


ANSWER = 'Hola. **Resultado listo**. Ejemplo: {"message": "bien"} 😊'
CONTRACT = {"texto_hablado": ANSWER, "emocion": "feliz",
            "animacion_virtual": "confirm", "extra": {"internal": True}}
RAW = json.dumps(CONTRACT, ensure_ascii=False)


@pytest.mark.parametrize('raw', [
    RAW,
    '```json\n' + RAW + '\n```',
    'Respuesta preliminar.\n' + RAW,
    'Respuesta preliminar.\n```json\n' + RAW + '\n```',
    json.dumps(RAW, ensure_ascii=False),
    json.dumps({'role': 'assistant', 'content': [{'type': 'text', 'text': RAW}]}),
])
def test_all_three_surfaces_read_the_same_iris_answer(raw):
    assert normalize_messages([{'role': 'assistant', 'content': raw}])[0]['content'] == ANSWER
    assert _extract_response_text('# Cron Job: Test\n\n## Response\n' + raw) == ANSWER
    assert result_text({'output': raw}) == ANSWER


def test_structured_blocks_exclude_reasoning_and_tools_before_unwrapping():
    blocks = [
        {'type': 'reasoning', 'text': 'PRIVATE'},
        {'type': 'tool_result', 'content': 'PRIVATE'},
        {'type': 'output_text', 'text': RAW},
    ]
    assert normalize_messages([{'role': 'assistant', 'content': blocks}])[0]['content'] == ANSWER
    assert result_text({'output': [{'role': 'assistant', 'type': 'message', 'content': blocks}]}) == ANSWER
    assert result_text({'role': 'tool', 'content': 'PRIVATE'}) == ''


@pytest.mark.parametrize('text', [
    '{"message":"Texto solicitado","value":42}',
    'Ejemplo:\n```json\n{"output":"correcto"}\n```\nFin del ejemplo.',
    'Código:\n```python\nprint("hola")\n```\nUna explicación después.',
    'Respuesta normal con {llaves}, saltos\ny **formato**.',
    '{"texto_hablado": "JSON incompleto',
])
def test_legitimate_json_code_and_plain_text_are_preserved(text):
    assert normalize_messages([{'role': 'assistant', 'content': text}])[0]['content'] == text
    assert _extract_response_text('## Response\n' + text) == text
    assert result_text(text) == text


def test_user_messages_are_never_interpreted_as_assistant_commands():
    assert normalize_messages([{'role': 'user', 'content': RAW}])[0]['content'] == RAW


def test_unpack_before_truncation_and_keep_empty_contracts_empty():
    long = json.dumps({**CONTRACT, 'texto_hablado': 'a' * 13000})
    assert normalize_messages([{'role': 'assistant', 'content': long}])[0]['content'] == 'a' * 12000
    empty = '{"texto_hablado":"","emocion":"neutral","animacion_virtual":"idle"}'
    assert normalize_messages([{'role': 'assistant', 'content': empty}]) == []
    assert _extract_response_text(empty) == ''
    assert result_text(empty) == ''


def test_existing_cron_files_get_clean_text_and_summary_without_being_rewritten(tmp_path, monkeypatch):
    home = tmp_path / 'hermes'
    file = home / 'cron' / 'output' / 'job1' / 'result.md'
    file.parent.mkdir(parents=True)
    file.write_text('# Cron Job: Test\n\n## Response\n' + RAW, encoding='utf8')
    before = file.read_bytes()
    monkeypatch.setattr('core.hermes_cron._hermes_home', lambda: home)
    output = list_cron_outputs(job_id='job1')['outputs'][0]
    assert output['text'] == ANSWER
    assert output['summary'] == ANSWER[:180]
    assert file.read_bytes() == before


def test_old_manual_snapshots_are_projected_on_read_without_migrating_the_database(tmp_path):
    store = ManualRunStore(tmp_path / 'manual.db')
    store.save('gateway', {'id': 'run1', 'status': 'completed', 'summary': RAW, 'result': RAW})
    before = store.path.read_bytes()
    assert store.get('gateway', 'run1')['result'] == ANSWER
    assert store.list('gateway')[0]['summary'] == ANSWER
    assert store.path.read_bytes() == before


def test_manual_run_api_projects_nested_results():
    run = ws._normalize_run({'id': 'run1', 'output': {'content': [{'type': 'text', 'text': RAW}]}})
    assert run['summary'] == ANSWER
    assert run['result'] == ANSWER


def test_cron_narration_also_normalizes_a_previous_clients_payload(monkeypatch):
    respond = AsyncMock()
    monkeypatch.setattr(ws, '_agent_or_503', lambda: type('Agent', (), {'_respond': respond})())

    async def exercise():
        response = await ws.cron_speak({'name': 'Mi rutina', 'text': RAW})
        await asyncio.sleep(0)
        assert response.status_code == 200

    asyncio.run(exercise())
    assert respond.await_args.args[0]['texto_hablado'] == 'Cron finalizado: Mi rutina. ' + ANSWER


def test_chat_delegations_decode_each_child_and_leave_hermes_ledger_intact(tmp_path):
    path = tmp_path / 'state.db'
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE async_delegations (delegation_id TEXT, origin_session TEXT, state TEXT, dispatched_at REAL, task_json TEXT, result_json TEXT)')
        connection.execute('INSERT INTO async_delegations VALUES (?,?,?,?,?,?)', (
            'delegated', 'iris:test', 'completed', 1,
            json.dumps({'goals': ['Primer agente', 'Segundo agente']}),
            json.dumps({'results': [
                {'status': 'completed', 'summary': 'Respuesta:\n' + RAW},
                {'status': 'completed', 'output': 'Justificación:\n```json\n' + RAW + '\n```'},
            ], 'reasoning': 'PRIVATE'}),
        ))
    before = path.read_bytes()
    run = chat_delegations(session_key='iris:test', session_id='', run_id='delegated', db_path=path)['runs'][0]
    assert [child['text'] for child in run['children']] == [ANSWER, ANSWER]
    assert run['result'] == 'Primer agente\n' + ANSWER + '\n\nSegundo agente\n' + ANSWER
    assert 'PRIVATE' not in json.dumps(run)
    assert path.read_bytes() == before


def test_completed_manual_snapshot_and_offline_detail_are_clean(monkeypatch, tmp_path):
    store = ManualRunStore(tmp_path / 'manual.db')
    store.save('gateway', {'id': 'old', 'title': 'Mi tarea', 'status': 'completed',
                           'summary': RAW, 'result': RAW})
    monkeypatch.setattr(async_agents_api, 'ManualRunStore', lambda: store)
    monkeypatch.setattr(ws, 'ManualRunStore', lambda: store)
    monkeypatch.setattr(ws, '_hermes_api_base_url', lambda: 'gateway')
    monkeypatch.setattr(ws, '_api_cache', {})
    request = Mock(return_value={'ok': False, 'error': 'offline'})
    monkeypatch.setattr(ws, '_hermes_api_request', request)
    listing = asyncio.run(async_agents_api.manual_snapshot(False))
    assert listing['runs'][0]['summary'] == ANSWER
    request.assert_not_called()
    detail = json.loads(asyncio.run(ws.hermes_run_detail('old', force=True)).body)
    assert detail['run']['result'] == ANSWER
    assert detail['run']['stale']

