"""Constants for the mqtt_flespi_message integration."""

from homeassistant.const import Platform

DOMAIN = "mqtt_flespi_message"
PLATFORMS = [Platform.DEVICE_TRACKER, Platform.SENSOR]

# Flespi message keys (flat dot-notation format)
ATTR_POSITION_LATITUDE = "position.latitude"
ATTR_POSITION_LONGITUDE = "position.longitude"
ATTR_POSITION_SPEED = "position.speed"
ATTR_POSITION_ALTITUDE = "position.altitude"
ATTR_POSITION_DIRECTION = "position.direction"
ATTR_POSITION_HDOP = "position.hdop"
ATTR_POSITION_SATELLITES = "position.satellites"
ATTR_BATTERY_LEVEL = "battery.level"
ATTR_BATTERY_VOLTAGE = "battery.voltage"

# Alternative keys (telemetry/position format)
ATTR_ALT_LATITUDE = "latitude"
ATTR_ALT_LONGITUDE = "longitude"
ATTR_ALT_SPEED = "speed"
ATTR_ALT_ALTITUDE = "altitude"
ATTR_ALT_DIRECTION = "direction"
ATTR_ALT_HDOP = "hdop"
ATTR_ALT_SATELLITES = "satellites"
