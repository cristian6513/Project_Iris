"""F10.1: contracts of Iris' Google connections (scopes, grants, projections, private RPC)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from core.google_contracts import (
    EMAIL_SCOPE,
    MAX_CREDENTIAL_BYTES,
    MAX_RPC_LINE_BYTES,
    RPC_SCHEMA,
    SERVICE_SCOPES,
    ContractError,
    RpcError,
    decode_credential,
    encode_credential,
    evaluate_grant,
    new_account_id,
    new_session_id,
    normalize_scopes,
    parse_rpc,
    public_part,
    public_session,
    requested_scopes,
    rpc_line,
    services_in,
    valid_authorization_url,
    validate_services,
)

ROOT = Path(__file__).resolve().parents[1]
AUTH = "https://www.googleapis.com/auth/"
CALENDAR = list(SERVICE_SCOPES["calendar.read"])
GMAIL = list(SERVICE_SCOPES["gmail.read"])


def test_only_read_scopes_and_identity_are_ever_requested() -> None:
    assert requested_scopes(["gmail.read"]) == ("openid", EMAIL_SCOPE, AUTH + "gmail.readonly")
    assert requested_scopes(["calendar.read"]) == (
        "openid", EMAIL_SCOPE, AUTH + "calendar.calendarlist.readonly", AUTH + "calendar.events.readonly")
    every = {scope for service in SERVICE_SCOPES for scope in requested_scopes([service])}
    assert all(scope in ("openid", EMAIL_SCOPE) or scope.endswith("readonly") for scope in every)
    assert validate_services(["gmail.read", "calendar.read"]) == ("calendar.read", "gmail.read")
    for bad in ([], ["drive.read"], ["gmail.read", "gmail.read"], "gmail.read", [1]):
        with pytest.raises(ContractError):
            validate_services(bad)


def test_google_aliases_are_the_same_scope() -> None:
    assert normalize_scopes("openid email") == normalize_scopes(["openid", EMAIL_SCOPE])
    assert services_in(["openid", *CALENDAR]) == ("calendar.read",)
    assert services_in([CALENDAR[0]]) == (), "a service counts only with every one of its scopes"


@pytest.mark.parametrize(("granted", "services", "missing", "error"), [
    (["openid", "email", *CALENDAR, *GMAIL], ("calendar.read", "gmail.read"), (), None),
    (["openid", "email", *CALENDAR], ("calendar.read",), ("gmail.read",), None),  # partial
    (["openid", "email", CALENDAR[0], *GMAIL], ("gmail.read",), ("calendar.read",), None),  # half a service
    (["openid", "email"], (), ("calendar.read", "gmail.read"), "permission_missing"),
    (["email", *CALENDAR, *GMAIL], ("calendar.read", "gmail.read"), (), "identity_unverified"),
])
def test_a_grant_is_compared_with_what_was_asked(granted, services, missing, error) -> None:
    grant = evaluate_grant(["calendar.read", "gmail.read"], granted)
    assert (grant.services, grant.missing, grant.error) == (services, missing, error)
    assert grant.partial == bool(missing)


@pytest.mark.parametrize("extra", [
    AUTH + "gmail.modify",  # writing
    AUTH + "gmail.send",
    AUTH + "drive",
    AUTH + "gmail.readonly",  # even read-only, if Iris did not ask for it
    "profile",
])
def test_a_broader_grant_is_never_trimmed_into_read_only(extra) -> None:
    grant = evaluate_grant(["calendar.read"], ["openid", "email", *CALENDAR, extra])
    assert grant.error == "unexpected_grant"
    assert grant.unexpected


@pytest.mark.parametrize(("url", "shown"), [
    ("https://accounts.google.com/o/oauth2/v2/auth?client_id=x&state=y", True),
    ("http://accounts.google.com/o/oauth2/v2/auth", False),
    ("https://accounts.google.com.evil.example/auth", False),
    ("https://evil.example/https://accounts.google.com/", False),
    ("https://user@accounts.google.com/auth", False),
    ("https://accounts.google.com:8443/auth", False),
    ("javascript:alert(1)", False),
    (None, False),
])
def test_only_a_google_consent_page_is_shown_and_only_while_waiting(url, shown) -> None:
    assert valid_authorization_url(url) is shown
    common = dict(session_id=new_session_id(), services=["gmail.read"], account_id=None, expires_at=1.0,
                  error_code="", result_account_id=None)
    waiting = public_session(state="awaiting_user", authorization_url=url, **common)
    assert waiting["authorization_url"] == (url if shown else None)
    assert public_session(state="committed", authorization_url=url, **common)["authorization_url"] is None


def _request(**fields) -> dict:
    return {"schema": RPC_SCHEMA, "id": "job_1", "method": "auth.begin", **fields}


def test_the_private_protocol_accepts_its_contract_and_nothing_else() -> None:
    account, session = new_account_id(), new_session_id()
    blob = b"\x01\x02 protegido"
    line = rpc_line({"id": "job_1", "event": "authorization_candidate", "session_id": session,
                     "account_id": account, "generation": 2, "public": {"granted_services": ["gmail.read"]},
                     "private": {"credential": encode_credential(blob)}})
    message = parse_rpc(line)
    assert decode_credential(message["private"]["credential"]) == blob
    assert "private" not in public_part(message) and "credential" not in json.dumps(public_part(message))

    for bad in (
        _request(method="eval"),  # outside the closed list
        {"schema": RPC_SCHEMA, "id": "job_1", "event": "token"},
        _request(event="ready"),  # a request and an event at once
        {"schema": RPC_SCHEMA, "id": "job_1"},
        _request(schema="otro/v1"),
        _request(id="../x"),
        _request(path="C:/Windows"),  # unknown field
        _request(session_id="s_1"),
        _request(account_id="me@example.com"),
        _request(generation=-1),
        _request(generation=True),
        _request(params=["x"]),
        _request(private={"token": "x"}),
        _request(private={"credential": "no es base64!"}),
    ):
        with pytest.raises(RpcError):
            parse_rpc(json.dumps(bad).encode())
    for raw in (b"\xff\xfe", b"not json", b"[]", b"{" + b" " * MAX_RPC_LINE_BYTES + b"}"):
        with pytest.raises(RpcError):
            parse_rpc(raw)
    with pytest.raises(RpcError):
        encode_credential(b"x" * (MAX_CREDENTIAL_BYTES + 1))
    with pytest.raises(RpcError):
        rpc_line({"id": "job_1", "method": "shell"})  # what Iris writes is checked too


def test_the_worker_can_load_the_contracts_with_the_standard_library_only() -> None:
    """The worker (F10.2) loads this file by path in an isolated Python: no Core, no pydantic."""
    script = (
        "import importlib.util, sys\n"
        f"spec = importlib.util.spec_from_file_location('gc', {str(ROOT / 'core' / 'google_contracts.py')!r})\n"
        "module = importlib.util.module_from_spec(spec); sys.modules['gc'] = module; spec.loader.exec_module(module)\n"
        "print(module.requested_scopes(['gmail.read'])[-1])\n"
        "assert not any(name.startswith(('core', 'pydantic', 'fastapi')) for name in sys.modules)\n"
    )
    pythons = [[sys.executable, "-I", "-S"]]
    private = ROOT / "artifacts" / "a08-9" / "v0.9.2-qa11" / "runtime" / "runtimes" / "hermes" / "python.exe"
    if private.is_file():
        pythons.append([str(private)])
    for python in pythons:
        run = subprocess.run([*python, "-c", script], capture_output=True, text=True, timeout=60)
        assert run.returncode == 0, run.stderr
        assert run.stdout.strip() == AUTH + "gmail.readonly"
