"""A08.2 profile paths and actual cooperative processes, isolated from homes."""

from __future__ import annotations

import asyncio
import json
import multiprocessing
import os
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import profile_paths as profiles
from core.profile_paths import (
    PROFILE_FORMAT_VERSION,
    ProfileBusyError,
    ProfilePathError,
    ProfilePaths,
    async_profile_writer,
    get_profile_paths,
    profile_coordination,
    profile_writer,
    validate_profile_home,
)


@pytest.fixture(autouse=True)
def isolated_legacy_process_inventory(monkeypatch):
    """No unit test may inspect the user's actual processes or environments."""
    import psutil

    monkeypatch.setattr(psutil, "process_iter", lambda attrs: iter(()))

    def missing_process(pid):
        raise psutil.NoSuchProcess(pid)

    monkeypatch.setattr(psutil, "Process", missing_process)


class LegacyProcess:
    def __init__(self, pid, argv, environment, *, name="python.exe", deny=""):
        self.pid = pid
        self.info = {"pid": pid, "name": name}
        self.argv = argv
        self.environment = environment
        self.deny = deny
        self.environment_reads = 0
        self.command_reads = 0

    def cmdline(self):
        import psutil

        self.command_reads += 1
        if self.deny == "cmdline":
            raise psutil.AccessDenied(self.pid)
        return self.argv

    def environ(self):
        import psutil

        self.environment_reads += 1
        if self.deny == "environ":
            raise psutil.AccessDenied(self.pid)
        return self.environment


def _legacy_inventory(monkeypatch, processes, *, enumerated=True):
    import psutil

    by_pid = {process.pid: process for process in processes}
    monkeypatch.setattr(
        psutil,
        "process_iter",
        lambda attrs: iter(processes if enumerated else ()),
    )

    def process_for(pid):
        if pid not in by_pid:
            raise psutil.NoSuchProcess(pid)
        return by_pid[pid]

    monkeypatch.setattr(psutil, "Process", process_for)


def _hold_writer(home, version, ready, release):
    """Spawn-compatible fixture; never resolves or reads a personal profile."""
    try:
        with profile_writer(
            "fixture-child", paths=ProfilePaths(Path(home)), format_version=version
        ):
            ready.put("entered")
            release.wait(20)
    except BaseException as exc:
        ready.put(type(exc).__name__)


def _start_writer(paths, version=PROFILE_FORMAT_VERSION):
    context = multiprocessing.get_context("spawn")
    ready, release = context.Queue(), context.Event()
    process = context.Process(target=_hold_writer, args=(str(paths.home), version, ready, release))
    process.start()
    assert ready.get(timeout=15) == "entered"
    return process, release


def _finish_writer(process, release):
    release.set()
    process.join(timeout=10)
    if process.is_alive():
        process.terminate()
        process.join(timeout=10)
    assert process.exitcode == 0


def test_paths_keep_existing_profile_and_do_not_create_overlay(tmp_path, monkeypatch):
    home = tmp_path / "perfil con espacios y \u00e1"
    monkeypatch.setenv("IRIS_HOME", str(home))
    paths = get_profile_paths()
    assert paths.home == home.resolve()
    assert paths.animations == paths.home / "animations"
    assert paths.state == paths.home / ".profile"
    assert paths.migrations == paths.home / "migrations"
    assert home.is_dir()  # Existing home resolver behavior is preserved.
    assert not paths.animations.exists()
    assert not paths.state.exists()


@pytest.mark.parametrize("kind", ["iris", "hermes", "default-iris", "default-hermes"])
def test_packaged_rejects_installation_profile_before_writing(tmp_path, monkeypatch, kind):
    from core import config, runtime_descriptor
    from wizard import hermes_config

    install = tmp_path / "installation"
    install.mkdir()
    monkeypatch.setattr(
        runtime_descriptor, "get_runtime", lambda: SimpleNamespace(packaged=True, base=install)
    )
    if kind in {"iris", "hermes"}:
        variable = "IRIS_HOME" if kind == "iris" else "HERMES_HOME"
        destination = install / "personal"
        monkeypatch.setenv(variable, str(destination))
        resolver = config.get_iris_home if kind == "iris" else hermes_config.get_hermes_home
    elif kind == "default-iris":
        monkeypatch.delenv("IRIS_HOME", raising=False)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: install))
        destination, resolver = install / ".iris", config.get_iris_home
    else:
        monkeypatch.delenv("HERMES_HOME", raising=False)
        if os.name == "nt":
            monkeypatch.setenv("LOCALAPPDATA", str(install))
            destination = install / "hermes"
        else:
            monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
            monkeypatch.setattr(Path, "home", classmethod(lambda cls: install))
            destination = install / ".hermes"
        resolver = hermes_config.get_hermes_home
    with pytest.raises(ProfilePathError):
        resolver()
    assert not destination.exists()


def _directory_alias(alias, target):
    try:
        alias.symlink_to(target, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            raise
        result = subprocess.run(
            ["cmd.exe", "/D", "/C", "mklink", "/J", str(alias), str(target)],
            capture_output=True,
            text=True,
            errors="replace",  # cmd may emit OEM bytes even when Python uses UTF-8.
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode:
            pytest.fail(f"Cannot create the Windows junction fixture: {result.stderr}")


@pytest.mark.parametrize("name", ["animations", ".profile", "migrations"])
def test_internal_aliases_are_rejected_even_when_target_is_inside_home(tmp_path, name):
    home = tmp_path / "profile"
    home.mkdir()
    target = home / "other"
    target.mkdir()
    _directory_alias(home / name, target)
    with pytest.raises(ProfilePathError):
        ProfilePaths(home)


def test_packaged_rejects_home_alias_into_installation(tmp_path, monkeypatch):
    from core import runtime_descriptor

    installation = tmp_path / "installation"
    installation.mkdir()
    _directory_alias(tmp_path / "alias", installation)
    monkeypatch.setattr(
        runtime_descriptor, "get_runtime", lambda: SimpleNamespace(packaged=True, base=installation)
    )
    with pytest.raises(ProfilePathError):
        validate_profile_home(tmp_path / "alias" / "profile")
    assert not (installation / "profile").exists()


@pytest.mark.parametrize("aliased", [False, True])
def test_packaged_rejects_home_ancestor_of_installation(tmp_path, monkeypatch, aliased):
    from core import config, runtime_descriptor

    home = tmp_path / "home"
    installation = home / "installation"
    installation.mkdir(parents=True)
    candidate = home
    if aliased:
        candidate = tmp_path / "alias"
        _directory_alias(candidate, home)
    monkeypatch.setattr(
        runtime_descriptor,
        "get_runtime",
        lambda: SimpleNamespace(packaged=True, base=installation),
    )
    monkeypatch.setenv("IRIS_HOME", str(candidate))
    with pytest.raises(ProfilePathError):
        config.get_iris_home()
    assert sorted(path.name for path in home.iterdir()) == ["installation"]


def test_relative_profile_and_file_subdirectory_are_rejected(tmp_path):
    with pytest.raises(ProfilePathError):
        validate_profile_home("relative-profile")
    (tmp_path / "animations").write_text("fixture", encoding="utf-8")
    with pytest.raises(ProfilePathError):
        ProfilePaths(tmp_path)


def test_nested_compatible_contexts_and_preparation_commit_order(tmp_path):
    paths = ProfilePaths(tmp_path)
    events = []

    def prepare():
        assert not (paths.state / "format.json").exists()
        assert not list((paths.state / "writers").glob("*.lease"))
        with profile_coordination(paths=paths):
            events.append("prepared")

    with profile_writer("outer", paths=paths, prepare=prepare):
        assert events == ["prepared"]
        with profile_writer("inner", paths=paths):
            assert len(list((paths.state / "writers").glob("*.lease"))) == 2
    assert not list((paths.state / "writers").glob("*.lease"))
    assert json.loads((paths.state / "format.json").read_text()) == {
        "schema_version": 1,
        "format_version": 1,
    }
    assert (paths.state / "coordination.lock").exists()


def test_prepare_failure_is_resumable_and_does_not_commit_a_format(tmp_path):
    paths = ProfilePaths(tmp_path)

    def interrupted():
        (paths.state / "fixture-journal").write_text("started", encoding="utf-8")
        raise OSError("fixture interruption")

    with pytest.raises(OSError, match="fixture interruption"):
        with profile_writer("failed", paths=paths, prepare=interrupted):
            pytest.fail("A failed preparation must not enter a writer")
    assert not (paths.state / "format.json").exists()
    assert not list((paths.state / "writers").glob("*.lease"))
    with profile_writer(
        "retry", paths=paths, prepare=lambda: (paths.state / "fixture-journal").unlink()
    ):
        pass
    assert (paths.state / "format.json").is_file()


def test_stale_lease_with_live_pid_is_reclaimed_using_native_lock(tmp_path):
    paths = ProfilePaths(tmp_path)
    writers = paths.state / "writers"
    writers.mkdir(parents=True)
    stale = writers / "previous.lease"
    stale.write_bytes(
        b"\0"
        + json.dumps(
            {
                "schema_version": 1,
                "format_version": 2,
                "role": "previous",
                "pid": os.getpid(),
            }
        ).encode("utf-8")
    )
    with profile_writer("current", paths=paths):
        assert not stale.exists()


def test_two_real_processes_allow_compatible_writer_and_block_incompatible_prepare(tmp_path):
    paths = ProfilePaths(tmp_path)
    process, release = _start_writer(paths)
    try:
        with profile_writer("parent-compatible", paths=paths):
            assert len(list((paths.state / "writers").glob("*.lease"))) == 2
        prepared = []
        with pytest.raises(ProfileBusyError):
            with profile_writer(
                "parent-incompatible",
                paths=paths,
                format_version=2,
                prepare=lambda: prepared.append(True),
            ):
                pass
        assert not prepared
    finally:
        _finish_writer(process, release)


def test_crashed_writer_releases_native_lease_and_allows_later_migration(tmp_path):
    paths = ProfilePaths(tmp_path)
    process, release = _start_writer(paths)
    process.terminate()
    process.join(timeout=10)
    assert not process.is_alive()
    assert list((paths.state / "writers").glob("*.lease"))
    with profile_writer("upgrade-after-crash", paths=paths, format_version=2, prepare=lambda: None):
        assert len(list((paths.state / "writers").glob("*.lease"))) == 1
    assert not list((paths.state / "writers").glob("*.lease"))
    with pytest.raises(ProfileBusyError):
        with profile_writer("older-version", paths=paths):
            pass


@pytest.mark.parametrize(
    "document",
    [
        {"schema_version": 1, "format_version": 2},
        {"schema_version": 2, "format_version": 1},
        {"schema_version": 1, "format_version": True},
    ],
)
def test_unknown_or_newer_persisted_format_blocks_prepare(tmp_path, document):
    paths = ProfilePaths(tmp_path)
    paths.state.mkdir()
    (paths.state / "format.json").write_text(json.dumps(document), encoding="utf-8")
    prepared = []
    with pytest.raises(ProfileBusyError):
        with profile_writer("old-version", paths=paths, prepare=lambda: prepared.append(True)):
            pass
    assert not prepared


@pytest.mark.asyncio
async def test_cancelled_blocked_prepare_settles_and_releases_lease(tmp_path):
    paths = ProfilePaths(tmp_path)
    started, release = threading.Event(), threading.Event()

    def blocked_prepare():
        started.set()
        assert release.wait(10)

    async def task_body():
        async with async_profile_writer("cancelled", paths=paths, prepare=blocked_prepare):
            pytest.fail("The cancelled operation must not yield its writer")

    task = asyncio.create_task(task_body())
    assert await asyncio.to_thread(started.wait, 10)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    task.cancel()  # A repeated cancellation must not orphan the native worker.
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=10)
    assert not list((paths.state / "writers").glob("*.lease"))
    async with async_profile_writer("compatible-retry", paths=paths):
        pass
    async with async_profile_writer(
        "upgrade-retry", paths=paths, format_version=2, prepare=lambda: None
    ):
        pass


@pytest.mark.asyncio
async def test_async_writer_releases_lease_when_body_is_cancelled(tmp_path):
    paths = ProfilePaths(tmp_path)
    entered = asyncio.Event()

    async def task_body():
        async with async_profile_writer("cancelled-body", paths=paths):
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(task_body())
    await asyncio.wait_for(entered.wait(), timeout=10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=10)
    assert not list((paths.state / "writers").glob("*.lease"))


@pytest.mark.parametrize(
    "argv",
    [
        ["python.exe", "-m", "core.agent"],
        ["python.exe", "-m", "core.desktop"],
        ["python.exe", "-m", "wizard.app"],
        ["python.exe", "-m", "wizard"],
        ["python.exe", "-m", "uvicorn", "wizard.app:app"],
        ["uvicorn.exe", "wizard.app:app"],
        ["python.exe", "F:/legacy/core/agent.py"],
        ["python.exe", "core/desktop.py"],
        ["python.exe", "wizard/app.py"],
        ["F:/venv/Scripts/iris.exe"],
        ["F:/venv/Scripts/alice.exe"],
        ["/venv/bin/iris"],
        ["python", "/venv/bin/alice"],
    ],
)
def test_legacy_same_profile_blocks_prepare_without_any_markers(tmp_path, monkeypatch, argv):
    paths = ProfilePaths(tmp_path)
    process = LegacyProcess(
        51001,
        argv,
        {"IRIS_HOME": str(paths.home)},
        name=Path(argv[0]).name,
    )
    _legacy_inventory(monkeypatch, [process])
    prepared = []
    with pytest.raises(ProfileBusyError):
        with profile_writer("new", paths=paths, prepare=lambda: prepared.append(True)):
            pass
    assert not prepared
    assert not (paths.state / "format.json").exists()
    assert not paths.migrations.exists()
    assert not paths.animations.exists()


def test_legacy_different_profile_allows_prepare(tmp_path, monkeypatch):
    paths = ProfilePaths(tmp_path / "current")
    process = LegacyProcess(
        51002,
        ["python.exe", "-m", "wizard.app"],
        {"IRIS_HOME": str(tmp_path / "other")},
    )
    _legacy_inventory(monkeypatch, [process])
    prepared = []
    with profile_writer("new", paths=paths, prepare=lambda: prepared.append(True)):
        pass
    assert prepared == [True]


def test_native_iris_at_packaged_installation_root_does_not_block_prepare(tmp_path, monkeypatch):
    from core import runtime_descriptor

    installation = tmp_path / "installation"
    installation.mkdir()
    monkeypatch.setattr(
        runtime_descriptor,
        "get_runtime",
        lambda: SimpleNamespace(packaged=True, base=installation),
    )
    paths = ProfilePaths(tmp_path / "profile")
    process = LegacyProcess(
        51012,
        [str(installation / "Iris.exe")],
        {"IRIS_HOME": str(paths.home)},
        name="Iris.exe",
    )
    _legacy_inventory(monkeypatch, [process])
    prepared = []
    with profile_writer("new", paths=paths, prepare=lambda: prepared.append(True)):
        pass
    assert prepared == [True]
    assert process.environment_reads == 0


@pytest.mark.parametrize("explicit", [False, True])
def test_legacy_default_and_tilde_home_match_candidate_environment(tmp_path, monkeypatch, explicit):
    paths = ProfilePaths(tmp_path / ("custom" if explicit else ".iris"))
    env = {"USERPROFILE" if os.name == "nt" else "HOME": str(tmp_path)}
    if explicit:
        env["IRIS_HOME"] = "~/custom"
    process = LegacyProcess(51003, ["python.exe", "-m", "core.agent"], env)
    _legacy_inventory(monkeypatch, [process])
    with pytest.raises(ProfileBusyError):
        profiles._legacy_profile_writers(paths)


@pytest.mark.parametrize("marker", ["IRIS.pid", "IRIS.pid.json", "companion_runtime.json"])
def test_legacy_marker_pid_is_reverified_even_when_not_enumerated(tmp_path, monkeypatch, marker):
    paths = ProfilePaths(tmp_path)
    process = LegacyProcess(51004, ["python.exe", "-m", "core.agent"], {"IRIS_HOME": str(tmp_path)})
    _legacy_inventory(monkeypatch, [process], enumerated=False)
    raw = str(process.pid) if marker == "IRIS.pid" else json.dumps({"pid": process.pid})
    (tmp_path / marker).write_text(raw, encoding="utf-8")
    with pytest.raises(ProfileBusyError):
        profiles._legacy_profile_writers(paths)


@pytest.mark.parametrize("deny", ["cmdline", "environ"])
def test_legacy_uninspectable_candidate_blocks_prepare(tmp_path, monkeypatch, deny):
    paths = ProfilePaths(tmp_path)
    process = LegacyProcess(
        51005,
        ["python.exe", "-m", "wizard.app"],
        {"IRIS_HOME": str(tmp_path)},
        deny=deny,
    )
    _legacy_inventory(monkeypatch, [process])
    prepared = []
    with pytest.raises(ProfileBusyError):
        with profile_writer("new", paths=paths, prepare=lambda: prepared.append(True)):
            pass
    assert not prepared


def test_legacy_own_stale_and_reused_pid_hints_do_not_block(tmp_path, monkeypatch):
    paths = ProfilePaths(tmp_path)
    own = LegacyProcess(
        os.getpid(), ["python.exe", "-m", "core.agent"], {"IRIS_HOME": str(tmp_path)}
    )
    reused = LegacyProcess(51006, ["python.exe", "-m", "unrelated"], {}, deny="environ")
    _legacy_inventory(monkeypatch, [own, reused])
    (tmp_path / "IRIS.pid").write_text(str(os.getpid()), encoding="utf-8")
    (tmp_path / "IRIS.pid.json").write_text(json.dumps({"pid": 51007}), encoding="utf-8")
    (tmp_path / "companion_runtime.json").write_text(
        json.dumps({"pid": reused.pid}), encoding="utf-8"
    )
    with profile_writer("new", paths=paths, prepare=lambda: None):
        pass
    assert own.command_reads == 0
    assert reused.environment_reads == 0


def test_native_compatible_lease_excludes_candidate_pid_before_first_prepare(tmp_path, monkeypatch):
    paths = ProfilePaths(tmp_path)
    process = LegacyProcess(51008, ["python.exe", "-m", "core.agent"], {"IRIS_HOME": str(tmp_path)})
    _legacy_inventory(monkeypatch, [process])
    with profile_writer("cooperating", paths=paths):
        lease_path = next((paths.state / "writers").glob("*.lease"))
        # Simulate a compatible writer whose profile format was restored to
        # absence. Its live native lease, rather than a PID hint, proves use.
        with lease_path.open("r+b") as handle:
            handle.seek(1)
            handle.write(
                json.dumps(
                    {
                        "schema_version": 1,
                        "format_version": 1,
                        "role": "cooperating",
                        "pid": process.pid,
                    }
                ).encode("utf-8")
            )
            handle.truncate()
        (paths.state / "format.json").unlink()
        with profile_writer("new", paths=paths, prepare=lambda: None):
            pass
    assert process.command_reads == 0


def test_current_profile_format_skips_legacy_probe_and_old_format_requires_it(
    tmp_path, monkeypatch
):
    paths = ProfilePaths(tmp_path)
    with profile_writer("initial", paths=paths):
        pass
    process = LegacyProcess(51009, ["python.exe", "-m", "wizard.app"], {"IRIS_HOME": str(tmp_path)})
    _legacy_inventory(monkeypatch, [process])
    with profile_writer("current", paths=paths, prepare=lambda: None):
        pass
    assert process.command_reads == 0
    (paths.state / "format.json").write_text(
        json.dumps({"schema_version": 1, "format_version": 0}),
        encoding="utf-8",
    )
    prepared = []
    with pytest.raises(ProfileBusyError):
        with profile_writer("upgrade", paths=paths, prepare=lambda: prepared.append(True)):
            pass
    assert not prepared


def test_deeply_nested_profile_metadata_is_rejected_as_incompatible(tmp_path):
    paths = ProfilePaths(tmp_path)
    paths.state.mkdir()
    (paths.state / "format.json").write_bytes(
        b'{"nested":' + b"[" * 1500 + b"0" + b"]" * 1500 + b"}"
    )
    with pytest.raises(ProfileBusyError):
        with profile_writer("new", paths=paths, prepare=lambda: None):
            pass


@pytest.mark.parametrize("target_exists", [False, True])
def test_legacy_home_migration_guard_runs_before_any_profile_changes(
    tmp_path,
    monkeypatch,
    target_exists,
):
    from core import config

    base = tmp_path / "fixture-user"
    source, target = base / ".alice", base / ".iris"
    source.mkdir(parents=True)
    (source / "config.json").write_bytes(b'{"assistant_name":"fixture-old"}')
    if target_exists:
        target.mkdir()
        (target / "config.json").write_bytes(b'{"assistant_name":"fixture-current"}')
    before = {
        str(path.relative_to(base)): path.read_bytes() for path in base.rglob("*") if path.is_file()
    }
    monkeypatch.delenv("IRIS_HOME", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: base))

    def busy_source(paths, *, compatible_pids=None):
        if paths.home == source.resolve():
            raise ProfileBusyError("fixture old writer")

    monkeypatch.setattr(profiles, "_legacy_profile_writers", busy_source)
    with pytest.raises(ProfileBusyError, match="fixture old writer"):
        config.get_iris_home()
    after = {
        str(path.relative_to(base)): path.read_bytes() for path in base.rglob("*") if path.is_file()
    }
    assert after == before
    assert target.exists() == target_exists
    assert not (source / ".profile").exists()
    assert not (target / ".profile").exists()


@pytest.mark.parametrize("active_source", [False, True])
def test_retained_legacy_home_excludes_native_compatible_target_but_checks_source(
    tmp_path,
    monkeypatch,
    active_source,
):
    from core import config

    base = tmp_path / "fixture-user"
    source, target = base / ".alice", base / ".iris"
    source.mkdir(parents=True)
    target.mkdir()
    (source / "source-only.txt").write_bytes(b"fixture original")
    (target / "config.json").write_bytes(b'{"assistant_name":"fixture-current"}')
    current = LegacyProcess(51010, ["python.exe", "-m", "core.agent"], {"IRIS_HOME": str(target)})
    legacy = LegacyProcess(51011, ["python.exe", "-m", "wizard.app"], {"IRIS_HOME": str(source)})
    _legacy_inventory(monkeypatch, [current, legacy] if active_source else [current])
    monkeypatch.delenv("IRIS_HOME", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: base))
    paths = ProfilePaths(target)
    with profile_writer("cooperating", paths=paths):
        lease_path = next((paths.state / "writers").glob("*.lease"))
        with lease_path.open("r+b") as handle:
            handle.seek(1)
            handle.write(
                json.dumps(
                    {
                        "schema_version": 1,
                        "format_version": 1,
                        "role": "cooperating",
                        "pid": current.pid,
                    }
                ).encode("utf-8")
            )
            handle.truncate()
        previous_format = (paths.state / "format.json").read_bytes()
        if active_source:
            with pytest.raises(ProfileBusyError):
                config.get_iris_home()
            assert not (target / "source-only.txt").exists()
        else:
            assert config.get_iris_home() == target.resolve()
            assert (target / "source-only.txt").read_bytes() == b"fixture original"
        assert (target / "config.json").read_bytes() == b'{"assistant_name":"fixture-current"}'
        assert (paths.state / "format.json").read_bytes() == previous_format
    assert (source / "source-only.txt").read_bytes() == b"fixture original"


@pytest.mark.skipif(os.name != "nt", reason="Windows aliases directory case")
def test_case_alias_of_current_home_does_not_start_a_legacy_migration(tmp_path, monkeypatch):
    from core import config

    source = tmp_path / ".IRIS"
    source.mkdir()
    (source / "config.json").write_bytes(b'{"assistant_name":"fixture-current"}')
    monkeypatch.delenv("IRIS_HOME", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    def unexpected_scan(paths, *, compatible_pids=None):
        pytest.fail("A case alias of the same directory needs no home migration")

    monkeypatch.setattr(profiles, "_legacy_profile_writers", unexpected_scan)
    assert config.get_iris_home() == source.resolve()
    assert (source / "config.json").read_bytes() == b'{"assistant_name":"fixture-current"}'


# ── A08.7: a launcher that relays this very process is not another writer ────

_RELAY_SCRIPT = """
import os, subprocess, sys
sys.path.insert(0, os.environ["IRIS_TEST_REPO"])
if os.environ.pop("IRIS_TEST_RELAY", "") == "1":
    # Like a venv's Scripts/python.exe: start the real interpreter with the same
    # arguments and stay alive while it runs.
    raise SystemExit(subprocess.run([sys.executable, *sys.argv]).returncode)
if "--hold" in sys.argv:
    sys.stdin.read()
    raise SystemExit(0)
from pathlib import Path
from core.profile_paths import ProfileBusyError, ProfilePaths, _legacy_profile_writers
try:
    _legacy_profile_writers(ProfilePaths(Path(os.environ["IRIS_HOME"])))
    print("free", flush=True)
except ProfileBusyError as exc:
    print("busy", exc, flush=True)
"""


def _relay_script(tmp_path):
    # Named like a standard entrypoint so the scan recognizes it as an Iris writer.
    script = tmp_path / "fuente" / "core" / "desktop.py"
    script.parent.mkdir(parents=True)
    script.write_text(_RELAY_SCRIPT, "utf-8")
    return script


def _relay_env(home, **extra):
    import sys

    return {**os.environ, "IRIS_HOME": str(home), "IRIS_TEST_REPO": str(Path(__file__).resolve().parents[1]),
            "PYTHONPATH": "", **extra}


def test_relaying_launcher_parent_does_not_block_a_new_profile(tmp_path):
    import sys

    home = tmp_path / "perfil nuevo"
    home.mkdir()
    result = subprocess.run([sys.executable, str(_relay_script(tmp_path))], env=_relay_env(home, IRIS_TEST_RELAY="1"),
                            capture_output=True, text=True, timeout=120)
    assert result.stdout.strip() == "free", (result.stdout, result.stderr)


def test_an_independent_writer_of_the_same_profile_still_blocks(tmp_path):
    import sys

    home = tmp_path / "perfil en uso"
    home.mkdir()
    script = _relay_script(tmp_path)
    holder = subprocess.Popen([sys.executable, str(script), "--hold"], env=_relay_env(home),
                              stdin=subprocess.PIPE, text=True)
    try:
        result = subprocess.run([sys.executable, str(script)], env=_relay_env(home, IRIS_TEST_RELAY="1"),
                                capture_output=True, text=True, timeout=120)
        assert result.stdout.startswith("busy"), (result.stdout, result.stderr)
    finally:
        holder.stdin.close()
        holder.wait(30)
