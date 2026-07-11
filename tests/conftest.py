"""Shared fixtures for Dynamic Dimming tests."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading of the custom integration in every test."""
    yield


def set_light_state(hass, entity_id, *, brightness=None, color_modes=("brightness",), on=True):
    """Helper: register a light state with the given color-mode support."""
    attrs = {"supported_color_modes": list(color_modes)}
    if brightness is not None:
        attrs["brightness"] = brightness
    hass.states.async_set(entity_id, "on" if on else "off", attrs)
