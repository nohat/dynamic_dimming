"""Tests for the simulation stepper."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.dynamic_dimming.backends.simulation import (
    SimulationBackend,
    resolve_rate,
)
from custom_components.dynamic_dimming.const import (
    DIRECTION_DOWN,
    DIRECTION_UP,
    TICK_INTERVAL,
)

from .conftest import set_light_state


def _turn_on_calls(hass):
    """Record light.turn_on calls; return the list they land in."""
    calls = []
    hass.services.async_register("light", "turn_on", lambda call: calls.append(call.data))
    return calls


async def _advance(hass, ticks):
    now = dt_util.utcnow()
    for i in range(1, ticks + 1):
        async_fire_time_changed(hass, now + TICK_INTERVAL * i)
        await hass.async_block_till_done()


def test_resolve_rate_profiles_and_numbers():
    assert resolve_rate("slow") == 40.0
    assert resolve_rate("fast") == 160.0
    assert resolve_rate(75) == 75.0
    assert resolve_rate(None) == 90.0  # DEFAULT_RATE == "medium"


async def test_move_up_steps_toward_full(hass):
    set_light_state(hass, "light.lamp", brightness=100, color_modes=("brightness",))
    calls = _turn_on_calls(hass)
    backend = SimulationBackend(hass)

    unsub = await backend.async_move("light.lamp", DIRECTION_UP, "medium")
    await _advance(hass, 3)
    unsub()

    assert calls, "expected at least one light.turn_on call"
    brightnesses = [c["brightness"] for c in calls]
    assert brightnesses == sorted(brightnesses)  # monotonic up
    assert all(c["entity_id"] == "light.lamp" for c in calls)
    # medium = 90 u/s * 0.05 s ~= 4-5 units per tick, not a jump to 255.
    assert max(brightnesses) < 130


async def test_move_down_stops_at_zero(hass):
    set_light_state(hass, "light.lamp", brightness=8, color_modes=("brightness",))
    calls = _turn_on_calls(hass)
    backend = SimulationBackend(hass)

    await backend.async_move("light.lamp", DIRECTION_DOWN, "fast")
    await _advance(hass, 20)

    assert min(c["brightness"] for c in calls) == 0


async def test_move_stops_when_entity_unavailable(hass):
    set_light_state(hass, "light.lamp", brightness=100, color_modes=("brightness",))
    calls = _turn_on_calls(hass)
    backend = SimulationBackend(hass)

    await backend.async_move("light.lamp", DIRECTION_UP, "medium")
    await _advance(hass, 2)
    count_before = len(calls)
    hass.states.async_set("light.lamp", "unavailable", {})
    await _advance(hass, 3)

    assert len(calls) == count_before  # no further steps after unavailable


async def test_step_makes_one_relative_change(hass):
    set_light_state(hass, "light.lamp", brightness=100, color_modes=("brightness",))
    calls = _turn_on_calls(hass)
    backend = SimulationBackend(hass)

    await backend.async_step("light.lamp", DIRECTION_UP, 10.0)
    await hass.async_block_till_done()

    assert len(calls) == 1
    # +10% of 255 ~= +26 -> ~126
    assert 120 <= calls[0]["brightness"] <= 132
