"""A08.9 installer review (Codex, 2026-10-06): data protection, confinement and recovery.

Each test is the regression of one confirmed finding. Junctions are real Windows
directory junctions; a test is skipped where they cannot be created.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import iris_install as inst
from core import iris_installer as installer
from core import iris_updates as upd
from tests.test_iris_installer import _profile_format, _stage, quiet  # noqa: F401
from tests.test_iris_updates import channel_doc, installed, keypair, make_version, profile  # noqa: F401


def _junction(link: Path, target: Path) -> None:
    made = subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
    if made.returncode:
        pytest.skip("directory junctions are not available here")


def _unjunction(link: Path) -> None:
    if os.path.lexists(link):
        os.rmdir(link)  # removes the junction itself, never its target


def _packaged_at(monkeypatch, folder: Path) -> None:
    from core import runtime_descriptor

    monkeypatch.setattr(runtime_descriptor, "get_runtime", lambda: SimpleNamespace(packaged=True, base=folder))


@pytest.mark.parametrize("where", ["sibling of v", "alias"])
def test_a_packaged_profile_cannot_live_anywhere_in_the_installation(installed, monkeypatch, tmp_path, where):
    from core import profile_paths

    install = installed.install
    _packaged_at(monkeypatch, install.folder(installed.active))
    candidate = install.root / "perfil-iris"
    alias = tmp_path / "alias-del-perfil"
    if where == "alias":
        candidate.mkdir()
        _junction(alias, candidate)
        candidate = alias
    try:
        with pytest.raises(profile_paths.ProfilePathError, match="fuera de la instalación"):
            profile_paths.validate_profile_home(candidate)
    finally:
        _unjunction(alias)


@pytest.mark.parametrize("which", ["iris_home", "hermes_home"])
def test_uninstall_refuses_and_changes_nothing_when_data_lives_inside_the_installation(
        installed, quiet, monkeypatch, tmp_path, which):
    install = installed.install
    inside = install.root / "datos"
    inside.mkdir()
    (inside / "memoria.db").write_bytes(b"recuerdos")
    folders = {"iris_home": str(tmp_path / "perfil"), "hermes_home": str(tmp_path / "hermes"), which: str(inside)}
    monkeypatch.setattr(installer, "profile_folders", lambda: folders)
    with pytest.raises(inst.InstallError, match="dentro de la carpeta de instalación"):
        installer.uninstall(install.root, delete_data=False)
    assert quiet == [] and (inside / "memoria.db").exists()  # nothing was closed or deleted


@pytest.mark.parametrize("position", ["inside", "equal"])
def test_deleting_the_profile_never_deletes_hermes(profile, monkeypatch, position):
    hermes = profile.iris / "hermes" if position == "inside" else profile.iris
    hermes.mkdir(exist_ok=True)
    _profile_format(profile.iris)
    credential = hermes / ".env"
    credential.write_text("TEST_ONLY=fixture", "utf-8")
    monkeypatch.setenv("HERMES_HOME", str(hermes))
    result = installer.delete_profile()
    assert result["deleted"] is False and "Hermes" in result["reason"] and credential.exists()


def _managed_companion(profile, monkeypatch, diagnostic):
    from core import companion_lifecycle as companion

    managed = profile.iris / "companion" / "app"
    managed.mkdir(parents=True)
    (managed / "uninstall.exe").write_bytes(b"MZ")
    monkeypatch.setattr(companion, "_controlled_install_dir", lambda: managed)
    monkeypatch.setattr(companion, "_installation_diagnostic", lambda: diagnostic(managed))
    monkeypatch.setattr(companion, "_stop_companion", lambda: ("stopped", ""))
    monkeypatch.setattr(companion, "_startup_path", lambda: None)
    monkeypatch.setattr(upd, "processes_of", lambda *a, **k: [])
    monkeypatch.setattr(upd, "_wait_gone", lambda processes, timeout: [])
    runs = []
    monkeypatch.setattr(installer, "_run",
                        lambda command, **kw: runs.append(command[1:]) or SimpleNamespace(returncode=0))
    return managed, runs


def test_a_damaged_managed_companion_is_still_removed(profile, monkeypatch):
    from core import companion_lifecycle as companion

    managed, runs = _managed_companion(profile, monkeypatch, lambda managed: {
        "installed": False, "state": "damaged", "_executable": None, "_repair_directory": managed})
    record = companion._install_record_path()
    record.write_text("{}", "utf-8")
    report = installer.remove_companion()
    assert report["removed"] and report["state"] == "damaged" and not managed.exists() and not record.exists()
    assert runs == [["/S", f"_?={managed}"]] and "warning" not in report


def test_a_companion_folder_that_is_a_link_is_left_alone(profile, monkeypatch, tmp_path):
    from core import companion_lifecycle as companion

    elsewhere = tmp_path / "otro companion"
    elsewhere.mkdir()
    (elsewhere / "datos.txt").write_text("ajeno", "utf-8")
    managed = profile.iris / "companion" / "app"
    managed.parent.mkdir(parents=True)
    _junction(managed, elsewhere)
    monkeypatch.setattr(companion, "_controlled_install_dir", lambda: managed)
    monkeypatch.setattr(companion, "_installation_diagnostic", lambda: {"installed": False})
    try:
        report = installer.remove_companion()
        assert "enlace" in report["warning"] and (elsewhere / "datos.txt").exists()
    finally:
        _unjunction(managed)


def test_close_never_runs_programs_reached_through_a_link(installed, monkeypatch):
    install = installed.install
    outside = installed.tmp / "fuera de la instalacion"
    (outside / "runtimes/iris").mkdir(parents=True)
    (outside / "runtimes/iris/python.exe").write_bytes(b"never launched")
    linked_version = install.versions / "deadbeef"
    _junction(linked_version, outside)
    # A real version whose runtime folder is a link elsewhere is not intact either.
    tampered = make_version(installed.tmp / "tampered", iris="1.0.1", revision="c" * 40)
    tampered_id = inst.verify_version(tampered)["id"]
    inst._copy_listed(tampered, install.folder(tampered_id))
    runtime = install.folder(tampered_id) / "runtimes" / "iris"
    shutil.rmtree(runtime)
    _junction(runtime, outside / "runtimes" / "iris")
    attempted = []
    monkeypatch.setattr(upd, "processes_of", lambda *a, **k: [])
    monkeypatch.setattr(upd, "_wait_gone", lambda processes, timeout: [])
    monkeypatch.setattr(upd, "_stop_version_services", lambda install, version: attempted.append(version))
    try:
        assert linked_version not in installer.version_folders(install)
        closed = installer.close_installation(install)
        assert attempted == [installed.active] and closed["not_run"] == [tampered_id]
    finally:
        _unjunction(runtime)
        _unjunction(linked_version)


def test_uninstall_unlinks_every_link_so_the_folder_delete_cannot_leave_it(installed, tmp_path):
    install = installed.install
    outside = tmp_path / "datos ajenos"
    outside.mkdir()
    (outside / "tesis.docx").write_bytes(b"no borrar")
    _junction(install.root / "updates" / "enlace", outside)
    _junction(install.versions / "deadbeef", outside)
    removed = installer.unlink_links(install.root)
    assert sorted(removed) == ["updates/enlace", "v/deadbeef"]
    assert (outside / "tesis.docx").exists() and not os.path.lexists(install.versions / "deadbeef")


def test_set_aside_extractions_are_removed_without_following_links(installed, quiet, tmp_path):
    install = installed.install
    outside = tmp_path / "datos ajenos"
    outside.mkdir()
    (outside / "tesis.docx").write_bytes(b"no borrar")
    aside = install.versions / ("~" + installed.active[1:])  # what NSIS set aside could be a link
    _junction(aside, outside)
    staging = make_version(installed.tmp / "same", channel=channel_doc(installed.entry))
    staged = _stage(install.root, staging)
    action, result = installer.plan(install.root, staged)
    assert action == "repair-keep" and result["cleaned"] == [aside.name]
    assert (outside / "tesis.docx").exists() and not os.path.lexists(aside)


def test_an_interrupted_repair_resumes_from_either_rename(installed, quiet):
    install, active = installed.install, installed.active
    target, backup = install.folder(active), install.versions / ("-" + active[1:])
    staging = make_version(installed.tmp / "same", channel=channel_doc(installed.entry))
    # 1. Killed after target -> backup: the target is missing, the backup (a damaged copy) remains.
    (target / "LEEME.txt").write_bytes(b"damaged")
    target.rename(backup)
    staged = _stage(install.root, staging)
    assert installer.plan(install.root, staged)[0] == "repair-replace"
    assert not backup.exists()  # plan cleared it: NSIS's first rename has nothing to collide with
    staged.rename(target)  # NSIS: no target to move aside, so only staged -> target
    assert installer.commit(install.root, active, "repair")["result"] == "repaired"
    inst.verify_version(target)
    # 2. Killed after staged -> target, before the replaced copy was removed.
    shutil.copytree(target, backup)
    staged = _stage(install.root, staging)
    assert installer.plan(install.root, staged)[0] == "repair-keep" and not backup.exists()
    shutil.copytree(target, backup)
    shutil.rmtree(staged)
    installer.commit(install.root, active, "repair")
    assert not backup.exists()  # commit removes the replaced copy once the version is verified


class _FakeRegistry:
    HKEY_CURRENT_USER, KEY_READ, KEY_SET_VALUE, REG_SZ = "HKCU", 1, 2, 1

    def __init__(self, values):
        self.values = values

    def OpenKey(self, hive, path, reserved=0, access=0):
        if self.values is None or path != inst.WINDOWS_ENTRY:
            raise OSError("no key")
        return contextlib.nullcontext(self)

    def QueryValueEx(self, key, name):
        return self.values[name], 1

    def SetValueEx(self, key, name, reserved, kind, value):
        self.values[name] = value


def test_installed_apps_shows_the_version_that_actually_runs(installed):
    install = installed.install
    registry = _FakeRegistry({"InstallLocation": str(install.root), "DisplayVersion": "9.9.9"})
    assert inst.sync_windows_entry(install, registry) == "1.0.0" and registry.values["DisplayVersion"] == "1.0.0"
    other = _FakeRegistry({"InstallLocation": str(installed.tmp / "otra"), "DisplayVersion": "9.9.9"})
    assert inst.sync_windows_entry(install, other) == "other" and other.values["DisplayVersion"] == "9.9.9"
    assert inst.sync_windows_entry(install, _FakeRegistry(None)) == "absent"


def test_confirmation_revert_and_fallback_all_refresh_installed_apps(installed, monkeypatch):
    install, active = installed.install, installed.active
    synced = []
    monkeypatch.setattr(inst, "sync_windows_entry", lambda install, registry=None: synced.append("sync") or "x")
    state = inst.read_state(install)
    state["confirmed"] = False
    inst.write_state(install, state)
    assert inst.confirm_active(install, active) and synced == ["sync"]
    # A rollback or failed activation going back: the helper's revert refreshes it too.
    target = make_version(installed.tmp / "new", iris="1.0.1", revision="c" * 40, channel=channel_doc(installed.entry))
    target_id = inst.verify_version(target)["id"]
    inst._copy_listed(target, install.folder(target_id))
    state = inst.read_state(install)
    state["versions"][target_id] = inst.version_record(inst.verify_version(install.folder(target_id)), "update")
    state.update(active=target_id, previous=active, confirmed=False)
    state["pending"] = {"operation": "upgrade", "from": active, "to": target_id, "phase": "switched",
                        "started_at": inst.now(), "helper": None, "backup": None, "previous_before": None}
    inst.write_state(install, state)
    for name in ("_close_desktop", "_stop_version_services", "_launch"):
        monkeypatch.setattr(upd, name, lambda *a, **k: None)
    monkeypatch.setattr(upd, "processes_of", lambda *a, **k: [])
    monkeypatch.setattr(upd, "_wait_gone", lambda processes, timeout: [])
    assert upd._revert(install, active, target_id, "no arrancó") and synced == ["sync", "sync"]
    # The launcher's fallback after failed trial launches refreshes it as well.
    state = inst.read_state(install)
    state.update(active=target_id, previous=active, confirmed=False, pending=None)
    inst.write_state(install, state)
    install.trial_path.write_text(json.dumps({"target": target_id, "launches": inst.TRIAL_LIMIT + 1}), "utf-8")
    assert inst.startup_check(install, active) is None and synced == ["sync", "sync", "sync"]


# ── Codex qa4 reverification (2026-10-07): the Companion cleanup preflight ──

def _external_companion(tmp_path: Path) -> Path:
    outside = tmp_path / "companion ajeno" / "app"
    outside.mkdir(parents=True)
    (outside / "uninstall.exe").write_bytes(b"MZ external")
    (outside / "marcador.txt").write_text("no borrar", "utf-8")
    return outside


def _recorder(monkeypatch):
    from core import companion_lifecycle as companion

    runs = []
    monkeypatch.setattr(installer, "_run", lambda command, **kw: runs.append(command) or SimpleNamespace(returncode=0))
    monkeypatch.setattr(companion, "_stop_companion", lambda: runs.append("stop") or ("stopped", ""))
    monkeypatch.setattr(companion, "_startup_path", lambda: None)
    monkeypatch.setattr(companion, "_installation_diagnostic", lambda: {"installed": False, "state": "damaged"})
    monkeypatch.setattr(upd, "processes_of", lambda *a, **k: runs.append("processes") or [])
    monkeypatch.setattr(upd, "_wait_gone", lambda processes, timeout: [])
    return runs


def test_a_link_on_the_companion_parent_never_reaches_outside(profile, monkeypatch, tmp_path):
    outside = _external_companion(tmp_path)
    parent = profile.iris / "companion"
    _junction(parent, outside.parent)
    runs = _recorder(monkeypatch)
    try:
        report = installer.remove_companion()
        assert "enlace" in report["warning"] and runs == []
        assert (outside / "marcador.txt").read_text("utf-8") == "no borrar" and (outside / "uninstall.exe").exists()
    finally:
        _unjunction(parent)


def test_a_linked_companion_program_is_never_run(profile, monkeypatch, tmp_path):
    outside = _external_companion(tmp_path)
    managed = profile.iris / "companion" / "app"
    managed.mkdir(parents=True)
    try:
        os.symlink(outside / "uninstall.exe", managed / "uninstall.exe")
    except OSError:
        pytest.skip("file symlinks need Developer Mode or elevation here")
    runs = _recorder(monkeypatch)
    report = installer.remove_companion()
    assert "enlace" in report["warning"] and runs == [] and (outside / "uninstall.exe").exists()


def test_links_inside_the_companion_folder_are_removed_before_its_uninstaller(profile, monkeypatch, tmp_path):
    outside = _external_companion(tmp_path)
    managed = profile.iris / "companion" / "app"
    managed.mkdir(parents=True)
    (managed / "uninstall.exe").write_bytes(b"MZ")
    _junction(managed / "_up_", outside)  # the Companion's NSIS uninstaller would follow it
    runs = _recorder(monkeypatch)
    report = installer.remove_companion()
    assert report["links_removed"] == ["_up_"] and report["removed"]
    assert (outside / "marcador.txt").read_text("utf-8") == "no borrar"
    assert any(isinstance(run, list) and str(run[0]).endswith("uninstall.exe") for run in runs)


@pytest.mark.parametrize("where", ["equal", "inside", "alias"])
@pytest.mark.parametrize("delete_data", [False, True])
def test_the_companion_cleanup_never_deletes_hermes(installed, profile, monkeypatch, tmp_path, where, delete_data):
    managed = profile.iris / "companion" / "app"
    managed.mkdir(parents=True)
    (managed / "uninstall.exe").write_bytes(b"MZ")
    hermes = {"equal": managed, "inside": managed / "hermes", "alias": tmp_path / "alias de hermes"}[where]
    if where == "alias":
        (managed / "hermes").mkdir()
        _junction(hermes, managed / "hermes")
        data = managed / "hermes"
    else:
        hermes.mkdir(exist_ok=True)
        data = hermes
    (data / ".env").write_text("TEST_ONLY=hermes", "utf-8")
    monkeypatch.setenv("HERMES_HOME", str(hermes))
    _profile_format(profile.iris)
    runs = _recorder(monkeypatch)
    monkeypatch.setattr(installer, "close_installation", lambda install, timeout=30: {})
    try:
        report = installer.uninstall(installed.install.root, delete_data=delete_data)
        assert "Hermes" in report["companion"]["warning"] and [r for r in runs if r != "processes"] == []
        assert (data / ".env").read_text("utf-8") == "TEST_ONLY=hermes" and (managed / "uninstall.exe").exists()
        assert report["warnings"]  # shown by the uninstaller
    finally:
        _unjunction(hermes) if where == "alias" else None


@pytest.mark.parametrize("program", ["uninstall.exe", "iris-desktop-companion.exe"])
def test_a_companion_program_reported_as_a_link_stops_the_cleanup(profile, monkeypatch, program):
    """Same rule as the real symlink test, without needing the privilege to create one."""
    managed = profile.iris / "companion" / "app"
    managed.mkdir(parents=True)
    (managed / program).write_bytes(b"MZ")
    real_is_link = installer.is_link
    monkeypatch.setattr(installer, "is_link", lambda path: Path(path).name == program or real_is_link(path))
    runs = _recorder(monkeypatch)
    report = installer.remove_companion()
    assert program in report["warning"] and runs == [] and (managed / program).exists()


# ── Codex qa10 closure, C3: closing the Companion before an update ─────────

def _companion_program(folder: Path) -> Path:
    """A real x64 program standing in for the Companion (the base interpreter)."""
    import sys

    folder.mkdir(parents=True, exist_ok=True)
    program = folder / "iris-desktop-companion.exe"
    shutil.copyfile(Path(sys.base_prefix) / "python.exe", program)
    return program


def _record_companion(executable: Path, program: Path) -> None:
    """The record Iris writes after installing the Companion, with a valid hash and size."""
    import hashlib

    from core import companion_lifecycle as companion

    companion._install_record_path().write_text(json.dumps({
        "schema": 1, "product": "IRIS Desktop Companion", "version": "0.1.3", "executable_arch": "x64",
        "executable": str(executable), "executable_sha256": hashlib.sha256(program.read_bytes()).hexdigest(),
        "executable_size": program.stat().st_size,
    }), "utf-8")


@pytest.fixture
def companion_effects(profile, monkeypatch):
    """The real diagnostic with every process effect intercepted: the program looks open,
    nothing real is started or stopped, and preference writes are recorded."""
    import psutil

    from core import companion_lifecycle as companion

    effects = SimpleNamespace(launched=[], saved=[], diagnosed=0, running=[])
    monkeypatch.setattr(companion.platform, "system", lambda: "Windows")
    monkeypatch.setattr(companion.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(companion, "_registry_installation", lambda: (None, "", ""))
    monkeypatch.setattr(companion, "_registry_residue", lambda: {})
    monkeypatch.setattr(companion, "_process_state", lambda candidate: (bool(candidate), True))
    monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: [
        SimpleNamespace(info={"exe": str(path)}, pid=999_999) for path in effects.running])
    monkeypatch.setattr(companion, "_wait_for_companion_processes", lambda processes, timeout: (processes, []))
    real_popen = companion.subprocess.Popen  # the module is shared: mklink still runs

    def popen(argv, *args, **kwargs):
        if Path(str(argv[0])).name.casefold() in {name.casefold() for name in companion._EXECUTABLE_NAMES}:
            return effects.launched.append([str(part) for part in argv])
        return real_popen(argv, *args, **kwargs)

    monkeypatch.setattr(companion.subprocess, "Popen", popen)
    monkeypatch.setattr(companion, "_save_companion_flags", lambda **kwargs: effects.saved.append(kwargs))
    real_diagnostic = companion._installation_diagnostic

    def diagnostic():
        effects.diagnosed += 1
        return real_diagnostic()

    monkeypatch.setattr(companion, "_installation_diagnostic", diagnostic)
    return effects


@pytest.mark.parametrize("linked", ["companion", "app"])
def test_closing_for_an_update_refuses_a_companion_folder_reached_through_a_link(
        profile, companion_effects, tmp_path, linked):
    # The diagnostic resolved the folder and its program together, so a copy outside
    # the profile looked managed and was started with --iris-exit (Codex qa10 closure, C3).
    from core import companion_lifecycle as companion

    outside = tmp_path / "copia fuera del perfil"
    program = _companion_program(outside / "app" if linked == "companion" else outside)
    link = profile.iris / "companion" if linked == "companion" else profile.iris / "companion" / "app"
    link.parent.mkdir(parents=True, exist_ok=True)
    _junction(link, outside)
    managed = profile.iris / "companion" / "app"
    _record_companion(managed / program.name, program)
    companion_effects.running.append(program.resolve())
    try:
        with pytest.raises(RuntimeError, match="enlace"):
            companion.close_companion_for_update()
        assert companion_effects.launched == [] and companion_effects.saved == []
        assert companion_effects.diagnosed == 0  # refused before the diagnostic
        # The same folder still passes the diagnostic: only the new check stops it.
        assert companion._installation_diagnostic()["source"] == "managed"
        assert program.exists()
    finally:
        _unjunction(link)


def test_closing_for_an_update_refuses_a_companion_program_that_is_a_link(profile, companion_effects, tmp_path):
    from core import companion_lifecycle as companion

    outside = _companion_program(tmp_path / "copia fuera del perfil")
    managed = profile.iris / "companion" / "app"
    managed.mkdir(parents=True)
    program = managed / outside.name
    try:
        os.symlink(outside, program)
    except OSError as exc:  # file symlinks need a privilege on Windows
        pytest.skip(f"no file symlinks here: {exc}")
    _record_companion(program, outside)
    companion_effects.running.append(outside.resolve())
    try:
        with pytest.raises(RuntimeError, match="enlace"):
            companion.close_companion_for_update()
        assert companion_effects.launched == [] and companion_effects.saved == []
    finally:
        program.unlink()


def test_closing_for_an_update_still_closes_the_companion_iris_installed(profile, companion_effects):
    from core import companion_lifecycle as companion

    program = _companion_program(profile.iris / "companion" / "app")
    _record_companion(program, program)
    companion_effects.running.append(program.resolve())
    assert companion.close_companion_for_update() is True
    assert companion_effects.launched == [[str(program.resolve()), "--iris-exit"]]
    assert companion_effects.saved == []  # the user's preferences are kept
    companion_effects.running.clear()
    companion_effects.launched.clear()
    assert companion.close_companion_for_update() is False  # nothing open: nothing to do
    assert companion_effects.launched == []


def test_closing_for_an_update_leaves_a_companion_installed_elsewhere(profile, companion_effects, tmp_path, monkeypatch):
    from core import companion_lifecycle as companion

    program = _companion_program(tmp_path / "instalado por su cuenta")
    monkeypatch.setattr(companion, "_registry_installation", lambda: (program, "0.1.3", "user"))
    companion_effects.running.append(program.resolve())
    assert companion._installation_diagnostic()["source"] == "registry"
    assert companion.close_companion_for_update() is False
    assert companion_effects.launched == [] and companion_effects.saved == []


def test_the_installer_reports_a_refused_companion_without_touching_it(
        installed, profile, companion_effects, monkeypatch, tmp_path):
    outside = tmp_path / "copia fuera del perfil"
    program = _companion_program(outside)
    managed = profile.iris / "companion" / "app"
    managed.parent.mkdir(parents=True)
    _junction(managed, outside)
    _record_companion(managed / program.name, program)
    companion_effects.running.append(program.resolve())
    monkeypatch.setattr(upd, "processes_of", lambda folder, profile_only=True, name=None: [])
    monkeypatch.setattr(upd, "_stop_version_services", lambda install, version: None)
    monkeypatch.setattr(upd, "_wait_gone", lambda processes, timeout: [])
    try:
        with pytest.raises(inst.InstallError, match="No se pudo cerrar el Companion: no se tocó porque .*enlace"):
            installer.close_installation(installed.install)
        assert installer.main(["close", "--root", str(installed.install.root)]) == installer.REFUSED
        assert companion_effects.launched == [] and program.exists()
    finally:
        _unjunction(managed)
