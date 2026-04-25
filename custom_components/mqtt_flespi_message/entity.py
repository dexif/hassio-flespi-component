"""Shared entity helpers: base mixin, spec dataclass, and inference helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import (
    DEGREE,
    UnitOfElectricPotential,
    UnitOfLength,
    UnitOfSpeed,
)
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.util import slugify

from .const import DOMAIN

# Legacy unique_id suffixes preserved for the 5 hardcoded sensors shipped in
# 0.1.x–0.3.x. Keeping them means existing entity_ids, recorder history and
# user automations all survive the switch to auto-discovery.
LEGACY_SUFFIX_MAP: dict[str, str] = {
    "position.speed": "speed",
    "position.altitude": "altitude",
    "position.direction": "direction",
    "position.satellites": "satellites",
    "battery.voltage": "battery_voltage",
}

# Translation keys already present in strings.json for the legacy 5 sensors.
LEGACY_TRANSLATION_KEYS: dict[str, str] = {
    "position.speed": "speed",
    "position.altitude": "altitude",
    "position.direction": "direction",
    "position.satellites": "satellites",
    "battery.voltage": "battery_voltage",
}


def unique_id_suffix(flespi_key: str) -> str:
    """Return a stable unique_id suffix for a flespi parameter key."""
    return LEGACY_SUFFIX_MAP.get(flespi_key) or slugify(flespi_key)


def _derive_name(flespi_key: str) -> str:
    """Derive a human-ish display name from a canonical key (fallback)."""
    return flespi_key.replace(".", " ").replace("_", " ").capitalize()


@dataclass(frozen=True, kw_only=True)
class FlespiEntitySpec:
    """Declarative description of a dynamically-created flespi entity."""

    flespi_key: str
    unique_suffix: str
    name: str | None = None
    translation_key: str | None = None
    device_class: Any = None
    state_class: Any = None
    unit: str | None = None
    icon: str | None = None


# Units flespi hands out that are compatible with a given HA sensor device_class.
# Device_class is only assigned when units match — HA otherwise rejects it.
_SENSOR_CLASS_UNITS: dict[SensorDeviceClass, set[str]] = {
    SensorDeviceClass.VOLTAGE: {"mV", "V", "kV"},
    SensorDeviceClass.TEMPERATURE: {"°C", "°F", "C", "F"},
    SensorDeviceClass.SPEED: {"km/h", "mph", "m/s", "kn"},
    SensorDeviceClass.DISTANCE: {"m", "km", "cm", "ft", "mi", "yd"},
    SensorDeviceClass.BATTERY: {"%"},
    SensorDeviceClass.HUMIDITY: {"%"},
    SensorDeviceClass.PRESSURE: {"Pa", "hPa", "kPa", "bar", "psi"},
    SensorDeviceClass.CURRENT: {"A", "mA"},
    SensorDeviceClass.POWER: {"W", "kW"},
    SensorDeviceClass.ENERGY: {"Wh", "kWh"},
    SensorDeviceClass.FREQUENCY: {"Hz", "kHz", "MHz"},
}


def _sensor_class_candidate(key: str) -> SensorDeviceClass | None:
    k = key.lower()
    if k == "battery.level":
        return SensorDeviceClass.BATTERY
    if k == "position.speed":
        return SensorDeviceClass.SPEED
    if k == "position.altitude":
        return SensorDeviceClass.DISTANCE
    if "voltage" in k:
        return SensorDeviceClass.VOLTAGE
    if "temperature" in k:
        return SensorDeviceClass.TEMPERATURE
    if "humidity" in k:
        return SensorDeviceClass.HUMIDITY
    if "pressure" in k:
        return SensorDeviceClass.PRESSURE
    if k.endswith(".current"):
        return SensorDeviceClass.CURRENT
    if "frequency" in k:
        return SensorDeviceClass.FREQUENCY
    return None


def infer_sensor_device_class(key: str, units: str | None) -> SensorDeviceClass | None:
    """Pick a SensorDeviceClass only when both the key and the unit support it."""
    candidate = _sensor_class_candidate(key)
    if candidate is None:
        return None
    compatible = _SENSOR_CLASS_UNITS.get(candidate, set())
    if units and units in compatible:
        return candidate
    if not units and candidate in (SensorDeviceClass.BATTERY,):
        return candidate  # tolerate missing unit for %-only classes
    return None


def infer_binary_device_class(key: str) -> BinarySensorDeviceClass | None:
    """Heuristic BinarySensorDeviceClass for common flespi boolean params."""
    k = key.lower()
    if "ignition" in k:
        return BinarySensorDeviceClass.RUNNING
    if "movement" in k or "motion" in k:
        return BinarySensorDeviceClass.MOVING
    if "door" in k:
        return BinarySensorDeviceClass.DOOR
    if "window" in k:
        return BinarySensorDeviceClass.WINDOW
    if "lock" in k:
        return BinarySensorDeviceClass.LOCK
    if "charg" in k:
        return BinarySensorDeviceClass.BATTERY_CHARGING
    if "plug" in k:
        return BinarySensorDeviceClass.PLUG
    if "power" in k:
        return BinarySensorDeviceClass.POWER
    if "sos" in k or "alarm" in k:
        return BinarySensorDeviceClass.SAFETY
    return None


class FlespiEntity:
    """Shared behavior for every flespi-backed entity attached to a coordinator."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, coordinator: Any, unique_suffix: str) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"{DOMAIN}_{coordinator.dev_id}_{unique_suffix}"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._coordinator.dev_id)},
            name=self._coordinator.dev_id,
            manufacturer="Flespi",
        )

    async def async_added_to_hass(self) -> None:
        self._coordinator.async_add_listener(self._handle_update)

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()


# Hardcoded specs matching the 0.1.x–0.3.x sensor set. Used when auto-discovery
# is off (including all HA-MQTT entries and legacy direct-mode entries).
LEGACY_SENSOR_SPECS: list[FlespiEntitySpec] = [
    FlespiEntitySpec(
        flespi_key="position.speed",
        unique_suffix="speed",
        translation_key="speed",
        device_class=SensorDeviceClass.SPEED,
        state_class=SensorStateClass.MEASUREMENT,
        unit=UnitOfSpeed.KILOMETERS_PER_HOUR,
    ),
    FlespiEntitySpec(
        flespi_key="position.altitude",
        unique_suffix="altitude",
        translation_key="altitude",
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.MEASUREMENT,
        unit=UnitOfLength.METERS,
    ),
    FlespiEntitySpec(
        flespi_key="position.direction",
        unique_suffix="direction",
        translation_key="direction",
        state_class=SensorStateClass.MEASUREMENT,
        unit=DEGREE,
    ),
    FlespiEntitySpec(
        flespi_key="position.satellites",
        unique_suffix="satellites",
        translation_key="satellites",
        state_class=SensorStateClass.MEASUREMENT,
    ),
    FlespiEntitySpec(
        flespi_key="battery.voltage",
        unique_suffix="battery_voltage",
        translation_key="battery_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        unit=UnitOfElectricPotential.MILLIVOLT,
    ),
]


def build_sensor_specs(
    telemetry: dict[str, dict[str, Any]],
    params_meta: dict[str, dict[str, Any]],
    stale_threshold_s: int,
    now_ts: float,
) -> tuple[list[FlespiEntitySpec], list[FlespiEntitySpec]]:
    """Split fresh telemetry params into (sensor_specs, binary_sensor_specs).

    Stale params (value ts older than stale_threshold_s) are dropped entirely
    to avoid polluting HA with year-old sensors. Live updates for them are
    still processed if they arrive; a future options flow could instead keep
    them as `unavailable`.
    """
    sensor_specs: list[FlespiEntitySpec] = []
    binary_specs: list[FlespiEntitySpec] = []

    for key, entry in telemetry.items():
        if not isinstance(entry, dict):
            continue
        value = entry.get("value")
        ts = entry.get("ts")
        if ts is None or (now_ts - float(ts)) > stale_threshold_s:
            continue

        meta = params_meta.get(key, {})
        # flespi /gw/message-parameters fields: name, type, unit (singular!), info.
        unit = meta.get("unit") or None
        description = meta.get("info") or None
        param_type = (meta.get("type") or "").lower()

        is_bool = isinstance(value, bool) or param_type == "boolean"

        if is_bool:
            binary_specs.append(
                FlespiEntitySpec(
                    flespi_key=key,
                    unique_suffix=unique_id_suffix(key),
                    name=None if LEGACY_TRANSLATION_KEYS.get(key) else (description or _derive_name(key)),
                    translation_key=LEGACY_TRANSLATION_KEYS.get(key),
                    device_class=infer_binary_device_class(key),
                )
            )
        elif isinstance(value, (int, float)) or param_type in ("number", "integer", "float"):
            sensor_specs.append(
                FlespiEntitySpec(
                    flespi_key=key,
                    unique_suffix=unique_id_suffix(key),
                    name=None if LEGACY_TRANSLATION_KEYS.get(key) else (description or _derive_name(key)),
                    translation_key=LEGACY_TRANSLATION_KEYS.get(key),
                    device_class=infer_sensor_device_class(key, unit),
                    state_class=SensorStateClass.MEASUREMENT,
                    unit=unit,
                )
            )
        # Strings and complex types are not exposed as entities.

    return sensor_specs, binary_specs
