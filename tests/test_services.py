"""Tests for service registration and dispatch."""

from __future__ import annotations

from unittest.mock import patch

import pytest
import voluptuous as vol
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dynamic_dimming.const import (
    DEFAULT_STEP_PCT,
    DOMAIN,
    SERVICE_MOVE,
    SERVICE_STEP,
    SERVICE_STOP,
)

from .conftest import set_light_state


async def _setup_entry(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_services_are_registered(hass):
    await _setup_entry(hass)
    assert hass.services.has_service(DOMAIN, SERVICE_MOVE)
    assert hass.services.has_service(DOMAIN, SERVICE_STOP)
    assert hass.services.has_service(DOMAIN, SERVICE_STEP)


async def test_move_service_dispatches_to_controller(hass):
    await _setup_entry(hass)
    set_light_state(hass, "light.lamp", brightness=100, color_modes=("brightness",))
    with patch(
        "custom_components.dynamic_dimming.controller.DimmingController.async_move"
    ) as mock_move:
        await hass.services.async_call(
            DOMAIN, SERVICE_MOVE,
            {"entity_id": "light.lamp", "direction": "up", "rate": "fast"},
            blocking=True,
        )
    mock_move.assert_awaited_once()
    args = mock_move.await_args.args
    assert args[0] == "light.lamp" and args[1] == "up" and args[2] == "fast"


async def test_stop_service_dispatches_to_controller(hass):
    await _setup_entry(hass)
    set_light_state(hass, "light.lamp", brightness=100, color_modes=("brightness",))
    with patch(
        "custom_components.dynamic_dimming.controller.DimmingController.async_stop"
    ) as mock_stop:
        await hass.services.async_call(
            DOMAIN, SERVICE_STOP, {"entity_id": "light.lamp"}, blocking=True
        )
    mock_stop.assert_awaited_once_with("light.lamp")


async def test_move_requires_direction(hass):
    await _setup_entry(hass)
    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            DOMAIN, SERVICE_MOVE, {"entity_id": "light.lamp"}, blocking=True
        )


async def test_step_service_dispatches_to_controller(hass):
    await _setup_entry(hass)
    set_light_state(hass, "light.lamp", brightness=100, color_modes=("brightness",))
    with patch(
        "custom_components.dynamic_dimming.controller.DimmingController.async_step"
    ) as mock_step:
        await hass.services.async_call(
            DOMAIN, SERVICE_STEP,
            {"entity_id": "light.lamp", "direction": "up", "step_pct": 15},
            blocking=True,
        )
    mock_step.assert_awaited_once_with("light.lamp", "up", 15.0)


async def test_step_service_uses_default_step_pct(hass):
    await _setup_entry(hass)
    set_light_state(hass, "light.lamp", brightness=100, color_modes=("brightness",))
    with patch(
        "custom_components.dynamic_dimming.controller.DimmingController.async_step"
    ) as mock_step:
        await hass.services.async_call(
            DOMAIN, SERVICE_STEP,
            {"entity_id": "light.lamp", "direction": "down"},
            blocking=True,
        )
    mock_step.assert_awaited_once()
    args = mock_step.await_args.args
    assert args[2] == DEFAULT_STEP_PCT


async def test_unload_entry_deregisters_services_and_drops_controller(hass):
    entry = await _setup_entry(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert not hass.services.has_service(DOMAIN, SERVICE_MOVE)
    assert not hass.services.has_service(DOMAIN, SERVICE_STOP)
    assert not hass.services.has_service(DOMAIN, SERVICE_STEP)
    assert entry.entry_id not in hass.data[DOMAIN]
