"""The flespi integration."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from types import MappingProxyType
from typing import Any

from homeassistant.config_entries import (
    SOURCE_IMPORT,
    ConfigEntry,
    ConfigEntryDisabler,
    ConfigSubentry,
    ConfigSubentryData,
    ConfigSubentryDataWithId,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_AUTO_DISCOVERY,
    CONF_DEV_ID,
    CONF_HOST,
    CONF_MODE,
    CONF_PORT,
    CONF_PROTOCOL,
    CONF_TOKEN,
    CONF_TOPIC,
    CONF_USE_TLS,
    DEFAULT_HOST,
    DEFAULT_PORT_TLS,
    DEFAULT_PROTOCOL,
    DOMAIN,
    MODE_DIRECT,
    MODE_HA_MQTT,
    PLATFORMS,
    SUBENTRY_TYPE_DEVICE,
)
from .coordinator import FlespiCoordinator

_LOGGER = logging.getLogger(__name__)

# The previous domain. The cross-domain migrator below picks up any orphan
# entries still attached to it (users who upgraded directly from a version
# without the 0.4.5 shim, or where the shim didn't get a chance to run).
OLD_DOMAIN = "mqtt_flespi_message"


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the integration.

    Runs the v<3 → v3 migration globally before any entry loads (no-op on a
    fresh flespi-domain install, since entries start at v3 from day one).

    The cross-domain migration (mqtt_flespi_message → flespi) intentionally
    does NOT run here. It would deadlock: the migration's final step calls
    `async_set_disabled_by(None)` on the freshly-added flespi entry, which
    triggers `async_setup_component("flespi")`, which would try to acquire
    the flespi setup lock that this very call is already holding. That
    migration lives in `config_flow.async_step_migrate_from_old` instead —
    by the time the flow runs, this `async_setup` has completed and the
    lock is released.
    """
    await _migrate_legacy_entries(hass)
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """No-op at the per-entry level.

    All migration work happens globally in `async_setup` because we need to
    group old per-device entries across the whole domain. Returning True here
    lets HA proceed to set up the entry normally; if it's still v<3 at this
    point, the global migrator already logged and refused to convert it
    (leaving the entry valid-but-untouched so a future run can retry).
    """
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a connection main entry and all its device subentries."""
    if entry.version < 3:
        _LOGGER.warning(
            "Entry %s is still on version %d; waiting for migration on next restart",
            entry.entry_id,
            entry.version,
        )
        return False

    domain_data = hass.data.setdefault(DOMAIN, {})
    per_subentry: dict[str, FlespiCoordinator] = {}
    domain_data[entry.entry_id] = per_subentry

    # Build a coordinator per subentry up-front so REST discovery can run in
    # parallel across devices. A serial loop means N devices × 15s REST timeout
    # in the worst case, which trips HA's "setup taking over 10 seconds" warning.
    device_subentries = [
        s for s in entry.subentries.values() if s.subentry_type == SUBENTRY_TYPE_DEVICE
    ]
    coords: list[tuple[str, FlespiCoordinator, str]] = []
    for subentry in device_subentries:
        _migrate_legacy_tracker_entity(hass, subentry.data[CONF_DEV_ID])
        dev_id = subentry.data.get(CONF_DEV_ID, subentry.subentry_id)
        coords.append((subentry.subentry_id, FlespiCoordinator(hass, entry, subentry), dev_id))

    # Parallel REST discovery. Exceptions are captured and logged per coordinator
    # so a single failure doesn't abort the entire entry setup.
    prepare_results = await asyncio.gather(
        *(coord.async_prepare() for _, coord, _ in coords),
        return_exceptions=True,
    )
    for (_, _, dev_id), result in zip(coords, prepare_results):
        if isinstance(result, Exception):
            _LOGGER.exception(
                "async_prepare failed for device '%s'", dev_id, exc_info=result
            )

    # Start MQTT subscriptions serially — they all go through the pool which
    # serializes connection setup on its lock, so gathering here gives no win
    # and complicates teardown on partial failure.
    for subentry_id, coord, dev_id in coords:
        try:
            await coord.async_start()
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Failed to start MQTT for device '%s'", dev_id)
            continue
        per_subentry[subentry_id] = coord

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the main entry: stop every subentry's coordinator + unload platforms."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        per_subentry: dict[str, FlespiCoordinator] = (
            hass.data.get(DOMAIN, {}).pop(entry.entry_id, {})
        )
        for coord in per_subentry.values():
            try:
                await coord.async_stop()
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Error stopping coordinator during unload")
    return unload_ok


def _migrate_legacy_tracker_entity(hass: HomeAssistant, dev_id: str) -> None:
    """Remove the old no-unique_id device_tracker entity from pre-config-flow days."""
    ent_reg = er.async_get(hass)
    entity_id = f"device_tracker.{dev_id}"
    existing = ent_reg.async_get(entity_id)
    if existing is not None and existing.unique_id != f"{DOMAIN}_{dev_id}":
        _LOGGER.info(
            "Removing legacy entity %s to complete migration", entity_id
        )
        ent_reg.async_remove(entity_id)


# =============================================================================
# v<3 → v3 migration (global, runs in async_setup before any entry loads)
# =============================================================================


def _conn_key(entry: ConfigEntry) -> tuple:
    """Stable tuple identifying the connection for grouping purposes."""
    mode = entry.data.get(CONF_MODE, MODE_HA_MQTT)
    if mode == MODE_HA_MQTT:
        return ("ha_mqtt",)
    return (
        "direct",
        entry.data.get(CONF_HOST, DEFAULT_HOST),
        entry.data.get(CONF_PORT, DEFAULT_PORT_TLS),
        bool(entry.data.get(CONF_USE_TLS, True)),
        entry.data.get(CONF_PROTOCOL, DEFAULT_PROTOCOL),
        entry.data.get(CONF_TOKEN, ""),
    )


def _main_unique_id_for_key(key: tuple) -> str:
    if key[0] == "ha_mqtt":
        return "ha_mqtt"
    _, host, port, use_tls, protocol, token = key
    raw = f"{host}|{port}|{int(use_tls)}|{protocol}|{token}"
    return f"direct:{hashlib.sha1(raw.encode()).hexdigest()[:12]}"


def _main_data_for_key(key: tuple) -> dict[str, Any]:
    if key[0] == "ha_mqtt":
        return {CONF_MODE: MODE_HA_MQTT}
    _, host, port, use_tls, protocol, token = key
    return {
        CONF_MODE: MODE_DIRECT,
        CONF_TOKEN: token,
        CONF_HOST: host,
        CONF_PORT: port,
        CONF_USE_TLS: use_tls,
        CONF_PROTOCOL: protocol,
    }


def _main_title_for_key(key: tuple) -> str:
    if key[0] == "ha_mqtt":
        return "Flespi (via Home Assistant MQTT)"
    return f"Flespi ({key[1]})"


def _subentry_data_from_legacy(entry: ConfigEntry) -> ConfigSubentryData:
    dev_id = entry.data.get(CONF_DEV_ID) or entry.data.get("dev_id")
    topic = entry.data.get(CONF_TOPIC) or entry.data.get("topic")
    auto_discovery = bool(entry.data.get(CONF_AUTO_DISCOVERY, False))
    return ConfigSubentryData(
        subentry_type=SUBENTRY_TYPE_DEVICE,
        title=dev_id,
        unique_id=dev_id,
        data={
            CONF_DEV_ID: dev_id,
            CONF_TOPIC: topic,
            CONF_AUTO_DISCOVERY: auto_discovery,
        },
    )


def _find_main_entry(hass: HomeAssistant, unique_id: str) -> ConfigEntry | None:
    for candidate in hass.config_entries.async_entries(DOMAIN):
        if candidate.version >= 3 and candidate.unique_id == unique_id:
            return candidate
    return None


async def _migrate_legacy_entries(hass: HomeAssistant) -> None:
    """Scan for v<3 entries, group by connection, convert each group to v3.

    Paranoid ordering: the new main entry (or its appended subentry) is created
    BEFORE the corresponding old entry is removed. Any exception in a group
    leaves that group's old entries intact so the migration retries next startup.
    """
    legacy: list[ConfigEntry] = [
        e for e in hass.config_entries.async_entries(DOMAIN) if e.version < 3
    ]
    if not legacy:
        return

    _LOGGER.info("Migrating %d legacy config entries to subentry model", len(legacy))

    # Group by conn key so all devices on the same connection end up under one
    # main entry. Preserve ordering to keep logs deterministic.
    groups: dict[tuple, list[ConfigEntry]] = {}
    for old in legacy:
        groups.setdefault(_conn_key(old), []).append(old)

    # Migrate groups in parallel. Each group is independent — different main
    # entries, different subentries — so there's no shared state to serialize on.
    results = await asyncio.gather(
        *(_migrate_group(hass, key, entries) for key, entries in groups.items()),
        return_exceptions=True,
    )
    for (key, entries), result in zip(groups.items(), results):
        if isinstance(result, Exception):
            _LOGGER.exception(
                "Migration failed for group %s — leaving %d entries untouched",
                key,
                len(entries),
                exc_info=result,
            )


async def _migrate_group(
    hass: HomeAssistant, key: tuple, entries: list[ConfigEntry]
) -> None:
    unique_id = _main_unique_id_for_key(key)
    subentries_data = [_subentry_data_from_legacy(e) for e in entries]
    main_entry = _find_main_entry(hass, unique_id)

    _LOGGER.debug(
        "Migrating group %s (%d legacy entries → main uid=%s, existing=%s)",
        key,
        len(entries),
        unique_id,
        main_entry.entry_id if main_entry else None,
    )

    if main_entry is None:
        # No existing v3 main entry for this connection — create one via the
        # import flow with all subentries in a single atomic step.
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_IMPORT},
            data={
                "__migrate__": True,
                "main_unique_id": unique_id,
                "main_title": _main_title_for_key(key),
                "main_data": _main_data_for_key(key),
                "subentries_data": subentries_data,
            },
        )
        _LOGGER.debug("Migration flow returned %s for group %s", result["type"], key)
        if result["type"] != "create_entry":
            raise RuntimeError(f"Migration import flow returned {result}")
    else:
        # Main entry already exists — append subentries (may happen if the user
        # had a v3 install and also some v<3 entries lying around). Skip any
        # subentry whose unique_id already lives on the main entry (e.g. the
        # previous migration run left it there and the legacy entry is now
        # just stale).
        existing_uids = {s.unique_id for s in main_entry.subentries.values()}
        for sub_data in subentries_data:
            sub_uid = sub_data.get("unique_id")
            if sub_uid in existing_uids:
                _LOGGER.info(
                    "Subentry %s already present on %s; dropping stale legacy entry only",
                    sub_uid,
                    main_entry.entry_id,
                )
                continue
            subentry = ConfigSubentry(
                subentry_type=sub_data["subentry_type"],
                title=sub_data["title"],
                unique_id=sub_uid,
                data=MappingProxyType(dict(sub_data["data"])),
            )
            hass.config_entries.async_add_subentry(main_entry, subentry)
            existing_uids.add(sub_uid)

    # Only after the new main entry/subentries exist do we drop the legacy ones.
    for old in entries:
        await hass.config_entries.async_remove(old.entry_id)

    _LOGGER.info(
        "Migrated %d device(s) into connection '%s'",
        len(entries),
        _main_title_for_key(key),
    )


# =============================================================================
# Cross-domain migration (mqtt_flespi_message → flespi)
#
# Run only from `config_flow.async_step_migrate_from_old`. Cannot live in
# `async_setup` because the final `async_set_disabled_by(None)` call here
# would re-acquire the flespi setup lock and deadlock. By the time the
# config flow runs, our `async_setup` has completed and the lock is free.
# =============================================================================


async def _migrate_entry_from_old_domain(
    hass: HomeAssistant, old_entry: ConfigEntry
) -> None:
    """Move one mqtt_flespi_message entry to the flespi domain.

    Order of operations is delicate. Three HA core constraints conspire here:

    1. `EntityRegistry._validate_item` rejects updates whose
       `new_config_entry_id` isn't already in `hass.config_entries` →
       must register the new entry BEFORE re-parenting registries.
    2. `EntityRegistry.async_update_entity_platform` refuses to migrate
       entities that are currently loaded (have a state in `hass.states`)
       → must unload the old entry's entities BEFORE re-parenting.
    3. We can't just `async_add` and let it set up the new entry — its
       `async_setup_entry` would call `async_get_or_create` for every
       entity, miss the still-old-platform rows, create duplicates, and
       then collide with us when we try to migrate the originals.

    Resolution:
    - Register the new entry **disabled** (`async_add` stores it but
      `async_setup` early-returns on `entry.disabled_by`) — gets us past
      constraint 1 without tripping constraint 3.
    - Unload the old entry — gets us past constraint 2.
    - Re-parent registries.
    - Mirror missing subentries (resume safety).
    - Re-enable the new entry → triggers the deferred `async_setup_entry`;
      lookups now hit the migrated rows.
    - Remove the old entry.
    """
    new_entry = next(
        (
            e
            for e in hass.config_entries.async_entries(DOMAIN)
            if e.unique_id == old_entry.unique_id
        ),
        None,
    )

    is_fresh = new_entry is None
    if is_fresh:
        # ConfigSubentryDataWithId preserves subentry_id across the migration.
        # Without it, fresh ULIDs leak into the new entry, leaving
        # entity_registry rows with `config_subentry_id` pointing at IDs that
        # no longer exist (entities still load but lose their subentry-card
        # grouping in the UI).
        subentries_data = [
            ConfigSubentryDataWithId(
                subentry_type=s.subentry_type,
                title=s.title,
                unique_id=s.unique_id,
                data=dict(s.data),
                subentry_id=s.subentry_id,
            )
            for s in old_entry.subentries.values()
        ]
        new_entry = ConfigEntry(
            version=old_entry.version,
            minor_version=getattr(old_entry, "minor_version", 1) or 1,
            domain=DOMAIN,
            title=old_entry.title,
            data=dict(old_entry.data),
            options=dict(old_entry.options) if old_entry.options else {},
            source="migration",
            unique_id=old_entry.unique_id,
            disabled_by=ConfigEntryDisabler.USER,
            discovery_keys=MappingProxyType({}),
            subentries_data=subentries_data,
        )
        await hass.config_entries.async_add(new_entry)

    # Unload the old entry first — `async_update_entity_platform` rejects
    # loaded entities. `async_unload` is idempotent (no-op on an already-
    # unloaded entry) and returns False on failure; in that case the OLD
    # integration's `async_unload_entry` raised, leaving entities live, so
    # we can't safely migrate.
    unload_ok = await hass.config_entries.async_unload(old_entry.entry_id)
    if not unload_ok:
        raise RuntimeError(
            f"Cannot migrate {old_entry.entry_id}: failed to unload the "
            f"old `{OLD_DOMAIN}` entry — its entities are still loaded"
        )

    _migrate_entity_registry_from_old(hass, old_entry, new_entry)
    _migrate_device_registry_from_old(hass, old_entry, new_entry)

    # Mirror any subentries missing on a pre-existing new_entry (resume case)
    # or freshly-added one (no-op since async_add already embedded them).
    existing_sub_uids = {s.unique_id for s in new_entry.subentries.values()}
    for old_sub in old_entry.subentries.values():
        if old_sub.unique_id in existing_sub_uids:
            continue
        new_sub = ConfigSubentry(
            subentry_type=old_sub.subentry_type,
            title=old_sub.title,
            unique_id=old_sub.unique_id,
            data=MappingProxyType(dict(old_sub.data)),
            subentry_id=old_sub.subentry_id,
        )
        hass.config_entries.async_add_subentry(new_entry, new_sub)

    # Re-enable: registry rows are migrated, async_setup_entry now finds them.
    if new_entry.disabled_by is not None:
        await hass.config_entries.async_set_disabled_by(new_entry.entry_id, None)

    await hass.config_entries.async_remove(old_entry.entry_id)


def _migrate_entity_registry_from_old(
    hass: HomeAssistant, old_entry: ConfigEntry, new_entry: ConfigEntry
) -> None:
    """Rewrite entity_registry rows from old_entry (mqtt_flespi_message) to new_entry (flespi).

    Updates per row:
    - `config_entry_id` → new_entry.entry_id
    - `unique_id`       → swap `mqtt_flespi_message_*` prefix for `flespi_*`
    - `platform`        → `flespi`

    `entity_id` is left alone so user automations, Lovelace cards, and
    Recorder history continue to resolve. `async_update_entity_platform`
    records the prior unique_id under `previous_unique_id` for history
    continuity.

    `config_subentry_id` is intentionally NOT touched — subentry_ids are
    preserved across the migration via ConfigSubentryDataWithId.
    """
    ent_reg = er.async_get(hass)
    prefix_old = f"{OLD_DOMAIN}_"
    prefix_new = f"{DOMAIN}_"

    for entity in list(ent_reg.entities.values()):
        if entity.config_entry_id != old_entry.entry_id:
            continue

        new_unique_id = entity.unique_id
        if new_unique_id.startswith(prefix_old):
            new_unique_id = prefix_new + new_unique_id[len(prefix_old):]

        ent_reg.async_update_entity_platform(
            entity.entity_id,
            DOMAIN,
            new_unique_id=new_unique_id,
            new_config_entry_id=new_entry.entry_id,
        )


def _migrate_device_registry_from_old(
    hass: HomeAssistant, old_entry: ConfigEntry, new_entry: ConfigEntry
) -> None:
    """Rewrite device_registry rows from old_entry to new_entry.

    Three fields change per device:
    - `identifiers`: `(mqtt_flespi_message, dev_id)` → `(flespi, dev_id)`. Must
      change before the old entry is removed; otherwise the new integration's
      `DeviceInfo(identifiers={(flespi, dev_id)})` won't match the existing
      device and HA creates a duplicate.
    - `config_entries` set: drop old_entry.entry_id, add new_entry.entry_id.
      Without this, removing the old entry would orphan the device.
    - `config_entries_subentries` mapping: rebind each subentry attachment from
      old_entry.entry_id to new_entry.entry_id, keeping the same subentry_ids.
      `async_update_device` only handles ONE subentry per call, so devices
      attached to multiple subentries (rare here — one device = one subentry)
      need follow-up calls.
    """
    dev_reg = dr.async_get(hass)

    for device in list(dev_reg.devices.values()):
        if old_entry.entry_id not in device.config_entries:
            continue

        new_identifiers = {
            (DOMAIN if dom == OLD_DOMAIN else dom, ident)
            for (dom, ident) in device.identifiers
        }

        # Snapshot the subentry attachment under the old entry. `{None}`
        # default means "attached to the main entry without a specific
        # subentry", which `async_update_device` accepts as a valid value.
        old_subentry_ids = list(
            device.config_entries_subentries.get(old_entry.entry_id, {None})
        )

        # First call carries the identifier swap, the entry-set swap, and the
        # first subentry rebinding atomically.
        dev_reg.async_update_device(
            device.id,
            new_identifiers=new_identifiers,
            add_config_entry_id=new_entry.entry_id,
            add_config_subentry_id=old_subentry_ids[0],
            remove_config_entry_id=old_entry.entry_id,
        )

        for sub_id in old_subentry_ids[1:]:
            dev_reg.async_update_device(
                device.id,
                add_config_entry_id=new_entry.entry_id,
                add_config_subentry_id=sub_id,
            )
