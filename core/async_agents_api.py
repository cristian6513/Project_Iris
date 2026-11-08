"""Read the two Async Agents sources without starting or modifying a worker."""
import asyncio
import sqlite3
import urllib.parse

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from core.async_agents import ManualRunStore, chat_delegations

router = APIRouter()
SNAPSHOT_TIMEOUT = 9.0


def chat_scope() -> dict:
    from core import websocket_server as ws
    bridge = getattr(getattr(ws.app.state, 'agent', None), 'bridge', None)
    return {'session_key': str(getattr(bridge, '_session_key', '') or ''),
            'session_id': ws._continuity_current_session_id(),
            'profile': str(getattr(bridge, '_active_profile', '') or ''),
            'local': ws.is_loopback_url(getattr(bridge, 'base_url', '') or ws._hermes_api_base_url())}


async def manual_snapshot(force: bool) -> dict:
    from core import websocket_server as ws
    gateway = ws._hermes_api_base_url()
    key = 'hermes:async-agents:manual:' + gateway
    cached = ws._cache_get(key, 8.0, force=force)
    if cached is not None:
        return cached
    store = ManualRunStore()
    runs = await asyncio.to_thread(store.list, gateway)
    semaphore = asyncio.Semaphore(4)

    async def refresh(run: dict) -> dict:
        if not force and run.get('status') in {'completed', 'failed', 'cancelled', 'stopped'}:
            return run
        async with semaphore:
            result = await asyncio.to_thread(ws._hermes_api_request, 'GET',
                '/v1/runs/' + urllib.parse.quote(run['id'], safe=''), timeout=8)
        if not result.get('ok'):
            return {**run, 'stale': True}
        data = result.get('data') or {}
        raw = data.get('run', data) if isinstance(data, dict) else {}
        if not isinstance(raw, dict):
            return {**run, 'stale': True}
        fresh = ws._normalize_run(raw)
        fresh.update(id=run['id'], title=run.get('title') or fresh['title'], source='manual')
        fresh['created_at'] = fresh.get('created_at') or run.get('created_at')
        try:
            await asyncio.to_thread(store.save, gateway, fresh)
        except (OSError, sqlite3.Error):
            fresh['stale'] = True
        return fresh

    tasks = [asyncio.create_task(refresh(run)) for run in runs]
    if tasks:
        _, pending = await asyncio.wait(tasks, timeout=SNAPSHOT_TIMEOUT)
        for task in pending:
            task.cancel()
        results = await asyncio.gather(*tasks, return_exceptions=True)
    else:
        results = []
    refreshed = [result if isinstance(result, dict) else {**run, 'stale': True}
                 for run, result in zip(runs, results)]
    # Fetch full results only when selected, not on every polling cycle.
    light = [{k: v for k, v in run.items() if k not in {'raw', 'result'}} for run in refreshed]
    for run in light:
        run['summary'] = str(run.get('summary') or '')[:240]
    return ws._cache_set(key, {'success': True, 'runs': light,
        'notice': 'Algunos estados no pudieron actualizarse.' if any(r.get('stale') for r in refreshed) else ''})


@router.get('/api/hermes/async-agents')
async def async_agents_snapshot(force: bool = False) -> JSONResponse:
    async def manual() -> dict:
        try:
            return await manual_snapshot(force)
        except (OSError, ValueError, sqlite3.Error):
            return {'success': False, 'runs': [], 'error': 'No se pudo leer el historial de tareas manuales.'}
    scope = chat_scope()
    manual_data, chat = await asyncio.gather(manual(), asyncio.to_thread(chat_delegations, **scope))
    return JSONResponse({'success': True, 'manual': manual_data, 'chat': chat,
                         'scope': f"{scope.get('profile') or 'default'}:{scope['session_key'] or scope['session_id']}"})


@router.get('/api/hermes/delegations/{delegation_id}')
async def delegation_detail(delegation_id: str) -> JSONResponse:
    data = await asyncio.to_thread(chat_delegations, **chat_scope(), run_id=delegation_id)
    if not data.get('success'):
        return JSONResponse(data, status_code=503)
    if not data['runs']:
        raise HTTPException(status_code=404, detail='No se encontró esa delegación en el chat de esta identidad.')
    run = data['runs'][0]
    events = [{'kind': 'Creado desde el chat', 'created_at': run['created_at'], 'message': run['title']}]
    if run['status'] in {'completed', 'failed', 'error', 'cancelled', 'interrupted', 'stalled', 'unknown'}:
        events.append({'kind': 'Finalizado', 'created_at': run['updated_at'],
                       'message': 'Resultado disponible.' if run['result'] else 'Sin resultado disponible.'})
    return JSONResponse({'success': True, 'run': run, 'events': events})
