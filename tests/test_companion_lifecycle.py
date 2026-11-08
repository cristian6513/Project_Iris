"""Focused tests for the optional Windows Companion lifecycle."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import core.companion_lifecycle as lifecycle


@pytest.fixture(autouse=True)
def _isolate_companion_profile(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Lifecycle tests must never leave install journals in the real profile."""
    isolated_home = tmp_path / "isolated-iris-home"
    monkeypatch.setattr(lifecycle, "get_iris_home", lambda: isolated_home)
    # Unit cases must also be independent from a real Companion registered on
    # the Windows host running pytest. Individual registry scenarios replace
    # these stubs explicitly.
    monkeypatch.setattr(lifecycle, "_registry_installation", lambda: (None, "", ""))
    monkeypatch.setattr(lifecycle, "_registry_residue", lambda: {})


def _write_fake_pe(path: Path, *, machine: int = 0x8664, payload: bytes = b"") -> Path:
    data = bytearray(65_536 + len(payload))
    data[:2] = b"MZ"
    data[0x3C:0x40] = (0x80).to_bytes(4, "little")
    data[0x80:0x84] = b"PE\0\0"
    data[0x84:0x86] = machine.to_bytes(2, "little")
    if payload:
        data[-len(payload):] = payload
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _local_client() -> TestClient:
    app = FastAPI()
    app.include_router(lifecycle.router)
    return TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 51999))


def _fixture_package(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    installer = tmp_path / "IRIS Desktop Companion_0.1.0_x64-setup.exe"
    _write_fake_pe(installer, payload=b"trusted local installer")
    manifest = tmp_path / "release-manifest.json"
    manifest.write_text(json.dumps({
        "schema": 1,
        "product": "IRIS Desktop Companion",
        "version": "0.1.0",
        "platform": "windows",
        "arch": "x64",
        "filename": installer.name,
        "sha256": hashlib.sha256(installer.read_bytes()).hexdigest(),
        "signed": False,
        "webview2_mode": "downloadBootstrapper",
    }), encoding="utf-8")
    monkeypatch.setattr(lifecycle, "_RELEASE_MANIFEST", manifest)
    monkeypatch.setenv("IRIS_COMPANION_INSTALLER", str(installer))
    return installer


def _windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lifecycle.platform, "system", lambda: "Windows")
    monkeypatch.setattr(lifecycle.platform, "machine", lambda: "AMD64")


def test_nsis_registry_location_removes_only_balanced_outer_quotes(tmp_path: Path) -> None:
    installed = tmp_path / "IRIS Desktop Companion"

    assert lifecycle._normalise_registry_location(f'  "{installed}"  ') == installed
    assert lifecycle._normalise_registry_location(str(installed)) == installed
    assert lifecycle._normalise_registry_location(f'"{installed}') is None
    assert lifecycle._normalise_registry_location("relative/path") is None


def test_nsis_install_destination_is_last_and_unquoted(tmp_path: Path) -> None:
    installer = tmp_path / "IRIS Companion setup.exe"
    destination = tmp_path / "Profile With Spaces" / ".iris" / "companion" / "app"

    command, application = lifecycle._nsis_install_invocation(installer, destination)

    assert application == str(installer.resolve(strict=False))
    assert command == f'"{application}" /NS /S /D={destination.resolve(strict=False)}'
    assert not command.partition(" /D=")[2].startswith('"')
    assert command.endswith(str(destination.resolve(strict=False)))


def test_package_requires_a_manifest_hash_match(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    installer = _fixture_package(monkeypatch, tmp_path)
    assert lifecycle._package_diagnostic()["trusted"] is True

    _write_fake_pe(installer, payload=b"replaced")
    diagnostic = lifecycle._package_diagnostic()
    assert diagnostic["available"] is True
    assert diagnostic["trusted"] is False
    assert "SHA-256" in diagnostic["error"]


def test_x86_nsis_bootstrapper_can_carry_an_x64_payload(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    installer = _fixture_package(monkeypatch, tmp_path)
    _write_fake_pe(installer, machine=0x014C, payload=b"NSIS x86 bootstrapper")
    manifest = json.loads((tmp_path / "release-manifest.json").read_text(encoding="utf-8"))
    manifest["sha256"] = hashlib.sha256(installer.read_bytes()).hexdigest()
    (tmp_path / "release-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    diagnostic = lifecycle._package_diagnostic()
    assert diagnostic["trusted"] is True
    assert diagnostic["arch"] == "x64"
    assert diagnostic["installer_arch"] == "x86"


def test_invalid_override_does_not_hide_valid_packaged_candidate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    valid = _fixture_package(monkeypatch, tmp_path)
    invalid = _write_fake_pe(tmp_path / "obsolete.exe", payload=b"obsolete")
    monkeypatch.setenv("IRIS_COMPANION_INSTALLER", str(invalid))
    monkeypatch.setattr(lifecycle, "_installer_candidates", lambda _: [invalid, valid])

    diagnostic = lifecycle._package_diagnostic()
    assert diagnostic["trusted"] is True
    assert diagnostic["filename"] == valid.name


def test_invalid_manifest_override_does_not_hide_valid_release_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _fixture_package(monkeypatch, tmp_path)
    valid_manifest = tmp_path / "release-manifest.json"
    invalid_manifest = tmp_path / "invalid-manifest.json"
    invalid_manifest.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        lifecycle,
        "_manifest_candidates",
        lambda: [invalid_manifest, valid_manifest],
    )

    assert lifecycle._package_diagnostic()["trusted"] is True


def test_lifecycle_never_promises_a_download_or_build(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _windows(monkeypatch)
    monkeypatch.setattr(lifecycle, "get_iris_home", lambda: tmp_path / ".iris")
    monkeypatch.setattr(lifecycle, "_RELEASE_MANIFEST", tmp_path / "absent.json")
    monkeypatch.setattr(lifecycle, "_webview2_diagnostic", lambda: {"available": False, "version": "", "method": "not_found"})
    monkeypatch.setattr(lifecycle, "_core_diagnostic", lambda: {"available": False, "port": 8765, "transport": "loopback"})

    report = lifecycle.companion_lifecycle_diagnostic()
    assert report["action"] == "unavailable"
    assert report["automatic_download"] is False
    assert report["automatic_build"] is False


def test_core_diagnostic_requires_the_identified_iris_status(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class Response:
        def __init__(self, payload: dict[str, object]) -> None:
            self.payload = payload

        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self, _: int) -> bytes:
            return json.dumps(self.payload).encode()

    monkeypatch.setattr(lifecycle, "get_iris_home", lambda: tmp_path)
    monkeypatch.setattr(lifecycle.IRISConfig, "load", lambda: SimpleNamespace(websocket_port=8765))
    opener = SimpleNamespace(
        open=lambda *args, **kwargs: Response({"name": "otro servicio", "status": "running"})
    )
    handlers: list[object] = []
    monkeypatch.setattr(
        lifecycle.urllib.request,
        "build_opener",
        lambda *args: handlers.extend(args) or opener,
    )
    assert lifecycle._core_diagnostic()["available"] is False
    assert any(
        isinstance(handler, lifecycle.urllib.request.ProxyHandler)
        and handler.proxies == {}
        for handler in handlers
    )

    opener.open = lambda *args, **kwargs: Response({
        "name": "IRIS",
        "status": "running",
        "version": "1.2.3",
    })
    diagnostic = lifecycle._core_diagnostic()
    assert diagnostic["available"] is True
    assert diagnostic["version"] == "1.2.3"


def test_running_installed_companion_must_close_before_reinstall(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _windows(monkeypatch)
    _fixture_package(monkeypatch, tmp_path)
    monkeypatch.setattr(lifecycle, "get_iris_home", lambda: tmp_path / ".iris")
    monkeypatch.setattr(lifecycle, "_installation_diagnostic", lambda: {
        "installed": True,
        "state": "installed",
        "version": "0.1.0",
        "source": "managed",
        "running": True,
        "running_known": True,
        "_executable": tmp_path / "iris-desktop-companion.exe",
    })
    monkeypatch.setattr(lifecycle, "_webview2_diagnostic", lambda: {"available": True, "version": "1", "method": "test"})
    monkeypatch.setattr(lifecycle, "_core_diagnostic", lambda: {"available": True, "port": 8765, "transport": "loopback"})

    assert lifecycle.companion_lifecycle_diagnostic()["action"] == "close_required"


def test_newer_installation_is_never_offered_an_automatic_downgrade(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _windows(monkeypatch)
    _fixture_package(monkeypatch, tmp_path)
    monkeypatch.setattr(lifecycle, "get_iris_home", lambda: tmp_path / ".iris")
    monkeypatch.setattr(lifecycle, "_installation_diagnostic", lambda: {
        "installed": True,
        "state": "installed",
        "version": "0.10.0",
        "source": "managed",
        "running": False,
        "running_known": True,
        "_executable": tmp_path / "iris-desktop-companion.exe",
    })
    monkeypatch.setattr(lifecycle, "_webview2_diagnostic", lambda: {"available": True, "version": "1", "method": "test"})
    monkeypatch.setattr(lifecycle, "_core_diagnostic", lambda: {"available": False, "port": 8765, "transport": "loopback"})

    report = lifecycle.companion_lifecycle_diagnostic()
    assert report["installation"]["package_older"] is True
    assert report["installation"]["update_available"] is False
    assert report["action"] == "ready"


@pytest.mark.parametrize("installed_version,running_known", [
    ("0.2.0", True),
    ("0.1.0", False),
])
def test_direct_install_blocks_downgrade_and_unknown_process_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    installed_version: str,
    running_known: bool,
) -> None:
    _windows(monkeypatch)
    _fixture_package(monkeypatch, tmp_path)
    monkeypatch.setattr(lifecycle, "_installation_diagnostic", lambda: {
        "installed": True,
        "state": "installed",
        "version": installed_version,
        "source": "managed",
        "running": False,
        "running_known": running_known,
        "_executable": tmp_path / "iris-desktop-companion.exe",
    })
    run = Mock()
    monkeypatch.setattr(lifecycle.subprocess, "run", run)

    with pytest.raises(Exception) as error:
        lifecycle._install_companion()
    assert getattr(error.value, "status_code", None) == 409
    run.assert_not_called()


def test_direct_install_blocks_an_unwritable_machine_scope_update(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _windows(monkeypatch)
    _fixture_package(monkeypatch, tmp_path)
    monkeypatch.setattr(lifecycle, "_installation_diagnostic", lambda: {
        "installed": True,
        "state": "installed",
        "version": "0.0.9",
        "source": "registry",
        "registry_scope": "machine",
        "registry_update_supported": False,
        "running": False,
        "running_known": True,
        "_executable": tmp_path / "Program Files" / "iris-desktop-companion.exe",
    })
    run = Mock()
    monkeypatch.setattr(lifecycle.subprocess, "run", run)

    with pytest.raises(Exception) as error:
        lifecycle._install_companion()
    assert getattr(error.value, "status_code", None) == 409
    run.assert_not_called()


def test_manifest_signature_declaration_is_not_treated_as_verification(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "setup.exe"
    artifact.write_bytes(b"signed-looking")
    monkeypatch.setattr(lifecycle.shutil, "which", lambda _: None)
    signed, status = lifecycle._authenticode_status(artifact, True)
    assert signed is False
    assert status == "unverified"


def test_install_rejects_missing_intent_and_nonlocal_peer(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _local_client()
    missing = client.post("/api/companion/install")
    assert missing.status_code == 403

    app = FastAPI()
    app.include_router(lifecycle.router)
    remote = TestClient(app, base_url="http://127.0.0.1", client=("192.168.1.20", 51999))
    denied = remote.post(
        "/api/companion/install",
        headers={"X-IRIS-Intent": "install-companion"},
    )
    assert denied.status_code == 403


def test_install_rejects_dns_rebinding_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lifecycle, "_install_companion", Mock())
    app = FastAPI()
    app.include_router(lifecycle.router)
    attacker = TestClient(
        app,
        base_url="http://attacker.example:8765",
        client=("127.0.0.1", 51999),
    )

    response = attacker.post(
        "/api/companion/install",
        headers={
            "X-IRIS-Intent": "install-companion",
            "Origin": "http://attacker.example:8765",
            "Sec-Fetch-Site": "same-origin",
        },
    )
    assert response.status_code == 403
    lifecycle._install_companion.assert_not_called()


def test_install_rejects_a_different_loopback_origin_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lifecycle, "_install_companion", Mock())
    client = _local_client()
    response = client.post(
        "/api/companion/install",
        headers={
            "X-IRIS-Intent": "install-companion",
            "Origin": "http://127.0.0.1:3000",
            "Sec-Fetch-Site": "same-site",
        },
    )
    assert response.status_code == 403
    lifecycle._install_companion.assert_not_called()


def test_lifecycle_lock_rejects_a_second_operation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(lifecycle.config_module, "get_iris_home", lambda: tmp_path)
    with lifecycle.companion_operation_lock():
        with pytest.raises(Exception) as busy:
            with lifecycle.companion_operation_lock():
                pass
    assert getattr(busy.value, "status_code", None) == 409


def test_lifecycle_lock_is_shared_between_processes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(lifecycle.config_module, "get_iris_home", lambda: tmp_path)
    child = """
import sys
from pathlib import Path
from fastapi import HTTPException
import core.companion_lifecycle as lifecycle
lifecycle.config_module.get_iris_home = lambda: Path(sys.argv[1])
try:
    with lifecycle.companion_operation_lock():
        pass
except HTTPException as exc:
    raise SystemExit(23 if exc.status_code == 409 else 24)
raise SystemExit(25)
"""
    with lifecycle.companion_operation_lock():
        result = subprocess.run(
            [sys.executable, "-c", child, str(tmp_path)],
            cwd=Path(__file__).resolve().parents[1],
            check=False,
            timeout=20,
        )
    assert result.returncode == 23


def test_managed_install_validates_output_and_records_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _windows(monkeypatch)
    _fixture_package(monkeypatch, tmp_path)
    iris_home = tmp_path / ".iris"
    monkeypatch.setattr(lifecycle, "get_iris_home", lambda: iris_home)
    monkeypatch.setattr(lifecycle, "_registry_installation", lambda: (None, "", ""))
    monkeypatch.setattr(lifecycle, "_process_state", lambda executable: (False, True))
    monkeypatch.setattr(lifecycle, "_is_expected_companion_binary", lambda path, version="": lifecycle._pe_architecture(path) == "x64")
    monkeypatch.setattr(lifecycle, "_startup_path", lambda: None)
    monkeypatch.setattr(lifecycle.IRISConfig, "load", lambda: SimpleNamespace(companion_autostart=False))
    monkeypatch.setattr(lifecycle, "_save_companion_flags", Mock(return_value=""))

    def fake_run(command: str, **kwargs: object) -> SimpleNamespace:
        assert kwargs.get("executable")
        marker = " /S /D="
        assert marker in command
        install_dir = Path(command.partition(marker)[2])
        assert command.endswith(str(install_dir))
        install_dir.mkdir(parents=True, exist_ok=True)
        _write_fake_pe(install_dir / "iris-desktop-companion.exe", payload=b"native")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(lifecycle.subprocess, "run", fake_run)
    lifecycle._install_companion()

    record = json.loads((iris_home / "companion_install.json").read_text(encoding="utf-8"))
    assert record["schema"] == 1
    assert record["version"] == "0.1.0"
    assert Path(record["executable"]).is_file()
    assert not (iris_home / "companion_installing.json").exists()


def test_failed_installer_leaves_a_repair_journal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _windows(monkeypatch)
    _fixture_package(monkeypatch, tmp_path)
    iris_home = tmp_path / ".iris"
    monkeypatch.setattr(lifecycle, "get_iris_home", lambda: iris_home)
    monkeypatch.setattr(lifecycle, "_registry_installation", lambda: (None, "", ""))
    monkeypatch.setattr(lifecycle, "_startup_path", lambda: None)
    monkeypatch.setattr(lifecycle.IRISConfig, "load", lambda: SimpleNamespace(companion_autostart=False))
    monkeypatch.setattr(
        lifecycle.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=7),
    )

    with pytest.raises(Exception) as error:
        lifecycle._install_companion()
    assert getattr(error.value, "status_code", None) == 500
    assert (iris_home / "companion_installing.json").is_file()
    assert lifecycle._installation_diagnostic()["state"] == "damaged"


def test_corrupt_managed_install_cannot_fall_back_to_registry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    iris_home = tmp_path / ".iris"
    executable = _write_fake_pe(iris_home / "companion" / "app" / "iris-desktop-companion.exe")
    record = {
        "schema": 1,
        "product": "IRIS Desktop Companion",
        "version": "0.1.0",
        "executable": str(executable),
        "executable_arch": "x64",
        "executable_size": executable.stat().st_size,
        "executable_sha256": "0" * 64,
    }
    iris_home.mkdir(parents=True, exist_ok=True)
    (iris_home / "companion_install.json").write_text(json.dumps(record), encoding="utf-8")
    registry = Mock(return_value=(executable, "0.1.0", "user"))
    monkeypatch.setattr(lifecycle, "get_iris_home", lambda: iris_home)
    monkeypatch.setattr(lifecycle, "_registry_installation", registry)

    diagnostic = lifecycle._installation_diagnostic()
    assert diagnostic["state"] == "damaged"
    assert diagnostic["installed"] is False
    registry.assert_not_called()


def test_repair_blocks_a_corrupt_companion_that_is_still_running(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _windows(monkeypatch)
    _fixture_package(monkeypatch, tmp_path)
    iris_home = tmp_path / ".iris"
    executable = _write_fake_pe(
        iris_home / "companion" / "app" / "iris-desktop-companion.exe"
    )
    record = {
        "schema": 1,
        "product": "IRIS Desktop Companion",
        "version": "0.1.0",
        "executable": str(executable),
        "executable_arch": "x64",
        "executable_size": executable.stat().st_size,
        "executable_sha256": "0" * 64,
    }
    iris_home.mkdir(parents=True, exist_ok=True)
    (iris_home / "companion_install.json").write_text(json.dumps(record), encoding="utf-8")
    monkeypatch.setattr(lifecycle, "get_iris_home", lambda: iris_home)
    monkeypatch.setattr(lifecycle, "_registry_installation", Mock())
    monkeypatch.setattr(lifecycle, "_process_state", lambda candidate: (True, True))
    run = Mock()
    monkeypatch.setattr(lifecycle.subprocess, "run", run)

    diagnostic = lifecycle._installation_diagnostic()
    assert diagnostic["state"] == "damaged"
    assert diagnostic["running"] is True
    with pytest.raises(Exception) as error:
        lifecycle._install_companion()
    assert getattr(error.value, "status_code", None) == 409
    run.assert_not_called()


def test_interrupted_install_is_reported_as_damaged(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    iris_home = tmp_path / ".iris"
    iris_home.mkdir()
    (iris_home / "companion_installing.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(lifecycle, "get_iris_home", lambda: iris_home)
    monkeypatch.setattr(lifecycle, "_registry_installation", Mock())

    diagnostic = lifecycle._installation_diagnostic()
    assert diagnostic["state"] == "damaged"
    assert diagnostic["interrupted"] is True
    lifecycle._registry_installation.assert_not_called()


def test_completed_nsis_install_recovers_from_registry_and_retires_journal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    home = tmp_path / ".iris"
    installed_dir = tmp_path / "Installed Companion"
    executable = _write_fake_pe(installed_dir / "iris-desktop-companion.exe")
    home.mkdir()
    journal = home / "companion_installing.json"
    journal.write_text(json.dumps({
        "schema": 1,
        "product": "IRIS Desktop Companion",
        "version": "0.1.0",
        "destination": str(home / "companion" / "app"),
        "previous_source": "",
    }), encoding="utf-8")
    monkeypatch.setattr(lifecycle, "get_iris_home", lambda: home)
    monkeypatch.setattr(
        lifecycle,
        "_registry_installation",
        lambda: (executable, "0.1.0", "user"),
    )
    monkeypatch.setattr(lifecycle, "_process_state", lambda candidate: (False, True))

    diagnostic = lifecycle._installation_diagnostic()

    assert diagnostic["installed"] is True
    assert diagnostic["state"] == "installed"
    assert diagnostic["source"] == "registry"
    assert diagnostic["version"] == "0.1.0"
    assert diagnostic["interrupted"] is False
    assert diagnostic["_executable"] == executable
    assert not journal.exists()


def test_autostart_is_separate_reversible_and_requires_install(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _windows(monkeypatch)
    startup = tmp_path / "Startup" / "IRIS-Desktop-Companion.vbs"
    executable = tmp_path / "managed" / "iris-desktop-companion.exe"
    executable.parent.mkdir()
    _write_fake_pe(executable, payload=b"native")
    monkeypatch.setattr(lifecycle, "_startup_path", lambda: startup)
    monkeypatch.setattr(lifecycle, "_save_companion_flags", Mock())
    monkeypatch.setattr(lifecycle, "_installation_diagnostic", lambda: {
        "installed": True,
        "_executable": executable,
    })

    lifecycle._set_autostart(True)
    script = startup.read_text(encoding="utf-16")
    assert "WScript.Shell" in script
    assert str(executable) in script
    assert "Hermes" not in startup.name

    lifecycle._set_autostart(False)
    assert not startup.exists()
    lifecycle._set_autostart(False)

    monkeypatch.setattr(lifecycle, "_installation_diagnostic", lambda: {"installed": False, "_executable": None})
    with pytest.raises(Exception) as error:
        lifecycle._set_autostart(True)
    assert getattr(error.value, "status_code", None) == 409


def test_launch_is_idempotent_and_never_uses_a_shell(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _windows(monkeypatch)
    executable = tmp_path / "iris-desktop-companion.exe"
    _write_fake_pe(executable, payload=b"native")
    monkeypatch.setattr(lifecycle, "_save_companion_flags", Mock(return_value=""))
    popen = Mock()
    monkeypatch.setattr(lifecycle.subprocess, "Popen", popen)
    monkeypatch.setattr(lifecycle, "_installation_diagnostic", lambda: {
        "installed": True,
        "running": True,
        "running_known": True,
        "_executable": executable,
    })
    assert lifecycle._launch_companion() == ("focus_requested", "")
    popen.assert_called_once()
    popen.reset_mock()

    monkeypatch.setattr(lifecycle, "_installation_diagnostic", lambda: {
        "installed": True,
        "running": False,
        "running_known": True,
        "_executable": executable,
    })
    assert lifecycle._launch_companion()[0] == "launched"
    args, kwargs = popen.call_args
    assert args[0] == [str(executable)]
    assert "shell" not in kwargs


def test_launch_blocks_unknown_process_state_and_non_windows(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _windows(monkeypatch)
    executable = _write_fake_pe(tmp_path / "iris-desktop-companion.exe")
    monkeypatch.setattr(lifecycle, "_installation_diagnostic", lambda: {
        "installed": True,
        "running": False,
        "running_known": False,
        "_executable": executable,
    })
    with pytest.raises(Exception) as unknown:
        lifecycle._launch_companion()
    assert getattr(unknown.value, "status_code", None) == 409

    monkeypatch.setattr(lifecycle.platform, "system", lambda: "Linux")
    with pytest.raises(Exception) as unsupported:
        lifecycle._launch_companion()
    assert getattr(unsupported.value, "status_code", None) == 409


def test_stop_requests_a_graceful_native_exit_for_the_exact_installed_binary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _windows(monkeypatch)
    executable = _write_fake_pe(tmp_path / "iris-desktop-companion.exe")
    process = Mock()
    popen = Mock()
    monkeypatch.setattr(lifecycle.subprocess, "Popen", popen)
    monkeypatch.setattr(lifecycle, "_save_companion_flags", Mock(return_value=""))
    monkeypatch.setattr(lifecycle, "_installation_diagnostic", lambda: {
        "installed": True,
        "running": True,
        "running_known": True,
        "_executable": executable,
        "_process_candidate": executable,
        "_legacy_process_candidate": None,
    })
    monkeypatch.setattr(lifecycle, "_matching_companion_processes", lambda candidates: [process])
    monkeypatch.setattr(
        lifecycle,
        "_wait_for_companion_processes",
        lambda processes, timeout: (list(processes), []),
    )

    assert lifecycle._stop_companion() == ("stopped", "")
    args, kwargs = popen.call_args
    assert args[0] == [str(executable), "--iris-exit"]
    assert "shell" not in kwargs
    process.terminate.assert_not_called()
    process.kill.assert_not_called()


def test_stop_endpoint_requires_local_intent_and_returns_fresh_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stop = Mock(return_value=("stopped", ""))
    monkeypatch.setattr(lifecycle, "_stop_companion", stop)
    monkeypatch.setattr(
        lifecycle,
        "companion_lifecycle_diagnostic",
        lambda: {"action": "reinstall", "installation": {"running": False}},
    )
    client = _local_client()

    assert client.post("/api/companion/stop").status_code == 403
    response = client.post(
        "/api/companion/stop",
        headers={"X-IRIS-Intent": "stop-companion"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "stopped"
    assert response.json()["lifecycle"]["installation"]["running"] is False
    stop.assert_called_once_with()


def test_autostart_diagnostic_detects_a_stale_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    startup = tmp_path / "IRIS-Desktop-Companion.vbs"
    startup.write_text('shell.Run """C:\\old\\companion.exe"""', encoding="utf-8")
    executable = _write_fake_pe(tmp_path / "current" / "iris-desktop-companion.exe")
    monkeypatch.setattr(lifecycle, "_startup_path", lambda: startup)

    diagnostic = lifecycle._autostart_diagnostic({
        "installed": True,
        "_executable": executable,
    })
    assert diagnostic["enabled"] is False
    assert diagnostic["stale"] is True


def test_signed_manifest_requires_a_valid_authenticode_signature(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _fixture_package(monkeypatch, tmp_path)
    manifest_path = tmp_path / "release-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["signed"] = True
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(
        lifecycle,
        "_authenticode_status",
        lambda path, required: (False, "NotSigned"),
    )

    diagnostic = lifecycle._package_diagnostic()

    assert diagnostic["trusted"] is False
    assert "Authenticode" in diagnostic["error"]


def test_corrupt_record_version_cannot_block_its_own_repair_as_a_downgrade(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _windows(monkeypatch)
    _fixture_package(monkeypatch, tmp_path)
    home = tmp_path / ".iris"
    monkeypatch.setattr(lifecycle, "get_iris_home", lambda: home)
    monkeypatch.setattr(lifecycle, "_startup_path", lambda: None)
    monkeypatch.setattr(lifecycle.IRISConfig, "load", lambda: SimpleNamespace(companion_autostart=False))
    monkeypatch.setattr(lifecycle, "_installation_diagnostic", lambda: {
        "installed": False,
        "state": "damaged",
        "version": "999.0.0",
        "version_trusted": False,
        "source": "managed",
        "running": False,
        "running_known": True,
        "process_check_required": False,
        "_repair_directory": home / "companion" / "app",
        "_executable": None,
    })
    run = Mock(return_value=SimpleNamespace(returncode=7))
    monkeypatch.setattr(lifecycle.subprocess, "run", run)

    with pytest.raises(Exception) as error:
        lifecycle._install_companion()

    assert getattr(error.value, "status_code", None) == 500
    run.assert_called_once()


def test_damaged_running_installation_always_requests_close_first(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _windows(monkeypatch)
    _fixture_package(monkeypatch, tmp_path)
    monkeypatch.setattr(lifecycle, "_installation_diagnostic", lambda: {
        "installed": False,
        "state": "damaged",
        "version": "0.2.0",
        "version_trusted": True,
        "source": "registry",
        "registry_update_supported": True,
        "running": True,
        "running_known": True,
        "process_check_required": False,
        "_executable": None,
    })
    monkeypatch.setattr(lifecycle, "_webview2_diagnostic", lambda: {"available": True, "version": "1", "method": "test"})
    monkeypatch.setattr(lifecycle, "_core_diagnostic", lambda: {"available": True, "port": 8765, "transport": "loopback_http"})

    report = lifecycle.companion_lifecycle_diagnostic()

    assert report["installation"]["package_older"] is True
    assert report["action"] == "close_required"


def test_interrupted_registry_update_preserves_its_repair_destination(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    home = tmp_path / ".iris"
    destination = tmp_path / "Installed Companion"
    executable = destination / "iris-desktop-companion.exe"
    home.mkdir()
    destination.mkdir()
    (home / "companion_installing.json").write_text(json.dumps({
        "schema": 1,
        "product": "IRIS Desktop Companion",
        "destination": str(destination),
        "previous_source": "registry",
        "previous_version": "0.1.0",
        "previous_registry_scope": "user",
        "previous_executable": str(executable),
    }), encoding="utf-8")
    monkeypatch.setattr(lifecycle, "get_iris_home", lambda: home)
    registry = Mock()
    monkeypatch.setattr(lifecycle, "_registry_installation", registry)
    monkeypatch.setattr(lifecycle, "_process_state", lambda candidate: (False, True))

    diagnostic = lifecycle._installation_diagnostic()

    assert diagnostic["state"] == "damaged"
    assert diagnostic["source"] == "registry"
    assert diagnostic["_repair_directory"] == destination
    assert diagnostic["_process_candidate"] == executable
    registry.assert_not_called()


def test_migrated_record_detects_a_companion_still_running_from_legacy_home(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    home = tmp_path / ".iris"
    executable = _write_fake_pe(home / "companion" / "app" / "iris-desktop-companion.exe")
    legacy_executable = _write_fake_pe(
        tmp_path / ".alice" / "companion" / "app" / "iris-desktop-companion.exe"
    )
    record = {
        "schema": 1,
        "product": "IRIS Desktop Companion",
        "version": "0.1.0",
        "executable": str(executable),
        "legacy_executable": str(legacy_executable),
        "executable_arch": "x64",
        "executable_size": executable.stat().st_size,
        "executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
    }
    home.mkdir(exist_ok=True)
    (home / "companion_install.json").write_text(json.dumps(record), encoding="utf-8")
    monkeypatch.setattr(lifecycle, "get_iris_home", lambda: home)
    monkeypatch.setattr(lifecycle.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(lifecycle, "_process_state", lambda candidate: (
        candidate == legacy_executable,
        True,
    ))

    diagnostic = lifecycle._installation_diagnostic()

    assert diagnostic["installed"] is True
    assert diagnostic["running"] is True


def test_autostart_script_uses_unicode_safe_encoding(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _windows(monkeypatch)
    startup = tmp_path / "Inicio" / "IRIS-Desktop-Companion.vbs"
    executable = _write_fake_pe(
        tmp_path / "Compañía" / "iris-desktop-companion.exe",
        payload=b"native",
    )
    monkeypatch.setattr(lifecycle, "_startup_path", lambda: startup)
    monkeypatch.setattr(lifecycle, "_save_companion_flags", Mock(return_value=""))
    monkeypatch.setattr(lifecycle, "_installation_diagnostic", lambda: {
        "installed": True,
        "_executable": executable,
    })

    lifecycle._set_autostart(True)

    assert startup.read_bytes().startswith((b"\xff\xfe", b"\xfe\xff"))
    assert str(executable) in startup.read_text(encoding="utf-16")
    assert lifecycle._autostart_diagnostic({
        "installed": True,
        "_executable": executable,
    })["enabled"] is True


def test_installed_distribution_manifest_is_discovered_outside_sys_prefix(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    relative = Path("share/iris-os/companion/release-manifest.json")
    installed = tmp_path / relative
    installed.parent.mkdir(parents=True)
    installed.write_text("{}", encoding="utf-8")
    distribution = SimpleNamespace(
        files=[relative],
        locate_file=lambda entry: tmp_path / entry,
    )
    monkeypatch.setattr(lifecycle.importlib.metadata, "distribution", lambda name: distribution)

    assert installed in lifecycle._manifest_candidates()


# ── A08.7: the packaged runtime only trusts a package inside its installation ─

def _packaged(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    from core.runtime_descriptor import DESCRIPTOR_ENV
    from tests.runtime_descriptor_fixtures import write_runtime_fixture

    descriptor, _spec = write_runtime_fixture(tmp_path / "Iris instalada ñ")
    monkeypatch.setenv(DESCRIPTOR_ENV, str(descriptor))
    return descriptor.parent


def _external_package(tmp_path: Path, folder: str) -> tuple[Path, Path]:
    installer = _write_fake_pe(tmp_path / folder / "IRIS Desktop Companion_0.1.0_x64-setup.exe",
                               payload=b"outside the installation")
    manifest = tmp_path / folder / "release-manifest.json"
    manifest.write_text(json.dumps({
        "schema": 1, "product": "IRIS Desktop Companion", "version": "0.1.0", "platform": "windows",
        "arch": "x64", "filename": installer.name, "sha256": hashlib.sha256(installer.read_bytes()).hexdigest(),
        "signed": False, "webview2_mode": "downloadBootstrapper",
    }), encoding="utf-8")
    return manifest, installer


def test_packaged_ignores_companion_overrides_from_the_environment(monkeypatch, tmp_path):
    _packaged(monkeypatch, tmp_path)
    manifest, installer = _external_package(tmp_path, "descargas")
    monkeypatch.setenv("IRIS_COMPANION_MANIFEST", str(manifest))
    monkeypatch.setenv("IRIS_COMPANION_INSTALLER", str(installer))
    monkeypatch.setattr(lifecycle, "_RELEASE_MANIFEST", manifest)  # a checkout-style path, also outside
    assert manifest not in lifecycle._manifest_candidates()
    diagnostic = lifecycle._package_diagnostic()
    assert diagnostic["available"] is False and diagnostic["trusted"] is False


def test_packaged_uses_a_companion_package_inside_its_installation(monkeypatch, tmp_path):
    base = _packaged(monkeypatch, tmp_path)
    manifest, installer = _external_package(base, "companion")
    diagnostic = lifecycle._package_diagnostic()
    assert diagnostic["available"] and diagnostic["trusted"], diagnostic
    assert diagnostic["_path"] == installer


def test_development_keeps_its_companion_overrides(monkeypatch, tmp_path):
    from core.runtime_descriptor import DESCRIPTOR_ENV

    monkeypatch.delenv(DESCRIPTOR_ENV, raising=False)
    manifest, installer = _external_package(tmp_path, "build")
    monkeypatch.setenv("IRIS_COMPANION_MANIFEST", str(manifest))
    monkeypatch.setenv("IRIS_COMPANION_INSTALLER", str(installer))
    assert lifecycle._manifest_candidates()[0] == manifest
    assert lifecycle._package_diagnostic()["_path"] == installer
