from __future__ import annotations

import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException, Request

from wizard.app import (
    _INSTALLER_MAX_BYTES,
    _enforce_local_install_request,
    _external_provider_preflight,
    _hermes_installation_state,
    _hermes_install_readiness,
    _hermes_runtime_health,
    _installer_environment,
    _is_hermes_gateway_command,
    _local_provider_preflight,
    _trusted_request_origin,
    _validate_installer_payload,
    _verify_hermes_install,
    app,
    hermes_status,
    install_hermes_stream,
    _stop_running_gateway,
)


def test_shell_bootstrap_never_promises_an_unimplemented_engine() -> None:
    source = (Path(__file__).resolve().parents[1] / "install.sh").read_text(encoding="utf-8")

    assert 'ENGINE="hermes"' in source
    assert 'ENGINE="picoclaw"' not in source
    assert "use a remote provider" in source


def _supported(system: str) -> dict[str, object]:
    return {
        "status": "supported",
        "tier": "Tier 1",
        "label": f"{system} compatible",
        "detail": "Supported.",
        "system": system,
        "architecture": "x86_64",
        "supported": True,
    }


def _request(headers: dict[str, str]) -> Request:
    return Request({
        "type": "http",
        "method": "POST",
        "scheme": "http",
        "path": "/api/install-hermes-stream",
        "raw_path": b"/api/install-hermes-stream",
        "query_string": b"",
        "headers": [
            (key.lower().encode("latin-1"), value.encode("latin-1"))
            for key, value in headers.items()
        ],
        "client": ("127.0.0.1", 50000),
        "server": ("127.0.0.1", 5177),
    })


def test_installer_payload_validation_records_audit_digest() -> None:
    payload = b"#!/bin/bash\n# Hermes Agent Installer\n" + (b"# safe\n" * 180)
    metadata = _validate_installer_payload("Linux", payload)

    assert metadata["bytes"] == len(payload)
    assert len(str(metadata["sha256"])) == 64
    assert metadata["platform"] == "Linux"


def test_installer_payload_validation_rejects_wrong_and_oversized_content() -> None:
    for payload in (
        b"<html>not an installer</html>",
        b"#!/bin/bash\n" + (b"# unrelated\n" * 200),
        b"#!/bin/bash\n# Hermes Agent Installer\n" + (b"x" * _INSTALLER_MAX_BYTES),
    ):
        try:
            _validate_installer_payload("Linux", payload)
        except RuntimeError:
            continue
        raise AssertionError("unsafe installer payload was accepted")


def test_windows_readiness_does_not_false_block_managed_dependencies() -> None:
    def probe(command: str, *_args: str) -> tuple[bool, str]:
        if command == "powershell":
            return True, "5.1"
        return False, ""

    with (
        patch("wizard.app.platform.system", return_value="Windows"),
        patch("wizard.app._probe_command", side_effect=probe),
        patch("wizard.app.resolve_hermes_cli", return_value=None),
        patch("core.hermes_compatibility.platform_support", return_value=_supported("Windows")),
    ):
        report = _hermes_install_readiness()

    assert report["all_required_ok"] is True
    assert report["deps"]["git"]["status"] == "managed"
    assert report["deps"]["python"]["status"] == "managed"
    assert report["deps"]["node"]["status"] == "managed"
    assert report["deps"]["uv"]["status"] == "managed"


def test_linux_readiness_reports_real_posix_blockers() -> None:
    def probe(command: str, *_args: str) -> tuple[bool, str]:
        return (command in {"git", "bash", "xz"}, "present")

    with (
        patch("wizard.app.platform.system", return_value="Linux"),
        patch("wizard.app._probe_command", side_effect=probe),
        patch("wizard.app.resolve_hermes_cli", return_value=None),
        patch("core.hermes_compatibility.platform_support", return_value=_supported("Linux")),
    ):
        report = _hermes_install_readiness()

    assert report["all_required_ok"] is False
    assert report["blocking_dependencies"] == ["curl"]
    assert "curl" in report["hint"]


def test_linux_does_not_require_xz_when_compatible_node_is_present() -> None:
    def probe(command: str, *_args: str) -> tuple[bool, str]:
        values = {
            "git": "git version 2.50.0",
            "bash": "GNU bash 5.2",
            "curl": "curl 8.0",
            "node": "v22.22.0",
        }
        return (command in values, values.get(command, ""))

    with (
        patch("wizard.app.platform.system", return_value="Linux"),
        patch("wizard.app._probe_command", side_effect=probe),
        patch("wizard.app.resolve_hermes_cli", return_value=None),
        patch("core.hermes_compatibility.platform_support", return_value=_supported("Linux")),
    ):
        report = _hermes_install_readiness()

    assert report["all_required_ok"] is True
    assert report["deps"]["node"]["status"] == "ok"
    assert report["deps"]["xz"]["required"] is False
    assert report["deps"]["xz"]["status"] == "optional"


def test_install_endpoint_is_post_only() -> None:
    route = next(route for route in app.routes if getattr(route, "path", "") == "/api/install-hermes-stream")

    assert route.methods == {"POST"}


def test_install_origin_policy_accepts_only_local_or_private_hosts() -> None:
    assert _trusted_request_origin("http://127.0.0.1:5177")
    assert _trusted_request_origin("http://localhost:5177")
    assert _trusted_request_origin("https://192.168.1.22:5177")
    assert not _trusted_request_origin("https://evil.example")
    assert not _trusted_request_origin("file:///tmp/wizard.html")
    assert not _trusted_request_origin("not a url")


def test_install_request_requires_explicit_intent_and_rejects_cross_site() -> None:
    for headers in (
        {"origin": "http://127.0.0.1:5177"},
        {
            "x-iris-intent": "install-hermes",
            "origin": "http://127.0.0.1:5177",
            "sec-fetch-site": "cross-site",
        },
        {"x-iris-intent": "install-hermes", "origin": "https://evil.example"},
    ):
        try:
            _enforce_local_install_request(_request(headers))
        except HTTPException as exc:
            assert exc.status_code == 403
            continue
        raise AssertionError("unsafe install request was accepted")

    _enforce_local_install_request(_request({
        "x-iris-intent": "install-hermes",
        "origin": "http://127.0.0.1:5177",
        "sec-fetch-site": "same-origin",
    }))


def test_install_stream_emits_artifact_and_verified_terminal_event() -> None:
    class Output:
        def __init__(self) -> None:
            self.lines = [b"Installing managed uv\n", b""]

        async def readline(self) -> bytes:
            return self.lines.pop(0)

    class Process:
        def __init__(self) -> None:
            self.stdout = Output()
            self.returncode: int | None = None
            self.pid = 12345

        async def wait(self) -> int:
            self.returncode = 0
            return 0

    async def collect() -> list[dict[str, object]]:
        response = await install_hermes_stream(_request({
            "x-iris-intent": "install-hermes",
            "origin": "http://127.0.0.1:5177",
            "sec-fetch-site": "same-origin",
        }))
        events: list[dict[str, object]] = []
        async for chunk in response.body_iterator:
            text = chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk
            for frame in text.split("\n\n"):
                if frame.startswith("data: "):
                    events.append(json.loads(frame[6:]))
        return events

    with TemporaryDirectory() as temp_dir:
        script_path = Path(temp_dir) / "install.sh"
        script_path.write_text("#!/bin/bash\n", encoding="utf-8")
        artifact = {"bytes": 2048, "sha256": "a" * 64, "platform": "Linux"}
        process = Process()
        with (
            patch(
                "wizard.app._hermes_install_readiness",
                return_value={"all_required_ok": True},
            ),
            patch(
                "wizard.app._download_official_installer",
                new=AsyncMock(return_value=(script_path, artifact)),
            ),
            patch(
                "wizard.app.asyncio.create_subprocess_exec",
                new=AsyncMock(return_value=process),
            ),
            patch(
                "wizard.app._verify_hermes_install",
                return_value={"ok": True, "message": "Lanzador verificado."},
            ),
        ):
            events = asyncio.run(collect())

        assert not script_path.exists()

    assert any(event.get("type") == "artifact" for event in events)
    terminal = [event for event in events if event.get("type") == "done"]
    assert terminal == [{
        "type": "done",
        "ok": True,
        "returncode": 0,
        "verification": {"ok": True, "message": "Lanzador verificado."},
        "artifact": artifact,
    }]


def test_installer_environment_removes_the_iris_virtualenv() -> None:
    with patch.dict(
        "wizard.app.os.environ",
        {
            "PATH": "safe",
            "VIRTUAL_ENV": "iris-venv",
            "PYTHONPATH": "iris-path",
            "UV_PROJECT_ENVIRONMENT": "iris-uv",
        },
        clear=True,
    ):
        env = _installer_environment()

    assert env["PATH"] == "safe"
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert "VIRTUAL_ENV" not in env
    assert "PYTHONPATH" not in env
    assert "UV_PROJECT_ENVIRONMENT" not in env


def test_postinstall_verification_rejects_a_runnable_but_obsolete_cli() -> None:
    with (
        patch("wizard.app.resolve_hermes_cli", return_value="C:/Hermes/hermes.exe"),
        patch(
            "wizard.app.run_hermes_cli",
            return_value={"success": True, "output": "Hermes Agent v0.14.0"},
        ),
    ):
        result = _verify_hermes_install()

    assert result["ok"] is False
    assert "0.19.0" in result["message"]


def test_existing_broken_launcher_is_classified_for_repair() -> None:
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        with (
            patch("wizard.app.resolve_hermes_cli", return_value=str(root / "venv" / "hermes.exe")),
            patch("wizard.app.resolve_hermes_python", return_value=str(root / "venv" / "python.exe")),
            patch(
                "wizard.app.run_hermes_cli",
                return_value={"success": False, "error": "missing hermes_cli"},
            ),
            patch("wizard.app.get_hermes_repo", return_value=root),
            patch("wizard.app.get_hermes_config_path", return_value=root / "config.yaml"),
        ):
            state = _hermes_installation_state()

    assert state["status"] == "damaged"
    assert state["mode"] == "repair"
    assert "missing hermes_cli" in state["diagnostic"]


def test_unmanaged_global_launcher_without_runtime_offers_clean_install() -> None:
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        launcher = "C:/Users/Test/AppData/Local/Programs/Python/Python314/Scripts/hermes.exe"
        with (
            patch("wizard.app.resolve_hermes_cli", return_value=launcher),
            patch("wizard.app.resolve_hermes_python", return_value=None),
            patch(
                "wizard.app.run_hermes_cli",
                return_value={"success": True, "output": "Hermes Agent v0.14.0"},
            ),
            patch("wizard.app.get_hermes_repo", return_value=root / "missing-runtime"),
            patch("wizard.app.get_hermes_config_path", return_value=root / "config.yaml"),
        ):
            state = _hermes_installation_state()

    assert state["status"] == "missing"
    assert state["mode"] == "full"
    assert state["residual_launcher"] is True
    assert state["installation_kind"] == "unmanaged_launcher"
    assert state["cli_path"] == launcher
    assert state["version"] == "Hermes Agent v0.14.0"
    assert "runtime administrado" in state["diagnostic"]


def test_runnable_hermes_with_malformed_config_is_sent_to_safe_setup() -> None:
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        config = root / "config.yaml"
        config.write_text("model: [unterminated", encoding="utf-8")
        with (
            patch("wizard.app.resolve_hermes_cli", return_value="C:/Hermes/hermes.exe"),
            patch(
                "wizard.app.run_hermes_cli",
                return_value={"success": True, "output": "Hermes Agent v0.19.0"},
            ),
            patch("wizard.app.get_hermes_repo", return_value=root),
            patch("wizard.app.get_hermes_config_path", return_value=config),
            patch("wizard.app._hermes_runtime_health", return_value=(True, "")),
        ):
            state = _hermes_installation_state()

    assert state["status"] == "needs_setup"
    assert state["mode"] == "full"
    assert state["config_health"] == "invalid"
    assert state["diagnostic"]


def test_wake_only_hermes_config_requires_provider_setup_instead_of_defaulting() -> None:
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        config = root / "config.yaml"
        config.write_text(
            "wake_word:\n  enabled: false\n  phrase: hey hermes\n",
            encoding="utf-8",
        )
        with (
            patch("wizard.app.resolve_hermes_cli", return_value="C:/Hermes/hermes.exe"),
            patch(
                "wizard.app.run_hermes_cli",
                return_value={"success": True, "output": "Hermes Agent v0.20.0"},
            ),
            patch("wizard.app.get_hermes_repo", return_value=root),
            patch("wizard.app.get_hermes_config_path", return_value=config),
            patch("wizard.app._hermes_runtime_health", return_value=(True, "")),
        ):
            state = _hermes_installation_state()

    assert state["status"] == "needs_setup"
    assert state["mode"] == "full"
    assert state["config_health"] == "incomplete"
    assert "proveedor y modelo" in state["message"]


def test_version_only_launcher_with_missing_runtime_dependency_is_repairable() -> None:
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        config = root / "config.yaml"
        config.write_text("model:\n  provider: openrouter\n", encoding="utf-8")
        with (
            patch("wizard.app.resolve_hermes_cli", return_value="C:/Hermes/hermes.exe"),
            patch(
                "wizard.app.run_hermes_cli",
                return_value={"success": True, "output": "Hermes Agent v0.19.0"},
            ),
            patch("wizard.app.get_hermes_repo", return_value=root),
            patch("wizard.app.get_hermes_config_path", return_value=config),
            patch(
                "wizard.app._hermes_runtime_health",
                return_value=(False, "Falta el módulo concurrent_log_handler."),
            ),
        ):
            state = _hermes_installation_state()

    assert state["status"] == "damaged"
    assert state["mode"] == "repair"
    assert state["version"] == "Hermes Agent v0.19.0"
    assert "concurrent_log_handler" in state["diagnostic"]


def test_runtime_health_reports_missing_managed_module_without_traceback(tmp_path) -> None:
    process = type("Process", (), {
        "returncode": 1,
        "stdout": "",
        "stderr": "Traceback... ModuleNotFoundError: No module named 'missing_runtime'",
    })()
    with (
        patch("wizard.app.resolve_hermes_python", return_value="C:/Hermes/python.exe"),
        patch("wizard.app.get_hermes_repo", return_value=tmp_path),
        patch("wizard.app.hermes_subprocess_env", return_value={"HERMES_HOME": str(tmp_path)}),
        patch("wizard.app.subprocess.run", return_value=process),
    ):
        healthy, diagnostic = _hermes_runtime_health()

    assert healthy is False
    assert diagnostic == "Falta el módulo missing_runtime en el entorno administrado de Hermes."


def test_postinstall_verification_rejects_an_incomplete_runtime() -> None:
    with (
        patch("wizard.app.resolve_hermes_cli", return_value="C:/Hermes/hermes.exe"),
        patch(
            "wizard.app.run_hermes_cli",
            return_value={"success": True, "output": "Hermes Agent v0.19.0"},
        ),
        patch(
            "wizard.app._hermes_runtime_health",
            return_value=(False, "Falta el módulo concurrent_log_handler."),
        ),
    ):
        result = _verify_hermes_install()

    assert result["ok"] is False
    assert "runtime sigue incompleto" in result["message"]


def test_readiness_marks_broken_hermes_as_repairable_not_ready() -> None:
    def probe(command: str, *_args: str) -> tuple[bool, str]:
        return (command == "powershell", "5.1")

    broken = {
        "status": "damaged",
        "mode": "repair",
        "message": "Lanzador roto.",
        "cli_path": "C:/Hermes/hermes.exe",
        "version": "",
    }
    with (
        patch("wizard.app.platform.system", return_value="Windows"),
        patch("wizard.app._probe_command", side_effect=probe),
        patch("wizard.app._hermes_installation_state", return_value=broken),
        patch("core.hermes_compatibility.platform_support", return_value=_supported("Windows")),
    ):
        report = _hermes_install_readiness()

    assert report["all_required_ok"] is True
    assert report["deps"]["hermes"]["status"] == "broken"
    assert report["install_action"] == "repair"


def test_readiness_does_not_recommend_updating_a_validated_install() -> None:
    def probe(command: str, *_args: str) -> tuple[bool, str]:
        return (command == "powershell", "5.1")

    installed = {
        "status": "installed",
        "mode": "quick",
        "message": "Hermes listo.",
        "cli_path": "C:/Hermes/hermes.exe",
        "version": "Hermes Agent v0.19.0",
        "compatibility": {"status": "ready", "label": "Compatible"},
    }
    with (
        patch("wizard.app.platform.system", return_value="Windows"),
        patch("wizard.app._probe_command", side_effect=probe),
        patch("wizard.app._hermes_installation_state", return_value=installed),
        patch("core.hermes_compatibility.platform_support", return_value=_supported("Windows")),
    ):
        report = _hermes_install_readiness()

    assert report["deps"]["hermes"]["status"] == "ok"
    assert report["install_action"] == "none"
    assert "no ejecutará el instalador" in report["hint"]


def test_gateway_pid_identity_must_contain_hermes_and_gateway() -> None:
    assert _is_hermes_gateway_command(
        "python.exe", "C:/Hermes/hermes.exe", "gateway", "run",
    )
    assert not _is_hermes_gateway_command("python.exe", "unrelated_server.py", "--port", "8642")
    assert not _is_hermes_gateway_command("hermes.exe", "status")


def test_repair_does_not_kill_an_unrelated_reused_pid() -> None:
    with TemporaryDirectory() as temp_dir:
        home = Path(temp_dir)
        (home / "gateway.pid").write_text('{"pid": 424242}', encoding="utf-8")
        with (
            patch("core.workspace_sync.get_hermes_home", return_value=home),
            patch.dict("sys.modules", {"psutil": None}),
            patch("wizard.app._native_process_command", return_value="python unrelated_server.py --port 8642"),
            patch("wizard.app.subprocess.run") as killer,
        ):
            result = asyncio.run(_stop_running_gateway())

        assert not (home / "gateway.pid").exists()

    killer.assert_not_called()
    assert result["stopped"] is False
    assert result["stale_pid"] is True


def test_status_endpoint_routes_a_broken_launcher_to_explicit_repair() -> None:
    damaged = {
        "status": "damaged",
        "mode": "repair",
        "message": "Lanzador roto.",
        "diagnostic": "missing module",
        "cli_path": "C:/Hermes/hermes.exe",
        "version": "",
        "config_exists": True,
        "repo_exists": True,
    }
    with patch("wizard.app._hermes_installation_state", return_value=damaged):
        response = asyncio.run(hermes_status())
    payload = json.loads(response.body)

    assert payload["status"] == "damaged"
    assert payload["mode"] == "repair"
    assert payload["beta_warning"] is False


def test_local_provider_preflight_rejects_missing_selected_model() -> None:
    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"models": [{"name": "qwen2.5:3b"}]}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, _url: str) -> Response:
            return Response()

    with patch("wizard.app.httpx.AsyncClient", return_value=Client()):
        error = asyncio.run(_local_provider_preflight("ollama-local", "gemma3:4b"))

    assert error is not None
    assert error["code"] == "local_model_not_found"
    assert error["available_models"] == ["qwen2.5:3b"]


def test_local_provider_preflight_explains_that_iris_does_not_start_ollama() -> None:
    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url: str):
            request = httpx.Request("GET", url)
            raise httpx.ConnectError("offline", request=request)

    with patch("wizard.app.httpx.AsyncClient", return_value=Client()):
        error = asyncio.run(_local_provider_preflight("ollama-local", "qwen2.5:3b"))

    assert error is not None
    assert error["code"] == "local_server_unavailable"
    assert "IRIS no instala ni inicia" in error["message"]


def test_copilot_acp_is_not_mistaken_for_a_local_http_server() -> None:
    assert asyncio.run(_local_provider_preflight("copilot-acp", "gpt-4o")) is None


def test_copilot_acp_reports_missing_external_cli() -> None:
    with (
        patch("wizard.hermes_config.read_hermes_dotenv", return_value={}),
        patch("wizard.app.shutil.which", return_value=None),
    ):
        error = asyncio.run(_external_provider_preflight("copilot-acp"))

    assert error is not None
    assert error["code"] == "external_cli_missing"
