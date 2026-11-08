"""F02/F03b: Iris' private adapter for Hermes' auxiliary vision model.

Executed by Hermes' own Python, never Iris', with ``HERMES_HOME`` set to the
profile Iris captured for the request (``core.vision_model`` starts it through
``runpy`` so no Iris folder lands on ``sys.path``). It reads one JSON request
on stdin and prints one ``IRIS_VISION=<json>`` line. Hermes owns credentials,
provider resolution and request shaping; nothing secret crosses stdout.

Commands:

* ``status``: the configured ``auxiliary.vision`` block and the route Hermes'
  resolver plans for it (no request is sent: probe mode); with ``models``,
  also their capabilities.
* ``capabilities``: whether each ``(provider, model)`` can read images,
  according to Hermes' own lookup (config override, local runtime, catalog).
* ``write``: change ``auxiliary.vision`` with Hermes' config writer.
* ``analyze``: describe the images (up to 8) with the vision model. Hermes'
  provider fallback is switched off for this process: an image is sent to one
  provider only, and the route that answered is reported.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys

MARKER = "IRIS_VISION="
_MAX_PAIRS = 400


def emit(payload: dict) -> None:
    sys.stdout.write(MARKER + json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _clean(value) -> str:
    return str(value or "").strip()


def _mapping(value) -> dict:
    return value if isinstance(value, dict) else {}


def configured(raw: dict) -> dict:
    """The user's ``auxiliary.vision`` and ``agent.image_input_mode`` exactly as written."""
    vision = _mapping(_mapping(raw.get("auxiliary")).get("vision"))
    mode = _clean(_mapping(raw.get("agent")).get("image_input_mode")).lower()
    return {
        "provider": _clean(vision.get("provider")) or "auto",
        "model": _clean(vision.get("model")),
        "custom_endpoint": bool(_clean(vision.get("base_url"))),
        "own_key": bool(_clean(vision.get("api_key"))),
        "image_input_mode": mode if mode in {"auto", "native", "text"} else "auto",
    }


def planned_route() -> dict:
    """What Hermes' resolver picks for the vision task right now, without calling it."""
    from agent.auxiliary_client import aux_probe_mode, resolve_vision_provider_client

    with aux_probe_mode():
        provider, client, model = resolve_vision_provider_client()
    return {"provider": _clean(provider), "model": _clean(model), "available": client is not None}


def status(request: dict) -> dict:
    from hermes_cli.config import read_raw_config

    result = {"ok": True, "configured": configured(read_raw_config()), "planned": planned_route()}
    if request.get("models"):
        result["models"] = capabilities(request)["models"]  # one process for the whole settings view
    return result


def capabilities(request: dict) -> dict:
    from agent.image_routing import _lookup_supports_vision
    from hermes_cli.config import load_config

    cfg = load_config()
    rows = []
    for pair in list(request.get("models") or [])[:_MAX_PAIRS]:
        provider, model = _clean(_mapping(pair).get("provider")), _clean(_mapping(pair).get("model"))
        if not provider or not model:
            continue
        try:
            verdict = _lookup_supports_vision(provider, model, cfg)
        except Exception:
            verdict = None
        rows.append({"provider": provider, "model": model, "vision": verdict})
    return {"ok": True, "models": rows}


def write(request: dict) -> dict:
    from hermes_cli.config import read_raw_config, save_config

    raw = read_raw_config()
    if configured(raw)["custom_endpoint"]:
        # base_url beats the provider in Hermes' resolver: an Iris choice would
        # not take effect, and that endpoint's settings belong to whoever set it.
        return {"ok": False, "code": "custom_endpoint", "configured": configured(raw)}
    auxiliary = raw.get("auxiliary") if isinstance(raw.get("auxiliary"), dict) else {}
    vision = auxiliary.get("vision") if isinstance(auxiliary.get("vision"), dict) else {}
    if request.get("mode") == "auto":
        vision["provider"], vision["model"] = "auto", ""
    else:
        provider, model = _clean(request.get("provider")), _clean(request.get("model"))
        if not provider or not model or provider.lower() == "auto":
            return {"ok": False, "code": "invalid_selection"}
        vision["provider"], vision["model"] = provider, model
    auxiliary["vision"] = vision
    raw["auxiliary"] = auxiliary
    save_config(raw)
    return {"ok": True, "configured": configured(read_raw_config()), "planned": planned_route()}


def _freeze_config() -> None:
    """Every resolution in this process uses the configuration read once, now.

    The route is checked against the destination the user saw, then the image
    is prepared and sent: in between, Hermes reads its config again. A change
    written meanwhile (another provider, the same provider at another
    endpoint) would have received the image (Codex F02/F03 review, R02).
    """
    import copy

    import hermes_cli.config as config

    loaded, raw = config.load_config(), config.read_raw_config()
    replacements = {}
    for name, value in (("load_config", loaded), ("load_config_readonly", loaded),
                        ("read_raw_config", raw), ("read_raw_config_readonly", raw)):
        original = getattr(config, name, None)
        if original is None:
            continue
        frozen = (lambda value=value: copy.deepcopy(value))
        replacements[original] = frozen
        setattr(config, name, frozen)
    # Modules that bound the functions by name at import time.
    for module in list(sys.modules.values()):
        namespace = getattr(module, "__dict__", None)
        if not isinstance(namespace, dict) or module is config:
            continue
        for attr, value in list(namespace.items()):
            try:
                frozen = replacements.get(value)
            except TypeError:
                continue
            if frozen is not None:
                namespace[attr] = frozen


def _mark_temporary_files(token: str) -> None:
    """Hermes names its temporary image copies after ``uuid4()``: start them with
    ``token`` so Iris can delete this request's copies if it has to end the
    process before Hermes' own cleanup runs (Codex F02/F03 review, R08)."""
    import uuid

    if not re.fullmatch(r"[0-9a-f]{12}", token or ""):
        return
    real = uuid.uuid4

    def marked():
        return uuid.UUID(hex=token + real().hex[12:])  # keeps uuid4's version and variant bits

    uuid.uuid4 = marked


def _route_differs(planned: dict, answered: dict) -> bool:
    """Another provider, or another model when both are known (Hermes reports "default" for unnamed)."""
    if planned["provider"] != answered["provider"]:
        return True
    known = {planned["model"], answered["model"]} - {"", "default"}
    return len(known) > 1


def _without_provider_fallback() -> None:
    """A photo goes to the route the user can see, never to another provider."""
    import agent.auxiliary_client as auxiliary

    def no_provider_fallback(first_err, route):
        return None
        yield  # a generator, like the rung it replaces

    auxiliary._ladder_provider_fallback = no_provider_fallback


async def _analyze(request: dict) -> dict:
    import tools.vision_tools as vision_tools

    _mark_temporary_files(_clean(request.get("temp_token")))
    _freeze_config()  # before the route is checked: the check and the send see the same config
    planned = planned_route()
    if not planned["available"]:
        return {"ok": False, "code": "vision_unavailable", "planned": planned}
    expected = request.get("expected") if isinstance(request.get("expected"), dict) else None
    if expected and (
        _clean(expected.get("provider")) != planned["provider"] or _clean(expected.get("model")) != planned["model"]
    ):
        # The user authorised one destination; the profile now resolves another.
        return {"ok": False, "code": "route_changed", "planned": planned}
    _without_provider_fallback()
    vision_tools._load_auxiliary_client()
    real_call = vision_tools.async_call_llm
    route: dict = {}

    async def call_with_route(**kwargs):
        return await real_call(**kwargs, route_info=route)

    vision_tools.async_call_llm = call_with_route
    analyses = []
    for image in list(request.get("images") or [])[:8]:
        route.clear()
        raw = await vision_tools.vision_analyze_tool(image_url=str(image or ""), user_prompt=str(request.get("prompt") or ""))
        result = json.loads(raw) if isinstance(raw, str) else {}
        answered = {"provider": _clean(route.get("provider")) or planned["provider"],
                    "model": _clean(route.get("model")) or planned["model"]}
        if _route_differs(planned, answered):
            # Should not happen with the config frozen; never send the next image if it does.
            return {"ok": False, "code": "route_changed", "planned": planned, "route": answered}
        if not result.get("success"):
            return {"ok": False, "code": "analysis_failed", "planned": planned, "route": answered}
        analyses.append(_clean(result.get("analysis")))
    if not analyses:
        return {"ok": False, "code": "invalid_request"}
    return {"ok": True, "analyses": analyses, "planned": planned, "route": answered}


def analyze(request: dict) -> dict:
    return asyncio.run(_analyze(request))


COMMANDS = {"status": status, "capabilities": capabilities, "write": write, "analyze": analyze}


def main() -> int:
    try:
        request = json.loads(sys.stdin.read() or "{}")
        command = COMMANDS[str(request.get("command") or "")]
    except (ValueError, KeyError, TypeError):
        emit({"ok": False, "code": "invalid_request"})
        return 2
    try:
        emit(command(request))
    except Exception as exc:  # classified only: upstream errors can carry credentials
        emit({"ok": False, "code": "unsupported_runtime" if isinstance(exc, (ImportError, AttributeError)) else "error"})
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
