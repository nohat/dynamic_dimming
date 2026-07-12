"""Config flow for Dynamic Dimming."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import callback

from .const import CONF_Z2M_BASE_TOPIC, DEFAULT_Z2M_BASE_TOPIC, DOMAIN


class DynamicDimmingConfigFlow(ConfigFlow, domain=DOMAIN):
    """User-initiated config flow."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> DynamicDimmingOptionsFlow:
        """Return the options flow."""
        return DynamicDimmingOptionsFlow()

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        if user_input is not None:
            return self.async_create_entry(title="Dynamic Dimming", data={})
        return self.async_show_form(step_id="user")


class DynamicDimmingOptionsFlow(OptionsFlow):
    """Options: where the Zigbee2MQTT backend publishes."""

    async def async_step_init(self, user_input=None):
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_Z2M_BASE_TOPIC,
                        default=self.config_entry.options.get(
                            CONF_Z2M_BASE_TOPIC, DEFAULT_Z2M_BASE_TOPIC
                        ),
                    ): str,
                }
            ),
        )
