"""Config flow for mqtt_flespi_message integration."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Mapping

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow
from homeassistant.data_entry_flow import FlowResult
from homeassistant.util import slugify

from .api import FlespiApiError, FlespiRestClient
from .const import (
    CONF_AUTO_DISCOVERY,
    CONF_HOST,
    CONF_MODE,
    CONF_PORT,
    CONF_PROTOCOL,
    CONF_TOKEN,
    CONF_USE_TLS,
    DEFAULT_HOST,
    DEFAULT_PORT_TLS,
    DEFAULT_PROTOCOL,
    DOMAIN,
    MODE_DIRECT,
    MODE_HA_MQTT,
    PROTOCOL_CHOICES,
)
from .coordinator import build_direct_client

_LOGGER = logging.getLogger(__name__)

_SEARCH = "search"

# Step id rendered for each mode's form. MODE_DIRECT uses "direct_manual" because
# "direct" is the sub-menu step (search vs manual).
_STEP_ID_FOR_MODE: dict[str, str] = {
    MODE_HA_MQTT: MODE_HA_MQTT,
    MODE_DIRECT: "direct_manual",
}


def _build_schema(mode: str, defaults: Mapping[str, Any]) -> vol.Schema:
    """Build the config-flow schema for a given mode, seeded with defaults."""

    def d(key: str, fallback: Any = vol.UNDEFINED) -> Any:
        val = defaults.get(key)
        return val if val is not None else fallback

    fields: dict[Any, Any] = {
        vol.Required("dev_id", default=d("dev_id")): str,
    }
    if mode == MODE_DIRECT:
        fields[vol.Required(CONF_TOKEN, default=d(CONF_TOKEN))] = str
    fields[vol.Required("topic", default=d("topic"))] = str
    if mode == MODE_DIRECT:
        _extend_direct_conn_fields(fields, d)
    return vol.Schema(fields)


def _build_search_schema(defaults: Mapping[str, Any]) -> vol.Schema:
    """Schema for the direct-mode search form (no dev_id/topic yet)."""

    def d(key: str, fallback: Any = vol.UNDEFINED) -> Any:
        val = defaults.get(key)
        return val if val is not None else fallback

    fields: dict[Any, Any] = {
        vol.Required(CONF_TOKEN, default=d(CONF_TOKEN)): str,
        vol.Required(_SEARCH, default=d(_SEARCH)): str,
    }
    _extend_direct_conn_fields(fields, d)
    return vol.Schema(fields)


def _extend_direct_conn_fields(fields: dict[Any, Any], d) -> None:
    """Attach the direct-mode connection + auto-discovery fields to a schema dict."""
    fields[vol.Optional(CONF_HOST, default=d(CONF_HOST, DEFAULT_HOST))] = str
    fields[vol.Optional(CONF_PORT, default=d(CONF_PORT, DEFAULT_PORT_TLS))] = int
    fields[vol.Optional(CONF_USE_TLS, default=d(CONF_USE_TLS, True))] = bool
    fields[
        vol.Optional(CONF_PROTOCOL, default=d(CONF_PROTOCOL, DEFAULT_PROTOCOL))
    ] = vol.In(PROTOCOL_CHOICES)
    fields[
        vol.Optional(CONF_AUTO_DISCOVERY, default=d(CONF_AUTO_DISCOVERY, True))
    ] = bool


def _validate_topic(topic: str) -> str | None:
    """Return an error key if the topic is invalid, otherwise None."""
    if not topic or "#" in topic:
        return "invalid_topic"
    return None


async def _test_direct_connection(hass, user_input: Mapping[str, Any]) -> str | None:
    """Probe the flespi broker with the given credentials. Return error key or None."""
    token: str = user_input[CONF_TOKEN]
    host: str = user_input.get(CONF_HOST, DEFAULT_HOST)
    port: int = user_input.get(CONF_PORT, DEFAULT_PORT_TLS)
    use_tls: bool = user_input.get(CONF_USE_TLS, True)
    protocol: str = user_input.get(CONF_PROTOCOL, DEFAULT_PROTOCOL)

    done = asyncio.Event()
    outcome: dict[str, str | None] = {"error": "cannot_connect"}
    loop = hass.loop

    def _on_connect(c, userdata, flags, reason_code, properties) -> None:
        if reason_code.is_failure:
            rc_value = getattr(reason_code, "value", None)
            outcome["error"] = (
                "invalid_auth" if rc_value in (4, 5, 0x86, 0x87) else "cannot_connect"
            )
        else:
            outcome["error"] = None
        loop.call_soon_threadsafe(done.set)

    client = build_direct_client(
        f"ha-{DOMAIN}-test-{uuid.uuid4().hex[:8]}", token, use_tls, protocol
    )
    client.on_connect = _on_connect

    try:
        client.connect_async(host, port, keepalive=30)
        client.loop_start()
        try:
            await asyncio.wait_for(done.wait(), timeout=10)
        except asyncio.TimeoutError:
            outcome["error"] = "cannot_connect"
    except OSError:
        outcome["error"] = "cannot_connect"
    finally:
        def _cleanup() -> None:
            try:
                client.disconnect()
            except OSError:
                pass
            client.loop_stop()

        await hass.async_add_executor_job(_cleanup)

    return outcome["error"]


def _format_device_label(device: Mapping[str, Any]) -> str:
    """Human-readable label for a device dropdown option."""
    name = device.get("name") or "—"
    ident = (device.get("configuration") or {}).get("ident") or "—"
    return f'{name} (id={device.get("id")}, ident={ident})'


class MqttFlespiMessageConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for mqtt_flespi_message."""

    VERSION = 2

    def __init__(self) -> None:
        super().__init__()
        self._direct_creds: dict[str, Any] = {}
        self._direct_devices: list[dict[str, Any]] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Entry point: pick a connection mode."""
        return self.async_show_menu(
            step_id="user",
            menu_options=[MODE_HA_MQTT, MODE_DIRECT],
        )

    async def async_step_ha_mqtt(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Configure a device that receives data via HA's MQTT integration."""
        return await self._async_step_create(MODE_HA_MQTT, user_input)

    async def async_step_direct(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Sub-menu: pick device via REST search or enter it manually."""
        return self.async_show_menu(
            step_id="direct",
            menu_options=["direct_search", "direct_manual"],
        )

    async def async_step_direct_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manual-entry path for direct-mode (user types dev_id + topic)."""
        return await self._async_step_create(MODE_DIRECT, user_input)

    async def async_step_direct_search(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Search the flespi account for devices by name/ident substring."""
        errors: dict[str, str] = {}

        if user_input is not None:
            if err := await _test_direct_connection(self.hass, user_input):
                errors["base"] = err
            else:
                try:
                    devices = await self._search_devices(user_input)
                except FlespiApiError as err:
                    _LOGGER.warning("flespi device search failed: %s", err)
                    errors["base"] = "cannot_connect"
                else:
                    if not devices:
                        errors[_SEARCH] = "no_matches"
                    else:
                        self._direct_creds = {
                            k: v for k, v in user_input.items() if k != _SEARCH
                        }
                        self._direct_devices = devices
                        return await self.async_step_direct_pick()

        return self.async_show_form(
            step_id="direct_search",
            data_schema=_build_search_schema(user_input or {}),
            errors=errors,
        )

    async def async_step_direct_pick(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Second search step: pick one of the devices returned from the query."""
        if user_input is not None:
            device_id = int(user_input["device"])
            device = next(
                (d for d in self._direct_devices if d.get("id") == device_id), None
            )
            if device is None:
                return self.async_abort(reason="already_configured")

            dev_id = (
                slugify(device.get("name") or "") or f"device_{device_id}"
            )
            topic = f"flespi/message/gw/devices/{device_id}/#"
            await self.async_set_unique_id(f"{DOMAIN}_{dev_id}")
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=dev_id,
                data={
                    **self._direct_creds,
                    CONF_MODE: MODE_DIRECT,
                    "dev_id": dev_id,
                    "topic": topic,
                },
            )

        options = {
            str(d["id"]): _format_device_label(d)
            for d in self._direct_devices
            if "id" in d
        }
        schema = vol.Schema({vol.Required("device"): vol.In(options)})
        limit_note = (
            "max_limit" if len(self._direct_devices) >= 50 else None
        )
        return self.async_show_form(
            step_id="direct_pick",
            data_schema=schema,
            description_placeholders={"count": str(len(self._direct_devices))},
            errors={"base": limit_note} if limit_note else {},
        )

    async def _search_devices(
        self, user_input: Mapping[str, Any]
    ) -> list[dict[str, Any]]:
        client = FlespiRestClient(self.hass, user_input[CONF_TOKEN])
        return await client.search_devices(user_input[_SEARCH], limit=50)

    async def _async_step_create(
        self, mode: str, user_input: dict[str, Any] | None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            if err := _validate_topic(user_input["topic"]):
                errors["topic"] = err

            if mode == MODE_DIRECT and not errors:
                if err := await _test_direct_connection(self.hass, user_input):
                    errors["base"] = err

            if not errors:
                dev_id = user_input["dev_id"]
                await self.async_set_unique_id(f"{DOMAIN}_{dev_id}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=dev_id,
                    data={CONF_MODE: mode, **user_input},
                )

        return self.async_show_form(
            step_id=_STEP_ID_FOR_MODE[mode],
            data_schema=_build_schema(mode, user_input or {}),
            errors=errors,
        )

    async def async_step_import(
        self, import_data: dict[str, Any]
    ) -> FlowResult:
        """Handle import from YAML configuration (HA-MQTT mode)."""
        dev_id = import_data["dev_id"]

        await self.async_set_unique_id(f"{DOMAIN}_{dev_id}")
        self._abort_if_unique_id_configured()

        return self.async_create_entry(
            title=dev_id,
            data={
                CONF_MODE: MODE_HA_MQTT,
                "dev_id": dev_id,
                "topic": import_data["topic"],
            },
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle reconfiguration of an existing entry."""
        entry: ConfigEntry = self._get_reconfigure_entry()
        mode: str = entry.data[CONF_MODE]
        errors: dict[str, str] = {}

        if user_input is not None:
            if err := _validate_topic(user_input["topic"]):
                errors["topic"] = err

            if mode == MODE_DIRECT and not errors:
                if err := await _test_direct_connection(self.hass, user_input):
                    errors["base"] = err

            if not errors:
                new_dev_id = user_input["dev_id"]
                new_unique_id = f"{DOMAIN}_{new_dev_id}"
                if new_unique_id != entry.unique_id:
                    await self.async_set_unique_id(new_unique_id)
                    self._abort_if_unique_id_configured()

                return self.async_update_reload_and_abort(
                    entry,
                    title=new_dev_id,
                    data={CONF_MODE: mode, **user_input},
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_build_schema(mode, user_input or entry.data),
            errors=errors,
        )
