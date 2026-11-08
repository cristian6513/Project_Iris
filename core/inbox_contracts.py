"""F07: the general inbox envelope (``iris.inbox-item/v1``).

One item says *what happened* and *what happened to its delivery*, as two
separate facts: an automation can have failed and still be unread, or be
running while its card was already presented. Its identity is composite —
origin, profile and the source's own id — so a Vision notice and an
automation result with the same id are different items, and so are the same
automation id in two profiles.

Items are projections of their source of truth, never copies that could
disagree with it: an automation item is built from the occurrence, result and
delivery that ``AutomationStore.finish()`` wrote in one transaction, and a
Vision notice keeps living in ``iris.notification/v1`` (its own store, actions
and silent delivery); the adapter only wraps it for a general view. Hermes
results are never stored as Vision notices. Calendar notices stay in their own
profile store. Gmail projects the separate F14 classification/delivery ledger.
"""

from __future__ import annotations

import time
from datetime import date, datetime
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.automation_contracts import delivery_actions, delivery_view
from core.calendar_models import safe_public_https

INBOX_SCHEMA = "iris.inbox-item/v1"
ORIGINS = ("automation", "vision", "calendar", "gmail", "meeting", "background", "app")
RESERVED_ORIGINS = ()

Identifier = Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
ProfileName = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9_-]*$")]
Timestamp = Annotated[float, Field(ge=0, le=1e12, allow_inf_nan=False)]

# What the source says happened (execution), never whether the user saw it.
Outcome = Literal["completed", "failed", "running", "unknown", "notice"]
# What happened to its delivery: presented, read, snoozed and dismissed are separate facts.
DeliveryStatus = Literal["pending", "presented", "read", "snoozed", "dismissed", "expired"]
Action = Literal["read", "unread", "snooze", "dismiss", "restore"]
# Outcomes that are something to read; running and unknown work is shown, never counted as unread.
READABLE_OUTCOMES = ("completed", "failed", "notice")


def inbox_key(origin: str, profile: str, source_id: str) -> str:
    return f"{origin}:{profile}:{source_id}"


class InboxContract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, populate_by_name=True)


class InboxDelivery(InboxContract):
    status: DeliveryStatus = "pending"
    presented_at: Timestamp | None = None
    read_at: Timestamp | None = None
    snoozed_until: Timestamp | None = None
    dismissed_at: Timestamp | None = None
    expires_at: Timestamp | None = None
    revision: Annotated[int, Field(ge=0, le=1_000_000)] = 0
    # A return notice about it may have been shown without a receipt: it is not presented again by itself.
    uncertain: bool = False
    voice_attempts: Annotated[int, Field(ge=0, le=10)] = 0
    voice_receipt: Literal['', 'spoken', 'failed', 'ambiguous'] = ''

    @model_validator(mode="after")
    def facts_in_order(self) -> InboxDelivery:
        if self.status == "read" and self.read_at is None:
            raise ValueError("leída necesita read_at")
        if self.status == "snoozed" and self.snoozed_until is None:
            raise ValueError("pospuesta necesita snoozed_until")
        return self


class InboxItem(InboxContract):
    schema_name: Literal["iris.inbox-item/v1"] = Field(INBOX_SCHEMA, alias="schema")
    key: Annotated[str, Field(max_length=200)]
    origin: Literal["automation", "vision", "calendar", "gmail", "meeting", "background", "app"]
    profile: ProfileName
    source_id: Identifier
    source_revision: Annotated[int, Field(ge=0, le=1_000_000)] = 0
    title: Annotated[str, Field(min_length=1, max_length=120)]
    text: Annotated[str, Field(max_length=20_000)] = ""  # visible text only: never control JSON
    outcome: Outcome
    delivery: InboxDelivery = InboxDelivery()
    # What the general inbox may do with it now; a Vision notice keeps its actions in its own view.
    actions: Annotated[list[Action], Field(max_length=5)] = []
    rule_id: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")] | None = None
    calendar_event_id: Annotated[str, Field(pattern=r"^event_[0-9a-f]{32}$")] | None = None
    calendar_date: Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")] | None = None
    join_url: Annotated[str, Field(max_length=2000)] | None = None
    mail_account_id: Annotated[str, Field(pattern=r"^gacc_[0-9a-f]{32}$")] | None = None
    mail_message_id: Annotated[str, Field(pattern=r"^mail_[0-9a-f]{40}$")] | None = None
    # An installed app that posted the notice (F09 app notices); its name is in the title's kicker.
    app_id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]{1,31}$")] | None = None
    app_name: Annotated[str, Field(max_length=80)] | None = None
    created_at: Timestamp
    updated_at: Timestamp

    @model_validator(mode="after")
    def composite_identity(self) -> InboxItem:
        if self.key != inbox_key(self.origin, self.profile, self.source_id):
            raise ValueError("la clave debe ser origen:perfil:id")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at no puede ser anterior a created_at")
        if self.origin not in ("calendar", "meeting") and any(value is not None for value in
                (self.calendar_event_id, self.calendar_date, self.join_url)):
            raise ValueError("los enlaces y destinos del calendario solo pertenecen a esa fuente")
        if self.origin == "calendar" and (self.outcome != "notice" or self.rule_id is not None):
            raise ValueError("el calendario entrega avisos, no resultados de automatizaciones")
        if self.origin == "meeting" and (self.outcome not in ("completed", "failed") or not self.calendar_event_id
                or not self.calendar_date or self.join_url is not None or self.rule_id is not None):
            raise ValueError("la preparación requiere destino explícito y no tiene acciones remotas")
        if self.origin == "background" and (self.outcome not in ("completed", "failed") or self.rule_id):
            raise ValueError("una tarea en segundo plano entrega su resultado o su fallo, sin regla")
        if (self.origin == "app") != (self.app_id is not None) or (self.origin == "app") != (self.app_name is not None):
            raise ValueError("solo un aviso de app nombra su app, y siempre la nombra")
        if self.origin == "app" and (self.outcome != "notice" or self.rule_id):
            raise ValueError("una app entrega avisos, sin regla")
        if self.origin == "gmail":
            if not self.mail_account_id or not self.mail_message_id or self.outcome != "notice" or self.rule_id:
                raise ValueError("el aviso de correo requiere su destino local explícito")
        elif self.mail_account_id is not None or self.mail_message_id is not None:
            raise ValueError("el destino de correo solo pertenece a Gmail")
        if self.calendar_date is not None:
            date.fromisoformat(self.calendar_date)
        if self.join_url is not None and safe_public_https(self.join_url) != self.join_url:
            raise ValueError("el enlace del calendario debe ser HTTPS público y canónico")
        return self


_OCCURRENCE_OUTCOMES = {
    "completed": "completed", "failed": "failed",
    "submitted": "running", "running": "running", "ambiguous": "unknown", "stopping": "unknown",
}


def is_unread(item: InboxItem) -> bool:
    return item.outcome in READABLE_OUTCOMES and item.delivery.status in ("pending", "presented")


def from_automation(*, profile: str, rule_title: str, occurrence: Any, result: Any = None,
                    delivery: Any = None, now: float | None = None, uncertain: bool = False) -> InboxItem | None:
    """The inbox item of one automation occurrence, or None when it has nothing to show.

    Unsent occurrences (candidate, claimed, cancelled) never reach the inbox.
    The text is the result's visible text; a failure keeps its explanation.
    The delivery is shown as it is now (a due snooze is back, an unseen result
    past its expiry is expired) without writing anything.
    """
    outcome = _OCCURRENCE_OUTCOMES.get(occurrence.state)
    if outcome is None:
        return None
    now = time.time() if now is None else now
    view, actions = InboxDelivery(), []
    if delivery is not None:
        status = delivery_view(delivery, now)
        view = InboxDelivery(
            status=status, presented_at=delivery.presented_at, read_at=delivery.read_at,
            snoozed_until=delivery.snoozed_until if status == "snoozed" else None,
            dismissed_at=delivery.dismissed_at if status == "dismissed" else None,
            expires_at=delivery.expires_at, revision=delivery.revision,
            uncertain=uncertain and delivery.presented_at is None,
            voice_attempts=delivery.voice_attempts, voice_receipt=delivery.voice_receipt)
        actions = list(delivery_actions(delivery, now))
    text = result.text if result is not None else {
        "running": "Hermes la está ejecutando.",
        "unknown": "Iris todavía no sabe cómo terminó; lo dirá cuando lo sepa.",
    }.get(outcome, "")
    return InboxItem(
        key=inbox_key("automation", profile, occurrence.id), origin="automation", profile=profile,
        source_id=occurrence.id, source_revision=occurrence.rule_revision,
        title=(rule_title or "Automatización")[:120], text=text[:20_000], outcome=outcome,
        delivery=view, actions=actions, rule_id=occurrence.rule_id,
        created_at=occurrence.created_at, updated_at=max(occurrence.updated_at, occurrence.created_at))


def from_vision(*, profile: str, notice: dict[str, Any], revision: int = 0) -> InboxItem | None:
    """Wrap an ``iris.notification/v1`` Vision notice for the general view (read-only).

    Its actions keep going to ``/api/notifications``; nothing is copied into the
    automation store and no Hermes result is ever shaped like this. Its
    revision is the Vision inbox's, which Vision's own actions name.
    """
    if not isinstance(notice, dict) or notice.get("schema") != "iris.notification/v1" or notice.get("source") != "vision":
        return None
    status = {"active": "read" if notice.get("unread") is False else "pending",
              "snoozed": "snoozed", "dismissed": "dismissed"}.get(notice.get("status"), "pending")
    created = float(notice.get("created_at") or 0)
    try:
        return InboxItem(
            key=inbox_key("vision", profile, str(notice.get("id"))), origin="vision", profile=profile,
            source_id=str(notice.get("id")), title=str(notice.get("title") or "Aviso de Vision")[:120],
            text=str(notice.get("body") or "")[:20_000], outcome="notice",
            # Vision does not record when a notice was read; its own read_at if any, else its creation.
            delivery=InboxDelivery(status=status,
                                   read_at=float(notice.get("read_at") or created) if status == "read" else None,
                                   snoozed_until=float(notice.get("snoozed_until") or 0) or None
                                   if status == "snoozed" else None,
                                   revision=max(0, int(revision))),
            created_at=created, updated_at=created)
    except (ValueError, TypeError):
        return None


def from_calendar(*, profile: str, notice: Any, time_zone: str = "UTC",
                  now: float | None = None) -> InboxItem | None:
    """Project one durable CalendarNotice; reading does not present or read it.

    Destinations come only from the source's explicit fields, never its title,
    description or text. Calendar remains the owner of its delivery actions.
    """
    if not notice.active:
        return None
    now = time.time() if now is None else now
    status = notice.delivery_status(now)
    return InboxItem(
        key=inbox_key("calendar", profile, notice.id), origin="calendar", profile=profile,
        source_id=notice.id, source_revision=notice.revision,
        title=notice.title, text=notice.text, outcome="notice",
        calendar_event_id=notice.event_id,
        calendar_date=datetime.fromtimestamp(notice.start, ZoneInfo(time_zone)).date().isoformat(),
        join_url=safe_public_https(notice.join_url),
        delivery=InboxDelivery(status=status, presented_at=notice.presented_at, read_at=notice.read_at,
                               snoozed_until=notice.snoozed_until if status == "snoozed" else None,
                               dismissed_at=notice.dismissed_at if status == "dismissed" else None,
                               expires_at=notice.expires_at, revision=notice.revision,
                               uncertain=getattr(notice, "presentation_attempted_at", None) is not None
                               and notice.presented_at is None),
        actions=list(notice.actions(now)), created_at=notice.created_at, updated_at=notice.updated_at)


def from_mail(*, profile: str, job: dict) -> InboxItem:
    """A durable local relevance notice; all destinations come from admission, never the model."""
    delivery = job["delivery"]
    return InboxItem(key=inbox_key("gmail", profile, job["id"]), origin="gmail", profile=profile,
        source_id=job["id"], source_revision=job["revision"], title=job["subject"] or "Correo importante",
        text=job["result"]["summary"] + "\n" + job["result"]["reason"], outcome="notice",
        mail_account_id=job["account_id"], mail_message_id=job["message_id"],
        delivery=InboxDelivery(status=delivery, revision=job["revision"], read_at=job["read_at"], dismissed_at=job["dismissed_at"]),
        actions=["restore"] if delivery == "dismissed" else ["unread", "dismiss"] if delivery == "read" else ["read", "dismiss"],
        created_at=job["created_at"], updated_at=job["updated_at"])


def from_meeting(*, profile: str, job: dict) -> InboxItem:
    result = job.get("result")
    text = (result["summary"] + "\nPuntos:\n" + "\n".join(result["points"]) + "\nPreguntas:\n" + "\n".join(result["questions"])) if result else "No se pudo preparar la reunión. Los recordatorios de horario siguen funcionando."
    delivery = job["delivery"]
    return InboxItem(key=inbox_key("meeting", profile, job["id"]), origin="meeting", profile=profile,
        source_id=job["id"], source_revision=job["revision"], title="Preparación: " + job["title"][:105],
        text=text, outcome=job["state"], calendar_event_id=job["event_id"], calendar_date=job.get("calendar_date", job["date"]),
        delivery=InboxDelivery(status=delivery, revision=job["revision"], read_at=job["read_at"], dismissed_at=job["dismissed_at"]),
        actions=["restore"] if delivery == "dismissed" else ["unread", "dismiss"] if delivery == "read" else ["read", "dismiss"],
        created_at=job["created_at"], updated_at=job["updated_at"])


def from_background(*, profile: str, task: dict) -> InboxItem:
    """A finished background task kept for its own conversation: the agent's report, or why it ended."""
    from core.background_tasks import outcome_text

    completed = task["state"] == "completed"
    body = task["result"] if completed else outcome_text(task)
    text = (f"Encargo: «{task['request']}»\n\n{body}")[:20_000]
    status = {"read": "read", "dismissed": "dismissed"}.get(task["inbox_status"], "pending")
    return InboxItem(key=inbox_key("background", profile, task["id"]), origin="background", profile=profile,
        source_id=task["id"], source_revision=task["revision"], title=("Tarea: " + task["title"])[:120],
        text=text, outcome="completed" if completed else "failed",
        delivery=InboxDelivery(status=status, revision=task["revision"], read_at=task["read_at"],
                               dismissed_at=task["dismissed_at"]),
        actions=["restore"] if status == "dismissed" else ["unread", "dismiss"] if status == "read" else ["read", "dismiss"],
        created_at=task["created_at"], updated_at=max(task["updated_at"], task["created_at"]))


def from_app_notice(*, profile: str, notice: dict) -> InboxItem:
    """A notice an installed app posted, once it is due: kept until the person reads or dismisses it."""
    status = {"read": "read", "dismissed": "dismissed"}.get(notice["inbox_status"], "pending")
    return InboxItem(key=inbox_key("app", profile, notice["id"]), origin="app", profile=profile,
        source_id=notice["id"], source_revision=notice["revision"], title=notice["title"][:120],
        text=notice["text"][:20_000], outcome="notice", app_id=notice["app_id"], app_name=notice["app_name"][:80],
        delivery=InboxDelivery(status=status, presented_at=notice["presented_at"], revision=notice["revision"],
                               read_at=notice["read_at"], dismissed_at=notice["dismissed_at"]),
        actions=["restore"] if status == "dismissed" else ["unread", "dismiss"] if status == "read" else ["read", "dismiss"],
        created_at=notice["created_at"], updated_at=max(notice["updated_at"], notice["created_at"]))
