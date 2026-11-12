"""Traffic guards: what the simulated path may put on a mesh or bridge."""

from __future__ import annotations

import asyncio
from collections import Counter

import pytest
import voluptuous as vol
from homeassistant.components.light import LightEntityFeature
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.dynamic_dimming.backends.simulation import SimulationBackend
from custom_components.dynamic_dimming.const import (
    DIRECTION_UP,
    DOMAIN,
    TICK_INTERVAL,
)
from custom_components.dynamic_dimming.traffic import (
    MAX_WRITE_INTERVAL,
    TrafficBudget,
    write_costs,
)

from .conftest import register_device_light, set_light_state

_IEEE = "00:11:22:33:44:55:66:77"


def _turn_on_calls(hass):
    calls = []
    hass.services.async_register(
        "light", "turn_on", lambda call: calls.append(dict(call.data))
    )
    return calls


async def _advance(hass, ticks):
    now = dt_util.utcnow()
    for i in range(1, ticks + 1):
        async_fire_time_changed(hass, now + TICK_INTERVAL * i)
        await hass.async_block_till_done()


def _zha_light(hass, object_id, brightness=10):
    ieee = f"{_IEEE[:-2]}{len(object_id):02x}"
    return register_device_light(
        hass,
        object_id,
        domain="zha",
        identifiers={("zha", ieee)},
        unique_id=f"{ieee}-11",
        brightness=brightness,
    )


def _zwave_light(hass, object_id, brightness=10):
    return register_device_light(
        hass,
        object_id,
        domain="zwave_js",
        identifiers={("zwave_js", f"123-{object_id}")},
        unique_id="123.5-38-0-currentValue",
        brightness=brightness,
    )


def _group(hass, entity_id, members, brightness=10):
    hass.states.async_set(
        entity_id,
        "on",
        {
            "supported_color_modes": ["brightness"],
            "brightness": brightness,
            "entity_id": list(members),
        },
    )


# -- write_costs / TrafficBudget ---------------------------------------------------


def test_unknown_platform_is_unbudgeted(hass):
    set_light_state(hass, "light.plain", brightness=10)
    assert write_costs(hass, "light.plain") == Counter()
    assert TrafficBudget().interval(Counter()) == 0.0


def test_mesh_light_is_budgeted(hass):
    light = _zha_light(hass, "bulb")
    assert write_costs(hass, light) == Counter({"zha": 1.0})


def test_zwave_light_gets_one_write_a_second(hass):
    light = _zwave_light(hass, "dimmer")
    budget = TrafficBudget()
    costs = write_costs(hass, light)
    budget.acquire(costs)
    assert budget.interval(costs) == pytest.approx(1.0)


def test_ha_group_is_charged_for_every_member(hass):
    members = [_zha_light(hass, name) for name in ("a", "bb", "ccc")]
    _group(hass, "light.kitchen", members)
    assert write_costs(hass, "light.kitchen") == Counter({"zha": 3.0})


def test_nested_group_cycle_terminates(hass):
    _group(hass, "light.one", ["light.two"])
    _group(hass, "light.two", ["light.one"])
    assert write_costs(hass, "light.one") == Counter()


def test_zha_group_costs_a_broadcast(hass):
    light = register_device_light(
        hass, "zgroup", domain="zha", unique_id="light_zha_group_0x0002",
        identifiers={("zha", "group-2")},
    )
    budget = TrafficBudget()
    costs = write_costs(hass, light)
    budget.acquire(costs)
    assert budget.interval(costs) == pytest.approx(1.0)


def test_z2m_device_and_group_costs(hass):
    device = register_device_light(
        hass, "z2m_bulb", domain="mqtt",
        identifiers={("mqtt", "zigbee2mqtt_0x00124b0012345678")},
    )
    group = register_device_light(
        hass, "z2m_group", domain="mqtt",
        identifiers={("mqtt", "zigbee2mqtt_zigbee2mqtt_3")},
    )
    other = register_device_light(
        hass, "plain_mqtt", domain="mqtt", identifiers={("mqtt", "something")}
    )
    assert write_costs(hass, device) == Counter({"zigbee2mqtt": 1.0})
    assert write_costs(hass, group) == Counter({"zigbee2mqtt": 5.0})
    assert write_costs(hass, other) == Counter()


def test_budget_is_shared_and_released(hass):
    budget = TrafficBudget()
    one = Counter({"zha": 1.0})
    budget.acquire(one)
    assert budget.interval(one) == pytest.approx(0.2)
    budget.acquire(one)
    budget.acquire(one)
    assert budget.interval(one) == pytest.approx(0.6)  # three ramps share 5/s
    budget.release(one)
    budget.release(one)
    assert budget.interval(one) == pytest.approx(0.2)
    budget.release(one)
    assert budget.interval(one) == 0.0


def test_budget_interval_is_capped(hass):
    budget = TrafficBudget()
    big = Counter({"zha": 40.0})
    budget.acquire(big)
    assert budget.interval(big) == MAX_WRITE_INTERVAL


# -- the simulated path under a budget ---------------------------------------------


async def test_mesh_light_is_written_at_most_five_times_a_second(hass):
    light = _zha_light(hass, "bulb")
    calls = _turn_on_calls(hass)
    backend = SimulationBackend(hass)

    unsub = await backend.async_move(light, DIRECTION_UP, "slow")
    await _advance(hass, 20)  # one second
    unsub()

    assert len(calls) <= 6  # the first write, then one per 200 ms
    assert len(calls) >= 4
    levels = [c["brightness"] for c in calls]
    assert levels == sorted(levels)


async def test_concurrent_mesh_ramps_share_one_budget(hass):
    lights = [_zha_light(hass, name) for name in ("a", "bb", "ccc", "dddd", "eeeee")]
    calls = _turn_on_calls(hass)
    backend = SimulationBackend(hass)

    for light in lights:
        await backend.async_move(light, DIRECTION_UP, "slow")
    await _advance(hass, 40)  # two seconds

    # Five ramps on a 5 writes/s transport: each writes about once a second,
    # so the transport total stays near budget instead of 5 x 20 Hz.
    assert len(calls) <= 5 * 3


async def test_ha_group_of_mesh_lights_is_slowed_by_member_count(hass):
    members = [_zha_light(hass, name) for name in ("a", "bb", "ccc", "dddd", "eeeee")]
    _group(hass, "light.room", members)
    calls = _turn_on_calls(hass)
    backend = SimulationBackend(hass)

    await backend.async_move("light.room", DIRECTION_UP, "slow")
    await _advance(hass, 40)

    assert len(calls) <= 3  # one group write a second fans out to five bulbs


async def test_budgeted_write_carries_a_transition_when_supported(hass):
    light = _zha_light(hass, "bulb")
    hass.states.async_set(
        light,
        "on",
        {
            "supported_color_modes": ["brightness"],
            "brightness": 10,
            "supported_features": LightEntityFeature.TRANSITION,
        },
    )
    calls = _turn_on_calls(hass)
    backend = SimulationBackend(hass)

    unsub = await backend.async_move(light, DIRECTION_UP, "slow")
    await _advance(hass, 6)
    unsub()

    assert calls and all(c["transition"] == pytest.approx(0.2) for c in calls)


async def test_unbudgeted_write_carries_no_transition(hass):
    hass.states.async_set(
        "light.plain",
        "on",
        {
            "supported_color_modes": ["brightness"],
            "brightness": 100,
            "supported_features": LightEntityFeature.TRANSITION,
        },
    )
    calls = _turn_on_calls(hass)
    backend = SimulationBackend(hass)

    unsub = await backend.async_move("light.plain", DIRECTION_UP, "medium")
    await _advance(hass, 3)
    unsub()

    assert len(calls) == 3
    assert all("transition" not in c for c in calls)


async def test_budgeted_fade_still_lands_on_target(hass):
    light = _zha_light(hass, "bulb", brightness=50)
    calls = _turn_on_calls(hass)
    backend = SimulationBackend(hass)

    await backend.async_fade(light, 255, 0.5, color_temp_kelvin=2700)
    await _advance(hass, 12)

    assert calls[0]["color_temp_kelvin"] == 2700
    assert all("color_temp_kelvin" not in c for c in calls[1:])
    assert calls[-1]["brightness"] == 255
    assert len(calls) <= 4


async def test_long_zwave_fade_is_one_write_a_second(hass):
    light = _zwave_light(hass, "dimmer")
    calls = _turn_on_calls(hass)
    backend = SimulationBackend(hass)

    await backend.async_fade(light, 255, 5.0)
    await _advance(hass, 110)

    assert len(calls) <= 7  # was 100 before the budget
    assert calls[-1]["brightness"] == 255


# -- in-flight guard, latest-wins, dedupe ------------------------------------------


async def test_slow_light_never_has_two_writes_in_flight(hass):
    set_light_state(hass, "light.slow", brightness=10)
    release = asyncio.Event()
    in_flight = 0
    worst = 0
    levels = []

    async def _slow_turn_on(call):
        nonlocal in_flight, worst
        in_flight += 1
        worst = max(worst, in_flight)
        levels.append(call.data["brightness"])
        await release.wait()
        in_flight -= 1

    hass.services.async_register("light", "turn_on", _slow_turn_on)
    backend = SimulationBackend(hass)

    unsub = await backend.async_move("light.slow", DIRECTION_UP, "fast")
    now = dt_util.utcnow()
    for i in range(1, 11):  # ten ticks while the first write hangs
        async_fire_time_changed(hass, now + TICK_INTERVAL * i)
        await asyncio.sleep(0)
    assert len(levels) == 1 and worst == 1

    release.set()
    await hass.async_block_till_done()
    async_fire_time_changed(hass, now + TICK_INTERVAL * 11)
    await hass.async_block_till_done()
    unsub()

    # The second write is the newest level, not the nine stale ones queued up.
    assert len(levels) == 2
    assert levels[1] > levels[0] + 20


async def test_stop_drops_a_level_that_had_not_gone_out(hass):
    set_light_state(hass, "light.slow", brightness=10)
    release = asyncio.Event()
    levels = []

    async def _slow_turn_on(call):
        levels.append(call.data["brightness"])
        await release.wait()

    hass.services.async_register("light", "turn_on", _slow_turn_on)
    backend = SimulationBackend(hass)

    unsub = await backend.async_move("light.slow", DIRECTION_UP, "fast")
    now = dt_util.utcnow()
    for i in range(1, 6):
        async_fire_time_changed(hass, now + TICK_INTERVAL * i)
        await asyncio.sleep(0)
    unsub()  # released while a write is still in flight
    release.set()
    await hass.async_block_till_done()

    assert len(levels) == 1  # nothing trails out after the stop


async def test_unchanged_level_is_not_resent(hass):
    # At the bottom of the perceptual curve a slow ramp spends many ticks on
    # the same rounded level; each used to be a redundant write.
    set_light_state(hass, "light.lamp", brightness=1)
    calls = _turn_on_calls(hass)
    backend = SimulationBackend(hass)

    unsub = await backend.async_move("light.lamp", DIRECTION_UP, 5)
    await _advance(hass, 20)
    unsub()

    levels = [c["brightness"] for c in calls]
    assert len(levels) == len(set(levels))
    assert len(levels) < 20


async def test_failed_write_does_not_kill_the_ramp(hass):
    set_light_state(hass, "light.lamp", brightness=100)
    seen = []

    def _flaky(call):
        seen.append(call.data["brightness"])
        if len(seen) == 1:
            raise RuntimeError("delivery failed")

    hass.services.async_register("light", "turn_on", _flaky)
    backend = SimulationBackend(hass)

    unsub = await backend.async_move("light.lamp", DIRECTION_UP, "medium")
    await _advance(hass, 3)
    unsub()

    assert len(seen) == 3


# -- controller and service guards -------------------------------------------------


async def _setup(hass):
    entry = MockConfigEntry(domain=DOMAIN, title="Dynamic Dimming")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return hass.data[DOMAIN][entry.entry_id]


def _zha_commands(hass):
    commands = []
    hass.services.async_register(
        "zha", "issue_zigbee_cluster_command",
        lambda call: commands.append(dict(call.data)),
    )
    return commands


async def test_repeated_hold_events_send_one_native_move(hass):
    commands = _zha_commands(hass)
    light = _zha_light(hass, "bulb")
    controller = await _setup(hass)

    for _ in range(5):  # a remote repeating "hold" while the button is down
        await controller.async_move(light, DIRECTION_UP, "medium")
    await hass.async_block_till_done()
    assert len(commands) == 1

    await controller.async_move(light, "down", "medium")  # a reversal is new
    await controller.async_stop(light)
    await controller.async_move(light, "down", "medium")  # so is a fresh press
    await hass.async_block_till_done()
    assert [c["command"] for c in commands] == [1, 1, 3, 1]


async def test_native_override_is_strict_for_step_and_fade(hass):
    hass.services.async_register("zwave_js", "invoke_cc_api", lambda call: None)
    light = _zwave_light(hass, "dimmer")
    controller = await _setup(hass)

    with pytest.raises(ServiceValidationError):
        await controller.async_step(light, DIRECTION_UP, 5.0, backend="native")
    with pytest.raises(ServiceValidationError):
        await controller.async_fade(light, 128, 2.0, backend="native")


async def test_one_failing_light_does_not_strand_the_others(hass):
    commands = _zha_commands(hass)
    lights = [_zha_light(hass, name) for name in ("a", "bb")]
    controller = await _setup(hass)
    zha = next(b for b in controller.native_backends if type(b).__name__ == "ZhaBackend")
    real_stop = zha.async_stop

    async def _stop(entity_id):
        if entity_id == lights[0]:
            raise RuntimeError("unreachable")
        await real_stop(entity_id)

    zha.async_stop = _stop
    await controller.async_stop(lights)
    await hass.async_block_till_done()

    assert [c["command"] for c in commands] == [3]  # the healthy light stopped


async def test_group_and_members_in_one_target_are_not_double_driven(hass):
    commands = _zha_commands(hass)
    calls = _turn_on_calls(hass)
    members = [_zha_light(hass, name) for name in ("a", "bb")]
    _group(hass, "light.room", members)
    await _setup(hass)

    await hass.services.async_call(
        DOMAIN, "move",
        {"entity_id": ["light.room", *members], "direction": "up"},
        blocking=True,
    )
    await _advance(hass, 5)

    assert len(commands) == 2  # each bulb's own native Move
    assert calls == []  # and no simulated ramp on the group fighting them


@pytest.mark.parametrize(
    ("service", "data"),
    [
        ("move", {"direction": "up", "rate": float("inf")}),
        ("move", {"direction": "up", "rate": float("nan")}),
        ("move", {"direction": "up", "rate": 0}),
        ("step", {"direction": "up", "step_pct": -5}),
        ("step", {"direction": "up", "step_pct": float("nan")}),
        ("fade", {"brightness_pct": float("nan"), "duration": 1}),
        ("fade", {"brightness_pct": 50, "duration": float("inf")}),
    ],
)
async def test_non_finite_and_out_of_range_inputs_are_rejected(hass, service, data):
    set_light_state(hass, "light.lamp", brightness=100)
    await _setup(hass)
    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            DOMAIN, service, {"entity_id": "light.lamp", **data}, blocking=True
        )
