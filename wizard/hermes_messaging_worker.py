"""Private, persistent JSON-lines adapter to Hermes' native Channels APIs."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

PLUGIN_ID = "iris-messaging"
NON_CHAT = {"api_server", "a2a", "relay", "msgraph_webhook", "raft"}
# Adapters of the pinned revision that run a program outside Python. Node for
# the WhatsApp QR bridge and the Photon sidecar, the Raft CLI for Raft.
PROGRAMS = {"whatsapp": "node", "photon": "node", "raft": "raft"}
# Hermes 0.21.6 moved on-demand packages to its ``pm`` package: a channel's
# dependencies are pyproject extras. These are the channels 0.21.4 listed in
# ``tools.lazy_deps`` as ``platform.<id>``; the others need no extra.
CHANNEL_EXTRAS = {
    "telegram": ("telegram",), "discord": ("discord",), "slack": ("slack",), "matrix": ("matrix",),
    "dingtalk": ("dingtalk",), "feishu": ("feishu",), "wecom_callback": ("wecom",), "teams": ("teams",),
    "google_chat": ("google", "google-chat"),
}
REPO = ""


def _package_manager():
    """Hermes' ``pm.extras`` (0.21.6 and later), or ``None`` on 0.21.4."""
    try:
        from pm import extras
    except ImportError:
        return None
    return extras


def _node_present():
    import hermes_constants

    if _package_manager() is not None:
        # Hermes runs only the Node its package manager installed, never the
        # PATH copy; this lookup never installs or repairs one.
        return bool(hermes_constants.find_node_executable("node"))
    # Passive: Hermes' own resolver may try to heal (download) a managed Node.
    names = ("node.exe", "node.cmd", "node") if os.name == "nt" else ("node",)
    managed = any((Path(d) / n).is_file() for d in hermes_constants.iter_hermes_node_dirs() for n in names)
    return managed or bool(hermes_constants.find_node_executable_on_path("node"))


def _availability(available, code, detail="", packages=()):
    return {"available": available, "code": code, "detail": detail, "packages": sorted(packages)}


def _channel_packages(platform_id):
    """``(unsupported here, missing packages, Hermes may install them)``, passively."""
    extras = _package_manager()
    if extras is not None:
        from pm.install import lazy_installs_allowed

        names = CHANNEL_EXTRAS.get(platform_id, ())
        if not all(extras.extra_supported(name) for name in names):
            return True, set(), False
        # An extra is named by itself: its modules are not package names.
        return False, {name for name in names if extras.missing(name)}, lazy_installs_allowed()
    from tools import lazy_deps

    feature = f"platform.{platform_id}"
    if lazy_deps._unsupported_feature_reason(feature):
        return True, set(), False
    missing = lazy_deps.feature_missing(feature) if feature in lazy_deps.LAZY_DEPS else ()
    return False, {lazy_deps._pkg_name_from_spec(spec) for spec in missing}, lazy_deps._allow_lazy_installs()


def requirements(platform_id, repo):
    """Can this runtime run the channel now? Passive: never installs anything.

    Missing packages only count when Hermes may install them itself; Iris'
    packaged runtime seals that (``HERMES_DISABLE_LAZY_INSTALLS``) and limits
    PATH, so the same rules report the package's real capabilities.
    """
    import shutil

    unsupported, packages, may_install = _channel_packages(platform_id)
    if unsupported:
        return _availability(False, "unsupported_platform", "Hermes no admite este canal en este sistema operativo.")
    if packages and not may_install:
        return _availability(
            False, "missing_packages",
            "Esta instalación no incluye sus componentes y su motor no instala paquetes.", packages,
        )
    if platform_id == "whatsapp":
        from hermes_constants import get_hermes_home

        bridge = Path("scripts", "whatsapp-bridge", "bridge.js")
        if not ((Path(repo) / bridge).is_file() or (get_hermes_home() / bridge).is_file()):
            return _availability(
                False, "missing_bridge",
                "Esta instalación no incluye el puente de WhatsApp con QR. Usa WhatsApp Cloud API.",
            )
    program = PROGRAMS.get(platform_id)
    if (program == "node" and not _node_present()) or (
        program and program != "node" and not shutil.which(program)
    ):
        name = "Node.js" if program == "node" else program
        return _availability(False, "missing_program", f"Necesita {name}, que esta instalación no ofrece.")
    if packages:
        return _availability(True, "installs_on_start", "Hermes instalará sus componentes al iniciarlo.", packages)
    return _availability(True, "")


def require_available(platform_id, repo):
    from fastapi import HTTPException

    status = requirements(platform_id, repo)
    if not status["available"]:
        raise HTTPException(409, f"Este canal no está disponible: {status['detail']}")
    return status


def install_policy(entries):
    """Install our owned extension without editing Hermes source or starting a channel."""
    import yaml
    from hermes_cli.config import get_config_path
    from hermes_constants import get_hermes_home
    from utils import atomic_yaml_write  # Hermes' root module, in 0.21.4 and 0.21.6

    root = get_hermes_home() / "plugins" / PLUGIN_ID
    marker = root / ".iris-owned"
    if root.exists() and not marker.exists():
        raise ValueError("Ya existe una extensión iris-messaging ajena a Iris; no se reemplazó.")
    source = Path(__file__).with_name("iris_messaging_plugin.py").read_bytes()
    channels = sorted(e["id"] for e in entries if e["id"] not in NON_CHAT)
    path = get_config_path()
    config = yaml.safe_load(path.read_text("utf-8")) if path.exists() else {}
    if config is None:
        config = {}
    if not isinstance(config, dict):
        raise ValueError("La configuración de Hermes no es válida.")
    plugins = config.setdefault("plugins", {})
    display_root = config.setdefault("display", {})
    if not isinstance(plugins, dict) or not isinstance(display_root, dict):
        raise ValueError("La configuración de extensiones o canales no es válida.")
    enabled = plugins.setdefault("enabled", [])
    disabled = plugins.setdefault("disabled", [])
    display = display_root.setdefault("platforms", {})
    if (
        not isinstance(enabled, list)
        or not isinstance(disabled, list)
        or not isinstance(display, dict)
    ):
        raise ValueError("La configuración de extensiones o canales no es válida.")
    if PLUGIN_ID not in enabled:
        enabled.append(PLUGIN_ID)
    plugins["disabled"] = [name for name in disabled if name != PLUGIN_ID]
    for channel in channels:
        settings = display.setdefault(channel, {})
        if not isinstance(settings, dict):
            raise ValueError("La configuración de presentación del canal no es válida.")
        # Only delivery rendering: keep model streaming and Iris' API untouched.
        settings["streaming"] = False
        settings["interim_assistant_messages"] = False
    root.mkdir(parents=True, exist_ok=True)
    files = {
        "__init__.py": source,
        "plugin.yaml": (
            b"name: iris-messaging\nversion: '1.0.0'\n"
            b"description: Texto visible de Iris en canales humanos\n"
            b"hooks:\n  - transform_llm_output\n  - pre_llm_call\n"
        ),
        "iris-policy.json": json.dumps({"channels": channels}).encode(),
        ".iris-owned": hashlib.sha256(source).hexdigest().encode(),
    }
    for name, content in files.items():
        dest = root / name
        if not dest.exists() or dest.read_bytes() != content:
            temp = dest.with_name(dest.name + ".tmp")
            temp.write_bytes(content)
            os.replace(temp, dest)
    atomic_yaml_write(path, config)


def policy_ready(entries=None):
    from hermes_cli.config import load_config_readonly
    from hermes_cli.web_server_messaging import _messaging_platform_catalog
    from hermes_constants import get_hermes_home

    root = get_hermes_home() / "plugins" / PLUGIN_ID
    try:
        cfg = load_config_readonly()
        plugins = cfg.get("plugins", {})
        channels = {
            e["id"] for e in (entries or _messaging_platform_catalog()) if e["id"] not in NON_CHAT
        }
        stored = set(json.loads((root / "iris-policy.json").read_text("utf-8"))["channels"])
        display = cfg.get("display", {}).get("platforms", {})
        expected = Path(__file__).with_name("iris_messaging_plugin.py").read_bytes()
        return (
            (root / ".iris-owned").exists()
            and (root / "__init__.py").read_bytes() == expected
            and PLUGIN_ID in plugins.get("enabled", [])
            and PLUGIN_ID not in plugins.get("disabled", [])
            and channels == stored
            and all(
                display.get(c, {}).get("streaming") is False
                and display.get(c, {}).get("interim_assistant_messages") is False
                for c in channels
            )
        )
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False


def public_platform(platform):
    # Never send even a prefix/suffix of credentials to the wizard.
    fields = [
        {
            key: f.get(key)
            for key in (
                "key",
                "required",
                "is_set",
                "is_password",
                "prompt",
                "description",
                "help",
                "url",
                "advanced",
            )
        }
        for f in platform.get("env_vars", [])
    ]
    result = {
        key: platform.get(key)
        for key in (
            "id",
            "name",
            "description",
            "docs_url",
            "enabled",
            "configured",
            "gateway_running",
            "state",
        )
    }
    result.update(fields=fields, managed=platform["id"] == "api_server")
    return result


async def dispatch(action, data):
    from fastapi import HTTPException
    from hermes_cli import __version__
    from hermes_cli import web_server_messaging as support
    from hermes_cli.web_models import (
        MessagingPlatformUpdate,
        WhatsAppOnboardingApply,
        WhatsAppOnboardingStart,
    )
    from hermes_cli.web_routers import messaging as native

    if action == "catalog":
        if data.get("refresh"):
            from hermes_cli.plugins import discover_plugins

            await asyncio.to_thread(discover_plugins, force=True)
        result = await native.get_messaging_platforms()
        return {
            "platforms": [
                {**public_platform(p), "availability": requirements(p["id"], REPO)}
                for p in result["platforms"]
            ],
            "hermes_version": __version__,
            "policy_ready": policy_ready(result["platforms"]),
        }
    if action == "prepare":
        install_policy(support._messaging_platform_catalog())
        return {"ok": True, "needs_restart": True}
    if action == "save":
        pid = data["platform"]
        if pid == "api_server":
            raise HTTPException(400, "El servidor de Iris se configura en su sección de conexión.")
        entry = native._require_platform(pid)
        body = MessagingPlatformUpdate(**data["update"])
        if body.profile:
            raise HTTPException(400, "Solo se modifica el perfil de Hermes utilizado por Iris.")
        allowed = set(entry["env_vars"])
        if (set(body.env) | set(body.clear_env)) - allowed:
            raise HTTPException(400, "El campo no pertenece a este canal.")
        if set(body.env) & set(body.clear_env):
            raise HTTPException(400, "No puedes cambiar y borrar el mismo campo a la vez.")
        for key, value in body.env.items():
            native._validate_messaging_env_value(pid, key, value.strip())
        from hermes_cli.config import _env_write_blocked, load_env

        for key in set(body.env) | set(body.clear_env):
            if _env_write_blocked(key, "set"):
                raise HTTPException(409, "Hermes ha bloqueado la escritura de esta configuración.")
        values = {**load_env(), **{k: v.strip() for k, v in body.env.items() if v.strip()}}
        for key in body.clear_env:
            values.pop(key, None)
        if body.enabled and any(not values.get(k) for k in entry["required_env"]):
            raise HTTPException(
                400, "Completa los campos obligatorios antes de activar este canal."
            )
        if body.enabled or body.env:
            # A channel this runtime cannot start may only be switched off or cleared;
            # a new value (e.g. WHATSAPP_ENABLED=true) would enable it another way.
            require_available(pid, REPO)
        install_policy(support._messaging_platform_catalog())
        result = await native.update_messaging_platform(pid, body)
        saved = load_env()
        if any(saved.get(k) != v.strip() for k, v in body.env.items() if v.strip()) or any(
            saved.get(k) for k in body.clear_env
        ):
            raise HTTPException(500, "Hermes no confirmó el guardado de todos los campos.")
        return {"ok": bool(result["ok"]), "needs_restart": True}
    if action == "test":
        result = await native.test_messaging_platform(data["platform"])
        # Native diagnostic text can contain provider bodies. Keep a bounded state only.
        return {"ok": bool(result["ok"]), "state": result.get("state", "unknown")}
    if action == "restart":
        from hermes_cli.web_server_gateway import _restart_gateway_after

        return _restart_gateway_after(None, what="Iris channels", label="Iris channels")
    if action == "whatsapp_start":
        require_available("whatsapp", REPO)
    if action in {"whatsapp_start", "whatsapp_poll"}:
        result = (
            await native.start_whatsapp_onboarding(WhatsAppOnboardingStart(**data))
            if action == "whatsapp_start"
            else await native.get_whatsapp_onboarding_status(data["pairing_id"])
        )
        if result.get("error"):
            result["error"] = (
                "No se pudo completar la vinculación. Revisa la instalación de WhatsApp en Hermes."
            )
        return result
    if action == "whatsapp_cancel":
        return await native.cancel_whatsapp_onboarding(data["pairing_id"])
    if action == "whatsapp_apply":
        require_available("whatsapp", REPO)
        status = await native.get_whatsapp_onboarding_status(data["pairing_id"])
        if status["status"] != "connected":
            raise HTTPException(409, "WhatsApp todavía no ha terminado de vincularse.")
        from hermes_cli.config import _env_write_blocked

        if any(
            _env_write_blocked(key, "set")
            for key in (
                "WHATSAPP_MODE",
                "WHATSAPP_DM_POLICY",
                "WHATSAPP_ALLOWED_USERS",
                "WHATSAPP_ENABLED",
            )
        ):
            raise HTTPException(409, "Hermes ha bloqueado la escritura de esta configuración.")
        install_policy(support._messaging_platform_catalog())
        # Saving is distinct from restarting; the wizard exposes an explicit restart button.
        with patch.object(
            native,
            "_restart_gateway_after_whatsapp_onboarding",
            return_value={"restart_started": False},
        ):
            return await native.apply_whatsapp_onboarding(
                data["pairing_id"], WhatsAppOnboardingApply()
            )
    raise HTTPException(400, "Operación de mensajería desconocida.")


def _stdin_pipe():
    """wizard.stdin_pipe, or the copy next to this script. The packaged Hermes interpreter runs
    isolated (python311._pth): neither Iris nor this script's folder is on its sys.path, so the
    sibling is loaded by file path, never by adding the wizard folder to Hermes' imports."""
    try:
        from wizard import stdin_pipe
    except ImportError:
        import importlib.util

        spec = importlib.util.spec_from_file_location("iris_stdin_pipe", Path(__file__).with_name("stdin_pipe.py"))
        if spec is None or spec.loader is None:
            raise
        stdin_pipe = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(stdin_pipe)
    return stdin_pipe


async def main():
    global REPO
    REPO = sys.argv[1]
    sys.path.insert(0, sys.argv[1])
    output = sys.stdout
    with contextlib.redirect_stdout(sys.stderr):
        try:
            # One request at a time (the wizard holds a lock); polled so that a
            # pending stdin read cannot freeze DLL loads of background work.
            requests = _stdin_pipe().LineReader()
            while line := await asyncio.to_thread(requests.readline):
                try:
                    request = json.loads(line)
                    result = await dispatch(request["action"], request.get("data", {}))
                    reply = {"ok": True, "data": result}
                except Exception as exc:
                    status = getattr(exc, "status_code", 500)
                    detail = getattr(exc, "detail", "")
                    reply = {
                        "ok": False,
                        "status": status,
                        "message": detail
                        if status in {400, 404, 409, 410} and isinstance(detail, str)
                        else "No se pudo completar la operación con esta instalación de Hermes.",
                    }
                output.write(json.dumps(reply, ensure_ascii=True) + "\n")
                output.flush()
        finally:
            from hermes_cli.web_routers import messaging as native
            from hermes_cli.web_server_messaging import _whatsapp_onboarding_sessions

            for pairing_id in list(_whatsapp_onboarding_sessions):
                await native.cancel_whatsapp_onboarding(pairing_id)


if __name__ == "__main__":
    asyncio.run(main())
