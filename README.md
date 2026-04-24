# mqtt flespi message (Home Assistant component)

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![license_badge](https://img.shields.io/github/license/dexif/hassio-flespi-component)](https://github.com/dexif/hassio-flespi-component/blob/master/LICENSE)

Home Assistant integration for GPS trackers and other telematics devices connected to [flespi](https://flespi.com/).
One config entry per device, GPS location on the map, speed/altitude/battery sensors out of the box,
and optional auto-discovery of every other telemetry parameter the device reports.

## Connection modes

Starting with 0.3.0 the integration supports two ways to get data into Home Assistant:

- **Direct connection to the flespi MQTT broker** (recommended). Uses a flespi token.
  No bridge, no local mosquitto, no YAML. Enables device search in the config flow and
  auto-discovery of sensors/binary_sensors from the device's telemetry snapshot.
- **Via Home Assistant's MQTT integration** (the original mode). Requires you to bridge
  the flespi broker to your local mosquitto and have HA's `mqtt` integration configured.
  Keeps the classic 5 sensors (speed, altitude, direction, satellites, battery voltage).

## Installation

### HACS (recommended)
1. Add this repository as a custom repository in HACS (Integration type).
2. Install "Mqtt flespi message".
3. Restart Home Assistant.

### Manual
Copy `custom_components/mqtt_flespi_message/` to your `config/custom_components/` directory
and restart Home Assistant.

## Configuration

Go to **Settings → Devices & Services → Add Integration → Mqtt flespi message**.

Pick a connection mode on the first screen:

### Direct connection to flespi

Sub-menu offers two paths:

**Find my device** — enter a [flespi token](https://flespi.io/docs/#/platform/tokens)
and a name or IMEI substring. The integration queries flespi's REST API, shows up to
50 matches, and you pick one from the list. `dev_id` and the MQTT topic are filled in
automatically. Works fine on accounts with tens of thousands of devices because the
search is server-side.

**Enter device ID manually** — classic form: device name (for HA),
token, MQTT topic (e.g. `flespi/message/gw/devices/12345/#`), broker host/port/TLS,
MQTT protocol version, auto-discovery toggle.

Advanced options on both paths:
- Broker host (default `mqtt.flespi.io`), port (8883 TLS / 1883 plain), TLS toggle.
- MQTT protocol version: 3.1, 3.1.1, or 5 (default).
- Auto-discover sensors toggle — fetches the device's telemetry via REST at startup
  and creates a sensor or binary_sensor per recent parameter. Stale parameters
  (older than 7 days) are ignored.

### Via Home Assistant MQTT

Form asks for device name and an MQTT topic on your local broker. You still need to
configure a bridge from flespi to mosquitto and set up HA's `mqtt` integration.

### YAML (deprecated, auto-migrated)

Legacy configurations are imported automatically into config entries on startup
and removed on next restart once you remove the YAML block.

```yaml
device_tracker:
  - platform: mqtt_flespi_message
    devices:
      dexif: 'flespi/message/gw/channels/7730/Dexif'
      dexif2: 'flespi/message/gw/devices/173073'
```

## Entities

Each configured device creates one HA device with:

- `device_tracker.<dev_id>` — GPS location (latitude, longitude, accuracy, battery level)
- `sensor.<dev_id>_speed`, `..._altitude`, `..._direction`, `..._satellites`, `..._battery_voltage` — the always-on legacy set
- `binary_sensor.<dev_id>_online` — retained `connected` state from flespi (direct mode only)
- Auto-discovered sensors and binary_sensors for every fresh telemetry parameter
  (direct mode + auto-discovery enabled)

Auto-discovered entities get proper `device_class`/units where possible:
battery level → `battery`, voltages → `voltage`, temperatures → `temperature`,
ignition/movement/door/lock/charging booleans → respective binary device classes.
Good fit for dashboard cards like [Vehicle Status Card](https://github.com/ngocjohn/vehicle-status-card).

## Changelog

- **0.3.0**
  - Direct connection mode to the flespi MQTT broker (no bridge required).
  - Device discovery in the config flow — search your flespi account by name/IMEI.
  - Auto-discovery of sensors and binary_sensors from the device's telemetry.
  - `binary_sensor` platform: new online/connected sensor plus auto-discovered booleans.
  - MQTT protocol selector (3.1 / 3.1.1 / 5, default 5).
  - Retained state-topic bootstrap in direct mode (entities hydrate instantly after HA restart).
  - Config entry migration v1 → v2 (transparent for existing 0.2.x users).
- **0.2.1** Reconfigure step for device name and MQTT topic.
- **0.2.0** Modernized to config flow UI, added telemetry sensors, YAML auto-migration.
- **0.1.4** support device telemetry position format.
- **0.1.3** Fixed HA compatibility.
- **0.1.2** Fix import.
- **0.1.1** Added [HACS](https://github.com/hacs/integration) compatibility.
- **0.1.0** Now compatible with version 0.93.

## Links

- [flespi — smart home with a human touch (blog post)](https://flespi.com/blog/smart-home-with-a-human-touch-teaching-home-assistant-to-serve-you-coffee)
- [flespi REST API docs](https://flespi.io/docs/)
