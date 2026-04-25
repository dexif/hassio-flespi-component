"""Binary sensor platform: device-online status plus auto-discovered booleans."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, MODE_DIRECT
from .coordinator import FlespiCoordinator
from .entity import FlespiEntity, FlespiEntitySpec


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    per_subentry: dict[str, FlespiCoordinator] = hass.data[DOMAIN][entry.entry_id]
    for subentry_id, coordinator in per_subentry.items():
        entities: list[BinarySensorEntity] = []
        if coordinator.mode == MODE_DIRECT and coordinator.flespi_device_id is not None:
            entities.append(FlespiOnlineBinarySensor(coordinator))
        entities.extend(
            FlespiBinarySensor(coordinator, spec)
            for spec in coordinator.binary_sensor_specs
        )
        if entities:
            async_add_entities(entities, config_subentry_id=subentry_id)


class FlespiOnlineBinarySensor(FlespiEntity, BinarySensorEntity):
    """Reflects the retained `flespi/state/gw/devices/{id}/connected` topic."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_translation_key = "online"

    def __init__(self, coordinator: FlespiCoordinator) -> None:
        super().__init__(coordinator, "online")

    @property
    def is_on(self) -> bool | None:
        return self._coordinator.connected

    @property
    def available(self) -> bool:
        return self._coordinator.connected is not None


class FlespiBinarySensor(FlespiEntity, BinarySensorEntity):
    """Auto-discovered boolean telemetry parameter."""

    def __init__(
        self, coordinator: FlespiCoordinator, spec: FlespiEntitySpec
    ) -> None:
        super().__init__(coordinator, spec.unique_suffix)
        self._flespi_key = spec.flespi_key
        if spec.translation_key is not None:
            self._attr_translation_key = spec.translation_key
        elif spec.name is not None:
            self._attr_name = spec.name
        if spec.device_class is not None:
            self._attr_device_class = spec.device_class
        if spec.icon is not None:
            self._attr_icon = spec.icon
        self._attr_entity_registry_enabled_default = spec.enabled_by_default

    @property
    def is_on(self) -> bool | None:
        value = self._coordinator.data.get(self._flespi_key)
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        return None
