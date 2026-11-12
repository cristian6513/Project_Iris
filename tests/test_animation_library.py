"""A08.2: effective catalogs, immutable URLs and read-only factory roots."""

import hashlib
import io
import json
import os
import subprocess
import sys

import pytest
from asset_upload_fixtures import BVH, animation

from core import animations
from core.animation_library import AnimationLibrary
from core.animation_snapshot import animation_snapshot
from core.animation_store import AnimationStore, AnimationStoreError


def register(root, name, payload, extension="vrma", *, managed=True):
    root.mkdir(parents=True, exist_ok=True)
    filename = (
        f"{name}.{hashlib.sha256(payload).hexdigest()}.{extension}"
        if managed
        else f"{name}.{extension}"
    )
    (root / filename).write_bytes(payload)
    return filename


def manifest(root, clips, **metadata):
    root.mkdir(parents=True, exist_ok=True)
    (root / "manifest.json").write_text(json.dumps({"clips": clips, **metadata}), encoding="utf-8")


@pytest.fixture
def library(tmp_path, monkeypatch):
    monkeypatch.setattr("core.profile_paths._legacy_profile_writers", lambda *args, **kw: None)
    factory, user = tmp_path / "factory", tmp_path / "profile" / "animations"
    filename = register(factory, "wave", animation("factory"), managed=False)
    manifest(
        factory,
        {"wave": filename},
        pose_map={"greet": "wave"},
        body_only_clips=["wave"],
        facial_profiles={"wave": {"emotion": "feliz"}},
        menu_poses=["wave"],
    )
    return AnimationLibrary(factory, user)


def test_user_precedence_maps_and_explicit_empty_lists(library):
    replacement = register(library.user, "wave", BVH, "bvh")
    custom = register(library.user, "dance", animation("custom"))
    manifest(
        library.user,
        {"wave": replacement, "dance": custom},
        revision=4,
        pose_map={"new": "dance"},
        body_only_clips=[],
        facial_profiles={"wave": {"emotion": "triste"}},
        future_metadata={"keep": True},
    )
    data = library.snapshot()
    assert data["manifest"]["clips"] == {"wave": replacement, "dance": custom}
    assert data["manifest"]["pose_map"] == {"greet": "wave", "new": "dance"}
    assert data["manifest"]["body_only_clips"] == []
    assert data["manifest"]["facial_profiles"]["wave"] == {"emotion": "triste"}
    assert data["manifest"]["future_metadata"] == {"keep": True}
    assert data["resources"]["wave"]["source"] == "user"
    assert data == animation_snapshot(library.factory, library.user)
    assert str(library.user) not in json.dumps(data)


def test_revisions_follow_both_manifests_and_old_urls_survive(library):
    first = library.snapshot()
    old_factory = first["resources"]["wave"]["filename"]
    a = AnimationStore(library.user).save_upload(io.BytesIO(animation("a")), "a.vrma", "wave")
    second = library.snapshot()
    assert second["revision"] != first["revision"]
    assert library.resolve_file(old_factory).read_bytes() == animation("factory")
    b = AnimationStore(library.user).save_upload(io.BytesIO(BVH), "b.bvh", "wave")
    third = library.snapshot()
    assert third["revision"] != second["revision"]
    assert third["resources"]["wave"]["format"] == "bvh"
    assert library.resolve_file(a.filename).read_bytes() == animation("a")
    assert library.resolve_file(b.filename).read_bytes() == BVH
    orphan = register(library.user, "think", animation("orphan"))
    assert library.resolve_file(orphan).read_bytes() == animation("orphan")
    assert "think" not in library.merged_manifest()["clips"]


@pytest.mark.parametrize(
    "filename",
    ["../out.vrma", r"..\out.vrma", r"C:\out.vrma", "/out.vrma", "a.vrma:stream", "CON.vrma"],
)
def test_resolver_rejects_path_syntax(library, filename):
    with pytest.raises(AnimationStoreError):
        library.resolve_file(filename)


@pytest.mark.parametrize("failure", ["fixed-name", "digest", "missing", "json"])
def test_invalid_override_does_not_advertise_factory_instead(library, monkeypatch, failure):
    valid = register(library.user, "dance", animation("custom"))
    manifest(library.user, {"dance": valid})
    monkeypatch.setattr("core.animation_library.get_animation_library", lambda: library)
    monkeypatch.setattr(animations, "_cache_source", None)
    before = animations.get_available_animations()
    assert {entry["name"] for entry in before} == {"wave", "dance"}
    if failure == "fixed-name":
        bad = register(library.user, "wave", animation("broken-policy"), managed=False)
        manifest(library.user, {"wave": bad})
    elif failure == "digest":
        (library.user / valid).write_bytes(animation("changed"))
    elif failure == "missing":
        (library.user / valid).unlink()
    else:
        (library.user / "manifest.json").write_bytes(b"invalid")
    with pytest.raises(AnimationStoreError):
        library.snapshot()
    assert animations.get_available_animations() == before


def test_catalog_cache_isolated_by_profile(library, tmp_path, monkeypatch):
    register(library.user, "dance", animation("custom"))
    filename = next(library.user.glob("dance.*.vrma")).name
    manifest(library.user, {"dance": filename})
    monkeypatch.setattr("core.animation_library.get_animation_library", lambda: library)
    monkeypatch.setattr(animations, "_cache_source", None)
    assert "dance" in {entry["name"] for entry in animations.get_available_animations()}
    other = AnimationLibrary(library.factory, tmp_path / "other-profile" / "animations")
    monkeypatch.setattr("core.animation_library.get_animation_library", lambda: other)
    assert {entry["name"] for entry in animations.get_available_animations()} == {"wave"}


def test_invalid_upload_has_no_migration_side_effect(library):
    with pytest.raises(AnimationStoreError):
        library.save_upload(io.BytesIO(b"invalid"), "broken.vrma", "wave")
    assert not library.user.exists()
    assert not (library.user.parent / "migrations").exists()


def test_valid_upload_preserves_factory_and_idempotent_retry(library, monkeypatch):
    original = {p.name: p.read_bytes() for p in library.factory.iterdir()}
    monkeypatch.setattr(
        "core.profile_migration._factory_contract",
        lambda: {
            "wave": {
                "filename": "wave.vrma",
                "sha256": hashlib.sha256(animation("factory")).hexdigest(),
            }
        },
    )
    saved = library.save_upload(io.BytesIO(animation("new")), "new.vrma", "wave")
    assert saved.revision == 1 and saved.changed
    retry = library.save_upload(io.BytesIO(animation("new")), "again.vrma", "wave")
    assert retry.revision == saved.revision and not retry.changed
    assert {p.name: p.read_bytes() for p in library.factory.iterdir()} == original


def test_hard_link_cannot_redirect_an_overlay_read(library, tmp_path):
    filename = register(library.user, "wave", animation("custom"))
    manifest(library.user, {"wave": filename})
    outside = tmp_path / "outside.vrma"
    outside.write_bytes(animation("custom"))
    (library.user / filename).unlink()
    os.link(outside, library.user / filename)
    with pytest.raises(AnimationStoreError):
        library.snapshot()
    with pytest.raises(AnimationStoreError):
        library.resolve_file(filename)


def test_cross_root_managed_collision_is_rejected_before_snapshot_emission(library):
    filename = register(library.user, "wave", animation("personal"))
    manifest(library.user, {"wave": filename})
    (library.factory / filename).write_bytes(animation("different"))
    with pytest.raises(AnimationStoreError):
        library.snapshot()
    with pytest.raises(AnimationStoreError):
        library.resolve_file(filename)


def test_upload_cannot_reset_a_lost_initialized_manifest(library):
    library.save_upload(io.BytesIO(animation("new")), "new.vrma", "wave")
    library.save_upload(io.BytesIO(animation("dance")), "dance.vrma", "dance")
    (library.user / "manifest.json").unlink()
    before = {p.name: p.read_bytes() for p in library.user.iterdir()}
    with pytest.raises(AnimationStoreError) as error:
        library.save_upload(io.BytesIO(animation("replacement")), "new.vrma", "wave")
    assert error.value.status_code == 409
    assert {p.name: p.read_bytes() for p in library.user.iterdir()} == before


def test_separate_writer_is_visible_without_cache_event(library):
    first = library.snapshot()
    result = subprocess.run(
        [
            sys.executable,
            "-E",
            "-s",
            "-B",
            "-X",
            "utf8",
            "-c",
            "import io,sys;from pathlib import Path;"
            "from core import profile_paths;"
            "profile_paths._legacy_profile_writers=lambda *args,**kwargs:None;"
            "from core.animation_library import AnimationLibrary;"
            "AnimationLibrary(Path(sys.argv[1]),Path(sys.argv[2])).save_upload("
            "io.BytesIO(bytes.fromhex(sys.argv[3])), 'new.vrma','wave')",
            str(library.factory),
            str(library.user),
            animation("separate").hex(),
        ],
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    changed = library.snapshot()
    assert changed["revision"] != first["revision"]
    assert (
        changed["resources"]["wave"]["revision"]
        == hashlib.sha256(animation("separate")).hexdigest()
    )


def test_incompatible_writer_blocks_upload_before_migration(library):
    from core.profile_paths import ProfilePaths, profile_writer

    with profile_writer("other-version", paths=ProfilePaths(library.user.parent), format_version=2):
        with pytest.raises(AnimationStoreError) as error:
            library.save_upload(io.BytesIO(animation("new")), "new.vrma", "wave")
        assert error.value.status_code == 409
        assert not library.user.exists()
        assert not (library.user.parent / "migrations").exists()


@pytest.mark.parametrize("prior_override", [False, True])
def test_restored_missing_override_is_an_outage_but_prior_absence_is_valid(
    library, monkeypatch, prior_override
):
    from core.profile_migration import migrate_animation_profile, restore_animation_profile

    if prior_override:
        custom = register(library.user, "dance", animation("custom"))
        manifest(library.user, {"dance": custom})
    migrate_animation_profile(library.factory, library.user)
    restore_animation_profile(library.user)
    monkeypatch.setattr("core.animation_library.get_animation_library", lambda: library)
    monkeypatch.setattr(animations, "_cache_source", None)
    confirmed = animations.get_available_animations()
    assert ("dance" in {entry["name"] for entry in confirmed}) == prior_override
    if prior_override:
        (library.user / "manifest.json").unlink()
        with pytest.raises(AnimationStoreError, match="Falta"):
            library.snapshot()
    else:
        assert not (library.user / "manifest.json").exists()
        assert library.snapshot()["resources"]["wave"]["source"] == "factory"
    assert animations.get_available_animations() == confirmed


@pytest.mark.parametrize("operation", ["upload", "migration"])
def test_factory_alias_into_profile_is_rejected_before_any_profile_write(
    library, tmp_path, operation
):
    from core.profile_migration import migrate_animation_profile

    custom = register(library.user, "wave", animation("old"))
    manifest(library.user, {"wave": custom})
    alias = tmp_path / "factory-alias"
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(library.user), str(alias))
    else:
        alias.symlink_to(library.user, target_is_directory=True)
    before = {p.name: p.read_bytes() for p in library.user.iterdir()}
    try:
        with pytest.raises(AnimationStoreError):
            if operation == "upload":
                AnimationLibrary(alias, library.user).save_upload(
                    io.BytesIO(animation("new")),
                    "new.vrma",
                    "wave",
                )
            else:
                migrate_animation_profile(alias, library.user)
        assert {p.name: p.read_bytes() for p in library.user.iterdir()} == before
        assert not (library.user.parent / ".profile").exists()
        assert not (library.user.parent / "migrations").exists()
    finally:
        if os.name == "nt":
            alias.rmdir()  # Remove only the junction, never its target.
        else:
            alias.unlink()


@pytest.mark.parametrize("nested", ["user-in-factory", "factory-in-user"])
def test_overlapping_library_roots_never_create_locks(tmp_path, nested):
    root = tmp_path / "root"
    factory, user = (
        (root, root / "animations") if nested == "user-in-factory" else (root / "factory", root)
    )
    with pytest.raises(AnimationStoreError, match="separados"):
        AnimationLibrary(factory, user).save_upload(
            io.BytesIO(animation("new")), "new.vrma", "wave"
        )
    assert not root.exists()


def test_a_replaced_gesture_plays_only_the_persons_clip_without_the_factory_timing(tmp_path, monkeypatch):
    """QA23: a factory gesture with variants or timed facial moments, replaced from the wizard."""
    monkeypatch.setattr("core.profile_paths._legacy_profile_writers", lambda *args, **kw: None)
    factory, user = tmp_path / "factory", tmp_path / "profile" / "animations"
    clips = {name: register(factory, name, animation(name), managed=False)
             for name in ("wave", "wave_2", "idle_yawn", "dance", "dance_3")}
    manifest(
        factory, clips,
        variants={"wave": ["wave", "wave_2"], "dance": ["dance", "dance_3"]},
        variant_weights={"wave_2": 0.5},
        facial_profiles={
            "idle_yawn": {"emotion": "neutral", "moments": [{"from": 0.4, "to": 1.6, "emotion": "sorpresa"}]},
            "wave": {"emotion": "feliz"},
        },
    )
    library = AnimationLibrary(factory, user)
    own = {name: register(user, name, animation("mine-" + name)) for name in ("wave", "idle_yawn", "dance")}
    manifest(user, own, revision=1, variants={"dance": ["dance", "dance_3"]})
    merged = library.snapshot()["manifest"]
    assert "wave" not in merged["variants"], "the factory's other wave no longer plays in its place"
    assert merged["variants"]["dance"] == ["dance", "dance_3"], "variants the person set stay"
    assert merged["facial_profiles"]["idle_yawn"] == {"emotion": "neutral"}, "no timed moments on another clip"
    assert merged["facial_profiles"]["wave"] == {"emotion": "feliz"}, "the rest of the profile stays"
    assert merged["clips"]["wave_2"] == clips["wave_2"]
    # Nothing replaced: the factory metadata is untouched.
    manifest(user, {}, revision=2)
    untouched = library.snapshot()["manifest"]
    assert untouched["variants"]["wave"] == ["wave", "wave_2"]
    assert untouched["facial_profiles"]["idle_yawn"]["moments"]
