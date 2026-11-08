#!/usr/bin/env python3
"""Leave one long task working in the background while Iris keeps talking.

    python background_task.py --turn <código> --payload-file <tarea.json>

``tarea.json``: ``{"title": "revisar noticias", "task": "Busca las noticias más importantes…"}``.
Only valid with the one-time code IRIS OS offered in this turn's context. Core
posts the task to Hermes as its own run and answers as soon as Hermes accepted
it: this script never waits for the task to finish. When it ends, IRIS OS
gives Iris its result in a later turn. Like app_action.py, it reads the task
from a file (never user text in a shell command), talks only to loopback and
prints only what IRIS OS answered.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

_TURN = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


def _sibling(name: str):
    """A script next to this one, by its exact path (Iris' private Python runs isolated)."""
    path = Path(__file__).resolve().with_name(f"{name}.py")
    spec = importlib.util.spec_from_file_location(f"iris_os_skill_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_player = _sibling("control_player")
_iris_port, _read_payload = _player._iris_port, _player._read_payload


def _start(turn: str, title: str, task: str) -> dict[str, Any]:
    payload = {"schema": "iris.background-task/v1", "turn": turn, "title": title, "task": task}
    request = urllib.request.Request(
        f"http://127.0.0.1:{_iris_port()}/api/background-tasks",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        # Core answers once Hermes accepted the run (two attempts at most).
        with urllib.request.urlopen(request, timeout=60) as response:
            body = json.loads(response.read(65_536).decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read(16_384).decode("utf-8")).get("detail")
        except Exception:
            detail = None
        raise RuntimeError(str(detail or f"IRIS OS rechazó la solicitud ({exc.code}).")) from exc
    except (OSError, TimeoutError, urllib.error.URLError) as exc:
        raise RuntimeError("IRIS Core no está disponible en este equipo.") from exc
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("IRIS Core devolvió una respuesta no válida.") from exc
    if not isinstance(body, dict) or body.get("schema") != "iris.background-result/v1":
        raise RuntimeError("IRIS Core devolvió un contrato no compatible.")
    return body


def main() -> int:
    parser = argparse.ArgumentParser(description="Tarea en segundo plano de IRIS OS")
    parser.add_argument("--turn", required=True, help="el código de turno que IRIS OS ofreció")
    parser.add_argument("--payload-file", type=Path, required=True, help="JSON con title y task")
    args = parser.parse_args()
    try:
        if not _TURN.fullmatch(args.turn):
            raise RuntimeError(
                "El código de turno no es válido. No inicies otra tarea en este turno."
            )
        document = _read_payload(args.payload_file.expanduser())
        title, task = document.get("title"), document.get("task")
        if (
            not isinstance(title, str)
            or not isinstance(task, str)
            or set(document) - {"title", "task"}
        ):
            raise RuntimeError("El archivo debe tener solo «title» y «task», como texto.")
        body = _start(args.turn, title, task)
    except (RuntimeError, ValueError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(body, ensure_ascii=False))
    return 0 if body.get("status") == "started" else 2


if __name__ == "__main__":
    sys.exit(main())
