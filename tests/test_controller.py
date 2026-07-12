"""Tests for the dimming controller's routing, override, and job registry."""

from __future__ import annotations

import pytest
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dynamic_dimming.const import (
    BACKEND_NATIVE,
    BACKEND_SIMULATED,
    DIRECTION_DOWN,
    DIRECTION_UP,
    DOMAIN,
)
from custom_components.dynamic_dimming.controller import DimmingController

from .conftest import register_device_light, set_light_state

IEEE = "0x588e81fffe512c62"


def _controller(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    return DimmingController(hass, entry)


def _z2m_light(hass):
    return register_device_light(
        hass, "strip", domain="mqtt", identifiers={("mqtt", f"zigbee2mqtt_{IEEE}")}
    )


async def test_move_on_unsupported_is_noop(hass):
    hass.states.async_set("switch.fan", "on", {})
    controller = _controller(hass)
    await controller.async_move("switch.fan", DIRECTION_UP, "medium")
    assert "switch.fan" not in controller._jobs


async def test_move_registers_a_job(hass):
    set_light_state(hass, "light.lamp", brightness=100)
    controller = _controller(hass)
    await controller.async_move("light.lamp", DIRECTION_UP, "medium")
    assert "light.lamp" in controller._jobs


async def test_second_move_supersedes_the_first(hass):
    set_light_state(hass, "light.lamp", brightness=100)
    controller = _controller(hass)
    await controller.async_move("light.lamp", DIRECTION_UP, "medium")
    first = controller._jobs["light.lamp"]
    await controller.async_move("light.lamp", DIRECTION_DOWN, "medium")
    second = controller._jobs["light.lamp"]
    assert first is not second


async def test_stop_clears_the_job(hass):
    set_light_state(hass, "light.lamp", brightness=100)
    controller = _controller(hass)
    await controller.async_move("light.lamp", DIRECTION_UP, "medium")
    await controller.async_stop("light.lamp")
    assert "light.lamp" not in controller._jobs


async def test_stop_without_active_job_is_safe(hass):
    set_light_state(hass, "light.lamp", brightness=100)
    controller = _controller(hass)
    await controller.async_stop("light.lamp")  # must not raise
    assert "light.lamp" not in controller._jobs


async def test_native_move_publishes_and_stores_no_job(hass, mqtt_mock):
    entity_id = _z2m_light(hass)
    controller = _controller(hass)
    await controller.async_move(entity_id, DIRECTION_UP, "medium")
    assert entity_id not in controller._jobs
    mqtt_mock.async_publish.assert_called_once_with(
        f"zigbee2mqtt/{IEEE}/set", '{"brightness_move": 90}', 0, False
    )


async def test_override_simulated_forces_tick_path(hass, mqtt_mock):
    entity_id = _z2m_light(hass)
    controller = _controller(hass)
    await controller.async_move(entity_id, DIRECTION_UP, "medium", BACKEND_SIMULATED)
    assert entity_id in controller._jobs
    mqtt_mock.async_publish.assert_not_called()


async def test_override_native_without_claimer_raises(hass):
    set_light_state(hass, "light.lamp", brightness=100)
    controller = _controller(hass)
    with pytest.raises(ServiceValidationError):
        await controller.async_move("light.lamp", DIRECTION_UP, None, BACKEND_NATIVE)


async def test_stop_is_belt_and_braces(hass, mqtt_mock):
    """Stop kills the sim job AND publishes native stop, however the move began."""
    entity_id = _z2m_light(hass)
    controller = _controller(hass)
    await controller.async_move(entity_id, DIRECTION_UP, "medium", BACKEND_SIMULATED)
    mqtt_mock.async_publish.reset_mock()
    await controller.async_stop(entity_id)
    assert entity_id not in controller._jobs
    mqtt_mock.async_publish.assert_called_once_with(
        f"zigbee2mqtt/{IEEE}/set", '{"brightness_move": "stop"}', 0, False
    )


async def test_stop_on_unclaimed_light_publishes_nothing(hass, mqtt_mock):
    set_light_state(hass, "light.lamp", brightness=100)
    controller = _controller(hass)
    await controller.async_stop("light.lamp")
    mqtt_mock.async_publish.assert_not_called()


async def test_native_step_routes_to_claimer(hass, mqtt_mock):
    entity_id = _z2m_light(hass)
    controller = _controller(hass)
    await controller.async_step(entity_id, DIRECTION_DOWN, 5.0)
    mqtt_mock.async_publish.assert_called_once_with(
        f"zigbee2mqtt/{IEEE}/set", '{"brightness_step": -13}', 0, False
    )


async def test_unload_cancels_active_jobs(hass):
    set_light_state(hass, "light.lamp", brightness=100)
    controller = _controller(hass)
    await controller.async_move("light.lamp", DIRECTION_UP, "medium")
    assert "light.lamp" in controller._jobs
    await controller.async_unload()
    assert controller._jobs == {}


async def test_simulated_override_then_native_move_cancels_sim_job(hass, mqtt_mock):
    """A sim move superseded by a plain native move must cancel the tick loop."""
    entity_id = _z2m_light(hass)
    controller = _controller(hass)
    await controller.async_move(entity_id, DIRECTION_UP, "medium", BACKEND_SIMULATED)
    assert entity_id in controller._jobs
    mqtt_mock.async_publish.reset_mock()
    await controller.async_move(entity_id, DIRECTION_UP, "medium")
    assert entity_id not in controller._jobs
    mqtt_mock.async_publish.assert_called_once_with(
        f"zigbee2mqtt/{IEEE}/set", '{"brightness_move": 90}', 0, False
    )
