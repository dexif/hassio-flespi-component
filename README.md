# mqtt flespi message (Home Assistant component)

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![license_badge](https://img.shields.io/github/license/shaiu/technicolor)](https://img.shields.io/github/license/shaiu/technicolor)


## Changelog
- 0.1.3 Fixed HA compatibility
- 0.1.2 Fix import
- 0.1.1 Added [HACS](https://github.com/hacs/integration) compatibility
- 0.1.0 Now compatible with version 0.93

## Usage
Add in /config/configuration.yaml
```yaml
device_tracker:
  — platform: mqtt_flespi_message
    devices:
      # flespi channel
      dexif: 'flespi/message/gw/channels/7730/Dexif'
      another: 'flespi/message/gw/channels/7730/another'
      # flespi device
      dexif2: 'flespi/message/gw/devices/173073'
```

You can also add the known devices into the known_devices.yaml file (otherwise they will appear there automatically).
```yaml
dexif:
  hide_if_away: false
  icon:
  mac:
  name: dexif
  picture:
  track: true
  gravatar: blah@blah.blah
```

https://flespi.com/blog/smart-home-with-a-human-touch-teaching-home-assistant-to-serve-you-coffee