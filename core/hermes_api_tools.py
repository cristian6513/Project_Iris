"""Repair only Iris' API tool selection, using the installed Hermes resolver.

Raw toolset names are not proof: ``all``, ``*`` and plugin composites can bring
back an excluded tool. Cache only the worker's verified file hashes, by home
and runtime; a config/env/managed-policy edit requires another verification.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import threading
from pathlib import Path

from core.hermes_runtime import (
    API_SERVER_TOOLS_OFF,
    NO_WINDOW,
    get_hermes_config_path,
    get_hermes_repo,
    hermes_python_prefix,
    hermes_subprocess_env,
    hermes_timeout,
)

_lock = threading.RLock()
_verified: dict[tuple[str, str], tuple[str, dict[str, str | None]]] = {}


def _hash_file(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


def _profile_home(profile: str | None) -> Path:
    from core.hermes_continuity import valid_profile_name

    home = get_hermes_config_path().parent.resolve()
    name = str(profile or "").strip().lower()
    if not name or name == "default":
        return home
    if not valid_profile_name(name):
        raise ValueError("Invalid Hermes profile")
    target = (home / "profiles" / name).resolve(strict=True)
    if not target.is_relative_to(home / "profiles") or not target.is_dir():
        raise ValueError("Hermes profile is outside its home")
    return target


def ensure_api_tools(profile: str | None = None) -> bool:
    """False means no verified policy: the caller must not start a chat turn."""
    try:
        home, repo = _profile_home(profile), get_hermes_repo().resolve()
        prefix = hermes_python_prefix()
        if not prefix:
            return False
        env = hermes_subprocess_env()
        env["HERMES_HOME"] = str(home)
        # Never log this hash's input: an environment can contain credentials.
        environment = hashlib.sha256(json.dumps(sorted(env.items())).encode()).hexdigest()
        key = (str(home), str(repo))
        with _lock:
            cached = _verified.get(key)
            if (
                cached
                and cached[0] == environment
                and all(_hash_file(Path(path)) == digest for path, digest in cached[1].items())
            ):
                return True
            worker = Path(__file__).with_name("hermes_api_tools_worker.py")
            result = subprocess.run(
                [
                    *prefix,
                    "-B",
                    str(worker),
                    "--hermes-root",
                    str(repo),
                    "--blocked",
                    *API_SERVER_TOOLS_OFF,
                ],
                cwd=str(repo),
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=hermes_timeout(60),
                creationflags=NO_WINDOW,
                check=False,
            )
            lines = [
                line.removeprefix("IRIS_API_TOOLS=")
                for line in result.stdout.splitlines()
                if line.startswith("IRIS_API_TOOLS=")
            ]
            payload = json.loads(lines[-1]) if lines else {}
            hashes = payload.get("files")
            if result.returncode or payload.get("ok") is not True or not isinstance(hashes, dict):
                return False
            # A write after verification is not accepted as the worker's result.
            if str(home / "config.yaml") not in hashes or any(
                _hash_file(Path(path)) != digest for path, digest in hashes.items()
            ):
                return False
            _verified[key] = (environment, hashes)
            return True
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        return False
