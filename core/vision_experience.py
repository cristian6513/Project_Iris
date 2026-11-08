"""Local, explicit Vision tuning. Presets never grant permissions or erase receipts."""

from typing import Annotated, Literal

from pydantic import Field

from core.vision_policy import VisionPolicySettings
from core.vision_session import VisionContract

Profile = Literal["custom", "silent", "company", "wellbeing", "work"]
Performance = Literal["economy", "balanced", "responsive"]
Feedback = Literal["useful", "too_soon", "not_applicable"]


class FeedbackDelays(VisionContract):
    long_session: Annotated[int, Field(ge=0, le=180)] = 0
    low_head_motion: Annotated[int, Field(ge=0, le=180)] = 0
    returned: Annotated[int, Field(ge=0, le=180)] = 0


class VisionExperience(VisionContract):
    profile: Profile = "custom"
    physical_suggestions: bool = True
    greetings: bool = True
    performance: Performance = "balanced"
    feedback_delays: FeedbackDelays = Field(default_factory=FeedbackDelays)


# Explicit overrides only. Custom always uses the user's existing policy.
PRESETS = {
    "silent": {"max_per_hour": 0},
    "company": {"max_per_hour": 1, "long_session_seconds": 3600},
    "wellbeing": {
        "max_per_hour": 2,
        "long_session_seconds": 2700,
        "low_head_session_seconds": 2400,
    },
    "work": {"max_per_hour": 1, "long_session_seconds": 5400},
}
PROFILE_COOLDOWN = {"silent": 25, "company": 45, "wellbeing": 30, "work": 60}


def effective_policy(base: VisionPolicySettings, experience: VisionExperience):
    data = base.model_dump()
    data.update(PRESETS.get(experience.profile, {}))
    for kind, minutes in experience.feedback_delays.model_dump().items():
        data["kind_cooldown_minutes"][kind] = min(
            180, data["kind_cooldown_minutes"][kind] + minutes
        )
    return VisionPolicySettings.model_validate(data)


def effective_cooldown(base: int, experience: VisionExperience) -> int:
    # A preset cannot shorten a user's stricter global cooldown.
    return max(base, PROFILE_COOLDOWN.get(experience.profile, base))


def experience_blocks(experience: VisionExperience) -> set[str]:
    if experience.profile == "silent":
        return {"long_session", "low_head_motion", "returned"}
    if not experience.physical_suggestions or experience.profile in {"company", "work"}:
        return {"low_head_motion"}
    return set()


def physical_copy(experience: VisionExperience) -> bool:
    return experience.physical_suggestions and experience.profile not in {"company", "work"}
