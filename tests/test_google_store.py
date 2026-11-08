"""F10.1: the profile's Google connections store. Synthetic identities and blobs only."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
from pathlib import Path

import pytest

from core.google_contracts import SERVICE_SCOPES, requested_scopes
from core.google_store import (
    AccountChanged,
    AccountExists,
    AuthorizationCandidate,
    GoogleStore,
    GoogleStoreError,
    GoogleStorePathError,
    GoogleStoreVersionError,
    SCHEMA_VERSION,
    RevisionConflict,
    ServiceNotGranted,
    StaleGeneration,
    profile_store_path,
)

BOTH = ("calendar.read", "gmail.read")
SECRET_SUBJECT = "109876543210-sintetico"
SECRET_CLIENT = "client-sintetico.apps.example"


def candidate(subject: str = SECRET_SUBJECT, *, services=BOTH, granted=None, blob: bytes = b"BLOB-A",
              email: str = "ana@example.com") -> AuthorizationCandidate:
    scopes = requested_scopes(services)
    return AuthorizationCandidate(client_id=SECRET_CLIENT, issuer="https://accounts.google.com", subject=subject,
                                  email=email, requested_scopes=scopes,
                                  granted_scopes=tuple(granted if granted is not None else scopes), credential=blob)


def store(tmp_path: Path, name: str = "perfil") -> GoogleStore:
    home = tmp_path / name
    home.mkdir(exist_ok=True)
    return GoogleStore.for_profile(home)


def test_two_accounts_in_one_profile_and_nothing_crosses_profiles(tmp_path) -> None:
    first = store(tmp_path, "uno")
    ana = first.commit_authorization(candidate())
    bea = first.commit_authorization(candidate("sub-bea", services=("calendar.read",), blob=b"BLOB-B",
                                               email="bea@example.com"))
    assert [record.id for record in first.accounts()] == [ana.id, bea.id]
    assert ana.id != bea.id and ana.id.startswith("gacc_")
    assert first.credential(ana.id).credential == b"BLOB-A"
    assert first.credential(bea.id).credential == b"BLOB-B"
    assert (ana.state, bea.state) == ("connected", "connected")
    assert profile_store_path(tmp_path / "uno").is_file()

    other = store(tmp_path, "dos")
    assert other.accounts() == [] and other.credential(ana.id) is None


def test_nothing_secret_leaves_a_record(tmp_path) -> None:
    db = store(tmp_path)
    record = db.commit_authorization(candidate(granted=("openid", "email", *SERVICE_SCOPES["calendar.read"])))
    public = json.dumps(record.public())
    for secret in (SECRET_SUBJECT, SECRET_CLIENT, "BLOB-A", "accounts.google.com"):
        assert secret not in public and secret not in repr(record)
    ref = repr(db.credential(record.id))
    assert "BLOB-A" not in ref and SECRET_SUBJECT not in ref
    assert record.public()["auth_state"] == "partial"
    assert record.public()["services"]["gmail.read"] == {"granted": False, "enabled": False,
                                                         "availability": "permission_missing"}
    assert "BLOB" not in json.dumps(db.history()) and SECRET_SUBJECT not in json.dumps(db.history())


def test_an_identity_is_one_account(tmp_path) -> None:
    db = store(tmp_path)
    ana = db.commit_authorization(candidate())
    with pytest.raises(AccountExists) as refused:
        db.commit_authorization(candidate(blob=b"OTRO"))
    assert refused.value.account_id == ana.id
    assert db.credential(ana.id).credential == b"BLOB-A", "the connected one is not overwritten"


def test_reconnecting_needs_the_same_identity_and_generation(tmp_path) -> None:
    db = store(tmp_path)
    ana = db.commit_authorization(candidate())
    db.set_enabled_services(ana.id, ["calendar.read", "gmail.read"], expected_revision=ana.revision)
    with pytest.raises(AccountChanged):
        db.commit_authorization(candidate("sub-otra"), account_id=ana.id, expected_generation=ana.generation)
    with pytest.raises(StaleGeneration):
        db.commit_authorization(candidate(blob=b"NUEVO"), account_id=ana.id, expected_generation=ana.generation + 5)
    assert db.credential(ana.id).credential == b"BLOB-A"

    again = db.commit_authorization(candidate(services=BOTH, granted=requested_scopes(["calendar.read"]),
                                              blob=b"NUEVO"), account_id=ana.id, expected_generation=ana.generation)
    assert again.generation == ana.generation + 1 and again.credential_revision == ana.credential_revision + 1
    assert again.enabled_services == ("calendar.read",), "still enabled, limited to what is granted now"
    assert again.state == "partial" and db.credential(ana.id).credential == b"NUEVO"


def test_every_write_names_what_it_read(tmp_path) -> None:
    db = store(tmp_path)
    ana = db.commit_authorization(candidate())
    ref = db.credential(ana.id)
    db.replace_credential(ana.id, generation=ref.generation, credential_revision=ref.credential_revision,
                          credential=b"RENOVADA")
    with pytest.raises(StaleGeneration):  # a renewal from the old credential, late
        db.replace_credential(ana.id, generation=ref.generation, credential_revision=ref.credential_revision,
                              credential=b"VIEJA")
    assert db.credential(ana.id).credential == b"RENOVADA"

    current = db.account(ana.id)
    with pytest.raises(RevisionConflict):
        db.set_enabled_services(ana.id, ["gmail.read"], expected_revision=current.revision - 1)
    partial = db.commit_authorization(candidate("sub-cal", services=("calendar.read",), blob=b"C"))
    with pytest.raises(ServiceNotGranted):
        db.set_enabled_services(partial.id, ["gmail.read"], expected_revision=partial.revision)


def test_disconnecting_drops_the_credential_unless_a_revocation_was_asked(tmp_path) -> None:
    db = store(tmp_path)
    ana = db.commit_authorization(candidate())
    record, blob = db.disconnect(ana.id)
    assert blob is None and record.state == "disconnected" and record.generation == ana.generation + 1
    assert not record.has_credential and db.credential(ana.id) is None

    bea = db.commit_authorization(candidate("sub-bea", blob=b"BLOB-B"))
    record, blob = db.disconnect(bea.id, revoke_remote=True)
    assert blob == b"BLOB-B" and record.remote_revocation == "pending"
    assert db.credential(bea.id) is None, "kept aside, out of every read"
    assert db.record_revocation(bea.id, revocation_generation=record.revocation_generation + 1,
                                outcome="confirmed").remote_revocation == "pending", "another generation's outcome"
    done = db.record_revocation(bea.id, revocation_generation=record.revocation_generation, outcome="unknown")
    assert done.remote_revocation == "unknown" and done.has_credential, "unknown is not done: still kept aside"


def test_a_revocation_outcome_never_lands_on_a_newer_connection(tmp_path) -> None:
    db = store(tmp_path)
    ana = db.commit_authorization(candidate())
    record, _ = db.disconnect(ana.id, revoke_remote=True)
    db.record_revocation(ana.id, revocation_generation=record.revocation_generation, outcome="confirmed")
    back = db.commit_authorization(candidate(blob=b"DE-NUEVO"))  # the same identity, added again
    assert back.id == ana.id and back.state == "connected" and back.enabled_services == ()
    late = db.record_revocation(ana.id, revocation_generation=record.revocation_generation, outcome="confirmed")
    assert late.remote_revocation == "not_requested" and db.credential(ana.id).credential == b"DE-NUEVO"


def test_a_grant_the_store_must_not_hold_is_refused_there_too(tmp_path) -> None:
    db = store(tmp_path)
    for bad in (
        candidate(granted=(*requested_scopes(BOTH), "https://www.googleapis.com/auth/gmail.modify")),
        candidate(granted=("openid", "email")),
        candidate(granted=("email", *SERVICE_SCOPES["gmail.read"])),
    ):
        with pytest.raises(GoogleStoreError):
            db.commit_authorization(bad)
    assert db.accounts() == []


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_a_store_from_a_newer_iris_is_refused_and_left_untouched(tmp_path) -> None:
    db = store(tmp_path)
    db.commit_authorization(candidate())
    path = db.path
    with sqlite3.connect(path) as conn:  # a newer Iris wrote it
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    conn.close()
    for sidecar in ("-wal", "-shm"):
        Path(str(path) + sidecar).unlink(missing_ok=True)
    before = _sha(path)
    with pytest.raises(GoogleStoreVersionError):
        GoogleStore.for_profile(tmp_path / "perfil")
    assert _sha(path) == before


@pytest.mark.skipif(os.name != "nt", reason="junctions are a Windows directory link")
def test_a_linked_folder_is_refused_and_its_target_never_written(tmp_path) -> None:
    home = tmp_path / "perfil"
    (home / "connections").mkdir(parents=True)
    outside = tmp_path / "fuera"
    outside.mkdir()
    subprocess.run(["cmd", "/c", "mklink", "/J", str(home / "connections" / "google"), str(outside)],
                   check=True, capture_output=True)
    with pytest.raises((GoogleStorePathError, GoogleStoreError)):
        GoogleStore.for_profile(home)
    assert list(outside.iterdir()) == []


def test_a_hardlinked_database_is_refused(tmp_path) -> None:
    db = store(tmp_path)
    db.commit_authorization(candidate())
    outside = tmp_path / "copia.sqlite3"
    os.link(db.path, outside)
    with pytest.raises(GoogleStorePathError):
        db.accounts()
    with pytest.raises(GoogleStorePathError):
        GoogleStore.for_profile(tmp_path / "perfil")


def test_a_store_from_before_reading_existed_is_migrated_and_keeps_its_accounts(tmp_path) -> None:
    db = store(tmp_path)
    ana = db.commit_authorization(candidate())
    with sqlite3.connect(db.path) as conn:  # what F10.1 wrote: schema 1, no reading tables
        conn.execute("DROP TABLE collections")
        conn.execute("DROP TABLE items")
        conn.execute("PRAGMA user_version = 1")
    conn.close()
    for sidecar in ("-wal", "-shm"):
        Path(str(db.path) + sidecar).unlink(missing_ok=True)
    again = GoogleStore.for_profile(tmp_path / "perfil")
    assert again.account(ana.id).id == ana.id and again.credential(ana.id).credential == b"BLOB-A"
    assert again.collections(ana.id) == []
    assert again.history()[0]["kind"] == "store_migrated"


def test_a_round_from_an_older_generation_lands_nowhere(tmp_path) -> None:
    db = store(tmp_path)
    ana = db.commit_authorization(candidate())
    db.commit_round(ana.id, generation=ana.generation, service="gmail.read", collection="mailbox",
                    upserts={"m1": {"id": "m1"}}, deletes=(), checkpoint="100", coverage={}, replace=True)
    db.disconnect(ana.id)
    back = db.commit_authorization(candidate(blob=b"OTRA"))
    with pytest.raises(StaleGeneration):  # a round that started before the disconnection
        db.commit_round(ana.id, generation=ana.generation, service="gmail.read", collection="mailbox",
                        upserts={"m2": {"id": "m2"}}, deletes=("m1",), checkpoint="200", coverage={}, replace=False)
    with pytest.raises(StaleGeneration):
        db.record_failure(ana.id, generation=ana.generation, service="gmail.read", collection="mailbox",
                          code="offline", next_attempt_at=None)
    assert [item for item, _ in db.items(back.id, "gmail.read", "mailbox")] == ["m1"]
    assert db.collection(back.id, "gmail.read", "mailbox").checkpoint == "100"
    record, _ = db.disconnect(back.id, clear_cache=True)
    assert db.items(back.id, "gmail.read", "mailbox") == [] and db.collections(back.id) == []
