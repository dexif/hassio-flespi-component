# mqtt flespi message (Home Assistant component)

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![license_badge](https://img.shields.io/github/license/dexif/hassio-flespi-component)](https://github.com/dexif/hassio-flespi-component/blob/master/LICENSE)


## Changelog
- 0.2.0 Modernized to config flow UI, added telemetry sensors (speed, altitude, direction, satellites, battery voltage), YAML auto-migration
- 0.1.4 support device telemetry position format
- 0.1.3 Fixed HA compatibility
- 0.1.2 Fix import
- 0.1.1 Added [HACS](https://github.com/hacs/integration) compatibility
- 0.1.0 Now compatible with version 0.93

## Installation

### HACS (recommended)
1. Add this repository as a custom repository in HACS
2. Install "Mqtt flespi message"
3. Restart Home Assistant

### Manual
Copy the `custom_components/mqtt_flespi_message` folder to your `config/custom_components/` directory.

## Configuration

### UI (recommended)
1. Go to **Settings > Devices & Services > Add Integration**
2. Search for "Mqtt flespi message"
3. Enter a device name and the flespi MQTT topic

### YAML (deprecated, auto-migrated to UI)
```yaml
device_tracker:
  - platform: mqtt_flespi_message
    devices:
      # flespi channel
      dexif: 'flespi/message/gw/channels/7730/Dexif'
      another: 'flespi/message/gw/channels/7730/another'
      # flespi device
      dexif2: 'flespi/message/gw/devices/173073'
      # flespi device telemetry position
      dexiftelemetry: 'flespi/state/gw/devices/173073/telemetry/position'
```

> **Note:** YAML configuration is deprecated and will be automatically migrated to config entries on next restart. After migration, remove the YAML config and restart.

## Entities

Each configured device creates:
- **Device tracker** — GPS location with latitude, longitude, accuracy, and battery level
- **Sensors** — Speed (km/h), Altitude (m), Direction (°), Satellites, Battery voltage (mV)

## Links
https://flespi.com/blog/smart-home-with-a-human-touch-teaching-home-assistant-to-serve-you-coffee
