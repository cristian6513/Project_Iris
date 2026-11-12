"""Shared Hermes runtime helpers for IRIS bridges.

This module is the single place where IRIS discovers Hermes paths and
invokes the Hermes CLI. Feature bridges should prefer these helpers over
hardcoded installer paths, and keep their own filesystem fallbacks only for
older Hermes builds or temporarily unavailable CLI commands.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Sequence

from core.workspace_sync import get_hermes_home
from core.runtime_descriptor import (
    RuntimeDescriptorError, get_runtime, require_runtime_capability, runtime_bytecode_flags,
    runtime_subprocess_env,
)


ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
# Windows: a console program started from a process without a visible console
# opens its own window; Hermes commands must never flash one (qa9).
NO_WINDOW = 0x08000000 if os.name == "nt" else 0
# A cold start of the included engine imports hundreds of modules; on a slow PC
# the first command can need far more than the few seconds a warm one takes.
PACKAGED_MIN_TIMEOUT = 60
# What the API server Iris talks to cannot use. A model's delegate_task always
# runs detached there and nothing wakes Iris when it ends; execute_code is
# refused on unattended platforms. Offered, each only costs the turn seconds.
API_SERVER_TOOLS_OFF = ("delegation", "code_execution")


def get_hermes_config_path() -> Path:
    """Return Hermes' canonical config.yaml path."""
    return get_hermes_home() / "config.yaml"


def keep_unusable_tools_off(profile: str | None = None) -> bool:
    """Verify the effective API tools in the profile this turn will use."""
    from core.hermes_api_tools import ensure_api_tools

    return ensure_api_tools(profile)


def get_hermes_env_path() -> Path:
    """Return Hermes' canonical .env path."""
    return get_hermes_home() / ".env"


def _candidate_repos() -> list[Path]:
    runtime = get_runtime()
    if runtime.hermes:
        return [runtime.hermes.root]
    candidates: list[Path] = []
    for key in ("HERMES_REPO", "HERMES_AGENT_ROOT"):
        value = os.environ.get(key, "").strip()
        if value:
            candidates.append(Path(value).expanduser())

    home = get_hermes_home()
    candidates.append(home / "hermes-agent")

    local = os.environ.get("LOCALAPPDATA", "").strip()
    if local:
        candidates.append(Path(local) / "hermes" / "hermes-agent")

    candidates.append(Path.home() / ".hermes" / "hermes-agent")
    if os.name != "nt":
        # The official install.sh uses this FHS layout for new root installs
        # on Linux and links the launcher into /usr/local/bin.
        candidates.append(Path("/usr/local/lib/hermes-agent"))

    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate).lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def get_hermes_repo() -> Path:
    """Return the best known Hermes repo/install root.

    The official Windows installer uses LOCALAPPDATA, while root Linux installs
    use /usr/local/lib/hermes-agent. Users can override either layout with
    HERMES_HOME or HERMES_REPO. Returning a non-existing fallback path is
    intentional so callers can produce useful diagnostics.
    """
    candidates = _candidate_repos()
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return candidates[0]


def resolve_hermes_cli() -> str | None:
    """Return the supported managed launcher, with PATH as a fallback.

    An old ``pip install hermes-agent`` can leave a global console script on
    PATH even after the official installer creates a current managed runtime.
    Hermes documents pip installs as unsupported, so managed layouts must win
    whenever both are present.
    """
    runtime = get_runtime()
    if runtime.hermes:
        return str(runtime.hermes.cli)
    candidates: list[Path] = []
    for repo in _candidate_repos():
        candidates.extend([
            repo / "venv" / "Scripts" / "hermes.exe",
            repo / ".venv" / "Scripts" / "hermes.exe",
            repo / "venv" / "bin" / "hermes",
            repo / ".venv" / "bin" / "hermes",
        ])

    # A process that predates installation does not inherit the newly written
    # PATH. Check Hermes' documented Unix launcher location explicitly.
    local_bin = Path.home() / ".local" / "bin"
    candidates.extend([local_bin / "hermes", local_bin / "hermes.exe"])
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return shutil.which("hermes") or shutil.which("hermes.exe")


def hermes_command_prefix() -> list[str] | None:
    """Return an argv prefix that can execute the discovered Hermes CLI.

    Windows setuptools launchers can occasionally skip their venv's ``.pth``
    initialisation. When the expected managed layout is present we run the same
    zipapp through that venv's Python instead, which preserves site-packages
    discovery. Other installations keep their ordinary launcher untouched.
    """
    runtime = get_runtime()
    if runtime.hermes:
        return [*hermes_python_prefix(), str(runtime.hermes.cli)]
    cli = resolve_hermes_cli()
    if not cli:
        return None
    path = Path(cli)
    if os.name == "nt":
        scripts = path.parent
        python = scripts / "python.exe"
        repo = scripts.parent.parent
        package_init = repo / "hermes_cli" / "__init__.py"
        if python.exists() and package_init.exists():
            return [str(python), str(path)]
    return [str(path)]


def resolve_hermes_python() -> str | None:
    """Return the Python interpreter owned by the managed Hermes install.

    Native feature bridges (wake word, voice recording, TUI RPC) must run in
    Hermes' environment: IRIS' virtualenv intentionally does not carry those
    optional audio dependencies.  Deriving the interpreter from the supported
    launcher also avoids hard-coding the Windows installer layout.
    """
    runtime = get_runtime()
    if runtime.hermes:
        return str(runtime.hermes.python)
    cli = resolve_hermes_cli()
    if cli:
        scripts = Path(cli).parent
        names = ("python.exe", "python") if os.name == "nt" else ("python", "python3")
        for name in names:
            candidate = scripts / name
            if candidate.exists():
                return str(candidate)

    repo = get_hermes_repo()
    candidates = (
        repo / "venv" / "Scripts" / "python.exe",
        repo / ".venv" / "Scripts" / "python.exe",
        repo / "venv" / "bin" / "python",
        repo / ".venv" / "bin" / "python",
    )
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None


def hermes_python_prefix(python: str | Path | None = None) -> list[str] | None:
    """Run Python bridges in the declared Hermes environment.

    A caller-supplied interpreter is a development compatibility fallback only.
    A pinned runtime always chooses its own Python and packaged isolation flags.
    """
    runtime = get_runtime()
    if runtime.hermes:
        flags = ["-E", "-s", *runtime_bytecode_flags(), "-X", "utf8"] if runtime.packaged else []
        return [str(runtime.hermes.python), *flags]
    interpreter = str(python) if python is not None else resolve_hermes_python()
    return [interpreter] if interpreter else None


def hermes_subprocess_env() -> dict[str, str]:
    """Prevent IRIS' venv from leaking into Hermes' managed runtime."""
    blocked = {
        "VIRTUAL_ENV",
        "__PYVENV_LAUNCHER__",
        "PYTHONHOME",
        "PYTHONPATH",
        "PYTHONSTARTUP",
        "PYTHONNOUSERSITE",
        "UV_PROJECT_ENVIRONMENT",
    }
    env = runtime_subprocess_env({key: value for key, value in os.environ.items() if key not in blocked})
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env["HERMES_HOME"] = str(get_hermes_home())
    runtime = get_runtime()
    if runtime.packaged:
        from core.audio_decoder import private_audio_bin
        from core.private_shell import private_shell_bin

        # Fresh Windows guests may have only a small set of OS roots. Hermes'
        # urllib account requests do not use requests/httpx's certifi defaults.
        # Give this private process (and its children) the CA bundle already
        # shipped in its verified runtime. SSL verification stays enabled;
        # neither the parent's environment nor the Windows trust store changes.
        env["SSL_CERT_FILE"] = str(
            runtime.hermes.python.parent / "Lib/site-packages/certifi/cacert.pem"
        )
        audio_bin = private_audio_bin(runtime)
        shell_bin = private_shell_bin(runtime)
        # Audio tools come from the same verified installation. Retain Windows
        # shell utilities, but do not inherit a developer Python/Hermes/codec PATH.
        paths = [str(audio_bin), str(runtime.hermes.python.parent), str(shell_bin)]
        if os.name == "nt":
            windows = Path(os.environ.get("SystemRoot", r"C:\Windows"))
            paths.extend([str(windows / "System32"), str(windows / "System32/WindowsPowerShell/v1.0")])
        env["PATH"] = os.pathsep.join(paths)
        for key in list(env):
            if key.upper() in {"HERMES_LAZY_INSTALL_TARGET", "HERMES_GIT_BASH_PATH", "BASH_ENV", "ENV", "SHELL"} or key.upper().startswith("BASH_FUNC_"):
                env.pop(key, None)
        env["HERMES_GIT_BASH_PATH"] = str(shell_bin / "bash.exe")
        env["SHELL"] = str(shell_bin / "bash.exe")
        env["HERMES_DISABLE_LAZY_INSTALLS"] = "1"
    return env


# Backward-compatible private name for older in-tree callers.
_hermes_subprocess_env = hermes_subprocess_env


def clean_cli_output(value: str) -> str:
    """Strip terminal styling and normalize common mojibake from CLI output."""
    value = ANSI_RE.sub("", value or "")
    replacements = {
        "â”‚": "|",
        "â”€": "-",
        "â”Œ": "+",
        "â”": "+",
        "â””": "+",
        "â”˜": "+",
        "â€¦": "...",
        "âœ“": "OK",
    }
    for bad, good in replacements.items():
        value = value.replace(bad, good)
    return value.replace("\r\n", "\n").strip()


def hermes_timeout(seconds: float) -> float:
    """Room for a cold start of the included engine; development keeps its limits."""
    return max(seconds, PACKAGED_MIN_TIMEOUT) if get_runtime().packaged else seconds


def hermes_failure_message(exc: BaseException) -> str:
    """What the user reads when a Hermes process fails; the command line stays in the detail."""
    if isinstance(exc, subprocess.TimeoutExpired):
        return f"Hermes tardó más de {int(exc.timeout or 0)} s en responder. Vuelve a intentarlo."
    return f"No se pudo iniciar Hermes: {getattr(exc, 'strerror', '') or exc}"


def run_hermes_cli(
    args: Sequence[str],
    *,
    timeout: int | None = 30,
    cwd: str | Path | None = None,
) -> dict[str, Any]:
    """Run Hermes CLI without shell/profile noise and return a safe result."""
    if args and args[0] == "update":
        require_runtime_capability("external_hermes_update")
    command_prefix = hermes_command_prefix()
    if not command_prefix:
        return {
            "success": False,
            "returncode": None,
            "stdout": "",
            "stderr": "",
            "output": "",
            "error": "Hermes CLI was not found",
            "cli_path": "",
            "source": "hermes-cli",
        }

    cli = command_prefix[-1]
    env = _hermes_subprocess_env()

    runtime = get_runtime()
    if runtime.packaged:
        run_cwd = runtime.hermes.root
        if cwd and Path(cwd).resolve(strict=True) != run_cwd:
            raise RuntimeDescriptorError("El comando debe usar el runtime Hermes incluido.")
        if timeout is not None:
            timeout = hermes_timeout(timeout)
    else:
        run_cwd = Path(cwd).expanduser() if cwd else get_hermes_repo()
    if not run_cwd.exists():
        if runtime.packaged:
            raise RuntimeDescriptorError("Falta el runtime Hermes incluido.")
        run_cwd = Path.cwd()

    try:
        proc = subprocess.run(
            [*command_prefix, *[str(arg) for arg in args]],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=str(run_cwd),
            env=env,
            check=False,
            creationflags=NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "success": False,
            "returncode": None,
            "stdout": "",
            "stderr": "",
            "output": "",
            "error": hermes_failure_message(exc),
            "detail": str(exc),
            "cli_path": cli,
            "source": "hermes-cli",
        }

    stdout = clean_cli_output(proc.stdout or "")
    stderr = clean_cli_output(proc.stderr or "")
    output = clean_cli_output(f"{stdout}\n{stderr}".strip())
    return {
        "success": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "output": output,
        "error": "" if proc.returncode == 0 else (stderr or stdout),
        "cli_path": cli,
        "source": "hermes-cli",
    }


def _line_value(output: str, label: str) -> str:
    pattern = rf"^\s*{re.escape(label)}\s*:\s*(.+?)\s*$"
    match = re.search(pattern, output or "", flags=re.IGNORECASE | re.MULTILINE)
    return match.group(1).strip() if match else ""


def _parse_status(output: str) -> dict[str, str]:
    return {
        "project": _line_value(output, "Project"),
        "python": _line_value(output, "Python"),
        "model": _line_value(output, "Model"),
        "provider": _line_value(output, "Provider"),
        "env_file": _line_value(output, ".env file"),
        "gateway_service": _line_value(output, "Gateway Service"),
        "scheduled_jobs": _line_value(output, "Scheduled Jobs"),
        "sessions": _line_value(output, "Sessions"),
    }


def hermes_diagnostics() -> dict[str, Any]:
    """Return a compact, secret-free Hermes diagnostics snapshot."""
    commands = {
        "status": (["status"], 8),
        "skills": (["skills", "list", "--source", "all"], 10),
        "mcp": (["mcp", "catalog"], 8),
        "cron": (["cron", "status"], 8),
    }
    with ThreadPoolExecutor(max_workers=len(commands)) as executor:
        futures = {
            name: executor.submit(run_hermes_cli, args, timeout=timeout)
            for name, (args, timeout) in commands.items()
        }
        results = {name: future.result() for name, future in futures.items()}

    status = results["status"]
    skills = results["skills"]
    mcp = results["mcp"]
    cron = results["cron"]

    parsed_status = _parse_status(status.get("output", ""))
    return {
        "success": bool(status.get("success")) or bool(resolve_hermes_cli()),
        "hermes_home": str(get_hermes_home()),
        "config_path": str(get_hermes_config_path()),
        "env_path": str(get_hermes_env_path()),
        "repo_path": str(get_hermes_repo()),
        "cli_path": resolve_hermes_cli() or "",
        "status": parsed_status,
        "commands": {
            "status": {
                "success": bool(status.get("success")),
                "error": str(status.get("error") or "")[:500],
            },
            "skills": {
                "success": bool(skills.get("success")),
                "summary": _summarize_skills_output(skills.get("output", "")),
                "error": str(skills.get("error") or "")[:500],
            },
            "mcp": {
                "success": bool(mcp.get("success")),
                "summary": _summarize_mcp_output(mcp.get("output", "")),
                "error": str(mcp.get("error") or "")[:500],
            },
            "cron": {
                "success": bool(cron.get("success")),
                "summary": _summarize_cron_output(cron.get("output", "")),
                "error": str(cron.get("error") or "")[:500],
            },
        },
    }


def _summarize_skills_output(output: str) -> dict[str, Any]:
    text = output or ""
    enabled = re.search(r"(\d+)\s+enabled", text, flags=re.IGNORECASE)
    disabled = re.search(r"(\d+)\s+disabled", text, flags=re.IGNORECASE)
    builtin = re.search(r"(\d+)\s+builtin", text, flags=re.IGNORECASE)
    return {
        "enabled": int(enabled.group(1)) if enabled else None,
        "disabled": int(disabled.group(1)) if disabled else None,
        "builtin": int(builtin.group(1)) if builtin else None,
    }


def _summarize_mcp_output(output: str) -> dict[str, Any]:
    lines = [line for line in (output or "").splitlines() if line.strip()]
    catalog_rows = [
        line for line in lines
        if re.match(r"^\s*[A-Za-z0-9_.-]+\s+\w+\s+", line)
        and not line.lower().startswith(("name ", "install "))
    ]
    return {"catalog_rows": len(catalog_rows)}


def _summarize_cron_output(output: str) -> dict[str, Any]:
    text = output or ""
    gateway_down = bool(re.search(r"gateway\s+is\s+not\s+running|will\s+not\s+fire", text, flags=re.IGNORECASE))
    gateway_up = bool(re.search(r"gateway\s+is\s+running|scheduler\s+running", text, flags=re.IGNORECASE))
    if re.search(r"no scheduled jobs", text, flags=re.IGNORECASE):
        return {
            "jobs": 0,
            "gateway_running": False if gateway_down else (True if gateway_up else None),
            "message": "Gateway detenido: las rutinas no se ejecutan solas." if gateway_down else "No scheduled jobs.",
        }
    rows = [
        line for line in text.splitlines()
        if line.strip() and not line.lower().startswith(("id ", "name ", "schedule "))
    ]
    return {
        "rows": max(0, len(rows)),
        "gateway_running": False if gateway_down else (True if gateway_up else None),
        "message": "Gateway detenido: las rutinas no se ejecutan solas." if gateway_down else "",
    }


__all__ = [
    "clean_cli_output",
    "get_hermes_config_path",
    "get_hermes_env_path",
    "get_hermes_home",
    "get_hermes_repo",
    "hermes_command_prefix",
    "hermes_diagnostics",
    "hermes_python_prefix",
    "hermes_subprocess_env",
    "hermes_timeout",
    "resolve_hermes_cli",
    "resolve_hermes_python",
    "keep_unusable_tools_off",
    "run_hermes_cli",
]

