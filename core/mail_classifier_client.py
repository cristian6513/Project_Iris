"""Owned, cancellable, single-use private classifier process. No mail in logs."""
import asyncio
import json
from pathlib import Path

from core.mail_classifier_worker import MARKER


async def worker(home: Path, request: dict, *, timeout=55, script_name="mail_classifier_worker.py", marker=MARKER):
    from wizard.provider_sync import _hermes_probe_kwargs, _hermes_python_probe_command, _resolve_hermes_python
    from core.google_connections import GoogleConnectionsService

    python = _resolve_hermes_python()
    if python is None:
        return {"ok": False, "code": "unsupported_runtime"}
    options = _hermes_probe_kwargs(timeout)
    # Only Core chooses this sibling script; neither value comes from an HTTP request.
    script = Path(__file__).with_name(script_name)
    command = _hermes_python_probe_command(python, f"import runpy; runpy.run_path({str(script)!r}, run_name='__main__')")
    spawn = asyncio.create_task(asyncio.create_subprocess_exec(*command,
        env={**options["env"], "HERMES_HOME": str(home)},
        **{key: options[key] for key in ("cwd", "creationflags") if key in options},
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        limit=128 * 1024))
    process = None
    try:
        process = await asyncio.shield(spawn)
        async with asyncio.timeout(timeout):
            output, _ = await process.communicate(json.dumps(request, ensure_ascii=False).encode())
        if len(output) > 16000:
            return {"ok": False, "code": "invalid_output"}
        line = next((line[len(marker):] for line in reversed(output.decode("utf-8").splitlines()) if line.startswith(marker)), "")
        result = json.loads(line)
        return result if isinstance(result, dict) else {"ok": False, "code": "request_unknown"}
    except (TimeoutError, OSError, ValueError, UnicodeError):
        return {"ok": False, "code": "request_unknown"}
    finally:
        async def collect():
            nonlocal process
            if process is None:
                try:
                    process = await spawn
                except OSError:
                    return
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            await process.wait()
        await GoogleConnectionsService._settle(asyncio.create_task(collect()))
