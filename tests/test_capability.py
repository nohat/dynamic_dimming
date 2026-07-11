"""Tests for the capability probe."""

from __future__ import annotations

from custom_components.dynamic_dimming.capability import classify
from custom_components.dynamic_dimming.const import DimmingClass

from .conftest import set_light_state


async def test_brightness_light_is_simulated(hass):
    set_light_state(hass, "light.lamp", brightness=128, color_modes=("brightness",))
    assert classify(hass, "light.lamp") is DimmingClass.SIMULATED


async def test_color_temp_light_is_simulated(hass):
    set_light_state(hass, "light.lamp", color_modes=("color_temp", "hs"))
    assert classify(hass, "light.lamp") is DimmingClass.SIMULATED


async def test_onoff_only_light_is_unsupported(hass):
    set_light_state(hass, "light.plug", color_modes=("onoff",))
    assert classify(hass, "light.plug") is DimmingClass.UNSUPPORTED


async def test_non_light_is_unsupported(hass):
    hass.states.async_set("switch.fan", "on", {})
    assert classify(hass, "switch.fan") is DimmingClass.UNSUPPORTED


async def test_missing_entity_is_unsupported(hass):
    assert classify(hass, "light.ghost") is DimmingClass.UNSUPPORTED


async def test_missing_supported_color_modes_attribute_is_unsupported(hass):
    hass.states.async_set("light.legacy", "on", {})
    assert classify(hass, "light.legacy") is DimmingClass.UNSUPPORTED
