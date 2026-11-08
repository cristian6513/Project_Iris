"""A08.5: approved optional extras install into isolated profile environments."""
from __future__ import annotations

import asyncio
import base64
import csv
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import wave
import zipfile
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from core import extras
from core import runtime_descriptor as policy
from core.config import IRISConfig
from core.profile_paths import ProfilePathError, _open_plain_file, _try_lock, _unlock

ROOT = Path(__file__).resolve().parents[1]
URL = "https://files.pythonhosted.org/packages/aa/bb/"


# ── Fixture builders ────────────────────────────────────────────────


def build_wheel(directory: Path, name: str, version: str, files: dict[str, bytes]) -> Path:
    """Write a valid pure-Python wheel with RECORD; members may use .data schemes."""
    dist = f"{name.replace('-', '_')}-{version}"
    path = directory / f"{dist}-py3-none-any.whl"
    entries = dict(files)
    entries[f"{dist}.dist-info/METADATA"] = f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n".encode()
    entries[f"{dist}.dist-info/WHEEL"] = b"Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
    record = io.StringIO()
    writer = csv.writer(record, lineterminator="\n")
    for member, data in entries.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        writer.writerow([member, f"sha256={digest}", len(data)])
    writer.writerow([f"{dist}.dist-info/RECORD", "", ""])
    entries[f"{dist}.dist-info/RECORD"] = record.getvalue().encode()
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for member, data in entries.items():
            archive.writestr(member, data)
    return path


def repack_installed(directory: Path, distribution: str) -> Path:
    """Wheel of an installed distribution (used to give the fake env real NumPy)."""
    from importlib import metadata

    dist = metadata.distribution(distribution)
    base = Path(dist.locate_file(""))
    wheel = directory / f"{dist.metadata['Name']}-{dist.version}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w", zipfile.ZIP_STORED) as archive:
        for item in dist.files or ():
            parts = Path(str(item)).parts
            if not parts or {"..", "__pycache__", "tests"} & set(parts) or str(item).endswith(".pyc"):
                continue
            source = base / item
            if source.is_file():
                archive.write(source, str(item).replace("\\", "/"))
    return wheel


class Index:
    """Serve approved wheel URLs from memory; variants simulate network faults."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.calls: list[str] = []
        self.faults: dict[str, str] = {}

    def add(self, wheel: Path) -> dict:
        data = wheel.read_bytes()
        url = URL + wheel.name
        self.files[url] = data
        return {"url": url, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                "name": wheel.name.split("-")[0].replace("_", "-"), "version": wheel.name.split("-")[1]}

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.calls.append(url)
        fault = self.faults.get(url)
        if fault in {"500-once", "timeout-once"}:
            del self.faults[url]   # the network recovers on the next attempt
        if fault == "500-once":
            return httpx.Response(500)
        if fault in {"timeout", "timeout-once"}:
            raise httpx.ReadTimeout("stalled", request=request)
        if fault == "500" or url not in self.files:
            return httpx.Response(500)
        data = self.files[url]
        if fault == "corrupt":
            data = data[:-1] + bytes([data[-1] ^ 1])
        elif fault == "truncate":
            data = data[: len(data) // 2]
        elif fault == "oversize":
            data = data + b"x"
        return httpx.Response(200, content=data)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


def write_lock(data_dir: Path, extra_id: str, packages: list[dict], extra_entries: str = "") -> bytes:
    lines = ['lock-version = "1.0"', 'created-by = "test"', 'requires-python = ">=3.11"', ""]
    for package in packages:
        lines += [
            "[[packages]]",
            f'name = "{package["name"]}"',
            f'version = "{package["version"]}"',
            f'wheels = [{{ url = "{package["url"]}", size = {package["size"]}, '
            f'hashes = {{ sha256 = "{package["sha256"]}" }} }}]',
            "",
        ]
    raw = ("\n".join(lines) + extra_entries).encode()
    (data_dir / f"pylock.{extra_id}.toml").write_bytes(raw)
    return raw


def make_manifest(data_dir: Path, locks: dict[str, bytes], *, probe: dict[str, list[str]] | None = None,
                  python: str | None = None) -> extras.ExtrasManifest:
    document = {
        "schema_version": 1,
        "platform": extras.interpreter_platform(),
        "python": python or f"{sys.version_info[0]}.{sys.version_info[1]}",
        "resolution": {"index_url": "https://pypi.org/simple", "exclude_newer": "2026-10-03T00:00:00Z",
                       "python_platform": "x86_64-pc-windows-msvc"},
        "extras": {
            extra_id: {
                "label": f"Extra {extra_id}", "consumer": f"tts:{extra_id}", "requirements": [extra_id],
                "lock": f"pylock.{extra_id}.toml", "lock_sha256": hashlib.sha256(raw).hexdigest(),
                "probe": (probe or {}).get(extra_id, ["irisfake"]), "download_bytes": 1, "packages": 1,
                "models": "Modelo de prueba.", "warning": "",
            }
            for extra_id, raw in locks.items()
        },
        "unsupported": {"neutts": "Sin wheels para esta plataforma."},
    }
    (data_dir / "manifest.json").write_text(json.dumps(document), encoding="utf-8")
    return extras.load_manifest(data_dir / "manifest.json")


@pytest.fixture
def lab(tmp_path, monkeypatch):
    monkeypatch.delenv(policy.DESCRIPTOR_ENV, raising=False)
    monkeypatch.setattr(extras, "_DOWNLOAD_RETRY_DELAYS", (0.0, 0.0))
    home = tmp_path / "perfil con espacios ñ"
    home.mkdir()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    index = Index()
    fake = index.add(build_wheel(wheels, "irisfake", "1.0", {"irisfake/__init__.py": b"VALUE = 1\n"}))
    manifest = make_manifest(data_dir, {"kokoro": write_lock(data_dir, "kokoro", [fake])})
    return SimpleNamespace(home=home, data_dir=data_dir, wheels=wheels, index=index, fake=fake,
                           manifest=manifest, root=home / "extras")


def install(lab, extra_id="kokoro", **kwargs):
    return extras.install_extra(extra_id, manifest=kwargs.pop("manifest", lab.manifest), home=lab.home,
                                transport=lab.index.transport, **kwargs)


def tree(path: Path) -> dict[str, str]:
    return {item.relative_to(path).as_posix(): hashlib.sha256(item.read_bytes()).hexdigest()
            for item in sorted(path.rglob("*")) if item.is_file()}


# ── Manifest and approved lock ──────────────────────────────────────


def test_shipped_manifest_admits_only_wheel_locks_from_pypi():
    manifest = extras.load_manifest()
    assert set(manifest.extras) == {"kokoro", "hf-s2s"}
    assert {"coqui", "neutts", "faster-whisper", "elevenlabs"} <= set(manifest.unsupported)
    assert manifest.platform == "win_amd64" and manifest.python == "3.11"
    for spec in manifest.extras.values():
        raw = (extras.DATA_DIR / spec.lock).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == spec.lock_sha256
        assert spec.consumer == f"tts:{spec.id}" and spec.packages > 0 and spec.download_bytes > 0
        if not extras.compatibility_error(manifest):
            wheels = extras.locked_wheels(spec, manifest)
            assert len(wheels) == spec.packages
            assert sum(wheel.size for wheel in wheels) == spec.download_bytes
            assert all(wheel.url.startswith("https://files.pythonhosted.org/") for wheel in wheels)


def test_shipped_kokoro_lock_bundles_espeak_and_modern_transformers():
    manifest = extras.load_manifest()
    if extras.compatibility_error(manifest):
        pytest.skip("approved locks target CPython 3.11 win_amd64")
    versions = {wheel.name: wheel.version for wheel in extras.locked_wheels(manifest.extras["kokoro"], manifest)}
    assert "espeakng-loader" in versions and "numpy" in versions
    assert versions["transformers"].startswith("4.")
    hf = {wheel.name for wheel in extras.locked_wheels(manifest.extras["hf-s2s"], manifest)}
    assert {"speech-to-speech", "numpy", "torch"} <= hf


@pytest.mark.parametrize("mutation", ["sha", "sdist", "host", "http", "nohash"])
def test_lock_that_is_not_exactly_approved_is_rejected_before_any_download(lab, mutation):
    package = dict(lab.fake)
    raw = None
    if mutation == "host":
        package["url"] = "https://example.com/irisfake-1.0-py3-none-any.whl"
    elif mutation == "http":
        package["url"] = package["url"].replace("https://", "http://")
    if mutation == "sdist":
        raw = ('lock-version = "1.0"\ncreated-by = "t"\n[[packages]]\nname = "irisfake"\nversion = "1.0"\n'
               f'sdist = {{ url = "{URL}irisfake-1.0.tar.gz", size = 10, hashes = {{ sha256 = "{"a" * 64}" }} }}\n')
        (lab.data_dir / "pylock.kokoro.toml").write_text(raw, encoding="utf-8")
        raw = raw.encode()
    elif mutation == "nohash":
        raw = ('lock-version = "1.0"\ncreated-by = "t"\n[[packages]]\nname = "irisfake"\nversion = "1.0"\n'
               f'wheels = [{{ url = "{package["url"]}", size = {package["size"]}, hashes = {{ md5 = "{"a" * 32}" }} }}]\n')
        (lab.data_dir / "pylock.kokoro.toml").write_text(raw, encoding="utf-8")
        raw = raw.encode()
    else:
        raw = write_lock(lab.data_dir, "kokoro", [package])
    manifest = make_manifest(lab.data_dir, {"kokoro": raw})
    if mutation == "sha":
        (lab.data_dir / "pylock.kokoro.toml").write_bytes(raw + b"\n# tampered\n")
    with pytest.raises(extras.ExtrasError) as error:
        install(lab, manifest=manifest)
    assert error.value.code == "lock_invalid"
    assert lab.index.calls == []
    assert not lab.root.exists()


def test_unknown_unsupported_and_incompatible_extras_change_nothing(lab):
    for extra_id, code in (("desconocido", "unknown_extra"), ("../x", "unknown_extra"), ("neutts", "unsupported")):
        with pytest.raises(extras.ExtrasError) as error:
            install(lab, extra_id)
        assert error.value.code == code
    incompatible = make_manifest(lab.data_dir, {"kokoro": (lab.data_dir / "pylock.kokoro.toml").read_bytes()},
                                 python="3.99")
    with pytest.raises(extras.ExtrasError) as error:
        install(lab, manifest=incompatible)
    assert error.value.code == "incompatible"
    status = extras.extras_status(manifest=incompatible, home=lab.home)
    assert status["extras"]["kokoro"]["state"] == "incompatible"
    assert extras.resolve_environment("kokoro", manifest=incompatible, home=lab.home) is None
    assert lab.index.calls == [] and not lab.root.exists()


# ── Install, consumer resolution, repair and removal ────────────────


def test_install_commits_isolated_environment_and_consumer_resolves_it(lab):
    events = []
    result = install(lab, progress=events.append)
    assert result == {"status": "installed", "changed": True, "packages": 1}
    environment = extras.resolve_environment("kokoro", manifest=lab.manifest, home=lab.home)
    assert environment is not None
    assert (environment.site / "irisfake/__init__.py").read_bytes() == b"VALUE = 1\n"
    assert environment.env_dir.parent == lab.root / "kokoro"
    assert environment.models == lab.root / "kokoro/models"
    assert extras.verify_environment(environment.env_dir) == []
    state = json.loads((lab.root / "kokoro/state.json").read_text(encoding="utf-8"))
    assert state["packages"] == {"irisfake": "1.0"} and state["lock_sha256"] == lab.manifest.extras["kokoro"].lock_sha256
    status = extras.extras_status(manifest=lab.manifest, home=lab.home)["extras"]["kokoro"]
    assert status["state"] == "installed" and status["versions"] == {"irisfake": "1.0"}
    assert {event.get("phase") for event in events} >= {"resolve", "fetch", "install", "verify", "done"}
    # Downloads are released after commit; no staging/partial files remain.
    assert not list((lab.root / ".downloads").iterdir())
    assert [p.name for p in (lab.root / "kokoro").iterdir() if p.name.startswith(".")] == []
    # The extra is invisible to Core's own interpreter.
    assert "irisfake" not in sys.modules
    # A second install verifies identity without downloading again.
    assert install(lab)["changed"] is False


def test_probe_runs_in_isolation_and_cannot_see_base_packages(lab):
    manifest = make_manifest(lab.data_dir, {"kokoro": (lab.data_dir / "pylock.kokoro.toml").read_bytes()},
                             probe={"kokoro": ["irisfake", "fastapi"]})
    with pytest.raises(extras.ExtrasError) as error:
        install(lab, manifest=manifest)
    assert error.value.code == "probe_failed" and "fastapi" in str(error.value)
    assert extras.extras_status(manifest=manifest, home=lab.home)["extras"]["kokoro"]["state"] == "not_installed"
    assert [p.name for p in (lab.root / "kokoro").iterdir()] == []


PACKAGED_LAUNCHER = """
import runpy, sys
# Reproduce python311._pth: base site-packages is on sys.path before the
# worker runs, and the worker itself is installed inside it.
base, worker = sys.argv[1], sys.argv[2]
sys.path.append(base)
sys.argv = [worker] + sys.argv[3:]
runpy.run_path(worker, run_name="__main__")
"""


def _packaged_probe(tmp_path, *modules, preload=""):
    base = tmp_path / "runtimes/iris/Lib/site-packages"
    (base / "core").mkdir(parents=True)
    for name in ("extras_worker.py", "tts_engines.py"):
        (base / "core" / name).write_bytes((ROOT / "core" / name).read_bytes())
    (base / "basepkg").mkdir()
    (base / "basepkg/__init__.py").write_text("VALUE = 'base'\n", encoding="utf-8")
    site = tmp_path / "extra/site"
    (site / "irisfake").mkdir(parents=True)
    (site / "irisfake/__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    launcher = tmp_path / "launcher.py"
    launcher.write_text(preload + PACKAGED_LAUNCHER, encoding="utf-8")
    process = subprocess.run(
        [sys.executable, "-I", "-S", "-B", str(launcher), str(base), str(base / "core/extras_worker.py"),
         "--site", str(site), "--probe", ",".join(modules)],
        capture_output=True, timeout=60,
    )
    return extras._parse_reply(process.stdout), process


def test_worker_installed_inside_base_site_packages_isolates_from_it(tmp_path):
    reply, process = _packaged_probe(tmp_path, "irisfake")
    assert reply and reply["ok"] is True, process.stderr.decode(errors="replace")
    reply, _ = _packaged_probe(tmp_path / "base-visible", "irisfake", "basepkg")
    assert reply["ok"] is False and "basepkg" in reply["detail"]


def test_worker_refuses_to_start_after_a_base_package_was_imported(tmp_path):
    preload = "import sys\nsys.path.append(sys.argv[1])\nimport basepkg\n"
    reply, process = _packaged_probe(tmp_path, "irisfake", preload=preload)
    assert reply is None and process.returncode != 0
    assert b"inherited base modules: basepkg" in process.stderr


@pytest.mark.parametrize("fault,code", [("corrupt", "corrupt_download"), ("truncate", "corrupt_download"),
                                        ("oversize", "corrupt_download"), ("500", "download_failed")])
def test_failed_download_keeps_previous_environment_and_leaves_no_partial_files(lab, fault, code):
    install(lab)
    before = tree(lab.root / "kokoro")
    second = lab.index.add(build_wheel(lab.wheels, "irisfake", "2.0", {"irisfake/__init__.py": b"VALUE = 2\n"}))
    manifest = make_manifest(lab.data_dir, {"kokoro": write_lock(lab.data_dir, "kokoro", [second])})
    assert extras.extras_status(manifest=manifest, home=lab.home)["extras"]["kokoro"]["state"] == "outdated"
    assert extras.resolve_environment("kokoro", manifest=manifest, home=lab.home) is None
    lab.index.faults[second["url"]] = fault
    with pytest.raises(extras.ExtrasError) as error:
        install(lab, manifest=manifest)
    assert error.value.code == code
    assert tree(lab.root / "kokoro") == before
    assert list((lab.root / ".downloads").iterdir()) == []


@pytest.mark.parametrize("fault", ["500-once", "timeout-once"])
def test_a_dropped_download_is_retried_and_the_install_completes(lab, fault):
    events = []
    lab.index.faults[lab.fake["url"]] = fault
    install(lab, progress=events.append)
    assert lab.index.calls.count(lab.fake["url"]) == 2
    assert any("Reintentando" in event.get("text", "") for event in events)
    assert extras.extras_status(manifest=lab.manifest, home=lab.home)["extras"]["kokoro"]["state"] == "installed"


@pytest.mark.parametrize("fault,attempts,code", [("timeout", 3, "download_failed"), ("500", 3, "download_failed"),
                                                 ("corrupt", 1, "corrupt_download")])
def test_retries_are_bounded_and_a_hash_mismatch_is_never_retried(lab, fault, attempts, code):
    lab.index.faults[lab.fake["url"]] = fault
    with pytest.raises(extras.ExtrasError) as error:
        install(lab)
    assert error.value.code == code
    assert lab.index.calls.count(lab.fake["url"]) == attempts
    assert list((lab.root / ".downloads").iterdir()) == []


def test_cancellation_before_commit_restores_nothing_because_nothing_changed(lab):
    install(lab)
    before = tree(lab.root / "kokoro")
    payload = b"x" * (3 * extras.CHUNK)
    second = lab.index.add(build_wheel(lab.wheels, "irisfake", "2.0",
                                       {"irisfake/__init__.py": b"VALUE = 2\n", "irisfake/big.bin": payload}))
    manifest = make_manifest(lab.data_dir, {"kokoro": write_lock(lab.data_dir, "kokoro", [second])})
    cancel = threading.Event()

    def progress(event):
        if event.get("type") == "progress":
            cancel.set()

    with pytest.raises(extras.ExtrasError) as error:
        install(lab, manifest=manifest, cancel=cancel, progress=progress)
    assert error.value.code == "cancelled"
    assert tree(lab.root / "kokoro") == before
    assert not any(path.name.endswith(".part") for path in (lab.root / ".downloads").iterdir())


def test_cancel_during_probe_kills_worker_and_discards_staging(lab, monkeypatch):
    cancel = threading.Event()
    real_popen = subprocess.Popen

    def slow_probe(command, **kwargs):
        cancel.set()
        return real_popen([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)

    monkeypatch.setattr(extras.subprocess, "Popen", slow_probe)
    with pytest.raises(extras.ExtrasError) as error:
        install(lab, cancel=cancel)
    assert error.value.code == "cancelled"
    assert [p.name for p in (lab.root / "kokoro").iterdir()] == []


def test_insufficient_space_is_reported_before_downloading(lab, monkeypatch):
    monkeypatch.setattr(extras, "_free_bytes", lambda _path: 1024)
    with pytest.raises(extras.ExtrasError) as error:
        install(lab)
    assert error.value.code == "insufficient_space" and error.value.status_code == 507
    assert lab.index.calls == []
    assert not (lab.root / "kokoro/state.json").exists()


def test_disk_full_during_extraction_discards_staging(lab, monkeypatch):
    real = extras._extract

    def full(*args):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(extras, "_extract", full)
    with pytest.raises(extras.ExtrasError) as error:
        install(lab)
    assert error.value.code == "insufficient_space"
    assert [p.name for p in (lab.root / "kokoro").iterdir()] == []
    monkeypatch.setattr(extras, "_extract", real)
    assert install(lab)["changed"] is True


# Windows zipfile already normalises backslashes in member names to "/".
@pytest.mark.parametrize("member", ["../evil.py", "/abs.py", "C:/drive.py", "a/../../evil.py", "nul.py",
                                    "aux.txt", "dir./x.py", "irisfake-3.0.data/unknown/x.py"])
def test_wheel_members_cannot_escape_or_use_unknown_schemes(lab, member):
    bad = lab.index.add(build_wheel(lab.wheels, "irisfake", "3.0", {member: b"bad", "irisfake/__init__.py": b""}))
    manifest = make_manifest(lab.data_dir, {"kokoro": write_lock(lab.data_dir, "kokoro", [bad])})
    with pytest.raises(extras.ExtrasError) as error:
        install(lab, manifest=manifest)
    assert error.value.code == "install_failed"
    assert not (lab.home.parent / "evil.py").exists()
    assert [p.name for p in (lab.root / "kokoro").iterdir()] == []


def test_wheel_data_schemes_map_like_a_target_install(lab):
    wheel = lab.index.add(build_wheel(lab.wheels, "irisfake", "4.0", {
        "irisfake/__init__.py": b"",
        "irisfake-4.0.data/platlib/irisfake_native.py": b"NATIVE = 1\n",
        "irisfake-4.0.data/data/share/irisfake.txt": b"data",
        "irisfake-4.0.data/scripts/irisfake.exe": b"launcher",
        "irisfake-4.0.data/headers/irisfake.h": b"header",
    }))
    manifest = make_manifest(lab.data_dir, {"kokoro": write_lock(lab.data_dir, "kokoro", [wheel])})
    install(lab, manifest=manifest)
    site = extras.resolve_environment("kokoro", manifest=manifest, home=lab.home).site
    assert (site / "irisfake_native.py").is_file() and (site / "share/irisfake.txt").is_file()
    assert not list(site.rglob("*.exe")) and not list(site.rglob("*.h"))


def test_repair_rebuilds_a_damaged_environment_in_a_new_generation(lab):
    install(lab)
    old = extras.resolve_environment("kokoro", manifest=lab.manifest, home=lab.home)
    (old.site / "irisfake/__init__.py").write_bytes(b"VALUE = 'tampered'\n")
    assert extras.verify_environment(old.env_dir) == ["irisfake/__init__.py"]
    assert install(lab, repair=True)["changed"] is True
    new = extras.resolve_environment("kokoro", manifest=lab.manifest, home=lab.home)
    assert new.env_dir != old.env_dir and not old.env_dir.exists()
    assert (new.site / "irisfake/__init__.py").read_bytes() == b"VALUE = 1\n"
    assert install(lab, repair=True) == {"status": "installed", "changed": False, "verified": True}


def test_missing_environment_reports_broken_and_repair_recovers(lab):
    install(lab)
    environment = extras.resolve_environment("kokoro", manifest=lab.manifest, home=lab.home)
    extras._remove_tree(environment.env_dir)
    assert extras.extras_status(manifest=lab.manifest, home=lab.home)["extras"]["kokoro"]["state"] == "broken"
    assert extras.resolve_environment("kokoro", manifest=lab.manifest, home=lab.home) is None
    install(lab, repair=True)
    assert extras.extras_status(manifest=lab.manifest, home=lab.home)["extras"]["kokoro"]["state"] == "installed"


def test_remove_keeps_models_and_unrelated_profile_data_unless_purged(lab):
    install(lab)
    second = make_manifest(lab.data_dir, {"kokoro": (lab.data_dir / "pylock.kokoro.toml").read_bytes(),
                                          "hf-s2s": write_lock(lab.data_dir, "hf-s2s", [lab.fake])})
    install(lab, "hf-s2s", manifest=second)
    models = lab.root / "kokoro/models/huggingface/model.bin"
    models.parent.mkdir(parents=True)
    models.write_bytes(b"weights")
    voice = lab.home / "voices/mi voz.wav"
    voice.parent.mkdir()
    voice.write_bytes(b"RIFF")
    other = tree(lab.root / "hf-s2s")
    result = extras.remove_extra("kokoro", manifest=second, home=lab.home)
    assert result["status"] == "not_installed" and result["models_kept"] is True
    assert models.read_bytes() == b"weights" and voice.read_bytes() == b"RIFF"
    assert tree(lab.root / "hf-s2s") == other
    assert extras.extras_status(manifest=second, home=lab.home)["extras"]["kokoro"]["state"] == "not_installed"
    assert sorted(p.name for p in (lab.root / "kokoro").iterdir()) == ["models"]
    purged = extras.remove_extra("kokoro", purge_models=True, manifest=second, home=lab.home)
    assert purged["models_kept"] is False and not (lab.root / "kokoro").exists()
    assert voice.exists() and tree(lab.root / "hf-s2s") == other


def test_install_never_renames_the_new_tree_that_scanners_may_hold(lab, monkeypatch):
    real_replace = os.replace

    def scanner(source, target):
        if Path(source).is_dir():
            raise PermissionError(5, "Access is denied")  # Windows Defender holding new DLLs
        return real_replace(source, target)

    monkeypatch.setattr(extras.os, "replace", scanner)
    assert install(lab)["changed"] is True
    assert extras.resolve_environment("kokoro", manifest=lab.manifest, home=lab.home) is not None


def test_remove_retries_a_brief_sharing_violation(lab, monkeypatch):
    install(lab)
    real_replace = os.replace
    failures = []

    def briefly_held(source, target):
        if Path(source).name.startswith("env-") and len(failures) < 2:
            failures.append(source)
            raise PermissionError(32, "being used by another process")
        return real_replace(source, target)

    monkeypatch.setattr(extras, "_SHARING_RETRY_DELAYS", (0, 0, 0))
    monkeypatch.setattr(extras.os, "replace", briefly_held)
    assert extras.remove_extra("kokoro", manifest=lab.manifest, home=lab.home)["status"] == "not_installed"
    assert len(failures) == 2


def test_remove_in_use_environment_reports_it_and_changes_nothing(lab, monkeypatch):
    install(lab)
    before = tree(lab.root / "kokoro")
    monkeypatch.setattr(extras, "_SHARING_RETRY_DELAYS", (0, 0))
    real_replace = os.replace

    def locked(source, target):
        if Path(source).name.startswith("env-"):
            raise PermissionError(32, "in use")
        return real_replace(source, target)

    monkeypatch.setattr(extras.os, "replace", locked)
    with pytest.raises(extras.ExtrasError) as error:
        extras.remove_extra("kokoro", manifest=lab.manifest, home=lab.home)
    assert error.value.code == "in_use"
    assert tree(lab.root / "kokoro") == before


def test_one_operation_at_a_time_per_profile(lab):
    lab.root.mkdir()
    with _open_plain_file(lab.root / ".operation.lock") as handle:
        assert _try_lock(handle)
        try:
            with pytest.raises(extras.ExtrasError) as error:
                install(lab)
            assert error.value.code == "busy"
            with pytest.raises(extras.ExtrasError):
                extras.remove_extra("kokoro", manifest=lab.manifest, home=lab.home)
        finally:
            _unlock(handle)
    assert lab.index.calls == []


@pytest.mark.skipif(os.name != "nt", reason="directory junctions are Windows-specific")
def test_extras_root_junction_is_rejected_before_writing_outside_profile(lab, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    subprocess.run(["cmd", "/c", "mklink", "/J", str(lab.root), str(outside)], check=True,
                   capture_output=True)
    with pytest.raises(ProfilePathError):
        install(lab)
    with pytest.raises(ProfilePathError):
        extras.resolve_environment("kokoro", manifest=lab.manifest, home=lab.home)
    assert list(outside.iterdir()) == []


def test_path_too_long_is_reported_before_writing_any_file(lab, monkeypatch):
    deep = lab.index.add(build_wheel(lab.wheels, "irisfake", "5.0", {
        "irisfake/__init__.py": b"", "irisfake/" + "d" * 120 + "/" + "f" * 120 + ".py": b"",
    }))
    manifest = make_manifest(lab.data_dir, {"kokoro": write_lock(lab.data_dir, "kokoro", [deep])})
    monkeypatch.setattr(extras, "_long_paths_enabled", lambda: False)
    with pytest.raises(extras.ExtrasError) as error:
        install(lab, manifest=manifest)
    assert error.value.code == "path_too_long" and "ruta más corta" in str(error.value)
    assert [p.name for p in (lab.root / "kokoro").iterdir()] == []


def _junction(alias: Path, target: Path) -> None:
    alias.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["cmd", "/c", "mklink", "/J", str(alias), str(target)], check=True, capture_output=True)


CACHE_ALIASES = ["models", "pycache", "models/huggingface", "models/torch", "models/cache"]


@pytest.mark.skipif(os.name != "nt", reason="directory junctions are Windows-specific")
@pytest.mark.parametrize("relative", CACHE_ALIASES)
def test_cache_junction_outside_profile_is_never_resolved(lab, tmp_path, relative):
    install(lab)
    outside = tmp_path / "fuera del perfil"
    outside.mkdir()
    alias = lab.root / "kokoro" / relative
    _junction(alias, outside)
    try:
        with pytest.raises(ProfilePathError):
            extras.resolve_environment("kokoro", manifest=lab.manifest, home=lab.home)
        state = extras.extras_status(manifest=lab.manifest, home=lab.home)["extras"]["kokoro"]["state"]
        assert state == "broken"
    finally:
        os.rmdir(alias)  # the link itself, never its target
    assert list(outside.iterdir()) == []
    assert extras.resolve_environment("kokoro", manifest=lab.manifest, home=lab.home) is not None


@pytest.mark.skipif(os.name != "nt", reason="directory junctions are Windows-specific")
@pytest.mark.parametrize("relative", CACHE_ALIASES)
def test_worker_start_revalidates_caches_planted_after_resolution(lab, tmp_path, monkeypatch, relative):
    install(lab)
    environment = extras.resolve_environment("kokoro", manifest=lab.manifest, home=lab.home)
    outside = tmp_path / "fuera del perfil"
    outside.mkdir()
    alias = lab.root / "kokoro" / relative
    _junction(alias, outside)
    launched = []

    async def never(*args, **kwargs):
        launched.append(args)
        raise AssertionError("worker must not start")

    monkeypatch.setattr(extras.asyncio, "create_subprocess_exec", never)
    try:
        with pytest.raises(ProfilePathError):
            asyncio.run(extras.ExtraWorker(environment).start())
    finally:
        os.rmdir(alias)
    assert launched == [] and list(outside.iterdir()) == []


@pytest.mark.skipif(os.name != "nt", reason="directory junctions are Windows-specific")
def test_tts_reports_unavailable_when_a_cache_alias_appears_before_launch(lab, tmp_path, monkeypatch):
    from core import tts

    install(lab)
    environment = extras.resolve_environment("kokoro", manifest=lab.manifest, home=lab.home)
    outside = tmp_path / "fuera del perfil"
    outside.mkdir()
    _junction(environment.pycache, outside)
    try:
        provider = tts.IsolatedExtraTTSProvider("kokoro", {"voice": "ef_dora", "lang": "es"}, environment)
        asyncio.run(provider.initialize())  # logs and stays unavailable; never raises into Core
        assert asyncio.run(provider.synthesize("hola")) == (b"", [])
    finally:
        os.rmdir(environment.pycache)
    assert list(outside.iterdir()) == []


def test_worker_start_creates_every_cache_as_a_plain_profile_directory(lab, monkeypatch):
    install(lab)
    environment = extras.resolve_environment("kokoro", manifest=lab.manifest, home=lab.home)
    seen = {}

    async def capture(*args, **kwargs):
        seen.update(kwargs["env"])
        raise OSError("stop before launching")

    monkeypatch.setattr(extras.asyncio, "create_subprocess_exec", capture)
    with pytest.raises(OSError):
        asyncio.run(extras.ExtraWorker(environment).start())
    for directory in environment.writable_directories():
        assert directory.is_dir() and not directory.is_symlink()
        assert directory.resolve() == directory.parent.resolve() / directory.name
    for key, name in extras._CACHE_DIRECTORIES.items():
        assert seen[key] == str(environment.models / name)


@pytest.mark.skipif(os.name != "nt", reason="directory junctions are Windows-specific")
def test_purging_an_aliased_models_folder_removes_only_the_link(lab, tmp_path):
    install(lab)
    outside = tmp_path / "fuera del perfil"
    outside.mkdir()
    (outside / "ajeno.bin").write_bytes(b"not ours")
    _junction(lab.root / "kokoro/models", outside)
    result = extras.remove_extra("kokoro", purge_models=True, manifest=lab.manifest, home=lab.home)
    assert result["status"] == "not_installed" and result["models_kept"] is False
    assert not (lab.root / "kokoro").exists()
    assert (outside / "ajeno.bin").read_bytes() == b"not ours"


# ── Packaged runtime: profile only, never the installation ──────────


@pytest.fixture
def packaged(tmp_path, monkeypatch):
    installation = tmp_path / "instalación"
    for relative, content in {
        "iris/core/desktop.py": "# anchor\n", "iris/python/python.exe": "bundled python",
        "hermes/hermes_cli/__init__.py": "# anchor\n", "hermes/python/python.exe": "hermes python",
        "hermes/cli.py": "# cli\n", "tools/audio/bin/ffmpeg.exe": "decoder",
        "tools/audio/bin/ffprobe.exe": "probe", "tools/audio/bin/avcodec.dll": "codec",
    }.items():
        target = installation / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    (installation / "iris/face").mkdir()
    (installation / "hermes/pin.json").write_text(
        json.dumps({"schema_version": 1, "version": "0.21.4", "revision": "b" * 40}), encoding="utf-8")
    descriptor = installation / "runtime.json"
    descriptor.write_text(json.dumps({
        "schema_version": 2, "mode": "packaged",
        "iris": {"root": "iris", "python": "iris/python/python.exe", "assets": "iris/face", "revision": "a" * 40},
        "hermes": {"root": "hermes", "python": "hermes/python/python.exe", "cli": "hermes/cli.py",
                   "version": "0.21.4", "revision": "b" * 40, "metadata": "hermes/pin.json"},
    }), encoding="utf-8")
    monkeypatch.setenv(policy.DESCRIPTOR_ENV, str(descriptor))
    return installation


def test_packaged_install_success_and_failure_never_touch_the_installation(lab, packaged, monkeypatch):
    before = tree(packaged)
    commands = []

    def probe(site, modules, cancel, timeout=0):
        commands.append(extras.worker_command(site, "--probe", ",".join(modules)))
        return {"ok": True}

    monkeypatch.setattr(extras, "run_probe", probe)
    assert policy.runtime_capability_allowed("profile_extras") is True
    install(lab)
    lab.index.faults[lab.fake["url"]] = "corrupt"
    extras.remove_extra("kokoro", manifest=lab.manifest, home=lab.home)
    with pytest.raises(extras.ExtrasError):
        install(lab)
    assert tree(packaged) == before
    command = commands[0]
    assert command[0] == str(packaged.resolve() / "iris/python/python.exe")
    assert command[1:4] == ["-I", "-S", "-X"] and "pip" not in " ".join(command)
    env = extras.worker_environment(lab.root / "kokoro/models")
    assert env["PATH"].split(os.pathsep)[0] == str(packaged.resolve() / "tools/audio/bin")
    assert env["HF_HOME"] == str(lab.root / "kokoro/models/huggingface")
    assert "PYTHONPATH" not in env and env.get(policy.DESCRIPTOR_ENV)


def test_packaged_profile_inside_installation_is_rejected(lab, packaged):
    with pytest.raises(ProfilePathError):
        extras.extras_paths(packaged / "iris")


def test_packaged_tts_never_imports_local_engines_into_core(lab, packaged, monkeypatch):
    from core import tts

    class Forbidden:
        def __init__(self, **_options):
            raise AssertionError("local engine imported into Core")

    monkeypatch.setattr(tts, "LOCAL_ENGINE_PROVIDERS", {name: Forbidden for name in tts.LOCAL_ENGINE_PROVIDERS})
    monkeypatch.setattr(extras, "resolve_environment", lambda _name: None)
    engine = tts.TextToSpeech(IRISConfig(tts_provider="kokoro"))
    asyncio.run(engine.initialize())
    assert isinstance(engine._provider, tts.UnavailableExtraTTSProvider)
    assert asyncio.run(engine.speak("hola")) == (b"FALLBACK_FAILED", [])
    environment = extras.ExtraEnvironment("kokoro", lab.home, lab.home, lab.home, lab.home, lab.home)
    monkeypatch.setattr(extras, "resolve_environment", lambda _name: environment)
    engine = tts.TextToSpeech(IRISConfig(tts_provider="hf-s2s"))
    provider = engine._local_engine_provider("hf-s2s")
    assert isinstance(provider, tts.IsolatedExtraTTSProvider)
    assert provider.options["model_name"] == "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice"


def test_development_without_profile_extra_keeps_in_process_engines(monkeypatch):
    from core import tts

    monkeypatch.delenv(policy.DESCRIPTOR_ENV, raising=False)
    monkeypatch.setattr(extras, "resolve_environment", lambda _name: None)
    provider = tts.TextToSpeech(IRISConfig(tts_provider="kokoro"))._local_engine_provider("kokoro")
    assert isinstance(provider, tts.KokoroTTSProvider)


# ── Real worker: the consumer synthesises through the isolated env ──


FAKE_KOKORO = b'''
import sys
import numpy as np

class KPipeline:
    def __init__(self, lang_code):
        assert lang_code == "e"
        # Proof of isolation from inside the worker process.
        import importlib.util
        assert importlib.util.find_spec("fastapi") is None
        assert importlib.util.find_spec("core") is None
        print("stray library output on stdout")

    def __call__(self, text, voice, speed=1.0):
        assert voice == "ef_dora"
        yield text, "", np.full(2400, 0.25, dtype=np.float32)
'''


def test_core_tts_synthesises_through_the_isolated_worker(lab, monkeypatch):
    from core import tts

    # A typical short profile path: pytest's own temporary path is deep enough
    # to exceed MAX_PATH with NumPy's headers, which test_path_too_long covers.
    short = Path(tempfile.mkdtemp(prefix="ix", dir=tempfile.gettempdir())).resolve()
    lab.home = short
    lab.root = short / "extras"
    numpy_wheel = repack_installed(lab.wheels, "numpy")
    packages = [lab.index.add(numpy_wheel),
                lab.index.add(build_wheel(lab.wheels, "kokoro", "0.0.1", {"kokoro/__init__.py": FAKE_KOKORO}))]
    manifest = make_manifest(lab.data_dir, {"kokoro": write_lock(lab.data_dir, "kokoro", packages)},
                             probe={"kokoro": ["kokoro", "numpy"]})
    install(lab, manifest=manifest)
    environment = extras.resolve_environment("kokoro", manifest=manifest, home=lab.home)
    monkeypatch.setattr(extras, "resolve_environment", lambda _name: environment)

    async def scenario():
        engine = tts.TextToSpeech(IRISConfig(tts_provider="kokoro", kokoro_voice="ef_dora"))
        await engine.initialize()
        assert isinstance(engine._provider, tts.IsolatedExtraTTSProvider)
        first = await engine.speak("Hola desde el entorno aislado")
        second = await engine.speak("Segunda frase")
        await engine.close()
        return first, second

    (audio, visemes), (again, _) = asyncio.run(scenario())
    assert visemes == [] and again[:4] == b"RIFF"
    with wave.open(io.BytesIO(audio), "rb") as wav:
        assert wav.getframerate() == 24000 and wav.getnchannels() == 1 and wav.getnframes() == 2400
        assert int.from_bytes(wav.readframes(1), "little", signed=True) == int(0.25 * 32767)
    assert (lab.root / "kokoro/pycache").is_dir()  # bytecode stays in the profile
    extras.remove_extra("kokoro", purge_models=True, manifest=manifest, home=short)
    assert not (short / "extras/kokoro").exists()
    extras._remove_tree(short)


@pytest.mark.skipif(os.name != "nt", reason="eSpeak-NG narrow paths are a Windows issue")
def test_kokoro_worker_reaches_espeak_data_from_a_non_ascii_profile(tmp_path, monkeypatch):
    from core import tts_engines

    loader = tmp_path / "perfil José ñ" / "site" / "espeakng_loader"
    (loader / "espeak-ng-data").mkdir(parents=True)
    calls = []

    class Wrapper:
        @staticmethod
        def set_data_path(value):
            calls.append(value)

    fakes = {
        "espeakng_loader": SimpleNamespace(get_data_path=lambda: str(loader / "espeak-ng-data")),
        "misaki": SimpleNamespace(espeak=SimpleNamespace()),
        "misaki.espeak": SimpleNamespace(),
        "phonemizer": SimpleNamespace(), "phonemizer.backend": SimpleNamespace(),
        "phonemizer.backend.espeak": SimpleNamespace(),
        "phonemizer.backend.espeak.wrapper": SimpleNamespace(EspeakWrapper=Wrapper),
    }
    for name, module in fakes.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PHONEMIZER_ESPEAK_DATA_PATH", "C:/elsewhere")
    assert tts_engines.use_relative_espeak_data() is True
    assert Path.cwd() == loader and os.environ["ESPEAK_DATA_PATH"] == "."
    assert "PHONEMIZER_ESPEAK_DATA_PATH" not in os.environ and calls == [None]
    ascii_loader = tmp_path / "ascii" / "espeakng_loader"
    (ascii_loader / "espeak-ng-data").mkdir(parents=True)
    fakes["espeakng_loader"].get_data_path = lambda: str(ascii_loader / "espeak-ng-data")
    monkeypatch.chdir(tmp_path)
    assert tts_engines.use_relative_espeak_data() is False and Path.cwd() == tmp_path


def test_only_the_worker_changes_directory_for_espeak():
    worker = (ROOT / "core/extras_worker.py").read_text(encoding="utf-8")
    assert 'if engine == "kokoro":' in worker and "use_relative_espeak_data()" in worker
    for module in ("core/tts.py", "core/tts_engines.py"):
        source = (ROOT / module).read_text(encoding="utf-8")
        calls = re.findall(r"(?<!def )use_relative_espeak_data\(\)", source)
        assert calls == []


def test_crashed_worker_returns_no_audio_and_restarts_on_next_utterance(lab, monkeypatch):
    from core import tts

    starts = []

    class Worker:
        running = False

        def __init__(self, environment):
            starts.append(environment)

        async def start(self):
            self.running = True

        async def request(self, payload, *, timeout):
            if payload["op"] == "load":
                return {"ok": True}
            if len(starts) == 1:
                self.running = False
                raise extras.ExtrasError("worker_failed", "crash")
            return {"ok": True, "audio": base64.b64encode(b"RIFFok").decode(), "visemes": []}

        async def close(self):
            self.running = False

    monkeypatch.setattr(extras, "ExtraWorker", Worker)
    provider = tts.IsolatedExtraTTSProvider("kokoro", {"voice": "ef_dora", "lang": "es"}, object())
    assert asyncio.run(provider.synthesize("uno")) == (b"", [])
    assert asyncio.run(provider.synthesize("dos")) == (b"RIFFok", [])
    assert len(starts) == 2


def test_slow_engine_budget_extends_the_synthesis_timeout_and_keeps_the_last_error(monkeypatch):
    from core import tts

    seen = []

    class Worker:
        running = False

        def __init__(self, environment):
            pass

        async def start(self):
            self.running = True

        async def request(self, payload, *, timeout):
            seen.append((payload["op"], timeout))
            if payload["op"] == "load":
                return {"ok": True, "seconds_per_char": 1.5}
            if payload["text"] == "falla":
                return {"ok": False, "error": "no_audio", "detail": "Qwen3-TTS no pudo generar el audio: X"}
            return {"ok": True, "audio": base64.b64encode(b"RIFFok").decode(), "visemes": []}

        async def close(self):
            self.running = False

    monkeypatch.setattr(extras, "ExtraWorker", Worker)
    provider = tts.IsolatedExtraTTSProvider("hf-s2s", {}, object())
    assert asyncio.run(provider.synthesize("a" * 400)) == (b"RIFFok", [])
    assert seen[-1] == ("synthesize", 600.0)   # 400 characters x 1.5 s
    asyncio.run(provider.synthesize("corto"))
    assert seen[-1] == ("synthesize", tts.IsolatedExtraTTSProvider.SYNTH_TIMEOUT)
    assert asyncio.run(provider.synthesize("falla")) == (b"", [])
    assert provider.last_error == "Qwen3-TTS no pudo generar el audio: X"
    asyncio.run(provider.synthesize("bien"))
    assert provider.last_error == ""


def test_a_failed_load_reason_reaches_the_tts_selector(monkeypatch):
    from core import tts

    class Worker:
        running = False

        def __init__(self, environment):
            pass

        async def start(self):
            self.running = True

        async def request(self, payload, *, timeout):
            return {"ok": False, "error": "engine_unavailable", "detail": "Qwen3-TTS no pudo cargar M: E"}

        async def close(self):
            self.running = False

    monkeypatch.setattr(extras, "ExtraWorker", Worker)
    engine = tts.TextToSpeech(IRISConfig(tts_provider="hf-s2s"))
    engine._provider = tts.IsolatedExtraTTSProvider("hf-s2s", {}, object())
    assert asyncio.run(engine._provider.synthesize("hola")) == (b"", [])
    assert engine.last_error == "Qwen3-TTS no pudo cargar M: E"


def test_worker_reports_only_a_sane_synthesis_budget():
    import importlib.util

    spec = importlib.util.spec_from_file_location("extras_worker_under_test", ROOT / "core" / "extras_worker.py")
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)

    def provider_with(budget):
        class Provider:
            seconds_per_char = budget

            def __init__(self, **_options):
                pass

            async def initialize(self):
                self._handler, self._input_type = object(), object()
        return Provider

    for budget, expected in ((1.2, 1.2), (0.0, None), (50.0, None)):
        session = worker._Session(worker._LastError())
        session.engines = SimpleNamespace(HFSpeechToSpeechQwenProvider=provider_with(budget))
        reply = session.load("hf-s2s", {"model_name": "Qwen/Qwen3-TTS-12Hz-0.6B-Base"})
        assert reply["ok"] is True and reply.get("seconds_per_char") == expected


# ── Wizard endpoints and diagnostics ────────────────────────────────


@pytest.fixture
def wizard_client(lab, monkeypatch):
    from wizard import app as wizard

    monkeypatch.setattr(extras, "load_manifest", lambda path=None: lab.manifest)
    monkeypatch.setenv("IRIS_HOME", str(lab.home))
    real_install = extras.install_extra

    def install_with_index(extra_id, **kwargs):
        return real_install(extra_id, home=lab.home, transport=lab.index.transport,
                            manifest=lab.manifest, **kwargs)

    monkeypatch.setattr(extras, "install_extra", install_with_index)
    return wizard


async def _request(wizard, method, url, **kwargs):
    transport = httpx.ASGITransport(app=wizard.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        return await client.request(method, url, **kwargs)


def _events(response) -> list[dict]:
    return [json.loads(line[5:]) for line in response.text.splitlines() if line.startswith("data:")]


def test_wizard_status_install_and_remove_flow(wizard_client, lab):
    wizard = wizard_client
    status = asyncio.run(_request(wizard, "GET", "/api/check-extras")).json()
    assert status["extras"]["kokoro"]["state"] == "not_installed"
    assert status["unsupported"]["neutts"] and status["runtime_allowed"] is True
    refused = asyncio.run(_request(wizard, "POST", "/api/install-extra-stream?extra=kokoro"))
    assert refused.status_code == 403 and lab.index.calls == []
    response = asyncio.run(_request(wizard, "POST", "/api/install-extra-stream?extra=kokoro",
                                    headers={"X-IRIS-Intent": "install-extra"}))
    events = _events(response)
    assert events[-1]["type"] == "done" and events[-1]["ok"] is True
    assert asyncio.run(_request(wizard, "GET", "/api/check-extras")).json()["extras"]["kokoro"]["state"] == "installed"
    repair = _events(asyncio.run(_request(wizard, "POST", "/api/install-extra-stream?extra=kokoro&action=repair",
                                          headers={"X-IRIS-Intent": "install-extra"})))
    assert repair[-1] == {"type": "done", "ok": True, "changed": False, "verified": True, "returncode": 0}
    unsupported = _events(asyncio.run(_request(wizard, "POST", "/api/install-extra-stream?extra=neutts",
                                               headers={"X-IRIS-Intent": "install-extra"})))
    assert unsupported[-1]["type"] == "error" and unsupported[-1]["code"] == "unsupported"
    denied = asyncio.run(_request(wizard, "POST", "/api/extras/remove", json={"extra": "kokoro"}))
    assert denied.status_code == 403
    removed = asyncio.run(_request(wizard, "POST", "/api/extras/remove", json={"extra": "kokoro"},
                                   headers={"X-IRIS-Intent": "remove-extra"}))
    assert removed.json()["ok"] is True
    assert asyncio.run(_request(wizard, "GET", "/api/check-extras")).json()["extras"]["kokoro"]["state"] == "not_installed"


def test_wizard_cancel_endpoint_stops_the_running_operation(wizard_client, lab):
    wizard = wizard_client
    cancelled = threading.Event()
    wizard._EXTRA_OPERATIONS["kokoro"] = cancelled
    try:
        missing_intent = asyncio.run(_request(wizard, "POST", "/api/extras/cancel?extra=kokoro"))
        assert missing_intent.status_code == 403 and not cancelled.is_set()
        response = asyncio.run(_request(wizard, "POST", "/api/extras/cancel?extra=kokoro",
                                        headers={"X-IRIS-Intent": "cancel-extra"}))
        assert response.json() == {"ok": True, "cancelling": True} and cancelled.is_set()
        busy = _events(asyncio.run(_request(wizard, "POST", "/api/install-extra-stream?extra=kokoro",
                                            headers={"X-IRIS-Intent": "install-extra"})))
        assert busy[-1]["code"] == "busy"
    finally:
        wizard._EXTRA_OPERATIONS.pop("kokoro", None)


def test_wizard_ui_has_lifecycle_actions_and_no_base_installer():
    script = (ROOT / "wizard" / "ui" / "wizard.js").read_text(encoding="utf-8")
    backend = (ROOT / "wizard" / "app.py").read_text(encoding="utf-8")
    assert "_runtimeOperationAllowed('profile_extras')" in script
    for intent in ("'X-IRIS-Intent': 'install-extra'", "'X-IRIS-Intent': 'cancel-extra'",
                   "'X-IRIS-Intent': 'remove-extra'"):
        assert intent in script
    assert "faster-whisper" not in script and "base_package_install" not in script
    assert '"-m", "pip"' not in backend and '"install", "--upgrade"' not in backend
    assert "EXTRA_SPECS" not in backend


@pytest.mark.parametrize("state,expected", [("installed", "ok"), ("broken", "fail"), ("outdated", "fail"),
                                            ("not_installed", "inactive")])
def test_health_check_reports_the_real_voice_extra_state(monkeypatch, state, expected):
    from wizard import health_check

    monkeypatch.delenv(policy.DESCRIPTOR_ENV, raising=False)
    monkeypatch.setattr(extras, "extras_status", lambda: {
        "extras": {"kokoro": {"label": "Kokoro", "consumer": "tts:kokoro", "state": state, "detail": "x"}},
        "unsupported": {"neutts": "sin wheels"},
    })
    assert asyncio.run(health_check.check_voice_extra("kokoro"))["status"] == expected
    assert asyncio.run(health_check.check_voice_extra("edge-tts"))["status"] == "skip"
    assert asyncio.run(health_check.check_voice_extra("neutts"))["status"] == "fail"
