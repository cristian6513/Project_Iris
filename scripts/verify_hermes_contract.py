"""Exercise installed Hermes + IRIS contracts in an isolated home, without an LLM.

Run with IRIS Python. The worker uses Hermes' own Python and native HTTP routes.
Only the model/agent execution is substituted; no provider credentials are used.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@dataclass(frozen=True)
class ContractRuntime:
    hermes_root: Path
    hermes_python: Path
    iris_python: Path
    work_root: Path
    output: Path
    explicit: bool


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument('--hermes-root', type=Path)
    result.add_argument('--hermes-python', type=Path)
    result.add_argument('--iris-python', type=Path)
    result.add_argument('--work-root', type=Path)
    result.add_argument('--output', type=Path)
    result.add_argument('--timeout', type=float, default=90)
    result.add_argument('--native-server', action='store_true', help=argparse.SUPPRESS)
    result.add_argument('--worker-origin', help=argparse.SUPPRESS)
    return result


def resolve_runtime(args: argparse.Namespace) -> ContractRuntime:
    declared = (args.hermes_root, args.hermes_python, args.iris_python)
    explicit = any(value is not None for value in declared)
    if explicit:
        if not all(value is not None for value in declared) or args.work_root is None:
            raise ValueError('Explicit staging requires --hermes-root, --hermes-python, '
                             '--iris-python and --work-root.')
        hermes_root, hermes_python, iris_python = declared
    else:
        # Legacy developer mode still discovers the managed installation. The
        # explicit branch never imports or invokes any discovery helper.
        from core.hermes_runtime import get_hermes_repo, resolve_hermes_python

        hermes_root = get_hermes_repo()
        hermes_python = resolve_hermes_python()
        iris_python = Path(sys.executable)
        if not hermes_python:
            raise ValueError('No managed Hermes Python was found.')
    hermes_root = Path(hermes_root).resolve(strict=True)
    hermes_python = Path(hermes_python).resolve(strict=True)
    iris_python = Path(iris_python).resolve(strict=True)
    if not (hermes_root / 'hermes_cli' / '__init__.py').is_file():
        raise ValueError('The declared Hermes source root is incomplete.')
    if not hermes_python.is_file() or not iris_python.is_file():
        raise ValueError('Declared interpreters must be files.')
    work_root = (args.work_root or ROOT / 'artifacts/hermes-update').resolve()
    work_root.mkdir(parents=True, exist_ok=True)
    output = (args.output or work_root / 'native-contract.json').resolve()
    return ContractRuntime(hermes_root, hermes_python, iris_python, work_root, output, explicit)


def isolated_environment(home: Path, inherited: dict[str, str] | None = None) -> dict[str, str]:
    """Construct an allowlisted environment; no shell/provider credentials survive."""
    inherited = os.environ if inherited is None else inherited
    env = {key: inherited[key] for key in ('SystemRoot', 'WINDIR', 'COMSPEC', 'PATHEXT')
           if key in inherited}
    if os.name == 'nt':
        # The A08.4 private interpreters are Windows x64. CPython reads this
        # variable for platform.machine(); omitting it changes dependency paths.
        env['PROCESSOR_ARCHITECTURE'] = 'AMD64'
    for relative in ('hermes', 'iris', 'user', 'local', 'roaming', 'tmp'):
        (home / relative).mkdir(parents=True, exist_ok=True)
    env.update({
        'PATH': '', 'PYTHONNOUSERSITE': '1', 'PYTHONDONTWRITEBYTECODE': '1',
        'PYTHONIOENCODING': 'utf-8', 'HERMES_DISABLE_LAZY_INSTALLS': '1',
        'IRIS_HERMES_CONTRACT_CHILD': '1',
        'HERMES_HOME': str(home / 'hermes'), 'IRIS_HOME': str(home / 'iris'),
        'USERPROFILE': str(home / 'user'), 'HOME': str(home / 'user'),
        'LOCALAPPDATA': str(home / 'local'), 'APPDATA': str(home / 'roaming'),
        'TEMP': str(home / 'tmp'), 'TMP': str(home / 'tmp'), 'TMPDIR': str(home / 'tmp'),
    })
    return env


def _read_port(process: subprocess.Popen, timeout: float) -> int:
    lines: queue.Queue[str] = queue.Queue()

    def reader():
        # Daemon avoids an indefinite thread join if a faulty server never
        # writes readiness. Cleanup below closes its pipe by ending the child.
        for line in process.stdout:
            lines.put(line)
            if line.startswith('PORT='):
                return
        lines.put('')

    threading.Thread(target=reader, daemon=True).start()
    deadline = time.monotonic() + timeout
    while True:
        try:
            line = lines.get(timeout=max(.001, deadline - time.monotonic()))
        except queue.Empty as exc:
            raise TimeoutError('Native contract server did not become ready.') from exc
        if not line:
            raise RuntimeError('Native contract server exited before readiness; see its log.')
        if line.startswith('PORT='):
            value = line.partition('=')[2].strip()
            if not value.isdigit() or not 0 < int(value) < 65536:
                raise RuntimeError('Native contract server returned an invalid port.')
            return int(value)
        if time.monotonic() >= deadline:
            raise TimeoutError('Native contract server did not become ready.')


def _stop_server(process: subprocess.Popen) -> None:
    try:
        try:
            process.stdin.write('\n')
            process.stdin.flush()
        except (BrokenPipeError, OSError):
            pass
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    finally:
        for pipe in (process.stdin, process.stdout):
            if pipe is not None:
                pipe.close()


def launch(runtime: ContractRuntime, *, timeout: float = 90) -> int:
    home = Path(tempfile.mkdtemp(prefix='contract-home-', dir=runtime.work_root))
    env = isolated_environment(home)
    script = str(Path(__file__).resolve())
    flags = ['-E', '-s', '-B', '-X', 'utf8']
    start = time.monotonic()
    with (home / 'native-server.log').open('w', encoding='utf8') as log:
        process = subprocess.Popen(
            [str(runtime.hermes_python), *flags, script, '--native-server',
             '--hermes-root', str(runtime.hermes_root)],
            cwd=str(runtime.hermes_root), env=env, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=log, text=True, encoding='utf8',
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            port = _read_port(process, timeout)
            result = subprocess.run(
                [str(runtime.iris_python), *flags, script, '--worker-origin',
                 f'http://127.0.0.1:{port}', '--output', str(runtime.output)],
                cwd=str(home), env=env, capture_output=True, text=True, encoding='utf8',
                timeout=max(.001, timeout - (time.monotonic() - start)),
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), check=False)
            (home / 'iris-worker.log').write_text(result.stdout + result.stderr, encoding='utf8')
            print(result.stdout, end='', flush=True)
            if result.returncode:
                raise RuntimeError(f'IRIS contract worker failed ({result.returncode}); '
                                   f'see {home / "iris-worker.log"}.')
        finally:
            _stop_server(process)
    return 0


def block_external_network() -> None:
    import socket

    original_connect = socket.socket.connect

    def local_connect(sock, address):
        if isinstance(address, tuple) and address[0] not in ('127.0.0.1', '::1', 'localhost'):
            raise OSError('External network disabled in isolated contract test')
        return original_connect(sock, address)

    socket.socket.connect = local_connect


async def worker(origin, output: Path):
    block_external_network()
    import httpx
    checks = []
    def passed(name):
        checks.append(name)
        print('PASS:', name, flush=True)

    response = json.dumps({'texto_hablado': 'Hola desde la prueba de compatibilidad.',
                           'emocion': 'neutral', 'animacion_virtual': 'idle'})
    async with httpx.AsyncClient(base_url=origin, trust_env=False, timeout=30) as client:
        headers = {'Authorization': 'Bearer iris-isolated-contract-test'}
        async def request(method, path, **kw):
            resp = await client.request(method, path, headers=headers, **kw)
            data = resp.json()
            assert resp.status_code < 300, (path, resp.status_code, data)
            return data
        warnings = await exercise(client, request, origin, checks, passed, response)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({'iris_contracts_ok': True, 'checks': checks, 'native_api_warnings': warnings,
        'scope': 'Installed Hermes + IRIS; isolated state; simulated agent, no LLM calls'}, indent=2), encoding='utf8')


async def native_server(hermes_root: Path):
    block_external_network()
    # The source checkout is an explicit input, not PYTHONPATH or discovery.
    sys.path.insert(0, str(hermes_root.resolve(strict=True)))
    import hermes_cli

    if not Path(hermes_cli.__file__).resolve().is_relative_to(hermes_root.resolve(strict=True)):
        raise RuntimeError('Hermes imported from outside the declared source root.')
    from unittest.mock import MagicMock

    from aiohttp import web
    from gateway.config import PlatformConfig
    from gateway.platforms.api_server import APIServerAdapter
    from hermes_state import SessionDB
    db = SessionDB(Path(os.environ['HERMES_HOME']) / 'state.db')
    response = json.dumps({'texto_hablado': 'Hola desde la prueba de compatibilidad.',
                           'emocion': 'neutral', 'animacion_virtual': 'idle'})
    sid = db.create_session('iris-contract-continuity', 'api_server')
    db.replace_messages(sid, [{'role': 'user', 'content': 'hola'},
                             {'role': 'assistant', 'content': response}])
    adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={
        'key': 'iris-isolated-contract-test', 'model_name': 'contract-fixture'}))
    adapter._session_db = db
    calls = []
    def fake_agent(**kwargs):
        calls.append(kwargs)
        agent = MagicMock()
        agent.session_id = kwargs.get('session_id', 'contract-session')
        agent.session_prompt_tokens = agent.session_completion_tokens = agent.session_total_tokens = 0
        def answer(user_message=None, conversation_history=None, task_id=None, **extra):
            callback = kwargs.get('stream_delta_callback')
            if callable(callback):
                callback(response)
            return {'final_response': response, 'completed': True,
                    'messages': [{'role': 'user', 'content': user_message},
                                 {'role': 'assistant', 'content': response}]}
        agent.run_conversation.side_effect = answer
        return agent
    adapter._create_agent = fake_agent
    app = web.Application()
    app['api_server_adapter'] = adapter
    for method, path, handler in adapter._http_route_table():
        app.router.add_route(method, path, handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    print('PORT=' + str(site._server.sockets[0].getsockname()[1]), flush=True)
    try:
        await asyncio.to_thread(sys.stdin.readline)
    finally:
        await adapter.disconnect()
        await runner.cleanup()
        db.close()


async def exercise(client, request, origin, checks, passed, response):
    from core.async_agents import result_text
    from core.config import IRISConfig
    from core.hermes_bridge import HermesBridge
    from core.hermes_continuity import normalize_messages, normalize_sessions
    caps = await request('GET', '/v1/capabilities')
    for feature in ('session_resources', 'session_fork', 'skills_api', 'model_options', 'run_steer'):
        assert caps['features'].get(feature) is True, feature
    passed('Native capabilities: sessions, skills, reasoning and runs')
    cfg = IRISConfig(hermes_url=origin,
                     hermes_api_key='iris-isolated-contract-test')
    bridge = HermesBridge(cfg)
    await bridge.initialize()
    assert bridge.is_available and bridge._supports_model_options
    plain = await bridge.send_message('Hola')
    assert plain.get('texto_hablado') == 'Hola desde la prueba de compatibilidad.', plain
    passed('IRIS chat, authentication, session headers and response JSON')
    progress = []
    streamed = await bridge.send_message('Hola con streaming', progress_callback=progress.append)
    assert streamed.get('texto_hablado') == plain.get('texto_hablado'), streamed
    passed('IRIS streaming through native Hermes HTTP/SSE')
    # The image stays local and agent execution is simulated.
    image = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aV7cAAAAASUVORK5CYII='
    vision = await bridge.send_message('Describe la imagen', images=[image], reasoning_effort='none')
    assert vision.get('texto_hablado') == plain.get('texto_hablado'), vision
    passed('IRIS vision payload and per-request reasoning')
    sid = 'iris-contract-continuity'
    sessions = await request('GET', '/api/sessions')
    assert any(row['id'] == sid for row in normalize_sessions(sessions))
    messages = normalize_messages(await request('GET', f'/api/sessions/{sid}/messages'))
    assert messages[-1]['content'] == 'Hola desde la prueba de compatibilidad.', messages
    await request('PATCH', f'/api/sessions/{sid}', json={'title': 'IRIS isolated test'})
    await request('POST', f'/api/sessions/{sid}/fork', json={})
    passed('Continuity: list, messages, clean text, rename and fork')
    run = await request('POST', '/v1/runs', json={'input': 'Prueba aislada'})
    for _ in range(100):
        status = await request('GET', '/v1/runs/' + run['run_id'])
        if status['status'] in ('completed', 'failed', 'cancelled'):
            break
        await asyncio.sleep(.05)
    assert status['status'] == 'completed', status
    assert 'Hola desde la prueba' in result_text(status), status
    passed('Async agents: submit, poll, complete and render result')
    warnings = []
    skills = await client.get('/v1/skills', headers={'Authorization': 'Bearer iris-isolated-contract-test'})
    if skills.status_code == 200:
        passed('Native skills API')
    else:
        warnings.append({'endpoint': '/v1/skills', 'status': skills.status_code, 'body': skills.json(),
                         'impact': 'IRIS Skills Hub uses its local catalogue, tested separately'})
        print('WARN: Hermes native /v1/skills returned', skills.status_code, flush=True)
    await bridge.close()
    return warnings


def main(argv: list[str] | None = None) -> int:
    command_parser = parser()
    args = command_parser.parse_args(argv)
    if not 0 < args.timeout <= 900:
        command_parser.error('--timeout must be greater than zero and at most 900 seconds.')
    if args.native_server:
        if args.hermes_root is None:
            command_parser.error('--native-server requires --hermes-root.')
        if os.environ.get('IRIS_HERMES_CONTRACT_CHILD') != '1':
            command_parser.error('--native-server is an internal isolated child mode.')
        asyncio.run(native_server(args.hermes_root))
        return 0
    if args.worker_origin:
        if os.environ.get('IRIS_HERMES_CONTRACT_CHILD') != '1':
            command_parser.error('The contract worker is an internal isolated child mode.')
        from urllib.parse import urlsplit

        address = urlsplit(args.worker_origin)
        if address.scheme != 'http' or address.hostname not in ('127.0.0.1', '::1'):
            command_parser.error('The contract worker accepts only a loopback HTTP origin.')
        if args.output is None:
            command_parser.error('The contract worker requires --output.')
        asyncio.run(worker(args.worker_origin, args.output))
        return 0
    try:
        runtime = resolve_runtime(args)
    except (ValueError, OSError) as exc:
        command_parser.error(str(exc))
    return launch(runtime, timeout=args.timeout)


if __name__ == '__main__':
    raise SystemExit(main())
