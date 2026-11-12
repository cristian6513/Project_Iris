"""A08.9: the QA script's `nucleo` phase identifies the Core exactly like the Core does.

The real phase runs in Windows PowerShell against local stand-ins for /api/status.

- Profile and Hermes: the stand-in returns the ids of the real runtime_service_identity.
  A valid alias (a junction) must pass; a different canonical folder must fail.
- Owner: the process checked is the one behind 127.0.0.1, never just the first listener
  of the port. Stand-ins run from a copy of the base interpreter placed inside the
  installation (active or older version) or from outside it, all on loopback only.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from core.runtime_descriptor import runtime_service_identity

SCRIPT = Path(__file__).resolve().parents[1] / "desktop" / "installer" / "qa" / "verificar-instalacion.ps1"
POWERSHELL = shutil.which("powershell.exe")
PROFILE_CHECK = "Usa el perfil esperado"
HERMES_CHECK = "Usa la carpeta de Hermes esperada"
LISTENER_CHECK = "El proceso que escucha es de la versión activa de esta instalación"
ACTIVE, OLDER = "abcd1234", "0000aaaa"
BASE = Path(getattr(sys, "_base_executable", sys.executable))
SERVER = """
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
address, port, body, ready = sys.argv[1], int(sys.argv[2]), open(sys.argv[3], "rb").read(), sys.argv[4]
class Status(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *args):
        pass
server = HTTPServer((address, port), Status)
open(ready, "w").close()
server.serve_forever()
"""

pytestmark = [
    pytest.mark.skipif(os.name != "nt" or not POWERSHELL, reason="needs Windows PowerShell"),
    pytest.mark.skipif(not SCRIPT.is_file(), reason="needs the internal installer QA script"),
]


def _junction(link: Path, target: Path) -> None:
    made = subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
    if made.returncode:
        pytest.skip("directory junctions are not available here")


@pytest.fixture
def folders(tmp_path):
    names = {"perfil": "perfil real ÁÑ", "otro_perfil": "otro perfil", "hermes": "hermes real", "otro_hermes": "otro hermes"}
    made = {key: tmp_path / "datos" / name for key, name in names.items()}
    for folder in made.values():
        folder.mkdir(parents=True)
    made["alias_perfil"] = tmp_path / "alias del perfil"
    made["alias_hermes"] = tmp_path / "alias de hermes"
    _junction(made["alias_perfil"], made["perfil"])
    _junction(made["alias_hermes"], made["hermes"])
    yield made
    for key in ("alias_perfil", "alias_hermes"):
        os.rmdir(made[key])


def _status(profile: Path, hermes: Path, monkeypatch) -> dict:
    monkeypatch.setenv("HERMES_HOME", str(hermes))
    identity = runtime_service_identity(profile)
    return {"name": "IRIS", "status": "running", "version": "0.9.2",
            "runtime": {"mode": "packaged", "iris_revision": "rev-iris", "hermes_revision": "rev-hermes",
                        "profile_id": identity["profile_id"], "hermes_profile_id": identity["hermes_profile_id"]}}


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "Iris OS"
    root.mkdir(exist_ok=True)
    record = {"iris": "0.9.2", "hermes": "0.21.4", "iris_revision": "rev-iris", "hermes_revision": "rev-hermes"}
    (root / "install.json").write_text(json.dumps({"active": ACTIVE, "versions": {ACTIVE: record}}), "utf-8")
    return root


def _nucleo(tmp_path: Path, port: int, iris_home: Path, hermes_home: Path) -> dict[str, str]:
    root = _root(tmp_path)
    reports = tmp_path / "informes"
    reports.mkdir(exist_ok=True)
    env = {**os.environ, "IRIS_HOME": str(iris_home), "HERMES_HOME": str(hermes_home)}
    env.pop("PSModulePath", None)  # Windows PowerShell finds its own modules
    run = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                          "-File", str(SCRIPT), "-Fase", "nucleo", "-Carpeta", str(root),
                          "-Puerto", str(port), "-EsperaSegundos", "30"],
                         cwd=reports, env=env, capture_output=True, timeout=180)
    assert run.returncode == 0, run.stdout.decode("utf-8", "replace") + run.stderr.decode("utf-8", "replace")
    (report,) = reports.glob("qa-iris-nucleo-*.json")
    checks = json.loads(report.read_text("utf-8-sig"))["pruebas"]
    report.unlink()
    return {check["prueba"]: check["estado"] for check in checks}


@contextmanager
def _in_process(status: dict):
    body = json.dumps(status).encode("utf-8")

    class Status(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - http.server API
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Status)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()


def _interpreter(folder: Path) -> Path:
    """A copy of the base interpreter whose process path lies in `folder`; its stdlib stays where it is."""
    folder.mkdir(parents=True, exist_ok=True)
    for name in ("python.exe", "python311.dll", "python3.dll", "vcruntime140.dll", "vcruntime140_1.dll"):
        if (BASE.parent / name).exists():
            shutil.copy2(BASE.parent / name, folder / name)
    (folder / "python311._pth").write_text(f"{BASE.parent / 'Lib'}\n{BASE.parent / 'DLLs'}\n", "utf-8")
    return folder / "python.exe"


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@contextmanager
def _servers(tmp_path: Path, status: dict, port: int, *listeners: tuple[Path, str]):
    """Start one stand-in per (interpreter, address) on the same port, all answering `status`."""
    work = tmp_path / "servidores"
    work.mkdir(exist_ok=True)
    (work / "server.py").write_text(SERVER, "utf-8")
    (work / "status.json").write_text(json.dumps(status), "utf-8")
    started = []
    try:
        for index, (python, address) in enumerate(listeners):
            ready = work / f"listo-{index}"
            started.append(subprocess.Popen([str(python), "-I", str(work / "server.py"), address, str(port),
                                             str(work / "status.json"), str(ready)], cwd=work))
            deadline = time.monotonic() + 30
            while not ready.exists():
                if started[-1].poll() is not None or time.monotonic() > deadline:
                    pytest.fail(f"stand-in {python} on {address}:{port} did not start")
                time.sleep(0.1)
        yield
    finally:
        for process in started:
            process.kill()
            process.wait(timeout=30)


def test_a_valid_alias_of_the_profile_and_of_hermes_passes(folders, tmp_path, monkeypatch):
    with _in_process(_status(folders["alias_perfil"], folders["alias_hermes"], monkeypatch)) as port:
        checks = _nucleo(tmp_path, port, folders["alias_perfil"], folders["alias_hermes"])
    assert checks[PROFILE_CHECK] == "ok" and checks[HERMES_CHECK] == "ok"
    assert checks["Responde Iris en ejecución"] == "ok" and checks["Versión del Core = versión activa"] == "ok"
    assert checks[LISTENER_CHECK] == "falla"  # this stand-in is not the installation's Core


def test_a_hermes_folder_not_created_yet_behind_an_alias_passes(folders, tmp_path, monkeypatch):
    hermes = folders["alias_hermes"] / "todavía no existe"
    with _in_process(_status(folders["perfil"], hermes, monkeypatch)) as port:
        checks = _nucleo(tmp_path, port, folders["perfil"], hermes)
    assert checks[PROFILE_CHECK] == "ok" and checks[HERMES_CHECK] == "ok"


def test_a_different_canonical_profile_fails(folders, tmp_path, monkeypatch):
    with _in_process(_status(folders["perfil"], folders["hermes"], monkeypatch)) as port:
        checks = _nucleo(tmp_path, port, folders["otro_perfil"], folders["hermes"])
    assert checks[PROFILE_CHECK] == "falla" and checks[HERMES_CHECK] == "ok"


def test_a_different_canonical_hermes_folder_fails(folders, tmp_path, monkeypatch):
    with _in_process(_status(folders["alias_perfil"], folders["hermes"], monkeypatch)) as port:
        checks = _nucleo(tmp_path, port, folders["alias_perfil"], folders["otro_hermes"])
    assert checks[PROFILE_CHECK] == "ok" and checks[HERMES_CHECK] == "falla"


OWNERS = {
    # the answer comes from the active version; a foreign listener on another address is ignored
    "active on 127.0.0.1, foreign on 127.0.0.2": ([("active", "127.0.0.1"), ("foreign", "127.0.0.2")], "ok"),
    # the inverse: whoever answered is foreign, even with the active version on another address
    "foreign on 127.0.0.1, active on 127.0.0.2": ([("foreign", "127.0.0.1"), ("active", "127.0.0.2")], "falla"),
    "older version on 127.0.0.1": ([("older", "127.0.0.1")], "falla"),
    # two owners on the very address asked: which one answered is unknown, so never approved
    "active and foreign both on 127.0.0.1": ([("active", "127.0.0.1"), ("foreign", "127.0.0.1")], "falla"),
}


@pytest.mark.parametrize("listeners, expected", OWNERS.values(), ids=OWNERS.keys())
def test_the_owner_is_the_process_behind_the_address_asked(folders, tmp_path, monkeypatch, listeners, expected):
    root = _root(tmp_path)
    pythons = {"active": _interpreter(root / "v" / ACTIVE / "runtimes" / "iris"),
               "older": _interpreter(root / "v" / OLDER / "runtimes" / "iris"), "foreign": BASE}
    port = _free_port()
    status = _status(folders["perfil"], folders["hermes"], monkeypatch)
    with _servers(tmp_path, status, port, *((pythons[who], address) for who, address in listeners)):
        checks = _nucleo(tmp_path, port, folders["perfil"], folders["hermes"])
    assert checks[LISTENER_CHECK] == expected
    assert checks[PROFILE_CHECK] == "ok" and checks["Responde Iris en ejecución"] == "ok"  # the rest is unchanged


def _function(name: str) -> str:
    text = SCRIPT.read_text("utf-8-sig")
    return re.search(rf"^function {name}\b.*?^}}\r?$", text, re.M | re.S).group(0)


LEVELS = {
    "exact address wins over another loopback address": ([("127.0.0.2", 1), ("127.0.0.1", 2)], "2"),
    "IPv4 wildcard": ([("0.0.0.0", 3)], "3"),
    "dual-stack wildcard": ([("::", 4)], "4"),
    "exact address wins over the wildcard": ([("0.0.0.0", 3), ("127.0.0.1", 5)], "5"),
    "one process on both wildcards": ([("0.0.0.0", 3), ("::", 3)], "3"),
    "two processes on the exact address": ([("127.0.0.1", 5), ("127.0.0.1", 6)], ""),
    "two processes on the wildcards": ([("0.0.0.0", 3), ("::", 4)], ""),
    "only another address": ([("127.0.0.2", 1)], ""),
    "nobody": ([], ""),
}


@pytest.mark.parametrize("listeners, expected", LEVELS.values(), ids=LEVELS.keys())
def test_the_owner_rule_for_wildcards_and_ambiguity(listeners, expected):
    """Wildcard binds are not opened for real here: they could raise a firewall prompt."""
    records = ", ".join(f"[pscustomobject]@{{ LocalAddress = '{address}'; OwningProcess = {pid} }}"
                        for address, pid in listeners)
    command = f"{_function('ListenerOwner')}\n$r = ListenerOwner @({records}); Write-Output \"[$($r.id)]\""
    run = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", command],
                         capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stderr
    assert re.search(r"\[(.*)\]", run.stdout).group(1) == expected
