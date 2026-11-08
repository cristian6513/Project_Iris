"""
IRIS fallback — offline emergency mode

Provides basic responses when Hermes/PicoClaw is unavailable.
Handles common commands without LLM access.
"""

from __future__ import annotations

import datetime
import logging
import platform
from typing import Any

logger = logging.getLogger("iris.fallback")


class FallbackEngine:
    """Offline response engine for when the LLM is unavailable."""

    def __init__(self) -> None:
        self._commands: dict[str, Any] = {
            "hora": self._get_time,
            "qué hora es": self._get_time,
            "qué hora": self._get_time,
            "fecha": self._get_date,
            "qué fecha": self._get_date,
            "qué día es": self._get_date,
            "hola": self._greet,
            "buenos días": self._greet,
            "buenas tardes": self._greet,
            "buenas noches": self._greet,
            "cómo estás": self._status,
            "estado": self._system_status,
            "ayuda": self._help,
            "help": self._help,
        }

    def process(self, user_input: str) -> dict[str, Any]:
        """
        Process user input offline.

        Returns IRIS' JSON response format.
        """
        normalized = user_input.lower().strip()

        # Check for exact or partial matches
        for trigger, handler in self._commands.items():
            if trigger in normalized:
                return handler()

        # Default fallback
        return {
            "texto_hablado": (
                "Lo siento, estoy en modo sin conexión y no puedo procesar esa solicitud. "
                "Puedo decirte la hora, la fecha, o mi estado del sistema. "
                "Intenta reconectarme ejecutando: hermes (con API_SERVER_ENABLED=true en ~/.hermes/.env)"
            ),
            "emocion": "triste",
            "animacion_virtual": "pensar",
        }

    def _get_time(self) -> dict[str, Any]:
        now = datetime.datetime.now()
        time_str = now.strftime("%I:%M %p")
        return {
            "texto_hablado": f"Son las {time_str}.",
            "emocion": "neutral",
            "animacion_virtual": "asentir",
        }

    def _get_date(self) -> dict[str, Any]:
        now = datetime.datetime.now()
        # Spanish month names
        months = [
            "", "enero", "febrero", "marzo", "abril", "mayo", "junio",
            "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
        ]
        days = [
            "lunes", "martes", "miércoles", "jueves",
            "viernes", "sábado", "domingo",
        ]
        day_name = days[now.weekday()]
        date_str = f"{day_name}, {now.day} de {months[now.month]} de {now.year}"
        return {
            "texto_hablado": f"Hoy es {date_str}.",
            "emocion": "neutral",
            "animacion_virtual": "asentir",
        }

    def _greet(self) -> dict[str, Any]:
        now = datetime.datetime.now()
        hour = now.hour
        if hour < 12:
            greeting = "¡Buenos días!"
        elif hour < 18:
            greeting = "¡Buenas tardes!"
        else:
            greeting = "¡Buenas noches!"

        return {
            "texto_hablado": f"{greeting} Soy IRIS. Estoy en modo sin conexión, "
            "pero puedo ayudarte con información básica.",
            "emocion": "feliz",
            "animacion_virtual": "saludar_feliz",
        }

    def _status(self) -> dict[str, Any]:
        return {
            "texto_hablado": (
                "Estoy funcionando en modo sin conexión. "
                "No puedo acceder a Hermes, pero mis funciones básicas están activas."
            ),
            "emocion": "neutral",
            "animacion_virtual": "pensar",
        }

    def _system_status(self) -> dict[str, Any]:
        system = platform.system()
        node = platform.node()
        python_ver = platform.python_version()
        return {
            "texto_hablado": (
                f"Estado del sistema: {system} en {node}. "
                f"Python {python_ver}. "
                "Hermes: desconectado. Modo: offline."
            ),
            "emocion": "neutral",
            "animacion_virtual": "asentir",
        }

    def _help(self) -> dict[str, Any]:
        return {
            "texto_hablado": (
                "En modo sin conexión puedo responder: "
                "la hora, la fecha, saludos, y mi estado del sistema. "
                "Para funcionalidad completa, asegúrate de que Hermes esté ejecutándose."
            ),
            "emocion": "neutral",
            "animacion_virtual": "asentir",
        }
