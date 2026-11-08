"""A08.2: profile rejection remains a diagnostic, without starting Hermes."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from core import runtime_descriptor
from wizard import app as wizard
from wizard.hermes_config import get_hermes_home


@pytest.mark.parametrize("invalid_home", ["file", "inside-installation"])
@pytest.mark.parametrize("boundary", ["python", "repo", "environment"])
def test_runtime_health_rejects_invalid_home_before_subprocess(
    tmp_path, monkeypatch, invalid_home, boundary
):
    installation = tmp_path / "installation"
    installation.mkdir()
    if invalid_home == "file":
        home = tmp_path / "not-a-directory"
        home.write_bytes(b"unchanged fixture")
    else:
        home = installation / "private-profile"
    monkeypatch.setenv("HERMES_HOME", str(home))
    runtime = SimpleNamespace(packaged=invalid_home == "inside-installation", base=installation)
    monkeypatch.setattr(runtime_descriptor, "get_runtime", lambda: runtime)
    monkeypatch.setattr(
        wizard,
        "resolve_hermes_python",
        lambda: str(get_hermes_home() / "python") if boundary == "python" else "qa-python",
    )
    monkeypatch.setattr(
        wizard, "get_hermes_repo", lambda: get_hermes_home() if boundary == "repo" else tmp_path
    )
    monkeypatch.setattr(wizard, "hermes_command_prefix", lambda: ["qa-python", "qa-hermes.py"])
    monkeypatch.setattr(
        wizard,
        "hermes_subprocess_env",
        lambda: {"HERMES_HOME": str(get_hermes_home())},
    )
    launch = Mock(side_effect=AssertionError("Invalid profiles must never launch a probe"))
    monkeypatch.setattr(wizard.subprocess, "run", launch)

    healthy, diagnostic = wizard._hermes_runtime_health()

    assert healthy is False
    assert diagnostic.startswith("El perfil de Hermes no es válido: ")
    assert "Traceback" not in diagnostic
    launch.assert_not_called()
    assert not list(installation.iterdir())
    if invalid_home == "file":
        assert home.read_bytes() == b"unchanged fixture"
    else:
        assert not home.exists()
