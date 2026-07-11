"""Config flow for Dynamic Dimming."""

from __future__ import annotations

from homeassistant.config_entries import ConfigFlow

from .const import DOMAIN


class DynamicDimmingConfigFlow(ConfigFlow, domain=DOMAIN):
    """User-initiated config flow."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        if user_input is not None:
            return self.async_create_entry(title="Dynamic Dimming", data={})
        return self.async_show_form(step_id="user")
