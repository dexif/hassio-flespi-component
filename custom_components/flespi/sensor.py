"""Sensor platform for the flespi integration (spec-driven, auto-discovery aware)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import FlespiCoordinator
from .customer_coordinator import FlespiCustomerCoordinator
from .customer_sensor import FlespiCustomerSensor, build_customer_sensors, is_default_enabled
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

    # Customer-counter sensors (only present when a customer subentry exists
    # and the token is Master-level — see customer_coordinator.async_start).
    customer_map: dict[str, FlespiCustomerCoordinator] = (
        hass.data.get(DOMAIN, {}).get("_customer", {}).get(entry.entry_id, {})
    )
    for subentry_id, customer_coord in customer_map.items():
        customer_sensors = build_customer_sensors(customer_coord)
        if customer_sensors:
            async_add_entities(customer_sensors, config_subentry_id=subentry_id)

        @callback
        def _on_new_counter(
            counter_key: str,
            _coord: FlespiCustomerCoordinator = customer_coord,
            _sub_id: str = subentry_id,
        ) -> None:
            async_add_entities(
                [
                    FlespiCustomerSensor(
                        _coord,
                        counter_key,
                        enabled_by_default=is_default_enabled(counter_key),
                    )
                ],
                config_subentry_id=_sub_id,
            )

        customer_coord.on_new_key(_on_new_counter)


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
