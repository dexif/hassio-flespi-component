"""Sensor platform for mqtt_flespi_message."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    DEGREE,
    UnitOfElectricPotential,
    UnitOfLength,
    UnitOfSpeed,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    ATTR_BATTERY_VOLTAGE,
    ATTR_POSITION_ALTITUDE,
    ATTR_POSITION_DIRECTION,
    ATTR_POSITION_SATELLITES,
    ATTR_POSITION_SPEED,
    DOMAIN,
)
from .coordinator import FlespiCoordinator


@dataclass(frozen=True, kw_only=True)
class FlespiSensorEntityDescription(SensorEntityDescription):
    """Describe a flespi sensor."""

    flespi_key: str


SENSOR_DESCRIPTIONS: tuple[FlespiSensorEntityDescription, ...] = (
    FlespiSensorEntityDescription(
        key="speed",
        translation_key="speed",
        flespi_key=ATTR_POSITION_SPEED,
        native_unit_of_measurement=UnitOfSpeed.KILOMETERS_PER_HOUR,
        device_class=SensorDeviceClass.SPEED,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    FlespiSensorEntityDescription(
        key="altitude",
        translation_key="altitude",
        flespi_key=ATTR_POSITION_ALTITUDE,
        native_unit_of_measurement=UnitOfLength.METERS,
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    FlespiSensorEntityDescription(
        key="direction",
        translation_key="direction",
        flespi_key=ATTR_POSITION_DIRECTION,
        native_unit_of_measurement=DEGREE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    FlespiSensorEntityDescription(
        key="satellites",
        translation_key="satellites",
        flespi_key=ATTR_POSITION_SATELLITES,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    FlespiSensorEntityDescription(
        key="battery_voltage",
        translation_key="battery_voltage",
        flespi_key=ATTR_BATTERY_VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.MILLIVOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up flespi sensors from a config entry."""
    coordinator: FlespiCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        FlespiSensor(coordinator, description)
        for description in SENSOR_DESCRIPTIONS
    )


class FlespiSensor(SensorEntity):
    """Represent a flespi telemetry sensor."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    entity_description: FlespiSensorEntityDescription

    def __init__(
        self,
        coordinator: FlespiCoordinator,
        description: FlespiSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        self._coordinator = coordinator
        self.entity_description = description
        self._attr_unique_id = f"{DOMAIN}_{coordinator.dev_id}_{description.key}"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._coordinator.dev_id)},
            name=self._coordinator.dev_id,
            manufacturer="Flespi",
        )

    @property
    def native_value(self) -> float | None:
        """Return the sensor value."""
        return self._coordinator.data.get(self.entity_description.flespi_key)

    async def async_added_to_hass(self) -> None:
        """Register for data updates when added to hass."""
        self._coordinator.async_add_listener(self._handle_update)

    @callback
    def _handle_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()
