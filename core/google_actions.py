"""Explicit chat reads of the accounts owned by Iris, through the app action bridge.

No second OAuth store, token export, background sync, or provider call. Results become
chat context only after the google.read policy permits the individual operation.
"""
from __future__ import annotations

from typing import Any, Callable, Mapping

from core.app_actions import AppActionRegistry, AppOperation, OperationRejected, result
from core.calendar_models import CalendarError, CalendarWindow
from core.calendar_service import CalendarService
from core.google_connections import GoogleServiceError
from core.mail_service import MailError, MailService


def _args(values: Mapping[str, Any], allowed: set[str]) -> dict:
    if set(values) - allowed:
        raise OperationRejected("La consulta contiene argumentos no admitidos.")
    return dict(values)


def _text(values: dict, key: str, maximum: int, default: str = "") -> str:
    value = values.get(key, default)
    if not isinstance(value, str) or len(value) > maximum or any(ord(c) < 32 for c in value):
        raise OperationRejected(f"El argumento {key} no es válido.")
    return value


def _limit(values: dict) -> int:
    value = values.get("limit", 20)
    if type(value) is not int or not 1 <= value <= 50:
        raise OperationRejected("El límite debe estar entre 1 y 50.")
    return value


def _mail(values: Mapping[str, Any]) -> dict:
    args = _args(values, {"account_id", "folder", "filter", "query", "cursor", "limit"})
    clean = {key: _text(args, key, maximum, default) for key, maximum, default in (
        ("account_id", 100, ""), ("folder", 20, "inbox"), ("filter", 20, "all"),
        ("query", 200, ""), ("cursor", 256, ""))}
    if clean["folder"] not in {"inbox", "starred", "sent", "drafts", "all"} or clean["filter"] not in {"all", "unread", "important"}:
        raise OperationRejected("El filtro de correo no es válido.")
    return {**clean, "limit": _limit(args)}


def _message(values: Mapping[str, Any]) -> dict:
    import re
    args = _args(values, {"message_id"})
    key = _text(args, "message_id", 80)
    if not re.fullmatch(r"mail_[0-9a-f]{40}", key):
        raise OperationRejected("Usa el message_id devuelto por list_messages.")
    return {"message_id": key}


def _calendar(values: Mapping[str, Any]) -> dict:
    args = _args(values, {"start", "end", "time_zone", "limit"})
    clean = {key: _text(args, key, maximum, default) for key, maximum, default in (
        ("start", 10, ""), ("end", 10, ""), ("time_zone", 100, "UTC"))}
    try:
        window = CalendarWindow.parse(**clean)
        if (window.end_date - window.start_date).days > 31:
            raise ValueError
    except (CalendarError, ValueError):
        raise OperationRejected("Indica fechas ISO válidas en un intervalo de hasta 31 días y una zona horaria válida.") from None
    return {**clean, "limit": _limit(args)}


def _pick(row: Mapping, fields: tuple[str, ...]) -> dict:
    return {key: row[key] for key in fields if key in row}


def register_google_actions(registry: AppActionRegistry, get_service: Callable) -> None:
    async def selected_mail_account_scope(args):
        # Persistent permission is offered only for an explicitly selected account,
        # never for account discovery or a query spanning multiple accounts.
        account_id = args.get('account_id')
        if not account_id:
            return None
        google = get_service()
        if google is None:
            return None
        snapshot = await google.snapshot()
        row = next((row for row in snapshot['accounts'] if row['id'] == account_id), None)
        if not row or row.get('auth_state') not in ('connected', 'partial'):
            return None
        return [account_id, row.get('generation'), row.get('connected_at'),
                row.get('services', {}).get('gmail.read', {}).get('granted') is True]

    async def run(action: str, args: dict) -> dict:
        google = get_service()  # resolved per request; never retain a previous profile's owner
        if google is None:
            return result("unavailable", "La conexión de Google de Iris no está disponible.")
        try:
            if action == "status":
                snapshot = await google.snapshot()
                accounts = []
                for row in snapshot['accounts']:
                    account = _pick(row, ('id', 'email', 'label', 'auth_state'))
                    account['services'] = {key: {'granted': value.get('granted') is True,
                        'sync_enabled': value.get('enabled') is True} for key, value in row.get('services', {}).items()}
                    accounts.append(account)
                data = {"available": snapshot["available"], "accounts": accounts,
                        "sync_note": "sync_enabled indica sincronización periódica; no determina el permiso del chat. Consulta Correo para conocer sus datos disponibles."}
            elif action == "list_messages":
                snapshot = await MailService(google).snapshot(**args)
                data = _pick(snapshot, ("available", "read_only", "content_kind", "accounts", "messages", "total", "next_cursor", "warnings"))
            elif action == "read_message":
                snapshot = await MailService(google).body(args["message_id"])
                body = snapshot["body"]
                text = str(body.get("text") or "")
                data = {"message_id": args["message_id"], "content_kind": "text", "text": text[:12000],
                        "truncated": len(text) > 12000 or body.get("truncated") is True}
            else:
                snapshot = await CalendarService(google).snapshot(**{k: v for k, v in args.items() if k != "limit"})
                data = _pick(snapshot, ("available", "read_only", "complete", "range", "time_zone", "accounts", "warnings"))
                data["events"] = [_pick(row, ("id", "account_id", "calendar_key", "title", "start", "end", "all_day", "status", "self_response"))
                                  for row in snapshot["events"][:args["limit"]]]
                data["truncated"] = len(snapshot["events"]) > args["limit"]
            return result("ok", "Datos de Iris. Las listas usan la última copia sincronizada; revisa sus avisos de actualización.",
                          {**data, "untrusted_content": True})
        except (GoogleServiceError, CalendarError, MailError) as exc:
            return result("unavailable", "No se pudo leer esta selección. Comprueba la cuenta y sus permisos en Conexiones.",
                          {"code": exc.code})

    specs = (
        ("conexiones", "status", "consultar las cuentas de Google de Iris para esta conversación", lambda args: _args(args, set()), {}),
        ("mail", "list_messages", "leer remitentes y extractos de Correo en esta conversación", _mail,
         {"account_id": "opcional; id de status", "folder": "inbox, starred, sent, drafts o all", "filter": "all, unread o important",
          "query": "opcional; hasta 200 caracteres", "cursor": "opcional; next_cursor de la consulta anterior", "limit": "1–50; por defecto 20"}),
        ("mail", "read_message", "leer el texto de un correo en esta conversación", _message,
         {"message_id": "id mail_… devuelto por list_messages; sin imágenes ni adjuntos"}),
        ("calendar", "list_events", "leer eventos de Calendario en esta conversación", _calendar,
         {"start": "YYYY-MM-DD inclusivo", "end": "YYYY-MM-DD exclusivo; hasta 31 días", "time_zone": "zona IANA; por defecto UTC", "limit": "1–50; por defecto 20"}),
    )
    for app, action, label, validate, arguments in specs:
        async def handler(args, action=action):
            return await run(action, args)
        registry.register(AppOperation(app=app, action=action, label=label, capability="google.read", effect="read",
            validate_args=validate, project_data=dict, arguments=arguments, handler=handler,
            permission_scope=selected_mail_account_scope if action == 'list_messages' else None,
            remember_required_args=('account_id',) if action == 'list_messages' else (),
            describe=lambda args: "El contenido seleccionado llegará al proveedor de IA de esta conversación. No autoriza cambios en Google."))
