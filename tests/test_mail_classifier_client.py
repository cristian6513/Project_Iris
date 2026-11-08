"""Cancellation while Windows is still spawning must own the late process too."""
import asyncio
from pathlib import Path

import pytest

from core.mail_classifier_client import worker


@pytest.mark.asyncio
async def test_cancellation_during_spawn_waits_then_kills_before_sending_stdin(monkeypatch):
    from wizard import provider_sync
    entered, release = asyncio.Event(), asyncio.Event()
    class Process:
        returncode = None
        waited = False
        def kill(self): self.returncode = -9
        async def wait(self): self.waited = True
        async def communicate(self, data): raise AssertionError('Cancelled mail must never be sent')
    process = Process()
    async def spawn(*args, **kwargs):
        entered.set(); await release.wait(); return process
    monkeypatch.setattr(provider_sync, '_resolve_hermes_python', lambda: Path('synthetic.exe'))
    monkeypatch.setattr(provider_sync, '_hermes_probe_kwargs', lambda timeout: {'env':{}})
    monkeypatch.setattr(provider_sync, '_hermes_python_probe_command', lambda *args: ['synthetic.exe'])
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', spawn)
    task = asyncio.create_task(worker(Path('test-profile'), {'command':'classify','message':{'private':'test'}}))
    await entered.wait(); task.cancel(); await asyncio.sleep(0)
    assert not task.done()
    task.cancel(); release.set()
    with pytest.raises(asyncio.CancelledError): await task
    assert process.returncode == -9 and process.waited
