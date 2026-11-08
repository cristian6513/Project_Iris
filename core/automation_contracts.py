"""F04: versioned contracts for Iris automations (rule, occurrence, result, delivery).

These are data, not behaviour: saving a rule runs nothing. F05 will decide
when a rule has an occurrence (from ``core.presence_state``); F06 will run it
with Hermes. Each object is separate, strict (unknown fields, wrong types and
non-finite numbers are rejected) and carries its own schema name.

Design: ``docs/iris_automations_google_plan.md`` ("Ejecución durable y
entrega al regresar"); report: ``docs/audits/iris_f04_automation_base_report_2026-10-10.md``.
"""

from __future__ import annotations

import re
import uuid
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

RULE_SCHEMA = "iris.automation-rule/v1"
OCCURRENCE_SCHEMA = "iris.automation-occurrence/v1"
RESULT_SCHEMA = "iris.automation-result/v1"
# F07: v2 adds the delivery's own revision, "snoozed", "dismissed_at" and the
# visual receipt. v1 documents stay valid and are read as revision 1; they are
# only rewritten (as v2) when the user or a receipt changes them.
DELIVERY_SCHEMA = "iris.automation-delivery/v2"
DELIVERY_SCHEMA_V1 = "iris.automation-delivery/v1"
PRESENTATION_SCHEMA = "iris.inbox-presentation/v1"
INBOX_PREFERENCES_SCHEMA = "iris.inbox-preferences/v1"

Identifier = Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
Timestamp = Annotated[float, Field(ge=0, le=1e12, allow_inf_nan=False)]
Revision = Annotated[int, Field(ge=1, le=1_000_000)]
OccurrenceKey = Annotated[str, Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9_.:-]+$")]
ProfileName = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9_-]*$")]
ShortText = Annotated[str, Field(min_length=1, max_length=120)]

# Where "no estoy" comes from. They are never swapped silently: not typing
# can mean reading, and a still head is not inactivity.
TriggerSource = Literal["iris_idle", "desktop_idle", "observed_absence"]
OccurrenceState = Literal[
    "candidate", "claimed", "submitted", "running", "completed", "failed", "cancelled", "ambiguous", "stopping",
]
FINAL_STATES = frozenset({"completed", "failed", "cancelled"})

# Allowed moves. "ambiguous": a submission whose fate is unknown (a POST that
# timed out); only the same request may settle it, never a new one.
# "stopping": Iris will not send anything more for it (the user cancelled, or
# replaying was no longer safe); it keeps its place until Hermes confirms the
# end or its deadline passes, and says what is known then.
TRANSITIONS: dict[str, frozenset[str]] = {
    "candidate": frozenset({"claimed", "cancelled"}),
    "claimed": frozenset({"candidate", "submitted", "ambiguous", "cancelled", "failed"}),
    "submitted": frozenset({"running", "completed", "failed", "ambiguous", "stopping"}),
    "running": frozenset({"completed", "failed", "ambiguous", "stopping"}),
    "ambiguous": frozenset({"running", "completed", "failed", "stopping"}),
    "stopping": frozenset({"completed", "failed"}),
    "completed": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
}

_TOOL = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")


def new_id() -> str:
    return uuid.uuid4().hex


class AutomationContract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class Trigger(AutomationContract):
    """When an occurrence may exist: a source of inactivity/absence held for ``threshold_s``."""

    source: TriggerSource
    threshold_s: Annotated[int, Field(ge=60, le=86_400)]
    repeat: Literal["once_per_episode"] = "once_per_episode"
    cooldown_s: Annotated[int, Field(ge=0, le=604_800)] = 0
    min_confidence: Annotated[float, Field(ge=0.5, le=1, allow_inf_nan=False)] = 0.7


class Execution(AutomationContract):
    """What F06 would ask Hermes; nothing here is sent by F04."""

    kind: Literal["hermes_prompt"] = "hermes_prompt"
    prompt: Annotated[str, Field(min_length=1, max_length=4000)]
    profile: ProfileName = "default"
    provider: Annotated[str, Field(max_length=120)] = ""
    model: Annotated[str, Field(max_length=200)] = ""
    tool_allowlist: Annotated[list[str], Field(max_length=32)] = []
    timeout_s: Annotated[int, Field(ge=30, le=7_200)] = 900
    max_runs_per_day: Annotated[int, Field(ge=1, le=48)] = 4
    token_budget: Annotated[int, Field(ge=0, le=2_000_000)] = 0  # 0: Hermes' own limits
    concurrency: Literal[1] = 1

    @field_validator("tool_allowlist")
    @classmethod
    def plain_tools(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value) or not all(_TOOL.fullmatch(item) for item in value):
            raise ValueError("tool_allowlist: nombres únicos de herramienta")
        return value

    @model_validator(mode="after")
    def model_with_provider(self) -> Execution:
        if bool(self.provider) != bool(self.model):
            raise ValueError("provider y model van juntos (ambos vacíos: el modelo del perfil)")
        return self


class QuietHours(AutomationContract):
    start_minute: Annotated[int, Field(ge=0, le=1439)]
    end_minute: Annotated[int, Field(ge=0, le=1439)]


class DeliveryPolicy(AutomationContract):
    when: Literal["on_return", "immediate", "when_surface"] = "on_return"
    voice: bool = False
    quiet_hours: QuietHours | None = None
    expiration_s: Annotated[int, Field(ge=60, le=2_592_000)] = 86_400


class AutomationRule(AutomationContract):
    """A user's rule. ``revision`` grows with every edit; occurrences name the one they belong to."""

    schema_name: Literal["iris.automation-rule/v1"] = Field(RULE_SCHEMA, alias="schema")
    id: Identifier
    revision: Revision = 1
    enabled: bool = False  # saved rules start paused: saving runs nothing
    title: ShortText
    trigger: Trigger
    execution: Execution
    delivery: DeliveryPolicy = DeliveryPolicy()
    created_at: Timestamp
    updated_at: Timestamp

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, populate_by_name=True)

    @model_validator(mode="after")
    def ordered_times(self) -> AutomationRule:
        if self.updated_at < self.created_at:
            raise ValueError("updated_at no puede ser anterior a created_at")
        return self


class Claim(AutomationContract):
    owner: Annotated[str, Field(min_length=1, max_length=96, pattern=r"^[A-Za-z0-9_.:-]+$")]
    token: Identifier
    lease_expires_at: Timestamp


class AutomationOccurrence(AutomationContract):
    """One chance for a rule to run: unique per (rule, rule revision, occurrence key)."""

    schema_name: Literal["iris.automation-occurrence/v1"] = Field(OCCURRENCE_SCHEMA, alias="schema")
    id: Identifier
    rule_id: Identifier
    rule_revision: Revision
    occurrence_key: OccurrenceKey
    state: OccurrenceState = "candidate"
    correlation_id: Identifier
    claim: Claim | None = None
    run_id: Annotated[str, Field(max_length=128)] = ""
    result_id: Identifier | None = None
    error: Annotated[str, Field(max_length=500)] = ""
    created_at: Timestamp
    updated_at: Timestamp

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, populate_by_name=True)

    @model_validator(mode="after")
    def consistent(self) -> AutomationOccurrence:
        if (self.state == "claimed") != (self.claim is not None):
            raise ValueError("solo una ocurrencia reclamada tiene claim")
        if self.state == "completed" and self.result_id is None:
            raise ValueError("una ocurrencia completada nombra su resultado")
        return self


class AutomationResult(AutomationContract):
    """What a run produced, as text: never internal JSON or avatar actions."""

    schema_name: Literal["iris.automation-result/v1"] = Field(RESULT_SCHEMA, alias="schema")
    id: Identifier
    occurrence_id: Identifier
    rule_id: Identifier
    status: Literal["completed", "failed"]
    text: Annotated[str, Field(max_length=20_000)] = ""
    sources: Annotated[list[Annotated[str, Field(max_length=500)]], Field(max_length=20)] = []
    finished_at: Timestamp

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, populate_by_name=True)


SurfaceName = Annotated[str, Field(min_length=1, max_length=96, pattern=r"^[A-Za-z0-9._:-]+$")]
DeliveryStatus = Literal["pending", "presented", "read", "snoozed", "dismissed", "expired"]


class VisualReceipt(AutomationContract):
    """A local surface said it showed this result in a return notice."""

    presentation_id: Identifier
    surface_id: SurfaceName
    shown_at: Timestamp


class AutomationDelivery(AutomationContract):
    """Presented, read, snoozed, dismissed and spoken are different facts; one does not imply another.

    ``revision`` grows with every change, so an action names the revision it
    saw. Reading a result never presents it, and presenting never reads it.
    """

    schema_name: Literal["iris.automation-delivery/v2"] = Field(DELIVERY_SCHEMA, alias="schema")
    result_id: Identifier
    revision: Revision = 1
    status: DeliveryStatus = "pending"
    presented_at: Timestamp | None = None
    read_at: Timestamp | None = None
    snoozed_until: Timestamp | None = None
    dismissed_at: Timestamp | None = None
    visual_receipt: VisualReceipt | None = None
    voice_attempts: Annotated[int, Field(ge=0, le=10)] = 0
    voice_receipt: Literal["", "spoken", "ambiguous", "failed"] = ""
    expires_at: Timestamp

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, populate_by_name=True)

    @model_validator(mode="before")
    @classmethod
    def read_v1(cls, data: Any) -> Any:
        """A v1 document is read as revision 1 of the same facts, never rewritten by reading."""
        if isinstance(data, dict) and data.get("schema") == DELIVERY_SCHEMA_V1:
            data = {**data, "schema": DELIVERY_SCHEMA}
        return data

    @model_validator(mode="after")
    def facts_in_order(self) -> AutomationDelivery:
        if self.status == "presented" and self.presented_at is None:
            raise ValueError("presentada necesita presented_at")
        if self.status == "read" and self.read_at is None:
            raise ValueError("leída necesita read_at")
        if self.status == "snoozed" and self.snoozed_until is None:
            raise ValueError("pospuesta necesita snoozed_until")
        if self.visual_receipt is not None and self.presented_at is None:
            raise ValueError("un recibo visual necesita presented_at")
        return self


PresentationState = Literal["claimed", "sent", "shown", "released", "ambiguous"]


class InboxPresentation(AutomationContract):
    """One return notice: claimed by one local surface with a lease, a token and a generation.

    ``claimed`` → ``sent`` (the socket accepted it) → ``shown`` (the surface's
    receipt). ``released``: certainly not shown (nothing went out, or the
    surface deferred it), so its items may be presented later. ``ambiguous``:
    it may have been shown; its items are never presented again automatically.
    """

    schema_name: Literal["iris.inbox-presentation/v1"] = Field(PRESENTATION_SCHEMA, alias="schema")
    id: Identifier
    generation: Annotated[int, Field(ge=1, le=10**12)]
    token: Identifier
    surface_id: SurfaceName
    state: PresentationState = "claimed"
    results: Annotated[list[Identifier], Field(max_length=50)] = []
    running: Annotated[list[Identifier], Field(max_length=10)] = []
    created_at: Timestamp
    lease_expires_at: Timestamp
    updated_at: Timestamp
    shown_at: Timestamp | None = None

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, populate_by_name=True)

    @model_validator(mode="after")
    def has_something(self) -> InboxPresentation:
        if not self.results and not self.running:
            raise ValueError("una presentación muestra al menos un resultado o una tarea en curso")
        if self.state == "shown" and self.shown_at is None:
            raise ValueError("mostrada necesita shown_at")
        return self


class InboxPreferences(AutomationContract):
    """Whether a return notice may appear, and the user's silence for it."""

    schema_name: Literal["iris.inbox-preferences/v1"] = Field(INBOX_PREFERENCES_SCHEMA, alias="schema")
    revision: Annotated[int, Field(ge=0, le=1_000_000)] = 0
    return_notice: bool = True
    quiet_until: Timestamp = 0.0

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, populate_by_name=True)


class AutomationPreferences(AutomationContract):
    """F08: separate, explicit consent; older inbox preferences remain unchanged."""

    schema_name: Literal["iris.automation-preferences/v1"] = Field("iris.automation-preferences/v1", alias="schema")
    revision: Annotated[int, Field(ge=0, le=1_000_000)] = 0
    desktop_idle: bool = False
    return_voice: bool = False
    focus: bool = False

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, populate_by_name=True)


def can_transition(current: str, target: str) -> bool:
    return target in TRANSITIONS.get(current, frozenset())


# ── F07: what the user can do with a delivered result ──────────────────────

DELIVERY_ACTIONS = ("read", "unread", "snooze", "dismiss", "restore")
SNOOZE_MINUTES = (10, 30, 60, 240)


def delivery_view(delivery: AutomationDelivery, now: float) -> str:
    """The status as seen now, derived without writing: a due snooze is back, an unseen result past its
    expiry is expired. Read and dismissed never expire."""
    status = delivery.status
    if status == "snoozed":
        if delivery.snoozed_until is not None and delivery.snoozed_until > now:
            return "snoozed"
        status = "presented" if delivery.presented_at is not None else "pending"
    if status in ("pending", "presented") and now >= delivery.expires_at:
        return "expired"
    return status


def delivery_actions(delivery: AutomationDelivery, now: float) -> tuple[str, ...]:
    view = delivery_view(delivery, now)
    if view == "dismissed":
        return ("restore",)
    if view == "read":
        return ("unread", "dismiss")
    if view == "snoozed":
        return ("restore", "read", "dismiss")  # restore: show it again now
    if view == "expired":
        return ("read", "dismiss")
    return ("read", "snooze", "dismiss")


def delivery_effect_holds(delivery: AutomationDelivery, action: str, now: float) -> bool:
    """Whether what ``action`` asks for is already true (a repeated request is then a no-op)."""
    view = delivery_view(delivery, now)
    return {
        "read": view == "read",
        "unread": delivery.read_at is None,
        "snooze": view == "snoozed",
        "dismiss": view == "dismissed",
        "restore": view not in ("dismissed", "snoozed"),
    }.get(action, False)


def apply_delivery_action(delivery: AutomationDelivery, action: str, now: float,
                          minutes: int | None = None) -> AutomationDelivery:
    """The next revision after ``action``; the caller checked it is allowed."""
    data = delivery.model_dump(by_alias=True)
    if action == "read":
        data.update(status="read", read_at=now, snoozed_until=None)
    elif action == "unread":
        data.update(status="presented" if delivery.presented_at is not None else "pending", read_at=None)
    elif action == "snooze":
        if minutes not in SNOOZE_MINUTES:
            raise ValueError(f"Posponer: {', '.join(map(str, SNOOZE_MINUTES))} minutos")
        until = now + minutes * 60
        # A reminder the user asked for is not lost to the expiry: it gets an hour after it comes back.
        data.update(status="snoozed", snoozed_until=until, expires_at=max(delivery.expires_at, until + 3600))
    elif action == "dismiss":
        data.update(status="dismissed", dismissed_at=now, snoozed_until=None)
    elif action == "restore":
        if delivery.status == "dismissed":
            data.update(status="read" if delivery.read_at is not None
                        else "presented" if delivery.presented_at is not None else "pending", dismissed_at=None)
        else:
            data.update(status="presented" if delivery.presented_at is not None else "pending", snoozed_until=None)
    else:
        raise ValueError(f"Acción desconocida: {action}")
    data.update(schema=DELIVERY_SCHEMA, revision=delivery.revision + 1)
    return AutomationDelivery.model_validate(data)
