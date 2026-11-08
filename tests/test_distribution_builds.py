"""Real offline builds and installed-wheel resource checks, outside the checkout."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tomllib
import venv
import zipfile
from pathlib import Path

import pytest

from scripts.distribution_assets import load_contract, prepare_assets, public_files
from scripts.verify_distribution import verify_archive
from scripts.distribution_web_assets import (
    is_web_member, prepare_web_assets, web_assets_root,
)

ROOT = Path(__file__).resolve().parents[1]


def run(command, cwd):
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("IRIS_WEB_ASSETS_ROOT", None)
    # All commands use an absolute interpreter; no Node/npm or checkout tool is
    # discoverable during package build, rebuild, install or the resource probe.
    env["PATH"] = ""
    env["PIP_NO_INDEX"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, timeout=180)
    log = cwd / "build-check.log"
    with log.open("a", encoding="utf-8") as handle:
        handle.write(result.stdout + result.stderr)
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-3000:]
    return result.stdout


@pytest.fixture(scope="module")
def distributions(tmp_path_factory):
    work = tmp_path_factory.mktemp("iris-release-build")
    source = work / "source"
    source.mkdir()

    def copy(path):
        destination = source / path.relative_to(ROOT)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)

    # Reproduce all package code and declared public data; never copy credentials,
    # installed environments, native build trees, or the user's private models.
    for package in ("core", "pico", "hardware", "wizard", "face"):
        for path in (ROOT / package).rglob("*.py"):
            if "__pycache__" not in path.parts:
                copy(path)
    config = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))
    setup = config["tool"]["setuptools"]
    for package, patterns in setup["package-data"].items():
        for pattern in patterns:
            for path in (ROOT / package).glob(pattern):
                if path.is_file():
                    copy(path)
    for patterns in setup["data-files"].values():
        for pattern in patterns:
            if pattern.endswith("*.exe"):
                continue  # Optional installer is covered separately with its SHA contract.
            for path in ROOT.glob(pattern):
                copy(path)
    for name in ("pyproject.toml", "MANIFEST.in", "README.md", "LICENSE"):
        if (ROOT / name).is_file():
            copy(ROOT / name)
    for name in (
        "distribution_build.py",
        "distribution_assets.py",
        "distribution_web_assets.py",
        "release_web_assets.json",
        "verify_distribution.py",
        "release_assets.json",
    ):
        copy(ROOT / "scripts" / name)
    web_assets = prepare_web_assets(web_assets_root(ROOT))
    for package in ("face", "wizard/ui"):
        for path in (source / package).rglob("*"):
            if path.is_file() and is_web_member(path.relative_to(source).as_posix()):
                path.unlink()
    for name, payload in web_assets.items():
        path = source.joinpath(*name.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    animations = source / "face" / "animations"
    original = (animations / "manifest.json").read_bytes()
    # A wildcard iris_* or copying every existing local clip must not pass unnoticed.
    for filename in ("iris_private.vrma", "_draft_pc_sit_down_v2.glb", "sit.vrma", "private.vrm"):
        (animations / filename).write_bytes(b"PRIVATE MUST NOT SHIP")

    direct = work / "direct"
    run(
        [sys.executable, "-m", "build", "--no-isolation", "--wheel", "--outdir", str(direct)],
        source,
    )
    sdists = work / "sdist"
    run(
        [sys.executable, "-m", "build", "--no-isolation", "--sdist", "--outdir", str(sdists)],
        source,
    )
    sdist = next(sdists.glob("*.tar.gz"))
    verify_archive(sdist)
    assert (animations / "manifest.json").read_bytes() == original
    assert (animations / "sit.vrma").read_bytes() == b"PRIVATE MUST NOT SHIP"

    extracted = work / "extracted"
    extracted.mkdir()
    with tarfile.open(sdist) as archive:
        # Validate paths/types before extraction, including on Python 3.11.
        archive.extractall(extracted, filter="data")
    unpacked = next(extracted.iterdir())
    rebuilt = work / "rebuilt"
    run(
        [sys.executable, "-m", "build", "--no-isolation", "--wheel", "--outdir", str(rebuilt)],
        unpacked,
    )
    wheels = [next(direct.glob("*.whl")), next(rebuilt.glob("*.whl"))]
    for wheel in wheels:
        verify_archive(wheel)
    return work, wheels


def test_direct_and_sdist_wheels_have_the_same_approved_resources(distributions):
    _, wheels = distributions
    expected = prepare_assets(ROOT / "face" / "animations")
    expected_web = prepare_web_assets(web_assets_root(ROOT))
    for wheel in wheels:
        with zipfile.ZipFile(wheel) as archive:
            actual = {
                name.removeprefix("face/animations/"): archive.read(name)
                for name in archive.namelist()
                if name.startswith("face/animations/")
            }
            assert actual == expected
            actual_web = {
                name: archive.read(name)
                for name in archive.namelist() if is_web_member(name)
            }
            assert actual_web == expected_web
            assert "wizard/data/provider_models_snapshot.json" not in archive.namelist()


@pytest.mark.parametrize("index", [0, 1], ids=["direct-wheel", "sdist-wheel"])
def test_installed_wheel_resources_outside_checkout(distributions, index):
    work, wheels = distributions
    clean = work / f"installed-{index}"
    clean.mkdir()
    environment = clean / "venv"
    venv.EnvBuilder(with_pip=True).create(environment)
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    run(
        [
            str(python),
            "-I",
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--no-index",
            "--no-compile",
            str(wheels[index]),
        ],
        clean,
    )
    contract = load_contract()
    smoke = clean / "smoke.py"
    smoke.write_text(
        "import hashlib, importlib.resources, json, pathlib, shutil, sys\n"
        "import core.animations, core.animation_store, wizard, face\n"
        "import core.animation_library as library_api, core.profile_paths as profile_api\n"
        "from core.profile_migration import migrate_animation_profile\n"
        "from core.factory_animation_inventory import FACTORY_CLIPS\n"
        "root = pathlib.Path(core.animations.__file__).resolve().parents[1]\n"
        "assert root.is_relative_to(pathlib.Path(sys.prefix).resolve()), root\n"
        "assets = root / 'face' / 'animations'\n"
        "assert all(pathlib.Path(p).resolve().is_relative_to(root) for p in face.__path__)\n"
        "assert all(pathlib.Path(p).resolve().is_relative_to(root) for p in wizard.__path__)\n"
        "resource = importlib.resources.files(face) / 'animations' / 'iris_idle.vrma'\n"
        "assert resource.read_bytes() == (assets / 'iris_idle.vrma').read_bytes()\n"
        "assert assets.parent == root / 'face'\n"
        "skill = pathlib.Path(sys.prefix) / 'share/iris-os/hermes-skills/iris-os'\n"
        "assert (skill / 'references/google.md').is_file()\n"
        "assert 'references/google.md' in (skill / 'SKILL.md').read_text('utf8')\n"
        "builder = skill.parent / 'iris-app-builder'\n"
        "assert 'name: iris-app-builder' in (builder / 'SKILL.md').read_text('utf8')\n"
        "for relative in ('app.json', 'ui/main.js', 'ui/widget.js', 'ui/style.css'):\n"
        "    assert (builder / 'template' / relative).is_file()\n"
        "resources = skill.parents[1] / 'builder'\n"
        "for relative in ('app.json', 'LICENSE', 'ui/main.js', 'ui/widget.js', 'ui/view.js', 'ui/style.css', 'backend/main.py', 'skill/SKILL.md'):\n"
        "    assert (resources / 'sdk2' / relative).is_file()\n"
        "for name in ('README.md', 'community-apps-python.md'):\n"
        "    assert (resources / name).is_file()\n"
        "assert (root / 'core/app_builder.py').is_file()\n"
        "web = json.loads((root / 'face/web-assets.json').read_text('utf-8'))\n"
        "for item in web['files']:\n"
        "    payload = (root / item['path']).read_bytes()\n"
        "    assert len(payload) == item['size']\n"
        "    assert hashlib.sha256(payload).hexdigest() == item['sha256']\n"
        "assert web['entrypoints']['face'] == 'face/index.html'\n"
        "assert web['entrypoints']['wizard'] == 'wizard/ui/index.html'\n"
        "assert core.animations._ANIM_DIR == assets\n"
        f"expected = {contract!r}\n"
        "assert FACTORY_CLIPS == expected['clips']\n"
        "assert not (root / 'scripts/release_assets.json').exists()\n"
        f"assert {{p.name for p in assets.iterdir()}} == {public_files(contract)!r}\n"
        "manifest = json.loads((assets / 'manifest.json').read_text('utf-8'))\n"
        "assert len(manifest['clips']) == 59\n"
        "profile = pathlib.Path.cwd() / 'qa-profile'\n"
        "library = library_api.AnimationLibrary(assets, profile / 'animations')\n"
        # This no-deps resource probe supplies an explicit profile and no live
        # process inventory. Runtime resolver/scanner tests use installed deps.
        "library_api.get_animation_library = lambda factory=None: library\n"
        "profile_api._legacy_profile_writers = lambda *args, **kwargs: None\n"
        "migrate_animation_profile(assets, profile / 'animations')\n"
        "assert json.loads((profile / 'animations/manifest.json').read_text())['clips'] == {}\n"
        "assert all(item['source'] == 'factory' "
        "for item in library.snapshot()['resources'].values())\n"
        "catalog = {item['name'] for item in core.animations.get_available_animations()}\n"
        "assert set(manifest['clips']) - set(manifest['runtime_only']) == catalog\n"
        "assert not {'sit', 'pc_sit_down_v2', 'pc_seated_idle_v2', 'walk_in_place'} & catalog\n"
        "assert manifest['clips']['idle'] == 'iris_idle.vrma'\n"
        "for item in expected['clips'].values():\n"
        "    payload = (assets / item['filename']).read_bytes()\n"
        "    assert hashlib.sha256(payload).hexdigest() == item['sha256']\n"
        "upgrade = pathlib.Path.cwd() / 'next-factory'\n"
        "shutil.copytree(assets, upgrade)\n"
        "(upgrade / 'iris_wave.vrma').write_bytes((assets / 'iris_nod.vrma').read_bytes())\n"
        "migrate_animation_profile(upgrade, profile / 'animations')\n"
        "updated = library_api.AnimationLibrary(upgrade, profile / 'animations')"
        ".snapshot()['resources']['wave']\n"
        "assert updated['source'] == 'factory' and updated['filename'] == 'iris_wave.vrma'\n"
        "assert updated['revision'] == expected['clips']['nod']['sha256']\n"
        "avatar = (root / 'face/js/avatar.js').read_text('utf-8')\n"
        "assert '../animations/iris_idle.vrma' in avatar\n"
        "assert not (root / 'wizard/data/provider_models_snapshot.json').exists()\n"
        "print('installed resources verified:', assets)\n",
        encoding="utf-8",
    )
    assert "installed resources verified" in run([str(python), "-I", str(smoke)], clean)


def test_python_and_companion_generate_equivalent_release_manifests(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the cross-runtime contract check")
    module = (ROOT / "companion/scripts/animation_assets.mjs").as_uri()
    script = (
        f"import {{prepareAnimationAssets}} from {json.dumps(module)};"
        f"const assets=await prepareAnimationAssets({json.dumps(str(ROOT / 'face/animations'))});"
        "const pairs=[...assets].map(([name, data])=>[name,data.toString('base64')]);"
        "console.log(JSON.stringify(Object.fromEntries(pairs)));"
    )
    import base64

    actual = json.loads(run([node, "--input-type=module", "-e", script], tmp_path))
    expected = prepare_assets(ROOT / "face" / "animations")
    assert set(actual) == set(expected)
    for name, data in expected.items():
        other = base64.b64decode(actual[name])
        if name.endswith(".json"):
            assert json.loads(other) == json.loads(data)
        else:
            assert hashlib.sha256(other).digest() == hashlib.sha256(data).digest()
