"""Tests for the native deCONZ backend."""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from homeassistant.helpers import entity_registry as er

from custom_components.dynamic_dimming.backends.deconz import (
    DeconzBackend,
    parse_field,
    to_bri,
)
from custom_components.dynamic_dimming.const import (
    DECONZ_DOMAIN,
    DECONZ_FIELD_ACTION,
    DECONZ_FIELD_STATE,
    DECONZ_SERVICE_CONFIGURE,
    DIRECTION_DOWN,
    DIRECTION_UP,
)

from .conftest import set_light_state

SERIAL = "00:0b:57:ff:fe:94:a3:1c-01"
BRIDGE_ID = "0123456789AB"
GROUP_UNIQUE_ID = f"{BRIDGE_ID}-/groups/3"


@pytest.fixture
def configure_calls(hass):
    """Stand in for deconz.configure, recording every call."""
    return async_mock_service(hass, DECONZ_DOMAIN, DECONZ_SERVICE_CONFIGURE)


def _deconz_light(
    hass,
    object_id="lamp",
    *,
    unique_id=SERIAL,
    bridge_id=BRIDGE_ID,
    brightness=100,
):
    """Register light.<object_id> as a deCONZ light on a deCONZ config entry."""
    entry = MockConfigEntry(domain=DECONZ_DOMAIN, unique_id=bridge_id)
    entry.add_to_hass(hass)
    er.async_get(hass).async_get_or_create(
        "light",
        DECONZ_DOMAIN,
        unique_id,
        suggested_object_id=object_id,
        config_entry=entry,
    )
    entity_id = f"light.{object_id}"
    set_light_state(hass, entity_id, brightness=brightness)
    return entity_id


def _only(calls):
    """The single recorded call's data, asserting there was exactly one."""
    assert len(calls) == 1
    return calls[0].data


# -- field routing -----------------------------------------------------------------


def test_parse_field_routes_a_light_to_state():
    assert parse_field(SERIAL) == DECONZ_FIELD_STATE


def test_parse_field_routes_a_group_to_action():
    assert parse_field(GROUP_UNIQUE_ID) == DECONZ_FIELD_ACTION


@pytest.mark.parametrize(
    "unique_id",
    [
        "",
        "not-a-deconz-id",
        "00:0b:57:ff:fe:94-01",  # too few colon groups
        f"{BRIDGE_ID}-/scenes/1/2",  # a scene, not a light
    ],
)
def test_parse_field_rejects_junk(unique_id):
    assert parse_field(unique_id) is None


@pytest.mark.parametrize(
    ("brightness", "bri"), [(0, 1), (1, 1), (128, 128), (255, 255), (1000, 255)]
)
def test_to_bri_clamps_into_the_still_on_range(brightness, bri):
    assert to_bri(brightness) == bri


# -- claiming ----------------------------------------------------------------------


async def test_claims_deconz_light(hass, configure_calls):
    assert DeconzBackend(hass).claims(_deconz_light(hass))


async def test_claims_deconz_group(hass, configure_calls):
    entity_id = _deconz_light(hass, "group", unique_id=GROUP_UNIQUE_ID)
    assert DeconzBackend(hass).claims(entity_id)


async def test_does_not_claim_without_the_configure_service(hass):
    entity_id = _deconz_light(hass)
    assert not DeconzBackend(hass).claims(entity_id)


async def test_does_not_claim_other_platform(hass, configure_calls):
    entry = MockConfigEntry(domain="hue")
    entry.add_to_hass(hass)
    er.async_get(hass).async_get_or_create(
        "light", "hue", "abc", suggested_object_id="other", config_entry=entry
    )
    set_light_state(hass, "light.other")
    assert not DeconzBackend(hass).claims("light.other")


async def test_does_not_claim_an_unparseable_unique_id(hass, configure_calls):
    entity_id = _deconz_light(hass, unique_id="weird-id")
    assert not DeconzBackend(hass).claims(entity_id)


# -- move / stop -------------------------------------------------------------------


async def test_move_up_writes_the_distance_to_the_top_rail(hass, configure_calls):
    entity_id = _deconz_light(hass, brightness=100)
    backend = DeconzBackend(hass)
    assert await backend.async_move(entity_id, DIRECTION_UP, "medium") is None
    data = _only(configure_calls)
    assert data["entity"] == entity_id
    assert data["field"] == DECONZ_FIELD_STATE
    assert data["bridgeid"] == BRIDGE_ID
    # 154 units at medium (90/s) is 1.71 s -> 17 tenths.
    assert data["data"] == {"bri_inc": 154, "transitiontime": 17}


async def test_move_down_aims_one_unit_above_zero(hass, configure_calls):
    entity_id = _deconz_light(hass, brightness=100)
    await DeconzBackend(hass).async_move(entity_id, DIRECTION_DOWN, "medium")
    assert _only(configure_calls)["data"] == {"bri_inc": -99, "transitiontime": 11}


async def test_move_at_the_rail_still_asks_for_a_nudge(hass, configure_calls):
    """HA's cache is not the authority on the level; the gateway is."""
    entity_id = _deconz_light(hass, brightness=255)
    await DeconzBackend(hass).async_move(entity_id, DIRECTION_UP, "medium")
    assert _only(configure_calls)["data"]["bri_inc"] == 1


async def test_move_on_a_group_writes_under_action(hass, configure_calls):
    entity_id = _deconz_light(hass, "group", unique_id=GROUP_UNIQUE_ID)
    await DeconzBackend(hass).async_move(entity_id, DIRECTION_UP, None)
    assert _only(configure_calls)["field"] == DECONZ_FIELD_ACTION


async def test_move_on_unclaimed_entity_sends_nothing(hass, configure_calls):
    entity_id = _deconz_light(hass, unique_id="weird-id")
    await DeconzBackend(hass).async_move(entity_id, DIRECTION_UP, "medium")
    assert not configure_calls


async def test_stop_writes_bri_inc_zero(hass, configure_calls):
    entity_id = _deconz_light(hass)
    await DeconzBackend(hass).async_stop(entity_id)
    assert _only(configure_calls)["data"] == {"bri_inc": 0}


async def test_calls_omit_bridgeid_when_the_entry_has_none(hass, configure_calls):
    entity_id = _deconz_light(hass, bridge_id=None)
    await DeconzBackend(hass).async_stop(entity_id)
    assert "bridgeid" not in _only(configure_calls)


# -- step --------------------------------------------------------------------------


async def test_step_is_a_native_increment_that_snaps(hass, configure_calls):
    entity_id = _deconz_light(hass)
    await DeconzBackend(hass).async_step(entity_id, DIRECTION_DOWN, 5.0)
    assert _only(configure_calls)["data"] == {"bri_inc": -13, "transitiontime": 0}


async def test_step_never_rounds_down_to_nothing(hass, configure_calls):
    entity_id = _deconz_light(hass)
    await DeconzBackend(hass).async_step(entity_id, DIRECTION_UP, 0.1)
    assert _only(configure_calls)["data"]["bri_inc"] == 1


# -- fade --------------------------------------------------------------------------


def test_backend_claims_fade(hass):
    assert DeconzBackend(hass).supports_fade


async def test_fade_is_one_bri_transition(hass, configure_calls):
    entity_id = _deconz_light(hass)
    assert await DeconzBackend(hass).async_fade(entity_id, 128, 5.0) is None
    assert _only(configure_calls)["data"] == {
        "on": True,
        "bri": 128,
        "transitiontime": 50,
    }


async def test_fade_asserts_color_before_the_ramp(hass, configure_calls):
    entity_id = _deconz_light(hass)
    await DeconzBackend(hass).async_fade(entity_id, 255, 1.0, color_temp_kelvin=2700)
    assert len(configure_calls) == 2
    color, ramp = configure_calls[0].data, configure_calls[1].data
    assert color["data"] == {"ct": 370, "transitiontime": 0}
    assert ramp["data"]["bri"] == 255


async def test_fade_still_ramps_when_the_color_write_fails(hass):
    """A light with no color temperature loses its white, not its fade."""
    seen: list = []

    async def _handler(call):
        seen.append(call.data)
        if "ct" in call.data["data"]:
            raise ValueError("parameter, ct, not available")

    hass.services.async_register(
        DECONZ_DOMAIN, DECONZ_SERVICE_CONFIGURE, _handler
    )
    entity_id = _deconz_light(hass)
    await DeconzBackend(hass).async_fade(entity_id, 128, 1.0, color_temp_kelvin=2700)
    await hass.async_block_till_done()
    assert [list(data["data"]) for data in seen] == [
        ["ct", "transitiontime"],
        ["on", "bri", "transitiontime"],
    ]


async def test_fade_clamps_a_very_long_transition(hass, configure_calls):
    entity_id = _deconz_light(hass)
    await DeconzBackend(hass).async_fade(entity_id, 255, 100_000.0)
    assert _only(configure_calls)["data"]["transitiontime"] == 65535
