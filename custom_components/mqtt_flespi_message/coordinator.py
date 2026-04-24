"""Coordinator for the mqtt_flespi_message integration."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Awaitable, Callable

import paho.mqtt.client as mqtt_client

from homeassistant.components import mqtt
from homeassistant.config_entries import ConfigEntry
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
    PROTOCOL_V31,
    PROTOCOL_V311,
    PROTOCOL_V5,
)
from .entity import (
    LEGACY_SENSOR_SPECS,
    FlespiEntitySpec,
    build_sensor_specs,
)

_PROTOCOL_MAP: dict[str, int] = {
    PROTOCOL_V31: mqtt_client.MQTTv31,
    PROTOCOL_V311: mqtt_client.MQTTv311,
    PROTOCOL_V5: mqtt_client.MQTTv5,
}

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


def build_direct_client(
    client_id: str,
    token: str,
    use_tls: bool,
    protocol: str = DEFAULT_PROTOCOL,
) -> mqtt_client.Client:
    """Create a paho MQTT client configured for flespi direct mode."""
    paho_protocol = _PROTOCOL_MAP[protocol]
    kwargs: dict[str, Any] = {
        "callback_api_version": mqtt_client.CallbackAPIVersion.VERSION2,
        "client_id": client_id,
        "protocol": paho_protocol,
    }
    # clean_session is only accepted for MQTTv3; MQTTv5 uses clean_start on connect.
    if paho_protocol != mqtt_client.MQTTv5:
        kwargs["clean_session"] = True
    client = mqtt_client.Client(**kwargs)
    client.username_pw_set(token, "")
    if use_tls:
        client.tls_set()
    return client


class FlespiCoordinator:
    """Coordinate MQTT subscription and data updates for a flespi device."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        self.hass = hass
        self.entry = entry
        self.dev_id: str = entry.data["dev_id"]
        self.topic: str = entry.data["topic"]
        self.mode: str = entry.data[CONF_MODE]
        self.auto_discovery: bool = entry.data.get(CONF_AUTO_DISCOVERY, False)
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
        if self.mode == MODE_DIRECT and self.auto_discovery:
            await self._discover_from_rest()

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

    async def _discover_from_rest(self) -> None:
        """Fetch telemetry snapshot + param metadata, build entity specs."""
        if self.flespi_device_id is None:
            _LOGGER.warning(
                "Can't parse flespi device_id from topic %r; "
                "falling back to legacy sensors for %s",
                self.topic,
                self.dev_id,
            )
            return

        token = self.entry.data[CONF_TOKEN]
        client = FlespiRestClient(self.hass, token)
        try:
            telemetry, params_meta = await asyncio.gather(
                client.get_device_telemetry(self.flespi_device_id),
                client.get_message_parameters(),
            )
        except FlespiApiError as err:
            _LOGGER.error(
                "Auto-discovery REST failed for %s; keeping legacy sensors: %s",
                self.dev_id,
                err,
            )
            return

        sensor_specs, binary_specs = build_sensor_specs(
            telemetry,
            params_meta,
            self.stale_threshold_s,
            time.time(),
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

        # Seed self.data so entities show current values immediately on startup.
        initial: dict[str, Any] = {}
        for key, entry in telemetry.items():
            if isinstance(entry, dict) and "value" in entry:
                initial[key] = entry["value"]
        if initial:
            self.data = initial

    async def _start_ha_mqtt(self) -> Callable[[], Awaitable[None]]:
        """Subscribe via the Home Assistant MQTT integration."""
        unsub = await mqtt.async_subscribe(
            self.hass, self.topic, self._on_ha_mqtt_message, 0
        )

        async def teardown() -> None:
            unsub()

        return teardown

    async def _start_direct(self) -> Callable[[], Awaitable[None]]:
        """Subscribe via a dedicated paho client connected to flespi."""
        data = self.entry.data
        token: str = data[CONF_TOKEN]
        host: str = data.get(CONF_HOST, DEFAULT_HOST)
        port: int = data.get(CONF_PORT, DEFAULT_PORT_TLS)
        use_tls: bool = data.get(CONF_USE_TLS, True)
        protocol: str = data.get(CONF_PROTOCOL, DEFAULT_PROTOCOL)

        client_id = f"ha-{DOMAIN}-{self.dev_id}"
        client = build_direct_client(client_id, token, use_tls, protocol)

        subscriptions: list[tuple[str, int]] = [(self.topic, 0)]
        connected_topic: str | None = None
        telemetry_state_prefix: str | None = None
        if self.flespi_device_id is not None:
            connected_topic = (
                f"flespi/state/gw/devices/{self.flespi_device_id}/connected"
            )
            telemetry_state_prefix = (
                f"flespi/state/gw/devices/{self.flespi_device_id}/telemetry/"
            )
            subscriptions.append((connected_topic, 0))
            subscriptions.append((f"{telemetry_state_prefix}#", 0))

        def _on_connect(c, userdata, flags, reason_code, properties) -> None:
            if reason_code.is_failure:
                _LOGGER.error(
                    "Flespi MQTT connect failed for %s: %s",
                    self.dev_id,
                    reason_code,
                )
                return
            c.subscribe(subscriptions)

        def _on_message(c, userdata, message) -> None:
            topic = message.topic
            if connected_topic is not None and topic == connected_topic:
                self.hass.loop.call_soon_threadsafe(
                    self._process_connected, message.payload
                )
            elif (
                telemetry_state_prefix is not None
                and topic.startswith(telemetry_state_prefix)
            ):
                key = topic[len(telemetry_state_prefix):]
                self.hass.loop.call_soon_threadsafe(
                    self._process_state_param, key, message.payload
                )
            else:
                self.hass.loop.call_soon_threadsafe(
                    self._process_payload, message.payload
                )

        def _on_disconnect(c, userdata, flags, reason_code, properties) -> None:
            if reason_code != 0:
                _LOGGER.warning(
                    "Flespi MQTT disconnected from %s: %s", host, reason_code
                )

        client.on_connect = _on_connect
        client.on_message = _on_message
        client.on_disconnect = _on_disconnect
        client.reconnect_delay_set(min_delay=1, max_delay=60)
        client.connect_async(host, port, keepalive=60)
        client.loop_start()

        async def teardown() -> None:
            def _stop() -> None:
                client.disconnect()
                client.loop_stop()

            await self.hass.async_add_executor_job(_stop)

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
    def _process_state_param(self, key: str, payload: bytes | str) -> None:
        """Apply a single retained telemetry-state update to self.data."""
        try:
            value = json.loads(payload)
        except (ValueError, TypeError):
            value = (
                payload.decode("utf-8", errors="replace")
                if isinstance(payload, (bytes, bytearray))
                else str(payload)
            )
        self.data[key] = value
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
