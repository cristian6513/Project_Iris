#!/usr/bin/env python3
"""Ask the local IRIS Core for one camera photo or screen capture in this turn.

    python visual_request.py --source camera --turn <código>
    python visual_request.py --source screen --turn <código>

Only valid with the one-time code IRIS OS offered in this turn's context. Core
checks the code, the permissions and the person's answer, takes the image,
has the profile's vision model describe it and returns that description. The
image itself never reaches this script. Like app_action.py, it talks only to
loopback and prints only what IRIS OS answered.
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


_iris_port = _sibling("control_player")._iris_port


def _ask(source: str, turn: str) -> dict[str, Any]:
    payload = {"schema": "iris.visual-request/v1", "turn": turn, "source": source}
    request = urllib.request.Request(
        f"http://127.0.0.1:{_iris_port()}/api/visual-requests",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        # The person may take a while to choose a window; the analysis follows.
        with urllib.request.urlopen(request, timeout=400) as response:
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
    if not isinstance(body, dict) or body.get("schema") != "iris.visual-result/v1":
        raise RuntimeError("IRIS Core devolvió un contrato no compatible.")
    return body


def main() -> int:
    parser = argparse.ArgumentParser(description="Pedir una imagen a IRIS OS en este turno")
    parser.add_argument("--source", choices=("camera", "screen"), required=True)
    parser.add_argument("--turn", required=True, help="el código de turno que IRIS OS ofreció")
    args = parser.parse_args()
    try:
        if not _TURN.fullmatch(args.turn):
            raise RuntimeError(
                "El código de turno no es válido. No pidas otra imagen en este turno.")
        body = _ask(args.source, args.turn)
    except RuntimeError as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(body, ensure_ascii=False))
    return 0 if body.get("status") == "ok" else 2


if __name__ == "__main__":
    sys.exit(main())
