"""F10 review regressions: issuer aliases and quarantined revocations, synthetic data only."""

from dataclasses import replace

import pytest

from core.google_contracts import (
    CANONICAL_GOOGLE_ISSUER,
    ContractError,
    normalize_issuer,
    requested_scopes,
)
from core.google_store import (
    AccountChanged,
    AccountExists,
    AccountIdentityConflict,
    AuthorizationCandidate,
    GoogleStore,
    GoogleStoreError,
    RevocationPending,
)


ISSUERS = ("https://accounts.google.com", "accounts.google.com")


def candidate(*, issuer=ISSUERS[0], subject="review-subject", client="review-client",
              blob=b"SYNTHETIC-A"):
    scopes = requested_scopes(("calendar.read",))
    return AuthorizationCandidate(client_id=client, issuer=issuer, subject=subject,
                                  email="synthetic@example.invalid", requested_scopes=scopes,
                                  granted_scopes=scopes, credential=blob)


@pytest.fixture
def db(tmp_path):
    home = tmp_path / "synthetic-profile"
    home.mkdir()
    return GoogleStore.for_profile(home)


def contents(db):
    """Logical database contents, including private blobs, for atomic refusal assertions."""
    with db._read() as conn:
        return tuple((table, conn.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall())
                     for table in ("accounts", "history", "collections", "items", "meta"))


def legacy_issuer(db, account_id, issuer):
    # An earlier Iris persisted the accepted spelling verbatim. The production opener
    # and transaction still guard this synthetic fixture's database and sidecars.
    with db._transaction() as conn:
        conn.execute("UPDATE accounts SET issuer = ? WHERE id = ?", (issuer, account_id))


@pytest.mark.parametrize("issuer", ISSUERS)
def test_both_documented_issuers_have_one_canonical_identity(issuer):
    assert normalize_issuer(issuer) == CANONICAL_GOOGLE_ISSUER


@pytest.mark.parametrize("issuer", (None, "", "http://accounts.google.com",
                                    "https://accounts.google.com/", "ACCOUNTS.GOOGLE.COM",
                                    "https://accounts.google.com.evil.invalid"))
def test_issuer_canonicalization_never_broadens_the_accepted_issuers(issuer):
    with pytest.raises(ContractError) as caught:
        normalize_issuer(issuer)
    assert caught.value.code == "identity_unverified"


@pytest.mark.parametrize("issuer", ISSUERS)
def test_new_accounts_persist_only_the_canonical_issuer(db, issuer):
    account = db.commit_authorization(candidate(issuer=issuer))
    assert account.issuer == CANONICAL_GOOGLE_ISSUER
    with db._read() as conn:
        assert conn.execute("SELECT issuer FROM accounts WHERE id = ?", (account.id,)).fetchone() == (
            CANONICAL_GOOGLE_ISSUER,)


@pytest.mark.parametrize("stored_issuer", ISSUERS)
@pytest.mark.parametrize("response_issuer", ISSUERS)
def test_reconnect_matches_legacy_aliases_and_preserves_account_ownership(db, stored_issuer, response_issuer):
    initial = db.commit_authorization(candidate())
    db.set_enabled_services(initial.id, ("calendar.read",), expected_revision=initial.revision)
    db.commit_round(initial.id, generation=initial.generation, service="calendar.read", collection="c1",
                    upserts={"e1": {"id": "e1"}}, deletes=(), checkpoint="checkpoint-A",
                    coverage={}, replace=True)
    legacy_issuer(db, initial.id, stored_issuer)
    before = db.account(initial.id)

    result = db.commit_authorization(candidate(issuer=response_issuer, blob=b"SYNTHETIC-B"),
                                     account_id=initial.id, expected_generation=before.generation)

    assert result.id == initial.id and result.issuer == CANONICAL_GOOGLE_ISSUER
    assert result.generation == before.generation + 1
    assert result.credential_revision == before.credential_revision + 1
    assert result.enabled_services == ("calendar.read",)
    assert db.credential(initial.id).credential == b"SYNTHETIC-B"
    assert db.items(initial.id, "calendar.read", "c1") == [("e1", {"id": "e1"})]
    assert len(db.accounts()) == 1


@pytest.mark.parametrize("stored_issuer", ISSUERS)
@pytest.mark.parametrize("response_issuer", ISSUERS)
def test_adding_an_already_connected_alias_cannot_create_another_account(db, stored_issuer, response_issuer):
    initial = db.commit_authorization(candidate())
    legacy_issuer(db, initial.id, stored_issuer)
    before = contents(db)
    with pytest.raises(AccountExists) as caught:
        db.commit_authorization(candidate(issuer=response_issuer, blob=b"MUST-NOT-REPLACE"))
    assert caught.value.account_id == initial.id
    assert contents(db) == before


def test_disconnected_legacy_alias_comes_back_under_its_original_id(db):
    initial = db.commit_authorization(candidate())
    legacy_issuer(db, initial.id, ISSUERS[1])
    db.disconnect(initial.id)
    result = db.commit_authorization(candidate(issuer=ISSUERS[0], blob=b"SYNTHETIC-B"))
    assert result.id == initial.id and result.issuer == CANONICAL_GOOGLE_ISSUER
    assert result.state == "connected" and result.enabled_services == ()
    assert db.credential(initial.id).credential == b"SYNTHETIC-B"


@pytest.mark.parametrize("change", ({"subject": "other-subject"}, {"client_id": "other-client"}))
def test_aliases_never_make_another_subject_or_client_the_same_identity(db, change):
    initial = db.commit_authorization(candidate())
    changed = replace(candidate(issuer=ISSUERS[1]), **change)
    before = contents(db)
    with pytest.raises(AccountChanged):
        db.commit_authorization(changed, account_id=initial.id, expected_generation=initial.generation)
    assert contents(db) == before
    added = db.commit_authorization(changed)
    assert added.id != initial.id and len(db.accounts()) == 2


@pytest.mark.parametrize("explicit_target", (None, "first", "second"))
def test_legacy_duplicate_aliases_are_reported_without_choosing_or_merging(db, explicit_target):
    first = db.commit_authorization(candidate(blob=b"FIRST-PRIVATE-BLOB"))
    second = db.commit_authorization(candidate(subject="temporary-other", blob=b"SECOND-PRIVATE-BLOB"))
    with db._transaction() as conn:
        conn.execute("UPDATE accounts SET issuer = ?, subject = ? WHERE id = ?",
                     (ISSUERS[1], first.subject, second.id))
    before = contents(db)
    target = {"first": first, "second": second}.get(explicit_target)

    with pytest.raises(AccountIdentityConflict) as caught:
        db.commit_authorization(candidate(blob=b"MUST-NOT-REPLACE"),
                                 account_id=target.id if target else None,
                                 expected_generation=target.generation if target else None)
    assert caught.value.code == "identity_conflict"
    assert contents(db) == before
    assert db.credential(first.id).credential == b"FIRST-PRIVATE-BLOB"
    assert db.credential(second.id).credential == b"SECOND-PRIVATE-BLOB"

    # Both original ids remain manageable. Explicitly removing the unwanted row
    # resolves the ambiguity; no automatic merge or credential choice is involved.
    db.disconnect(second.id)
    db.forget(second.id)
    result = db.commit_authorization(candidate(issuer=ISSUERS[1]),
                                     account_id=first.id, expected_generation=first.generation)
    assert result.id == first.id and len(db.accounts()) == 1


@pytest.mark.parametrize("issuer", ISSUERS)
def test_store_refuses_unverified_issuers_without_touching_an_existing_account(db, issuer):
    first = db.commit_authorization(candidate(issuer=issuer))
    before = contents(db)
    with pytest.raises(GoogleStoreError):
        db.commit_authorization(candidate(issuer="https://google.example.invalid"),
                                 account_id=first.id, expected_generation=first.generation)
    assert contents(db) == before


@pytest.mark.parametrize("explicit_target", (False, True))
@pytest.mark.parametrize("issuer", ISSUERS)
def test_reconnecting_never_overwrites_a_credential_pending_remote_revocation(db, explicit_target, issuer):
    first = db.commit_authorization(candidate())
    pending, blob = db.disconnect(first.id, revoke_remote=True)
    assert blob == b"SYNTHETIC-A"
    before = contents(db)
    with pytest.raises(RevocationPending) as caught:
        db.commit_authorization(candidate(issuer=issuer, blob=b"MUST-NOT-REPLACE"),
                                 account_id=first.id if explicit_target else None,
                                 expected_generation=pending.generation if explicit_target else None)
    assert caught.value.code == "revocation_pending"
    assert contents(db) == before and db.credential(first.id) is None
    confirmed = db.record_revocation(first.id, revocation_generation=pending.revocation_generation,
                                     outcome="confirmed")
    result = db.commit_authorization(candidate(issuer=issuer, blob=b"SYNTHETIC-B"),
                                     account_id=first.id if explicit_target else None,
                                     expected_generation=confirmed.generation if explicit_target else None)
    assert result.id == first.id and db.credential(first.id).credential == b"SYNTHETIC-B"
