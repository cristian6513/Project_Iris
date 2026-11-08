"""Build the Iris input wheel from an isolated, public-data-only source tree."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import tomllib
from pathlib import Path

if __package__:
    from .distribution_assets import prepare_assets
    from .distribution_web_assets import prepare_web_assets
    from .prepare_private_runtime import new_directory, regular_file
    from .stage_companion_release import _pe_architecture, _read_manifest, stage_companion_release
    from .verify_distribution import verify_archive
else:
    from distribution_assets import prepare_assets
    from distribution_web_assets import prepare_web_assets
    from prepare_private_runtime import new_directory, regular_file
    from stage_companion_release import _pe_architecture, _read_manifest, stage_companion_release
    from verify_distribution import verify_archive

ROOT = Path(__file__).resolve().parents[1]


def build_runtime_wheel(work: Path, python: Path, web_root: Path) -> Path:
    python = regular_file(python)
    factory = prepare_assets(ROOT / "face/animations")
    web = prepare_web_assets(web_root)
    inputs = set()
    for package in ("core", "pico", "hardware", "wizard", "face"):
        for path in (ROOT / package).rglob("*.py"):
            if "__pycache__" not in path.parts:
                inputs.add(regular_file(path))
    setup = tomllib.loads(regular_file(ROOT / "pyproject.toml").read_text("utf8"))["tool"]["setuptools"]
    for patterns in setup["data-files"].values():
        for pattern in patterns:
            if pattern.endswith("*.exe"):
                continue
            for path in ROOT.glob(pattern):
                inputs.add(regular_file(path))
    for name in ("pyproject.toml", "MANIFEST.in", "README.md", "LICENSE"):
        inputs.add(regular_file(ROOT / name))
    for name in (
        "distribution_build.py", "distribution_assets.py", "distribution_web_assets.py",
        "release_assets.json", "release_web_assets.json", "verify_distribution.py",
    ):
        inputs.add(regular_file(ROOT / "scripts" / name))
    manifest_path = regular_file(ROOT / "companion/release-manifest.json")
    manifest = _read_manifest(manifest_path)
    installer = regular_file(
        ROOT / "companion/src-tauri/target/release/bundle/nsis" / manifest["filename"],
        manifest["sha256"],
    )
    if _pe_architecture(installer) not in {"x86", "x64"}:
        raise ValueError("Companion build input must be a valid PE installer")
    # Reject every input alias before creating the source snapshot. The
    # Companion staging helper rechecks its release pair after copying it.
    work = new_directory(work)
    source = work / "source"
    source.mkdir()
    for path in sorted(inputs):
        regular_file(path)
        destination = source / path.relative_to(ROOT)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)
    for name, payload in web.items():
        destination = source / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)
    for name, payload in factory.items():
        destination = source / "face/animations" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)
    # This validates and copies the existing release pair without rebuilding or
    # changing the developer Companion preview or its canonical manifest.
    stage_companion_release(ROOT, source / "dist/companion")
    environment = {
        key: value for key, value in os.environ.items()
        if key.upper() in {"SYSTEMROOT", "WINDIR", "TEMP", "TMP", "COMSPEC"}
    }
    environment.update(
        PATH="", PIP_NO_INDEX="1", PYTHONDONTWRITEBYTECODE="1",
        IRIS_WEB_ASSETS_ROOT=str(source),
        IRIS_HOME=str(work / "profile/iris"), HERMES_HOME=str(work / "profile/hermes"),
    )
    with (work / "build.log").open("w", encoding="utf8") as log:
        subprocess.run([
            str(python), "-E", "-s", "-B", "-X", "utf8", "-m", "build",
            "--wheel", "--no-isolation", "--outdir", str(work / "dist"),
        ], cwd=source, env=environment, stdout=log, stderr=log, check=True, timeout=180)
    wheels = list((work / "dist").glob("*.whl"))
    if len(wheels) != 1:
        raise ValueError("Isolated Iris build must produce exactly one wheel")
    verify_archive(wheels[0])
    return wheels[0]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--web-root", type=Path, required=True)
    args = parser.parse_args(argv)
    print(build_runtime_wheel(args.work_root, args.python, args.web_root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
