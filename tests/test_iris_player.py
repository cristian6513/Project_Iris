"""Tests for IRIS Player's local-first library core."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.iris_player import (
    InvalidLibraryRootError,
    IrisPlayerLibrary,
    LibraryNotConfiguredError,
    TrackNotFoundError,
    UnsafeTrackPathError,
)


def _audio(path: Path, content: bytes = b"audio") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_library_is_opt_in_and_construction_does_not_scan(tmp_path: Path) -> None:
    music = tmp_path / "music"
    _audio(music / "hidden.mp3")
    player = IrisPlayerLibrary(tmp_path / "state")

    assert player.status()["configured"] is False
    assert player.list_tracks() == []
    assert player.state_path.exists() is False
    with pytest.raises(LibraryNotConfiguredError):
        player.refresh()


def test_configure_does_not_scan_until_explicit_refresh(tmp_path: Path) -> None:
    music = tmp_path / "music"
    _audio(music / "album" / "Neon_Horizon.MP3")
    player = IrisPlayerLibrary(tmp_path / "state")

    configured = player.configure_library(music)

    assert configured["configured"] is True
    assert configured["folder_name"] == "music"
    assert configured["track_count"] == 0
    assert player.list_tracks() == []

    result = player.refresh()
    tracks = player.list_tracks()
    assert result == {
        "added": 1,
        "removed": 0,
        "changed": 0,
        "unchanged": 0,
        "skipped": 0,
        "total": 1,
        "refreshed_at": result["refreshed_at"],
    }
    assert tracks[0]["title"] == "Neon Horizon"
    assert tracks[0]["relative_path"] == "album/Neon_Horizon.MP3"
    assert tracks[0]["artist"] is None
    assert tracks[0]["album"] is None
    assert tracks[0]["duration_seconds"] is None
    json.dumps(tracks)


def test_scan_is_recursive_and_ignores_unsupported_files(tmp_path: Path) -> None:
    music = tmp_path / "music"
    expected = _audio(music / "nested" / "song.flac")
    _audio(music / "cover.jpg")
    _audio(music / "notes.txt")
    player = IrisPlayerLibrary(tmp_path / "state")
    player.configure_library(music)

    assert player.refresh()["total"] == 1
    track = player.list_tracks()[0]
    assert player.resolve_track(track["id"]) == expected.resolve()
    assert str(tmp_path.resolve()) not in track["id"]
    assert not Path(track["relative_path"]).is_absolute()


def test_refresh_reports_added_removed_and_changed_with_stable_ids(tmp_path: Path) -> None:
    music = tmp_path / "music"
    first = _audio(music / "first.mp3", b"one")
    removed = _audio(music / "removed.wav", b"old")
    player = IrisPlayerLibrary(tmp_path / "state")
    player.configure_library(music)
    player.refresh()
    first_id = next(track["id"] for track in player.list_tracks() if track["title"] == "first")

    first.write_bytes(b"one changed and longer")
    removed.unlink()
    _audio(music / "new.ogg", b"new")
    result = player.refresh()

    assert result["added"] == 1
    assert result["removed"] == 1
    assert result["changed"] == 1
    assert result["unchanged"] == 0
    current = next(track for track in player.list_tracks() if track["title"] == "first")
    assert current["id"] == first_id


def test_search_favorites_and_play_history_survive_reload_and_refresh(tmp_path: Path) -> None:
    music = tmp_path / "music"
    _audio(music / "Calma_Nocturna.mp3")
    _audio(music / "Entrenamiento.wav")
    state_root = tmp_path / "state"
    player = IrisPlayerLibrary(state_root)
    player.configure_library(music)
    player.refresh()
    track_id = player.list_tracks(query="calma")[0]["id"]

    player.set_favorite(track_id, True)
    played = player.record_play(track_id)
    player.record_play(track_id)
    assert played["last_played"].endswith("Z")

    reloaded = IrisPlayerLibrary(state_root)
    assert [track["id"] for track in reloaded.list_tracks(favorites_only=True)] == [track_id]
    assert reloaded.list_tracks(query="NOCTURNA")[0]["play_count"] == 2
    reloaded.refresh()
    preserved = reloaded.list_tracks(favorites_only=True)[0]
    assert preserved["favorite"] is True
    assert preserved["play_count"] == 2
    assert preserved["last_played"] is not None


def test_relative_or_missing_library_is_rejected(tmp_path: Path) -> None:
    player = IrisPlayerLibrary(tmp_path / "state")

    with pytest.raises(InvalidLibraryRootError):
        player.configure_library(Path("relative/music"))
    with pytest.raises(InvalidLibraryRootError):
        player.configure_library(tmp_path / "missing")


def test_catalog_escape_is_discarded_when_private_state_is_tampered(tmp_path: Path) -> None:
    music = tmp_path / "music"
    music.mkdir()
    outside = _audio(tmp_path / "outside.mp3")
    player = IrisPlayerLibrary(tmp_path / "state")
    player.configure_library(music)
    state = json.loads(player.state_path.read_text(encoding="utf-8"))
    state["catalog"] = {
        "trk_untrusted": {
            "id": "trk_untrusted",
            "relative_path": f"../{outside.name}",
            "title": "outside",
            "extension": ".mp3",
            "size_bytes": outside.stat().st_size,
            "modified_ns": outside.stat().st_mtime_ns,
        }
    }
    player.state_path.write_text(json.dumps(state), encoding="utf-8")

    assert player.list_tracks() == []
    with pytest.raises(TrackNotFoundError):
        player.resolve_track("trk_untrusted")


def test_malformed_catalog_metadata_is_discarded(tmp_path: Path) -> None:
    music = tmp_path / "music"
    _audio(music / "song.mp3")
    player = IrisPlayerLibrary(tmp_path / "state")
    player.configure_library(music)
    player.refresh()
    state = json.loads(player.state_path.read_text(encoding="utf-8"))
    track_id = next(iter(state["catalog"]))
    state["catalog"][track_id]["size_bytes"] = "not-a-number"
    player.state_path.write_text(json.dumps(state), encoding="utf-8")

    assert player.list_tracks() == []
    with pytest.raises(TrackNotFoundError):
        player.resolve_track(track_id)


def test_resolve_blocks_a_track_replaced_by_a_symlink(tmp_path: Path) -> None:
    music = tmp_path / "music"
    track_path = _audio(music / "song.mp3")
    outside = _audio(tmp_path / "outside.mp3")
    player = IrisPlayerLibrary(tmp_path / "state")
    player.configure_library(music)
    player.refresh()
    track_id = player.list_tracks()[0]["id"]
    track_path.unlink()
    try:
        track_path.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlinks are unavailable in this test environment: {exc}")

    with pytest.raises(UnsafeTrackPathError):
        player.resolve_track(track_id)


def test_refresh_skips_symlinked_directories(tmp_path: Path) -> None:
    music = tmp_path / "music"
    music.mkdir()
    outside = tmp_path / "outside"
    _audio(outside / "private.mp3")
    link = music / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks are unavailable in this test environment: {exc}")
    player = IrisPlayerLibrary(tmp_path / "state")
    player.configure_library(music)

    result = player.refresh()

    assert result["total"] == 0
    assert result["skipped"] == 1


def test_missing_track_and_invalid_pagination_are_reported(tmp_path: Path) -> None:
    music = tmp_path / "music"
    music.mkdir()
    player = IrisPlayerLibrary(tmp_path / "state")
    player.configure_library(music)

    with pytest.raises(TrackNotFoundError):
        player.record_play("trk_missing")
    with pytest.raises(ValueError):
        player.list_tracks(offset=-1)
    with pytest.raises(ValueError):
        player.list_tracks(limit=-1)


def test_corrupt_state_recovers_without_scanning_or_exposing_files(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    state_path = state_root / "player" / "library.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text("{not-json", encoding="utf-8")

    player = IrisPlayerLibrary(state_root)

    assert player.status()["configured"] is False
    assert player.list_tracks() == []
    assert state_path.read_text(encoding="utf-8") == "{not-json"
