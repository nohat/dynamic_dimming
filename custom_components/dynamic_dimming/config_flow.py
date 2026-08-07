"""Config flow for Dynamic Dimming."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import callback

from .const import (
    CONF_CURVE,
    CONF_MIN_BRIGHTNESS,
    CONF_Z2M_BASE_TOPIC,
    DEFAULT_MIN_BRIGHTNESS,
    DEFAULT_Z2M_BASE_TOPIC,
    DOMAIN,
)
from .curve import CURVE_LINEAR, CURVE_PERCEPTUAL


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
    """Options: Zigbee2MQTT topic, and the shape of a stepped ramp."""

    async def async_step_init(self, user_input=None):
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_Z2M_BASE_TOPIC,
                        default=options.get(
                            CONF_Z2M_BASE_TOPIC, DEFAULT_Z2M_BASE_TOPIC
                        ),
                    ): str,
                    vol.Optional(
                        CONF_CURVE,
                        default=options.get(CONF_CURVE, CURVE_PERCEPTUAL),
                    ): vol.In([CURVE_PERCEPTUAL, CURVE_LINEAR]),
                    # Raise this to a device's real floor when the bottom of a
                    # hold produces no visible change: WiZ bulbs, for instance,
                    # report minDimLevel 10 (~26 on the 0-255 scale), below
                    # which they are not specified to do anything useful.
                    vol.Optional(
                        CONF_MIN_BRIGHTNESS,
                        default=options.get(
                            CONF_MIN_BRIGHTNESS, DEFAULT_MIN_BRIGHTNESS
                        ),
                    ): vol.All(vol.Coerce(int), vol.Range(min=1, max=200)),
                }
            ),
        )
