"""Tests for the dimming controller's routing and job registry."""

from __future__ import annotations

from custom_components.dynamic_dimming.const import DIRECTION_DOWN, DIRECTION_UP
from custom_components.dynamic_dimming.controller import DimmingController

from .conftest import set_light_state


async def test_move_on_unsupported_is_noop(hass):
    hass.states.async_set("switch.fan", "on", {})
    controller = DimmingController(hass)
    await controller.async_move("switch.fan", DIRECTION_UP, "medium")
    assert "switch.fan" not in controller._jobs


async def test_move_registers_a_job(hass):
    set_light_state(hass, "light.lamp", brightness=100, color_modes=("brightness",))
    controller = DimmingController(hass)
    await controller.async_move("light.lamp", DIRECTION_UP, "medium")
    assert "light.lamp" in controller._jobs


async def test_second_move_supersedes_the_first(hass):
    set_light_state(hass, "light.lamp", brightness=100, color_modes=("brightness",))
    controller = DimmingController(hass)
    await controller.async_move("light.lamp", DIRECTION_UP, "medium")
    first = controller._jobs["light.lamp"]
    await controller.async_move("light.lamp", DIRECTION_DOWN, "medium")
    second = controller._jobs["light.lamp"]
    assert first is not second


async def test_stop_clears_the_job(hass):
    set_light_state(hass, "light.lamp", brightness=100, color_modes=("brightness",))
    controller = DimmingController(hass)
    await controller.async_move("light.lamp", DIRECTION_UP, "medium")
    await controller.async_stop("light.lamp")
    assert "light.lamp" not in controller._jobs


async def test_stop_without_active_job_is_safe(hass):
    set_light_state(hass, "light.lamp", brightness=100, color_modes=("brightness",))
    controller = DimmingController(hass)
    await controller.async_stop("light.lamp")  # must not raise
    assert "light.lamp" not in controller._jobs
