"""Classify how an entity can be dimmed."""

from __future__ import annotations

from homeassistant.components.light import ColorMode
from homeassistant.core import HomeAssistant, split_entity_id

from .const import DimmingClass

# Color modes that do NOT carry a brightness channel.
_NON_BRIGHTNESS_MODES = {ColorMode.ONOFF, ColorMode.UNKNOWN}


def classify(hass: HomeAssistant, entity_id: str) -> DimmingClass:
    """Return how ``entity_id`` can be driven for continuous dimming.

    v0.1a: any brightness-capable ``light`` is SIMULATED; everything else is
    UNSUPPORTED. NATIVE is reserved for future backends that claim an entity.
    """
    if split_entity_id(entity_id)[0] != "light":
        return DimmingClass.UNSUPPORTED

    state = hass.states.get(entity_id)
    if state is None:
        return DimmingClass.UNSUPPORTED

    modes = set(state.attributes.get("supported_color_modes") or [])
    if modes - _NON_BRIGHTNESS_MODES:
        return DimmingClass.SIMULATED
    return DimmingClass.UNSUPPORTED
