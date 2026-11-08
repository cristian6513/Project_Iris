"""Local notification contracts. No provider messages, media or executable actions."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from core.vision_experience import Feedback, Performance, Profile, VisionExperience
from core.vision_session import (
    Confidence,
    Identifier,
    Seconds,
    Timestamp,
    VisionCandidate,
    VisionContract,
)

NOTIFICATION_COPY = {
    "returned": (
        "Tu resumen de regreso",
        "Hay avisos de Vision pendientes. Puedes revisarlos cuando te venga bien.",
    ),
    "long_session": (
        "Un momento para ti",
        "Llevas un rato con presencia observada. Si te viene bien, puedes hacer una pausa.",
    ),
    "low_head_motion": (
        "¿Te viene bien una pausa?",
        "Se observó poco movimiento de cabeza durante varios minutos. "
        "Es una señal aproximada, no una evaluación de postura o salud.",
    ),
}

NotificationKind = Literal["long_session", "low_head_motion"]
CONVERSATION_COPY = {
    "returned": "Acabo de volver. ¿Me ayudas a organizar lo que quiero retomar? "
    "Prefiero elegir el siguiente paso contigo.",
    "long_session": "¿Me ayudas a decidir si hago una pausa breve y a organizar lo que sigue? "
    "Prefiero opciones sencillas que yo pueda elegir.",
    "low_head_motion": "Me gustaría organizar una pausa a mi ritmo. "
    "¿Qué opciones sencillas tengo, sin asumir cómo me siento ni mi estado físico?",
}


class NotificationEvidence(VisionContract):
    rule: Literal["long_session", "low_head_motion"]
    priority: Annotated[int, Field(ge=0, le=100)]
    observed_seconds: Seconds
    threshold_seconds: Seconds
    confidence: Confidence
    motion_window_seconds: Annotated[float, Field(ge=0, le=600, allow_inf_nan=False)]
    low_head_motion_ratio: Confidence
    basis: Literal["head_pose"] = "head_pose"


class ReturnEvidence(VisionContract):
    rule: Literal["returned"] = "returned"
    priority: Literal[30] = 30
    observed_seconds: Seconds
    threshold_seconds: Literal[30] = 30
    confidence: Confidence
    basis: Literal["presence"] = "presence"
    related_ids: Annotated[list[Identifier], Field(min_length=1, max_length=100)]

    @model_validator(mode="after")
    def unique_references(self):
        if len(set(self.related_ids)) != len(self.related_ids):
            raise ValueError("Duplicate summary reference")
        return self


class VisionNotification(VisionContract):
    candidate: VisionCandidate
    evidence: NotificationEvidence | ReturnEvidence
    delivered_at: Timestamp
    read_at: Timestamp | None = None
    dismissed_at: Timestamp | None = None
    snoozed_until: Timestamp = 0.0
    feedback: Feedback | None = None

    @model_validator(mode="after")
    def matching_rule(self) -> VisionNotification:
        if self.candidate.kind != self.evidence.rule:
            raise ValueError("Notification rule must match its candidate")
        return self

    def public(self, *, wall: float = 0.0, physical_suggestions: bool = True) -> dict:
        title, body = NOTIFICATION_COPY[self.evidence.rule]
        if not physical_suggestions and self.candidate.kind == "long_session":
            title = "Un momento para organizarte"
            body = (
                "Llevas un rato en esta sesión. Si te viene bien, puedes revisar prioridades "
                "o cambiar de ritmo a tu manera."
            )
        if isinstance(self.evidence, ReturnEvidence):
            count = len(self.evidence.related_ids)
            body = (
                f"Al regresar había {count} aviso(s) de Vision pendientes sin resumir. "
                "No se marcaron como leídos. Puedes revisarlos en esta bandeja."
            )
        status = (
            "dismissed"
            if self.dismissed_at is not None
            else ("snoozed" if self.snoozed_until > wall else "active")
        )
        return {
            "schema": "iris.notification/v1",
            "id": self.candidate.candidate_id,
            "source": "vision",
            "kind": self.candidate.kind,
            "title": title,
            "body": body,
            "created_at": self.delivered_at,
            "read_at": self.read_at,
            "unread": self.read_at is None,
            "status": status,
            "snoozed_until": self.snoozed_until,
            "feedback": self.feedback,
            "delivery": "inbox",
            "contains_media": False,
            "explanation": self.evidence.model_dump(),
        }


class NotificationPreferences(VisionContract):
    experience: VisionExperience = Field(default_factory=VisionExperience)
    return_summary: bool = False
    muted_until: Timestamp = 0.0
    disabled_kinds: Annotated[list[NotificationKind], Field(max_length=2)] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def unique_kinds(self):
        if len(self.disabled_kinds) != len(set(self.disabled_kinds)):
            raise ValueError("Duplicate notification kind")
        return self


class NotificationInbox(VisionContract):
    schema_version: Literal["iris.notification-inbox/v1"] = Field(
        default="iris.notification-inbox/v1",
        alias="schema",
    )
    revision: Annotated[int, Field(ge=0)] = 0
    items: Annotated[list[VisionNotification], Field(max_length=100)] = Field(default_factory=list)
    preferences: NotificationPreferences = Field(default_factory=NotificationPreferences)

    @model_validator(mode="after")
    def unique_ids(self) -> NotificationInbox:
        ids = [item.candidate.candidate_id for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("Notification IDs must be unique")
        return self

    def changed(self, items: list[VisionNotification]) -> NotificationInbox:
        return NotificationInbox(
            revision=self.revision + 1,
            items=items[-100:],
            preferences=self.preferences,
        )

    def retained(self, *, days: int, wall: float) -> list[VisionNotification]:
        if days <= 0:
            return []
        cutoff = wall - min(days, 30) * 86400
        # Do not discard future-dated entries after a clock correction.
        return [item for item in self.items if item.delivered_at >= cutoff]


class NotificationReadRequest(VisionContract):
    id: Identifier
    read: bool = True


class NotificationConfirmRequest(VisionContract):
    confirm: bool

    @model_validator(mode="after")
    def explicit_confirmation(self):
        if self.confirm is not True:
            raise ValueError("Explicit confirmation is required")
        return self


class NotificationAvailabilityRequest(NotificationConfirmRequest):
    available: bool


class NotificationMutationRequest(NotificationConfirmRequest):
    expected_revision: Annotated[int, Field(ge=0)]


class NotificationActionRequest(NotificationMutationRequest):
    id: Identifier
    action: Literal["dismiss", "restore", "snooze"]
    minutes: int | None = None

    @model_validator(mode="after")
    def valid_delay(self):
        if self.action == "snooze":
            if self.minutes not in {10, 30, 60}:
                raise ValueError("Choose 10, 30 or 60 minutes")
        elif self.minutes is not None:
            raise ValueError("Delay is only valid for snooze")
        return self


class NotificationPreferenceRequest(NotificationMutationRequest):
    action: Literal[
        "mute", "unmute", "disable_kind", "enable_kind", "enable_return", "disable_return"
    ]
    minutes: int | None = None
    kind: NotificationKind | None = None

    @model_validator(mode="after")
    def matching_action(self):
        if self.action == "mute":
            valid = self.kind is None and self.minutes in {10, 30, 60}
        elif self.action in {"unmute", "enable_return", "disable_return"}:
            valid = self.kind is None and self.minutes is None
        else:
            valid = self.kind is not None and self.minutes is None
        if not valid:
            raise ValueError("Fields do not match preference action")
        return self


class NotificationConversationRequest(NotificationConfirmRequest):
    id: Identifier


class NotificationFeedbackRequest(NotificationMutationRequest):
    id: Identifier
    feedback: Feedback


class NotificationCalibrationRequest(NotificationConfirmRequest):
    action: Literal["start", "reset"]


class NotificationExperienceRequest(NotificationMutationRequest):
    action: Literal["profile", "physical_suggestions", "greetings", "performance", "reset_feedback"]
    profile: Profile | None = None
    performance: Performance | None = None
    enabled: bool | None = None

    @model_validator(mode="after")
    def matching_setting(self):
        fields = {
            key for key in ("profile", "performance", "enabled") if getattr(self, key) is not None
        }
        expected = {
            "profile": {"profile"},
            "performance": {"performance"},
            "physical_suggestions": {"enabled"},
            "greetings": {"enabled"},
            "reset_feedback": set(),
        }[self.action]
        if fields != expected:
            raise ValueError("Fields do not match experience action")
        return self
