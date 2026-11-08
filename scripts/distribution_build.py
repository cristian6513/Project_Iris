"""Setuptools hooks: consume verified web staging and prepare factory manifests."""

from __future__ import annotations

import importlib.util
import os
import stat
import tempfile
from pathlib import Path

from setuptools.command.build_py import build_py
from setuptools.command.sdist import sdist

# Cmdclass modules must also work when rebuilding from a source distribution.
ROOT = Path(__file__).resolve().parents[1]


def _helper(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_assets = _helper("iris_build_assets", "distribution_assets.py")
_web = _helper("iris_build_web_assets", "distribution_web_assets.py")


def _write_atomic(destination: Path, payload: bytes) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
        os.replace(temporary, destination)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def _stage(root: Path, assets: dict[str, bytes]) -> None:
    # sdist may use hardlinks: replacing a directory entry never edits its source.
    for name in ("manifest.json", "contexts.json"):
        _write_atomic(root / name, assets[name])
    _assets.verify_assets(
        {path.name: _assets.read_regular(root, path.name) for path in root.iterdir()},
        _assets.load_contract(),
    )


def _assert_output_directory(root: Path) -> None:
    for path in (*reversed(root.absolute().parents), root):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise _assets.DistributionError(f"Linked web build output: {path}")


def _stage_web(root: Path, assets: dict[str, bytes]) -> None:
    _assert_output_directory(root)
    # Discard source web data copied by setuptools, including stale build output.
    # Only directory entries in the build tree are removed, never hardlink sources.
    for base in (root / "face", root / "wizard" / "ui"):
        if not base.exists():
            continue
        stack = [base]
        while stack:
            current = stack.pop()
            _assert_output_directory(current)
            for path in current.iterdir():
                name = path.relative_to(root).as_posix()
                if name == "face/animations" or name == "face/__init__.py":
                    continue
                info = path.lstat()
                if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                    raise _assets.DistributionError(f"Linked web build output: {path}")
                if path.is_dir():
                    stack.append(path)
                elif path.is_file():
                    path.unlink()
                else:
                    raise _assets.DistributionError(f"Non-regular web build output: {path}")
    for name, payload in assets.items():
        destination = root.joinpath(*name.split("/"))
        destination.parent.mkdir(parents=True, exist_ok=True)
        _assert_output_directory(destination.parent)
        _write_atomic(destination, payload)
    _web.prepare_web_assets(root)


class BuildPy(build_py):
    def run(self):
        if getattr(self, "editable_mode", False):
            return super().run()
        assets = _assets.prepare_assets(ROOT / "face" / "animations")
        web_assets = _web.prepare_web_assets(_web.web_assets_root(ROOT))
        super().run()
        _stage(Path(self.build_lib) / "face" / "animations", assets)
        _stage_web(Path(self.build_lib), web_assets)


class Sdist(sdist):
    def make_release_tree(self, base_dir, files):
        assets = _assets.prepare_assets(ROOT / "face" / "animations")
        web_assets = _web.prepare_web_assets(_web.web_assets_root(ROOT))
        super().make_release_tree(base_dir, files)
        _stage(Path(base_dir) / "face" / "animations", assets)
        _stage_web(Path(base_dir), web_assets)
