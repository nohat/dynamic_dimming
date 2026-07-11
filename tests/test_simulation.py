"""Tests for the simulation stepper."""

from __future__ import annotations

from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.dynamic_dimming.backends.simulation import (
    SimulationBackend,
    resolve_rate,
)
from custom_components.dynamic_dimming.const import (
    DIRECTION_DOWN,
    DIRECTION_UP,
    RATE_PROFILES,
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


def test_resolve_rate_clamps_nonpositive():
    # A non-positive numeric rate must not zero out the per-tick step, or the
    # interval will write identical light.turn_on calls forever at 20 Hz.
    assert resolve_rate(0) == 1.0
    assert resolve_rate(-10) == 1.0
    # Unknown profile strings fall back to the default profile.
    assert resolve_rate("fastt") == RATE_PROFILES["medium"]


async def test_rate_only_scales_step_not_tick_count(hass):
    # Anti-flood invariant: the tick cadence (and therefore call count) is
    # fixed by TICK_INTERVAL regardless of rate; only the per-step brightness
    # delta should scale with rate. Two entities, mid-brightness so neither
    # move reaches a rail within the tick budget.
    set_light_state(hass, "light.slow", brightness=128, color_modes=("brightness",))
    set_light_state(hass, "light.fast", brightness=128, color_modes=("brightness",))
    calls = _turn_on_calls(hass)
    backend = SimulationBackend(hass)

    await backend.async_move("light.slow", DIRECTION_UP, "slow")
    await backend.async_move("light.fast", DIRECTION_UP, "fast")
    await _advance(hass, 3)

    slow_calls = [c for c in calls if c["entity_id"] == "light.slow"]
    fast_calls = [c for c in calls if c["entity_id"] == "light.fast"]

    assert len(slow_calls) == 3
    assert len(fast_calls) == 3
    assert len(slow_calls) == len(fast_calls)  # rate-independent tick count

    slow_delta = slow_calls[-1]["brightness"] - slow_calls[0]["brightness"]
    fast_delta = fast_calls[-1]["brightness"] - fast_calls[0]["brightness"]
    assert fast_delta > slow_delta  # rate scales the step size, not the count


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


async def test_unsub_double_cancel_is_safe(hass):
    set_light_state(hass, "light.lamp", brightness=100, color_modes=("brightness",))
    calls = _turn_on_calls(hass)
    backend = SimulationBackend(hass)

    unsub = await backend.async_move("light.lamp", DIRECTION_UP, "medium")
    await _advance(hass, 2)
    unsub()
    unsub()  # second call must not raise

    count_before = len(calls)
    await _advance(hass, 3)
    assert len(calls) == count_before  # no further steps after cancellation


async def test_async_stop_is_noop(hass):
    set_light_state(hass, "light.lamp", brightness=100, color_modes=("brightness",))
    backend = SimulationBackend(hass)

    await backend.async_stop("light.lamp")  # no active job for this entity

    await backend.async_move("light.lamp", DIRECTION_UP, "medium")
    await backend.async_stop("light.lamp")  # active job present; still a no-op


async def test_stale_unsub_does_not_orphan_newer_job(hass):
    set_light_state(hass, "light.lamp", brightness=8, color_modes=("brightness",))
    calls = _turn_on_calls(hass)
    backend = SimulationBackend(hass)

    unsub_a = await backend.async_move("light.lamp", DIRECTION_DOWN, "fast")  # job A
    unsub_b = await backend.async_move("light.lamp", DIRECTION_DOWN, "fast")  # job B supersedes A

    unsub_a()  # stale unsub from superseded job A

    assert "light.lamp" in backend._unsubs  # job B must still be tracked

    await _advance(hass, 20)  # enough ticks at "fast" to reach the rail (0)

    assert min(c["brightness"] for c in calls) == 0  # job B reached the rail
    count_at_rail = len(calls)
    await _advance(hass, 3)
    assert len(calls) == count_at_rail  # job B self-stopped, no lingering turn_ons

    unsub_b()  # cleanup; must not raise


async def test_step_makes_one_relative_change(hass):
    set_light_state(hass, "light.lamp", brightness=100, color_modes=("brightness",))
    calls = _turn_on_calls(hass)
    backend = SimulationBackend(hass)

    await backend.async_step("light.lamp", DIRECTION_UP, 10.0)
    await hass.async_block_till_done()

    assert len(calls) == 1
    # +10% of 255 ~= +26 -> ~126
    assert 120 <= calls[0]["brightness"] <= 132
