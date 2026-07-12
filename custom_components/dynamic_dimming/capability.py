"""Classify how an entity can be dimmed."""

from __future__ import annotations

from collections.abc import Iterable

from homeassistant.components.light import ColorMode
from homeassistant.core import HomeAssistant, split_entity_id

from .const import DimmingClass

# Color modes that do NOT carry a brightness channel.
_NON_BRIGHTNESS_MODES = {ColorMode.ONOFF, ColorMode.UNKNOWN}


def classify(
    hass: HomeAssistant, entity_id: str, backends: Iterable = ()
) -> DimmingClass:
    """Return how ``entity_id`` can be driven for continuous dimming.

    ``backends`` is the ordered native-backend list; the brightness gate runs
    first, so a claiming backend can never rescue a non-dimmable light. With
    no claimer, any brightness-capable light is SIMULATED.
    """
    if split_entity_id(entity_id)[0] != "light":
        return DimmingClass.UNSUPPORTED

    state = hass.states.get(entity_id)
    if state is None:
        return DimmingClass.UNSUPPORTED

    modes = set(state.attributes.get("supported_color_modes") or [])
    if not (modes - _NON_BRIGHTNESS_MODES):
        return DimmingClass.UNSUPPORTED
    if any(backend.claims(entity_id) for backend in backends):
        return DimmingClass.NATIVE
    return DimmingClass.SIMULATED
