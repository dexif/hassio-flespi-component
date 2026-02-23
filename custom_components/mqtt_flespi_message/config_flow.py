"""Config flow for mqtt_flespi_message integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow
from homeassistant.data_entry_flow import FlowResult

from .const import DOMAIN


class MqttFlespiMessageConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for mqtt_flespi_message."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the user configuration step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            dev_id = user_input["dev_id"]
            topic = user_input["topic"]

            await self.async_set_unique_id(f"{DOMAIN}_{dev_id}")
            self._abort_if_unique_id_configured()

            if not topic or "#" in topic:
                errors["topic"] = "invalid_topic"

            if not errors:
                return self.async_create_entry(
                    title=dev_id,
                    data={"dev_id": dev_id, "topic": topic},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required("dev_id"): str,
                    vol.Required("topic"): str,
                }
            ),
            errors=errors,
        )

    async def async_step_import(
        self, import_data: dict[str, Any]
    ) -> FlowResult:
        """Handle import from YAML configuration."""
        dev_id = import_data["dev_id"]
        topic = import_data["topic"]

        await self.async_set_unique_id(f"{DOMAIN}_{dev_id}")
        self._abort_if_unique_id_configured()

        return self.async_create_entry(
            title=dev_id,
            data={"dev_id": dev_id, "topic": topic},
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle reconfiguration of an existing entry."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            dev_id = user_input["dev_id"]
            topic = user_input["topic"]

            if not topic or "#" in topic:
                errors["topic"] = "invalid_topic"

            if not errors:
                new_unique_id = f"{DOMAIN}_{dev_id}"
                if new_unique_id != entry.unique_id:
                    await self.async_set_unique_id(new_unique_id)
                    self._abort_if_unique_id_configured()

                return self.async_update_reload_and_abort(
                    entry,
                    title=dev_id,
                    data={"dev_id": dev_id, "topic": topic},
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required("dev_id", default=entry.data["dev_id"]): str,
                    vol.Required("topic", default=entry.data["topic"]): str,
                }
            ),
            errors=errors,
        )
