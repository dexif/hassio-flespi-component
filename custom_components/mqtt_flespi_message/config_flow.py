"""Config flow for mqtt_flespi_message integration.

Schema v3 layout:
- Main ConfigEntry: one per (mode, connection creds). Title: "Flespi (<host>)"
  or "Flespi (via Home Assistant MQTT)". data: {mode, [token, host, port, use_tls, protocol]}.
- ConfigSubentry of type "device": one per flespi device, owned by a main entry.
  data: {dev_id, topic, auto_discovery}.

The main flow (add integration) collects the connection creds AND the first
device in a single pass, then creates a main entry with that device as an
initial subentry. Additional devices are added via the per-entry
"Add device" action, which drives ConfigSubentryFlow subclasses.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import uuid
from typing import Any, Mapping

import voluptuous as vol

from homeassistant.components.mqtt import valid_subscribe_topic
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigSubentry,
    ConfigSubentryData,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.util import slugify

from .api import FlespiApiError, FlespiRestClient
from .const import (
    CONF_AUTO_DISCOVERY,
    CONF_DEV_ID,
    CONF_FLESPI_DEVICE_ID,
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
    SUBENTRY_TYPE_DEVICE,
)
from .pool import build_direct_client

_LOGGER = logging.getLogger(__name__)

_SEARCH = "search"
_MIGRATE_KEY = "__migrate__"

_DEVICE_ID_RE = re.compile(r"flespi/(?:message|state)/gw/devices/(\d+)")


def _topic_for_device_id(device_id: int) -> str:
    return f"flespi/message/gw/devices/{device_id}/#"


def _device_id_from_topic(topic: str) -> int | None:
    match = _DEVICE_ID_RE.search(topic or "")
    return int(match.group(1)) if match else None


# ---- unique_id helpers -------------------------------------------------------


def main_unique_id_ha_mqtt() -> str:
    return "ha_mqtt"


def main_unique_id_direct(
    host: str, port: int, use_tls: bool, protocol: str, token: str
) -> str:
    raw = f"{host}|{port}|{int(use_tls)}|{protocol}|{token}"
    digest = hashlib.sha1(raw.encode()).hexdigest()[:12]
    return f"direct:{digest}"


def main_title_for(main_data: Mapping[str, Any]) -> str:
    if main_data.get(CONF_MODE) == MODE_HA_MQTT:
        return "Flespi (via Home Assistant MQTT)"
    host = main_data.get(CONF_HOST, DEFAULT_HOST)
    return f"Flespi ({host})"


# ---- shared validation / schemas --------------------------------------------


def _validate_topic(topic: str) -> str | None:
    if not topic:
        return "invalid_topic"
    try:
        valid_subscribe_topic(topic)
    except vol.Invalid:
        return "invalid_topic"
    return None


def _device_schema(defaults: Mapping[str, Any], mode: str) -> vol.Schema:
    """Device form schema.

    For `MODE_DIRECT`: user enters a flespi numeric device_id — the topic is
    auto-built as `flespi/message/gw/devices/{id}/#` on submit. For
    `MODE_HA_MQTT`: user enters the raw MQTT topic (custom bridges may use
    any prefix, so we don't dictate the format).
    """

    def d(key: str, fallback: Any = vol.UNDEFINED) -> Any:
        val = defaults.get(key)
        return val if val is not None else fallback

    fields: dict[Any, Any] = {
        vol.Required(CONF_DEV_ID, default=d(CONF_DEV_ID)): str,
    }
    if mode == MODE_DIRECT:
        fields[
            vol.Required(CONF_FLESPI_DEVICE_ID, default=d(CONF_FLESPI_DEVICE_ID))
        ] = int
        fields[
            vol.Optional(CONF_AUTO_DISCOVERY, default=d(CONF_AUTO_DISCOVERY, True))
        ] = bool
    else:
        fields[vol.Required(CONF_TOPIC, default=d(CONF_TOPIC))] = str
    return vol.Schema(fields)


def _token_schema(defaults: Mapping[str, Any]) -> vol.Schema:
    """Schema for direct-mode connection (only token exposed; rest uses defaults)."""
    return vol.Schema(
        {
            vol.Required(CONF_TOKEN, default=defaults.get(CONF_TOKEN, vol.UNDEFINED)): str,
        }
    )


def _search_schema(defaults: Mapping[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(_SEARCH, default=defaults.get(_SEARCH, vol.UNDEFINED)): str,
        }
    )


# ---- connection test (unchanged logic, moved here) ---------------------------


async def _test_direct_connection(hass, token: str) -> str | None:
    """Probe mqtt.flespi.io with `token`. Returns an error key or None on success.

    Host/port/TLS/protocol are hardcoded to the flespi defaults — these are
    hidden from the UI per the 0.4.0 design.
    """
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

    # build_direct_client calls tls_set() which does blocking disk I/O;
    # run in executor to stay off the event loop.
    client = await hass.async_add_executor_job(
        build_direct_client,
        f"ha-{DOMAIN}-test-{uuid.uuid4().hex[:8]}",
        token,
        True,
        DEFAULT_PROTOCOL,
    )
    client.on_connect = _on_connect

    try:
        client.connect_async(DEFAULT_HOST, DEFAULT_PORT_TLS, keepalive=30)
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


# ---- REST search helper -----------------------------------------------------


async def _search_devices(hass, token: str, query: str) -> list[dict[str, Any]]:
    client = FlespiRestClient(hass, token)
    return await client.search_devices(query, limit=50)


def _format_device_label(device: Mapping[str, Any]) -> str:
    name = device.get("name") or "—"
    ident = (device.get("configuration") or {}).get("ident") or "—"
    return f'{name} (id={device.get("id")}, ident={ident})'


def _device_subentry_data_from_device(
    device: Mapping[str, Any], auto_discovery: bool
) -> dict[str, Any]:
    dev_id_raw = device.get("name") or ""
    device_id = device["id"]
    dev_id = slugify(dev_id_raw) or f"device_{device_id}"
    return {
        CONF_DEV_ID: dev_id,
        CONF_TOPIC: f"flespi/message/gw/devices/{device_id}/#",
        CONF_AUTO_DISCOVERY: auto_discovery,
    }


# =============================================================================
# Main ConfigFlow
# =============================================================================


class MqttFlespiMessageConfigFlow(ConfigFlow, domain=DOMAIN):
    """Main flow: pick a connection mode and add the first device under it."""

    VERSION = 3

    def __init__(self) -> None:
        super().__init__()
        # Transient state carried across search steps in direct mode.
        self._direct_token: str | None = None
        self._direct_auto_discovery: bool = True
        self._direct_search_results: list[dict[str, Any]] = []

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Expose the "device" subentry type so users can add more devices."""
        return {SUBENTRY_TYPE_DEVICE: DeviceSubentryFlow}

    # ---- top-level menu -----------------------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        return self.async_show_menu(
            step_id="user",
            menu_options=[MODE_HA_MQTT, MODE_DIRECT],
        )

    # ---- HA-MQTT path: one step adds first device + singleton main entry ----

    async def async_step_ha_mqtt(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if err := _validate_topic(user_input[CONF_TOPIC]):
                errors[CONF_TOPIC] = err
            if not errors:
                await self.async_set_unique_id(main_unique_id_ha_mqtt())
                self._abort_if_unique_id_configured()
                first_device = ConfigSubentryData(
                    subentry_type=SUBENTRY_TYPE_DEVICE,
                    title=user_input[CONF_DEV_ID],
                    unique_id=user_input[CONF_DEV_ID],
                    data={
                        CONF_DEV_ID: user_input[CONF_DEV_ID],
                        CONF_TOPIC: user_input[CONF_TOPIC],
                        CONF_AUTO_DISCOVERY: False,  # N/A for HA-MQTT mode
                    },
                )
                return self.async_create_entry(
                    title=main_title_for({CONF_MODE: MODE_HA_MQTT}),
                    data={CONF_MODE: MODE_HA_MQTT},
                    subentries=[first_device],
                )
        return self.async_show_form(
            step_id=MODE_HA_MQTT,
            data_schema=_device_schema(user_input or {}, mode=MODE_HA_MQTT),
            errors=errors,
        )

    # ---- Direct path: menu (search vs manual) → first device → main entry ---

    async def async_step_direct(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        return self.async_show_menu(
            step_id="direct",
            menu_options=["direct_search", "direct_manual"],
        )

    async def async_step_direct_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Direct mode, manual device entry: token + dev_id + flespi device_id."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if err := await _test_direct_connection(self.hass, user_input[CONF_TOKEN]):
                errors["base"] = err
            if not errors:
                device_id = int(user_input[CONF_FLESPI_DEVICE_ID])
                topic = _topic_for_device_id(device_id)
                main_data = _direct_main_data(user_input[CONF_TOKEN])
                uid = main_unique_id_direct(
                    DEFAULT_HOST, DEFAULT_PORT_TLS, True, DEFAULT_PROTOCOL,
                    user_input[CONF_TOKEN],
                )
                await self.async_set_unique_id(uid)
                self._abort_if_unique_id_configured()
                first_device = ConfigSubentryData(
                    subentry_type=SUBENTRY_TYPE_DEVICE,
                    title=user_input[CONF_DEV_ID],
                    unique_id=user_input[CONF_DEV_ID],
                    data={
                        CONF_DEV_ID: user_input[CONF_DEV_ID],
                        CONF_TOPIC: topic,
                        CONF_AUTO_DISCOVERY: user_input.get(CONF_AUTO_DISCOVERY, True),
                    },
                )
                return self.async_create_entry(
                    title=main_title_for(main_data),
                    data=main_data,
                    subentries=[first_device],
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_TOKEN, default=(user_input or {}).get(CONF_TOKEN, vol.UNDEFINED)): str,
                vol.Required(CONF_DEV_ID, default=(user_input or {}).get(CONF_DEV_ID, vol.UNDEFINED)): str,
                vol.Required(
                    CONF_FLESPI_DEVICE_ID,
                    default=(user_input or {}).get(CONF_FLESPI_DEVICE_ID, vol.UNDEFINED),
                ): int,
                vol.Optional(
                    CONF_AUTO_DISCOVERY,
                    default=(user_input or {}).get(CONF_AUTO_DISCOVERY, True),
                ): bool,
            }
        )
        return self.async_show_form(step_id="direct_manual", data_schema=schema, errors=errors)

    async def async_step_direct_search(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            token = user_input[CONF_TOKEN]
            self._direct_auto_discovery = user_input.get(CONF_AUTO_DISCOVERY, True)
            if err := await _test_direct_connection(self.hass, token):
                errors["base"] = err
            else:
                try:
                    devices = await _search_devices(self.hass, token, user_input[_SEARCH])
                except FlespiApiError as err:
                    _LOGGER.warning("flespi device search failed: %s", err)
                    errors["base"] = "cannot_connect"
                else:
                    if not devices:
                        errors[_SEARCH] = "no_matches"
                    else:
                        self._direct_token = token
                        self._direct_search_results = devices
                        return await self.async_step_direct_pick()

        schema = vol.Schema(
            {
                vol.Required(CONF_TOKEN, default=(user_input or {}).get(CONF_TOKEN, vol.UNDEFINED)): str,
                vol.Required(_SEARCH, default=(user_input or {}).get(_SEARCH, vol.UNDEFINED)): str,
                vol.Optional(
                    CONF_AUTO_DISCOVERY,
                    default=(user_input or {}).get(CONF_AUTO_DISCOVERY, True),
                ): bool,
            }
        )
        return self.async_show_form(step_id="direct_search", data_schema=schema, errors=errors)

    async def async_step_direct_pick(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            device_id = int(user_input["device"])
            device = next(
                (d for d in self._direct_search_results if d.get("id") == device_id), None
            )
            if device is None:
                return self.async_abort(reason="already_configured")

            sub_data = _device_subentry_data_from_device(
                device, self._direct_auto_discovery
            )
            token = self._direct_token or ""
            uid = main_unique_id_direct(
                DEFAULT_HOST, DEFAULT_PORT_TLS, True, DEFAULT_PROTOCOL, token
            )
            await self.async_set_unique_id(uid)
            self._abort_if_unique_id_configured()
            main_data = _direct_main_data(token)
            first_device = ConfigSubentryData(
                subentry_type=SUBENTRY_TYPE_DEVICE,
                title=sub_data[CONF_DEV_ID],
                unique_id=sub_data[CONF_DEV_ID],
                data=sub_data,
            )
            return self.async_create_entry(
                title=main_title_for(main_data),
                data=main_data,
                subentries=[first_device],
            )

        options = {
            str(d["id"]): _format_device_label(d)
            for d in self._direct_search_results
            if "id" in d
        }
        schema = vol.Schema({vol.Required("device"): vol.In(options)})
        return self.async_show_form(
            step_id="direct_pick",
            data_schema=schema,
            description_placeholders={"count": str(len(self._direct_search_results))},
        )

    # ---- import: YAML legacy + migration from v<3 entries -------------------

    async def async_step_import(
        self, import_data: dict[str, Any]
    ) -> FlowResult:
        if import_data.get(_MIGRATE_KEY):
            return await self._async_step_migration_import(import_data)
        # Legacy YAML: create a v<3-shaped entry; the next restart's migration
        # pass will fold it into the proper subentry model.
        dev_id = import_data["dev_id"]
        await self.async_set_unique_id(f"{DOMAIN}_legacy_{dev_id}")
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=main_title_for({CONF_MODE: MODE_HA_MQTT}),
            data={CONF_MODE: MODE_HA_MQTT},
            subentries=[
                ConfigSubentryData(
                    subentry_type=SUBENTRY_TYPE_DEVICE,
                    title=dev_id,
                    unique_id=dev_id,
                    data={
                        CONF_DEV_ID: dev_id,
                        CONF_TOPIC: import_data["topic"],
                        CONF_AUTO_DISCOVERY: False,
                    },
                )
            ],
        )

    async def _async_step_migration_import(
        self, import_data: dict[str, Any]
    ) -> FlowResult:
        uid = import_data["main_unique_id"]
        await self.async_set_unique_id(uid)
        # If the main entry already exists, we merge via async_add_subentry
        # in the migration helper — so the flow is only expected to run when
        # the main entry doesn't yet exist.
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=import_data["main_title"],
            data=import_data["main_data"],
            subentries=import_data["subentries_data"],
        )

    # ---- reconfigure: edit main entry creds --------------------------------

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Reconfigure the main entry (only token is editable for direct mode)."""
        entry: ConfigEntry = self._get_reconfigure_entry()
        mode = entry.data.get(CONF_MODE, MODE_HA_MQTT)

        if mode == MODE_HA_MQTT:
            # Nothing to reconfigure for HA-MQTT — it's a placeholder entry.
            return self.async_abort(reason="nothing_to_configure")

        errors: dict[str, str] = {}
        if user_input is not None:
            if err := await _test_direct_connection(self.hass, user_input[CONF_TOKEN]):
                errors["base"] = err
            else:
                new_data = {**entry.data, CONF_TOKEN: user_input[CONF_TOKEN]}
                # Token change → unique_id changes too; only update if different.
                new_uid = main_unique_id_direct(
                    new_data.get(CONF_HOST, DEFAULT_HOST),
                    new_data.get(CONF_PORT, DEFAULT_PORT_TLS),
                    new_data.get(CONF_USE_TLS, True),
                    new_data.get(CONF_PROTOCOL, DEFAULT_PROTOCOL),
                    new_data[CONF_TOKEN],
                )
                if new_uid != entry.unique_id:
                    await self.async_set_unique_id(new_uid)
                    self._abort_if_unique_id_configured()
                return self.async_update_reload_and_abort(
                    entry, data=new_data
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_token_schema(user_input or entry.data),
            errors=errors,
        )


def _direct_main_data(token: str) -> dict[str, Any]:
    """Canonical direct-mode main-entry data with hidden defaults for host/port/tls/protocol."""
    return {
        CONF_MODE: MODE_DIRECT,
        CONF_TOKEN: token,
        CONF_HOST: DEFAULT_HOST,
        CONF_PORT: DEFAULT_PORT_TLS,
        CONF_USE_TLS: True,
        CONF_PROTOCOL: DEFAULT_PROTOCOL,
    }


# =============================================================================
# Device Subentry Flow — add / reconfigure a device under an existing main entry
# =============================================================================


class DeviceSubentryFlow(ConfigSubentryFlow):
    """Add / edit a flespi device within an existing connection main entry."""

    def __init__(self) -> None:
        super().__init__()
        self._search_results: list[dict[str, Any]] = []

    def _main_entry(self) -> ConfigEntry:
        # ConfigSubentryFlow exposes the parent entry via self._get_entry() helper
        # (matches the convention of self._get_reconfigure_entry() on ConfigFlow).
        return self._get_entry()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        entry = self._main_entry()
        mode = entry.data.get(CONF_MODE, MODE_HA_MQTT)
        if mode == MODE_HA_MQTT:
            return await self.async_step_manual()
        return self.async_show_menu(
            step_id="user",
            menu_options=["search", "manual"],
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        entry = self._main_entry()
        mode = entry.data.get(CONF_MODE, MODE_HA_MQTT)
        errors: dict[str, str] = {}

        if user_input is not None:
            topic = _resolve_topic(user_input, mode)
            if err := _validate_topic(topic):
                errors[CONF_TOPIC] = err
            if not errors:
                dev_id = user_input[CONF_DEV_ID]
                data = {
                    CONF_DEV_ID: dev_id,
                    CONF_TOPIC: topic,
                    CONF_AUTO_DISCOVERY: user_input.get(
                        CONF_AUTO_DISCOVERY, False if mode == MODE_HA_MQTT else True
                    ),
                }
                return self.async_create_entry(
                    title=dev_id,
                    data=data,
                    unique_id=dev_id,
                )

        return self.async_show_form(
            step_id="manual",
            data_schema=_device_schema(user_input or {}, mode=mode),
            errors=errors,
        )

    async def async_step_search(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        entry = self._main_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            token = entry.data[CONF_TOKEN]
            try:
                devices = await _search_devices(self.hass, token, user_input[_SEARCH])
            except FlespiApiError as err:
                _LOGGER.warning("subentry device search failed: %s", err)
                errors["base"] = "cannot_connect"
            else:
                if not devices:
                    errors[_SEARCH] = "no_matches"
                else:
                    self._search_results = devices
                    return await self.async_step_pick()
        return self.async_show_form(
            step_id="search",
            data_schema=_search_schema(user_input or {}),
            errors=errors,
        )

    async def async_step_pick(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        if user_input is not None:
            device_id = int(user_input["device"])
            device = next(
                (d for d in self._search_results if d.get("id") == device_id), None
            )
            if device is None:
                return self.async_abort(reason="already_configured")
            sub_data = _device_subentry_data_from_device(device, auto_discovery=True)
            return self.async_create_entry(
                title=sub_data[CONF_DEV_ID],
                data=sub_data,
                unique_id=sub_data[CONF_DEV_ID],
            )

        options = {
            str(d["id"]): _format_device_label(d)
            for d in self._search_results
            if "id" in d
        }
        schema = vol.Schema({vol.Required("device"): vol.In(options)})
        return self.async_show_form(
            step_id="pick",
            data_schema=schema,
            description_placeholders={"count": str(len(self._search_results))},
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Edit an existing device subentry: dev_id, device_id/topic, auto_discovery."""
        subentry: ConfigSubentry = self._get_reconfigure_subentry()
        entry = self._main_entry()
        mode = entry.data.get(CONF_MODE, MODE_HA_MQTT)
        errors: dict[str, str] = {}

        if user_input is not None:
            topic = _resolve_topic(user_input, mode)
            if err := _validate_topic(topic):
                errors[CONF_TOPIC] = err
            if not errors:
                dev_id = user_input[CONF_DEV_ID]
                data = {
                    CONF_DEV_ID: dev_id,
                    CONF_TOPIC: topic,
                    CONF_AUTO_DISCOVERY: user_input.get(
                        CONF_AUTO_DISCOVERY,
                        subentry.data.get(CONF_AUTO_DISCOVERY, False),
                    ),
                }
                return self.async_update_and_abort(
                    entry,
                    subentry,
                    title=dev_id,
                    data=data,
                    unique_id=dev_id,
                )

        # Pre-fill: for direct mode, derive device_id from the stored topic.
        defaults = dict(user_input or {})
        if not defaults:
            defaults[CONF_DEV_ID] = subentry.data.get(CONF_DEV_ID)
            defaults[CONF_AUTO_DISCOVERY] = subentry.data.get(CONF_AUTO_DISCOVERY, False)
            if mode == MODE_DIRECT:
                defaults[CONF_FLESPI_DEVICE_ID] = _device_id_from_topic(
                    subentry.data.get(CONF_TOPIC, "")
                )
            else:
                defaults[CONF_TOPIC] = subentry.data.get(CONF_TOPIC)

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_device_schema(defaults, mode=mode),
            errors=errors,
        )


def _resolve_topic(user_input: Mapping[str, Any], mode: str) -> str:
    """Turn form input into a concrete MQTT topic.

    Direct mode ignores any user-typed topic and builds one from device_id;
    HA-MQTT uses the user's raw topic verbatim.
    """
    if mode == MODE_DIRECT:
        return _topic_for_device_id(int(user_input[CONF_FLESPI_DEVICE_ID]))
    return user_input[CONF_TOPIC]
