"""Pinned Git source extraction never copies developer environments or profiles."""

from __future__ import annotations

import hashlib
import io
import json
import stat
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import hermes_source as source

REVISION = "fca3221df48d4ae1e4db9e8a1a041de21ea4d9cf"
FILES = {
    "LICENSE": b"MIT notice\n",
    "README.md": b"Factory documentation\n",
    "pyproject.toml": b'[project]\nversion = "0.21.4"\n',
    "uv.lock": b"version = 1\n",
    "setup.py": b"raise RuntimeError('Never execute upstream wheel build')\n",
    "hermes_cli/__init__.py": b'__version__ = "0.21.4"\n',
    "hermes_cli/main.py": b"# exact factory code\n",
    "tools/transcription_audio.py": b"# exact factory code\n",
    "skills/example/SKILL.md": b"# factory skill\n",
    "optional-skills/mail/run.py": b"# factory optional skill\n",
    "optional-mcps/server.yaml": b"name: factory\n",
    "plugin-catalog/plugin.yaml": b"name: factory\n",
    "plugins/platforms/example/plugin.yaml": b"name: factory\n",
    "locales/en.json": b'{"hello":"Hello"}\n',
    "assets/icon.png": b"factory image\n",
}
COMMIT_TIME = 1791100000
# Hermes 0.21.6: version served from the install stamp, two new top-level packages,
# and GitHub's own tarball prefix.
STAMPED = {
    **FILES,
    "pyproject.toml": b'[project]\nversion = "0.0.0"\n',
    "hermes_cli/__init__.py": b'__release_date__ = "2026.9.24"\n__version__: str\n',
    "pm/paths.py": b"# package manager\n",
    "hermes_platform/__init__.py": b"# platform layer\n",
}
GITHUB_PREFIX = f"hermes-agent-{REVISION}"
STAMP_POLICY = {"distribution": "iris", "update_mechanism": "external"}


def make_archive(tmp_path, files=None, *, revision=REVISION, extra=None, prefix="hermes"):
    files = FILES if files is None else files
    archive = tmp_path / "source.tar"
    with tarfile.open(archive, "w", format=tarfile.PAX_FORMAT, pax_headers={"comment": revision}) as out:
        for name, payload in files.items():
            item = tarfile.TarInfo(f"{prefix}/{name}")
            item.size = len(payload)
            item.mtime = COMMIT_TIME
            out.addfile(item, io.BytesIO(payload))
        if extra is not None:
            member, data = extra
            out.addfile(member, io.BytesIO(data) if data is not None else None)
    pin = {
        "version": "0.21.4", "revision": REVISION,
        "source_archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "source_hashes": {name: hashlib.sha256(files.get(name, FILES[name])).hexdigest()
                          for name in source.REQUIRED_SOURCE_HASHES},
    }
    return archive, pin


def test_factory_bytes_preserved_with_portable_launcher_and_pin(tmp_path):
    archive, pin = make_archive(tmp_path)
    destination = tmp_path / "Iris ñ/runtimes source/hermes"
    result = source.extract_hermes_source(archive, destination, pin)
    assert result["source_files"] == len(FILES)
    assert result["source_bytes"] == sum(map(len, FILES.values()))
    assert result["source_archive_sha256"] == pin["source_archive_sha256"]
    for name, payload in FILES.items():
        assert (destination / name).read_bytes() == payload
    assert (destination / "iris_cli.py").read_text("utf8") == source.CLI_LAUNCHER
    assert json.loads((destination / "runtime-pin.json").read_text("utf8")) == {
        "schema_version": 1, "version": "0.21.4", "revision": REVISION,
    }
    assert "runpy.run_module" in source.CLI_LAUNCHER
    assert "F:" not in source.CLI_LAUNCHER


def test_archive_public_source_allowlist_excludes_profiles_tools_and_caches(tmp_path):
    excluded = {
        ".env": b"PRIVATE", "auth.json": b"PRIVATE", "state.db": b"PRIVATE",
        ".hermes/config.yaml": b"PRIVATE", "venv/Lib/site-packages/x.py": b"PRIVATE",
        "tools/__pycache__/x.pyc": b"PRIVATE", "skills/example/.env": b"PRIVATE",
        "tools/.cache/data": b"PRIVATE", "tests/test_factory.py": b"DEV",
        "scripts/install.ps1": b"DEV", "apps/desktop/main.ts": b"DEV",
        "web/src/main.ts": b"DEV", "ui-tui/main.js": b"DEV", ".github/ci.yaml": b"DEV",
    }
    archive, pin = make_archive(tmp_path, {**FILES, **excluded})
    destination = tmp_path / "hermes"
    source.extract_hermes_source(archive, destination, pin)
    for name in excluded:
        assert not (destination / name).exists()
    assert (destination / "setup.py").read_bytes() == FILES["setup.py"]


@pytest.mark.parametrize("name", [
    "/absolute.py", "../outside.py", "hermes/../outside.py", "hermes/tools/../../outside.py",
    "C:/absolute.py", "hermes/C:/absolute.py", "hermes/tools\\outside.py",
    "hermes/tools/file.py:stream", "hermes//tools/x.py", "hermes/tools/file. ",
    "wrong-prefix/tools/x.py", "//server/share.py", "hermes/tools/NUL.py",
])
def test_unsafe_archive_paths_rejected_before_creating_destination(tmp_path, name):
    item = tarfile.TarInfo(name)
    archive, pin = make_archive(tmp_path, extra=(item, b""))
    destination = tmp_path / "hermes"
    with pytest.raises(source.HermesSourceError):
        source.extract_hermes_source(archive, destination, pin)
    assert not destination.exists()
    assert not list(tmp_path.glob(".hermes-source-*"))


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE, tarfile.CHRTYPE])
def test_links_and_unknown_member_methods_are_rejected(tmp_path, kind):
    item = tarfile.TarInfo("hermes/tools/linked.py")
    item.type = kind
    item.linkname = "../../outside.py"
    archive, pin = make_archive(tmp_path, extra=(item, None))
    with pytest.raises(source.HermesSourceError, match="member type"):
        source.extract_hermes_source(archive, tmp_path / "hermes", pin)
    assert not (tmp_path / "hermes").exists()


@pytest.mark.parametrize("name", ["tools/transcription_audio.py", "TOOLS/TRANSCRIPTION_AUDIO.PY"])
def test_duplicate_and_windows_case_collisions_fail(tmp_path, name):
    item = tarfile.TarInfo("hermes/" + name)
    archive, pin = make_archive(tmp_path, extra=(item, b""))
    with pytest.raises(source.HermesSourceError, match="case-colliding"):
        source.extract_hermes_source(archive, tmp_path / "hermes", pin)


def test_wrong_archive_hash_fails(tmp_path):
    archive, pin = make_archive(tmp_path)
    pin["source_archive_sha256"] = "0" * 64
    with pytest.raises(source.HermesSourceError, match="archive SHA256"):
        source.extract_hermes_source(archive, tmp_path / "hermes", pin)


def test_wrong_git_revision_fails_even_when_archive_hash_matches(tmp_path):
    archive, pin = make_archive(tmp_path, revision="0" * 40)
    with pytest.raises(source.HermesSourceError, match="Git archive commit"):
        source.extract_hermes_source(archive, tmp_path / "hermes", pin)


@pytest.mark.parametrize("name", sorted(source.REQUIRED_SOURCE_HASHES))
def test_source_manifest_hash_is_independently_checked(tmp_path, name):
    archive, pin = make_archive(tmp_path)
    pin["source_hashes"][name] = "0" * 64
    with pytest.raises(source.HermesSourceError, match="source hash mismatch"):
        source.extract_hermes_source(archive, tmp_path / "hermes", pin)


@pytest.mark.parametrize("missing", sorted(source.REQUIRED_SOURCE_HASHES))
def test_missing_source_manifests_fail(tmp_path, missing):
    archive, pin = make_archive(tmp_path, {key: value for key, value in FILES.items() if key != missing})
    # Keep the reviewed expected manifest hash even if the archive omits it.
    pin["source_hashes"][missing] = hashlib.sha256(FILES[missing]).hexdigest()
    with pytest.raises(source.HermesSourceError, match="source hash mismatch"):
        source.extract_hermes_source(archive, tmp_path / "hermes", pin)


def test_source_version_must_match_without_executing_any_code(tmp_path):
    changed = dict(FILES)
    changed["hermes_cli/__init__.py"] = b'__version__ = "0.22.0"\nraise RuntimeError("never execute")'
    archive, pin = make_archive(tmp_path, changed)
    with pytest.raises(source.HermesSourceError, match="source version"):
        source.extract_hermes_source(archive, tmp_path / "hermes", pin)


@pytest.mark.parametrize("name", sorted(source.GENERATED_FILES))
def test_source_cannot_replace_iris_generated_runtime_metadata(tmp_path, name):
    archive, pin = make_archive(tmp_path, {**FILES, name: b"upstream collision"})
    with pytest.raises(source.HermesSourceError, match="collides"):
        source.extract_hermes_source(archive, tmp_path / "hermes", pin)


@pytest.mark.parametrize("key,value", [
    ("version", "latest"), ("revision", "main"), ("source_archive_sha256", "missing"),
    ("source_hashes", {}), ("source_hashes", {"outside": "0" * 64}),
])
def test_incomplete_or_floating_pin_fails(tmp_path, key, value):
    archive, pin = make_archive(tmp_path)
    pin[key] = value
    with pytest.raises(source.HermesSourceError):
        source.extract_hermes_source(archive, tmp_path / "hermes", pin)


def test_existing_destination_is_preserved(tmp_path):
    archive, pin = make_archive(tmp_path)
    destination = tmp_path / "hermes"
    destination.mkdir()
    original = destination / "original.txt"
    original.write_bytes(b"original")
    with pytest.raises(source.HermesSourceError, match="new destination"):
        source.extract_hermes_source(archive, destination, pin)
    assert original.read_bytes() == b"original"
    assert list(destination.iterdir()) == [original]


@pytest.mark.parametrize("link_kind", ["symlink", "junction"])
def test_linked_destination_is_rejected_before_resolution(tmp_path, monkeypatch, link_kind):
    archive, pin = make_archive(tmp_path)
    destination = tmp_path / "linked/hermes"
    real_lstat = Path.lstat

    def lstat(path):
        if path == destination.parent:
            return SimpleNamespace(st_mode=stat.S_IFLNK if link_kind == "symlink" else stat.S_IFDIR,
                                   st_file_attributes=0x400 if link_kind == "junction" else 0)
        return real_lstat(path)

    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(source.HermesSourceError, match="symlink or junction"):
        source.extract_hermes_source(archive, destination, pin)
    assert not destination.exists()


def test_interrupted_extraction_never_publishes_partial_tree(tmp_path, monkeypatch):
    archive, pin = make_archive(tmp_path)
    copy = source.shutil.copyfileobj
    calls = 0

    def interrupted(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated disk failure")
        return copy(*args, **kwargs)

    monkeypatch.setattr(source.shutil, "copyfileobj", interrupted)
    with pytest.raises(OSError, match="disk failure"):
        source.extract_hermes_source(archive, tmp_path / "hermes", pin)
    assert not (tmp_path / "hermes").exists()
    assert not list(tmp_path.glob(".hermes-source-*"))


def test_source_changed_during_extraction_never_publishes(tmp_path, monkeypatch):
    archive, pin = make_archive(tmp_path)
    original = source.shutil.copyfileobj
    changed = False

    def copy(*args, **kwargs):
        nonlocal changed
        result = original(*args, **kwargs)
        if not changed:
            with archive.open("r+b") as handle:
                handle.seek(0)
                handle.write(b"X")
            changed = True
        return result

    monkeypatch.setattr(source.shutil, "copyfileobj", copy)
    with pytest.raises(source.HermesSourceError, match="changed during extraction"):
        source.extract_hermes_source(archive, tmp_path / "hermes", pin)
    assert not (tmp_path / "hermes").exists()
    assert not list(tmp_path.glob(".hermes-source-*"))


# ── Hermes 0.21.6 (A08.4b): install stamp, pm/hermes_platform, GitHub tarball prefix


def make_stamped(tmp_path, files=None, **policy):
    files = STAMPED if files is None else files
    archive, pin = make_archive(tmp_path, files, prefix=GITHUB_PREFIX)
    pin.update(version="0.21.6", archive_prefix=GITHUB_PREFIX, install_stamp={**STAMP_POLICY, **policy})
    pin["source_hashes"]["hermes_cli/__init__.py"] = hashlib.sha256(files["hermes_cli/__init__.py"]).hexdigest()
    return archive, pin


def test_hermes_0216_tree_gets_its_packages_and_a_sealed_install_stamp(tmp_path):
    archive, pin = make_stamped(tmp_path)
    destination = tmp_path / "hermes"
    source.extract_hermes_source(archive, destination, pin)
    assert (destination / "pm/paths.py").read_bytes() == STAMPED["pm/paths.py"]
    assert (destination / "hermes_platform/__init__.py").exists()
    stamp = json.loads((destination / "install-stamp.json").read_text("utf-8"))
    assert stamp == {
        "schemaVersion": 2, "commit": REVISION, "commitDate": COMMIT_TIME, "branch": None,
        "builtAt": "2026-10-04T07:46:40+00:00", "dirty": False, "source": "iris",
        "distribution": "iris", "updateMechanism": "external", "baseVersion": "0.21.6",
        "displayVersion": "0.21.6", "distance": 0, "payload": "bootstrap", "tag": None,
    }
    assert json.loads((destination / "runtime-pin.json").read_text("utf8"))["version"] == "0.21.6"
    # The same archive always gives the same stamp.
    again = tmp_path / "again"
    source.extract_hermes_source(archive, again, pin)
    assert (again / "install-stamp.json").read_bytes() == (destination / "install-stamp.json").read_bytes()


def test_a_literal_version_needs_no_stamp_and_writes_none(tmp_path):
    archive, pin = make_archive(tmp_path)
    source.extract_hermes_source(archive, tmp_path / "hermes", pin)
    assert not (tmp_path / "hermes/install-stamp.json").exists()


@pytest.mark.parametrize("missing", ["install_stamp", "init_hash"])
def test_a_source_without_literal_version_needs_the_stamp_and_its_reviewed_init(tmp_path, missing):
    archive, pin = make_stamped(tmp_path)
    if missing == "install_stamp":
        del pin["install_stamp"]
    else:
        del pin["source_hashes"]["hermes_cli/__init__.py"]
    with pytest.raises(source.HermesSourceError, match="literal version"):
        source.extract_hermes_source(archive, tmp_path / "hermes", pin)
    assert not (tmp_path / "hermes").exists()


@pytest.mark.parametrize("policy", [
    {"update_mechanism": "self"}, {"update_mechanism": "electron-updater"},
    {"distribution": "Iris OS"}, {"distribution": ""},
])
def test_the_stamp_never_lets_hermes_update_itself_and_names_a_plain_steward(tmp_path, policy):
    archive, pin = make_stamped(tmp_path, **policy)
    with pytest.raises(source.HermesSourceError, match="install stamp policy"):
        source.extract_hermes_source(archive, tmp_path / "hermes", pin)


def test_an_unknown_stamp_field_is_rejected(tmp_path):
    archive, pin = make_stamped(tmp_path)
    pin["install_stamp"]["payload"] = "bundled"
    with pytest.raises(source.HermesSourceError, match="install stamp policy"):
        source.extract_hermes_source(archive, tmp_path / "hermes", pin)


@pytest.mark.parametrize("prefix", ["hermes", "../hermes", "hermes/agent", ""])
def test_the_archive_prefix_is_the_reviewed_one(tmp_path, prefix):
    archive, pin = make_stamped(tmp_path)
    pin["archive_prefix"] = prefix
    with pytest.raises(source.HermesSourceError, match="prefix"):
        source.extract_hermes_source(archive, tmp_path / "hermes", pin)
    assert not (tmp_path / "hermes").exists()


def test_a_literal_version_still_wins_over_the_pin_with_a_stamp(tmp_path):
    changed = {**STAMPED, "hermes_cli/__init__.py": b'__version__ = "0.21.4"\n'}
    archive, pin = make_stamped(tmp_path, changed)
    with pytest.raises(source.HermesSourceError, match="source version"):
        source.extract_hermes_source(archive, tmp_path / "hermes", pin)
