"""A08.9: what the Windows installer delegates to an Iris version's own Python."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import iris_install as inst
from core import iris_installer as installer
from core import iris_updates as upd
from tests.test_iris_updates import channel_doc, installed, keypair, make_version, profile  # noqa: F401


def _stage(root: Path, staging: Path) -> Path:
    """What NSIS does: extract the version into ``v/_xxxxxxx`` of the installation."""
    info = inst.verify_version(staging)
    target = root / "v" / ("_" + info["id"][1:])
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(staging, target)
    return target


def _place(staged: Path) -> str:
    """NSIS renames the extracted folder to its final name."""
    version = inst.verify_version(staged)["id"]
    staged.rename(staged.parent / version)
    return version


@pytest.fixture
def quiet(monkeypatch):
    """No real processes: record what would be closed or started."""
    calls = []
    monkeypatch.setattr(installer, "close_installation", lambda install, timeout=30: calls.append("close") or {})
    monkeypatch.setattr(installer, "spawn_activation",
                        lambda install, own, target: calls.append(("activate", own, target)) or 4242)
    return calls


def test_fresh_install_lays_out_the_versioned_installation(tmp_path, quiet):
    root = tmp_path / "Iris OS"
    staged = _stage(root, make_version(tmp_path / "staging"))
    action, result = installer.plan(root, staged)
    assert action == "install" and result["iris"] == "1.0.0"
    version = _place(staged)
    done = installer.commit(root, version, "install")
    install = inst.Install(root)
    state = inst.read_state(install)
    assert done["result"] == "installed" and state["active"] == version and state["confirmed"]
    assert state["versions"][version]["source"] == "installer"
    assert install.launcher.read_bytes() == (install.folder(version) / "Iris.exe").read_bytes()
    assert install.updates.is_dir() and quiet == []


def test_a_fresh_install_needs_an_empty_folder_but_clears_its_own_leftovers(tmp_path, quiet):
    root = tmp_path / "Iris OS"
    staged = _stage(root, make_version(tmp_path / "staging"))
    leftover = root / "v" / "0123abcd"
    leftover.mkdir()
    (leftover / "channel.json").write_bytes(b"{}")  # an interrupted extraction writes Iris files
    assert installer.plan(root, staged)[0] == "install" and not leftover.exists()
    (root / "Mis documentos.txt").write_text("ajeno", "utf-8")
    with pytest.raises(inst.InstallError, match="no está vacía"):
        installer.plan(root, staged)


def test_a_silent_install_never_clears_folders_without_iris_files(tmp_path, quiet):
    """The folder page (skipped by /S) is not the only guard: plan refuses too."""
    root = tmp_path / "carpeta ajena"
    staged = _stage(root, make_version(tmp_path / "staging"))
    foreign = root / "v" / "0123abcd"
    foreign.mkdir()
    (foreign / "tesis.docx").write_bytes(b"datos del usuario")
    with pytest.raises(inst.InstallError, match="no está vacía"):
        installer.plan(root, staged)
    assert (foreign / "tesis.docx").exists()


def test_plan_checks_the_extraction_folder_and_the_path_budget(tmp_path, quiet, monkeypatch):
    root = tmp_path / "Iris OS"
    staged = _stage(root, make_version(tmp_path / "staging"))
    wrong = staged.with_name("_wrong00")
    staged.rename(wrong)
    with pytest.raises(inst.InstallError, match="carpeta inesperada"):
        installer.plan(root, wrong)
    wrong.rename(staged)
    monkeypatch.setattr(inst, "fits", lambda root, longest: False)
    with pytest.raises(inst.InstallError, match="demasiado larga"):
        installer.plan(root, staged)


def test_plan_rejects_an_altered_extracted_version(tmp_path, quiet):
    root = tmp_path / "Iris OS"
    staged = _stage(root, make_version(tmp_path / "staging"))
    (staged / "runtimes/iris/python.exe").write_bytes(b"altered")
    with pytest.raises(inst.InstallError):
        installer.plan(root, staged)


def test_reinstalling_the_same_version_repairs_only_what_is_damaged(installed, quiet):
    install, active = installed.install, installed.active
    staging = make_version(installed.tmp / "same", channel=channel_doc(installed.entry))
    staged = _stage(install.root, staging)
    assert installer.plan(install.root, staged)[0] == "repair-keep" and quiet == []
    shutil.rmtree(staged)
    install.launcher.write_bytes(b"stale launcher")
    assert installer.commit(install.root, active, "repair")["result"] == "repaired"
    assert install.launcher.read_bytes() == (install.folder(active) / "Iris.exe").read_bytes()

    (install.folder(active) / "runtimes/iris/python.exe").write_bytes(b"damaged")
    staged = _stage(install.root, staging)
    assert installer.plan(install.root, staged)[0] == "repair-replace"
    assert quiet == ["close"]  # Iris is closed before NSIS swaps the folders


def test_a_newer_version_is_handed_to_the_running_version_for_activation(installed, quiet):
    install, active = installed.install, installed.active
    staged = _stage(install.root, make_version(installed.tmp / "new", iris="1.0.1", revision="c" * 40,
                                               channel=channel_doc(installed.entry)))
    action, result = installer.plan(install.root, staged)
    assert action == "upgrade" and result["previous"] == "1.0.0"
    version = _place(staged)
    done = installer.commit(install.root, version, "upgrade")
    state = inst.read_state(install)
    assert done == {"result": "upgrading", "from": active, "to": version, "iris": "1.0.1", "helper": 4242}
    assert state["active"] == active and state["pending"]["to"] == version  # the helper switches
    assert state["versions"][version]["source"] == "installer"
    assert quiet == [("activate", active, version)]


def test_upgrade_reuses_or_replaces_a_version_folder_that_is_already_there(installed, quiet):
    install = installed.install
    staging = make_version(installed.tmp / "new", iris="1.0.1", revision="c" * 40, channel=channel_doc(installed.entry))
    version = inst.verify_version(staging)["id"]
    inst._copy_listed(staging, install.folder(version))
    staged = _stage(install.root, staging)
    assert installer.plan(install.root, staged)[0] == "upgrade-keep"
    (install.folder(version) / "LEEME.txt").write_bytes(b"damaged")
    assert installer.plan(install.root, staged)[0] == "upgrade-replace"


@pytest.mark.parametrize("case", ["older", "damaged-active", "pending"])
def test_upgrade_refusals_change_nothing(installed, quiet, case):
    install, active = installed.install, installed.active
    iris = "0.9.0" if case == "older" else "1.0.1"
    staged = _stage(install.root, make_version(installed.tmp / "other", iris=iris, revision="d" * 40,
                                               channel=channel_doc(installed.entry)))
    if case == "damaged-active":
        (install.folder(active) / "runtimes/iris/python.exe").write_bytes(b"damaged")
    if case == "pending":
        state = inst.read_state(install)
        state["pending"] = {"operation": "upgrade", "from": active, "to": "fedcba98", "phase": "starting",
                            "started_at": inst.now(), "helper": None, "backup": None, "previous_before": None}
        inst.write_state(install, state)
    before = inst.read_state(install)
    with pytest.raises(inst.InstallError, match={"older": "más nueva", "damaged-active": "dañada",
                                                  "pending": "en curso"}[case]):
        installer.plan(install.root, staged)
    assert inst.read_state(install) == before and quiet == []


def test_command_line_reports_the_action_as_exit_code_and_errors_as_json(tmp_path, quiet, capsys):
    root = tmp_path / "Iris OS"
    staged = _stage(root, make_version(tmp_path / "staging"))
    assert installer.main(["plan", "--root", str(root), "--staged", str(staged)]) == installer.ACTIONS["install"]
    assert json.loads(capsys.readouterr().out)["action"] == "install"
    (root / "ajeno.txt").write_text("x", "utf-8")
    assert installer.main(["plan", "--root", str(root), "--staged", str(staged)]) == installer.REFUSED
    assert "no está vacía" in capsys.readouterr().out  # plain text, shown as is by the installer


def test_close_installation_asks_each_version_then_ends_its_leftovers(installed, monkeypatch):
    install, active = installed.install, installed.active
    calls = []

    class Proc(SimpleNamespace):
        def terminate(self):
            calls.append(("terminate", self.pid))

    window, leftover = Proc(pid=1, info={"name": "Iris.exe"}), Proc(pid=2, info={"name": "python.exe"})
    monkeypatch.setattr(upd, "processes_of", lambda folder, profile_only=True, name=None:
                        [window] if name else [leftover])
    def run(command, **kw):
        if str(command[0]).endswith("Iris.exe"):
            calls.append(("run", command[1:]))
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(installer, "_run", run)
    monkeypatch.setattr(upd, "_stop_version_services", lambda install, version: calls.append(("stop", version)))
    gone = iter([[leftover], []])
    monkeypatch.setattr(upd, "_wait_gone", lambda processes, timeout: next(gone))
    import core.companion_lifecycle as companion

    monkeypatch.setattr(companion, "close_companion_for_update", lambda: calls.append(("companion",)) or True)
    closed = installer.close_installation(install)
    assert calls == [("run", ["--iris-exit"]), ("stop", active), ("terminate", 2), ("companion",)]
    assert closed == {"windows": [active], "services": [active], "terminated": ["python.exe"], "not_run": [],
                      "companion": True}
    assert installer.closed_anything(closed)
    monkeypatch.setattr(upd, "_wait_gone", lambda processes, timeout: [leftover])
    with pytest.raises(inst.InstallError, match="sigue en uso"):
        installer.close_installation(install)


def test_close_reports_whether_it_closed_anything_and_refuses_a_companion_it_cannot_close(installed, monkeypatch):
    # qa9: the installer closes Iris and the Companion before copying (exit 3 = it
    # closed something, so a failed install reopens Iris); nothing open is exit 0.
    import core.companion_lifecycle as companion

    monkeypatch.setattr(upd, "processes_of", lambda folder, profile_only=True, name=None: [])
    monkeypatch.setattr(upd, "_stop_version_services", lambda install, version: None)
    monkeypatch.setattr(upd, "_wait_gone", lambda processes, timeout: [])
    monkeypatch.setattr(companion, "close_companion_for_update", lambda: False)
    assert installer.main(["close", "--root", str(installed.install.root)]) == 0
    monkeypatch.setattr(companion, "close_companion_for_update", lambda: True)
    assert installer.main(["close", "--root", str(installed.install.root)]) == installer.CLOSED == 3

    def refuse():
        raise RuntimeError("Windows no permitió cerrar el proceso verificado del Companion.")

    monkeypatch.setattr(companion, "close_companion_for_update", refuse)
    with pytest.raises(inst.InstallError, match="No se pudo cerrar el Companion"):
        installer.close_installation(installed.install)


def _profile_format(home: Path) -> None:
    (home / ".profile").mkdir(exist_ok=True)
    (home / ".profile/format.json").write_text('{"format_version": 1}', "utf-8")


def test_deleting_data_removes_only_the_iris_profile(profile, monkeypatch):
    _profile_format(profile.iris)
    (profile.iris / "memoria.db").write_bytes(b"recuerdos")
    (profile.hermes / ".env").write_text("SECRET=hermes", "utf-8")
    result = installer.delete_profile()
    assert result["deleted"] and not profile.iris.exists()
    assert (profile.hermes / ".env").exists() and result["kept_hermes"] == str(profile.hermes)


def test_deleting_data_refuses_folders_that_are_not_an_idle_iris_profile(profile, monkeypatch):
    assert installer.delete_profile()["deleted"] is False  # no format marker
    _profile_format(profile.iris)
    import core.profile_paths as paths

    monkeypatch.setattr(paths, "_existing_compatible_writer_pids", lambda home: {1234})
    assert installer.delete_profile()["deleted"] is False and profile.iris.exists()


def test_the_companion_app_is_removed_only_from_the_folder_iris_manages(profile, monkeypatch, tmp_path):
    from core import companion_lifecycle as companion

    managed = profile.iris / "companion" / "app"
    managed.mkdir(parents=True)
    (managed / "uninstall.exe").write_bytes(b"MZ")
    exe = managed / "iris-desktop-companion.exe"
    exe.write_bytes(b"MZ")
    runs = []
    monkeypatch.setattr(companion, "_controlled_install_dir", lambda: managed)
    monkeypatch.setattr(companion, "_stop_companion", lambda: ("stopped", ""))
    monkeypatch.setattr(companion, "_set_autostart", lambda enabled: "off")
    def run(command, **kw):
        if str(command[0]).endswith("uninstall.exe"):
            runs.append(command[1:])
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(installer, "_run", run)
    monkeypatch.setattr(companion, "_installation_diagnostic", lambda: {"installed": True, "_executable": exe})
    report = installer.remove_companion()
    assert report["removed"] and runs == [["/S", f"_?={managed}"]] and not managed.exists()

    elsewhere = tmp_path / "otro" / "iris-desktop-companion.exe"
    elsewhere.parent.mkdir()
    elsewhere.write_bytes(b"MZ")
    monkeypatch.setattr(companion, "_installation_diagnostic", lambda: {"installed": True, "_executable": elsewhere})
    assert "kept" in installer.remove_companion() and elsewhere.exists()
    monkeypatch.setattr(companion, "_installation_diagnostic", lambda: {"installed": False})
    assert installer.remove_companion() == {"installed": False}


def test_processes_of_ignores_bare_system_names_resolved_inside_the_folder(tmp_path, monkeypatch):
    """psutil reports "Registry" for a system process; abspath would put it in the cwd."""
    folder = tmp_path / "Iris OS"
    (folder / "v").mkdir(parents=True)
    own = SimpleNamespace(pid=11, info={"pid": 11, "exe": str(folder / "v" / "Iris.exe"), "name": "Iris.exe"})
    system = SimpleNamespace(pid=224, info={"pid": 224, "exe": "Registry", "name": "Registry"})
    import psutil

    monkeypatch.setattr(psutil, "process_iter", lambda attrs: [own, system])
    monkeypatch.chdir(folder / "v")  # the installer's Python runs from inside the installation
    assert upd.processes_of(folder, profile_only=False) == [own]
