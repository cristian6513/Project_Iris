"""
IRIS Serial Bridge — Hardware Communication

Handles Serial/Bluetooth communication between IRIS Core
and Arduino/ESP32 hardware controllers.

Status: Scaffold — Future implementation (v0.5)
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from core.config import IRISConfig

logger = logging.getLogger("iris.hardware")


class SerialBridge:
    """Serial/Bluetooth communication with Arduino/ESP32."""

    def __init__(self, config: IRISConfig) -> None:
        self.config = config
        self.port = config.serial_port
        self.baudrate = config.serial_baudrate
        self._connection: Any = None
        self._available = False

    async def initialize(self) -> None:
        """Initialize serial connection."""
        if not self.config.hardware_enabled:
            logger.info("Hardware control disabled.")
            return

        if not self.port:
            logger.warning("No serial port configured.")
            return

        try:
            import serial_asyncio

            self._connection = await serial_asyncio.open_serial_connection(
                url=self.port,
                baudrate=self.baudrate,
            )
            self._available = True
            logger.info("Serial bridge connected: %s @ %d", self.port, self.baudrate)
        except ImportError:
            logger.error("pyserial-asyncio not installed. Run: pip install pyserial-asyncio")
        except Exception as e:
            logger.error("Serial connection failed: %s", e)

    @property
    def is_available(self) -> bool:
        return self._available

    async def send_command(self, command: dict[str, Any]) -> dict[str, Any]:
        """Send a hardware command via serial."""
        if not self._available or self._connection is None:
            return {"error": "hardware_unavailable"}

        try:
            reader, writer = self._connection
            data = json.dumps(command) + "\n"
            writer.write(data.encode())
            await writer.drain()

            # Wait for response
            response_line = await asyncio.wait_for(reader.readline(), timeout=5.0)
            return json.loads(response_line.decode().strip())
        except asyncio.TimeoutError:
            logger.error("Hardware command timed out.")
            return {"error": "timeout"}
        except Exception as e:
            logger.error("Hardware command error: %s", e)
            return {"error": str(e)}

    async def close(self) -> None:
        """Close the serial connection."""
        if self._connection:
            _, writer = self._connection
            writer.close()
            self._available = False
