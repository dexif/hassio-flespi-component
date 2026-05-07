"""Device tracker platform for the flespi integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

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


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up a flespi device tracker for every device subentry."""
    per_subentry: dict[str, FlespiCoordinator] = hass.data[DOMAIN][entry.entry_id]
    for subentry_id, coordinator in per_subentry.items():
        async_add_entities(
            [FlespiDeviceTracker(coordinator)],
            config_subentry_id=subentry_id,
        )


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
