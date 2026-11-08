"""Thin bridge from IRIS to Hermes' native cronjob tool.

IRIS should not own scheduling logic. Hermes already stores and runs
cron jobs, so this module only calls Hermes' local cronjob tool and
normalizes the JSON response for the face UI.
"""

from __future__ import annotations

import json
import contextlib
import re
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

from core.hermes_runtime import (
    NO_WINDOW, get_hermes_home, get_hermes_repo, hermes_python_prefix,
    hermes_subprocess_env, hermes_timeout, run_hermes_cli,
)
from core.runtime_descriptor import get_runtime
from core.response_text import visible_response_text


class HermesCronError(RuntimeError):
    """Raised when the Hermes cron bridge cannot complete a request."""


def _hermes_repo() -> Path:
    return get_hermes_repo()


def _hermes_python(repo: Path) -> Path | str:
    for candidate in (
        repo / "venv" / "Scripts" / "python.exe",
        repo / ".venv" / "Scripts" / "python.exe",
    ):
        if candidate.exists():
            return candidate
    return sys.executable


def _hermes_home() -> Path:
    return get_hermes_home()


_CRON_MODULE_LOCK = threading.RLock()


def _list_cron_jobs_in_process(include_disabled: bool = True) -> dict[str, Any] | None:
    """Fast path mirroring Hermes Dashboard: call cron.jobs in-process.

    Spawning Hermes for a read-only list makes the Routines tab feel slow.
    Hermes' own dashboard retargets cron.jobs globals and calls list_jobs
    directly, so we do the same for IRIS's default profile.
    """
    # Private Iris and Hermes environments must not import each other's modules.
    if get_runtime().packaged:
        return None
    repo = _hermes_repo()
    if not repo.exists():
        return None

    inserted = False
    repo_str = str(repo)
    if repo_str not in sys.path:
        sys.path.insert(0, repo_str)
        inserted = True

    try:
        from cron import jobs as cron_jobs  # type: ignore

        home = _hermes_home()
        with _CRON_MODULE_LOCK:
            old_cron_dir = cron_jobs.CRON_DIR
            old_jobs_file = cron_jobs.JOBS_FILE
            old_output_dir = cron_jobs.OUTPUT_DIR
            cron_jobs.CRON_DIR = home / "cron"
            cron_jobs.JOBS_FILE = cron_jobs.CRON_DIR / "jobs.json"
            cron_jobs.OUTPUT_DIR = cron_jobs.CRON_DIR / "output"
            try:
                jobs = cron_jobs.list_jobs(include_disabled)
            finally:
                cron_jobs.CRON_DIR = old_cron_dir
                cron_jobs.JOBS_FILE = old_jobs_file
                cron_jobs.OUTPUT_DIR = old_output_dir
    except Exception:
        return None
    finally:
        if inserted:
            with contextlib.suppress(ValueError):
                sys.path.remove(repo_str)

    if not isinstance(jobs, list):
        return None
    return {
        "success": True,
        "jobs": jobs,
        "count": len(jobs),
        "source_mode": "hermes-cron-module",
    }


def run_cronjob(**payload: Any) -> dict[str, Any]:
    """Run Hermes' ``cronjob`` tool and return its JSON payload."""

    repo = _hermes_repo()
    if not repo.exists():
        raise HermesCronError(f"Hermes repo not found at {repo}")

    script = (
        "import json, sys\n"
        f"sys.path.insert(0, {str(repo)!r})\n"
        "from tools.cronjob_tools import cronjob\n"
        "payload = json.loads(sys.stdin.read() or '{}')\n"
        "result = cronjob(**payload)\n"
        "print(result if isinstance(result, str) else json.dumps(result, ensure_ascii=False))\n"
    )
    runtime = get_runtime()
    prefix = hermes_python_prefix(None if runtime.hermes else _hermes_python(repo))
    if not prefix:
        raise HermesCronError("Hermes Python was not found")

    try:
        proc = subprocess.run(
            [*prefix, "-c", script],
            input=json.dumps(payload, ensure_ascii=False),
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(repo),
            env=hermes_subprocess_env(),
            capture_output=True,
            timeout=hermes_timeout(25),
            check=False,
            creationflags=NO_WINDOW,
        )
    except subprocess.TimeoutExpired as exc:
        raise HermesCronError("Hermes cron request timed out") from exc

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise HermesCronError(detail or f"Hermes cron exited with {proc.returncode}")

    stdout = (proc.stdout or "").strip()
    if not stdout:
        return {"success": False, "error": "Hermes cron returned an empty response"}

    try:
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise HermesCronError(f"Could not parse Hermes cron response: {stdout[:500]}") from exc


def _list_cron_jobs_cli(include_disabled: bool = True) -> dict[str, Any] | None:
    args = ["cron", "list"]
    if include_disabled:
        args.append("--all")
    result = run_hermes_cli(args, timeout=20)
    if not result.get("success"):
        return None

    output = str(result.get("output") or "")
    if re.search(r"no scheduled jobs", output, flags=re.IGNORECASE):
        return {
            "success": True,
            "jobs": [],
            "count": 0,
            "source_mode": "hermes-cli",
            "message": "No scheduled jobs.",
        }

    # Hermes cron list output is terminal-oriented. Until it exposes JSON,
    # keep rich job parsing on the internal tool and use CLI only for the
    # empty-state fast path.
    return None


def list_cron_jobs(include_disabled: bool = True) -> dict[str, Any]:
    module_result = _list_cron_jobs_in_process(include_disabled=include_disabled)
    if module_result:
        return module_result
    cli_result = _list_cron_jobs_cli(include_disabled=include_disabled)
    if cli_result:
        return cli_result
    result = run_cronjob(action="list", include_disabled=include_disabled)
    if isinstance(result, dict):
        result.setdefault("source_mode", "hermes-cronjob-tool")
    return result


def create_cron_job(payload: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "prompt",
        "schedule",
        "name",
        "repeat",
        "deliver",
        "skill",
        "skills",
        "model",
        "provider",
        "base_url",
        "script",
        "context_from",
        "enabled_toolsets",
        "workdir",
        "profile",
        "no_agent",
    }
    clean = {k: v for k, v in payload.items() if k in allowed and v not in ("", None)}
    clean["action"] = "create"
    return run_cronjob(**clean)


def update_cron_job(job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Update a Hermes cron job through the native cronjob tool.

    Hermes owns the actual scheduler schema, so IRIS only forwards fields
    that are documented/common in Hermes cron jobs and ignores empty values.
    """
    allowed = {
        "prompt",
        "schedule",
        "name",
        "repeat",
        "deliver",
        "skill",
        "skills",
        "model",
        "provider",
        "base_url",
        "script",
        "context_from",
        "enabled_toolsets",
        "workdir",
        "profile",
        "no_agent",
        "enabled",
        "disabled",
        "timezone",
        "description",
    }
    clean = {k: v for k, v in payload.items() if k in allowed and v not in ("", None)}
    return manage_cron_job("update", job_id, **clean)


def manage_cron_job(action: str, job_id: str, **payload: Any) -> dict[str, Any]:
    clean = {k: v for k, v in payload.items() if v not in ("", None)}
    return run_cronjob(action=action, job_id=job_id, **clean)


def _extract_response_text(raw: str) -> str:
    match = re.search(r"## Response\s+(.*)", raw, flags=re.IGNORECASE | re.DOTALL)
    response = match.group(1).strip() if match else raw.strip()
    return visible_response_text(response)


def _clean_job_id(job_id: str | None) -> str:
    if not job_id:
        return ""
    return re.sub(r"[^A-Za-z0-9_.-]", "", str(job_id))


def _infer_output_status(raw: str) -> dict[str, Any]:
    lowered = raw.lower()
    has_error = any(token in lowered for token in ("traceback", " error:", "exception", "failed", "fallo", "could not"))
    delivery = "error" if has_error else "local-output"
    return {
        "status": "error" if has_error else "completed",
        "delivery_status": delivery,
        "has_error": has_error,
        "error": raw[:280].strip() if has_error else "",
    }


def list_cron_outputs(limit: int = 20, job_id: str | None = None) -> dict[str, Any]:
    """Return recent local cron outputs saved by Hermes."""

    output_dir = _hermes_home() / "cron" / "output"
    if not output_dir.exists():
        return {"success": True, "count": 0, "outputs": []}

    clean_job_id = _clean_job_id(job_id)
    pattern = f"{clean_job_id}/*.md" if clean_job_id else "*/*.md"
    files = sorted(
        output_dir.glob(pattern),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[: max(1, min(int(limit or 20), 100))]

    outputs: list[dict[str, Any]] = []
    for path in files:
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
            title_match = re.search(r"^#\s+Cron Job:\s*(.+)$", raw, flags=re.MULTILINE)
            run_match = re.search(r"^\*\*Run Time:\*\*\s*(.+)$", raw, flags=re.MULTILINE)
            stat = path.stat()
            status_info = _infer_output_status(raw)
            text = _extract_response_text(raw)
            outputs.append({
                "id": f"{path.parent.name}/{path.name}",
                "job_id": path.parent.name,
                "name": title_match.group(1).strip() if title_match else path.parent.name,
                "run_time": run_match.group(1).strip() if run_match else "",
                "mtime_ms": int(stat.st_mtime * 1000),
                "path": str(path),
                "text": text,
                "summary": text[:180],
                **status_info,
            })
        except OSError:
            continue

    return {"success": True, "count": len(outputs), "outputs": outputs, "job_id": clean_job_id}

