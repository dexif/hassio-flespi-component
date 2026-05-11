"""Constants for the flespi integration."""

from homeassistant.const import Platform

DOMAIN = "flespi"
PLATFORMS = [Platform.BINARY_SENSOR, Platform.DEVICE_TRACKER, Platform.SENSOR]

# Connection modes
CONF_MODE = "mode"
MODE_HA_MQTT = "ha_mqtt"
MODE_DIRECT = "direct"

# Subentry type (each flespi device is a subentry of a connection main entry)
SUBENTRY_TYPE_DEVICE = "device"
SUBENTRY_TYPE_CUSTOMER = "customer"

# Per-device config keys (stored on each subentry)
CONF_DEV_ID = "dev_id"
CONF_TOPIC = "topic"
# Form-only key for direct mode (not persisted — the topic is stored instead)
CONF_FLESPI_DEVICE_ID = "flespi_device_id"

# Direct-mode config keys
CONF_TOKEN = "token"
CONF_HOST = "host"
CONF_PORT = "port"
CONF_USE_TLS = "use_tls"
CONF_PROTOCOL = "protocol"

# Direct-mode defaults
DEFAULT_HOST = "mqtt.flespi.io"
DEFAULT_PORT_TLS = 8883
DEFAULT_PORT_PLAIN = 1883

# MQTT protocol versions (stored as strings in entry.data)
PROTOCOL_V31 = "3.1"
PROTOCOL_V311 = "3.1.1"
PROTOCOL_V5 = "5"
PROTOCOL_CHOICES = [PROTOCOL_V31, PROTOCOL_V311, PROTOCOL_V5]
DEFAULT_PROTOCOL = PROTOCOL_V5

# Auto-discovery (direct mode only)
CONF_AUTO_DISCOVERY = "auto_discovery"
# When False (default), only well-known parameters (speed, altitude, fuel, ignition, …)
# are enabled out of the box; the rest are added to the entity registry but
# disabled, so the user can enable just what they want.
CONF_ENABLE_ALL_SENSORS = "enable_all_sensors"
DEFAULT_STALE_THRESHOLD_S = 7 * 24 * 3600  # 7 days

# REST API base URL (public flespi)
FLESPI_REST_BASE_URL = "https://flespi.io"

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
