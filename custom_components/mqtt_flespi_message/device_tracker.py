"""Device tracker platform for mqtt_flespi_message."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.components import mqtt
from homeassistant.components.device_tracker import (
    PLATFORM_SCHEMA as PARENT_PLATFORM_SCHEMA,
    SourceType,
    TrackerEntity,
)
from homeassistant.components.mqtt import CONF_QOS
from homeassistant.config_entries import ConfigEntry, SOURCE_IMPORT
from homeassistant.const import CONF_DEVICES
from homeassistant.core import HomeAssistant, callback
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.issue_registry import IssueSeverity, async_create_issue
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from .const import (
    ATTR_BATTERY_LEVEL,
    ATTR_POSITION_ALTITUDE,
    ATTR_POSITION_DIRECTION,
    ATTR_POSITION_HDOP,
    ATTR_POSITION_LATITUDE,
    ATTR_POSITION_LONGITUDE,
    ATTR_POSITION_SATELLITES,
    ATTR_POSITION_SPEED,
    DOMAIN,
)
from .coordinator import FlespiCoordinator

_LOGGER = logging.getLogger(__name__)

# Legacy YAML platform schema (kept for backwards-compatible YAML import)
PLATFORM_SCHEMA = PARENT_PLATFORM_SCHEMA.extend(mqtt.config.SCHEMA_BASE).extend(
    {vol.Required(CONF_DEVICES): {cv.string: mqtt.valid_subscribe_topic}}
)


async def async_setup_scanner(
    hass: HomeAssistant,
    config: ConfigType,
    async_see: Any,
    discovery_info: DiscoveryInfoType | None = None,
) -> bool:
    """Import YAML configuration into config entries."""
    devices = config[CONF_DEVICES]
    for dev_id, topic in devices.items():
        hass.async_create_task(
            hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": SOURCE_IMPORT},
                data={"dev_id": dev_id, "topic": topic},
            )
        )

    async_create_issue(
        hass,
        DOMAIN,
        "yaml_deprecated",
        is_fixable=False,
        severity=IssueSeverity.WARNING,
        translation_key="yaml_deprecated",
    )

    return True


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up flespi device tracker from a config entry."""
    coordinator: FlespiCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([FlespiDeviceTracker(coordinator)])


class FlespiDeviceTracker(TrackerEntity):
    """Represent a flespi GPS tracker."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_should_poll = False

    def __init__(self, coordinator: FlespiCoordinator) -> None:
        """Initialize the device tracker."""
        self._coordinator = coordinator
        self._attr_unique_id = f"{DOMAIN}_{coordinator.dev_id}"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._coordinator.dev_id)},
            name=self._coordinator.dev_id,
            manufacturer="Flespi",
        )

    @property
    def source_type(self) -> SourceType:
        """Return the source type."""
        return SourceType.GPS

    @property
    def latitude(self) -> float | None:
        """Return latitude value of the device."""
        return self._coordinator.data.get(ATTR_POSITION_LATITUDE)

    @property
    def longitude(self) -> float | None:
        """Return longitude value of the device."""
        return self._coordinator.data.get(ATTR_POSITION_LONGITUDE)

    @property
    def location_accuracy(self) -> int:
        """Return the location accuracy of the device."""
        return self._coordinator.data.get(ATTR_POSITION_HDOP, 0)

    @property
    def battery_level(self) -> int | None:
        """Return the battery level of the device."""
        level = self._coordinator.data.get(ATTR_BATTERY_LEVEL)
        if level is not None:
            try:
                return int(float(level))
            except (ValueError, TypeError):
                return None
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        attrs: dict[str, Any] = {}
        data = self._coordinator.data
        if ATTR_POSITION_SPEED in data:
            attrs["speed"] = data[ATTR_POSITION_SPEED]
        if ATTR_POSITION_ALTITUDE in data:
            attrs["altitude"] = data[ATTR_POSITION_ALTITUDE]
        if ATTR_POSITION_DIRECTION in data:
            attrs["direction"] = data[ATTR_POSITION_DIRECTION]
        if ATTR_POSITION_SATELLITES in data:
            attrs["satellites"] = data[ATTR_POSITION_SATELLITES]
        return attrs

    async def async_added_to_hass(self) -> None:
        """Register for data updates when added to hass."""
        self._coordinator.async_add_listener(self._handle_update)

    @callback
    def _handle_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()
