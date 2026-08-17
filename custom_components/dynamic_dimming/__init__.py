"""The Dynamic Dimming integration."""

from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.target import (
    TargetSelection,
    async_extract_referenced_entity_ids,
)

from .const import (
    ATTR_BACKEND,
    ATTR_CURVE,
    ATTR_DIRECTION,
    ATTR_RATE,
    ATTR_STEP_PCT,
    BACKEND_AUTO,
    BACKEND_NATIVE,
    BACKEND_SIMULATED,
    DEFAULT_STEP_PCT,
    DIRECTION_DOWN,
    DIRECTION_UP,
    DOMAIN,
    SERVICE_MOVE,
    SERVICE_FADE,
    SERVICE_STEP,
    SERVICE_STOP,
)
from .controller import DimmingController

_LOGGER = logging.getLogger(__name__)

_DIRECTION = vol.In([DIRECTION_UP, DIRECTION_DOWN])

_BACKEND = vol.In([BACKEND_AUTO, BACKEND_SIMULATED, BACKEND_NATIVE])

# Entity-service schemas: every service takes a standard HA target, so one call
# may name one entity, several, or an area/device/label that resolves to several.
_MOVE_SCHEMA = cv.make_entity_service_schema(
    {
        vol.Required(ATTR_DIRECTION): _DIRECTION,
        vol.Optional(ATTR_RATE): vol.Any(vol.Coerce(float), cv.string),
        vol.Optional(ATTR_BACKEND, default=BACKEND_AUTO): _BACKEND,
        vol.Optional(ATTR_CURVE): vol.Any(vol.Coerce(float), cv.string),
    }
)
_STOP_SCHEMA = cv.make_entity_service_schema({})
_FADE_SCHEMA = cv.make_entity_service_schema(
    {
        vol.Required("brightness_pct"): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
        vol.Required("duration"): vol.All(vol.Coerce(float), vol.Range(min=0.1, max=120)),
        vol.Optional("color_temp_kelvin"): vol.All(
            vol.Coerce(int), vol.Range(min=1000, max=10000)
        ),
        vol.Optional("backend", default="auto"): vol.In(["auto", "native", "simulated"]),
        vol.Optional("curve"): cv.string,
    }
)

_STEP_SCHEMA = cv.make_entity_service_schema(
    {
        vol.Required(ATTR_DIRECTION): _DIRECTION,
        vol.Optional(ATTR_STEP_PCT, default=DEFAULT_STEP_PCT): vol.Coerce(float),
        vol.Optional(ATTR_BACKEND, default=BACKEND_AUTO): _BACKEND,
        vol.Optional(ATTR_CURVE): vol.Any(vol.Coerce(float), cv.string),
    }
)


def _targets(hass: HomeAssistant, call: ServiceCall) -> list[str]:
    """Resolve a call's target to the list of entities it names.

    ``expand_group=False`` is deliberate: a light group is an entity in its own
    right to this integration, and a backend may know how to drive a whole group
    with one command (the WiZ backend does). Expanding a group to its members
    here would take that choice away before the backend ever sees it.
    """
    selected = async_extract_referenced_entity_ids(
        hass, TargetSelection(call.data), expand_group=False
    )
    # Entities the caller named keep the order they were given in; area, device
    # and label expansion has no order of its own, so it is sorted for
    # determinism. `entity_id: all` / `none` match nothing and drop out here.
    named = [
        entity_id
        for entity_id in cv.ensure_list(call.data.get(ATTR_ENTITY_ID, []))
        if entity_id in selected.referenced
    ]
    entity_ids = named + sorted(selected.indirectly_referenced - selected.referenced)
    if not entity_ids:
        # Reachable through an empty area or `entity_id: all`, which resolves to
        # nothing here because this integration owns no entities of its own.
        _LOGGER.debug("%s call resolved to no entities: %s", call.service, call.data)
    return entity_ids


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Dynamic Dimming from a config entry."""
    controller = DimmingController(hass, entry)
    await controller.async_setup()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = controller

    async def _move(call: ServiceCall) -> None:
        await controller.async_move(
            _targets(hass, call),
            call.data[ATTR_DIRECTION],
            call.data.get(ATTR_RATE),
            call.data[ATTR_BACKEND],
            call.data.get(ATTR_CURVE),
        )

    async def _stop(call: ServiceCall) -> None:
        await controller.async_stop(_targets(hass, call))

    async def _step(call: ServiceCall) -> None:
        await controller.async_step(
            _targets(hass, call),
            call.data[ATTR_DIRECTION],
            call.data[ATTR_STEP_PCT],
            call.data[ATTR_BACKEND],
            call.data.get(ATTR_CURVE),
        )

    async def _fade(call: ServiceCall) -> None:
        await controller.async_fade(
            _targets(hass, call),
            int(call.data["brightness_pct"] * 255 / 100),
            float(call.data["duration"]),
            call.data.get("backend", "auto"),
            call.data.get("curve"),
            call.data.get("color_temp_kelvin"),
        )

    hass.services.async_register(DOMAIN, SERVICE_MOVE, _move, schema=_MOVE_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_STOP, _stop, schema=_STOP_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_STEP, _step, schema=_STEP_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_FADE, _fade, schema=_FADE_SCHEMA)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    for service in (SERVICE_MOVE, SERVICE_STOP, SERVICE_STEP, SERVICE_FADE):
        hass.services.async_remove(DOMAIN, service)
    controller = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if controller is not None:
        await controller.async_unload()
    return True
