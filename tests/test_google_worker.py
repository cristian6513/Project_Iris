"""F10.2: the Google worker in Hermes' Python, driven by Core's client, against a stand-in for Google.

No request leaves the machine: ``tests/google_fake_google.py`` answers the worker's HTTP calls
(with a test RSA key for the ID token) and blocks everything else. The "browser" here is an HTTP
GET to the worker's loopback callback, as Google's redirect would make it. Skipped where no Hermes
Python with the Google libraries exists (CI).
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

from core.google_connections import GoogleConnectionsService, GoogleServiceError
from core.google_contracts import SERVICE_SCOPES, requested_scopes
from core.google_worker_client import GoogleClientConfig, GoogleWorkerClient, default_env

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "core" / "google_worker.py"
STAND_IN = Path(__file__).with_name("google_fake_google.py")
CLIENT = GoogleClientConfig.from_document({"installed": {
    "client_id": "123-sintetico.apps.googleusercontent.com", "client_secret": "secreto-sintetico",
    "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://oauth2.googleapis.com/token"}})
KEY = b"k" * 32
CALENDAR = " ".join(requested_scopes(["calendar.read"]))


def _pythons() -> list[str]:
    found = []
    private = ROOT / "artifacts" / "a08-9" / "v0.9.2-qa11" / "runtime" / "runtimes" / "hermes" / "python.exe"
    try:
        from core.hermes_runtime import resolve_hermes_python

        dev = resolve_hermes_python()
    except Exception:
        dev = None
    for candidate in (str(private), dev):
        if not candidate or not Path(candidate).is_file() or candidate in found:
            continue
        probe = subprocess.run([candidate, "-c", "import google_auth_oauthlib, google.oauth2.id_token, cryptography"],
                               capture_output=True, timeout=120)
        if probe.returncode == 0:
            found.append(candidate)
    return found


PYTHONS = _pythons()
pytestmark = [
    pytest.mark.skipif(not PYTHONS, reason="no Hermes Python with the Google libraries here"),
    pytest.mark.skipif(os.name != "nt", reason="the worker protects credentials with Windows DPAPI"),
]
PYTHON = PYTHONS[0] if PYTHONS else ""


class Google:
    """The stand-in's scenario (rewritten between jobs) and its request log."""

    def __init__(self, folder: Path, **scenario) -> None:
        self.folder = folder
        self.file = folder / "escenario.json"
        self.log_file = folder / "peticiones.jsonl"
        self.scenario = {"client_id": CLIENT.client_id, "code": "codigo-sintetico", "log": str(self.log_file),
                         "token": {"scope": CALENDAR, "id_token": {}}}
        self.set(**scenario)

    def set(self, **changes) -> None:
        self.scenario.update(changes)
        self.file.write_text(json.dumps(self.scenario), encoding="utf-8")

    def log(self, kind: str | None = None) -> list[dict]:
        if not self.log_file.exists():
            return []
        entries = [json.loads(line) for line in self.log_file.read_text(encoding="utf-8").splitlines()]
        return [entry for entry in entries if kind is None or entry["kind"] == kind]

    def client(self, python: str = PYTHON, **kwargs) -> GoogleWorkerClient:
        return GoogleWorkerClient(client=CLIENT, command=lambda: [python, "-B", str(STAND_IN), str(self.file), str(WORKER)],
                                  env=default_env, **kwargs)


def visit(consent_url: str, **query) -> tuple[int, dict, str]:
    """What Google's redirect does: a GET to the loopback callback named in the consent URL."""
    params = parse_qs(urlsplit(consent_url).query)
    redirect = params["redirect_uri"][0]
    query = {"state": params["state"][0], **query}
    query = {key: value for key, value in query.items() if value is not None}
    try:
        with urllib.request.urlopen(f"{redirect}?{urlencode(query)}", timeout=10) as response:
            return response.status, dict(response.headers), response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read().decode("utf-8")


async def authorize(google: Google, python: str = PYTHON, scopes=requested_scopes(["calendar.read"]), **query):
    client = google.client(python)
    handle = await client.begin_authorization(session_id="gses_" + "a" * 32, scopes=tuple(scopes), protection=KEY)
    try:
        page = await asyncio.to_thread(visit, handle.url, **{"code": "codigo-sintetico", **query})
        outcome = await asyncio.wait_for(handle.result(), 60)
    finally:
        await handle.close()
    return handle, page, outcome


@pytest.mark.parametrize("python", PYTHONS)
def test_the_worker_runs_by_path_from_outside_the_checkout(tmp_path, python) -> None:
    """A folder with spaces and accents, no Iris on sys.path and a planted decoy module."""
    install = tmp_path / "Iris prueba ñ"
    (install / "core").mkdir(parents=True)
    (install / "wizard").mkdir()
    for source, target in ((WORKER, "core/google_worker.py"), (ROOT / "core" / "google_contracts.py", "core/google_contracts.py"),
                           (ROOT / "core" / "mail_body.py", "core/mail_body.py"),
                           (ROOT / "core" / "windows_dpapi.py", "core/windows_dpapi.py"),
                           (ROOT / "wizard" / "stdin_pipe.py", "wizard/stdin_pipe.py")):
        shutil.copy(source, install / target)
    decoy = tmp_path / "señuelo"
    decoy.mkdir()
    (decoy / "google_contracts.py").write_text("raise SystemExit('decoy imported')\n", encoding="utf-8")

    def env():
        return {**default_env(), "PYTHONPATH": str(decoy)}

    client = GoogleWorkerClient(client=CLIENT, command=lambda: [python, "-B", str(install / "core" / "google_worker.py")], env=env)
    report = asyncio.run(client.probe())
    assert report["dpapi"] is True
    assert all(report["libraries"][name] for name in ("google-auth", "google-auth-oauthlib", "google-api-python-client"))


@pytest.mark.parametrize("python", PYTHONS)
def test_a_complete_authorization_with_pkce_state_nonce_and_a_verified_identity(tmp_path, python) -> None:
    google = Google(tmp_path, token={"scope": CALENDAR, "id_token": {"sub": "sub-ana", "email": "ana@example.com"}})
    handle, page, outcome = asyncio.run(authorize(google, python))
    assert urlsplit(handle.url).hostname == "accounts.google.com"
    consent = google.log("consent_url")[0]
    assert consent == {"kind": "consent_url", "host": "accounts.google.com", "method": "S256", "scope": CALENDAR,
                       "access_type": "offline", "prompt": "consent", "redirect_host": "127.0.0.1", "has_nonce": True}
    status, headers, body = page
    assert status == 200 and "codigo-sintetico" not in body
    assert headers["Cache-Control"] == "no-store" and headers["Referrer-Policy"] == "no-referrer"
    assert google.log("exchange") == [{"kind": "exchange", "pkce_ok": True, "code_ok": True, "redirect_ok": True}]
    assert google.log("certs"), "the ID token was checked against Google's keys (the test key here)"
    assert google.log("blocked") == []
    assert (outcome.error, outcome.subject, outcome.issuer, outcome.client_id, outcome.email) == (
        "", "sub-ana", "https://accounts.google.com", CLIENT.client_id, "ana@example.com")
    assert set(outcome.granted_scopes) == set(requested_scopes(["calendar.read"]))
    assert outcome.credential.startswith(b"IGC1") and b"sintetico-refresco" not in outcome.credential
    with pytest.raises(OSError):  # the listener is gone: a repeated answer finds nobody
        visit(handle.url, code="codigo-sintetico")
    assert handle._job.process.returncode is not None


@pytest.mark.parametrize(("id_token", "error"), [
    ({"nonce": "otro-nonce"}, "identity_unverified"),
    ({"aud": "otro-cliente.apps.googleusercontent.com"}, "identity_unverified"),
    ({"exp_offset": -600}, "identity_unverified"),
    ({"iss": "https://evil.example"}, "identity_unverified"),
    ({"foreign_key": True}, "identity_unverified"),  # signed by a key Google never published
    ({"email_verified": False}, ""),  # the account is verified; only its e-mail is not shown
])
def test_the_identity_is_verified_or_the_attempt_fails(tmp_path, id_token, error) -> None:
    google = Google(tmp_path, token={"scope": CALENDAR, "id_token": id_token})
    _handle, _page, outcome = asyncio.run(authorize(google))
    assert outcome.error == error
    if not error:
        assert outcome.email == "" and outcome.subject == "sub-sintetico"


def test_a_denial_and_answers_for_another_attempt(tmp_path) -> None:
    google = Google(tmp_path)
    _handle, page, outcome = asyncio.run(authorize(google, code=None, error="access_denied"))
    assert outcome.error == "denied" and page[0] == 200 and google.log("exchange") == []

    async def wrong_states():
        handle = await google.client().begin_authorization(session_id="gses_" + "b" * 32,
                                                           scopes=requested_scopes(["calendar.read"]), protection=KEY)
        try:
            statuses = [(await asyncio.to_thread(visit, handle.url, code="x", state=f"falso-{n}"))[0] for n in range(5)]
            return statuses, await asyncio.wait_for(handle.result(), 30)
        finally:
            await handle.close()

    statuses, outcome = asyncio.run(wrong_states())
    assert statuses == [400] * 5 and outcome.error == "state_mismatch" and google.log("exchange") == []


def test_cancelling_closes_the_listener_and_the_process(tmp_path) -> None:
    google = Google(tmp_path)

    async def scenario():
        handle = await google.client().begin_authorization(session_id="gses_" + "c" * 32,
                                                           scopes=requested_scopes(["calendar.read"]), protection=KEY)
        await handle.close()
        return handle

    handle = asyncio.run(scenario())
    assert handle._job.process.returncode is not None, "closed means gone"
    with pytest.raises(OSError):
        visit(handle.url, code="codigo-sintetico")
    assert google.log("exchange") == []


def test_the_worker_ends_when_its_owner_is_gone_and_at_its_own_deadline(tmp_path) -> None:
    google = Google(tmp_path)

    async def owner_gone():
        handle = await google.client().begin_authorization(session_id="gses_" + "d" * 32,
                                                           scopes=requested_scopes(["calendar.read"]), protection=KEY)
        handle._job.process.stdin.close()  # Core died: nobody closes the job properly
        code = await asyncio.wait_for(handle._job.process.wait(), 10)
        return handle, code

    handle, code = asyncio.run(owner_gone())
    assert code == 3
    with pytest.raises(OSError):
        visit(handle.url, code="codigo-sintetico")

    async def deadline():
        handle = await google.client(authorization_timeout_s=1).begin_authorization(
            session_id="gses_" + "e" * 32, scopes=requested_scopes(["calendar.read"]), protection=KEY)
        try:
            return await asyncio.wait_for(handle.result(), 30)
        finally:
            await handle.close()

    assert asyncio.run(deadline()).error == "expired"


def test_renewing_keeps_the_refresh_token_and_says_what_failed(tmp_path) -> None:
    google = Google(tmp_path)
    _handle, _page, outcome = asyncio.run(authorize(google))
    client = google.client()
    renewed = asyncio.run(client.refresh(outcome.credential, protection=KEY))
    assert renewed.error == "" and renewed.credential != outcome.credential
    again = asyncio.run(client.refresh(renewed.credential, protection=KEY))
    assert again.error == ""
    used = [entry["refresh_token"] for entry in google.log("refresh")]
    assert used == ["1//sintetico-refresco", "1//sintetico-refresco"], "Google sent none: the old one stays"

    google.set(refresh={"new_refresh_token": "1//rotado"})
    rotated = asyncio.run(client.refresh(again.credential, protection=KEY))
    google.set(refresh={})
    asyncio.run(client.refresh(rotated.credential, protection=KEY))
    assert google.log("refresh")[-1]["refresh_token"] == "1//rotado"

    for refresh, error in (({"error": "invalid_grant"}, "reauth_required"), ({"network": True}, "offline")):
        google.set(refresh=refresh)
        assert asyncio.run(client.refresh(outcome.credential, protection=KEY)).error == error
    google.set(refresh={})
    assert asyncio.run(client.refresh(outcome.credential, protection=b"z" * 32)).error == "reauth_required", \
        "a credential does not open with another profile's key"


@pytest.mark.parametrize(("revoke", "outcome_state"), [({"status": 200}, "confirmed"), ({"status": 400}, "failed"),
                                                        ({"network": True}, "unknown")])
def test_revoking_reports_what_google_said(tmp_path, revoke, outcome_state) -> None:
    google = Google(tmp_path)
    _handle, _page, outcome = asyncio.run(authorize(google))
    google.set(revoke=revoke)
    assert asyncio.run(google.client().revoke(outcome.credential, protection=KEY)) == outcome_state
    assert google.log("revoke")[0]["token"] == "1//sintetico-refresco"


@pytest.mark.parametrize("output", ["print('no es el protocolo', flush=True)",
                                    "import sys; sys.stdout.write('{' + 'x' * 3000000 + '}\\n'); sys.stdout.flush()",
                                    # a valid consent line, but of another job: never taken as this one's
                                    "import json; print(json.dumps({'schema': 'iris.google-rpc/v1', 'id': 'otro', 'event': 'authorization_required',"
                                    " 'public': {'url': 'https://accounts.google.com/o/oauth2/v2/auth?state=x', 'expires_at': 1}}), flush=True)"])
def test_a_worker_that_breaks_the_protocol_ends_the_job(tmp_path, output) -> None:
    script = tmp_path / "roto.py"
    script.write_text(f"{output}\nimport time\ntime.sleep(30)\n", encoding="utf-8")
    spawned = []
    client = GoogleWorkerClient(client=CLIENT, command=lambda: [sys.executable, str(script)], env=default_env)
    real_spawn = client._spawn

    async def spawn():
        process = await real_spawn()
        spawned.append(process)
        return process

    client._spawn = spawn

    async def scenario():
        with pytest.raises(GoogleServiceError) as refused:
            await client.begin_authorization(session_id="gses_" + "f" * 32, scopes=requested_scopes(["calendar.read"]),
                                             protection=KEY)
        return refused.value.code

    assert asyncio.run(scenario()) == "provider_unavailable"
    assert spawned[0].returncode is not None, "the broken worker was collected"


def test_cancelling_while_the_worker_starts_collects_it(tmp_path) -> None:
    script = tmp_path / "lento.py"
    script.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    spawned = []
    client = GoogleWorkerClient(client=CLIENT, command=lambda: [sys.executable, str(script)], env=default_env)
    real_spawn = client._spawn

    async def spawn():
        process = await real_spawn()
        spawned.append(process)
        return process

    client._spawn = spawn

    async def scenario():
        task = asyncio.ensure_future(client.begin_authorization(
            session_id="gses_" + "1" * 32, scopes=requested_scopes(["calendar.read"]), protection=KEY))
        while not spawned:
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert spawned[0].returncode is not None


def test_the_service_connects_renews_and_revokes_through_the_real_worker(tmp_path) -> None:
    google = Google(tmp_path, token={"scope": " ".join(requested_scopes(["calendar.read", "gmail.read"])),
                                     "id_token": {"sub": "sub-ana", "email": "ana@example.com"}})
    home = tmp_path / "perfil"
    home.mkdir()

    async def scenario():
        service = GoogleConnectionsService(home, worker=google.client())
        session = await service.start_session(["calendar.read", "gmail.read"], request_id="click-0001")
        while service.session(session["id"])["state"] == "starting":
            await asyncio.sleep(0.05)
        waiting = service.session(session["id"])
        assert waiting["state"] == "awaiting_user"
        await asyncio.to_thread(visit, waiting["authorization_url"], code="codigo-sintetico")
        while service.session(session["id"])["state"] in ("awaiting_user", "validating"):
            await asyncio.sleep(0.05)
        final = service.session(session["id"])
        account_id = final["result_account_id"]
        renewed = await service.refresh_credential(account_id)
        revoked = await service.disconnect(account_id, revoke_remote=True)
        await service.drain()
        snapshot = await service.snapshot()
        await service.close()
        return final, renewed, revoked, snapshot

    final, renewed, revoked, snapshot = asyncio.run(scenario())
    assert final["state"] == "committed"
    assert renewed["services"]["gmail.read"]["granted"] is True
    assert revoked["remote_revocation"] == "pending"
    assert snapshot["accounts"][0]["remote_revocation"] == "confirmed"
    text = json.dumps(snapshot)
    assert "sub-ana" not in text and "sintetico-refresco" not in text and CLIENT.client_id not in text
    from core.google_store import GoogleStore

    store = GoogleStore.for_profile(home)
    assert store.credential(final["result_account_id"]) is None, "revoked and confirmed: nothing kept"
    raw = (home / "connections" / "google" / "google.sqlite3").read_bytes()
    assert b"sintetico-refresco" not in raw and b"ya29." not in raw, "no token in clear on disk"
    assert base64.b64encode(KEY) not in raw


def test_the_client_file_must_be_a_google_desktop_client() -> None:
    good = {"installed": {"client_id": "1-a.apps.googleusercontent.com", "client_secret": "secreto-x"}}
    assert GoogleClientConfig.from_document(good).client_id == "1-a.apps.googleusercontent.com"
    assert "secreto-x" not in repr(GoogleClientConfig.from_document(good))
    for bad in (
        {"web": good["installed"]},
        {"installed": {**good["installed"], "client_id": "1-a.example.com"}},
        {"installed": {**good["installed"], "token_uri": "https://evil.example/token"}},
        {"installed": {**good["installed"], "auth_uri": "https://evil.example/auth"}},
        {"installed": {**good["installed"]}, "extra": {}},
        {"installed": {"client_id": "1-a.apps.googleusercontent.com"}},
    ):
        with pytest.raises(ValueError):
            GoogleClientConfig.from_document(bad)
    assert SERVICE_SCOPES  # the catalogue the worker accepts is the contract's


def test_the_worker_only_serves_iris_scopes_its_own_host_and_its_own_client(tmp_path) -> None:
    google = Google(tmp_path)

    async def asks(scopes):
        with pytest.raises(GoogleServiceError) as refused:
            await google.client().begin_authorization(session_id="gses_" + "2" * 32, scopes=tuple(scopes), protection=KEY)
        return refused.value.code

    for scopes in ((*requested_scopes(["gmail.read"]), "https://www.googleapis.com/auth/gmail.modify"),
                   ("openid", "https://www.googleapis.com/auth/userinfo.email"),
                   tuple(reversed(requested_scopes(["calendar.read"])))):
        assert asyncio.run(asks(scopes)) == "invalid_request"

    async def foreign_host():
        import http.client

        handle = await google.client().begin_authorization(session_id="gses_" + "3" * 32,
                                                           scopes=requested_scopes(["calendar.read"]), protection=KEY)
        try:
            params = parse_qs(urlsplit(handle.url).query)
            redirect = urlsplit(params["redirect_uri"][0])

            def request():
                connection = http.client.HTTPConnection(redirect.hostname, redirect.port, timeout=10)
                connection.request("GET", f"{redirect.path}?{urlencode({'state': params['state'][0], 'code': 'codigo-sintetico'})}",
                                   headers={"Host": "evil.example"})  # DNS rebinding: another site's name
                return connection.getresponse().status

            status = await asyncio.to_thread(request)
            later = await asyncio.to_thread(visit, handle.url, code="codigo-sintetico")
            return status, later[0], await asyncio.wait_for(handle.result(), 60)
        finally:
            await handle.close()

    status, later, outcome = asyncio.run(foreign_host())
    assert status == 400 and later == 200 and outcome.error == "", "refused without consuming the attempt"

    _handle, _page, outcome = asyncio.run(authorize(google))
    other = GoogleClientConfig.from_document({"installed": {"client_id": "999-otro.apps.googleusercontent.com",
                                                            "client_secret": "otro"}})
    client = GoogleWorkerClient(client=other, command=google.client()._command, env=default_env)
    assert asyncio.run(client.refresh(outcome.credential, protection=KEY)).error == "reauth_required"
