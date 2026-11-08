"""A08.9: the NSIS installer script and its build, checked without running NSIS."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pytest

from core import iris_installer
from scripts import build_installer, desktop_licenses
from scripts.distribution_assets import DistributionError
from tests.test_iris_updates import make_version

SCRIPT = build_installer.SCRIPT.read_text("utf-8")


def _section(name: str) -> str:
    start = SCRIPT.index(f'Section "{name}"')
    return SCRIPT[start:SCRIPT.index("SectionEnd", start)]


def test_every_plan_action_has_its_branch_and_mode_in_the_script():
    install = _section("${PRODUCT}")
    cases = {int(code): body for code, body in re.findall(r"\$\{Case\} (\d+)\n(.*?)\$\{Break\}", install, re.S)}
    assert set(cases) == set(iris_installer.ACTIONS.values())
    for action, code in iris_installer.ACTIONS.items():
        mode = action.split("-")[0]
        assert f'StrCpy $Mode "{mode}"' in cases[code], action
        # "keep" discards the fresh extraction; "replace" moves the damaged copy aside only if it
        # is still there (an interrupted run may have moved it) and leaves it to commit.
        assert ('RMDir /r "$INSTDIR\\v\\${STAGED}"' in cases[code]) == action.endswith("-keep"), action
        swap = '${If} ${FileExists} "$R2"\n        !insertmacro RenameRetry "$R2" "$R3"'
        assert (swap in cases[code]) == action.endswith("-replace"), action
        assert "$R3" not in cases[code].replace(swap, ""), action
        assert ("RenameRetry \"$R2\" \"$R3\"" in cases[code]) == action.endswith("-replace"), action
    assert "${Default}" in install and "SetErrorLevel 4" in install


def test_the_script_only_calls_commands_core_iris_installer_has():
    used = set(re.findall(r"StrCpy \$R1 '?\"?(plan|commit|close|uninstall|profile)\b", SCRIPT))
    parser_commands = {"plan", "commit", "close", "uninstall", "profile"}
    assert used and used <= parser_commands
    assert "--mode $Mode" in SCRIPT and "--delete-profile" in SCRIPT
    flags = re.search(r'!define PY_FLAGS "([^"]+)"', SCRIPT).group(1)
    assert flags.split()[:6] == ["-E", "-s", "-B", "-P", "-X", "utf8"]  # no bytecode inside the installation


def test_per_user_installer_never_downloads_and_writes_only_the_user_hive():
    assert "RequestExecutionLevel user" in SCRIPT
    assert not re.search(r"WriteReg\w+ HKLM|DeleteRegKey HKLM", SCRIPT)
    assert not re.search(r"NSISdl|inetc|download", SCRIPT, re.I)
    # WebView2 is detected in both registry views and for the user; missing means stop and explain.
    assert SCRIPT.count('"pv"') >= 4 and "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" in SCRIPT
    assert 'ExecShell "open" "${WEBVIEW2_PAGE}"' in SCRIPT and "SetErrorLevel 3" in SCRIPT


def test_shortcuts_metadata_and_data_choice():
    assert '!define PUBLISHER "Iris OS"' in SCRIPT and '"Publisher" "${PUBLISHER}"' in SCRIPT
    install = _section("${PRODUCT}")
    assert 'CreateShortcut "$SMPROGRAMS\\${PRODUCT}.lnk"' in install
    assert re.search(r'\$\{If\} \$DesktopShortcut == \$\{BST_CHECKED\}\s+CreateShortcut "\$DESKTOP', install)
    assert "${NSD_SetState} $DeleteCheck ${BST_UNCHECKED}" in SCRIPT  # deleting data is opt-in
    assert "MB_DEFBUTTON2" in SCRIPT  # and confirmed, defaulting to "no"
    assert "beta sin firma" in SCRIPT and "Comments\" \"Beta sin firma digital" in SCRIPT
    uninstall = _section("Uninstall")
    assert uninstall.index("Call un.RunVersionPython") < uninstall.index('RMDir /r "$INSTDIR"')


def test_installer_name_says_it_is_unsigned():
    assert build_installer.installer_name("0.9.2") == "IrisOS-0.9.2-beta-x64-sin-firma-setup.exe"


def test_defines_come_from_the_version_manifest(tmp_path):
    staging = make_version(tmp_path / "staging", iris="0.9.2")
    values = build_installer.defines(staging, tmp_path / "out")
    version_id = values["VERSION_ID"]
    assert values["VIVERSION"] == "0.9.2.0" and len(version_id) == 8
    assert values["STAGED"] == "_" + version_id[1:] and values["STAGED_TAIL"] == version_id[1:]
    assert values["MAX_ROOT"] == str(198 - len("\\v\\00000000"))
    assert values["OUTFILE"].endswith("IrisOS-0.9.2-beta-x64-sin-firma-setup.exe")
    with pytest.raises(DistributionError):
        build_installer.defines(make_version(tmp_path / "beta", iris="0.9.2b1"), tmp_path / "out")


def test_nsis_must_be_the_reviewed_toolchain(tmp_path, monkeypatch):
    nsis = tmp_path / "NSIS"
    (nsis / "Bin").mkdir(parents=True)
    (nsis / "makensis.exe").write_bytes(b"makensis 3.11")
    tree = desktop_licenses.tree_digest(nsis)
    reviewed = {"installer": {"NSIS==3.11": {"tool": {"tree_sha256": tree[0], "files": tree[1]}}}}
    monkeypatch.setattr(build_installer, "load_reviewed", lambda repository: reviewed)
    assert build_installer.check_nsis(nsis) == tree
    (nsis / "Bin/zlib1.dll").write_bytes(b"extra")
    with pytest.raises(DistributionError, match="reviewed toolchain"):
        build_installer.check_nsis(nsis)


@pytest.fixture
def staged(tmp_path, monkeypatch):
    staging = make_version(tmp_path / "staging", iris="0.9.2")
    manifest = json.loads((staging / "iris-staging.json").read_text("utf-8"))
    manifest["companion"] = {"version": "0.1.3", "sha256": "e" * 64, "webview2_mode": "skip"}
    (staging / "iris-staging.json").write_text(json.dumps(manifest), "utf-8")
    monkeypatch.setattr(build_installer, "verify_staging", lambda root, expected_pin=None: {"licenses_pending": []})
    monkeypatch.setattr(build_installer, "check_nsis", lambda nsis_dir: ("f" * 64, 442))
    monkeypatch.setattr(build_installer, "authenticode_status", lambda path: "NotSigned")
    # Synthetic versions carry no runtime; test_installer_nsis_selection checks the real rule.
    monkeypatch.setattr(build_installer, "check_search_path", lambda root: None)
    return staging


def test_build_stops_before_nsis_when_the_runtime_search_path_is_not_the_shipped_one(staged, tmp_path, monkeypatch):
    def refuse(root):
        raise DistributionError("The Iris runtime's python311._pth is not the shipped search path")

    monkeypatch.setattr(build_installer, "check_search_path", refuse)
    monkeypatch.setattr(build_installer.subprocess, "run", lambda *a, **k: pytest.fail("makensis must not run"))
    with pytest.raises(DistributionError, match="search path"):
        build_installer.build_installer(staged, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_build_records_hashes_and_refuses_to_overwrite(staged, tmp_path, monkeypatch):
    calls = []

    def makensis(command, **kwargs):
        calls.append(command)
        out = next(arg for arg in command if arg.startswith("/DOUTFILE=")).split("=", 1)[1]
        Path(out).write_bytes(b"MZ installer")
        return argparse.Namespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(build_installer.subprocess, "run", makensis)
    document = build_installer.build_installer(staged, tmp_path / "out")
    assert document["signed"] is False and document["authenticode"] == "NotSigned"
    assert document["installer"]["file"] == "IrisOS-0.9.2-beta-x64-sin-firma-setup.exe"
    assert document["companion"]["version"] == "0.1.3" and document["webview2"] == "detect-and-inform"
    assert calls[0][1:4] == ["/V2", "/INPUTCHARSET", "UTF8"]
    with pytest.raises(DistributionError, match="overwrite"):
        build_installer.build_installer(staged, tmp_path / "out")


def test_build_refuses_pending_notices_or_a_version_without_companion(staged, tmp_path, monkeypatch):
    monkeypatch.setattr(build_installer, "verify_staging",
                        lambda root, expected_pin=None: {"licenses_pending": ["webview2-com-sys"]})
    with pytest.raises(DistributionError, match="pending"):
        build_installer.build_installer(staged, tmp_path / "out")
    monkeypatch.setattr(build_installer, "verify_staging", lambda root, expected_pin=None: {"licenses_pending": []})
    manifest = json.loads((staged / "iris-staging.json").read_text("utf-8"))
    manifest.pop("companion")
    (staged / "iris-staging.json").write_text(json.dumps(manifest), "utf-8")
    with pytest.raises(DistributionError, match="Companion"):
        build_installer.build_installer(staged, tmp_path / "out")


def test_the_recorded_script_hash_does_not_depend_on_the_checkout():
    """Every installer records the script's SHA-256; .gitattributes keeps its LF bytes everywhere."""
    assert b"\r" not in build_installer.SCRIPT.read_bytes()
    rules = (build_installer.PROJECT_ROOT / ".gitattributes").read_text("utf-8").splitlines()
    assert "*.nsi text eol=lf" in rules


def test_recursive_deletes_never_reach_a_folder_the_installer_did_not_just_create():
    """NSIS RMDir /r follows junctions (checked with NSIS 3.11)."""
    deletes = re.findall(r'RMDir /r "([^"]+)"', SCRIPT)
    assert set(deletes) == {"$INSTDIR\\v\\${STAGED}", "$INSTDIR"}
    install = _section("${PRODUCT}")
    # A previous extraction is set aside with Rename before the new one is written.
    assert install.index("Call SetAsideStaged") < install.index("File /r")
    assert install.index('StrCpy $R9 "$INSTDIR\\v"') < install.index("Call SetAsideStaged")
    uninstall = _section("Uninstall")
    python_branch, no_python_branch = uninstall.split("${Else}", 1)[0], uninstall.split("${Else}", 1)[1]
    assert 'RMDir /r "$INSTDIR"' in python_branch and "RMDir" not in no_python_branch
    assert "${If} $0 = 2" in uninstall  # warnings are shown, then the uninstall finishes


def test_the_uninstaller_only_runs_a_python_without_links_on_its_path():
    # One macro defines FindVersionPython for the uninstaller and, since qa10, the
    # installer (it closes Iris before copying); each side gets its own prefix.
    find = SCRIPT[SCRIPT.index("Function ${PREFIX}FindVersionPython"):SCRIPT.index("FunctionEnd", SCRIPT.index(
        "Function ${PREFIX}FindVersionPython"))]
    assert '!insertmacro DEFINE_VERSION_PYTHON "un."' in SCRIPT
    # Each candidate goes through SafeVersion; test_installer_nsis_selection runs it with real junctions.
    assert find.count("Call ${PREFIX}IsLink") == 1 and find.count("Call ${PREFIX}SafeVersion") == 1
    assert "${If} $R7 = 0" in find and "StrLen $6 $1" in find
    for prefix in "_-~!=+":
        assert f'${{AndIf}} $5 != "{prefix}"' in find
    assert "GetFileAttributesW(w r19) i .r18" in SCRIPT and "IntOp $R8 $R8 & 0x400" in SCRIPT


def test_an_upgrade_leaves_the_shown_version_to_the_confirmation():
    install = _section("${PRODUCT}")
    assert re.search(r'\$\{If\} \$Mode != "upgrade"\s+;[^\n]*\n[^\n]*\n\s+WriteRegStr HKCU "\$\{UNINST_KEY\}" '
                     r'"DisplayVersion"', install)
