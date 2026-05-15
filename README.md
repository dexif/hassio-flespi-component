# Flespi (Home Assistant integration)

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![license_badge](https://img.shields.io/github/license/dexif/ha-flespi)](https://github.com/dexif/ha-flespi/blob/master/LICENSE)

<details>
<summary><b>Upgrading from 0.4.x — one-time HACS reinstall required (install 0.5.7 or newer)</b></summary>

0.5.0 renamed the integration's domain from `mqtt_flespi_message` to `flespi`. HACS caches the
integration's path from the first time the repository was added and never refreshes it on update,
so a normal HACS update fails with `No manifest.json file found
'custom_components/mqtt_flespi_message/manifest.json'`. This is a HACS limitation, not a bug in
the integration.

**What to do — once:**

1. In HACS, open this repository → ⋮ menu → **Remove**. This only unregisters the repo from HACS
   — it does **not** delete files on disk and does **not** touch your Home Assistant config.
   Your devices, history, and automations stay intact.
2. Add the repository back as a custom repository (Integration type) and install **0.5.7** (or
   newer). Earlier 0.5.x versions had migration bugs that left devices stranded on the old
   domain.
3. Restart Home Assistant.
4. Open *Settings → Devices & Services → + Add Integration* and search for **Flespi**. Two
   entries with the same name appear — click either; the new integration detects the orphan
   entries and shows a migration form. Submit it to re-parent every config entry, device, and
   entity registry row from `mqtt_flespi_message` to `flespi`. `entity_id`s, Recorder history,
   Lovelace cards, and automations continue to work unchanged.
5. After a successful migration, manually delete the leftover
   `custom_components/mqtt_flespi_message/` folder from your `config/custom_components/`
   directory. HACS does not clean up old-domain folders on update; the folder is harmless to
   leave in place but contributes a duplicate "Flespi" entry to the *Add Integration* picker
   until you remove it.

After this one-time step, future HACS updates work normally.
</details>

Home Assistant integration for GPS trackers and telematics devices connected to [flespi](https://flespi.com/).
One connection covers many devices; each device shows up as a standard HA device with GPS location on the map,
speed / altitude / battery sensors out of the box, and optional auto-discovery of every other telemetry
parameter the device reports. With a Master-level token you can also add **account counters** — flespi
platform usage metrics (API calls, MQTT sessions, storage, entity counts, etc.) as HA sensors.

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

#### Account counters

Open the connection card and click **Add account counters**. This creates a "Flespi account" device
with sensors for every platform usage counter flespi publishes (`api/calls`, `mqtt/messages`,
`devices/count`, channel/stream throughput, storage, errors, plan limits, etc.). Requires a
**Master-level** flespi token — Standard and ACL tokens silently skip this feature. Only one
account-counters entry per connection.

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

### Account counters (direct mode, Master token)

When an *Account counters* subentry is added, the integration creates a **Flespi account** device
with sensors for every counter published on `flespi/state/platform/customer/counters/#`. Counters
are auto-discovered from retained MQTT messages — new counters added by flespi appear automatically
without a restart.

**Enabled by default**: `api/calls`, `api/traffic`, `mqtt/sessions`, `mqtt/messages`, and every
`*/count` counter (device, channel, stream, calculator, plugin, … counts). All other counters
(storage, traffic, errors, plan limits, grants, etc.) are registered but disabled — enable what you
need from the entity list. `_limit` sensors whose value is `-1` (unlimited plan) report as
unavailable.

## Upgrading from 0.2.x or 0.3.x

Migration is automatic: on the first restart with 0.4.0 installed, the integration scans every
existing config entry, groups them by connection credentials, and converts each group into a new
*main entry* with each device as a subentry. Old entries are only removed after the new one is in
place; any failure leaves the originals untouched and retries next restart.

Entity `unique_id`s and `entity_id`s for the classic five sensors remain unchanged, so your
automations, dashboards and Recorder history keep working.

## Changelog

- **0.6.0** — Account counters subentry (direct mode, Master token). ~140 flespi platform usage sensors, auto-discovered from MQTT retained topics.
- **0.5.0–0.5.8** — Domain rename `mqtt_flespi_message` → `flespi` with automatic migration. 0.5.1–0.5.8 fix edge cases. One-time HACS reinstall required from 0.4.x — see upgrade note above.
- **0.4.6** — Atomic device-message updates (fixes staircase trail on map). REST telemetry seed on startup.
- **0.4.5** — Dormant migration shim for the upcoming domain rename.
- **0.4.4** — Renamed to "Flespi". Repository moved to [dexif/ha-flespi](https://github.com/dexif/ha-flespi).
- **0.4.2** — Two-tier auto-discovery: curated set enabled by default, rest disabled. "Enable all" toggle.
- **0.4.1** — Fixed auto-discovery field names (`unit`, `info`).
- **0.4.0** — Subentry model (one connection, many devices). Shared MQTT client per token. Migration from 0.2.x/0.3.x.
- **0.3.0** — Direct connection to flespi broker. Device search. Auto-discovery. Online sensor. Retained bootstrap.
- **0.2.0** — Config flow UI, telemetry sensors, YAML auto-migration.
- **0.1.x** — Initial releases, HACS compatibility.

## Links

- [flespi — smart home with a human touch (blog post)](https://flespi.com/blog/smart-home-with-a-human-touch-teaching-home-assistant-to-serve-you-coffee)
- [flespi REST API docs](https://flespi.io/docs/)
