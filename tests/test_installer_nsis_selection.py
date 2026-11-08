"""A08.9: which Python the NSIS uninstaller (and, since qa10, the installer) would run.

Checked with the real NSIS logic: the shared ``DEFINE_VERSION_PYTHON`` macro both
use is copied from desktop/installer/iris-os.nsi into a small probe
compiled with the reviewed NSIS; the probe only records the version folder it
would choose, it never runs a Python. Each case puts a real junction, a changed
search path or a shadowing ``core`` in one version folder: that folder must never
be chosen, a safe one must be, and with no safe folder none is (the uninstaller
then keeps the installation and says so). Skipped without NSIS or junctions.

What Python reads is the runtime's contract, measured with the real runtime:
python311._pth fixes sys.path, the environment and the current folder are ignored,
site reads the .pth files of runtimes/iris and Lib/site-packages, and pyvenv.cfg is
looked for next to python.exe and one folder up.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts import build_installer
from scripts.distribution_assets import DistributionError
from scripts.prepare_private_runtime import interpreter_path_file

NSIS = build_installer.DEFAULT_NSIS / "makensis.exe"
SCRIPT = build_installer.SCRIPT.read_text("utf-8")
SHIPPED_PTH = interpreter_path_file(hermes=False).encode("utf-8")
SAFE_FILES = ("runtimes/iris/python.exe", "runtimes/iris/python311.dll", "runtimes/iris/DLLs/_socket.pyd",
              "runtimes/iris/Lib/os.py", "runtimes/iris/Lib/encodings/__init__.py",
              "runtimes/iris/Lib/site-packages/core/__init__.py",
              "runtimes/iris/Lib/site-packages/core/iris_install.py",
              "runtimes/iris/Lib/site-packages/core/iris_installer.py")

pytestmark = pytest.mark.skipif(os.name != "nt" or not NSIS.is_file(), reason="needs Windows and the reviewed NSIS")


def _block(start: str, end: str) -> str:
    begin = SCRIPT.index(start)
    return SCRIPT[begin:SCRIPT.index(end, begin) + len(end)]


@pytest.fixture(scope="module")
def probe(tmp_path_factory):
    folder = tmp_path_factory.mktemp("nsis-probe")
    functions = "\n".join((
        _block("!define IRIS_PTH ", "\n"), _block("!define IRIS_PTH_BYTES ", "\n"),
        _block("!macro DEFINE_IS_LINK", "!macroend"),
        "!insertmacro DEFINE_IS_LINK \"\"",
        # The installer closes Iris with these checks before copying (qa10), the
        # uninstaller before deleting: one macro, instantiated for each side.
        _block("!macro DEFINE_VERSION_PYTHON", "!macroend"),
        "!insertmacro DEFINE_VERSION_PYTHON \"\"",
    ))
    newline = "$" + "\\r" + "$" + "\\n"
    script = "\n".join((
        "Unicode true", f'OutFile "{folder / "probe.exe"}"', "RequestExecutionLevel user", "SilentInstall silent",
        "!include LogicLib.nsh", "Var VersionPython", functions, "Section",
        "  Call FindVersionPython", '  FileOpen $9 "$INSTDIR\\selected.txt" w',
        f'  FileWrite $9 "[$VersionPython]{newline}"', "  FileClose $9", "SectionEnd"))
    (folder / "probe.nsi").write_text(script, "utf-8")
    result = subprocess.run([str(NSIS), "/V1", "/INPUTCHARSET", "UTF8", str(folder / "probe.nsi")],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    return folder / "probe.exe"


def _version(root: Path, name: str) -> Path:
    folder = root / "v" / name
    for relative in SAFE_FILES:
        (folder / relative).parent.mkdir(parents=True, exist_ok=True)
        (folder / relative).write_bytes(b"fixture, never run")
    (folder / "runtimes/iris/python311._pth").write_bytes(SHIPPED_PTH)  # the authentic contract
    return folder


def _junction(link: Path, target: Path) -> None:
    made = subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
    if made.returncode:
        pytest.skip("directory junctions are not available here")


def _selected(probe: Path, root: Path) -> str:
    # NSIS wants /D= last and unquoted, even with spaces: pass the command line as is.
    subprocess.run(f'"{probe}" /S /D={root}', check=True, timeout=120)
    raw = (root / "selected.txt").read_bytes()
    return re.search(r"\[(.*)\]", raw.decode("utf-16" if raw[:2] == b"\xff\xfe" else "utf-8")).group(1)


def _pair(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path / "Iris OS"
    return root, _version(root, "aaaaaaaa"), _version(root, "bbbbbbbb")


SEGMENTS = ["", "runtimes", "runtimes/iris", "runtimes/iris/DLLs", "runtimes/iris/Lib",
            "runtimes/iris/Lib/site-packages", "runtimes/iris/Lib/site-packages/core",
            "runtimes/iris/Lib/encodings"]


@pytest.mark.parametrize("segment", SEGMENTS, ids=lambda s: s or "version")
def test_a_junction_anywhere_on_the_import_path_is_never_chosen(probe, tmp_path, segment):
    root, unsafe, safe = _pair(tmp_path)
    outside = tmp_path / "fuera"
    outside.mkdir()
    linked = unsafe / segment
    target = outside / "copia"
    shutil.copytree(linked, target)  # the outside copy satisfies every existence check
    shutil.rmtree(linked)
    _junction(linked, target)
    try:
        assert _selected(probe, root) == str(safe)
    finally:
        os.rmdir(linked)


def test_a_linked_package_ahead_of_a_module_is_never_chosen(probe, tmp_path):
    """core/iris_install as a package wins over iris_install.py, and core.iris_installer imports it first."""
    root, unsafe, safe = _pair(tmp_path)
    outside = tmp_path / "fuera" / "iris_install"
    outside.mkdir(parents=True)
    (outside / "__init__.py").write_bytes(b"external, never run")
    link = unsafe / "runtimes/iris/Lib/site-packages/core/iris_install"
    _junction(link, outside)
    try:
        assert _selected(probe, root) == str(safe)
    finally:
        os.rmdir(link)


@pytest.mark.parametrize("shadow", ["runtimes/iris/core", "runtimes/iris/Lib/core", "runtimes/iris/DLLs/core.pyd"])
def test_a_core_package_ahead_of_site_packages_is_never_chosen(probe, tmp_path, shadow):
    root, unsafe, safe = _pair(tmp_path)
    path = unsafe / shadow
    if path.suffix:
        path.write_bytes(b"shadow")
    else:
        path.mkdir()
        (path / "iris_installer.py").write_bytes(b"shadow")
    assert _selected(probe, root) == str(safe)


SEARCH_PATHS = {
    "external folder first": b"C:\\fuera\n" + SHIPPED_PTH,
    "external folder last": SHIPPED_PTH.replace(b"import site", b"C:/fuera\nimport site"),
    "relative path out of the runtime": SHIPPED_PTH.replace(b"import site", b"..\\..\\..\\..\\fuera\nimport site"),
    "search order changed": b"Lib/site-packages\n.\nDLLs\nLib\nimport site\n",
    "trailing null byte": SHIPPED_PTH + b"\x00",
    "empty": b"",
}


@pytest.mark.parametrize("content", SEARCH_PATHS.values(), ids=SEARCH_PATHS.keys())
def test_only_the_shipped_search_path_is_accepted(probe, tmp_path, content):
    root, unsafe, safe = _pair(tmp_path)
    (unsafe / "runtimes/iris/python311._pth").write_bytes(content)
    assert _selected(probe, root) == str(safe)


@pytest.mark.parametrize("other", ["python-not-used._pth", "python._pth"])
def test_another_search_path_file_is_never_accepted(probe, tmp_path, other):
    root, unsafe, safe = _pair(tmp_path)
    (unsafe / "runtimes/iris" / other).write_bytes(SHIPPED_PTH)
    if other == "python-not-used._pth":  # the real file is missing, another one is there
        (unsafe / "runtimes/iris/python311._pth").unlink()
    assert _selected(probe, root) == str(safe)


@pytest.mark.parametrize("extra", ["runtimes/iris/fuera.pth", "runtimes/iris/Lib/site-packages/fuera.pth",
                                   "runtimes/iris/pyvenv.cfg", "runtimes/pyvenv.cfg"])
def test_files_that_would_extend_the_search_path_are_never_accepted(probe, tmp_path, extra):
    root, unsafe, safe = _pair(tmp_path)
    (unsafe / extra).write_bytes(b"C:\\fuera\n")
    assert _selected(probe, root) == str(safe)


def test_the_normal_version_is_chosen_and_none_when_all_are_unsafe(probe, tmp_path):
    root = tmp_path / "Iris OS"
    safe = _version(root, "bbbbbbbb")
    assert _selected(probe, root) == str(safe)
    (safe / "runtimes/iris/python311._pth").unlink()  # without its search path the runtime is not the shipped one
    assert _selected(probe, root) == ""
    for name in ("_bbbbbbb", "-bbbbbbb", "~bbbbbbb"):  # installer leftovers are never candidates
        _version(root, name)
    assert _selected(probe, root) == ""


@pytest.mark.parametrize("module", ["runtimes/iris/Lib/site-packages/core/iris_installer.py",
                                    "runtimes/iris/Lib/site-packages/core/__init__.py", "runtimes/iris/python.exe"])
def test_a_symlinked_module_or_program_is_never_chosen(probe, tmp_path, module):
    root, unsafe, safe = _pair(tmp_path)
    outside = tmp_path / "fuera" / Path(module).name
    outside.parent.mkdir()
    outside.write_bytes(b"external, never run")
    (unsafe / module).unlink()
    try:
        os.symlink(outside, unsafe / module)
    except OSError:
        pytest.skip("file symlinks need Developer Mode or elevation here")
    assert _selected(probe, root) == str(safe)


def test_the_script_and_the_build_keep_the_runtime_contract(tmp_path):
    runtime = tmp_path / "staging" / "runtimes" / "iris"
    (runtime / "Lib" / "site-packages").mkdir(parents=True)
    (runtime / "python311._pth").write_bytes(SHIPPED_PTH)
    build_installer.check_search_path(tmp_path / "staging")
    changed = SCRIPT.replace("Lib/site-packages$\\n", "Lib$\\n", 1)
    assert changed != SCRIPT
    with pytest.raises(DistributionError, match="contract"):
        build_installer.check_search_path(tmp_path / "staging", changed)
    for extra in (runtime / "Lib" / "site-packages" / "fuera.pth", runtime / "python._pth", runtime.parent / "pyvenv.cfg"):
        extra.write_bytes(b"C:\\fuera\n")
        with pytest.raises(DistributionError, match="search path"):
            build_installer.check_search_path(tmp_path / "staging")
        extra.unlink()
    (runtime / "python311._pth").write_bytes(b"C:\\fuera\n" + SHIPPED_PTH)
    with pytest.raises(DistributionError, match="shipped search path"):
        build_installer.check_search_path(tmp_path / "staging")


def test_installer_and_uninstaller_share_the_same_checks():
    assert '!insertmacro DEFINE_VERSION_PYTHON ""' in SCRIPT and '!insertmacro DEFINE_VERSION_PYTHON "un."' in SCRIPT
    assert "Function un.SafeVersion" not in SCRIPT  # no second, divergent copy
    section = _block('Section "${PRODUCT}" SecMain', "SectionEnd")
    # The installed version closes Iris before anything is copied, and only a safe one runs.
    assert section.index("Call FindVersionPython") < section.index("'close --root") < section.index("File /r")
