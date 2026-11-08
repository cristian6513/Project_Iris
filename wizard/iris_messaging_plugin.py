"""Portable Hermes extension installed by Iris; no Iris imports in Hermes' venv."""

from __future__ import annotations

import functools
import json
import re
from pathlib import Path

POLICY_FILE = "iris-policy.json"
FALLBACK = "No pude preparar el texto de esta respuesta. Pídeme que lo intente de nuevo."


def visible_message(text):
    """Unwrap only a leading, complete Iris envelope; preserve ordinary JSON/media."""
    if not isinstance(text, str):
        return text
    original = text
    candidate = text.lstrip()
    fenced = re.match(r"^```(?:json)?\s*\n", candidate, re.I)
    if fenced:
        candidate = candidate[fenced.end() :]
    if not candidate.startswith("{"):
        return original
    try:
        obj, end = json.JSONDecoder().raw_decode(candidate)
    except (ValueError, RecursionError):
        # A truncated avatar envelope is not suitable for a human chat. Other
        # code/JSON, including a quoted example after prose, stays untouched.
        if all(
            '"' + key + '"' in candidate
            for key in ("texto_hablado", "emocion", "animacion_virtual")
        ):
            return FALLBACK
        return original
    if not isinstance(obj, dict) or not all(
        key in obj for key in ("texto_hablado", "emocion", "animacion_virtual")
    ):
        return original
    if not isinstance(obj["texto_hablado"], str):
        return FALLBACK
    tail = candidate[end:].lstrip()
    if fenced and tail.startswith("```"):
        tail = tail[3:].lstrip()
    spoken = obj["texto_hablado"].strip()
    return "\n".join(part for part in (spoken, tail) if part) or FALLBACK


def _channels():
    try:
        return set(json.loads(Path(__file__).with_name(POLICY_FILE).read_text("utf-8"))["channels"])
    except (OSError, ValueError, KeyError, TypeError):
        return set()


def _human_platform(platform):
    value = str(getattr(platform, "value", platform) or "")
    return value in _channels() or value == "cron"


def transform_reply(response_text, platform="", **_kwargs):
    if not _human_platform(platform):
        return None  # api_server keeps the avatar contract, including its metadata.
    result = visible_message(response_text)
    return result if result != response_text else None


def channel_context(platform="", **_kwargs):
    if _human_platform(platform):
        return (
            "Esta respuesta se presenta como un mensaje para una persona. Mantén tu "
            "personalidad y responde con texto normal y el formato que pida el usuario. "
            "No envuelvas el mensaje en el contrato del avatar de Iris ni añadas campos "
            "de emoción o animación. Los adjuntos y las herramientas siguen siendo de Hermes."
        )
    return None


def register(ctx):
    ctx.register_hook("transform_llm_output", transform_reply)
    ctx.register_hook("pre_llm_call", channel_context)

    # Hermes' standalone deliveries (cron scripts, CLI/MCP sends) don't pass
    # through the LLM finalizer. Wrap the shared dispatcher, not vendor SDKs.
    # Its public function resolves _handle_send at call time, even when imported
    # before this plugin. Keep this wrapper idempotent on plugin force-reload.
    from tools import send_message_tool as sender

    previous = sender._handle_send
    original = getattr(previous, "_iris_original", previous)

    @functools.wraps(original)
    def send(args):
        target = str(args.get("target") or "").partition(":")[0].strip().lower()
        if target in _channels():
            args = {**args, "message": visible_message(args.get("message", ""))}
        return original(args)

    send._iris_original = original
    sender._handle_send = send

    # A fresh CLI can discover plugins from inside its first _handle_send call.
    # That frame is already executing the old dispatcher; its later lookup of
    # _send_to_platform must still clean the first delivery.
    previous_delivery = sender._send_to_platform
    original_delivery = getattr(previous_delivery, "_iris_original", previous_delivery)

    @functools.wraps(original_delivery)
    async def deliver(platform, pconfig, chat_id, message, *args, **kwargs):
        if _human_platform(platform):
            message = visible_message(message)
            if isinstance(kwargs.get("args"), dict):
                kwargs["args"] = {**kwargs["args"], "message": message}
        return await original_delivery(platform, pconfig, chat_id, message, *args, **kwargs)

    deliver._iris_original = original_delivery
    sender._send_to_platform = deliver
