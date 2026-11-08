#!/usr/bin/env python3
"""Ask the local IRIS Core for an IRIS OS app action, or list the ones it accepts.

    python app_action.py --catalog
    python app_action.py --payload-file <request.json>

Like control_player.py, it reads the request from a JSON file (never user text in a shell
command), talks only to loopback and prints only what IRIS OS validated and answered. Core
decides whether the action exists, whether it is allowed and where it runs.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def _sibling(name: str):
    """A script next to this one, by its exact path. Iris' private Python runs isolated, so the
    script's folder is not on sys.path and an import by name would fail there."""
    spec = importlib.util.spec_from_file_location(f"iris_os_skill_{name}", Path(__file__).resolve().with_name(f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_player = _sibling("control_player")
_iris_port, _read_payload = _player._iris_port, _player._read_payload


def _call(path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    request = urllib.request.Request(
        f"http://127.0.0.1:{_iris_port()}{path}",
        data=None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="GET" if payload is None else "POST",
    )
    try:
        # Core may wait for the person to confirm on screen; stay above its bound.
        with urllib.request.urlopen(request, timeout=180) as response:
            body = json.loads(response.read(262_144).decode("utf-8"))
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
    if not isinstance(body, dict):
        raise RuntimeError("IRIS Core devolvió un contrato no compatible.")
    return body


def main() -> int:
    parser = argparse.ArgumentParser(description="Acciones de apps de IRIS OS")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--catalog", action="store_true", help="lista las acciones disponibles")
    group.add_argument("--payload-file", type=Path, help="archivo JSON con iris.app-intent/v1")
    args = parser.parse_args()
    try:
        if args.catalog:
            body = _call("/api/app-actions/catalog")
            if body.get("schema") != "iris.app-actions/v1":
                raise RuntimeError("IRIS Core devolvió un catálogo no compatible.")
        else:
            body = _call("/api/app-actions", _read_payload(args.payload_file.expanduser()))
            if body.get("schema") != "iris.app-result/v1":
                raise RuntimeError("IRIS Core devolvió un contrato no compatible.")
    except (RuntimeError, ValueError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(body, ensure_ascii=False))
    if args.catalog:
        return 0
    return 0 if body.get("status") in {"ok", "needs_choice"} else 2


if __name__ == "__main__":
    sys.exit(main())
