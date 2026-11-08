"""A08.1: packaged runtime operations fail closed without external effects."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import httpx
import psutil
import pytest

from core import hermes_service_registry as registry
from core import hermes_updates as updates
from core import runtime_descriptor as policy
from core import websocket_server as ws
from core.hermes_runtime import hermes_command_prefix
from wizard import app as wizard
from wizard import autostart, health_check, hermes_config, provider_sync


@pytest.fixture
def packaged_runtime(tmp_path, monkeypatch):
    installation = tmp_path / "installation"
    paths = {
        "iris/core/desktop.py": "# bundled iris anchor\n",
        "iris/python/python.exe": "bundled iris python",
        "hermes/hermes_cli/__init__.py": "# bundled hermes anchor\n",
        "hermes/python/python.exe": "bundled hermes python",
        "hermes/cli.py": "# bundled cli\n",
        # A08.4 makes the private shared audio decoder part of the runtime.
        # These ordinary files are never executed by the policy fixtures.
        "tools/audio/bin/ffmpeg.exe": "fixture decoder",
        "tools/audio/bin/ffprobe.exe": "fixture probe",
        "tools/audio/bin/avcodec.dll": "fixture shared decoder",
        "tools/shell/usr/bin/bash.exe": "fixture shell",
        "tools/shell/usr/bin/sh.exe": "fixture shell",
        "tools/shell/usr/bin/msys-2.0.dll": "fixture POSIX runtime",
        "tools/shell/etc/fstab": "fixture mounts",
    }
    for relative, content in paths.items():
        target = installation / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    (installation / "iris/face").mkdir()
    metadata = installation / "hermes/pin.json"
    metadata.write_text(json.dumps({"schema_version": 1, "version": "0.21.4", "revision": "b" * 40}), encoding="utf-8")
    spec = {
        "schema_version": 2, "mode": "packaged",
        "iris": {"root": "iris", "python": "iris/python/python.exe", "assets": "iris/face", "revision": "a" * 40},
        "hermes": {"root": "hermes", "python": "hermes/python/python.exe", "cli": "hermes/cli.py",
                   "version": "0.21.4", "revision": "b" * 40, "metadata": "hermes/pin.json"},
    }
    descriptor = installation / "runtime.json"
    descriptor.write_text(json.dumps(spec), encoding="utf-8")
    iris_home, hermes_home = tmp_path / "iris-profile", tmp_path / "hermes-profile"
    iris_home.mkdir()
    hermes_home.mkdir()
    monkeypatch.setenv(policy.DESCRIPTOR_ENV, str(descriptor))
    monkeypatch.setenv("IRIS_HOME", str(iris_home))
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    return SimpleNamespace(context=policy.get_runtime(), descriptor=descriptor, spec=spec,
                           iris_home=iris_home, hermes_home=hermes_home)


@pytest.fixture
def owned_gateway(packaged_runtime, monkeypatch):
    runtime = packaged_runtime.context
    process = Mock()
    process.create_time.return_value = 1_700_000_000.125
    process.exe.return_value = str(runtime.hermes.python)
    process.cmdline.return_value = hermes_command_prefix() + ["gateway", "run"]
    process.cwd.return_value = str(runtime.hermes.root)
    process.environ.return_value = {"HERMES_HOME": str(packaged_runtime.hermes_home),
                                   policy.DESCRIPTOR_ENV: str(packaged_runtime.descriptor)}
    process.net_connections.return_value = [SimpleNamespace(
        status=psutil.CONN_LISTEN, laddr=SimpleNamespace(ip="127.0.0.1", port=8642))]
    monkeypatch.setattr(psutil, "Process", Mock(return_value=process))
    registry.record_spawned_gateway(packaged_runtime.hermes_home, 24680)
    return process


def no_effect_mock():
    return Mock(side_effect=AssertionError("External operation must not run"))


@pytest.mark.parametrize("name", ["external_hermes_install", "external_hermes_update", "source_update", "external_autostart"])
def test_packaged_capabilities_are_disabled(packaged_runtime, name):
    assert policy.runtime_capability_allowed(name) is False
    with pytest.raises(policy.RuntimePolicyError):
        policy.require_runtime_capability(name)
    assert policy.runtime_capability_allowed("profile_configuration") is True
    # A08.5: extras install into isolated profile environments, never the runtime.
    assert policy.runtime_capability_allowed("profile_extras") is True
    with pytest.raises(policy.RuntimeDescriptorError):
        policy.runtime_capability_allowed("base_package_install")


def test_packaged_runtime_updates_only_through_the_joint_channel(packaged_runtime):
    # A08.8: the bundled channel replaces Git and ``hermes update``.
    assert policy.runtime_capability_allowed("bundled_update") is True
    policy.require_runtime_capability("bundled_update")
    assert policy.runtime_public_info()["capabilities"]["bundled_update"] is True


def test_unknown_operation_is_not_a_permitted_fallback(packaged_runtime):
    with pytest.raises(policy.RuntimeDescriptorError):
        policy.runtime_capability_allowed("external_hermes_updat")


def test_direct_update_helpers_block_before_any_git_cli_or_receipt(packaged_runtime, monkeypatch):
    effect = no_effect_mock()
    monkeypatch.setattr(updates, "run_hermes_cli", effect)
    monkeypatch.setattr(updates, "_receipt_state", effect)
    monkeypatch.setattr(updates.subprocess, "run", effect)
    for call in (
        updates.apply_hermes_update,
        lambda: updates._git_output(packaged_runtime.context.hermes.root, "fetch"),
        lambda: ws._git_update_snapshot(packaged_runtime.context.iris_root, "IRIS OS"),
        lambda: ws._apply_git_update(packaged_runtime.context.iris_root, "IRIS OS"),
    ):
        with pytest.raises(policy.RuntimePolicyError):
            call()
    effect.assert_not_called()


@pytest.mark.asyncio
async def test_direct_installer_migration_and_autostart_block_before_effects(packaged_runtime, monkeypatch):
    effect = no_effect_mock()
    monkeypatch.setattr(wizard.httpx, "AsyncClient", effect)
    monkeypatch.setattr(hermes_config, "shutil", SimpleNamespace(which=effect))
    monkeypatch.setattr(autostart, "_os", effect)
    for operation in (wizard._download_official_installer, autostart.enable_autostart,
                      autostart.disable_autostart, autostart._disable_macos,
                      lambda: autostart._macos_launchctl("load", packaged_runtime.iris_home / "entry.plist")):
        with pytest.raises(policy.RuntimePolicyError):
            await operation()
    with pytest.raises(policy.RuntimePolicyError):
        hermes_config.run_claw_migrate()
    for operation in (autostart._disable_windows, autostart._disable_linux):
        with pytest.raises(policy.RuntimePolicyError):
            operation()
    effect.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("path,payload", [
    ("/api/install-hermes-stream", {}),
    # Optional extras are allowed since A08.5: they write only isolated profile
    # environments (tests/test_extras.py proves the installation is untouched).
    ("/api/autostart", {"action": "enable"}),
    ("/api/autostart", {"action": "disable"}),
])
async def test_packaged_wizard_rejects_mutations_even_when_called_directly(packaged_runtime, monkeypatch, path, payload):
    effect = no_effect_mock()
    monkeypatch.setattr(wizard, "_enforce_local_install_request", effect)
    monkeypatch.setattr(wizard.asyncio, "create_subprocess_exec", effect)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=wizard.app), base_url="http://localhost") as client:
        response = await client.post(path, json=payload, headers={"Origin": "http://localhost"})
    assert response.status_code == 403, response.text
    assert response.json()["code"] == "runtime_operation_unavailable"
    effect.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("component", ["iris-os", "hermes-agent"])
async def test_packaged_core_update_admission_never_uses_git_or_hermes_update(packaged_runtime, monkeypatch, component):
    from core import iris_updates

    effect = no_effect_mock()
    monkeypatch.setattr(ws, "_system_update_snapshot", effect)
    monkeypatch.setattr(ws, "_apply_git_update", effect)
    monkeypatch.setattr(updates, "apply_hermes_update", effect)
    monkeypatch.setattr(updates, "run_hermes_cli", effect)
    monkeypatch.setattr(ws, "_system_update_task", None)
    monkeypatch.setattr(ws, "_system_update_job", {"active": False, "phase": "idle"})
    monkeypatch.setattr(iris_updates, "JOB", {"active": False, "phase": "idle"})
    joint = Mock()
    monkeypatch.setattr(iris_updates, "run_update_job", joint)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=ws.app), base_url="http://localhost") as client:
        response = await client.post("/api/system-updates/apply", json={"components": [component]}, headers={"Origin": "http://localhost"})
        await ws._packaged_update_task
    assert response.status_code == 202, response.text
    assert response.json()["operation"] == "upgrade"
    joint.assert_called_once_with()
    assert ws._system_update_task is None
    assert ws._system_update_job == {"active": False, "phase": "idle"}
    effect.assert_not_called()


@pytest.mark.asyncio
async def test_packaged_update_checks_ignore_old_development_cache(packaged_runtime, monkeypatch):
    effect = no_effect_mock()
    monkeypatch.setattr(updates, "run_hermes_cli", effect)
    monkeypatch.setattr(ws, "_system_update_snapshot", effect)
    monkeypatch.setattr(ws, "_api_cache", {"system:updates": (time.monotonic(), {"can_update": True})})
    monkeypatch.setattr(ws, "_system_update_task", SimpleNamespace(done=lambda: False))
    monkeypatch.setattr(ws, "_system_update_job", {"active": True, "phase": "updating", "snapshot": {"can_update": True}})
    from core import iris_updates

    monkeypatch.setattr(iris_updates, "JOB", {"active": False, "phase": "idle"})
    checked = json.loads((await ws.system_updates_check(force=True)).body)
    status = json.loads((await ws.system_updates_status()).body)
    native = updates.hermes_update_snapshot()
    # A copy without the versioned installer layout is never offered updates.
    assert checked["summary"] == "unavailable" and native["status"] == "managed"
    assert checked["can_update"] is False and native["can_update"] is False
    assert [item["id"] for item in checked["items"]] == ["iris-os"]
    assert status["active"] is False and status["phase"] == "idle"
    assert checked["runtime"]["mode"] == "packaged"
    effect.assert_not_called()


@pytest.mark.asyncio
async def test_worker_rechecks_policy_before_a_deferred_source_update(packaged_runtime, monkeypatch):
    effect = no_effect_mock()
    monkeypatch.setattr(ws, "_system_update_snapshot", effect)
    monkeypatch.setattr(ws, "_system_update_job", {"active": True})
    await ws._perform_system_update({"iris-os"})
    assert ws._system_update_job["phase"] == "failed"
    assert ws._system_update_job["ok"] is False
    effect.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["hermes"])
async def test_install_stream_rechecks_packaged_policy_before_it_executes(packaged_runtime, monkeypatch, kind):
    # Extras are allowed in packaged since A08.5; tests/test_extras.py proves they
    # never write the runtime and never launch pip.
    monkeypatch.delenv(policy.DESCRIPTOR_ENV)
    monkeypatch.setattr(wizard, "_enforce_local_install_request", lambda *a: None)
    effect = no_effect_mock()
    monkeypatch.setattr(wizard, "_download_official_installer", effect)
    monkeypatch.setattr(wizard.asyncio, "create_subprocess_exec", effect)
    response = await wizard.install_hermes_stream(None)
    monkeypatch.setenv(policy.DESCRIPTOR_ENV, str(packaged_runtime.descriptor))
    with pytest.raises(policy.RuntimePolicyError):
        await anext(response.body_iterator)
    effect.assert_not_called()


@pytest.mark.asyncio
async def test_public_runtime_contract_and_core_identity_have_no_paths(packaged_runtime):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=wizard.app), base_url="http://localhost") as client:
        response = await client.get("/api/runtime-info")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    public = response.json()
    identity = json.loads((await ws.get_status()).body)["runtime"]
    assert public["hermes_version"] == "0.21.4"
    assert identity == policy.runtime_service_identity(packaged_runtime.iris_home)
    for data in (public, identity):
        assert str(packaged_runtime.context.base) not in json.dumps(data)
        assert str(packaged_runtime.iris_home) not in json.dumps(data)


@pytest.mark.asyncio
@pytest.mark.parametrize("application,path", [(wizard.app, "/api/install-hermes-stream"), (ws.app, "/api/system-updates/apply")])
async def test_unknown_http_method_does_not_trigger_any_mutation(packaged_runtime, monkeypatch, application, path):
    effect = no_effect_mock()
    monkeypatch.setattr(wizard, "_download_official_installer", effect)
    monkeypatch.setattr(ws, "_perform_system_update", effect)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://localhost") as client:
        response = await client.request("BREW", path, headers={"Origin": "http://localhost"})
    assert response.status_code == 405
    effect.assert_not_called()


@pytest.mark.asyncio
async def test_invalid_descriptor_precedes_cached_catalog_or_external_fallback(packaged_runtime, monkeypatch):
    packaged_runtime.descriptor.write_text('{"schema_version": 99}', encoding="utf-8")
    effect = no_effect_mock()
    monkeypatch.setattr(wizard, "_hermes_installation_state_legacy", effect)
    monkeypatch.setattr(wizard, "_hermes_install_readiness_legacy", effect)
    monkeypatch.setattr(provider_sync, "_provider_meta_cache", (time.monotonic(), {"cached": {}}))
    monkeypatch.setattr(provider_sync, "_cache", {("__snapshot__", "__snapshot__"): (time.monotonic(), {"cached": []})})
    for call in (wizard._hermes_installation_state, wizard._hermes_install_readiness,
                 provider_sync._resolve_hermes_python, provider_sync.local_hermes_provider_metadata,
                 hermes_config.get_hermes_home, updates.hermes_update_snapshot):
        with pytest.raises(policy.RuntimeDescriptorError):
            call()
    with pytest.raises(policy.RuntimeDescriptorError):
        await provider_sync.hermes_catalog()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=wizard.app), base_url="http://localhost") as client:
        response = await client.get("/api/runtime-info")
    assert response.status_code == 503 and response.json()["code"] == "runtime_descriptor_invalid"
    assert str(packaged_runtime.descriptor) not in response.text
    effect.assert_not_called()


def test_packaged_provider_probe_uses_declared_python_and_isolated_cwd(packaged_runtime, monkeypatch):
    effect = no_effect_mock()
    monkeypatch.setattr("core.hermes_runtime.shutil.which", effect)
    decoy = packaged_runtime.context.hermes.cli.parent / "python.exe"
    decoy.write_text("PATH/CLI neighbor decoy", encoding="utf-8")
    assert provider_sync._resolve_hermes_python() == packaged_runtime.context.hermes.python
    command = provider_sync._hermes_python_probe_command(decoy, "print('test')")
    assert command == hermes_command_prefix()[:-1] + ["-c", "print('test')"]
    assert provider_sync._hermes_probe_kwargs(1)["cwd"] == str(packaged_runtime.context.hermes.root)
    effect.assert_not_called()


def test_packaged_runtime_health_probe_uses_isolated_prefix(packaged_runtime, monkeypatch):
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout="IRIS_HERMES_RUNTIME_OK", stderr=""))
    monkeypatch.setattr(wizard.subprocess, "run", run)
    assert wizard._hermes_runtime_health() == (True, "")
    command = run.call_args.args[0]
    assert command[:-2] == hermes_command_prefix()[:-1]
    assert command[-2] == "-c"
    assert run.call_args.kwargs["cwd"] == str(packaged_runtime.context.hermes.root)
    assert run.call_args.kwargs["env"][policy.DESCRIPTOR_ENV] == str(packaged_runtime.descriptor)


def test_receipt_accepts_only_owned_runtime_profile_and_listener(packaged_runtime, owned_gateway):
    assert registry.gateway_registry_state(packaged_runtime.hermes_home, "http://127.0.0.1:8642")["verified"] is True
    payload = json.loads((packaged_runtime.hermes_home / registry.RECEIPT_NAME).read_text("utf-8"))
    assert payload["pid"] == 24680
    assert str(packaged_runtime.context.base) not in json.dumps(payload)
    assert registry.gateway_registry_state(packaged_runtime.hermes_home, "https://example.invalid")["verified"] is False
    assert registry.gateway_registry_state(packaged_runtime.hermes_home, "http://127.0.0.1:8643")["verified"] is False


@pytest.mark.parametrize("field,value", [("create_time", 1_700_000_002), ("exe", "outside.exe"), ("cwd", "outside"), ("cmdline", ["hermes", "gateway", "run"]), ("net_connections", [])])
def test_gateway_process_mismatch_never_allows_stop(packaged_runtime, owned_gateway, field, value):
    if field in {"exe", "cwd"}:
        target = packaged_runtime.context.base.parent / value
        if field == "cwd":
            target.mkdir()
        else:
            target.write_text("external executable", encoding="utf-8")
        value = str(target)
    getattr(owned_gateway, field).return_value = value
    assert registry.gateway_registry_state(packaged_runtime.hermes_home, "http://127.0.0.1:8642")["verified"] is False
    if field == "create_time":
        # The recorded PID now belongs to a younger process: our gateway is
        # gone. The receipt is stale (it must not block a new start, qa9), and
        # the process holding that PID is still never touched.
        assert registry.gateway_registry_state(packaged_runtime.hermes_home)["state"] == "stale"
        assert registry.stop_owned_gateway(packaged_runtime.hermes_home)["blocked"] is False
        owned_gateway.terminate.assert_not_called()
        owned_gateway.kill.assert_not_called()
        return
    # Listener is necessary for reuse; a stopped listener alone does not revoke
    # ownership of the known foreground process, so only identity mismatches
    # must block stopping it.
    if field != "net_connections":
        assert registry.stop_owned_gateway(packaged_runtime.hermes_home)["blocked"] is True
        owned_gateway.terminate.assert_not_called()
        owned_gateway.kill.assert_not_called()


@pytest.mark.parametrize("field", ["runtime_id", "profile_id", "hermes_revision", "iris_revision"])
def test_wrong_receipt_identity_never_terminates_process(packaged_runtime, owned_gateway, field):
    path = packaged_runtime.hermes_home / registry.RECEIPT_NAME
    payload = json.loads(path.read_text("utf-8"))
    payload["runtime"][field] = "foreign"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert registry.stop_owned_gateway(packaged_runtime.hermes_home)["blocked"] is True
    owned_gateway.terminate.assert_not_called()


def test_uninspectable_gateway_fails_closed(packaged_runtime, owned_gateway, monkeypatch):
    monkeypatch.setattr(psutil, "Process", Mock(side_effect=psutil.AccessDenied(24680)))
    assert registry.stop_owned_gateway(packaged_runtime.hermes_home)["blocked"] is True
    owned_gateway.terminate.assert_not_called()


def test_missing_process_inspector_never_falls_back_to_pid_file(packaged_runtime, owned_gateway, monkeypatch):
    monkeypatch.setitem(sys.modules, "psutil", None)
    assert registry.stop_owned_gateway(packaged_runtime.hermes_home)["blocked"] is True
    owned_gateway.terminate.assert_not_called()


@pytest.mark.parametrize("key", ["HERMES_HOME", policy.DESCRIPTOR_ENV])
def test_same_runtime_process_from_different_profile_or_descriptor_is_rejected(packaged_runtime, owned_gateway, key):
    contaminated = dict(owned_gateway.environ.return_value)
    contaminated[key] = str(packaged_runtime.context.base.parent)
    owned_gateway.environ.return_value = contaminated
    assert registry.stop_owned_gateway(packaged_runtime.hermes_home)["blocked"] is True
    owned_gateway.terminate.assert_not_called()


@pytest.mark.parametrize("change", [{"schema_version": True}, {"method": "unknown"}, {"pid": True}, {"created_at": float("nan")}])
def test_receipt_schema_unknown_fields_and_invalid_values_fail_closed(packaged_runtime, owned_gateway, change):
    path = packaged_runtime.hermes_home / registry.RECEIPT_NAME
    payload = json.loads(path.read_text("utf-8"))
    payload.update(change)
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert registry.stop_owned_gateway(packaged_runtime.hermes_home)["blocked"] is True
    owned_gateway.terminate.assert_not_called()


def test_oversized_receipt_is_rejected_before_read_or_process_inspection(packaged_runtime, owned_gateway, monkeypatch):
    path = packaged_runtime.hermes_home / registry.RECEIPT_NAME
    path.write_bytes(b"x" * (registry.MAX_RECEIPT_BYTES + 1))
    process_lookup = no_effect_mock()
    monkeypatch.setattr(psutil, "Process", process_lookup)
    assert registry.stop_owned_gateway(packaged_runtime.hermes_home)["blocked"] is True
    process_lookup.assert_not_called()
    owned_gateway.terminate.assert_not_called()


def test_opened_receipt_must_match_the_current_file_identity(packaged_runtime, owned_gateway, monkeypatch):
    actual_fstat = os.fstat

    def replaced_descriptor(file_descriptor):
        observed = actual_fstat(file_descriptor)
        return SimpleNamespace(st_mode=observed.st_mode, st_nlink=observed.st_nlink,
                               st_size=observed.st_size, st_dev=observed.st_dev,
                               st_ino=observed.st_ino + 1)

    monkeypatch.setattr(os, "fstat", replaced_descriptor)
    assert registry.stop_owned_gateway(packaged_runtime.hermes_home)["blocked"] is True
    owned_gateway.terminate.assert_not_called()


def test_receipt_duplicate_keys_are_rejected(packaged_runtime, owned_gateway):
    path = packaged_runtime.hermes_home / registry.RECEIPT_NAME
    payload = path.read_text("utf-8")
    path.write_text(payload[:-1] + ', "pid": 24680}', encoding="utf-8")
    assert registry.stop_owned_gateway(packaged_runtime.hermes_home)["blocked"] is True
    owned_gateway.terminate.assert_not_called()


def test_legacy_pid_file_alone_is_not_authority_to_stop(packaged_runtime, owned_gateway):
    (packaged_runtime.hermes_home / registry.RECEIPT_NAME).unlink()
    path = packaged_runtime.hermes_home / "gateway.pid"
    path.write_text(json.dumps({"pid": 24680}), encoding="utf-8")
    assert registry.stop_owned_gateway(packaged_runtime.hermes_home)["stopped"] is False
    owned_gateway.terminate.assert_not_called()
    assert path.exists()


@pytest.mark.parametrize("link_kind", ["symlink", "hardlink"])
def test_receipt_alias_is_rejected_without_terminating_foreign_process(packaged_runtime, owned_gateway, link_kind, monkeypatch):
    path = packaged_runtime.hermes_home / registry.RECEIPT_NAME
    foreign = packaged_runtime.context.base.parent / "foreign-receipt.json"
    foreign.write_bytes(path.read_bytes())
    path.unlink()
    try:
        if link_kind == "symlink":
            path.symlink_to(foreign)
        else:
            os.link(foreign, path)
    except OSError:
        if link_kind != "symlink":
            raise
        # Windows often denies symlink creation without developer mode. Still
        # exercise the alias rejection branch with simulated filesystem metadata.
        path.write_bytes(foreign.read_bytes())
        actual_is_symlink = Path.is_symlink
        monkeypatch.setattr(Path, "is_symlink", lambda candidate: candidate == path or actual_is_symlink(candidate))
    before = foreign.read_bytes()
    assert registry.stop_owned_gateway(packaged_runtime.hermes_home)["blocked"] is True
    assert foreign.read_bytes() == before
    owned_gateway.terminate.assert_not_called()


def test_stop_only_removes_pid_file_that_matches_own_process(packaged_runtime, owned_gateway):
    gateway_pid = packaged_runtime.hermes_home / "gateway.pid"
    gateway_lock = packaged_runtime.hermes_home / "gateway.lock"
    gateway_pid.write_text(json.dumps({"pid": 99999}), encoding="utf-8")
    gateway_lock.write_text("external lock", encoding="utf-8")
    assert registry.stop_owned_gateway(packaged_runtime.hermes_home)["stopped"] is True
    owned_gateway.terminate.assert_called_once()
    owned_gateway.wait.assert_called_once_with(timeout=4)
    owned_gateway.kill.assert_not_called()
    assert not (packaged_runtime.hermes_home / registry.RECEIPT_NAME).exists()
    assert gateway_pid.exists() and gateway_lock.read_text("utf-8") == "external lock"


def test_pid_reuse_during_shutdown_never_escalates_to_kill(packaged_runtime, owned_gateway, monkeypatch):
    owned_gateway.wait.side_effect = psutil.TimeoutExpired(4, pid=24680)
    owned_gateway.create_time.side_effect = [1_700_000_000.125, 1_700_000_000.125, 1_700_000_001.125]
    result = registry.stop_owned_gateway(packaged_runtime.hermes_home)
    assert result["blocked"] is True
    owned_gateway.terminate.assert_called_once()
    owned_gateway.kill.assert_not_called()
    assert (packaged_runtime.hermes_home / registry.RECEIPT_NAME).exists()


def test_pid_reuse_between_initial_probe_and_retained_process_never_terminates(packaged_runtime, owned_gateway, monkeypatch):
    replacement = Mock()
    replacement.create_time.return_value = 1_700_000_099.125
    replacement.exe.return_value = owned_gateway.exe.return_value
    replacement.cmdline.return_value = owned_gateway.cmdline.return_value
    replacement.cwd.return_value = owned_gateway.cwd.return_value
    replacement.environ.return_value = owned_gateway.environ.return_value
    monkeypatch.setattr(psutil, "Process", Mock(side_effect=[owned_gateway, replacement]))
    assert registry.stop_owned_gateway(packaged_runtime.hermes_home)["blocked"] is True
    owned_gateway.terminate.assert_not_called()
    replacement.terminate.assert_not_called()
    replacement.kill.assert_not_called()


@pytest.mark.asyncio
async def test_unowned_healthy_gateway_is_never_probed_or_reused(packaged_runtime, monkeypatch):
    effect = no_effect_mock()
    monkeypatch.setattr(health_check.httpx, "AsyncClient", effect)
    assert (await health_check.check_gateway_health("http://127.0.0.1:8642"))["ok"] is False
    monkeypatch.setattr(wizard.IRISConfig, "load", lambda: SimpleNamespace(hermes_url="http://127.0.0.1:8642"))
    monkeypatch.setattr(hermes_config, "read_hermes_dotenv", lambda: {})
    monkeypatch.setattr(health_check, "check_gateway_port", AsyncMock(return_value={"ok": True}))
    monkeypatch.setattr(wizard.subprocess, "Popen", effect)
    result = await wizard._start_gateway_impl()
    assert result.status_code == 409
    assert json.loads(result.body)["code"] == "gateway_port_conflict"
    effect.assert_not_called()


@pytest.mark.asyncio
async def test_existing_unverified_gateway_receipt_blocks_spawn_even_without_listener(packaged_runtime, owned_gateway, monkeypatch):
    owned_gateway.cwd.return_value = str(packaged_runtime.context.base.parent)  # live, not ours
    monkeypatch.setattr(wizard.IRISConfig, "load", lambda: SimpleNamespace(hermes_url="http://127.0.0.1:8642"))
    monkeypatch.setattr(hermes_config, "read_hermes_dotenv", lambda: {})
    monkeypatch.setattr(health_check, "check_gateway_port", AsyncMock(return_value={"ok": False}))
    effect = no_effect_mock()
    monkeypatch.setattr(wizard.subprocess, "Popen", effect)
    result = await wizard._start_gateway_impl()
    assert result.status_code == 409
    assert json.loads(result.body)["code"] == "gateway_runtime_conflict"
    effect.assert_not_called()


@pytest.mark.asyncio
async def test_owned_gateway_without_api_listener_remains_pending_without_second_spawn(packaged_runtime, owned_gateway, monkeypatch):
    monkeypatch.setattr(wizard.IRISConfig, "load", lambda: SimpleNamespace(hermes_url="http://127.0.0.1:8642"))
    monkeypatch.setattr(hermes_config, "read_hermes_dotenv", lambda: {})
    monkeypatch.setattr(health_check, "check_gateway_port", AsyncMock(return_value={"ok": False}))
    effect = no_effect_mock()
    monkeypatch.setattr(wizard.subprocess, "Popen", effect)
    result = await wizard._start_gateway_impl()
    assert result.status_code == 202
    assert json.loads(result.body)["status"] == "pending"
    assert json.loads(result.body)["code"] == "gateway_runtime_pending"
    effect.assert_not_called()


@pytest.mark.parametrize("process_state", ["gone", "pid_reused"])
def test_receipt_of_another_version_whose_gateway_ended_is_stale(packaged_runtime, owned_gateway, monkeypatch, process_state):
    # qa9: 0.9.2's gateway ended (closed by hand) and its receipt stayed. The
    # new version saw an identity that was not its own and refused to start
    # Hermes ("Gateway sin identidad acreditada"), so the update rolled back.
    path = packaged_runtime.hermes_home / registry.RECEIPT_NAME
    payload = json.loads(path.read_text("utf-8"))
    payload["runtime"]["runtime_id"] = "f" * 64  # written by another version
    path.write_text(json.dumps(payload), encoding="utf-8")
    if process_state == "gone":
        monkeypatch.setattr(psutil, "Process", Mock(side_effect=psutil.NoSuchProcess(24680)))
    else:
        owned_gateway.create_time.return_value += 1
    assert registry.gateway_registry_state(packaged_runtime.hermes_home)["state"] == "stale"
    assert registry.stop_owned_gateway(packaged_runtime.hermes_home)["blocked"] is False
    owned_gateway.terminate.assert_not_called()
    owned_gateway.kill.assert_not_called()


def test_live_gateway_of_another_version_still_blocks(packaged_runtime, owned_gateway):
    # The protection stays: a recorded gateway that still runs but is not this
    # version's is neither reused, stopped nor replaced.
    path = packaged_runtime.hermes_home / registry.RECEIPT_NAME
    payload = json.loads(path.read_text("utf-8"))
    payload["runtime"]["runtime_id"] = "f" * 64
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert registry.gateway_registry_state(packaged_runtime.hermes_home)["state"] == "unverified"
    assert registry.stop_owned_gateway(packaged_runtime.hermes_home)["blocked"] is True
    owned_gateway.terminate.assert_not_called()


def test_receipt_for_own_absent_process_does_not_block_later_start(packaged_runtime, owned_gateway, monkeypatch):
    monkeypatch.setattr(psutil, "Process", Mock(side_effect=psutil.NoSuchProcess(24680)))
    assert registry.gateway_registry_state(packaged_runtime.hermes_home)["state"] == "stale"
    assert registry.stop_owned_gateway(packaged_runtime.hermes_home)["blocked"] is False
    owned_gateway.terminate.assert_not_called()
    assert (packaged_runtime.hermes_home / registry.RECEIPT_NAME).exists()


@pytest.mark.asyncio
async def test_restart_with_unverified_gateway_does_not_stop_or_start(packaged_runtime, owned_gateway, monkeypatch):
    owned_gateway.cwd.return_value = str(packaged_runtime.context.base.parent)  # live, not ours
    start = AsyncMock(side_effect=AssertionError("Must not spawn after a refused stop"))
    monkeypatch.setattr(wizard, "start_gateway", start)
    result = await wizard.restart_gateway()
    assert result.status_code == 409
    owned_gateway.terminate.assert_not_called()
    start.assert_not_called()


@pytest.mark.asyncio
async def test_core_stop_exposes_conflict_without_killing_unverified_gateway(packaged_runtime, owned_gateway):
    owned_gateway.cwd.return_value = str(packaged_runtime.context.base.parent)  # live, not ours
    result = await ws.stop_gateway_proxy()
    assert result.status_code == 409
    assert json.loads(result.body)["code"] == "gateway_runtime_conflict"
    owned_gateway.terminate.assert_not_called()


@pytest.mark.asyncio
async def test_verified_gateway_health_still_requires_native_protocol(packaged_runtime, owned_gateway, monkeypatch):
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"platform": "other-server", "status": "ok"})))
    monkeypatch.setattr(health_check.httpx, "AsyncClient", lambda **_: client)
    assert (await health_check.check_gateway_health("http://127.0.0.1:8642"))["ok"] is False


@pytest.mark.asyncio
async def test_packaged_gateway_status_rejects_remote_without_http(packaged_runtime, monkeypatch):
    effect = no_effect_mock()
    monkeypatch.setattr(ws.IRISConfig, "load", lambda: SimpleNamespace(hermes_url="https://example.invalid"))
    monkeypatch.setattr(ws, "_hermes_api_request", effect)
    monkeypatch.setattr(health_check.httpx, "AsyncClient", effect)
    result = json.loads((await ws.gateway_status()).body)
    assert result["healthy"] is False and result["managed"] is True
    effect.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("record_failure", [None, OSError("receipt write failed"), policy.RuntimeDescriptorError("invalid descriptor"), asyncio.CancelledError()],
                         ids=["success", "receipt-write-error", "descriptor-error", "cancelled"])
async def test_packaged_gateway_spawn_uses_only_declared_runtime(packaged_runtime, owned_gateway, monkeypatch, record_failure):
    (packaged_runtime.hermes_home / registry.RECEIPT_NAME).unlink()
    cfg = SimpleNamespace(hermes_url="http://127.0.0.1:8642", llm_provider="ollama-local",
                          llm_model="test-model", llm_api_key="", llm_use_oauth=False)
    monkeypatch.setattr(wizard.IRISConfig, "load", lambda: cfg)
    monkeypatch.setattr(health_check, "check_gateway_port", AsyncMock(return_value={"ok": False}))
    monkeypatch.setattr(health_check, "check_gateway_health", AsyncMock(return_value={"ok": True}))
    monkeypatch.setattr(hermes_config, "read_hermes_dotenv", lambda: {"LLM_PROVIDER": "ollama-local", "LLM_MODEL": "test-model"})
    monkeypatch.setattr(hermes_config, "has_hermes_auth_profile", lambda *a, **kw: False)
    monkeypatch.setattr(hermes_config, "read_provider_base_url", lambda _: "")
    monkeypatch.setattr(hermes_config, "_read_env_file", lambda: {})
    monkeypatch.setattr(wizard, "_external_provider_preflight", AsyncMock(return_value=None))
    monkeypatch.setattr(wizard, "_local_provider_preflight", AsyncMock(return_value=None))
    sync = AsyncMock()
    monkeypatch.setattr(wizard, "sync_hermes_config_from_iris", sync)
    effect = no_effect_mock()
    monkeypatch.setattr(wizard, "resolve_cmd", effect)
    monkeypatch.setenv("HERMES_REPO", "external-runtime-decoy")
    monkeypatch.setenv("PYTHONPATH", "external-python-decoy")
    process = Mock(pid=24680, poll=lambda: None)
    spawn = Mock(return_value=process)
    monkeypatch.setattr(wizard.subprocess, "Popen", spawn)
    monkeypatch.setattr("socket.create_connection", MagicMock())
    if record_failure is not None:
        monkeypatch.setattr(registry, "record_spawned_gateway", Mock(side_effect=record_failure))
    if isinstance(record_failure, (policy.RuntimeDescriptorError, asyncio.CancelledError)):
        with pytest.raises(type(record_failure)):
            await wizard._start_gateway_impl()
        response = None
    else:
        response = await wizard._start_gateway_impl()
    if record_failure is not None:
        if response is not None:
            assert response.status_code == 503
            assert json.loads(response.body)["code"] == "gateway_runtime_unverified"
        process.terminate.assert_called_once()
        process.wait.assert_called_once_with(timeout=4)
        process.kill.assert_not_called()
        owned_gateway.terminate.assert_not_called()
        owned_gateway.kill.assert_not_called()
        return
    payload = json.loads(response.body)
    assert response.status_code == 200 and payload["status"] == "success", payload
    assert spawn.call_args.args[0] == hermes_command_prefix() + ["gateway", "run"]
    assert spawn.call_args.kwargs["cwd"] == str(packaged_runtime.context.hermes.root)
    env = spawn.call_args.kwargs["env"]
    assert env[policy.DESCRIPTOR_ENV] == str(packaged_runtime.descriptor)
    assert env["HERMES_HOME"] == str(packaged_runtime.hermes_home)
    assert "HERMES_REPO" not in env and "PYTHONPATH" not in env
    assert spawn.call_args.kwargs["stdout"].closed
    assert registry.gateway_registry_state(packaged_runtime.hermes_home)["verified"] is True
    sync.assert_not_called()
    effect.assert_not_called()


@pytest.mark.asyncio
async def test_development_retains_operation_contract(monkeypatch):
    monkeypatch.delenv(policy.DESCRIPTOR_ENV, raising=False)
    assert policy.get_runtime().mode == "development"
    assert policy.runtime_capability_allowed("external_hermes_install") is True
    assert policy.runtime_capability_allowed("external_hermes_update") is True
    assert policy.runtime_capability_allowed("source_update") is True
    assert policy.runtime_capability_allowed("profile_extras") is True
    assert policy.runtime_capability_allowed("external_autostart") is True
    assert policy.runtime_capability_allowed("bundled_update") is False
    assert "runtime" not in json.loads((await ws.get_status()).body)


def test_static_mount_uses_declared_assets_instead_of_cwd_or_argument(packaged_runtime, tmp_path, monkeypatch):
    decoy = tmp_path / "external-face"
    decoy.mkdir()
    fake_app = SimpleNamespace(state=SimpleNamespace(), mount=Mock())
    monkeypatch.setattr(ws, "app", fake_app)
    ws.mount_static_files(decoy)
    assert fake_app.state.face_dir == packaged_runtime.context.assets_root
    face_mount = fake_app.mount.call_args_list[0]
    assert face_mount.args[0] == "/face"
    assert Path(face_mount.args[1].directory) == packaged_runtime.context.assets_root


@pytest.mark.asyncio
async def test_development_schema2_explicit_hermes_is_authoritative_for_wizard_and_gateway(packaged_runtime, monkeypatch):
    spec = dict(packaged_runtime.spec, mode="development")
    packaged_runtime.descriptor.write_text(json.dumps(spec), encoding="utf-8")
    runtime = policy.get_runtime()
    assert not runtime.packaged and runtime.hermes is not None
    decoy = runtime.hermes.cli.parent / "python.exe"
    decoy.write_text("CLI neighbor must not select an interpreter", encoding="utf-8")
    effect = no_effect_mock()
    monkeypatch.setattr("shutil.which", effect)
    monkeypatch.setattr(wizard, "resolve_cmd", effect)
    expected = [str(runtime.hermes.python), str(runtime.hermes.cli)]
    assert wizard._resolve_hermes_invocation()[0] == expected
    assert hermes_config.hermes_cli_available() is True
    assert (await health_check.check_cli_in_path())["ok"] is True

    cfg = SimpleNamespace(hermes_url="http://127.0.0.1:8642", llm_provider="ollama-local",
                          llm_model="test-model", llm_api_key="", llm_use_oauth=False)
    monkeypatch.setattr(wizard.IRISConfig, "load", lambda: cfg)
    monkeypatch.setattr(health_check, "check_gateway_port", AsyncMock(return_value={"ok": False}))
    monkeypatch.setattr(health_check, "check_gateway_health", AsyncMock(return_value={"ok": True}))
    monkeypatch.setattr(hermes_config, "read_hermes_dotenv", lambda: {"LLM_PROVIDER": "ollama-local", "LLM_MODEL": "test-model"})
    monkeypatch.setattr(hermes_config, "has_hermes_auth_profile", lambda *a, **kw: False)
    monkeypatch.setattr(hermes_config, "read_provider_base_url", lambda _: "")
    monkeypatch.setattr(hermes_config, "_read_env_file", lambda: {})
    monkeypatch.setattr(wizard, "_external_provider_preflight", AsyncMock(return_value=None))
    monkeypatch.setattr(wizard, "_local_provider_preflight", AsyncMock(return_value=None))
    monkeypatch.setattr(wizard, "sync_hermes_config_from_iris", AsyncMock())
    monkeypatch.setattr(registry, "record_spawned_gateway", effect)
    process = Mock(pid=24680, poll=lambda: None)
    spawn = Mock(return_value=process)
    monkeypatch.setattr(wizard.subprocess, "Popen", spawn)
    monkeypatch.setattr("socket.create_connection", MagicMock())
    response = await wizard._start_gateway_impl()
    assert json.loads(response.body)["status"] == "success"
    assert spawn.call_args.args[0] == expected + ["gateway", "run"]
    assert spawn.call_args.kwargs["cwd"] == str(runtime.hermes.root)
    effect.assert_not_called()
