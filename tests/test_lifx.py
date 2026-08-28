"""Tests for the native LIFX backend."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from homeassistant.const import CONF_HOST
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from custom_components.dynamic_dimming.backends import lifx as lifx_mod
from custom_components.dynamic_dimming.backends.lifx import (
    LifxBackend,
    parse_serial,
    to_bri16,
)
from custom_components.dynamic_dimming.const import (
    DIRECTION_DOWN,
    DIRECTION_UP,
    LIFX_DEFAULT_KELVIN,
    LIFX_DOMAIN,
    LIFX_MAX_BRI,
    LIFX_MIN_BRI,
    LIFX_PKT_SET_COLOR,
    LIFX_PKT_SET_LIGHT_POWER,
    LIFX_PORT,
)

SERIAL = "d0:73:d5:01:02:03"
SERIAL_BYTES = bytes.fromhex("d073d5010203")
HOST = "192.168.1.40"


class FakeSocket:
    """Stands in for the backend's UDP socket, recording every datagram."""

    def __init__(self):
        self.sent: list[tuple[bytes, tuple[str, int]]] = []
        self.closed = False

    def setblocking(self, _flag):
        pass

    def sendto(self, packet, addr):
        self.sent.append((packet, addr))

    def close(self):
        self.closed = True


class Packet:
    """One decoded LIFX datagram."""

    def __init__(self, raw, addr):
        (
            self.size,
            self.protocol,
            self.source,
            self.target,
            _reserved,
            self.flags,
            self.sequence,
            _reserved2,
            self.type,
            _reserved3,
        ) = lifx_mod._HEADER.unpack(raw[: lifx_mod._HEADER.size])
        self.raw = raw
        self.addr = addr
        if self.type == LIFX_PKT_SET_COLOR:
            (
                _reserved4,
                self.hue,
                self.saturation,
                self.brightness,
                self.kelvin,
                self.duration,
            ) = lifx_mod._SET_COLOR.unpack(raw[lifx_mod._HEADER.size :])
        elif self.type == LIFX_PKT_SET_LIGHT_POWER:
            self.level, self.duration = lifx_mod._SET_POWER.unpack(
                raw[lifx_mod._HEADER.size :]
            )


def _packets(sock):
    return [Packet(raw, addr) for raw, addr in sock.sent]


def _backend(hass):
    backend = LifxBackend(hass)
    sock = FakeSocket()
    backend._sock = sock
    return backend, sock


def _lifx_light(
    hass,
    object_id="beam",
    *,
    host=HOST,
    unique_id=SERIAL,
    brightness=128,
    on=True,
    extra_attrs=None,
):
    """Register light.<object_id> as a LIFX bulb reachable at ``host``."""
    entry = MockConfigEntry(domain=LIFX_DOMAIN, data={CONF_HOST: host})
    entry.add_to_hass(hass)
    er.async_get(hass).async_get_or_create(
        "light",
        LIFX_DOMAIN,
        unique_id,
        suggested_object_id=object_id,
        config_entry=entry,
    )
    entity_id = f"light.{object_id}"
    attrs = {"supported_color_modes": ["hs", "color_temp"], "brightness": brightness}
    attrs.update(extra_attrs or {})
    hass.states.async_set(entity_id, "on" if on else "off", attrs)
    return entity_id


@pytest.fixture
def turn_on_calls(hass):
    return async_mock_service(hass, "light", "turn_on")


# -- serial / brightness mapping ---------------------------------------------------


def test_parse_serial_reads_the_colon_form():
    assert parse_serial(SERIAL) == SERIAL_BYTES


def test_parse_serial_accepts_a_bare_hex_form():
    assert parse_serial("d073d5010203") == SERIAL_BYTES


@pytest.mark.parametrize("unique_id", ["", "d0:73:d5", "not-hex-junk-!", "d073d5"])
def test_parse_serial_rejects_junk(unique_id):
    assert parse_serial(unique_id) is None


@pytest.mark.parametrize(
    ("brightness", "bri16"), [(0, 0), (1, 257), (128, 32896), (255, 65535)]
)
def test_to_bri16_scales_onto_the_16_bit_range(brightness, bri16):
    assert to_bri16(brightness) == bri16


# -- claiming ----------------------------------------------------------------------


async def test_claims_lifx_light(hass):
    assert LifxBackend(hass).claims(_lifx_light(hass))


async def test_does_not_claim_without_a_host(hass):
    entity_id = _lifx_light(hass, host="")
    assert not LifxBackend(hass).claims(entity_id)


async def test_does_not_claim_an_unparseable_serial(hass):
    entity_id = _lifx_light(hass, unique_id="not-a-serial")
    assert not LifxBackend(hass).claims(entity_id)


async def test_does_not_claim_other_platform(hass):
    entry = MockConfigEntry(domain="wiz", data={CONF_HOST: HOST})
    entry.add_to_hass(hass)
    er.async_get(hass).async_get_or_create(
        "light", "wiz", "abc", suggested_object_id="other", config_entry=entry
    )
    hass.states.async_set(
        "light.other", "on", {"supported_color_modes": ["brightness"]}
    )
    assert not LifxBackend(hass).claims("light.other")


# -- move --------------------------------------------------------------------------


async def test_move_up_is_one_interpolated_set_color(hass):
    backend, sock = _backend(hass)
    entity_id = _lifx_light(hass, brightness=128)
    assert await backend.async_move(entity_id, DIRECTION_UP, "medium") is None
    (pkt,) = _packets(sock)
    assert pkt.addr == (HOST, LIFX_PORT)
    assert pkt.size == len(pkt.raw) == 49
    assert pkt.target == SERIAL_BYTES + b"\x00\x00"
    assert pkt.flags == 0  # no ack, no response — fire and forget
    assert pkt.type == LIFX_PKT_SET_COLOR
    assert pkt.brightness == LIFX_MAX_BRI
    assert pkt.kelvin == LIFX_DEFAULT_KELVIN
    # 32639 sixteenths-scale units at medium (90 * 257 per second) is 1411 ms.
    assert pkt.duration == 1411


async def test_move_down_aims_at_the_still_on_floor(hass):
    backend, sock = _backend(hass)
    entity_id = _lifx_light(hass, brightness=128)
    await backend.async_move(entity_id, DIRECTION_DOWN, "medium")
    (pkt,) = _packets(sock)
    assert pkt.brightness == LIFX_MIN_BRI


async def test_move_carries_the_lights_own_color(hass):
    backend, sock = _backend(hass)
    entity_id = _lifx_light(
        hass, extra_attrs={"color_mode": "hs", "hs_color": (180.0, 50.0)}
    )
    await backend.async_move(entity_id, DIRECTION_UP, None)
    (pkt,) = _packets(sock)
    assert pkt.hue == 32768
    assert pkt.saturation == 32768


async def test_move_in_color_temp_mode_is_an_unsaturated_white(hass):
    backend, sock = _backend(hass)
    entity_id = _lifx_light(
        hass,
        extra_attrs={
            "color_mode": "color_temp",
            "color_temp_kelvin": 2700,
            "hs_color": (27.0, 20.0),  # HA's derived approximation, to ignore
        },
    )
    await backend.async_move(entity_id, DIRECTION_UP, None)
    (pkt,) = _packets(sock)
    assert (pkt.hue, pkt.saturation, pkt.kelvin) == (0, 0, 2700)


async def test_move_on_an_off_light_is_declined(hass):
    """SetColor while off would rewrite the stored level invisibly."""
    backend, sock = _backend(hass)
    entity_id = _lifx_light(hass, on=False)
    assert await backend.async_move(entity_id, DIRECTION_UP, None) is None
    assert not sock.sent


async def test_move_on_unclaimed_entity_sends_nothing(hass):
    backend, sock = _backend(hass)
    entity_id = _lifx_light(hass, unique_id="not-a-serial")
    await backend.async_move(entity_id, DIRECTION_UP, None)
    assert not sock.sent


async def test_reversal_starts_from_the_active_ramps_estimate(hass):
    """A second move mid-ramp trusts the ramp's arithmetic over HA's cache."""
    backend, sock = _backend(hass)
    entity_id = _lifx_light(hass, brightness=0)
    with patch.object(lifx_mod.time, "monotonic", side_effect=[1000.0, 1000.5]):
        # Rate 255/s means the full 16-bit range in exactly one second.
        await backend.async_move(entity_id, DIRECTION_UP, 255)
        await backend.async_move(entity_id, DIRECTION_DOWN, 255)
    up, down = _packets(sock)
    assert up.brightness == LIFX_MAX_BRI
    # Half a second in, the ramp sits at half scale; the reversal's duration
    # covers the distance from there down to the floor.
    assert down.brightness == LIFX_MIN_BRI
    assert down.duration == round((32767.5 - LIFX_MIN_BRI) / (255 * 257) * 1000)


# -- stop --------------------------------------------------------------------------


async def test_stop_pins_the_computed_level_and_resyncs(hass, turn_on_calls):
    backend, sock = _backend(hass)
    entity_id = _lifx_light(hass, brightness=0)
    with patch.object(lifx_mod.time, "monotonic", side_effect=[1000.0, 1000.5]):
        await backend.async_move(entity_id, DIRECTION_UP, 255)
        await backend.async_stop(entity_id)
    _, pin = _packets(sock)
    assert pin.type == LIFX_PKT_SET_COLOR
    assert pin.brightness == 32768  # half a second up a one-second full sweep
    assert pin.duration == 0
    assert len(turn_on_calls) == 1
    assert turn_on_calls[0].data == {"entity_id": entity_id, "brightness": 128}


async def test_stop_with_nothing_moving_is_silent(hass, turn_on_calls):
    backend, sock = _backend(hass)
    entity_id = _lifx_light(hass)
    await backend.async_stop(entity_id)
    assert not sock.sent
    assert not turn_on_calls


async def test_stop_after_the_ramp_ran_out_pins_the_rail(hass, turn_on_calls):
    backend, sock = _backend(hass)
    entity_id = _lifx_light(hass, brightness=0)
    with patch.object(lifx_mod.time, "monotonic", side_effect=[1000.0, 1005.0]):
        await backend.async_move(entity_id, DIRECTION_UP, 255)
        await backend.async_stop(entity_id)
    assert _packets(sock)[1].brightness == LIFX_MAX_BRI


# -- step --------------------------------------------------------------------------


async def test_backend_declines_native_step(hass):
    backend, sock = _backend(hass)
    assert not backend.supports_step
    await backend.async_step(_lifx_light(hass), DIRECTION_UP, 5.0)
    assert not sock.sent


# -- fade --------------------------------------------------------------------------


def test_backend_claims_fade(hass):
    assert LifxBackend(hass).supports_fade


async def test_fade_is_one_interpolated_set_color(hass):
    backend, sock = _backend(hass)
    entity_id = _lifx_light(hass, brightness=128)
    assert await backend.async_fade(entity_id, 255, 5.0) is None
    (pkt,) = _packets(sock)
    assert pkt.brightness == LIFX_MAX_BRI
    assert pkt.duration == 5000


async def test_fade_asserts_color_before_the_ramp(hass):
    backend, sock = _backend(hass)
    entity_id = _lifx_light(hass, brightness=128)
    await backend.async_fade(entity_id, 255, 2.0, color_temp_kelvin=2700)
    color, ramp = _packets(sock)
    assert (color.saturation, color.kelvin) == (0, 2700)
    assert color.brightness == 32896  # the current level: color changes, level not yet
    assert color.duration == 0
    assert ramp.brightness == LIFX_MAX_BRI
    assert ramp.duration == 2000


async def test_fade_from_off_becomes_a_power_fade(hass):
    backend, sock = _backend(hass)
    entity_id = _lifx_light(hass, on=False)
    await backend.async_fade(entity_id, 128, 3.0)
    color, power = _packets(sock)
    assert color.type == LIFX_PKT_SET_COLOR
    assert (color.brightness, color.duration) == (32896, 0)
    assert power.type == LIFX_PKT_SET_LIGHT_POWER
    assert (power.level, power.duration) == (65535, 3000)


async def test_stop_mid_power_fade_pins_both_color_and_power(hass, turn_on_calls):
    backend, sock = _backend(hass)
    entity_id = _lifx_light(hass, on=False)
    with patch.object(lifx_mod.time, "monotonic", side_effect=[1000.0, 1001.5]):
        await backend.async_fade(entity_id, 128, 3.0)
        await backend.async_stop(entity_id)
    pin, power_pin = _packets(sock)[2:]
    assert pin.type == LIFX_PKT_SET_COLOR
    assert pin.brightness == 16448  # halfway up a fade to 32896
    assert (power_pin.type, power_pin.level, power_pin.duration) == (
        LIFX_PKT_SET_LIGHT_POWER,
        65535,
        0,
    )


async def test_stop_after_a_kelvin_fade_reasserts_the_color(hass, turn_on_calls):
    backend, sock = _backend(hass)
    entity_id = _lifx_light(hass, brightness=128)
    with patch.object(lifx_mod.time, "monotonic", side_effect=[1000.0, 1001.0]):
        await backend.async_fade(entity_id, 255, 2.0, color_temp_kelvin=2700)
        await backend.async_stop(entity_id)
    assert turn_on_calls[0].data["color_temp_kelvin"] == 2700


async def test_fade_on_unavailable_light_sends_nothing(hass):
    backend, sock = _backend(hass)
    entity_id = _lifx_light(hass)
    hass.states.async_set(entity_id, "unavailable", {})
    await backend.async_fade(entity_id, 128, 1.0)
    assert not sock.sent


# -- lifecycle ---------------------------------------------------------------------


async def test_unload_closes_the_socket_and_forgets_ramps(hass):
    backend, sock = _backend(hass)
    entity_id = _lifx_light(hass)
    await backend.async_move(entity_id, DIRECTION_UP, None)
    await backend.async_unload()
    assert sock.closed
    assert not backend._transitions
