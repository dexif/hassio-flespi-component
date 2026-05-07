# Flespi (Home Assistant integration)

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![license_badge](https://img.shields.io/github/license/dexif/ha-flespi)](https://github.com/dexif/ha-flespi/blob/master/LICENSE)

> [!IMPORTANT]
> **Upgrading from 0.4.x to 0.5.0 — one-time HACS reinstall required.**
>
> 0.5.0 renames the integration's domain from `mqtt_flespi_message` to `flespi`. HACS caches the
> integration's path from the first time the repository was added and never refreshes it on update,
> so a normal HACS update fails with `No manifest.json file found
> 'custom_components/mqtt_flespi_message/manifest.json'`. This is a HACS limitation, not a bug in the
> integration.
>
> **What to do — once, before installing 0.5.0:**
>
> 1. Make sure you have run **0.4.5 at least once** on this Home Assistant instance. 0.4.5 ships a
>    dormant migration shim that performs the actual rename of your config entries / devices /
>    entities once 0.5.0 is on disk. Skip this step and your devices will be stranded on the old
>    domain after upgrade. (HACS lets you pick a specific version: card → ⋮ menu → "Redownload" → choose v0.4.5.)
> 2. In HACS, open this repository → ⋮ menu → **Remove**. This only unregisters the repo from HACS
>    — it does **not** delete files on disk and does **not** touch your Home Assistant config.
>    Your devices, history, and automations stay intact.
> 3. Add the repository back as a custom repository (Integration type) and select version
>    **0.5.0** (or newer).
> 4. Restart Home Assistant. The 0.4.5 migration shim runs automatically and re-parents every
>    config entry, device, and entity to the `flespi` domain. `entity_id`s, Recorder history,
>    Lovelace cards, and automations continue to work unchanged.
>
> After this one-time step, future HACS updates work normally.

Home Assistant integration for GPS trackers and telematics devices connected to [flespi](https://flespi.com/).
One connection covers many devices; each device shows up as a standard HA device with GPS location on the map,
speed / altitude / battery sensors out of the box, and optional auto-discovery of every other telemetry
parameter the device reports.

## Connection modes

- **Direct connection to the flespi MQTT broker** (recommended). Uses a flespi token. No bridge, no
  local mosquitto, no YAML. Enables device search in the config flow and auto-discovery of
  sensors/binary_sensors from the device's telemetry snapshot.
- **Via Home Assistant's MQTT integration** (the original mode). Requires you to bridge the flespi
  broker to your local mosquitto and have HA's `mqtt` integration configured. Keeps the classic
  five sensors (speed, altitude, direction, satellites, battery voltage).

## Installation

### HACS (recommended)
1. Add this repository as a custom repository in HACS (Integration type).
2. Install "Flespi".
3. Restart Home Assistant.

### Manual
Copy `custom_components/flespi/` to your `config/custom_components/` directory and restart Home
Assistant.

## Configuration

**Settings → Devices & Services → Add Integration → Flespi.**

Starting with 0.4.0 the integration uses a *connection + subentries* model: one config entry
represents the credentials (flespi token for direct mode, or the HA-MQTT placeholder), and each
flespi device is a *subentry* of that connection. Pick the mode on the first screen.

### Direct connection to flespi

Sub-menu offers two paths:

**Find my device** — enter a [flespi token](https://flespi.io/docs/#/platform/tokens) and a name or
IMEI substring. The integration queries flespi's REST API, shows up to 50 matches, and you pick one
from the list. `dev_id` and the MQTT topic are filled in automatically. Works on accounts with tens
of thousands of devices because the search is server-side.

**Enter device ID manually** — short form: HA device name, flespi token, numeric flespi device ID
(e.g. `5100080`), auto-discovery toggle. The MQTT topic is built automatically as
`flespi/message/gw/devices/{id}/#`.

Additional devices on the same token: open the connection card and click **Add device** — the token
is not asked again. All devices sharing the same token use a single MQTT session to `mqtt.flespi.io`.

Advanced options (host/port/TLS/protocol) are hidden from the UI and use sensible defaults
(`mqtt.flespi.io`, port `8883`, TLS, MQTT v5). Users who had non-default values in 0.3.x keep them
through migration.

### Via Home Assistant MQTT

Form asks for device name and an MQTT topic on your local broker. You still need to configure a
bridge from flespi to mosquitto and set up HA's `mqtt` integration. All HA-MQTT devices live under
a single placeholder connection entry.

### Reconfiguring

- **Connection**: reconfigure the main entry to update the flespi token (direct mode only). All
  devices tied to it start using the new token automatically.
- **Device**: reconfigure a subentry to rename the HA device, point it at a different flespi device
  ID, or toggle auto-discovery.

## Entities

Each configured device creates one HA device with:

- `device_tracker.<dev_id>` — GPS location (latitude, longitude, accuracy, battery level).
- `sensor.<dev_id>_speed`, `..._altitude`, `..._direction`, `..._satellites`, `..._battery_voltage`
  — the always-on legacy set. `unique_id`s are preserved across the 0.3.x → 0.4.0 migration, so
  automations and Recorder history survive intact.
- `binary_sensor.<dev_id>_online` — retained `connected` state from flespi (direct mode only).
- Auto-discovered sensors and binary_sensors for every fresh telemetry parameter (direct mode +
  auto-discovery enabled). By default, only common parameters are enabled out of the box; the
  rest are added but disabled — toggle them on per device from the entity list when you need them.

**Enabled by default** (auto-discovery): the legacy five plus `fuel.level`, `engine.ignition.status`
(and variants), `external.powersource.voltage`, `can.fuel.volume`, `can.fuel.level`. To activate
every auto-discovered sensor at once, tick **Enable all auto-discovered sensors** when adding or
reconfiguring a device.

Auto-discovered entities get proper `device_class` / units where possible: battery level →
`battery`, voltages → `voltage`, temperatures → `temperature`, ignition / movement / door / lock /
charging booleans → respective binary device classes. Good fit for dashboard cards like
[Vehicle Status Card](https://github.com/ngocjohn/vehicle-status-card).

## Upgrading from 0.2.x or 0.3.x

Migration is automatic: on the first restart with 0.4.0 installed, the integration scans every
existing config entry, groups them by connection credentials, and converts each group into a new
*main entry* with each device as a subentry. Old entries are only removed after the new one is in
place; any failure leaves the originals untouched and retries next restart.

Entity `unique_id`s and `entity_id`s for the classic five sensors remain unchanged, so your
automations, dashboards and Recorder history keep working.

## Changelog

- **0.5.0**
  - Domain rename: `mqtt_flespi_message` → `flespi`. The integration's display
    name was already "Flespi" since 0.4.4; the internal domain now matches.
  - Existing installs migrate automatically on the first restart after upgrade,
    via the dormant shim shipped in 0.4.5: config entries, devices, entities,
    and registry rows are re-parented to the new domain. `entity_id`s, Recorder
    history, Lovelace cards, and automations continue to work unchanged.
  - **Upgrading from 0.4.x requires a one-time HACS reinstall** — see the
    upgrade box at the top of this README. HACS caches the integration's path
    on first registration and doesn't re-scan it on update, so a normal HACS
    update fails with `No manifest.json file found
    'custom_components/mqtt_flespi_message/manifest.json'`. Workaround: ensure
    0.4.5 has run, then remove and re-add the repo in HACS before installing
    0.5.0.
  - Removed the deprecated YAML platform import (`device_tracker:` `platform:
    mqtt_flespi_message`). The `flespi:` YAML section never existed, so there
    is nothing in the new domain for it to import.
- **0.4.6**
  - Direct mode: dropped the per-parameter `flespi/state/.../telemetry/#`
    subscription. Per-param topics arrived as separate MQTT packets and caused
    `position.latitude` / `position.longitude` updates to land out of sync,
    producing a staircase trail on the map. Live updates now flow only through
    the device-message topic, where all parameters are atomic.
  - Initial values are seeded from the REST telemetry snapshot on startup —
    unconditionally in direct mode, not just when auto-discovery is on — so
    the device tracker and legacy sensors have values immediately on first
    load.
- **0.4.5**
  - Dormant migration shim for the upcoming domain rename. Behavior is identical
    to 0.4.4; no user action required. When the next release ships the new
    `flespi` domain on disk, this shim activates automatically on the next HA
    restart and migrates every existing entry over (config entries, entity
    registry unique_id and platform, device registry identifiers), preserving
    `entity_id`s and Recorder history.
- **0.4.4**
  - Renamed the integration to **Flespi** (was "Mqtt flespi message"). Display-name change
    only — domain (`mqtt_flespi_message`) is unchanged in this release, so existing
    configurations, entity IDs and Recorder history are unaffected.
  - Repository moved to [`dexif/ha-flespi`](https://github.com/dexif/ha-flespi). GitHub
    redirects the old URL automatically; HACS picks up the rename without manual action.
  - Heads-up: the domain will change to `flespi` in an upcoming release (planned for 0.5.0).
    The transition will include an automatic migration that preserves entity IDs and history,
    same approach as the 0.4.0 subentry migration. No action required from users.
- **0.4.2**
  - Auto-discovered sensors are now added in two tiers: a curated set (legacy five plus
    `fuel.level`, `engine.ignition.status`, `external.powersource.voltage`, `can.fuel.volume`,
    `can.fuel.level`) is enabled by default; everything else is registered but disabled.
  - New **Enable all auto-discovered sensors** toggle in the device form / reconfigure to flip
    all auto-discovered entities on at once.
- **0.4.1**
  - Fixed `/gw/message-parameters` field names — auto-discovery now reads the correct `unit`
    (singular) and `info` fields, so units and human-readable descriptions land on the entities.
- **0.4.0**
  - Subentry model: one main entry per connection, one subentry per device. Add many devices under
    the same token without retyping credentials.
  - Shared MQTT client per connection — N devices on one token now use one TCP/TLS session
    instead of N.
  - Simplified direct-mode UI: the form asks for a token and a numeric flespi device ID; the MQTT
    topic is constructed automatically.
  - Reconfigure token on the connection card; reconfigure per-device on each subentry card.
  - Automatic migration from 0.2.x / 0.3.x entries, preserving entity IDs and history.
- **0.3.0**
  - Direct connection mode to the flespi MQTT broker (no bridge required).
  - Device discovery in the config flow — search your flespi account by name/IMEI.
  - Auto-discovery of sensors and binary_sensors from the device's telemetry.
  - `binary_sensor` platform: new online/connected sensor plus auto-discovered booleans.
  - MQTT protocol selector (3.1 / 3.1.1 / 5, default 5).
  - Retained state-topic bootstrap in direct mode (entities hydrate instantly after HA restart).
- **0.2.1** Reconfigure step for device name and MQTT topic.
- **0.2.0** Modernized to config flow UI, added telemetry sensors, YAML auto-migration.
- **0.1.4** Support for device telemetry position format.
- **0.1.3** Fixed HA compatibility.
- **0.1.2** Fix import.
- **0.1.1** Added [HACS](https://github.com/hacs/integration) compatibility.
- **0.1.0** Now compatible with version 0.93.

## Links

- [flespi — smart home with a human touch (blog post)](https://flespi.com/blog/smart-home-with-a-human-touch-teaching-home-assistant-to-serve-you-coffee)
- [flespi REST API docs](https://flespi.io/docs/)
