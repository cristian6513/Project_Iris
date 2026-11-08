"""Connected IRIS surface registry and exclusive resource coordination.

IRIS can be rendered by more than one surface at once: the existing Face UI,
the local Desktop Companion, and eventually a remote client.  WebSocket
connections used to be stored in a plain set, which meant the TTS pipeline
treated every connection as an audio player and broadcast the same bytes to
all of them.

This module deliberately has no FastAPI dependency.  It owns only synchronous
metadata and selection rules, which keeps the policy deterministic and easy to
unit-test.  The WebSocket server remains responsible for actual I/O.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

SURFACE_ROLES = frozenset({"face", "desktop_companion", "remote", "legacy_face", "observer"})
CAPABILITY_NAMES = (
    "renders_avatar",
    "plays_tts",
    "reports_tts_playback",  # completion receipt, opt-in for updated clients
    "captures_camera",
    "accepts_chat",
    "controls_local_player",
    "presents_vision",
    "tunes_vision",
    # F03b: answers ``vision_snapshot_request`` (one photo for a chat turn).
    # No role has it by default: only a client that implements it says so.
    "takes_snapshots",
    "takes_screen_snapshots",
    # F07: shows the return notice of the general inbox and answers its receipt.
    # Separate from ``presents_vision``; no role has it by default.
    "presents_inbox",
    "speaks_inbox",  # F08b: one-owner audio with a final playback receipt
    # F09.4: runs IRIS OS apps with their own code and answers ``iris_app_intent`` (an app
    # action, or the confirmation of one that Core handles). No role has it by default.
    "hosts_iris_apps",
)

_ROLE_CAPABILITIES: dict[str, dict[str, bool]] = {
    "face": {
        "renders_avatar": True,
        "plays_tts": True,
        "captures_camera": True,
        "accepts_chat": True,
        "controls_local_player": True,
    },
    "desktop_companion": {
        "renders_avatar": True,
        "plays_tts": True,
        "captures_camera": True,
        "accepts_chat": True,
        "controls_local_player": False,
    },
    "remote": {
        "renders_avatar": True,
        "plays_tts": True,
        "captures_camera": True,
        "accepts_chat": True,
        "controls_local_player": False,
    },
    # A cached pre-surface-protocol Face client must behave exactly as it did
    # before this registry existed.
    "legacy_face": {
        "renders_avatar": True,
        "plays_tts": True,
        "captures_camera": True,
        "accepts_chat": True,
        # Cached clients predate the player intent protocol. Advertising it
        # here would route commands to a page that can never acknowledge them.
        "controls_local_player": False,
    },
    "observer": {
        "renders_avatar": False,
        "plays_tts": False,
        "captures_camera": False,
        "accepts_chat": False,
        "controls_local_player": False,
    },
}

# Used only when no visible surface has reported a real user interaction.
# The established Face remains the safe default; the Companion takes over as
# soon as Face is hidden/closed or the user interacts with the Companion.
_ROLE_IDLE_PRIORITY = {
    "face": 40,
    "legacy_face": 35,
    "desktop_companion": 30,
    "remote": 20,
    "observer": 0,
}

_SURFACE_ID_RE = re.compile(r"[^A-Za-z0-9._:-]+")


def _clean_surface_id(value: Any) -> str:
    cleaned = _SURFACE_ID_RE.sub("-", str(value or "").strip()).strip("-.")
    return cleaned[:96]


def _normalise_role(value: Any) -> str:
    role = str(value or "").strip().lower()
    return role if role in SURFACE_ROLES else "legacy_face"


def _capabilities_for(
    role: str,
    raw: Any,
    current: Mapping[str, bool] | None = None,
) -> dict[str, bool]:
    defaults = dict(current or _ROLE_CAPABILITIES[role])
    if not isinstance(raw, Mapping):
        return defaults
    for name in CAPABILITY_NAMES:
        value = raw.get(name)
        if isinstance(value, bool):
            defaults[name] = value
    return defaults


@dataclass(slots=True)
class SurfaceRecord:
    """Metadata for one live transport connection."""

    client: Any = field(repr=False)
    surface_id: str
    role: str
    capabilities: dict[str, bool]
    visible: bool
    focused: bool
    connected_at: float
    ready_at: float
    updated_at: float
    last_interaction_at: float | None = None

    def supports(self, capability: str) -> bool:
        return bool(self.capabilities.get(capability, False))

    def snapshot(self, *, audio_owner: bool = False, camera_owner: bool = False) -> dict[str, Any]:
        return {
            "surface_id": self.surface_id,
            "role": self.role,
            "visible": self.visible,
            "focused": self.focused,
            "capabilities": dict(self.capabilities),
            "audio_owner": audio_owner,
            "camera_owner": camera_owner,
        }


@dataclass(slots=True, frozen=True)
class CameraLeaseDecision:
    """Result of a camera lease request without performing any I/O."""

    granted: bool
    owner: Any | None
    previous_owner: Any | None = None
    reason: str = ""


class SurfaceRegistry:
    """Track live surfaces and choose exclusive presentation resources."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._records: dict[Any, SurfaceRecord] = {}
        self._camera_owner: Any | None = None
        self._camera_purpose = ""
        # Every lease grant, numbered: a photo taken for a turn is accepted only
        # if no other surface got the camera meanwhile (F03b).
        self._camera_generation = 0
        self._camera_grants: list[tuple[int, Any]] = []

    def connect(self, client: Any) -> SurfaceRecord:
        """Register a newly accepted socket as a backwards-compatible Face."""
        existing = self._records.get(client)
        if existing is not None:
            return existing
        now = self._clock()
        surface_id = self._unique_surface_id(f"legacy-{id(client):x}", client)
        record = SurfaceRecord(
            client=client,
            surface_id=surface_id,
            role="legacy_face",
            capabilities=dict(_ROLE_CAPABILITIES["legacy_face"]),
            visible=True,
            focused=False,
            connected_at=now,
            ready_at=now,
            updated_at=now,
        )
        self._records[client] = record
        return record

    def register_ready(
        self,
        client: Any,
        payload: Mapping[str, Any] | None = None,
    ) -> SurfaceRecord:
        """Apply a ``surface_ready`` payload to an existing/new connection."""
        payload = payload if isinstance(payload, Mapping) else {}
        record = self.connect(client)
        role = _normalise_role(payload.get("role", record.role))
        requested_id = _clean_surface_id(payload.get("surface_id")) or record.surface_id
        record.surface_id = self._unique_surface_id(requested_id, client)
        role_changed = role != record.role
        record.role = role
        record.capabilities = _capabilities_for(
            role,
            payload.get("capabilities"),
            None if role_changed else record.capabilities,
        )
        visible = payload.get("visible")
        focused = payload.get("focused")
        if isinstance(visible, bool):
            record.visible = visible
        if isinstance(focused, bool):
            record.focused = focused
        now = self._clock()
        record.ready_at = now
        record.updated_at = now
        if payload.get("interaction") is True:
            record.last_interaction_at = now
        return record

    def update_state(self, client: Any, payload: Mapping[str, Any] | None = None) -> SurfaceRecord:
        """Update visibility/focus/capabilities without changing identity."""
        payload = payload if isinstance(payload, Mapping) else {}
        record = self.connect(client)
        visible = payload.get("visible")
        focused = payload.get("focused")
        if isinstance(visible, bool):
            record.visible = visible
        if isinstance(focused, bool):
            record.focused = focused
        if "capabilities" in payload:
            record.capabilities = _capabilities_for(
                record.role, payload.get("capabilities"), record.capabilities,
            )
        now = self._clock()
        record.updated_at = now
        if payload.get("interaction") is True:
            record.last_interaction_at = now
        return record

    def mark_interaction(self, client: Any) -> SurfaceRecord:
        record = self.connect(client)
        now = self._clock()
        record.last_interaction_at = now
        record.updated_at = now
        return record

    def disconnect(self, client: Any) -> SurfaceRecord | None:
        if self._camera_owner is client:
            self._camera_owner = None
            self._camera_purpose = ""
        return self._records.pop(client, None)

    def clear(self) -> None:
        self._records.clear()
        self._camera_owner = None
        self._camera_purpose = ""

    def get(self, client: Any) -> SurfaceRecord | None:
        return self._records.get(client)

    def clients(self) -> tuple[Any, ...]:
        return tuple(self._records)

    def records(self) -> tuple[SurfaceRecord, ...]:
        return tuple(self._records.values())

    def __len__(self) -> int:
        return len(self._records)

    def capable_clients(self, capability: str, *, exclude: Iterable[Any] = ()) -> tuple[Any, ...]:
        excluded = set(exclude)
        return tuple(
            record.client
            for record in self._records.values()
            if record.client not in excluded and record.supports(capability)
        )

    def audio_capable_count(self) -> int:
        return sum(record.supports("plays_tts") for record in self._records.values())

    def audio_owner(self) -> Any | None:
        candidates = [record for record in self._records.values() if record.supports("plays_tts")]
        if not candidates:
            return None
        return max(candidates, key=self._audio_rank).client

    def capability_owner(self, capability: str, *, allowed: Callable[[Any], bool] | None = None) -> Any | None:
        """Choose one visible/active surface for a single-owner capability.

        Player commands must never be broadcast to every open IRIS tab or the
        same song would start several times.  Reusing the presentation rank
        keeps control on the focused surface, with the established Face as the
        deterministic idle fallback.  ``allowed`` narrows the candidates first
        (F09.4: app actions choose among this PC's own surfaces only).
        """
        candidates = [
            record for record in self._records.values()
            if record.supports(capability) and (allowed is None or allowed(record.client))
        ]
        if not candidates:
            return None
        return max(candidates, key=self._audio_rank).client

    def audio_owner_surface_id(self) -> str | None:
        owner = self.audio_owner()
        record = self._records.get(owner)
        return record.surface_id if record is not None else None

    def vision_owner(self, *, local_clients: Iterable[Any]) -> Any | None:
        """One visible, explicitly capable local surface; never a hidden fallback."""
        allowed = set(local_clients)
        candidates = [
            row for row in self._records.values()
            if row.client in allowed and row.visible
            and row.role in {"face", "desktop_companion"}
            and row.supports("renders_avatar") and row.supports("presents_vision")
        ]
        return max(candidates, key=self._audio_rank).client if candidates else None

    def _audio_rank(self, record: SurfaceRecord) -> tuple[float, ...]:
        interaction = record.last_interaction_at
        # A focused visible surface is where the user currently expects sound.
        # Previously *any* historical click permanently outranked focus, so an
        # older background tab could keep receiving every TTS response while
        # the active IRIS window appeared silent.
        if record.visible and record.focused:
            tier = 6
        elif record.visible and interaction is not None:
            tier = 5
        elif record.visible:
            tier = 3
        elif interaction is not None:
            tier = 2
        else:
            tier = 1
        return (
            float(tier),
            interaction if interaction is not None else -1.0,
            float(_ROLE_IDLE_PRIORITY.get(record.role, 0)),
            record.ready_at,
        )

    def request_camera(self, client: Any, *, purpose: str = "") -> CameraLeaseDecision:
        record = self.connect(client)
        if not record.supports("captures_camera"):
            return CameraLeaseDecision(
                granted=False,
                owner=self._camera_owner,
                reason="surface-does-not-capture-camera",
            )
        previous = self._camera_owner if self._camera_owner is not client else None
        self._camera_owner = client
        self._camera_purpose = str(purpose or "")[:80]
        self._camera_generation += 1
        self._camera_grants = [*self._camera_grants[-63:], (self._camera_generation, client)]
        return CameraLeaseDecision(
            granted=True,
            owner=client,
            previous_owner=previous,
        )

    def release_camera(self, client: Any) -> bool:
        if self._camera_owner is not client:
            return False
        self._camera_owner = None
        self._camera_purpose = ""
        return True

    def camera_owner(self) -> Any | None:
        return self._camera_owner

    def camera_generation(self) -> int:
        return self._camera_generation

    def camera_granted_only_to(self, client: Any, since: int) -> bool:
        """True when every lease granted after ``since`` went to ``client``."""
        grants = [owner for generation, owner in self._camera_grants if generation > since]
        if since < self._camera_generation - len(self._camera_grants):
            return False  # older than the history kept: cannot be accredited
        return all(owner is client for owner in grants)

    def camera_snapshot(self) -> dict[str, Any]:
        record = self._records.get(self._camera_owner)
        return {
            "active": record is not None,
            "surface_id": record.surface_id if record is not None else None,
            "purpose": self._camera_purpose if record is not None else "",
        }

    def snapshot_for(self, client: Any) -> dict[str, Any] | None:
        record = self._records.get(client)
        if record is None:
            return None
        return record.snapshot(
            audio_owner=self.audio_owner() is client,
            camera_owner=self._camera_owner is client,
        )

    def _unique_surface_id(self, requested: str, client: Any) -> str:
        base = requested or f"surface-{id(client):x}"
        used = {
            record.surface_id
            for other, record in self._records.items()
            if other is not client
        }
        if base not in used:
            return base
        suffix = f"-{id(client) & 0xFFFF:x}"
        return f"{base[: max(1, 96 - len(suffix))]}{suffix}"


__all__ = [
    "CAPABILITY_NAMES",
    "SURFACE_ROLES",
    "CameraLeaseDecision",
    "SurfaceRecord",
    "SurfaceRegistry",
]
