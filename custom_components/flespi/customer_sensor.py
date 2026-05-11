"""Customer-counter sensors for the flespi integration (Master-token-only)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.util import slugify

from .const import DOMAIN

# Counters enabled by default in the entity registry.  Everything else is
# registered but disabled -- users toggle what they need from the entity list.
# Keys match the MQTT topic suffix after
# ``flespi/state/platform/customer/counters/``.
DEFAULT_ENABLED_COUNTERS: frozenset[str] = frozenset({
    "api/calls",
    "api/traffic",
    "mqtt/sessions",
    "mqtt/messages",
})


def is_default_enabled(counter_key: str) -> bool:
    """Check if a counter should be enabled by default."""
    return counter_key in DEFAULT_ENABLED_COUNTERS or counter_key.endswith("/count")

_UPPER_WORDS: dict[str, str] = {
    "api": "API",
    "mqtt": "MQTT",
    "cdns": "CDN",
    "cdn": "CDN",
    "ai": "AI",
    "sms": "SMS",
    "udp": "UDP",
    "rest": "REST",
}


def _counter_display_name(key: str) -> str:
    """Derive a sensor name from the last two segments of the counter topic."""
    segments = key.split("/")
    name_parts = segments[-2:] if len(segments) >= 2 else segments
    words: list[str] = []
    for part in name_parts:
        for word in part.split("_"):
            words.append(_UPPER_WORDS.get(word, word.capitalize()))
    return " ".join(words)


def build_customer_sensors(
    coordinator: Any,
) -> list[FlespiCustomerSensor]:
    """Build sensor entities for every counter key discovered so far."""
    return [
        FlespiCustomerSensor(
            coordinator,
            counter_key,
            enabled_by_default=is_default_enabled(counter_key),
        )
        for counter_key in sorted(coordinator.data)
    ]


class FlespiCustomerSensor(SensorEntity):
    """A sensor reflecting a single flespi platform counter."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        coordinator: Any,
        counter_key: str,
        *,
        enabled_by_default: bool = False,
    ) -> None:
        self._coordinator = coordinator
        self._counter_key = counter_key
        self._is_limit = counter_key.endswith("_limit") or counter_key.endswith("/limit")
        self._attr_unique_id = (
            f"{DOMAIN}_customer_{coordinator.cid}_{slugify(counter_key)}"
        )
        self._attr_name = _counter_display_name(counter_key)
        self._attr_entity_registry_enabled_default = enabled_by_default
        if not self._is_limit:
            self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, f"customer_{self._coordinator.cid}")},
            name="Flespi account",
            manufacturer="Flespi",
        )

    @property
    def native_value(self) -> Any:
        value = self._coordinator.data.get(self._counter_key)
        if self._is_limit and value == -1:
            return None
        return value

    @property
    def available(self) -> bool:
        value = self._coordinator.data.get(self._counter_key)
        if value is None:
            return False
        if self._is_limit and value == -1:
            return False
        return True

    async def async_added_to_hass(self) -> None:
        self._coordinator.async_add_listener(self._handle_update)

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()
