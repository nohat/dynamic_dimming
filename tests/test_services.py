"""Tests for service registration and dispatch."""

from __future__ import annotations

from unittest.mock import patch

import pytest
import voluptuous as vol
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dynamic_dimming.const import (
    ATTR_BACKEND,
    BACKEND_AUTO,
    BACKEND_SIMULATED,
    DEFAULT_STEP_PCT,
    DOMAIN,
    SERVICE_FADE,
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
    args = mock_move.await_args.args
    assert args == (["light.lamp"], "up", "fast", BACKEND_AUTO, None)


async def test_stop_service_dispatches_to_controller(hass):
    await _setup_entry(hass)
    set_light_state(hass, "light.lamp", brightness=100, color_modes=("brightness",))
    with patch(
        "custom_components.dynamic_dimming.controller.DimmingController.async_stop"
    ) as mock_stop:
        await hass.services.async_call(
            DOMAIN, SERVICE_STOP, {"entity_id": "light.lamp"}, blocking=True
        )
    mock_stop.assert_awaited_once_with(["light.lamp"])


async def test_move_requires_direction(hass):
    await _setup_entry(hass)
    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            DOMAIN, SERVICE_MOVE, {"entity_id": "light.lamp"}, blocking=True
        )


async def test_move_passes_backend_override(hass):
    await _setup_entry(hass)
    set_light_state(hass, "light.lamp", brightness=100, color_modes=("brightness",))
    with patch(
        "custom_components.dynamic_dimming.controller.DimmingController.async_move"
    ) as mock_move:
        await hass.services.async_call(
            DOMAIN, SERVICE_MOVE,
            {
                "entity_id": "light.lamp",
                "direction": "up",
                ATTR_BACKEND: BACKEND_SIMULATED,
            },
            blocking=True,
        )
    assert mock_move.await_args.args[3] == BACKEND_SIMULATED


async def test_move_rejects_unknown_backend(hass):
    await _setup_entry(hass)
    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            DOMAIN, SERVICE_MOVE,
            {"entity_id": "light.lamp", "direction": "up", ATTR_BACKEND: "warp"},
            blocking=True,
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
    mock_step.assert_awaited_once_with(["light.lamp"], "up", 15.0, BACKEND_AUTO, None)


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


async def test_step_passes_backend_override(hass):
    await _setup_entry(hass)
    set_light_state(hass, "light.lamp", brightness=100, color_modes=("brightness",))
    with patch(
        "custom_components.dynamic_dimming.controller.DimmingController.async_step"
    ) as mock_step:
        await hass.services.async_call(
            DOMAIN, SERVICE_STEP,
            {
                "entity_id": "light.lamp",
                "direction": "up",
                ATTR_BACKEND: BACKEND_SIMULATED,
            },
            blocking=True,
        )
    assert mock_step.await_args.args[3] == BACKEND_SIMULATED


async def test_unload_entry_deregisters_services_and_drops_controller(hass):
    entry = await _setup_entry(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert not hass.services.has_service(DOMAIN, SERVICE_MOVE)
    assert not hass.services.has_service(DOMAIN, SERVICE_STOP)
    assert not hass.services.has_service(DOMAIN, SERVICE_STEP)
    assert entry.entry_id not in hass.data[DOMAIN]


async def test_fade_service_dispatches_with_color_temp(hass):
    await _setup_entry(hass)
    set_light_state(hass, "light.lamp", brightness=100, color_modes=("color_temp",))
    with patch(
        "custom_components.dynamic_dimming.controller.DimmingController.async_fade"
    ) as mock_fade:
        await hass.services.async_call(
            DOMAIN, SERVICE_FADE,
            {"entity_id": "light.lamp", "brightness_pct": 100, "duration": 2,
             "color_temp_kelvin": 2700},
            blocking=True,
        )
    args = mock_fade.await_args.args
    assert args == (["light.lamp"], 255, 2.0, BACKEND_AUTO, None, 2700)


async def test_fade_service_color_temp_is_optional(hass):
    await _setup_entry(hass)
    set_light_state(hass, "light.lamp", brightness=100, color_modes=("color_temp",))
    with patch(
        "custom_components.dynamic_dimming.controller.DimmingController.async_fade"
    ) as mock_fade:
        await hass.services.async_call(
            DOMAIN, SERVICE_FADE,
            {"entity_id": "light.lamp", "brightness_pct": 50, "duration": 1},
            blocking=True,
        )
    assert mock_fade.await_args.args[-1] is None


async def test_fade_service_rejects_out_of_range_kelvin(hass):
    await _setup_entry(hass)
    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            DOMAIN, SERVICE_FADE,
            {"entity_id": "light.lamp", "brightness_pct": 50, "duration": 1,
             "color_temp_kelvin": 40000},
            blocking=True,
        )


# -- targeting: one entity, many entities, or an area -------------------------
#
# Every service takes a standard HA target. A caller passing a list is the
# common case, not an edge case: it is what `target:` in an automation and most
# generic API clients send, and rejecting it used to abort whole scripts.


def _two_lamps(hass):
    for entity_id in ("light.lamp", "light.sconce"):
        set_light_state(hass, entity_id, brightness=100, color_modes=("brightness",))
    return ["light.lamp", "light.sconce"]


async def test_move_accepts_a_list_of_entities(hass):
    await _setup_entry(hass)
    lamps = _two_lamps(hass)
    with patch(
        "custom_components.dynamic_dimming.controller.DimmingController.async_move"
    ) as mock_move:
        await hass.services.async_call(
            DOMAIN, SERVICE_MOVE,
            {"entity_id": lamps, "direction": "up"},
            blocking=True,
        )
    assert mock_move.await_args.args[0] == lamps


async def test_move_accepts_a_target_block(hass):
    """The `target:` form an automation writes, rather than data.entity_id."""
    await _setup_entry(hass)
    lamps = _two_lamps(hass)
    with patch(
        "custom_components.dynamic_dimming.controller.DimmingController.async_move"
    ) as mock_move:
        await hass.services.async_call(
            DOMAIN, SERVICE_MOVE,
            {"direction": "up"},
            blocking=True,
            target={"entity_id": lamps},
        )
    assert mock_move.await_args.args[0] == lamps


async def test_stop_accepts_a_list_of_entities(hass):
    await _setup_entry(hass)
    lamps = _two_lamps(hass)
    with patch(
        "custom_components.dynamic_dimming.controller.DimmingController.async_stop"
    ) as mock_stop:
        await hass.services.async_call(
            DOMAIN, SERVICE_STOP, {"entity_id": lamps}, blocking=True
        )
    mock_stop.assert_awaited_once_with(lamps)


async def test_step_accepts_a_list_of_entities(hass):
    await _setup_entry(hass)
    lamps = _two_lamps(hass)
    with patch(
        "custom_components.dynamic_dimming.controller.DimmingController.async_step"
    ) as mock_step:
        await hass.services.async_call(
            DOMAIN, SERVICE_STEP,
            {"entity_id": lamps, "direction": "down"},
            blocking=True,
        )
    assert mock_step.await_args.args[0] == lamps


async def test_fade_accepts_a_list_of_entities(hass):
    await _setup_entry(hass)
    lamps = _two_lamps(hass)
    with patch(
        "custom_components.dynamic_dimming.controller.DimmingController.async_fade"
    ) as mock_fade:
        await hass.services.async_call(
            DOMAIN, SERVICE_FADE,
            {"entity_id": lamps, "brightness_pct": 50, "duration": 1},
            blocking=True,
        )
    assert mock_fade.await_args.args[0] == lamps


async def test_target_order_is_the_order_given(hass):
    await _setup_entry(hass)
    _two_lamps(hass)
    reversed_lamps = ["light.sconce", "light.lamp"]
    with patch(
        "custom_components.dynamic_dimming.controller.DimmingController.async_move"
    ) as mock_move:
        await hass.services.async_call(
            DOMAIN, SERVICE_MOVE,
            {"entity_id": reversed_lamps, "direction": "up"},
            blocking=True,
        )
    assert mock_move.await_args.args[0] == reversed_lamps


async def test_move_resolves_an_area_target(hass):
    await _setup_entry(hass)
    area = ar.async_get(hass).async_create("Kitchen")
    registry = er.async_get(hass)
    for object_id in ("counter", "island"):
        entry = registry.async_get_or_create(
            "light", "demo", f"uid_{object_id}", suggested_object_id=object_id
        )
        registry.async_update_entity(entry.entity_id, area_id=area.id)
        set_light_state(hass, entry.entity_id, brightness=100)
    with patch(
        "custom_components.dynamic_dimming.controller.DimmingController.async_move"
    ) as mock_move:
        await hass.services.async_call(
            DOMAIN, SERVICE_MOVE,
            {"direction": "up"},
            blocking=True,
            target={"area_id": area.id},
        )
    assert mock_move.await_args.args[0] == ["light.counter", "light.island"]


async def test_group_entities_are_not_expanded(hass):
    """A group is an entity in its own right -- the WiZ backend drives one whole.

    Expanding it to members here would hand the backend two bulbs it then ramps
    from two separate jobs, which is exactly the drift the group path avoids.
    """
    await _setup_entry(hass)
    _two_lamps(hass)
    hass.states.async_set(
        "group.ceiling", "on", {"entity_id": ["light.lamp", "light.sconce"]}
    )
    with patch(
        "custom_components.dynamic_dimming.controller.DimmingController.async_move"
    ) as mock_move:
        await hass.services.async_call(
            DOMAIN, SERVICE_MOVE,
            {"entity_id": "group.ceiling", "direction": "up"},
            blocking=True,
        )
    assert mock_move.await_args.args[0] == ["group.ceiling"]


async def test_move_requires_a_target(hass):
    await _setup_entry(hass)
    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            DOMAIN, SERVICE_MOVE, {"direction": "up"}, blocking=True
        )


async def test_move_tolerates_frontend_metadata(hass):
    """Developer Tools sends a `metadata` key alongside a target; it is not data."""
    await _setup_entry(hass)
    set_light_state(hass, "light.lamp", brightness=100, color_modes=("brightness",))
    with patch(
        "custom_components.dynamic_dimming.controller.DimmingController.async_move"
    ) as mock_move:
        await hass.services.async_call(
            DOMAIN, SERVICE_MOVE,
            {"entity_id": "light.lamp", "direction": "up", "metadata": {}},
            blocking=True,
        )
    assert mock_move.await_args.args[0] == ["light.lamp"]
