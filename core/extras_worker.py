"""Isolated process for one approved profile extra (A08.5).

Core launches this file by path with the Iris interpreter:

    python -I -S -X utf8 -X pycache_prefix=<extra>/pycache extras_worker.py --site <env>/site ...

``-I -S`` keep the base site-packages, user site and ``PYTHON*`` variables out
of ``sys.path``; the only third-party directory is the extra's own site. The
worker imports nothing from ``core`` and loads ``tts_engines.py`` from its own
directory by file path. Protocol replies are single lines on the original
stdout prefixed by ``MARKER``; library output is redirected to stderr at the
file-descriptor level and stdin is replaced by the null device so an engine
can never read or corrupt the request channel.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import importlib
import importlib.util
import json
import logging
import os
import site
import sys
from pathlib import Path
from typing import Any, BinaryIO

PROTOCOL = 1
MARKER = b"\x1eIRIS-EXTRA\x1f"
MAX_REQUEST_BYTES = 64 * 1024
MAX_TEXT_CHARS = 5000
ENGINES = {
    "kokoro": ("KokoroTTSProvider", {"voice", "lang"}),
    "coqui": ("CoquiTTSProvider", {"model_name", "reference_audio", "language"}),
    "neutts": ("NeuTTSProvider", {"model_repo", "codec_repo", "ref_audio", "ref_text", "device"}),
    "hf-s2s": (
        "HFSpeechToSpeechQwenProvider",
        {"model_name", "speaker", "language", "reference_audio", "reference_text", "device"},
    ),
}
# Each provider swallows load errors and leaves its engine handle unset.
_READY_ATTRIBUTES = {
    "kokoro": ("_pipeline",),
    "coqui": ("_tts",),
    "neutts": ("_tts", "_ref_codes"),
    "hf-s2s": ("_handler", "_input_type"),
}


class _LastError(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.ERROR)
        self.message = ""

    def emit(self, record: logging.LogRecord) -> None:
        self.message = record.getMessage()[:500]


def _base_site_entry(entry: str) -> bool:
    parts = {part.lower() for part in Path(entry).parts}
    return bool(parts & {"site-packages", "dist-packages"})


def _isolate(site_dir: Path) -> None:
    """Leave only the standard library and the extra's own site on ``sys.path``.

    ``-I -S`` is not enough for the packaged interpreter: its ``python311._pth``
    lists ``Lib/site-packages`` and imports ``site`` regardless of ``-S``. The
    base entries are removed before any third-party import; if one was already
    loaded from them, the worker refuses to start instead of mixing runtimes.
    """
    if not sys.flags.isolated:
        raise SystemExit("extras worker requires -I")
    removed = [entry for entry in sys.path if entry and _base_site_entry(entry)]
    sys.path[:] = [entry for entry in sys.path if entry and not _base_site_entry(entry)]
    for entry in removed:
        sys.path_importer_cache.pop(entry, None)
    roots = tuple(os.path.normcase(os.path.abspath(entry)) + os.sep for entry in removed)
    # Packaged Iris installs this stdlib-only script inside base site-packages;
    # the script itself is the one module allowed to come from there.
    own = os.path.normcase(os.path.abspath(__file__))
    leaked = []
    for name, module in list(sys.modules.items()):
        origin = getattr(module, "__file__", None)
        if not roots or not origin:
            continue
        origin = os.path.normcase(os.path.abspath(origin))
        if origin != own and origin.startswith(roots):
            leaked.append(name)
    if leaked:
        raise SystemExit(f"extras worker inherited base modules: {', '.join(sorted(leaked)[:5])}")
    importlib.invalidate_caches()
    site.addsitedir(str(site_dir))


def _channels() -> tuple[BinaryIO, BinaryIO]:
    requests = os.fdopen(os.dup(0), "rb")
    replies = os.fdopen(os.dup(1), "wb")
    null = os.open(os.devnull, os.O_RDONLY)
    os.dup2(null, 0)
    os.close(null)
    os.dup2(2, 1)
    sys.stdin = open(os.devnull, encoding="utf-8")  # noqa: SIM115 - process lifetime
    sys.stdout = sys.stderr
    return requests, replies


def _reply(channel: BinaryIO, payload: dict[str, Any]) -> None:
    channel.write(MARKER + json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n")
    channel.flush()


def _load_engines() -> Any:
    path = Path(__file__).with_name("tts_engines.py")
    spec = importlib.util.spec_from_file_location("iris_extra_tts_engines", path)
    if spec is None or spec.loader is None:
        raise ImportError("tts_engines.py is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _probe(modules: list[str]) -> dict[str, Any]:
    loaded = {}
    for name in modules:
        module = importlib.import_module(name)
        origin = getattr(module, "__file__", None) or ""
        loaded[name] = origin
    return {"ok": True, "protocol": PROTOCOL, "modules": loaded,
            "python": ".".join(map(str, sys.version_info[:3]))}


class _Session:
    def __init__(self, last_error: _LastError) -> None:
        self.loop = asyncio.new_event_loop()
        self.engines: Any = None
        self.engine = ""
        self.provider: Any = None
        self.last_error = last_error

    def load(self, engine: Any, options: Any) -> dict[str, Any]:
        if self.provider is not None:
            return {"ok": False, "error": "already_loaded"}
        if not isinstance(engine, str) or engine not in ENGINES:
            return {"ok": False, "error": "unknown_engine"}
        class_name, allowed = ENGINES[engine]
        if not isinstance(options, dict) or set(options) - allowed or any(
            not isinstance(value, str) or len(value) > 4096 for value in options.values()
        ):
            return {"ok": False, "error": "invalid_options"}
        if self.engines is None:
            self.engines = _load_engines()
        if engine == "kokoro":
            # Process-wide directory change: safe here, never inside Core.
            self.engines.use_relative_espeak_data()
        provider = getattr(self.engines, class_name)(**options)
        self.loop.run_until_complete(provider.initialize())
        if any(getattr(provider, name, None) is None for name in _READY_ATTRIBUTES[engine]):
            return {"ok": False, "error": "engine_unavailable", "detail": self.last_error.message}
        self.engine, self.provider = engine, provider
        reply: dict[str, Any] = {"ok": True, "engine": engine}
        # Slow engines (Qwen3-TTS on CPU) declare a synthesis budget per character.
        budget = getattr(provider, "seconds_per_char", 0.0)
        if isinstance(budget, (int, float)) and 0 < budget <= 10:
            reply["seconds_per_char"] = float(budget)
        return reply

    def synthesize(self, text: Any) -> dict[str, Any]:
        if self.provider is None:
            return {"ok": False, "error": "not_loaded"}
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT_CHARS:
            return {"ok": False, "error": "invalid_text"}
        audio, visemes = self.loop.run_until_complete(self.provider.synthesize(text))
        if not audio:
            return {"ok": False, "error": "no_audio", "detail": self.last_error.message}
        return {"ok": True, "audio": base64.b64encode(audio).decode("ascii"), "visemes": visemes}


def _serve(requests: BinaryIO, replies: BinaryIO, last_error: _LastError) -> int:
    session = _Session(last_error)
    _reply(replies, {"ok": True, "op": "ready", "protocol": PROTOCOL})
    while True:
        raw = requests.readline(MAX_REQUEST_BYTES + 1)
        if not raw:
            return 0
        if len(raw) > MAX_REQUEST_BYTES:
            _reply(replies, {"ok": False, "error": "request_too_large"})
            return 2
        try:
            request = json.loads(raw.decode("utf-8"))
            if not isinstance(request, dict):
                raise ValueError("request must be an object")
        except (UnicodeError, ValueError):
            _reply(replies, {"ok": False, "error": "invalid_request"})
            continue
        op, request_id = request.get("op"), request.get("id")
        try:
            if op == "load":
                result = session.load(request.get("engine"), request.get("options", {}))
            elif op == "synthesize":
                result = session.synthesize(request.get("text"))
            elif op == "shutdown":
                _reply(replies, {"id": request_id, "ok": True})
                return 0
            else:
                result = {"ok": False, "error": "unknown_operation"}
        except Exception as exc:  # report engine failures; Core decides the fallback
            logging.getLogger("IRIS.extras.worker").exception("Extra request failed")
            result = {"ok": False, "error": "engine_error", "detail": f"{type(exc).__name__}: {exc}"[:500]}
        _reply(replies, {"id": request_id, **result})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--probe", help="comma-separated modules that must import")
    mode.add_argument("--serve", action="store_true")
    args = parser.parse_args(argv)
    requests, replies = _channels()
    logging.basicConfig(stream=sys.stderr, level=logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    last_error = _LastError()
    logging.getLogger().addHandler(last_error)
    site_dir = Path(args.site)
    if not site_dir.is_absolute() or not site_dir.is_dir():
        _reply(replies, {"ok": False, "error": "invalid_site"})
        return 2
    _isolate(site_dir)
    if args.probe is not None:
        try:
            _reply(replies, _probe([name for name in args.probe.split(",") if name]))
            return 0
        except Exception as exc:
            _reply(replies, {"ok": False, "error": "probe_failed",
                             "detail": f"{type(exc).__name__}: {exc}"[:500]})
            return 1
    return _serve(requests, replies, last_error)


if __name__ == "__main__":
    raise SystemExit(main())
