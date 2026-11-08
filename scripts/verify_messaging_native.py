"""Check Iris/Hermes channel contracts in temporary homes, with networking blocked."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import importlib.util
import io
import json
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


async def verify(repo):
    sys.path.insert(0, str(repo))
    path = ROOT / "wizard/hermes_messaging_worker.py"
    spec = importlib.util.spec_from_file_location("iris_messaging_worker", path)
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)

    def offline(*_args, **_kwargs):
        raise RuntimeError("External network is disabled during this test")

    with patch.object(socket.socket, "connect", offline):
        from hermes_cli.config import get_config_path, load_config_readonly, load_env
        from hermes_constants import get_hermes_home

        get_config_path().write_text(
            "model:\n  provider: existing-provider\n  streaming: true\n", encoding="utf-8"
        )
        (get_hermes_home() / ".env").write_text(
            "OPENAI_API_KEY=unrelated-test-value\n", encoding="utf-8"
        )
        catalog = await worker.dispatch("catalog", {})
        ids = [p["id"] for p in catalog["platforms"]]
        assert {"telegram", "whatsapp", "matrix", "teams", "api_server"} <= set(ids)
        assert len(ids) > 8
        token = "123456:" + "A" * 40
        result = await worker.dispatch(
            "save",
            {
                "platform": "telegram",
                "update": {
                    "enabled": False,
                    "env": {"TELEGRAM_BOT_TOKEN": token, "TELEGRAM_ALLOWED_USERS": "12345"},
                },
            },
        )
        assert result["ok"]
        assert load_env()["TELEGRAM_BOT_TOKEN"] == token
        assert load_env()["OPENAI_API_KEY"] == "unrelated-test-value"
        cfg = load_config_readonly()
        assert cfg["model"]["provider"] == "existing-provider"
        assert cfg["model"]["streaming"] is True
        assert cfg["display"]["platforms"]["telegram"]["streaming"] is False
        assert cfg["display"]["platforms"]["telegram"]["interim_assistant_messages"] is False
        assert (
            "api_server"
            not in json.loads(
                (get_hermes_home() / "plugins/iris-messaging/iris-policy.json").read_text()
            )["channels"]
        )
        assert worker.policy_ready()
        from fastapi import HTTPException

        try:
            await worker.dispatch(
                "save",
                {
                    "platform": "telegram",
                    "update": {
                        "env": {
                            "TELEGRAM_BOT_TOKEN": "234567:" + "B" * 40,
                            "TELEGRAM_ALLOWED_USERS": "invalid-user",
                        }
                    },
                },
            )
            raise AssertionError("invalid IDs accepted")
        except HTTPException as exc:
            assert exc.status_code == 400
        assert load_env()["TELEGRAM_BOT_TOKEN"] == token
        for platform, update in [
            ("api_server", {"enabled": False}),
            ("telegram", {"env": {"PATH": "bad"}}),
        ]:
            try:
                await worker.dispatch("save", {"platform": platform, "update": update})
                raise AssertionError("unowned settings accepted")
            except HTTPException as exc:
                assert exc.status_code == 400
        catalog = await worker.dispatch("catalog", {"refresh": True})
        assert token not in json.dumps(catalog)
        import logging

        from agent.turn_finalizer import _apply_output_hooks

        message = json.dumps(
            {"texto_hablado": "Hola", "emocion": "feliz", "animacion_virtual": "iris_wave"}
        )
        args = dict(effective_task_id="test", original_user_message="hola", messages=[])
        agent = SimpleNamespace(session_id="test", model="fake", _persist_disabled=True)
        # One turn per platform: Hermes 0.21.6 records a turn's transform by turn_id.
        text, changed, _ = _apply_output_hooks(
            agent, message, logging.getLogger("test"), platform="telegram", turn_id="telegram-turn", **args
        )
        assert (text, changed) == ("Hola", True)
        text, changed, _ = _apply_output_hooks(
            agent, message, logging.getLogger("test"), platform="api_server", turn_id="api-turn", **args
        )
        assert text == message and not changed

        from agent.turn_context import _collect_pre_llm_call_context

        agent._persist_disabled = False
        agent.platform = "telegram"
        context = _collect_pre_llm_call_context(
            agent,
            effective_task_id="test",
            turn_id="test",
            original_user_message="hola",
            messages=[],
            conversation_history=[],
        )
        assert "texto normal" in context
        agent.platform = "api_server"
        assert not _collect_pre_llm_call_context(
            agent,
            effective_task_id="test",
            turn_id="test",
            original_user_message="hola",
            messages=[],
            conversation_history=[],
        )
        from gateway.display_config import resolve_display_setting

        assert resolve_display_setting(cfg, "telegram", "streaming", True) is False
        assert resolve_display_setting(cfg, "api_server", "streaming", True) is True

        from tools import send_message_tool as sender

        plugin_path = get_hermes_home() / "plugins/iris-messaging/__init__.py"
        plugin_spec = importlib.util.spec_from_file_location("iris_send_test", plugin_path)
        plugin = importlib.util.module_from_spec(plugin_spec)
        plugin_spec.loader.exec_module(plugin)
        sent = []

        def capture(args):
            sent.append(args)
            return "captured locally"

        with patch.object(sender, "_handle_send", capture):
            ctx = SimpleNamespace(register_hook=lambda *_args: None)
            plugin.register(ctx)
            plugin.register(ctx)
            sender.send_message_tool(
                {"target": "telegram:123", "message": message, "mentions": ["123"]}
            )
            assert sent == [{"target": "telegram:123", "message": "Hola", "mentions": ["123"]}]
            sender.send_message_tool({"target": "api_server:123", "message": message})
            assert sent[-1]["message"] == message

        # Even a dispatcher obtained before plugin discovery uses the late
        # delivery boundary, with media/thread arguments preserved.
        from unittest.mock import AsyncMock

        delivery = AsyncMock(spec=[], return_value={"success": True})
        with patch.object(sender, "_send_to_platform", delivery):
            plugin.register(ctx)
            plugin.register(ctx)
            await sender._send_to_platform(
                "telegram",
                None,
                "123",
                message,
                thread_id="thread",
                media_files=[("image", "/tmp/test.png")],
                args={"target": "telegram:123", "message": message},
            )
            assert delivery.await_count == 1
            assert delivery.call_args.args[3] == "Hola"
            assert delivery.call_args.kwargs["thread_id"] == "thread"
            assert delivery.call_args.kwargs["media_files"] == [("image", "/tmp/test.png")]
            assert delivery.call_args.kwargs["args"]["message"] == "Hola"

        from hermes_cli.web_routers import messaging as native
        from hermes_cli.web_server_messaging import _whatsapp_onboarding_sessions

        whatsapp = worker.requirements("whatsapp", worker.REPO)
        if whatsapp["code"] == "missing_bridge":
            # A packaged tree ships without the QR bridge: the start is refused, nothing runs.
            try:
                await worker.dispatch("whatsapp_start", {"mode": "self-chat"})
                raise AssertionError("WhatsApp QR started without its bridge")
            except HTTPException as exc:
                assert exc.status_code == 409
            qr_lifecycle = "refused: this tree has no QR bridge"
        else:
            # Exercise native records/events/apply/cancel, replacing only the bridge thread.
            with patch.object(native.threading.Thread, "start"):
                pairing = await worker.dispatch("whatsapp_start", {"mode": "self-chat"})
            pid = pairing["pairing_id"]
            record = _whatsapp_onboarding_sessions[pid]
            record.status = "installing"
            assert (await worker.dispatch("whatsapp_poll", {"pairing_id": pid}))[
                "status"
            ] == "installing"
            native._apply_pairing_event(record, {"event": "qr", "qr": "local-test-qr"})
            assert (await worker.dispatch("whatsapp_poll", {"pairing_id": pid}))[
                "qr_payload"
            ] == "local-test-qr"
            native._apply_pairing_event(
                record, {"event": "connected", "user": {"id": "15551234567@s.whatsapp.net"}}
            )
            with patch.object(
                native,
                "_restart_gateway_after_whatsapp_onboarding",
                side_effect=AssertionError("Unexpected real restart"),
            ):
                applied = await worker.dispatch("whatsapp_apply", {"pairing_id": pid})
            assert applied["needs_restart"] and not applied["restart_started"]
            assert load_env()["WHATSAPP_ALLOWED_USERS"] == "15551234567"
            assert load_env()["WHATSAPP_MODE"] == "self-chat"
            with patch.object(native.threading.Thread, "start"):
                pairing = await worker.dispatch("whatsapp_start", {"mode": "bot"})
            await worker.dispatch("whatsapp_cancel", {"pairing_id": pairing["pairing_id"]})
            assert pairing["pairing_id"] not in _whatsapp_onboarding_sessions
            qr_lifecycle = True
        await worker.dispatch(
            "save", {"platform": "telegram", "update": {"clear_env": ["TELEGRAM_BOT_TOKEN"]}}
        )
        assert not load_env().get("TELEGRAM_BOT_TOKEN")
        assert load_env()["OPENAI_API_KEY"] == "unrelated-test-value"
        return {
            "platform_count": len(ids),
            "platforms": ids,
            "native_save_clear": True,
            "validation_before_write": True,
            "native_hook": True,
            "direct_delivery_filter": True,
            "native_qr_lifecycle": qr_lifecycle,
            "avatar_unchanged": True,
            "network": "blocked",
            "real_messages_sent": 0,
        }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--child", type=Path)
    args = parser.parse_args()
    if args.child:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = asyncio.run(verify(args.child))
        print(json.dumps(result))
        return
    sys.path.insert(0, str(ROOT))
    from core.hermes_runtime import get_hermes_repo, hermes_subprocess_env, resolve_hermes_python

    with tempfile.TemporaryDirectory(prefix="iris-channels-") as temporary:
        env = {
            k: v
            for k, v in hermes_subprocess_env().items()
            if not any(part in k.upper() for part in ("TOKEN", "KEY", "SECRET", "HERMES", "NOUS"))
        }
        env.update(HERMES_HOME=temporary, PYTHONDONTWRITEBYTECODE="1")
        result = subprocess.run(
            [
                resolve_hermes_python(),
                "-B",
                str(Path(__file__).resolve()),
                "--child",
                str(get_hermes_repo()),
            ],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=90,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode:
            raise SystemExit(result.stderr or result.stdout)
        report = json.loads(result.stdout)
    output = ROOT / "artifacts/messaging-review"
    output.mkdir(parents=True, exist_ok=True)
    (output / "native.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
