"""The Dynamic Dimming integration."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv

from .const import (
    ATTR_DIRECTION,
    ATTR_RATE,
    ATTR_STEP_PCT,
    DEFAULT_STEP_PCT,
    DIRECTION_DOWN,
    DIRECTION_UP,
    DOMAIN,
    SERVICE_MOVE,
    SERVICE_STEP,
    SERVICE_STOP,
)
from .controller import DimmingController

_DIRECTION = vol.In([DIRECTION_UP, DIRECTION_DOWN])

_MOVE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Required(ATTR_DIRECTION): _DIRECTION,
        vol.Optional(ATTR_RATE): vol.Any(vol.Coerce(float), cv.string),
    }
)
_STOP_SCHEMA = vol.Schema({vol.Required(ATTR_ENTITY_ID): cv.entity_id})
_STEP_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Required(ATTR_DIRECTION): _DIRECTION,
        vol.Optional(ATTR_STEP_PCT, default=DEFAULT_STEP_PCT): vol.Coerce(float),
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Dynamic Dimming from a config entry."""
    controller = DimmingController(hass)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = controller

    async def _move(call: ServiceCall) -> None:
        await controller.async_move(
            call.data[ATTR_ENTITY_ID],
            call.data[ATTR_DIRECTION],
            call.data.get(ATTR_RATE),
        )

    async def _stop(call: ServiceCall) -> None:
        await controller.async_stop(call.data[ATTR_ENTITY_ID])

    async def _step(call: ServiceCall) -> None:
        await controller.async_step(
            call.data[ATTR_ENTITY_ID],
            call.data[ATTR_DIRECTION],
            call.data[ATTR_STEP_PCT],
        )

    hass.services.async_register(DOMAIN, SERVICE_MOVE, _move, schema=_MOVE_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_STOP, _stop, schema=_STOP_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_STEP, _step, schema=_STEP_SCHEMA)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    for service in (SERVICE_MOVE, SERVICE_STOP, SERVICE_STEP):
        hass.services.async_remove(DOMAIN, service)
    hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return True
