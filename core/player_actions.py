"""IRIS Player operations (F09.4): the first app registered on the app action bridge.

Same contract as before F09.4: seven actions, the same validation and result projection, one
local surface announcing ``controls_local_player``, the same messages and the 20-second wait
for a real playback acknowledgement. ``POST /api/player/actions`` keeps working unchanged.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

from core.app_actions import AppActionRegistry, AppOperation, OperationRejected, SurfaceRoute

PLAYER_CONFIRMATION_TIMEOUT_S = 20.0

PLAYER_ROUTE = SurfaceRoute(
    capability="controls_local_player",
    intent_message="iris_player_intent",
    result_message="iris_player_result",
    unavailable_message="Abre IRIS OS para controlar IRIS Player.",
    disconnected_message="La superficie de IRIS Player se desconectó.",
    # Chromium/WebView may spend several seconds probing a codec and buffering the first
    # frame; past the wait the song may have started, so it is never sent again.
    timeout_message=(
        "IRIS Player recibió la orden, pero no confirmó el resultado a tiempo. "
        "La reproducción pudo haber comenzado; comprueba el Player antes de reintentar."
    ),
)


def _only(args: Mapping[str, Any], allowed: set[str]) -> None:
    if set(args) - allowed:
        raise OperationRejected("La acción contiene argumentos no admitidos")


def _no_args(args: Mapping[str, Any]) -> dict[str, Any]:
    _only(args, set())
    return {}


def _play(args: Mapping[str, Any]) -> dict[str, Any]:
    _only(args, {"track_id"})
    if "track_id" not in args:
        return {}
    track_id = args.get("track_id")
    if (
        not isinstance(track_id, str)
        or len(track_id) != 36
        or not track_id.startswith("trk_")
        or any(char not in "0123456789abcdef" for char in track_id[4:])
    ):
        raise OperationRejected("El ID de pista no es válido")
    return {"track_id": track_id}


def _set_volume(args: Mapping[str, Any]) -> dict[str, Any]:
    _only(args, {"percent"})
    percent = args.get("percent")
    if isinstance(percent, bool) or not isinstance(percent, int) or not 0 <= percent <= 100:
        raise OperationRejected("El volumen debe ser un entero entre 0 y 100")
    return {"percent": percent}


def _text(field: str):
    def validate(args: Mapping[str, Any]) -> dict[str, Any]:
        _only(args, {field})
        value = args.get(field)
        if not isinstance(value, str):
            raise OperationRejected("La solicitud musical debe ser texto")
        normalized = " ".join(value.split())
        if not normalized or len(normalized) > 180 or "\x00" in normalized:
            raise OperationRejected("La solicitud musical no es válida")
        return {field: normalized}
    return validate


def project_player_data(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Keep only the public, documented result fields from the browser."""
    data: dict[str, Any] = {}
    origin = raw.get("selection_origin")
    if origin in {"manual", "query", "intent_local", "intent_hermes"}:
        data["selection_origin"] = origin
    for field in ("track_id", "title", "artist", "reason"):
        value = raw.get(field)
        if isinstance(value, str) and value.strip():
            data[field] = value.strip()[:240]
    playback_state = raw.get("playback_state")
    if playback_state in {"playing", "paused", "idle", "error"}:
        data["playback_state"] = playback_state
    candidates = raw.get("candidates")
    if isinstance(candidates, list):
        safe = []
        for candidate in candidates[:5]:
            if not isinstance(candidate, Mapping):
                continue
            track_id = candidate.get("track_id")
            title = candidate.get("title")
            if not isinstance(track_id, str) or not isinstance(title, str):
                continue
            safe.append({
                "track_id": track_id[:64],
                "title": title.strip()[:180],
                "artist": str(candidate.get("artist") or "").strip()[:180],
            })
        if safe:
            data["candidates"] = safe
    return data


def _describe_play(track_label: Callable[[str], str | None] | None):
    """The track a «play» names: its title from the library, or its public id; never a path."""
    def describe(args: Mapping[str, Any]) -> str:
        track_id = args.get("track_id")
        if not track_id:
            return ""
        try:
            label = track_label(track_id) if track_label else None
        except Exception:
            label = None
        return f"Pista: «{label}»" if label else f"Pista {track_id}"
    return describe


# What each action works on, said from its validated arguments (shown when confirming).
_DESCRIBE = {
    "set_volume": lambda args: f"Volumen al {args['percent']} %",
    "play_by_query": lambda args: f"Buscar: «{args['query']}»",
    "play_by_intent": lambda args: f"Para: «{args['intent']}»",
}

_ACTIONS: tuple[tuple[str, str, Any, dict[str, str]], ...] = (
    ("play", "reproducir o reanudar música", _play, {"track_id": "opcional; un ID trk_… devuelto por IRIS OS"}),
    ("pause", "pausar la música", _no_args, {}),
    ("next", "pasar a la siguiente canción", _no_args, {}),
    ("previous", "volver a la canción anterior", _no_args, {}),
    ("set_volume", "cambiar el volumen", _set_volume, {"percent": "entero de 0 a 100"}),
    ("play_by_query", "buscar y reproducir en tu biblioteca", _text("query"), {"query": "texto breve, hasta 180 caracteres"}),
    ("play_by_intent", "elegir música para una actividad o ambiente", _text("intent"), {"intent": "texto breve, hasta 180 caracteres"}),
)


def register_player_actions(registry: AppActionRegistry, *,
                            track_label: Callable[[str], str | None] | None = None) -> None:
    """``track_label`` names a track by its library title in the confirmation."""
    for action, label, validate, arguments in _ACTIONS:
        registry.register(AppOperation(
            app="player",
            action=action,
            label=label,
            capability="player.control",
            effect="local",
            validate_args=validate,
            project_data=project_player_data,
            arguments=arguments,
            surface=PLAYER_ROUTE,
            timeout_s=PLAYER_CONFIRMATION_TIMEOUT_S,
            denied_message="Capability Center bloqueó el control de IRIS Player.",
            describe=_describe_play(track_label) if action == "play" else _DESCRIBE.get(action),
        ))
