"""qa10: the installer closes Iris and the Companion before replacing a version.

Codex's qa10 review (R3): before copying, NSIS runs `close` with the code of the
version already installed. qa9's `close` always exits 0 (even after closing Iris)
and does not close the Companion. So:

* `RecordClose` recognises both contracts: exit code 3 (current) or, from qa9,
  exit 0 with a JSON that lists closed windows or terminated processes;
* after copying, the new version's own `close` runs before the check, so a
  transition from qa9 also closes the Companion.

The NSIS functions are compiled with the reviewed NSIS into a small probe that
only records the decision; nothing is closed.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from scripts import build_installer

NSIS = build_installer.DEFAULT_NSIS / "makensis.exe"
SCRIPT = build_installer.SCRIPT.read_text("utf-8")


def _block(start: str, end: str) -> str:
    begin = SCRIPT.index(start)
    return SCRIPT[begin:SCRIPT.index(end, begin) + len(end)]


def _qa9_output(**closed) -> str:
    """Exactly what qa9's `close` prints: json.dumps(close_installation(...), ensure_ascii=False)."""
    return json.dumps({"windows": [], "services": ["96906675"], "terminated": [], "not_run": [], **closed},
                      ensure_ascii=False)


CASES = {
    "current: closed something": ("3", json.dumps({"windows": [], "services": ["a"], "terminated": [], "not_run": [],
                                                   "companion": True}), "1"),
    "current: nothing open": ("0", json.dumps({"windows": [], "services": ["a"], "terminated": [], "not_run": [],
                                               "companion": False}), "0"),
    "qa9: closed the Iris window": ("0", _qa9_output(windows=["96906675"]), "1"),
    "qa9: ended leftover processes": ("0", _qa9_output(terminated=["python.exe"]), "1"),
    "qa9: nothing was open": ("0", _qa9_output(), "0"),
    "refused": ("1", "Un proceso de Iris sigue en uso (python.exe).", "0"),
    "could not start": ("error", "", "0"),
    "exit 0 without close output": ("0", "otra salida", "0"),
}


@pytest.mark.skipif(os.name != "nt" or not NSIS.is_file(), reason="needs Windows and the reviewed NSIS")
@pytest.mark.parametrize("name", list(CASES))
def test_the_installer_remembers_a_close_under_both_contracts(tmp_path, name):
    code, output, expected = CASES[name]
    assert "'" not in output
    probe = "\n".join((
        "Unicode true", f'OutFile "{tmp_path / "probe.exe"}"', "RequestExecutionLevel user", "SilentInstall silent",
        "!include LogicLib.nsh", "Var ClosedIris",
        _block("Function StrContains", "FunctionEnd"), _block("Function RecordClose", "FunctionEnd"),
        "Section", "  StrCpy $ClosedIris 0", f"  StrCpy $0 '{code}'", f"  StrCpy $1 '{output}'",
        "  Call RecordClose", '  FileOpen $9 "$EXEDIR\\closed.txt" w', '  FileWrite $9 "[$ClosedIris]"',
        "  FileClose $9", "SectionEnd"))
    (tmp_path / "probe.nsi").write_text(probe, "utf-8")
    built = subprocess.run([str(NSIS), "/V1", "/INPUTCHARSET", "UTF8", str(tmp_path / "probe.nsi")],
                           capture_output=True, text=True)
    assert built.returncode == 0, built.stdout + built.stderr
    subprocess.run([str(tmp_path / "probe.exe"), "/S"], check=True, timeout=60)
    raw = (tmp_path / "closed.txt").read_bytes()
    assert raw.decode("utf-16" if raw[:2] == b"\xff\xfe" else "utf-8").strip() == f"[{expected}]"


def test_the_new_version_closes_what_is_left_before_its_check():
    section = _block('Section "${PRODUCT}" SecMain', "SectionEnd")
    copy = section.index("File /r")
    check = section.index("'plan --root")
    first = section.index("'close --root")
    second = section.index("'close --root", first + 1)
    assert first < copy < second < check
    # Each close is remembered (so a stop without switching reopens Iris) and a
    # refused close leaves nothing behind.
    assert section.count("Call RecordClose") == 2
    after_copy = section[copy:check]
    assert 'StrCpy $R0 "$INSTDIR\\v\\${STAGED}"' in after_copy
    assert after_copy.index('RMDir /r "$INSTDIR\\v\\${STAGED}"') < after_copy.index("Call ReopenClosedIris")


def test_the_current_close_keeps_the_output_qa9_installers_read():
    # A qa10 installer reads qa9's output; a later installer reads this one's.
    from core import iris_installer

    source = Path(iris_installer.__file__).read_text("utf-8")
    assert "print(json.dumps(closed, ensure_ascii=False))" in source
    assert "return CLOSED if closed_anything(closed) else 0" in source
