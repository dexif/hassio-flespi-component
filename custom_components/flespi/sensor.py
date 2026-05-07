"""Sensor platform for the flespi integration (spec-driven, auto-discovery aware)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import FlespiCoordinator
from .entity import FlespiEntity, FlespiEntitySpec


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    per_subentry: dict[str, FlespiCoordinator] = hass.data[DOMAIN][entry.entry_id]
    for subentry_id, coordinator in per_subentry.items():
        entities = [
            FlespiSensor(coordinator, spec) for spec in coordinator.sensor_specs
        ]
        if entities:
            async_add_entities(entities, config_subentry_id=subentry_id)


class FlespiSensor(FlespiEntity, SensorEntity):
    """A sensor fed by a single flespi parameter key."""

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
        if spec.state_class is not None:
            self._attr_state_class = spec.state_class
        if spec.unit is not None:
            self._attr_native_unit_of_measurement = spec.unit
        if spec.icon is not None:
            self._attr_icon = spec.icon
        self._attr_entity_registry_enabled_default = spec.enabled_by_default

    @property
    def native_value(self) -> Any:
        return self._coordinator.data.get(self._flespi_key)
