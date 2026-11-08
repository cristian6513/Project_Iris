"""Bridge from IRIS to Hermes' native Kanban board.

IRIS does not own mission/task state. Hermes keeps the durable board in
SQLite, so this module only exposes a small JSON bridge for the face UI.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from core.hermes_runtime import (
    NO_WINDOW, get_hermes_repo, hermes_python_prefix, hermes_subprocess_env, hermes_timeout,
)
from core.runtime_descriptor import get_runtime


class HermesKanbanError(RuntimeError):
    """Raised when the Hermes kanban bridge cannot complete a request."""


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


def _run_bridge(payload: dict[str, Any], timeout: int = 20) -> dict[str, Any]:
    repo = _hermes_repo()
    if not repo.exists():
        raise HermesKanbanError(f"Hermes repo not found at {repo}")

    script = r"""
import json, sys, time
sys.path.insert(0, __HERMES_REPO__)
from hermes_cli import kanban_db as kb
# Closes the connection on exit (sqlite3's own ``with`` only commits). It is the
# entry point in Hermes 0.21.4 and 0.21.6; 0.21.6 no longer exports kb.connect.
from hermes_cli.kanban_db_connect import connect_closing

payload = json.loads(sys.stdin.read() or "{}")
action = payload.get("action") or "board"
board = payload.get("board") or None

def attr(obj, name, default=None):
    return getattr(obj, name, default)

def task_to_dict(conn, t):
    try:
        parents = kb.parent_ids(conn, t.id)
    except Exception:
        parents = []
    try:
        children = kb.child_ids(conn, t.id)
    except Exception:
        children = []
    return {
        "id": t.id,
        "title": t.title,
        "body": t.body,
        "assignee": t.assignee,
        "status": t.status,
        "priority": t.priority,
        "tenant": attr(t, "tenant"),
        "workspace_kind": attr(t, "workspace_kind"),
        "workspace_path": attr(t, "workspace_path"),
        "created_by": attr(t, "created_by"),
        "created_at": attr(t, "created_at"),
        "updated_at": attr(t, "updated_at"),
        "started_at": attr(t, "started_at"),
        "completed_at": attr(t, "completed_at"),
        "archived_at": attr(t, "archived_at"),
        "result": attr(t, "result"),
        "skills": list(attr(t, "skills", []) or []),
        "max_retries": attr(t, "max_retries"),
        "session_id": attr(t, "session_id"),
        "current_run_id": attr(t, "current_run_id"),
        "last_heartbeat_at": attr(t, "last_heartbeat_at"),
        "last_failure_error": attr(t, "last_failure_error"),
        "consecutive_failures": attr(t, "consecutive_failures"),
        "current_step_key": attr(t, "current_step_key"),
        "workflow_template_id": attr(t, "workflow_template_id"),
        "model_override": attr(t, "model_override"),
        "claim_lock": attr(t, "claim_lock"),
        "claim_expires_at": attr(t, "claim_expires_at"),
        "branch_name": attr(t, "branch_name"),
        "parents": parents,
        "children": children,
        "parent_count": len(parents),
        "child_count": len(children),
    }

def comment_to_dict(c):
    return {
        "id": c.id,
        "task_id": c.task_id,
        "author": c.author,
        "body": c.body,
        "created_at": c.created_at,
    }

def event_to_dict(e):
    return {
        "id": e.id,
        "task_id": e.task_id,
        "kind": e.kind,
        "payload": e.payload,
        "created_at": e.created_at,
        "run_id": e.run_id,
    }

def run_to_dict(r):
    return {
        "id": r.id,
        "task_id": r.task_id,
        "profile": r.profile,
        "step_key": r.step_key,
        "status": r.status,
        "worker_pid": r.worker_pid,
        "max_runtime_seconds": r.max_runtime_seconds,
        "last_heartbeat_at": r.last_heartbeat_at,
        "started_at": r.started_at,
        "ended_at": r.ended_at,
        "outcome": r.outcome,
        "summary": r.summary,
        "metadata": r.metadata,
        "error": r.error,
    }

def best_effort_count(fn, *args):
    try:
        return len(fn(*args))
    except Exception:
        return 0

def worker_to_dict(w):
    if isinstance(w, dict):
        return w
    return {
        "id": attr(w, "id") or attr(w, "worker_id") or attr(w, "name") or "worker",
        "name": attr(w, "name") or attr(w, "profile") or attr(w, "id") or "worker",
        "status": attr(w, "status") or "active",
        "task_id": attr(w, "task_id") or attr(w, "current_task_id"),
        "last_heartbeat_at": attr(w, "last_heartbeat_at") or attr(w, "heartbeat_at"),
    }

if action == "create":
    title = str(payload.get("title") or "").strip()
    if not title:
        raise ValueError("title is required")
    with connect_closing(board=board) as conn:
        task_id = kb.create_task(
            conn,
            title=title,
            body=str(payload.get("body") or "").strip() or None,
            assignee=str(payload.get("assignee") or "").strip() or None,
            created_by="IRIS",
            tenant=str(payload.get("tenant") or "").strip() or None,
            priority=int(payload.get("priority") or 0),
            skills=payload.get("skills") or None,
            board=board,
        )
    print(json.dumps({"success": True, "task_id": task_id}, ensure_ascii=False))
    raise SystemExit(0)

if action == "workers":
    workers = []
    with connect_closing(board=board) as conn:
        for name in ("list_active_workers", "active_workers", "list_workers"):
            fn = getattr(kb, name, None)
            if not fn:
                continue
            try:
                raw = fn(conn)
            except TypeError:
                try:
                    raw = fn()
                except Exception:
                    raw = []
            except Exception:
                raw = []
            workers = [worker_to_dict(w) for w in (raw or [])]
            if workers:
                break
        if not workers:
            try:
                tasks = kb.list_tasks(conn, status="running", include_archived=False)
            except Exception:
                tasks = []
            workers = [{
                "id": attr(t, "current_run_id") or attr(t, "assignee") or t.id,
                "name": attr(t, "assignee") or "Hermes worker",
                "status": attr(t, "status") or "running",
                "task_id": t.id,
                "last_heartbeat_at": attr(t, "last_heartbeat_at"),
            } for t in tasks]
    print(json.dumps({
        "success": True,
        "board": board or kb.get_current_board(),
        "workers": workers,
        "count": len(workers),
        "generated_at_ms": int(time.time() * 1000),
    }, ensure_ascii=False))
    raise SystemExit(0)

if action == "detail":
    task_id = str(payload.get("task_id") or "").strip()
    if not task_id:
        raise ValueError("task_id is required")
    with connect_closing(board=board) as conn:
        task = kb.get_task(conn, task_id)
        if task is None:
            raise ValueError(f"unknown task {task_id}")
        runs = [run_to_dict(r) for r in kb.list_runs(conn, task_id)]
        comments = [comment_to_dict(c) for c in kb.list_comments(conn, task_id)]
        events = [event_to_dict(e) for e in kb.list_events(conn, task_id)]
        active_run = next((r for r in reversed(runs) if str(r.get("status", "")).lower() in ("running", "active", "started")), None)
        task_dict = task_to_dict(conn, task)
        artifacts = []
        for run in runs:
            meta = run.get("metadata")
            if isinstance(meta, dict):
                raw_artifacts = meta.get("artifacts") or meta.get("outputs") or []
                if isinstance(raw_artifacts, list):
                    artifacts.extend(raw_artifacts)
        detail = {
            "success": True,
            "board": board or kb.get_current_board(),
            "task": task_dict,
            "comments": comments,
            "events": events,
            "runs": runs,
            "run_count": len(runs),
            "comment_count": len(comments),
            "event_count": len(events),
            "active_run": active_run,
            "progress": {
                "children_done": 0,
                "children_total": task_dict.get("child_count", 0),
                "current_step": task_dict.get("current_step_key"),
            },
            "artifacts": artifacts[:12],
            "latest_summary": kb.latest_summary(conn, task_id),
        }
    print(json.dumps(detail, ensure_ascii=False))
    raise SystemExit(0)

include_archived = bool(payload.get("include_archived", False))
try:
    boards = kb.list_boards(include_archived=False)
except Exception:
    boards = []
current = board or kb.get_current_board()

with connect_closing(board=board) as conn:
    try:
        kb.recompute_ready(conn)
    except Exception:
        pass
    tasks = kb.list_tasks(
        conn,
        assignee=payload.get("assignee") or None,
        status=payload.get("status") or None,
        tenant=payload.get("tenant") or None,
        include_archived=include_archived,
        order_by=payload.get("sort") or "priority",
    )
    rows = []
    for t in tasks:
        row = task_to_dict(conn, t)
        row["run_count"] = best_effort_count(kb.list_runs, conn, t.id)
        row["comment_count"] = best_effort_count(kb.list_comments, conn, t.id)
        row["event_count"] = best_effort_count(kb.list_events, conn, t.id)
        rows.append(row)

counts = {}
for t in rows:
    counts[t["status"]] = counts.get(t["status"], 0) + 1

print(json.dumps({
    "success": True,
    "board": current,
    "boards": boards,
    "counts": counts,
    "tasks": rows,
    "generated_at_ms": int(time.time() * 1000),
}, ensure_ascii=False))
""".replace("__HERMES_REPO__", repr(str(repo)))

    runtime = get_runtime()
    prefix = hermes_python_prefix(None if runtime.hermes else _hermes_python(repo))
    if not prefix:
        raise HermesKanbanError("Hermes Python was not found")

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
            timeout=hermes_timeout(timeout),
            check=False,
            creationflags=NO_WINDOW,
        )
    except subprocess.TimeoutExpired as exc:
        raise HermesKanbanError("Hermes kanban request timed out") from exc

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise HermesKanbanError(detail or f"Hermes kanban exited with {proc.returncode}")

    stdout = (proc.stdout or "").strip()
    if not stdout:
        return {"success": False, "error": "Hermes kanban returned an empty response"}

    try:
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise HermesKanbanError(f"Could not parse Hermes kanban response: {stdout[:500]}") from exc


def read_board(
    *,
    board: str | None = None,
    status: str | None = None,
    assignee: str | None = None,
    tenant: str | None = None,
    include_archived: bool = False,
) -> dict[str, Any]:
    return _run_bridge({
        "action": "board",
        "board": board,
        "status": status,
        "assignee": assignee,
        "tenant": tenant,
        "include_archived": include_archived,
    })


def create_task(payload: dict[str, Any]) -> dict[str, Any]:
    clean = {
        "action": "create",
        "board": payload.get("board"),
        "title": payload.get("title"),
        "body": payload.get("body"),
        "assignee": payload.get("assignee"),
        "tenant": payload.get("tenant"),
        "priority": payload.get("priority"),
        "skills": payload.get("skills"),
    }
    return _run_bridge({k: v for k, v in clean.items() if v not in ("", None)})


def task_detail(task_id: str, *, board: str | None = None) -> dict[str, Any]:
    return _run_bridge({
        "action": "detail",
        "board": board,
        "task_id": task_id,
    })


def active_workers(*, board: str | None = None) -> dict[str, Any]:
    return _run_bridge({
        "action": "workers",
        "board": board,
    })

