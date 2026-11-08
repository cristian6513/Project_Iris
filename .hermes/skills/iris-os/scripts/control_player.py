#!/usr/bin/env python3
"""Send one bounded IRIS Player intent to the local IRIS Core.

The helper deliberately accepts a JSON file instead of interpolating user text
into a shell command. It talks only to loopback and prints only the validated
JSON response returned by IRIS OS.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def _iris_port() -> int:
    environment_port = os.environ.get("IRIS_WEBSOCKET_PORT", "").strip()
    if environment_port:
        try:
            value = int(environment_port)
            if 1024 <= value <= 65535:
                return value
        except ValueError:
            pass
    explicit_home = os.environ.get("IRIS_HOME", "").strip()
    home = Path(explicit_home).expanduser() if explicit_home else Path.home() / ".iris"
    try:
        document = json.loads((home / "config.json").read_text(encoding="utf-8"))
        value = document.get("websocket_port") if isinstance(document, dict) else None
        if isinstance(value, int) and not isinstance(value, bool) and 1024 <= value <= 65535:
            return value
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
        pass
    return 8765


def _read_payload(path: Path) -> dict[str, Any]:
    try:
        if not path.is_file() or path.stat().st_size > 8192:
            raise ValueError("La solicitud debe ser un archivo JSON de hasta 8 KB.")
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("No pude leer una solicitud JSON válida.") from exc
    if not isinstance(document, dict):
        raise ValueError("La solicitud JSON debe ser un objeto.")
    # Core owns the transport correlation ID. Older draft documentation
    # showed request_id in the skill payload, so tolerate and remove only that
    # one legacy field while keeping Core's extra-field validation strict.
    document.pop("request_id", None)
    return document


def _send(payload: dict[str, Any]) -> dict[str, Any]:
    encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"http://127.0.0.1:{_iris_port()}/api/player/actions",
        data=encoded,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        # Core may wait up to 20 seconds for Chromium/WebView to confirm real
        # playback. Keep the transport timeout comfortably above that bound.
        with urllib.request.urlopen(request, timeout=24) as response:
            result = json.loads(response.read(131_072).decode("utf-8"))
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
    if not isinstance(result, dict) or result.get("schema") != "iris.app-result/v1":
        raise RuntimeError("IRIS Core devolvió un contrato no compatible.")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Control local y seguro de IRIS Player")
    parser.add_argument("--payload-file", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = _send(_read_payload(args.payload_file.expanduser()))
    except (ValueError, RuntimeError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("status") in {"ok", "needs_choice"} else 2


if __name__ == "__main__":
    sys.exit(main())
