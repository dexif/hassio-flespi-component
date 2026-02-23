"""Coordinator for the mqtt_flespi_message integration."""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

from homeassistant.components import mqtt
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback

from .const import (
    ATTR_ALT_ALTITUDE,
    ATTR_ALT_DIRECTION,
    ATTR_ALT_HDOP,
    ATTR_ALT_LATITUDE,
    ATTR_ALT_LONGITUDE,
    ATTR_ALT_SATELLITES,
    ATTR_ALT_SPEED,
    ATTR_POSITION_ALTITUDE,
    ATTR_POSITION_DIRECTION,
    ATTR_POSITION_HDOP,
    ATTR_POSITION_LATITUDE,
    ATTR_POSITION_LONGITUDE,
    ATTR_POSITION_SATELLITES,
    ATTR_POSITION_SPEED,
)

_LOGGER = logging.getLogger(__name__)

# Mapping from alternative (telemetry) keys to canonical (dot-notation) keys
_ALT_KEY_MAP: dict[str, str] = {
    ATTR_ALT_LATITUDE: ATTR_POSITION_LATITUDE,
    ATTR_ALT_LONGITUDE: ATTR_POSITION_LONGITUDE,
    ATTR_ALT_SPEED: ATTR_POSITION_SPEED,
    ATTR_ALT_ALTITUDE: ATTR_POSITION_ALTITUDE,
    ATTR_ALT_DIRECTION: ATTR_POSITION_DIRECTION,
    ATTR_ALT_HDOP: ATTR_POSITION_HDOP,
    ATTR_ALT_SATELLITES: ATTR_POSITION_SATELLITES,
}


class FlespiCoordinator:
    """Coordinate MQTT subscription and data updates for a flespi device."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        self.hass = hass
        self.entry = entry
        self.dev_id: str = entry.data["dev_id"]
        self.topic: str = entry.data["topic"]
        self.data: dict[str, Any] = {}
        self._listeners: list[Callable[[], None]] = []
        self._unsubscribe: Callable[[], None] | None = None

    async def async_start(self) -> None:
        """Subscribe to the MQTT topic."""
        self._unsubscribe = await mqtt.async_subscribe(
            self.hass, self.topic, self._handle_message, 0
        )

    async def async_stop(self) -> None:
        """Unsubscribe from MQTT."""
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None

    @callback
    def _handle_message(self, msg: mqtt.models.ReceiveMessage) -> None:
        """Parse an incoming MQTT message and notify listeners."""
        try:
            payload = json.loads(msg.payload)
        except (ValueError, TypeError):
            _LOGGER.error("Error parsing JSON payload: %s", msg.payload)
            return

        if not isinstance(payload, dict):
            _LOGGER.error("Payload is not a JSON object: %s", msg.payload)
            return

        data = self._normalize(payload)

        if ATTR_POSITION_LATITUDE not in data or ATTR_POSITION_LONGITUDE not in data:
            _LOGGER.warning(
                "Skipping update for %s: missing latitude/longitude in %s",
                self.dev_id,
                msg.payload,
            )
            return

        self.data = data
        for listener_cb in self._listeners:
            listener_cb()

    @staticmethod
    def _normalize(payload: dict[str, Any]) -> dict[str, Any]:
        """Normalize telemetry keys to canonical dot-notation format."""
        data: dict[str, Any] = {}
        for key, value in payload.items():
            canonical = _ALT_KEY_MAP.get(key, key)
            data[canonical] = value
        return data

    @callback
    def async_add_listener(self, update_callback: Callable[[], None]) -> None:
        """Register a listener for data updates."""
        self._listeners.append(update_callback)
