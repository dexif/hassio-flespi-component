"""The mqtt_flespi_message integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, PLATFORMS
from .coordinator import FlespiCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the mqtt_flespi_message integration."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up mqtt_flespi_message from a config entry."""
    _migrate_legacy_entity(hass, entry.data["dev_id"])

    coordinator = FlespiCoordinator(hass, entry)
    await coordinator.async_start()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


def _migrate_legacy_entity(hass: HomeAssistant, dev_id: str) -> None:
    """Remove legacy device_tracker entity to prevent entity_id conflicts.

    The old component used async_see() which creates legacy entities without
    unique_id. The new TrackerEntity uses the same entity_id pattern
    (device_tracker.{dev_id}) but with a unique_id. Remove the old entry
    so the new one registers cleanly.
    """
    ent_reg = er.async_get(hass)
    entity_id = f"device_tracker.{dev_id}"
    existing = ent_reg.async_get(entity_id)
    if existing is not None and existing.unique_id != f"{DOMAIN}_{dev_id}":
        _LOGGER.info(
            "Removing legacy entity %s to complete migration", entity_id
        )
        ent_reg.async_remove(entity_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    coordinator: FlespiCoordinator = hass.data[DOMAIN][entry.entry_id]
    await coordinator.async_stop()
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
