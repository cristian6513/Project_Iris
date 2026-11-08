"""Local-only Notification Center routes, isolated from Hermes tools and chat."""

from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, HTTPException

from core.config import IRISConfig
from core.notification_actions import NotificationConflictError
from core.vision_notifications import (
    NotificationActionRequest,
    NotificationAvailabilityRequest,
    NotificationCalibrationRequest,
    NotificationConfirmRequest,
    NotificationConversationRequest,
    NotificationExperienceRequest,
    NotificationFeedbackRequest,
    NotificationPreferenceRequest,
    NotificationReadRequest,
)


def notification_router(*, runtime, broadcast, vision_snapshot, calibrate=None) -> APIRouter:
    router = APIRouter(prefix="/api/notifications")

    async def snapshot():
        cfg = IRISConfig.load()
        body = await asyncio.to_thread(
            runtime().notification_snapshot,
            retention_days=cfg.vision_assistant_log_retention_days,
        )
        vision = vision_snapshot(cfg)
        body["vision"] = {
            "enabled": cfg.vision_assistant_enabled,
            "paused": cfg.vision_assistant_paused_until > time.time(),
            "state": vision["state"],
            "decision": vision["v2"]["decision"],
            "effective_policy": vision["effective_policy"],
            "effective_cooldown_minutes": vision["effective_cooldown_minutes"],
        }
        return body

    async def announce():
        body = await snapshot()
        await broadcast(
            {
                "revision": body["revision"],
                "unread": body["unread"],
                "availability": body["availability"],
                "experience": body["preferences"]["experience"],
            }
        )
        return body

    async def perform(operation):
        try:
            return await asyncio.to_thread(operation)
        except KeyError as exc:
            raise HTTPException(
                status_code=404, detail="El aviso ya no está en la bandeja."
            ) from exc
        except NotificationConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=503, detail="No pude guardar el cambio local.") from exc

    @router.post("/action")
    async def notification_action(payload: NotificationActionRequest):
        cfg = IRISConfig.load()
        await perform(
            lambda: runtime().act_on_notification(
                payload,
                retention_days=cfg.vision_assistant_log_retention_days,
            )
        )
        return await announce()

    @router.post("/preferences")
    async def notification_preferences(payload: NotificationPreferenceRequest):
        await perform(lambda: runtime().set_notification_preferences(payload))
        return await announce()

    @router.post("/conversation")
    async def prepare_conversation(payload: NotificationConversationRequest):
        cfg = IRISConfig.load()
        draft = await perform(
            lambda: runtime().notification_draft(
                payload.id,
                retention_days=cfg.vision_assistant_log_retention_days,
            )
        )
        # Only the initiating browser receives a draft. No chat, TTS or Hermes call.
        return {"draft": draft}

    @router.post("/experience")
    async def set_experience(payload: NotificationExperienceRequest):
        await perform(lambda: runtime().set_vision_experience(payload))
        return await announce()

    @router.post("/feedback")
    async def set_feedback(payload: NotificationFeedbackRequest):
        cfg = IRISConfig.load()
        await perform(
            lambda: runtime().record_vision_feedback(
                payload, retention_days=cfg.vision_assistant_log_retention_days
            )
        )
        return await announce()

    @router.post("/clear-diagnostics")
    async def clear_diagnostics(payload: NotificationConfirmRequest):
        runtime().clear_vision_diagnostics()
        return await snapshot()

    @router.post("/calibration")
    async def calibration(payload: NotificationCalibrationRequest):
        if calibrate is None:
            raise HTTPException(status_code=409, detail="Esta superficie no admite calibración.")
        await calibrate(payload.action)
        body = await snapshot()
        body["calibration_requested"] = True
        return body

    @router.get("")
    async def get_notifications():
        return await snapshot()

    @router.post("/read")
    async def read_notification(payload: NotificationReadRequest):
        try:
            await asyncio.to_thread(runtime().update_notification, payload.id, read=payload.read)
        except KeyError as exc:
            raise HTTPException(
                status_code=404, detail="El aviso ya no está en la bandeja."
            ) from exc
        except OSError as exc:
            raise HTTPException(
                status_code=503, detail="No pude guardar el estado de lectura."
            ) from exc
        return await announce()

    @router.post("/clear")
    async def clear_notifications(payload: NotificationConfirmRequest):
        try:
            await asyncio.to_thread(runtime().clear_notifications)
        except OSError as exc:
            raise HTTPException(status_code=503, detail="No pude vaciar la bandeja local.") from exc
        return await announce()

    @router.post("/availability")
    async def set_availability(payload: NotificationAvailabilityRequest):
        cfg = IRISConfig.load()
        if payload.available and (
            not cfg.vision_assistant_enabled or cfg.vision_assistant_paused_until > time.time()
        ):
            raise HTTPException(
                status_code=409,
                detail="Primero habilita Vision Assistant y quita su pausa desde Configuración.",
            )
        runtime().set_notification_availability(payload.available)
        return await announce()

    return router
