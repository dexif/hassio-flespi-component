"""Coordinator for the flespi integration."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Awaitable, Callable

from homeassistant.components import mqtt
from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant, callback

from .api import FlespiApiError, FlespiRestClient
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
    ATTR_POSITION_SPEED,  # imports retained for _ALT_KEY_MAP
    CONF_AUTO_DISCOVERY,
    CONF_ENABLE_ALL_SENSORS,
    CONF_HOST,
    CONF_MODE,
    CONF_PORT,
    CONF_PROTOCOL,
    CONF_TOKEN,
    CONF_USE_TLS,
    DEFAULT_HOST,
    DEFAULT_PORT_TLS,
    DEFAULT_PROTOCOL,
    DEFAULT_STALE_THRESHOLD_S,
    DOMAIN,
    MODE_DIRECT,
)
from .entity import (
    LEGACY_SENSOR_SPECS,
    FlespiEntitySpec,
    build_sensor_specs,
)
from .pool import ConnectionKey, FlespiDirectClient, get_pool

_LOGGER = logging.getLogger(__name__)

_DEVICE_ID_RE = re.compile(r"flespi/(?:message|state)/gw/devices/(\d+)")

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
    """Coordinate MQTT subscription and data updates for a single flespi device.

    Creds come from the main ConfigEntry (shared across all devices that use the
    same connection); device-level data (dev_id, topic, auto_discovery) comes
    from the ConfigSubentry.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        main_entry: ConfigEntry,
        subentry: ConfigSubentry,
    ) -> None:
        """Initialize the coordinator."""
        self.hass = hass
        self.main_entry = main_entry
        self.subentry = subentry
        self.dev_id: str = subentry.data["dev_id"]
        self.topic: str = subentry.data["topic"]
        self.mode: str = main_entry.data[CONF_MODE]
        self.auto_discovery: bool = subentry.data.get(CONF_AUTO_DISCOVERY, False)
        self.enable_all_sensors: bool = subentry.data.get(CONF_ENABLE_ALL_SENSORS, False)
        self.stale_threshold_s: int = DEFAULT_STALE_THRESHOLD_S
        self.flespi_device_id: int | None = _parse_device_id(self.topic)
        self.data: dict[str, Any] = {}
        self.connected: bool | None = None
        self.sensor_specs: list[FlespiEntitySpec] = list(LEGACY_SENSOR_SPECS)
        self.binary_sensor_specs: list[FlespiEntitySpec] = []
        self._listeners: list[Callable[[], None]] = []
        self._teardown: Callable[[], Awaitable[None]] | None = None

    async def async_prepare(self) -> None:
        """Populate entity specs and seed initial data before platforms load."""
        if self.mode == MODE_DIRECT:
            await self._seed_from_rest()

    async def async_start(self) -> None:
        """Start the appropriate MQTT subscription based on mode."""
        if self.mode == MODE_DIRECT:
            self._teardown = await self._start_direct()
        else:
            self._teardown = await self._start_ha_mqtt()

    async def async_stop(self) -> None:
        """Tear down the active subscription."""
        if self._teardown is not None:
            await self._teardown()
            self._teardown = None

    async def _seed_from_rest(self) -> None:
        """Seed self.data from the REST telemetry snapshot.

        With auto-discovery on, also fetches param metadata and builds entity
        specs. Without it, only the snapshot is used so legacy sensors and the
        device tracker have values right away — live updates then flow in
        atomically via the device-message MQTT topic, which keeps lat/lon
        coherent on the map (per-parameter `state/.../telemetry/#` updates
        would arrive separately and cause a staircase).
        """
        if self.flespi_device_id is None:
            if self.auto_discovery:
                _LOGGER.warning(
                    "Can't parse flespi device_id from topic %r; "
                    "falling back to legacy sensors for %s",
                    self.topic,
                    self.dev_id,
                )
            return

        token = self.main_entry.data[CONF_TOKEN]
        client = FlespiRestClient(self.hass, token)
        try:
            if self.auto_discovery:
                telemetry, params_meta = await asyncio.gather(
                    client.get_device_telemetry(self.flespi_device_id),
                    client.get_message_parameters(),
                )
            else:
                telemetry = await client.get_device_telemetry(self.flespi_device_id)
                params_meta = {}
        except FlespiApiError as err:
            _LOGGER.error(
                "REST seed failed for %s; entities will populate from MQTT only: %s",
                self.dev_id,
                err,
            )
            return

        # Seed self.data so entities show current values immediately on startup.
        initial: dict[str, Any] = {}
        for key, snapshot in telemetry.items():
            if isinstance(snapshot, dict) and "value" in snapshot:
                initial[key] = snapshot["value"]
        if initial:
            self.data = {**self.data, **initial}

        if not self.auto_discovery:
            return

        sensor_specs, binary_specs = build_sensor_specs(
            telemetry,
            params_meta,
            self.stale_threshold_s,
            time.time(),
            enable_all=self.enable_all_sensors,
        )
        if not sensor_specs and not binary_specs:
            # Device has no fresh telemetry — keep legacy specs so the user
            # still sees speed/altitude/etc. once data starts flowing.
            _LOGGER.info(
                "No fresh telemetry for %s; keeping legacy sensors as fallback",
                self.dev_id,
            )
            return
        self.sensor_specs = sensor_specs
        self.binary_sensor_specs = binary_specs

    async def _start_ha_mqtt(self) -> Callable[[], Awaitable[None]]:
        """Subscribe via the Home Assistant MQTT integration."""
        unsub = await mqtt.async_subscribe(
            self.hass, self.topic, self._on_ha_mqtt_message, 0
        )

        async def teardown() -> None:
            unsub()

        return teardown

    async def _start_direct(self) -> Callable[[], Awaitable[None]]:
        """Subscribe via the shared flespi MQTT client pool.

        Multiple coordinators sharing the same (host, port, tls, protocol, token)
        end up on the same TCP/TLS session. The pool handles connect/disconnect
        and re-subscribes on reconnect; this method only registers topic-level
        callbacks and returns a teardown that unregisters them.
        """
        data = self.main_entry.data
        key: ConnectionKey = (
            data.get(CONF_HOST, DEFAULT_HOST),
            data.get(CONF_PORT, DEFAULT_PORT_TLS),
            data.get(CONF_USE_TLS, True),
            data.get(CONF_PROTOCOL, DEFAULT_PROTOCOL),
            data[CONF_TOKEN],
        )
        pool = get_pool(self.hass)
        pool_client: FlespiDirectClient = await pool.acquire(key)

        unsubs: list[Callable[[], None]] = []

        def _main_cb(topic: str, payload: bytes) -> None:
            self._process_payload(payload)

        unsubs.append(pool_client.subscribe(self.topic, _main_cb))

        if self.flespi_device_id is not None:
            # Live updates flow through the device-message topic above (whole
            # message, all params atomic). We don't subscribe to per-parameter
            # state/.../telemetry/# — those arrive as separate MQTT packets and
            # cause lat/lon updates to land out of sync (staircase on the map).
            # Initial values come from the REST snapshot in _seed_from_rest().
            connected_topic = (
                f"flespi/state/gw/devices/{self.flespi_device_id}/connected"
            )

            def _connected_cb(topic: str, payload: bytes) -> None:
                self._process_connected(payload)

            unsubs.append(pool_client.subscribe(connected_topic, _connected_cb))

        async def teardown() -> None:
            for unsub in unsubs:
                unsub()
            await pool.release(pool_client)

        return teardown

    @callback
    def _on_ha_mqtt_message(self, msg: mqtt.models.ReceiveMessage) -> None:
        """Forward HA-MQTT payload into the common processor."""
        self._process_payload(msg.payload)

    @callback
    def _process_payload(self, payload: bytes | str) -> None:
        """Parse payload, normalize keys, and notify listeners."""
        try:
            parsed = json.loads(payload)
        except (ValueError, TypeError):
            _LOGGER.error("Error parsing JSON payload: %s", payload)
            return

        if not isinstance(parsed, dict):
            _LOGGER.error("Payload is not a JSON object: %s", payload)
            return

        data = self._normalize(parsed)
        if not data:
            return

        # Merge partial updates: keys absent from this payload keep their prior
        # value. Lets auto-discovered non-GPS sensors refresh from telemetry-only
        # messages; device_tracker still handles missing lat/lon via .get().
        self.data = {**self.data, **data}
        self._notify()

    @callback
    def _process_connected(self, payload: bytes | str) -> None:
        """Handle a retained `connected` state-topic payload."""
        text = payload.decode("utf-8", errors="replace") if isinstance(payload, (bytes, bytearray)) else str(payload)
        text = text.strip().lower()
        if text in ("true", "1"):
            self.connected = True
        elif text in ("false", "0"):
            self.connected = False
        else:
            _LOGGER.debug("Unexpected connected payload for %s: %r", self.dev_id, text)
            return
        self._notify()

    def _notify(self) -> None:
        for listener_cb in self._listeners:
            listener_cb()

    @staticmethod
    def _normalize(payload: dict[str, Any]) -> dict[str, Any]:
        """Normalize telemetry keys to canonical dot-notation format."""
        return {_ALT_KEY_MAP.get(k, k): v for k, v in payload.items()}

    @callback
    def async_add_listener(self, update_callback: Callable[[], None]) -> None:
        """Register a listener for data updates."""
        self._listeners.append(update_callback)


def _parse_device_id(topic: str) -> int | None:
    """Extract the numeric flespi device id from an MQTT topic, if present."""
    match = _DEVICE_ID_RE.search(topic)
    return int(match.group(1)) if match else None
