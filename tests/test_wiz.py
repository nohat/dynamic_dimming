"""Tests for the native WiZ backend."""

from __future__ import annotations

import json
from unittest.mock import patch

from homeassistant.const import CONF_HOST
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.dynamic_dimming.backends.wiz import WizBackend, to_dimming
from custom_components.dynamic_dimming.const import (
    DEFAULT_MIN_BRIGHTNESS,
    DIRECTION_DOWN,
    DIRECTION_UP,
    TICK_INTERVAL,
    WIZ_PORT,
)

from .conftest import set_light_state


class FakeSocket:
    """Stands in for the backend's UDP socket, recording every datagram."""

    def __init__(self):
        self.sent: list[tuple[dict, str]] = []
        self.ports: set[int] = set()
        self.closed = False

    def setblocking(self, _flag):
        pass

    def sendto(self, payload, addr):
        self.sent.append((json.loads(payload), addr[0]))
        self.ports.add(addr[1])

    def recvfrom(self, _bufsize):
        raise BlockingIOError  # nothing queued

    def close(self):
        self.closed = True

    def params_to(self, host):
        return [p["params"] for p, h in self.sent if h == host]

    @property
    def dimmings(self):
        return [p["params"]["dimming"] for p, _ in self.sent]


async def _backend(hass):
    """A set-up backend with a fake socket pre-seeded into its lazy slot."""
    backend = WizBackend(hass)
    await backend.async_setup()
    sock = FakeSocket()
    backend._sock = sock
    return backend, sock


def wiz_light(hass, object_id, host, *, brightness=100, on=True):
    """Register light.<object_id> as a WiZ bulb reachable at ``host``."""
    entry = MockConfigEntry(domain="wiz", data={CONF_HOST: host})
    entry.add_to_hass(hass)
    er.async_get(hass).async_get_or_create(
        "light",
        "wiz",
        f"uid_{object_id}",
        suggested_object_id=object_id,
        config_entry=entry,
    )
    entity_id = f"light.{object_id}"
    set_light_state(hass, entity_id, brightness=brightness, on=on)
    return entity_id


def group_light(hass, object_id, members, *, brightness=100):
    """Register a light group entity whose state lists ``members``."""
    entity_id = f"light.{object_id}"
    hass.states.async_set(
        entity_id,
        "on",
        {
            "supported_color_modes": ["brightness"],
            "brightness": brightness,
            "entity_id": members,
        },
    )
    return entity_id


def _turn_on_calls(hass):
    calls = []
    hass.services.async_register("light", "turn_on", lambda call: calls.append(call.data))
    return calls


async def _advance(hass, ticks):
    now = dt_util.utcnow()
    for i in range(1, ticks + 1):
        async_fire_time_changed(hass, now + TICK_INTERVAL * i)
        await hass.async_block_till_done()


# -- brightness mapping -----------------------------------------------------------


def test_to_dimming_maps_and_clamps():
    assert to_dimming(255) == 100
    assert to_dimming(128) == 50
    # WiZ clamps into 1..100 firmware-side; never emit 0, which would not
    # switch the bulb off anyway.
    assert to_dimming(0) == DEFAULT_MIN_BRIGHTNESS
    assert to_dimming(1) == 1
    assert to_dimming(1000) == 100


# -- claiming ---------------------------------------------------------------------


async def test_claims_wiz_light(hass):
    entity_id = wiz_light(hass, "bulb", "192.168.1.17")
    backend, _ = await _backend(hass)
    assert backend.claims(entity_id)


async def test_does_not_claim_non_wiz_light(hass):
    set_light_state(hass, "light.other", brightness=100)
    backend, _ = await _backend(hass)
    assert not backend.claims("light.other")


async def test_does_not_claim_wiz_entry_without_host(hass):
    # A WiZ entry with no host must degrade to simulation, not go dead.
    entry = MockConfigEntry(domain="wiz", data={})
    entry.add_to_hass(hass)
    er.async_get(hass).async_get_or_create(
        "light", "wiz", "uid_hostless", suggested_object_id="hostless", config_entry=entry
    )
    set_light_state(hass, "light.hostless", brightness=100)
    backend, _ = await _backend(hass)
    assert not backend.claims("light.hostless")


async def test_claims_all_wiz_group(hass):
    a = wiz_light(hass, "ceil_1", "192.168.1.10")
    b = wiz_light(hass, "ceil_2", "192.168.1.11")
    group = group_light(hass, "ceiling", [a, b])
    backend, _ = await _backend(hass)
    assert backend.claims(group)


async def test_does_not_claim_mixed_group(hass):
    a = wiz_light(hass, "ceil_1", "192.168.1.10")
    set_light_state(hass, "light.zigbee_can", brightness=100)
    group = group_light(hass, "mixed", [a, "light.zigbee_can"])
    backend, _ = await _backend(hass)
    # All-or-nothing: simulation can still drive the mixed group correctly.
    assert not backend.claims(group)


async def test_self_referential_group_terminates(hass):
    group = group_light(hass, "loop", ["light.loop"])
    backend, _ = await _backend(hass)
    assert not backend.claims(group)


# -- movement ---------------------------------------------------------------------


async def test_move_up_streams_absolute_udp_not_service_calls(hass):
    entity_id = wiz_light(hass, "bulb", "192.168.1.17", brightness=100)
    calls = _turn_on_calls(hass)
    backend, sock = await _backend(hass)

    await backend.async_move(entity_id, DIRECTION_UP, "medium")
    await _advance(hass, 4)

    assert len(sock.sent) == 4
    assert sock.dimmings == sorted(sock.dimmings)  # monotonic up
    assert all(host == "192.168.1.17" for _, host in sock.sent)
    assert sock.ports == {WIZ_PORT}
    assert all(p["method"] == "setPilot" for p, _ in sock.sent)
    # The whole point: the ramp does not go through the acknowledged path.
    assert calls == []


async def test_move_up_is_perceptual_not_linear(hass):
    # The reported symptom was a hold that spent most of its travel at the top
    # of the range. On the perceptual curve the commanded dimming should
    # accelerate: small increments low down, large ones near full.
    entity_id = wiz_light(hass, "bulb", "192.168.1.17", brightness=0, on=False)
    backend, sock = await _backend(hass)

    await backend.async_move(entity_id, DIRECTION_UP, "fast")
    await _advance(hass, 20)

    dims = sock.dimmings
    assert dims == sorted(dims)
    deltas = [b - a for a, b in zip(dims, dims[1:])]
    assert deltas[-1] > deltas[0]  # accelerating, not constant
    # A linear ramp at "fast" (160 u/s) would already be past 60% dimming after
    # 20 ticks; the perceptual one is still well down the range.
    assert dims[19] < 40


async def test_linear_curve_override_restores_constant_steps(hass):
    entity_id = wiz_light(hass, "bulb", "192.168.1.17", brightness=0, on=False)
    backend, sock = await _backend(hass)

    await backend.async_move(entity_id, DIRECTION_UP, "fast", curve="linear")
    await _advance(hass, 20)

    deltas = [b - a for a, b in zip(sock.dimmings, sock.dimmings[1:])]
    # Constant to within the 1-unit quantisation of WiZ's 100-step scale.
    assert max(deltas) - min(deltas) <= 1


async def test_move_carries_state_so_a_dark_bulb_lights(hass):
    entity_id = wiz_light(hass, "bulb", "192.168.1.17", brightness=0, on=False)
    backend, sock = await _backend(hass)

    await backend.async_move(entity_id, DIRECTION_UP, "medium")
    await _advance(hass, 2)

    assert sock.sent
    assert all(p["params"]["state"] is True for p, _ in sock.sent)


async def test_move_fans_group_out_from_one_tick(hass):
    a = wiz_light(hass, "ceil_1", "192.168.1.10")
    b = wiz_light(hass, "ceil_2", "192.168.1.11")
    group = group_light(hass, "ceiling", [a, b])
    backend, sock = await _backend(hass)

    await backend.async_move(group, DIRECTION_UP, "medium")
    await _advance(hass, 3)

    # Every member gets every step, and gets the *same* step — that's what keeps
    # a multi-bulb fixture visibly in sync.
    assert sock.params_to("192.168.1.10") == sock.params_to("192.168.1.11")
    assert len(sock.params_to("192.168.1.10")) == 3


async def test_move_down_floors_at_min_and_resyncs(hass):
    entity_id = wiz_light(hass, "bulb", "192.168.1.17", brightness=8)
    calls = _turn_on_calls(hass)
    backend, sock = await _backend(hass)

    await backend.async_move(entity_id, DIRECTION_DOWN, "fast")
    await _advance(hass, 20)

    assert min(sock.dimmings) == DEFAULT_MIN_BRIGHTNESS
    assert 0 not in sock.dimmings  # never driven off
    # Reaching the rail self-stops and reconciles HA's state machine.
    assert len(calls) == 1
    assert calls[0]["brightness"] == DEFAULT_MIN_BRIGHTNESS

    sent_at_rail = len(sock.sent)
    await _advance(hass, 3)
    assert len(sock.sent) == sent_at_rail  # no lingering datagrams


async def test_move_stops_when_entity_unavailable(hass):
    entity_id = wiz_light(hass, "bulb", "192.168.1.17")
    backend, sock = await _backend(hass)

    await backend.async_move(entity_id, DIRECTION_UP, "medium")
    await _advance(hass, 2)
    sent_before = len(sock.sent)
    hass.states.async_set(entity_id, "unavailable", {})
    await _advance(hass, 3)

    assert len(sock.sent) == sent_before


async def test_rate_scales_step_not_datagram_count(hass):
    # Anti-flood invariant: rate must not buy more packets per second.
    slow = wiz_light(hass, "slow", "192.168.1.10", brightness=128)
    fast = wiz_light(hass, "fast", "192.168.1.11", brightness=128)
    backend, sock = await _backend(hass)

    await backend.async_move(slow, DIRECTION_UP, "slow")
    await backend.async_move(fast, DIRECTION_UP, "fast")
    await _advance(hass, 3)

    slow_dims = [p["dimming"] for p in sock.params_to("192.168.1.10")]
    fast_dims = [p["dimming"] for p in sock.params_to("192.168.1.11")]
    assert len(slow_dims) == len(fast_dims) == 3
    assert fast_dims[-1] - fast_dims[0] > slow_dims[-1] - slow_dims[0]


# -- stop / step ------------------------------------------------------------------


async def test_stop_reasserts_final_level_through_the_entity(hass):
    entity_id = wiz_light(hass, "bulb", "192.168.1.17", brightness=100)
    calls = _turn_on_calls(hass)
    backend, sock = await _backend(hass)

    await backend.async_move(entity_id, DIRECTION_UP, "medium")
    await _advance(hass, 3)
    await backend.async_stop(entity_id)
    await hass.async_block_till_done()

    # HA's state machine never saw the ramp; stop is what reconciles it, and it
    # must land on where the ramp actually finished, not where it started.
    assert len(calls) == 1
    assert calls[0]["entity_id"] == entity_id
    assert calls[0]["brightness"] > 100  # moved up from the starting 100
    assert to_dimming(calls[0]["brightness"]) == sock.dimmings[-1]

    sent_after_stop = len(sock.sent)
    await _advance(hass, 3)
    assert len(sock.sent) == sent_after_stop


async def test_stop_without_a_move_is_a_noop(hass):
    entity_id = wiz_light(hass, "bulb", "192.168.1.17")
    calls = _turn_on_calls(hass)
    backend, _ = await _backend(hass)

    await backend.async_stop(entity_id)
    await hass.async_block_till_done()

    assert calls == []


async def test_step_sends_udp_then_reconciles(hass):
    entity_id = wiz_light(hass, "bulb", "192.168.1.17", brightness=100)
    calls = _turn_on_calls(hass)
    backend, sock = await _backend(hass)

    await backend.async_step(entity_id, DIRECTION_UP, 10.0)
    await hass.async_block_till_done()

    assert len(sock.sent) == 1  # immediate, unacknowledged
    assert len(calls) == 1  # then reconciled
    # 10% of perceptual travel, not of 0-255.
    assert 140 <= calls[0]["brightness"] <= 152


async def test_step_on_unclaimed_entity_does_nothing(hass):
    set_light_state(hass, "light.other", brightness=100)
    calls = _turn_on_calls(hass)
    backend, sock = await _backend(hass)

    await backend.async_step("light.other", DIRECTION_UP, 10.0)
    await hass.async_block_till_done()

    assert sock.sent == []
    assert calls == []


async def test_socket_failure_degrades_instead_of_raising(hass):
    # No socket must mean "this ramp does nothing", never an exception out of a
    # 20 Hz interval callback.
    entity_id = wiz_light(hass, "bulb", "192.168.1.17")
    calls = _turn_on_calls(hass)
    backend = WizBackend(hass)
    await backend.async_setup()

    with patch(
        "custom_components.dynamic_dimming.backends.wiz.socket.socket",
        side_effect=OSError("no fds"),
    ):
        await backend.async_move(entity_id, DIRECTION_UP, "medium")
        await _advance(hass, 3)
        await backend.async_step(entity_id, DIRECTION_UP, 10.0)
        await hass.async_block_till_done()

    assert backend._sock is None
    # step still lands, because its reconciling service call is the fallback path
    assert len(calls) == 1


async def test_unload_cancels_jobs_and_closes_socket(hass):
    entity_id = wiz_light(hass, "bulb", "192.168.1.17")
    backend, sock = await _backend(hass)

    await backend.async_move(entity_id, DIRECTION_UP, "medium")
    await _advance(hass, 2)
    await backend.async_unload()

    sent_at_unload = len(sock.sent)
    await _advance(hass, 3)
    # An unload mid-move must not leave a 20 Hz interval firing at a dead socket.
    assert len(sock.sent) == sent_at_unload
    assert sock.closed


# -- fade ---------------------------------------------------------------------


async def test_fade_streams_udp_toward_target(hass):
    entity_id = wiz_light(hass, "bulb", "192.168.1.17", brightness=50)
    calls = _turn_on_calls(hass)
    backend, sock = await _backend(hass)

    await backend.async_fade(entity_id, 255, 0.5)
    await _advance(hass, 10)

    assert sock.dimmings == sorted(sock.dimmings)
    assert sock.dimmings[-1] == 100  # landed on the target
    # Only the end-of-fade resync goes through the acknowledged path.
    assert len(calls) == 1
    assert calls[0]["brightness"] == 255


async def test_fade_color_temp_rides_in_every_datagram(hass):
    # The whole point of the parameter: the *first* packet must already carry
    # the color, and every later one re-asserts it so a lost datagram is
    # corrected a tick later — same philosophy as the absolute brightness.
    entity_id = wiz_light(hass, "bulb", "192.168.1.17", brightness=50)
    calls = _turn_on_calls(hass)
    backend, sock = await _backend(hass)

    await backend.async_fade(entity_id, 255, 0.5, color_temp_kelvin=2700)
    await _advance(hass, 10)

    assert sock.sent
    assert all(p["params"]["temp"] == 2700 for p, _ in sock.sent)
    # setPilot has no "mode" key; `temp` alone selects tunable-white mode, and
    # an unknown key would make the firmware reject the whole datagram.
    assert all("mode" not in p["params"] for p, _ in sock.sent)
    # The resync teaches HA's state machine the color as well as the level.
    assert len(calls) == 1
    assert calls[0]["color_temp_kelvin"] == 2700


async def test_fade_without_color_sends_no_temp(hass):
    entity_id = wiz_light(hass, "bulb", "192.168.1.17", brightness=50)
    calls = _turn_on_calls(hass)
    backend, sock = await _backend(hass)

    await backend.async_fade(entity_id, 255, 0.5)
    await _advance(hass, 10)

    assert sock.sent
    assert all("temp" not in p["params"] for p, _ in sock.sent)
    assert len(calls) == 1
    assert "color_temp_kelvin" not in calls[0]


async def test_move_after_color_fade_does_not_reassert_stale_temp(hass):
    # A move makes no color promise. If it supersedes a color-carrying fade,
    # its resync must not re-assert a temperature that may have moved on.
    entity_id = wiz_light(hass, "bulb", "192.168.1.17", brightness=50)
    calls = _turn_on_calls(hass)
    backend, sock = await _backend(hass)

    await backend.async_fade(entity_id, 255, 5.0, color_temp_kelvin=2700)
    await _advance(hass, 2)
    await backend.async_move(entity_id, DIRECTION_UP, "medium")
    await _advance(hass, 2)
    await backend.async_stop(entity_id)
    await hass.async_block_till_done()

    assert len(calls) == 1
    assert "color_temp_kelvin" not in calls[0]
