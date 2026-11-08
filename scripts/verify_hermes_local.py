"""IRIS local bridges against installed Hermes, with disposable synthetic state."""
import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def launch():
    from core.hermes_runtime import get_hermes_repo, hermes_subprocess_env
    home = ROOT / f'artifacts/hermes-update/local-home-{time.time_ns()}'
    home.mkdir(parents=True)
    # No scheduler or listener is enabled in this synthetic profile.
    (home / 'config.yaml').write_text('wake_word:\n  enabled: false\n', encoding='utf8')
    env = hermes_subprocess_env()
    env.update(HERMES_HOME=str(home), HERMES_REPO=str(get_hermes_repo()),
               IRIS_HOME=str(home / 'iris'))
    result = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--worker'],
                            env=env, cwd=ROOT, capture_output=True, text=True,
                            encoding='utf8', errors='replace', timeout=150)
    (home.parent / 'local-bridges.log').write_text(result.stdout + '\n' + result.stderr, encoding='utf8')
    print(result.stdout)
    if result.returncode:
        print(result.stderr[-6000:])
    return result.returncode


async def worker():
    from core.hermes_runtime import get_hermes_home, run_hermes_cli
    from core import hermes_cron as cron, hermes_kanban as kanban
    from core.hermes_commands import hermes_command_catalogue
    from core.skills_bridge import list_installed_skills
    from core.hermes_continuity import parse_profile_list
    from core.hermes_wake import HermesWakeRuntime
    home = get_hermes_home().resolve()
    assert home.is_relative_to(ROOT / 'artifacts/hermes-update')
    checks = []
    def passed(name):
        print('PASS:', name, flush=True)
        checks.append(name)

    created = cron.create_cron_job({'name': 'IRIS isolated check', 'prompt': 'Synthetic test',
                                   'schedule': '0 0 1 1 *', 'deliver': 'local'})
    assert created.get('success'), created
    job_id = created.get('job_id') or created.get('job', {}).get('id')
    assert job_id, created
    try:
        assert cron.list_cron_jobs()['count'] >= 1
        for action in ('pause', 'resume'):
            result = cron.manage_cron_job(action, job_id)
            assert result.get('success'), result
        result = cron.update_cron_job(job_id, {'name': 'IRIS isolated updated'})
        assert result.get('success'), result
    finally:
        result = cron.manage_cron_job('remove', job_id)
        assert result.get('success'), result
    passed('Native cron: create, list, pause, resume, update and remove')
    task = kanban.create_task({'title': 'IRIS isolated Kanban test'})
    assert task.get('success'), task
    assert kanban.task_detail(task['task_id'])['task']['title'] == 'IRIS isolated Kanban test'
    assert kanban.read_board()['success']
    assert kanban.active_workers()['success']
    passed('Native Kanban: create, detail, board and workers')
    skill = home / 'skills/iris-contract-skill/SKILL.md'
    skill.parent.mkdir(parents=True, exist_ok=True)
    skill.write_text('---\nname: iris-contract-skill\ndescription: Synthetic compatibility fixture\n---\nLocal test.\n', encoding='utf8')
    assert any(row['name'] == 'iris-contract-skill' for row in list_installed_skills(refresh=True))
    passed('IRIS Skills Hub catalogue independent of native /v1/skills')
    catalogue = hermes_command_catalogue(refresh=True)
    assert catalogue['commands'] and catalogue['source'] != 'fallback', catalogue['source']
    passed('Native slash command registry')
    profiles = run_hermes_cli(['profile', 'list'], timeout=20)
    assert profiles['success'] and parse_profile_list(profiles['output']), profiles
    passed('Native profile list consumed by Continuity')
    for name, args in [('skills', ['skills', 'list', '--source', 'all']),
                       ('MCP', ['mcp', 'catalog']), ('plugins', ['plugins', 'list'])]:
        result = run_hermes_cli(args, timeout=30)
        assert result['success'], result.get('error')
        passed('Native ' + name + ' CLI')
    runtime = HermesWakeRuntime()
    try:
        await runtime.ensure_gateway()
        status = await runtime.status()
        assert status['gateway_running'] and not status.get('active'), status
        passed('Native voice/wake WebSocket: startup, authentication and status (no recording)')
    finally:
        await runtime.close()
    (ROOT / 'artifacts/hermes-update/local-bridges.json').write_text(
        json.dumps({'ok': True, 'checks': checks, 'scope': 'Synthetic isolated home; no microphone or scheduler started'}, indent=2), encoding='utf8')


if __name__ == '__main__':
    if '--worker' in sys.argv:
        asyncio.run(worker())
    else:
        raise SystemExit(launch())
