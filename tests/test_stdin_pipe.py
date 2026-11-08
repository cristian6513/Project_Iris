"""A08.7: parent pipes are watched and read without a pending ReadFile (Windows)."""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from wizard import stdin_pipe

ROOT = Path(__file__).resolve().parents[1]
WINDOWS = pytest.mark.skipif(sys.platform != "win32", reason="Windows serializes synchronous I/O on a pipe handle")

READER_CHILD = """
import ctypes, sys, threading, time
sys.path.insert(0, sys.argv[1])
from wizard.stdin_pipe import LineReader
reader = LineReader(interval=0.02)
print("ready", flush=True)
idle = threading.Thread(target=lambda: print("got " + repr(reader.readline()), flush=True))
idle.start()
time.sleep(0.5)  # the reader is now waiting for a line that has not arrived
start = time.monotonic()
info = ctypes.create_string_buffer(64)
ctypes.windll.kernel32.GetFileInformationByHandle(ctypes.windll.kernel32.GetStdHandle(-10), info)
print(f"queried in {time.monotonic() - start:.2f}s", flush=True)
idle.join(20)
print("got " + repr(reader.readline()), flush=True)
print("got " + repr(reader.readline()), flush=True)
"""


def _collect(child, lines, count, timeout=30):
    def read():
        for raw in child.stdout:
            lines.append(raw.decode().strip())
            if len(lines) >= count:
                return

    thread = threading.Thread(target=read, daemon=True)
    thread.start()
    thread.join(timeout)


@WINDOWS
def test_line_reader_waits_without_stalling_other_stdin_queries():
    child = subprocess.Popen([sys.executable, "-c", READER_CHILD, str(ROOT)], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    lines: list[str] = []
    try:
        _collect(child, lines, 2)
        assert lines[:2] == ["ready", lines[1]] and lines[1].startswith("queried in"), lines
        child.stdin.write('{"action": "a"}\n{"action": "b"}\n'.encode())
        child.stdin.flush()
        _collect(child, lines, 4)
        assert lines[2:4] == ["got '{\"action\": \"a\"}\\n'", "got '{\"action\": \"b\"}\\n'"], lines
        child.stdin.close()
        _collect(child, lines, 5)
        assert lines[4] == "got ''", lines  # EOF
    finally:
        child.kill()


def test_non_pipe_streams_keep_their_blocking_behaviour():
    stream = SimpleNamespace(buffer=io.BytesIO(b"x"), readline=io.StringIO("uno\ndos\n").readline)
    stdin_pipe.wait_for_eof(stream)
    assert stream.buffer.read() == b""
    reader = stdin_pipe.LineReader(stream)
    assert [reader.readline(), reader.readline(), reader.readline()] == ["uno\n", "dos\n", ""]


FAKE_HERMES = {
    # Just what the workers import, so they run under ``-I -S`` without Hermes or fastapi.
    "fastapi/__init__.py": (
        "class HTTPException(Exception):\n"
        "    def __init__(self, status_code, detail=None):\n"
        "        self.status_code, self.detail = status_code, detail\n"),
    "hermes_cli/__init__.py": "__version__ = '0.0-test'\n",
    "hermes_cli/web_models.py": (
        "class MessagingPlatformUpdate: pass\n"
        "class WhatsAppOnboardingApply: pass\n"
        "class WhatsAppOnboardingStart: pass\n"),
    "hermes_cli/web_server_messaging.py": "_whatsapp_onboarding_sessions = {}\n",
    "hermes_cli/web_routers/__init__.py": "",
    "hermes_cli/web_routers/messaging.py": "async def cancel_whatsapp_onboarding(pairing_id):\n    pass\n",
    # A device-code login the user never approves.
    "hermes_cli/web_routers/oauth.py": (
        "async def _start_device_code_flow(provider):\n"
        "    return {'user_code': 'TEST-CODE', 'verification_url': 'https://example.test/device',\n"
        "            'expires_in': 600, 'session_id': 'pending-session'}\n"),
    "hermes_cli/web_server_oauth.py": (
        "import threading\n"
        "_oauth_sessions = {'pending-session': {'status': 'pending'}}\n"
        "_oauth_sessions_lock = threading.Lock()\n"),
}


@pytest.fixture
def isolated_workers(tmp_path):
    """The two workers and their helper copied outside the project, plus a fake Hermes.

    The packaged Hermes interpreter runs isolated (python311._pth): neither Iris nor the
    worker's folder is on sys.path. ``-I -S`` on these copies reproduces that; the old
    ``from stdin_pipe import`` fallback failed there and the messaging catalog never loaded
    (A08.9 QA on a clean Windows 11)."""
    for name in ("hermes_auth_worker.py", "hermes_messaging_worker.py", "stdin_pipe.py"):
        shutil.copy2(ROOT / "wizard" / name, tmp_path / name)
    repo = tmp_path / "hermes"
    for relative, text in FAKE_HERMES.items():
        (repo / relative).parent.mkdir(parents=True, exist_ok=True)
        (repo / relative).write_text(text, "utf-8")
    return tmp_path, repo


def _isolated(*args, **kwargs):
    return subprocess.Popen([sys.executable, "-I", "-S", *map(str, args)], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)


def test_workers_poll_their_pipe_instead_of_reading_it():
    for name in ("hermes_auth_worker.py", "hermes_messaging_worker.py"):
        source = (ROOT / "wizard" / name).read_text("utf-8")
        assert "sys.stdin.buffer.read()" not in source and "asyncio.to_thread(sys.stdin.readline)" not in source


def test_the_messaging_worker_answers_a_request_under_the_isolated_interpreter(isolated_workers):
    folder, repo = isolated_workers
    worker = _isolated(folder / "hermes_messaging_worker.py", repo)
    try:
        out, err = worker.communicate(b'{"action": "no-such-action", "data": {}}\n', timeout=60)
    finally:
        worker.kill()
    assert worker.returncode == 0, err.decode("utf-8", "replace")  # EOF after the request: a clean stop
    assert json.loads(out) == {"ok": False, "status": 400, "message": "Operación de mensajería desconocida."}


def test_a_pending_login_ends_when_the_wizard_goes_away(isolated_workers):
    folder, repo = isolated_workers
    worker = _isolated(folder / "hermes_auth_worker.py", "nous", "device", repo)
    try:
        first = json.loads(worker.stdout.readline())
        assert first["status"] == "pending" and first["user_code"] == "TEST-CODE"
        worker.stdin.close()  # the wizard died: the watchdog must stop the login
        assert worker.wait(timeout=30) == 1, worker.stderr.read().decode("utf-8", "replace")
        assert b"approved" not in worker.stdout.read()
    finally:
        worker.kill()


def test_an_unknown_provider_is_refused_under_the_isolated_interpreter(isolated_workers):
    folder, repo = isolated_workers
    worker = _isolated(folder / "hermes_auth_worker.py", "no-such-provider", "device", repo)
    try:
        # Stdin stays open: on EOF the watchdog ends the process on purpose.
        assert worker.wait(timeout=60) == 0, worker.stderr.read().decode("utf-8", "replace")
        assert json.loads(worker.stdout.read()) == {"status": "error", "code": "external_required"}
    finally:
        worker.stdin.close()
        worker.kill()
