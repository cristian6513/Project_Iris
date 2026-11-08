"""A Hermes footer must neither expose avatar JSON nor conceal an unconfirmed edit."""

import asyncio
import json

import httpx
import pytest

from core.config import IRISConfig
from core.hermes_bridge import HermesBridge
from core.response_text import visible_response_text

REPLY = {
    "texto_hablado": "Desarrollo mi caballo a f6. ¡Te toca!",
    "emocion": "feliz",
    "animacion_virtual": "nod",
}
RAW = json.dumps(REPLY, ensure_ascii=False)
FOOTER = (
    "⚠️ File-mutation verifier: 1 file edit(s) FAILED this turn despite any wording "
    "above that may suggest otherwise. "
    "Run `git status` or `read_file` to confirm what actually landed.\n"
    "  • `C:/Users/example-user/AppData/Local/hermes/cache/scratch/chess-move.json` — [write_file] "
    "Refusing to overwrite: file was last read with a partial view."
)


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    monkeypatch.setenv("IRIS_HOME", str(tmp_path / "iris"))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    return HermesBridge(IRISConfig())


def assert_honest_failure(reply):
    text = reply["texto_hablado"]
    assert "guardar un archivo" in text and "completada" in text
    assert not any(
        raw in text
        for raw in ("texto_hablado", "File-mutation", "write_file", "C:/", "⚠", "¡Te toca!")
    )
    assert reply["emocion"] != "feliz" and reply["animacion_virtual"] != "nod"
    assert reply["_file_mutation_error"]["failed_count"] == 1


@pytest.mark.parametrize(
    "text",
    [
        RAW + "\n\n" + FOOTER,
        RAW + " " + FOOTER,
        "Respuesta:\n" + RAW + "\n\n" + FOOTER,
        "```json\n" + RAW + "\n```\n\n" + FOOTER,
        "Respuesta:\n```json\n" + RAW + "\n```\n\n" + FOOTER,
        "He realizado la jugada.\n\n" + FOOTER,
        FOOTER,
        RAW + "\n\n" + FOOTER.replace("⚠️", "⚠"),
        RAW + "\nOtro comentario.\n\n" + FOOTER,
        FOOTER + "\n  • Error preview: " + RAW,
    ],
)
def test_write_failure_is_explained_without_claiming_the_move_landed(bridge, text):
    reply = bridge._parse_iris_response(text)
    assert_honest_failure(reply)
    assert visible_response_text(text) == reply["texto_hablado"], (
        "history must not reinstate the success claim"
    )


@pytest.mark.parametrize(
    "prefix,suffix",
    [
        ("", ""),
        ("Respuesta:\n", ""),
        ("", "\nComentario final."),
        ("```json\n", "\n```\nComentario final."),
    ],
)
def test_complete_envelope_survives_prose_braces_and_nested_metadata(bridge, prefix, suffix):
    reply = {
        **REPLY,
        "texto_hablado": 'Ejemplo: {"dato": "llave } y comilla \\""}. ¡Listo! 😊',
        "extra": {"nested": {"n": 2}},
    }
    text = prefix + json.dumps(reply, ensure_ascii=False) + suffix
    assert bridge._parse_iris_response(text) == reply


def test_an_example_of_the_footer_inside_spoken_text_is_preserved(bridge):
    reply = {**REPLY, "texto_hablado": "El aviso significa esto:\n" + FOOTER}
    raw = json.dumps(reply, ensure_ascii=False)
    assert bridge._parse_iris_response(raw) == reply
    assert visible_response_text(raw) == reply["texto_hablado"]


def test_nested_stored_messages_preserve_the_failed_edit_warning(bridge):
    raw = json.dumps({"role": "assistant", "content": RAW + "\n\n" + FOOTER})
    assert (
        visible_response_text(raw)
        == bridge._parse_iris_response(RAW + "\n\n" + FOOTER)["texto_hablado"]
    )


def test_an_ordinary_json_or_markdown_example_is_not_an_avatar_action(bridge):
    for text in (
        '{"message":"JSON solicitado","n":2}',
        'Ejemplo:\n```json\n{"message":"ejemplo"}\n```\nFin.',
        "Ejemplo:\n```text\n" + FOOTER + "\n```",
    ):
        assert bridge._parse_iris_response(text)["texto_hablado"] == text


@pytest.mark.parametrize("stream", [False, True])
def test_real_http_transport_delivers_the_clean_warning_even_with_split_stream_chunks(
    bridge, stream
):
    raw = RAW + "\n\n" + FOOTER

    def handler(request):
        if not stream:
            return httpx.Response(
                200, json={"id": "test-run", "choices": [{"message": {"content": raw}}]}
            )
        pieces = [raw[:37], raw[37:90], raw[90:125], raw[125:]]
        body = "".join(
            "data: "
            + json.dumps({"id": "test-run", "choices": [{"delta": {"content": piece}}]})
            + "\n\n"
            for piece in pieces
        )
        return httpx.Response(
            200, text=body + "data: [DONE]\n\n", headers={"content-type": "text/event-stream"}
        )

    bridge._client = httpx.AsyncClient(
        base_url="http://synthetic-hermes.test", transport=httpx.MockTransport(handler)
    )
    bridge._is_available = True
    bridge._supports_session_runtime = False
    bridge._supports_model_options = False

    async def scenario():
        try:
            return await bridge.send_message(
                "Te toca", progress_callback=(lambda event: None) if stream else None
            )
        finally:
            await bridge._client.aclose()

    assert_honest_failure(asyncio.run(scenario()))


def test_agent_speaks_and_publishes_only_the_human_warning(bridge, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from core import agent as agent_module

    reply = bridge._parse_iris_response(RAW + "\n\n" + FOOTER)
    signals = {}
    for name in (
        "send_emotion",
        "send_animation",
        "send_speech_start",
        "send_subtitle",
        "send_speech_end",
        "send_status",
    ):
        signals[name] = AsyncMock()
        monkeypatch.setattr(agent_module, name, signals[name])
    monkeypatch.setattr(agent_module, "audio_clients_connected", lambda: 0)
    speak = AsyncMock(return_value=(b"", []))
    actor = SimpleNamespace(_speak_lock=asyncio.Lock(), tts=SimpleNamespace(speak=speak))
    assert asyncio.run(agent_module.IRISAgent._respond(actor, reply))
    assert speak.await_args.args[0] == reply["texto_hablado"]
    assert signals["send_speech_start"].await_args.args[0] == reply["texto_hablado"]
    assert signals["send_subtitle"].await_args.args[0] == reply["texto_hablado"]
    assert_honest_failure(reply)
