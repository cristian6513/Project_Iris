"""Animation-only backup, durable resume and exact prior-overlay restoration."""

import builtins
import hashlib
import io
import json
from pathlib import Path

import pytest
from asset_upload_fixtures import animation

from core import profile_migration as migration
from core.animation_library import AnimationLibrary
from core.animation_store import AnimationStoreError


@pytest.fixture(autouse=True)
def isolated_profile_scanner(monkeypatch):
    monkeypatch.setattr("core.profile_paths._legacy_profile_writers", lambda *args, **kwargs: None)


def put(root, name, payload, *, managed=False):
    root.mkdir(parents=True, exist_ok=True)
    filename = f"{name}.{hashlib.sha256(payload).hexdigest()}.vrma" if managed else f"{name}.vrma"
    (root / filename).write_bytes(payload)
    return filename


def write_manifest(root, clips, **metadata):
    root.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps({"clips": clips, **metadata}, indent=3, ensure_ascii=False) + "\n").encode()
    (root / "manifest.json").write_bytes(raw)
    return raw


@pytest.fixture
def roots(tmp_path, monkeypatch):
    factory, user = tmp_path / "factory", tmp_path / "profile" / "animations"
    filename = put(factory, "iris_wave", animation("personal"))
    write_manifest(
        factory,
        {"wave": filename},
        pose_map={"greet": "wave"},
        facial_profiles={"wave": {"emotion": "feliz"}},
        future_metadata=["á"],
    )
    monkeypatch.setattr(
        migration,
        "_factory_contract",
        lambda: {
            "wave": {
                "filename": filename,
                "sha256": hashlib.sha256(animation("approved")).hexdigest(),
            }
        },
    )
    return factory, user


def test_customized_fixed_name_migrates_and_factory_urls_keep_their_bytes(roots):
    factory, user = roots
    original = {p.name: p.read_bytes() for p in factory.iterdir()}
    state = migration.migrate_animation_profile(factory, user)
    assert state["version"] == 1 and state["phase"] == "complete"
    assert state["classifications"] == {"wave": "preserved-legacy"}
    library = AnimationLibrary(factory, user)
    filename = library.merged_manifest()["clips"]["wave"]
    assert filename == f"wave.{hashlib.sha256(animation('personal')).hexdigest()}.vrma"
    assert library.resolve_file(filename).read_bytes() == animation("personal")
    assert {p.name: p.read_bytes() for p in factory.iterdir()} == original
    # An installation upgrade cannot erase a migrated override.
    (factory / "iris_wave.vrma").write_bytes(animation("approved"))
    assert library.snapshot()["resources"]["wave"]["source"] == "user"
    assert library.resolve_file("iris_wave.vrma").read_bytes() == animation("approved")
    assert library.resolve_file(filename).read_bytes() == animation("personal")


def test_exact_approved_factory_is_not_misclassified_as_user(roots):
    factory, user = roots
    (factory / "iris_wave.vrma").write_bytes(animation("approved"))
    migration.migrate_animation_profile(factory, user)
    assert json.loads((user / "manifest.json").read_bytes())["clips"] == {}
    assert AnimationLibrary(factory, user).snapshot()["resources"]["wave"]["source"] == "factory"
    # Reopening against a future factory revision must not pin the initial bytes.
    (factory / "iris_wave.vrma").write_bytes(animation("future-official"))
    migration.migrate_animation_profile(factory, user)
    snapshot = AnimationLibrary(factory, user).snapshot()
    assert snapshot["resources"]["wave"]["source"] == "factory"
    assert (
        snapshot["resources"]["wave"]["revision"]
        == hashlib.sha256(animation("future-official")).hexdigest()
    )
    assert json.loads((user / "manifest.json").read_bytes())["clips"] == {}


def test_runtime_inventory_matches_the_approved_build_inventory():
    approved = json.loads(
        (Path(__file__).resolve().parents[1] / "scripts" / "release_assets.json").read_text("utf-8")
    )
    assert migration._factory_contract() == approved["clips"]


def test_runtime_inventory_has_no_build_script_filesystem_dependency(monkeypatch):
    original = Path.read_bytes

    def deny_build_script(path):
        if path.name == "release_assets.json":
            raise AssertionError("Runtime must not depend on source build scripts")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", deny_build_script)
    inventory = migration._factory_contract()
    assert len(inventory) == 95  # 59 until the Tripo gestures of 2026-11-09, 92 until face down on the bed
    inventory["wave"]["filename"] = "changed.vrma"
    assert migration._factory_contract()["wave"]["filename"] == "iris_wave.vrma"


def test_missing_runtime_inventory_blocks_migration_before_manifest_commit(tmp_path, monkeypatch):
    factory, user = tmp_path / "factory", tmp_path / "profile" / "animations"
    filename = put(factory, "wave", animation("factory"))
    write_manifest(factory, {"wave": filename})
    previous = put(user, "dance", animation("previous"), managed=True)
    before = write_manifest(user, {"dance": previous})
    original = builtins.__import__

    def missing(name, *args, **kwargs):
        if name == "core.factory_animation_inventory":
            raise ModuleNotFoundError("inventory absent")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing)
    with pytest.raises(AnimationStoreError) as error:
        migration.migrate_animation_profile(factory, user)
    assert error.value.status_code == 409
    assert (user / "manifest.json").read_bytes() == before


def test_repeat_is_idempotent_and_historical_orphan_urls_survive(roots):
    factory, user = roots
    orphan = put(factory, "wave", animation("older"), managed=True)
    first = migration.migrate_animation_profile(factory, user)
    before = {p.name: p.read_bytes() for p in user.iterdir() if p.is_file()}
    assert migration.migrate_animation_profile(factory, user) == first
    assert {p.name: p.read_bytes() for p in user.iterdir() if p.is_file()} == before
    library = AnimationLibrary(factory, user)
    assert library.resolve_file(orphan).read_bytes() == animation("older")
    assert orphan not in library.merged_manifest()["clips"].values()


def test_existing_user_override_wins_and_restore_is_exact(roots):
    factory, user = roots
    previous = put(user, "wave", animation("newer-user"), managed=True)
    before = write_manifest(
        user,
        {"wave": previous},
        revision=7,
        pose_map={"custom": "wave"},
        body_only_clips=[],
        facial_profiles={"wave": {"emotion": "triste"}},
        future_metadata={"user": True},
    )
    state = migration.migrate_animation_profile(factory, user)
    effective = AnimationLibrary(factory, user).merged_manifest()
    assert effective["clips"]["wave"] == previous and effective["revision"] == 7
    assert effective["facial_profiles"]["wave"] == {"emotion": "triste"}
    assert effective["body_only_clips"] == []
    assert effective["future_metadata"] == {"user": True}
    newer_files = set(p.name for p in user.glob("*.vrma"))
    assert state["classifications"]["wave"] == "existing-user"
    assert migration.restore_animation_profile(user)["phase"] == "restored"
    assert (user / "manifest.json").read_bytes() == before
    assert not (user.parent / ".profile" / "format.json").exists()
    assert set(p.name for p in user.glob("*.vrma")) == newer_files
    assert migration.migrate_animation_profile(factory, user)["phase"] == "restored"
    assert (user / "manifest.json").read_bytes() == before


def test_profile_format_backup_and_restore_preserves_exact_bytes(roots):
    factory, user = roots
    profile_state = user.parent / ".profile"
    profile_state.mkdir(parents=True)
    raw = b'{ "format_version": 1, "schema_version": 1 }\r\n'
    (profile_state / "format.json").write_bytes(raw)
    state = migration.migrate_animation_profile(factory, user)
    assert state["profile_format_hash"] == hashlib.sha256(raw).hexdigest()
    assert migration.restore_animation_profile(user)["phase"] == "restored"
    assert (profile_state / "format.json").read_bytes() == raw


@pytest.mark.parametrize("phase", ["prepared", "published", "complete"])
def test_interrupted_journal_resumes_before_or_after_manifest_commit(roots, monkeypatch, phase):
    factory, user = roots
    original = (factory / "manifest.json").read_bytes()
    real = migration._journal
    failed = False

    def interrupt(root, state):
        nonlocal failed
        if state["phase"] == phase and not failed:
            failed = True
            raise OSError("interrupted")
        return real(root, state)

    monkeypatch.setattr(migration, "_journal", interrupt)
    with pytest.raises(AnimationStoreError) as error:
        migration.migrate_animation_profile(factory, user)
    assert error.value.status_code == 503 and failed
    assert (factory / "manifest.json").read_bytes() == original
    state = migration.migrate_animation_profile(factory, user)
    assert state["phase"] == "complete"
    assert len(list(user.glob("wave.*.vrma"))) == 1
    assert AnimationLibrary(factory, user).resolve_file(
        next(user.glob("wave.*.vrma")).name
    ).read_bytes() == animation("personal")


def test_interruption_during_asset_publication_keeps_prior_manifest(roots, monkeypatch):
    factory, user = roots
    previous = put(user, "dance", animation("existing"), managed=True)
    before = write_manifest(user, {"dance": previous})
    real = migration._publish_file

    def interrupt(root, filename, payload):
        real(root, filename, payload)
        raise OSError("after immutable publication")

    monkeypatch.setattr(migration, "_publish_file", interrupt)
    with pytest.raises(AnimationStoreError):
        migration.migrate_animation_profile(factory, user)
    assert (user / "manifest.json").read_bytes() == before
    monkeypatch.setattr(migration, "_publish_file", real)
    assert migration.migrate_animation_profile(factory, user)["phase"] == "complete"


def test_concurrent_legacy_change_is_detected_before_visibility(roots, monkeypatch):
    factory, user = roots
    previous = put(user, "dance", animation("existing"), managed=True)
    before = write_manifest(user, {"dance": previous})
    real = migration._publish_file

    def change_source(root, filename, payload):
        real(root, filename, payload)
        (factory / "iris_wave.vrma").write_bytes(animation("concurrent"))

    monkeypatch.setattr(migration, "_publish_file", change_source)
    with pytest.raises(AnimationStoreError) as error:
        migration.migrate_animation_profile(factory, user)
    assert error.value.status_code == 409
    assert (user / "manifest.json").read_bytes() == before


def test_digest_collision_preserves_overlay_and_source(roots):
    factory, user = roots
    filename = f"wave.{hashlib.sha256(animation('personal')).hexdigest()}.vrma"
    user.mkdir(parents=True)
    (user / filename).write_bytes(animation("wrong"))
    before = write_manifest(user, {})
    source = (factory / "iris_wave.vrma").read_bytes()
    with pytest.raises(AnimationStoreError):
        migration.migrate_animation_profile(factory, user)
    assert (user / "manifest.json").read_bytes() == before
    assert (factory / "iris_wave.vrma").read_bytes() == source


def test_backup_corruption_blocks_restore_without_changing_active_manifest(roots):
    factory, user = roots
    migration.migrate_animation_profile(factory, user)
    before = (user / "manifest.json").read_bytes()
    (user.parent / "migrations" / "animations-v1" / "legacy" / "iris_wave.vrma").write_bytes(
        b"corrupt"
    )
    with pytest.raises(AnimationStoreError):
        migration.restore_animation_profile(user)
    assert (user / "manifest.json").read_bytes() == before


def test_factory_never_receives_staging_lock_or_backup_writes(roots, monkeypatch):
    factory, user = roots
    original = {p.name: p.read_bytes() for p in factory.iterdir()}
    real = migration._write_temp

    def confined(root, content):
        assert root != factory and factory not in root.parents
        return real(root, content)

    monkeypatch.setattr(migration, "_write_temp", confined)
    migration.migrate_animation_profile(factory, user)
    assert {p.name: p.read_bytes() for p in factory.iterdir()} == original
    assert not (factory / ".animation-store.lock").exists()


def test_missing_committed_overlay_is_an_outage_not_factory_fallback(roots):
    factory, user = roots
    migration.migrate_animation_profile(factory, user)
    (user / "manifest.json").unlink()
    with pytest.raises(AnimationStoreError):
        AnimationLibrary(factory, user).snapshot()


@pytest.mark.parametrize(
    "field,value",
    [("version", True), ("phase", []), ("inventory", []), ("profile_format_hash", "invalid")],
)
def test_invalid_journal_cannot_mutate_the_active_overlay(roots, field, value):
    factory, user = roots
    state = migration.migrate_animation_profile(factory, user)
    before = (user / "manifest.json").read_bytes()
    state[field] = value
    journal = user.parent / "migrations" / "animations-v1" / "journal.json"
    journal.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(AnimationStoreError):
        migration.migrate_animation_profile(factory, user)
    assert (user / "manifest.json").read_bytes() == before


@pytest.mark.parametrize("target", ["manifest", "journal"])
def test_deeply_nested_json_is_a_recoverable_validation_error(roots, target):
    factory, user = roots
    migration.migrate_animation_profile(factory, user)
    before = (user / "manifest.json").read_bytes()
    deep = b'{"clips":{},"deep":' + b"[" * 5000 + b"0" + b"]" * 5000 + b"}"
    if target == "manifest":
        path = user / "manifest.json"
        path.write_bytes(deep)
        with pytest.raises(AnimationStoreError) as error:
            AnimationLibrary(factory, user).snapshot()
        assert error.value.status_code == 409 and path.read_bytes() == deep
    else:
        (user.parent / "migrations" / "animations-v1" / "journal.json").write_bytes(deep)
        with pytest.raises(AnimationStoreError) as error:
            migration.migrate_animation_profile(factory, user)
        assert error.value.status_code == 409
        assert (user / "manifest.json").read_bytes() == before


@pytest.mark.parametrize("prior_format", [None, 0, 1])
@pytest.mark.parametrize("boundary", ["manifest", "format", "journal"])
def test_restore_interruption_retries_exact_prior_state_and_keeps_all_urls(
    roots, monkeypatch, prior_format, boundary
):
    factory, user = roots
    profile_state = user.parent / ".profile"
    profile_state.mkdir(parents=True)
    original_format = None
    if prior_format is not None:
        original_format = (
            f'{{ "format_version": {prior_format}, "schema_version": 1 }}\r\n'.encode()
        )
        (profile_state / "format.json").write_bytes(original_format)
    previous = put(user, "dance", animation("previous"), managed=True)
    put(user, "dance", animation("older-user"), managed=True)
    put(factory, "wave", animation("older-source"), managed=True)
    original_manifest = write_manifest(user, {"dance": previous}, revision=4)
    migration.migrate_animation_profile(factory, user)
    library = AnimationLibrary(factory, user)
    library.save_upload(io.BytesIO(animation("latest-wave")), "latest.vrma", "wave")
    history = {path.name: path.read_bytes() for path in user.glob("*.vrma")}
    atomic, unlink, journal = migration._atomic, Path.unlink, migration._journal
    interrupted = False

    def fail_atomic(root, filename, payload):
        nonlocal interrupted
        atomic(root, filename, payload)
        reached = (boundary == "manifest" and root == user and filename == "manifest.json") or (
            boundary == "format" and root == profile_state and filename == "format.json"
        )
        if reached and not interrupted:
            interrupted = True
            raise OSError("interrupted after restoration publication")

    def fail_unlink(path, *args, **kwargs):
        nonlocal interrupted
        result = unlink(path, *args, **kwargs)
        if boundary == "format" and path == profile_state / "format.json" and not interrupted:
            interrupted = True
            raise OSError("interrupted after restoring format absence")
        return result

    def fail_journal(root, state):
        nonlocal interrupted
        if boundary == "journal" and state["phase"] == "restored" and not interrupted:
            interrupted = True
            raise OSError("interrupted before restored journal commit")
        return journal(root, state)

    monkeypatch.setattr(migration, "_atomic", fail_atomic)
    monkeypatch.setattr(Path, "unlink", fail_unlink)
    monkeypatch.setattr(migration, "_journal", fail_journal)
    with pytest.raises(OSError):
        migration.restore_animation_profile(user)
    assert interrupted
    restored = migration.restore_animation_profile(user)
    assert restored["phase"] == "restored"
    assert (user / "manifest.json").read_bytes() == original_manifest
    if original_format is None:
        assert not (profile_state / "format.json").exists()
    else:
        assert (profile_state / "format.json").read_bytes() == original_format
    for filename, payload in history.items():
        assert library.resolve_file(filename).read_bytes() == payload


def test_restore_prepare_rejects_bad_backup_before_recommitting_older_format(roots):
    factory, user = roots
    migration.migrate_animation_profile(factory, user)
    original_format = b'{ "schema_version": 1, "format_version": 0 }\r\n'
    format_path = user.parent / ".profile" / "format.json"
    format_path.write_bytes(original_format)
    manifest_before = (user / "manifest.json").read_bytes()
    (user.parent / "migrations" / "animations-v1" / "legacy" / "iris_wave.vrma").write_bytes(
        b"bad backup"
    )
    with pytest.raises(AnimationStoreError) as error:
        migration.restore_animation_profile(user)
    assert error.value.status_code == 409
    assert format_path.read_bytes() == original_format
    assert (user / "manifest.json").read_bytes() == manifest_before


def test_restore_cannot_overlap_an_incompatible_active_writer(roots):
    from core.profile_paths import ProfileBusyError, ProfilePaths, profile_writer

    factory, user = roots
    migration.migrate_animation_profile(factory, user)
    original = (user / "manifest.json").read_bytes()
    format_path = user.parent / ".profile" / "format.json"
    original_format = format_path.read_bytes()
    with profile_writer(
        "future-version", paths=ProfilePaths(user.parent), format_version=2, prepare=lambda: None
    ):
        # Keep persisted format compatible so the native active lease, rather
        # than the future-format marker alone, must reject restoration.
        format_path.write_bytes(original_format)
        with pytest.raises(ProfileBusyError):
            migration.restore_animation_profile(user)
    assert (user / "manifest.json").read_bytes() == original
    assert format_path.read_bytes() == original_format
